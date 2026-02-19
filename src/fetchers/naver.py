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
    max_pages: int = 5,
) -> list[dict]:
    headers = {
        "X-Naver-Client-Id": config.client_id,
        "X-Naver-Client-Secret": config.client_secret,
    }
    items: list[dict] = []
    session = requests.Session()
    for query in queries:
        for page in range(max_pages):
            start_idx = 1 + page * display
            if start_idx > 1000:
                break
            params = {
                "query": query,
                "display": display,
                "start": start_idx,
                "sort": "date",
            }
            response = session.get(
                NAVER_NEWS_ENDPOINT, headers=headers, params=params, timeout=10
            )
            response.raise_for_status()
            payload = response.json()
            entries = payload.get("items", [])
            if not entries:
                break

            oldest_in_page: datetime | None = None
            for entry in entries:
                pub_date = _parse_pub_date(entry.get("pubDate"))
                if pub_date is None:
                    continue
                if oldest_in_page is None or pub_date < oldest_in_page:
                    oldest_in_page = pub_date
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

            if len(entries) < display:
                break
            if oldest_in_page is not None and oldest_in_page < start:
                break
    return items
