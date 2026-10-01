from pathlib import Path
from types import SimpleNamespace

from text_extraction_service.extractor import MistralOptions, extract_ocr_mistral
from text_extraction_service.formatting import html_table_to_markdown, inline_tables, strip_image_refs
from text_extraction_service.utils import ServiceConfig

TABLE_HTML = (
    "<table><thead><tr><th></th><th colspan='2'>2025</th></tr></thead>"
    "<tr><td>iPhone</td><td>$ 209,586</td><td>4 %</td></tr>"
    "<tr><td>Services <sup>(1)</sup></td><td>109|158</td><td>14 %</td></tr></table>"
)


def test_html_table_to_markdown_handles_colspan_and_pipes() -> None:
    assert html_table_to_markdown(TABLE_HTML).splitlines() == [
        "|  | 2025 |  |",
        "|---|---|---|",
        "| iPhone | $ 209,586 | 4 % |",
        "| Services (1) | 109\\|158 | 14 % |",
    ]


def test_inline_tables_and_strip_images() -> None:
    markdown = "## Sales\n\n[tbl-1.html](tbl-1.html)\n\n![img-0.jpeg](img-0.jpeg)\n\nDone."
    text = strip_image_refs(inline_tables(markdown, {"tbl-1.html": ("html", TABLE_HTML)}))
    assert text.startswith("## Sales\n\n|  | 2025 |  |")
    assert "img-0" not in text
    assert text.endswith("Done.")


class _FakeOCR:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    def process(self, **kwargs):
        self.kwargs = kwargs
        page = SimpleNamespace(
            index=0,
            markdown="# Report\n\n[tbl-0.html](tbl-0.html)\n\n![img-0.jpeg](img-0.jpeg)",
            header="ACME | Annual Report 2025",
            footer="12",
            dimensions=SimpleNamespace(width=791, height=1023, dpi=93),
            tables=[SimpleNamespace(id="tbl-0.html", format_="html", content=TABLE_HTML)],
            images=[
                SimpleNamespace(
                    id="img-0.jpeg",
                    top_left_x=10,
                    top_left_y=20,
                    bottom_right_x=110,
                    bottom_right_y=220,
                    image_base64="data:image/jpeg;base64,AAAA",
                )
            ],
            blocks=[
                SimpleNamespace(
                    type="title",
                    top_left_x=1,
                    top_left_y=2,
                    bottom_right_x=3,
                    bottom_right_y=4,
                    content="# Report",
                    table_id=None,
                    image_id=None,
                )
            ],
            hyperlinks=["https://example.com"],
            confidence_scores=SimpleNamespace(average_page_confidence_score=0.98, minimum_page_confidence_score=0.4),
        )
        return SimpleNamespace(
            pages=[page],
            model="mistral-ocr-4-1",
            usage_info=SimpleNamespace(pages_processed=1, doc_size_bytes=1234),
        )


def _fake_client() -> SimpleNamespace:
    return SimpleNamespace(
        ocr=_FakeOCR(),
        files=SimpleNamespace(
            upload=lambda **_: SimpleNamespace(id="file-1"),
            get_signed_url=lambda **_: SimpleNamespace(url="https://signed.example/file-1"),
        ),
    )


def test_extract_ocr_mistral_keeps_layout(tmp_path: Path) -> None:
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    client = _fake_client()

    output = extract_ocr_mistral(client, pdf, "mistral-ocr-4-1", MistralOptions())

    assert client.ocr.kwargs["table_format"] == "html"
    assert client.ocr.kwargs["extract_header"] is True
    assert client.ocr.kwargs["include_image_base64"] is True
    assert client.ocr.kwargs["confidence_scores_granularity"] == "page"
    assert output.model == "mistral-ocr-4-1"
    assert output.usage == {"pages_processed": 1, "doc_size_bytes": 1234}

    page = output.pages[0]
    assert page.page_number == 1
    assert page.text.startswith("# Report\n\n|  | 2025 |  |")
    assert "img-0" not in page.text
    assert page.markdown.endswith("![img-0.jpeg](img-0.jpeg)")
    assert (page.header, page.footer) == ("ACME | Annual Report 2025", "12")
    assert page.dimensions.model_dump() == {"width": 791, "height": 1023, "dpi": 93}
    assert page.tables[0].format == "html"
    assert page.images[0].bbox == [10, 20, 110, 220]
    assert page.images[0].image_base64.startswith("data:image/jpeg")
    assert page.blocks[0].type == "title"
    assert page.hyperlinks == ["https://example.com"]
    assert page.confidence.average == 0.98


def test_mistral_options_from_config() -> None:
    config = ServiceConfig(
        {
            "mistral.tableFormat": "markdown",
            "mistral.includeImages": False,
            "mistral.imageLimit": 5,
            "mistral.confidenceGranularity": None,
        }
    )
    kwargs = config.mistral_options.request_kwargs()
    assert kwargs["table_format"] == "markdown"
    assert kwargs["include_image_base64"] is False
    assert kwargs["image_limit"] == 5
    assert "confidence_scores_granularity" not in kwargs
