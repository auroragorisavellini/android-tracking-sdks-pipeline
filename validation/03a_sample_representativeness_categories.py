#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
03_sample_representativeness.py

Technical validation script for sample representativeness.

The script:
1. selects one latest APK version per package name from the final dataset;
2. optionally scrapes current Google Play category metadata for apps still
   available on Google Play;
3. saves a resumable category table;
4. optionally merges scraped categories back into the final dataset;
5. compares the sample category distribution with aggregate Google Play
   statistics from 42matters;
6. exports validation tables and a figure.

Important:
Google Play categories can only be retrieved for apps that are still available
on Google Play at execution time. Some apps present in AndroZoo may no longer be
active on Google Play, so category coverage is expected to be incomplete.

Example:
    python validation/03_sample_representativeness.py \
        --input-dataset "/content/drive/MyDrive/Massimo SDK Innovation Project/sdk_with_gp_metadata_exactmerge.csv" \
        --category-out outputs/validation/play_categories_final.csv \
        --merged-out outputs/validation/final_dataset_with_play_categories.csv \
        --outdir outputs/validation \
        --scrape
"""

import argparse
import ast
import json
import random
import time
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
from tqdm.auto import tqdm


try:
    from google_play_scraper import app
except Exception:
    app = None


TEMP_ERROR_KEYWORDS = [
    "503",
    "429",
    "too many requests",
    "captcha",
    "timed out",
    "timeout",
    "connection",
    "reset",
    "temporarily unavailable",
    "service unavailable",
]

NOT_FOUND_KEYWORDS = [
    "not found",
    "does not exist",
    "no application was found",
]


def parse_json_list(x):
    if pd.isna(x):
        return []
    if isinstance(x, list):
        return [str(v).strip() for v in x if str(v).strip()]
    if not isinstance(x, str):
        return []

    x = x.strip()
    if not x:
        return []

    try:
        out = json.loads(x)
        if isinstance(out, list):
            return [str(v).strip() for v in out if str(v).strip()]
    except Exception:
        pass

    try:
        out = ast.literal_eval(x)
        if isinstance(out, list):
            return [str(v).strip() for v in out if str(v).strip()]
    except Exception:
        pass

    return []


def classify_error_message(msg):
    if msg is None:
        return "unknown_error"

    m = str(msg).lower()

    if any(k in m for k in TEMP_ERROR_KEYWORDS):
        return "temporary_error"

    if any(k in m for k in NOT_FOUND_KEYWORDS):
        return "not_found"

    return "other_error"


def normalize_categories_field(result):
    categories = result.get("categories")
    if categories is None:
        return None
    try:
        return json.dumps(categories, ensure_ascii=False)
    except Exception:
        return str(categories)


def fetch_app_once(pkg_name, lang="en", country="us"):
    if app is None:
        raise ImportError(
            "google-play-scraper is not installed. "
            "Install it with: pip install google-play-scraper"
        )

    try:
        result = app(pkg_name, lang=lang, country=country)
        return {
            "pkg_name": pkg_name,
            "gp_found": True,
            "gp_status": "found",
            "gp_country_used": country,
            "gp_lang_used": lang,
            "gp_title_scraped": result.get("title"),
            "gp_genre": result.get("genre"),
            "gp_genreId": result.get("genreId"),
            "gp_categories_json": normalize_categories_field(result),
            "gp_url": f"https://play.google.com/store/apps/details?id={pkg_name}",
            "gp_error": None,
        }
    except Exception as e:
        err = str(e)
        return {
            "pkg_name": pkg_name,
            "gp_found": False,
            "gp_status": classify_error_message(err),
            "gp_country_used": country,
            "gp_lang_used": lang,
            "gp_title_scraped": None,
            "gp_genre": None,
            "gp_genreId": None,
            "gp_categories_json": None,
            "gp_url": f"https://play.google.com/store/apps/details?id={pkg_name}",
            "gp_error": err,
        }


def scrape_play_category_robust(
    pkg_name,
    lang="en",
    countries=("us", "it"),
    max_retries_temp=2,
    sleep_range=(0.6, 1.4),
    retry_sleep_range=(2.0, 5.0),
):
    last_result = None

    for country in countries:
        temp_attempt = 0

        while True:
            result = fetch_app_once(pkg_name, lang=lang, country=country)
            last_result = result

            if result["gp_status"] == "found":
                time.sleep(random.uniform(*sleep_range))
                return result

            if result["gp_status"] == "not_found":
                break

            if result["gp_status"] == "temporary_error":
                temp_attempt += 1
                if temp_attempt <= max_retries_temp:
                    time.sleep(random.uniform(*retry_sleep_range))
                    continue
                break

            break

    time.sleep(random.uniform(*sleep_range))
    return last_result


def select_latest_package_records(df, require_ok=True, require_sdk=True):
    df = df.copy()

    if require_ok and "download_status" in df.columns:
        df = df[df["download_status"].astype(str).str.upper() == "OK"].copy()

    if require_sdk and "sdk_count" in df.columns:
        df["sdk_count_num"] = pd.to_numeric(df["sdk_count"], errors="coerce").fillna(0)
        df = df[df["sdk_count_num"] > 0].copy()

    df["vercode_num"] = pd.to_numeric(df.get("vercode"), errors="coerce")
    df["dex_date_dt"] = pd.to_datetime(df.get("dex_date"), errors="coerce")
    df["extraction_ts_dt"] = pd.to_datetime(df.get("extraction_ts_utc"), errors="coerce")

    df = df.sort_values(
        by=["pkg_name", "vercode_num", "dex_date_dt", "extraction_ts_dt"],
        ascending=[True, False, False, False],
    )

    latest_df = df.drop_duplicates(subset=["pkg_name"], keep="first").copy()

    if "sdk_names_json" in latest_df.columns:
        latest_df["sdk_list"] = latest_df["sdk_names_json"].apply(parse_json_list)
        if require_sdk:
            latest_df = latest_df[latest_df["sdk_list"].map(len) > 0].copy()

    return latest_df


def scrape_categories(pkg_names, category_out, args):
    category_out = Path(category_out)
    category_out.parent.mkdir(parents=True, exist_ok=True)

    if category_out.exists():
        done_df = pd.read_csv(category_out, dtype=str, low_memory=False)
        done_df["pkg_name"] = done_df["pkg_name"].astype(str).str.strip()
        done_pkgs = set(done_df["pkg_name"])
        print(f"Existing category file found: {category_out}")
        print(f"Packages already processed: {len(done_pkgs)}")
    else:
        done_pkgs = set()
        print("No existing category file found. Starting from scratch.")

    todo_pkgs = [p for p in pkg_names if p not in done_pkgs]

    if args.max_apps is not None:
        todo_pkgs = todo_pkgs[: args.max_apps]

    print(f"Total packages in sample: {len(pkg_names)}")
    print(f"Packages to process now: {len(todo_pkgs)}")

    countries = tuple(c.strip() for c in args.countries.split(",") if c.strip())
    buffer = []

    for i, pkg in enumerate(tqdm(todo_pkgs), start=1):
        row = scrape_play_category_robust(
            pkg_name=pkg,
            lang=args.lang,
            countries=countries,
            max_retries_temp=args.max_retries_temp,
            sleep_range=(args.sleep_min, args.sleep_max),
            retry_sleep_range=(args.retry_sleep_min, args.retry_sleep_max),
        )
        buffer.append(row)

        if i % args.save_every == 0:
            save_category_checkpoint(category_out, buffer)
            buffer = []
            print(f"Checkpoint saved after {i} packages in this session.")

    if buffer:
        save_category_checkpoint(category_out, buffer)

    print("Category scraping completed or final checkpoint saved.")


def save_category_checkpoint(category_out, buffer):
    category_out = Path(category_out)
    chunk_df = pd.DataFrame(buffer)

    if category_out.exists():
        old = pd.read_csv(category_out, dtype=str, low_memory=False)
        new = pd.concat([old, chunk_df], ignore_index=True)
    else:
        new = chunk_df.copy()

    new["pkg_name"] = new["pkg_name"].astype(str).str.strip()
    new = new.drop_duplicates(subset=["pkg_name"], keep="last")
    new.to_csv(category_out, index=False)


def build_population_benchmark():
    total_apps = 2301171

    pop_data = {
        "Education": 259422,
        "Business": 192405,
        "Tools": 166434,
        "Productivity": 126911,
        "Food & Drink": 120539,
        "Lifestyle": 119880,
        "Health & Fitness": 113252,
    }

    pop_df = pd.DataFrame(list(pop_data.items()), columns=["category", "population_count"])
    pop_df["population_percent"] = pop_df["population_count"] / total_apps * 100
    pop_df = pop_df.sort_values("population_percent", ascending=False)

    return pop_df


def compare_category_distribution(category_path, outdir):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    play_cat_df = pd.read_csv(category_path, dtype=str, low_memory=False)
    play_cat_df["pkg_name"] = play_cat_df["pkg_name"].astype(str).str.strip()
    play_cat_df = play_cat_df.drop_duplicates(subset=["pkg_name"], keep="last").copy()

    play_cat_df["gp_found_bool"] = (
        play_cat_df["gp_found"].astype(str).str.lower().isin(["true", "1", "yes"])
    )

    coverage_summary = pd.DataFrame([
        {
            "metric": "Packages processed for Google Play category scraping",
            "value": len(play_cat_df),
        },
        {
            "metric": "Packages found on Google Play",
            "value": int(play_cat_df["gp_found_bool"].sum()),
        },
        {
            "metric": "Packages with non-missing Google Play genre",
            "value": int(play_cat_df["gp_genre"].notna().sum()),
        },
        {
            "metric": "Google Play category coverage (%)",
            "value": round(100 * play_cat_df["gp_genre"].notna().mean(), 2),
        },
    ])
    coverage_summary.to_csv(outdir / "sample_category_scraping_coverage.csv", index=False)

    sample_cat = (
        play_cat_df.loc[play_cat_df["gp_genre"].notna()]
        .groupby("gp_genre")
        .agg(sample_app_count=("pkg_name", "nunique"))
        .reset_index()
        .rename(columns={"gp_genre": "category"})
    )

    total_with_category = sample_cat["sample_app_count"].sum()
    sample_cat["sample_percent"] = sample_cat["sample_app_count"] / total_with_category * 100

    pop_df = build_population_benchmark()

    comparison = pop_df.merge(sample_cat, on="category", how="left")
    comparison["sample_app_count"] = comparison["sample_app_count"].fillna(0).astype(int)
    comparison["sample_percent"] = comparison["sample_percent"].fillna(0)
    comparison["difference_percentage_points"] = (
        comparison["sample_percent"] - comparison["population_percent"]
    )
    comparison["absolute_difference_percentage_points"] = (
        comparison["difference_percentage_points"].abs()
    )

    comparison.to_csv(outdir / "sample_vs_google_play_categories.csv", index=False)

    corr = comparison[["sample_percent", "population_percent"]].corr().iloc[0, 1]
    summary = pd.DataFrame([
        {
            "metric": "Mean absolute difference across benchmark categories (percentage points)",
            "value": round(comparison["absolute_difference_percentage_points"].mean(), 3),
        },
        {
            "metric": "Correlation between sample and population percentages",
            "value": round(corr, 3),
        },
    ])
    summary.to_csv(outdir / "sample_representativeness_summary.csv", index=False)

    plot_df = comparison.sort_values("population_percent", ascending=True)

    fig, ax = plt.subplots(figsize=(8, 5))
    y = list(range(len(plot_df)))

    ax.barh(
        [i - 0.18 for i in y],
        plot_df["population_percent"],
        height=0.35,
        label="Google Play population",
        color="#D9D9D9",
    )

    ax.barh(
        [i + 0.18 for i in y],
        plot_df["sample_percent"],
        height=0.35,
        label="Sample",
        color="#9ACD9A",
    )

    ax.set_yticks(y)
    ax.set_yticklabels(plot_df["category"])
    ax.set_xlabel("Share of apps (%)")
    ax.set_title("Sample vs Google Play category distribution")
    ax.grid(axis="x", linestyle="--", alpha=0.35)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(frameon=False)

    plt.tight_layout()
    plt.savefig(outdir / "sample_vs_google_play_categories.png", dpi=300, bbox_inches="tight")
    plt.close()

    print("Sample representativeness outputs saved to:", outdir)
    print("\nCoverage:")
    print(coverage_summary.to_string(index=False))
    print("\nComparison:")
    print(comparison.to_string(index=False))


def merge_categories_to_dataset(input_dataset, category_path, merged_out):
    df = pd.read_csv(input_dataset, dtype=str, low_memory=False)
    df.columns = df.columns.str.strip()

    cat = pd.read_csv(category_path, dtype=str, low_memory=False)
    cat.columns = cat.columns.str.strip()

    df["pkg_name"] = df["pkg_name"].astype(str).str.strip()
    cat["pkg_name"] = cat["pkg_name"].astype(str).str.strip()

    cat = cat.drop_duplicates(subset=["pkg_name"], keep="last")

    merged = df.merge(cat, on="pkg_name", how="left")
    merged.to_csv(merged_out, index=False)

    print("Merged dataset saved to:", merged_out)
    print("Merged shape:", merged.shape)


def main():
    parser = argparse.ArgumentParser(
        description="Scrape Google Play categories and compare sample representativeness."
    )

    parser.add_argument("--input-dataset", required=True)
    parser.add_argument("--category-out", default="outputs/validation/play_categories_final.csv")
    parser.add_argument("--merged-out", default=None)
    parser.add_argument("--outdir", default="outputs/validation")
    parser.add_argument("--scrape", action="store_true")
    parser.add_argument("--max-apps", type=int, default=None)
    parser.add_argument("--save-every", type=int, default=1000)
    parser.add_argument("--lang", default="en")
    parser.add_argument("--countries", default="us,it")
    parser.add_argument("--max-retries-temp", type=int, default=2)
    parser.add_argument("--sleep-min", type=float, default=0.6)
    parser.add_argument("--sleep-max", type=float, default=1.4)
    parser.add_argument("--retry-sleep-min", type=float, default=2.0)
    parser.add_argument("--retry-sleep-max", type=float, default=5.0)
    parser.add_argument("--include-errors", action="store_true")
    parser.add_argument("--include-apps-without-sdk", action="store_true")

    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.input_dataset, dtype=str, low_memory=False)
    df.columns = df.columns.str.strip()

    latest_df = select_latest_package_records(
        df,
        require_ok=not args.include_errors,
        require_sdk=not args.include_apps_without_sdk,
    )

    pkg_names = (
        latest_df["pkg_name"]
        .dropna()
        .astype(str)
        .str.strip()
        .drop_duplicates()
        .tolist()
    )

    print("Unique package names selected for representativeness check:", len(pkg_names))

    if args.scrape:
        scrape_categories(pkg_names, args.category_out, args)

    if not Path(args.category_out).exists():
        raise FileNotFoundError(
            f"Category file not found: {args.category_out}. "
            "Run with --scrape or provide an existing category file."
        )

    compare_category_distribution(args.category_out, args.outdir)

    if args.merged_out:
        merge_categories_to_dataset(
            input_dataset=args.input_dataset,
            category_path=args.category_out,
            merged_out=args.merged_out,
        )


if __name__ == "__main__":
    main()
