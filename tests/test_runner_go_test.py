"""Unit tests for `runners.go_test` — command assembly + `go test -json`
NDJSON event-stream parsing. Mocks safe_run; never actually invokes `go`."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from gomore_qa_master.runners import go_test


@pytest.fixture
def runner():
    return go_test.GoTestRunner()


def _fake_completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _evt(**kwargs) -> str:
    return json.dumps(kwargs)


# ---- list_tests ----------------------------------------------------------------


def test_list_tests_invokes_go_test_list(runner, monkeypatch, tmp_path):
    captured = {}

    def fake_safe_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed(stdout="TestFoo\nTestBar\n")
    monkeypatch.setattr(go_test, "safe_run", fake_safe_run)
    monkeypatch.setattr(go_test, "PROJECT_ROOT", tmp_path)
    out = runner.list_tests()
    assert captured["cmd"] == ["go", "test", "-list", ".*", "./..."]
    assert "TestFoo" in out


# ---- run_tests / run_failed command assembly -----------------------------------


def test_run_tests_writes_stdout_to_report_path(runner, monkeypatch, tmp_path):
    report = tmp_path / "report.json"
    monkeypatch.setattr(go_test, "safe_run", lambda cmd, **kwargs: _fake_completed(
        stdout=_evt(Action="pass", Test="TestFoo") + "\n"))
    monkeypatch.setattr(go_test, "REPORT_PATH", report)
    monkeypatch.setattr(go_test, "PROJECT_ROOT", tmp_path)
    runner.run_tests()
    assert "TestFoo" in report.read_text()


def test_run_tests_adds_run_filter(runner, monkeypatch, tmp_path):
    captured = {}

    def fake_safe_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed(stdout="")
    monkeypatch.setattr(go_test, "safe_run", fake_safe_run)
    monkeypatch.setattr(go_test, "REPORT_PATH", tmp_path / "report.json")
    monkeypatch.setattr(go_test, "PROJECT_ROOT", tmp_path)
    runner.run_tests(filter="TestFoo")
    idx = captured["cmd"].index("-run")
    assert captured["cmd"][idx + 1] == "TestFoo"


def test_run_tests_includes_json_flag(runner, monkeypatch, tmp_path):
    captured = {}

    def fake_safe_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed(stdout="")
    monkeypatch.setattr(go_test, "safe_run", fake_safe_run)
    monkeypatch.setattr(go_test, "REPORT_PATH", tmp_path / "report.json")
    monkeypatch.setattr(go_test, "PROJECT_ROOT", tmp_path)
    runner.run_tests()
    assert "-json" in captured["cmd"]


def test_run_failed_without_report_runs_full_suite(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(go_test, "REPORT_PATH", tmp_path / "missing.json")
    called = {}

    def fake_run_tests(*a, **kw):
        called["ran"] = True
        return {"exit_code": 0}
    monkeypatch.setattr(runner, "run_tests", fake_run_tests)
    result = runner.run_failed()
    assert called.get("ran") is True
    assert result == {"exit_code": 0}


def test_run_failed_reports_nothing_when_all_passed(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text(_evt(Action="pass", Test="TestFoo") + "\n")
    monkeypatch.setattr(go_test, "REPORT_PATH", p)
    result = runner.run_failed()
    assert result == {"exit_code": 0, "stdout_tail": "（沒有失敗）"}


def test_run_failed_builds_regex_pattern_from_failed_tests(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text(
        _evt(Action="fail", Test="TestFoo") + "\n"
        + _evt(Action="pass", Test="TestBar") + "\n"
    )
    monkeypatch.setattr(go_test, "REPORT_PATH", p)
    captured = {}

    def fake_run_tests(filter=None, **kwargs):
        captured["filter"] = filter
        return {"exit_code": 1}
    monkeypatch.setattr(runner, "run_tests", fake_run_tests)
    runner.run_failed()
    assert captured["filter"] == "^(TestFoo)$"


def test_run_failed_skips_malformed_json_lines(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text("not json\n" + _evt(Action="fail", Test="TestFoo") + "\n")
    monkeypatch.setattr(go_test, "REPORT_PATH", p)
    captured = {}
    monkeypatch.setattr(runner, "run_tests", lambda filter=None, **kw: captured.setdefault("filter", filter))
    runner.run_failed()
    assert captured["filter"] == "^(TestFoo)$"


# ---- get_report_summary ---------------------------------------------------------


def test_get_report_summary_missing_file(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(go_test, "REPORT_PATH", tmp_path / "missing.json")
    assert runner.get_report_summary() == {"error": "找不到報告"}


def test_get_report_summary_counts_pass_fail_skip(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text("\n".join([
        _evt(Action="pass", Test="TestA"),
        _evt(Action="fail", Test="TestB"),
        _evt(Action="skip", Test="TestC"),
        _evt(Action="output", Test="TestA", Output="ok\n"),
        _evt(Action="run"),  # no Test field — package-level event, ignored
    ]))
    monkeypatch.setattr(go_test, "REPORT_PATH", p)
    result = runner.get_report_summary()
    assert result == {"total": 3, "passed": 1, "failed": 1, "skipped": 1}


def test_get_report_summary_skips_malformed_lines(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text("garbage\n" + _evt(Action="pass", Test="TestA"))
    monkeypatch.setattr(go_test, "REPORT_PATH", p)
    result = runner.get_report_summary()
    assert result["total"] == 1


# ---- get_failure_details ----------------------------------------------------------


def test_get_failure_details_missing_file(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(go_test, "REPORT_PATH", tmp_path / "missing.json")
    assert runner.get_failure_details() == [{"error": "找不到報告"}]


def test_get_failure_details_joins_output_lines_for_failed_test(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text("\n".join([
        _evt(Action="output", Package="pkg", Test="TestFoo", Output="line1\n"),
        _evt(Action="output", Package="pkg", Test="TestFoo", Output="line2\n"),
        _evt(Action="fail", Package="pkg", Test="TestFoo"),
        _evt(Action="pass", Package="pkg", Test="TestBar"),
    ]))
    monkeypatch.setattr(go_test, "REPORT_PATH", p)
    result = runner.get_failure_details()
    assert len(result) == 1
    assert result[0]["nodeid"] == "pkg::TestFoo"
    assert result[0]["message"] == "line1\nline2\n"


def test_get_failure_details_filters_by_test_id(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text("\n".join([
        _evt(Action="fail", Package="pkg", Test="TestFoo"),
        _evt(Action="fail", Package="pkg", Test="TestBar"),
    ]))
    monkeypatch.setattr(go_test, "REPORT_PATH", p)
    result = runner.get_failure_details(test_id="TestFoo")
    assert len(result) == 1
    assert result[0]["nodeid"] == "pkg::TestFoo"


# ---- generate_test -----------------------------------------------------------------


def test_generate_test_adds_test_go_suffix(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(go_test, "PROJECT_ROOT", tmp_path)
    runner.generate_test("desc", "login")
    assert (tmp_path / "login_test.go").exists()


def test_generate_test_titlecases_slug(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(go_test, "PROJECT_ROOT", tmp_path)
    runner.generate_test("desc", "user_login")
    content = (tmp_path / "user_login_test.go").read_text()
    assert "func TestUserLogin(" in content
