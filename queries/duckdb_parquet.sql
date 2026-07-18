-- Run with: duckdb -readonly < queries/duckdb_parquet.sql
SELECT symbol, event_ts, sequence_no, bid_price_1, ask_price_1
FROM read_parquet('/mnt/lob-data/parquet/symbol=*/trading_date=*/lob_events.parquet', hive_partitioning = true)
WHERE symbol = '2330'
ORDER BY event_ts, sequence_no
LIMIT 100;
