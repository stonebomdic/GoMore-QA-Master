"""Tool descriptions + input schemas — 自 server.py list_tools 逐字搬出（P2）。

單一事實來源：REGISTRY（tools/registry.py）以 name 取用這兩個 dict。
內容與重構前 tool surface 逐字相同，由
tests/fixtures/tool_surface_golden.json 鎖定。
"""

TOOL_DESCRIPTIONS = {'get_runner_info': '回傳目前由 QA_RUNNER 環境變數選定的測試 runner（pytest / jest / cypress / go / '
                    'maestro 五選一）加上 server 編譯時內建的全部 runner 清單。建議每個 session 第一個呼叫——AI '
                    '用它判斷後續該產 Playwright .py 還是 Maestro .yaml、要不要 headed '
                    'browser，避免後面拿錯模板。也用來確認專案環境設定正確：QA_PROJECT_ROOT 指對地方、QA_RUNNER '
                    "沒拼錯。回傳 shape：{active: 'pytest', available: ['pytest', 'jest', "
                    '...]}。',
 'list_tests': '用 runner 的原生 collection 機制列出受測專案內所有可執行測試：pytest 走 `pytest '
               '--collect-only`、Jest 走 `npx jest --listTests`、Cypress 走 '
               '`cypress/e2e/*.cy.*` glob、Go 走 `go test -list .*`、Maestro 走 `*.yaml` '
               '遞迴掃。回傳一份逐行 nodeid / 檔名清單。用法：run_tests 前確認 collection 沒漏、generate_test '
               '前避免跟既有 case 重複。',
 'run_tests': 'Execute the test suite under the active QA_RUNNER and produce a '
              'structured report. The single most-called tool — invoke whenever a user '
              'says 「跑/run/test/check/驗證/執行」, after generate_test (verify new test), '
              'or after a fix (confirm bug gone).\n'
              '\n'
              'Behavior:\n'
              "- Invokes the runner's native CLI under QA_PROJECT_ROOT — pytest with   "
              '--screenshot=on / --tracing=on / --video=retain-on-failure, or   `npx '
              'jest --json`, `npx cypress run --reporter json`, `go test -json`,   '
              '`maestro test --format junit`\n'
              '- Optional `filter` narrows the scope: pytest -k expr, jest -t '
              'pattern,   cypress --spec glob, go -run regex, maestro flow-name '
              'substring\n'
              '- Writes report.json (pytest-json-report shape, runner-agnostic) + '
              'JUnit XML\n'
              '- Snapshots the run into history/ and auto-triggers '
              'optimizer.write_plan()   → optimization-plan.md is refreshed\n'
              '- Maestro: auto-retries flows that failed on first attempt '
              '(MAESTRO_RETRY=true),   surfaces flaky_in_run count\n'
              'Returns: {exit_code, raw_exit_code, stdout_tail, stderr_tail, '
              'retry_enabled, flaky_in_run, ...}\n'
              '\n'
              'When to use:\n'
              '- After writing a new test → verify it actually passes\n'
              '- Smoke before a release\n'
              '- Whenever the user prompt contains a run/test verb\n'
              '\n'
              'When NOT to use:\n'
              '- Inspecting last results without re-running → use get_test_report '
              '(cheaper)\n'
              '- Re-running only failed cases → use run_failed (way faster)\n'
              '- Enumerating which tests exist → use list_tests\n'
              '\n'
              'Edge cases:\n'
              '- No tests match `filter` → exit_code != 0 with 「no tests ran」 in '
              'stderr_tail\n'
              '- QA_TIMEOUT_SECONDS exceeded → exit_code 124 + `[TIMEOUT…]` tag in '
              'stderr_tail\n'
              '- `filter` starting with `-` or containing `..` → blocked by security   '
              'guardrail, returns {error: …}',
 'run_failed': '只重跑上次失敗的測試——比跑整套套件快很多，適合修完一個 bug 後驗證迭代。pytest 走 '
               '`--lf`（last-failed）、Jest 走 `--onlyFailures`、Cypress 解析上次 report.json 的 '
               'failures[] 反查 spec 重跑、Go 撈失敗的 Test 名組成 regex 餵 -run、Maestro 反查 nodeid '
               '對應 .yaml 重跑。需要先有過一次 run_tests（不然 report.json 不存在）。回傳 shape 跟 run_tests '
               '一樣，接 get_test_report / get_failure_details 同樣方式檢視。',
 'get_test_report': '讀上一次 run_tests 留下的 report.json，回傳一個輕量摘要：total / passed / failed / '
                    'skipped / flaky_in_run（auto-retry 救回的數量）/ duration（秒）。比再跑一次 suite '
                    '便宜得多——適合在連續操作中間反覆查狀態。未跑過時回 {error: 找不到報告，請先執行 run_tests}。拿到摘要後若 '
                    'failed > 0，接 get_failure_details 拿錯誤細節。',
 'get_failure_details': 'Extract full root-cause-analysis materials for every failed '
                        'test in the most recent run.\n'
                        '\n'
                        'Behavior:\n'
                        '- Reads report.json, filters tests where outcome == 「failed」\n'
                        '- pytest: parses Playwright trace.zip → extracts real API '
                        'call sequence   (Frame.*, Page.*, Locator.*, ElementHandle.* '
                        'events) as steps[]\n'
                        '- Maestro: parses flow YAML for `takeScreenshot:` directives '
                        '→ resolves   <name>.png at PROJECT_ROOT root\n'
                        '- Best-effort resolves screenshot / trace.zip / video / '
                        'recording paths   from --output / --debug-output artifact '
                        'directories\n'
                        'Returns: list[{nodeid, title, message, duration, steps[], '
                        'screenshot, trace, video}]\n'
                        '\n'
                        'When to use:\n'
                        '- run_tests just reported failed > 0 → drill into each case\n'
                        '- User asks 「why did it fail / show me the trace / what '
                        'broke」\n'
                        '- Filing a JIRA bug → use the artifact paths to attach '
                        'screenshot+trace\n'
                        '- Comparing failure signatures across runs (pair with '
                        'get_test_history)\n'
                        '\n'
                        'When NOT to use:\n'
                        '- Want the summary count only → use get_test_report '
                        '(lighter)\n'
                        '- No tests have been run yet → returns [{error: 「找不到報告」}]\n'
                        '- Want details for PASSING tests too → not supported here; '
                        'the HTML   reporter renders those via a different path\n'
                        '\n'
                        'Edge cases:\n'
                        '- test_id substring matches nothing → empty list, no error\n'
                        '- screenshot/trace/video missing on disk → those fields are '
                        'null but   the entry stays\n'
                        '- Retry-recovered flake (was failed, now passed) → not listed '
                        'here;   surfaces in summary.flaky_in_run instead',
 'generate_test': '產生 pytest-playwright 測試骨架。推薦流程：先呼叫 analyze_url 拿 '
                  'candidate_tcs，再對每條想覆蓋的 TC 呼叫一次 generate_test、把該 candidate_tc 整段字串當 '
                  'description 傳入 — 這段會自動寫成 test 函式的 docstring，HTML 報告會把它當作 case '
                  '名稱顯示。若提供 url+module（來自 analyze_url 的 modules[]），會用 selectors '
                  '預填可執行版本。若想一次處理整個 URL、不想自己編排，請改用 auto_generate_tests。',
 'codegen': 'Launch interactive test recording for the active runner. Useful as a '
            'baseline-builder before refining with generate_test.\n'
            '\n'
            'Behavior:\n'
            '- pytest-playwright: spawns `playwright codegen -o <output> <url>` — a '
            'real   Chromium window opens, you click / type / navigate, Playwright '
            'transcribes   every action into runnable pytest code, output is saved '
            'to   PROJECT_ROOT/<output> on browser close\n'
            '- Maestro: returns a human-readable hint string pointing at   `maestro '
            'studio` (no shell-able codegen exists for it)\n'
            '- jest / cypress / go runners: same Maestro-style fallback hint\n'
            'Returns: a string with the saved path or the manual-record hint.\n'
            '\n'
            'When to use:\n'
            '- Building a baseline happy-path test interactively (you click, it '
            'transcribes)\n'
            "- Site has complex auth / JS state you'd rather not script by hand\n"
            '- Quick prototype before refining with generate_test\n'
            '- User says 「record / 錄製 / use codegen / 紀錄操作」\n'
            '\n'
            'When NOT to use:\n'
            "- Headless CI / container environments → can't open Chromium\n"
            '- Need structured, AI-driven test generation from analysis → use   '
            'generate_test or auto_generate_tests instead\n'
            '- One-shot per-module test coverage → use auto_generate_tests\n'
            '- Mobile UI flows → returns a hint anyway, consider analyze_screen +   '
            'generate_test instead\n'
            '\n'
            'Edge cases:\n'
            '- `output` contains `..` or is absolute → blocked by security guardrail\n'
            '- Chromium not installed → playwright codegen fails; user sees the   '
            '`playwright install` hint in stderr',
 'generate_html_report': '把最近一次 run_tests 的結果渲染成單檔自包含 HTML——base64 內嵌截圖、嵌入式 step '
                         'list、history sparkline 走勢、折疊的 Passed 區塊、展開的 Failed cards。沒外部 '
                         'CSS/JS 依賴，可以直接寄信、丟靜態 host、貼到 Slack。預設輸出 '
                         'PROJECT_ROOT/report.html。實作位於 reporters/html.py，走 '
                         'sample_report.html 同款設計。',
 'get_test_history': '遍歷 test-results/history/*.json 快照（每次 run_tests '
                     '完會自動歸檔），回傳逐次摘要：timestamp / total / passed / failed / skipped / '
                     'duration / pass_rate(0-100)。用於 flake 分析（『這條測試上週一直 fail '
                     '嗎』）、速度退化分析（『duration 是不是越來越長』）、覆蓋趨勢圖。預設回最近 10 次，limit 可調 '
                     '1-100。想要可執行行動建議的話接 get_optimization_plan，它已綜合 history + '
                     'telemetry。',
 'get_optimization_plan': '綜合 history/ 快照、telemetry tool-usage、analyze_url 偵測過的 '
                          'modules，產出三層自我強化分析：(1) 測試套件品質：每條 test 算 outcomes 字串（PFPFP '
                          '那種）→ flake_score、再對失敗 error signature 做指紋比對，連 3 次相同 '
                          'signature 升級為 broken，duration 退化超 1.5x 標記 '
                          'slow_regression，否則 stable_passing；(2) MCP 使用模式：top tool、重複 '
                          'args、錯誤率、常見呼叫鏈（A→B 共現）；(3) AI 產測效益：generate_test 寫的 test '
                          '有沒有出現在下一次 run、analyze_url 偵測到的 module 對不對得到 test 檔（採用率 vs '
                          '覆蓋缺口）。回傳結構化 JSON 並同步寫進 PROJECT_ROOT/optimization-plan.md。每次 '
                          'run_tests 結束會自動 trigger 一次、所以這個 tool 用來「即時讀」結果。',
 'analyze_url': 'Probe a live web page in headless Chromium and return a structured '
                'map of testable modules plus the API endpoints the page actually '
                'called. The web counterpart of analyze_screen.\n'
                '\n'
                'Behavior:\n'
                '- page.goto(url) with DOMContentLoaded + 5s networkidle wait\n'
                '- DOM probe extracts five module kinds: form (with fields[] + '
                'required   flags), nav (link lists), dialog (modal containers), '
                'section (labeled   regions), cta (action buttons matching action '
                'keywords like 登入/送出/  Login/Submit)\n'
                '- Each module gets a candidate_tcs[] — domain-aware test case '
                'strings   ready to paste into generate_test\n'
                '- Records every fetch/XHR the page issues, dedupes by (method, '
                'path),   adds endpoint-specific candidate TCs (401, 404, 4xx, '
                'payload-too-large…)\n'
                '- Layout overflow scan flags visible elements whose content escapes '
                'its   container by >2 px horizontal / >10 px vertical (跑版 / '
                'text-overflow)\n'
                'Returns: {url, page_title, scanned_at, modules[], api_endpoints[], '
                'layout_warnings[]}\n'
                '\n'
                'When to use:\n'
                '- User wants tests for a specific URL or page\n'
                '- Designing regression coverage from real user-facing behavior\n'
                '- Need backend API coverage hints (api_endpoints[] gives methods + '
                'paths)\n'
                '- Investigating layout bugs at the current viewport\n'
                '- Pair with generate_test(module=…) for one runnable test per module\n'
                '\n'
                'When NOT to use:\n'
                '- Mobile apps (no DOM) → use analyze_screen\n'
                '- Want analysis + immediate test generation → use '
                'auto_generate_tests   (one-shot version)\n'
                '- Looking for existing tests → use list_tests\n'
                '- Single-page testing prototype → use codegen instead\n'
                '\n'
                'Edge cases:\n'
                '- URL unreachable / timeout → returns {error: 「打開頁面失敗…」, url}\n'
                '- Page has 0 forms / 0 ctas → modules[] is empty but the call '
                'succeeds\n'
                '- Login-walled URL with no auth_cookie → analyzes the login page '
                '(less   useful) — pass auth_cookie to reach post-login pages\n'
                '- SPA with delayed hydration → bump timeout_ms to 30000+',
 'analyze_screen': 'Mobile 版的 analyze_url：透過 `maestro hierarchy` dump 當前 iOS Simulator '
                   '/ Android Emulator / 實體機 / BlueStacks（透過 QA_ANDROID_HOST）前景 app 的 '
                   'view tree，再分類成 form（具 hint_text 的輸入欄位）、cta（enabled + '
                   '有文字的可點元件）、tab_bar（selected 狀態 + 同 y 對齊的 2+ 個 tab）三種 modules 並附 '
                   'candidate_tcs。內建 noise filter 自動排除 iOS 狀態列 + asset 命名標籤（bg_* / '
                   '*_filled / 純數字 / 單一 ASCII 字元等）讓結果信號集中。需 Maestro CLI 已裝、裝置 '
                   'booted、app 已在前景。若給 app_id + launch_app=true，會先用 launchApp 啟動再 '
                   'dump。',
 'init_qa_knowledge': '在受測專案根 (PROJECT_ROOT) 建立 qa-knowledge.md 起手範本，含業務規則 / 歷史 Bug / '
                      '標準斷言文字 / User Journeys / 技術約束 5 個 H2 區段，每段都有 TODO '
                      '提示。Idempotent：檔已存在不會覆蓋（除非 overwrite=true）。新用戶建議第一次跑 MCP 就先 call '
                      '一次。這份檔案後續會被 get_qa_context 讀、做為 business_context 傳進 '
                      'generate_test，讓 AI 寫出有業務邏輯的測試（而不是泛例 monkey testing）。',
 'get_qa_context': '讀取受測專案的 qa-knowledge.md（業務規則 / 歷史 Bug / 標準斷言文字 / User Journeys '
                   '等領域知識），用 ## H2 區段拆分。用法：先 call 拿到整份或指定 section，再把相關段落以 '
                   'business_context 傳給 generate_test，產出的 test 就會自帶業務知識註解 — 跳脫 monkey '
                   'testing。若檔案不存在會 fallback 到內建的 ISTQB 七大原則 + 等價分割 + 邊界值 + 決策表 + 狀態轉換 '
                   '+ Mobile checklist 通用知識，先用著也可以；之後跑 init_qa_knowledge 建立專案專屬版本。',
 'auto_generate_tests': '一鍵交付：在內部依序做 analyze_url → 為每個偵測到的 module 用 candidate_tcs '
                        '內容各跑一次 generate_test，把整套 pytest 測試骨架寫進 '
                        'PROJECT_ROOT/tests/。等同於『analyze_url 後對每個 module 手動跑 N 次 '
                        'generate_test』的自動化版本，適合「給我一個 URL、其他你看著辦」這種快速覆蓋場景。每條 '
                        'candidate_tc 變成對應 test 函式的 docstring，run_tests 跑完 HTML 報告會用 '
                        'docstring 當 case 名稱顯示。回傳產生的檔案路徑列表 + 每個 module 對應幾個 test。預設每個 '
                        'module 1 條，想要更密的覆蓋拉 tests_per_module。',
 'run_api_security_scan': 'v0.8.0: OWASP API Security Top 10 (2023) rule-based '
                          'scanner. Loads an OpenAPI 3.x spec, walks each path × '
                          "method, and dispatches v0.8's 5 in-scope rules — BOLA "
                          '(API1), Broken Authentication (API2), Mass Assignment '
                          '(API3, opt-in), Function-Level Authz (API5), Security '
                          'Misconfiguration (API8). Returns a v0.8 security report '
                          'block with per-finding rule_id, severity '
                          '(critical/high/medium/low/info), endpoint, evidence dict, '
                          'and remediation_hint.\n'
                          '\n'
                          'Requires QA_API_SECURITY_CONSENT=true at the server level. '
                          'Non-localhost hosts must be in '
                          'QA_API_SECURITY_AUTHORIZED_DOMAINS (comma-separated). '
                          'mass_assignment mutates server state — opt in by passing it '
                          'in `categories`. Tier 1 fixture '
                          '(`examples/sample_vulnerable_api/`) ships with the package '
                          'for self-tests.\n'
                          '\n'
                          'v0.9.4 — Pass `plan_id` (from qa_plan) to auto-verify the '
                          "scan's findings against the plan's critical points in the "
                          'same call. The response gains a `plan_verification` block '
                          '(per-CP checklist + overall status). One-shot equivalent of '
                          'qa_plan → run_api_security_scan → verify_plan.\n'
                          '\n'
                          'v0.9.6 — writes a redacted `scan-results.json` (path via '
                          '`GOMORE_QA_SCAN_PATH` → '
                          '`<QA_PROJECT_ROOT>/scan-results.json` → '
                          '`./scan-results.json`) BEFORE plan verification runs. '
                          'Verified `finding_present`/`finding_absent` CPs load ground '
                          'truth from this artifact (not host evidence), so the '
                          'plan_id bookend is artifact-backed. Response gains '
                          '`scan_results_path`.\n'
                          '\n'
                          'Returns: {scan_id, spec_url, base_url, categories_run, '
                          'rules_ran, ops_scanned, severity_threshold, findings[...], '
                          'summary{total, by_severity}, '
                          'findings_below_threshold_count, scan_results_path, '
                          'plan_verification (only when plan_id given)}.\n'
                          '\n'
                          'Error shapes: consent_required / unauthorized_domain / '
                          'spec_load_failed / no_base_url / unknown_categories / '
                          'bad_severity_threshold.',
 'qa_plan': 'v0.9.1 — Store a critical-points checklist before acting on a QA task. '
            'The host LLM declares what success looks like (test passes, scan finds X, '
            'screenshot shows Y), this tool stores it, returns a `plan_id`. Later, '
            'call `verify_plan` with evidence (test result rows, scan findings, log '
            'lines, screenshot paths) and get a per-CP pass/fail verdict. Inspired by '
            "microsoft/Webwright's plan.md pattern: declaring success criteria "
            'up-front makes the verifier honest about whether the work was done.\n'
            '\n'
            'Plans live 30 minutes (cache TTL) in memory and are LRU-bounded at 50 '
            'outstanding.\n'
            '\n'
            'v0.9.3 — disk persistence: when QA_PROJECT_ROOT is set (or '
            'QA_PLAN_PERSIST=true), the plan is also dumped atomically to '
            '<QA_PROJECT_ROOT>/test-results/plans/<plan_id>.json. verify_plan '
            'transparently falls back to disk on in-memory misses, so plans survive '
            'process restarts and cache eviction. Expiry is still honored on disk '
            "reads — a TTL'd plan won't silently reload. Persistence is best-effort: "
            'filesystem errors never raise into the caller.\n'
            '\n'
            'Returns: {plan_id (12 hex chars), task, kind, critical_points [{id, '
            'description, verification_hint}], created_at, expires_at, persisted_to '
            '(filesystem path or null when persistence is off)}.\n'
            '\n'
            "v0.9.6 — a CP may carry an `assert` block to become 'verified' "
            "(artifact-backed) rather than 'attested' (hint substring); and a "
            'top-level `strict` flag can require all CPs be verified. See the '
            '`critical_points` / `strict` schema for details.\n'
            '\n'
            'Error shapes: no_task / no_critical_points / bad_critical_points '
            '(duplicate id, missing description, wrong type, bad assert) / bad_kind.',
 'verify_plan': "v0.9.1 (extended v0.9.2 with auto-discovery) — Walk a plan's critical "
                'points and check each against evidence. Pairs with `qa_plan` — must '
                'be called with the plan_id returned by a prior qa_plan call. Returns '
                'a structured checklist with per-CP satisfaction + an overall status '
                '(passed / incomplete / failed).\n'
                '\n'
                'Matching rule: a CP is satisfied when its verification_hint appears '
                "(case-insensitively, as a substring) in any evidence item's "
                'stringified form. Evidence items may be strings, dicts, or nested '
                'structures — the matcher flattens them.\n'
                '\n'
                'v0.9.2 — auto_discover mode: set `auto_discover: true` and the '
                "verifier reads the project's pytest-json-report at "
                '`<QA_PROJECT_ROOT>/report.json` (or `GOMORE_QA_REPORT_PATH`, or the '
                '`report_path` arg) and adds its `tests` list to the evidence stream. '
                'Best-effort — missing or malformed report is silently skipped, NOT a '
                "hard error. The response's `evidence_sources` field reports what was "
                'used.\n'
                '\n'
                'status semantics:\n'
                "  - 'passed': every CP satisfied\n"
                "  - 'incomplete': some satisfied, some not\n"
                "  - 'failed': zero CPs satisfied (or empty evidence)\n"
                '\n'
                "Even if the host claims 'all good', verify_plan returns 'incomplete' "
                "when any CP is unsatisfied. That's the design — ground truth wins "
                'over capability claims.\n'
                '\n'
                'v0.9.3 — When persistence is enabled (see qa_plan), an in-memory '
                "cache miss transparently falls back to disk. The response's "
                "`plan_source` field reports where the plan came from: 'memory' (cache "
                "hit) or 'disk' (loaded from <plans_dir>/<plan_id>.json after a "
                'restart / eviction).\n'
                '\n'
                'v0.9.6 — verified vs attested tiers. A CP with an `assert` block is '
                "'verified': the tool loads the authoritative artifact (report.json "
                'for test_*; scan-results for finding_*) and judges the typed '
                "assertion itself, IGNORING host-supplied evidence — so it can't be "
                "faked. A failed test therefore can't satisfy test_passed (the bug the "
                'substring matcher had). Missing artifact = fail-closed (never '
                'satisfied, incl. finding_absent). CPs without `assert` stay '
                "'attested' (substring, unchanged). The `strict` arg (or a strict "
                "plan) requires every CP be verified AND satisfied for 'passed'. Each "
                'checklist entry carries `tier`; verified entries add `assertion` + '
                '`actual`. Response also gains `verification{verified, '
                'verified_satisfied, attested, attested_satisfied}`.\n'
                '\n'
                'Returns: {plan_id, task, kind, strict, status, checklist[{id, '
                'description, verification_hint, tier, satisfied, matched_evidence, '
                'assertion?, actual?}], unmet[], summary{total, satisfied, '
                'unsatisfied}, verification{...}, evidence_sources{...}, plan_source '
                "('memory' or 'disk'), verified_at}.\n"
                '\n'
                'Error shapes: no_plan_id / plan_not_found / no_evidence (only when '
                "there's an attested CP AND both explicit evidence AND auto_discover "
                'are omitted) / bad_evidence.'}

TOOL_SCHEMAS = {'get_runner_info': {'properties': {}, 'type': 'object'},
 'list_tests': {'properties': {}, 'type': 'object'},
 'run_tests': {'properties': {'browser': {'default': 'chromium',
                                          'description': '選填，僅對 pytest-playwright '
                                                         '有效，指定 Playwright 啟用的 browser '
                                                         'engine。需事先 `playwright '
                                                         'install <browser>` 過。',
                                          'enum': ['chromium', 'firefox', 'webkit'],
                                          'type': 'string'},
                              'filter': {'description': '選填，測試名稱關鍵字。pytest 走 -k 表達式（支援 '
                                                        'and/or/not）、Jest 走 -t、Cypress '
                                                        "走 --spec '**/*<filter>*'、Go 走 "
                                                        '-run regex、Maestro 在 flow '
                                                        '檔名作子字串比對。',
                                         'type': 'string'},
                              'headed': {'default': False,
                                         'description': '選填，僅對 pytest-playwright '
                                                        '有效。True 時瀏覽器有 UI 模式跑（適合 '
                                                        'debug、看 flake 視覺現象）；預設 '
                                                        'headless 跑、CI / 大量套件用這個。',
                                         'type': 'boolean'}},
               'type': 'object'},
 'run_failed': {'properties': {}, 'type': 'object'},
 'get_test_report': {'properties': {}, 'type': 'object'},
 'get_failure_details': {'properties': {'test_id': {'description': '選填，僅回傳 nodeid '
                                                                   '含此關鍵字的 '
                                                                   'case（substring '
                                                                   'match，不分大小寫）。省略則回傳全部失敗 '
                                                                   'case。常用模式：先全部抓→看到特定模式後再用 '
                                                                   'test_id 收斂。',
                                                    'type': 'string'}},
                         'type': 'object'},
 'generate_test': {'properties': {'business_context': {'description': '選填，業務規則 / 歷史 '
                                                                      'Bug / 標準斷言文字 '
                                                                      '等領域知識。提供後會以 `# '
                                                                      'Business '
                                                                      'context:` '
                                                                      '註解區塊印進 test '
                                                                      '函式內，讓人類 '
                                                                      'reviewer 與後續 AI '
                                                                      '都能看到設計依據。建議先 '
                                                                      'call '
                                                                      'get_qa_context() '
                                                                      '拿到相關 section '
                                                                      '再傳入。',
                                                       'type': 'string'},
                                  'description': {'description': 'test 的描述文字。會直接寫成產出 '
                                                                 'test 函式的 '
                                                                 'docstring（pytest）或 '
                                                                 'YAML '
                                                                 '開頭註解（Maestro），HTML '
                                                                 '報告會用這段當 case '
                                                                 '名稱顯示。建議直接傳 '
                                                                 'analyze_url / '
                                                                 'analyze_screen 回來的某個 '
                                                                 'candidate_tc 整段字串。',
                                                  'type': 'string'},
                                  'filename': {'description': '輸出檔名，相對於 '
                                                              'PROJECT_ROOT。pytest 用 '
                                                              '.py、Maestro 用 '
                                                              '.yaml、Jest 用 '
                                                              '.test.js、Cypress 用 '
                                                              '.cy.js、Go 用 '
                                                              '_test.go。不可絕對路徑、不可含 '
                                                              '`..`（會被 security '
                                                              'guardrail 擋）。',
                                               'type': 'string'},
                                  'module': {'description': '選填，analyze_url 結果 '
                                                            'modules[] 中的一個項目；提供後會用 '
                                                            'selectors 預填',
                                             'type': 'object'},
                                  'url': {'description': '選填，受測 URL；提供後 page.goto 會預填',
                                          'type': 'string'}},
                   'required': ['description', 'filename'],
                   'type': 'object'},
 'codegen': {'properties': {'output': {'default': 'recorded_test.py',
                                       'description': '選填，輸出檔名（相對於 '
                                                      'PROJECT_ROOT，不可絕對路徑、不可含 '
                                                      '`..`）。預設 `recorded_test.py`。',
                                       'type': 'string'},
                            'url': {'description': '受測 URL。Playwright codegen 會開瀏覽器 '
                                                   'navigate 到此網址、從這頁開始錄製你的互動。',
                                    'type': 'string'}},
             'required': ['url'],
             'type': 'object'},
 'generate_html_report': {'properties': {'output': {'default': 'report.html',
                                                    'description': '選填，輸出檔名（相對於 '
                                                                   'QA_PROJECT_ROOT）。預設 '
                                                                   '`report.html`。',
                                                    'type': 'string'}},
                          'type': 'object'},
 'get_test_history': {'properties': {'limit': {'default': 10,
                                               'description': '選填，回最近 N 次 run '
                                                              '的摘要。1-100，預設 10。長期 '
                                                              'flake 分析建議 30+。',
                                               'maximum': 100,
                                               'minimum': 1,
                                               'type': 'integer'}},
                      'type': 'object'},
 'get_optimization_plan': {'properties': {'history_limit': {'default': 10,
                                                            'description': '選填，套件品質分析會看最近 '
                                                                           'N 次 '
                                                                           'history '
                                                                           '快照。1-100，預設 '
                                                                           '10。flake '
                                                                           'score 至少要 '
                                                                           '5 '
                                                                           '次以上才穩，深度分析建議 '
                                                                           '30+。',
                                                            'maximum': 100,
                                                            'minimum': 1,
                                                            'type': 'integer'},
                                          'telemetry_limit': {'default': 500,
                                                              'description': '選填，MCP '
                                                                             '使用模式分析會看 '
                                                                             'telemetry '
                                                                             '最近 N 筆 '
                                                                             'tool-call。10-5000，預設 '
                                                                             '500。長期使用模式分析拉到 '
                                                                             '2000+，近期問題排查 '
                                                                             '100-200 '
                                                                             '就夠。',
                                                              'maximum': 5000,
                                                              'minimum': 10,
                                                              'type': 'integer'}},
                           'type': 'object'},
 'analyze_url': {'properties': {'auth_cookie': {'description': '選填，預先注入登入 '
                                                               'cookie，格式：`name1=value1; '
                                                               'name2=value2`（一行 '
                                                               'cookie '
                                                               'header）。用法：先在瀏覽器 '
                                                               'DevTools / Application '
                                                               '/ Cookies '
                                                               '複製值再貼進來。用於分析需要登入後才看得到的頁面。',
                                                'type': 'string'},
                                'auth_storage': {'additionalProperties': {'type': 'string'},
                                                 'description': '選填，適用於把 token 存在 '
                                                                'localStorage 的 SPA（cookie '
                                                                '認證請改用 '
                                                                'auth_cookie）。分析前先把每個 '
                                                                'key/value 寫進 '
                                                                'localStorage，導航前就位，SPA '
                                                                '啟動時即已登入；注入只作用於目標 URL '
                                                                '的 origin，不會外流到跨網域 '
                                                                'iframe（廣告 / 分析 / 客服 widget）或 SSO '
                                                                '轉址網域。格式：{"token": '
                                                                '"..."}。強烈建議值用 '
                                                                '`$ENV_NAME` '
                                                                '間接引用（例如 '
                                                                '`"$QA_WEB_TOKEN"`），實際值改放環境變數，避免明碼 '
                                                                'token 落進對話 / '
                                                                'log；僅當整段字串完全符合 '
                                                                '`$ENV_NAME`（英數底線、不可數字開頭）格式才會展開，其餘（含單一 '
                                                                '`$` '
                                                                '或不合法變數名）一律當明碼直接使用。環境變數不存在時整個 '
                                                                'tool 會回傳 error（不會靜默略過）。',
                                                 'type': 'object'},
                                'timeout_ms': {'default': 15000,
                                               'description': '選填，page.goto 等待 '
                                                              'DOMContentLoaded '
                                                              '的逾時毫秒數。之後額外 wait 5 秒讓 '
                                                              'networkidle（XHR '
                                                              '載入）穩定。預設 15000。慢站 / 需要 '
                                                              'SSR / 重 JS hydration '
                                                              '的網站可拉到 30000+。',
                                               'type': 'integer'},
                                'url': {'description': '要分析的網頁 URL，需含 protocol（http:// '
                                                       '或 https://）。',
                                        'type': 'string'}},
                 'required': ['url'],
                 'type': 'object'},
 'analyze_screen': {'properties': {'app_id': {'description': '選填，bundle id (iOS) / '
                                                             'package name '
                                                             '(Android)，格式如 '
                                                             '`com.example.app`。搭配 '
                                                             'launch_app=true '
                                                             '使用，或為了在輸出標註是分析哪個 app。',
                                              'type': 'string'},
                                   'launch_app': {'default': False,
                                                  'description': '搭配 app_id：True 時在 '
                                                                 'hierarchy dump 前用 '
                                                                 'maestro launchApp 啟動 '
                                                                 'app。用 clearState: '
                                                                 'false（保留 app '
                                                                 '狀態），確保看到「真實」起始畫面。省略則假設裝置上 '
                                                                 'app 已是當前前景。',
                                                  'type': 'boolean'},
                                   'timeout_ms': {'default': 30000,
                                                  'description': '選填，hierarchy '
                                                                 '命令超時毫秒。預設 '
                                                                 '30000；BlueStacks / '
                                                                 '遠端 ADB '
                                                                 '較慢，QA_ANDROID_HOST '
                                                                 '有設時會自動拉到 60000 起跳。',
                                                  'type': 'integer'}},
                    'type': 'object'},
 'init_qa_knowledge': {'properties': {'overwrite': {'default': False,
                                                    'description': '強制覆蓋既存檔案（會丟失你已填的內容、請先備份）',
                                                    'type': 'boolean'}},
                       'type': 'object'},
 'get_qa_context': {'properties': {'section': {'description': '選填，只取單一 H2 '
                                                              'section（不區分大小寫、支援部分匹配）。省略則回整份檔 '
                                                              '+ 所有 section 名稱清單。',
                                               'type': 'string'}},
                    'type': 'object'},
 'auto_generate_tests': {'properties': {'auth_cookie': {'description': '選填，登入後分析所需 '
                                                                       'cookie，格式：`name1=value1; '
                                                                       'name2=value2`。從 '
                                                                       'DevTools / '
                                                                       'Application / '
                                                                       'Cookies '
                                                                       '抓現成值貼進來。',
                                                        'type': 'string'},
                                        'auth_storage': {'additionalProperties': {'type': 'string'},
                                                         'description': '選填，適用於把 token '
                                                                        '存在 localStorage '
                                                                        '的 SPA（cookie '
                                                                        '認證請改用 '
                                                                        'auth_cookie）。內部 '
                                                                        'analyze_url '
                                                                        '分析前先把每個 '
                                                                        'key/value 寫進 '
                                                                        'localStorage，導航前就位，SPA '
                                                                        '啟動時即已登入；注入只作用於目標 '
                                                                        'URL 的 origin，不會外流到跨網域 '
                                                                        'iframe（廣告 / 分析 / 客服 '
                                                                        'widget）或 SSO '
                                                                        '轉址網域。格式：{"token": '
                                                                        '"..."}。強烈建議值用 '
                                                                        '`$ENV_NAME` '
                                                                        '間接引用（例如 '
                                                                        '`"$QA_WEB_TOKEN"`），實際值改放環境變數，避免明碼 '
                                                                        'token '
                                                                        '落進對話 / '
                                                                        'log；僅當整段字串完全符合 '
                                                                        '`$ENV_NAME`（英數底線、不可數字開頭）格式才會展開，其餘（含單一 '
                                                                        '`$` '
                                                                        '或不合法變數名）一律當明碼直接使用。環境變數不存在時整個 '
                                                                        'tool 會回傳 '
                                                                        'error（不會靜默略過）。',
                                                         'type': 'object'},
                                        'tests_per_module': {'default': 1,
                                                             'description': '選填，每個 '
                                                                            'module 從 '
                                                                            'candidate_tcs '
                                                                            '取前 N '
                                                                            '條各產一條 '
                                                                            'test。1-10，預設 '
                                                                            '1（最少噪音）。想要更密的覆蓋拉 '
                                                                            '3-5；拉到 10 '
                                                                            '通常會產 '
                                                                            'garbage '
                                                                            'tests，因為 '
                                                                            'candidate_tcs '
                                                                            '後段是泛例。',
                                                             'maximum': 10,
                                                             'minimum': 1,
                                                             'type': 'integer'},
                                        'timeout_ms': {'default': 15000,
                                                       'description': '選填，analyze_url '
                                                                      '內部 page.goto 等 '
                                                                      'DOMContentLoaded '
                                                                      '的逾時毫秒。預設 '
                                                                      '15000，慢站可拉到 '
                                                                      '30000+。',
                                                       'type': 'integer'},
                                        'url': {'description': '要分析並批次產測的 URL，需含 '
                                                               'protocol（http:// 或 '
                                                               'https://）。',
                                                'type': 'string'}},
                         'required': ['url'],
                         'type': 'object'},
 'run_api_security_scan': {'properties': {'auth': {'description': 'Auth config. '
                                                                  '`token` enables '
                                                                  'single-user rules '
                                                                  '(headers + '
                                                                  'broken_auth). Add '
                                                                  '`alt_user_token` to '
                                                                  'enable two-user '
                                                                  'rules (bola + '
                                                                  'function_authz). '
                                                                  'For BOLA: also '
                                                                  'provide '
                                                                  '`bola_test_ids: '
                                                                  '{user_a: [...], '
                                                                  'user_b: [...]}` '
                                                                  'listing the ids of '
                                                                  'objects each user '
                                                                  'owns. Optionally '
                                                                  'add '
                                                                  '`bola_shared_endpoints` '
                                                                  '(glob path '
                                                                  'patterns) for '
                                                                  'endpoints already '
                                                                  'known to be shared '
                                                                  '— matches downgrade '
                                                                  'BOLA to INFO '
                                                                  '`-DeclaredShared`.',
                                                   'properties': {'alt_user_token': {'description': 'Second '
                                                                                                    "user's "
                                                                                                    'bearer '
                                                                                                    'token '
                                                                                                    '(enables '
                                                                                                    'BOLA '
                                                                                                    '+ '
                                                                                                    'FLA).',
                                                                                     'type': 'string'},
                                                                  'bola_shared_endpoints': {'description': '(glob) '
                                                                                                           '已知登入後共享的 '
                                                                                                           'path '
                                                                                                           '樣式，命中降級 '
                                                                                                           'INFO '
                                                                                                           'DeclaredShared',
                                                                                            'items': {'type': 'string'},
                                                                                            'type': 'array'},
                                                                  'bola_test_ids': {'description': '{user_a: '
                                                                                                   '[ids], '
                                                                                                   'user_b: '
                                                                                                   '[ids]}',
                                                                                    'type': 'object'},
                                                                  'fla_admin_paths': {'description': 'Substrings '
                                                                                                     'marking '
                                                                                                     'elevated-priv '
                                                                                                     'paths. '
                                                                                                     'Default: '
                                                                                                     "['/admin/', "
                                                                                                     "'/admin'].",
                                                                                      'items': {'type': 'string'},
                                                                                      'type': 'array'},
                                                                  'fla_low_priv_user': {'default': 'user_a',
                                                                                        'enum': ['user_a',
                                                                                                 'user_b'],
                                                                                        'type': 'string'},
                                                                  'token': {'description': 'Primary '
                                                                                           'user '
                                                                                           'bearer '
                                                                                           'token.',
                                                                            'type': 'string'}},
                                                   'type': 'object'},
                                          'base_url': {'description': "Override spec's "
                                                                      '`servers[0].url`. '
                                                                      'Use when the '
                                                                      'spec is hosted '
                                                                      'separately from '
                                                                      'the API.',
                                                       'type': 'string'},
                                          'categories': {'description': 'Rules to run. '
                                                                        'Default: '
                                                                        'headers + '
                                                                        'broken_auth + '
                                                                        'bola + '
                                                                        'function_authz '
                                                                        '(mass_assignment '
                                                                        'excluded — it '
                                                                        'mutates '
                                                                        'server state, '
                                                                        'opt in '
                                                                        'explicitly).',
                                                         'items': {'enum': ['headers',
                                                                            'broken_auth',
                                                                            'bola',
                                                                            'function_authz',
                                                                            'mass_assignment'],
                                                                   'type': 'string'},
                                                         'type': 'array'},
                                          'plan_id': {'description': 'v0.9.4 — '
                                                                     'Optional. '
                                                                     'plan_id returned '
                                                                     'by qa_plan. When '
                                                                     'supplied, the '
                                                                     'scan '
                                                                     'auto-verifies '
                                                                     'its findings '
                                                                     'against the '
                                                                     "plan's critical "
                                                                     'points and adds '
                                                                     'a '
                                                                     '`plan_verification` '
                                                                     'block to the '
                                                                     'response (per-CP '
                                                                     'checklist + '
                                                                     'overall '
                                                                     'passed/incomplete/failed '
                                                                     'status). Only '
                                                                     'findings ABOVE '
                                                                     'severity_threshold '
                                                                     'are seen by the '
                                                                     'verifier — if a '
                                                                     'CP targets a '
                                                                     'low-severity '
                                                                     'finding, lower '
                                                                     'the threshold to '
                                                                     "'low' or 'info' "
                                                                     'accordingly.',
                                                      'type': 'string'},
                                          'severity_threshold': {'default': 'medium',
                                                                 'description': 'Minimum '
                                                                                'severity '
                                                                                'to '
                                                                                'include '
                                                                                'in '
                                                                                '`findings`. '
                                                                                'Lower-severity '
                                                                                'findings '
                                                                                'counted '
                                                                                'in '
                                                                                '`findings_below_threshold_count`.',
                                                                 'enum': ['critical',
                                                                          'high',
                                                                          'medium',
                                                                          'low',
                                                                          'info'],
                                                                 'type': 'string'},
                                          'spec_url': {'description': 'OpenAPI 3.x URL '
                                                                      '(http:// or '
                                                                      'https://) or '
                                                                      'local path '
                                                                      '(file:// or '
                                                                      'bare). YAML and '
                                                                      'JSON both '
                                                                      'accepted.',
                                                       'type': 'string'},
                                          'timeout_s': {'default': 30,
                                                        'description': 'Per-request '
                                                                       'timeout. '
                                                                       'Default 30s.',
                                                        'type': 'integer'}},
                           'required': ['spec_url'],
                           'type': 'object'},
 'qa_plan': {'properties': {'critical_points': {'description': 'Required, non-empty. '
                                                               'Each entry is either a '
                                                               'string (used as '
                                                               'description+verification_hint) '
                                                               'or a dict {id?, '
                                                               'description, '
                                                               'verification_hint?}. '
                                                               'IDs auto-assigned as '
                                                               'CP1..CPn if omitted. '
                                                               'verification_hint '
                                                               'defaults to '
                                                               'description — pick a '
                                                               'substring that will '
                                                               'literally appear in '
                                                               "the evidence you'll "
                                                               'later pass.',
                                                'items': {'oneOf': [{'type': 'string'},
                                                                    {'properties': {'assert': {'description': 'v0.9.6 '
                                                                                                              '— '
                                                                                                              'makes '
                                                                                                              'this '
                                                                                                              'CP '
                                                                                                              "'verified' "
                                                                                                              '(artifact-backed) '
                                                                                                              'instead '
                                                                                                              'of '
                                                                                                              "'attested' "
                                                                                                              '(hint '
                                                                                                              'substring). '
                                                                                                              'verify_plan '
                                                                                                              'loads '
                                                                                                              'the '
                                                                                                              'authoritative '
                                                                                                              'artifact '
                                                                                                              'itself '
                                                                                                              'and '
                                                                                                              'IGNORES '
                                                                                                              'host-supplied '
                                                                                                              'evidence '
                                                                                                              'for '
                                                                                                              'this '
                                                                                                              'CP, '
                                                                                                              'so '
                                                                                                              'it '
                                                                                                              "can't "
                                                                                                              'be '
                                                                                                              'faked. '
                                                                                                              'Types: '
                                                                                                              'test_passed{test_id, '
                                                                                                              'match?}, '
                                                                                                              'test_outcome{test_id, '
                                                                                                              'expected}, '
                                                                                                              'finding_present{rule_id, '
                                                                                                              'endpoint?}, '
                                                                                                              'finding_absent{rule_id, '
                                                                                                              'endpoint?}. '
                                                                                                              'test_id '
                                                                                                              'defaults '
                                                                                                              'to '
                                                                                                              'exact-or-suffix '
                                                                                                              'match '
                                                                                                              '(incl. '
                                                                                                              'parametrized '
                                                                                                              'variants); '
                                                                                                              'set '
                                                                                                              "match:'substring' "
                                                                                                              'to '
                                                                                                              'opt '
                                                                                                              'into '
                                                                                                              'loose '
                                                                                                              'matching.',
                                                                                               'properties': {'type': {'enum': ['test_passed',
                                                                                                                                'test_outcome',
                                                                                                                                'finding_present',
                                                                                                                                'finding_absent'],
                                                                                                                       'type': 'string'}},
                                                                                               'required': ['type'],
                                                                                               'type': 'object'},
                                                                                    'description': {'type': 'string'},
                                                                                    'id': {'type': 'string'},
                                                                                    'verification_hint': {'type': 'string'}},
                                                                     'required': ['description'],
                                                                     'type': 'object'}]},
                                                'minItems': 1,
                                                'type': 'array'},
                            'kind': {'description': 'Optional. Hint for downstream '
                                                    'verifiers about which evidence '
                                                    'stream to expect. Omit if unsure.',
                                     'enum': ['run',
                                              'generate',
                                              'scan',
                                              'debug',
                                              'captcha'],
                                     'type': 'string'},
                            'strict': {'default': False,
                                       'description': 'v0.9.6 — Declared at creation '
                                                      'and fixed. When true, '
                                                      'verify_plan only returns '
                                                      "'passed' if EVERY CP is "
                                                      'verified-tier (artifact-backed) '
                                                      'AND satisfied — attested '
                                                      '(hint-only) CPs never suffice. '
                                                      "verify_plan's own strict arg "
                                                      'can only tighten this, never '
                                                      'loosen it.',
                                       'type': 'boolean'},
                            'task': {'description': 'Required. The natural-language '
                                                    'goal — what the user wants done. '
                                                    'Will be echoed back in '
                                                    "verify_plan's output.",
                                     'type': 'string'}},
             'required': ['task', 'critical_points'],
             'type': 'object'},
 'verify_plan': {'properties': {'auto_discover': {'default': False,
                                                  'description': 'v0.9.2 — When true, '
                                                                 "read the project's "
                                                                 'pytest-json-report '
                                                                 'and add its `tests` '
                                                                 'array to the '
                                                                 'evidence stream. '
                                                                 'Useful for verifying '
                                                                 'a CP set against the '
                                                                 'most recent test run '
                                                                 'without manually '
                                                                 'copying report rows '
                                                                 'into the call.',
                                                  'type': 'boolean'},
                                'evidence': {'description': 'Optional when '
                                                            '`auto_discover: true`. '
                                                            'Each item is searched for '
                                                            "each CP's "
                                                            'verification_hint. Pass '
                                                            'structured payloads — '
                                                            'test result rows from '
                                                            '`get_test_report`, scan '
                                                            'findings from '
                                                            '`run_api_security_scan`, '
                                                            'log lines, screenshot '
                                                            'paths, etc.',
                                             'items': {},
                                             'type': 'array'},
                                'plan_id': {'description': 'Required. The plan_id '
                                                           'returned by qa_plan.',
                                            'type': 'string'},
                                'report_path': {'description': 'v0.9.2 — Override the '
                                                               'report.json location '
                                                               'when auto_discover is '
                                                               'true. Defaults to '
                                                               '`GOMORE_QA_REPORT_PATH` '
                                                               'env, then '
                                                               '`<QA_PROJECT_ROOT>/report.json`, '
                                                               'then `./report.json`. '
                                                               'Also used to resolve '
                                                               'the artifact for '
                                                               'verified-tier '
                                                               '`test_passed`/`test_outcome` '
                                                               'CPs.',
                                                'type': 'string'},
                                'strict': {'default': False,
                                           'description': 'v0.9.6 — Tighten '
                                                          'verification for this call: '
                                                          'require every CP to be '
                                                          'verified-tier AND satisfied '
                                                          "for 'passed'. Can only "
                                                          'tighten — if the plan was '
                                                          'created strict, passing '
                                                          'false here does NOT loosen '
                                                          'it.',
                                           'type': 'boolean'}},
                 'required': ['plan_id'],
                 'type': 'object'}}
