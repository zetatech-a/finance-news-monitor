from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml

from src.pipeline.normalize import Article
from src.pipeline.report import render_html
from src.pipeline.tagger import tag_articles


def _tag_one(title: str, description: str = ""):
    data = yaml.safe_load(Path("queries.yml").read_text(encoding="utf-8"))
    article = Article(
        title=title,
        description=description,
        link=f"https://example.com/{title}",
        originallink=None,
        naver_link=None,
        pub_date=datetime(2026, 5, 27, 9, 0),
        query="phase9c-test",
    )
    return tag_articles([article], data["sectors"], data["topics"])[0]


def test_phase9c_loan_business_topic_coverage():
    tagged = _tag_one("새도약기금 채권 소각률 21.3% 그쳐…대부업권 참여 압박 커진다")
    assert tagged.sectors[0] == "대부"
    assert tagged.topics
    assert any(t in tagged.topics for t in ("서민금융·대환·채무조정", "연체·부실", "정책·제도개선"))

    tagged = _tag_one("캠코 만난 대부업계, 실질적 인센티브 절실")
    assert tagged.sectors[0] == "대부"
    assert tagged.topics

    tagged = _tag_one("정부, 상품권 사채 엄정 대응…피해자 지원 추진")
    assert tagged.sectors[0] == "대부"
    assert "불법사금융·불법추심·보이스피싱" in tagged.topics

    tagged = _tag_one("금융위, 대부업 제도개선 방안 발표")
    assert tagged.sectors[0] == "대부"
    assert "정책·제도개선" in tagged.topics

    tagged = _tag_one("금감원, 대부업체 불법추심 검사 착수")
    assert tagged.sectors[0] == "대부"
    assert "불법사금융·불법추심·보이스피싱" in tagged.topics
    assert "감독·제재" in tagged.topics

    for title in ("김용남 차명 대부업 의혹", "후보 고리대금업 논란 확산"):
        tagged = _tag_one(title)
        assert tagged.sectors[0] == "대부"
        assert tagged.topics


def test_phase9c_common_finance_topics():
    assert "연체·부실" in _tag_one("저축은행 연체율 상승").topics
    assert any(t in _tag_one("새마을금고 PF 부실 우려").topics for t in ("부동산·PF", "연체·부실"))
    tagged = _tag_one("카드채 만기 몰리는데 여전채 금리 4% 돌파")
    assert "자금시장·유동성" in tagged.topics
    assert "금리·수수료·최고금리" in tagged.topics
    tagged = _tag_one("가계대출 증가에 주담대 금리 상승")
    assert "가계대출·부채" in tagged.topics
    assert "금리·수수료·최고금리" in tagged.topics
    assert "금리·수수료·최고금리" in _tag_one("여신금융협회 카드수수료 논의").topics
    assert "정책·제도개선" in _tag_one("금융위 금융권 제도개선 방안 발표").topics
    assert "감독·제재" in _tag_one("금감원 은행권 대출 검사 착수").topics
    assert "건전성·자본규제" in _tag_one("보험사 킥스 비율 하락").topics
    assert "디지털자산" in _tag_one("업비트 가상자산 거래량 증가").topics
    assert "해외·글로벌" in _tag_one("미국 CPI 상승에 뉴욕증시 혼조").topics


def test_phase9c_over_tagging_guards():
    assert len(_tag_one("금융 소식 종합").topics) <= 1
    assert len(_tag_one("정책 발표").topics) <= 1
    assert "연체·부실" not in _tag_one("일반 기업 부실 우려").topics
    assert "평판·사회이슈" not in _tag_one("논란 확산").topics
    assert "자금시장·유동성" not in _tag_one("채권 발행").topics


def test_phase9c_report_visible_only_topic_count_compatibility():
    visible_with_topic = _tag_one("금감원, 대부업체 불법추심 검사 착수")
    visible_without_topic = _tag_one("금융 소식 종합")
    hidden_misc_with_topic = _tag_one("상품권 사채 특별단속")

    visible_with_topic.article.relevance_score = 10
    visible_without_topic.article.relevance_score = 10
    hidden_misc_with_topic.sectors = ["기타"]
    hidden_misc_with_topic.article.relevance_score = 1

    html = render_html(datetime(2026, 5, 27), [visible_with_topic, visible_without_topic, hidden_misc_with_topic], [])
    assert "<strong>불법사금융·불법추심·보이스피싱</strong><span class='count'>1</span>" in html
    assert "<strong>주제 없음</strong><span class='count'>1</span>" in html
