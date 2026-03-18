from __future__ import annotations

from pathlib import Path

from .auction_loader import load_auction_csv
from .auction_metrics import summarize_auction_insights
from .chart_builder import ChartBuilder
from .config_loader import ConfigLoader
from .data_loader import detect_latest_complete_quarter, load_csv
from .metrics import format_summary_table, prepare_report_data, validate_report_data
from .narrative_generator import (
    generate_auction_bullets,
    generate_mix_bullets,
    generate_overall_bullets,
    generate_scope_bullets,
    generate_trend_bullets,
)
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
    ) -> Path:
        client_config = self.config_loader.get_client_config(client_id)
        template_path = self.config_loader.get_template_path(self.project_root, client_config)

        df = load_csv(input_csv)
        quarter = detect_latest_complete_quarter(df)
        report = prepare_report_data(
            df,
            quarter,
            campaign_order=self.config_loader.get_campaign_types(client_config),
            destination_order=self.config_loader.get_destinations(client_config),
        )
        validate_report_data(report)

        output_path = Path(output_pptx) if output_pptx else self.output_root / f"{client_config['id']}_{quarter.label}.pptx"
        subtitle = f"{quarter.label} ({quarter.start.strftime('%b')} - {quarter.end.strftime('%b %Y')})"
        client_name = self.config_loader.get_client_name(client_config)
        report_title = self.config_loader.get_report_title(client_config)
        agency_name = self.config_loader.get_agency_name(client_config)
        chart_styles = self.config_loader.get_chart_styles()

        chart_builder = ChartBuilder(self.charts_root / f"{client_config['id']}/{quarter.year}_Q{quarter.quarter}", chart_styles=chart_styles)
        builder = SlideBuilder(template_path, chart_styles=chart_styles)

        title_text = report_title if not agency_name else f"{client_name} | {report_title}"
        subtitle_text = subtitle if not agency_name else f"{subtitle} | {agency_name}"
        builder.add_title_slide(title_text, subtitle_text)

        if self.config_loader.is_slide_enabled("include_performance", client_config):
            self._build_performance_section(builder, chart_builder, report, subtitle, client_config)

        trends_summary = self._load_trends_summary(client_config, quarter, trends_dir)
        if self.config_loader.is_slide_enabled("include_trends", client_config) and trends_summary:
            self._build_trends_section(builder, chart_builder, trends_summary, subtitle, client_config)

        auction_summary = self._load_auction_summary(client_config, auction_csv)
        if self.config_loader.is_slide_enabled("include_auction_insights", client_config) and auction_summary:
            self._build_auction_section(builder, auction_summary, subtitle, client_config)

        recommendations = generate_recommendations(report, trends_summary=trends_summary, auction_summary=auction_summary)
        if self.config_loader.is_slide_enabled("include_recommendations", client_config) and recommendations:
            builder.add_divider_slide("Recommendations")
            builder.add_recommendations_slide(
                title="Recommendations / Next Steps",
                subtitle=subtitle,
                recommendations=recommendations,
            )

        builder.save(output_path)
        return output_path

    def _build_performance_section(self, builder: SlideBuilder, chart_builder: ChartBuilder, report: dict, subtitle: str, client_config: dict) -> None:
        builder.add_divider_slide("Performance")

        if self.config_loader.is_slide_enabled("overview", client_config):
            overall_charts = chart_builder.build_scope_trend_charts("overall", report["overall"]["monthly"])
            builder.add_trend_slide(
                title="Overall Performance Trend",
                subtitle=subtitle,
                cpl_cvr_chart_path=overall_charts["cpl_cvr"],
                cost_leads_chart_path=overall_charts["cost_leads"],
                bullets=generate_overall_bullets(report["overall"], report["mix_overall"]),
            )

            overall_table = format_summary_table(report["overall"]["monthly"], report["include_revenue"])
            builder.add_table_slide(
                title="Overall Quarter Summary",
                subtitle=subtitle,
                table_df=overall_table,
                bullets=generate_scope_bullets("Overall", report["overall"]),
            )

        if self.config_loader.is_slide_enabled("campaign_mix", client_config):
            mix_charts = chart_builder.build_mix_charts("overall", report["mix_overall"])
            builder.add_mix_slide(
                title="Campaign Type Mix",
                subtitle=subtitle,
                cost_mix_chart_path=mix_charts["cost_share"],
                leads_mix_chart_path=mix_charts["leads_share"],
                bullets=generate_mix_bullets(report["mix_overall"], "overall"),
            )

        if self.config_loader.is_slide_enabled("campaign_summary", client_config):
            for campaign in report["available_campaigns"]:
                scope = report["campaigns"][campaign]
                table_df = format_summary_table(scope["monthly"], report["include_revenue"])
                builder.add_table_slide(
                    title=f"{campaign} Summary",
                    subtitle=subtitle,
                    table_df=table_df,
                    bullets=generate_scope_bullets(campaign, scope),
                )

                scope_charts = chart_builder.build_scope_trend_charts(
                    f"campaign_{_slug(campaign)}", scope["monthly"]
                )
                builder.add_trend_slide(
                    title=f"{campaign} Monthly Trend",
                    subtitle=subtitle,
                    cpl_cvr_chart_path=scope_charts["cpl_cvr"],
                    cost_leads_chart_path=scope_charts["cost_leads"],
                    bullets=generate_scope_bullets(campaign, scope),
                )

        if self.config_loader.is_slide_enabled("destination_summary", client_config):
            for destination in report["available_destinations"]:
                scope = report["destinations"][destination]
                table_df = format_summary_table(scope["monthly"], report["include_revenue"])
                builder.add_table_slide(
                    title=f"{destination} Summary + YoY",
                    subtitle=subtitle,
                    table_df=table_df,
                    bullets=generate_scope_bullets(destination, scope),
                )

                scope_charts = chart_builder.build_scope_trend_charts(
                    f"destination_{_slug(destination)}", scope["monthly"]
                )
                builder.add_trend_slide(
                    title=f"{destination} Monthly Trend",
                    subtitle=subtitle,
                    cpl_cvr_chart_path=scope_charts["cpl_cvr"],
                    cost_leads_chart_path=scope_charts["cost_leads"],
                    bullets=generate_scope_bullets(destination, scope),
                )

                mix_df = report["dest_mix"][destination]
                mix_charts = chart_builder.build_mix_charts(f"destination_{_slug(destination)}", mix_df)
                builder.add_mix_slide(
                    title=f"{destination} Campaign Mix",
                    subtitle=subtitle,
                    cost_mix_chart_path=mix_charts["cost_share"],
                    leads_mix_chart_path=mix_charts["leads_share"],
                    bullets=generate_mix_bullets(mix_df, destination),
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


def _slug(value: str) -> str:
    return (
        value.strip()
        .lower()
        .replace(" ", "_")
        .replace("/", "_")
        .replace("-", "_")
    )
