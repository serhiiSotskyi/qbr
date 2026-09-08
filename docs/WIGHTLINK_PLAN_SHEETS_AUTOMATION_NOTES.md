# Wightlink Plan Sheets Automation Notes

This note captures the Google Sheets access approach used to automate the
Wightlink plan workbook input. It intentionally does not include credential
values.

## Goal

Automatically pull the Wightlink plan workbook/table from Google Sheets for the
API Source Test workflow, save it into the request source files, pass it to the
existing Wightlink pipeline as `plan_workbook`, and include it in the source
generation manifest used by the PPTX, handoff, and native Google Slides paths.

## Scope

The implemented path reuses the Google Workspace OAuth client and refresh token
already used for native Google Slides. That refresh token must include Sheets
read access. The minimum scope for the Sheets read is:

- `https://www.googleapis.com/auth/spreadsheets.readonly`

Add Drive read scope only if the implementation needs Drive lookup/export rather than direct Sheets API range reads:

- `https://www.googleapis.com/auth/drive.readonly`

The existing GA4/Google Ads refresh token is not sufficient because it was created for:

- `https://www.googleapis.com/auth/analytics.readonly`
- `https://www.googleapis.com/auth/adwords`

## Env Names

- `GOOGLE_WORKSPACE_OAUTH_CLIENT_ID`
- `GOOGLE_WORKSPACE_OAUTH_CLIENT_SECRET`
- `GOOGLE_WORKSPACE_OAUTH_REFRESH_TOKEN`
- `GOOGLE_WORKSPACE_OAUTH_TOKEN_URL=https://oauth2.googleapis.com/token`
- `WIGHTLINK_PLAN_SPREADSHEET_ID=18w3DWmDtJ5plGWuzHjGNnnQ-M-uvyCrhIBKeaSTOl9Q`
- `WIGHTLINK_PLAN_SHEET_GID=596378878`
- `WIGHTLINK_PLAN_RANGE=A1:AB1000`

The Wightlink plan spreadsheet ID, gid, and range have built-in defaults matching
the approved plan sheet, so they only need to be set if the source sheet moves.

## Read Method

Use the native Google Sheets API:

`GET https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}/values/{range}`

Header:

`Authorization: Bearer {access_token}`

Implemented Python flow:

- refresh access token through `GoogleWorkspaceClient`
- resolve the tab title from the configured sheet gid
- fetch values with the native Sheets API
- write the result to `source_data/wightlink_plan_google_sheet.csv`
- record the generated CSV in `source_generation_manifest.json`

## OAuth Generation Notes

OAuth URL settings from the working connector reference:

- auth URL: `https://accounts.google.com/o/oauth2/v2/auth`
- token URL: `https://oauth2.googleapis.com/token`
- `access_type=offline`
- `prompt=consent`
- `include_granted_scopes=false`

The working connector used user OAuth under `serge@summon.co`, not a service account. The sheet must be accessible to that user.

## Integration Notes

For `client_id == "wightlink"` on the API Source Test page:

- if Sheets credentials and Wightlink plan env vars are configured, pull the plan automatically
- keep manual upload as fallback when Sheets credentials are missing
- save raw Sheets API JSON and normalized CSV under the request/source folder
- include the generated plan CSV in the source-generation manifest
- use the same plan CSV for Wightlink QBR PPTX, handoff package, and native Google Slides generation
