# gomore-qa-master — SQA 內部導入 POC 執行手冊

> 目標：在 1.5–2 週內驗證 `gomore-qa-master` 是否適合 SQA 團隊用於**公司內部 API / web / mobile 驗證**，並產出 Go/No-Go 決策依據。
>
> 團隊現況（POC 前提）：
> - **API**：部分服務有 OpenAPI/Swagger spec（→ schemathesis 為主力）；部分為手刻測試（→ 需補 spec 或改走 newman）。
> - **Web**：以 Playwright/pytest 為主力（→ jest / cypress / go runner 本次不啟用）。
> - **Mobile**：iOS + Android 都要（→ maestro runner 全保留，Phase 4 納入）。
> - **AI client**：Claude Code（→ MCP 走 Claude Code 接線）。

---

## 0. 採用範圍決策（保留 / 停用清單）

依現況裁切，POC 只啟用以下模組，降低噪音：

| 模組 | 決策 | 理由 |
|---|---|---|
| `runners/schemathesis.py` | ✅ 啟用（API 主力） | 有 OpenAPI spec，自動 property-based fuzz |
| `runners/newman.py` | ✅ 備援 | 手刻 API 可改寫為 Postman collection 過渡 |
| `runners/api_security.py` + `security_rules/*` | ✅ 啟用 | 內部 API OWASP Top 10 掃描，高附加價值 |
| `runners/pytest_playwright.py` | ✅ 啟用（web 主力） | 與現有技術棧一致 |
| `runners/maestro.py` | ✅ 啟用（iOS + Android） | mobile 在職責範圍 |
| `reporters/html.py` | ✅ 啟用 | 交付用報告 |
| `config.py` / `security.py` | ✅ 啟用 | 安全護欄，進公司環境前提 |
| `tools/qa_plan.py`（qa_plan / verify_plan） | ✅ 試用 | 輕量驗收機制；注意 verify 僅子字串比對 |
| `tools/analyzer.py` / `generator.py` / `auto_generate_tests` | ⚠️ 重點評估 | 產測品質決定去留（Phase 3 審查） |
| `tools/optimizer.py` / `telemetry.py` | △ 後置 | nice-to-have，POC 不做評估重點 |
| `runners/jest.py` / `cypress.py` / `go_test.py` | ⛔ 本次不啟用 | 非現有技術棧（保留無成本，靠 `QA_RUNNER` 切換） |
| `tools/visual_challenge*.py`（reCAPTCHA/hCaptcha 解題） | ⛔ 停用 | 合規 + 維護風險高；內部驗證應於測試環境關閉 captcha。`QA_VISUAL_CHALLENGE_CONSENT` 維持預設關閉 |

> **架構限制（重要）**：一個 server 行程同時只綁定一種 `QA_RUNNER`。要同時做 API + web + mobile，需在 Claude Code 設定**三個 MCP server entry**（見 Phase 5）。

---

## Phase 0｜環境準備與健全性（0.5 天）

```bash
# 1. 隔離環境
git clone <repo-url> gomore-qa-master && cd gomore-qa-master
python3 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e '.[api]'          # 含 schemathesis extra

# 2. 跑專案自帶單元測試（驗證安裝完整）
pytest tests/ -v                 # 期望：全綠

# 3. 確認 server 能啟動（Ctrl-C 結束）
QA_PROJECT_ROOT=$PWD/tests_project QA_RUNNER=pytest gomore-qa-master
```

- [ ] `pytest tests/` 全綠
- [ ] server 正常啟動（停在等待 stdio）

**Go/No-Go**：任一失敗 → 先排除環境問題再繼續。

---

## Phase 1｜自帶 sample 驗證各 runner（0.5 天）

不碰公司系統，先確認每條 pipeline 跑得通。

```bash
# --- web (pytest-playwright) ---
python -m playwright install chromium
QA_PROJECT_ROOT=$PWD/tests_project QA_RUNNER=pytest pytest tests_project/ -v

# --- API fuzz (schemathesis，dry-run 不發實際請求) ---
schemathesis run --dry-run --checks all examples/sample_api_project/openapi.yaml

# --- OWASP 安全掃描（對自帶 vulnerable fixture）---
# 先啟動 examples/sample_vulnerable_api（見其 README），再：
QA_API_SECURITY_CONSENT=true python -c "
from gomore_qa_master.runners.api_security import run_scan
import json
print(json.dumps(run_scan(
    spec_url='examples/sample_vulnerable_api/openapi.yaml',
    base_url='http://localhost:<port>',
), ensure_ascii=False, indent=2))
"
```

- [ ] web sample 產出 report.json
- [ ] schemathesis dry-run 列出 operations
- [ ] 安全掃描對 vulnerable fixture 抓到預期 findings（驗證掃描器有效）

---

## Phase 2｜對接公司內部 API（2–3 天｜核心）

挑一個 **staging 環境、有 OpenAPI 的內部 API**。

### 2a. 功能測試（schemathesis）

```bash
QA_RUNNER=schemathesis \
QA_OPENAPI_URL="https://staging.internal/openapi.yaml" \
QA_SCHEMATHESIS_AUTH="Bearer <staging-token>" \
QA_SCHEMATHESIS_MAX_EXAMPLES=20 \
QA_PROJECT_ROOT=$PWD/poc-api \
  gomore-qa-master
# 經 Claude Code 呼叫 run_tests；或先用 `--dry-run` 對 prod spec 做安全預覽
```

> 手刻 API 的服務：先補一份 OpenAPI spec（建議），或將既有請求整理成 Postman collection，改用
> `QA_RUNNER=newman` + `QA_POSTMAN_COLLECTION=<path>`。

### 2b. 安全掃描（run_api_security_scan）

```bash
# 環境層 consent + 授權網域（非 localhost 必填）
export QA_API_SECURITY_CONSENT=true
export QA_API_SECURITY_AUTHORIZED_DOMAINS="staging.internal"
```

經 Claude Code 呼叫 `run_api_security_scan`：
- `spec_url`：staging OpenAPI
- `auth.token` + `auth.alt_user_token`：兩個不同使用者 token（觸發 BOLA / 函式級授權規則）
- `auth.bola_test_ids`：`{user_a:[...], user_b:[...]}` 各自擁有的物件 id
- `categories`：預設 `headers + broken_auth + bola + function_authz`
- ⚠️ **`mass_assignment` 會變更伺服器狀態** → 只在可丟棄的測試環境，明確加進 `categories` 才跑

- [ ] schemathesis 對內部 API 跑出報告
- [ ] 安全掃描產出 findings，並逐條標記真陽性 / 假陽性
- [ ] 比對已知漏洞，記錄是否有漏報

**Go/No-Go**：報告可信、假陽性比例可接受。

---

## Phase 3｜對接內部 web（1–2 天｜評估 analyzer 去留）

對一個內部 staging web 頁面：

1. **建立業務知識**（決定產測品質的關鍵）：
   經 Claude Code 呼叫 `init_qa_knowledge`，填寫 `qa-knowledge.md`（業務規則 / 歷史 Bug / 標準斷言）。
2. **探索**：`analyze_url`（登入後頁面帶 `auth_cookie="name=value; ..."`）→ 檢視 modules / api_endpoints / layout_warnings 偵測品質。
3. **產測**：`auto_generate_tests`（或逐 module `generate_test` 並帶 `business_context`）→ **人工審查**：是真有業務意義，還是泛例 monkey test？
4. **執行 + 報告**：`run_tests` → `generate_html_report` → 檢視 HTML 可讀性。

- [ ] analyze_url 偵測到關鍵表單 / API endpoint
- [ ] 填了 qa-knowledge 後產測品質明顯提升（對照組）
- [ ] HTML 報告可直接交付 PM/RD

**Go/No-Go**：若無業務知識時產測淪為 monkey test，且填知識後仍不足 → analyzer 標記為「輔助、非主力」。

---

## Phase 4｜對接 mobile（iOS + Android，1–1.5 天）

```bash
# 前置：安裝 Maestro CLI、啟動 iOS Simulator / Android Emulator
# Android 遠端裝置（BlueStacks 等）：設 QA_ANDROID_HOST=127.0.0.1:5555
QA_RUNNER=maestro QA_PROJECT_ROOT=$PWD/poc-mobile gomore-qa-master
```

1. `analyze_screen`（帶 `app_id` + `launch_app=true`）分別對 iOS / Android dump → 檢視 form / cta / tab_bar 偵測品質。
2. `generate_test`（產 Maestro `.yaml` flow）→ `run_tests` 跑通，確認截圖與 auto-retry。
3. iOS、Android 各跑一條 happy-path flow。

- [ ] iOS Simulator 跑通一條 flow
- [ ] Android Emulator / 實機跑通一條 flow
- [ ] 失敗時截圖 / 重試機制正常

---

## Phase 5｜Claude Code 整合 + plan→verify 迴路（0.5 天）

因單 server 綁單 runner，設定 **3 個 entry**。編輯 Claude Code 的 MCP 設定（`claude_desktop_config.json` 或 plugin manifest）：

```jsonc
{
  "mcpServers": {
    "qa-api": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "gomore_qa_master.server"],
      "cwd": "/path/to/gomore-qa-master",
      "env": {
        "QA_RUNNER": "schemathesis",
        "QA_PROJECT_ROOT": "/path/to/poc-api",
        "QA_OPENAPI_URL": "https://staging.internal/openapi.yaml",
        "QA_API_SECURITY_CONSENT": "true",
        "QA_API_SECURITY_AUTHORIZED_DOMAINS": "staging.internal"
      }
    },
    "qa-web": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "gomore_qa_master.server"],
      "cwd": "/path/to/gomore-qa-master",
      "env": { "QA_RUNNER": "pytest", "QA_PROJECT_ROOT": "/path/to/poc-web" }
    },
    "qa-mobile": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "gomore_qa_master.server"],
      "cwd": "/path/to/gomore-qa-master",
      "env": { "QA_RUNNER": "maestro", "QA_PROJECT_ROOT": "/path/to/poc-mobile" }
    }
  }
}
```

重啟 Claude Code，確認三組工具出現在 autocomplete。

**plan→verify 迴路測試**：
1. `qa_plan`：宣告成功標準（critical points）。
2. 執行對應 runner。
3. `verify_plan`（可開 `auto_discover:true` 讀 report.json）。
4. **刻意假陽性測試**：放一個故意不滿足的 critical point，確認回 `incomplete`（驗證子字串比對弱點對你們是否可接受）。

- [ ] 三組工具皆可在 Claude Code 呼叫
- [ ] plan→verify 迴路運作
- [ ] 假陽性測試：verify 正確回報未滿足

---

## Phase 6｜評估彙整與 Go/No-Go

評分（1–5 分，3 為可接受門檻）：

| 評估面向 | 量測來源 | 分數 | 備註 |
|---|---|---|---|
| 安裝 / 上手難度 | Phase 0–1 | | |
| 內部 API 功能驗證 | Phase 2a | | |
| API 安全掃描真陽性率 | Phase 2b（比對已知漏洞） | | |
| Web 產測品質 | Phase 3 人工審查 | | |
| Mobile（iOS + Android） | Phase 4 | | |
| 報告可讀性 / 可交付 | HTML 報告 | | |
| 安全護欄充分性 | 路徑/注入/timeout/redaction | | |
| Claude Code 整合成本 | Phase 5 | | |
| 授權 / 合規 | captcha 已停用、MIT 授權符合內部政策？ | | |
| 長期維護風險 | 單一作者 + Beta，是否需 fork 內部化 | | |

### 已知風險（決策時納入）

1. **單一 active runner** → 多 server entry 管理複雜度。
2. **verify_plan 僅子字串比對** → 不可當品質閘門唯一依據。
3. **runner 封裝層幾乎無單元測試** → 關鍵流程需自行補測。
4. **無內建 secret 管理** → token 走 env，需搭配公司 secret 管理（CI secrets / Vault）。
5. **Beta + 單一作者維護** → 建議採用即 **fork 內部化**，鎖版本、自行補測、停用 captcha 模組。

### 建議決策路徑

- **Go（推薦範圍）**：採用 schemathesis（API）+ pytest-playwright（web）+ maestro（mobile）+ api_security 掃描 + HTML 報告；**fork 內部化**並停用 captcha solver。
- **條件式 Go**：analyzer / auto_generate_tests 視 Phase 3 結果決定「主力」或「輔助」。
- **No-Go 觸發**：API 安全掃描假陽性過高、或產測品質無法超越 monkey test 且無法靠業務知識改善。

---

## 附錄 A｜Phase 0–1 實測結果（2026-05-29，本機 macOS）

| 項目 | 結果 |
|---|---|
| 套件安裝 `pip install -e '.[api]'` | ✅ schemathesis 3.39 等依賴就緒 |
| 單元測試 `pytest tests/` | ✅ **283 passed**（約 4.5s） |
| MCP server 啟動 | ✅ 註冊 **21 tools** + 3 resources + 13 runner aliases，stdin 開著時正常掛起 |
| Web sample `pytest tests_project/` | ✅ 2 passed |
| schemathesis dry-run（自帶 OpenAPI） | ✅ 收集 3 operations |
| OWASP 掃描（vulnerable fixture） | ✅ 12 ops，命中全部 ground-truth：BOLA(critical)×/vuln/orders、FunctionAuthz(high)×/vuln/admin、BrokenAuth AlgNone+WrongSignature(high)×/vuln/profile；`/safe/*` 未誤報嚴重類別 |

掃描 summary：`total 38 → critical 2 / high 4 / medium 32`。**真陽性命中率佳**；medium 32 筆全來自 `OWASP-API8-Headers`（每個 endpoint × 缺少的安全標頭各一筆），是主要 noise 來源，Phase 2 對真實 API 時建議用 `severity_threshold='high'` 過濾，或單獨檢視 headers 類別。

### 環境注意事項（本機踩到、寫給後續操作者）

1. **Python 版本**：專案需 `>=3.10`，系統預設 `python3.9` 不可用。本機 Homebrew 的 `python@3.12` 因 `pyexpat`／系統 `libexpat` 符號不相容而壞掉（pip 無法 bootstrap）；改用 `~/.local/bin/python3.11`（uv 管理）建立 venv 才成功。
2. **editable 安裝的 `.pth` 未生效**：uv 管理的 python 建立的 venv，其 `_editable_impl_gomore_qa_master.pth`（指向 `src/`）未被加進 `sys.path`，直接 `import gomore_qa_master` 會 `ModuleNotFoundError`。**繞過**：執行時加 `PYTHONPATH=$PWD/src`。透過 console script（`.venv/bin/gomore-qa-master`）或 MCP 接線則不受影響，僅手動 `python -c/-` 呼叫內部模組時需注意。

---

## 附錄 B｜Phase 2 目標 spec 偵察（GWP Backend QA）

spec URL：`https://gwp-backend-qa.gomore.net/api-json`（base URL：`https://gwp-backend-qa.gomore.net`）

| 項目 | 值 |
| --- | --- |
| 規格 | OpenAPI 3.0.0，`GWP Backend API` v1.8.34，693 KB |
| 規模 | **265 paths / 336 operations / 412 schemas** |
| 方法分布 | GET 161、POST 96、PUT 35、DELETE 30、PATCH 14 → **mutating 共 175** |
| servers | **未宣告** → 必須顯式傳 `base_url` |
| security schemes | `userAccessToken`、`adminAccessToken`、`registrationToken`、`passwordResetToken`（皆 bearer） |
| global security | 無（逐 operation 宣告） |

### 偵察即發現的真實缺陷（可直接回報後端）

dry-run（不發請求）解析時 **329 ops 正常、7 ops errored**，根因是 spec 內有 **3 個被 `$ref` 引用卻未定義的 schema**：

- `EmailLoginResponseDto`、`LoginRequireConfirmResponseDto` → 影響 `POST /auth/email/login`
- `DepartmentParticipationItemDto` → 影響 `GET /admin/activities/vbt-physical/{activityId}/participation-report`

→ 這會讓任何依 spec 產 client/mock/contract test 的工具在這些端點失敗，建議列為 spec 修正項。

### Phase 2 安全注意（重要）

- **175 個 mutating operations**：schemathesis 預設會對所有 operation（含 POST/PUT/DELETE）送請求，**會變更 QA 資料**。對真實 QA 站務必先 `--dry-run`，正式跑要用 tag / path 收斂範圍，或限定 GET。
- `run_api_security_scan` 的 `bola` / `function_authz` 多為 GET（相對安全）；`broken_auth` 會竄改 token 重送；**`mass_assignment` 會變更狀態，預設關閉、勿對 QA 開啟**。
- 非 localhost：安全掃描需設 `QA_API_SECURITY_AUTHORIZED_DOMAINS=gwp-backend-qa.gomore.net`。

### 未認證連線確認（2026-06-01，read-only）

| 探測 | 結果 |
| --- | --- |
| TLS / 連線 | ✅ 憑證有效，connect ~0.42s、TLS ~0.46s |
| `GET /app-version` | ✅ 200，回版本 JSON（~136ms） |
| `GET /iap/products` | ✅ 200，回商品清單 JSON（4.5 KB） |
| `GET /auth/bindings`（認證 GET） | ✅ 401 Unauthorized（認證牆正常） |

→ QA 站可達、回應快、認證牆運作正常。147 個認證 GET 需 token 才能實測；已備好 GET-only spec（`/tmp/gwp_spec_getonly.json`，161 GET / 0 mutating），待兩個 user token 即可跑 schemathesis + 安全掃描。

### Token 驗證（2026-06-01）

第一個 user token（user 548 `bomdic_qa`，非 superuser，效期約 10h）驗證結果：

- ✅ JWT 有效、認證層放行（帶 token 後不再 401）。
- ⚠️ **`/account/profile`、`/auth/bindings` 回 `404 ACCOUNT_NOT_FOUND`** — claims `isNewUser:true`，此帳號在 QA 尚未建置資料。
- 影響：BOLA 需「使用者各自擁有的物件」才有意義；空帳號無法有效驗證跨使用者讀取。

### 決議與待辦

- **功能 fuzz + BOLA 改用兩個「已建置資料、各自擁有資源」的 user token**（待提供）。理想再附各自的物件 id 作 `bola_test_ids`。
- schemathesis fuzz 量定為 **`--hypothesis-max-examples 3`**（161 GET ≈ 480 唯讀請求，先看 QA 穩定度）。
- 執行順序：token 驗證 → schemathesis GET-only fuzz → 安全掃描（`headers + broken_auth + bola + function_authz`，**不開 mass_assignment**）→ 彙整。

---

## 附錄 C｜Phase 2 實跑結果（2026-06-01，GET 唯讀）

兩個已建置帳號 token（user 530 / 528，皆一般使用者、效期 1h）。schemathesis GET-only fuzz（max-examples=3，1707 test cases，46.6s）+ OWASP 安全掃描（161 GET、不開 mass_assignment）。

### C-1. schemathesis 找到的真實 bug（高價值）

**🔴 7 個端點對畸形輸入回 500（輸入驗證不足，應回 4xx）**

| 端點 | 觸發輸入 | 500 訊息 |
| --- | --- | --- |
| `GET /activities/vbt-physical/{activityId}/records` | `activityId=0.0&page=0.0&pageSize=1.0` | **MySQL 語法錯誤外洩**（error-based 資訊洩漏／潛在注入面，最嚴重） |
| `GET /exercises` | `limit=-1.0` | Database query error |
| `GET /missions/weekly-report` | `startDate/endDate` | 資料庫查詢錯誤 |
| `GET /mood-diary/stats` | `endDate=0` | Incorrect DATE value: '0' |
| `GET /mood-diary/card/{date}` | `date=0` | Incorrect DATE value: '0' |
| `GET /mood-diary/date/{date}` | `date=0` | Incorrect DATE value: '0' |
| `GET /weather/{language}/{latitude}/{longitude}` | `0.0/0.0` | HTTP 客戶端錯誤 |

→ 共通根因：畸形 query/path 參數（負數、`0`、浮點 id）未驗證就進 DB，回 500 而非 400。`vbt-physical/records` 直接吐 raw MySQL 錯誤，建議列**安全 + 穩定性**雙重優先修。

**🟠 9 個端點回應 schema 與 spec 不符**（contract drift）

`/account/profile`（`waistCircumference` 回 `null` 但 spec 宣告 `number` 非 nullable）、`/missions`、`/ai/history/messages`、`/activities`、`/activities/muscle-fat/{id}/progress`、`/meditations/courses`、`/food/{code}`、`/nutrition/daily`、`/nutrition/weekly`。

### C-2. OWASP 安全掃描結果

| 規則 | 結果 | 評語 |
| --- | --- | --- |
| `bola` (API1) | ⚠️ **6 筆 CRITICAL 全為假陽性** | 已人工驗證：USER_A/USER_B 對 `/activities/muscle-fat/528/notice` 拿到**完全相同**內容（同 md5）→ 是共享活動內容，非他人私料。規則只憑「拿別人 id 得 200」即判洩漏，未比對回應是否真為私有 |
| `headers` (API8) | 6 筆 MEDIUM，合理但低值 | 多數 QA 端點其實有安全標頭；僅幾個 OAuth 端點缺 HSTS/CSP。CSP 對 JSON API 意義不大（偏 noise），HSTS 算合理建議 |
| `broken_auth` (API2) | 0 筆 | 認證牆穩固（未認證一律 401） |
| `function_authz` (API5) | 0 筆（GET-only 下無觸發） | 一般使用者打 `/admin/*` 一律 401，授權正確 |

### C-3. 工具評估結論（對應附錄 A 評分表）

- **schemathesis（API 功能/穩定性）：⭐⭐⭐⭐⭐ 高度推薦。** 純唯讀 GET fuzz 47 秒就抓到 7 個 500 + 1 個 SQL 錯誤外洩 + 9 個 contract drift，零設定、零汙染資料。對內部 API 驗證即戰力強。
- **api_security BOLA 規則：⭐⭐ 精準度低、需人工覆核。** 6 筆 CRITICAL 全假陽性。根因：(1) 需餵「真正的物件 id」而非 user id；(2) 規則未做「跨使用者回應差異比對」就判洩漏。**結論：BOLA findings 不可直接採信，須搭配回應 diff 人工驗證**；建議內部 fork 後強化此規則或改用其他 BOLA 工具。
- **api_security headers / broken_auth / function_authz：⭐⭐⭐ 可用。** 無假陽性風暴，headers 偏保守但有效，認證/授權判斷正確。
- **整體 spec 健康度**：偵察階段即抓到 3 個 dangling `$ref`（見附錄 B），加上 9 個 contract drift，顯示此工具鏈對「spec 與實作一致性」很有揭露力。

### C-4. 給後端團隊的可回報清單（優先序）

1. **P0 安全**：`/activities/vbt-physical/{activityId}/records` 畸形 id → raw MySQL 錯誤外洩，檢查輸入驗證與 SQL 組裝。
2. **P1 穩定**：6 個端點對 `0` / 負數 / 浮點參數回 500，補輸入驗證（回 400）。
3. **P2 契約**：9 個端點回應與 OpenAPI schema 不符（含可空欄位未標 nullable）。
4. **P3 spec**：3 個 dangling `$ref` schema 未定義。
5. **P3 標頭**：少數 OAuth 端點補 HSTS。

---

## 附錄 D｜Phase 3 實跑結果（2026-06-01，Web / QA Admin）

目標：`https://gwp-admin-qa.gomore.net/`（Megoluki Admin，**Nuxt/Vue SPA**）。

### D-1. 認證機制查核（決定 analyze_url 可用性）

靜態解析前端 bundle 確認：

- token 存於 **`localStorage["accessToken"]`**（+ `userInfo`），由 axios 攔截器以 `Authorization: Bearer <token>` 送出。
- **無** cookie-based auth（無 Nuxt `useCookie`、登入頁無 Set-Cookie）。

→ **`analyze_url` 的 `auth_cookie` 對此站無效**（只能注入 cookie、不能注入 localStorage）。這是 analyzer 對「token-in-localStorage 型 SPA」（現代內部工具常見模式）的明確限制。
→ 評估時以 Playwright 手動注入 `localStorage["accessToken"]` 繞過，成功進入登入後儀表板（`Megoluki Admin - 儀表板`），再對真實 admin 內容跑 analyzer 的 DOM 偵測。

### D-2. analyzer 偵測品質（登入後 3 個頁面）

| 頁面 | API 偵測 | 模組偵測 |
| --- | --- | --- |
| `/`（儀表板） | ✅ 13 個真實 admin API（users/stats、companies、activities/*…） | 6 module，多為登出對話框 chrome |
| `/profile` | ✅ 同上 13 個 | form×1 但 **fields=None**、其餘為登出 CTA |
| `/company/muscle-fat/detail` | ✅ `GET /admin/activities/muscle-fat/1` | form×2 fields=None、含「確認刪除」對話框 |

**強項 ⭐⭐⭐⭐ — API 相依圖**：自動抓出每頁實際呼叫的後端 API，等於替後台畫出 backend 相依清單，對「改一支 API 影響哪些頁」很有用。

**弱項 ⭐⭐ — 模組/測項偵測**（資料密集 SPA admin）：

- `<form>` 抓得到但 **欄位明細抓不到（fields=None）** — Nuxt 用自訂元件而非原生 `<input name>`，欄位擷取邏輯落空（saucedemo 也同樣 fields=None）。
- DOM probe `sections:0` 恆為 0 — **資料表、統計卡、圖表完全沒被辨識**。
- 模組輸出被**全域 layout chrome**（登出系統／確認登出／取消／確認刪除）洗版，每頁雷同、非該頁真正功能。
- candidate TC 通用（「點擊送出應有回應」「loading 時禁用」），非業務導向。

### D-3. 未跑 auto_generate_tests→run_tests→report 的理由

對此 admin 跑「產測→執行」會雙重失效：(1) 產出的 pytest-playwright 測試用純 `page.goto`、**不會注入 localStorage token**，執行時會被導回登入頁；(2) 產測來源是 D-2 的弱模組，內容多為登出 chrome。→ 結論可預期，不另燒資源。**若要驗證 generator/runner/reporter 三模組的機制**，建議改用可直接執行的 cookie-less 目標（如公開練習站或一般使用者 web）單獨測。

### D-4. Phase 3 結論

- **analyzer 對「API 相依探勘」高度可用**，對「資料密集後台的測項自動生成」幫助有限，且**對 localStorage-token SPA 的登入後分析需手動繞過**。
- 對你們而言：admin 後台的 E2E 自動化，analyzer/auto_generate 目前**只能當輔助**（探 API、列頁面），真正的測試案例仍需人工＋業務知識撰寫。
- **改善前提**（若要 fork 內部化）：analyzer 需補 (a) localStorage/token 注入的 auth 參數、(b) 自訂元件表單欄位擷取、(c) 資料表/卡片區塊辨識。

### D-5. 順帶發現

注入 admin token 後，儀表板與 `/profile` 都會觸發同一批 13 個 `/admin/*` GET（layout 預抓）。可交叉比對 Phase 2：這些正是一般使用者打會回 401 的端點，admin token 下回 200，**授權分層正確**。

---

## 附錄 E｜Phase 4（Mobile）環境前置盤點（2026-06-01，本機 Apple Silicon Mac）

實跑前的環境檢查即揭露 mobile 導入的高前置成本：

| 元件 | 狀態 | 影響 |
| --- | --- | --- |
| Maestro CLI | ✅ 已安裝（official 安裝腳本） | 可用 |
| Java 21 | ✅ | Maestro 依賴滿足 |
| **iOS 工具鏈** | ❌ 只有 CommandLineTools、**無完整 Xcode** → `simctl` 不存在 | **iOS 無法測**，需從 App Store 裝完整 Xcode（數 GB） |
| **Android AVD** | ⚠️ 唯一 AVD `Pixel_6_API_31` 為 **x86_64 image** | 本機為 **arm64**，x86_64 需 Intel VT-x → emulator 開機即失敗 |
| Android arm64 image | ❌ 未安裝（僅 x86_64 的 28/29/31） | 需下載 arm64-v8a image（~1GB）+ 建新 AVD |
| Android SDK 工具鏈 | ⚠️ 舊 `tools/bin/sdkmanager` 與 Java 21 不相容（JAXB 移除）、新 cmdline-tools 未裝 | 需先補 cmdline-tools 才能裝 image |
| **Megoluki app 安裝檔** | ❌ 無 .apk / .ipa | `analyze_screen` 需 app 在前景，無安裝檔則無法真實評估 |

### E-1. 評估結論（對應 rubric「Mobile 可用度」與「環境/維運成本」）

- **mobile 測試的環境前置成本遠高於 API/web**：iOS 需完整 Xcode + simulator runtime；Android 需架構相符（Apple Silicon → arm64）的 emulator image + 可用的 SDK 工具鏈；兩者都需 app 安裝檔。導入時須將這筆**環境建置 + CI runner 成本**計入（CI 上跑 mobile 需 macOS runner + 模擬器，成本與維運明顯較高）。
- 這與工具本身無關，是 maestro / 行動測試的固有成本，但**對採用決策實質有影響**。
- 待補：取得 Megoluki .apk + 建好 arm64 AVD 後，續跑 `analyze_screen` → 產 Maestro flow → `run_tests` 評估 mobile 偵測品質。

### E-2. Android arm64 環境建置（已完成，可供 SQA 複製）

在 Apple Silicon Mac 上把 Android 環境從零修好的步驟（每步都踩過坑，記錄供團隊複製）：

1. **裝 Maestro**：`curl -Ls https://get.maestro.mobile.dev | bash` → 得 maestro 2.6.0。
2. **補新版 cmdline-tools**（舊 `tools/bin/sdkmanager` 與 Java 21 不相容、JAXB 被移除）：下載 `commandlinetools-mac-*_latest.zip` 解壓到 `$SDK/cmdline-tools/latest/` → sdkmanager 20.0 可用。
3. **裝 arm64 image**：`sdkmanager "system-images;android-31;google_apis;arm64-v8a"`（~1GB）。
4. **建 arm64 AVD**：`avdmanager create avd -n Pixel6_arm64 -k "system-images;android-31;google_apis;arm64-v8a" -d pixel_6`。
5. **更新 emulator 套件為 Apple Silicon 原生**（既有 emulator 32.1.11 是 x86_64，開 arm64 AVD 會 `PANIC: arm64 not supported on x86_64 host`）：`sdkmanager --install "emulator"` → emulator 執行檔變 arm64。
6. **開機**：`emulator -avd Pixel6_arm64 -no-snapshot -gpu swiftshader_indirect` → ~20s boot 完成。

→ 共踩 4 個坑：Java 21 vs 舊 sdkmanager、x86_64 image vs arm64 host、x86_64 emulator binary vs arm64 AVD、無 arm64 image。**這正是「mobile 環境前置成本高」的具體證據**，CI 上要重現這套需 macOS arm64 runner + 完整腳本。

### E-3. Pipeline 就緒驗證（無需 app）

對 launcher 跑 `analyze_screen` 成功：maestro 2.6.0 認得 `emulator-5554`，dump view hierarchy 並偵測到 6 個 CTA 模組（home_settings / widgets / wallpapers / back / overview / home）。
→ **maestro → analyze_screen 整條 pipeline 在此環境可用**；待 Megoluki .apk 安裝後即可對真實 app 評估偵測品質。

### E-4. 真實 app 實跑結果（決定性發現）

安裝 `com.gomore.megoluki.qa`（Mego Luki QA v2.0.30，209 MB，minSdk 26）→ 啟動成功（pid 7120，前景 MainActivity）。

**🔴 Megoluki 是 Flutter app，`analyze_screen` 對它幾乎完全失效。**

logcat 證據：`com.llfbandit.app_links` plugin、ANGLE 渲染、Firebase → 典型 Flutter 技術棧。

| 畫面 | 截圖實際內容 | `maestro hierarchy` 看到的 |
| --- | --- | --- |
| 歡迎頁 | MegoLuki logo、角色插畫、**「點擊開始旅程！」** CTA | 49 節點，文字節點 7 個**全是 Android 系統列**（Back/Overview/Home + 時間/Wifi/電池）。CTA 完全不存在 |
| 載入頁 | 進度條、「加載中… 11/789」 | 109 節點，accessibilityText 為**逐字拆碎**（「加」「載」「中」分開、「16/789」），無任何可互動元件 |

→ **根因**：Flutter 把整個 UI 渲染成單一 opaque canvas，預設不對外暴露原生 semantics tree。`analyze_screen` 依賴的 `maestro hierarchy` 只能讀到 Android 系統 chrome + 偶爾漏出的破碎文字，**讀不到任何 app 自身的按鈕/表單/tab**。

### E-5. Phase 4 結論

- **對 Megoluki（Flutter）而言，gomore-qa-master 的 mobile 自動分析（analyze_screen → 產 Maestro flow）不可用** — 偵測不到任何真實 app 元件。這不是裝置或設定問題，是 analyzer **假設原生 view hierarchy**、遇到 Flutter/canvas-based app 即失效。
- 影響範圍：Flutter、（部分）React Native、遊戲、WebView-heavy app 都會有類似問題。你們的主力 app 既是 Flutter，**這條路目前走不通**。
- **可能的補救（需評估成本）**：
  1. app 端啟用 Flutter semantics（`SemanticsBinding.instance.ensureSemantics()`）或在裝置上開 accessibility service，讓 maestro 讀得到語意樹 — 需 RD 配合改 app。
  2. 改用 Maestro 的**座標/影像式**操作（非 hierarchy）手寫 flow — 但這就完全用不到 gomore-qa-master 的 analyze_screen / 自動產測，等於只用底層 Maestro。
- **maestro CLI 本身可驅動 app**（座標點擊已驗證能翻頁），但 gomore-qa-master 在 mobile 這層提供的「自動偵測 + 產測」加值，對 Flutter app 無法兌現。
- iOS 未測（本機無 Xcode）；但同為 Flutter app，iOS 上 `analyze_screen` 預期會遇到**相同的 canvas 不可見問題**。

### E-6. rubric 對應

- **Mobile 可用度：⭐（對你們的 Flutter app）** — 環境前置成本高 + analyzer 對 Flutter 失效，雙重阻礙。
- 若未來有原生 (Kotlin/Swift) app，analyze_screen 才有機會發揮（launcher 測試證明對原生 view tree 可用）。

---

## 附錄 F｜generator / runner / reporter 三模組端到端驗證（2026-06-01）

目標：cookie-less 公開站 `saucedemo.com`（標準 HTML 登入表單）。流程：`auto_generate_tests → run_tests → get_test_report → get_failure_details → generate_html_report`。

### F-1. generator（pytest-playwright 產測）⭐⭐⭐ 機制可用、品質有缺陷

- ✅ 對標準 HTML 表單**選擇器擷取正確**（`#user-name` / `#password` / `#login-button`）— 與 Phase 3 Nuxt 自訂元件 fields=None 形成對比，**原生 HTML 表單才抓得到欄位**。
- 🐛 缺陷一：對**非可填元件（送出按鈕）也呼叫 `.fill()`** → 執行即報錯。
- 🐛 缺陷二：產出 body 與 docstring 的 TC 描述**矛盾**（描述「欄位為空時送出」，程式卻先填值）。
- 🐛 缺陷三：**無真實斷言**，只有 `# TODO: 補上實際斷言`。
- → 產出是「需人工補完的骨架」，開箱即跑會 fail。

### F-2. runner（pytest_playwright）⭐⭐⭐⭐ 端到端可用

- ✅ 執行後產出 `report.json` + `junit.xml`、**自動歸檔 history 快照**、**自動產出 `optimization-plan.md`**。
- ✅ 正確捕捉失敗，`get_failure_details` 解析出每個失敗的 **screenshot + trace.zip + video** 路徑。
- ⚠️ 小觀察：runner 以**裸 `pytest` 指令**呼叫（假設在 PATH），而非 `sys.executable -m pytest`；venv 環境下需確保 pytest 在 PATH（MCP 接線時 env 要帶對）。
- 註：本次 2 個測試 fail 是 F-1 的 generator 缺陷所致，非 runner 問題；runner 反而正確呈現了失敗 + artifact。

### F-3. reporter（html）⭐⭐⭐⭐⭐ 產品級

- ✅ 產出 **99 KB 自包含 HTML**（無外部 CDN）、**base64 內嵌截圖**、Passed/Failed 區塊、失敗卡片含完整 traceback、趨勢 sparkline。
- ✅ 渲染後為深色儀表板（摘要卡 TOTAL/PASSED/FAILED/DURATION + Pass Rate + 失敗卡片），**可直接交付 PM/RD / 寄信 / 貼 Slack**。

### F-4. 小結

- **runner + reporter 機制成熟可用**（rubric「報告可讀性」「pytest_playwright」由「未端到端驗」改為已驗證）。
- **generator 僅產骨架、且有 fill 按鈕等缺陷**，需人工補完才能跑 —— 與 Phase 3 結論一致：**自動產測是輔助、非主力**。
