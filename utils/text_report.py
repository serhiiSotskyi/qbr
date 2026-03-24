from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

from src.auction_loader import load_auction_csv
from src.auction_metrics import summarize_auction_insights
from src.config_loader import ConfigLoader
from src.data_loader import detect_latest_complete_quarter, load_csv
from src.metrics import format_summary_table, prepare_report_data, validate_report_data
from src.narrative_generator import (
    generate_auction_bullets,
    generate_mix_bullets,
    generate_overall_bullets,
    generate_scope_bullets,
    generate_trend_bullets,
)
from src.recommendation_generator import generate_recommendations
from src.trends_loader import TrendsLoader
from src.trends_metrics import summarize_trends


SECTION_DIVIDER = "-" * 40


class TextReportBuilder:
    def __init__(self) -> None:
        self._sections: list[str] = []

    def add_title_slide(self, title: str, subtitle: str) -> None:
        self._sections.append(f"{SECTION_DIVIDER}\n\n{title}\n{subtitle}")

    def add_divider_slide(self, title: str) -> None:
        self._sections.append(f"{SECTION_DIVIDER}\n\n{title.upper()}")

    def add_trend_slide(
        self,
        title: str,
        subtitle: str,
        table_df: pd.DataFrame,
        bullets: Iterable[str],
    ) -> None:
        body_parts = [subtitle]
        if not table_df.empty:
            body_parts.extend(["", self._render_table(table_df)])
        body_parts.extend(self._render_bullets(bullets))
        self._sections.append(self._render_section(title, body_parts))

    def add_mix_slide(
        self,
        title: str,
        subtitle: str,
        table_df: pd.DataFrame,
        bullets: Iterable[str],
    ) -> None:
        body_parts = [subtitle]
        if not table_df.empty:
            body_parts.extend(["", self._render_table(table_df)])
        body_parts.extend(self._render_bullets(bullets))
        self._sections.append(self._render_section(title, body_parts))

    def add_table_slide(
        self,
        title: str,
        subtitle: str,
        table_df: pd.DataFrame,
        bullets: Iterable[str],
    ) -> None:
        body_parts = [subtitle]
        if not table_df.empty:
            body_parts.extend(["", self._render_table(table_df)])
        body_parts.extend(self._render_bullets(bullets))
        self._sections.append(self._render_section(title, body_parts))

    def add_single_chart_slide(
        self,
        title: str,
        subtitle: str,
        table_df: pd.DataFrame,
        bullets: Iterable[str],
        source_note: str = "",
    ) -> None:
        body_parts = [subtitle]
        if not table_df.empty:
            body_parts.extend(["", self._render_table(table_df)])
        body_parts.extend(self._render_bullets(bullets))
        if source_note:
            body_parts.extend(["", source_note])
        self._sections.append(self._render_section(title, body_parts))

    def add_auction_insights_slide(
        self,
        title: str,
        subtitle: str,
        table_df: pd.DataFrame,
        bullets: Iterable[str],
        source_note: str = "",
    ) -> None:
        body_parts = [subtitle]
        if not table_df.empty:
            body_parts.extend(["", self._render_table(table_df)])
        body_parts.extend(self._render_bullets(bullets))
        if source_note:
            body_parts.extend(["", source_note])
        self._sections.append(self._render_section(title, body_parts))

    def add_recommendations_slide(
        self,
        title: str,
        subtitle: str,
        recommendations: Sequence[dict],
        source_note: str = "",
    ) -> None:
        body_parts = [subtitle]
        for item in recommendations or [{"heading": "Next quarter focus", "text": "No recommendation data was available."}]:
            heading = str(item.get("heading", "")).strip()
            text = str(item.get("text", "")).strip()
            if heading and text:
                body_parts.append(f"- {heading}: {text}")
            elif text:
                body_parts.append(f"- {text}")
        if source_note:
            body_parts.extend(["", source_note])
        self._sections.append(self._render_section(title, body_parts))

    def render(self) -> str:
        return "\n\n".join(section.rstrip() for section in self._sections).strip() + "\n"

    @staticmethod
    def _render_section(title: str, body_parts: Sequence[str]) -> str:
        lines = [SECTION_DIVIDER, "", title]
        lines.extend(part for part in body_parts if part != "")
        return "\n".join(lines)

    @staticmethod
    def _render_bullets(bullets: Iterable[str]) -> list[str]:
        return [f"- {bullet}" for bullet in list(bullets)]

    @staticmethod
    def _render_table(table_df: pd.DataFrame) -> str:
        safe_df = table_df.copy()
        if safe_df.empty:
            safe_df = pd.DataFrame([{"Status": "No data available"}])
        return safe_df.to_string(index=False)


def generate_text_report(
    data,
    campaigns=None,
    other_inputs_if_needed: dict | None = None,
    *,
    output_path: str | Path | None = None,
) -> str:
    report = data
    if not isinstance(report, dict) or "overall" not in report:
        raise ValueError("generate_text_report expects prepared report data from prepare_report_data().")

    context = other_inputs_if_needed or {}
    client_name = str(context["client_name"])
    report_title = str(context["report_title"])
    agency_name = str(context.get("agency_name", ""))
    subtitle = str(context["subtitle"])
    client_config = dict(context["client_config"])
    config_loader = context["config_loader"]
    trends_summary = context.get("trends_summary")
    auction_summary = context.get("auction_summary")
    recommendations = context.get("recommendations", [])

    builder = TextReportBuilder()

    title_text = report_title if not agency_name else f"{client_name} | {report_title}"
    subtitle_text = subtitle if not agency_name else f"{subtitle} | {agency_name}"
    builder.add_title_slide(title_text, subtitle_text)

    if config_loader.is_slide_enabled("include_performance", client_config):
        _build_performance_section(builder, report, subtitle, client_config, config_loader, campaigns)

    if config_loader.is_slide_enabled("include_trends", client_config) and trends_summary:
        _build_trends_section(builder, trends_summary, subtitle, client_config, config_loader)

    if config_loader.is_slide_enabled("include_auction_insights", client_config) and auction_summary:
        _build_auction_section(builder, auction_summary, subtitle, client_config, config_loader)

    if config_loader.is_slide_enabled("include_recommendations", client_config) and recommendations:
        builder.add_divider_slide("Recommendations")
        builder.add_recommendations_slide(
            title="Recommendations / Next Steps",
            subtitle=subtitle,
            recommendations=recommendations,
        )

    text = builder.render()
    if output_path is not None:
        save_text_report(text, output_path)
    return text


def save_text_report(report_text: str, output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report_text, encoding="utf-8")
    return output


class TextReportPipeline:
    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root)
        self.config_loader = ConfigLoader(
            report_config_path=self.project_root / "config" / "report_config.yaml",
            chart_styles_path=self.project_root / "config" / "chart_styles.yaml",
            clients_config_path=self.project_root / "config" / "clients_config.json",
        )

    def run(
        self,
        input_csv: str | Path,
        output_txt: str | Path | None = None,
        client_id: str | None = None,
        auction_csv: str | Path | None = None,
        trends_dir: str | Path | None = None,
    ) -> Path:
        client_config = self.config_loader.get_client_config(client_id)
        df = load_csv(input_csv)
        quarter = detect_latest_complete_quarter(df)
        report = prepare_report_data(
            df,
            quarter,
            campaign_order=self.config_loader.get_campaign_types(client_config),
            destination_order=self.config_loader.get_destinations(client_config),
        )
        validate_report_data(report)

        subtitle = f"{quarter.label} ({quarter.start.strftime('%b')} - {quarter.end.strftime('%b %Y')})"
        client_name = self.config_loader.get_client_name(client_config)
        report_title = self.config_loader.get_report_title(client_config)
        agency_name = self.config_loader.get_agency_name(client_config)

        trends_summary = _load_trends_summary(self.project_root, self.config_loader, client_config, quarter, trends_dir)
        auction_summary = _load_auction_summary(self.config_loader, client_config, auction_csv)
        recommendations = generate_recommendations(report, trends_summary=trends_summary, auction_summary=auction_summary)

        output_path = Path(output_txt) if output_txt else self.project_root / "reports" / "report.txt"
        generate_text_report(
            report,
            campaigns=report.get("available_campaigns"),
            other_inputs_if_needed={
                "client_name": client_name,
                "report_title": report_title,
                "agency_name": agency_name,
                "subtitle": subtitle,
                "client_config": client_config,
                "config_loader": self.config_loader,
                "trends_summary": trends_summary,
                "auction_summary": auction_summary,
                "recommendations": recommendations,
            },
            output_path=output_path,
        )
        return output_path


def _build_performance_section(
    builder: TextReportBuilder,
    report: dict,
    subtitle: str,
    client_config: dict,
    config_loader: ConfigLoader,
    campaigns,
) -> None:
    builder.add_divider_slide("Performance")

    if config_loader.is_slide_enabled("overview", client_config):
        overall_scope = report["overall"]
        builder.add_trend_slide(
            title="Overall Performance Trend",
            subtitle=subtitle,
            table_df=format_summary_table(overall_scope["monthly"], report["include_revenue"]),
            bullets=generate_overall_bullets(overall_scope, report["mix_overall"]),
        )

        builder.add_table_slide(
            title="Overall Quarter Summary",
            subtitle=subtitle,
            table_df=format_summary_table(overall_scope["monthly"], report["include_revenue"]),
            bullets=generate_scope_bullets("Overall", overall_scope),
        )

    if config_loader.is_slide_enabled("campaign_mix", client_config):
        builder.add_mix_slide(
            title="Campaign Type Mix",
            subtitle=subtitle,
            table_df=_format_mix_table(report["mix_overall"]),
            bullets=generate_mix_bullets(report["mix_overall"], "overall"),
        )

    if config_loader.is_slide_enabled("campaign_summary", client_config):
        campaign_names = list(campaigns or report["available_campaigns"])
        for campaign in campaign_names:
            scope = report["campaigns"][campaign]
            builder.add_table_slide(
                title=f"{campaign} Summary",
                subtitle=subtitle,
                table_df=format_summary_table(scope["monthly"], report["include_revenue"]),
                bullets=generate_scope_bullets(campaign, scope),
            )

            builder.add_trend_slide(
                title=f"{campaign} Monthly Trend",
                subtitle=subtitle,
                table_df=format_summary_table(scope["monthly"], report["include_revenue"]),
                bullets=generate_scope_bullets(campaign, scope),
            )

    if config_loader.is_slide_enabled("destination_summary", client_config):
        for destination in report["available_destinations"]:
            scope = report["destinations"][destination]
            builder.add_table_slide(
                title=f"{destination} Summary + YoY",
                subtitle=subtitle,
                table_df=format_summary_table(scope["monthly"], report["include_revenue"]),
                bullets=generate_scope_bullets(destination, scope),
            )

            builder.add_trend_slide(
                title=f"{destination} Monthly Trend",
                subtitle=subtitle,
                table_df=format_summary_table(scope["monthly"], report["include_revenue"]),
                bullets=generate_scope_bullets(destination, scope),
            )

            builder.add_mix_slide(
                title=f"{destination} Campaign Mix",
                subtitle=subtitle,
                table_df=_format_mix_table(report["dest_mix"][destination]),
                bullets=generate_mix_bullets(report["dest_mix"][destination], destination),
            )


def _build_trends_section(
    builder: TextReportBuilder,
    trends_summary: dict,
    subtitle: str,
    client_config: dict,
    config_loader: ConfigLoader,
) -> None:
    builder.add_divider_slide("Google Trends")
    source_note = config_loader.get_source_note("google_trends", client_config)

    brand_summary = trends_summary.get("brand")
    if brand_summary:
        builder.add_single_chart_slide(
            title=f"{config_loader.get_client_name(client_config)} Terms Are Growing",
            subtitle=subtitle,
            table_df=_format_trends_table(brand_summary["comparison"]),
            bullets=generate_trend_bullets(brand_summary, "Brand"),
            source_note=source_note,
        )

    for destination_summary in trends_summary.get("destinations", []):
        builder.add_single_chart_slide(
            title=f"{destination_summary['name']} Demand Trend",
            subtitle=subtitle,
            table_df=_format_trends_table(destination_summary["comparison"]),
            bullets=generate_trend_bullets(destination_summary, destination_summary["name"]),
            source_note=source_note,
        )


def _build_auction_section(
    builder: TextReportBuilder,
    auction_summary: dict,
    subtitle: str,
    client_config: dict,
    config_loader: ConfigLoader,
) -> None:
    builder.add_divider_slide("Auction Insights")
    builder.add_auction_insights_slide(
        title="Brand coverage is very strong",
        subtitle=subtitle,
        table_df=auction_summary["table"].head(8),
        bullets=generate_auction_bullets(auction_summary),
        source_note=config_loader.get_source_note("auction_insights", client_config),
    )


def _load_trends_summary(
    project_root: Path,
    config_loader: ConfigLoader,
    client_config: dict,
    quarter,
    trends_dir: str | Path | None,
) -> dict | None:
    brand_config = client_config.get("brand_trends", {})
    destination_config = client_config.get("destination_trends", {})
    trend_aliases = client_config.get("trend_aliases", {})
    if not brand_config.get("enabled") and not destination_config.get("enabled"):
        return None
    if not trends_dir:
        return None

    loader = TrendsLoader(project_root / trends_dir if not Path(trends_dir).is_absolute() else trends_dir)
    trends_df = loader.load_from_directory()
    if trends_df.empty:
        return None

    summary = summarize_trends(
        trends_df=trends_df,
        quarter=quarter,
        brand_terms=brand_config.get("terms", []) if brand_config.get("enabled") else [],
        destination_configs=destination_config.get("destinations", []) if destination_config.get("enabled") else [],
        trend_aliases=trend_aliases,
    )
    if not summary.get("brand") and not summary.get("destinations"):
        return None
    return summary


def _load_auction_summary(
    config_loader: ConfigLoader,
    client_config: dict,
    auction_csv: str | Path | None,
) -> dict | None:
    auction_config = client_config.get("auction_insights", {})
    if not auction_config.get("enabled") or not auction_csv:
        return None

    auction_df = load_auction_csv(auction_csv)
    if auction_df.empty:
        return None

    return summarize_auction_insights(
        auction_df,
        client_domain=auction_config.get("client_domain"),
        known_competitors=auction_config.get("known_competitors", []),
    )


def _format_mix_table(mix_df: pd.DataFrame) -> pd.DataFrame:
    if mix_df.empty:
        return pd.DataFrame(columns=["Campaign Type", "Cost", "Sales Leads", "Cost Share", "Lead Share", "CPL"])

    formatted = mix_df.copy()
    formatted["Cost"] = formatted["Cost"].map(lambda value: f"£{value:,.2f}")
    formatted["Sales Leads"] = formatted["Sales Leads"].map(lambda value: f"{int(round(value)):,}")
    formatted["Cost Share"] = formatted["Cost Share"].map(_fmt_percent)
    formatted["Lead Share"] = formatted["Lead Share"].map(_fmt_percent)
    formatted["CPL"] = formatted["CPL"].map(lambda value: f"£{value:,.2f}" if pd.notna(value) else "n/a")
    return formatted[["Campaign Type", "Cost", "Sales Leads", "Cost Share", "Lead Share", "CPL"]]


def _format_trends_table(comparison_df: pd.DataFrame) -> pd.DataFrame:
    if comparison_df.empty:
        return pd.DataFrame(columns=["Month", "Current Value", "Prior Value"])

    formatted = comparison_df.rename(
        columns={
            "month_label": "Month",
            "current_value": "Current Value",
            "prior_value": "Prior Value",
        }
    )[["Month", "Current Value", "Prior Value"]].copy()
    formatted["Current Value"] = formatted["Current Value"].map(lambda value: f"{value:.1f}" if pd.notna(value) else "n/a")
    formatted["Prior Value"] = formatted["Prior Value"].map(lambda value: f"{value:.1f}" if pd.notna(value) else "n/a")
    return formatted


def _fmt_percent(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value * 100:.2f}%"
