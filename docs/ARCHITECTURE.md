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
│   ├── reviews/       # Review, ReviewScore, ReviewReply（Nnc1Verification/Dispute 待 P4-2/P4-3）
│   ├── rfq/           # Rfq, RfqRequirement, Quote, QuoteLineItem, QuotaLedger
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

## 4. AI Agent 架構

```
apps/agents/
├── base.py            # BaseAgent: run(), _call_llm(), retry, timeout, fallback, logging
├── registry.py        # AGENTS = {"matching": MatchingAgent, ...} 供 task/view 取用
├── schemas.py         # pydantic models = LLM structured output 契約
├── prompts/
│   ├── matching_v1.md
│   ├── rfq_intake_v1.md
│   ├── moderation_v1.md
│   ├── nnc1_extract_v1.md
│   ├── quote_analysis_v1.md
│   ├── advisor_v1.md
│   └── registry_diff_v1.md
├── tools/             # 給 agent 用的 tool functions（查 DB、算分、查牌照）
├── models.py          # AgentRun, AgentFeedback
├── evals/             # golden set + pytest-based eval，見 ai-agent-builder skill
└── tasks.py
```

`BaseAgent` 契約：

```python
class BaseAgent(ABC):
    name: str
    model: str
    prompt_file: str  # e.g. "matching_v1.md"
    output_schema: type[BaseModel]
    max_tokens: int = 2048
    timeout_s: int = 30
    max_retries: int = 2

    def run(self, ctx: dict) -> AgentResult:
        """Render prompt -> call LLM with tool-use schema -> validate ->
        log AgentRun -> return AgentResult(data, confidence, run_id).
        On failure after retries -> self.fallback(ctx)."""

    @abstractmethod
    def fallback(self, ctx: dict) -> AgentResult:
        """Deterministic non-LLM path. MUST be implemented."""
```

**所有 agent 輸出都是「建議」**，寫入時一律進 `status=pending_review` 或 `is_ai_suggested=True`。

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
```

以 `.env.example` 為準，上表只是導覽。證明檔案存**私有 bucket**，只透過會過權限檢查的
download view 取用；backend 能簽名就發限時簽名 URL，不能簽名就串流，**沒有退回
`storage.url()` 這條路**——那等於給私有檔案一個永久免驗證連結。

## 7. 測試策略

- **Unit**：`services.py`、評分演算法、Agent 的 `fallback()`（純函數，必測）。
- **Agent**：用 `responses`/mock 打樁 Anthropic；另有 `evals/` 用真實 API 手動跑（不進 CI）。
- **Integration**：完整流程 — 註冊 → 發 RFQ → 報價 → 下單 → 上傳 NNC1 → 評價驗證。
- **Data**：同步任務用 fixture CSV 測 diff 與 sanity check 中止路徑。
