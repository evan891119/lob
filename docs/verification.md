# 驗證紀錄

此檔只保存可重現的技術驗證，不保存主機、帳戶或 credential 資訊。詳細完成狀態仍以 `docs/project-plan.md` 為準。

## 本機已完成

- Python 3.14 host 上以 `PYTHONPATH=src` 執行 52 個 stdlib unit tests。
- Docker image 以 Python 3.12 與 pinned dependencies 建置成功。
- Pinned Shioaji 1.5.3 在無 credential/read-only container 中確認 contract descriptor、BidAsk/Tick STK/FOP callbacks、system callbacks、subscribe/unsubscribe surface 存在。
- Shioaji source 同時支援新版 lowercase contract facade 與 1.5 legacy uppercase facade；legacy direct lookup、exchange group fallback、safe login/lookup category 與全訂閱失敗結果均有無 credential unit test。
- Hardened fixture container：UID/GID 10001、read-only rootfs、capabilities ALL dropped、tmpfs `/tmp`。
- Linux tmpfs mount：`host-prepare`/`storage-check` 的 UID/GID/mode/marker 通過；UID 10001 可讀 root、只能寫指定子目錄，普通未掛載目錄被 live mode 拒絕。
- 16MB Linux tmpfs 實測 93.82% 使用率會觸發 `disk_capacity` stop；health/session 分開記錄 bytes 與 inode percent。
- Compose：ClickHouse healthy、collector healthy、ClickHouse ports 未發布到 host。
- DDL/migration：四張 tables、latest views 與可重跑 migrator 建立成功；舊資料目錄也能補欄位及重建 views。
- Fixture ClickHouse insert：BidAsk 與 Tick 各至少一列。
- 短時間 database outage：同一 collector process 收到 362 筆，正常寫入 326 筆、spool/replay 36/36、drop 0；唯一 database gap 已關閉。
- Audit ordering：open gap 先於 market replay，closed gap 最後寫入；恢復後 latest view 不殘留錯誤的 open gap。
- Zstd Parquet：全商品日匯出 362 列；DuckDB 能以 `union_by_name` 查詢；7 類 quality counters 全為 0。
- 查詢 SQL：同一 symbol/date/time range 實際合併回傳 BidAsk 與 Tick event envelope，並以 event/received/session/sequence 穩定排序。
- Private runtime inventory：containerized management path 不需 host Python；實際 runtime purge 清除 2 個檔案後保留 mount root inode、Parquet 與行情 tables；一筆 spool purge 留下 closed `manual_spool_purge` gap（affected count 1）。

## 必須在目標 Linux/外部環境完成

- 目標 20TB filesystem 的真實 mount/UUID、實際 owner/mode 與重新開機後掛載驗證。
- 目標 Linux 已確認 Shioaji 1.5.3 simulation login 能進入 contract stage；仍須用修正版 image 重驗單商品 contract/subscription/market-hours event，再驗多商品交易時段訂閱。
- 目標 Linux 上的長時間 database outage、container restart、網路斷線與多商品 replay 測試。
- 至少一完整交易日、建議五日的 3–5 商品 pilot。
- 依實際 filesystem 可用 bytes 產生 20TB retention/backup 報告。
