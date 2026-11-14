"""10분 단타형(번개) 규칙 찾기 — 10분마다 판단해서 한 종목을 사고, 10분 뒤 다시 판단(같은 종목이 또 1등이면 그대로 보유).

  .venv/bin/python apps/scalp_lab.py          # 표 출력 + journal/traders/lab/scalp_result.json

데이터: traders_lab 캐시의 Alpaca 5분봉(관심종목, 2024-01~2026-10). 10분 = 5분봉 2개.
체결: 5분봉 종가에 판단 → 다음 봉 시가에 체결. 09:40~15:40 사이에만 사고 15:50에 전부 정리. 롱만, 한 번에 한 종목(계좌 전부).
비용: 앱과 같은 편도 슬리피지 0.05% + 주문당 $2 ($20,000 기준 0.01%) = 왕복 0.12%. 비교용으로 왕복 0.02%(대형주 실제 스프레드 수준)·0%도 같이 봄.
검증: 학습(24.01~25.06)에서 고르고 → 확인(25.07~12) → 시험(26.01~10)은 고르는 데 안 씀.

2차로 surge·dip에 '버티면 10분 더(keep)'와 '시장 필터(mkt: SPY가 VWAP 위)'를 붙여 봄. 결과(2026-11-14): 앱 비용에서 학습 1등은
dip a=1.5 b=3 keep=−1 mkt — 학습 +17.6% · 확인 +6.4% · 시험 +7.0% (하루 0.2건). 176개 중 학습·확인 둘 다 플러스는 3개뿐이고
이웃 설정(keep=0, 시장 필터 없음)은 확인에서 마이너스라 튼튼한 우위라고 보긴 어려움 — 가상 계좌에서 지켜보는 용도.

규칙군 (전부 '직전 10분'을 그 종목의 평소 10분 변동폭으로 나눈 z로 봄)
  surge  급등 추종: 10분 z ≥ a + 거래량 b배 + VWAP 위 → 가장 세게 오른 종목
  dip    눌림 반등: 오늘 오르는 종목(VWAP 위·전일 대비 플러스)이 10분 z ≤ −a로 빠짐 → 가장 많이 빠진 종목
  fade   과매도 반등: VWAP보다 c(ATR 대비) 이상 아래 + 직전 10분은 반등 시작(z > 0)
  trend  30분 추세: 30분 z ≥ a + VWAP 위 + 직전 10분도 플러스
"""
from __future__ import annotations

import itertools
import json

import numpy as np

import quant_lab as Q
import traders as T

NB = Q.NB
SLOTS = list(range(1, 74, 2))  # 판단하는 봉(종가 09:40, 09:50, … 15:40)
APP_COST = T.SLIP + T.FEE / T.START_CASH  # 편도
K10 = np.sqrt(10 / 390)  # 일봉 ATR% → 10분 변동폭 환산


def features(d: Q.Data) -> dict:
    C, O, V = d.C, d.O, d.V
    S, D, _ = C.shape
    a10 = (d.ATRP * K10)[:, :, None]
    r10 = np.full(C.shape, np.nan)
    r10[:, :, 1:] = C[:, :, 1:] / O[:, :, :-1] - 1
    r30 = np.full(C.shape, np.nan)
    r30[:, :, 6:] = C[:, :, 6:] / C[:, :, :-6] - 1
    v10 = np.full(C.shape, np.nan)
    v10[:, :, 1:] = V[:, :, 1:] + V[:, :, :-1]
    return dict(z10=r10 / a10, z30=r30 / (a10 * np.sqrt(3)), rv=v10 / Q.lagmean(v10, 14),
                vw=(C / d.VWAP - 1) / d.ATRP[:, :, None], day=C / d.PC[:, :, None] - 1)


def score(F: dict, t: int, rule: str, a: float, b: float) -> np.ndarray:
    """그 시각(t) 종목별 점수 — 조건을 못 채우면 −inf."""
    z10, z30, rv, vw, day = (F[k][:, :, t] for k in ("z10", "z30", "rv", "vw", "day"))
    if rule == "surge":
        ok, sc = (z10 >= a) & (rv >= b) & (vw > 0), z10
    elif rule == "dip":
        ok, sc = (z10 <= -a) & (vw > 0) & (day > 0) & (rv >= b), -z10
    elif rule == "fade":
        ok, sc = (vw <= -a / 4) & (z10 > 0) & (rv >= b), -vw
    else:
        ok, sc = (z30 >= a) & (vw > 0) & (z10 > 0) & (rv >= b), z30
    return np.where(ok & np.isfinite(sc), sc, -np.inf)


def run(d: Q.Data, F: dict, rule: str, a: float, b: float, keep: float | None = None, mkt: bool = False, cost: float = APP_COST):
    """하루 수익률, 하루 매수 횟수.
    keep: 들고 있는 종목의 직전 10분 z가 이 값보다 크고 VWAP 위면 팔지 않고 10분 더 (None이면 매번 새로 판단)
    mkt:  SPY가 VWAP 위일 때만 새로 삼"""
    D = len(d.days)
    day_r, day_n = np.zeros(D), np.zeros(D)
    held = np.full(D, -1)
    ar = np.arange(D)
    spy = d.ix["SPY"]
    for t in SLOTS:
        sc = np.where(d.liquid, score(F, t, rule, a, b), -np.inf)
        if mkt:
            sc = np.where(F["vw"][spy, :, t][None, :] > 0, sc, -np.inf)
        best = sc.argmax(0)
        pick = np.where(np.isfinite(sc[best, ar]), best, -1)
        if keep is not None:
            h = np.maximum(held, 0)
            pick = np.where((held >= 0) & (F["z10"][h, ar, t] > keep) & (F["vw"][h, ar, t] > 0), held, pick)
        o_in, o_out = d.O[:, :, t + 1], d.O[:, :, t + 3]
        j = np.maximum(pick, 0)
        r = np.where(pick >= 0, np.nan_to_num(o_out[j, ar] / o_in[j, ar] - 1), 0.0)
        change = pick != held
        day_r += r - cost * ((held >= 0) & change) - cost * ((pick >= 0) & change)
        day_n += (pick >= 0) & change
        held = pick
    day_r -= cost * (held >= 0)  # 15:50 정리
    return day_r, day_n


def grid() -> list[dict]:
    """1차: 규칙군 4개를 '매번 새로 판단'으로 (48개). 2차: 비용 전에 신호가 있던 surge·dip에 '버티면 10분 더'와 시장 필터를 붙임 (128개)."""
    G = [dict(rule=r, a=a, b=b) for r, a, b in itertools.product(("surge", "dip", "fade", "trend"), (1.0, 1.5, 2.0, 3.0), (0.0, 1.5, 3.0))]
    return G + [dict(rule=r, a=a, b=b, keep=k, mkt=m) for r, a, b, k, m in itertools.product(
        ("surge", "dip"), (1.5, 2.0, 3.0, 4.0), (1.5, 3.0), (None, 0.0, -1.0, -2.0), (False, True)) if k is not None or m]


LIVE = dict(rule="dip", a=1.5, b=3.0, keep=-1.0, mkt=True)  # traders.py의 번개가 쓰는 규칙 (학습 샤프 1등)


def main():
    d = Q.Data()
    F = features(d)
    print(f"{len(d.syms)}종목 · {len(d.days)}거래일 ({d.days[0]} ~ {d.days[-1]}) · 편도 비용 {APP_COST * 100:.3f}%")
    rows = []
    for g in grid():
        r, n = run(d, F, **g)
        r0, _ = run(d, F, **g, cost=0.0)
        r1, _ = run(d, F, **g, cost=0.0001)
        rows.append(dict(p=g, **{k: Q.stats(r, n, m) for k, m in d.mask.items()},
                         **{f"{k}_0": Q.stats(r0, n, m) for k, m in d.mask.items()},
                         **{f"{k}_1bp": Q.stats(r1, n, m) for k, m in d.mask.items()}))
    live = [x for x in rows if x["학습"]["tpd"] >= 0.2]

    def show(x):
        g = x["p"]
        print(f"  {g['rule']:6s} a={g['a']:<4} b={g['b']:<4} keep={str(g.get('keep')):<5} mkt={int(g.get('mkt', False))}" + "".join(
            f" | {k} {x[k]['ret'] * 100:+7.1f}% S{x[k]['sharpe']:+5.2f} ({x[k]['tpd']:4.1f}건/일) 비용0 {x[k + '_0']['ret'] * 100:+6.1f}% 0.02% {x[k + '_1bp']['ret'] * 100:+6.1f}%"
            for k in Q.PERIODS))

    for rule in ("surge", "dip", "fade", "trend"):
        rs = [x for x in live if x["p"]["rule"] == rule]
        print(f"\n[{rule}] 설정 {len(rs)}개 — 앱 비용으로 학습 플러스 {np.mean([x['학습']['ret'] > 0 for x in rs]) * 100:.0f}% · "
              f"비용 0에서 학습 플러스 {np.mean([x['학습_0']['ret'] > 0 for x in rs]) * 100:.0f}% · 시험 플러스 {np.mean([x['시험_0']['ret'] > 0 for x in rs]) * 100:.0f}%")
        for x in sorted(rs, key=lambda x: -x["학습"]["sharpe"])[:4]:
            show(x)
    best = max(live, key=lambda x: x["학습"]["sharpe"])
    best0 = max(live, key=lambda x: x["학습_0"]["sharpe"])
    print("\n■ 앱 비용 기준 학습 1등" + (" (= 번개가 쓰는 규칙)" if best["p"] == LIVE else ""))
    show(best)
    both = [x for x in live if x["학습"]["ret"] > 0 and x["확인"]["ret"] > 0]
    print(f"  전체 {len(live)}개 중 앱 비용으로 학습·확인 둘 다 플러스 {len(both)}개, 그중 시험도 플러스 {sum(x['시험']['ret'] > 0 for x in both)}개 — 드물어서 우연일 수 있음")
    print("■ 비용 0 기준 학습 1등 (신호 자체의 힘)")
    show(best0)
    (Q.LAB / "scalp_result.json").write_text(json.dumps(dict(cost=APP_COST, best=best, best_gross=best0, rows=rows), ensure_ascii=False, indent=1, default=float))


if __name__ == "__main__":
    main()
