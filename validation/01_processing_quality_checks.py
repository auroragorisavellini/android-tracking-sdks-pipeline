#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
01_processing_quality_checks.py

Technical validation script for the Data Acquisition and Processing Quality
section of the Scientific Data manuscript.

This script computes:
1. APK download success rates
2. Metadata merge completeness
3. SDK detection plausibility statistics
4. Internal consistency checks
5. Summary table and SDK count distribution figure

Example:
    python validation/01_processing_quality_checks.py \
        --final-app-version outputs/final_datasets/final_app_version_dataset.csv \
        --outdir outputs/validation
"""

import argparse
import json
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt


def parse_json_list(value):
    """Safely parse JSON-encoded lists."""
    if pd.isna(value):
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def pct(x):
    """Convert proportion to percentage rounded to two decimals."""
    return round(100 * x, 2)


def main():
    parser = argparse.ArgumentParser(
        description="Technical validation: data acquisition and processing quality checks."
    )

    parser.add_argument(
        "--final-app-version",
        default="outputs/final_datasets/final_app_version_dataset.csv",
        help="Path to final_app_version_dataset.csv",
    )

    parser.add_argument(
        "--outdir",
        default="outputs/validation",
        help="Output directory for validation tables and figures",
    )

    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Load dataset
    # ------------------------------------------------------------------
    df = pd.read_csv(args.final_app_version, dtype=str, low_memory=False)
    df.columns = df.columns.str.strip()

    # ------------------------------------------------------------------
    # Basic dataset size
    # ------------------------------------------------------------------
    n_rows = len(df)
    n_apks = df["sha256"].nunique() if "sha256" in df.columns else None
    n_apps = df["pkg_name"].nunique() if "pkg_name" in df.columns else None

    # ------------------------------------------------------------------
    # APK download status
    # ------------------------------------------------------------------
    if "download_status" in df.columns:
        download_counts = (
            df["download_status"]
            .fillna("MISSING")
            .astype(str)
            .str.upper()
            .str.strip()
            .value_counts()
            .reset_index()
        )
        download_counts.columns = ["download_status", "count"]
        download_counts["share_percent"] = round(
            100 * download_counts["count"] / download_counts["count"].sum(), 2
        )
        download_counts.to_csv(outdir / "apk_download_status.csv", index=False)
    else:
        download_counts = pd.DataFrame(
            columns=["download_status", "count", "share_percent"]
        )

    # ------------------------------------------------------------------
    # Metadata merge completeness
    # ------------------------------------------------------------------
    if "metadata_merge_status" in df.columns:
        merge_counts = (
            df["metadata_merge_status"]
            .fillna("MISSING")
            .astype(str)
            .str.strip()
            .value_counts()
            .reset_index()
        )
        merge_counts.columns = ["metadata_merge_status", "count"]
        merge_counts["share_percent"] = round(
            100 * merge_counts["count"] / merge_counts["count"].sum(), 2
        )
        merge_counts.to_csv(outdir / "metadata_merge_status.csv", index=False)
    else:
        merge_counts = pd.DataFrame(
            columns=["metadata_merge_status", "count", "share_percent"]
        )

    # ------------------------------------------------------------------
    # SDK detection plausibility
    # ------------------------------------------------------------------
    if "sdk_count" in df.columns:
        df["sdk_count_num"] = pd.to_numeric(df["sdk_count"], errors="coerce").fillna(0)
    else:
        df["sdk_count_num"] = 0

    apps_with_sdk = int((df["sdk_count_num"] > 0).sum())
    share_apps_with_sdk = pct(apps_with_sdk / n_rows) if n_rows > 0 else None

    sdk_summary = pd.DataFrame(
        [
            {
                "rows": n_rows,
                "unique_apks": n_apks,
                "unique_apps": n_apps,
                "apps_with_at_least_one_sdk": apps_with_sdk,
                "share_apps_with_at_least_one_sdk_percent": share_apps_with_sdk,
                "mean_sdk_count": round(df["sdk_count_num"].mean(), 3),
                "median_sdk_count": round(df["sdk_count_num"].median(), 3),
                "min_sdk_count": int(df["sdk_count_num"].min()),
                "max_sdk_count": int(df["sdk_count_num"].max()),
            }
        ]
    )
    sdk_summary.to_csv(outdir / "sdk_detection_summary.csv", index=False)

    sdk_count_distribution = (
        df["sdk_count_num"].value_counts().sort_index().reset_index()
    )
    sdk_count_distribution.columns = ["sdk_count", "count"]
    sdk_count_distribution["share_percent"] = round(
        100
        * sdk_count_distribution["count"]
        / sdk_count_distribution["count"].sum(),
        2,
    )
    sdk_count_distribution.to_csv(
        outdir / "sdk_count_distribution.csv", index=False
    )

    # ------------------------------------------------------------------
    # Internal consistency checks
    # ------------------------------------------------------------------
    if all(col in df.columns for col in ["sha256", "pkg_name", "vercode"]):
        duplicate_key_count = int(
            df.duplicated(subset=["sha256", "pkg_name", "vercode"]).sum()
        )
    else:
        duplicate_key_count = None

    if "sdk_names_json" in df.columns:
        df["sdk_names_list"] = df["sdk_names_json"].apply(parse_json_list)
        df["sdk_names_json_length"] = df["sdk_names_list"].apply(len)

        inconsistent_sdk_count = int(
            (
                df["sdk_count_num"].astype(int)
                != df["sdk_names_json_length"]
            ).sum()
        )
    else:
        inconsistent_sdk_count = None

    consistency = pd.DataFrame(
        [
            {
                "duplicate_sha256_pkg_vercode_rows": duplicate_key_count,
                "rows_where_sdk_count_differs_from_sdk_names_json_length": (
                    inconsistent_sdk_count
                ),
            }
        ]
    )
    consistency.to_csv(
        outdir / "internal_consistency_checks.csv", index=False
    )

    # ------------------------------------------------------------------
    # Figure: SDK count distribution
    # ------------------------------------------------------------------
    plt.figure(figsize=(8, 5))
    plt.hist(df["sdk_count_num"], bins=30)
    plt.xlabel("Number of detected SDKs per APK")
    plt.ylabel("Number of APKs")
    plt.title("Distribution of detected SDKs per APK")
    plt.tight_layout()
    plt.savefig(outdir / "sdk_count_distribution.png", dpi=300)
    plt.close()

    # ------------------------------------------------------------------
    # Combined technical validation summary
    # ------------------------------------------------------------------
    ok_rate = None
    if (
        not download_counts.empty
        and "OK" in download_counts["download_status"].values
    ):
        ok_rate = download_counts.loc[
            download_counts["download_status"] == "OK",
            "share_percent",
        ].iloc[0]

    merge_rate = None
    if (
        not merge_counts.empty
        and "matched"
        in merge_counts["metadata_merge_status"].astype(str).str.lower().values
    ):
        merge_rate = merge_counts.loc[
            merge_counts["metadata_merge_status"]
            .astype(str)
            .str.lower()
            == "matched",
            "share_percent",
        ].iloc[0]

    validation_summary = pd.DataFrame(
        [
            {"metric": "Total app-version records", "value": n_rows},
            {"metric": "Unique APKs", "value": n_apks},
            {"metric": "Unique package names", "value": n_apps},
            {"metric": "APK download success rate (%)", "value": ok_rate},
            {"metric": "Metadata merge rate (%)", "value": merge_rate},
            {
                "metric": "Apps with at least one detected SDK (%)",
                "value": share_apps_with_sdk,
            },
            {
                "metric": "Mean detected SDKs per APK",
                "value": round(df["sdk_count_num"].mean(), 3),
            },
            {
                "metric": "Median detected SDKs per APK",
                "value": round(df["sdk_count_num"].median(), 3),
            },
            {"metric": "Duplicate key rows", "value": duplicate_key_count},
            {
                "metric": "SDK count inconsistencies",
                "value": inconsistent_sdk_count,
            },
        ]
    )

    validation_summary.to_csv(
        outdir / "processing_quality_summary.csv", index=False
    )

    print("\nProcessing quality validation completed.")
    print(f"Outputs saved to: {outdir}")
    print("\nSummary:")
    print(validation_summary.to_string(index=False))


if __name__ == "__main__":
    main()
