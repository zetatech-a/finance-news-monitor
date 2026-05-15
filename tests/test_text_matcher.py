from src.pipeline.text_matcher import contains_term, find_terms


def test_shinhyup_safe_matching():
    assert contains_term("신협 연체율", "신협")
    assert contains_term("신협중앙회 건전성", "신협")
    assert contains_term("신협 연체율과 여신협회 현안", "신협")
    assert not contains_term("여신협회", "신협")
    assert not contains_term("여신협회장", "신협")
    assert not contains_term("여신금융협회", "신협")
    assert not contains_term("여신전문금융협회", "신협")


def test_financial_services_commission_safe_matching():
    assert contains_term("금융위 제도개선", "금융위")
    assert contains_term("금융위원회 제도개선", "금융위")
    assert contains_term("금융위 제도개선과 금융위기 대응", "금융위")
    assert not contains_term("금융위기 이후", "금융위")
    assert not contains_term("금융위축 우려", "금융위")
    assert not contains_term("금융위험 점검", "금융위")


def test_cp_safe_matching_and_alias():
    assert contains_term("CP시장 경색", "CP")
    assert contains_term("기업어음 시장 경색", "CP")
    assert not contains_term("CPI 상승", "CP")


def test_insurance_strong_terms_are_specific():
    strong_terms = ["보험사", "보험업계", "손해보험", "생명보험", "손보사", "생보사", "킥스"]
    assert find_terms("보험사 킥스 비율", strong_terms)
    assert find_terms("보험업계 K-ICS 대응", strong_terms)
    assert find_terms("손해보험 생명보험 손보사 생보사", strong_terms)
    for text in ("건강보험", "고용보험", "산재보험", "운송·보험료 상승", "재보험"):
        assert not contains_term(text, "보험")
