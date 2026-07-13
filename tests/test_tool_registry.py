"""P2 — Tool surface characterization golden + registry 契約。

Task 2（重構前擷取）：list_tools 輸出逐字鎖定在
tests/fixtures/tool_surface_golden.json；每個 tool 經 _dispatch 以最小
合法 args（依賴 monkeypatch 掉）呼叫都回 list[TextContent] 且不 raise。
重構（registry + dict dispatch）前後這些測試必須逐字綠。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from mcp.types import TextContent

from gomore_qa_master import server

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "tool_surface_golden.json"

GOLDEN_REQUIRED = {
    "get_runner_info": [],
    "list_tests": [],
    "run_tests": [],
    "run_failed": [],
    "get_test_report": [],
    "get_failure_details": [],
    "generate_test": ["description", "filename"],
    "codegen": ["url"],
    "generate_html_report": [],
    "get_test_history": [],
    "get_optimization_plan": [],
    "analyze_url": ["url"],
    "analyze_screen": [],
    "init_qa_knowledge": [],
    "get_qa_context": [],
    "auto_generate_tests": ["url"],
    "run_api_security_scan": ["spec_url"],
    "qa_plan": ["critical_points", "task"],
    "verify_plan": ["plan_id"],
}


def _list_tools():
    return asyncio.run(server.list_tools())


def test_tool_count_and_names():
    tools = _list_tools()
    assert len(tools) == 19
    assert [t.name for t in tools] == list(GOLDEN_REQUIRED)


def test_required_fields_match_golden():
    tools = _list_tools()
    actual = {t.name: sorted(t.inputSchema.get("required", [])) for t in tools}
    assert actual == {k: sorted(v) for k, v in GOLDEN_REQUIRED.items()}


def test_tool_surface_matches_golden_fixture():
    """整個 tool surface（name/description/inputSchema）與重構前 snapshot
    逐字相同 —— schema 搬家（schemas.py）不得改變任何位元。"""
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    tools = _list_tools()
    actual = [
        {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
        for t in tools
    ]
    # 與 fixture 相同的正規化（sort_keys）後比對
    norm = lambda x: json.loads(json.dumps(x, ensure_ascii=False, sort_keys=True))  # noqa: E731
    assert norm(actual) == norm(golden)


# ---------------------------------------------------------------------------
# 每個 tool 的最小合法呼叫（依賴 monkeypatch 掉）— dispatch 語義 golden
# ---------------------------------------------------------------------------

@pytest.fixture
def stubbed(monkeypatch, tmp_path):
    """把所有會碰 subprocess / 網路 / 磁碟的依賴換成 stub。"""
    monkeypatch.setattr(server.runner, "list_tests", lambda: "t.py::t1")
    monkeypatch.setattr(server.runner, "run_tests", lambda **k: {"ok": 1})
    monkeypatch.setattr(server.runner, "run_failed", lambda: {"ok": 1})
    monkeypatch.setattr(server.reporter, "get_report_summary", lambda: {"ok": 1})
    monkeypatch.setattr(server.reporter, "get_failure_details", lambda tid: {"ok": tid})
    monkeypatch.setattr(server.reporter, "get_history", lambda limit: {"runs": []})
    monkeypatch.setattr(server.generator, "generate_test", lambda *a, **k: "generated")
    monkeypatch.setattr(server.generator, "codegen", lambda *a, **k: "codegen ok")
    monkeypatch.setattr(server.qa_context, "load_context", lambda s: {"ok": 1})
    monkeypatch.setattr(server.qa_context, "init_qa_knowledge", lambda overwrite: {"ok": 1})
    monkeypatch.setattr(server.html_reporter, "write_report", lambda out: str(tmp_path / out))
    monkeypatch.setattr(server.optimizer, "build_plan", lambda **k: {"plan": []})
    monkeypatch.setattr(server.optimizer, "write_plan", lambda plan=None: None)
    monkeypatch.setattr(server.telemetry, "log_generation", lambda *a, **k: None)
    monkeypatch.setattr(server.telemetry, "log_discovered_modules", lambda *a, **k: None)

    async def fake_analyze_url(url, **k):
        return {"modules": []}

    async def fake_auto_generate(**k):
        return {"generated": []}

    monkeypatch.setattr(server.analyzer, "analyze_url", fake_analyze_url)
    monkeypatch.setattr(server.analyzer, "analyze_screen", lambda *a, **k: {"modules": []})
    monkeypatch.setattr(server, "_auto_generate_tests", fake_auto_generate)
    monkeypatch.setattr(
        "gomore_qa_master.runners.api_security.run_scan",
        lambda *a, **k: {"findings": []},
    )
    monkeypatch.setenv("QA_PLAN_PERSIST", "false")
    from gomore_qa_master.tools.qa_plan import _reset_cache_for_tests
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


MINIMAL_ARGS = {
    "get_runner_info": {},
    "list_tests": {},
    "run_tests": {},
    "run_failed": {},
    "get_test_report": {},
    "get_failure_details": {"test_id": "t.py::t1"},
    "generate_test": {"description": "d", "filename": "f"},
    "codegen": {"url": "http://localhost"},
    "generate_html_report": {},
    "get_test_history": {},
    "get_optimization_plan": {},
    "analyze_url": {"url": "http://localhost"},
    "analyze_screen": {},
    "init_qa_knowledge": {},
    "get_qa_context": {},
    "auto_generate_tests": {"url": "http://localhost"},
    "run_api_security_scan": {"spec_url": "http://localhost/openapi.json"},
    # qa_plan / verify_plan 另測（有先後順序）
}


@pytest.mark.parametrize("tool_name", sorted(MINIMAL_ARGS))
def test_dispatch_smoke_returns_textcontent(stubbed, tool_name):
    result = asyncio.run(server._dispatch(tool_name, MINIMAL_ARGS[tool_name]))
    assert isinstance(result, list) and result
    assert all(isinstance(c, TextContent) for c in result)


def test_dispatch_smoke_plan_bookends(stubbed):
    plan_res = asyncio.run(server._dispatch(
        "qa_plan", {"task": "t", "critical_points": ["cp1"]}))
    plan = json.loads(plan_res[0].text)
    verify_res = asyncio.run(server._dispatch(
        "verify_plan", {"plan_id": plan["plan_id"], "evidence": ["cp1 done"]}))
    assert isinstance(verify_res[0], TextContent)
    assert json.loads(verify_res[0].text)["plan_id"] == plan["plan_id"]


def test_dispatch_unknown_tool():
    result = asyncio.run(server._dispatch("no_such_tool", {}))
    assert "未知的 tool" in result[0].text
