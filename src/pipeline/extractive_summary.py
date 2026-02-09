from __future__ import annotations
import re
from collections import Counter

STOPWORDS = {
    "기자", "사진", "연합뉴스", "뉴스", "보도", "오늘", "이번", "관련", "통해", "대한", "등",
    "있다", "했다", "한다고", "따라", "때문", "지난", "가장", "면서", "그리고"
}

_BREADCRUMB_HEAD = re.compile(r"^\s*(HOME|Home|홈)\s*(>\s*[^>]{1,20}){1,8}\s*", re.IGNORECASE)

def split_sentences(text: str) -> list[str]:
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return []

    # 빵크럼/경로가 맨 앞에 붙는 케이스 제거
    t = _BREADCRUMB_HEAD.sub("", t).strip()

    # 아주 단순한 문장 분리(무료/가벼움)
    parts = re.split(r"(?<=[\.\?\!])\s+|(?<=다\.)\s+|(?<=요\.)\s+|(?<=니다\.)\s+", t)
    sents = [p.strip() for p in parts if len(p.strip()) >= 20]
    return sents

def tokenize(text: str) -> list[str]:
    # 한글 2자 이상 + 영문/숫자 토큰
    tokens = re.findall(r"[가-힣]{2,}|[A-Za-z0-9]{2,}", text)
    return [t for t in tokens if t not in STOPWORDS]

def summarize(text: str, max_sentences: int = 3, max_chars: int = 320) -> str:
    sents = split_sentences(text)
    if not sents:
        return ""

    # 너무 길면 앞부분 위주로(속도/안정)
    joined = " ".join(sents[:80])
    freq = Counter(tokenize(joined))
    if not freq:
        return sents[0][:max_chars]

    def score(sent: str) -> float:
        toks = tokenize(sent)
        if not toks:
            return 0.0
        return sum(freq[t] for t in toks) / (len(toks) ** 0.7)

    scored = [(i, score(s), s) for i, s in enumerate(sents)]
    scored.sort(key=lambda x: x[1], reverse=True)
    picked = sorted(scored[:max_sentences], key=lambda x: x[0])
    out = " ".join(s for _, __, s in picked).strip()
    return out[:max_chars].rstrip()
