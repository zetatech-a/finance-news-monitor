from __future__ import annotations

import json
from datetime import datetime, timedelta

from src.pipeline.normalize import Article
from src.pipeline.quality import build_quality_metrics, write_quality_metrics
from src.pipeline.tagger import TaggedArticle


def _tagged(
    idx: int,
    *,
    title: str | None = None,
    sectors: list[str] | None = None,
    topics: list[str] | None = None,
    score: int | float | None = None,
    prob: float | None = None,
    cluster_size: int | None = None,
    minutes: int = 0,
    publisher: str | None = None,
    publisher_field: str = "press",
) -> TaggedArticle:
    article = Article(
        title=title or f"item {idx}",
        description=f"description {idx}",
        link=f"https://example.com/{idx}",
        originallink=None,
        naver_link=None,
        pub_date=datetime(2026, 5, 27, 9, 0) + timedelta(minutes=minutes),
        query="quality",
        relevance_score=score,
        relevance_prob=prob,
        cluster_size=cluster_size,
    )
    if publisher is not None:
        setattr(article, publisher_field, publisher)
    return TaggedArticle(
        article=article,
        sectors=sectors or [],
        topics=topics or [],
        matched_keywords=[],
    )


def test_build_quality_metrics_counts_taxonomy_clusters_publishers_and_top_items():
    tagged_before_cluster = [_tagged(i) for i in range(4)]
    final_items = [
        _tagged(
            1,
            title="bank policy article",
            sectors=["bank"],
            topics=["rates", "policy"],
            score=8,
            prob=0.91,
            cluster_size=1,
            minutes=10,
            publisher="Daily A",
        ),
        _tagged(
            2,
            title="loan no topic",
            sectors=["loan"],
            topics=[],
            score=5,
            cluster_size=60,
            minutes=20,
        ),
        _tagged(
            3,
            title="misc no topic",
            sectors=[],
            topics=[],
            prob=0.4,
            cluster_size=80,
            minutes=30,
            publisher="Daily B",
            publisher_field="publisher",
        ),
    ]

    metrics = build_quality_metrics(
        report_date="2026-05-27",
        generated_at=datetime(2026, 5, 27, 1, 2, 3),
        counts={
            "raw_items": 9,
            "normalized_articles": 8,
            "deduped_articles": 7,
            "rule_filtered_articles": 6,
            "relevance_filtered_articles": 5,
            "tagged_before_cluster": 4,
            "final_tagged_representatives": 3,
            "displayed_articles": 3,
        },
        tagged_before_cluster=tagged_before_cluster,
        tagged_final=final_items,
        top_items=final_items[:2],
        mega_cluster_threshold=50,
    )

    assert metrics["date"] == "2026-05-27"
    assert metrics["generated_at"] == "2026-05-27T01:02:03"
    assert metrics["counts"] == {
        "raw_items": 9,
        "normalized_articles": 8,
        "deduped_articles": 7,
        "rule_filtered_articles": 6,
        "relevance_filtered_articles": 5,
        "tagged_before_cluster": 4,
        "final_tagged_representatives": 3,
        "displayed_articles": 3,
    }

    taxonomy = metrics["taxonomy"]
    assert taxonomy["sector_counts"] == {"bank": 1, "loan": 1, "\uae30\ud0c0": 1}
    assert taxonomy["topic_counts"] == {"policy": 1, "rates": 1}
    assert taxonomy["no_topic_count"] == 2
    assert taxonomy["no_topic_ratio"] == 0.6667
    assert [item["title"] for item in taxonomy["no_topic_samples"]] == [
        "misc no topic",
        "loan no topic",
    ]

    clusters = metrics["clusters"]
    assert clusters["cluster_count"] == 3
    assert clusters["cluster_size_p50"] == 60
    assert clusters["cluster_size_p95"] == 80
    assert clusters["cluster_size_max"] == 80
    assert clusters["mega_cluster_count"] == 2
    assert [item["title"] for item in clusters["mega_cluster_samples"]] == [
        "misc no topic",
        "loan no topic",
    ]

    assert metrics["publishers"] == {
        "publisher_counts": {"Daily A": 1, "Daily B": 1},
        "missing_publisher_count": 1,
    }
    assert metrics["top10"]["count"] == 2
    assert metrics["top10"]["sector_counts"] == {"bank": 1, "loan": 1}
    assert metrics["top10"]["topic_counts"] == {"policy": 1, "rates": 1}
    assert metrics["top10"]["no_topic_count"] == 1
    assert [item["title"] for item in metrics["top10"]["items"]] == [
        "bank policy article",
        "loan no topic",
    ]


def test_build_quality_metrics_empty_inputs_produce_zeroed_sections():
    metrics = build_quality_metrics(
        report_date="2026-05-27",
        generated_at=None,
        counts={},
        tagged_before_cluster=[],
        tagged_final=[],
    )

    assert metrics["generated_at"] is None
    assert metrics["counts"] == {
        "raw_items": 0,
        "normalized_articles": 0,
        "deduped_articles": 0,
        "rule_filtered_articles": 0,
        "relevance_filtered_articles": 0,
        "tagged_before_cluster": 0,
        "final_tagged_representatives": 0,
        "displayed_articles": 0,
    }
    assert metrics["taxonomy"] == {
        "sector_counts": {},
        "topic_counts": {},
        "no_topic_count": 0,
        "no_topic_ratio": 0.0,
        "no_topic_samples": [],
    }
    assert metrics["clusters"] == {
        "cluster_count": 0,
        "cluster_size_p50": 0,
        "cluster_size_p95": 0,
        "cluster_size_max": 0,
        "mega_cluster_threshold": 50,
        "mega_cluster_count": 0,
        "mega_cluster_samples": [],
    }
    assert metrics["top10"] == {
        "count": 0,
        "sector_counts": {},
        "topic_counts": {},
        "no_topic_count": 0,
        "items": [],
    }
    assert metrics["publishers"] == {
        "publisher_counts": {},
        "missing_publisher_count": 0,
    }


def test_write_quality_metrics_writes_utf8_json_without_ascii_escaping(tmp_path):
    publisher = "\uc5b8\ub860\uc0ac"
    metrics = {
        "date": "2026-05-27",
        "publishers": {"publisher_counts": {publisher: 1}},
    }

    path = write_quality_metrics(
        metrics,
        tmp_path / "reports" / "_metrics" / "quality.json",
    )

    raw = path.read_bytes()
    assert b"\\uc5b8" not in raw
    payload = json.loads(raw.decode("utf-8"))
    assert payload["publishers"]["publisher_counts"][publisher] == 1
