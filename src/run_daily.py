from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from src.config import KST, load_config, now_kst
from src.fetchers.naver import fetch_news
from src.pipeline.dedup import deduplicate
from src.pipeline.filtering import filter_articles
from src.pipeline.normalize import normalize
from src.pipeline.report import render_markdown, write_index, write_report, render_html
from src.pipeline.tagger import keyword_trends, tag_articles

# ✅ 금융 관련성(스코어링/모델) 필터
from src.pipeline.relevance_filter import filter_relevance

# ✅ (무료) 본문 추출 + 추출요약
from src.pipeline.fulltext_fetch import fetch_html, extract_main_text
from src.pipeline.extractive_summary import summarize

# ✅ 캐시(같은 URL 재요청 방지)
from src.pipeline.summary_cache import load_cache, save_cache

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT_DIR / "reports"
QUERIES_PATH = ROOT_DIR / "queries.yml"


def load_queries() -> tuple[dict[str, list[str]], dict[str, list[str]], list[str]]:
    """Load taxonomy queries.

    queries.yml supports:
      - legacy format: {sector: [keywords...]}
      - new format: {sectors: {...}, topics: {...}, fetch_queries: [...]}

    fetch_queries is used ONLY for collecting articles (high precision).
    sectors/topics are used for tagging (high recall).
    """

    if not QUERIES_PATH.exists():
        raise FileNotFoundError(f"queries.yml not found at: {QUERIES_PATH}")

    data = yaml.safe_load(QUERIES_PATH.read_text(encoding="utf-8")) or {}

    # backward compatibility: legacy format can be plain sector mapping
    if any(k in data for k in ("sectors", "topics", "fetch_queries")):
        sectors = data.get("sectors", {}) or {}
        topics = data.get("topics", {}) or {}
        raw_fetch = data.get("fetch_queries", []) or []
    else:
        sectors = data or {}
        topics = {}
        raw_fetch = []

    fetch_queries: list[str] = []
    if isinstance(raw_fetch, list):
        fetch_queries = [str(x).strip() for x in raw_fetch if str(x).strip()]
    elif isinstance(raw_fetch, dict):
        # allow grouped fetch queries: {group: [..]}
        for v in raw_fetch.values():
            if isinstance(v, list):
                fetch_queries.extend([str(x).strip() for x in v if str(x).strip()])

    return sectors, topics, fetch_queries


def build_query_list(sector_queries: dict[str, list[str]]) -> list[str]:
    """Legacy behavior: build query list from ALL sector keywords."""
    seen: set[str] = set()
    queries: list[str] = []
    for keywords in sector_queries.values():
        for keyword in keywords:
            if keyword not in seen:
                seen.add(keyword)
                queries.append(keyword)
    return queries


def build_fetch_query_list(fetch_queries: list[str], sector_queries: dict[str, list[str]]) -> list[str]:
    """Preferred behavior: use fetch_queries for collection.

    If fetch_queries is empty, fallback to legacy sector-keyword query list.
    """
    if fetch_queries:
        seen: set[str] = set()
        out: list[str] = []
        for q in fetch_queries:
            q = (q or "").strip()
            if not q or q in seen:
                continue
            seen.add(q)
            out.append(q)
        return out
    return build_query_list(sector_queries)


def compute_window(target_date: datetime, window_hours: float) -> tuple[datetime, datetime]:
    # 기준 시간(07:30 KST)을 anchor로 잡고, window_hours만큼 과거로 수집
    end = target_date.replace(hour=7, minute=30, second=0, microsecond=0)
    start = end - timedelta(hours=window_hours)
    return start, end


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily finance news monitor")
    parser.add_argument("--date", type=str, help="YYYY-MM-DD (KST)")
    parser.add_argument("--window_hours", type=float, default=13.5)
    parser.add_argument("--use_deepsearch", action="store_true")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()

    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=KST)
    else:
        target_date = now_kst()

    start, end = compute_window(target_date, args.window_hours)
    logger.info("Collecting news from %s to %s", start, end)

    if args.use_deepsearch:
        logger.warning("DeepSearch is not configured in this MVP; skipping.")

    # reports 폴더 없으면 생성
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    sector_queries, topic_queries, fetch_queries = load_queries()
    query_list = build_fetch_query_list(fetch_queries, sector_queries)
    logger.info("Fetch queries: %d (sample=%s)", len(query_list), ", ".join(query_list[:6]))

    config = load_config()
    raw_items = fetch_news(config.naver, query_list, start=start, end=end)

    articles = normalize(raw_items)
    articles = deduplicate(articles)

    # ✅ 기존 1차 룰 필터(스포츠/엔터/잡기사 등)
    articles = filter_articles(articles)

    # ✅ 금융 관련성 필터(모델 있으면 모델, 없으면 스코어링 기준으로 통과)
    # 모델이 없을 때는 min_score가 precision을 좌우하므로 보수적으로 설정
    model_path = ROOT_DIR / "models" / "relevance.joblib"
    candidates_csv = REPORT_DIR / "_candidates" / f"{end.date().isoformat()}_candidates.csv"
    before = len(articles)
    articles = filter_relevance(
        articles,
        model_path=model_path,
        out_candidates_csv=candidates_csv,
        min_prob=0.60,
        min_score=5,
    )
    logger.info("Relevance filtered: %d -> %d (dropped=%d)", before, len(articles), before - len(articles))

    # ✅ 금융 관련성 통과 기사만 섹터/토픽 태깅
    tagged = tag_articles(articles, sector_queries, topic_queries=topic_queries)

    # ✅ 요약 캐시 로드 (같은 URL 재요청 방지)
    cache_path = REPORT_DIR / "_cache" / "summary_cache.json"
    summary_cache = load_cache(cache_path)

    # ✅ (중요) 태깅 후, 리포트에 실릴 "일부 기사만" 본문 추출 + 추출요약
    MAX_SUMMARIZE = 80  # 필요하면 60~120 사이로 조정
    summarized = 0
    seen_urls: set[str] = set()
    cache_hits = 0

    # 최신 기사부터 처리(리포트에 들어갈 가능성이 높은 것 우선)
    for item in sorted(tagged, key=lambda x: x.article.pub_date, reverse=True):
        fetch_url = item.article.naver_link or item.article.originallink or item.article.link
        if not fetch_url or fetch_url in seen_urls:
            continue
        seen_urls.add(fetch_url)

        # ✅ 캐시 hit면 크롤링 없이 바로 사용
        cached = (summary_cache.get(fetch_url) or "").strip()
        if cached:
            item.article.description = cached
            setattr(item.article, "summary_cached", True)  # ✅ UI에서 ⚡ 표시용
            summarized += 1
            cache_hits += 1
            if summarized >= MAX_SUMMARIZE:
                break
            continue

        # ✅ 캐시에 없으면 본문 추출 → 추출요약 시도
        try:
            html = fetch_html(fetch_url, timeout=12)
            full = extract_main_text(fetch_url, html)

            # 본문 추출이 실패/차단/잡문이면 네이버 스니펫(기존 description) 유지
            if full and len(full) >= 350:
                s = summarize(full, max_sentences=3, max_chars=320)
                if s and len(s) >= 40:
                    item.article.description = s
                    summary_cache[fetch_url] = s  # ✅ 캐시에 저장
                    setattr(item.article, "summary_cached", False)  # ✅ (선택) 신규 요약 표시
                    summarized += 1
        except Exception:
            # 실패하면 기존 description(네이버 스니펫) 그대로 사용
            pass

        if summarized >= MAX_SUMMARIZE:
            break

    # ✅ 캐시 저장
    save_cache(cache_path, summary_cache)
    logger.info("Summary cache saved: %s (items=%d)", cache_path, len(summary_cache))
    logger.info("Extractive summaries applied: %s (cache_hits=%s)", summarized, cache_hits)

    markdown_text = render_markdown(end, tagged, keyword_trends(tagged))

    # ✅ 제품형 UI HTML 생성
    html_page = render_html(end, tagged, keyword_trends(tagged))

    # ✅ html_override로 저장
    paths = write_report(end, markdown_text, REPORT_DIR, html_override=html_page)

    # index.html은 최근 리스트에서 제외하고, 날짜 파일명 기준으로 정렬 (YYYY-MM-DD.html)
    recent_reports = sorted(
        [p for p in REPORT_DIR.glob("*.html") if p.name != "index.html"],
        key=lambda p: p.name,
        reverse=True,
    )[:14]
    write_index(recent_reports, REPORT_DIR)

    logger.info("Report written: %s", paths["markdown"])
    logger.info("Index written: %s", REPORT_DIR / "index.html")
    logger.info("Candidates saved: %s", candidates_csv)


if __name__ == "__main__":
    main()
