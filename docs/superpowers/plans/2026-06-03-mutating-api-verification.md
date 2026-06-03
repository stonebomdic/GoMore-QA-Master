# Mutating Method API 驗證 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 對 `gwp-backend-qa.gomore.net` 的 145 個 POST/PUT/PATCH operations 做四面向驗證（穩定性、合約、CRUD、mass_assignment），DELETE 與副作用端點黑名單，低權限可丟棄帳號授權圍欄。

**Architecture:** 混合型計畫。少數可本機 TDD 的 helper（scope 產生器、CRUD readback 比對）放在 `scripts/` + `tests/`；其餘為操作型 runbook 步驟，直呼 schemathesis CLI（M0/M1）與 `api_security.run_scan`（M3），以真實輸出驗證，**無法單元測試**——這類步驟明確標註「需 token + 後端放行」。

**Tech Stack:** schemathesis 3.39 CLI、`gomore_qa_master.runners.api_security`、pytest、Python 3.11（venv）。

**設計來源：** [`docs/superpowers/specs/2026-06-03-mutating-api-verification-design.md`](../specs/2026-06-03-mutating-api-verification-design.md)

---

## 檔案結構

| 檔案 | 責任 |
|---|---|
| `scripts/mutating_scope.py`（新增） | 純函式：由 OpenAPI spec 切出 runnable / no_body_schema / blacklisted 三桶（DELETE 一律排除） |
| `tests/test_mutating_scope.py`（新增） | `mutating_scope.build_scope` 的單元測試（fixture spec） |
| `scripts/crud_readback.py`（新增） | 純函式：比對 CRUD 寫入值 vs 讀回值，回不符欄位清單 |
| `tests/test_crud_readback.py`（新增） | `crud_readback.diff_readback` 的單元測試 |
| `docs/poc-mutating/`（新增目錄） | 操作產出物：scope 清單、各階段 log、CRUD 序列定義 |
| `docs/sqa-poc-plan.zh-TW.md`（修改） | 新增「Phase 2.5（mutating）」+「附錄 G」 |
| `docs/poc-summary-report.zh-TW.md`（修改） | 更新「2. 評估方法」與 rubric |
| `docs/qa-bug-tickets-gwp-backend-qa.md`（修改） | 併入 mutating 發現的 bug |

---

## Phase 0 — 可 TDD 的 helper（本機、無需 token）

### Task 1: scope 產生器 `scripts/mutating_scope.py`

**Files:**
- Create: `scripts/mutating_scope.py`
- Test: `tests/test_mutating_scope.py`

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_mutating_scope.py
from scripts.mutating_scope import build_scope


def _op_with_body():
    return {"requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}}}


def test_build_scope_splits_three_buckets_and_drops_delete():
    spec = {
        "paths": {
            "/mood-diary": {"post": _op_with_body()},
            "/auth/email/login": {"post": _op_with_body()},
            "/iap/purchase": {"post": _op_with_body()},
            "/ping": {"post": {}},                       # 無 requestBody schema
            "/widgets/{id}": {"delete": {}, "get": {}},  # DELETE / GET 不入桶
        }
    }
    scope = build_scope(spec, blacklist_patterns=[r"^/auth/", r"^/iap/"])

    assert {"method": "POST", "path": "/mood-diary"} in scope["runnable"]
    assert {"method": "POST", "path": "/auth/email/login"} in scope["blacklisted"]
    assert {"method": "POST", "path": "/iap/purchase"} in scope["blacklisted"]
    assert {"method": "POST", "path": "/ping"} in scope["no_body_schema"]

    flat = scope["runnable"] + scope["no_body_schema"] + scope["blacklisted"]
    assert all(e["method"] in {"POST", "PUT", "PATCH"} for e in flat)  # 無 DELETE/GET
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `PYTHONPATH=$PWD .venv/bin/pytest tests/test_mutating_scope.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.mutating_scope'`

- [ ] **Step 3: 寫最小實作**

```python
# scripts/mutating_scope.py
"""由 OpenAPI spec 切出 mutating 測試範圍。

POST/PUT/PATCH 分三桶：
  runnable        — 有 requestBody schema 且不在黑名單
  no_body_schema  — mutating 但無 requestBody schema（fuzz 價值低）
  blacklisted     — path 命中副作用黑名單
DELETE 一律不入任何桶。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

MUTATING_METHODS = ("post", "put", "patch")


def load_spec(path: str) -> dict:
    text = Path(path).read_text()
    if path.endswith((".yaml", ".yml")):
        import yaml
        return yaml.safe_load(text)
    return json.loads(text)


def _has_request_body(op: dict) -> bool:
    content = ((op or {}).get("requestBody") or {}).get("content") or {}
    return any("schema" in (c or {}) for c in content.values())


def build_scope(spec: dict, blacklist_patterns: list[str]) -> dict:
    compiled = [re.compile(p) for p in blacklist_patterns]
    runnable, no_body, blacklisted = [], [], []
    for path, item in (spec.get("paths") or {}).items():
        for method, op in (item or {}).items():
            if method.lower() not in MUTATING_METHODS:
                continue
            entry = {"method": method.upper(), "path": path}
            if any(c.search(path) for c in compiled):
                blacklisted.append(entry)
            elif _has_request_body(op):
                runnable.append(entry)
            else:
                no_body.append(entry)
    return {"runnable": runnable, "no_body_schema": no_body, "blacklisted": blacklisted}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `PYTHONPATH=$PWD .venv/bin/pytest tests/test_mutating_scope.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/mutating_scope.py tests/test_mutating_scope.py
git commit -m "feat: mutating scope 產生器 (POST/PUT/PATCH 三桶切分)"
```

---

### Task 2: CRUD readback 比對 `scripts/crud_readback.py`

**Files:**
- Create: `scripts/crud_readback.py`
- Test: `tests/test_crud_readback.py`

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_crud_readback.py
from scripts.crud_readback import diff_readback


def test_diff_readback_reports_only_written_keys():
    written = {"title": "A", "score": 5}
    readback = {"title": "A", "score": 9, "id": 1, "createdAt": "..."}  # server 加欄位不算
    assert diff_readback(written, readback) == ["score"]


def test_diff_readback_all_match_returns_empty():
    assert diff_readback({"title": "A"}, {"title": "A", "id": 1}) == []
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `PYTHONPATH=$PWD .venv/bin/pytest tests/test_crud_readback.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.crud_readback'`

- [ ] **Step 3: 寫最小實作**

```python
# scripts/crud_readback.py
"""比對 CRUD 寫入值與讀回值。"""
from __future__ import annotations


def diff_readback(written: dict, readback: dict) -> list[str]:
    """回傳「寫入值與讀回值不符」的 key 清單。

    只檢查 written 內的 key（server 自行新增的欄位不算不符）。
    """
    return [k for k, v in written.items() if readback.get(k) != v]
```

- [ ] **Step 4: 跑測試確認通過**

Run: `PYTHONPATH=$PWD .venv/bin/pytest tests/test_crud_readback.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/crud_readback.py tests/test_crud_readback.py
git commit -m "feat: CRUD readback 比對 helper"
```

---

## Phase 1 — M0 盤點（操作型；需 spec，不需 token）

### Task 3: 產出 scope 三桶清單 + 確認黑名單

**Files:**
- Create: `docs/poc-mutating/scope.json`
- Create: `docs/poc-mutating/blacklist.txt`（每行一個 regex）

- [ ] **Step 1: 取得 spec 快照**

Run:
```bash
mkdir -p docs/poc-mutating
curl -s https://gwp-backend-qa.gomore.net/api-json -o docs/poc-mutating/spec.json
.venv/bin/python -c "import json;d=json.load(open('docs/poc-mutating/spec.json'));print(len(d.get('paths',{})),'paths')"
```
Expected: 印出 `265 paths`（與附錄 B 一致；數字不同代表 spec 已更新，記錄之）

- [ ] **Step 2: 定義黑名單 regex**

寫入 `docs/poc-mutating/blacklist.txt`（依設計 §2，M0 確認實際 path 前綴）：
```
^/auth/
^/iap/
^/notification
deactivate
```

- [ ] **Step 3: 產生三桶清單**

Run:
```bash
PYTHONPATH=$PWD .venv/bin/python -c "
import json
from scripts.mutating_scope import load_spec, build_scope
spec = load_spec('docs/poc-mutating/spec.json')
pats = [l.strip() for l in open('docs/poc-mutating/blacklist.txt') if l.strip()]
scope = build_scope(spec, pats)
json.dump(scope, open('docs/poc-mutating/scope.json','w'), ensure_ascii=False, indent=2)
print('runnable', len(scope['runnable']))
print('no_body_schema', len(scope['no_body_schema']))
print('blacklisted', len(scope['blacklisted']))
"
```
Expected: 三個數字相加 ≈ 145（POST 96 + PUT 35 + PATCH 14，扣掉黑名單後 runnable + no_body + blacklisted = 145）。記錄實際分布。

- [ ] **Step 4: 人工覆核黑名單覆蓋**

開 `docs/poc-mutating/scope.json`，確認 `blacklisted` 桶涵蓋全部會寄信 / 付費 / 推播 / 自毀的端點；若 `runnable` 桶裡仍有漏網的副作用端點，補進 `blacklist.txt` 重跑 Step 3。

- [ ] **Step 5: Commit**

```bash
git add docs/poc-mutating/blacklist.txt docs/poc-mutating/scope.json
git commit -m "docs: M0 mutating scope 盤點 (三桶 + 副作用黑名單)"
```

> **Go/No-Go：** 黑名單已涵蓋全部副作用端點、runnable 規模合理 → 進 M1。`spec.json` 體積大，視需要加進 `.gitignore`。

---

## Phase 2 — M1 穩定性 + 合約 fuzz（操作型；**需 token + 後端放行**）

### Task 4: 環境前置與 token 健全性

- [ ] **Step 1: 確認前置條件（設計 §3）**

人工核對：
- 取得「已建置資料、可丟棄」測試帳號 token（**非空帳號**；空帳號會回 `404 ACCOUNT_NOT_FOUND`，見附錄 C）。
- 帳號重建 runbook 就緒。
- 已與後端對齊可在 QA mutate、選離峰時段。

- [ ] **Step 2: token 設為環境變數（不落地）**

Run:
```bash
read -rs TOKEN && export TOKEN
```

- [ ] **Step 3: token 健全性探測**

Run:
```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer $TOKEN" \
  https://gwp-backend-qa.gomore.net/account/profile
```
Expected: `200`（若 `404` → 帳號無資料，換帳號；若 `401` → token 失效，重取）

- [ ] **Step 4: 確認 schemathesis 過濾旗標名稱**

Run: `.venv/bin/schemathesis run --help | grep -E "include-method|exclude-path"`
Expected: 列出 `--include-method` 與 path 排除旗標。**若排除旗標名非 `--exclude-path-regex`，以此處輸出為準調整 Task 5 指令。**

### Task 5: 低 example 數 mutating fuzz

**Files:**
- Create: `docs/poc-mutating/m1-fuzz.log`

- [ ] **Step 1: 低汙染試跑（max-examples=1）**

Run:
```bash
mkdir -p docs/poc-mutating
.venv/bin/schemathesis run https://gwp-backend-qa.gomore.net/api-json \
  --base-url https://gwp-backend-qa.gomore.net \
  --include-method POST --include-method PUT --include-method PATCH \
  --exclude-path-regex '^/(auth|iap|notification)|deactivate' \
  -H "Authorization: Bearer $TOKEN" \
  --checks all \
  --hypothesis-max-examples 1 \
  2>&1 | tee docs/poc-mutating/m1-fuzz.log
```
Expected: 收集到 ~runnable 數量的 operations、開始送請求；觀察是否大量逾時 / 站台不穩。

- [ ] **Step 2: 評估站台穩定度與資料汙染**

檢視 `m1-fuzz.log`：若站台穩定、測試帳號未被打爆，再視需要把 `--hypothesis-max-examples` 調到 2~3 重跑。**不穩則停**，記錄並縮小範圍（用 `--include-path` 收斂）。

- [ ] **Step 3: 萃取 5xx 與 contract drift**

人工從 log 整理：
- `not_a_server_error` 失敗 → 5xx 清單（比照附錄 C-1 表格：端點 / 觸發輸入 / 訊息）。
- `response_schema_conformance` 失敗 → contract drift 清單（比照附錄 C-1）。

- [ ] **Step 4: Commit log**

```bash
git add docs/poc-mutating/m1-fuzz.log
git commit -m "docs: M1 mutating fuzz log (穩定性 + 合約)"
```

> **Go/No-Go：** 站台穩定、資料汙染可控才放大 example 數；否則停在低量並記錄限制。

---

## Phase 3 — M2 策劃式 CRUD（操作型；**需 token**）

### Task 6: 定義 CRUD 序列

**Files:**
- Create: `docs/poc-mutating/crud-families.json`

- [ ] **Step 1: 從 runnable 桶挑 5–10 個帳號自有資源族**

開 `docs/poc-mutating/scope.json`，挑出有完整 POST + GET（+ PUT/PATCH）的資源族（如 `mood-diary`、`activities/.../records`）。寫入 `crud-families.json`：
```json
[
  {
    "name": "mood-diary",
    "create": {"method": "POST", "path": "/mood-diary", "body": {"date": "2026-06-03", "mood": 3}},
    "read":   {"method": "GET",  "path": "/mood-diary/card/2026-06-03"},
    "update": {"method": "PATCH","path": "/mood-diary/2026-06-03", "body": {"mood": 5}},
    "verify_key": "mood"
  }
]
```
（實際 path/body 依 spec 填；DELETE 不放，收尾用帳號重置。）

- [ ] **Step 2: Commit 序列定義**

```bash
git add docs/poc-mutating/crud-families.json
git commit -m "docs: M2 CRUD 序列定義 (帳號自有資源族)"
```

### Task 7: 執行 CRUD 序列並比對讀回

**Files:**
- Create: `docs/poc-mutating/m2-crud.log`

- [ ] **Step 1: 逐族執行 create→read→update→read**

對 `crud-families.json` 每族，用 `curl`（帶 `-H "Authorization: Bearer $TOKEN"`）依序打 create / read / update / read，把每步狀態碼與回應記入 `m2-crud.log`。

- [ ] **Step 2: 比對寫入 vs 讀回**

對「update 後的讀回」用 Task 2 的 helper 驗證：
```bash
PYTHONPATH=$PWD .venv/bin/python -c "
from scripts.crud_readback import diff_readback
written={'mood':5}; readback={'mood':5,'id':1}   # 代入實際讀回 JSON
print('mismatch:', diff_readback(written, readback))
"
```
Expected: `mismatch: []`（不符則記為 CRUD 正確性 bug）

- [ ] **Step 3: 收尾還原**

依帳號重建 runbook 還原測試帳號狀態（或保留資料，記錄殘留）。

- [ ] **Step 4: Commit log**

```bash
git add docs/poc-mutating/m2-crud.log
git commit -m "docs: M2 CRUD 實跑結果 (讀回比對)"
```

> **Go/No-Go：** 序列機制可重複、結果可信 → 進 M3。

---

## Phase 4 — M3 mass_assignment 安全（操作型；**需 token + consent**）

### Task 8: 範圍化 mass_assignment 掃描

**Files:**
- Create: `docs/poc-mutating/m3-mass-assignment.json`

- [ ] **Step 1: 設定 consent + 授權網域**

Run:
```bash
export QA_API_SECURITY_CONSENT=true
export QA_API_SECURITY_AUTHORIZED_DOMAINS=gwp-backend-qa.gomore.net
```

- [ ] **Step 2: 只開 mass_assignment、只打自有資源**

Run（注意：`gomore_qa_master` 在 `src/` 底下、editable `.pth` 在此 venv 不生效——見附錄 A——故用 `PYTHONPATH=$PWD/src`）：
```bash
PYTHONPATH=$PWD/src .venv/bin/python -c "
import json
from gomore_qa_master.runners.api_security import run_scan
out = run_scan(
    spec_url='https://gwp-backend-qa.gomore.net/api-json',
    base_url='https://gwp-backend-qa.gomore.net',
    auth={'token': __import__('os').environ['TOKEN']},
    categories=['mass_assignment'],
)
json.dump(out, open('docs/poc-mutating/m3-mass-assignment.json','w'), ensure_ascii=False, indent=2)
print('findings', out.get('summary'))
"
```
Expected: 印出 findings summary；輸出落入 `m3-mass-assignment.json`。
（若回 `consent_required` → Step 1 環境變數未生效；若 `unauthorized_domain` → 網域變數未設。）

- [ ] **Step 3: 人工覆核 findings**

比照 BOLA 經驗（附錄 C-2，假陽性高）：每筆 finding 用手動請求覆核「是否真的寫入了不該寫的欄位」，標記真 / 假陽性。

- [ ] **Step 4: Commit**

```bash
git add docs/poc-mutating/m3-mass-assignment.json
git commit -m "docs: M3 mass_assignment 掃描結果 (範圍化自有資源)"
```

---

## Phase 5 — 文件彙整

### Task 9: sqa-poc-plan 新增 Phase 2.5 + 附錄 G

**Files:**
- Modify: `docs/sqa-poc-plan.zh-TW.md`

- [ ] **Step 1: 新增「Phase 2.5（mutating）」章節**

在 Phase 2 之後插入小節，摘述設計 §4 的 M0–M3 四階段與 Go/No-Go（連結到 spec）。

- [ ] **Step 2: 新增「附錄 G」實跑結果**

比照附錄 C 體例，整理 M1 的 5xx + drift 表、M2 的 CRUD 結果、M3 的 mass_assignment 覆核結論。

- [ ] **Step 3: Commit**

```bash
git add docs/sqa-poc-plan.zh-TW.md
git commit -m "docs: POC 計畫補 Phase 2.5 (mutating) + 附錄 G"
```

### Task 10: poc-summary-report 更新涵蓋與 rubric

**Files:**
- Modify: `docs/poc-summary-report.zh-TW.md`

- [ ] **Step 1: 更新「2. 評估方法」**

把「API 只跑 GET」改為「GET 唯讀 + POST/PUT/PATCH（DELETE 與副作用端點黑名單）」，並註明圍欄模型。

- [ ] **Step 2: 更新 rubric / 模組建議**

依 M1–M3 實際結果，調整「內部 API 功能驗證」「API 安全掃描可信度」分數與依據（補 mutating 證據）。

- [ ] **Step 3: Commit**

```bash
git add docs/poc-summary-report.zh-TW.md
git commit -m "docs: 總結報告補 mutating 涵蓋與 rubric"
```

### Task 11: 併入 bug 單

**Files:**
- Modify: `docs/qa-bug-tickets-gwp-backend-qa.md`

- [ ] **Step 1: 新增 mutating 發現的 bug**

把 M1 的 5xx / drift、M2 的 CRUD 不正確、M3 真陽性，整理成 Jira-ready bug 單（沿用既有格式）。

- [ ] **Step 2: Commit**

```bash
git add docs/qa-bug-tickets-gwp-backend-qa.md
git commit -m "docs: 併入 mutating 驗證發現的 bug 單"
```

---

## 全套驗證

- [ ] 全測試綠：`PYTHONPATH=$PWD .venv/bin/pytest tests/ -q`（既有 283 + 新增 helper 測試）
- [ ] `docs/poc-mutating/scope.json` 三桶相加 ≈ 145
- [ ] 四面向各有產出檔（m1-fuzz.log / m2-crud.log / m3-mass-assignment.json）或明確「為何未跑」
- [ ] QA 站未被破壞、測試帳號狀態可還原
