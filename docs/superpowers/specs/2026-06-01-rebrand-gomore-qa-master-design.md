# 設計：Fork 內部化 + Rebrand 為 GoMore QA Master

- 日期：2026-06-01
- 分支：`feature/rebrand-gomore-qa-master`
- 狀態：設計已核可，待寫實作計畫

## 1. 背景與目標

POC 評估（見 `docs/poc-summary-report.zh-TW.md`）結論為「以 API 測試工具名義導入、fork 內部化、停用 CAPTCHA 模組」。本設計定義將上游 `mk-qa-master` fork 為公司內部專案 **GoMore QA Master** 的具體改動。

**目標**

1. 把運作/對外面的 `mk-qa-master` / `mk_qa_master` 字樣全面換為 GoMore QA Master 品牌。
2. 停用並移除 CAPTCHA（visual challenge）模組與其資產，落實 POC 合規建議。
3. 改為公司內部 Git 散佈，移除公開發佈管道。
4. 換上 logo placeholder，待正式圖。

**非目標（本次不做）**

- 不改歷史/內部文件的字樣：`docs/prd-*`、`docs/walkthrough-*`、`docs/framework.md`、`docs/*postmortem*`、`docs/demo-video-script.md`、`docs/smithery-listing.md`，以及 POC 產出（`docs/poc-summary-report.zh-TW.md`、`docs/sqa-poc-plan.zh-TW.md`、`docs/qa-bug-tickets-gwp-backend-qa.md`）。
- 不做鎖版本（`==`）—— 經確認本次不納入。
- 不改功能行為（runner / 安全規則邏輯不動）。
- 不處理 iOS / mobile 相關（POC 已判 NO-GO）。

## 2. 採用方案

**方案 A：原地 `git mv` + 系統化 find/replace（已選定）**。專案僅約 11.5k 行、套件邊界清楚，就地改最乾淨並保留 git 歷史。
（已否決：B 重寫新 repo —— 丟失 inline 歷史、過度工程；C 薄包裝 shim —— 內外名不一致、半套 rebrand。）

## 3. 命名對照表

| 類別 | 舊 | 新 |
| --- | --- | --- |
| 顯示名 | mk-qa-master / MK QA Master | **GoMore QA Master** |
| Python 模組/套件 | `mk_qa_master` | `gomore_qa_master` |
| kebab 識別字（MCP server 名、PyPI 名、plugin、skill 目錄） | `mk-qa-master` | `gomore-qa-master` |
| entry point | `mk-qa-master = "mk_qa_master.server:run"` | `gomore-qa-master = "gomore_qa_master.server:run"` |

## 4. 改動範圍

### 4.1 結構移動（`git mv`，保留歷史）

- `src/mk_qa_master/` → `src/gomore_qa_master/`
- `skills/mk-qa-master/` → `skills/gomore-qa-master/`

### 4.2 字串替換（規則式 sweep）

在以下範圍替換 `mk_qa_master→gomore_qa_master`、`mk-qa-master→gomore-qa-master`、顯示字樣 `MK QA Master / mk-qa-master → GoMore QA Master`：

- `src/gomore_qa_master/**`（所有 import、`Server("…")`、reporters/html.py 報告標題等）
- `skills/gomore-qa-master/**`
- `pyproject.toml`、`Dockerfile`、`README.md`、`README.zh-TW.md`
- `examples/**`（含 `configs/*.example.{json,toml}`、`sample_api_project/`、`sample_vulnerable_api/`；不含 4.3 將刪除的 captcha fixtures）、`sample_report.html`
- `.github/ISSUE_TEMPLATE/*`、`.github/pull_request_template.md`、`.github/workflows/ci.yml`
- `.gitguardian.yaml`、`.gitguardian.yml`
- `tests/**`（連同 4.3 的工具數/CAPTCHA 調整一起改）

**排除**：`docs/**`（歷史文件 + POC 報告）保留原樣。

### 4.3 停用 CAPTCHA（移除工具面 + 資產）

- `src/gomore_qa_master/server.py`：移除 `inspect_visual_challenge` / `solve_visual_challenge` 的 list_tools 宣告與 dispatch 分支（工具數 **21 → 19**）。
- 刪除：`src/gomore_qa_master/tools/visual_challenge.py`、`visual_challenge_driver.py`。
- `src/gomore_qa_master/config.py`：移除 CAPTCHA 區段（`QA_VISUAL_CHALLENGE_*`）。
- 刪除 CI：`.github/workflows/dogfood-real-recaptcha.yml`。
- 刪除 scripts：`scripts/dogfood-recaptcha.py`、`scripts/dogfood-inspect-only.py`、`examples/closed_loop_solver.py`。
- 刪除 fixtures：`examples/sample_captcha_fixture/`、`examples/sample_hcaptcha_fixture/`、`examples/sample_captcha_mobile_app/`。
- 測試：刪 `tests/test_visual_challenge.py`；更新 `tests/test_smoke.py`（工具數斷言 21→19、移除 captcha 工具名）；修 `tests/test_skill_distribution.py`（如引用 captcha command/skill）。
- skill：移除 captcha 相關 command/reference（若有）。
- `pyproject.toml`：description / keywords 移除 reCAPTCHA / hCaptcha / visual-challenge 字樣。

### 4.4 內部化散佈

- 刪除 `.github/workflows/publish.yml`、`smithery.yaml`。
- `pyproject.toml` `[project.urls]` 改指內部 git placeholder：`git+ssh://git@<INTERNAL_GIT_HOST>/gomore-qa-master.git`（實際 host 由團隊填）。
- README 安裝指引改為 `pip install git+ssh://...`（內部 repo）。

### 4.5 Logo

- `assets/logo.png` 換為**文字版 placeholder**（內容標示「GoMore QA Master」），檔名維持 `logo.png` 以免動引用。
- README logo 引用維持指向 `assets/logo.png`；旁加 `<!-- TODO: 換成正式 GoMore logo -->`。

### 4.6 授權（法律必要，不可省）

- `LICENSE`：**保留**原作者 (Jack Kao) 的 MIT 著作權聲明。
- 可新增 `NOTICE`：載明本專案 fork 自上游 `mk-qa-master`、GoMore 內部使用。

## 5. 驗收標準

1. `pytest tests/` 全綠（已扣除 captcha 測試、工具數斷言已更新為 19）。
2. server 啟動 `list_tools()` 回傳 **19** 個工具，且不含 `inspect_visual_challenge` / `solve_visual_challenge`。
3. `grep -rIE "mk[-_]qa[-_]master|MK QA Master" src/ skills/ pyproject.toml Dockerfile README*.md .claude-plugin .codex-plugin examples/configs` → **零殘留**。
4. `pip install -e .`（用新 `gomore-qa-master` 名）成功；console script `gomore-qa-master` 可啟動。
5. `find . -path ./.git -prune -o -iname "*captcha*" -print` 與 visual_challenge 檔案 → 已清除（docs 歷史 PRD 除外）。
6. 公開發佈檔（publish.yml、smithery.yaml）已移除。

## 6. 風險與緩解

| 風險 | 緩解 |
| --- | --- |
| 移除 captcha 漏改某處 import → 啟動爆 | 驗收 #2 server 啟動 + `pytest` 把關 |
| 字串替換誤傷字面相依（如 telemetry 路徑常數） | sweep 後跑全測；先 dry-run grep 預覽差異 |
| git mv 後 import 殘留舊名 | 驗收 #3 grep 零殘留 |
| `test_app/`（209MB apk）誤入 git | 加入 `.gitignore`，commit 前確認不 staged |
| MIT 授權合規 | 保留原 LICENSE + 新增 NOTICE（驗收檢查） |

## 7. 不在本次範圍

- 鎖相依版本（`==`）。
- 歷史文件字樣替換。
- iOS / mobile 支援。
- 正式 logo（待團隊提供後替換 placeholder）。
