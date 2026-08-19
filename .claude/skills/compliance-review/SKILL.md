---
name: compliance-review
description: 對 包公 BaoGong 執行合規檢查 — 免責聲明、數據來源標註、禁用宣稱、PDPO 個資、AI 輸出邊界、佣金披露、受規管活動邊界。當使用者說「跑合規檢查」「上線前檢查」「compliance review」「這樣會不會有法律問題」「檢查免責」或準備 release 時觸發。
---

# compliance-review

依據：`docs/COMPLIANCE.md`。本 skill 是**執行檢查**，不是重寫政策。

> 這不是法律意見。最終須由香港律師覆核。本檢查的目的是確保 code 與政策一致。

## 執行方式

逐項檢查，每項輸出：`✅ 通過（證據：檔案:行號 / 測試名稱）` 或 `❌ 未通過（問題 + 修法）` 或 `⚠️ 需人工判斷`。
**不要只說通過，一定要給證據路徑。**

最後產出一份 markdown 報告存 `docs/compliance-reports/YYYY-MM-DD.md`。

---

## 檢查清單

### A. 數據來源與身分

- [ ] A1 每個顯示 TCSP 數據的 template 都 include `data_source_notice.html`
      → `grep -rL "data_source_notice" templates/pages/providers/`
- [ ] A2 `last_synced_at` 實際渲染且時區為 HKT
- [ ] A3 有連向官方登記冊的連結
- [ ] A4 全站無「官方」「政府認證」「註冊處指定」等自稱
      → 跑 banned phrase 掃描（含 template、fixture、seed 文章）
- [ ] A5 `registry.Licensee` 除 sync 服務外無其他寫入點
      → `grep -rn "Licensee.objects.\(create\|update\|bulk_\)" apps/ --include=*.py`，
        白名單只有 `apps/registry/services.py`

### B. 禁用宣稱

- [ ] B1 `apps/core/compliance.py::check_banned_phrases` 存在且清單與 `COMPLIANCE.md §2` 一致
- [ ] B2 套用於：全部 AI agent 的 postprocess、Provider 自填欄位的 clean()、行銷頁 CMS
- [ ] B3 有測試覆蓋每一條禁用詞
- [ ] B4 無任何開戶成功率數字出現在 code、template、seed data
      → `grep -rniE "开户成功率|開戶成功率|success rate|100%|包过|包過|保证|保證" --include=*.py --include=*.html --include=*.md`

### C. 評價與誹謗

- [ ] C1 AI 無法自動刪除評價 —— `Review.status` 改為 `removed` 的路徑只有 moderator action
- [ ] C2 Dispute 流程存在，且有 SLA 欄位／提醒機制
- [ ] C3 `ReviewReply` 讓公司可回覆（OneToOne 約束存在）
- [ ] C4 Moderation 的 `suggested_redactions` 需人工確認才套用
- [ ] C5 留評表單明示「須基於親身經歷與可查證事實」

### D. 個人資料（PDPO）

- [ ] D1 PICS / 私隱政策 / 服務條款 / Cookie 頁面存在且三語
- [ ] D2 NNC1 檔案：加密 bucket + 私有 + 簽名 URL（無公開 URL）
- [ ] D3 `purge_nnc1_files` beat task 存在 **且有測試**
      → `grep -rn "purge_nnc1" apps/ tests/`
- [ ] D4 送 LLM 前有 PII redaction（NNC1 抽取除外）
- [ ] D5 DSAR 後台流程可用（查閱／更正／刪除）
- [ ] D6 Sentry 有敏感欄位 scrubbing 設定

### E. AI 邊界

- [ ] E1 每個 agent 都有 `fallback()` 且有測試
- [ ] E2 每個 agent 都有 kill switch 環境變數且生效
- [ ] E3 AdvisorAgent：citations 為空時拒答 —— **測試必須存在**
- [ ] E4 AdvisorAgent 回覆不含任何 Provider 名稱 —— filter + 測試
- [ ] E5 AdvisorAgent 每則回覆附免責文字
- [ ] E6 MatchingAgent 有 grounding check，且 grounding violation 有測試
- [ ] E7 NNC1 比對是規則不是 LLM 決策；`document_looks_authentic=false` 只轉人工
- [ ] E8 無任何 AI 輸出直接寫入正式欄位（全部經 pending_review / is_ai_suggested）
      → 人工 review `apps/agents/` 的所有 service 呼叫點

### F. 商業模式邊界

- [ ] F1 平台無代收代付客戶服務費的流程（billing 只向 Provider 收費）
      → 檢查 `apps/billing/` 的 payer 一律是 Provider
- [ ] F2 `CommissionDisclosure` 在 `commission_agreement=true` 的頁面渲染 —— 有測試
- [ ] F3 贊助置頂位獨立且有「贊助」標示，未混入自然排序
      → 檢查排序 service：`premium` 不得加分進 organic score
- [ ] F4 平台文案未自稱提供公司秘書／代辦註冊服務

### G. 顯示正確性

- [ ] G1 0 條已驗證評價的 Provider **不顯示 5.00 分** —— 有 template 測試
- [ ] G2 未驗證評價明確標示且不計入主分
- [ ] G3 評分 tooltip 說明貝葉斯先驗機制
- [ ] G4 全站 footer 免責文字（三語）

### H. 基礎安全

- [ ] H1 `python manage.py check --deploy` 無 warning
- [ ] H2 CSP / HSTS / X-Frame-Options header
- [ ] H3 上傳檔案 MIME + magic bytes + 病毒掃描
- [ ] H4 Rate limiting 在：登入、註冊、留評、RFQ、報價、Advisor 問答
- [ ] H5 無 secret 進 git（`git log -p | grep -iE "api[_-]?key|secret"` 抽查）
- [ ] H6 伺服器區域為香港或海外，非內地

---

## 輸出格式

```markdown
# Compliance Review — YYYY-MM-DD
Commit: <sha>   Reviewer: Claude

## 摘要
通過 X / 未通過 Y / 需人工判斷 Z
**阻擋上線的項目：** <列表，或「無」>

## 明細
### A. 數據來源與身分
- ✅ A1 通過 — templates/pages/providers/{list,detail}.html:3
- ❌ A4 未通過 — templates/pages/home.html:42 出現「官方認證」
      修法：改為「持牌狀態已對照官方登記冊」

## 需使用者拍板
1. <議題> — 涉及 COMPLIANCE.md §N，建議諮詢律師

## 建議送律師覆核的清單
...
```

## 紅線提醒

檢查過程中若發現以下任一項，**立即停止並告知使用者，不要自行修改**：
- 平台代收客戶款項的任何跡象
- AI 自動刪除用戶內容
- 官方數據被人手覆寫
- 保證開戶成功的宣稱
- 伺服器或資料庫位於內地
