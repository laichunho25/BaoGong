# QS Matching Platform — VS Code Claude 啟動包

> **用法**：把整個 `qs-platform-kit/` 資料夾的內容，複製到你的空專案根目錄。
> 然後在 VS Code 開啟該資料夾 → 開 Claude Code → 貼上下面 **【主啟動 Prompt】**。
>
> Claude 會自動讀取 `CLAUDE.md`（每次對話都會載入）與 `.claude/skills/`（按需觸發）。
> 之後每一個開發階段，直接從 `docs/PROMPT_LIBRARY.md` 複製對應 prompt。

## 資料夾結構

```
your-project/
├── CLAUDE.md                       # 專案憲法 — Claude 每次都讀
├── docs/
│   ├── PRD.md                      # 產品需求
│   ├── ARCHITECTURE.md             # 技術架構與目錄規範
│   ├── DATA_MODEL.md               # 資料模型（權威來源）
│   ├── RATING_SYSTEM.md            # 評分演算法規格
│   ├── AI_AGENTS.md                # 7 個 AI Agent 的完整規格
│   ├── COMPLIANCE.md               # 合規／免責／PDPO 紅線
│   ├── DESIGN_SYSTEM.md            # 介面語言：token、元件、首頁結構
│   ├── DEPLOY_RENDER.md            # 部署
│   ├── ROADMAP.md                  # 分階段交付計劃
│   └── PROMPT_LIBRARY.md           # 每階段可直接複製的 prompts
└── .claude/skills/
    ├── django-feature/SKILL.md     # 新增 Django 功能的標準流程
    ├── tcsp-data-sync/SKILL.md     # TCSP 官方數據同步與 diff
    ├── ai-agent-builder/SKILL.md   # 新增／修改 AI Agent
    ├── review-verification/SKILL.md# NNC1 核驗與評價審核
    ├── frontend-page/SKILL.md      # 前端頁面（HTMX + Tailwind）
    └── compliance-review/SKILL.md  # 上線前合規檢查
```

---

## 【主啟動 Prompt】— 第一次對話貼這段

```text
你是這個專案的 lead engineer。專案代號 QS Matching Platform：一個香港 TCSP（信託或公司服務）
持牌秘書公司的比較、評價與報價撮合平台，主要服務內地客戶。

開工前請先做這四件事，不要急著寫 code：

1. 讀完 CLAUDE.md 和 docs/ 底下全部 7 份文件。讀完後用不超過 15 行，向我複述：
   - 平台的三個核心價值主張
   - 你認為技術上最高風險的三個點
   - docs 之間任何你發現的矛盾或缺口（很重要，請直說）

2. 確認我的本機環境：檢查是否已有 python3.12+、node 20+、docker、postgres client。
   缺什麼就告訴我安裝指令，不要自己亂裝。

3. 提出 Phase 0（專案骨架）的實作計劃，包含：目錄結構、依賴清單、docker-compose 服務、
   settings 分層方式、CI 檢查項目。等我說「開始」你才動手建檔。

4. 全程遵守 CLAUDE.md 裡的「不可違反規則」。任何涉及 docs/COMPLIANCE.md 紅線的
   實作，先停下來問我。

語言：跟我對話用繁體中文，code / commit message / docstring 用英文。
```

---

## 【每次新對話的暖機 Prompt】— 之後每次開新 session 貼這段

```text
讀 CLAUDE.md + docs/ROADMAP.md，然後跑 `git log --oneline -15` 和 `git status`，
告訴我目前進度停在哪個 Phase、有沒有未完成的 WIP。之後等我下一步指示。
```

---

## 【Skill 觸發速查】

在 Claude Code 對話中直接講這些話，就會觸發對應 skill：

| 你想做的事 | 講這句話 |
|---|---|
| 新增一個功能模組 | 「幫我加一個 XXX 功能」→ `django-feature` |
| 處理 TCSP 官方名單同步 | 「更新 TCSP 數據 / 處理牌照 diff」→ `tcsp-data-sync` |
| 新增或改 AI Agent | 「加一個 XXX agent / 改 matching agent 的 prompt」→ `ai-agent-builder` |
| NNC1 核驗、評價審核 | 「做評價驗證流程」→ `review-verification` |
| 做頁面 / UI | 「做一個 XXX 頁」→ `frontend-page` |
| 上線前檢查 | 「跑合規檢查」→ `compliance-review` |

也可以用 `/` 明確叫：`/django-feature`、`/ai-agent-builder` …

---

## 【建議的開發節奏】

一個 Phase = 一個 git branch = 一輪對話。每個 Phase 結束時對 Claude 說：

```text
Phase N 完成。請：
1. 跑 pytest + ruff + mypy，全綠才繼續
2. 更新 docs/ROADMAP.md 的完成狀態
3. 如果這個 Phase 改動了資料模型或 agent 行為，同步更新 docs/DATA_MODEL.md 或 docs/AI_AGENTS.md
4. 寫一個 conventional commit
5. 用 5 行總結這個 Phase 的技術債，寫進 docs/ROADMAP.md 的「Tech Debt」段落
```
