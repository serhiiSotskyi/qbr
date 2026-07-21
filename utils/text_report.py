from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

from src.auction_loader import load_auction_csv
from src.auction_metrics import summarize_auction_insights
from src.config_loader import ConfigLoader
from src.data_loader import MonthInfo, QuarterInfo, detect_latest_complete_month, detect_latest_complete_quarter, load_csv
from src.metrics import format_summary_table, prepare_report_data, validate_report_data
from src.narrative_generator import (
    generate_auction_bullets,
    generate_mix_bullets,
    generate_overall_bullets,
    generate_scope_bullets,
    generate_trend_bullets,
)
from src.other_campaigns import (
    describe_other_campaign_filter,
    format_other_top_campaigns_table,
    load_other_campaign_summary,
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

    def add_summary_slide(
        self,
        title: str,
        subtitle: str,
        kpis: Sequence[dict],
        table_df: pd.DataFrame,
        bullets: Iterable[str],
    ) -> None:
        body_parts = [subtitle]
        if kpis:
            kpi_heading = "Key Metrics + MoM + YoY" if any("mom_label" in kpi for kpi in kpis) else "Key Metrics + YoY"
            body_parts.extend(["", kpi_heading, self._render_kpis(kpis)])
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

    def add_other_top_campaigns_slide(
        self,
        title: str,
        subtitle: str,
        top_clicks: pd.DataFrame,
        top_conversions: pd.DataFrame,
        source_files: Sequence[str],
        excluded_terms: Sequence[str],
    ) -> None:
        body_parts = [
            subtitle,
            describe_other_campaign_filter(excluded_terms),
            f"Source files: {', '.join(source_files) if source_files else 'not supplied'}",
        ]
        if not top_clicks.empty:
            body_parts.extend(["", "Top 10 by Clicks", self._render_table(format_other_top_campaigns_table(top_clicks))])
        if not top_conversions.empty:
            body_parts.extend(["", "Top 10 by Conversions", self._render_table(format_other_top_campaigns_table(top_conversions))])
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
        return [f"- {bullet}" for bullet in list(bullets) if str(bullet).strip()]

    @staticmethod
    def _render_table(table_df: pd.DataFrame) -> str:
        safe_df = table_df.copy()
        if safe_df.empty:
            safe_df = pd.DataFrame([{"Status": "No data available"}])
        return safe_df.to_string(index=False)

    @staticmethod
    def _render_kpis(kpis: Sequence[dict]) -> str:
        rows = []
        include_mom = any("mom_label" in kpi for kpi in kpis)
        for kpi in kpis:
            row = {
                "Metric": str(kpi.get("label", "")),
                "Value": str(kpi.get("value", "n/a")),
            }
            if include_mom:
                row["MoM"] = str(kpi.get("mom_label", "n/a"))
            row["YoY"] = str(kpi.get("yoy_label", "n/a"))
            rows.append(row)
        return pd.DataFrame(rows).to_string(index=False)


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
    other_campaigns_summary = context.get("other_campaigns_summary")
    recommendations = context.get("recommendations", [])
    report_mode = str(context.get("report_mode", report.get("report_mode", "quarterly")))

    builder = TextReportBuilder()

    title_text = report_title if not agency_name else f"{client_name} | {report_title}"
    subtitle_text = subtitle if not agency_name else f"{subtitle} | {agency_name}"
    builder.add_title_slide(title_text, subtitle_text)

    if config_loader.is_slide_enabled("include_performance", client_config):
        _build_performance_section(
            builder,
            report,
            subtitle,
            client_config,
            config_loader,
            campaigns,
            other_campaigns_summary,
            report_mode=report_mode,
        )

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
        trends_ytd_current_dir: str | Path | None = None,
        trends_ytd_previous_dir: str | Path | None = None,
        other_campaigns_dir: str | Path | None = None,
        report_mode: str = "quarterly",
    ) -> Path:
        client_config = self.config_loader.get_client_config(client_id)
        df = load_csv(input_csv)
        use_monthly = report_mode == "monthly" and client_config.get("id") in {"wendy_wu", "wendy_wu_australia"}
        quarter = detect_latest_complete_month(df) if use_monthly else detect_latest_complete_quarter(df)
        report = prepare_report_data(
            df,
            quarter,
            campaign_order=self.config_loader.get_campaign_types(client_config),
            destination_order=self.config_loader.get_destinations(client_config),
            destination_aliases=client_config.get("destination_aliases"),
            destination_other_config=client_config.get("destination_other"),
            report_mode="monthly" if use_monthly else "quarterly",
        )
        validate_report_data(report)

        subtitle = _format_period_subtitle(quarter, "monthly" if use_monthly else "quarterly")
        client_name = self.config_loader.get_client_name(client_config)
        report_title = self.config_loader.get_report_title(client_config)
        if use_monthly:
            report_title = report_title.replace("Quarterly", "Monthly")
        agency_name = self.config_loader.get_agency_name(client_config)

        trends_summary = (
            None
            if use_monthly
            else _load_trends_summary(
                self.project_root,
                self.config_loader,
                client_config,
                quarter,
                trends_dir,
                trends_ytd_current_dir=trends_ytd_current_dir,
                trends_ytd_previous_dir=trends_ytd_previous_dir,
            )
        )
        auction_summary = None if use_monthly else _load_auction_summary(self.config_loader, client_config, auction_csv)
        other_campaigns_summary = _load_other_campaigns_summary(client_config, other_campaigns_dir)
        recommendations = [] if use_monthly else generate_recommendations(report, trends_summary=trends_summary, auction_summary=auction_summary)

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
                "other_campaigns_summary": other_campaigns_summary,
                "recommendations": recommendations,
                "report_mode": "monthly" if use_monthly else "quarterly",
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
    other_campaigns_summary: dict | None = None,
    report_mode: str = "quarterly",
) -> None:
    builder.add_divider_slide("Performance")
    use_kpi_cards = _use_kpi_summary_cards(client_config)
    is_monthly = report_mode == "monthly"
    summary_word = "Month" if is_monthly else "Quarter"
    trend_word = "YTD" if is_monthly else "Monthly"

    if config_loader.is_slide_enabled("overview", client_config):
        overall_scope = report["overall"]
        if use_kpi_cards:
            builder.add_summary_slide(
                title=f"Overall {summary_word} Summary",
                subtitle=subtitle,
                kpis=overall_scope["kpis"],
                table_df=format_summary_table(overall_scope["monthly"], report["include_revenue"]),
                bullets=generate_overall_bullets(overall_scope, report["mix_overall"]),
            )
        else:
            builder.add_table_slide(
                title=f"Overall {summary_word} Summary",
                subtitle=subtitle,
                table_df=format_summary_table(overall_scope["monthly"], report["include_revenue"]),
                bullets=generate_scope_bullets("Overall", overall_scope),
            )

        builder.add_trend_slide(
            title=f"Overall {trend_word} Trend" if is_monthly else "Overall Performance Trend",
            subtitle=subtitle,
            table_df=format_summary_table(overall_scope["monthly"], report["include_revenue"]),
            bullets=[] if use_kpi_cards else generate_overall_bullets(overall_scope, report["mix_overall"]),
        )

    if config_loader.is_slide_enabled("campaign_mix", client_config):
        builder.add_mix_slide(
            title="Campaign Type YTD Mix" if is_monthly else "Campaign Type Mix",
            subtitle=subtitle,
            table_df=_format_mix_table(report["mix_overall"]),
            bullets=generate_mix_bullets(report["mix_overall"], "overall"),
        )

    if config_loader.is_slide_enabled("campaign_summary", client_config):
        campaign_names = list(campaigns or report["available_campaigns"])
        for campaign in campaign_names:
            scope = report["campaigns"][campaign]
            summary_bullets = generate_scope_bullets(campaign, scope)
            if use_kpi_cards:
                builder.add_summary_slide(
                    title=f"{campaign} {summary_word} Summary" if is_monthly else f"{campaign} Summary",
                    subtitle=subtitle,
                    kpis=scope["kpis"],
                    table_df=format_summary_table(scope["monthly"], report["include_revenue"]),
                    bullets=summary_bullets,
                )
            else:
                builder.add_table_slide(
                    title=f"{campaign} {summary_word} Summary" if is_monthly else f"{campaign} Summary",
                    subtitle=subtitle,
                    table_df=format_summary_table(scope["monthly"], report["include_revenue"]),
                    bullets=summary_bullets,
                )

            builder.add_trend_slide(
                title=f"{campaign} {trend_word} Trend",
                subtitle=subtitle,
                table_df=format_summary_table(scope["monthly"], report["include_revenue"]),
                bullets=[] if use_kpi_cards else summary_bullets,
            )

    if config_loader.is_slide_enabled("destination_summary", client_config):
        for destination in report["available_destinations"]:
            scope = report["destinations"][destination]
            summary_bullets = generate_scope_bullets(destination, scope)
            if use_kpi_cards:
                builder.add_summary_slide(
                    title=f"{destination} {summary_word} Summary + YoY" if is_monthly else f"{destination} Summary + YoY",
                    subtitle=subtitle,
                    kpis=scope["kpis"],
                    table_df=format_summary_table(scope["monthly"], report["include_revenue"]),
                    bullets=summary_bullets,
                )
            else:
                builder.add_table_slide(
                    title=f"{destination} {summary_word} Summary + YoY" if is_monthly else f"{destination} Summary + YoY",
                    subtitle=subtitle,
                    table_df=format_summary_table(scope["monthly"], report["include_revenue"]),
                    bullets=summary_bullets,
                )

            builder.add_trend_slide(
                title=f"{destination} {trend_word} Trend",
                subtitle=subtitle,
                table_df=format_summary_table(scope["monthly"], report["include_revenue"]),
                bullets=[] if use_kpi_cards else summary_bullets,
            )

            builder.add_mix_slide(
                title=f"{destination} Campaign YTD Mix" if is_monthly else f"{destination} Campaign Mix",
                subtitle=subtitle,
                table_df=_format_mix_table(report["dest_mix"][destination]),
                bullets=generate_mix_bullets(report["dest_mix"][destination], destination),
            )

            if destination == "Other" and other_campaigns_summary:
                builder.add_other_top_campaigns_slide(
                    title="Other (Destination) Top 10 campaigns",
                    subtitle=subtitle,
                    top_clicks=other_campaigns_summary["top_clicks"],
                    top_conversions=other_campaigns_summary["top_conversions"],
                    source_files=other_campaigns_summary.get("source_files", []),
                    excluded_terms=other_campaigns_summary.get("excluded_terms", []),
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
    *,
    trends_ytd_current_dir: str | Path | None = None,
    trends_ytd_previous_dir: str | Path | None = None,
) -> dict | None:
    brand_config = client_config.get("brand_trends", {})
    destination_config = client_config.get("destination_trends", {})
    trend_aliases = client_config.get("trend_aliases", {})
    if not brand_config.get("enabled") and not destination_config.get("enabled"):
        return None
    use_ytd = client_config.get("id") in {"wendy_wu", "wendy_wu_australia"}
    current_trends_dir = trends_ytd_current_dir if use_ytd and trends_ytd_current_dir else trends_dir
    if not current_trends_dir:
        return None

    loader = TrendsLoader(project_root / current_trends_dir if not Path(current_trends_dir).is_absolute() else current_trends_dir)
    trends_df = loader.load_from_directory()
    if trends_df.empty:
        return None
    previous_trends_df = None
    if use_ytd and trends_ytd_previous_dir:
        previous_path = project_root / trends_ytd_previous_dir if not Path(trends_ytd_previous_dir).is_absolute() else trends_ytd_previous_dir
        previous_trends_df = TrendsLoader(previous_path).load_from_directory()
        if previous_trends_df.empty:
            previous_trends_df = None

    summary = summarize_trends(
        trends_df=trends_df,
        quarter=quarter,
        brand_terms=brand_config.get("terms", []) if brand_config.get("enabled") else [],
        destination_configs=destination_config.get("destinations", []) if destination_config.get("enabled") else [],
        trend_aliases=trend_aliases,
        comparison_period="ytd" if use_ytd else "quarter",
        previous_trends_df=previous_trends_df,
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


def _load_other_campaigns_summary(client_config: dict, other_campaigns_dir: str | Path | None) -> dict | None:
    config = client_config.get("other_top_campaigns", {})
    if not config.get("enabled") or not other_campaigns_dir:
        return None
    return load_other_campaign_summary(
        other_campaigns_dir,
        exclude_terms=config.get("exclude_terms", []),
        top_n=int(config.get("top_n", 10)),
    )


def _format_mix_table(mix_df: pd.DataFrame) -> pd.DataFrame:
    if mix_df.empty:
        return pd.DataFrame(columns=["Campaign Type", "Cost", "Sales Leads", "Cost Share", "Lead Share", "CPL"])

    formatted = mix_df.copy()
    formatted["Cost"] = [
        f"£{value:,.2f}{_fmt_inline_yoy(yoy)}"
        for value, yoy in zip(formatted["Cost"], formatted.get("Cost YoY", pd.Series([None] * len(formatted))))
    ]
    formatted["Sales Leads"] = [
        f"{int(round(value)):,}{_fmt_inline_yoy(yoy)}"
        for value, yoy in zip(formatted["Sales Leads"], formatted.get("Sales Leads YoY", pd.Series([None] * len(formatted))))
    ]
    formatted["Cost Share"] = formatted["Cost Share"].map(_fmt_percent)
    formatted["Lead Share"] = formatted["Lead Share"].map(_fmt_percent)
    formatted["Cost Share"] = [
        f"{value}{_fmt_inline_yoy(yoy)}"
        for value, yoy in zip(formatted["Cost Share"], formatted.get("Cost Share YoY", pd.Series([None] * len(formatted))))
    ]
    formatted["Lead Share"] = [
        f"{value}{_fmt_inline_yoy(yoy)}"
        for value, yoy in zip(formatted["Lead Share"], formatted.get("Lead Share YoY", pd.Series([None] * len(formatted))))
    ]
    formatted["CPL"] = [
        f"{f'£{value:,.2f}' if pd.notna(value) else 'n/a'}{_fmt_inline_yoy(yoy)}"
        for value, yoy in zip(formatted["CPL"], formatted.get("CPL YoY", pd.Series([None] * len(formatted))))
    ]
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


def _fmt_inline_yoy(value: float | None) -> str:
    if value is None or pd.isna(value):
        return ""
    return f" ({value * 100:+.0f}%)"


def _use_kpi_summary_cards(client_config: dict) -> bool:
    return client_config.get("id") in {"wendy_wu", "wendy_wu_australia"}


def _format_period_subtitle(period: QuarterInfo | MonthInfo, report_mode: str) -> str:
    if report_mode == "monthly" and isinstance(period, MonthInfo):
        return f"{period.label} (YTD Jan - {period.start.strftime('%b %Y')})"
    return f"{period.label} ({period.start.strftime('%b')} - {period.end.strftime('%b %Y')})"
