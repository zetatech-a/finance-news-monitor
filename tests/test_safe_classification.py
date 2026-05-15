from datetime import datetime
from pathlib import Path

import yaml

from src.pipeline.normalize import Article
from src.pipeline.relevance_filter import _matched_terms
from src.pipeline.relevance_score import relevance_score
from src.pipeline.tagger import tag_articles
from scripts.generate_relevance_pseudo_labels import assign_pseudo_label


def _tag_one(title: str, description: str = ""):
    data = yaml.safe_load(Path("queries.yml").read_text(encoding="utf-8"))
    article = Article(
        title=title,
        description=description,
        link=f"https://example.com/{title}",
        originallink=None,
        naver_link=None,
        pub_date=datetime(2026, 5, 15, 9, 0),
        query="test",
    )
    return tag_articles([article], data["sectors"], data["topics"])[0]


def test_tagger_avoids_shinhyup_substring_false_positive():
    tagged = _tag_one("여신협회장 현안 산적")
    assert tagged.sectors[0] != "상호금융"


def test_tagger_classifies_true_credit_finance_and_shinhyup():
    assert _tag_one("여신금융협회, 카드수수료 논의").sectors[0] == "여전"
    assert _tag_one("신협 연체율 상승").sectors[0] == "상호금융"


def test_tagger_regulator_policy_and_supervision_are_safe():
    assert _tag_one("금융위기 이후 은행 건전성").sectors[0] != "감독·제재"
    assert _tag_one("금융위, 금융권 제도개선 방안 발표").sectors[0] == "입법·정책"
    assert _tag_one("금감원, 은행권 대출 검사 착수").sectors[0] == "감독·제재"


def test_tagger_insurance_false_positive_and_true_positive():
    assert _tag_one("운송·보험료 상승에 항공사 실적 악화").sectors[0] != "보험"
    assert _tag_one("보험사 킥스 비율 하락").sectors[0] == "보험"


def test_relevance_matched_terms_are_safe():
    assert "금융위" not in _matched_terms({"title": "금융위기 이후", "summary": "은행 건전성"})["matched_hard"].split(";")
    assert "신협" not in _matched_terms({"title": "여신협회", "summary": "현안"})["matched_hard"].split(";")
    assert "cp" not in _matched_terms({"title": "CPI 상승", "summary": "물가"})["matched_hard"].split(";")
    assert "신협" in _matched_terms({"title": "신협 연체율", "summary": "상승"})["matched_hard"].split(";")
    assert relevance_score({"title": "금융위원회 제도개선", "summary": "금융권"}) >= 4


def test_pseudo_label_strong_cases_and_false_positives():
    assert assign_pseudo_label({"title": "보험사 킥스 비율 하락", "summary": "", "decision": "keep", "relevance_score": "8"})["auto_label"] == "1"
    assert assign_pseudo_label({"title": "금융위 제도개선", "summary": "", "decision": "keep", "relevance_score": "8"})["auto_label"] == "1"
    assert assign_pseudo_label({"title": "운송·보험료 상승", "summary": "항공사 실적 악화", "decision": "drop", "relevance_score": "1"})["auto_label"] != "1"
    assert assign_pseudo_label({"title": "금융위기", "summary": "", "decision": "keep", "relevance_score": "6", "matched_hard": "금융위"})["auto_label"] != "1"
