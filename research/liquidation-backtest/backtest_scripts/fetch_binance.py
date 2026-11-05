"""Download Binance spot 1m klines (public API, no key) for the two crash windows plus a daily series to today."""
import time, sys, requests, pandas as pd
from datetime import datetime, timezone
OUT = sys.argv[1]

def ms(s):
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp() * 1000)

def klines(symbol, interval, start, end):
    rows, t = [], start
    while t < end:
        for attempt in range(5):
            try:
                r = requests.get("https://api.binance.com/api/v3/klines",
                                 params=dict(symbol=symbol, interval=interval, startTime=t, endTime=end, limit=1000), timeout=30)
                r.raise_for_status(); batch = r.json(); break
            except Exception as e:
                print("retry", e); time.sleep(2)
        if not batch: break
        rows += batch
        t = batch[-1][0] + 1
        time.sleep(0.15)
    df = pd.DataFrame(rows, columns=["open_time","open","high","low","close","volume","close_time","quote_volume","trades","tb_base","tb_quote","ignore"])
    df = df[["open_time","open","high","low","close","volume","quote_volume","trades"]]
    for c in ["open","high","low","close","volume","quote_volume"]: df[c] = df[c].astype(float)
    df["ts"] = pd.to_datetime(df.open_time, unit="ms", utc=True)
    df["symbol"] = symbol; df["interval"] = interval
    return df.drop_duplicates("open_time")

frames = []
for sym in ["BTCUSDT", "ETHUSDT", "USDCUSDT"]:
    for a, b in [("2025-10-03 00:00", "2025-10-15 00:00"), ("2026-01-22 00:00", "2026-02-10 00:00")]:
        df = klines(sym, "1m", ms(a), ms(b)); print(sym, a, len(df)); frames.append(df)
    df = klines(sym, "1d", ms("2025-09-01 00:00"), ms("2026-10-23 00:00")); print(sym, "1d", len(df)); frames.append(df)
    df = klines(sym, "1h", ms("2025-09-01 00:00"), ms("2026-10-23 00:00")); print(sym, "1h", len(df)); frames.append(df)
pd.concat(frames).to_parquet(OUT)
print("saved", OUT)
