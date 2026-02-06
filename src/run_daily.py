from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from src.config import KST, load_config, now_kst
from src.fetchers.naver import fetch_news
from src.pipeline.dedup import deduplicate
from src.pipeline.normalize import normalize
from src.pipeline.report import render_markdown, write_index, write_report
from src.pipeline.tagger import keyword_trends, tag_articles

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT_DIR / "reports"
QUERIES_PATH = ROOT_DIR / "queries.yml"


def load_queries() -> dict[str, list[str]]:
    data = yaml.safe_load(QUERIES_PATH.read_text(encoding="utf-8"))
    return data.get("sectors", {})


def build_query_list(sector_queries: dict[str, list[str]]) -> list[str]:
    seen = set()
    queries: list[str] = []
    for keywords in sector_queries.values():
        for keyword in keywords:
            if keyword not in seen:
                seen.add(keyword)
                queries.append(keyword)
    return queries


def compute_window(target_date: datetime, window_hours: float) -> tuple[datetime, datetime]:
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

    sector_queries = load_queries()
    query_list = build_query_list(sector_queries)

    config = load_config()
    raw_items = fetch_news(config.naver, query_list, start=start, end=end)
    articles = normalize(raw_items)
    articles = deduplicate(articles)
    tagged = tag_articles(articles, sector_queries)

    markdown_text = render_markdown(end, tagged, keyword_trends(tagged))
    paths = write_report(end, markdown_text, REPORT_DIR)

    recent_reports = sorted(
    [p for p in REPORT_DIR.glob("*.html") if p.name != "index.html"],
    reverse=True
)[:14]
write_index(recent_reports, REPORT_DIR)


    logger.info("Report written: %s", paths["markdown"])


if __name__ == "__main__":
    main()
