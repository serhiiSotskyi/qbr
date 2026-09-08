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
- `GOOGLE_DRIVE_OUTPUT_SHARE_ENABLED=true`
- `GOOGLE_DRIVE_COPY_TEMPLATE_PERMISSIONS=true`
- `GOOGLE_DRIVE_LINK_SHARE_FALLBACK_ENABLED=true`
- `GOOGLE_DRIVE_LINK_SHARE_FALLBACK_ROLE=reader`
- `GOOGLE_DRIVE_SHARE_DOMAIN=summon.co` (optional; only if Google Workspace domain sharing is enabled)
- `GOOGLE_DRIVE_SHARE_ROLE=writer` (optional explicit target role)
- `GOOGLE_DRIVE_SHARE_EMAILS=person@example.com,person2@example.com` (optional)
- `GOOGLE_DRIVE_SHARE_GROUPS=team@example.com` (optional)
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
6. The generated deck copies the template deck's non-owner Drive sharing
   permissions, then adds any explicit configured Drive share targets. If neither
   path yields a share target, the deck falls back to anyone-with-link reader
   access so non-owner reviewers can open the link.
7. The page returns the live Google Slides URL and writes
   `google_slides_generation_manifest.json`.
8. If PDF export works, `google_slides_qa.pdf` is written for visual QA.

## Monthly Template Prototype

A first WWT UK monthly native Slides template has been copied from the Jun 2026
example deck and tokenized for the future monthly generator:

- Template: `https://docs.google.com/presentation/d/1864ehY6EwTpneAnh0sTe9xtL6A7bWtLlmTMwqidsX2Q/edit`
- Manifest: `docs/google_slides_templates/wendy_wu_uk_monthly_test_template.json`

The API Source Test page now enables this template for WWT UK monthly reports.
The monthly builder overwrites KPI cards, inserts YTD table rows as needed,
fills/creates native tables from the manifest, and replaces chart slots with
API-generated PNG charts.

WWT AUS monthly and Wightlink monthly are also enabled with their approved
template manifests. Olympic monthly and annual native Google Slides generation
remain disabled until matching approved template mappings are added.

## Wightlink QBR Template

Wightlink QBR native Slides generation is enabled on API Source Test using a
copied template deck, not the original example deck:

- Template: `https://docs.google.com/presentation/d/1BFtXTYytY_jmQrTgw1FZLQdxTSDH1nFtPEmOWFfF5lE/edit`
- Manifest: `docs/google_slides_templates/wightlink_qbr_test_template.json`

The Wightlink QBR flow uses API-generated GA4 performance data, DataForSEO
trend CSVs, manually uploaded Google and Microsoft Auction Insights for the same
quarter, and the Wightlink plan from Google Sheets. A manual plan CSV/XLSX
upload remains available as a fallback if Sheets access is unavailable.

Template-specific safeguards are applied during generation:

- dark header/title text is forced to white
- spend/cost comparison deltas are forced to grey
- trend and YTD comparison charts are line-only
- purchases/revenue charts are bar-only
- current-quarter auction insights combine Google and Microsoft uploads while
  keeping platform rows separate
