---
name: django-feature
description: 在 QS Matching Platform 新增或修改一個 Django 功能模組的標準流程。當使用者說「加一個 XXX 功能」「做 XXX 模組」「改 XXX 的邏輯」「新增 model / API / 頁面」時觸發。強制走 model → selectors → services → views 分層、先寫規格再寫 code、先跑 migration 風險評估。
---

# django-feature

## 何時用

新增或修改任何 Django 功能：model、service、API、Celery task、admin 介面。
**不適用**：純前端頁面（用 `frontend-page`）、AI agent（用 `ai-agent-builder`）、TCSP 同步（用 `tcsp-data-sync`）。

## 流程（不要跳步）

### Step 1 — 規格對齊（不寫 code）

先讀 `docs/DATA_MODEL.md` 與 `CLAUDE.md`，然後輸出一份不超過 25 行的實作計劃：

```
功能：<一句話>
影響 app：<列表>
Model 變更：<新增/修改欄位，或「無」>
Migration 風險：<none | additive | 需要 data migration | 破壞性>
Service 函式：<簽名列表>
Selector 函式：<簽名列表>
對外介面：<view / api endpoint / celery task>
合規檢查：<有無踩 docs/COMPLIANCE.md 的線；沒有就寫「無」>
測試計劃：<unit / integration 各幾條，測什麼>
未決問題：<需要使用者拍板的>
```

**停下來等使用者確認**。有「未決問題」時絕不自行假設。

### Step 2 — 更新文件

若涉及 model 變更 → **先改 `docs/DATA_MODEL.md`**，再寫 code。文件是權威來源。

### Step 3 — 分層實作

嚴格照 `docs/ARCHITECTURE.md §3`：

```python
# models.py — 只有欄位、約束、__str__、property
class Quote(BaseModel):
    ...

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["rfq", "provider"], name="uniq_quote_per_rfq")
        ]


# selectors.py — 只讀，回傳 queryset 或 dataclass
def get_quotes_for_rfq(rfq_id: UUID) -> QuerySet[Quote]: ...


# services.py — 所有寫入，@transaction.atomic，型別完整（mypy strict）
@transaction.atomic
def submit_quote(*, rfq: Rfq, provider: Provider, items: list[LineItemDTO]) -> Quote: ...


# views.py — 薄，只做 request → service → template
# tasks.py — 只 orchestrate，不放邏輯
```

規則：
- Service 一律 **keyword-only 參數**（`*,`），回傳具體型別，不回 `dict`。
- 任何金額用 `Decimal` + `amount_minor: int`，禁止 float。
- 任何跨 model 的寫入包 `@transaction.atomic`。
- 需要鎖的地方用 `select_for_update()`（例如額度扣減），並寫併發測試。
- 不在 view 或 template 裡做 query loop（N+1）。

### Step 4 — Migration

```bash
python manage.py makemigrations <app> --name <descriptive_name>
python manage.py sqlmigrate <app> <number>   # 貼給使用者看實際 SQL
```
- 破壞性變更（刪欄位、改型別、加 NOT NULL）→ **拆成三段**：加欄位 → 回填 data migration → 移除舊欄位。
- 大表加 index → 用 `AddIndexConcurrently` + `atomic = False`。
- 絕不 `--fake`，絕不手改已 apply 的 migration。

### Step 5 — 測試

最少要有：
1. Service 的 happy path
2. Service 的每一條業務規則違反時的 exception
3. DB 約束被觸發的測試
4. 若有併發風險 → `pytest-django` + threading 或 `TransactionTestCase` 測 race
5. 若有權限 → 每個角色各一條

用 `factory_boy` 建 fixture，不要在測試裡手刻一堆 `objects.create`。

### Step 6 — 驗收

跑齊 `CLAUDE.md §7` 的 DoD，然後：
```bash
ruff check . && ruff format --check . && mypy apps/ && pytest -q
python manage.py makemigrations --check --dry-run
```
全綠才寫 conventional commit，訊息說明 **why**。

## 常見陷阱（本專案特有）

| 陷阱 | 正確做法 |
|---|---|
| 想直接改 `registry.Licensee` 補資料 | 官方表唯讀。enrich 放 `providers.Provider` |
| 在 view 裡 `Provider.objects.update(...)` | 一律經 `services.py` |
| AI 輸出直接寫進正式欄位 | 進 `status=pending_review` 或 `is_ai_suggested=True` |
| 評分即時算 | 用 Celery task 重算並快取到 `Provider.rating_cached` |
| 硬編中文 UI 文案 | 一律 `gettext`，預設語系 zh-Hans |
| 額度扣減沒鎖 | `select_for_update()` + unique(provider, date) |
