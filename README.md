# Quarterly PPC Report Generator

Reusable Python project to generate quarterly PPC PowerPoint reports from CSV exports. The existing PPC performance workflow is preserved, and the pipeline now also supports Google Trends slides, Auction Insights slides, and a recommendations slide.

## Updated file tree

```text
report_generator/
  batch_generate.py
  config/
    chart_styles.yaml
    clients_config.json
    report_config.yaml
  data/
    input.csv
  templates/
    report_template.pptx
  charts/
  output/
  src/
    __init__.py
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
  main.py
  requirements.txt
  README.md
```

## What changed

- Existing PPC performance reporting remains intact.
- Client settings now come primarily from `config/clients_config.json`.
- Google Trends data can be loaded from CSV exports in a modular way.
- Auction Insights CSV exports can be loaded and normalised.
- Recommendations are generated from performance, trends, and auction signals.
- The CLI accepts optional `--client-id`, `--auction-csv`, and `--trends-dir` inputs.

## Configuration

Primary client config lives in [config/clients_config.json](/Users/sergeysotskiy/Documents/test/report_generator/config/clients_config.json).

Use one client object per client. All client-specific settings belong there:

- report labels
- template path
- campaign ordering
- destination ordering
- brand trend terms
- destination trend terms
- competitor domains
- slide toggles
- source notes

Example:

```json
{
  "clients": [
    {
      "id": "wendy_wu",
      "name": "Wendy Wu Tours",
      "country": "UK",
      "site_domain": "wendywu.co.uk",
      "report_title": "Quarterly PPC Performance Report",
      "agency": "Summon Digital",
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
          {"name": "Japan", "terms": ["japan holidays", "japan tours"]},
          {"name": "China", "terms": ["china holidays", "china tours"]}
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
    },
    {
      "id": "wendy_wu_australia",
      "name": "Wendy Wu Tours Australia",
      "country": "Australia",
      "site_domain": "wendywutours.com.au",
      "report_title": "Quarterly PPC Performance Report",
      "agency": "Summon Digital",
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
          {"name": "Japan", "terms": ["japan holidays", "japan tours"]},
          {"name": "China", "terms": ["china holidays", "china tours"]}
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
  ]
}
```

`config/report_config.yaml` is still supported as a backward-compatible fallback if no JSON client config is present.

## Input files

### Performance CSV

The existing PPC performance input still drives the core report. Expected columns include:

- `Date`
- `Campaign Type`
- `Destination`
- `Impressions`
- `Clicks`
- `Cost`
- `Sales Leads`
- `Revenue` (optional)

### Google Trends CSV

Put one or more CSV exports into a trends directory and pass that directory with `--trends-dir`.

Supported format:

- one date column such as `Month`, `Week`, or `Date`
- one or more trend term columns

Example:

```csv
Month,wendy wu tours,japan holidays,japan tours
2024-10-01,42,35,28
2024-11-01,48,37,29
2024-12-01,54,39,31
```

If multiple terms are configured for a destination, the loader uses the mean monthly interest across the matched terms.

Optional future support exists for `pytrends`, but it is not required and is not enabled by default.

### Auction Insights CSV

Pass an exported Google Ads Auction Insights file with `--auction-csv`.

Common header variations are normalised for:

- domain
- impression share
- overlap rate
- position above rate
- top of page rate
- absolute top of page rate
- outranking share

## Commands

### Existing command still works

```bash
python main.py data/input.csv
```

### Extended single-run command

```bash
python main.py \
  --performance-csv data/input.csv \
  --client-id wendy_wu \
  --auction-csv data/auction_insights.csv \
  --trends-dir data/trends \
  --output output/WWT_QBR.pptx
```

### Batch mode

Batch generation looks for:

- `data/<client_id>.csv`
- optional trends at `<trends-dir>/<client_id>/`
- optional auction file at `<auction-dir>/<client_id>.csv`

Run:

```bash
python batch_generate.py \
  --performance-dir data \
  --trends-dir data/trends \
  --auction-dir data/auction \
  --output-dir output
```

## How the pipeline works

1. `main.py` parses CLI arguments and calls `ReportPipeline`.
2. `config_loader.py` loads chart styling and resolves the selected client config.
3. `data_loader.py` loads the performance CSV and detects the latest complete quarter.
4. `metrics.py` builds the existing PPC aggregates and YoY calculations.
5. `trends_loader.py` and `trends_metrics.py` optionally load and summarise Google Trends data.
6. `auction_loader.py` and `auction_metrics.py` optionally load and summarise Auction Insights data.
7. `recommendation_generator.py` creates deterministic next-step recommendations from the available evidence.
8. `chart_builder.py` renders performance charts and trend comparison charts.
9. `narrative_generator.py` creates bullets for performance, trends, and auction insights.
10. `slide_builder.py` assembles the deck, including the new trends, auction, and recommendations slides.

## Module responsibilities

- [src/config_loader.py](/Users/sergeysotskiy/Documents/test/report_generator/src/config_loader.py)
  Loads YAML and JSON config, resolves client settings, and preserves legacy YAML fallback behaviour.
- [src/trends_loader.py](/Users/sergeysotskiy/Documents/test/report_generator/src/trends_loader.py)
  Loads and normalises Google Trends CSV exports, with an optional future `pytrends` hook.
- [src/trends_metrics.py](/Users/sergeysotskiy/Documents/test/report_generator/src/trends_metrics.py)
  Summarises trend averages, YoY, peaks, and broad trend classification.
- [src/auction_loader.py](/Users/sergeysotskiy/Documents/test/report_generator/src/auction_loader.py)
  Loads and normalises Auction Insights CSV exports.
- [src/auction_metrics.py](/Users/sergeysotskiy/Documents/test/report_generator/src/auction_metrics.py)
  Builds auction summaries and a formatted table for the slide.
- [src/recommendation_generator.py](/Users/sergeysotskiy/Documents/test/report_generator/src/recommendation_generator.py)
  Creates deterministic recommendations from performance, trends, and competition data.
- [src/report_pipeline.py](/Users/sergeysotskiy/Documents/test/report_generator/src/report_pipeline.py)
  Orchestrates the full report build while skipping unavailable optional sections cleanly.

## Error handling

- Missing performance CSV still fails fast.
- Missing trends directory skips trends slides.
- Missing auction CSV skips the auction slide.
- Missing prior-year data leaves YoY as unavailable rather than crashing.
- Missing revenue is still supported.
- Unknown client IDs raise a clear error message.

## Setup

```bash
cd report_generator
python3 -m pip install -r requirements.txt
```
