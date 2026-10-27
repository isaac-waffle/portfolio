"""가상 트레이더 백테스트·튜닝 — traders.py의 판단 코드(Trader.step)를 과거 5분봉으로 그대로 재생.

  .venv/bin/python apps/traders_lab.py prep [2026-01-01]          # 5분봉 받고 시점별 신호 미리 계산 (캐시)
      # Alpaca 키(ALPACA_KEY_ID·ALPACA_SECRET_KEY, 환경변수 또는 ~/.trading_desk.env)가 있으면 시작일부터 SIP 5분봉,
      # 없으면 Yahoo (최근 60거래일까지만)
  .venv/bin/python apps/traders_lab.py run 2026-09-01 2026-10-23  # 지금 규칙으로 시뮬
  .venv/bin/python apps/traders_lab.py tune 학습시작:끝 확인시작:끝 최종시험시작:끝 [--from-v1]
      # 학습 → 변경마다 확인 기간에서도 나빠지지 않는지 → 튜닝에 안 쓴 최종 시험 기간에서 나빠지면 적용 안 함
      # --from-v1: 처음 규칙(v1)에서 출발 (v2는 8~10월을 보고 정한 거라, 그 기간을 시험에 쓰려면 v1에서 출발해야 공정)

실시간과 다른 점: 5분마다 판단(실시간은 30초), 손절·익절도 5분봉으로 확인(같은 봉에 둘 다 닿으면 손절),
스캐너가 찾는 '새로 발견' 종목은 과거를 재현할 수 없어 관심종목만 씀. 결과: apps/journal/traders/lab/
"""
from __future__ import annotations

import datetime as dt
import gc
import json
import multiprocessing as mp
import os
import pickle
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from pathlib import Path

import traders as T

warnings.filterwarnings("ignore")
LAB = T.DATA / "lab"
FIRST, LAST, STEP = 575, 955, 5  # 판단 시각 (ET 분)

# 튜닝할 값과 후보 — 성격이 바뀌지 않도록 트레이더별 범위를 제한
SPACE = {
    "agg": dict(min_score=[45, 50, 55, 60, 65, 70], min_rvol=[1.0, 1.2, 1.5, 2.0, 2.5], min_rr=[1.0, 1.2, 1.5, 2.0],
                min_mkt=[25, 35, 45], time_stop=[30, 45, 60, 90], trail=["ema9", "ema20"],
                trend_w=[0, 10, 20, 30], need_trend=[False, True], stop_atr=[0.3, 0.6, 1.0, 1.5], t1_r=[0, 1.0, 1.5, 2.0],
                dtrend=[None, "up", "brk", "up_rs"], news_w=[0, 10, 20, 40], news_block=[-1.0, -0.4, -0.2], news_min=[0, 0.3],
                setups=["ORB", "GAPGO", "VWAP", "PULL", "SQZ", "PDL", "FADE", "GAPFILL"]),
    "bal": dict(min_score=[50, 55, 60, 65, 70, 75], min_rvol=[1.0, 1.2, 1.5, 2.0], min_rr=[1.0, 1.3, 1.5, 2.0],
                min_mkt=[40, 50, 60], time_stop=[45, 60, 90, 120], trail=["ema9", "ema20"],
                trend_w=[0, 10, 20, 30], need_trend=[False, True], stop_atr=[0.3, 0.6, 1.0, 1.5], t1_r=[0, 1.0, 1.5, 2.0],
                dtrend=[None, "up", "brk", "up_rs"], news_w=[0, 10, 20, 40], news_block=[-1.0, -0.3, -0.15], news_min=[0, 0.3],
                setups=["ORB", "VWAP", "PULL", "PDL", "SQZ", "GAPFILL", "FADE"]),
    "def": dict(min_score=[55, 60, 65, 70, 75, 80], min_rvol=[0.8, 1.0, 1.2, 1.5], min_rr=[1.2, 1.5, 2.0],
                min_mkt=[55, 65, 75], time_stop=[60, 90, 120], trail=["ema9", "ema20"],
                trend_w=[0, 10, 20, 30], need_trend=[False, True], stop_atr=[0.3, 0.6, 1.0, 1.5], t1_r=[0, 1.0, 1.5, 2.0],
                dtrend=[None, "up", "brk", "up_rs"], news_w=[0, 10], news_block=[-1.0, -0.2, -0.1], news_wait=[0, 30, 60],
                setups=["VWAP", "PULL", "PDL", "GAPFILL"]),
}
MIN_RATE = {"agg": 0.7, "bal": 0.35, "def": 0.2}  # 학습 기간 하루당 최소 거래 수 (너무 안 사서 좋아 보이는 것 방지)
# 처음 규칙 (v2 튜닝 전) — 수수료·쿨다운은 사용자가 정한 현재 값 유지
# 단타 규칙 v5 — v6에서 실시간 트레이더는 스윙으로 바뀌었고, 단타 백테스트·튜닝은 이 규칙으로 계속 할 수 있음
DAY_RULES = {
    "agg": dict(name="공격형", nick="아이작", color="#2a78d6",
                risk=0.010, max_pos=0.50, max_open=3, max_trades=8, daily_loss=0.030,
                min_mkt=35, max_vix=35, min_dvol=2e7, max_atr=0.12, min_rvol=2.5, min_rr=2.0, min_score=60,
                setups={"ORB", "GAPGO", "PULL", "SQZ", "PDL", "FADE"}, windows=[(575, 945)],
                need_rs=False, no_earn=False, time_stop=90, trail="ema9", cooldown=0, trend_w=0, need_trend=False, stop_atr=1.5, t1_r=0, news_w=20, news_block=-0.2, news_wait=0,
                motto="갭과 거래량이 터진 종목의 돌파를 쫓는다. 승률보다 한 번의 큰 수익."),
    "bal": dict(name="밸런스형", nick="티모", color="#eb6834",
                risk=0.005, max_pos=0.35, max_open=3, max_trades=4, daily_loss=0.015,
                min_mkt=60, max_vix=28, min_dvol=1e8, max_atr=0.08, min_rvol=2.0, min_rr=2.0, min_score=60,
                setups={"ORB", "VWAP", "PULL", "PDL", "SQZ", "GAPFILL", "FADE"}, windows=[(580, 690), (840, 940)],
                need_rs=True, no_earn=True, time_stop=120, trail="ema20", cooldown=20, trend_w=0, need_trend=False, stop_atr=0.3, t1_r=0, news_w=10, news_block=-0.3, news_wait=0,
                motto="시장 방향과 같은 쪽, 거래량이 확인된 추세만. 점심 횡보는 쉰다."),
    "def": dict(name="방어형", nick="차차", color="#1baf7a",
                risk=0.0025, max_pos=0.25, max_open=2, max_trades=2, daily_loss=0.0075,
                min_mkt=75, max_vix=22, min_dvol=5e8, max_atr=0.05, min_rvol=1.5, min_rr=2.0, min_score=65,
                setups={"VWAP", "PULL", "PDL"}, windows=[(585, 690), (840, 930)],
                need_rs=True, no_earn=True, time_stop=90, trail="ema20", cooldown=60, trend_w=0, need_trend=True, stop_atr=1.5, t1_r=0, news_w=0, news_block=-0.2, news_wait=30,
                motto="시장이 깨끗할 때만, 큰 종목의 눌림목을 산다. 안 하는 것도 매매."),
}
DAY = list(DAY_RULES)
V1 = {"agg": dict(min_rvol=1.5, setups={"ORB", "GAPGO", "VWAP", "PULL", "SQZ", "PDL", "FADE"}, time_stop=45, stop_atr=0.3),
      "bal": dict(min_mkt=50, min_rvol=1.2, time_stop=60, trend_w=0),
      "def": dict()}
# 시뮬에 필요한 값만 캐시에 남김 (메모리 절약)
KEEP = {"sym", "n", "last", "chg", "gap", "rvol", "rs", "dvol", "atr_pct", "atrb", "ema9", "ema20", "earn_d",
        "setups", "t", "ret63", "sec63", "sma50d"}


# ───────────────────────── 데이터 준비 ─────────────────────────
alpaca_keys = T.alpaca_keys


def alpaca_5m(syms: list, start: dt.date, keys: tuple) -> dict:
    """Alpaca 5분봉 (SIP 전체 시장, 프리·애프터마켓 포함). 무료 계정은 15분 이전 데이터만 — 백테스트엔 충분."""
    hdr = {"APCA-API-KEY-ID": keys[0], "APCA-API-SECRET-KEY": keys[1]}
    end = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows: dict = {}
    for i in range(0, len(syms), 10):
        token = None
        while True:
            q = {"symbols": ",".join(syms[i:i + 10]), "timeframe": "5Min", "start": f"{start}T00:00:00Z", "end": end,
                 "limit": 10000, "feed": "sip", "adjustment": "all", "sort": "asc"}
            if token:
                q["page_token"] = token
            r = requests.get("https://data.alpaca.markets/v2/stocks/bars", params=q, headers=hdr, timeout=30)
            if r.status_code == 429:
                time.sleep(3)
                continue
            r.raise_for_status()
            js = r.json()
            for s, b in (js.get("bars") or {}).items():
                rows.setdefault(s, []).extend(b)
            token = js.get("next_page_token")
            if not token:
                break
        print(f"  Alpaca {min(i + 10, len(syms))}/{len(syms)} 종목", flush=True)
    out = {}
    for s, b in rows.items():
        df = pd.DataFrame(b).rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
        df.index = pd.to_datetime(df["t"], utc=True).dt.tz_convert(T.NY)
        out[s] = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    return out


def fetch_all(start: dt.date | None) -> tuple[dict, dict, dict, str]:
    uni = T.MARKET + [T.VIX] + T.WATCHLIST
    keys = alpaca_keys()
    if all(keys):
        bars = alpaca_5m([s for s in uni if s != T.VIX], (start or dt.date.today() - dt.timedelta(days=90)) - dt.timedelta(days=20), keys)
        src = "Alpaca SIP 5분봉"
    else:
        d = yf.download(uni, period="60d", interval="5m", prepost=True, progress=False, group_by="ticker",
                        auto_adjust=True, threads=True)
        bars = {}
        for s in uni:
            df = T._pick(d, s)
            if df is not None:
                df.index = df.index.tz_convert(T.NY)
                bars[s] = df
        src = "Yahoo 5분봉 (최근 60거래일)"
    dd = yf.download(uni, period="5y", interval="1d", progress=False, group_by="ticker", auto_adjust=True, threads=True)
    daily = {}
    for s in uni:
        df = T._pick(dd, s)
        if df is not None:
            df.index = pd.DatetimeIndex([pd.Timestamp(x).date() for x in df.index])
            daily[s] = df
    earn = {}
    for s in T.WATCHLIST:
        try:
            e = yf.Ticker(s).get_earnings_dates(limit=24)
            earn[s] = sorted({x.date() for x in e.index}) if e is not None else []
        except Exception:
            earn[s] = []
    return bars, daily, earn, src


def prep_day(args) -> tuple:
    day, bars, daily, earn = args
    out = {}
    for m in range(FIRST, LAST + 1, STEP):
        cut = dt.datetime.combine(day, dt.time(m // 60, m % 60), T.NY) - dt.timedelta(minutes=5)
        cb = {s: df[df.index <= cut] for s, df in bars.items()}
        spy = T.analyze("SPY", cb.get("SPY"), daily.get("SPY"), day, None, None)
        snaps = {"SPY": spy} if spy else {}
        for s in cb:
            if s in ("SPY", T.VIX):
                continue
            nxt = next((x for x in earn.get(s, []) if x >= day), None)
            try:
                a = T.analyze(s, cb[s], daily.get(s), day, spy["chg"] if spy else None,
                              (nxt.isoformat(), None) if nxt else None)
            except Exception:
                a = None
            if a:
                snaps[s] = a
        T.add_sector(snaps)
        vx = cb.get(T.VIX)
        if vx is not None and len(vx):
            vix = float(vx["Close"].iloc[-1])
        else:  # 장중 VIX가 없는 소스(Alpaca)면 전날 종가
            vd = daily.get(T.VIX)
            vix = float(vd["Close"].iloc[-1]) if vd is not None and len(vd) else np.nan
        out[m] = ({s: {k: v for k, v in a.items() if k in KEEP} for s, a in snaps.items()}, T.regime(snaps, vix))
    return day, out


def prep(start: dt.date | None = None):
    LAB.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    bars, daily, earn, src = fetch_all(start)
    spy = bars["SPY"]
    reg = (T.mins(spy.index) >= 570) & (T.mins(spy.index) < 960)
    cnt = pd.Series(reg, index=spy.index.date).groupby(level=0).sum()
    days = sorted(d for d, c in cnt.items() if T.is_trading_day(d) and c >= 70 and d < dt.date.today())
    days = [d for d in days if not start or d >= start] if start and days and days[0] < start else days[5:]
    print(f"{src} · {time.time() - t0:.0f}초 · 종목 {len(bars)}개 · 거래일 {len(days)}일 ({days[0]} ~ {days[-1]})")
    def job(day):
        lo = pd.Timestamp(day - dt.timedelta(days=10), tz=T.NY)
        hi = pd.Timestamp(day + dt.timedelta(days=1), tz=T.NY)
        return (day, {s: df[(df.index >= lo) & (df.index < hi)] for s, df in bars.items()},
                {s: df[df.index < pd.Timestamp(day)] for s, df in daily.items()}, earn)

    cache = {}
    with ProcessPoolExecutor(max_workers=8) as ex:
        for b in range(0, len(days), 40):  # 40일씩 (전 기간 작업을 한꺼번에 만들면 메모리가 수 GB)
            for day, out in ex.map(prep_day, [job(d) for d in days[b:b + 40]]):
                cache[day] = out
            print(f"  {days[min(b + 40, len(days)) - 1]}까지 완료 ({min(b + 40, len(days))}/{len(days)}일, {time.time() - t0:.0f}초)", flush=True)
    sim_bars = {s: df[(T.mins(df.index) >= 570) & (T.mins(df.index) < 960)] for s, df in bars.items()}
    pickle.dump(dict(cache=cache, bars=sim_bars, days=days, src=src), open(LAB / "cache.pkl", "wb"), protocol=5)
    print(f"캐시 저장 {LAB / 'cache.pkl'} · 총 {time.time() - t0:.0f}초")


# ───────────────────────── 시뮬레이션 ─────────────────────────
class Cut:
    """판단 시각 이전에 끝난 봉만 보이게 하는 bars 래퍼 (미래 데이터 누설 방지)."""

    def __init__(self, bars: dict, cut: pd.Timestamp):
        self.bars, self.cut = bars, cut

    def __contains__(self, s):
        return s in self.bars

    def __getitem__(self, s):
        df = self.bars[s]
        return df[df.index <= self.cut]

    def get(self, s, d=None):
        return self[s] if s in self.bars else d


_CLOCK = [None]
_ROWS: list = []


def _patch():
    T.now_ny = lambda: _CLOCK[0]
    T.write_event = lambda ev: None
    T.append_csv = lambda path, cols, row: _ROWS.append(row) if path.name == "trades.csv" else None


_BY_DAY: dict = {}


def day_bars(bars: dict, day: dt.date) -> dict:
    if not _BY_DAY:
        for s, df in bars.items():
            for d, g in df.groupby(df.index.date):
                _BY_DAY.setdefault(d, {})[s] = g
    return _BY_DAY.get(day, {})


class HistNews:
    """과거 뉴스로 실시간 NewsBook.score와 같은 값을 냄 (판단 시각 이전 기사만)."""

    def __init__(self, raw: list, universe: set):
        import bisect
        self._bisect = bisect.bisect_right
        by: dict = {}
        for it in raw:
            c = T.news_mod.classify(it, universe)
            if c:
                ts = T.news_mod._ts(c["t"])
                for s, v in c["imp"].items():
                    by.setdefault(s, []).append((ts, v, c["head"]))
        self.by = {s: sorted(v) for s, v in by.items()}
        self.ts = {s: [x[0] for x in v] for s, v in self.by.items()}

    def score(self, sym: str, now: float):
        if sym not in self.by:
            return 0.0, None
        k = self._bisect(self.ts[sym], now)
        tot, top, best = 0.0, None, 0.0
        for ts, v, head in reversed(self.by[sym][max(0, k - 40):k]):
            age = (now - ts) / 60
            if age > T.news_mod.KEEP_MIN:
                break
            d = v * 0.5 ** (age / T.news_mod.HALF_LIFE)
            tot += d
            if abs(d) > best:
                best, top = abs(d), dict(head=head, v=v, age=age)
        return max(-1.0, min(1.0, tot)), top


def dflags(days: list) -> dict:
    """일봉 추세 플래그 (traders.daily_trend와 같은 정의, 전날 종가 기준) — swing_lab 일봉으로 한 번에 계산."""
    import swing_lab
    X = swing_lab.indicators(swing_lab.load("2014-06-01"))
    cols, idx = X["cols"], X["idx"]
    W = [s for s in T.WATCHLIST if s in cols]
    ci = np.array([cols.index(s) for s in W])
    C, ma50, ma200, hh20, mom = X["C"], X["ma50"], X["ma200"], X["hh20"], X["mom126"]
    out = {}
    for d in days:
        i = int(np.searchsorted(idx, pd.Timestamp(d))) - 1  # 전날
        if i < 5:
            continue
        up = (C[i, ci] > ma50[i, ci]) & (ma50[i, ci] > ma200[i, ci])
        brk = np.any(C[i - 4:i + 1, ci] > hh20[i - 4:i + 1, ci], axis=0)
        m = pd.Series(mom[i, ci], index=W).dropna()
        rk = m.rank(pct=True)
        out[d] = {s: dict(d_up=bool(up[k]), d_brk=bool(brk[k]), d_rs=float(rk.get(s, 0.0))) for k, s in enumerate(W)}
    return out


def simulate(key: str, P: dict, days: list, cache: dict, bars: dict, news=None, flags=None) -> dict:
    _patch()
    _ROWS.clear()
    tr = T.Trader(key, None)
    tr.P = P
    curve = []
    for day in days:
        _CLOCK[0] = dt.datetime.combine(day, dt.time(9, 0), T.NY)
        tr.new_day(day, {}, {})
        snaps = {}
        fl = (flags or {}).get(day, {})
        for m, (snaps, mk) in cache[day].items():
            now = dt.datetime.combine(day, dt.time(m // 60, m % 60), T.NY)
            _CLOCK[0] = now
            if fl:
                for s, a in snaps.items():
                    a.update(fl.get(s, {}))
            tr.step(now, snaps, Cut(day_bars(bars, day), pd.Timestamp(now - dt.timedelta(minutes=5))), mk, {}, news)
        for s in list(tr.pos):
            tr.sell(s, tr.pos[s]["shares"], tr.price(s, snaps, tr.pos[s]["entry"]), "마감 정리", _CLOCK[0], final=True)
        spy = snaps.get("SPY")
        curve.append((day.isoformat(), tr.cash, spy["chg"] if spy else 0.0))
    return metrics(list(_ROWS), curve)


def metrics(rows: list, curve: list) -> dict:
    n = len(rows)
    eq = [T.START_CASH] + [c[1] for c in curve]
    peak, mdd = eq[0], 0.0
    for e in eq:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    pnl = eq[-1] - T.START_CASH
    dpnl = np.diff(eq)
    down = [p for p, c in zip(dpnl, curve) if c[2] < 0]
    out = dict(n=n, pnl=round(pnl, 2), ret=pnl / T.START_CASH, mdd=mdd, days=len(curve), curve=curve,
               down_days=len(down), down_pnl=round(float(sum(down)), 2), green_days=int((dpnl > 0).sum()))
    if n:
        w = [r for r in rows if r["pnl"] > 0]
        gl = -sum(r["pnl"] for r in rows if r["pnl"] <= 0)
        out.update(win=len(w) / n, avg_r=float(np.mean([r["r"] for r in rows])),
                   pf=(sum(r["pnl"] for r in w) / gl) if gl > 0 else None, fees=n * 2 * T.FEE)
        by = {}
        for r in rows:
            by.setdefault(r["setup"], []).append(r)
        out["by_setup"] = {k: dict(n=len(v), pnl=round(sum(x["pnl"] for x in v), 2), avg_r=float(np.mean([x["r"] for x in v])),
                                   win=sum(x["pnl"] > 0 for x in v) / len(v)) for k, v in by.items()}
        ex = {}
        for r in rows:
            ex.setdefault(r["exit_reason"], []).append(r["pnl"])
        out["by_exit"] = {k: dict(n=len(v), pnl=round(sum(v), 2)) for k, v in ex.items()}
    return out


def load():
    return pickle.load(open(LAB / "cache.pkl", "rb"))


def span(arg: str, days: list) -> list:
    a, b = (dt.date.fromisoformat(x) for x in arg.split(":"))
    return [d for d in days if a <= d <= b]


def line(m: dict) -> str:
    if not m["n"]:
        return "거래 0회"
    return (f"{m['pnl']:+8.0f}$ ({m['ret'] * 100:+.1f}%) · {m['n']:3d}회 · 승률 {m['win'] * 100:3.0f}% · "
            f"평균 {m['avg_r']:+.2f}R · PF {m['pf'] or 0:.2f} · 최대낙폭 {m['mdd'] * 100:.1f}%")


def run(a: str, b: str):
    C = load()
    days = span(f"{a}:{b}", C["days"])
    for k in DAY:
        m = simulate(k, dict(DAY_RULES[k]), days, C["cache"], C["bars"])
        print(f"{DAY_RULES[k]['nick']:>4} {line(m)}")
        for s, v in sorted(m.get("by_setup", {}).items(), key=lambda kv: -kv[1]["pnl"]):
            print(f"      {T.SETUP_KO[s]:<10} {v['n']:3d}회 {v['pnl']:+7.0f}$ 평균 {v['avg_r']:+.2f}R 승률 {v['win'] * 100:.0f}%")


# ───────────────────────── 튜닝 ─────────────────────────
DEFAULTS = dict(dtrend=None, news_min=0, news_wait=0, news_w=0, news_block=-1.0, trend_w=0, need_trend=False, stop_atr=0.3, t1_r=0)


def variants(key: str, P: dict):
    for name, opts in SPACE[key].items():
        if name == "setups":
            for s in opts:
                cur = set(P["setups"])
                new = cur - {s} if s in cur else cur | {s}
                if len(new) >= 2:
                    yield f"셋업 {'빼기' if s in cur else '추가'}: {T.SETUP_KO[s]}", dict(P, setups=new)
        else:
            for v in opts:
                cur = P.get(name, DEFAULTS.get(name))
                if v != cur:
                    yield f"{name} {cur} → {v}", dict(P, **{name: v})


def bench(days: list) -> dict:
    """같은 기간 그냥 사서 들고 있었으면? (SPY·QQQ·SMH 반도체 ETF)"""
    d = yf.download(["SPY", "QQQ", "SMH"], period="5y", interval="1d", progress=False, group_by="ticker", auto_adjust=True)
    out = {}
    for s in ("SPY", "QQQ", "SMH"):
        c = d[s]["Close"].dropna()
        c.index = [x.date() for x in c.index]
        before = c[c.index < days[0]]
        upto = c[c.index <= days[-1]]
        out[s] = float(upto.iloc[-1] / before.iloc[-1] - 1)
    return out


_C: dict = {}  # tune에서 한 번 읽고 fork로 워커에 공유 (캐시가 커서 워커마다 다시 읽지 않음)


def quarterly(m: dict) -> dict:
    out, prev = {}, T.START_CASH
    for day, cash, _ in m["curve"]:
        q = f"{day[2:4]}Q{(int(day[5:7]) - 1) // 3 + 1}"
        out[q] = out.get(q, 0.0) + cash - prev
        prev = cash
    return out


_PER: dict = {}  # 기간 이름 → 날짜 목록 (fork로 워커에 공유)


_EXTRA: dict = {}  # news(HistNews) · flags(일봉 추세) — fork로 공유


def _sim(args) -> dict:
    k, Q, per = args
    m = simulate(k, Q, _PER[per], _C["cache"], _C["bars"], _EXTRA.get("news"), _EXTRA.get("flags"))
    m["q"] = quarterly(m)
    m.pop("curve")
    return m


def tune(train_arg: str, check_arg: str, hold_arg: str, from_v1: bool = False):
    """학습 기간으로 찾고 → 변경마다 확인 기간에서도 나빠지지 않는지 → 튜닝에 안 쓴 최종 시험 기간에서 나빠지면 적용 안 함."""
    _C.update(load())
    _PER.update(train=span(train_arg, _C["days"]), check=span(check_arg, _C["days"]), hold=span(hold_arg, _C["days"]))
    _PER["all"] = sorted(set(_PER["train"]) | set(_PER["check"]) | set(_PER["hold"]))
    day_bars(_C["bars"], _C["days"][0])  # 날짜별 봉을 fork 전에 만들어 워커들이 공유
    _EXTRA["flags"] = dflags(_PER["all"])
    if (LAB / "news_hist.pkl").exists():
        _EXTRA["news"] = HistNews(pickle.load(open(LAB / "news_hist.pkl", "rb")), set(T.WATCHLIST))
        print(f"과거 뉴스 {sum(len(v) for v in _EXTRA['news'].by.values())}건 (종목별 영향) 반영", flush=True)
    rng = lambda d: f"{d[0]}~{d[-1]} ({len(d)}일)"
    print(f"데이터 {_C.get('src', '?')} · 학습 {rng(_PER['train'])} · 확인 {rng(_PER['check'])} · "
          f"최종 시험 {rng(_PER['hold'])} · 출발점 {'v1' if from_v1 else T.RULES}", flush=True)
    t0 = time.time()
    st = {}
    for k in DAY:
        P0 = dict(DAY_RULES[k], **V1[k]) if from_v1 else dict(DAY_RULES[k])
        st[k] = dict(P0=P0, P=P0, log=[], on=True)
    gc.collect()
    gc.freeze()  # fork한 워커가 캐시를 덜 복사하도록
    with ProcessPoolExecutor(max_workers=int(os.environ.get("LAB_WORKERS", 4)), mp_context=mp.get_context("fork")) as ex:
        run_ = lambda tasks: list(ex.map(_sim, tasks, chunksize=1))
        keys = [(k, per) for k in st for per in ("train", "check", "hold")]
        for (k, per), m in zip(keys, run_([(k, st[k]["P0"], per) for k, per in keys])):
            st[k][f"base_{per}"] = m
        for k in st:
            st[k]["tr"], st[k]["ck"] = st[k]["base_train"], st[k]["base_check"]
        for rnd in range(5):
            cand = [(k, d, Q) for k in st if st[k]["on"] for d, Q in variants(k, st[k]["P"])]
            if not cand:
                break
            res = run_([(k, Q, "train") for k, _, Q in cand])
            top = {}
            for (k, d, Q), m in zip(cand, res):
                c = st[k]
                if m["n"] >= MIN_RATE[k] * len(_PER["train"]) and m["pnl"] > c["tr"]["pnl"] + 25 and m["mdd"] >= c["tr"]["mdd"] - 0.01:
                    top.setdefault(k, []).append((m["pnl"], d, Q, m))
            chk = [(k, d, Q, m) for k in top for _, d, Q, m in sorted(top[k], key=lambda x: -x[0])[:4]]
            cres = run_([(k, Q, "check") for k, _, Q, _ in chk])
            for k in st:
                if not st[k]["on"]:
                    continue
                took = None
                for (kk, d, Q, m), ck in zip(chk, cres):  # 학습 상위 후보 순서대로, 확인 기간에서도 괜찮은 첫 번째
                    if kk != k or took:
                        continue
                    good = ck["pnl"] >= st[k]["ck"]["pnl"] - 10 and ck["mdd"] >= st[k]["ck"]["mdd"] - 0.01
                    st[k]["log"].append(dict(change=d, train=m["pnl"], check=ck["pnl"], kept=good))
                    if good:
                        took = (Q, m, ck)
                if took:
                    st[k]["P"], st[k]["tr"], st[k]["ck"] = took
                else:
                    st[k]["on"] = False
            print(f"  {rnd + 1}회차 끝 ({time.time() - t0:.0f}초): " + ", ".join(
                f"{DAY_RULES[k]['nick']} {st[k]['log'][-1]['change'] if st[k]['log'] and st[k]['on'] else '멈춤'}" for k in st), flush=True)
        fin = [(k, st[k]["P"], "hold") for k in st]
        for (k, _, _), m in zip(fin, run_(fin)):
            st[k]["hold"] = m
        cmp = [(k, n, Q) for k in st for n, Q in (("v1", dict(DAY_RULES[k], **V1[k])), ("현재", dict(DAY_RULES[k])), ("new", st[k]["P"]))]
        for (k, n, _), m in zip(cmp, run_([(k, Q, "all") for k, _, Q in cmp])):
            st[k][f"all_{n}"] = m

    labs = (("train", "학습"), ("check", "확인"), ("hold", "최종시험"))
    bm = {n: bench(_PER[n]) for n, _ in labs}
    report = {}
    for k, c in st.items():
        P = DAY_RULES[k]
        fin = dict(train=c["tr"], check=c["ck"], hold=c["hold"])
        changes = {kk: (sorted(v) if isinstance(v, set) else v) for kk, v in c["P"].items()
                   if kk in SPACE[k] and c["P"].get(kk, DEFAULTS.get(kk)) != c["P0"].get(kk, DEFAULTS.get(kk))}
        passed = bool(changes) and c["hold"]["pnl"] >= c["base_hold"]["pnl"] - 10
        print(f"\n[{P['nick']} {P['name']}]")
        for x in c["log"]:
            print(f"  후보 {x['change']:<30} 학습 {x['train']:+7.0f}$ · 확인 {x['check']:+6.0f}$ → {'채택' if x['kept'] else '기각'}")
        for n, lab in labs:
            print(f"  {lab:<6} 출발 {line(c['base_' + n])}\n  {'':<6} 튜닝 {line(fin[n])}")
            print(f"  {'':<6} 하락일 {fin[n]['down_days']}일 손익 {fin[n]['down_pnl']:+.0f}$ (출발 {c['base_' + n]['down_pnl']:+.0f}$)")
        print(f"  바뀐 규칙: {changes or '없음'} → {'적용 가능' if passed else '적용 안 함'}")
        qs = sorted(c["all_v1"]["q"])
        print("  분기 손익 " + " ".join(f"{q:>6}" for q in qs))
        for n in ("v1", "현재", "new"):
            qq = c[f"all_{n}"]["q"]
            print(f"  {n:<8} " + " ".join(f"{qq.get(q, 0):+6.0f}" for q in qs) +
                  f" | 플러스 {sum(qq.get(q, 0) > 0 for q in qs)}/{len(qs)}분기 · 전체 {line(c[f'all_{n}'])}")
        report[k] = dict(start={kk: (sorted(v) if isinstance(v, set) else v) for kk, v in c["P0"].items() if kk in SPACE[k]},
                         params={kk: (sorted(v) if isinstance(v, set) else v) for kk, v in c["P"].items() if kk in SPACE[k]},
                         changes=changes, passed=passed, log=c["log"], final=fin,
                         base={n: c["base_" + n] for n, _ in labs}, all={n: c[f"all_{n}"] for n in ("v1", "현재", "new")})
    print("\n그냥 사서 보유했다면:", " · ".join(f"{lab} " + ", ".join(f"{s} {v * 100:+.1f}%" for s, v in bm[n].items())
                                       for n, lab in labs))
    out = LAB / f"tune_{dt.date.today().isoformat()}_{_PER['train'][0]}.json"
    out.write_text(json.dumps(T._clean(dict(results=report, bench=bm, periods={n: [str(_PER[n][0]), str(_PER[n][-1])] for n, _ in labs})),
                              ensure_ascii=False, indent=1, default=str))
    print(f"저장: {out} · 총 {time.time() - t0:.0f}초")


# ───────────────────────── 과거 뉴스 (뉴스 반응 백테스트용) ─────────────────────────
def fetch_news_hist(start: str, end: str | None = None):
    """관심종목이 태그된 Alpaca(Benzinga) 과거 뉴스를 받아 저장 — 헤드라인·시각·태그만."""
    keys = T.alpaca_keys()
    hdr = {"APCA-API-KEY-ID": keys[0], "APCA-API-SECRET-KEY": keys[1]}
    end = end or dt.date.today().isoformat()
    out, token, t0, n = [], None, time.time(), 0
    while True:
        q = {"symbols": ",".join(T.WATCHLIST), "start": f"{start}T00:00:00Z", "end": f"{end}T23:59:59Z", "limit": 50,
             "sort": "asc", "include_content": "false", "exclude_contentless": "false"}
        if token:
            q["page_token"] = token
        r = requests.get(T.news_mod.NEWS_URL, params=q, headers=hdr, timeout=30)
        if r.status_code == 429:
            time.sleep(5)
            continue
        r.raise_for_status()
        js = r.json()
        out += [dict(id=x["id"], created_at=x["created_at"], headline=x["headline"], symbols=x.get("symbols", [])) for x in js.get("news", [])]
        token = js.get("next_page_token")
        n += 1
        if n % 100 == 0:
            print(f"  {len(out)}건 · {out[-1]['created_at'][:10]}까지 · {time.time() - t0:.0f}초", flush=True)
        if not token:
            break
    LAB.mkdir(parents=True, exist_ok=True)
    pickle.dump(out, open(LAB / "news_hist.pkl", "wb"))
    print(f"저장 {len(out)}건 · {time.time() - t0:.0f}초")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "prep":
        prep(dt.date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else None)
    elif cmd == "news":
        fetch_news_hist(sys.argv[2])
    elif cmd == "run":
        run(sys.argv[2], sys.argv[3])
    elif cmd == "tune":
        tune(sys.argv[2], sys.argv[3], sys.argv[4], "--from-v1" in sys.argv)
    else:
        print(__doc__)
