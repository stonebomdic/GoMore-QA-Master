# 架構硬化 Implementation Plan — P1–P4

> 設計：[`specs/2026-07-07-architecture-hardening-p1-p4-design.md`](../specs/2026-07-07-architecture-hardening-p1-p4-design.md)
> 原則：TDD、refactor-under-test（先鎖 golden 再重構）、每項獨立可出貨。
> 排序：**P1 → P2（吸收 P1）→ P3 → P4**；且**全部讓位給 P0 先出貨**（見 §與 P0 的交互）。

## 檔案結構

| 檔案 | 動作 | 屬 |
|---|---|---|
| `src/gomore_qa_master/server.py` | 修改 — dispatch 加 `_offload` | P1 |
| `tests/test_async_dispatch.py` | 新增 — 併發不阻塞測試 | P1 |
| `src/gomore_qa_master/tools/registry.py` | 新增 — `ToolSpec` + `REGISTRY` | P2 |
| `src/gomore_qa_master/tools/schemas.py` | 新增 — 搬出的 input_schema | P2 |
| `src/gomore_qa_master/server.py` | 修改 — list_tools/call_tool 改吃 registry | P2 |
| `tests/test_tool_registry.py` · `tests/test_smoke.py` | 新增/修改 — golden snapshot + drift + 版本 single-source | P2 |
| `src/gomore_qa_master/runners/base.py` · `.../pytest_playwright.py` · `tools/generator.py` | 修改 — 能力屬性、除 `inspect` | P3 |
| `src/gomore_qa_master/scanners/__init__.py` · `scanners/api_security.py` | 新增 — `SCANNERS` + `ApiSecurityScanner` | P3 |
| `src/gomore_qa_master/tools/optimizer.py` · `tests/test_optimizer_backtest.py` | 修改/新增 — backtest + conversion | P4 |

---

## Phase P1 — Async 解阻塞

### Task 1: dispatch 邊界 offload
- [ ] **Step 1: 寫失敗測試** `tests/test_async_dispatch.py`
  - monkeypatch 一個 fake runner，其 `run_tests` 內 `time.sleep(0.3)` 回固定 dict。
  - `asyncio.gather` 併發：一個 `run_tests`（慢）+ 一個 `get_runner_info`（快）；斷言快的 `done` 時間戳 < 慢的完成時間戳（證明未被阻塞）。
  - 斷言 `run_tests` 回傳值與同步呼叫逐字相同（offload 不改結果）。
- [ ] **Step 2: 跑測試確認失敗**（現況同步 → 快的被卡，時序斷言紅）
- [ ] **Step 3: 最小實作** — server.py 加 `async def _offload(fn,*a,**k): return await asyncio.to_thread(fn,*a,**k)`；把 `_dispatch` 內 `run_tests/run_failed/list_tests/get_report_summary/get_failure_details/get_history/generate_test/codegen/run_api_security_scan` 與 `optimizer.write_plan` 改為 `await _offload(...)`；`analyze_screen` 的 maestro 段包 `to_thread`。
- [ ] **Step 4: 跑測試確認通過** — `pytest tests/` 全綠。
- [ ] **Step 5: Commit** — `perf(server): dispatch 阻塞工作 offload 到 thread（不再卡 event loop）`

> 選配後續（不在本 Task）：真取消（Popen+terminate）、MCP progress notification。

---

## Phase P2 — Tool Registry

> ⚠️ 前置：**在 P0 合併之後**再擷取 golden，避免 P0 的 `assert`/`strict` 加法被誤判為 diff。

### Task 2: 擷取重構前 golden（characterization）
- [ ] **Step 1: 寫測試** `tests/test_tool_registry.py`
  - snapshot 目前 `list_tools()` 的 `{name → required 欄位集}`（存成測試內常數或 fixture）。
  - 對每個 tool 名跑一次 `call_tool`（用最小合法 args 或 monkeypatched 依賴），斷言不 raise、回 `list[TextContent]`。
- [ ] **Step 2: 跑測試確認通過**（這是 baseline，重構前必綠）
- [ ] **Step 3: Commit** — `test(server): 重構前 tool surface golden snapshot`

### Task 3: 導入 registry + dict dispatch + 版本 single-source
- [ ] **Step 1: 寫失敗測試**（擴 `test_tool_registry.py` + `test_smoke.py`）
  - `set(REGISTRY) == SKILL.md 宣告 tool 集`（drift 測試）。
  - `set(REGISTRY)` == Task 2 golden 的 name 集（不多不少）。
  - 版本：`importlib.metadata.version("gomore-qa-master") == plugin.json 版本 == .codex-plugin 版本`。
  - `blocking=True` 的 tool 經 dispatch 確實走 offload（沿用 P1 fake-runner 時序法）。
- [ ] **Step 2: 跑測試確認失敗**（registry 未存在）
- [ ] **Step 3: 最小實作**
  - `tools/registry.py`：`ToolSpec` dataclass + `REGISTRY`（每個既有 tool 一筆，`handler` 指既有薄函式，`blocking` 標好）。
  - `tools/schemas.py`：把 17–827 行的 input_schema 逐一搬入（**內容逐字保留**）。
  - server.py：`list_tools` 由 REGISTRY 生成；`call_tool` 改 `spec = REGISTRY[name]; await _offload(spec.handler,...) if spec.blocking else spec.handler(...)`；刪 if/else 鏈。
  - `Server("gomore-qa-master", version=importlib.metadata.version(...))`。
- [ ] **Step 4: 跑測試確認通過** — **Task 2 golden 必須逐字綠**（證明對 client 語義不變）；全套件綠。
- [ ] **Step 5: Commit** — `refactor(server): tool registry + dict dispatch + 版本 single-source`

---

## Phase P3 — Runner 抽象統一

### Task 4: 除反射式能力偵測
- [ ] **Step 1: 寫失敗測試**（`tests/test_generator_capability.py` 新增）
  - 各 runner 的 `generate_test` 輸出 golden（pytest 帶 context / jest 不帶）。
  - 斷言對非 pytest runner 傳 `url/module` **不會**被轉發（無 TypeError、輸出不含 context 痕跡）。
  - 斷言 `generator` 模組不再需要 `inspect`（可用 `"inspect" not in generator 原始碼` 或行為測試）。
- [ ] **Step 2: 跑測試確認失敗**
- [ ] **Step 3: 最小實作** — `base.py` 加 `generation_context_fields: frozenset = frozenset()`；`pytest_playwright` 覆寫 `{"url","module","business_context"}`；`generator.py` 改讀屬性組 `extra`，移除 `import inspect`。
- [ ] **Step 4: 跑測試確認通過**（含各 runner golden 逐字綠）
- [ ] **Step 5: Commit** — `refactor(runner): 能力以屬性宣告，除 generator 反射偵測`

### Task 5: scanner 家族形式化
- [ ] **Step 1: 寫失敗測試**（`tests/test_scanner_registry.py`）
  - `SCANNERS["api_security"]` 可取得，`ApiSecurityScanner().scan(...)` 輸出與舊 `run_scan(...)` 逐字相同（golden）。
  - consent/authorized-domains 閘門行為不變（沿用既有測試路徑）。
- [ ] **Step 2: 跑測試確認失敗**
- [ ] **Step 3: 最小實作** — `scanners/__init__.py`（`SCANNERS` registry）；`scanners/api_security.py` 的 `ApiSecurityScanner` 包住 `run_scan`（薄封裝，邏輯不動）；server 的 `run_api_security_scan` handler 改經 `SCANNERS` 取得。
- [ ] **Step 4: 跑測試確認通過** — 含既有 `test_runner_api_security*.py` 與 dogfood 全綠。
- [ ] **Step 5: Commit** — `refactor(scan): SCANNERS registry + ApiSecurityScanner 封裝`

---

## Phase P4 — Optimizer 指標驗證

### Task 6: backtest 迴歸
- [ ] **Step 1: 寫測試** `tests/test_optimizer_backtest.py`
  - 造 fixtures：一組固定的 `report.json` history + telemetry JSONL（含明顯 flaky / broken / 高頻 tool / 生成未採用）。
  - replay 進 `build_plan`，斷言優先序與 evidence 符合手標期望（例：交替 outcome 的 test 進 flake 高優先）。
- [ ] **Step 2: 跑測試確認通過/調整** — 若現況啟發式與期望不符，記錄差異（可能揭露既有 bug；依判斷修或標 TODO）。
- [ ] **Step 3: Commit** — `test(optimizer): build_plan backtest 迴歸鎖定`

### Task 7: conversion 量測 + 剪枝
- [ ] **Step 1: 寫失敗測試** — 給定含 `auto_action_hint` 的建議 + 後續 tool 序列，`_recommendation_conversion(telemetry)` 算出轉換率。
- [ ] **Step 2: 跑測試確認失敗**
- [ ] **Step 3: 最小實作** — optimizer 加 conversion 計算；把轉換率為 0 / 無 `auto_action_hint` 且無法對應行動的建議類型，從 top-line 降到附錄。
- [ ] **Step 4: 跑測試確認通過**
- [ ] **Step 5: Commit** — `feat(optimizer): recommendation→action 轉換率 + 無效指標降級`

---

## 驗收（對應設計各項成功標準）
- P1：併發不阻塞測試綠；tool 回傳零變更。
- P2：golden snapshot 逐字綠；drift + 版本 single-source 測試綠；新增 tool 只改一處。
- P3：`generator` 不再依賴 `inspect`；scanner 走 registry；各 golden 綠。
- P4：backtest 鎖定；至少降級一類無效指標。

## 與 P0 的交互（務必遵守的排序）
1. **P0 先出貨**（最高優先、風險最低價值最高）。
2. **P2 的 golden（Task 2）在 P0 合併之後才擷取**——否則 P0 的 `assert`/`strict` 加法欄位會污染 snapshot。
3. **P1 之後**，P0 的 `verify_plan` handler 在 registry 標 `blocking=True`（產物檔案讀一併 offload）。
4. **P3 Task 5** 若晚於 P0 Phase 2，順手把 `scan-results.json` 落地邏輯從裸 `run_scan` 搬進 `ApiSecurityScanner`。
