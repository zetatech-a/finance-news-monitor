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
from src.pipeline import report as report_module
from src.pipeline.report import (
    ai_summary_lines,
    display_summary_text,
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


def _batch_payload(prompt: str) -> str:
    """프롬프트에 들어온 article id 전부에 3줄 요약을 돌려주는 정상 배치 응답."""
    ids = re.findall(r'<article id="(article-\d+)">', prompt)
    return json.dumps(
        {"summaries": [_entry(i, list(GOOD_LINES)) for i in ids]},
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


def test_bodies_are_not_fetched_beyond_the_request_capacity(tmp_path, monkeypatch):
    """보낼 수 없는 기사는 크롤링하지 않는다.

    요청 상한이 낮으면(요청 1회 × 배치 2건 = 2건) 나머지 기사의 본문을 아무리 모아도
    전송되지 않고 버려진다 — 12초짜리 fetch를 그만큼 낭비하게 된다.
    """
    monkeypatch.setenv("GEMINI_MAX_REQUESTS_PER_RUN", "1")
    monkeypatch.setenv("GEMINI_BATCH_MAX_ARTICLES", "2")
    monkeypatch.setenv("GEMINI_MAX_FETCH_ATTEMPTS", "300")  # fetch 예산은 넉넉하다

    items = [_item(i) for i in range(6)]
    fetched: list[str] = []
    monkeypatch.setattr(
        run_daily, "fetch_html", lambda url, timeout=12: fetched.append(url) or "<html/>"
    )
    monkeypatch.setattr(run_daily, "extract_main_text", lambda url, html: BODY)

    applied = _apply(items, tmp_path, _summarizer(), {})

    assert len(fetched) == 2  # 전송 가능한 2건만 크롤링한다
    assert applied == 2


def test_fetch_cap_respects_the_input_char_budget(tmp_path, monkeypatch):
    """기사 수만으로 상한을 잡으면 문자 예산 때문에 배치가 일찍 닫히는 설정에서
    여전히 과잉 크롤링이 난다 — 요청 1회 × 배치 25건이라도 문자 예산상 1건뿐이면
    1건만 크롤링해야 한다."""
    from src.pipeline.gemini_summary import (
        estimate_item_chars,
        iter_batch_items,
        prompt_overhead_chars,
    )

    one_item_chars = estimate_item_chars(
        iter_batch_items([("대부업 감독 규정 개정 0", BODY)], article_max_chars=3000)[0]
    )
    monkeypatch.setenv("GEMINI_MAX_REQUESTS_PER_RUN", "1")
    monkeypatch.setenv("GEMINI_BATCH_MAX_ARTICLES", "25")
    monkeypatch.setenv(
        "GEMINI_BATCH_MAX_INPUT_CHARS", str(prompt_overhead_chars() + one_item_chars)
    )

    items = [_item(i) for i in range(10)]
    fetched: list[str] = []
    monkeypatch.setattr(
        run_daily, "fetch_html", lambda url, timeout=12: fetched.append(url) or "<html/>"
    )
    monkeypatch.setattr(run_daily, "extract_main_text", lambda url, html: BODY)

    applied = _apply(items, tmp_path, _summarizer(), {})

    assert len(fetched) == 1  # 배치 25건 설정이어도 문자 예산상 1건만 보낼 수 있다
    assert applied == 1


def test_fetch_stops_once_actual_sizes_fill_the_request_budget(tmp_path, monkeypatch):
    """최소 크기 가정으로 통과했더라도 실제 크기로 담을 수 없으면 준비를 멈춘다.

    여기서 계속 준비하면 요청 예산에서 어차피 버려질 기사를 계속 크롤링한다.
    """
    from src.pipeline.gemini_summary import (
        estimate_item_chars,
        iter_batch_items,
        prompt_overhead_chars,
    )

    one_item = iter_batch_items([("대부업 감독 규정 개정 0", BODY)], article_max_chars=3000)[0]
    item_chars = estimate_item_chars(one_item)
    monkeypatch.setenv("GEMINI_MAX_REQUESTS_PER_RUN", "1")
    monkeypatch.setenv("GEMINI_BATCH_MAX_ARTICLES", "25")
    # 1건 + 최소 크기(약 276자) 자리가 남는 예산 — has_room()은 낙관적으로 통과한다.
    monkeypatch.setenv(
        "GEMINI_BATCH_MAX_INPUT_CHARS", str(prompt_overhead_chars() + item_chars + 400)
    )

    items = [_item(i) for i in range(6)]
    fetched: list[str] = []
    monkeypatch.setattr(
        run_daily, "fetch_html", lambda url, timeout=12: fetched.append(url) or "<html/>"
    )
    monkeypatch.setattr(run_daily, "extract_main_text", lambda url, html: BODY)

    applied = _apply(items, tmp_path, _summarizer(), {})

    # 2건째에서 실제 크기로 실패 → 그 뒤로는 크롤링하지 않는다(최대 1건 낭비).
    assert len(fetched) == 2
    assert applied == 1


def test_cached_articles_still_apply_past_the_request_capacity(tmp_path, monkeypatch):
    """용량 초과로 건너뛰는 것은 '본문 수집'뿐이다 — 캐시 hit은 그대로 적용된다."""
    warm = [_item(5)]
    body_cache = {warm[0].article.link: BODY}
    _apply(warm, tmp_path, _summarizer(), body_cache)

    monkeypatch.setenv("GEMINI_MAX_REQUESTS_PER_RUN", "1")
    monkeypatch.setenv("GEMINI_BATCH_MAX_ARTICLES", "1")
    monkeypatch.setattr(run_daily, "fetch_html", lambda url, timeout=12: "<html/>")
    monkeypatch.setattr(run_daily, "extract_main_text", lambda url, html: BODY)

    # 앞의 2건은 cache miss(1건만 전송 가능), 마지막 1건은 cache hit이다.
    items = [_item(0), _item(1), _item(5)]
    applied = _apply(items, tmp_path, _summarizer(), {})

    assert items[2].article.summary_lines == GOOD_LINES  # 캐시 hit은 살아남는다
    assert applied == 2  # 캐시 1건 + 새로 요약한 1건


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


def test_cache_hit_is_revalidated_against_the_current_line_limit(tmp_path, monkeypatch):
    """GEMINI_MAX_LINE_CHARS를 낮추면 캐시 hit도 지금 기준으로 다시 검증한다.

    한도는 캐시 키에 들어가지 않으므로, 재검증이 없으면 "같은 응답이 지금은 거부되는데
    캐시만 통과"하는 상태가 된다.
    """
    items = [_item(0)]
    body_cache = {items[0].article.link: BODY}
    _apply(items, tmp_path, _summarizer(), body_cache)

    # GOOD_LINES는 40자를 넘는 줄을 포함한다 — 한도를 낮추면 캐시 항목이 무효가 된다.
    assert max(len(line) for line in GOOD_LINES) > 40
    monkeypatch.setenv("GEMINI_MAX_LINE_CHARS", "40")

    fresh = [_item(0)]
    calls: list[str] = []
    _apply(fresh, tmp_path, _summarizer(calls=calls), body_cache)
    assert len(calls) == 1  # 캐시를 그대로 쓰지 않고 다시 요약했다


def test_cache_hit_is_reused_when_the_line_limit_still_allows_it(tmp_path, monkeypatch):
    items = [_item(0)]
    body_cache = {items[0].article.link: BODY}
    _apply(items, tmp_path, _summarizer(), body_cache)

    monkeypatch.setenv("GEMINI_MAX_LINE_CHARS", "90")
    fresh = [_item(0)]
    calls: list[str] = []
    applied = _apply(fresh, tmp_path, _summarizer(calls=calls), body_cache)

    assert applied == 1
    assert calls == []
    assert fresh[0].article.summary_lines == GOOD_LINES


def test_snippet_fallback_summaries_are_not_cached(tmp_path, monkeypatch):
    """본문 크롤링이 실패해 스니펫으로 만든 요약은 캐시하지 않는다.

    캐시 키에는 입력 출처가 없다 — 저품질 입력으로 만든 요약이 박히면, 나중에 본문을
    정상적으로 받는 실행에서도 그 항목이 hit되어 신선도 상한까지 재생성되지 않는다.
    """
    long_description = "추출요약 문장입니다. " * 30
    items = [_item(0, description=long_description)]

    def _boom(url, timeout=12):
        raise RuntimeError("fetch failed")

    monkeypatch.setattr(run_daily, "fetch_html", _boom)

    calls: list[str] = []
    applied = _apply(items, tmp_path, _summarizer(calls=calls), {})
    assert applied == 1  # 스니펫으로라도 요약은 만든다(표시는 정상)
    assert len(calls) == 1
    assert items[0].article.summary_lines == GOOD_LINES

    cache_file = tmp_path / "gemini_summary_cache.json"
    assert not cache_file.exists() or json.loads(cache_file.read_text(encoding="utf-8"))[
        "entries"
    ] == {}

    # 다음 실행에서 본문을 정상적으로 받으면 캐시가 아니라 본문으로 다시 요약한다.
    monkeypatch.setattr(run_daily, "fetch_html", lambda url, timeout=12: "<html/>")
    monkeypatch.setattr(run_daily, "extract_main_text", lambda url, html: BODY)
    fresh_calls: list[str] = []
    _apply([_item(0, description=long_description)], tmp_path, _summarizer(calls=fresh_calls), {})
    assert len(fresh_calls) == 1


def test_full_text_summaries_are_still_cached(tmp_path):
    items = [_item(0)]
    body_cache = {items[0].article.link: BODY}
    _apply(items, tmp_path, _summarizer(), body_cache)

    entries = json.loads(
        (tmp_path / "gemini_summary_cache.json").read_text(encoding="utf-8")
    )["entries"]
    assert len(entries) == 1


def test_corrected_title_at_the_same_url_causes_a_cache_miss(tmp_path):
    """같은 URL에서 기사가 정정되면 옛 요약을 그대로 쓰면 안 된다."""
    items = [_item(0)]
    body_cache = {items[0].article.link: BODY}
    _apply(items, tmp_path, _summarizer(), body_cache)

    corrected = _item(0)
    corrected.article.title = "[정정] " + corrected.article.title
    calls: list[str] = []
    _apply([corrected], tmp_path, _summarizer(calls=calls), body_cache)
    assert len(calls) == 1  # 제목이 바뀌었으니 다시 요약한다


def test_stale_cache_entries_are_not_reused(tmp_path, monkeypatch):
    """제목 그대로 본문만 정정된 기사는 fingerprint로 못 잡는다 — 신선도로 막는다."""
    from src.pipeline import gemini_cache

    items = [_item(0)]
    body_cache = {items[0].article.link: BODY}
    _apply(items, tmp_path, _summarizer(), body_cache)

    # 저장 시각을 신선도 상한보다 오래된 것으로 바꾼다.
    path = tmp_path / "gemini_summary_cache.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    stale = (datetime(2026, 8, 1, tzinfo=KST) - timedelta(days=gemini_cache.MAX_AGE_DAYS + 1))
    for entry in payload["entries"].values():
        entry["created_at"] = stale.isoformat()
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(run_daily, "now_kst", lambda: datetime(2026, 8, 1, tzinfo=KST))
    calls: list[str] = []
    _apply([_item(0)], tmp_path, _summarizer(calls=calls), body_cache)
    assert len(calls) == 1  # 오래된 요약은 버리고 다시 만든다


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


@pytest.mark.parametrize("count,expected_calls", [(25, 1), (40, 2), (50, 2), (100, 4), (250, 10)])
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
            {"summaries": [_entry(i, list(GOOD_LINES)) for i in keep]},
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
            {"summaries": [_entry(i, list(GOOD_LINES)) for i in ids]},
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
    assert applied == 50  # 배치 25 × 2회
    remaining = [it for it in items if not it.article.summary_lines]
    assert len(remaining) == 200
    assert all((it.article.description or "").strip() for it in remaining)


def test_no_single_article_request_in_the_normal_path(tmp_path):
    items, body_cache = _bulk(120, tmp_path)
    calls: list[str] = []
    _apply(items, tmp_path, _summarizer(calls=calls), body_cache)

    sizes = [len(re.findall(r'<article id="', c)) for c in calls]
    assert sizes == [25, 25, 25, 25, 20]
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


def _ai_items(html: str) -> list[str]:
    """AI 요약 패널의 <li> 내용."""
    block = html.split("class='summary-panel__list' data-summary>")[1].split("</ul>")[0]
    return [chunk.split("</li>")[0] for chunk in block.split("<li>")[1:]]


def test_html_renders_three_separate_list_items():
    item = _item(0)
    item.article.summary_lines = list(GOOD_LINES)
    html = _render([item])

    for line in GOOD_LINES:
        assert line in html
    # 세 문장은 실제 <ul><li> 구조로 렌더된다(<br> 연결 아님).
    assert _ai_items(html) == GOOD_LINES
    for card in _cards(html):
        assert card.count("<li>") == 3
        assert "summary-panel--ai" in card
        assert "summary-panel--preview" not in card
        assert "AI 핵심 요약" in card
        # 세 문장에 clamp가 붙지 않도록 미리보기용 클래스를 재사용하지 않는다.
        assert "class='summary summary-panel__text'" not in card


def test_html_falls_back_to_description_without_ai_lines():
    item = _item(0, description="추출요약 문장입니다.")
    cards = _cards(_render([item]))
    assert cards
    for card in cards:
        # 기존 clamp 정책을 그대로 쓰도록 'summary' 클래스가 유지된다.
        assert (
            "class='summary summary-panel__text' data-summary>추출요약 문장입니다.</p>" in card
        )
        assert "summary-panel--preview" in card
        assert "summary-panel--ai" not in card
        assert "기사 미리보기" in card
        assert "AI 요약 제외" not in card
        assert "AI 3줄" not in card
        assert "AI 핵심 요약" not in card


def test_html_uses_the_ai_panel_only_for_ai_summaries():
    plain, ai = _item(0), _item(1)
    ai.article.summary_lines = list(GOOD_LINES)
    cards = _cards(_render([plain, ai]))

    # 같은 기사가 TOP 섹션과 업권 섹션에 각각 렌더되므로 카드 단위로 검사한다.
    ai_cards = [c for c in cards if "AI 핵심 요약" in c]
    plain_cards = [c for c in cards if "AI 핵심 요약" not in c]
    assert ai_cards and plain_cards
    assert all("summary-panel--ai" in c and "summary-panel--preview" not in c for c in ai_cards)
    assert all(
        "summary-panel--preview" in c and "summary-panel--ai" not in c for c in plain_cards
    )
    # Top 10 카드와 업권별 카드가 같은 helper로 렌더된다.
    assert len(ai_cards) == 2 and len(plain_cards) == 2


def test_ai_meta_badge_is_removed_in_favor_of_the_panel_title():
    item = _item(0)
    item.article.summary_lines = list(GOOD_LINES)
    for card in _cards(_render([item])):
        assert "<span class='badge'>AI 3줄</span>" not in card
        assert "AI 핵심 요약" in card


def test_cache_badge_is_preserved():
    item = _item(0)
    item.article.summary_lines = list(GOOD_LINES)
    item.article.summary_cached = True
    for card in _cards(_render([item])):
        assert "⚡ 캐시" in card


def test_html_escapes_model_output():
    item = _item(0)
    item.article.summary_lines = [
        '<script>alert("xss")</script> 금융위가 발표했다.',
        "AT&T와 <b>대부업체</b>가 협약을 맺었다.",
        "브레이크<br>태그도 이스케이프되어야 한다.",
    ]
    html = _render([item])
    items = _ai_items(html)
    assert len(items) == 3
    summary_block = "".join(items)

    # 카드 마크업 안에는 모델이 만든 태그가 절대 살아있지 않아야 한다.
    # (페이지 전체에는 인라인된 report.js의 <script>가 정상적으로 존재한다)
    for card in _cards(html):
        assert "<script>" not in card
        assert "<b>" not in card
        assert "<br>" not in card
    assert "alert(&quot;xss&quot;)" in summary_block
    assert "&lt;script&gt;" in summary_block
    assert "&amp;" in summary_block
    assert "&lt;b&gt;" in summary_block
    # 모델이 만든 <br>은 이스케이프되고, 줄 구분은 <li>가 담당한다.
    assert "&lt;br&gt;" in summary_block


@pytest.mark.parametrize(
    "rejection_reason", ["title_body_mismatch", "multi_topic", "insufficient_content"]
)
def test_content_rejected_card_shows_a_neutral_preview_panel(tmp_path, rejection_reason):
    items = [_polluted(0)]
    body_cache = {items[0].article.link: BODY}
    _apply(items, tmp_path, _summarizer(_gate({0: rejection_reason})), body_cache)

    cards = [c for c in _cards(_render(items)) if "대부업 감독 규정 개정 0" in c]
    assert cards
    for card in cards:
        assert "기사 미리보기" in card
        assert "AI 요약 제외" in card
        assert "summary-panel--preview" in card
        assert "summary-panel--ai" not in card
        assert "AI 핵심 요약" not in card
        assert "<li>" not in card  # AI 목록이 없다
        # 내부 사유 문자열은 절대 노출되지 않는다.
        for reason in ("title_body_mismatch", "multi_topic", "insufficient_content"):
            assert reason not in card
        # 도움말은 native title + 스크린리더용 보조 텍스트로만 제공한다.
        assert report_module.CONTENT_REJECTED_HELP in card
        assert "sr-only" in card


def test_general_fallback_card_has_no_rejection_status():
    item = _item(0, description="추출요약 문장입니다.")
    for card in _cards(_render([item])):
        assert "기사 미리보기" in card
        assert "AI 요약 제외" not in card
        assert "summary-panel__status" not in card


def test_summary_state_helper_covers_the_three_states():
    plain = _item(0, description="추출요약 문장입니다.").article
    assert report_module.summary_state(plain) == "preview"

    rejected = _item(1, description="추출요약 문장입니다.").article
    rejected.summary_rejection_reason = "multi_topic"
    assert report_module.summary_state(rejected) == "content_rejected"

    ai = _item(2).article
    ai.summary_lines = list(GOOD_LINES)
    ai.summary_rejection_reason = "multi_topic"  # AI 3줄이 있으면 ai가 이긴다
    assert report_module.summary_state(ai) == "ai"


def test_ai_lines_are_searchable_via_data_hay():
    item = _item(0)
    item.article.summary_lines = list(GOOD_LINES)
    html = _render([item])
    hay = html.split("data-hay='")[1].split("'")[0]
    assert "900곳" in hay
    # 원본 description도 계속 검색된다.
    assert "네이버 스니펫 원본 설명" in hay
    # 표시용 label은 검색 결과를 오염시키지 않는다.
    for label in ("AI 핵심 요약", "기사 미리보기", "AI 요약 제외"):
        assert label not in hay


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


def test_markdown_keeps_long_ai_summaries_intact():
    """GEMINI_MAX_LINE_CHARS를 올리면 3줄 합계가 300자를 넘는다.

    마크다운에만 고정 한도를 걸면 세 번째 문장이 사라진 채 저장되어 HTML 리포트와
    내용이 어긋난다. AI 요약은 이미 계약(3줄 × 줄당 상한)으로 묶여 있으므로 자르지 않는다.
    """
    long_lines = [
        "금융위원회가 대부업 감독 규정 개정안을 의결하며 최고금리 산정 방식과 등록 요건을 함께 손질했다고 " + "자세히 " * 12 + "밝혔다.",
        "개정 규정은 2026년 9월 1일부터 시행되며 적용 대상은 등록 대부업체 900곳과 대부중개업체 " + "다수의 " * 12 + "1200곳이다.",
        "금융위는 시행 후 6개월간 이행 실태를 점검하고 위반 업체에는 등록 취소를 포함한 " + "강력한 " * 12 + "제재를 예고했다.",
    ]
    assert sum(len(line) for line in long_lines) > 300
    item = _item(0)
    item.article.summary_lines = list(long_lines)

    md = render_markdown(datetime(2026, 8, 1, tzinfo=KST), [item], [])
    for line in long_lines:
        assert line.rstrip(".") in md
    assert "..." not in md


def test_markdown_still_truncates_long_extractive_summaries():
    """길이가 보장되지 않는 추출요약·스니펫은 그대로 잘라 쓴다."""
    item = _item(0, description="추출요약 문장입니다. " * 40)
    md = render_markdown(datetime(2026, 8, 1, tzinfo=KST), [item], [])
    assert "..." in md


def test_markdown_falls_back_to_description():
    item = _item(0, description="추출요약 문장입니다.")
    md = render_markdown(datetime(2026, 8, 1, tzinfo=KST), [item], [])
    assert "추출요약 문장입니다." in md


def test_markdown_labels_ai_summaries_with_a_nested_bullet_list():
    item = _item(0)
    item.article.summary_lines = list(GOOD_LINES)
    md = render_markdown(datetime(2026, 8, 1, tzinfo=KST), [item], [])

    assert "**AI 핵심 요약**" in md
    for line in GOOD_LINES:
        assert f"    - {line}" in md
    assert "**기사 미리보기**" not in md


def test_markdown_ai_bullets_stay_nested_when_converted_to_html():
    """write_report의 마크다운→HTML fallback 경로에서 중첩 목록이 유지되는지."""
    import markdown as markdown_lib

    item = _item(0)
    item.article.summary_lines = list(GOOD_LINES)
    md = render_markdown(datetime(2026, 8, 1, tzinfo=KST), [item], [])
    html = markdown_lib.markdown(md, extensions=["tables"], output_format="html5")

    # 3문장이 기사 항목 안의 중첩 <ul>로 들어간다(형제 항목으로 펴지지 않는다).
    assert "<strong>AI 핵심 요약</strong><ul>" in html
    for line in GOOD_LINES:
        assert f"<li>{line}</li>" in html


def test_markdown_labels_content_rejection():
    item = _item(0, description="추출요약 문장입니다. 충분히 긴 설명입니다.")
    item.article.source_description = SOURCE_SNIPPET
    item.article.summary_rejection_reason = "multi_topic"
    md = render_markdown(datetime(2026, 8, 1, tzinfo=KST), [item], [])

    assert f"**{'기사 미리보기'} · AI 요약 제외**" in md
    assert SOURCE_SNIPPET in md
    assert "multi_topic" not in md
    assert "**AI 핵심 요약**" not in md


def test_markdown_labels_the_general_fallback():
    item = _item(0, description="추출요약 문장입니다.")
    md = render_markdown(datetime(2026, 8, 1, tzinfo=KST), [item], [])

    assert "**기사 미리보기** 추출요약 문장입니다." in md
    assert "AI 요약 제외" not in md
    assert "**AI 핵심 요약**" not in md


# --- 사용자 안내 문구의 정확성 ------------------------------------------------
#
# fallback 원천은 상태에 따라 다르다 — 내용 거부는 `source_description`(네이버 원본
# 스니펫), 일반 API 실패는 `description`(추출요약일 수도, 스니펫일 수도 있다).
# 안내 문구가 "AI 실패 시 기존 추출식 요약"이라고 단정하면 실제 동작과 어긋난다.

# 특정 fallback 원천이나 실패 원인을 단정하는 표현들.
INACCURATE_NOTICE_PHRASES = ("추출식 요약", "AI 처리가 실패하면", "AI 처리가 실패")


def test_report_notice_describes_ai_summary_and_fallback():
    html = _render([_item(0)])
    assert "AI 핵심 요약" in html
    assert "기사 미리보기" in html
    for phrase in INACCURATE_NOTICE_PHRASES:
        assert phrase not in html, phrase


def test_markdown_fallback_html_notice_matches_actual_behaviour(tmp_path):
    """write_report의 마크다운→HTML fallback 경로(html_override 없음)도 같은 안내를 쓴다."""
    from src.pipeline.report import write_report

    report_date = datetime(2026, 8, 1, tzinfo=KST)
    md = render_markdown(report_date, [_item(0)], [])
    paths = write_report(report_date, md, tmp_path)
    html = paths["html"].read_text(encoding="utf-8")

    assert "AI 핵심 요약" in html
    assert "기사 미리보기" in html
    for phrase in INACCURATE_NOTICE_PHRASES:
        assert phrase not in html, phrase


def test_content_rejection_help_is_not_premised_on_specific_reasons():
    """도움말은 세 사유를 모두 포괄해야 한다.

    '복합 기사이거나 제목과 본문의 일치도가 낮아' 같은 문구는 multi_topic과
    title_body_mismatch만 설명하고 insufficient_content(본문 품질 부족)를 빼놓는다.
    """
    help_text = report_module.CONTENT_REJECTED_HELP

    assert "복합 기사" not in help_text
    assert "일치도" not in help_text
    # 기사 구조 문제와 본문 품질 문제를 함께 아우른다.
    assert "구조" in help_text and "본문 품질" in help_text
    # 내부 enum 이름은 도움말 자체에도 들어가지 않는다.
    for reason in ("title_body_mismatch", "multi_topic", "insufficient_content"):
        assert reason not in help_text


@pytest.mark.parametrize(
    "bad_lines",
    [[], ["하나", "둘"], ["하나", "둘", "셋", "넷"], ["하나", "", "셋"], ["하나", 2, "셋"]],
)
def test_renderer_rejects_malformed_summary_lines(bad_lines):
    item = _item(0)
    item.article.summary_lines = bad_lines
    assert ai_summary_lines(item.article) == []
    cards = _cards(_render([item]))
    assert cards
    for card in cards:
        assert "summary-panel--ai" not in card
        assert "summary-panel--preview" in card


def _report_css() -> str:
    css = Path(gemini_summary.__file__).resolve().parent / "templates" / "report.css"
    return css.read_text(encoding="utf-8")


def _mobile_css_block(text: str) -> str:
    """모바일 전용 블록.

    breakpoint 픽셀 값은 레이아웃 작업에서 바뀔 수 있으므로 리터럴로 찾지 않고
    `max-width`가 가장 작은 미디어 블록(=모바일)을 고른다. 중첩 규칙은 들여쓰기된
    닫는 괄호를 쓰므로 열 0의 `}`로 블록 끝을 잡는다.
    """
    blocks = re.findall(r"@media \(max-width:(\d+)px\)\s*\{(.*?)\n\}", text, re.S)
    assert blocks, "모바일 미디어 쿼리 블록이 없다"
    return min(blocks, key=lambda block: int(block[0]))[1]


def test_css_never_clamps_the_ai_summary_list():
    text = _report_css()

    # AI 목록에는 어떤 뷰포트에서도 line-clamp가 걸리지 않는다.
    for chunk in text.split(".summary-panel__list")[1:]:
        rule = chunk.split("}")[0]
        assert "line-clamp" not in rule

    # 미리보기 문단의 기존 clamp 정책(데스크톱 3줄 / 모바일 2줄)은 유지된다.
    assert ".summary{ -webkit-line-clamp:2;" in _mobile_css_block(text)
    # 좁은 화면 override 앞쪽(기본 규칙)에서 데스크톱 clamp를 찾는다.
    # (다른 미디어 블록이 사이에 추가돼도 기본 규칙 판정이 흔들리지 않게 max-width만 잘라낸다)
    desktop_summary = re.split(r"@media \(max-width", text)[0].split(".summary{")[1].split("}")[0]
    assert "-webkit-line-clamp:3" in desktop_summary


def test_css_defines_summary_panel_variables_for_both_themes():
    text = _report_css()
    light = text.split(":root{")[1].split("}")[0]
    dark = text.split('html[data-theme="dark"]{')[1].split("}")[0]
    for name in (
        "--summary-accent",
        "--summary-ai-bg",
        "--summary-ai-border",
        "--summary-preview-bg",
        "--summary-preview-border",
    ):
        assert name in light, name
        assert name in dark, name


def test_css_keeps_long_sentences_inside_the_card():
    text = _report_css()
    assert "overflow-wrap:anywhere" in text
    mobile_block = _mobile_css_block(text)
    # 모바일에서는 패널 padding과 목록 들여쓰기를 줄인다.
    assert ".summary-panel{ padding:" in mobile_block
    assert ".summary-panel__list{ padding-left:" in mobile_block


# --- 내용 품질 게이트: 파이프라인 동작 ----------------------------------------


def _gate(unusable_indexes: dict[int, str]):
    """지정한 순번의 기사만 usable=false로 답하는 responder."""

    def behaviour(prompt: str) -> str:
        ids = re.findall(r'<article id="(article-\d+)">', prompt)
        summaries = []
        for n, article_id in enumerate(ids):
            index = int(article_id.split("-")[1]) - 1
            if index in unusable_indexes:
                summaries.append(_unusable(article_id, unusable_indexes[index]))
            else:
                summaries.append(_entry(article_id, list(GOOD_LINES)))
        return json.dumps({"summaries": summaries}, ensure_ascii=False)

    return behaviour


def test_unusable_article_falls_back_to_extractive_summary(tmp_path):
    items = [
        _item(0, description="추출요약 문장입니다. 충분히 긴 설명입니다."),
        _item(1),
    ]
    body_cache = {it.article.link: BODY for it in items}
    applied = _apply(items, tmp_path, _summarizer(_gate({0: "title_body_mismatch"})), body_cache)

    assert applied == 1
    # unusable 기사는 AI 요약 없이 기존 추출요약이 그대로 표시된다.
    assert items[0].article.summary_lines == []
    assert items[0].article.summary_source is None
    assert items[0].article.description == "추출요약 문장입니다. 충분히 긴 설명입니다."
    # 같은 배치의 정상 기사는 그대로 적용된다.
    assert items[1].article.summary_lines == GOOD_LINES


def test_unusable_article_is_not_cached(tmp_path):
    items = [_item(0), _item(1)]
    body_cache = {it.article.link: BODY for it in items}
    _apply(items, tmp_path, _summarizer(_gate({0: "multi_topic"})), body_cache)

    payload = json.loads((tmp_path / "gemini_summary_cache.json").read_text(encoding="utf-8"))
    assert len(payload["entries"]) == 1  # 정상 1건만 저장
    cached_urls = {e["url"] for e in payload["entries"].values()}
    assert items[0].article.link not in cached_urls
    assert items[1].article.link in cached_urls


def test_unusable_article_is_retried_on_the_next_run_not_within_the_run(tmp_path):
    """캐시에 없으니 다음 실행에서는 다시 시도되지만, 이번 실행 중엔 재요청 없다."""
    items = [_item(0)]
    body_cache = {items[0].article.link: BODY}
    calls: list[str] = []
    _apply(items, tmp_path, _summarizer(_gate({0: "multi_topic"}), calls=calls), body_cache)
    assert len(calls) == 1

    fresh = [_item(0)]
    calls2: list[str] = []
    applied = _apply(fresh, tmp_path, _summarizer(calls=calls2), body_cache)
    assert len(calls2) == 1  # 캐시 hit이 아니므로 다시 물어본다
    assert applied == 1
    assert fresh[0].article.summary_lines == GOOD_LINES


def test_html_falls_back_for_unusable_articles(tmp_path):
    items = [
        _item(0, description="추출요약으로 남는 문장입니다."),
        _item(1),
    ]
    body_cache = {it.article.link: BODY for it in items}
    _apply(items, tmp_path, _summarizer(_gate({0: "title_body_mismatch"})), body_cache)

    cards = _cards(_render(items))
    unusable_cards = [c for c in cards if "대부업 감독 규정 개정 0" in c]
    assert unusable_cards
    for card in unusable_cards:
        assert "summary-panel--ai" not in card
        assert "AI 핵심 요약" not in card
        assert "추출요약으로 남는 문장입니다." in card


def test_run_summary_log_reports_content_rejection_counters(tmp_path, caplog):
    import logging

    items = [_item(i) for i in range(6)]
    body_cache = {it.article.link: BODY for it in items}
    gate = _gate({0: "title_body_mismatch", 1: "multi_topic", 2: "insufficient_content"})

    with caplog.at_level(logging.INFO):
        _apply(items, tmp_path, _summarizer(gate), body_cache)

    summary = [m for m in caplog.messages if m.startswith("Gemini run summary:")]
    assert len(summary) == 1
    line = summary[0]
    assert "content_rejected=3" in line
    assert "title_body_mismatch=1" in line
    assert "multi_topic=1" in line
    assert "insufficient_content=1" in line
    assert "gemini_applied=3" in line
    assert "extractive_fallback=3" in line
    # 내용 거부는 오류가 아니다.
    assert "api_errors=0" in line
    assert "items_rejected=0" in line
    # 제목·본문·URL은 로그에 없다.
    assert "대부업 감독 규정 개정" not in line
    assert "example.com" not in line
    assert "금융위원회는" not in line


# --- 내용 거부 시 원본 스니펫 fallback ----------------------------------------
#
# usable=false 기사의 현재 description은 "오염된 크롤링 본문"에서 만든 추출요약일 수
# 있다. 그런 기사에는 네이버 API 원본 스니펫을 대신 보여준다.

SOURCE_SNIPPET = "금융위원회가 대부업 최고금리 산정 방식을 개편한다고 밝혔다."
POLLUTED_EXTRACTIVE = "화물차 단속 독자 제보 동성결혼 인기기사 목록이 섞인 추출요약."


def _polluted(idx: int) -> TaggedArticle:
    """추출요약이 오염된 상태의 기사 — 원본 스니펫은 따로 보존돼 있다."""
    item = _item(idx, description=POLLUTED_EXTRACTIVE)
    item.article.source_description = SOURCE_SNIPPET
    return item


@pytest.mark.parametrize(
    "reason", ["title_body_mismatch", "multi_topic", "insufficient_content"]
)
def test_content_rejection_shows_the_original_naver_snippet(tmp_path, reason):
    items = [_polluted(0)]
    body_cache = {items[0].article.link: BODY}
    _apply(items, tmp_path, _summarizer(_gate({0: reason})), body_cache)

    article = items[0].article
    assert article.summary_rejection_reason == reason
    assert article.summary_lines == []
    # 분류 입력인 description은 그대로 둔다.
    assert article.description == POLLUTED_EXTRACTIVE
    # 표시만 원본 스니펫으로 되돌린다.
    assert display_summary_text(article) == SOURCE_SNIPPET

    cards = [c for c in _cards(_render(items)) if "대부업 감독 규정 개정 0" in c]
    assert cards
    for card in cards:
        assert SOURCE_SNIPPET in card
        assert POLLUTED_EXTRACTIVE not in card
        assert "AI 핵심 요약" not in card
        assert "summary-panel--ai" not in card


def test_content_rejection_without_source_description_keeps_description(tmp_path):
    items = [_item(0, description="추출요약만 남아 있는 충분히 긴 문장입니다.")]
    items[0].article.source_description = ""
    body_cache = {items[0].article.link: BODY}
    _apply(items, tmp_path, _summarizer(_gate({0: "multi_topic"})), body_cache)

    assert display_summary_text(items[0].article) == "추출요약만 남아 있는 충분히 긴 문장입니다."


def test_content_rejection_with_too_short_source_description_keeps_description(tmp_path):
    items = [_item(0, description="추출요약만 남아 있는 충분히 긴 문장입니다.")]
    items[0].article.source_description = "짧음"  # MIN_SOURCE_DESCRIPTION_CHARS 미만
    body_cache = {items[0].article.link: BODY}
    _apply(items, tmp_path, _summarizer(_gate({0: "title_body_mismatch"})), body_cache)

    assert display_summary_text(items[0].article) == "추출요약만 남아 있는 충분히 긴 문장입니다."


@pytest.mark.parametrize("code", [429, 500])
def test_general_api_failure_keeps_the_existing_description(tmp_path, code):
    """일반 API 장애는 내용 거부가 아니다 — 기존 description을 그대로 쓴다."""

    class Boom(Exception):
        pass

    err = Boom()
    err.code = code
    items = [_polluted(0)]
    body_cache = {items[0].article.link: BODY}
    _apply(items, tmp_path, _summarizer(err), body_cache)

    article = items[0].article
    assert article.summary_rejection_reason is None
    assert display_summary_text(article) == POLLUTED_EXTRACTIVE


def test_source_description_survives_the_extractive_summary_stage(monkeypatch):
    """추출요약이 description을 덮어써도 원본 스니펫은 그대로 남는다."""
    from src.pipeline.normalize import normalize

    articles = normalize(
        [
            {
                "title": "제목",
                "description": SOURCE_SNIPPET,
                "link": "https://example.com/a",
                "originallink": None,
                "naver_link": None,
                "pubDate": datetime(2026, 8, 1, tzinfo=KST),
                "query": "q",
            }
        ]
    )
    assert articles[0].description == SOURCE_SNIPPET
    assert articles[0].source_description == SOURCE_SNIPPET

    tagged = [
        TaggedArticle(article=articles[0], sectors=["대부"], topics=[], matched_keywords=[])
    ]
    monkeypatch.setattr(run_daily, "fetch_html", lambda url, timeout=12: "<html/>")
    monkeypatch.setattr(run_daily, "extract_main_text", lambda url, html: "본문")
    monkeypatch.setattr(
        run_daily,
        "summarize_with_fallback",
        lambda full, *, title, description, max_chars: POLLUTED_EXTRACTIVE,
    )
    run_daily.apply_extractive_summaries(tagged, {})

    assert articles[0].description == POLLUTED_EXTRACTIVE  # 덮어써졌고
    assert articles[0].source_description == SOURCE_SNIPPET  # 원본은 보존된다


def test_content_rejection_does_not_change_classification_or_top10(tmp_path):
    items = [_polluted(i) for i in range(6)]
    body_cache = {it.article.link: BODY for it in items}

    before_visible = [it.article.title for it in visible_report_items(items)]
    before_top = [it.article.title for it in top_report_items(items, limit=10)]
    before_sectors = [list(it.sectors) for it in items]
    before_descriptions = [it.article.description for it in items]

    _apply(items, tmp_path, _summarizer(_gate({0: "multi_topic", 3: "title_body_mismatch"})), body_cache)

    assert [it.article.title for it in visible_report_items(items)] == before_visible
    assert [it.article.title for it in top_report_items(items, limit=10)] == before_top
    assert [list(it.sectors) for it in items] == before_sectors
    assert [it.article.description for it in items] == before_descriptions


def test_markdown_uses_the_source_snippet_for_rejected_articles(tmp_path):
    items = [_polluted(0)]
    body_cache = {items[0].article.link: BODY}
    _apply(items, tmp_path, _summarizer(_gate({0: "multi_topic"})), body_cache)

    md = render_markdown(datetime(2026, 8, 1, tzinfo=KST), items, [])
    assert SOURCE_SNIPPET.rstrip(".") in md
    assert "화물차 단속" not in md


# --- 실행 집계 JSON (smoke 판정용) --------------------------------------------


def test_run_summary_json_is_written_only_when_requested(tmp_path, monkeypatch):
    items = [_item(0)]
    body_cache = {items[0].article.link: BODY}

    # 변수가 없으면 파일을 만들지 않는다 (daily 동작 불변).
    monkeypatch.delenv(run_daily.GEMINI_RUN_SUMMARY_ENV, raising=False)
    _apply(items, tmp_path, _summarizer(), body_cache)
    assert not (tmp_path / "run-summary.json").exists()

    target = tmp_path / "run-summary.json"
    monkeypatch.setenv(run_daily.GEMINI_RUN_SUMMARY_ENV, str(target))
    # 앞선 실행이 캐시를 채웠으므로 별도 캐시 경로로 실제 전송을 만든다.
    fresh_cache = tmp_path / "second"
    fresh_cache.mkdir()
    _apply([_item(0)], fresh_cache, _summarizer(), body_cache)

    summary = json.loads(target.read_text(encoding="utf-8"))
    assert summary["targets"] == 1
    assert summary["gemini_applied"] == 1
    assert summary["sent_articles"] == 1
    assert summary["model"] == gemini_summary.DEFAULT_MODEL


def test_run_summary_json_contains_no_article_text_or_urls(tmp_path, monkeypatch):
    target = tmp_path / "run-summary.json"
    monkeypatch.setenv(run_daily.GEMINI_RUN_SUMMARY_ENV, str(target))
    items = [_item(i) for i in range(3)]
    body_cache = {it.article.link: BODY for it in items}
    _apply(items, tmp_path, _summarizer(_gate({0: "multi_topic"})), body_cache)

    raw = target.read_text(encoding="utf-8")
    assert "대부업 감독 규정 개정" not in raw  # 제목
    assert "example.com" not in raw  # URL
    assert "금융위원회는" not in raw  # 본문
    assert API_KEY not in raw

    summary = json.loads(raw)
    assert summary["content_rejected"] == 1
    assert summary["multi_topic"] == 1
    for key, value in summary.items():
        assert isinstance(value, (int, float, bool)) or key in {"model", "disabled_reason"}


def test_run_summary_json_written_even_when_gemini_is_disabled(tmp_path, monkeypatch):
    target = tmp_path / "run-summary.json"
    monkeypatch.setenv(run_daily.GEMINI_RUN_SUMMARY_ENV, str(target))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    items = [_item(0)]
    run_daily.apply_gemini_summaries(
        priority_items=items,
        visible_items=items,
        body_cache={items[0].article.link: BODY},
        cache_path=tmp_path / "gemini_summary_cache.json",
        summarizer=_summarizer(),
    )
    summary = json.loads(target.read_text(encoding="utf-8"))
    assert summary["disabled_reason"] == "no_api_key"
    assert summary["gemini_applied"] == 0


def test_total_api_failure_still_produces_a_report_and_a_summary(tmp_path, monkeypatch):
    """daily의 fail-open — API가 전부 죽어도 파이프라인은 성공한다."""

    class Boom(Exception):
        code = 503

    target = tmp_path / "run-summary.json"
    monkeypatch.setenv(run_daily.GEMINI_RUN_SUMMARY_ENV, str(target))
    items = [_item(i) for i in range(5)]
    body_cache = {it.article.link: BODY for it in items}

    applied = _apply(items, tmp_path, _summarizer(Boom()), body_cache)
    assert applied == 0
    # 리포트는 정상 생성되고 기존 요약이 남는다.
    html = _render(items)
    assert "대부업 감독 규정 개정 0" in html
    assert all(it.article.description for it in items)

    summary = json.loads(target.read_text(encoding="utf-8"))
    assert summary["gemini_applied"] == 0
    assert summary["api_errors"] > 0
