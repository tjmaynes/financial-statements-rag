from datetime import date
from pathlib import Path

from financial_statements_rag.ingestion.extraction.metadata import (
    ReportMetadata,
    ReportMetadataInferer,
)
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
        company_name="ACME CORPORATION",
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
