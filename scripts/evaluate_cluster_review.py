"""PR #85 fixed-cohort replay and one-factor clustering ablations (offline).

Historical modules are read with git show, never checked out over the worktree.
No model inference or generated report writes. Pair identity uses CSV row index.
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
FIRST = "1b4b732c0f4005a7fab80ced58a7fbc9ff658e9e"


def snapshot(revision, suffix="", transform=None):
    source = subprocess.check_output(
        ["git", "show", f"{revision}:src/pipeline/issue_cluster.py"], encoding="utf-8")
    if transform:
        source = transform(source)
    name = "_cluster_replay_" + revision[:8] + suffix
    module = ModuleType(name)
    sys.modules[name] = module
    exec(compile(source, name, "exec"), module.__dict__)
    return module


def replace_once(source, old, new):
    assert source.count(old) == 1, old
    return source.replace(old, new)


def input_order(source):
    start = source.index("    order = sorted(range(len(tagged))")
    end = source.index("    for idx in order:", start)
    return source[:start] + "    order = range(len(tagged))\n" + source[end:]


def single_link(source):
    return replace_once(source, "if all(_should_cluster_features", "if any(_should_cluster_features")


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
    args = parser.parse_args()
    modules = {"base": snapshot(BASE), "first": snapshot(FIRST), "revised": current}
    variants = {
        "first_no_metric": snapshot(FIRST, "_no_metric"),
        "first_input_order": snapshot(FIRST, "_input", input_order),
        "first_single_link": snapshot(FIRST, "_single", single_link),
        "first_input_single": snapshot(FIRST, "_input_single", lambda s: single_link(input_order(s))),
        "first_old_fingerprint": snapshot(FIRST, "_old_fp"),
    }
    variants["first_no_metric"]._reported_metrics = lambda title: set()
    variants["first_old_fingerprint"]._issue_fingerprint = modules["base"]._issue_fingerprint
    result = {"base_revision": BASE, "first_revision": FIRST, "days": {}}
    for date in ("2026-09-15", "2026-09-16", "2026-09-17"):
        rows = replay.load_rows(Path(f"reports/_candidates/{date}_candidates.csv"))
        day, measured = {}, {}
        for name, module in modules.items():
            tagged = prepare(rows, original_aliases=name == "base")
            measured[name] = measure(module, tagged)
            day[name] = measured[name][0]
        tagged = prepare(rows)
        day["base_to_first"] = changes(measured["base"][1], measured["first"][1], tagged)
        day["base_to_revised"] = changes(measured["base"][1], measured["revised"][1], tagged)
        day["first_to_revised"] = changes(measured["first"][1], measured["revised"][1], tagged)
        rescored = prepare(rows, recorded=False)
        day["revised_rescored"] = measure(current, rescored)[0]
        if date == "2026-09-17":
            day["ablations"] = {}
            for name, module in variants.items():
                summary, pairs, _ = measure(module, tagged)
                day["ablations"][name] = {k: v for k, v in summary.items() if k != "top10"}
                day["ablations"][name]["changed_pairs_vs_first"] = len(pairs ^ measured["first"][1])
            # Isolate alias/tagging changes from clustering.
            summary, pairs, _ = measure(modules["base"], tagged)
            day["ablations"]["base_current_tags"] = {k: v for k, v in summary.items() if k != "top10"}
            day["ablations"]["base_current_tags"]["changed_pairs_vs_base"] = len(pairs ^ measured["base"][1])
            largest = set(max(measured["first"][2], key=len))
            old_largest = set(max(measured["base"][2], key=len))
            day["largest_transition"] = {
                "retained": len(largest & old_largest),
                "added": [{"index": i, "title": tagged[i].article.title,
                           "fingerprint": modules["first"]._issue_fingerprint(tagged[i]),
                           "old_cluster_size": next(len(g) for g in measured["base"][2] if i in g)} for i in sorted(largest - old_largest)],
                "removed": [tagged[i].article.title for i in sorted(old_largest - largest)],
            }
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
