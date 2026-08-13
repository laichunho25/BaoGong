---
name: review-verification
description: 處理 QS Matching Platform 的評價真實性驗證與審核流程 — NNC1 文件核驗、評分演算法、moderation 佇列、公司回覆與爭議申訴。當使用者提到「評價」「評分」「NNC1」「核驗」「審核」「假評」「申訴」「rating」「moderation」時觸發。
---

# review-verification

先讀 `docs/RATING_SYSTEM.md`（演算法）與 `docs/AI_AGENTS.md` A3/A4（agent 規格）。

## 這個功能是平台的差異化核心

其他比較站都是假評滿天飛。我們的護城河是：**評價必須用 NNC1 文件證明確有合作關係**。
因此這裡的錯誤成本極高 —— 誤放一條假評，護城河就沒了；誤擋一條真評，用戶就流失。

## 一、NNC1 核驗流程

```
用戶上傳 NNC1
  → MIME + magic bytes 驗證（PDF/JPG/PNG）+ 大小 ≤ 10MB
  → clamav 掃描
  → 加密存 S3 私有 bucket，purge_at = now + 90d
  → A3 Nnc1ExtractionAgent 抽取（vision，haiku）
  → 【規則比對，非 LLM 決策】
  → 結果寫 Nnc1Verification
```

**比對規則（照這個順序，不准改成讓 LLM 判斷）**：

```python
if extracted.secretary_licence_no and exact_match(Licensee.licence_no):
    result = "pass"
elif trigram_similarity(extracted.secretary_name, licensee.name_*) >= 0.88:
    result = "pass"
elif 0.70 <= similarity < 0.88 or extraction_confidence < 0.8:
    result = "needs_human"
else:
    result = "fail"

# 額外強制：
if not extracted.document_looks_authentic:
    result = "needs_human"      # 絕不因 AI 說可疑就 fail
if extracted.quality_issues:
    result = "needs_human"
```

**false-pass 是最嚴重的錯誤。寧可 needs_human 佔用人力，不可放行假件。**

隱私要求（`docs/COMPLIANCE.md §4`）：
- 只抽取 schema 定義的欄位，不存全文 OCR。
- `purge_nnc1_files` Celery beat 每日跑，刪 `purge_at < now` 的檔案（保留 `Nnc1Verification` 的抽取結果與比對結論，但清空可識別欄位）。
- **這個 purge task 必須有測試**，這是合規 checklist 項目。

## 二、評分演算法

完全依 `docs/RATING_SYSTEM.md`。實作在 `apps/reviews/services.py::recompute_provider_rating`。

必須有的測試（少一條都不算完成）：

```python
def test_no_reviews_shows_no_score():          # rating_count=0 -> UI 不顯示分數
def test_single_verified_45_gives_495():       # (10*5 + 4.5)/11 = 4.95
def test_unverified_excluded_from_main_score():
def test_bank_support_excluded_when_not_used():
def test_recompute_is_idempotent():
def test_hidden_review_removed_from_score():
```

**UI 鐵律**：0 條已驗證評價 → 空狀態，**絕不顯示 5.00**。這是 `docs/COMPLIANCE.md §8` 的檢查項。

觸發重算：評價 publish / hide / remove / dispute 決議後，發 Celery task（去抖動：同一 provider 5 秒內只跑一次）。

## 三、Moderation 佇列

A4 ReviewModerationAgent 輸出 → **規則決定動作**：

```python
if "personal_data_leak" in labels or "defamation_risk" in labels:
    action = "human_review"  # 不看 AI 建議
elif severity == "high":
    action = "human_review"
elif severity == "none" and confidence >= 0.8 and author.is_verified:
    action = "publish"
else:
    action = "human_review"
```

**AI 絕不自動刪除評價**（`docs/COMPLIANCE.md §3`）。最嚴重是 `hidden` + 通知作者與理由。

Moderator 介面必須有：
- 原文 + AI 標籤 + 理由 + 建議遮蔽片段（一鍵套用）
- 該用戶的歷史評價、該公司近期評價曲線（偵測異常暴增）
- 決策按鈕 + 必填理由
- 記錄 `AgentFeedback(verdict)` → 餵回 eval set

## 四、反作弊檢查清單

實作時逐條確認：
- [ ] DB unique(provider, author) — 一人一公司一評
- [ ] 留評需郵箱驗證 + Turnstile
- [ ] 同一 provider 24h 內評價數 > 閾值 → 全數轉人工
- [ ] 作者郵箱網域 == provider 網站網域 → 自動標記 `possible_self_review`
- [ ] 同 IP / 裝置指紋多筆 → 標記
- [ ] 註冊未滿 24h 的帳號留評 → 轉人工
- [ ] 已驗證評價的作者若之後被判定造假 → 連帶重算該 provider 分數

## 五、爭議申訴

秘書公司提出 Dispute → 5 個工作天內處理（合規要求）→
可選：用 opus 生成仲裁草稿（`ai_arbitration_draft`）→ **人手決定** keep / hide / amend / remove →
記錄理由 → 通知雙方。

公司對每則評價有**一次公開回覆權**（`ReviewReply`，OneToOne）。

## 六、實作順序建議

1. Review / ReviewScore model + 評分演算法 + 全部測試（先不接 AI）
2. 手動 moderation 佇列（純人工也要能運作）
3. NNC1 上傳 + 加密存放 + purge task
4. 接 A3 抽取 agent
5. 接 A4 moderation agent
6. Dispute 流程

**每一層都要能在下一層壞掉時獨立運作。** AI 全掛 → 全人工，平台照常運行。
