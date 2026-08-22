# ARCHITECTURE — 包公 BaoGong

## 1. 系統圖

```
                    ┌──────────────────────────────┐
  data.gov.hk /     │  Celery Beat (daily 06:00 HKT)│
  tcsp.cr.gov.hk ──▶│  sync_tcsp_registry task      │
                    └──────────┬───────────────────┘
                               ▼
  ┌────────────┐      ┌────────────────┐      ┌──────────────┐
  │  HTMX UI   │◀────▶│  Django 5.1    │◀────▶│ PostgreSQL 16│
  │  Tailwind  │      │  + DRF         │      │  + pgvector  │
  └────────────┘      └───┬────────┬───┘      └──────────────┘
                          │        │
                 ┌────────▼──┐  ┌──▼─────────┐
                 │  Celery   │  │ apps.agents│──▶ Anthropic API
                 │  Workers  │  │ BaseAgent  │    (sonnet/haiku/opus)
                 └────┬──────┘  └──┬─────────┘
                      │            │
                 ┌────▼────┐   ┌───▼──────────┐
                 │  Redis  │   │ AgentRun log │
                 └─────────┘   │ (cost/latency)│
                               └──────────────┘
   S3 / MinIO ◀── NNC1 uploads (encrypted, 90-day TTL)
```

## 2. 目錄結構（權威）

```
baogong/
├── config/
│   ├── settings/{base,dev,prod,test}.py
│   ├── celery.py
│   ├── urls.py
│   └── wsgi.py asgi.py
├── apps/
│   ├── core/          # BaseModel(uuid, created_at, updated_at), middleware, money, i18n utils
│   ├── accounts/      # User(AbstractUser), ProviderMember, ProviderMemberInvite, EmailVerification, permissions.py
│   ├── registry/      # Licensee, SyncRun, LicenseeChange
│   ├── providers/     # Provider, ProviderClaim, ClaimEvidence, ServiceOffering, PriceItem, Certification
│   ├── reviews/       # Review, ReviewScore, ReviewReply, Nnc1Verification, Dispute
│   ├── rfq/           # Rfq, Quote, QuoteLineItem, QuotaLedger
│   ├── agents/        # BaseAgent, registry, prompts/, tools/, AgentRun, evals/
│   ├── billing/       # Plan, Subscription, CreditPack, Invoice, CommissionDisclosure
│   └── content/       # Article, Chunk(pgvector), rendering.py（markdown → 消毒過的 HTML／可引用段落）
├── templates/         # base.html, components/（共用元件）, pages/, 各 app 的頁面
├── static/            # tailwind input.css → app.css（已進版控）, alpine, self-hosted fonts
├── tests/             # 跨 app 的 integration tests
├── docs/              # 本資料夾
├── .claude/skills/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml     # ruff, mypy, pytest config
└── .env.example
```

## 3. App 內分層（強制）

| 檔案 | 責任 | 禁止 |
|---|---|---|
| `models.py` | 欄位、約束、`__str__`、property | 不放 business logic |
| `selectors.py` | 所有讀取查詢，回傳 queryset/dataclass | 不寫入 |
| `services.py` | 所有寫入與 business logic，`@transaction.atomic` | 不碰 request/response |
| `views.py` | HTMX/HTML view，薄 | 不直接 ORM 寫入 |
| `api.py` | DRF viewsets/serializers | 同上 |
| `tasks.py` | Celery tasks，只 orchestrate 呼叫 services | 不放邏輯 |
| `admin.py` | 內部審核介面 | 規則不放這裡（見下） |

**首頁是四個 app 的 selector 組出來的**：`apps/core/views.py::home` 只做組裝，不含查詢邏輯——
`registry.market_snapshot`、`providers.service_summaries` / `popular_searches`、
`reviews.featured_reviews`、`rfq.matching_snapshot` / `open_rfqs`。這樣做的理由是首頁上的每個
數字都是一項對外宣稱：宣稱的定義留在擁有該資料的 app 裡（連同它的測試），首頁只負責排版。
唯一與「誰在看」有關的是需求預覽——數字公開，需求內容須登入（COMPLIANCE §4）。
介面本身的規範見 `docs/DESIGN_SYSTEM.md`。

**授權**：不裝 django-guardian。全站只有三個判斷——「是不是這間 provider 的成員」、
「是不是它的 owner（誰能決定成員名單）」與「是不是 moderator」——都由
`apps/accounts/permissions.py` 從 `ProviderMember` / `User.role` 回答。
存在與否本身即資訊的頁面（別人的認領申請、moderator 佇列）一律回 **404 而非 403**。

**審核佇列**：P3 的認領審核用**客製 Django admin**（並排顯示申請內容與官方登記冊、證明檔案
連結、批准／拒絕且理由必填），不自建介面——每日審核量是個位數，自建介面的成本留給 P4 的評價
審核佇列。規則（moderator 身分、檔案須經掃描、理由不得為空）一律寫在 `services.py`，admin
只是眾多呼叫者之一，因此換介面時規則不會跟著消失。

**通知**：`apps/core/notifications.py` + `core.send_notification` task，模板在 `templates/emails/`
（`<name>.subject.txt` 與 `<name>.txt` 分開，翻譯者不必猜哪一行是主旨）。三條規則：

1. **通知永遠不能推翻決定**。一律 `transaction.on_commit` 丟給 worker，SMTP 連不上不會
   把審核員的決定 rollback，也不會在資料落地前先寄出去。
2. **只帶結論與連結，不帶證據**。郵件會經過我們控制不了的中繼站，所以評價正文、NNC1 欄位、
   上傳檔名一律不進郵件（CLAUDE.md 規則 5）；要看內容請登入。
3. **理由一定跟著結論走**。每一處決定都強制填理由，就是因為有人在等這個理由；
   把它留在一個對方要自己想到才會回來看的頁面上，等於沒有寄。

撮合層加了第四條，只適用於這裡：**沒被選上的一方也要收到信**。`accept_quote` 對得標與落選的
公司寄同一個模板（`quote_decided`，靠 `chosen` 分岔），因為每一家都為這則需求付出了一次每日額度；
只寄給贏家的市集，等於讓其餘所有人付錢換沉默。

呼叫方式是 service 呼叫 `notify(template=..., recipients=..., context=...)`，
context 必須可 JSON 序列化（會在呼叫端就檢查，傳 model instance 要當場失敗，而不是四小時後
在 retry loop 裡失敗）。`NOTIFICATIONS_ENABLED` 只為資料回填而存在，不是生產開關。

## 4. AI Agent 架構

```
apps/agents/
├── base.py            # BaseAgent: run(), retry, timeout, fallback, AgentRun logging  ✅
├── registry.py        # AGENTS = {"review_moderation": ..., ...} 供 task/admin 取用    ✅
├── schemas.py         # pydantic models = LLM structured output 契約                    ✅
├── pricing.py         # Decimal 價目表 → cost_usd（CLAUDE.md 規則 6）                   ✅
├── redaction.py       # redact() / hash_input() / summarise_for_log()                   ✅
├── review_moderation.py   # A4                                                          ✅
├── nnc1_extraction.py     # A3                                                          ✅
├── prompts/
│   ├── moderation_v1.md    ✅
│   ├── nnc1_extract_v1.md  ✅
│   └── （matching / rfq_intake / quote_analysis / advisor / registry_diff 待 P5）
├── tools/             # 給 agent 用的 tool functions（查 DB、算分、查牌照）— 待 A2
├── models.py          # AgentRun, AgentFeedback                                         ✅
├── selectors.py       # runs_for(), spend_today(), health()                             ✅
├── services.py        # 唯一寫入者：moderate_review(), extract_nnc1(), record_feedback() ✅
├── admin.py           # 唯讀 run log + 三個 verdict action                              ✅
├── evals/             # golden set + pytest-based eval（`@pytest.mark.eval`）           ✅
└── tasks.py           # 只做編排                                                        ✅
```

`BaseAgent` 契約（P4-3 實作後的真實簽名）：

```python
class BaseAgent(ABC):
    name: str
    model: str
    prompt_file: str  # e.g. "moderation_v1.md"；stem 的 "_v1" 就是 prompt_version
    output_schema: type[BaseModel]
    max_tokens: int = 2048
    timeout_s: int = 30
    max_retries: int = 2
    backoff_base_s: float = 0.5
    object_type: str = ""  # e.g. "reviews.Review"，AgentRun 的 generic link

    def run(self, ctx: dict) -> AgentResult:
        """Hash input -> check kill switches & daily budget -> call LLM with a
        forced single tool -> validate against output_schema -> log AgentRun ->
        AgentResult(data, confidence, used_fallback, run_id, fallback_reason).
        Every failure path routes to self.fallback(ctx, reason); run() never raises."""

    @abstractmethod
    def build_user_prompt(self, ctx: dict) -> str | list[dict]:
        """Text, or content blocks for vision (A3 sends a document/image block)."""

    @abstractmethod
    def fallback(self, ctx: dict, reason: str) -> BaseModel:
        """Deterministic non-LLM path. MUST be implemented. Returns the same
        schema, so callers never branch on whether a model was involved."""
```

三點值得知道的實作決定：

- `fallback()` 回傳的是 **`output_schema` 本身**而不是 `AgentResult`。呼叫端因此不必寫
  「如果有模型就這樣、沒有就那樣」——那種分支正是規則路徑日後腐爛而沒人發現的地方。
  是不是模型答的，記在 `AgentResult.used_fallback` 與 `AgentRun` 裡，給人看，不給程式分支。
- `run()` **不會拋例外**。一個評價不該因為 Anthropic 掛了而卡在無人知曉的狀態。
- `_log()` 自己包在 try/except 裡：稽核寫入失敗不可以把已經拿到的答案弄丟。

**所有 agent 輸出都是「建議」**，寫入時一律進 `status=pending_review` 或 `is_ai_suggested=True`。
A4 的實作把這條推得更遠——它連 `recommended_action` 都不執行，只用來排序人工佇列，
理由見 `docs/AI_AGENTS.md` A4 節的偏離說明。

## 5. 資料流：每日 TCSP 同步

1. `sync_tcsp_registry` (Celery beat, 06:00 HKT)
2. 下載 CSV → 存原始檔到 S3（`raw/tcsp/YYYY-MM-DD.csv`）
3. **Sanity check**：筆數與上次相差 > 15% → 中止 + 告警，**不寫入**
4. Upsert `Licensee`（by `licence_no`）——`raw` 保留當次官方 row
   （原稿的 `LicenseeSnapshot` 不實作，理由見 DATA_MODEL.md registry 段）
5. Diff → 寫 `LicenseeChange`（new / removed / renamed / address_changed）
6. `RegistryDiffAgent` 對 change 產生人類可讀摘要 + 風險標記（如「已認領且付費的公司牌照消失」→ P0 告警）
7. 更新 `SyncRun(status, row_count, duration, checksum)`
8. 失效 cache、重建 search index

## 6. 環境變數（`.env.example`）

```
DJANGO_SETTINGS_MODULE=config.settings.dev
SECRET_KEY=
DATABASE_URL=postgres://baogong:baogong@db:5432/baogong
REDIS_URL=redis://redis:6379/0
ANTHROPIC_API_KEY=
AGENT_BUDGET_DAILY_USD=20
S3_ENDPOINT_URL= S3_BUCKET= S3_ACCESS_KEY= S3_SECRET_KEY=
TCSP_CSV_URL=https://www.tcsp.cr.gov.hk/open-data/licensees.csv
SENTRY_DSN=
TURNSTILE_SITE_KEY= TURNSTILE_SECRET=
ADMIN_URL= ADMIN_ENABLED= ADMIN_IP_ALLOWLIST= ADMIN_TRUST_PROXY_IP=
S3_PRIVATE_BUCKET= PRIVATE_FILE_URL_TTL=300
FILE_SCANNER_BACKEND=apps.core.scanning.UnavailableScanner
CLAMAV_HOST=clamav CLAMAV_PORT=3310
CLAIM_SITE_VERIFICATION_KEY=baogong-site-verification
SITE_URL=http://localhost:8000 DEFAULT_FROM_EMAIL= NOTIFICATIONS_ENABLED=true
DISPUTE_SLA_BUSINESS_DAYS=5
RFQ_QUOTA_FREE=3/month RFQ_QUOTA_VERIFIED=15/month RFQ_QUOTA_PREMIUM=40/month
RFQ_MAX_QUOTES_PER_REQUEST=8
RFQ_OPEN_DAYS=14
```

以 `.env.example` 為準，上表只是導覽。證明檔案存**私有 bucket**，只透過會過權限檢查的
download view 取用；backend 能簽名就發限時簽名 URL，不能簽名就串流，**沒有退回
`storage.url()` 這條路**——那等於給私有檔案一個永久免驗證連結。

## 7. 測試策略

- **Unit**：`services.py`、評分演算法、Agent 的 `fallback()`（純函數，必測）。
- **Agent**：用 `responses`/mock 打樁 Anthropic；另有 `evals/` 用真實 API 手動跑（不進 CI）。
- **Integration**：完整流程 — 註冊 → 發 RFQ → 報價 → 下單 → 上傳 NNC1 → 評價驗證。
- **Data**：同步任務用 fixture CSV 測 diff 與 sanity check 中止路徑。

## 8. 帳號、登入與憑證（權威）

帳號能做的每一件事都指向一家真實的持牌公司——發表評價、代表公司回覆、認領頁面、
發需求單。所以這條路上的規則寫在這裡，不散落在各個 view。

**次序：先驗證郵箱，再用密碼登入。**

```
註冊 → 建立帳號（不登入）→ 寄驗證信 → 點連結 → 登入頁 → 密碼登入 → dashboard
```

- `register` **不再**自動登入。未經驗證的地址只是「有人這樣宣稱」，
  憑這個發出的 session 就是一個能以該地址發文的 session。
- `EmailLoginForm.confirm_login_allowed` 擋下未驗證帳號，並把地址帶到
  `accounts:verification_sent`，那裡可以**免登入**重寄——需要重寄的人正好就是登不進來的人。
- 重寄與忘記密碼的回覆**不因地址是否已註冊而不同**：會因此不同的表單，
  等於一份「哪些地址在本平台有帳號」的查詢介面。

**只給未登入者看的頁**（`views.AnonymousOnlyMixin` + `redirect_authenticated_user`）：
註冊、登入、忘記密碼全流程。已登入者一律轉到 dashboard，且這四類頁面都帶 `noindex`。

**密碼**（`config/settings/base.py::AUTH_PASSWORD_VALIDATORS`）：
10 位以上，且同時要有大寫、小寫、數字與符號
（`apps/core/password_validation.py`，一則訊息說完整條規則，不要讓人失敗四次）。
規則由 `password_validators_help_texts()` 渲染到表單上，改設定就會改文案。

**忘記密碼**：`accounts:password_reset` 一組四頁，連結 3 小時、單次有效
（`PASSWORD_RESET_TIMEOUT`）。完成重設會順帶把郵箱標記為已驗證
（`services.mark_email_verified`）——信寄到那個信箱又被用掉，是比驗證信更強的證明。

**節流**（`apps/core/throttling.py`，計數放 Redis cache，key 只存雜湊）：

| 流程 | 額度 | 鍵 |
|---|---|---|
| 登入 | 6 次 / 15 分鐘 | 來源 IP + 郵箱 |
| 重寄驗證信 | 4 次 / 小時 | 來源 IP + 郵箱 |
| 忘記密碼 | 4 次 / 小時 | 來源 IP + 郵箱 |

鍵同時綁 IP 與郵箱：只綁郵箱，任何人打錯幾次就能把一家公司鎖在自己頁面外；
只綁 IP，一個共用出口的辦公室會被整層鎖住。

**識別碼**：所有 model 繼承 `core.BaseModel`，主鍵是 UUIDv7，全站無自增序號——
訂單／需求單的 URL 猜不到第幾張，也就推不出還有誰下過單。
但 UUID 不是授權：`rfq_detail` 仍逐次比對 `buyer_id`，非本人只看得到牆上公開的部分。

**電話**：`apps/core/validators.py` 是全站唯一一份規則——只收數字，
可帶 `+` 國碼，空白與 `-()` 在入庫前去掉。三個表單（註冊、認領、公司資料）共用同一份。
