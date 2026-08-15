# COMPLIANCE — 合規紅線

> Claude：碰到本文件任何一條，**停下來問我**，不要自行判斷。
> 本文件不構成法律意見；上線前須經香港律師覆核。

## 1. 數據使用

- 只使用公司註冊處 / data.gov.hk **公開** TCSP 持牌人名單。
- 每個顯示官方數據的頁面必須有：
  - 「資料來源：香港公司註冊處《信託或公司服務持牌人登記冊》／data.gov.hk」
  - 「最後更新：{last_synced_at}（香港時間）」
  - 「牌照狀態以官方最新公佈為準，請以官方登記冊核實」+ 官方連結
- **不得**修改官方欄位、不得聲稱為官方或獲官方認可。
- 遵守 data.gov.hk 的開放數據條款（附連結於 footer）。

## 2. 禁止的宣稱（banned phrases — 寫成 code 檢查）

實作 `apps/core/compliance.py::check_banned_phrases(text)`，套用於：AI 輸出、Provider 自填文案、平台行銷文案。

禁止：
- 「保證開戶成功」「100% 開戶」「包過」「必定批核」
- 「官方認證」「政府推薦」「註冊處指定」
- 「最佳／第一／唯一」等絕對排名宣稱（除非有可驗證依據）
- 平台自稱提供「公司秘書服務」「代辦註冊」（我們是資訊平台，不是服務商）
- 任何具體開戶成功率百分比

## 3. 評價與誹謗

- 評價須「基於事實、可查證的親身經歷」— 提交表單明示此要求。
- 秘書公司有**回覆權**與**申訴權**（Dispute 流程），且申訴須在 5 個工作天內處理。
- 平台保留 notice-and-takedown 流程與紀錄。
- 評價中的第三方個人姓名／電話／郵箱必須遮蔽（Moderation Agent 的 `suggested_redactions` + 人工確認）。
- **AI 不得自動刪除評價**；最多 `hidden` 並通知雙方。

## 4. 個人資料（PDPO, Cap. 486）

- 收集目的須在 PICS（Personal Information Collection Statement）明示。
- NNC1 上傳檔（`reviews.Nnc1Verification`）：私有 bucket、**只抄三個自述欄位**
  （公司名稱、公司編號、文件上列明的秘書名稱），董事姓名／住址／身份證明號碼一律不入庫；
  只有上傳者本人與 moderator 可取用，未經掃描不可下載；核驗**決策後** 90 日由
  `reviews.purge_nnc1_documents` 刪除 bytes（`NNC1_RETENTION_DAYS`，每日 beat，有測試），
  **保留該列、sha256、比對結果與審核理由**。檔案被清除不會使核驗失效。
- 認領證明檔案（`providers.ClaimEvidence`）同一規則：私有 bucket、只有申請人與 moderator 可取用、
  審核決定後 90 日由 `providers.purge_claim_evidence` 刪除 bytes；**保留該列與 sha256**，
  因為審核紀錄要留，個資不留。未經掃描的檔案一律不可預覽、不可下載。
- 用戶可要求查閱／更正／刪除其個資 → 後台必須有 DSAR 處理介面。
- **不得**把用戶個資或 NNC1 原文送給 LLM 供應商以外的第三方；送 LLM 前做 PII redaction（除 NNC1 抽取這個必要用途）。
- Anthropic API 用量不做 training（確認 commercial terms）。

## 5. 跨境與內地

- 伺服器放**香港或海外**（建議 AWS ap-east-1 / Fly.io HKG）。**不放內地，不做 ICP 備案**。
- 面向內地用戶的推廣文案受《廣告法》約束 → 避免絕對化用語（同 §2）。
- 不主動向內地用戶收集敏感個資；支付若涉內地渠道，另行法律評估。

## 6. 受規管活動的邊界（最重要）

平台**是**：資訊比較 + 評價 + 需求撮合的中立平台。
平台**不是**：TCSP 服務商、中介人、代理人。

紅線：
- 不代收代付客戶付給秘書公司的服務費（涉 MSO 牌照風險）→ 平台只向 **秘書公司** 收訂閱／額度費。
- 導流佣金若採「成交抽佣」，必須：
  1. 在該 Provider 頁面**明確披露**（`CommissionDisclosure` model 渲染）
  2. **不得**因佣金而影響自然排序（贊助位獨立且標示）
  3. 保留合約與佣金紀錄
- 不提供公司秘書法定職責的建議（那是專業意見）。
- 不做 KYC/AML 代理服務。

## 7. 內容免責（全站 footer + Advisor Agent 回覆尾部）

```
本平台為獨立資訊比較平台，並非香港公司註冊處或任何政府機構，亦非信託或公司服務持牌人。
平台所載資料僅供一般參考，不構成法律、稅務、會計或任何專業意見，亦不構成對任何服務商的推薦或保證。
銀行開戶與否由銀行全權決定，本平台不對開戶結果作任何承諾。
用戶應自行向官方登記冊核實牌照狀態，並在需要時諮詢持牌專業人士。
```

## 8. 上線前 checklist（由 `compliance-review` skill 執行）

- [ ] 全站 footer 免責文字存在且三語版本齊全
- [ ] 所有 TCSP 數據頁有來源 + last_synced_at
- [ ] banned phrase 檢查已套用在 AI 輸出、Provider 文案、行銷頁
- [ ] Advisor Agent 無 citation 時拒答的測試存在且通過
- [ ] NNC1 purge task 存在且有測試
- [ ] PICS / 私隱政策 / 服務條款 / Cookie 頁面存在
- [ ] 佣金披露元件在有 `commission_agreement=true` 的頁面渲染
- [ ] 贊助位有「贊助」標示
- [ ] 無評價公司不顯示 5.00 分（見 RATING_SYSTEM §4）
- [ ] 已離開官方名單的公司仍可瀏覽，且每處都渲染 `registry.notices.deregistration_notice()`；文案不含任何暗示執法行動的措辭，並已經律師覆核
- [ ] DSAR 後台流程可用
- [ ] Agent kill switch 全部可用 — P4-3 已落地三段：`AGENTS_ENABLED`（全域）、
      `AGENT_ENABLED_{NAME}`（逐 agent，目前 `REVIEW_MODERATION` / `NNC1_EXTRACTION`）、
      沒有 `ANTHROPIC_API_KEY` 即視為關閉。三者都只從環境變數讀，見 `.env.example`。
      **驗收方式不是「設定存在」而是「關掉之後平台照常運作」**：關掉時 agent 走規則式 fallback，
      評價照樣進審核佇列、NNC1 照樣可被人手核驗，`AgentRun` 照樣寫一列 `status=fallback`。
- [ ] 送進 LLM 的評價正文已 redact（A4 不是 §4 的例外；用 `[PHONE]` 這類佔位符而非刪除）
- [ ] `AgentRun.input_ref` 只存形狀摘要（`body_chars=412`），畫面上看不到評價原文或 NNC1 內容
- [ ] `AgentRun` 在 admin 不可新增／不可修改／不可刪除（可被改寫的稽核紀錄等於沒有稽核紀錄）
- [ ] 每個已啟用的 agent 都有跑過 eval 且分數記錄在 `apps/agents/evals/RESULTS.md`
      （**目前 A3 無 eval、A4 只有合成樣本，兩者都不得在生產啟用**）
