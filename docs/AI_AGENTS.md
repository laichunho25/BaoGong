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

---

## A1. RfqIntakeAgent — 需求解析

**用途**：把買家的自然語言（常見於微信貼過來的一段話）轉成結構化 RFQ。

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

---

## A3. Nnc1ExtractionAgent — NNC1 文件抽取

**用途**：從用戶上傳的 NNC1（法團成立表格）抽出公司秘書資料，用來核驗評價真實性。

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

---

## A4. ReviewModerationAgent — 評價審核

**用途**：新評價進來時做風險分類，決定 auto-publish / 轉人工 / 直接擋。

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
- Fallback: 全部進人工佇列。
- Eval：對 50 條標註樣本，high-severity recall ≥ 0.95（漏放誹謗最貴）。

---

## A5. QuoteAnalysisAgent — 報價分析

**用途**：把秘書公司提交的報價正規化，揪出隱藏費用與異常低價。

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
- Eval：對 25 份真實報價，missing_govt_fee 偵測 precision ≥ 0.9。

---

## A6. AdvisorAgent — 教育問答（RAG）

**用途**：回答「開公司要多久」「開戶被拒怎麼辦」「NNC1 是什麼」等問題。

- Model: `claude-sonnet-5`
- **只能引用平台自有內容**：pgvector 檢索 `content.Chunk` Top 8 → 塞進 context。
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
- Fallback: 回傳 FTS 搜到的 Top 3 文章連結，不生成文字。
- Eval：30 條問題，citation-grounded rate = 1.0；out-of-scope 正確拒答率 ≥ 0.9。

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
