"""Unit tests for `runners.pytest_playwright` — command assembly + report parsing.

封裝層本身（命令組裝、report.json / trace.zip 解析）幾乎沒有直接單元測試，
這裡補上。全部 mock safe_run / subprocess，絕不真的啟動瀏覽器。
"""
from __future__ import annotations

import json
import sys
import zipfile
from types import SimpleNamespace

import pytest

from gomore_qa_master.runners import pytest_playwright as pw

# ---- _PYTEST_CMD 迴歸測試 --------------------------------------------------
# Phase 5 實測：MCP server 行程 PATH 通常不含 venv bin，裸 `pytest` 會
# FileNotFoundError。修法是改用 `sys.executable -m pytest`；這裡鎖住迴歸。


def test_pytest_cmd_starts_with_current_interpreter_module_invocation():
    assert pw._PYTEST_CMD == [sys.executable, "-m", "pytest"]


def test_pytest_cmd_does_not_use_bare_pytest_binary():
    assert pw._PYTEST_CMD[0] != "pytest"


# ---- _base_cmd 命令組裝 ----------------------------------------------------


@pytest.fixture
def runner():
    return pw.PytestPlaywrightRunner()


def test_list_tests_uses_current_interpreter_module_invocation(runner, monkeypatch, tmp_path):
    """list_tests (src line ~94) builds its own `[*_PYTEST_CMD, ...]` command
    independently of `_base_cmd` — guard it too so a future refactor that
    reverts to a bare `pytest` binary would be caught here as well."""
    captured = {}

    def fake_safe_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed(stdout="collected 3 items\n")

    monkeypatch.setattr(pw, "safe_run", fake_safe_run)
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)

    out = runner.list_tests()
    assert captured["cmd"][:3] == [sys.executable, "-m", "pytest"]
    assert "collected 3 items" in out


def test_base_cmd_starts_with_pytest_cmd(runner):
    cmd = runner._base_cmd("chromium")
    assert cmd[: len(pw._PYTEST_CMD)] == pw._PYTEST_CMD


def test_base_cmd_includes_browser_flag(runner):
    cmd = runner._base_cmd("firefox")
    assert "--browser=firefox" in cmd


def test_base_cmd_includes_json_report_flags(runner):
    cmd = runner._base_cmd("chromium")
    assert "--json-report" in cmd
    assert f"--json-report-file={pw.REPORT_PATH}" in cmd


def test_base_cmd_includes_tracing_on(runner):
    cmd = runner._base_cmd("chromium")
    assert "--tracing=on" in cmd


def test_base_cmd_includes_junitxml(runner):
    cmd = runner._base_cmd("chromium")
    assert f"--junitxml={pw.JUNIT_PATH}" in cmd


def test_base_cmd_includes_rerun_flags_when_plugin_present(runner, monkeypatch):
    monkeypatch.setattr(pw, "_HAS_RERUNFAILURES", True)
    cmd = runner._base_cmd("chromium")
    assert "--reruns" in cmd
    assert "1" in cmd


def test_base_cmd_omits_rerun_flags_when_plugin_absent(runner, monkeypatch):
    monkeypatch.setattr(pw, "_HAS_RERUNFAILURES", False)
    cmd = runner._base_cmd("chromium")
    assert "--reruns" not in cmd


# ---- run_tests / run_failed 命令組裝（mock safe_run） ----------------------


def _fake_completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_tests_adds_filter_as_dash_k(runner, monkeypatch, tmp_path):
    captured = {}

    def fake_safe_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed()

    monkeypatch.setattr(pw, "safe_run", fake_safe_run)
    monkeypatch.setattr(pw, "REPORT_PATH", tmp_path / "report.json")
    monkeypatch.setattr(runner, "_archive_report", lambda: None)

    runner.run_tests(filter="test_login")
    assert "-k" in captured["cmd"]
    idx = captured["cmd"].index("-k")
    assert captured["cmd"][idx + 1] == "test_login"


def test_run_tests_adds_headed_flag_when_requested(runner, monkeypatch, tmp_path):
    captured = {}

    def fake_safe_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed()

    monkeypatch.setattr(pw, "safe_run", fake_safe_run)
    monkeypatch.setattr(runner, "_archive_report", lambda: None)

    runner.run_tests(headed=True)
    assert "--headed" in captured["cmd"]


def test_run_tests_returns_exit_code_and_tails(runner, monkeypatch):
    # Distinguishable head/tail content (not just repeated same char) so the
    # assertions actually prove *which end* got kept, not just the length.
    stdout = "H" * 1000 + "T" * 2000
    stderr = "H" * 1500 + "T" * 1000
    monkeypatch.setattr(
        pw, "safe_run",
        lambda cmd, **kwargs: _fake_completed(returncode=1, stdout=stdout, stderr=stderr),
    )
    monkeypatch.setattr(runner, "_archive_report", lambda: None)
    result = runner.run_tests()
    assert result["exit_code"] == 1
    assert len(result["stdout_tail"]) == 2000
    assert result["stdout_tail"] == "T" * 2000
    assert "H" not in result["stdout_tail"]
    assert len(result["stderr_tail"]) == 1000
    assert result["stderr_tail"] == "T" * 1000
    assert "H" not in result["stderr_tail"]


def test_run_failed_uses_lf_flag(runner, monkeypatch):
    captured = {}

    def fake_safe_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed()

    monkeypatch.setattr(pw, "safe_run", fake_safe_run)
    monkeypatch.setattr(runner, "_archive_report", lambda: None)
    runner.run_failed()
    assert "--lf" in captured["cmd"]


# ---- get_report_summary ----------------------------------------------------


def test_get_report_summary_missing_file_returns_error(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pw, "REPORT_PATH", tmp_path / "missing.json")
    result = runner.get_report_summary()
    assert "error" in result


def test_get_report_summary_reads_totals(runner, monkeypatch, tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({
        "summary": {"total": 3, "passed": 2, "failed": 1, "skipped": 0},
        "duration": 12.5,
        "tests": [],
    }))
    monkeypatch.setattr(pw, "REPORT_PATH", report)
    result = runner.get_report_summary()
    assert result["total"] == 3
    assert result["passed"] == 2
    assert result["failed"] == 1
    assert result["duration"] == 12.5


def test_get_report_summary_counts_flaky_reruns(runner, monkeypatch, tmp_path):
    """pytest-rerunfailures emits a 'rerun' record then the final outcome for
    the same nodeid — flaky_in_run should count those pairs, not every rerun."""
    report = tmp_path / "report.json"
    report.write_text(json.dumps({
        "summary": {"total": 2, "passed": 2, "failed": 0, "skipped": 0},
        "duration": 1.0,
        "tests": [
            {"nodeid": "t::a", "outcome": "rerun"},
            {"nodeid": "t::a", "outcome": "passed"},
            {"nodeid": "t::b", "outcome": "passed"},
        ],
    }))
    monkeypatch.setattr(pw, "REPORT_PATH", report)
    result = runner.get_report_summary()
    assert result["flaky_in_run"] == 1


# ---- get_failure_details ----------------------------------------------------


def test_get_failure_details_missing_report(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pw, "REPORT_PATH", tmp_path / "missing.json")
    result = runner.get_failure_details()
    assert result == [{"error": "找不到報告"}]


def test_get_failure_details_filters_by_test_id(runner, monkeypatch, tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({
        "tests": [
            {"nodeid": "tests/a.py::test_x", "outcome": "failed",
             "call": {"longrepr": "boom", "duration": 0.2}},
            {"nodeid": "tests/a.py::test_y", "outcome": "failed",
             "call": {"longrepr": "bang", "duration": 0.1}},
            {"nodeid": "tests/a.py::test_z", "outcome": "passed"},
        ],
    }))
    monkeypatch.setattr(pw, "REPORT_PATH", report)
    monkeypatch.setattr(pw, "ARTIFACTS_DIR", tmp_path / "no-artifacts")
    result = runner.get_failure_details(test_id="test_x")
    assert len(result) == 1
    assert result[0]["nodeid"] == "tests/a.py::test_x"
    assert result[0]["message"] == "boom"


# ---- _find_artifacts --------------------------------------------------------


def test_find_artifacts_returns_none_when_dir_missing(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pw, "ARTIFACTS_DIR", tmp_path / "does-not-exist")
    out = runner._find_artifacts("tests/a.py::test_login")
    assert out == {"screenshot": None, "trace": None, "video": None}


def test_find_artifacts_matches_token_folder(runner, monkeypatch, tmp_path):
    artifacts = tmp_path / "artifacts"
    folder = artifacts / "test-login-chromium"
    folder.mkdir(parents=True)
    (folder / "shot.png").write_bytes(b"")
    (folder / "trace.zip").write_bytes(b"")
    (folder / "video.webm").write_bytes(b"")
    monkeypatch.setattr(pw, "ARTIFACTS_DIR", artifacts)

    out = runner._find_artifacts("tests/a.py::test_login")
    assert out["screenshot"].endswith("shot.png")
    assert out["trace"].endswith("trace.zip")
    assert out["video"].endswith("video.webm")


def test_find_artifacts_skips_history_folder(runner, monkeypatch, tmp_path):
    """The token derived from the nodeid must actually be able to match the
    "history" folder name for this to be a meaningful guard — a token like
    "test-login" was never going to match "history" via substring search
    anyway, making the skip-check untested. Use a nodeid whose function
    name IS "history" so the token collides with the folder name, and the
    explicit `folder.name == "history"` skip is what keeps this from
    matching (deleting that check would make this test fail)."""
    artifacts = tmp_path / "artifacts"
    history = artifacts / "history"
    history.mkdir(parents=True)
    (history / "shot.png").write_bytes(b"")
    monkeypatch.setattr(pw, "ARTIFACTS_DIR", artifacts)

    out = runner._find_artifacts("tests/a.py::history")
    assert out["screenshot"] is None


# ---- _docstring_for ---------------------------------------------------------


def test_docstring_for_extracts_function_docstring(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    test_file = tmp_path / "test_sample.py"
    test_file.write_text(
        'def test_login():\n'
        '    """使用者可以登入成功"""\n'
        '    pass\n'
    )
    cache: dict = {}
    title = runner._docstring_for("test_sample.py::test_login", cache)
    assert title == "使用者可以登入成功"


def test_docstring_for_handles_parametrize_ids(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    test_file = tmp_path / "test_sample.py"
    test_file.write_text(
        'def test_login():\n'
        '    """登入案例"""\n'
        '    pass\n'
    )
    cache: dict = {}
    title = runner._docstring_for("test_sample.py::test_login[param-1]", cache)
    assert title == "登入案例"


def test_docstring_for_handles_class_scoped_nodeid(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    test_file = tmp_path / "test_sample.py"
    test_file.write_text(
        'class TestSuite:\n'
        '    def test_login(self):\n'
        '        """類別內的登入案例"""\n'
        '        pass\n'
    )
    cache: dict = {}
    title = runner._docstring_for("test_sample.py::TestSuite::test_login", cache)
    assert title == "類別內的登入案例"


def test_docstring_for_returns_none_without_docstring(runner, tmp_path, monkeypatch):
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    test_file = tmp_path / "test_sample.py"
    test_file.write_text('def test_login():\n    pass\n')
    cache: dict = {}
    title = runner._docstring_for("test_sample.py::test_login", cache)
    assert title is None


def test_docstring_for_returns_none_when_nodeid_has_no_separator(runner):
    cache: dict = {}
    assert runner._docstring_for("no-separator-here", cache) is None


def test_parse_docstrings_handles_syntax_error_gracefully(tmp_path):
    bad = tmp_path / "broken.py"
    bad.write_text("def broken(:\n")
    assert pw._parse_docstrings(bad) == {}


def test_parse_docstrings_handles_missing_file(tmp_path):
    assert pw._parse_docstrings(tmp_path / "missing.py") == {}


# ---- _extract_steps (trace.zip parsing) ------------------------------------


def _make_trace_zip(path, events):
    with zipfile.ZipFile(path, "w") as z:
        body = "\n".join(json.dumps(e) for e in events)
        z.writestr("resources/trace.trace", body)


def test_extract_steps_returns_empty_for_none_path(runner):
    assert runner._extract_steps(None) == []


def test_extract_steps_returns_empty_for_missing_file(runner, tmp_path):
    assert runner._extract_steps(str(tmp_path / "missing.zip")) == []


def test_extract_steps_returns_empty_for_corrupt_zip(runner, tmp_path):
    bad_zip = tmp_path / "trace.zip"
    bad_zip.write_bytes(b"not a zip file")
    assert runner._extract_steps(str(bad_zip)) == []


def test_extract_steps_parses_before_events(runner, tmp_path):
    trace_zip = tmp_path / "trace.zip"
    _make_trace_zip(trace_zip, [
        {"type": "before", "class": "Page", "method": "goto",
         "callId": "1", "title": "page.goto(url)"},
        {"type": "after", "callId": "1"},
        {"type": "before", "class": "Locator", "method": "click",
         "callId": "2", "title": "locator.click()"},
    ])
    steps = runner._extract_steps(str(trace_zip))
    assert steps == [
        {"api": "Page.goto", "title": "page.goto(url)"},
        {"api": "Locator.click", "title": "locator.click()"},
    ]


def test_extract_steps_dedupes_by_call_id(runner, tmp_path):
    trace_zip = tmp_path / "trace.zip"
    _make_trace_zip(trace_zip, [
        {"type": "before", "class": "Page", "method": "goto",
         "callId": "1", "title": "first"},
        {"type": "before", "class": "Page", "method": "goto",
         "callId": "1", "title": "duplicate"},
    ])
    steps = runner._extract_steps(str(trace_zip))
    assert len(steps) == 1


def test_extract_steps_filters_internal_events(runner, tmp_path):
    """Only Frame/Page/Locator/ElementHandle/BrowserContext/Keyboard/Mouse
    classes are kept — internal tracing.* events are filtered out."""
    trace_zip = tmp_path / "trace.zip"
    _make_trace_zip(trace_zip, [
        {"type": "before", "class": "Tracing", "method": "start", "callId": "1"},
        {"type": "before", "class": "Page", "method": "click", "callId": "2"},
    ])
    steps = runner._extract_steps(str(trace_zip))
    assert len(steps) == 1
    assert steps[0]["api"] == "Page.click"


def test_extract_steps_skips_malformed_json_lines(runner, tmp_path):
    trace_zip = tmp_path / "trace.zip"
    with zipfile.ZipFile(trace_zip, "w") as z:
        z.writestr("resources/trace.trace", "not json\n{\"type\": \"before\", \"class\": \"Page\", \"method\": \"goto\", \"callId\": \"1\"}")
    steps = runner._extract_steps(str(trace_zip))
    assert len(steps) == 1


def test_extract_steps_no_trace_file_in_zip(runner, tmp_path):
    trace_zip = tmp_path / "trace.zip"
    with zipfile.ZipFile(trace_zip, "w") as z:
        z.writestr("resources/other.txt", "irrelevant")
    assert runner._extract_steps(str(trace_zip)) == []


# ---- get_history ------------------------------------------------------------


def test_get_history_returns_empty_when_dir_missing(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pw, "HISTORY_DIR", tmp_path / "missing")
    assert runner.get_history() == []


def test_get_history_orders_oldest_first(runner, monkeypatch, tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    for i, (total, passed) in enumerate([(10, 5), (10, 8), (10, 10)]):
        (history / f"2026010{i}-000000-000000.json").write_text(json.dumps({
            "summary": {"total": total, "passed": passed, "failed": total - passed, "skipped": 0},
            "duration": 1.0,
        }))
    monkeypatch.setattr(pw, "HISTORY_DIR", history)
    result = runner.get_history()
    assert [h["passed"] for h in result] == [5, 8, 10]


def test_get_history_skips_unparseable_files(runner, monkeypatch, tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    (history / "20260101-000000-000000.json").write_text("not json")
    monkeypatch.setattr(pw, "HISTORY_DIR", history)
    assert runner.get_history() == []


def test_get_history_computes_pass_rate(runner, monkeypatch, tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    (history / "20260101-000000-000000.json").write_text(json.dumps({
        "summary": {"total": 4, "passed": 3, "failed": 1, "skipped": 0},
        "duration": 1.0,
    }))
    monkeypatch.setattr(pw, "HISTORY_DIR", history)
    result = runner.get_history()
    assert result[0]["pass_rate"] == 75.0


def test_get_history_zero_total_pass_rate_is_zero(runner, monkeypatch, tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    (history / "20260101-000000-000000.json").write_text(json.dumps({
        "summary": {"total": 0, "passed": 0, "failed": 0, "skipped": 0},
        "duration": 0,
    }))
    monkeypatch.setattr(pw, "HISTORY_DIR", history)
    result = runner.get_history()
    assert result[0]["pass_rate"] == 0


# ---- generate_test — filename normalization + rendering --------------------


def test_generate_test_adds_test_prefix_and_py_suffix(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    runner.generate_test("desc", "login")
    assert (tmp_path / "test_login.py").exists()


def test_generate_test_basic_renders_description_and_todo(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    output = runner.generate_test("使用者可以登入", "test_login.py")
    content = (tmp_path / "test_login.py").read_text()
    assert "def test_login(page: Page):" in content
    assert "使用者可以登入" in content
    assert "TODO" in content
    assert "已產生" in output


def test_generate_test_form_module_fills_fields(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "form",
        "name": "Login form",
        "selectors": {
            "fields": [{"selector": "#email", "type": "email"}],
            "submit": "#submit",
        },
        "candidate_tcs": ["TC1"],
    }
    runner.generate_test("desc", "test_login.py", url="https://x.test", module=module)
    content = (tmp_path / "test_login.py").read_text()
    assert 'page.locator(\'#email\').fill(' in content
    assert "test@example.com" in content
    assert "#submit" in content


def test_generate_test_generic_module_renders_container(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {"kind": "widget", "name": "Cart", "selectors": {"container": "#cart"}}
    runner.generate_test("desc", "test_cart.py", module=module)
    content = (tmp_path / "test_cart.py").read_text()
    assert "#cart" in content


def test_business_context_block_empty_when_blank(runner):
    assert runner._business_context_block(None) == ""
    assert runner._business_context_block("   ") == ""


def test_business_context_block_renders_lines(runner):
    block = runner._business_context_block("line1\nline2")
    assert "# Business context:" in block
    assert "# line1" in block
    assert "# line2" in block
