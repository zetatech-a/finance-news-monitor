from __future__ import annotations

import logging
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"

def is_naver_news(url: str | None) -> bool:
    if not url:
        return False
    return ("n.news.naver.com" in url) or ("news.naver.com" in url)

def fetch_html(url: str, timeout: int = 10) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    return r.text

def extract_main_text(url: str, html: str) -> str:
    soup = BeautifulSoup(html, "lxml")

    # 잡 태그 제거
    for tag in soup(["script", "style", "noscript", "header", "footer", "iframe", "form"]):
        tag.decompose()

    # 1) 네이버 뉴스는 셀렉터 우선
    if is_naver_news(url):
        for sel in ["#dic_area", "#articeBody", "#articleBodyContents", "#newsct_article"]:
            node = soup.select_one(sel)
            if node:
                text = node.get_text(" ", strip=True)
                return re.sub(r"\s+", " ", text).strip()

    # 2) 일반 사이트: <article> 우선
    article = soup.find("article")
    if article:
        text = article.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 200:
            return text

    # 3) 가장 길게 텍스트를 가진 div/section 찾기(가장 단순한 heuristic)
    best_text = ""
    for node in soup.find_all(["div", "section"], limit=300):
        text = node.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > len(best_text):
            best_text = text

    return best_text.strip()
