# Android Tracking SDK Pipeline

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20313937.svg)](https://doi.org/10.5281/zenodo.20313937)

This repository contains the full reproducible pipeline used to construct a large-scale dataset of Android applications, embedded tracking SDKs, and their corresponding provider companies.

The pipeline combines data from:

- https://androzoo.uni.lu/ (Android APK catalogue and Google Play metadata)
- https://reports.exodus-privacy.eu.org/ (SDK detection rules)
- https://42matters.com/google-play-statistics-and-trends (Google Play aggregate statistics used for validation)

The resulting outputs include datasets, network files, validation tables and figures, and community detection results.

This code accompanies the paper submitted to *Scientific Data*.

## Repository Structure

```text
android-tracking_sdk-pipeline/
├── scripts/
├── validation/
├── requirements.txt
├── README.md
└── LICENSE
```



## Table of Contents

- [Repository Structure](#repository-structure)
- [Main Pipeline](#main-pipeline)
- [Scripts](#scripts)
  - [01 Scrape Exodus Rules](#scripts01_scrape_exodus_rulespy)
  - [02 Build App SDK Dataset](#scripts02_build_app_sdk_datasetpy)
  - [03 Build Provider Mapping](#scripts03_build_provider_mappingpy)
  - [04 Build Final Datasets](#scripts04_build_final_datasetspy)
  - [05 Build Network Files](#scripts05_build_network_filespy)
  - [06 Network Construction Metrics Communities](#scripts06_network_construction_metrics_communitiespy)
- [Validation](#validation)
- [Network Analysis](#network-analysis)
- [Reproducibility Statement](#reproducibility-statement)
- [Citation](#citation)
- [License](#license)



## Main Pipeline

1. Scrape SDK detection rules from Exodus Privacy.
2. Sample Android applications and versions from AndroZoo.
3. Download APKs and perform static analysis.
4. Detect embedded tracking SDKs.
5. Retrieve Google Play metadata.
6. Map SDKs to canonical provider companies.
7. Construct final datasets and edge lists.
8. Build network files and projections.
9. Validate representativeness and internal consistency.
10. Generate figures and tables for publication.
    

## Requirements

The pipeline requires Python 3.10 or later.

Install all dependencies with:

```bash
pip install -r requirements.txt
```

The code has been tested in Google Colab and standard Linux environments.


## Scripts

### `scripts/01_scrape_exodus_rules.py`

**Purpose**

Downloads all tracker pages from Exodus Privacy and extracts the code signatures used to detect tracking SDKs in Android applications.

**Extracted information**

- SDK name
- Tracker slug
- Tracker URL
- Code detection rule
- SDK categories (e.g., Advertising, Analytics, Attribution)

**Inputs**

No external input files are required. The script downloads tracker pages directly from Exodus Privacy.

**Outputs**

- `data_raw/exodus_trackers_code_rules.csv`

**Example usage**

```bash
python scripts/01_scrape_exodus_rules.py \
  --outdir data_raw \
  --language en \
  --sleep-seconds 0.25
```

This script should be executed first, as the resulting detection rules are used by subsequent scripts to identify embedded SDKs in Android APK files.


### `scripts/02_build_app_sdk_dataset.py`

**Purpose**

Constructs the core application–SDK dataset by sampling Android applications from the AndroZoo catalogue, downloading APK files, and performing static analysis to detect embedded tracking SDKs using the code signatures extracted from Exodus Privacy. The script also retrieves package-level Google Play metadata from the AndroZoo API and merges it with the SDK detection results.

**Main operations**

- Scans the AndroZoo catalogue (`latest.csv.gz`) and identifies applications distributed through Google Play.
- Randomly samples a user-defined number of package names.
- Queries the AndroZoo API to retrieve all available version codes for each sampled package.
- Downloads all selected APK files using the AndroZoo download API.
- Extracts DEX classes from APK files and matches them against Exodus Privacy detection rules.
- Identifies embedded tracking SDKs and their package roots.
- Retrieves Google Play metadata for each package-version pair.
- Merges metadata and SDK detection outputs.
- Saves intermediate files to allow reproducibility and recovery after interruptions.

**Inputs**

- `data_raw/latest.csv.gz` (downloaded separately from AndroZoo)
- `data_raw/exodus_trackers_code_rules.csv`
- Environment variable `ANDROZOO_API_KEY`

**Outputs**

- `outputs/app_sdk_dataset/sampled_packages.csv`
- `outputs/app_sdk_dataset/sampled_pkg_versions.csv`
- `outputs/app_sdk_dataset/sdk_per_apk.csv`
- `outputs/app_sdk_dataset/gp_metadata_full.ndjson`
- `outputs/app_sdk_dataset/gp_metadata_full_flat.csv`

**Required environment variable**

```bash
export ANDROZOO_API_KEY="your_androzoo_api_key"
```

**Example usage**

```bash
python scripts/02_build_app_sdk_dataset.py \
  --catalogue data_raw/latest.csv.gz \
  --exodus-rules data_raw/exodus_trackers_code_rules.csv \
  --outdir outputs/app_sdk_dataset \
  --sample-size 100000 \
  --seed 42 \
  --prefilter-workers 8 \
  --workers 4 \
  --http-pool 16 \
  --candidate-step 500 \
  --max-candidates 500000 \
  --polite-sleep 1.0
```

**Alternative usage with a fixed sample**

```bash
python scripts/02_build_app_sdk_dataset.py \
  --input-sample-csv data_raw/fixed_sample.csv \
  --exodus-rules data_raw/exodus_trackers_code_rules.csv \
  --outdir outputs/app_sdk_dataset
```
This script is the computational core of the pipeline. It produces the application-level dataset containing APK metadata, detected SDKs, SDK package roots, SDK functional categories, and Google Play metadata. These outputs constitute the foundation for all subsequent validation, provider mapping, and network analyses.

The command-line arguments and outputs above are taken directly from the script header and argument parser in the uploaded file.

### `scripts/03_build_provider_mapping.py`

**Purpose**

Builds a canonical mapping between each detected tracking SDK and its corresponding provider company. The script combines automated web scraping of tracker websites, ownership information reported by Exodus Privacy, and a large manually curated lookup table to assign each SDK to a standardized corporate entity.

**Main operations**

- Loads tracker metadata from `exodus_trackers_code_rules.csv`.
- Visits each tracker page on Exodus Privacy.
- Extracts the official tracker website URL.
- Scrapes company information from website footers, metadata, JSON-LD tags, and legal pages.
- Retrieves ownership information directly from Exodus Privacy as a fallback.
- Applies a manually validated SDK-to-provider mapping for known cases.
- Applies additional corrections to ambiguous scraping results.
- Harmonizes all provider names into canonical company names (e.g., Google Inc, Meta Platforms, Inc, Amazon Inc).
- Produces diagnostic files for unmapped SDKs and provider frequencies.

**Inputs**

- `data_raw/exodus_trackers_code_rules.csv`

**Outputs**

- `outputs/provider_mapping/sdk_company_lookup_v4_style.csv`
- `outputs/provider_mapping/sdk_provider_mapping_final.csv`
- `outputs/provider_mapping/sdk_company_lookup_FINAL_CANONICAL.csv`
- `outputs/provider_mapping/unknown_sdks.csv`
- `outputs/provider_mapping/provider_counts.csv`

**Example usage**

```bash
python scripts/03_build_provider_mapping.py \
  --exodus-rules data_raw/exodus_trackers_code_rules.csv \
  --outdir outputs/provider_mapping \
  --sleep-seconds 0.25
```
**Methodological overview**

Provider assignment follows a hierarchical procedure:

1. **Manual mapping** using a large hand-curated dictionary of validated SDK-provider pairs.
2. **Scraping correction mapping** for known cases where automated extraction returns noisy or incomplete results.
3. **Automated web scraping** of tracker websites and associated legal disclosures.
4. **Exodus Ownership fallback** when company information is explicitly reported by Exodus Privacy.
5. **Canonical standardization** to merge alternative company names into a unified provider identity.

This approach ensures high coverage and consistent attribution of SDKs to their underlying corporate owners, including parent companies and acquisition-based consolidations (e.g., Firebase → Google Inc; MoPub → AppLovin; IronSource → Unity Technologies).

**Research significance**

The resulting mapping is a critical component of the dataset because it transforms SDK-level detections into provider-level entities. This enables the construction of provider–provider networks and allows researchers to study ownership concentration, ecosystem structure, market dominance, and privacy risks associated with major technology companies and advertising platforms.


The description above is based directly on the uploaded script, including its documented outputs, command-line arguments, and provider assignment pipeline. :contentReference[oaicite:0]{index=0} :contentReference[oaicite:1]{index=1} :contentReference[oaicite:2]{index=2}


### `scripts/04_build_final_datasets.py`

**Purpose**

Builds the final public-release datasets by integrating SDK detection results, Google Play metadata, and the canonical SDK–provider mapping. The script produces three core files: an app-version dataset, an app–SDK edge list, and an app–SDK–provider edge list.

**Main operations**

- Loads the SDK detection output generated by `02_build_app_sdk_dataset.py`.
- Loads the flattened Google Play metadata file generated by `02_build_app_sdk_dataset.py`.
- Normalizes merge keys (`sha256`, `pkg_name`, `vercode`) to ensure exact matching.
- Merges SDK detection results with Google Play metadata.
- Creates a metadata merge status indicator (`matched` vs `unmatched`).
- Parses JSON fields containing detected SDK names, package roots, and SDK functional types.
- Expands the app-version dataset into an app–SDK edge list.
- Merges the app–SDK edge list with the canonical provider mapping generated by `03_build_provider_mapping.py`.
- Produces a final app–SDK–provider edge list suitable for provider-level analyses.

**Inputs**

- `outputs/app_sdk_dataset/sdk_per_apk.csv`
- `outputs/app_sdk_dataset/gp_metadata_full_flat.csv`
- `outputs/provider_mapping/sdk_company_lookup_FINAL_CANONICAL.csv`

**Outputs**

- `outputs/final_datasets/final_app_version_dataset.csv`
- `outputs/final_datasets/final_app_sdk_edges.csv`
- `outputs/final_datasets/final_app_sdk_provider_edges.csv`

**Example usage**

```bash
python scripts/04_build_final_datasets.py \
  --run-dir outputs/app_sdk_dataset \
  --provider-path outputs/provider_mapping/sdk_company_lookup_FINAL_CANONICAL.csv \
  --outdir outputs/final_datasets
```

**Generated datasets**

1. **`final_app_version_dataset.csv`**

   One row per APK (application-version record), containing:

   - APK identifiers (`sha256`, `pkg_name`, `vercode`)
   - SDK detection results
   - SDK package roots
   - SDK functional categories
   - Google Play metadata
   - Metadata merge diagnostics

2. **`final_app_sdk_edges.csv`**

   One row per application–SDK relationship, suitable for constructing app–SDK bipartite networks.

3. **`final_app_sdk_provider_edges.csv`**

   One row per application–SDK–provider relationship, enabling the construction of app–provider bipartite networks and provider-level analyses.

**Research significance**

This script consolidates all upstream outputs into the final release-ready datasets distributed with the project. These files constitute the principal data products described in the associated *Scientific Data* article and serve as the foundation for all validation procedures, network analyses, and downstream empirical research on third-party tracking and data-sharing infrastructures in the Android ecosystem.

### `scripts/05_build_network_files.py`

**Purpose**

Constructs all network files used in the study from the final merged application-level dataset. The script applies the same filtering procedure used in the paper, selects the most recent version of each application, generates bipartite and projected networks, and exports them in formats suitable for network analysis software such as Gephi, Cytoscape, NetworkX, and igraph.

The script is specifically designed to reproduce the network datasets distributed with the repository and described in the associated *Scientific Data* article.

**Main operations**

- Loads the final application-version dataset.
- Keeps only APKs with `download_status = OK`.
- Retains only applications with at least one detected SDK.
- Selects the latest available version of each package based on:
  - `vercode`
  - `dex_date`
  - `extraction_ts_utc`
- Parses JSON fields containing SDK names, package roots, and SDK types.
- Builds the application–SDK edge list.
- Merges the canonical SDK–provider mapping.
- Builds the application–SDK–provider edge list.
- Constructs bipartite networks and one-mode projections.
- Computes basic network summaries.
- Exports GraphML files, node tables, edge tables, and a summary table.

---

**Inputs**

- `outputs/final_datasets/final_app_version_dataset.csv`
- `outputs/provider_mapping/sdk_provider_mapping_final.csv`

---

**Outputs**

### Edge lists

- `final_app_sdk_edges.csv`
- `final_app_sdk_provider_edges.csv`

### Bipartite networks

- `app_sdk_bipartite.graphml`
- `app_provider_bipartite.graphml`

### One-mode projections

- `sdk_sdk_projection.graphml`
- `provider_provider_projection.graphml`
- `app_app_projection_*.graphml` (optional)

### Associated files for each network

- `*_nodes.csv`
- `*_edges.csv`
- `*_edges_weighted.csv`

### Summary file

- `network_file_summary.csv`

---

**Constructed networks**

1. **Application–SDK bipartite network**
   - Nodes: applications and SDKs
   - Edge: an application embeds a given SDK

2. **Application–Provider bipartite network**
   - Nodes: applications and provider companies
   - Edge: an application embeds at least one SDK owned by the provider

3. **SDK–SDK projection**
   - Nodes: SDKs
   - Weighted edge: two SDKs co-occur in one or more applications

4. **Provider–Provider projection**
   - Nodes: provider companies
   - Weighted edge: two providers co-occur in one or more applications

5. **Application–Application projection (optional)**
   - Nodes: applications
   - Weighted edge: two applications share one or more SDKs

---

**Example usage**

```bash
python network_analysis/05_build_network_files.py \
  --final-dir outputs/final_datasets \
  --provider-path outputs/provider_mapping/sdk_provider_mapping_final.csv \
  --outdir outputs/networks \
  --build-app-app \
  --app-app-sample-size 250 \
  --seed 42
```

**Optional parameters**

- `--use-all-versions`  
  Uses all application versions instead of retaining only the most recent version.

- `--overwrite-edges`  
  Rebuilds edge lists even if existing files are already present.

- `--sdk-sdk-min-shared-apps`  
  Minimum number of shared applications required to create an SDK–SDK edge.

- `--provider-provider-min-shared-apps`  
  Minimum number of shared applications required to create a provider–provider edge.

- `--build-app-app`  
  Activates construction of the application–application projection.

- `--app-app-sample-size`  
  Limits the application–application projection to a random sample of applications.


**Methodological overview**

The script reproduces exactly the filtering strategy used in the empirical analysis. Only successfully processed APKs with at least one detected SDK are retained, and a single latest version is selected for each package. This ensures that network structures represent the most recent observable technological configuration of each application while avoiding duplication across historical versions.

One-mode projections are constructed by counting co-occurrences within the underlying bipartite networks. Weighted edges represent the number of applications in which two SDKs or two providers appear together.

**Research significance**

This script produces the core network files underlying all structural analyses in the project, including degree distributions, assortativity, clustering, centrality measures, core-periphery analysis, and community detection using Louvain, Leiden, SBM, and degree-corrected SBM. The resulting GraphML and CSV files enable immediate reuse by researchers interested in the architecture of third-party tracking and data-sharing infrastructures in the Android ecosystem.

### `scripts/06_network_construction_metrics_communities.py`

**Purpose**

Builds all major network representations used in the study, computes summary statistics, and optionally runs community detection algorithms. Starting from the final application-level dataset, the script constructs application–SDK and application–provider bipartite networks, generates one-mode projections, computes structural metrics, and estimates communities using Louvain, Leiden, stochastic block models (SBM), and degree-corrected stochastic block models (DCSBM).

This script integrates the complete network analysis workflow and reproduces all network-level outputs described in the associated *Scientific Data* article. :contentReference[oaicite:0]{index=0}

**Main operations**

- Loads the final application-version dataset.
- Retains only APKs with `download_status = OK`.
- Keeps only applications with at least one detected SDK.
- Selects the most recent available version of each package based on:
  - `vercode`
  - `dex_date`
  - `extraction_ts_utc`
- Parses JSON fields containing detected SDK names.
- Builds the application–SDK edge list.
- Merges the canonical SDK–provider mapping.
- Builds the application–provider edge list.
- Constructs sparse bipartite matrices.
- Generates one-mode projections:
  - SDK–SDK
  - Provider–Provider
  - Application–Application (optional)
- Computes network summary statistics:
  - number of nodes and edges
  - density
  - degree moments
  - assortativity
  - clustering coefficient
  - connected components
  - giant component size
- Runs community detection algorithms:
  - Louvain
  - Leiden
  - DOMINO SBM
  - DOMINO degree-corrected SBM (DCSBM)
- Saves partition assignments, figures, and serialized graph objects.

**Inputs**

- `outputs/final_datasets/final_app_version_dataset.csv`
- `outputs/provider_mapping/sdk_provider_mapping_final.csv`

**Outputs**

- `latest_apps_used.csv`
- `app_sdk_edges.csv`
- `app_provider_edges.csv`
- `sdk_sdk_projection_edges.csv`
- `provider_provider_projection_edges.csv`
- `app_app_projection_edges.csv` (optional)
- `network_summary_metrics.csv`
- `community_detection_summary.csv`
- `partitions_sdk.csv`
- `partitions_provider.csv`
- `partitions_app.csv` (optional)
- `figures/*.png`
- `pickles/*.pkl`

**Example usage**

```bash
python scripts/06_network_construction_metrics_communities.py \
  --input-file outputs/final_datasets/final_app_version_dataset.csv \
  --provider-mapping outputs/provider_mapping/sdk_provider_mapping_final.csv \
  --outdir outputs/network_analysis \
  --build-networks \
  --compute-metrics \
  --run-louvain \
  --run-leiden \
  --run-domino \
  --make-figures \
  --build-app-projection \
  --app-projection-sample-size 250 \
  --community-networks sdk,provider,app \
  --random-state 42
```

**Optional parameters**

- `--sample-mode all|random`  
  Uses the full dataset or a random sample of applications.

- `--sample-size`  
  Number of applications to include when `--sample-mode random` is selected.

- `--build-app-projection`  
  Constructs the application–application projection.

- `--app-projection-sample-size`  
  Limits the size of the application–application projection.

- `--community-networks`  
  Specifies which networks should be analyzed (`sdk`, `provider`, `app`).

- `--run-louvain`  
  Runs Louvain modularity maximization.

- `--run-leiden`  
  Runs Leiden modularity maximization.

- `--run-domino`  
  Runs DOMINO to estimate SBM and DCSBM partitions.

- `--make-figures`  
  Generates network visualizations colored by community assignments.

- `--no-giant-for-communities`  
  Runs community detection on the full graph instead of restricting to the giant component.

**Methodological overview**

The script reproduces the filtering strategy used throughout the empirical analysis. Only successfully processed APKs containing at least one detected SDK are retained, and a single latest version is selected for each package. This ensures that network structures reflect the most recent observable technological configuration of each application while avoiding duplication across historical versions.

One-mode projections are constructed by counting co-occurrences within the underlying bipartite matrices. Weighted edges indicate the number of applications in which two SDKs, two providers, or two applications share common technological dependencies.

Community detection is performed on the giant connected component by default. Louvain and Leiden maximize modularity, whereas DOMINO estimates information-theoretic stochastic block models and degree-corrected stochastic block models using the Bayesian Information Criterion (BIC).

**Research significance**

This script produces the principal network datasets and analytical outputs underlying the study. It enables researchers to investigate concentration, modular organization, core-periphery structure, and ownership patterns in third-party tracking ecosystems. The generated CSV, PNG, and serialized graph files can be immediately reused for downstream analyses using NetworkX, igraph, Gephi, Cytoscape, or other network analysis software.

## Validation

The `validation/` directory includes scripts assessing:
- Google Play category representativeness
- Game vs non-game composition
- Free vs paid monetization model
- APK download success
- Metadata merge quality
- Internal consistency checks


### `validation/01_processing_quality_checks.py`

**Purpose**

Performs technical validation checks on the final application-version dataset to assess the quality of data acquisition, APK processing, SDK detection, and metadata integration. The script generates summary tables and figures used in the *Technical Validation* section of the accompanying *Scientific Data* article.

**Main operations**

- Loads the final application-version dataset.
- Computes APK download success rates.
- Evaluates metadata merge completeness.
- Summarizes SDK detection plausibility statistics.
- Performs internal consistency checks.
- Generates the distribution of detected SDKs per APK.
- Produces a consolidated processing quality summary table.

**Inputs**

- `outputs/final_datasets/final_app_version_dataset.csv`

**Outputs**

- `apk_download_status.csv`
- `metadata_merge_status.csv`
- `sdk_detection_summary.csv`
- `sdk_count_distribution.csv`
- `internal_consistency_checks.csv`
- `processing_quality_summary.csv`
- `sdk_count_distribution.png`

**Example usage**

```bash
python validation/01_processing_quality_checks.py \
  --final-app-version outputs/final_datasets/final_app_version_dataset.csv \
  --outdir outputs/validation
```

**Computed validation checks**

1. **APK download success rates**
   - Counts and percentages of APKs successfully downloaded (`download_status = OK`) and failed downloads.

2. **Metadata merge completeness**
   - Counts and percentages of records for which Google Play metadata were successfully merged.

3. **SDK detection plausibility**
   - Number and percentage of applications containing at least one detected SDK.
   - Mean, median, minimum, and maximum number of SDKs per APK.

4. **Internal consistency checks**
   - Duplicate rows based on (`sha256`, `pkg_name`, `vercode`).
   - Records where `sdk_count` differs from the length of the parsed `sdk_names_json` list.

5. **SDK count distribution**
   - Histogram showing the empirical distribution of the number of detected SDKs per APK.

**Methodological overview**

The script evaluates both data completeness and internal coherence. Download success rates and metadata merge rates quantify the proportion of records successfully processed through the acquisition pipeline. SDK detection plausibility statistics summarize the prevalence and distribution of embedded tracking SDKs. Internal consistency checks verify that application identifiers are unique and that aggregate SDK counts match the corresponding JSON-encoded SDK lists.

Together, these diagnostics provide transparent evidence that the final dataset is internally consistent and technically reliable.

**Research significance**

This validation script produces the core quality-control outputs supporting the *Technical Validation* section of the dataset publication. The resulting tables and figures document the integrity of the acquisition pipeline and provide users with quantitative measures of data completeness, consistency, and plausibility before undertaking downstream empirical analyses.


### `validation/02_provider_mapping_validation.py`

**Purpose**

Evaluates the completeness and reliability of the SDK–provider mapping by measuring the proportion of detected SDKs, application–SDK edges, and applications that can be assigned to a canonical provider company. The script also documents the relative contribution of different mapping sources and identifies any remaining unmapped SDKs. 

**Main operations**

- Loads the canonical SDK–provider mapping generated by `03_build_provider_mapping.py`.
- Loads the final application–SDK–provider edge list.
- Determines whether each SDK has a valid canonical provider assignment.
- Computes coverage statistics at the SDK, edge, and application levels.
- Summarizes the distribution of provider assignment sources.
- Exports a list of unmapped SDKs for manual inspection.
- Generates a bar chart showing the contribution of each mapping source.

**Inputs**

- `outputs/provider_mapping/sdk_company_lookup_FINAL_CANONICAL.csv`
- `outputs/final_datasets/final_app_sdk_provider_edges.csv`

**Outputs**

- `provider_mapping_validation_summary.csv`
- `provider_source_distribution.csv`
- `unmapped_sdks.csv`
- `provider_source_distribution.png`

**Example usage**

```bash
python validation/02_provider_mapping_validation.py \
  --provider-mapping outputs/provider_mapping/sdk_company_lookup_FINAL_CANONICAL.csv \
  --app-sdk-provider-edges outputs/final_datasets/final_app_sdk_provider_edges.csv \
  --outdir outputs/validation
```
**Computed validation checks**

1. **SDK-level coverage**
   - Number of unique SDKs in the provider mapping.
   - Number and percentage of SDKs successfully assigned to a canonical provider.
   - Number of remaining unmapped SDKs.

2. **Edge-level coverage**
   - Number of application–SDK–provider edge rows.
   - Number and percentage of edges containing a valid provider assignment.

3. **Detected SDK coverage**
   - Number of unique SDKs observed in the final edge list.
   - Number and percentage of detected SDKs with an assigned provider.

4. **Application-level coverage**
   - Number of unique applications in the edge list.
   - Number and percentage of applications linked to at least one identified provider.

5. **Provider source distribution**
   - Frequency and share of assignments obtained from each source (e.g., manual mapping, automated scraping, Exodus ownership).

6. **Unmapped SDK diagnostics**
   - Export of SDKs without a canonical provider assignment, including available metadata such as SDK type, Exodus URL, and tracker website.

**Methodological overview**

The script validates the ownership attribution process by measuring provider assignment coverage at multiple levels of aggregation. SDK-level coverage quantifies how many distinct SDKs were mapped to corporate entities, whereas edge- and application-level coverage assess how extensively provider information propagates to the final network datasets. The distribution of mapping sources documents the relative contribution of manual curation, automated web scraping, and ownership information from Exodus Privacy.

Together, these diagnostics provide transparent evidence that the SDK–provider mapping achieves high coverage and supports robust provider-level analyses.

**Research significance**

This validation script verifies one of the most important transformations in the pipeline: the conversion of SDK detections into provider-level entities. The resulting statistics demonstrate that the dataset can be used to study ownership concentration, corporate interconnections, and market structure in the Android tracking ecosystem with a high degree of completeness and consistency.

### `validation/03a_sample_representativeness_categories.py`

**Purpose**

Evaluates the external validity of the dataset by comparing the distribution of Google Play application categories in the sample with aggregate marketplace statistics reported by 42matters. The script optionally scrapes current Google Play categories for all package names in the dataset, stores the results in a resumable table, and computes representativeness statistics and figures. :contentReference[oaicite:0]{index=0}

**Main operations**

- Selects one latest APK version for each package name.
- Retains only successfully downloaded APKs containing at least one detected SDK (by default).
- Optionally scrapes Google Play metadata using `google-play-scraper`.
- Saves category information in a resumable checkpoint file.
- Optionally merges scraped categories back into the main dataset.
- Compares the sample category distribution with benchmark statistics from 42matters.
- Computes coverage, category shares, absolute differences, and correlation coefficients.
- Generates comparison tables and a publication-quality figure.

**Inputs**

- `outputs/final_datasets/final_app_version_dataset.csv` (or equivalent merged dataset)
- Optional access to Google Play through the `google-play-scraper` package
- Benchmark category counts from 42matters (hard-coded in the script)

**Outputs**

- `play_categories_final.csv`
- `sample_category_scraping_coverage.csv`
- `sample_vs_google_play_categories.csv`
- `sample_representativeness_summary.csv`
- `sample_vs_google_play_categories.png`
- `final_dataset_with_play_categories.csv` (optional)

**Example usage**

```bash
python validation/03a_sample_representativeness_categories.py \
  --input-dataset outputs/final_datasets/final_app_version_dataset.csv \
  --category-out outputs/validation/play_categories_final.csv \
  --merged-out outputs/validation/final_dataset_with_play_categories.csv \
  --outdir outputs/validation \
  --scrape
```

**Computed validation checks**

1. **Google Play category coverage**
   - Number of package names selected for validation.
   - Number of applications still available on Google Play.
   - Number of applications with a successfully retrieved category.
   - Overall category retrieval coverage.

2. **Category representativeness**
   - Number and percentage of applications in each benchmark category.
   - Comparison with aggregate Google Play category shares reported by 42matters.
   - Differences in percentage points between the sample and the marketplace.

3. **Summary statistics**
   - Mean absolute difference across benchmark categories.
   - Correlation between sample and population category percentages.

4. **Visualization**
   - Horizontal bar chart comparing sample and Google Play category distributions.

**Methodological overview**

The script assesses whether the dataset captures a broad cross-section of Android application types. Because the sample includes both currently active and historically archived APKs from AndroZoo, category information can only be retrieved for applications that remain listed on Google Play at the time of execution. The resulting partial coverage is expected and reflects the high turnover of the mobile application ecosystem.

Representativeness is evaluated by comparing the category composition of matched applications with aggregate Google Play statistics published by 42matters. Differences are summarized using category-specific percentage-point gaps and overall correlation measures.

**Research significance**

This validation script provides external evidence that the sampled applications broadly reflect the composition of the Google Play marketplace. By demonstrating close correspondence between sample and population category distributions, the analysis strengthens confidence that the dataset can be used to study third-party tracking infrastructures across a diverse and representative set of Android applications.

### `validation/03b_sample_representativeness_game_vs_nongame.py`

**Purpose**

Evaluates whether the dataset reproduces the balance between gaming and non-gaming applications observed in the Google Play marketplace. The script classifies each application as either `Game` or `Non-game` using the Google Play `genreId` field and compares the resulting distribution with aggregate counts reported by 42matters.

**Main operations**

- Selects one latest APK version for each package name.
- Retains only successfully downloaded APKs containing at least one detected SDK (by default).
- Loads previously scraped Google Play category metadata.
- Classifies applications as:
  - `Game` if `gp_genreId` starts with `GAME` (e.g., `GAME_PUZZLE`, `GAME_ARCADE`)
  - `Non-game` for all other categories
- Merges classifications with the latest application sample.
- Compares sample shares with external Google Play population counts.
- Computes percentage-point differences and summary statistics.
- Exports validation tables.

**Inputs**

- `outputs/final_datasets/final_app_version_dataset.csv`
- `outputs/validation/play_categories_final.csv`
- Aggregate game and non-game counts from 42matters

**Outputs**

- `latest_apps_with_game_status.csv`
- `game_vs_nongame_coverage.csv`
- `game_vs_nongame_comparison.csv`
- `game_vs_nongame_summary.csv`

**Example usage**

```bash
python validation/03b_sample_representativeness_game_vs_nongame.py \
  --input-dataset outputs/final_datasets/final_app_version_dataset.csv \
  --category-file outputs/validation/play_categories_final.csv \
  --population-game-count 279603 \
  --population-nongame-count 2064795 \
  --outdir outputs/validation/sample_representativeness_game_vs_nongame_fixed
```

**Computed validation checks**

1. **Google Play genre coverage**
   - Number of package names selected for validation.
   - Number of applications with a retrieved `gp_genreId`.
   - Number of applications successfully classified as `Game` or `Non-game`.

2. **Game vs non-game representativeness**
   - Number and percentage of gaming and non-gaming applications in the matched sample.
   - Comparison with aggregate Google Play marketplace shares.
   - Differences in percentage points between the sample and the population.

3. **Summary statistics**
   - Total number of applications in the benchmark population.
   - Number of classified applications in the matched sample.
   - Mean and maximum absolute differences in percentage points.

**Methodological overview**

The classification is based entirely on the standardized Google Play `genreId` field. All categories whose identifier begins with `GAME` are collapsed into a single `Game` category, while all remaining categories are classified as `Non-game`. This approach aggregates subgenres such as Puzzle, Arcade, Strategy, and Role Playing into a unified gaming category.

Representativeness is then evaluated by comparing the resulting sample shares with aggregate Google Play statistics reported by 42matters. This provides a parsimonious but informative validation of whether the dataset captures the relative prevalence of gaming applications, which are known to rely heavily on advertising, analytics, and monetization SDKs.

**Research significance**

This validation script demonstrates that the dataset reproduces the broad distinction between gaming and non-gaming applications observed in the Android ecosystem. Because games constitute an important segment of the mobile economy and are intensive users of third-party tracking technologies, this comparison provides additional evidence that the dataset reflects the structural composition of the Google Play marketplace.

### `validation/03c_sample_representativeness_free_vs_paid.py`

**Purpose**

Evaluates whether the dataset reproduces the balance between free and paid applications observed in the Google Play marketplace. The script classifies each application using the Google Play metadata field `meta_offer.0.micros`, which stores the application price in micro-units of the corresponding currency, and compares the resulting distribution with aggregate marketplace statistics reported by 42matters.

**Main operations**

- Selects one latest APK version for each package name.
- Retains only successfully downloaded APKs containing at least one detected SDK (by default).
- Loads the Google Play price field `meta_offer.0.micros`.
- Classifies applications as:
  - `Free` if `meta_offer.0.micros` is missing or equal to zero.
  - `Paid` if `meta_offer.0.micros` is strictly greater than zero.
- Merges classifications with the latest application sample.
- Compares sample shares with aggregate Google Play population counts.
- Computes percentage-point differences and summary statistics.
- Exports validation tables.

**Inputs**

- `outputs/final_datasets/final_app_version_dataset.csv` (or equivalent merged dataset)
- Google Play metadata field `meta_offer.0.micros`
- Aggregate counts of free and paid applications from 42matters

**Outputs**

- `latest_apps_with_free_paid_status.csv`
- `free_vs_paid_coverage.csv`
- `free_vs_paid_comparison.csv`
- `free_vs_paid_summary.csv`

**Example usage**

```bash
python validation/03c_sample_representativeness_free_vs_paid.py \
  --input-dataset "/content/drive/MyDrive/Massimo SDK Innovation Project/sdk_with_gp_metadata_exactmerge.csv" \
  --population-free-count 2274378 \
  --population-paid-count 69250 \
  --outdir outputs/validation_full/sample_representativeness_free_vs_paid
```

**Computed validation checks**

1. **Free/paid classification coverage**
   - Number of package names selected for validation.
   - Number of applications successfully classified as `Free` or `Paid`.
   - Number of applications with missing price information.
   - Number of applications with strictly positive prices.

2. **Free vs paid representativeness**
   - Number and percentage of free and paid applications in the sample.
   - Comparison with aggregate Google Play marketplace shares.
   - Differences in percentage points between the sample and the population.

3. **Summary statistics**
   - Total number of applications in the benchmark population.
   - Number of classified applications in the sample.
   - Number of free and paid applications in the sample.
   - Mean and maximum absolute differences in percentage points.

**Methodological overview**

The classification is based on the Google Play metadata field `meta_offer.0.micros`, which records application prices in micro-units of the corresponding currency (for example, €1.09 is represented as 1,090,000). Applications with missing values or a value equal to zero are classified as `Free`, whereas applications with strictly positive values are classified as `Paid`.

Representativeness is evaluated by comparing the resulting sample shares with aggregate Google Play statistics reported by 42matters. Because third-party tracking SDKs are most commonly associated with advertising-supported business models, the dataset is expected to contain a predominance of free applications. Paid applications are relatively uncommon in the Android ecosystem and are less likely to rely extensively on advertising and analytics SDKs.

**Research significance**

This validation script demonstrates that the dataset reproduces the overwhelmingly free-to-use nature of the Android application ecosystem. The results confirm that paid applications constitute a very small fraction of the observed sample, while free applications account for nearly all records. This pattern is consistent with both the economic structure of the Google Play marketplace and the central role of advertising and analytics SDKs as mechanisms through which free applications monetize user attention and data.

### `validation/04_sdk_detection_plausibility.py`

**Purpose**

Evaluates the plausibility of the SDK detection procedure by comparing the prevalence of the most common tracking SDKs observed in the dataset with aggregate tracker prevalence statistics reported by Exodus Privacy. The script quantifies the extent to which the ranking and frequency of detected SDKs align with an independent external benchmark. :contentReference[oaicite:0]{index=0}

**Main operations**

- Loads the full application-version dataset.
- Retains successfully downloaded APKs containing at least one detected SDK.
- Selects the most recent available version of each package name.
- Parses the JSON field `sdk_names_json`.
- Computes SDK prevalence across unique applications.
- Builds an external benchmark using manually extracted prevalence statistics from Exodus Privacy.
- Harmonizes SDK names between the sample and the benchmark.
- Compares prevalence levels and rankings.
- Computes overlap and correlation statistics.
- Exports detailed comparison tables and summary metrics.

**Inputs**

- `outputs/final_datasets/final_app_version_dataset.csv`
- Manually extracted tracker prevalence statistics from Exodus Privacy (hard-coded in the script)

**Outputs**

- `sample_sdk_prevalence.csv`
- `exodus_sdk_benchmark.csv`
- `sdk_detection_plausibility_comparison.csv`
- `sdk_detection_plausibility_summary.csv`

**Example usage**

```bash
python validation/04_sdk_detection_plausibility.py \
  --full-dataset outputs/final_datasets/final_app_version_dataset.csv \
  --outdir outputs/validation/sdk_detection_plausibility
```


## Network Analysis

The `network_analysis/` directory includes:
- Bipartite and projected network construction
- Centrality measures
- Core-periphery analysis
- Community detection using Louvain, Leiden, SBM, and DCSBM

## Reproducibility Statement

The full pipeline has been tested in Google Colab. All outputs can be regenerated from raw inputs by executing the scripts sequentially.

## Citation

If you use this repository, dataset, or derived network files, please cite:

Gori Savellini, A. (2026). *Android Tracking SDK Dataset and Reproducible Pipeline*. Zenodo. https://doi.org/10.5281/zenodo.20313937

## License

This project is distributed under the terms of the license included in the `LICENSE` file.
