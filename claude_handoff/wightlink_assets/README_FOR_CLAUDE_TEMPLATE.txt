Wightlink {{quarter_short}} PPC Report Deck Handoff

V2 note
This handoff may include the July 2026 Wightlink QBR V2 requirements: YTD trend comparison charts, quarter-only Red Funnel Auction Insights, additional YTD monthly breakdown slides, and Plan comparison lines on KPI cards. Use UPDATED_SLIDE_MAPPING_WIGHTLINK_QBR_V2_TEMPLATE.csv and QA_CHECKLIST_V2_ADDENDUM.txt when included.

Goal
Transform the Streamlit output in this upload pack into a polished Wightlink Google Slides-style report that matches the visual system, slide language, typography, chart treatment, table styling, footer, and overall look of this reference deck:
{{reference_deck_url}}

Files in this pack
- report.txt: Primary source of truth for all {{period_label}} content, numbers, tables, KPI cards, bullets, chart intent, and period labels.
- {{streamlit_pptx_filename}}: Streamlit-generated PowerPoint. Use only to understand data coverage and rough chart/content intent. Do not use this as the visual target.
- reference_deck_exported_from_google_slides.pptx: PowerPoint export of the Wightlink Google Slides reference deck. Use this as the visual template if native Google Slides editing is unavailable.
- original_streamlit_prompt.txt: Original prompt used by Streamlit. Use as background only.
- SLIDE_MAPPING.csv: Target slide-by-slide mapping from the Wightlink reference deck to the source sections.
- SOURCE_SECTION_INDEX.txt: Index of report.txt sections and line numbers.
- REFERENCE_DECK_OUTLINE.txt: Human-readable reference deck outline.
- QA_CHECKLIST.txt: Required checks before returning the completed deck.
- CHART_QA_ADDENDUM_FOR_CLAUDE.txt: Required chart rendering and visual QA rules.
- INPUT_FILES_MANIFEST.txt: Source file roles and priority.
- PACKAGE_MANIFEST.json: Package metadata.
- source_data/: Raw uploaded CSVs used by the Streamlit app. These are audit/backup sources; report.txt remains primary.
- UPDATED_SLIDE_MAPPING_WIGHTLINK_QBR_V2_TEMPLATE.csv: V2 target slide order when included.
- QA_CHECKLIST_V2_ADDENDUM.txt: Extra V2 checks when included.
- GOOGLE_TRENDS_YTD_COMPARISON_RULES.txt, PLAN_COMPARISON_RULES.txt, AUCTION_INSIGHTS_REDFUNNEL_QUARTER_RULES.txt, YTD_MONTHLY_BREAKDOWN_RULES.txt: Detailed V2 implementation rules when included.

Non-negotiable target mode
- Final client deliverable must be suitable for native Google Slides.
- If you can edit Google Slides directly, copy the Google Slides reference deck first and edit the copy.
- If native Google Slides editing is not available, edit reference_deck_exported_from_google_slides.pptx as an intermediate file and return a finished PPTX that can be imported into Google Slides.
- Do not edit the original reference deck.
- Use the Wightlink reference deck as the visual template.
- Use report.txt as the source of truth for {{period_label}} values.
- Replace old period content with {{period_label}} content.
- Preserve the Wightlink/Summon visual system from the reference deck.
- Do not invent sections that are not present in report.txt.
- Do not silently drop source sections from report.txt. Every report.txt section must be represented in the final deck.
- For V2 packages, keep the deck quarter-led overall, but update trend slides to YTD comparison and add the specified YTD/Red Funnel slides.

Specific source notes
- Google Trends section titles in report.txt may be file-name based. Retitle them using the source_data CSV headers:
  - {{trend_1_file}} = {{trend_1_display_name}}
  - {{trend_2_file}} = {{trend_2_display_name}}
  - {{trend_3_file}} = {{trend_3_display_name}}
- The plan CSV is secondary. Use it only for audit/context unless plan comparison values are explicitly present in report.txt.
- Missing reference sections should become clear human-review placeholders.

Recommended workflow
1. Copy the Google Slides reference deck, or use reference_deck_exported_from_google_slides.pptx as the intermediate template if direct Google Slides editing is unavailable.
2. Read report.txt and use SLIDE_MAPPING.csv as the execution map.
3. Use REFERENCE_DECK_OUTLINE.txt to understand the visual template.
4. Replace text and table content in existing objects wherever possible.
5. Generate chart PNGs from report.txt/source_data values and replace existing chart/image slots.
6. Apply CHART_QA_ADDENDUM_FOR_CLAUDE.txt to every chart slide and inspect full-slide thumbnails/screenshots after chart replacement.
7. Turn unsupported reference sections into polished placeholders.
8. Run QA_CHECKLIST.txt.
9. Return the copied Google Slides deck link if working natively, or the finished PPTX if using the intermediate bridge.
