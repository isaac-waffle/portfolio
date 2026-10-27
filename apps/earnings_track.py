"""어닝 예측 기록·채점 — 어닝 대시보드(earnings_app.py)가 발표 전에 보여주는 예측을 저장하고, 발표 후 실제 반응과 비교.

저장하는 예측 (발표 직전 마지막 값으로 고정):
  옵션 내재 변동폭(첫 만기 ATM 스트래들 / 주가), 과거 평균 변동폭(반응일 종가 기준 |변동| 평균),
  시나리오별 상승 확률(과거 EPS 상회·하회 때 반응일에 오른 비율)
채점:
  범위 — 실제 반응일 변동이 옵션 ±1σ·±2σ, 과거 평균 범위 안에 들어왔나
  방향 — 실제 EPS 결과(상회/하회)에 해당하는 시나리오 상승 확률이 50%보다 크면 상승, 작으면 하락으로 보고 맞혔나
         (해당 시나리오 과거 표본이 3번 미만이거나 정확히 50%면 판단 없음)

기록: apps/journal/earnings_preds.json (git 제외)
실행:
  python apps/earnings_track.py loop                 # 20분마다 예측 저장·채점 (대시보드가 켜지면 자동으로 백그라운드 실행)
  python apps/earnings_track.py once                 # 한 번만
  python apps/earnings_track.py backfill 2026-10-12  # 지난 어닝을 재구성 (옵션 가격은 Alpaca 과거 일봉)
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import sys
import threading
import time
import warnings
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")
ET = ZoneInfo("America/New_York")
JOURNAL = Path(__file__).resolve().parent / "journal"
STORE = JOURNAL / "earnings_preds.json"
LOCK = JOURNAL / "earnings_track.lock"
WINDOW_DAYS = 3      # 발표 며칠 전부터 예측을 저장할지
EVERY = 20 * 60      # loop 간격(초)
MIN_N = 3            # 방향 판단에 필요한 시나리오 최소 표본
_store_lock = threading.Lock()


# ───────────────────────── 대시보드와 같은 계산 ─────────────────────────
def _base_react(dates: pd.DatetimeIndex, local: pd.Timestamp):
    """발표 시각 → (기준일 위치, 반응일 위치). 장후면 당일 종가→다음 거래일, 장전이면 전일 종가→당일."""
    d = pd.Timestamp(local.date())
    pos = dates.searchsorted(d)
    if pos >= len(dates) or dates[pos] != d:
        return None
    if local.hour >= 12:
        return pos, pos + 1
    return (pos - 1, pos) if pos > 0 else None


def earnings_reactions(hist: pd.DataFrame, ed: pd.DataFrame | None) -> pd.DataFrame:
    """과거 각 어닝에 대해 반응일(장전→당일, 장후→다음 거래일) 기준 갭·종가·5일 변동."""
    if ed is None or ed.empty or hist.empty:
        return pd.DataFrame()
    h = hist.copy()
    h.index = h.index.tz_localize(None).normalize()
    dates = h.index
    rows = []
    for ts, r in ed.iterrows():
        if pd.isna(r.get("Reported EPS")):
            continue
        local = ts.tz_convert("America/New_York")
        br = _base_react(dates, local)
        if not br or br[1] >= len(dates):
            continue
        base_i, react_i = br
        base = h["Close"].iloc[base_i]
        w5_i = min(react_i + 4, len(dates) - 1)
        sur = r.get("Surprise(%)")
        rows.append({
            "발표일": local.date(), "시점": "장후" if local.hour >= 12 else "장전",
            "EPS 예상": r.get("EPS Estimate"), "EPS 실제": r.get("Reported EPS"),
            "서프라이즈": sur / 100 if pd.notna(sur) else np.nan,
            "갭(시가)": h["Open"].iloc[react_i] / base - 1, "반응일 종가": h["Close"].iloc[react_i] / base - 1,
            "5일 누적": h["Close"].iloc[w5_i] / base - 1,
        })
    return pd.DataFrame(rows)


def implied_move(s: str, earn_date: dt.date | None, price: float):
    """첫 어닝 이후 만기의 ATM 스트래들 가격으로 옵션 시장이 반영한 예상 변동폭을 계산."""
    if not earn_date or not price:
        return None
    try:
        t = yf.Ticker(s)
        exps = [e for e in t.options if dt.date.fromisoformat(e) >= earn_date + dt.timedelta(days=1)]
        if not exps:
            return None
        exp = exps[0]
        ch = t.option_chain(exp)
        calls, puts = ch.calls, ch.puts
        k = calls.iloc[(calls["strike"] - price).abs().argsort()[:1]]["strike"].iloc[0]

        def mid(df):
            r = df[df["strike"] == k].iloc[0]
            m = (r["bid"] + r["ask"]) / 2 if r["bid"] > 0 and r["ask"] > 0 else r["lastPrice"]
            return float(m), float(r.get("impliedVolatility", np.nan))

        c, civ = mid(calls)
        p, piv = mid(puts)
        return {"expiry": exp, "strike": float(k), "straddle": c + p, "move": (c + p) / price, "iv": np.nanmean([civ, piv])}
    except Exception:
        return None


def scenario_stats(react: pd.DataFrame) -> dict:
    """과거 반응으로 만든 예측: 평균 변동폭, 상회·하회 시나리오별 상승 확률과 표본 수."""
    if react.empty:
        return dict(hist_move=None, n_prev=0, beat_up=None, n_beat=0, miss_up=None, n_miss=0)
    day = react["반응일 종가"]
    beat, miss = day[react["서프라이즈"] > 0], day[react["서프라이즈"] <= 0]
    return dict(hist_move=_f(day.abs().mean()), n_prev=len(day),
                beat_up=_f((beat > 0).mean()) if len(beat) else None, n_beat=len(beat),
                miss_up=_f((miss > 0).mean()) if len(miss) else None, n_miss=len(miss))


# ───────────────────────── 저장소 ─────────────────────────
def _f(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), 6)


def load() -> list[dict]:
    try:
        return json.loads(STORE.read_text())
    except Exception:
        return []


def save(recs: list[dict]):
    JOURNAL.mkdir(parents=True, exist_ok=True)
    recs.sort(key=lambda r: (r["date"], r["sym"]))
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(recs, ensure_ascii=False, indent=1))
    os.replace(tmp, STORE)


def watchlist() -> list[str]:
    src = (Path(__file__).resolve().parent / "earnings_app.py").read_text()
    ns: dict = {}
    exec(src[src.index("WATCHLIST = ["):src.index("]", src.index("WATCHLIST = [")) + 1], ns)
    return [s for s in ns["WATCHLIST"] if not s.endswith("-USD")]


def freeze_at(local: pd.Timestamp) -> dt.datetime:
    """예측을 고정하는 시각 — 장후 발표는 당일 장 마감, 장전 발표는 전날 밤(전일 종가 시세가 마지막)."""
    d = local.date()
    if local.hour >= 12:
        return dt.datetime.combine(d, dt.time(16, 0), ET)
    return dt.datetime.combine(d, dt.time(0, 0), ET)


def _ticker_data(s: str):
    t = yf.Ticker(s)
    return t, t.history(period="5y", auto_adjust=False), t.get_earnings_dates(limit=24)


# ───────────────────────── 발표 전: 예측 저장 ─────────────────────────
def snapshot(syms: list[str] | None = None, now: dt.datetime | None = None) -> int:
    now = now or dt.datetime.now(ET)
    n = 0
    for s in syms or watchlist():
        try:
            t = yf.Ticker(s)
            ed = t.get_earnings_dates(limit=24)
        except Exception:
            continue
        if ed is None or ed.empty:
            continue
        up = [ts.tz_convert(ET) for ts, r in ed.iterrows() if pd.isna(r.get("Reported EPS"))]
        up = [ts for ts in up if now < freeze_at(ts) <= now + dt.timedelta(days=WINDOW_DAYS)]
        if not up:
            continue
        local = min(up)
        try:
            hist = t.history(period="5y", auto_adjust=False)
            price = float(t.fast_info["lastPrice"])
        except Exception:
            continue
        react = earnings_reactions(hist, ed)
        im = implied_move(s, local.date(), price)
        rec = dict(sym=s, date=str(local.date()), when="장후" if local.hour >= 12 else "장전",
                   snap_at=now.strftime("%Y-%m-%d %H:%M"), source="실시간", price=round(price, 4),
                   im=_f(im["move"]) if im else None, im_expiry=im["expiry"] if im else None,
                   im_strike=im["strike"] if im else None, **scenario_stats(react))
        with _store_lock:
            recs = [r for r in load() if not (r["sym"] == s and r["date"] == rec["date"])]
            save(recs + [rec])
        n += 1
    return n


# ───────────────────────── 발표 후: 채점 ─────────────────────────
def grade(rec: dict, move: float, sur: float | None) -> dict:
    a = abs(move)
    g = dict(move=_f(move), sur=_f(sur),
             im_in1=None if rec.get("im") is None else bool(a <= rec["im"]),
             im_in2=None if rec.get("im") is None else bool(a <= 2 * rec["im"]),
             hist_in=None if rec.get("hist_move") is None else bool(a <= rec["hist_move"]),
             call=None, dir_hit=None)
    if sur is not None and not np.isnan(sur):
        p, n = (rec.get("beat_up"), rec.get("n_beat", 0)) if sur > 0 else (rec.get("miss_up"), rec.get("n_miss", 0))
        if p is not None and n >= MIN_N and p != 0.5:
            g["call"] = "상승" if p > 0.5 else "하락"
            g["dir_hit"] = bool((move > 0) == (p > 0.5))
    return g


def resolve(now: dt.datetime | None = None) -> int:
    now = now or dt.datetime.now(ET)
    with _store_lock:
        todo = [r for r in load() if "move" not in r and r.get("status") != "취소"]
    done = 0
    for rec in todo:
        d = dt.date.fromisoformat(rec["date"])
        if now.date() < d:
            continue
        try:
            _, hist, ed = _ticker_data(rec["sym"])
        except Exception:
            continue
        row = None
        for ts, r in (ed if ed is not None else pd.DataFrame()).iterrows():
            if ts.tz_convert(ET).date() == d:
                row, local = r, ts.tz_convert(ET)
        if row is None or pd.isna(row.get("Reported EPS")):
            if (now.date() - d).days > 5:  # 날짜가 바뀌었거나 발표가 없음
                _update(rec, dict(status="취소"))
            continue
        h = hist.copy()
        h.index = h.index.tz_localize(None).normalize()
        br = _base_react(h.index, local)
        if not br or br[1] >= len(h.index):
            continue
        react_day = h.index[br[1]].date()
        if react_day > now.date() or (react_day == now.date() and now.time() < dt.time(16, 10)):
            continue  # 반응일 장이 아직 안 끝남
        base, close = h["Close"].iloc[br[0]], h["Close"].iloc[br[1]]
        sur = row.get("Surprise(%)")
        g = grade(rec, close / base - 1, sur / 100 if pd.notna(sur) else None)
        _update(rec, dict(react_date=str(react_day), base_close=_f(base), react_close=_f(close), **g))
        done += 1
    return done


def _update(rec: dict, extra: dict):
    with _store_lock:
        recs = load()
        for r in recs:
            if r["sym"] == rec["sym"] and r["date"] == rec["date"]:
                r.update(extra)
        save(recs)


# ───────────────────────── 지난 어닝 재구성 ─────────────────────────
def _alpaca():
    k, sec = os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY")
    f = Path.home() / ".trading_desk.env"
    if not (k and sec) and f.exists():
        for line in f.read_text().splitlines():
            line = line.strip().removeprefix("export ").strip()
            if "=" in line:
                n, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                k = v if n.strip() == "ALPACA_KEY_ID" else k
                sec = v if n.strip() == "ALPACA_SECRET_KEY" else sec
    return {"APCA-API-KEY-ID": k or "", "APCA-API-SECRET-KEY": sec or ""}


def alpaca_implied(s: str, d: dt.date, base: dt.date, base_close: float):
    """그날 장 마감 기준 ATM 스트래들(옵션 일봉 종가) / 주가. 발표일 다음 날 이후 첫 만기."""
    H = _alpaca()
    cons = []
    for status in ("active", "inactive"):
        try:
            cons += requests.get("https://paper-api.alpaca.markets/v2/options/contracts", headers=H, timeout=20, params=dict(
                underlying_symbols=s, expiration_date_gte=str(d + dt.timedelta(days=1)),
                expiration_date_lte=str(d + dt.timedelta(days=21)), status=status, limit=10000)).json().get("option_contracts", [])
        except Exception:
            pass
    if not cons:
        return None
    exp = min(c["expiration_date"] for c in cons)
    cs = [c for c in cons if c["expiration_date"] == exp]
    k = min({float(c["strike_price"]) for c in cs}, key=lambda x: abs(x - base_close))
    syms = [c["symbol"] for c in cs if float(c["strike_price"]) == k]
    try:
        bars = requests.get("https://data.alpaca.markets/v1beta1/options/bars", headers=H, timeout=20, params=dict(
            symbols=",".join(syms), timeframe="1Day", start=str(base), end=str(base))).json().get("bars", {})
    except Exception:
        return None
    if len(bars) != 2:
        return None
    return {"expiry": exp, "strike": k, "move": sum(v[-1]["c"] for v in bars.values()) / base_close}


def backfill(start: dt.date, end: dt.date | None = None) -> int:
    end = end or dt.datetime.now(ET).date()
    n = 0
    for s in watchlist():
        try:
            _, hist, ed = _ticker_data(s)
        except Exception:
            continue
        if ed is None or ed.empty or hist.empty:
            continue
        h = hist.copy()
        h.index = h.index.tz_localize(None).normalize()
        react = earnings_reactions(hist, ed)
        for ts in ed.index:
            local = ts.tz_convert(ET)
            d = local.date()
            if not (start <= d <= end) or pd.isna(ed.at[ts, "Reported EPS"]):
                continue
            if any(r["sym"] == s and r["date"] == str(d) for r in load()):
                continue  # 실시간 기록이 있으면 그걸 씀
            br = _base_react(h.index, local)
            if not br:
                continue
            base_day, base_close = h.index[br[0]].date(), float(h["Close"].iloc[br[0]])
            im = alpaca_implied(s, d, base_day, base_close)
            prev = react[react["발표일"] < d] if not react.empty else react
            rec = dict(sym=s, date=str(d), when="장후" if local.hour >= 12 else "장전",
                       snap_at=f"{base_day} 16:00", source="재구성", price=round(base_close, 4),
                       im=_f(im["move"]) if im else None, im_expiry=im["expiry"] if im else None,
                       im_strike=im["strike"] if im else None, **scenario_stats(prev))
            with _store_lock:
                save(load() + [rec])
            n += 1
    resolve()
    return n


# ───────────────────────── 백그라운드 실행 ─────────────────────────
def run_once():
    a = snapshot()
    b = resolve()
    return a, b


def loop():
    """프로세스 사이 중복 실행 방지(파일 잠금) 후 20분마다 저장·채점. 이미 다른 곳에서 돌고 있으면 바로 끝남."""
    JOURNAL.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    while True:
        try:
            a, b = run_once()
            print(f"{dt.datetime.now(ET):%m-%d %H:%M} ET 예측 저장 {a}건 · 채점 {b}건", flush=True)
        except Exception as e:
            print("earnings_track 오류:", e, flush=True)
        time.sleep(EVERY)


def start_background():
    """대시보드가 호출 — 데몬 스레드로 loop()."""
    th = threading.Thread(target=loop, name="earnings_track", daemon=True)
    th.start()
    return th


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "loop":
        if loop() is False:
            print("이미 실행 중")
    elif cmd == "backfill":
        print("재구성", backfill(dt.date.fromisoformat(sys.argv[2])), "건")
    else:
        print("예측 저장 %d건 · 채점 %d건" % run_once())
