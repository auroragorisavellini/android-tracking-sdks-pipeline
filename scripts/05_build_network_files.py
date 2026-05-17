#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
05_build_network_files.py

Build network files from the final merged app-version dataset.

This version is aligned with the paper analysis:
1. keep only APKs successfully processed;
2. keep only observations with at least one detected SDK;
3. select the most recent version for each package name before building networks.

The most recent version is selected by sorting:
    pkg_name ascending,
    vercode descending,
    dex_date descending,
    extraction_ts_utc descending

Inputs:
    final_app_version_dataset.csv
    sdk_company_lookup_FINAL_CANONICAL.csv

Outputs:
    final_app_sdk_edges.csv
    final_app_sdk_provider_edges.csv
    app-SDK bipartite network
    app-provider bipartite network
    SDK-SDK projection
    provider-provider projection
    optional app-app projection
    network_file_summary.csv
"""

import argparse
import json
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
import networkx as nx


def clean_string_column(series):
    return series.astype(str).str.strip()


def make_app_node(row):
    """
    App node identifier.

    Since this script selects one latest version per package by default,
    pkg_name uniquely identifies apps in the final network.
    """
    return str(row["pkg_name"]).strip()


def parse_json_list(value):
    if pd.isna(value):
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
    except Exception:
        pass
    return []


def normalize_sdk_for_merge(value):
    value = str(value).lower()
    value = value.replace("(", " ").replace(")", " ")
    value = " ".join(value.split())
    return value.strip()


def select_latest_app_versions(df):
    """
    Replicate the filtering used in the paper.

    Steps:
    1. keep only APKs with download_status == OK;
    2. keep only rows with sdk_count > 0;
    3. sort by pkg_name, vercode, dex_date, and extraction timestamp;
    4. keep the first observation for each pkg_name;
    5. keep only rows with a non-empty parsed SDK list.
    """
    df = df.copy()
    df.columns = df.columns.str.strip()

    required = [
        "sha256",
        "pkg_name",
        "vercode",
        "download_status",
        "sdk_count",
        "sdk_names_json",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in app-version dataset: {missing}")

    print("Initial app-version rows:", len(df))
    print("Initial unique packages:", df["pkg_name"].nunique())

    df = df[df["download_status"].astype(str).str.upper().str.strip() == "OK"].copy()
    df["sdk_count_num"] = pd.to_numeric(df["sdk_count"], errors="coerce").fillna(0)
    df = df[df["sdk_count_num"] > 0].copy()

    df["vercode_num"] = pd.to_numeric(df["vercode"], errors="coerce")
    df["dex_date_dt"] = pd.to_datetime(df.get("dex_date"), errors="coerce")
    df["extraction_ts_dt"] = pd.to_datetime(df.get("extraction_ts_utc"), errors="coerce")

    df = df.sort_values(
        by=["pkg_name", "vercode_num", "dex_date_dt", "extraction_ts_dt"],
        ascending=[True, False, False, False],
    )

    latest_df = df.drop_duplicates(subset=["pkg_name"], keep="first").copy()
    latest_df["sdk_list_check"] = latest_df["sdk_names_json"].apply(parse_json_list)
    latest_df = latest_df[latest_df["sdk_list_check"].map(len) > 0].copy()
    latest_df = latest_df.drop(columns=["sdk_list_check"])

    print("Rows after successful-processing and SDK filters:", len(df))
    print("Unique apps retained, latest version only:", latest_df["pkg_name"].nunique())
    print("Rows in latest-version dataset:", len(latest_df))

    return latest_df


def export_graph(G, graphml_path, edges_path, nodes_path):
    graphml_path = Path(graphml_path)
    edges_path = Path(edges_path)
    nodes_path = Path(nodes_path)
    graphml_path.parent.mkdir(parents=True, exist_ok=True)

    G_export = G.copy()

    for _, attrs in G_export.nodes(data=True):
        for key, value in list(attrs.items()):
            if value is None:
                attrs[key] = ""
            elif isinstance(value, (list, tuple, set, dict)):
                attrs[key] = json.dumps(value, ensure_ascii=False)
            else:
                attrs[key] = str(value)

    for _, _, attrs in G_export.edges(data=True):
        for key, value in list(attrs.items()):
            if value is None:
                attrs[key] = ""
            elif isinstance(value, (list, tuple, set, dict)):
                attrs[key] = json.dumps(value, ensure_ascii=False)
            else:
                attrs[key] = str(value)

    nx.write_graphml(G_export, graphml_path)
    nx.to_pandas_edgelist(G).to_csv(edges_path, index=False)
    pd.DataFrame(
        [{"node": node, **attrs} for node, attrs in G.nodes(data=True)]
    ).to_csv(nodes_path, index=False)


def graph_summary(G, network_name):
    if G.number_of_nodes() == 0:
        return {
            "network": network_name,
            "nodes": 0,
            "edges": 0,
            "density": np.nan,
            "connected_components": 0,
            "giant_component_nodes": 0,
            "giant_component_edges": 0,
        }

    components = sorted(nx.connected_components(G), key=len, reverse=True)
    giant = G.subgraph(components[0]).copy()

    return {
        "network": network_name,
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "density": nx.density(G),
        "connected_components": len(components),
        "giant_component_nodes": giant.number_of_nodes(),
        "giant_component_edges": giant.number_of_edges(),
    }


def build_app_sdk_edges_from_app_version(app_version_path, latest_only=True):
    df = pd.read_csv(app_version_path, dtype=str, low_memory=False)
    df.columns = df.columns.str.strip()

    if latest_only:
        df = select_latest_app_versions(df)

    required = ["sha256", "pkg_name", "vercode", "sdk_names_json"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in app-version dataset: {missing}")

    rows = []
    for _, row in df.iterrows():
        sdk_names = parse_json_list(row.get("sdk_names_json"))
        sdk_roots = parse_json_list(row.get("sdk_roots_json"))
        sdk_types = parse_json_list(row.get("sdk_types_json"))

        for i, sdk_name in enumerate(sdk_names):
            rows.append({
                "app_node": make_app_node(row),
                "sha256": str(row["sha256"]).upper().strip(),
                "pkg_name": str(row["pkg_name"]).strip(),
                "vercode": str(row["vercode"]).strip(),
                "dex_date": row.get("dex_date", ""),
                "extraction_ts_utc": row.get("extraction_ts_utc", ""),
                "sdk_name": sdk_name,
                "sdk_root": sdk_roots[i] if i < len(sdk_roots) else "",
                "sdk_types_json": json.dumps(sdk_types, ensure_ascii=False),
            })

    app_sdk_edges = pd.DataFrame(rows)

    if not app_sdk_edges.empty:
        app_sdk_edges = app_sdk_edges.drop_duplicates(subset=["app_node", "sdk_name"])
        app_sdk_edges["sdk_node"] = "sdk::" + app_sdk_edges["sdk_name"]

    return app_sdk_edges


def build_app_sdk_provider_edges(app_sdk_edges, provider_path):
    provider = pd.read_csv(provider_path, dtype=str, low_memory=False)
    provider.columns = provider.columns.str.strip()

    required = ["sdk_name", "company_canonical"]
    missing = [c for c in required if c not in provider.columns]
    if missing:
        raise ValueError(f"Missing required columns in provider mapping: {missing}")

    provider["sdk_name_norm"] = provider["sdk_name"].apply(normalize_sdk_for_merge)

    app_sdk_edges = app_sdk_edges.copy()
    app_sdk_edges["sdk_name_norm"] = app_sdk_edges["sdk_name"].apply(normalize_sdk_for_merge)

    cols = ["sdk_name_norm", "company_canonical"]
    if "provider_source" in provider.columns:
        cols.append("provider_source")

    provider_small = provider[cols].drop_duplicates(subset=["sdk_name_norm"])

    app_sdk_provider_edges = app_sdk_edges.merge(
        provider_small,
        on="sdk_name_norm",
        how="left",
    )

    app_sdk_provider_edges = app_sdk_provider_edges.drop(columns=["sdk_name_norm"])
    return app_sdk_provider_edges


def load_app_sdk_edges(path):
    df = pd.read_csv(path, dtype=str, low_memory=False)
    df.columns = df.columns.str.strip()

    required = ["sha256", "pkg_name", "vercode", "sdk_name"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in app-SDK edge list: {missing}")

    df["sha256"] = df["sha256"].astype(str).str.upper().str.strip()
    df["pkg_name"] = clean_string_column(df["pkg_name"])
    df["vercode"] = clean_string_column(df["vercode"])
    df["sdk_name"] = clean_string_column(df["sdk_name"])

    df = df[df["sdk_name"].notna()]
    df = df[df["sdk_name"].str.len() > 0]
    df = df[df["sdk_name"].str.lower() != "nan"]

    if "app_node" not in df.columns:
        df["app_node"] = df.apply(make_app_node, axis=1)

    if "sdk_node" not in df.columns:
        df["sdk_node"] = "sdk::" + df["sdk_name"]

    df = df.drop_duplicates(subset=["app_node", "sdk_name"])
    return df


def load_app_sdk_provider_edges(path):
    df = pd.read_csv(path, dtype=str, low_memory=False)
    df.columns = df.columns.str.strip()

    required = ["sha256", "pkg_name", "vercode", "sdk_name", "company_canonical"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in app-SDK-provider edge list: {missing}")

    df["sha256"] = df["sha256"].astype(str).str.upper().str.strip()
    df["pkg_name"] = clean_string_column(df["pkg_name"])
    df["vercode"] = clean_string_column(df["vercode"])
    df["sdk_name"] = clean_string_column(df["sdk_name"])
    df["company_canonical"] = clean_string_column(df["company_canonical"])

    df = df[df["company_canonical"].notna()]
    df = df[df["company_canonical"].str.len() > 0]
    df = df[df["company_canonical"].str.lower() != "nan"]

    if "app_node" not in df.columns:
        df["app_node"] = df.apply(make_app_node, axis=1)

    df["provider_node"] = "provider::" + df["company_canonical"]
    df = df.drop_duplicates(subset=["app_node", "sdk_name", "company_canonical"])
    return df


def prepare_edge_lists(
    final_dir,
    app_version_path,
    provider_path,
    app_sdk_path,
    app_sdk_provider_path,
    latest_only=True,
    overwrite_edges=False,
):
    final_dir = Path(final_dir)
    final_dir.mkdir(parents=True, exist_ok=True)

    app_version_path = Path(app_version_path) if app_version_path else final_dir / "final_app_version_dataset.csv"
    app_sdk_path = Path(app_sdk_path) if app_sdk_path else final_dir / "final_app_sdk_edges.csv"
    app_sdk_provider_path = Path(app_sdk_provider_path) if app_sdk_provider_path else final_dir / "final_app_sdk_provider_edges.csv"

    if app_sdk_path.exists() and not overwrite_edges:
        print(f"Loading existing app-SDK edges from: {app_sdk_path}")
        app_sdk_edges = load_app_sdk_edges(app_sdk_path)
    else:
        print("Building app-SDK edges from app-version dataset.")
        app_sdk_edges = build_app_sdk_edges_from_app_version(
            app_version_path=app_version_path,
            latest_only=latest_only,
        )
        app_sdk_edges.to_csv(app_sdk_path, index=False)
        print(f"Saved generated app-SDK edges to: {app_sdk_path}")

    if app_sdk_provider_path.exists() and not overwrite_edges:
        print(f"Loading existing app-SDK-provider edges from: {app_sdk_provider_path}")
        app_sdk_provider_edges = load_app_sdk_provider_edges(app_sdk_provider_path)
    else:
        if provider_path is None:
            raise ValueError("Provider mapping is required to build app-SDK-provider edges. Pass --provider-path.")

        print("Building app-SDK-provider edges.")
        app_sdk_provider_edges = build_app_sdk_provider_edges(
            app_sdk_edges=app_sdk_edges,
            provider_path=provider_path,
        )
        app_sdk_provider_edges.to_csv(app_sdk_provider_path, index=False)
        print(f"Saved generated app-SDK-provider edges to: {app_sdk_provider_path}")

    app_sdk_edges = load_app_sdk_edges(app_sdk_path)
    app_sdk_provider_edges = load_app_sdk_provider_edges(app_sdk_provider_path)
    return app_sdk_edges, app_sdk_provider_edges


def build_app_sdk_bipartite(edges):
    G = nx.Graph()

    apps = edges[["app_node", "sha256", "pkg_name", "vercode"]].drop_duplicates("app_node")
    for _, row in apps.iterrows():
        G.add_node(
            row["app_node"],
            node_type="app",
            sha256=row["sha256"],
            pkg_name=row["pkg_name"],
            vercode=row["vercode"],
        )

    sdks = edges[["sdk_node", "sdk_name"]].drop_duplicates("sdk_node")
    for _, row in sdks.iterrows():
        G.add_node(row["sdk_node"], node_type="sdk", sdk_name=row["sdk_name"])

    for _, row in edges.iterrows():
        attrs = {}
        if "sdk_root" in edges.columns:
            attrs["sdk_root"] = row.get("sdk_root", "")
        if "sdk_types_json" in edges.columns:
            attrs["sdk_types_json"] = row.get("sdk_types_json", "")
        G.add_edge(row["app_node"], row["sdk_node"], **attrs)

    return G


def build_app_provider_bipartite(edges):
    collapsed = (
        edges.groupby(["app_node", "provider_node", "company_canonical"], as_index=False)
        .agg(
            weight=("sdk_name", "nunique"),
            sdk_names=("sdk_name", lambda x: "; ".join(sorted(set(map(str, x)))))
        )
    )

    G = nx.Graph()

    apps = edges[["app_node", "sha256", "pkg_name", "vercode"]].drop_duplicates("app_node")
    for _, row in apps.iterrows():
        G.add_node(
            row["app_node"],
            node_type="app",
            sha256=row["sha256"],
            pkg_name=row["pkg_name"],
            vercode=row["vercode"],
        )

    providers = collapsed[["provider_node", "company_canonical"]].drop_duplicates("provider_node")
    for _, row in providers.iterrows():
        G.add_node(
            row["provider_node"],
            node_type="provider",
            company_canonical=row["company_canonical"],
        )

    for _, row in collapsed.iterrows():
        G.add_edge(
            row["app_node"],
            row["provider_node"],
            weight=int(row["weight"]),
            sdk_names=row["sdk_names"],
        )

    return G, collapsed


def projection_from_groups(grouped_entities, node_type, min_shared=1):
    pair_weights = {}

    for entities in grouped_entities:
        entities = sorted(set(map(str, entities)))
        if len(entities) < 2:
            continue

        for u, v in combinations(entities, 2):
            pair_weights[(u, v)] = pair_weights.get((u, v), 0) + 1

    rows = [
        {"source": u, "target": v, "weight": w}
        for (u, v), w in pair_weights.items()
        if w >= min_shared
    ]

    edge_df = pd.DataFrame(rows)

    G = nx.Graph()
    for entities in grouped_entities:
        for node in set(map(str, entities)):
            G.add_node(node, node_type=node_type)

    for _, row in edge_df.iterrows():
        G.add_edge(row["source"], row["target"], weight=int(row["weight"]))

    return G, edge_df


def build_sdk_sdk_projection(app_sdk_edges, min_shared_apps=1):
    grouped = (
        app_sdk_edges.groupby("app_node")["sdk_name"]
        .apply(lambda x: sorted(set(map(str, x))))
        .tolist()
    )

    G, edges = projection_from_groups(grouped, node_type="sdk", min_shared=min_shared_apps)

    for node in G.nodes():
        G.nodes[node]["sdk_name"] = node

    return G, edges


def build_provider_provider_projection(app_sdk_provider_edges, min_shared_apps=1):
    grouped = (
        app_sdk_provider_edges.groupby("app_node")["company_canonical"]
        .apply(lambda x: sorted(set(map(str, x))))
        .tolist()
    )

    G, edges = projection_from_groups(grouped, node_type="provider", min_shared=min_shared_apps)

    for node in G.nodes():
        G.nodes[node]["company_canonical"] = node

    return G, edges


def build_app_app_projection(app_sdk_edges, min_shared_sdks=1, sample_size=None, seed=42):
    app_sdk = (
        app_sdk_edges.groupby("app_node")["sdk_name"]
        .apply(lambda x: sorted(set(map(str, x))))
        .reset_index()
    )

    if sample_size is not None and sample_size < len(app_sdk):
        app_sdk = app_sdk.sample(n=sample_size, random_state=seed)

    sdk_to_apps = (
        app_sdk.explode("sdk_name")
        .groupby("sdk_name")["app_node"]
        .apply(lambda x: sorted(set(map(str, x))))
        .tolist()
    )

    G, edges = projection_from_groups(
        sdk_to_apps,
        node_type="app",
        min_shared=min_shared_sdks
    )

    for node in G.nodes():
        G.nodes[node]["app_node"] = node

    return G, edges


def main():
    parser = argparse.ArgumentParser(
        description="Build bipartite and projected network files from final app-version datasets."
    )

    parser.add_argument("--final-dir", default="outputs/final_datasets")
    parser.add_argument("--app-version-path", default=None)
    parser.add_argument("--provider-path", default=None)
    parser.add_argument("--app-sdk-path", default=None)
    parser.add_argument("--app-sdk-provider-path", default=None)
    parser.add_argument("--outdir", default="outputs/networks")

    parser.add_argument(
        "--use-all-versions",
        action="store_true",
        help="Use all app-version observations instead of selecting the latest version per package."
    )

    parser.add_argument(
        "--overwrite-edges",
        action="store_true",
        help="Overwrite existing final_app_sdk_edges.csv and final_app_sdk_provider_edges.csv."
    )

    parser.add_argument("--sdk-sdk-min-shared-apps", type=int, default=1)
    parser.add_argument("--provider-provider-min-shared-apps", type=int, default=1)

    parser.add_argument("--build-app-app", action="store_true")
    parser.add_argument("--app-app-min-shared", type=int, default=1)
    parser.add_argument("--app-app-sample-size", type=int, default=None)
    parser.add_argument("--seed", type=int, default=42)

    args = parser.parse_args()

    final_dir = Path(args.final_dir)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    app_sdk_edges, app_sdk_provider_edges = prepare_edge_lists(
        final_dir=final_dir,
        app_version_path=args.app_version_path,
        provider_path=args.provider_path,
        app_sdk_path=args.app_sdk_path,
        app_sdk_provider_path=args.app_sdk_provider_path,
        latest_only=not args.use_all_versions,
        overwrite_edges=args.overwrite_edges,
    )

    summaries = []

    print("\\nApp-SDK edges:", app_sdk_edges.shape)
    print("App-SDK-provider edges:", app_sdk_provider_edges.shape)

    G_app_sdk = build_app_sdk_bipartite(app_sdk_edges)
    export_graph(
        G_app_sdk,
        outdir / "app_sdk_bipartite.graphml",
        outdir / "app_sdk_bipartite_edges.csv",
        outdir / "app_sdk_bipartite_nodes.csv",
    )
    summaries.append(graph_summary(G_app_sdk, "app_sdk_bipartite"))

    G_app_provider, app_provider_edges = build_app_provider_bipartite(app_sdk_provider_edges)
    app_provider_edges.to_csv(outdir / "app_provider_bipartite_edges_collapsed.csv", index=False)
    export_graph(
        G_app_provider,
        outdir / "app_provider_bipartite.graphml",
        outdir / "app_provider_bipartite_edges.csv",
        outdir / "app_provider_bipartite_nodes.csv",
    )
    summaries.append(graph_summary(G_app_provider, "app_provider_bipartite"))

    G_sdk_sdk, sdk_sdk_edges = build_sdk_sdk_projection(
        app_sdk_edges,
        min_shared_apps=args.sdk_sdk_min_shared_apps,
    )
    sdk_sdk_edges.to_csv(outdir / "sdk_sdk_projection_edges_weighted.csv", index=False)
    export_graph(
        G_sdk_sdk,
        outdir / "sdk_sdk_projection.graphml",
        outdir / "sdk_sdk_projection_edges.csv",
        outdir / "sdk_sdk_projection_nodes.csv",
    )
    summaries.append(graph_summary(G_sdk_sdk, "sdk_sdk_projection"))

    G_provider_provider, provider_provider_edges = build_provider_provider_projection(
        app_sdk_provider_edges,
        min_shared_apps=args.provider_provider_min_shared_apps,
    )
    provider_provider_edges.to_csv(outdir / "provider_provider_projection_edges_weighted.csv", index=False)
    export_graph(
        G_provider_provider,
        outdir / "provider_provider_projection.graphml",
        outdir / "provider_provider_projection_edges.csv",
        outdir / "provider_provider_projection_nodes.csv",
    )
    summaries.append(graph_summary(G_provider_provider, "provider_provider_projection"))

    if args.build_app_app:
        G_app_app, app_app_edges = build_app_app_projection(
            app_sdk_edges,
            min_shared_sdks=args.app_app_min_shared,
            sample_size=args.app_app_sample_size,
            seed=args.seed,
        )

        suffix = "full" if args.app_app_sample_size is None else f"sample_{args.app_app_sample_size}"

        app_app_edges.to_csv(outdir / f"app_app_projection_edges_weighted_{suffix}.csv", index=False)
        export_graph(
            G_app_app,
            outdir / f"app_app_projection_{suffix}.graphml",
            outdir / f"app_app_projection_edges_{suffix}.csv",
            outdir / f"app_app_projection_nodes_{suffix}.csv",
        )
        summaries.append(graph_summary(G_app_app, f"app_app_projection_{suffix}"))

    summary_df = pd.DataFrame(summaries)
    summary_df.to_csv(outdir / "network_file_summary.csv", index=False)

    print("\\nNetwork files saved to:", outdir)
    print(summary_df)


if __name__ == "__main__":
    main()
