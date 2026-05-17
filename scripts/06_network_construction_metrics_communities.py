#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
06_network_construction_and_analysis.py

Build app-SDK and app-provider networks, construct one-mode projections,
compute network summary statistics, and optionally run community-detection
algorithms.

The script can be run on the full dataset or on a random sample of applications
for faster testing.

Main outputs:
    latest_apps_used.csv
    app_sdk_edges.csv
    app_provider_edges.csv
    sdk_sdk_projection_edges.csv
    provider_provider_projection_edges.csv
    app_app_projection_edges.csv                    optional
    network_summary_metrics.csv
    community_detection_summary.csv                 optional
    partitions_<network>.csv                        optional
    figures/*.png                                   optional

Example:
    python scripts/06_network_construction_and_analysis.py \
        --input-file outputs/final_datasets/final_app_version_dataset.csv \
        --provider-mapping outputs/provider_mapping/sdk_provider_mapping_final.csv \
        --outdir outputs/network_analysis \
        --build-networks \
        --compute-metrics \
        --run-louvain \
        --run-leiden \
        --make-figures
"""

import argparse
import json
import logging
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp
import networkx as nx

from networkx.algorithms.community import louvain_communities
from networkx.algorithms.community.quality import modularity


logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def parse_json_list(value):
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


def save_pickle(obj, path):
    with open(path, "wb") as f:
        pickle.dump(obj, f)


def make_output_dirs(outdir):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "figures").mkdir(parents=True, exist_ok=True)
    (outdir / "pickles").mkdir(parents=True, exist_ok=True)
    return outdir


def load_latest_app_versions(input_file, sample_mode="all", sample_size=None, random_state=42):
    required_cols = [
        "pkg_name",
        "vercode",
        "dex_date",
        "extraction_ts_utc",
        "download_status",
        "sdk_count",
        "sdk_names_json",
    ]

    logging.info("Loading dataset: %s", input_file)
    df = pd.read_csv(input_file, usecols=lambda c: c in required_cols, dtype=str, low_memory=False)

    missing = sorted(set(required_cols) - set(df.columns))
    if missing:
        raise ValueError(f"Missing required columns in input file: {missing}")

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

    latest = df.drop_duplicates(subset=["pkg_name"], keep="first").copy()
    latest["sdk_list"] = latest["sdk_names_json"].apply(parse_json_list)
    latest = latest[latest["sdk_list"].map(len) > 0].copy()

    if sample_mode == "random":
        if sample_size is None:
            raise ValueError("--sample-size must be provided when --sample-mode random is used.")
        n = min(int(sample_size), len(latest))
        latest = latest.sample(n=n, random_state=random_state).copy()
        logging.info("Random sample selected: %s apps", n)

    logging.info("Unique apps retained: %s", latest["pkg_name"].nunique())
    return latest


def build_app_sdk_edges(latest_df):
    edges = latest_df[["pkg_name", "sdk_list"]].explode("sdk_list")
    edges = edges.rename(columns={"sdk_list": "sdk_name"})
    edges["pkg_name"] = edges["pkg_name"].astype(str).str.strip()
    edges["sdk_name"] = edges["sdk_name"].astype(str).str.strip()
    edges = edges[(edges["pkg_name"] != "") & (edges["sdk_name"] != "")]
    edges = edges.drop_duplicates(subset=["pkg_name", "sdk_name"]).reset_index(drop=True)
    return edges


def build_app_provider_edges(app_sdk_edges, provider_mapping_file):
    mapping = pd.read_csv(provider_mapping_file, dtype=str, low_memory=False)
    mapping.columns = mapping.columns.str.strip()
    if "sdk_name" not in mapping.columns or "company_canonical" not in mapping.columns:
        raise ValueError("Provider mapping must contain 'sdk_name' and 'company_canonical'.")

    mapping = mapping[["sdk_name", "company_canonical"]].drop_duplicates()
    mapping["sdk_name"] = mapping["sdk_name"].astype(str).str.strip()
    mapping["company_canonical"] = mapping["company_canonical"].astype(str).str.strip()
    mapping = mapping[mapping["company_canonical"] != ""]

    merged = app_sdk_edges.merge(mapping, on="sdk_name", how="left")
    merged = merged[merged["company_canonical"].notna()].copy()
    merged = merged[merged["company_canonical"].astype(str).str.strip() != ""]

    return (
        merged[["pkg_name", "company_canonical"]]
        .drop_duplicates()
        .rename(columns={"company_canonical": "provider"})
        .reset_index(drop=True)
    )


def edge_list_to_sparse_bipartite(edges, row_col, col_col):
    row_labels = pd.Index(sorted(edges[row_col].astype(str).unique()))
    col_labels = pd.Index(sorted(edges[col_col].astype(str).unique()))

    row_codes = pd.Categorical(edges[row_col].astype(str), categories=row_labels).codes
    col_codes = pd.Categorical(edges[col_col].astype(str), categories=col_labels).codes

    mat = sp.coo_matrix(
        (np.ones(len(edges), dtype=np.int8), (row_codes, col_codes)),
        shape=(len(row_labels), len(col_labels)),
    ).tocsr()
    mat.data = np.ones_like(mat.data)
    mat.eliminate_zeros()
    return mat, list(row_labels), list(col_labels)


def projection_from_bipartite(B, side="columns", binary=True):
    if side == "columns":
        W = (B.T @ B).tocsr()
    elif side == "rows":
        W = (B @ B.T).tocsr()
    else:
        raise ValueError("side must be 'columns' or 'rows'.")

    W.setdiag(0)
    W.eliminate_zeros()

    if binary:
        A = W.copy()
        A.data = np.ones_like(A.data)
        A.eliminate_zeros()
        return A, W
    return W, W


def sparse_projection_to_edges(A, labels, source_name, target_name, weight_matrix=None):
    upper = sp.triu(A, k=1).tocoo()
    if weight_matrix is not None:
        W_upper = sp.triu(weight_matrix, k=1).tocoo()
        weights = W_upper.data
    else:
        weights = upper.data
    return pd.DataFrame({
        source_name: [labels[i] for i in upper.row],
        target_name: [labels[j] for j in upper.col],
        "weight": weights,
    })


def graph_from_projection(A, labels=None):
    G = nx.from_scipy_sparse_array(A)
    if labels is not None:
        G = nx.relabel_nodes(G, {i: labels[i] for i in range(len(labels))})
    G.remove_edges_from(nx.selfloop_edges(G))
    return G


def bipartite_graph_from_edges(edges, left_col, right_col, left_prefix="app", right_prefix="sdk"):
    G = nx.Graph()
    left_nodes = sorted(edges[left_col].astype(str).unique())
    right_nodes = sorted(edges[right_col].astype(str).unique())
    G.add_nodes_from([f"{left_prefix}:{x}" for x in left_nodes], bipartite=0)
    G.add_nodes_from([f"{right_prefix}:{x}" for x in right_nodes], bipartite=1)
    G.add_edges_from(
        (f"{left_prefix}:{row[left_col]}", f"{right_prefix}:{row[right_col]}")
        for _, row in edges.iterrows()
    )
    return G


def largest_connected_component(G):
    if G.number_of_nodes() == 0:
        return G.copy()
    largest = max(nx.connected_components(G), key=len)
    return G.subgraph(largest).copy()


def graph_summary(G, network_name, graph_type="monopartite"):
    n_nodes = G.number_of_nodes()
    n_edges = G.number_of_edges()
    if n_nodes == 0:
        return {
            "network": network_name, "graph_type": graph_type, "nodes": 0, "edges": 0,
            "density": np.nan, "mean_degree": np.nan, "median_degree": np.nan,
            "max_degree": np.nan, "degree_assortativity": np.nan,
            "average_clustering": np.nan, "connected_components": 0,
            "giant_component_nodes": 0, "giant_component_edges": 0,
            "giant_component_share_nodes": np.nan,
        }

    degrees = np.array([d for _, d in G.degree()], dtype=float)
    density = nx.density(G)

    try:
        assort = nx.degree_assortativity_coefficient(G) if n_edges > 0 else np.nan
    except Exception:
        assort = np.nan

    try:
        avg_clust = nx.average_clustering(G) if graph_type == "monopartite" and n_edges > 0 else np.nan
    except Exception:
        avg_clust = np.nan

    if n_edges > 0:
        components = sorted(nx.connected_components(G), key=len, reverse=True)
        G_giant = G.subgraph(components[0])
        n_components = len(components)
        giant_n = G_giant.number_of_nodes()
        giant_e = G_giant.number_of_edges()
    else:
        n_components = n_nodes
        giant_n = 1 if n_nodes > 0 else 0
        giant_e = 0

    return {
        "network": network_name,
        "graph_type": graph_type,
        "nodes": n_nodes,
        "edges": n_edges,
        "density": density,
        "mean_degree": degrees.mean() if len(degrees) else np.nan,
        "median_degree": np.median(degrees) if len(degrees) else np.nan,
        "max_degree": degrees.max() if len(degrees) else np.nan,
        "degree_assortativity": assort,
        "average_clustering": avg_clust,
        "connected_components": n_components,
        "giant_component_nodes": giant_n,
        "giant_component_edges": giant_e,
        "giant_component_share_nodes": giant_n / n_nodes if n_nodes else np.nan,
    }


def bipartite_matrix_summary(B, network_name):
    n_left, n_right = B.shape
    edges = B.nnz
    left_degrees = np.asarray(B.sum(axis=1)).ravel()
    right_degrees = np.asarray(B.sum(axis=0)).ravel()
    return {
        "network": network_name,
        "graph_type": "bipartite_matrix",
        "left_nodes": n_left,
        "right_nodes": n_right,
        "nodes": n_left + n_right,
        "edges": edges,
        "density": edges / (n_left * n_right) if n_left * n_right > 0 else np.nan,
        "mean_degree_left": left_degrees.mean() if len(left_degrees) else np.nan,
        "median_degree_left": np.median(left_degrees) if len(left_degrees) else np.nan,
        "max_degree_left": left_degrees.max() if len(left_degrees) else np.nan,
        "mean_degree_right": right_degrees.mean() if len(right_degrees) else np.nan,
        "median_degree_right": np.median(right_degrees) if len(right_degrees) else np.nan,
        "max_degree_right": right_degrees.max() if len(right_degrees) else np.nan,
    }


def run_louvain(G, seed=42):
    partition = louvain_communities(G, seed=seed)
    part_map = {node: cid for cid, comm in enumerate(partition) for node in comm}
    Q = modularity(G, partition) if G.number_of_edges() > 0 else np.nan
    return partition, part_map, Q


def run_leiden(G, seed=42):
    try:
        import igraph as ig
        import leidenalg as la
    except ImportError as exc:
        raise ImportError("Leiden requires: pip install python-igraph leidenalg") from exc

    nodes = list(G.nodes())
    node_to_idx = {node: i for i, node in enumerate(nodes)}
    edges = [(node_to_idx[u], node_to_idx[v]) for u, v in G.edges()]
    g_ig = ig.Graph(n=len(nodes), edges=edges, directed=False)
    result = la.find_partition(g_ig, la.ModularityVertexPartition, seed=seed)

    partition = [{nodes[i] for i in comm} for comm in result]
    part_map = {node: cid for cid, comm in enumerate(partition) for node in comm}
    Q = modularity(G, partition) if G.number_of_edges() > 0 else np.nan
    return partition, part_map, Q


def run_domino(G, degree_corrected=False, max_outer=3):
    try:
        from domino import detect
    except ImportError as exc:
        raise ImportError("Domino requires: pip install domino-mesoscale") from exc

    nodes = list(G.nodes())
    node_to_idx = {node: i for i, node in enumerate(nodes)}
    idx_to_node = {i: node for node, i in node_to_idx.items()}
    G_int = nx.relabel_nodes(G, node_to_idx)

    A = nx.to_numpy_array(G_int, nodelist=sorted(G_int.nodes()), dtype=np.int8)
    A = (A > 0).astype(np.int8)
    A = np.triu(A, 1)
    A = A + A.T

    res = detect(
        G_int,
        A,
        mode="binary",
        degree_corrected=degree_corrected,
        max_outer=max_outer,
        viz=False,
        report=False,
    )

    partition_idx = res.get("partition", [])
    bic = res.get("bic", np.nan)
    partition = []
    part_map = {}
    for cid, comm in enumerate(partition_idx):
        original_comm = {idx_to_node[int(i)] for i in comm}
        partition.append(original_comm)
        for node in original_comm:
            part_map[node] = cid
    return partition, part_map, bic, res


def save_partitions(network_name, G, partition_maps, outdir):
    degrees = dict(G.degree())
    rows = []
    for node in sorted(G.nodes(), key=str):
        row = {"network": network_name, "node": node, "degree": degrees.get(node, 0)}
        for algo, part_map in partition_maps.items():
            row[f"{algo}_community"] = part_map.get(node, np.nan)
        rows.append(row)
    pd.DataFrame(rows).to_csv(outdir / f"partitions_{network_name}.csv", index=False)


def draw_partition_figure(G, part_map, title, outpath, seed=42):
    import matplotlib.pyplot as plt
    if G.number_of_nodes() == 0:
        return
    pos = nx.spring_layout(G, seed=seed)
    nodes = list(G.nodes())
    degrees = dict(G.degree())
    degree_values = np.array([degrees[n] for n in nodes], dtype=float)
    node_sizes = 20 + (degree_values / degree_values.max()) * 250 if degree_values.max() > 0 else np.repeat(30, len(nodes))
    node_colors = [part_map.get(n, -1) for n in nodes]

    plt.figure(figsize=(10, 10), facecolor="white")
    nx.draw_networkx_edges(G, pos, alpha=0.15, width=0.5, edge_color="black")
    nx.draw_networkx_nodes(
        G, pos, nodelist=nodes, node_size=node_sizes, node_color=node_colors,
        cmap=plt.cm.Set3, alpha=0.9, linewidths=0,
    )
    plt.title(title, fontsize=14, color="black")
    plt.axis("off")
    plt.tight_layout()
    plt.savefig(outpath, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close()


def main():
    parser = argparse.ArgumentParser(description="Build networks, projections, metrics, and communities.")
    parser.add_argument("--input-file", required=True, help="Input app-version dataset CSV.")
    parser.add_argument("--provider-mapping", default=None, help="SDK-provider mapping CSV.")
    parser.add_argument("--outdir", default="outputs/network_analysis", help="Output directory.")

    parser.add_argument("--sample-mode", choices=["all", "random"], default="all")
    parser.add_argument("--sample-size", type=int, default=None)
    parser.add_argument("--random-state", type=int, default=42)

    parser.add_argument("--build-networks", action="store_true")
    parser.add_argument("--compute-metrics", action="store_true")
    parser.add_argument("--run-louvain", action="store_true")
    parser.add_argument("--run-leiden", action="store_true")
    parser.add_argument("--run-domino", action="store_true")
    parser.add_argument("--make-figures", action="store_true")

    parser.add_argument("--build-app-projection", action="store_true", help="Build app-app projection. Can be large.")
    parser.add_argument("--app-projection-sample-size", type=int, default=1000)
    parser.add_argument("--community-networks", default="sdk,provider", help="Comma-separated: sdk,provider,app.")
    parser.add_argument("--domino-max-outer", type=int, default=3)
    parser.add_argument("--no-giant-for-communities", action="store_true")

    args = parser.parse_args()

    if not any([args.build_networks, args.compute_metrics, args.run_louvain, args.run_leiden, args.run_domino, args.make_figures]):
        args.build_networks = True
        args.compute_metrics = True

    outdir = make_output_dirs(args.outdir)

    latest_df = load_latest_app_versions(args.input_file, args.sample_mode, args.sample_size, args.random_state)
    latest_df.drop(columns=["sdk_list"], errors="ignore").to_csv(outdir / "latest_apps_used.csv", index=False)

    app_sdk_edges = build_app_sdk_edges(latest_df)
    app_sdk_edges.to_csv(outdir / "app_sdk_edges.csv", index=False)

    B_sdk, app_labels, sdk_labels = edge_list_to_sparse_bipartite(app_sdk_edges, "pkg_name", "sdk_name")

    networks = {}
    metric_rows = []

    G_app_sdk_bip = bipartite_graph_from_edges(app_sdk_edges, "pkg_name", "sdk_name", "app", "sdk")
    metric_rows.append(bipartite_matrix_summary(B_sdk, "app_sdk_bipartite_matrix"))
    metric_rows.append(graph_summary(G_app_sdk_bip, "app_sdk_bipartite_graph", "bipartite"))

    A_sdk, W_sdk = projection_from_bipartite(B_sdk, side="columns", binary=True)
    G_sdk = graph_from_projection(A_sdk, sdk_labels)
    networks["sdk"] = G_sdk
    sparse_projection_to_edges(A_sdk, sdk_labels, "sdk_source", "sdk_target", W_sdk).to_csv(outdir / "sdk_sdk_projection_edges.csv", index=False)
    metric_rows.append(graph_summary(G_sdk, "sdk_sdk_projection_full", "monopartite"))
    metric_rows.append(graph_summary(largest_connected_component(G_sdk), "sdk_sdk_projection_giant", "monopartite"))

    if args.provider_mapping is not None:
        app_provider_edges = build_app_provider_edges(app_sdk_edges, args.provider_mapping)
        app_provider_edges.to_csv(outdir / "app_provider_edges.csv", index=False)
        B_provider, app_provider_labels, provider_labels = edge_list_to_sparse_bipartite(app_provider_edges, "pkg_name", "provider")
        G_app_provider_bip = bipartite_graph_from_edges(app_provider_edges, "pkg_name", "provider", "app", "provider")
        metric_rows.append(bipartite_matrix_summary(B_provider, "app_provider_bipartite_matrix"))
        metric_rows.append(graph_summary(G_app_provider_bip, "app_provider_bipartite_graph", "bipartite"))
        A_provider, W_provider = projection_from_bipartite(B_provider, side="columns", binary=True)
        G_provider = graph_from_projection(A_provider, provider_labels)
        networks["provider"] = G_provider
        sparse_projection_to_edges(A_provider, provider_labels, "provider_source", "provider_target", W_provider).to_csv(outdir / "provider_provider_projection_edges.csv", index=False)
        metric_rows.append(graph_summary(G_provider, "provider_provider_projection_full", "monopartite"))
        metric_rows.append(graph_summary(largest_connected_component(G_provider), "provider_provider_projection_giant", "monopartite"))

    if args.build_app_projection:
        n_sample = min(args.app_projection_sample_size, B_sdk.shape[0])
        rng = np.random.default_rng(args.random_state)
        sampled_idx = np.sort(rng.choice(B_sdk.shape[0], size=n_sample, replace=False))
        B_app_sub = B_sdk[sampled_idx, :]
        app_sample_labels = [app_labels[i] for i in sampled_idx]
        A_app, W_app = projection_from_bipartite(B_app_sub, side="rows", binary=True)
        G_app = graph_from_projection(A_app, app_sample_labels)
        networks["app"] = G_app
        sparse_projection_to_edges(A_app, app_sample_labels, "app_source", "app_target", W_app).to_csv(outdir / "app_app_projection_edges.csv", index=False)
        pd.DataFrame({"pkg_name": app_sample_labels}).to_csv(outdir / "app_projection_sampled_apps.csv", index=False)
        metric_rows.append(graph_summary(G_app, "app_app_projection_sample_full", "monopartite"))
        metric_rows.append(graph_summary(largest_connected_component(G_app), "app_app_projection_sample_giant", "monopartite"))

    for name, G in networks.items():
        save_pickle(G, outdir / "pickles" / f"G_{name}.pkl")

    if args.compute_metrics:
        pd.DataFrame(metric_rows).to_csv(outdir / "network_summary_metrics.csv", index=False)
        logging.info("Saved network summary metrics.")

    community_networks = [x.strip() for x in args.community_networks.split(",") if x.strip()]
    community_rows = []

    if args.run_louvain or args.run_leiden or args.run_domino or args.make_figures:
        for net_name in community_networks:
            if net_name not in networks:
                logging.warning("Skipping network '%s': not available.", net_name)
                continue
            G_base = networks[net_name]
            G_comm = G_base.copy() if args.no_giant_for_communities else largest_connected_component(G_base)
            scope = "full" if args.no_giant_for_communities else "giant"

            if G_comm.number_of_nodes() < 2 or G_comm.number_of_edges() == 0:
                logging.warning("Skipping community detection for '%s': graph too small.", net_name)
                continue

            partition_maps = {}

            if args.run_louvain:
                logging.info("Running Louvain on %s (%s)...", net_name, scope)
                part, pmap, Q = run_louvain(G_comm, seed=args.random_state)
                partition_maps["louvain"] = pmap
                community_rows.append({"network": net_name, "scope": scope, "algorithm": "louvain", "communities": len(part), "modularity": Q, "bic": np.nan, "nodes_used": G_comm.number_of_nodes(), "edges_used": G_comm.number_of_edges()})
                if args.make_figures:
                    draw_partition_figure(G_comm, pmap, f"{net_name.upper()} network: Louvain communities", outdir / "figures" / f"{net_name}_louvain.png", seed=args.random_state)

            if args.run_leiden:
                logging.info("Running Leiden on %s (%s)...", net_name, scope)
                try:
                    part, pmap, Q = run_leiden(G_comm, seed=args.random_state)
                    partition_maps["leiden"] = pmap
                    community_rows.append({"network": net_name, "scope": scope, "algorithm": "leiden", "communities": len(part), "modularity": Q, "bic": np.nan, "nodes_used": G_comm.number_of_nodes(), "edges_used": G_comm.number_of_edges()})
                    if args.make_figures:
                        draw_partition_figure(G_comm, pmap, f"{net_name.upper()} network: Leiden communities", outdir / "figures" / f"{net_name}_leiden.png", seed=args.random_state)
                except ImportError as exc:
                    logging.warning(str(exc))

            if args.run_domino:
                for degree_corrected, algo in [(False, "domino_sbm"), (True, "domino_dcsbm")]:
                    logging.info("Running %s on %s (%s)...", algo, net_name, scope)
                    try:
                        part, pmap, bic, raw = run_domino(G_comm, degree_corrected=degree_corrected, max_outer=args.domino_max_outer)
                        partition_maps[algo.replace("domino_", "")] = pmap
                        community_rows.append({"network": net_name, "scope": scope, "algorithm": algo, "communities": len(part), "modularity": np.nan, "bic": bic, "nodes_used": G_comm.number_of_nodes(), "edges_used": G_comm.number_of_edges()})
                        save_pickle(raw, outdir / "pickles" / f"{net_name}_{algo}_raw.pkl")
                        if args.make_figures:
                            draw_partition_figure(G_comm, pmap, f"{net_name.upper()} network: {algo}", outdir / "figures" / f"{net_name}_{algo}.png", seed=args.random_state)
                    except ImportError as exc:
                        logging.warning(str(exc))
                    except Exception as exc:
                        logging.warning("%s failed for %s: %s", algo, net_name, exc)

            if partition_maps:
                save_partitions(net_name, G_comm, partition_maps, outdir)

    if community_rows:
        pd.DataFrame(community_rows).to_csv(outdir / "community_detection_summary.csv", index=False)
        logging.info("Saved community detection summary.")

    logging.info("Completed network construction and analysis.")
    logging.info("Outputs saved to: %s", outdir)


if __name__ == "__main__":
    main()
