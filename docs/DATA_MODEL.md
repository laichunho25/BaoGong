# DATA_MODEL — 權威資料模型

> 改動任何 model 前先改這份文件，再寫 migration。

所有 model 繼承 `core.BaseModel`：`id (UUIDv7)`, `created_at`, `updated_at`。

## registry（官方數據，唯讀區）

### Licensee
| 欄位 | 型別 | 說明 |
|---|---|---|
| `licence_no` | `CharField(unique=True, db_index=True)` | 官方牌照編號，**天然主鍵** |
| `name_en` / `name_zh` | `CharField` | 官方名稱 |
| `business_address` | `TextField` | 官方地址原文 |
| `district` | `CharField(null=True)` | 由地址解析（enrich，可為空） |
| `status` | `CharField` | `active` / `inactive` |
| `first_seen_at` / `last_seen_at` | `DateTimeField` | 出現於官方名單的首次／最後一次 |
| `raw` | `JSONField` | 官方原始 row，永不修改 |
| `last_synced_at` | `DateTimeField` | UI 必須顯示 |

> **規則**：除 `sync` 任務外，任何 code 不得寫入本表。人工 enrich 一律放 `providers`。

### SyncRun
`source_url`, `started_at`, `finished_at`, `status(success|failed|aborted_sanity)`, `row_count`, `prev_row_count`, `checksum`, `raw_file_key`, `error`

### LicenseeChange
`sync_run(FK)`, `licence_no`, `change_type(new|removed|reactivated|renamed|address_changed)`, `before(JSON)`, `after(JSON)`, `severity(info|warn|critical)`, `ai_summary(TextField, null)`, `notified_at`

---

## providers（平台 enrich 區）

### Provider
`licensee = OneToOne(Licensee)`（可 null，允許尚未持牌之候選但預設不公開）
`claim_status`: `unclaimed | pending | claimed | rejected`
`tier`: `free | verified | premium`
`slug(unique)`, `logo`, `website`, `founded_year`, `team_size`, `office_photos(JSON)`
`languages(ArrayField)`: `mandarin | cantonese | english`
`supports_simplified(bool)`, `remote_onboarding(bool)`
`bank_account_support(bool)`, `bank_types(ArrayField: traditional|virtual|emi)`
`non_resident_shareholder_experience(bool)`
`industry_specialties(ArrayField)`
`rating_cached(Decimal 3,2)`, `rating_count(int)`, `verified_review_count(int)`
`is_published(bool)`, `commission_agreement(bool)` ← 若 true，頁面必須顯示披露

### ProviderClaim
`provider(FK)`, `submitted_by(FK User)`, `evidence_files(JSON)`, `business_registration_no`,
`website_verification_token`, `status(pending|approved|rejected)`, `reviewer(FK)`, `reviewed_at`, `notes`
`ai_risk_flags(JSON)` ← AI 建議，非決策

### ServiceOffering
`provider(FK)`, `category`: `incorporation | company_secretary | registered_address | accounting | audit_liaison | bank_account_assist | tax_filing | trademark | work_visa`
`description`, `is_active`

### PriceItem
`offering(FK)`, `label`, `currency(char3)`, `amount_minor(BigInteger)`, `unit(one_off|yearly|monthly|hourly)`,
`includes_govt_fee(bool)`, `min_amount_minor / max_amount_minor`（區間報價）, `effective_from`, `source(provider_declared|quote_derived|platform_survey)`

### Certification
`provider(FK)`, `type(tcsp_licence|office_verified|website_verified|track_record|premium_badge)`,
`verified_at`, `expires_at`, `evidence_ref`, `verified_by(FK User)`

---

## reviews

### Review
`provider(FK)`, `author(FK User)`, `overall(Decimal 2,1)`（由子分計算）
`is_verified(bool)` ← 只有 NNC1 核驗通過才 true
`status`: `pending_moderation | published | hidden | removed`
`body(TextField)`, `service_used(ArrayField)`, `engagement_year(int)`
`moderation(JSON)` ← Moderation Agent 輸出：`{labels, severity, reasons, model, run_id}`
`helpful_count`, `published_at`

### ReviewScore（子分，1–5，step 0.5）
`review(OneToOne)`, `price_transparency`, `responsiveness`, `bank_support`, `professionalism`, `after_sales`

### Nnc1Verification
`review(OneToOne)`, `file_key`（加密 S3）, `uploaded_at`, `purge_at`（預設 +90d）
`extracted(JSON)`: `{company_name, company_no, secretary_name, secretary_licence_no, incorporation_date}`
`extraction_confidence(Decimal)`, `matched_licence_no`, `match_method(exact|fuzzy|manual)`,
`result(pass|fail|needs_human)`, `reviewed_by(FK, null)`, `agent_run(FK AgentRun)`

### ReviewReply
`review(OneToOne)`, `provider(FK)`, `body`, `published_at`（每則評價只能回覆一次）

### Dispute
`review(FK)`, `raised_by(FK)`, `reason`, `evidence(JSON)`, `ai_arbitration_draft(TextField)`,
`decision(keep|hide|amend|remove)`, `decided_by(FK)`, `decided_at`

---

## rfq

### Rfq
`buyer(FK User)`, `title`, `raw_input(TextField)`（用戶原話）
`structured(JSON)` ← RFQ Intake Agent 輸出，經用戶確認後鎖定
`company_type`, `shareholder_nationalities(Array)`, `business_nature`, `services_needed(Array)`,
`budget_min_minor / budget_max_minor`, `currency`, `timeline`, `needs_bank_account(bool)`, `preferred_banks(Array)`
`status`: `draft | open | closed | awarded | expired`
`visibility(public|invited_only)`, `expires_at`

### Quote
`rfq(FK)`, `provider(FK)`, `first_year_total_minor`, `renewal_total_minor`, `currency`,
`includes_govt_fee(bool)`, `delivery_days(int)`, `validity_days`, `message`,
`analysis(JSON)` ← Quote Analysis Agent：`{hidden_fee_risks[], completeness_score, price_percentile, flags[]}`
`status(submitted|shortlisted|accepted|withdrawn|expired)`

### QuoteLineItem
`quote(FK)`, `label`, `amount_minor`, `unit`, `is_optional`, `note`
（標準化 label 用 enum，讓比較表同口徑）

### QuotaLedger
`provider(FK)`, `date`, `free_used(int)`, `paid_used(int)`, `paid_balance(int)`
規則：每日 `free_allowance = 3`；先扣免費再扣付費；`unique(provider, date)`

---

## agents

### AgentRun
`agent_name`, `model`, `prompt_version`, `input_hash(sha256)`, `input_ref(JSON, 去識別化)`,
`output(JSON)`, `status(ok|invalid_schema|timeout|error|fallback)`, `confidence(Decimal, null)`,
`input_tokens`, `output_tokens`, `cost_usd(Decimal 10,6)`, `latency_ms`, `error`,
`object_type / object_id`（generic link 到觸發對象）

### AgentFeedback
`agent_run(FK)`, `reviewer(FK)`, `verdict(correct|partially|wrong)`, `notes` ← 餵回 eval set

---

## billing

`Plan(code, name, price_minor, currency, interval, features JSON)`
`Subscription(provider, plan, status, current_period_end, external_ref)`
`CreditPack(provider, credits, price_minor, purchased_at, remaining)`
`Invoice(...)`
`CommissionDisclosure(provider, rate_bps, effective_from, public_note)` ← 前台必須渲染

---

## content

`Article(slug, title_zh_hans, title_zh_hant, title_en, body_md, category, published_at, seo JSON)`
`Chunk(article FK, ordinal, text, embedding vector(1536))` ← pgvector，供 Advisor Agent RAG
`Faq(question, answer, tags)`

---

## 關鍵 DB 約束

```sql
UNIQUE (registry_licensee.licence_no)
UNIQUE (providers_provider.licensee_id)
UNIQUE (reviews_review.provider_id, reviews_review.author_id)   -- 一人一公司一評
UNIQUE (rfq_quote.rfq_id, rfq_quote.provider_id)                -- 一單一報價
UNIQUE (rfq_quotaledger.provider_id, rfq_quotaledger.date)
CHECK  (reviews_reviewscore.* BETWEEN 1 AND 5)
CHECK  (amount_minor >= 0)
INDEX  GIN on Licensee(name_en gin_trgm_ops), Licensee(name_zh gin_trgm_ops)
INDEX  ivfflat on content_chunk(embedding vector_cosine_ops)
```
