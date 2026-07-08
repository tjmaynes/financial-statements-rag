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
        header_snippet = "\n".join(page.text for page in pages[:3])
        context_snippet = "\n".join(page.text for page in pages[:10])

        report_date = _extract_report_date(header_snippet)
        report_type = _extract_report_type(header_snippet)
        fiscal_quarter = _extract_fiscal_quarter(
            header_snippet,
            report_date,
            report_type,
        )

        return ReportMetadata(
            company_name=_extract_company_name(header_snippet),
            ticker=_extract_ticker(header_snippet),
            fiscal_year=report_date.year if report_date is not None else None,
            fiscal_quarter=fiscal_quarter,
            report_date=report_date,
            report_type=report_type,
            currency=_extract_currency(context_snippet),
            scale=_extract_scale(context_snippet),
        )


def _extract_company_name(snippet: str) -> str | None:
    lines = [raw_line.strip() for raw_line in snippet.splitlines() if raw_line.strip()]

    for index, line in enumerate(lines):
        if "exact name of registrant as specified in its charter" not in line.lower():
            continue
        if index == 0:
            continue
        candidate = lines[index - 1]
        if _is_possible_company_name(candidate):
            return _normalize_company_name(candidate)

    for line in lines:
        if _is_fallback_company_name(line):
            return _normalize_company_name(line)
    return None


def _normalize_company_name(name: str) -> str | None:
    normalized = name.upper().replace(",", " ")
    normalized = re.sub(r"[()]", " ", normalized)
    normalized = re.sub(
        r"(?:\s+\b(?:INC|INCORPORATED|LLC|L\.L\.C\.|CORPORATION|CORP|LTD|LIMITED|PLC)\b\.?)+$",
        " ",
        normalized,
    )
    normalized = re.sub(r"\s+", " ", normalized).strip(" .")
    return normalized or None


def _extract_ticker(snippet: str) -> str | None:
    match = re.search(
        r"(?:NASDAQ|NYSE|NYSE American|OTCQX)\s*:\s*([A-Z][A-Z0-9.-]{0,9})",
        snippet,
    )
    if match is None:
        for raw_line in snippet.splitlines():
            line = raw_line.strip()
            if not re.search(r"(nasdaq|nyse|otcqx)", line, flags=re.IGNORECASE):
                continue
            exchange_match = re.search(
                r"\b([A-Z][A-Z0-9.-]{0,9})\b\s+"
                r"(?:The\s+)?"
                r"(?:Nasdaq|NYSE|NYSE American|New York Stock Exchange|OTCQX)\b",
                line,
                flags=re.IGNORECASE,
            )
            if exchange_match is not None:
                return exchange_match.group(1)
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
    if snippet.count("$") >= 3:
        return "USD"
    return None


def _extract_scale(snippet: str) -> str | None:
    lowered = snippet.lower()
    scales = ("billions", "millions", "thousands")
    counts = {scale: len(re.findall(rf"\b{scale}\b", lowered)) for scale in scales}
    first_indexes = {
        scale: lowered.find(scale) if counts[scale] > 0 else -1 for scale in scales
    }
    matching_scales = [scale for scale in scales if counts[scale] > 0]
    if not matching_scales:
        return None
    return min(
        matching_scales,
        key=lambda scale: (-counts[scale], first_indexes[scale]),
    )


def _month_name_to_iso(raw_date: str) -> str:
    parsed = datetime.strptime(raw_date, "%B %d, %Y").date()
    return parsed.isoformat()


def _is_possible_company_name(line: str) -> bool:
    if not line or any(character.isdigit() for character in line):
        return False
    if _is_company_boilerplate(line):
        return False
    if not any(character.isalpha() for character in line):
        return False
    return len(line.strip()) >= 3


def _is_fallback_company_name(line: str) -> bool:
    if not _is_possible_company_name(line):
        return False
    if re.fullmatch(r"[A-Z][A-Z .,&'()/-]+", line):
        return True
    return (
        re.search(
            r"\b("
            r"inc\.?|corp\.?|corporation|company|co\.|holdings?|group|limited|ltd\.?|plc"
            r")\b",
            line,
            flags=re.IGNORECASE,
        )
        is not None
    )


def _is_company_boilerplate(line: str) -> bool:
    normalized = " ".join(line.upper().split())
    boilerplate_terms = (
        "UNITED STATES",
        "SECURITIES AND EXCHANGE COMMISSION",
        "WASHINGTON, D.C.",
        "FORM 10-Q",
        "FORM 10-K",
        "QUARTERLY REPORT",
        "ANNUAL REPORT",
        "COMMISSION FILE NUMBER",
        "TRADING SYMBOL",
        "REGISTRANT",
        "TELEPHONE NUMBER",
        "TITLE OF EACH CLASS",
        "STATE OR OTHER JURISDICTION",
        "INCORPORATION OR ORGANIZATION",
        "IDENTIFICATION NO.",
        "ADDRESS OF PRINCIPAL EXECUTIVE OFFICES",
        "LARGE ACCELERATED FILER",
        "ACCELERATED FILER",
        "NON-ACCELERATED FILER",
        "SMALLER REPORTING COMPANY",
        "EMERGING GROWTH COMPANY",
        "COMMON STOCK",
    )
    if normalized == "OR":
        return True
    return any(term in normalized for term in boilerplate_terms)
