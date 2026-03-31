from __future__ import annotations

import os
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
import matplotlib

matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
import pandas as pd
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt


class WightlinkPptxBuilder:
    def __init__(self, output_path: str | Path, charts_dir: str | Path) -> None:
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.charts_dir = Path(charts_dir)
        self.charts_dir.mkdir(parents=True, exist_ok=True)
        self.prs = Presentation()
        self.prs.slide_width = Inches(13.333)
        self.prs.slide_height = Inches(7.5)
        self.bg = RGBColor(247, 249, 252)
        self.text_primary = RGBColor(20, 42, 77)
        self.text_secondary = RGBColor(90, 90, 90)
        self.text_body = RGBColor(45, 45, 45)
        self.accent = RGBColor(12, 84, 96)
        self.light_fill = RGBColor(232, 239, 247)

    def build(self, slides: list[dict[str, Any]]) -> Path:
        for slide in slides:
            self._render_slide(slide)
        self.prs.save(str(self.output_path))
        return self.output_path

    def build_trend_chart(self, trend_section: dict[str, Any], filename: str) -> Path:
        output = self.charts_dir / filename
        labels = trend_section.get("labels", [])
        series = trend_section.get("series", [])
        if not labels or not series:
            return self._plot_empty(output, "No trend data")
        fig, ax = plt.subplots(figsize=(8.2, 4.2))
        palette = ["#0C5460", "#94A3B8", "#1D4ED8", "#14B8A6"]
        for index, item in enumerate(series):
            ax.plot(labels, item.get("data", []), linewidth=2.3, marker="o", label=item.get("name", f"Series {index+1}"), color=palette[index % len(palette)])
        ax.set_title(trend_section.get("title", "Google Trends"), fontsize=15)
        ax.tick_params(axis="x", rotation=35, labelsize=9)
        ax.tick_params(axis="y", labelsize=9)
        ax.grid(axis="y", alpha=0.2)
        ax.legend(fontsize=9, loc="upper left")
        plt.tight_layout()
        fig.savefig(output, dpi=180)
        plt.close(fig)
        return output

    def build_performance_chart(self, scope: dict[str, Any], filename: str, left_metric: str, right_metric: str | None, title: str) -> Path:
        output = self.charts_dir / filename
        monthly = scope.get("monthly", [])
        if not monthly:
            return self._plot_empty(output, "No performance data")
        df = pd.DataFrame(monthly)
        labels = df["month_label"].tolist()
        fig, ax1 = plt.subplots(figsize=(7.8, 4.0))
        ax1.plot(labels, df[left_metric], color="#0C5460", linewidth=2.3, marker="o", label=left_metric.upper())
        ax1.set_title(title, fontsize=14)
        ax1.tick_params(axis="both", labelsize=9)
        ax1.grid(axis="y", alpha=0.2)
        lines, labels_accum = ax1.get_legend_handles_labels()
        if right_metric and right_metric in df.columns and df[right_metric].notna().any():
            ax2 = ax1.twinx()
            ax2.plot(labels, df[right_metric], color="#1D4ED8", linewidth=2.0, marker="o", label=right_metric.upper())
            ax2.tick_params(axis="y", labelsize=9)
            right_lines, right_labels = ax2.get_legend_handles_labels()
            lines += right_lines
            labels_accum += right_labels
        ax1.legend(lines, labels_accum, fontsize=9, loc="upper left")
        plt.tight_layout()
        fig.savefig(output, dpi=180)
        plt.close(fig)
        return output

    def _render_slide(self, slide_spec: dict[str, Any]) -> None:
        slide = self.prs.slides.add_slide(self.prs.slide_layouts[6])
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = self.bg

        slide_type = slide_spec.get("type")
        if slide_type == "cover":
            self._add_title(slide, slide_spec.get("title", ""), top=1.8, size=30)
            self._add_subtitle(slide, slide_spec.get("subtitle", ""), top=2.65, size=18)
            self._add_center_badge(slide, slide_spec.get("client_name", "Wightlink"))
            return
        if slide_type == "divider":
            self._add_title(slide, slide_spec.get("title", ""), top=2.7, size=34)
            return
        if slide_type == "closing":
            self._add_title(slide, slide_spec.get("title", "Any Questions?"), top=2.8, size=34)
            return

        self._add_title(slide, slide_spec.get("title", ""))
        if slide_spec.get("subtitle"):
            self._add_subtitle(slide, slide_spec.get("subtitle", ""))

        charts = slide_spec.get("charts", [])
        table_rows = slide_spec.get("table", {}).get("rows", [])
        bullets = slide_spec.get("bullets", [])

        if slide_type == "agenda":
            self._add_bullets(slide, bullets, Inches(1.0), Inches(1.6), Inches(11.0), Inches(4.8), font_size=22)
        elif slide_type == "dual_chart_bullets":
            if len(charts) >= 1:
                slide.shapes.add_picture(str(charts[0]["path"]), Inches(0.45), Inches(1.35), width=Inches(6.0), height=Inches(3.15))
            if len(charts) >= 2:
                slide.shapes.add_picture(str(charts[1]["path"]), Inches(6.85), Inches(1.35), width=Inches(6.0), height=Inches(3.15))
            self._add_bullets(slide, bullets, Inches(0.8), Inches(4.85), Inches(11.8), Inches(1.65))
        elif slide_type == "single_chart_bullets":
            if charts:
                slide.shapes.add_picture(str(charts[0]["path"]), Inches(0.55), Inches(1.35), width=Inches(7.4), height=Inches(4.5))
            self._add_bullets(slide, bullets, Inches(8.2), Inches(1.55), Inches(4.3), Inches(3.9))
        elif slide_type == "table_bullets":
            self._render_table(slide, table_rows, Inches(0.4), Inches(1.35), Inches(12.45), Inches(3.35))
            self._add_bullets(slide, bullets, Inches(0.8), Inches(4.95), Inches(11.8), Inches(1.45))
        elif slide_type == "table_only":
            self._render_table(slide, table_rows, Inches(0.4), Inches(1.35), Inches(12.45), Inches(4.8))
            self._add_bullets(slide, bullets, Inches(0.8), Inches(6.15), Inches(11.8), Inches(0.7), font_size=13)
        elif slide_type == "bullets_only":
            self._add_bullets(slide, bullets, Inches(0.9), Inches(1.65), Inches(11.7), Inches(4.8), font_size=20)
        elif slide_type == "image_bullets":
            image_path = slide_spec.get("image_path")
            if image_path and Path(image_path).exists():
                slide.shapes.add_picture(str(image_path), Inches(0.55), Inches(1.45), width=Inches(6.8), height=Inches(4.3))
            else:
                self._add_image_placeholder(slide, Inches(0.55), Inches(1.45), Inches(6.8), Inches(4.3), "Image / screenshot placeholder")
            self._add_bullets(slide, bullets, Inches(7.7), Inches(1.6), Inches(4.8), Inches(4.0))

        if slide_spec.get("source_note"):
            self._add_source_note(slide, slide_spec["source_note"])

    def _add_title(self, slide, text: str, top: float = 0.28, size: int = 24) -> None:
        box = slide.shapes.add_textbox(Inches(0.6), Inches(top), Inches(12.0), Inches(0.6)).text_frame
        run = box.paragraphs[0].add_run()
        run.text = text
        run.font.size = Pt(size)
        run.font.bold = True
        run.font.color.rgb = self.text_primary

    def _add_subtitle(self, slide, text: str, top: float = 0.88, size: int = 14) -> None:
        box = slide.shapes.add_textbox(Inches(0.6), Inches(top), Inches(12.0), Inches(0.35)).text_frame
        run = box.paragraphs[0].add_run()
        run.text = text
        run.font.size = Pt(size)
        run.font.color.rgb = self.text_secondary

    def _add_center_badge(self, slide, text: str) -> None:
        shape = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, Inches(4.2), Inches(3.6), Inches(4.9), Inches(1.15))
        shape.fill.solid()
        shape.fill.fore_color.rgb = self.light_fill
        shape.line.color.rgb = self.accent
        frame = shape.text_frame
        frame.clear()
        para = frame.paragraphs[0]
        para.alignment = PP_ALIGN.CENTER
        run = para.add_run()
        run.text = text
        run.font.size = Pt(22)
        run.font.bold = True
        run.font.color.rgb = self.accent

    def _add_bullets(self, slide, bullets: list[str], left, top, width, height, font_size: int = 16) -> None:
        text_frame = slide.shapes.add_textbox(left, top, width, height).text_frame
        text_frame.clear()
        bullet_list = bullets or ["No narrative was available for this slide."]
        for index, bullet in enumerate(bullet_list):
            para = text_frame.paragraphs[0] if index == 0 else text_frame.add_paragraph()
            para.text = bullet
            para.level = 0
            para.font.size = Pt(font_size)
            para.font.color.rgb = self.text_body
            para.space_after = Pt(10)

    def _render_table(self, slide, rows: list[dict[str, Any]], left, top, width, height) -> None:
        if not rows:
            self._add_image_placeholder(slide, left, top, width, height, "No table data")
            return
        headers = list(rows[0].keys())
        shape = slide.shapes.add_table(len(rows) + 1, len(headers), left, top, width, height)
        table = shape.table
        for col_index, header in enumerate(headers):
            cell = table.cell(0, col_index)
            cell.text = str(header)
            cell.fill.solid()
            cell.fill.fore_color.rgb = self.light_fill
            p = cell.text_frame.paragraphs[0]
            p.font.bold = True
            p.font.size = Pt(9)
            p.font.color.rgb = self.text_primary
        for row_index, row in enumerate(rows, start=1):
            for col_index, header in enumerate(headers):
                cell = table.cell(row_index, col_index)
                cell.text = str(row.get(header, ""))
                p = cell.text_frame.paragraphs[0]
                p.font.size = Pt(8.5)
                p.font.color.rgb = self.text_body

    def _add_image_placeholder(self, slide, left, top, width, height, text: str) -> None:
        shape = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, left, top, width, height)
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor(236, 241, 246)
        shape.line.color.rgb = self.text_secondary
        frame = shape.text_frame
        frame.clear()
        para = frame.paragraphs[0]
        para.alignment = PP_ALIGN.CENTER
        run = para.add_run()
        run.text = text
        run.font.size = Pt(16)
        run.font.color.rgb = self.text_secondary

    def _add_source_note(self, slide, text: str) -> None:
        box = slide.shapes.add_textbox(Inches(0.6), Inches(6.9), Inches(12.0), Inches(0.25)).text_frame
        run = box.paragraphs[0].add_run()
        run.text = text
        run.font.size = Pt(8)
        run.font.color.rgb = self.text_secondary

    def _plot_empty(self, output: Path, message: str) -> Path:
        fig, ax = plt.subplots(figsize=(7.8, 4.0))
        ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=16)
        ax.axis("off")
        plt.tight_layout()
        fig.savefig(output, dpi=180)
        plt.close(fig)
        return output
