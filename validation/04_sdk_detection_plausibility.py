#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
04_sdk_detection_plausibility.py

Technical validation script for assessing the plausibility of SDK detection.

This script compares the prevalence of the most common SDKs detected in the
study sample with aggregate tracker prevalence statistics manually extracted
from Exodus Privacy tracker reports.

The script:
1. loads the full app-version dataset;
2. keeps successfully downloaded APKs with at least one SDK;
3. selects the latest available version for each package name;
4. parses sdk_names_json;
5. computes SDK prevalence across unique applications;
6. compares sample prevalence with Exodus Privacy benchmark values;
7. computes top-20 overlap, Spearman rank correlation, Pearson prevalence
   correlation, and mean absolute difference in prevalence;
8. saves validation tables to the specified output directory.

Example:
    python validation/04_sdk_detection_plausibility.py \
        --full-dataset outputs/final_datasets/final_app_version_dataset.csv \
        --outdir outputs/validation/sdk_detection_plausibility
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr


# ==================================================
# Exodus benchmark
# ==================================================

EXODUS_DATA = [
    ("Google Firebase Analytics", 173086, 59),
    ("Google AdMob", 126790, 43),
    ("Google Crashlytics", 106870, 36),
    ("Facebook Login", 57184, 19),
    ("Facebook Share", 52164, 17),
    ("Facebook Analytics", 48826, 16),
    ("Facebook Ads", 47412, 16),
    ("IAB Open Measurement", 39208, 13),
    ("Unity3d Ads", 37218, 12),
    ("AppLovin (MAX and SparkLabs)", 35402, 12),
    ("Google Analytics", 31765, 10),
    ("Amazon Advertisement", 29588, 10),
    ("ironSource", 26439, 9),
    ("Google Tag Manager", 26366, 9),
    ("AppsFlyer", 23717, 8),
    ("Mintegral", 21987, 7),
    ("Inmobi", 21980, 7),
    ("Pangle", 21646, 7),
    ("Fyber", 17533, 6),
    ("Huawei Mobile Services (HMS) Core", 15847, 5),
    ("OneSignal", 15631, 5),
]


NAME_MAP = {
    "Google Firebase Analytics": "Google Firebase Analytics",
    "Google AdMob": "Google AdMob",
    "Google Crashlytics": "Google CrashLytics",
    "Facebook Login": "Facebook Login",
    "Facebook Share": "Facebook Share",
    "Facebook Analytics": "Facebook Analytics",
    "Facebook Ads": "Facebook Ads",
    "IAB Open Measurement": "IAB Open Measurement",
    "Unity3d Ads": "Unity3d Ads",
    "AppLovin (MAX and SparkLabs)": "AppLovin (MAX and SparkLabs)",
    "Google Analytics": "Google Analytics",
    "Amazon Advertisement": "Amazon Advertisement",
    "ironSource": "ironSource",
    "Google Tag Manager": "Google Tag Manager",
    "AppsFlyer": "AppsFlyer",
    "Mintegral": "Mintegral",
    "Inmobi": "Inmobi",
    "Pangle": "Pangle",
    "Fyber": "Fyber",
    "Huawei Mobile Services (HMS) Core": "Huawei Mobile Services (HMS) Core",
    "OneSignal": "OneSignal",
}


def parse_sdk_list(value):
    """Parse a JSON-encoded SDK list."""
    if pd.isna(value):
        return []

    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]

    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(v).strip() for v in parsed if str(v).strip()]
    except Exception:
        return []

    return []


def build_exodus_benchmark():
    """Build the Exodus benchmark table from manually extracted values."""
    exodus_df = pd.DataFrame(
        EXODUS_DATA,
        columns=[
            "sdk_name_exodus",
            "exodus_app_count",
            "exodus_prevalence_percent",
        ],
    )

    exodus_df["exodus_rank"] = np.arange(1, len(exodus_df) + 1)
    exodus_df["sdk_name_match"] = exodus_df["sdk_name_exodus"].map(NAME_MAP)

    return exodus_df


def load_latest_app_versions(full_dataset):
    """
    Load the full app-version dataset and keep the latest available APK
    for each package name.
    """
    required_cols = [
        "pkg_name",
        "vercode",
        "dex_date",
        "extraction_ts_utc",
        "download_status",
        "sdk_count",
        "sdk_names_json",
    ]

    df = pd.read_csv(
        full_dataset,
        usecols=required_cols,
        dtype=str,
        low_memory=False,
    )

    df.columns = df.columns.str.strip()

    df = df[df["download_status"].astype(str).str.upper() == "OK"].copy()
    df["sdk_count_num"] = pd.to_numeric(df["sdk_count"], errors="coerce").fillna(0)
    df = df[df["sdk_count_num"] > 0].copy()

    df["vercode_num"] = pd.to_numeric(df["vercode"], errors="coerce")
    df["dex_date_dt"] = pd.to_datetime(df["dex_date"], errors="coerce")
    df["extraction_ts_dt"] = pd.to_datetime(df["extraction_ts_utc"], errors="coerce")

    df = df.sort_values(
        by=["pkg_name", "vercode_num", "dex_date_dt", "extraction_ts_dt"],
        ascending=[True, False, False, False],
    )

    latest_df = df.drop_duplicates(subset=["pkg_name"], keep="first").copy()

    latest_df["sdk_list"] = latest_df["sdk_names_json"].apply(parse_sdk_list)
    latest_df = latest_df[latest_df["sdk_list"].map(len) > 0].copy()

    return latest_df


def compute_sample_sdk_prevalence(latest_df):
    """Compute SDK prevalence across unique applications."""
    n_apps = latest_df["pkg_name"].nunique()

    edges = latest_df[["pkg_name", "sdk_list"]].explode("sdk_list")
    edges = edges.rename(columns={"sdk_list": "sdk_name"})
    edges["sdk_name"] = edges["sdk_name"].astype(str).str.strip()
    edges = edges[edges["sdk_name"] != ""]
    edges = edges.drop_duplicates(subset=["pkg_name", "sdk_name"])

    sample_df = (
        edges.groupby("sdk_name")["pkg_name"]
        .nunique()
        .reset_index(name="sample_app_count")
        .sort_values("sample_app_count", ascending=False)
        .reset_index(drop=True)
    )

    sample_df["sample_prevalence_percent"] = (
        sample_df["sample_app_count"] / n_apps * 100
    )
    sample_df["sample_rank"] = np.arange(1, len(sample_df) + 1)

    return sample_df, n_apps


def build_comparison(sample_df, exodus_df):
    """Merge sample prevalence with the Exodus benchmark and compute differences."""
    comparison = exodus_df.merge(
        sample_df,
        left_on="sdk_name_match",
        right_on="sdk_name",
        how="left",
    )

    comparison["sample_app_count"] = (
        comparison["sample_app_count"]
        .fillna(0)
        .astype(int)
    )

    comparison["sample_prevalence_percent"] = (
        comparison["sample_prevalence_percent"]
        .fillna(0)
    )

    comparison["sample_rank_within_exodus_top"] = (
        comparison["sample_prevalence_percent"]
        .rank(ascending=False, method="min")
        .astype(int)
    )

    comparison["difference_percentage_points"] = (
        comparison["sample_prevalence_percent"]
        - comparison["exodus_prevalence_percent"]
    )

    comparison["absolute_difference_percentage_points"] = (
        comparison["difference_percentage_points"].abs()
    )

    return comparison


def safe_spearman(x, y):
    if len(x) < 2:
        return np.nan
    return spearmanr(x, y).correlation


def safe_pearson(x, y):
    if len(x) < 2:
        return np.nan
    if pd.Series(x).nunique(dropna=True) < 2 or pd.Series(y).nunique(dropna=True) < 2:
        return np.nan
    return pearsonr(x, y)[0]


def compute_summary(sample_df, comparison, n_apps):
    """Compute summary metrics for the plausibility check."""
    spearman_rank_corr = safe_spearman(
        comparison["exodus_rank"],
        comparison["sample_rank_within_exodus_top"],
    )

    pearson_prev_corr = safe_pearson(
        comparison["exodus_prevalence_percent"],
        comparison["sample_prevalence_percent"],
    )

    top20_sample = set(sample_df.head(20)["sdk_name"])
    top20_exodus_mapped = set(comparison["sdk_name_match"])
    top20_overlap = len(top20_sample.intersection(top20_exodus_mapped))

    summary = pd.DataFrame([
        {
            "metric": "Unique apps in latest-version sample",
            "value": n_apps,
        },
        {
            "metric": "Unique SDKs detected in sample",
            "value": sample_df["sdk_name"].nunique(),
        },
        {
            "metric": "Exodus SDKs used for benchmark",
            "value": len(comparison),
        },
        {
            "metric": "Top-20 overlap between sample and Exodus benchmark",
            "value": top20_overlap,
        },
        {
            "metric": "Spearman rank correlation",
            "value": round(spearman_rank_corr, 4)
            if pd.notna(spearman_rank_corr)
            else np.nan,
        },
        {
            "metric": "Pearson prevalence correlation",
            "value": round(pearson_prev_corr, 4)
            if pd.notna(pearson_prev_corr)
            else np.nan,
        },
        {
            "metric": "Mean absolute difference in prevalence, percentage points",
            "value": round(
                comparison["absolute_difference_percentage_points"].mean(),
                4,
            ),
        },
    ])

    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Validate SDK detection plausibility against Exodus Privacy statistics."
    )

    parser.add_argument(
        "--full-dataset",
        required=True,
        help="Path to the full app-version dataset containing sdk_names_json.",
    )

    parser.add_argument(
        "--outdir",
        default="outputs/validation/sdk_detection_plausibility",
        help="Output directory for validation tables.",
    )

    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print("Loading latest application versions...")
    latest_df = load_latest_app_versions(args.full_dataset)
    n_latest = latest_df["pkg_name"].nunique()
    print(f"Unique apps, latest version only: {n_latest}")

    print("Computing SDK prevalence in sample...")
    sample_df, n_apps = compute_sample_sdk_prevalence(latest_df)
    print(f"Apps with at least one parsed SDK: {n_apps}")

    print("Building Exodus benchmark...")
    exodus_df = build_exodus_benchmark()

    print("Comparing sample prevalence with Exodus benchmark...")
    comparison = build_comparison(sample_df, exodus_df)
    summary = compute_summary(sample_df, comparison, n_apps)

    sample_df.to_csv(outdir / "sample_sdk_prevalence.csv", index=False)
    exodus_df.to_csv(outdir / "exodus_sdk_benchmark.csv", index=False)
    comparison.to_csv(
        outdir / "sdk_detection_plausibility_comparison.csv",
        index=False,
    )
    summary.to_csv(
        outdir / "sdk_detection_plausibility_summary.csv",
        index=False,
    )

    print("\nSDK detection plausibility validation completed.")
    print(f"Outputs saved to: {outdir}")
    print("\nSummary:")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
