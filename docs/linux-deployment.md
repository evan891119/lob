# Linux 部署與維運

本文件不包含真實 credential、帳戶資訊、主機名稱或個人路徑。所有路徑皆為可替換的部署範例。

## 1. 主機前置條件

- Linux x86_64 或 arm64。
- Docker Engine 與 Docker Compose plugin。
- 已格式化為 ext4/XFS、由 `/etc/fstab` 以 UUID 掛載的資料磁碟；可使用整個 filesystem，或使用共用 filesystem 裡的專案子目錄。
- 僅需對外連線；不需開 inbound port。

先確認 20TB 磁碟實際掛載到選定位置，再準備目錄。Script 會拒絕普通目錄，以防磁碟未掛載時誤寫系統碟。

若整個 filesystem 專供本專案使用，可以直接以 UUID 掛載：

```fstab
UUID=<filesystem-uuid> /mnt/lob-data ext4 defaults 0 2
```

若 20TB filesystem 是共用的，保留它原本的 UUID mount，再將專案子目錄 bind mount 到固定資料根目錄。兩行都必須存在；第二行的明確 systemd dependency 可避免底層共用磁碟未掛載時，bind mount 誤用系統碟上的空目錄：

```fstab
UUID=<filesystem-uuid> /mnt/shared-data ext4 defaults 0 2
/mnt/shared-data/lob-data /mnt/lob-data none bind,x-systemd.requires-mounts-for=/mnt/shared-data 0 0
```

以上是一般化範例，不要把真實 UUID、主機名稱或個人路徑提交到 repo。`/mnt/shared-data` 應替換成該共用 filesystem 已有的固定 mount point；不要為了本專案把整顆共用硬碟改掛到 `/mnt/lob-data`。

```bash
sudo scripts/host-prepare /mnt/lob-data
sudo scripts/storage-check /mnt/lob-data
sudo scripts/storage-identity-check /mnt/lob-data
```

`storage-identity-check` 是唯讀驗收：確認 exact mount target、ext4/XFS、filesystem UUID 與 `/etc/fstab` 的 `UUID=` entry。直接 mount 時要求 UUID entry 指向資料根目錄；共用 filesystem 的 bind layout 則另外核對底層 UUID mount、專案子目錄來源及 exact `x-systemd.requires-mounts-for=` dependency。輸出只包含 layout、filesystem 類型、總 bytes、service 可用 bytes 與布林結果；不輸出 device path、UUID 值、hostname、底層 mount point 或個人路徑。`fstab_uuid_match=true` 只證明設定存在，仍需在安排好的重新開機後重跑本指令與 `acceptance-check`，才能證明自動掛載及資料服務恢復。

Compose 啟動後可做不需 reboot 的唯讀 boot readiness 檢查：

```bash
sudo scripts/docker-boot-readiness-check \
  /etc/shioaji-lob-recorder/host.env
```

工具會 fail closed 確認 Ubuntu `docker.service` 已設為開機啟動且目前 active、Compose config 有效、ClickHouse 與 collector 正在運行，而且實際 containers 的 restart policy 都是 `unless-stopped`。成功只輸出布林結果，不輸出 host config path、container ID、hostname 或個人路徑；工具不會 enable/restart Docker，也不會建立、停止或重建 container。這是靜態 readiness 證據，不等同真正 reboot 後的自動掛載與服務恢復證據。曾執行 `docker compose down` 會刪除 containers；曾明確 stop 的 container 也不應以本工具冒充可自動恢復。

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
sudo chown 10001:10001 /etc/shioaji-lob-recorder/shioaji.env
sudo chmod 0600 /etc/shioaji-lob-recorder/shioaji.env
```

Compose 將檔案以唯讀 file-backed secret 掛到 `/run/secrets/shioaji_credentials`；此 file source 由 UID/GID `10001:10001` 的 collector 直接讀取，因此 host file 必須精確為 owner `10001:10001`、mode `0600`，不可用 `0644` 或 other-read 補救。Credential 不會出現在 Compose environment。ClickHouse 僅存在 `lob_internal` network 且不發布 port，因此使用該隔離 network 內的 default user；不要把 ClickHouse service 接到其他 network 或新增 `ports:`。

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

Pinned Shioaji 1.5.3 在部分 Linux runtime 使用 uppercase `api.Contracts`，collector 已同時支援這個 legacy facade 與新版 lowercase facade。更新程式碼後必須使用 `up -d --build` 重建 collector image；只有 restart 舊 container 不會套用修正。安全的 collector log 會用 `ShioajiLoginError`、`ContractLookupError`、`NoActiveSubscriptionError` 或逐 stream 的例外類別標示階段，不保存 Shioaji 原始例外訊息。不要為了除錯直接輸出 login 回傳值或 Shioaji native log。

### 啟用大台期貨

`config/instruments.yaml` 提供預設停用的 `TXFR1`（大台連續近月）範例。確認部署帳戶具有期貨行情權限後，將該項目的 `enabled` 改成 `true`；若只要期貨，可同時停用股票項目，再使用 `up -d --build` 重建 collector。FUT/OPT 共用 Shioaji FOP callbacks；collector 會保留設定中的 logical alias，並把 callback 回傳的實際到期合約代碼存入行情 `symbol`，兩者的對應會留在公開的 subscription result。股票、期貨與選擇權寫入相同兩張 event tables，但可用 `security_type`、`exchange` 與 `symbol` 明確區分，不會無法辨識地混在一起。

### 唯讀驗收摘要

Collector 運行時，可用一條指令同時重跑 storage ownership/mount、Compose config，並從 health 與 ClickHouse allowlist 產生技術摘要：

```bash
sudo scripts/acceptance-check /mnt/lob-data /etc/shioaji-lob-recorder/host.env
sudo cat /mnt/lob-data/parquet/acceptance-report.json
```

工具會先確認 `host.env` 指向同一個資料根目錄，並以 metadata-only 方式驗證 `LOB_CREDENTIAL_FILE` 是絕對路徑、非 symlink regular file 且主機 owner/mode 為 `10001:10001`/`0600`；不讀 credential 內容，也不把路徑寫入 report。工具不會停止、重啟或清除服務，也不會讀取 Shioaji 原始 log。Report 不包含 session ID、credential、帳戶、主機名稱或 host path；只包含公開商品、rows、時間範圍、simulation/subscription 狀態、queue/capacity counters 與彙總 gap。`checks` 中的 `health_fresh`、`collector_operational`、`simulation_only`、`subscriptions_active`、`both_streams_present`、`stock_both_streams_present`、`futures_both_streams_present`、`options_both_streams_present`、`current_session_no_drops`、`no_open_gaps` 與 `storage_below_stop_threshold` 用於當下檢查；各商品類型的雙 stream check 只有該類型至少一個實際 symbol 同時存在 BidAsk/Tick rows 才會成立。`latest_completed_session` 另外以內部參數化 UUID 查詢最近一個已結束 session 的兩張 market table rows，但不輸出 UUID；`completed_session_reconciled=true` 代表該 session 是 simulation、實際 rows 等於 received、drop/notice drop 為零且沒有 open gap。開始與結束時間仍需涵蓋部署者指定的完整交易時段，不能只憑此布林值推定時段長度。`pilot_scope_reached` 只有至少三個實際 symbol 及一個交易日才會成立，仍不代表完整交易時段或 3–5 個設定商品已驗收。

這份摘要只能保存執行當下的證據，不能單獨證明重新開機後 mount 持續存在、完整交易時段、刻意斷線/outage recovery 或多日 pilot；這些 gate 必須在相應操作之後重新執行並由部署者確認。

### 受控 ClickHouse outage drill

只在 simulation collector 正於交易時段持續收到行情、並已接受 ClickHouse 暫停寫入的情況執行。以下單一指令會先驗證 storage/Compose/服務狀態及產生 before report，明確停止 ClickHouse 300 秒，再啟動並等待 spool replay；任何 exit、terminal interrupt 或驗證失敗都會透過 trap 再次嘗試啟動 ClickHouse。允許範圍為 30–900 秒，缺少確認 flag 會在變更服務前拒絕執行。

```bash
sudo scripts/database-outage-drill \
  /mnt/lob-data /etc/shioaji-lob-recorder/host.env 300 \
  --confirm-database-outage
sudo cat /mnt/lob-data/parquet/outage-report.json
```

成功報告只包含 requested seconds、counter deltas 與布林 checks，不包含 session ID、credential、帳戶、hostname 或 host path。`outage_recovery_verified=true` 要求 collector 未重啟、前後皆為 simulation、outage 期間確實收到行情並新增 spool、replayed delta 至少等於 spooled delta、沒有新增 drop，且新的 `database_failure` gap 已關閉。若非交易時段沒有新事件，驗收會安全失敗而不是冒充成功；ClickHouse 仍會被拉回。此 drill 不模擬 Shioaji 網路斷線，也不等同 host reboot。

### 受控 Shioaji outbound network drill

新版 collector 在 Shioaji event code `13`（reconnected）後，只由 callback 設定 signal，再由 collector main thread 重新訂閱全部已解析的公開行情 streams；不在 callback thread 執行可能阻塞的 `subscribe()`。只有至少一個 stream 恢復後才記錄 reconnect 並關閉 `connection_down` gap；全部失敗時以 5–60 秒 exponential backoff 重試，不輸出上游原始訊息。

以下指令只中斷 collector 的 Compose `outbound` network 60 秒；ClickHouse 所在的 `lob_internal` network 保持連線。它不停止或重建 collector，因此可驗證相同行程內的 reconnect/resubscribe。缺少確認 flag 或秒數不在 30–300 範圍時會在 network mutation 前拒絕；exit/interrupt trap 會嘗試把 outbound network 接回。

```bash
sudo scripts/network-outage-drill \
  /mnt/lob-data /etc/shioaji-lob-recorder/host.env 60 \
  --confirm-network-outage
sudo cat /mnt/lob-data/parquet/network-outage-report.json
```

`network_outage_verified=true` 要求同一 simulation collector session、reconnect counter 增加、subscriptions 數量恢復且未惡化、恢復後重新收到行情、沒有新增 drop，以及新的 `connection_down` gap 已關閉。若 Shioaji 沒有偵測到斷線、非交易時段沒有新行情或訂閱未恢復，report 會保留失敗 checks；工具不會把單純重新接上 Docker network 冒充 Shioaji 恢復成功。

### Host reboot persistence check

這是刻意分成兩階段的外部驗收；script 不會自行執行 reboot。`prepare` 先重跑 storage identity、acceptance 與 Docker boot readiness checks，將安全的 before report 寫到 Parquet，並把原始 Linux boot identifier 以 `10001:10001`、mode `0600` 暫存在 `private-runtime/reboot-check/`。原始值不會輸出或進入 report，且可由 `privacy-purge --runtime` 清除；重複 prepare 會拒絕覆寫既有 baseline。

```bash
sudo scripts/host-reboot-check prepare \
  /mnt/lob-data /etc/shioaji-lob-recorder/host.env \
  --confirm-reboot-preparation

# reboot 是獨立的管理者操作；確認沒有其他工作後才執行
sudo reboot

# 主機回來、Docker services 恢復後
cd /path/to/repository
sudo scripts/host-reboot-check verify \
  /mnt/lob-data /etc/shioaji-lob-recorder/host.env \
  --confirm-after-reboot
sudo cat /mnt/lob-data/parquet/reboot-report.json
```

`verify` 會再次要求 filesystem UUID/`fstab` match、mount/layout、credential metadata、Compose config、ClickHouse 與 collector 可用，並確認 boot identifier 已改變；最後只輸出 row deltas 與布林 checks。`reboot_persistence_verified=true` 另要求 collector session 已重建、前後皆為 simulation、歷史最早 event 未改變、LOB/Tick rows 未減少、subscriptions/health/storage 正常且沒有 open gap。成功後暫存 boot identifier 會立即刪除；若未 reboot 或任何 persistence check 失敗，狀態會保留以便真正 reboot 後重試，不會冒充成功。

## 4. 查詢、匯出與品質

```bash
docker compose exec -T clickhouse clickhouse-client \
  --param_security_type=STK --param_exchange=TSE \
  --param_symbol=2330 --param_trading_date=2026-01-02 \
  --param_start='2026-01-02 09:00:00' --param_end='2026-01-02 13:30:00' \
  < queries/events_by_symbol.sql

docker compose exec -T collector lob-recorder export \
  --host clickhouse --security-type STK --exchange TSE \
  --symbol 2330 --date 2026-01-02 \
  --output /var/lib/lob/parquet

# 每日匯出當天所有已有資料的公開商品
docker compose exec -T collector lob-recorder export \
  --host clickhouse --all-symbols --date 2026-01-02 \
  --output /var/lib/lob/parquet

docker compose exec -T collector lob-recorder quality \
  --parquet '/var/lib/lob/parquet/security_type=STK/exchange=TSE/symbol=2330/trading_date=2026-01-02/*.parquet'

# 只有當 pattern 包含該日期所有商品與兩種 stream，才可宣告 sequence scope 完整
docker compose exec -T collector lob-recorder quality \
  --parquet '/var/lib/lob/parquet/security_type=*/exchange=*/symbol=*/trading_date=2026-01-02/*.parquet' \
  --complete-sequence-scope

docker compose exec -T collector lob-recorder pilot-report \
  --host clickhouse \
  --start-date 2026-01-02 --end-date 2026-01-06 \
  --output /var/lib/lob/parquet/pilot-report.json
```

`sequence_no` 是 collector session 內跨商品、跨 BidAsk/Tick 共用的流水號。單一商品、單一 stream 或其他部分匯出不能判斷缺號；quality report 會 fail closed 輸出 `sequence_scope_complete=false` 與 `sequence_gaps=null`。只有 pattern 確實包含所選 session interval 的所有行情事件時，才可加 `--complete-sequence-scope`；錯把部分資料宣告完整會製造假 sequence gap。Duplicate identity 使用 `session_id + sequence_no`，相同 sequence 即使落在不同 stream 也會被偵測。

ClickHouse 會分開寫入 `lob_events` 與 `tick_events`。若前一張表已成功而後一張表失敗，collector 只把尚未確認成功的 stream 放入 market spool；replay 另以 `session_id + sequence_no` 查詢目標 table，跳過可能已由不確定 insert 提交的事件，只補缺少資料。這同時涵蓋連線在 server commit 後才中斷，以及 replay 寫入後、spool 檔案刪除前程序中止的情況。升級不會自動刪除舊 session 已存在的 duplicate；既有資料仍應先以 quality report 確認，再另行安排備份與受控清理。

新版 Parquet 路徑固定使用 `security_type/exchange/symbol/trading_date` 四層 Hive partitions；`--all-symbols` 也按這個完整公開 market identity 分開匯出。單商品匯出建議同時傳入 `--security-type` 與 `--exchange`；若只給 `--symbol`，工具會先查出該日期所有同代碼 identities，再分別輸出，不會合併到同一檔案。升級前已存在的舊 `symbol/trading_date` exports 不會被自動刪除或改寫；需要新 layout 時從 ClickHouse 重新匯出，並以新版 DuckDB glob 查詢，避免同時讀取新舊副本造成重複。

正式 pilot 必須同時指定 inclusive `--start-date` 與 `--end-date`，讓 market、peak、session 與 gap 只包含該次 pilot 範圍，避免把先前 fixture 或試跑資料混入。只給其中一個日期、日期倒置或非正容量 override 都會 fail closed。完全不指定日期仍可產生全資料庫診斷，但不可作為 scoped pilot 最終容量證據。

未指定 `--storage-total-bytes` 時，report 直接讀取 `LOB_STORAGE_ROOT` 所在 filesystem 對 service 可用的 bytes（排除 filesystem reserved free blocks）；參數只保留給受控測試或明確 override。Report 會分開輸出 ClickHouse `bytes_on_disk`、`compressed_data_bytes`、`uncompressed_data_bytes` 與 `compression_ratio`；ratio 定義為 `uncompressed / compressed`，例如 `4.0` 代表資料壓縮為原始大小的約四分之一。未使用日期 filter 時，這些數字是所有 active parts 的 exact totals；使用日期 filter 時，因目前 partition granularity 是月，scope bytes 會依各 table 的 scoped rows / active-part rows 等比例估算，並明確標為 `estimated_by_table_row_share_of_active_parts`。`global_active_parts` 仍保存 exact totals 供核對，retention/projection 使用 scoped estimate；不得把估算標成實際逐日磁碟 bytes。

`pilot_scope` 會按 `security_type + exchange + symbol` 回報觀測商品數與交易日數，並分開判斷至少 3 商品＋1 日的最低 dataset scope，以及至少 3 商品＋5 日的建議 scope。這些 checks 只描述資料庫中可觀測的範圍，不能證明其中任何一天涵蓋完整交易時段；完整時段仍由部署者依 collector start/end 與市場時段確認。Pilot 應設定 3–5 個不同活躍度商品，至少收集一個完整交易日，建議五日。

`capacity_projections` 會以觀測到的每商品／交易日磁碟量與 aggregate EPS，等比例推估 10、50、100 商品的每日、20 個交易日、250 個交易日用量，以及一份只包含新行情資料的 full-copy backup 大小。若提供實際 filesystem usable bytes，也會分別估算各商品數量在 90% 水位可保留的交易日數。`estimated_conservative_peak_sum_events_per_second` 是把各 stream 個別尖峰相加的保守上界，不是同一秒實測 aggregate peak；projection 不包含 replication、版本歷史、增量鏈、加密或檔案系統額外開銷。`minimum_dataset_scope_reached=false` 時仍會輸出數學估算供診斷，但不得拿來做最終容量決策。

每個 capture session 另保存 collector process 自該 session 開始的累積 CPU seconds 與 process lifetime max RSS bytes；`pilot-report` 依 session duration 算出 `average_process_cpu_percent`。CPU percent 是整個 process 的平均值，可能因多核心工作超過 100%；max RSS 是高水位而非當下記憶體。這些數字只涵蓋 collector container，不包含 ClickHouse container，因此最終 pilot 還要另記錄 ClickHouse 的 CPU／memory 使用量後才能回答整套主機資源需求。

把量測填入 `reports/pilot-template.md` 後才能決定 retention 與 20TB 可保存年限；空資料集的 ratio、projection values 與 retention days 都會是 `null`，不產生虛構估算。

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
