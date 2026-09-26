"""Unit tests for `runners.newman` — collection parsing, command assembly,
and Newman JSON→report.json normalization. No real `newman` CLI invocation.
"""
from __future__ import annotations

import json
import shutil
from types import SimpleNamespace

import pytest

from gomore_qa_master.runners import newman as nm


@pytest.fixture
def runner():
    return nm.NewmanRunner()


# ---- _validate_collection_path -----------------------------------------------


def test_validate_collection_path_rejects_empty():
    with pytest.raises(ValueError, match="QA_POSTMAN_COLLECTION is required"):
        nm._validate_collection_path("")


def test_validate_collection_path_rejects_missing_file(tmp_path):
    with pytest.raises(ValueError, match="missing file"):
        nm._validate_collection_path(str(tmp_path / "nope.json"))


def test_validate_collection_path_rejects_directory(tmp_path):
    d = tmp_path / "a_dir"
    d.mkdir()
    with pytest.raises(ValueError, match="must be a file"):
        nm._validate_collection_path(str(d))


def test_validate_collection_path_accepts_existing_file(tmp_path):
    f = tmp_path / "collection.json"
    f.write_text("{}")
    nm._validate_collection_path(str(f))  # should not raise


# ---- _require_newman_cli ------------------------------------------------------


def test_require_newman_cli_raises_when_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(ImportError, match="npm install -g newman"):
        nm._require_newman_cli()


def test_require_newman_cli_returns_path_when_found(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/newman")
    assert nm._require_newman_cli() == "/usr/local/bin/newman"


# ---- _base_cmd command assembly ----------------------------------------------


def test_base_cmd_includes_json_reporter_export(runner, monkeypatch, tmp_path):
    report_json = tmp_path / "report.json"
    monkeypatch.delenv("QA_POSTMAN_ENVIRONMENT", raising=False)
    monkeypatch.delenv("QA_POSTMAN_GLOBALS", raising=False)
    monkeypatch.delenv("QA_POSTMAN_FOLDER", raising=False)
    cmd = runner._base_cmd("newman", "collection.json", report_json)
    assert "--reporters" in cmd
    idx = cmd.index("--reporters")
    assert cmd[idx + 1] == "cli,json"
    assert "--reporter-json-export" in cmd
    idx2 = cmd.index("--reporter-json-export")
    assert cmd[idx2 + 1] == str(report_json)


def test_base_cmd_includes_environment_and_globals_when_set(runner, monkeypatch, tmp_path):
    monkeypatch.setenv("QA_POSTMAN_ENVIRONMENT", "/env.json")
    monkeypatch.setenv("QA_POSTMAN_GLOBALS", "/globals.json")
    cmd = runner._base_cmd("newman", "collection.json", tmp_path / "r.json")
    e_idx = cmd.index("-e")
    assert cmd[e_idx + 1] == "/env.json"
    g_idx = cmd.index("-g")
    assert cmd[g_idx + 1] == "/globals.json"


def test_base_cmd_omits_environment_globals_when_unset(runner, monkeypatch, tmp_path):
    monkeypatch.delenv("QA_POSTMAN_ENVIRONMENT", raising=False)
    monkeypatch.delenv("QA_POSTMAN_GLOBALS", raising=False)
    cmd = runner._base_cmd("newman", "collection.json", tmp_path / "r.json")
    assert "-e" not in cmd
    assert "-g" not in cmd


def test_base_cmd_repeats_folder_flag_for_csv(runner, monkeypatch, tmp_path):
    monkeypatch.setenv("QA_POSTMAN_FOLDER", "Auth, Users")
    cmd = runner._base_cmd("newman", "collection.json", tmp_path / "r.json")
    folder_values = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "--folder"]
    assert folder_values == ["Auth", "Users"]


def test_base_cmd_defaults_iterations_and_timeout(runner, monkeypatch, tmp_path):
    monkeypatch.delenv("QA_POSTMAN_ITERATIONS", raising=False)
    monkeypatch.delenv("QA_POSTMAN_TIMEOUT_REQUEST_MS", raising=False)
    cmd = runner._base_cmd("newman", "collection.json", tmp_path / "r.json")
    n_idx = cmd.index("-n")
    assert cmd[n_idx + 1] == "1"
    t_idx = cmd.index("--timeout-request")
    assert cmd[t_idx + 1] == "30000"


def test_base_cmd_falls_back_on_invalid_int_env(runner, monkeypatch, tmp_path):
    monkeypatch.setenv("QA_POSTMAN_ITERATIONS", "not-a-number")
    monkeypatch.setenv("QA_POSTMAN_TIMEOUT_REQUEST_MS", "also-bad")
    cmd = runner._base_cmd("newman", "collection.json", tmp_path / "r.json")
    n_idx = cmd.index("-n")
    assert cmd[n_idx + 1] == "1"
    t_idx = cmd.index("--timeout-request")
    assert cmd[t_idx + 1] == "30000"


# ---- run_tests / run_failed orchestration -----------------------------------


def _fake_completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_tests_adds_folder_filter(runner, monkeypatch, tmp_path):
    coll = tmp_path / "collection.json"
    coll.write_text("{}")
    monkeypatch.setenv("QA_POSTMAN_COLLECTION", str(coll))
    # Guard against an ambient QA_POSTMAN_FOLDER leaking in from the shell —
    # otherwise a stray --folder from the CSV env var could land before the
    # filter-derived one and shift the index this test relies on.
    monkeypatch.delenv("QA_POSTMAN_FOLDER", raising=False)
    monkeypatch.setattr(nm, "_require_newman_cli", lambda: "newman")
    monkeypatch.setattr(nm, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(nm, "REPORT_PATH", tmp_path / "report.json")

    captured = {}

    def fake_safe_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed()

    monkeypatch.setattr(nm, "safe_run", fake_safe_run)
    monkeypatch.setattr(runner, "_normalize_report", lambda p: None)
    monkeypatch.setattr(runner, "_archive_report", lambda: None)

    runner.run_tests(filter="Auth")
    assert "--folder" in captured["cmd"]
    idx = captured["cmd"].index("--folder")
    assert captured["cmd"][idx + 1] == "Auth"


def test_run_failed_no_previous_report(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(nm, "REPORT_PATH", tmp_path / "missing.json")
    result = runner.run_failed()
    assert result["error"].startswith("no previous report.json")


def test_run_failed_no_prior_failures(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"tests": [{"outcome": "passed"}]}))
    monkeypatch.setattr(nm, "REPORT_PATH", p)
    result = runner.run_failed()
    assert result["info"] == "no previous failures to re-run"


def test_run_failed_scopes_to_parent_folders(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"tests": [
        {"outcome": "failed", "artifacts": {"request_response": {"parent_folder": "Auth"}}},
    ]}))
    monkeypatch.setattr(nm, "REPORT_PATH", p)
    coll = tmp_path / "collection.json"
    coll.write_text("{}")
    monkeypatch.setenv("QA_POSTMAN_COLLECTION", str(coll))
    monkeypatch.delenv("QA_POSTMAN_FOLDER", raising=False)
    monkeypatch.setattr(nm, "_require_newman_cli", lambda: "newman")
    monkeypatch.setattr(nm, "PROJECT_ROOT", tmp_path)

    captured = {}

    def fake_safe_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed()

    monkeypatch.setattr(nm, "safe_run", fake_safe_run)
    monkeypatch.setattr(runner, "_normalize_report", lambda p: None)
    monkeypatch.setattr(runner, "_archive_report", lambda: None)
    monkeypatch.setattr(runner, "get_report_summary", lambda: {"failed": 0})

    result = runner.run_failed()
    assert result["folders_rerun"] == 1
    idx = captured["cmd"].index("--folder")
    assert captured["cmd"][idx + 1] == "Auth"


def test_run_failed_degrades_to_full_rerun_without_folders(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"tests": [
        {"outcome": "failed", "artifacts": {"request_response": {"parent_folder": None}}},
    ]}))
    monkeypatch.setattr(nm, "REPORT_PATH", p)
    coll = tmp_path / "collection.json"
    coll.write_text("{}")
    monkeypatch.setenv("QA_POSTMAN_COLLECTION", str(coll))
    monkeypatch.delenv("QA_POSTMAN_FOLDER", raising=False)
    monkeypatch.setattr(nm, "_require_newman_cli", lambda: "newman")
    monkeypatch.setattr(nm, "PROJECT_ROOT", tmp_path)

    captured = {}

    def fake_safe_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed()

    monkeypatch.setattr(nm, "safe_run", fake_safe_run)
    monkeypatch.setattr(runner, "_normalize_report", lambda p: None)
    monkeypatch.setattr(runner, "_archive_report", lambda: None)
    monkeypatch.setattr(runner, "get_report_summary", lambda: {"failed": 0})

    result = runner.run_failed()
    assert result["folders_rerun"] == 0
    assert "--folder" not in captured["cmd"]


# ---- _walk_items — Postman collection tree walker ---------------------------


def test_walk_items_emits_leaf_line_with_method_and_path(runner):
    items = [{"name": "List pets", "request": {"method": "GET", "url": "https://x/pets"}}]
    out: list[str] = []
    runner._walk_items(items, out, [])
    assert out == ["GET https://x/pets :: List pets"]


def test_walk_items_recurses_into_folders_with_breadcrumb(runner):
    items = [{
        "name": "Auth",
        "item": [{"name": "Login", "request": {"method": "POST", "url": "https://x/login"}}],
    }]
    out: list[str] = []
    runner._walk_items(items, out, [])
    assert out == ["POST https://x/login :: Auth :: Login"]


def test_walk_items_handles_string_request(runner):
    items = [{"name": "Quick", "request": "https://x/quick"}]
    out: list[str] = []
    runner._walk_items(items, out, [])
    assert out == ["GET https://x/quick :: Quick"]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "_walk_items 對無 raw 的 struct URL 只讀 path，丟失 host（與"
        "_construct_url 的行為不一致，list_tests 輸出會缺主機名）；"
        "修 src 讓兩者一致後移除此標記。"
    ),
)
def test_walk_items_handles_url_struct_without_raw(runner):
    """`_walk_items` (used by list_tests) should include the host the same
    way `_construct_url` (used by report normalization) does for a
    struct-shaped URL with no `raw`."""
    items = [{"name": "Struct", "request": {
        "method": "GET",
        "url": {"host": ["api", "example", "com"], "path": ["v1", "pets"]},
    }}]
    out: list[str] = []
    runner._walk_items(items, out, [])
    assert out == ["GET api.example.com/v1/pets :: Struct"]


def test_walk_items_skips_non_dict_items(runner):
    out: list[str] = []
    runner._walk_items(["not-a-dict", None], out, [])
    assert out == []


def test_walk_items_skips_items_without_request(runner):
    out: list[str] = []
    runner._walk_items([{"name": "Empty folder marker"}], out, [])
    assert out == []


def test_list_tests_reports_parse_failure(runner, monkeypatch, tmp_path):
    bad = tmp_path / "collection.json"
    bad.write_text("not json")
    monkeypatch.setenv("QA_POSTMAN_COLLECTION", str(bad))
    out = runner.list_tests()
    assert "could not parse collection JSON" in out


def test_list_tests_truncates_over_200(runner, monkeypatch, tmp_path):
    items = [{"name": f"r{i}", "request": {"method": "GET", "url": f"https://x/{i}"}} for i in range(210)]
    coll = tmp_path / "collection.json"
    coll.write_text(json.dumps({"item": items}))
    monkeypatch.setenv("QA_POSTMAN_COLLECTION", str(coll))
    out = runner.list_tests()
    lines = out.splitlines()
    assert len(lines) == 201
    assert "more, truncated" in lines[-1]


# ---- _construct_url ----------------------------------------------------------


def test_construct_url_none_returns_none(runner):
    assert runner._construct_url(None) is None


def test_construct_url_string_passthrough(runner):
    assert runner._construct_url("https://x/y") == "https://x/y"


def test_construct_url_prefers_raw(runner):
    assert runner._construct_url({"raw": "https://x/y", "host": ["x"]}) == "https://x/y"


def test_construct_url_falls_back_to_host_path_join(runner):
    url = runner._construct_url({"host": ["api", "x", "com"], "path": ["v1", "pets"]})
    assert url == "api.x.com/v1/pets"


def test_construct_url_non_dict_non_string_returns_none(runner):
    assert runner._construct_url(123) is None


# ---- _decode_response_stream -------------------------------------------------


def test_decode_response_stream_none(runner):
    assert runner._decode_response_stream(None) is None


def test_decode_response_stream_plain_string(runner):
    assert runner._decode_response_stream("hello") == "hello"


def test_decode_response_stream_valid_buffer(runner):
    data = list(b"hello world")
    assert runner._decode_response_stream({"type": "Buffer", "data": data}) == "hello world"


def test_decode_response_stream_binary_returns_none(runner):
    # 0xFF 0xFE is not valid utf-8
    assert runner._decode_response_stream({"type": "Buffer", "data": [0xFF, 0xFE]}) is None


def test_decode_response_stream_malformed_dict_returns_none(runner):
    assert runner._decode_response_stream({"type": "Buffer", "data": "not-a-list"}) is None


# ---- _redact ------------------------------------------------------------------


def test_redact_masks_password_field():
    out = nm._redact('{"password": "hunter2"}')
    assert "hunter2" not in out
    assert "[REDACTED]" in out


def test_redact_masks_bearer_token():
    out = nm._redact("Authorization: Bearer abc.def.ghi")
    assert "abc.def.ghi" not in out


def test_redact_noop_with_no_redact_env(monkeypatch):
    monkeypatch.setenv("QA_NO_REDACT", "true")
    text = '"token": "secret"'
    assert nm._redact(text) == text


# ---- _normalize_report — Newman JSON → report.json --------------------------


def test_normalize_report_missing_file_writes_empty(runner, monkeypatch, tmp_path):
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(nm, "REPORT_PATH", report_path)
    runner._normalize_report(tmp_path / "missing-newman.json")
    data = json.loads(report_path.read_text())
    assert data["summary"]["total"] == 0


def test_normalize_report_malformed_json_writes_empty(runner, monkeypatch, tmp_path):
    newman_json = tmp_path / "newman.json"
    newman_json.write_text("not json")
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(nm, "REPORT_PATH", report_path)
    runner._normalize_report(newman_json)
    data = json.loads(report_path.read_text())
    assert data["summary"]["total"] == 0


def _newman_payload(executions, started=0, completed=0):
    return {
        "run": {
            "executions": executions,
            "timings": {"started": started, "completed": completed},
        }
    }


def test_normalize_report_converts_assertions_to_tests(runner, monkeypatch, tmp_path):
    payload = _newman_payload([{
        "item": {"name": "List pets"},
        "request": {"method": "GET", "url": "https://x/pets"},
        "response": {"code": 200, "responseTime": 120, "stream": None},
        "assertions": [
            {"assertion": "status is 200"},
            {"assertion": "has body", "error": {"message": "missing field", "stack": ""}},
        ],
    }], started=1000, completed=1500)
    newman_json = tmp_path / "newman.json"
    newman_json.write_text(json.dumps(payload))
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(nm, "REPORT_PATH", report_path)
    runner._normalize_report(newman_json)
    data = json.loads(report_path.read_text())
    assert data["summary"] == {"total": 2, "passed": 1, "failed": 1, "skipped": 0}
    assert data["duration"] == pytest.approx(0.5)
    failed = next(t for t in data["tests"] if t["outcome"] == "failed")
    assert "missing field" in failed["message"]


def test_normalize_report_stub_test_when_no_assertions(runner, monkeypatch, tmp_path):
    payload = _newman_payload([{
        "item": {"name": "Ping"},
        "request": {"method": "GET", "url": "https://x/ping"},
        "response": {"code": 200, "responseTime": 10, "stream": None},
        "assertions": [],
    }])
    newman_json = tmp_path / "newman.json"
    newman_json.write_text(json.dumps(payload))
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(nm, "REPORT_PATH", report_path)
    runner._normalize_report(newman_json)
    data = json.loads(report_path.read_text())
    assert data["summary"]["passed"] == 1
    assert "(no assertions)" in data["tests"][0]["nodeid"]


def test_normalize_report_falls_back_to_stats_when_no_executions(runner, monkeypatch, tmp_path):
    payload = {
        "run": {
            "executions": [],
            "timings": {},
            "stats": {"assertions": {"total": 5, "failed": 2}},
        }
    }
    newman_json = tmp_path / "newman.json"
    newman_json.write_text(json.dumps(payload))
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(nm, "REPORT_PATH", report_path)
    runner._normalize_report(newman_json)
    data = json.loads(report_path.read_text())
    assert data["summary"] == {"total": 5, "passed": 3, "failed": 2, "skipped": 0}
    assert data["tests"] == []


def test_normalize_report_captures_parent_folder(runner, monkeypatch, tmp_path):
    payload = _newman_payload([{
        "item": {"name": "Login", "parent": {"name": "Auth"}},
        "request": {"method": "POST", "url": "https://x/login"},
        "response": {"code": 200, "responseTime": 50, "stream": None},
        "assertions": [{"assertion": "ok"}],
    }])
    newman_json = tmp_path / "newman.json"
    newman_json.write_text(json.dumps(payload))
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(nm, "REPORT_PATH", report_path)
    runner._normalize_report(newman_json)
    data = json.loads(report_path.read_text())
    parent = data["tests"][0]["artifacts"]["request_response"]["parent_folder"]
    assert parent == "Auth"


# ---- get_report_summary / get_failure_details / get_all_test_details -------


def test_get_report_summary_missing(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(nm, "REPORT_PATH", tmp_path / "missing.json")
    assert "error" in runner.get_report_summary()


def test_get_failure_details_filters_by_id(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"tests": [
        {"nodeid": "GET a :: check", "outcome": "failed", "call": {"longrepr": "x", "duration": 0.1}},
        {"nodeid": "GET b :: check", "outcome": "failed", "call": {"longrepr": "y", "duration": 0.1}},
    ]}))
    monkeypatch.setattr(nm, "REPORT_PATH", p)
    result = runner.get_failure_details(test_id="GET a")
    assert len(result) == 1


def test_get_all_test_details_skips_entries_without_nodeid(runner, monkeypatch, tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"tests": [{"outcome": "passed"}]}))
    monkeypatch.setattr(nm, "REPORT_PATH", p)
    assert runner.get_all_test_details() == []


# ---- generate_test / get_history --------------------------------------------


def test_generate_test_explains_no_codegen(runner):
    msg = runner.generate_test("desc", "filename")
    assert "does not author test files" in msg


def test_get_history_computes_pass_rate(runner, monkeypatch, tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    (history / "20260101-000000-000000.json").write_text(json.dumps({
        "summary": {"total": 2, "passed": 1, "failed": 1, "skipped": 0},
        "duration": 1.0,
    }))
    monkeypatch.setattr(nm, "HISTORY_DIR", history)
    result = runner.get_history()
    assert result[0]["pass_rate"] == 50.0
