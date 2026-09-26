"""Unit tests for `runners.schemathesis` — command assembly, CLI resolution,
and JUnit→report.json normalization. All subprocess calls are mocked;
nothing here fuzzes a real API.
"""
from __future__ import annotations

import json
import shutil
import sys
import types
from types import SimpleNamespace

import pytest

from gomore_qa_master.runners import schemathesis as sth


@pytest.fixture
def runner():
    return sth.SchemathesisRunner()


@pytest.fixture(autouse=True)
def _clean_schemathesis_env(monkeypatch):
    """CI's smoke job runs across Python 3.10–3.13 and the plain env may
    carry ambient QA_SCHEMATHESIS_* / QA_OPENAPI_URL values (or lack
    them) unpredictably — clear them before every test so `_base_cmd`
    assertions don't depend on whatever the invoking shell happens to
    have set. Individual tests still `setenv`/`delenv` on top of this
    where the value under test matters."""
    for var in (
        "QA_SCHEMATHESIS_CHECKS", "QA_SCHEMATHESIS_AUTH",
        "QA_SCHEMATHESIS_DRY_RUN", "QA_SCHEMATHESIS_MAX_EXAMPLES",
        "QA_OPENAPI_URL",
    ):
        monkeypatch.delenv(var, raising=False)


# ---- _to_cli_target ---------------------------------------------------------


def test_to_cli_target_strips_file_scheme():
    assert sth._to_cli_target("file:///tmp/spec.yaml") == "/tmp/spec.yaml"


def test_to_cli_target_passes_through_http_url():
    url = "https://api.example.com/openapi.json"
    assert sth._to_cli_target(url) == url


def test_to_cli_target_passes_through_plain_http():
    url = "http://localhost:8000/schema.json"
    assert sth._to_cli_target(url) == url


# ---- _validate_openapi_url ---------------------------------------------------


def test_validate_openapi_url_rejects_empty():
    with pytest.raises(ValueError, match="QA_OPENAPI_URL is required"):
        sth._validate_openapi_url("")


def test_validate_openapi_url_rejects_unknown_scheme():
    with pytest.raises(ValueError, match="http\\(s\\)://"):
        sth._validate_openapi_url("ftp://example.com/spec.yaml")


@pytest.mark.parametrize("url", [
    "http://localhost/openapi.json",
    "https://api.example.com/openapi.json",
    "file:///tmp/spec.yaml",
])
def test_validate_openapi_url_accepts_valid_schemes(url):
    sth._validate_openapi_url(url)  # should not raise


# ---- _require_schemathesis_cli — venv-bin priority resolution ---------------


def test_require_cli_raises_when_schemathesis_module_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "schemathesis", None)
    with pytest.raises(ImportError, match="not installed"):
        sth._require_schemathesis_cli()


def test_require_cli_prefers_venv_bin_next_to_interpreter(monkeypatch, tmp_path):
    # CI's smoke job only `pip install -e .` (no `[api]` extra), so the real
    # `schemathesis` package may not be importable there. Stub it in
    # sys.modules so `_require_schemathesis_cli`'s `import schemathesis`
    # succeeds regardless of what's actually installed.
    monkeypatch.setitem(sys.modules, "schemathesis", types.ModuleType("schemathesis"))

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python"
    fake_python.write_text("")
    fake_cli = fake_bin / "schemathesis"
    fake_cli.write_text("")

    monkeypatch.setattr(sys, "executable", str(fake_python))

    def which_should_not_be_called(name):
        raise AssertionError("shutil.which should not be reached when venv-bin CLI exists")

    monkeypatch.setattr(shutil, "which", which_should_not_be_called)

    result = sth._require_schemathesis_cli()
    assert result == str(fake_cli)


def test_require_cli_falls_back_to_path_when_no_venv_bin(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "schemathesis", types.ModuleType("schemathesis"))

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python"
    fake_python.write_text("")
    # No "schemathesis" file placed next to the interpreter.

    monkeypatch.setattr(sys, "executable", str(fake_python))
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/schemathesis")

    result = sth._require_schemathesis_cli()
    assert result == "/usr/local/bin/schemathesis"


def test_require_cli_raises_when_neither_venv_bin_nor_path_has_it(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "schemathesis", types.ModuleType("schemathesis"))

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python"
    fake_python.write_text("")

    monkeypatch.setattr(sys, "executable", str(fake_python))
    monkeypatch.setattr(shutil, "which", lambda name: None)

    with pytest.raises(ImportError, match="not found on PATH"):
        sth._require_schemathesis_cli()


# ---- _base_cmd command assembly ---------------------------------------------


def test_base_cmd_defaults_to_checks_all(runner, monkeypatch, tmp_path):
    monkeypatch.delenv("QA_SCHEMATHESIS_CHECKS", raising=False)
    cmd = runner._base_cmd("cli", "http://x/schema.json", tmp_path / "report.json")
    idx = cmd.index("--checks")
    assert cmd[idx + 1] == "all"


def test_base_cmd_splits_csv_checks(runner, monkeypatch, tmp_path):
    monkeypatch.setenv("QA_SCHEMATHESIS_CHECKS", "response_schema_conformance,status_code_conformance")
    cmd = runner._base_cmd("cli", "http://x/schema.json", tmp_path / "report.json")
    checks = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "--checks"]
    assert checks == ["response_schema_conformance", "status_code_conformance"]


def test_base_cmd_includes_auth_header_when_set(runner, monkeypatch, tmp_path):
    monkeypatch.setenv("QA_SCHEMATHESIS_AUTH", "Bearer abc123")
    cmd = runner._base_cmd("cli", "http://x/schema.json", tmp_path / "report.json")
    idx = cmd.index("-H")
    assert cmd[idx + 1] == "Authorization: Bearer abc123"


def test_base_cmd_omits_auth_header_when_unset(runner, monkeypatch, tmp_path):
    monkeypatch.delenv("QA_SCHEMATHESIS_AUTH", raising=False)
    cmd = runner._base_cmd("cli", "http://x/schema.json", tmp_path / "report.json")
    assert "-H" not in cmd


def test_base_cmd_includes_dry_run_flag_when_enabled(runner, monkeypatch, tmp_path):
    monkeypatch.setenv("QA_SCHEMATHESIS_DRY_RUN", "true")
    cmd = runner._base_cmd("cli", "http://x/schema.json", tmp_path / "report.json")
    assert "--dry-run" in cmd


def test_base_cmd_translates_file_url_target(runner, monkeypatch, tmp_path):
    monkeypatch.delenv("QA_SCHEMATHESIS_DRY_RUN", raising=False)
    cmd = runner._base_cmd("cli", "file:///tmp/spec.yaml", tmp_path / "report.json")
    assert cmd[-1] == "/tmp/spec.yaml"


def test_base_cmd_includes_junit_xml_flag(runner, tmp_path):
    cmd = runner._base_cmd("cli", "http://x/schema.json", tmp_path / "report.json")
    assert any(tok.startswith("--junit-xml=") for tok in cmd)


# ---- run_tests / run_failed orchestration (mocked cli + safe_run) ----------


def _fake_completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_tests_adds_include_path_filter(runner, monkeypatch, tmp_path):
    monkeypatch.setenv("QA_OPENAPI_URL", "http://x/schema.json")
    monkeypatch.setattr(sth, "_require_schemathesis_cli", lambda: "cli")
    monkeypatch.setattr(sth, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sth, "REPORT_PATH", tmp_path / "report.json")
    monkeypatch.setattr(sth, "JUNIT_PATH", tmp_path / "junit.xml")

    captured = {}

    def fake_safe_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed()

    monkeypatch.setattr(sth, "safe_run", fake_safe_run)
    monkeypatch.setattr(runner, "_archive_report", lambda: None)

    runner.run_tests(filter="/pets")
    assert "--include-path" in captured["cmd"]
    idx = captured["cmd"].index("--include-path")
    assert captured["cmd"][idx + 1] == "/pets"


def test_run_tests_exit_code_derives_from_normalized_report(runner, monkeypatch, tmp_path):
    monkeypatch.setenv("QA_OPENAPI_URL", "http://x/schema.json")
    monkeypatch.setattr(sth, "_require_schemathesis_cli", lambda: "cli")
    monkeypatch.setattr(sth, "PROJECT_ROOT", tmp_path)
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(sth, "REPORT_PATH", report_path)
    monkeypatch.setattr(sth, "JUNIT_PATH", tmp_path / "junit.xml")
    # Raw CLI exit code is 1 (schemathesis exits nonzero on any failed check)
    # but the runner's own exit_code should reflect the *normalized* report.
    monkeypatch.setattr(sth, "safe_run", lambda cmd, **kwargs: _fake_completed(returncode=1))
    monkeypatch.setattr(runner, "_archive_report", lambda: None)
    monkeypatch.setattr(runner, "_normalize_report", lambda p: report_path.write_text(json.dumps({
        "summary": {"total": 1, "passed": 1, "failed": 0, "skipped": 0}, "duration": 0, "tests": [],
    })))

    result = runner.run_tests()
    assert result["raw_exit_code"] == 1
    assert result["exit_code"] == 0


def test_run_failed_no_previous_report_errors(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(sth, "REPORT_PATH", tmp_path / "missing.json")
    result = runner.run_failed()
    assert result["error"].startswith("no previous report.json")


def test_run_failed_no_prior_failures_returns_info(runner, monkeypatch, tmp_path):
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({"tests": [{"nodeid": "GET /x", "outcome": "passed"}]}))
    monkeypatch.setattr(sth, "REPORT_PATH", report_path)
    result = runner.run_failed()
    assert result["info"] == "no previous failures to re-run"


def test_run_failed_scopes_to_failed_operations(runner, monkeypatch, tmp_path):
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({"tests": [
        {"nodeid": "GET /pets :: response_schema_conformance", "outcome": "failed"},
        {"nodeid": "POST /pets :: status_code_conformance", "outcome": "passed"},
    ]}))
    monkeypatch.setattr(sth, "REPORT_PATH", report_path)
    monkeypatch.setenv("QA_OPENAPI_URL", "http://x/schema.json")
    monkeypatch.setattr(sth, "_require_schemathesis_cli", lambda: "cli")
    monkeypatch.setattr(sth, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sth, "JUNIT_PATH", tmp_path / "junit.xml")

    captured = {}

    def fake_safe_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed()

    monkeypatch.setattr(sth, "safe_run", fake_safe_run)
    monkeypatch.setattr(runner, "_normalize_report", lambda p: None)
    monkeypatch.setattr(runner, "_archive_report", lambda: None)
    monkeypatch.setattr(runner, "get_report_summary", lambda: {"failed": 0})

    result = runner.run_failed()
    assert result["ops_rerun"] == 1
    assert "--include-method" in captured["cmd"]
    m_idx = captured["cmd"].index("--include-method")
    assert captured["cmd"][m_idx + 1] == "GET"
    p_idx = captured["cmd"].index("--include-path")
    assert captured["cmd"][p_idx + 1] == "/pets"


# ---- _format_operations ------------------------------------------------------


def test_format_operations_keeps_method_path_lines(runner):
    raw = "some banner\nGET /pets\nnoise line\nPOST /pets\n"
    out = runner._format_operations(raw)
    assert out == "GET /pets\nPOST /pets"


def test_format_operations_falls_back_to_tail_when_nothing_parsed(runner):
    raw = "\n".join(f"line {i}" for i in range(40))
    out = runner._format_operations(raw)
    assert "no operations parsed" in out
    assert "line 39" in out


def test_format_operations_truncates_over_200(runner):
    raw = "\n".join(f"GET /path{i}" for i in range(210))
    out = runner._format_operations(raw)
    lines = out.splitlines()
    assert len(lines) == 201
    assert "more, truncated" in lines[-1]


# ---- _normalize_report — JUnit XML → report.json ----------------------------


def test_normalize_report_writes_empty_when_junit_missing(runner, monkeypatch, tmp_path):
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(sth, "JUNIT_PATH", tmp_path / "missing.xml")
    monkeypatch.setattr(sth, "REPORT_PATH", report_path)
    runner._normalize_report()
    data = json.loads(report_path.read_text())
    assert data["summary"]["total"] == 0
    assert data["tests"] == []


def test_normalize_report_writes_empty_on_malformed_xml(runner, monkeypatch, tmp_path):
    junit = tmp_path / "junit.xml"
    junit.write_text("<not-valid-xml")
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(sth, "JUNIT_PATH", junit)
    monkeypatch.setattr(sth, "REPORT_PATH", report_path)
    runner._normalize_report()
    data = json.loads(report_path.read_text())
    assert data["summary"]["total"] == 0


def test_normalize_report_single_testsuite_shape(runner, monkeypatch, tmp_path):
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuite time="1.5">'
        '<testcase name="GET /pets" time="0.5"/>'
        '<testcase name="POST /pets" time="1.0">'
        '<failure message="schema mismatch">details here</failure>'
        '</testcase>'
        '</testsuite>'
    )
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(sth, "JUNIT_PATH", junit)
    monkeypatch.setattr(sth, "REPORT_PATH", report_path)
    runner._normalize_report()
    data = json.loads(report_path.read_text())
    assert data["summary"] == {"total": 2, "passed": 1, "failed": 1, "skipped": 0}
    failed = next(t for t in data["tests"] if t["nodeid"] == "POST /pets")
    assert failed["outcome"] == "failed"
    assert "schema mismatch" in failed["message"]


def test_normalize_report_wrapped_testsuites_shape(runner, monkeypatch, tmp_path):
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuites>'
        '<testsuite time="0.5"><testcase name="GET /a" time="0.5"/></testsuite>'
        '<testsuite time="0.2"><testcase name="GET /b" time="0.2"/></testsuite>'
        '</testsuites>'
    )
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(sth, "JUNIT_PATH", junit)
    monkeypatch.setattr(sth, "REPORT_PATH", report_path)
    runner._normalize_report()
    data = json.loads(report_path.read_text())
    assert data["summary"]["total"] == 2
    assert data["duration"] == pytest.approx(0.7)


def test_normalize_report_skipped_testcase(runner, monkeypatch, tmp_path):
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuite time="0.1">'
        '<testcase name="GET /x" time="0.0"><skipped message="not applicable"/></testcase>'
        '</testsuite>'
    )
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(sth, "JUNIT_PATH", junit)
    monkeypatch.setattr(sth, "REPORT_PATH", report_path)
    runner._normalize_report()
    data = json.loads(report_path.read_text())
    assert data["summary"]["skipped"] == 1


def test_normalize_report_error_element_counts_as_failed(runner, monkeypatch, tmp_path):
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuite time="0.1">'
        '<testcase name="GET /x" time="0.1"><error message="boom"/></testcase>'
        '</testsuite>'
    )
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(sth, "JUNIT_PATH", junit)
    monkeypatch.setattr(sth, "REPORT_PATH", report_path)
    runner._normalize_report()
    data = json.loads(report_path.read_text())
    assert data["summary"]["failed"] == 1


def test_normalize_report_redacts_bearer_token_in_message(runner, monkeypatch, tmp_path):
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuite time="0.1">'
        '<testcase name="GET /x" time="0.1">'
        '<failure message="Authorization: Bearer sekrit123">body</failure>'
        '</testcase>'
        '</testsuite>'
    )
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(sth, "JUNIT_PATH", junit)
    monkeypatch.setattr(sth, "REPORT_PATH", report_path)
    monkeypatch.delenv("QA_NO_REDACT", raising=False)
    runner._normalize_report()
    data = json.loads(report_path.read_text())
    msg = data["tests"][0]["message"]
    assert "sekrit123" not in msg
    assert "[REDACTED]" in msg


def test_redact_noop_when_qa_no_redact_set(monkeypatch):
    monkeypatch.setenv("QA_NO_REDACT", "1")
    text = '"password": "hunter2"'
    assert sth._redact(text) == text


def test_redact_returns_none_for_none_input():
    assert sth._redact(None) is None


# ---- get_report_summary / get_failure_details / get_all_test_details -------


def test_get_report_summary_missing_file(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(sth, "REPORT_PATH", tmp_path / "missing.json")
    result = runner.get_report_summary()
    assert "error" in result


def test_get_report_summary_invalid_json(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text("not json")
    monkeypatch.setattr(sth, "REPORT_PATH", p)
    result = runner.get_report_summary()
    assert result["error"] == "report.json invalid"


def test_get_failure_details_filters_by_test_id(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"tests": [
        {"nodeid": "GET /pets :: check_a", "outcome": "failed", "call": {"longrepr": "x", "duration": 0.1}},
        {"nodeid": "POST /pets :: check_b", "outcome": "failed", "call": {"longrepr": "y", "duration": 0.2}},
    ]}))
    monkeypatch.setattr(sth, "REPORT_PATH", p)
    result = runner.get_failure_details(test_id="GET /pets")
    assert len(result) == 1
    assert result[0]["nodeid"] == "GET /pets :: check_a"


def test_get_all_test_details_includes_passed_and_failed(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"tests": [
        {"nodeid": "GET /a", "outcome": "passed", "call": {"duration": 0.1}},
        {"nodeid": "GET /b", "outcome": "failed", "call": {"longrepr": "boom", "duration": 0.2}},
    ]}))
    monkeypatch.setattr(sth, "REPORT_PATH", p)
    result = runner.get_all_test_details()
    assert len(result) == 2
    failed = next(t for t in result if t["nodeid"] == "GET /b")
    assert failed["message"] == "boom"


# ---- generate_test / get_history --------------------------------------------


def test_generate_test_explains_no_codegen(runner):
    msg = runner.generate_test("desc", "filename")
    assert "does not author test files" in msg


def test_get_history_orders_oldest_first(runner, monkeypatch, tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    for i, passed in enumerate([1, 2, 3]):
        (history / f"2026010{i}-000000-000000.json").write_text(json.dumps({
            "summary": {"total": 3, "passed": passed, "failed": 3 - passed, "skipped": 0},
            "duration": 1.0,
        }))
    monkeypatch.setattr(sth, "HISTORY_DIR", history)
    result = runner.get_history()
    assert [h["passed"] for h in result] == [1, 2, 3]
