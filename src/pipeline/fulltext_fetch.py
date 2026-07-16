from __future__ import annotations

import logging
import re
import html as ihtml
import unicodedata
from typing import Optional

import requests
from bs4 import BeautifulSoup
from charset_normalizer import from_bytes

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"

DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

# 보일러플레이트/네비/광고성 키워드(라인 제거용)
_BOILERPLATE_RE = re.compile(
    r"(구독|회원가입|로그인|기사제보|광고|제휴|전체기사|면책|무단전재|재배포|저작권|댓글|추천|공유|SNS|바로가기)"
)

def is_naver_news(url: str | None) -> bool:
    if not url:
        return False
    return ("n.news.naver.com" in url) or ("news.naver.com" in url)

def _fix_mojibake(s: str) -> str:
    """
    UTF-8 바이트가 latin1로 잘못 디코딩된 흔한 케이스 복구 시도.
    (예: ì´ë° / ÀüÃ¼º¸±â 등)
    """
    if not s:
        return s
    if any(ch in s for ch in ("ì", "Ã", "Â", "À")):
        for enc in ("utf-8", "cp949"):
            try:
                return s.encode("latin1").decode(enc)
            except Exception:
                pass
    return s

# 응답 크기 상한 — 뉴스 본문은 앞부분에 있으므로 초대형 페이지(무한 피드,
# 대용량 임베드 등)는 여기서 잘라 메모리 폭주를 막는다.
MAX_HTML_BYTES = 2_000_000


def fetch_html(url: str, timeout: int = 12, max_bytes: int = MAX_HTML_BYTES) -> str:
    """
    requests의 r.text(추정 인코딩)에 의존하지 말고,
    바이트 기반 + charset_normalizer로 디코딩해서 모지바케를 최소화.
    """
    with requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout, stream=True) as r:
        r.raise_for_status()
        header_encoding = r.encoding
        chunks: list[bytes] = []
        size = 0
        for chunk in r.iter_content(chunk_size=65536):
            chunks.append(chunk)
            size += len(chunk)
            if size >= max_bytes:
                logger.debug("fetch_html truncated %s at %d bytes", url, size)
                break
        raw = b"".join(chunks)

    best = from_bytes(raw).best()
    if best is not None:
        return str(best)

    # fallback: 헤더 선언 인코딩 → utf-8
    try:
        return raw.decode(header_encoding or "utf-8", errors="replace")
    except Exception:
        return raw.decode("utf-8", errors="replace")

def _looks_like_breadcrumb(line: str) -> bool:
    # 짧은 HOME > ... 경로류 제거
    if not line:
        return False
    if "HOME" in line.upper() and ">" in line and len(line) < 120:
        return True
    # ' > ' 가 여러 번 나오는 짧은 라인도 제거
    if line.count(">") >= 1 and len(line) < 100:
        return True
    return False

def _is_bad_container(node) -> bool:
    clsid = (" ".join(node.get("class", [])) + " " + (node.get("id") or "")).lower()
    bad_keys = [
        "nav", "menu", "gnb", "lnb", "snb",
        "header", "footer", "aside", "sidebar",
        "comment", "reply", "share", "sns",
        "banner", "ad", "advert", "promotion",
        "breadcrumb", "location", "path",
        "related", "recommend",
    ]
    return any(k in clsid for k in bad_keys)

def _clean_text(raw: str) -> str:
    raw = ihtml.unescape(raw or "")
    raw = unicodedata.normalize("NFKC", raw)
    raw = _fix_mojibake(raw)

    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    cleaned: list[str] = []
    for ln in lines:
        if len(ln) <= 3:
            continue
        if _BOILERPLATE_RE.search(ln):
            continue
        if _looks_like_breadcrumb(ln):
            continue
        cleaned.append(ln)

    text = "\n".join(cleaned)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    # 최종적으로 공백을 단일화(요약 모델 입력용)
    return re.sub(r"\s+", " ", text).strip()

def extract_main_text(url: str, html: str) -> str:
    soup = BeautifulSoup(html, "lxml")

    # 잡 태그 제거
    for tag in soup(["script", "style", "noscript", "header", "footer", "iframe", "form", "nav", "aside"]):
        tag.decompose()

    # 1) 네이버 뉴스는 셀렉터 우선
    if is_naver_news(url):
        for sel in ["#dic_area", "#articeBody", "#articleBodyContents", "#newsct_article"]:
            node = soup.select_one(sel)
            if node:
                raw = node.get_text("\n", strip=True)
                return _clean_text(raw)

    # 2) 일반 사이트: <article> / <main> 우선
    for tag_name in ("article", "main"):
        node = soup.find(tag_name)
        if node and not _is_bad_container(node):
            raw = node.get_text("\n", strip=True)
            text = _clean_text(raw)
            if len(text) > 300:
                return text

    # 3) 컨테이너 후보 스코어링 (div/section 포함)
    candidates = []
    for node in soup.find_all(["article", "main", "section", "div"], limit=800):
        if _is_bad_container(node):
            continue

        raw = node.get_text("\n", strip=True)
        if len(raw) < 400:
            continue

        p_cnt = len(node.find_all("p"))
        a_cnt = len(node.find_all("a"))
        text = _clean_text(raw)
        if len(text) < 300:
            continue

        # 점수: 길이 + p태그 보너스 - 링크 과다 패널티
        score = len(text) + (p_cnt * 220) - (a_cnt * 40)
        candidates.append((score, text))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1].strip()

    # 4) 최후 fallback
    raw = soup.get_text("\n", strip=True)
    return _clean_text(raw)
