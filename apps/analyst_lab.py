"""애널리스트 의견·은행 목표가·실적 서프라이즈가 주가를 맞히는지 — 트레이더가 '고려'하게 하기 전에 과거 이력으로 확인.

  .venv/bin/python apps/analyst_lab.py fetch   # Yahoo에서 종목별 의견 변경·목표가 이력, 실적 발표 이력 → journal/traders/lab/analyst.pkl
  .venv/bin/python apps/analyst_lab.py         # 사건 뒤 수익률 + 포트폴리오 백테스트 → 표 + journal/traders/lab/analyst_result.json

데이터: Yahoo Finance의 upgrades_downgrades(증권사·등급 변경·목표가 현재/직전, 시각 포함)와 earnings_dates(예상 EPS·실제·서프라이즈%).
  종목은 나스닥100 + 관심종목(지금 구성 — 생존 편향). 가격은 swing_lab의 일봉.
체결: 사건 시각 뒤 '처음 오는 시가'에 삼 (개장 전 발표면 그날 시가, 장중·마감 뒤면 다음 날 시가). 비용 편도 0.05%.
초과수익 = 종목 수익률 − 같은 기간 전 종목 평균 (시장이 오른 덕을 빼기 위해).
구간: 장기(~2023) / 학습(24.01~25.06) / 확인(25.07~12) / 시험(26.01~).

결과 (2026-11-17, 124종목 · 사건 47,276건) — 매매 신호로 채택 안 함, 참고 정보로만 씀
  등급 상향·하향: 뒤 5·20·60일 초과수익이 구간마다 부호가 바뀜 (상향 60일: 장기 −53bp · 확인 −587bp · 시험 +199bp).
  목표가 +15% 이상 상향: 60일 뒤 초과 +68 · +151 · +1554 · +721bp로 네 구간 모두 플러스 — 유일하게 일관된 것. 하지만 목표가는 주가가 오른 뒤에 올리므로
    '직전 60일 수익률이 비슷한 종목'과 비교하면 20일 뒤 −12bp(장기) · +42bp(학습, t 0.8) · +234 · +137 — 2025년 하반기 이후에만 보임.
  포트폴리오: 큰 상향−하향 상위 10(60일)은 샤프 1.07·1.29·1.25·2.20이지만 전 종목 균등 보유(1.17·1.18·1.51·1.67)를 못 이기고 낙폭 −42%.
    모멘텀 상위 종목에서 하향 우위 종목을 빼거나 큰 상향 종목만 고르는 필터도 기존 모멘텀보다 낫지 않음.
  실적 서프라이즈: +5% 이상 뒤 표류 없음(20일: −10 · −87 · +112 · −38bp).
  주의: Yahoo 이력은 2018년 전이 성김(연 500~800건 → 2024년 5,000건). t값은 같은 날 여러 사건이 겹쳐 부풀려져 있음.
"""
from __future__ import annotations

import json
import pickle
import sys
import time
import warnings

import numpy as np
import pandas as pd
import yfinance as yf

import strategy_lab as L
import traders as T

warnings.filterwarnings("ignore")
LAB = T.DATA / "lab"
FILE = LAB / "analyst.pkl"
PERIODS = {"장기": ("2016-01-01", "2023-12-31"), "학습": ("2024-01-01", "2025-06-30"), "확인": ("2025-07-01", "2025-12-31"), "시험": ("2026-01-01", "2026-12-31")}


def universe() -> list:
    return sorted(set(T.WATCHLIST) | set(T.NDX))


def fetch():
    out = pickle.load(open(FILE, "rb")) if FILE.exists() else {}
    for s in universe():
        if s in out:
            continue
        row = {}
        for key in ("upgrades_downgrades", "earnings_dates"):
            for _ in range(3):
                try:
                    tk = yf.Ticker(s)
                    row[key] = tk.get_earnings_dates(limit=60) if key == "earnings_dates" else tk.upgrades_downgrades
                    break
                except Exception as e:  # 한도·일시 오류는 잠깐 쉬었다 다시
                    row[key] = None
                    print(f"{s} {key}: {type(e).__name__} {str(e)[:80]}", flush=True)
                    time.sleep(5)
            time.sleep(0.4)
        out[s] = row
        ud, ed = row.get("upgrades_downgrades"), row.get("earnings_dates")
        print(f"{s} 의견 {0 if ud is None else len(ud)}건 · 실적 {0 if ed is None else len(ed)}건", flush=True)
        pickle.dump(out, open(FILE, "wb"))


# ───────────────────────── 사건 표 ─────────────────────────
def ny_naive(idx) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(idx)
    return (idx.tz_localize("UTC") if idx.tz is None else idx).tz_convert(T.NY).tz_localize(None)


def trade_day(ts: pd.DatetimeIndex, days: pd.DatetimeIndex) -> np.ndarray:
    """사건 시각 뒤 처음 오는 시가의 날짜 위치 (없으면 −1). 09:30 전이면 그날, 이후면 다음 거래일."""
    d = ts.normalize() + pd.to_timedelta((ts.hour * 60 + ts.minute >= 570).astype(int), unit="D")
    i = days.searchsorted(d)
    return np.where(i < len(days), i, -1)


def events(raw: dict, days: pd.DatetimeIndex, C: pd.DataFrame) -> pd.DataFrame:
    """종목별 의견·목표가·실적 사건을 한 표로. kind: up/down(등급 변경) · pt(목표가 변경, chg=변경률, upside=직전 종가 대비) · earn(서프라이즈%)."""
    rows = []
    for s, r in raw.items():
        if s not in C.columns:
            continue
        ud = r.get("upgrades_downgrades")
        if ud is not None and len(ud):
            ud = ud[~ud.index.isna()]
            ti = trade_day(ny_naive(ud.index), days)
            cur, pri = pd.to_numeric(ud.get("currentPriceTarget"), errors="coerce").to_numpy(), pd.to_numeric(ud.get("priorPriceTarget"), errors="coerce").to_numpy()
            for i, act, c, p, firm in zip(ti, ud["Action"].to_numpy(), cur, pri, ud["Firm"].to_numpy()):
                if i < 1:
                    continue
                if act in ("up", "down"):
                    rows.append((s, i, act, np.nan, np.nan, firm))
                if c > 0 and p > 0 and c != p:
                    prev = C[s].iat[i - 1]
                    rows.append((s, i, "pt", c / p - 1, c / prev - 1 if prev > 0 else np.nan, firm))
        ed = r.get("earnings_dates")
        if ed is not None and len(ed):
            ed = ed[ed["Surprise(%)"].notna()]
            for i, x in zip(trade_day(ny_naive(ed.index), days), ed["Surprise(%)"].to_numpy()):
                if i >= 1:
                    rows.append((s, i, "earn", float(x) / 100, np.nan, ""))
    return pd.DataFrame(rows, columns=["sym", "i", "kind", "chg", "upside", "firm"])


def fwd(O: pd.DataFrame, n: int) -> pd.DataFrame:
    """그날 시가에 사서 n거래일 뒤 시가에 판 수익률 − 같은 기간 전 종목 평균."""
    r = O.shift(-n) / O - 1
    return r.sub(r.mean(1), axis=0)


def study(ev: pd.DataFrame, O: pd.DataFrame, days: pd.DatetimeIndex, name: str, horizons=(5, 20, 60)) -> dict:
    """사건 뒤 초과수익 평균(bp)과 t값을 구간별로. 같은 종목·같은 날 사건은 하나로."""
    ev = ev.drop_duplicates(["sym", "i"])
    col = O.columns.get_indexer(ev.sym)
    out, txt = {}, []
    per = np.select([(days[ev.i] >= a) & (days[ev.i] <= b) for a, b in PERIODS.values()], list(PERIODS), "-")
    for n in horizons:
        x = fwd(O, n).to_numpy()[ev.i.to_numpy(), col]
        cells = []
        for k in PERIODS:
            v = x[(per == k) & np.isfinite(x)]
            m, t = (v.mean() * 1e4, v.mean() / v.std() * np.sqrt(len(v))) if len(v) > 5 and v.std() > 0 else (np.nan, np.nan)
            out[f"{n}d_{k}"] = dict(n=int(len(v)), bp=float(m), t=float(t))
            cells.append(f"{k} {m:+5.0f}bp t{t:+.1f} ({len(v)})")
        txt.append(f"   {n:2d}일 뒤 | " + " | ".join(cells))
    print(f"\n[{name}]\n" + "\n".join(txt))
    return out


# ───────────────────────── 포트폴리오 ─────────────────────────
def daily_sum(ev: pd.DataFrame, val: np.ndarray, days: pd.DatetimeIndex, cols: pd.Index) -> pd.DataFrame:
    """사건 값을 (거래일 × 종목) 표로 합침. i는 '그 시가에 살 수 있는 날'이라, 전날 종가 시점엔 아직 모르는 정보일 수 있음 → 쓰는 쪽에서 하루 늦춰 씀."""
    M = pd.DataFrame(0.0, index=days, columns=cols)
    np.add.at(M.to_numpy(), (ev.i.to_numpy(), cols.get_indexer(ev.sym)), val)
    return M


def portfolios(ev: pd.DataFrame, S: dict) -> dict:
    """매주 다시 고르는 10종목 포트폴리오. 신호는 '그 주 마지막 종가까지 알려진 사건'만 씀 (i ≤ 그날 → 그날 시가 전에 나온 것)."""
    O, C, V = S["Open"], S["Close"], S["Volume"]
    days, we = C.index, L.week_end(C.index)
    liquid = ((C * V).rolling(20).mean() >= 2e7) & (C >= 5)
    pt = ev[ev.kind == "pt"]
    rev = daily_sum(pt, np.sign(pt.chg.to_numpy()), days, C.columns)       # 목표가 상향 − 하향 건수
    grade = ev[ev.kind.isin(["up", "down"])]
    net = daily_sum(grade, np.where(grade.kind == "up", 1.0, -1.0), days, C.columns)
    earn = ev[ev.kind == "earn"]
    sue = daily_sum(earn, earn.chg.to_numpy(), days, C.columns)
    had = daily_sum(earn, np.ones(len(earn)), days, C.columns)
    out = {}
    for n in (20, 60):
        s = rev.rolling(n).sum()
        out[f"목표가 상향 우위 {n}일"] = L.hold(L.topn(s, 10, liquid & (s > 0)), we)
    s = (rev.rolling(60).sum() + 3 * net.rolling(60).sum())
    out["목표가+등급 60일"] = L.hold(L.topn(s, 10, liquid & (s > 0)), we)
    last = sue.where(had > 0).ffill(limit=60)                                 # 최근 실적 서프라이즈 (60거래일까지 유효)
    out["실적 서프라이즈 상위"] = L.hold(L.topn(last, 10, liquid & (last > 0)), we)
    mom = C.shift(21) / C.shift(252) - 1
    s = rev.rolling(60).sum()
    out["12개월 모멘텀 상위 30 중 목표가 상향 우위"] = L.hold(L.topn(s, 10, liquid & (mom.rank(axis=1, ascending=False) <= 30) & (s > 0)), we)
    out["12개월 모멘텀 상위 10 (비교)"] = L.hold(L.topn(mom, 10, liquid), L.month_end(days))
    eq = liquid.astype(float)
    out["전 종목 균등 (비교)"] = L.hold(eq.div(eq.sum(1).clip(lower=1), axis=0), L.month_end(days))
    return out


def main():
    raw = pickle.load(open(FILE, "rb"))
    _, S = L.load()
    O, C = S["Open"], S["Close"]
    days = C.index
    ev = events(raw, days, C)
    ev = ev[ev.i < len(days)]
    print(f"{len(raw)}종목 · 사건 {len(ev):,}건 (등급 상향 {(ev.kind == 'up').sum()} · 하향 {(ev.kind == 'down').sum()} · 목표가 변경 {(ev.kind == 'pt').sum()} · 실적 {(ev.kind == 'earn').sum()}) · "
          f"{days[ev.i.min()].date()} ~ {days[ev.i.max()].date()}")
    pt = ev[ev.kind == "pt"]
    res = dict(study={}, port={})
    for name, e in (("등급 상향", ev[ev.kind == "up"]), ("등급 하향", ev[ev.kind == "down"]),
                    ("목표가 상향", pt[pt.chg > 0]), ("목표가 +15% 이상 상향", pt[pt.chg >= 0.15]), ("목표가 하향", pt[pt.chg < 0]),
                    ("새 목표가가 주가보다 30% 넘게 높음", pt[pt.upside >= 0.30]), ("새 목표가가 주가보다 낮음", pt[pt.upside < 0]),
                    ("실적 서프라이즈 +5% 이상", ev[(ev.kind == "earn") & (ev.chg >= 0.05)]), ("실적 예상 하회", ev[(ev.kind == "earn") & (ev.chg < 0)])):
        res["study"][name] = study(e, O, days, name)
    print("\n■ 매주 고르는 10종목 포트폴리오 (다음 날 시가 체결, 편도 0.05%)")
    for name, W in portfolios(ev, S).items():
        r = L.bt(W, S, "open")
        row = {k: L.stat(r, a, b) for k, (a, b) in PERIODS.items()}
        res["port"][name] = row
        print(f"  {name:36s}" + " | ".join(f"{k} {(s['ret'] / (len(r[a:b]) / 252)) * 100:+6.1f}%/y S{s['sharpe']:+.2f} DD{s['dd'] * 100:4.0f}%"
                                         for (k, (a, b)), s in zip(PERIODS.items(), row.values()) if s))
    (LAB / "analyst_result.json").write_text(json.dumps(res, ensure_ascii=False, default=float))


if __name__ == "__main__":
    fetch() if sys.argv[1:] == ["fetch"] else main()
