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
    [(25, 1), (50, 2), (100, 4), (250, 10), (300, 12)],
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
    # 정상 경로에서는 검증된 배치 크기(25)로만 나가고 크기 1은 없다.
    assert recorder.sizes == [25, 25]
    assert 1 not in recorder.sizes


def test_char_budget_closes_the_batch_before_the_article_count(monkeypatch):
    # 기사당 ~3,116자(본문 3,000 + ID/제목/구분자) × 50건 ≈ 155,800자 > 150,000자 예산
    # → 배치 크기를 50으로 올려도 문자 예산이 먼저 배치를 닫는다.
    monkeypatch.setenv("GEMINI_BATCH_MAX_ARTICLES", "50")
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


def test_oversized_single_article_is_truncated_to_fit_the_budget():
    """단독으로도 예산을 넘는 기사는 더 잘라서 보낸다 — 예산을 무시하고 담지 않는다.

    첫 항목이라는 이유로 그냥 담으면 요청당 입력 상한이 무력화되고, 그렇게 만든
    요청은 400을 받아도 이미 크기 1이라 분할로 복구할 수 없다.
    """
    items = _items(2, body_chars=3000)
    budget = prompt_overhead_chars() + 1200
    batches = plan_batches(items, max_articles=50, max_input_chars=budget)

    assert [len(b) for b in batches] == [1, 1]
    for batch in batches:
        assert prompt_overhead_chars() + estimate_item_chars(batch[0]) <= budget
        assert len(batch[0].body) < 3000  # 예산에 맞춰 더 잘렸다


def test_fitting_never_goes_below_the_configured_input_minimum():
    """예산에 맞추려고 잘라도 운영자가 정한 입력 하한 아래로는 내려가지 않는다."""
    from src.pipeline.gemini_summary import fit_item_to_budget

    item = _items(1, body_chars=2000)[0]
    budget = prompt_overhead_chars() + len(item.id) + len(item.title) + 64 + 400

    # 기본 하한(200자)에서는 400자로 잘려 전송된다.
    fitted = fit_item_to_budget(item, max_input_chars=budget)
    assert fitted is not None and len(fitted.body) <= 400

    # 하한을 1000자로 올리면 400자로 잘라 보내지 않고 아예 건너뛴다.
    assert fit_item_to_budget(item, max_input_chars=budget, min_body_chars=1000) is None


def test_summarizer_applies_the_configured_input_minimum_when_fitting():
    """summarize_many 경로에서도 GEMINI_INPUT_MIN_CHARS가 fit 하한으로 쓰인다."""
    items = _items(2, body_chars=2000)
    per_item = estimate_item_chars(items[0])
    summarizer, recorder = _make(
        _all_ok(items),
        GEMINI_BATCH_MAX_INPUT_CHARS=prompt_overhead_chars() + per_item // 2,
        GEMINI_INPUT_MIN_CHARS=1500,
    )
    results = summarizer.summarize_many(items)

    assert recorder.count == 0  # 하한을 지킬 수 없으니 아무것도 보내지 않는다
    assert results == {}


def test_article_that_cannot_fit_the_budget_at_all_is_dropped(caplog):
    """본문을 남길 자리조차 없으면 보내지 않는다(추출요약 fallback)."""
    items = _items(2, body_chars=3000)
    with caplog.at_level(logging.WARNING):
        assert plan_batches(items, max_articles=50, max_input_chars=100) == []
    assert any("input budget" in record.message for record in caplog.records)


def test_capacity_planner_accounts_for_the_char_budget():
    """전송 가능량은 '요청 수 × 배치 크기'가 아니다 — 문자 예산이 먼저 걸리면 더 적다."""
    from src.pipeline.gemini_summary import BatchCapacityPlanner

    item_chars = estimate_item_chars(_items(1, body_chars=3000)[0])
    # 요청 1회 / 배치 25건이지만 문자 예산상 요청 1건에 1기사만 들어간다.
    planner = BatchCapacityPlanner(
        max_articles=25,
        max_input_chars=prompt_overhead_chars() + item_chars,
        max_requests=1,
        min_item_chars=200,
    )
    assert planner.has_room()
    assert planner.try_add(item_chars)
    assert not planner.has_room()  # 두 번째 기사는 새 요청이 필요한데 예산이 없다
    assert planner.planned_batches == 1


def test_capacity_planner_counts_articles_when_chars_are_generous():
    from src.pipeline.gemini_summary import BatchCapacityPlanner

    planner = BatchCapacityPlanner(
        max_articles=2, max_input_chars=10**9, max_requests=2, min_item_chars=100
    )
    for _ in range(4):  # 2건 × 2요청 = 4건까지는 여유가 있다
        assert planner.has_room()
        assert planner.try_add(100)
    assert not planner.has_room()
    assert planner.planned_batches == 2


def test_capacity_planner_never_spills_past_the_request_budget():
    """최소 크기 가정으로 통과해도, 실제 크기가 크면 예산을 넘겨 담지 않는다.

    여기서 배치를 열어버리면 계획 상태가 실제 전송 가능량보다 커지고,
    `has_room()`이 계속 True를 돌려줘 전송되지 않을 기사를 계속 크롤링하게 된다.
    """
    from src.pipeline.gemini_summary import BatchCapacityPlanner

    planner = BatchCapacityPlanner(
        max_articles=25,
        max_input_chars=prompt_overhead_chars() + 700,
        max_requests=1,
        min_item_chars=200,
    )
    assert planner.try_add(500)
    # 남은 자리는 200자뿐 — 최소 크기 기준으로는 "자리 있음"이다.
    assert planner.has_room()
    # 그런데 실제 기사는 500자다 → 새 요청이 필요한데 예산이 없다.
    assert not planner.try_add(500)
    assert planner.planned_batches == 1
    # 경계에서 fetch를 반복 낭비하지 않도록 준비를 닫는다.
    assert not planner.has_room()


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

    summarizer, recorder = _make(responder, GEMINI_BATCH_MAX_ARTICLES=50)
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

    summarizer, recorder = _make(responder, GEMINI_BATCH_MAX_ARTICLES=50)
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

    summarizer, recorder = _make(
        responder, GEMINI_MAX_REQUESTS_PER_RUN=30, GEMINI_BATCH_MAX_ARTICLES=50
    )
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
    assert len(results) == 75  # 3배치 × 25건, 나머지 175건은 extractive fallback


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

    summarizer, recorder = _make(responder, GEMINI_BATCH_MAX_ARTICLES=50)
    results = summarizer.summarize_many(items)

    assert recorder.sizes == [50, 50]  # 분할이 아니라 동일 배치 재시도
    assert len(results) == 50


def test_429_exhausted_falls_back_without_splitting():
    items = _items(50)
    summarizer, recorder = _make(
        lambda call_no, prompt, schema: (_ for _ in ()).throw(FakeAPIError(429)),
        GEMINI_BATCH_MAX_ARTICLES=50,
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

    summarizer, recorder = _make(responder, GEMINI_BATCH_MAX_ARTICLES=50)
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
        GEMINI_BATCH_MAX_ARTICLES=50,
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

    # 100건 / 배치 25 = 4회, 첫 호출은 대기 없음
    assert recorder.count == 4
    assert slept == [pytest.approx(1.0)] * 3  # 응답에 1초 걸렸으니 남은 1초만 대기


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
        ("GEMINI_BATCH_MAX_ARTICLES", "0", "batch_max_articles", 25),
        ("GEMINI_BATCH_MAX_ARTICLES", "-1", "batch_max_articles", 25),
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
    assert monkeypatch_free.batch_max_articles == 25
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

    # 배치 25 기준 정상 10회 + 복구 8회(상한) = 18회로 기본 상한 20 안에서 끝난다.
    assert summarizer.stats["normal_requests"] == 10
    assert summarizer.stats["recovery_requests"] == 8
    assert recorder.count == 18
    assert recorder.count <= 20
    assert len(results) == 228
    assert 250 - len(results) == 22


def test_recovery_budget_never_starves_the_normal_batches():
    """복구 예산을 다 써도 남은 정상 배치는 계속 처리된다."""
    items = _items(250)
    summarizer, recorder = _make(_partial_failure_responder(), GEMINI_MAX_RECOVERY_REQUESTS=2)
    summarizer.summarize_many(items)

    assert summarizer.stats["normal_requests"] == 10  # 10개 정상 배치 전부 전송됨
    assert summarizer.stats["recovery_requests"] == 2
    assert recorder.count == 12


def test_total_request_budget_is_reserved_for_unsent_normal_batches():
    """복구가 총 요청 예산을 먼저 써버려 뒤쪽 정상 배치가 굶으면 안 된다.

    smoke 설정(요청 4회 / 정상 배치 2개)처럼 총 예산이 정상 배치 수에 가까우면
    복구 예산(기본 8)만으로는 굶주림을 막지 못한다 — 남은 정상 배치 몫을 예약한다.
    """
    items = _items(50)
    first_ids = {item.id for item in items[:25]}

    def responder(call_no, prompt, schema):
        ids = _ids_in_prompt(prompt)
        if ids[0] in first_ids:
            return "broken"  # 첫 배치는 무슨 크기로 쪼개도 실패한다
        return json.dumps(
            {"summaries": [_entry(i, _lines(n)) for n, i in enumerate(ids)]},
            ensure_ascii=False,
        )

    summarizer, recorder = _make(
        responder,
        GEMINI_MAX_REQUESTS_PER_RUN=4,
        GEMINI_BATCH_MAX_ARTICLES=25,
        GEMINI_CIRCUIT_BREAKER_FAILURES=50,  # 여기서 보는 건 예산 배분이다
    )
    results = summarizer.summarize_many(items)

    assert recorder.count == 4
    # 두 번째 정상 배치가 반드시 전송된다(복구에 밀려 사라지지 않는다).
    assert summarizer.stats["normal_requests"] == 2
    assert recorder.calls[-1]["ids"] == sorted(item.id for item in items[25:])
    assert set(results) == {item.id for item in items[25:]}


def test_transient_retry_also_respects_the_reserved_normal_capacity():
    """429 재시도도 복구 요청이다 — 뒤쪽 정상 배치 몫을 먹으면 안 된다."""
    items = _items(50)
    first_ids = {item.id for item in items[:25]}

    def responder(call_no, prompt, schema):
        ids = _ids_in_prompt(prompt)
        if ids[0] in first_ids:
            raise FakeAPIError(429)
        return json.dumps(
            {"summaries": [_entry(i, _lines(n)) for n, i in enumerate(ids)]},
            ensure_ascii=False,
        )

    summarizer, recorder = _make(
        responder, GEMINI_MAX_REQUESTS_PER_RUN=2, GEMINI_BATCH_MAX_ARTICLES=25
    )
    results = summarizer.summarize_many(items)

    # 1회차(첫 배치 429) + 2회차(두 번째 정상 배치) — 재시도에 예산을 쓰지 않는다.
    assert recorder.count == 2
    assert summarizer.stats["normal_requests"] == 2
    assert summarizer.stats["recovery_requests"] == 0
    assert set(results) == {item.id for item in items[25:]}


def test_recovery_budget_zero_disables_splitting_only():
    items = _items(100)
    summarizer, _ = _make(_partial_failure_responder(), GEMINI_MAX_RECOVERY_REQUESTS=0)
    results = summarizer.summarize_many(items)

    assert summarizer.stats["normal_requests"] == 4
    assert summarizer.stats["recovery_requests"] == 0
    assert len(results) == 88  # 배치(25)당 3건씩 누락, 복구 없음


def test_transient_retries_count_against_the_recovery_budget():
    items = _items(50)
    calls = {"n": 0}

    def responder(call_no, prompt, schema):
        calls["n"] += 1
        raise FakeAPIError(429)

    summarizer, recorder = _make(
        responder, GEMINI_MAX_RECOVERY_REQUESTS=0, GEMINI_BATCH_MAX_ARTICLES=50
    )
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

    assert stats["batches"] == 4
    assert stats["requests"] == 4
    assert stats["normal_requests"] == 4
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
    assert recorder.count == 2  # 50건 = 25 + 25 정상 배치
    assert recorder.sizes == [25, 25]
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
    assert recorder.count == 6  # 150건 = 25 × 6배치, 모두 정상 처리
    assert summarizer.stats["content_rejected"] == 150


def test_usable_articles_take_the_expected_number_of_requests():
    # 검증된 기본 배치는 25다 — 25건은 1회, 50건은 2회.
    items = _items(25)
    summarizer, recorder = _make(_all_ok(items))
    assert len(summarizer.summarize_many(items)) == 25
    assert recorder.count == 1

    items = _items(50)
    summarizer, recorder = _make(_all_ok(items))
    assert len(summarizer.summarize_many(items)) == 50
    assert recorder.count == 2


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


def test_unusable_contract_is_reason_plus_empty_lines():
    """usable=false의 정상 형태 — 사유는 목록에 있고 lines는 빈 배열이다."""
    items = _items(1)
    payload = json.dumps(
        {"summaries": [_entry(items[0].id, [], usable=False, reason="multi_topic")]},
        ensure_ascii=False,
    )
    outcome = validate_batch_response(payload, items)
    assert outcome.accepted == {}
    assert outcome.content_rejected == {items[0].id: "multi_topic"}
    assert outcome.failed_ids == []


def test_unusable_with_lines_present_is_a_structural_violation():
    """usable=false인데 lines가 들어있으면 "정상적인 요약 불가 신고"가 아니다.

    내용 거부로 세면 재요청 대상에서 빠지고, smoke strict 검증도 "게이트가 걸렀다"로
    읽어 초록이 된다 — 모델이 이 형태를 계속 뱉으면 AI 요약이 전부 사라져도 모른다.
    """
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
    assert outcome.content_rejected == {}
    assert outcome.failed_ids == [items[0].id]
    assert outcome.rejected_reasons.get("unusable_lines_present") == 1


def test_unknown_unusable_reason_is_a_structural_violation():
    """스키마 enum 밖의 사유는 계약 위반이다 — 조용한 내용 거부로 흡수하지 않는다."""
    items = _items(1)
    payload = json.dumps(
        {"summaries": [_entry(items[0].id, [], usable=False, reason="something_else")]},
        ensure_ascii=False,
    )
    outcome = validate_batch_response(payload, items)
    assert outcome.content_rejected == {}
    assert outcome.failed_ids == [items[0].id]
    assert outcome.rejected_reasons.get("unusable_reason_conflict") == 1


def test_unusable_reason_ok_is_a_structural_violation():
    """usable=false + reason="ok"는 모순이다(usable=true + reason≠ok과 대칭)."""
    items = _items(1)
    payload = json.dumps(
        {"summaries": [_entry(items[0].id, [], usable=False, reason="ok")]},
        ensure_ascii=False,
    )
    outcome = validate_batch_response(payload, items)
    assert outcome.content_rejected == {}
    assert outcome.failed_ids == [items[0].id]
    assert outcome.rejected_reasons.get("unusable_reason_conflict") == 1


def test_malformed_unusable_items_are_retried_not_silently_dropped():
    """구조 위반이므로 분할 재요청 대상이고, 끝내 실패하면 추출요약으로 내려간다."""
    items = _items(30)
    bad_ids = {items[0].id}

    def responder(call_no, prompt, schema):
        ids = _ids_in_prompt(prompt)
        return json.dumps(
            {
                "summaries": [
                    _entry(i, [], usable=False, reason="ok")
                    if i in bad_ids
                    else _entry(i, _lines(n))
                    for n, i in enumerate(ids)
                ]
            },
            ensure_ascii=False,
        )

    summarizer, recorder = _make(responder)
    results = summarizer.summarize_many(items)

    assert items[0].id not in results  # 표시는 추출요약으로 fallback
    assert len(results) == 29
    assert summarizer.stats["items_rejected"] >= 1
    assert summarizer.stats["content_rejected"] == 0  # 내용 거부로 세지 않는다
    assert recorder.count > 2  # 재요청이 실제로 일어났다


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


# --- 400 오류 진단 (기계 판독 가능한 토큰만) ---------------------------------


def test_400_diagnosis_extracts_only_safe_tokens():
    from src.pipeline.gemini_summary import describe_client_error

    err = FakeAPIError(
        400,
        "Invalid JSON payload received. Unknown name response_json_schema",
        details={
            "error": {
                "code": 400,
                "status": "INVALID_ARGUMENT",
                "details": [{"reason": "INVALID_ARGUMENT", "domain": "googleapis.com"}],
            }
        },
    )
    err.status = "INVALID_ARGUMENT"
    err.message = "Invalid JSON payload received. Unknown name response_json_schema"

    info = describe_client_error(err)
    assert info == {
        "status": "INVALID_ARGUMENT",
        "reason": "INVALID_ARGUMENT",
        "schema_rejected": True,
        "invalid_argument": True,
    }
    # 값은 전부 사전 정의된 토큰/불리언이다.
    assert all(isinstance(v, (bool, str)) for v in info.values())


def test_400_diagnosis_never_leaks_free_text():
    from src.pipeline.gemini_summary import describe_client_error

    secret = "일급비밀 기사 본문과 프롬프트가 섞인 서버 응답"
    err = FakeAPIError(400, secret, details={"error": {"message": secret}})
    err.status = secret
    err.message = secret

    info = describe_client_error(err)
    blob = " ".join(str(v) for v in info.values())
    assert secret not in blob
    # 안전 토큰 패턴에 맞지 않으면 unknown으로 떨어뜨린다.
    assert info["status"] == "unknown"
    assert info["reason"] == "unknown"


def test_400_diagnosis_marks_non_schema_errors():
    from src.pipeline.gemini_summary import describe_client_error

    err = FakeAPIError(400, "request payload size exceeds the limit")
    err.status = "INVALID_ARGUMENT"
    err.message = "request payload size exceeds the limit"

    info = describe_client_error(err)
    assert info["schema_rejected"] is False
    assert info["invalid_argument"] is True


def test_400_log_line_carries_diagnosis_but_no_prompt_or_body(caplog):
    items = iter_batch_items(
        [("비밀제목", "일급비밀본문 " * 60)], article_max_chars=3000
    )
    err = FakeAPIError(400, "bad", details={"reason": "INVALID_ARGUMENT"})
    err.status = "INVALID_ARGUMENT"
    err.message = "Unknown name response_json_schema"

    summarizer, _ = _make(lambda call_no, prompt, schema: (_ for _ in ()).throw(err))
    with caplog.at_level(logging.DEBUG):
        summarizer.summarize_many(items)

    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "status=INVALID_ARGUMENT" in blob
    assert "schema_rejected=True" in blob
    assert "invalid_argument=True" in blob
    # 프롬프트·본문·제목·키는 없다.
    assert "일급비밀본문" not in blob
    assert "비밀제목" not in blob
    assert "<article" not in blob
    assert API_KEY not in blob
