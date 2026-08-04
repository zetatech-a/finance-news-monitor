"""GeminiBatchSummarizer — 배치 구성/부분성공/분할/재시도/breaker. 실제 API 호출 없음."""
from __future__ import annotations

import json
import logging
import os

import pytest

from src.pipeline.gemini_summary import (
    BatchItem,
    GeminiBatchSummarizer,
    GeminiProgrammingError,
    build_batch_item,
    chunk_items,
    estimate_item_chars,
    build_batch_prompt,
    iter_batch_items,
    load_gemini_config,
    next_split_size,
    plan_batches,
    prompt_overhead_chars,
    validate_batch_response,
)

API_KEY = "test-key-not-a-real-credential"
BODY = "금융위원회는 대부업 감독 규정 개정안을 의결했다고 밝혔다. " * 20


def _lines(n: int) -> list[str]:
    return [
        f"금융위원회가 {n}번 안건을 의결했다고 발표했다.",
        f"안건은 2026년 9월 {n % 28 + 1}일 시행되며 대상은 {n}곳이다.",
        f"금융위는 시행 후 {n % 12 + 1}개월간 이행 실태를 점검할 계획이다.",
    ]


def _entry(article_id, lines=None, *, usable=True, reason=None):
    """정상 응답 항목 하나. schema v3의 usable/reason을 항상 채운다."""
    if reason is None:
        reason = "ok" if usable else "title_body_mismatch"
    return {
        "id": article_id,
        "usable": usable,
        "reason": reason,
        "lines": list(lines or []),
    }


def _unusable(article_id, reason="title_body_mismatch"):
    return _entry(article_id, [], usable=False, reason=reason)


def _ok_response(items, *, skip: set[str] | None = None, extra=None):
    skip = skip or set()
    summaries = [
        _entry(item.id, _lines(i))
        for i, item in enumerate(items)
        if item.id not in skip
    ]
    if extra:
        summaries.extend(extra)
    return json.dumps({"summaries": summaries}, ensure_ascii=False)


class FakeAPIError(Exception):
    """google.genai.errors.APIError처럼 .code(HTTP status)를 갖는 예외."""

    def __init__(self, code: int, message: str = "fake", response=None, details=None):
        super().__init__(message)
        self.code = code
        self.response = response
        self.details = details


class FakeTimeout(Exception):
    pass


FakeTimeout.__name__ = "ReadTimeout"


@pytest.fixture(autouse=True)
def _clean_gemini_env(monkeypatch):
    for name in list(dict(os.environ)):
        if name.startswith("GEMINI_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", API_KEY)
    monkeypatch.setenv("GEMINI_MIN_INTERVAL_SECONDS", "0")


def _items(count: int, *, body_chars: int = 800, article_max_chars: int = 3000):
    body = "가" * body_chars
    return iter_batch_items(
        ((f"기사 제목 {i}", body) for i in range(count)), article_max_chars=article_max_chars
    )


class Recorder:
    """generate_fn mock — 호출 횟수와 배치 크기를 그대로 기록한다."""

    def __init__(self, responder):
        self.responder = responder
        self.calls: list[dict] = []

    def __call__(self, *, system_instruction, prompt, schema):
        self.calls.append(
            {
                "prompt": prompt,
                "schema": schema,
                "size": schema["properties"]["summaries"]["maxItems"],
                "ids": sorted(set(_ids_in_prompt(prompt))),
            }
        )
        return self.responder(len(self.calls), prompt, schema)

    @property
    def count(self) -> int:
        return len(self.calls)

    @property
    def sizes(self) -> list[int]:
        return [c["size"] for c in self.calls]


def _ids_in_prompt(prompt: str) -> list[str]:
    import re

    return re.findall(r'<article id="(article-\d+)">', prompt)


def _make(responder, **env):
    for key, value in env.items():
        os.environ[key] = str(value)
    recorder = Recorder(responder)
    summarizer = GeminiBatchSummarizer(
        load_gemini_config(),
        generate_fn=recorder,
        sleep_fn=lambda _s: None,
        monotonic_fn=lambda: 0.0,
    )
    return summarizer, recorder


def _all_ok(items):
    """요청된 기사를 프롬프트에서 읽어 전부 정상 응답하는 responder."""

    def responder(call_no, prompt, schema):
        ids = _ids_in_prompt(prompt)
        return json.dumps(
            {"summaries": [_entry(i, _lines(n)) for n, i in enumerate(ids)]},
            ensure_ascii=False,
        )

    return responder


# --- 배치 구성: 호출 횟수 --------------------------------------------------


@pytest.mark.parametrize(
    "count,expected_calls",
    [(50, 1), (100, 2), (250, 5), (300, 6)],
)
def test_normal_batching_call_counts(count, expected_calls):
    items = _items(count)
    summarizer, recorder = _make(_all_ok(items))
    results = summarizer.summarize_many(items)

    assert recorder.count == expected_calls
    assert len(results) == count
    assert all(len(v) == 3 for v in results.values())


def test_no_per_article_call_in_the_normal_path():
    items = _items(50)
    summarizer, recorder = _make(_all_ok(items))
    summarizer.summarize_many(items)
    # 정상 경로에서는 배치 크기가 1인 요청이 나오면 안 된다.
    assert recorder.sizes == [50]
    assert 1 not in recorder.sizes


def test_char_budget_closes_the_batch_before_the_article_count():
    # 기사당 ~3,116자(본문 3,000 + ID/제목/구분자) × 50건 ≈ 155,800자 > 150,000자 예산
    # → 기사 수(50)가 아니라 문자 예산이 먼저 배치를 닫는다.
    items = _items(50, body_chars=3000)
    summarizer, recorder = _make(_all_ok(items))
    summarizer.summarize_many(items)

    assert recorder.count == 2
    assert recorder.sizes[0] < 50
    assert sum(recorder.sizes) == 50


def test_plan_batches_respects_both_limits():
    items = _items(10, body_chars=1000)
    by_count = plan_batches(items, max_articles=3, max_input_chars=10**9)
    assert [len(b) for b in by_count] == [3, 3, 3, 1]

    per_item = estimate_item_chars(items[0])
    budget = prompt_overhead_chars() + per_item * 2
    by_chars = plan_batches(items, max_articles=100, max_input_chars=budget)
    assert [len(b) for b in by_chars] == [2, 2, 2, 2, 2]


def test_oversized_single_article_is_still_included_alone():
    items = _items(2, body_chars=3000)
    batches = plan_batches(items, max_articles=50, max_input_chars=100)
    assert [len(b) for b in batches] == [1, 1]


def test_article_max_chars_truncates_each_article():
    item = build_batch_item("article-0001", "제목", "가" * 9999, article_max_chars=3000)
    assert len(item.body) <= 3000


def test_batch_max_articles_clamped_to_hard_cap(monkeypatch, caplog):
    monkeypatch.setenv("GEMINI_BATCH_MAX_ARTICLES", "400")
    monkeypatch.setenv("GEMINI_BATCH_HARD_MAX_ARTICLES", "100")
    with caplog.at_level(logging.WARNING):
        config = load_gemini_config()
    assert config.batch_max_articles == 100
    assert "hard cap" in caplog.text


# --- 부분 성공 --------------------------------------------------------------


def test_partial_success_applies_47_and_reasks_only_the_missing_3():
    items = _items(50)
    missing = {items[7].id, items[20].id, items[41].id}

    def responder(call_no, prompt, schema):
        ids = _ids_in_prompt(prompt)
        if call_no == 1:
            keep = [i for i in ids if i not in missing]
            return json.dumps(
                {"summaries": [_entry(i, _lines(n)) for n, i in enumerate(keep)]},
                ensure_ascii=False,
            )
        return json.dumps(
            {"summaries": [_entry(i, _lines(n)) for n, i in enumerate(ids)]},
            ensure_ascii=False,
        )

    summarizer, recorder = _make(responder)
    applied: list[str] = []
    results = summarizer.summarize_many(
        items, on_result=lambda item, lines: applied.append(item.id)
    )

    # 1차에서 47건 즉시 확정, 2차 요청에는 실패한 3건만 들어간다.
    assert len(applied) == 50
    assert applied[:47] == [i.id for i in items if i.id not in missing]
    assert recorder.count == 2
    assert recorder.calls[1]["ids"] == sorted(missing)
    assert len(results) == 50


def test_successful_items_are_never_resent():
    items = _items(50)
    missing = {items[3].id}

    def responder(call_no, prompt, schema):
        ids = _ids_in_prompt(prompt)
        if call_no == 1:
            ids = [i for i in ids if i not in missing]
        return json.dumps(
            {"summaries": [_entry(i, _lines(n)) for n, i in enumerate(ids)]},
            ensure_ascii=False,
        )

    summarizer, recorder = _make(responder)
    summarizer.summarize_many(items)

    sent_twice = set(recorder.calls[0]["ids"]) & set(recorder.calls[1]["ids"])
    assert sent_twice == missing


def test_out_of_order_response_maps_by_id():
    items = _items(5)
    ordered_ids = [i.id for i in items]

    def responder(call_no, prompt, schema):
        ids = list(reversed(_ids_in_prompt(prompt)))
        return json.dumps(
            {"summaries": [_entry(i, _lines(int(i[-4:]))) for i in ids]},
            ensure_ascii=False,
        )

    summarizer, _ = _make(responder)
    results = summarizer.summarize_many(items)

    assert set(results) == set(ordered_ids)
    for item in items:
        assert results[item.id] == _lines(int(item.id[-4:]))


# --- 항목 검증 --------------------------------------------------------------


def test_unknown_id_is_rejected():
    items = _items(3)
    outcome = validate_batch_response(
        json.dumps(
            {
                "summaries": [
                    _entry(items[0].id, _lines(0)),
                    _entry("article-9999", _lines(1)),
                ]
            },
            ensure_ascii=False,
        ),
        items,
    )
    assert set(outcome.accepted) == {items[0].id}
    assert outcome.rejected_reasons.get("unknown_id") == 1
    assert sorted(outcome.failed_ids) == sorted([items[1].id, items[2].id])


def test_duplicate_id_keeps_the_first_and_rejects_the_rest():
    items = _items(2)
    outcome = validate_batch_response(
        json.dumps(
            {
                "summaries": [
                    _entry(items[0].id, _lines(0)),
                    _entry(items[0].id, _lines(5)),
                    _entry(items[1].id, _lines(1)),
                ]
            },
            ensure_ascii=False,
        ),
        items,
    )
    assert outcome.accepted[items[0].id] == _lines(0)
    assert outcome.rejected_reasons.get("duplicate_id") == 1
    assert outcome.failed_ids == []


@pytest.mark.parametrize(
    "bad_lines",
    [
        _lines(1)[:2],
        _lines(1) + ["네 번째 문장이다."],
        ["", _lines(1)[1], _lines(1)[2]],
        ["- 불릿으로 시작하는 문장이다.", _lines(1)[1], _lines(1)[2]],
        ["1. 번호로 시작하는 문장이다.", _lines(1)[1], _lines(1)[2]],
        ["가" * 200, _lines(1)[1], _lines(1)[2]],
    ],
)
def test_line_contract_violations_fail_only_that_item(bad_lines):
    items = _items(2)
    outcome = validate_batch_response(
        json.dumps(
            {
                "summaries": [
                    _entry(items[0].id, bad_lines),
                    _entry(items[1].id, _lines(1)),
                ]
            },
            ensure_ascii=False,
        ),
        items,
    )
    assert set(outcome.accepted) == {items[1].id}
    assert outcome.failed_ids == [items[0].id]


def test_unparsable_response_fails_the_whole_batch():
    items = _items(4)
    outcome = validate_batch_response("{completely broken", items)
    assert outcome.parse_failed
    assert outcome.accepted == {}
    assert outcome.failed_ids == [i.id for i in items]


def test_entry_with_extra_fields_is_rejected():
    items = _items(1)
    outcome = validate_batch_response(
        json.dumps(
            {"summaries": [{**_entry(items[0].id, _lines(0)), "score": 0.9}]},
            ensure_ascii=False,
        ),
        items,
    )
    assert outcome.accepted == {}
    assert outcome.rejected_reasons.get("unexpected_fields") == 1


# --- 분할 ------------------------------------------------------------------


def test_split_ladder_is_50_25_10_1():
    assert next_split_size(50) == 25
    assert next_split_size(25) == 10
    assert next_split_size(10) == 1
    assert next_split_size(1) is None


def test_whole_json_parse_failure_splits_the_batch():
    items = _items(50)

    def responder(call_no, prompt, schema):
        if call_no == 1:
            return "not json at all"
        ids = _ids_in_prompt(prompt)
        return json.dumps(
            {"summaries": [_entry(i, _lines(n)) for n, i in enumerate(ids)]},
            ensure_ascii=False,
        )

    summarizer, recorder = _make(responder)
    results = summarizer.summarize_many(items)

    assert recorder.sizes == [50, 25, 25]  # 50 → 25 단위로 분할 후 성공
    assert len(results) == 50


def test_split_continues_down_the_ladder():
    items = _items(50)

    def responder(call_no, prompt, schema):
        if schema["properties"]["summaries"]["maxItems"] > 10:
            return "broken"
        ids = _ids_in_prompt(prompt)
        return json.dumps(
            {"summaries": [_entry(i, _lines(n)) for n, i in enumerate(ids)]},
            ensure_ascii=False,
        )

    summarizer, recorder = _make(responder, GEMINI_MAX_REQUESTS_PER_RUN=30)
    results = summarizer.summarize_many(items)

    assert recorder.sizes[0] == 50
    assert 25 in recorder.sizes and 10 in recorder.sizes
    assert len(results) == 50


def test_final_failure_at_size_one_falls_back_without_infinite_retry():
    items = _items(10)
    summarizer, recorder = _make(
        lambda call_no, prompt, schema: "broken", GEMINI_MAX_REQUESTS_PER_RUN=50
    )
    results = summarizer.summarize_many(items)

    assert results == {}
    assert 1 in recorder.sizes  # 최종 복구 수단으로 개별 호출까지 내려갔다
    assert recorder.count <= 50


# --- 요청 예산 --------------------------------------------------------------


def test_request_budget_stops_remaining_batches():
    items = _items(250)
    summarizer, recorder = _make(_all_ok(items), GEMINI_MAX_REQUESTS_PER_RUN=3)
    results = summarizer.summarize_many(items)

    assert recorder.count == 3
    assert len(results) == 150  # 3배치 × 50건, 나머지 100건은 extractive fallback


def test_zero_request_budget_disables_the_feature():
    summarizer, recorder = _make(lambda *a, **k: "", GEMINI_MAX_REQUESTS_PER_RUN=0)
    assert summarizer.disabled
    assert summarizer.disabled_reason == "max_requests_zero"
    assert summarizer.summarize_many(_items(10)) == {}
    assert recorder.count == 0


# --- 일시적 오류 ------------------------------------------------------------


def test_429_retries_the_same_batch():
    items = _items(50)

    def responder(call_no, prompt, schema):
        if call_no == 1:
            raise FakeAPIError(429)
        ids = _ids_in_prompt(prompt)
        return json.dumps(
            {"summaries": [_entry(i, _lines(n)) for n, i in enumerate(ids)]},
            ensure_ascii=False,
        )

    summarizer, recorder = _make(responder)
    results = summarizer.summarize_many(items)

    assert recorder.sizes == [50, 50]  # 분할이 아니라 동일 배치 재시도
    assert len(results) == 50


def test_429_exhausted_falls_back_without_splitting():
    items = _items(50)
    summarizer, recorder = _make(
        lambda call_no, prompt, schema: (_ for _ in ()).throw(FakeAPIError(429))
    )
    results = summarizer.summarize_many(items)

    assert results == {}
    assert recorder.sizes == [50, 50]  # retry_attempts=2, 분할 없음
    assert 25 not in recorder.sizes


def test_5xx_then_success():
    items = _items(20)

    def responder(call_no, prompt, schema):
        if call_no == 1:
            raise FakeAPIError(503)
        ids = _ids_in_prompt(prompt)
        return json.dumps(
            {"summaries": [_entry(i, _lines(n)) for n, i in enumerate(ids)]},
            ensure_ascii=False,
        )

    summarizer, recorder = _make(responder)
    assert len(summarizer.summarize_many(items)) == 20
    assert recorder.count == 2


def test_timeout_is_retried():
    items = _items(20)
    summarizer, recorder = _make(
        lambda call_no, prompt, schema: (_ for _ in ()).throw(FakeTimeout("read timeout"))
    )
    assert summarizer.summarize_many(items) == {}
    assert recorder.count == 2


def test_400_is_not_retried_but_may_split():
    items = _items(50)

    def responder(call_no, prompt, schema):
        if schema["properties"]["summaries"]["maxItems"] > 25:
            raise FakeAPIError(400, "request too large")
        ids = _ids_in_prompt(prompt)
        return json.dumps(
            {"summaries": [_entry(i, _lines(n)) for n, i in enumerate(ids)]},
            ensure_ascii=False,
        )

    summarizer, recorder = _make(responder)
    results = summarizer.summarize_many(items)

    assert recorder.sizes == [50, 25, 25]  # 400은 재시도 없이 곧장 분할
    assert len(results) == 50


# --- 인증 실패 / circuit breaker --------------------------------------------


@pytest.mark.parametrize("code", [401, 403])
def test_auth_error_stops_all_remaining_gemini_calls(code):
    items = _items(250)
    summarizer, recorder = _make(
        lambda call_no, prompt, schema: (_ for _ in ()).throw(FakeAPIError(code))
    )
    results = summarizer.summarize_many(items)

    assert results == {}
    assert recorder.count == 1  # 재시도도 분할도 하지 않는다
    assert summarizer.disabled_reason == "auth"
    # 이후 호출도 전부 차단
    assert summarizer.summarize_many(items) == {}
    assert recorder.count == 1


def test_invalid_model_id_stops_all_remaining_calls():
    items = _items(100)
    summarizer, recorder = _make(
        lambda call_no, prompt, schema: (_ for _ in ()).throw(FakeAPIError(404)),
        GEMINI_MODEL="gemini-does-not-exist",
    )
    assert summarizer.summarize_many(items) == {}
    assert recorder.count == 1
    assert summarizer.disabled_reason == "bad_model"


def test_consecutive_batch_failures_open_the_circuit_breaker():
    items = _items(150)
    summarizer, recorder = _make(
        lambda call_no, prompt, schema: (_ for _ in ()).throw(FakeAPIError(500)),
        GEMINI_RETRY_ATTEMPTS=1,
        GEMINI_CIRCUIT_BREAKER_FAILURES=2,
        GEMINI_MAX_REQUESTS_PER_RUN=50,
    )
    assert summarizer.summarize_many(items) == {}
    assert summarizer.disabled_reason == "consecutive_failures"
    assert recorder.count == 2  # 3번째 배치는 시도조차 하지 않는다


def test_successful_batch_resets_the_failure_counter():
    items = _items(150)

    def responder(call_no, prompt, schema):
        if call_no == 2:
            ids = _ids_in_prompt(prompt)
            return json.dumps(
                {"summaries": [_entry(i, _lines(n)) for n, i in enumerate(ids)]},
                ensure_ascii=False,
            )
        raise FakeAPIError(500)

    summarizer, _ = _make(
        responder,
        GEMINI_RETRY_ATTEMPTS=1,
        GEMINI_CIRCUIT_BREAKER_FAILURES=3,
        GEMINI_MAX_REQUESTS_PER_RUN=50,
    )
    summarizer.summarize_many(items)
    assert not summarizer.disabled


# --- 프로그래밍 오류 --------------------------------------------------------


def test_programming_error_is_not_silently_swallowed():
    items = _items(10)
    summarizer, _ = _make(
        lambda call_no, prompt, schema: (_ for _ in ()).throw(TypeError("bad kwargs"))
    )
    with pytest.raises(GeminiProgrammingError):
        summarizer.summarize_many(items)
    assert summarizer.disabled_reason == "programming_error"


# --- 페이싱 ----------------------------------------------------------------


def test_min_interval_paces_consecutive_requests():
    clock = {"now": 0.0}
    slept: list[float] = []
    items = _items(100)

    def responder(call_no, prompt, schema):
        clock["now"] += 1.0
        ids = _ids_in_prompt(prompt)
        return json.dumps(
            {"summaries": [_entry(i, _lines(n)) for n, i in enumerate(ids)]},
            ensure_ascii=False,
        )

    os.environ["GEMINI_MIN_INTERVAL_SECONDS"] = "2"
    recorder = Recorder(responder)
    summarizer = GeminiBatchSummarizer(
        load_gemini_config(),
        generate_fn=recorder,
        sleep_fn=lambda s: (slept.append(s), clock.__setitem__("now", clock["now"] + s)),
        monotonic_fn=lambda: clock["now"],
    )
    summarizer.summarize_many(items)

    assert recorder.count == 2
    assert slept == [pytest.approx(1.0)]  # 응답에 1초 걸렸으니 남은 1초만 대기


# --- 비활성 조건 ------------------------------------------------------------


def test_missing_api_key_disables_without_calling(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    summarizer, recorder = _make(lambda *a, **k: "")
    assert summarizer.disabled_reason == "no_api_key"
    assert summarizer.summarize_many(_items(50)) == {}
    assert recorder.count == 0


def test_gemini_enabled_false_disables_without_calling():
    summarizer, recorder = _make(lambda *a, **k: "", GEMINI_ENABLED="0")
    assert summarizer.disabled_reason == "disabled_by_env"
    assert recorder.count == 0


def test_max_summaries_zero_disables():
    summarizer, _ = _make(lambda *a, **k: "", GEMINI_MAX_SUMMARIES="0")
    assert summarizer.disabled_reason == "max_summaries_zero"


def test_empty_item_list_makes_no_request():
    summarizer, recorder = _make(lambda *a, **k: "")
    assert summarizer.summarize_many([]) == {}
    assert recorder.count == 0


# --- 로그 안전성 ------------------------------------------------------------


def test_logs_never_contain_api_key_or_article_body(caplog):
    secret = "일급비밀_기사_본문_토큰"
    items = iter_batch_items(
        ((f"비밀 제목 {i}", f"{secret} " * 40) for i in range(20)), article_max_chars=3000
    )
    responses = ["broken", "broken", "broken"]

    def responder(call_no, prompt, schema):
        if call_no <= len(responses):
            return responses[call_no - 1]
        raise FakeAPIError(401)

    summarizer, _ = _make(responder, GEMINI_MAX_REQUESTS_PER_RUN=40)
    with caplog.at_level(logging.DEBUG):
        summarizer.summarize_many(items)

    blob = "\n".join(record.getMessage() for record in caplog.records)
    assert blob  # 오류가 조용히 삼켜지지 않고 실제로 기록됐다
    assert API_KEY not in blob
    assert secret not in blob
    assert "<article" not in blob  # 전체 프롬프트가 실리지 않는다
    assert "비밀 제목" not in blob


# --- 설정 검증 --------------------------------------------------------------


@pytest.mark.parametrize(
    "name,bad,attr,expected",
    [
        ("GEMINI_MAX_SUMMARIES", "-5", "max_summaries", 300),
        ("GEMINI_MAX_SUMMARIES", "not-a-number", "max_summaries", 300),
        ("GEMINI_MAX_SUMMARIES", "99999", "max_summaries", 300),
        ("GEMINI_BATCH_MAX_ARTICLES", "0", "batch_max_articles", 50),
        ("GEMINI_BATCH_MAX_ARTICLES", "-1", "batch_max_articles", 50),
        ("GEMINI_BATCH_MAX_INPUT_CHARS", "10", "batch_max_input_chars", 150_000),
        ("GEMINI_BATCH_MAX_INPUT_CHARS", "99999999", "batch_max_input_chars", 150_000),
        ("GEMINI_ARTICLE_MAX_CHARS", "5", "article_max_chars", 3_000),
        ("GEMINI_MAX_FETCH_ATTEMPTS", "-1", "max_fetch_attempts", 300),
        ("GEMINI_MAX_REQUESTS_PER_RUN", "-2", "max_requests_per_run", 20),
        ("GEMINI_MAX_REQUESTS_PER_RUN", "9999", "max_requests_per_run", 20),
        ("GEMINI_MAX_RECOVERY_REQUESTS", "-1", "max_recovery_requests", 8),
        ("GEMINI_MAX_RECOVERY_REQUESTS", "9999", "max_recovery_requests", 8),
        ("GEMINI_RETRY_ATTEMPTS", "0", "retry_attempts", 2),
        ("GEMINI_MIN_INTERVAL_SECONDS", "-3", "min_interval_seconds", 2.0),
        ("GEMINI_REQUEST_TIMEOUT_SECONDS", "99999", "request_timeout_seconds", 90.0),
        ("GEMINI_CIRCUIT_BREAKER_FAILURES", "0", "circuit_breaker_failures", 3),
    ],
)
def test_invalid_env_values_fall_back_to_safe_defaults(monkeypatch, name, bad, attr, expected):
    monkeypatch.delenv("GEMINI_MIN_INTERVAL_SECONDS", raising=False)
    monkeypatch.setenv(name, bad)
    assert getattr(load_gemini_config(), attr) == expected


def test_documented_defaults(monkeypatch):
    monkeypatch.delenv("GEMINI_MIN_INTERVAL_SECONDS", raising=False)
    monkeypatch_free = load_gemini_config()
    assert monkeypatch_free.max_summaries == 300
    assert monkeypatch_free.batch_max_articles == 50
    assert monkeypatch_free.batch_hard_max_articles == 100
    assert monkeypatch_free.batch_max_input_chars == 150_000
    assert monkeypatch_free.article_max_chars == 3_000
    assert monkeypatch_free.max_fetch_attempts == 300
    assert monkeypatch_free.max_requests_per_run == 20
    assert monkeypatch_free.max_recovery_requests == 8
    assert monkeypatch_free.min_interval_seconds == 2.0


def test_model_is_overridable_by_env(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")
    assert load_gemini_config().model == "gemini-3.6-flash"


def test_chunk_items_helper():
    items = _items(7)
    assert [len(c) for c in chunk_items(items, 3)] == [3, 3, 1]
    assert isinstance(items[0], BatchItem)


# --- 요청 예산 분리 (정상 / 복구) -------------------------------------------


def _partial_failure_responder(drop_ratio: float = 0.1):
    """각 배치에서 10%(최소 1건)를 누락시키는 응답기."""
    import math

    def responder(call_no, prompt, schema):
        ids = _ids_in_prompt(prompt)
        drop = max(1, math.ceil(len(ids) * drop_ratio))
        keep = ids[: len(ids) - drop]
        return json.dumps(
            {"summaries": [_entry(i, _lines(n)) for n, i in enumerate(keep)]},
            ensure_ascii=False,
        )

    return responder


def test_250_articles_with_partial_failures_fit_in_the_default_budget():
    """정상 5배치 + 각 배치 10% 누락 + 재요청에서 1건 재누락 시나리오."""
    items = _items(250)
    summarizer, recorder = _make(_partial_failure_responder())
    results = summarizer.summarize_many(items)

    # 정상 5회 + 복구 8회(상한) = 13회로 기본 상한 20 안에서 처리된다.
    assert summarizer.stats["normal_requests"] == 5
    assert summarizer.stats["recovery_requests"] == 8
    assert recorder.count == 13
    assert recorder.count <= 20
    # 예전 상한 12에서는 5번째 배치가 통째로 fallback됐다(196건).
    assert len(results) == 241
    assert 250 - len(results) == 9


def test_recovery_budget_never_starves_the_normal_batches():
    """복구 예산을 다 써도 남은 정상 배치는 계속 처리된다."""
    items = _items(250)
    summarizer, recorder = _make(_partial_failure_responder(), GEMINI_MAX_RECOVERY_REQUESTS=2)
    summarizer.summarize_many(items)

    assert summarizer.stats["normal_requests"] == 5  # 5개 정상 배치 전부 전송됨
    assert summarizer.stats["recovery_requests"] == 2
    assert recorder.count == 7


def test_recovery_budget_zero_disables_splitting_only():
    items = _items(100)
    summarizer, _ = _make(_partial_failure_responder(), GEMINI_MAX_RECOVERY_REQUESTS=0)
    results = summarizer.summarize_many(items)

    assert summarizer.stats["normal_requests"] == 2
    assert summarizer.stats["recovery_requests"] == 0
    assert len(results) == 90  # 배치당 5건씩 누락, 복구 없음


def test_transient_retries_count_against_the_recovery_budget():
    items = _items(50)
    calls = {"n": 0}

    def responder(call_no, prompt, schema):
        calls["n"] += 1
        raise FakeAPIError(429)

    summarizer, recorder = _make(responder, GEMINI_MAX_RECOVERY_REQUESTS=0)
    summarizer.summarize_many(items)

    # 복구 예산이 0이면 재시도를 하지 않는다(최초 요청 1회만).
    assert recorder.count == 1
    assert summarizer.stats["normal_requests"] == 1
    assert summarizer.stats["recovery_requests"] == 0


# --- 실행 단위 집계 ---------------------------------------------------------


def test_run_stats_expose_sanitized_aggregates():
    items = _items(100, body_chars=500)
    summarizer, recorder = _make(_all_ok(items))
    summarizer.summarize_many(items)
    stats = summarizer.stats

    assert stats["batches"] == 2
    assert stats["requests"] == 2
    assert stats["normal_requests"] == 2
    assert stats["recovery_requests"] == 0
    assert stats["sent_articles"] == 100
    assert stats["sent_chars"] == sum(len(c["prompt"]) for c in recorder.calls)
    assert stats["articles_ok"] == 100
    assert stats["items_rejected"] == 0
    assert stats["rate_limit_hits"] == 0
    assert not summarizer.breaker_tripped
    # 집계는 전부 숫자다 — 제목/본문/프롬프트가 섞여 들어가지 않는다.
    assert all(isinstance(v, int) for v in stats.values())


def test_rate_limit_hits_are_counted():
    items = _items(50)

    def responder(call_no, prompt, schema):
        if call_no <= 1:
            raise FakeAPIError(429)
        ids = _ids_in_prompt(prompt)
        return json.dumps(
            {"summaries": [_entry(i, _lines(n)) for n, i in enumerate(ids)]},
            ensure_ascii=False,
        )

    summarizer, _ = _make(responder)
    summarizer.summarize_many(items)
    assert summarizer.stats["rate_limit_hits"] == 1


def test_breaker_tripped_flag():
    items = _items(150)
    summarizer, _ = _make(
        lambda call_no, prompt, schema: (_ for _ in ()).throw(FakeAPIError(500)),
        GEMINI_RETRY_ATTEMPTS=1,
        GEMINI_CIRCUIT_BREAKER_FAILURES=2,
    )
    summarizer.summarize_many(items)
    assert summarizer.breaker_tripped
    assert summarizer.disabled_reason == "consecutive_failures"


def test_invalid_api_key_400_is_treated_as_auth_failure():
    """실제 SDK 검증 결과: 잘못된 API 키는 400 INVALID_ARGUMENT + API_KEY_INVALID로 온다."""
    items = _items(250)
    err = FakeAPIError(
        400,
        "API key not valid. Please pass a valid API key.",
        details={
            "error": {
                "code": 400,
                "status": "INVALID_ARGUMENT",
                "details": [{"reason": "API_KEY_INVALID", "domain": "googleapis.com"}],
            }
        },
    )
    summarizer, recorder = _make(
        lambda call_no, prompt, schema: (_ for _ in ()).throw(err)
    )
    assert summarizer.summarize_many(items) == {}
    # 400으로만 분류하면 breaker가 열릴 때까지 배치 3개를 태운다. 즉시 중단해야 한다.
    assert recorder.count == 1
    assert summarizer.disabled_reason == "auth"


def test_auth_detection_uses_the_structured_reason_not_free_text():
    """사람이 읽는 메시지가 아니라 기계가 읽는 reason 마커로만 판단한다.

    기사 본문·프롬프트에 'api key' 같은 문구가 있어도 오탐하지 않기 위함이다.
    """
    from src.pipeline.gemini_summary import CATEGORY_AUTH, CATEGORY_BAD_REQUEST, classify_error

    # 크기 문제로 인한 400은 그대로 bad_request (분할 대상)
    assert classify_error(FakeAPIError(400, "request payload too large")) == CATEGORY_BAD_REQUEST
    # 사람이 읽는 문구만으로는 auth로 승격하지 않는다
    assert classify_error(FakeAPIError(400, "API key not valid")) == CATEGORY_BAD_REQUEST
    # 구조화된 reason이 있으면 auth
    assert (
        classify_error(FakeAPIError(400, "x", details={"reason": "API_KEY_INVALID"}))
        == CATEGORY_AUTH
    )
    assert (
        classify_error(FakeAPIError(400, "x", details={"reason": "API_KEY_SERVICE_BLOCKED"}))
        == CATEGORY_AUTH
    )
    assert classify_error(FakeAPIError(403, "denied")) == CATEGORY_AUTH


# --- SDK 클라이언트 구성 (가짜 모듈 주입 — 실제 네트워크 없음) -----------------


def _install_fake_genai(monkeypatch, captured):
    """google.genai를 가짜로 주입해 client 구성 인자를 캡처한다."""
    import sys
    import types as pytypes

    class HttpRetryOptions:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class HttpOptions:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            captured["http_options"] = kw

    class ThinkingConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            captured["thinking"] = kw

    class GenerateContentConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            captured["gen_config"] = kw

    class _Models:
        def generate_content(self, **kw):
            captured["request"] = kw
            return pytypes.SimpleNamespace(text='{"summaries": []}')

    class Client:
        def __init__(self, **kw):
            captured["client"] = kw
            self.models = _Models()

    types_mod = pytypes.ModuleType("google.genai.types")
    types_mod.HttpOptions = HttpOptions
    types_mod.HttpRetryOptions = HttpRetryOptions
    types_mod.ThinkingConfig = ThinkingConfig
    types_mod.GenerateContentConfig = GenerateContentConfig

    genai_mod = pytypes.ModuleType("google.genai")
    genai_mod.Client = Client
    genai_mod.types = types_mod

    google_mod = pytypes.ModuleType("google")
    google_mod.genai = genai_mod

    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_mod)


def test_client_pins_api_version_and_disables_sdk_retries(monkeypatch):
    from src.pipeline.gemini_summary import (
        API_VERSION, THINKING_LEVEL, build_generate_fn, response_json_schema,
    )

    captured: dict = {}
    _install_fake_genai(monkeypatch, captured)
    monkeypatch.setenv("GEMINI_REQUEST_TIMEOUT_SECONDS", "90")

    fn = build_generate_fn(load_gemini_config())
    fn(system_instruction="s", prompt="p", schema=response_json_schema(5))

    http = captured["http_options"]
    assert API_VERSION == "v1"
    assert http["api_version"] == "v1"
    # timeout은 밀리초로 변환된다.
    assert http["timeout"] == 90_000
    # SDK 자동 재시도는 꺼둔다 — 재시도 예산은 이 모듈이 통제한다.
    assert http["retry_options"].attempts == 1
    assert captured["thinking"]["thinking_level"] == THINKING_LEVEL
    assert captured["gen_config"]["response_mime_type"] == "application/json"
    assert captured["gen_config"]["response_json_schema"]["properties"]["summaries"]["maxItems"] == 5


def test_client_is_constructed_once_across_requests(monkeypatch):
    from src.pipeline.gemini_summary import build_generate_fn, response_json_schema

    captured: dict = {}
    calls = {"n": 0}
    _install_fake_genai(monkeypatch, captured)

    import sys

    original = sys.modules["google.genai"].Client

    class CountingClient(original):
        def __init__(self, **kw):
            calls["n"] += 1
            super().__init__(**kw)

    sys.modules["google.genai"].Client = CountingClient
    fn = build_generate_fn(load_gemini_config())
    for _ in range(3):
        fn(system_instruction="s", prompt="p", schema=response_json_schema(1))
    assert calls["n"] == 1


def test_thinking_config_omitted_for_pre_gemini_3_models(monkeypatch):
    from src.pipeline.gemini_summary import build_generate_fn, response_json_schema

    captured: dict = {}
    _install_fake_genai(monkeypatch, captured)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.0-flash")

    fn = build_generate_fn(load_gemini_config())
    fn(system_instruction="s", prompt="p", schema=response_json_schema(1))

    assert "thinking" not in captured  # ThinkingConfig 자체를 만들지 않는다
    assert "thinking_config" not in captured["gen_config"]
    # api_version은 모델과 무관하게 항상 고정한다.
    assert captured["http_options"]["api_version"] == "v1"


# --- 내용 품질 게이트 (usable=false) -----------------------------------------
#
# 실제 50건 smoke test에서 나온 회귀 사례들이다. API 오류 0, 구조 검증 실패 0인데도
# 제목과 무관한 여러 뉴스가 한 요약에 섞여 나왔다.


def _content_gate_response(prompt, unusable: dict[str, str]):
    """unusable에 있는 id는 usable=false로, 나머지는 정상 3줄로 답한다."""
    ids = _ids_in_prompt(prompt)
    summaries = []
    for n, article_id in enumerate(ids):
        if article_id in unusable:
            summaries.append(_unusable(article_id, unusable[article_id]))
        else:
            summaries.append(_entry(article_id, _lines(n)))
    return json.dumps({"summaries": summaries}, ensure_ascii=False)


def test_president_approval_title_with_unrelated_body_is_unusable():
    """제목은 대통령 지지율인데 본문에 대부업·형사사건이 섞인 경우."""
    items = iter_batch_items(
        [
            ("대통령 지지율 52% ... 전주 대비 3%p 상승", "지지율 조사 결과 " * 30),
            ("금융위, 대부업 감독 규정 개정", "대부업 감독 규정 " * 30),
        ],
        article_max_chars=3000,
    )
    mismatch = {items[0].id: "title_body_mismatch"}
    summarizer, recorder = _make(
        lambda call_no, prompt, schema: _content_gate_response(prompt, mismatch)
    )
    results = summarizer.summarize_many(items)

    assert items[0].id not in results  # AI 요약으로 쓰이지 않는다
    assert items[1].id in results  # 같은 배치의 정상 항목은 그대로 적용
    assert recorder.count == 1  # 재요청 없음
    assert summarizer.stats["title_body_mismatch"] == 1
    assert summarizer.stats["content_rejected"] == 1


def test_bank_title_with_reader_tip_roundup_body_is_unusable():
    """IBK기업은행 제목인데 본문이 화물차 단속·동성결혼·독자 제보 모음인 경우."""
    items = iter_batch_items(
        [("IBK기업은행, 중소기업 대출 확대", "독자 제보 화물차 단속 " * 30)],
        article_max_chars=3000,
    )
    summarizer, recorder = _make(
        lambda call_no, prompt, schema: _content_gate_response(
            prompt, {items[0].id: "title_body_mismatch"}
        )
    )
    assert summarizer.summarize_many(items) == {}
    assert recorder.count == 1
    assert summarizer.stats["title_body_mismatch"] == 1


def test_front_page_roundup_without_a_single_topic_is_unusable():
    """신문 1면 모음 기사 — 서로 다른 사건을 한 줄씩 나열하면 안 된다."""
    items = iter_batch_items(
        [("[오늘의 신문 1면] 주요 기사 모음", "1면 헤드라인 모음 " * 30)],
        article_max_chars=3000,
    )
    summarizer, _ = _make(
        lambda call_no, prompt, schema: _content_gate_response(
            prompt, {items[0].id: "multi_topic"}
        )
    )
    assert summarizer.summarize_many(items) == {}
    assert summarizer.stats["multi_topic"] == 1
    assert summarizer.stats["content_rejected"] == 1


def test_insufficient_content_is_unusable():
    items = _items(1)
    summarizer, _ = _make(
        lambda call_no, prompt, schema: _content_gate_response(
            prompt, {items[0].id: "insufficient_content"}
        )
    )
    assert summarizer.summarize_many(items) == {}
    assert summarizer.stats["insufficient_content"] == 1


def test_matching_title_and_body_stays_usable_with_exactly_three_lines():
    items = _items(3)
    summarizer, recorder = _make(_all_ok(items))
    results = summarizer.summarize_many(items)

    assert len(results) == 3
    assert all(len(v) == 3 for v in results.values())
    assert summarizer.stats["content_rejected"] == 0
    assert recorder.count == 1


def test_unusable_items_are_never_re_requested():
    items = _items(50)
    unusable = {items[i].id: "multi_topic" for i in range(10)}
    summarizer, recorder = _make(
        lambda call_no, prompt, schema: _content_gate_response(prompt, unusable)
    )
    results = summarizer.summarize_many(items)

    # usable=false는 정상 응답이므로 분할·재요청 대상이 아니다.
    assert recorder.count == 1
    assert recorder.sizes == [50]
    assert len(results) == 40
    assert summarizer.stats["splits"] == 0


def test_content_rejection_is_not_counted_as_an_error():
    items = _items(20)
    unusable = {items[i].id: "title_body_mismatch" for i in range(5)}
    summarizer, _ = _make(
        lambda call_no, prompt, schema: _content_gate_response(prompt, unusable)
    )
    summarizer.summarize_many(items)
    stats = summarizer.stats

    assert stats["content_rejected"] == 5
    # API 오류도, 구조 위반도 아니다.
    assert stats["api_error"] == 0
    assert stats["items_rejected"] == 0
    assert stats["articles_ok"] == 15
    assert not summarizer.disabled


def test_all_unusable_batch_does_not_trip_the_circuit_breaker():
    """뉴스 모음만 담긴 배치가 와도 API는 정상이므로 breaker가 열리면 안 된다."""
    items = _items(150)

    def responder(call_no, prompt, schema):
        ids = _ids_in_prompt(prompt)
        return json.dumps(
            {"summaries": [_unusable(i, "multi_topic") for i in ids]}, ensure_ascii=False
        )

    summarizer, recorder = _make(responder, GEMINI_CIRCUIT_BREAKER_FAILURES=2)
    assert summarizer.summarize_many(items) == {}
    assert not summarizer.disabled
    assert recorder.count == 3  # 세 배치 모두 정상 처리
    assert summarizer.stats["content_rejected"] == 150


def test_fifty_usable_articles_still_take_exactly_one_request():
    items = _items(50)
    summarizer, recorder = _make(_all_ok(items))
    assert len(summarizer.summarize_many(items)) == 50
    assert recorder.count == 1


def test_usable_true_with_non_ok_reason_is_a_structural_violation():
    items = _items(2)

    def responder(call_no, prompt, schema):
        ids = _ids_in_prompt(prompt)
        return json.dumps(
            {
                "summaries": [
                    _entry(ids[0], _lines(0), reason="multi_topic"),
                    _entry(ids[1], _lines(1)),
                ]
            },
            ensure_ascii=False,
        )

    outcome = validate_batch_response(responder(1, build_batch_prompt(items), None), items)
    assert set(outcome.accepted) == {items[1].id}
    assert outcome.failed_ids == [items[0].id]
    assert outcome.rejected_reasons.get("usable_reason_conflict") == 1
    assert outcome.content_rejected == {}


def test_usable_true_with_broken_lines_is_still_a_structural_violation():
    items = _items(2)
    payload = json.dumps(
        {"summaries": [_entry(items[0].id, _lines(0)[:2]), _entry(items[1].id, _lines(1))]},
        ensure_ascii=False,
    )
    outcome = validate_batch_response(payload, items)
    assert outcome.failed_ids == [items[0].id]
    assert outcome.content_rejected == {}


def test_unusable_with_lines_present_still_rejects_the_content():
    """usable=false면 lines가 들어있어도 표시하지 않는다."""
    items = _items(1)
    payload = json.dumps(
        {
            "summaries": [
                _entry(items[0].id, _lines(0), usable=False, reason="multi_topic")
            ]
        },
        ensure_ascii=False,
    )
    outcome = validate_batch_response(payload, items)
    assert outcome.accepted == {}
    assert outcome.content_rejected == {items[0].id: "multi_topic"}
    assert outcome.failed_ids == []


def test_unknown_unusable_reason_is_still_rejected_safely():
    items = _items(1)
    payload = json.dumps(
        {"summaries": [_entry(items[0].id, [], usable=False, reason="something_else")]},
        ensure_ascii=False,
    )
    outcome = validate_batch_response(payload, items)
    assert outcome.content_rejected == {items[0].id: "unspecified"}
    assert outcome.failed_ids == []


@pytest.mark.parametrize("bad", [{"usable": "yes"}, {"reason": 5}])
def test_malformed_usable_or_reason_is_a_structural_violation(bad):
    items = _items(1)
    entry = _entry(items[0].id, _lines(0))
    entry.update(bad)
    outcome = validate_batch_response(
        json.dumps({"summaries": [entry]}, ensure_ascii=False), items
    )
    assert outcome.failed_ids == [items[0].id]
    assert outcome.rejected_reasons.get("usable_contract") == 1


def test_prompt_states_the_single_topic_rule_and_unusable_reasons():
    prompt = build_batch_prompt(_items(2))
    assert "usable=false" in prompt
    for reason in ("title_body_mismatch", "multi_topic", "insufficient_content"):
        assert reason in prompt
    assert "하나의 핵심 사건" in prompt
    assert "사이드바" in prompt
    assert "억지로" in prompt or "추측해 채우지 않는다" in prompt
