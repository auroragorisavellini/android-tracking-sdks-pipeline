#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
03c_sample_representativeness_free_vs_paid.py

Validate sample representativeness by comparing the share of free and paid
applications in the dataset against external Google Play population counts.

Classification rule:
    Free = meta_offer.0.micros is missing or equal to 0
    Paid = meta_offer.0.micros is greater than 0

The field meta_offer.0.micros stores the app price in micro-units of the
corresponding currency. A value of 0 indicates a free app; positive values
indicate paid apps.

Example:
    python validation/03c_sample_representativeness_free_vs_paid.py \
        --input-dataset "/content/drive/MyDrive/Massimo SDK Innovation Project/sdk_with_gp_metadata_exactmerge.csv" \
        --population-free-count 2274378 \
        --population-paid-count 69250 \
        --outdir outputs/validation_full/sample_representativeness_free_vs_paid
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


PRICE_COL = "meta_offer.0.micros"


def clean_columns(df):
    df = df.copy()
    df.columns = (
        df.columns.astype(str)
        .str.replace("\ufeff", "", regex=False)
        .str.strip()
    )
    return df


def load_latest_apps(input_dataset, include_apps_without_sdk=False):
    """
    Load the app-version dataset, keep successfully downloaded APKs,
    optionally require at least one detected SDK, and select the latest
    version for each package name.
    """
    required_cols = [
        "pkg_name",
        "vercode",
        "dex_date",
        "extraction_ts_utc",
        "download_status",
        "sdk_count",
        PRICE_COL,
    ]

    df = pd.read_csv(
        input_dataset,
        usecols=lambda c: str(c).replace("\ufeff", "").strip() in required_cols,
        dtype=str,
        low_memory=False,
    )
    df = clean_columns(df)

    missing = sorted(set(required_cols) - set(df.columns))
    if missing:
        raise ValueError(
            f"Missing required columns in input dataset: {missing}. "
            f"Available columns loaded: {df.columns.tolist()}"
        )

    df["pkg_name"] = df["pkg_name"].astype(str).str.strip()
    df = df[df["pkg_name"] != ""].copy()

    df = df[df["download_status"].astype(str).str.upper() == "OK"].copy()

    if not include_apps_without_sdk:
        df["sdk_count_num"] = pd.to_numeric(df["sdk_count"], errors="coerce").fillna(0)
        df = df[df["sdk_count_num"] > 0].copy()

    df["vercode_num"] = pd.to_numeric(df["vercode"], errors="coerce")
    df["dex_date_dt"] = pd.to_datetime(df["dex_date"], errors="coerce")
    df["extraction_ts_dt"] = pd.to_datetime(df["extraction_ts_utc"], errors="coerce")

    df = df.sort_values(
        by=["pkg_name", "vercode_num", "dex_date_dt", "extraction_ts_dt"],
        ascending=[True, False, False, False],
    )

    latest = df.drop_duplicates(subset=["pkg_name"], keep="first").copy()

    latest[PRICE_COL] = pd.to_numeric(latest[PRICE_COL], errors="coerce")

    return latest[["pkg_name", PRICE_COL]].drop_duplicates(subset=["pkg_name"]).copy()


def classify_free_vs_paid(price_micros):
    """
    Classify app monetization status using Google Play price in micros.

    Missing prices are treated as Free because the Google Play metadata used here
    encodes paid apps with strictly positive micros values, while free apps are
    normally encoded as 0.
    """
    if pd.isna(price_micros):
        return "Free"

    try:
        price = float(price_micros)
    except Exception:
        return "Free"

    if price > 0:
        return "Paid"

    return "Free"


def compute_free_vs_paid(
    latest_apps,
    population_free_count,
    population_paid_count,
):
    apps = latest_apps.copy()
    apps["price_micros"] = pd.to_numeric(apps[PRICE_COL], errors="coerce")
    apps["free_paid_status"] = apps["price_micros"].apply(classify_free_vs_paid)

    processed = apps["pkg_name"].nunique()
    classified = apps["free_paid_status"].notna().sum()
    missing_price = apps["price_micros"].isna().sum()
    positive_price = (apps["price_micros"].fillna(0) > 0).sum()

    sample_counts = (
        apps.groupby("free_paid_status")["pkg_name"]
        .nunique()
        .reset_index(name="sample_app_count")
    )

    population_df = pd.DataFrame(
        [
            {
                "free_paid_status": "Free",
                "population_count": int(population_free_count),
            },
            {
                "free_paid_status": "Paid",
                "population_count": int(population_paid_count),
            },
        ]
    )

    population_total = population_df["population_count"].sum()
    sample_total = sample_counts["sample_app_count"].sum()

    population_df["population_percent"] = (
        population_df["population_count"] / population_total * 100
    )

    sample_counts["sample_percent"] = (
        sample_counts["sample_app_count"] / sample_total * 100
        if sample_total > 0
        else np.nan
    )

    comparison = population_df.merge(sample_counts, on="free_paid_status", how="left")
    comparison["sample_app_count"] = comparison["sample_app_count"].fillna(0).astype(int)
    comparison["sample_percent"] = comparison["sample_percent"].fillna(0)

    comparison["difference_percentage_points"] = (
        comparison["sample_percent"] - comparison["population_percent"]
    )
    comparison["absolute_difference_percentage_points"] = (
        comparison["difference_percentage_points"].abs()
    )

    coverage = pd.DataFrame(
        [
            {
                "metric": "Unique package names selected for representativeness check",
                "value": processed,
            },
            {
                "metric": "Packages classified as free or paid",
                "value": classified,
            },
            {
                "metric": "Packages with missing price micros",
                "value": missing_price,
            },
            {
                "metric": "Packages with positive price micros",
                "value": positive_price,
            },
            {
                "metric": "Free/paid classification coverage (%)",
                "value": round(classified / processed * 100, 2) if processed > 0 else np.nan,
            },
        ]
    )

    summary = pd.DataFrame(
        [
            {
                "metric": "Population total apps",
                "value": population_total,
            },
            {
                "metric": "Sample apps with free/paid classification",
                "value": sample_total,
            },
            {
                "metric": "Free apps in sample",
                "value": int(
                    comparison.loc[
                        comparison["free_paid_status"] == "Free", "sample_app_count"
                    ].iloc[0]
                ),
            },
            {
                "metric": "Paid apps in sample",
                "value": int(
                    comparison.loc[
                        comparison["free_paid_status"] == "Paid", "sample_app_count"
                    ].iloc[0]
                ),
            },
            {
                "metric": "Mean absolute difference, percentage points",
                "value": round(
                    comparison["absolute_difference_percentage_points"].mean(), 4
                ),
            },
            {
                "metric": "Maximum absolute difference, percentage points",
                "value": round(
                    comparison["absolute_difference_percentage_points"].max(), 4
                ),
            },
        ]
    )

    return apps, coverage, comparison, summary


def main():
    parser = argparse.ArgumentParser(
        description="Compare free vs paid composition between sample and Google Play population."
    )

    parser.add_argument(
        "--input-dataset",
        required=True,
        help="Full app-version dataset CSV.",
    )
    parser.add_argument(
        "--population-free-count",
        required=True,
        type=int,
        help="Google Play population count for free apps.",
    )
    parser.add_argument(
        "--population-paid-count",
        required=True,
        type=int,
        help="Google Play population count for paid apps.",
    )
    parser.add_argument(
        "--outdir",
        default="outputs/validation/sample_representativeness_free_vs_paid",
        help="Output directory.",
    )
    parser.add_argument(
        "--include-apps-without-sdk",
        action="store_true",
        help="Include apps with zero detected SDKs.",
    )

    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    latest_apps = load_latest_apps(
        args.input_dataset,
        include_apps_without_sdk=args.include_apps_without_sdk,
    )

    apps, coverage, comparison, summary = compute_free_vs_paid(
        latest_apps=latest_apps,
        population_free_count=args.population_free_count,
        population_paid_count=args.population_paid_count,
    )

    apps.to_csv(outdir / "latest_apps_with_free_paid_status.csv", index=False)
    coverage.to_csv(outdir / "free_vs_paid_coverage.csv", index=False)
    comparison.to_csv(outdir / "free_vs_paid_comparison.csv", index=False)
    summary.to_csv(outdir / "free_vs_paid_summary.csv", index=False)

    print("Free vs paid representativeness outputs saved to:", outdir)

    print("\nCoverage:")
    print(coverage.to_string(index=False))

    print("\nComparison:")
    print(comparison.to_string(index=False))

    print("\nSummary:")
    print(summary.to_string(index=False))

    print("\nMatched sample sanity check:")
    print(apps["free_paid_status"].value_counts(dropna=False).to_string())


if __name__ == "__main__":
    main()
