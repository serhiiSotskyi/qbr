Olympic Holidays {{quarter_short}} PPC Report Deck Handoff

Goal
Transform the Streamlit output in this upload pack into a polished Olympic Holidays Google Slides-style report that matches the visual system, slide language, typography, chart/table treatment, footer, and overall look of this reference deck:
{{reference_deck_url}}

Files in this pack
- report.txt: Primary source of truth for all {{period_label}} content, numbers, tables, KPI cards, bullets, chart intent, and period labels.
- {{streamlit_pptx_filename}}: Streamlit-generated PowerPoint. Use only to understand data coverage and rough chart/content intent. Do not use this as the visual target.
- reference_deck_exported_from_google_slides.pptx: PowerPoint export of the Olympic Holidays Google Slides reference deck. Use this as the visual template if native Google Slides editing is unavailable.
- original_streamlit_prompt.txt: Original prompt used by Streamlit. Use as background only.
- SLIDE_MAPPING.csv: Target slide-by-slide mapping from the Olympic Holidays reference deck to the source sections.
- SOURCE_SECTION_INDEX.txt: Index of report.txt sections and line numbers.
- REFERENCE_DECK_OUTLINE.txt: Human-readable reference deck outline.
- QA_CHECKLIST.txt: Required checks before returning the completed deck.
- CHART_QA_ADDENDUM_FOR_CLAUDE.txt: Required chart/table rendering and visual QA rules.
- INPUT_FILES_MANIFEST.txt: Source file roles and priority.
- PACKAGE_MANIFEST.json: Package metadata.
- source_data/: Raw uploaded CSVs used by the Streamlit app. These are audit/backup sources; report.txt remains primary.

Non-negotiable target mode
- Final client deliverable must be suitable for native Google Slides.
- If you can edit Google Slides directly, copy the Google Slides reference deck first and edit the copy.
- If native Google Slides editing is not available, edit reference_deck_exported_from_google_slides.pptx as an intermediate file and return a finished PPTX that can be imported into Google Slides.
- Do not edit the original reference deck.
- Use the Olympic Holidays reference deck as the visual template.
- Use report.txt as the source of truth for {{period_label}} values.
- Replace old period content with {{period_label}} content.
- Preserve the Olympic Holidays/Summon visual system from the reference deck.
- Do not invent sections that are not present in report.txt.
- Do not silently drop source sections from report.txt. Every report.txt section must be represented in the final deck.
- Keep the final deck to the 11-slide reference structure unless the user explicitly asks to extend it.

Specific source notes
- Google Trends section titles in report.txt may be file-name based. Retitle them using the source_data CSV headers:
  - {{brand_trend_file}} = {{trend_brand_display_name}}
  - {{category_trend_file}} = {{trend_category_display_name}}
- If the category trend source is not "Holidays to Greece", update slide 3's title to the actual category source from the report/CSV.
- Raw CSVs are secondary. Use them only for audit/context or to regenerate charts when report.txt does not expose enough chart detail.

Recommended workflow
1. Copy the Google Slides reference deck, or use reference_deck_exported_from_google_slides.pptx as the intermediate template if direct Google Slides editing is unavailable.
2. Read report.txt and use SLIDE_MAPPING.csv as the execution map.
3. Use REFERENCE_DECK_OUTLINE.txt to understand the visual template.
4. Replace text and table content in existing objects wherever possible.
5. Generate chart/table images only where necessary; otherwise keep editable tables and text.
6. Apply CHART_QA_ADDENDUM_FOR_CLAUDE.txt to trend, chart, table, and KPI-heavy slides and inspect full-slide thumbnails/screenshots after replacement.
7. Turn unsupported source gaps into concise human-review placeholders only when a reference slide cannot be populated from report.txt.
8. Run QA_CHECKLIST.txt.
9. Return the copied Google Slides deck link if working natively, or the finished PPTX if using the intermediate bridge.
