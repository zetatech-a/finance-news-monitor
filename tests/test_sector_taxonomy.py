from datetime import datetime
from pathlib import Path

import yaml

from src.pipeline.normalize import Article
from src.pipeline.tagger import tag_articles


def _tag_one(title: str, description: str = ""):
    data = yaml.safe_load(Path("queries.yml").read_text(encoding="utf-8"))
    article = Article(
        title=title,
        description=description,
        link=f"https://example.com/{title}",
        originallink=None,
        naver_link=None,
        pub_date=datetime(2026, 5, 15, 9, 0),
        query="taxonomy-test",
    )
    return tag_articles([article], data["sectors"], data["topics"])[0]


def _primary(title: str, description: str = "") -> str:
    return _tag_one(title, description).sectors[0]


def test_mutual_finance_and_credit_finance_conflicts():
    for title in (
        "신협 연체율 상승",
        "신협중앙회 건전성 점검",
        "새마을금고 PF 부실 우려",
        "농협 상호금융 연체율 상승",
        "수협 상호금융 대출 건전성 악화",
        "산림조합 금융 건전성 점검",
    ):
        assert _primary(title) == "상호금융"

    tagged = _tag_one("농협은행 가계대출 증가")
    assert tagged.sectors[0] == "은행"
    assert tagged.sectors[0] != "상호금융"

    tagged = _tag_one("수협은행 대출금리 인하")
    assert tagged.sectors[0] == "은행"
    assert tagged.sectors[0] != "상호금융"

    tagged = _tag_one("여신협회장 현안 산적")
    assert tagged.sectors[0] == "여전"
    assert tagged.sectors[0] != "상호금융"

    assert _primary("여신금융협회 카드수수료 논의") == "여전"


def test_credit_finance_products_and_safe_lease_matching():
    for title in (
        "카드론 연체율 상승",
        "캐피탈사 부동산 PF 충당금 확대",
        "할부금융 취급액 감소",
        "리스금융 연체 증가",
    ):
        assert _primary(title) == "여전"

    assert _primary("리스크 관리 강화") != "여전"


def test_insurance_requires_sector_evidence_and_avoids_social_insurance():
    for title in (
        "보험사 킥스 비율 하락",
        "손해보험사 자동차보험 손해율 상승",
        "생명보험사 지급여력비율 하락",
        "코리안리 재보험 시장 확대",
        "재보험사 실적 개선",
    ):
        assert _primary(title) == "보험"

    for title in (
        "운송·보험료 상승에 항공사 실적 악화",
        "건강보험 재정 악화",
        "고용보험료 인상",
        "산재보험 적용 확대",
    ):
        assert _primary(title) != "보험"


def test_supervision_and_policy_require_action_context():
    assert _primary("금융위 금융권 제도개선 방안 발표") == "입법·정책"
    assert _primary("금융위 대부업 제도개선 방안 발표") == "대부"
    assert _primary("금감원 은행권 대출 검사 착수") == "감독·제재"
    assert _primary("금융위 불공정거래 과징금 의결") == "감독·제재"
    assert _primary("금융위 보험업 감독규정 개정안 의결") == "입법·정책"
    assert _primary("금감원장 금융권 간담회 참석") != "감독·제재"
    assert _primary("금융위 관계자 발언") != "감독·제재"
    assert _primary("검사 착수") != "감독·제재"


def test_bank_savings_bank_loan_business_conflicts():
    assert _primary("저축은행 연체율 상승") == "저축은행"
    assert _primary("상호저축은행 PF 부실 우려") == "저축은행"

    tagged = _tag_one("투자은행 IPO 주관 경쟁")
    assert tagged.sectors[0] == "IB·자본시장"
    assert tagged.sectors[0] != "은행"

    assert _primary("공유재산 대부계약 체결") != "대부"
    assert _primary("대부업 제도개선 방안 발표") in {"대부", "입법·정책"}


def test_market_securities_ib_and_digital_asset_conflicts():
    assert _primary("증권사 신용융자 잔고 증가") == "증권(브로커리지/리테일)"
    assert _primary("공모주 IPO 주관 경쟁에 증권사 수수료 증가") == "IB·자본시장"
    assert _primary("AI 기업 IPO 상장 흥행") != "IB·자본시장"
    assert _primary("한국거래소 코스닥 상장 심사") != "디지털자산"
    assert _primary("업비트 가상자산 거래량 증가") == "디지털자산"
    assert _primary("원달러 환율 외환시장 변동성 확대") == "거시·시장"
    assert _primary("항공사 고환율·유가 부담에 영업이익 감소") != "거시·시장"


def test_targeted_fetch_queries_exist_and_broad_standalone_queries_are_absent():
    data = yaml.safe_load(Path("queries.yml").read_text(encoding="utf-8"))
    fetch_queries = set(data["fetch_queries"])

    assert {
        "상호금융 연체율",
        "상호금융 건전성",
        "농협 상호금융",
        "수협 상호금융",
        "산림조합 금융",
        "새마을금고 PF",
        "새마을금고 건전성",
        "신협 연체율",
        "신협 건전성",
    } <= fetch_queries
    assert {
        "여신금융협회",
        "여신협회 카드수수료",
        "여신전문금융업",
        "카드론 연체율",
        "캐피탈사 PF",
        "가맹점 수수료 카드사",
    } <= fetch_queries

    assert not ({"환율", "회사채", "저축은행", "금융위", "금감원"} & fetch_queries)
    assert not ({"IPO 상장", "연체율", "공매도", "불공정거래", "연준 금리"} & fetch_queries)
