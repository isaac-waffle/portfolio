"""스윙·추세형 트레이더 백테스트 — 일봉으로 며칠~몇 주 보유. 4번째 트레이더로 넣을지 정하기 위한 실험.

  .venv/bin/python apps/swing_lab.py

전략 3종 × 파라미터 조합을 학습(24.01~25.06)으로 고르고 → 확인(25.07~12)에서도 플러스인지 → 최종 시험(26.01~10)은 고르는 데 안 씀.
  돌파형    : N일 최고가 돌파 + 50·200일선 위 → ATR 트레일링 스탑 / 이동평균 이탈 시 청산
  눌림목형  : 200·50일선 위 상승 추세에서 RSI(2) 과매도 → 5일선 회복 또는 N일 뒤 청산
  모멘텀형  : 매주/매월 3~12개월 수익률 상위 N종목으로 갈아타기 (50일선 위만)
선택 편향 점검: 관심종목(지금 핫한 AI·반도체로 고른 것)을 똑같이 들고 있었을 때와 비교하고, 나스닥100으로 다시 시험.
체결: 신호는 종가로, 매매는 다음 날 시가. 손절은 장중 저가로 확인(갭 하락이면 시가). 수수료 주문당 $2, 슬리피지 0.05%.
"""
from __future__ import annotations

import itertools
import json
import pickle
import time
import warnings

import numpy as np
import pandas as pd
import yfinance as yf

import traders as T

warnings.filterwarnings("ignore")
LAB = T.DATA / "lab"
CASH0, FEE, SLIP = T.START_CASH, T.FEE, T.SLIP
PERIODS = {"학습": ("2024-01-01", "2025-06-30"), "확인": ("2025-07-01", "2025-12-31"), "최종시험": ("2026-01-01", "2026-10-23")}
# 나스닥100 (2026년 구성 근사) — 관심종목처럼 '오를 걸 알고 고른' 목록이 아닌 넓은 비교군
NDX = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "AVGO", "TSLA", "COST", "NFLX", "AMD", "PEP", "ADBE", "CSCO", "TMUS",
       "INTC", "QCOM", "TXN", "AMGN", "INTU", "AMAT", "ISRG", "HON", "BKNG", "CMCSA", "LRCX", "VRTX", "ADP", "MU", "PANW",
       "ADI", "SBUX", "GILD", "MELI", "REGN", "KLAC", "MDLZ", "SNPS", "CDNS", "PYPL", "CRWD", "ASML", "MAR", "CTAS", "ORLY",
       "CSX", "MRVL", "ABNB", "WDAY", "FTNT", "ROP", "NXPI", "ADSK", "PCAR", "CHTR", "CPRT", "MNST", "PAYX", "AEP", "ROST",
       "KDP", "ODFL", "FAST", "EA", "DDOG", "KHC", "VRSK", "BKR", "EXC", "CTSH", "XEL", "GEHC", "IDXX", "CCEP", "TTWO",
       "LULU", "ON", "CSGP", "ZS", "DXCM", "TEAM", "BIIB", "MDB", "WBD", "GFS", "ARM", "DASH", "PLTR", "APP", "AXON",
       "MSTR", "SHOP", "TRI", "LIN", "AZN", "PDD"]
BENCH = ["SPY", "QQQ", "SMH"]


# ───────────────────────── 데이터 ─────────────────────────
def load(start: str = "2022-06-01") -> dict:
    f = LAB / ("swing_daily.pkl" if start == "2022-06-01" else f"swing_daily_{start[:4]}.pkl")
    if f.exists():
        return pickle.load(open(f, "rb"))
    syms = sorted(set(T.WATCHLIST) | set(NDX) | set(BENCH))
    d = yf.download(syms, start=start, end="2026-10-24", interval="1d", auto_adjust=True, group_by="ticker",
                    progress=False, threads=True)
    F = {k: pd.DataFrame({s: d[s][k] for s in syms if s in d.columns.get_level_values(0)}) for k in ("Open", "High", "Low", "Close", "Volume")}
    F = {k: v.dropna(how="all", axis=1) for k, v in F.items()}
    LAB.mkdir(parents=True, exist_ok=True)
    pickle.dump(F, open(f, "wb"))
    return F


def indicators(F: dict) -> dict:
    O, H, L, C, V = (F[k] for k in ("Open", "High", "Low", "Close", "Volume"))
    pc = C.shift()
    tr = pd.concat([H - L, (H - pc).abs(), (L - pc).abs()]).groupby(level=0).max().reindex(C.index)
    d = C.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 2, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 2, adjust=False).mean()
    X = dict(O=O, H=H, L=L, C=C, atr=tr.rolling(14).mean(), rsi2=100 - 100 / (1 + up / dn.replace(0, np.nan)),
             ma5=C.rolling(5).mean(), ma20=C.rolling(20).mean(), ma50=C.rolling(50).mean(), ma200=C.rolling(200).mean(),
             dvol=(C * V).rolling(20).mean(), volr=V / V.rolling(50).mean())
    for n in (10, 20, 50):
        X[f"hh{n}"] = C.rolling(n).max().shift()
        X[f"ll{n}"] = C.rolling(n).min().shift()
    for n in (63, 126, 252):
        X[f"mom{n}"] = C.shift(5) / C.shift(n) - 1  # 최근 1주는 빼고 (단기 되돌림 회피)
    spy = C["SPY"]
    X["mkt200"] = spy > spy.rolling(200).mean()
    X["mkt50"] = spy > spy.rolling(50).mean()
    return {k: (v.to_numpy() if isinstance(v, pd.DataFrame) else v.to_numpy()) for k, v in X.items()} | {"cols": list(C.columns), "idx": C.index}


# ───────────────────────── 시뮬레이션 ─────────────────────────
def simulate(P: dict, X: dict, start: str, end: str, uni: list) -> dict:
    idx, cols = X["idx"], X["cols"]
    ci = np.array([cols.index(s) for s in uni if s in cols])
    days = np.where((idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end)))[0]
    O, H, L, C, atr = X["O"], X["H"], X["L"], X["C"], X["atr"]
    cash, pos, trades, curve, buys, sells = CASH0, {}, [], [], [], set()
    mkt = X["mkt200"] if P["mkt"] == "200" else X["mkt50"] if P["mkt"] == "50" else np.ones(len(idx), bool)
    expo = []

    def sell(j, px, i, why):
        nonlocal cash
        p = pos.pop(j)
        fill = px * (1 - SLIP)
        cash += p["sh"] * fill - FEE
        pnl = p["sh"] * (fill - p["entry"]) - 2 * FEE + p.get("real", 0.0)  # real: 1차 익절로 이미 번 돈
        trades.append(dict(sym=cols[j], d0=str(idx[p["i0"]].date()), d1=str(idx[i].date()), days=i - p["i0"], pnl=pnl,
                           ret=pnl / (p["sh0"] * p["entry"]), why=why))

    for n, i in enumerate(days):
        # 1) 전날 정한 매도·매수를 오늘 시가에
        for j in list(sells):
            if j in pos and np.isfinite(O[i, j]):
                sell(j, O[i, j], i, pos[j].get("why", "청산 신호"))
        sells = set()
        eq_prev = cash + sum(p["sh"] * C[i - 1, j] for j, p in pos.items() if np.isfinite(C[i - 1, j]))
        for j, w in buys:
            if j in pos or len(pos) >= P["max_pos"] or not np.isfinite(O[i, j]):
                continue
            px = O[i, j] * (1 + SLIP)
            if P["mode"] == "rotation":
                val = eq_prev * w
            elif not P.get("stop_atr"):  # 손절 없는 전략은 종목당 같은 비중
                val = eq_prev / P["max_pos"]
            else:
                stop_d = P["stop_atr"] * atr[i - 1, j]
                val = min(eq_prev * P["risk"] / (stop_d / px) if stop_d > 0 else 0, eq_prev / P["max_pos"])
            sh = int(min(val, cash - FEE) / px)
            if sh < 1:
                continue
            cash -= sh * px + FEE
            stop = px - P["stop_atr"] * atr[i - 1, j] if P.get("stop_atr") else -np.inf
            pos[j] = dict(sh=sh, sh0=sh, entry=px, stop=stop, r0=px - stop if np.isfinite(stop) else 0, i0=i)
        buys = []
        # 2) 장중 손절
        for j in list(pos):
            p = pos[j]
            if np.isfinite(L[i, j]) and L[i, j] <= p["stop"]:
                sell(j, min(O[i, j], p["stop"]), i, "손절·트레일링 스탑")
        # 2-1) 1차 익절: +N R에서 절반 팔고 손절을 본전으로
        if P.get("partial"):
            for j, p in pos.items():
                tgt = p["entry"] + P["partial"] * p["r0"]
                if not p.get("half") and p["r0"] > 0 and p["sh"] >= 2 and np.isfinite(H[i, j]) and H[i, j] >= tgt:
                    half = p["sh"] // 2
                    fill = max(O[i, j], tgt) * (1 - SLIP)
                    cash += half * fill - FEE
                    p["real"] = p.get("real", 0.0) + half * (fill - p["entry"]) - FEE
                    p["sh"] -= half
                    p["half"] = True
                    p["stop"] = max(p["stop"], p["entry"])
        # 3) 종가: 트레일링 갱신, 청산 신호
        for j, p in pos.items():
            c = C[i, j]
            if not np.isfinite(c):
                continue
            if P.get("trail_atr") and np.isfinite(atr[i, j]):
                p["stop"] = max(p["stop"], c - P["trail_atr"] * atr[i, j])
            held = i - p["i0"]
            why = None
            if P["mode"] == "breakout" and c < X[f"ma{P['exit_ma']}"][i, j]:
                why = f"{P['exit_ma']}일선 이탈"
            elif P["mode"] == "trendpull" and c < X[f"ma{P['exit_ma']}"][i, j]:
                why = f"{P['exit_ma']}일선 이탈 (추세 꺾임)"
            elif P["mode"] == "pullback" and (c > X["ma5"][i, j] or held >= P["max_hold"]):
                why = "5일선 회복 (반등 완료)" if c > X["ma5"][i, j] else f"{P['max_hold']}일 경과"
            elif P.get("exit_mkt") and not mkt[i]:
                why = "시장 필터 꺼짐"
            if why:
                p["why"] = why
                sells.add(j)
        # 4) 종가: 다음 날 살 종목
        nxt = days[n + 1] if n + 1 < len(days) else None
        if nxt is not None:
            ok = (np.isfinite(C[i, ci]) & (X["dvol"][i, ci] >= 2e7) & (C[i, ci] >= 5) & np.isfinite(atr[i, ci]))
            mom = X[f"mom{P.get('mom', 126)}"][i, ci]
            if P["mode"] == "rotation":
                rebal = (idx[nxt].isocalendar()[1] != idx[i].isocalendar()[1]) if P["rebal"] == "W" else (idx[nxt].month != idx[i].month)
                if rebal:
                    elig = ok & (C[i, ci] > X["ma50"][i, ci]) & np.isfinite(mom) & (mom > 0)
                    target = [ci[k] for k in np.argsort(-np.where(elig, mom, -np.inf))[:P["max_pos"]] if elig[k]] if mkt[i] else []
                    for j in pos:
                        if j not in target:
                            pos[j]["why"] = "순위 밖 · 갈아타기"
                            sells.add(j)
                    buys = [(j, 1 / P["max_pos"]) for j in target if j not in pos]
            elif mkt[i]:
                if P["mode"] == "breakout":
                    sig = ok & (C[i, ci] > X[f"hh{P['n']}"][i, ci]) & (C[i, ci] > X["ma50"][i, ci]) & (X["ma50"][i, ci] > X["ma200"][i, ci]) \
                        & (X["volr"][i, ci] >= P["volr"])
                elif P["mode"] == "trendpull":  # 정배열 종목이 20일선까지 내려왔다가 그 위에서 반등 마감
                    m20 = X["ma20"][i, ci]
                    sig = ok & (C[i, ci] > X["ma50"][i, ci]) & (X["ma50"][i, ci] > X["ma200"][i, ci]) \
                        & (L[i, ci] <= m20 * (1 + P["tol"])) & (C[i, ci] > m20) & (C[i, ci] > C[i - 1, ci])
                else:
                    sig = ok & (C[i, ci] > X["ma200"][i, ci]) & (C[i, ci] > X["ma50"][i, ci]) & (X["rsi2"][i, ci] < P["rsi"])
                score = (-atr[i, ci] / C[i, ci] if P.get("rank") == "lowvol" else -X["rsi2"][i, ci] if P.get("rank") == "rsi"
                         else np.nan_to_num(mom, nan=-9))
                cand = [ci[k] for k in np.argsort(-np.where(sig, score, -np.inf)) if sig[k]]
                free = P["max_pos"] - len(pos) + len(sells)
                buys = [(j, 0) for j in cand if j not in pos][:max(free, 0)]
        eq = cash + sum(p["sh"] * (C[i, j] if np.isfinite(C[i, j]) else p["entry"]) for j, p in pos.items())
        curve.append(eq)
        expo.append(1 - cash / eq if eq > 0 else 0)
    for j in list(pos):  # 기간 끝: 마지막 종가로 정리 (성적 계산용)
        sell(j, C[days[-1], j], days[-1], "기간 끝")
    eq = np.array([CASH0] + curve)
    peak = np.maximum.accumulate(eq)
    yrs = len(days) / 252
    ret = eq[-1] / CASH0 - 1
    out = dict(ret=ret, cagr=(1 + ret) ** (1 / yrs) - 1 if yrs > 0 else 0, mdd=float((eq / peak - 1).min()), n=len(trades),
               expo=float(np.mean(expo)) if expo else 0)
    if trades:
        out.update(win=np.mean([t["pnl"] > 0 for t in trades]), hold=float(np.mean([t["days"] for t in trades])),
                   avg=float(np.mean([t["ret"] for t in trades])))
    out["calmar"] = out["cagr"] / abs(out["mdd"]) if out["mdd"] < 0 else out["cagr"]
    out["trades"] = trades
    out["curve"] = pd.Series(eq[1:], index=idx[days])
    return out


def bh(X: dict, uni: list, start: str, end: str) -> float:
    """같은 종목을 똑같은 비중으로 사서 들고 있었으면 (기간 첫날 시가 → 마지막 종가)."""
    idx, cols, O, C = X["idx"], X["cols"], X["O"], X["C"]
    d = np.where((idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end)))[0]
    r = [C[d[-1], cols.index(s)] / O[d[0], cols.index(s)] - 1 for s in uni if s in cols
         and np.isfinite(O[d[0], cols.index(s)]) and np.isfinite(C[d[-1], cols.index(s)])]
    return float(np.mean(r))


# ───────────────────────── 실험 ─────────────────────────
def grid() -> list[dict]:
    G = []
    for n, tr, em, mp, vr, mk in itertools.product((20, 50), (2.5, 3.5), (20, 50), (5, 8), (1.0, 1.5), ("200", "none")):
        G.append(dict(mode="breakout", n=n, stop_atr=2.0, trail_atr=tr, exit_ma=em, max_pos=mp, volr=vr, risk=0.01, mkt=mk, mom=126))
    for rs, mh, sa, mp, mk in itertools.product((5, 10, 20), (5, 10), (2.0, 3.0), (5, 8), ("200", "none")):
        G.append(dict(mode="pullback", rsi=rs, max_hold=mh, stop_atr=sa, max_pos=mp, risk=0.01, mkt=mk, mom=126))
    for top, mom, rb, mk in itertools.product((3, 5, 8), (63, 126, 252), ("W", "M"), ("200", "50", "none")):
        G.append(dict(mode="rotation", max_pos=top, mom=mom, rebal=rb, mkt=mk, exit_mkt=mk != "none", stop_atr=None, trail_atr=None))
    return G


def desc(P: dict) -> str:
    if P["mode"] == "breakout":
        mk = {"200": "SPY 200일선", "50": "SPY 50일선"}.get(P["mkt"], "없음")
        return (f"돌파형: {P['n']}일 신고가 + 거래량 {P['volr']}배↑, 손절 2ATR→트레일링 {P['trail_atr']}ATR, {P['exit_ma']}일선 이탈 청산, "
                f"최대 {P['max_pos']}종목, 시장필터 {mk}{' (꺼지면 전량 정리)' if P.get('exit_mkt') else ''}"
                + (f", +{P['partial']}R에서 절반 익절" if P.get("partial") else ""))
    if P["mode"] == "trendpull":
        mk = {"200": "SPY 200일선", "50": "SPY 50일선"}.get(P["mkt"], "없음")
        tol = f" +{P['tol'] * 100:.0f}%" if P["tol"] else ""
        return (f"추세 눌림형: 정배열 종목이 20일선{tol}까지 내려왔다 반등 마감, "
                f"{P['exit_ma']}일선 이탈 청산, 손절 {P['stop_atr']}ATR" + (f"→트레일링 {P['trail_atr']}ATR" if P.get("trail_atr") else "")
                + f", 최대 {P['max_pos']}종목, {'변동성 낮은 순' if P.get('rank') == 'lowvol' else '6개월 수익률 순'}, 시장필터 {mk}"
                + (" (꺼지면 전량 정리)" if P.get("exit_mkt") else ""))
    if P["mode"] == "pullback":
        return (f"눌림목형: 상승추세 중 RSI(2)<{P['rsi']}, 5일선 회복 또는 {P['max_hold']}일 뒤 청산, "
                f"{'손절 ' + format(P['stop_atr'], 'g') + 'ATR' if P.get('stop_atr') else '손절 없음·종목당 같은 비중'}, "
                f"{({'rsi': 'RSI 낮은 순', 'lowvol': '변동성 낮은 순'}).get(P.get('rank'), '6개월 수익률 순')}, "
                f"최대 {P['max_pos']}종목, 시장필터 {({'200': 'SPY 200일선', '50': 'SPY 50일선'}).get(P['mkt'], '없음')}")
    return (f"모멘텀형: {'매주' if P['rebal'] == 'W' else '매월'} {P['mom'] // 21}개월 수익률 상위 {P['max_pos']}종목, 50일선 위만, "
            f"시장필터 {'SPY ' + P['mkt'] + '일선 (꺼지면 현금)' if P['mkt'] != 'none' else '없음'}")


def fmt(m: dict) -> str:
    return (f"{m['ret'] * 100:+7.1f}% (연 {m['cagr'] * 100:+6.1f}%) · 낙폭 {m['mdd'] * 100:6.1f}% · {m['n']:3d}회"
            + (f" · 승률 {m['win'] * 100:3.0f}% · 평균 {m['hold']:4.1f}일 보유" if m["n"] else "") + f" · 투자비중 {m['expo'] * 100:3.0f}%")


def main():
    t0 = time.time()
    F = load()
    X = indicators(F)
    W = [s for s in T.WATCHLIST if s in X["cols"]]
    N = [s for s in NDX if s in X["cols"]]
    print(f"일봉 {X['idx'][0].date()}~{X['idx'][-1].date()} · 관심종목 {len(W)}개 · 나스닥100 {len(N)}개 · 조합 {len(grid())}개")
    rows = []
    for P in grid():
        r = {k: simulate(P, X, a, b, W) for k, (a, b) in PERIODS.items()}
        rows.append((P, r))
    print(f"시뮬 {time.time() - t0:.0f}초\n")

    print("■ 비교 기준 (그냥 사서 보유)")
    for k, (a, b) in PERIODS.items():
        bm = {s: bh(X, [s], a, b) for s in BENCH}
        print(f"  {k:<5} SPY {bm['SPY'] * 100:+6.1f}% · QQQ {bm['QQQ'] * 100:+6.1f}% · SMH {bm['SMH'] * 100:+6.1f}% · "
              f"관심종목 동일비중 {bh(X, W, a, b) * 100:+6.1f}% · 나스닥100 동일비중 {bh(X, N, a, b) * 100:+6.1f}%")

    print("\n■ 전략별: 학습 기간 성적(연수익/낙폭)으로 고르고, 확인 기간도 플러스인 것 중 1등 — 최종시험은 고를 때 안 봄")
    picks = {}
    for mode, name in (("breakout", "돌파형"), ("pullback", "눌림목형"), ("rotation", "모멘텀형")):
        rs = [x for x in rows if x[0]["mode"] == mode]
        ranked = sorted(rs, key=lambda x: -x[1]["학습"]["calmar"])
        pick = next((x for x in ranked if x[1]["확인"]["ret"] > 0), ranked[0])
        picks[mode] = pick
        P, r = pick
        print(f"\n[{name}] {desc(P)}")
        for k in PERIODS:
            print(f"  {k:<5} {fmt(r[k])}")
        hold = np.array([x[1]["최종시험"]["ret"] for x in rs])
        tr = np.array([x[1]["학습"]["ret"] for x in rs])
        print(f"  같은 전략 전체 {len(rs)}개 조합: 학습 중앙값 {np.median(tr) * 100:+.1f}% · 최종시험 중앙값 {np.median(hold) * 100:+.1f}% "
              f"· 최종시험 플러스 {np.mean(hold > 0) * 100:.0f}% (파라미터를 잘못 골라도 되는지 = 안정성)")

    print("\n■ 선택 편향 점검: 같은 규칙을 나스닥100에 (핫한 종목만 고른 효과 빼기)")
    for mode, name in (("breakout", "돌파형"), ("pullback", "눌림목형"), ("rotation", "모멘텀형")):
        P, _ = picks[mode]
        print(f"[{name}]")
        for k, (a, b) in PERIODS.items():
            m = simulate(P, X, a, b, N)
            print(f"  {k:<5} {fmt(m)}  | 나스닥100 동일비중 {bh(X, N, a, b) * 100:+.1f}% · QQQ {bh(X, ['QQQ'], a, b) * 100:+.1f}%")

    out = LAB / "swing_result.json"
    out.write_text(json.dumps(T._clean({mode: dict(params=P, desc=desc(P), **{k: {kk: vv for kk, vv in v.items() if kk != "trades"}
                                                                             for k, v in r.items()})
                                        for mode, (P, r) in picks.items()}), ensure_ascii=False, indent=1, default=str))
    best = max(picks.values(), key=lambda x: x[1]["확인"]["calmar"])
    print(f"\n최근 거래 예시 ({desc(best[0])[:5]}, 최종시험 기간 마지막 12건):")
    for t in best[1]["최종시험"]["trades"][-12:]:
        print(f"  {t['sym']:<5} {t['d0']} → {t['d1']} ({t['days']}일) {t['ret'] * 100:+6.1f}% {t['pnl']:+7.0f}$ · {t['why']}")
    print(f"\n저장: {out} · 총 {time.time() - t0:.0f}초")


# ───────────────────────── 10년 검증 (돌쇠 강화) ─────────────────────────
LONG = {"학습": ("2016-01-01", "2021-12-31"), "확인": ("2022-01-01", "2023-12-31"), "최종시험": ("2024-01-01", "2026-10-23")}
CURRENT = dict(mode="breakout", n=20, stop_atr=2.0, trail_atr=3.5, exit_ma=20, max_pos=5, volr=1.5, risk=0.01, mkt="none", mom=126)


def long_grid() -> list[dict]:
    G = []
    for n, tr, em, mp, vr, mk, pa in itertools.product((20, 50), (2.5, 3.5), (20, 50), (5, 8), (1.0, 1.5), ("200", "50", "none"), (0, 2.0)):
        for ex in ((False, True) if mk != "none" else (False,)):
            G.append(dict(mode="breakout", n=n, stop_atr=2.0, trail_atr=tr, exit_ma=em, max_pos=mp, volr=vr, risk=0.01, mkt=mk,
                          mom=126, partial=pa, exit_mkt=ex))
    for top, mom, rb, mk in itertools.product((3, 5, 8), (63, 126, 252), ("W", "M"), ("200", "50", "none")):
        G.append(dict(mode="rotation", max_pos=top, mom=mom, rebal=rb, mkt=mk, exit_mkt=mk != "none", stop_atr=None, trail_atr=None))
    return G


def long_main():
    """2016~2026 일봉. 선택 편향을 줄이려고 나스닥100으로 고르고(학습 16~21, 확인 22~23 하락장 포함), 관심종목에선 확인만.
    최종시험(24~26)은 지금 돌쇠 규칙을 정할 때 이미 본 기간이라 '완전히 새 기간'은 아님 — 연도별 표로 같이 판단."""
    t0 = time.time()
    X = indicators(load("2014-06-01"))
    W = [s for s in T.WATCHLIST if s in X["cols"]]
    N = [s for s in NDX if s in X["cols"]]
    G = long_grid()
    print(f"일봉 {X['idx'][0].date()}~{X['idx'][-1].date()} · 관심종목 {len(W)}개 · 나스닥100 {len(N)}개 · 조합 {len(G)}개")
    rows = [(P, {k: simulate(P, X, a, b, N) for k, (a, b) in LONG.items()}) for P in G]
    print(f"시뮬 {time.time() - t0:.0f}초\n")
    print("■ 비교 기준 (그냥 사서 보유)")
    for k, (a, b) in LONG.items():
        bm = {s: bh(X, [s], a, b) for s in BENCH}
        print(f"  {k:<5} {a[:4]}~{b[:4]}  SPY {bm['SPY'] * 100:+7.1f}% · QQQ {bm['QQQ'] * 100:+7.1f}% · SMH {bm['SMH'] * 100:+7.1f}% · "
              f"나스닥100 동일비중 {bh(X, N, a, b) * 100:+7.1f}% · 관심종목 동일비중 {bh(X, W, a, b) * 100:+7.1f}%")
    picks = {"현재 돌쇠": CURRENT}
    for mode, name in (("breakout", "돌파형 최적"), ("rotation", "모멘텀형 최적")):
        rs = [x for x in rows if x[0]["mode"] == mode]
        ranked = sorted(rs, key=lambda x: -x[1]["학습"]["calmar"])
        picks[name] = next((x for x in ranked if x[1]["확인"]["ret"] > 0), ranked[0])[0]
        ck = np.array([x[1]["확인"]["ret"] for x in rs])
        hd = np.array([x[1]["최종시험"]["ret"] for x in rs])
        print(f"\n  {name[:3]} 전체 {len(rs)}개 조합 — 2022~23 플러스 {np.mean(ck > 0) * 100:.0f}% · 24~26 플러스 {np.mean(hd > 0) * 100:.0f}% (중앙값 {np.median(hd) * 100:+.1f}%)")
    for name, P in picks.items():
        print(f"\n[{name}] {desc(P)}")
        for uni_name, U in (("나스닥100", N), ("관심종목", W)):
            for k, (a, b) in LONG.items():
                print(f"  {uni_name:<5} {k:<5} {fmt(simulate(P, X, a, b, U))}")
    print("\n■ 연도별 (매년 $20,000으로 새로 시작 · 나스닥100 기준) — 하락장(2018·2020·2022)에서 어떻게 버티는지")
    yrs = range(2016, 2027)
    print("  " + " " * 14 + " ".join(f"{y:>7}" for y in yrs))
    for name, P in picks.items():
        print(f"  {name:<12}" + " ".join(f"{simulate(P, X, f'{y}-01-01', f'{y}-12-31', N)['ret'] * 100:+7.1f}" for y in yrs))
    for s in ("SPY", "QQQ"):
        print(f"  {s + ' 보유':<12}" + " ".join(f"{bh(X, [s], f'{y}-01-01', f'{y}-12-31') * 100:+7.1f}" for y in yrs))
    print(f"  {'나스닥100 동일':<11}" + " ".join(f"{bh(X, N, f'{y}-01-01', f'{y}-12-31') * 100:+7.1f}" for y in yrs))
    print("\n  낙폭(연도별 최대)")
    for name, P in list(picks.items())[:2]:
        print(f"  {name:<12}" + " ".join(f"{simulate(P, X, f'{y}-01-01', f'{y}-12-31', N)['mdd'] * 100:+7.1f}" for y in yrs))
    out = LAB / "swing_long.json"
    out.write_text(json.dumps(T._clean({k: dict(params=P, desc=desc(P)) for k, P in picks.items()}), ensure_ascii=False, indent=1, default=str))
    print(f"\n저장: {out} · 총 {time.time() - t0:.0f}초")


# ───────────────────────── 셋을 서로 다른 규칙으로 (v7) ─────────────────────────
# 아이작 = 모멘텀 순환(그대로), 돌쇠 = 돌파(그대로), 티모 = 단기 눌림목(RSI2 과매도 반등), 차차 = 추세 눌림(20일선 지지 + 시장 필터)
ISAAC = dict(mode="rotation", max_pos=5, mom=126, rebal="M", mkt="none", exit_mkt=False, stop_atr=None, trail_atr=None)


def trio_grid() -> dict:
    timo = [dict(mode="pullback", rsi=rs, max_hold=mh, stop_atr=sa, max_pos=mp, risk=0.0075, mkt=mk, mom=126, rank=rk)
            for rs, mh, sa, mp, mk, rk in itertools.product((5, 10, 15), (5, 10), (None, 2.0, 3.0), (5, 8, 10), ("200", "50", "none"),
                                                            ("mom", "rsi", "lowvol"))]
    chacha = [dict(mode="trendpull", tol=tl, exit_ma=em, stop_atr=2.0, trail_atr=tr, max_pos=mp, risk=0.005, mkt=mk,
                   exit_mkt=ex, rank=rk, mom=126)
              for tl, em, tr, mp, mk, ex, rk in itertools.product((0.0, 0.01, 0.02), (20, 50), (None, 3.0), (5, 8),
                                                                  ("200", "50"), (False, True), ("lowvol", "mom"))]
    return {"티모": timo, "차차": chacha}


def trio_main():
    """2016~2026 나스닥100으로 고르고(학습 16~21 → 확인 22~23 플러스), 24~26과 관심종목은 확인만."""
    t0 = time.time()
    X = indicators(load("2014-06-01"))
    W = [s for s in T.WATCHLIST if s in X["cols"]]
    N = [s for s in NDX if s in X["cols"]]
    picks = {"아이작": ISAAC}
    for who, G in trio_grid().items():
        rows = [(P, {k: simulate(P, X, a, b, N) for k, (a, b) in LONG.items()}) for P in G]
        ranked = sorted(rows, key=lambda x: -x[1]["학습"]["calmar"])
        picks[who] = next((x for x in ranked if x[1]["확인"]["ret"] > 0), ranked[0])[0]
        ck = np.mean([x[1]["확인"]["ret"] > 0 for x in rows])
        hd = np.array([x[1]["최종시험"]["ret"] for x in rows])
        print(f"{who} 후보 {len(G)}개 — 22~23 플러스 {ck * 100:.0f}% · 24~26 플러스 {np.mean(hd > 0) * 100:.0f}% (중앙값 {np.median(hd) * 100:+.1f}%)")
    picks["돌쇠"] = CURRENT
    print(f"시뮬 {time.time() - t0:.0f}초")
    curves = {}
    for who, P in picks.items():
        print(f"\n[{who}] {desc(P)}")
        for uni_name, U in (("나스닥100", N), ("관심종목", W)):
            for k, (a, b) in LONG.items():
                r = simulate(P, X, a, b, U)
                print(f"  {uni_name:<5} {k:<5} {fmt(r)}")
                if uni_name == "관심종목" and k == "최종시험":
                    curves[who] = r["curve"]
                    rec = simulate(P, X, "2026-07-01", "2026-10-23", W)
                    print(f"  관심종목 최근 4개월(26.07~10) 거래 {rec['n']}회")
    print("\n■ 연도별 (매년 $20,000 · 나스닥100)")
    yrs = range(2016, 2027)
    print("  " + " " * 8 + " ".join(f"{y:>7}" for y in yrs))
    for who, P in picks.items():
        print(f"  {who:<6}" + " ".join(f"{simulate(P, X, f'{y}-01-01', f'{y}-12-31', N)['ret'] * 100:+7.1f}" for y in yrs))
    print(f"  {'QQQ':<6}" + " ".join(f"{bh(X, ['QQQ'], f'{y}-01-01', f'{y}-12-31') * 100:+7.1f}" for y in yrs))
    rets = pd.DataFrame(curves).pct_change().dropna()
    print("\n■ 일간 수익률 상관 (관심종목 24~26) — 낮을수록 서로 다르게 움직임")
    print(rets.corr().round(2).to_string())
    out = LAB / "swing_trio.json"
    out.write_text(json.dumps(T._clean({k: dict(params=P, desc=desc(P)) for k, P in picks.items()}), ensure_ascii=False, indent=1, default=str))
    print(f"\n저장: {out} · 총 {time.time() - t0:.0f}초")


if __name__ == "__main__":
    import sys
    trio_main() if "trio" in sys.argv else long_main() if "long" in sys.argv else main()
