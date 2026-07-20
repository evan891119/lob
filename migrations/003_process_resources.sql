ALTER TABLE lob.capture_sessions
    ADD COLUMN IF NOT EXISTS process_cpu_seconds Float64 AFTER clock_anomalies;
ALTER TABLE lob.capture_sessions
    ADD COLUMN IF NOT EXISTS process_max_rss_bytes UInt64 AFTER process_cpu_seconds;

DROP VIEW IF EXISTS lob.capture_sessions_latest;

CREATE VIEW lob.capture_sessions_latest AS
SELECT * EXCEPT updated_at FROM lob.capture_sessions
ORDER BY updated_at DESC LIMIT 1 BY session_id;
