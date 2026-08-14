# DATA_MODEL — 權威資料模型

> 改動任何 model 前先改這份文件，再寫 migration。

所有 model 繼承 `core.BaseModel`：`id (UUIDv7)`, `created_at`, `updated_at`。

## accounts（身分與授權）

### User（`AUTH_USER_MODEL`）
`email(unique, USERNAME_FIELD)`, `role(buyer|provider|moderator|admin)`, `phone`,
`preferred_language`, `email_verified_at`, 以及 `AbstractUser` 的 `is_staff / is_active / …`
（`username` 已移除）。

- Email 是登入身分：秘書公司換人接手時，帳號是跟著信箱走的。
- `email_verified_at` 為 null 代表「還沒證明自己收得到這個信箱」，認領流程（見
  `ProviderClaim`）在此之前一律擋下——否則任何人都能用別人的信箱申請控制一間公司的頁面。

### EmailVerification
`user(FK)`, `token_hash(unique)`, `email`, `expires_at`, `used_at`

只存 token 的 SHA-256：DB 外洩時不應該連帶交出可用的驗證連結。

### ProviderMember
`user(FK)`, `provider(FK providers.Provider)`, `member_role(owner|manager|staff)`,
`is_active(bool, indexed)`, `claim(FK ProviderClaim, null)`
`UNIQUE (user, provider)`、`INDEX (provider, is_active)`

**這張表就是全站的權限模型。** 平台只問兩個問題：「這個人是不是這間 provider 的成員」與
「是不是 moderator（`User.role`）」，因此不裝 django-guardian、不做 per-object ACL 表——
那會多出一整組要維護與備份的權限資料，換來用不到的彈性。判斷寫在
`apps/accounts/permissions.py`（`is_provider_member` / `member_providers` /
`moderator_required`）。
`is_active=False` 與「從來不是成員」等價：公司請走員工時用停用，不刪列，紀錄要留。
`claim` 記錄這個成員資格是由哪一份認領申請授予的。

---

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
申請人資料：`provider(FK)`, `submitted_by(FK User)`, `contact_name`, `contact_role`,
`contact_phone`, `business_registration_no`, `applicant_note`
（表單另有「確認獲授權」勾選，屬送出前檢查，不存欄位）
網站所有權：`website`, `website_verification_token(indexed)`, `website_verified_at`,
`website_verification_method(dns_txt|well_known|meta_tag)`, `website_verification_log(JSON)`
決策：`status(pending|approved|rejected|withdrawn)`, `reviewer(FK User)`, `reviewed_at`,
`decision_reason(TextField)`, `notes`（內部，永不對申請人顯示）
`ai_risk_flags(JSON)` ← AI 建議，非決策（CLAUDE.md §4.3）

`UNIQUE (provider) WHERE status='pending'`（`providers_one_pending_claim_per_provider`）：
沒有它，同一頁可以被兩份申請並行認領，第二次批准會無聲地把同一個 profile 交給第二間公司。
`INDEX (status, created_at)` 給 moderator 佇列用。

- **BR 號碼不是官方持牌名單的欄位**，只供審核人核對，不公開顯示（rule 1：registry 唯讀，
  enrich 資料一律留在 providers）。
- 網站驗證是**證據，不是放行條件**：token 只證明申請人控制那個網域，證明不了那個網域屬於
  持牌人。批准與否仍由 moderator 決定，理由必填。
- 批准時 `services.approve_claim` 一次交易內做三件事：建 `ProviderMember(owner)`、
  `provider.claim_status = claimed`、發 `Certification(tcsp_licence)`。

### ClaimEvidence
`claim(FK)`, `kind(business_registration|address_proof|authorisation|other)`,
`file(FileField, private storage)`, `original_filename`, `content_type`, `extension`,
`size_bytes`, `sha256(indexed)`
掃描：`scan_status(pending|clean|infected|error|skipped, indexed)`, `scan_detail`, `scanner`,
`scanned_at`, `scan_override_by(FK User)`
保留期：`purge_at(indexed)`, `purged_at`

**一份檔案一列，不是 `ProviderClaim.evidence_files(JSON)`**（本文件早期版本如此規劃）：
每個檔案有自己的掃描狀態與自己的保留期，而清除任務與「這份可以打開嗎」都是逐檔判斷，
兩者都必須可查詢。

- 儲存路徑為 `claims/<claim_id>/<pk>.<sniffed ext>`：上傳者提供的檔名是攻擊者可控字串、
  且常含真人姓名，不該出現在會進 log 的 storage key，因此改存欄位。
- `is_readable` 才可預覽或下載：未掃描等同未清白，moderator 的瀏覽器不該是「發現這個檔有毒」
  的那一步。放行只能經 `scan_override_by` 這條有署名、有理由的路，且 `infected` 不得放行。
- `purge_at = 決策時間 + 90 日`（COMPLIANCE §4）。每日 Celery beat 刪掉 bytes，**保留列與
  sha256**：審核紀錄要留，個資不留。

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

`UNIQUE (provider, type)`（`providers_one_certification_per_type`）：同一種徽章兩列會在頁面上
重複渲染，也答不出「哪一個才是現行的」；續期是更新該列，不是新增。
`tcsp_licence` 由 `services.approve_claim` 在批准認領時發出，`evidence_ref` 指向該份申請。

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
UNIQUE (accounts_user.email)
UNIQUE (accounts_providermember.user_id, accounts_providermember.provider_id)
UNIQUE (providers_providerclaim.provider_id) WHERE status = 'pending'  -- 一頁一份待審申請
UNIQUE (providers_certification.provider_id, providers_certification.type)
UNIQUE (reviews_review.provider_id, reviews_review.author_id)   -- 一人一公司一評
UNIQUE (rfq_quote.rfq_id, rfq_quote.provider_id)                -- 一單一報價
UNIQUE (rfq_quotaledger.provider_id, rfq_quotaledger.date)
CHECK  (reviews_reviewscore.* BETWEEN 1 AND 5)
CHECK  (amount_minor >= 0)
INDEX  GIN on Licensee(name_en gin_trgm_ops), Licensee(name_zh gin_trgm_ops)
INDEX  ivfflat on content_chunk(embedding vector_cosine_ops)
```
