#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
03b_sample_representativeness_game_vs_nongame_fixed.py

Fixed version of the game vs non-game representativeness validation.

This script compares the share of gaming and non-gaming apps in the dataset
against external Google Play population counts.

Classification rule:
    Game     = gp_genreId starts with "GAME" (e.g. GAME_PUZZLE, GAME_ARCADE)
    Non-game = all other Google Play categories with available metadata

Expected category file columns:
    pkg_name, gp_genre, gp_genreId

Example:
    python validation/03b_sample_representativeness_game_vs_nongame_fixed.py \
        --input-dataset "/content/drive/MyDrive/Massimo SDK Innovation Project/sdk_with_gp_metadata_exactmerge.csv" \
        --category-file "/content/drive/MyDrive/Massimo SDK Innovation Project/play_categories_final.csv" \
        --population-game-count 279603 \
        --population-nongame-count 2064795 \
        --outdir outputs/validation_full/sample_representativeness_game_vs_nongame_fixed
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def clean_columns(df):
    df = df.copy()
    df.columns = (
        df.columns.astype(str)
        .str.replace("\ufeff", "", regex=False)
        .str.strip()
    )
    return df


def load_latest_apps(input_dataset, include_apps_without_sdk=False):
    required_cols = [
        "pkg_name",
        "vercode",
        "dex_date",
        "extraction_ts_utc",
        "download_status",
        "sdk_count",
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
            f"Available columns: {df.columns.tolist()}"
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
    latest = latest[["pkg_name"]].drop_duplicates().copy()

    return latest


def load_categories(category_file):
    cat = pd.read_csv(category_file, dtype=str, low_memory=False)
    cat = clean_columns(cat)

    if "pkg_name" not in cat.columns:
        raise ValueError(
            f"Column 'pkg_name' not found in category file. "
            f"Available columns: {cat.columns.tolist()}"
        )

    if "gp_genre" not in cat.columns:
        cat["gp_genre"] = np.nan

    if "gp_genreId" not in cat.columns:
        raise ValueError(
            f"Column 'gp_genreId' not found in category file. "
            f"Available columns: {cat.columns.tolist()}"
        )

    cat["pkg_name"] = cat["pkg_name"].astype(str).str.strip()
    cat = cat[cat["pkg_name"] != ""].copy()
    cat = cat.drop_duplicates(subset=["pkg_name"], keep="last").copy()

    return cat[["pkg_name", "gp_genre", "gp_genreId"]]


def classify_game_status_from_genre_id(gp_genre_id):
    """
    Core classification rule.

    Examples:
        GAME_PUZZLE     -> Game
        GAME_ARCADE     -> Game
        EDUCATION       -> Non-game
        TOOLS           -> Non-game
        NaN             -> NaN
    """
    if pd.isna(gp_genre_id):
        return np.nan

    genre_id = str(gp_genre_id).strip().upper()

    if genre_id == "" or genre_id == "NAN":
        return np.nan

    if genre_id.startswith("GAME"):
        return "Game"

    return "Non-game"


def compute_game_vs_nongame(
    latest_apps,
    category_df,
    population_game_count,
    population_nongame_count,
):
    merged = latest_apps.merge(
        category_df[["pkg_name", "gp_genre", "gp_genreId"]],
        on="pkg_name",
        how="left",
    )

    merged["game_status"] = merged["gp_genreId"].apply(
        classify_game_status_from_genre_id
    )

    processed = latest_apps["pkg_name"].nunique()
    found_category = merged["gp_genreId"].notna().sum()
    classified = merged["game_status"].notna().sum()

    sample_counts = (
        merged.dropna(subset=["game_status"])
        .groupby("game_status")["pkg_name"]
        .nunique()
        .reset_index(name="sample_app_count")
    )

    population_df = pd.DataFrame(
        [
            {
                "game_status": "Game",
                "population_count": int(population_game_count),
            },
            {
                "game_status": "Non-game",
                "population_count": int(population_nongame_count),
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

    comparison = population_df.merge(sample_counts, on="game_status", how="left")
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
                "metric": "Packages with retrieved Google Play genreId",
                "value": found_category,
            },
            {
                "metric": "Packages classified as game or non-game",
                "value": classified,
            },
            {
                "metric": "Google Play category coverage (%)",
                "value": round(found_category / processed * 100, 2)
                if processed > 0
                else np.nan,
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
                "metric": "Sample apps with game/non-game classification",
                "value": sample_total,
            },
            {
                "metric": "Game apps in matched sample",
                "value": int(
                    comparison.loc[
                        comparison["game_status"] == "Game", "sample_app_count"
                    ].iloc[0]
                ),
            },
            {
                "metric": "Non-game apps in matched sample",
                "value": int(
                    comparison.loc[
                        comparison["game_status"] == "Non-game", "sample_app_count"
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

    return merged, coverage, comparison, summary


def main():
    parser = argparse.ArgumentParser(
        description="Fixed game vs non-game representativeness validation."
    )

    parser.add_argument(
        "--input-dataset",
        required=True,
        help="Full app-version dataset CSV.",
    )
    parser.add_argument(
        "--category-file",
        required=True,
        help="Google Play category metadata CSV with pkg_name and gp_genreId.",
    )
    parser.add_argument(
        "--population-game-count",
        required=True,
        type=int,
        help="Google Play population count for gaming apps.",
    )
    parser.add_argument(
        "--population-nongame-count",
        required=True,
        type=int,
        help="Google Play population count for non-gaming apps.",
    )
    parser.add_argument(
        "--outdir",
        default="outputs/validation/sample_representativeness_game_vs_nongame_fixed",
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
    category_df = load_categories(args.category_file)

    # Sanity check before merging
    game_rows_in_category_file = (
        category_df["gp_genreId"].astype(str).str.upper().str.startswith("GAME").sum()
    )
    print(f"Sanity check: game rows in category file = {game_rows_in_category_file}")

    merged, coverage, comparison, summary = compute_game_vs_nongame(
        latest_apps=latest_apps,
        category_df=category_df,
        population_game_count=args.population_game_count,
        population_nongame_count=args.population_nongame_count,
    )

    merged.to_csv(outdir / "latest_apps_with_game_status.csv", index=False)
    coverage.to_csv(outdir / "game_vs_nongame_coverage.csv", index=False)
    comparison.to_csv(outdir / "game_vs_nongame_comparison.csv", index=False)
    summary.to_csv(outdir / "game_vs_nongame_summary.csv", index=False)

    print("Game vs non-game representativeness outputs saved to:", outdir)

    print("\nCoverage:")
    print(coverage.to_string(index=False))

    print("\nComparison:")
    print(comparison.to_string(index=False))

    print("\nSummary:")
    print(summary.to_string(index=False))

    print("\nMatched sample sanity check:")
    print(merged["game_status"].value_counts(dropna=False).to_string())


if __name__ == "__main__":
    main()
