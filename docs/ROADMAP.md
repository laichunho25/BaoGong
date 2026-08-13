# ROADMAP — 分階段交付

狀態圖示：⬜ 未開始 / 🟨 進行中 / ✅ 完成

| Phase | 內容 | 產出 | 狀態 |
|---|---|---|---|
| **P0 骨架** | Django 專案、docker-compose(db/redis/minio/web/worker/beat)、settings 分層、ruff/mypy/pytest、CI、`core.BaseModel`、base template + Tailwind | 可 `docker compose up` 跑起來，`/healthz` 200 | ✅ |
| **P1 registry** | `Licensee/SyncRun/LicenseeChange`、下載+sanity check+upsert+diff、Celery beat、admin、管理指令 `sync_tcsp` / `registry_health`、除名通知 | 名單入庫，可重跑冪等 | ✅ |
| **P2 目錄前台** | 名單列表頁（搜尋/篩選/分頁，HTMX）、Provider 詳情頁、來源與免責元件、i18n 骨架、SEO | 可公開瀏覽的 MVP | ⬜ |
| **P3 帳號 + 認領** | User/角色、註冊登入（郵箱+手機）、`ProviderClaim` 流程、審核後台、Certification | 秘書公司可認領 | ⬜ |
| **P4 評價 + 核驗** | Review/ReviewScore/ReviewReply/Dispute、評分演算法、NNC1 上傳與加密存放、**A3 + A4 agent**、審核佇列 | 已驗證評價可上線 | ⬜ |
| **P5 RFQ 撮合** | Rfq/Quote/QuoteLineItem/QuotaLedger、需求牆、報價表單、比較表、**A1 + A5 agent**、每日 3 單額度 | 撮合閉環 | ⬜ |
| **P6 匹配 + 內容** | **A2 MatchingAgent**、pgvector + content app、**A6 AdvisorAgent**、教育文章 CMS、**A7 RegistryDiffAgent** | AI 完整上線 | ⬜ |
| **P7 商業化** | Plan/Subscription/CreditPack、Stripe（或 Airwallex）、佣金披露、Provider 分析後台 | 可收費 | ⬜ |
| **P8 上線** | `compliance-review` 全綠、Sentry、備份、負載測試、法律覆核 | Production | ⬜ |

**建議節奏**：P0–P2 是必須先跑通的地基（沒有數據就沒有平台）；P4 的 NNC1 核驗是最大差異化，優先於 P5。

## 依賴關係

```
P0 → P1 → P2 → P3 → P4 → P6
                  └→ P5 → P7 → P8
```

## Tech Debt

_（每個 Phase 結束時由 Claude 追加，格式：`[Pn] 描述 — 影響 — 建議處理時機`）_

- `[P0] docker compose up --build 未在本機驗證` — 本機無 Docker／WSL2，compose 檔僅靜態檢查 — 裝好 Docker Desktop 後立即補跑。
- `[P0] 免責文字只有繁中 msgid，locale/ 尚無 zh-Hans／en 翻譯` — 預設語言 zh-Hans 會落回繁中 — P2 做 i18n 時補，且法律文字須人手翻譯不得機器轉換。
- `[P0] check_banned_phrases 為正則規則版` — 變體寫法（拼音、諧音、圖片文字）可繞過 — 有真實違規樣本後補測試語料，P8 合規檢查前重新評估。
- ~~`[P0] TCSP_CSV_URL 未經驗證`~~ — 已於 P1 對真實檔案驗證（7,457 列），欄位對映見 DATA_MODEL.md。
- `[P1] district 由地址字串比對推導，覆蓋 94.4%（417 列未識別）` — P2 地區篩選會漏掉這些公司 — P2 前補足 locality 對照表，或改為「未分類」可見選項。
- `[P1] 同步沒有用 ETag／checksum 短路` — 官方檔約每月更新一次，每日仍全量 upsert 7,457 列 — 資料量再大時再優化，目前一次約 20 秒。
- `[P1] sanity check 告警只寫 logger.critical` — 靠 Sentry 的 logging integration 才會通知 — P8 接正式告警通道（郵件／IM）。
- `[P1] registry_health 需外部排程去打` — 指令本身只回傳非零碼，沒人跑就等於沒有 — 部署到 Render 時掛 cron／uptime monitor，並在 P2 開 `/healthz/registry` endpoint。
- `[P1] 除名通知文案未經法律覆核` — `apps/registry/notices.py` 的措辭是對具名公司的公開陳述 — 上線前（P8）連同 COMPLIANCE §7 免責文一併送律師。
- `[P1] LicenseeChange.notified_at 目前無人寫入` — `registry_health --fail-on-critical` 會永遠告警 — P8 告警通道落地時同步寫入。
- `[P1] LicenseeChange 未接 A7 RegistryDiffAgent` — `ai_summary` 目前恆為空 — P6。
- `[P0] 未自託管字體、無 CSP／rate limiting` — 首屏字體會落回系統字體、安全 header 缺失 — 字體 P2、安全 header P8。

---

## 每個 Phase 的驗收（DoD 見 CLAUDE.md §7）

額外要求：
- P1：必須有 fixture 測試涵蓋「筆數暴跌 > 15% → aborted_sanity，DB 不變」
- P4：必須有測試「1 條 4.5 分驗證評價 → 顯示 4.95」與「0 條評價 → 不顯示分數」
- P5：必須有測試「同一天第 4 次報價被擋，購買額度後可報」
- P6：每個 agent 的 eval 必須跑過並記錄分數在 `apps/agents/evals/RESULTS.md`
