from __future__ import annotations

from datetime import date

from financial_statements_rag.ingestion.indexing.chunking import (
    IndexedDocument,
    StatementSection,
    build_document_id,
    build_section_id,
    create_document_chunks,
)


def _document() -> IndexedDocument:
    document_id = build_document_id("job-123", "data/uploads/report.pdf")
    return IndexedDocument(
        document_id=document_id,
        job_id="job-123",
        stored_path="data/uploads/report.pdf",
        original_filename="report.pdf",
        company_name="Example Corp",
        ticker="EXM",
        fiscal_year=2026,
        fiscal_quarter="Q2",
        report_date=date(2026, 6, 30),
        report_type="10-Q",
        currency="USD",
        scale="millions",
        page_count=8,
    )


def _section(
    document_id: str,
    *,
    statement_type: str,
    title: str,
    page_start: int,
    raw_text: str,
) -> StatementSection:
    return StatementSection(
        section_id=build_section_id(
            document_id,
            statement_type=statement_type,
            page_start=page_start,
            page_end=page_start,
            title=title,
        ),
        document_id=document_id,
        statement_type=statement_type,
        title=title,
        page_start=page_start,
        page_end=page_start,
        raw_text=raw_text,
        confidence=0.95,
    )


def test_statement_chunks_are_created_before_remaining_text() -> None:
    document = _document()
    balance_sheet = _section(
        document.document_id,
        statement_type="balance_sheet",
        title="Condensed Consolidated Balance Sheets",
        page_start=3,
        raw_text="Cash and cash equivalents 100. Accounts receivable 40. Total assets 400.",
    )
    cash_flow = _section(
        document.document_id,
        statement_type="cash_flow_statement",
        title="Condensed Consolidated Statements of Cash Flows",
        page_start=5,
        raw_text="Net cash provided by operating activities 33. Capital expenditures 12.",
    )
    document_text = (
        "Cover page summary and legal boilerplate. "
        f"{balance_sheet.raw_text} "
        "Management discussion and analysis continues here. "
        f"{cash_flow.raw_text} "
        "Notes to unaudited condensed consolidated financial statements."
    )

    chunks = create_document_chunks(
        document,
        document_text=document_text,
        sections=[cash_flow, balance_sheet],
        chunk_size=90,
        chunk_overlap=10,
        index_remaining_text=True,
    )

    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert [chunk.statement_type for chunk in chunks[:2]] == [
        "balance_sheet",
        "cash_flow_statement",
    ]
    assert chunks[0].section_title == balance_sheet.title
    assert chunks[1].section_title == cash_flow.title
    assert chunks[-1].statement_type is None
    assert any(
        chunk.statement_type is None and "Management discussion" in chunk.text
        for chunk in chunks
    )


def test_chunk_ids_and_metadata_are_deterministic() -> None:
    document = _document()
    section = _section(
        document.document_id,
        statement_type="income_statement",
        title="Condensed Consolidated Statements of Operations",
        page_start=4,
        raw_text=(
            "Revenue 125. Cost of revenue 70. Gross profit 55. "
            "Research and development 11. Net income 18."
        ),
    )
    document_text = f"Forward-looking statements. {section.raw_text} Notes."

    first_run = create_document_chunks(
        document,
        document_text=document_text,
        sections=[section],
        chunk_size=60,
        chunk_overlap=8,
        index_remaining_text=True,
    )
    second_run = create_document_chunks(
        document,
        document_text=document_text,
        sections=[section],
        chunk_size=60,
        chunk_overlap=8,
        index_remaining_text=True,
    )

    assert [chunk.chunk_id for chunk in first_run] == [
        chunk.chunk_id for chunk in second_run
    ]
    assert first_run[0].document_id == document.document_id
    assert first_run[0].job_id == document.job_id
    assert first_run[0].company_name == "Example Corp"
    assert first_run[0].ticker == "EXM"
    assert first_run[0].fiscal_year == 2026
    assert first_run[0].fiscal_quarter == "Q2"
    assert first_run[0].report_type == "10-Q"
    assert first_run[0].statement_type == "income_statement"
