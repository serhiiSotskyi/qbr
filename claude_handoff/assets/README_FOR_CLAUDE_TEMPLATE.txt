{{client_display_name}} {{quarter_short}} Report Deck Handoff

Goal
Transform the Streamlit output in this upload pack into a native Google Slides report that matches this reference deck's structure, styling, slide order, typography, chart treatment, tables, footer, and overall look:
{{reference_deck_url}}

Files in this pack
- report.txt: Source of truth for all {{period_label}} content, numbers, tables, bullets, period labels, auction insights, Google Trends, and recommendations.
- {{streamlit_pptx_filename}}: Streamlit-generated PowerPoint. Use only to understand data coverage and rough chart/content intent. Do not use this as the visual target.
- reference_deck_exported_from_google_slides.pptx: PowerPoint export of the Google Slides reference deck. Use this as the visual template if native Google Slides editing is unavailable.
- original_streamlit_prompt.txt: Original prompt used by Streamlit. Use only as background. It is not the final instruction because this task requires matching the Google Slides reference deck.
- SLIDE_MAPPING.csv: Target slide-by-slide mapping from the 38-slide Google Slides reference deck to the source sections.
- SOURCE_SECTION_INDEX.txt: Index of the report.txt sections and line numbers.
- QA_CHECKLIST.txt: Required checks before returning the completed deck.
- CHART_QA_ADDENDUM_FOR_CLAUDE.txt: Required chart rendering and visual QA rules. Use this for all chart slides.
- PACKAGE_MANIFEST.json: Package metadata.

Non-negotiable target mode
- Final client deliverable must be suitable for native Google Slides.
- If you can edit Google Slides directly, copy the Google Slides reference deck first and edit the copy.
- If native Google Slides editing is not available, edit reference_deck_exported_from_google_slides.pptx as an intermediate file and return a finished PPTX that can be imported into Google Slides.
- Do not edit the original reference deck.
- Keep the reference deck's 38-slide structure and order.
- Do not add extra slides just because report.txt has extra sections.
- Use report.txt as the source of truth for all {{period_label}} values.
- Replace all old quarter labels/content with {{period_label}} content.
- Use "{{client_display_name}}" where the client/market name appears.
- Preserve the Summon/Wendy Wu visual system from the reference deck.
- Remove or replace stale prior-quarter content. No old testing, updates, or next steps should remain unless intentionally turned into placeholders.

Specific folding rules
- Performance "Other Summary" and "Other Monthly Trend" should not create extra channel slides. Their values are represented in Campaign Type Mix as the "Other" row.
- Destination campaign mix sections such as "China Campaign Mix", "Japan Campaign Mix", etc. should be folded into the matching destination summary slides.
- Destination campaign mix tables must preserve inline YoY values in Cost, Sales Leads, Cost Share, Lead Share, and CPL cells when report.txt includes them.
- For UK reports, Central Asia & Mongolia is a core destination. Do not roll Central Asia or Mongolia into destination Other.
- Destination "Other Summary + YoY", "Other Monthly Trend", and "Other Campaign Mix" should populate the reference deck's destination Other area.
- "Other (Destination) Top 10 campaigns" should be used only when the report includes uploaded Google/MS campaign export data; the Other definition excludes Brand, Japan, China, India, SE Asia, Vietnam, Cambodia, Thailand, Malaysia/Borneo, Central Asia, and Mongolia.
- Google Trends slides should show current YTD versus previous YTD when both series are present. Treat separate Google Trends exports as normalized index comparisons, not exact search-volume deltas.
- "Recommendations / Next Steps" should populate the reference deck's Next Steps slides.
- Testing and Other Updates should be manual placeholders unless report.txt contains those sections.

Source consistency note
If narrative bullets differ slightly from KPI and Total rows, use the Key Metrics and Total rows as authoritative for KPI blocks, tables, chart data, and headline numbers.

Recommended workflow
1. Copy the Google Slides reference deck, or use reference_deck_exported_from_google_slides.pptx as the intermediate template if direct Google Slides editing is unavailable.
2. Read report.txt and use SLIDE_MAPPING.csv as the execution map.
3. Replace text and table content in existing objects wherever possible.
4. Generate chart PNGs from report.txt data and replace existing chart/image slots.
5. Apply CHART_QA_ADDENDUM_FOR_CLAUDE.txt to every chart slide and inspect full-slide thumbnails/screenshots after chart replacement.
6. Keep manual placeholder slides visually consistent with the template.
7. Run QA_CHECKLIST.txt.
8. Return the copied Google Slides deck link if working natively, or the finished PPTX if using the intermediate bridge.
