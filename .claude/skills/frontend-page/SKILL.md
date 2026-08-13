---
name: frontend-page
description: 在 QS Matching Platform 建立或修改前端頁面（Django Templates + HTMX + Alpine.js + Tailwind）。當使用者說「做一個 XXX 頁」「改 UI」「加篩選器」「這頁很醜」「做比較表」「加載入狀態」時觸發。含 i18n、內地可訪問性、必備合規元件與效能要求。
---

# frontend-page

## 技術約束（不要提議換成 React/Next）

- Django Templates + **HTMX 2** + **Alpine.js**（只做 local UI state）+ **Tailwind CSS 3**
- 不做 SPA。頁面必須 SSR，SEO 是主要獲客渠道。
- **內地可訪問性**：禁用 Google Fonts、Google Analytics、reCAPTCHA、任何 Google/Facebook CDN。
  → 自託管字體（Noto Sans SC subset）、Plausible 自託管、Cloudflare Turnstile。
  → 所有 JS/CSS 自託管，不用公共 CDN。

## 每個頁面的必備檢查

- [ ] 所有文案走 `{% trans %}` / `{% blocktrans %}`，**零硬編中文**
- [ ] 預設語系 `zh-Hans`（目標客群內地），提供 zh-Hant / en 切換
- [ ] 顯示 TCSP 官方數據 → 必須 include `components/data_source_notice.html`
- [ ] 顯示評分 → 必須用 `components/rating_display.html`（0 條評價時空狀態，**不顯示 5.00**）
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

- 8pt 間距網格：只用 `2/3/4/6/8/12/16` 這幾階
- 顏色用語意 token（在 `tailwind.config.js` 定義）：`brand`, `surface`, `muted`, `success`, `warning`, `danger`
  → 不要在 template 直接寫 `bg-blue-500`
- 字級用固定 scale：`text-xs/sm/base/lg/xl/2xl/3xl`，不自訂
- 每個互動元素必須有 `hover:` / `focus-visible:` / `disabled:` 狀態
- 重複 3 次以上的樣式組合 → 抽成 `templates/components/`，不是 `@apply`

## 效能

- 列表頁 P95 < 800ms
- `select_related` / `prefetch_related` 消滅 N+1；用 django-debug-toolbar 確認 **query 數 < 15**
- 圖片：WebP + `loading="lazy"` + 明確 width/height（避免 CLS）
- 分頁用 cursor pagination（大表 OFFSET 會慢）
- 熱門查詢加 `cache_page` 或 template fragment cache，TTL 短（數據每日更新）

## 關鍵頁面規格

| 頁面 | 重點 |
|---|---|
| `/providers/` 列表 | 搜尋（名稱模糊 + 牌照號）、多維篩選、排序、分頁、每張卡顯示認證徽章 + 評分 + 價格區間 |
| `/providers/<slug>/` | 官方區（灰底 + 來源標註）與平台區視覺區分；未認領顯示 CTA；評價區分「已驗證/未驗證」兩塊 |
| `/compare/` | 最多 3 欄並排，差異列高亮，缺資料顯示「未提供」而非空白 |
| `/rfq/` 需求牆 | 秘書公司視角；顯示今日剩餘報價額度；已報價的置灰 |
| 報價比較表 | 同口徑 line item 對齊，flags 用中性措辭的 tooltip |
| 教育文章 | 目錄側欄、閱讀進度、相關文章、結尾免責 |

## 可及性

- 所有互動元素鍵盤可達，`focus-visible` 有明顯樣式
- 表單 label 正確關聯，錯誤訊息 `aria-describedby`
- 顏色對比 ≥ 4.5:1
- HTMX 更新區塊加 `aria-live="polite"`
