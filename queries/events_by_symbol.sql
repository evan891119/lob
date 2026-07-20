SELECT
    stream,
    trading_date,
    exchange,
    security_type,
    symbol,
    event_ts,
    received_ts,
    session_id,
    sequence_no,
    simtrade,
    intraday_odd,
    ingested_at
FROM
(
    SELECT
        'bidask' AS stream,
        trading_date, exchange, security_type, symbol, event_ts, received_ts,
        session_id, sequence_no, simtrade, intraday_odd, ingested_at
    FROM lob.lob_events
    UNION ALL
    SELECT
        'tick' AS stream,
        trading_date, exchange, security_type, symbol, event_ts, received_ts,
        session_id, sequence_no, simtrade, intraday_odd, ingested_at
    FROM lob.tick_events
)
WHERE security_type = {security_type:String}
  AND exchange = {exchange:String}
  AND symbol = {symbol:String}
  AND trading_date = {trading_date:Date}
  AND event_ts >= {start:DateTime64(6, 'Asia/Taipei')}
  AND event_ts < {end:DateTime64(6, 'Asia/Taipei')}
ORDER BY event_ts, received_ts, session_id, sequence_no;
