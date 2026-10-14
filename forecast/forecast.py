"""Live next-bar forecasts for the portfolio's Forecast Lab.

A lean, cloud-runnable port of the stock-forecast-intraday research system:
pooled cross-sectional models (ridge + logistic regression) on volatility-scaled
returns, own/market/peer features, walk-forward validation, a forward-test log
that scores every published forecast once its bar has closed.

Run:  python forecast.py --out <dir>      writes <dir>/forecast.json and <dir>/history.json
Analysis only. Not investment advice.
"""
import argparse, json, math, os, warnings, datetime as dt
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd, yfinance as yf
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.preprocessing import StandardScaler
warnings.filterwarnings("ignore")

NY = ZoneInfo("America/New_York")
NAMES = {"NVDA": "NVIDIA", "AMD": "AMD", "TSLA": "Tesla", "GOOGL": "Alphabet", "META": "Meta Platforms",
         "ORCL": "Oracle", "MU": "Micron", "INTC": "Intel", "PANW": "Palo Alto Networks", "SNOW": "Snowflake",
         "SMCI": "Super Micro", "DELL": "Dell", "SNDK": "SanDisk", "NBIS": "Nebius", "SPCX": "SpaceX", "MRNA": "Moderna"}
TICKERS = list(NAMES)
MARKET = ["SPY", "QQQ"]
# period: Yahoo history; vw: vol window (bars); train/test/every: walk-forward days; bar: minutes
H = {"5m": dict(iv="5m", period="60d", vw=48, train=10, test=20, every=2, bar=5, label="Next 5 min"),
     "1h": dict(iv="1h", period="730d", vw=35, train=60, test=120, every=10, bar=60, label="Next hour"),
     "1d": dict(iv="1d", period="5y", vw=20, train=500, test=250, every=20, bar=390, label="Next session")}
FEATS = ["z0", "z1", "z2", "z3", "z4", "c5", "c20", "vr", "ma", "rg", "vz", "spy", "qqq", "peer", "rel", "tod"]


def download(h):
    c = H[h]
    d = yf.download(TICKERS + MARKET, period=c["period"], interval=c["iv"], progress=False,
                    auto_adjust=True, group_by="ticker", prepost=False, threads=True)
    syms = [s for s in TICKERS + MARKET if s in d.columns.get_level_values(0)]
    W = {f: pd.DataFrame({s: d[s][f] for s in syms}) for f in ["Open", "High", "Low", "Close", "Volume"]}
    idx = W["Close"].index
    if h != "1d":
        idx = idx.tz_convert(NY) if idx.tz is not None else idx.tz_localize("UTC").tz_convert(NY)
    else:
        idx = pd.DatetimeIndex(idx.date).tz_localize(NY) + pd.Timedelta(hours=16)   # daily bar "ends" at the close
    for f in W:
        W[f].index = idx
    return W


def completed(W, h, now):
    """Drop the bar that is still forming (Yahoo returns it with a partial close)."""
    idx = W["Close"].index
    if h == "1d":
        done = idx <= now
    else:
        done = idx + pd.Timedelta(minutes=H[h]["bar"]) <= now + pd.Timedelta(seconds=30)
    return {f: x[done] for f, x in W.items()}


def panel(W, h):
    c = H[h]
    C = W["Close"]
    r = np.log(C).diff()
    vol = r.rolling(c["vw"], min_periods=10).std()
    z = (r / vol).clip(-8, 8)
    peers = [t for t in TICKERS if t in z]
    pmean = z[peers].mean(axis=1)
    day = pd.Series(C.index.date, index=C.index)
    tod = ((C.index.hour * 60 + C.index.minute - 570) / 390.0) if h != "1d" else np.zeros(len(C))
    rows = []
    for t in peers:
        f = pd.DataFrame(index=C.index)
        for k in range(5):
            f[f"z{k}"] = z[t].shift(k)
        f["c5"] = z[t].rolling(5).sum() / math.sqrt(5)
        f["c20"] = z[t].rolling(20).sum() / math.sqrt(20)
        f["vr"] = (r[t].rolling(5).std() / vol[t]).clip(0, 5)
        f["ma"] = ((C[t] / C[t].rolling(20).mean() - 1) / vol[t]).clip(-10, 10)
        f["rg"] = (np.log(W["High"][t] / W["Low"][t]) / vol[t]).clip(0, 10)
        lv = np.log1p(W["Volume"][t])
        f["vz"] = ((lv - lv.rolling(20).mean()) / lv.rolling(20).std()).clip(-5, 5)
        f["spy"] = z["SPY"] if "SPY" in z else 0.0
        f["qqq"] = z["QQQ"] if "QQQ" in z else 0.0
        f["peer"] = (pmean * len(peers) - z[t]) / (len(peers) - 1)
        f["rel"] = z[t] - f["peer"]
        f["tod"] = tod
        nxt = r[t].shift(-1)
        same = (day.shift(-1) == day) if h != "1d" else pd.Series(True, index=C.index)
        f["y"] = (nxt / vol[t]).where(same).clip(-6, 6)
        f["ret_next"] = nxt.where(same)
        f["vol"] = vol[t]
        f["close"] = C[t]
        f["ticker"] = t
        f["day"] = day.values
        rows.append(f)
    P = pd.concat(rows).reset_index(names="ts")
    return P.dropna(subset=FEATS + ["vol", "close"])


class Model:
    def __init__(self, D):
        self.sc = StandardScaler().fit(D[FEATS])
        X = self.sc.transform(D[FEATS])
        self.ridge = Ridge(alpha=50.0).fit(X, D["y"])
        self.logit = LogisticRegression(C=0.05, max_iter=500).fit(X, (D["y"] > 0).astype(int))

    def predict(self, D):
        X = self.sc.transform(D[FEATS])
        zr = self.ridge.predict(X)
        p_r = 0.5 * (1 + np.vectorize(math.erf)(zr / math.sqrt(2)))
        p_l = self.logit.predict_proba(X)[:, 1]
        return 0.5 * (p_r + p_l), zr


def walk_forward(P, h):
    """Rolling-window retrain over the last `test` days; scores direction on bars never seen in training."""
    c = H[h]
    L = P.dropna(subset=["y"])
    L = L[L["y"] != 0]
    days = sorted(L["day"].unique())
    test_days = days[-c["test"]:]
    hits, n, up, mom = 0, 0, 0, 0
    daily = []
    for i in range(0, len(test_days), c["every"]):
        block = test_days[i:i + c["every"]]
        start = days.index(block[0])
        train_days = days[max(0, start - c["train"]):start]
        if len(train_days) < max(5, c["train"] // 4):
            continue
        tr = L[L["day"].isin(train_days)]
        te = L[L["day"].isin(block)]
        if len(tr) < 500 or te.empty:
            continue
        p, _ = Model(tr).predict(te)
        ok = (p > 0.5) == (te["y"] > 0)
        hits += int(ok.sum()); n += len(te)
        up += int((te["y"] > 0).sum())
        mom += int(((te["z0"] > 0) == (te["y"] > 0)).sum())
        for d_, g in pd.DataFrame({"d": te["day"].values, "ok": ok.values}).groupby("d"):
            daily.append([str(d_), round(float(g["ok"].mean()), 4), int(len(g))])
    if not n:
        return None
    hit = hits / n
    se = math.sqrt(0.25 / n)
    return {"hit": round(hit, 4), "n": n, "days": len(daily), "z": round((hit - 0.5) / se, 2),
            "always_up": round(up / n, 4), "momentum": round(mom / n, 4), "daily": daily[-60:]}


def market_status(now):
    ny = now.astimezone(NY)
    if ny.weekday() >= 5:
        return False, "Weekend: market closed"
    m = ny.hour * 60 + ny.minute
    if 570 <= m < 960:
        return True, "US market open"
    return False, "Pre-market" if m < 570 else "After hours"


def score_history(hist, closes):
    """Mark every unscored forecast whose next bar has now closed."""
    for e in hist:
        if e.get("ok") is not None:
            continue
        s = closes.get(e["h"], {}).get(e["t"])
        if s is None:
            continue
        ts = pd.Timestamp(e["ts"])
        after = s[s.index > ts]
        if after.empty:
            continue
        nts = after.index[0]
        if e["h"] != "1d" and nts.date() != ts.date():
            e["ok"] = "x"          # bar crossed the close: not scored
            continue
        move = float(after.iloc[0]) - e["last"]
        if move == 0:
            e["ok"] = "x"; continue
        e["ok"] = int((move > 0) == (e["p"] > 0.5))
        e["actual"] = round(float(after.iloc[0]), 4)
    return hist


def live_stats(hist, now):
    out = {}
    for h in H:
        sc = [e for e in hist if e["h"] == h and e.get("ok") in (0, 1)]
        wk = [e for e in sc if pd.Timestamp(e["ts"]) > now - pd.Timedelta(days=7)]
        out[h] = {"hit": round(sum(e["ok"] for e in sc) / len(sc), 4) if sc else None, "n": len(sc),
                  "hit7": round(sum(e["ok"] for e in wk) / len(wk), 4) if wk else None, "n7": len(wk),
                  "since": min((e["ts"] for e in sc), default=None)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    ap.add_argument("--force-backtest", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    fp, hp = os.path.join(a.out, "forecast.json"), os.path.join(a.out, "history.json")
    prev = json.load(open(fp)) if os.path.exists(fp) else {}
    hist = json.load(open(hp)) if os.path.exists(hp) else []
    now = pd.Timestamp.now(tz=NY)
    is_open, status = market_status(now)
    today = str(now.date())

    rows = {t: {"name": NAMES[t], "f": {}} for t in TICKERS}
    horizons, closes = {}, {}
    for h in H:
        W = completed(download(h), h, now)
        closes[h] = {t: W["Close"][t].dropna() for t in TICKERS if t in W["Close"]}
        P = panel(W, h)
        bt = prev.get("horizons", {}).get(h, {}).get("backtest")
        if a.force_backtest or not bt or bt.get("date") != today:
            bt = walk_forward(P, h) or {}
            bt["date"] = today
        L = P.dropna(subset=["y"])
        L = L[L["y"] != 0]
        days = sorted(L["day"].unique())
        model = Model(L[L["day"].isin(days[-H[h]["train"]:])])
        last = P.sort_values("ts").groupby("ticker").tail(1)
        p, zr = model.predict(last)
        asof = None
        for (_, r_), pi, zi in zip(last.iterrows(), p, zr):
            t, px, sig = r_["ticker"], float(r_["close"]), float(r_["vol"])
            rows[t]["f"][h] = {"p": round(float(pi), 4), "ret": round(float(zi * sig), 6), "sigma": round(sig, 6),
                               "px": round(px * math.exp(zi * sig), 4), "lo": round(px * math.exp(-sig), 4),
                               "hi": round(px * math.exp(sig), 4), "last": round(px, 4), "ts": r_["ts"].isoformat()}
            asof = max(asof or r_["ts"], r_["ts"])
            key = (h, t, r_["ts"].isoformat())
            if not any((e["h"], e["t"], e["ts"]) == key for e in hist[-2000:]):
                hist.append({"h": h, "t": t, "ts": r_["ts"].isoformat(), "p": round(float(pi), 4), "last": round(px, 4), "ok": None})
        horizons[h] = {"label": H[h]["label"], "asof": asof.isoformat() if asof is not None else None, "backtest": bt}

    # intraday sparkline + day change from the 5-minute bars
    for t in TICKERS:
        s = closes["5m"].get(t)
        d = closes["1d"].get(t)
        if s is None or s.empty:
            continue
        last_day = s.index[-1].date()
        td = s[s.index.date == last_day]
        prev_close = d[d.index.date < last_day].iloc[-1] if d is not None and (d.index.date < last_day).any() else td.iloc[0]
        rows[t]["px"] = round(float(td.iloc[-1]), 4)
        rows[t]["prev_close"] = round(float(prev_close), 4)
        rows[t]["chg"] = round(float(td.iloc[-1] / prev_close - 1), 5)
        rows[t]["spark"] = [[int(i.timestamp() * 1000), round(float(v), 3)] for i, v in td.items()]
        rows[t]["session"] = str(last_day)

    hist = score_history(hist, closes)[-6000:]
    recent = [e for e in reversed(hist) if e.get("ok") in (0, 1)][:40]
    out = {"updated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "market": {"open": is_open, "status": status, "ny_time": now.strftime("%H:%M ET")},
           "models": "Ensemble of ridge regression and L2-regularised logistic regression, pooled across 16 stocks",
           "horizons": horizons, "live": live_stats(hist, now), "recent": recent, "rows": rows,
           "disclaimer": "Research demo. Forecasts are statistical estimates, not investment advice."}
    json.dump(out, open(fp, "w"), separators=(",", ":"))
    json.dump(hist, open(hp, "w"), separators=(",", ":"))
    for h in H:
        b = horizons[h]["backtest"]
        print(h, "walk-forward hit", b.get("hit"), "n", b.get("n"), "z", b.get("z"), "| live", out["live"][h])


if __name__ == "__main__":
    main()
