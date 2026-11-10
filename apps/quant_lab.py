"""분봉 퀀트 단타 전략 탐색 — 차트 패턴이 아니라 통계적 규칙 7종을 같은 5분봉(Alpaca SIP, traders_lab 캐시)으로 검증.

  .venv/bin/python apps/quant_lab.py            # 전부 돌리고 표 출력 + journal/traders/lab/quant_result.json
  .venv/bin/python apps/quant_lab.py noise gap  # 일부만

전략군
  noise   장중 모멘텀 — 가격이 '평소 이 시각 변동폭' 밖으로 나가면 그 방향으로 탐 (지수·종목)
  xs      횡단면 — 특정 시각에 종목들을 장중 수익률로 줄 세워 반전(진 놈 매수) 또는 추세(이긴 놈 매수)
  last30  마감 30분 — 그날 15:30까지의 방향이 마지막 30분에 이어지는지
  pairs   페어 — 같이 움직이는 두 종목의 간격(z점수)이 벌어지면 좁혀지는 쪽에 베팅
  gap     시가 갭 — 메우는 쪽(fade) / 이어지는 쪽(go)
  orb     거래량 터진 종목의 첫 5분봉 돌파
  ml      여러 신호를 선형모형으로 묶어 30분 뒤 수익률 예측

검증: 학습(2024-01~2025-06)에서 샤프가 가장 높은 설정을 고름 → 확인(2025-07~12)에서 플러스인지 →
     손대지 않은 최종 시험(2026-01~10) 결과를 그대로 보고. 군별로 '시험 기간에 플러스인 설정 비율'도 같이 봄(우연 여부).
가정: 신호는 봉 종가에 계산, 다음 봉 시가에 체결. 비용은 편도 COST(기본 0.05%, 수수료 $0 — Alpaca 기준). 레버리지 없음(총 노출 ≤ 100%).
     조기 폐장일은 제외. 공매도 가능(롱 전용 버전도 따로 봄).
"""
from __future__ import annotations

import itertools
import json
import pickle
import sys
import warnings

import numpy as np
import pandas as pd

import traders as T

warnings.filterwarnings("ignore")
LAB = T.DATA / "lab"
NB = 78  # 하루 5분봉 수
COST = 0.0005
PERIODS = {"학습": ("2024-01-01", "2025-06-30"), "확인": ("2025-07-01", "2025-12-31"), "시험": ("2026-01-01", "2026-12-31")}


# ───────────────────────── 데이터 ─────────────────────────
class Data:
    def __init__(self):
        raw = pickle.load(open(LAB / "cache.pkl", "rb"))["bars"]
        spy = raw["SPY"]
        cnt = pd.Series(1, index=spy.index).groupby(spy.index.date).sum()
        self.days = [d for d, n in cnt.items() if n >= NB]
        didx = {d: i for i, d in enumerate(self.days)}
        self.syms = sorted(raw)
        S, D = len(self.syms), len(self.days)
        A = {k: np.full((S, D, NB), np.nan) for k in "OHLCV"}
        for si, s in enumerate(self.syms):
            df = raw[s]
            di = np.array([didx.get(d, -1) for d in df.index.date])
            bi = ((df.index.hour * 60 + df.index.minute - 570) // 5).to_numpy()
            ok = (di >= 0) & (bi >= 0) & (bi < NB)
            for k, col in zip("OHLCV", ["Open", "High", "Low", "Close", "Volume"]):
                A[k][si, di[ok], bi[ok]] = df[col].to_numpy()[ok]
        O, H, L, C, V = (A[k] for k in "OHLCV")
        have = ~np.isnan(C)
        self.valid = (have.sum(2) >= 70) & have[:, :, 0]  # 그날 거래 가능한 종목
        for t in range(1, NB):  # 빈 봉은 직전 종가로 메움 (거래량 0)
            m = np.isnan(C[:, :, t])
            C[:, :, t][m] = C[:, :, t - 1][m]
            for X in (O, H, L):
                X[:, :, t][m] = C[:, :, t - 1][m]
            V[:, :, t][m] = 0
        for X in (O, H, L, C, V):
            X[~self.valid] = np.nan
        self.O, self.H, self.L, self.C, self.V = O, H, L, C, V
        dc = C[:, :, -1]
        self.PC = np.roll(dc, 1, axis=1)
        self.PC[:, 0] = np.nan
        tr = np.nanmax(np.stack([np.nanmax(H, 2) - np.nanmin(L, 2), abs(np.nanmax(H, 2) - self.PC), abs(np.nanmin(L, 2) - self.PC)]), 0)
        self.ATR = lagmean(tr, 14)
        self.ATRP = self.ATR / self.PC
        self.ADV = lagmean(np.nansum(C * V, 2), 20)
        self.liquid = self.valid & (self.ADV >= 2e7) & (self.PC >= 5) & ~np.isnan(self.ATRP)
        dts = pd.to_datetime(self.days)
        self.mask = {k: np.asarray((dts >= a) & (dts <= b)) for k, (a, b) in PERIODS.items()}
        self.ix = {s: i for i, s in enumerate(self.syms)}
        cumv = np.cumsum(np.nan_to_num(V), 2)
        self.VWAP = np.cumsum(np.nan_to_num((H + L + C) / 3 * V), 2) / np.where(cumv > 0, cumv, np.nan)
        self.CUMV = cumv


def lagmean(X: np.ndarray, n: int) -> np.ndarray:
    """날짜축(axis=1) 기준 직전 n일 평균 (당일 제외 — 미래 정보 차단)."""
    sh = X.shape
    Y = np.moveaxis(X, 1, 0).reshape(sh[1], -1)
    R = pd.DataFrame(Y).rolling(n, min_periods=max(3, n // 2)).mean().shift(1).to_numpy()
    return np.moveaxis(R.reshape((sh[1],) + sh[:1] + sh[2:]), 0, 1)


def stats(r: np.ndarray, ntr: np.ndarray, m: np.ndarray) -> dict:
    x, n = r[m], ntr[m]
    if len(x) == 0:
        return dict(ret=0, sharpe=0, dd=0, tpd=0)
    eq = np.cumprod(1 + x)
    sd = x.std()
    return dict(ret=float(eq[-1] - 1), sharpe=float(x.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0,
                dd=float((eq / np.maximum.accumulate(eq) - 1).min()), tpd=float(n.mean()))


# ───────────────────────── 전략군 ─────────────────────────
def noise(d: Data, univ="idx", k=1.0, step=6, long_only=False, vstop=True, cost=COST):
    """장중 모멘텀 (noise area). 상단 = max(시가, 전일종가)×(1+k·σ_t), σ_t = 최근 14일 같은 시각의 |시가 대비 변동| 평균."""
    sel = [d.ix[s] for s in (["SPY", "QQQ"] if univ == "idx" else d.syms if univ == "all" else [univ])]
    O, C, VW = d.O[sel], d.C[sel], d.VWAP[sel]
    o0, pc = O[:, :, 0], d.PC[sel]
    sig = lagmean(abs(C / o0[:, :, None] - 1), 14)
    up = np.fmax(o0, pc)[:, :, None] * (1 + k * sig)
    dn = np.fmin(o0, pc)[:, :, None] * (1 - k * sig)
    okd = (d.valid[sel] if univ == "idx" else d.liquid[sel]) & ~np.isnan(sig[:, :, 0])
    pos = np.zeros(o0.shape)
    pnl, ntr = np.zeros(o0.shape), np.zeros(o0.shape)
    for t in range(NB):
        nxt = O[:, :, t + 1] if t < NB - 1 else C[:, :, -1]
        pnl += pos * np.nan_to_num(nxt / O[:, :, t] - 1)
        if t == NB - 1:
            new = np.zeros_like(pos)
        elif (t + 1) % step == 0:
            c, u, l, vw = C[:, :, t], up[:, :, t], dn[:, :, t], VW[:, :, t]
            lu = np.fmax(u, vw) if vstop else u
            sl = np.fmin(l, vw) if vstop else l
            new = np.where((pos > 0) & (c < lu), 0, np.where((pos < 0) & (c > sl), 0, pos))
            new = np.where(c > u, 1, np.where((c < l) & (not long_only), -1, new))
            new = np.where(okd, new, 0)
        else:
            continue
        ch = abs(new - pos)
        pnl -= ch * cost
        ntr += ch > 0
        pos = new
    n = np.maximum(okd.sum(0), 1)
    return (pnl * okd).sum(0) / n, (ntr * okd).sum(0) / 2


def xs(d: Data, t0=5, look=0, hold=99, q=5, mode="rev", long_only=False, resid=True, cost=COST):
    """횡단면 반전/추세. t0번 봉 종가에 순위 → 다음 봉 시가 진입 → hold봉 뒤 종가(또는 마감) 청산."""
    C, O = d.C, d.O
    ref = O[:, :, 0] if look == 0 or t0 < look else C[:, :, t0 - look]  # 봉이 모자라면 시가 기준
    r = (C[:, :, t0] / ref - 1)
    if resid:
        r = r - r[d.ix["SPY"]]
    z = np.where(d.liquid, r / d.ATRP, np.nan)
    t1 = min(t0 + hold, NB - 1)
    fwd = C[:, :, t1] / O[:, :, t0 + 1] - 1
    if mode == "mom":
        z = -z
    rk = np.argsort(np.argsort(np.where(np.isnan(z), np.inf, z), axis=0), axis=0)  # 0 = 가장 많이 빠진 종목
    nv = (~np.isnan(z)).sum(0)
    lo = (rk < q) & ~np.isnan(z)
    hi = (rk >= nv - q) & ~np.isnan(z)
    enough = nv >= 4 * q
    lr = np.where(lo, np.nan_to_num(fwd), 0).sum(0) / np.maximum(lo.sum(0), 1)
    sr = np.where(hi, np.nan_to_num(fwd), 0).sum(0) / np.maximum(hi.sum(0), 1)
    out = (lr - 2 * cost) if long_only else ((lr - sr) / 2 - 2 * cost)
    return np.where(enough, out, 0), np.where(enough, q * (1 if long_only else 2), 0)


def last30(d: Data, univ="idx", t0=71, th=0.0, long_only=False, src="day", cost=COST):
    """마감 30분 모멘텀. 15:30까지의 수익률(src=day: 전일 종가 대비, open: 시가 대비, first: 첫 30분) 부호 방향으로 마지막 구간을 탐."""
    sel = [d.ix[s] for s in (["SPY", "QQQ"] if univ == "idx" else d.syms)]
    C, O = d.C[sel], d.O[sel]
    r = {"day": C[:, :, t0] / d.PC[sel] - 1, "open": C[:, :, t0] / O[:, :, 0] - 1, "first": C[:, :, 5] / d.PC[sel] - 1}[src]
    okd = (d.valid[sel] if univ == "idx" else d.liquid[sel])
    z = r / d.ATRP[sel]
    side = np.where(z > th, 1, np.where((z < -th) & (not long_only), -1, 0)) * okd
    side = np.nan_to_num(side)
    fwd = np.nan_to_num(C[:, :, -1] / O[:, :, t0 + 1] - 1)
    n = np.maximum(okd.sum(0), 1)
    return (side * fwd - abs(side) * 2 * cost).sum(0) / n, abs(side).sum(0)


def pick_pairs(d: Data, n=15) -> list:
    """학습 기간 5분 수익률 상관이 높은 순으로 n쌍 (지수 ETF는 제외)."""
    m = d.mask["학습"]
    R = np.diff(np.log(d.C[:, m, :]), axis=2).reshape(len(d.syms), -1)
    full = np.isnan(R).mean(1) < 0.05
    cc = pd.DataFrame(R.T).corr(min_periods=5000).to_numpy()
    out = []
    for i, j in itertools.combinations(range(len(d.syms)), 2):
        if full[i] and full[j] and not {d.syms[i], d.syms[j]} & {"SPY", "QQQ", "IWM"} and not np.isnan(cc[i, j]):
            out.append((cc[i, j], i, j))
    out.sort(reverse=True)
    return [(i, j) for _, i, j in out[:n]]


def pairs(d: Data, P: list, W=390, ze=2.0, zx=0.5, zs=4.0, overnight=False, cost=COST):
    """페어. 로그 가격비의 W봉 이동 z점수. |z|>ze 진입(벌어진 쪽 반대로), |z|<zx 청산, |z|>zs 손절. overnight=False면 마감 전 전부 청산."""
    D = len(d.days)
    a, b = np.array([p[0] for p in P]), np.array([p[1] for p in P])
    flat = lambda X: np.moveaxis(X, 0, 2).reshape(D * NB, -1)  # (시간, 종목)
    Cf, Of = flat(d.C), flat(d.O)
    sp = pd.DataFrame(np.log(Cf[:, a] / Cf[:, b]))
    z = ((sp - sp.rolling(W, min_periods=W).mean()) / sp.rolling(W, min_periods=W).std()).to_numpy()
    pos = np.zeros(len(P))
    day_p, day_n = np.zeros(D), np.zeros(D)
    for i in range(D * NB):
        di, t = divmod(i, NB)
        if t < NB - 1:
            nxa, nxb = Of[i + 1, a], Of[i + 1, b]
        else:
            nxa, nxb = Cf[i, a], Cf[i, b]
        day_p[di] += np.nansum(pos * ((nxa / Of[i, a] - 1) - (nxb / Of[i, b] - 1)) / 2)
        if t == NB - 1:
            if overnight:  # 종가→다음 시가 구간
                if di + 1 < D:
                    j = i + 1
                    day_p[di + 1] += np.nansum(pos * ((Of[j, a] / Cf[i, a] - 1) - (Of[j, b] / Cf[i, b] - 1)) / 2)
                continue
            new = np.zeros_like(pos)
        else:
            zi = z[i]
            new = np.where(abs(zi) < zx, 0, pos)
            new = np.where(abs(zi) > zs, 0, new)
            can = (t >= 2) & (t < NB - 7)
            new = np.where((pos == 0) & can & (zi > ze) & (zi < zs), -1, new)
            new = np.where((pos == 0) & can & (zi < -ze) & (zi > -zs), 1, new)
            new = np.where(np.isnan(zi), 0, new)
        ch = abs(new - pos)
        day_p[di] -= (ch * cost).sum()
        day_n[di] += (ch > 0).sum()
        pos = new
    return day_p / len(P), day_n / 2


def gap(d: Data, th=1.0, mode="fade", long_only=False, fill=True, cap=0.2, cost=COST):
    """시가 갭 (ATR 대비 th배 이상). fade: 갭 반대로 → 전일 종가 닿으면 청산(fill) 또는 마감. go: 갭 방향으로 → 마감."""
    O, H, L, C = d.O, d.H, d.L, d.C
    g = (O[:, :, 0] / d.PC - 1) / d.ATRP
    side = np.where(g > th, -1, np.where(g < -th, 1, 0)) * (1 if mode == "fade" else -1)
    side = np.where(d.liquid & ~np.isnan(g), side, 0)
    if long_only:
        side = np.maximum(side, 0)
    ent = O[:, :, 1]
    ex = C[:, :, -1].copy()
    if mode == "fade" and fill:
        done = np.zeros(side.shape, bool)
        for t in range(1, NB):
            hit = ~done & (((side > 0) & (H[:, :, t] >= d.PC)) | ((side < 0) & (L[:, :, t] <= d.PC)))
            ex[hit] = d.PC[hit]
            done |= hit
    n = abs(side).sum(0)
    w = np.minimum(1 / np.maximum(n, 1), cap)
    return (side * np.nan_to_num(ex / ent - 1) - abs(side) * 2 * cost).sum(0) * w, n


def orb(d: Data, K=3, rv=2.0, stop=0.5, risk=0.01, long_only=False, cost=COST):
    """거래량 터진 종목의 첫 5분봉 돌파. 첫 봉 방향으로, 첫 봉 고가(저가) 돌파 시 진입, 손절 = stop×일봉ATR, 마감 청산."""
    O, H, L, C, V = d.O, d.H, d.L, d.C, d.V
    v0 = V[:, :, 0]
    rvol = v0 / lagmean(v0, 14)
    dirn = np.sign(C[:, :, 0] - O[:, :, 0])
    if long_only:
        dirn = np.maximum(dirn, 0)
    sc = np.where(d.liquid & (rvol >= rv) & (dirn != 0), rvol, -np.inf)
    rk = np.argsort(np.argsort(-sc, axis=0), axis=0)
    pick = (rk < K) & np.isfinite(sc)
    lvl = np.where(dirn > 0, H[:, :, 0], L[:, :, 0])
    sd = stop * d.ATR
    ent = np.full(pick.shape, np.nan)
    ex = np.full(pick.shape, np.nan)
    for t in range(1, NB):
        o, h, l = O[:, :, t], H[:, :, t], L[:, :, t]
        live = ~np.isnan(ent) & np.isnan(ex)
        stp = ent - dirn * sd
        hit = live & np.where(dirn > 0, l <= stp, h >= stp)
        ex[hit] = np.where(dirn > 0, np.minimum(stp, o), np.maximum(stp, o))[hit]
        if t < NB - 6:
            trig = pick & np.isnan(ent) & np.where(dirn > 0, h >= lvl, l <= lvl)
            ent[trig] = np.where(dirn > 0, np.maximum(lvl, o), np.minimum(lvl, o))[trig]
            stp = ent - dirn * sd
            hit = trig & np.where(dirn > 0, l <= stp, h >= stp)  # 같은 봉에서 손절선까지 닿으면 손절로 봄 (보수적)
            ex[hit] = stp[hit]
    took = ~np.isnan(ent)
    ex = np.where(took & np.isnan(ex), C[:, :, -1], ex)
    w = np.minimum(risk / (sd / ent), 1 / K)
    pnl = np.where(took, w * (dirn * (ex / ent - 1) - 2 * cost), 0)
    return np.nansum(pnl, 0), took.sum(0)


_ML = {}


def ml_prep(d: Data):
    """30분마다 (종목×날짜×시각) 신호 6개와 다음 30분 수익률. 신호는 그 시각 종목들 사이에서 평균 0으로 맞춤."""
    if _ML:
        return _ML
    ts = list(range(5, NB - 7, 6))
    C, O = d.C, d.O
    av = lagmean(d.CUMV, 14)
    X, Y = [], []
    for t in ts:
        a = d.ATRP
        f = np.stack([(C[:, :, t] / O[:, :, 0] - 1) / a, (C[:, :, t] / C[:, :, t - 5] - 1) / a,
                      (C[:, :, t] / d.VWAP[:, :, t] - 1) / a, np.log(np.clip(d.CUMV[:, :, t] / av[:, :, t], 0.05, 20)),
                      (O[:, :, 0] / d.PC - 1) / a, (C[:, :, t] / C[:, :, max(t - 1, 0)] - 1) / a], -1)
        f[~d.liquid] = np.nan
        f = np.clip(f - np.nanmean(f, 0, keepdims=True), -5, 5)
        y = C[:, :, t + 6] / O[:, :, t + 1] - 1
        y = y - np.nanmean(np.where(d.liquid, y, np.nan), 0, keepdims=True)
        X.append(f)
        Y.append(y)
    _ML.update(X=np.stack(X, 2), Y=np.stack(Y, 2), ts=ts)  # (S, D, nt, 6), (S, D, nt)
    X_, Y_ = _ML["X"], _ML["Y"]
    m = d.mask["학습"]
    xf, yf = X_[:, m].reshape(-1, X_.shape[-1]), Y_[:, m].reshape(-1)
    g = ~np.isnan(xf).any(1) & ~np.isnan(yf)
    A = xf[g]
    _ML["beta"] = np.linalg.solve(A.T @ A + 10.0 * np.eye(A.shape[1]), A.T @ yf[g])
    _ML["pred"] = X_ @ _ML["beta"]
    return _ML


def ml(d: Data, q=3, edge=0.0, long_only=False, cost=COST):
    """선형모형 예측 상·하위 q종목을 30분씩 보유. edge = 예측 격차가 이 값(수익률) 이상일 때만 매매."""
    M = ml_prep(d)
    P, Y = M["pred"], M["Y"]
    D = len(d.days)
    r, n = np.zeros(D), np.zeros(D)
    for k in range(P.shape[2]):
        p, y = P[:, :, k], Y[:, :, k]
        bad = np.isnan(p) | np.isnan(y)
        ps = np.where(bad, np.nan, p)
        rk = np.argsort(np.argsort(np.where(bad, np.inf, -p), axis=0), axis=0)
        nv = (~bad).sum(0)
        top, bot = (rk < q) & ~bad, (rk >= nv - q) & ~bad
        pt = np.where(top, ps, 0).sum(0) / q
        pb = np.where(bot, ps, 0).sum(0) / q
        go = (nv >= 4 * q) & ((pt if long_only else (pt - pb) / 2) > edge)
        lr = np.where(top, np.nan_to_num(y), 0).sum(0) / q
        sr = np.where(bot, np.nan_to_num(y), 0).sum(0) / q
        r += np.where(go, (lr - 2 * cost) if long_only else ((lr - sr) / 2 - 2 * cost), 0)
        n += np.where(go, q * (1 if long_only else 2), 0)
    return r, n


# ───────────────────────── 탐색 ─────────────────────────
def grid(**kw):
    keys = list(kw)
    return [dict(zip(keys, v)) for v in itertools.product(*kw.values())]


def families(d: Data) -> dict:
    P = pick_pairs(d)
    print("페어:", ", ".join(f"{d.syms[i]}/{d.syms[j]}" for i, j in P))
    return {
        "noise": (noise, grid(univ=["idx", "SPY", "QQQ", "all"], k=[0.75, 1.0, 1.5], step=[3, 6], long_only=[False, True], vstop=[True, False])),
        "xs": (xs, grid(t0=[2, 5, 11, 23, 41, 59, 65], look=[0, 6], hold=[6, 12, 99], q=[3, 5], mode=["rev", "mom"], long_only=[False, True])),
        "last30": (last30, grid(univ=["idx", "all"], t0=[65, 71], th=[0.0, 0.25, 0.5], long_only=[False, True], src=["day", "open", "first"])),
        "pairs": (lambda d, **k: pairs(d, P, **k), grid(W=[78, 390, 1170], ze=[1.5, 2.0, 2.5, 3.0], zx=[0.0, 0.5], overnight=[False, True])),
        "gap": (gap, grid(th=[0.25, 0.5, 1.0, 1.5], mode=["fade", "go"], long_only=[False, True], fill=[True, False])),
        "orb": (orb, grid(K=[2, 3, 5], rv=[1.0, 2.0, 3.0], stop=[0.1, 0.25, 0.5, 1.0], long_only=[False, True])),
        "ml": (ml, grid(q=[2, 3, 5], edge=[0.0, 0.0005, 0.001, 0.002], long_only=[False, True])),
    }


def run(d: Data, which: list | None = None) -> dict:
    out = {}
    fam = families(d)
    spy = d.C[d.ix["SPY"], :, -1] / d.PC[d.ix["SPY"]] - 1
    bench = {k: stats(np.nan_to_num(spy), np.zeros(len(spy)), m) for k, m in d.mask.items()}
    print("\nSPY 보유:", " · ".join(f"{k} {v['ret'] * 100:+.0f}% (샤프 {v['sharpe']:.1f}, 낙폭 {v['dd'] * 100:.0f}%)" for k, v in bench.items()))
    for name, (fn, G) in fam.items():
        if which and name not in which:
            continue
        rows = []
        for g in G:
            r, n = fn(d, **g)
            r = np.nan_to_num(r)
            rows.append(dict(p=g, **{k: stats(r, n, m) for k, m in d.mask.items()}))
        live = [x for x in rows if x["학습"]["tpd"] >= 0.2]
        live.sort(key=lambda x: -x["학습"]["sharpe"])
        best = live[0]
        r2, n2 = fn(d, **best["p"], cost=0.0002)
        r0, _ = fn(d, **best["p"], cost=0.0)
        best["시험_비용0.02%"] = stats(np.nan_to_num(r2), n2, d.mask["시험"])
        best["시험_비용0"] = stats(np.nan_to_num(r0), n2, d.mask["시험"])
        pos_test = np.mean([x["시험"]["ret"] > 0 for x in live])
        top5 = live[:5]
        out[name] = dict(best=best, n=len(G), n_live=len(live), pos_train=float(np.mean([x["학습"]["ret"] > 0 for x in live])),
                         pos_test=float(pos_test), top5=top5,
                         passed=bool(best["학습"]["sharpe"] > 0.5 and best["확인"]["ret"] > 0 and best["확인"]["sharpe"] > 0.5))
        b = best
        print(f"\n[{name}] 설정 {len(G)}개 · 학습 플러스 {out[name]['pos_train'] * 100:.0f}% · 시험 플러스 {pos_test * 100:.0f}%")
        print("  학습 최고:", b["p"])
        for k in PERIODS:
            s = b[k]
            print(f"   {k}: {s['ret'] * 100:+6.1f}%  샤프 {s['sharpe']:+.2f}  낙폭 {s['dd'] * 100:5.1f}%  하루 {s['tpd']:.1f}건")
        print(f"   시험(비용 0.02%): {b['시험_비용0.02%']['ret'] * 100:+.1f}% · 시험(비용 0): {b['시험_비용0']['ret'] * 100:+.1f}%"
              f"   → {'확인 통과' if out[name]['passed'] else '확인 탈락'}")
    return dict(bench=bench, families=out, cost=COST, periods=PERIODS)


if __name__ == "__main__":
    d = Data()
    print(f"{len(d.syms)}종목 · {len(d.days)}거래일 ({d.days[0]} ~ {d.days[-1]})")
    res = run(d, sys.argv[1:] or None)
    if not sys.argv[1:]:
        (LAB / "quant_result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float))
