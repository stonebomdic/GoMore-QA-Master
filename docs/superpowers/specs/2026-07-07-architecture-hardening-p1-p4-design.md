# 架構硬化設計 — P1–P4

> 目標版本：v0.9.7 ~ v0.10.x（分項獨立交付）
> 對應架構評估：P1 async 解阻塞 / P2 tool registry / P3 runner 抽象統一 / P4 optimizer 指標驗證
> 前置：與 [P0 verify_plan 真驗證](2026-07-07-verify-plan-artifact-backed-design.md) 有排序相依，見 §5。

---

## P1 — Async 解阻塞（blocking subprocess 不再卡 event loop）

### 問題
`call_tool` / `_dispatch`（`server.py:874-1040`）是 async，但 `run_tests`→`tools/runner.py`→runner→`safe_run`（`security.py:98`）是**同步 `subprocess.run`**，timeout 上限 600s。期間整個 stdio event loop 阻塞：不能併發服務、不能取消、不能回報進度。對比 `analyze_url` 是正統 async playwright（`_dispatch` 有 `await`）——所以問題只在 subprocess-based 路徑（`run_tests` / `run_failed` / `list_tests` / `generate_test` 的 codegen / `run_api_security_scan` / `analyze_screen` 的 maestro shell-out）。

### 設計
- 在 dispatch 邊界用 `asyncio.to_thread(fn, *args)` 把阻塞工作 offload 到 thread pool。引入單一 helper：
  ```python
  async def _offload(fn, /, *args, **kwargs):
      return await asyncio.to_thread(fn, *args, **kwargs)
  ```
- 套用對象（維持各 tool 回傳值不變）：`list_tests` / `run_tests` / `run_failed` / `get_report_summary` / `get_failure_details` / `get_history` / `generate_test` / `codegen` / `run_api_security_scan`，以及 `run_tests` 後的 `optimizer.write_plan()` side-effect。
- `analyze_screen` 內若有 `subprocess.run`（maestro），改用 `asyncio.to_thread` 包住該段（或整個函式 offload）。
- 執行緒安全審視：`telemetry._append` 為單行 JSONL append（POSIX 小寫入原子，仍加註），`qa_plan` 已有 `_CACHE_LOCK`，`config` 唯讀 → 併發安全無需額外鎖。

### 護欄 / 非目標
- **真正的取消不在本次範圍**：`to_thread` 的 thread 無法被 Python 強制中止；runaway 仍靠 `safe_run` 的 600s timeout 收尾。真取消需 `subprocess.Popen` + 收到 cancel 時 `terminate()`，churn 較大，列為後續。
- MCP progress notification（長跑串進度）列為**選配第二階段**——低階 `Server` API 要先確認 progress token 可用性。
- 不改任何 tool 的輸入/輸出契約。

### 成功標準
- 併發測試：以 fake runner（內含 `time.sleep`）並發兩個 dispatch 呼叫，斷言快的那個在慢的完成**之前**返回（證明 loop 未被阻塞）。
- 既有全套件綠、tool 回傳值零變更。

---

## P2 — Tool Registry（消 god-file 與四處 drift）

### 問題
`server.py` 1,114 行：`@app.list_tools()` 的 schema 是 17–827 行一整坨 literal、`_dispatch` 是 19 分支 if/else（888–1040）。「19 個 tool」這個事實同時散落在 schema、dispatch、`SKILL.md`、`test_smoke.py` 四處，天生 drift。另外 `serverInfo.version` 回報 `1.27.2` 與 `pyproject` 的 `0.9.5` 不一致（版本無 single source）。

### 設計
- 新增 `tools/registry.py`：每個 tool 一筆 record
  ```python
  @dataclass(frozen=True)
  class ToolSpec:
      name: str
      description: str
      input_schema: dict
      handler: Callable          # 既有的薄函式
      blocking: bool = False     # True → dispatch 走 _offload（接 P1）
  ```
  以 decorator 或明確 table 註冊；`REGISTRY: dict[str, ToolSpec]`。
- `list_tools()` 由 `REGISTRY` 生成 `Tool(...)` 清單；`call_tool()` 改成 `REGISTRY[name].handler` 的 dict dispatch（O(1)），取代 if/else 鏈。`blocking=True` 者由 dispatch 自動 `await _offload(handler, ...)`（**P2 吸收 P1 的 offload 樣式**）。
- schema literal 從 server.py 搬出（co-locate 於各 handler 模組或集中 `tools/schemas.py`）。
- **single source of truth**：
  - tool 清單 = `REGISTRY.keys()`；新增 drift 測試斷言 `set(REGISTRY) == SKILL.md 宣告的 tool 集`。
  - 版本：`Server(name, version=importlib.metadata.version("gomore-qa-master"))`，並加測試斷言 `pyproject 版本 == plugin.json 版本 == server 回報版本`，修掉 1.27.2 mismatch。

### 護欄 / 非目標
- **這是 refactor-under-test**：先寫 characterization/golden 測試鎖定「目前 list_tools 回傳的 name+required 欄位 snapshot」與「每條 dispatch 路徑行為」，**再**重構，保持 golden 綠。schema 對 client 必須語義不變。
- 不重新設計 tool API、不把 server 拆成大 package（避免 scope creep），只做 registry + 搬 schema + dict dispatch + 版本 single-source。

### 成功標準
- `list_tools` 輸出（names + schema 必填欄位）與重構前 golden snapshot 一致。
- drift 測試：REGISTRY / SKILL.md / 版本三處同步，任一漂移即紅。
- 新增一個 tool 只需在 registry 加一筆（不再改三處）。

---

## P3 — Runner 抽象統一（除反射式能力偵測 + 明確 scanner 家族）

### 問題
1. `generator.py:25` 用 `inspect.signature(runner.generate_test)` 嗅探是否支援 `url/module/business_context`——脆弱的反射式能力偵測。
2. `api_security` 是獨立 `run_scan` 函式、**不是** `TestRunner` 子類，卻同樣以 tool 暴露、還接了 plan——功能測試與安全掃描是兩套並行模型但沒講清楚。

### 設計
- **除反射**：在 `TestRunner` base 加明確能力宣告
  ```python
  class TestRunner(ABC):
      generation_context_fields: frozenset[str] = frozenset()  # 預設不吃 context
  ```
  `PytestPlaywrightRunner` 覆寫為 `{"url", "module", "business_context"}`。`generator.py` 改讀此屬性組 `extra`，不再 `inspect.signature`。
- **明確 scanner 家族**：新增 `scanners/__init__.py` 的 `SCANNERS` registry（與 `runners/REGISTRY` 平行），把 `run_scan` 包成 `ApiSecurityScanner` class，實作最小 `scan(spec_url, ...) -> dict` 介面。文件明確區分兩個家族（functional runners = pass/fail 套件；scanners = findings 清單），不強行合併成單一階層（它們語義本就不同）。

### 護欄 / 非目標
- 不合併 runner 與 scanner 的型別階層（刻意）。
- `generate_test` 對外 tool 契約不變；只換內部能力偵測機制。TDD：先鎖各 runner 的 `generate_test` 輸出 golden，重構後須逐字相同。
- 依賴 P2 的 registry 樣式較自然（scanner registry 比照 runner registry），故排序上 P3 在 P2 後。

### 成功標準
- `generator.py` 不再 import `inspect`；能力由屬性驅動，加測試覆蓋「非 pytest runner 不收 context kwargs」。
- `api_security` 走 `SCANNERS` registry 取得；兩家族在 README/SKILL.md 有明確區分。

---

## P4 — Optimizer 指標驗證（避免 vanity metric）

### 問題
`optimizer.py` 的 flake score（transition density）、生成採用率、「AI 策略」層是啟發式，且**未經驗證** agent 是否真的因 `optimization-plan.md` 改變行為——有淪為 vanity metric 的風險。

### 設計（以「先量測再投資」為原則，非重建）
- **conversion 量測**：用既有 `tool-usage.jsonl`（含 tool 序列）定義「recommendation → action 轉換率」——某條建議帶 `auto_action_hint`，其後 N 次呼叫內是否出現對應 tool。低轉換率的建議類型 → 降級或砍。
- **backtest 迴歸**：建一個 fixtures 語料（歷史 `report.json` + telemetry），replay 進 `build_plan`，斷言產出的優先序符合手標期望——把啟發式鎖進迴歸測試，防止未來靜默 drift。
- **每層精度檢查**：flake「被標 flaky 的 test 是否真的 outcome 交替」、adoption「被標採用的生成 test 是否真的進了下次 run」。無法對應到行動的指標，從 top-line plan 降到附錄。

### 護欄 / 非目標
- 不做 ML、不重建 optimizer。只加量測 + backtest + 剪枝。
- 最低優先序；可在 P0–P3 落地並累積真實 telemetry 後再做（否則 backtest 語料不足）。

### 成功標準
- 有 backtest 測試鎖定 build_plan 對固定語料的輸出。
- 有一份 conversion 報告能回答「哪些建議 agent 真的採納」；至少砍/降級一類無效指標。

---

## 5. 排序、相依、與 P0 的交互

### P1–P4 之間
- **P1 先行**（小、低風險、立即見效：dispatch 邊界包 `to_thread`）。
- **P2 接手並吸收 P1**：registry 的 `blocking` 旗標讓 offload 從「手動包」變「宣告式」。故 P1 可先獨立出貨，P2 再把 offload 收進 registry。
- **P3 在 P2 之後**（scanner registry 比照 runner registry 較自然；除反射那半可獨立先做）。
- **P4 最後**（需先累積真實 telemetry 才有 backtest 語料）。

### 與 P0 的交互（**P0 仍應最先出貨**，見其計畫的可交付邊界）
- **P0 × P2（schema 位置）**：P0 會改 `server.py` 的 list_tools inline schema + 為 verify_plan 加 `assert`/`strict` 欄位。P2 之後這些搬進 registry record。**排序要點：P2 的 golden snapshot 必須在 P0 之後擷取**，否則 P0 的加法欄位會被誤判成 refactor diff。
- **P0 × P1（產物 I/O）**：P0 的 verify_plan 會做同步檔案讀（report.json / scan-results.json）。P1 落地後，verify_plan handler 應標 `blocking=True` 一併 offload（小檔案、影響低，但求一致）。
- **P0 × P3（scan 產物歸屬）**：P0 Phase 2 讓 `run_scan` 落地 `scan-results.json`。P3 把掃描包成 `ApiSecurityScanner` 後，該寫檔邏輯應移進 scanner class，而非留在 `run_scan` 裸函式。若 P3 先於 P0 Phase 2，直接寫在 class；否則 P3 順手搬遷。
- **P0 × P4（訊號品質）**：P0 的 verified CP 產生 artifact-backed 的 pass/fail，是比 substring 更乾淨的訊號源，P4 的 optimizer 可優先消費（機會、非相依）。
