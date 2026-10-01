# Changelog

本檔案記錄 GoMore QA Master（fork）自身的版本演進，格式依循
[Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，版號依循
[Semantic Versioning](https://semver.org/lang/zh-TW/)。

> **版號語意說明**：本 fork 於 2026-05-27（分岔點 `14c8a11`）自上游
> `kao273183/mk-qa-master` v0.9.x 系列岔出後，`version`（`pyproject.toml`）
> 即在 fork 內**獨立演進**，不再與上游版號同步。上游之後持續到
> v1.4.0（2026-06-04），但那是另一條時間線——**fork 的 0.9.8–0.9.11
> 與上游的 v1.x 沒有語意對應關係**，純屬巧合地數字相近。詳見
> [`docs/UPSTREAM.md`](docs/UPSTREAM.md)。

## [Unreleased]

## [0.9.11] - 2026-10-02

對應 main 的合併提交：`5cb423b`（Merge pull request #14 from
`stonebomdic/feat/analyzer-visibility-table-assertions`）。

### Added
- DOM probe 新增共用 `isVisible()` 判斷，標記 form / cta / table / dialog
  的可見性（`a1c3afb`）。
- `metadata.visible=False` 的 form / cta / table 改為渲染「存在性骨架」
  斷言，取代先前的省略處理（`ba9d7c0`）。

### Changed
- `isVisible()` 對齊 Playwright 的可見性定義，修復表單 / CTA 的可見性
  誤判（`1112c69`）。
- runner 的 table 表頭斷言改為比對整個 `thead` + `innerText`；隱藏骨架
  改用文字過濾，避免誤判（`524de44`）。
- 覆審收尾：RTL 版面跳過水平離屏判斷、aria 表頭 `has_text` 改取首
  token、相關註解校正（`8c4188b`）。
- 補充 Opus 覆審修復的行為說明文件（可見性定義、table 斷言、隱藏骨架
  文字過濾）（`48c50fa`）。

### Fixed
- `visible=False` 骨架不再疊加重複的通用 TODO 尾巴（`f18f227`）。

## [0.9.10] - 2026-10-01

對應 main 的合併提交：`70f9d61`（Merge pull request #13 from
`stonebomdic/feat/generator-selector-auth-scaffold`）。

### Added
- `auto_generate_tests` 新增檔名碰撞改名機制 + auth conftest 鷹架
  （`04d4d59`）。
- 補充 `auto_generate_tests` gotchas 文件（隨版號提交一併補上）。

### Changed
- renderer 對非唯一 selector 新增退化處理 + CTA 文字定位 + dialog
  存在性斷言（`da7edeb`）。
- 覆審收尾三項：`<a>` 標籤 CTA 定位改用 `filter(has_text=...)`、env
  變數名加流水號避免碰撞、預設 port 省略（`3bdd84b`）。

### Fixed
- Opus 覆審 round 3 必修項：userinfo 落檔、CTA 定位、dialog 空斷言等
  （`a6c49b8`）。

## [0.9.9] - 2026-10-01

對應 main 的合併提交：`9b4c7e1`（Merge pull request #12 from
`stonebomdic/feat/analyzer-table-modules`）。

### Added
- `analyze_url` 新增資料表（table）模組偵測，並擷取 implicit form 欄位
  （`4380272`）。

### Fixed
- table / implicit form 偵測的假陽性與 render 語意問題（code review
  回修）（`16d8fbe`）。
- 回修 implicit form 退回欄位的 `fill()` 崩潰（N3）（`6f6ac88`）。
- `sel()` / `hasStableSelector` 補 placeholder fallback，修回真實頁面
  回歸（`490094b`）。
- name fallback 擴及 `SELECT` / `TEXTAREA`，`data-testid` / `name` 補
  `escAttr` 跳脫（`4621a71`）。

## [0.9.8] - 2026-09-27

對應 main 的合併提交：`804d864`（Merge pull request #6 from
`stonebomdic/chore/lint-hardening-ci`）。本版本號在 pyproject.toml 中
持續到 `9b4c7e1`（0.9.9 進版）前，期間另有 PR #7、#8、#9（經 PR #10
併入）與 PR #11（文件）落地，一併列在本節。

### Added
- 新增 ruff lint CI job + `pyproject.toml` 的 `[tool.ruff]` 守門設定
  （`1616ac2`，PR #6）。
- runner 封裝層補上 234 個新單元測試（339 → 573）（`c4ea88f`，PR #6）。
- `analyze_url` 支援 localStorage auth 注入（P2）（`fc2043b`，PR #7）。

### Changed
- ruff 全面清理，違規由 125 → 43，剩餘皆為刻意設計保留（`9687382`，
  PR #6）。
- pytest / schemathesis runner 改以 venv 直譯器解析，不再依賴 `PATH`
  （`47ee6aa`，PR #6）。
- 鎖 `mcp<2` 依賴版本 + 回填 POC Phase 5/6 實測結果（`9158999`，
  PR #6）。
- 歸檔官網 QA 缺陷報告至 `docs/`，忽略本機掃描 artifact（`5a788ae`，
  PR #6）。
- BOLA 掃描器改以回應差異比對根治假陽性（`6bcd554`，PR #7）。
- BOLA 判定邏輯改用 4-probe fingerprint，取代會漏報的「相同即 INFO」
  邏輯；`PublicContent` 改列 HIGH、加萬用字元防呆、調整判定順序
  （`19e49fa` / `a7fdfa4`，PR #9）。
- pytest-playwright 產生器修復三項缺陷（POC F-1）（`7625c3a`，PR #7）。
- 產生器修正斷言方向、單欄留空誤判、預設流程觸發率（P3 覆審）
  （`266a6a1`，PR #8）。
- skill 文件 prompt audit：清除 17+ 項 stale facts 與壞連結
  （`0dd6e80`，PR #11）。

[Unreleased]: https://github.com/stonebomdic/GoMore-QA-Master/compare/main...HEAD
