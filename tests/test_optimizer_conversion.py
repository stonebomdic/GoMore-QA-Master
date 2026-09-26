"""P4 Task 7 — recommendation → action 轉換率 + 無效指標剪枝。

契約：
- _recommendation_conversion(actions, records, window)：以 telemetry 中每次
  get_optimization_plan 呼叫為建議交付點，看其後 window 筆內是否出現
  auto_action_hint 指到的 tool。
- build_plan 輸出分流：無法對應具體行動的觀察型類別
  （ai_adoption / mcp_repeat / mcp_chain）降到 appendix_actions；
  轉換率 0 且交付 >= 3 次的類別也降級 —— 但 suite 品質核心訊號
  （broken / flaky / slow_regression）永不因未採納而隱藏。
"""
from __future__ import annotations

import json

from gomore_qa_master.tools import optimizer


def _rec(tool: str) -> dict:
    return {"ts": "t", "tool": tool, "args_hash": "h",
            "duration_ms": 1, "error_type": None}


BROKEN_ACTION = {
    "priority": "high", "category": "broken", "target": "t.py::x",
    "evidence": "e", "suggestion": "s",
    "auto_action_hint": 'call get_failure_details(test_id="x")',
}


def test_conversion_converted_after_delivery():
    records = [_rec("run_tests"), _rec("get_optimization_plan"),
               _rec("get_failure_details"), _rec("run_tests")]
    conv = optimizer._recommendation_conversion([BROKEN_ACTION], records)
    assert conv["deliveries"] == 1
    assert conv["by_category"]["broken"] == {"hinted": 1, "converted": 1, "rate": 1.0}


def test_conversion_not_converted():
    records = [_rec("get_optimization_plan"), _rec("run_tests"), _rec("list_tests")]
    conv = optimizer._recommendation_conversion([BROKEN_ACTION], records)
    assert conv["by_category"]["broken"]["rate"] == 0.0


def test_conversion_no_delivery_is_unmeasured():
    conv = optimizer._recommendation_conversion([BROKEN_ACTION], [_rec("run_tests")])
    assert conv["deliveries"] == 0
    assert conv["by_category"]["broken"]["rate"] is None


def test_conversion_respects_window():
    records = ([_rec("get_optimization_plan")]
               + [_rec("run_tests")] * 25
               + [_rec("get_failure_details")])  # 超出 window=20
    conv = optimizer._recommendation_conversion([BROKEN_ACTION], records, window=20)
    assert conv["by_category"]["broken"]["rate"] == 0.0


# --- 剪枝 ---------------------------------------------------------------

def test_split_advisory_categories_to_appendix():
    actions = [
        BROKEN_ACTION,
        {"priority": "medium", "category": "ai_adoption", "target": "g",
         "evidence": "e", "suggestion": "s"},
        {"priority": "low", "category": "mcp_repeat", "target": "t",
         "evidence": "e", "suggestion": "s"},
        {"priority": "low", "category": "mcp_chain", "target": "t",
         "evidence": "e", "suggestion": "s"},
    ]
    top, appendix = optimizer._split_actions(actions, {"deliveries": 0, "by_category": {}})
    assert [a["category"] for a in top] == ["broken"]
    assert {a["category"] for a in appendix} == {"ai_adoption", "mcp_repeat", "mcp_chain"}


def test_split_zero_conversion_demotes_non_suite_category():
    action = {"priority": "medium", "category": "custom_hinted", "target": "t",
              "evidence": "e", "suggestion": "s", "auto_action_hint": "call foo()"}
    conv = {"deliveries": 3,
            "by_category": {"custom_hinted": {"hinted": 1, "converted": 0, "rate": 0.0}}}
    top, appendix = optimizer._split_actions([action], conv)
    assert top == []
    assert appendix == [action]


def test_split_zero_conversion_never_hides_suite_signals():
    conv = {"deliveries": 5,
            "by_category": {"broken": {"hinted": 1, "converted": 0, "rate": 0.0}}}
    top, appendix = optimizer._split_actions([BROKEN_ACTION], conv)
    assert top == [BROKEN_ACTION]
    assert appendix == []


def test_build_plan_has_conversion_and_appendix(tmp_path, monkeypatch):
    monkeypatch.setattr(optimizer, "HISTORY_DIR", tmp_path / "none")
    gen = tmp_path / "generations.jsonl"
    gen.write_text("\n".join(json.dumps(
        {"ts": "t", "filename": f"test_gen_{i}.py", "description": "d",
         "source": "manual"}) for i in range(3)) + "\n", encoding="utf-8")
    monkeypatch.setattr(optimizer, "GENERATION_LOG", gen)
    monkeypatch.setattr(optimizer, "MODULES_LOG", tmp_path / "none2.jsonl")
    monkeypatch.setattr(optimizer, "TOOL_USAGE_LOG", tmp_path / "none3.jsonl")

    plan = optimizer.build_plan()
    # 採用率 0%（3 筆生成、無 run）→ ai_adoption 是觀察型 → 附錄
    assert "recommendation_conversion" in plan
    assert [a["category"] for a in plan["appendix_actions"]] == ["ai_adoption"]
    assert all(a["category"] != "ai_adoption" for a in plan["prioritized_actions"])

    md = optimizer._to_markdown(plan)
    assert "Appendix" in md and "ai_adoption" in md
