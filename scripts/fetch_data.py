#!/usr/bin/env python3
"""Fetch market data from Yahoo Finance and write data.json for the dashboard.

Standard library only, so it runs on a bare GitHub Actions runner.
Usage: python3 scripts/fetch_data.py [output_path]
"""
import datetime as dt
import json
import sys
import time
import urllib.request

UA = "Mozilla/5.0"
BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"

# key -> Yahoo symbol
SYMBOLS = {
    "IXIC": "^IXIC",
    "SOX": "^SOX",
    "QLD": "QLD",
    "SOXL": "SOXL",
    "VIX": "^VIX",
    "USDKRW": "KRW=X",
}
# Decline size that starts a buying cycle (used for the sell rules' reference peak)
CYCLE_DD = {"IXIC": 20, "SOX": 30}


def get_json(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise last


def chart(symbol, params):
    url = BASE + urllib.request.quote(symbol) + "?" + params
    res = get_json(url)["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    rows = []
    for i, t in enumerate(res.get("timestamp") or []):
        c = q["close"][i]
        if c is None:
            continue
        rows.append({
            "t": t,
            "h": q["high"][i] if q["high"][i] is not None else c,
            "l": q["low"][i] if q["low"][i] is not None else c,
            "c": c,
        })
    return res["meta"], rows


def sma(values, n):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def rsi(values, n=14):
    """Wilder's RSI, the same formula most charting apps use."""
    if len(values) <= n:
        return None
    gain = loss = 0.0
    for i in range(1, n + 1):
        d = values[i] - values[i - 1]
        gain += max(d, 0)
        loss += max(-d, 0)
    gain /= n
    loss /= n
    for i in range(n + 1, len(values)):
        d = values[i] - values[i - 1]
        gain = (gain * (n - 1) + max(d, 0)) / n
        loss = (loss * (n - 1) + max(-d, 0)) / n
    if loss == 0:
        return 100.0
    return 100 - 100 / (1 + gain / loss)


def r(x, nd=2):
    return None if x is None else round(x, nd)


def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat()


def build(key, symbol):
    meta, daily = chart(symbol, "range=5y&interval=1d&includePrePost=false")
    closes = [x["c"] for x in daily]
    price = meta.get("regularMarketPrice") or closes[-1]
    # Make the last bar reflect the live price so indicators are intraday-fresh
    if closes:
        closes[-1] = price
    out = {
        "symbol": symbol,
        "price": r(price),
        "prevClose": r(meta.get("chartPreviousClose") if len(closes) < 2 else closes[-2]),
        "marketTime": iso(meta["regularMarketTime"]) if meta.get("regularMarketTime") else None,
        "exchangeTz": meta.get("exchangeTimezoneName"),
    }
    if out["prevClose"]:
        out["changePct"] = r((price / out["prevClose"] - 1) * 100)

    # Recent daily closes for the page's charts (dates as YYYY-MM-DD)
    span = 260 if key in ("IXIC", "SOX") else 90
    out["series"] = {
        "d": [iso(x["t"])[:10] for x in daily[-span:]],
        "c": [round(v, 2) for v in closes[-span:]],
    }

    if key == "USDKRW" or key == "VIX":
        for n in (5, 20):
            if len(closes) > n:
                out[f"chg{n}"] = r(price - closes[-1 - n])
                out[f"chg{n}Pct"] = r((price / closes[-1 - n] - 1) * 100)
        out["ma20"] = r(sma(closes, 20))
        return out

    # All-time high from monthly bars (small payload, full history)
    _, monthly = chart(symbol, "range=max&interval=1mo")
    ath_bar = max(monthly, key=lambda x: x["h"])
    recent_hi = max(daily, key=lambda x: x["h"])  # monthly highs can lag the current month
    if recent_hi["h"] >= ath_bar["h"]:
        ath, ath_t = recent_hi["h"], recent_hi["t"]
    else:
        ath, ath_t = ath_bar["h"], ath_bar["t"]
    # Pin the ATH date to the actual day when it is inside the daily window
    for x in daily:
        if abs(x["h"] - ath) < 1e-9:
            ath_t = x["t"]
            break
    out["ath"] = r(ath)
    out["athDate"] = iso(ath_t)[:10]
    out["ddPct"] = r((price / ath - 1) * 100)

    # Lowest close since the ATH (this decline's low so far)
    since = [x for x in daily if x["t"] >= ath_t]
    if since:
        low = min(since, key=lambda x: x["c"])
        low_c = min(low["c"], price)
        out["cycleLow"] = r(low_c)
        out["cycleLowDate"] = iso(low["t"])[:10] if low["c"] <= price else iso(meta["regularMarketTime"])[:10]
        out["cycleLowPct"] = r((low_c / ath - 1) * 100)

    # Reference peak of the latest buying cycle (closing basis, as in the backtest):
    # the running closing high at the start of the most recent decline of CYCLE_DD % or more.
    if key in CYCLE_DD:
        th = CYCLE_DD[key]
        first_t = daily[0]["t"]
        run_max = max([x["c"] for x in monthly if x["t"] < first_t] or [daily[0]["c"]])
        run_t = None
        ref = ref_t = deep_t = None
        for x in daily:
            if x["c"] > run_max:
                run_max, run_t = x["c"], x["t"]
            if x["c"] / run_max - 1 <= -th / 100:
                if ref is None or run_max != ref:
                    ref, ref_t = run_max, run_t
                deep_t = x["t"]
        if ref is not None:
            out["refPeak"] = r(ref)
            out["refPeakDate"] = iso(ref_t)[:10] if ref_t else None
            out["refDdPct"] = r((price / ref - 1) * 100)
            out["deepDate"] = iso(deep_t)[:10]
            # Has the index made a new closing high since the cycle's deepest phase?
            out["recoveredSinceDeep"] = any(x["c"] > ref for x in daily if x["t"] > deep_t) or price > ref
            # Highest close since the deepest phase, relative to the reference peak
            after = [x["c"] for x in daily if x["t"] > deep_t] + [price]
            out["refMaxPct"] = r((max(after) / ref - 1) * 100)

    for n in (20, 50, 60, 200):
        m = sma(closes, n)
        out[f"ma{n}"] = r(m)
        out[f"disp{n}"] = r(price / m * 100, 1) if m else None
    out["rsi14"] = r(rsi(closes), 1)

    # 10-month moving average on completed month-end closes, built from daily bars
    # (Yahoo's monthly series duplicates the current month, so it is not used here)
    month_last = {}
    for x in daily[:-1] + [dict(daily[-1], c=price)]:
        month_last[iso(x["t"])[:7]] = x["c"]
    cur_month = iso(daily[-1]["t"])[:7]
    months = sorted(m for m in month_last if m != cur_month)
    mcloses = [month_last[m] for m in months]
    if len(mcloses) >= 11:
        m10 = sum(mcloses[-10:]) / 10
        prev10 = sum(mcloses[-11:-1]) / 10
        out["ma10m"] = r(m10)
        out["lastMonthDate"] = months[-1]
        out["lastMonthClose"] = r(mcloses[-1])
        out["lastMonthAbove10m"] = mcloses[-1] > m10
        out["prevMonthAbove10m"] = mcloses[-2] > prev10
        # A fresh month-end break: last month closed below, the month before closed above
        out["fresh10mBreak"] = (not out["lastMonthAbove10m"]) and out["prevMonthAbove10m"]
        # Where this month would close relative to a 10-month average including it
        cur10 = (sum(mcloses[-9:]) + price) / 10
        out["ma10mLive"] = r(cur10)
        out["priceAbove10m"] = price > cur10
    return out


# CNN Fear & Greed: the JSON feed CNN's own page reads. CNN rejects obvious bots, so send a
# normal browser User-Agent. If it fails, the previous value in data.json is kept.
CNN_FG = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


def fear_greed():
    req = urllib.request.Request(CNN_FG, headers={
        "User-Agent": BROWSER_UA, "Accept": "application/json",
        "Referer": "https://edition.cnn.com/markets/fear-and-greed", "Origin": "https://edition.cnn.com"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        fg = json.load(resp)["fear_and_greed"]
    out = {"score": round(float(fg["score"]), 1), "rating": fg.get("rating"), "timestamp": fg.get("timestamp"), "source": "CNN"}
    for k_src, k_dst in (("previous_close", "prevClose"), ("previous_1_week", "prev1w"), ("previous_1_month", "prev1m")):
        if fg.get(k_src) is not None:
            out[k_dst] = round(float(fg[k_src]), 1)
    return out


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "data.json"
    data = {"updatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "source": "Yahoo Finance", "symbols": {}, "errors": []}
    for key, sym in SYMBOLS.items():
        try:
            data["symbols"][key] = build(key, sym)
        except Exception as e:  # noqa: BLE001
            data["errors"].append(f"{key}: {e}")
    try:
        data["fg"] = fear_greed()
    except Exception as e:  # noqa: BLE001
        data["errors"].append(f"FG: {e}")
    # Keep the previous good values when something fails, so the page never goes blank
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
        if "fg" not in data and old.get("fg"):
            data["fg"] = dict(old["fg"], stale=True)
        for key in SYMBOLS:
            if key not in data["symbols"] and key in old.get("symbols", {}):
                data["symbols"][key] = dict(old["symbols"][key], stale=True)
    except (OSError, ValueError):
        pass
    if not data["symbols"]:
        print("no data fetched:", data["errors"], file=sys.stderr)
        sys.exit(1)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("wrote", path, "symbols:", ", ".join(data["symbols"]), "errors:", data["errors"] or "none")


if __name__ == "__main__":
    main()
