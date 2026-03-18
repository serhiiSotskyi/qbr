from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
import matplotlib
import pandas as pd

matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt


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

    def build_mix_charts(self, scope_key: str, mix_df: pd.DataFrame) -> Dict[str, Path]:
        cost_path = self._plot_mix_pie(scope_key, mix_df, value_col="Cost", suffix="cost_share")
        leads_path = self._plot_mix_pie(scope_key, mix_df, value_col="Sales Leads", suffix="leads_share")
        return {"cost_share": cost_path, "leads_share": leads_path}

    def build_trends_chart(self, scope_key: str, comparison_df: pd.DataFrame, title: str) -> Path:
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
            label="Current period",
        )

        if "prior_value" in comparison_df.columns and comparison_df["prior_value"].notna().any():
            ax.plot(
                comparison_df["month_label"],
                comparison_df["prior_value"],
                marker="o",
                linewidth=2,
                linestyle="--",
                color=self.colors.get("trend_prior", "#94A3B8"),
                label="Prior year",
            )

        ax.set_title(title, fontsize=self.title_size)
        ax.set_ylabel("Interest", fontsize=self.body_size)
        ax.tick_params(axis="both", labelsize=self.body_size)
        ax.grid(axis="y", alpha=0.2)
        ax.legend(loc="upper left", fontsize=self.body_size)

        plt.tight_layout()
        fig.savefig(out_path, dpi=180)
        plt.close(fig)
        return out_path

    def _plot_cpl_cvr(self, scope_key: str, monthly_table: pd.DataFrame) -> Path:
        df = monthly_table[monthly_table["Month"] != "Total"].copy()
        out_path = self.charts_dir / f"{scope_key}_cpl_cvr.png"

        months = df["Month"].tolist()
        cpl = df["CPL"].tolist()
        cvr = [x * 100 if x is not None else None for x in df["CVR"].tolist()]

        fig, ax1 = plt.subplots(figsize=self.figure_size)
        ax2 = ax1.twinx()

        ax1.plot(months, cpl, marker="o", color=self.colors.get("cpl", "#0E7490"), linewidth=2, label="CPL (£)")
        ax2.plot(months, cvr, marker="o", color=self.colors.get("cvr", "#1D4ED8"), linewidth=2, label="CVR (%)")

        ax1.set_title("CPL vs CVR", fontsize=self.title_size)
        ax1.set_ylabel("CPL (£)", fontsize=self.body_size)
        ax2.set_ylabel("CVR (%)", fontsize=self.body_size)
        ax1.tick_params(axis="both", labelsize=self.body_size)
        ax2.tick_params(axis="both", labelsize=self.body_size)

        lines_1, labels_1 = ax1.get_legend_handles_labels()
        lines_2, labels_2 = ax2.get_legend_handles_labels()
        ax1.legend(lines_1 + lines_2, labels_1 + labels_2, loc="upper left", fontsize=self.body_size)

        plt.tight_layout()
        fig.savefig(out_path, dpi=180)
        plt.close(fig)
        return out_path

    def _plot_cost_leads(self, scope_key: str, monthly_table: pd.DataFrame) -> Path:
        df = monthly_table[monthly_table["Month"] != "Total"].copy()
        out_path = self.charts_dir / f"{scope_key}_cost_leads.png"

        months = df["Month"].tolist()
        cost = df["Cost"].tolist()
        leads = df["Sales Leads"].tolist()

        fig, ax1 = plt.subplots(figsize=self.figure_size)
        ax2 = ax1.twinx()

        ax1.bar(months, cost, color=self.colors.get("cost", "#14B8A6"), alpha=0.75, label="Cost (£)")
        ax2.plot(months, leads, marker="o", color=self.colors.get("leads", "#0F172A"), linewidth=2, label="Sales Leads")

        ax1.set_title("Cost vs Sales Leads", fontsize=self.title_size)
        ax1.set_ylabel("Cost (£)", fontsize=self.body_size)
        ax2.set_ylabel("Sales Leads", fontsize=self.body_size)
        ax1.tick_params(axis="both", labelsize=self.body_size)
        ax2.tick_params(axis="both", labelsize=self.body_size)

        lines_1, labels_1 = ax1.get_legend_handles_labels()
        lines_2, labels_2 = ax2.get_legend_handles_labels()
        ax1.legend(lines_1 + lines_2, labels_1 + labels_2, loc="upper right", fontsize=self.body_size)

        plt.tight_layout()
        fig.savefig(out_path, dpi=180)
        plt.close(fig)
        return out_path

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

    def _plot_empty_state(self, out_path: Path, message: str) -> Path:
        fig, ax = plt.subplots(figsize=self.figure_size)
        ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=self.title_size)
        ax.axis("off")
        plt.tight_layout()
        fig.savefig(out_path, dpi=180)
        plt.close(fig)
        return out_path
