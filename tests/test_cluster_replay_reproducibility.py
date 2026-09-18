"""A fresh clone of a squashed history must not require transient PR commits."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from scripts.evaluate_cluster_review import BASE

ROOT = Path(__file__).resolve().parents[1]


def test_replay_runs_in_fresh_clone_without_unsquashed_pr_objects(tmp_path: Path) -> None:
    # Keep linked-worktree environment overrides out of the isolated repositories.
    env = {k: v for k, v in os.environ.items()
           if k not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "PYTHONPATH"}}
    env.update(GIT_AUTHOR_NAME="Replay Test", GIT_AUTHOR_EMAIL="replay@example.test",
               GIT_COMMITTER_NAME="Replay Test", GIT_COMMITTER_EMAIL="replay@example.test")

    def git(*args: str, cwd: Path) -> str:
        return subprocess.check_output(["git", *args], cwd=cwd, env=env, encoding="utf-8", stderr=subprocess.STDOUT).strip()

    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", cwd=seed)
    baseline_file = seed / "src/pipeline/issue_cluster.py"
    baseline_file.parent.mkdir(parents=True)
    # Preserve the real main implementation without copying its entire report history.
    baseline_file.write_text(subprocess.check_output(
        ["git", "show", f"{BASE}:src/pipeline/issue_cluster.py"], cwd=ROOT, encoding="utf-8"), encoding="utf-8")
    git("add", ".", cwd=seed)
    git("commit", "-m", "baseline", cwd=seed)
    baseline = git("rev-parse", "HEAD", cwd=seed)
    for directory in ("src", "scripts", "tests/fixtures/loan_news"):
        shutil.copytree(ROOT / directory, seed / directory, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / "queries.yml", seed / "queries.yml")
    # Tiny deterministic CSVs exercise the daily CLI path as well as both labelled fixtures.
    rows = json.loads((ROOT / "tests/fixtures/loan_news/2026-09-17.json").read_text(encoding="utf-8-sig"))[:5]
    fields = ("title", "summary", "url", "score", "prob", "keep")
    candidates = seed / "reports/_candidates"
    candidates.mkdir(parents=True)
    for date in ("2026-09-15", "2026-09-16", "2026-09-17"):
        with (candidates / f"{date}_candidates.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    git("add", ".", cwd=seed)
    git("commit", "-m", "squashed changes", cwd=seed)
    clone = tmp_path / "fresh"
    git("clone", "--no-local", str(seed), str(clone), cwd=tmp_path)
    for revision in ("1b4b732c0f4005a7fab80ced58a7fbc9ff658e9e", "3de826ac4f7f31a53a6b19fa16e8eb8a8da8c715"):
        assert subprocess.run(["git", "cat-file", "-e", revision], cwd=clone, env=env,
                              capture_output=True).returncode != 0
    # Only the baseline's object identity differs in the compact two-commit history.
    command = ("from scripts import evaluate_cluster_review as e; "
               f"e.BASE={baseline!r}; e.main()")
    result = subprocess.run([sys.executable, "-c", command, "--output", "result.json"],
                            cwd=clone, env=env, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    output = json.loads((clone / "result.json").read_text(encoding="utf-8"))
    assert len(output["days"]) == 3
    assert "first_revision" not in output
    assert output["base_revision"] == baseline
    assert output["golden_fixed_relevant_cohort"]["revised"]["pair_precision"] == 1
    assert output["other_sectors_labelled"]["revised"]["pair_precision"] == 1
