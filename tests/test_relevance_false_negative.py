from __future__ import annotations

from src.pipeline import relevance_filter as rf
from src.pipeline.relevance_score import relevance_score


def _article(title: str) -> dict[str, str]:
    return {"title": title, "description": "", "link": f"https://example.com/{abs(hash(title))}"}


def test_strong_finance_anchor_noise_terms_are_kept_in_rule_only(tmp_path):
    titles = [
        "SNS 얼굴 박제 불법추심 피해 확산",
        "불법사금융 피해자 SNS 협박",
        "저축은행 금리 맛집 예금금리 4%대 재진입",
        "금감원, 유튜브 불법 대출광고 점검",
        "금융위, SNS 불법 대부광고 단속",
        "보이스피싱 조직 유튜브 광고 악용",
        "카드론 연체율 상승 경고등",
        "금감원 저축은행 검사 착수",
    ]
    articles = [_article(title) for title in titles]

    kept = rf.filter_relevance(
        articles,
        tmp_path / "missing.joblib",
        model_policy="rule_only",
        min_score=4,
    )

    assert kept == articles
    assert all(article["decision"] == "keep" for article in articles)
    assert all(article["relevance_score"] >= 4 for article in articles)


def test_non_finance_noise_and_weak_anchors_still_drop_in_rule_only(tmp_path):
    titles = [
        "프로야구 금리 이벤트",
        "맛집 추천 은행나무길",
        "유튜브 먹방 맛집 인기",
        "SNS 인플루언서 행사 개최",
        "연예인 사채 루머",
        "게임 플랫폼 대출 이벤트",
        "유튜브 대출 후기",
        "금리 맛집 카페 추천",
    ]
    articles = [_article(title) for title in titles]

    kept = rf.filter_relevance(
        articles,
        tmp_path / "missing.joblib",
        model_policy="rule_only",
        min_score=4,
    )

    assert kept == []
    assert all(article["decision"] == "drop" for article in articles)


def test_candidate_hybrid_negative_cap_keeps_strong_finance_context_without_model(tmp_path):
    article = _article("금감원, 유튜브 불법 대출광고 점검")

    kept = rf.filter_relevance([article], tmp_path / "missing.joblib", model_policy="candidate_hybrid")

    assert kept == [article]
    assert article["decision_reason"] in {
        "candidate_hybrid_keep_strong_finance_anchor_neg_cap",
        "candidate_hybrid_no_model_keep_domain_score_ge_threshold",
    }


def test_relevance_score_caps_noise_penalty_only_for_strong_finance_context():
    assert relevance_score(_article("SNS 얼굴 박제 불법추심 피해 확산")) >= 4
    assert relevance_score(_article("금감원, 유튜브 불법 대출광고 점검")) >= 4
    assert relevance_score(_article("유튜브 대출 후기")) < 4
    assert relevance_score(_article("금리 맛집 카페 추천")) < 4
