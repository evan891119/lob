# Shioaji LOB Recorder Linux / Docker 專案規劃

## 1. 專案目標

建立一個可長時間運行的 Shioaji 即時行情收集器，讓使用者透過設定檔決定要收集哪些商品，並將資料可靠地寫入適合時間序列分析的資料庫。

正式部署目標是 Linux 主機。Collector、ClickHouse 與相關維護工作都由 Docker Compose 管理，主機只需要 Docker Engine、Docker Compose plugin、可選擇掛載位置的持久化磁碟與對 Shioaji 後端的對外網路。專案必須能透過 Git 搬到另一台 Linux 電腦，在設定資料根目錄與建立主機端憑證檔後以一致的 Compose 指令啟動。

此專案預設可能運行在多人可使用的共用電腦，因此 tracked source、設定、log、database、匯出檔與錯誤訊息都不得包含姓名、身分證字號、券商帳號、API credential、主機名稱、個人絕對路徑或私人註記。

第一版收集兩種資料：

- `BidAsk`：五檔委買、五檔委賣及各檔數量變化。
- `Tick`：逐筆成交及成交當下的最佳買賣價量。

Shioaji 的 `BidAsk` 是五檔聚合資料，不是包含 order ID 與排隊順序的完整 market-by-order。歷史 API 也不能回補過去的完整五檔，因此本系統必須從啟動日起自行持續累積。

## 2. MVP 範圍

### 包含

- 使用設定檔選擇股票、期貨或選擇權商品。
- 每個商品可分別啟用 `BidAsk`、`Tick` 與盤中零股行情。
- 提供 Linux 用 `Dockerfile` 與 `compose.yaml`，以單一 collector replica 搭配 ClickHouse 運行。
- 使用 repo 外 `host.env` 的 `LOB_DATA_ROOT` 選擇 ClickHouse、Parquet、spool、backup 與 private runtime 的實際儲存磁碟。
- API Key 與 Secret Key 只由一個 repo 外的主機憑證檔提供，並以唯讀 Docker secret 掛入 collector。
- 提供不需要真實 credential 的 fixture/replay 測試路徑。
- 啟動時驗證商品代碼並列出實際訂閱結果。
- 將行情 callback 快速放入記憶體 queue，不在 callback 裡直接執行 database I/O。
- 背景 worker 批次寫入 ClickHouse。
- 記錄交易所時間、程式接收時間、session 與本機 sequence，供延遲及缺口分析。
- 自動重連、錯誤紀錄、健康狀態與 database 暫時不可用時的本機 spool。
- 提供基本查詢與 Parquet 匯出。

### 第一版不包含

- 自動下單或任何交易功能。
- 回補歷史五檔 LOB。
- 五檔以外的完整市場深度。
- 個別委託的 order ID、排隊位置或 market-by-order 重建。
- Web dashboard 與遠端管理介面。
- 對外開放 ClickHouse、health endpoint 或其他 inbound service。
- 將 CA、下單權限或任何 production order 功能加入 collector image。

## 3. Database 選型

### 決策：ClickHouse

主要行情資料使用 ClickHouse。LOB 是高頻、append-only、以商品和時間範圍為主的分析型資料，ClickHouse 的 columnar storage、壓縮、批次寫入與 `MergeTree` 排序方式符合這個 workload。ClickHouse 由 Compose 啟動，資料以 configurable host bind mount 放在使用者指定的 `LOB_DATA_ROOT`，不使用會默認落到 Docker data-root 的 named volume；服務只存在於 Docker internal network，不發布到共用主機的外部網路介面。

| 選項 | 適合度 | 結論 |
| --- | --- | --- |
| ClickHouse | 高寫入量、長期保存、時間區間與聚合分析 | **MVP 採用** |
| TimescaleDB | 熟悉 PostgreSQL、資料量中等、需要較多 relational 操作 | 可作替代方案，但不是首選 |
| DuckDB + Parquet | 單機研究、離線分析、模型訓練資料集 | 作為匯出與備份層 |
| SQLite | 設定與少量 metadata | 不存主要 LOB event |

ClickHouse 不應每收到一筆 callback 就 insert 一次。collector 應按「筆數或時間」flush，例如累積 1,000 筆或 250 ms 後批次寫入；實際參數要由 pilot 測量決定。

## 4. 商品選擇方式

第一版使用版本可追蹤、容易審查的 `config/instruments.yaml`，不先做 UI。

```yaml
instruments:
  - code: "2330"
    security_type: "STK"
    exchange: "TSE"
    enabled: true
    streams: ["bidask", "tick"]
    intraday_odd: false

  - code: "TXFR1"
    security_type: "FUT"
    exchange: "TAIFEX"
    enabled: false
    streams: ["bidask", "tick"]
```

啟動流程：

1. 載入設定並拒絕重複或不完整的商品項目。
2. 透過 Shioaji contract lookup 驗證每個商品。
3. 先註冊 callback，再訂閱已啟用的 stream。
4. 輸出成功、失敗與實際解析到的 contract。
5. 只有驗證和訂閱都成功的商品才標記為 active。

第一版修改設定後重新啟動 collector 生效；hot reload 留到後續版本，避免訂閱狀態難以追蹤。

## 5. 系統架構

```text
Linux host
|
+-- credential file (repo 外、0600、只由部署帳號讀取)
|
+-- host config: LOB_DATA_ROOT=/mnt/lob-data (非機密)
|
+-- Docker Compose
    |
    +-- collector (單一 replica、non-root、outbound only)
    |      |
    |      +--> Shioaji backend: login / contract / BidAsk / Tick
    |      +--> bounded queue --> batch writer
    |      +--> ${LOB_DATA_ROOT}/spool bind mount
    |      +--> privacy-safe health / metrics / logs
    |
    +-- ClickHouse (internal network only)
           |
           +--> ${LOB_DATA_ROOT}/clickhouse bind mount
           +--> SQL queries / daily Parquet export
```

Collector 必須運行在長時間存在的 process；MVP 的正式執行方式是 Linux 上的 Docker container，不使用只有 request 生命週期的 serverless function。Collector 保持單一 replica，避免同一組 Shioaji credential 因多個 container 同時登入而浪費連線額度或產生重複資料。

## 6. Linux、Docker 與隱私邊界

### Docker 執行方式

- `compose.yaml` 管理 `collector` 與 `clickhouse`，並明確設定 healthcheck、restart policy 與 resource limits；collector 停用 Docker daemon log persistence，ClickHouse 則使用有限大小與保留數量的 log rotation。
- Collector image 由 pinned Python dependencies 建置；在目標 Linux 主機使用 host config 執行 `docker compose --env-file /etc/shioaji-lob-recorder/host.env up -d --build`，讓 image architecture 與主機一致。MVP 不先依賴公開 container registry。
- Collector 以 non-root user 執行，啟用 `no-new-privileges`、移除不需要的 Linux capabilities，root filesystem 儘量設為 read-only。
- 只有 `LOB_DATA_ROOT` 下的 ClickHouse、spool、Shioaji cache、匯出、備份與 private runtime 路徑可持久寫入；其他必要暫存使用 tmpfs。
- ClickHouse 不發布 public port；如需主機端維護，只允許 loopback 綁定或使用 `docker compose exec`。
- Collector 不提供 inbound API；網路需求只有下載 image/dependency，以及連往 Shioaji 的 outbound connection。

### 可選擇的資料儲存根目錄

- Linux 主機使用非機密設定檔 `/etc/shioaji-lob-recorder/host.env` 指定 `LOB_DATA_ROOT`。預設範例是 `LOB_DATA_ROOT=/mnt/lob-data`；目前預期使用約 20TB 的磁碟，但容量與路徑不得硬編碼在 source 或 Compose。
- Repo 提供 `deploy/host.env.example`，只含一般化 placeholder；實際 `host.env` 放在 repo 外，不包含 credential、帳戶資訊或私人註記。
- Compose 將資料根目錄唯讀掛入 collector，再只把 `parquet/`、`spool/`、`private-runtime/` 疊加為 UID 10001 可寫的 host bind mounts；ClickHouse 使用自己的 `clickhouse/` bind mount，collector 不取得其父目錄寫權。資料位置仍可由 host 直接檢視、備份與搬移：

```text
${LOB_DATA_ROOT}/
|-- clickhouse/       # ClickHouse 主要資料
|-- parquet/          # Parquet 匯出
|-- spool/            # 尚未入庫的行情事件
|-- backup/           # 受控的市場資料備份
`-- private-runtime/  # Shioaji state/log 與其他可清除資料
```

- Credential 不放在 `LOB_DATA_ROOT`；它仍只存在 `/etc/shioaji-lob-recorder/shioaji.env`。
- 正式 Linux 主機建議使用 ext4 或 XFS，並以 UUID 寫入 `/etc/fstab`，讓 20TB 磁碟在重新開機後維持相同 mount point。實際掛載與 `/etc/fstab` 修改由主機管理者完成，不由 collector 自動執行。
- Live collector 啟動前必須 fail closed 驗證：`LOB_DATA_ROOT` 是 absolute path、不是 `/`、存在內容完全相符的 `.lob-storage-root` marker、root 可由 service UID/GID 讀取、三個 collector 子目錄可寫，而且 `findmnt`/`mountpoint` 證明它是預期的獨立 mount。若磁碟未掛載，不得退回系統碟上的同名普通目錄。
- Fixture/unit test 可透過明確的 test-only override 使用 temporary directory；此 override 在 live Shioaji mode 必須被拒絕。
- 監控整個 `LOB_DATA_ROOT` 的 bytes 與 inode 使用率。預設 80% 發出 privacy-safe warning，90% 停止新資料接收、graceful flush 並記錄 disk-capacity gap；門檻可配置但不可完全停用。
- 變更 `LOB_DATA_ROOT` 或搬移資料時，必須先 graceful stop Compose，再使用保留 owner、mode、timestamp 與 sparse file 的 filesystem copy/restore，驗證 row counts 與 ClickHouse health 後才從新位置啟動。

### 單一憑證檔

- 真實 credential 只放在 Linux 主機的 `/etc/shioaji-lob-recorder/shioaji.env`。此路徑是一般化部署路徑，不得改成包含使用者名稱的 tracked 路徑。
- 檔案只允許兩個鍵：`SJ_API_KEY` 與 `SJ_SEC_KEY`。MVP 不需要 CA，因為專案不下單。
- 檔案 owner 為專用部署帳號、mode 為 `0600`，不得放進 repo、Docker image、Compose environment、command line、log、database、spool 或 Parquet。
- Compose 以 file-backed secret 將它唯讀掛載到 `/run/secrets/shioaji_credentials`；collector 啟動時解析檔案並直接傳給 `api.login()`，不得把值回寫到其他檔案或輸出。
- Repo 只提供 `secrets/shioaji.env.example`，內容是固定 placeholder，不含真實值、帳號或個人路徑。
- Docker Compose 的 file-backed secret 主要降低誤提交與一般使用者誤讀風險，不是硬體或加密 secret vault；共用主機的 root、Docker daemon 管理者或有等同權限者仍可讀取。若這些管理者不可信，該主機不符合部署條件。

### 隱私與資料最小化

- 不記錄 `api.login()` 回傳的 account object，也不保存 `person_id`、`account_id`、`broker_id`、`username`、credential、token 或 HTTP authorization header。
- `capture_sessions` 只保存隨機 `session_id`、simulation mode、商品、counter、時間與技術狀態；不得保存可識別帳戶的欄位。
- Error handling 必須先做 redaction，再寫入 log 或 `capture_gaps`；未知的上游錯誤只保留安全錯誤類別與內部 correlation ID。
- 所有 log 使用結構化 allowlist schema，禁止 dump 任意 Python object、environment、request header 或完整 exception context。
- Collector 的 file log 設定保存上限，ClickHouse 的 Docker log 設定 rotation；匯出的 dataset 預設只含公開市場資料與技術 metadata。
- 文件、source comment、測試 fixture、commit message 範例與設定模板使用中性內容，不放私人訊息、主機細節或真實帳戶資料。

### 可檢視與手動刪除的 private runtime 區

所有無法完全控制內容、可能由 Shioaji 或 runtime 產生的檔案，都集中在 Linux 主機的 `${LOB_DATA_ROOT}/private-runtime/`。這個目錄由專用服務帳號持有、mode 為 `0700`，使用 bind mount 掛入 collector，不使用不易直接檢視的 anonymous volume。預定結構如下：

```text
${LOB_DATA_ROOT}/private-runtime/
|-- shioaji/
|   |-- home/          # token pool、session state、其他 Shioaji state
|   |-- contracts/     # contract cache
|   `-- shioaji.log    # Shioaji 自己產生的 log
|-- collector/
|   |-- collector.log  # 經 allowlist/redaction 的應用程式 log
|   |-- health.json    # privacy-safe health/capacity gauges
|   `-- audit-spool/   # 尚未寫入 session/gap tables 的 allowlist metadata
|-- crash/             # 若未來啟用診斷檔，只能寫在這裡
`-- tmp/               # 可重建暫存檔
```

- Container entrypoint 必須在 `import shioaji` 前設定 `SJ_HOME_PATH`、`SJ_CONTRACTS_PATH` 與 `SJ_LOG_PATH`，分別指向上述目錄，避免 Shioaji 寫入 container home、`~/.shioaji` 或其他難以追蹤的位置。
- Collector container 的 Docker logging driver 設為 `none`，避免 stdout/stderr 另被複製到 Docker daemon 的 container log。需要保留的 operational log 由 collector 寫入 `private-runtime/collector/`。
- 不把 credential 當作 Docker build arg 或 environment；image layer、build cache、`docker inspect` 與 shell history 不得出現 secret 值。
- Container 關閉 core dump，`/tmp` 使用 tmpfs；任何明確啟用的診斷輸出只能寫入 `private-runtime/crash/`。
- ClickHouse 行情 tables、spool 與 private runtime 分開。Spool 只接受正規化市場事件；`capture_sessions`、`capture_gaps` 只接受 allowlist 欄位與已清理的錯誤分類，不保存原始上游訊息。

專案要提供兩個不含個資的管理工具：

- `scripts/privacy-list`：列出 private runtime 各類別的檔名、大小、mtime 與檔案數量，並檢查 spool 與 database metadata 的 allowlist；敏感模式掃描只回報檔名或資料區與命中數，不印出命中文字。
- `scripts/privacy-purge`：互動式停止 collector、顯示即將刪除的範圍並要求確認，再清除選定資料。至少支援 `--runtime`、`--credentials`、`--database-metadata`、`--spool` 與 `--all-private`；預設不刪 ClickHouse 行情 tables 或 spool。

停止 collector 後，管理者仍可直接刪除 host `private-runtime` 整個目錄，但重新啟動前必須再跑 `host-prepare`/`storage-check` 以正確 UID/mode 重建 mount root。管理 script 平時只清內容並保留 bind root，是先做盤點、保護行情資料並降低誤刪風險，不把它變成唯一清理方式。

刪除規則：

- `--runtime` 清除 Shioaji log、token pool、cache、collector log、tmp 與 crash，不清 spool。
- `--credentials` 只刪除 `/etc/shioaji-lob-recorder/shioaji.env`，刪除後 collector 不得自動重啟登入。
- `--database-metadata` 清除 `capture_sessions` 與 `capture_gaps`，不碰 `lob_events`、`tick_events`。
- `--spool` 明確清除 `${LOB_DATA_ROOT}/spool`；執行前必須再次警告會造成尚未入庫資料遺失並留下 gap。
- `--all-private` 組合清除 runtime、credential 與 database metadata，但仍不得暗中刪除 spool 或市場資料；刪除所有市場資料必須是另一個明確命名、再次確認的操作。
- Purge 前必須 graceful shutdown，避免 token file、log 或 spool 正在寫入；purge 後重新建立空目錄與最小權限，並輸出不含路徑個資的完成摘要。
- 若曾發現未清理的個資進入 ClickHouse，單純 `DELETE`/`TRUNCATE` 不視為可靠的立即清除；應停止服務、備份需要保留的公開市場資料、移除整個 ClickHouse volume，再重建並重新匯入乾淨資料。
- `private-runtime` 與主機憑證檔預設排除於一般備份、snapshot 與同步工具之外。若主機管理者另外備份了這些路徑，privacy purge 無法刪除那些外部副本，部署者必須在相同清理流程中另外處理備份。

### 跨機搬移

- Git repository 保存 source、Docker 定義、migration、public config、測試與文件；credential、runtime data 與 machine-specific state 不隨 Git 搬移。
- 新 Linux 主機先安裝 Docker Engine 與 Compose plugin，再 clone repository、掛載資料磁碟、建立 repo 外 `host.env`、建立主機憑證檔、設定權限，最後 build 並啟動 Compose。
- ClickHouse data、spool 與 Parquet 需要另外使用受控的 filesystem backup/restore 搬移；不可 commit 到 Git。
- Shioaji 仍依賴外部帳戶、API Key/Secret、行情權限、網路與交易時段。Docker 只讓部署可攜，不會讓即時行情變成完全離線。

## 7. 資料模型

### `lob_events`

每列代表一次收到的五檔狀態更新。五檔固定展開為欄位，方便 SQL、特徵工程及 Parquet 使用。

核心欄位：

- `trading_date`
- `exchange`
- `security_type`
- `symbol`
- `event_ts`：Shioaji/交易所行情時間，使用 `Asia/Taipei` 語意，不自行加八小時。
- `received_ts`：collector 收到事件的本機時間。
- `session_id`
- `sequence_no`
- `bid_price_1` ... `bid_price_5`
- `bid_volume_1` ... `bid_volume_5`
- `ask_price_1` ... `ask_price_5`
- `ask_volume_1` ... `ask_volume_5`
- `diff_bid_vol_1` ... `diff_bid_vol_5`
- `diff_ask_vol_1` ... `diff_ask_vol_5`
- `simtrade`
- `intraday_odd`
- `ingested_at`

建議使用 `MergeTree`，以月份 partition，並以 `(symbol, trading_date, event_ts, sequence_no)` 作為 `ORDER BY` 起點。不要依每個商品建立 partition 或獨立 table。

### `tick_events`

核心欄位：

- `trading_date`
- `exchange`
- `security_type`
- `symbol`
- `event_ts`
- `received_ts`
- `session_id`
- `sequence_no`
- `close`
- `volume`
- `total_volume`
- `tick_type`
- `best_bid_price`
- `best_bid_volume`
- `best_ask_price`
- `best_ask_volume`
- `simtrade`
- `intraday_odd`
- `ingested_at`

### `capture_sessions`

記錄每次 collector 啟停、登入模式、啟用商品、訂閱結果、重連次數、收到及寫入筆數、丟棄筆數與結束原因。這張表用於判斷某一天的資料是否完整。

### `capture_gaps`

記錄斷線、queue overflow、database failure、spool replay 與手動停止區間。沒有這張表就不能把「有檔案」誤認為「資料完整」。

## 8. 可靠性原則

- callback 只做輕量轉換與 enqueue。
- queue 必須有容量上限並監控使用率；不得無限制吃記憶體。
- database 寫入採 batch，失敗時保留本機 durable spool，再按原始順序 replay。
- event 採 append-only；原始事件不做盤中 update/delete。
- 每個 process 啟動建立新的 `session_id`，sequence 在該 session 內單調增加。
- 偵測系統時鐘偏移，所有本機時間要明確保存 timezone。
- graceful shutdown 時停止新訂閱、flush queue、寫入 session 結果後才離開。
- credential 只存在第 6 節定義的 repo 外單一憑證檔，不可注入 Compose environment，也不可寫入 repo、image、log、database 或匯出資料。
- 預設先使用 Shioaji simulation 驗證流程；正式環境切換必須是明確設定。

Collector 必須先以 fixture 完成無 credential 測試，再由部署者在 Linux 主機上進行 live Shioaji 驗證。

## 9. 容量規劃

目前不先猜每個商品每天會產生多少 LOB event，因為活躍度、商品類型與交易時段差異很大。第一個 pilot 應選 3 至 5 個不同活躍度商品連續收集完整交易日，量測：

- 每秒平均與尖峰 events。
- 每個商品每天的 `lob_events` / `tick_events` rows。
- ClickHouse 實際 compressed bytes。
- queue 尖峰使用率。
- batch insert latency。
- callback event time 到 `received_ts` 的延遲分布。
- 是否存在斷線、drop 或 spool replay。

再用實測結果推估 10、50、100 個商品的日/月/年容量，決定 retention、磁碟與備份策略。

## 10. 實作階段

### Phase 0：Git、Linux container 與隱私基線

- 初始化 Git repository，預設 branch 使用 `main`，先不設定 GitHub remote。
- 建立 Python project、dependency lock、multi-stage `Dockerfile`、`compose.yaml` 與 container entrypoint。
- 建立 ClickHouse service、internal network、configurable host bind mounts、healthcheck、restart policy 與 log rotation。
- 建立 `deploy/host.env.example`、`LOB_DATA_ROOT` 目錄配置、storage marker、mount-point fail-closed 驗證與 test-only temporary override。
- 建立 `secrets/shioaji.env.example`、repo 外 secret mount 與 credential parser；不得建立含真實值的檔案。
- 建立 `.gitignore`，排除 credential、`.env`、`*.pfx`、token/cache、database、spool、export、log、coverage 與 local editor state。
- 建立 privacy-safe logging/redaction 與禁止 account object 落盤的測試。
- 將 `SJ_HOME_PATH`、`SJ_CONTRACTS_PATH`、`SJ_LOG_PATH` 和所有 runtime-writable path 導向 `private-runtime` bind mount，並停用 collector 的 Docker daemon log persistence。
- 建立 `scripts/privacy-list` 與具有互動確認、dry-run、範圍隔離的 `scripts/privacy-purge`。
- 建立 fixture/replay source，使 unit/integration test 不需要 Shioaji credential 或交易時段。

完成條件：Linux container 可在無 credential 狀態完成 build、unit test 與 fixture integration test；`docker compose config` 不含 secret 值；Git tracked files 通過 secret/個資掃描；啟動後不會在 `LOB_DATA_ROOT` 與明確 tmpfs 以外建立 runtime file；模擬磁碟未掛載時 live mode 必須拒絕啟動；privacy list/purge 可在 fixture 資料上驗證。

### Phase 1：專案骨架與單商品通路

- 建立設定載入器與 public instrument config。
- 建立 database migration / DDL。
- 以一個股票商品完成 BidAsk + Tick 訂閱。
- 將正規化事件輸出到測試 sink，先驗證欄位與時間。

完成條件：fixture 模式能在 CI/本機重現合法事件；提供 credential 的 Linux 部署環境能以 simulation 模式成功解析設定、訂閱單一商品並接收合法事件，且 log 不含 account object 或 credential。

### Phase 2：ClickHouse 持久化

- 實作 bounded queue 與 batch writer。
- 實作 `lob_events`、`tick_events`、`capture_sessions`。
- 加入 graceful shutdown 與基本查詢。
- 加入 `LOB_DATA_ROOT` bytes/inode metrics、80% warning、90% graceful stop 與 disk-capacity gap。

完成條件：連續運行一個交易時段，資料筆數與 collector counters 一致，重啟後歷史資料仍可查詢；ClickHouse、Parquet、spool 與 private runtime 都實際寫入指定的 `LOB_DATA_ROOT`，Docker data-root 沒有主要行情資料；容量保護可用受控的小型 test filesystem 驗證。

### Phase 3：多商品與可靠性

- 支援多商品設定及逐項驗證。
- 實作 reconnect、retry、local spool、replay。
- 實作 `capture_gaps`、queue/drop/latency metrics。
- 建立斷線與 database outage 測試。
- 建立 privacy canary 測試，故意注入假的 account-like/secret-like 值，確認 log redaction、檔案盤點與 purge 都不會輸出原文。

完成條件：模擬 database 暫停後不靜默遺失資料；恢復後可 replay，且 gap/session 狀態可查；privacy purge 在 collector 停止後能清除所有 private runtime test artifacts，且不誤刪行情 tables。

### Phase 4：Pilot 與容量報告

- 選 3 至 5 個不同活躍度商品。
- 收集至少一個完整交易日，建議連續五個交易日。
- 產生容量、壓縮率、延遲與缺口報告。
- 根據實測調整 batch、queue、partition、retention。
- 對預計 20TB 容量產生 80%/90% 水位、保留空間、retention 與備份估算；報告使用實際可用容量，不假設標稱容量等於可用容量。

完成條件：能用實際數字回答指定商品數量需要多少磁碟與運算資源。

### Phase 5：研究資料介面

- 提供按商品與日期查詢的 SQL 範例。
- 建立每日 Parquet 匯出與 DuckDB 查詢範例。
- 建立資料品質檢查：排序、重複、crossed book、負數量、時間缺口。

完成條件：研究程式不需連到 production collector，也能從唯讀 ClickHouse 或 Parquet dataset 重現指定時段。

## 11. Git 與 GitHub 發布策略

- Phase 0 才執行 `git init`，預設 branch 為 `main`；本次規劃更新不先建立 remote 或推送 GitHub。
- Git 只追蹤 source、tests、migration、Docker 定義、public config、placeholder 與文件。
- `.gitignore` 與 `.dockerignore` 必須排除 `secrets/` 真實檔、實際 `host.env`、`.env`、`*.pfx`、`private-runtime/`、Shioaji state、ClickHouse data、spool、backup、log、tmp、crash dump、Parquet export 與本機工具狀態。
- `secrets/shioaji.env.example` 以 allowlist 例外保留；除固定 key 名稱與 placeholder 外不可包含其他內容。
- 每次準備 commit 與首次 push 前執行 secret scan、個資模式掃描、`git status` 與 `git ls-files` audit；掃描結果只顯示檔名與規則，不回顯疑似 secret。
- 不把 credential 放進 commit 後再刪；Git history 仍會保留。若誤提交，在任何 GitHub push 前先旋轉 credential、重寫 history 並重新掃描。
- GitHub repository 由使用者之後自行建立與決定 public/private；建立 remote 與 push 不屬於 MVP 自動執行步驟。

## 12. MVP 驗收標準

- 能透過設定檔啟用或停用商品，不修改 collector 程式碼。
- 能同時保存五檔 BidAsk 與 Tick。
- 收集器重啟不破壞既有資料。
- database 短暫失效時不會靜默丟資料。
- 每次運行都有 session 與 gap 稽核紀錄。
- 能查出「某商品、某日期、某時間區間」的完整事件序列。
- 能匯出 Parquet 並用 DuckDB 查詢。
- 同一份 repository clone 到另一台 Linux 主機後，只需安裝 Docker、掛載資料磁碟、設定 repo 外 `host.env`、建立單一主機憑證檔並啟動 Compose，不需修改 source code。
- `LOB_DATA_ROOT` 可指定到 20TB 磁碟或其他 Linux mount point；ClickHouse、Parquet、spool、backup 與 private runtime 都依設定寫入該位置，而不是 Docker data-root。
- Live mode 在資料磁碟未掛載、marker 不符、路徑不可寫或到達 critical capacity threshold 時 fail closed，不得靜默改寫系統碟。
- API Key、Secret Key、token、account object 與個人識別欄位不會進入 Git、Docker image/build cache、Compose environment、Docker daemon log、ClickHouse 或 Parquet。
- `SJ_LOG_PATH`、`SJ_HOME_PATH`、`SJ_CONTRACTS_PATH` 與所有可疑 runtime output 都落在可列出、可手動清除、預設不備份的 `private-runtime` 範圍。
- `scripts/privacy-list` 不顯示敏感內容；`scripts/privacy-purge` 能分別清除 runtime、credential 與 database metadata，並保護行情 tables 不被預設刪除。
- Collector 只使用行情所需的最小權限；MVP 不保存 CA、不要求下單權限，也不包含 production order 程式碼。

## 13. 技術決策摘要

- Language：Python（直接使用 Shioaji callback/async API）。
- Primary database：ClickHouse。
- Offline/research format：Zstd-compressed Parquet。
- Local analytics：DuckDB。
- Product selection：`config/instruments.yaml`。
- Runtime：Linux + Docker Compose，單一 long-running collector container。
- Storage：repo 外 `host.env` 設定 `LOB_DATA_ROOT`；主要資料使用 host bind mounts，預設範例 `/mnt/lob-data`。
- Secrets：repo 外單一 `0600` credential file，以唯讀 Compose secret 掛入。
- Privacy runtime：host bind mount，集中 Shioaji home/log/token/cache、collector log、tmp 與 crash artifacts；spool 使用獨立可控 bind mount。
- Network：collector outbound only；ClickHouse internal network only。
- Portability：在目標 Linux 主機從 source build；GitHub remote 之後由使用者建立。
- Ingestion：bounded queue + batch insert + durable local spool。
- Data policy：append-only raw events，衍生特徵另建資料集，不覆寫原始資料。

## 14. 參考資料

- [Shioaji 即時股票五檔](https://sinotrade.github.io/zh/tutor/market_data/streaming/stocks/)
- [Shioaji 歷史行情](https://sinotrade.github.io/zh/tutor/market_data/historical/)
- [ClickHouse time-series guide](https://clickhouse.com/docs/use-cases/time-series)
- [ClickHouse MergeTree](https://clickhouse.com/docs/engines/table-engines/mergetree-family/mergetree)
- [DuckDB Parquet](https://duckdb.org/docs/stable/data/parquet/overview)

## 15. 執行狀態（2026-07-20）

本節是 goal 執行進度的唯一狀態表。`本機完成` 代表不需要真實 credential、交易時段或目標 Linux mount 的部分已實作並驗證；`外部驗收` 只能由部署者在目標 Linux/Shioaji 環境完成，不得用 fixture 結果冒充。

| Phase | 狀態 | 已完成證據 | 尚需外部驗收 |
| --- | --- | --- | --- |
| Phase 0 | 本機完成 | Git `main`、fully pinned Python/Docker、Compose config、non-root/read-only fixture container、root RO + nested RW mounts、Linux tmpfs UID/mode/marker/live fail-closed、containerized privacy controls、tracked-file scan、read-only acceptance check | 目標 Linux 20TB mount/UUID 與 host credential mode |
| Phase 1 | 完成（本機＋部署者 live 驗收） | instrument config、BidAsk/Tick normalization、最新五檔 enrichment、callback-before-subscribe、逐 stream 部分失敗、Shioaji 1.5 uppercase 與新版 lowercase contract facade 相容；部署者確認目標 Linux simulation collector 已持續收到並寫入單一股票行情 | 無；FUT/OPT 與多商品外部驗收列在 Phase 3 |
| Phase 2 | 本機完成；交易時段 gate | bounded queue/batch metrics、bytes/inode capacity gauges、ClickHouse fixture insert、session/gap latest views、idempotent migrator、graceful stop、93.82% Linux tmpfs capacity stop、bind-mount persistence；部署者已確認 live rows 寫入 | 完整交易時段 counters/rows、重啟後持久性與目標 20TB filesystem 實測 |
| Phase 3 | 本機完成；FUT live gate 完成；長時間 gate | 多商品 loader、FUT/OPT callbacks、`TXFR1` logical/resolved/target metadata mapping、FOP optional diff handling、connection gap/reconnect counter、exponential login backoff、durable market/audit spool、同 process 自動 replay、short Docker outage 與 privacy purge 實測；部署者在目標 Linux 確認 `FUT` BidAsk/Tick rows 持續增加 | 目標 Linux OPT、多商品、網路斷線、container restart、長時間 outage/replay 測試 |
| Phase 4 | 工具完成；pilot 待執行 | `pilot-report` 查詢 ClickHouse rows、平均/尖峰 EPS、latency、parts、session/gap，並用實際可用容量與觀測壓縮 bytes/day 估算 80%/90% 水位及 retention days | 3–5 商品至少一完整交易日、建議五日，據實決定 retention/backup |
| Phase 5 | 本機完成 | BidAsk+Tick 時間區間 event query、全商品日匯出、Zstd Parquet、DuckDB `union_by_name`、duplicate/order/sequence/time gap/timestamp/crossed book/negative volume checks | 用實際 pilot dataset 重跑並保存結果 |

### 本機驗證摘要

- Host unit tests：59 tests passed；不使用 Shioaji credential，包含 Shioaji 1.5 legacy contract lookup、FUT/OPT target metadata、FOP optional fields、safe login/lookup category、全訂閱失敗診斷、pilot retention 邊界與 acceptance report no-leak checks。
- Target Linux live gate（部署者提供的外部證據）：simulation collector 已持續收到並寫入單一股票行情；另在 `security_type = 'FUT'` 的兩次 ClickHouse 查詢中，Tick rows 由 `138` 增至 `141`、BidAsk rows 由 `746` 增至 `779`，證明大台期貨兩種 stream 持續寫入。這份證據不等同完整交易時段、多商品、選擇權、重啟或 outage 驗收；Codex 未直接存取該主機，也未保存任何 credential 或私人 runtime output。
- Hardened fixture container：UID/GID `10001:10001`、read-only rootfs、`cap_drop: ALL`、`no-new-privileges`、tmpfs `/tmp`。
- Linux storage proof：獨立 tmpfs 上 `host-prepare`/`storage-check` 通過；root/marker 為 root-owned read-only metadata，UID 10001 只能寫三個 nested mounts，普通目錄 live validation 被拒絕；16MB tmpfs 93.82% 時觸發 `disk_capacity` stop。
- Compose：ClickHouse、idempotent migrator 與 collector 啟動成功；ClickHouse ports 未發布；collector 使用 no logging driver、唯讀 rootfs 與 repo 外 bind root。
- Clean outage fixture：同一 process 收到 `362` 筆，正常寫入 `326`、spool `36`、replay `36`、drop `0`；唯一 database gap 的 `open_intervals=0`。
- Session/audit：定期 counters、batch/queue/latency metrics、公開 subscription results 可查；舊 open audit 必須先 replay，closed gap 最後寫入。
- Research path：全商品日匯出兩張 Zstd Parquet 共 `362` 列；DuckDB 可查；quality 七類 counters 全為 `0`。
- Pilot tool fixture proof：LOB/Tick 各 `181` rows、compressed bytes `10,316`、fixture latency p50/p95/p99 分別為 `5/5/5 ms` 與 `7/7/7 ms`；工具另輸出每商品/日期 peak EPS、觀測交易日、平均 compressed bytes/day 與 90% 水位 retention days。20TB decimal usable bytes 對應 80% `16,000,000,000,000`、90% `18,000,000,000,000`。這些 fixture 數字不代表真實市場容量。
- Privacy path：management container 固定使用 placeholder secret；runtime、market spool schema 與 database metadata allowlist 通過；實際 runtime purge 保留 bind root/Parquet/ClickHouse，一筆 spool purge 留下 affected count 1 的 closed gap。
- External acceptance path：單一 read-only script 驗證 storage/Compose，並將 health、market rows、latest session 與 gap aggregates 寫入 Parquet 區的 allowlisted JSON；刻意忽略 session ID、未知欄位與非公開 token，且不把當下摘要冒充 reboot/outage/full-day 證據。

### Goal 完成邊界

Source、fixture、Docker/Compose 與本機可重現驗證已完成，單一股票與大台期貨的目標 Linux live gate 亦已由部署者確認。整體 goal 尚未完成：仍需目標 20TB filesystem 的 mount/reboot 證據、OPT 與多商品行情驗收、完整交易時段/重啟/長時間 outage 測試，以及 3–5 商品 pilot 與實際 retention/backup 決策。在這些條件完成前，不把 Phase 0、2、3、4 的外部驗收標為完成，也不要求使用者把 credential 提供給 Codex。
