from __future__ import annotations

import re
from collections import Counter

STOPWORDS = {
    "기자",
    "사진",
    "연합뉴스",
    "뉴스",
    "보도",
    "오늘",
    "이번",
    "관련",
    "통해",
    "대한",
    "등",
    "있다",
    "했다",
    "한다고",
    "따라",
    "때문",
    "지난",
    "가장",
    "면서",
    "그리고",
}

_BREADCRUMB_HEAD = re.compile(
    r"^\s*(HOME|Home|홈)\s*(>\s*[^>]{1,20}){1,8}\s*", re.IGNORECASE
)
_WS_RE = re.compile(r"\s+")
_PAREN_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_REPORTER_RE = re.compile(r"[가-힣]{2,4}\s*기자")
_PHOTO_RE = re.compile(r"사진\s*=")
_BYLINE_PREFIX_RE = re.compile(r"^[가-힣A-Za-z]{2,12}\s*=\s*(연합뉴스|뉴스1|뉴시스)\s*")
_ENG_CHAR_RE = re.compile(r"[A-Za-z]")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://|www\.")
_NUMERIC_SIGNAL_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|bp|원|만원|억원|조원|달러|엔|유로|명|건|배|p|개월|년|일)|"
    r"(?:금리|환율|연체율|발행|시행일|만기|기준금리|코스피|코스닥)"
)
_ACTION_SIGNAL_RE = re.compile(
    r"(발표|밝혔|결정|시행|개편|확대|축소|제재|조사|징계|연체|발행|동결|인상|인하|강화|완화|판결|선고)"
)
_JUNK_PATTERNS = [
    re.compile(p)
    for p in [
        r"무단전재",
        r"재배포\s*금지",
        r"저작권자",
        r"구독",
        r"광고",
        r"기사제보",
        r"사진\s*=",
        r"기자\s*=",
        r"ⓒ",
        r"copyright",
        r"주요\s*일정",
        r"행사\s*안내",
        r"관련\s*기사",
        r"추천\s*기사",
        r"기사\s*원문",
        r"show\s*more",
        r"url\s*복사",
        r"카카오톡|페이스북|트위터|\bX\b",
        r"로그인|회원가입",
        r"무료\s*공개|유료|구독\s*신청|구독하고",
        r"기사를?\s*더\s*보",
        r"이\s*기사를",
        r"후\s*무료공개",
    ]
]
_LINE_JUNK_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"^\s*(show\s*more|read\s*more|more)\s*$",
        r"^\s*(url\s*복사|링크\s*복사|기사\s*보기|전체\s*보기)\s*$",
        r"^\s*(카카오톡|페이스북|트위터|공유|공유하기)\s*$",
        r"^\s*(관련\s*기사|추천\s*기사|많이\s*본\s*기사)\s*$",
        r"^\s*(로그인|회원가입|구독|구독하기|유료|무료공개)\s*$",
        r"^\s*(사진|이미지)\s*설명[:：]",
        r"(무단전재|재배포\s*금지|저작권자|copyright)",
        r"^\s*[가-힣]{2,5}\s*기자\s*$",
        r"^\s*[가-힣A-Za-z]{2,12}\s*=\s*(연합뉴스|뉴스1|뉴시스)\s*$",
        r"^\s*서울=연합뉴스",
        r"^\s*입력\s*\d{4}[-./]\d{1,2}[-./]\d{1,2}",
    ]
]
_INLINE_STRIP_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bshow\s*more\b",
        r"\burl\s*복사\b",
        r"\b카카오톡\b",
        r"\b페이스북\b",
        r"\b트위터\b",
        r"\b관련\s*기사\b",
        r"\b추천\s*기사\b",
        r"\b기사\s*원문\b",
        r"\b이\s*기사를[^.]{0,20}$",
        r"\b후\s*무료공개\b",
    ]
]
_TYPE_KEYWORDS = {
    "policy": {"정책", "제도", "법안", "입법", "시행", "개편", "지원", "규정"},
    "enforcement": {"감독", "제재", "징계", "조사", "검사", "위반", "중징계", "취소"},
    "market": {"금리", "환율", "채권", "국채", "외화채", "코스피", "코스닥", "동결", "인상", "인하"},
    "banking": {"은행", "대출", "여신", "예대금리", "주담대", "전세대출", "신용대출", "토스뱅크"},
    "earnings": {"실적", "순이익", "영업이익", "매출", "가이던스", "분기", "연간", "경영"},
}
_TYPE_BOOST = {
    "policy": re.compile(r"(시행|개편|바뀌|변경|적용|대상|지원|확대|축소)"),
    "enforcement": re.compile(r"(제재|징계|조사|판단|취소|위반|권한)"),
    "market": re.compile(r"(상승|하락|동결|인상|인하|발행|수급|변동|영향)"),
    "banking": re.compile(r"(은행|대출|금리|여신|한도|차주|이자)"),
    "earnings": re.compile(r"(실적|매출|이익|비용|손실|수익성|전년|전분기)"),
}


def _is_low_info_line(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return True
    if _EMAIL_RE.search(s):
        return True
    if re.fullmatch(r"[\W_]+", s):
        return True
    if len(s) < 6 and not re.search(r"\d|[가-힣A-Za-z]", s):
        return True
    if len(s) <= 12 and not re.search(r"[가-힣]{2,}|\d{2,}", s):
        return True
    if _URL_RE.search(s) and len(s) < 50:
        return True
    if sum(1 for _ in _ENG_CHAR_RE.finditer(s)) / max(len(s), 1) > 0.75 and len(s) < 40:
        return True
    if re.fullmatch(r"[\d\s:./-]{5,}", s):
        return True
    return any(p.search(s) for p in _LINE_JUNK_PATTERNS)


def _clean_source_text(text: str) -> str:
    t = (text or "").replace("\r", "\n")
    t = _BREADCRUMB_HEAD.sub("", t)
    lines = [ln.strip() for ln in re.split(r"\n+", t)]
    cleaned_lines: list[str] = []
    seen: set[str] = set()
    for ln in lines:
        if not ln or _is_low_info_line(ln):
            continue
        trimmed = ln
        for p in _INLINE_STRIP_PATTERNS:
            trimmed = p.sub(" ", trimmed)
        trimmed = _WS_RE.sub(" ", trimmed).strip(" -|·•\t")
        if not trimmed or _is_low_info_line(trimmed):
            continue
        norm_key = trimmed.lower()
        if norm_key in seen:
            continue
        seen.add(norm_key)
        cleaned_lines.append(trimmed)

    compact = " ".join(cleaned_lines)
    compact = _WS_RE.sub(" ", compact).strip()
    return compact


def split_sentences(text: str) -> list[str]:
    t = _clean_source_text(text)
    if not t:
        return []

    parts = re.split(r"(?<=[\.!?])\s+", t)
    return [p.strip() for p in parts if len(p.strip()) >= 16]


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[가-힣]{2,}|[A-Za-z0-9]{2,}", text)
    return [t for t in tokens if t not in STOPWORDS]


def _detect_article_type(title: str, text: str) -> str | None:
    bag = set(tokenize(f"{title} {text[:1200]}"))
    best_type, best_score = None, 0
    for k, kws in _TYPE_KEYWORDS.items():
        score = len(bag.intersection(kws))
        if score > best_score:
            best_type, best_score = k, score
    return best_type if best_score > 0 else None


def _looks_like_junk(sent: str) -> bool:
    s = (sent or "").strip()
    if len(s) < 16:
        return True
    eng_ratio = sum(1 for _ in _ENG_CHAR_RE.finditer(s)) / max(len(s), 1)
    if eng_ratio > 0.45:
        return True
    if eng_ratio > 0.35 and len(s) < 36:
        return True
    if _EMAIL_RE.search(s):
        return True
    if _URL_RE.search(s):
        return True
    if re.fullmatch(r"[\d\W_]+", s):
        return True
    if re.search(r"(사진|이미지)\s*(설명|캡션|제공|출처)", s):
        return True
    if re.search(r"(메뉴|버튼|공유|복사|클릭)", s) and len(s) < 60:
        return True
    if re.search(r"\d{1,2}:\d{2}", s) and len(re.findall(r"\d{1,2}:\d{2}", s)) >= 2:
        return True
    if re.search(r"(\d{1,2}:\d{2}|오전|오후)", s) and re.search(r"(일정|행사|개최|세미나)", s):
        return True
    return any(p.search(s) for p in _JUNK_PATTERNS)


def clean_sentence(sent: str, hard_limit: int = 150) -> str:
    s = _PAREN_RE.sub(" ", sent or "")
    s = _BYLINE_PREFIX_RE.sub("", s)
    s = _REPORTER_RE.sub(" ", s)
    s = _PHOTO_RE.sub(" ", s)
    s = re.sub(r"\s*[-–—]\s*", " ", s)
    s = _WS_RE.sub(" ", s).strip(" .,;:-")
    if len(s) > hard_limit:
        cut = s[:hard_limit].rsplit(" ", 1)[0].strip()
        s = (cut or s[:hard_limit]).rstrip(" ,")
    if s and not re.search(r"[.!?]$", s):
        s += "."
    return s


def _title_overlap_score(sent: str, title_tokens: set[str]) -> float:
    if not title_tokens:
        return 0.0
    sent_tokens = set(tokenize(sent))
    overlap = len(sent_tokens.intersection(title_tokens))
    return min(2.2, overlap * 0.55)


def _score_sentence(
    sent: str,
    idx: int,
    freq: Counter[str],
    title_tokens: set[str],
    article_type: str | None,
) -> float:
    toks = tokenize(sent)
    if not toks:
        return -999.0

    score = sum(freq[t] for t in toks) / (len(toks) ** 0.72)
    score += _title_overlap_score(sent, title_tokens)

    if _NUMERIC_SIGNAL_RE.search(sent):
        score += 1.2
    if _ACTION_SIGNAL_RE.search(sent):
        score += 1.0
    if idx <= 1:
        score += 0.7
    if article_type and _TYPE_BOOST[article_type].search(sent):
        score += 1.1

    if _looks_like_junk(sent):
        score -= 4.5
    return score


def _is_quality_summary(summary: str, title: str) -> bool:
    s = (summary or "").strip()
    if len(s) < 24:
        return False
    if _looks_like_junk(s):
        return False
    title_clean = clean_sentence(title, hard_limit=200).rstrip(".")
    if title_clean and s.rstrip(".") == title_clean:
        return False
    return True


def summarize(
    text: str,
    max_sentences: int = 2,
    max_chars: int = 220,
    title: str = "",
) -> str:
    sents = split_sentences(text)
    if not sents:
        return ""

    joined = " ".join(sents[:80])
    freq = Counter(tokenize(joined))
    if not freq:
        first = clean_sentence(sents[0], hard_limit=min(150, max_chars))
        return first[:max_chars].rstrip()

    title_tokens = set(tokenize(title))
    article_type = _detect_article_type(title, joined)
    scored = [
        (i, _score_sentence(s, i, freq, title_tokens, article_type), s)
        for i, s in enumerate(sents)
    ]
    scored.sort(key=lambda x: x[1], reverse=True)

    picked: list[tuple[int, float, str]] = []
    seen: set[str] = set()
    for i, sc, s in scored:
        cleaned = clean_sentence(s)
        if not cleaned or cleaned in seen or _looks_like_junk(cleaned):
            continue
        picked.append((i, sc, cleaned))
        seen.add(cleaned)
        if len(picked) >= max_sentences:
            break

    if not picked:
        return ""

    first = max(
        picked,
        key=lambda x: (
            bool(_ACTION_SIGNAL_RE.search(x[2])) + bool(_NUMERIC_SIGNAL_RE.search(x[2])),
            x[1] - (0.08 * x[0]),
        ),
    )
    remaining = [p for p in picked if p != first]
    second = None
    if remaining:
        second = max(
            remaining,
            key=lambda x: (
                bool(_NUMERIC_SIGNAL_RE.search(x[2]))
                + bool(re.search(r"(영향|대상|차주|시장|업계)", x[2])),
                x[1],
            ),
        )

    out_parts = [first[2]]
    if second:
        out_parts.append(second[2])
    out = " ".join(out_parts)

    if len(out) > max_chars:
        out = out[:max_chars].rsplit(" ", 1)[0].rstrip(" ,")
        if out and not re.search(r"[.!?]$", out):
            out += "."
    return out.strip()


def summarize_with_fallback(
    full_text: str,
    *,
    title: str = "",
    description: str = "",
    max_chars: int = 220,
) -> str:
    candidates = [
        summarize(full_text, max_sentences=2, max_chars=max_chars, title=title),
        summarize(description, max_sentences=2, max_chars=max_chars, title=title),
        clean_sentence(title, hard_limit=max_chars),
    ]
    for c in candidates:
        if _is_quality_summary(c, title):
            return c
    return clean_sentence(title, hard_limit=max_chars)
