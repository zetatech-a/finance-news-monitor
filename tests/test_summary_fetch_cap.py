from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src import run_daily
from src.config import KST
from src.pipeline.normalize import Article
from src.pipeline.tagger import TaggedArticle


def _item(i: int) -> TaggedArticle:
    article = Article(
        title=f"금융 기사 {i}",
        description="네이버 스니펫 원본 설명",
        link=f"https://example.com/news/{i}",
        originallink=None,
        naver_link=None,
        pub_date=datetime(2026, 7, 15, 9, 0, tzinfo=KST) - timedelta(minutes=i),
        query="테스트",
    )
    return TaggedArticle(article=article, sectors=["은행"], topics=[], matched_keywords=[])


def _raise_fetch(url, timeout=12):
    raise RuntimeError("network down")


def test_fetch_attempts_capped_even_when_all_fetches_fail(monkeypatch):
    items = [_item(i) for i in range(10)]
    calls: list[str] = []

    def failing_fetch(url, timeout=12):
        calls.append(url)
        raise RuntimeError("network down")

    monkeypatch.setattr(run_daily, "fetch_html", failing_fetch)

    summarized, cache_hits, fetch_attempts = run_daily.apply_extractive_summaries(
        items, {}, max_summaries=5, max_fetch_attempts=3
    )

    assert len(calls) == 3
    assert fetch_attempts == 3
    assert summarized == 0
    assert cache_hits == 0


def test_cache_hits_still_apply_after_fetch_cap_exhausted(monkeypatch):
    items = [_item(i) for i in range(6)]
    cached_summary = "캐시된 요약문입니다. 검증에 사용할 충분히 긴 문장입니다."
    cache = {items[5].article.link: cached_summary}
    monkeypatch.setattr(run_daily, "fetch_html", _raise_fetch)

    summarized, cache_hits, fetch_attempts = run_daily.apply_extractive_summaries(
        items, cache, max_summaries=5, max_fetch_attempts=2
    )

    assert fetch_attempts == 2
    assert cache_hits == 1
    assert summarized == 1
    assert items[5].article.description == cached_summary


def test_successful_summaries_count_toward_summary_budget(monkeypatch):
    items = [_item(i) for i in range(5)]
    new_summary = "새로 생성된 요약문입니다. 스물네 자 이상이 되도록 충분히 길게 씁니다."

    monkeypatch.setattr(run_daily, "fetch_html", lambda url, timeout=12: "<html></html>")
    monkeypatch.setattr(run_daily, "extract_main_text", lambda url, html: "본문")
    monkeypatch.setattr(
        run_daily,
        "summarize_with_fallback",
        lambda full, *, title, description, max_chars: new_summary,
    )

    cache: dict[str, str] = {}
    summarized, cache_hits, fetch_attempts = run_daily.apply_extractive_summaries(
        items, cache, max_summaries=2, max_fetch_attempts=10
    )

    assert summarized == 2
    assert fetch_attempts == 2
    assert cache_hits == 0
    assert items[0].article.description == new_summary
    assert items[1].article.description == new_summary
    # 예산 초과분은 손대지 않음
    assert items[2].article.description == "네이버 스니펫 원본 설명"
    # 성공한 요약은 캐시에 저장됨
    assert cache[items[0].article.link] == new_summary


def test_short_summary_does_not_consume_summary_budget(monkeypatch):
    items = [_item(i) for i in range(4)]

    monkeypatch.setattr(run_daily, "fetch_html", lambda url, timeout=12: "<html></html>")
    monkeypatch.setattr(run_daily, "extract_main_text", lambda url, html: "본문")
    monkeypatch.setattr(
        run_daily,
        "summarize_with_fallback",
        lambda full, *, title, description, max_chars: "짧음",
    )

    summarized, cache_hits, fetch_attempts = run_daily.apply_extractive_summaries(
        items, {}, max_summaries=2, max_fetch_attempts=3
    )

    assert summarized == 0
    assert fetch_attempts == 3
    assert all(item.article.description == "네이버 스니펫 원본 설명" for item in items)
