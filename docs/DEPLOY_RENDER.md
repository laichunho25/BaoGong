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
| `DATABASE_URL` | `fromDatabase: baogong-postgres` | Postgres 連線字串（internal） |
| `REDIS_URL` | `fromService: baogong-keyvalue` | Celery broker + result backend |
| `PORT` | Render 注入 | gunicorn 必須綁這個，不是 8000 |
| `RENDER_EXTERNAL_HOSTNAME` | Render 注入 | `prod.py` 自動加進 `ALLOWED_HOSTS` 與 `CSRF_TRUSTED_ORIGINS` |

`config/settings/prod.py` 已經處理 `RENDER_EXTERNAL_HOSTNAME`，所以**首次部署不需要
手動填 `ALLOWED_HOSTS`**。自訂網域則要自己列——`render.yaml` 的 `baogong-shared`
已經把 `ALLOWED_HOSTS`、`CSRF_TRUSTED_ORIGINS`、`SITE_URL` 三個都填成
`www.baogong.com.hk`（見 §10）。`.onrender.com` 那個主機名仍然照常可用，
因為 `prod.py` 是「附加」而不是「取代」。

### 2.2 Blueprint 自動生成

| 變數 | 做法 |
|---|---|
| `SECRET_KEY` | `generateValue: true`（web），worker/beat 用 `fromService` 取同一個值 |

三個服務共用同一把 `SECRET_KEY` 是必要的：session、signed URL、密碼重設 token
都靠它，值不同會導致 worker 產生的簽章 web 驗不過。

### 2.3 你必須在 Render dashboard 手動填（`sync: false`）

在 **Env Group `baogong-shared`** 裡填一次，四個服務共用：

| 變數 | 從哪拿 | 沒填會怎樣 |
|---|---|---|
| `ADMIN_URL` | 自己生：`python -c "import secrets;print(secrets.token_urlsafe(12))"` | `prod.py` 直接 `ImproperlyConfigured`；填 `admin` 也會被拒（見 §8） |
| `ANTHROPIC_API_KEY` | console.anthropic.com → Settings → API Keys | `prod.py` 直接 `ImproperlyConfigured`，服務起不來 |
| `S3_PRIVATE_BUCKET` | 見 §3，**與公開 bucket 分開的私有 bucket** | 同上（認領證明檔無處可放） |
| `S3_ENDPOINT_URL` | 見 §3 | 同上 |
| `S3_BUCKET` | 見 §3 | 同上 |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | 見 §3 | 同上 |
| `EMAIL_HOST_PASSWORD` | Resend → API Keys（見 §3.2） | `prod.py` 直接 `ImproperlyConfigured`。沒有它就沒有郵箱驗證、沒有成員邀請 |
| `SENTRY_DSN` | sentry.io 專案設定 | 可留空，只是沒有告警 |
| `TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET` | Cloudflare → Turnstile | P3 註冊流程才用到 |

**起不來是刻意的**：`prod.py` 對缺失 secret 直接拋錯，不給不安全預設值。

### 2.4 一次看清楚缺哪些：`scripts/check_env.py`

清單的唯一真實來源是 [`config/settings/env_spec.py`](../config/settings/env_spec.py)。
`prod.py` 與檢查腳本都讀它，所以兩邊永遠一致。

```bash
python scripts/check_env.py                  # 檢查當前環境
python scripts/check_env.py --env-file .env  # 部署前先驗本機檔案
python scripts/check_env.py --show-optional  # 連「可留空但功能會關掉」的一起列
```

輸出長這樣，一次列完，缺一個就 exit 1：

```
check_env: 2 problem(s) - production will not boot:
  - ANTHROPIC_API_KEY is not set - every AI agent call; no key means no matching, ...
  - ADMIN_URL is not set - secret prefix for the admin console
```

三個服務的 `dockerCommand` 都以它開頭，所以**環境沒填好時看到的是這份清單，
不是一段講「第一個被讀到的 secret」的 Django traceback**。`prod.py` 本身也改成
一次收集全部問題再拋一個錯——以前是讀到第一個缺的就炸，填一個、重啟、再炸下一個。

腳本刻意不 import Django，也不 import `config/__init__.py`（那裡會拉 Celery），
用檔案路徑載入 `env_spec`：需要它的時刻，正好是應用起不來的時刻。

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

### 3.1 檔案掃毒（Render 沒有託管 ClamAV）

`FILE_SCANNER_BACKEND` **刻意不寫進 `render.yaml`**。不設就是 `UnavailableScanner`，
它對每個檔案回 `pending`，而 `pending` 在我們的規則裡等於「不可讀」——所以 logo 發佈不了、
認領證明也打不開。這是 fail-closed 的預期狀態，不是壞掉：另一個選項是把沒掃過的 bytes
送到每一位訪客眼前。

**現況（首次部署）**：不開掃毒器。`upload_logo()` 直接拒收，管理頁把上傳框整個拿掉並寫明
「標志上傳功能正在準備中」。拿掉表單擋不住記得 URL 的人，所以拒絕寫在 service 層而不是模板。
NNC1 與認領證明照舊可以上傳（那條路徑本來就假設要等人審），只是永遠停在待掃描。

**要開啟時**，在 `render.yaml` 加一個私有服務：

```yaml
  - type: pserv
    name: baogong-clamav
    runtime: image
    image:
      url: docker.io/clamav/clamav:stable
    region: singapore
    plan: standard        # clamd 載入病毒庫要 ~2GB RAM，starter 的 512MB 會 OOM
    ipAllowList: []       # 私有網路而已，不對外
```

然後在 `baogong-shared` 加兩個變數：

```yaml
      - key: FILE_SCANNER_BACKEND
        value: apps.core.scanning.ClamAvScanner
      - key: CLAMAV_HOST
        value: baogong-clamav   # Render 私有網路的服務名
```

`CLAMAV_PORT` 預設 3310，不用設。開啟後 `scanning_available()` 變 `True`，上傳框自己會回來。

> 掃不到 clamd 時 `ClamAvScanner` 回 `ERROR` 而不是放行——連不上掃毒器等於沒掃過。
> 所以 pserv 掛掉的後果是「審核卡住」，不是「未掃描的檔案上線」。這是刻意的取捨。

### 3.2 寄信（Resend）

Django 預設的 backend 是 `smtp` 指向 `localhost:25`。Render 的容器裡沒有 MTA，
所以**不設定不等於關掉寄信**，等於每一次寄送都在 Celery task 裡丟 `ConnectionRefusedError`
而沒有人看到。郵箱驗證、成員邀請、認領與 logo 決定通知全部靠它，因此 `prod.py`
把 API key 列為**啟動必要**：寄不出信的版本乾脆不要起來。

| 變數 | 值 | 說明 |
|---|---|---|
| `EMAIL_HOST` | `smtp.resend.com` | `prod.py` 預設，換供應商才要設 |
| `EMAIL_PORT` | `587` | 預設，STARTTLS |
| `EMAIL_HOST_USER` | `resend` | Resend 的固定使用者名 |
| `EMAIL_HOST_PASSWORD` | `re_...` | **Resend API key，dashboard 手填** |
| `DEFAULT_FROM_EMAIL` | `包公 BaoGong <no-reply@baogong.com.hk>` | 已在 `render.yaml` |

`prod.py` 另外擋兩件事：`EMAIL_USE_TLS` 與 `EMAIL_USE_SSL` 同時為真（設定衝突，會靜默
連不上），以及 `DEFAULT_FROM_EMAIL` 還留著 `example.com`（沒有收件方會收）。

**先做網域驗證再部署**：Resend 要在 `baogong.com.hk` 加 SPF 與 DKIM 的 TXT 記錄。
沒驗證之前 API key 是有效的、程式也不會報錯，信會被**收件方**丟掉——這種失敗最難查，
因為我們這一端看起來一切正常。DNS 記錄與 §10.3 的網域設定一起做。

## 4. 首次部署步驟

```bash
# 1. 推上 GitHub（Render 從 repo 讀 render.yaml）
git remote add origin git@github.com:<you>/baogong.git
git push -u origin main
```

2. Render Dashboard → **New → Blueprint** → 選這個 repo → Apply。
3. 第一次會失敗（`sync: false` 的變數還是空的）。看 web service 的 log，
   `check_env` 會把缺的變數**一次列完**。到 **Env Groups → baogong-shared**
   照 §2.3 填好 → Manual Deploy。（想先在本機確認，見 §2.4。）
4. 開 `https://<service>.onrender.com/healthz`，應該回 `200` 與
   `{"status":"ok","checks":{"database":{"ok":true},"redis":{"ok":true}}}`。

`healthCheckPath: /healthz` 會讓 Render 在 DB 或 Redis 掛掉時把該版本判定為不健康、
不切流量。這是刻意的。

### 4.1 部署成功了，但網站看起來是空的

`/healthz` 回 200 只代表 process 活著。新資料庫裡沒有任何一列資料，所以首頁與目錄
會是空的。分兩種來源處理：

**指南文章——不用做任何事。** `apps/content/library/*.md` 隨 image 出貨，web 的
啟動指令在 migrate 之後跑 `load_articles`：已存在的 slug 會被**跳過**（後台的編輯
不會被覆寫），只有新增的檔案會寫進去。要覆寫得自己在 Shell 明講 `--update`。

**持牌名單——第一次要手動觸發一次。** beat 每天 **06:00 UTC**（香港 14:00）才跑，
所以剛部署完的目錄是空的。到 `baogong-web` → **Shell**：

```bash
python manage.py sync_tcsp --dry-run   # 先看下載與解析是否正常
python manage.py sync_tcsp             # 寫入官方名單（約 7,400 列）
python manage.py backfill_providers    # 為每一列建立可展示的公司頁
```

兩件事要知道：

- `sync_tcsp` 會把原始 CSV 存一份到 S3 存檔。**存檔失敗不會擋下同步**（只寫 log），
  所以 §3 的物件儲存還沒決定也可以先跑。
- 同步有健全性檢查：列數與上一次成功的差距過大會直接中止而**不寫入**——
  寧可舊資料，不要錯資料。第一次沒有上一次可比，所以會照寫。

### 4.2 網域還沒生效之前，先把 `SITE_URL` 指回 onrender

`render.yaml` 的 env group 預設把 `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` / `SITE_URL`
都寫成 `www.baogong.com.hk`。網域還沒買、DNS 還沒指過來的時候：

| 變數 | 網域生效前 | 網域生效後 |
|---|---|---|
| `ALLOWED_HOSTS` | 不用改（`prod.py` 會自動信任 `RENDER_EXTERNAL_HOSTNAME`） | 維持兩個網域 |
| `CSRF_TRUSTED_ORIGINS` | 不用改（同上，自動補上 onrender 那個） | 維持 |
| `SITE_URL` | **要改**成 `https://<service>.onrender.com` | 改回 `https://www.baogong.com.hk` |

只有 `SITE_URL` 非改不可，原因是它是唯一**沒有 request 可問**的地方：Celery 任務寄信時
用它組連結。指著一個還不存在的網域，等於每一封驗證信裡的連結都打不開，
而信箱驗證是沒有替代路徑的一步。

另外兩件事：

- **憑證沒簽發前不要用瀏覽器開那個網域。** `prod.py` 開了一年的 HSTS 加 preload，
  瀏覽器記下來之後你自己也連不進去，且無法清除。詳見 §10.4 第 4 步。
- Render dashboard 上 custom domain 顯示 **Unverified** 是 DNS 還沒指過來，
  不是部署失敗；服務本身照樣在 `<service>.onrender.com` 上跑。

### 4.3 帳號裡已經有別的 project 怎麼辦

一個 Render workspace 可以同時跑多個 Blueprint，彼此不共用任何東西。要注意的只有三件：

- **服務名稱全 workspace 唯一。** 這裡全部叫 `baogong-*`，跟舊 project 撞不到；
  真撞了 Blueprint apply 會直接失敗，不會覆蓋既有服務。
- **一個 repo 一份 `render.yaml`。** Blueprint 是綁 repo 的，所以這個平台要有**自己的
  GitHub repo**，不能塞進既有 project 的 repo 裡。Render 那邊連的還是同一個 GitHub 帳號，
  在 New → Blueprint 的清單裡挑新 repo 就行，不必另開 Render 帳號。
- **費用是相加的。** §7 那張表是這個 Blueprint 自己的成本，與既有 project 各算各的。
  Free Postgres 每個 workspace 只有一個而且 30 天後刪除，所以無論舊 project 有沒有用掉，
  這裡都要用付費方案。

### 4.4 名單新鮮度監控 `/healthz/registry`

`/healthz` 只答「這個 process 活著嗎」——每日同步悄悄停掉時它照樣回 200。
`/healthz/registry` 答的是「資料還值得顯示嗎」：最後一次**成功**同步超過 26 小時
（`?max_age_hours=` 可調）就回 **503**，否則 200。dry run 與 sanity abort 不算數。

```
GET /healthz/registry
200 {"healthy": true, "stale": false, "reason": "", "last_success_at": "...",
     "age_hours": 0.76, "max_age_hours": 26, "row_count": 7457,
     "last_run_status": "success", "unnotified_critical": 0}
```

**不要**把它設成 Render 的 `healthCheckPath`——名單過期不代表這版程式壞了，
把服務下線只會讓情況更糟。正確做法是掛外部 uptime monitor（UptimeRobot／
Better Stack／Render Cron 打 `curl -f`）盯 503。

不需登入，因為 monitor 要打得到；回傳內容不是網站本來就要公開的
（COMPLIANCE §1 的 `last_synced_at`、筆數）就是純數字。

### 4.5 只有 web 跑 migration，worker 與 beat 等它

`migrate` 只寫在 **web** 的 `dockerCommand` 裡。三個服務同時 migrate 會搶同一組鎖，
可能留下套用到一半的 schema——比崩一次糟得多，所以改 schema 的權限只給一個服務。

但 Render 是三個服務**同時**啟動的。凡是帶新 migration 的部署，worker 與 beat 都會先
碰到還沒建好的表。beat 最容易中招，因為 `DatabaseScheduler` 第一個 tick 就要讀
`django_celery_beat_*`：

```
django.db.utils.ProgrammingError: relation "django_celery_beat_clockedschedule" does not exist
```

它會崩、Render 重啟、web 那邊 migrate 完了就自己好——**但 log 裡留下一段跟真故障
一模一樣的 traceback**。真出事的時候，這種假警報會讓人把它當雜訊略過。

所以兩者改成 `scripts/wait_for_migrations.sh` 安靜地等：`migrate --check` 在有未套用
migration 時回非零，正好就是「等 web 做完」的條件。

等待是**有上限**的（預設 100 次 × 3 秒 = 5 分鐘，可用 `MIGRATION_WAIT_ATTEMPTS` /
`MIGRATION_WAIT_INTERVAL` 調）。真的連不上資料庫要大聲失敗，而不是永遠卡在 starting。

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

`baogong-beat` 的 instance count 必須固定為 **1**。兩個 scheduler = 每日 TCSP 同步跑兩次，
會產生重複的 `SyncRun` 與 `LicenseeChange`。不要對 beat 開 autoscaling。

## 7. 方案與費用（2026 年價目，僅供估算）

| 服務 | 方案 | 說明 |
|---|---|---|
| baogong-web | Starter | Free 方案會 spin down，冷啟動對 SEO 與 P95 < 800ms 是災難 |
| baogong-worker | Starter | |
| baogong-beat | Starter | 負載極低，但不能省 |
| baogong-keyvalue | Starter | `maxmemoryPolicy: noeviction`——broker 不准丟任務 |
| baogong-postgres | Basic 256MB 起 | **Free Postgres 30 天後會被刪除**，不要用 |

## 8. 內部控制台的位置（不是 `/admin/`）

Django admin 是全站權限最高的介面，而 `/admin/` 是掃描器第一個試的路徑。因此：

- 掛在 `ADMIN_URL` 指定的**秘密前綴**下（`config/urls.py`）。`prod.py` 缺值或填 `admin` 都拒絕啟動。
- `/admin/` 因而不存在，回一般 404 —— 不是 403，403 等於承認「這裡有東西」。
- 已登入但非 staff 的帳號打到正確前綴也只拿到 **404**（`apps/core/admin_site.py`）。
  Django 預設會顯示「你是 X，但無權限」，等於替猜中路徑的人確認答案。
- `ADMIN_IP_ALLOWLIST` 為第二道鎖：URL 秘密會從代理日誌、瀏覽器歷史、共享畫面外洩。
  Render 有自己的代理，所以 prod 預設 `ADMIN_TRUST_PROXY_IP=true`，且只採信
  `X-Forwarded-For` **最後一段**（左邊幾段是客戶端可偽造的）。
- `robots.txt` **刻意不列**這個路徑（列了就等於公開它）；改由 `X-Robots-Tag: noindex` 擋索引。
- `ADMIN_ENABLED=false` 可整個不掛載 URL，前綴外洩也沒有東西可打。

> 秘密前綴不是存取控制，只是把噪音擋掉。真正的控制是 `is_staff` + 強密碼；
> IP allowlist 是第二道。P8 再評估是否加 TOTP。

## 9. 尚未處理

- **檔案掃毒**：首次部署不開（§3.1）。在那之前 logo 上傳整個關閉，NNC1 與認領證明
  可以上傳但永遠停在待掃描——認領流程因此還不能完整走完，這是上線時就知道的缺口。

- **備份**：Render Postgres 有自動 daily backup，但還原演練腳本是 P8 的事。
- **Image 體積**：目前 `Dockerfile` 連 dev 依賴一起裝（單一 image，本機／CI／prod 一致）。
  要瘦身就加 build stage 分離，列為技術債。
- **CSP / rate limiting / HSTS preload 提交**：P8。

## 10. 正式網域 `www.baogong.com.hk`

### 10.1 為什麼是 `.com.hk`

平台講的是香港公司註冊，讀者多數在內地。`.com.hk` 說明了兩件 `.com` 說不出來的事：
主體在香港、受香港規則管。副作用也是好的——`.hk` 的註冊門檻本身就是一道信任背書。

**`.com.hk` 有註冊資格要求**：須為香港註冊公司（提供商業登記證 BR / CI 副本）。
向 HKIRC 認可註冊商申請（HKDNR、Cloudflare Registrar 不支援 `.hk`，
用 Gandi / 香港本地註冊商）。審批通常 1–3 個工作天，要留在切換排程裡。

同時把 `baogong.hk` 一併登記、301 導向 `www.baogong.com.hk`，成本很低，
但可以擋掉一個明顯的仿冒位置。

### 10.2 www 是正式的，apex 只做轉址

`www.baogong.com.hk` 是**唯一正式主機名**，`baogong.com.hk` 由 Render 轉址過去。
理由不是美感：頁面的 `<link rel="canonical">` 是用 `request.get_host()` 組出來的
（見 `templates/` 各頁的 `canonical` block），兩個主機名都回 200 就等於每一頁
發出兩個 canonical，`sitemap.xml` 與 `robots.txt` 也會跟著分裂。

另外 apex 不能設 CNAME（DNS 規範），只能靠註冊商的 ALIAS/ANAME 或 A record；
把 www 當正式的，等於把最脆弱的那一環放在轉址上，而不是放在主站上。

### 10.3 DNS 記錄

在網域註冊商的 DNS 面板設定，值以 Render dashboard 上顯示的為準：

| 類型 | 名稱 | 值 |
|---|---|---|
| CNAME | `www` | `baogong-web.onrender.com` |
| ALIAS / ANAME（若註冊商支援） | `@` | `baogong-web.onrender.com` |
| A（若不支援 ALIAS） | `@` | Render 提供的 anycast IP |

TTL 先設 **300 秒**，切換完確認無誤再調回 3600——出事時要能快速回退。

### 10.4 切換步驟

1. 網域註冊完成、可自行管理 DNS。
2. Render → `baogong-web` → Settings → Custom Domains，加入兩個網域
   （`render.yaml` 的 `domains:` 已經聲明，Blueprint 套用時會自動建立）。
3. 設好 §10.3 的 DNS，等 Render 顯示 **Verified**。
4. Render 自動簽發 Let's Encrypt 憑證（不需要自己買）。憑證未簽發前
   **不要**對外公布網址：`prod.py` 開了 `SECURE_HSTS_PRELOAD` 與一年的 HSTS，
   在憑證好之前被瀏覽器記下 HSTS，會讓人連不進來且無法自行清除。
5. 確認 `https://www.baogong.com.hk/healthz` 回 200，且
   `https://baogong.com.hk` 會 301 到 www。
6. 到 Google Search Console / Bing 提交 `https://www.baogong.com.hk/sitemap.xml`。

### 10.5 HSTS preload 的先後次序

`SECURE_HSTS_PRELOAD = True` 只是送出 header，**還沒**提交到瀏覽器的 preload 清單。
提交（hstspreload.org）要等網域穩定跑滿至少幾週——preload 一旦生效，
撤銷要等好幾個月，是全站最難回退的一個決定。列在 §9。

## 11. 服務改名的代價（`qs-*` → `baogong-*`）

Render 的 Blueprint 是**用名字認服務**的。如果這份 `render.yaml` 從來沒有 Apply 過，
下面整節可以略過——直接部署即可。

若已經 Apply 過，改名會被當成「刪掉舊的、建立新的」：

| 資源 | 改名的後果 | 做法 |
|---|---|---|
| `baogong-postgres` | **舊資料庫連同資料一起被刪** | 先在 Render 開新庫 → `pg_dump` 舊庫 → `pg_restore` 新庫 → 確認筆數 → 才 Apply |
| `baogong-web` | 服務重建，`.onrender.com` 主機名跟著變 | 舊網址失效；若已對外公布過，要留轉址 |
| `baogong-keyvalue` | Redis 重建，佇列中的任務遺失 | 選 Celery 佇列清空的時段做 |
| `baogong-shared` | Env group 重建，`sync: false` 的 secret **要重新填一次** | 動手前先把值抄下來 |

本機 `docker compose` 同理：project name 換了，volume 前綴跟著換，等於一個空的
Postgres。開發資料是可丟的，重來一次就好：

```bash
docker compose down -v          # 舊 volume 不會自己消失，要明確刪掉
docker compose up --build
docker compose run --rm web python manage.py seed_demo
```
