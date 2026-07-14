"""P4 Task 6 — build_plan backtest 迴歸：固定語料 replay，鎖定啟發式。

語料手標期望：
- flaky_test：P/F 交替 10 次 → flaky，high
- broken_test：前 7 P、後 3 F 同 error signature → broken，high + auto_action_hint
- stable_test：10 連 P → stable_passing，low（runs>=10 才出 action）
- analyze_url 錯誤率 2/3 → mcp_error_prone，medium
- 生成 3 筆未進最新 run → ai_adoption（採用率 0）
- 發現模組無對應 test 檔 → coverage_gap
優先序：high 先於 medium 先於 low。
"""
from __future__ import annotations

import json

import pytest

from gomore_qa_master.tools import optimizer


def _report(tests: list[dict]) -> dict:
    return {"tests": tests}


def _t(nodeid: str, outcome: str, duration: float = 0.1, longrepr: str = "") -> dict:
    call: dict = {"duration": duration}
    if longrepr:
        call["longrepr"] = longrepr
    return {"nodeid": nodeid, "outcome": outcome, "call": call}


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """10 run 歷史 + telemetry JSONL 的固定語料。"""
    history = tmp_path / "history"
    history.mkdir()
    for i in range(10):
        tests = [
            _t("tests/test_a.py::flaky_test",
               "passed" if i % 2 == 0 else "failed",
               longrepr="" if i % 2 == 0 else f"AssertionError: race at :{i}"),
            _t("tests/test_a.py::broken_test",
               "passed" if i < 7 else "failed",
               longrepr="" if i < 7 else "AssertionError: assert login_button.visible :42"),
            _t("tests/test_a.py::stable_test", "passed"),
        ]
        if i == 9:
            tests.append(_t("tests/test_a.py::new_test", "passed"))
        (history / f"run-{i:02d}.json").write_text(
            json.dumps(_report(tests)), encoding="utf-8")

    usage = tmp_path / "tool-usage.jsonl"
    records = (
        [{"ts": "t", "tool": "analyze_url", "args_hash": "u1",
          "duration_ms": 100, "error_type": "TimeoutError"}] * 2
        + [{"ts": "t", "tool": "analyze_url", "args_hash": "u2",
            "duration_ms": 100, "error_type": None}]
        + [{"ts": "t", "tool": "run_tests", "args_hash": "r1",
            "duration_ms": 500, "error_type": None}] * 2
    )
    usage.write_text("\n".join(json.dumps(r) for r in records) + "\n",
                     encoding="utf-8")

    gen = tmp_path / "generations.jsonl"
    gen.write_text("\n".join(json.dumps(
        {"ts": "t", "filename": f"test_gen_{i}.py", "description": "d",
         "source": "manual"}) for i in range(3)) + "\n", encoding="utf-8")

    modules = tmp_path / "modules.jsonl"
    modules.write_text(json.dumps(
        {"ts": "t", "url": "http://x", "module_names": ["Checkout Flow"]})
        + "\n", encoding="utf-8")

    proj = tmp_path / "proj"
    proj.mkdir()  # 空專案 → Checkout Flow 無對應 test 檔

    monkeypatch.setattr(optimizer, "HISTORY_DIR", history)
    monkeypatch.setattr(optimizer, "TOOL_USAGE_LOG", usage)
    monkeypatch.setattr(optimizer, "GENERATION_LOG", gen)
    monkeypatch.setattr(optimizer, "MODULES_LOG", modules)
    monkeypatch.setattr(optimizer, "PROJECT_ROOT", proj)
    return tmp_path


def _actions_by_cat(plan: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for a in plan["prioritized_actions"]:
        out.setdefault(a["category"], []).append(a)
    return out


def test_backtest_suite_categories(corpus):
    plan = optimizer.build_plan()
    cats = plan["suite_quality"]["by_category"]
    assert cats.get("flaky") == 1
    assert cats.get("broken") == 1
    assert cats.get("stable_passing") == 1
    assert cats.get("new") == 1


def test_backtest_prioritized_actions(corpus):
    plan = optimizer.build_plan()
    by_cat = _actions_by_cat(plan)

    flaky = by_cat["flaky"][0]
    assert flaky["priority"] == "high"
    assert flaky["target"] == "tests/test_a.py::flaky_test"
    assert "flake_score=1.0" in flaky["evidence"]

    broken = by_cat["broken"][0]
    assert broken["priority"] == "high"
    assert broken["target"] == "tests/test_a.py::broken_test"
    assert broken["auto_action_hint"] == 'call get_failure_details(test_id="broken_test")'

    stable = by_cat["stable_passing"][0]
    assert stable["priority"] == "low"

    err = by_cat["mcp_error_prone"][0]
    assert err["target"] == "analyze_url"
    assert "67%" in err["evidence"]

    # ai_adoption 是觀察型類別（Task 7 剪枝）→ 進 appendix 而非 top-line
    plan_appendix = plan["appendix_actions"]
    adoption = [a for a in plan_appendix if a["category"] == "ai_adoption"][0]
    assert "0%" in adoption["evidence"]
    assert "ai_adoption" not in by_cat

    gap = by_cat["coverage_gap"][0]
    assert gap["target"] == "Checkout Flow"
    assert "test_checkout_flow.py" in gap["suggestion"]


def test_backtest_priority_ordering(corpus):
    plan = optimizer.build_plan()
    order = {"high": 0, "medium": 1, "low": 2}
    ranks = [order[a["priority"]] for a in plan["prioritized_actions"]]
    assert ranks == sorted(ranks), "high → medium → low 順序被破壞"


def test_backtest_empty_corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(optimizer, "HISTORY_DIR", tmp_path / "none")
    monkeypatch.setattr(optimizer, "TOOL_USAGE_LOG", tmp_path / "none.jsonl")
    monkeypatch.setattr(optimizer, "GENERATION_LOG", tmp_path / "none2.jsonl")
    monkeypatch.setattr(optimizer, "MODULES_LOG", tmp_path / "none3.jsonl")
    plan = optimizer.build_plan()
    assert plan["suite_quality"].get("empty") is True
    assert plan["prioritized_actions"] == []
