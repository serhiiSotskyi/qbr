# User Guide

## What this tool does

This tool creates a first-draft quarterly PPC report package from your exported marketing files.

You upload your source files, click one button, and download a ZIP package containing:

- a PowerPoint file (`.pptx`)
- a text version of the same report (`report.txt`)
- a ready-to-use prompt for Claude (`prompt.txt`)

The PowerPoint is intentionally a rough first draft. The usual workflow is to generate the package here, then use Claude to turn that draft into a polished presentation.

## Before you start

Pick the correct client first, because the tool changes the report structure based on the selected client.

You will usually need:

- `Performance CSV` from your reporting source

You may also need:

- `Auction CSV` from Google Ads Auction Insights
- one or more `Trends CSVs` from Google Trends
- `Wightlink Plan Workbook` if you are generating a Wightlink report

## Step-by-step workflow

### 1. Open the app

Open the report generator and go to the main upload screen.

### 2. Choose the client

Use the `Client` dropdown and select the correct client.

This matters because each client has its own reporting structure and expected inputs.

### 3. Upload the required file

Upload the `Performance CSV`.

This file is required for every report. The tool will not run without it.

### 4. Upload any optional supporting files

If you have them, upload:

- `Auction CSV`
- `Trends CSVs`

If the selected client is `Wightlink`, also upload:

- `Wightlink Plan Workbook` (`.xlsx`)

### 5. Generate the files

Click `Generate Files`.

The tool will process the uploads and create a download package.

### 6. Download the ZIP package

Click `Download All Files`.

You will receive one ZIP file named like:

- `wightlink_package.zip`
- `wendy_wu_package.zip`
- `olympic_holidays_package.zip`

## What is inside the ZIP

The ZIP contains three files:

### 1. `[client]_report.pptx`

This is the generated PowerPoint draft.

It includes the structure, slide order, tables, charts, and reporting content, but it may still look plain or unfinished visually.

### 2. `report.txt`

This is the source-of-truth text version of the report.

It contains the same reporting content in text form. Claude should use this as the main content reference.

### 3. `prompt.txt`

This is the prompt you should copy into Claude.

It tells Claude how to transform the draft PowerPoint and `report.txt` into a more polished presentation without changing the structure, numbers, or content.

## How to use the output with Claude

After downloading and unzipping the package:

1. Open Claude.
2. Copy all text from `prompt.txt` and paste it into Claude.
3. Attach `report.txt`.
4. Attach `[client]_report.pptx`.
5. Send the message.

If you also have a previous deck, brand deck, or layout example for that client, you can attach that too as extra visual inspiration. It is helpful, but not required by this app.

## What to expect from the result

The tool is designed to give you a strong first draft, not a final client-ready deck.

Typical output flow:

1. Generate the ZIP package here.
2. Use Claude to redesign the presentation.
3. Review the result manually.
4. Make any final edits before sharing with the client.

## Client-specific notes

### Wightlink

For Wightlink, upload:

- `Performance CSV`
- `Auction CSV` if available
- `Trends CSVs` if available
- `Wightlink Plan Workbook`

### Wendy Wu / Wendy Wu Australia

For Wendy Wu reports, upload:

- `Performance CSV`
- `Auction CSV` if available
- `Trends CSVs` if available

### Olympic Holidays

For Olympic Holidays, upload:

- `Performance CSV`

You can also add:

- `Auction CSV` if available
- `Trends CSVs` if available

## Common mistakes to avoid

- Selecting the wrong client before uploading files
- Forgetting to upload the `Performance CSV`
- Uploading partial quarter data and expecting the current incomplete quarter to be used
- Treating the generated PowerPoint as the final polished version
- Sending only the `.pptx` to Claude without also attaching `report.txt`
- Forgetting to paste the contents of `prompt.txt` into Claude

## Quick version

If you just want the shortest possible instructions:

1. Pick the client.
2. Upload the `Performance CSV`.
3. Upload `Auction CSV`, `Trends CSVs`, and the Wightlink plan workbook if relevant.
4. Click `Generate Files`.
5. Download the ZIP.
6. Unzip it.
7. In Claude, paste `prompt.txt`, then attach `report.txt` and the generated `.pptx`.
8. Ask Claude to produce the polished presentation.
