"""Unit tests for `runners.maestro` — command assembly, JUnit→report.json
translation, flow-file introspection (title/steps/screenshots), and the
auto-retry-on-failure patching logic. Mocks safe_run; never launches a
real simulator/emulator."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from gomore_qa_master.runners import maestro


@pytest.fixture
def runner():
    return maestro.MaestroRunner()


def _fake_completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# ---- _base_cmd / _discover_flows ---------------------------------------------


def test_base_cmd_includes_junit_format_and_output(runner, monkeypatch, tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    junit_path = tmp_path / "junit.xml"
    monkeypatch.setattr(maestro, "ARTIFACTS_DIR", artifacts_dir)
    monkeypatch.setattr(maestro, "JUNIT_PATH", junit_path)
    cmd = runner._base_cmd()
    assert cmd[:2] == ["maestro", "test"]
    assert "--format" in cmd
    idx = cmd.index("--format")
    assert cmd[idx + 1] == "junit"
    out_idx = cmd.index("--output")
    assert cmd[out_idx + 1] == str(junit_path)
    debug_idx = cmd.index("--debug-output")
    assert cmd[debug_idx + 1] == str(artifacts_dir)


def test_discover_flows_returns_all_yaml_when_no_filter(runner, monkeypatch, tmp_path):
    (tmp_path / "a.yaml").write_text("")
    (tmp_path / "b.yaml").write_text("")
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    flows = runner._discover_flows(None)
    assert len(flows) == 2


def test_discover_flows_filters_case_insensitively(runner, monkeypatch, tmp_path):
    (tmp_path / "Login.yaml").write_text("")
    (tmp_path / "checkout.yaml").write_text("")
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    flows = runner._discover_flows("login")
    assert len(flows) == 1
    assert flows[0].name == "Login.yaml"


def test_discover_flows_skips_test_results_dir(runner, monkeypatch, tmp_path):
    (tmp_path / "flow.yaml").write_text("")
    tr = tmp_path / "test-results"
    tr.mkdir()
    (tr / "archived.yaml").write_text("")
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    flows = runner._discover_flows(None)
    assert len(flows) == 1
    assert flows[0].name == "flow.yaml"


def test_list_tests_reports_no_flows(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    out = runner.list_tests()
    assert "no .yaml flows found" in out


# ---- run_tests orchestration --------------------------------------------------


def test_run_tests_returns_error_when_no_flows_match(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    result = runner.run_tests(filter="nonexistent")
    assert "error" in result


def test_run_tests_invokes_safe_run_and_reports_flow_count(runner, monkeypatch, tmp_path):
    (tmp_path / "flow.yaml").write_text("")
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(maestro, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(maestro, "connect_android_host", lambda: (True, ""))
    monkeypatch.setattr(maestro, "safe_run", lambda cmd, **kw: _fake_completed())
    monkeypatch.setattr(runner, "_junit_to_report_json", lambda: None)
    monkeypatch.setattr(runner, "_retry_failures_if_any", lambda: 0)
    monkeypatch.setattr(runner, "_archive_report", lambda: None)
    monkeypatch.setattr(runner, "get_report_summary", lambda: {"failed": 0})

    result = runner.run_tests()
    assert result["flows_run"] == 1
    assert result["exit_code"] == 0


def test_run_tests_adjusts_exit_code_from_patched_report(runner, monkeypatch, tmp_path):
    """raw_exit_code may be 1 (maestro CLI failed a flow) but after retry the
    report shows 0 failed — the returned exit_code should follow the report."""
    (tmp_path / "flow.yaml").write_text("")
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(maestro, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(maestro, "connect_android_host", lambda: (True, ""))
    monkeypatch.setattr(maestro, "safe_run", lambda cmd, **kw: _fake_completed(returncode=1))
    monkeypatch.setattr(runner, "_junit_to_report_json", lambda: None)
    monkeypatch.setattr(runner, "_retry_failures_if_any", lambda: 1)
    monkeypatch.setattr(runner, "_archive_report", lambda: None)
    monkeypatch.setattr(runner, "get_report_summary", lambda: {"failed": 0})

    result = runner.run_tests()
    assert result["raw_exit_code"] == 1
    assert result["exit_code"] == 0
    assert result["flaky_in_run"] == 1


def test_run_tests_surfaces_android_host_status(runner, monkeypatch, tmp_path):
    (tmp_path / "flow.yaml").write_text("")
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(maestro, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(maestro, "ANDROID_HOST", "127.0.0.1:5555")
    monkeypatch.setattr(maestro, "connect_android_host", lambda: (False, "connection refused"))
    monkeypatch.setattr(maestro, "safe_run", lambda cmd, **kw: _fake_completed())
    monkeypatch.setattr(runner, "_junit_to_report_json", lambda: None)
    monkeypatch.setattr(runner, "_retry_failures_if_any", lambda: 0)
    monkeypatch.setattr(runner, "_archive_report", lambda: None)
    monkeypatch.setattr(runner, "get_report_summary", lambda: {"failed": 0})

    result = runner.run_tests()
    assert result["android_host"] == "127.0.0.1:5555"
    assert result["android_host_connected"] is False
    assert result["android_host_message"] == "connection refused"


# ---- run_failed ----------------------------------------------------------------


def test_run_failed_without_prior_report(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(maestro, "REPORT_PATH", tmp_path / "missing.json")
    result = runner.run_failed()
    assert "error" in result


def test_run_failed_with_no_prior_failures(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"tests": [{"nodeid": "flow::flow", "outcome": "passed"}]}))
    monkeypatch.setattr(maestro, "REPORT_PATH", p)
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    result = runner.run_failed()
    assert result == {"info": "no previous failures to re-run"}


@pytest.mark.xfail(
    strict=True,
    reason=(
        "run_failed 以 classname 當檔案路徑找檔（nodeid.split('::')[0]），"
        "與 _flow_path_for/_retry_failures_if_any 用 stem 匹配不一致；"
        "真實 Maestro JUnit 的 classname == name == flow 名稱（無副檔名），"
        "會讓 PROJECT_ROOT / 'login' 找不到檔案，run_failed 因而退化成 no-op。"
        "修 src maestro.py 後移除此標記。"
    ),
)
def test_run_failed_reruns_matching_flow_files(runner, monkeypatch, tmp_path):
    """Uses the *real* Maestro JUnit nodeid shape (classname == name ==
    flow name, no `.yaml` extension) — matching test_junit_to_report_json's
    own fixtures elsewhere in this file. Under that real shape, run_failed
    should still find and rerun the corresponding flow file."""
    flow = tmp_path / "login.yaml"
    flow.write_text("")
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"tests": [{"nodeid": "login::login", "outcome": "failed"}]}))
    monkeypatch.setattr(maestro, "REPORT_PATH", p)
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(maestro, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(maestro, "connect_android_host", lambda: (True, ""))
    captured = {}

    def fake_safe_run(cmd, **kw):
        captured["cmd"] = cmd
        return _fake_completed()
    monkeypatch.setattr(maestro, "safe_run", fake_safe_run)
    monkeypatch.setattr(runner, "_junit_to_report_json", lambda: None)
    monkeypatch.setattr(runner, "_retry_failures_if_any", lambda: 0)
    monkeypatch.setattr(runner, "_archive_report", lambda: None)
    monkeypatch.setattr(runner, "get_report_summary", lambda: {"failed": 0})

    result = runner.run_failed()
    assert result["flows_rerun"] == 1
    assert str(flow) in captured["cmd"]


# ---- _junit_to_report_json -----------------------------------------------------


def test_junit_to_report_json_noop_when_missing(runner, monkeypatch, tmp_path):
    report = tmp_path / "report.json"
    monkeypatch.setattr(maestro, "JUNIT_PATH", tmp_path / "missing.xml")
    monkeypatch.setattr(maestro, "REPORT_PATH", report)
    runner._junit_to_report_json()
    assert not report.exists()


def test_junit_to_report_json_noop_on_malformed_xml(runner, monkeypatch, tmp_path):
    junit = tmp_path / "junit.xml"
    junit.write_text("<broken")
    report = tmp_path / "report.json"
    monkeypatch.setattr(maestro, "JUNIT_PATH", junit)
    monkeypatch.setattr(maestro, "REPORT_PATH", report)
    runner._junit_to_report_json()
    assert not report.exists()


def test_junit_to_report_json_converts_testcases(runner, monkeypatch, tmp_path):
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuite>'
        '<testcase name="flowA" classname="flowA" time="1.2"/>'
        '<testcase name="flowB" classname="flowB" time="0.5">'
        '<failure message="assertVisible failed">stack trace</failure>'
        '</testcase>'
        '<testcase name="flowC" classname="flowC" time="0"><skipped/></testcase>'
        '</testsuite>'
    )
    report = tmp_path / "report.json"
    monkeypatch.setattr(maestro, "JUNIT_PATH", junit)
    monkeypatch.setattr(maestro, "REPORT_PATH", report)
    runner._junit_to_report_json()
    data = json.loads(report.read_text())
    assert data["summary"] == {"total": 3, "passed": 1, "failed": 1, "skipped": 1}
    failed = next(t for t in data["tests"] if t["nodeid"] == "flowB::flowB")
    assert "assertVisible failed" in failed["message"]


# ---- _retry_enabled / _retry_failures_if_any -----------------------------------


def test_retry_enabled_defaults_true(runner, monkeypatch):
    monkeypatch.delenv("MAESTRO_RETRY", raising=False)
    assert runner._retry_enabled() is True


@pytest.mark.parametrize("val", ["false", "0", "no", "FALSE"])
def test_retry_enabled_can_be_disabled(runner, monkeypatch, val):
    monkeypatch.setenv("MAESTRO_RETRY", val)
    assert runner._retry_enabled() is False


def test_retry_failures_if_any_returns_zero_when_disabled(runner, monkeypatch, tmp_path):
    monkeypatch.setenv("MAESTRO_RETRY", "false")
    assert runner._retry_failures_if_any() == 0


def test_retry_failures_if_any_returns_zero_without_report(runner, monkeypatch, tmp_path):
    monkeypatch.delenv("MAESTRO_RETRY", raising=False)
    monkeypatch.setattr(maestro, "REPORT_PATH", tmp_path / "missing.json")
    assert runner._retry_failures_if_any() == 0


def test_retry_failures_if_any_returns_zero_with_no_failures(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"tests": [{"nodeid": "flow::flow", "outcome": "passed"}]}))
    monkeypatch.delenv("MAESTRO_RETRY", raising=False)
    monkeypatch.setattr(maestro, "REPORT_PATH", p)
    assert runner._retry_failures_if_any() == 0


def test_retry_failures_if_any_flips_outcome_on_pass(runner, monkeypatch, tmp_path):
    """nodeid shape mirrors _junit_to_report_json's own output: real Maestro
    JUnit sets classname == name == the flow's name, so nodeid is
    "<flow-name>::<flow-name>" — not "<filename.yaml>::<flow-name>"."""
    flow = tmp_path / "login.yaml"
    flow.write_text("")
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({
        "summary": {"total": 1, "passed": 0, "failed": 1, "skipped": 0},
        "tests": [{"nodeid": "login::login", "outcome": "failed", "message": "boom"}],
    }))
    monkeypatch.delenv("MAESTRO_RETRY", raising=False)
    monkeypatch.setattr(maestro, "REPORT_PATH", report_path)
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(maestro, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(maestro, "connect_android_host", lambda: (True, ""))

    retry_junit = tmp_path / "artifacts" / "junit-retry.xml"

    def fake_safe_run(cmd, **kw):
        retry_junit.parent.mkdir(parents=True, exist_ok=True)
        retry_junit.write_text(
            '<testsuite><testcase name="login" classname="login" time="0.1"/></testsuite>'
        )
        return _fake_completed()

    monkeypatch.setattr(maestro, "safe_run", fake_safe_run)

    flipped = runner._retry_failures_if_any()
    assert flipped == 1
    data = json.loads(report_path.read_text())
    assert data["tests"][0]["nodeid"] == "login::login"
    assert data["tests"][0]["outcome"] == "passed"
    assert data["tests"][0]["was_flaky_in_run"] is True
    assert data["summary"]["flaky_in_run"] == 1
    assert data["summary"]["failed"] == 0


def test_retry_failures_if_any_keeps_failed_when_retry_still_fails(runner, monkeypatch, tmp_path):
    flow = tmp_path / "login.yaml"
    flow.write_text("")
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({
        "summary": {"total": 1, "passed": 0, "failed": 1, "skipped": 0},
        "tests": [{"nodeid": "login::login", "outcome": "failed", "message": "boom"}],
    }))
    monkeypatch.delenv("MAESTRO_RETRY", raising=False)
    monkeypatch.setattr(maestro, "REPORT_PATH", report_path)
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(maestro, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(maestro, "connect_android_host", lambda: (True, ""))

    retry_junit = tmp_path / "artifacts" / "junit-retry.xml"

    def fake_safe_run(cmd, **kw):
        retry_junit.parent.mkdir(parents=True, exist_ok=True)
        retry_junit.write_text(
            '<testsuite><testcase name="login" classname="login" time="0.1">'
            '<failure message="still broken"/></testcase></testsuite>'
        )
        return _fake_completed()

    monkeypatch.setattr(maestro, "safe_run", fake_safe_run)

    flipped = runner._retry_failures_if_any()
    assert flipped == 0
    data = json.loads(report_path.read_text())
    assert data["tests"][0]["outcome"] == "failed"


# ---- _flow_path_for / _flow_title / _flow_steps / _flow_screenshots -----------


def test_flow_path_for_matches_by_stem(runner, monkeypatch, tmp_path):
    flow = tmp_path / "login.yaml"
    flow.write_text("")
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    found = runner._flow_path_for("login.yaml::login")
    assert found == flow


def test_flow_path_for_returns_none_when_not_found(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    assert runner._flow_path_for("missing::missing") is None


def test_flow_path_for_returns_none_for_empty_nodeid(runner):
    assert runner._flow_path_for("::") is None


def test_flow_title_extracts_leading_comment_block(runner, tmp_path):
    flow = tmp_path / "login.yaml"
    flow.write_text("# Smoke: app starts\n# and shows home\nappId: com.x\n---\n- launchApp\n")
    assert runner._flow_title(flow) == "Smoke: app starts / and shows home"


def test_flow_title_returns_none_without_comments(runner, tmp_path):
    flow = tmp_path / "login.yaml"
    flow.write_text("appId: com.x\n---\n- launchApp\n")
    assert runner._flow_title(flow) is None


def test_flow_title_returns_none_for_missing_file(runner, tmp_path):
    assert runner._flow_title(tmp_path / "missing.yaml") is None


def test_flow_title_returns_none_for_none_path(runner):
    assert runner._flow_title(None) is None


def test_flow_steps_extracts_top_level_actions(runner, tmp_path):
    flow = tmp_path / "login.yaml"
    flow.write_text(
        "appId: com.x\n---\n"
        "- launchApp\n"
        '- tapOn: "Login"\n'
        "- inputText: hello\n"
    )
    steps = runner._flow_steps(flow)
    assert {"api": "launchApp", "title": ""} in steps
    assert {"api": "tapOn", "title": '"Login"'} in steps


def test_flow_steps_returns_empty_for_none_path(runner):
    assert runner._flow_steps(None) == []


def test_flow_steps_returns_empty_for_missing_file(runner, tmp_path):
    assert runner._flow_steps(tmp_path / "missing.yaml") == []


def test_flow_screenshots_matches_take_screenshot_directive(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    flow = tmp_path / "login.yaml"
    flow.write_text("---\n- takeScreenshot: after_login\n")
    png = tmp_path / "after_login.png"
    png.write_bytes(b"")
    result = runner._flow_screenshots(flow)
    assert result == [png]


def test_flow_screenshots_empty_when_png_missing(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    flow = tmp_path / "login.yaml"
    flow.write_text("---\n- takeScreenshot: after_login\n")
    assert runner._flow_screenshots(flow) == []


# ---- _find_artifact / _find_screenshot / _find_video --------------------------


def test_find_artifact_returns_none_when_dir_missing(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(maestro, "ARTIFACTS_DIR", tmp_path / "missing")
    assert runner._find_artifact("login.yaml::login", ("*.png",)) is None


def test_find_artifact_matches_folder_token(runner, monkeypatch, tmp_path):
    artifacts = tmp_path / "artifacts"
    folder = artifacts / "login-flow-run"
    folder.mkdir(parents=True)
    (folder / "shot.png").write_bytes(b"")
    monkeypatch.setattr(maestro, "ARTIFACTS_DIR", artifacts)
    result = runner._find_artifact("login.yaml::login", ("*.png",))
    assert result.endswith("shot.png")


def test_find_screenshot_falls_back_to_named_screenshot(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(maestro, "ARTIFACTS_DIR", tmp_path / "no-artifacts")
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    flow = tmp_path / "login.yaml"
    flow.write_text("---\n- takeScreenshot: done\n")
    (tmp_path / "done.png").write_bytes(b"")
    result = runner._find_screenshot("login.yaml::login")
    assert result is not None
    assert result.endswith("done.png")


# ---- get_report_summary / get_failure_details / get_all_test_details ----------


def test_get_report_summary_missing_file(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(maestro, "REPORT_PATH", tmp_path / "missing.json")
    assert "error" in runner.get_report_summary()


def test_get_failure_details_includes_flow_metadata(runner, monkeypatch, tmp_path):
    flow = tmp_path / "login.yaml"
    flow.write_text("# Login smoke\n---\n- launchApp\n")
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"tests": [
        {"nodeid": "login.yaml::login", "outcome": "failed", "message": "boom", "duration": 1.0},
    ]}))
    monkeypatch.setattr(maestro, "REPORT_PATH", p)
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(maestro, "ARTIFACTS_DIR", tmp_path / "no-artifacts")
    result = runner.get_failure_details()
    assert result[0]["title"] == "Login smoke"
    assert result[0]["message"] == "boom"


def test_get_all_test_details_includes_passed_and_failed(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"tests": [
        {"nodeid": "login.yaml::login", "outcome": "passed", "duration": 1.0},
        {"nodeid": "login.yaml::login2", "outcome": "failed", "message": "x", "duration": 0.5},
    ]}))
    monkeypatch.setattr(maestro, "REPORT_PATH", p)
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(maestro, "ARTIFACTS_DIR", tmp_path / "no-artifacts")
    result = runner.get_all_test_details()
    assert len(result) == 2


# ---- generate_test — kind dispatch ---------------------------------------------


def test_generate_test_basic_flow(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    runner.generate_test("desc", "smoke", url="com.example.app")
    content = (tmp_path / "smoke.yaml").read_text()
    assert "appId: com.example.app" in content
    assert "launchApp" in content


def test_generate_test_cta_module(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    module = {"kind": "cta", "name": "Buy Now", "selectors": {"text": "Buy Now", "resource_id": "btn_buy"}}
    runner.generate_test("desc", "buy", module=module)
    content = (tmp_path / "buy.yaml").read_text()
    assert "btn_buy" in content
    assert "tapOn" in content


def test_generate_test_form_module(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    module = {
        "kind": "form", "name": "Signup",
        "selectors": {"fields": [{"label": "email", "resource_id": "f_email"}]},
    }
    runner.generate_test("desc", "signup", module=module)
    content = (tmp_path / "signup.yaml").read_text()
    assert "f_email" in content
    assert "test@example.com" in content


def test_generate_test_tab_bar_module(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    module = {"kind": "tab_bar", "name": "Tabs", "tabs": [{"label": "Home"}, {"label": "Profile"}]}
    runner.generate_test("desc", "tabs", module=module)
    content = (tmp_path / "tabs.yaml").read_text()
    assert "tapOn: 'Home'" in content
    assert "tapOn: 'Profile'" in content


def test_generate_test_generic_module(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    module = {"kind": "widget", "name": "Card"}
    runner.generate_test("desc", "card", module=module)
    content = (tmp_path / "card.yaml").read_text()
    assert "takeScreenshot" in content


def test_generate_test_adds_yaml_suffix(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(maestro, "PROJECT_ROOT", tmp_path)
    runner.generate_test("desc", "noext")
    assert (tmp_path / "noext.yaml").exists()


def test_codegen_returns_studio_hint(runner):
    msg = runner.codegen("https://example.com")
    assert "maestro studio" in msg


# ---- get_history ---------------------------------------------------------------


def test_get_history_computes_pass_rate(runner, monkeypatch, tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    (history / "20260101-000000-000000.json").write_text(json.dumps({
        "summary": {"total": 4, "passed": 2, "failed": 2, "skipped": 0},
        "duration": 1.0,
    }))
    monkeypatch.setattr(maestro, "HISTORY_DIR", history)
    result = runner.get_history()
    assert result[0]["pass_rate"] == 50.0
