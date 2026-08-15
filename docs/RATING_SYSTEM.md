# RATING_SYSTEM — 評分演算法規格

## 1. 基礎規則（來自產品定義）

初始每家公司有 **10 條虛擬 5 分評價** 作為先驗（Bayesian prior），避免「1 條 1 分 = 1.0 分」的失真。

```
prior_count = 10
prior_mean  = 5.0

rating = (prior_count * prior_mean + Σ weight_i * score_i) / (prior_count + Σ weight_i)
```

範例（產品原始定義，全部權重 = 1）：
第一條真實評價 4.5 → `(10*5 + 4.5) / 11 = 4.9545…` → 顯示 **4.95**

## 2. 權重（v2，v1 可先全部設 1.0）

| 條件 | weight |
|---|---|
| 已 NNC1 驗證評價 | **1.0** |
| 未驗證評價 | **0.0**（顯示但不計入主分） |
| 驗證評價且距今 > 24 個月 | 0.5（時間衰減） |
| 同一 IP／裝置短期多筆 | 0（moderation 先擋） |

> v1 上線只用「驗證 = 1.0 / 未驗證 = 0」這條規則，簡單且可解釋。時間衰減留到有量再開。

## 3. 子分與總分

5 個子分（1–5，step 0.5）：`price_transparency`, `responsiveness`, `bank_support`, `professionalism`, `after_sales`

單條評價的 `overall` = 5 個子分的算術平均，四捨五入到 0.1。
`bank_support` 若用戶勾選「未使用開戶服務」則排除，改取其餘 4 項平均。

公司總分套用 §1 公式；子分維度也各自套同一公式，用於雷達圖。

## 4. 顯示規則

- 顯示到小數點後 2 位（`4.95`）。
- 必須同時顯示 **`n` 條已驗證評價**（不要顯示 prior 的 10 條，否則誤導）。
- `n = 0` 時：顯示「暫無已驗證評價」，**不顯示 5.00 分**。星等區塊改為灰色空狀態。
  → 這一點非常重要：不能讓沒評價的公司看起來滿分。
- Tooltip 說明：「本分數採用貝葉斯先驗（10 條 5 分基準）計算，避免少量評價造成極端分數。」

## 5. 排序（搜尋結果 default）

```
score = 0.45 * normalized_rating          # 0-1
      + 0.20 * verified_review_volume     # log10(1+n) 正規化，上限 n=50
      + 0.15 * certification_level        # free 0 / verified 0.6 / premium 1.0
      + 0.12 * profile_completeness       # 0-1
      + 0.08 * responsiveness_score       # RFQ 平均回覆時間反向正規化
```
`premium` 置頂位是**獨立版位**，必須標示「贊助」，**不得混進自然排序並偽裝**。

## 6. 反作弊

- 一個帳號對同一公司只能有一條評價（DB unique 約束）。
- 留評需：手機或郵箱驗證 + Turnstile。
- Moderation Agent 標記：極端用語、無具體事實、疑似競爭對手、疑似公關稿、含個資。
- 短期內同公司暴增評價 → 自動進 `pending_moderation` 佇列。
- 秘書公司自評／關聯帳號：比對註冊郵箱網域、IP、上傳文件關聯。

## 7. 實作位置

`apps/reviews/services.py::recompute_provider_rating(provider_id)`（P4-1 已實作）
- 每次評價 publish / hide / remove 決議後**同步**重算並寫 `Provider.rating_cached`、
  `rating_count`、`verified_review_count`，接著呼叫 `providers.services.recompute_ranking_inputs`
  （§5 的排序把評分和另外四個輸入混在一起，評分一動排序就過期）。
  同步而非丟給 Celery：頁面上讀得到的評價和算不出來的分數對不上，是使用者看得見的自打嘴巴。
- 核驗（P4-2）也走同一條同步路：`services.decide_verification` 通過或撤銷「已驗證」後，
  在同一個交易內重算——徽章先出現、分數晚一步，同樣是看得見的自打嘴巴。
- `reviews.recompute_provider_rating` / `reviews.recompute_all_ratings` 兩個 task 留給
  做不到同步的路徑：批次修正，以及日後從 worker 回來的結果。
  後者**刻意不排進 beat**——它是改公式時的一次性遷移動作，不是每日雜務。
- 重算是「從評價重新算一次」而不是「調整累計值」：某個狀態轉換寫錯時，
  再跑一次就能修好，不會留下一個永遠錯下去的數字。
- unit test 覆蓋（`apps/reviews/tests/test_rating.py`）：0 條、1 條 4.5 分（期望 4.95）、
  1 條 1.0 分（期望 4.64，先驗的用處）、全部未驗證、全部未 published、公司已被刪除。
