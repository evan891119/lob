SELECT *
FROM lob.lob_events
WHERE symbol = {symbol:String}
  AND trading_date = {trading_date:Date}
  AND event_ts >= {start:DateTime64(6, 'Asia/Taipei')}
  AND event_ts < {end:DateTime64(6, 'Asia/Taipei')}
ORDER BY event_ts, sequence_no;
