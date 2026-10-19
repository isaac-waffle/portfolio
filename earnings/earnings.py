"""Data feed for the portfolio's Earnings Desk.

Builds a static JSON API: the US earnings calendar (Nasdaq), a watchlist overview, and one
detail file per covered ticker (company profile, price history with indicators, past earnings
reactions, options-implied move, valuation, analyst view, news and recent moves).

Run:  python earnings.py --out <dir>      writes <dir>/calendar.json and <dir>/t/<SYM>.json
Analysis only. Not investment advice.
"""
import argparse, json, math, os, time, warnings, datetime as dt
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd, requests, yfinance as yf
warnings.filterwarnings("ignore")

NY = ZoneInfo("America/New_York")
HERE = os.path.dirname(os.path.abspath(__file__))
WATCHLIST = [
    "VIST", "BA", "GE", "AAPL", "MCD", "NKE", "STLD", "QNT", "BTC-USD", "IBM", "ASML", "NVDA",
    "SPCX", "AMZN", "MSFT", "TTWO", "TSLA", "TSM", "GOOGL", "RBLX", "KARO", "QCOM", "META", "AVGO",
    "DELL", "ORCL", "AMAT", "LRCX", "MRVL", "MU", "INTC", "AMD", "BE", "SKHY", "U", "SNDK",
    "WDC", "ARM", "CRWD", "PLTR", "STX", "PANW", "PENG", "MRNA", "SNOW", "IONQ", "SMCI", "IREN",
    "CRWV", "NBIS",
]
CAL_DAYS = 14          # calendar window
DETAIL_DAYS, DETAIL_MIN_CAP = 10, 5e10  # non-watchlist names reporting this soon and this big also get a detail page
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/126 Safari/537.36"}


def clean(x, nd=4):
    """JSON-safe scalar: NaN/inf -> None, numpy -> python, floats rounded."""
    if x is None:
        return None
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return None if not math.isfinite(float(x)) else round(float(x), nd)
    return x


def cap_num(s):
    try:
        return float(str(s).replace("$", "").replace(",", ""))
    except Exception:
        return None


# ───────────────────────── calendar ─────────────────────────
def nasdaq_calendar(start, days):
    rows = []
    for i in range(days):
        d = start + dt.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        for attempt in range(3):
            try:
                r = requests.get(f"https://api.nasdaq.com/api/calendar/earnings?date={d}", headers=UA, timeout=20)
                for row in (r.json().get("data") or {}).get("rows") or []:
                    rows.append({
                        "date": str(d), "sym": row["symbol"].strip().replace("/", "-").replace(".", "-"),
                        "name": row.get("name"), "mc": cap_num(row.get("marketCap")),
                        "timing": {"time-pre-market": "bmo", "time-after-hours": "amc"}.get(row.get("time"), ""),
                        "eps": row.get("epsForecast") or None, "ly": row.get("lastYearEPS") or None,
                        "n": row.get("noOfEsts") or None, "fq": row.get("fiscalQuarterEnding"),
                    })
                break
            except Exception:
                time.sleep(2 + attempt * 3)
    return rows


# ───────────────────────── per-ticker detail ─────────────────────────
def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def reactions(hist, ed):
    """Each past report: reaction day = same day for pre-market, next session for after-close."""
    if ed is None or ed.empty or hist.empty:
        return []
    h = hist.copy()
    h.index = h.index.tz_localize(None).normalize()
    dates, out = h.index, []
    for ts, r in ed.iterrows():
        if pd.isna(r.get("Reported EPS")):
            continue
        local = ts.tz_convert(NY)
        d = pd.Timestamp(local.date())
        amc = local.hour >= 12
        pos = dates.searchsorted(d)
        if pos >= len(dates) or dates[pos] != d or (not amc and pos == 0):
            continue
        base_i, react_i = (pos, pos + 1) if amc else (pos - 1, pos)
        if react_i >= len(dates):
            continue
        base = h["Close"].iloc[base_i]
        sur = r.get("Surprise(%)")
        out.append({
            "date": str(local.date()), "timing": "amc" if amc else "bmo",
            "est": clean(r.get("EPS Estimate")), "act": clean(r.get("Reported EPS")),
            "sur": clean(sur / 100) if pd.notna(sur) else None,
            "gap": clean(h["Open"].iloc[react_i] / base - 1),
            "day": clean(h["Close"].iloc[react_i] / base - 1),
            "w5": clean(h["Close"].iloc[min(react_i + 4, len(dates) - 1)] / base - 1),
        })
    return out


def implied_move(t, earn_date, price):
    if not earn_date or not price:
        return None
    try:
        exps = [e for e in t.options if dt.date.fromisoformat(e) > earn_date]
        if not exps:
            return None
        ch = t.option_chain(exps[0])
        k = ch.calls.iloc[(ch.calls["strike"] - price).abs().argsort()[:1]]["strike"].iloc[0]

        def mid(df):
            r = df[df["strike"] == k].iloc[0]
            return float((r["bid"] + r["ask"]) / 2 if r["bid"] > 0 and r["ask"] > 0 else r["lastPrice"])

        straddle = mid(ch.calls) + mid(ch.puts)
        return {"expiry": exps[0], "strike": clean(k), "straddle": clean(straddle), "move": clean(straddle / price)}
    except Exception:
        return None


def google_news(query, n=12):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query + " when:14d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    try:
        root = ET.fromstring(requests.get(url, headers=UA, timeout=15).content)
    except Exception:
        return []
    items = []
    for it in root.iter("item"):
        try:
            when = parsedate_to_datetime(it.findtext("pubDate"))
        except Exception:
            continue
        items.append({"t": it.findtext("title"), "u": it.findtext("link"), "s": it.findtext("source") or "",
                      "d": when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")})
    items.sort(key=lambda x: x["d"], reverse=True)
    return items[:n]


def detail(sym, ko, spy_1y, timing_hint):
    t = yf.Ticker(sym)
    hist = t.history(period="5y", auto_adjust=False)
    if hist.empty:
        return None
    try:
        info = t.info or {}
    except Exception:
        info = {}
    crypto = sym.endswith("-USD")
    ed = None if crypto else _try(lambda: t.get_earnings_dates(limit=24))
    cal = {} if crypto else (_try(lambda: t.calendar) or {})
    rec = None if crypto else _try(lambda: t.recommendations)
    q = None if crypto else _try(lambda: t.quarterly_income_stmt)
    intra = _try(lambda: t.history(period="5d", interval="15m"))

    close = hist["Close"]
    price = clean(info.get("regularMarketPrice") or info.get("currentPrice") or close.iloc[-1])
    prev = clean(info.get("regularMarketPreviousClose") or close.iloc[-2])
    ma50, ma200, r14 = close.rolling(50).mean(), close.rolling(200).mean(), rsi(close)
    tail = hist.tail(504)
    ohlc = [[d.strftime("%Y-%m-%d"), clean(r.Open, 3), clean(r.High, 3), clean(r.Low, 3), clean(r.Close, 3), int(r.Volume),
             clean(ma50.loc[d], 3), clean(ma200.loc[d], 3), clean(r14.loc[d], 1)] for d, r in tail.iterrows()]

    def ret(n):
        return clean(close.iloc[-1] / close.iloc[-1 - n] - 1) if len(close) > n else None

    prior = close[close.index.year < close.index[-1].year]
    next_ed = (cal.get("Earnings Date") or [None])[0]
    # exact report time when Yahoo has one for that same date (UTC); it also fills in a missing timing
    ts, ets = None, info.get("earningsTimestampStart") or info.get("earningsTimestamp")
    if next_ed and ets:
        when = dt.datetime.fromtimestamp(ets, dt.timezone.utc)
        if when.astimezone(NY).date() == next_ed:
            ts = when.strftime("%Y-%m-%dT%H:%MZ")
            timing_hint = timing_hint or ("bmo" if when.astimezone(NY).hour < 12 else "amc")
    mc = info.get("marketCap")
    if not mc:
        try:
            mc = t.fast_info["marketCap"]
        except Exception:
            mc = None

    quarterly = []
    if q is not None and not q.empty and "Total Revenue" in q.index:
        for col in sorted(q.columns)[-8:]:
            if pd.isna(q.loc["Total Revenue", col]):
                continue
            quarterly.append([col.strftime("%Y-%m"), clean(q.loc["Total Revenue", col], 0),
                              clean(q.loc["Net Income", col], 0) if "Net Income" in q.index else None])

    dist = None
    if rec is not None and not rec.empty:
        r0 = rec.iloc[0]
        dist = [int(r0[k]) for k in ["strongBuy", "buy", "hold", "sell", "strongSell"]]

    short = (info.get("shortName") or sym).split(",")[0].replace(" Inc.", "").replace(" Corporation", "")
    I = lambda k, nd=4: clean(info.get(k), nd)
    return {
        "sym": sym, "name": info.get("longName") or info.get("shortName") or sym, "crypto": crypto,
        "sector": info.get("sectorDisp") or info.get("sector"), "industry": info.get("industryDisp") or info.get("industry"),
        "country": info.get("country"), "employees": info.get("fullTimeEmployees"), "website": info.get("website"),
        "exchange": info.get("fullExchangeName"), "summary": info.get("longBusinessSummary"), "ko": ko.get(sym),
        "price": price, "prev": prev, "mc": clean(mc, 0),
        "next": {"date": str(next_ed) if next_ed else None, "timing": timing_hint, "ts": ts,
                 "estimated": bool(info.get("isEarningsDateEstimate")),
                 "eps_avg": clean(cal.get("Earnings Average")), "eps_lo": clean(cal.get("Earnings Low")),
                 "eps_hi": clean(cal.get("Earnings High")), "rev_avg": clean(cal.get("Revenue Average"), 0),
                 "rev_lo": clean(cal.get("Revenue Low"), 0), "rev_hi": clean(cal.get("Revenue High"), 0)},
        "fin": {"rev": I("totalRevenue", 0), "rev_g": I("revenueGrowth"), "eps_g": I("earningsGrowth"),
                "gm": I("grossMargins"), "om": I("operatingMargins"), "pm": I("profitMargins"),
                "roe": I("returnOnEquity"), "fcf": I("freeCashflow", 0)},
        "val": {"pe": I("trailingPE", 2), "fpe": I("forwardPE", 2), "peg": clean(info.get("trailingPegRatio") or info.get("pegRatio"), 2),
                "ps": I("priceToSalesTrailing12Months", 2), "pb": I("priceToBook", 2), "ev_ebitda": I("enterpriseToEbitda", 2),
                "eps": I("trailingEps", 2), "div": I("dividendYield", 2)},
        "health": {"cash": I("totalCash", 0), "debt": I("totalDebt", 0), "de": I("debtToEquity", 1), "cr": I("currentRatio", 2),
                   "short": I("shortPercentOfFloat"), "short_days": I("shortRatio", 1),
                   "inst": I("heldPercentInstitutions"), "insider": I("heldPercentInsiders"),
                   "beta": I("beta", 2), "avg_vol": I("averageVolume", 0)},
        "analyst": {"mean": I("targetMeanPrice", 2), "lo": I("targetLowPrice", 2), "hi": I("targetHighPrice", 2),
                    "n": info.get("numberOfAnalystOpinions"), "key": info.get("recommendationKey"), "dist": dist},
        "ret": {"1w": ret(5), "1m": ret(21), "3m": ret(63), "6m": ret(126),
                "ytd": clean(close.iloc[-1] / prior.iloc[-1] - 1) if len(prior) else None, "1y": ret(252), "spy1y": spy_1y},
        "vol3m": clean(close.pct_change().tail(63).std() * math.sqrt(252)),
        "hi52": clean(close.tail(252).max(), 3), "lo52": clean(close.tail(252).min(), 3),
        "ohlc": ohlc,
        "reactions": reactions(hist, ed),
        "implied": implied_move(t, next_ed, price),
        "quarterly": quarterly,
        "intraday": [] if intra is None or intra.empty else
                    [[d.tz_convert(NY).strftime("%Y-%m-%d %H:%M"), clean(c, 3)] for d, c in intra["Close"].items()],
        "news": google_news(f'"{sym.replace("-USD", "")}" OR "{short}" stock'),
    }


def _try(f):
    try:
        return f()
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    ap.add_argument("--only", nargs="*", help="limit detail files to these tickers (for local testing)")
    a = ap.parse_args()
    os.makedirs(os.path.join(a.out, "t"), exist_ok=True)
    ko = json.load(open(os.path.join(HERE, "ko_profiles.json"), encoding="utf-8"))

    today = dt.datetime.now(NY).date()
    cal = nasdaq_calendar(today, CAL_DAYS)
    timing = {r["sym"]: r["timing"] for r in cal}
    soon = str(today + dt.timedelta(days=DETAIL_DAYS))
    upcoming = sorted({r["sym"] for r in cal if (r["mc"] or 0) >= DETAIL_MIN_CAP and r["date"] <= soon} - set(WATCHLIST))
    covered = a.only or (WATCHLIST + upcoming)

    spy = _try(lambda: yf.Ticker("SPY").history(period="2y")["Close"])
    spy_1y = clean(spy.iloc[-1] / spy.iloc[-253] - 1) if spy is not None and len(spy) > 253 else None

    def one(sym):
        for attempt in range(2):
            try:
                d = detail(sym, ko, spy_1y, timing.get(sym, ""))
                if d:
                    with open(os.path.join(a.out, "t", f"{sym}.json"), "w", encoding="utf-8") as f:
                        json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
                return d
            except Exception as e:
                print(f"{sym}: {e}")
                time.sleep(3)
        return None

    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(one, covered))

    watch = []
    for sym, d in zip(covered, results):
        if d and sym in WATCHLIST:
            watch.append({"sym": sym, "name": d["name"], "price": d["price"],
                          "chg": clean(d["price"] / d["prev"] - 1) if d["price"] and d["prev"] else None,
                          "mc": d["mc"], "next": d["next"]["date"], "timing": d["next"]["timing"], "ts": d["next"]["ts"],
                          "eps": d["next"]["eps_avg"]})
    # a ticker that failed this run keeps the file restored from the previous run
    ok = sorted(s for s in covered if os.path.exists(os.path.join(a.out, "t", f"{s}.json")))
    if not cal and os.path.exists(os.path.join(a.out, "calendar.json")):
        cal = json.load(open(os.path.join(a.out, "calendar.json"), encoding="utf-8"))["calendar"]
    out = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "today": str(today),
           "watch": watch, "calendar": cal, "covered": ok}
    with open(os.path.join(a.out, "calendar.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"calendar rows {len(cal)} · detail files {len(ok)}/{len(covered)}")


if __name__ == "__main__":
    main()
