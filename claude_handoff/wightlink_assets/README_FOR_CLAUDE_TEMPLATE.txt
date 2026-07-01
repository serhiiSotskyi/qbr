Wightlink {{quarter_short}} QBR Deck Handoff

Goal
Transform the Streamlit output in this upload pack into a polished Wightlink QBR deck that matches the visual system, slide grammar, typography, tables, chart treatment, section dividers, footer, and overall look of the Wightlink reference deck:
{{reference_deck_url}}

Files in this pack
- report.txt: Source of truth for the current {{period_label}} report content, section order, numbers, tables, bullets, and period labels.
- wightlink_streamlit_output.pptx: Streamlit-generated PowerPoint. Use only to understand the generated content and chart intent. Do not use this as the visual target.
- reference_deck_exported_from_google_slides.pptx: PowerPoint export of the Wightlink Google Slides reference deck. Use this as the visual template if native Google Slides editing is unavailable.
- original_streamlit_prompt.txt: Original prompt used by Streamlit. Use only as background. Any generic design-system block should not override the Wightlink reference deck.
- SLIDE_MAPPING.csv: Target slide-by-slide mapping from the 27-slide Wightlink reference deck to the QBR source sections.
- SOURCE_SECTION_INDEX.txt: Index of report.txt sections and line numbers.
- RAW_INPUTS_MANIFEST.txt: Description of raw CSV inputs.
- QA_CHECKLIST.txt: Required checks before returning the completed deck.
- CHART_QA_ADDENDUM_FOR_CLAUDE.txt: Required chart rendering and visual QA rules. Use this for all chart slides.
- raw_inputs/: Source CSVs with stable names.
- PACKAGE_MANIFEST.json: Package metadata.

Non-negotiable target mode
- Final client deliverable must be suitable for native Google Slides.
- If you can edit Google Slides directly, copy the Wightlink Google Slides reference deck first and edit the copy.
- If native Google Slides editing is not available, edit reference_deck_exported_from_google_slides.pptx as an intermediate file and return a finished PPTX that can be imported into Google Slides.
- Preserve the Wightlink/Summon visual system from the reference deck.
- Default to the reference deck's 27-slide structure and order. Where source data does not exist, turn old content into clear manual placeholders rather than leaving stale content.
- Use report.txt as the primary source of truth for all current-quarter values and narrative.
- Use raw_inputs only for traceability, chart regeneration, and plan/forecast context where the reference layout requires it.
- Replace old period labels/content with {{period_label}}, except where a prior-period comparison is explicitly required.

Recommended workflow
1. Copy the Google Slides reference deck, or use reference_deck_exported_from_google_slides.pptx as the intermediate template.
2. Read report.txt, RAW_INPUTS_MANIFEST.txt, and SLIDE_MAPPING.csv.
3. Replace text and table content in existing reference objects wherever possible.
4. Generate clean chart images from report.txt/raw_inputs and replace existing chart/image slots.
5. Apply CHART_QA_ADDENDUM_FOR_CLAUDE.txt to every chart slide and inspect full-slide thumbnails/screenshots after chart replacement.
6. Convert unsupported old sections into manual placeholders.
7. Run QA_CHECKLIST.txt.
8. Return the copied Google Slides deck link if working natively, or the finished PPTX if using the intermediate bridge.
