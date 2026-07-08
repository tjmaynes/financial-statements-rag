from pathlib import Path

from sec_filings_rag.ingestion.extraction.metadata import ReportMetadata
from sec_filings_rag.ingestion.extraction.pdf import ExtractedPage
from sec_filings_rag.ingestion.extraction.statements import (
    StatementExtractor,
    StatementType,
)


def test_detects_balance_sheet_section() -> None:
    extractor = StatementExtractor()

    result = extractor.detect_sections(
        [
            ExtractedPage(
                page_number=1,
                source_path=Path("uploads/report.pdf"),
                text="Cover page",
            ),
            ExtractedPage(
                page_number=2,
                source_path=Path("uploads/report.pdf"),
                text=(
                    "Condensed Consolidated Balance Sheets\n"
                    "Cash and cash equivalents  1,250  March 31, 2026\n"
                ),
            ),
        ]
    )

    assert len(result.sections) == 1
    assert result.sections[0].statement_type is StatementType.BALANCE_SHEET
    assert result.sections[0].title == "Condensed Consolidated Balance Sheets"
    assert result.sections[0].page_start == 2
    assert result.sections[0].page_end == 2
    assert result.sections[0].confidence >= 0.9
    assert result.warnings == ()


def test_detects_income_statement_section() -> None:
    extractor = StatementExtractor()

    result = extractor.detect_sections(
        [
            ExtractedPage(
                page_number=3,
                source_path=Path("uploads/report.pdf"),
                text=(
                    "Consolidated Statements of Operations\n"
                    "Revenue  2,100  Q1 2026\n"
                ),
            )
        ]
    )

    assert len(result.sections) == 1
    assert result.sections[0].statement_type is StatementType.INCOME_STATEMENT
    assert result.sections[0].page_start == 3
    assert result.sections[0].page_end == 3


def test_detects_cash_flow_statement_section() -> None:
    extractor = StatementExtractor()

    result = extractor.detect_sections(
        [
            ExtractedPage(
                page_number=4,
                source_path=Path("uploads/report.pdf"),
                text=(
                    "Condensed Consolidated Statements of Cash Flows\n"
                    "Net cash provided by operating activities  315  Q1 2026\n"
                ),
            )
        ]
    )

    assert len(result.sections) == 1
    assert result.sections[0].statement_type is StatementType.CASH_FLOW_STATEMENT
    assert result.sections[0].page_start == 4
    assert result.sections[0].page_end == 4


def test_returns_warning_when_no_statement_sections_are_detected() -> None:
    extractor = StatementExtractor()

    result = extractor.detect_sections(
        [
            ExtractedPage(
                page_number=1,
                source_path=Path("uploads/report.pdf"),
                text="Management discussion and analysis",
            )
        ]
    )

    assert result.sections == ()
    assert result.warnings == ("NO_STATEMENT_SECTIONS_FOUND",)


def test_extracts_line_item_candidates_with_provenance() -> None:
    extractor = StatementExtractor()
    pages = [
        ExtractedPage(
            page_number=2,
            source_path=Path("uploads/report.pdf"),
            text=(
                "Condensed Consolidated Balance Sheets\n"
                "Cash and cash equivalents  1,250  March 31, 2026\n"
                "Accounts receivable  980  March 31, 2026\n"
            ),
        )
    ]
    sections = extractor.detect_sections(pages).sections

    line_items = extractor.extract_line_items(
        sections,
        pages,
        ReportMetadata(currency="USD", scale="millions"),
    )

    assert len(line_items) >= 2
    assert line_items[0].statement_type is StatementType.BALANCE_SHEET
    assert line_items[0].line_item_label == "Cash and cash equivalents"
    assert line_items[0].raw_value_text == "1,250"
    assert line_items[0].period_label == "March 31, 2026"
    assert line_items[0].currency == "USD"
    assert line_items[0].scale == "millions"
    assert line_items[0].page_number == 2
    assert (
        line_items[0].source_text == "Cash and cash equivalents  1,250  March 31, 2026"
    )
    assert line_items[0].confidence >= 0.8
