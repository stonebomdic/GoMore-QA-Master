---
title: GoMore 官網 QA 缺陷清單
tags: [QA, GoMore, 官網, 缺陷報告]
description: www.gomore.net 文案與功能查核缺陷清單
robots: noindex, nofollow
---

# GoMore 官網缺陷清單（Defect Report）

> **受測標的**：https://www.gomore.net/ （Nuxt 3 SPA，繁／簡／英三語）
> **查核日期**：2026-06-04（初驗）／2026-06-04（複驗）
> **查核方式**：JS bundle 文案抽取（71 chunks）+ 信箱網域／DNS／HTTP 標頭／影片資產實機驗證
> **嚴重度定義**：🔴 Critical（須立即修）｜🟠 High｜🟡 Medium｜⚪ Low
>
> **複驗結論**：14 項中 **7 項已修、1 項部分修、6 項未修**；未發現新的 side effect。詳見 [§A-2 複驗對照](#a-2-2026-06-04-複驗對照)。

[TOC]

---

## A. 缺陷清單

| ID | 缺陷描述 | 嚴重度 | 重現步驟 | 建議修正 |
|----|----------|--------|----------|----------|
| **DEF-01** | 徵才信箱網域 `gomore.me` 已過期：履歷寄不到，且過期網域可被他人搶註後攔截求職者個資 | 🔴 Critical | 1. 開 `/careers` 點「寄送履歷至 hr@gomore.me」<br>2. DNS 查 `gomore.me`：A 指向 `expired.hichina.com`、**無 MX**<br>3. 對照 `gomore.net` MX = `gomore-net.mail.protection.outlook.com`（正常） | 將徵才信箱改為 `hr@gomore.net`；全站搜尋並修正所有 `gomore.me`；如要保留則須續訂網域並設定 MX |
| **DEF-02** | 全站缺少 HTTP 安全性標頭（HSTS／CSP／X-Frame-Options／X-Content-Type-Options／Referrer-Policy） | 🟠 High | `curl -I https://www.gomore.net/`，回應僅 `Server: Tengine`，無任何安全標頭 | 於阿里雲 OSS／CDN 層加上 `Strict-Transport-Security`、`X-Content-Type-Options: nosniff`、`X-Frame-Options: SAMEORIGIN`、`Referrer-Policy`，並評估 CSP |
| **DEF-03** | 隱私權政策最後更新 2023-12-25（逾 2.5 年），未涵蓋 2025 新增企業健康服務蒐集的照片／飲食／情緒等敏感資料 | 🟠 High | 開 `/privacy`，見「最後更新日期：2023年12月25日」；對照首頁宣稱「2025 年推出企業員工健康整合服務」 | 依現行功能更新政策內容與日期，補充新增資料類別、用途與企業端共享方式 |
| **DEF-04** | 「聯絡我們」表單實為 `mailto:` 開啟本機郵件程式，資料未送後端；無郵件程式者會無聲失敗，但有「提交中…／提交失敗」狀態誤導 | 🟡 Medium | 開 `/contact` 填表送出 → 觸發 `mailto:Sales@gomore.net?...`；於未設定郵件程式的瀏覽器測試會無反應 | 改為後端 API 送單（含防濫發驗證），或將按鈕明確改為「以 Email 聯繫」 |
| **DEF-05** | `og:image` 使用相對路徑，社群分享卡片無法顯示預覽圖 | 🟡 Medium | 檢視首頁原始碼：`<meta property="og:image" content="/main/logo.png">`；以 FB／LINE 分享偵錯工具測試無縮圖 | 改為絕對網址 `https://www.gomore.net/main/logo.png` |
| **DEF-06** | 新聞列表英文標題夾雜簡體字：`...from Mo样 Technology` | 🟡 Medium | 開新聞列表「GoMore Wins "2025 Strategic Partner" Award」卡片英文標題；同 chunk 另一處正確為 `from Moyang Technology` | 將 `Mo样` 改為 `Moyang` |
| **DEF-07** | 繁中新聞摘要夾雜簡體字：「運動科技行業的發展**趋**勢」 | 🟡 Medium | 開新聞列表「上升至國家戰略…」卡片摘要末句（首頁同句已正確作「發展趨勢」） | 「趋」改為「趨」 |
| **DEF-08** | 首頁見證／輪播出現未替換的佔位文字「專家 1／專家 2／專家 3」 | 🟡 Medium | 開首頁專家驗證／見證區塊，輪播項目名稱顯示「專家 1/2/3」 | 替換為真實專家姓名與職稱，或移除該佔位 |
| **DEF-09** | 首頁技術區塊「性能 96.7%／準確度 98.2%／效率 94.1%」為無來源、無說明的裝飾數據 | ⚪ Low | 開首頁技術展示區塊可見三組百分比，無指涉對象 | 補上明確指標定義與出處，或移除避免誤導／不實宣稱 |
| **DEF-10** | SEO `og:description` 數字過時且為陸用語：「超過100種算法、1000萬台設備」 vs 實際頁面「140+ 演算法、2000萬台」 | 🟡 Medium | 檢視首頁原始碼 `og:description`；對照 hero 區「2000萬台+／140個+」 | 數字更新為 140+／2000萬+，並改「算法→演算法、行業→產業」 |
| **DEF-11** | 繁中站用詞混用：智能（×24）vs 智慧、個性化（×4）vs 個人化（×21）、算法 vs 演算法 | ⚪ Low | 全站文案比對；企業方案段尤其多「個性化」「智能教練」 | 以台灣用語統一為「智慧／人工智慧／個人化／演算法」 |
| **DEF-12** | 公司地址格式不一致：頁尾「北新路**3**段」vs 隱私頁「北新路**三**段」 | ⚪ Low | 比對頁尾與 `/privacy` 末段地址 | 統一為「三段」 |
| **DEF-13** | 信箱大小寫不一致：`Sales@gomore.net`（首字大寫）vs `gwp-support@gomore.net` | ⚪ Low | 比對聯絡／頁尾與隱私頁信箱 | 統一全小寫 `sales@gomore.net` |
| **DEF-14** | 影片檔過大影響載入效能：`GoMore_AI_en.mp4` 76MB、`1AIAlgo.webm` 90MB | ⚪ Low | `curl -I` 各影片 Content-Length；行動網路載入緩慢 | 重新壓縮／降位元率，或改自適應串流（HLS／DASH）、加 `preload="none"` 與封面圖 |

---

## A-2. 2026-06-04 複驗對照

> 以與初驗相同方法重新驗證（DNS／HTTP 標頭／71 個 JS chunk 文案抽取／影片資產實機）。

| ID | 嚴重度 | 複驗狀態 | 重驗發現 |
|----|--------|----------|----------|
| DEF-01 | 🔴 | ✅ 已修 ⚠️ | 全站已改 `hr@gomore.net`，無任何 `gomore.me` 引用。⚠️ 殘留：`gomore.me` 網域本身 DNS 仍指向 `expired.hichina.com`、無 MX（過期），有被搶註風險，但已非站內缺陷 |
| DEF-02 | 🟠 | ❌ 未修 | `curl -I` 仍僅 `Server: Tengine` + Date，無 HSTS／CSP／X-Frame-Options／X-Content-Type-Options／Referrer-Policy |
| DEF-03 | 🟠 | ❌ 未修 | 隱私頁仍為「最後更新日期：2023年12月25日」 |
| DEF-04 | 🟡 | ❌ 未修 | 聯絡表單仍為 `mailto:sales@gomore.net?subject=` |
| DEF-05 | 🟡 | ✅ 已修 | og:image 改絕對網址 `https://www.gomore.net/main/logo.png`，實測 HTTP 200／image-png |
| DEF-06 | 🟡 | ✅ 已修 | 僅剩 `Moyang`，無 `Mo样` |
| DEF-07 | 🟡 | ✅ 已修 | 繁中已作「發展趨勢」；簡體站 `发展趋势`（本應如此），無混用 |
| DEF-08 | 🟡 | ✅ 已修 | 全 chunk 已無 `專家[123]`／`专家[123]` 佔位 |
| DEF-09 | ⚪ | ❌ 未修 | 裝飾數據 96.7%／98.2%／94.1% 仍在 |
| DEF-10 | 🟡 | ✅ 已修 | og:description 更新為「超過140項演算法、2000萬台設備搭載、10年產業經驗」，與 hero 一致 |
| DEF-11 | ⚪ | 🟠 部分修 | `個性化`→0（已修）、`演算法` 257 處；但 `智能` 仍 179 處（vs 智慧 278），混用未完全收斂 |
| DEF-12 | ⚪ | ❌ 未修 | `北新路3段` 與 `北新路三段` 仍並存 |
| DEF-13 | ⚪ | ✅ 已修 | 信箱已全小寫 `sales@gomore.net`，無 `Sales@` 大寫殘留 |
| DEF-14 | ⚪ | ❌ 未修（微改善） | en.mp4 72MB、1AIAlgo 86MB、tw 34MB — 僅微降，仍偏大 |

**複驗統計**

| 狀態 | 數量 | 缺陷 ID |
|------|------|---------|
| ✅ 已修 | 7 | DEF-01、05、06、07、08、10、13 |
| 🟠 部分修 | 1 | DEF-11 |
| ❌ 未修 | 6 | DEF-02、03、04、09、12、14 |

> 修復集中在文案層（簡繁字／佔位／SEO meta／信箱），最高優先的 🔴 DEF-01 已解。架構／後端層（安全標頭、隱私政策、表單後端、影片壓縮）尚未處理。

**Side effect 檢查 — 未發現新問題。** 原「無異常」項目回歸驗證皆維持正常：

| 項目 | 複驗 |
|------|------|
| 前往平台 `megoluki.gomore.net` | ✅ HTTP/2 200 |
| 影片 Range 串流／快轉 | ✅ 206 Partial Content + `Accept-Ranges: bytes` |
| 影片快取 | ✅ `Cache-Control: max-age=2592000` |
| og:image 絕對網址實際可達 | ✅ 200 image/png（連帶確認 DEF-05 修復） |
| og:description 與 hero 數字一致性 | ✅ 140／2000萬，無新矛盾 |

---

## B. 已驗證「無異常」項目（按鈕／影片／串流）

| 項目 | 驗證方法 | 結果 |
|------|----------|------|
| 影片播放（繁／簡／英三語） | `GoMore_AI_tw/cn/en.mp4`、`main.webm`、`MegoLuki_V4.webm`、`1AIAlgo.webm` | ✅ 全部 HTTP 200、`Content-Type` 正確（video/mp4、video/webm） |
| 影片串流／快轉（seek） | Range 請求 `curl -r 0-1023` | ✅ 全部回 `206 Partial Content`、`Accept-Ranges: bytes`，可邊載邊播與拖曳 |
| 影片快取 | 資產回應標頭 | ✅ `Cache-Control: max-age=2592000`（30 天） |
| 「觀看影片」按鈕 | 對應 `GoMore_AI_tw.mp4`（35MB） | ✅ 可正常載入 |
| 「前往平台／試用與購買」按鈕 | 連向 `https://megoluki.gomore.net/` | ✅ HTTP/2 200 可達 |
| 「LinkedIn」連結 | `linkedin.com/company/bomdic-inc-/` | ✅ 200 |
| 合作夥伴外連（小米／Anker／Suunto／OPPO…） | 網域比對 | ✅ 皆為合法官方網域 |
| JS 內機敏資料 | 全 bundle 比對 api key／secret／token | ✅ 未發現外洩金鑰 |

---

## C. 待人工確認（2026-06-04 複驗已處理）

| 項目 | 說明 | 複驗結果 |
|------|------|----------|
| 「104 人力銀行」連結 | `www.104.com.tw/company/1a2x6bi2d7` 對 curl 回 **403**（104 反爬蟲常見） | ✅ 實機瀏覽器手動驗證：連結正常開啟 |
| 「LinkedIn」連結 | `linkedin.com/company/bomdic-inc-/`，curl 回 `999`（LinkedIn 反爬蟲制式碼） | ✅ 實機瀏覽器手動驗證：連結正常開啟 |
| DEF-08／DEF-09 | 透過 JS 抽取判定，信心度略低，建議於實際渲染畫面目視複核是否為佔位／裝飾 | DEF-08 已修（無佔位）；DEF-09 裝飾數據仍在 |

---

## D. 統計摘要

| 嚴重度 | 數量 | 缺陷 ID |
|--------|------|---------|
| 🔴 Critical | 1 | DEF-01 |
| 🟠 High | 2 | DEF-02、DEF-03 |
| 🟡 Medium | 6 | DEF-04、DEF-05、DEF-06、DEF-07、DEF-08、DEF-10 |
| ⚪ Low | 5 | DEF-09、DEF-11、DEF-12、DEF-13、DEF-14 |
| **合計** | **14** | — |

> **建議優先處理**：DEF-01（`gomore.me` 過期信箱）——同時是功能失效＋個資外洩風險，且僅需將 `.me` 改為 `.net`，修復成本最低、效益最高。
>
> 📌 上表為 **2026-06-04 初驗** 的缺陷分佈。截至同日 **複驗**，DEF-01 已修復，剩餘待處理為 DEF-02／03／04／09／11／12／14（含 1 項部分修）——詳見 [§A-2 複驗對照](#a-2-2026-06-04-複驗對照)。
