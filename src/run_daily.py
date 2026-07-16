from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from src.config import KST, load_config, load_dotenv_if_present, now_kst
from src.fetchers.naver import fetch_news
from src.pipeline.dedup import deduplicate
from src.pipeline.filtering import filter_articles
from src.pipeline.normalize import normalize
from src.pipeline.report import (
    render_markdown,
    top_report_items,
    visible_report_items,
    write_index,
    write_report,
    render_html,
)
from src.pipeline.tagger import keyword_trends, tag_articles
from src.pipeline.issue_cluster import cluster_tagged_articles
from src.pipeline.quality import build_quality_metrics, write_quality_metrics

# ✅ 금융 관련성(스코어링/모델) 필터
from src.pipeline.relevance_filter import filter_relevance

# ✅ (무료) 본문 추출 + 추출요약
from src.pipeline.fulltext_fetch import fetch_html, extract_main_text
from src.pipeline.extractive_summary import summarize_with_fallback

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


def build_fetch_query_list(
    fetch_queries: list[str], sector_queries: dict[str, list[str]]
) -> list[str]:
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


def compute_window(
    target_date: datetime,
    window_hours: float,
    end_hhmm: str,
    overlap_minutes: int,
    is_auto_date: bool,
) -> tuple[datetime, datetime]:
    hhmm = (end_hhmm or "").strip()
    normalized = hhmm.replace(":", "")
    if len(normalized) != 4 or not normalized.isdigit():
        raise ValueError(f"Invalid --end_hhmm format: {end_hhmm}")

    hour = int(normalized[:2])
    minute = int(normalized[2:])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid --end_hhmm value: {end_hhmm}")

    end = target_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if is_auto_date and end > now_kst():
        end -= timedelta(days=1)

    start = end - timedelta(hours=window_hours) - timedelta(minutes=overlap_minutes)
    return start, end


MAX_SUMMARIZE = 80  # 리포트에 반영할 추출요약 성공 건수 상한 (필요하면 60~120 사이로 조정)
MAX_SUMMARY_FETCH_ATTEMPTS = 160  # 본문 크롤링 시도(실패 포함) 상한 — 실행시간 폭주 방지


def apply_extractive_summaries(
    tagged: list,
    summary_cache: dict[str, str],
    *,
    max_summaries: int = MAX_SUMMARIZE,
    max_fetch_attempts: int = MAX_SUMMARY_FETCH_ATTEMPTS,
) -> tuple[int, int, int]:
    """대표 기사들의 본문을 추출·요약해 description에 반영한다.

    max_summaries는 성공 건수 기준, max_fetch_attempts는 네트워크 크롤링
    시도(실패 포함) 기준이다. 크롤링 한도를 다 써도 캐시 hit는 계속 반영한다.
    Returns (summarized, cache_hits, fetch_attempts).
    """
    summarized = 0
    cache_hits = 0
    fetch_attempts = 0
    seen_urls: set[str] = set()

    # 최신 기사부터 처리(리포트에 들어갈 가능성이 높은 것 우선)
    for item in sorted(
        tagged, key=lambda x: _article_pub_timestamp(x.article), reverse=True
    ):
        if summarized >= max_summaries:
            break
        fetch_url = (
            item.article.naver_link or item.article.originallink or item.article.link
        )
        if not fetch_url or fetch_url in seen_urls:
            continue
        seen_urls.add(fetch_url)

        # 캐시 hit면 크롤링 없이 바로 사용
        cached = (summary_cache.get(fetch_url) or "").strip()
        if cached:
            item.article.description = cached
            setattr(item.article, "summary_cached", True)  # UI에서 ⚡ 표시용
            summarized += 1
            cache_hits += 1
            continue

        if fetch_attempts >= max_fetch_attempts:
            continue
        fetch_attempts += 1

        # 캐시에 없으면 본문 추출 → 추출요약 시도
        try:
            html = fetch_html(fetch_url, timeout=12)
            full = extract_main_text(fetch_url, html)

            s = summarize_with_fallback(
                full or "",
                title=item.article.title,
                description=item.article.description,
                max_chars=220,
            )
            if s and len(s) >= 24:
                item.article.description = s
                summary_cache[fetch_url] = s
                setattr(item.article, "summary_cached", False)
                summarized += 1
        except Exception:
            # 실패하면 기존 description(네이버 스니펫) 그대로 사용
            pass

    return summarized, cache_hits, fetch_attempts


def _article_pub_timestamp(article: object) -> float:
    pub_date = getattr(article, "pub_date", None)
    if isinstance(pub_date, datetime):
        try:
            return float(pub_date.timestamp())
        except Exception:
            return 0.0
    return 0.0


def choose_relevance_model_policy(
    *,
    operating_model_path: Path,
    candidate_model_path: Path,
    disable_candidate_model: bool = False,
) -> tuple[Path, str]:
    if operating_model_path.exists():
        return operating_model_path, "authoritative"
    if not disable_candidate_model and candidate_model_path.exists():
        return candidate_model_path, "candidate_hybrid"
    return operating_model_path, "rule_only"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily finance news monitor")
    parser.add_argument("--date", type=str, help="YYYY-MM-DD (KST)")
    parser.add_argument("--window_hours", type=float, default=24.0)
    parser.add_argument(
        "--end_hhmm",
        type=str,
        default="0730",
        help='Collection end time in KST (e.g. "0900" or "09:00")',
    )
    parser.add_argument(
        "--overlap_minutes",
        type=int,
        default=15,
        help="Safety overlap minutes to extend start time backward",
    )
    parser.add_argument(
        "--dry_run",
        action="store_true",
        help="Only print calculated collection window and exit",
    )
    parser.add_argument("--max_pages", type=int, default=5)
    parser.add_argument("--use_deepsearch", action="store_true")
    parser.add_argument(
        "--disable_candidate_model",
        action="store_true",
        help="Ignore models/relevance_candidate.joblib when no operating relevance model exists",
    )
    parser.add_argument(
        "--candidate_keep_prob",
        type=float,
        default=0.65,
        help="Candidate hybrid keep threshold",
    )
    parser.add_argument(
        "--candidate_drop_prob",
        type=float,
        default=0.35,
        help="Candidate hybrid drop threshold",
    )
    parser.add_argument(
        "--candidate_gray_keep_min_score",
        type=int,
        default=6,
        help="Candidate hybrid gray-zone rule score threshold for domain-anchored keeps",
    )
    parser.add_argument(
        "--candidate_strong_rule_keep_score",
        type=int,
        default=8,
        help="Candidate hybrid strong domain-rule keep score threshold",
    )
    parser.add_argument(
        "--candidate_no_model_keep_min_score",
        type=int,
        default=5,
        help="Candidate hybrid no-probability rule score threshold for domain-anchored keeps",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    load_dotenv_if_present()  # 로컬 .env 자동 로드 (이미 export된 값은 유지)
    args = parse_args()

    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=KST)
        is_auto_date = False
    else:
        target_date = now_kst()
        is_auto_date = True

    start, end = compute_window(
        target_date,
        args.window_hours,
        args.end_hhmm,
        args.overlap_minutes,
        is_auto_date,
    )
    logger.info("Collecting news from %s to %s (KST)", start, end)

    if args.dry_run:
        return

    if args.use_deepsearch:
        logger.warning("DeepSearch is not configured in this MVP; skipping.")

    run_pipeline(args, start, end)


def collect_articles(
    args: argparse.Namespace, start: datetime, end: datetime
) -> tuple[list, dict[str, list[str]], dict[str, list[str]], dict[str, int]]:
    """수집 → 정규화 → 중복 제거 → 1차 룰 필터."""
    sector_queries, topic_queries, fetch_queries = load_queries()
    query_list = build_fetch_query_list(fetch_queries, sector_queries)
    logger.info(
        "Fetch queries: %d (sample=%s)", len(query_list), ", ".join(query_list[:6])
    )

    config = load_config()
    raw_items = fetch_news(
        config.naver,
        query_list,
        start=start,
        end=end,
        max_pages=args.max_pages,
    )
    counts: dict[str, int] = {"raw_items": len(raw_items)}
    logger.info("Counts: raw_items=%d", counts["raw_items"])

    articles = normalize(raw_items)
    counts["normalized_articles"] = len(articles)
    logger.info("Counts: normalize=%d", counts["normalized_articles"])

    articles = deduplicate(articles)
    counts["deduped_articles"] = len(articles)
    logger.info("Counts: dedup=%d", counts["deduped_articles"])

    # 1차 룰 필터(스포츠/엔터/잡기사 등)
    articles = filter_articles(articles)
    counts["rule_filtered_articles"] = len(articles)
    logger.info("Counts: rule_filter=%d", counts["rule_filtered_articles"])
    return articles, sector_queries, topic_queries, counts


def apply_relevance_filter(
    articles: list, args: argparse.Namespace, end: datetime
) -> tuple[list, Path]:
    """2차 금융 관련성 필터 — 정책 선택 + 후보 CSV/메트릭 기록."""
    # operating model은 authoritative, candidate model은 guardrail hybrid, 없으면 rule_only
    model_path, model_policy = choose_relevance_model_policy(
        operating_model_path=ROOT_DIR / "models" / "relevance.joblib",
        candidate_model_path=ROOT_DIR / "models" / "relevance_candidate.joblib",
        disable_candidate_model=args.disable_candidate_model,
    )
    date_str = end.date().isoformat()
    candidates_csv = REPORT_DIR / "_candidates" / f"{date_str}_candidates.csv"
    relevance_metrics_path = (
        REPORT_DIR / "_metrics" / f"{date_str}_relevance_filter_metrics.json"
    )
    before = len(articles)
    articles = filter_relevance(
        articles,
        model_path=model_path,
        out_candidates_csv=candidates_csv,
        min_prob=0.55,
        min_score=4,
        model_policy=model_policy,
        candidate_keep_prob=args.candidate_keep_prob,
        candidate_drop_prob=args.candidate_drop_prob,
        strong_rule_keep_score=args.candidate_strong_rule_keep_score,
        gray_keep_min_score=args.candidate_gray_keep_min_score,
        no_model_keep_min_score=args.candidate_no_model_keep_min_score,
        metrics_path=relevance_metrics_path,
        metrics_date=date_str,
    )
    logger.info(
        "Relevance filtered: %d -> %d (dropped=%d, policy=%s, model_path=%s)",
        before,
        len(articles),
        before - len(articles),
        model_policy,
        model_path,
    )
    logger.info("Counts: relevance_filter=%d", len(articles))
    return articles, candidates_csv


def tag_and_cluster(
    articles: list,
    sector_queries: dict[str, list[str]],
    topic_queries: dict[str, list[str]],
) -> tuple[list, list]:
    """섹터/토픽 태깅 후 같은 이슈는 대표 기사만 남긴다."""
    tagged = tag_articles(articles, sector_queries, topic_queries=topic_queries)
    tagged_before_cluster_items = list(tagged)

    before_cluster = len(tagged)
    tagged = cluster_tagged_articles(tagged)
    cluster_sizes = [int(getattr(item.article, "cluster_size", 1) or 1) for item in tagged]
    logger.info(
        "Issue clustering: %d -> %d representatives grouped=%d max_cluster_size=%d",
        before_cluster,
        len(tagged),
        before_cluster - len(tagged),
        max(cluster_sizes, default=0),
    )
    return tagged, tagged_before_cluster_items


def summarize_representatives(tagged: list) -> None:
    """대표 기사 본문 추출요약 — 캐시 사용, description 교체."""
    cache_path = REPORT_DIR / "_cache" / "summary_cache.json"
    summary_cache = load_cache(cache_path)

    summarized, cache_hits, fetch_attempts = apply_extractive_summaries(
        tagged, summary_cache
    )

    save_cache(cache_path, summary_cache)
    logger.info("Summary cache saved: %s (items=%d)", cache_path, len(summary_cache))
    logger.info(
        "Extractive summaries applied: %s (cache_hits=%s, fetch_attempts=%s)",
        summarized,
        cache_hits,
        fetch_attempts,
    )


def write_quality(
    end: datetime,
    counts: dict[str, int],
    tagged_before_cluster_items: list,
    tagged: list,
    top_items: list,
) -> None:
    """품질 메트릭 JSON 기록 — 실패해도 리포트 생성은 계속한다."""
    quality_metrics_path = (
        REPORT_DIR / "_metrics" / f"{end.date().isoformat()}_quality_metrics.json"
    )
    try:
        quality_metrics = build_quality_metrics(
            report_date=end.date().isoformat(),
            generated_at=now_kst(),
            counts=counts,
            tagged_before_cluster=tagged_before_cluster_items,
            tagged_final=tagged,
            top_items=top_items,
        )
        write_quality_metrics(quality_metrics, quality_metrics_path)
        logger.info("Quality metrics saved: %s", quality_metrics_path)
    except Exception as exc:
        logger.warning(
            "Failed to write quality metrics to %s: %s",
            quality_metrics_path,
            exc,
        )


def write_outputs(end: datetime, tagged: list, candidates_csv: Path) -> None:
    """Markdown/HTML 리포트 + 14일 인덱스 생성."""
    trends = keyword_trends(tagged)
    markdown_text = render_markdown(end, tagged, trends)
    html_page = render_html(end, tagged, trends)
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


def run_pipeline(args: argparse.Namespace, start: datetime, end: datetime) -> None:
    """일일 파이프라인: 수집 → 필터 → 태깅/클러스터 → 요약 → 리포트."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    articles, sector_queries, topic_queries, counts = collect_articles(args, start, end)

    articles, candidates_csv = apply_relevance_filter(articles, args, end)
    counts["relevance_filtered_articles"] = len(articles)

    tagged, tagged_before_cluster_items = tag_and_cluster(
        articles, sector_queries, topic_queries
    )
    counts["tagged_before_cluster"] = len(tagged_before_cluster_items)

    summarize_representatives(tagged)

    # 요약 반영 후 최종 본문(description) 기준으로 대표 기사만 태깅 재계산
    representative_articles = [item.article for item in tagged]
    tagged = tag_articles(representative_articles, sector_queries, topic_queries=topic_queries)
    counts["final_tagged_representatives"] = len(tagged)
    logger.info("Counts: final_tagged_representatives=%d", len(tagged))

    visible_items = visible_report_items(tagged)
    top_items = top_report_items(tagged, limit=10)
    counts["displayed_articles"] = len(visible_items)

    write_quality(end, counts, tagged_before_cluster_items, tagged, top_items)
    write_outputs(end, tagged, candidates_csv)


if __name__ == "__main__":
    main()
