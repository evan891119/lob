# 驗證紀錄

此檔只保存可重現的技術驗證，不保存主機、帳戶或 credential 資訊。詳細完成狀態仍以 `docs/project-plan.md` 為準。

## 本機已完成

- Python 3.14 host 上以 `PYTHONPATH=src` 執行 stdlib unit tests。
- Docker image 以 Python 3.12 與 pinned dependencies 建置成功。
- Hardened fixture container：UID/GID 10001、read-only rootfs、capabilities ALL dropped、tmpfs `/tmp`。
- Compose：ClickHouse healthy、collector healthy、ClickHouse ports 未發布到 host。
- DDL：`lob_events`、`tick_events`、`capture_sessions`、`capture_gaps` 建立成功。
- Fixture ClickHouse insert：BidAsk 與 Tick 各至少一列。
- Database insert failure：event 進入 spool 且 `capture_gaps` 留下清理後分類。
- Zstd Parquet：兩張市場 table 匯出成功；DuckDB 能以 `union_by_name` 查詢。
- Private runtime inventory：只列檔案 metadata 和命中數，fixture artifacts 命中數為 0。

## 必須在目標 Linux/外部環境完成

- 真實 mount-point、marker、UID/GID 與 80%/90% 小型受控 filesystem 測試。
- 真實 Shioaji simulation login、單商品及多商品交易時段訂閱。
- Database outage、container restart、網路斷線與 spool replay 的長時間測試。
- 至少一完整交易日、建議五日的 3–5 商品 pilot。
- 依實際 filesystem 可用 bytes 產生 20TB retention/backup 報告。
