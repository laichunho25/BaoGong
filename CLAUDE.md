# CLAUDE.md — QS Matching Platform 專案憲法

> 這份文件每次對話都會被載入。保持精簡；細節放 `docs/`。

## 1. 專案是什麼

香港 TCSP（Trust or Company Service Provider）持牌秘書公司的**中立比較 + 真實評價 + 報價撮合**平台。
主要用戶：不熟悉香港開公司／開戶流程的**內地客戶**。

三個核心價值：
1. **透明**：只列公司註冊處官方持牌名單，每日同步，價格區間公開。
2. **可信**：評價須以 NNC1 文件核驗「確有合作」為前提，才給「已驗證」標記。
3. **撮合**：客戶發 RFQ，秘書公司主動報價（免費額度每日 3 個，超額付費）。

商業模式：秘書公司付費認領／置頂／認證年費 + 報價額度包 + 導流佣金（**必須披露**）。

## 2. 技術棧（不要擅自更換）

| 層 | 選型 |
|---|---|
| 後端 | Django 5.1 + Django REST Framework |
| DB | PostgreSQL 16（`pgvector` extension 供 RAG 用） |
| 非同步 | Celery 5 + Redis（beat 做每日 TCSP 同步） |
| 前端 | Django Templates + HTMX 2 + Alpine.js + Tailwind CSS 3 |
| AI | Anthropic Python SDK（`anthropic`），模型見 §5 |
| 檔案 | S3-compatible（本機用 MinIO） |
| 搜尋 | Postgres FTS + trigram；量大再上 Meilisearch |
| 測試 | pytest + pytest-django + factory_boy + responses |
| 品質 | ruff（lint+format）、mypy（strict on `apps/*/services.py`） |
| 部署 | Docker Compose → 之後上 Fly.io / AWS ap-east-1（香港） |

**伺服器必須在香港或海外，不得放內地。** 見 `docs/COMPLIANCE.md`。

## 3. 目錄規範

```
config/              # settings/{base,dev,prod}.py, celery.py, urls.py
apps/
  accounts/          # 用戶、會員、秘書公司帳號、KYC
  registry/          # TCSP 官方名單、同步、diff、牌照狀態
  providers/         # 秘書公司 profile、認領、認證、服務標籤、價格
  reviews/           # 評價、NNC1 核驗、審核、公司回覆
  rfq/               # 客戶需求單、報價、報價額度
  agents/            # 全部 AI Agent（見 docs/AI_AGENTS.md）
  billing/           # 訂閱、額度包、發票
  content/           # 教育文章、CMS、SEO
  core/              # 共用 model mixin、middleware、utils、首頁、表單 widget class
templates/
  components/        # 全站共用元件（見 docs/DESIGN_SYSTEM.md）
```

每個 app 的分層：`models.py` → `selectors.py`（讀）→ `services.py`（寫／business logic）→ `views.py` → `api.py`。
**View 不准直接寫 ORM 寫入邏輯**，一律經 `services.py`。

前端：介面語言與元件清單見 `docs/DESIGN_SYSTEM.md`，動手前的檢查表見
`.claude/skills/frontend-page`。**改完 template 的 class 一定要重建 CSS**
（`powershell -NoProfile -File scripts/tailwind.ps1`），`static/css/app.css` 有進版控。

## 4. 不可違反的規則

1. **官方數據唯讀**：`registry` app 裡的 TCSP 原始欄位永不被人手修改。任何 enrich 資料放 `providers`，用 FK 關聯。
2. **不得聲稱官方身分**，不得保證開戶成功率。任何顯示 TCSP 數據的頁面必須有來源標註 + `last_synced_at`。
3. **AI 產出永不直接落 DB 成為事實**。Agent 輸出一律進「建議／草稿」狀態，需人手或規則確認。
4. **Agent 呼叫必須記錄**：每次 LLM 呼叫寫入 `agents.AgentRun`（input hash、model、tokens、cost、latency、outcome）。
5. **個資最小化**：NNC1 上傳檔案只抽取需要欄位，原檔加密存放，預設 90 日後刪除。
6. **金額用 `Decimal`**，不用 float。貨幣一律存 `currency` + `amount_minor`（整數，最小單位）。
7. **Migration 一律 review**：不准 `--fake`，不准手改已 apply 的 migration。
8. **Secrets 只從環境變數讀**，`.env` 進 `.gitignore`，提供 `.env.example`。
9. **新功能必須有測試**：service 層 unit test + 關鍵流程 integration test，否則不算完成。
10. 遇到 `docs/COMPLIANCE.md` 的紅線議題 → **停下來問我**，不要自己判斷。

## 5. AI 模型使用政策

| 用途 | 模型 | 理由 |
|---|---|---|
| Matching / Quote Analysis / Moderation 推理 | `claude-sonnet-5` | 品質與成本平衡 |
| 文件抽取（NNC1 OCR 後結構化）、分類、短摘要 | `claude-haiku-4-5-20251001` | 高頻低成本 |
| 複雜合規判斷、爭議評價仲裁草稿 | `claude-opus-5` | 只在 escalation 用 |

- 全部 agent 走 `apps/agents/base.py` 的 `BaseAgent`，**不准在 view 裡直接 `anthropic.Anthropic()`**。
- 一律用 **structured output（tool use / JSON schema）**，不要 parse 自由文字。
- 每個 agent 有 `max_tokens`、timeout、retry（指數退避）、以及 **fallback 到規則式邏輯**。
- Prompt 存在 `apps/agents/prompts/*.md`，**版本化**（檔名帶 `v1`, `v2`），不要 inline 在 Python 裡。

## 6. 語言與溝通

- 跟我對話：**繁體中文**。
- Code、變數、commit message、docstring、log：**英文**。
- 面向用戶的 UI 文案：**簡體中文為主**（目標客群為內地），繁中／英文為切換選項。i18n 用 Django `gettext`，不要硬編中文。

## 7. 完成的定義（DoD）

一個任務算完成，必須全部滿足：
- [ ] `ruff check . && ruff format --check .` 通過
- [ ] `mypy apps/` 通過
- [ ] `pytest` 全綠，新增 code 覆蓋率 ≥ 80%
- [ ] Migration 已產生且 `makemigrations --check --dry-run` 無殘留
- [ ] 相關 `docs/*.md` 已同步更新
- [ ] Conventional commit，訊息說明 **why** 而非 what
