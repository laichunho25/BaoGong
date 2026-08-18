# Eval 結果紀錄

> COMPLIANCE §8 要求：**每個已啟用的 agent 都要有 eval 分數記錄在這裡**。
> 沒有記錄 = 不得在生產啟用。這份檔案是那條 checklist 的證據，不是報告。

## 怎麼跑

```powershell
# 離線部分（golden set 格式、scoring、規則式 fallback 的地板）——CI 每次都跑
uv run pytest apps/agents/tests/test_evals.py

# 真 API 部分——要錢，CI 不跑（-m 'not eval'），要手動跑
$env:ANTHROPIC_API_KEY = "sk-ant-..."
uv run pytest -m eval -s
```

`-s` 是必要的：分數用 `print` 出來，不 `-s` 就只看得到 pass/fail。
跑完把當日日期、模型版本、分數補一列到下面的表。

## 現況（2026-08-18）

| Agent | Golden set | 門檻 | 模型分數 | 規則 fallback 分數 |
|---|---|---|---|---|
| A1 RfqIntake | 22 筆（合成） | services F1 ≥ 0.85、hallucinated_budget_rate = 0 | **未跑** | F1 0.85（P 0.95 / R 0.78）、幻覺預算 0 |
| A2 Matching | 22 筆（合成） | nDCG@5 ≥ 0.7、grounding violation rate = 0 | **未跑** | nDCG@5 0.80、grounding 0/207 |
| A3 Nnc1Extraction | **無** | licence_no 準確率 ≥ 0.95、false-pass = 0 | — | — |
| A4 ReviewModeration | 22 筆（合成） | escalation recall ≥ 0.95、false escalation ≤ 0.35 | **未跑** | recall 0.50、false 0.06 |
| A5 QuoteAnalysis | 22 筆（合成） | missing_govt_fee precision ≥ 0.9 | **未跑** | precision 0.56、recall 1.00 |

**五個 agent 目前都沒有跑過真 API eval，所以五個都不得在生產啟用。**
`AGENTS_ENABLED` 預設關，`config/settings/test.py` 也明確關掉。

## 為什麼還是先記了 fallback 分數

因為關掉模型時跑的就是那條路。fallback 的分數不是 agent 的分數，
但它是「模型掛掉那天平台實際的表現」，而那個數字現在有人看過了：

- **A1 fallback F1 0.85**，recall 0.78 偏低——關鍵字讀不出沒有寫成關鍵字的需求。
  但 precision 0.95 且**幻覺預算 0**：它寧可少填一欄，也不會把買家沒寫過的金額
  填進表單再送去給持牌公司看。這一項是硬零，模型路徑同樣要守。
- **A4 fallback recall 0.50**——正則表達式讀得出電話號碼，讀不出誹謗。
  這就是 A4 存在的理由，也是為什麼 A4 關掉時所有評價一律進人工佇列而非自動放行。
- **A2 fallback nDCG@5 0.80，但 top-1 只對 4/22**——這兩個數字要一起看。
  0.80 幾乎全部來自「有沒有做買家要的那幾項服務」這個硬條件（SQL 排序就是這樣排的），
  也就是說**規則已經越過 0.7 的門檻了**。所以 A2 上線的實際標準不是 0.7，是
  **「模型要明顯好過 0.80」**，否則付錢呼叫模型換不到東西。
  模型真正要贏的是 top-1：同樣做齊那幾項服務的公司之間誰該排第一，
  規則只有 4/22 猜對，因為它沒讀過買家那段話。
- **A2 grounding violation 0/207**——不是「跑出來剛好是 0」，是
  `screen_matches` 讓不合格的句子**進不了 DB**：兩條路（模型與 fallback）共用同一個
  篩子，過不了就整句丟掉。這條是硬零，理由見 COMPLIANCE §2。
- **A5 fallback precision 0.56 / recall 1.00**——**刻意的**。規則只看得到
  line item 和那個勾選框，看不懂「已包含政府规费」寫在附言裡，所以它寧可多喊。
  9 筆該喊的全喊中了，另外誤喊 7 筆。買家看到的措辭是
  「此报价未列明政府规费，建议向服务商确认」，誤喊的代價是買家多問一句；
  漏喊的代價是簽約之後才發現要再付一筆政府費。方向選對了。
  0.9 的 precision 門檻是**給模型的**，不是給規則的——這正是模型要換回來的東西。

## 樣本的來源與限制（重要）

**四個 golden set 全部是合成的**，由開發者按 AI_AGENTS 的規格與真實使用情境手寫，
不是抽樣自平台上的真實資料（P5 階段平台還沒有真實資料）。

合成樣本量得到的是「**規則有沒有壞**」，量不到「**模型準不準**」：
寫題目的人和寫規則的人是同一個，題目自然會落在規則想得到的形狀裡。
上線前仍然欠：

- A1：30 段真實買家原話（微信貼過來那種），人工標註 services 與有無預算。
- A2：30 張真實 RFQ，配上當時真實的候選名單，人工標註理想 Top5。
  現有 22 筆的候選池是**開發者自己寫的**，理想答案也是同一個人標的——
  這比其他三個更危險，因為排序題的「正確答案」本來就有主觀成分。
- A3：20 份去識別化 NNC1 樣本。**完全沒有**，這是四個裡面最缺的一個。
- A4：50 條真實評價的人工標註（現有 22 筆合成，6 筆應升級）。
- A5：25 份真實報價，人工標註有沒有列明政府規費。

拿到真實樣本後**不要刪掉合成樣本**——合成的那批是回歸測試，
真實的那批才是 eval。兩者答的是不同的問題。

## A5 golden set 裡刻意留的兩題

q03 和 q13 沒有政府費的 line item、`includes_govt_fee` 也沒勾，
但附言裡用中文寫了費用已包含。規則判不出來，一定誤喊；模型應該讀得出來。
`test_the_quote_set_holds_quotes_that_only_say_so_in_words` 會擋住把這兩題刪掉的改動——
刪掉它們，eval 量的就從「讀不讀得懂」變成「正則寫得對不對」了。

## A2 golden set 裡刻意做的一件事

候選池在寫檔時用固定種子**洗過牌**（`random.Random(20260818)`）。
原本是按「好→壞」順序寫的，那樣的話一個什麼都不做、原樣輸出的排序器
也能拿到 nDCG 0.99——量到的是我寫題的順序，不是誰排得好。
洗牌之後 fallback 從 0.99 掉到 0.80，這個 0.80 才是真的服務覆蓋度帶來的。

`test_every_case_holds_a_candidate_that_does_not_belong_in_the_top` 擋住
「整池都是好答案」的題目：那種題目誰來排都滿分，等於沒考。
