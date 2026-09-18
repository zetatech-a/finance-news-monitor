"""Main-baseline versus current fixed-cohort replay (offline).

Only the durable main ancestor is required. Unsquashed first-PR ablations are
historical artifacts in docs/analysis/loan-news/review-replay.json, not rerun.
An optional --compare-revision adds a locally available revision for follow-up
audits; it is never required by the default path. Use a full-history clone.
No model inference or report writes. Pair identity uses retained CSV row index.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from itertools import combinations
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from unittest.mock import patch

from scripts import evaluate_loan_news as replay
from src.pipeline import issue_cluster as current, text_matcher

BASE = "e67e3a740b0004e56a0087aa34edfeaffc6264ae"


def snapshot(revision: str) -> ModuleType:
    source = subprocess.check_output(
        ["git", "show", f"{revision}:src/pipeline/issue_cluster.py"], encoding="utf-8")
    name = "_cluster_replay_" + revision[:8]
    module = ModuleType(name)
    sys.modules[name] = module
    exec(compile(source, name, "exec"), module.__dict__)
    return module


def prepare(rows, *, original_aliases=False, recorded=True):
    aliases = {k: v for k, v in text_matcher._TERM_ALIASES.items()
               if not original_aliases or k in {"cp", "킥스"}}
    with patch.dict(text_matcher._TERM_ALIASES, aliases, clear=True), patch.object(
        replay, "cluster_tagged_articles", lambda tagged: []
    ):
        return replay.replay(rows, recorded=recorded)[0]


def measure(module, tagged, labels=None):
    representatives = module.cluster_tagged_articles(tagged)
    groups = defaultdict(list)
    index = {id(item): n for n, item in enumerate(tagged)}
    for item in tagged:
        groups[item.article.cluster_id].append(index[id(item)])
    clusters = list(groups.values())
    pairs = {pair for group in clusters for pair in combinations(sorted(group), 2)}
    ordered = sorted(representatives, key=lambda r: (-len(groups[r.article.cluster_id]), r.article.title))
    result = {
        "kept_count": len(tagged), "cluster_count": len(clusters),
        "largest_cluster": max(map(len, clusters), default=0),
        "clusters_ge_50": sum(len(g) >= 50 for g in clusters),
        "sector_representatives": dict(sorted(Counter(module._primary_sector(r) for r in representatives).items())),
        "top10": [],
    }
    for rep in ordered[:10]:
        members = groups[rep.article.cluster_id]
        result["top10"].append({
            "representative": rep.article.title, "sector": module._primary_sector(rep),
            "size": len(members), "fingerprint": module._issue_fingerprint(rep),
            "fingerprint_counts": dict(Counter(module._issue_fingerprint(tagged[i]) or "none" for i in members)),
            "member_indices": sorted(members),
            "member_title_sample": [tagged[i].article.title for i in sorted(members)[:8]],
        })
    if labels:
        expected = {pair for pair in combinations(range(len(tagged)), 2)
                    if labels[tagged[pair[0]].article.link] == labels[tagged[pair[1]].article.link]}
        correct = len(pairs & expected)
        result.update(pair_precision=correct / len(pairs) if pairs else None,
                      pair_recall=correct / len(expected) if expected else None,
                      true_pairs=correct, predicted_pairs=len(pairs), expected_pairs=len(expected))
    return result, pairs, clusters


def changes(before, after, tagged):
    merged, split = after - before, before - after
    def sample(pairs):
        return [[tagged[a].article.title, tagged[b].article.title] for a, b in sorted(pairs)[:8]]
    return {"newly_merged_pairs": len(merged), "newly_split_pairs": len(split),
            "merged_pair_sample": sample(merged), "split_pair_sample": sample(split),
            "merged_pairs_by_sector": dict(Counter(" / ".join(sorted({current._primary_sector(tagged[a]), current._primary_sector(tagged[b])})) for a, b in merged)),
            "split_pairs_by_sector": dict(Counter(" / ".join(sorted({current._primary_sector(tagged[a]), current._primary_sector(tagged[b])})) for a, b in split))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare-revision", help="Optional locally available pre-edit revision; not needed after squash")
    args = parser.parse_args()
    modules = {"base": snapshot(BASE), "revised": current}
    result = {"base_revision": BASE, "days": {}}
    if args.compare_revision:
        modules["comparison"] = snapshot(args.compare_revision)
        result["comparison_revision"] = args.compare_revision
    for date in ("2026-09-15", "2026-09-16", "2026-09-17"):
        rows = replay.load_rows(Path(f"reports/_candidates/{date}_candidates.csv"))
        day, measured = {}, {}
        for name, module in modules.items():
            tagged = prepare(rows, original_aliases=name == "base")
            measured[name] = measure(module, tagged)
            day[name] = measured[name][0]
        tagged = prepare(rows)
        day["base_to_revised"] = changes(measured["base"][1], measured["revised"][1], tagged)
        if "comparison" in measured:
            day["comparison_to_revised"] = changes(measured["comparison"][1], measured["revised"][1], tagged)
        rescored = prepare(rows, recorded=False)
        day["revised_rescored"] = measure(current, rescored)[0]
        result["days"][date] = day
        print(date, {name: {k: v for k, v in day[name].items() if k != "top10"}
                     for name in modules}, flush=True)
    rows = replay.load_rows(replay.FIXTURE)
    labels = {row["url"]: row["event"] for row in rows}
    result["golden_fixed_relevant_cohort"] = {}
    # Compare all labelled relevant items, including ones base relevance dropped.
    rows = [dict(row, keep=1) for row in rows if row["relevant"]]
    for name, module in modules.items():
        result["golden_fixed_relevant_cohort"][name] = measure(module, prepare(rows, original_aliases=name == "base"), labels)[0]
    other_rows = replay.load_rows(Path("tests/fixtures/loan_news/other_sectors_review.json"))
    labels = {row["url"]: row["event"] for row in other_rows}
    result["other_sectors_labelled"] = {
        name: measure(module, prepare(other_rows, original_aliases=name == "base"), labels)[0]
        for name, module in modules.items()
    }
    result["golden_revised_end_to_end"] = replay.evaluate(replay.load_rows(replay.FIXTURE))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
