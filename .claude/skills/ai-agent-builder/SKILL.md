---
name: ai-agent-builder
description: 在 QS Matching Platform 新增、修改或調校 AI Agent（matching、rfq intake、nnc1 extraction、review moderation、quote analysis、advisor RAG、registry diff）。當使用者說「加一個 agent」「改 prompt」「agent 輸出不對」「調整 AI 行為」「跑 eval」「AI 成本太高」時觸發。強制 structured output、fallback、eval 門檻與版本化 prompt。
---

# ai-agent-builder

先讀 `docs/AI_AGENTS.md`。那是 agent 的權威規格；本 skill 是**怎麼做**。

## 六條不可違反

1. **AI 不做最終決定。** 輸出一律 `suggestion` / `draft` / `flag`，由規則或人手 confirm 才生效。
2. **一律 structured output**（Anthropic tool use + pydantic 驗證）。禁止 regex 解析自由文字。
3. **每個 agent 必須有 `fallback()`** — 純規則、不呼叫 LLM、必須有測試。
4. **Prompt 存檔案並版本化**：`apps/agents/prompts/{name}_v{n}.md`。禁止 inline 在 Python。
5. **每次呼叫寫 `AgentRun`**：model、prompt_version、tokens、cost、latency、status、confidence。
6. **Kill switch**：`AGENT_ENABLED_{NAME}=false` 立即降級到 fallback，不需重新部署 code。

## 新增一個 Agent 的流程

### Step 1 — 先問「這真的需要 LLM 嗎？」

能用 SQL / 規則 / 正則做到的，就不要用 LLM。本專案的分工原則：

| 該用規則 | 該用 LLM |
|---|---|
| 篩選、排序分數、價格分位數、額度計算 | 自然語言理解、非結構化文件抽取 |
| 牌照號比對、trigram 相似度 | 生成解釋文案、分類語意風險 |
| severity 判定、業務決策 | 摘要、改寫、追問問題生成 |

**典型正確架構**：SQL 硬篩 → LLM 只做它擅長的那一小段 → 規則做最終決策。
（見 A2 MatchingAgent：SQL 篩 Top 30，LLM 只排序＋寫理由，grounding check 再過濾。）

如果評估後不需要 LLM，**直接告訴使用者**並提出規則方案。

### Step 2 — 定義 schema（先於 prompt）

```python
# apps/agents/schemas.py
class MatchItem(BaseModel):
    provider_id: UUID
    rank: int = Field(ge=1)
    fit_score: float = Field(ge=0, le=1)
    reasons: list[str] = Field(max_length=3)
    concerns: list[str] = Field(max_length=2)


class MatchingOut(BaseModel):
    items: list[MatchItem]
    unmatched_requirements: list[str]
```

Schema 設計原則：
- 用 `Literal` / `Enum` 收斂自由文字欄位。
- 每個生成型欄位加長度上限（防 LLM 話癆燒 token）。
- 一律有 `confidence: float`。
- 不確定的欄位型別用 `X | None`，讓模型能誠實留空，**不要逼它猜**。

### Step 3 — 寫 prompt 檔

`apps/agents/prompts/{name}_v1.md` 結構：

```markdown
# Role
你是 <角色>。你的唯一任務是 <一句話>。

# Context
{{ 用 Django template 語法注入的結構化資料 }}

# Rules
- 只能引用 Context 中實際存在的資料，不得推斷或補充。
- 禁止使用「保證」「一定」「100%」等字眼。
- 不得提及開戶成功率的任何數字。
- 資料不足時，把欄位留空並在 confidence 反映，不要編造。

# Output
呼叫 `submit_result` tool。不要輸出其他文字。
```

規則：
- Prompt 用**英文寫指令、中文寫輸出要求**（模型指令遵循較穩，輸出語言明確）。
- 把 banned phrases 明寫進 prompt，**同時**在 code 層做 filter（雙保險）。
- 少即是多：不要塞 10 個 few-shot；先 0-shot 跑 eval，不夠再加 2–3 個針對性例子。

### Step 4 — 實作

```python
class MatchingAgent(BaseAgent):
    name = "matching"
    model = settings.AGENT_MODELS["matching"]  # claude-sonnet-5
    prompt_file = "matching_v1.md"
    output_schema = MatchingOut
    max_tokens = 2048
    timeout_s = 30

    def build_context(self, ctx: dict) -> dict:
        # 所有檢索/篩選在這裡用 SQL 做完，LLM 只看已篩好的資料
        ...

    def postprocess(self, out: MatchingOut, ctx: dict) -> MatchingOut:
        # grounding check：reason 找不到依據就丟棄
        # banned phrase filter
        ...

    def fallback(self, ctx: dict) -> AgentResult:
        # 純 RATING_SYSTEM §5 排序 + 模板理由
        ...
```

必做：
- PII redaction 在 `build_context` 就做掉（除非該 agent 必需，如 NNC1 抽取）。
- 每日預算檢查：超過 `AGENT_BUDGET_DAILY_USD` → 直接 fallback + 告警。
- Retry：schema 驗證失敗時把錯誤訊息回饋給模型重試一次，第二次還失敗 → fallback。

### Step 5 — Eval（沒有 eval 不准上線）

```
apps/agents/evals/{name}/
├── golden.jsonl        # ≥ 20 筆 {input, expected, notes}
├── metrics.py          # 該 agent 的評分函式
└── README.md           # 門檻與最近一次分數
```

跑法：`pytest -m eval -k matching`（標記排除於 CI，手動跑）。
結果寫進 `apps/agents/evals/RESULTS.md`：日期、prompt 版本、模型、各項分數。

各 agent 門檻見 `docs/AI_AGENTS.md`。**分數退步就不准合併。**

### Step 6 — 觀測

確認 `/admin/agents/` 能看到這個 agent 的：呼叫數、成功率、fallback 率、schema 失敗率、p95 latency、累計成本。

## 改 Prompt 的流程

1. 複製 `{name}_v{n}.md` → `{name}_v{n+1}.md`，改新檔（**不改舊檔**）
2. 更新 `docs/AI_AGENTS.md` 對應段落
3. 對 golden set 跑 **新舊版對比** eval，把兩組分數並列給使用者
4. 新版不退步 → 更新 `prompt_file` 指向新版；退步 → 說明原因，不切換
5. 保留舊版檔案供回退；`AgentRun.prompt_version` 會記錄實際使用版本

## 成本失控時的處理順序

1. 看 `/admin/agents/` 找出成本最高的 agent
2. 降模型（sonnet → haiku）先跑 eval，分數可接受就換
3. 縮 context（Top 30 → Top 15）
4. 加快取：相同 `input_hash` 在 N 小時內直接回傳上次結果
5. 改 async batch（非即時的 agent 如 A7 用 Message Batches API）
6. 最後才是調 `max_tokens`

## 常見失敗模式

| 症狀 | 原因 | 修法 |
|---|---|---|
| Schema 驗證失敗率 > 5% | schema 太複雜 / 欄位語意不清 | 拆成兩次呼叫，或把 Literal 選項寫進 prompt |
| Agent 幻覺出不存在的 provider | context 給太多、指令不夠硬 | grounding check（code 層），不要只靠 prompt |
| 理由都很空泛（「服務好」） | prompt 沒要求引用具體欄位 | 要求每條 reason 必須引用一個 context 欄位名 |
| Moderation 漏放誹謗 | 用了 auto-publish 太寬 | 規則優先：high severity 一律人工，不看 AI 建議 |
| NNC1 誤判通過 | 讓 LLM 做比對決策 | 比對必須是規則（exact → trigram → needs_human） |
