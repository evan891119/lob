# Pilot 容量與品質報告

此模板只記錄公開商品代碼與技術量測，不得填入帳戶、credential、主機名稱或個人路徑。

## 採集範圍

- 日期：`YYYY-MM-DD` 至 `YYYY-MM-DD`
- 商品：3–5 個不同活躍度的公開商品代碼
- ClickHouse 可用容量（bytes）：
- 實際採集小時：
- `report_scope.market_storage_measurement`：
- scoped bytes 是 exact／row-share estimate：

## 實測結果

| 指標 | 平均 | P95 | 尖峰 |
| --- | ---: | ---: | ---: |
| events / second | | | |
| callback-to-received latency (ms) | | | |
| batch insert latency (ms) | | | |
| queue usage (%) | | | |
| collector process CPU (%) | | | |
| collector process max RSS (bytes) | | | |

| 商品 | lob rows/day | tick rows/day | bytes on disk/day | compression ratio |
| --- | ---: | ---: | ---: | ---: |
| | | | | |

## 完整性

- reconnect 次數：
- queue drop：
- spool / replay：
- capture gaps：
- quality checks（duplicate / ordering / crossed book / negative volume）：

## 20TB 磁碟估算

- `pilot-report.json` 會提供觀測商品／交易日數、平均 `bytes_on_disk`/day、壓縮前後 bytes、compression ratio，以及 10／50／100 商品到 90% 水位的估計保留天數；資料集為空時 ratio、projection 與保留天數必須是 `null`，不得臆測。
- filesystem 實際可用容量：
- 80% warning bytes：`實際可用容量 × 0.80`
- 90% stop bytes：`實際可用容量 × 0.90`
- 每日成長量：
- 保留天數估算：`90% stop bytes ÷ 每日成長量`
- 10／50／100 商品的 20 日、250 日容量：
- 一份新行情 full-copy backup 的月／年 bytes：
- replication、版本、增量鏈、加密與 filesystem overhead 預留：
- backup 預留與 retention 決策：

## Projection 判讀

- `minimum_dataset_scope_reached`：
- `recommended_five_day_scope_reached`：
- 完整交易時段已由部署者核對：是／否
- 線性商品數投影是否適合這組活躍度配置：
- `conservative_peak_sum` 與實際 aggregate peak 的差異：
