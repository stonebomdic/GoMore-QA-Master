# GoMore QA Master Fork/Rebrand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fork 上游 `mk-qa-master` 為公司內部專案 **GoMore QA Master**：全面 rebrand 運作/對外面字樣與 logo、移除 CAPTCHA 解題模組、改為內部 Git 散佈。

**Architecture:** 原地 `git mv` 保留歷史 + 規則式字串替換。每個 task 結束時 repo 維持「可安裝、pytest 綠」狀態。CAPTCHA 移除先做（仍是舊名），再做改名，最後字串 sweep 與發佈面清理。

**Tech Stack:** Python 3.10+（venv 在 `.venv/`，pytest / hatchling / MCP）。執行環境注意：本機 editable `.pth` 可能不生效，必要時用 `PYTHONPATH=$PWD/src` 或重跑 `pip install -e .`。

**設計來源：** `docs/superpowers/specs/2026-06-01-rebrand-gomore-qa-master-design.md`

**命名對照：** `mk_qa_master`→`gomore_qa_master`；`mk-qa-master`→`gomore-qa-master`；顯示 `MK QA Master`/`mk-qa-master`→`GoMore QA Master`。

**刻意保留（非 CAPTCHA 解題器，勿動）：** `src/.../tools/qa_context.py` 的「驗證碼 (CAPTCHA) 測試策略」防禦性知識段落（僅 rebrand 其中 `mk-qa-master` 字樣）；`src/.../tools/qa_plan.py` 的 `captcha` kind enum。

**不動：** `docs/**`（歷史 PRD / walkthrough / postmortem / POC 報告）。

---

### Task 1: 前置 — 保護 apk、確認綠底

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: 把 test_app/ 加入 .gitignore（避免 209MB apk 誤入版控）**

在 `.gitignore` 末尾加一行：
```
test_app/
```

- [ ] **Step 2: 確認在 feature 分支**

Run: `git branch --show-current`
Expected: `feature/rebrand-gomore-qa-master`

- [ ] **Step 3: 確保可安裝 + 基準綠底**

Run:
```bash
.venv/bin/pip install -e '.[api]' -q
.venv/bin/pytest tests/ -q
```
Expected: 全 pass（基準 283 passed 等），確認改動前是綠的。

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore test_app/ 並確立 rebrand 基準"
```

---

### Task 2: 移除 CAPTCHA 解題器工具面（server / tools 匯出 / config / 模組檔）

**Files:**
- Modify: `src/mk_qa_master/server.py`
- Modify: `src/mk_qa_master/tools/__init__.py`
- Modify: `src/mk_qa_master/config.py`
- Modify: `tests/test_smoke.py`
- Delete: `src/mk_qa_master/tools/visual_challenge.py`, `src/mk_qa_master/tools/visual_challenge_driver.py`
- Delete: `tests/test_visual_challenge.py`

- [ ] **Step 1: 更新 test_smoke.py 斷言（先改測試，TDD 風格）**

在 `tests/test_smoke.py`：
- 刪除 `EXPECTED_TOOLS` 清單中的兩行：`"inspect_visual_challenge",` 與 `"solve_visual_challenge",`（約 line 35-36）。
- 把 `test_list_tools_count_matches_advertised_21` 改名為 `test_list_tools_count_matches_advertised_19`，斷言 `len(declared) == 19`（約 line 65-75），docstring 內 `21 tools` 改 `19 tools`。
- 刪除整個 `test_visual_challenge_tools_registered` 測試函式（約 line 78 到該函式結束）。

- [ ] **Step 2: 跑測試確認「現在」失敗（因 server 仍註冊 21 個）**

Run: `.venv/bin/pytest tests/test_smoke.py -q`
Expected: FAIL —— count 斷言期待 19、實際 21。

- [ ] **Step 3: 從 server.py 移除 CAPTCHA 工具**

在 `src/mk_qa_master/server.py`：
- import 行（約 line 9）`from .tools import runner, reporter, generator, analyzer, telemetry, optimizer, qa_context, visual_challenge` → 移除結尾 `, visual_challenge`。
- `from mcp.types import Tool, TextContent, ImageContent, Resource`（約 line 6）→ 移除 `ImageContent,`（移除 captcha 後不再使用）。
- 移除 `list_tools()` 中 `name="inspect_visual_challenge"` 與 `name="solve_visual_challenge"` 兩個完整 `Tool(...)` 區塊。
- 移除 `_dispatch()` 中 `if name == "inspect_visual_challenge":` 與 `if name == "solve_visual_challenge":` 兩個完整分支（含其 `asyncio.to_thread(visual_challenge...)` 與 ImageContent 組裝）。

- [ ] **Step 4: 從 tools/__init__.py 移除 visual_challenge**

`src/mk_qa_master/tools/__init__.py`：從 import 與 `__all__` 兩處移除 `visual_challenge`。改為：
```python
from . import runner, reporter, generator, analyzer, telemetry, optimizer, qa_context

__all__ = [
    "runner", "reporter", "generator", "analyzer",
    "telemetry", "optimizer", "qa_context",
]
```

- [ ] **Step 5: 從 config.py 移除 CAPTCHA 區段**

`src/mk_qa_master/config.py`：刪除 `# ---- AI Visual Challenge Solver (v0.7.0) ----` 整段（含 `QA_VISUAL_CHALLENGE_CONSENT` / `QA_VISUAL_CHALLENGE_TIMEOUT` / `QA_VISUAL_CHALLENGE_AUTHORIZED_DOMAINS` 與其註解，約 line 113-151）。其餘設定保留。

- [ ] **Step 6: 刪除 CAPTCHA 模組與其測試**

```bash
git rm src/mk_qa_master/tools/visual_challenge.py \
       src/mk_qa_master/tools/visual_challenge_driver.py \
       tests/test_visual_challenge.py
```

- [ ] **Step 7: 跑測試確認綠 + server 列 19 工具**

Run:
```bash
PYTHONPATH=$PWD/src .venv/bin/python -c "import asyncio; from mk_qa_master.server import list_tools; t=asyncio.run(list_tools()); print(len(t)); assert len(t)==19; assert not any('visual_challenge' in x.name for x in t)"
PYTHONPATH=$PWD/src .venv/bin/pytest tests/ -q
```
Expected: 印出 `19`；pytest 全 pass。

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: 移除 CAPTCHA 解題器工具面（21→19 tools）"
```

---

### Task 3: 移除 CAPTCHA 資產（CI jobs / scripts / fixtures）+ pyproject 描述清理

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `pyproject.toml`
- Delete: `.github/workflows/dogfood-real-recaptcha.yml`
- Delete: `scripts/dogfood-recaptcha.py`, `scripts/dogfood-inspect-only.py`, `examples/closed_loop_solver.py`
- Delete: `examples/sample_captcha_fixture/`, `examples/sample_hcaptcha_fixture/`, `examples/sample_captcha_mobile_app/`

- [ ] **Step 1: 刪除 CAPTCHA CI / scripts / fixtures**

```bash
git rm .github/workflows/dogfood-real-recaptcha.yml \
       scripts/dogfood-recaptcha.py scripts/dogfood-inspect-only.py \
       examples/closed_loop_solver.py
git rm -r examples/sample_captcha_fixture examples/sample_hcaptcha_fixture examples/sample_captcha_mobile_app
```

- [ ] **Step 2: 從 ci.yml 移除 captcha jobs**

`.github/workflows/ci.yml`：刪除 `api-captcha:`（約 line 161）與 `api-hcaptcha:`（約 line 196）兩個 job 區塊（各到下一個 job 或檔尾）。

- [ ] **Step 3: 清理 pyproject 描述/關鍵字**

`pyproject.toml`：
- `description` 移除 `the v0.7 AI Visual Challenge Solver (reCAPTCHA / hCaptcha),` 片段。
- `keywords` 移除與 captcha 相關項（無則略）。

- [ ] **Step 4: 驗證殘留 + 綠底**

Run:
```bash
find . -path ./.git -prune -o -path './docs/*' -prune -o -iname '*captcha*' -print
PYTHONPATH=$PWD/src .venv/bin/pytest tests/ -q
```
Expected: 第一行**無輸出**（docs 歷史 PRD 除外）；pytest 全 pass。

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: 移除 CAPTCHA CI/scripts/fixtures 與 pyproject 描述"
```

---

### Task 4: 改名 Python 套件 `mk_qa_master` → `gomore_qa_master`（含 pyproject、imports、重裝）

**Files:**
- Move: `src/mk_qa_master/` → `src/gomore_qa_master/`
- Modify: `pyproject.toml`（name / scripts / packages / urls）
- Modify: 套件內所有 `mk_qa_master` import 與 `Server("mk-qa-master")`
- Modify: `tests/**`（import + 字串）

- [ ] **Step 1: git mv 套件目錄**

```bash
git mv src/mk_qa_master src/gomore_qa_master
```

- [ ] **Step 2: 替換套件內與測試內的全部品牌形式（底線 / kebab / 顯示名）**

```bash
grep -rIl -E 'mk[-_]qa[-_]master|MK QA Master' src/gomore_qa_master tests \
  | xargs sed -i '' -e 's/mk_qa_master/gomore_qa_master/g' \
                    -e 's/mk-qa-master/gomore-qa-master/g' \
                    -e 's/MK QA Master/GoMore QA Master/g'
```
（macOS `sed -i ''`；Linux 用 `sed -i`。此處一次涵蓋 import 的 `mk_qa_master`、`Server("mk-qa-master")`、reporters/html.py 報告標題 `MK QA Master` 等全部形式。）

- [ ] **Step 3: 確認 server 名稱字串已換**

Run: `grep -n 'Server(' src/gomore_qa_master/server.py`
Expected: 顯示 `Server("gomore-qa-master")`。

- [ ] **Step 4: 更新 pyproject.toml 套件設定**

`pyproject.toml`：
- `name = "mk-qa-master"` → `name = "gomore-qa-master"`
- `[project.scripts]` `mk-qa-master = "mk_qa_master.server:run"` → `gomore-qa-master = "gomore_qa_master.server:run"`
- `[tool.hatch.build.targets.wheel] packages = ["src/mk_qa_master"]` → `["src/gomore_qa_master"]`
- `[project.urls]` Homepage/Repository → 內部 git placeholder：
  ```toml
  Repository = "git+ssh://git@<INTERNAL_GIT_HOST>/gomore-qa-master.git"
  ```
  （`<INTERNAL_GIT_HOST>` 由團隊填實際值。）

- [ ] **Step 5: 重裝 + 綠底 + console script**

Run:
```bash
.venv/bin/pip install -e '.[api]' -q
.venv/bin/pytest tests/ -q
.venv/bin/python -c "import gomore_qa_master; print('import OK')"
PYTHONPATH=$PWD/src .venv/bin/python -c "import asyncio; from gomore_qa_master.server import list_tools; print(len(asyncio.run(list_tools())))"
```
Expected: pytest 全 pass；`import OK`；印出 `19`。

- [ ] **Step 6: 確認舊模組名零殘留**

Run: `grep -rIn 'mk_qa_master' src/ tests/ pyproject.toml`
Expected: **無輸出**。

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: 套件改名 mk_qa_master → gomore_qa_master"
```

---

### Task 5: 改名 skill 目錄 + rebrand skill 內容 + manifests

**Files:**
- Move: `skills/mk-qa-master/` → `skills/gomore-qa-master/`
- Modify: `skills/gomore-qa-master/**`、`.claude-plugin/plugin.json`、`.codex-plugin/plugin.json`
- Modify: `tests/test_skill_distribution.py`（skill 路徑）

- [ ] **Step 1: git mv skill 目錄**

```bash
git mv skills/mk-qa-master skills/gomore-qa-master
```

- [ ] **Step 2: 替換 skill 內容、manifests、skill 分發測試的字樣/路徑**

```bash
grep -rIl -E 'mk[-_]qa[-_]master|MK QA Master' skills/gomore-qa-master .claude-plugin .codex-plugin tests/test_skill_distribution.py \
  | xargs sed -i '' -e 's/mk_qa_master/gomore_qa_master/g' -e 's/mk-qa-master/gomore-qa-master/g' -e 's/MK QA Master/GoMore QA Master/g'
```

- [ ] **Step 3: 移除 skill 內 CAPTCHA 工具描述（保留防禦性 captcha 知識引用）**

檢查 `skills/gomore-qa-master/SKILL.md`、`reference/tool-surface.md`、`reference/workflow.md`，移除對 `inspect_visual_challenge` / `solve_visual_challenge` 工具的描述（若 tool 清單寫死數量，改 19）。**不要**移除「測試中如何繞過 captcha」這類方法論敘述。

- [ ] **Step 4: 綠底（skill 分發測試對齊新路徑）**

Run: `PYTHONPATH=$PWD/src .venv/bin/pytest tests/test_skill_distribution.py -q`
Expected: 全 pass（manifest commands 路徑、skill 目錄解析皆指向 `gomore-qa-master`）。

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: skill 目錄與 manifests 改名 gomore-qa-master"
```

---

### Task 6: 字串 sweep — Dockerfile / README / examples / 樣板 / 其他根設定

**Files:**
- Modify: `Dockerfile`、`README.md`、`README.zh-TW.md`、`sample_report.html`
- Modify: `examples/**`（排除已刪的 captcha fixtures）
- Modify: `.github/ISSUE_TEMPLATE/*`、`.github/pull_request_template.md`、`.github/workflows/ci.yml`
- Modify: `.gitguardian.yaml`、`.gitguardian.yml`

- [ ] **Step 1: 規則式替換（運作/對外面，排除 docs）**

```bash
grep -rIl -E 'mk[-_]qa[-_]master|MK QA Master' \
  Dockerfile README.md README.zh-TW.md sample_report.html examples \
  .github .gitguardian.yaml .gitguardian.yml \
  | xargs sed -i '' -e 's/mk_qa_master/gomore_qa_master/g' -e 's/mk-qa-master/gomore-qa-master/g' -e 's/MK QA Master/GoMore QA Master/g'
```

- [ ] **Step 2: README 安裝指引改內部 Git**

`README.md` / `README.zh-TW.md`：把 `pip install mk-qa-master` / PyPI 安裝段，改為
`pip install git+ssh://git@<INTERNAL_GIT_HOST>/gomore-qa-master.git`。

- [ ] **Step 3: 驗證運作面零殘留**

Run:
```bash
grep -rIn -E 'mk[-_]qa[-_]master|MK QA Master' \
  src/ skills/ tests/ examples/ pyproject.toml Dockerfile README.md README.zh-TW.md \
  .claude-plugin .codex-plugin examples/configs sample_report.html .github
```
Expected: **無輸出**。

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor: rebrand 字樣 sweep（Dockerfile/README/examples/templates）"
```

---

### Task 7: 移除公開發佈管道

**Files:**
- Delete: `.github/workflows/publish.yml`、`smithery.yaml`

- [ ] **Step 1: 刪除發佈檔**

```bash
git rm .github/workflows/publish.yml smithery.yaml
```

- [ ] **Step 2: 確認 CI 仍可解析（無語法殘缺）**

Run: `.venv/bin/python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo OK`
Expected: `OK`。

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: 移除公開發佈管道（publish.yml / smithery.yaml）"
```

---

### Task 8: Logo placeholder

**Files:**
- Replace: `assets/logo.png`
- Modify: README logo 引用旁加 TODO 註解

- [ ] **Step 1: 產生文字版 placeholder logo（維持檔名 logo.png）**

```bash
PYTHONPATH=$PWD/src .venv/bin/python - <<'PY'
from PIL import Image, ImageDraw
img = Image.new("RGB", (800, 240), "#0f172a")
d = ImageDraw.Draw(img)
d.text((40, 100), "GoMore QA Master  (logo placeholder)", fill="#e2e8f0")
img.save("assets/logo.png")
print("placeholder logo written")
PY
```
（若無 Pillow：`.venv/bin/pip install pillow -q` 後再跑。）

- [ ] **Step 2: README 加 TODO 註解**

在 `README.md` / `README.zh-TW.md` 的 logo 圖片引用上方加：
```markdown
<!-- TODO: 換成正式 GoMore logo -->
```

- [ ] **Step 3: Commit**

```bash
git add assets/logo.png README.md README.zh-TW.md
git commit -m "chore: logo 換為文字版 placeholder（待正式圖）"
```

---

### Task 9: 授權合規 — 保留原 LICENSE + 新增 NOTICE

**Files:**
- Keep: `LICENSE`（不改原作者著作權）
- Create: `NOTICE`

- [ ] **Step 1: 確認 LICENSE 仍含原作者著作權**

Run: `grep -i 'copyright' LICENSE`
Expected: 仍有原作者 (Jack Kao) 著作權行——**不得移除**。

- [ ] **Step 2: 新增 NOTICE**

建立 `NOTICE`：
```
GoMore QA Master

本專案 fork 自上游開源專案 mk-qa-master（MIT License，原作者 Jack Kao）。
原始授權見 LICENSE。本 fork 由 GoMore 內部維護與使用，並已：
- rebrand 為 GoMore QA Master
- 移除 CAPTCHA 解題模組
- 改為公司內部 Git 散佈
```

- [ ] **Step 3: Commit**

```bash
git add NOTICE
git commit -m "docs: 新增 NOTICE 標註 fork 來源與 MIT 授權保留"
```

---

### Task 10: 最終驗收 Gate

**Files:** 無（純驗證）

- [ ] **Step 1: 運作面零殘留**

Run:
```bash
grep -rIn -E 'mk[-_]qa[-_]master|MK QA Master' \
  src/ skills/ tests/ examples/ pyproject.toml Dockerfile README.md README.zh-TW.md \
  .claude-plugin .codex-plugin .github 2>/dev/null
```
Expected: **無輸出**。

- [ ] **Step 2: CAPTCHA 解題器零殘留**

Run:
```bash
find . -path ./.git -prune -o -path './docs/*' -prune -o \( -iname '*captcha*' -o -iname '*visual_challenge*' \) -print
```
Expected: **無輸出**（docs 歷史 PRD 除外）。

- [ ] **Step 3: 全新安裝 + 綠底 + 19 工具 + console script**

Run:
```bash
.venv/bin/pip install -e '.[api]' -q
.venv/bin/pytest tests/ -q
PYTHONPATH=$PWD/src .venv/bin/python -c "import asyncio; from gomore_qa_master.server import list_tools; t=asyncio.run(list_tools()); print('tools:', len(t)); assert len(t)==19"
QA_PROJECT_ROOT=$PWD/tests_project QA_RUNNER=pytest .venv/bin/gomore-qa-master </dev/null >/tmp/boot.log 2>&1 & sleep 2; kill %1 2>/dev/null; echo "console script boots OK"
```
Expected: pytest 全 pass；`tools: 19`；console script 啟動無錯。

- [ ] **Step 4: 確認 LICENSE 原著作權仍在**

Run: `grep -i 'copyright' LICENSE`
Expected: 原作者著作權仍在。

- [ ] **Step 5: 收尾 commit（如有殘留調整）+ 回報**

```bash
git status -s
git log --oneline -12
```
Expected：工作目錄乾淨（POC 文件與 test_app/ 除外，屬未追蹤）；commit 歷史涵蓋 Task 1-9。

---

## 完成後

- 分支 `feature/rebrand-gomore-qa-master` 即內部 fork 起點，可推到內部 Git。
- 待團隊提供正式 logo → 替換 `assets/logo.png` placeholder。
- 待團隊填 `<INTERNAL_GIT_HOST>` 實際值（pyproject urls + README 安裝指引）。
