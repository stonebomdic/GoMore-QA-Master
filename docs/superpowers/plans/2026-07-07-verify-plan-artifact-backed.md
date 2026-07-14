# verify_plan 真驗證 Implementation Plan（P0）

> 設計：[`specs/2026-07-07-verify-plan-artifact-backed-design.md`](../specs/2026-07-07-verify-plan-artifact-backed-design.md)
> 原則：TDD、純加法、零既有行為變更。每個 Task 走「失敗測試 → 確認失敗 → 最小實作 → 確認通過 → commit」。

## 檔案結構

| 檔案 | 動作 |
|---|---|
| `src/gomore_qa_master/tools/assertions.py` | **新增** — 純函式型別化 checker（無 I/O） |
| `tests/test_assertions.py` | **新增** — checker 單元測試 |
| `src/gomore_qa_master/tools/qa_plan.py` | **修改** — CP schema 加 `assert`、verify 分流、回應 tiering、strict |
| `tests/test_qa_plan_verified.py` | **新增** — verified 路徑 + 紅隊 + 反造假測試 |
| `src/gomore_qa_master/runners/api_security.py` | **修改**（Phase 2） — 掃描結果落地 `scan-results.json` |
| `src/gomore_qa_master/config.py` | **修改**（Phase 2） — 加 `SCAN_RESULTS_PATH` |
| `examples/sample_vulnerable_api/tests/test_verified_plan_dogfood.py` | **新增**（Phase 2） — finding_* dogfood |
| `skills/gomore-qa-master/SKILL.md` · `reference/tool-surface.md` · `README.md` · `docs/walkthrough-api.md` | **修改**（Phase 3） — 文件與範例 |
| `pyproject.toml` · `.claude-plugin/plugin.json` · `.codex-plugin/plugin.json` | **修改**（Phase 3） — 版本 → 0.9.6 |

---

## Phase 0 — 純函式 checker（本機、無需 token、無 I/O）

### Task 1: 型別化斷言 checker `assertions.py`

- [ ] **Step 1: 寫失敗測試** `tests/test_assertions.py`
  - `test_passed` 命中 passed row → satisfied，`actual` 帶該 row 的 nodeid/outcome。
  - `test_passed` 命中 **failed** row → **not** satisfied（核心紅隊）。
  - `test_passed` 找不到 test_id → not satisfied，`actual.error == "not_found"`。
  - `test_id` 比對：預設 exact / `::suffix` 各一測；**裸 substring 需 `match:"substring"` 才啟用**，且測 `test_login` 不誤中 `test_login_redirect`（預設模式）。
  - `test_outcome(expected="failed")` 對 failed row → satisfied。
  - `finding_absent(rule_id, endpoint)`：findings 中無該項 → satisfied；有 → not。
  - `finding_present`：反向。
  - **fail-closed**：`artifact=None`（產物缺失）時 `finding_absent` **也**回 not-satisfied + `artifact_missing`（不可把「沒證據」當「沒漏洞」）。
  - 未知 `assert.type` → 回結構化 error（不 raise）。
- [ ] **Step 2: 跑測試確認失敗** `pytest tests/test_assertions.py`（模組不存在 → import error / red）
- [ ] **Step 3: 寫最小實作** `src/gomore_qa_master/tools/assertions.py`

```python
"""v0.9.6 — 型別化斷言 checker（純函式，無 I/O）。

verify_plan 對帶 `assert` 的 CP 呼叫這裡；輸入是「已由 qa_plan.py 載入的
權威產物 dict」，輸出是 (satisfied, actual)。刻意不碰檔案/env，方便單元測。
"""
from __future__ import annotations
from typing import Any


def _iter_test_rows(report: dict[str, Any]) -> list[dict]:
    tests = report.get("tests")
    return [t for t in tests if isinstance(t, dict)] if isinstance(tests, list) else []


def _match_test_id(nodeid: str, test_id: str, mode: str) -> bool:
    if mode == "substring":                      # opt-in、較鬆
        return test_id in nodeid
    return nodeid == test_id or nodeid.endswith("::" + test_id)  # 預設 exact-or-suffix


def _check_test_outcome(assertion, report, expected) -> tuple[bool, dict]:
    test_id = str(assertion.get("test_id") or "").strip()
    if not test_id:
        return False, {"error": "bad_assertion", "hint": "test_id required"}
    mode = str(assertion.get("match") or "exact")
    rows = [r for r in _iter_test_rows(report)
            if _match_test_id(str(r.get("nodeid", "")), test_id, mode)]
    if not rows:
        return False, {"error": "not_found", "test_id": test_id}
    # 多命中：全部須符合 expected（避免 parametrize 只過一個就算過）
    outcomes = [str(r.get("outcome")) for r in rows]
    ok = all(o == expected for o in outcomes)
    return ok, {"test_id": test_id, "matched": len(rows), "outcomes": outcomes,
                "expected": expected}


def _finding_matches(f: dict, rule_id: str, endpoint: str | None) -> bool:
    if str(f.get("rule_id")) != rule_id:
        return False
    if endpoint and str(f.get("endpoint")) != endpoint:
        return False
    return True


def _check_finding(assertion, scan, present: bool) -> tuple[bool, dict]:
    rule_id = str(assertion.get("rule_id") or "").strip()
    if not rule_id:
        return False, {"error": "bad_assertion", "hint": "rule_id required"}
    endpoint = assertion.get("endpoint")
    findings = scan.get("findings")
    if not isinstance(findings, list):
        return False, {"error": "artifact_missing", "hint": "no findings[] in scan artifact"}
    hits = [f for f in findings if isinstance(f, dict)
            and _finding_matches(f, rule_id, endpoint)]
    satisfied = (len(hits) > 0) if present else (len(hits) == 0)
    return satisfied, {"rule_id": rule_id, "endpoint": endpoint, "hits": len(hits)}


# artifact_kind 告訴呼叫端要餵哪個產物（"report" | "scan"）
_DISPATCH = {
    "test_passed": ("report", lambda a, art: _check_test_outcome(a, art, "passed")),
    "test_outcome": ("report", lambda a, art: _check_test_outcome(a, art, str(a.get("expected") or "passed"))),
    "finding_absent": ("scan", lambda a, art: _check_finding(a, art, present=False)),
    "finding_present": ("scan", lambda a, art: _check_finding(a, art, present=True)),
}


def artifact_kind_for(assert_type: str) -> str | None:
    entry = _DISPATCH.get(assert_type)
    return entry[0] if entry else None


def evaluate(assertion: dict[str, Any], artifact: dict[str, Any] | None) -> tuple[bool, dict]:
    """回 (satisfied, actual)。artifact 為 None（載入失敗）→ 未滿足。"""
    a_type = str(assertion.get("type") or "").strip()
    entry = _DISPATCH.get(a_type)
    if entry is None:
        return False, {"error": "unknown_assert_type", "type": a_type}
    if artifact is None:
        return False, {"error": "artifact_missing", "type": a_type}
    return entry[1](assertion, artifact)
```

- [ ] **Step 4: 跑測試確認通過** `pytest tests/test_assertions.py`
- [ ] **Step 5: Commit** — `feat(verify): 型別化斷言 checker（純函式 assertions.py）`

---

## Phase 1 — 接進 verify_plan（功能測試家族，用 report.json）

### Task 2: CP schema 接受 `assert`

- [ ] **Step 1: 寫失敗測試**（加進 `tests/test_qa_plan_verified.py`）
  - `qa_plan` 帶 `critical_points=[{id, description, assert:{type:"test_passed", test_id:"x"}}]` → 回傳的 CP 保留 `assert`。
  - 沒帶 `assert` 的 CP 行為與現況一致（回歸保險）。
  - `assert` 非 dict / 缺 `type` → `bad_critical_points`。
  - **`strict` 建立時可宣告並存進 plan**：`qa_plan(strict=true)` → plan dict 帶 `strict:true`，且往返持久化（`_plan_to_json`/`_plan_from_json`）保留。
- [ ] **Step 2: 跑測試確認失敗**
- [ ] **Step 3: 最小實作** — 改 `_CriticalPoint`（加 `assertion: dict | None = None` 欄位）、`_normalize_critical_points`（解析並淺驗 `assert`）、`to_dict`（有才輸出 `assert`）、`_plan_to_json`/`_plan_from_json`（持久化往返帶上 `assert`）。
  - 注意：dataclass 新欄位給預設值，避免既有建構呼叫爆掉。
- [ ] **Step 4: 跑測試確認通過**（含 `pytest tests/test_qa_plan_persistence.py` 確認往返不壞）
- [ ] **Step 5: Commit** — `feat(verify): critical_point 支援型別化 assert 欄位`

### Task 3: verify_plan 分流 + 產物載入 + 回應 tiering

- [ ] **Step 1: 寫失敗測試**
  - **紅隊**：report 內 `test_login` outcome=failed；verified CP `test_passed(test_login)` → `satisfied=false`、`tier="verified"`、`actual.outcomes==["failed"]`。
  - **兩層並存**：同一 plan 一個 attested（`verification_hint`）+ 一個 verified，回應各自 tier 正確；top-level `verification` 統計正確。
  - **反造假**：verified CP 即使 host 傳 `evidence=[{"nodeid":"...test_login...","outcome":"passed"}]`（偽造），仍以磁碟 report.json（failed）為準 → `satisfied=false`。
  - 產物缺檔：verified CP → `satisfied=false`、`actual.error=="artifact_missing"`（**不**靜默略過）。
  - `strict=true`：全 verified 且 satisfied → `passed`；混入 attested → `incomplete`。
  - `strict` 預設 false 時 status 計算與今日一致。
  - **strict 只能收緊不能放鬆**：plan 建立為 strict、verify 傳 `strict=false` → 仍以 strict 計（測試鎖定）；plan 非 strict、verify 傳 `strict=true` → 臨時 strict 檢視。有效 strict = `plan.strict or arg.strict`。
- [ ] **Step 2: 跑測試確認失敗**
- [ ] **Step 3: 最小實作**（`verify_plan_tool`）
  - 產物載入器（call-time、lazy）：`_load_report()` 復用 `_default_report_path` + `_autodiscover_evidence` 已有邏輯，回整包 dict（非只 tests list）；`_load_scan()`（Phase 2 才接線，先留 stub 回 None）。
  - CP 迴圈分流：

```python
from . import assertions  # 或 from ..tools import assertions

def _eval_cp(cp, evidence, artifacts):
    if cp.assertion:                       # verified 路徑
        kind = assertions.artifact_kind_for(str(cp.assertion.get("type")))
        artifact = artifacts.get(kind)     # host evidence 對此 CP 無效
        satisfied, actual = assertions.evaluate(cp.assertion, artifact)
        return {"tier": "verified", "assertion": cp.assertion,
                "actual": actual, "matched_evidence": [], "satisfied": satisfied}
    matched = _match_cp(cp, evidence)      # attested 路徑（不變）
    return {"tier": "attested", "matched_evidence": matched,
            "satisfied": bool(matched)}
```
  - `artifacts` 只在「有 verified CP 需要該 kind」時才載入（省 I/O）。
  - 組 checklist 時合入 `tier`/`assertion`/`actual`；算 top-level `verification` 統計。
  - status：有效 strict = `plan.strict or arg.strict`。非 strict 沿用舊算法；strict 另計（全 verified 且全 satisfied → passed）。
- [ ] **Step 4: 跑測試確認通過** — **關鍵**：`pytest tests/` 全綠，特別確認 `test_qa_plan*.py` 既有 71 測試零變更。若有測試對 checklist 做「整個 dict 相等」斷言而被新增的 `tier` 鍵打到，該測試改為子集斷言（記錄於 commit message）。
- [ ] **Step 5: Commit** — `feat(verify): verified/attested 兩層 + 反造假 + strict 模式`

---

## Phase 2 — 安全掃描家族（finding_present / finding_absent）

### Task 4: 掃描結果落地 `scan-results.json`

- [ ] **Step 1: 寫失敗測試**（`tests/test_runner_api_security.py` 增補）
  - `run_scan` 成功後於解析出的 scan 路徑寫出 `{findings:[...], meta:{...}}`；redaction 沿用既有機制（token 不落地）。
  - 唯讀 FS → best-effort 不 raise。
- [ ] **Step 2: 跑測試確認失敗**
- [ ] **Step 3: 最小實作** — `config.py` 加 `SCAN_RESULTS_PATH`（`GOMORE_QA_SCAN_PATH` → `<root>/scan-results.json` → `./scan-results.json`）；`api_security.py` 掃描尾端原子寫檔（比照 `_persist_plan` 的 tempfile+os.replace）。
- [ ] **Step 4: 跑測試確認通過**
- [ ] **Step 5: Commit** — `feat(scan): 掃描結果落地 scan-results.json（verify 產物契約）`

### Task 5: verify_plan 接 scan 產物 + dogfood

- [ ] **Step 1: 寫失敗測試** `examples/sample_vulnerable_api/tests/test_verified_plan_dogfood.py`
  - 對 vuln 端點 `finding_present(rule_id, endpoint)` → satisfied。
  - 對 safe 端點 `finding_absent(rule_id, endpoint)` → satisfied。
  - 反例各一（present 在 safe → not；absent 在 vuln → not）。
- [ ] **Step 2: 跑測試確認失敗**（`_load_scan` 仍為 stub）
- [ ] **Step 3: 最小實作** — 把 Task 3 的 `_load_scan()` stub 換成讀 `SCAN_RESULTS_PATH`。
- [ ] **Step 4: 跑測試確認通過** — 全套件 + dogfood 綠。
- [ ] **Step 5: Commit** — `feat(verify): finding_present/absent 接 scan 產物 + dogfood`

---

## Phase 3 — 文件、版本、收尾

### Task 6: 文件與範例

- [ ] SKILL.md：verify_plan 段落加型別化斷言用法 + 「verified vs attested」說明 + strict gate 建議。
- [ ] `reference/tool-surface.md`、`README.md`（Tool surface / Self-improvement loop）、`docs/walkthrough-api.md`：加 `assert` 範例（bookend 用 `finding_absent` 當安全 gate）。
- [ ] `tests/test_skill_distribution.py`：若有斷言 tool schema/文件同步，補上新欄位。
- [ ] **Commit** — `docs(verify): 型別化斷言用法 + verified/attested 說明`

### Task 7: 版本 bump + 全綠驗收

- [ ] `pyproject.toml` / `.claude-plugin/plugin.json` / `.codex-plugin/plugin.json` → `0.9.6`。（順帶：`serverInfo.version` 與 pyproject 對齊——見架構評估的 hygiene 項；本 Task 一併收斂 single-source。）
- [ ] `pytest tests/ examples/` 全綠；`test_smoke.py` 的 19-tool 計數不變（本次不加 tool，只擴 verify_plan schema）。
- [ ] **Commit** — `chore: v0.9.6 — verify_plan artifact-backed assertions`

---

## 驗收（對應設計 §7）

1. 紅隊測試綠：failed 測試的 `test_passed` 回 not satisfied，舊 hint 路徑仍回 satisfied。
2. 反造假測試綠：verified CP 無視偽造 evidence。
3. 既有 71 個 qa_plan 測試零行為變更。
4. dogfood：finding_present/absent 在 sample_vulnerable_api 上正確。
5. 文件更新完成，SKILL.md 有範例。

## 落地順序與可交付邊界

- **Phase 0 + 1 即可獨立交付**（修掉可示範的破洞、兩層並存、反造假、strict），完全不碰掃描器——風險最低、價值最高，建議先合這一段。
- **Phase 2** 需動 `api_security.py` 落地產物契約，獨立一個 PR。
- **Phase 3** 純文件/版本，隨 Phase 1 或 2 附掛。

### 與 P1–P4 的排序（見 [架構硬化 plan](2026-07-07-architecture-hardening-p1-p4.md#與-p0-的交互務必遵守的排序)）
- **P0 先出貨**；P2 的 tool-surface golden 必須在 P0 合併**之後**擷取（否則 `assert`/`strict` 加法欄位污染 snapshot）。
- P1 落地後，`verify_plan` handler 在 registry 標 `blocking=True` 一併 offload 檔案讀。
- P0 Phase 2 的 `scan-results.json` 落地，若 P3 已把掃描包成 `ApiSecurityScanner`，寫檔邏輯放進該 class。
