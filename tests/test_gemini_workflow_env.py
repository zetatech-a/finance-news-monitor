"""daily.yml / smoke.yml의 Gemini 환경변수 배선 검증 (실제 워크플로 파일 파싱)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
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
def test_kill_switch_is_wired_as_a_repository_variable(workflow_name):
    """GEMINI_ENABLED=0으로 끌 수 있어야 한다 — 배선이 없으면 문서상의 kill switch가
    실제로는 동작하지 않아, 키를 지우거나 워크플로를 고쳐야만 중단할 수 있다."""
    env = _run_step_env(_load(workflow_name), "src.run_daily")
    assert env["GEMINI_ENABLED"] == "${{ vars.GEMINI_ENABLED }}"


def test_unset_kill_switch_keeps_the_feature_on(monkeypatch):
    """미설정 variable은 빈 문자열로 전달된다 — 기본값(활성)이 유지돼야 한다."""
    monkeypatch.setenv("GEMINI_ENABLED", "")
    assert load_gemini_config().enabled is True
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    assert load_gemini_config().enabled is False


def _config_env_names() -> set[str]:
    """`load_gemini_config()`이 실제로 읽는 GEMINI_* 환경변수 이름을 코드에서 뽑는다."""
    source = Path("src/pipeline/gemini_summary.py").read_text(encoding="utf-8")
    return set(re.findall(r'"(GEMINI_[A-Z0-9_]+)"', source))


@pytest.mark.parametrize("workflow_name", ["daily.yml", "smoke.yml"])
def test_every_config_variable_is_wired(workflow_name):
    """코드가 읽는 knob은 전부 워크플로 env에 있어야 한다.

    GitHub repository variable은 자동으로 주입되지 않는다 — env에 없으면 변수를
    설정해도 무시되고, 워크플로를 고쳐야만 조정할 수 있다(문서와 실제가 어긋난다).
    """
    env = _run_step_env(_load(workflow_name), "src.run_daily")
    missing = sorted(name for name in _config_env_names() if name not in env)
    assert not missing, f"{workflow_name}: {missing} 미배선"


@pytest.mark.parametrize("workflow_name", ["daily.yml", "smoke.yml"])
def test_validation_and_timeout_knobs_come_from_variables(workflow_name):
    env = _run_step_env(_load(workflow_name), "src.run_daily")
    for name in (
        "GEMINI_MAX_LINE_CHARS",
        "GEMINI_INPUT_MIN_CHARS",
        "GEMINI_ARTICLE_MAX_CHARS",
        "GEMINI_REQUEST_TIMEOUT_SECONDS",
        "GEMINI_RETRY_ATTEMPTS",
        "GEMINI_CIRCUIT_BREAKER_FAILURES",
        "GEMINI_BATCH_HARD_MAX_ARTICLES",
    ):
        assert env[name] == "${{ vars.%s }}" % name


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

    assert inputs["gemini_max_summaries"]["default"] == "50"
    assert inputs["gemini_batch_max_articles"]["default"] == "25"
    assert inputs["gemini_max_requests"]["default"] == "4"
    assert inputs["gemini_max_fetch_attempts"]["default"] == "50"

    config = load_gemini_config()  # 코드 기본값
    assert int(inputs["gemini_max_summaries"]["default"]) < config.max_summaries
    assert int(inputs["gemini_max_requests"]["default"]) < config.max_requests_per_run
    assert int(inputs["gemini_max_fetch_attempts"]["default"]) < config.max_fetch_attempts
    # 스모크도 실 API로 검증된 배치 크기를 그대로 쓴다.
    assert int(inputs["gemini_batch_max_articles"]["default"]) == config.batch_max_articles


def test_smoke_defaults_produce_two_normal_requests_for_fifty_articles():
    """50건 스모크의 정상 기대값은 requests=2, normal_requests=2다."""
    import math

    workflow = _load("smoke.yml")
    inputs = (workflow.get("on") or workflow[True])["workflow_dispatch"]["inputs"]
    articles = int(inputs["gemini_max_summaries"]["default"])
    batch = int(inputs["gemini_batch_max_articles"]["default"])
    expected = math.ceil(articles / batch)

    assert expected == 2
    # 요청 상한이 정상 경로(2회)보다 커야 복구 여유가 남는다.
    assert int(inputs["gemini_max_requests"]["default"]) > expected


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


# --- smoke artifact 업로드 -----------------------------------------------

ARTIFACT_DIR = "smoke-artifact"
FORBIDDEN_REPORT_SUBDIRS = ["reports/_cache", "reports/_candidates", "reports/_metrics"]


def _step(workflow: dict, name: str) -> dict:
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if step.get("name") == name:
                return step
    raise AssertionError(f"step {name!r} not found")


def _upload_step() -> dict:
    return _step(_load("smoke.yml"), "Upload smoke report")


def test_upload_artifact_action_version_and_settings():
    step = _upload_step()
    assert step["uses"] == "actions/upload-artifact@v4"
    with_ = step["with"]
    assert with_["name"] == "gemini-smoke-report"
    assert with_["retention-days"] == 1
    assert with_["if-no-files-found"] == "error"


def test_artifact_uploads_only_the_dedicated_directory():
    path = str(_upload_step()["with"]["path"]).strip()
    assert path.rstrip("/") == ARTIFACT_DIR
    # reports/ 전체를 올리면 캐시·후보 CSV·메트릭이 통째로 따라간다.
    assert not path.startswith("reports")


def test_artifact_never_includes_cache_candidates_or_metrics():
    workflow = _load("smoke.yml")
    upload = _upload_step()
    collect = _step(workflow, "Collect smoke report")

    for forbidden in FORBIDDEN_REPORT_SUBDIRS:
        assert forbidden not in str(upload["with"]["path"])
        # 복사 소스로도 등장하면 안 된다.
        assert f"cp {forbidden}" not in collect["run"]
        assert f'cp "{forbidden}' not in collect["run"]

    # reports/ 최상위만 훑어 하위 디렉터리를 구조적으로 배제한다.
    assert "-maxdepth 1" in collect["run"]
    assert "-type f" in collect["run"]


def test_collect_step_selects_only_dated_reports_from_this_run():
    collect = _step(_load("smoke.yml"), "Collect smoke report")
    # 이번 실행에서 생성·수정된 것만 (오래된 리포트 업로드 방지)
    assert "-newer" in collect["run"]
    assert "smoke-start-marker" in collect["run"]
    # 날짜 파일명 패턴만 — index.html 등은 제외된다
    assert "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].html" in collect["run"]
    assert "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md" in collect["run"]


def test_start_marker_is_created_before_the_daily_run():
    steps = [s for job in _load("smoke.yml")["jobs"].values() for s in job["steps"]]
    names = [s.get("name") for s in steps]
    assert names.index("Mark run start") < names.index("Run daily (naver)")
    assert "smoke-start-marker" in _step(_load("smoke.yml"), "Mark run start")["run"]


def test_artifact_steps_run_even_when_the_pipeline_fails():
    workflow = _load("smoke.yml")
    for name in ("Collect smoke report", "Upload smoke report"):
        assert _step(workflow, name).get("if") == "always()"


def test_artifact_steps_never_touch_secrets():
    workflow = _load("smoke.yml")
    for name in ("Mark run start", "Collect smoke report", "Upload smoke report"):
        blob = str(_step(workflow, name))
        assert "secrets." not in blob
        assert "GEMINI_API_KEY" not in blob
        assert "NCP_APIGW" not in blob


def test_no_step_dumps_the_environment():
    """환경변수 dump는 API 키를 통째로 artifact/로그에 흘린다."""
    dump_patterns = [
        "printenv",
        "env > ",
        "env >>",
        "env |",
        "$(env)",
        "toJSON(env)",
        "toJSON(secrets)",
        "set -o posix",
        "declare -x",
        "export -p",
    ]
    for job in _load("smoke.yml")["jobs"].values():
        for step in job["steps"]:
            run = str(step.get("run", ""))
            for pattern in dump_patterns:
                assert pattern not in run, f"{step.get('name')}: {pattern!r}"


def test_collect_step_does_not_print_report_contents():
    collect = _step(_load("smoke.yml"), "Collect smoke report")["run"]
    # 파일명만 나열한다 — cat/head/tail로 본문을 찍지 않는다.
    assert "ls -1 smoke-artifact/" in collect
    for leaky in ("cat ", "head ", "tail ", "grep -r"):
        assert leaky not in collect


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash 필요")
def test_collect_script_behaviour_end_to_end(tmp_path):
    """워크플로의 collect 스크립트를 실제로 실행해 선택 규칙을 검증한다."""
    script = _step(_load("smoke.yml"), "Collect smoke report")["run"]

    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    reports = tmp_path / "reports"
    for sub in ("_cache", "_candidates", "_metrics"):
        (reports / sub).mkdir(parents=True)

    # 이전 실행이 남긴 리포트 + 민감 디렉터리
    (reports / "2026-07-01.html").write_text("old", encoding="utf-8")
    (reports / "2026-07-01.md").write_text("old", encoding="utf-8")
    (reports / "_cache" / "gemini_summary_cache.json").write_text("{}", encoding="utf-8")
    (reports / "_candidates" / "2026-08-03_candidates.csv").write_text("x", encoding="utf-8")
    (reports / "_metrics" / "2026-08-03_quality_metrics.json").write_text("{}", encoding="utf-8")

    time.sleep(1.1)
    (runner_temp / "smoke-start-marker").touch()
    time.sleep(1.1)

    # 이번 실행이 만든 리포트 + 갱신된 index/캐시
    (reports / "2026-08-03.html").write_text("new", encoding="utf-8")
    (reports / "2026-08-03.md").write_text("new", encoding="utf-8")
    (reports / "index.html").write_text("idx", encoding="utf-8")
    (reports / "_cache" / "gemini_summary_cache.json").write_text('{"v":1}', encoding="utf-8")

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env={**os.environ, "RUNNER_TEMP": str(runner_temp)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    collected = sorted(p.name for p in (tmp_path / ARTIFACT_DIR).iterdir())
    assert collected == ["2026-08-03.html", "2026-08-03.md"]
    assert "collected_files=2" in result.stdout
    # 리포트 본문이 로그로 새지 않는다.
    assert "new" not in result.stdout.replace("2026-08-03", "")


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash 필요")
def test_collect_script_uploads_nothing_when_the_run_produced_no_report(tmp_path):
    """파이프라인이 리포트를 만들기 전에 실패하면 오래된 리포트를 올리지 않는다."""
    script = _step(_load("smoke.yml"), "Collect smoke report")["run"]

    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "2026-07-01.html").write_text("old", encoding="utf-8")
    (reports / "2026-07-01.md").write_text("old", encoding="utf-8")

    time.sleep(1.1)
    (runner_temp / "smoke-start-marker").touch()

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env={**os.environ, "RUNNER_TEMP": str(runner_temp)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert list((tmp_path / ARTIFACT_DIR).iterdir()) == []
    assert "collected_files=0" in result.stdout
    # 비어 있으면 upload-artifact의 if-no-files-found: error가 실패로 드러낸다.
    assert _upload_step()["with"]["if-no-files-found"] == "error"


# --- smoke strict 검증 --------------------------------------------------------


def test_smoke_writes_a_sanitized_run_summary_json():
    env = _run_step_env(_load("smoke.yml"), "src.run_daily")
    assert env["GEMINI_RUN_SUMMARY_PATH"] == "${{ runner.temp }}/gemini-run-summary.json"


def test_daily_does_not_enable_the_run_summary_json():
    """daily는 이 변수를 설정하지 않아야 한다 — 동작이 달라지면 안 된다."""
    env = _run_step_env(_load("daily.yml"), "src.run_daily")
    assert "GEMINI_RUN_SUMMARY_PATH" not in env


def test_smoke_has_a_strict_verification_step_after_the_run():
    steps = [s for job in _load("smoke.yml")["jobs"].values() for s in job["steps"]]
    names = [s.get("name") for s in steps]
    assert "Verify Gemini smoke result" in names
    assert names.index("Run daily (naver)") < names.index("Verify Gemini smoke result")

    verify = _step(_load("smoke.yml"), "Verify Gemini smoke result")
    # 자유 형식 로그 grep이 아니라 집계 JSON을 읽는다.
    assert "scripts/check_gemini_smoke.py" in verify["run"]
    assert "gemini-run-summary.json" in verify["run"]
    for leaky in ("grep ", "| grep", "GEMINI_API_KEY", "secrets."):
        assert leaky not in str(verify)
    # 이 스텝은 always()가 아니다 — 실패하면 job이 빨간불이 되어야 한다.
    assert verify.get("if") is None


def test_artifact_upload_still_runs_after_a_failed_verification():
    """strict 검증이 실패해도 artifact는 올라가야 원인을 볼 수 있다."""
    workflow = _load("smoke.yml")
    steps = [s for job in workflow["jobs"].values() for s in job["steps"]]
    names = [s.get("name") for s in steps]
    assert names.index("Verify Gemini smoke result") < names.index("Upload smoke report")
    for name in ("Collect smoke report", "Upload smoke report"):
        assert _step(workflow, name).get("if") == "always()"


def test_daily_workflow_has_no_gemini_strict_check():
    """daily는 Gemini 0건이어도 기존 요약으로 성공해야 한다."""
    workflow = _load("daily.yml")
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            assert "check_gemini_smoke" not in str(step.get("run", ""))
