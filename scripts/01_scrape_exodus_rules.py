#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
01_scrape_exodus_rules.py
Build Exodus Privacy SDK detection rule table.

This version is generalized for public release:
- No Google Colab dependency
- No hard-coded Google Drive paths
- Output directory is specified via --outdir

Example:
    python code/01_scrape_exodus_rules.py --outdir data_raw
"""

import argparse
import csv
import json
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

# ==================================================
# Configuration
# ==================================================

BASE_URL = "https://reports.exodus-privacy.eu.org"
HEADERS = {"User-Agent": "android-sdk-pipeline/1.0 research"}

TRACKER_LINK_RE = re.compile(r"^/(?:it|en)/trackers/([^/]+)/?$")
CATEGORY_LINK_RE = re.compile(r"/(?:it|en)/trackers/categories/[^/]+/?$")

LABEL_CODE_RULES = [
    "Regola di rilevamento (codice):",
    "Code detection rule:",
]

LABEL_NETWORK_RULES = [
    "Regola di rilevamento (rete):",
    "Network detection rule:",
]

session = requests.Session()
session.headers.update(HEADERS)

# ==================================================
# Helper functions 
# ==================================================

def normalize_space(text):
    if text is None:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    return re.sub(r"\s+", " ", text).strip()


def clean_tracker_name(name):
    name = normalize_space(name)
    name = re.sub(r"\s*[\(\[\{]\s*\d+(?:[.,]\d+)?\s*%\s*[\)\]\}]\s*$", "", name)
    name = re.sub(r"\s*(?:[-–—]\s*)?\d+(?:[.,]\d+)?\s*%\s*$", "", name)
    return name.strip()


def get_html(url, max_retries=3, timeout=30):
    for attempt in range(1, max_retries + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.text
        except Exception:
            if attempt == max_retries:
                raise
            time.sleep(1.5 * attempt)


def unique_preserve_order(values):
    seen = set()
    output = []

    for value in values:
        key = value.lower()
        if key not in seen:
            seen.add(key)
            output.append(value)

    return output


def smallest_unique_container(link_tag, slug):
    for ancestor in link_tag.parents:
        if getattr(ancestor, "name", None) not in ("li", "div", "article", "section"):
            continue

        slugs = set()

        for a in ancestor.find_all("a", href=True):
            absolute_url = urljoin(BASE_URL, a["href"].strip())
            match = TRACKER_LINK_RE.match(urlparse(absolute_url).path)

            if match:
                slugs.add(match.group(1))

        if slugs == {slug}:
            return ancestor

    return link_tag.parent or link_tag


def collect_tracker_types(scope):
    types = []

    for a in scope.find_all("a", href=True):
        href = a["href"].strip()

        if CATEGORY_LINK_RE.search(href):
            label = normalize_space(a.get_text())

            if label:
                types.append(label)

    if types:
        return unique_preserve_order(types)

    for element in scope.find_all(["span", "div", "a"]):
        classes = " ".join(element.get("class", [])).lower()

        if not any(k in classes for k in ("badge", "tag", "label", "chip", "pill")):
            continue

        label = normalize_space(element.get_text())

        if not label:
            continue

        if re.fullmatch(r"\d+(?:[.,]\d+)?\s*%?", label):
            continue

        if re.search(r"(trovato|applications|applicazioni)", label, re.I):
            continue

        types.append(label)

    return unique_preserve_order(types)


def extract_tracker_items(list_html):
    soup = BeautifulSoup(list_html, "lxml")
    items = []

    for a in soup.find_all("a", href=True):
        absolute_url = urljoin(BASE_URL, a["href"].strip())
        parsed = urlparse(absolute_url)
        match = TRACKER_LINK_RE.match(parsed.path)

        if not match:
            continue

        slug = match.group(1)
        name = clean_tracker_name(a.get_text()) or slug

        container = smallest_unique_container(a, slug)
        types_hint = collect_tracker_types(container)

        items.append({
            "name": name,
            "slug": slug,
            "url": absolute_url,
            "types_hint": types_hint,
        })

    deduplicated = {}

    for item in items:
        slug = item["slug"]

        if slug in deduplicated:
            previous = deduplicated[slug]
            item["types_hint"] = unique_preserve_order(
                previous["types_hint"] + item["types_hint"]
            )

        deduplicated[slug] = item

    return list(deduplicated.values())


def find_label_node(soup, labels):
    label_set = {normalize_space(label) for label in labels}

    for text_node in soup.find_all(string=True):
        text = normalize_space(str(text_node))

        if text in label_set:
            return text_node.parent if hasattr(text_node, "parent") else text_node

    return None


def extract_types_from_detail(detail_html):
    soup = BeautifulSoup(detail_html, "lxml")
    types = []

    for a in soup.find_all("a", href=True):
        if CATEGORY_LINK_RE.search(a["href"].strip()):
            label = normalize_space(a.get_text())

            if label:
                types.append(label)

    if types:
        return unique_preserve_order(types)

    return collect_tracker_types(soup)


def extract_code_rule(detail_html):
    soup = BeautifulSoup(detail_html, "lxml")
    code_label = find_label_node(soup, LABEL_CODE_RULES)

    if not code_label:
        return ""

    current = code_label
    visited = set()

    def is_network_label(tag):
        if not tag:
            return False

        if hasattr(tag, "get_text"):
            text = normalize_space(tag.get_text(separator=" ", strip=True))
        else:
            text = normalize_space(str(tag))

        return any(
            text.startswith(normalize_space(label))
            for label in LABEL_NETWORK_RULES
        )

    while current and current not in visited:
        visited.add(current)

        if is_network_label(current):
            break

        if getattr(current, "name", None) in ("pre", "code"):
            return current.get_text("\n", strip=True)

        current = current.find_next_sibling() or current.find_next()

    current = code_label
    visited.clear()
    chunks = []

    while current and current not in visited:
        visited.add(current)

        if is_network_label(current):
            break

        if current is not code_label:
            if hasattr(current, "get_text"):
                text = current.get_text(separator="\n", strip=True)
            else:
                text = str(current)

            text = text.strip()

            if text:
                chunks.append(text)

        current = current.find_next_sibling() or current.find_next()

    return "\n".join(chunks).strip()


def scrape_exodus_rules(language="it", sleep_seconds=0.25):
    tracker_list_url = f"{BASE_URL}/{language}/trackers/"

    print(f"Downloading tracker list: {tracker_list_url}")

    list_html = get_html(tracker_list_url)
    tracker_items = extract_tracker_items(list_html)

    print(f"Found {len(tracker_items)} tracker pages")

    rows = []

    for i, item in enumerate(sorted(tracker_items, key=lambda x: x["slug"]), start=1):
        print(f"[{i}/{len(tracker_items)}] {item['name']}")

        try:
            detail_html = get_html(item["url"])
            code_rule = extract_code_rule(detail_html).strip()
            types = item["types_hint"] or extract_types_from_detail(detail_html)

        except Exception:
            code_rule = ""
            types = item["types_hint"]

        rows.append({
            "name": item["name"],
            "slug": item["slug"],
            "url": item["url"],
            "code_rule": code_rule,
            "types": json.dumps(types, ensure_ascii=False),
        })

        time.sleep(sleep_seconds)

    return rows


def main():
    parser = argparse.ArgumentParser(
        description="Build Exodus Privacy SDK detection rule table."
    )

    parser.add_argument(
        "--outdir",
        default="data_raw",
        help="Directory where the output CSV will be saved."
    )

    parser.add_argument(
        "--language",
        default="it",
        choices=["it", "en"],
        help="Language version of the Exodus website to scrape."
    )

    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.25,
        help="Pause between tracker page requests."
    )

    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    out_csv = outdir / "exodus_trackers_code_rules.csv"

    rows = scrape_exodus_rules(
        language=args.language,
        sleep_seconds=args.sleep_seconds,
    )

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["name", "slug", "url", "code_rule", "types"]
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Saved Exodus rule table to: {out_csv}")
    print(f"Rows: {len(rows)}")

    exodus_rules = pd.read_csv(out_csv)
    print(exodus_rules.head())


if __name__ == "__main__":
    main()
