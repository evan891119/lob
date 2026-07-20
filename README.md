# Shioaji LOB Recorder

Linux/Docker 用即時五檔與逐筆成交收集器。資料寫入 ClickHouse，研究資料可匯出為 Zstd Parquet 並由 DuckDB 離線查詢。專案不下單、不使用 CA，也不把 API credential、account object 或私人主機資訊放進 Git。

完整需求、設計決策、階段與目前驗證狀態以 [`docs/project-plan.md`](docs/project-plan.md) 為唯一規格來源。Linux 實際部署與清理步驟見 [`docs/linux-deployment.md`](docs/linux-deployment.md)。

## 本機無 credential 驗證

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m lob_recorder.cli fixture \
  --input fixtures/events.jsonl \
  --output /tmp/lob-fixture.jsonl
PYTHONPATH=src python3 -m lob_recorder.cli quality \
  --input /tmp/lob-fixture.jsonl
```

## Linux 啟動摘要

```bash
sudo scripts/host-prepare /mnt/lob-data
sudo scripts/storage-check /mnt/lob-data
docker compose --env-file /etc/shioaji-lob-recorder/host.env config --quiet
docker compose --env-file /etc/shioaji-lob-recorder/host.env up -d --build
sudo scripts/acceptance-check /mnt/lob-data /etc/shioaji-lob-recorder/host.env
```

正式啟動前必須由部署者在 repo 外建立 `/etc/shioaji-lob-recorder/host.env` 與 mode `0600` 的 `/etc/shioaji-lob-recorder/shioaji.env`。不要把真實值貼進 issue、commit、shell command 或對話。
