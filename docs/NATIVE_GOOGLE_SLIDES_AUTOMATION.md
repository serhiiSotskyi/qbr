# Native Google Slides Automation

This is enabled only on `pages/API_Source_Test.py` for quarterly/QBR reports.
The main upload page still produces the existing PPTX, text report, and Claude
handoff package even if native Google Slides generation is not configured or
fails.

## Required Streamlit Secrets / Env

Set these in Streamlit Cloud secrets or local `.env`:

- `GOOGLE_WORKSPACE_OAUTH_CLIENT_ID`
- `GOOGLE_WORKSPACE_OAUTH_CLIENT_SECRET`
- `GOOGLE_WORKSPACE_OAUTH_REFRESH_TOKEN`
- `GOOGLE_WORKSPACE_OAUTH_TOKEN_URL=https://oauth2.googleapis.com/token`
- `GOOGLE_DRIVE_OUTPUT_FOLDER_ID`
- `GOOGLE_DRIVE_ASSET_FOLDER_ID`
- `GOOGLE_SLIDES_TEMPLATE_WWT_UK_QBR`
- `GOOGLE_SLIDES_TEMPLATE_WWT_AUS_QBR`
- `GOOGLE_SLIDES_TEMPLATE_WIGHTLINK_QBR`
- `GOOGLE_SLIDES_TEMPLATE_OLYMPIC_QBR`

The template env vars have built-in defaults matching the approved QBR deck IDs,
but setting them explicitly is safer for Streamlit Cloud operations.

## Runtime Flow

1. API Source Test generates normal source CSVs, PPTX, text report, prompt, and
   Claude handoff package.
2. The page writes `report_artifacts.json` beside the generated outputs.
3. Native Slides generation copies the configured template deck into
   `GOOGLE_DRIVE_OUTPUT_FOLDER_ID`.
4. Chart PNGs are uploaded to `GOOGLE_DRIVE_ASSET_FOLDER_ID`, temporarily made
   link-readable for Slides insertion, then restricted again in a cleanup step.
5. The copied deck is updated using Google Slides `batchUpdate`.
6. The page returns the live Google Slides URL and writes
   `google_slides_generation_manifest.json`.
7. If PDF export works, `google_slides_qa.pdf` is written for visual QA.

## Monthly Template Prototype

A first WWT UK monthly native Slides template has been copied from the Jun 2026
example deck and tokenized for the future monthly generator:

- Template: `https://docs.google.com/presentation/d/1864ehY6EwTpneAnh0sTe9xtL6A7bWtLlmTMwqidsX2Q/edit`
- Manifest: `docs/google_slides_templates/wendy_wu_uk_monthly_test_template.json`

The API Source Test page now enables this template for WWT UK monthly reports.
The monthly builder overwrites KPI cards, inserts YTD table rows as needed,
fills/creates native tables from the manifest, and replaces chart slots with
API-generated PNG charts.

WWT AUS monthly, Wightlink monthly, Olympic monthly, and annual native Google
Slides generation remain disabled until matching approved template mappings are
added.
