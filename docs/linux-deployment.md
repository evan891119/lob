# Linux 部署與維運

本文件不包含真實 credential、帳戶資訊、主機名稱或個人路徑。所有路徑皆為可替換的部署範例。

## 1. 主機前置條件

- Linux x86_64 或 arm64。
- Docker Engine 與 Docker Compose plugin。
- 已格式化為 ext4/XFS、由 `/etc/fstab` 以 UUID 掛載的資料磁碟。
- 僅需對外連線；不需開 inbound port。

先確認 20TB 磁碟實際掛載到選定位置，再準備目錄。Script 會拒絕普通目錄，以防磁碟未掛載時誤寫系統碟。

```bash
findmnt /mnt/lob-data
sudo scripts/host-prepare /mnt/lob-data
sudo scripts/storage-check /mnt/lob-data
```

`host-prepare` 會建立 `.lob-storage-root` marker、ClickHouse 目錄及 UID `10001` 擁有的 `parquet/`、`spool/`、`backup/`、`private-runtime/`。資料根目錄本身由 root 擁有；collector 只讀掛載根目錄，再將 `parquet/`、`spool/`、`private-runtime/` 疊加為可寫 bind mounts，`backup/` 保留給 host 維護流程，不掛成 collector 可寫路徑。因此 collector 不能改名或刪除 ClickHouse/backup 目錄。ClickHouse 目錄依 pinned image UID/GID `101:101` 設定；升級 image 前需重新確認 UID/GID。

## 2. Repo 外設定與 credential

將 `deploy/host.env.example` 複製到 `/etc/shioaji-lob-recorder/host.env`，至少確認：

```dotenv
LOB_DATA_ROOT=/mnt/lob-data
LOB_CREDENTIAL_FILE=/etc/shioaji-lob-recorder/shioaji.env
LOB_MODE=live
```

Credential file 只能有下列兩個 key；請在主機上用限制權限的編輯器填入，不要把值放進 shell history：

```dotenv
SJ_API_KEY=<set-on-host>
SJ_SEC_KEY=<set-on-host>
```

```bash
sudo chown <service-user>:<service-group> /etc/shioaji-lob-recorder/shioaji.env
sudo chmod 0600 /etc/shioaji-lob-recorder/shioaji.env
```

Compose 將檔案以唯讀 file-backed secret 掛到 `/run/secrets/shioaji_credentials`；container 內可能顯示 Compose 的唯讀 `0444` mode，但主機原檔仍必須是 `0600`，且 credential 不會出現在 Compose environment。ClickHouse 僅存在 `lob_internal` network 且不發布 port，因此使用該隔離 network 內的 default user；不要把 ClickHouse service 接到其他 network 或新增 `ports:`。

## 3. Fixture 與 live 啟動

第一次在 Linux 上先跑無 credential fixture：

```bash
LOB_DATA_ROOT=/mnt/lob-data \
LOB_MODE=fixture \
LOB_ALLOW_TEST_STORAGE=true \
LOB_CREDENTIAL_FILE=./secrets/shioaji.env.example \
docker compose up -d --build
```

確認 `lob.lob_events` 與 `lob.tick_events` 有 fixture rows 後停止。Fixture override 不能用於 live mode。

若要在測試環境持續產生事件以演練 database outage，可額外設定
`LOB_FIXTURE_REPEAT_SECONDS=0.25`；這是明確的 test-only 開關，正式 live 設定必須移除。

```bash
docker compose exec -T clickhouse clickhouse-client --query \
  "SELECT table, total_rows FROM system.tables WHERE database='lob'"
docker compose down
```

正式 simulation market-data collector：

```bash
sudo scripts/storage-check /mnt/lob-data
docker compose --env-file /etc/shioaji-lob-recorder/host.env config --quiet
docker compose --env-file /etc/shioaji-lob-recorder/host.env up -d --build
docker compose --env-file /etc/shioaji-lob-recorder/host.env ps
```

Collector 固定使用 Shioaji simulation login，沒有 production order code。Live 驗證需要真實行情權限與交易時段；不得把 login 回傳的 accounts 印出。

## 4. 查詢、匯出與品質

```bash
docker compose exec -T clickhouse clickhouse-client \
  --param_symbol=2330 --param_trading_date=2026-01-02 \
  --param_start='2026-01-02 09:00:00' --param_end='2026-01-02 13:30:00' \
  < queries/events_by_symbol.sql

docker compose exec -T collector lob-recorder export \
  --host clickhouse --symbol 2330 --date 2026-01-02 \
  --output /var/lib/lob/parquet

# 每日匯出當天所有已有資料的公開商品
docker compose exec -T collector lob-recorder export \
  --host clickhouse --all-symbols --date 2026-01-02 \
  --output /var/lib/lob/parquet

docker compose exec -T collector lob-recorder quality \
  --parquet '/var/lib/lob/parquet/symbol=2330/trading_date=2026-01-02/*.parquet'

docker compose exec -T collector lob-recorder pilot-report \
  --host clickhouse --output /var/lib/lob/parquet/pilot-report.json
```

未指定 `--storage-total-bytes` 時，report 直接讀取 `LOB_STORAGE_ROOT` 所在 filesystem 對 service 可用的 bytes（排除 filesystem reserved free blocks）；參數只保留給受控測試或明確 override。Pilot 至少需 3–5 個不同活躍度商品與一個完整交易日；建議五日。把量測填入 `reports/pilot-template.md` 後才能決定 retention 與 20TB 可保存年限。

## 5. 隱私盤點與清除

```bash
scripts/privacy-list /mnt/lob-data

LOB_DATA_ROOT=/mnt/lob-data scripts/privacy-purge --runtime --dry-run
LOB_DATA_ROOT=/mnt/lob-data scripts/privacy-purge --runtime
LOB_DATA_ROOT=/mnt/lob-data scripts/privacy-purge --spool
LOB_DATA_ROOT=/mnt/lob-data scripts/privacy-purge --database-metadata
LOB_DATA_ROOT=/mnt/lob-data scripts/privacy-purge --all-private
```

管理 script 透過 collector image 執行 inventory/runtime/spool 操作，主機不需要另外安裝 Python dependencies，也不把真實 credential 掛入 management container。`privacy-list` 只列相對檔名、大小、mtime 與命中數，不顯示命中文字，並檢查 market spool 的完整 allowlist schema；`sensitive_hits=-1` 表示檔案超過安全掃描上限或無法讀取，不代表 0 hits。`--runtime` 會先停止 collector，再清除集中在 `private-runtime/` 的 Shioaji home/contracts/log、collector log/health、audit spool、tmp 與 crash artifacts，並保留 bind mount root 的 UID/mode。`--spool` 是獨立且有資料遺失警告的操作，要求 ClickHouse 正在執行，清除後會留下不含私人內容的 `manual_spool_purge` gap。`--all-private` 不刪 market spool 或 `lob_events`/`tick_events`。若 credential 也要刪除，需另外設定 `LOB_CREDENTIAL_FILE` 或使用預設 `/etc/shioaji-lob-recorder/shioaji.env`；host credential 會在其他 Compose 清理完成後才最後 unlink。

Shioaji 自身 log 視為不可信 private artifact，預設超過 20 MB 會由 collector 截斷；可用 repo 外 `host.env` 的 `LOB_SHIOAJI_LOG_MAX_BYTES` 調整。若要確定完全移除，停止 collector 後使用 `privacy-purge --runtime`，不要查看或複製 log 內容。

## 6. 搬移資料根目錄

1. `docker compose down`，確認 collector 已 graceful stop。
2. 以能保留 owner、mode、timestamp、ACL/xattr 與 sparse file 的工具複製。
3. 在新 filesystem 建立/確認 `.lob-storage-root`，執行 `storage-check`。
4. 修改 repo 外 `host.env` 的 `LOB_DATA_ROOT`。
5. 啟動後比較 ClickHouse table row counts、Parquet files 與 health。

Credential、`private-runtime/` 預設不隨市場資料備份搬移。若外部 snapshot/backup 已包含它們，privacy purge 不會自動刪除外部副本。

若管理者不用 script、而是直接刪除整個 host `private-runtime/` 或 `spool/` 根目錄，重新啟動前必須再執行 `sudo scripts/host-prepare /mnt/lob-data` 與 `sudo scripts/storage-check /mnt/lob-data`；entrypoint 只會重建 mount root 內的子目錄，不負責修復 host owner/mode。
