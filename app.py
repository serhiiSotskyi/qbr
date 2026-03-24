from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import streamlit as st

from main import run_report, run_text_report


BASE_DIR = Path(__file__).resolve().parent
TEMP_DIR = BASE_DIR / "temp_uploads"
TEMP_DIR.mkdir(exist_ok=True)


def load_client_ids() -> list[str]:
    config_path = BASE_DIR / "config" / "clients_config.json"
    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    clients = config.get("clients", {})
    if isinstance(clients, dict):
        return list(clients.keys())
    return [str(client.get("id", "")).strip() for client in clients if str(client.get("id", "")).strip()]


def save_uploaded_file(uploaded_file, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        handle.write(uploaded_file.getvalue())
    return str(destination)


def main() -> None:
    st.title("PPC Report Generator")

    client_ids = load_client_ids()
    client_id = st.selectbox("Client", client_ids)

    st.subheader("File Uploads")
    performance_file = st.file_uploader("Performance CSV", type=["csv"])
    auction_file = st.file_uploader("Auction CSV", type=["csv"])
    trends_files = st.file_uploader("Trends CSVs", type=["csv"], accept_multiple_files=True)

    if st.button("Generate Report"):
        if performance_file is None:
            st.error("Please upload a performance CSV")
            return

        request_id = uuid4().hex
        request_dir = TEMP_DIR / request_id
        request_dir.mkdir(parents=True, exist_ok=True)

        perf_path = save_uploaded_file(performance_file, request_dir / "performance" / performance_file.name)
        auction_path = None
        if auction_file is not None:
            auction_path = save_uploaded_file(auction_file, request_dir / "auction" / auction_file.name)

        trends_dir = None
        if trends_files:
            trends_path = request_dir / "trends"
            trends_path.mkdir(parents=True, exist_ok=True)
            for trend_file in trends_files:
                save_uploaded_file(trend_file, trends_path / trend_file.name)
            trends_dir = str(trends_path)

        output_path = f"output/{client_id}_report.pptx"

        st.write(
            {
                "client": client_id,
                "performance_file": perf_path,
                "auction_file": auction_path,
                "trends_files": [uploaded.name for uploaded in trends_files] if trends_files else [],
            }
        )

        try:
            st.info("Generating report...")
            generated_output = run_report(
                performance_csv=perf_path,
                client_id=client_id,
                trends_dir=trends_dir if trends_files else None,
                auction_csv=auction_path,
                output_path=output_path,
            )
            st.success("Report generated successfully!")

            with open(generated_output, "rb") as handle:
                st.download_button(
                    label="Download Report",
                    data=handle,
                    file_name=f"{client_id}_report.pptx",
                )
        except Exception as exc:
            st.error(str(exc))

    if st.button("Generate Text Report"):
        if performance_file is None:
            st.error("Please upload a performance CSV")
            return

        request_id = uuid4().hex
        request_dir = TEMP_DIR / request_id
        request_dir.mkdir(parents=True, exist_ok=True)

        perf_path = save_uploaded_file(performance_file, request_dir / "performance" / performance_file.name)
        auction_path = None
        if auction_file is not None:
            auction_path = save_uploaded_file(auction_file, request_dir / "auction" / auction_file.name)

        trends_dir = None
        if trends_files:
            trends_path = request_dir / "trends"
            trends_path.mkdir(parents=True, exist_ok=True)
            for trend_file in trends_files:
                save_uploaded_file(trend_file, trends_path / trend_file.name)
            trends_dir = str(trends_path)

        output_path = f"reports/{client_id}_report.txt"

        try:
            st.info("Generating text report...")
            generated_output = run_text_report(
                performance_csv=perf_path,
                client_id=client_id,
                trends_dir=trends_dir if trends_files else None,
                auction_csv=auction_path,
                output_path=output_path,
            )
            report_text = Path(generated_output).read_text(encoding="utf-8")
            st.success("Text report generated")
            st.download_button(
                "Download TXT",
                data=report_text,
                file_name="report.txt",
            )
        except Exception as exc:
            st.error(str(exc))


if __name__ == "__main__":
    main()
