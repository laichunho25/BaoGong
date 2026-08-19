---
name: frontend-page
description: 在 包公 BaoGong 建立或修改前端頁面（Django Templates + HTMX + Alpine.js + Tailwind）。當使用者說「做一個 XXX 頁」「改 UI」「加篩選器」「這頁很醜」「做比較表」「加載入狀態」時觸發。含 i18n、內地可訪問性、必備合規元件與效能要求。
---

# frontend-page

> 設計語言與「為什麼長這樣」在 `docs/DESIGN_SYSTEM.md`。這裡是動手前的檢查表。

## 技術約束（不要提議換成 React/Next）

- Django Templates + **HTMX 2** + **Alpine.js**（只做 local UI state）+ **Tailwind CSS 3**
- 不做 SPA。頁面必須 SSR，SEO 是主要獲客渠道。
- **內地可訪問性**：禁用 Google Fonts、Google Analytics、reCAPTCHA、任何 Google/Facebook CDN。
  → 自託管字體（Noto Sans SC subset）、Plausible 自託管、Cloudflare Turnstile。
  → 所有 JS/CSS 自託管，不用公共 CDN。

## 每個頁面的必備檢查

- [ ] 所有文案走 `{% trans %}` / `{% blocktrans %}`，**零硬編中文**
- [ ] 預設語系 `zh-Hans`（目標客群內地），提供 zh-Hant / en 切換
- [ ] 顯示 TCSP 官方數據 → 必須 include `components/data_source_notice.html`，且該區塊用 `official-*` 灰色
- [ ] 顯示評分 → 必須用 `components/rating_display.html`（0 條評價時空狀態，**不顯示 5.00**）
- [ ] 顯示統計數字 → 用 `components/stat.html`，且必須是資料庫真實 count，**沒有示意數字**
- [ ] 表單 → include `components/form_fields.html`，widget class 從 `apps/core/form_styles.py` 來
- [ ] 頁尾 include `components/disclaimer_footer.html`
- [ ] 有贊助／置頂內容 → 必須有「贊助」標示
- [ ] Provider 有 `commission_agreement=true` → 渲染佣金披露元件
- [ ] title / meta description / canonical / og tags
- [ ] 空狀態、載入狀態、錯誤狀態都有設計（不是白畫面）
- [ ] 手機優先；主要斷點 `sm/md/lg`

## HTMX 慣例

```html
<!-- 篩選器：局部刷新 + URL 可分享 -->
<form hx-get="{% url 'providers:list' %}"
      hx-target="#results"
      hx-push-url="true"
      hx-indicator="#spinner"
      hx-trigger="change, keyup[target.matches('input[type=search]')] changed delay:300ms">
```

規則：
- 一律 `hx-push-url="true"`，讓用戶能分享篩選結果連結（SEO + 用戶體驗）。
- 一律有 `hx-indicator`；載入超過 200ms 要看得見。
- Partial template 放 `templates/partials/`，命名 `_xxx.html`。
- View 用 `request.htmx`（django-htmx）判斷回完整頁還是 partial。
- 表單錯誤用 HTMX 回 422 + partial，不要整頁 reload。

## Alpine.js 用途邊界

只做**純前端狀態**：下拉開合、tab 切換、比較清單的暫存勾選、複製按鈕。
任何需要伺服器資料的 → HTMX。不要用 Alpine 存業務資料。

## Tailwind 規範

- **改完 template class 一定要重建 CSS**：`powershell -NoProfile -File scripts/tailwind.ps1`（加 `-Watch` 開發用）。
  `static/css/app.css` 有進版控；忘了重建＝樣式沒生效。不用 npm。
- 8pt 間距網格：只用 `2/3/4/6/8/12/16` 這幾階；區塊之間 `mt-14`
- 顏色只用語意 token（`tailwind.config.js`），不要寫 `bg-blue-500` 或 `text-slate-500`：
  - `brand`（50–950）平台自身；`accent` 只給金額／報價
  - `ink` / `ink-soft` / `ink-muted` / `ink-faint`（文字）
  - `surface` / `surface-sunken` / `surface-raised` / `surface-inverse`（底色）
  - `line` / `line-strong`（邊框）
  - `official-{bg,border,text}` — **官方登記冊資料專用灰，永不用品牌色**（COMPLIANCE §1）
  - `success` / `warning` / `danger` @ 50/200/600/700/900
  - `shadow-card` / `shadow-lift`；`bg-brand-wash` 只給首頁 hero
- 圓角語彙：卡片 `rounded-2xl`、大區塊 `rounded-3xl`、控制項 `rounded-lg/xl`、pill `rounded-full`
- 字級用固定 scale：`text-xs/sm/base/lg/xl/2xl/3xl`，不自訂
- 數字排排站的地方（統計、比較表、金額）加 `.tabular`
- 每個互動元素必須有 `hover:` / `focus-visible:` / `disabled:` 狀態
- 重複 3 次以上的樣式組合 → 抽成 `templates/components/`，不是 `@apply`

## 動效

- 只有一種：`animate-rise-in`（400ms）與 `.stagger`（子元素 `style="--i: {{ forloop.counter0 }}"`）
- **動效是裝飾，不是資訊**：`prefers-reduced-motion` 關掉後頁面說的話必須一字不少
- 不做無限循環動畫、不做自動輪播

## 圖示

只用 `{% include "components/icon.html" with name="shield" class="h-4 w-4" %}`。
24×24 stroke 內聯 SVG，預設 `aria-hidden`，要語意時傳 `label=`。
不用 icon font、不用外部 sprite。加新圖示＝在該檔加一個 `{% elif %}` 分支。

## 元件清單（`templates/components/`）

`badge` `data_source_notice` `deregistration_notice` `disclaimer_footer` `empty_state`
`form_fields` `icon` `provider_card` `rating_display` `section_heading` `stat`

各自的硬規則見 `docs/DESIGN_SYSTEM.md` §6。表單 widget class 集中在
`apps/core/form_styles.py`（`INPUT_CLASSES` / `CHECKBOX_CLASSES` / `FILE_CLASSES`）——
寫在 `.py` 裡也會被掃到，因為 Tailwind `content` 包含 `./apps/**/*.py`。

## 頁面骨架（非首頁一律照這個順序）

```
麵包屑 → 標題卡（名稱 + 徽章列 + 右側關鍵數字）→ 狀態通知
→ 主內容（左右分欄用 grid lg:grid-cols-[minmax(0,1fr)_20rem]）
→ sticky 側欄：下一步動作 + 摘要
```

長表格（比較表、報價比較）第一欄 `sticky left-0`，背景要跟該列自己的底色一致。

## 效能

- 列表頁 P95 < 800ms
- `select_related` / `prefetch_related` 消滅 N+1；用 django-debug-toolbar 確認 **query 數 < 15**
- 圖片：WebP + `loading="lazy"` + 明確 width/height（避免 CLS）
- 分頁用 cursor pagination（大表 OFFSET 會慢）
- 熱門查詢加 `cache_page` 或 template fragment cache，TTL 短（數據每日更新）

## 關鍵頁面規格

| 頁面 | 重點 |
|---|---|
| `/` 首頁 | 六段：hero 統計 → 業務功能 → 需要報價 → 市場資訊 → 精選用家評語 → 熱門搜尋。**全部即時讀 DB，無編輯精選、無示意數字。** 匿名訪客只給數字，需求內容登入後才出現；只有已核驗評價可被精選。組裝邏輯見 `apps/core/views.py::home`，規矩由 `apps/core/tests/test_home.py` 守住 |
| `/providers/` 列表 | 搜尋（名稱模糊 + 牌照號）、多維篩選（含 `service=`，首頁業務功能卡直接連進來）、排序、分頁、比較托盤；每張卡顯示認證徽章 + 評分 + 價格區間 |
| `/providers/<slug>/` | 官方區（灰底 + 來源標註）與平台區視覺區分；sticky 章節導航；未認領顯示 CTA；評價區分「已驗證/未驗證」兩塊 |
| `/compare/` | 最多 3 欄並排，差異列高亮，缺資料顯示「未提供」而非空白 |
| `/rfq/` 需求牆 | 秘書公司視角；顯示今日剩餘報價額度；已報價的置灰 |
| 報價比較表 | 同口徑 line item 對齊，flags 用中性措辭的 tooltip |
| 教育文章 | 目錄側欄、閱讀進度、相關文章、結尾免責 |

## 可及性

- 所有互動元素鍵盤可達，`focus-visible` 有明顯樣式
- 表單 label 正確關聯，錯誤訊息 `aria-describedby`
- 顏色對比 ≥ 4.5:1
- HTMX 更新區塊加 `aria-live="polite"`
