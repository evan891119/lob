-- Run with: duckdb -readonly < queries/duckdb_parquet.sql
-- This glob intentionally reads both lob_events.parquet and tick_events.parquet.
WITH events AS
(
    SELECT
        CASE
            WHEN filename LIKE '%/lob_events.parquet' THEN 'bidask'
            WHEN filename LIKE '%/tick_events.parquet' THEN 'tick'
            ELSE 'unknown'
        END AS stream,
        security_type,
        exchange,
        symbol,
        trading_date,
        event_ts,
        received_ts,
        sequence_no,
        bid_price_1,
        bid_volume_1,
        ask_price_1,
        ask_volume_1,
        close,
        volume,
        total_volume,
        best_bid_price,
        best_bid_volume,
        best_ask_price,
        best_ask_volume
    FROM read_parquet(
        '/mnt/lob-data/parquet/security_type=*/exchange=*/symbol=*/trading_date=*/*.parquet',
        hive_partitioning = true,
        union_by_name = true,
        filename = true
    )
)
SELECT *
FROM events
WHERE security_type = 'STK'
  AND exchange = 'TSE'
  AND symbol = '2330'
  AND trading_date = DATE '2026-01-02'
  AND event_ts >= TIMESTAMPTZ '2026-01-02 09:00:00+08:00'
  AND event_ts < TIMESTAMPTZ '2026-01-02 13:30:00+08:00'
ORDER BY event_ts, received_ts, sequence_no
LIMIT 100;
