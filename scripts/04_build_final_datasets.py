#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
04_build_final_datasets.py

Build final app-version dataset by merging:
- SDK detection output produced by 02_build_app_sdk_dataset.py
- Google Play metadata flat file produced by 02_build_app_sdk_dataset.py

This public-release version keeps the original merge logic, but removes:
- Google Colab / Google Drive dependencies
- hard-coded local paths

Example:
    python code/04_build_final_datasets.py \
        --run-dir outputs/app_sdk_dataset \
        --provider-path outputs/provider_mapping/sdk_company_lookup_FINAL_CANONICAL.csv \
        --outdir outputs/final_datasets

Outputs:
    final_app_version_dataset.csv
    final_app_sdk_edges.csv
    final_app_sdk_provider_edges.csv
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


# ==================================================
# Helper functions
# ==================================================

def read_csv_with_separator_detection(path, dtype=str, low_memory=False):
    """
    Read a CSV file and detect whether it is comma- or semicolon-separated.

    This follows the logic used in the original notebook:
    - first read the header with the default comma separator;
    - if only one column is detected, reload using sep=';'.
    """
    cols = pd.read_csv(path, nrows=0).columns.tolist()

    if len(cols) == 1:
        print(f"Semicolon separator detected for {path}. Reloading with sep=';'...")
        df = pd.read_csv(
            path,
            dtype=dtype,
            low_memory=low_memory,
            sep=";"
        )
    else:
        df = pd.read_csv(
            path,
            dtype=dtype,
            low_memory=low_memory
        )

    df.columns = df.columns.str.strip()
    return df


def normalize_merge_keys(df, required_cols, dataset_name):
    """
    Check and normalize merge keys.
    """
    missing_cols = [c for c in required_cols if c not in df.columns]

    if missing_cols:
        raise ValueError(
            f"Missing required {dataset_name} columns: {missing_cols}\n"
            f"Available columns: {df.columns[:30].tolist()}"
        )

    df["sha256"] = df["sha256"].astype(str).str.upper().str.strip()
    df["pkg_name"] = df["pkg_name"].astype(str).str.strip()
    df["vercode"] = df["vercode"].astype(str).str.strip()

    return df


def parse_json_list(value):
    """
    Parse JSON list fields such as sdk_names_json, sdk_roots_json, sdk_types_json.
    """
    if pd.isna(value):
        return []

    if isinstance(value, list):
        return value

    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass

    return []


# ==================================================
# Load input datasets
# ==================================================

def load_sdk_dataset(sdk_path):
    sdk_path = Path(sdk_path)

    sdk_cols = pd.read_csv(sdk_path, nrows=0, sep=",").columns.tolist()

    print("Number of SDK columns:", len(sdk_cols))
    print("SDK columns:")
    print(sdk_cols[:20])

    sdk = pd.read_csv(
        sdk_path,
        dtype=str,
        low_memory=False,
        sep=",",
        on_bad_lines="warn"
    )

    sdk.columns = sdk.columns.str.strip()

    print("\nSDK dataset:", sdk.shape)
    print("SDK columns:")
    print(sdk.columns.tolist())

    required_cols = ["sha256", "pkg_name", "vercode"]
    sdk = normalize_merge_keys(sdk, required_cols, "SDK")

    print("Unique APKs in SDK dataset:", sdk["sha256"].nunique())
    print("Unique apps in SDK dataset:", sdk["pkg_name"].nunique())

    return sdk


def load_metadata_dataset(meta_path):
    meta_path = Path(meta_path)

    meta_cols = pd.read_csv(meta_path, nrows=0).columns.tolist()

    print("Number of metadata columns:", len(meta_cols))
    print("First 20 columns:")
    print(meta_cols[:20])

    if len(meta_cols) == 1:
        print("Semicolon separator detected. Reloading metadata with sep=';'...")
        meta = pd.read_csv(
            meta_path,
            dtype=str,
            low_memory=False,
            sep=";"
        )
    else:
        meta = pd.read_csv(
            meta_path,
            dtype=str,
            low_memory=False
        )

    meta.columns = meta.columns.str.strip()

    print("\nMetadata dataset:", meta.shape)
    print("First 10 columns:")
    print(meta.columns[:10].tolist())

    required_cols = ["sha256", "pkg_name", "vercode"]
    meta = normalize_merge_keys(meta, required_cols, "metadata")

    print("Unique APKs in metadata:", meta["sha256"].nunique())
    print("Unique apps in metadata:", meta["pkg_name"].nunique())

    return meta


# ==================================================
# Merge SDK output with metadata
# ==================================================

def build_final_app_version_dataset(sdk, meta, out_app_version):
    merge_keys = ["sha256", "pkg_name", "vercode"]

    final_app_version = sdk.merge(
        meta,
        on=merge_keys,
        how="left",
        suffixes=("", "_meta"),
        indicator=True
    )

    final_app_version["metadata_merge_status"] = np.where(
        final_app_version["_merge"] == "both",
        "matched",
        "unmatched"
    )

    final_app_version = final_app_version.drop(columns=["_merge"])

    print("Final app-version dataset:", final_app_version.shape)
    print("\nMetadata merge status:")
    print(final_app_version["metadata_merge_status"].value_counts(dropna=False))
    print(final_app_version["metadata_merge_status"].value_counts(normalize=True, dropna=False))

    final_app_version.to_csv(out_app_version, index=False)
    print(f"\nSaved app-version dataset to: {out_app_version}")

    return final_app_version


# ==================================================
# Build app-SDK edge list
# ==================================================

def build_app_sdk_edges(final_app_version, out_app_sdk):
    rows = []

    for _, row in final_app_version.iterrows():
        sdk_names = parse_json_list(row.get("sdk_names_json"))
        sdk_roots = parse_json_list(row.get("sdk_roots_json"))
        sdk_types = parse_json_list(row.get("sdk_types_json"))

        for i, sdk_name in enumerate(sdk_names):
            sdk_root = sdk_roots[i] if i < len(sdk_roots) else None

            rows.append({
                "sha256": row.get("sha256"),
                "pkg_name": row.get("pkg_name"),
                "vercode": row.get("vercode"),
                "sdk_name": sdk_name,
                "sdk_root": sdk_root,
                "sdk_types_json": json.dumps(sdk_types, ensure_ascii=False),
            })

    app_sdk_edges = pd.DataFrame(rows)

    app_sdk_edges.to_csv(out_app_sdk, index=False)

    print("\nFinal app-SDK edge list:", app_sdk_edges.shape)
    print(f"Saved app-SDK edges to: {out_app_sdk}")

    return app_sdk_edges


# ==================================================
# Build app-SDK-provider edge list
# ==================================================

def build_app_sdk_provider_edges(app_sdk_edges, provider_path, out_app_sdk_provider):
    provider_path = Path(provider_path)

    provider = pd.read_csv(provider_path, dtype=str, low_memory=False)
    provider.columns = provider.columns.str.strip()

    if "sdk_name" not in provider.columns:
        raise ValueError(
            f"Provider mapping must contain 'sdk_name'. Available columns: {provider.columns.tolist()}"
        )

    if "company_canonical" not in provider.columns:
        raise ValueError(
            f"Provider mapping must contain 'company_canonical'. Available columns: {provider.columns.tolist()}"
        )

    provider["sdk_name_norm"] = provider["sdk_name"].astype(str).str.lower().str.strip()
    app_sdk_edges["sdk_name_norm"] = app_sdk_edges["sdk_name"].astype(str).str.lower().str.strip()

    provider_small = (
        provider[["sdk_name_norm", "company_canonical", "provider_source"]]
        .drop_duplicates(subset=["sdk_name_norm"])
    )

    app_sdk_provider_edges = app_sdk_edges.merge(
        provider_small,
        on="sdk_name_norm",
        how="left"
    )

    app_sdk_provider_edges = app_sdk_provider_edges.drop(columns=["sdk_name_norm"])

    app_sdk_provider_edges.to_csv(out_app_sdk_provider, index=False)

    print("\nFinal app-SDK-provider edge list:", app_sdk_provider_edges.shape)
    print(f"Saved app-SDK-provider edges to: {out_app_sdk_provider}")

    return app_sdk_provider_edges


# ==================================================
# Main
# ==================================================

def main():
    parser = argparse.ArgumentParser(
        description="Build final app-version, app-SDK, and app-SDK-provider datasets."
    )

    parser.add_argument(
        "--run-dir",
        default="outputs/app_sdk_dataset",
        help="Directory containing sdk_per_apk.csv and gp_metadata_full_flat.csv."
    )

    parser.add_argument(
        "--sdk-path",
        default=None,
        help="Optional explicit path to sdk_per_apk.csv."
    )

    parser.add_argument(
        "--meta-path",
        default=None,
        help="Optional explicit path to gp_metadata_full_flat.csv."
    )

    parser.add_argument(
        "--provider-path",
        default="outputs/provider_mapping/sdk_company_lookup_FINAL_CANONICAL.csv",
        help="Path to canonical SDK-provider mapping CSV."
    )

    parser.add_argument(
        "--outdir",
        default="outputs/final_datasets",
        help="Output directory for final datasets."
    )

    args = parser.parse_args()

    run_dir = Path(args.run_dir)

    sdk_path = Path(args.sdk_path) if args.sdk_path else run_dir / "sdk_per_apk.csv"
    meta_path = Path(args.meta_path) if args.meta_path else run_dir / "gp_metadata_full_flat.csv"

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    out_app_version = outdir / "final_app_version_dataset.csv"
    out_app_sdk = outdir / "final_app_sdk_edges.csv"
    out_app_sdk_provider = outdir / "final_app_sdk_provider_edges.csv"

    sdk = load_sdk_dataset(sdk_path)
    meta = load_metadata_dataset(meta_path)

    final_app_version = build_final_app_version_dataset(
        sdk=sdk,
        meta=meta,
        out_app_version=out_app_version
    )

    app_sdk_edges = build_app_sdk_edges(
        final_app_version=final_app_version,
        out_app_sdk=out_app_sdk
    )

    build_app_sdk_provider_edges(
        app_sdk_edges=app_sdk_edges,
        provider_path=args.provider_path,
        out_app_sdk_provider=out_app_sdk_provider
    )


if __name__ == "__main__":
    main()
