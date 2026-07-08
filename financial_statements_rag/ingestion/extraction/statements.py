from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
import re

from financial_statements_rag.ingestion.extraction.metadata import ReportMetadata
from financial_statements_rag.ingestion.extraction.pdf import ExtractedPage


class StatementType(StrEnum):
    BALANCE_SHEET = "balance_sheet"
    INCOME_STATEMENT = "income_statement"
    CASH_FLOW_STATEMENT = "cash_flow_statement"


@dataclass(frozen=True)
class StatementSection:
    statement_type: StatementType
    title: str
    page_start: int
    page_end: int
    raw_text: str
    confidence: float


@dataclass(frozen=True)
class StatementLineItem:
    statement_type: StatementType
    line_item_label: str
    raw_value_text: str | None
    period_label: str | None
    currency: str | None
    scale: str | None
    page_number: int
    source_text: str
    confidence: float


@dataclass(frozen=True)
class StatementDetectionResult:
    sections: tuple[StatementSection, ...]
    warnings: tuple[str, ...] = ()


_TITLE_PATTERNS: tuple[tuple[StatementType, tuple[re.Pattern[str], ...]], ...] = (
    (
        StatementType.BALANCE_SHEET,
        (
            re.compile(
                r"^(?:condensed\s+)?(?:consolidated\s+)?balance sheets$",
                flags=re.IGNORECASE,
            ),
            re.compile(
                r"^(?:condensed\s+)?(?:consolidated\s+)?statements? of financial position$",
                flags=re.IGNORECASE,
            ),
        ),
    ),
    (
        StatementType.INCOME_STATEMENT,
        (
            re.compile(
                r"^(?:condensed\s+)?(?:consolidated\s+)?statements? of operations$",
                flags=re.IGNORECASE,
            ),
            re.compile(
                r"^(?:condensed\s+)?(?:consolidated\s+)?statements? of income$",
                flags=re.IGNORECASE,
            ),
            re.compile(
                r"^(?:condensed\s+)?(?:consolidated\s+)?income statements?$",
                flags=re.IGNORECASE,
            ),
        ),
    ),
    (
        StatementType.CASH_FLOW_STATEMENT,
        (
            re.compile(
                r"^(?:condensed\s+)?(?:consolidated\s+)?statements? of cash flows$",
                flags=re.IGNORECASE,
            ),
        ),
    ),
)

_VALUE_PATTERN = re.compile(r"\(?\$?\d[\d,]*(?:\.\d+)?\)?")
_TRAILING_PERIOD_PATTERN = re.compile(r"(Q[1-4]\s+\d{4}|[A-Z][a-z]+ \d{1,2}, \d{4})$")
_HEADER_PERIOD_PATTERN = re.compile(r"\b(Q[1-4]\s+\d{4}|[A-Z][a-z]+ \d{1,2}, \d{4})\b")
_KNOWN_LABEL_FRAGMENTS: tuple[str, ...] = (
    "cash and cash equivalents",
    "accounts receivable",
    "inventory",
    "total assets",
    "revenue",
    "net income",
    "operating income",
    "operating expenses",
    "cost of revenue",
    "net cash provided by operating activities",
    "net cash used in investing activities",
    "net cash used in financing activities",
)


class StatementExtractor:
    def detect_sections(
        self,
        pages: Sequence[ExtractedPage],
    ) -> StatementDetectionResult:
        page_list = sorted(pages, key=lambda page: page.page_number)
        detections: list[tuple[ExtractedPage, StatementType, str, float]] = []

        for page in page_list:
            match = _match_statement_title(page.text)
            if match is not None:
                detections.append((page, *match))

        if not detections:
            return StatementDetectionResult(
                sections=(),
                warnings=("NO_STATEMENT_SECTIONS_FOUND",),
            )

        sections: list[StatementSection] = []
        last_page_number = page_list[-1].page_number

        for index, (page, statement_type, title, confidence) in enumerate(detections):
            next_page_start = (
                detections[index + 1][0].page_number
                if index + 1 < len(detections)
                else None
            )
            page_end = (
                next_page_start - 1 if next_page_start is not None else last_page_number
            )
            raw_text = "\n".join(
                candidate.text
                for candidate in page_list
                if page.page_number <= candidate.page_number <= page_end
            )
            sections.append(
                StatementSection(
                    statement_type=statement_type,
                    title=title,
                    page_start=page.page_number,
                    page_end=page_end,
                    raw_text=raw_text,
                    confidence=confidence,
                )
            )

        return StatementDetectionResult(sections=tuple(sections))

    def extract_line_items(
        self,
        sections: Sequence[StatementSection],
        pages: Sequence[ExtractedPage],
        metadata: ReportMetadata | None = None,
    ) -> tuple[StatementLineItem, ...]:
        page_by_number = {page.page_number: page for page in pages}
        line_items: list[StatementLineItem] = []

        for section in sections:
            for page_number in range(section.page_start, section.page_end + 1):
                page = page_by_number.get(page_number)
                if page is None:
                    continue
                header_period = _extract_header_period(page.text)
                for raw_line in page.text.splitlines():
                    line = raw_line.strip()
                    if not line or line == section.title:
                        continue
                    if _looks_like_non_item_line(line):
                        continue
                    line_item = _parse_line_item(
                        line=line,
                        statement_type=section.statement_type,
                        page_number=page_number,
                        currency=metadata.currency if metadata is not None else None,
                        scale=metadata.scale if metadata is not None else None,
                        fallback_period=header_period,
                    )
                    if line_item is not None:
                        line_items.append(line_item)

        return tuple(line_items)


def _match_statement_title(
    page_text: str,
) -> tuple[StatementType, str, float] | None:
    for raw_line in page_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for statement_type, patterns in _TITLE_PATTERNS:
            for pattern in patterns:
                if pattern.fullmatch(line):
                    confidence = (
                        0.98
                        if "statements of financial position" not in pattern.pattern
                        else 0.9
                    )
                    return statement_type, line, confidence
    return None


def _looks_like_non_item_line(line: str) -> bool:
    lowered = line.lower()
    return lowered.startswith("see accompanying notes")


def _extract_header_period(page_text: str) -> str | None:
    for raw_line in page_text.splitlines()[:5]:
        line = raw_line.strip()
        match = _HEADER_PERIOD_PATTERN.search(line)
        if match is not None:
            return match.group(1)
    return None


def _parse_line_item(
    *,
    line: str,
    statement_type: StatementType,
    page_number: int,
    currency: str | None,
    scale: str | None,
    fallback_period: str | None,
) -> StatementLineItem | None:
    if not any(character.isalpha() for character in line):
        return None

    period_match = _TRAILING_PERIOD_PATTERN.search(line)
    period_label = (
        period_match.group(1) if period_match is not None else fallback_period
    )
    line_without_period = (
        line[: period_match.start()].rstrip() if period_match is not None else line
    )

    value_match = None
    for candidate in _VALUE_PATTERN.finditer(line_without_period):
        value_match = candidate
    if value_match is None:
        return None

    label = line_without_period[: value_match.start()].strip(" .\t")
    if not label or not any(character.isalpha() for character in label):
        return None

    confidence = 0.8
    if period_label is not None:
        confidence += 0.05
    if _looks_like_known_label(label):
        confidence += 0.1

    return StatementLineItem(
        statement_type=statement_type,
        line_item_label=label,
        raw_value_text=value_match.group(0),
        period_label=period_label,
        currency=currency,
        scale=scale,
        page_number=page_number,
        source_text=line,
        confidence=min(confidence, 0.99),
    )


def _looks_like_known_label(label: str) -> bool:
    lowered = label.lower()
    return any(fragment in lowered for fragment in _KNOWN_LABEL_FRAGMENTS)
