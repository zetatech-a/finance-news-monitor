from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta

from src.pipeline.normalize import Article
from src.pipeline.report import _select_top_items, _top_rank_score, render_html
from src.pipeline.tagger import TaggedArticle


def _mk(idx: int, *, title: str, sector: str, topics: list[str] | None = None, score: float | None = 8.0, prob: float | None = None, cluster_id: str | None = None, cluster_size: int = 1, related: list[dict] | None = None):
    return TaggedArticle(
        article=Article(
            title=title,
            description=title,
            link=f"https://example.com/{idx}",
            originallink=None,
            naver_link=None,
            pub_date=datetime(2026, 5, 27, 12, 0) - timedelta(minutes=idx),
            query="phase9e",
            relevance_score=score,
            relevance_prob=prob,
            cluster_id=cluster_id,
            cluster_size=cluster_size,
            related_articles=related or [],
        ),
        sectors=[sector],
        topics=topics or [],
        matched_keywords=[],
    )


def _top_titles(items: list[TaggedArticle]) -> list[str]:
    return [x.article.title for x in _select_top_items(items, limit=10)]


def test_phase9e_top_uses_visible_only_and_cluster_dedup():
    items = [
        _mk(1, title="대부 불법사금융 단속", sector="대부", topics=["불법사금융"], score=8.5),
        _mk(2, title="은행 대출 관리", sector="은행", topics=["가계대출·부채"], score=8.2),
        _mk(3, title="숨김 기타 고점", sector="기타", topics=["해외·글로벌"], score=9.9, prob=0.2),
        _mk(4, title="대표 기사", sector="거시·시장", score=8.0, cluster_id="c1", cluster_size=3, related=[{"title": "rel"}]),
        _mk(5, title="중복 클러스터 기사", sector="거시·시장", score=8.1, cluster_id="c1", cluster_size=3),
    ]
    tops = _select_top_items(items, limit=10)
    titles = [x.article.title for x in tops]
    assert "숨김 기타 고점" not in titles
    assert sum(1 for x in tops if x.article.cluster_id == "c1") == 1


def test_phase9e_critical_loan_inclusion_and_tab_consistency():
    items = [_mk(i, title=f"뉴욕증시 상승 마감 {i}", sector="거시·시장", topics=["해외·글로벌"], score=9.3) for i in range(1, 6)]
    items += [_mk(30, title="대부 새도약기금 불법사금융 대응", sector="대부", topics=["불법사금융"], score=8.7)]
    titles = _top_titles(items)
    assert any("새도약기금" in t for t in titles)
    html = render_html(datetime(2026, 5, 27), items, [])
    assert "data-sector='대부'" in html


def test_phase9e_macro_digital_caps_and_penalties():
    items = []
    items += [_mk(i, title=f"FOMC 경계 뉴욕증시 상승 마감 {i}", sector="거시·시장", topics=["해외·글로벌"], score=9.6) for i in range(1, 7)]
    items += [_mk(100 + i, title=f"비트코인 신고가 경신 {i}", sector="디지털자산", topics=["디지털자산"], score=9.5) for i in range(1, 7)]
    items += [
        _mk(300, title="저축은행 연체율 상승", sector="저축은행", topics=["연체·부실"], score=8.9),
        _mk(301, title="여전채 스프레드 확대", sector="여전", topics=["자금시장·유동성"], score=8.8),
        _mk(302, title="보험 킥스 비율 점검", sector="보험", topics=["건전성·자본규제"], score=8.7),
        _mk(303, title="금감원 은행권 대출 검사 착수", sector="감독·제재", topics=["감독·제재"], score=8.8),
        _mk(304, title="금융위 금융권 제도개선 방안 발표", sector="입법·정책", topics=["정책·제도개선"], score=8.8),
        _mk(305, title="대부 고리대금업 논란 확산", sector="대부", topics=["평판·사회이슈"], score=8.6),
        _mk(306, title="FIU 가상자산거래소 제재 착수", sector="디지털자산", topics=["디지털자산"], score=8.8),
    ]
    tops = _select_top_items(items, limit=10)
    sectors = Counter(x.sectors[0] for x in tops)
    assert sectors["거시·시장"] <= 3
    assert sectors["디지털자산"] <= 2
    assert sectors["거시·시장"] + sectors["디지털자산"] <= 4

    score_price = _top_rank_score(_mk(1, title="비트코인 신고가 경신", sector="디지털자산", score=8.5))
    score_event = _top_rank_score(_mk(2, title="두나무 피자데이 행사 개최", sector="디지털자산", score=8.5))
    score_fiu = _top_rank_score(_mk(3, title="FIU 가상자산거래소 제재 착수", sector="디지털자산", score=8.5))
    score_stable = _top_rank_score(_mk(4, title="두나무 원화 스테이블코인 사업 검토", sector="디지털자산", score=8.5))
    assert score_fiu > score_price
    assert score_stable > score_event


def test_phase9e_policy_supervision_priority_and_determinism_and_counts():
    items = [
        _mk(1, title="금융위 금융권 제도개선 방안 발표", sector="입법·정책", topics=["정책·제도개선"], score=8.2),
        _mk(2, title="금감원 은행권 대출 검사 착수", sector="감독·제재", topics=["감독·제재"], score=8.2),
        _mk(3, title="금융권 소식 종합", sector="은행", topics=["해외·글로벌"], score=8.2),
        _mk(4, title="후보 고리대금업 논란 확산", sector="대부", topics=["평판·사회이슈"], score=8.2),
        _mk(5, title="대부업을 고리대금업으로 표현한 논란", sector="대부", topics=["평판·사회이슈"], score=8.2),
        _mk(6, title="hidden", sector="기타", score=9.9, prob=0.1),
    ]
    s1 = _top_titles(items)
    s2 = _top_titles(list(reversed(items)))
    assert s1 == s2
    assert s1.index("금융위 금융권 제도개선 방안 발표") < s1.index("금융권 소식 종합")
    assert s1.index("금감원 은행권 대출 검사 착수") < s1.index("금융권 소식 종합")
    assert "후보 고리대금업 논란 확산" in s1

    html = render_html(datetime(2026, 5, 27), items, [])
    total = int(re.search(r"<strong>전체</strong><span class='count'>(\d+)</span>", html).group(1))
    visible_main = sum(len(re.findall(r"data-card", body)) for sec, body in re.findall(r"<section data-group id='sec-([^']+)'>(.*?)</section>", html, re.S) if sec != "TOP")
    assert total == visible_main


def test_phase9e_relaxed_fill_reaches_limit_for_single_sector_inputs():
    bank_only = [_mk(i, title=f"은행 기사 {i}", sector="은행", topics=["정책"], score=8.0) for i in range(1, 21)]
    macro_only = [_mk(100 + i, title=f"뉴욕증시 상승 마감 {i}", sector="거시·시장", topics=["해외·글로벌"], score=8.0) for i in range(1, 21)]
    digital_only = [_mk(200 + i, title=f"비트코인 신고가 경신 {i}", sector="디지털자산", topics=["디지털자산"], score=8.0) for i in range(1, 21)]

    assert len(_select_top_items(bank_only, limit=10)) == 10
    assert len(_select_top_items(macro_only, limit=10)) == 10
    assert len(_select_top_items(digital_only, limit=10)) == 10


def test_phase9e_relaxed_fill_still_respects_cluster_and_visibility_guards():
    items = [_mk(i, title=f"은행 기사 {i}", sector="은행", topics=["정책"], score=8.0) for i in range(1, 9)]
    items += [
        _mk(100, title="대표 클러스터", sector="은행", topics=["정책"], score=8.1, cluster_id="dup"),
        _mk(101, title="중복 클러스터", sector="은행", topics=["정책"], score=8.0, cluster_id="dup"),
        _mk(102, title="숨김 기타 고점", sector="기타", topics=["해외·글로벌"], score=9.9, prob=0.2),
        _mk(103, title="은행 기사 9", sector="은행", topics=["정책"], score=8.0),
        _mk(104, title="은행 기사 10", sector="은행", topics=["정책"], score=8.0),
        _mk(105, title="은행 기사 11", sector="은행", topics=["정책"], score=8.0),
    ]
    tops = _select_top_items(items, limit=10)
    titles = [x.article.title for x in tops]
    assert len(tops) == 10
    assert "숨김 기타 고점" not in titles
    assert sum(1 for x in tops if x.article.cluster_id == "dup") == 1


def test_phase9e_diversity_caps_still_apply_when_enough_alternatives_exist():
    items = [_mk(i, title=f"거시 기사 {i}", sector="거시·시장", topics=["해외·글로벌"], score=9.0) for i in range(1, 21)]
    items += [_mk(100 + i, title=f"디지털 기사 {i}", sector="디지털자산", topics=["디지털자산"], score=8.9) for i in range(1, 21)]
    items += [_mk(200 + i, title=f"은행 기사 {i}", sector="은행", topics=["정책"], score=8.8) for i in range(1, 21)]

    tops = _select_top_items(items, limit=10)
    sectors = Counter(x.sectors[0] for x in tops)
    assert sectors["거시·시장"] <= 2
    assert sectors["디지털자산"] <= 2
