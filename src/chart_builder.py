from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
import matplotlib
import pandas as pd

matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter


class ChartBuilder:
    def __init__(self, charts_dir: str | Path, chart_styles: Dict | None = None) -> None:
        self.charts_dir = Path(charts_dir)
        self.charts_dir.mkdir(parents=True, exist_ok=True)
        mpl_dir = self.charts_dir / ".mplconfig"
        mpl_dir.mkdir(parents=True, exist_ok=True)
        os.environ["MPLCONFIGDIR"] = str(mpl_dir)
        self.chart_styles = chart_styles or {}
        self.colors = self.chart_styles.get("colors", {})
        figure_size = self.chart_styles.get("figure_size", {})
        fonts = self.chart_styles.get("fonts", {})
        self.figure_size = (
            float(figure_size.get("width", 10)),
            float(figure_size.get("height", 5)),
        )
        self.title_size = int(fonts.get("title_size", 16))
        self.body_size = int(fonts.get("body_size", 11))

    def build_scope_trend_charts(self, scope_key: str, monthly_table: pd.DataFrame) -> Dict[str, Path]:
        chart1 = self._plot_cpl_cvr(scope_key, monthly_table)
        chart2 = self._plot_cost_leads(scope_key, monthly_table)
        return {"cpl_cvr": chart1, "cost_leads": chart2}

    def build_monthly_scope_trend_charts(
        self,
        scope_key: str,
        monthly_table: pd.DataFrame,
        prior_monthly_table: pd.DataFrame | None,
        include_revenue: bool,
    ) -> Dict[str, Path]:
        charts = {
            "cpl_cvr": self._plot_cpl_cvr(scope_key, monthly_table),
            "leads_yoy": self._plot_yoy_metric(
                scope_key,
                monthly_table,
                prior_monthly_table,
                metric_col="Sales Leads",
                title="Leads YoY",
                suffix="leads_yoy",
                value_format="number",
            ),
        }
        if include_revenue:
            charts["revenue_yoy"] = self._plot_yoy_metric(
                scope_key,
                monthly_table,
                prior_monthly_table,
                metric_col="Revenue",
                title="Revenue YoY",
                suffix="revenue_yoy",
                value_format="currency",
            )
        return charts

    def build_mix_charts(self, scope_key: str, mix_df: pd.DataFrame) -> Dict[str, Path]:
        cost_path = self._plot_mix_pie(scope_key, mix_df, value_col="Cost", suffix="cost_share")
        leads_path = self._plot_mix_pie(scope_key, mix_df, value_col="Sales Leads", suffix="leads_share")
        return {"cost_share": cost_path, "leads_share": leads_path}

    def build_trends_chart(
        self,
        scope_key: str,
        comparison_df: pd.DataFrame,
        title: str,
        current_label: str = "Current period",
        prior_label: str = "Prior year",
    ) -> Path:
        out_path = self.charts_dir / f"{scope_key}_trend.png"
        if comparison_df.empty:
            return self._plot_empty_state(out_path, "No trend data")

        fig, ax = plt.subplots(figsize=self.figure_size)
        ax.plot(
            comparison_df["month_label"],
            comparison_df["current_value"],
            marker="o",
            linewidth=2.5,
            color=self.colors.get("trend_current", "#0E7490"),
            label=current_label,
        )

        if "prior_value" in comparison_df.columns and comparison_df["prior_value"].notna().any():
            ax.plot(
                comparison_df["month_label"],
                comparison_df["prior_value"],
                marker="o",
                linewidth=2,
                linestyle="--",
                color=self.colors.get("trend_prior", "#94A3B8"),
                label=prior_label,
            )

        ax.set_title(title, fontsize=self.title_size)
        ax.set_xlabel("Month", fontsize=self.body_size)
        ax.set_ylabel("Interest", fontsize=self.body_size)
        ax.tick_params(axis="both", labelsize=self.body_size)
        ax.grid(axis="y", alpha=0.2)
        ax.legend(loc="upper left", fontsize=self.body_size)

        plt.tight_layout()
        fig.savefig(out_path, dpi=180)
        plt.close(fig)
        return out_path

    def build_other_top_campaign_charts(self, scope_key: str, top_clicks: pd.DataFrame, top_conversions: pd.DataFrame) -> Dict[str, Path]:
        clicks_path = self._plot_other_campaign_bar_chart(
            self.charts_dir / f"{scope_key}_top_clicks.png",
            top_clicks,
            value_col="Clicks",
            title="TOP 10 CAMPAIGNS BY CLICKS",
            x_label="Clicks",
            colors=[self.colors.get("cost", "#C32026"), "#DD726E"],
        )
        conversions_path = self._plot_other_campaign_bar_chart(
            self.charts_dir / f"{scope_key}_top_conversions.png",
            top_conversions,
            value_col="Conversions",
            title="TOP 10 CAMPAIGNS BY CONVERSIONS",
            x_label="Conversions",
            colors=[self.colors.get("leads", "#111111"), "#444444"],
        )
        return {"top_clicks": clicks_path, "top_conversions": conversions_path}

    def _plot_cpl_cvr(self, scope_key: str, monthly_table: pd.DataFrame) -> Path:
        out_path = self.charts_dir / f"{scope_key}_cpl_cvr.png"
        fig, ax1, ax2 = self.build_cpl_cvr_figure(monthly_table)

        plt.tight_layout()
        fig.savefig(out_path, dpi=180)
        plt.close(fig)
        return out_path

    def _plot_cost_leads(self, scope_key: str, monthly_table: pd.DataFrame) -> Path:
        out_path = self.charts_dir / f"{scope_key}_cost_leads.png"
        fig, ax1, ax2 = self.build_cost_leads_figure(monthly_table)

        plt.tight_layout()
        fig.savefig(out_path, dpi=180)
        plt.close(fig)
        return out_path

    def _plot_yoy_metric(
        self,
        scope_key: str,
        monthly_table: pd.DataFrame,
        prior_monthly_table: pd.DataFrame | None,
        *,
        metric_col: str,
        title: str,
        suffix: str,
        value_format: str,
    ) -> Path:
        out_path = self.charts_dir / f"{scope_key}_{suffix}.png"
        if metric_col not in monthly_table.columns:
            return self._plot_empty_state(out_path, f"No {metric_col} data")

        current_df = monthly_table[monthly_table["Month"] != "Total"].copy()
        if current_df.empty:
            return self._plot_empty_state(out_path, f"No {metric_col} data")

        months = current_df["Month"].tolist()
        prior_df = pd.DataFrame()
        if prior_monthly_table is not None and not prior_monthly_table.empty and metric_col in prior_monthly_table.columns:
            prior_df = prior_monthly_table[prior_monthly_table["Month"] != "Total"].copy()
            prior_df = prior_df.set_index("Month").reindex(months).reset_index()

        current_values = pd.to_numeric(current_df[metric_col], errors="coerce").fillna(0).tolist()
        prior_values = (
            pd.to_numeric(prior_df[metric_col], errors="coerce").fillna(0).tolist()
            if not prior_df.empty and metric_col in prior_df.columns
            else [0] * len(months)
        )
        x_positions = list(range(len(months)))
        width = 0.34

        fig, ax = plt.subplots(figsize=self.figure_size)
        current_bars = ax.bar(
            [position - width / 2 for position in x_positions],
            current_values,
            width=width,
            color=self.colors.get("trend_current", "#C32026"),
            label="Current YTD",
        )
        prior_bars = ax.bar(
            [position + width / 2 for position in x_positions],
            prior_values,
            width=width,
            color=self.colors.get("trend_prior", "#8E8E8E"),
            label="Prior-year YTD",
        )

        ax.set_title(title, fontsize=self.title_size)
        ax.set_xlabel("Month", fontsize=self.body_size)
        ax.set_ylabel(metric_col, fontsize=self.body_size)
        ax.set_xticks(x_positions, months)
        ax.tick_params(axis="both", labelsize=self.body_size)
        ax.grid(axis="y", alpha=0.2)
        if value_format == "currency":
            ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: self._format_currency_axis(value)))
        else:
            ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.11), ncol=2, fontsize=self.body_size, frameon=False)

        max_value = max([*current_values, *prior_values, 0])
        ax.set_ylim(top=max_value * 1.18 if max_value > 0 else 1)
        for bars in (current_bars, prior_bars):
            labels = [
                self._format_currency_label(float(bar.get_height()), abbreviated=True)
                if value_format == "currency" and bar.get_height()
                else f"{bar.get_height():,.0f}" if bar.get_height() else ""
                for bar in bars
            ]
            ax.bar_label(bars, labels=labels, padding=3, fontsize=self.body_size - 1)

        plt.tight_layout()
        fig.savefig(out_path, dpi=180)
        plt.close(fig)
        return out_path

    def build_cpl_cvr_figure(self, monthly_table: pd.DataFrame):
        df = monthly_table[monthly_table["Month"] != "Total"].copy()
        months = df["Month"].tolist()
        cpl = df["CPL"].tolist()
        cvr = [x * 100 if x is not None else None for x in df["CVR"].tolist()]

        fig, ax1 = plt.subplots(figsize=self.figure_size)
        ax2 = ax1.twinx()

        cpl_line = ax1.plot(
            months,
            cpl,
            marker="o",
            markersize=7,
            color=self.colors.get("cpl", "#C32026"),
            linewidth=2.5,
            label="CPL (£)",
            zorder=3,
        )[0]
        cvr_line = ax2.plot(
            months,
            cvr,
            marker="o",
            markersize=7,
            color=self.colors.get("cvr", "#111111"),
            linewidth=3,
            label="CVR (%)",
            zorder=4,
        )[0]

        ax1.set_title("CPL vs CVR", fontsize=self.title_size)
        ax1.set_xlabel("Month", fontsize=self.body_size)
        ax1.set_ylabel("CPL (£)", fontsize=self.body_size)
        ax2.set_ylabel("CVR (%)", fontsize=self.body_size)
        ax1.tick_params(axis="both", labelsize=self.body_size)
        ax2.tick_params(axis="both", labelsize=self.body_size)
        ax1.grid(axis="y", alpha=0.2)

        for index, value in enumerate(cpl):
            if value is None or pd.isna(value):
                continue
            ax1.annotate(
                self._format_currency_label(value),
                xy=(index, value),
                xytext=(0, 10),
                textcoords="offset points",
                ha="center",
                fontsize=self.body_size - 1,
                color=self.colors.get("cpl", "#C32026"),
            )

        for index, value in enumerate(cvr):
            if value is None or pd.isna(value):
                continue
            ax2.annotate(
                f"{value:.1f}%",
                xy=(index, value),
                xytext=(0, -16),
                textcoords="offset points",
                ha="center",
                fontsize=self.body_size - 1,
                color=self.colors.get("cvr", "#111111"),
            )

        ax1.legend([cpl_line, cvr_line], ["CPL (£)", "CVR (%)"], loc="upper left", fontsize=self.body_size)
        return fig, ax1, ax2

    def build_cost_leads_figure(self, monthly_table: pd.DataFrame):
        df = monthly_table[monthly_table["Month"] != "Total"].copy()
        months = df["Month"].tolist()
        cost = df["Cost"].tolist()
        leads = df["Sales Leads"].tolist()

        fig, ax1 = plt.subplots(figsize=self.figure_size)
        ax2 = ax1.twinx()

        bars = ax1.bar(
            months,
            cost,
            color=self.colors.get("cost", "#D83A40"),
            alpha=0.9,
            label="Cost (£)",
            zorder=2,
        )
        leads_line = ax2.plot(
            months,
            leads,
            marker="o",
            markersize=7,
            color=self.colors.get("leads", "#111111"),
            linewidth=2.75,
            label="Sales Leads",
            zorder=4,
        )[0]

        ax1.set_title("Cost vs Sales Leads", fontsize=self.title_size)
        ax1.set_xlabel("Month", fontsize=self.body_size)
        ax1.set_ylabel("Cost (£)", fontsize=self.body_size)
        ax2.set_ylabel("Sales Leads", fontsize=self.body_size)
        ax1.tick_params(axis="both", labelsize=self.body_size)
        ax2.tick_params(axis="both", labelsize=self.body_size)
        ax1.grid(axis="y", alpha=0.2)

        for bar, value in zip(bars, cost):
            if value is None or pd.isna(value):
                continue
            ax1.annotate(
                self._format_currency_label(value, abbreviated=True),
                xy=(bar.get_x() + bar.get_width() / 2, bar.get_height()),
                xytext=(0, 5),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=self.body_size - 1,
                color=self.colors.get("cost", "#D83A40"),
            )

        for index, value in enumerate(leads):
            if value is None or pd.isna(value):
                continue
            ax2.annotate(
                f"{int(round(value)):,}",
                xy=(index, value),
                xytext=(0, -16),
                textcoords="offset points",
                ha="center",
                fontsize=self.body_size - 1,
                color=self.colors.get("leads", "#111111"),
            )

        ax1.legend([bars, leads_line], ["Cost (£)", "Sales Leads"], loc="upper left", fontsize=self.body_size)
        return fig, ax1, ax2

    def _plot_mix_pie(self, scope_key: str, mix_df: pd.DataFrame, value_col: str, suffix: str) -> Path:
        out_path = self.charts_dir / f"{scope_key}_{suffix}.png"
        if mix_df.empty or mix_df[value_col].sum() == 0:
            return self._plot_empty_state(out_path, "No data")

        chart_df = mix_df[mix_df[value_col] > 0].copy()

        fig, ax = plt.subplots(figsize=self.figure_size)
        ax.pie(
            chart_df[value_col],
            labels=chart_df["Campaign Type"],
            autopct=lambda p: f"{p:.1f}%" if p >= 2 else "",
            startangle=90,
            textprops={"fontsize": self.body_size},
        )
        ax.axis("equal")
        plt.tight_layout()
        fig.savefig(out_path, dpi=180)
        plt.close(fig)
        return out_path

    def _plot_other_campaign_bar_chart(
        self,
        out_path: Path,
        table_df: pd.DataFrame,
        *,
        value_col: str,
        title: str,
        x_label: str,
        colors: list[str],
    ) -> Path:
        if table_df.empty or value_col not in table_df.columns or float(table_df[value_col].fillna(0).sum()) <= 0:
            return self._plot_empty_state(out_path, "No campaign data")

        chart_df = table_df[["Campaign", value_col]].copy().head(10)
        chart_df = chart_df.sort_values(value_col, ascending=True)
        bar_colors = [colors[index % len(colors)] for index in range(len(chart_df))]

        fig, ax = plt.subplots(figsize=(6.1, 4.7))
        bars = ax.barh(chart_df["Campaign"], chart_df[value_col], color=bar_colors, height=0.62)
        ax.set_title(title, fontsize=self.body_size + 1, color="#909090", fontweight="bold", pad=14)
        ax.set_xlabel(x_label, fontsize=self.body_size, color="#B0B0B0")
        ax.set_ylabel("Campaign", fontsize=self.body_size, color="#B0B0B0")
        ax.tick_params(axis="both", labelsize=self.body_size - 1, colors="#606060")
        ax.grid(axis="x", alpha=0.2)
        ax.set_axisbelow(True)
        for spine in ["top", "right", "left"]:
            ax.spines[spine].set_visible(False)
        ax.spines["bottom"].set_color("#E6E6E6")

        max_value = float(chart_df[value_col].max())
        ax.set_xlim(right=max_value * 1.18 if max_value > 0 else 1)
        for bar, value in zip(bars, chart_df[value_col]):
            label = self._format_plain_number(value)
            ax.annotate(
                label,
                xy=(bar.get_width(), bar.get_y() + bar.get_height() / 2),
                xytext=(6, 0),
                textcoords="offset points",
                va="center",
                ha="left",
                fontsize=self.body_size - 1,
                color="#4B5563",
            )

        plt.tight_layout(pad=1.5)
        fig.savefig(out_path, dpi=220, facecolor="white")
        plt.close(fig)
        return out_path

    def _plot_empty_state(self, out_path: Path, message: str) -> Path:
        fig, ax = plt.subplots(figsize=self.figure_size)
        ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=self.title_size)
        ax.axis("off")
        plt.tight_layout()
        fig.savefig(out_path, dpi=180)
        plt.close(fig)
        return out_path

    @staticmethod
    def _format_currency_label(value: float, abbreviated: bool = False) -> str:
        if abbreviated:
            absolute = abs(value)
            if absolute >= 1_000_000:
                return f"£{value / 1_000_000:.1f}m"
            if absolute >= 1_000:
                return f"£{value / 1_000:.1f}k"
            return f"£{value:,.0f}"
        return f"£{value:,.2f}"

    @staticmethod
    def _format_plain_number(value: float) -> str:
        numeric = float(value)
        if abs(numeric - round(numeric)) < 0.005:
            return f"{int(round(numeric)):,}"
        return f"{numeric:,.1f}"

    @staticmethod
    def _format_currency_axis(value: float) -> str:
        absolute = abs(float(value))
        if absolute >= 1_000_000:
            return f"£{float(value) / 1_000_000:.1f}m"
        if absolute >= 1_000:
            return f"£{float(value) / 1_000:.0f}k"
        return f"£{float(value):,.0f}"
