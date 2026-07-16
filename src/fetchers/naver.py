from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from html import unescape
from typing import Iterable

import requests
from dateutil import parser as date_parser

from src.config import KST, NaverConfig, env_float as _env_float, env_int as _env_int

NAVER_NEWS_ENDPOINT = "https://openapi.naver.com/v1/search/news.json"
DEFAULT_NAVER_HTTP_TIMEOUT_SECONDS = 10.0
DEFAULT_NAVER_RETRY_ATTEMPTS = 3
DEFAULT_NAVER_RETRY_BACKOFF_SECONDS = 1.0
MAX_NAVER_HTTP_TIMEOUT_SECONDS = 60.0
MAX_NAVER_RETRY_ATTEMPTS = 5
MAX_NAVER_RETRY_BACKOFF_SECONDS = 30.0
RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}

logger = logging.getLogger(__name__)


def _parse_pub_date(value: str) -> datetime | None:
    try:
        parsed = date_parser.parse(value)
    except (ValueError, TypeError) as exc:
        logger.warning("Failed to parse pubDate %s: %s", value, exc)
        return None
    if parsed.tzinfo is None:
        # tz가 빠진 pubDate가 오면 KST로 간주한다 — naive datetime이 그대로
        # 흘러가면 수집 윈도우(aware)와의 비교에서 TypeError로 전체 실행이 죽는다.
        parsed = parsed.replace(tzinfo=KST)
    return parsed


def _clean_text(text: str) -> str:
    return unescape(text).replace("<b>", "").replace("</b>", "").strip()


def _is_retryable_status(status_code: int) -> bool:
    return status_code in RETRYABLE_HTTP_STATUSES


def _backoff_seconds(initial_backoff: float, attempt: int) -> float:
    return min(initial_backoff * (2 ** (attempt - 1)), MAX_NAVER_RETRY_BACKOFF_SECONDS)


def _request_with_retry(
    session: requests.Session,
    *,
    url: str,
    headers: dict[str, str],
    params: dict[str, object],
    timeout: float,
    attempts: int,
    backoff: float,
) -> requests.Response:
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, headers=headers, params=params, timeout=timeout)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_error = exc
            if attempt >= attempts:
                break
            wait = _backoff_seconds(backoff, attempt)
            logger.warning(
                "Naver request retry %s/%s after %s; waiting %.1fs",
                attempt,
                attempts,
                exc.__class__.__name__,
                wait,
            )
            time.sleep(wait)
            continue

        if not _is_retryable_status(response.status_code):
            response.raise_for_status()
            return response

        if attempt >= attempts:
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                raise RuntimeError(
                    f"Naver request failed after {attempts} attempts with HTTP {response.status_code}"
                ) from exc
            return response

        wait = _backoff_seconds(backoff, attempt)
        logger.warning(
            "Naver request retry %s/%s after HTTP %s; waiting %.1fs",
            attempt,
            attempts,
            response.status_code,
            wait,
        )
        time.sleep(wait)

    raise RuntimeError(f"Naver request failed after {attempts} attempts: {last_error.__class__.__name__}") from last_error


def fetch_news(
    config: NaverConfig,
    queries: Iterable[str],
    start: datetime,
    end: datetime,
    display: int = 100,
    max_pages: int = 5,
) -> list[dict]:
    if not config.client_id:
        raise RuntimeError("Missing required Naver configuration: NAVER_CLIENT_ID")
    if not config.client_secret:
        raise RuntimeError("Missing required Naver configuration: NAVER_CLIENT_SECRET")

    timeout = _env_float(
        "NAVER_HTTP_TIMEOUT_SECONDS",
        DEFAULT_NAVER_HTTP_TIMEOUT_SECONDS,
        minimum=0.1,
        maximum=MAX_NAVER_HTTP_TIMEOUT_SECONDS,
    )
    attempts = _env_int(
        "NAVER_RETRY_ATTEMPTS",
        DEFAULT_NAVER_RETRY_ATTEMPTS,
        maximum=MAX_NAVER_RETRY_ATTEMPTS,
    )
    backoff = _env_float(
        "NAVER_RETRY_BACKOFF_SECONDS",
        DEFAULT_NAVER_RETRY_BACKOFF_SECONDS,
        maximum=MAX_NAVER_RETRY_BACKOFF_SECONDS,
    )

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
            response = _request_with_retry(
                session,
                url=NAVER_NEWS_ENDPOINT,
                headers=headers,
                params=params,
                timeout=timeout,
                attempts=attempts,
                backoff=backoff,
            )
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
