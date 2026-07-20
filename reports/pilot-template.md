# Pilot 容量與品質報告

此模板只記錄公開商品代碼與技術量測，不得填入帳戶、credential、主機名稱或個人路徑。

## 採集範圍

- 日期：`YYYY-MM-DD` 至 `YYYY-MM-DD`
- 商品：3–5 個不同活躍度的公開商品代碼
- ClickHouse 可用容量（bytes）：
- 實際採集小時：

## 實測結果

| 指標 | 平均 | P95 | 尖峰 |
| --- | ---: | ---: | ---: |
| events / second | | | |
| callback-to-received latency (ms) | | | |
| batch insert latency (ms) | | | |
| queue usage (%) | | | |

| 商品 | lob rows/day | tick rows/day | compressed bytes/day |
| --- | ---: | ---: | ---: |
| | | | |

## 完整性

- reconnect 次數：
- queue drop：
- spool / replay：
- capture gaps：
- quality checks（duplicate / ordering / crossed book / negative volume）：

## 20TB 磁碟估算

- `pilot-report.json` 會提供觀測交易日數、平均壓縮 bytes/day 與到 90% 水位的估計保留天數；資料集為空時保留天數必須是 `null`，不得臆測。
- filesystem 實際可用容量：
- 80% warning bytes：`實際可用容量 × 0.80`
- 90% stop bytes：`實際可用容量 × 0.90`
- 每日成長量：
- 保留天數估算：`90% stop bytes ÷ 每日成長量`
- backup 預留與 retention 決策：
