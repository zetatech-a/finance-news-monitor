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
        pub_date=datetime(2026, 5, 27, 9, 0),
        query="phase9a-test",
    )
    return tag_articles([article], data["sectors"], data["topics"])[0]


def _primary(title: str, description: str = "") -> str:
    return _tag_one(title, description).sectors[0]


def test_phase9a_explicit_loan_business_primary_sector_protection():
    must_be_loan_business = (
        "새도약기금 채권 소각률 21.3% 그쳐…대부업권 참여 압박 커진다",
        "캠코 만난 대부업계, 실질적 인센티브 절실",
        "정부, 상품권 사채 엄정 대응…피해자 지원 추진",
        "금융위, 대부업 제도개선 방안 발표",
        "불법사금융 특별단속",
        "금감원, 대부업체 불법추심 검사 착수",
        "은행권 차입 허용 요구한 대부업계",
        "대부업체 장기연체채권 매각 추진",
        "김용남 차명 대부업 의혹",
        "후보 고리대금업 논란 확산",
        "사채업자 연루 의혹 제기",
        "대부업을 고리대금업으로 표현한 논란",
    )
    for title in must_be_loan_business:
        assert _primary(title) == "대부", title


def test_phase9a_non_financial_loan_lease_exclusions():
    must_not_be_loan_business = (
        "공유재산 대부계약 체결",
        "국유재산 대부료 인상",
        "토지 대부 사업자 모집",
        "농지 대부 신청 접수",
        "공공시설 대부계약",
        "지자체 공유재산 대부",
    )
    for title in must_not_be_loan_business:
        assert _primary(title) != "대부", title


def test_phase9a_conflict_resolution_prefers_loan_business():
    assert _primary("금융위, 대부업권 제도개선 방안 발표") == "대부"
    assert _primary("금감원, 대부업권 불법추심 검사 착수") == "대부"
    tagged_bank_conflict = _tag_one("은행권 차입 허용 요구한 대부업계")
    assert tagged_bank_conflict.sectors[0] == "대부"
    assert tagged_bank_conflict.sectors[0] != "은행"
    tagged_policy_conflict = _tag_one("정부 불법사금융 피해자 지원 대책 발표")
    assert tagged_policy_conflict.sectors[0] == "대부"
    assert tagged_policy_conflict.sectors[0] != "입법·정책"
    assert _primary("상품권 사채 피해자 지원 강화") == "대부"


def test_phase9a_regression_guards_for_other_sectors():
    assert _primary("저축은행 연체율 상승") == "저축은행"
    tagged_bank = _tag_one("농협은행 가계대출 증가")
    assert tagged_bank.sectors[0] == "은행"
    assert tagged_bank.sectors[0] != "상호금융"
    assert _primary("여신금융협회 카드수수료 논의") == "여전"
    assert _primary("보험사 킥스 비율 하락") == "보험"
    assert _primary("금융위 금융권 제도개선 방안 발표") == "입법·정책"
    assert _primary("금감원 은행권 대출 검사 착수") == "감독·제재"
