CREATE DATABASE IF NOT EXISTS lob;

CREATE TABLE IF NOT EXISTS lob.lob_events (
    trading_date Date,
    exchange LowCardinality(String), security_type LowCardinality(String), symbol LowCardinality(String),
    event_ts DateTime64(6, 'Asia/Taipei'), received_ts DateTime64(6, 'Asia/Taipei'),
    session_id UUID, sequence_no UInt64,
    bid_price_1 Decimal64(4), bid_price_2 Decimal64(4), bid_price_3 Decimal64(4), bid_price_4 Decimal64(4), bid_price_5 Decimal64(4),
    bid_volume_1 Int64, bid_volume_2 Int64, bid_volume_3 Int64, bid_volume_4 Int64, bid_volume_5 Int64,
    ask_price_1 Decimal64(4), ask_price_2 Decimal64(4), ask_price_3 Decimal64(4), ask_price_4 Decimal64(4), ask_price_5 Decimal64(4),
    ask_volume_1 Int64, ask_volume_2 Int64, ask_volume_3 Int64, ask_volume_4 Int64, ask_volume_5 Int64,
    diff_bid_vol_1 Int64, diff_bid_vol_2 Int64, diff_bid_vol_3 Int64, diff_bid_vol_4 Int64, diff_bid_vol_5 Int64,
    diff_ask_vol_1 Int64, diff_ask_vol_2 Int64, diff_ask_vol_3 Int64, diff_ask_vol_4 Int64, diff_ask_vol_5 Int64,
    simtrade Bool, intraday_odd Bool, ingested_at DateTime64(6, 'Asia/Taipei') DEFAULT now64(6)
) ENGINE = MergeTree PARTITION BY toYYYYMM(trading_date)
ORDER BY (symbol, trading_date, event_ts, sequence_no);

CREATE TABLE IF NOT EXISTS lob.tick_events (
    trading_date Date,
    exchange LowCardinality(String), security_type LowCardinality(String), symbol LowCardinality(String),
    event_ts DateTime64(6, 'Asia/Taipei'), received_ts DateTime64(6, 'Asia/Taipei'),
    session_id UUID, sequence_no UInt64,
    close Decimal64(4), volume Int64, total_volume Int64, tick_type Int8,
    best_bid_price Decimal64(4), best_bid_volume Int64, best_ask_price Decimal64(4), best_ask_volume Int64,
    simtrade Bool, intraday_odd Bool, ingested_at DateTime64(6, 'Asia/Taipei') DEFAULT now64(6)
) ENGINE = MergeTree PARTITION BY toYYYYMM(trading_date)
ORDER BY (symbol, trading_date, event_ts, sequence_no);

CREATE TABLE IF NOT EXISTS lob.capture_sessions (
    session_id UUID, started_at DateTime64(6, 'Asia/Taipei'), ended_at Nullable(DateTime64(6, 'Asia/Taipei')),
    simulation Bool, status LowCardinality(String), symbols Array(String), enabled_symbols UInt16,
    subscriptions_active UInt16, subscriptions_failed UInt16, subscription_results Array(String),
    received UInt64, written UInt64, spooled UInt64, replayed UInt64, dropped UInt64, notice_dropped UInt64,
    reconnects UInt32, queue_high_water UInt32, batch_count UInt64,
    batch_insert_ms_total Float64, batch_insert_ms_max Float64, callback_latency_ms_max Float64,
    clock_anomalies UInt64, updated_at DateTime64(6, 'Asia/Taipei') DEFAULT now64(6)
) ENGINE = MergeTree ORDER BY (started_at, session_id);

CREATE TABLE IF NOT EXISTS lob.capture_gaps (
    gap_id UUID, session_id UUID, started_at DateTime64(6, 'Asia/Taipei'), ended_at Nullable(DateTime64(6, 'Asia/Taipei')),
    category LowCardinality(String), correlation_id FixedString(12), affected_count UInt64,
    updated_at DateTime64(6, 'Asia/Taipei') DEFAULT now64(6)
) ENGINE = MergeTree ORDER BY (started_at, session_id, gap_id);

CREATE VIEW IF NOT EXISTS lob.capture_sessions_latest AS
SELECT * EXCEPT updated_at FROM lob.capture_sessions
ORDER BY updated_at DESC LIMIT 1 BY session_id;

CREATE VIEW IF NOT EXISTS lob.capture_gaps_latest AS
SELECT * EXCEPT updated_at FROM lob.capture_gaps
ORDER BY updated_at DESC LIMIT 1 BY gap_id;
