# verify_plan 真驗證設計 — Artifact-backed Assertions（P0）

> 目標版本：v0.9.6（**純加法、向後相容**，不改既有 71 個 qa_plan 測試的行為）
> 對應架構評估：P0 —「verify_plan 的『驗證』目前是自我實現的」

## 1. 問題陳述

`verify_plan` 目前是 plan→act→verify bookend 的核心，賣點是「杜絕誤判」。但實際比對邏輯（`tools/qa_plan.py:499-511`，`_match_cp`）有兩個結構性破洞：

1. **自我實現**：同一個 host LLM 既撰寫 CP 的 `verification_hint`，又在 verify 時提供 `evidence`。比對僅是 case-insensitive substring，所以 agent 寫 hint `"login works"` + 餵 evidence `"login works"` 即 `satisfied=true`。
2. **substring 無法表達斷言語義**：即使開 `auto_discover` 從 `report.json` 拉入真實 test rows，比對仍是「hint 字串是否出現在某 row 的攤平字串」。一個 **失敗** 的測試，其 `nodeid` 照樣出現在報告裡——CP 的 hint 只要寫測試名（如 `test_login`）就會被判為 `satisfied`，**即使該測試 outcome=failed**。

**可示範的失敗案例**（將作為紅隊測試）：
- `report.json` 內 `test_login` 的 `outcome="failed"`。
- CP：`{id: "CP1", verification_hint: "test_login"}`，`verify_plan(auto_discover=true)`。
- 現況：`satisfied=true`（錯）。因為 `"test_login"` 是 `nodeid` 的子字串。
- 期望（本設計後）：型別化斷言 `{type: "test_passed", test_id: "test_login"}` → `satisfied=false`。

對一個以「可信驗證」為差異點的 QA 工具，這是槓桿最高的修正。

## 2. 設計模型：Attested vs Verified 兩層

引入一個**可選**的型別化斷言欄位 `assert`，與既有自由文字 `verification_hint` 並存：

| 層級 | 觸發 | 證據來源 | 可否被 host 造假 | 標記 |
|---|---|---|---|---|
| **verified** | CP 帶 `assert` | 工具自己從**權威產物**載入（report.json / scan-results.json） | 否（host 提供的 evidence 對此 CP 無效） | `tier: "verified"` |
| **attested** | CP 只有 `verification_hint`（現況） | host 提供的 evidence + auto_discover | 是（弱保證） | `tier: "attested"` |

**關鍵不變量**：對 `verified` CP，工具**忽略 host 傳入的 `evidence`**，只採信自己從磁碟載入的權威產物。這是讓驗證「擋得住造假」的根本。

向後相容：沒有 `assert` 的 CP 行為與今日**完全一致**（走 attested 路徑），所以既有測試不受影響；新欄位皆為加法。

## 3. CP schema 演進

```jsonc
{
  "id": "CP1",
  "description": "登入測試通過",
  // 舊路徑（attested）— 維持不變
  "verification_hint": "test_login",       // 可選
  // 新路徑（verified）— 出現時該 CP 走型別化檢查
  "assert": {
    "type": "test_passed",
    "test_id": "test_login"
  }
}
```

`assert.type` 首波支援兩個家族：

### 3.1 功能測試家族（Phase 1，用 report.json）
- `test_passed` — `{type, test_id}`：report 中比對到 `test_id` 的 row 存在**且** `outcome == "passed"`。
- `test_outcome` — `{type, test_id, expected}`：`expected ∈ {passed, failed, skipped, error}`，row 的 `outcome` 須等於 `expected`。
- `test_id` 比對規則（可預測、有文件）：**預設** `row.nodeid == test_id` 或 `row.nodeid` 以 `"::" + test_id` 結尾（exact-or-suffix）。裸 substring 太鬆（`test_login` 會誤中 `test_login_redirect`），故降為 **opt-in**：`assert.match: "substring"` 才啟用。**找不到 = 未滿足**（不是靜默 pass）。

### 3.2 安全掃描家族（Phase 2，用 scan-results.json）
- `finding_absent` — `{type, rule_id, endpoint?}`：掃描產物中**沒有**符合 `rule_id`（+ 選填 `endpoint`）的 finding。這是安全 gate 最常用的（例：`/orders/{id}` 上沒有 BOLA finding = pass）。
- `finding_present` — `{type, rule_id, endpoint?}`：反向，用於驗證掃描器確實抓到已知漏洞（dogfood）。

## 4. 權威產物契約

| 產物 | 路徑解析（沿用既有邏輯） | 產生者 |
|---|---|---|
| `report.json`（pytest-json-report） | `GOMORE_QA_REPORT_PATH` → `<QA_PROJECT_ROOT>/report.json` → `./report.json`（`_default_report_path()`，已存在） | `run_tests` |
| `scan-results.json`（新增契約） | `GOMORE_QA_SCAN_PATH` → `<QA_PROJECT_ROOT>/scan-results.json` → `./scan-results.json` | `run_api_security_scan`（Phase 2 讓它落地寫檔） |

產物載入失敗（缺檔/壞檔）時，verified CP 一律 **未滿足**，並在該 CP 回傳 `actual: {error: "artifact_missing", ...}` —— 缺證據不能算通過（對比現況 auto_discover 是 best-effort 靜默略過；verified 路徑相反，**缺證據即 fail**）。

**fail-closed 特別注意 `finding_absent`**：「沒有 finding = pass」的斷言，若掃描產物缺失就回 satisfied 會極危險（把「沒證據」當成「沒漏洞」）。本設計明定：產物缺失 → `evaluate` 回 not-satisfied + `artifact_missing`，`finding_absent` 也不例外（fail-closed）。需有測試明確鎖定此行為。

## 5. verify_plan 回應演進（加法）

每個 checklist 項目新增：
```jsonc
{
  "id": "CP1", "description": "...", "satisfied": false,
  "tier": "verified",                          // 新增："verified" | "attested"
  "assertion": {"type": "test_passed", "test_id": "test_login"},  // 新增（verified 才有）
  "actual": {"nodeid": "tests/test_login.py::test_login", "outcome": "failed"},  // 新增：工具看到的真實值
  "matched_evidence": []                        // attested 仍沿用
}
```

top-level 新增 `verification` 分層統計：
```jsonc
"verification": {
  "verified": 3, "verified_satisfied": 2,
  "attested": 1, "attested_satisfied": 1
}
```

### status 語義
- 預設（**非 strict**，向後相容）：`passed / incomplete / failed` 的計算維持「所有 CP 是否 satisfied」，不論 tier。既有測試不變。
- `strict: bool`（預設 `false`）：strict=true 時，plan 只有在「**每個 CP 都是 tier=verified 且 satisfied**」才回 `passed`；存在任何 attested CP 或任何未滿足 → `incomplete`。strict 讓 CI gate 能要求「全程 artifact-backed」。
- **strict 宣告點（P1–P4 覆審後調整）**：`strict` 應在 `qa_plan` 建立時宣告並**存進 plan**，而非只在 verify 時傳。理由與 P0 的反造假論點一致——契約要在行動**前**固定，不能在 verify 時被放寬。`verify_plan` 的 `strict` 參數**只能收緊不能放鬆**：plan 建立為 strict → verify 一律 strict；plan 非 strict → verify 可臨時 strict 檢視。

## 6. 護欄與非目標

護欄：
- verified CP **不讀** host 的 `evidence`——防造假的根本，需有測試明確鎖定。
- 產物路徑解析全程 lazy（call-time env），與既有 `_default_report_path` 一致，測試可 monkeypatch。
- 型別化 checker 放獨立純函式模組 `assertions.py`（無 I/O，吃已載入的 dict），I/O 留在 `qa_plan.py`——checker 100% 單元可測、本機無需 token。

非目標（本次不做）：
- 主動探測型斷言（`http_status` 實打 endpoint）——需 live 請求，留待後續。
- 改動 attested/substring 行為本身——只加標記，不改判定。
- plan 的 TTL / 持久化機制（v0.9.3 已有，不動）。

## 7. 成功標準

1. 紅隊測試：對 outcome=failed 的測試，`test_passed` 斷言回 `satisfied=false`；同一情境舊 `verification_hint` 路徑仍回 `true`（證明兩層並存且新層修正了破洞）。
2. host 對 verified CP 塞入偽造 `evidence` **無法**使其 `satisfied=true`。
3. 既有 71 個 qa_plan 測試全綠（零行為變更）。
4. dogfood：`examples/sample_vulnerable_api` 上，`finding_present`（vuln 端點）與 `finding_absent`（safe 端點）各自正確。
5. SKILL.md / README / walkthrough 更新，agent 有型別化斷言的使用範例。

## 8. 與 P1–P4 的交互（覆審後補記）

見 [架構硬化設計 §5](2026-07-07-architecture-hardening-p1-p4-design.md#5-排序相依與-p0-的交互)。要點：

- **P0 仍最先出貨**（風險最低、價值最高）。
- **P2（tool registry）**：P2 的 golden snapshot 必須**在 P0 合併之後**擷取，否則 P0 為 verify_plan 新增的 `assert`/`strict` schema 欄位會被誤判成重構 diff。P0 先改現有 inline schema，P2 再把它搬進 registry record。
- **P1（async offload）**：P0 的 `verify_plan` 會同步讀 report.json / scan-results.json；P1 落地後該 handler 標 `blocking=True` 一併 offload（小檔案、影響低，求一致）。
- **P3（scanner 家族）**：P0 Phase 2 讓 `run_scan` 落地 `scan-results.json`；P3 若把掃描包成 `ApiSecurityScanner`，該寫檔邏輯應移進 scanner class。兩者誰先落地，後者順手搬遷。
