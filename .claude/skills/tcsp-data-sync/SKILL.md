---
name: tcsp-data-sync
description: 處理香港公司註冊處 TCSP 持牌人官方名單的每日同步、diff、異常告警與資料修復。當使用者提到「TCSP 同步」「更新名單」「licensee」「牌照 diff」「同步失敗」「data.gov.hk」「registry app」時觸發。包含 sanity check 中止規則與絕不覆蓋成空資料的保護。
---

# tcsp-data-sync

## 這是全站數據地基 — 錯了全盤皆錯

官方來源：
- CSV: `https://www.tcsp.cr.gov.hk/open-data/licensees.csv`
- XLSX: `https://www.tcsp.cr.gov.hk/open-data/licensees.xlsx`
- 查冊: `https://www.tcsp.cr.gov.hk`

## 鐵律

1. **`registry.Licensee` 只有 sync 任務能寫。** 其他任何 code 想改 → 拒絕，改去 `providers`。
2. **`raw` JSONField 存官方原始 row，永不修改。** normalize 後的值另存欄位。
3. **Sanity check 未過 → 完全不寫入。** 寧可資料舊一天，不可資料錯。
4. **從不硬刪除。** 消失的持牌人 → `status='inactive'` + 保留 `last_seen_at`。
5. **冪等。** 同一份檔跑 N 次，結果與跑 1 次相同，且不產生重複 `LicenseeChange`。

## 同步流程（實作於 `apps/registry/services.py`）

```
1. download(source_url) -> bytes            # timeout 60s, retry 3, UA 標示平台名稱
2. store_raw(bytes) -> s3_key               # raw/tcsp/YYYY-MM-DD-HHMM.csv
3. parse(bytes) -> list[LicenseeRow]        # 見下方欄位處理
4. sanity_check(rows, prev_sync)            # 未過 -> SyncRun(aborted_sanity) + 告警 + return
5. upsert(rows) -> UpsertResult             # bulk, by licence_no
6. mark_missing_inactive(seen_licence_nos)
7. compute_diff(prev_snapshot, rows) -> list[LicenseeChange]
8. RegistryDiffAgent.run(changes)           # 只寫文案，severity 由規則定
9. finalize SyncRun + invalidate cache + reindex
```

## Sanity check 規則

```python
ABORT if prev_row_count and abs(n - prev) / prev > 0.15      # 筆數變動 > 15%
ABORT if n < 100                                              # 明顯抓到錯誤頁面
ABORT if required_columns not in header                       # 官方改格式
ABORT if duplicate licence_no in file
WARN  if removed_count > 20                                   # 不中止，但升級告警
```
每一條都要有對應的 fixture 測試。

## 欄位處理（官方資料很髒，務必 normalize）

| 問題 | 處理 |
|---|---|
| 全形／半形空格混用 | `unicodedata.normalize("NFKC", s).strip()` |
| 公司名稱大小寫不一 | 存原文；另存 `name_en_normalized`（casefold）供比對 |
| 中英文名在同一欄或分欄不定 | 偵測 CJK 字元比例切分；切不開就整串塞 `name_zh` 並記 warning |
| 地址無結構 | 只做 `district` 的關鍵詞抽取（18 區字典），抽不到就 null，**不要猜** |
| 牌照號格式 `TC-000123` vs `TC000123` | normalize 成無連字號大寫；`raw` 保留原文 |
| BOM / encoding | 先試 `utf-8-sig`，失敗試 `big5hkscs`，記錄實際使用的 encoding |

**欄位名不要憑猜測。** 第一次實作時先跑 `--dry-run` 印出真實 header 給使用者確認。

## Diff 分類

```python
new  # licence_no 首次出現
reactivated  # 曾 inactive，本次又出現
removed  # 上次有、本次無 -> status=inactive
renamed  # name_en 或 name_zh 變更（normalized 比對）
address_changed  # business_address 變更
```

## Severity 升級規則（規則決定，AI 只寫文案）

| 條件 | severity | 動作 |
|---|---|---|
| `removed` 且該 Licensee 有付費 Provider | `critical` | 立即 email 營運 + **自動下架付費曝光** + Provider 標記 `licence_lapsed` |
| `removed` 且有已認領 Provider | `critical` | email + Provider 頁面顯示警示橫幅 |
| `renamed` / `address_changed` 且已認領 | `warn` | 要求重新驗證 Certification |
| 其他 | `info` | 只進日誌 |

## 疑難排解

| 症狀 | 檢查 |
|---|---|
| SyncRun 一直 `aborted_sanity` | 對比 `raw_file_key` 兩天的檔案；官方可能改了格式或發佈了部分檔 |
| Diff 每天都出現大量 `renamed` | normalize 邏輯有 bug（多半是空格或全形），先修 normalize 再重跑 |
| 重跑產生重複 Change | `compute_diff` 應以「上一次成功 SyncRun 的 snapshot」為基準，不是以當前 DB |
| 下載 403 | 加 User-Agent；不要高頻打；考慮改用 data.gov.hk API endpoint |

## 手動修復

```bash
python manage.py sync_tcsp --dry-run                    # 只印，不寫
python manage.py sync_tcsp --file /path/to/local.csv    # 用本地檔（官方掛掉時）
python manage.py sync_tcsp --force                      # 跳過 sanity check（需 --reason，會記進 SyncRun）
```
`--force` 必須要求使用者明確確認，並在 SyncRun 記錄操作者與理由。
