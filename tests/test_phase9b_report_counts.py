from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta

from src.pipeline.normalize import Article
from src.pipeline.report import render_html
from src.pipeline.tagger import TaggedArticle


def _mk(
    idx: int,
    *,
    sector: str,
    topics: list[str] | None = None,
    score: int | None = 8,
    cluster_size: int = 1,
    related: list[dict[str, str]] | None = None,
    title: str | None = None,
) -> TaggedArticle:
    article = Article(
        title=title or f"t{idx}",
        description=f"d{idx}",
        link=f"https://example.com/{idx}",
        originallink=None,
        naver_link=None,
        pub_date=datetime(2026, 5, 27, 9, 0) - timedelta(minutes=idx),
        query="phase9b",
        relevance_score=score,
        cluster_size=cluster_size,
        related_articles=related or [],
    )
    return TaggedArticle(article=article, sectors=[sector], topics=topics or [], matched_keywords=[])


def _nav_count(html: str, label: str) -> int:
    m = re.search(rf"<strong>{re.escape(label)}</strong><span class='count'>(\d+)</span>", html)
    assert m, label
    return int(m.group(1))


def _topic_count(html: str, label: str) -> int:
    m = re.search(rf"data-topic-pill[^>]*><strong>{re.escape(label)}</strong><span class='count'>(\d+)</span>", html)
    assert m, label
    return int(m.group(1))


def _cards_by_sector(html: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for section_id, body in re.findall(r"<section data-group id='sec-([^']+)'>(.*?)</section>", html, re.S):
        if section_id == "TOP":
            continue
        counts[section_id] += len(re.findall(r"data-card", body))
    return counts


def test_phase9b_counts_match_visible_cards_and_hidden_misc_excluded():
    items = [
        _mk(1, sector="은행", topics=["정책"]),
        _mk(2, sector="대부", topics=["해외·글로벌"]),
        _mk(3, sector="기타", topics=["해외·글로벌"], score=1),  # hidden low-confidence misc
        _mk(4, sector="대부", cluster_size=3, related=[{"title": "r1", "link": "https://r/1"}]),
        _mk(5, sector="은행"),
    ]
    html = render_html(datetime(2026, 5, 27), items, [])

    visible_cards = _cards_by_sector(html)
    visible_total = sum(visible_cards.values())

    assert _nav_count(html, "전체") == visible_total
    assert _nav_count(html, "대부") == visible_cards["대부"] == 2
    assert _nav_count(html, "은행") == visible_cards["은행"] == 2
    assert "<strong>기타</strong><span class='count'>" not in html
    assert "관련 기사 3건" in html


def test_phase9b_loan_business_tab_exists_for_visible_top_and_no_double_count():
    items = [
        _mk(1, sector="대부", title="새도약기금 채권 소각률…대부업권 참여 압박", score=10),
        _mk(2, sector="은행", score=9),
        _mk(3, sector="기타", score=1),
    ]
    html = render_html(datetime(2026, 5, 27), items, [])

    assert "data-sector='대부'" in html
    assert _nav_count(html, "대부") >= 1
    visible_total = sum(_cards_by_sector(html).values())
    assert _nav_count(html, "전체") == visible_total


def test_phase9b_topic_and_section_header_counts_use_visible_cards_only():
    items = [
        _mk(1, sector="대부", topics=["해외·글로벌"]),
        _mk(2, sector="은행", topics=[]),
        _mk(3, sector="기타", topics=["해외·글로벌"], score=1),
        _mk(4, sector="대부", topics=[]),
    ]
    html = render_html(datetime(2026, 5, 27), items, [])

    assert _topic_count(html, "해외·글로벌") == 1
    assert _topic_count(html, "주제 없음") == 2

    sector_cards = _cards_by_sector(html)
    for sector, expected in sector_cards.items():
        section_m = re.search(rf"<section data-group id='sec-{re.escape(sector)}'>.*?<h2>{re.escape(sector)}<span class='count'>(\d+)</span></h2>", html, re.S)
        assert section_m
        assert int(section_m.group(1)) == expected
        assert _nav_count(html, sector) == expected

    assert "id=\"searchInput\"" in html
    assert "id=\"sortSel\"" in html
    assert "id=\"topOnly\"" in html
    assert "data-sector-pill" in html
    assert "data-topic-pill" in html
    assert "data-card" in html
