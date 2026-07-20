# 驗證紀錄

此檔只保存可重現的技術驗證，不保存主機、帳戶或 credential 資訊。詳細完成狀態仍以 `docs/project-plan.md` 為準。

## 本機已完成

- Python 3.14 host 上以 `PYTHONPATH=src` 執行 101 個 stdlib unit tests。
- Docker image 以 Python 3.12 與 pinned dependencies 建置成功。
- Pinned Shioaji 1.5.3 在無 credential/read-only container 中確認 contract descriptor、BidAsk/Tick STK/FOP callbacks、system callbacks、subscribe/unsubscribe surface 存在。
- Shioaji source 同時支援新版 lowercase contract facade 與 1.5 legacy uppercase facade；legacy direct lookup、exchange group fallback、safe login/lookup category 與全訂閱失敗結果均有無 credential unit test。
- FUT/OPT callback 會在訂閱前登記 logical code、resolved code 與 continuous future `target_code`；FOP 未提供 diff-volume 欄位時補為 schema 的零值，並保留實際 callback symbol 供歷史查詢。
- Hardened fixture container：UID/GID 10001、read-only rootfs、capabilities ALL dropped、tmpfs `/tmp`。
- Linux tmpfs mount：`host-prepare`/`storage-check` 的 UID/GID/mode/marker 通過；UID 10001 可讀 root、只能寫指定子目錄，普通未掛載目錄被 live mode 拒絕。
- 16MB Linux tmpfs 實測 93.82% 使用率會觸發 `disk_capacity` stop；health/session 分開記錄 bytes 與 inode percent。
- Compose：ClickHouse healthy、collector healthy、ClickHouse ports 未發布到 host。
- DDL/migration：四張 tables、latest views 與可重跑 migrator 建立成功；舊資料目錄也能補欄位及重建 views。
- Fixture ClickHouse insert：BidAsk 與 Tick 各至少一列。
- 短時間 database outage：同一 collector process 收到 362 筆，正常寫入 326 筆、spool/replay 36/36、drop 0；唯一 database gap 已關閉。
- Audit ordering：open gap 先於 market replay，closed gap 最後寫入；恢復後 latest view 不殘留錯誤的 open gap。
- Zstd Parquet：全商品日匯出 362 列；DuckDB 能以 `union_by_name` 查詢；明確標示完整 sequence scope 後 7 類 quality counters 全為 0。部分商品／stream scope 會輸出 `sequence_gaps=null`，不把其他商品的合法 sequence 誤報為缺號；duplicate identity 使用 session＋sequence，能抓到跨 stream 重複。
- 查詢 SQL：同一 security type/exchange/symbol/date/time range 實際合併回傳 BidAsk 與 Tick event envelope，並以 event/received/session/sequence 穩定排序。Parquet exporter 以完整 market identity 查詢及建立 Hive partitions，同代碼跨 STK/FUT/OPT 或 exchange 不會共用輸出檔。
- Private runtime inventory：containerized management path 不需 host Python；實際 runtime purge 清除 2 個檔案後保留 mount root inode、Parquet 與行情 tables；一筆 spool purge 留下 closed `manual_spool_purge` gap（affected count 1）。
- Pilot report unit proof：inclusive start/end date 以 ClickHouse typed parameters 限制 market/peak/session/gap，缺單側、倒置範圍及非正容量 fail closed；按 `security_type + exchange + symbol` 合併 average/peak EPS。無日期時使用 exact active parts；日期範圍則按每 table scoped/active rows 比例估算 `bytes_on_disk` 與壓縮前後 data bytes，report 明列 measurement method 並保留 global exact totals。Retention/projection 使用 scoped bytes，另輸出最低／建議 scope，以及明列 20／250 交易日、線性商品數、保守 peak-sum 與單一 full-copy backup 假設的 10／50／100 商品 projections；空資料集衍生值維持 `null`。
- Acceptance report no-leak proof：health 中注入假的 session/account/unknown canary 後輸出不含原文；health 不可讀時只回傳安全 unavailable 狀態。Wrapper 先以 metadata-only 驗證 repo 外 credential 是絕對路徑、非 symlink regular file 且 owner/mode 精確為 `10001:10001`/`0600`，再執行 storage check、Compose config 與 read-only ClickHouse/health 查詢；錯誤 owner 或 mode 均 fail closed，report 另分開判斷 STK/FUT/OPT 是否各自同時具有 BidAsk/Tick rows。最近已結束 session 會用內部參數化 UUID 直接計算兩張 market table rows，但輸出不含 UUID；rows mismatch 與沒有 completed session 都會讓 reconciliation 明確失敗。
- Storage identity wrapper proof：只接受 ext4/XFS 與 exact mount target；direct layout 要求 filesystem UUID 對應資料根目錄的 `/etc/fstab` entry，shared layout 另要求底層 UUID mount、exact bind source 與 explicit systemd dependency。成功輸出只有 layout、filesystem 類型、容量 bytes 與布林結果，device/UUID/path canary 不會出現在 stdout/stderr。這不取代實際 reboot 後重跑。
- Docker boot readiness proof：唯讀 wrapper 要求 `docker.service` enabled/active、Compose config 有效、ClickHouse/collector 正在執行且實際 restart policies 都是 `unless-stopped`；disabled/inactive/stopped/policy mismatch 均 fail closed，輸出不含 host path 或 container ID。這是靜態 readiness，不冒充實際 reboot persistence。
- Database outage drill proof：wrapper 沒有明確 confirmation 或 duration 超過 30–900 秒時會在呼叫 Docker 前拒絕；執行階段以 trap 保證失敗/interrupt 仍嘗試啟動 ClickHouse。Verifier 只比較 allowlisted acceptance reports，要求同一 simulation session、spooled/replayed delta、零新增 drop 與新增且 closed 的 database gap；輸出不含輸入中的 private canary。這些 unit proof 不冒充目標 Linux 實際 outage。
- Shioaji reconnect proof：event `13` callback 只 signal main thread，main thread 才重新訂閱已解析商品的全部 configured streams；逐 stream 結果仍使用安全 category，成功後才記錄 reconnect/close gap，全部失敗則 exponential retry。Network drill 只 disconnect collector 的 Compose outbound network，保留 ClickHouse internal network，並以 exit trap 恢復；wrapper confirmation/bounds 與 verifier 的 same-session/reconnect/subscription/closed-gap/no-drop/no-leak checks 有 unit proof，但尚未冒充目標 Linux 實際 network drill。
- Health atomic-write concurrency proof：main/worker 共用單一 lock 保護同一 `health.tmp` 的 write/replace；8 threads 各寫 50 次後沒有 exception，最終 JSON 仍可解析，避免並行 replace 偶發遺失 temporary file。
- Process resource proof：collector 將 session-relative CPU seconds 與 process max RSS bytes 寫入 health/capture session；pilot report 以 session duration 計算 average process CPU percent。注入式 unit probe 證明數值轉換與持久化，不冒充目標 Linux 或 ClickHouse container 的實際資源量測。
- Host reboot proof tooling：prepare/verify wrapper 都需要明確 confirmation；raw boot identifier 只以 `10001:10001`/`0600` 暫存在可 purge 的 private-runtime，report 不含該值。Verifier 要求 boot 改變、collector session 改變、simulation、最早歷史 event 保留、LOB/Tick rows 不減、health/subscriptions/storage/no-open-gap 全通過；same boot/session 與 private canary cases 有 unit proof，但尚未冒充目標 Linux 實際 reboot。

## 部署者確認的外部證據

- 目標 Linux 使用修正版 image 後，Shioaji simulation collector 已持續收到並寫入單一股票行情。此證據由部署者確認；本文件不保存該主機、帳戶、credential、原始 Shioaji log 或私人 runtime 內容。
- 目標 Linux 的 `security_type = 'FUT'` ClickHouse 查詢連續兩次顯示 Tick rows `138 → 141`、BidAsk rows `746 → 779`，確認大台期貨兩種 callback 已持續寫入。此證據不代表完整交易時段、多商品、選擇權、重啟或 outage 驗收。
- Privacy-safe acceptance report 顯示 health fresh/running、simulation、`2330` 與 `TXFR1→TXFH6` 四個 subscriptions active/零 failed，STK/FUT 各自 BidAsk/Tick rows 皆存在；LOB/Tick 總列數為 `79,657/18,576`，當下 drop/spool/replay/reconnect/clock anomaly 均 `0`，三個既有 gap intervals 全部 closed，storage used `8.12%`。Wrapper 成功亦證明當下 mount/layout/marker、Compose config 與 credential path/type/`0600` metadata 通過。
- 新 collector session 起於 `13:16`，database 仍含 `10:53` 起的 rows，證明 collector rebuild/recreate 未破壞既有 ClickHouse 歷史資料；不擴大解讀為 host reboot 或 ClickHouse restore 驗收。
- Privacy-safe storage identity report 顯示 shared ext4 bind layout、底層 UUID/`fstab`、bind source 與 systemd dependency 全部通過；filesystem total `19,919,910,756,352` bytes、service usable `18,919,865,425,920` bytes。Report 不含 UUID 值、device、hostname 或 host path。

## 必須在目標 Linux/外部環境完成

- 目標 Docker boot readiness；實際重新開機後掛載／服務恢復由部署者因共用主機決定延後，維持未驗證。
- OPT 交易時段訂閱；STK＋FUT 兩商品 live gate 已由部署者確認。
- 目標 Linux 上的長時間 database outage、ClickHouse/host restart、網路斷線與 replay 測試。
- 至少一完整交易日、建議五日的 3–5 商品 pilot。
- 依實際 filesystem 可用 bytes 產生 20TB retention/backup 報告。
