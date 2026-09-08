from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from report_generator.pipelines.olympic_pipeline import _parse_olympic_dates

from .data_loader import MonthInfo, detect_latest_complete_month
from .google_slides_builder import (
    DriveChartAssetStore,
    GoogleSlidesGenerationResult,
    share_copied_presentation,
)
from .google_slides_templates import TemplateConfig
from .google_workspace import (
    PDF_MIME_TYPE,
    GoogleWorkspaceClient,
    GoogleWorkspaceConfig,
)
from .monthly_google_slides_builder import (
    _read_json,
    _resolve_performance_csv_path,
    _send_batch_updates,
    _table_cell_text_by_id,
    _table_cell_text_requests,
    _table_dimensions_by_id,
    _write_json,
)

import matplotlib

matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OLYMPIC_MONTHLY_TEMPLATE_MANIFEST = (
    PROJECT_ROOT
    / "docs"
    / "google_slides_templates"
    / "olympic_holidays_monthly_test_template.json"
)
BATCH_UPDATE_CHUNK_SIZE = 400
TABLE_FONT_SIZE_PT = 6
CAMPAIGN_TABLE_FONT_SIZE_PT = 5.5
TABLE_ROW_HEIGHT_EMU = 95000
HEADER_FILL_RGB = {"red": 0.09, "green": 0.09, "blue": 0.09}
WHITE_RGB = {"red": 1.0, "green": 1.0, "blue": 1.0}
BLACK_RGB = {"red": 0.0, "green": 0.0, "blue": 0.0}
NEUTRAL_RGB = {"red": 0.42, "green": 0.42, "blue": 0.42}
MUTED_ON_DARK_RGB = {"red": 0.78, "green": 0.78, "blue": 0.78}
POSITIVE_RGB = {"red": 0.03, "green": 0.47, "blue": 0.22}
NEGATIVE_RGB = {"red": 0.78, "green": 0.16, "blue": 0.13}
PRIMARY_RGB = {"red": 0.06, "green": 0.16, "blue": 0.30}
OLYMPIC_BLUE = "#102A4A"
OLYMPIC_RED = "#C7362F"
OLYMPIC_GREY = "#B6B2AD"
CAMPAIGN_ORDER = ("Brand", "Generic", "Performance Max", "Demand Gen", "Other")
MONTHLY_TABLE_COLUMNS = (
    "Month",
    "Revenue",
    "Cost",
    "Purchases",
    "Add to Cart",
    "CPA",
    "Cost/ATC",
    "AOV",
)
OVERALL_TABLE_COLUMNS = ("Month", "Revenue", "Spend", "Purchases", "CPA", "Cost/ATC")
CAMPAIGN_TABLE_COLUMNS = (
    "Channel",
    "Revenue",
    "Cost",
    "Purchases",
    "Add to Cart",
    "CPA",
    "Cost/ATC",
    "AOV",
    "Cost %",
    "Rev %",
)
ISLAND_TABLE_COLUMNS = (
    "Island",
    "Impr.",
    "Clicks",
    "CTR",
    "Avg. CPC",
    "Cost",
    "Conversions",
    "Conv. Rate",
    "CPA",
    "Cost Share %",
)
CARD_METRICS = ("revenue", "purchases", "cpa", "cost", "aov", "cpatc")
CARD_SUBLABELS = {
    "revenue": "Selected month revenue",
    "purchases": "Selected month purchases",
    "cpa": "Cost per purchase",
    "cost": "Selected month spend",
    "aov": "Avg order value",
    "cpatc": "Cost per add-to-cart",
}


def generate_olympic_monthly_google_slides(
    *,
    client_id: str,
    client_name: str,
    request_dir: str | Path,
    report_artifacts_path: str | Path,
    template: TemplateConfig,
    workspace_config: GoogleWorkspaceConfig,
    google_client: GoogleWorkspaceClient | None = None,
    export_pdf: bool = True,
) -> GoogleSlidesGenerationResult:
    request_path = Path(request_dir)
    outputs_dir = request_path / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = Path(report_artifacts_path)
    manifest_path = outputs_dir / "google_slides_generation_manifest.json"

    base_manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "client_id": client_id,
        "client_name": client_name,
        "report_mode": "monthly",
        "builder": "olympic_holidays_monthly_template_manifest",
        "report_artifacts": str(artifact_path),
        "template_id": template.template_id,
        "template_key": template.key,
        "chart_assets": [],
        "permission_cleanup": [],
        "output_sharing": [],
        "warnings": [],
    }

    artifact = _read_json(artifact_path)
    template_manifest = _read_json(OLYMPIC_MONTHLY_TEMPLATE_MANIFEST)
    client = google_client or GoogleWorkspaceClient(workspace_config)
    asset_store: DriveChartAssetStore | None = None
    copied_id: str | None = None
    copied_url: str | None = None
    qa_pdf_path: Path | None = None
    warnings: list[str] = []
    output_sharing: list[dict[str, Any]] = []
    status = "success"
    message = "Native Olympic Holidays monthly Google Slides deck generated."
    batch_update_request_count = 0

    try:
        payload = build_olympic_monthly_slides_payload(
            request_dir=request_path,
            artifact=artifact,
            template_manifest=template_manifest,
        )
        warnings.extend(payload.get("warnings") or [])
        title = _output_deck_title(client_name, payload["period"]["label"])
        copied = client.copy_file(
            template.template_id, title, workspace_config.output_folder_id
        )
        copied_id = str(copied["id"])
        copied_url = f"https://docs.google.com/presentation/d/{copied_id}/edit"
        output_sharing = share_copied_presentation(
            client, copied_id, template.template_id, warnings
        )
        presentation = client.get_presentation(copied_id)
        asset_store = DriveChartAssetStore(
            client, str(workspace_config.asset_folder_id)
        )

        shape_text = _shape_text_by_id(presentation)
        table_dimensions = _table_dimensions_by_id(presentation)
        table_cell_text = _table_cell_text_by_id(presentation)

        requests_body: list[dict[str, Any]] = []
        requests_body.extend(
            _build_shape_text_requests(
                payload["shape_text"], shape_text, warnings=warnings
            )
        )
        requests_body.extend(
            _build_table_requests(
                payload["tables"], table_dimensions, table_cell_text, warnings=warnings
            )
        )
        uploaded_assets = _upload_chart_assets(asset_store, payload["charts"])
        requests_body.extend(
            _build_chart_requests(
                payload["charts"], uploaded_assets, presentation, warnings=warnings
            )
        )
        requests_body.extend(_build_text_style_requests(payload))

        batch_update_request_count = len(requests_body)
        _send_batch_updates(client, copied_id, requests_body)

        if export_pdf:
            try:
                qa_pdf_path = client.export_file(
                    copied_id, PDF_MIME_TYPE, outputs_dir / "google_slides_qa.pdf"
                )
            except (
                Exception
            ) as exc:  # noqa: BLE001 - PDF export is useful QA, not required
                warnings.append(f"QA PDF export failed: {exc}")
    except (
        Exception
    ) as exc:  # noqa: BLE001 - keep PPTX/package output usable if Slides fails
        status = "failed"
        message = f"Native Olympic Holidays monthly Google Slides generation failed: {exc}"
    finally:
        cleanup_records = (
            asset_store.cleanup_public_permissions() if asset_store else []
        )
        manifest = {
            **base_manifest,
            "status": status,
            "message": message,
            "copied_presentation_id": copied_id,
            "google_slides_url": copied_url,
            "batch_update_request_count": batch_update_request_count,
            "chart_assets": (
                [asset.to_manifest() for asset in asset_store.assets]
                if asset_store
                else []
            ),
            "permission_cleanup": cleanup_records,
            "output_sharing": output_sharing,
            "qa_pdf_path": str(qa_pdf_path) if qa_pdf_path else None,
            "warnings": warnings,
        }
        _write_json(manifest_path, manifest)

    return GoogleSlidesGenerationResult(
        enabled=True,
        status=status,
        message=message,
        google_slides_url=copied_url,
        presentation_id=copied_id,
        manifest_path=manifest_path,
        artifact_path=artifact_path,
        qa_pdf_path=qa_pdf_path,
        warnings=warnings,
        output_sharing=output_sharing,
    )


def build_olympic_monthly_slides_payload(
    *,
    request_dir: str | Path,
    artifact: dict[str, Any],
    template_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_path = Path(request_dir)
    manifest = template_manifest or _read_json(OLYMPIC_MONTHLY_TEMPLATE_MANIFEST)
    performance_csv = _resolve_performance_csv_path(request_path, artifact)
    data = _prepare_olympic_monthly_data(performance_csv, request_path)
    period = data["period"]
    slides = manifest["slides"]
    global_ids = manifest["global_text_ids"]

    shape_text: dict[str, str] = {
        global_ids["cover_report_title"]: f"Monthly {period['year']} Performance Review",
        global_ids["cover_period"]: period["label"],
        global_ids["cover_revenue"]: _currency(data["overall"]["current"]["revenue"]),
        global_ids["cover_purchases"]: _count(data["overall"]["current"]["purchases"]),
        global_ids["cover_cpa"]: _currency(data["overall"]["current"]["cpa"]),
        slides["overall_cards"]["title_id"]: f"Overall Performance {period['month_short']}",
        slides["overall_cards"]["subtitle_id"]: f"Olympic Holidays | {period['label']}",
        slides["overall_cards"]["insights_id"]: "\n".join(
            _summary_insights("Overall", data["overall"], period)
        ),
        slides["overall_ytd"]["title_id"]: "Overall Performance",
        slides["overall_ytd"]["subtitle_id"]: f"Olympic Holidays | {period['ytd_label']}",
        slides["yoy"]["title_id"]: "YoY YTD Comparison",
        slides["yoy"]["subtitle_id"]: "Current YTD versus same period last year",
        slides["yoy"]["coverage_id"]: (
            f"Full coverage: {period['matched_months']} of {period['selected_month']} "
            "months matched against the prior-year YTD period."
        ),
        slides["yoy"]["insights_id"]: "\n".join(_yoy_insights(data, period)),
        slides["campaign_mix"]["title_id"]: "Campaign Type Performance",
        slides["campaign_mix"]["subtitle_id"]: f"Cost and revenue share | {period['ytd_label']}",
        slides["campaign_mix"]["insights_id"]: "\n".join(
            _campaign_mix_insights(data["campaign_mix"])
        ),
        slides["generic_cards"]["title_id"]: "Generic Performance",
        slides["generic_cards"]["subtitle_id"]: f"Olympic Holidays | {period['label']}",
        slides["generic_cards"]["insights_id"]: "\n".join(
            _summary_insights("Generic", data["sections"]["Generic"], period)
        ),
        slides["generic_ytd"]["title_id"]: "Generic Performance",
        slides["generic_ytd"]["subtitle_id"]: f"Olympic Holidays | {period['ytd_label']}",
        slides["generic_ytd"]["insights_id"]: "\n".join(
            _ytd_insights("Generic", data["sections"]["Generic"], period)
        ),
        slides["pmax_cards"]["title_id"]: "Performance Max Performance",
        slides["pmax_cards"]["subtitle_id"]: f"Olympic Holidays | {period['label']}",
        slides["pmax_cards"]["insights_id"]: "\n".join(
            _summary_insights("Performance Max", data["sections"]["Performance Max"], period)
        ),
        slides["pmax_ytd"]["title_id"]: "Performance Max Performance",
        slides["pmax_ytd"]["subtitle_id"]: f"Olympic Holidays | {period['ytd_label']}",
        slides["pmax_ytd"]["insights_id"]: "\n".join(
            _ytd_insights("Performance Max", data["sections"]["Performance Max"], period)
        ),
        slides["island_destination"]["title_id"]: "Island Performance by Destination",
        slides["island_destination"]["subtitle_id"]: f"Review required | {period['label']}",
        slides["island_insights"]["title_id"]: "Island Performance - Key Insights",
        slides["island_insights"]["subtitle_id"]: f"Olympic Holidays | {period['label']}",
        slides["island_insights"]["insights_id"]: (
            "Review required: monthly API performance data does not include the "
            "island search-term or destination-level source needed for this slide.\n"
            "The generator clears the template example values so stale Q2 figures are not reused."
        ),
    }

    for card_key, section_name in (
        ("overall_cards", "overall"),
        ("generic_cards", "Generic"),
        ("pmax_cards", "Performance Max"),
        ("island_cards", "Island Hopping"),
    ):
        section = data["overall"] if section_name == "overall" else data["sections"][section_name]
        shape_text.update(
            _card_shape_text(
                slides[card_key]["kpi_ids"],
                section,
                period,
                missing_label=(
                    "No Island Hopping campaign type is present in the monthly performance CSV."
                    if section_name == "Island Hopping" and section["missing"]
                    else None
                ),
            )
        )

    if data["sections"]["Island Hopping"]["missing"]:
        shape_text.update(
            {
                slides["island_cards"]["title_id"]: "Island Hopping Performance",
                slides["island_cards"]["subtitle_id"]: f"Review required | {period['label']}",
                slides["island_cards"]["insights_id"]: (
                    "No Island Hopping campaign type is present in the API-generated "
                    "Olympic monthly performance CSV.\n"
                    "This slide has been cleared to avoid carrying forward example Q2 values."
                ),
                slides["island_ytd"]["title_id"]: "Island Hopping Performance",
                slides["island_ytd"]["subtitle_id"]: f"Review required | {period['ytd_label']}",
                slides["island_ytd"]["insights_id"]: (
                    "No Island Hopping monthly performance rows were available.\n"
                    "Add a dedicated source before using this section for client-ready commentary."
                ),
            }
        )
    else:
        shape_text.update(
            {
                slides["island_cards"]["title_id"]: "Island Hopping Performance",
                slides["island_cards"]["subtitle_id"]: f"Olympic Holidays | {period['label']}",
                slides["island_cards"]["insights_id"]: "\n".join(
                    _summary_insights("Island Hopping", data["sections"]["Island Hopping"], period)
                ),
                slides["island_ytd"]["title_id"]: "Island Hopping Performance",
                slides["island_ytd"]["subtitle_id"]: f"Olympic Holidays | {period['ytd_label']}",
                slides["island_ytd"]["insights_id"]: "\n".join(
                    _ytd_insights("Island Hopping", data["sections"]["Island Hopping"], period)
                ),
            }
        )

    tables = {
        slides["overall_ytd"]["table_id"]: {
            "values": _monthly_table_values(data["overall"]["ytd_monthly"], overall=True),
            "font_size": TABLE_FONT_SIZE_PT,
        },
        slides["yoy"]["table_id"]: {
            "values": _yoy_table_values(data["yoy"], period),
            "font_size": TABLE_FONT_SIZE_PT,
        },
        slides["campaign_mix"]["table_id"]: {
            "values": _campaign_mix_table_values(data["campaign_mix"]),
            "font_size": CAMPAIGN_TABLE_FONT_SIZE_PT,
        },
        slides["generic_ytd"]["table_id"]: {
            "values": _monthly_table_values(data["sections"]["Generic"]["ytd_monthly"]),
            "font_size": TABLE_FONT_SIZE_PT,
        },
        slides["pmax_ytd"]["table_id"]: {
            "values": _monthly_table_values(data["sections"]["Performance Max"]["ytd_monthly"]),
            "font_size": TABLE_FONT_SIZE_PT,
        },
        slides["island_ytd"]["table_id"]: {
            "values": _monthly_table_values(data["sections"]["Island Hopping"]["ytd_monthly"]),
            "font_size": TABLE_FONT_SIZE_PT,
        },
        slides["island_destination"]["table_id"]: {
            "values": [list(ISLAND_TABLE_COLUMNS), ["No API source", "", "", "", "", "", "", "", "", ""]],
            "font_size": TABLE_FONT_SIZE_PT,
        },
    }

    charts = {
        slides["overall_ytd"]["chart_ids"]["revenue_cpa"]: data["overall"]["charts"]["revenue_cpa"],
        slides["overall_ytd"]["chart_ids"]["purchases_cpatc"]: data["overall"]["charts"]["purchases_cpatc"],
        slides["yoy"]["chart_ids"]["yoy_summary"]: data["charts"]["yoy_summary"],
        slides["campaign_mix"]["chart_ids"]["cost_share"]: data["charts"]["cost_share"],
        slides["campaign_mix"]["chart_ids"]["revenue_share"]: data["charts"]["revenue_share"],
        slides["generic_ytd"]["chart_ids"]["revenue_cpa"]: data["sections"]["Generic"]["charts"]["revenue_cpa"],
        slides["generic_ytd"]["chart_ids"]["purchases_cpatc"]: data["sections"]["Generic"]["charts"]["purchases_cpatc"],
        slides["pmax_ytd"]["chart_ids"]["revenue_cpa"]: data["sections"]["Performance Max"]["charts"]["revenue_cpa"],
        slides["pmax_ytd"]["chart_ids"]["purchases_cpatc"]: data["sections"]["Performance Max"]["charts"]["purchases_cpatc"],
        slides["island_ytd"]["chart_ids"]["revenue_cpa"]: data["sections"]["Island Hopping"]["charts"]["revenue_cpa"],
        slides["island_ytd"]["chart_ids"]["purchases_cpatc"]: data["sections"]["Island Hopping"]["charts"]["purchases_cpatc"],
    }

    return {
        "period": period,
        "performance_csv": str(performance_csv),
        "shape_text": shape_text,
        "tables": tables,
        "charts": charts,
        "data_status": data["data_status"],
        "warnings": data["warnings"],
    }


def _prepare_olympic_monthly_data(
    performance_csv: str | Path, request_dir: Path
) -> dict[str, Any]:
    df = _load_olympic_performance_csv(performance_csv)
    month = detect_latest_complete_month(df)
    current = _filter_month(df, month)
    previous = _filter_month(df, month.previous_month)
    prior = _filter_month(df, MonthInfo(month.year - 1, month.month))
    ytd = _filter_ytd(df, month.year, month.month)
    prior_ytd = _filter_ytd(df, month.year - 1, month.month)
    matched_months = int(
        len(
            set(ytd["month_start"].dropna().dt.month.astype(int))
            & set(prior_ytd["month_start"].dropna().dt.month.astype(int))
        )
    )

    charts_dir = request_dir / "outputs" / "native_google_slides_charts" / "olympic_monthly"
    charts_dir.mkdir(parents=True, exist_ok=True)

    overall = _section_data(
        label="Overall",
        current=current,
        previous=previous,
        prior=prior,
        ytd=ytd,
        prior_ytd=prior_ytd,
        charts_dir=charts_dir,
        chart_key="overall",
    )
    sections = {
        "Generic": _campaign_section_data(
            "Generic", df, month, charts_dir, "generic"
        ),
        "Performance Max": _campaign_section_data(
            "Performance Max", df, month, charts_dir, "pmax"
        ),
        "Island Hopping": _campaign_section_data(
            "Island Hopping", df, month, charts_dir, "island_hopping"
        ),
    }
    campaign_mix = _campaign_mix(ytd)
    period = {
        "label": month.label,
        "subtitle": _monthly_period_subtitle(month),
        "ytd_label": f"YTD Jan - {month.start.strftime('%b %Y')}",
        "prior_ytd_label": f"YTD Jan - {month.start.strftime('%b')} {month.year - 1}",
        "start": month.start.strftime("%Y-%m-%d"),
        "end": month.end.strftime("%Y-%m-%d"),
        "month_short": month.start.strftime("%b"),
        "year": month.year,
        "selected_month": month.month,
        "matched_months": matched_months,
    }
    yoy = _yoy_summary(ytd, prior_ytd)
    charts = {
        "cost_share": _plot_share_donut(
            charts_dir / "campaign_cost_share.png",
            campaign_mix,
            "cost",
            "Cost Share",
        ),
        "revenue_share": _plot_share_donut(
            charts_dir / "campaign_revenue_share.png",
            campaign_mix,
            "revenue",
            "Revenue Share",
        ),
        "yoy_summary": _plot_yoy_summary(
            charts_dir / "yoy_summary.png", yoy, period
        ),
    }
    warnings: list[str] = []
    if sections["Island Hopping"]["missing"]:
        warnings.append(
            "Island Hopping campaign type was not present in the Olympic monthly performance CSV; island slides were cleared and marked review-required."
        )

    return {
        "period": period,
        "overall": overall,
        "sections": sections,
        "campaign_mix": campaign_mix,
        "yoy": yoy,
        "charts": charts,
        "data_status": {
            "performance_csv": str(performance_csv),
            "selected_month": month.label,
            "tables_and_charts_period": period["ytd_label"],
            "cards_period": month.label,
        },
        "warnings": warnings,
    }


def _load_olympic_performance_csv(path: str | Path) -> pd.DataFrame:
    source = pd.read_csv(path)
    rename = {
        _normalise_column(column): column for column in source.columns
    }
    aliases = {
        "date": "date",
        "campaign type": "campaign_type",
        "campaign_type": "campaign_type",
        "purchases": "purchases",
        "revenue": "revenue",
        "purchase revenue": "revenue",
        "purchase_revenue": "revenue",
        "cost": "cost",
        "spend": "cost",
        "add to cart": "add_to_cart",
        "add_to_cart": "add_to_cart",
        "adds to cart": "add_to_cart",
        "cpa": "cpa",
        "cost per atc": "cpatc",
        "cost_per_atc": "cpatc",
        "aov": "aov",
    }
    working = source.rename(
        columns={original: aliases[key] for key, original in rename.items() if key in aliases}
    )
    required = {"date", "campaign_type", "purchases", "revenue", "cost", "add_to_cart"}
    missing = required - set(working.columns)
    if missing:
        raise ValueError(f"Olympic monthly performance CSV missing columns: {sorted(missing)}")

    working = working.copy()
    working["date"] = _parse_olympic_dates(working["date"])
    working = working.dropna(subset=["date"]).copy()
    if working.empty:
        raise ValueError("Olympic monthly performance CSV has no valid dates.")

    for column in ("purchases", "revenue", "cost", "add_to_cart", "cpa", "cpatc", "aov"):
        if column not in working.columns:
            working[column] = 0.0
        working[column] = _to_number(working[column])
    working["campaign_type"] = (
        working["campaign_type"].fillna("Other").astype(str).str.strip()
    )
    working.loc[working["campaign_type"].eq(""), "campaign_type"] = "Other"
    working["month_start"] = working["date"].dt.to_period("M").dt.to_timestamp()
    working["year"] = working["date"].dt.year
    working["month"] = working["date"].dt.month
    return working.sort_values("date").reset_index(drop=True)


def _campaign_section_data(
    campaign_type: str,
    df: pd.DataFrame,
    month: MonthInfo,
    charts_dir: Path,
    chart_key: str,
) -> dict[str, Any]:
    filtered = df[
        df["campaign_type"].str.casefold() == campaign_type.casefold()
    ].copy()
    return _section_data(
        label=campaign_type,
        current=_filter_month(filtered, month),
        previous=_filter_month(filtered, month.previous_month),
        prior=_filter_month(filtered, MonthInfo(month.year - 1, month.month)),
        ytd=_filter_ytd(filtered, month.year, month.month),
        prior_ytd=_filter_ytd(filtered, month.year - 1, month.month),
        charts_dir=charts_dir,
        chart_key=chart_key,
    )


def _section_data(
    *,
    label: str,
    current: pd.DataFrame,
    previous: pd.DataFrame,
    prior: pd.DataFrame,
    ytd: pd.DataFrame,
    prior_ytd: pd.DataFrame,
    charts_dir: Path,
    chart_key: str,
) -> dict[str, Any]:
    ytd_monthly = _monthly_metrics(ytd)
    prior_ytd_monthly = _monthly_metrics(prior_ytd)
    missing = current.empty and ytd.empty
    if ytd_monthly.empty:
        ytd_monthly = _empty_monthly_frame()
    charts = {
        "revenue_cpa": _plot_revenue_cpa(
            charts_dir / f"{chart_key}_revenue_cpa.png", ytd_monthly, label
        ),
        "purchases_cpatc": _plot_purchases_cpatc(
            charts_dir / f"{chart_key}_purchases_cpatc.png", ytd_monthly, label
        ),
    }
    return {
        "label": label,
        "current": _metric_totals(current),
        "previous": _metric_totals(previous),
        "prior": _metric_totals(prior),
        "ytd": _metric_totals(ytd),
        "prior_ytd": _metric_totals(prior_ytd),
        "ytd_monthly": ytd_monthly,
        "prior_ytd_monthly": prior_ytd_monthly,
        "missing": missing,
        "charts": charts,
    }


def _filter_month(df: pd.DataFrame, month: MonthInfo) -> pd.DataFrame:
    return df[
        (df["date"].dt.year == month.year) & (df["date"].dt.month == month.month)
    ].copy()


def _filter_ytd(df: pd.DataFrame, year: int, month: int) -> pd.DataFrame:
    return df[(df["year"] == year) & (df["month"] <= month)].copy()


def _metric_totals(frame: pd.DataFrame) -> dict[str, float]:
    if frame.empty:
        totals = {"revenue": 0.0, "cost": 0.0, "purchases": 0.0, "add_to_cart": 0.0}
    else:
        totals = {
            "revenue": float(frame["revenue"].sum()),
            "cost": float(frame["cost"].sum()),
            "purchases": float(frame["purchases"].sum()),
            "add_to_cart": float(frame["add_to_cart"].sum()),
        }
    totals["cpa"] = _safe_div(totals["cost"], totals["purchases"])
    totals["cpatc"] = _safe_div(totals["cost"], totals["add_to_cart"])
    totals["aov"] = _safe_div(totals["revenue"], totals["purchases"])
    return totals


def _monthly_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return _empty_monthly_frame()
    grouped = (
        frame.groupby("month_start", as_index=False)[
            ["revenue", "cost", "purchases", "add_to_cart"]
        ]
        .sum(min_count=1)
        .sort_values("month_start")
        .reset_index(drop=True)
    )
    grouped["cpa"] = grouped.apply(
        lambda row: _safe_div(row["cost"], row["purchases"]), axis=1
    )
    grouped["cpatc"] = grouped.apply(
        lambda row: _safe_div(row["cost"], row["add_to_cart"]), axis=1
    )
    grouped["aov"] = grouped.apply(
        lambda row: _safe_div(row["revenue"], row["purchases"]), axis=1
    )
    grouped["month_label"] = grouped["month_start"].dt.strftime("%b")
    return grouped


def _empty_monthly_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "month_start",
            "month_label",
            "revenue",
            "cost",
            "purchases",
            "add_to_cart",
            "cpa",
            "cpatc",
            "aov",
        ]
    )


def _campaign_mix(ytd: pd.DataFrame) -> pd.DataFrame:
    if ytd.empty:
        return pd.DataFrame(
            columns=["campaign_type", "revenue", "cost", "purchases", "add_to_cart", "cpa", "cpatc", "aov", "cost_share", "revenue_share"]
        )
    grouped = (
        ytd.groupby("campaign_type", as_index=False)[
            ["revenue", "cost", "purchases", "add_to_cart"]
        ]
        .sum(min_count=1)
        .reset_index(drop=True)
    )
    grouped["cpa"] = grouped.apply(
        lambda row: _safe_div(row["cost"], row["purchases"]), axis=1
    )
    grouped["cpatc"] = grouped.apply(
        lambda row: _safe_div(row["cost"], row["add_to_cart"]), axis=1
    )
    grouped["aov"] = grouped.apply(
        lambda row: _safe_div(row["revenue"], row["purchases"]), axis=1
    )
    total_cost = grouped["cost"].sum()
    total_revenue = grouped["revenue"].sum()
    grouped["cost_share"] = grouped["cost"].map(lambda value: _safe_div(value, total_cost) * 100)
    grouped["revenue_share"] = grouped["revenue"].map(lambda value: _safe_div(value, total_revenue) * 100)
    order = {name: index for index, name in enumerate(CAMPAIGN_ORDER)}
    grouped["sort_order"] = grouped["campaign_type"].map(lambda value: order.get(str(value), len(order)))
    return grouped.sort_values(["sort_order", "revenue"], ascending=[True, False]).reset_index(drop=True)


def _yoy_summary(ytd: pd.DataFrame, prior_ytd: pd.DataFrame) -> dict[str, dict[str, float | None]]:
    current = _metric_totals(ytd)
    prior = _metric_totals(prior_ytd)
    summary: dict[str, dict[str, float | None]] = {}
    for metric in CARD_METRICS + ("add_to_cart",):
        summary[metric] = {
            "current": current[metric],
            "prior": prior[metric],
            "change_pct": _pct_change(prior[metric], current[metric]),
        }
    return summary


def _card_shape_text(
    kpi_ids: Mapping[str, Sequence[str]],
    section: Mapping[str, Any],
    period: Mapping[str, Any],
    *,
    missing_label: str | None = None,
) -> dict[str, str]:
    current = section.get("current", {})
    previous = section.get("previous", {})
    prior = section.get("prior", {})
    shape_text: dict[str, str] = {}
    for metric in CARD_METRICS:
        ids = list(kpi_ids.get(metric) or [])
        if len(ids) != 3:
            continue
        value_id, period_id, delta_id = ids
        if missing_label:
            shape_text[value_id] = "n/a"
            shape_text[period_id] = period["label"]
            shape_text[delta_id] = "Review required"
            continue
        value = float(current.get(metric, 0.0))
        shape_text[value_id] = _metric_value(metric, value)
        shape_text[period_id] = CARD_SUBLABELS.get(metric, period["label"])
        shape_text[delta_id] = (
            f"MoM: {_delta_label(metric, previous.get(metric), value)}\n"
            f"YoY: {_delta_label(metric, prior.get(metric), value)}"
        )
    return shape_text


def _monthly_table_values(frame: pd.DataFrame, *, overall: bool = False) -> list[list[str]]:
    columns = OVERALL_TABLE_COLUMNS if overall else MONTHLY_TABLE_COLUMNS
    values = [list(columns)]
    if frame.empty:
        values.append(["No data"] + [""] * (len(columns) - 1))
        return values
    for row in frame.sort_values("month_start").itertuples(index=False):
        base = {
            "Month": row.month_label,
            "Revenue": _currency(row.revenue),
            "Cost": _currency(row.cost),
            "Spend": _currency(row.cost),
            "Purchases": _count(row.purchases),
            "Add to Cart": _count(row.add_to_cart),
            "CPA": _currency(row.cpa),
            "Cost/ATC": _currency(row.cpatc),
            "AOV": _currency(row.aov),
        }
        values.append([base[column] for column in columns])
    totals = _metric_totals(frame)
    base_total = {
        "Month": "Total",
        "Revenue": _currency(totals["revenue"]),
        "Cost": _currency(totals["cost"]),
        "Spend": _currency(totals["cost"]),
        "Purchases": _count(totals["purchases"]),
        "Add to Cart": _count(totals["add_to_cart"]),
        "CPA": _currency(totals["cpa"]),
        "Cost/ATC": _currency(totals["cpatc"]),
        "AOV": _currency(totals["aov"]),
    }
    values.append([base_total[column] for column in columns])
    return values


def _yoy_table_values(yoy: Mapping[str, Mapping[str, float | None]], period: Mapping[str, Any]) -> list[list[str]]:
    values = [["Metric", period["prior_ytd_label"], period["ytd_label"], "Change"]]
    specs = (
        ("Revenue", "revenue", _currency),
        ("Cost", "cost", _currency),
        ("Purchases", "purchases", _count),
        ("Add to Cart", "add_to_cart", _count),
        ("CPA", "cpa", _currency),
        ("Cost per ATC", "cpatc", _currency),
        ("AOV", "aov", _currency),
    )
    for label, key, formatter in specs:
        item = yoy.get(key, {})
        values.append(
            [
                label,
                formatter(float(item.get("prior") or 0.0)),
                formatter(float(item.get("current") or 0.0)),
                _fmt_pct(item.get("change_pct")),
            ]
        )
    return values


def _campaign_mix_table_values(frame: pd.DataFrame) -> list[list[str]]:
    values = [list(CAMPAIGN_TABLE_COLUMNS)]
    if frame.empty:
        values.append(["No data"] + [""] * (len(CAMPAIGN_TABLE_COLUMNS) - 1))
        return values
    for row in frame.itertuples(index=False):
        values.append(
            [
                str(row.campaign_type),
                _currency(row.revenue),
                _currency(row.cost),
                _count(row.purchases),
                _count(row.add_to_cart),
                _currency(row.cpa),
                _currency(row.cpatc),
                _currency(row.aov),
                f"{float(row.cost_share):.1f}%",
                f"{float(row.revenue_share):.1f}%",
            ]
        )
    return values


def _summary_insights(label: str, section: Mapping[str, Any], period: Mapping[str, Any]) -> list[str]:
    if section.get("missing"):
        return [f"No {label} rows were available in the monthly performance CSV."]
    current = section["current"]
    ytd = section["ytd"]
    previous = section["previous"]
    prior = section["prior"]
    return [
        f"{label} delivered {_currency(current['revenue'])} revenue from {_count(current['purchases'])} purchases on {_currency(current['cost'])} spend in {period['label']}.",
        f"Revenue moved {_delta_label('revenue', previous['revenue'], current['revenue'])} MoM and {_delta_label('revenue', prior['revenue'], current['revenue'])} YoY.",
        f"{period['ytd_label']} totals are {_currency(ytd['revenue'])} revenue, {_count(ytd['purchases'])} purchases, and {_currency(ytd['cpa'])} CPA.",
        f"Add-to-cart volume was {_count(current['add_to_cart'])} for the month, with {_currency(current['cpatc'])} cost per ATC.",
    ]


def _ytd_insights(label: str, section: Mapping[str, Any], period: Mapping[str, Any]) -> list[str]:
    if section.get("missing"):
        return [f"No {label} YTD rows were available in the monthly performance CSV."]
    monthly = section["ytd_monthly"]
    if monthly.empty:
        return [f"No {label} YTD rows were available in the monthly performance CSV."]
    best_revenue = monthly.loc[monthly["revenue"].idxmax()]
    best_cpa = monthly.loc[monthly["cpa"].idxmin()]
    totals = section["ytd"]
    return [
        f"{period['ytd_label']} {label} revenue totalled {_currency(totals['revenue'])} from {_count(totals['purchases'])} purchases.",
        f"{best_revenue['month_label']} was the strongest {label} revenue month.",
        f"{best_cpa['month_label']} was the most efficient {label} month on CPA.",
        f"YTD cost per ATC is {_currency(totals['cpatc'])}, with AOV at {_currency(totals['aov'])}.",
    ]


def _campaign_mix_insights(frame: pd.DataFrame) -> list[str]:
    if frame.empty:
        return ["No campaign type rows were available for the selected YTD period."]
    top_revenue = frame.sort_values("revenue_share", ascending=False).iloc[0]
    top_cost = frame.sort_values("cost_share", ascending=False).iloc[0]
    strongest = frame[frame["purchases"] > 0].sort_values("cpa", ascending=True).iloc[0]
    return [
        f"{top_revenue['campaign_type']} drove the largest revenue share at {top_revenue['revenue_share']:.1f}%.",
        f"{top_cost['campaign_type']} carried the largest cost share at {top_cost['cost_share']:.1f}%.",
        f"{strongest['campaign_type']} was the most efficient campaign type on YTD CPA at {_currency(strongest['cpa'])}.",
    ]


def _yoy_insights(data: Mapping[str, Any], period: Mapping[str, Any]) -> list[str]:
    yoy = data["yoy"]
    return [
        f"The comparison covers {period['matched_months']} matched month(s) against the same YTD period last year.",
        f"Revenue moved {_fmt_pct(yoy['revenue']['change_pct'])}, spend moved {_fmt_pct(yoy['cost']['change_pct'])}, and purchases moved {_fmt_pct(yoy['purchases']['change_pct'])}.",
        f"CPA moved {_fmt_pct(yoy['cpa']['change_pct'])}, cost per ATC moved {_fmt_pct(yoy['cpatc']['change_pct'])}, and AOV moved {_fmt_pct(yoy['aov']['change_pct'])}.",
    ]


def _build_shape_text_requests(
    shape_text: Mapping[str, str],
    existing_shape_text: Mapping[str, str],
    *,
    warnings: list[str],
) -> list[dict[str, Any]]:
    requests_body: list[dict[str, Any]] = []
    for object_id, text in shape_text.items():
        if object_id not in existing_shape_text:
            warnings.append(f"Template text object not found: {object_id}")
            continue
        if existing_shape_text.get(object_id, "").strip():
            requests_body.append(
                {
                    "deleteText": {
                        "objectId": object_id,
                        "textRange": {"type": "ALL"},
                    }
                }
            )
        if text:
            requests_body.append(
                {
                    "insertText": {
                        "objectId": object_id,
                        "insertionIndex": 0,
                        "text": str(text),
                    }
                }
            )
    return requests_body


def _build_table_requests(
    table_payloads: Mapping[str, Mapping[str, Any]],
    table_dimensions: Mapping[str, tuple[int, int]],
    table_cell_text: Mapping[str, dict[tuple[int, int], str]],
    *,
    warnings: list[str],
) -> list[dict[str, Any]]:
    requests_body: list[dict[str, Any]] = []
    for table_id, payload in table_payloads.items():
        values = payload.get("values") or []
        if not values:
            continue
        if table_id not in table_dimensions:
            warnings.append(f"Template table object not found: {table_id}")
            continue
        existing_rows, existing_columns = table_dimensions[table_id]
        target_rows = len(values)
        target_columns = max(len(row) for row in values)
        final_rows = max(existing_rows, target_rows)
        final_columns = max(existing_columns, target_columns)
        if target_rows > existing_rows:
            requests_body.append(
                {
                    "insertTableRows": {
                        "tableObjectId": table_id,
                        "cellLocation": {
                            "rowIndex": existing_rows - 1,
                            "columnIndex": 0,
                        },
                        "insertBelow": True,
                        "number": target_rows - existing_rows,
                    }
                }
            )
        if target_columns > existing_columns:
            requests_body.append(
                {
                    "insertTableColumns": {
                        "tableObjectId": table_id,
                        "cellLocation": {
                            "rowIndex": 0,
                            "columnIndex": existing_columns - 1,
                        },
                        "insertRight": True,
                        "number": target_columns - existing_columns,
                    }
                }
            )
        requests_body.extend(
            _table_cell_text_requests(
                table_id,
                values,
                final_rows,
                final_columns,
                table_cell_text.get(table_id, {}),
            )
        )
        requests_body.extend(
            _table_format_requests(
                table_id,
                values=values,
                row_count=final_rows,
                column_count=final_columns,
                font_size=float(payload.get("font_size") or TABLE_FONT_SIZE_PT),
            )
        )
        requests_body.append(_table_row_height_request(table_id))
    return requests_body


def _table_format_requests(
    table_id: str,
    *,
    values: Sequence[Sequence[str]],
    row_count: int,
    column_count: int,
    font_size: float,
) -> list[dict[str, Any]]:
    requests_body: list[dict[str, Any]] = [
        {
            "updateTableCellProperties": {
                "objectId": table_id,
                "tableRange": {
                    "location": {"rowIndex": 0, "columnIndex": 0},
                    "rowSpan": 1,
                    "columnSpan": column_count,
                },
                "tableCellProperties": {
                    "tableCellBackgroundFill": {
                        "solidFill": {"color": {"rgbColor": HEADER_FILL_RGB}}
                    }
                },
                "fields": "tableCellBackgroundFill.solidFill.color",
            }
        }
    ]
    for row_index in range(row_count):
        for column_index in range(column_count):
            row = values[row_index] if row_index < len(values) else []
            cell_text = str(row[column_index]) if column_index < len(row) else ""
            if not cell_text.strip():
                continue
            rgb = WHITE_RGB if row_index == 0 else BLACK_RGB
            requests_body.append(
                {
                    "updateParagraphStyle": {
                        "objectId": table_id,
                        "cellLocation": {
                            "rowIndex": row_index,
                            "columnIndex": column_index,
                        },
                        "textRange": {"type": "ALL"},
                        "style": {"alignment": "CENTER"},
                        "fields": "alignment",
                    }
                }
            )
            requests_body.append(
                {
                    "updateTextStyle": {
                        "objectId": table_id,
                        "cellLocation": {
                            "rowIndex": row_index,
                            "columnIndex": column_index,
                        },
                        "textRange": {"type": "ALL"},
                        "style": {
                            "foregroundColor": {
                                "opaqueColor": {"rgbColor": rgb}
                            },
                            "bold": row_index == 0,
                            "fontSize": {"magnitude": font_size, "unit": "PT"},
                        },
                        "fields": "foregroundColor,bold,fontSize",
                    }
                }
            )
    return requests_body


def _table_row_height_request(object_id: str) -> dict[str, Any]:
    return {
        "updateTableRowProperties": {
            "objectId": object_id,
            "tableRowProperties": {
                "minRowHeight": {"magnitude": TABLE_ROW_HEIGHT_EMU, "unit": "EMU"}
            },
            "fields": "minRowHeight",
        }
    }


def _upload_chart_assets(
    asset_store: DriveChartAssetStore,
    charts: Mapping[str, Path],
) -> dict[str, str]:
    urls: dict[str, str] = {}
    for image_id, chart_path in charts.items():
        path = Path(chart_path)
        if not path.exists():
            continue
        asset = asset_store.upload_chart(path)
        urls[image_id] = asset.public_url
    return urls


def _build_chart_requests(
    charts: Mapping[str, Path],
    uploaded_assets: Mapping[str, str],
    presentation: Mapping[str, Any],
    *,
    warnings: list[str],
) -> list[dict[str, Any]]:
    image_ids = _image_object_ids(presentation)
    requests_body: list[dict[str, Any]] = []
    for image_id in charts:
        url = uploaded_assets.get(image_id)
        if not url:
            continue
        if image_id not in image_ids:
            warnings.append(f"Template image object not found: {image_id}")
            continue
        requests_body.append(
            {
                "replaceImage": {
                    "imageObjectId": image_id,
                    "url": url,
                    "imageReplaceMethod": "CENTER_INSIDE",
                }
            }
        )
    return requests_body


def _build_text_style_requests(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    requests_body: list[dict[str, Any]] = []
    requests_body.extend(_template_title_style_requests())
    metric_delta_ids = _metric_delta_object_ids()
    cost_delta_ids = {
        object_id for object_id, metric in metric_delta_ids.items() if metric == "cost"
    }
    for object_id in cost_delta_ids:
        requests_body.append(_shape_text_color_request(object_id, NEUTRAL_RGB))
    for object_id, text in payload["shape_text"].items():
        if str(text).startswith("MoM:") and object_id not in cost_delta_ids:
            rgb = _delta_text_rgb(str(text), metric_delta_ids.get(str(object_id), ""))
            requests_body.append(_shape_text_color_request(str(object_id), rgb))
    return requests_body


def _template_title_style_requests() -> list[dict[str, Any]]:
    manifest = _read_json(OLYMPIC_MONTHLY_TEMPLATE_MANIFEST)
    global_ids = manifest.get("global_text_ids", {})
    slides = manifest.get("slides", {})
    white_ids = {
        str(global_ids.get("cover_report_title") or ""),
        str(global_ids.get("cover_period") or ""),
        str(global_ids.get("cover_revenue") or ""),
        str(global_ids.get("cover_purchases") or ""),
        str(global_ids.get("cover_cpa") or ""),
    }
    muted_ids = {
        "g3faba7ffb95_2_18",
        "g3faba7ffb95_2_20",
        "g3faba7ffb95_2_22",
    }
    for slide in slides.values():
        if slide.get("title_id"):
            white_ids.add(str(slide["title_id"]))
        if slide.get("subtitle_id"):
            muted_ids.add(str(slide["subtitle_id"]))
    white_ids.discard("")
    muted_ids.discard("")
    requests_body = [_shape_text_color_request(object_id, WHITE_RGB) for object_id in sorted(white_ids)]
    requests_body.extend(
        _shape_text_color_request(object_id, MUTED_ON_DARK_RGB)
        for object_id in sorted(muted_ids)
    )
    return requests_body


def _metric_delta_object_ids() -> dict[str, str]:
    ids: dict[str, str] = {}
    manifest = _read_json(OLYMPIC_MONTHLY_TEMPLATE_MANIFEST)
    for section in ("overall_cards", "generic_cards", "pmax_cards", "island_cards"):
        kpi_ids = manifest.get("slides", {}).get(section, {}).get("kpi_ids", {})
        for metric, object_ids in kpi_ids.items():
            object_ids = list(object_ids or [])
            if len(object_ids) == 3:
                ids[str(object_ids[2])] = str(metric)
    return ids


def _shape_text_color_request(object_id: str, rgb: Mapping[str, float]) -> dict[str, Any]:
    return {
        "updateTextStyle": {
            "objectId": object_id,
            "textRange": {"type": "ALL"},
            "style": {
                "foregroundColor": {"opaqueColor": {"rgbColor": dict(rgb)}}
            },
            "fields": "foregroundColor",
        }
    }


def _shape_text_by_id(presentation: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(element.get("objectId") or ""): _element_text(element)
        for slide in presentation.get("slides") or []
        for element in slide.get("pageElements") or []
        if element.get("shape")
    }


def _image_object_ids(presentation: Mapping[str, Any]) -> set[str]:
    return {
        str(element.get("objectId") or "")
        for slide in presentation.get("slides") or []
        for element in slide.get("pageElements") or []
        if "image" in element
    }


def _element_text(element: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for item in element.get("shape", {}).get("text", {}).get("textElements", []) or []:
        text_run = item.get("textRun")
        if text_run and text_run.get("content"):
            parts.append(str(text_run["content"]))
    return "".join(parts)


def _plot_revenue_cpa(output_path: Path, frame: pd.DataFrame, label: str) -> Path:
    return _plot_bar_line(
        output_path,
        frame,
        bar_col="revenue",
        line_col="cpa",
        bar_label="Revenue",
        line_label="CPA",
        title=f"{label} Revenue and CPA",
        bar_color=OLYMPIC_BLUE,
        line_color=OLYMPIC_RED,
    )


def _plot_purchases_cpatc(output_path: Path, frame: pd.DataFrame, label: str) -> Path:
    return _plot_bar_line(
        output_path,
        frame,
        bar_col="purchases",
        line_col="cpatc",
        bar_label="Purchases",
        line_label="Cost/ATC",
        title=f"{label} Purchases and Cost/ATC",
        bar_color=OLYMPIC_RED,
        line_color=OLYMPIC_BLUE,
    )


def _plot_bar_line(
    output_path: Path,
    frame: pd.DataFrame,
    *,
    bar_col: str,
    line_col: str,
    bar_label: str,
    line_label: str,
    title: str,
    bar_color: str,
    line_color: str,
) -> Path:
    fig, ax1 = plt.subplots(figsize=(6.0, 2.9))
    if frame.empty or frame[bar_col].fillna(0).sum() == 0:
        _empty_chart(ax1, "No source data")
    else:
        plot_df = frame.sort_values("month_start")
        ax2 = ax1.twinx()
        ax1.bar(plot_df["month_label"], plot_df[bar_col], color=bar_color, alpha=0.88)
        ax2.plot(
            plot_df["month_label"],
            plot_df[line_col],
            color=line_color,
            marker="o",
            linewidth=2.0,
        )
        ax1.set_title(title, fontsize=10, color=OLYMPIC_BLUE, fontweight="bold")
        ax1.set_ylabel(bar_label, fontsize=8, color=OLYMPIC_BLUE)
        ax2.set_ylabel(line_label, fontsize=8, color=line_color)
        ax1.tick_params(axis="x", labelsize=8)
        ax1.tick_params(axis="y", labelsize=7)
        ax2.tick_params(axis="y", labelsize=7)
        ax1.grid(axis="y", alpha=0.18)
        ax1.spines[["top", "right"]].set_visible(False)
        ax2.spines[["top", "left"]].set_visible(False)
    plt.tight_layout()
    fig.savefig(output_path, dpi=220, transparent=False, facecolor="white")
    plt.close(fig)
    return output_path


def _plot_share_donut(
    output_path: Path, frame: pd.DataFrame, value_col: str, title: str
) -> Path:
    fig, ax = plt.subplots(figsize=(4.1, 2.95))
    chart_df = frame[frame[value_col] > 0].copy() if not frame.empty else frame
    if chart_df.empty:
        _empty_chart(ax, "No source data")
    else:
        colors = [OLYMPIC_BLUE, OLYMPIC_RED, "#68778D", OLYMPIC_GREY, "#D9D5D0"]
        wedges, _, _ = ax.pie(
            chart_df[value_col],
            labels=None,
            autopct=lambda pct: f"{pct:.0f}%" if pct >= 4 else "",
            startangle=90,
            colors=colors[: len(chart_df)],
            wedgeprops={"width": 0.42, "edgecolor": "white"},
            textprops={"fontsize": 7},
        )
        ax.legend(
            wedges,
            chart_df["campaign_type"],
            loc="center left",
            bbox_to_anchor=(0.88, 0.5),
            fontsize=6.5,
            frameon=False,
        )
        ax.set_title(title, fontsize=10, color=OLYMPIC_BLUE, fontweight="bold")
        ax.axis("equal")
    plt.tight_layout()
    fig.savefig(output_path, dpi=220, transparent=False, facecolor="white")
    plt.close(fig)
    return output_path


def _plot_yoy_summary(
    output_path: Path,
    yoy: Mapping[str, Mapping[str, float | None]],
    period: Mapping[str, Any],
) -> Path:
    fig, ax = plt.subplots(figsize=(6.0, 2.9))
    metrics = ["Revenue", "Cost", "Purchases"]
    keys = ["revenue", "cost", "purchases"]
    current = [float(yoy[key].get("current") or 0.0) for key in keys]
    prior = [float(yoy[key].get("prior") or 0.0) for key in keys]
    if not any(current) and not any(prior):
        _empty_chart(ax, "No prior-year source data")
    else:
        x = range(len(metrics))
        width = 0.34
        ax.bar([item - width / 2 for item in x], prior, width, label=period["prior_ytd_label"], color=OLYMPIC_GREY)
        ax.bar([item + width / 2 for item in x], current, width, label=period["ytd_label"], color=OLYMPIC_RED)
        ax.set_xticks(list(x))
        ax.set_xticklabels(metrics, fontsize=8)
        ax.set_title("YTD YoY Comparison", fontsize=10, color=OLYMPIC_BLUE, fontweight="bold")
        ax.grid(axis="y", alpha=0.18)
        ax.legend(fontsize=7, loc="upper left")
        ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    fig.savefig(output_path, dpi=220, transparent=False, facecolor="white")
    plt.close(fig)
    return output_path


def _empty_chart(ax, label: str) -> None:
    ax.text(0.5, 0.5, label, ha="center", va="center", color=NEUTRAL_RGB_HEX())
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)


def NEUTRAL_RGB_HEX() -> str:
    return "#6B6B6B"


def _normalise_column(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip().replace("_", " ")).casefold()


def _to_number(values: pd.Series) -> pd.Series:
    text = (
        values.astype(str)
        .str.replace("£", "", regex=False)
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.strip()
    )
    return pd.to_numeric(text, errors="coerce").fillna(0.0)


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator == 0 or pd.isna(denominator):
        return 0.0
    return float(numerator) / float(denominator)


def _pct_change(base: Any, current: Any) -> float | None:
    try:
        base_value = float(base)
        current_value = float(current)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(base_value) or not math.isfinite(current_value) or base_value == 0:
        return None
    return ((current_value - base_value) / base_value) * 100


def _delta_label(metric: str, base: Any, current: Any) -> str:
    change = _pct_change(base, current)
    if change is None:
        return "n/a"
    return _fmt_pct(change)


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(numeric):
        return "n/a"
    return f"{numeric:+.1f}%"


def _delta_text_rgb(text: str, metric: str = "") -> Mapping[str, float]:
    matches = re.findall(r"[-+]?\d+(?:\.\d+)?%", text)
    if not matches:
        return NEUTRAL_RGB
    value = float(matches[-1].replace("%", ""))
    if metric in {"cpa", "cpatc"}:
        return POSITIVE_RGB if value <= 0 else NEGATIVE_RGB
    return POSITIVE_RGB if value >= 0 else NEGATIVE_RGB


def _metric_value(metric: str, value: float) -> str:
    if metric in {"revenue", "cost", "cpa", "cpatc", "aov"}:
        return _currency(value)
    return _count(value)


def _currency(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 0.0
    if not math.isfinite(numeric):
        numeric = 0.0
    return f"£{numeric:,.0f}"


def _count(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 0.0
    if not math.isfinite(numeric):
        numeric = 0.0
    return f"{numeric:,.1f}"


def _monthly_period_subtitle(month: MonthInfo) -> str:
    return f"{month.label} (YTD Jan - {month.start.strftime('%b %Y')})"


def _output_deck_title(client_name: str, period_label: str) -> str:
    cleaned_period = f" {period_label}" if period_label else ""
    generated_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H%M UTC")
    return f"{client_name}{cleaned_period} Monthly Report - API Source Test - {generated_stamp}"


__all__ = [
    "build_olympic_monthly_slides_payload",
    "generate_olympic_monthly_google_slides",
]
