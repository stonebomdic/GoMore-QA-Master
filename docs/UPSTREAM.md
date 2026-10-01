# 上游追蹤（Upstream Tracking）

本文件記錄 GoMore QA Master（本 fork）與上游開源專案
[`kao273183/mk-qa-master`](https://github.com/kao273183/mk-qa-master)
（remote 名稱 `upstream-public`）之間的分岔狀態、逐項採用決策、追蹤政策
與授權風險提醒。目的是讓「要不要跟上游」成為每季一次的**明確決策**，
而不是靠記憶或臨時起意。

## 分岔現況

| 項目 | 數值 |
|---|---|
| 分岔點 commit | `14c8a117de65e425f4cc4f75b3e8f0088143d85f` |
| 分岔點日期 | 2026-05-27 |
| 分岔點取得方式 | `git merge-base main upstream-public/main` |
| Fork（`main`）領先的 commit 數 | 86（`git rev-list --count 14c8a11..main`） |
| Upstream（`upstream-public/main`）領先的 commit 數 | 26（`git rev-list --count 14c8a11..upstream-public/main`） |
| Upstream 最後活動日 | 2026-06-04（`v1.4.0`，之後無新 commit） |

Fork 分岔後走的是完全獨立的路線：rebrand（`gomore_qa_master`）、移除
CAPTCHA / visual_challenge 解題模組、BOLA 偵測改為 A′ 4-probe fingerprint
比對、新增 `analyze_url` 的 auth_storage 注入、table/implicit form 偵測、
可見性骨架斷言、lint/CI 加固、dogfood CI 等——這些都是 upstream 這 26
個 commit 完全沒有涵蓋的方向。

## Upstream 26 個 commit 逐項決策

決策欄位三種值：
- **採用**：已經、或預計在近期 cherry-pick 進 fork。
- **候選**：有潛在價值，但尚未排入工作，需要先經評估/拆分。
- **不採用**：與 fork 方向不符、已被 fork 自身方案取代，或功能已被移除。

| Commit | 版本 / 子序 | 主題 | 決策 | 理由 |
|---|---|---|---|---|
| `2fdbd14` | v0.10.0 PR-1 (#72) | `run_tests(plan_id=…)` universal bookend 1/4 | 候選 | `plan_id` 貫穿 MCP tool 的設計對追蹤跨 tool 呼叫鏈有潛在價值，但需評估與我們既有 `verify_plan` / `scan-results.json` 產物契約的相容性，不可直接套用 |
| `79581e4` | v0.10.0 PR-2 (#73) | `solve_visual_challenge(plan_id=…)` universal bookend 2/4 | 不採用 | CAPTCHA / visual_challenge 解題模組已從 fork 整個移除（21→19 tools），此工具本體在 fork 中不存在，無從套用 |
| `32f568a` | v0.10.0 PR-3 (#74) | `analyze_url(plan_id=…)` universal bookend 3/4 | 候選 | 與 `run_tests` 同組評估，若採用 `plan_id` 設計需一併處理 |
| `09078be` | v0.10.0 PR-4 (#75) | `auto_generate_tests(plan_id=…)` universal bookend 4/4 | 候選 | 同上 |
| `107c2ff` | v0.10.0 release (#76) | universal bookend 版本收尾 PR-5/5 | 候選 | 本身僅為版本收尾 commit，隨上面三項一併評估，不單獨處理 |
| `e7333bd` | v1.0 PR-1 (#77) | schema snapshot test + tool-surface freeze 1/4 | 不採用 | 我們已有 `tests/fixtures/tool_surface_golden.json` 做對等的 tool-surface 守門，重複建置無益 |
| `b6289ba` | v1.0 PR-2 (#78) | tool-count sync + soft version-pin tests 2/4 | 不採用 | 同上，golden snapshot 機制已涵蓋同等目的 |
| `6198fe2` | v1.0 PR-3 (#79) | `MIGRATION-0.x-to-1.0` + `DEPRECATION-POLICY` + ack gating 3/4 | 不採用 | 我們的版號與工具面自 0.9.x 分岔後已與 upstream 1.x 不同源，遷移文件/棄用政策沒有對應的遷移標的 |
| `d9b3c59` | v1.0.0 release (#80) | stability lock 版本收尾 4/4 | 不採用 | 同上，是 v1.0 PR-1~3 的收尾 commit |
| `abf3261` | v1.1.0 PR-1 (#81) | `EdgeConfig` + `edge/` 模組樹 1/4 | 不採用 | Edge AI runner 整條線與我們的 runner 矩陣（pytest / jest / cypress / go / maestro / schemathesis / newman）無關 |
| `056effc` | v1.1.0 PR-2 (#82) | `EdgeInferenceRunner` + REGISTRY wiring 2/4 | 不採用 | 同上，Edge AI 專屬 |
| `4af9f7e` | v1.1.0 PR-3 (#83) | `analyze_stream` MCP tool + edge template 3/4 | 不採用 | 同上 |
| `7854e45` | v1.1.0 release (#84) | Edge AI Runner Phase 1+2 收尾 4/4 | 不採用 | 同上 |
| `f069e96` | v1.1.1 (#85) | housekeeping：sample fixture + CI job + Edge 知識章節 | 不採用 | 修補對象是 Edge AI 專屬項目的收尾 |
| `16a1fc2` | v1.1.2 (#86) | README Edge AI 章節補充（postmortem §9） | 不採用 | 同上 |
| `f91ee24` | v1.2.0 PR-1 (#87) | CI ack-check workflow + PR templates + response-shape lock 1/4 | 不採用 | 綁定 Edge AI release 流程的治理機制，與我們的 CI 無關 |
| `a5f52e0` | v1.2.0 PR-2 (#88) | `RemoteHTTP.infer()` 真實實作 2/4 | 不採用 | Edge AI 專屬 |
| `a601fa5` | v1.2.0 PR-3 (#89) | `_healthcheck_device` 真實 GET /health 探測 + `QA_INFERENCE_TIMEOUT_S` 3/4 | 不採用 | 同上 |
| `fcc806d` | v1.2.0 release (#90) | `init_qa_knowledge` runner-aware + Phase 3 收尾 4/4 | 候選 | `init_qa_knowledge` runner-aware 化若拆出 Edge AI 無關的部分，對我們現有的 `init_qa_knowledge` 可能有參考價值；需先拆分評估，不能整包採用 |
| `9a67438` | v1.2.1 (#91) | 文件宣告：v2.0.0 起由 MIT → Apache 2.0 relicense | 不採用（純文件公告，列入授權追蹤） | 本身不涉及程式碼變更，但其宣告的授權條款變更對我們未來是否能繼續 cherry-pick 有直接影響，見下方「授權註記」 |
| `535f91e` | v1.3.0 PR-1 (#92) | `mk_qa_master.edge.resilience` 模組 1/4 | 不採用 | Edge AI 專屬 |
| `4bf4121` | v1.3.0 PR-2 (#93) | `get_optimization_plan` Edge flake signals 2/4 | 不採用 | 同上 |
| `7a9cd4e` | v1.3.0 PR-3 (#94) | `generate_test(resilience_mode='netem')` 3/4 | 不採用 | 同上 |
| `e937cdb` | v1.3.0 release (#95) | Edge AI Phase 4 resilience + flake signals 收尾 4/4 | 不採用 | 同上 |
| `2992740` | v1.3.1 (#96) | edge runner 覆寫 `get_all_test_details`，修 HTML report 卡片渲染 | 不採用 | Edge AI 專屬 bugfix，我們沒有對應的 edge runner |
| `4fd6508` | v1.4.0 (#97) | `doctor` 子命令：install + env audit | 候選 | 對應我們實際的安裝上手痛點（Python ≥ 3.10 要求、`uv` editable `.pth` 檔案問題、MCP server 的 `PATH` 解析、`NODE_OPTIONS` 環境變數影響），值得評估是否移植其診斷邏輯 |

### 小結

- **CAPTCHA / visual_challenge 相關（`79581e4`）**：全部不採用，模組已從
  fork 移除，沒有掛載點。
- **Edge AI Runner 全系列（v1.1.0–v1.3.1，共 13 個 commit）**：全部
  不採用，與 fork 的 runner 矩陣無關，長期也不計畫支援。
- **v1.0 stability lock（schema snapshot / tool-count sync /
  MIGRATION / DEPRECATION-POLICY，共 4 個 commit）**：不採用，因為
  fork 已有自己的 `tests/fixtures/tool_surface_golden.json` 達成同等
  的 tool-surface 守門效果，且版號系統已與 upstream 1.x 脫鉤，遷移/
  棄用文件沒有對應標的。
- **`plan_id` universal bookend（v0.10.0，4 個核心 MCP tool + 1 個
  收尾，共 5 個 commit）**：列為候選，其中 `solve_visual_challenge`
  那支因 CAPTCHA 模組已移除而排除，其餘 3 支工具（`run_tests` /
  `analyze_url` / `auto_generate_tests`）若要採用需整體評估。
- **`doctor` 子命令（v1.4.0）**：列為候選，與我們當前安裝文件
  （`docs/` 內多份疑難排解說明）要解決的問題高度重疊。

## 追蹤政策

1. **不自動跟版**：本 fork 不會自動合併或 rebase 到 upstream 的任何
   tag 或分支，upstream 的版號演進與我們無關。
2. **每季檢視一次**：
   ```bash
   git fetch upstream-public
   git log --oneline upstream-public/main --not main
   ```
   檢視是否有新 commit，若有，比照本文件的表格格式逐項補列決策。
3. **Cherry-pick 前置條件**：任何從 upstream 挑選進 fork 的 commit，
   合併前必須：
   - 經過 Opus 等級的覆審（不可僅靠自動測試通過就合併）；
   - 跑過全套測試（`pytest tests/ tests_project/` + 相關 dogfood /
     lint CI job）；
   - 在本文件對應列補上「已採用」與落地的 commit/PR 連結。
4. **授權註記（重要）**：upstream 已在 `9a67438`（v1.2.1）公開宣告
   **自 v2.0.0 起授權條款由 MIT 改為 Apache 2.0**；v1.4.0（含）以前
   仍是 MIT。本 fork 目前的程式碼基礎（分岔點 `14c8a11`）與後續所有
   26 個 upstream commit 都在 MIT 授權範圍內，沿用沒有問題。但
   **任何 v2.0.0 之後（若 upstream 真的推出）的 cherry-pick，必須先
   送內部法遵審查授權相容性**，不可逕行合併 Apache 2.0 授權下的程式碼
   到本 MIT 專案。
5. **CAPTCHA 相關**：upstream 若在其 CAPTCHA/visual_challenge 路線上
   有後續更新，一律不追蹤——該模組是 fork rebrand 時有意移除的功能，
   不在追蹤範圍內。

## 版號策略（建議，待決）

目前 fork 沒有自己的 git tag（`git tag` 列出的所有 `v0.1.0`–`v1.4.0`
均為 upstream 歷史遺留，不是 fork 自己打的）。建議：

> fork 往後的 release 改用 **`gomore-v<semver>`** 作為 tag 前綴
> （例如 `gomore-v0.9.11`），與 upstream 既有的裸 `v1.x` tag 系列
> 明確區隔，避免兩邊 tag namespace 衝突、也避免有人誤以為兩邊版號有
> 對應關係。

此為建議，**尚未拍板**，需要維護者確認後才正式採用並回填歷史版本的
tag（見本次 PR 的回報內容，列出建議的 4 個 tag 對應 `main` 上的
merge commit SHA）。
