from __future__ import annotations

import re
from collections.abc import Iterable

FIN_POS = [
    "금리", "기준금리", "예대금리", "대출", "대환", "연체", "부실", "NPL", "부실채권",
    "채권추심", "추심", "최고금리", "가계부채", "DSR", "LTV", "규제", "제재", "검사",
    "금감원", "금융위", "금융위원회", "금융감독원", "수수료", "가맹점", "카드론",
    "보험료", "실손", "지급여력", "IFRS17", "공매도", "IPO", "상장", "불공정거래",
    "PF", "건전성", "충당금", "실적", "순이익"
]

NON_FIN_NEG = [
    "프로야구", "프로배구", "선수", "감독", "경기", "득점", "우승", "개봉", "드라마",
    "연예", "맛집", "여행", "날씨", "운세", "사건사고"
]

def _text(article) -> str:
    # dict/객체 모두 대응
    if isinstance(article, dict):
        title = (article.get("title") or "").strip()
        summary = (article.get("summary") or article.get("description") or "").strip()
    else:
        title = (getattr(article, "title", "") or "").strip()
        summary = (getattr(article, "summary", "") or getattr(article, "description", "") or "").strip()
    return f"{title}\n{summary}".strip()

def relevance_score(article) -> int:
    t = _text(article)
    if not t:
        return 0
    score = 0
    for w in FIN_POS:
        if w in t:
            score += 1
    for w in NON_FIN_NEG:
        if w in t:
            score -= 2
    return score
