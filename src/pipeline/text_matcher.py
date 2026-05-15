from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Iterable

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_KOREAN_RE = re.compile(r"[가-힣]")
_ASCII_ALNUM_RE = re.compile(r"^[a-z0-9]+$")


@dataclass(frozen=True)
class MatchRule:
    term: str = ""
    mode: str = "auto"
    exclude_terms: list[str] = field(default_factory=list)


_DEFAULT_EXCLUDES: dict[str, tuple[str, ...]] = {
    "신협": ("여신협회", "여신협회장", "여신금융협회", "여신전문금융협회"),
    "금융위": ("금융위기", "금융위축", "금융위험"),
    "보험": ("건강보험", "고용보험", "산재보험", "보험료", "재보험"),
    "감독": ("금융감독원", "금융감독", "감독·제재"),
    "경기": ("경기침체", "경기둔화", "경기회복", "경기민감", "경기 전망", "경기전망"),
}

_TERM_ALIASES: dict[str, tuple[str, ...]] = {
    "cp": ("기업어음",),
    "킥스": ("k-ics", "kics"),
}


def normalize_text(text: str) -> str:
    """Normalize article text for safe keyword matching."""
    value = html.unescape(str(text or ""))
    value = _HTML_TAG_RE.sub(" ", value)
    value = value.replace("\u00a0", " ")
    value = value.lower()
    return _WS_RE.sub(" ", value).strip()


def _exclude_spans(text: str, term: str, exclude_terms: Iterable[str] | None) -> list[tuple[int, int]]:
    excludes = [*(_DEFAULT_EXCLUDES.get(term, ())), *(exclude_terms or [])]
    spans: list[tuple[int, int]] = []
    for exclude in excludes:
        normalized_exclude = normalize_text(exclude)
        if not normalized_exclude:
            continue
        spans.extend(match.span() for match in re.finditer(re.escape(normalized_exclude), text))
    return spans


def _overlaps(span: tuple[int, int], exclude_spans: Iterable[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < exclude_end and exclude_start < end for exclude_start, exclude_end in exclude_spans)


def _auto_mode(term: str) -> str:
    if _ASCII_ALNUM_RE.fullmatch(term) and len(term) <= 4:
        return "english_token"
    if _KOREAN_RE.search(term) and len(term) <= 3:
        return "korean_short"
    return "phrase"


def _match_spans(text: str, term: str, mode: str) -> list[tuple[int, int]]:
    if not term:
        return []
    if mode == "phrase":
        pattern = re.escape(term)
    elif mode == "english_token":
        pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
    elif mode == "token":
        pattern = rf"(?<![가-힣a-z0-9]){re.escape(term)}(?![가-힣a-z0-9])"
    elif mode == "korean_short":
        if term == "신협":
            pattern = r"(?<![가-힣a-z0-9])신협"
        elif term == "금융위":
            pattern = r"금융위(?![기축험])"
        elif term == "보험":
            pattern = r"(?<![가-힣a-z0-9])보험(?![가-힣a-z0-9])"
        elif term == "감독":
            if not has_any_term(text, ("프로야구", "프로축구", "선수", "구단", "k리그", "mlb", "epl", "월드컵")):
                return []
            pattern = r"감독\s*경질|(?<![가-힣a-z0-9])감독(?![가-힣a-z0-9])"
        elif term == "경기":
            if not has_any_term(text, ("축구", "프로야구", "프로축구", "선수", "득점", "구단", "k리그", "mlb", "epl", "월드컵")):
                return []
            pattern = r"축구\s*경기|경기\s*결과|(?<![가-힣a-z0-9])경기(?![가-힣a-z0-9])"
        else:
            pattern = rf"(?<![가-힣a-z0-9]){re.escape(term)}(?![가-힣a-z0-9])"
    else:
        raise ValueError(f"unsupported match mode: {mode}")
    return [match.span() for match in re.finditer(pattern, text)]


def _contains_normalized(
    text: str,
    term: str,
    mode: str,
    exclude_spans: Iterable[tuple[int, int]] = (),
) -> bool:
    return any(not _overlaps(span, exclude_spans) for span in _match_spans(text, term, mode))


def contains_term(
    text: str,
    term: str,
    *,
    exclude_terms: list[str] | None = None,
    mode: str = "auto",
) -> bool:
    normalized_text = normalize_text(text)
    normalized_term = normalize_text(term)
    if not normalized_text or not normalized_term:
        return False

    excludes = _exclude_spans(normalized_text, normalized_term, exclude_terms)
    aliases = _TERM_ALIASES.get(normalized_term, ())
    chosen_mode = _auto_mode(normalized_term) if mode == "auto" else mode
    if _contains_normalized(normalized_text, normalized_term, chosen_mode, excludes):
        return True
    return any(_contains_normalized(normalized_text, normalize_text(alias), "phrase") for alias in aliases)


def find_terms(
    text: str,
    terms: Iterable[str],
    *,
    term_rules: dict[str, MatchRule] | None = None,
) -> list[str]:
    matches: list[str] = []
    for term in terms:
        rule = (term_rules or {}).get(term)
        target = rule.term if rule and rule.term else term
        mode = rule.mode if rule else "auto"
        excludes = rule.exclude_terms if rule else None
        if contains_term(text, target, exclude_terms=excludes, mode=mode):
            matches.append(term)
    return matches


def has_any_term(
    text: str,
    terms: Iterable[str],
    *,
    term_rules: dict[str, MatchRule] | None = None,
) -> bool:
    return bool(find_terms(text, terms, term_rules=term_rules))
