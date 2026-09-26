"""Unit tests for `runners.jest` — command assembly + report parsing.
Mocks safe_run; never actually invokes npx/jest."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from gomore_qa_master.runners import jest


@pytest.fixture
def runner():
    return jest.JestRunner()


def _fake_completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# ---- list_tests --------------------------------------------------------------


def test_list_tests_invokes_list_tests_flag(runner, monkeypatch, tmp_path):
    captured = {}

    def fake_safe_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed(stdout="test list\n")
    monkeypatch.setattr(jest, "safe_run", fake_safe_run)
    monkeypatch.setattr(jest, "PROJECT_ROOT", tmp_path)
    out = runner.list_tests()
    assert captured["cmd"] == ["npx", "jest", "--listTests"]
    assert out == "test list\n"


def test_list_tests_falls_back_to_stderr_when_stdout_empty(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(jest, "safe_run", lambda cmd, **kwargs: _fake_completed(stdout="", stderr="err"))
    monkeypatch.setattr(jest, "PROJECT_ROOT", tmp_path)
    assert runner.list_tests() == "err"


# ---- run_tests / run_failed command assembly ---------------------------------


def test_run_tests_includes_json_and_output_file(runner, monkeypatch, tmp_path):
    report = tmp_path / "report.json"
    captured = {}

    def fake_safe_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed()
    monkeypatch.setattr(jest, "safe_run", fake_safe_run)
    monkeypatch.setattr(jest, "REPORT_PATH", report)
    monkeypatch.setattr(jest, "PROJECT_ROOT", tmp_path)

    runner.run_tests()
    assert "--json" in captured["cmd"]
    assert f"--outputFile={report}" in captured["cmd"]


def test_run_tests_adds_filter_as_dash_t(runner, monkeypatch, tmp_path):
    captured = {}

    def fake_safe_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed()
    monkeypatch.setattr(jest, "safe_run", fake_safe_run)
    monkeypatch.setattr(jest, "REPORT_PATH", tmp_path / "report.json")
    monkeypatch.setattr(jest, "PROJECT_ROOT", tmp_path)

    runner.run_tests(filter="renders correctly")
    idx = captured["cmd"].index("-t")
    assert captured["cmd"][idx + 1] == "renders correctly"


def test_run_tests_truncates_tails(runner, monkeypatch, tmp_path):
    # Distinguishable head/tail content so the assertions prove *which end*
    # survives truncation, not just the resulting length.
    stdout = "H" * 1000 + "T" * 2000
    stderr = "H" * 1500 + "T" * 1000
    monkeypatch.setattr(jest, "safe_run", lambda cmd, **kwargs: _fake_completed(
        returncode=1, stdout=stdout, stderr=stderr))
    monkeypatch.setattr(jest, "REPORT_PATH", tmp_path / "report.json")
    monkeypatch.setattr(jest, "PROJECT_ROOT", tmp_path)
    result = runner.run_tests()
    assert result["exit_code"] == 1
    assert len(result["stdout_tail"]) == 2000
    assert result["stdout_tail"] == "T" * 2000
    assert "H" not in result["stdout_tail"]
    assert len(result["stderr_tail"]) == 1000
    assert result["stderr_tail"] == "T" * 1000
    assert "H" not in result["stderr_tail"]


def test_run_failed_uses_only_failures_flag(runner, monkeypatch, tmp_path):
    captured = {}

    def fake_safe_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed()
    monkeypatch.setattr(jest, "safe_run", fake_safe_run)
    monkeypatch.setattr(jest, "REPORT_PATH", tmp_path / "report.json")
    monkeypatch.setattr(jest, "PROJECT_ROOT", tmp_path)
    runner.run_failed()
    assert "--onlyFailures" in captured["cmd"]


# ---- get_report_summary -------------------------------------------------------


def test_get_report_summary_missing_file(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(jest, "REPORT_PATH", tmp_path / "missing.json")
    result = runner.get_report_summary()
    assert "error" in result


def test_get_report_summary_maps_jest_fields(runner, monkeypatch, tmp_path):
    # Deliberately not asserting the `duration` value here — see
    # test_get_report_summary_duration_is_actually_start_time_not_elapsed
    # below: src currently reports raw `startTime` (an epoch-ms timestamp)
    # as "duration", which is wrong, and we don't want to lock that bug in.
    p = tmp_path / "report.json"
    p.write_text(json.dumps({
        "numTotalTests": 10, "numPassedTests": 7, "numFailedTests": 2,
        "numPendingTests": 1, "startTime": 123456,
    }))
    monkeypatch.setattr(jest, "REPORT_PATH", p)
    result = runner.get_report_summary()
    assert result["total"] == 10
    assert result["passed"] == 7
    assert result["failed"] == 2
    assert result["skipped"] == 1


@pytest.mark.xfail(
    strict=True,
    reason=(
        "jest.py get_report_summary() 回傳 data['startTime']（epoch 毫秒的"
        "起始時間戳）當作 duration，語意錯誤；正確應為 endTime - startTime"
        "（jest --json 報告同時提供兩者）。修 src 後移除此標記。"
    ),
)
def test_get_report_summary_duration_is_actually_start_time_not_elapsed(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({
        "numTotalTests": 1, "numPassedTests": 1, "numFailedTests": 0,
        "numPendingTests": 0, "startTime": 1000, "endTime": 1500,
    }))
    monkeypatch.setattr(jest, "REPORT_PATH", p)
    result = runner.get_report_summary()
    assert result["duration"] == 1500 - 1000


# ---- get_failure_details -------------------------------------------------------


def test_get_failure_details_missing_file(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(jest, "REPORT_PATH", tmp_path / "missing.json")
    assert runner.get_failure_details() == [{"error": "找不到報告"}]


def test_get_failure_details_extracts_failed_assertions(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({
        "testResults": [{
            "name": "suite.test.js",
            "assertionResults": [
                {"status": "failed", "fullName": "renders error",
                 "failureMessages": ["Expected true, got false"], "duration": 12},
                {"status": "passed", "fullName": "renders ok",
                 "failureMessages": [], "duration": 5},
            ],
        }],
    }))
    monkeypatch.setattr(jest, "REPORT_PATH", p)
    result = runner.get_failure_details()
    assert len(result) == 1
    assert result[0]["nodeid"] == "suite.test.js::renders error"
    assert "Expected true" in result[0]["message"]


def test_get_failure_details_filters_by_test_id(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({
        "testResults": [{
            "name": "suite.test.js",
            "assertionResults": [
                {"status": "failed", "fullName": "case a", "failureMessages": ["x"], "duration": 1},
                {"status": "failed", "fullName": "case b", "failureMessages": ["y"], "duration": 1},
            ],
        }],
    }))
    monkeypatch.setattr(jest, "REPORT_PATH", p)
    result = runner.get_failure_details(test_id="case a")
    assert len(result) == 1
    assert "case a" in result[0]["nodeid"]


# ---- generate_test — filename normalization ------------------------------------


def test_generate_test_defaults_to_test_js_suffix(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(jest, "PROJECT_ROOT", tmp_path)
    runner.generate_test("desc", "login")
    assert (tmp_path / "login.test.js").exists()


def test_generate_test_keeps_existing_spec_js_suffix(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(jest, "PROJECT_ROOT", tmp_path)
    runner.generate_test("desc", "login.spec.js")
    assert (tmp_path / "login.spec.js").exists()


def test_generate_test_renders_description_in_content(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(jest, "PROJECT_ROOT", tmp_path)
    runner.generate_test("登入行為測試", "login")
    content = (tmp_path / "login.test.js").read_text()
    assert "登入行為測試" in content
    assert 'describe("login"' in content
