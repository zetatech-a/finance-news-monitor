"""daily.yml / smoke.yml의 Gemini 환경변수 배선 검증 (실제 워크플로 파일 파싱)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.pipeline.gemini_summary import load_gemini_config

WORKFLOWS = Path(".github/workflows")

# Repository Variable로 넘겨야 하는 처리량 변수들.
THROUGHPUT_VARS = [
    "GEMINI_MAX_SUMMARIES",
    "GEMINI_BATCH_MAX_ARTICLES",
    "GEMINI_BATCH_MAX_INPUT_CHARS",
    "GEMINI_MAX_REQUESTS_PER_RUN",
    "GEMINI_MAX_RECOVERY_REQUESTS",
    "GEMINI_MAX_FETCH_ATTEMPTS",
    "GEMINI_MIN_INTERVAL_SECONDS",
]


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _run_step_env(workflow: dict, needle: str) -> dict[str, str]:
    """`python -m src.run_daily`를 실행하는 스텝의 env 블록을 찾는다."""
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if needle in str(step.get("run", "")):
                return step.get("env", {}) or {}
    raise AssertionError(f"step running {needle!r} not found")


@pytest.mark.parametrize("workflow_name", ["daily.yml", "smoke.yml"])
def test_workflow_yaml_is_parseable(workflow_name):
    assert _load(workflow_name)["jobs"]


@pytest.mark.parametrize("workflow_name", ["daily.yml", "smoke.yml"])
def test_api_key_comes_from_secrets_not_variables(workflow_name):
    env = _run_step_env(_load(workflow_name), "src.run_daily")
    assert env["GEMINI_API_KEY"] == "${{ secrets.GEMINI_API_KEY }}"
    # 키가 평문이나 repository variable로 새면 안 된다.
    assert "vars.GEMINI_API_KEY" not in str(env)


@pytest.mark.parametrize("workflow_name", ["daily.yml", "smoke.yml"])
def test_all_throughput_vars_are_wired(workflow_name):
    env = _run_step_env(_load(workflow_name), "src.run_daily")
    for name in THROUGHPUT_VARS:
        assert name in env, f"{workflow_name}: {name} 미배선"
        assert env[name].startswith("${{"), f"{workflow_name}: {name} 하드코딩됨"


def test_daily_takes_every_throughput_var_from_repository_variables():
    env = _run_step_env(_load("daily.yml"), "src.run_daily")
    for name in THROUGHPUT_VARS:
        assert env[name] == "${{ vars.%s }}" % name
    assert env["GEMINI_MODEL"] == "${{ vars.GEMINI_MODEL }}"


def test_smoke_inputs_have_small_explicit_defaults():
    """스모크가 실수로 운영 기본값(300건)을 태우지 못하게 한다."""
    # PyYAML은 `on:`을 boolean True로 파싱한다.
    workflow = _load("smoke.yml")
    trigger = workflow.get("on") or workflow[True]
    inputs = trigger["workflow_dispatch"]["inputs"]

    assert inputs["gemini_max_summaries"]["default"] == "5"
    assert inputs["gemini_batch_max_articles"]["default"] == "5"
    assert inputs["gemini_max_requests"]["default"] == "1"
    assert inputs["gemini_max_fetch_attempts"]["default"] == "5"

    config = load_gemini_config()  # 코드 기본값
    assert int(inputs["gemini_max_summaries"]["default"]) < config.max_summaries
    assert int(inputs["gemini_max_requests"]["default"]) < config.max_requests_per_run
    assert int(inputs["gemini_max_fetch_attempts"]["default"]) < config.max_fetch_attempts


def test_smoke_throughput_inputs_win_over_repository_variables():
    env = _run_step_env(_load("smoke.yml"), "src.run_daily")
    assert env["GEMINI_MAX_SUMMARIES"] == "${{ inputs.gemini_max_summaries }}"
    assert env["GEMINI_BATCH_MAX_ARTICLES"] == "${{ inputs.gemini_batch_max_articles }}"
    assert env["GEMINI_MAX_REQUESTS_PER_RUN"] == "${{ inputs.gemini_max_requests }}"
    assert env["GEMINI_MAX_FETCH_ATTEMPTS"] == "${{ inputs.gemini_max_fetch_attempts }}"
    # input이 없는 나머지는 repository variable을 따른다.
    assert env["GEMINI_BATCH_MAX_INPUT_CHARS"] == "${{ vars.GEMINI_BATCH_MAX_INPUT_CHARS }}"
    assert env["GEMINI_MAX_RECOVERY_REQUESTS"] == "${{ vars.GEMINI_MAX_RECOVERY_REQUESTS }}"
    assert env["GEMINI_MIN_INTERVAL_SECONDS"] == "${{ vars.GEMINI_MIN_INTERVAL_SECONDS }}"


@pytest.mark.parametrize("name", THROUGHPUT_VARS + ["GEMINI_MODEL"])
def test_unset_variable_falls_back_to_the_code_default(monkeypatch, name):
    """미설정 variable은 빈 문자열로 전달된다 — 코드 기본값이 그대로 쓰여야 한다."""
    for var in THROUGHPUT_VARS + ["GEMINI_MODEL"]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-a-real-credential")
    defaults = load_gemini_config()

    monkeypatch.setenv(name, "")  # GitHub이 미설정 variable을 넘기는 방식
    assert load_gemini_config() == defaults


def test_smoke_run_step_echoes_limits_without_secrets():
    workflow = _load("smoke.yml")
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            run = str(step.get("run", ""))
            if "src.run_daily" in run:
                assert "GEMINI_API_KEY" not in run  # 키를 echo하지 않는다
                assert "$GEMINI_MAX_SUMMARIES" in run  # 적용된 상한은 로그로 확인 가능
                return
    raise AssertionError("smoke run step not found")
