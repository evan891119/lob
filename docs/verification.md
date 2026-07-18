# 驗證紀錄

此檔只保存可重現的技術驗證，不保存主機、帳戶或 credential 資訊。詳細完成狀態仍以 `docs/project-plan.md` 為準。

## 本機已完成

- Python 3.14 host 上以 `PYTHONPATH=src` 執行 38 個 stdlib unit tests。
- Docker image 以 Python 3.12 與 pinned dependencies 建置成功。
- Hardened fixture container：UID/GID 10001、read-only rootfs、capabilities ALL dropped、tmpfs `/tmp`。
- Compose：ClickHouse healthy、collector healthy、ClickHouse ports 未發布到 host。
- DDL/migration：四張 tables、latest views 與可重跑 migrator 建立成功；舊資料目錄也能補欄位及重建 views。
- Fixture ClickHouse insert：BidAsk 與 Tick 各至少一列。
- 短時間 database outage：同一 collector process 收到 362 筆，正常寫入 326 筆、spool/replay 36/36、drop 0；唯一 database gap 已關閉。
- Audit ordering：open gap 先於 market replay，closed gap 最後寫入；恢復後 latest view 不殘留錯誤的 open gap。
- Zstd Parquet：全商品日匯出 362 列；DuckDB 能以 `union_by_name` 查詢；7 類 quality counters 全為 0。
- Private runtime inventory：只列檔案 metadata 和命中數，fixture artifacts 命中數為 0；實際 runtime purge 清除 2 個檔案後仍保留 Parquet 與行情 tables。

## 必須在目標 Linux/外部環境完成

- 真實 mount-point、marker、UID/GID 與 80%/90% 小型受控 filesystem 測試。
- 真實 Shioaji simulation login、單商品及多商品交易時段訂閱。
- 目標 Linux 上的長時間 database outage、container restart、網路斷線與多商品 replay 測試。
- 至少一完整交易日、建議五日的 3–5 商品 pilot。
- 依實際 filesystem 可用 bytes 產生 20TB retention/backup 報告。
