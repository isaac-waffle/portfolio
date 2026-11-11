"""전체 미국 주식으로 넓힌 '첫 5분봉 돌파(ORB)' 시험 — 그날 개장 5분 거래량이 평소보다 터진 종목만 골라 첫 봉 방향으로 돌파 매매.
(Zarattini·Barbon·Aziz 2024 "A Profitable Day Trading Strategy for the U.S. Equity Market"의 규칙을 그대로 옮김)

  .venv/bin/python apps/quant_wide.py fetch   # ① 종목 목록·일봉 ② 날마다 전 종목 첫 5분봉 ③ 거래량 상위 종목의 하루치 5분봉 (이어받기 가능)
  .venv/bin/python apps/quant_wide.py run     # 백테스트 (학습/확인/시험)

대상: NYSE·NASDAQ·AMEX 상장 주식 중 전일 기준 주가 $5 이상, 14일 평균 거래량 100만 주 이상, ATR $0.5 이상.
결과(2026-11-11, 2024-01~2026-11): 둘째 봉 시가 진입 + 손절 0.1 ATR(논문 설정)은 비용 0이면 세 구간 모두 플러스(샤프 2.5/1.4/2.8)지만
     편도 0.02%에서 확인 구간이 0 근처, 0.05%에서는 학습·확인이 마이너스 — 신호는 있고 체결 비용이 관건. 돌파 진입(break)은 5분봉으로는 불리하게 나옴.
체결 비용 실측(2026-11-11): 고른 종목들의 09:35:00 실제 호가 1,028건(60일)에서 반스프레드 중앙값 0.18%(편도). 거래당 총수익은 평균 0.077%라
     시장가로는 왕복 비용(약 0.37%)이 수익의 5배 — 실행 프로그램은 만들지 않음. 무료 실시간 데이터(IEX)로 고르면 종목이 42%만 겹치고 신호도 사라짐.
한계: 지금 상장된 종목만 받음(상장폐지 종목 빠짐). 같은 봉에서 진입가와 손절선에 다 닿으면 손절로 계산(보수적).
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import pickle
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

import traders as T
from quant_lab import PERIODS, stats

WIDE = T.DATA / "lab" / "wide"
NY, UTC = ZoneInfo("America/New_York"), dt.timezone.utc
START = dt.date(2023, 11, 1)
TOPN, NB = 40, 78
_lock, _calls = threading.Lock(), []


def hdr():
    k = T.alpaca_keys()
    return {"APCA-API-KEY-ID": k[0], "APCA-API-SECRET-KEY": k[1]}


def throttle():
    """분당 180회 이하."""
    while True:
        with _lock:
            now = time.time()
            while _calls and now - _calls[0] > 60:
                _calls.pop(0)
            if len(_calls) < 180:
                _calls.append(now)
                return
        time.sleep(0.3)


def bars(syms: list, tf: str, start: str, end: str) -> dict:
    out, token = {}, None
    while True:
        q = {"symbols": ",".join(syms), "timeframe": tf, "start": start, "end": end, "limit": 10000, "feed": "sip",
             "adjustment": "split", "sort": "asc"}
        if token:
            q["page_token"] = token
        throttle()
        try:
            r = requests.get("https://data.alpaca.markets/v2/stocks/bars", params=q, headers=hdr(), timeout=60)
        except requests.RequestException:
            time.sleep(2)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(3)
            continue
        r.raise_for_status()
        js = r.json()
        for s, b in (js.get("bars") or {}).items():
            out.setdefault(s, []).extend(b)
        token = js.get("next_page_token")
        if not token:
            return out


def et(d: dt.date, h: int, m: int) -> str:
    return dt.datetime(d.year, d.month, d.day, h, m, tzinfo=NY).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(name):
    p = WIDE / f"{name}.pkl"
    return pickle.load(open(p, "rb")) if p.exists() else None


def save(name, obj):
    WIDE.mkdir(parents=True, exist_ok=True)
    tmp = WIDE / f"{name}.tmp"
    pickle.dump(obj, open(tmp, "wb"), protocol=5)
    tmp.replace(WIDE / f"{name}.pkl")


# ───────────────────────── 받기 ─────────────────────────
def fetch_daily() -> dict:
    D = load("daily")
    if D:
        return D
    r = requests.get("https://paper-api.alpaca.markets/v2/assets", params={"status": "active", "asset_class": "us_equity"}, headers=hdr(), timeout=60)
    r.raise_for_status()
    syms = sorted(a["symbol"] for a in r.json() if a["exchange"] in ("NYSE", "NASDAQ", "AMEX") and a["tradable"] and a["symbol"].isalpha())
    print(f"종목 {len(syms)}개 일봉 받는 중…", flush=True)
    end = (dt.datetime.now(UTC) - dt.timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    chunks = [syms[i:i + 100] for i in range(0, len(syms), 100)]
    rows = {}
    with ThreadPoolExecutor(4) as ex:
        for i, got in enumerate(ex.map(lambda c: bars(c, "1Day", f"{START}T00:00:00Z", end), chunks)):
            rows.update(got)
            if i % 10 == 0:
                print(f"  일봉 {i + 1}/{len(chunks)}", flush=True)
    fr = {k: {} for k in "ohlcv"}
    for s, b in rows.items():
        idx = [x["t"][:10] for x in b]
        for k in "ohlcv":
            fr[k][s] = pd.Series([x[k] for x in b], index=idx, dtype=float)
    D = {k: pd.DataFrame(v).sort_index() for k, v in fr.items()}
    save("daily", D)
    return D


def universe(D: dict) -> pd.DataFrame:
    """날짜×종목 — 전일까지의 정보로 그날 대상인지."""
    c, h, l, v = D["c"], D["h"], D["l"], D["v"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()]).groupby(level=0).max()
    atr = tr.rolling(14, min_periods=10).mean().shift(1)
    av = v.rolling(14, min_periods=10).mean().shift(1)
    D["atr"], D["pc"] = atr, pc
    return (pc >= 5) & (av >= 1e6) & (atr >= 0.5)


def fetch_first(D: dict, U: pd.DataFrame) -> dict:
    """날마다 대상 종목 전부의 첫 5분봉 (09:30~09:35)."""
    F = load("first") or {}
    days = [d for d in U.index if d >= "2023-12-01" and d not in F]
    print(f"첫 5분봉: 남은 {len(days)}일", flush=True)

    def one(day):
        d = dt.date.fromisoformat(day)
        syms = list(U.columns[U.loc[day].to_numpy()])
        got = {}
        for i in range(0, len(syms), 400):
            got.update(bars(syms[i:i + 400], "5Min", et(d, 9, 30), et(d, 9, 34)))
        return day, {s: (b[0]["o"], b[0]["h"], b[0]["l"], b[0]["c"], b[0]["v"]) for s, b in got.items() if b[0]["t"][11:16] == et(d, 9, 30)[11:16]}

    with ThreadPoolExecutor(4) as ex:
        for i, (day, got) in enumerate(ex.map(one, days)):
            F[day] = got
            if i % 25 == 0:
                print(f"  첫 봉 {i + 1}/{len(days)} ({day}, {len(got)}종목)", flush=True)
                save("first", F)
    save("first", F)
    return F


def rank(F: dict) -> dict:
    """날마다 상대 거래량(첫 5분 거래량 ÷ 최근 14일 첫 5분 거래량 평균) 상위 TOPN."""
    days = sorted(F)
    v0 = pd.DataFrame({d: {s: x[4] for s, x in F[d].items()} for d in days}).T
    rv = v0 / v0.rolling(14, min_periods=5).mean().shift(1)
    out = {}
    for d in days[5:]:
        r = rv.loc[d].dropna()
        r = r[r >= 1.0].sort_values(ascending=False).head(TOPN)
        out[d] = [(s, float(x)) for s, x in r.items() if F[d][s][3] != F[d][s][0]]
    return out


def fetch_intraday(R: dict) -> dict:
    I = load("intraday") or {}
    days = [d for d in sorted(R) if d not in I]
    print(f"하루치 5분봉: 남은 {len(days)}일", flush=True)

    def one(day):
        d = dt.date.fromisoformat(day)
        got = bars([s for s, _ in R[day]], "5Min", et(d, 9, 30), et(d, 15, 59)) if R[day] else {}
        o = {}
        for s, b in got.items():
            a = np.full((NB, 4), np.nan)
            for x in b:
                t = dt.datetime.fromisoformat(x["t"].replace("Z", "+00:00")).astimezone(NY)
                i = (t.hour * 60 + t.minute - 570) // 5
                if 0 <= i < NB:
                    a[i] = (x["o"], x["h"], x["l"], x["c"])
            o[s] = a
        return day, o

    with ThreadPoolExecutor(4) as ex:
        for i, (day, got) in enumerate(ex.map(one, days)):
            I[day] = got
            if i % 50 == 0:
                print(f"  5분봉 {i + 1}/{len(days)} ({day})", flush=True)
                save("intraday", I)
    save("intraday", I)
    return I


# ───────────────────────── 백테스트 ─────────────────────────
def panel():
    D = load("daily")
    universe(D)
    R, I = rank(load("first")), load("intraday")
    days = [d for d in sorted(R) if d in I and d >= "2024-01-01"]
    n = len(days)
    B = np.full((TOPN, n, NB, 4), np.nan)
    rv, atr = np.full((TOPN, n), np.nan), np.full((TOPN, n), np.nan)
    for j, d in enumerate(days):
        for i, (s, x) in enumerate(R[d]):
            a = I[d].get(s)
            if a is None or np.isnan(a[0, 0]):
                continue
            B[i, j], rv[i, j], atr[i, j] = a, x, D["atr"].at[d, s]
    last = np.full((TOPN, n), np.nan)  # 조기 폐장·빈 봉 → 마지막으로 거래된 가격으로 마감 청산
    for t in range(NB):
        c = B[:, :, t, 3]
        last = np.where(np.isnan(c), last, c)
    return days, B, rv, atr, last


def orb(P, K=20, rvmin=1.0, stop=0.1, risk=0.01, lev=1.0, long_only=False, entry="open", cost=0.0005):
    """entry=open: 둘째 봉 시가에 바로 진입(논문 방식 — 봉 안에서 진입·손절 순서가 분명함) / break: 첫 봉 고가·저가 돌파 시 진입."""
    days, B, rv, atr, last = P
    O, H, L, C = (B[:, :, :, i] for i in range(4))
    dirn = np.sign(C[:, :, 0] - O[:, :, 0])
    if long_only:
        dirn = np.maximum(dirn, 0)
    okc = (rv >= rvmin) & (dirn != 0) & ~np.isnan(atr)
    order = np.cumsum(okc, axis=0)  # 이미 상대 거래량 순으로 정렬돼 있음
    pick = okc & (order <= K)
    lvl = np.where(dirn > 0, H[:, :, 0], L[:, :, 0])
    sd = stop * atr
    ent, ex = np.full(pick.shape, np.nan), np.full(pick.shape, np.nan)
    if entry == "open":
        ent = np.where(pick & ~np.isnan(O[:, :, 1]), O[:, :, 1], np.nan)
    for t in range(1, NB):
        o, h, l = O[:, :, t], H[:, :, t], L[:, :, t]
        has = ~np.isnan(o)
        live = ~np.isnan(ent) & np.isnan(ex) & has
        stp = ent - dirn * sd
        hit = live & np.where(dirn > 0, l <= stp, h >= stp)
        ex[hit] = np.where(dirn > 0, np.minimum(stp, o), np.maximum(stp, o))[hit]
        if entry == "break" and t < NB - 6:
            trig = pick & has & np.isnan(ent) & np.where(dirn > 0, h >= lvl, l <= lvl)
            ent[trig] = np.where(dirn > 0, np.maximum(lvl, o), np.minimum(lvl, o))[trig]
            stp = ent - dirn * sd
            hit = trig & np.where(dirn > 0, l <= stp, h >= stp)
            ex[hit] = stp[hit]
    took = ~np.isnan(ent)
    ex = np.where(took & np.isnan(ex), last, ex)
    w = np.minimum(risk / (sd / ent), lev / K)
    pnl = np.where(took, w * (dirn * (ex / ent - 1) - 2 * cost), 0)
    return np.nansum(pnl, 0), took.sum(0)


def run():
    P = panel()
    days = P[0]
    dts = pd.to_datetime(days)
    mask = {k: np.asarray((dts >= a) & (dts <= b)) for k, (a, b) in PERIODS.items()}
    print(f"{len(days)}거래일 ({days[0]} ~ {days[-1]}) · 하루 후보 평균 {np.mean((~np.isnan(P[2])).sum(0)):.0f}종목")
    G = [dict(zip("K rvmin stop long_only lev entry".split(), v)) for v in
         itertools.product([5, 10, 20], [1.0, 2.0, 3.0], [0.1, 0.25, 0.5, 1.0], [False, True], [1.0], ["open", "break"])]
    rows = []
    for g in G:
        r, n = orb(P, **g)
        rows.append(dict(p=g, **{k: stats(r, n, m) for k, m in mask.items()}))
    rows.sort(key=lambda x: -x["학습"]["sharpe"])
    pos = {k: np.mean([x[k]["ret"] > 0 for x in rows]) for k in mask}
    print(f"설정 {len(G)}개 · 플러스 비율: " + " · ".join(f"{k} {v * 100:.0f}%" for k, v in pos.items()))
    for x in rows[:8]:
        print(" ", x["p"], " | ".join(f"{k} {x[k]['ret'] * 100:+.1f}% 샤프 {x[k]['sharpe']:+.2f} 낙폭 {x[k]['dd'] * 100:.0f}%" for k in mask), f"하루 {x['시험']['tpd']:.1f}건")
    b = rows[0]
    for c in (0.0002, 0.0):
        r, n = orb(P, **b["p"], cost=c)
        print(f"  학습 최고, 비용 {c * 100:.2f}%: " + " | ".join(f"{k} {stats(r, n, m)['ret'] * 100:+.1f}%" for k, m in mask.items()))
    for e in ("open", "break"):
        sub = [x for x in rows if x["p"]["entry"] == e]
        print(f"  진입 {e}: 플러스 비율 " + " · ".join(f"{k} {np.mean([x[k]['ret'] > 0 for x in sub]) * 100:.0f}%" for k in mask))
    paper = dict(K=20, rvmin=1.0, stop=0.1, long_only=False, lev=4.0, entry="open")
    for c in (0.0005, 0.0002, 0.0):
        r, n = orb(P, **paper, cost=c)
        print(f"  논문 설정(상위 20, 손절 0.1 ATR, 레버리지 4배), 비용 {c * 100:.2f}%: " + " | ".join(f"{k} {stats(r, n, m)['ret'] * 100:+.1f}% 샤프 {stats(r, n, m)['sharpe']:+.2f} 낙폭 {stats(r, n, m)['dd'] * 100:.0f}%" for k, m in mask.items()))
    (WIDE / "orb_result.json").write_text(json.dumps(dict(rows=rows, pos=pos), ensure_ascii=False, indent=1, default=float))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "fetch":
        D = fetch_daily()
        U = universe(D)
        print(f"일봉 {D['c'].shape} · 하루 대상 평균 {U.sum(1).mean():.0f}종목", flush=True)
        F = fetch_first(D, U)
        R = rank(F)
        fetch_intraday(R)
        print("받기 끝", flush=True)
    else:
        run()
