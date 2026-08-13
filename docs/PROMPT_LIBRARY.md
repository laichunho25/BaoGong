# PROMPT_LIBRARY — 各階段可直接複製的 Prompt

> 用法：一個 Phase 開一個新 Claude Code 對話（context 乾淨），貼對應 prompt。
> 每段 prompt 都假設 Claude 已讀過 `CLAUDE.md`（它會自動載入）。

---

## P0 — 專案骨架

```text
執行 Phase 0：建立專案骨架。依 docs/ARCHITECTURE.md §2 的目錄結構。

要求：
1. Django 5.1 + DRF，Python 3.12，用 uv 或 poetry 管理依賴（你選一個並說明理由）
2. settings 分層 config/settings/{base,dev,prod,test}.py，用 django-environ 讀 .env
3. docker-compose.yml：web / worker / beat / db(postgres16 + pgvector) / redis / minio
4. apps/core：BaseModel（UUIDv7 主鍵 + timestamps）、Money helper（Decimal + amount_minor）、
   compliance.py 的 check_banned_phrases stub（清單先從 docs/COMPLIANCE.md §2 抄）
5. pyproject.toml 配好 ruff（line-length 100）、mypy（strict 只對 apps/*/services.py）、
   pytest-django、coverage 門檻 80%
6. GitHub Actions：ruff + mypy + pytest + makemigrations --check
7. templates/base.html：Tailwind（自託管字體，不用 Google Fonts）、HTMX 2、Alpine.js、
   footer 放 docs/COMPLIANCE.md §7 的免責文字（先用 gettext 包起來）
8. /healthz endpoint 回 200 + DB/Redis 連線狀態
9. .env.example 依 docs/ARCHITECTURE.md §6

先給我完整檔案清單和關鍵決策（不超過 20 行），我確認後你再建檔。
建完後跑一次 docker compose up --build，確認全綠，然後 conventional commit。
```

---

## P1 — TCSP 數據同步

```text
執行 Phase 1：registry app。這是全站數據地基，做錯全盤皆錯，請特別小心。

依 docs/DATA_MODEL.md 的 registry 段落建 model，並依 docs/ARCHITECTURE.md §5 實作同步流程。

必須做到：
1. Licensee 表除同步任務外任何 code 不得寫入 —— 用 model 層或 manager 層強制（說明你怎麼做）
2. sanity check：本次筆數與上次 SyncRun 相差 > 15% → status=aborted_sanity，
   完全不寫入 Licensee，發告警。這條要有測試。
3. 冪等：同一份 CSV 跑兩次，第二次不產生任何 LicenseeChange
4. diff 分類：new / removed / reactivated / renamed / address_changed
5. 原始 CSV 存 S3 (raw/tcsp/YYYY-MM-DD.csv)，SyncRun 記 checksum
6. management command: python manage.py sync_tcsp [--dry-run] [--file path]
7. Celery beat 每日 06:00 HKT
8. 官方欄位可能有中英文名、全形空格、括號差異 —— 做 normalize 但**原始值存進 raw JSON**
9. 測試用 tests/fixtures/tcsp_*.csv（你自己造 3 份：正常、暴跌、含變更）

注意：先用 --dry-run 對真實 CSV 跑一次，把實際欄位名稱貼給我看，
再確定 model 欄位對映。不要憑猜測寫欄位名。
```

---

## P2 — 目錄前台

```text
執行 Phase 2：公開目錄前台。技術：Django templates + HTMX + Tailwind，不要 SPA。

頁面：
1. /providers/ 列表：搜尋框（名稱模糊 + 牌照號）、篩選（地區、是否協助開戶、語言、認證等級）、
   排序（依 docs/RATING_SYSTEM.md §5）、分頁。全部用 HTMX 局部刷新，URL 要能分享（push-url）。
2. /providers/<slug>/ 詳情：官方欄位區（標明來源）+ 平台補充區（未認領時顯示「此頁面尚未被認領」CTA）
3. /compare/?ids=a,b,c 並排比較最多 3 間
4. 共用元件 templates/components/：
   - data_source_notice.html（來源 + last_synced_at + 官方連結，依 COMPLIANCE §1）
   - rating_display.html（**0 條驗證評價時顯示空狀態，絕不顯示 5.00**，見 RATING_SYSTEM §4）
   - disclaimer_footer.html
5. i18n：zh-Hans（預設）/ zh-Hant / en，全部文案走 gettext，不要硬編
6. SEO：每頁 title/description/canonical、JSON-LD Organization、sitemap.xml、robots.txt

效能要求：列表頁 P95 < 800ms，用 select_related/prefetch_related，
給我一份 django-debug-toolbar 的 query count 報告（目標 < 15 queries）。
```

---

## P3 — 帳號與認領

```text
執行 Phase 3：accounts + providers 的認領流程。

1. User(AbstractUser) + role: buyer / provider_member / moderator / admin
2. 註冊登入：郵箱 + 密碼、郵箱驗證、Cloudflare Turnstile（不要 reCAPTCHA，內地訪問問題）
3. ProviderClaim 流程：
   選公司 → 上傳證明（BR、公司網站、地址證明）→ 網站 TXT/meta token 驗證 →
   進 moderator 佇列 → 批准後綁定 ProviderMember
4. Moderator 後台（用 Django admin 客製，或自建簡單介面 —— 你評估後建議）
   必須顯示：申請內容、證明檔案預覽、官方登記冊比對結果、批准/拒絕 + 理由
5. Certification model 與徽章顯示邏輯
6. 權限：用 django-guardian 或自寫 mixin？給我建議，理由要包含維護成本

安全要求：上傳檔案做 MIME 驗證 + 病毒掃描（clamav container）+ 存 S3 私有 bucket + 簽名 URL。
不要用 MEDIA_ROOT 直接對外。
```

---

## P4 — 評價 + NNC1 核驗 + 前兩個 Agent

```text
執行 Phase 4：這是平台最大的差異化，請分成 4 個 commit。

Commit 1 — 評價資料層與演算法
  依 docs/DATA_MODEL.md reviews 段落建 model
  實作 apps/reviews/services.py::recompute_provider_rating 完全依 docs/RATING_SYSTEM.md
  測試必須包含：0 條 / 1 條 4.5 分 → 4.95 / 全未驗證 / 100 條

Commit 2 — Agent 基礎設施
  依 docs/ARCHITECTURE.md §4 建 apps/agents/base.py：
  BaseAgent（render prompt → Anthropic tool-use structured output → pydantic 驗證 →
  retry 指數退避 → timeout → fallback → 寫 AgentRun）
  AgentRun model、registry.py、kill switch（AGENT_ENABLED_{NAME}）、每日預算檢查
  用 mock 寫完整測試：成功 / schema 失敗 / timeout / 預算用盡 → fallback

Commit 3 — A3 Nnc1ExtractionAgent
  依 docs/AI_AGENTS.md A3 實作。重點：
  - 比對邏輯是**規則**不是 LLM（licence_no exact → trigram ≥ 0.88 → needs_human）
  - false-pass 絕對禁止，寧可 needs_human
  - 檔案加密存放 + purge_at 90 天 + Celery beat purge task（要有測試）
  - AI 說文件可疑 → 只轉人工，不自動拒絕

Commit 4 — A4 ReviewModerationAgent + 審核佇列
  依 docs/AI_AGENTS.md A4。重點：
  - 規則優先於 AI：high severity / PII / 誹謗風險一律人工
  - 絕不自動刪除，最多 hidden
  - moderator 佇列 UI：AI 標籤、建議遮蔽片段、一鍵套用、AgentFeedback 記錄

每個 agent 都要建 apps/agents/evals/{name}/golden.jsonl（先造 20 筆合成樣本），
並寫一個 pytest -m eval 的 runner（不進 CI）。
```

---

## P5 — RFQ 撮合

```text
執行 Phase 5：rfq app + A1 + A5 agent。

1. Model 依 docs/DATA_MODEL.md rfq 段落
2. 買家發單流程：自然語言輸入框 → A1 RfqIntakeAgent 解析 → **預填表單給用戶確認** →
   確認後才寫入 Rfq.structured。用戶可全部手改。
3. 需求牆 /rfq/ ：秘書公司可見（登入 + 已認領），可篩選
4. 報價表單：強制標準化 line items（用 enum label），首年/續年總價自動加總
5. QuotaLedger：每日免費 3 個，先扣免費再扣付費，超額擋下並導到購買頁。
   必須有 race condition 保護（select_for_update）+ 併發測試
6. A5 QuoteAnalysisAgent：市場價分位數用 SQL 算好再餵給 agent，不讓 LLM 估價
7. 買家的報價比較表：同口徑並排，顯示 flags（措辭中性，見 AI_AGENTS A5）

合規檢查：報價流程中平台不得代收款項（COMPLIANCE §6）——
確認沒有任何「付款給秘書公司」的 flow。買家與秘書公司直接聯繫。
```

---

## P6 — 匹配 + 內容 + RAG

```text
執行 Phase 6：A2 + A6 + A7，以及 content app。

1. A2 MatchingAgent — 關鍵是「SQL 硬篩 Top 30 → LLM 只做排序與解釋」。
   必須實作 grounding check：每條 reason 的關鍵詞若在候選資料中找不到依據就丟棄。
   輸出過 banned-phrase filter。寫 nDCG 的 eval runner。

2. content app + pgvector：
   Article/Chunk/Faq、embedding pipeline（Celery task）、ivfflat index
   先寫 8 篇種子文章（開公司流程、開戶被拒 10 大原因、TCSP 牌照是什麼、
   如何自行核實牌照、隱藏費用清單、NNC1 是什麼、公司秘書法定責任、內地股東常見問題）
   —— 文章你先寫草稿，我來審。

3. A6 AdvisorAgent（RAG）：
   **citations 為空一律拒答**，這條要有測試
   禁止提及任何 Provider 名稱 —— 寫 filter 檢查
   每則回答強制附免責文字

4. A7 RegistryDiffAgent：接到 P1 的同步流程尾巴。
   severity 由規則決定，AI 只寫文案。
   已付費 Provider 牌照消失 → critical + 自動下架付費曝光 + email

5. /admin/agents/ 觀測後台：依 docs/AI_AGENTS.md「Agent 觀測與治理」段落
```

---

## P7 — 商業化

```text
執行 Phase 7：billing。

1. Plan / Subscription / CreditPack / Invoice / CommissionDisclosure
2. 支付：評估 Stripe vs Airwallex（考慮：香港公司收款、內地客戶付款、訂閱支援、費率），
   給我比較表再決定。先做 Stripe，抽象成 PaymentProvider interface。
3. 額度包接到 P5 的 QuotaLedger
4. 佣金披露元件：commission_agreement=true 的 Provider 頁面必須渲染（COMPLIANCE §6）
5. 贊助置頂位：獨立版位 + 「贊助」標示，**不得混入自然排序**
6. Provider 分析後台：頁面瀏覽、RFQ 曝光、報價轉換率、評分趨勢

Webhook 要冪等（event_id 去重）+ 簽名驗證 + 完整測試。
```

---

## P8 — 上線前

```text
執行 Phase 8：上線準備。

1. 跑 compliance-review skill 的完整 checklist，逐項給我證據（檔案路徑 + 測試名稱）
2. Sentry 接入（含 Celery），敏感欄位 scrubbing
3. 資料庫每日備份 + 還原演練腳本
4. 負載測試（locust）：目錄頁 200 RPS，找出瓶頸
5. 安全：django check --deploy 全綠、CSP header、HSTS、rate limiting（django-ratelimit）
6. 產出 docs/RUNBOOK.md：同步失敗怎麼辦、agent 成本爆了怎麼辦、
   收到 takedown 通知怎麼辦、DB 還原步驟
7. 列出所有必須由律師覆核的項目清單
```

---

## 通用維護 Prompt

**改一個 agent 的 prompt**
```text
我想改 {agent_name} 的行為：{描述}。
用 ai-agent-builder skill 的流程：新版 prompt 檔（v+1）→ 更新 docs/AI_AGENTS.md →
先對 golden set 跑 eval 比較新舊版分數 → 分數不退步才切換 → 保留舊版可回退。
```

**加一個新功能**
```text
我想加 {功能}。用 django-feature skill。
先給我：影響哪些 app、要改哪些 model、有沒有 migration 風險、
有沒有踩到 docs/COMPLIANCE.md 的線。我確認後再動手。
```

**Debug**
```text
{錯誤描述 + 完整 traceback}
先不要改 code。先告訴我：root cause 是什麼、有幾種修法、各自的取捨。
我選了你才改，並補上一個會 fail 的回歸測試（先讓它紅，再讓它綠）。
```

**Code review**
```text
review 我這個 branch 的改動（git diff main...HEAD）。
重點看：CLAUDE.md §4 的十條規則有沒有被違反、N+1 query、
service 層有沒有漏 transaction、AI 輸出有沒有直接落 DB、測試覆蓋是否足夠。
按嚴重度排序，每條給具體修法。
```
