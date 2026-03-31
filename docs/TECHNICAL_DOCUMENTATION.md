# Technical Documentation

## 1. Overview

`qbr` is a Python-based quarterly business review report generator for PPC reporting. It converts CSV exports into a formatted PowerPoint deck using a deterministic pipeline:

1. Load client and style configuration.
2. Load and normalize PPC performance data.
3. Detect the latest complete quarter in the dataset.
4. Aggregate monthly, quarterly, and YoY metrics.
5. Optionally enrich with Google Trends and Auction Insights data.
6. Generate narrative bullets and recommendations.
7. Render charts to PNG files.
8. Build a PowerPoint presentation from a template.

Primary use cases:

- produce a single quarterly client report from local CSV files
- batch-generate reports for all configured clients
- allow non-technical users to upload files and download a deck through Streamlit

## 2. Repository Structure

```text
qbr/
  app.py
  batch_generate.py
  main.py
  requirements.txt
  README.md
  config/
    chart_styles.yaml
    clients_config.json
    report_config.yaml
  data/
    ...
  docs/
    NON_TECHNICAL_OVERVIEW.md
    TECHNICAL_DOCUMENTATION.md
  output/
    ...
  charts/
    ...
  src/
    auction_loader.py
    auction_metrics.py
    chart_builder.py
    config_loader.py
    data_loader.py
    metrics.py
    narrative_generator.py
    recommendation_generator.py
    report_pipeline.py
    slide_builder.py
    trends_loader.py
    trends_metrics.py
  templates/
    report_template.pptx
```

## 3. Runtime Architecture

### 3.1 Main components

- `main.py`
  Parses CLI arguments and runs a single report.
- `batch_generate.py`
  Loops through configured clients and generates one deck per client.
- `app.py`
  Streamlit upload UI for interactive generation.
- `src/report_pipeline.py`
  Orchestrates the full end-to-end pipeline.

### 3.2 Supporting components

- `src/config_loader.py`
  Loads YAML and JSON configuration, merges defaults, and resolves client settings.
- `src/data_loader.py`
  Loads performance CSVs, normalizes columns, parses dates, and detects a valid quarter.
- `src/metrics.py`
  Computes monthly tables, totals, mix tables, and YoY values.
- `src/trends_loader.py`
  Loads Google Trends CSV files and normalizes terms.
- `src/trends_metrics.py`
  Matches configured terms, computes quarter comparisons, and classifies trend shape.
- `src/auction_loader.py`
  Normalizes Auction Insights exports and converts percent columns to decimals.
- `src/auction_metrics.py`
  Produces competitor summaries and a formatted table for presentation.
- `src/chart_builder.py`
  Renders PNG charts with Matplotlib.
- `src/narrative_generator.py`
  Creates text bullets for performance, trends, and auction insights.
- `src/recommendation_generator.py`
  Creates deterministic recommendations from performance and enrichment data.
- `src/slide_builder.py`
  Creates the `.pptx` file with template-aware and fallback slide rendering.

## 4. End-to-End Processing Flow

### 4.1 Single report generation

`main.py` accepts:

- positional `input_csv`
- `--performance-csv`
- `--client-id`
- `--auction-csv`
- `--trends-dir`
- `--output`

It passes these inputs into `run_report()`, which instantiates `ReportPipeline` and calls `ReportPipeline.run()`.

### 4.2 Pipeline sequence

`ReportPipeline.run()` performs the following work:

1. Resolve the selected client config with `ConfigLoader.get_client_config()`.
2. Resolve the PowerPoint template path.
3. Load the performance CSV through `load_csv()`.
4. Detect the latest complete quarter via `detect_latest_complete_quarter()`.
5. Build report data with `prepare_report_data()`.
6. Validate that grouped totals match overall totals using `validate_report_data()`.
7. Build chart and slide builder instances.
8. Add title slide.
9. Conditionally build performance slides.
10. Optionally load and summarize Google Trends.
11. Optionally load and summarize Auction Insights.
12. Generate recommendations from all available signals.
13. Save the final presentation.

### 4.3 Section toggles

Slides are conditionally included from `clients_config.json` under `slides`:

- `include_performance`
- `include_overview`
- `include_campaign_mix`
- `include_campaign_summary`
- `include_destination_summary`
- `include_trends`
- `include_auction_insights`
- `include_recommendations`

Legacy YAML list-based slide config is still supported through `report_config.yaml`.

## 5. Input Contracts

## 5.1 Performance CSV

### Required logical fields

- `date`
- `campaign_type`
- `destination`
- `impressions`
- `clicks`
- `cost`
- `sales_leads`

### Accepted input headers

The loader maps these CSV headers:

- `Date` -> `date`
- `Campaign Type` -> `campaign_type`
- `Destination` -> `destination`
- `Impressions` -> `impressions`
- `Clicks` -> `clicks`
- `Cost` -> `cost`
- `Sales Leads` -> `sales_leads`
- `Leads` -> `sales_leads`
- `Revenue` -> `revenue`

### Loader behaviour

- parses dates with `dayfirst=True`
- drops rows with invalid dates
- coerces metric columns to numeric
- fills missing numeric values with `0`
- fills missing `campaign_type` and `destination` with `Unknown`
- adds derived columns:
  `year`, `month`, `quarter`, `month_start`

### Quarter selection rule

The system selects the latest quarter where all three months are present in the data. If no complete quarter exists, execution fails.

This means partial in-progress quarters are intentionally excluded.

## 5.2 Google Trends CSVs

### Supported shape

Each trends CSV must contain:

- one date column named one of `Date`, `Time`, `Week`, or `Month`
- one or more term columns containing numeric interest values

### Loader behaviour

- each file is read independently
- wide-format data is melted into rows of `date`, `term`, `value`
- terms are normalized to improve matching
- files are combined into one dataframe
- a `source_file` column is retained

### Term normalization

The term normalizer:

- lowercases terms
- trims whitespace
- replaces `_` and `-` with spaces
- normalizes plural variants like `holidays` -> `holiday`, `tours` -> `tour`

This is used by `TrendsLoader.match_terms()` to connect client config terms to CSV column names.

## 5.3 Auction Insights CSV

### Supported shape

The loader is designed for Google Ads Auction Insights exports with header variations such as:

- display URL/domain
- impression share
- overlap rate
- position above rate
- top of page rate
- absolute top of page rate
- outranking share

### Loader behaviour

- skips pre-header lines until the real auction header row is found
- normalizes header names
- strips URL prefixes from domains
- converts percentage strings to decimal floats
- treats empty values, `--`, and `< 10%` as unavailable

If no domain column is found, execution fails for that dataset.

## 6. Configuration

## 6.1 Primary configuration files

- [config/clients_config.json](/Users/sergeysotskiy/Documents/GitHub/qbr/config/clients_config.json)
- [config/chart_styles.yaml](/Users/sergeysotskiy/Documents/GitHub/qbr/config/chart_styles.yaml)
- [config/report_config.yaml](/Users/sergeysotskiy/Documents/GitHub/qbr/config/report_config.yaml)

## 6.2 `clients_config.json`

This is the main project configuration source. It supports multiple clients. Each client object can define:

- `id`
- `name`
- `country`
- `site_domain`
- `report_title`
- `agency`
- `template_path`
- `campaign_types`
- `destinations`
- `brand_trends`
- `destination_trends`
- `auction_insights`
- `slides`
- `source_notes`
- optional `trend_aliases`

### Current client model example

```json
{
  "id": "wendy_wu",
  "name": "Wendy Wu Tours",
  "template_path": "templates/report_template.pptx",
  "campaign_types": ["Brand", "Generic", "Performance Max", "Demand Gen"],
  "destinations": ["China", "Japan", "SE Asia", "India"],
  "brand_trends": {
    "enabled": true,
    "terms": ["wendy wu tours"]
  },
  "destination_trends": {
    "enabled": true,
    "destinations": [
      {"name": "Japan", "terms": ["japan holidays", "japan tours"]}
    ]
  },
  "auction_insights": {
    "enabled": true,
    "client_domain": "wendywu.co.uk",
    "known_competitors": ["travelclubelite.com", "riviera.co.uk"]
  },
  "slides": {
    "include_performance": true,
    "include_overview": true,
    "include_campaign_mix": true,
    "include_campaign_summary": true,
    "include_destination_summary": true,
    "include_trends": true,
    "include_auction_insights": true,
    "include_recommendations": true
  }
}
```

Australia can be added as a separate client with the same structure, for example:

```json
{
  "id": "wendy_wu_australia",
  "name": "Wendy Wu Tours Australia",
  "country": "Australia",
  "site_domain": "wendywutours.com.au",
  "template_path": "templates/report_template.pptx",
  "campaign_types": ["Brand", "Generic", "Performance Max", "Demand Gen"],
  "destinations": ["China", "Japan", "SE Asia", "India"],
  "brand_trends": {
    "enabled": true,
    "terms": ["wendy wu tours australia"]
  },
  "trend_aliases": {
    "wendy wu tours australia": ["wendy wu tours"]
  },
  "destination_trends": {
    "enabled": true,
    "destinations": [
      {"name": "Japan", "terms": ["japan holidays", "japan tours"]}
    ]
  },
  "auction_insights": {
    "enabled": true,
    "client_domain": "wendywutours.com.au",
    "known_competitors": ["travelclubelite.com", "riviera.co.uk"]
  },
  "slides": {
    "include_performance": true,
    "include_overview": true,
    "include_campaign_mix": true,
    "include_campaign_summary": true,
    "include_destination_summary": true,
    "include_trends": true,
    "include_auction_insights": true,
    "include_recommendations": true
  }
}
```

## 6.3 `report_config.yaml`

This is a backward-compatibility fallback for older single-client behaviour. If `clients_config.json` does not provide clients, the project derives a legacy client configuration from this YAML.

## 6.4 `chart_styles.yaml`

Defines:

- chart colors
- table colors
- body/title font sizes
- chart figure size

These values affect both chart rendering and slide text/table styling.

## 7. Metric Computation

## 7.1 Report structure returned by `prepare_report_data()`

The core report dictionary contains:

- `quarter`
- `include_revenue`
- `overall`
- `campaigns`
- `destinations`
- `mix_overall`
- `dest_mix`
- `available_campaigns`
- `available_destinations`

Each scope entry such as `overall`, `campaigns["Brand"]`, or `destinations["Japan"]` contains:

- `monthly`
- `total`
- `prior_total`
- `yoy`

## 7.2 Metrics produced

For monthly and total views:

- impressions
- clicks
- cost
- sales leads
- revenue when present
- CTR
- CPC
- CPL
- CVR

### Formula definitions

- `CTR = Clicks / Impressions`
- `CPC = Cost / Clicks`
- `CPL = Cost / Sales Leads`
- `CVR = Sales Leads / Clicks`
- `YoY = (current - prior) / prior`

Safe division returns `None` when the denominator is zero or missing.

## 7.3 Mix tables

`build_mix_table()` groups by campaign type and computes:

- `Cost Share`
- `Lead Share`
- `CPL`

These mix tables drive pie charts and campaign allocation commentary.

## 7.4 Validation logic

Two classes of validation are built in:

- monthly totals must equal the sum of the three individual month rows
- campaign and destination lead totals must reconcile to overall leads

The code also checks that filtered subsets do not accidentally equal the global totals when they should not.

## 8. Trends Processing

## 8.1 Matching configured terms to CSV columns

`TrendsLoader.match_terms()` tries to find configured terms by:

1. exact normalized match
2. alias match if `trend_aliases` are supplied
3. substring match against normalized available terms

## 8.2 Output of `build_trend_summary()`

Each trend summary includes:

- `name`
- `terms`
- `current_average`
- `previous_year_average`
- `yoy_change`
- `peak_months`
- `classification`
- `seasonality_summary`
- `comparison`
- `history`
- `term_count`

## 8.3 Trend classification

`classify_trend()` uses recent history to classify the trend as:

- `increasing`
- `decreasing`
- `flat`
- `seasonal / spiky`

The classification is based on a simple slope and coefficient-of-variation heuristic, not a statistical forecasting model.

## 9. Auction Insights Processing

`summarize_auction_insights()` produces:

- `competitor_count`
- `our_impression_share`
- `top_overlap_competitors`
- `top_impression_share_competitors`
- `top_outranking_competitors`
- `average_top_of_page_rate`
- `table`

If a `client_domain` is configured, the project separates the client's own row from competitor rows before creating summaries.

Known competitors are tagged internally, although the current presentation layer does not render that flag separately.

## 10. Recommendation Logic

Recommendations are rule-based and deterministic. They are generated from:

- overall YoY performance
- campaign mix efficiency and scale
- brand trend growth
- strongest destination demand growth
- auction overlap and impression share

The generator returns up to five recommendation objects:

```python
{"heading": "...", "text": "..."}
```

There is no LLM dependency in this repository. All recommendations are generated from code rules.

## 11. Chart Rendering

Charts are built with Matplotlib and saved as PNG files.

### Chart types

- scope trend charts
  - `CPL vs CVR`
  - `Cost vs Sales Leads`
- mix charts
  - pie chart for cost share
  - pie chart for lead share
- trends charts
  - current period vs prior-year interest

### Output location

Charts are written under:

`charts/<client_id>/<year>_Q<quarter>/`

Example:

`charts/wendy_wu/2026_Q1/overall_cost_leads.png`

### Rendering notes

- Matplotlib uses the non-interactive `Agg` backend
- `MPLCONFIGDIR` is redirected to a writable directory under the chart folder to avoid environment issues
- empty datasets render a placeholder image rather than crashing chart generation

## 12. PowerPoint Generation

## 12.1 Template strategy

The system reads [templates/report_template.pptx](/Users/sergeysotskiy/Documents/GitHub/qbr/templates/report_template.pptx) and looks for marker text inside template slides:

- `{{SLIDE_TYPE:TITLE}}`
- `{{SLIDE_TYPE:DIVIDER}}`
- `{{SLIDE_TYPE:TREND}}`
- `{{SLIDE_TYPE:TABLE}}`
- `{{SLIDE_TYPE:MIX}}`

It also replaces content placeholders such as:

- `{{TITLE}}`
- `{{SUBTITLE}}`
- `{{CHART_LEFT}}`
- `{{CHART_RIGHT}}`
- `{{TABLE_DATA}}`
- `{{BULLETS}}`

If a matching template slide is missing, `SlideBuilder` falls back to building a plain slide programmatically.

## 12.2 Slide types produced

- title slide
- divider slides
- dual-chart trend slides
- dual-pie mix slides
- table slides
- single-chart trend slides
- auction insights slide
- recommendations slide

## 12.3 Styling

Slide colors and font sizes are partly inherited from `chart_styles.yaml`. The deck background and fallback layouts are code-defined in `SlideBuilder`.

## 13. Entry Points and Usage

## 13.1 Single run CLI

```bash
python main.py \
  --performance-csv data/wendy_wu.csv \
  --client-id wendy_wu \
  --auction-csv data/auction/wendy_wu.csv \
  --trends-dir data/trends/wendy_wu \
  --output output/wendy_wu_report.pptx
```

Positional input is also accepted:

```bash
python main.py data/wendy_wu.csv
```

## 13.2 Batch generation

Batch mode expects:

- performance CSV at `data/<client_id>.csv`
- optional trends data at `data/trends/<client_id>/`
- optional auction CSV at `data/auction/<client_id>.csv`

Run:

```bash
python batch_generate.py \
  --performance-dir data \
  --trends-dir data/trends \
  --auction-dir data/auction \
  --output-dir output
```

## 13.3 Streamlit UI

Run:

```bash
streamlit run app.py
```

Behaviour:

- loads available client IDs from `clients_config.json`
- accepts performance, auction, and trends file uploads
- stores uploads under `temp_uploads/<request_id>/`
- generates the report
- provides a download button for the `.pptx`

## 14. Environment and Dependencies

Install:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Current Python package dependencies:

- `pandas`
- `matplotlib`
- `python-pptx`
- `PyYAML`
- `streamlit`

The project does not currently include:

- pinned Python version metadata
- test suite
- lint/format config
- packaging metadata like `pyproject.toml`

## 15. Outputs and Persistence

## 15.1 Generated files

- charts: `charts/...`
- decks: `output/...`
- temporary uploaded files: `temp_uploads/...`

## 15.2 File lifecycle

- chart files are regenerated per run
- output presentations are overwritten when the same output path is reused
- temporary uploads are retained unless manually cleaned up

## 16. Failure Modes and Troubleshooting

### Common failure cases

- missing required performance CSV columns
- no complete quarter in the performance data
- `client_id` not found in config
- trends directory exists but contains no matching terms
- auction export missing a usable domain column
- template path is invalid

### Important operational notes

- `load_csv()` parses dates with `dayfirst=True`, which is important for UK-style exports
- trends and auction data are optional; the report still generates without them
- missing prior-year values disable YoY for the affected metric rather than failing the whole run
- there are several `print()` debug statements in the codebase that will appear in console output

## 17. Extension Guide

## 17.1 Add a new client

1. Add a client object to [config/clients_config.json](/Users/sergeysotskiy/Documents/GitHub/qbr/config/clients_config.json).
2. Add the client's performance CSV.
3. Optionally add trends and auction files.
4. Run the single-client or batch workflow.

## 17.2 Add a new slide type

1. Add a new builder method in [src/slide_builder.py](/Users/sergeysotskiy/Documents/GitHub/qbr/src/slide_builder.py).
2. Add pipeline orchestration in [src/report_pipeline.py](/Users/sergeysotskiy/Documents/GitHub/qbr/src/report_pipeline.py).
3. Add any new metrics or charts needed.
4. Optionally add a template slide with a new `{{SLIDE_TYPE:...}}` marker.

## 17.3 Change input schema support

Update:

- header aliases in [src/data_loader.py](/Users/sergeysotskiy/Documents/GitHub/qbr/src/data_loader.py)
- auction header aliases in [src/auction_loader.py](/Users/sergeysotskiy/Documents/GitHub/qbr/src/auction_loader.py)
- trends matching logic in [src/trends_loader.py](/Users/sergeysotskiy/Documents/GitHub/qbr/src/trends_loader.py)

## 18. Current Limitations

- no automated tests
- no explicit logging framework
- no schema validation layer beyond runtime checks
- recommendations are heuristic, not model-based
- only the latest complete quarter is processed per run
- presentation layouts are mostly fixed
- currency formatting is hardcoded to pounds sterling
- Streamlit upload cleanup is manual

## 19. Suggested Next Improvements

- add unit tests around loaders, quarter detection, and metric reconciliation
- replace `print()` debugging with structured logging
- add explicit sample input schemas and fixtures
- support configurable currency symbols
- add data quality reporting before slide generation
- add a cleanup routine for `temp_uploads/`
- add CI checks and dependency pinning
