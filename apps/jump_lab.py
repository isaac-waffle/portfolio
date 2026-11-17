"""급등 추종(틱의 규칙)을 여러 종목에 — MRNA 한 종목에서 나온 결과가 우연인지 다른 종목으로 확인하고, 통하면 여러 종목을 보는 트레이더로.

  .venv/bin/python apps/jump_lab.py fetch     # 관심종목 1분봉(2024-01~) → journal/traders/lab/jump/<종목>.npz
  .venv/bin/python apps/jump_lab.py           # 검증 → 표 + journal/traders/lab/jump_result.json

규칙 (mrna_lab.tick_run의 mom을 1분봉으로 옮긴 것 — 실전 엔진이 30초마다 받는 1분봉과 같은 걸 봄)
  1분 종가가 '직전 W분 종가 평균 × (1 + k·u)' 위로 올라서면(직전 봉은 그 아래) 다음 1분 시가에 시장가 매수. u = 평소 1분 변동폭(직전 14일) × √W.
  손절 = 진입가 − st·u (봉 안 저가로 확인, 시가가 이미 아래면 시가). 익절선 없음. 15:50 ET 전량 정리. 09:35~15:40 사이에만 삼.
비용: 편도 슬리피지 0.05% + 주문당 $2 ($20,000 기준 0.01%) = 왕복 0.12%. 롱만.
검증: 학습(24.01~25.06) → 확인(25.07~12) → 시험(26.01~). 핵심은 틱이 쓰는 값(W=3·k=4·st=6)을 그대로 다른 종목에 대 보는 것 — 고르는 과정이 없음.

결과 (2026-11-17, 45종목 · 716일) — 통과 못 함, 트레이더로 안 만듦
  틱의 값 그대로 종목마다 따로: MRNA를 뺀 44종목 4,569건의 거래당 평균 +1.6bp (t=0.42, 승률 34%) — 0과 구별 안 됨.
    플러스인 종목 수: 학습 22/44 · 확인 21/44 · 시험 13/44. 세 기간 모두 플러스는 IONQ·AMD 둘뿐.
  변동성 큰 종목(IONQ·SMCI·IREN·NBIS…)에서만 통하는 것처럼 보여 '그날 변동성 순위(전날까지 정보)'로 나눠 봄:
    상위 1~5위 거래당 학습 +49bp · 확인 −30bp · 시험 +30bp, 6~10위 +30 · −44 · +23 — 확인 기간에 뒤집힘.
  여러 종목 트레이더(한 번에 한 종목): 전체·거래대금 상위 30/15·변동성 상위 3/5/8/10 × 설정 24개 중 세 기간 모두 플러스는 1개(W=10·k=5, 학습 +5% 낙폭 −39%, 이웃은 마이너스).
  → 틱의 MRNA 성적(+41%·+4%·+20%)은 다른 종목으로 뒷받침되지 않음. 운이 섞였을 가능성이 큼. 같은 계열을 근거 없이 다시 돌리지 말 것.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

import quant_wide as W
import traders as T

DIR = T.DATA / "lab" / "jump"
START = "2024-01-02"
PERIODS = {"학습": ("2024-01-01", "2025-06-30"), "확인": ("2025-07-01", "2025-12-31"), "시험": ("2026-01-01", "2026-12-31")}
NB, FLAT = 390, 380  # 380 = 15:50
COST = T.SLIP + T.FEE / T.START_CASH  # 편도
LIVE = dict(W=3, k=4.0, st=6.0)  # 틱(v11)과 같은 값


# ───────────────────────── 데이터 ─────────────────────────
def fetch_one(sym: str, end: str) -> int:
    """한 종목의 정규장 1분봉을 (날짜 서수, 분, 시·고·저·종·거래량) 배열로 저장."""
    parts, token = [], None
    while True:
        q = dict(symbols=sym, timeframe="1Min", start=START, end=end, limit=10000, feed="sip", adjustment="split", sort="asc")
        if token:
            q["page_token"] = token
        W.throttle()
        try:
            r = requests.get("https://data.alpaca.markets/v2/stocks/bars", params=q, headers=W.hdr(), timeout=60)
        except requests.RequestException:
            continue
        if r.status_code == 429 or r.status_code >= 500:
            continue
        r.raise_for_status()
        js = r.json()
        b = (js.get("bars") or {}).get(sym) or []
        if b:
            ts = pd.DatetimeIndex(pd.to_datetime([x["t"] for x in b], utc=True)).tz_convert(T.NY)
            m = ts.hour * 60 + ts.minute
            ok = (m >= 570) & (m < 960)
            day = np.array([d.toordinal() for d in ts.date])
            parts.append(np.c_[day, m - 570, [[x["o"], x["h"], x["l"], x["c"], x["v"]] for x in b]][ok])
        token = js.get("next_page_token")
        if not token:
            break
    A = np.vstack(parts) if parts else np.zeros((0, 7))
    np.savez_compressed(DIR / f"{sym}.npz", a=A)
    return len(A)


def fetch(syms: list):
    DIR.mkdir(parents=True, exist_ok=True)
    end = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")  # 최근 15분은 무료 계정에 안 줌
    todo = [s for s in syms if not (DIR / f"{s}.npz").exists()]
    with ThreadPoolExecutor(4) as ex:
        for s, n in zip(todo, ex.map(lambda s: fetch_one(s, end), todo)):
            print(f"{s} {n:,}개", flush=True)


class Sym:
    """한 종목의 날짜 × 분(390) 배열. 빈 봉은 직전 종가로 메움. 하루 봉이 300개 미만인 날(상장 전·거래 뜸)은 제외."""

    def __init__(self, sym: str, days: list):
        A = np.load(DIR / f"{sym}.npz")["a"]
        ix = {d.toordinal(): i for i, d in enumerate(days)}
        di = np.array([ix.get(int(x), -1) for x in A[:, 0]])
        A, di = A[di >= 0], di[di >= 0]
        bi = A[:, 1].astype(int)
        O, H, L, C, V = (np.full((len(days), NB), np.nan) for _ in range(5))
        for X, j in ((O, 2), (H, 3), (L, 4), (C, 5), (V, 6)):
            X[di, bi] = A[:, j]
        full = (~np.isnan(C)).sum(1) >= 300
        for t in range(NB):
            m = np.isnan(C[:, t])
            C[m, t] = C[m, t - 1] if t else O[m, min(t + 1, NB - 1)]
            for X in (O, H, L):
                X[m, t] = C[m, t]
        self.sym, self.O, self.H, self.L, self.C = sym, O, H, L, C
        with np.errstate(invalid="ignore", divide="ignore"):
            sd = np.where(full, np.diff(np.log(C), axis=1).std(axis=1), np.nan)
        self.s1 = pd.Series(sd).rolling(14, min_periods=7).mean().shift(1).to_numpy()  # 평소 1분 변동폭 (직전 14일)
        self.ok = full & np.isfinite(self.s1) & (self.s1 > 0)
        self.dvol = np.where(full, np.nansum(np.nan_to_num(V) * C, 1), np.nan)


def calendar() -> list:
    """거래일 = AAPL에 1분봉이 380개 이상 있는 날 (조기 폐장일 제외)."""
    A = np.load(DIR / "AAPL.npz")["a"]
    d, n = np.unique(A[:, 0].astype(int), return_counts=True)
    return [dt.date.fromordinal(int(x)) for x in d[n >= 380]]


def masks(days: list) -> dict:
    dts = pd.to_datetime(days)
    return {k: np.asarray((dts >= a) & (dts <= b)) for k, (a, b) in PERIODS.items()}


# ───────────────────────── 규칙 ─────────────────────────
def hits(s: Sym, W=3, k=4.0) -> tuple[np.ndarray, np.ndarray]:
    """t분 종가에 확인되는 신호와 그 세기(z). 평균·밴드는 직전 W분 종가만 씀 — 미래 정보 없음."""
    C = s.C
    ref = np.full(C.shape, np.nan)
    ref[:, W:] = pd.DataFrame(C.T).rolling(W).mean().to_numpy().T[:, W - 1:-1]
    with np.errstate(invalid="ignore"):
        z = (C / ref - 1) / (s.s1[:, None] * np.sqrt(W))
        hit = z > k
    hit[:, 1:] &= ~hit[:, :-1]  # 밴드 위로 '올라선' 봉만
    hit[:, :max(5, W)] = False
    hit[:, 370:] = False
    return hit & s.ok[:, None], z


def exit_of(s: Sym, i: int, e: int, W: int, st: float) -> tuple[int, float]:
    """e분 시가에 산 포지션이 언제 얼마에 나가는지."""
    px = s.O[i, e]
    line = px - st * px * s.s1[i] * np.sqrt(W)
    lo = np.flatnonzero(s.L[i, e:FLAT] <= line)
    if len(lo):
        x = e + lo[0]
        return x, min(s.O[i, x], line)
    return FLAT, s.O[i, FLAT]


def solo(s: Sym, W=3, k=4.0, st=6.0, cost=COST):
    """한 종목만 보는 경우 (틱과 같은 방식). 하루 수익률, 하루 거래 수, 거래별 수익률."""
    hit, _ = hits(s, W, k)
    D = len(s.ok)
    day_r, day_n, rets = np.zeros(D), np.zeros(D), []
    for i in np.flatnonzero(hit.any(1)):
        free, eq = 0, 1.0
        for t in np.flatnonzero(hit[i]):
            e = t + 1
            if e < free:
                continue
            x, out = exit_of(s, i, e, W, st)
            r = out / s.O[i, e] * (1 - cost) / (1 + cost) - 1
            eq *= 1 + r
            rets.append(r)
            day_n[i] += 1
            free = x + 1
        day_r[i] = eq - 1
    return day_r, day_n, np.array(rets)


def multi(S: list, W=3, k=4.0, st=6.0, cost=COST, liquid: np.ndarray | None = None):
    """여러 종목을 보면서 한 번에 한 종목만 계좌 전부로. 같은 분에 신호가 여럿이면 가장 세게 튄 종목. 손절로 나오면 다음 신호를 다시 받음.
    liquid: 종목 × 날짜 — 그날 살 수 있는 종목인지 (전날까지의 거래대금으로 정함)."""
    D = len(S[0].ok)
    H = [hits(s, W, k) for s in S]
    day_r, day_n, rets = np.zeros(D), np.zeros(D), []
    for i in range(D):
        ev = sorted((int(t), -float(z[i, t]), j) for j, (h, z) in enumerate(H) if liquid is None or liquid[j, i] for t in np.flatnonzero(h[i]))
        free, eq = 0, 1.0
        for t, _, j in ev:
            e = t + 1
            if e < free:
                continue
            x, out = exit_of(S[j], i, e, W, st)
            r = out / S[j].O[i, e] * (1 - cost) / (1 + cost) - 1
            eq *= 1 + r
            rets.append((i, j, r))
            day_n[i] += 1
            free = x + 1
        day_r[i] = eq - 1
    return day_r, day_n, rets


def stats(r: np.ndarray, n: np.ndarray, m: np.ndarray) -> dict:
    x = r[m]
    eq = np.cumprod(1 + x)
    sd = x.std()
    return dict(ret=float(eq[-1] - 1), sharpe=float(x.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0,
                dd=float((eq / np.maximum.accumulate(eq) - 1).min()), tpd=float(n[m].mean()), n=int(n[m].sum()))


def liquid_mask(S: list, top: int) -> np.ndarray:
    """날마다 직전 20일 평균 거래대금 상위 top개 (전날까지의 정보만)."""
    dv = np.array([pd.Series(s.dvol).rolling(20, min_periods=5).mean().shift(1).to_numpy() for s in S])
    rk = (-np.nan_to_num(dv, nan=-1.0)).argsort(0).argsort(0)
    return (rk < top) & np.isfinite(dv)


GRID = [dict(W=w, k=k, st=st) for w, k, st in itertools.product((3, 10, 30), (2.0, 3.0, 4.0, 5.0), (3.0, 6.0))]


def line(r, n, M) -> str:
    return " | ".join(f"{k} {stats(r, n, m)['ret'] * 100:+6.1f}% S{stats(r, n, m)['sharpe']:+.2f} 낙폭{stats(r, n, m)['dd'] * 100:4.0f}% {stats(r, n, m)['n']}건" for k, m in M.items())


def main():
    days = calendar()
    M = masks(days)
    S = [Sym(p.stem, days) for p in sorted(DIR.glob("*.npz"))]
    S = [s for s in S if s.ok.sum() >= 120]
    print(f"{len(S)}종목 · {days[0]} ~ {days[-1]} ({len(days)}일) · 편도 비용 {COST * 100:.3f}%")
    out = dict(solo={}, multi=[])

    print(f"\n■ 틱이 쓰는 값 그대로({LIVE}) 종목마다 따로 — 고르는 과정 없음")
    tab = []
    for s in S:
        r, n, tr = solo(s, **LIVE)
        r0, _, _ = solo(s, **LIVE, cost=0.0)
        st = {k: stats(r, n, m) for k, m in M.items()}
        tab.append((s.sym, st, {k: stats(r0, n, m)["ret"] for k, m in M.items()}, len(tr), float(tr.mean()) if len(tr) else 0.0))
        out["solo"][s.sym] = st
    for sym, st, g, n, avg in sorted(tab, key=lambda x: -sum(x[1][k]["ret"] for k in PERIODS)):
        print(f"  {sym:5s} " + " | ".join(f"{k} {st[k]['ret'] * 100:+6.1f}% ({st[k]['n']:3d}건)" for k in PERIODS) + f" | 거래당 평균 {avg * 1e4:+5.0f}bp")
    others = [x for x in tab if x[0] != "MRNA"]
    for k in PERIODS:
        act = [x for x in others if x[1][k]["n"] > 0]
        print(f"  {k}: MRNA 빼고 {len(act)}종목 중 플러스 {sum(x[1][k]['ret'] > 0 for x in act)}개 · 중앙값 {np.median([x[1][k]['ret'] for x in act]) * 100:+.1f}% · "
              f"비용 0이면 플러스 {sum(x[2][k] > 0 for x in act)}개")
    allr = np.concatenate([solo(s, **LIVE)[2] for s in S if s.sym != "MRNA"])
    print(f"  MRNA 빼고 전체 거래 {len(allr)}건: 평균 {allr.mean() * 1e4:+.1f}bp · 승률 {np.mean(allr > 0) * 100:.0f}% · t={allr.mean() / allr.std() * np.sqrt(len(allr)):+.2f}")

    print("\n■ 여러 종목을 보는 트레이더 (한 번에 한 종목, 계좌 전부)")
    for top in (None, 30, 15):
        liq = liquid_mask(S, top) if top else None
        for g in GRID:
            r, n, tr = multi(S, **g, liquid=liq)
            row = dict(p=g, top=top, **{k: stats(r, n, m) for k, m in M.items()})
            r2, _, _ = multi(S, **g, liquid=liq, cost=COST * 2)
            row["x2"] = {k: stats(r2, n, m)["ret"] for k, m in M.items()}
            out["multi"].append(row)
        rs = [w for w in out["multi"] if w["top"] == top]
        allp = [w for w in rs if all(w[k]["ret"] > 0 for k in PERIODS)]
        print(f"\n  [거래대금 상위 {top or '전체'}] 설정 {len(rs)}개 — 학습 플러스 {sum(w['학습']['ret'] > 0 for w in rs)} · 학습·확인 플러스 "
              f"{sum(w['학습']['ret'] > 0 and w['확인']['ret'] > 0 for w in rs)} · 세 기간 모두 플러스 {len(allp)}")
        for w in sorted(rs, key=lambda w: -w["학습"]["sharpe"])[:6] + [w for w in rs if w["p"] == LIVE]:
            print(f"   {w['p']}{' ← 틱과 같은 값' if w['p'] == LIVE else ''}\n      " + " | ".join(
                f"{k} {w[k]['ret'] * 100:+6.1f}% S{w[k]['sharpe']:+.2f} 낙폭{w[k]['dd'] * 100:4.0f}% {w[k]['tpd']:.2f}건/일 (비용 2배 {w['x2'][k] * 100:+.0f}%)" for k in PERIODS))
    (T.DATA / "lab" / "jump_result.json").write_text(json.dumps(out, ensure_ascii=False, default=float))


if __name__ == "__main__":
    fetch(T.WATCHLIST) if sys.argv[1:] == ["fetch"] else main()
