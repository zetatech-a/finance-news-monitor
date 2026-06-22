from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml

from src.pipeline.normalize import Article
from src.pipeline.tagger import tag_articles


def _tag_one(title: str, description: str = ""):
    cfg = yaml.safe_load(Path("queries.yml").read_text(encoding="utf-8"))
    article = Article(
        title=title,
        description=description,
        link="https://example.com/topic-fallback",
        originallink=None,
        naver_link=None,
        pub_date=datetime(2026, 6, 22, 9, 0),
        query="topic-fallback-test",
    )
    return tag_articles([article], cfg["sectors"], cfg["topics"])[0]


@pytest.mark.parametrize(
    ("title", "expected_topics"),
    [
        ("美 PCE 발표 앞두고 국채금리 상승", {"해외·글로벌", "물가·경기지표"}),
        ("뉴욕증시, 연준 경계감에 혼조 마감", {"해외·글로벌", "증시·시장시황"}),
        ("원달러 환율, 달러 강세에 상승", {"환율·외환"}),
        ("저축은행 예금금리 4%대 재진입", {"상품·영업·예금금리"}),
        ("인터넷은행 마통 한도 축소", {"상품·영업·예금금리"}),
        ("중앙그룹 회생 후폭풍…금융권 익스포저 경고등", {"기업금융·익스포저"}),
        ("다음주 한국은행 및 금융위·금감원 일정", {"일정·브리핑"}),
        ("금융 브리핑 NH농협은행·신협중앙회·새마을금고", {"업계동정·사회공헌"}),
        ("은행권 사회공헌 캠페인 확대", {"업계동정·사회공헌"}),
        ("칼럼 금리 인하 시기와 금융시장", {"칼럼·오피니언"}),
    ],
)
def test_topic_fallback_coverage(title: str, expected_topics: set[str]):
    tagged = _tag_one(title)

    assert expected_topics.issubset(set(tagged.topics))


def test_non_finance_phishing_does_not_get_voice_phishing_topic():
    tagged = _tag_one("피싱 메일 보안 주의")

    assert "불법사금융·불법추심·보이스피싱" not in tagged.topics


def test_us_market_shorthand_gets_overseas_and_market_topics():
    tagged = _tag_one("미 증시 상승 마감")

    assert {"해외·글로벌", "증시·시장시황"}.issubset(set(tagged.topics))
