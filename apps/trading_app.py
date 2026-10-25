"""미국 주식 단타 트레이딩 데스크 (실시간 데이터: Yahoo Finance, 어닝 일정: Earnings Desk 피드)

실행:  .venv/bin/streamlit run apps/trading_app.py   (또는 apps/start.sh 로 허브에서)

매매를 대신하지 않는 보조 도구입니다. 모든 신호는 통계적 참고치이며 투자 조언이 아닙니다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import threading
import time
import uuid
from collections import deque
import warnings
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf
from plotly.subplots import make_subplots
from scipy.stats import norm
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

NY, AMS = ZoneInfo("America/New_York"), ZoneInfo("Europe/Amsterdam")
UP, DOWN, FLAT = "#16a34a", "#dc2626", "#64748b"
EARN_URL = "https://raw.githubusercontent.com/isaac-waffle/portfolio/earnings-data/calendar.json"

CATEGORIES = {
    "반도체": ["NVDA", "AMD", "AVGO", "QCOM", "INTC", "MU", "TSM", "ASML", "AMAT", "LRCX", "MRVL", "ARM", "SKHY"],
    "AI 인프라·클라우드": ["CRWV", "NBIS", "IREN", "SMCI", "DELL", "PENG", "ORCL", "BE"],
    "스토리지": ["SNDK", "WDC", "STX"],
    "빅테크": ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA"],
    "소프트웨어·보안": ["PLTR", "SNOW", "CRWD", "PANW", "IBM", "KARO"],
    "게임·3D": ["TTWO", "RBLX", "U"],
    "양자컴퓨팅": ["IONQ", "QNT"],
    "산업·우주": ["BA", "GE", "SPCX"],
    "소비재": ["MCD", "NKE"],
    "에너지·소재": ["VIST", "STLD"],
    "바이오": ["MRNA"],
    "크립토": ["BTC-USD"],
}
CAT_OF = {s: c for c, ss in CATEGORIES.items() for s in ss}
WATCHLIST = [s for ss in CATEGORIES.values() for s in ss]
MARKET = ["SPY", "QQQ", "IWM", "SMH"]
VIXES = ["^VIX", "^VIX9D", "^VIX3M"]
SECTORS = {"XLK": "기술", "XLC": "커뮤니케이션", "XLY": "경기소비재", "XLF": "금융", "XLV": "헬스케어", "XLI": "산업재",
           "XLE": "에너지", "XLB": "소재", "XLU": "유틸리티", "XLP": "필수소비재", "XLRE": "부동산"}
EXTRA = list(SECTORS) + ["RSP"]  # RSP = S&P500 동일가중
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# 2026 FOMC 결정일 (발표 14:00 ET)
FOMC = {dt.date(2026, 1, 28), dt.date(2026, 3, 18), dt.date(2026, 4, 29), dt.date(2026, 6, 17),
        dt.date(2026, 7, 29), dt.date(2026, 9, 16), dt.date(2026, 10, 28), dt.date(2026, 12, 9)}

# 분 단위 (ET, 자정 기준)
PROFILES = {
    "공격형": dict(
        risk=1.0, min_mkt=35, short_max=45, max_vix=35, max_atr=0.12, min_dvol=2e7, max_trades=6, daily_loss=3.0,
        windows=[(570, 955)], align=False, no_backwardation=False,
        desc="모멘텀·돌파 위주. 오프닝 15분부터 참여, 고RVOL·갭 종목 선호. 승률보다 큰 R배수를 노림."),
    "밸런스형": dict(
        risk=0.5, min_mkt=50, short_max=35, max_vix=28, max_atr=0.08, min_dvol=1e8, max_trades=4, daily_loss=1.5,
        windows=[(575, 690), (840, 945)], align=True, no_backwardation=False,
        desc="시장 방향과 같은 쪽 셋업만. 점심 횡보·마감 직전은 쉼. 추세 + 확인된 거래량."),
    "방어형": dict(
        risk=0.25, min_mkt=65, short_max=25, max_vix=22, max_atr=0.05, min_dvol=5e8, max_trades=2, daily_loss=0.75,
        windows=[(585, 690), (840, 930)], align=True, no_backwardation=True,
        desc="시장이 깨끗할 때만, 대형 유동성 종목의 눌림목·VWAP 지지 매수만. 이벤트일·고변동성은 쉼."),
}
PROFILE_SETUPS = {
    "공격형": {"ORB", "VWAP", "PULL", "GAPGO", "GAPFILL", "FADE", "SQZ", "PDL"},
    "밸런스형": {"ORB", "VWAP", "PULL", "GAPFILL", "FADE", "SQZ", "PDL"},
    "방어형": {"VWAP", "PULL", "PDL"},
}

PHASES = [
    (240, 570, "프리마켓", "갭·뉴스 확인, 핵심 레벨 표시. 유동성 얇음 — 스프레드 주의"),
    (570, 585, "오프닝 15분", "변동성 최대. 공격형만 참여, 나머지는 오프닝 레인지 형성 대기"),
    (585, 690, "오전 추세 구간", "ORB·VWAP 셋업 신뢰도 최고 — 하루의 메인 구간"),
    (690, 840, "점심 횡보", "거래량 급감, 가짜 돌파 잦음 — 사이즈 축소 또는 관망"),
    (840, 900, "오후 재개", "유럽 마감 후 방향 재설정 · FOMC 날은 14:00 발표"),
    (900, 950, "파워아워", "기관 리밸런싱·MOC 물량, 추세 가속 또는 반전"),
    (950, 960, "마감 직전 (MOC)", "15:50 MOC 불균형 공시 — 신규 진입 지양, 청산 정리"),
    (960, 1200, "애프터마켓", "실적 발표 반응 구간. 유동성 얇음"),
]

st.set_page_config(page_title="트레이딩 데스크", layout="wide")
st.markdown("""<style>
html, body, .stApp, button, input, textarea, select {
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Apple SD Gothic Neo", "Helvetica Neue", sans-serif }
[data-testid="stDecoration"] { display: none }
.block-container { padding-top: 2.4rem; padding-bottom: 3rem; max-width: 1440px }
h1 { font-weight: 700 !important; letter-spacing: -.02em }
h2, h3 { font-weight: 600 !important; letter-spacing: -.01em }
.tiles { display: grid; gap: 10px; margin: 4px 0 18px }
@media (max-width: 900px) { .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)) !important } }
.tile, .card { background: rgba(128,128,128,.08); border-radius: 14px; padding: 12px 14px }
.tile .l { font-size: 12px; opacity: .6 }
.tile .v { font-size: 20px; font-weight: 600; letter-spacing: -.01em; margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis }
.tile .s { font-size: 12px; opacity: .7; margin-top: 1px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis }
.x-up { color: #1f9d55 !important; opacity: 1 !important } .x-down { color: #e5484d !important; opacity: 1 !important }
.x-warn { color: #d97706 !important; opacity: 1 !important }
.vcard { background: rgba(128,128,128,.08); border-radius: 16px; padding: 16px 18px; min-height: 214px; border: 2px solid transparent }
.vcard.active { border-color: #0071e3 }
.vcard .p { font-size: 13px; opacity: .6; font-weight: 500 }
.vcard .h { font-size: 22px; font-weight: 700; margin: 4px 0 10px; letter-spacing: -.01em }
.vcard ul { margin: 0; padding-left: 16px; font-size: 13px; line-height: 1.6; opacity: .8 }
.GO { color: #1f9d55 } .SELECT { color: #d97706 } .WAIT { color: #e5484d }
.pill { display: inline-block; font-size: 12px; padding: 3px 10px; border-radius: 980px; background: rgba(128,128,128,.13); margin: 0 6px 6px 0 }
.score .big { font-size: 44px; font-weight: 700; letter-spacing: -.02em; line-height: 1.1 }
.score .big small { font-size: 16px; opacity: .5; font-weight: 500 }
.bar { height: 6px; border-radius: 3px; background: rgba(128,128,128,.18); margin: 10px 0 12px; overflow: hidden }
.bar i { display: block; height: 100%; border-radius: 3px }
.kv { display: flex; justify-content: space-between; font-size: 13px; padding: 6px 0; border-top: 1px solid rgba(128,128,128,.15) }
.note { font-size: 12px; opacity: .65; margin-top: 10px; line-height: 1.6 }
.side { font-size: 13px; line-height: 1.6 } .side b { font-size: 15px } .side span { opacity: .65 }
</style>""", unsafe_allow_html=True)


def tone(x) -> str:
    return "x-up" if ok(x) and x > 0 else "x-down" if ok(x) and x < 0 else ""


def tiles(items: list[tuple]):
    """숫자 타일 한 줄: (라벨, 값, 보조문구, 색 클래스). 항목 수만큼 칸을 나눠 줄바꿈 없이."""
    html = "".join(f"<div class='tile'><div class='l'>{l}</div><div class='v'>{v}</div>"
                   f"<div class='s {c}'>{s or '&nbsp;'}</div></div>" for l, v, s, c in items)
    n = min(len(items), 8)
    st.markdown(f"<div class='tiles' style='grid-template-columns:repeat({n},minmax(0,1fr))'>{html}</div>".replace("$", "&#36;"),
                unsafe_allow_html=True)  # $ 두 개면 수식으로 렌더되는 것 방지


def pills(items: list[str]):
    if items:
        st.markdown("".join(f"<span class='pill'>{t}</span>" for t in items), unsafe_allow_html=True)


# ───────────────────────── helpers ─────────────────────────
def now_ny() -> dt.datetime:
    return dt.datetime.now(NY)


def mins(idx) -> np.ndarray:
    return np.asarray(idx.hour * 60 + idx.minute)


def ams(m: int, day: dt.date | None = None) -> str:
    day = day or now_ny().date()
    t = dt.datetime.combine(day, dt.time(m // 60 % 24, m % 60), NY).astimezone(AMS)
    return f"{t:%H:%M}"


def hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def ok(x) -> bool:
    return x is not None and not (isinstance(x, float) and np.isnan(x))


def pct(x, d=2) -> str:
    return f"{x * 100:+.{d}f}%" if ok(x) else "-"


def num(x, d=2) -> str:
    return f"{x:,.{d}f}" if ok(x) else "-"


def usd(x: float) -> str:
    """부호가 맨 앞에 오는 달러 표기 (st.metric 델타 색이 부호를 읽음)."""
    return f"{'-' if x < 0 else '+'}\\${abs(x):,.0f}"


def fmt_big(x) -> str:
    if not ok(x):
        return "-"
    for u, k in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(x) >= k:
            return f"${x / k:,.1f}{u}"
    return f"${x:,.0f}"


def market_state(now: dt.datetime) -> str:
    if now.weekday() >= 5:
        return "휴장 (주말)"
    m = now.hour * 60 + now.minute
    if 240 <= m < 570:
        return "프리마켓"
    if 570 <= m < 960:
        return "정규장"
    if 960 <= m < 1200:
        return "애프터마켓"
    return "장 마감"


def next_open(now: dt.datetime) -> dt.datetime:
    d = now.date()
    t = dt.datetime.combine(d, dt.time(9, 30), NY)
    if now >= t:
        d += dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return dt.datetime.combine(d, dt.time(9, 30), NY)


def phase_of(m: int) -> tuple | None:
    return next((p for p in PHASES if p[0] <= m < p[1]), None)


def rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["Close"].shift()
    return pd.concat([df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()], axis=1).max(axis=1)


def vwap_bands(S: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    tp = (S["High"] + S["Low"] + S["Close"]) / 3
    v = S["Volume"].clip(lower=0)
    if v.sum() == 0:
        v = pd.Series(1.0, index=S.index)
    cv = v.cumsum().replace(0, np.nan)
    vw = (tp * v).cumsum() / cv
    var = (tp * tp * v).cumsum() / cv - vw ** 2
    return vw.ffill().bfill(), np.sqrt(var.clip(lower=0)).ffill().fillna(0)


def daily_vwap(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """정규장 바만으로 매일 09:30에 리셋되는 VWAP과 표준편차."""
    out_v, out_s = [], []
    for _, g in df.groupby(df.index.date):
        v, s = vwap_bands(g)
        out_v.append(v)
        out_s.append(s)
    return pd.concat(out_v), pd.concat(out_s)


def _pick(d: pd.DataFrame, s: str) -> pd.DataFrame | None:
    if d is None or d.empty:
        return None
    if isinstance(d.columns, pd.MultiIndex):
        if s not in d.columns.get_level_values(0):
            return None
        df = d[s]
    else:
        df = d
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
    df = df[df["Close"] > 0]
    return df if len(df) else None


# ───────────────────────── data ─────────────────────────
# Alpaca 키가 있으면 미국 주식 분봉을 Alpaca에서(무료: IEX 실시간), 없으면 Yahoo(지연 가능)에서 가져옴
ALPACA = (os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY"))
ALPACA_FEED = os.environ.get("ALPACA_FEED", "iex")
PERIOD_DAYS = {"1d": 3, "5d": 8, "7d": 10, "1mo": 31, "60d": 88}


def data_source() -> str:
    return f"Alpaca ({ALPACA_FEED.upper()} 실시간)" if all(ALPACA) else "Yahoo Finance (지연 가능)"


def _clean(df: pd.DataFrame, s: str) -> pd.DataFrame:
    idx = df.index
    df.index = idx.tz_convert(NY) if idx.tz is not None else idx.tz_localize("UTC").tz_convert(NY)
    df = df.fillna({"Volume": 0})
    m = mins(df.index)
    ext = (m < 570) | (m >= 960)
    if ext.any() and s != "BTC-USD":  # 장외 시간 튀는 체결(꼬리) 정리
        body_hi, body_lo = df[["Open", "Close"]].max(axis=1), df[["Open", "Close"]].min(axis=1)
        df.loc[ext, "High"] = np.minimum(df["High"], body_hi * 1.01)[ext]
        df.loc[ext, "Low"] = np.maximum(df["Low"], body_lo * 0.99)[ext]
    return df


def _alpaca_parse(payload: dict) -> dict:
    out = {}
    for s, rows in (payload.get("bars") or {}).items():
        if rows:
            df = pd.DataFrame(rows).rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
            df.index = pd.to_datetime(df["t"], utc=True)
            out[s] = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    return out


def _alpaca_bars(tickers: list, interval: str, period: str) -> dict:
    tf = {"1m": "1Min", "5m": "5Min", "15m": "15Min", "1h": "1Hour"}[interval]
    start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=PERIOD_DAYS.get(period, 31))).strftime("%Y-%m-%dT%H:%M:%SZ")
    hdr = {"APCA-API-KEY-ID": ALPACA[0], "APCA-API-SECRET-KEY": ALPACA[1]}
    out, token = {}, None
    for _ in range(200):
        q = {"symbols": ",".join(tickers), "timeframe": tf, "start": start, "limit": 10000, "feed": ALPACA_FEED,
             "adjustment": "split", "sort": "asc"}
        if token:
            q["page_token"] = token
        r = requests.get("https://data.alpaca.markets/v2/stocks/bars", params=q, headers=hdr, timeout=20)
        r.raise_for_status()
        js = r.json()
        for sym, df in _alpaca_parse(js).items():
            out[sym] = pd.concat([out[sym], df]) if sym in out else df
        token = js.get("next_page_token")
        if not token:
            break
    return out


def _bars_raw(tickers: tuple, interval: str, period: str) -> dict:
    out, rest = {}, list(tickers)
    if all(ALPACA):
        stocks = [t for t in tickers if not t.startswith("^") and "-" not in t]
        try:
            got = _alpaca_bars(stocks, interval, period)
            out = {s: _clean(df, s) for s, df in got.items() if len(df)}
        except Exception:
            out = {}
        rest = [t for t in tickers if t not in out]
    if rest:
        d = yf.download(rest, period=period, interval=interval, prepost=True, progress=False,
                        group_by="ticker", auto_adjust=True, threads=True)
        for s in rest:
            df = _pick(d, s)
            if df is not None:
                out[s] = _clean(df, s)
    return out


@st.cache_data(ttl=60, show_spinner=False)
def bars(tickers: tuple, interval: str, period: str) -> dict:
    return _bars_raw(tickers, interval, period)


@st.cache_data(ttl=900, show_spinner=False)
def daily(tickers: tuple) -> dict:
    d = yf.download(list(tickers), period="1y", interval="1d", progress=False, group_by="ticker",
                    auto_adjust=True, threads=True)
    out = {}
    for s in tickers:
        df = _pick(d, s)
        if df is not None:
            df.index = pd.DatetimeIndex([pd.Timestamp(x).date() for x in df.index])
            out[s] = df
    return out


@st.cache_data(ttl=3 * 3600, show_spinner=False)
def earnings_map() -> dict:
    try:
        r = requests.get(EARN_URL, timeout=8).json()
    except Exception:
        return {}
    return {w["sym"]: (w["next"], w.get("timing")) for w in r.get("watch", []) if w.get("next")}


@st.cache_data(ttl=3600, show_spinner=False)
def info_of(sym: str) -> dict:
    try:
        return yf.Ticker(sym).info or {}
    except Exception:
        return {}


# ───────────────────────── per-ticker analysis ─────────────────────────
def analyze(df, dly, spy_df, spy_chg, or_min=15, earn=None) -> dict | None:
    """5분봉(또는 1분봉) + 일봉으로 단타 레벨·지표·셋업을 계산."""
    if df is None or len(df) < 30:
        return None
    idx = df.index
    m, d = mins(idx), np.array(idx.date)
    reg, pre = (m >= 570) & (m < 960), (m >= 240) & (m < 570)
    rdates = sorted(set(d[reg]))
    if not rdates:
        return None
    if d[-1] == rdates[-1]:
        cur, prevd, mode = d[-1], (rdates[-2] if len(rdates) > 1 else None), ("reg" if reg[-1] else "post")
    else:
        cur, prevd, mode = d[-1], rdates[-1], "pre"
    R = df[reg]
    S = df[reg & (d == cur)]
    P = df[reg & (d == prevd)] if prevd else df.iloc[:0]
    PM = df[pre & (d == cur)]
    bar = max(1, int(round(np.median(np.diff(idx.asi8[-60:])) / 6e10)))

    last = float(df["Close"].iloc[-1])
    pdc = float(P["Close"].iloc[-1]) if len(P) else np.nan
    if dly is not None and prevd is not None and pd.Timestamp(prevd) in dly.index:
        pdc = float(dly.loc[pd.Timestamp(prevd), "Close"])
    px = float(S["Close"].iloc[-1]) if mode == "post" and len(S) else last  # 장 마감 후엔 정규장 종가 기준
    a = dict(mode=mode, cur=cur, bar=bar, last=last, px=px, last_t=idx[-1], pdc=pdc,
             pdh=float(P["High"].max()) if len(P) else np.nan, pdl=float(P["Low"].min()) if len(P) else np.nan,
             pmh=float(PM["High"].max()) if len(PM) else np.nan, pml=float(PM["Low"].min()) if len(PM) else np.nan,
             pmv=float(PM["Volume"].sum()))
    a["open"] = float(S["Open"].iloc[0]) if len(S) else np.nan
    a["gap"] = (a["open"] if len(S) else last) / pdc - 1 if ok(pdc) else np.nan
    a["chg"] = last / pdc - 1 if ok(pdc) else np.nan
    a["hod"] = float(S["High"].max()) if len(S) else np.nan
    a["lod"] = float(S["Low"].min()) if len(S) else np.nan

    # 시간대 동일 상대거래량 (RVOL)
    mask, upto = (reg, int(m[reg & (d == cur)][-1])) if len(S) else (pre, int(m[-1]))
    sel = mask & (m <= upto)
    cum = pd.Series(df["Volume"].values[sel], index=d[sel]).groupby(level=0).sum()
    hist = cum[cum.index != cur].tail(20)
    hist = hist[hist > 0]
    a["rvol"] = float(cum.get(cur, 0.0) / hist.mean()) if len(hist) >= 3 else np.nan

    # 바 기준 지표 (정규장 연속 시계열)
    c = R["Close"]
    ema9, ema20 = c.ewm(span=9, adjust=False).mean(), c.ewm(span=20, adjust=False).mean()
    rs14 = rsi(c)
    tr = true_range(R)
    atrb = tr.rolling(14).mean()
    sma20, sd20 = c.rolling(20).mean(), c.rolling(20).std()
    sq_on = (2 * sd20) < (1.5 * tr.rolling(20).mean())
    mom = c - ((R["High"].rolling(20).max() + R["Low"].rolling(20).min()) / 2 + sma20) / 2
    a.update(ema9=float(ema9.iloc[-1]), ema20=float(ema20.iloc[-1]), rsi=float(rs14.iloc[-1]),
             atrb=float(atrb.iloc[-1]), sq_on=bool(sq_on.iloc[-1]),
             sq_fired=bool(sq_on.iloc[-3:-1].any() and not sq_on.iloc[-1]), mom=float(mom.iloc[-1]))

    # 일봉 통계
    a.update(atr=np.nan, dvol=np.nan, nr7=False, inside=False, sma20d=np.nan, sma50d=np.nan)
    if dly is not None and len(dly) > 25:
        dd = dly[dly.index < pd.Timestamp(cur)]
        if len(dd) > 20:
            a["atr"] = float(true_range(dd).rolling(14).mean().iloc[-1])
            a["dvol"] = float((dd["Close"] * dd["Volume"]).tail(20).mean())
            rng = dd["High"] - dd["Low"]
            a["nr7"] = bool(rng.iloc[-1] <= rng.tail(7).min())
            a["inside"] = bool(dd["High"].iloc[-1] < dd["High"].iloc[-2] and dd["Low"].iloc[-1] > dd["Low"].iloc[-2])
        a["sma20d"] = float(dly["Close"].tail(20).mean())
        a["sma50d"] = float(dly["Close"].tail(50).mean())
    a["atr_pct"] = a["atr"] / last if ok(a["atr"]) else np.nan
    a["atr_used"] = (a["hod"] - a["lod"]) / a["atr"] if ok(a["atr"]) and len(S) else np.nan

    # 베타·상대강도 (SPY 대비)
    a["beta"], a["rs"] = 1.0, np.nan
    if spy_df is not None:
        sm = mins(spy_df.index)
        sr = spy_df[(sm >= 570) & (sm < 960)]["Close"].pct_change()
        j = pd.concat([c.pct_change(), sr], axis=1, join="inner").dropna().tail(78 * 20)
        if len(j) > 100 and j.iloc[:, 1].var() > 0:
            a["beta"] = float(np.clip(j.cov().iloc[0, 1] / j.iloc[:, 1].var(), -1, 4))
        if ok(spy_chg) and ok(a["chg"]):
            a["rs"] = a["chg"] - a["beta"] * spy_chg

    # 세션 VWAP · 오프닝 레인지 · 거래량 델타(추정)
    a.update(vwap=np.nan, vsd=np.nan, vwz=np.nan, orh=np.nan, orl=np.nan, or_done=False, delta=np.nan)
    if len(S):
        vw, sd = vwap_bands(S)
        a["vwap"], a["vsd"] = float(vw.iloc[-1]), float(sd.iloc[-1])
        a["vwz"] = (S["Close"].iloc[-1] - a["vwap"]) / a["vsd"] if a["vsd"] > 0 else 0.0
        sm_ = mins(S.index)
        orb = S[sm_ < 570 + max(or_min, bar)]
        a["orh"], a["orl"] = float(orb["High"].max()), float(orb["Low"].min())
        a["or_done"] = bool(sm_[-1] >= 570 + max(or_min, bar))
        hl = (S["High"] - S["Low"]).replace(0, np.nan)
        clv = (((S["Close"] - S["Low"]) - (S["High"] - S["Close"])) / hl).fillna(0)
        a["delta"] = float((clv * S["Volume"]).sum() / max(S["Volume"].sum(), 1))
        a["_S"] = (S, vw, sd, ema9.reindex(S.index), ema20.reindex(S.index), rs14.reindex(S.index),
                   sq_on.reindex(S.index), mom.reindex(S.index))
    ref = a["vwap"] if ok(a["vwap"]) else pdc
    if ok(ref) and px > ref and a["ema9"] > a["ema20"]:
        a["bias"] = "롱"
    elif ok(ref) and px < ref and a["ema9"] < a["ema20"]:
        a["bias"] = "숏"
    else:
        a["bias"] = "중립"

    a["earn_d"], a["earn_t"] = None, None
    if earn:
        try:
            a["earn_d"] = (dt.date.fromisoformat(earn[0]) - now_ny().date()).days
            a["earn_t"] = earn[1]
        except Exception:
            pass
    a["setups"] = find_setups(a) if len(S) >= 3 else []
    a["tags"] = make_tags(a)
    a.pop("_S", None)
    return a


def find_setups(a: dict) -> list[dict]:
    S, vw, sd, e9, e20, r14, sq, mom = a["_S"]
    C, H, L, V = (S[k].to_numpy() for k in ("Close", "High", "Low", "Volume"))
    vw, sd, e9, e20 = vw.to_numpy(), sd.to_numpy(), e9.to_numpy(), e20.to_numpy()
    last, atrb = C[-1], a["atrb"] if ok(a["atrb"]) and a["atrb"] > 0 else C[-1] * 0.002
    k = max(2, round(15 / a["bar"]))
    out = []

    def add(key, name, side, entry, stop, t1, t2, src, note=""):
        sg = 1 if side == "롱" else -1
        stop = min(stop, entry - 0.3 * atrb) if sg > 0 else max(stop, entry + 0.3 * atrb)
        risk = (entry - stop) * sg
        if not np.isfinite(risk) or risk <= 0:
            return
        if not ok(t1) or (t1 - entry) * sg <= 0:
            t1 = entry + sg * 1.5 * risk
        if not ok(t2) or (t2 - t1) * sg <= 0:
            t2 = t1 + sg * risk
        out.append(dict(key=key, name=name, side=side, entry=entry, stop=stop, t1=t1, t2=t2,
                        rr=(t1 - entry) * sg / risk, src=src, note=note))

    def crossed(level, up, n=k):
        seg = C[-(n + 1):]
        if len(seg) < 2 or not ok(level):
            return False
        return bool(((seg[1:] > level) & (seg[:-1] <= level)).any()) if up else bool(((seg[1:] < level) & (seg[:-1] >= level)).any())

    # 1) 오프닝 레인지 돌파 — Toby Crabel
    if a["or_done"]:
        orh, orl = a["orh"], a["orl"]
        rng, mid = orh - orl, (orh + orl) / 2
        for side, cond, lvl, up in (("롱", last > orh and last > vw[-1], orh, True), ("숏", last < orl and last < vw[-1], orl, False)):
            if cond:
                fresh = crossed(lvl, up, 2 * k)
                sg = 1 if up else -1
                add("ORB", f"ORB {'상단 돌파' if up else '하단 이탈'}", side, last, mid, lvl + sg * rng, lvl + sg * 2 * rng,
                    "Toby Crabel — Opening Range Breakout",
                    "막 돌파 — RVOL 확인 후 진입" if fresh else "돌파 후 시간 경과 — 추격 금지, 레벨 리테스트 대기")

    # 2) VWAP 리클레임 / 이탈 — 기관 집행 벤치마크
    if len(C) > k + 1:
        prior = C[-(k + 1):-1]
        if last > vw[-1] and (prior < vw[-(k + 1):-1]).any() and V[-1] >= V.mean() * 0.8:
            add("VWAP", "VWAP 리클레임", "롱", last, L[-(k + 1):].min(), vw[-1] + sd[-1], vw[-1] + 2 * sd[-1],
                "VWAP — GS·MS 등 기관 집행 알고리즘의 기준가", "VWAP 위 안착 확인. 다시 VWAP 아래로 마감하면 무효")
        if last < vw[-1] and (prior > vw[-(k + 1):-1]).any() and V[-1] >= V.mean() * 0.8:
            add("VWAP", "VWAP 이탈", "숏", last, H[-(k + 1):].max(), vw[-1] - sd[-1], vw[-1] - 2 * sd[-1],
                "VWAP — GS·MS 등 기관 집행 알고리즘의 기준가", "VWAP 아래 안착. 되돌려 위로 마감하면 무효")

    # 3) 9EMA 눌림목 — Qullamaggie·Minervini 추세추종의 장중 버전
    if len(C) > 4:
        if last > vw[-1] and e9[-1] > e20[-1] and e20[-1] > e20[-4] and L[-1] <= e9[-1] * 1.002 and last > e9[-1]:
            add("PULL", "9EMA 눌림목 매수", "롱", last, min(L[-1], e20[-1]) - 0.1 * atrb, a["hod"], np.nan,
                "Qullamaggie·Minervini — 추세 속 첫 눌림", "고점(HOD) 재돌파가 1차 목표")
        if last < vw[-1] and e9[-1] < e20[-1] and e20[-1] < e20[-4] and H[-1] >= e9[-1] * 0.998 and last < e9[-1]:
            add("PULL", "9EMA 되돌림 매도", "숏", last, max(H[-1], e20[-1]) + 0.1 * atrb, a["lod"], np.nan,
                "Qullamaggie·Minervini — 추세 속 첫 되돌림", "저점(LOD) 재이탈이 1차 목표")

    t_open = int(mins(S.index)[-1]) - 570
    gap, rv = a["gap"], a["rvol"]
    # 4) 갭 앤 고 — Ross Cameron
    if ok(gap) and ok(rv) and abs(gap) >= 0.03 and rv >= 2 and t_open <= 90:
        if gap > 0 and last > vw[-1] and (not ok(a["pmh"]) or last >= a["pmh"] * 0.995):
            add("GAPGO", "갭 상승 지속 (Gap & Go)", "롱", last, vw[-1] - 0.1 * atrb, np.nan, np.nan,
                "Ross Cameron — 갭 + 상대거래량 모멘텀", "프리마켓 고점 위 유지가 조건")
        if gap < 0 and last < vw[-1] and (not ok(a["pml"]) or last <= a["pml"] * 1.005):
            add("GAPGO", "갭 하락 지속 (Gap & Go)", "숏", last, vw[-1] + 0.1 * atrb, np.nan, np.nan,
                "Ross Cameron — 갭 + 상대거래량 모멘텀", "프리마켓 저점 아래 유지가 조건")
    # 5) 갭 메우기
    if ok(gap) and abs(gap) >= 0.015 and ok(a["pdc"]):
        o, pdc = a["open"], a["pdc"]
        if gap > 0 and last < o and last < vw[-1]:
            add("GAPFILL", "갭 메우기 (하락)", "숏", last, max(vw[-1], H[-k:].max()) + 0.1 * atrb, (o + pdc) / 2, pdc,
                "갭 필 통계 — 시가·VWAP 아래로 밀리면 전일 종가 회귀", "전일 종가 도달 시 전량 청산")
        if gap < 0 and last > o and last > vw[-1]:
            add("GAPFILL", "갭 메우기 (상승)", "롱", last, min(vw[-1], L[-k:].min()) - 0.1 * atrb, (o + pdc) / 2, pdc,
                "갭 필 통계 — 시가·VWAP 위로 회복하면 전일 종가 회귀", "전일 종가 도달 시 전량 청산")

    # 6) VWAP 2σ 평균회귀 — Larry Connors / 마켓메이커식 페이드
    if sd[-1] > 0 and ok(a["rsi"]):
        if last > vw[-1] + 2 * sd[-1] and a["rsi"] >= 70:
            add("FADE", "VWAP +2σ 과열 페이드", "숏", last, H.max() + 0.25 * atrb, vw[-1] + sd[-1], vw[-1],
                "Larry Connors 평균회귀 · 마켓메이커 페이드", "역추세 — 반전 캔들 확인 후, 감마 플러스 환경에서 신뢰도↑")
        if last < vw[-1] - 2 * sd[-1] and a["rsi"] <= 30:
            add("FADE", "VWAP −2σ 과매도 반등", "롱", last, L.min() - 0.25 * atrb, vw[-1] - sd[-1], vw[-1],
                "Larry Connors 평균회귀 · 마켓메이커 페이드", "역추세 — 반전 캔들 확인 후, 감마 플러스 환경에서 신뢰도↑")

    # 7) 스퀴즈 발사 — John Carter TTM Squeeze
    if a["sq_fired"] and ok(a["mom"]):
        mid = float(S["Close"].rolling(20, min_periods=5).mean().iloc[-1])
        side = "롱" if a["mom"] > 0 else "숏"
        add("SQZ", f"스퀴즈 발사 ({'상방' if side == '롱' else '하방'})", side, last,
            mid - 0.2 * atrb if side == "롱" else mid + 0.2 * atrb, np.nan, np.nan,
            "John Carter — TTM Squeeze (볼린저가 켈트너 안→밖)", "압축 후 확장 초기. 모멘텀 꺾이면 청산")

    # 8) 전일 고점/저점 돌파 — Linda Raschke 핵심 레벨
    if crossed(a["pdh"], True) and last > vw[-1]:
        add("PDL", "전일 고점 돌파", "롱", last, a["pdh"] - 0.5 * atrb, np.nan, np.nan,
            "Linda Raschke — 전일 고저 핵심 레벨", "돌파 후 전일 고점이 지지로 바뀌는지 확인")
    if crossed(a["pdl"], False) and last < vw[-1]:
        add("PDL", "전일 저점 이탈", "숏", last, a["pdl"] + 0.5 * atrb, np.nan, np.nan,
            "Linda Raschke — 전일 고저 핵심 레벨", "이탈 후 전일 저점이 저항으로 바뀌는지 확인")
    return out


def make_tags(a: dict) -> list[str]:
    t = []
    if ok(a["rvol"]) and a["rvol"] >= 2:
        t.append(f"고RVOL {a['rvol']:.1f}x")
    if ok(a["rs"]) and a["rs"] >= 0.015:
        t.append("상대강도 리더")
    if ok(a["rs"]) and a["rs"] <= -0.015:
        t.append("상대강도 약세")
    if a["sq_on"]:
        t.append("스퀴즈 압축 중")
    if a["nr7"]:
        t.append("NR7 (확장 대기)")
    if a["inside"]:
        t.append("인사이드 데이")
    if ok(a["atr_used"]) and a["atr_used"] >= 1:
        t.append(f"ATR 소진 {a['atr_used'] * 100:.0f}%")
    if a["earn_d"] is not None and 0 <= a["earn_d"] <= 3:
        t.append(f"어닝 D-{a['earn_d']}" + (" 장후" if a["earn_t"] == "amc" else " 장전" if a["earn_t"] == "bmo" else ""))
    return t


# ───────────────────────── market scan ─────────────────────────
@st.cache_data(ttl=60, show_spinner=False)
def scan(or_min: int) -> dict:
    intr = bars(tuple(WATCHLIST + MARKET + VIXES + EXTRA), "5m", "1mo")
    dly = daily(tuple(WATCHLIST + MARKET + VIXES + EXTRA))
    earn = earnings_map()
    spy = analyze(intr.get("SPY"), dly.get("SPY"), None, None, or_min)
    spy_chg = spy["chg"] if spy else None
    snaps = {"SPY": spy} if spy else {}
    for s in WATCHLIST + MARKET[1:] + EXTRA:
        try:
            r = analyze(intr.get(s), dly.get(s), intr.get("SPY"), spy_chg, or_min, earn.get(s))
        except Exception:
            r = None
        if r:
            snaps[s] = r
    vix = {}
    for s in VIXES:
        df = intr.get(s) if s in intr else None
        v = float(df["Close"].iloc[-1]) if df is not None else (float(dly[s]["Close"].iloc[-1]) if s in dly else np.nan)
        prev = dly[s]["Close"] if s in dly else None
        vix[s] = dict(v=v, chg=(v / float(prev.iloc[-2]) - 1) if prev is not None and len(prev) > 2 else np.nan)
    spy_t = intr["SPY"].index[-1] if "SPY" in intr else None
    return dict(snaps=snaps, vix=vix, earn=earn, spy_t=spy_t, tick=synthetic_tick(intr, spy),
                spy_df=intr.get("SPY"), fetched=now_ny())


def synthetic_tick(intr: dict, spy: dict | None) -> pd.Series | None:
    """NYSE $TICK 대용: 관심종목+섹터 ETF 중 직전 5분봉 대비 오른 종목 수 − 내린 종목 수."""
    if not spy:
        return None
    cols = {}
    for s in [x for x in WATCHLIST if x != "BTC-USD"] + list(SECTORS):
        df = intr.get(s)
        if df is None:
            continue
        m = mins(df.index)
        c = df[(np.array(df.index.date) == spy["cur"]) & (m >= 570) & (m < 960)]["Close"]
        if len(c) > 1:
            cols[s] = np.sign(c.diff())
    if not cols:
        return None
    return pd.DataFrame(cols).iloc[1:].sum(axis=1)


@st.cache_data(ttl=3600, show_spinner=False)
def econ_week() -> list[dict]:
    """이번 주 미국 경제지표 (ForexFactory 공개 피드, 영향도 High/Medium)."""
    try:
        js = requests.get(FF_URL, timeout=8, headers={"User-Agent": "Mozilla/5.0"}).json()
    except Exception:
        return []
    out = []
    for x in js:
        if x.get("country") == "USD" and x.get("impact") in ("High", "Medium"):
            try:
                t = dt.datetime.fromisoformat(x["date"]).astimezone(NY)
            except Exception:
                continue
            out.append(dict(t=t, title=x["title"], impact=x["impact"], fc=x.get("forecast", ""), prev=x.get("previous", "")))
    return sorted(out, key=lambda e: e["t"])


@st.cache_data(ttl=300, show_spinner=False)
def movers(kind: str) -> pd.DataFrame:
    try:
        q = yf.screen(kind, count=100).get("quotes", [])
    except Exception:
        return pd.DataFrame()
    rows = [dict(티커=x["symbol"], 회사=x.get("shortName") or x.get("longName") or "", 현재가=x.get("regularMarketPrice"),
                 등락=x.get("regularMarketChangePercent"), 프리마켓=x.get("preMarketChangePercent"),
                 애프터=x.get("postMarketChangePercent"), 거래량=x.get("regularMarketVolume"),
                 일RVOL=(x.get("regularMarketVolume") or 0) / x["averageDailyVolume3Month"] if x.get("averageDailyVolume3Month") else None,
                 시가총액=x.get("marketCap"), 관심=x["symbol"] in CAT_OF) for x in q]
    return pd.DataFrame(rows)


def above_ref(a: dict) -> bool:
    return a["px"] > a["vwap"] if ok(a["vwap"]) and a["mode"] != "pre" else (ok(a["chg"]) and a["chg"] > 0)


def regime(sc: dict) -> dict:
    sn, vx = sc["snaps"], sc["vix"]
    spy, qqq, iwm, smh = (sn.get(s) for s in MARKET)
    comp, notes = {}, []
    t = 0.0
    for name, s, w in (("SPY", spy, 10), ("QQQ", qqq, 10)):
        if s:
            if above_ref(s):
                t += w
            notes.append(f"{name} {'VWAP 위' if above_ref(s) else 'VWAP 아래'} ({pct(s['chg'])})")
    if spy:
        t += 5 if spy["ema9"] > spy["ema20"] else 0
        t += 7.5 if ok(spy["sma20d"]) and spy["last"] > spy["sma20d"] else 0
        t += 7.5 if ok(spy["sma50d"]) and spy["last"] > spy["sma50d"] else 0
        notes.append(f"SPY 일봉 20MA {'위' if spy['last'] > spy['sma20d'] else '아래'}, 50MA {'위' if spy['last'] > spy['sma50d'] else '아래'}")
    comp["추세"] = (t, 40)

    v, v9, v3 = (vx.get(s, {}).get("v", np.nan) for s in VIXES)
    vs = 25 if v < 15 else 20 if v < 20 else 12 if v < 25 else 5 if v < 30 else 0
    back9, back3 = ok(v9) and v9 > v, ok(v3) and v > v3
    vs -= 8 * back9 + 8 * back3
    comp["변동성"] = (max(vs, 0), 25)
    if ok(v):
        notes.append(f"VIX {v:.1f}" + (" · 단기 역전(VIX9D>VIX)" if back9 else "") + (" · 기간구조 역전(VIX>VIX3M)" if back3 else ""))

    w = [sn[s] for s in WATCHLIST if s in sn and s != "BTC-USD"]
    br_w = np.mean([above_ref(a) for a in w]) if w else 0.5
    secs = [sn[s] for s in SECTORS if s in sn]
    br_s = np.mean([above_ref(a) for a in secs]) if secs else br_w
    br = (br_w + br_s) / 2
    comp["시장 폭"] = (20 * br, 20)
    notes.append(f"관심종목 {br_w * 100:.0f}% · 섹터 {sum(above_ref(a) for a in secs)}/{len(secs)}개가 VWAP 위")
    rsp = sn.get("RSP")
    eq_gap = (rsp["chg"] - spy["chg"]) if rsp and spy and ok(rsp["chg"]) and ok(spy["chg"]) else np.nan
    if ok(eq_gap):
        notes.append(f"동일가중−시총가중 {eq_gap * 100:+.2f}%p ({'고른 상승 참여' if eq_gap > 0 else '대형주 쏠림'})")

    p = sum(7.5 for s in (iwm, smh) if s and above_ref(s))
    comp["참여도"] = (p, 15)
    score = sum(x for x, _ in comp.values())
    bias = "롱 우위" if score >= 60 else "숏 우위" if score <= 35 else "혼조"
    return dict(score=score, comp=comp, notes=notes, bias=bias, vix=v, back9=back9, back3=back3, breadth=br,
                br_w=br_w, br_s=br_s, eq_gap=eq_gap)


def verdict(pname: str, rg: dict, now: dt.datetime, shorts: bool, events: list | None = None) -> dict:
    P = PROFILES[pname]
    st_ = market_state(now)
    m = now.hour * 60 + now.minute
    s, why = rg["score"], []
    fomc_today = now.date() in FOMC
    hot = [e for e in (events or []) if e["impact"] == "High" and -15 <= (e["t"] - now).total_seconds() / 60 <= 30]
    if ok(rg["vix"]) and rg["vix"] > P["max_vix"]:
        lvl, head = "WAIT", "관망"
        why.append(f"VIX {rg['vix']:.1f} > 프로필 한도 {P['max_vix']}")
    elif P["no_backwardation"] and (rg["back9"] or rg["back3"]):
        lvl, head = "WAIT", "관망"
        why.append("VIX 기간구조 역전 — 방어형은 스트레스 구간에서 쉼")
    elif fomc_today and pname == "방어형":
        lvl, head = "WAIT", "관망"
        why.append("오늘 FOMC 발표 (14:00 ET) — 방어형은 이벤트일 휴식")
    elif hot and pname != "공격형":
        lvl, head = "WAIT", "관망 (지표 발표)"
        why.append(" · ".join(f"{e['t']:%H:%M} ET {e['title']}" for e in hot) + " 전후 30분 — 발표 직후 방향 확인 전까지 쉼")
    else:
        long_ok, strong = s >= P["min_mkt"], s >= P["min_mkt"] + 15
        short_ok = shorts and s <= P["short_max"]
        if strong:
            lvl, head = "GO", "롱 진입 가능"
        elif long_ok and short_ok:
            lvl, head = "SELECT", "양방향 — 셋업 따라 선별"
        elif long_ok:
            lvl, head = "SELECT", "선별 진입 (A+ 셋업만)"
        elif short_ok:
            lvl, head = "GO" if s <= P["short_max"] - 15 else "SELECT", "숏 우위"
        else:
            lvl, head = "WAIT", "관망"
        why.append(f"시장 점수 {s:.0f} (롱 기준 ≥{P['min_mkt']}" + (f", 숏 기준 ≤{P['short_max']})" if shorts else ")"))
        if fomc_today:
            why.append("오늘 FOMC — 13:45~15:00 ET 신규 진입 자제")
    if st_ == "정규장":
        if not any(a <= m < b for a, b in P["windows"]):
            nxt = next((a for a, _ in P["windows"] if a > m), None)
            why.append("지금은 이 프로필 시간대 밖" + (f" — 다음 구간 {hm(nxt)} ET ({ams(nxt)} 암스테르담)" if nxt else " — 오늘은 종료"))
            if lvl != "WAIT":
                lvl = "WAIT"
                head = f"대기 (시간대) · {head}"
    else:
        why.insert(0, f"{st_} — 직전 정규장 기준의 다음 세션 전망")
    why.append(f"거래당 리스크 {P['risk']}% · 하루 최대 {P['max_trades']}회 · 일 손실 한도 {P['daily_loss']}%")
    return dict(lvl=lvl, head=head, why=why)


def profile_score(a: dict, pname: str, mkt_bias: str, shorts: bool) -> tuple[float, list[dict]]:
    P = PROFILES[pname]
    fit = [x for x in a["setups"] if x["key"] in PROFILE_SETUPS[pname] and (shorts or x["side"] == "롱")]
    if P["align"] and mkt_bias != "혼조":
        fit = [x for x in fit if (x["side"] == "롱") == (mkt_bias == "롱 우위")]
    if pname == "방어형":
        fit = [x for x in fit if x["side"] == "롱" or shorts]
    rv = a["rvol"] if ok(a["rvol"]) else 1.0
    atrp = a["atr_pct"] if ok(a["atr_pct"]) else 0.03
    dv = a["dvol"] if ok(a["dvol"]) else 0
    rs = abs(a["rs"]) if ok(a["rs"]) else 0
    align = (a["bias"] == "롱" and mkt_bias == "롱 우위") or (a["bias"] == "숏" and mkt_bias == "숏 우위")
    earn_soon = a["earn_d"] is not None and 0 <= a["earn_d"] <= 1
    clip = lambda x: float(min(max(x, 0), 1))
    if pname == "공격형":
        sc = 30 * clip(rv / 3) + 20 * clip(abs(a["gap"] if ok(a["gap"]) else 0) / 0.05) + 20 * clip(atrp / 0.06) + 15 * bool(fit) + 15 * clip(rs / 0.03)
    elif pname == "밸런스형":
        sc = 25 * clip(rv / 2) + 20 * align + 20 * bool(fit) + 15 * clip(rs / 0.02) + 10 * (dv >= 1e8) + 10 * (0.02 <= atrp <= 0.06)
        sc -= 20 * earn_soon
    else:
        sc = 25 * (dv >= 5e8) + 20 * align + 20 * bool(fit) + 15 * (atrp <= 0.04) + 10 * (not earn_soon) + 10 * clip(rv / 1.5)
        sc -= 50 * earn_soon
    if dv and dv < P["min_dvol"]:
        sc -= 25
    if atrp > P["max_atr"]:
        sc -= 15
    return max(sc, 0.0), fit


# ───────────────────────── next-bar model ─────────────────────────
FEAT_KO = {"z1": "직전 5분 수익률", "z3": "15분 모멘텀", "z6": "30분 모멘텀", "z12": "1시간 모멘텀",
           "vw": "VWAP 거리(σ)", "rsi": "RSI", "vr": "거래량 급증", "ema": "9/20 EMA 간격",
           "tod": "장중 시각", "tod2": "장중 시각²", "s1": "SPY 직전 5분", "s3": "SPY 15분", "rel": "SPY 대비 상대 모멘텀"}


def _xy(df: pd.DataFrame, spy: pd.DataFrame | None):
    c, o, v = df["Close"], df["Open"], df["Volume"]
    d = pd.Series(df.index.date, index=df.index)
    new = d.ne(d.shift())
    lr = np.log(c).diff().where(~new, np.log(c / o))
    vol = lr.rolling(78, min_periods=20).std()
    z = (lr / vol).clip(-6, 6)
    X = pd.DataFrame(index=df.index)
    X["z1"] = z
    for n in (3, 6, 12):
        X[f"z{n}"] = z.rolling(n).sum() / np.sqrt(n)
    vw, sd = daily_vwap(df)
    X["vw"] = ((c - vw) / np.maximum(sd, c * vol.fillna(0.002))).clip(-4, 4)
    X["rsi"] = (rsi(c) - 50) / 50
    X["vr"] = np.log((v + 1) / (v.rolling(78, min_periods=20).mean() + 1)).clip(-3, 3)
    X["ema"] = ((c.ewm(span=9, adjust=False).mean() - c.ewm(span=20, adjust=False).mean()) / c / vol).clip(-10, 10)
    m = mins(df.index)
    X["tod"] = (m - 570) / 390
    X["tod2"] = X["tod"] ** 2
    if spy is not None:
        sl = np.log(spy["Close"]).diff().reindex(df.index)
        sz = (sl / sl.rolling(78, min_periods=20).std()).clip(-6, 6)
        X["s1"], X["s3"] = sz, sz.rolling(3).sum() / np.sqrt(3)
        X["rel"] = X["z3"] - X["s3"]
    y = (lr.shift(-1) > 0).astype(int)
    has_next = d.shift(-1).eq(d)
    return X, y, has_next, lr, d


@st.cache_data(ttl=60, show_spinner=False)
def next_bar_model(sym: str) -> dict | None:
    data = bars((sym, "SPY"), "5m", "60d")
    df, spy = data.get(sym), data.get("SPY")
    if df is None or len(df) < 500:
        return None
    now = now_ny()
    if df.index[-1] + pd.Timedelta(minutes=5) > now:
        df = df.iloc[:-1]  # 진행 중인 바는 제외
    m = mins(df.index)
    df = df[(m >= 570) & (m < 960)]
    if spy is not None:
        sm = mins(spy.index)
        spy = spy[(sm >= 570) & (sm < 960)]
    X, y, has_next, lr, d = _xy(df, spy)
    good = X.notna().all(axis=1)
    train_mask = good & has_next
    days = sorted(set(d[train_mask]))
    if len(days) < 25:
        return None
    test_days = days[-20:]
    rec = []
    for i in range(0, 20, 5):
        blk = set(test_days[i:i + 5])
        tr = train_mask & (d < min(blk))
        te = train_mask & d.isin(blk)
        sc = StandardScaler().fit(X[tr])
        clf = LogisticRegression(C=0.05, max_iter=500).fit(sc.transform(X[tr]), y[tr])
        p = clf.predict_proba(sc.transform(X[te]))[:, 1]
        rec.append(pd.DataFrame({"p": p, "y": y[te].to_numpy(), "base": y[tr].mean(),
                                 "ret": lr.shift(-1)[te].to_numpy()}, index=X.index[te]))
    oos = pd.concat(rec)
    conf = oos[(oos["p"] - 0.5).abs() >= 0.04]
    sc = StandardScaler().fit(X[train_mask])
    clf = LogisticRegression(C=0.05, max_iter=500).fit(sc.transform(X[train_mask]), y[train_mask])
    coef = pd.Series(clf.coef_[0], index=X.columns).sort_values(key=np.abs, ascending=False)

    last_t = df.index[-1]
    live = market_state(now) == "정규장" and last_t.date() == now.date() and (now - last_t) < pd.Timedelta(minutes=15)
    p_up = float(clf.predict_proba(sc.transform(X[good].iloc[[-1]]))[:, 1][0]) if live and good.iloc[-1] else None

    # 장중 시각별 변동성(U자형) 보정 후 다음 5/15/30분 1σ 범위
    am = pd.Series(lr.abs().to_numpy(), index=m[(m >= 570) & (m < 960)]).groupby(level=0).mean()
    prof = (am / am.mean()).rolling(3, center=True, min_periods=1).mean()
    mm = pd.Series(mins(df.index), index=df.index)
    u = lr / mm.map(prof).fillna(1.0)
    sig_u = float(u.tail(78 * 3).ewm(span=78).std().iloc[-1])
    start = int(mm.iloc[-1]) + 5 if live else 570
    nxt = [min(start + 5 * j, 955) for j in range(6)]
    pf = np.array([prof.get(x, 1.0) for x in nxt])
    cone = {h: sig_u * float(np.sqrt((pf[:n] ** 2).sum())) for h, n in ((5, 1), (15, 3), (30, 6))}
    return dict(p_up=p_up, live=live, last_t=last_t, last=float(df["Close"].iloc[-1]),
                acc=float(((oos["p"] > 0.5) == oos["y"]).mean()),
                base_acc=float(((oos["base"] > 0.5) == oos["y"]).mean()),
                brier=float(((oos["p"] - oos["y"]) ** 2).mean()),
                base_brier=float(((oos["base"] - oos["y"]) ** 2).mean()),
                n=len(oos), conf_acc=float(((conf["p"] > 0.5) == conf["y"]).mean()) if len(conf) else np.nan,
                conf_n=len(conf), coef=coef, cone=cone, recent=oos.tail(12), days=len(days))


# ───────────────────────── options / dealer gamma ─────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def options_view(sym: str, spot: float) -> dict | None:
    try:
        tk = yf.Ticker(sym)
        exps = list(tk.options)[:4]
    except Exception:
        return None
    if not exps:
        return None
    now, rows = now_ny(), []
    for e in exps:
        try:
            oc = tk.option_chain(e)
        except Exception:
            continue
        T = max((dt.datetime.combine(dt.date.fromisoformat(e), dt.time(16), NY) - now).total_seconds() / (365 * 86400), 0.5 / 365)
        for kind, x in (("C", oc.calls), ("P", oc.puts)):
            x = x[["strike", "lastPrice", "bid", "ask", "volume", "openInterest", "impliedVolatility"]].copy()
            rows.append(x.assign(kind=kind, exp=e, T=T))
    if not rows:
        return None
    ch = pd.concat(rows, ignore_index=True).fillna({"volume": 0, "openInterest": 0, "bid": 0, "ask": 0})
    ch["mid"] = np.where((ch["bid"] > 0) & (ch["ask"] > 0), (ch["bid"] + ch["ask"]) / 2, ch["lastPrice"])
    ch["iv"] = ch["impliedVolatility"].where(ch["impliedVolatility"].between(0.03, 4))
    out = dict(exps=exps)

    f = ch[ch["exp"] == exps[0]]
    k = f.loc[(f["strike"] - spot).abs().idxmin(), "strike"]
    atm = f[f["strike"] == k]
    out["front"], out["straddle"] = exps[0], float(atm["mid"].sum())
    out["move"] = out["straddle"] / spot
    ivx = ch[ch["T"] >= 2 / 365]
    ivx = ivx if len(ivx) else ch
    e2 = ivx["exp"].iloc[0]
    g = ivx[ivx["exp"] == e2]
    near = g.iloc[(g["strike"] - spot).abs().argsort()[:4]]
    out["atm_iv"] = float(near["iv"].mean()) if near["iv"].notna().any() else np.nan
    out["iv_exp"] = e2
    out["pc_vol"] = ch.loc[ch.kind == "P", "volume"].sum() / max(ch.loc[ch.kind == "C", "volume"].sum(), 1)
    out["pc_oi"] = ch.loc[ch.kind == "P", "openInterest"].sum() / max(ch.loc[ch.kind == "C", "openInterest"].sum(), 1)

    ks = np.sort(f["strike"].unique())
    cf, pf = f[f.kind == "C"], f[f.kind == "P"]
    pain = [(cf["openInterest"] * np.maximum(K - cf["strike"], 0)).sum() + (pf["openInterest"] * np.maximum(pf["strike"] - K, 0)).sum() for K in ks]
    out["max_pain"] = float(ks[int(np.argmin(pain))]) if len(ks) else np.nan

    # 딜러 감마 노출: 고객이 콜 매수·풋 매수, 딜러가 반대편이라는 표준 가정 (콜 +, 풋 −)
    gx = ch[ch["iv"].notna() & (ch["openInterest"] > 0)].copy()
    if len(gx):
        sgn = np.where(gx["kind"] == "C", 1.0, -1.0)
        K, iv, T, oi = gx["strike"].to_numpy(), gx["iv"].to_numpy(), gx["T"].to_numpy(), gx["openInterest"].to_numpy()

        def gex_at(S):
            d1 = (np.log(S / K) + 0.5 * iv ** 2 * T) / (iv * np.sqrt(T))
            return sgn * norm.pdf(d1) / (S * iv * np.sqrt(T)) * oi * 100 * S * S * 0.01

        gx["gex"] = gex_at(spot)
        by = gx.groupby("strike")["gex"].sum()
        by = by[(by.index > spot * 0.85) & (by.index < spot * 1.15)]
        out["by_strike"] = by
        out["net_gex"] = float(gx["gex"].sum())
        grid = np.linspace(spot * 0.88, spot * 1.12, 97)
        prof = np.array([gex_at(s).sum() for s in grid])
        flips = [(grid[i] + grid[i + 1]) / 2 for i in range(len(grid) - 1) if np.sign(prof[i]) != np.sign(prof[i + 1])]
        out["flip"] = float(min(flips, key=lambda x: abs(x - spot))) if flips else np.nan
        cg = gx[gx.kind == "C"].groupby("strike")["gex"].sum()
        pg = gx[gx.kind == "P"].groupby("strike")["gex"].sum()
        cg, pg = cg[cg.index >= spot * 0.98], pg[pg.index <= spot * 1.02]
        out["call_wall"] = float(cg.idxmax()) if len(cg) else np.nan
        out["put_wall"] = float(pg.idxmin()) if len(pg) else np.nan
    return out


def volume_profile(S: pd.DataFrame, bins: int = 40) -> dict | None:
    if len(S) < 5:
        return None
    lo, hi = float(S["Low"].min()), float(S["High"].max())
    if hi <= lo:
        return None
    edges = np.linspace(lo, hi, bins + 1)
    vol = np.zeros(bins)
    for l, h, v in zip(S["Low"], S["High"], S["Volume"]):
        i0 = int(np.clip(np.searchsorted(edges, l, "right") - 1, 0, bins - 1))
        i1 = int(np.clip(np.searchsorted(edges, h, "right") - 1, 0, bins - 1))
        vol[i0:i1 + 1] += v / (i1 - i0 + 1)
    poc = int(vol.argmax())
    lo_i = hi_i = poc
    tot, acc = vol.sum(), vol[poc]
    while acc < 0.7 * tot and (lo_i > 0 or hi_i < bins - 1):
        up = vol[hi_i + 1] if hi_i < bins - 1 else -1
        dn = vol[lo_i - 1] if lo_i > 0 else -1
        if up >= dn:
            hi_i += 1
            acc += up
        else:
            lo_i -= 1
            acc += dn
    mid = (edges[:-1] + edges[1:]) / 2
    return dict(mid=mid, vol=vol, poc=float(mid[poc]), vah=float(edges[hi_i + 1]), val=float(edges[lo_i]))


# ───────────────────────── trade journal ─────────────────────────
JOURNAL = os.environ.get("TRADING_JOURNAL") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "journal", "trades.csv")
SETUP_NAMES = {"ORB": "ORB 돌파", "VWAP": "VWAP 리클레임/이탈", "PULL": "9EMA 눌림목", "GAPGO": "갭 앤 고",
               "GAPFILL": "갭 메우기", "FADE": "VWAP 2σ 페이드", "SQZ": "스퀴즈 발사", "PDL": "전일 고저 돌파", "OTHER": "기타·즉흥"}
SETUP_KEYS = {v: k for k, v in SETUP_NAMES.items()}
MISTAKES = ["추격 진입", "손절 미준수", "조기 청산", "물타기", "과다 사이즈", "복수 매매", "계획 외 매매", "시간대 위반", "뉴스·이벤트 무시"]
J_COLS = ["id", "date", "sym", "side", "setup", "profile", "entry_time", "exit_time", "entry", "stop", "target", "exit",
          "shares", "fees", "rule_ok", "mistakes", "notes"]
J_NUM = ["entry", "stop", "target", "exit", "shares", "fees"]
J_TXT = ["id", "sym", "side", "setup", "profile", "entry_time", "exit_time", "mistakes", "notes"]
MIN_N = 20  # 셋업 판단에 필요한 최소 표본


def load_journal() -> pd.DataFrame:
    try:
        df = pd.read_csv(JOURNAL, dtype=str, keep_default_na=False)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        df = pd.DataFrame(columns=J_COLS)
    for c in J_COLS:
        if c not in df:
            df[c] = ""
    df = df[J_COLS].copy()
    for c in J_NUM:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df["rule_ok"] = df["rule_ok"].astype(str).str.lower().isin(["true", "1", "yes"])
    return df.dropna(subset=["date"]).reset_index(drop=True)


def save_journal(df: pd.DataFrame):
    os.makedirs(os.path.dirname(JOURNAL), exist_ok=True)
    out = df[J_COLS].copy()
    out["id"] = [x if isinstance(x, str) and x else uuid.uuid4().hex[:8] for x in out["id"]]
    out.to_csv(JOURNAL, index=False)


def enrich(j: pd.DataFrame) -> pd.DataFrame:
    j = j.copy()
    sg = np.where(j["side"] == "숏", -1, 1)
    risk = (j["entry"] - j["stop"]).abs().replace(0, np.nan)
    j["R"] = (j["exit"] - j["entry"]) * sg / risk
    j["plan_R"] = (j["target"] - j["entry"]) * sg / risk
    j["pnl"] = (j["exit"] - j["entry"]) * sg * j["shares"] - j["fees"].fillna(0)
    j["risk_usd"] = risk * j["shares"]
    j["closed"] = j["R"].notna()
    j["setup_name"] = j["setup"].map(SETUP_NAMES).fillna(j["setup"])
    em = pd.to_datetime(j["entry_time"], format="%H:%M", errors="coerce")
    j["phase"] = [(phase_of(int(t.hour * 60 + t.minute)) or (0, 0, "장외"))[2] if pd.notna(t) else "시간 미기록" for t in em]
    j["weekday"] = [("월화수목금토일"[d.weekday()]) for d in j["date"]]
    return j.sort_values(["date", "entry_time"]).reset_index(drop=True)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return np.nan, np.nan
    p = k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return c - h, c + h


def summarize(g: pd.DataFrame) -> dict | None:
    g = g[g["closed"]]
    n = len(g)
    if not n:
        return None
    R = g["R"].to_numpy()
    w = R > 0
    lo, hi = wilson(int(w.sum()), n)
    cum = np.r_[0, np.cumsum(R)]
    gain, loss = R[R > 0].sum(), -R[R < 0].sum()
    streak = run = 0
    for x in R:
        run = run + 1 if x <= 0 else 0
        streak = max(streak, run)
    sd = R.std(ddof=1) if n > 1 else np.nan
    return dict(n=n, win=w.mean(), lo=lo, hi=hi, exp=R.mean(), ci=1.96 * sd / np.sqrt(n) if n > 1 else np.nan,
                avg_win=R[w].mean() if w.any() else np.nan, avg_loss=R[~w].mean() if (~w).any() else np.nan,
                pf=gain / loss if loss > 0 else np.inf, totR=R.sum(), pnl=g["pnl"].sum(),
                mdd=float((np.maximum.accumulate(cum) - cum).max()), streak=streak,
                sqn=R.mean() / sd * np.sqrt(min(n, 100)) if n > 1 and sd > 0 else np.nan)


def judge(s: dict) -> str:
    if s["n"] < MIN_N:
        return f"표본 부족 ({s['n']}/{MIN_N})"
    if ok(s["ci"]) and s["exp"] - s["ci"] > 0:
        return "엣지 확인 · 유지·확대"
    if s["exp"] > 0:
        return "플러스지만 불확실 · 유지"
    return "마이너스 · 중단 검토"


def group_stats(j: pd.DataFrame, col: str, label: str) -> pd.DataFrame:
    rows = []
    for k, g in j.groupby(col, sort=False):
        s = summarize(g)
        if s:
            rows.append({label: k, "거래": s["n"], "승률%": s["win"] * 100,
                         "승률 95% 범위": f"{s['lo'] * 100:.0f}–{s['hi'] * 100:.0f}%",
                         "기대값(R)": s["exp"], "평균 이익(R)": s["avg_win"], "평균 손실(R)": s["avg_loss"],
                         "PF": s["pf"] if np.isfinite(s["pf"]) else None, "누적 R": s["totR"], "손익($)": s["pnl"],
                         "판정": judge(s)})
    return pd.DataFrame(rows).sort_values("누적 R", ascending=False) if rows else pd.DataFrame()


STAT_COLS = {"승률%": st.column_config.NumberColumn(format="%.0f"), "기대값(R)": st.column_config.NumberColumn(format="%+.2f"),
             "평균 이익(R)": st.column_config.NumberColumn(format="%+.2f"), "평균 손실(R)": st.column_config.NumberColumn(format="%+.2f"),
             "PF": st.column_config.NumberColumn(format="%.2f"), "누적 R": st.column_config.NumberColumn(format="%+.1f"),
             "손익($)": st.column_config.NumberColumn(format="$%.0f")}


def today_status(j: pd.DataFrame, acct: float, pname: str) -> dict:
    P = PROFILES[pname]
    t = j[(j["date"] == now_ny().date()) & j["closed"]]
    pnl = float(t["pnl"].sum()) if len(t) else 0.0
    loss_pct = -pnl / acct * 100
    return dict(n=len(t), R=float(t["R"].sum()) if len(t) else 0.0, pnl=pnl, max_trades=P["max_trades"],
                limit=P["daily_loss"], stop=(loss_pct >= P["daily_loss"]) or len(t) >= P["max_trades"],
                loss_pct=loss_pct, streak=int((t["R"].iloc[-3:] <= 0).all()) if len(t) >= 3 else 0)


def excursions(j: pd.DataFrame) -> pd.DataFrame:
    """진입~청산 사이의 최대 유리/불리 이동(MFE/MAE)을 R 단위로 — 손절·목표가 적절했는지 점검."""
    rows = []
    today = now_ny().date()
    for _, r in j[j["closed"]].iterrows():
        if not r["entry_time"] or not r["exit_time"] or (today - r["date"]).days > 58:
            continue
        try:
            t0 = pd.Timestamp(dt.datetime.combine(r["date"], dt.datetime.strptime(r["entry_time"], "%H:%M").time()), tz=NY)
            t1 = pd.Timestamp(dt.datetime.combine(r["date"], dt.datetime.strptime(r["exit_time"], "%H:%M").time()), tz=NY)
        except ValueError:
            continue
        recent = (today - r["date"]).days <= 6
        df = bars((r["sym"],), "1m" if recent else "5m", "7d" if recent else "60d").get(r["sym"])
        if df is None:
            continue
        step = pd.Timedelta(minutes=1 if recent else 5)
        seg = df[(df.index >= t0.floor(step)) & (df.index <= t1)]
        if seg.empty or not (seg["Low"].min() * 0.995 <= r["entry"] <= seg["High"].max() * 1.005):
            continue  # 시각·가격이 실제 체결 범위와 맞지 않는 기록은 제외
        risk = abs(r["entry"] - r["stop"])
        if r["side"] == "숏":
            mfe, mae = (r["entry"] - seg["Low"].min()) / risk, (seg["High"].max() - r["entry"]) / risk
        else:
            mfe, mae = (seg["High"].max() - r["entry"]) / risk, (r["entry"] - seg["Low"].min()) / risk
        rows.append({"날짜": r["date"], "티커": r["sym"], "셋업": r["setup_name"], "R": r["R"],
                     "MFE(R)": max(mfe, 0), "MAE(R)": max(mae, 0), "놓친 R": max(mfe, 0) - r["R"]})
    return pd.DataFrame(rows)


# ───────────────────────── alerts ─────────────────────────
ALERTS = os.path.join(os.path.dirname(JOURNAL), "alerts.json")
ALERT_KINDS = {  # kind: (설명, 값 필요, 교차형)
    "가격 이상": ("현재가 ≥ 값", True, False), "가격 이하": ("현재가 ≤ 값", True, False),
    "VWAP 상향 돌파": ("VWAP 아래→위", False, True), "VWAP 하향 이탈": ("VWAP 위→아래", False, True),
    "ORH 돌파": ("오프닝 레인지 고점 위로", False, True), "ORL 이탈": ("오프닝 레인지 저점 아래로", False, True),
    "전일 고점 돌파": ("PDH 위로", False, True), "전일 저점 이탈": ("PDL 아래로", False, True),
    "RVOL 이상": ("상대거래량 ≥ 값", True, False), "셋업 발생": ("앱이 셋업을 감지하면", False, False),
}


def load_alerts() -> list[dict]:
    try:
        with open(ALERTS, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_alerts(al: list[dict]):
    os.makedirs(os.path.dirname(ALERTS), exist_ok=True)
    tmp = ALERTS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(al, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ALERTS)


def alert_state(al: dict, a: dict) -> tuple[bool | None, str]:
    """조건 충족 여부와 설명. 판단 불가면 None."""
    k, v, px = al["kind"], al.get("value"), a["last"]
    if k == "가격 이상":
        return px >= v, f"{px:,.2f} ≥ {v:,.2f}"
    if k == "가격 이하":
        return px <= v, f"{px:,.2f} ≤ {v:,.2f}"
    if k == "RVOL 이상":
        return (a["rvol"] >= v if ok(a["rvol"]) else None), f"RVOL {num(a['rvol'], 1)}x"
    if k == "셋업 발생":
        hits = [x for x in a["setups"] if not al.get("setup") or x["key"] == al["setup"]]
        return bool(hits), " · ".join(f"{x['side']} {x['name']} (진입 {x['entry']:,.2f} / 손절 {x['stop']:,.2f})" for x in hits)
    lvl = {"VWAP 상향 돌파": a["vwap"], "VWAP 하향 이탈": a["vwap"], "ORH 돌파": a["orh"], "ORL 이탈": a["orl"],
           "전일 고점 돌파": a["pdh"], "전일 저점 이탈": a["pdl"]}[k]
    if not ok(lvl) or a["mode"] == "pre" or (k.startswith("OR") and not a["or_done"]):
        return None, ""
    up = k in ("VWAP 상향 돌파", "ORH 돌파", "전일 고점 돌파")
    return (px > lvl if up else px < lvl), f"{px:,.2f} vs {k.split()[0]} {lvl:,.2f}"


def notify(title: str, body: str):
    if os.uname().sysname == "Darwin":
        esc = lambda x: x.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(["osascript", "-e", f'display notification "{esc(body)}" with title "트레이딩 데스크" '
                                           f'subtitle "{esc(title)}" sound name "Glass"'], timeout=10, check=False)
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if tok and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data={"chat_id": chat, "text": f"{title}\n{body}"}, timeout=10)
        except Exception:
            pass


class AlertWorker:
    """Streamlit 서버가 켜져 있는 동안 60초마다 알림 조건을 확인 (브라우저 탭을 닫아도 동작)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.log = deque(maxlen=200)
        self.prev: dict[str, bool] = {}
        self.last_run, self.last_err = None, None
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                if market_state(now_ny()) in ("프리마켓", "정규장", "애프터마켓"):
                    self.check()
            except Exception as e:
                self.last_err = f"{now_ny():%H:%M} {e}"
            time.sleep(60)

    def check(self) -> list[str]:
        with self.lock:
            al = load_alerts()
        act = [x for x in al if x.get("active", True)]
        if not act:
            return []
        syms = tuple(sorted({x["sym"] for x in act}))
        data = _bars_raw(syms, "5m", "1mo")
        today, fired = now_ny().date().isoformat(), []
        snaps = {s: analyze(data.get(s), None, None, None, 15) for s in syms}
        for x in act:
            a = snaps.get(x["sym"])
            if not a:
                continue
            state, why = alert_state(x, a)
            if state is None:
                continue
            cross = ALERT_KINDS[x["kind"]][2]
            was = self.prev.get(x["id"])
            self.prev[x["id"]] = state
            trigger = (state and was is False) if cross else state
            if trigger and x.get("last_fired") != today:
                x["last_fired"] = today
                msg = f"{x['sym']} {x['kind']}" + (f" ({x['note']})" if x.get("note") else "")
                notify(msg, why)
                self.log.appendleft(dict(time=now_ny(), sym=x["sym"], kind=x["kind"], why=why))
                fired.append(msg)
        if fired:
            with self.lock:
                cur = {y["id"]: y for y in load_alerts()}
                for x in act:
                    if x["id"] in cur and x.get("last_fired"):
                        cur[x["id"]]["last_fired"] = x["last_fired"]
                save_alerts(list(cur.values()))
        self.last_run = now_ny()
        return fired


@st.cache_resource
def alert_worker() -> AlertWorker:
    return AlertWorker()


def add_alert(sym: str, kind: str, value=None, setup=None, note=""):
    w = alert_worker()
    with w.lock:
        al = load_alerts()
        al.append(dict(id=uuid.uuid4().hex[:8], sym=sym.upper(), kind=kind, value=value, setup=setup, note=note,
                       active=True, last_fired=None, created=now_ny().isoformat(timespec="minutes")))
        save_alerts(al)


# ───────────────────────── setup backtest ─────────────────────────
EXIT_MODES = {"T1": "1차 목표 전량 청산", "HALF": "1차에서 절반 익절 + 본전 손절 → 2차 목표", "EOD": "손절 아니면 종가 청산"}


def _simulate(O, H, L, C, i0, sg, e, stop, t1, t2, mode) -> tuple[float, int]:
    """i0 봉 시가에 진입. 같은 봉에서 손절·목표가 모두 닿으면 손절로 처리 (보수적). 반환: (청산가 가중 R, 청산 봉)."""
    risk = abs(e - stop)
    n = len(C)
    half, stp = None, stop
    for b in range(i0, n):
        if sg * (L[b] if sg > 0 else H[b]) <= sg * stp:  # 손절 (갭이면 시가 체결)
            px = O[b] if b > i0 and sg * O[b] < sg * stp else stp
            r = (px - e) * sg / risk
            return (0.5 * half + 0.5 * r, b) if half is not None else (r, b)
        hi_lo = H[b] if sg > 0 else L[b]
        if mode == "T1" and sg * hi_lo >= sg * t1:
            px = O[b] if b > i0 and sg * O[b] > sg * t1 else t1
            return (px - e) * sg / risk, b
        if mode == "HALF":
            if half is None and sg * hi_lo >= sg * t1:
                half, stp = (t1 - e) * sg / risk, e
            if half is not None and sg * hi_lo >= sg * t2:
                return 0.5 * half + 0.5 * (t2 - e) * sg / risk, b
    r = (C[-1] - e) * sg / risk
    return (0.5 * half + 0.5 * r, n - 1) if half is not None else (r, n - 1)


@st.cache_data(ttl=3600, show_spinner=False)
def backtest(sym: str, or_min: int, mode: str, align: bool, cost_bps: float) -> pd.DataFrame:
    data = bars((sym, "SPY"), "5m", "60d")
    df, spy = data.get(sym), data.get("SPY")
    if df is None or len(df) < 500:
        return pd.DataFrame()
    if df.index[-1] + pd.Timedelta(minutes=5) > now_ny():
        df = df.iloc[:-1]
    idx, m = df.index, mins(df.index)
    d = np.array(idx.date)
    reg, pre = (m >= 570) & (m < 960), (m >= 240) & (m < 570)
    R = df[reg]
    c = R["Close"]
    ema9, ema20, rs14 = c.ewm(span=9, adjust=False).mean(), c.ewm(span=20, adjust=False).mean(), rsi(c)
    tr = true_range(R)
    atrb = tr.rolling(14).mean()
    sma20 = c.rolling(20).mean()
    sq_on = (2 * c.rolling(20).std()) < (1.5 * tr.rolling(20).mean())
    mom = c - ((R["High"].rolling(20).max() + R["Low"].rolling(20).min()) / 2 + sma20) / 2
    rd, rm = np.array(R.index.date), mins(R.index)
    cv = R["Volume"].groupby(rd).cumsum()
    piv = pd.DataFrame({"d": rd, "m": rm, "cv": cv.to_numpy()}).pivot_table(index="d", columns="m", values="cv")
    base = piv.rolling(20, min_periods=3).mean().shift(1)
    spy_up = None
    if spy is not None and align:
        smm = mins(spy.index)
        sr = spy[(smm >= 570) & (smm < 960)]
        svw, _ = daily_vwap(sr)
        spy_up = (sr["Close"] > svw)
    days = sorted(set(rd))
    trades = []
    for k in range(1, len(days)):
        day = days[k]
        S = R[rd == day]
        if len(S) < 12:
            continue
        P = R[rd == days[k - 1]]
        PM = df[pre & (d == day)]
        vw, sd = vwap_bands(S)
        sm_ = mins(S.index)
        pdc, open_ = float(P["Close"].iloc[-1]), float(S["Open"].iloc[0])
        or_end = 570 + max(or_min, 5)
        orb = S[sm_ < or_end]
        hod, lod = S["High"].cummax().to_numpy(), S["Low"].cummin().to_numpy()
        e9, e20, r14 = ema9.reindex(S.index), ema20.reindex(S.index), rs14.reindex(S.index)
        sq, mo, ab = sq_on.reindex(S.index), mom.reindex(S.index).to_numpy(), atrb.reindex(S.index).to_numpy()
        cvd = cv.reindex(S.index).to_numpy()
        bd = base.loc[day] if day in base.index else None
        O, H, L, C = (S[x].to_numpy() for x in ("Open", "High", "Low", "Close"))
        common = dict(bar=5, orh=float(orb["High"].max()), orl=float(orb["Low"].min()), gap=open_ / pdc - 1,
                      pmh=float(PM["High"].max()) if len(PM) else np.nan, pml=float(PM["Low"].min()) if len(PM) else np.nan,
                      pdc=pdc, open=open_, pdh=float(P["High"].max()), pdl=float(P["Low"].min()))
        taken = set()
        for i in range(2, len(S) - 1):
            rv = np.nan
            if bd is not None and sm_[i] in bd.index and ok(bd[sm_[i]]) and bd[sm_[i]] > 0:
                rv = cvd[i] / bd[sm_[i]]
            a = dict(common, or_done=bool(sm_[i] >= or_end), hod=hod[i], lod=lod[i], rvol=rv, rsi=float(r14.iloc[i]),
                     atrb=float(ab[i]), mom=float(mo[i]), sq_fired=bool(sq.iloc[max(i - 2, 0):i].any() and not sq.iloc[i]))
            a["_S"] = (S.iloc[:i + 1], vw.iloc[:i + 1], sd.iloc[:i + 1], e9.iloc[:i + 1], e20.iloc[:i + 1],
                       r14.iloc[:i + 1], sq.iloc[:i + 1], None)
            for x in find_setups(a):
                key = (x["key"], x["side"])
                if key in taken:
                    continue
                if spy_up is not None:
                    up = spy_up.get(S.index[i])
                    if up is not None and (x["side"] == "롱") != bool(up):
                        continue
                taken.add(key)
                sg = 1 if x["side"] == "롱" else -1
                e = O[i + 1]
                if (e - x["stop"]) * sg <= 0 or (x["t1"] - e) * sg <= 0:
                    continue  # 다음 봉 시가가 이미 손절선 너머이거나 목표 도달
                r, b = _simulate(O, H, L, C, i + 1, sg, e, x["stop"], x["t1"], x["t2"], mode)
                cost_r = 2 * e * cost_bps / 1e4 / abs(e - x["stop"])
                trades.append(dict(date=day, time=f"{S.index[i + 1]:%H:%M}", sym=sym, setup=x["key"], setup_name=SETUP_NAMES[x["key"]],
                                   side=x["side"], entry=e, stop=x["stop"], t1=x["t1"], R=r - cost_r, bars=b - i,
                                   phase=(phase_of(int(sm_[i + 1])) or (0, 0, "-"))[2]))
    return pd.DataFrame(trades)


# ───────────────────────── UI pieces ─────────────────────────
def verdict_card(pname: str, v: dict, active: bool):
    items = "".join(f"<li>{w}</li>" for w in v["why"])
    st.markdown(f"<div class='vcard{' active' if active else ''}'><div class='p'>{pname}{' · 선택됨' if active else ''}</div>"
                f"<div class='h {v['lvl']}'>{v['head']}</div><ul>{items}</ul></div>", unsafe_allow_html=True)


def score_card(rg: dict):
    col = "#1f9d55" if rg["score"] >= 60 else "#d97706" if rg["score"] > 35 else "#e5484d"
    rows = "".join(f"<div class='kv'><span>{k}</span><span>{x:.1f} / {w}</span></div>" for k, (x, w) in rg["comp"].items())
    notes = "<br>".join(rg["notes"])
    st.markdown(f"<div class='card score'><div style='font-size:12px;opacity:.6'>시장 점수</div>"
                f"<div class='big'>{rg['score']:.0f}<small> / 100 · {rg['bias']}</small></div>"
                f"<div class='bar'><i style='width:{rg['score']:.0f}%;background:{col}'></i></div>{rows}"
                f"<div class='note'>{notes}</div></div>", unsafe_allow_html=True)


def setup_table(fit: list[dict], acct: float, risk_pct: float):
    rows = []
    for x in fit:
        r = abs(x["entry"] - x["stop"])
        sh = int(acct * risk_pct / 100 / r) if r > 0 else 0
        sh = min(sh, int(acct * 4 / x["entry"]))  # 레버리지 4배 상한
        rows.append({"셋업": x["name"], "방향": x["side"], "진입": x["entry"], "손절": x["stop"], "1차 목표": x["t1"],
                     "2차 목표": x["t2"], "R:R": x["rr"], "수량": sh, "손실 한도($)": sh * r, "메모": x["note"], "출처": x["src"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
        k: st.column_config.NumberColumn(format="%.2f") for k in ("진입", "손절", "1차 목표", "2차 목표")} | {
        "R:R": st.column_config.NumberColumn(format="%.1f"), "손실 한도($)": st.column_config.NumberColumn(format="$%.0f")})


def base_layout(fig, h):
    fig.update_layout(height=h, margin=dict(l=10, r=10, t=36, b=10), hovermode="x unified",
                      legend=dict(orientation="h", y=1.04, x=0), title_font=dict(size=14),
                      font=dict(family="-apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', sans-serif"))
    return fig


# ───────────────────────── sidebar ─────────────────────────
st.session_state.setdefault("sym", "NVDA")
now = now_ny()
state = market_state(now)
with st.sidebar:
    st.markdown("### 트레이딩 데스크")
    nxt = "" if state == "정규장" else f"<br><span>다음 개장 {next_open(now).astimezone(AMS):%m/%d %H:%M} 암스테르담</span>"
    st.markdown(f"<div class='side'><b>{state}</b><br><span>뉴욕 {now:%H:%M} · 암스테르담 {now.astimezone(AMS):%H:%M}</span>{nxt}</div>",
                unsafe_allow_html=True)
    st.divider()
    pname = st.segmented_control("전략 프로필", list(PROFILES), default="밸런스형", key="profile") or "밸런스형"
    st.caption(PROFILES[pname]["desc"])
    acct = st.number_input("계좌 규모 (USD)", 1000, 10_000_000, 10_000, step=1000, key="acct")
    shorts = st.toggle("공매도 가능 계좌", value=False, key="shorts", help="끄면 숏 셋업은 참고용으로만 보이고 점수·판단에서 빠집니다.")
    or_min = st.select_slider("오프닝 레인지", [5, 15, 30], value=15, format_func=lambda x: f"{x}분", key="or_min")
    auto = st.toggle("60초마다 자동 새로고침", value=False, key="auto")
    if st.button("데이터 새로고침", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    alert_worker()
    st.caption(f"데이터 {data_source()} · 알림 {sum(x.get('active', True) for x in load_alerts())}개 켜짐  \n투자 조언이 아닌 보조 도구입니다.")


# ───────────────────────── pages ─────────────────────────
def page_market():
    sc = scan(or_min)
    rg = regime(sc)
    now = now_ny()
    sn, vx = sc["snaps"], sc["vix"]
    st.title("시장")
    stale = " · 데이터가 20분 넘게 지연됨" if state == "정규장" and sc["spy_t"] is not None and now - sc["spy_t"] > pd.Timedelta(minutes=20) else ""
    st.caption(f"{state} · {sc['fetched'].astimezone(AMS):%H:%M} 암스테르담 기준{stale}")
    ts = today_status(enrich(load_journal()), acct, pname)
    if ts["stop"]:
        st.error(f"오늘 매매 한도에 도달했습니다 ({ts['n']}회 · {usd(ts['pnl'])}, 손실 {ts['loss_pct']:.2f}% / 한도 {ts['limit']}%). 시장이 좋아도 오늘은 종료하세요.")
    elif ts["n"]:
        st.caption(f"오늘 내 매매 {ts['n']}/{ts['max_trades']}회 · {ts['R']:+.1f}R · {usd(ts['pnl'])}")
    cols = st.columns(3)
    for col, p in zip(cols, PROFILES):
        with col:
            verdict_card(p, verdict(p, rg, now, shorts, econ_week()), p == pname)
    st.write("")
    v, v9, v3 = (vx.get(k, {}) for k in VIXES)
    items = [(k, num(sn[k]["last"]), pct(sn[k]["chg"]), tone(sn[k]["chg"])) for k in MARKET if k in sn]
    items += [("VIX", num(v.get("v"), 1), pct(v.get("chg"), 1), tone(-(v.get("chg") or 0))),
              ("VIX 9일 / 3개월", f"{num(v9.get('v'), 1)} / {num(v3.get('v'), 1)}",
               "역전 (단기 스트레스)" if rg["back9"] or rg["back3"] else "정상", "x-down" if rg["back9"] or rg["back3"] else ""),
              ("시장 폭", f"{rg['breadth'] * 100:.0f}%", "VWAP 위 비율", "")]
    tiles(items)

    l, r = st.columns([1, 2], gap="large")
    with l:
        score_card(rg)
    with r:
        df = sc["spy_df"]
        if df is not None:
            m = mins(df.index)
            last_d = df.index[(m >= 570) & (m < 960)].date[-1]
            g = df[(np.array(df.index.date) == last_d) & (m >= 570) & (m < 960)]
            vw, sd = vwap_bands(g)
            x = g.index.tz_localize(None)
            fig = go.Figure()
            fig.add_candlestick(x=x, open=g["Open"], high=g["High"], low=g["Low"], close=g["Close"], name="SPY",
                                increasing_line_color=UP, decreasing_line_color=DOWN)
            fig.add_scatter(x=x, y=vw, name="VWAP", line=dict(color="#7c3aed", width=2))
            for kk, dash in ((1, "dot"), (2, "dash")):
                for sgn in (1, -1):
                    fig.add_scatter(x=x, y=vw + sgn * kk * sd, showlegend=False, line=dict(color="#a78bfa", width=1, dash=dash),
                                    hoverinfo="skip")
            fig.update_layout(title=f"SPY {last_d:%m/%d} · 5분봉 · VWAP ±1σ/2σ", xaxis_rangeslider_visible=False)
            st.plotly_chart(base_layout(fig, 400), width="stretch")

    t_ev, t_in, t_cat, t_time = st.tabs(["이번 주 일정", "섹터 · 내부 지표", "카테고리 흐름", "장중 시간표"])
    with t_ev:
        wk = [now.date() + dt.timedelta(days=i) for i in range(8)]
        day = lambda d: f"{d:%m/%d} ({'월화수목금토일'[d.weekday()]})"
        ev = [dict(t=dt.datetime.combine(d, dt.time(14), NY), 날짜=day(d), 시각=ams(840, d), 이벤트="FOMC 금리 결정", 중요도="높음", 예상="", 이전="")
              for d in wk if d in FOMC]
        for e in econ_week():
            if e["t"].date() >= now.date():
                ev.append(dict(t=e["t"], 날짜=day(e["t"].date()), 시각=f"{e['t'].astimezone(AMS):%H:%M}", 이벤트=e["title"],
                               중요도="높음" if e["impact"] == "High" else "중간", 예상=e["fc"], 이전=e["prev"]))
        for s_, (ed, tm) in sc["earn"].items():
            try:
                d = dt.date.fromisoformat(ed)
            except Exception:
                continue
            if now.date() <= d <= wk[-1]:
                ev.append(dict(t=dt.datetime.combine(d, dt.time(8 if tm == "bmo" else 16, 30), NY), 날짜=day(d),
                               시각="장전" if tm == "bmo" else "장후" if tm == "amc" else "", 이벤트=f"{s_} 어닝", 중요도="관심종목", 예상="", 이전=""))
        if ev:
            st.dataframe(pd.DataFrame(sorted(ev, key=lambda e: e["t"])).drop(columns="t"), hide_index=True, width="stretch",
                         height=min(36 * len(ev) + 40, 560))
        else:
            st.info("이번 주 주요 일정이 없습니다.")
        st.caption("시각은 암스테르담 기준. 경제지표는 ForexFactory 주간 피드(미국 고·중요도). 중요도 '높음' 지표 전후 30분은 밸런스·방어형이 자동으로 관망합니다.")
    with t_in:
        secs = [(SECTORS[k], sn[k]) for k in SECTORS if k in sn]
        tk = sc.get("tick")
        items = [("섹터 폭", f"{sum(above_ref(x) for _, x in secs)}/{len(secs)}", "VWAP 위 섹터", ""),
                 ("동일가중 − 시총가중", f"{rg['eq_gap'] * 100:+.2f}%p" if ok(rg["eq_gap"]) else "-",
                  "고른 참여" if ok(rg["eq_gap"]) and rg["eq_gap"] > 0 else "대형주 쏠림", tone(rg["eq_gap"]))]
        if tk is not None and len(tk):
            items += [("합성 TICK (최근 봉)", f"{tk.iloc[-1]:+.0f}", f"범위 ±{len(WATCHLIST) - 1 + len(SECTORS)}", tone(tk.iloc[-1])),
                      ("누적 TICK", f"{tk.sum():+.0f}", "매수 우위" if tk.sum() > 0 else "매도 우위", tone(tk.sum()))]
        tiles(items)
        c1, c2 = st.columns(2)
        with c1:
            if secs:
                secs.sort(key=lambda x: x[1]["chg"])
                fig = go.Figure(go.Bar(x=[x["chg"] * 100 for _, x in secs], y=[n for n, _ in secs], orientation="h",
                                       marker_color=[UP if x["chg"] > 0 else DOWN for _, x in secs],
                                       marker_opacity=[1 if above_ref(x) else 0.45 for _, x in secs],
                                       hovertemplate="%{y}<br>%{x:.2f}%<extra></extra>"))
                fig.update_layout(title="섹터 등락 (진한 막대 = VWAP 위)", xaxis_title="%")
                st.plotly_chart(base_layout(fig, 380), width="stretch")
        with c2:
            if tk is not None and len(tk):
                x = tk.index.tz_localize(None)
                fig = make_subplots(specs=[[{"secondary_y": True}]])
                fig.add_bar(x=x, y=tk.values, marker_color=[UP if v > 0 else DOWN for v in tk.values], name="TICK", opacity=0.6)
                fig.add_scatter(x=x, y=tk.cumsum().values, name="누적", line=dict(color="#7c3aed", width=2), secondary_y=True)
                fig.update_layout(title=f"합성 TICK ({tk.index[0]:%m/%d})")
                st.plotly_chart(base_layout(fig, 380), width="stretch")
        st.caption("NYSE \\$TICK·\\$ADD는 무료 데이터에 없어 대용 지표를 씁니다: 섹터 ETF 11개, 동일가중(RSP)−시총가중(SPY), 관심종목+섹터 60여 개로 만든 합성 TICK.")
    with t_cat:
        rows = []
        for c, ss in CATEGORIES.items():
            xs = [sn[k] for k in ss if k in sn]
            if xs:
                rows.append(dict(cat=c, chg=np.nanmean([a["chg"] for a in xs]),
                                 rvol=np.nanmean([a["rvol"] for a in xs if ok(a["rvol"])] or [np.nan])))
        h = pd.DataFrame(rows).sort_values("chg")
        fig = go.Figure(go.Bar(x=h["chg"] * 100, y=h["cat"], orientation="h", marker_color=[UP if v > 0 else DOWN for v in h["chg"]],
                               customdata=h["rvol"], hovertemplate="%{y}<br>평균 등락 %{x:.2f}%<br>평균 RVOL %{customdata:.2f}x<extra></extra>"))
        fig.update_layout(title="관심종목 카테고리별 평균 등락", xaxis_title="%")
        st.plotly_chart(base_layout(fig, 420), width="stretch")
    with t_time:
        m_now = now.hour * 60 + now.minute
        rows = [{"구간": n, "뉴욕": f"{hm(a)}–{hm(b)}", "암스테르담": f"{ams(a)}–{ams(b)}", "운용": t,
                 "지금": "지금" if a <= m_now < b and now.weekday() < 5 else ""} for a, b, n, t in PHASES]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        gap_h = (dt.datetime.combine(now.date(), dt.time(12), AMS).utcoffset() - dt.datetime.combine(now.date(), dt.time(12), NY).utcoffset()).seconds // 3600
        st.caption(f"오늘 뉴욕–암스테르담 시차 {gap_h}시간.")


def page_scanner():
    sc = scan(or_min)
    rg = regime(sc)
    st.title("스캐너")
    t_w, t_m = st.tabs(["관심종목 (셋업·점수)", "시장 전체 무버"])
    with t_m:
        movers_tab()
    with t_w:
        watch_tab(sc, rg)


def movers_tab():
    c = st.columns([2, 1, 1, 2])
    kind = c[0].radio("목록", ["day_gainers", "day_losers", "most_actives"], horizontal=True, key="mv_kind",
                      format_func={"day_gainers": "급등", "day_losers": "급락", "most_actives": "거래 활발"}.get)
    mcap = c[1].selectbox("최소 시가총액", [3e8, 2e9, 1e10], format_func=fmt_big, key="mv_cap")
    minp = c[2].number_input("최소 가격", 0.0, 1000.0, 2.0, 1.0, key="mv_px")
    df = movers(kind)
    if df.empty:
        st.info("Yahoo 스크리너를 불러오지 못했습니다.")
        return
    df = df[(df["시가총액"].fillna(0) >= mcap) & (df["현재가"].fillna(0) >= minp)].reset_index(drop=True)
    c[3].caption("Yahoo 미국 전체 스크리너 (5분 캐시). 장중 기준이며 프리마켓·애프터 등락은 별도 열. 행을 클릭하면 종목 분석으로 이동.")
    ev = st.dataframe(df, hide_index=True, width="stretch", height=min(36 * len(df) + 40, 760), on_select="rerun",
                      selection_mode="single-row", key="mov_tbl", column_config={
            "현재가": st.column_config.NumberColumn(format="%.2f"), "등락": st.column_config.NumberColumn("등락%", format="%+.2f"),
            "프리마켓": st.column_config.NumberColumn("프리%", format="%+.2f"), "애프터": st.column_config.NumberColumn("애프터%", format="%+.2f"),
            "거래량": st.column_config.NumberColumn(format="compact"), "일RVOL": st.column_config.NumberColumn("거래량/3개월평균", format="%.1fx"),
            "시가총액": st.column_config.NumberColumn(format="compact"), "관심": st.column_config.CheckboxColumn("관심종목")})
    if ev.selection.rows:
        st.session_state["_goto"] = df.iloc[ev.selection.rows[0]]["티커"]
        st.rerun()


def watch_tab(sc: dict, rg: dict):
    st.caption(f"{pname} 기준 점수순 · 시장 {rg['bias']} ({rg['score']:.0f}) · 행을 클릭하면 종목 분석으로 이동합니다")
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    cats = c1.multiselect("카테고리", list(CATEGORIES), placeholder="전체")
    only = c2.toggle("셋업 있는 종목만", value=False)
    min_rv = c3.number_input("최소 RVOL", 0.0, 10.0, 0.0, 0.5)
    side = c4.selectbox("방향", ["전체", "롱", "숏"])
    rows = []
    for s in WATCHLIST:
        a = sc["snaps"].get(s)
        if not a or (cats and CAT_OF[s] not in cats):
            continue
        score, fit = profile_score(a, pname, rg["bias"], shorts)
        if only and not fit:
            continue
        if min_rv and not (ok(a["rvol"]) and a["rvol"] >= min_rv):
            continue
        if side != "전체" and a["bias"] != side:
            continue
        rows.append({"티커": s, "카테고리": CAT_OF[s], "점수": round(score), "현재가": a["last"],
                     "등락%": a["chg"] * 100 if ok(a["chg"]) else None, "갭%": a["gap"] * 100 if ok(a["gap"]) else None,
                     "RVOL": a["rvol"], "VWAP σ": a["vwz"], "ATR%": a["atr_pct"] * 100 if ok(a["atr_pct"]) else None,
                     "ATR 소진%": a["atr_used"] * 100 if ok(a["atr_used"]) else None,
                     "RS vs SPY%": a["rs"] * 100 if ok(a["rs"]) else None, "RSI": a["rsi"],
                     "바이어스": a["bias"],
                     "셋업": " · ".join(f"{x['side']} {x['name']}" for x in fit),
                     "태그": " · ".join(a["tags"]), "거래대금(20일)": a["dvol"]})
    if not rows:
        st.info("조건에 맞는 종목이 없습니다.")
        return
    df = pd.DataFrame(rows).sort_values("점수", ascending=False)
    ev = st.dataframe(df, hide_index=True, width="stretch", height=min(38 * len(df) + 40, 900),
                      on_select="rerun", selection_mode="single-row", key="scan_tbl", column_config={
            "점수": st.column_config.ProgressColumn("점수", min_value=0, max_value=100, format="%d"),
            "현재가": st.column_config.NumberColumn(format="%.2f"),
            "등락%": st.column_config.NumberColumn(format="%+.2f"), "갭%": st.column_config.NumberColumn(format="%+.2f"),
            "RVOL": st.column_config.NumberColumn(format="%.2fx"), "VWAP σ": st.column_config.NumberColumn(format="%+.1f"),
            "ATR%": st.column_config.NumberColumn(format="%.1f"), "ATR 소진%": st.column_config.NumberColumn(format="%.0f"),
            "RS vs SPY%": st.column_config.NumberColumn(format="%+.2f"), "RSI": st.column_config.NumberColumn(format="%.0f"),
            "거래대금(20일)": st.column_config.NumberColumn(format="compact")})
    if ev.selection.rows:
        st.session_state["_goto"] = df.iloc[ev.selection.rows[0]]["티커"]
        st.rerun()
    with st.expander("컬럼 설명"):
        st.markdown("""
- **RVOL**: 같은 시각까지의 누적 거래량 ÷ 최근 20일 같은 시각 평균. 2배 이상이면 '오늘 뭔가 있다'.
- **VWAP σ**: 세션 VWAP에서 몇 표준편차 떨어져 있는지. ±2σ 밖은 과열/과매도.
- **ATR%**: 하루 평균 변동폭(14일) ÷ 가격. **ATR 소진%**: 오늘 고저폭이 ATR의 몇 %를 썼는지 — 100% 넘으면 추가 확장 여력이 줄어듦.
- **RS vs SPY**: 베타를 감안한 SPY 대비 초과 등락. 리더는 눌림에서 사고, 약세주는 반등에서 판다.
- **점수**: 선택한 프로필 기준 적합도. 공격형은 RVOL·갭·변동성, 방어형은 유동성·시장 정렬·낮은 변동성을 높게 봅니다.
- 장외 시간에는 직전 정규장 기준이며, 프리마켓에는 갭과 프리마켓 거래량 기준입니다.
""")


def page_ticker():
    c1, c2, c3, c4, c5 = st.columns([2, 1, 1, 1, 1])
    pick = c1.selectbox("종목", WATCHLIST, key="sym", format_func=lambda s: f"{s} · {CAT_OF[s]}")
    custom = c2.text_input("직접 입력", key="custom", placeholder="예: COIN").strip().upper()
    iv = c3.selectbox("봉", ["1분", "5분"], index=1, key="iv")
    span = c4.selectbox("범위", ["오늘", "2일", "5일"], index=0, key="span")
    ext = c5.toggle("장외 포함", value=True, key="ext")
    sym = custom or pick

    sc = scan(or_min)
    rg = regime(sc)
    with st.spinner(f"{sym} 불러오는 중…"):
        d5 = bars((sym, "SPY"), "5m", "60d")
        dl = daily((sym,))
        if sym in sc["snaps"] and not custom:
            a = sc["snaps"][sym]
        else:
            spy = sc["snaps"].get("SPY")
            a = analyze(d5.get(sym), dl.get(sym), d5.get("SPY"), spy["chg"] if spy else None, or_min,
                        sc["earn"].get(sym))
    if not a:
        st.error(f"'{sym}' 장중 데이터를 찾지 못했습니다.")
        return
    inf = info_of(sym)
    score, fit = profile_score(a, pname, rg["bias"], shorts)
    st.title(f"{inf.get('shortName') or sym} ({sym})")
    st.caption(f"{CAT_OF.get(sym, '기타')} · {a['last_t']:%m/%d %H:%M} ET 기준 · {pname} 적합도 {score:.0f}/100 · 시장 {rg['bias']}")

    tiles([("현재가", num(a["last"]), pct(a["chg"]), tone(a["chg"])), ("갭", pct(a["gap"]), "전일 종가 대비", tone(a["gap"])),
           ("RVOL", f"{num(a['rvol'], 2)}x", "같은 시각 평균 대비", "x-warn" if ok(a["rvol"]) and a["rvol"] >= 2 else ""),
           ("VWAP 거리", f"{num(a['vwz'], 1)}σ", f"VWAP {num(a['vwap'])}", tone(a["vwz"])),
           ("ATR (14일)", f"{num(a['atr_pct'] * 100 if ok(a['atr_pct']) else None, 1)}%",
            f"오늘 {num(a['atr_used'] * 100 if ok(a['atr_used']) else None, 0)}% 사용", ""),
           ("SPY 대비 강도", pct(a["rs"]), f"베타 {a['beta']:.2f}", tone(a["rs"])), ("RSI (14)", num(a["rsi"], 0), "", ""),
           ("방향", a["bias"], "거래량 델타 " + (f"{a['delta'] * 100:+.0f}%" if ok(a["delta"]) else "-"),
            "x-up" if a["bias"] == "롱" else "x-down" if a["bias"] == "숏" else "")])
    pills(a["tags"])

    mdl = next_bar_model(sym)
    opt = options_view(sym, float(f"{a['last']:.3g}")) if sym != "BTC-USD" else None

    # 차트
    if iv == "1분":
        raw = bars((sym,), "1m", "5d").get(sym)
    else:
        raw = d5.get(sym)
    if raw is None:
        st.warning("차트 데이터 없음")
        return
    dates = sorted(set(raw.index[(mins(raw.index) >= 570) & (mins(raw.index) < 960)].date))
    keep = dates[-{"오늘": 1, "2일": 2, "5일": 5}[span]:]
    if a["mode"] == "pre":
        keep = keep + [a["cur"]]
    g = raw[np.isin(np.array(raw.index.date), keep)]
    gm = mins(g.index)
    if not ext:
        g = g[(gm >= 570) & (gm < 960)]
        gm = mins(g.index)
    regb = g[(gm >= 570) & (gm < 960)]
    x = g.index.tz_localize(None)
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.66, 0.17, 0.17], vertical_spacing=0.02)
    fig.add_candlestick(x=x, open=g["Open"], high=g["High"], low=g["Low"], close=g["Close"], name=sym,
                        increasing_line_color=UP, decreasing_line_color=DOWN, row=1, col=1)
    if len(regb):
        vw, sd = daily_vwap(regb)
        xr = regb.index.tz_localize(None)
        brk = pd.Series(regb.index.date, index=regb.index).ne(pd.Series(regb.index.date, index=regb.index).shift())
        gaps = lambda s: s.where(~brk)  # 날짜가 바뀌는 지점에서 선을 끊음
        fig.add_scatter(x=xr, y=gaps(vw), name="VWAP", line=dict(color="#7c3aed", width=2), row=1, col=1)
        for kk, dash in ((1, "dot"), (2, "dash")):
            for sg in (1, -1):
                fig.add_scatter(x=xr, y=gaps(vw + sg * kk * sd), showlegend=False, hoverinfo="skip",
                                line=dict(color="#c4b5fd", width=1, dash=dash), row=1, col=1)
        cc = regb["Close"]
        fig.add_scatter(x=xr, y=cc.ewm(span=9, adjust=False).mean(), name="EMA9", line=dict(color="#f59e0b", width=1.2), row=1, col=1)
        fig.add_scatter(x=xr, y=cc.ewm(span=20, adjust=False).mean(), name="EMA20", line=dict(color="#0ea5e9", width=1.2), row=1, col=1)
        if span != "오늘":
            tp = (regb["High"] + regb["Low"] + regb["Close"]) / 3
            fig.add_scatter(x=xr, y=(tp * regb["Volume"]).cumsum() / regb["Volume"].cumsum().replace(0, np.nan),
                            name=f"AVWAP ({keep[0]:%m/%d}~)", line=dict(color="#be185d", width=1.5, dash="dashdot"), row=1, col=1)
        fig.add_scatter(x=xr, y=rsi(cc), name="RSI", line=dict(color="#334155", width=1.2), row=3, col=1)
    vcol = [UP if c >= o else DOWN for o, c in zip(g["Open"], g["Close"])]
    fig.add_bar(x=x, y=g["Volume"], marker_color=vcol, name="거래량", opacity=0.7, row=2, col=1)
    for yv in (30, 70):
        fig.add_hline(y=yv, line=dict(color="#cbd5e1", width=1, dash="dot"), row=3, col=1)

    S_cur = g[(np.array(g.index.date) == a["cur"]) & (gm >= 570) & (gm < 960)] if len(g) else g
    vp = volume_profile(S_cur)
    lv = {"PDH": (a["pdh"], "#0f766e"), "PDL": (a["pdl"], "#0f766e"), "PDC": (a["pdc"], "#64748b"),
          "PMH": (a["pmh"], "#d97706"), "PML": (a["pml"], "#d97706"),
          "ORH": (a["orh"], "#2563eb"), "ORL": (a["orl"], "#2563eb")}
    if vp:
        lv.update(POC=(vp["poc"], "#db2777"), VAH=(vp["vah"], "#f9a8d4"), VAL=(vp["val"], "#f9a8d4"))
    if opt:
        lv.update({"콜월": (opt.get("call_wall"), "#16a34a"), "풋월": (opt.get("put_wall"), "#dc2626"),
                   "감마플립": (opt.get("flip"), "#111827"), "맥스페인": (opt.get("max_pain"), "#9333ea")})
    default = ["PDH", "PDL", "PDC", "ORH", "ORL", "PMH", "PML", "POC"]
    show = st.multiselect("차트에 표시할 레벨", list(lv), default=[k for k in default if k in lv], key="levels")
    lo_, hi_ = float(g["Low"].min()), float(g["High"].max())
    pad = (hi_ - lo_) * 0.6
    for k in show:
        y, col = lv[k]
        if ok(y) and lo_ - pad <= y <= hi_ + pad:
            fig.add_hline(y=y, line=dict(color=col, width=1, dash="dash"), annotation_text=f"{k} {y:,.2f}",
                          annotation_position="top right", annotation_font=dict(size=10, color=col), row=1, col=1)

    # 다음 5/15/30분 예상 범위 콘
    if mdl and mdl["live"]:
        t0 = g.index[-1].tz_localize(None)
        px = a["last"]
        xs = [t0] + [t0 + pd.Timedelta(minutes=h) for h in (5, 15, 30)]
        for z_, op in ((1.96, 0.10), (1.0, 0.20)):
            upb = [px] + [px * np.exp(z_ * mdl["cone"][h]) for h in (5, 15, 30)]
            dnb = [px] + [px * np.exp(-z_ * mdl["cone"][h]) for h in (5, 15, 30)]
            fig.add_scatter(x=xs, y=upb, line=dict(width=0), showlegend=False, hoverinfo="skip", row=1, col=1)
            fig.add_scatter(x=xs, y=dnb, fill="tonexty", fillcolor=f"rgba(37,99,235,{op})", line=dict(width=0),
                            name=f"예상 범위 {'68%' if z_ == 1 else '95%'}", row=1, col=1)

    if ext and sym != "BTC-USD":
        for d in keep:
            for a_, b_ in ((240, 570), (960, 1200)):
                t_a = dt.datetime.combine(d, dt.time(a_ // 60, a_ % 60))
                t_b = dt.datetime.combine(d, dt.time(b_ // 60 % 24, b_ % 60)) if b_ < 1440 else t_a
                fig.add_vrect(x0=t_a, x1=t_b, fillcolor="rgba(148,163,184,0.15)", line_width=0, layer="below")
    if sym != "BTC-USD":
        fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"]),
                                      dict(bounds=[20, 4] if ext else [16, 9.5], pattern="hour")])
    fig.update_layout(xaxis_rangeslider_visible=False)
    fig.update_yaxes(title_text="RSI", row=3, col=1, range=[0, 100])
    st.plotly_chart(base_layout(fig, 720), width="stretch")

    tabs = st.tabs(["트레이드 플랜", "다음 5분", "키 레벨", "옵션 · 감마", "거래량 프로필", "예측 모델", "종목 정보"])
    mid, l, r = tabs[0], tabs[1], tabs[2]
    with l:
        if not mdl:
            st.info("60일 5분봉 데이터가 부족해 모델을 만들 수 없습니다.")
        else:
            if mdl["p_up"] is not None:
                p = mdl["p_up"]
                st.metric("상승 확률 (다음 5분봉)", f"{p * 100:.1f}%",
                          "상승 쪽" if p > 0.53 else "하락 쪽" if p < 0.47 else "동전 던지기 수준", delta_color="off")
            else:
                st.caption("장중(정규장)에만 다음 5분 방향 확률을 냅니다. 아래 범위는 다음 세션 개장 직후 기준(갭 제외)입니다.")
            px = a["last"]
            st.dataframe(pd.DataFrame([{"구간": f"{h}분 후", "68% 범위": f"{px * np.exp(-s):,.2f} – {px * np.exp(s):,.2f}",
                                        "95% 범위": f"{px * np.exp(-1.96 * s):,.2f} – {px * np.exp(1.96 * s):,.2f}",
                                        "1σ": f"±{s * 100:.2f}%"} for h, s in mdl["cone"].items()]),
                         hide_index=True, width="stretch")
            if opt and ok(opt.get("atm_iv")):
                s_iv = opt["atm_iv"] * np.sqrt(5 / (252 * 390))
                st.caption(f"옵션 내재변동성 기준 5분 1σ: ±{s_iv * 100:.2f}% (ATM IV {opt['atm_iv'] * 100:.0f}%)")
            edge = mdl["acc"] - mdl["base_acc"]
            st.caption(f"모델 검증 (최근 20거래일, 표본 외 {mdl['n']:,}개 바): 적중률 {mdl['acc'] * 100:.1f}% vs 기준선 {mdl['base_acc'] * 100:.1f}% "
                       f"({edge * 100:+.1f}%p) · 확신 구간(|p−50%|≥4%p) {mdl['conf_n']}개 바 적중률 {num(mdl['conf_acc'] * 100 if ok(mdl['conf_acc']) else None, 1)}%")
            st.caption("5분 방향 예측은 대형 퀀트도 51~53%면 잘하는 수준입니다. 방향보다 **범위**를 손절·목표 설정에 쓰세요.")
    with mid:
        st.caption(f"{pname} 기준" + (f" · {state}: {a['cur']:%m/%d} 정규장 마감 시점 기준입니다. 다음 세션 개장 후 다시 확인하세요." if state != "정규장" else ""))
        if fit:
            setup_table(fit, acct, PROFILES[pname]["risk"])
        else:
            st.info("지금 이 프로필 조건에 맞는 셋업이 없습니다 — 대기.")
        other = [x for x in a["setups"] if x not in fit]
        if other:
            with st.expander(f"다른 프로필/방향 셋업 {len(other)}개 (참고)"):
                setup_table(other, acct, PROFILES[pname]["risk"])
        jj = enrich(load_journal())
        mine = []
        for key in dict.fromkeys(x["key"] for x in a["setups"]):
            ss = summarize(jj[jj["setup"] == key])
            mine.append(f"{SETUP_NAMES[key]}: " + (f"{ss['n']}회 · 승률 {ss['win'] * 100:.0f}% · 기대값 {ss['exp']:+.2f}R"
                                                   + (" (표본 부족)" if ss["n"] < MIN_N else "") if ss else "기록 없음"))
        if mine:
            st.caption("내 일지 기록 — " + " / ".join(mine))
        if a["setups"]:
            c_a, c_b = st.columns([3, 1])
            opts = fit + other
            ix = c_a.selectbox("일지에 기록할 셋업", range(len(opts)), format_func=lambda i: f"{opts[i]['side']} · {opts[i]['name']}",
                               label_visibility="collapsed", key="j_pick")
            if c_b.button("일지에 기록", width="stretch"):
                x = opts[ix]
                sh = min(int(acct * PROFILES[pname]["risk"] / 100 / abs(x["entry"] - x["stop"])), int(acct * 4 / x["entry"]))
                st.session_state["_prefill"] = dict(sym=sym, side=x["side"], setup=x["key"], profile=pname,
                                                    entry=round(x["entry"], 2), stop=round(x["stop"], 2), t1=round(x["t1"], 2), shares=sh)
                st.session_state["_goto_page"] = "매매 일지"
                st.rerun()
        trig = []
        if ok(a["orh"]) and a["last"] < a["orh"]:
            trig.append(f"ORH {a['orh']:,.2f} 돌파 + VWAP 위 → ORB 롱 트리거")
        if ok(a["pdh"]) and a["last"] < a["pdh"]:
            trig.append(f"전일 고점 {a['pdh']:,.2f} 돌파 → 레벨 돌파 롱")
        if ok(a["vwap"]) and a["last"] < a["vwap"]:
            trig.append(f"VWAP {a['vwap']:,.2f} 회복 → 리클레임 롱")
        if ok(a["orl"]) and a["last"] > a["orl"]:
            trig.append(f"ORL {a['orl']:,.2f} 이탈 → 롱 관점 무효/숏 트리거")
        if trig:
            st.caption("대기 중인 트리거")
            st.markdown("\n".join(f"- {t}" for t in trig))
        chk = [("유동성 (20일 거래대금)", ok(a["dvol"]) and a["dvol"] >= PROFILES[pname]["min_dvol"], fmt_big(a["dvol"])),
               ("변동성 (ATR%)", ok(a["atr_pct"]) and a["atr_pct"] <= PROFILES[pname]["max_atr"], f"{num(a['atr_pct'] * 100 if ok(a['atr_pct']) else None, 1)}%"),
               ("시장 방향 정렬", (a["bias"] == "롱") == (rg["bias"] == "롱 우위") or rg["bias"] == "혼조", f"종목 {a['bias']} / 시장 {rg['bias']}"),
               ("어닝 리스크", not (a["earn_d"] is not None and 0 <= a["earn_d"] <= 1), f"D-{a['earn_d']}" if a["earn_d"] is not None and a["earn_d"] >= 0 else "-")]
        st.caption("체크리스트")
        tiles([(n, "통과" if okk else "주의", v, "x-up" if okk else "x-warn") for n, okk, v in chk])
    with r:
        lvls = [(k, y) for k, (y, _) in lv.items() if ok(y)]
        if ok(a["vwap"]):
            lvls += [("VWAP", a["vwap"]), ("VWAP+2σ", a["vwap"] + 2 * a["vsd"]), ("VWAP−2σ", a["vwap"] - 2 * a["vsd"])]
        if ok(a["hod"]):
            lvls += [("HOD", a["hod"]), ("LOD", a["lod"])]
        lvls.append(("현재가", a["last"]))
        L = pd.DataFrame(lvls, columns=["레벨", "가격"]).sort_values("가격", ascending=False)
        L["거리%"] = (L["가격"] / a["last"] - 1) * 100
        L["ATR 배수"] = (L["가격"] - a["last"]) / a["atr"] if ok(a["atr"]) else np.nan
        st.dataframe(L, hide_index=True, width="stretch", height=min(36 * len(L) + 40, 640), column_config={
            "가격": st.column_config.NumberColumn(format="%.2f"), "거리%": st.column_config.NumberColumn(format="%+.2f"),
            "ATR 배수": st.column_config.NumberColumn(format="%+.2f")})
        with st.popover("이 종목에 알림 걸기"):
            lv_pick = st.selectbox("레벨", [r for r in L["레벨"] if r != "현재가"], key="al_lvl")
            y = float(L.loc[L["레벨"] == lv_pick, "가격"].iloc[0])
            st.caption(f"{lv_pick} {y:,.2f} — 현재가 {'위' if y > a['last'] else '아래'}에 있어 '{'가격 이상' if y > a['last'] else '가격 이하'}'로 겁니다.")
            if st.button("이 레벨에 알림", key="al_add_lvl"):
                add_alert(sym, "가격 이상" if y > a["last"] else "가격 이하", round(y, 2), note=f"{lv_pick}")
                st.toast(f"{sym} {lv_pick} {y:,.2f} 알림 추가")
            if st.button(f"{sym} 셋업 발생 시 알림", key="al_add_setup"):
                add_alert(sym, "셋업 발생", note=pname)
                st.toast(f"{sym} 셋업 알림 추가")

    t1, t2, t3, t4 = tabs[3:]
    with t1:
        if not opt:
            st.info("옵션 데이터 없음")
        else:
            items = [(f"내재 무브 ({opt['front'][5:]})", f"±{opt['move'] * 100:.1f}%", f"스트래들 ${opt['straddle']:.2f}", ""),
                     ("ATM IV", f"{num(opt['atm_iv'] * 100 if ok(opt['atm_iv']) else None, 0)}%",
                      f"일간 ±{opt['atm_iv'] / np.sqrt(252) * 100:.1f}%" if ok(opt["atm_iv"]) else "", ""),
                     ("풋/콜 거래량", num(opt["pc_vol"]), f"미결제약정 {num(opt['pc_oi'])}", ""), ("맥스 페인", num(opt["max_pain"]), "", "")]
            if "net_gex" in opt:
                items += [("순 감마 (1%당)", fmt_big(opt["net_gex"]), "변동 억제" if opt["net_gex"] > 0 else "변동 증폭", tone(opt["net_gex"])),
                          ("감마 플립", num(opt["flip"]), f"콜월 {num(opt['call_wall'], 0)} · 풋월 {num(opt['put_wall'], 0)}", "")]
            tiles(items)
            if "net_gex" in opt:
                by = opt["by_strike"]
                fig = go.Figure(go.Bar(x=by.index, y=by.values, marker_color=[UP if v > 0 else DOWN for v in by.values],
                                       hovertemplate="행사가 %{x}<br>GEX %{y:$,.0f}<extra></extra>"))
                fig.add_vline(x=a["last"], line=dict(color="#111827", dash="dash"), annotation_text="현재가")
                if ok(opt["flip"]):
                    fig.add_vline(x=opt["flip"], line=dict(color="#7c3aed", dash="dot"), annotation_text="감마 플립",
                                  annotation_position="bottom left")
                fig.update_layout(title=f"행사가별 딜러 감마 노출 (만기 {', '.join(opt['exps'])})", xaxis_title="행사가")
                st.plotly_chart(base_layout(fig, 360), width="stretch")
            st.markdown("""
**읽는 법 (옵티버·시타델 시큐리티즈 같은 옵션 마켓메이커 관점)**
- **순 감마 플러스 + 현재가가 감마 플립 위**: 딜러가 오르면 팔고 내리면 사며 헤지 → 변동이 눌리고 큰 행사가 쪽으로 **핀(고정)** 되기 쉬움 → VWAP 평균회귀·방어형에 유리.
- **순 감마 마이너스 / 플립 아래**: 딜러 헤지가 움직임을 키움 → 추세·돌파가 잘 나오고 손절이 빨리 맞음 → 공격형 모멘텀에 유리, 사이즈는 줄이기.
- **콜월/풋월**: 감마가 가장 큰 행사가 — 장중 저항/지지로 자주 작동. **내재 무브**는 옵션 시장이 만기까지 가격에 반영한 기대 변동폭.
- 딜러 포지션은 공개되지 않아 '고객 콜 매수·풋 매수' 표준 가정으로 추정합니다. 주말·장외엔 호가가 비어 값이 부정확할 수 있습니다.
""")
    with t2:
        if vp:
            fig = go.Figure(go.Bar(x=vp["vol"], y=vp["mid"], orientation="h",
                                   marker_color=["#db2777" if vp["val"] <= y <= vp["vah"] else "#cbd5e1" for y in vp["mid"]]))
            fig.add_hline(y=a["last"], line=dict(color="#111827", dash="dash"), annotation_text="현재가")
            fig.update_layout(title=f"{a['cur']:%m/%d} 정규장 거래량 프로필 · POC {vp['poc']:,.2f} · 가치영역 {vp['val']:,.2f}–{vp['vah']:,.2f}")
            st.plotly_chart(base_layout(fig, 420), width="stretch")
            st.caption("POC(최다 거래 가격)는 자석, 가치영역(거래량 70%) 밖으로 나갔다가 다시 들어오면 반대편 끝까지 가는 경향 (마켓 프로파일 80% 룰, Steidlmayer·Dalton).")
        else:
            st.info("오늘 정규장 데이터가 아직 없습니다.")
    with t3:
        if mdl:
            st.markdown(f"**로지스틱 회귀, 워크포워드 검증** — {mdl['days']}거래일 5분봉으로 학습, 최근 20일은 5일 단위로 '미래를 모르는 상태'에서 예측해 채점.")
            cf = mdl["coef"]
            fig = go.Figure(go.Bar(x=cf.values, y=[FEAT_KO.get(k, k) for k in cf.index], orientation="h",
                                   marker_color=[UP if v > 0 else DOWN for v in cf.values]))
            fig.update_layout(title="모델이 보는 신호 (표준화 계수, +는 상승 쪽)", yaxis=dict(autorange="reversed"))
            st.plotly_chart(base_layout(fig, 380), width="stretch")
            rc = mdl["recent"].copy()
            rc.index = rc.index.strftime("%m/%d %H:%M")
            rc = rc.assign(예측=np.where(rc["p"] > 0.5, "상승", "하락"), 실제=np.where(rc["y"] == 1, "상승", "하락"),
                           적중=np.where((rc["p"] > 0.5) == (rc["y"] == 1), "적중", "빗나감"),
                           상승확률=rc["p"] * 100, 다음봉수익률=rc["ret"] * 100)
            st.caption("최근 표본 외 예측 기록")
            st.dataframe(rc[["상승확률", "예측", "실제", "적중", "다음봉수익률"]], width="stretch", column_config={
                "상승확률": st.column_config.NumberColumn(format="%.1f%%"),
                "다음봉수익률": st.column_config.NumberColumn(format="%+.3f%%")})
    with t4:
        rows = [("시가총액", fmt_big(inf.get("marketCap"))), ("유통주식", f"{inf['floatShares'] / 1e6:,.0f}M" if inf.get("floatShares") else "-"),
                ("공매도 비율 (유통 대비)", pct(inf.get("shortPercentOfFloat"), 1).lstrip("+")),
                ("숏 커버 일수", num(inf.get("shortRatio"), 1)), ("평균 거래량 (10일)", f"{inf['averageVolume10days'] / 1e6:,.1f}M" if inf.get("averageVolume10days") else "-"),
                ("베타 (Yahoo)", num(inf.get("beta"))), ("52주 고가", num(inf.get("fiftyTwoWeekHigh"))), ("52주 저가", num(inf.get("fiftyTwoWeekLow"))),
                ("다음 어닝", f"D-{a['earn_d']}" if a["earn_d"] is not None and a["earn_d"] >= 0 else "-")]
        st.dataframe(pd.DataFrame(rows, columns=["항목", "값"]), hide_index=True, width="stretch")
        st.caption("유통주식이 적고 공매도 비율이 높으면 숏 스퀴즈로 급등할 수 있습니다 (소형 모멘텀 단타에서 중요).")


def page_risk():
    st.title("리스크 관리")
    P = PROFILES[pname]
    st.caption(f"{pname} 규칙: 거래당 계좌의 {P['risk']}% · 하루 최대 {P['max_trades']}회 · 일 손실 {P['daily_loss']}% 도달 시 그날 종료")
    l, r = st.columns(2)
    with l:
        st.subheader("포지션 사이즈")
        risk = st.number_input("거래당 리스크 (%)", 0.05, 5.0, float(P["risk"]), 0.05)
        e = st.number_input("진입가", 0.01, 1e6, 100.0)
        s = st.number_input("손절가", 0.01, 1e6, 98.0)
        t = st.number_input("목표가", 0.01, 1e6, 104.0)
        rps = abs(e - s)
        if rps > 0:
            sh = int(acct * risk / 100 / rps)
            c = st.columns(3)
            c[0].metric("수량", f"{sh:,}주")
            c[1].metric("포지션 금액", f"${sh * e:,.0f}", f"계좌의 {sh * e / acct * 100:.0f}%", delta_color="off")
            c[2].metric("R:R", f"{abs(t - e) / rps:.2f}", f"손실 \\${sh * rps:,.0f} / 이익 \\${sh * abs(t - e):,.0f}", delta_color="off")
            if sh * e > acct:
                st.warning("포지션이 계좌보다 큽니다 — 마진이 필요하거나 손절이 너무 좁습니다.")
        st.caption("손절은 '틀렸다는 게 증명되는 가격'(VWAP·OR 반대편·직전 스윙)에 두고, 수량은 그 거리로 역산합니다 (Van Tharp 고정 비율 사이징).")
    with r:
        st.subheader("켈리 기준 · 기대값")
        js = summarize(enrich(load_journal()))
        if js and js["n"] >= 10 and ok(js["avg_win"]) and ok(js["avg_loss"]):
            if st.button(f"내 일지 값으로 채우기 ({js['n']}회 · 승률 {js['win'] * 100:.0f}% · {js['avg_win']:.2f}R / {js['avg_loss']:.2f}R)"):
                st.session_state["kelly_wr"] = int(np.clip(round(js["win"] * 100), 20, 80))
                st.session_state["kelly_b"] = float(np.clip(round(js["avg_win"] / abs(js["avg_loss"]), 1), 0.5, 5.0))
                st.rerun()
        st.session_state.setdefault("kelly_wr", 45)
        st.session_state.setdefault("kelly_b", 2.0)
        wr = st.slider("승률 (%)", 20, 80, key="kelly_wr")
        b = st.slider("평균 이익 / 평균 손실 (R)", 0.5, 5.0, step=0.1, key="kelly_b")
        p = wr / 100
        kelly = p - (1 - p) / b
        ev = p * b - (1 - p)
        c = st.columns(3)
        c[0].metric("기대값 (거래당)", f"{ev:+.2f}R")
        c[1].metric("풀 켈리", f"{kelly * 100:.1f}%")
        c[2].metric("권장 (¼ 켈리)", f"{max(kelly, 0) * 25:.2f}%", f"프로필 한도 {P['risk']}%", delta_color="off")
        if ev <= 0:
            st.error("기대값이 음수 — 이 승률·손익비로는 사이즈와 무관하게 장기적으로 잃습니다.")
        n = st.slider("시뮬레이션 거래 수", 50, 500, 200, 50)
        rng = np.random.default_rng(7)
        wins = rng.random((2000, n)) < p
        rets = np.where(wins, b * risk / 100, -risk / 100)
        eq = np.cumprod(1 + rets, axis=1) * acct
        dd = 1 - eq / np.maximum.accumulate(eq, axis=1)
        qs = np.percentile(eq, [5, 25, 50, 75, 95], axis=0)
        fig = go.Figure()
        xs = np.arange(1, n + 1)
        fig.add_scatter(x=xs, y=qs[4], line=dict(width=0), showlegend=False)
        fig.add_scatter(x=xs, y=qs[0], fill="tonexty", fillcolor="rgba(37,99,235,0.12)", line=dict(width=0), name="5–95%")
        fig.add_scatter(x=xs, y=qs[3], line=dict(width=0), showlegend=False)
        fig.add_scatter(x=xs, y=qs[1], fill="tonexty", fillcolor="rgba(37,99,235,0.25)", line=dict(width=0), name="25–75%")
        fig.add_scatter(x=xs, y=qs[2], line=dict(color="#1d4ed8", width=2), name="중앙값")
        fig.update_layout(title=f"몬테카를로 2,000회 · 리스크 {risk}%", xaxis_title="거래 수", yaxis_title="계좌 ($)")
        st.plotly_chart(base_layout(fig, 320), width="stretch")
        st.caption(f"최대 낙폭 20% 이상 겪을 확률 {(dd.max(axis=1) >= 0.2).mean() * 100:.0f}% · 중앙값 최종 계좌 ${np.median(eq[:, -1]):,.0f}")
    st.subheader("단타 운영 규칙")
    st.markdown(f"""
- **일 손실 한도 {P['daily_loss']}%** (약 ${acct * P['daily_loss'] / 100:,.0f}) 도달 → 그날 종료. 3연속 손절도 종료. 복수 매매가 계좌를 망가뜨리는 1순위 원인입니다.
- **1차 목표에서 절반 익절 + 손절을 본전으로** 이동 — 승률이 낮은 돌파 전략을 견딜 수 있게 만듭니다.
- **물타기 금지**. 손절가를 내리지 않습니다. 진입 전 손절·목표·수량이 정해져 있지 않으면 진입하지 않습니다.
- **PDT 규정**: 미국 브로커 마진 계좌는 패턴 데이트레이더 규정 적용 여부를 확인하세요. DEGIRO 같은 EU 브로커는 FINRA PDT 대상이 아니지만 자체 마진·주문 규칙이 있습니다.
- Paul Tudor Jones: "5:1 손익비면 승률 20%로도 바보가 아닐 수 있다" — 승률보다 손익비와 손실 통제.
""")


def page_guide():
    st.title("전략 가이드")
    st.markdown("""
앱의 신호가 어디서 왔는지, 언제 통하고 언제 안 통하는지 정리했습니다. 대형 IB·HFT의 '비밀 전략'은 공개되지 않지만,
**그들이 시장에 남기는 흔적**(VWAP 집행, 옵션 헤지, 리밸런싱)은 개인도 읽을 수 있고 이 앱은 그걸 레벨로 보여줍니다.
""")
    st.subheader("앱에 들어간 셋업")
    st.dataframe(pd.DataFrame([
        ("ORB 돌파", "Toby Crabel (1990)", "첫 5/15/30분 고저 돌파 + VWAP 같은 쪽 + RVOL", "추세일, 갭 후 오전", "횡보일 가짜 돌파", "공격 · 밸런스"),
        ("VWAP 리클레임/이탈", "기관 집행 벤치마크 (GS·MS 알고)", "VWAP 아래→위 마감 + 거래량", "기관 매수 유입일", "VWAP 주변 지그재그", "전체"),
        ("9EMA 눌림목", "Qullamaggie · Mark Minervini", "VWAP 위, 9>20 EMA, 9EMA 터치 후 양봉", "강한 추세주", "추세 말기, ATR 소진 후", "전체"),
        ("갭 앤 고", "Ross Cameron", "갭 ±3%+, RVOL 2x+, 프리마켓 고점 위", "뉴스·실적 촉매 소형/중형주", "촉매 없는 갭, 오후", "공격"),
        ("갭 메우기", "갭 필 통계", "시가·VWAP 아래로 밀리면 전일 종가 목표", "촉매 약한 갭", "실적 갭 (안 메움)", "공격 · 밸런스"),
        ("VWAP ±2σ 페이드", "Larry Connors · 마켓메이커", "2σ 밖 + RSI 극단 → VWAP 회귀", "감마 플러스, 횡보장", "추세일 (밴드 타고 감)", "공격 · 밸런스"),
        ("스퀴즈 발사", "John Carter (TTM Squeeze)", "볼린저가 켈트너 밖으로 + 모멘텀 방향", "압축 후 확장", "이벤트 전 가짜 발사", "공격 · 밸런스"),
        ("전일 고저 돌파", "Linda Raschke", "PDH/PDL 돌파 + VWAP 같은 쪽", "추세 연장일", "레인지 데이", "전체"),
    ], columns=["셋업", "출처", "앱 규칙", "잘 통할 때", "실패할 때", "프로필"]), hide_index=True, width="stretch")

    st.subheader("앱이 쓰는 분석 도구")
    st.dataframe(pd.DataFrame([
        ("시장 점수", "SPY·QQQ VWAP/EMA, 일봉 20·50MA, VIX 수준·기간구조, 시장 폭, IWM·SMH 참여도", "'오늘 판을 깔아도 되나' — 프로필별 진입/선별/관망"),
        ("상대거래량 (RVOL)", "같은 시각 누적 거래량 vs 20일 평균", "관심이 몰린 종목을 찾는 단타의 1번 필터"),
        ("상대강도 (RS)", "베타 조정 SPY 대비 초과 수익 — 통계적 차익거래(르네상스·투시그마)의 잔차 개념", "시장 약할 때 버티는 리더 찾기"),
        ("VWAP ±σ 밴드", "거래량 가중 평균가와 표준편차", "기관 평균 단가 · 과열/과매도"),
        ("거래량 프로필", "POC·가치영역 (Steidlmayer 마켓 프로파일)", "가격이 '받아들여진' 구간과 자석 레벨"),
        ("딜러 감마 (GEX)", "옵션 OI×감마 — 옵티버·SIG·시타델 시큐리티즈 헤지 흐름 추정", "핀/증폭 환경 판별, 콜월·풋월 레벨"),
        ("내재 무브", "ATM 스트래들 가격", "옵션 시장이 반영한 기대 변동폭 — 목표가 현실성 체크"),
        ("다음 5분 모델", "로지스틱 회귀 + 워크포워드 검증, 장중 U자 변동성 보정 범위", "방향 확률(약한 신호) + 손절/목표용 범위(강한 신호)"),
        ("거래량 델타 (추정)", "봉 안 종가 위치 × 거래량", "매수/매도 우위 근사 — 진짜 체결 방향(틱 데이터)은 아님"),
        ("포지션 사이징", "고정 비율 리스크(Van Tharp), ¼ 켈리, 몬테카를로", "살아남는 사이즈"),
    ], columns=["도구", "방법", "용도"]), hide_index=True, width="stretch")

    st.subheader("대형 트레이딩 회사가 실제로 하는 일 → 개인이 가져다 쓸 수 있는 것")
    st.markdown("""
- **골드만삭스·모건스탠리 (에이전시·프린시플 데스크)**: 고객 대량 주문을 VWAP/TWAP/IS 알고리즘으로 쪼개 집행. → 그래서 **VWAP이 장중 지지/저항**이 되고,
  월말·분기말 리밸런싱, **15:50 ET MOC 불균형**이 막판 흐름을 만듭니다. 매월 셋째 금요일 옵션 만기(OpEx)·분기 '쿼드러플 위칭'도 이들 흐름이 큰 날.
- **옵티버·SIG·시타델 시큐리티즈·제인 스트리트 (마켓메이커)**: 호가 양쪽을 대고 스프레드를 벌며, 옵션 재고를 델타 헤지. → **딜러 감마**가 장중 변동성의 성격을 바꿉니다.
  이들의 엣지는 속도·재고 관리라 개인이 복제할 수 없지만, 그들이 **어디서 헤지해야 하는지**(콜월·풋월·감마 플립)는 추정할 수 있습니다.
- **르네상스·투시그마·DE 쇼 (퀀트)**: 수천 종목의 작은 통계적 우위를 레버리지로 반복. → 개인 단타에 주는 교훈은 **"한 번의 확신보다 작은 우위 × 많은 횟수 × 엄격한 사이징"**,
  그리고 이 앱의 5분 모델처럼 **표본 외 검증 없이 믿지 말 것**.

**유명 개인 트레이더에게서 가져온 원칙**
- **Toby Crabel**: 오프닝 레인지, NR7(좁은 날 다음은 확장). **Linda Raschke**: 전일 고저, 'Holy Grail' 첫 눌림.
- **Brian Shannon**: 앵커드 VWAP·멀티 타임프레임 (차트의 AVWAP). **Al Brooks**: 가격 행동, 실패한 돌파의 반대 매매.
- **Ross Cameron**: 갭·RVOL·유통주식 적은 소형주 모멘텀 (종목 정보 탭의 유통주식·공매도 비율).
- **Qullamaggie·Minervini**: 리더 종목의 눌림·VCP — 상대강도 리더 + 9EMA 눌림.
- **Mark Douglas·Brett Steenbarger**: 매매 일지와 심리 — 규칙 위반을 기록하고 줄이는 게 엣지.
""")
    st.subheader("이 앱의 한계 (꼭 알아둘 것)")
    st.markdown("""
- Yahoo 데이터는 **실시간이 아닐 수 있고**(종목에 따라 지연), 호가창(L2)·체결 테이프(Time & Sales)·$TICK/$ADD 같은 시장 내부 지표가 없습니다.
  진지하게 단타를 한다면 브로커(IBKR 등)나 Polygon·Databento 같은 유료 실시간 데이터로 바꾸는 것을 권합니다.
- 셋업 감지는 규칙 기반 체크일 뿐 **승률이 검증된 시스템이 아닙니다**. 본인 매매 기록으로 셋업별 성과를 쌓아 확인하세요.
""")


def page_journal():
    st.title("매매 일지")
    j = enrich(load_journal())
    ts = today_status(j, acct, pname)
    msg = (f"오늘 {ts['n']}/{ts['max_trades']}회 · {ts['R']:+.1f}R · \\${ts['pnl']:+,.0f} "
           f"(일 손실 한도 {ts['limit']}% = \\${acct * ts['limit'] / 100:,.0f})")
    if ts["stop"]:
        st.error(f"오늘 한도에 도달했습니다. 매매를 종료하세요. {msg}")
    elif ts["streak"]:
        st.warning(f"3연속 손실 — 쉬고 오세요. {msg}")
    else:
        st.caption(msg)
    st.caption(f"저장 위치: `{JOURNAL}` (내 컴퓨터에만 저장되고 git에는 올라가지 않습니다)")

    t_add, t_list, t_stats, t_pat, t_exc = st.tabs(["기록하기", "기록 목록", "셋업별 통계", "패턴 분석", "손절·목표 점검 (MAE/MFE)"])
    with t_add:
        if msg_ := st.session_state.pop("_j_msg", None):
            st.success(msg_)
        pf = st.session_state.get("_prefill") or {}
        if pf:
            st.info(f"종목 분석에서 가져온 플랜: {pf['sym']} · {SETUP_NAMES.get(pf['setup'], pf['setup'])} · {pf['side']}")
        with st.form("add_trade", clear_on_submit=True):
            c = st.columns(5)
            d = c[0].date_input("날짜 (미국 거래일)", now_ny().date())
            sym = c[1].text_input("티커", pf.get("sym", "")).strip().upper()
            side = c[2].radio("방향", ["롱", "숏"], index=1 if pf.get("side") == "숏" else 0, horizontal=True)
            names = list(SETUP_NAMES.values())
            setup = c[3].selectbox("셋업", names, index=names.index(SETUP_NAMES.get(pf.get("setup"), "기타·즉흥")))
            prof = c[4].selectbox("프로필", list(PROFILES), index=list(PROFILES).index(pf.get("profile", pname)))
            c = st.columns(6)
            t_in = c[0].time_input("진입 시각 (암스테르담)", None, step=60)
            t_out = c[1].time_input("청산 시각 (암스테르담)", None, step=60)
            entry = c[2].number_input("진입가", 0.0, value=pf.get("entry"), format="%.2f", step=0.01)
            stop = c[3].number_input("손절가", 0.0, value=pf.get("stop"), format="%.2f", step=0.01)
            target = c[4].number_input("목표가", 0.0, value=pf.get("t1"), format="%.2f", step=0.01)
            exit_ = c[5].number_input("청산가 (보유 중이면 비움)", 0.0, value=None, format="%.2f", step=0.01)
            c = st.columns([1, 1, 1, 3])
            shares = c[0].number_input("수량", 0, value=int(pf.get("shares", 0)), step=1)
            fees = c[1].number_input("수수료 ($)", 0.0, value=1.0, step=0.5)
            rule_ok = c[2].checkbox("계획대로 매매함", value=True)
            mistakes = c[3].multiselect("실수 태그", MISTAKES)
            notes = st.text_area("메모 (왜 들어갔나, 무엇을 배웠나)", height=80)
            sub = st.form_submit_button("기록 저장", type="primary")
        if sub:
            sg = -1 if side == "숏" else 1
            err = [m for m, bad in (("티커를 입력하세요", not sym), ("진입가·손절가를 입력하세요", not entry or not stop),
                                    ("수량을 입력하세요", shares <= 0),
                                    ("손절가가 진입가의 반대편이어야 합니다", bool(entry and stop) and (entry - stop) * sg <= 0)) if bad]
            if err:
                st.error(" · ".join(err))
            else:
                to_et = lambda t: dt.datetime.combine(d, t, AMS).astimezone(NY).strftime("%H:%M") if t else ""
                row = dict(id=uuid.uuid4().hex[:8], date=d, sym=sym, side=side, setup=SETUP_KEYS[setup], profile=prof,
                           entry_time=to_et(t_in), exit_time=to_et(t_out), entry=entry, stop=stop, target=target,
                           exit=exit_, shares=shares, fees=fees, rule_ok=rule_ok, mistakes=";".join(mistakes), notes=notes)
                save_journal(pd.concat([load_journal(), pd.DataFrame([row])], ignore_index=True))
                st.session_state.pop("_prefill", None)
                r = (exit_ - entry) * sg / abs(entry - stop) if exit_ else None
                st.session_state["_j_msg"] = f"저장됨: {sym} {side} {setup}" + (f" → {r:+.2f}R" if r is not None else " (보유 중)")
                st.rerun()

    with t_list:
        if j.empty:
            st.info("아직 기록이 없습니다. '기록하기' 탭에서 첫 매매를 남겨보세요.")
        else:
            ed = j[J_COLS + ["R", "pnl"]].copy()
            ed["setup"] = ed["setup"].map(SETUP_NAMES).fillna("기타·즉흥")
            ed = ed.iloc[::-1].reset_index(drop=True)
            new = st.data_editor(ed, hide_index=True, width="stretch", num_rows="dynamic", key="j_editor",
                                 disabled=["id", "R", "pnl"], column_order=[c for c in ed.columns if c != "id"], column_config={
                    "date": st.column_config.DateColumn("날짜"), "sym": "티커",
                    "side": st.column_config.SelectboxColumn("방향", options=["롱", "숏"]),
                    "setup": st.column_config.SelectboxColumn("셋업", options=list(SETUP_NAMES.values())),
                    "profile": st.column_config.SelectboxColumn("프로필", options=list(PROFILES)),
                    "entry_time": "진입(ET)", "exit_time": "청산(ET)",
                    "entry": st.column_config.NumberColumn("진입", format="%.2f"), "stop": st.column_config.NumberColumn("손절", format="%.2f"),
                    "target": st.column_config.NumberColumn("목표", format="%.2f"), "exit": st.column_config.NumberColumn("청산", format="%.2f"),
                    "shares": st.column_config.NumberColumn("수량", format="%d"), "fees": st.column_config.NumberColumn("수수료", format="%.2f"),
                    "rule_ok": st.column_config.CheckboxColumn("계획대로"), "mistakes": "실수 (;로 구분)", "notes": "메모",
                    "R": st.column_config.NumberColumn("R", format="%+.2f"), "pnl": st.column_config.NumberColumn("손익($)", format="%+.0f")})
            c = st.columns([1, 1, 4])
            if c[0].button("변경 저장", type="primary"):
                new = new.dropna(subset=["date"]).copy()
                new["setup"] = new["setup"].map(SETUP_KEYS).fillna("OTHER")
                new = new.fillna({k: "" for k in J_TXT})
                save_journal(new.iloc[::-1])
                st.success("저장했습니다.")
                st.rerun()
            c[1].download_button("CSV 내보내기", j[J_COLS].to_csv(index=False).encode("utf-8-sig"), "trades.csv", "text/csv")
            c[2].caption("셀을 고치거나 행을 선택해 삭제한 뒤 '변경 저장'을 누르세요. 시각은 뉴욕(ET) 기준입니다. R·손익은 저장 후 다시 계산됩니다.")

    closed = j[j["closed"]]
    with t_stats:
        if closed.empty:
            st.info("청산된 매매가 생기면 통계가 나옵니다.")
        else:
            c = st.columns([1, 1, 2])
            rng = c[0].selectbox("기간", ["전체", "최근 30일", "최근 90일"], key="j_rng")
            pf_ = c[1].selectbox("프로필", ["전체"] + list(PROFILES), key="j_prof")
            g = closed
            if rng != "전체":
                g = g[g["date"] >= now_ny().date() - dt.timedelta(days=30 if rng == "최근 30일" else 90)]
            if pf_ != "전체":
                g = g[g["profile"] == pf_]
            s_ = summarize(g)
            if not s_:
                st.info("조건에 맞는 매매가 없습니다.")
            else:
                m = st.columns(8)
                m[0].metric("거래", f"{s_['n']}회")
                m[1].metric("승률", f"{s_['win'] * 100:.0f}%", f"95% {s_['lo'] * 100:.0f}–{s_['hi'] * 100:.0f}%", delta_color="off")
                m[2].metric("기대값", f"{s_['exp']:+.2f}R", f"±{s_['ci']:.2f}R" if ok(s_["ci"]) else None, delta_color="off")
                m[3].metric("손익비", f"{s_['avg_win']:+.2f} / {s_['avg_loss']:+.2f}" if ok(s_["avg_win"]) and ok(s_["avg_loss"]) else "-")
                m[4].metric("PF", f"{s_['pf']:.2f}" if np.isfinite(s_["pf"]) else "∞")
                m[5].metric("누적", f"{s_['totR']:+.1f}R", usd(s_["pnl"]), delta_color="normal")
                m[6].metric("최대 낙폭", f"{s_['mdd']:.1f}R", f"최장 연패 {s_['streak']}회", delta_color="off")
                m[7].metric("SQN", num(s_["sqn"], 1), help="Van Tharp 시스템 품질 지수: 기대값/표준편차×√N(최대 100). 1.6↓ 약함, 2–3 좋음, 3↑ 우수")
                st.caption(f"기대값 = 거래 1회당 평균 R. 표본이 {MIN_N}회 미만인 셋업은 운인지 실력인지 아직 구분할 수 없습니다.")

                l, r = st.columns([3, 2])
                with l:
                    g2 = g.reset_index(drop=True)
                    fig = make_subplots(specs=[[{"secondary_y": True}]])
                    fig.add_scatter(x=g2.index + 1, y=g2["R"].cumsum(), name="누적 R", line=dict(color="#2563eb", width=2),
                                    customdata=np.c_[g2["sym"], g2["setup_name"], g2["R"]],
                                    hovertemplate="#%{x} %{customdata[0]} · %{customdata[1]}<br>%{customdata[2]:+.2f}R · 누적 %{y:+.1f}R<extra></extra>")
                    fig.add_scatter(x=g2.index + 1, y=g2["pnl"].cumsum(), name="누적 손익($)", line=dict(color="#94a3b8", width=1, dash="dot"),
                                    secondary_y=True, hovertemplate="누적 $%{y:,.0f}<extra></extra>")
                    fig.update_layout(title="자산 곡선", xaxis_title="거래 #")
                    st.plotly_chart(base_layout(fig, 340), width="stretch")
                with r:
                    fig = go.Figure(go.Histogram(x=g["R"], xbins=dict(size=0.5), marker_color="#64748b"))
                    fig.add_vline(x=0, line=dict(color="#111827"))
                    fig.add_vline(x=s_["exp"], line=dict(color="#2563eb", dash="dash"), annotation_text=f"평균 {s_['exp']:+.2f}R")
                    fig.update_layout(title="R 분포", xaxis_title="R", bargap=0.05)
                    st.plotly_chart(base_layout(fig, 340), width="stretch")

                st.subheader("셋업별 성과")
                tb = group_stats(g, "setup_name", "셋업")
                st.dataframe(tb, hide_index=True, width="stretch", column_config=STAT_COLS)
                ex = []
                for k, gg in g.groupby("setup_name"):
                    ss = summarize(gg)
                    ex.append((k, ss["exp"], ss["ci"] if ok(ss["ci"]) else 0, ss["n"]))
                ex.sort(key=lambda x: x[1])
                fig = go.Figure(go.Bar(x=[e[1] for e in ex], y=[f"{e[0]} ({e[3]})" for e in ex], orientation="h",
                                       marker_color=[UP if e[1] > 0 else DOWN for e in ex],
                                       error_x=dict(type="data", array=[e[2] for e in ex], color="#94a3b8")))
                fig.add_vline(x=0, line=dict(color="#111827"))
                fig.update_layout(title="셋업별 기대값 (R) · 막대 = 95% 신뢰구간", xaxis_title="거래당 평균 R")
                st.plotly_chart(base_layout(fig, 60 + 36 * len(ex)), width="stretch")
                st.caption("신뢰구간이 0을 넘어 전부 오른쪽에 있어야 '검증된 엣지'입니다. 0에 걸쳐 있으면 아직 운과 구분되지 않습니다.")

    with t_pat:
        if closed.empty:
            st.info("청산된 매매가 생기면 패턴 분석이 나옵니다.")
        else:
            ins = []
            by_set = [(k, summarize(g)) for k, g in closed.groupby("setup_name")]
            by_set = [(k, x) for k, x in by_set if x["n"] >= 5]
            if by_set:
                best, worst = max(by_set, key=lambda x: x[1]["exp"]), min(by_set, key=lambda x: x[1]["exp"])
                ins.append(f"가장 좋은 셋업: **{best[0]}** ({best[1]['n']}회, {best[1]['exp']:+.2f}R/회)")
                if worst[0] != best[0]:
                    ins.append(f"가장 나쁜 셋업: **{worst[0]}** ({worst[1]['n']}회, {worst[1]['exp']:+.2f}R/회)")
            bad = closed[~closed["rule_ok"]]
            if len(bad):
                ins.append(f"계획을 어긴 매매 {len(bad)}회의 누적 **{bad['R'].sum():+.1f}R** (\\${bad['pnl'].sum():+,.0f}) — "
                           f"계획대로 한 매매는 회당 {closed[closed['rule_ok']]['R'].mean():+.2f}R, 어긴 매매는 {bad['R'].mean():+.2f}R")
            wins = closed[closed["R"] > 0]
            if len(wins) and wins["plan_R"].notna().any():
                ins.append(f"이긴 매매의 평균 실현 {wins['R'].mean():+.2f}R vs 계획 목표 {wins['plan_R'].mean():+.2f}R"
                           + (" — 목표 전에 일찍 청산하는 경향" if wins["R"].mean() < 0.8 * wins["plan_R"].mean() else ""))
            ph = [(k, summarize(g)) for k, g in closed.groupby("phase")]
            ph = [(k, x) for k, x in ph if x["n"] >= 5 and x["exp"] < 0]
            for k, x in ph:
                ins.append(f"**{k}** 구간 매매가 마이너스 ({x['n']}회, {x['exp']:+.2f}R/회) — 이 시간대는 쉬는 것을 고려")
            if ins:
                st.subheader("발견한 패턴")
                st.markdown("\n".join(f"- {t}" for t in ins))
            mx = closed.assign(mistake=closed["mistakes"].str.split(";")).explode("mistake")
            mx = mx[mx["mistake"].fillna("") != ""]
            pairs = [("phase", "장중 구간"), ("weekday", "요일"), ("side", "방향"), ("profile", "프로필"),
                     ("rule_ok", "계획 준수"), ("sym", "종목")]
            for i in range(0, len(pairs), 2):
                cols = st.columns(2)
                for col, (k, lab) in zip(cols, pairs[i:i + 2]):
                    with col:
                        st.markdown(f"**{lab}별**")
                        src = closed.assign(rule_ok=closed["rule_ok"].map({True: "계획대로", False: "계획 위반"})) if k == "rule_ok" else closed
                        tb = group_stats(src, k, lab)
                        if not tb.empty:
                            st.dataframe(tb[[lab, "거래", "승률%", "기대값(R)", "누적 R", "손익($)"]], hide_index=True,
                                         width="stretch", column_config=STAT_COLS)
            if len(mx):
                st.markdown("**실수 태그별 비용**")
                tb = group_stats(mx, "mistake", "실수")
                st.dataframe(tb[["실수", "거래", "승률%", "기대값(R)", "누적 R", "손익($)"]], hide_index=True, width="stretch",
                             column_config=STAT_COLS)

    with t_exc:
        st.markdown("진입부터 청산까지 가격이 **최대로 유리하게(MFE)**, **최대로 불리하게(MAE)** 움직인 폭을 R로 잽니다 "
                    "(John Sweeney의 MAE 분석). 시각이 기록된 최근 58일 매매만 계산합니다.")
        if closed.empty:
            st.info("청산된 매매가 없습니다.")
        elif st.button("계산하기 (Yahoo 분봉 조회)"):
            st.caption("진입가가 그 시각의 실제 거래 범위 밖인 기록(시각·가격 오기)은 제외합니다.")
            with st.spinner("분봉 불러오는 중…"):
                ex = excursions(closed)
            if ex.empty:
                st.info("계산 가능한 매매가 없습니다 (진입·청산 시각과 최근 58일 이내 기록 필요).")
            else:
                fig = go.Figure(go.Scatter(x=ex["MAE(R)"], y=ex["R"], mode="markers",
                                           marker=dict(size=10, color=[UP if r > 0 else DOWN for r in ex["R"]]),
                                           customdata=np.c_[ex["티커"], ex["셋업"], ex["MFE(R)"]],
                                           hovertemplate="%{customdata[0]} · %{customdata[1]}<br>MAE %{x:.2f}R · MFE %{customdata[2]:.2f}R<br>결과 %{y:+.2f}R<extra></extra>"))
                fig.add_vline(x=1, line=dict(color="#94a3b8", dash="dash"), annotation_text="손절선 (1R)")
                fig.add_hline(y=0, line=dict(color="#111827"))
                fig.update_layout(title="MAE vs 결과", xaxis_title="최대 불리 이동 MAE (R)", yaxis_title="결과 (R)")
                st.plotly_chart(base_layout(fig, 380), width="stretch")
                w, lz = ex[ex["R"] > 0], ex[ex["R"] <= 0]
                tips = []
                if len(w):
                    tips.append(f"이긴 매매의 MAE 중앙값 {w['MAE(R)'].median():.2f}R — "
                                + ("손절을 조금 좁혀 사이즈를 키울 여지" if w["MAE(R)"].quantile(0.8) < 0.6 else "현재 손절 폭이 필요한 수준"))
                    tips.append(f"이긴 매매에서 평균 {w['놓친 R'].mean():.2f}R을 남기고 나옴 (MFE − 실현)")
                if len(lz):
                    gave = (lz["MFE(R)"] >= 1).mean()
                    tips.append(f"진 매매의 {gave * 100:.0f}%는 한때 +1R 이상 갔었음"
                                + (" — 1R에서 절반 익절·본전 손절 이동 규칙을 권장" if gave >= 0.25 else ""))
                st.markdown("\n".join(f"- {t}" for t in tips))
                st.dataframe(ex, hide_index=True, width="stretch", column_config={
                    k: st.column_config.NumberColumn(format="%+.2f") for k in ("R", "MFE(R)", "MAE(R)", "놓친 R")})


def page_alerts():
    st.title("알림")
    w = alert_worker()
    st.caption(f"이 앱 서버가 켜져 있는 동안 프리마켓~애프터마켓 60초마다 확인하고 Mac 알림(소리 포함)을 띄웁니다. 브라우저 탭은 닫아도 됩니다. "
               f"마지막 확인: {w.last_run.astimezone(AMS):%H:%M:%S} 암스테르담" if w.last_run else
               "이 앱 서버가 켜져 있는 동안 프리마켓~애프터마켓 60초마다 확인하고 Mac 알림(소리 포함)을 띄웁니다. 브라우저 탭은 닫아도 됩니다. 아직 장중 확인 기록 없음.")
    if w.last_err:
        st.warning(f"최근 오류: {w.last_err}")
    with st.form("add_alert", clear_on_submit=True):
        c = st.columns([1, 1.4, 1, 1.4, 2])
        sym = c[0].text_input("티커", st.session_state.get("sym", "NVDA"))
        kind = c[1].selectbox("조건", list(ALERT_KINDS), format_func=lambda k: f"{k} — {ALERT_KINDS[k][0]}")
        val = c[2].number_input("값 (가격·RVOL)", 0.0, value=None, format="%.2f")
        stp = c[3].selectbox("셋업 (셋업 발생일 때)", ["전체"] + list(SETUP_NAMES)[:-1], format_func=lambda k: SETUP_NAMES.get(k, k))
        note = c[4].text_input("메모", placeholder="예: 눌림 매수 준비")
        if st.form_submit_button("알림 추가", type="primary"):
            if ALERT_KINDS[kind][1] and val is None:
                st.error("이 조건에는 값이 필요합니다.")
            elif not sym.strip():
                st.error("티커를 입력하세요.")
            else:
                add_alert(sym.strip(), kind, val, None if stp == "전체" else stp, note)
                st.rerun()
    al = load_alerts()
    if al:
        df = pd.DataFrame(al)
        df["setup"] = df["setup"].map(lambda k: SETUP_NAMES.get(k, "") if k else "")
        new = st.data_editor(df[["active", "sym", "kind", "value", "setup", "note", "last_fired", "created", "id"]], hide_index=True,
                             width="stretch", num_rows="dynamic", key="al_editor", disabled=["sym", "kind", "setup", "last_fired", "created", "id"],
                             column_order=["active", "sym", "kind", "value", "setup", "note", "last_fired", "created"], column_config={
                "active": st.column_config.CheckboxColumn("켜짐"), "sym": "티커", "kind": "조건",
                "value": st.column_config.NumberColumn("값", format="%.2f"), "setup": "셋업", "note": "메모",
                "last_fired": "마지막 알림일", "created": "생성"})
        c = st.columns([1, 1, 1, 3])
        if c[0].button("변경 저장"):
            keep = {r["id"]: r for r in new.to_dict("records") if r.get("id")}
            with w.lock:
                out = []
                for x in load_alerts():
                    if x["id"] in keep:
                        x.update(active=bool(keep[x["id"]]["active"]), note=keep[x["id"]]["note"] or "",
                                 value=None if pd.isna(keep[x["id"]]["value"]) else float(keep[x["id"]]["value"]))
                        out.append(x)
                save_alerts(out)
            st.rerun()
        if c[1].button("지금 확인"):
            with st.spinner("확인 중…"):
                f = w.check()
            st.success("발동: " + " / ".join(f)) if f else st.info("발동한 알림 없음 (같은 알림은 하루 한 번만 울립니다)")
        if c[2].button("테스트 알림"):
            notify("테스트", "알림이 이렇게 표시됩니다.")
        c[3].caption("돌파·이탈형은 '반대편에 있다가 넘어올 때' 울리고, 가격·RVOL·셋업형은 조건이 처음 충족될 때 울립니다. 행을 지우고 저장하면 삭제됩니다.")
    else:
        st.info("등록된 알림이 없습니다. 종목 분석 화면의 '알림 걸기'로 핵심 레벨에 바로 걸 수도 있습니다.")
    if w.log:
        st.subheader("최근 발동")
        st.dataframe(pd.DataFrame([dict(시각=f"{x['time'].astimezone(AMS):%m/%d %H:%M}", 티커=x["sym"], 조건=x["kind"], 내용=x["why"])
                                   for x in w.log]), hide_index=True, width="stretch")
    st.caption("텔레그램으로도 받으려면 앱 실행 전에 환경변수 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID를 설정하세요. "
               "Mac 알림이 안 보이면 시스템 설정 → 알림 → '스크립트 편집기'를 허용하세요.")


def page_backtest():
    st.title("셋업 백테스트")
    st.caption("앱의 셋업 감지 규칙을 최근 60거래일 5분봉에 한 봉씩 다시 돌려, 신호가 뜬 봉 다음 봉 시가에 들어갔다면 어땠는지 계산합니다. "
               "같은 셋업·방향은 하루 한 번만, 같은 봉에서 손절과 목표가 모두 닿으면 손절로 칩니다.")
    c = st.columns([3, 1.6, 1, 1, 1])
    cat = c[0].selectbox("빠른 선택", ["직접 고르기"] + list(CATEGORIES), key="bt_cat")
    default = CATEGORIES[cat] if cat != "직접 고르기" else ["NVDA", "AMD", "TSLA", "META", "PLTR"]
    syms = st.multiselect("종목", WATCHLIST, default=[x for x in default if x != "BTC-USD"], key=f"bt_syms_{cat}")
    mode = c[1].selectbox("청산 규칙", list(EXIT_MODES), format_func=EXIT_MODES.get, key="bt_mode")
    align = c[2].toggle("SPY 방향 일치만", value=PROFILES[pname]["align"], key="bt_align",
                        help="SPY가 VWAP 위일 때만 롱, 아래일 때만 숏 (밸런스·방어형 규칙)")
    only_prof = c[3].toggle(f"{pname} 셋업만", value=True, key="bt_prof")
    cost = c[4].number_input("비용 (bp/편도)", 0.0, 50.0, 2.0, 0.5, key="bt_cost", help="수수료+슬리피지. 2bp = 0.02%. 손절이 좁을수록 비용이 R에서 차지하는 비중이 커집니다.")
    if not syms:
        st.info("종목을 고르세요.")
        return
    prog = st.progress(0.0, "계산 중…")
    parts = []
    for i, s_ in enumerate(syms):
        prog.progress((i + 1) / len(syms), f"{s_} 계산 중… ({i + 1}/{len(syms)})")
        parts.append(backtest(s_, or_min, mode, align, cost))
    prog.empty()
    t = pd.concat([x for x in parts if len(x)], ignore_index=True) if any(len(x) for x in parts) else pd.DataFrame()
    if t.empty:
        st.info("신호가 없습니다.")
        return
    if only_prof:
        t = t[t["setup"].isin(PROFILE_SETUPS[pname])]
    if not shorts:
        t = t[t["side"] == "롱"]
    if t.empty:
        st.info("필터 후 남은 거래가 없습니다.")
        return
    risk_usd = acct * PROFILES[pname]["risk"] / 100
    t = t.assign(closed=True, pnl=t["R"] * risk_usd).sort_values(["date", "time"]).reset_index(drop=True)
    s_ = summarize(t)
    m = st.columns(7)
    m[0].metric("거래", f"{s_['n']}회", f"{t['date'].nunique()}일 · {t['sym'].nunique()}종목", delta_color="off")
    m[1].metric("승률", f"{s_['win'] * 100:.0f}%", f"95% {s_['lo'] * 100:.0f}–{s_['hi'] * 100:.0f}%", delta_color="off")
    m[2].metric("기대값", f"{s_['exp']:+.2f}R", f"±{s_['ci']:.2f}R" if ok(s_["ci"]) else None, delta_color="off")
    m[3].metric("PF", f"{s_['pf']:.2f}" if np.isfinite(s_["pf"]) else "∞")
    m[4].metric("누적", f"{s_['totR']:+.1f}R", f"{usd(s_['pnl'])} (회당 리스크 \\${risk_usd:,.0f})", delta_color="normal")
    m[5].metric("최대 낙폭", f"{s_['mdd']:.1f}R", f"최장 연패 {s_['streak']}", delta_color="off")
    m[6].metric("SQN", num(s_["sqn"], 1))

    st.subheader("셋업별")
    tb = group_stats(t, "setup_name", "셋업")
    st.dataframe(tb, hide_index=True, width="stretch", column_config=STAT_COLS)
    l, r = st.columns([3, 2])
    with l:
        fig = go.Figure()
        for k, g in t.groupby("setup_name"):
            fig.add_scatter(x=g.index + 1, y=g["R"].cumsum(), name=k, mode="lines", line=dict(width=1.3))
        fig.add_scatter(x=t.index + 1, y=t["R"].cumsum(), name="전체", line=dict(color="#111827", width=2.5))
        fig.update_layout(title="누적 R (시간순)", xaxis_title="거래 #")
        st.plotly_chart(base_layout(fig, 380), width="stretch")
    with r:
        j = enrich(load_journal())
        jc = j[j["closed"]]
        rows = []
        for k, g in t.groupby("setup"):
            bs, js = summarize(g), summarize(jc[jc["setup"] == k])
            rows.append({"셋업": SETUP_NAMES[k], "백테스트 기대값": bs["exp"], "백테스트 n": bs["n"],
                         "내 실제 기대값": js["exp"] if js else None, "내 실제 n": js["n"] if js else 0})
        st.markdown("**백테스트 vs 내 실제 매매**")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
            "백테스트 기대값": st.column_config.NumberColumn(format="%+.2f"), "내 실제 기대값": st.column_config.NumberColumn(format="%+.2f")})
        st.caption("백테스트는 좋은데 내 실제가 나쁘면 실행(진입 타이밍·손절 준수) 문제, 둘 다 나쁘면 셋업 자체를 빼는 게 맞습니다.")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**종목별**")
        tb = group_stats(t, "sym", "종목")
        st.dataframe(tb[["종목", "거래", "승률%", "기대값(R)", "누적 R", "판정"]], hide_index=True, width="stretch", column_config=STAT_COLS)
    with c2:
        st.markdown("**장중 구간별**")
        tb = group_stats(t, "phase", "구간")
        st.dataframe(tb[["구간", "거래", "승률%", "기대값(R)", "누적 R", "판정"]], hide_index=True, width="stretch", column_config=STAT_COLS)
    with st.expander(f"전체 거래 {len(t)}건"):
        st.dataframe(t[["date", "time", "sym", "setup_name", "side", "entry", "stop", "t1", "R", "bars", "phase"]].iloc[::-1],
                     hide_index=True, width="stretch", column_config={
                "entry": st.column_config.NumberColumn(format="%.2f"), "stop": st.column_config.NumberColumn(format="%.2f"),
                "t1": st.column_config.NumberColumn(format="%.2f"), "R": st.column_config.NumberColumn(format="%+.2f")})
    st.warning("한계: 60일은 짧고(한 가지 장세만 반영될 수 있음), Yahoo 5분봉에는 봉 안의 순서가 없으며, 같은 규칙을 이 데이터로 다듬으면 과최적화됩니다. "
               "판정은 거래 20회 이상 + 95% 구간이 0 위일 때만 믿으세요.")


def _page(fn, live: bool):
    def run():
        if fn is page_ticker and (nxt := st.session_state.pop("_sym_next", None)):
            st.session_state["sym"] = nxt if nxt in WATCHLIST else st.session_state.get("sym", "NVDA")
            st.session_state["custom"] = "" if nxt in WATCHLIST else nxt
        with st.spinner("불러오는 중…"):
            (st.fragment(run_every=60)(fn) if live and auto else fn)()
    run.__name__ = fn.__name__
    return run


PAGES = {t: st.Page(_page(f, live), title=t, url_path=u, default=u == "market") for t, u, f, live in [
    ("시장", "market", page_market, True), ("스캐너", "scanner", page_scanner, True), ("종목 분석", "stock", page_ticker, True),
    ("매매 일지", "journal", page_journal, False), ("백테스트", "backtest", page_backtest, False), ("알림", "alerts", page_alerts, False),
    ("리스크", "risk", page_risk, False), ("가이드", "guide", page_guide, False)]}
nav = st.navigation(list(PAGES.values()), position="top")
if p := st.session_state.pop("_goto_page", None):
    st.switch_page(PAGES[p])
if goto := st.session_state.pop("_goto", None):
    st.session_state.pop("scan_tbl", None)
    st.session_state.pop("mov_tbl", None)
    st.session_state["_sym_next"] = goto
    st.switch_page(PAGES["종목 분석"])
nav.run()
