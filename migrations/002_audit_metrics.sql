ALTER TABLE lob.capture_sessions ADD COLUMN IF NOT EXISTS symbols Array(String) AFTER status;
ALTER TABLE lob.capture_sessions ADD COLUMN IF NOT EXISTS subscriptions_active UInt16 AFTER enabled_symbols;
ALTER TABLE lob.capture_sessions ADD COLUMN IF NOT EXISTS subscriptions_failed UInt16 AFTER subscriptions_active;
ALTER TABLE lob.capture_sessions ADD COLUMN IF NOT EXISTS subscription_results Array(String) AFTER subscriptions_failed;
ALTER TABLE lob.capture_sessions ADD COLUMN IF NOT EXISTS notice_dropped UInt64 AFTER dropped;
ALTER TABLE lob.capture_sessions ADD COLUMN IF NOT EXISTS queue_capacity UInt32 AFTER reconnects;
ALTER TABLE lob.capture_sessions ADD COLUMN IF NOT EXISTS queue_high_water UInt32 AFTER queue_capacity;
ALTER TABLE lob.capture_sessions ADD COLUMN IF NOT EXISTS capacity_bytes_percent Nullable(Float64) AFTER queue_high_water;
ALTER TABLE lob.capture_sessions ADD COLUMN IF NOT EXISTS capacity_inode_percent Nullable(Float64) AFTER capacity_bytes_percent;
ALTER TABLE lob.capture_sessions ADD COLUMN IF NOT EXISTS capacity_used_percent Nullable(Float64) AFTER capacity_inode_percent;
ALTER TABLE lob.capture_sessions ADD COLUMN IF NOT EXISTS batch_count UInt64 AFTER capacity_used_percent;
ALTER TABLE lob.capture_sessions ADD COLUMN IF NOT EXISTS batch_insert_ms_total Float64 AFTER batch_count;
ALTER TABLE lob.capture_sessions ADD COLUMN IF NOT EXISTS batch_insert_ms_max Float64 AFTER batch_insert_ms_total;
ALTER TABLE lob.capture_sessions ADD COLUMN IF NOT EXISTS callback_latency_ms_max Float64 AFTER batch_insert_ms_max;
ALTER TABLE lob.capture_sessions ADD COLUMN IF NOT EXISTS clock_anomalies UInt64 AFTER callback_latency_ms_max;
ALTER TABLE lob.capture_sessions ADD COLUMN IF NOT EXISTS updated_at DateTime64(6, 'Asia/Taipei') DEFAULT now64(6);
ALTER TABLE lob.capture_gaps ADD COLUMN IF NOT EXISTS updated_at DateTime64(6, 'Asia/Taipei') DEFAULT now64(6);

DROP VIEW IF EXISTS lob.capture_sessions_latest;
DROP VIEW IF EXISTS lob.capture_gaps_latest;

CREATE VIEW lob.capture_sessions_latest AS
SELECT * EXCEPT updated_at FROM lob.capture_sessions
ORDER BY updated_at DESC LIMIT 1 BY session_id;

CREATE VIEW lob.capture_gaps_latest AS
SELECT * EXCEPT updated_at FROM lob.capture_gaps
ORDER BY updated_at DESC LIMIT 1 BY gap_id;
