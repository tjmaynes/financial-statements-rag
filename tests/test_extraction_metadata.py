from datetime import date
from pathlib import Path

from financial_statements_rag.ingestion.extraction.metadata import (
    ReportMetadata,
    ReportMetadataInferer,
)
from financial_statements_rag.ingestion.extraction.pdf import PdfPageExtractor
from financial_statements_rag.ingestion.extraction.pdf import ExtractedPage


def test_infers_quarterly_report_metadata() -> None:
    inferer = ReportMetadataInferer()
    pages = [
        ExtractedPage(
            page_number=1,
            source_path=Path("uploads/acme-q1-2026.pdf"),
            text=(
                "ACME CORPORATION\n"
                "NASDAQ: ACME\n"
                "Quarterly Report on Form 10-Q\n"
                "For the quarterly period ended March 31, 2026\n"
                "Unaudited, in millions of U.S. dollars\n"
            ),
        )
    ]

    metadata = inferer.infer(pages)

    assert metadata == ReportMetadata(
        company_name="ACME",
        ticker="ACME",
        fiscal_year=2026,
        fiscal_quarter="Q1",
        report_date=date(2026, 3, 31),
        report_type="quarterly",
        currency="USD",
        scale="millions",
    )


def test_returns_empty_metadata_when_snippet_is_unstructured() -> None:
    inferer = ReportMetadataInferer()
    pages = [
        ExtractedPage(
            page_number=1,
            source_path=Path("uploads/unknown.pdf"),
            text="Table of contents\nForward-looking statements\n",
        )
    ]

    metadata = inferer.infer(pages)

    assert metadata == ReportMetadata()


def test_infers_sec_style_cover_page_metadata() -> None:
    inferer = ReportMetadataInferer()
    pages = [
        ExtractedPage(
            page_number=1,
            source_path=Path("uploads/tsla-20260331.pdf"),
            text=(
                "UNITED STATES\n"
                "SECURITIES AND EXCHANGE COMMISSION\n"
                "FORM 10-Q\n"
                "For the quarterly period ended March 31, 2026\n"
                "Tesla, Inc.\n"
                "(Exact name of registrant as specified in its charter)\n"
                "Title of each class Trading Symbol(s) Name of each exchange on which registered\n"
                "Common stock TSLA The Nasdaq Global Select Market\n"
            ),
        ),
        ExtractedPage(
            page_number=2,
            source_path=Path("uploads/tsla-20260331.pdf"),
            text="",
        ),
        ExtractedPage(
            page_number=3,
            source_path=Path("uploads/tsla-20260331.pdf"),
            text="",
        ),
        ExtractedPage(
            page_number=4,
            source_path=Path("uploads/tsla-20260331.pdf"),
            text="(in millions, except per share data)\n",
        ),
        ExtractedPage(
            page_number=5,
            source_path=Path("uploads/tsla-20260331.pdf"),
            text="exchange rates affect our operating results as expressed in U.S. dollars.\n",
        ),
    ]

    metadata = inferer.infer(pages)

    assert metadata == ReportMetadata(
        company_name="TESLA",
        ticker="TSLA",
        fiscal_year=2026,
        fiscal_quarter="Q1",
        report_date=date(2026, 3, 31),
        report_type="quarterly",
        currency="USD",
        scale="millions",
    )


def test_infers_metadata_from_tesla_example_pdf() -> None:
    pages = PdfPageExtractor().extract(
        Path(__file__).resolve().parent.parent / "examples/tsla-20260331.pdf",
    )

    metadata = ReportMetadataInferer().infer(pages)

    assert metadata.company_name == "TESLA"
    assert metadata.ticker == "TSLA"
    assert metadata.fiscal_year == 2026
    assert metadata.fiscal_quarter == "Q1"
    assert metadata.report_date == date(2026, 3, 31)
    assert metadata.report_type == "quarterly"
    assert metadata.currency == "USD"
    assert metadata.scale == "millions"


def test_infers_metadata_from_microsoft_example_pdf() -> None:
    pages = PdfPageExtractor().extract(
        Path(__file__).resolve().parent.parent / "examples/microsoft-fy26q3.pdf",
    )

    metadata = ReportMetadataInferer().infer(pages)

    assert metadata.company_name == "MICROSOFT"
    assert metadata.ticker == "MSFT"
    assert metadata.fiscal_year == 2026
    assert metadata.fiscal_quarter == "Q1"
    assert metadata.report_date == date(2026, 3, 31)
    assert metadata.report_type == "quarterly"
    assert metadata.currency == "USD"
    assert metadata.scale == "millions"
