#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt


def pct(x):
    return round(100 * x, 2)


def main():
    parser = argparse.ArgumentParser(
        description="Technical validation: SDK-provider mapping coverage."
    )

    parser.add_argument(
        "--provider-mapping",
        default="outputs/provider_mapping/sdk_company_lookup_FINAL_CANONICAL.csv",
        help="Path to sdk_company_lookup_FINAL_CANONICAL.csv",
    )

    parser.add_argument(
        "--app-sdk-provider-edges",
        default="outputs/final_datasets/final_app_sdk_provider_edges.csv",
        help="Path to final_app_sdk_provider_edges.csv",
    )

    parser.add_argument(
        "--outdir",
        default="outputs/validation",
        help="Output directory for validation tables and figures",
    )

    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    provider = pd.read_csv(args.provider_mapping, dtype=str, low_memory=False)
    provider.columns = provider.columns.str.strip()

    edges = pd.read_csv(args.app_sdk_provider_edges, dtype=str, low_memory=False)
    edges.columns = edges.columns.str.strip()

    provider["company_canonical_clean"] = (
        provider["company_canonical"].fillna("").astype(str).str.strip()
    )

    provider["is_mapped"] = (
        provider["company_canonical_clean"].ne("")
        & provider["company_canonical_clean"].str.lower().ne("nan")
    )

    total_sdks = provider["sdk_name"].nunique()
    mapped_sdks = provider.loc[provider["is_mapped"], "sdk_name"].nunique()
    unmapped_sdks = total_sdks - mapped_sdks
    mapping_coverage = pct(mapped_sdks / total_sdks)

    provider["provider_source_clean"] = (
        provider["provider_source"]
        .fillna("unknown")
        .astype(str)
        .str.strip()
        .replace("", "unknown")
    )

    source_dist = provider["provider_source_clean"].value_counts().reset_index()
    source_dist.columns = ["provider_source", "count"]
    source_dist["share_percent"] = round(
        100 * source_dist["count"] / source_dist["count"].sum(), 2
    )
    source_dist.to_csv(outdir / "provider_source_distribution.csv", index=False)

    cols_to_keep = [
        c for c in ["sdk_name", "sdk_type", "exodus_url", "tracker_website"]
        if c in provider.columns
    ]
    unmapped = provider.loc[~provider["is_mapped"], cols_to_keep]
    unmapped.to_csv(outdir / "unmapped_sdks.csv", index=False)

    edges["company_canonical_clean"] = (
        edges["company_canonical"].fillna("").astype(str).str.strip()
    )

    edges["has_provider"] = (
        edges["company_canonical_clean"].ne("")
        & edges["company_canonical_clean"].str.lower().ne("nan")
    )

    total_edges = len(edges)
    edges_with_provider = int(edges["has_provider"].sum())
    edge_provider_coverage = pct(edges_with_provider / total_edges)

    total_apps = edges["pkg_name"].nunique() if "pkg_name" in edges.columns else None
    apps_with_provider = (
        edges.loc[edges["has_provider"], "pkg_name"].nunique()
        if "pkg_name" in edges.columns else None
    )

    app_provider_coverage = (
        pct(apps_with_provider / total_apps)
        if total_apps and total_apps > 0 else None
    )

    unique_detected_sdks = edges["sdk_name"].nunique()
    detected_sdks_with_provider = edges.loc[
        edges["has_provider"], "sdk_name"
    ].nunique()

    detected_sdk_provider_coverage = pct(
        detected_sdks_with_provider / unique_detected_sdks
    )

    summary = pd.DataFrame([
        {"metric": "Unique SDKs in provider mapping", "value": total_sdks},
        {"metric": "Mapped SDKs", "value": mapped_sdks},
        {"metric": "Unmapped SDKs", "value": unmapped_sdks},
        {"metric": "SDK mapping coverage (%)", "value": mapping_coverage},
        {"metric": "App-SDK-provider edge rows", "value": total_edges},
        {"metric": "Edges with assigned provider", "value": edges_with_provider},
        {"metric": "Edge-level provider coverage (%)", "value": edge_provider_coverage},
        {"metric": "Unique detected SDKs in final edge list", "value": unique_detected_sdks},
        {"metric": "Detected SDKs with assigned provider", "value": detected_sdks_with_provider},
        {"metric": "Detected SDK provider coverage (%)", "value": detected_sdk_provider_coverage},
        {"metric": "Unique apps in edge list", "value": total_apps},
        {"metric": "Apps with at least one assigned provider", "value": apps_with_provider},
        {"metric": "App-level provider coverage (%)", "value": app_provider_coverage},
    ])

    summary.to_csv(outdir / "provider_mapping_validation_summary.csv", index=False)

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(
        source_dist["provider_source"],
        source_dist["count"],
        color="#9ACD9A",
        edgecolor="white",
        linewidth=0.8,
    )
    ax.set_title("SDK-Provider Mapping Sources", fontsize=15, fontweight="bold", pad=12)
    ax.set_xlabel("Mapping source")
    ax.set_ylabel("Number of SDKs")
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    plt.xticks(rotation=30, ha="right")
    plt.tight_layout()
    plt.savefig(
        outdir / "provider_source_distribution.png",
        dpi=300,
        bbox_inches="tight",
    )
    plt.close()

    print("\nProvider mapping validation completed.")
    print(f"Outputs saved to: {outdir}")
    print("\nSummary:")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
