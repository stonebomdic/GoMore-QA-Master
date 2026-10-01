"""Unit tests for `runners.pytest_playwright` — command assembly + report parsing.

封裝層本身（命令組裝、report.json / trace.zip 解析）幾乎沒有直接單元測試，
這裡補上。全部 mock safe_run / subprocess，絕不真的啟動瀏覽器。
"""
from __future__ import annotations

import ast
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


def test_generate_test_implicit_form_module_fills_one_field_and_presses_enter(runner, monkeypatch, tmp_path):
    """`analyzer._build_modules`'s implicit_form_0（metadata.implicit=True）
    must NOT go through `_render_form_test`'s fill-every-field +
    click-submit flow — there's no enclosing <form>/submit button. It
    should fill the first text/search field and press Enter instead."""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "form",
        "name": "implicit_form_0",
        "selectors": {
            "container": None,
            "fields": [
                {"label": "關鍵字", "selector": "#q", "type": "text", "required": False},
                {"label": "狀態", "selector": "#status", "type": "select", "required": False},
            ],
            "submit": None,
        },
        "metadata": {"implicit": True, "field_count": 2},
        "candidate_tcs": ["輸入關鍵字後按 Enter 應觸發查詢／過濾"],
    }
    runner.generate_test("desc", "test_search.py", url="https://x.test", module=module)
    content = (tmp_path / "test_search.py").read_text()
    ast.parse(content)
    assert 'page.locator(\'#q\').press("Enter")' in content
    # Must only fill the ONE target field, not every field in the module —
    # the select (#status) must never be touched (no .select_option call).
    assert "#status" not in content
    assert ".select_option" not in content
    assert content.count(".fill(") == 1


def test_generate_test_implicit_form_clear_description_fills_empty_string(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "form",
        "name": "implicit_form_0",
        "selectors": {
            "container": None,
            "fields": [{"label": "關鍵字", "selector": "#q", "type": "text", "required": False}],
            "submit": None,
        },
        "metadata": {"implicit": True, "field_count": 1},
        "candidate_tcs": ["清空輸入應還原列表"],
    }
    runner.generate_test("清空輸入應還原列表", "test_clear.py", url="https://x.test", module=module)
    content = (tmp_path / "test_clear.py").read_text()
    ast.parse(content)
    assert "page.locator('#q').fill('')" in content
    assert 'page.locator(\'#q\').press("Enter")' in content


def test_generate_test_implicit_form_all_checkbox_fields_checks_instead_of_fill(runner, monkeypatch, tmp_path):
    """Review N3: naively falling back to `fields[0]` and calling `.fill()`
    on it crashes for real — Playwright rejects `.fill()` on a checkbox
    ("Input of type checkbox cannot be filled"). With no text/search-like
    field at all, a checkbox-only implicit form must render `.check()`
    and must NOT press Enter afterward (that's not "submitting a
    search", it's a different, unverified action)."""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "form",
        "name": "implicit_form_0",
        "selectors": {
            "container": None,
            "fields": [
                {"label": "啟用", "selector": "#active", "type": "checkbox", "required": False},
                {"label": "已驗證", "selector": "#verified", "type": "checkbox", "required": False},
            ],
            "submit": None,
        },
        "metadata": {"implicit": True, "field_count": 2},
        "candidate_tcs": ["TC"],
    }
    runner.generate_test("desc", "test_checkbox.py", url="https://x.test", module=module)
    content = (tmp_path / "test_checkbox.py").read_text()
    ast.parse(content)
    assert "page.locator('#active').check()" in content
    assert ".fill(" not in content
    assert "press(" not in content


def test_generate_test_implicit_form_all_select_fields_uses_select_option(runner, monkeypatch, tmp_path):
    """Same crash class as the checkbox case: `.fill()` on a `<select>`
    also isn't valid Playwright — a select-only implicit form must use
    `select_option(index=1)` and must not press Enter afterward."""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "form",
        "name": "implicit_form_0",
        "selectors": {
            "container": None,
            "fields": [
                {"label": "狀態", "selector": "#status", "type": "select", "required": False},
            ],
            "submit": None,
        },
        "metadata": {"implicit": True, "field_count": 1},
        "candidate_tcs": ["TC"],
    }
    runner.generate_test("desc", "test_select_only.py", url="https://x.test", module=module)
    content = (tmp_path / "test_select_only.py").read_text()
    ast.parse(content)
    assert "page.locator('#status').select_option(index=1)" in content
    assert ".fill(" not in content
    assert "press(" not in content


def test_generate_test_form_module_with_non_dict_metadata_does_not_crash(runner, monkeypatch, tmp_path):
    """Review S1: `(module.get("metadata") or {}).get("implicit")` throws
    AttributeError the moment a caller hands a non-dict `metadata` (e.g. a
    list) — `.get()` doesn't exist on a list. Must fall back to the
    regular (non-implicit) form renderer instead of crashing."""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "form",
        "name": "Login form",
        "selectors": {
            "fields": [{"selector": "#email", "type": "email"}],
            "submit": "#submit",
        },
        "metadata": ["not", "a", "dict"],
        "candidate_tcs": ["TC1"],
    }
    runner.generate_test("desc", "test_weird_metadata.py", url="https://x.test", module=module)
    content = (tmp_path / "test_weird_metadata.py").read_text()
    ast.parse(content)
    # Took the regular form path: fills the field and clicks the submit.
    assert "#email" in content
    assert "#submit" in content


def test_generate_test_generic_module_renders_container(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {"kind": "widget", "name": "Cart", "selectors": {"container": "#cart"}}
    runner.generate_test("desc", "test_cart.py", module=module)
    content = (tmp_path / "test_cart.py").read_text()
    assert "#cart" in content


# ---- _render_form_test — P3 生成品質修復 ------------------------------------
# POC 附錄 F-1 實測缺陷：(1) 送出按鈕誤被當欄位 fill() 而炸掉、
# (2) empty-submit 描述卻仍先填值（描述與 body 矛盾）、(3) 只有 TODO 無真斷言。
#
# Review round 2 追加：
# major#1 負向 TC 不該掛成功斷言 / major#2 單一欄位留空不該被當全空 /
# major#3（見 test_server.py 的 _select_candidate_tcs）/ minor#6~9。

_POSITIVE_DESC = "全部填入合法值後送出，應觸發成功流程（導頁或顯示成功訊息）"


def test_generate_test_form_filters_button_type_fields(runner, monkeypatch, tmp_path):
    """analyzer 可能把 submit/button 誤放進 fields —— 模板端要防禦性過濾，
    不然會對送出按鈕呼叫 .fill() 直接炸掉（F-1 缺陷 1）。"""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "form",
        "name": "Login form",
        "selectors": {
            "fields": [
                {"selector": "#email", "type": "email"},
                {"selector": "#go", "type": "SUBMIT"},  # 大小寫不敏感
                {"selector": "#reset-btn", "type": "reset"},
                {"selector": "#hidden-token", "type": "hidden"},
                {"selector": "#submit", "type": "text"},  # 與 submit 選擇器同一個
            ],
            "submit": "#submit",
        },
    }
    runner.generate_test("使用者登入", "test_login.py", url="https://x.test", module=module)
    content = (tmp_path / "test_login.py").read_text()
    ast.parse(content)  # review round 2 minor#9：擋壞語法回歸
    assert "page.locator('#email').fill(" in content
    assert "'#go'" not in content
    assert "'#reset-btn'" not in content
    assert "'#hidden-token'" not in content
    # 唯一允許出現 #submit 的地方是 submit click，不應該再對它呼叫 .fill()
    assert "page.locator('#submit').fill(" not in content
    assert "page.locator('#submit').click()" in content


def test_generate_test_form_empty_submit_variant_chinese_keyword(runner, monkeypatch, tmp_path):
    """描述含「留空」等關鍵字 → 改渲染 empty-submit 變體：不 fill、直接 click，
    且 module docstring 要標出 variant，避免描述與 body 矛盾（F-1 缺陷 2）。"""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "form",
        "name": "Login form",
        "selectors": {
            "fields": [{"selector": "#email", "type": "email"}],
            "submit": "#submit",
        },
    }
    runner.generate_test("欄位留空時送出", "test_login_empty.py", url="https://x.test", module=module)
    content = (tmp_path / "test_login_empty.py").read_text()
    ast.parse(content)
    assert "page.locator('#email').fill(" not in content
    assert "page.locator('#submit').click()" in content
    assert "variant=empty-submit" in content
    assert "斷言錯誤提示" in content


def test_generate_test_form_empty_submit_variant_english_keyword(runner, monkeypatch, tmp_path):
    """英文關鍵字（大小寫不敏感）一樣要觸發 empty-submit 變體。"""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "form",
        "name": "Login form",
        "selectors": {
            "fields": [{"selector": "#email", "type": "email"}],
            "submit": "#submit",
        },
    }
    runner.generate_test(
        "Submit WITHOUT FILLING required fields", "test_login_blank.py",
        url="https://x.test", module=module,
    )
    content = (tmp_path / "test_login_blank.py").read_text()
    ast.parse(content)
    assert "page.locator('#email').fill(" not in content
    assert "page.locator('#submit').click()" in content
    assert "variant=empty-submit" in content


def test_generate_test_form_single_field_empty_skips_only_that_field(runner, monkeypatch, tmp_path):
    """analyzer 的「只填其他欄位、{label} 留空」TC 只該跳過該欄位的 fill，
    其餘欄位仍要正常填值（review round 2 major#2 —— 之前被通用關鍵字誤判成
    全部留空）。"""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "form",
        "name": "Login form",
        "selectors": {
            "fields": [
                {"selector": "#email", "type": "email", "label": "Email"},
                {"selector": "#password", "type": "password", "label": "Password"},
            ],
            "submit": "#submit",
        },
    }
    runner.generate_test(
        "只填其他欄位、Password 留空，應顯示該欄位必填錯誤",
        "test_login_single_empty.py", url="https://x.test", module=module,
    )
    content = (tmp_path / "test_login_single_empty.py").read_text()
    ast.parse(content)
    assert "variant=single-field-empty" in content
    # Email 仍要 fill，只有 Password 被跳過
    assert "page.locator('#email').fill(" in content
    assert "page.locator('#password').fill(" not in content
    assert "page.locator('#submit').click()" in content
    assert "斷言錯誤提示" in content


def test_generate_test_form_single_field_empty_unknown_label_falls_back_to_full_empty(
    runner, monkeypatch, tmp_path,
):
    """TC 提到的 label 對不到任何已知欄位時，fallback 為全部留空，且要在
    產出裡註明是 fallback（review round 2 major#2）。"""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "form",
        "name": "Login form",
        "selectors": {
            "fields": [{"selector": "#email", "type": "email", "label": "Email"}],
            "submit": "#submit",
        },
    }
    runner.generate_test(
        "只填其他欄位、Nickname 留空，應顯示該欄位必填錯誤",
        "test_login_unknown_label.py", url="https://x.test", module=module,
    )
    content = (tmp_path / "test_login_unknown_label.py").read_text()
    ast.parse(content)
    assert "variant=empty-submit" in content
    assert "page.locator('#email').fill(" not in content
    assert "Nickname" in content
    assert "fallback" in content


def test_generate_test_form_happy_path_renders_api_response_assertion(runner, monkeypatch, tmp_path):
    """明確正向描述 + module['api'] 存在時，happy-path 變體要產出真實斷言：
    等待對應 API response、檢查 method 與狀態碼，而不是留一個空 TODO
    （F-1 缺陷 3；review round 2 minor#6 補上 method 比對）。"""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "form",
        "name": "Login form",
        "selectors": {
            "fields": [{"selector": "#email", "type": "email"}],
            "submit": "#submit",
        },
        "api": {"method": "POST", "url_substring": "/api/login"},
    }
    runner.generate_test(_POSITIVE_DESC, "test_login_api.py", url="https://x.test", module=module)
    content = (tmp_path / "test_login_api.py").read_text()
    ast.parse(content)
    assert "r.request.method == 'POST' and \"/api/login\" in r.url" in content
    assert "assert _resp.value.status < 400" in content
    assert "page.locator('#submit').click()" in content
    assert "TODO: 補上實際斷言" not in content


def test_generate_test_form_happy_path_fallback_todo_without_api(runner, monkeypatch, tmp_path):
    """沒有 module['api'] 時，維持現行 TODO fallback，不硬湊斷言。"""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "form",
        "name": "Login form",
        "selectors": {
            "fields": [{"selector": "#email", "type": "email"}],
            "submit": "#submit",
        },
    }
    runner.generate_test(_POSITIVE_DESC, "test_login_noapi.py", url="https://x.test", module=module)
    content = (tmp_path / "test_login_noapi.py").read_text()
    ast.parse(content)
    assert "TODO: 補上實際斷言" in content
    assert "page.expect_response" not in content


def test_generate_test_form_negative_description_does_not_get_success_assertion(
    runner, monkeypatch, tmp_path,
):
    """review round 2 major#1：負向 TC（如「Email 格式錯誤應顯示錯誤」）就算
    帶 module['api']，也不該被掛上「status < 400」這種成功斷言 —— 斷言方向
    與描述相反，寧缺勿錯，維持 TODO fallback。"""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "form",
        "name": "Login form",
        "selectors": {
            "fields": [{"selector": "#email", "type": "email"}],
            "submit": "#submit",
        },
        "api": {"method": "POST", "url_substring": "/api/login"},
    }
    runner.generate_test(
        "Email 欄位填入格式錯誤的字串（無 @），應顯示格式錯誤",
        "test_login_negative.py", url="https://x.test", module=module,
    )
    content = (tmp_path / "test_login_negative.py").read_text()
    ast.parse(content)
    assert "page.expect_response" not in content
    assert "assert _resp.value.status" not in content
    assert "TODO: 補上實際斷言" in content


def test_generate_test_form_api_path_slash_is_too_generic_for_assertion(runner, monkeypatch, tmp_path):
    """review round 2 minor#6：url_substring 為 "/" 時幾乎恆真，不產斷言，
    fallback 回 TODO。"""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "form",
        "name": "Login form",
        "selectors": {
            "fields": [{"selector": "#email", "type": "email"}],
            "submit": "#submit",
        },
        "api": {"method": "POST", "url_substring": "/"},
    }
    runner.generate_test(_POSITIVE_DESC, "test_login_slash.py", url="https://x.test", module=module)
    content = (tmp_path / "test_login_slash.py").read_text()
    ast.parse(content)
    assert "page.expect_response" not in content
    assert "TODO: 補上實際斷言" in content


# ---- _is_empty_submit_description / _is_positive_description ---------------
# review round 2 minor#7：關鍵字改詞組，單字「空」「blank」移除避免誤判；
# 兩個分類器各建一份正負向表，含已知誤判案例。


@pytest.mark.parametrize("description", [
    "所有必填欄位為空時送出，應顯示必填錯誤",
    "欄位留空時送出",
    "Email 未填時應顯示錯誤",
    "空白送出應顯示必填提示",
    "Submit empty submit form",
    "Submit WITHOUT FILLING required fields",
    "Please leave blank and submit",
])
def test_is_empty_submit_description_true_cases(description):
    assert pw._is_empty_submit_description(description) is True


@pytest.mark.parametrize("description", [
    "清空購物車",
    "Blank page check",
    "空白字元處理應被 trim",
    "全部填入合法值後送出，應觸發成功流程",
    "使用者登入",
    None,
])
def test_is_empty_submit_description_false_cases(description):
    assert pw._is_empty_submit_description(description) is False


@pytest.mark.parametrize("description", [
    "全部填入合法值後送出，應觸發成功流程（導頁或顯示成功訊息）",
    "全部填寫正確後應顯示成功訊息",
    "happy path: valid login redirects to dashboard",
])
def test_is_positive_description_true_cases(description):
    assert pw._is_positive_description(description) is True


@pytest.mark.parametrize("description", [
    "Email 欄位填入格式錯誤的字串（無 @），應顯示格式錯誤",
    "Password 太短或不符合複雜度規則時應顯示錯誤",
    "使用者登入",
    "全部填入合法值後送出，但格式錯誤時應顯示錯誤",  # 正負向關鍵字都命中，負向優先
    None,
])
def test_is_positive_description_false_cases(description):
    assert pw._is_positive_description(description) is False


def test_business_context_block_empty_when_blank(runner):
    assert runner._business_context_block(None) == ""
    assert runner._business_context_block("   ") == ""


def test_business_context_block_renders_lines(runner):
    block = runner._business_context_block("line1\nline2")
    assert "# Business context:" in block
    assert "# line1" in block
    assert "# line2" in block


# ---- bare-tag / non-unique selector 退化（table/section/cta/dialog module） -
# 真實站實測（gwp-admin /users，9 筆產出 2 綠 6 紅）：table module selector
# 退回 "table"（頁上 2 個）、cta/dialog 退回 "button"（頁上 10+ 個），兩者都
# 觸發 Playwright strict-mode violation。渲染端改用 .first 降低碰撞機率，
# cta 另外改走文字定位，dialog 的 open_on_load=False 改成不會必紅的存在性
# 斷言。


def test_is_bare_tag_selector_true_for_plain_tags():
    assert pw._is_bare_tag_selector("table") is True
    assert pw._is_bare_tag_selector("button") is True
    assert pw._is_bare_tag_selector("div") is True
    assert pw._is_bare_tag_selector("dialog") is True


@pytest.mark.parametrize("selector", [
    "#cart", ".btn", "[data-testid=x]", "table.foo", 'a:has-text("x")', "table#users",
])
def test_is_bare_tag_selector_false_for_qualified_selectors(selector):
    assert pw._is_bare_tag_selector(selector) is False


def test_is_bare_tag_selector_false_for_none_or_non_string():
    assert pw._is_bare_tag_selector(None) is False
    assert pw._is_bare_tag_selector(123) is False


def test_generate_test_table_module_bare_tag_selector_renders_first_with_comment(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "table",
        "name": "users_table_0",
        "selectors": {"container": "table"},
        "metadata": {
            "headers": ["Name"], "column_count": 1, "row_count": 2, "detection": "native",
        },
    }
    runner.generate_test("表格應正確渲染", "test_users_table.py", url="https://x.test", module=module)
    content = (tmp_path / "test_users_table.py").read_text()
    ast.parse(content)
    assert "page.locator('table').first" in content
    assert "data-testid" in content  # 退化註解建議補 data-testid


def test_generate_test_table_module_selector_unique_false_flag_forces_first(runner, monkeypatch, tmp_path):
    """table metadata 有 selector_unique 旗標時，優先採用它而非 bare-tag
    heuristic —— 即使 selector 本身不是 bare tag，一樣要退化成 .first。"""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "table",
        "name": "users_table_0",
        "selectors": {"container": "table[data-testid=users]"},
        "metadata": {"selector_unique": False},
    }
    runner.generate_test("desc", "test_users_table2.py", url="https://x.test", module=module)
    content = (tmp_path / "test_users_table2.py").read_text()
    assert "page.locator('table[data-testid=users]').first" in content


def test_generate_test_table_module_selector_unique_true_flag_skips_first(runner, monkeypatch, tmp_path):
    """反向：selector 是 bare tag，但 metadata 明確標 selector_unique=True
    （例如頁面上真的只有一個 <table>）—— 旗標優先，不應該硬加 .first。"""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "table",
        "name": "users_table_0",
        "selectors": {"container": "table"},
        "metadata": {"selector_unique": True},
    }
    runner.generate_test("desc", "test_users_table3.py", url="https://x.test", module=module)
    content = (tmp_path / "test_users_table3.py").read_text()
    assert "page.locator('table').first" not in content
    assert "page.locator('table')" in content


def test_generate_test_section_module_bare_tag_falls_back_to_heuristic(runner, monkeypatch, tmp_path):
    """section module 沒有 selector_unique metadata —— 純用 bare-tag
    heuristic 判斷。"""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {"kind": "section", "name": "section_0", "selectors": {"container": "section"}}
    runner.generate_test("desc", "test_section.py", module=module)
    content = (tmp_path / "test_section.py").read_text()
    assert "page.locator('section').first" in content


def test_generate_test_generic_module_non_bare_selector_does_not_add_first(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {"kind": "widget", "name": "Cart", "selectors": {"container": "#cart"}}
    runner.generate_test("desc", "test_cart2.py", module=module)
    content = (tmp_path / "test_cart2.py").read_text()
    assert "#cart" in content
    assert ".first" not in content


# ---- cta module — 文字定位 ---------------------------------------------------


def test_generate_test_cta_module_button_tag_uses_get_by_role_text(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "cta",
        "name": "cta_confirm_logout",
        "selectors": {"trigger": "button"},
        "metadata": {"label_text": "確認登出", "tag": "button"},
    }
    runner.generate_test("desc", "test_cta_logout.py", url="https://x.test", module=module)
    content = (tmp_path / "test_cta_logout.py").read_text()
    ast.parse(content)
    assert 'page.get_by_role("button", name=' in content
    assert "確認登出" in content
    assert ".first" in content
    assert "has-text" not in content


# ---- review round 3 (#2/#3/#4) — cta 文字定位重做 ---------------------------
# #2 label 結尾反斜線／含換行時，舊版把 label 塞進 CSS :has-text("...")
#    字串字面值，Playwright 解析該 CSS 會丟 BADSTRING。
# #3 非 button 的 [role=button] div 走 CSS :has-text(...) 會匹配所有含該
#    文字的祖先節點，.first 選到最外層（例如 #app）—— 假綠。
# #4 analyzer 已給出穩定 selector（#id/[data-testid]/aria-label...）時，
#    硬改走 get_by_role(name=innerText) 會找不到元素（accessible name 不
#    一定等於 innerText，例如 aria-label 按鈕）——綠變紅。
#
# 修法：tag=="a" 用 get_by_role("link", ...)；tag=="button" 或 selector 命中
# role="button" 用 get_by_role("button", ...)；真的判斷不出 tag 時才退回
# page.locator(sel).filter(has_text=label)（Python 字串參數，完全不碰 CSS
# 字串跳脫）。以上三者都只在 selector 非唯一（bare tag 或
# metadata.selector_unique=False）時才啟用；selector 本身穩定時一律保留
# 原 selector，不管有沒有 label_text。


def _get_by_role_call(tree):
    return next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "get_by_role"
    )


def test_render_cta_label_with_trailing_backslash_and_newline_parses_cleanly(runner, monkeypatch, tmp_path):
    """review round 3 #2 的具體重現：label 結尾反斜線、且含換行（多行
    innerText 很常見）。新版完全不組 CSS 字串，單純靠 get_by_role(name=...)
    的 Python 字串參數，天生不會有 CSS BADSTRING 的問題。"""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    tricky_label = "登出\n確認\\"
    module = {
        "kind": "cta",
        "name": "cta_tricky",
        "selectors": {"trigger": "button"},
        "metadata": {"label_text": tricky_label, "tag": "button"},
    }
    runner.generate_test("desc", "test_cta_tricky.py", url="https://x.test", module=module)
    content = (tmp_path / "test_cta_tricky.py").read_text()
    tree = ast.parse(content)
    call = _get_by_role_call(tree)
    name_kw = next(kw for kw in call.keywords if kw.arg == "name")
    assert name_kw.value.value == tricky_label
    assert "has-text" not in content


def test_render_cta_div_role_button_uses_get_by_role_not_has_text(runner, monkeypatch, tmp_path):
    """review round 3 #3 的具體重現：一個 `<div role="button">` 沒有
    id/data-testid/aria-label 等可用屬性，analyzer 的 sel() 退回 bare tag
    "div"（tag metadata 仍正確記成 "div"）。舊版這種非 "a" 的 tag 會走 CSS
    :has-text(...)，.first 選到最外層祖先（假綠）；新版一律
    get_by_role("button", ...)。"""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "cta",
        "name": "cta_div_button",
        "selectors": {"trigger": "div"},
        "metadata": {"label_text": "確認登出", "tag": "div"},
    }
    runner.generate_test("desc", "test_cta_div_button.py", url="https://x.test", module=module)
    content = (tmp_path / "test_cta_div_button.py").read_text()
    tree = ast.parse(content)
    call = _get_by_role_call(tree)
    assert call.args[0].value == "button"
    assert "has-text" not in content


def test_render_cta_anchor_tag_uses_get_by_role_link(runner, monkeypatch, tmp_path):
    """tag=="a" 改用 get_by_role("link", ...)，不再是 CSS :has-text(...)。"""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "cta",
        "name": "cta_buy",
        "selectors": {"trigger": "a"},
        "metadata": {"label_text": "立即購買", "tag": "a"},
    }
    runner.generate_test("desc", "test_cta_buy.py", url="https://x.test", module=module)
    content = (tmp_path / "test_cta_buy.py").read_text()
    tree = ast.parse(content)
    call = _get_by_role_call(tree)
    assert call.args[0].value == "link"
    name_kw = next(kw for kw in call.keywords if kw.arg == "name")
    assert name_kw.value.value == "立即購買"
    assert "has-text" not in content


def test_render_cta_label_text_with_embedded_quotes_round_trips_via_python_repr(runner, monkeypatch, tmp_path):
    """get_by_role 的 name 參數是純 Python 字串參數（不是 CSS 字串字面
    值），雙引號不需要任何特殊跳脫就能安全往返——取代舊版專門測 CSS
    :has-text 跳脫的案例。"""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "cta",
        "name": "cta_quote",
        "selectors": {"trigger": "a"},
        "metadata": {"label_text": '說"你好"', "tag": "a"},
    }
    runner.generate_test("desc", "test_cta_quote.py", url="https://x.test", module=module)
    content = (tmp_path / "test_cta_quote.py").read_text()
    tree = ast.parse(content)
    call = _get_by_role_call(tree)
    name_kw = next(kw for kw in call.keywords if kw.arg == "name")
    assert name_kw.value.value == '說"你好"'
    assert "has-text" not in content


def test_render_cta_unknown_tag_falls_back_to_filter_has_text(runner, monkeypatch, tmp_path):
    """tag 既不是 "a" 也不是 "button"、selector 也沒有 role="button" 標記
    （例如 analyze_screen 的 mobile cta，metadata 裡根本沒有 "tag" 欄位）
    ——不亂猜 ARIA role，改用 .filter(has_text=...)，一樣不碰 CSS 字串
    跳脫。"""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "cta",
        "name": "cta_mobile",
        "selectors": {"trigger": "custom-el"},
        "metadata": {"label_text": "確認"},  # 沒有 "tag"
    }
    runner.generate_test("desc", "test_cta_mobile.py", url="https://x.test", module=module)
    content = (tmp_path / "test_cta_mobile.py").read_text()
    tree = ast.parse(content)
    assert "get_by_role" not in content
    filter_call = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "filter"
    )
    has_text_kw = next(kw for kw in filter_call.keywords if kw.arg == "has_text")
    assert has_text_kw.value.value == "確認"
    assert "has-text" not in content  # 不是 CSS 偽類那種寫法


def test_render_cta_stable_selector_is_preserved_over_text_locator(runner, monkeypatch, tmp_path):
    """review round 3 #4：selector 已經是穩定的（這裡用 aria-label 屬性
    選擇器）——即使有 label_text，也不該被硬改成 get_by_role(name=...)，
    因為 accessible name 來自 aria-label 不是 innerText，用 innerText 當
    name 反而會找不到元素（本來會過的測試被改成找不到元素）。"""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "cta",
        "name": "cta_logout",
        "selectors": {"trigger": 'button[aria-label="登出"]'},
        "metadata": {"label_text": "登出圖示", "tag": "button"},
    }
    runner.generate_test("desc", "test_cta_stable.py", url="https://x.test", module=module)
    content = (tmp_path / "test_cta_stable.py").read_text()
    ast.parse(content)
    assert "get_by_role" not in content
    assert "filter(has_text" not in content
    assert "page.locator('button[aria-label=\"登出\"]')" in content
    assert ".first" not in content


def test_render_cta_selector_unique_false_flag_still_uses_text_locator_even_if_qualified(
    runner, monkeypatch, tmp_path,
):
    """反向：selector 看起來像是「qualified」（非 bare tag），但
    metadata.selector_unique 明確為 False —— 旗標優先，一樣要走文字定位，
    跟 table module 的規則一致。"""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "cta",
        "name": "cta_logout",
        "selectors": {"trigger": "button.logout-btn"},
        "metadata": {"label_text": "登出", "tag": "button", "selector_unique": False},
    }
    runner.generate_test("desc", "test_cta_flagged.py", url="https://x.test", module=module)
    content = (tmp_path / "test_cta_flagged.py").read_text()
    ast.parse(content)
    assert "get_by_role" in content


def test_generate_test_cta_module_without_label_text_falls_back_to_bare_tag_first(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "cta",
        "name": "cta_unknown",
        "selectors": {"trigger": "button"},
        "metadata": {},
    }
    runner.generate_test("desc", "test_cta_unknown.py", url="https://x.test", module=module)
    content = (tmp_path / "test_cta_unknown.py").read_text()
    ast.parse(content)
    assert "page.locator('button').first" in content
    assert "get_by_role" not in content
    assert "has-text" not in content


# ---- dialog module — open_on_load ------------------------------------------


def test_generate_test_dialog_module_open_on_load_false_renders_existence_assertion(runner, monkeypatch, tmp_path):
    """open_on_load=False 代表 dialog 預設關閉，渲染 visible 斷言必紅——
    改為存在性／hidden 斷言，並留 TODO 註記要先補觸發步驟。"""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "dialog",
        "name": "dialog_confirm_logout",
        "selectors": {"container": "dialog"},
        "metadata": {"open_on_load": False},
    }
    runner.generate_test("desc", "test_dialog_logout.py", url="https://x.test", module=module)
    content = (tmp_path / "test_dialog_logout.py").read_text()
    ast.parse(content)
    assert "to_be_visible()" not in content
    assert "to_be_hidden()" in content
    assert "TODO" in content
    assert "觸發" in content


# ---- review round 3 (#5) — dialog 先 attached 再 hidden，避免 .first.first --
# 0 匹配也會讓單純的 to_be_hidden() 通過（空斷言）。先補 to_be_attached()
# 確保真的有找到元素，再驗證預設是隱藏的；同時避免 container selector 自己
# 已經退化成 .first 時又疊一層 .first（.first.first 語意重複）。


def test_render_dialog_open_on_load_false_asserts_attached_before_hidden(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "dialog",
        "name": "dialog_confirm_logout",
        "selectors": {"container": "#confirm-logout-dialog"},
        "metadata": {"open_on_load": False},
    }
    runner.generate_test("desc", "test_dialog_unique.py", url="https://x.test", module=module)
    content = (tmp_path / "test_dialog_unique.py").read_text()
    ast.parse(content)
    assert "expect(target.first).to_be_attached()" in content
    assert "expect(target.first).to_be_hidden()" in content
    assert ".first.first" not in content


def test_render_dialog_open_on_load_false_bare_tag_does_not_double_first(runner, monkeypatch, tmp_path):
    """container selector 已經因為 bare-tag 退化成 .first 時，後面的
    attached/hidden 斷言不該再疊一層 .first。"""
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "dialog",
        "name": "dialog_confirm_logout",
        "selectors": {"container": "dialog"},
        "metadata": {"open_on_load": False},
    }
    runner.generate_test("desc", "test_dialog_bare.py", url="https://x.test", module=module)
    content = (tmp_path / "test_dialog_bare.py").read_text()
    ast.parse(content)
    assert "target = page.locator('dialog').first" in content
    assert "expect(target).to_be_attached()" in content
    assert "expect(target).to_be_hidden()" in content
    assert ".first.first" not in content
    assert "target.first" not in content


def test_generate_test_dialog_module_open_on_load_true_keeps_visible_assertion(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "dialog",
        "name": "dialog_welcome",
        "selectors": {"container": "dialog"},
        "metadata": {"open_on_load": True},
    }
    runner.generate_test("desc", "test_dialog_welcome.py", url="https://x.test", module=module)
    content = (tmp_path / "test_dialog_welcome.py").read_text()
    ast.parse(content)
    assert "to_be_visible()" in content
    assert "to_be_hidden()" not in content
    assert "page.locator('dialog').first" in content


def test_generate_test_dialog_module_non_bare_selector_open_on_load_true_no_first(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "dialog",
        "name": "dialog_welcome",
        "selectors": {"container": "#welcome-dialog"},
        "metadata": {"open_on_load": True},
    }
    runner.generate_test("desc", "test_dialog_welcome2.py", url="https://x.test", module=module)
    content = (tmp_path / "test_dialog_welcome2.py").read_text()
    assert "#welcome-dialog" in content
    assert ".first" not in content
