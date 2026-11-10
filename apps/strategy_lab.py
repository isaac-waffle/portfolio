"""기관·유명 트레이더가 쓰는 공개 전략을 '발표된 규칙 그대로' 일봉으로 재현 — 숫자를 우리 데이터에 맞춰 고르지 않음(과최적화 방지).

  .venv/bin/python apps/strategy_lab.py        # 표 출력 + journal/traders/lab/strategy_result.json

ETF 전략 (2007~, 자산배분·추세추종 — CTA/은행 리스크 프리미아 데스크가 하는 것)
  faber      Faber GTAA: 5개 자산을 10개월선 위일 때만 20%씩 보유
  tsmom      시계열 모멘텀(Moskowitz·Ooi·Pedersen, AQR): 1·3·6·12개월 수익률이 플러스인 만큼 보유, 변동성 역가중 (tsmom12 = 12개월만)
  dual       Antonacci 듀얼 모멘텀(GEM): 미국 vs 해외 주식 중 강한 쪽, 둘 다 단기채보다 못하면 채권
  ma200      SPY 200일선 위에서만 보유 (Paul Tudor Jones의 '200일선 아래는 안 산다')
  voltgt     변동성 타기팅: 최근 20일 변동성이 클수록 SPY 비중을 줄임 (목표 연 10%)
  tom        월말·월초 효과: 월말 마지막 날~다음 달 3거래일만 SPY 보유
  ibs        IBS 평균회귀: QQQ가 그날 저가 근처에서 마감하면 종가 매수, 전일 고가 위 마감 시 매도
  rsi2       Connors RSI(2): SPY 200일선 위 + RSI(2)<10 종가 매수, 5일선 위 마감 시 매도
  night      오버나이트: SPY를 종가에 사서 다음 날 시가에 팖
주식 전략 (2015~, 나스닥100+관심종목 약 120개 — 지금 구성 종목이라 생존 편향으로 수익이 부풀려짐)
  mom12      횡단면 모멘텀(Jegadeesh·Titman): 12개월(최근 1개월 제외) 수익률 상위 10종목, 매월
  hi52       52주 신고가 근접(George·Hwang): 신고가에 가장 가까운 10종목, 매월
  lowvol     저변동성: 60일 변동성 하위 10종목, 매월
  rev5       단기 반전(스탯아브의 기본형): 5일 수익률 하위 10종목, 매주
  minervini  Minervini 트렌드 템플릿: 50>150>200일선·200일선 상승·52주 고점 25% 이내·저점 대비 +30% 통과 종목 중 상대강도 상위 10, 매주
  mom12v·minerviniv  위 둘에 변동성 스케일링(Barroso·Santa-Clara)을 얹은 것 — 수익은 줄지만 낙폭이 크게 줄어듦
  ep         에피소딕 피벗(Qullamaggie)·실적 후 표류: 갭 +8% 이상·거래량 3배·고가권 마감 → 다음 날 시가 매수, 20일 보유
  nightmom   종목 오버나이트(Lou·Polk·Skouras): 모멘텀 상위 10종목을 종가 매수·시가 매도

체결: 종가에 신호 → 다음 날 시가 매매 (ibs·rsi2·night는 종가 매매). 비용 편도 0.05%, 레버리지·공매도 없음.
구간: 장기(시작~2023) / 학습(2024-01~2025-06) / 확인(2025-07~12) / 시험(2026-01~).
"""
from __future__ import annotations

import json
import pickle
import warnings

import numpy as np
import pandas as pd

import traders as T

warnings.filterwarnings("ignore")
LAB = T.DATA / "lab"
COST = 0.0005
PERIODS = {"장기": ("2007-01-01", "2023-12-31"), "학습": ("2024-01-01", "2025-06-30"), "확인": ("2025-07-01", "2025-12-31"), "시험": ("2026-01-01", "2026-12-31")}


def load():
    E = pickle.load(open(LAB / "etf_daily.pkl", "rb"))
    S = pickle.load(open(LAB / "swing_daily_2014.pkl", "rb"))
    for F in (E, S):
        for k in F:
            F[k] = F[k].copy()
            F[k].index = pd.DatetimeIndex(F[k].index).tz_localize(None)
    return E, S


NEXT_DAY = None  # 실시간(paper_desk)에서만 넣음: 다음 거래일 — 마지막 행이 월말·주말인지 알기 위해


def month_end(idx: pd.DatetimeIndex) -> np.ndarray:
    m = idx.to_period("M")
    return np.append(m[1:] != m[:-1], bool(NEXT_DAY and pd.Period(NEXT_DAY, "M") != m[-1]))


def week_end(idx: pd.DatetimeIndex) -> np.ndarray:
    w = idx.to_period("W")
    return np.append(w[1:] != w[:-1], bool(NEXT_DAY and pd.Period(NEXT_DAY, "W") != w[-1]))


def hold(W: pd.DataFrame, when: np.ndarray) -> pd.DataFrame:
    """when인 날에만 비중을 새로 정하고 그 사이엔 유지."""
    return W.where(pd.Series(when, index=W.index), axis=0).ffill().fillna(0)


def topn(score: pd.DataFrame, n: int, ok: pd.DataFrame | None = None) -> pd.DataFrame:
    s = score.where(ok) if ok is not None else score
    r = s.rank(axis=1, ascending=False, method="first")
    return (r <= n).astype(float).div(n)


def bt(W: pd.DataFrame, F: dict, mode="open", cost=COST) -> pd.Series:
    """W[t] = t일 종가에 정한 비중. open: 다음 날 시가에 체결 / close: 그날 종가에 체결 / night: 종가→다음 시가만 보유."""
    O, C = F["Open"][W.columns], F["Close"][W.columns]
    W = W.fillna(0)
    if mode == "open":
        r = (W.shift(1) * (O.shift(-1) / O - 1)).sum(1) - cost * (W.shift(1) - W.shift(2)).abs().sum(1)
    elif mode == "close":
        r = (W.shift(1) * (C / C.shift(1) - 1)).sum(1) - cost * (W.shift(1) - W.shift(2)).abs().sum(1)
    else:
        r = (W.shift(1) * (O / C.shift(1) - 1)).sum(1) - 2 * cost * W.shift(1).abs().sum(1)
    return r.fillna(0)


def stat(r: pd.Series, a: str, b: str) -> dict | None:
    x = r[(r.index >= a) & (r.index <= b)]
    if len(x) < 60:
        return None
    eq = (1 + x).cumprod()
    yrs = len(x) / 252
    return dict(cagr=float(eq.iloc[-1] ** (1 / yrs) - 1), ret=float(eq.iloc[-1] - 1), sharpe=float(x.mean() / x.std() * np.sqrt(252)) if x.std() > 0 else 0.0,
                dd=float((eq / eq.cummax() - 1).min()), expo=float((x != 0).mean()))


def volscale(W: pd.DataFrame, F: dict, target=0.20) -> pd.DataFrame:
    """변동성 스케일링(Barroso·Santa-Clara 2015): 전략 자체의 최근 60일 변동성이 목표(연 20%)보다 크면 그만큼 비중을 줄임. 레버리지는 안 씀."""
    rv = bt(W, F, "open").rolling(60).std() * np.sqrt(252)
    return W.mul((target / rv).clip(upper=1).fillna(1), axis=0)


def rsi(c: pd.Series, n: int) -> pd.Series:
    d = c.diff()
    return 100 - 100 / (1 + d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean() / (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean())


# ───────────────────────── ETF ─────────────────────────
def etf_strategies(E: dict) -> dict:
    C, H, L = E["Close"], E["High"], E["Low"]
    idx = C.index
    me = month_end(idx)
    out = {}
    g5 = ["SPY", "EFA", "IEF", "VNQ", "DBC"]
    out["faber"] = (hold((C[g5] > C[g5].rolling(210).mean()).astype(float) * 0.2, me), "open")
    a10 = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "GLD", "DBC", "VNQ"]
    vol = C[a10].pct_change().rolling(60).std() * np.sqrt(252)
    w = ((C[a10].shift(21) / C[a10].shift(252) - 1) > 0) * (0.10 / vol).clip(upper=1) / len(a10) * 2
    out["tsmom12"] = (hold(w.div(w.sum(1).clip(lower=1), axis=0), me), "open")
    # 강화: 1·3·6·12개월 추세를 평균 (한 기간에만 기대지 않음 — CTA들이 쓰는 앙상블). 2020년 −5% → +5%, 장기 낙폭 −22% → −14%
    sig = sum(((C[a10] / C[a10].shift(n) - 1) > 0).astype(float) for n in (21, 63, 126, 252)) / 4
    w = sig * (0.10 / vol).clip(upper=1) / len(a10) * 2
    out["tsmom"] = (hold(w.div(w.sum(1).clip(lower=1), axis=0), me), "open")
    r12 = C / C.shift(252) - 1
    best = np.where(r12["SPY"] > r12["EFA"], "SPY", "EFA")
    pick = np.where(np.maximum(r12["SPY"], r12["EFA"]) > r12["SHY"], best, "IEF")
    w = pd.DataFrame({s: (pick == s).astype(float) for s in ["SPY", "EFA", "IEF"]}, index=idx)
    w[r12["SPY"].isna()] = 0
    out["dual"] = (hold(w, me), "open")
    out["ma200"] = (hold((C[["SPY"]] > C[["SPY"]].rolling(200).mean()).astype(float), me), "open")
    rv = C["SPY"].pct_change().rolling(20).std() * np.sqrt(252)
    out["voltgt"] = (hold((0.10 / rv).clip(upper=1).to_frame("SPY"), week_end(idx)), "open")
    m = idx.to_period("M")
    dom = pd.Series(1, index=idx).groupby(m).cumsum().to_numpy()  # 그 달의 몇 번째 거래일
    out["tom"] = (pd.DataFrame({"SPY": ((np.append(me[1:], False)) | me | (dom <= 2)).astype(float)}, index=idx), "close")
    ibs = (C["QQQ"] - L["QQQ"]) / (H["QQQ"] - L["QQQ"])
    pos, w = 0, []
    for i in range(len(idx)):
        if pos and C["QQQ"].iat[i] > H["QQQ"].iat[i - 1]:
            pos = 0
        elif not pos and ibs.iat[i] < 0.2:
            pos = 1
        w.append(pos)
    out["ibs"] = (pd.DataFrame({"QQQ": w}, index=idx, dtype=float), "close")
    c = C["SPY"]
    r2, ma200, ma5 = rsi(c, 2), c.rolling(200).mean(), c.rolling(5).mean()
    pos, w = 0, []
    for i in range(len(idx)):
        if pos and c.iat[i] > ma5.iat[i]:
            pos = 0
        elif not pos and c.iat[i] > ma200.iat[i] and r2.iat[i] < 10:
            pos = 1
        w.append(pos)
    out["rsi2"] = (pd.DataFrame({"SPY": w}, index=idx, dtype=float), "close")
    out["night"] = (pd.DataFrame({"SPY": 1.0}, index=idx), "night")
    out["SPY 보유"] = (pd.DataFrame({"SPY": 1.0}, index=idx), "close")
    return out


# ───────────────────────── 주식 ─────────────────────────
def stock_strategies(S: dict) -> dict:
    O, H, L, C, V = (S[k] for k in ("Open", "High", "Low", "Close", "Volume"))
    idx = C.index
    me, we = month_end(idx), week_end(idx)
    liquid = ((C * V).rolling(20).mean() >= 2e7) & (C >= 5)
    up = C > C.rolling(200).mean()
    out = {}
    out["mom12"] = (hold(topn(C.shift(21) / C.shift(252) - 1, 10, liquid), me), "open")
    hi = C.rolling(252).max()
    out["hi52"] = (hold(topn(C / hi, 10, liquid & hi.notna()), me), "open")
    out["lowvol"] = (hold(topn(-C.pct_change().rolling(60).std(), 10, liquid & up), me), "open")
    out["rev5"] = (hold(topn(-(C / C.shift(5) - 1), 10, liquid & up), we), "open")
    m50, m150, m200 = C.rolling(50).mean(), C.rolling(150).mean(), C.rolling(200).mean()
    lo = C.rolling(252).min()
    tt = liquid & (C > m50) & (m50 > m150) & (m150 > m200) & (m200 > m200.shift(22)) & (C >= 0.75 * hi) & (C >= 1.3 * lo)
    rs = (C / C.shift(126) - 1).rank(axis=1, pct=True)
    out["minervini"] = (hold(topn(rs, 10, tt & (rs >= 0.7)), we), "open")
    gap = O / C.shift(1) - 1
    ev = liquid.shift(1).fillna(False) & (gap >= 0.08) & (V >= 3 * V.rolling(50).mean().shift(1)) & ((C - L) / (H - L) >= 0.5)
    w = ev.astype(float).rolling(20, min_periods=1).max() * 0.1
    out["ep"] = (w.div((w.sum(1) / 1.0).clip(lower=1), axis=0), "open")
    out["mom12v"] = (volscale(out["mom12"][0], S), "open")
    out["minerviniv"] = (volscale(out["minervini"][0], S), "open")
    out["nightmom"] = (hold(topn(C.shift(21) / C.shift(252) - 1, 10, liquid), me), "night")
    eq = liquid.astype(float)
    out["전 종목 균등 보유"] = (hold(eq.div(eq.sum(1).clip(lower=1), axis=0), me), "open")
    return out


KO = {"faber": "Faber 10개월선 자산배분", "tsmom": "추세추종 (1·3·6·12개월)", "tsmom12": "추세추종 (12개월만)",
      "mom12v": "12개월 모멘텀 + 변동성 조절", "minerviniv": "Minervini + 변동성 조절", "dual": "듀얼 모멘텀 (Antonacci)", "ma200": "SPY 200일선 필터",
      "voltgt": "변동성 타기팅", "tom": "월말·월초 효과", "ibs": "IBS 평균회귀 (QQQ)", "rsi2": "Connors RSI(2) (SPY)", "night": "오버나이트 (SPY)",
      "mom12": "12개월 모멘텀 상위 10", "hi52": "52주 신고가 근접 10", "lowvol": "저변동성 10", "rev5": "5일 반전 10",
      "minervini": "Minervini 트렌드 템플릿", "ep": "에피소딕 피벗 (갭+거래량)", "nightmom": "모멘텀 종목 오버나이트"}


def main():
    E, S = load()
    res = {}
    for grp, F, strat in (("ETF", E, etf_strategies(E)), ("주식", S, stock_strategies(S))):
        print(f"\n── {grp} ({F['Close'].index[0].date()} ~ {F['Close'].index[-1].date()}) ──")
        print(f"{'':28s}" + "".join(f"{k:>26s}" for k in PERIODS))
        for name, (W, mode) in strat.items():
            r = bt(W, F, mode)
            r = r[r.index >= W.index[min(260, len(W) - 1)]]
            row = {k: stat(r, a, b) for k, (a, b) in PERIODS.items()}
            res[name] = dict(group=grp, name=KO.get(name, name), **row)
            print(f"{KO.get(name, name):28s}" + "".join(
                f"{s['cagr'] * 100:+7.1f}%/y S{s['sharpe']:+.2f} DD{s['dd'] * 100:4.0f}%  " if s else f"{'-':>26s}" for s in row.values()))
    (LAB / "strategy_result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float))


if __name__ == "__main__":
    main()
