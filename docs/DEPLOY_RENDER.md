# DEPLOY_RENDER — Render 部署與環境變數

> 對應 `render.yaml`（Blueprint）。本文只講 Render；本機開發看 `README.md`。

## 1. 為什麼是 Singapore

Render **沒有香港 region**。可選：Oregon / Ohio / Virginia / Frankfurt / Singapore。
選 **Singapore**：延遲對香港與內地最低，且屬「海外」，滿足 `COMPLIANCE.md §5`
（伺服器放香港或海外，不放內地、不做 ICP 備案）。

四個服務（web / worker / beat / keyvalue）與 Postgres **必須同一 region**，
否則走公網、慢且產生 egress。

## 2. 環境變數分三類

### 2.1 Render 自動提供（你不要手動設）

| 變數 | 來源 | 用途 |
|---|---|---|
| `DATABASE_URL` | `fromDatabase: qs-postgres` | Postgres 連線字串（internal） |
| `REDIS_URL` | `fromService: qs-keyvalue` | Celery broker + result backend |
| `PORT` | Render 注入 | gunicorn 必須綁這個，不是 8000 |
| `RENDER_EXTERNAL_HOSTNAME` | Render 注入 | `prod.py` 自動加進 `ALLOWED_HOSTS` 與 `CSRF_TRUSTED_ORIGINS` |

`config/settings/prod.py` 已經處理 `RENDER_EXTERNAL_HOSTNAME`，所以**首次部署不需要
手動填 `ALLOWED_HOSTS`**。之後綁自訂網域時，才要在 dashboard 加
`ALLOWED_HOSTS=qs.example.com` 與 `CSRF_TRUSTED_ORIGINS=https://qs.example.com`。

### 2.2 Blueprint 自動生成

| 變數 | 做法 |
|---|---|
| `SECRET_KEY` | `generateValue: true`（web），worker/beat 用 `fromService` 取同一個值 |

三個服務共用同一把 `SECRET_KEY` 是必要的：session、signed URL、密碼重設 token
都靠它，值不同會導致 worker 產生的簽章 web 驗不過。

### 2.3 你必須在 Render dashboard 手動填（`sync: false`）

在 **Env Group `qs-shared`** 裡填一次，四個服務共用：

| 變數 | 從哪拿 | 沒填會怎樣 |
|---|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com → Settings → API Keys | `prod.py` 直接 `ImproperlyConfigured`，服務起不來 |
| `S3_ENDPOINT_URL` | 見 §3 | 同上 |
| `S3_BUCKET` | 見 §3 | 同上 |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | 見 §3 | 同上 |
| `SENTRY_DSN` | sentry.io 專案設定 | 可留空，只是沒有告警 |
| `TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET` | Cloudflare → Turnstile | P3 註冊流程才用到 |

**起不來是刻意的**：`prod.py` 對缺失 secret 直接拋錯，不給不安全預設值。

## 3. 物件儲存（Render 沒有）

Render **不提供 S3**。NNC1 上傳檔（加密個資，`COMPLIANCE.md §4`）必須有 S3-compatible
儲存。兩個選項：

| | AWS S3 `ap-east-1`（香港） | Cloudflare R2 |
|---|---|---|
| 位置 | 香港 —— `COMPLIANCE §5` 明文點名 | APAC（無法精確指定香港） |
| Egress 費用 | 有 | 無 |
| 成本 | 較高 | 較低 |
| `S3_ENDPOINT_URL` | 留空（用預設 AWS endpoint） | `https://<account_id>.r2.cloudflarestorage.com` |

**建議**：NNC1 那個 bucket 用 **AWS S3 `ap-east-1`**。它裝的是個資，
`COMPLIANCE §5` 直接寫了 ap-east-1，位置說得清楚在合規上比省錢重要。
其餘非個資檔案（原始 TCSP CSV、logo、辦公室照片）可以放 R2 省成本。

> 這一條牽涉 `COMPLIANCE.md §4/§5`，**要你拍板**。在你決定前，P0 只把變數留成
> 可設定，沒有寫死任何供應商。

Bucket 必須 **private**，一律用簽名 URL 存取（`prod.py` 已設 `default_acl: private`、
`querystring_auth: True`）。

## 4. 首次部署步驟

```bash
# 1. 推上 GitHub（Render 從 repo 讀 render.yaml）
git remote add origin git@github.com:<you>/qs-platform.git
git push -u origin main
```

2. Render Dashboard → **New → Blueprint** → 選這個 repo → Apply。
3. 第一次會失敗（`sync: false` 的變數還是空的）。到 **Env Groups → qs-shared**
   填好 §2.3 的值 → Manual Deploy。
4. 開 `https://<service>.onrender.com/healthz`，應該回 `200` 與
   `{"status":"ok","checks":{"database":{"ok":true},"redis":{"ok":true}}}`。

`healthCheckPath: /healthz` 會讓 Render 在 DB 或 Redis 掛掉時把該版本判定為不健康、
不切流量。這是刻意的。

## 5. pgvector

Render Postgres 支援 pgvector，但 extension 要自己開。P6（content app）之前，
在 Render 的 psql console 跑一次：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- DATA_MODEL.md 的 GIN trigram index 要用
```

或寫進 P1 的第一個 migration（`django.contrib.postgres.operations.CreateExtension`）——
**建議用 migration**，這樣本機、CI、Render 三邊一致，不會有人忘記。

## 6. Celery beat 只能有一個

`qs-beat` 的 instance count 必須固定為 **1**。兩個 scheduler = 每日 TCSP 同步跑兩次，
會產生重複的 `SyncRun` 與 `LicenseeChange`。不要對 beat 開 autoscaling。

## 7. 方案與費用（2026 年價目，僅供估算）

| 服務 | 方案 | 說明 |
|---|---|---|
| qs-web | Starter | Free 方案會 spin down，冷啟動對 SEO 與 P95 < 800ms 是災難 |
| qs-worker | Starter | |
| qs-beat | Starter | 負載極低，但不能省 |
| qs-keyvalue | Starter | `maxmemoryPolicy: noeviction`——broker 不准丟任務 |
| qs-postgres | Basic 256MB 起 | **Free Postgres 30 天後會被刪除**，不要用 |

## 8. 尚未處理

- **備份**：Render Postgres 有自動 daily backup，但還原演練腳本是 P8 的事。
- **Image 體積**：目前 `Dockerfile` 連 dev 依賴一起裝（單一 image，本機／CI／prod 一致）。
  要瘦身就加 build stage 分離，列為技術債。
- **CSP / rate limiting / HSTS preload 提交**：P8。
