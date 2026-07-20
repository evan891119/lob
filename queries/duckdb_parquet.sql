-- Run with: duckdb -readonly < queries/duckdb_parquet.sql
SELECT security_type, exchange, symbol, event_ts, sequence_no, bid_price_1, ask_price_1
FROM read_parquet('/mnt/lob-data/parquet/security_type=*/exchange=*/symbol=*/trading_date=*/lob_events.parquet', hive_partitioning = true, union_by_name = true)
WHERE security_type = 'STK' AND exchange = 'TSE' AND symbol = '2330'
ORDER BY event_ts, sequence_no
LIMIT 100;
