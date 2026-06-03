# Mutating Method API 驗證設計（POC Phase 2.5）

> 日期：2026-06-03
> 對象：SQA 團隊 — `gomore-qa-master` 導入 POC 的延伸驗證
> 背景：先前 POC（見 [`sqa-poc-plan.zh-TW.md`](../../sqa-poc-plan.zh-TW.md) 附錄 C）對 GWP Backend QA 僅跑 **GET 唯讀** fuzz，spec 內 175 個 mutating operations（POST 96 / PUT 35 / DELETE 30 / PATCH 14）尚未驗證。本設計補上 POST/PUT/PATCH 的實跑驗證。

---

## 1. 目標

對 `gwp-backend-qa.gomore.net` 的 mutating API 做更完整驗證，涵蓋四個面向：

1. **穩定性 / 輸入驗證** — 對畸形 request body / 參數，端點是否回 5xx（應回 4xx）。
2. **合約一致性** — mutating 回應是否符合 OpenAPI schema 與宣告狀態碼。
3. **CRUD 正確性** — 建立 → 讀回 → 更新 → 再讀回的狀態是否正確。
4. **安全（mass_assignment / BOPLA）** — 能否透過 mutating 寫入不該寫的欄位。

非目標：DELETE 破壞性驗證、跨使用者 / admin 端點（已於 Phase 2 證實被 401 擋下）、行動 / web。

---

## 2. 安全模型

- **目標環境**：現有 QA 站（不另建可丟棄環境）。
- **圍欄 = 授權範圍（authorization scope）**：以低權限、可丟棄的 user token 執行，最壞情況只汙染「該測試帳號自己的資料」；跨使用者 / admin 的破壞性操作已被授權層擋下（Phase 2 實證：一般使用者打 `/admin/*` 一律 401）。
- **Method 範圍**：POST 96 + PUT 35 + PATCH 14 = **145 ops 全跑**；DELETE 30 以 method 排除。
- **端點黑名單**（在 M0 用 spec tag / path 實際列出，path-based 排除）：
  - `auth/*` — email login、password reset 等會**寄信**。
  - `iap/*` — **付費 / 帳務**副作用。
  - `notification` / push — **推播**副作用。
  - 任何 `deactivate` / 自毀帳號類（避免結束測試帳號 session）。

---

## 3. 前置條件

1. **可丟棄測試帳號 + token**：須為「已建置資料」帳號（Phase 2 踩過空帳號回 `404 ACCOUNT_NOT_FOUND`，空帳號無法有效驗證 mutating 對自有資源的效果）。
2. **帳號重建 runbook**：測試跑壞帳號狀態時能還原。
3. **與後端對齊**：確認此帳號可在 QA 自由 mutate；選**離峰**時段，避免干擾他人。
4. **環境變數**：`QA_API_SECURITY_CONSENT=true`、`QA_API_SECURITY_AUTHORIZED_DOMAINS=gwp-backend-qa.gomore.net`（M3 需要）。token 只走 env，不寫入專案檔 / git / log。

---

## 4. 四階段（風險遞增，各有 Go/No-Go）

### M0 — Dry-run 盤點（不發請求）

- `schemathesis --dry-run --include-method POST --include-method PUT --include-method PATCH --exclude-path-regex <黑名單>` 解析 145 ops。
- 分出三類：**有 requestBody schema（可 fuzz）** / **無 schema（標記，fuzz 價值低）** / **黑名單（排除）**。
- **產出**：可跑清單、無 body schema 清單、最終黑名單。
- **Go/No-Go**：黑名單已涵蓋全部副作用端點、可跑清單規模合理。

### M1 — 穩定性 + 合約 fuzz（面向 ①②）

- `schemathesis run --include-method POST,PUT,PATCH --checks all`（含 `not_a_server_error`、`response_schema_conformance`、`status_code_conformance`、`content_type_conformance`）。
- `--hypothesis-max-examples 1~3` 起步（先低汙染探站台穩定度與資料量）。
- 排除黑名單 path。
- **產出**：5xx 端點清單（比照附錄 C 體例）、contract drift 清單。
- **Go/No-Go**：站台穩定、資料汙染可控 → 才考慮放大 example 數。

### M2 — 策劃式 CRUD 序列（面向 ③）

- **不使用 schemathesis stateful**：附錄 B 顯示此 spec 未宣告 OpenAPI `links`，無法自動串序列。
- 手挑 5–10 個帳號自有資源族（如 mood-diary、activities records）。
- 每族流程：`POST 建立 → GET 讀回確認 → PUT/PATCH 更新 → GET 驗證寫入值`（DELETE 在黑名單，改以帳號重置或保留資料收尾）。
- **工具**：Newman/Postman collection 或 pytest 腳本策劃。
- **產出**：CRUD 正確性結果（讀回值是否符合寫入、狀態碼是否合理）。
- **Go/No-Go**：機制可用、序列可重複。

### M3 — mass_assignment / BOPLA 安全（面向 ④，風險最高放最後）

- `api_security` runner，`categories` **只開 `mass_assignment`**。
- 只打帳號自有資源端點，不碰 admin、不跨使用者。
- **產出**：能否寫入不該寫欄位的 findings（比照 BOLA 經驗，**須人工覆核**）。
- **Go/No-Go**：完成即彙整。

---

## 5. 全程安全護欄

- DELETE method 排除；副作用 path 黑名單。
- 低並發、低 example 數起步，視穩定度再放大。
- 離峰執行、與後端對齊。
- 帳號重建 runbook 待命。
- token 只走 env，不落地。

---

## 6. 產出物

- `docs/sqa-poc-plan.zh-TW.md`：新增「Phase 2.5（mutating）」章節 + 「附錄 G」實跑結果（比照附錄 C 體例）。
- bug → 併入 `docs/qa-bug-tickets-gwp-backend-qa.md`。
- `docs/poc-summary-report.zh-TW.md`：更新「2. 評估方法」與 rubric（補 mutating 涵蓋與限制）。

---

## 7. 成功標準

- 145 個 POST/PUT/PATCH（扣黑名單）跑完 M1。
- 四個面向各有具體結果，或明確記錄「為何未跑」。
- QA 站未被破壞、測試帳號狀態可還原。
