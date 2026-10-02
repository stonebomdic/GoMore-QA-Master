# gomore-qa-master 導入評估 — POC 總結報告 / 採用建議書

> 對象：SQA 團隊（公司內部 API / Web / Mobile 驗證）
> 評估標的：`gomore-qa-master` v0.9.5（MCP server + agent skill，整合 7 種測試框架 + OWASP 掃描 + 行動/視覺挑戰）
> 評估期間：2026-05-29 ~ 2026-06-01；**加固後重評：2026-10-02（內部 fork v0.9.11，見 §10）**
> 受測環境：GWP Backend QA（`gwp-backend-qa.gomore.net`）、GWP Admin QA（`gwp-admin-qa.gomore.net`）、Megoluki QA Android app
> 詳細逐步紀錄：見 [`sqa-poc-plan.zh-TW.md`](sqa-poc-plan.zh-TW.md) 附錄 A–E；bug 清單見 [`qa-bug-tickets-gwp-backend-qa.md`](qa-bug-tickets-gwp-backend-qa.md)

---

## 1. 結論先行（TL;DR）

| 領域 | 建議 | 一句話理由 |
| --- | --- | --- |
| **API 測試（有 OpenAPI）** | ✅ **採用** | schemathesis 純唯讀 fuzz 47 秒抓到 7 個真實 500（含 1 個 SQL 錯誤外洩），零設定即戰力 |
| **API 安全掃描** | ⚠️ **有條件採用** | headers/認證/授權判斷正確；但 **BOLA 規則假陽性高，findings 須人工覆核** |
| **Web（SPA）** | ⚠️ **僅當輔助** → 10/02 重評後建議升為 **有條件採用** | 原：測項自動生成弱、`auth_cookie` 對 localStorage SPA 無效。**加固後**：`auth_storage` 真登入、table/隱藏元素偵測、產測帶真斷言，gwp-admin 實測 9/9 可直接執行（§10） |
| **Mobile（Flutter app）** | ❌ **不採用** | Megoluki 是 Flutter，`analyze_screen` 讀不到 canvas UI；行動環境前置成本也高 |

**總體建議：以「API 測試工具」名義導入，fork 內部化、鎖版本、停用 CAPTCHA 模組；Web 當 API 探勘輔助；Mobile 此版本不導入。**

---

## 2. 評估方法

- 採分階段 POC（Phase 0–4），每階段有明確 Go/No-Go。
- 對真實 QA 系統實跑，**唯讀優先**（API 只跑 GET、安全掃描不開 mutating 的 mass_assignment）。
- 所有「真陽性」均以獨立手動請求覆核；「假陽性」以回應差異比對驗證。
- token 僅以環境變數傳入，不寫入專案檔 / git / log。

---

## 3. 四階段結果摘要

### Phase 0–1：安裝與自我驗證 ✅
- `pip install -e '.[api]'` + `pytest tests/` → **283 passed**。
- 對內建 vulnerable fixture 掃描 → 命中全部 ground-truth 漏洞（BOLA/認證/授權）。
- 環境坑：需 Python ≥3.10（系統 3.9、Homebrew 3.12 均不可用，改用 3.11）；uv venv 不認 editable `.pth`，手動執行需 `PYTHONPATH=src`。

### Phase 2：內部 API（GWP Backend QA）✅ 高價值
- spec 規模 265 paths / 336 ops / 412 schemas。
- **schemathesis GET-only fuzz（1707 cases / 47s）抓到：**
  - 🔴 **7 個端點對畸形參數回 500**（輸入驗證缺失），其中 `/activities/vbt-physical/{id}/records` **外洩 raw MySQL 語法錯誤**（P0：資訊洩漏 + 注入面）。
  - 🟠 **9 個端點回應與 OpenAPI schema 不符**（contract drift）。
- 偵察階段另發現 **3 個 dangling `$ref` schema**（spec 缺陷）。
- **OWASP 掃描：BOLA 報 6 筆 CRITICAL → 經人工驗證全為假陽性**（兩使用者取得相同共享內容、同 md5）；headers 合理低值；認證/授權 0 筆（分層正確）。
- → 已整理成 **5 張 Jira-ready bug 單**。

### Phase 3：內部 Web（GWP Admin，Nuxt SPA）⚠️
- 認證為 **localStorage + Bearer**（非 cookie）→ **`analyze_url` 的 `auth_cookie` 無效**（須手動注入 localStorage 繞過）。
- analyzer 偵測品質：
  - ⭐⭐⭐⭐ **API 相依探勘**：自動抓出每頁實際呼叫的 13 個 `/admin/*` API。
  - ⭐⭐ **模組/測項**：抓不到表單欄位（fields=None）、資料表/卡片（sections=0），輸出被全域登出對話框洗版，TC 通用。
- 跨階段旁證：admin token 下 `/admin/*` 回 200、一般使用者回 401 → 授權分層正確。

### Phase 4：Mobile（Megoluki QA Android）❌
- 在 Apple Silicon Mac 上**從零修好 Android arm64 環境**（踩 4 個坑：Java21 vs 舊 sdkmanager、x86_64 image vs arm64 host、x86_64 emulator binary、無 arm64 image）。
- App 安裝啟動成功，但 **Megoluki 是 Flutter app** → `maestro hierarchy` 讀不到 canvas UI：
  - 歡迎頁有「點擊開始旅程！」CTA，hierarchy 只看到 Android 系統列。
  - `analyze_screen` 偵測到的全是系統 chrome，**零 app 真實元件**。
- iOS 未測（本機無完整 Xcode），同為 Flutter 預期相同問題。

---

## 4. Rubric 評分（1–5，3 為可接受門檻）

「POC 分數」為 2026-06 對上游 v0.9.5 的原始評分（歷史紀錄，不改寫）；「10/02 重評」為內部 fork v0.9.11 加固後的重評，只重評有新實證的面向（依據見 §10），其餘維持。

| 評估面向 | POC 分數 | 10/02 重評 | 依據 |
| --- | --- | --- | --- |
| 安裝 / 上手難度 | 3 | — | 核心可裝，但 Python 版本 / editable `.pth` 有摩擦（fork 已加 `pythonpath=["src"]`、`sys.executable -m pytest`，但未重測新人安裝） |
| 內部 API 功能驗證 | **5** | — | schemathesis 即抓真 bug，零設定 |
| API 安全掃描可信度 | 2 | **—（待 W2 重驗）** | 原：BOLA 假陽性高。fork 已改 A′ 四探測指紋判定＋`bola_shared_endpoints`，但只在 dogfood fixture 驗過；對 GWP Backend 重掃前不改分 |
| Web 測項自動生成 | 2 | **4** | 原：表單/資料表偵測失效。現：native/aria/重複結構三層 table 偵測、`<form>` 外欄位聚合（implicit form）、隱藏元素標 `visible`；gwp-admin `/users` 產出 9 筆、9/9 可直接執行（原 2/8） |
| Web auth 支援 | 2 | **4** | 原：只支援 cookie。現：`auth_storage`（localStorage，`$ENV` 間接引用）＋ `auto_generate_tests` 自動產出 auth conftest 鷹架（token 不落檔）；真登入端到端驗證 |
| Mobile 可用度（Flutter） | 1 | — | analyze_screen 對 Flutter canvas 失效 + 環境成本高（不在加固範圍） |
| 產測（generator）品質 | 2 | **4** | 原：fill 按鈕、無真實斷言。現：按鈕過濾、`expect_response` API 斷言、table 列數/表頭真斷言、selector 唯一化、隱藏元素存在性骨架；跨模組斷言刻意不活化以防假紅。未達 5：仍有 TODO 需人工補業務斷言 |
| 測試執行（runner）| **4** | — | 端到端產出 report.json/junit/history/optimization-plan，捕捉失敗 + screenshot/trace/video |
| 報告可讀性（reporter）| **5** | — | 99KB 自包含 HTML、base64 內嵌截圖、Pass/Fail + 趨勢，產品級可直接交付 |
| 安全護欄 | 4 | — | 路徑穿越 / 參數注入 / timeout / redaction / consent gate 完整 |
| CI 整合成本 | 3 | — | 專案附 CI 範例；多 runner 需多份設定 |
| 授權 / 合規 | 3 | — | MIT；CAPTCHA solver 已自 fork 移除。注意：上游預告 v2.0.0 起改 Apache 2.0（見 `UPSTREAM.md`） |
| 長期維護風險 | 2 | **—（待 W3）** | Beta、單一作者（上游 2026-06-04 後無活動）→ fork 內部化已執行；tag/CHANGELOG/上游追蹤政策/每週 fresh-install 健檢落地後再評 |

---

## 5. 模組最終建議（保留 / 替換 / 停用）

| 模組 | 決策 | 依據（POC 實證） |
| --- | --- | --- |
| `runners/schemathesis`（API fuzz） | ✅ **採用為主力** | Phase 2 抓到 7 真 bug |
| `runners/newman`（Postman） | ✅ 備援 | 手刻 API 過渡用 |
| `security_rules/headers·broken_auth·function_authz` | ✅ 採用 | 無假陽性風暴、判斷正確 |
| `security_rules/bola` | ⚠️ **採用但須人工覆核** | Phase 2 全假陽性 |
| `tools/analyzer.analyze_url` | ⚠️ 僅當 API 探勘輔助 → **10/02：可採用**（API 探勘＋模組/測項偵測） | Phase 3：測項弱、auth_cookie 限制 → 加固後 `auth_storage`、table/implicit form/可見性偵測（§10） |
| `tools/analyzer.analyze_screen` | ❌ **不採用（Flutter）** | Phase 4：讀不到 Flutter UI |
| `runners/pytest_playwright` | ✅ **採用** | 附錄 F：端到端產出 report/junit/history/artifacts，捕捉失敗正確 |
| `reporters/html` | ✅ **採用** | 附錄 F：99KB 自包含 HTML、內嵌截圖、產品級可交付 |
| `tools/generator`（產測） | ⚠️ 僅產骨架 → **10/02：可採用為起點**（可執行＋基礎斷言，業務斷言仍需人工） | 附錄 F：會 fill 按鈕、無真實斷言 → 加固後 gwp-admin 9/9 可執行、table/API 真斷言（§10） |
| `config` / `security`（護欄） | ✅ 保留 | 進公司環境前提 |
| `tools/visual_challenge*`（CAPTCHA solver） | ⛔ **停用** | 合規 + 維護風險 |
| `runners/jest·cypress·go` | ⛔ 不啟用 | 非現有技術棧 |

---

## 6. Go / No-Go 決策

### ✅ GO — API 測試（限定範圍）
以 **schemathesis（功能/穩定性 fuzz）** 為核心導入，對所有有 OpenAPI 的內部 API 納入 CI。**這是本次 POC 證明最有價值、可立即見效的部分。**

### ⚠️ CONDITIONAL GO — API 安全掃描
可導入 `headers + broken_auth + function_authz`；**BOLA 規則的 findings 一律須人工覆核**（或 fork 後強化「回應差異比對 + 真實物件 id fixture」）。不對 QA 開 `mass_assignment`。

### ⚠️ LIMITED — Web
analyzer 僅作為「**列出頁面呼叫哪些 API**」的探勘輔助；**不依賴它自動產測**。auth 需手動處理（localStorage 注入）。E2E 測試案例仍需人工 + 業務知識撰寫。

> **10/02 重評**：上述限制已於 fork 加固中解除（§10）。建議改為 **CONDITIONAL GO**：`auth_storage` 真登入後以 `auto_generate_tests` 產出「可執行＋基礎斷言」的起點，業務斷言由 SQA 補完；仍不視為取代人工案例設計。

### ❌ NO-GO — Mobile（此版本）
主力 app 為 Flutter，`analyze_screen` 無法兌現自動化加值。若未來要做行動自動化，建議**直接評估 Maestro / Appium 等底層工具 + RD 開 Flutter semantics**，而非透過 gomore-qa-master 這層。

---

## 7. 導入前置條件（若 GO）

1. **Fork 內部化**：建內部 fork、鎖版本、自行補關鍵流程單元測試。
2. **停用 CAPTCHA 模組**：`QA_VISUAL_CHALLENGE_CONSENT` 保持關閉，不納入流程。
3. **Secret 管理**：token 接公司既有機制（CI secrets / Vault），勿明文。
4. **CI 接線**：API runner 接進 pipeline，schemathesis contract test 設為回歸守門。
5. **多 runner 限制**：一個 server 綁一種 runner，API/web 需分別設定 entry（見 plan 附錄）。
6. **授權審查**：MIT 授權過內部法遵；確認對外服務（如安全掃描目標）皆為授權網域。

---

## 8. 風險與緩解

| 風險 | 緩解 |
| --- | --- |
| BOLA 假陽性誤報後端 | 一律人工覆核；fork 強化規則 |
| `verify_plan` 僅子字串比對、易假陽性 | 不作為品質閘門唯一依據 |
| runner 封裝層幾無單元測試 | 內部 fork 補測關鍵流程 |
| Beta + 單一作者維護 | 鎖版本、內部化、評估自維成本 |
| analyzer 對現代 SPA / Flutter 失效 | Web 僅當輔助、Mobile 不導入 |
| 行動環境前置成本高 | 若做行動，編列 macOS arm64 runner + 環境腳本成本 |

---

## 9. 一句話總結

> **gomore-qa-master 對「有 OpenAPI 的後端 API」是即戰力（schemathesis 真的抓得到 bug）；但對「現代 SPA / Flutter app」的自動化加值，受限於 analyzer 對原生結構的技術假設。建議以 API 測試工具名義、fork 內部化導入，Web 當輔助，Mobile 此版本不採用。**

---

## 10. 加固後重評（2026-10-02，內部 fork v0.9.11）

POC 後針對 Rubric 低於 3 分的面向，於內部 fork（`stonebomdic/GoMore-QA-Master`）逐項加固；每案皆為 Sonnet 隔離實作 → Opus 覆審 → 全套測試 → 真實站回歸的流程。

| PR | 版本 | 內容 | 對應面向 |
| --- | --- | --- | --- |
| #6 / #10 | 0.9.8 | lint 歸零＋CI 守門、runner PATH 修復、`mcp<2` 鎖版、runner 層 234+ 單元測試、dogfood CI | 維護風險、CI |
| #7 | 0.9.8 | `analyze_url`/`auto_generate_tests` 支援 `auth_storage`（localStorage token，`$ENV` 間接引用、origin 限定注入） | Web auth |
| #8 | 0.9.8 | generator：按鈕欄位過濾、描述分類、`expect_response` API 斷言、`_pick_form_api` | 產測品質 |
| #9 | 0.9.8 | BOLA A′ 四探測指紋判定（owner/actor/unauth/對照組）、`bola_shared_endpoints` 宣告降級、GET-only 防呆 | API 安全掃描（待 W2 重驗） |
| #11 | 0.9.8 | skill/plugin manifest prompt audit（stale facts、壞連結） | 整合成本 |
| #12 | 0.9.9 | table 模組三層偵測、implicit form 欄位聚合、selector fallback 鏈（placeholder/name）＋跳脫 | Web 測項生成 |
| #13 | 0.9.10 | selector 唯一化、cta 語意定位、auth conftest 鷹架（token 零落檔）、檔名碰撞 | 產測品質、Web auth |
| #14 | 0.9.11 | 可見性感知（隱藏元素存在性骨架）、table 列數/表頭真斷言、E2E 實跑產出斷言 | Web 測項生成、產測品質 |

**實證**：gwp-admin-qa `/users` 以 `auto_generate_tests(auth_storage={"accessToken": "$QA_WEB_TOKEN"})` 產出 9 筆（2 native table、implicit 搜尋表單、nav、dialog、3 cta、1 form），**9/9 可直接執行**（POC 時同頁 table 偵測 0、form 欄位空；加固初期 2/8）；table 測試含 `tbody tr` 非零與表頭文字斷言。測試基線 283 → 876 passed。

**尚未完成**：W2（對 GWP Backend QA 重跑 OWASP 掃描驗證 BOLA 假陽性歸零，需兩組使用者 token＋`bola_test_ids`）；W3（tag/CHANGELOG/上游追蹤政策/每週 fresh-install 健檢）；mutating M1–M3 驗證（需可丟棄帳號）。

---

## 附錄：實證索引

- 7 個 500 + 9 個 schema drift 的完整參數與重現：[`qa-bug-tickets-gwp-backend-qa.md`](qa-bug-tickets-gwp-backend-qa.md)
- 各階段逐步紀錄與環境細節：[`sqa-poc-plan.zh-TW.md`](sqa-poc-plan.zh-TW.md) 附錄 A（Phase 0–1）/ B–C（Phase 2）/ D（Phase 3）/ E（Phase 4）
