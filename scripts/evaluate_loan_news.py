"""Offline candidate replay; no fetching, model inference, or report-cache writes.

Run from the repository root with ``python -m scripts.evaluate_loan_news``.
CSV probabilities are rounded production observations, not a recreated model.
Candidates lack timestamps, query provenance, and absorbed duplicate metadata;
this evaluates candidate-level clustering before extractive-summary re-tagging.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from src.pipeline.issue_cluster import _issue_fingerprint, cluster_tagged_articles
from src.pipeline.normalize import Article
from src.pipeline.relevance_filter import _decide_relevance, _matched_terms
from src.pipeline.relevance_score import relevance_score
from src.pipeline.tagger import TaggedArticle, tag_articles

FIXTURE = Path("tests/fixtures/loan_news/2026-09-17.json")


def load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return json.load(stream) if path.suffix == ".json" else list(csv.DictReader(stream))


def make_article(row: dict[str, Any]) -> Article:
    return Article(
        title=row["title"], description=row["summary"], link=row["url"],
        originallink=row["url"], naver_link=None,
        # Fixed neutral date: the CSV does not retain actual publication times.
        pub_date=datetime(2000, 1, 1), query="",
    )


def replay(rows: list[dict[str, Any]], *, recorded: bool = False) -> tuple[list[TaggedArticle], list[TaggedArticle]]:
    articles = []
    for row in rows:
        article = make_article(row)
        article.relevance_prob = float(row["prob"]) if row.get("prob") not in (None, "") else None
        article.relevance_score = int(row["score"]) if recorded else relevance_score(article)
        if recorded:
            keep = bool(int(row["keep"]))
        else:
            matched = _matched_terms(article)
            keep, _ = _decide_relevance(
                score=article.relevance_score, prob=article.relevance_prob,
                min_score=4, min_prob=0.55, model_policy="candidate_hybrid",
                article_text=f"{article.title}\n{article.description}",
                matched_hard=matched["matched_hard"], matched_negative=matched["matched_negative"],
            )
        if keep:
            articles.append(article)
    taxonomy = yaml.safe_load(Path("queries.yml").read_text(encoding="utf-8"))
    tagged = tag_articles(articles, taxonomy["sectors"], taxonomy["topics"])
    return tagged, cluster_tagged_articles(tagged)


def evaluate(rows: list[dict[str, Any]], *, recorded: bool = False) -> dict[str, Any]:
    tagged, reps = replay(rows, recorded=recorded)
    groups: dict[str, list[TaggedArticle]] = defaultdict(list)
    for item in tagged:
        groups[item.article.cluster_id].append(item)
    result: dict[str, Any] = {
        "input_count": len(rows), "kept_count": len(tagged), "cluster_count": len(reps),
        "largest_cluster": max((len(group) for group in groups.values()), default=0),
        "suspicious_large_clusters_ge_50": sum(len(group) >= 50 for group in groups.values()),
        "loan_representatives": sum(item.sectors[0] == "대부" for item in reps),
    }
    if rows and "event" in rows[0]:
        labels = {row["url"]: row for row in rows}
        true_keeps = sum(labels[item.article.link]["relevant"] for item in tagged)
        result["filter_precision"] = true_keeps / len(tagged) if tagged else None
        result["candidate_recall"] = true_keeps / sum(row["relevant"] for row in rows)
        majority = sum(max(Counter(labels[item.article.link]["event"] for item in group).values()) for group in groups.values())
        result["cluster_purity"] = majority / len(tagged) if tagged else None
        true_pairs = predicted_pairs = expected_pairs = 0
        event_counts = Counter(labels[item.article.link]["event"] for item in tagged)
        expected_pairs = sum(n * (n - 1) // 2 for n in event_counts.values())
        for group in groups.values():
            predicted_pairs += len(group) * (len(group) - 1) // 2
            counts = Counter(labels[item.article.link]["event"] for item in group)
            true_pairs += sum(n * (n - 1) // 2 for n in counts.values())
        result["pair_precision"] = true_pairs / predicted_pairs if predicted_pairs else None
        result["pair_recall_among_kept"] = true_pairs / expected_pairs if expected_pairs else None
        result["groups"] = [
            {"representative": rep.article.title, "sector": rep.sectors[0],
             "events": dict(Counter(labels[item.article.link]["event"] for item in groups[rep.article.cluster_id])),
             "titles": [item.article.title for item in groups[rep.article.cluster_id]],
             "fingerprints": [_issue_fingerprint(item) for item in groups[rep.article.cluster_id]]}
            for rep in reps
        ]
    else:
        result["largest_cluster_samples"] = [
            {"representative": rep.article.title, "sector": rep.sectors[0],
             "size": len(groups[rep.article.cluster_id])}
            for rep in sorted(reps, key=lambda item: len(groups[item.article.cluster_id]), reverse=True)[:3]
        ]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, nargs="*", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {"golden": evaluate(load_rows(FIXTURE))}
    result["golden_recorded_keep_clustering"] = evaluate(load_rows(FIXTURE), recorded=True)
    for path in args.candidates:
        rows = load_rows(path)
        result[path.name] = {
            "recorded_keep_clustering": evaluate(rows, recorded=True),
            "rescored_replay": evaluate(rows),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({name: {key: value for key, value in data.items() if key != "groups"} for name, data in result.items()}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
