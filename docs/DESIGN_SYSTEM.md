# DESIGN_SYSTEM.md — 介面語言

> 這份文件說明「為什麼長這樣」。要做一頁新畫面時看 `.claude/skills/frontend-page/SKILL.md`（怎麼做）。
> 唯一權威來源是程式碼：token 在 `tailwind.config.js`，全域行為在 `static/css/input.css`，
> 元件在 `templates/components/`，表單 widget class 在 `apps/core/form_styles.py`。

## 1. 設計要解決的問題

目標用戶是**不熟悉香港開公司流程的內地客戶**。他們同時面對三種資訊：

1. 政府公布的事實（牌照、地址、狀態）
2. 秘書公司自己填的內容（服務、價格、賣點）
3. 平台計算的結果（評分、比較表、統計）

**介面的第一責任是讓這三種東西看起來不一樣。** 混在一起會讓平台無意中「聲稱官方身分」
（CLAUDE.md §4.2、COMPLIANCE §1）。所以：

| 資料來源 | 視覺 | 規則 |
|---|---|---|
| 官方登記冊 | `official-bg` / `official-border` / `official-text`（灰） | 永不用品牌色；同一區塊內必須有 `data_source_notice` |
| 公司自填 | 白底卡片 + `line` 邊框 | 旁邊要寫明「由該公司提供」 |
| 平台計算 | `brand` 色 | 數字旁要能點進去看怎麼算的 |

## 2. 顏色 token

只用語意名稱，不在 template 寫 `bg-blue-500`。

- `brand`（50–950，靛藍紫 indigo）：平台自己的動作與強調。整個色階換過一次色相
  （原本是海港藍綠）——換的是 `tailwind.config.js` 一個地方，template 一行都沒改，
  這正是只寫語意名稱的用意。
- `accent`（琥珀）：只用在金額與報價相關的位置。
- 中性色刻意拆成三組，避免文字色和邊框色不小心撞到同一階灰：
  - `ink` / `ink-soft` / `ink-muted` / `ink-faint` — 文字
  - `surface` / `surface-sunken` / `surface-raised` / `surface-inverse` — 底色
  - `line` / `line-strong` — 邊框
- `official-{bg,border,text}` — 官方資料專用灰，見上表。
- `success` / `warning` / `danger`（50/200/600/700/900）— tone 是**意思**不是顏色：
  `success` 是對讀者有利且已定案的狀態，`warning` 是需要他做點什麼的狀態。

陰影只有兩階：`shadow-card`（卡片）、`shadow-lift`（浮起來的東西，如首頁搜尋框）。
`bg-brand-wash` 只給首頁 hero 用——全站唯一允許大聲的地方。

## 3. 版面骨架

每頁的順序固定，讀者不必重新學：

```
麵包屑（非首頁）
標題卡：名稱 + 徽章列 + 右側關鍵數字
狀態通知（除名、審核中、申訴中…）
主內容 ← 左右分欄時為 grid lg:grid-cols-[minmax(0,1fr)_20rem]
側欄（sticky）：下一步動作 + 摘要
```

- 圓角語彙：卡片 `rounded-2xl`、大區塊 `rounded-3xl`、控制項 `rounded-lg/xl`、pill `rounded-full`。
- 間距走 8pt：只用 `2/3/4/6/8/12/16`。區塊之間 `mt-14`。
- 長表格（比較表、報價比較）第一欄 `sticky left-0`，且背景要跟該列自己的底色一致。

## 4. 動效

`animate-rise-in` + `.stagger`（`--i` 設延遲）只用於「一次性入場」，400ms。
**動效是裝飾，永遠不是資訊**：`prefers-reduced-motion` 關掉之後，頁面說的話必須一字不少。
沒有無限循環動畫、沒有自動輪播。

## 5. 圖示

`templates/components/icon.html` 是唯一來源：24×24 stroke，同一格線上畫的內聯 SVG。
不用 icon font、不用外部 sprite（PRD §4 禁公共 CDN，且 icon font 會塞進沒人用的字符）。
預設 `aria-hidden`；需要語意時傳 `label=`。

現有名稱：`alert` `arrow-right` `bank` `building` `calculator` `chat` `check` `clock`
`close` `coins` `doc` `lock` `menu` `passport` `quote` `scale` `search` `shield` `spark`
`stamp` `trademark` `trend` `users`。加新圖示就加一個 `{% elif %}` 分支，不要另開檔案。

## 6. 元件清單

| 元件 | 用途 | 硬規則 |
|---|---|---|
| `badge.html` | 狀態 pill | `tone` 是意思不是顏色 |
| `data_source_notice.html` | 官方資料來源 + `last_synced_at` | 顯示 TCSP 數據的頁面**必須**有（COMPLIANCE §1） |
| `deregistration_notice.html` | 已除名警示 | 用 `warning` token，內容來自 registry template tag |
| `disclaimer_footer.html` | 全站免責聲明 | 每頁都在（在 `base.html`） |
| `empty_state.html` | 空狀態 | 必須說明**為什麼空**並給下一步；不准留白畫面 |
| `form_fields.html` | 全站表單欄位渲染 | 表單一律 include 它，不要各頁自己寫 label/error |
| `icon.html` | 圖示 | 見上 |
| `provider_card.html` | 目錄卡片 | 評分走 `rating_display` |
| `rating_display.html` | 評分 | 0 條已驗證評價時顯示空狀態，**不准顯示 5.00**（RATING_SYSTEM §4） |
| `section_heading.html` | 區塊標題（eyebrow / title / subtitle / 連結） | eyebrow 是標籤不是口號 |
| `stat.html` | 統計數字 | 每個數字都必須是資料庫真實 count，沒有示意數字（COMPLIANCE §2） |

表單 widget 的 class 集中在 `apps/core/form_styles.py`（`INPUT_CLASSES` / `CHECKBOX_CLASSES` /
`FILE_CLASSES`）。這些字串寫在 `.py` 裡也能被 Tailwind 掃到，因為 `content` glob 包含 `./apps/**/*.py`。

## 7. 首頁結構

首頁由 `apps/core/views.py::home` 組出來，**每一個數字、每一則評語都是即時讀取**，
沒有編輯精選、沒有示意數字：

| 區塊 | 資料來源 |
|---|---|
| Hero 統計 | `registry.selectors.market_snapshot` + `rfq.selectors.matching_snapshot` |
| 業務功能 | `providers.selectors.service_summaries`（每格連到 `?service=`） |
| 需要報價 | `matching_snapshot`；登入後才多出 `open_rfqs()[:3]` 的需求預覽 |
| 市場資訊 | `market_snapshot` + `data_source_notice` |
| 精選用家評語 | `reviews.selectors.featured_reviews`（只有 `PUBLISHED` 且 `is_verified`） |
| 熱門搜尋 | `providers.selectors.popular_searches`（`count=0` 的 chip 直接不出現） |

兩條不能破的線：

- **匿名訪客只看得到數字，看不到需求內容。** 需求牆在登入之後，首頁不是它的破口（COMPLIANCE §4）。
- **只有經 NNC1 核驗的評價會被精選。** 首頁是平台講這條規矩的地方，放未核驗的內容等於自打嘴巴。

由 `apps/core/tests/test_home.py` 守住。

## 8. 無障礙

- 焦點環在 `input.css` `@layer base` 統一定義，所有互動元素一致。
- 表單的 radio「chip」樣式用 `has-[:checked]:`，**radio 本體保持可見**——把它藏起來會連焦點環一起藏掉。
- HTMX 更新的區塊加 `aria-live="polite"`；顏色對比 ≥ 4.5:1。
- `<details>` 篩選面板在沒有 JavaScript 時也能用。

## 9. 改動流程

1. 改 template class 之後**必須**重建 CSS：`powershell -NoProfile -File scripts/tailwind.ps1`
   （`app.css` 有進版控，忘了重建等於樣式沒生效）。
2. 樣式組合重複 3 次以上 → 抽成 `templates/components/`，不是 `@apply`。
3. 面向用戶文案一律 `{% trans %}` / `{% blocktrans %}`，零硬編中文。
4. 新的文案要能通過 `apps/core/compliance.py::check_banned_phrases`。
