# GWP Backend QA — Bug 單（Jira-ready）

> 來源：gomore-qa-master POC，schemathesis GET-only fuzz（max-examples=3）+ OWASP 掃描，2026-06-01。
> 環境：`https://gwp-backend-qa.gomore.net`（QA）。所有重現皆需 `Authorization: Bearer <user token>`。
> 格式：Jira wiki 標記。若貴 Jira 用新版 rich-text 編輯器、`h3.`/`{code}` 未自動渲染，貼上後切到「{} / Wiki」輸入模式即可。
> 嚴重度對照：Blocker=P0、Critical=P1、Major=P2、Minor=P3。

---

## BUG-1 〔Blocker / P0〕`/activities/vbt-physical/{activityId}/records` 畸形參數導致 MySQL 語法錯誤外洩

*Type:* Bug
*Priority:* Blocker
*Component:* Backend / activities-vbt-physical
*Labels:* security, sql-injection-surface, input-validation, info-disclosure
*Environment:* QA `https://gwp-backend-qa.gomore.net`

h3. Description
畸形的 path / query 參數（浮點 `activityId`、`page`）未經驗證即進入 SQL 組裝，伺服器回 500 並**在回應主體中外洩 raw MySQL 語法錯誤訊息**。同時暴露兩個風險：(1) 錯誤型資訊洩漏（DB 種類、SQL 片段）；(2) 使用者輸入直達 SQL 字串組裝，屬潛在 SQL Injection 攻擊面。

h3. Steps to Reproduce
# 取得任一有效 user token。
# 執行：
{code:bash}
curl -i -H "Authorization: Bearer <TOKEN>" \
  'https://gwp-backend-qa.gomore.net/activities/vbt-physical/0.0/records?page=0.0&pageSize=1.0'
{code}

h3. Expected
回 *400 Bad Request*（參數型別/範圍驗證失敗），不揭露任何 SQL/DB 內部資訊。

h3. Actual
HTTP *500*，回應主體：
{code:json}
{"errorCode":"unknown","message":"You have an error in your SQL syntax; check the manual that corresponds to your MySQL server version for the right syntax to use near '-1' at line 1"}
{code}
（`page=0.0` 被換算成 `OFFSET -1` 灌入查詢。）

h3. Suggested Fix
參數型別/範圍驗證（整數、>=0）並參數化查詢（prepared statement）；統一錯誤處理，永不回傳 DB 原始錯誤。

---

## BUG-2 〔Critical / P1〕多個 GET 端點對畸形數值/日期參數回 500（輸入驗證缺失）

*Type:* Bug
*Priority:* Critical
*Component:* Backend / 多模組（missions, exercises, mood-diary, weather）
*Labels:* input-validation, error-handling, robustness
*Environment:* QA `https://gwp-backend-qa.gomore.net`

h3. Description
多個 GET 端點在收到負數、`0`、浮點或無效日期等畸形參數時，未驗證即查 DB / 呼叫外部服務，回 *500* 而非 *400*。屬同一根因（輸入驗證 + 錯誤處理缺失），可作 umbrella 單或拆為各端點子單。

h3. Affected endpoints（即時確認可重現 500）
|| # || 端點（含觸發參數） || 致命參數 || 回應 message ||
| 1 | {{GET /missions/weekly-report?startDate=2025-10-13&endDate=2025-10-20}} | 日期區間 | 500-01-10-001 資料庫查詢錯誤。 |
| 2 | {{GET /exercises?limit=-1.0}} | limit 負數/浮點 | 500-01-09-003 Database query error. |
| 3 | {{GET /mood-diary/stats?endDate=0}} | endDate=0 | Incorrect DATE value: '0' |
| 4 | {{GET /mood-diary/card/0}} | path date=0 | Incorrect DATE value: '0' |
| 5 | {{GET /mood-diary/date/0}} | path date=0 | Incorrect DATE value: '0' |
| 6 | {{GET /weather/en-US/0.0/0.0}} | 經緯度 0.0/0.0 | 500-00-11-001 HTTP 客戶端發生錯誤 |

h3. Steps to Reproduce
{code:bash}
TOKEN='<user token>'
B=https://gwp-backend-qa.gomore.net
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" "$B/exercises?limit=-1.0"
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" "$B/mood-diary/stats?endDate=0"
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" "$B/mood-diary/card/0"
# 全部回 500
{code}

h3. Expected
畸形參數一律回 *400 Bad Request* 並附明確錯誤訊息；伺服器不得因使用者輸入而 500。

h3. Actual
皆回 *500*；DB 層錯誤（`Incorrect DATE value`、`Database query error`）直接外露。

h3. Suggested Fix
入口層加參數 schema 驗證（型別、範圍、日期格式）；DATE 欄位先驗證再查；外部服務（weather）失敗回 502/降級而非 500。

---

## BUG-3 〔Major / P2〕9 個端點回應與 OpenAPI schema 不符（contract drift）

*Type:* Bug
*Priority:* Major
*Component:* Backend / API contract
*Labels:* openapi, contract-test, schema-conformance
*Environment:* QA `https://gwp-backend-qa.gomore.net`

h3. Description
實際回應與 `/api-json` 宣告的 schema 不符（以 schemathesis `response_schema_conformance` 偵測）。最典型：可空欄位回 `null` 但 spec 宣告為非 nullable 型別，會讓依 spec 產生的 client / 強型別反序列化在線上炸掉。

h3. Affected endpoints
{{/account/profile}}, {{/missions}}, {{/ai/history/messages}}, {{/activities}}, {{/activities/muscle-fat/{id}/progress}}, {{/meditations/courses}}, {{/food/{code}}}, {{/nutrition/daily}}, {{/nutrition/weekly}}

h3. Steps to Reproduce（以 /account/profile 為例）
{code:bash}
curl -s -H "Authorization: Bearer <TOKEN>" \
  https://gwp-backend-qa.gomore.net/account/profile | python3 -m json.tool
{code}

h3. Expected
`waistCircumference` 等欄位的實際值需符合 spec 宣告型別。

h3. Actual
回 {{"waistCircumference": null}}，但 spec 宣告 {{type: number}}（未標 nullable）。

h3. Suggested Fix
擇一：(a) 修 spec 將可空欄位標 {{nullable: true}}；(b) 修實作確保非空。建議將 schemathesis contract test 納入 CI 防回歸。

---

## BUG-4 〔Minor / P3〕OpenAPI spec 含 3 個未定義的 `$ref` schema

*Type:* Bug
*Priority:* Minor
*Component:* Backend / API spec
*Labels:* openapi, spec-quality
*Environment:* QA `https://gwp-backend-qa.gomore.net/api-json`

h3. Description
`/api-json` 中有 3 個被 `$ref` 引用卻未定義的 schema，導致任何 spec-driven 工具（client/mock/contract test）在相關端點解析失敗。

h3. Detail
|| 缺少的 schema || 影響端點 ||
| EmailLoginResponseDto | POST /auth/email/login |
| LoginRequireConfirmResponseDto | POST /auth/email/login |
| DepartmentParticipationItemDto | GET /admin/activities/vbt-physical/{activityId}/participation-report |

h3. Expected
所有 `$ref` 指向的 schema 皆在 `components.schemas` 定義。

h3. Actual
3 個 dangling `$ref`；schemathesis 解析時對應 7 個 operation errored。

h3. Suggested Fix
補上 schema 定義（多為 DTO 未匯出至 swagger）。

---

## BUG-5 〔Minor / P3〕部分 OAuth 端點缺 Strict-Transport-Security 標頭

*Type:* Bug
*Priority:* Minor
*Component:* Backend / security headers
*Labels:* security, headers, hsts
*Environment:* QA `https://gwp-backend-qa.gomore.net`

h3. Description
多數端點已正確設定安全標頭，惟少數 OAuth 登入端點（如 {{/auth/login/line}}、{{/auth/login/line/callback}}）缺 *Strict-Transport-Security*。

h3. Steps to Reproduce
{code:bash}
curl -sI https://gwp-backend-qa.gomore.net/auth/login/line | grep -i strict-transport-security
# 無輸出 = 缺 HSTS
{code}

h3. Expected
全站回應含 {{Strict-Transport-Security: max-age=...; includeSubDomains}}。

h3. Actual
該端點未回 HSTS 標頭。

h3. Suggested Fix
於反向代理 / LB 或框架統一注入 HSTS。屬低風險加固項。

---

## 附註：未列入的「假陽性」（供 QA 內部備查，請勿開單）

OWASP 掃描器的 *BOLA* 規則報了 6 筆 CRITICAL，經人工驗證**全為假陽性**：USER_A 與 USER_B 對 `/activities/muscle-fat/528/notice` 取得**完全相同**的內容（同 md5），該內容為活動共享注意事項、非他人私有資料。規則僅以「拿他人 id 得 200」判定洩漏、未比對回應差異，且需餵真正的物件 id 而非 user id。**這些不是 bug，不應開單。**
