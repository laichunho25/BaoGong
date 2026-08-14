# ROADMAP — 分階段交付

狀態圖示：⬜ 未開始 / 🟨 進行中 / ✅ 完成

| Phase | 內容 | 產出 | 狀態 |
|---|---|---|---|
| **P0 骨架** | Django 專案、docker-compose(db/redis/minio/web/worker/beat)、settings 分層、ruff/mypy/pytest、CI、`core.BaseModel`、base template + Tailwind | 可 `docker compose up` 跑起來，`/healthz` 200 | ✅ |
| **P1 registry** | `Licensee/SyncRun/LicenseeChange`、下載+sanity check+upsert+diff、Celery beat、admin、管理指令 `sync_tcsp` / `registry_health`、除名通知 | 名單入庫，可重跑冪等 | ✅ |
| **P2 目錄前台** | `providers` app（Provider/ServiceOffering/PriceItem/Certification）、每日回填、列表頁（HTMX 搜尋/篩選/排序/分頁）、詳情頁、`/compare/`、來源與評分元件、sitemap/robots | 可公開瀏覽的 MVP | ✅ |
| **P3 帳號 + 認領** | User/角色、郵箱註冊登入（手機為選填欄位）、`ProviderMember` 權限、`ProviderClaim` + `ClaimEvidence` 流程（私有儲存、MIME/大小驗證、可插拔病毒掃描、網站 TXT/meta 驗證、90 日保留期）、moderator 佇列（客製 admin，理由必填）、批准後發 `tcsp_licence` | 秘書公司可認領 | ✅ |
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
- `[P1] district 由地址字串比對推導，覆蓋 94.4%（417 列未識別）` — **P2 地區篩選已上線，這 417 間公司選任何地區都不會出現**（空字串不進篩選清單，見 `available_districts`）— 補足 locality 對照表，或在 UI 加「未分類地區」選項；P3 前處理。
- `[P1] 同步沒有用 ETag／checksum 短路` — 官方檔約每月更新一次，每日仍全量 upsert 7,457 列 — 資料量再大時再優化，目前一次約 20 秒。
- `[P1] sanity check 告警只寫 logger.critical` — 靠 Sentry 的 logging integration 才會通知 — P8 接正式告警通道（郵件／IM）。
- `[P1] registry_health / healthz/registry 仍需外部 monitor 去打` — 指令回非零碼、endpoint 回 503，但沒人盯就等於沒有 — 部署到 Render 時掛 uptime monitor（見 DEPLOY_RENDER §4.1）。
- `[P1] 除名通知文案未經法律覆核` — `apps/registry/notices.py` 的措辭是對具名公司的公開陳述 — 上線前（P8）連同 COMPLIANCE §7 免責文一併送律師。
- `[P1] LicenseeChange.notified_at 目前無人寫入` — `registry_health --fail-on-critical` 會永遠告警 — P8 告警通道落地時同步寫入。
- `[P1] LicenseeChange 未接 A7 RegistryDiffAgent` — `ai_summary` 目前恆為空 — P6。
- `[P0] 未自託管字體、無 CSP／rate limiting` — 首屏字體會落回系統字體、安全 header 缺失 — 字體 P2、安全 header P8。
- `[P2] responsiveness_score 恆為 0` — 排序權重 §5 的 0.08 目前對所有公司同值，等於少了一個維度 — P5 RFQ 落地後由回覆時間寫入。
- `[P2] rating_cached / verified_review_count 尚無寫入者` — 全站目前都是「暫無已驗證評價」空狀態，排序只由認證等級與資料完整度決定 — P4 評價系統上線後由 `reviews.services` 回寫。
- ~~`[P2] 認領 CTA 只是靜態文字`~~ — P3 已接上 `providers:claim_start`，已有待審申請的頁面改顯示「審核中」。
- `[P2] UI 文案只有簡中硬字串（gettext 已包，locale/ 仍空）` — 切到繁中／英文會落回簡中 — 與 P0 的免責文案一併處理，法律相關文字須人手翻譯。
- `[P2] Provider.logo 用 FileField 而非 ImageField，且未接 core.uploads / 掃描器` — P3 的 `ClaimEvidence` 已有 magic-byte 嗅探、大小上限與病毒掃描，但 logo 尚未開放上傳，也還沒走同一條路 — P7 開放公司自助編輯 profile 時，logo 必須改走 `inspect_upload` + 掃描，且沒裝 Pillow 就不驗尺寸。
- `[P2] 目錄頁沒有快取` — 7,457 列每次都打 DB，查詢數已測 < 15 但仍是每請求全打 — 有真實流量後再加 Redis 片段快取。
- `[P3] 手機號碼只收不驗` — `User.phone` 是選填自由文字，沒有 SMS 驗證，因此不可作為身分證據 — P5 RFQ 需要可聯絡的買家時再接簡訊供應商（內地號碼須先確認 COMPLIANCE §2 的跨境限制）。
- `[P3] 預設不接病毒掃描器（UnavailableScanner）` — 所有證明檔案停在 `scan_pending`，不可預覽、不可下載，也擋住批准；moderator 只能逐檔 `override_scan`（有署名有理由） — compose 已有 `clamav` service，部署時把 `FILE_SCANNER_BACKEND` 指向 `ClamAvScanner`；P8 前必須完成。
- `[P3] 網站驗證只是證據，不是放行條件` — token 證明申請人控制該網域，證明不了該網域屬於持牌人，因此仍靠人手核對 BR 與登記冊 — 維持現狀，量大時再考慮加自動風險評分（AI 產出仍不得直接落 DB，CLAUDE.md §4.3）。
- `[P3] 認領審核沒有任何通知` — 批准／拒絕後申請人只有自己回 dashboard 才看得到結果 — P4 建郵件通知模板時一併補（決策理由要一併寄出）。
- `[P3] 證明檔案清除任務未在真實 S3 上驗證` — `purge_expired_evidence` 只在本機 storage 測過，MinIO／S3 的刪除語意（版本控制、object lock）可能讓 bytes 留下 — 部署 P8 前用真實 bucket 跑一次並確認版本也被清掉。

---

## 每個 Phase 的驗收（DoD 見 CLAUDE.md §7）

額外要求：
- P1：必須有 fixture 測試涵蓋「筆數暴跌 > 15% → aborted_sanity，DB 不變」
- P3：必須有測試「未掃描的證明檔案不被服務、也擋住批准」與「他人的申請回 404 而非 403」
- P4：必須有測試「1 條 4.5 分驗證評價 → 顯示 4.95」與「0 條評價 → 不顯示分數」
- P5：必須有測試「同一天第 4 次報價被擋，購買額度後可報」
- P6：每個 agent 的 eval 必須跑過並記錄分數在 `apps/agents/evals/RESULTS.md`
