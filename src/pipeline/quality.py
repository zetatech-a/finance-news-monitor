from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from src.pipeline.fields import field_value as _field, unwrap_article as _article
from src.pipeline.tagger import TaggedArticle

MISC_SECTOR = "\uae30\ud0c0"

COUNT_KEYS = (
    "raw_items",
    "normalized_articles",
    "deduped_articles",
    "rule_filtered_articles",
    "relevance_filtered_articles",
    "tagged_before_cluster",
    "final_tagged_representatives",
    "displayed_articles",
)

PUBLISHER_KEYS = ("press", "publisher", "office", "company", "source")


def _clean_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _number_or_none(value: Any) -> int | float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            return None
    if not math.isfinite(number):
        return None
    if number.is_integer():
        return int(number)
    return number


def _first_number(article: Any, *keys: str) -> int | float | None:
    for key in keys:
        value = _number_or_none(_field(article, key))
        if value is not None:
            return value
    return None


def _cluster_size(article: Any, *, default: int | None) -> int | None:
    value = _number_or_none(_field(article, "cluster_size"))
    if value is None:
        return default
    size = int(value)
    if size < 1:
        return default
    return size


def _pub_timestamp(article: Any) -> float:
    value = _field(article, "pub_date")
    if isinstance(value, datetime):
        try:
            return float(value.timestamp())
        except Exception:
            return 0.0
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return float(parsed.timestamp())
        except ValueError:
            return 0.0
    return 0.0


def _primary_sector(item: TaggedArticle | Any) -> str:
    sectors = _field(item, "sectors") or []
    if isinstance(sectors, (list, tuple)) and sectors:
        sector = _clean_str(sectors[0])
        if sector:
            return sector
    return MISC_SECTOR


def _topics(item: TaggedArticle | Any) -> list[str]:
    raw_topics = _field(item, "topics") or []
    if not isinstance(raw_topics, (list, tuple, set)):
        return []
    return [topic for topic in (_clean_str(value) for value in raw_topics) if topic]


def _primary_url(article: Any) -> str:
    for key in ("naver_link", "originallink", "link", "url"):
        value = _clean_str(_field(article, key))
        if value:
            return value
    return ""


def _publisher(article: Any) -> str:
    for key in PUBLISHER_KEYS:
        value = _clean_str(_field(article, key))
        if value:
            return value
    return ""


def _sorted_counter(counter: Counter[str]) -> dict[str, int]:
    return {key: int(counter[key]) for key in sorted(counter)}


def _percentile_nearest_rank(values: list[int], percentile: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = math.ceil((percentile / 100) * len(ordered))
    index = min(max(rank - 1, 0), len(ordered) - 1)
    return int(ordered[index])


def _sample(item: TaggedArticle | Any) -> dict[str, Any]:
    article = _article(item)
    return {
        "title": _clean_str(_field(article, "title")),
        "url": _primary_url(article),
        "sector": _primary_sector(item),
        "relevance_score": _first_number(article, "relevance_score", "score"),
        "relevance_prob": _first_number(article, "relevance_prob", "prob"),
        "cluster_size": _cluster_size(article, default=None),
    }


def _sample_sort_key(item: TaggedArticle | Any) -> tuple[float, str, str]:
    article = _article(item)
    return (
        -_pub_timestamp(article),
        _clean_str(_field(article, "title")),
        _primary_url(article),
    )


def _cluster_sample_sort_key(item: TaggedArticle | Any) -> tuple[int, str, str]:
    article = _article(item)
    return (
        -int(_cluster_size(article, default=1) or 1),
        _clean_str(_field(article, "title")),
        _primary_url(article),
    )


def _taxonomy_metrics(tagged: list[TaggedArticle]) -> dict[str, Any]:
    sector_counts: Counter[str] = Counter()
    topic_counts: Counter[str] = Counter()
    no_topic_items: list[TaggedArticle] = []

    for item in tagged:
        sector_counts[_primary_sector(item)] += 1
        topics = _topics(item)
        if not topics:
            no_topic_items.append(item)
        topic_counts.update(topics)

    total = len(tagged)
    no_topic_count = len(no_topic_items)
    samples = [
        _sample(item)
        for item in sorted(no_topic_items, key=_sample_sort_key)[:10]
    ]
    return {
        "sector_counts": _sorted_counter(sector_counts),
        "topic_counts": _sorted_counter(topic_counts),
        "no_topic_count": no_topic_count,
        "no_topic_ratio": round(no_topic_count / total, 4) if total else 0.0,
        "no_topic_samples": samples,
    }


def _cluster_metrics(
    tagged: list[TaggedArticle], mega_cluster_threshold: int
) -> dict[str, Any]:
    sizes = [int(_cluster_size(_article(item), default=1) or 1) for item in tagged]
    mega_items = [
        item
        for item in tagged
        if int(_cluster_size(_article(item), default=1) or 1) >= mega_cluster_threshold
    ]
    return {
        "cluster_count": len(tagged),
        "cluster_size_p50": _percentile_nearest_rank(sizes, 50),
        "cluster_size_p95": _percentile_nearest_rank(sizes, 95),
        "cluster_size_max": max(sizes, default=0),
        "mega_cluster_threshold": int(mega_cluster_threshold),
        "mega_cluster_count": len(mega_items),
        "mega_cluster_samples": [
            _sample(item)
            for item in sorted(mega_items, key=_cluster_sample_sort_key)[:10]
        ],
    }


def _top10_metrics(top_items: list[TaggedArticle]) -> dict[str, Any]:
    sector_counts: Counter[str] = Counter()
    topic_counts: Counter[str] = Counter()
    no_topic_count = 0

    for item in top_items:
        sector_counts[_primary_sector(item)] += 1
        topics = _topics(item)
        if not topics:
            no_topic_count += 1
        topic_counts.update(topics)

    return {
        "count": len(top_items),
        "sector_counts": _sorted_counter(sector_counts),
        "topic_counts": _sorted_counter(topic_counts),
        "no_topic_count": no_topic_count,
        "items": [_sample(item) for item in top_items],
    }


def _publisher_metrics(tagged: list[TaggedArticle]) -> dict[str, Any]:
    publisher_counts: Counter[str] = Counter()
    missing_publisher_count = 0

    for item in tagged:
        publisher = _publisher(_article(item))
        if publisher:
            publisher_counts[publisher] += 1
        else:
            missing_publisher_count += 1

    return {
        "publisher_counts": _sorted_counter(publisher_counts),
        "missing_publisher_count": missing_publisher_count,
    }


def _count_metrics(
    counts: dict[str, int],
    tagged_before_cluster: list[TaggedArticle],
    tagged_final: list[TaggedArticle],
) -> dict[str, int]:
    inferred = {
        "tagged_before_cluster": len(tagged_before_cluster),
        "final_tagged_representatives": len(tagged_final),
        "displayed_articles": len(tagged_final),
    }
    return {
        key: _safe_int(counts.get(key, inferred.get(key, 0)))
        for key in COUNT_KEYS
    }


def build_quality_metrics(
    *,
    report_date: str,
    generated_at: datetime | None,
    counts: dict[str, int],
    tagged_before_cluster: list[TaggedArticle],
    tagged_final: list[TaggedArticle],
    top_items: list[TaggedArticle] | None = None,
    mega_cluster_threshold: int = 50,
) -> dict[str, Any]:
    final_items = list(tagged_final)
    before_cluster_items = list(tagged_before_cluster)
    top_items = list(top_items or [])

    return {
        "date": report_date,
        "generated_at": generated_at.isoformat() if generated_at else None,
        "counts": _count_metrics(counts, before_cluster_items, final_items),
        "taxonomy": _taxonomy_metrics(final_items),
        "clusters": _cluster_metrics(final_items, mega_cluster_threshold),
        "top10": _top10_metrics(top_items),
        "publishers": _publisher_metrics(final_items),
    }


def write_quality_metrics(metrics: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path
