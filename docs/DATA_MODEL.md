# DATA_MODEL — 權威資料模型

> 改動任何 model 前先改這份文件，再寫 migration。

所有 model 繼承 `core.BaseModel`：`id (UUIDv7)`, `created_at`, `updated_at`。

## registry（官方數據，唯讀區）

### 官方 CSV 實際欄位（2026-08-13 驗證，7,457 列，UTF-8 無 BOM、CRLF）

| CSV 欄位 | 對映 model 欄位 |
|---|---|
| `Licence No.(牌照編號)` | `licence_no` |
| `Name of TCSP Licensee in English(持牌人的英文姓名／名稱)` | `name_en` |
| `Name of TCSP Licensee in Chinese(持牌人的中文姓名／名稱)` | `name_zh`（2,037 列為空） |
| `Business Address(營業地址)` | `business_address` |
| `Remarks in English(英文備註)` | `remarks_en`（291 列為《受託人條例》第 78(1) 條註冊之信託公司） |
| `Remarks in Chinese(中文備註)` | `remarks_zh` |

> 官方 CSV **沒有** status、發牌日期或到期日欄位。`status` 由「是否出現於當次名單」推導。
> 標頭比對只取英文部分（`normalise_header`），中文標點改動不會弄壞匯入。

### Licensee
| 欄位 | 型別 | 說明 |
|---|---|---|
| `licence_no` | `CharField(unique=True, db_index=True)` | 官方牌照編號，**天然主鍵** |
| `name_en` / `name_zh` | `CharField` | 官方名稱（`name_zh` 可為空字串） |
| `business_address` | `TextField` | 官方地址原文（已 NFKC normalize，原值在 `raw`） |
| `remarks_en` / `remarks_zh` | `TextField(blank=True)` | 官方備註 |
| `district` | `CharField(blank=True, default="")` | 由地址解析（enrich，未識別時為空字串；實測覆蓋 94.4%） |
| `status` | `CharField` | `active` / `inactive`（推導，非官方欄位） |
| `first_seen_at` / `last_seen_at` | `DateTimeField` | 出現於官方名單的首次／最後一次 |
| `raw` | `JSONField` | 官方原始 row，永不修改 |
| `last_synced_at` | `DateTimeField` | UI 必須顯示 |

> **規則**：除 `sync` 任務外，任何 code 不得寫入本表。人工 enrich 一律放 `providers`。
> 執行面：`Licensee.save/delete` 與 `LicenseeQuerySet.update/delete/bulk_create/bulk_update`
> 只在 `allow_registry_writes()`（ContextVar）內才放行，admin 亦註冊為唯讀。

> **牌照從官方名單消失 ≠ 從平台消失**：`status` 轉 `inactive`，資料列保留，
> 目錄與搜尋（`selectors.listed_licensees` / `search_licensees`）**仍會列出**，只是排在後面，
> 並強制顯示 `notices.deregistration_notice()`。撮合資格（RFQ、報價權）另用
> `selectors.active_licensees()`，只含仍在名單者 —— 兩個集合刻意不同。
> 官方名單不載明移除原因也沒有日期欄，因此平台唯一可陳述的事實是
> 「最後一次出現於官方名單的日期」（`Licensee.deregistered_since` = `last_seen_at`）。
> 不得使用「吊銷／撤銷／除牌／違規」等暗示執法行動的措辭（COMPLIANCE §3）。

> **`LicenseeSnapshot` 不實作**：ARCHITECTURE §5 提過，但本文件（權威）從未定義它。
> 每次同步的完整 `raw` row 加上 `LicenseeChange` 已能還原歷史，另存 7,457 列／日的快照
> 只是純儲存成本。若日後需要「任意時點完整名單」再引入。

### SyncRun
`source_url`, `started_at`, `finished_at`, `status(running|success|failed|aborted_sanity)`, `row_count`, `prev_row_count`, `checksum(sha256)`, `raw_file_key`, `error`, `is_dry_run`

> `is_dry_run` 為 P1 新增：dry run 也要留紀錄，但不得成為下次 sanity check 的基準。

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
`rating_cached(Decimal 3,2, null)`, `rating_count(int)`, `verified_review_count(int)`
`is_published(bool)`, `commission_agreement(bool)` ← 若 true，頁面必須顯示披露

排序快取欄位（P2 加，由 `providers.services.recompute_ranking_inputs` 寫入）：
`profile_completeness(Decimal 4,3)`、`responsiveness_score(Decimal 4,3)`、`ranking_score(Decimal 6,4, indexed)`

- `rating_cached` 是 **null 而非 0**：「還沒有已驗證評價」不是「分數為零」。RATING_SYSTEM §4
  禁止在這種情況顯示數字，`ranking_score` 也不會拿貝氏先驗的 5.00 去排序（見 §5 實作）。
- `ranking_score` 是反正規化欄位：RATING_SYSTEM §5 的權重混合四個 app 的輸入，列表頁必須在
  DB 內排序與分頁，不能在 Python 算完再切頁。
- `profile_completeness` 的定義是 `services.COMPLETENESS_FIELDS` 中「有填」的比例——刻意只算
  公司自己能控制的欄位，因為這個數字餵給公開排序，被問「為什麼我排這麼後面」時要解釋得出來。

**每個 Licensee 都有一個 Provider**：`services.ensure_providers` 在每日同步後半小時單獨跑
（`providers.backfill_providers`，06:30 HKT），把新出現的持牌人建成 `unclaimed` 頁面。
它與同步分開排程，因為平台層的失敗不可以回滾或拖延官方檔的鏡像。
slug 為 `slugify(name_en) + "-" + licence_no`：登記冊裡真的有同名公司，而用流水號會讓
同一份檔案在不同機器上產生不同 URL。

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

DB 約束 `providers_price_point_or_range`：必須是單點價或完整區間，兩者皆空會被拒——
比較表上的空格會被讀成「免費」。

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
