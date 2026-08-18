# AI_AGENTS — 平台 AI Agent 完整規格

> 共 7 個 agent。全部繼承 `apps/agents/base.py::BaseAgent`，全部有 `fallback()`，
> 全部輸出 structured JSON（pydantic schema），全部寫 `AgentRun` log。

## 通用原則

1. **AI 不做最終決定**。輸出一律是 `suggestion` / `draft` / `flag`，由規則或人手 confirm。
2. **一律 structured output**：用 Anthropic tool use 定義 schema，禁止 regex 解析自由文字。
3. **Prompt 版本化**：`apps/agents/prompts/{name}_v{n}.md`，改 prompt = 新版本檔 + 更新 `prompt_version`。
4. **成本控制**：`AGENT_BUDGET_DAILY_USD` 用完 → 全部 agent 走 fallback，並告警。
5. **每個 agent 必須有 eval**：`apps/agents/evals/{name}/golden.jsonl` ≥ 20 筆，附通過門檻。
6. **PII 最小化**：送進 LLM 前先 redact 身分證號、護照號、電話、完整地址（除非該 agent 必需）。

## 實作進度（截至 A6）

| Agent | 狀態 | Prompt | Eval |
|---|---|---|---|
| A1 RfqIntake | ✅ 已實作（**有偏離，見該節**） | `rfq_intake_v1.md` | ⚠️ 22 筆合成 golden，欠 30 段真實買家原話 |
| A2 Matching | ✅ 已實作（**有偏離，見該節**） | `matching_v1.md` | ⚠️ 22 筆合成 golden，欠 30 張真實 RFQ 標註 |
| A3 Nnc1Extraction | ✅ 已實作 | `nnc1_extract_v1.md` | ❌ 欠 20 份去識別化樣本 |
| A4 ReviewModeration | ✅ 已實作（**有偏離，見該節**） | `moderation_v1.md` | ⚠️ 22 筆合成 golden，欠 50 筆真實標註 |
| A5 QuoteAnalysis | ✅ 已實作（**有偏離，見該節**） | `quote_analysis_v1.md` | ⚠️ 22 筆合成 golden，欠 25 份真實報價 |
| A6 Advisor | ✅ 已實作（**有偏離，見該節**） | `advisor_v1.md` | ⚠️ 32 筆合成 golden，欠 30 條真實提問 |
| A7 RegistryDiff | 未做 | — | — |

分數與樣本來源記在 `apps/agents/evals/RESULTS.md`。**六個 agent 都還沒跑過真 API eval，
所以六個都不得在生產啟用**（COMPLIANCE §8 的上線 gate）。

共用基礎設施已完成：`base.py::BaseAgent`（強制 tool use、指數退避、逐條路徑 fallback）、
`pricing.py`（Decimal 價目表）、`redaction.py`、`schemas.py`、`registry.py`、
`selectors.health()`、`AgentRun` / `AgentFeedback` 與其唯讀 admin。

### fallback 是正常結果，不是錯誤

kill switch 關掉、沒 API key、當日預算用完、API 連續失敗、回傳不合 schema——
五條路全部收斂到同一個 `fallback()`，寫 `status=fallback` + `fallback_reason`。
系統因此永遠答得出東西。**真正的失敗模式是沒有人在看 fallback 率**：
agent 可以連續一個月完全沒被呼叫過，而畫面上看起來一切正常。
`selectors.health(days=7)` 就是為了讓那件事被問出來而存在的。

### 三段 kill switch（COMPLIANCE §8）

`AGENTS_ENABLED`（全域）→ `AGENT_ENABLED_{NAME}`（單一 agent）→ `ANTHROPIC_API_KEY`（沒有就等於關）。
出事時要能只關掉出事的那一個，而不是為了關掉一個 agent 把整個平台的 AI 停掉。
`config/settings/test.py` 預設 `AGENTS_ENABLED = False`：測試忘了 patch 時會走規則，
而不是靜靜地開一個 socket 出去。

---

## A1. RfqIntakeAgent — 需求解析

**用途**：把買家的自然語言（常見於微信貼過來的一段話）轉成結構化 RFQ。

> ### ⚠️ 實作偏離本文件（P5-3，刻意的）
>
> 1. **`services_needed` 等 enum 用平台自己的代碼**（`ServiceCategory` / `CompanyType` /
>    `Timeline` 的 `TextChoices` 值），不是本節寫的那組較短的字串。
>    prefill 的每一欄最後都要落進表單的 `<select>`，中間夾一張對照表，
>    等於讓錯誤發生在對照表裡而不是模型裡。`test_schemas.py` 逐個 `Literal`
>    對著 `TextChoices` 斷言，改了其中一邊另一邊會紅。
> 2. **多了 `title`**：需求牆上顯示的就是標題，讓模型順手擬一句，
>    比讓買家對著空白框想一句好。
>
> **實作狀態**：`apps/agents/rfq_intake.py`，prompt `prompts/rfq_intake_v1.md`。
> 由 `rfq.views` 的 prefill endpoint（HTMX）呼叫，**不寫任何 `Rfq` 列**——
> 回來的只是一張填好的表單（CLAUDE.md 規則 3）。買家按確認送出的那一張才是被存的那一張，
> 並標 `is_ai_assisted=True`；棄掉 prefill 直接手打，下一張需求單不會沾到它
> （`test_an_abandoned_prefill_does_not_attach_to_the_next_requirement`）。
> 送進模型的原話先過 `redaction.redact()`——電話與 email 不需要出境才讀得懂一段需求。
>
> **幻覺預算是硬零**：規則式 fallback 也一樣照這個標準量
> （`test_the_intake_fallback_never_invents_a_budget` 掃過 golden set 裡每一筆
> 沒寫預算的原話）。人民幣金額**不會**被換算成 HKD 預算：五萬人民幣不是五萬港元，
> 而一個被平台自行換算過的數字會以買家自己寫的樣子出現在持牌公司眼前。

- Model: `claude-haiku-4-5-20251001`
- Input: `raw_input`（用戶原話）、可選的部分表單欄位
- Output schema:
```python
class RfqIntakeOut(BaseModel):
    company_type: Literal["private_limited", "branch", "rep_office", "unknown"]
    shareholder_nationalities: list[str]
    business_nature: str
    services_needed: list[ServiceCode]
    needs_bank_account: bool
    preferred_bank_types: list[Literal["traditional", "virtual", "emi"]]
    budget_min_hkd: int | None
    budget_max_hkd: int | None
    timeline: Literal["asap", "1_month", "3_months", "flexible", "unknown"]
    missing_fields: list[str]  # 需要追問的
    clarifying_questions: list[str]  # 最多 3 條，簡體中文
    confidence: float
```
- **後處理**：結果以「預填表單」呈現給用戶確認，用戶按下確認才寫入 `Rfq.structured`。
- Fallback: 只做關鍵字規則抽取（服務名稱、金額 regex、銀行名稱字典），`confidence=0.3`，全部欄位標 `needs_review`。
- Eval 門檻：services_needed 的 F1 ≥ 0.85；不得幻覺出用戶沒提的預算數字（hallucinated_budget_rate = 0）。
  目前 `evals/rfq_intake/golden.jsonl` 22 筆合成樣本，門檻寫在 `evals/runner.py`：
  `INTAKE_SERVICES_F1_THRESHOLD = 0.85`、`MAX_HALLUCINATED_BUDGET_RATE = 0.0`。
  分數見 `evals/RESULTS.md`。30 段真實買家原話仍然欠著。

---

## A2. MatchingAgent — 秘書公司匹配

**用途**：對一張 RFQ，從候選池中排序並解釋為何推薦。

- Model: `claude-sonnet-5`
- **關鍵設計：先用 SQL 硬篩，再讓 LLM 排序。** LLM 只看 Top 30 候選的結構化摘要，不做資料檢索。

```
硬性過濾（SQL，不經 LLM）:
  status = active AND is_published
  AND (needs_bank_account -> bank_account_support = true)
  AND (若指定語言 -> languages 包含)
  AND (預算上限 -> 至少一個 PriceItem 落在範圍或無報價)
排序候選 Top 30 by RATING_SYSTEM §5 的 score
```

- Output schema:
```python
class MatchItem(BaseModel):
    provider_id: str
    rank: int
    fit_score: float  # 0-1
    reasons: list[str]  # 最多 3 條，引用具體事實（"支持简体中文"、"有非本地股东开户案例"）
    concerns: list[str]  # 最多 2 條，例如 "价格区间高于预算 20%"


class MatchingOut(BaseModel):
    items: list[MatchItem]
    unmatched_requirements: list[str]
```
- **紅線**：`reasons` 只能引用候選摘要裡實際存在的欄位。實作時對每條 reason 做 grounding check（關鍵詞必須命中候選資料），未命中則丟棄該 reason。
- **不得輸出**：任何開戶成功率的數字、任何「保證」字眼。輸出後過 banned-phrase filter。
- Fallback: 純 §5 排序分，`reasons` 用模板生成（"支持普通话 · 提供开户协助 · 已认证"）。
- Eval：人工標註 30 張 RFQ 的理想 Top5，量 nDCG@5 ≥ 0.7；grounding violation rate = 0。

> ### ⚠️ 實作偏離本文件（P6-1，刻意的）
>
> **實作位置**：`apps/agents/matching.py`、`prompts/matching_v1.md`、
> 硬篩在 `providers/selectors.py::match_candidates`、寫入在 `agents/services.py::match_providers`、
> 派發在 `rfq/services.py::publish_rfq` 的 `on_commit` → `agents.tasks.match_rfq`。
>
> 1. **候選排序多了一層：先按「命中幾項買家要的服務」，再按 §5 的 `ranking_score`。**
>    本文件只寫了按 §5 排。理由：一家分數很高但完全不做買家要的那幾項服務的公司，
>    不該是買家看到的第一列，而 §5 的分數完全不知道這張 RFQ 要什麼。
> 2. **grounding check 不是「關鍵詞命中」，是「關鍵詞對應的事實必須為真」。**
>    `CLAIM_TERMS` 把每組措辭綁到候選摘要的一個布林欄位；提到「开户」但
>    `bank_account_support=False` → 整句丟掉。另外兩種也丟：命中 banned phrase 的、
>    以及**什麼可查證的事實都沒引用的**（「专业可靠」）。
> 3. **`concerns` 走同一個篩子，但比對的是事實的「不存在」。**
>    「平台上没有公开报价」在沒有報價時才成立；反過來對一家有公開報價的公司這樣寫，
>    同樣是捏造，同樣丟掉。這條在文件裡沒寫，是實作時才發現的對稱情況。
> 4. **篩子對模型與 fallback 一視同仁**（`screen_matches` 兩條路都跑）。
>    grounding violation rate 要維持 0，唯一可靠的做法是讓不合格的句子進不了 DB，
>    而不是事後量它有多少。
> 5. **候選摘要故意不含 `tier`。** `tier` 帶付費成分，給了模型就等於給它一個
>    「這家有付錢」的事實可以當成推薦理由（COMPLIANCE §5：商業排序要分開標示，
>    不能包裝成 fit）。
> 6. **`fit_score` 在 fallback 裡是名次的換算，不是判斷。** 沒有人讀過買家那段話，
>    所以 `confidence=0.3`，畫面上也不顯示任何分數。
> 7. **候選池空的時候不呼叫模型**（`match_providers` 回 `None`），
>    RFQ 狀態不是 `open` 的時候 task 直接 `skipped`。
> 8. **寫入只有 `Rfq.matches` 一欄**（JSON，advisory）。這份清單不給任何公司任何身分：
>    需求牆不變、每家已認領公司照樣可以報價、公司端看不到自己有沒有被推薦。
>    畫面在 `templates/components/provider_matches.html`，只出現在買家自己的頁面，
>    底部固定寫明「由 AI 生成、未經人工審閱、不代表推薦」。

---

## A3. Nnc1ExtractionAgent — NNC1 文件抽取

**用途**：從用戶上傳的 NNC1（法團成立表格）抽出公司秘書資料，用來核驗評價真實性。

> **實作狀態（P4-3）**：`apps/agents/nnc1_extraction.py`，prompt `prompts/nnc1_extract_v1.md`。
> 由 `reviews.tasks.process_nnc1` 在病毒掃描與規則式姓名比對之後才派發，只寫
> `Nnc1Verification.extracted / extraction_confidence / agent_run_id_ref` 三欄。
> **比對邏輯與 `result` 仍然 100% 由規則寫**（P4-2 的 `run_name_match` / `decide_verification`），
> agent 的讀數只是擺在旁邊給審核員對照的第二意見。
> 已決案（`is_decided`）不會再讀一次——重讀一份已經有人做過判斷的文件，只會製造推翻它的誘因。
>
> **刻意不告訴模型答案**：prompt 裡不含上傳者填的 `declared_secretary_name`。
> 給了就不是抽取而是確認，那一欄的價值正正在於它獨立於待驗證的宣稱
> （`test_the_declared_name_is_never_shown_to_the_model`）。
>
> **Fallback 的讀數是空的**（全 `None`、`confidence=0.0`、`quality_issues=["not_read"]`），
> 且 `document_looks_authentic=True`——沒讀到的文件是沒讀到，不是偽造。

- Model: `claude-haiku-4-5-20251001`（vision，直接吃 PDF/圖片）
- 前置：檔案 ≤ 10MB，PDF/JPG/PNG；先做病毒掃描；存 S3 加密；`purge_at = +90d`。
- Output schema:
```python
class Nnc1Out(BaseModel):
    company_name_en: str | None
    company_name_zh: str | None
    company_number: str | None
    incorporation_date: date | None
    secretary_name: str | None
    secretary_licence_no: str | None  # TC 開頭
    secretary_is_corporate: bool
    document_looks_authentic: bool
    quality_issues: list[str]  # "blurry", "partial_page", "not_nnc1"
    confidence: float
```
- **比對邏輯（規則，不是 LLM）**：
  1. `secretary_licence_no` exact match `Licensee.licence_no` → `pass`
  2. 否則 `secretary_name` 對 `name_en`/`name_zh` 做 trigram similarity ≥ 0.88 → `pass`
  3. similarity 0.70–0.88 或 `confidence < 0.8` → `needs_human`
  4. 其餘 → `fail`
- **紅線**：AI **不判斷文件真偽作為最終結論**；`document_looks_authentic=false` 只會把案件轉 `needs_human`。
- Fallback: 直接轉 `needs_human`，通知審核員。
- Eval：20 份去識別化樣本，licence_no 抽取準確率 ≥ 0.95；false-pass rate = 0（寧可 needs_human）。
  **⚠️ 這 20 份樣本尚未取得**，所以 A3 目前只有單元測試沒有 eval。
  上線前必須補齊——現在把 `AGENT_ENABLED_NNC1_EXTRACTION` 打開，等於在沒有量過準確率的情況下
  把讀數擺到審核員眼前，而審核員會相信它。

---

## A4. ReviewModerationAgent — 評價審核

**用途**：新評價進來時做風險分類，**排序人工佇列**。

> ### ⚠️ 實作偏離本文件（P4-3，刻意的）
>
> 本節原本寫「`severity=none` 且 `confidence ≥ 0.8` 且該用戶已驗證 → auto-publish」。
> **實作沒有做這件事：A4 永遠不會發佈任何評價。** 原因有兩層，兩層都比省下人力重要：
>
> 1. CLAUDE.md 規則 3——AI 產出永不直接落 DB 成為事實。「這則評價是安全的」正是一個事實宣稱。
> 2. P4-1 的 `reviews.services.publish_review` 要求**具名審核員 + 必填理由**。那個簽名不是裝飾：
>    一則評價會永久掛在一間持牌公司的頁面上，出事時平台要答得出「是誰放行的、憑什麼」。
>    讓 agent 繞過它，等於讓最有法律風險的一類內容走最沒有人負責的一條路。
>
> 所以實作把 `recommended_action` 降格為**建議**，只寫進 `Review.moderation`（JSON），
> 評價維持 `pending_moderation`，`moderated_by` 維持 `None`。
> 實際被用到的是 `escalation_reason()`：
> `high_severity` > `defamation_risk` / `personal_data_leak` > `no_agent_reading` > `routine`，
> 用來**排序**審核員的佇列（`URGENT_REASONS` 是要先看的那批）。A4 決定的是**看的順序**，不是結果。
>
> 這個取捨是可以改的：日後若你要開 auto-publish，那是一個開關 + 一條政策決定
> （誰在法律上為 agent 放行的評價負責），不是重寫。要開再跟我說。
>
> **實作狀態**：`apps/agents/review_moderation.py`，prompt `prompts/moderation_v1.md`，
> 由 `reviews.services.submit_review` 的 `transaction.on_commit` 派發。
> 送進模型的 body 是 `redaction.redact()` 之後的版本——A4 不是 COMPLIANCE §4 的例外，
> 而且遮蔽用的是 `[PHONE]` 這類佔位符而非刪除，模型才看得出「這裡本來有個電話」。

- Model: `claude-sonnet-5`
- Output schema:
```python
class ModerationOut(BaseModel):
    labels: list[
        Literal[
            "defamation_risk",
            "unsubstantiated_claim",
            "personal_data_leak",
            "spam_or_ad",
            "competitor_attack",
            "off_topic",
            "profanity",
            "guarantees_bank_success",
            "looks_like_pr_copy",
            "non_specific",
        ]
    ]
    severity: Literal["none", "low", "medium", "high"]
    reasons: list[str]
    suggested_redactions: list[str]  # 要遮蔽的原文片段（如電話、人名）
    recommended_action: Literal["publish", "human_review", "reject"]
    confidence: float
```
- **決策規則（規則優先於 AI）**：
  - `severity=high` 或含 `personal_data_leak` / `defamation_risk` → **一律人工**，不論 AI 建議。
  - `severity=none` 且 `confidence ≥ 0.8` 且該用戶已驗證 → auto-publish。
  - 其餘 → 人工佇列。
- **絕不自動刪除**評價；最嚴重也只是 `hidden` + 通知作者。
- Fallback: 全部進人工佇列。規則式 fallback 抓得到的只有三類——
  `redact()` 前後不相等 → `personal_data_leak`；命中 banned phrase → `guarantees_bank_success` /
  `unsubstantiated_claim`；過短 → `non_specific`。`confidence` 固定 0.3，
  審核員一眼看得出這是關鍵字比對而不是誰讀過。
- Eval：對 50 條標註樣本，high-severity recall ≥ 0.95（漏放誹謗最貴）。
  **目前只有 `evals/review_moderation/golden.jsonl` 22 筆合成樣本**（簡中／英文混合，6 筆應升級），
  門檻寫在 `evals/runner.py`：`ESCALATION_RECALL_THRESHOLD = 0.95`、
  `MAX_FALSE_ESCALATION_RATE = 0.35`（誤升級只是浪費審核時間，漏升級是上法庭）。
  合成樣本量的是「規則有沒有壞」，不是「模型準不準」——50 筆真實標註仍然欠著。
  eval 測試掛 `@pytest.mark.eval`，沒有真 API key 時 skip，所以 CI 不會因為沒 key 而紅。

---

## A5. QuoteAnalysisAgent — 報價分析

**用途**：把秘書公司提交的報價正規化，揪出隱藏費用與異常低價。

> ### ⚠️ 實作偏離本文件（P5-3，刻意的）
>
> 1. **只分析 HKD 報價**。其他貨幣直接 `skipped`，連 `AgentRun` 都不寫——
>    市場分位數是用 HKD 算的，拿 CNY 的總額去比 HKD 的 p10 會得出一個看起來
>    很便宜的假結論，而那個結論會顯示給買家看。
> 2. **`total_renewal_hkd` 可為 `None`**（本節寫的是 `int`）。很多報價根本沒寫續期費，
>    那時候 0 是錯的答案：0 的意思是「續期免費」。`None` 的意思是「他沒說」，
>    而「他沒說」正是這個 agent 要買家去問的東西。
>
> **實作狀態**：`apps/agents/quote_analysis.py`，prompt `prompts/quote_analysis_v1.md`。
> 由 `rfq.services.submit_quote` 的 `transaction.on_commit` 派發，只寫 `Quote.analysis`
> 一欄（JSON，建議性質），**不動報價金額、不動狀態、不排序**。
> 買家看到的仍然是公司自己填的價，agent 只是旁邊那幾條要問的問題。
>
> **分位數由 SQL 算**：`rfq.selectors` 用 Postgres `PERCENTILE_CONT` 算同類服務的
> p10/p50/p90，樣本少於 `MIN_PERCENTILE_SAMPLE = 8` 就回空 dict，
> prompt 裡明講「Not enough comparable quotes」。這一條有測試把每個坑都釘住：
> 一張報價不會跟自己比、撤回的報價不是價格、兩種貨幣不混在一起
> （`test_selectors.py::TestMarketPercentiles`）。
> **沒有市場數字與「不低於市場」必須答得一樣**——不然「沒得比」會被讀成「便宜」。

- Model: `claude-sonnet-5`
- Input: `QuoteLineItem[]` + RFQ 需求 + 該服務類別的市場價分位數（由平台自算，非 LLM）
- Output schema:
```python
class QuoteAnalysisOut(BaseModel):
    normalized_items: list[NormalizedItem]  # 對映到標準 label enum
    missing_common_items: list[str]  # 如 "政府注册费"、"商业登记证费"、"首年秘书费"
    hidden_fee_risks: list[HiddenFeeRisk]  # {item, why, est_amount_hkd|None}
    completeness_score: float  # 0-1
    total_first_year_hkd: int
    total_renewal_hkd: int
    flags: list[Literal["below_market_p10", "missing_govt_fee", "vague_scope", "short_validity"]]
    buyer_questions: list[str]  # 建議買家追問的 3 條，簡體中文
```
- **價格分位數由 SQL 算**，不讓 LLM 估市場價（會幻覺）。
- 前台顯示 `flags` 時措辭要中性：「此报价未列明政府规费，建议向服务商确认」，**不可寫「此公司不可信」**。
- Fallback: 用標準項目清單做 set difference 找 missing items，`flags` 只留規則能判的。
- Eval：對 25 份真實報價，missing_govt_fee 偵測 precision ≥ 0.9
  （`MISSING_GOVT_FEE_PRECISION_THRESHOLD`；選 precision 不選 recall，
  因為一個常常喊錯的警示，買家很快就學會不看）。
  **目前只有 `evals/quote_analysis/golden.jsonl` 22 筆合成報價**，其中 q03／q13
  刻意只在附言裡用中文寫「已包含政府规费」——規則判不出來，模型應該讀得出來。
  規則式 fallback 在這個 set 上 precision 0.56 / recall 1.00，**是刻意多喊**；
  所以 fallback 的測試只量 recall。25 份真實報價仍然欠著，分數見 `evals/RESULTS.md`。

---

## A6. AdvisorAgent — 教育問答（RAG）

**用途**：回答「開公司要多久」「開戶被拒怎麼辦」「NNC1 是什麼」等問題。

- Model: `claude-sonnet-5`
- **只能引用平台自有內容**：檢索 `content.Chunk` Top 8 → 塞進 context。檢索一律走
  `apps.content.selectors.search_chunks()`（只回已發布文章的段落）。**現況**：`embedding`
  仍是 NULL，該函式先用 `text__icontains`；換成 pgvector 相似度時只改它一個地方，
  agent 這邊不用動（見 ROADMAP 的 `[P6] 指南的檢索還不是向量檢索`）。
- Output schema:
```python
class AdvisorOut(BaseModel):
    answer_zh_hans: str
    citations: list[Citation]  # {article_slug, chunk_ordinal, quote}
    confidence: float
    out_of_scope: bool
```
- **硬性規則**：
  - `citations` 為空 → **不回答**，改回「這個問題我們的資料庫暫時沒有可靠答案，建議諮詢持牌專業人士」。
  - 禁止提供法律、稅務、投資意見 → 命中關鍵詞（稅務籌劃、避稅、離岸豁免、投資回報）自動加免責並降級為「一般資訊」。
  - 每則回答尾部強制附：「以上為一般資訊，不構成法律或專業意見。」
  - 禁止推薦具體某一家秘書公司（避免變成中介行為）→ banned phrase filter 檢查是否出現 Provider 名稱。
- Fallback: 回傳檢索到的 Top 3 段落（原文摘錄 + 文章連結），不生成任何句子。
- Eval：`evals/advisor/golden.jsonl` 32 筆，citation-grounded rate = 1.0
  （`ADVISOR_GROUNDING_RATE_THRESHOLD`，不得放寬）；答不了的問題正確拒答率 ≥ 0.9
  （`ADVISOR_REFUSAL_RATE_THRESHOLD`）；答得了的問題回答率 ≥ 0.7
  （`ADVISOR_ANSWER_RATE_THRESHOLD`——全部拒答的 agent 前兩個分數都是滿分，卻一文不值）。

### 實作偏離（A6）

1. **拒答是整段丟掉，不是改寫。** `screen_answer()` 一旦判定引文不成立／命中 banned
   phrase／點名了持牌公司，整個回答換成 `refusal()`，不編輯句子——一句「推薦某公司」
   改幾個字仍然是推薦。
2. **公司名比對的是官方名單，不是 Provider 名稱。** 規格寫「檢查是否出現 Provider 名稱」，
   實作改為比對 `registry.Licensee` 的 `name_en`／`name_zh`（≥ 6 字，快取 5 分鐘）：
   規則是「不點名任何持牌公司」，而持牌公司的清單就是官方名單，不是我們收錄了誰。
3. **引文是逐字核對的。** `is_grounded()` 把引文與該段原文都去掉空白後做子字串比對；
   模型換行不算改字，其餘一律視為虛構。
4. **檢索不是整句 `icontains`。** 中文整句比對等於什麼都比不到，所以
   `selectors.query_terms()` 先把問題切成重疊 bigram 再 OR 查詢、在 Python 重排。
   換 pgvector 時仍然只改 `search_chunks()`。
5. **問答入口要登入且有節流。** `content:ask` 是 `@login_required` + 每帳號每小時 12 條
   （cache 計數，不落 DB——問題本身不留紀錄，COMPLIANCE §4）。檢索不到段落時
   **不呼叫模型**，直接拒答。

---

## A7. RegistryDiffAgent — 名單變動摘要

**用途**：每日同步後，把 `LicenseeChange` 轉成人類可讀的營運告警。

- Model: `claude-haiku-4-5-20251001`
- Input: 當日 diff 列表 + 每筆是否關聯到已認領／付費 Provider
- Output schema:
```python
class DiffDigestOut(BaseModel):
    headline: str
    critical_items: list[CriticalItem]  # {licence_no, provider_name, what, why_it_matters, action}
    routine_summary: str
    counts: dict[str, int]
```
- **升級規則（規則決定 severity，AI 只寫文案）**：
  - 已付費 Provider 的牌照 `removed` → `critical` + 立即 email 營運 + **自動下架該 Provider 的付費曝光**（自動化這一步是允許的，因為方向是保守的）。
  - 已認領 Provider 名稱／地址變更 → `warn`，要求重新驗證。
  - 新增／移除未認領公司 → `info`。
- Fallback: 用模板生成純數字摘要。

---

## Agent 觀測與治理

`/admin/agents/` 後台必須有：
- 每個 agent 的 24h 呼叫數、成功率、fallback 率、p95 latency、累計 cost
- schema 驗證失敗率（> 5% 就是 prompt 出問題）
- 人工覆核的 `AgentFeedback` 分佈（correct / partially / wrong）
- 每日成本 vs `AGENT_BUDGET_DAILY_USD` 進度條

**上線 gate**：任何 agent 開到生產前，必須 (a) eval 通過門檻，(b) fallback 有測試，(c) 後台可見，(d) 有 kill switch（`AGENT_ENABLED_{NAME}` 環境變數）。
