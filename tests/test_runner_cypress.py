"""Unit tests for `runners.cypress` — command assembly + report parsing.
Mocks safe_run; never actually invokes npx/cypress."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from gomore_qa_master.runners import cypress


@pytest.fixture
def runner():
    return cypress.CypressRunner()


def _fake_completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# ---- list_tests — filesystem walk, no subprocess -----------------------------


def test_list_tests_reports_missing_e2e_dir(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(cypress, "PROJECT_ROOT", tmp_path)
    out = runner.list_tests()
    assert "找不到" in out


def test_list_tests_lists_cy_files(runner, monkeypatch, tmp_path):
    e2e = tmp_path / "cypress" / "e2e"
    e2e.mkdir(parents=True)
    (e2e / "login.cy.js").write_text("")
    (e2e / "checkout.cy.ts").write_text("")
    monkeypatch.setattr(cypress, "PROJECT_ROOT", tmp_path)
    out = runner.list_tests()
    assert "login.cy.js" in out
    assert "checkout.cy.ts" in out


def test_list_tests_reports_no_files_when_dir_empty(runner, monkeypatch, tmp_path):
    e2e = tmp_path / "cypress" / "e2e"
    e2e.mkdir(parents=True)
    monkeypatch.setattr(cypress, "PROJECT_ROOT", tmp_path)
    out = runner.list_tests()
    assert "沒有測試檔" in out


# ---- run_tests / run_failed command assembly ---------------------------------


def test_run_tests_includes_json_reporter_and_output(runner, monkeypatch, tmp_path):
    report = tmp_path / "report.json"
    captured = {}

    def fake_safe_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed()
    monkeypatch.setattr(cypress, "safe_run", fake_safe_run)
    monkeypatch.setattr(cypress, "REPORT_PATH", report)
    monkeypatch.setattr(cypress, "PROJECT_ROOT", tmp_path)

    runner.run_tests()
    assert "--reporter" in captured["cmd"]
    idx = captured["cmd"].index("--reporter")
    assert captured["cmd"][idx + 1] == "json"
    assert f"output={report}" in captured["cmd"]


def test_run_tests_filter_becomes_spec_glob(runner, monkeypatch, tmp_path):
    captured = {}

    def fake_safe_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed()
    monkeypatch.setattr(cypress, "safe_run", fake_safe_run)
    monkeypatch.setattr(cypress, "REPORT_PATH", tmp_path / "report.json")
    monkeypatch.setattr(cypress, "PROJECT_ROOT", tmp_path)

    runner.run_tests(filter="login")
    idx = captured["cmd"].index("--spec")
    assert captured["cmd"][idx + 1] == "**/*login*"


def test_run_failed_without_prior_report_runs_full_suite(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(cypress, "REPORT_PATH", tmp_path / "missing.json")
    called = {}

    def fake_run_tests(*args, **kwargs):
        called["ran"] = True
        return {"exit_code": 0}
    monkeypatch.setattr(runner, "run_tests", fake_run_tests)
    result = runner.run_failed()
    assert called.get("ran") is True
    assert result == {"exit_code": 0}


def test_run_failed_with_no_failures_reports_nothing_to_rerun(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"failures": []}))
    monkeypatch.setattr(cypress, "REPORT_PATH", p)
    result = runner.run_failed()
    assert result == {"exit_code": 0, "stdout_tail": "（沒有失敗）"}


def test_run_failed_scopes_spec_to_failed_files(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"failures": [{"file": "cypress/e2e/login.cy.js"}]}))
    monkeypatch.setattr(cypress, "REPORT_PATH", p)
    monkeypatch.setattr(cypress, "PROJECT_ROOT", tmp_path)
    captured = {}

    def fake_safe_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed()
    monkeypatch.setattr(cypress, "safe_run", fake_safe_run)

    runner.run_failed()
    idx = captured["cmd"].index("--spec")
    assert captured["cmd"][idx + 1] == "cypress/e2e/login.cy.js"


# ---- get_report_summary -------------------------------------------------------


def test_get_report_summary_missing_file(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(cypress, "REPORT_PATH", tmp_path / "missing.json")
    assert runner.get_report_summary() == {"error": "找不到報告"}


def test_get_report_summary_maps_mocha_stats(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"stats": {
        "tests": 5, "passes": 3, "failures": 2, "pending": 0, "duration": 999,
    }}))
    monkeypatch.setattr(cypress, "REPORT_PATH", p)
    result = runner.get_report_summary()
    assert result == {"total": 5, "passed": 3, "failed": 2, "skipped": 0, "duration": 999}


# ---- get_failure_details -------------------------------------------------------


def test_get_failure_details_missing_file(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(cypress, "REPORT_PATH", tmp_path / "missing.json")
    assert runner.get_failure_details() == [{"error": "找不到報告"}]


def test_get_failure_details_extracts_stack(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"failures": [
        {"fullTitle": "login suite should fail", "err": {"stack": "Error: boom"}, "duration": 42},
    ]}))
    monkeypatch.setattr(cypress, "REPORT_PATH", p)
    result = runner.get_failure_details()
    assert result[0]["nodeid"] == "login suite should fail"
    assert result[0]["message"] == "Error: boom"


def test_get_failure_details_filters_by_test_id(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"failures": [
        {"fullTitle": "case a", "err": {}, "duration": 1},
        {"fullTitle": "case b", "err": {}, "duration": 1},
    ]}))
    monkeypatch.setattr(cypress, "REPORT_PATH", p)
    result = runner.get_failure_details(test_id="case a")
    assert len(result) == 1


# ---- generate_test -------------------------------------------------------------


def test_generate_test_adds_cy_js_suffix_and_e2e_path(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(cypress, "PROJECT_ROOT", tmp_path)
    runner.generate_test("desc", "login")
    target = tmp_path / "cypress" / "e2e" / "login.cy.js"
    assert target.exists()
    assert "desc" in target.read_text()


def test_generate_test_creates_missing_e2e_dir(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(cypress, "PROJECT_ROOT", tmp_path)
    assert not (tmp_path / "cypress").exists()
    runner.generate_test("desc", "checkout.cy.js")
    assert (tmp_path / "cypress" / "e2e" / "checkout.cy.js").exists()
