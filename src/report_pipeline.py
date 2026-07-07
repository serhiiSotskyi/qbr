from __future__ import annotations

from pathlib import Path

from .auction_loader import load_auction_csv
from .auction_metrics import summarize_auction_insights
from .chart_builder import ChartBuilder
from .config_loader import ConfigLoader
from .data_loader import MonthInfo, QuarterInfo, detect_latest_complete_month, detect_latest_complete_quarter, load_csv
from .metrics import format_summary_table, prepare_report_data, validate_report_data
from .narrative_generator import (
    generate_auction_bullets,
    generate_mix_bullets,
    generate_overall_bullets,
    generate_scope_bullets,
    generate_trend_bullets,
)
from .other_campaigns import load_other_campaign_summary
from .recommendation_generator import generate_recommendations
from .slide_builder import SlideBuilder
from .trends_loader import TrendsLoader
from .trends_metrics import summarize_trends


class ReportPipeline:
    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root)
        self.charts_root = self.project_root / "charts"
        self.output_root = self.project_root / "output"
        self.config_loader = ConfigLoader(
            report_config_path=self.project_root / "config" / "report_config.yaml",
            chart_styles_path=self.project_root / "config" / "chart_styles.yaml",
            clients_config_path=self.project_root / "config" / "clients_config.json",
        )

    def run(
        self,
        input_csv: str | Path,
        output_pptx: str | Path | None = None,
        client_id: str | None = None,
        auction_csv: str | Path | None = None,
        trends_dir: str | Path | None = None,
        other_campaigns_dir: str | Path | None = None,
        report_mode: str = "quarterly",
    ) -> Path:
        client_config = self.config_loader.get_client_config(client_id)
        template_path = self.config_loader.get_template_path(self.project_root, client_config)

        df = load_csv(input_csv)
        use_monthly = report_mode == "monthly" and client_config.get("id") in {"wendy_wu", "wendy_wu_australia"}
        quarter = detect_latest_complete_month(df) if use_monthly else detect_latest_complete_quarter(df)
        report = prepare_report_data(
            df,
            quarter,
            campaign_order=self.config_loader.get_campaign_types(client_config),
            destination_order=self.config_loader.get_destinations(client_config),
            destination_other_config=client_config.get("destination_other"),
            report_mode="monthly" if use_monthly else "quarterly",
        )
        validate_report_data(report)

        output_path = Path(output_pptx) if output_pptx else self.output_root / f"{client_config['id']}_{quarter.label}.pptx"
        subtitle = _format_period_subtitle(quarter, "monthly" if use_monthly else "quarterly")
        client_name = self.config_loader.get_client_name(client_config)
        report_title = self.config_loader.get_report_title(client_config)
        if use_monthly:
            report_title = report_title.replace("Quarterly", "Monthly")
        agency_name = self.config_loader.get_agency_name(client_config)
        chart_styles = self.config_loader.get_chart_styles(client_config)

        chart_builder = ChartBuilder(self.charts_root / f"{client_config['id']}/{_period_chart_dir(quarter, use_monthly)}", chart_styles=chart_styles)
        builder = SlideBuilder(template_path, chart_styles=chart_styles)
        other_campaigns_summary = self._load_other_campaigns_summary(client_config, other_campaigns_dir)

        title_text = report_title if not agency_name else f"{client_name} | {report_title}"
        subtitle_text = subtitle if not agency_name else f"{subtitle} | {agency_name}"
        builder.add_title_slide(title_text, subtitle_text)

        if self.config_loader.is_slide_enabled("include_performance", client_config):
            self._build_performance_section(builder, chart_builder, report, subtitle, client_config, other_campaigns_summary, report_mode="monthly" if use_monthly else "quarterly")

        trends_summary = None if use_monthly else self._load_trends_summary(client_config, quarter, trends_dir)
        if not use_monthly and self.config_loader.is_slide_enabled("include_trends", client_config) and trends_summary:
            self._build_trends_section(builder, chart_builder, trends_summary, subtitle, client_config)

        auction_summary = None if use_monthly else self._load_auction_summary(client_config, auction_csv)
        if not use_monthly and self.config_loader.is_slide_enabled("include_auction_insights", client_config) and auction_summary:
            self._build_auction_section(builder, auction_summary, subtitle, client_config)

        recommendations = [] if use_monthly else generate_recommendations(report, trends_summary=trends_summary, auction_summary=auction_summary)
        if not use_monthly and self.config_loader.is_slide_enabled("include_recommendations", client_config) and recommendations:
            builder.add_divider_slide("Recommendations")
            builder.add_recommendations_slide(
                title="Recommendations / Next Steps",
                subtitle=subtitle,
                recommendations=recommendations,
            )

        builder.save(output_path)
        return output_path

    def _build_performance_section(
        self,
        builder: SlideBuilder,
        chart_builder: ChartBuilder,
        report: dict,
        subtitle: str,
        client_config: dict,
        other_campaigns_summary: dict | None = None,
        report_mode: str = "quarterly",
    ) -> None:
        builder.add_divider_slide("Performance")
        use_kpi_cards = _use_kpi_summary_cards(client_config)
        is_monthly = report_mode == "monthly"
        summary_word = "Month" if is_monthly else "Quarter"
        trend_word = "YTD" if is_monthly else "Monthly"

        if self.config_loader.is_slide_enabled("overview", client_config):
            overall_charts = chart_builder.build_scope_trend_charts("overall", report["overall"]["monthly"])
            if use_kpi_cards:
                builder.add_summary_cards_slide(
                    title=f"Overall {summary_word} Summary",
                    subtitle=subtitle,
                    kpis=report["overall"]["kpis"],
                    bullets=generate_overall_bullets(report["overall"], report["mix_overall"]),
                )
            else:
                overall_table = format_summary_table(report["overall"]["monthly"], report["include_revenue"])
                builder.add_table_slide(
                    title=f"Overall {summary_word} Summary",
                    subtitle=subtitle,
                    table_df=overall_table,
                    bullets=generate_scope_bullets("Overall", report["overall"]),
                )

            builder.add_trend_slide(
                title=f"Overall {trend_word} Trend" if is_monthly else "Overall Performance Trend",
                subtitle=subtitle,
                cpl_cvr_chart_path=overall_charts["cpl_cvr"],
                cost_leads_chart_path=overall_charts["cost_leads"],
                bullets=[] if use_kpi_cards else generate_overall_bullets(report["overall"], report["mix_overall"]),
                use_template=not use_kpi_cards,
            )

        if self.config_loader.is_slide_enabled("campaign_mix", client_config):
            mix_charts = chart_builder.build_mix_charts("overall", report["mix_overall"])
            builder.add_mix_slide(
                title="Campaign Type YTD Mix" if is_monthly else "Campaign Type Mix",
                subtitle=subtitle,
                cost_mix_chart_path=mix_charts["cost_share"],
                leads_mix_chart_path=mix_charts["leads_share"],
                bullets=generate_mix_bullets(report["mix_overall"], "overall"),
            )

        if self.config_loader.is_slide_enabled("campaign_summary", client_config):
            for campaign in report["available_campaigns"]:
                scope = report["campaigns"][campaign]
                summary_bullets = generate_scope_bullets(campaign, scope)
                if use_kpi_cards:
                    builder.add_summary_cards_slide(
                        title=f"{campaign} {summary_word} Summary" if is_monthly else f"{campaign} Summary",
                        subtitle=subtitle,
                        kpis=scope["kpis"],
                        bullets=summary_bullets,
                    )
                else:
                    table_df = format_summary_table(scope["monthly"], report["include_revenue"])
                    builder.add_table_slide(
                        title=f"{campaign} {summary_word} Summary" if is_monthly else f"{campaign} Summary",
                        subtitle=subtitle,
                        table_df=table_df,
                        bullets=summary_bullets,
                    )

                scope_charts = chart_builder.build_scope_trend_charts(
                    f"campaign_{_slug(campaign)}", scope["monthly"]
                )
                builder.add_trend_slide(
                    title=f"{campaign} {trend_word} Trend",
                    subtitle=subtitle,
                    cpl_cvr_chart_path=scope_charts["cpl_cvr"],
                    cost_leads_chart_path=scope_charts["cost_leads"],
                    bullets=[] if use_kpi_cards else summary_bullets,
                    use_template=not use_kpi_cards,
                )

        if self.config_loader.is_slide_enabled("destination_summary", client_config):
            for destination in report["available_destinations"]:
                scope = report["destinations"][destination]
                summary_bullets = generate_scope_bullets(destination, scope)
                if use_kpi_cards:
                    builder.add_summary_cards_slide(
                        title=f"{destination} {summary_word} Summary + YoY" if is_monthly else f"{destination} Summary + YoY",
                        subtitle=subtitle,
                        kpis=scope["kpis"],
                        bullets=summary_bullets,
                    )
                else:
                    table_df = format_summary_table(scope["monthly"], report["include_revenue"])
                    builder.add_table_slide(
                        title=f"{destination} {summary_word} Summary + YoY" if is_monthly else f"{destination} Summary + YoY",
                        subtitle=subtitle,
                        table_df=table_df,
                        bullets=summary_bullets,
                    )

                scope_charts = chart_builder.build_scope_trend_charts(
                    f"destination_{_slug(destination)}", scope["monthly"]
                )
                builder.add_trend_slide(
                    title=f"{destination} {trend_word} Trend",
                    subtitle=subtitle,
                    cpl_cvr_chart_path=scope_charts["cpl_cvr"],
                    cost_leads_chart_path=scope_charts["cost_leads"],
                    bullets=[] if use_kpi_cards else summary_bullets,
                    use_template=not use_kpi_cards,
                )

                mix_df = report["dest_mix"][destination]
                mix_charts = chart_builder.build_mix_charts(f"destination_{_slug(destination)}", mix_df)
                builder.add_mix_slide(
                    title=f"{destination} Campaign YTD Mix" if is_monthly else f"{destination} Campaign Mix",
                    subtitle=subtitle,
                    cost_mix_chart_path=mix_charts["cost_share"],
                    leads_mix_chart_path=mix_charts["leads_share"],
                    bullets=generate_mix_bullets(mix_df, destination),
                )

                if destination == "Other" and other_campaigns_summary:
                    top_charts = chart_builder.build_other_top_campaign_charts(
                        "destination_other",
                        other_campaigns_summary["top_clicks"],
                        other_campaigns_summary["top_conversions"],
                    )
                    builder.add_other_top_campaigns_slide(
                        title="Other (Destination) Top 10 campaigns",
                        subtitle=subtitle,
                        clicks_chart_path=top_charts["top_clicks"],
                        conversions_chart_path=top_charts["top_conversions"],
                    )

    def _build_trends_section(
        self,
        builder: SlideBuilder,
        chart_builder: ChartBuilder,
        trends_summary: dict,
        subtitle: str,
        client_config: dict,
    ) -> None:
        builder.add_divider_slide("Google Trends")
        source_note = self.config_loader.get_source_note("google_trends", client_config)

        brand_summary = trends_summary.get("brand")
        if brand_summary:
            brand_chart = chart_builder.build_trends_chart(
                "brand",
                brand_summary["comparison"],
                title="Brand Search Interest",
            )
            builder.add_single_chart_slide(
                title=f"{self.config_loader.get_client_name(client_config)} Terms Are Growing",
                subtitle=subtitle,
                chart_path=brand_chart,
                bullets=generate_trend_bullets(brand_summary, "Brand"),
                source_note=source_note,
            )

        for destination_summary in trends_summary.get("destinations", []):
            chart_path = chart_builder.build_trends_chart(
                f"trend_{_slug(destination_summary['name'])}",
                destination_summary["comparison"],
                title=f"{destination_summary['name']} Search Interest",
            )
            builder.add_single_chart_slide(
                title=f"{destination_summary['name']} Demand Trend",
                subtitle=subtitle,
                chart_path=chart_path,
                bullets=generate_trend_bullets(destination_summary, destination_summary["name"]),
                source_note=source_note,
            )

    def _build_auction_section(self, builder: SlideBuilder, auction_summary: dict, subtitle: str, client_config: dict) -> None:
        builder.add_divider_slide("Auction Insights")
        builder.add_auction_insights_slide(
            title="Brand coverage is very strong",
            subtitle=subtitle,
            table_df=auction_summary["table"],
            bullets=generate_auction_bullets(auction_summary),
            source_note=self.config_loader.get_source_note("auction_insights", client_config),
        )

    def _load_trends_summary(self, client_config: dict, quarter, trends_dir: str | Path | None) -> dict | None:
        brand_config = client_config.get("brand_trends", {})
        destination_config = client_config.get("destination_trends", {})
        trend_aliases = client_config.get("trend_aliases", {})
        if not brand_config.get("enabled") and not destination_config.get("enabled"):
            return None
        if not trends_dir:
            return None

        loader = TrendsLoader(trends_dir)
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
            print("No matching trend terms found. Check config or CSV column names.")
            return None
        return summary

    def _load_auction_summary(self, client_config: dict, auction_csv: str | Path | None) -> dict | None:
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

    def _load_other_campaigns_summary(self, client_config: dict, other_campaigns_dir: str | Path | None) -> dict | None:
        config = client_config.get("other_top_campaigns", {})
        if not config.get("enabled") or not other_campaigns_dir:
            return None
        return load_other_campaign_summary(
            other_campaigns_dir,
            exclude_terms=config.get("exclude_terms", []),
            top_n=int(config.get("top_n", 10)),
        )


def _slug(value: str) -> str:
    return (
        value.strip()
        .lower()
        .replace(" ", "_")
        .replace("/", "_")
        .replace("-", "_")
    )


def _use_kpi_summary_cards(client_config: dict) -> bool:
    return client_config.get("id") in {"wendy_wu", "wendy_wu_australia"}


def _format_period_subtitle(period: QuarterInfo | MonthInfo, report_mode: str) -> str:
    if report_mode == "monthly" and isinstance(period, MonthInfo):
        return f"{period.label} (YTD Jan - {period.start.strftime('%b %Y')})"
    return f"{period.label} ({period.start.strftime('%b')} - {period.end.strftime('%b %Y')})"


def _period_chart_dir(period: QuarterInfo | MonthInfo, is_monthly: bool) -> str:
    if is_monthly and isinstance(period, MonthInfo):
        return f"{period.year}_{period.month:02d}_monthly"
    return f"{period.year}_Q{period.quarter}"
