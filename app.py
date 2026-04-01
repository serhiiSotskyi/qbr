from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import streamlit as st

from main import run_report, run_text_report
from presentation_prompt_builder import build_presentation_prompt


BASE_DIR = Path(__file__).resolve().parent
TEMP_DIR = BASE_DIR / "temp_uploads"
TEMP_DIR.mkdir(exist_ok=True)


def load_client_options() -> list[dict[str, str]]:
    config_path = BASE_DIR / "config" / "clients_config.json"
    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    clients = config.get("clients", {})
    if isinstance(clients, dict):
        return [
            {"id": str(client_id).strip(), "name": str(client_id).strip()}
            for client_id in clients.keys()
            if str(client_id).strip()
        ]

    options: list[dict[str, str]] = []
    for client in clients:
        client_id = str(client.get("id", "")).strip()
        if not client_id:
            continue
        client_name = str(client.get("name", "")).strip() or client_id
        options.append({"id": client_id, "name": client_name})
    return options


def save_uploaded_file(uploaded_file, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        handle.write(uploaded_file.getvalue())
    return str(destination)


def build_request_inputs(performance_file, auction_file, trends_files) -> tuple[Path, str, str | None, str | None]:
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

    return request_dir, perf_path, auction_path, trends_dir


def create_package_bundle(client_id: str, pptx_path: Path, report_txt_path: Path, prompt_txt_path: Path, request_dir: Path) -> Path:
    package_path = request_dir / f"{client_id}_package.zip"
    with ZipFile(package_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.write(pptx_path, arcname=f"{client_id}_report.pptx")
        archive.write(report_txt_path, arcname="report.txt")
        archive.write(prompt_txt_path, arcname="prompt.txt")
    return package_path


def main() -> None:
    st.title("PPC Report Generator")

    client_options = load_client_options()
    selected_client = st.selectbox("Client", client_options, format_func=lambda client: client["name"])
    client_id = selected_client["id"]

    st.subheader("File Uploads")
    performance_file = st.file_uploader("Performance CSV", type=["csv"])
    auction_file = st.file_uploader("Auction CSV", type=["csv"])
    trends_files = st.file_uploader("Trends CSVs", type=["csv"], accept_multiple_files=True)

    if "generated_bundle" not in st.session_state:
        st.session_state.generated_bundle = None

    if st.button("Generate Files"):
        if performance_file is None:
            st.error("Please upload a performance CSV")
            return

        request_dir, perf_path, auction_path, trends_dir = build_request_inputs(performance_file, auction_file, trends_files)
        outputs_dir = request_dir / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)

        pptx_path = outputs_dir / f"{client_id}_report.pptx"
        report_txt_path = outputs_dir / "report.txt"
        prompt_txt_path = outputs_dir / "prompt.txt"

        try:
            st.info("Generating PPTX, TXT, and prompt...")
            generated_pptx = Path(
                run_report(
                    performance_csv=perf_path,
                    client_id=client_id,
                    trends_dir=trends_dir,
                    auction_csv=auction_path,
                    output_path=str(pptx_path),
                )
            )
            generated_txt = Path(
                run_text_report(
                    performance_csv=perf_path,
                    client_id=client_id,
                    trends_dir=trends_dir,
                    auction_csv=auction_path,
                    output_path=str(report_txt_path),
                )
            )
            prompt_txt_path.write_text(build_presentation_prompt(client_id), encoding="utf-8")
            package_path = create_package_bundle(client_id, generated_pptx, generated_txt, prompt_txt_path, request_dir)

            st.session_state.generated_bundle = {
                "client_id": client_id,
                "pptx_path": str(generated_pptx),
                "report_txt_path": str(generated_txt),
                "prompt_txt_path": str(prompt_txt_path),
                "package_path": str(package_path),
            }
            st.success("Files generated successfully.")
        except Exception as exc:
            st.session_state.generated_bundle = None
            st.error(str(exc))

    bundle = st.session_state.generated_bundle
    if bundle:
        st.subheader("Generated Files")
        st.write(
            {
                "client": bundle["client_id"],
                "pptx": bundle["pptx_path"],
                "report_txt": bundle["report_txt_path"],
                "prompt_txt": bundle["prompt_txt_path"],
                "package_zip": bundle["package_path"],
            }
        )

        with open(bundle["package_path"], "rb") as handle:
            st.download_button(
                label="Download All Files",
                data=handle,
                file_name=f"{bundle['client_id']}_package.zip",
                mime="application/zip",
            )


if __name__ == "__main__":
    main()
