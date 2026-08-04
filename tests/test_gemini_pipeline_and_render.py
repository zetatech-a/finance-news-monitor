"""Gemini 요약의 파이프라인 통합·캐시·렌더링 회귀 테스트 (네트워크 호출 없음)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src import run_daily
from src.config import KST
from src.pipeline import gemini_summary
from src.pipeline.gemini_cache import (
    CACHE_VERSION,
    cache_key,
    load_gemini_cache,
    save_gemini_cache,
)
from src.pipeline.gemini_summary import GeminiBatchSummarizer, load_gemini_config
from src.pipeline.normalize import Article
from src.pipeline.report import (
    ai_summary_lines,
    render_html,
    render_markdown,
    top_report_items,
    visible_report_items,
)
from src.pipeline.tagger import TaggedArticle

GOOD_LINES = [
    "금융위원회가 대부업 감독 규정을 개정했다고 발표했다.",
    "개정 규정은 2026년 9월 1일 시행되며 대상은 등록 대부업체 900곳이다.",
    "금융위는 시행 후 6개월간 이행 실태를 점검할 계획이다.",
]


def _batch_payload(prompt: str) -> str:
    """프롬프트에 들어온 article id 전부에 3줄 요약을 돌려주는 정상 배치 응답."""
    ids = re.findall(r'<article id="(article-\d+)">', prompt)
    return json.dumps(
        {"summaries": [{"id": i, "lines": list(GOOD_LINES)} for i in ids]},
        ensure_ascii=False,
    )

BODY = "금융위원회는 대부업 감독 규정 개정안을 의결했다고 밝혔다. " * 20
API_KEY = "test-key-not-a-real-credential"


@pytest.fixture(autouse=True)
def _clean_gemini_env(monkeypatch):
    import os

    for name in list(dict(os.environ)):
        if name.startswith("GEMINI_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", API_KEY)
    monkeypatch.setenv("GEMINI_MIN_INTERVAL_SECONDS", "0")


def _item(idx: int, *, sector: str = "대부", description: str = "네이버 스니펫 원본 설명") -> TaggedArticle:
    article = Article(
        title=f"대부업 감독 규정 개정 {idx}",
        description=description,
        link=f"https://example.com/news/{idx}",
        originallink=None,
        naver_link=None,
        pub_date=datetime(2026, 8, 1, 9, 0, tzinfo=KST) - timedelta(minutes=idx),
        query="대부업",
        relevance_score=8,
    )
    return TaggedArticle(article=article, sectors=[sector], topics=["감독·제재"], matched_keywords=[])


def _summarizer(behaviour=None, *, calls=None):
    """behaviour: None이면 전부 정상 응답. 예외를 주면 매 호출마다 raise."""
    recorded = calls if calls is not None else []

    def generate_fn(*, system_instruction, prompt, schema):
        recorded.append(prompt)
        if isinstance(behaviour, BaseException):
            raise behaviour
        if callable(behaviour):
            return behaviour(prompt)
        return _batch_payload(prompt)

    return GeminiBatchSummarizer(
        load_gemini_config(),
        generate_fn=generate_fn,
        sleep_fn=lambda _s: None,
        monotonic_fn=lambda: 0.0,
    )


def _apply(items, tmp_path, summarizer, body_cache=None, priority=None):
    return run_daily.apply_gemini_summaries(
        priority_items=priority if priority is not None else items,
        visible_items=items,
        body_cache=body_cache if body_cache is not None else {},
        cache_path=tmp_path / "gemini_summary_cache.json",
        summarizer=summarizer,
    )


# --- 기본 통합 --------------------------------------------------------------


def test_gemini_lines_are_applied_without_touching_description(tmp_path):
    items = [_item(0)]
    body_cache = {items[0].article.link: BODY}
    applied = _apply(items, tmp_path, _summarizer(), body_cache)

    assert applied == 1
    assert items[0].article.summary_lines == GOOD_LINES
    assert items[0].article.summary_source == "gemini"
    # 분류/랭킹 입력인 description은 그대로다.
    assert items[0].article.description == "네이버 스니펫 원본 설명"


def test_max_summaries_caps_the_number_of_articles(tmp_path):
    import os

    os.environ["GEMINI_MAX_SUMMARIES"] = "2"
    items = [_item(i) for i in range(5)]
    body_cache = {it.article.link: BODY for it in items}
    calls: list[str] = []
    applied = _apply(items, tmp_path, _summarizer(calls=calls), body_cache)

    assert applied == 2
    assert len(calls) == 1  # 2건이 한 배치에 함께 들어간다
    assert [bool(it.article.summary_lines) for it in items] == [True, True, False, False, False]


def test_top_items_are_summarized_first(tmp_path):
    import os

    os.environ["GEMINI_MAX_SUMMARIES"] = "1"
    items = [_item(i) for i in range(3)]
    body_cache = {it.article.link: BODY for it in items}
    _apply(items, tmp_path, _summarizer(), body_cache, priority=[items[2]])

    assert items[2].article.summary_lines == GOOD_LINES
    assert items[0].article.summary_lines == []


# --- fallback ---------------------------------------------------------------


def test_api_failure_keeps_extractive_summary(tmp_path):
    class Boom(Exception):
        code = 500

    items = [_item(0, description="추출요약된 문장입니다. 충분히 긴 요약문입니다.")]
    body_cache = {items[0].article.link: BODY}
    applied = _apply(items, tmp_path, _summarizer(Boom()), body_cache)

    assert applied == 0
    assert items[0].article.summary_lines == []
    assert items[0].article.summary_source is None
    assert items[0].article.description == "추출요약된 문장입니다. 충분히 긴 요약문입니다."


def test_missing_api_key_skips_gemini_entirely(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    items = [_item(0)]
    calls: list[str] = []
    applied = run_daily.apply_gemini_summaries(
        priority_items=items,
        visible_items=items,
        body_cache={items[0].article.link: BODY},
        cache_path=tmp_path / "gemini_summary_cache.json",
        summarizer=_summarizer(calls=calls),
    )
    assert applied == 0
    assert calls == []
    assert items[0].article.summary_lines == []


def test_programming_error_does_not_propagate_out_of_the_pipeline(tmp_path, caplog):
    items = [_item(0)]
    body_cache = {items[0].article.link: BODY}
    # TypeError는 summarizer가 GeminiProgrammingError로 올리고, 파이프라인이 흡수한다.
    applied = _apply(items, tmp_path, _summarizer(TypeError("bad kwargs")), body_cache)
    assert applied == 0
    assert any(record.levelname == "ERROR" for record in caplog.records)


def test_empty_body_is_skipped_without_calling_the_api(tmp_path):
    items = [_item(0)]
    calls: list[str] = []
    applied = _apply(items, tmp_path, _summarizer(calls=calls), {})
    # body_cache에 없고 fetch도 실패/미수행이면 호출하지 않는다.
    assert applied == 0
    assert calls == []


def test_body_is_fetched_within_a_dedicated_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_MAX_FETCH_ATTEMPTS", "1")
    items = [_item(0), _item(1)]
    fetched: list[str] = []

    monkeypatch.setattr(run_daily, "fetch_html", lambda url, timeout=12: fetched.append(url) or "<html/>")
    monkeypatch.setattr(run_daily, "extract_main_text", lambda url, html: BODY)

    applied = _apply(items, tmp_path, _summarizer(), {})
    assert applied == 1
    assert len(fetched) == 1  # 예산 1회를 넘기지 않는다


# --- 캐시 -------------------------------------------------------------------


def test_cache_hit_avoids_the_api(tmp_path):
    items = [_item(0)]
    body_cache = {items[0].article.link: BODY}
    _apply(items, tmp_path, _summarizer(), body_cache)

    fresh = [_item(0)]
    calls: list[str] = []
    applied = _apply(fresh, tmp_path, _summarizer(calls=calls), body_cache)

    assert applied == 1
    assert calls == []
    assert fresh[0].article.summary_lines == GOOD_LINES


@pytest.mark.parametrize("env_name,env_value", [("GEMINI_MODEL", "gemini-3.5-flash")])
def test_model_change_causes_cache_miss(tmp_path, monkeypatch, env_name, env_value):
    items = [_item(0)]
    body_cache = {items[0].article.link: BODY}
    _apply(items, tmp_path, _summarizer(), body_cache)

    monkeypatch.setenv(env_name, env_value)
    fresh = [_item(0)]
    calls: list[str] = []
    _apply(fresh, tmp_path, _summarizer(calls=calls), body_cache)
    assert len(calls) == 1  # 캐시를 재사용하지 않았다


def test_prompt_version_change_causes_cache_miss(tmp_path, monkeypatch):
    items = [_item(0)]
    body_cache = {items[0].article.link: BODY}
    _apply(items, tmp_path, _summarizer(), body_cache)

    monkeypatch.setattr(run_daily, "PROMPT_VERSION", gemini_summary.PROMPT_VERSION + 1)
    fresh = [_item(0)]
    calls: list[str] = []
    _apply(fresh, tmp_path, _summarizer(calls=calls), body_cache)
    assert len(calls) == 1


def test_schema_version_change_causes_cache_miss(tmp_path, monkeypatch):
    items = [_item(0)]
    body_cache = {items[0].article.link: BODY}
    _apply(items, tmp_path, _summarizer(), body_cache)

    monkeypatch.setattr(run_daily, "SCHEMA_VERSION", gemini_summary.SCHEMA_VERSION + 1)
    fresh = [_item(0)]
    calls: list[str] = []
    _apply(fresh, tmp_path, _summarizer(calls=calls), body_cache)
    assert len(calls) == 1


def test_cache_never_stores_article_body_or_prompt(tmp_path):
    items = [_item(0)]
    body_cache = {items[0].article.link: BODY}
    _apply(items, tmp_path, _summarizer(), body_cache)

    raw = (tmp_path / "gemini_summary_cache.json").read_text(encoding="utf-8")
    assert "금융위원회는 대부업 감독 규정 개정안을 의결했다" not in raw
    assert "<article>" not in raw
    assert API_KEY not in raw

    entry = next(iter(json.loads(raw)["entries"].values()))
    assert set(entry) == {
        "url",
        "model",
        "prompt_version",
        "schema_version",
        "lines",
        "created_at",
    }


def test_cache_key_distinguishes_url_model_and_versions():
    base = cache_key("https://a", "m1", 1, 1)
    assert base != cache_key("https://b", "m1", 1, 1)
    assert base != cache_key("https://a", "m2", 1, 1)
    assert base != cache_key("https://a", "m1", 2, 1)
    assert base != cache_key("https://a", "m1", 1, 2)
    assert base == cache_key("https://a", "m1", 1, 1)


def test_corrupt_cache_file_does_not_break_loading(tmp_path):
    path = tmp_path / "gemini_summary_cache.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert load_gemini_cache(path) == {}


def test_corrupt_entry_is_dropped_without_losing_the_rest(tmp_path):
    path = tmp_path / "gemini_summary_cache.json"
    path.write_text(
        json.dumps(
            {
                "version": CACHE_VERSION,
                "entries": {
                    "good": {"lines": GOOD_LINES},
                    "short": {"lines": ["하나"]},
                    "wrong-type": {"lines": "문자열"},
                    "not-a-dict": 12,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cache = load_gemini_cache(path)
    assert set(cache) == {"good"}


def test_cache_write_is_atomic_and_leaves_no_tmp_file(tmp_path):
    path = tmp_path / "gemini_summary_cache.json"
    save_gemini_cache(path, {"k": {"lines": GOOD_LINES}})
    assert path.exists()
    assert not list(tmp_path.glob("*.tmp"))
    assert load_gemini_cache(path)["k"]["lines"] == GOOD_LINES


def test_old_extractive_cache_format_is_untouched(tmp_path):
    """Gemini 캐시는 별도 파일이라 기존 추출요약 캐시 포맷을 건드리지 않는다."""
    from src.pipeline.summary_cache import load_cache, save_cache

    legacy = tmp_path / "summary_cache.json"
    save_cache(legacy, {"https://example.com/a": "기존 추출요약"})

    items = [_item(0)]
    _apply(items, tmp_path, _summarizer(), {items[0].article.link: BODY})

    assert load_cache(legacy) == {"https://example.com/a": "기존 추출요약"}
    assert json.loads(legacy.read_text(encoding="utf-8")) == {
        "https://example.com/a": "기존 추출요약"
    }


# --- 배치 처리량 / 캐시 제외 ------------------------------------------------


def _bulk(count: int, tmp_path):
    items = [_item(i) for i in range(count)]
    body_cache = {it.article.link: BODY for it in items}
    return items, body_cache


@pytest.mark.parametrize("count,expected_calls", [(40, 1), (50, 1), (100, 2), (250, 5)])
def test_display_articles_are_summarized_in_micro_batches(tmp_path, count, expected_calls):
    items, body_cache = _bulk(count, tmp_path)
    calls: list[str] = []
    applied = _apply(items, tmp_path, _summarizer(calls=calls), body_cache)

    assert applied == count
    assert len(calls) == expected_calls
    # 표시 대상 전원이 AI 3줄을 갖는다.
    assert all(len(it.article.summary_lines) == 3 for it in items)


@pytest.mark.parametrize("count", [40, 120, 250])
def test_every_article_ends_with_lines_or_extractive_fallback(tmp_path, count):
    """일부만 성공해도 나머지는 기존 요약이 남아 표시가 비지 않는다."""
    items, body_cache = _bulk(count, tmp_path)

    def half_ok(prompt: str) -> str:
        ids = re.findall(r'<article id="(article-\d+)">', prompt)
        keep = ids[: len(ids) // 2]
        return json.dumps(
            {"summaries": [{"id": i, "lines": list(GOOD_LINES)} for i in keep]},
            ensure_ascii=False,
        )

    _apply(items, tmp_path, _summarizer(half_ok), body_cache)

    for it in items:
        has_lines = len(it.article.summary_lines) == 3
        has_fallback = bool((it.article.description or "").strip())
        assert has_lines or has_fallback


def test_cache_hits_are_excluded_from_the_request(tmp_path):
    """표시 50건 중 43건이 캐시에 있으면 Gemini에는 7건만 보낸다."""
    items, body_cache = _bulk(50, tmp_path)
    # 1차 실행으로 50건 전부 캐시에 채운다.
    _apply(items, tmp_path, _summarizer(), body_cache)

    fresh = [_item(i) for i in range(50)]
    # 43건은 캐시 그대로 두고, 7건은 캐시에서 제거해 miss를 만든다.
    cache_path = tmp_path / "gemini_summary_cache.json"
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    evicted = list(payload["entries"])[:7]
    for key in evicted:
        del payload["entries"][key]
    cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    calls: list[str] = []
    applied = _apply(fresh, tmp_path, _summarizer(calls=calls), body_cache)

    assert applied == 50
    assert len(calls) == 1
    sent_ids = re.findall(r'<article id="(article-\d+)">', calls[0])
    assert len(sent_ids) == 7  # 캐시 hit 43건은 전송되지 않았다


def test_partial_failure_caches_only_the_successful_articles(tmp_path):
    items, body_cache = _bulk(10, tmp_path)

    def drop_three(prompt: str) -> str:
        ids = re.findall(r'<article id="(article-\d+)">', prompt)
        if len(ids) == 10:
            ids = ids[:7]
        return json.dumps(
            {"summaries": [{"id": i, "lines": list(GOOD_LINES)} for i in ids]},
            ensure_ascii=False,
        )

    calls: list[str] = []
    applied = _apply(items, tmp_path, _summarizer(drop_three, calls=calls), body_cache)

    assert applied == 10  # 7건 즉시 + 재요청된 3건
    assert len(calls) == 2
    assert len(re.findall(r'<article id="', calls[1])) == 3  # 실패한 3건만 재요청
    entries = json.loads((tmp_path / "gemini_summary_cache.json").read_text(encoding="utf-8"))
    assert len(entries["entries"]) == 10  # 기사별로 저장(배치 단위 entry 아님)


def test_request_budget_exhaustion_leaves_the_rest_on_extractive(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_MAX_REQUESTS_PER_RUN", "2")
    items, body_cache = _bulk(250, tmp_path)
    calls: list[str] = []
    applied = _apply(items, tmp_path, _summarizer(calls=calls), body_cache)

    assert len(calls) == 2
    assert applied == 100
    remaining = [it for it in items if not it.article.summary_lines]
    assert len(remaining) == 150
    assert all((it.article.description or "").strip() for it in remaining)


def test_no_single_article_request_in_the_normal_path(tmp_path):
    items, body_cache = _bulk(120, tmp_path)
    calls: list[str] = []
    _apply(items, tmp_path, _summarizer(calls=calls), body_cache)

    sizes = [len(re.findall(r'<article id="', c)) for c in calls]
    assert sizes == [50, 50, 20]
    assert 1 not in sizes


def test_body_reused_from_extractive_stage_without_refetching(tmp_path, monkeypatch):
    items, body_cache = _bulk(30, tmp_path)
    fetched: list[str] = []
    monkeypatch.setattr(
        run_daily, "fetch_html", lambda url, timeout=12: fetched.append(url) or "<html/>"
    )
    monkeypatch.setattr(run_daily, "extract_main_text", lambda url, html: BODY)

    _apply(items, tmp_path, _summarizer(), body_cache)
    assert fetched == []  # body_sink에 있는 본문은 다시 받아오지 않는다


def test_short_body_falls_back_to_description_as_input(tmp_path):
    long_description = "추출요약으로 채워진 충분히 긴 설명 문장입니다. " * 12
    items = [_item(0, description=long_description)]
    calls: list[str] = []
    applied = _apply(items, tmp_path, _summarizer(calls=calls), {})

    # 본문 fetch가 없어도 description이 길면 입력 후보로 쓴다.
    assert applied == 1
    assert len(calls) == 1
    assert "추출요약으로 채워진" in calls[0]


# --- 기존 파이프라인 계약 보존 ----------------------------------------------


def test_body_sink_does_not_change_existing_summary_budgets(monkeypatch):
    items = [_item(i) for i in range(5)]
    new_summary = "새로 생성된 요약문입니다. 스물네 자 이상이 되도록 충분히 길게 씁니다."

    monkeypatch.setattr(run_daily, "fetch_html", lambda url, timeout=12: "<html></html>")
    monkeypatch.setattr(run_daily, "extract_main_text", lambda url, html: "본문 텍스트")
    monkeypatch.setattr(
        run_daily,
        "summarize_with_fallback",
        lambda full, *, title, description, max_chars: new_summary,
    )

    sink: dict[str, str] = {}
    summarized, cache_hits, fetch_attempts = run_daily.apply_extractive_summaries(
        items, {}, max_summaries=2, max_fetch_attempts=10, body_sink=sink
    )

    # 기존 예산 의미(성공 2건, 시도 2회)가 그대로다.
    assert (summarized, cache_hits, fetch_attempts) == (2, 0, 2)
    # sink에는 실제로 받아온 본문만 담긴다.
    assert list(sink.values()) == ["본문 텍스트", "본문 텍스트"]


def test_body_sink_is_optional_and_defaults_to_disabled(monkeypatch):
    items = [_item(0)]
    monkeypatch.setattr(run_daily, "fetch_html", lambda url, timeout=12: "<html></html>")
    monkeypatch.setattr(run_daily, "extract_main_text", lambda url, html: "본문")
    monkeypatch.setattr(
        run_daily,
        "summarize_with_fallback",
        lambda full, *, title, description, max_chars: "충분히 긴 요약문입니다. 스물네 자 기준을 넘도록 씁니다.",
    )
    # body_sink 없이 호출해도 기존과 동일하게 동작한다.
    assert run_daily.apply_extractive_summaries(items, {}) == (1, 0, 1)


def test_gemini_does_not_change_classification_or_selection(tmp_path):
    items = [_item(i, sector="대부" if i % 2 == 0 else "저축은행") for i in range(6)]
    body_cache = {it.article.link: BODY for it in items}

    before_visible = [it.article.title for it in visible_report_items(items)]
    before_top = [it.article.title for it in top_report_items(items, limit=10)]
    before_sectors = [list(it.sectors) for it in items]
    before_topics = [list(it.topics) for it in items]
    before_descriptions = [it.article.description for it in items]

    applied = _apply(items, tmp_path, _summarizer(), body_cache)
    assert applied == 6

    assert [it.article.title for it in visible_report_items(items)] == before_visible
    assert [it.article.title for it in top_report_items(items, limit=10)] == before_top
    assert [list(it.sectors) for it in items] == before_sectors
    assert [list(it.topics) for it in items] == before_topics
    assert [it.article.description for it in items] == before_descriptions


def test_article_defaults_keep_summary_fields_empty():
    article = Article(
        title="t",
        description="d",
        link="https://example.com",
        originallink=None,
        naver_link=None,
        pub_date=datetime(2026, 8, 1, tzinfo=KST),
        query="q",
    )
    assert article.summary_lines == []
    assert article.summary_source is None
    # 서로 다른 인스턴스가 같은 리스트를 공유하면 안 된다.
    other = Article(
        title="t2",
        description="d",
        link="https://example.com/2",
        originallink=None,
        naver_link=None,
        pub_date=datetime(2026, 8, 1, tzinfo=KST),
        query="q",
    )
    article.summary_lines.append("x")
    assert other.summary_lines == []


# --- 렌더링 ------------------------------------------------------------------


def _render(items):
    return render_html(datetime(2026, 8, 1, tzinfo=KST), items, [])


def _cards(html: str) -> list[str]:
    """카드 마크업만 잘라낸다 — 인라인된 report.css/report.js/푸터를 오탐하지 않기 위해."""
    return [
        f"<article class='card'{chunk.split('</article>')[0]}</article>"
        for chunk in html.split("<article class='card'")[1:]
    ]


def test_html_renders_three_separate_sentences():
    item = _item(0)
    item.article.summary_lines = list(GOOD_LINES)
    html = _render([item])

    for line in GOOD_LINES:
        assert line in html
    summary_block = html.split("class='summary ai' data-summary>")[1].split("</p>")[0]
    assert summary_block.count("<br>") == 2
    assert summary_block.split("<br>") == GOOD_LINES


def test_html_falls_back_to_description_without_ai_lines():
    item = _item(0, description="추출요약 문장입니다.")
    cards = _cards(_render([item]))
    assert cards
    for card in cards:
        assert "class='summary' data-summary>추출요약 문장입니다.</p>" in card
        assert "summary ai" not in card
        assert "AI 3줄" not in card


def test_html_shows_ai_badge_only_for_ai_summaries():
    plain, ai = _item(0), _item(1)
    ai.article.summary_lines = list(GOOD_LINES)
    cards = _cards(_render([plain, ai]))

    # 같은 기사가 TOP 섹션과 업권 섹션에 각각 렌더되므로 카드 단위로 검사한다.
    ai_cards = [c for c in cards if "AI 3줄" in c]
    plain_cards = [c for c in cards if "AI 3줄" not in c]
    assert ai_cards and plain_cards
    assert all("summary ai" in c for c in ai_cards)
    assert all("summary ai" not in c for c in plain_cards)


def test_html_escapes_model_output():
    item = _item(0)
    item.article.summary_lines = [
        '<script>alert("xss")</script> 금융위가 발표했다.',
        "AT&T와 <b>대부업체</b>가 협약을 맺었다.",
        "브레이크<br>태그도 이스케이프되어야 한다.",
    ]
    html = _render([item])
    summary_block = html.split("class='summary ai' data-summary>")[1].split("</p>")[0]

    # 카드 마크업 안에는 모델이 만든 태그가 절대 살아있지 않아야 한다.
    # (페이지 전체에는 인라인된 report.js의 <script>가 정상적으로 존재한다)
    for card in _cards(html):
        assert "<script>" not in card
        assert "<b>" not in card
    assert "alert(&quot;xss&quot;)" in summary_block
    assert "&lt;script&gt;" in summary_block
    assert "&amp;" in summary_block
    assert "&lt;b&gt;" in summary_block
    # 우리가 넣은 구분자 <br>만 남고, 모델이 만든 <br>은 이스케이프된다.
    assert summary_block.count("<br>") == 2
    assert "&lt;br&gt;" in summary_block


def test_ai_lines_are_searchable_via_data_hay():
    item = _item(0)
    item.article.summary_lines = list(GOOD_LINES)
    html = _render([item])
    hay = html.split("data-hay='")[1].split("'")[0]
    assert "900곳" in hay
    # 원본 description도 계속 검색된다.
    assert "네이버 스니펫 원본 설명" in hay


def test_card_links_and_controls_are_preserved():
    item = _item(0)
    item.article.summary_lines = list(GOOD_LINES)
    html = _render([item])
    assert f"href='{item.article.link}'" in html
    assert "data-clip" in html  # 저장(즐겨찾기) 버튼
    assert 'id="searchInput"' in html and 'id="sortSel"' in html  # 검색/정렬 유지
    assert "data-sector-pill" in html  # 필터 유지


def test_markdown_keeps_all_three_sentences():
    item = _item(0)
    item.article.summary_lines = list(GOOD_LINES)
    md = render_markdown(datetime(2026, 8, 1, tzinfo=KST), [item], [])
    for line in GOOD_LINES:
        assert line.rstrip(".") in md


def test_markdown_falls_back_to_description():
    item = _item(0, description="추출요약 문장입니다.")
    md = render_markdown(datetime(2026, 8, 1, tzinfo=KST), [item], [])
    assert "추출요약 문장입니다." in md


def test_report_notice_describes_ai_summary_and_fallback():
    html = _render([_item(0)])
    assert "AI" in html and "추출식 요약" in html


@pytest.mark.parametrize(
    "bad_lines",
    [[], ["하나", "둘"], ["하나", "둘", "셋", "넷"], ["하나", "", "셋"], ["하나", 2, "셋"]],
)
def test_renderer_rejects_malformed_summary_lines(bad_lines):
    item = _item(0)
    item.article.summary_lines = bad_lines
    assert ai_summary_lines(item.article) == []
    assert "summary ai" not in _render([item])


def test_mobile_css_does_not_clamp_the_ai_summary():
    css = Path(gemini_summary.__file__).resolve().parent / "templates" / "report.css"
    text = css.read_text(encoding="utf-8")

    mobile_block = text.split("@media (max-width:767px){")[1]
    assert ".summary{ -webkit-line-clamp:2;" in mobile_block  # 기존 동작 유지
    ai_rule = mobile_block.split(".summary.ai{")[1].split("}")[0]
    assert "-webkit-line-clamp:none" in ai_rule
    assert "overflow:visible" in ai_rule

    desktop_block = text.split("@media")[0]
    desktop_ai = desktop_block.split(".summary.ai{")[1].split("}")[0]
    assert "-webkit-line-clamp:none" in desktop_ai
