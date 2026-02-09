from __future__ import annotations

import logging
from datetime import datetime
from html import unescape
from typing import Iterable

import requests
from dateutil import parser as date_parser

from src.config import NaverConfig

NAVER_NEWS_ENDPOINT = "https://openapi.naver.com/v1/search/news.json"

logger = logging.getLogger(__name__)


def _parse_pub_date(value: str) -> datetime | None:
    try:
        return date_parser.parse(value)
    except (ValueError, TypeError) as exc:
        logger.warning("Failed to parse pubDate %s: %s", value, exc)
        return None


def _clean_text(text: str) -> str:
    return unescape(text).replace("<b>", "").replace("</b>", "").strip()


def fetch_news(
    config: NaverConfig,
    queries: Iterable[str],
    start: datetime,
    end: datetime,
    display: int = 100,
) -> list[dict]:
    headers = {
        "X-Naver-Client-Id": config.client_id,
        "X-Naver-Client-Secret": config.client_secret,
    }
    items: list[dict] = []
    session = requests.Session()
    for query in queries:
        params = {
            "query": query,
            "display": display,
            "start": 1,
            "sort": "date",
        }
        response = session.get(NAVER_NEWS_ENDPOINT, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        payload = response.json()

        for entry in payload.get("items", []):
            pub_date = _parse_pub_date(entry.get("pubDate"))
            if pub_date is None:
                continue
            if pub_date < start or pub_date >= end:
                continue

            # link: (대개) 네이버 뉴스 링크
            # originallink: 언론사 원문 링크
            naver_link = (entry.get("link") or "").strip()
            originallink = (entry.get("originallink") or "").strip() or None

            # 사용자가 클릭할 링크는 원문 우선(없으면 네이버 링크)
            display_link = originallink or naver_link

            items.append(
                {
                    "title": _clean_text(entry.get("title", "")),
                    "description": _clean_text(entry.get("description", "")),
                    "link": display_link,
                    "originallink": originallink,
                    "naver_link": naver_link or None,
                    "pubDate": pub_date,
                    "query": query,
                }
            )
    return items
