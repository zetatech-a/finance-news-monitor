from __future__ import annotations

import csv

from scripts import generate_relevance_pseudo_labels as pseudo


def _write_csv(path, rows, fieldnames=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = [
            "title", "summary", "url", "query", "score", "prob", "keep", "decision", "decision_reason",
            "relevance_score", "relevance_prob", "matched_hard", "matched_soft", "matched_negative",
        ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_strong_keep_candidate_becomes_positive(tmp_path):
    input_path = tmp_path / "reports" / "_candidates" / "2026-05-11_candidates.csv"
    _write_csv(input_path, [{
        "title": "저축은행 PF 연체율 상승", "summary": "부실채권 관리 강화", "url": "https://e/1",
        "score": "8", "decision": "keep", "relevance_score": "8", "matched_hard": "저축은행;PF", "matched_negative": "",
    }])
    output = tmp_path / "labels.csv"

    assert pseudo.main(["--input", str(input_path), "--output", str(output)]) == 0

    row = _read_csv(output)[0]
    assert row["date"] == "2026-05-11"
    assert row["auto_label"] == "1"
    assert row["excluded_from_training"] == "false"


def test_strong_drop_candidate_becomes_negative(tmp_path):
    input_path = tmp_path / "2026-05-12_candidates.csv"
    _write_csv(input_path, [{
        "title": "금융과 무관한 쇼핑 행사", "summary": "패션 할인", "url": "https://e/drop",
        "score": "1", "decision": "drop", "decision_reason": "rule_drop_no_financial_anchor", "relevance_score": "1",
    }])
    output = tmp_path / "labels.csv"

    assert pseudo.main(["--input", str(input_path), "--output", str(output)]) == 0

    assert _read_csv(output)[0]["auto_label"] == "0"


def test_ambiguous_candidate_becomes_review_and_excluded(tmp_path):
    input_path = tmp_path / "2026-05-13_candidates.csv"
    _write_csv(input_path, [{
        "title": "금리 변동", "summary": "시장 관망", "url": "https://e/review",
        "score": "4", "decision": "drop", "relevance_score": "4",
    }])
    output = tmp_path / "labels.csv"

    assert pseudo.main(["--input", str(input_path), "--output", str(output)]) == 0

    row = _read_csv(output)[0]
    assert row["auto_label"] == "review"
    assert row["excluded_from_training"] == "true"


def test_corporate_macro_noise_becomes_negative(tmp_path):
    input_path = tmp_path / "2026-05-14_candidates.csv"
    _write_csv(input_path, [{
        "title": "항공사 환율 상승에 영업이익 감소", "summary": "유가와 달러 부담으로 실적 악화",
        "url": "https://e/macro", "score": "5", "decision": "drop", "relevance_score": "5",
    }])
    output = tmp_path / "labels.csv"

    assert pseudo.main(["--input", str(input_path), "--output", str(output)]) == 0

    row = _read_csv(output)[0]
    assert row["auto_label"] == "0"
    assert row["auto_label_reason"] == "drop_corporate_macro_noise"


def test_obvious_non_finance_short_article_becomes_negative(tmp_path):
    input_path = tmp_path / "2026-05-18_candidates.csv"
    _write_csv(input_path, [{
        "title": "축구", "summary": "경기", "url": "https://e/sports",
        "score": "0", "decision": "drop", "relevance_score": "0",
    }])
    output = tmp_path / "labels.csv"

    assert pseudo.main(["--input", str(input_path), "--output", str(output)]) == 0

    row = _read_csv(output)[0]
    assert row["auto_label"] == "0"
    assert row["auto_label_reason"] == "drop_obvious_non_finance"


def test_conflicting_keep_and_negative_signals_become_review(tmp_path):
    input_path = tmp_path / "2026-05-19_candidates.csv"
    _write_csv(input_path, [{
        "title": "은행 가계대출 연체와 스포츠 이벤트",
        "summary": "금융권 리스크 기사처럼 보이지만 스포츠 신호도 포함",
        "url": "https://e/conflict",
        "score": "8",
        "decision": "keep",
        "relevance_score": "8",
        "matched_hard": "은행;가계대출",
        "matched_negative": "스포츠",
    }])
    output = tmp_path / "labels.csv"

    assert pseudo.main(["--input", str(input_path), "--output", str(output)]) == 0

    row = _read_csv(output)[0]
    assert row["auto_label"] == "review"
    assert row["excluded_from_training"] == "true"
    assert row["auto_label_reason"].startswith("review_conflicting_signals")


def test_deduplication_by_url_works(tmp_path):
    input_path = tmp_path / "2026-05-15_candidates.csv"
    _write_csv(input_path, [
        {"title": "은행 가계대출 연체", "summary": "요약", "url": "https://e/dup", "score": "8", "decision": "keep", "relevance_score": "8", "matched_hard": "은행"},
        {"title": "다른 제목", "summary": "요약", "url": "https://e/dup", "score": "1", "decision": "drop", "relevance_score": "1"},
    ])
    output = tmp_path / "labels.csv"

    assert pseudo.main(["--input", str(input_path), "--output", str(output)]) == 0

    assert len(_read_csv(output)) == 1


def test_output_is_deterministic_with_same_seed(tmp_path):
    input_path = tmp_path / "2026-05-16_candidates.csv"
    rows = [
        {"title": f"은행 가계대출 연체 {i}", "summary": "요약", "url": f"https://e/{i}", "score": "8", "decision": "keep", "relevance_score": "8", "matched_hard": "은행"}
        for i in range(20)
    ]
    _write_csv(input_path, rows)
    one = tmp_path / "one.csv"
    two = tmp_path / "two.csv"

    args = ["--input", str(input_path), "--max-rows", "7", "--seed", "7"]
    assert pseudo.main([*args, "--output", str(one)]) == 0
    assert pseudo.main([*args, "--output", str(two)]) == 0

    assert one.read_text(encoding="utf-8") == two.read_text(encoding="utf-8")


def test_existing_output_is_not_overwritten_without_force(tmp_path):
    input_path = tmp_path / "2026-05-17_candidates.csv"
    _write_csv(input_path, [{"title": "은행 가계대출 연체", "summary": "요약", "url": "https://e/1"}])
    output = tmp_path / "labels.csv"
    output.write_text("existing", encoding="utf-8")

    assert pseudo.main(["--input", str(input_path), "--output", str(output)]) == 2
    assert output.read_text(encoding="utf-8") == "existing"
    assert pseudo.main(["--input", str(input_path), "--output", str(output), "--force"]) == 0
