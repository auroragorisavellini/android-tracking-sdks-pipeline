#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
02_build_app_sdk_dataset.py

Build an app--SDK dataset from AndroZoo APKs and Exodus Privacy SDK rules.

This public-release version keeps the original pipeline logic, but removes:
- Google Colab / Google Drive dependencies
- hard-coded local paths
- hard-coded API keys

Required environment variable:
    ANDROZOO_API_KEY

Example:
    export ANDROZOO_API_KEY="your_androzoo_api_key"

    python code/02_build_app_sdk_dataset.py \
        --catalogue data_raw/latest.csv.gz \
        --exodus-rules data_raw/exodus_trackers_code_rules.csv \
        --outdir outputs/app_sdk_dataset \
        --sample-size 100000

Outputs:
    sampled_packages.csv
    sampled_pkg_versions.csv
    sdk_per_apk.csv
    gp_metadata_full.ndjson
    gp_metadata_full_flat.csv
"""

import argparse
import csv
import gzip
import io
import json
import logging
import os
import random
import re
import time
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import Dict, Iterable, List, Optional, Set, Tuple

import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except Exception:
    Retry = None


# ==================================================
# Configuration
# ==================================================

DOWNLOAD_URL = "https://androzoo.uni.lu/api/download"
GP_METADATA_BASE = "https://androzoo.uni.lu/api/get_gp_metadata"

STD_NAMESPACE_PREFIXES = (
    "java.", "javax.", "kotlin.", "kotlinx.",
    "android.", "androidx.", "dalvik.", "sun.",
    "org.xmlpull.", "org.w3c.", "org.junit.", "junit.",
)

DEX_CLASS_RE = re.compile(rb"L([a-z0-9_]+(?:/[a-z0-9_]+)+);")
GENERIC_DROP_ROOTS = {"com.google", "com.google.android"}

TYPE_SYNONYMS = {
    "ads": "Advertising",
    "ad": "Advertising",
    "advertisement": "Advertising",
    "advertising": "Advertising",
    "analytics": "Analytics",
    "analytic": "Analytics",
    "crash": "Crash reporting",
    "crashreporting": "Crash reporting",
    "location": "Location",
    "push": "Push notifications",
    "notifications": "Push notifications",
    "identification": "Identification",
}


# ==================================================
# Logging
# ==================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


# ==================================================
# Data classes
# ==================================================

@dataclass(frozen=True)
class ExodusPrefixRule:
    sdk_name: str
    prefix_dot: str
    sdk_types: Tuple[str, ...]


@dataclass(frozen=True)
class ExodusRegexRule:
    sdk_name: str
    pattern: re.Pattern
    sdk_types: Tuple[str, ...]


# ==================================================
# HTTP utilities
# ==================================================

class SessionFactory:
    def __init__(self, pool_size: int):
        self.pool_size = pool_size
        self._sessions: Dict[int, requests.Session] = {}

    def get(self) -> requests.Session:
        import threading

        thread_id = threading.get_ident()

        if thread_id in self._sessions:
            return self._sessions[thread_id]

        session = requests.Session()
        session.headers.update({
            "User-Agent": "android-sdk-dataset-pipeline/1.0 research"
        })

        retries = Retry(
            total=3,
            connect=3,
            read=3,
            redirect=2,
            backoff_factor=0.7,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "HEAD"]),
        ) if Retry else None

        adapter = HTTPAdapter(
            pool_connections=self.pool_size,
            pool_maxsize=self.pool_size,
            max_retries=retries,
        )

        session.mount("https://", adapter)
        session.mount("http://", adapter)

        self._sessions[thread_id] = session
        return session


def http_get_with_retries(
    session: requests.Session,
    url: str,
    params: dict,
    timeout: int = 40,
    max_tries: int = 3,
) -> Optional[requests.Response]:

    backoff = 0.8

    for _ in range(max_tries):
        try:
            response = session.get(url, params=params, timeout=timeout)

            if response.status_code == 404:
                return None

            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(backoff)
                backoff *= 1.8
                continue

            response.raise_for_status()
            return response

        except requests.RequestException:
            time.sleep(backoff)
            backoff *= 1.8

    return None


def get_json(
    session: requests.Session,
    url: str,
    params: dict,
    timeout: int = 40,
    max_tries: int = 3,
):
    response = http_get_with_retries(
        session=session,
        url=url,
        params=params,
        timeout=timeout,
        max_tries=max_tries,
    )

    if response is None:
        return None

    try:
        return response.json()
    except Exception:
        return None


# ==================================================
# AndroZoo catalogue utilities
# ==================================================

def iter_catalogue_rows(catalogue_path: str) -> Iterable[dict]:
    with gzip.open(catalogue_path, "rt", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def collect_google_play_packages(catalogue_path: str) -> List[str]:
    packages: Set[str] = set()
    scanned = 0

    logger.info("Scanning AndroZoo catalogue for Google Play packages")

    for row in iter_catalogue_rows(catalogue_path):
        scanned += 1

        markets = row.get("markets", "") or ""
        pkg = (row.get("pkg_name", "") or "").strip()

        if pkg and "play.google.com" in markets:
            packages.add(pkg)

        if scanned % 1_000_000 == 0:
            logger.info(
                "Scanned %s rows; found %s Google Play packages",
                scanned,
                len(packages),
            )

    logger.info("Google Play package filter completed: %s packages found", len(packages))
    return sorted(packages)


def get_version_code(record: dict) -> Optional[int]:
    paths = [
        ("details", "appDetails", "versionCode"),
        ("details", "appDetails", "versioncode"),
        ("versionCode",),
        ("versioncode",),
    ]

    for path in paths:
        current = record
        ok = True

        for key in path:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                ok = False
                break

        if ok:
            try:
                return int(str(current).strip())
            except Exception:
                return None

    return None


def fetch_package_versions(
    session: requests.Session,
    api_key: str,
    package_name: str,
) -> Optional[List[int]]:

    url = f"{GP_METADATA_BASE.rstrip('/')}/{package_name}"

    data = get_json(
        session=session,
        url=url,
        params={"apikey": api_key},
        timeout=60,
        max_tries=4,
    )

    if not isinstance(data, list):
        return None

    version_codes = set()

    for record in data:
        vc = get_version_code(record)
        if vc is not None:
            version_codes.add(vc)

    if not version_codes:
        return None

    return sorted(version_codes)


# ==================================================
# Sampling
# ==================================================

def write_csv(path: Path, fieldnames: List[str], rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sample_packages_from_catalogue(
    catalogue_path: str,
    outdir: Path,
    api_key: str,
    sample_size: int,
    seed: int,
    workers: int,
    http_pool: int,
    candidate_step: int,
    max_candidates: int,
) -> None:

    rng = random.Random(seed)

    packages = collect_google_play_packages(catalogue_path)
    rng.shuffle(packages)

    session_factory = SessionFactory(pool_size=http_pool)
    versions_by_pkg: Dict[str, List[int]] = {}

    cursor = 0
    scanned = 0

    logger.info("Starting adaptive package prefiltering")

    def worker(pkg: str):
        session = session_factory.get()
        versions = fetch_package_versions(session, api_key, pkg)
        return pkg, versions

    while len(versions_by_pkg) < sample_size and scanned < max_candidates and cursor < len(packages):
        batch = packages[cursor:cursor + candidate_step]
        cursor += len(batch)
        scanned += len(batch)

        logger.info(
            "Prefilter batch: %s candidates; %s valid packages collected out of %s",
            len(batch),
            len(versions_by_pkg),
            sample_size,
        )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(worker, pkg) for pkg in batch]

            for future in as_completed(futures):
                pkg, versions = future.result()

                if versions:
                    versions_by_pkg[pkg] = versions

                if len(versions_by_pkg) >= sample_size:
                    break

    selected = list(versions_by_pkg.items())[:sample_size]

    sampled_packages = [{"pkg_name": pkg} for pkg, _ in selected]
    sampled_versions = [
        {"pkg_name": pkg, "vercode": str(vc)}
        for pkg, versions in selected
        for vc in versions
    ]

    write_csv(outdir / "sampled_packages.csv", ["pkg_name"], sampled_packages)
    write_csv(outdir / "sampled_pkg_versions.csv", ["pkg_name", "vercode"], sampled_versions)

    logger.info(
        "Sampling completed: %s packages and %s package-version pairs",
        len(sampled_packages),
        len(sampled_versions),
    )


def load_fixed_sample_csv(sample_csv: str, outdir: Path) -> List[dict]:
    rows = []

    with open(sample_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        required = {"sha256", "pkg_name", "vercode"}
        missing = required - set(reader.fieldnames or [])

        if missing:
            raise RuntimeError(f"Fixed sample CSV is missing columns: {sorted(missing)}")

        for row in reader:
            rows.append(row)

    sampled_packages = sorted({row["pkg_name"] for row in rows})
    sampled_versions = [
        {"pkg_name": row["pkg_name"], "vercode": str(row["vercode"])}
        for row in rows
    ]

    write_csv(
        outdir / "sampled_packages.csv",
        ["pkg_name"],
        [{"pkg_name": pkg} for pkg in sampled_packages],
    )

    write_csv(
        outdir / "sampled_pkg_versions.csv",
        ["pkg_name", "vercode"],
        sampled_versions,
    )

    logger.info("Loaded fixed sample with %s APK rows", len(rows))
    return rows


# ==================================================
# Exodus rules
# ==================================================

def parse_types(value: Optional[str]) -> Tuple[str, ...]:
    if not value:
        return tuple()

    cleaned = re.sub(r"[\[\]\"'\\]", "", str(value))
    parts = re.split(r"[,;/|]+|\s{2,}", cleaned)

    out = []
    seen = set()

    for part in parts:
        p = part.strip()

        if not p:
            continue

        norm = TYPE_SYNONYMS.get(p.lower(), p.capitalize())

        if norm not in seen:
            seen.add(norm)
            out.append(norm)

    return tuple(out)


def split_rule_to_prefixes(rule: str) -> List[str]:
    parts = [p.strip() for p in str(rule).split("|")]
    out = []

    for part in parts:
        if not part:
            continue

        p = part.replace(" ", "")
        p = re.sub(r"(\.\*)+$", "", p).rstrip(".").rstrip("/")
        p_dot = p.replace("/", ".")

        if any(ch in p_dot for ch in r"^$[](){}*+?\\"):
            continue

        if "." in p_dot:
            out.append(p_dot)

    return out


def load_exodus_rules(path: str) -> Tuple[List[ExodusPrefixRule], List[ExodusRegexRule]]:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Exodus rules file not found: {path}")

    prefix_rules: List[ExodusPrefixRule] = []
    regex_rules: List[ExodusRegexRule] = []

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        if "code_rule" not in (reader.fieldnames or []):
            raise RuntimeError("Exodus rules CSV must contain a 'code_rule' column")

        for row in reader:
            sdk_name = (row.get("name") or row.get("tracker") or "").strip()
            raw_rule = (row.get("code_rule") or "").strip().strip("`")
            sdk_types = parse_types(row.get("types"))

            if not sdk_name or not raw_rule:
                continue

            for prefix in split_rule_to_prefixes(raw_rule):
                prefix_rules.append(
                    ExodusPrefixRule(
                        sdk_name=sdk_name,
                        prefix_dot=prefix,
                        sdk_types=sdk_types,
                    )
                )

            regex_rule = raw_rule.replace(" ", "").replace(".", "/")

            try:
                regex_rules.append(
                    ExodusRegexRule(
                        sdk_name=sdk_name,
                        pattern=re.compile(regex_rule),
                        sdk_types=sdk_types,
                    )
                )
            except Exception:
                try:
                    regex_rules.append(
                        ExodusRegexRule(
                            sdk_name=sdk_name,
                            pattern=re.compile(re.escape(regex_rule)),
                            sdk_types=sdk_types,
                        )
                    )
                except Exception:
                    continue

    prefix_rules = list({
        (r.sdk_name, r.prefix_dot, r.sdk_types): r
        for r in prefix_rules
    }.values())

    regex_rules = list({
        (r.sdk_name, r.pattern.pattern, r.sdk_types): r
        for r in regex_rules
    }.values())

    logger.info(
        "Loaded Exodus rules: %s prefix rules and %s regex rules",
        len(prefix_rules),
        len(regex_rules),
    )

    return prefix_rules, regex_rules


# ==================================================
# APK static analysis
# ==================================================

def list_dex_classes(apk_bytes: bytes) -> Tuple[Set[str], Set[str]]:
    classes_dot: Set[str] = set()
    classes_slash: Set[str] = set()

    try:
        with zipfile.ZipFile(io.BytesIO(apk_bytes)) as zf:
            for filename in zf.namelist():
                if not filename.endswith(".dex"):
                    continue

                try:
                    dex_bytes = zf.read(filename)

                    for match in DEX_CLASS_RE.finditer(dex_bytes):
                        cls_slash = match.group(1).decode("utf-8", errors="ignore")
                        cls_dot = cls_slash.replace("/", ".")

                        if "." in cls_dot:
                            classes_slash.add(cls_slash)
                            classes_dot.add(cls_dot)

                except Exception:
                    continue

    except Exception:
        pass

    return classes_dot, classes_slash


def is_standard_namespace(class_name: str) -> bool:
    return class_name.startswith(STD_NAMESPACE_PREFIXES)


def canonical_root_from_prefix(class_name: str, prefix: str) -> str:
    parts = class_name.split(".")
    n = max(3, len(prefix.split(".")))
    return ".".join(parts[:min(n, len(parts))])


def shorten_vendor_root(root: str) -> str:
    parts = root.split(".")

    if len(parts) >= 3:
        first_two = ".".join(parts[:2])

        if first_two in {
            "com.startapp",
            "com.facebook",
            "com.flurry",
            "com.ironsource",
            "com.chartboost",
            "com.adjust",
        }:
            return first_two

    return root


def detect_sdks_from_apk(
    apk_bytes: bytes,
    app_package: str,
    prefix_rules: List[ExodusPrefixRule],
    regex_rules: List[ExodusRegexRule],
) -> List[Tuple[str, str]]:

    classes_dot, classes_slash = list_dex_classes(apk_bytes)

    if not classes_dot:
        return []

    hits: Dict[str, Set[str]] = defaultdict(set)

    sorted_prefix_rules = sorted(
        prefix_rules,
        key=lambda r: len(r.prefix_dot),
        reverse=True,
    )

    for cls in classes_dot:
        if cls == app_package or cls.startswith(app_package + "."):
            continue

        if is_standard_namespace(cls) or cls.startswith("com.android."):
            continue

        for rule in sorted_prefix_rules:
            if cls.startswith(rule.prefix_dot):
                root = canonical_root_from_prefix(cls, rule.prefix_dot)

                if root not in GENERIC_DROP_ROOTS:
                    hits[rule.sdk_name].add(shorten_vendor_root(root))

                break

    for cls_slash in classes_slash:
        cls_dot = cls_slash.replace("/", ".")

        if cls_dot == app_package or cls_dot.startswith(app_package + "."):
            continue

        if is_standard_namespace(cls_dot) or cls_dot.startswith("com.android."):
            continue

        for rule in regex_rules:
            if rule.pattern.search(cls_slash):
                root = ".".join(cls_dot.split(".")[:5])

                if root not in GENERIC_DROP_ROOTS:
                    hits[rule.sdk_name].add(shorten_vendor_root(root))

                break

    out = []

    for sdk_name, roots in hits.items():
        if not roots:
            continue

        best_root = min(roots, key=lambda x: (len(x.split(".")), len(x)))
        out.append((sdk_name, best_root))

    return sorted(out, key=lambda x: x[0].lower())


def download_apk(
    session: requests.Session,
    api_key: str,
    sha256: str,
) -> Tuple[str, Optional[bytes]]:

    try:
        with session.get(
            DOWNLOAD_URL,
            params={"apikey": api_key, "sha256": sha256},
            stream=True,
            timeout=180,
        ) as response:

            if response.status_code == 403:
                return "NOT_AVAILABLE", None

            response.raise_for_status()

            buffer = io.BytesIO()

            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    buffer.write(chunk)

            return "OK", buffer.getvalue()

    except Exception:
        return "ERROR", None


# ==================================================
# Metadata utilities
# ==================================================

def flatten_dict(obj, parent_key: str = "", sep: str = ".") -> dict:
    items = {}

    if isinstance(obj, dict):
        for key, value in obj.items():
            new_key = f"{parent_key}{sep}{key}" if parent_key else str(key)
            items.update(flatten_dict(value, new_key, sep=sep))

    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            new_key = f"{parent_key}{sep}{i}" if parent_key else str(i)
            items.update(flatten_dict(value, new_key, sep=sep))

    else:
        items[parent_key] = "" if obj is None else str(obj)

    return items


def fetch_all_package_metadata(
    session: requests.Session,
    api_key: str,
    pkg: str,
) -> List[dict]:

    data = get_json(
        session=session,
        url=f"{GP_METADATA_BASE.rstrip('/')}/{pkg}",
        params={"apikey": api_key},
        timeout=60,
        max_tries=4,
    )

    if isinstance(data, list):
        return data

    return []


def build_metadata_index(
    rows: List[dict],
    api_key: str,
    http_pool: int,
    workers: int = 8,
) -> Dict[Tuple[str, str], dict]:

    packages = sorted({
        (row.get("pkg_name") or "").strip()
        for row in rows
        if row.get("pkg_name")
    })

    session_factory = SessionFactory(pool_size=http_pool)
    metadata_index: Dict[Tuple[str, str], dict] = {}

    logger.info("Fetching package-level Google Play metadata for %s packages", len(packages))

    def worker(pkg: str):
        session = session_factory.get()
        records = fetch_all_package_metadata(session, api_key, pkg)

        out = []

        for record in records:
            vc = get_version_code(record)

            if vc is not None:
                out.append((pkg, str(vc), record))

        return out

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(worker, pkg) for pkg in packages]

        for i, future in enumerate(as_completed(futures), start=1):
            for pkg, vc, record in future.result():
                metadata_index[(pkg, vc)] = record

            if i % 100 == 0 or i == len(futures):
                logger.info("Metadata fetched for %s/%s packages", i, len(futures))

    logger.info(
        "Metadata index built: %s package-version metadata records",
        len(metadata_index),
    )

    return metadata_index


def build_flat_metadata_csv(ndjson_path: Path, out_csv: Path) -> None:
    rows = []

    if not ndjson_path.exists():
        logger.warning("No metadata NDJSON file found")
        return

    with open(ndjson_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue

    if not rows:
        logger.warning("Metadata NDJSON is empty")
        return

    columns = sorted({key for row in rows for key in row.keys()})

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    logger.info("Flat metadata CSV written to %s with %s rows", out_csv, len(rows))


# ==================================================
# APK processing
# ==================================================

def load_rows_from_catalogue(
    catalogue_path: str,
    wanted_pairs: Set[Tuple[str, str]],
) -> List[dict]:

    wanted_packages = {pkg for pkg, _ in wanted_pairs}
    rows = []

    logger.info("Scanning catalogue to recover SHA256 identifiers for sampled versions")

    for row in iter_catalogue_rows(catalogue_path):
        pkg = (row.get("pkg_name") or "").strip()
        vc = str(row.get("vercode") or "").strip()

        if pkg in wanted_packages and (pkg, vc) in wanted_pairs:
            rows.append(row)

    logger.info("Recovered %s catalogue rows for sampled package-version pairs", len(rows))
    return rows


def load_rows_for_processing(
    catalogue_path: Optional[str],
    fixed_sample_rows: Optional[List[dict]],
    outdir: Path,
) -> List[dict]:

    if fixed_sample_rows is not None:
        return fixed_sample_rows

    sampled_versions_path = outdir / "sampled_pkg_versions.csv"

    if not sampled_versions_path.exists():
        raise RuntimeError("sampled_pkg_versions.csv not found. Run sampling first or provide --input-sample-csv.")

    wanted_pairs = set()

    with open(sampled_versions_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            wanted_pairs.add((row["pkg_name"].strip(), str(row["vercode"]).strip()))

    if not catalogue_path:
        raise RuntimeError("Catalogue path is required when no fixed sample CSV is provided.")

    return load_rows_from_catalogue(catalogue_path, wanted_pairs)


def process_apks(
    rows: List[dict],
    outdir: Path,
    api_key: str,
    exodus_rules_path: str,
    workers: int,
    polite_sleep: float,
    http_pool: int,
) -> None:

    prefix_rules, regex_rules = load_exodus_rules(exodus_rules_path)

    name_to_types: Dict[str, Set[str]] = defaultdict(set)

    for rule in prefix_rules:
        name_to_types[rule.sdk_name].update(rule.sdk_types)

    for rule in regex_rules:
        name_to_types[rule.sdk_name].update(rule.sdk_types)

    sdk_csv = outdir / "sdk_per_apk.csv"
    ndjson_path = outdir / "gp_metadata_full.ndjson"
    flat_metadata_csv = outdir / "gp_metadata_full_flat.csv"

    processed_shas: Set[str] = set()

    if sdk_csv.exists():
        with open(sdk_csv, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            for row in reader:
                status = (row.get("download_status") or "").strip().upper()

                if status in {"OK", "NOT_AVAILABLE"}:
                    processed_shas.add((row.get("sha256") or "").strip())

    tasks = []

    for row in rows:
        sha = (row.get("sha256") or "").strip()

        if sha and sha not in processed_shas:
            tasks.append(row)

    logger.info("APK processing tasks: %s pending out of %s rows", len(tasks), len(rows))

    metadata_index = build_metadata_index(
        rows=rows,
        api_key=api_key,
        http_pool=http_pool,
        workers=min(8, http_pool),
    )

    queue: Queue = Queue(maxsize=5000)
    stop_signal = object()

    def writer():
        sdk_exists = sdk_csv.exists()

        with open(sdk_csv, "a", newline="", encoding="utf-8") as f_sdk, \
             open(ndjson_path, "a", encoding="utf-8") as f_meta:

            sdk_writer = csv.writer(f_sdk, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL)

            if not sdk_exists:
                sdk_writer.writerow([
                    "sha256",
                    "pkg_name",
                    "vercode",
                    "dex_date",
                    "markets",
                    "vt_detection",
                    "vt_scan_date",
                    "extraction_ts_utc",
                    "download_status",
                    "sdk_count",
                    "sdk_names_json",
                    "sdk_roots_json",
                    "sdk_types_json",
                    "gp_title",
                    "gp_source",
                    "match_type",
                ])

            while True:
                item = queue.get()

                if item is stop_signal:
                    f_sdk.flush()
                    f_meta.flush()
                    return

                kind, payload = item

                if kind == "sdk_row":
                    if len(payload) != 16:
                        logger.warning("Skipping malformed SDK row with %s columns: %s", len(payload), payload)
                    else:
                        sdk_writer.writerow(payload)

                elif kind == "metadata":
                    f_meta.write(json.dumps(payload, ensure_ascii=False) + "\n")

                queue.task_done()

    writer_thread = Thread(target=writer, daemon=True)
    writer_thread.start()

    session_factory = SessionFactory(pool_size=http_pool)

    def worker(row: dict):
        session = session_factory.get()

        sha = (row.get("sha256") or "").strip()
        pkg = (row.get("pkg_name") or "").strip()
        vc = str(row.get("vercode") or "").strip()

        metadata = metadata_index.get((pkg, vc))
        match_type = "exact" if isinstance(metadata, dict) else "unmatched"

        gp_title = ""

        if isinstance(metadata, dict):
            gp_title = metadata.get("title") or metadata.get("titleText") or ""

        status, apk_bytes = download_apk(session, api_key, sha)
        time.sleep(polite_sleep)

        matches: List[Tuple[str, str]] = []

        if status == "OK" and apk_bytes:
            try:
                matches = detect_sdks_from_apk(
                    apk_bytes=apk_bytes,
                    app_package=pkg,
                    prefix_rules=prefix_rules,
                    regex_rules=regex_rules,
                )
            except Exception:
                matches = []

        sdk_names = sorted({name for name, _ in matches}) if status == "OK" else []
        sdk_roots = sorted({root for _, root in matches}) if status == "OK" else []
        sdk_types = sorted({t for name in sdk_names for t in name_to_types.get(name, set())})

        queue.put((
            "sdk_row",
            [
                sha,
                pkg,
                vc,
                row.get("dex_date", ""),
                row.get("markets", ""),
                row.get("vt_detection", ""),
                row.get("vt_scan_date", ""),
                datetime.now(timezone.utc).isoformat(),
                status,
                len(sdk_names),
                json.dumps(sdk_names, ensure_ascii=False),
                json.dumps(sdk_roots, ensure_ascii=False),
                json.dumps(sdk_types, ensure_ascii=False),
                gp_title,
                "package_level",
                match_type,
            ],
        ))

        if isinstance(metadata, dict):
            flat = flatten_dict(metadata)
            flat = {f"meta_{k}": v for k, v in flat.items()}
            flat.update({
                "sha256": sha,
                "pkg_name": pkg,
                "vercode": vc,
                "dex_date": row.get("dex_date", ""),
                "gp_source": "package_level",
                "match_type": match_type,
            })

            queue.put(("metadata", flat))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(worker, row) for row in tasks]

        for i, _ in enumerate(as_completed(futures), start=1):
            if i % 100 == 0 or i == len(futures):
                logger.info("Processed %s/%s APK tasks", i, len(futures))

    queue.put(stop_signal)
    writer_thread.join(timeout=120)

    build_flat_metadata_csv(ndjson_path, flat_metadata_csv)

    logger.info("APK processing completed")


# ==================================================
# Main
# ==================================================

def main():
    parser = argparse.ArgumentParser(
        description="Build app-SDK dataset from AndroZoo APKs and Exodus Privacy rules."
    )

    parser.add_argument(
        "--catalogue",
        default="data_raw/latest.csv.gz",
        help="Path to the AndroZoo catalogue file latest.csv.gz."
    )

    parser.add_argument(
        "--exodus-rules",
        default="data_raw/exodus_trackers_code_rules.csv",
        help="Path to the Exodus tracker detection rules CSV."
    )

    parser.add_argument(
        "--outdir",
        default="outputs/app_sdk_dataset",
        help="Output directory for generated app-SDK datasets."
    )

    parser.add_argument("--input-sample-csv", default=None)
    parser.add_argument("--skip-sampling", action="store_true")

    parser.add_argument("--sample-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)

    parser.add_argument("--prefilter-workers", type=int, default=4)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--http-pool", type=int, default=8)
    parser.add_argument("--candidate-step", type=int, default=50)
    parser.add_argument("--max-candidates", type=int, default=500)
    parser.add_argument("--polite-sleep", type=float, default=1.0)

    args = parser.parse_args()

    api_key = os.environ.get("ANDROZOO_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError(
            "Please set the ANDROZOO_API_KEY environment variable before running this script."
        )

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    fixed_sample_rows = None

    if args.input_sample_csv:
        fixed_sample_rows = load_fixed_sample_csv(args.input_sample_csv, outdir)

    elif not args.skip_sampling:
        sample_packages_from_catalogue(
            catalogue_path=args.catalogue,
            outdir=outdir,
            api_key=api_key,
            sample_size=args.sample_size,
            seed=args.seed,
            workers=args.prefilter_workers,
            http_pool=args.http_pool,
            candidate_step=args.candidate_step,
            max_candidates=args.max_candidates,
        )

    rows = load_rows_for_processing(
        catalogue_path=args.catalogue,
        fixed_sample_rows=fixed_sample_rows,
        outdir=outdir,
    )

    process_apks(
        rows=rows,
        outdir=outdir,
        api_key=api_key,
        exodus_rules_path=args.exodus_rules,
        workers=args.workers,
        polite_sleep=args.polite_sleep,
        http_pool=args.http_pool,
    )


if __name__ == "__main__":
    main()
