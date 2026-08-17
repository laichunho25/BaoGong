# ARCHITECTURE — QS Matching Platform

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
qs-platform/
├── config/
│   ├── settings/{base,dev,prod,test}.py
│   ├── celery.py
│   ├── urls.py
│   └── wsgi.py asgi.py
├── apps/
│   ├── core/          # BaseModel(uuid, created_at, updated_at), middleware, money, i18n utils
│   ├── accounts/      # User(AbstractUser), ProviderMember, EmailVerification, permissions.py
│   ├── registry/      # Licensee, SyncRun, LicenseeChange
│   ├── providers/     # Provider, ProviderClaim, ClaimEvidence, ServiceOffering, PriceItem, Certification
│   ├── reviews/       # Review, ReviewScore, ReviewReply, Nnc1Verification, Dispute
│   ├── rfq/           # Rfq, Quote, QuoteLineItem, QuotaLedger
│   ├── agents/        # BaseAgent, registry, prompts/, tools/, AgentRun, evals/
│   ├── billing/       # Plan, Subscription, CreditPack, Invoice, CommissionDisclosure
│   └── content/       # Article, Faq, Embedding(pgvector), SeoMeta
├── templates/         # base.html, components/, pages/
├── static/            # tailwind input.css, alpine, self-hosted fonts
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

**授權**：不裝 django-guardian。全站只有兩個判斷——「是不是這間 provider 的成員」與「是不是
moderator」——都由 `apps/accounts/permissions.py` 從 `ProviderMember` / `User.role` 回答。
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
DATABASE_URL=postgres://qs:qs@db:5432/qs
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
CLAIM_SITE_VERIFICATION_KEY=qs-site-verification
SITE_URL=http://localhost:8000 DEFAULT_FROM_EMAIL= NOTIFICATIONS_ENABLED=true
DISPUTE_SLA_BUSINESS_DAYS=5
RFQ_FREE_QUOTES_PER_DAY=3 RFQ_OPEN_DAYS=14
```

以 `.env.example` 為準，上表只是導覽。證明檔案存**私有 bucket**，只透過會過權限檢查的
download view 取用；backend 能簽名就發限時簽名 URL，不能簽名就串流，**沒有退回
`storage.url()` 這條路**——那等於給私有檔案一個永久免驗證連結。

## 7. 測試策略

- **Unit**：`services.py`、評分演算法、Agent 的 `fallback()`（純函數，必測）。
- **Agent**：用 `responses`/mock 打樁 Anthropic；另有 `evals/` 用真實 API 手動跑（不進 CI）。
- **Integration**：完整流程 — 註冊 → 發 RFQ → 報價 → 下單 → 上傳 NNC1 → 評價驗證。
- **Data**：同步任務用 fixture CSV 測 diff 與 sanity check 中止路徑。
