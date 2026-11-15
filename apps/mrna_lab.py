"""MRNA 전문 단타(모나) 규칙 찾기 — 모더나 한 종목만, 1분봉으로 검증하고 실전은 몇 초 간격으로 감시.

  .venv/bin/python apps/mrna_lab.py fetch     # Alpaca에서 MRNA·SPY 1분봉(2024-01~)과 호가 표본을 받아 journal/traders/lab/mrna_1m.pkl
  .venv/bin/python apps/mrna_lab.py           # 규칙 검증 → 표 + journal/traders/lab/mrna_result.json

데이터: Alpaca 전체 시장(SIP) 1분봉, 정규장만. 호가 표본으로 실제 매수·매도 호가 차이(스프레드)를 재서 비용으로 씀.
체결: 신호는 1분봉 종가에 확인 → 다음 1분봉 시가에 매수. 손절·익절은 봉 안의 고가·저가로 확인하고, 한 봉에 둘 다 닿으면 손절로 침(보수적).
비용: 주문당 $2 ($20,000 기준 편도 0.01%) + 편도 슬리피지 = max(앱 기본 0.05%, 실측 스프레드의 절반). 롱만, 한 번에 계좌 전부, 15:50 ET 전량 정리.
검증: 학습(24.01~25.06)에서 고르고 → 확인(25.07~12) → 시험(26.01~)은 고르는 데 안 씀.

결과 (2026-11-15) — 통과한 규칙 없음
  시장가 규칙 6군 2,057개: 학습·확인 둘 다 플러스 23개, 그중 시험도 플러스 4개. 학습 1등(급등 추종)은 시험 −18%. 비용이 없으면 대부분 플러스 — 스프레드가 다 가져감.
  지정가 받아먹기 193개: 학습은 좋지만(1등 +31%) 확인·시험에서 마이너스. 둘 다 플러스 9개, 시험까지 0개.
  장중 추세 추종 96개: 학습 전부 마이너스, 확인·시험은 4분의 3이 플러스.
  → MRNA가 99→28달러로 내린 학습 기간엔 받아먹기가, 28→223달러로 오른 뒤엔 추세 추종이 번 것. 그래서 모나는 전일 종가가 50일선 위면 추세 추종,
    아래면 받아먹기로 바꿔 씀 (학습 +10% · 확인 −0% · 시험 +14%). 세 기간을 다 보고 만든 규칙이고 이웃 설정도 불안정해서 검증된 우위가 아님.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import pickle
import sys

import numpy as np
import pandas as pd
import requests

import quant_wide as W
import traders as T

LAB = T.DATA / "lab"
FILE = LAB / "mrna_1m.pkl"
SYM, START = "MRNA", "2024-01-02"
PERIODS = {"학습": ("2024-01-01", "2025-06-30"), "확인": ("2025-07-01", "2025-12-31"), "시험": ("2026-01-01", "2026-12-31")}
NB = 390


# ───────────────────────── 데이터 ─────────────────────────
def frame(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows).rename(columns=dict(o="Open", h="High", l="Low", c="Close", v="Volume"))
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("t"), utc=True)).tz_convert(T.NY)
    m = T.mins(df.index)
    return df[(m >= 570) & (m < 960)][["Open", "High", "Low", "Close", "Volume"]]


def quote_sample(days: list, per_day=13) -> pd.DataFrame:
    """날마다 30분 간격으로 그 순간의 호가를 조금씩 받아 스프레드(매도호가−매수호가)를 잼."""
    rows = []
    for d in days:
        for k in range(per_day):
            t0 = dt.datetime.combine(d, dt.time(9, 35), T.NY) + dt.timedelta(minutes=30 * k)
            W.throttle()
            r = requests.get(f"https://data.alpaca.markets/v2/stocks/{SYM}/quotes", headers=W.hdr(), timeout=30,
                             params=dict(start=t0.isoformat(), end=(t0 + dt.timedelta(seconds=20)).isoformat(), limit=200, feed="sip"))
            if r.status_code != 200:
                continue
            q = [x for x in r.json().get("quotes") or [] if x["bp"] > 0 and x["ap"] > x["bp"]]
            if q:
                sp = np.median([(x["ap"] - x["bp"]) / ((x["ap"] + x["bp"]) / 2) for x in q])
                rows.append(dict(day=d, minute=t0.hour * 60 + t0.minute, spread=float(sp), mid=float(np.median([(x["ap"] + x["bp"]) / 2 for x in q]))))
    return pd.DataFrame(rows)


def fetch():
    end = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")  # 최근 15분은 무료 계정에 안 줌
    raw = W.bars([SYM, "SPY"], "1Min", START, end)
    B = {s: frame(raw[s]) for s in (SYM, "SPY")}
    days = sorted(set(B[SYM].index.date))
    Q = quote_sample(days[-250::5])
    LAB.mkdir(parents=True, exist_ok=True)
    pickle.dump(dict(bars=B, quotes=Q), open(FILE, "wb"))
    print(f"{SYM} 1분봉 {len(B[SYM]):,}개 · {days[0]} ~ {days[-1]} ({len(days)}일) · 호가 표본 {len(Q)}개")



class Data:
    """날짜 × 분(390) 배열. 빈 봉은 직전 종가로 메움. 모든 지표는 그 시각까지의 정보만 씀."""

    def __init__(self):
        raw = pickle.load(open(FILE, "rb"))
        self.quotes = raw["quotes"]
        B, S = raw["bars"][SYM], raw["bars"]["SPY"]
        cnt = B.groupby(B.index.date).size()
        self.days = [d for d, n in cnt.items() if n >= 380]  # 조기 폐장일 제외
        ix = {d: i for i, d in enumerate(self.days)}

        def cube(df):
            A = {k: np.full((len(self.days), NB), np.nan) for k in ("Open", "High", "Low", "Close", "Volume")}
            di = np.array([ix.get(d, -1) for d in df.index.date])
            bi = T.mins(df.index) - 570
            ok = di >= 0
            for k in A:
                A[k][di[ok], bi[ok]] = df[k].to_numpy()[ok]
            C = A["Close"]
            for t in range(NB):
                m = np.isnan(C[:, t])
                C[m, t] = C[m, t - 1] if t else A["Open"][m, min(t + 1, NB - 1)]
                for k in ("Open", "High", "Low"):
                    A[k][m, t] = C[m, t]
                A["Volume"][m, t] = 0
            return A

        A, P = cube(B), cube(S)
        self.O, self.H, self.L, self.C, self.V = (A[k] for k in ("Open", "High", "Low", "Close", "Volume"))
        O, H, L, C, V = self.O, self.H, self.L, self.C, self.V
        lag = lambda X, n=14: pd.DataFrame(X).rolling(n, min_periods=7).mean().shift(1).to_numpy()
        self.PC = np.r_[np.nan, C[:-1, -1]]
        self.s1 = lag(np.diff(np.log(C), axis=1).std(axis=1)[:, None])[:, 0]  # 평소 1분 변동폭 (직전 14일)
        cv = np.cumsum(V, 1)
        self.VW = np.cumsum((H + L + C) / 3 * V, 1) / np.where(cv > 0, cv, np.nan)
        pv = np.cumsum(P["Volume"], 1)
        self.SPYUP = P["Close"] > np.cumsum((P["High"] + P["Low"] + P["Close"]) / 3 * P["Volume"], 1) / np.where(pv > 0, pv, np.nan)
        self.RV1 = V / lag(V)  # 이 1분의 거래량 ÷ 평소 같은 시각
        v5 = pd.DataFrame(V.T).rolling(5, min_periods=1).sum().to_numpy().T
        self.RV5 = v5 / lag(v5)
        ema = lambda n: pd.DataFrame(C.T).ewm(span=n, adjust=False).mean().to_numpy().T
        self.E9, self.E20 = ema(9), ema(20)
        self.ok = ~np.isnan(self.s1) & ~np.isnan(self.PC)
        dts = pd.to_datetime(self.days)
        self.mask = {k: np.asarray((dts >= a) & (dts <= b)) for k, (a, b) in PERIODS.items()}
        # 비용: 실측 스프레드의 절반(시간대별 중앙값)과 앱 기본 0.05% 중 큰 값 + 수수료
        q = self.quotes.groupby("minute").spread.median() / 2
        half = np.interp(np.arange(570, 960), q.index.to_numpy(), q.to_numpy())
        self.cost = np.maximum(T.SLIP, half) + T.FEE / T.START_CASH


def roll_all(X: np.ndarray, n: int, fn) -> np.ndarray:
    return fn(pd.DataFrame(X.T).rolling(n, min_periods=n)).to_numpy().T


def signals(d: Data, fam: str, **p) -> np.ndarray:
    """t분 종가에 확인되는 매수 신호 (다음 1분봉 시가에 체결)."""
    C, H, L, VW, s1 = d.C, d.H, d.L, d.VW, d.s1[:, None]
    up = C > d.PC[:, None]
    prevC = np.c_[np.full(len(C), np.nan), C[:, :-1]]
    if fam == "orb":  # 첫 N분 고점을 거래량과 함께 돌파
        n = p["n"]
        orh = np.nanmax(H[:, :n], 1)[:, None]
        sig = (C > orh) & (prevC <= orh) & (C > VW) & (d.RV5 >= p["rv"])
        sig[:, :n] = False
    elif fam == "reclaim":  # VWAP 아래에 n분 있다가 다시 올라섬
        below = roll_all((C < VW).astype(float), p["n"], lambda r: r.min())
        sig = (C > VW) & (np.c_[np.zeros(len(C)), below[:, :-1]] == 1) & (d.RV1 >= p["rv"]) & (up | (not p["up"]))
    elif fam in ("dip", "fade"):  # n분 사이 급락. dip = 오르는 날 VWAP 위에서, fade = VWAP 아래로 뻗었을 때
        n = p["n"]
        z = np.full(C.shape, np.nan)
        z[:, n:] = (C[:, n:] / C[:, :-n] - 1) / (s1 * np.sqrt(n))
        sig = (z <= -p["a"]) & (d.RV5 >= p["rv"]) & ((C > VW) & up if fam == "dip" else (C < VW))
        if p.get("turn"):  # 마지막 1분은 양봉이어야 (떨어지는 칼날 피하기)
            sig &= C > prevC
    elif fam == "burst":  # 1분 급등 + 거래량 폭발 + n분 신고가
        z1 = (C / prevC - 1) / s1
        hi = roll_all(C, p["n"], lambda r: r.max())
        sig = (z1 >= p["a"]) & (d.RV1 >= p["rv"]) & (C >= hi) & (C > VW)
    else:  # pull: 상승 추세(9EMA>20EMA, VWAP 위)에서 9EMA까지 눌렸다가 직전 봉 고가를 넘김
        prevH = np.c_[np.full(len(C), np.nan), H[:, :-1]]
        sig = (d.E9 > d.E20) & (C > VW) & (L <= d.E9) & (C > prevH) & (d.RV1 >= p["rv"]) & (up | (not p["up"]))
        sig[:, :20] = False
    if p.get("mkt"):
        sig &= d.SPYUP
    sig[:, :p.get("skip", 5)] = False  # 개장 직후 몇 분은 스프레드가 넓어서 쉼
    sig[:, 370:] = False  # 15:40 이후엔 새로 안 삼
    return sig & d.ok[:, None]


def trade(d: Data, i: int, e: int, stop: float, tp: float | None, hold: int | None, trail: float | None) -> tuple[int, float]:
    """e분 시가에 사서 언제 얼마에 파는지. stop·tp·trail은 '평소 1분 변동폭'의 배수. 한 봉에 손절과 익절이 같이 닿으면 손절."""
    px, u = d.O[i, e], d.O[i, e] * d.s1[i]
    end = min(380, e + hold) if hold else 380  # 380 = 15:50
    lo, hi, op = d.L[i, e:end], d.H[i, e:end], d.O[i, e:end]
    line = np.full(len(lo), px - stop * u)
    if trail:
        line = np.maximum(line, np.r_[px, np.maximum.accumulate(hi)[:-1]] - trail * u)
    hs = np.flatnonzero(lo <= line)
    ht = np.flatnonzero(hi >= px + tp * u) if tp else np.array([], int)
    a, b = (hs[0] if len(hs) else 10 ** 6), (ht[0] if len(ht) else 10 ** 6)
    if a <= b and a < 10 ** 6:
        return e + a, min(op[a], line[a])
    if b < 10 ** 6:
        return e + b, px + tp * u
    return end, d.O[i, end]


def run(d: Data, sig: np.ndarray, stop=6.0, tp=None, hold=None, trail=None, cost=None):
    """하루 수익률, 하루 거래 수, 거래별 수익률."""
    c = d.cost if cost is None else np.full(NB, cost)
    day_r, day_n, rets = np.zeros(len(d.days)), np.zeros(len(d.days)), []
    for i in np.flatnonzero(sig.any(1)):
        free, eq = 0, 1.0
        for t in np.flatnonzero(sig[i]):
            e = t + 1
            if e < free:
                continue
            x, out = trade(d, i, e, stop, tp, hold, trail)
            r = out / d.O[i, e] * (1 - c[min(x, NB - 1)]) / (1 + c[e]) - 1
            eq *= 1 + r
            rets.append(r)
            day_n[i] += 1
            free = x + 1
        day_r[i] = eq - 1
    return day_r, day_n, np.array(rets)


def limit_run(d: Data, ref="vwap", k=5.0, tp=5.0, stop=10.0, hold=15, up=False, mkt=False, below=False, skip=5, fee_only=True):
    """지정가로 받아먹기: 매 1분 종가에 '기준가 − k×평소 1분 변동폭'에 매수 지정가를 걸어 두고, 다음 1분 안에 가격이 그 아래로 뚫고 내려가면 체결.
    익절도 지정가(체결가 + tp), 손절(체결가 − stop)·시간 청산은 시장가. 지정가 체결은 스프레드를 안 내므로 수수료만, 시장가 청산은 스프레드까지.
    보수적 가정: 가격이 지정가를 '뚫어야'(같으면 미체결) 체결 · 체결된 봉에서는 익절 불가 · 한 봉에 손절·익절이 같이 닿으면 손절."""
    C, O, H, L = d.C, d.O, d.H, d.L
    u = d.C * d.s1[:, None]
    base = {"close": C, "vwap": d.VW, "ema": d.E20}[ref]
    P = np.minimum(base, C) - k * u  # 지금 가격보다 위에 매수 지정가를 걸 수는 없음
    okb = d.ok[:, None] & np.isfinite(P)
    if up:
        okb &= C > d.PC[:, None]
    if mkt:
        okb &= d.SPYUP
    if below:
        okb &= C < d.VW
    fee = T.FEE / T.START_CASH
    day_r, day_n, rets = np.zeros(len(d.days)), np.zeros(len(d.days)), []
    fill = np.zeros(C.shape, bool)
    fill[:, 1:] = okb[:, :-1] & (L[:, 1:] < P[:, :-1])
    fill[:, :skip + 1] = False
    fill[:, 370:] = False
    for i in np.flatnonzero(fill.any(1)):
        free, eq = 0, 1.0
        for e in np.flatnonzero(fill[i]):
            if e < free:
                continue
            px = min(P[i, e - 1], O[i, e])
            uu = px * d.s1[i]
            end = min(380, e + hold)
            sl, tgt = px - stop * uu, px + tp * uu
            x, out, mk_exit = end, O[i, end], True
            for j in range(e, end):
                if L[i, j] <= sl:
                    x, out = j, (min(O[i, j], sl) if j > e else sl)
                    break
                if j > e and H[i, j] > tgt:
                    x, out, mk_exit = j, max(tgt, O[i, j]) if O[i, j] > tgt else tgt, False
                    break
            c_out = (d.cost[min(x, NB - 1)] if mk_exit else fee) if fee_only else d.cost[min(x, NB - 1)]
            c_in = fee if fee_only else d.cost[e]
            r = out / px * (1 - c_out) / (1 + c_in) - 1
            eq *= 1 + r
            rets.append(r)
            day_n[i] += 1
            free = x + 1
        day_r[i] = eq - 1
    return day_r, day_n, np.array(rets)


LIMIT_GRID = [dict(ref=r, k=k, tp=tp, stop=st, hold=h, up=u, mkt=m, below=b)
              for r, k, tp, st, h, u, m, b in itertools.product(("close", "vwap", "ema"), (3.0, 5.0, 8.0), (3.0, 6.0), (8.0, 16.0), (5, 15, 60),
                                                                (False, True), (False, True), (False,))]


def limit_main(d: Data) -> list:
    rows = []
    for g in LIMIT_GRID:
        r, n, tr = limit_run(d, **g)
        rm, _, _ = limit_run(d, **g, fee_only=False)
        rows.append(dict(p=g, win=float((tr > 0).mean()) if len(tr) else 0, avg=float(tr.mean()) if len(tr) else 0,
                         **{k: stats(r, n, m) for k, m in d.mask.items()}, **{k + "_m": stats(rm, n, m) for k, m in d.mask.items()}))
    live = [w for w in rows if w["학습"]["tpd"] >= 0.3]

    def show(w):
        print(f"  {({k: v for k, v in w['p'].items() if v not in (False,)})}  승률 {w['win'] * 100:.0f}% 평균 {w['avg'] * 1e4:+.1f}bp\n     " + " | ".join(
            f"{k} {w[k]['ret'] * 100:+6.1f}% S{w[k]['sharpe']:+.2f} 낙폭{w[k]['dd'] * 100:4.0f}% {w[k]['tpd']:.1f}건/일 (전부 시장가였다면 {w[k + '_m']['ret'] * 100:+.0f}%)" for k in PERIODS))

    both = [w for w in live if w["학습"]["ret"] > 0 and w["확인"]["ret"] > 0]
    print(f"\n[지정가 받아먹기] 설정 {len(live)}개 — 학습 플러스 {np.mean([w['학습']['ret'] > 0 for w in live]) * 100:.0f}% · 확인 플러스 {np.mean([w['확인']['ret'] > 0 for w in live]) * 100:.0f}% · "
          f"시험 플러스 {np.mean([w['시험']['ret'] > 0 for w in live]) * 100:.0f}% · 학습·확인 둘 다 플러스 {len(both)}개, 그중 시험도 플러스 {sum(w['시험']['ret'] > 0 for w in both)}개")
    for w in sorted(live, key=lambda w: -w["학습"]["sharpe"])[:8]:
        show(w)
    return rows


def trend_run(d: Data, k=1.0, step=5, exit="both", mkt=False, start=5, last=360, cost=None):
    """장중 추세 추종 (Zarattini·Aziz·Barbon 2024의 '노이즈 구간' 돌파, 롱만).
    상단 = max(오늘 시가, 전일 종가) × (1 + k × 평소 이 시각까지의 시가 대비 변동폭(직전 14일 평균)).
    step분마다 종가가 상단 위면 다음 1분 시가에 매수. 들고 있는 동안 step분마다 종가가 청산선(상단·VWAP 중 exit에 따라) 아래면 매도. 15:50 전량 정리."""
    C, O = d.C, d.O
    c = d.cost if cost is None else np.full(NB, cost)
    move = pd.DataFrame(np.abs(C / O[:, :1] - 1)).rolling(14, min_periods=7).mean().shift(1).to_numpy()
    upper = np.fmax(O[:, :1], d.PC[:, None]) * (1 + k * move)
    line = {"band": upper, "vwap": d.VW, "both": np.fmax(upper, d.VW)}[exit]
    okd = d.ok & ~np.isnan(move[:, 0])
    day_r, day_n, rets = np.zeros(len(d.days)), np.zeros(len(d.days)), []
    for i in np.flatnonzero(okd):
        pos, eq = None, 1.0
        for t in range(start + step - 1, 380, step):
            if pos is None:
                if t < last and C[i, t] > upper[i, t] and (not mkt or d.SPYUP[i, t]):
                    pos = t + 1
            elif C[i, t] < line[i, t]:
                r = O[i, t + 1] / O[i, pos] * (1 - c[t + 1]) / (1 + c[pos]) - 1
                eq *= 1 + r
                rets.append(r)
                day_n[i] += 1
                pos = None
        if pos is not None and pos < 380:
            r = O[i, 380] / O[i, pos] * (1 - c[380]) / (1 + c[pos]) - 1
            eq *= 1 + r
            rets.append(r)
            day_n[i] += 1
        day_r[i] = eq - 1
    return day_r, day_n, np.array(rets)


TREND_GRID = [dict(k=k, step=st, exit=ex, mkt=m) for k, st, ex, m in itertools.product((0.5, 0.75, 1.0, 1.5), (1, 5, 15, 30), ("band", "vwap", "both"), (False, True))]


def trend_main(d: Data) -> list:
    rows = []
    for g in TREND_GRID:
        r, n, tr = trend_run(d, **g)
        r0, _, _ = trend_run(d, **g, cost=0.0)
        rows.append(dict(p=g, win=float((tr > 0).mean()) if len(tr) else 0, avg=float(tr.mean()) if len(tr) else 0,
                         **{k: stats(r, n, m) for k, m in d.mask.items()}, **{k + "_0": stats(r0, n, m) for k, m in d.mask.items()}))

    def show(w):
        print(f"  {w['p']}  승률 {w['win'] * 100:.0f}% 평균 {w['avg'] * 1e4:+.0f}bp\n     " + " | ".join(
            f"{k} {w[k]['ret'] * 100:+6.1f}% S{w[k]['sharpe']:+.2f} 낙폭{w[k]['dd'] * 100:4.0f}% {w[k]['tpd']:.1f}건/일 (비용0 {w[k + '_0']['ret'] * 100:+.0f}%)" for k in PERIODS))

    both = [w for w in rows if w["학습"]["ret"] > 0 and w["확인"]["ret"] > 0]
    print(f"\n[장중 추세 추종] 설정 {len(rows)}개 — 학습 플러스 {np.mean([w['학습']['ret'] > 0 for w in rows]) * 100:.0f}% · 확인 플러스 {np.mean([w['확인']['ret'] > 0 for w in rows]) * 100:.0f}% · "
          f"시험 플러스 {np.mean([w['시험']['ret'] > 0 for w in rows]) * 100:.0f}% · 학습·확인 둘 다 플러스 {len(both)}개, 그중 시험도 플러스 {sum(w['시험']['ret'] > 0 for w in both)}개")
    for w in sorted(rows, key=lambda w: -w["학습"]["sharpe"])[:8]:
        show(w)
    return rows


LIVE_TREND = dict(k=1.0, step=15, exit="both", mkt=False)  # traders.py의 모나가 쓰는 값 (격자의 가운데 값 — 성적으로 고른 게 아님)
LIVE_FADE = dict(ref="vwap", k=5.0, tp=6.0, stop=16.0, hold=60)


def regime_main(d: Data):
    """세 규칙군이 모두 검증에서 떨어진 뒤 세운 가설: MRNA가 내리던 학습 기간엔 받아먹기가, 오르던 확인·시험 기간엔 추세 추종이 벌었다 →
    전일 종가가 N일선 위인 날은 추세 추종, 아래인 날은 받아먹기. 세 기간을 다 본 뒤에 만든 규칙이라 '시험' 기간도 더는 깨끗한 검증이 아님."""
    dc = pd.Series(d.C[:, -1])
    rt, nt, _ = trend_run(d, **LIVE_TREND)
    rf, nf, _ = limit_run(d, **LIVE_FADE)
    line = lambda r, n: " | ".join(f"{k} {stats(r, n, m)['ret'] * 100:+6.1f}% S{stats(r, n, m)['sharpe']:+.2f} 낙폭{stats(r, n, m)['dd'] * 100:4.0f}% {stats(r, n, m)['tpd']:.1f}건/일"
                                   for k, m in d.mask.items())
    print("\n[추세에 따라 모드 바꾸기] 추세 추종 " + str(LIVE_TREND) + " · 받아먹기 " + str(LIVE_FADE))
    for n in (20, 50, 100):
        ma = dc.rolling(n).mean().shift(1).to_numpy()
        up = np.where(np.isnan(ma), False, d.PC > ma)
        print(f"  {n:3d}일선{' (모나)' if n == 50 else '':7s} {line(np.where(up, rt, rf), np.where(up, nt, nf))}")
        print(f"       반대로 쓰면  {line(np.where(up, rf, rt), np.where(up, nf, nt))}")
    res = []
    ma = dc.rolling(50).mean().shift(1).to_numpy()
    up = np.where(np.isnan(ma), False, d.PC > ma)
    for k, st, ex in itertools.product((0.75, 1.0, 1.5), (5, 15, 30), ("both", "vwap")):
        a, an, _ = trend_run(d, k=k, step=st, exit=ex)
        for fk, ftp, fh in itertools.product((5.0, 8.0), (3.0, 6.0), (15, 60)):
            b, bn, _ = limit_run(d, ref="vwap", k=fk, tp=ftp, stop=16.0, hold=fh)
            res.append([stats(np.where(up, a, b), np.where(up, an, bn), m)["ret"] for m in d.mask.values()])
    res = np.array(res)
    print(f"  이웃 설정 {len(res)}개 (50일선): 세 기간 모두 플러스 {(res > 0).all(1).mean() * 100:.0f}% · 기간별 플러스 "
          + " ".join(f"{k} {x * 100:.0f}%" for k, x in zip(PERIODS, (res > 0).mean(0))) + " · 중앙값 " + " ".join(f"{x * 100:+.0f}%" for x in np.median(res, 0)))


def stats(r: np.ndarray, n: np.ndarray, m: np.ndarray) -> dict:
    x = r[m]
    eq = np.cumprod(1 + x)
    sd = x.std()
    return dict(ret=float(eq[-1] - 1), sharpe=float(x.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0,
                dd=float((eq / np.maximum.accumulate(eq) - 1).min()), tpd=float(n[m].mean()))


def grid() -> list[tuple[str, dict]]:
    G = []
    G += [("orb", dict(n=n, rv=rv, mkt=m)) for n, rv, m in itertools.product((5, 15, 30), (1.0, 2.0), (False, True))]
    G += [("reclaim", dict(n=n, rv=rv, up=u, mkt=m)) for n, rv, u, m in itertools.product((5, 15), (1.0, 2.0), (False, True), (False, True))]
    G += [("dip", dict(n=n, a=a, rv=rv, turn=t, mkt=m)) for n, a, rv, t, m in itertools.product((3, 5, 10), (2.0, 3.0), (1.0, 3.0), (False, True), (False, True))]
    G += [("fade", dict(n=n, a=a, rv=rv, turn=t)) for n, a, rv, t in itertools.product((3, 5, 10), (2.0, 3.0, 4.0), (1.0, 3.0), (False, True))]
    G += [("burst", dict(n=n, a=a, rv=rv, mkt=m)) for n, a, rv, m in itertools.product((10, 30), (2.0, 3.0), (2.0, 4.0), (False, True))]
    G += [("pull", dict(rv=rv, up=u, mkt=m)) for rv, u, m in itertools.product((0.0, 1.5), (False, True), (False, True))]
    return G


EXITS = [dict(stop=s, tp=t, hold=h, trail=tr) for s, t, h, tr in itertools.product((4.0, 8.0), (None, 8.0, 16.0), (10, 30, None), (None, 8.0))]


def main():
    d = Data()
    q = d.quotes.spread * 1e4
    print(f"{SYM} {d.days[0]} ~ {d.days[-1]} ({len(d.days)}일) · 종가 {d.C[-1, -1]:.2f} · 평소 1분 변동폭 {np.nanmedian(d.s1) * 1e4:.0f}bp · "
          f"스프레드 중앙값 {q.median():.1f}bp (개장 직후 {d.quotes[d.quotes.minute == 575].spread.median() * 1e4:.0f}bp)")
    print("편도 비용: 09:35 {:.3%} · 10:05 {:.3%} · 12:00 {:.3%} · 15:30 {:.3%}".format(*(d.cost[m - 570] for m in (575, 605, 720, 930))))
    bh = d.C[:, -1] / d.O[:, 0] - 1
    print("장중 보유(시가→종가, 비용 없음): " + " · ".join(f"{k} {stats(np.nan_to_num(bh), bh * 0, m)['ret'] * 100:+.0f}%" for k, m in d.mask.items()))
    rows = []
    for fam, p in grid():
        sig = signals(d, fam, **p)
        for x in EXITS:
            r, n, _ = run(d, sig, **x)
            r0, _, _ = run(d, sig, **x, cost=0.0)
            rows.append(dict(fam=fam, p=p, x=x, **{k: stats(r, n, m) for k, m in d.mask.items()}, **{k + "_0": stats(r0, n, m) for k, m in d.mask.items()}))
    live = [w for w in rows if w["학습"]["tpd"] >= 0.3]

    def show(w):
        print(f"  {w['fam']:7s} {w['p']} {({k: v for k, v in w['x'].items() if v is not None})}\n     " + " | ".join(
            f"{k} {w[k]['ret'] * 100:+6.1f}% S{w[k]['sharpe']:+.2f} 낙폭{w[k]['dd'] * 100:4.0f}% {w[k]['tpd']:.1f}건/일 (비용0 {w[k + '_0']['ret'] * 100:+.0f}%)" for k in PERIODS))

    for fam in ("orb", "reclaim", "dip", "fade", "burst", "pull"):
        rs = [w for w in live if w["fam"] == fam]
        if not rs:
            continue
        print(f"\n[{fam}] 설정 {len(rs)}개 — 학습 플러스 {np.mean([w['학습']['ret'] > 0 for w in rs]) * 100:.0f}% · 확인 플러스 {np.mean([w['확인']['ret'] > 0 for w in rs]) * 100:.0f}% · "
              f"시험 플러스 {np.mean([w['시험']['ret'] > 0 for w in rs]) * 100:.0f}% · 비용 0이면 학습 플러스 {np.mean([w['학습_0']['ret'] > 0 for w in rs]) * 100:.0f}%")
        for w in sorted(rs, key=lambda w: -w["학습"]["sharpe"])[:3]:
            show(w)
    both = [w for w in live if w["학습"]["ret"] > 0 and w["확인"]["ret"] > 0]
    print(f"\n전체 {len(live)}개 중 학습·확인 둘 다 플러스 {len(both)}개, 그중 시험도 플러스 {sum(w['시험']['ret'] > 0 for w in both)}개")
    print("■ 학습 샤프 상위 5 (고르는 기준)")
    for w in sorted(live, key=lambda w: -w["학습"]["sharpe"])[:5]:
        show(w)
    lim, trd = limit_main(d), trend_main(d)
    regime_main(d)
    (LAB / "mrna_result.json").write_text(json.dumps(dict(rows=rows, limit=lim, trend=trd), ensure_ascii=False, default=float))


if __name__ == "__main__":
    fetch() if sys.argv[1:] == ["fetch"] else main()
