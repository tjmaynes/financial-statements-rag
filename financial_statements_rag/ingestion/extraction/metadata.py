from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
import re

from financial_statements_rag.ingestion.extraction.pdf import ExtractedPage


@dataclass(frozen=True)
class ReportMetadata:
    company_name: str | None = None
    ticker: str | None = None
    fiscal_year: int | None = None
    fiscal_quarter: str | None = None
    report_date: date | None = None
    report_type: str | None = None
    currency: str | None = None
    scale: str | None = None


class ReportMetadataInferer:
    def infer(self, pages: Sequence[ExtractedPage]) -> ReportMetadata:
        snippet = "\n".join(page.text for page in pages[:3])

        report_date = _extract_report_date(snippet)
        report_type = _extract_report_type(snippet)

        return ReportMetadata(
            company_name=_extract_company_name(snippet),
            ticker=_extract_ticker(snippet),
            fiscal_year=report_date.year if report_date is not None else None,
            fiscal_quarter=_extract_fiscal_quarter(snippet, report_date, report_type),
            report_date=report_date,
            report_type=report_type,
            currency=_extract_currency(snippet),
            scale=_extract_scale(snippet),
        )


def _extract_company_name(snippet: str) -> str | None:
    for raw_line in snippet.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.search(
            r"(NASDAQ|NYSE|FORM 10-[QK]|QUARTERLY REPORT|ANNUAL REPORT)", line
        ):
            continue
        if re.fullmatch(r"[A-Z0-9][A-Z0-9 .,&'()/-]+", line):
            return line
    return None


def _extract_ticker(snippet: str) -> str | None:
    match = re.search(
        r"(?:NASDAQ|NYSE|NYSE American|OTCQX)\s*:\s*([A-Z][A-Z0-9.-]{0,9})",
        snippet,
    )
    if match is None:
        return None
    return match.group(1)


def _extract_report_date(snippet: str) -> date | None:
    match = re.search(
        r"(?:quarterly period ended|fiscal quarter ended|year ended|ended)\s+"
        r"([A-Z][a-z]+ \d{1,2}, \d{4})",
        snippet,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return date.fromisoformat(_month_name_to_iso(match.group(1)))


def _extract_report_type(snippet: str) -> str | None:
    lowered = snippet.lower()
    if "10-q" in lowered or "quarterly report" in lowered:
        return "quarterly"
    if "10-k" in lowered or "annual report" in lowered:
        return "annual"
    return None


def _extract_fiscal_quarter(
    snippet: str,
    report_date: date | None,
    report_type: str | None,
) -> str | None:
    match = re.search(r"\b(Q[1-4])\s+(20\d{2})\b", snippet, flags=re.IGNORECASE)
    if match is not None:
        return match.group(1).upper()
    if report_date is None or report_type != "quarterly":
        return None
    quarter = ((report_date.month - 1) // 3) + 1
    return f"Q{quarter}"


def _extract_currency(snippet: str) -> str | None:
    lowered = snippet.lower()
    if (
        "u.s. dollars" in lowered
        or "united states dollars" in lowered
        or "usd" in lowered
    ):
        return "USD"
    return None


def _extract_scale(snippet: str) -> str | None:
    lowered = snippet.lower()
    if "in billions" in lowered:
        return "billions"
    if "in millions" in lowered:
        return "millions"
    if "in thousands" in lowered:
        return "thousands"
    return None


def _month_name_to_iso(raw_date: str) -> str:
    parsed = datetime.strptime(raw_date, "%B %d, %Y").date()
    return parsed.isoformat()
