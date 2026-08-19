# ROADMAP — 分階段交付

狀態圖示：⬜ 未開始 / 🟨 進行中 / ✅ 完成

| Phase | 內容 | 產出 | 狀態 |
|---|---|---|---|
| **P0 骨架** | Django 專案、docker-compose(db/redis/minio/web/worker/beat)、settings 分層、ruff/mypy/pytest、CI、`core.BaseModel`、base template + Tailwind | 可 `docker compose up` 跑起來，`/healthz` 200 | ✅ |
| **P1 registry** | `Licensee/SyncRun/LicenseeChange`、下載+sanity check+upsert+diff、Celery beat、admin、管理指令 `sync_tcsp` / `registry_health`、除名通知 | 名單入庫，可重跑冪等 | ✅ |
| **P2 目錄前台** | `providers` app（Provider/ServiceOffering/PriceItem/Certification）、每日回填、列表頁（HTMX 搜尋/篩選/排序/分頁）、詳情頁、`/compare/`、來源與評分元件、sitemap/robots | 可公開瀏覽的 MVP | ✅ |
| **P3 帳號 + 認領** | User/角色、郵箱註冊登入（手機為選填欄位）、`ProviderMember` 權限、`ProviderClaim` + `ClaimEvidence` 流程（私有儲存、MIME/大小驗證、可插拔病毒掃描、網站 TXT/meta 驗證、90 日保留期）、moderator 佇列（客製 admin，理由必填）、批准後發 `tcsp_licence` | 秘書公司可認領 | ✅ |
| **P4 評價 + 核驗** | **P4-1 ✅** Review/ReviewScore/ReviewReply、貝氏評分演算法（v1 權重：已驗證 1.0／未驗證 0.0）、提交流程（登入＋郵箱驗證＋Turnstile）、`pending_moderation` 審核佇列（客製 admin，理由必填）、詳情頁評價區塊與公司答辯權 · **P4-2 ✅** NNC1 上傳（私有儲存、MIME/大小驗證、病毒掃描、決策後 90 日保留期）、規則式名稱比對（證據非放行條件）、moderator 核驗佇列（客製 admin，理由必填）、`decide_verification` 為 `is_verified` 唯一寫入者 · **P4-3 ✅** `agents.BaseAgent` + `AgentRun`/`AgentFeedback` + 三段 kill switch + Decimal 成本記帳、A4 評價審核（建議而非放行，排序人工佇列）、A3 NNC1 抽取（讀數擺在規則比對旁邊，不碰 `result`）、唯讀 run log admin、22 筆合成 golden set · **P4-4 ✅** Dispute（申訴不改動評價任何欄位、`due_at` 以工作天落實 COMPLIANCE §3 的 5 天承諾、逾期在後台印 `OVERDUE`、一則評價同時只有一宗未決申訴由 partial unique constraint 保證、隱藏／移除一律繞回 `hide_review`／`remove_review`；仲裁 agent 未做） | 已驗證評價可上線 | ✅ |
| **P5 RFQ 撮合** | **P5-1 ✅** Rfq/Quote/QuoteLineItem/QuotaLedger 資料層與 services／selectors：需求單不帶買家身分、`structured` 只是 A1 草稿、報價以封閉 enum 的逐項明細存放（比較表同口徑）、只有已認領且仍在名單上的公司可報價、報價額度以流水帳實作（送出即扣、撤回不退、付費結餘跨日結轉；免費月制、付費日制見 PRD §3.7）、需求 14 日到期（每小時 beat + 牆上再過濾一次） · **P5-2 ✅** 需求牆（登入才看得到）／RFQ 表單（草稿→發布兩步）／報價表單（總額 + 逐項明細 formset）／同口徑比較表（空白格代表沒報這一項，並逐項點名）、兩封通知（買家收到報價、公司知道自己被選上**或落選**）、報價依 `validity_days` 到期（每小時 beat） · **P5-3 ✅** A1 RfqIntake（HTMX 預填表單，**不寫任何 `Rfq` 列**，買家確認過的那一張才是被存的那一張，標 `is_ai_assisted`；原話先 redact；人民幣不換算成港元預算）、A5 QuoteAnalysis（`submit_quote` 於 `on_commit` 派發，只寫 `Quote.analysis` 一欄建議，不動金額不動排序；市場 p10/p50/p90 由 Postgres `PERCENTILE_CONT` 算，樣本不足 8 就明說「沒得比」；非 HKD 報價直接跳過）、兩份 22 筆合成 golden set + `evals/RESULTS.md` | 撮合閉環 | ✅ |
| **P6 匹配 + 內容** | **P6-1 ✅** A2 MatchingAgent（硬篩在 SQL：仍在名單上、開戶協助、語言、預算內或未公開報價；候選 Top 30 先按命中服務數再按 §5 分數；模型只排序與解釋，`reasons`／`concerns` 逐句 grounding，對不上該公司公開資料的整句丟掉，模型與 fallback 同一個篩子；只寫 `Rfq.matches` 一欄建議，只出現在買家頁面；22 筆合成 golden set，nDCG@5 + grounding violation rate） · **P6-2 ✅** pgvector + `content` app（`Article`／`Chunk`；markdown 經 `nh3` 消毒後才進頁面——只有員工能寫稿，正是每一份 stored XSS 事後檢討的開場白；chunk 由正文在同一個 transaction 內重建，下架連同 chunk 一起刪，讀者打不開的頁面 Advisor 也不准引用；`embedding` 先留 NULL，檢索用 `icontains`，ivfflat index 待實際筆數再建）、指南列表／內文頁（公開、可被搜尋引擎索引、內文頁帶 COMPLIANCE §7 免責聲明）、sitemap 只收已發布；18 篇指南隨 app 出貨（`apps/content/library/*.md` + `load_articles` 指令：檔案只是起點，文章一旦進 DB 就歸後台編輯所有，重跑指令預設**跳過**已存在的 slug，要覆寫得明講 `--update`）· **A6 ✅** AdvisorAgent（只答自家指南：檢索 → 模型 → `screen_answer()` 逐條逐字核對引文，引文不成立／命中 banned phrase／點名任何一家持牌公司就整段丟掉換成拒答；尾部強制免責句，稅務／離岸／投資回報再加一句「請問持牌專業人士」；fallback 不生成任何句子，只回三段原文摘錄；`content:ask` 要登入 + 每帳號每小時 12 條，檢索不到段落就不呼叫模型；問題不落 DB，只留 `AgentRun` 的 input hash；32 筆合成 golden set） · **A7 ✅** RegistryDiffAgent（每日同步後把當日 `LicenseeChange` 寫成營運告警：severity 由`severity_for()` 依「頁面有沒有被認領／有沒有付費」重算，未認領公司的移除降級成 `info`，付費公司的牌照離開名單才是 `critical`；`suspend_paid_placement()` 立刻暫停該公司的付費曝光（新欄位 `paid_placement_suspended_at` + `effective_tier`，頁面照樣公開、除牌提示照樣在、帳務不動），這一步在呼叫模型之前就做完，所以模型掛掉那天營運照樣收到信；`screen_digest()` 丟掉模型對非 critical牌照寫的 item、替漏掉的補模板 item、`counts` 一律用 SQL 重算；prompt 禁止說出任何移除原因；12 筆合成 golden set，coverage + over-flag rate + 禁語率） | AI 完整上線 | ✅ |
| **P7-0 公司自助後台** | 已認領公司登入後可自行編輯 profile，能改哪些欄位、能改幾次由 `Tier` 決定（PRD §3.7）：`free` 只能改聯絡資料／網站／業務範疇且一年一次，`verified`／`premium` 不限；上傳 logo（走 `inspect_upload` + 掃毒）、每次修改留一列變更紀錄；新增公司簡介欄位並在寫入路徑上跑 `check_banned_phrases()`；牌照離開名單即全頁唯讀；打錯字走不佔額度的「更正申請」；`ProviderMember` 後台（誰能編輯哪一頁、停權、轉移擁有權）；詳情頁「認識這家公司」區塊 | 認領這件事終於有實際內容，`profile_completeness` 由公司自己填 | ⬜ |
| **P7 商業化** | PRD §3.7 三層方案落地：Plan/Subscription/CreditPack、Stripe（或 Airwallex）、訂閱週期額度取代或並行於每日免費額度、`premium` 置頂展示位（獨立區塊 + 「推廣」標示，不動自然排序）、佣金披露、Provider 分析後台 | 可收費 | ⬜ |
| **P8 上線** | `compliance-review` 全綠、Sentry、備份、負載測試、法律覆核 | Production | ⬜ |

**建議節奏**：P0–P2 是必須先跑通的地基（沒有數據就沒有平台）；P4 的 NNC1 核驗是最大差異化，優先於 P5。

**介面重做（P5-2 之後，跨 phase）✅**：全站頁面改版一次，把「這個平台知道什麼」講清楚——
首頁改成六段（業務功能／需要報價／市場資訊／精選用家評語／熱門搜尋 + hero 統計），
每一段都由既有 selector 即時讀取，沒有編輯精選與示意數字；目錄頁加 `service` 篩選與比較托盤；
詳情頁分官方區／平台區並加 sticky 章節導航；表單渲染與 widget class 收斂成
`components/form_fields.html` + `apps/core/form_styles.py`；新增 `stat` / `section_heading` /
`empty_state` / `icon` 等共用元件與 `animate-rise-in` 入場動效。規範見 `docs/DESIGN_SYSTEM.md`。

**徵求評價（首頁第七段）✅**：首頁新增「邀請已開公司的股東」區塊，用回報換一則有 NNC1／NAR1
佐證的評價。回報刻意只有兩樣，而且兩樣都是既有查詢的衍生值，不是新欄位：
`reviews.selectors.is_verified_reviewer`（評價被隱藏或核驗被推翻，資格同時消失）與需求牆上的
軟性優先排序（`rfq.selectors.open_rfqs` 的 `buyer_verified` + `-buyer_verified` 排序，
牆上顯示為「已核實用家」標記）。回報與打幾分無關、頁面上明說不付錢買評價——這兩句是
COMPLIANCE §3 的紅線，不是文案，見 `apps/core/tests/test_home.py::TestReviewInvitation`。

**品牌與視覺識別（跨 phase）✅**：平台定名 **包公 BaoGong**，正式網域規劃為
`www.baogong.com.hk`（www 為 canonical，因為 canonical tag 由 `request.get_host()` 組出來）。
名字兩義都寫進畫面：首頁「名字的意思」段落、全站 footer 的一句話由來，以及六個頁面各自的
一句品牌線（明鏡高懸／鐵面無私／一把尺量到底）。因為包拯是官，所以同一批頁面也必須說出
**「包公是個名字，不是身分」**——不是政府機構、不替任何人裁決、不承諾開戶結果，並以
`check_banned_phrases()` 對四個已渲染頁面斷言，防止「包」漂移成包過／包成功。
視覺上換掉了原本的通用模板長相：朱砂紅印章標誌（`components/logo.html`）+ favicon、
宋體 `font-display` 標題、回紋與祥雲兩條紋樣。硬規則寫在 `docs/BRAND.md` §5.3：
印章永不作認證徽章、`seal` 永不作狀態色、紋樣一律 `aria-hidden` 且不承載資訊。
Render 資源與 Python package 一併更名為 `baogong`，並補上生產環境**原本缺少的 `SITE_URL`**。

## 依賴關係

```
P0 → P1 → P2 → P3 → P4 → P6
                  └→ P5 → P7-0 → P7 → P8
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
- `[P0] 三篇金管局反洗錢草稿未經核實` — `apps/content/library/hkma-aml-*.md` 三篇的主題是「金管局 2026 年 2 月新指引」，但那份文件的名稱、編號、生效日期與具體改動**我無法核實**，因此三篇只寫了長期穩定的框架（風險為本、CDD、持續監察、STR），**沒有寫入任何具體條文**，且以 `status: draft` 載入——草稿不公開、不建 chunk，所以 A6 也引用不到。每篇開頭有一段【待人工核實】區塊列明發布前必須做的四件事 — 有人查過金管局原文並補上出處之後才可發布。
- `[P0] 未自託管字體、無 CSP／rate limiting` — 首屏字體會落回系統字體、安全 header 缺失 — 字體 P2、安全 header P8。
- `[P2] responsiveness_score 恆為 0` — 排序權重 §5 的 0.08 目前對所有公司同值，等於少了一個維度 — P5 RFQ 落地後由回覆時間寫入。
- ~~`[P2] rating_cached / verified_review_count 尚無寫入者`~~ — P4-1 已由 `reviews.services.recompute_provider_rating` 回寫，並接著重算排序輸入；沒有已驗證評價時寫 **null 而非 5.00**（RATING_SYSTEM §4）。在 P4-2 上線前 `is_verified` 仍無人寫入，因此全站分數實際上還是空狀態。
- ~~`[P2] 認領 CTA 只是靜態文字`~~ — P3 已接上 `providers:claim_start`，已有待審申請的頁面改顯示「審核中」。
- `[P2] 只有 zh_Hans 一本目錄，且內部後台仍是英文` — 切到繁中／英文會落回簡中；`locale/zh_Hans` 已建立並翻完面向用戶的 choice label，但**內部後台的 msgid 刻意留空**（給員工看的，翻了反而看不出哪些字還沒人審過），`zh_Hant` / `en` 兩本尚未產生 — 與 P0 的免責文案一併處理，法律相關文字須人手翻譯不得機器轉換。
- `[P2] Provider.logo 用 FileField 而非 ImageField，且未接 core.uploads / 掃描器` — P3 的 `ClaimEvidence` 已有 magic-byte 嗅探、大小上限與病毒掃描，但 logo 尚未開放上傳，也還沒走同一條路 — P7 開放公司自助編輯 profile 時，logo 必須改走 `inspect_upload` + 掃描，且沒裝 Pillow 就不驗尺寸。
- `[P2] 目錄頁沒有快取` — 7,457 列每次都打 DB，查詢數已測 < 15 但仍是每請求全打 — 有真實流量後再加 Redis 片段快取。
- `[P3] 手機號碼只收不驗` — `User.phone` 是選填自由文字，沒有 SMS 驗證，因此不可作為身分證據 — P5 RFQ 需要可聯絡的買家時再接簡訊供應商（內地號碼須先確認 COMPLIANCE §2 的跨境限制）。
- `[P3] 預設不接病毒掃描器（UnavailableScanner）` — 所有證明檔案停在 `scan_pending`，不可預覽、不可下載，也擋住批准；moderator 只能逐檔 `override_scan`（有署名有理由） — compose 已有 `clamav` service，部署時把 `FILE_SCANNER_BACKEND` 指向 `ClamAvScanner`；P8 前必須完成。
- `[P3] 網站驗證只是證據，不是放行條件` — token 證明申請人控制該網域，證明不了該網域屬於持牌人，因此仍靠人手核對 BR 與登記冊 — 維持現狀，量大時再考慮加自動風險評分（AI 產出仍不得直接落 DB，CLAUDE.md §4.3）。
- ~~`[P3] 認領審核沒有任何通知`~~ — 已補：`approve_claim` / `reject_claim` 於 commit 後寄出結論與**審核員寫的理由**給申請人本人。只寄給申請人：被拒絕的認領證明不了他是誰，通知公司現有成員等於把陌生人的申請洩漏給他們。
- `[P3] 證明檔案清除任務未在真實 S3 上驗證` — `purge_expired_evidence` 只在本機 storage 測過，MinIO／S3 的刪除語意（版本控制、object lock）可能讓 bytes 留下 — 部署 P8 前用真實 bucket 跑一次並確認版本也被清掉。
- ~~`[P4] 沒有任何評價會被標成已驗證`~~ — P4-2 已上線：`reviews.services.decide_verification` 是 `Review.is_verified` 的唯一寫入者，通過後同一交易內重算公司分數。
- `[P4] 核驗佇列的長度是人力問題，不是規則問題` — 名稱比對只是證據（`reviews/matching.py`），每一列都要有人打開文件才能結案，所以佇列不可能靠規則排空 — 上線後要盯 `verification_queue()` 的長度與最舊一列的年齡，超過就是要加人。
- ~~`[P4] 核驗結果不發通知`~~ — 已補：`decide_verification` 寄出結果、理由與 90 日保留期提醒；郵件裡**沒有**文件上的任何欄位（COMPLIANCE §4）。
- `[P4] 本機沒有病毒掃描器，等於所有 NNC1 都不可讀` — 預設 `UnavailableScanner`（fail-closed），沒部署 ClamAV 前 moderator 打不開任何文件，也就通不過任何核驗 — 與 P3 的認領證明同一個阻塞點，部署時一起解。
- ~~`[P4] 評價提交與審核都不發通知`~~ — 已補：作者每次審核決定都收到結論與理由；公司則是**評價公開時**才收到通知，不是提交時——提交時就寄，等於在任何人查證之前，先把投訴者的身分交到被投訴的公司手上。
- ~~`[P4] 審核佇列純人手，A4 尚未接上`~~ — P4-3 已接上：`submit_review` 於 `on_commit` 派發 A4，結果寫進 `Review.moderation`。**改變 `status` 的仍然只有人**，A4 只決定佇列順序（`escalation_reason`）。
- `[P4] A4 不會 auto-publish，與 AI_AGENTS §A4 原規劃不同` — 原文允許 `severity=none` + `confidence ≥ 0.8` + 已驗證用戶自動發佈；實作沒做，因為那與 CLAUDE.md 規則 3 及 `publish_review` 的具名審核員＋必填理由衝突 — **這是政策決定不是技術債**，要開的話是一個開關加一句「誰為 agent 放行的評價負責」，跟我說即可。
- `[P4] 四個 agent 都還沒有真實 eval 資料` — A1／A4／A5 各 22 筆合成 golden（量的是規則有沒有壞，不是模型準不準），A3 完全沒有；AI_AGENTS 要求的是 30 段真實買家原話、50 筆標註評價、20 份去識別化 NNC1、25 份真實報價 — **在補齊並跑過 `uv run pytest -m eval` 之前不要把 `AGENT_ENABLED_*` 打開到生產**，否則等於把沒量過準確率的讀數擺到審核員與買家眼前，而他們會相信它。現況與規則式 fallback 的分數記在 `apps/agents/evals/RESULTS.md`。
- `[P4] 沒有人在看 fallback 率` — `selectors.health(days=7)` 寫好了但沒有人／沒有告警去讀它，所以 agent 可以連續一個月完全沒被呼叫而畫面一切正常 — 與 `[P1] sanity check 告警只寫 logger.critical` 同一件事，P8 接告警通道時一起做。
- `[P4] 每日預算是全域而非逐 agent` — `AGENT_BUDGET_DAILY_USD` 用完是所有 agent 一起走 fallback，所以一個跑掉的高頻 agent 會餓死審核 — P5-3 之後已有四個 agent，其中 A1 是買家每打一段話就呼叫一次的高頻路徑，**這條已經該做了**：拆成逐 agent 額度，A1 用完不該讓 A4 停下來。
- `[P4] 時間衰減權重未實作` — RATING_SYSTEM §2 規劃「24 個月以上權重 0.5」，v1 沒做 — 它上線當天會改動全站每一個分數，而目前沒有足夠評價量支撐這個代價；有量之後再開，開的時候要跑 `reviews.recompute_all_ratings`。
- `[P4] 評價不能修改，只能重寫一則` — 一人一公司一則是硬約束，作者寫錯了只能請 moderator 下架 — 編輯流程要有自己的版本軌跡（誰在什麼時候改了什麼），是一個獨立功能，P7 再評估。
- `[P4] 申訴沒有仲裁 agent，ai_arbitration_draft 恆為空` — PRD §44 與 DATA_MODEL 都寫了這個欄位，實作只留欄位不留 agent — 仲裁草稿會是這個系統風險最高的一段 agent 產出（它要在兩造之間下判斷），而 A3／A4 到現在都還沒有真實 eval 資料；先累積幾十宗人手決定，那些才是這個 agent 的 golden set。
- `[P4] 申訴不能附檔案` — `evidence` 是 JSON（目前只放一個 `engagement_ref`），公司要附合約或往來紀錄就沒地方放 — 檔案要跟 NNC1 同一套掃毒、私有儲存與 90 日保留時鐘，是一個功能不是一個 widget；等真的有公司說「我有文件但貼不上去」再做。
- ~~`[P4] 申訴結案不發通知`~~ — 已補：`decide_dispute` 寄結論與理由給該公司全體在職成員（不只按下按鈕的那一位，人是會離職的）。
- `[P4] SLA 日曆不含香港公眾假期` — `core/dates.py` 只跳週末 — 農曆年、聖誕期間 `due_at` 會算得緊一天（誤差倒向平台自己更早到期），可接受；要準的話得引進假期表並每年維護。
- `[P4] 逾期申訴沒有人看` — `overdue_disputes()` 寫好了，只有打開後台的人會看到 `OVERDUE` — 與 `[P4] 沒有人在看 fallback 率` 同一件事，P8 接告警通道時一起做。
- `[P4] 通知只有郵件一條路，站內沒有收件匣` — 郵件被歸到垃圾郵件或公司換了聯絡人，通知就等於沒發過，而平台這邊看不出差別 — 站內通知中心是 P7 的事；在那之前，每一封信裡的連結都指回可以看到同一結論的頁面，這是刻意的冗餘。
- `[P4] 沒有投遞紀錄，也沒有退信處理` — 寄出即忘：`send_notification` 重試三次之後就沒有任何痕跡說某人從來沒收到過申訴結論 — 要真的守住 COMPLIANCE §3，得存一列投遞紀錄並接供應商的 bounce webhook；P8 接郵件供應商時一起做。
- `[P4] 通知沒有退訂，也沒有寄信頻率上限` — 目前每封都是交易性通知（都是對方等著的答覆），所以沒有退訂是合理的；但 P7 一旦有行銷類郵件，兩者必須分開，且行銷類要有退訂 — 做行銷郵件的那天一起處理。
- `[P4] 郵件語言是 worker 當下的語言，不是收件人的語言` — 用戶偏好語言沒有存在 `User` 上，所以全部落回 `LANGUAGE_CODE`（簡中） — 與 `[P2] locale/ 仍空` 同一件事，補翻譯時一併加 `User.preferred_language` 並在 `deliver` 裡 `translation.override`。
- ~~`[P5] 報價與撮合完全沒有通知`~~ — P5-2 已補：`submit_quote` 寄 `rfq_new_quote` 給買家、`accept_quote` 寄 `quote_decided` 給**得標與落選**的每一家（落選那封是刻意寄的：對方為這則需求用掉了一次額度，就該知道結果）。兩封都只帶結論與連結，金額與明細不進郵件。
- `[P5] 額度是每日流水帳，不是可稽核的購買紀錄` — `grant_quote_credits` 直接加 `paid_balance`，沒有「誰在什麼時候付了多少錢買了幾單」 — P7 的 `billing.CreditPack` 落地時，購買要先寫一列付款紀錄再呼叫這個 service，這個 service 本身不改。
- ~~`[P5] 報價沒有有效期的執行者`~~ — P5-2 已補：`rfq.expire_stale_quotes` 每小時把過了 `validity_days` 的 `submitted`／`shortlisted` 報價標成 `expired`，已獲選的不動（已成交的事不會因為日子到了而失效）。
- `[P5] 需求牆對所有已認領公司一視同仁` — 沒有邀請制、沒有排除機制，`invited_only` 有欄位無流程 — P6-1 的 A2 落地後**仍然刻意不做**：`Rfq.matches` 是給買家看的參考名單，不是誰能報價的名單。要把它變成資格之前，先想清楚沒被推薦的公司怎麼知道、怎麼申訴（COMPLIANCE §2）。
- `[P5] 買家聯絡方式完全不在系統內` — 這是刻意的（COMPLIANCE §4），代價是報價之後雙方要怎麼繼續談，目前平台沒有答案 — 站內訊息是 P7 的事；在那之前，成交後的聯絡方式交換要有一個明確、買家按過同意的動作。
- `[P5] responsiveness_score 仍然沒有寫入者` — P5-1 存了 `submitted_at`，但沒有人拿它去算「這家公司多久回一則需求」 — 排序權重 §5 那 0.08 依舊對所有公司同值，P5-2 有真實報價流量後補。
- `[P5] 需求牆只擋匿名，不擋「任何已登入帳號」` — 註冊一個免費帳號就能讀到全部開放中的需求內容（不含買家身分） — 真正的門檻應該是「已認領的持牌公司才看得到需求全文」，未認領者只看得到標題與服務類別；等 P6 的 A2 匹配決定「誰看得到這則需求」時一起收緊，屆時 `invited_only` 也才有流程。
- `[P5] 草稿只能發布或關閉，不能改` — 買家送出後發現寫錯，唯一的辦法是關掉重寫 — P5-3 的 A1 預填是在**送出之前**的表單上改，所以還沒逼出這條；但預填會讓買家更習慣「先出一版再修」，`update_rfq`（只允許 `draft`，發布後不得改動，因為公司是照著已發布的內容報的價）該排進 P6。
- `[P5] 市場分位數在開站初期幾乎永遠是空的` — `MIN_PERCENTILE_SAMPLE = 8`，同一服務類別湊不到八張同幣別報價就回空 dict，所以 `below_market_p10` 在有量之前基本上不會出現 — 這是刻意的（三張報價算出來的「市場價」是三家公司的價，不是市場），代價是 A5 前期只答得出「他沒說什麼」，答不出「他比別人貴」；不要為了讓畫面好看而調低這個門檻。
- `[P5] A5 關掉時會誤喊 missing_govt_fee` — 規則只看得懂 line item 與勾選框，看不懂附言裡的「已包含政府规费」，在合成 golden set 上 precision 只有 0.56（recall 1.00） — **方向是對的**（漏喊的代價是簽約後才發現要再付一筆），但模型長期關著的話，買家會學會不看這個標籤；`selectors.health()` 要盯的不只是 fallback 率，還有 fallback 期間的 flag 量。
- `[P6] A2 的 golden set 是自己出題自己標的` — 22 筆合成 RFQ 與候選池都是開發者寫的，
  理想 Top5 也是同一個人標的；候選池已用固定種子洗牌（不洗的話規則 fallback 能拿 nDCG 0.99，
  量到的是出題順序），洗完 fallback 是 0.80 — 排序題的「正確答案」本來就有主觀成分，
  這比其他四個 agent 更需要真實樣本：欠 30 張真實 RFQ 配當時真實候選名單的人工標註。
- `[P6] A2 的 0.7 門檻對模型沒有約束力` — 規則 fallback 在合成集上已經 nDCG@5 0.80，
  因為服務覆蓋度這個硬條件就做掉大半 — 實際的上線標準應該是「明顯好過 0.80」，
  模型真正要贏的是 top-1（同樣做齊那幾項服務的公司之間誰排第一，規則只對 4/22）；
  跑完真 API eval 後把這個數字寫回 `MATCHING_NDCG_THRESHOLD`。
- `[P6] A2 的推薦不會隨資料更新` — `Rfq.matches` 是發布當下算一次就存著的快照，
  之後公司改了服務或被除牌都不會重算（讀取時只擋掉已不在名單上的） — 需求最長 14 日，
  快照過期的風險有限；等 RFQ 可以編輯（見上面 P5 那條）時，改完要一併重算。
- `[P6] 指南的檢索還不是向量檢索` — `Chunk.embedding` 全部是 NULL，`selectors.search_chunks()`
  用 `text__icontains` 排序照文章日期 — 語料是簡體中文，這台 Postgres 沒有 `zhparser`，
  FTS 會把整句當一個 token，反而更差；幾十篇文章掃一遍是誠實且夠快的。A6 已經接上這個介面，
  問題先由 `query_terms()` 切成重疊 bigram 再 OR 查詢、在 Python 依命中詞數重排。
  真 API eval 前要決定 embedding provider（成本進 `agents.pricing`），補回填 task 與
  ivfflat index，介面不用改——換的只是 `search_chunks` 的排序。
- `[P6] A6 的節流是 cache 計數，重啟就歸零` — `content.views._over_the_limit()` 每帳號每小時
  12 條，記在 cache 不記在 DB — 這是花費控制不是稽核紀錄，能落地的版本等於一份「誰問過什麼」
  的清單（COMPLIANCE §4）；等真的有人刷，再改成只記次數不記內容的計數表。
- `[P6] A6 只在指南列表頁有入口` — 文章內文頁沒有提問框 — 先看讀者是在列表頁問還是讀完才問；
  真 API eval 跑完、成本有實測數字之後再決定要不要多開一個入口。
- `[P6] 文章只有一種語言的正文` — 標題有三語欄位，正文只有一份 —
  翻譯是人的工作，半翻的正文比一種誠實的語言更糟；等有量再決定要不要開 `ArticleTranslation`。
- `[P6] 指南沒有站內搜尋入口` — `selectors.search_articles()` 已經寫好但沒有頁面用它 —
  先看讀者是從搜尋引擎進來還是從站內找，再決定要不要做（多半是前者）。
- `[P5] 報價明細固定 6 行，沒有動態加行` — 超過六項就得塞進「其他」的備註 — HTMX 加行是小事，但先看真實報價的項目分布再決定要不要把常用項目做成預設列。
- `[P5] 需求牆沒有分頁、沒有篩選` — 上限寫死 100 則（`views.WALL_LIMIT`） — 牆上超過一頁的量出現之前，多給幾則比多一個分頁器有用；到時篩選要按服務類別與公司類型，不是按買家。
- `[P4] helpful_count 沒有寫入者也沒有 UI` — 欄位存在但恆為 0 — P7 做「這則評價有用嗎」時才需要，屆時要一併想清楚防刷。
- ~~`[UI] 首頁的業務功能與精選評語在空資料庫上是空的`~~ — 已補：`manage.py seed_demo`（`apps/core/management/commands/seed_demo.py`）造出服務、價格、三種狀態的評價、一張開放中的需求與三份報價，全部走 services，`--reset` 收回。**沒有 `DEBUG` 就拒跑**：它把虛構的評價與價格掛在真實持牌公司的名字下面，不得進生產。唯一繞過 service 的是掃毒——本機沒有掃描器，NNC1 永遠不可讀，所以 seed 自己把檔案標成 clean 再交給 `decide_verification` 決定。
- ~~`[UI] seed 出來的頁面會露出英文服務名稱`~~ — 已補：`locale/zh_Hans/LC_MESSAGES/django.po` 翻好了所有**面向用戶**的 choice label（服務類別、銀行類型、語言、需求／報價狀態、報價項目、申訴理由等），「提供你需要的服务：Company incorporation」不會再出現。`.mo` 是建置產物、不進版控，因此 Dockerfile 與 CI 各自跑一次 `compilemessages`。
- `[P3] 認領批准之後，公司還是不能自己編輯任何一個欄位` — 詳情頁對未認領公司寫著
  「可認領此頁面並完善資料」，但 `approve_claim` 給的只有 `ProviderMember` 這一列權限；
  `website`／`founded_year`／`team_size`／`languages`／服務與價格，全部仍然只有員工進
  Django admin 才改得動 — **這是平台已經寫在畫面上的承諾沒有兌現**，而且它同時卡住三件事：
  `profile_completeness`（排序權重的一部分）永遠靠員工手動填、公司無法自行更新價格、
  認領的實際好處只剩「可以報價」。應排在 P7 之前，見 P7-0。
- `[P3] ProviderMember 沒有任何後台` — `apps/accounts/admin.py` 只註冊了 `User` 與
  `EmailVerification`，所以員工看不到「誰有權編輯哪一間公司的頁面」，也沒有停權的動作。
  離職員工、批准給錯人、公司要求移除同事，目前都只能直接改資料庫 — 與上一條同一批做。
- `[P2] Provider 沒有一個讓客人「了解這家公司」的欄位` — 平台補充區只有 `website`、
  `founded_year`、`team_size`、幾個 boolean 與服務價格表；沒有公司簡介、沒有團隊或資歷、
  沒有服務流程說明，`ServiceOffering.description` 是逐項的、不是整體的 — 買家要判斷
  「這家跟那家有什麼不同」時，畫面上其實只有價格。加欄位同時要決定審核方式：
  自由文字是公司對外的公開陳述，`check_banned_phrases()` 必須擋在寫入路徑上。
- `[P0] Windows 本機的 celery worker 必須用 --pool=threads` — 預設 prefork pool 走
  billiard 的 POSIX semaphore，在 Windows 上每個子進程都拋 `PermissionError: [WinError 5]`，
  worker 會無限重生子進程、log 看起來活著但一個 task 都做不完 — Linux 與 Docker image
  不受影響，所以 `render.yaml` 維持預設；已寫進 README「Local (no Docker)」。
- `[UI] 沒有視覺回歸測試` — 現在只斷言「區塊在不在、數字對不對」，版面跑掉不會有人知道 — 頁面數穩定下來再考慮 Playwright 截圖比對；在那之前，改 template 後務必手動看一次並重建 `app.css`。

---


## 每個 Phase 的驗收（DoD 見 CLAUDE.md §7）

額外要求：
- P1：必須有 fixture 測試涵蓋「筆數暴跌 > 15% → aborted_sanity，DB 不變」
- P3：必須有測試「未掃描的證明檔案不被服務、也擋住批准」與「他人的申請回 404 而非 403」
- P4：必須有測試「1 條 4.5 分驗證評價 → 顯示 4.95」與「0 條評價 → 不顯示分數」
- P5：必須有測試「同一天第 4 次報價被擋，購買額度後可報」；A1 預填不得寫出任何 `Rfq` 列，
  A5 不得改動報價的任何一欄（兩者都有測試）
- P6：每個 agent 的 eval 必須跑過並記錄分數在 `apps/agents/evals/RESULTS.md`；
  A2 不得寫出 `Rfq.matches` 以外的任何一欄，推薦名單不得改變任何公司的報價權（有測試）
