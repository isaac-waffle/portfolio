"""가상 트레이더 7명 — 미국 정규장 실시간 모의 매매 (롱만).
  아이작·티모·차차·돌쇠  일봉 스윙 ($20,000씩, 매수 규칙이 제각각)
  알파  Alpaca 모의계좌에 넣은 퀀트 데스크(paper_desk.py)와 같은 배분 ($20,000, 소수점 주식, 수수료 $0)
  번개  10분마다 판단하는 단타 ($20,000, 15:50 ET 전량 정리)
  모나  MRNA 한 종목 전문 단타 ($20,000, 몇 초마다 가격 확인, 15:50 ET 전량 정리)

실행:  .venv/bin/python apps/traders.py [--open]      (localhost:8503, 허브에서 켜도 됨)
규칙 기반으로 판단하고 판단 근거를 한국어 '생각'으로 남김. 켜 두는 동안 맥이 잠들지 않게 caffeinate를 붙임.

기록 (git 제외):  apps/journal/traders/
  state.json            현재 계좌·포지션 (재시작해도 이어감)
  trades.csv            청산된 거래 + 진입 당시 지표·근거 (개선 분석용)
  days.csv              트레이더별 하루 결과
  events/YYYY-MM-DD.jsonl  그날의 모든 생각·매매·계획·회고
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import errno
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
import warnings
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import yfinance as yf

import traders_news as news_mod


class _MemTzCache:
    """yfinance 시간대 캐시를 메모리로 — 기본 캐시(SQLite)는 download(threads=True)의 스레드마다 연결을 열고 안 닫아서
    며칠 켜 두면 열린 파일 한도(256)를 넘겨 서버가 멈췄음 (2026-11-02)."""
    tz_db = None

    def __init__(self):
        self.d = {}

    def lookup(self, tkr):
        return self.d.get(tkr)

    def store(self, tkr, tz):
        self.d[tkr] = tz


yf.cache._TzCacheManager._tz_cache = _MemTzCache()

warnings.filterwarnings("ignore")

NY, AMS = ZoneInfo("America/New_York"), ZoneInfo("Europe/Amsterdam")
APPS = Path(__file__).resolve().parent
DATA = APPS / "journal" / "traders"
PAGE = APPS / "traders.html"
PORT = 8503
RULES = "v9"  # 규칙을 바꾸면 올릴 것 — 거래 기록에 남아 버전별 성과 비교 가능
# v3 (2026-10-26): 실시간 뉴스 반영 (traders_news.py) — 아직 백테스트로 검증 안 됨
# v4 (2026-10-26): 2024.01~2026.10 Alpaca 5분봉으로 v1에서 재튜닝 (학습 24.01~25.06 · 확인 25.07~12 · 최종 시험 26.01~10).
#     세 명 모두 v1·v2보다 덜 잃지만 여전히 손실 — 주로 거래를 크게 줄여서 좋아진 것 (lab/tune_2026-10-26_2024-01-02.json)
# v5 (2026-10-26): 일봉 추세 필터·과거 뉴스 8.1만 건으로 재튜닝 — 아이작만 소폭 변경(VWAP 회복 셋업 제외, 악재 뉴스 기준 −0.2),
#     티모·차차는 통과한 변경 없음. 돌쇠은 10년(2016~2026) 일봉 검증 후 트레일링 2.5→3.5 ATR (swing_lab.py long)
# v6 (2026-10-26): 단타에 우위가 없어 아이작=모멘텀(매월 6개월 상위 5), 티모=돌파 8종목, 차차=돌파+SPY 50일선 필터로 전환
# v7 (2026-10-28): 셋이 같은 돌파 규칙이라 같이 멈춰 있어서 매수 규칙을 나눔 (swing_lab.py trio, 2016~2026 나스닥100)
#     티모=RSI(2) 과매도 눌림목(손절 없이 5종목 균등, 5일선 회복·5거래일 청산), 차차=20일선 지지 반등(변동성 낮은 순, SPY 200일선 필터)
# v8 (2026-11-14): 알파(Alpaca 모의계좌 퀀트 데스크와 같은 배분)·번개(10분 단타, scalp_lab.py) 추가. 돌쇠는 후보를 관심종목+나스닥100으로 넓힘
#     (swing_lab.py v8: 학습·확인 둘 다 좋아진 유일한 변경). 아이작·티모·차차는 후보 272개 중 통과한 변경이 없어 그대로.
# v9 (2026-11-15): 모나(MRNA 전문 단타, mrna_lab.py) 추가 — 검증을 통과한 규칙은 아님 (2,300여 개 중 통과 없음).
#     일봉 추세에 따라 추세 추종 / 지정가 받아먹기를 바꿔 쓰고, 지정가·익절·손절은 Alpaca 실시간 체결가로 몇 초마다 확인.
# v2 (2026-10-26): traders_lab 튜닝 (9월 학습 · 10월 확인 · 8월 최종 시험) — 아이작·티모만 변경, 차차는 8월에서 나빠져 유지
START_CASH = 20_000.0
FEE = 2.0  # 주문당 수수료 $ (매수 $2, 매도 $2)
SLIP = 0.0005  # 편도 슬리피지 0.05%
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
}
CAT_OF = {s: c for c, ss in CATEGORIES.items() for s in ss}
WATCHLIST = [s for ss in CATEGORIES.values() for s in ss]
# 나스닥100 (2026년 구성 근사) — 관심종목처럼 '오를 걸 알고 고른' 목록이 아닌 넓은 후보군 (돌쇠·퀀트 데스크·백테스트 공용)
NDX = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "AVGO", "TSLA", "COST", "NFLX", "AMD", "PEP", "ADBE", "CSCO", "TMUS",
       "INTC", "QCOM", "TXN", "AMGN", "INTU", "AMAT", "ISRG", "HON", "BKNG", "CMCSA", "LRCX", "VRTX", "ADP", "MU", "PANW",
       "ADI", "SBUX", "GILD", "MELI", "REGN", "KLAC", "MDLZ", "SNPS", "CDNS", "PYPL", "CRWD", "ASML", "MAR", "CTAS", "ORLY",
       "CSX", "MRVL", "ABNB", "WDAY", "FTNT", "ROP", "NXPI", "ADSK", "PCAR", "CHTR", "CPRT", "MNST", "PAYX", "AEP", "ROST",
       "KDP", "ODFL", "FAST", "EA", "DDOG", "KHC", "VRSK", "BKR", "EXC", "CTSH", "XEL", "GEHC", "IDXX", "CCEP", "TTWO",
       "LULU", "ON", "CSGP", "ZS", "DXCM", "TEAM", "BIIB", "MDB", "WBD", "GFS", "ARM", "DASH", "PLTR", "APP", "AXON",
       "MSTR", "SHOP", "TRI", "LIN", "AZN", "PDD"]
MARKET = ["SPY", "QQQ", "IWM"]
VIX = "^VIX"

# NYSE 휴장일·조기 마감 (조기 마감은 13:00 ET = 780분)
HOLIDAYS = {dt.date(2026, 11, 26), dt.date(2026, 12, 25), dt.date(2027, 1, 1), dt.date(2027, 1, 18),
            dt.date(2027, 2, 15), dt.date(2027, 3, 26), dt.date(2027, 5, 31), dt.date(2027, 6, 18),
            dt.date(2027, 7, 5), dt.date(2027, 9, 6), dt.date(2027, 11, 25), dt.date(2027, 12, 24)}
EARLY = {dt.date(2026, 11, 27): 780, dt.date(2026, 12, 24): 780, dt.date(2027, 11, 26): 780}

SETUP_KO = {"MTREND": "MRNA 추세 추종", "MFADE": "MRNA 지정가 받아먹기", "DESK": "퀀트 데스크 배분", "DIP10": "10분 눌림 반등", "MOM": "모멘텀 상위 (스윙)", "BRK": "20일 신고가 돌파 (스윙)", "PB": "RSI(2) 과매도 눌림 (스윙)", "TP": "20일선 지지 반등 (스윙)", "ORB": "오프닝 레인지 돌파", "VWAP": "VWAP 회복", "PULL": "9EMA 눌림목", "GAPGO": "갭 상승 지속",
            "GAPFILL": "갭 메우기 반등", "FADE": "과매도 반등", "SQZ": "스퀴즈 발사", "PDL": "전일 고점 돌파"}

SETUP_OF = {"breakout": "BRK", "pullback": "PB", "trendpull": "TP", "rotation": "MOM", "desk": "DESK"}
K10 = math.sqrt(10 / 390)  # 일봉 ATR% → 10분 변동폭 환산 (scalp_lab.py와 같은 값)
# 분 단위는 ET 자정 기준 (570 = 09:30)
TRADERS = {
    # v6: 단타 셋을 성격에 맞는 스윙으로 전환 (단타 v5 규칙은 traders_lab.DAY_RULES에 보관, 단타 기록은 그대로 남음)
    "agg": dict(kind="swing", mode="rotation", name="공격형", nick="아이작", color="#2a78d6",
                risk=0.0, max_pos=5, max_trades=5, daily_loss=0.0, mom=126, min_dvol=2e7, min_rvol=0, cooldown=0,
                motto="6개월 가장 많이 오른 5종목을 20%씩 사서 매달 갈아탄다. 크게 벌고 크게 흔들린다 (낙폭 −40%대 각오)."),
    "bal": dict(kind="swing", mode="pullback", name="밸런스형", nick="티모", color="#eb6834",
                risk=0.0, max_pos=5, max_trades=5, daily_loss=0.0, rsi=10, max_hold=5, stop_atr=0, trail_atr=0, exit_ma=5,
                mkt="50", min_dvol=2e7, min_rvol=0, cooldown=0,
                motto="상승 추세 종목이 2~3일 급하게 빠지면(RSI(2) 10 미만) 산다. 5일선 위로 반등하면 팔고, 5거래일 안에 안 오르면 정리. 남들이 팔 때 사는 역발상."),
    "def": dict(kind="swing", mode="trendpull", name="방어형", nick="차차", color="#1baf7a",
                risk=0.005, max_pos=5, max_trades=5, daily_loss=0.0, tol=0.01, stop_atr=2.0, trail_atr=3.0, exit_ma=50,
                rank="lowvol", mkt="200", exit_mkt=True, min_dvol=2e7, min_rvol=0, cooldown=0,
                motto="정배열 종목이 20일선까지 내려왔다 다시 오르면 산다. 변동성 낮은 종목부터, 50일선이 깨지거나 시장(SPY 200일선)이 꺾이면 정리."),
    "swing": dict(kind="swing", mode="breakout", name="스윙형", nick="돌쇠", color="#eda100",
                  risk=0.01, max_pos=5, max_trades=5, daily_loss=0.0, n=20, volr=1.5, stop_atr=2.0, trail_atr=3.5, exit_ma=20,
                  min_dvol=2e7, min_rvol=0, cooldown=0, uni="wide",
                  motto="20일 신고가를 거래량과 함께 돌파한 종목을 며칠~몇 주 들고 간다. 손절은 ATR, 수익은 추세가 꺾일 때까지. 관심종목 + 나스닥100에서 고른다."),
    # v8: Alpaca 모의계좌에 넣은 퀀트 데스크(paper_desk.py)를 그대로 따라 하는 가상 계좌 — 같은 신호, 같은 주문 규칙, 수수료 $0.
    #     시작 자금은 다른 트레이더와 같은 $20,000 (사용자 요청) — 비중이 Alpaca 계좌($100,000)와 같게 나오도록 소수점 주식으로 삼
    "desk": dict(kind="swing", mode="desk", name="퀀트형", nick="알파", color="#7a5cd6", fee=0.0,
                 risk=0.0, max_pos=60, max_trades=60, daily_loss=0.0, min_dvol=0, min_rvol=0, cooldown=0,
                 motto="검증을 통과한 다섯 전략(추세추종·변동성 타기팅·12개월 모멘텀·Minervini·갭 피벗)을 20%씩 섞고 남는 돈은 초단기 국채(BIL)에. "
                       "Alpaca 모의계좌에 넣은 퀀트 데스크와 같은 신호·같은 주문 규칙 (지정가 ±2%, 총 보유 95% 이하)."),
    # v8: 10분마다 판단하는 단타 (scalp_lab.py — 학습에서 고르고 확인·시험에서도 플러스였던 유일한 규칙. 한 주에 한 번쯤만 삼)
    "scalp": dict(kind="scalp", mode="scalp", name="단타형", nick="번개", color="#d6336c",
                  risk=0.0, max_pos=1, max_trades=40, daily_loss=0.0, z_in=1.5, z_keep=1.0, rvol=3.0, min_dvol=2e7, min_rvol=0, cooldown=0,
                  motto="10분마다 판단한다. 오늘 오르는 종목이 거래량 3배로 10분 급락하면 사고, 10분 뒤 다시 본다 — 버티면 10분 더, 꺾이면 판다. "
                        "시장(SPY)이 VWAP 위일 때만, 한 번에 한 종목, 15:50 ET 전량 정리."),
    # v9: 모더나 한 종목만 보는 단타 (mrna_lab.py). k·step = 추세 모드, fk·tp·stop·hold = 받아먹기 모드 (단위: 평소 1분 변동폭의 배수, 분)
    "mrna": dict(kind="mrna", mode="mrna", name="MRNA 전문", nick="모나", color="#0aa5a5",
                 risk=0.0, max_pos=1, max_trades=30, daily_loss=0.0, k=1.0, step=15, fk=5.0, tp=6.0, stop=16.0, hold=60,
                 min_dvol=0, min_rvol=0, cooldown=0,
                 motto="모더나 하나만 판다. 50일선 위인 날은 추세 모드 — 평소 변동 범위를 뚫고 올라가면 따라 사고 꺾이면 판다. "
                       "50일선 아래인 날은 받아먹기 모드 — 급락에 지정가를 걸어 두고 반등에 지정가로 판다. 몇 초마다 가격 확인, 계좌 전부, 15:50 ET 전량 정리. "
                       "백테스트 검증을 통과하지 못한 규칙이라 크게 벌 수도 크게 잃을 수도 있다."),
}

LOCK = threading.RLock()
LIVE: dict = {}  # 화면용 스냅샷 (엔진 스레드가 매 틱 갱신)


# ───────────────────────── helpers ─────────────────────────
def now_ny() -> dt.datetime:
    return dt.datetime.now(NY)


def mins(idx) -> np.ndarray:
    return np.asarray(idx.hour * 60 + idx.minute)


def hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def ams(m: int, day: dt.date) -> str:
    return f"{dt.datetime.combine(day, dt.time(m // 60 % 24, m % 60), NY).astimezone(AMS):%H:%M}"


def ok(x) -> bool:
    return x is not None and not (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))


def f2(x) -> str:
    return f"{x:,.2f}"


def money(x: float) -> str:
    return f"{'-' if x < 0 else '+'}${abs(x):,.0f}"


def pc(x: float, d=1) -> str:
    return f"{x * 100:+.{d}f}%"


def is_trading_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def close_min(d: dt.date) -> int:
    return EARLY.get(d, 960)


def next_open(now: dt.datetime) -> dt.datetime:
    d = now.date()
    if now.hour * 60 + now.minute >= 570:
        d += dt.timedelta(days=1)
    while not is_trading_day(d):
        d += dt.timedelta(days=1)
    return dt.datetime.combine(d, dt.time(9, 30), NY)


def rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def true_range(df: pd.DataFrame) -> pd.Series:
    p = df["Close"].shift()
    return pd.concat([df["High"] - df["Low"], (df["High"] - p).abs(), (df["Low"] - p).abs()], axis=1).max(axis=1)


def vwap_bands(S: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    tp = (S["High"] + S["Low"] + S["Close"]) / 3
    v = S["Volume"].clip(lower=0)
    if v.sum() == 0:
        v = pd.Series(1.0, index=S.index)
    cv = v.cumsum().replace(0, np.nan)
    vw = (tp * v).cumsum() / cv
    var = (tp * tp * v).cumsum() / cv - vw ** 2
    return vw.ffill().bfill(), np.sqrt(var.clip(lower=0)).ffill().fillna(0)


def _pick(d: pd.DataFrame, s: str) -> pd.DataFrame | None:
    if d is None or d.empty:
        return None
    if isinstance(d.columns, pd.MultiIndex):
        if s not in d.columns.get_level_values(0):
            return None
        d = d[s]
    df = d[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
    df = df[df["Close"] > 0]
    return df.fillna({"Volume": 0}) if len(df) else None


# ───────────────────────── data ─────────────────────────
def fetch_1m(tickers: list[str]) -> dict:
    d = yf.download(tickers, period="5d", interval="1m", prepost=True, progress=False,
                    group_by="ticker", auto_adjust=True, threads=True)
    out = {}
    for s in tickers:
        df = _pick(d, s)
        if df is not None:
            df.index = df.index.tz_convert(NY) if df.index.tz is not None else df.index.tz_localize("UTC").tz_convert(NY)
            out[s] = df
    return out


def fetch_daily(tickers: list[str]) -> dict:
    d = yf.download(tickers, period="1y", interval="1d", progress=False, group_by="ticker",
                    auto_adjust=True, threads=True)
    out = {}
    for s in tickers:
        df = _pick(d, s)
        if df is not None:
            df.index = pd.DatetimeIndex([pd.Timestamp(x).date() for x in df.index])
            out[s] = df
    return out


def fetch_vprof(tickers: list[str], today: dt.date) -> dict:
    """종목별 '평소 이 시각 5분 거래량' — 오늘 이전 14거래일의 5분봉 거래량을 시각별(09:30부터 78칸)로 평균. 7일 미만이면 뺌."""
    d = yf.download(tickers, period="1mo", interval="5m", progress=False, group_by="ticker", auto_adjust=True, threads=True)
    out = {}
    for s in tickers:
        df = _pick(d, s)
        if df is None:
            continue
        idx = df.index.tz_convert(NY) if df.index.tz is not None else df.index.tz_localize("UTC").tz_convert(NY)
        m, day = mins(idx), np.array(idx.date)
        keep = (m >= 570) & (m < 960) & (day < today)
        tab = pd.DataFrame(dict(day=day[keep], bin=(m[keep] - 570) // 5, v=df["Volume"].to_numpy()[keep])).pivot_table(
            index="day", columns="bin", values="v", aggfunc="sum").reindex(columns=range(78))
        tab = tab[tab.notna().sum(axis=1) >= 70].fillna(0).tail(14)  # 조기 폐장일은 뺌 (백테스트와 같게)
        if len(tab) >= 7:
            out[s] = tab.mean().to_numpy()
    return out


def alpaca_keys() -> tuple[str | None, str | None]:
    """ALPACA_KEY_ID·ALPACA_SECRET_KEY — 환경변수, 없으면 ~/.trading_desk.env."""
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
    return k, sec


def fetch_earnings() -> dict:
    try:
        js = requests.get(EARN_URL, timeout=8).json()
    except Exception:
        return {}
    return {w["sym"]: (w["next"], w.get("timing")) for w in js.get("watch", []) if w.get("next")}


def discover() -> list[dict]:
    """야후 스크리너(급등·거래 상위)에서 단타 후보를 새로 찾음. 너무 작은 종목은 거름."""
    seen, out = set(), []
    for kind in ("day_gainers", "most_actives"):
        try:
            qs = yf.screen(kind, count=40).get("quotes", [])
        except Exception:
            continue
        for q in qs:
            s = q.get("symbol", "")
            px, cap = q.get("regularMarketPrice") or 0, q.get("marketCap") or 0
            vol, avg = q.get("regularMarketVolume") or 0, q.get("averageDailyVolume3Month") or 0
            chg = (q.get("regularMarketChangePercent") or 0) / 100
            if (not s or s in seen or s in WATCHLIST or "." in s or "-" in s or px < 5 or cap < 1e9
                    or avg * px < 2e7 or chg < 0.02):
                continue
            seen.add(s)
            out.append(dict(sym=s, name=q.get("shortName") or s, chg=chg, px=px, cap=cap,
                            rvol=vol / avg if avg else None, src=kind))
    out.sort(key=lambda x: x["chg"] * min(x["rvol"] or 1, 5), reverse=True)
    return out[:15]


# ───────────────────────── analysis ─────────────────────────
def analyze(sym: str, df: pd.DataFrame | None, dly: pd.DataFrame | None, today: dt.date,
            spy_chg: float | None, earn: tuple | None) -> dict | None:
    if df is None or dly is None:
        return None
    prev = dly[dly.index < pd.Timestamp(today)]
    if len(prev) < 21:
        return None
    m, d = mins(df.index), np.array(df.index.date)
    reg = (m >= 570) & (m < 960)
    T = df[reg & (d == today)]
    PM = df[(m >= 240) & (m < 570) & (d == today)]
    pdc = float(prev["Close"].iloc[-1])
    last = float(T["Close"].iloc[-1]) if len(T) else float(PM["Close"].iloc[-1]) if len(PM) else pdc
    atr = float(true_range(prev).rolling(14).mean().iloc[-1])
    a = dict(sym=sym, n=len(T), last=last, pdc=pdc, pdh=float(prev["High"].iloc[-1]), pdl=float(prev["Low"].iloc[-1]),
             atr=atr, atr_pct=atr / last, dvol=float((prev["Close"] * prev["Volume"]).tail(20).mean()),
             sma20d=float(prev["Close"].tail(20).mean()), sma50d=float(prev["Close"].tail(50).mean()), chg=last / pdc - 1,
             ret63=pdc / float(prev["Close"].iloc[-64]) - 1 if len(prev) > 64 else np.nan,
             ret126=pdc / float(prev["Close"].iloc[-127]) - 1 if len(prev) > 127 else np.nan, sec63=np.nan,
             pmh=float(PM["High"].max()) if len(PM) else np.nan, pml=float(PM["Low"].min()) if len(PM) else np.nan,
             open=float(T["Open"].iloc[0]) if len(T) else np.nan, setups=[], rvol=np.nan, vwap=np.nan,
             ema9=np.nan, ema20=np.nan, rsi=np.nan, atrb=np.nan, hod=np.nan, lod=np.nan, or_done=False,
             t=df.index[-1].isoformat())
    a["gap"] = (a["open"] if len(T) else last) / pdc - 1
    a["rs"] = a["chg"] - spy_chg if ok(spy_chg) else np.nan
    a["earn_d"] = None
    if earn:
        try:
            a["earn_d"] = (dt.date.fromisoformat(earn[0]) - today).days
        except Exception:
            pass
    if len(T) < 3:
        return a

    # RVOL: 오늘 누적 거래량 vs 직전 며칠 같은 시각까지의 누적 거래량
    upto = int(mins(T.index)[-1])
    cum_today = float(T["Volume"].sum())
    past = [float(df["Volume"][reg & (d == x) & (m <= upto)].sum()) for x in sorted(set(d[reg])) if x != today]
    past = [x for x in past if x > 0]
    if len(past) >= 2:
        a["rvol"] = cum_today / np.mean(past)
    else:
        avg = float(prev["Volume"].tail(20).mean())
        a["rvol"] = cum_today / (avg * (upto - 569) / 390) if avg else np.nan

    # 5분봉 (며칠치 정규장 연속) 지표
    R = df[reg]
    R5 = R.resample("5min").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna(subset=["Close"])
    c = R5["Close"]
    e9, e20 = c.ewm(span=9, adjust=False).mean(), c.ewm(span=20, adjust=False).mean()
    tr = true_range(R5)
    sd20 = c.rolling(20).std()
    sq_on = (2 * sd20) < (1.5 * tr.rolling(20).mean())
    mom = c - ((R5["High"].rolling(20).max() + R5["Low"].rolling(20).min()) / 2 + c.rolling(20).mean()) / 2
    S = R5[np.array(R5.index.date) == today]
    vw, sd = vwap_bands(S)
    a.update(ema9=float(e9.iloc[-1]), ema20=float(e20.iloc[-1]), rsi=float(rsi(c).iloc[-1]),
             atrb=float(tr.rolling(14).mean().iloc[-1]), hod=float(T["High"].max()), lod=float(T["Low"].min()),
             vwap=float(vw.iloc[-1]), vsd=float(sd.iloc[-1]), sq_fired=bool(sq_on.iloc[-3:-1].any() and not sq_on.iloc[-1]),
             mom=float(mom.iloc[-1]))
    orb = T[mins(T.index) < 585]
    a.update(orh=float(orb["High"].max()), orl=float(orb["Low"].min()), or_done=upto >= 585)
    a["setups"] = find_setups(a, S, vw.to_numpy(), sd.to_numpy(), e9.reindex(S.index).to_numpy(),
                              e20.reindex(S.index).to_numpy(), upto)
    return a


def find_setups(a: dict, S: pd.DataFrame, vw, sd, e9, e20, upto: int) -> list[dict]:
    """트레이딩 데스크의 셋업 감지를 롱 전용으로 옮긴 것."""
    C, H, L, V = (S[k].to_numpy() for k in ("Close", "High", "Low", "Volume"))
    last = C[-1]
    atrb = a["atrb"] if ok(a["atrb"]) and a["atrb"] > 0 else last * 0.002
    k, out = 3, []

    def add(key, entry, stop, t1, t2, why, fresh=True):
        stop = min(stop, entry - 0.3 * atrb)
        risk = entry - stop
        if not np.isfinite(risk) or risk <= 0:
            return
        if not ok(t1) or t1 <= entry:
            t1 = entry + 1.5 * risk
        if not ok(t2) or t2 <= t1:
            t2 = t1 + risk
        out.append(dict(key=key, name=SETUP_KO[key], entry=entry, stop=stop, t1=t1, t2=t2,
                        rr=(t1 - entry) / risk, why=why, fresh=fresh))

    def crossed_up(level, n=k):
        seg = C[-(n + 1):]
        return ok(level) and len(seg) >= 2 and bool(((seg[1:] > level) & (seg[:-1] <= level)).any())

    if a["or_done"] and last > a["orh"] and last > vw[-1]:
        rng = a["orh"] - a["orl"]
        add("ORB", last, (a["orh"] + a["orl"]) / 2, a["orh"] + rng, a["orh"] + 2 * rng,
            f"첫 15분 고점 {f2(a['orh'])} 돌파, VWAP 위", fresh=crossed_up(a["orh"], 2 * k))
    if len(C) > k + 1 and last > vw[-1] and (C[-(k + 1):-1] < vw[-(k + 1):-1]).any() and V[-1] >= V.mean() * 0.8:
        add("VWAP", last, L[-(k + 1):].min(), vw[-1] + sd[-1], vw[-1] + 2 * sd[-1],
            f"VWAP {f2(vw[-1])} 아래에 있다가 다시 올라섬")
    if len(C) > 4 and last > vw[-1] and e9[-1] > e20[-1] and e20[-1] > e20[-4] and L[-1] <= e9[-1] * 1.002 and last > e9[-1]:
        add("PULL", last, min(L[-1], e20[-1]) - 0.1 * atrb, a["hod"], np.nan,
            f"상승 추세에서 9EMA {f2(e9[-1])}까지 눌렸다가 지지")
    gap, rv = a["gap"], a["rvol"]
    if ok(rv) and gap >= 0.03 and rv >= 2 and upto - 570 <= 90 and last > vw[-1] and (not ok(a["pmh"]) or last >= a["pmh"] * 0.995):
        add("GAPGO", last, vw[-1] - 0.1 * atrb, np.nan, np.nan,
            f"갭 {pc(gap)} + 거래량 {rv:.1f}배, 프리마켓 고점 근처 유지")
    if gap <= -0.015 and last > a["open"] and last > vw[-1]:
        add("GAPFILL", last, min(vw[-1], L[-k:].min()) - 0.1 * atrb, (a["open"] + a["pdc"]) / 2, a["pdc"],
            f"갭 하락 {pc(gap)} 후 시가·VWAP 회복 — 전일 종가 {f2(a['pdc'])}까지 메우기")
    if sd[-1] > 0 and ok(a["rsi"]) and last < vw[-1] - 2 * sd[-1] and a["rsi"] <= 30:
        add("FADE", last, L.min() - 0.25 * atrb, vw[-1] - sd[-1], vw[-1],
            f"VWAP −2σ 아래 과매도 (RSI {a['rsi']:.0f}) — 되돌림 반등")
    if a.get("sq_fired") and ok(a.get("mom")) and a["mom"] > 0:
        mid = float(S["Close"].rolling(20, min_periods=5).mean().iloc[-1])
        add("SQZ", last, mid - 0.2 * atrb, np.nan, np.nan, "변동성 압축이 풀리며 위로 발사")
    if crossed_up(a["pdh"]) and last > vw[-1]:
        add("PDL", last, a["pdh"] - 0.5 * atrb, np.nan, np.nan, f"전일 고점 {f2(a['pdh'])} 돌파")
    return out


def daily_trend(daily: dict, day: dt.date) -> dict:
    """일봉 추세 플래그 (전날 종가 기준, 백테스트 traders_lab.dflags와 같은 정의).
    d_up: 50일선 위 + 50일선이 200일선 위 · d_brk: 최근 5일 안에 20일 신고가 · d_rs: 6개월 수익률 순위 (0~1)."""
    out, moms = {}, {}
    for s, df in daily.items():
        if s in MARKET or s == VIX or df is None:
            continue
        c = df["Close"][df.index < pd.Timestamp(day)]
        if len(c) < 200:
            continue
        hh = c.rolling(20).max().shift()
        out[s] = dict(d_up=bool(c.iloc[-1] > c.tail(50).mean() > c.tail(200).mean()),
                      d_brk=bool((c.tail(5) > hh.tail(5)).any()))
        if len(c) > 127:
            moms[s] = c.iloc[-6] / c.iloc[-127] - 1
    rk = pd.Series(moms, dtype=float).rank(pct=True)
    for s in out:
        out[s]["d_rs"] = float(rk.get(s, 0.0))
    return out


DTREND_KO = {"up": "50·200일선 정배열 아님", "brk": "최근 5일 안에 20일 신고가 없음", "up_rs": "정배열·6개월 강세(상위 절반) 아님"}


def trend_ok(a: dict, mode: str | None) -> bool:
    if not mode:
        return True
    if mode == "up":
        return bool(a.get("d_up"))
    if mode == "brk":
        return bool(a.get("d_brk"))
    return bool(a.get("d_up")) and a.get("d_rs", 0) >= 0.5


def add_sector(snaps: dict):
    """같은 카테고리(반도체 등) 종목들의 3개월 평균 수익률 — 섹터 전체가 오르는 중인지."""
    sec = {}
    for s, a in snaps.items():
        if s in CAT_OF and a and ok(a.get("ret63")):
            sec.setdefault(CAT_OF[s], []).append(a["ret63"])
    for s, a in snaps.items():
        if a and s in CAT_OF and sec.get(CAT_OF[s]):
            a["sec63"] = float(np.mean(sec[CAT_OF[s]]))


def regime(snaps: dict, vix: float) -> dict:
    """시장 점수 0~100 — 높을수록 롱 하기 좋은 날."""
    def above(a):
        return a["last"] > a["vwap"] if a and a["n"] >= 3 and ok(a["vwap"]) else bool(a and a["chg"] > 0)

    spy, qqq, iwm = (snaps.get(s) for s in MARKET)
    sc, notes = 0.0, []
    for nm, a, w in (("SPY", spy, 15), ("QQQ", qqq, 15), ("IWM", iwm, 10)):
        if a:
            sc += w * above(a)
            notes.append(f"{nm} {pc(a['chg'], 2)} ({'VWAP 위' if above(a) else 'VWAP 아래'})")
    if spy:
        sc += 10 * (ok(spy["ema9"]) and spy["ema9"] > spy["ema20"])
        sc += 10 * (spy["last"] > spy["sma20d"])
    if ok(vix):
        sc += 20 if vix < 15 else 15 if vix < 20 else 8 if vix < 25 else 3 if vix < 30 else 0
        notes.append(f"VIX {vix:.1f}")
    w = [snaps[s] for s in WATCHLIST if s in snaps]
    br = float(np.mean([above(a) for a in w])) if w else 0.5
    sc += 20 * br
    notes.append(f"관심종목 {br * 100:.0f}% 상승 쪽")
    bias = "상승 우위" if sc >= 60 else "하락 우위" if sc <= 35 else "혼조"
    return dict(score=round(sc), bias=bias, vix=vix, breadth=br, notes=notes)


# ───────────────────────── storage ─────────────────────────
TRADE_COLS = ["id", "rules", "trader", "date", "sym", "src", "setup", "entry_t", "exit_t", "hold_min", "shares",
              "entry", "exit", "stop0", "t1", "t2", "risk_usd", "pnl", "r", "mfe_r", "mae_r", "exit_reason",
              "mkt_score", "rvol", "rs", "atr_pct", "gap", "score", "why_in", "why_out"]
DAY_COLS = ["date", "trader", "rules", "start_equity", "end_equity", "pnl", "trades", "wins", "losses", "mkt_score", "summary"]


def append_csv(path: Path, cols: list, row: dict):
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new:
            w.writeheader()
        w.writerow({c: row.get(c, "") for c in cols})


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_event(ev: dict):
    p = DATA / "events" / f"{ev['day']}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")


# ───────────────────────── trader ─────────────────────────
class Trader:
    def __init__(self, key: str, st: dict | None):
        self.key, self.P = key, TRADERS[key]
        st = st or {}
        self.start_cash, self.fee = self.P.get("cash", START_CASH), self.P.get("fee", FEE)
        self.cash = st.get("cash", self.start_cash)
        self.pos: dict = st.get("pos", {})
        self.day: dict = st.get("day", {})
        self.cool: dict = st.get("cool", {})
        self.curve: list = st.get("curve", [])
        self.status, self.thought = st.get("status", "준비"), st.get("thought", "켜졌어요. 데이터를 불러오는 중…")
        self.head = st.get("head", self.status)
        self.watch: list = []
        self.feed: deque = deque(maxlen=120)
        self.last_sig, self.last_think = "", 0.0
        self.seen_pass: dict = {}
        self.reject: dict = st.get("reject", {})  # 오늘 종목별 마지막 패스 이유 (회고에서 '놓친 종목' 설명용)

    def dump(self) -> dict:
        return dict(cash=self.cash, pos=self.pos, day=self.day, cool=self.cool, curve=self.curve[-400:],
                    status=self.status, thought=self.thought, head=self.head, reject=self.reject)

    # ── 기록 ──
    def log(self, kind: str, text: str, sym: str | None = None, **extra):
        t = now_ny()
        ev = dict(ts=t.isoformat(), day=t.date().isoformat(), trader=self.key, kind=kind, text=text, sym=sym, **extra)
        self.feed.appendleft(ev)
        write_event(ev)

    def think(self, status: str, text: str, sig: str, head: str | None = None):
        """생각을 갱신. head는 화면에서 1초에 읽히는 한 줄 요약. 피드에는 내용이 바뀌었을 때(또는 15분마다)만 남김."""
        self.status, self.thought, self.head = status, text, head or status
        if sig != self.last_sig or time.time() - self.last_think > 900:
            self.log("think", text)
            self.last_sig, self.last_think = sig, time.time()

    # ── 계좌 ──
    def equity(self, snaps: dict) -> float:
        return self.cash + sum(p["shares"] * self.price(s, snaps, p["entry"]) for s, p in self.pos.items())

    @staticmethod
    def price(sym: str, snaps: dict, default: float) -> float:
        a = snaps.get(sym)
        return a["last"] if a else default

    def new_day(self, today: dt.date, snaps: dict, bars: dict):
        if self.pos:  # 프로그램이 꺼져 있어 전날 청산을 못 한 경우 — 전날 정규장 마지막 가격으로 정리
            for s in list(self.pos):
                df = bars.get(s)
                px = self.pos[s]["entry"]
                if df is not None:
                    d = df[(np.array(df.index.date) == dt.date.fromisoformat(self.day.get("date", today.isoformat())))
                           & (mins(df.index) < 960)]
                    px = float(d["Close"].iloc[-1]) if len(d) else px
                self.sell(s, self.pos[s]["shares"], px, "전날 청산 누락 정리 (프로그램 꺼져 있었음)", now_ny(), final=True)
        eq = self.cash
        self.day = dict(date=today.isoformat(), start=eq, trades=0, wins=0, losses=0, realized=0.0,
                        halted="", planned=False, reviewed=False)
        self.cool, self.reject, self.curve = {}, {}, []

    # ── 매매 ──
    def buy(self, a: dict, s: dict, shares: int, score: float, mk: dict, src: str, now: dt.datetime):
        px = a["last"] * (1 + SLIP)
        self.cash -= shares * px + self.fee
        self.pos[a["sym"]] = dict(shares=shares, init=shares, entry=px, stop=s["stop"], stop0=s["stop"], t1=s["t1"],
                                  t2=s["t2"], setup=s["key"], src=src, opened=now.isoformat(), checked=a["t"],
                                  half=False, realized=-self.fee, sold=0, sold_val=0.0, mfe=px, mae=px,
                                  why=s["why"], score=round(score), feat=dict(mkt_score=mk["score"], rvol=a["rvol"],
                                  rs=a["rs"], atr_pct=a["atr_pct"], gap=a["gap"]))
        self.day["trades"] += 1
        risk = shares * (px - s["stop"])
        self.log("buy", f"{a['sym']} {shares}주 매수 @ {f2(px)} — {s['name']}. {s['why']}. "
                        f"손절 {f2(s['stop'])} (최대 손실 ${risk:,.0f}), 1차 목표 {f2(s['t1'])}, 2차 {f2(s['t2'])}.",
                 sym=a["sym"], price=px, shares=shares)

    def sell(self, sym: str, shares: int, px: float, reason: str, now: dt.datetime, final: bool = False, slip: float = SLIP):
        p = self.pos[sym]
        fill = px * (1 - slip)  # 지정가로 판 경우(slip=0)는 스프레드를 안 냄
        self.cash += shares * fill - self.fee
        p["shares"] -= shares
        p["sold"] += shares
        p["sold_val"] += shares * fill
        p["realized"] += shares * (fill - p["entry"]) - self.fee
        rps = p["entry"] - p["stop0"]
        self.log("sell", f"{sym} {shares:g}주 매도 @ {f2(fill)} — {reason} ({money(shares * (fill - p['entry']))})",
                 sym=sym, price=fill, shares=shares)
        if p["shares"] > 0 and not final:
            return
        pnl = p["realized"]  # 매수·매도 수수료 포함
        r = pnl / (p["init"] * rps) if rps > 0 else 0.0
        opened = dt.datetime.fromisoformat(p["opened"])
        self.day["realized"] += pnl
        self.day["wins" if pnl > 0 else "losses"] += 1
        if pnl <= 0:
            self.cool[sym] = (now + dt.timedelta(minutes=self.P["cooldown"])).isoformat()
        append_csv(DATA / "trades.csv", TRADE_COLS, dict(
            id=uuid.uuid4().hex[:8], rules=RULES, trader=self.key, date=opened.date().isoformat(), sym=sym, src=p["src"],
            setup=p["setup"], entry_t=f"{opened:%H:%M}", exit_t=f"{now:%H:%M}",
            hold_min=round((now - opened).total_seconds() / 60), shares=p["init"], entry=round(p["entry"], 4),
            exit=round(p["sold_val"] / p["sold"], 4), stop0=round(p["stop0"], 4), t1=round(p["t1"], 4), t2=round(p["t2"], 4),
            risk_usd=round(p["init"] * rps, 2), pnl=round(pnl, 2), r=round(r, 3),
            mfe_r=round((p["mfe"] - p["entry"]) / rps, 3) if rps > 0 else "",
            mae_r=round((p["mae"] - p["entry"]) / rps, 3) if rps > 0 else "", exit_reason=reason.split(" (")[0].split(" —")[0],
            score=p["score"], why_in=p["why"], why_out=reason,
            **{k: (round(v, 4) if ok(v) else "") for k, v in p["feat"].items()}))
        self.log("close", f"{sym} 거래 종료: {money(pnl)} ({r:+.2f}R). {reason}.", sym=sym, pnl=pnl, r=r)
        del self.pos[sym]

    def manage(self, sym: str, df: pd.DataFrame, a: dict | None, now: dt.datetime, flat_at: int):
        """새로 나온 1분봉을 순서대로 보며 손절·익절·트레일링 처리 (같은 봉에 둘 다 닿으면 손절 우선)."""
        p = self.pos[sym]
        m = mins(df.index)
        new = df[(df.index > pd.Timestamp(p["checked"])) & (m >= 570) & (m < 960)]
        rps = p["entry"] - p["stop0"]
        for ts, b in new.iterrows():
            p["checked"] = ts.isoformat()
            p["mfe"], p["mae"] = max(p["mfe"], b["High"]), min(p["mae"], b["Low"])
            if b["Low"] <= p["stop"]:
                px = min(b["Open"], p["stop"])
                why = ("손절선 도달" if p["stop"] < p["entry"] else "본전 손절선 도달" if not p["half"] or p["stop"] <= p["entry"] * 1.001
                       else "트레일링 스탑 도달 — 남은 수익 지킴")
                return self.sell(sym, p["shares"], px, f"{why} ({f2(p['stop'])})", now, final=True)
            if not p["half"] and b["High"] >= p["t1"]:
                half = max(1, p["shares"] // 2) if p["shares"] > 1 else p["shares"]
                p["half"] = True
                self.sell(sym, half, p["t1"], "1차 목표 도달 — 절반 익절", now, final=half == p["shares"])
                if sym not in self.pos:
                    return
                p["stop"] = max(p["stop"], p["entry"])
                self.log("think", f"{sym} 남은 {p['shares']}주는 손절을 본전({f2(p['entry'])})으로 올리고 2차 목표 {f2(p['t2'])}를 노림.", sym=sym)
            if p["half"] and b["High"] >= p["t2"]:
                return self.sell(sym, p["shares"], p["t2"], "2차 목표 도달 — 전량 익절", now, final=True)
        if a and p["half"]:  # 트레일링
            line = a["ema9"] if self.P["trail"] == "ema9" else a["ema20"]
            if ok(line) and ok(a["atrb"]) and line - 0.1 * a["atrb"] > p["stop"] and line < a["last"]:
                p["stop"] = line - 0.1 * a["atrb"]
        held = (now - dt.datetime.fromisoformat(p["opened"])).total_seconds() / 60
        last = a["last"] if a else p["entry"]
        if held >= self.P["time_stop"] and not p["half"] and rps > 0 and (last - p["entry"]) / rps < 0.3:
            return self.sell(sym, p["shares"], last, f"시간 손절 — {held:.0f}분 동안 움직임 없음", now, final=True)
        if now.hour * 60 + now.minute >= flat_at:
            return self.sell(sym, p["shares"], last, "장 마감 전 청산 (단타 원칙)", now, final=True)

    # ── 한 번의 판단 ──
    def step(self, now: dt.datetime, snaps: dict, bars: dict, mk: dict, disc: dict, news=None):
        P, m, today = self.P, now.hour * 60 + now.minute, now.date()
        cm = close_min(today)
        for s in list(self.pos):
            if s in bars:
                self.manage(s, bars[s], snaps.get(s), now, cm - 10)
        eq = self.equity(snaps)
        day_pnl = eq - self.day["start"]
        if not self.day["halted"] and day_pnl <= -P["daily_loss"] * self.day["start"]:
            self.day["halted"] = f"일 손실 한도 {pc(-P['daily_loss'])} 도달"
            self.log("think", f"오늘 손실 {money(day_pnl)} — 한도 도달. 오늘은 더 이상 신규 진입하지 않음. 잃은 걸 되찾으려는 매매가 제일 위험해.")
        if not self.day["halted"] and self.day["trades"] >= P["max_trades"] and not self.pos:
            self.day["halted"] = f"하루 최대 {P['max_trades']}회 거래 채움"

        # 후보 평가 (보유 중이어도 화면용으로 계속 봄)
        cands = self.evaluate(snaps, mk, disc, now, news)
        self.watch = [dict(sym=c["sym"], setup=c["setup"], score=round(c["score"]), ok=c["ok"], why=c["why"],
                           src=c["src"]) for c in cands[:5]]

        in_win = any(a_ <= m < min(b_, cm - 15) for a_, b_ in P["windows"])
        pos_txt = " · ".join(self.pos_line(s, snaps) for s in self.pos)
        if self.day["halted"]:
            return self.think("휴식" if not self.pos else "보유", f"{self.day['halted']} — 오늘은 신규 진입 끝."
                              + (f" 남은 포지션만 관리: {pos_txt}" if pos_txt else " 내일 다시."), "halt" + str(len(self.pos)),
                              f"오늘 매매 끝 — {self.day['halted']}")
        if m >= cm - 15:
            return self.think("보유" if self.pos else "마감", (f"마감 직전 — 신규 진입 없음. {pos_txt}. 15:50 ET에 정리."
                              if self.pos else "마감 직전 — 오늘 매매 종료. 장 끝나면 회고를 쓸게."), "close" + str(len(self.pos)),
                              "마감 직전 — 15:50 ET 전량 정리" if self.pos else "오늘 매매 끝")
        if not in_win:
            nxt = next((a_ for a_, _ in P["windows"] if a_ > m), None)
            when = f"{hm(nxt)} ET ({ams(nxt, today)} 암스테르담)" if nxt else "내일"
            why = "개장 직후 레인지가 잡히길 기다리는 중" if m < P["windows"][0][0] else "점심 횡보 시간 — 가짜 돌파가 많아서 쉼"
            return self.think("보유" if self.pos else "관망", f"{why}. 다음 매매 시간대 {when}." + (f" 보유: {pos_txt}" if pos_txt else ""),
                              f"win{nxt}{len(self.pos)}",
                              f"{'개장 직후 대기' if m < P['windows'][0][0] else '점심 휴식'} — {ams(nxt, today) + ' 재개' if nxt else '오늘 끝'}")
        if ok(mk["vix"]) and mk["vix"] > P["max_vix"]:
            return self.think("관망", f"VIX {mk['vix']:.1f} > 내 한도 {P['max_vix']} — 변동성이 너무 커서 쉼." + (f" 보유: {pos_txt}" if pos_txt else ""), "vix",
                              f"쉼 — VIX {mk['vix']:.0f} 너무 높음")
        if mk["score"] < P["min_mkt"]:
            return self.think("관망", f"시장 점수 {mk['score']} ({mk['bias']}) < 내 기준 {P['min_mkt']}. "
                              f"{', '.join(mk['notes'][:2])}. 시장이 좋아질 때까지 기다림." + (f" 보유: {pos_txt}" if pos_txt else ""),
                              f"mkt{mk['score'] // 5}", f"관망 — 시장 점수 {mk['score']} < 기준 {P['min_mkt']}")
        if self.day["trades"] >= P["max_trades"]:
            return self.think("보유", f"오늘 거래 {P['max_trades']}회를 다 씀 — 신규 진입 없음. {pos_txt}", "maxed" + ",".join(self.pos),
                              "오늘 거래 횟수 다 씀")
        if len(self.pos) >= P["max_open"]:
            return self.think("보유", f"포지션 꽉 참 ({len(self.pos)}/{P['max_open']}). {pos_txt}", "full" + ",".join(self.pos),
                              f"자리 꽉 참 — {len(self.pos)}종목 보유")

        self.log_passes(cands)
        pick = next((c for c in cands if c["ok"]), None)
        if pick:
            a, s = snaps[pick["sym"]], pick["s"]
            rps = a["last"] * (1 + SLIP) - s["stop"]
            shares = int(min(eq * P["risk"] / rps, eq * P["max_pos"] / a["last"], (self.cash - FEE) / a["last"]))
            if shares >= 1:
                self.log("decide", f"{pick['sym']} 진입 결정 (점수 {pick['score']:.0f}/{P['min_score']}). "
                                   f"시장 {mk['score']}점 {mk['bias']}, 거래량 {a['rvol']:.1f}배, "
                                   f"SPY 대비 {pc(a['rs']) if ok(a['rs']) else '-'}"
                                   + (f", 3개월 {pc(a['ret63'], 0)} (섹터 {pc(a['sec63'], 0)})" if ok(a.get("ret63")) and ok(a.get("sec63")) else "")
                                   + (", 오늘 새로 찾은 종목" if pick["src"] == "새로 발견" else "")
                                   + (f", 뉴스 호재: \"{pick['news']}\"" if pick.get("news") else "") + ".", sym=pick["sym"])
                self.buy(a, s, shares, pick["score"], mk, pick["src"], now)
                return self.think("보유", f"{pick['sym']} 샀어. {s['why']}. 이제 손절 {f2(s['stop'])} / 목표 {f2(s['t1'])} 지켜봄.",
                                  "buy" + pick["sym"], f"{pick['sym']} 매수함")
            self.log("pass", f"{pick['sym']} 사고 싶지만 현금 부족 — 다음 기회.", sym=pick["sym"])
        near = [c for c in cands if not c["ok"]][:2]
        body = (f"시장 {mk['score']}점 {mk['bias']}. 조건 맞는 셋업 찾는 중 — "
                + (" / ".join(f"{c['sym']} {c['setup_ko']}: {c['why']}" for c in near) if near else "지금은 눈에 띄는 셋업 없음") + ".")
        self.think("보유" if self.pos else "탐색", body + (f" 보유: {pos_txt}" if pos_txt else ""),
                   "scan" + ",".join(c["sym"] for c in near) + str(len(self.pos)),
                   f"찾는 중 — 후보 {len(cands)}개, 아직 조건 미달" if cands else "찾는 중 — 셋업 없음")

    def react(self, it: dict, held: set):
        """큰 뉴스에 대한 트레이더의 생각."""
        imp = sorted(it["imp"].items(), key=lambda kv: -abs(kv[1]))
        up = [s for s, v in imp if v >= 0.5][:6]
        dn = [s for s, v in imp if v <= -0.5][:6]
        th = it["themes"][0]["name"] if it["themes"] else None
        head = f"뉴스 \"{it['head'][:90]}\"" + (f" ({th})" if th else "")
        parts = []
        mine_dn = [s for s in dn if s in held]
        if self.key == "agg":
            if up:
                parts.append(f"{', '.join(up)} 호재로 봄 — 거래량 붙고 셋업 나오면 우선 진입")
            if dn:
                parts.append(f"{', '.join(dn)} 악재 — 당분간 안 삼")
        elif self.key == "bal":
            if up:
                parts.append(f"{', '.join(up)} 우선순위 올림. 단 시장 방향·상대강도 확인 후")
            if dn:
                parts.append(f"{', '.join(dn)} 악재 — 후보에서 뺌")
        else:
            if up or dn:
                parts.append(f"큰 뉴스 — {', '.join((up + dn)[:6])}는 {self.P['news_wait']}분 동안 지켜만 봄")
        if mine_dn:
            parts.append(f"보유 중인 {', '.join(mine_dn)}에 악재 — 손절선 지키며 주시")
        if parts:
            self.log("news", f"{head} → {'. '.join(parts)}.", sym=(up + dn)[0] if up + dn else None)

    def pos_line(self, s: str, snaps: dict) -> str:
        p = self.pos[s]
        last = self.price(s, snaps, p["entry"])
        rps = p["entry"] - p["stop0"]
        return f"{s} {pc(last / p['entry'] - 1, 2)} ({(last - p['entry']) / rps:+.1f}R)" if rps > 0 else s

    def evaluate(self, snaps: dict, mk: dict, disc: dict, now: dt.datetime, news=None) -> list[dict]:
        P, out = self.P, []
        for sym, a in snaps.items():
            if sym in MARKET or not a or not a["setups"] or sym in self.pos:
                continue
            src = "관심종목" if sym in CAT_OF else "새로 발견"
            mine = [s for s in a["setups"] if s["key"] in P["setups"]]
            if not mine:
                s = a["setups"][0]
                out.append(self._cand(a, s, src, 0, False, f"{s['name']} — 내 전략이 아님"))
                continue
            s = max(mine, key=lambda x: (x["fresh"], x["rr"]))
            s = self.shape(s, a)
            ns, top = news.score(sym, now.timestamp()) if news else (0.0, None)
            sc = self.score(a, mk) + 5 * (s["rr"] >= 2) + P.get("news_w", 0) * max(ns, 0)
            rv = a["rvol"]
            why = None
            if a["last"] < 5:
                why = "주가 $5 미만"
            elif a["dvol"] < P["min_dvol"]:
                why = f"거래대금 부족 (일 ${a['dvol'] / 1e6:,.0f}M < ${P['min_dvol'] / 1e6:,.0f}M)"
            elif a["atr_pct"] > P["max_atr"]:
                why = f"변동성 과다 (ATR {a['atr_pct'] * 100:.1f}% > {P['max_atr'] * 100:.0f}%)"
            elif not ok(rv) or rv < P["min_rvol"]:
                why = f"거래량 부족 (RVOL {rv:.1f}배 < {P['min_rvol']}배)" if ok(rv) else "거래량 데이터 부족"
            elif P["no_earn"] and a["earn_d"] is not None and 0 <= a["earn_d"] <= 1:
                why = f"어닝 D-{a['earn_d']} — 이벤트 리스크"
            elif not s["fresh"]:
                why = "돌파 후 시간이 지남 — 추격 안 함"
            elif s["rr"] < P["min_rr"]:
                why = f"손익비 {s['rr']:.1f} < {P['min_rr']} (목표까지 여유 부족)"
            elif P["need_rs"] and ok(a["rs"]) and a["rs"] < 0:
                why = f"시장보다 약함 (SPY 대비 {pc(a['rs'])})"
            elif not trend_ok(a, P.get("dtrend")):
                why = f"일봉 추세 아님 — {DTREND_KO[P['dtrend']]}"
            elif P.get("news_min") and (not top or ns < P["news_min"]):
                why = "호재 뉴스 없음 — 뉴스 붙은 종목만 삼"
            elif P.get("need_trend") and ok(a.get("sma50d")) and a["last"] < a["sma50d"]:
                why = f"50일선({f2(a['sma50d'])}) 아래 — 중기 추세 약함"
            elif top and ns <= P.get("news_block", -1):
                why = f"악재 뉴스 — \"{top['head'][:50]}\""
            elif top and P.get("news_wait") and abs(top["v"]) >= 0.7 and top["age"] < P["news_wait"]:
                why = f"큰 뉴스 {top['age']:.0f}분 전 — {P['news_wait']}분 진정될 때까지 대기"
            elif sym in self.cool and now < dt.datetime.fromisoformat(self.cool[sym]):
                why = "최근 손절한 종목 — 쿨다운 중"
            elif sc < P["min_score"]:
                why = f"점수 {sc:.0f} < 기준 {P['min_score']}"
            if why:
                self.reject[sym] = why
            good_news = top["head"][:60] if top and ns >= 0.3 and P.get("news_w") else None
            c = self._cand(a, s, src, sc, why is None, why or f"{s['why']} — 조건 충족" + (" · 뉴스 호재" if good_news else ""))
            c["news"] = good_news
            out.append(c)
        out.sort(key=lambda c: (c["ok"], c["score"]), reverse=True)
        return out

    def shape(self, s: dict, a: dict) -> dict:
        """트레이더별 손절폭(5분 ATR의 최소 배수)과 1차 목표(R 배수)를 셋업에 적용."""
        k, t1r = self.P.get("stop_atr", 0.3), self.P.get("t1_r", 0)
        if k <= 0.3 and not t1r:
            return s
        atrb = a["atrb"] if ok(a["atrb"]) and a["atrb"] > 0 else a["last"] * 0.002
        s = dict(s, stop=min(s["stop"], s["entry"] - k * atrb))
        risk = s["entry"] - s["stop"]
        if t1r:
            s["t1"], s["t2"] = s["entry"] + t1r * risk, s["entry"] + (t1r + 1) * risk
        elif s["t1"] - s["entry"] < 1.0 * risk:
            s["t1"] = s["entry"] + 1.5 * risk
            s["t2"] = max(s["t2"], s["t1"] + risk)
        s["rr"] = (s["t1"] - s["entry"]) / risk
        return s

    @staticmethod
    def _cand(a, s, src, sc, ok_, why):
        return dict(sym=a["sym"], setup=s["key"], setup_ko=s["name"], s=s, score=sc, ok=ok_, why=why, src=src)

    def score(self, a: dict, mk: dict) -> float:
        clip = lambda x: float(min(max(x, 0), 1))
        r63, s63 = a.get("ret63"), a.get("sec63")
        trend = self.P.get("trend_w", 0) * (0.5 * clip((r63 if ok(r63) else 0) / 0.3) + 0.5 * clip((s63 if ok(s63) else 0) / 0.3))
        return self._score(a, mk, clip) + trend

    def _score(self, a: dict, mk: dict, clip) -> float:
        rv = a["rvol"] if ok(a["rvol"]) else 1.0
        rs = a["rs"] if ok(a["rs"]) else 0.0
        atrp, dv = a["atr_pct"], a["dvol"]
        up = mk["bias"] == "상승 우위"
        earn_soon = a["earn_d"] is not None and 0 <= a["earn_d"] <= 1
        if self.key == "agg":
            return 15 + 30 * clip(rv / 3) + 20 * clip(abs(a["gap"]) / 0.05) + 20 * clip(atrp / 0.06) + 15 * clip(rs / 0.03)
        if self.key == "bal":
            return 20 + 25 * clip(rv / 2) + 20 * up + 15 * clip(rs / 0.02) + 10 * (dv >= 1e8) + 10 * (0.02 <= atrp <= 0.06) - 20 * earn_soon
        return 20 + 25 * (dv >= 5e8) + 20 * up + 15 * (atrp <= 0.04) + 10 * (not earn_soon) + 10 * clip(rv / 1.5) - 50 * earn_soon

    def log_passes(self, cands: list[dict]):
        """기준 근처에서 탈락한 후보만 '패스' 이유를 피드에 남김 (같은 종목·이유는 30분에 한 번)."""
        n = 0
        for c in cands:
            if c["ok"] or c["score"] < self.P["min_score"] - 15 or n >= 2:
                continue
            k = f"{c['sym']}|{c['why']}"
            if time.time() - self.seen_pass.get(k, 0) < 1800:
                continue
            self.seen_pass[k] = time.time()
            self.log("pass", f"{c['sym']} {c['setup_ko']} 보임 — 하지만 {c['why']}. 패스.", sym=c["sym"])
            n += 1

    # ── 개장 전 계획 · 마감 후 회고 ──
    def plan(self, snaps: dict, mk: dict, disc: dict, today: dt.date, top_news: list | None = None):
        P = self.P
        eq = self.day["start"]
        gaps = sorted([a for s, a in snaps.items() if s not in MARKET and a and a["dvol"] >= P["min_dvol"]],
                      key=lambda a: a["chg"], reverse=True)
        ups = [f"{a['sym']} {pc(a['chg'])}" for a in gaps[:4] if a["chg"] > 0.01]
        earn = [s for s, a in snaps.items() if a and a["earn_d"] in (0, 1)]
        w = " / ".join(f"{hm(a_)}–{hm(b_)} ET ({ams(a_, today)}–{ams(b_, today)})" for a_, b_ in P["windows"])
        lines = [f"오늘 계획 — {P['motto']}",
                 f"프리마켓 시장 점수 {mk['score']} ({mk['bias']}): {', '.join(mk['notes'])}.",
                 f"갭 상승 관심: {', '.join(ups) if ups else '눈에 띄는 갭 없음'}."]
        if disc and self.key != "def":
            lines.append("스캐너가 새로 찾은 종목: " + ", ".join(f"{d['sym']} {pc(d['chg'])}" for d in list(disc.values())[:5]) + ".")
        if top_news:
            lines.append("밤사이 주요 뉴스: " + " / ".join(
                f"\"{it['head'][:60]}\" → " + ", ".join(f"{s} {'호재' if v > 0 else '악재'}" for s, v in
                                                     sorted(it["imp"].items(), key=lambda kv: -abs(kv[1]))[:4]) for it in top_news[:3]) + ".")
        if earn:
            lines.append(f"어닝 임박: {', '.join(earn[:6])}" + (" — 이 종목들은 안 건드림." if P["no_earn"] else " — 변동성 기회로 봄."))
        lines.append(f"규칙: 거래당 위험 ${eq * P['risk']:,.0f} · 하루 최대 {P['max_trades']}회 · 손실 한도 ${eq * P['daily_loss']:,.0f} · "
                     f"시장 점수 {P['min_mkt']} 이상에서만 · 매매 시간 {w}.")
        if mk["score"] < P["min_mkt"]:
            lines.append(f"지금 시장 점수로는 내 기준({P['min_mkt']}) 미달 — 개장 후 회복 안 되면 오늘은 쉬는 날.")
        self.log("plan", "\n".join(lines))
        self.day["planned"] = True

    def review(self, snaps: dict, mk: dict, today: dt.date):
        trades = [t for t in read_csv(DATA / "trades.csv") if t["trader"] == self.key and t["date"] == today.isoformat()]
        eq = self.cash
        pnl = eq - self.day["start"]
        L = [f"오늘 결과: {money(pnl)} ({pc(pnl / self.day['start'], 2)}), 거래 {len(trades)}회"
             + (f" — {self.day['wins']}승 {self.day['losses']}패." if trades else ".")]
        if trades:
            best = max(trades, key=lambda t: float(t["pnl"]))
            worst = min(trades, key=lambda t: float(t["pnl"]))
            L.append(f"최고: {best['sym']} {best['setup']} {money(float(best['pnl']))} / 최악: {worst['sym']} {worst['setup']} {money(float(worst['pnl']))}.")
            fast = [t for t in trades if float(t["r"]) < 0 and int(t["hold_min"]) <= 10]
            if fast:
                L.append(f"아쉬움: 진입 10분 안에 손절 {len(fast)}회 — 너무 성급하게 들어갔을 수 있음. 5분봉 마감 확인 후 진입을 고려.")
            gave = [t for t in trades if t["mfe_r"] and float(t["mfe_r"]) >= 1 and float(t["r"]) <= 0.1]
            if gave:
                L.append(f"아쉬움: 1R 이상 갔다가 본전 이하로 끝난 거래 {len(gave)}회 ({', '.join(t['sym'] for t in gave)}) — 익절을 더 빨리.")
            ts = [t for t in trades if t["exit_reason"].startswith("시간 손절")]
            if ts:
                L.append(f"시간 손절 {len(ts)}회 — 거래량이 약한 종목을 골랐을 가능성.")
            good = [t for t in trades if float(t["r"]) >= 1]
            if good:
                L.append(f"잘한 점: {', '.join(t['sym'] for t in good)}에서 계획대로 목표까지 들고 감.")
        else:
            L.append("매매 없음 — 내 기준을 통과한 셋업이 없었음. 기다린 것도 결정.")
        traded = {t["sym"] for t in trades}
        movers = sorted([a for s, a in snaps.items() if s not in MARKET and a and a["n"] and s not in traded],
                        key=lambda a: a["chg"], reverse=True)[:3]
        if movers:
            L.append("놓친 종목: " + " / ".join(f"{a['sym']} {pc(a['chg'])}" + (f" (패스 이유: {self.reject[a['sym']]})"
                     if a["sym"] in self.reject else " (셋업 감지 안 됨)") for a in movers) + ".")
        self.log("review", "\n".join(L))
        append_csv(DATA / "days.csv", DAY_COLS, dict(date=today.isoformat(), trader=self.key, rules=RULES,
                   start_equity=round(self.day["start"], 2), end_equity=round(eq, 2), pnl=round(pnl, 2), trades=len(trades),
                   wins=self.day["wins"], losses=self.day["losses"], mkt_score=mk["score"], summary=L[0]))
        self.day["reviewed"] = True



# ───────────────────────── 스윙 트레이더 (일봉) ─────────────────────────
def prev_trading_day(d: dt.date) -> dt.date:
    d -= dt.timedelta(days=1)
    while not is_trading_day(d):
        d -= dt.timedelta(days=1)
    return d


MODE_TXT = {  # mode → (장 마감 후 보는 것, 장중 생각 꼬리말, 보유 중 한 줄)
    "rotation": ("모멘텀 순위", " 손절 없이 매달 말에만 갈아탐.", "월말까지 그대로"),
    "pullback": ("과매도 눌림(RSI 2)", " 손절 없이 장 마감 후 5일선 회복·보유 일수만 확인.", "반등 기다림"),
    "trendpull": ("20일선 지지 반등", " 장중엔 손절선만 지켜보고, 판단은 장 마감 후 일봉으로.", "손절선만 감시"),
    "breakout": ("돌파 신호", " 장중엔 손절선만 지켜보고, 판단은 장 마감 후 일봉으로.", "손절선만 감시"),
    "desk": ("다섯 전략의 목표 비중", " 손절 없이 장 마감 후 목표 비중대로만 조정.", "목표 비중 유지"),
}


class SwingTrader(Trader):
    """돌파형 스윙 — swing_lab.py에서 검증한 규칙. 장 마감 후 일봉으로 신호 → 다음 날 시가 매매 → 장중엔 손절만 감시.
    단타 셋과 달리 포지션을 밤새 들고 감 (평균 10~15일)."""

    def __init__(self, key: str, st: dict | None):
        super().__init__(key, st)
        st = st or {}
        self.orders: dict = st.get("orders", {"for": None, "buy": [], "sell": [], "done": True})
        self.sig_day: str | None = st.get("sig_day")
        self.watch = st.get("watch", [])

    def dump(self) -> dict:
        return super().dump() | dict(orders=self.orders, sig_day=self.sig_day, watch=self.watch)

    def new_day(self, today: dt.date, snaps: dict, bars: dict):
        eq = self.equity(snaps)  # 단타와 달리 포지션 유지
        self.day = dict(date=today.isoformat(), start=eq, trades=0, wins=0, losses=0, realized=0.0,
                        halted="", planned=False, reviewed=False)
        self.curve = []

    def pool(self) -> list[str]:
        """매수 후보군 — 기본은 관심종목, uni="wide"(돌쇠)면 나스닥100까지."""
        return WATCHLIST + [s for s in NDX if s not in CAT_OF] if self.P.get("uni") == "wide" else WATCHLIST

    @staticmethod
    def first_bar(sym: str, now: dt.datetime, bars: dict):
        """오늘 정규장 첫 1분봉 (없으면 None)."""
        df = bars.get(sym)
        if df is None:
            return None
        m = mins(df.index)
        t = df[(np.array(df.index.date) == now.date()) & (m >= 570) & (m < 960)]
        return t.iloc[0] if len(t) else None

    def pending(self) -> list[str]:
        """아직 체결 안 된 시가 주문 종목 — 엔진이 이 종목들의 1분봉도 받아 오게."""
        o = self.orders
        return [] if o.get("done") else [x["sym"] for x in o.get("sell", []) + o.get("buy", [])]

    def held_txt(self, snaps: dict) -> str:
        if not self.pos:
            return "보유 없음"
        out = []
        for s, p in self.pos.items():
            last = self.price(s, snaps, p["entry"])
            days = (now_ny().date() - dt.date.fromisoformat(p["opened"][:10])).days
            out.append(f"{s} {pc(last / p['entry'] - 1)} ({days}일째" + (f", 손절 {f2(p['stop'])})" if p["stop"] > 0 else ")"))
        return " · ".join(out)

    def order_txt(self) -> str:
        o = self.orders
        if o.get("done") or not (o["buy"] or o["sell"]):
            return ""
        return ("시가 매도 " + ", ".join(x["sym"] for x in o["sell"]) if o["sell"] else "") + \
               (" / " if o["sell"] and o["buy"] else "") + ("시가 매수 " + ", ".join(x["sym"] for x in o["buy"]) if o["buy"] else "")

    # ── 장 마감 후: 일봉 신호 ──
    def daily_bars(self, day: dt.date) -> dict | None:
        syms = list(dict.fromkeys(self.pool() + list(self.pos) + ["SPY"]))
        d = yf.download(syms, period="2y", interval="1d", progress=False, group_by="ticker", auto_adjust=True, threads=True)
        F = {}
        for s in syms:
            df = _pick(d, s)
            if df is not None:
                df.index = pd.DatetimeIndex([pd.Timestamp(x).date() for x in df.index])
                df = df[df.index <= pd.Timestamp(day)]
                if len(df) >= 130:
                    F[s] = df
        if "NVDA" not in F or F["NVDA"].index[-1].date() != day:
            return None  # 그날 일봉이 아직 안 올라옴 — 다음 틱에 다시
        return F

    def market_on(self, F: dict) -> tuple[bool, str]:
        """시장 필터 (차차): SPY 종가가 N일선 위일 때만 신규 매수. 백테스트 swing_lab의 mkt50과 같은 정의."""
        n = self.P.get("mkt")
        if not n or "SPY" not in F:
            return True, ""
        c = F["SPY"]["Close"]
        last, ma = float(c.iloc[-1]), float(c.tail(int(n)).mean())
        return last > ma, f"SPY {f2(last)} {'>' if last > ma else '<'} {n}일선 {f2(ma)}"

    def signals(self, day: dt.date, snaps: dict):
        """장 마감 후 일봉으로 매수·매도 신호. 규칙은 mode별 (swing_lab.py와 같은 정의):
        breakout  (돌쇠) N일 종가 신고가 + 거래량 volr배 + 50·200일선 정배열 → 손절 2ATR·트레일링, exit_ma일선 이탈 청산
        pullback  (티모) 200·50일선 위에서 RSI(2) < rsi (단기 과매도) → 5일선 위 마감 또는 max_hold일 지나면 청산
        trendpull (차차) 정배열 종목이 20일선(+tol)까지 내려왔다가 20일선 위·전일 대비 상승 마감 → exit_ma일선 이탈 청산"""
        P, mode = self.P, self.P.get("mode", "breakout")
        F = self.daily_bars(day)
        if F is None:
            return False
        on, mtxt = self.market_on(F)
        if not on:
            self.log("think", f"시장 필터 꺼짐 ({mtxt}) — 신규 매수 없음" + (" · 보유 종목은 내일 시가에 전부 정리." if P.get("exit_mkt") and self.pos else "."))
        sells, cands, near = [], [], []
        stat = dict(trend=0, sig=0)
        pool = set(self.pool())
        for s, df in F.items():
            if s not in self.pos and s not in pool:
                continue
            src = "관심종목" if s in CAT_OF else "나스닥100"
            c, v, lo = df["Close"], df["Volume"], df["Low"]
            last, prev = float(c.iloc[-1]), float(c.iloc[-2])
            atr = float(true_range(df).rolling(14).mean().iloc[-1])
            ma5, ma20, ma50 = float(c.tail(5).mean()), float(c.tail(20).mean()), float(c.tail(50).mean())
            ma200 = float(c.tail(200).mean()) if len(c) >= 200 else np.nan
            d = c.diff()
            rsi2 = float((100 - 100 / (1 + d.clip(lower=0).ewm(alpha=0.5, adjust=False).mean()
                                       / (-d.clip(upper=0)).ewm(alpha=0.5, adjust=False).mean().replace(0, np.nan))).iloc[-1])
            hh = float(c.iloc[-P.get("n", 20) - 1:-1].max())
            volr = float(v.iloc[-1] / v.iloc[-51:-1].mean()) if v.iloc[-51:-1].mean() > 0 else 0
            dvol = float((c * v).tail(20).mean())
            mom = float(c.iloc[-6] / c.iloc[-127] - 1) if len(c) > 127 else np.nan
            if s in self.pos:  # 청산 신호 · 트레일링 갱신
                p = self.pos[s]
                if P.get("trail_atr"):
                    p["stop"] = max(p["stop"], last - P["trail_atr"] * atr)
                p["hi"] = max(p.get("hi", p["entry"]), last)
                held = int((pd.DatetimeIndex(df.index).date > dt.date.fromisoformat(p["opened"][:10])).sum())
                why = None
                if mode == "pullback" and last > ma5:
                    why = f"종가 {f2(last)}가 5일선 {f2(ma5)} 위 — 반등 완료, 차익 실현"
                elif mode == "pullback" and held >= P["max_hold"]:
                    why = f"{held}거래일 지나도 반등이 약함 — 시간 손절"
                elif mode != "pullback" and last < (ma20 if P["exit_ma"] == 20 else ma50):
                    why = f"종가 {f2(last)}가 {P['exit_ma']}일선 {f2(ma20 if P['exit_ma'] == 20 else ma50)} 아래 — 추세 꺾임"
                elif P.get("exit_mkt") and not on:
                    why = f"{mtxt} — 시장 필터 꺼짐, 현금으로"
                if why:
                    sells.append(dict(sym=s, why=why))
                continue
            if not (dvol >= P["min_dvol"] and last >= 5 and ok(atr) and ok(ma200)):
                continue
            score = -atr / last if P.get("rank") == "lowvol" else (mom if ok(mom) else -9)
            mtag = f", 6개월 {pc(mom, 0)}" if ok(mom) else ""
            if mode == "pullback":
                trend = last > ma200 and last > ma50
                hit = trend and rsi2 < P["rsi"]
                why = f"상승 추세(50·200일선 위)에서 RSI(2) {rsi2:.0f} — 단기 과매도, 5일선 {f2(ma5)} 회복까지 반등 노림"
                gap_txt = f"RSI(2) {rsi2:.0f} (필요 {P['rsi']} 미만)"
                closeness = 100 - (rsi2 - P["rsi"])
            elif mode == "trendpull":
                trend = last > ma50 > ma200
                touch = float(lo.iloc[-1]) <= ma20 * (1 + P["tol"])
                hit = trend and touch and last > ma20 and last > prev
                why = f"정배열 추세에서 20일선 {f2(ma20)}까지 눌렸다가 위에서 반등 마감 (종가 {f2(last)}, 전일 대비 {pc(last / prev - 1)})"
                gap_txt = (f"20일선까지 {(float(lo.iloc[-1]) / ma20 - 1) * 100:.1f}% 위에서 멈춤" if not touch else
                           f"20일선 닿았지만 {'20일선 아래 마감' if last <= ma20 else '하락 마감'}")
                closeness = 100 - max(0.0, (float(lo.iloc[-1]) / ma20 - 1 - P["tol"]) * 1000)
            else:
                trend = last > ma50 > ma200
                hit = trend and last > hh and volr >= P["volr"]
                why = f"20일 고점 {f2(hh)} 돌파 (종가 {f2(last)}), 거래량 {volr:.1f}배, 50·200일선 위"
                gap_txt = (f"20일 고점 돌파했지만 거래량 {volr:.1f}배 (필요 {P['volr']}배)" if last > hh
                           else f"20일 고점까지 {(hh / last - 1) * 100:.1f}% · 거래량 {volr:.1f}배")
                closeness = 100 - max(0.0, hh / last - 1) * 1000
            stat["trend"] += trend
            if hit:
                stat["sig"] += 1
                cands.append(dict(sym=s, mom=score, atr=atr, close=last, why=why + mtag, src=src))
            elif trend:
                near.append(dict(sym=s, setup=SETUP_OF[mode], score=round(min(99, max(0, closeness))), ok=False, src=src, why=gap_txt))
        cands.sort(key=lambda x: -x["mom"])
        if not on:
            cands = []
        free = P["max_pos"] - len(self.pos) + len(sells)
        buys = cands[:max(free, 0)]
        self.orders = dict(**{"for": next_open(dt.datetime.combine(day, dt.time(17), NY)).date().isoformat()},
                           buy=buys, sell=sells, done=False)
        near.sort(key=lambda w: -w["score"])
        self.watch = [dict(sym=b["sym"], setup=SETUP_OF[mode], score=100, ok=True, src=b["src"], why=b["why"]) for b in buys] + near[:5 - len(buys)]
        self.sig_day = day.isoformat()
        for x in sells:
            self.log("decide", f"{x['sym']} 내일 시가에 매도 — {x['why']}.", sym=x["sym"])
        for x in buys:
            self.log("decide", f"{x['sym']} 내일 시가에 매수 — {x['why']}. " + (f"손절은 시가 − {P['stop_atr']:g} ATR(≈{f2(P['stop_atr'] * x['atr'])})."
                     if P.get("stop_atr") else f"손절 없이 비중 {100 / P['max_pos']:.0f}%, 최대 {P['max_hold']}거래일."), sym=x["sym"])
        skipped = cands[len(buys):]
        if skipped:
            self.log("pass", f"신호는 더 있지만 자리가 없음 ({len(self.pos)}/{P['max_pos']}): "
                             + ", ".join(x["sym"] for x in skipped[:5]) + (" — 변동성 낮은 순으로 골랐어." if P.get("rank") == "lowvol" else " — 6개월 수익률 높은 순으로 골랐어."))
        if not buys and on:
            self.log("think", f"오늘 매수 신호 없음 — 후보 {len(pool)}개 중 추세 조건 통과 {stat['trend']}개, 신호 0개. "
                              + ("가장 가까운 후보: " + " · ".join(f"{w['sym']} ({w['why']})" for w in near[:3]) if near else "추세 조건을 통과한 종목이 없어."))
        return True

    # ── 개장: 시가 주문 실행 ──
    def at_open(self, now: dt.datetime, bars: dict):
        o = self.orders
        if o.get("done") or o.get("for") != now.date().isoformat():
            return
        first_bar = lambda sym: self.first_bar(sym, now, bars)
        for x in o["sell"]:
            if x["sym"] in self.pos:
                b = first_bar(x["sym"])
                if b is None:
                    return  # 아직 시가 데이터가 없음 — 다음 틱에
                self.sell(x["sym"], self.pos[x["sym"]]["shares"], float(b["Open"]), x["why"].split(" — ")[-1], now, final=True)
        eq = self.cash + sum(p["shares"] * self.price(s, {}, p["entry"]) for s, p in self.pos.items())
        for x in o["buy"]:
            if x["sym"] in self.pos or len(self.pos) >= self.P["max_pos"]:
                continue
            b = first_bar(x["sym"])
            if b is None:
                return
            px = float(b["Open"]) * (1 + SLIP)
            rot = self.P.get("mode") == "rotation"
            if rot or not self.P.get("stop_atr"):  # 모멘텀·눌림목: 종목당 1/N 비중, 손절 없음 (순위 밖 교체 / 5일선 회복·시간 청산)
                stop = 0.0
                shares = int(min(eq * x.get("w", 1 / self.P["max_pos"]), self.cash - self.fee) / px)
            else:
                stop = px - self.P["stop_atr"] * x["atr"]
                shares = int(min(eq * self.P["risk"] / (px - stop), eq / self.P["max_pos"] / px, (self.cash - self.fee) / px))
            if shares < 1:
                self.log("pass", f"{x['sym']} 살 현금이 부족해서 건너뜀.", sym=x["sym"])
                continue
            self.cash -= shares * px + self.fee
            self.pos[x["sym"]] = dict(shares=shares, init=shares, entry=px, stop=stop, stop0=stop, t1=px, t2=px, hi=px,
                                      setup=SETUP_OF[self.P.get("mode", "breakout")], src=x.get("src", "관심종목"), opened=now.isoformat(),
                                      checked=(b.name - pd.Timedelta(minutes=1)).isoformat(), half=True, realized=-self.fee,
                                      sold=0, sold_val=0.0, mfe=px, mae=px, why=x["why"], score=100,
                                      feat=dict(mkt_score=np.nan, rvol=np.nan, rs=np.nan, atr_pct=x["atr"] / px if x["atr"] else np.nan, gap=np.nan))
            self.day["trades"] += 1
            self.log("buy", f"{x['sym']} {shares}주 시가 매수 @ {f2(px)} — {x['why']}. " + (
                     f"비중 {x['w'] * 100:.0f}%, 손절 없음 — 다음 달 첫 거래일에 순위 밖이면 교체." if rot else
                     f"비중 {100 / self.P['max_pos']:.0f}%, 손절 없음 — 5일선 위로 마감하면 팔고, {self.P['max_hold']}거래일 안에 반등 없으면 정리."
                     if not self.P.get("stop_atr") else
                     f"손절 {f2(stop)} (최대 손실 ${shares * (px - stop):,.0f}). 목표는 없고, 추세가 꺾일 때까지 들고 감."),
                     sym=x["sym"], price=px, shares=shares)
        o["done"] = True

    # ── 장중: 손절만 감시 ──
    def watch_stops(self, now: dt.datetime, bars: dict):
        for s in list(self.pos):
            p, df = self.pos[s], bars.get(s)
            if df is None:
                continue
            m = mins(df.index)
            new = df[(df.index > pd.Timestamp(p["checked"])) & (m >= 570) & (m < 960)]
            for ts, b in new.iterrows():
                p["checked"] = ts.isoformat()
                p["mfe"], p["mae"] = max(p["mfe"], b["High"]), min(p["mae"], b["Low"])
                if b["Low"] <= p["stop"]:
                    why = "손절선 도달" if p["stop"] < p["entry"] else "트레일링 스탑 도달 — 수익 지킴"
                    self.sell(s, p["shares"], min(b["Open"], p["stop"]), f"{why} ({f2(p['stop'])})", now, final=True)
                    break

    def run_tick(self, now: dt.datetime, today: dt.date, m: int, snaps: dict, bars: dict):
        cm = close_min(today)
        for s in [s for s, p in self.pos.items() if p.get("setup") not in SETUP_OF.values()]:  # v6 전환 때 남은 단타 포지션
            self.sell(s, self.pos[s]["shares"], self.price(s, snaps, self.pos[s]["entry"]), "단타 → 스윙 전환 정리", now, final=True)
        last_done = today if m >= cm + 5 else prev_trading_day(today)
        if self.sig_day != last_done.isoformat() and (m >= cm + 5 or m < 570):
            if self.signals(last_done, snaps) and m >= cm + 5 and not self.day.get("reviewed"):
                self.review(snaps, today)
        if m < 570:
            if not self.day["planned"] and m >= 480:
                self.log("plan", f"오늘 계획 — {self.P['motto']}\n보유: {self.held_txt(snaps)}.\n"
                                 + (f"시가 주문: {self.order_txt()}." if self.order_txt() else "오늘 시가 주문 없음 — 보유 종목 손절선만 지킴."))
                self.day["planned"] = True
            return self.think("준비", f"개장 전 — {self.order_txt() or '시가 주문 없음'}. 보유: {self.held_txt(snaps)}.", "pre" + self.order_txt(),
                              f"개장 전 — {self.order_txt() or '시가 주문 없음'}")
        if m < cm:
            self.at_open(now, bars)
            self.watch_stops(now, bars)
            mode = self.P.get("mode")
            rot = mode == "rotation"
            what, tail, hold = MODE_TXT.get(mode, MODE_TXT["breakout"])
            body = f"보유: {self.held_txt(snaps)}." if self.pos else (
                "보유 없음 — 장 마감 후 모멘텀 순위를 보고 내일 시가에 첫 편입." if rot else f"보유 없음 — 장 마감 후 {what}을 다시 볼게.")
            return self.think("보유" if self.pos else "관망", body + tail, "day" + ",".join(self.pos),
                              f"{len(self.pos)}종목 보유 — {hold}" if self.pos else f"관망 — 장 마감 후 {what} 점검")
        nxt = self.order_txt()
        self.think("보유" if self.pos else "마감", (f"장 끝 — 일봉 신호 점검 완료. 내일 {nxt}." if self.sig_day == today.isoformat() and nxt else
                   "장 끝 — 일봉 신호 점검 완료. 내일 시가 주문 없음." if self.sig_day == today.isoformat() else
                   "장 끝 — 오늘 일봉이 올라오면 신호를 점검할게.") + f" 보유: {self.held_txt(snaps)}.", "close" + str(self.sig_day) + nxt,
                   f"장 끝 — 내일 {nxt}" if self.sig_day == today.isoformat() and nxt else
                   (f"장 끝 — 주문 없음 · 가장 가까운 {near[0]['sym']}: {near[0]['why']}" if (near := [w for w in self.watch if not w.get("ok")])
                    and self.P.get("mode") not in ("rotation", "desk") else "장 끝 — 내일 주문 없음") if self.sig_day == today.isoformat() else "장 끝 — 일봉 대기")

    def review(self, snaps: dict, today: dt.date, *_):
        eq = self.equity(snaps)
        pnl = eq - self.day["start"]
        L = [f"오늘 결과: {money(pnl)} ({pc(pnl / self.day['start'], 2)}) — 보유 {len(self.pos)}종목, 오늘 신규 {self.day['trades']}건.",
             f"보유: {self.held_txt(snaps)}.",
             f"내일 시가: {self.order_txt() or '주문 없음'}."]
        self.log("review", "\n".join(L))
        append_csv(DATA / "days.csv", DAY_COLS, dict(date=today.isoformat(), trader=self.key, rules=RULES,
                   start_equity=round(self.day["start"], 2), end_equity=round(eq, 2), pnl=round(pnl, 2), trades=self.day["trades"],
                   wins=self.day["wins"], losses=self.day["losses"], mkt_score="", summary=L[0]))
        self.day["reviewed"] = True

    def react(self, it: dict, held: set):
        dn = [s for s, v in it["imp"].items() if v <= -0.5 and s in held]
        if dn:
            self.log("news", f"뉴스 \"{it['head'][:90]}\" → 보유 중인 {', '.join(dn)}에 악재. 스윙이라 장중엔 손절선({', '.join(f2(self.pos[s]['stop']) for s in dn)})만 지킴.",
                     sym=dn[0])


class MomentumTrader(SwingTrader):
    """모멘텀 (아이작) — swing_lab 모멘텀형과 같은 규칙: 매달 마지막 거래일 장 마감 후 6개월 수익률(최근 1주 제외) 상위 N종목을 골라
    다음 달 첫 거래일 시가에 갈아탐. 50일선 위·거래대금 $20M 이상·6개월 플러스만. 종목당 1/N, 손절 없음.
    (백테스트와 한 가지 다른 점: 처음 켰을 때는 월말을 기다리지 않고 바로 편입)"""

    def signals(self, day: dt.date, snaps: dict):
        P = self.P
        F = self.daily_bars(day)
        if F is None:
            return False
        nxt = next_open(dt.datetime.combine(day, dt.time(17), NY)).date()
        rebal = nxt.month != day.month or not self.orders.get("rebal_day")
        rows = []
        for s, df in F.items():
            if s == "SPY" and s not in WATCHLIST:
                continue
            c, v = df["Close"], df["Volume"]
            last = float(c.iloc[-1])
            if s in self.pos:
                self.pos[s]["hi"] = max(self.pos[s].get("hi", self.pos[s]["entry"]), last)
            if len(c) <= 127:
                continue
            mom = float(c.iloc[-6] / c.iloc[-127] - 1)
            okk = last > float(c.tail(50).mean()) and float((c * v).tail(20).mean()) >= P["min_dvol"] and last >= 5 and mom > 0
            if okk and s in WATCHLIST:
                rows.append(dict(sym=s, mom=mom))
        rows.sort(key=lambda r: -r["mom"])
        rank = {r["sym"]: k + 1 for k, r in enumerate(rows)}
        target = [r["sym"] for r in rows[:P["max_pos"]]]
        self.watch = [dict(sym=r["sym"], setup="MOM", score=round(r["mom"] * 100), ok=r["sym"] in target, src="관심종목",
                           why=f"{rank[r['sym']]}위 · 6개월 {pc(r['mom'], 0)}" + (" · 편입 대상" if r["sym"] in target else ""))
                      for r in rows[:8]]
        self.sig_day = day.isoformat()
        if not rebal:
            self.orders = dict(**{"for": nxt.isoformat()}, buy=[], sell=[], done=False, rebal_day=self.orders.get("rebal_day"))
            return True
        sells = [dict(sym=s, why=f"6개월 수익률 순위 밖 ({rank.get(s, '-')}위) — 갈아타기") for s in self.pos if s not in target]
        buys = [dict(sym=s, w=1 / P["max_pos"], atr=0, why=f"6개월 수익률 {pc(rows[rank[s] - 1]['mom'], 0)} — {rank[s]}위")
                for s in target if s not in self.pos]
        self.orders = dict(**{"for": nxt.isoformat()}, buy=buys, sell=sells, done=False, rebal_day=nxt.isoformat())
        self.log("think", f"리밸런싱 — 6개월 수익률 상위 {P['max_pos']}: " + ", ".join(f"{s} {pc(rows[rank[s] - 1]['mom'], 0)}" for s in target)
                 + (f". 유지: {', '.join(s for s in target if s in self.pos)}" if any(s in self.pos for s in target) else "") + ".")
        for x in sells:
            self.log("decide", f"{x['sym']} 내일 시가에 매도 — {x['why']}.", sym=x["sym"])
        for x in buys:
            self.log("decide", f"{x['sym']} 내일 시가에 매수 — {x['why']}. 비중 {100 / P['max_pos']:.0f}%.", sym=x["sym"])
        return True


class DeskTrader(SwingTrader):
    """퀀트형 (알파) — Alpaca 모의계좌에 넣은 퀀트 데스크(paper_desk.py)를 가상 계좌로 그대로 따라 함.
    장 마감 후 paper_desk.py virtual(Alpaca 접속 없이 같은 신호·같은 주문 규칙)로 주문 목록을 받아 다음 날 시가에 체결.
    시작 자금 $20,000 — Alpaca 계좌($100,000)와 같은 비중이 되도록 소수점 주식(0.001주 단위)으로 삼.
    지정가 ±2% 밖으로 갭이 나면 그 주문은 건너뛰고 다음 날 다시 판단. 고점 대비 −15%면 신규 매수 중단. 손절 없음."""
    DD_HALT, RETRY = 0.15, 900

    def __init__(self, key: str, st: dict | None):
        super().__init__(key, st)
        self.peak = (st or {}).get("peak", self.cash)
        self.try_at = 0.0

    def dump(self) -> dict:
        return super().dump() | dict(peak=self.peak)

    def held_txt(self, snaps: dict) -> str:
        if not self.pos:
            return "보유 없음"
        val = {s: p["shares"] * self.price(s, snaps, p["entry"]) for s, p in self.pos.items()}
        eq = self.cash + sum(val.values())
        top = sorted(val, key=val.get, reverse=True)[:5]
        return (" · ".join(f"{s} {val[s] / eq * 100:.0f}%" for s in top)
                + (f" 외 {len(val) - 5}종목" if len(val) > 5 else "") + f" (현금 {self.cash / eq * 100:.0f}%)")

    def order_txt(self) -> str:
        o = self.orders
        if o.get("done") or not (o["buy"] or o["sell"]):
            return ""
        return f"시가 지정가 주문 매도 {len(o['sell'])}건 · 매수 {len(o['buy'])}건"

    def signals(self, day: dt.date, snaps: dict):
        if time.time() - self.try_at < self.RETRY:
            return False
        self.try_at = time.time()
        eq = self.equity(snaps)
        self.peak = max(self.peak, eq)
        buys_ok = eq >= self.peak * (1 - self.DD_HALT)
        arg = json.dumps(dict(day=day.isoformat(), equity=eq, held={s: p["shares"] for s, p in self.pos.items()}, buys_ok=buys_ok))
        try:
            r = subprocess.run([sys.executable, str(APPS / "paper_desk.py"), "virtual", arg], capture_output=True, text=True,
                               timeout=900, cwd=str(APPS))
            v = json.loads((APPS / "journal" / "paper" / "virtual.json").read_text()) if r.returncode == 0 else {}
        except Exception as e:
            self.log("think", f"퀀트 데스크 계산 실패 ({type(e).__name__}) — 15분 뒤 다시.")
            return False
        if r.returncode == 2 or v.get("day") != day.isoformat():  # 2 = 그날 일봉이 아직 안 올라옴
            if r.returncode not in (0, 2):
                self.log("think", f"퀀트 데스크 계산 오류({r.returncode}): {(r.stderr.strip().splitlines() or ['출력 없음'])[-1][:160]} — 15분 뒤 다시.")
            return False
        sleeves, ko = v.get("sleeves", {}), v.get("names", {})
        of = lambda s: " + ".join(ko.get(k, k) for k, w in sleeves.items() if s in w) or "목표 비중 밖"
        mk = lambda o: dict(sym=o["sym"], qty=o["qty"], limit=o["limit"], ref=o["ref"], w=o["weight"],
                            why=f"{of(o['sym'])} — 목표 비중 {o['weight'] * 100:.1f}%")
        sells, buys = [mk(o) for o in v["orders"] if o["side"] == "sell"], [mk(o) for o in v["orders"] if o["side"] == "buy"]
        self.orders = dict(**{"for": next_open(dt.datetime.combine(day, dt.time(17), NY)).date().isoformat()}, buy=buys, sell=sells, done=False)
        tg = sorted(v["targets"].items(), key=lambda kv: -kv[1])
        self.watch = [dict(sym=s, setup="DESK", score=round(w * 100), ok=True, src="퀀트 데스크", why=f"목표 {w * 100:.1f}% — {of(s)}") for s, w in tg[:8]]
        self.sig_day = day.isoformat()
        if not buys_ok:
            self.log("think", f"계좌 ${eq:,.0f}가 고점 ${self.peak:,.0f} 대비 −{self.DD_HALT:.0%} 아래 — 신규 매수 중단, 매도만.")
        self.log("think", "오늘 목표 비중 — " + " / ".join(
            f"{ko.get(k, k)}: {', '.join(f'{s} {x * 100:.0f}%' for s, x in sorted(w.items(), key=lambda kv: -kv[1])[:4]) or '현금'}"
            + (f" 외 {len(w) - 4}" if len(w) > 4 else "") for k, w in sleeves.items()) + ". (전략별 몫 20%씩, 괄호 안은 그 전략 안에서의 비중)")
        for side, xs in (("매도", sells), ("매수", buys)):
            if xs:
                self.log("decide", f"내일 시가 {side} {len(xs)}건 (지정가 ±2%): " + ", ".join(f"{x['sym']} {x['qty']:g}주" for x in xs[:12])
                         + (f" 외 {len(xs) - 12}건" if len(xs) > 12 else "") + ".", sym=xs[0]["sym"])
        if not sells and not buys:
            self.log("think", "목표 비중과 지금 보유가 거의 같아 내일 주문 없음.")
        return True

    def at_open(self, now: dt.datetime, bars: dict):
        """Alpaca의 '당일 지정가 주문'처럼 체결: 시가가 지정가 안이면 시가에, 밖이면 장중에 가격이 지정가까지 돌아올 때 지정가에.
        장 끝까지 안 닿으면 그 주문은 사라지고 장 마감 후 다시 판단. 매수는 매도의 시가가 확인된 뒤에 (현금 확보, 09:45까지만 기다림)."""
        o = self.orders
        if o.get("done") or o.get("for") != now.date().isoformat():
            return
        late = now.hour * 60 + now.minute >= 585
        for side, xs in (("sell", o["sell"]), ("buy", o["buy"])):
            for x in xs:
                if x.get("ok"):
                    continue
                df = bars.get(x["sym"])
                if df is not None:
                    m = mins(df.index)
                    df = df[(np.array(df.index.date) == now.date()) & (m >= 570) & (m < 960)]
                    df = df[df.index > pd.Timestamp(x["seen"])] if x.get("seen") else df
                for ts, b in (df.iterrows() if df is not None else ()):
                    first, x["seen"] = not x.get("seen"), ts.isoformat()
                    op = float(b["Open"])
                    inside = op <= x["limit"] if side == "buy" else op >= x["limit"]
                    touch = float(b["Low"]) <= x["limit"] if side == "buy" else float(b["High"]) >= x["limit"]
                    if not (inside or touch):
                        if first:
                            self.log("pass", f"{x['sym']} 시가 {f2(op)}가 지정가 {f2(x['limit'])} 밖 (전일 종가 {f2(x['ref'])} 대비 갭 {pc(op / x['ref'] - 1)}) — "
                                             f"오늘 장중에 지정가까지 오면 {'사고' if side == 'buy' else '팔고'}, 안 오면 장 마감 후 다시 판단.", sym=x["sym"])
                        continue
                    x["ok"] = True
                    px = op if inside else x["limit"]
                    if side == "sell" and x["sym"] in self.pos:
                        have = self.pos[x["sym"]]["shares"]
                        n = have if x["qty"] >= have - 0.0005 else x["qty"]
                        self.sell(x["sym"], n, px, f"비중 조정 — {x['why']}", now, final=n == have)
                    elif side == "buy":
                        self.fill_buy(x, px, now, b)
                    break
            if side == "sell" and not late and any(not x.get("seen") for x in xs):
                return
        o["done"] = all(x.get("ok") for x in o["sell"] + o["buy"])

    def fill_buy(self, x: dict, op: float, now: dt.datetime, b):
        px = op * (1 + SLIP)
        n = min(x["qty"], math.floor((self.cash - self.fee) / px * 1000) / 1000)  # 소수점 주식 (0.001주 단위)
        if n <= 0:
            return self.log("pass", f"{x['sym']} 살 현금이 부족해서 건너뜀.", sym=x["sym"])
        self.cash -= n * px + self.fee
        p = self.pos.get(x["sym"])
        if p:  # 추가 매수 — 평균 단가로
            p.update(entry=(p["entry"] * p["shares"] + px * n) / (p["shares"] + n), shares=p["shares"] + n, init=p["init"] + n,
                     realized=p["realized"] - self.fee, why=x["why"])
        else:
            self.pos[x["sym"]] = dict(shares=n, init=n, entry=px, stop=0.0, stop0=0.0, t1=px, t2=px, hi=px, setup="DESK", src="퀀트 데스크",
                                      opened=now.isoformat(), checked=(b.name - pd.Timedelta(minutes=1)).isoformat(), half=True,
                                      realized=-self.fee, sold=0, sold_val=0.0, mfe=px, mae=px, why=x["why"], score=round(x["w"] * 100),
                                      feat=dict(mkt_score=np.nan, rvol=np.nan, rs=np.nan, atr_pct=np.nan, gap=op / x["ref"] - 1))
        self.day["trades"] += 1
        self.log("buy", f"{x['sym']} {n:g}주 시가 매수 @ {f2(px)} — {x['why']}.", sym=x["sym"], price=px, shares=n)


def scalp_pick(F: dict, held: str | None, P: dict, mkt_on: bool) -> tuple[str | None, str | None, str]:
    """10분 단타 판단 (scalp_lab.py의 dip·keep·mkt 규칙과 같은 정의). F = 종목별 지표(z·vw·day·rv·liquid).
    돌려주는 값: (10분 뒤에도 들고 있을 종목, 새로 살 종목, 이유)."""
    ok_ = lambda f: bool(f and f["liquid"] and f["z"] <= -P["z_in"] and f["vw"] > 0 and f["day"] > 0 and ok(f["rv"]) and f["rv"] >= P["rvol"])
    cands = sorted((f for f in F.values() if ok_(f)), key=lambda f: f["z"])
    pick = cands[0]["sym"] if cands and mkt_on else None
    h = F.get(held) if held else None
    if held and h and h["z"] > -P["z_keep"] and h["vw"] > 0:
        return held, None, f"{held} 직전 10분 {pc(h['r10'], 2)} (z {h['z']:+.1f}) · VWAP 위 — 10분 더 보유"
    if held and pick == held:
        return held, None, f"{held} 다시 급락했지만 매수 조건을 또 채움 — 10분 더 보유"
    why = ""
    if held:
        why = (f"{held} 지표를 못 읽음 — 정리" if not h else f"{held} VWAP 아래로 내려감 — 정리" if h["vw"] <= 0
               else f"{held} 10분 새 {pc(h['r10'], 2)} (z {h['z']:+.1f}) 더 빠짐 — 정리")
    return None, pick, why


class ScalpTrader(Trader):
    """단타형 (번개) — 10분마다(09:40~15:40 ET) 한 번 판단. 규칙은 scalp_lab.py에서 학습 기간에 고르고 확인·시험 기간에도 플러스였던 것:
    오늘 오르는 종목(전일 대비 +, VWAP 위)이 거래량 3배로 10분 급락(z ≤ −1.5) → 가장 많이 빠진 한 종목을 계좌 전부로 매수.
    10분 뒤: z > −1 이고 VWAP 위면 10분 더, 아니면 매도. 새로 살 땐 SPY가 VWAP 위여야 함. 손절선 없음(10분 뒤 재판단이 손절), 15:50 전량 정리."""

    idle_txt = "매매 없음 — 10분 급락 + 거래량 3배 조건이 한 번도 안 맞았어. 이 규칙은 원래 한 주에 한 번쯤만 삼."

    def __init__(self, key: str, st: dict | None):
        super().__init__(key, st)
        self.vprof: dict = {}

    def plan(self, snaps: dict, mk: dict, disc: dict, today: dt.date, top_news: list | None = None):
        self.log("plan", f"오늘 계획 — {self.P['motto']}\n프리마켓 시장 점수 {mk['score']} ({mk['bias']}). "
                         f"평소 거래량 자료가 있는 종목 {len(self.vprof)}개. 백테스트로는 한 주에 한 번쯤만 조건이 맞아 — 안 사는 날이 대부분이야.")
        self.day["planned"] = True

    def react(self, it: dict, held: set):
        dn = [s for s, v in it["imp"].items() if v <= -0.5 and s in held]
        if dn:
            self.log("news", f"뉴스 \"{it['head'][:90]}\" → 보유 중인 {', '.join(dn)}에 악재. 다음 10분 판단 때 꺾였으면 바로 정리.", sym=dn[0])

    def feats(self, sym: str, snaps: dict, bars: dict, edge: int, today: dt.date) -> dict | None:
        """edge(판단 시각, 분) 직전 10분의 지표. 데이터가 늦거나 모자라면 None."""
        a, df = snaps.get(sym), bars.get(sym)
        if not a or df is None or not ok(a.get("atr")) or a["atr"] <= 0 or a["pdc"] <= 0:
            return None
        m = mins(df.index)
        T_ = df[(np.array(df.index.date) == today) & (m >= 570) & (m < edge)]
        if len(T_) < 5 or int(mins(T_.index)[-1]) < edge - 1:  # 직전 1분봉까지 와 있어야 10분이 다 찬 것 (백테스트는 항상 꽉 찬 10분)
            return None
        w = T_[mins(T_.index) >= edge - 10]
        if len(w) < 5:
            return None
        last, k = float(w["Close"].iloc[-1]), (edge - 580) // 5
        r10 = last / float(w["Open"].iloc[0]) - 1
        vw, _ = vwap_bands(T_)
        prof = self.vprof.get(sym)
        avg = float(prof[k] + prof[k + 1]) if prof is not None and k + 1 < len(prof) else 0.0
        return dict(sym=sym, last=last, r10=r10, z=r10 / (a["atr"] / a["pdc"] * K10), vw=last / float(vw.iloc[-1]) - 1, day=last / a["pdc"] - 1,
                    rv=float(w["Volume"].sum()) / avg if avg > 0 else np.nan, liquid=a["dvol"] >= self.P["min_dvol"] and a["pdc"] >= 5)

    def px_at(self, sym: str, snaps: dict, bars: dict, edge: int, today: dt.date, default: float) -> float:
        """판단 시각(edge) 이후 첫 1분봉의 시가 — 백테스트의 '다음 봉 시가 체결'에 맞춤. 그 봉이 아직 없으면 지금 가격."""
        df = bars.get(sym)
        if df is not None:
            m = mins(df.index)
            nxt = df[(np.array(df.index.date) == today) & (m >= edge) & (m < 960)]
            if len(nxt):
                return float(nxt["Open"].iloc[0])
        return self.price(sym, snaps, default)

    def step(self, now: dt.datetime, snaps: dict, bars: dict, mk: dict, disc: dict, news=None):
        P, m, today = self.P, now.hour * 60 + now.minute, now.date()
        cm = close_min(today)
        for s, p in self.pos.items():
            last = self.price(s, snaps, p["entry"])
            p["mfe"], p["mae"] = max(p["mfe"], last), min(p["mae"], last)
        held = next(iter(self.pos), None)
        pos_txt = f"{held} {pc(self.price(held, snaps, self.pos[held]['entry']) / self.pos[held]['entry'] - 1, 2)}" if held else ""
        if m >= cm - 10:
            for s in list(self.pos):
                self.sell(s, self.pos[s]["shares"], self.price(s, snaps, self.pos[s]["entry"]), "장 마감 전 청산 (단타 원칙)", now, final=True)
            return self.think("마감", "마감 10분 전 — 오늘 매매 끝. 장 끝나면 회고를 쓸게.", "close", "오늘 매매 끝")
        edge = 570 + (m - 570) // 10 * 10
        nxt = edge + 10 if edge + 10 <= cm - 20 else None
        idle = lambda: self.think("보유" if held else "탐색", (f"보유: {pos_txt}. " if held else "") + (
            f"다음 판단 {hm(nxt)} ET ({ams(nxt, today)} 암스테르담)." if nxt else "오늘 새 매수는 끝 — 마감 10분 전에 정리."),
            f"idle{held}{nxt}", (f"{held} 보유 — " if held else "대기 — ") + (f"{ams(nxt, today)} 재판단" if nxt else "마감 전 정리"))
        if edge < 580 or edge > cm - 20 or self.day.get("slot") == edge:
            return idle()
        if m - edge > 4:  # 판단 시각을 놓침 (재시작 등) — 다음 10분에
            self.day["slot"] = edge
            return idle()
        spy = self.feats("SPY", snaps, bars, edge, today)
        if not spy:
            return idle()  # 1분봉이 아직 안 옴 — 다음 틱(30초 뒤)에 다시
        self.day["slot"] = edge
        F = {s: self.feats(s, snaps, bars, edge, today) for s in dict.fromkeys(MARKET + WATCHLIST + list(self.pos))}
        keep, pick, why = scalp_pick(F, held, P, spy["vw"] > 0)
        near = sorted((f for f in F.values() if f and f["liquid"] and f["vw"] > 0 and f["day"] > 0 and f["z"] < 0), key=lambda f: f["z"])[:5]
        self.watch = [dict(sym=f["sym"], setup="DIP10", score=round(min(99, max(0, -f["z"] / P["z_in"] * 60 + (min(f["rv"], P["rvol"]) / P["rvol"] * 40 if ok(f["rv"]) else 0)))),
                           ok=f["sym"] == pick, src="관심종목" if f["sym"] in CAT_OF else "지수 ETF",
                           why=f"10분 {pc(f['r10'], 2)} (z {f['z']:+.1f}, 필요 −{P['z_in']}) · 거래량 " + (f"{f['rv']:.1f}배" if ok(f["rv"]) else "자료 없음") + f" (필요 {P['rvol']:g}배)")
                      for f in near]
        if held and not keep:
            self.sell(held, self.pos[held]["shares"], self.px_at(held, snaps, bars, edge, today, self.pos[held]["entry"]), why, now, final=True)
        elif keep:
            return self.think("보유", f"{hm(edge)} ET 판단 — {why}.", f"keep{edge}", f"{held} 보유 — 10분 더")
        if pick and not self.pos and self.day["trades"] < P["max_trades"]:
            f, a = F[pick], snaps[pick]
            px = self.px_at(pick, snaps, bars, edge, today, a["last"]) * (1 + SLIP)
            n = int((self.cash - self.fee) / px)
            if n >= 1:
                txt = (f"오늘 {pc(f['day'])} 오르는 중 · VWAP 위에서 10분 새 {pc(f['r10'], 2)} 급락 (평소 10분 변동의 {-f['z']:.1f}배), "
                       f"거래량 {f['rv']:.1f}배 · SPY는 VWAP 위")
                self.cash -= n * px + self.fee
                self.pos[pick] = dict(shares=n, init=n, entry=px, stop=0.0, stop0=px * (1 - a["atr"] / a["pdc"] * K10), t1=px, t2=px, setup="DIP10",
                                      src="관심종목" if pick in CAT_OF else "지수 ETF", opened=now.isoformat(), checked=a["t"], half=True,
                                      realized=-self.fee, sold=0, sold_val=0.0, mfe=px, mae=px, why=txt, score=round(-f["z"] * 10),
                                      feat=dict(mkt_score=mk["score"], rvol=f["rv"], rs=a["rs"], atr_pct=a["atr_pct"], gap=a["gap"]))
                self.day["trades"] += 1
                self.log("buy", f"{pick} {n}주 매수 @ {f2(px)} — {txt}. 손절선 없음 — {hm(edge + 10)} ET에 다시 보고 버티면 10분 더, 꺾이면 정리.",
                         sym=pick, price=px, shares=n)
                return self.think("보유", f"{pick} 샀어. {txt}. 10분 뒤 다시 판단.", "buy" + pick + str(edge), f"{pick} 매수함")
        top = near[0] if near else None
        body = (f"{hm(edge)} ET 판단 — " + (why + ". " if why else "") + ("SPY가 VWAP 아래라 새로 안 삼. " if spy["vw"] <= 0 else "")
                + (f"조건에 가장 가까운 건 {top['sym']}: {self.watch[0]['why']}." if top else "오르는 종목 중 10분 눌림이 없음."))
        self.think("탐색", body, f"scan{edge}", "대기 — " + ("시장 약함 (SPY VWAP 아래)" if spy["vw"] <= 0 else f"가까운 후보 {top['sym']}" if top else "눌림 없음"))

    def review(self, snaps: dict, mk: dict, today: dt.date):
        trades = [t for t in read_csv(DATA / "trades.csv") if t["trader"] == self.key and t["date"] == today.isoformat()]
        eq = self.cash
        pnl = eq - self.day["start"]
        L = [f"오늘 결과: {money(pnl)} ({pc(pnl / self.day['start'], 2)}), 거래 {len(trades)}회" + (f" — {self.day['wins']}승 {self.day['losses']}패." if trades else ".")]
        L.append(" / ".join(f"{t['sym']} {t['entry_t']}→{t['exit_t']} ({t['hold_min']}분) {money(float(t['pnl']))}" for t in trades) + "."
                 if trades else self.idle_txt)
        self.log("review", "\n".join(L))
        append_csv(DATA / "days.csv", DAY_COLS, dict(date=today.isoformat(), trader=self.key, rules=RULES,
                   start_equity=round(self.day["start"], 2), end_equity=round(eq, 2), pnl=round(pnl, 2), trades=len(trades),
                   wins=self.day["wins"], losses=self.day["losses"], mkt_score=mk["score"], summary=L[0]))
        self.day["reviewed"] = True


def mrna_hist(sym: str, today: dt.date, keys: tuple, bars: pd.DataFrame | None = None) -> dict:
    """모나용 기준값 — 직전 14거래일 1분봉에서: s1 = 평소 1분 변동폭(1분 로그수익률 표준편차의 평균),
    move[t] = 개장 t분 뒤까지 시가 대비 평균 변동폭. Alpaca 전체 시장 1분봉을 쓰고, 키가 없으면 엔진이 가진 야후 1분봉(며칠치)으로 대신함."""
    df = None
    if keys[0] and keys[1]:
        rows, token = [], None
        end = min(dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20), dt.datetime.combine(today, dt.time(0), NY).astimezone(dt.timezone.utc))
        while True:
            q = dict(symbols=sym, timeframe="1Min", start=(today - dt.timedelta(days=35)).isoformat(), end=end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     limit=10000, feed="sip", adjustment="split", sort="asc")
            if token:
                q["page_token"] = token
            r = requests.get("https://data.alpaca.markets/v2/stocks/bars", params=q, timeout=30,
                             headers={"APCA-API-KEY-ID": keys[0], "APCA-API-SECRET-KEY": keys[1]})
            r.raise_for_status()
            js = r.json()
            rows += (js.get("bars") or {}).get(sym, [])
            token = js.get("next_page_token")
            if not token:
                break
        if rows:
            df = pd.DataFrame(rows).rename(columns=dict(o="Open", c="Close"))
            df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("t"), utc=True)).tz_convert(NY)
    need = 7 if df is not None else 3
    df = df if df is not None else bars
    if df is None:
        raise RuntimeError("1분봉 없음")
    m = mins(df.index)
    df = df[(m >= 570) & (m < 960) & (np.array(df.index.date) < today)]
    s1, mv = [], []
    for _, g in df.groupby(df.index.date):
        if len(g) < 380:
            continue
        c = g["Close"].groupby(mins(g.index) - 570).last().reindex(range(390)).ffill().bfill()
        s1.append(float(np.diff(np.log(c.to_numpy())).std()))
        mv.append(np.abs(c.to_numpy() / float(g["Open"].iloc[0]) - 1))
    if len(s1) < need:
        raise RuntimeError(f"온전한 거래일이 {len(s1)}일뿐")
    return dict(s1=float(np.mean(s1[-14:])), move=np.mean(mv[-14:], axis=0), days=len(s1[-14:]))


def mrna_view(df: pd.DataFrame | None, today: dt.date, m: int) -> dict | None:
    """오늘 정규장에서 '끝난' 1분봉들을 분 번호(0 = 09:30)로 정리. b = 마지막으로 끝난 봉. 빈 분은 직전 종가로 메움."""
    if df is None:
        return None
    mm = mins(df.index)
    T_ = df[(np.array(df.index.date) == today) & (mm >= 570) & (mm < min(m, 960))]
    if len(T_) < 3:
        return None
    k = mins(T_.index) - 570
    b = int(k[-1])
    if b < m - 570 - 3:  # 3분 넘게 늦은 데이터로는 판단하지 않음
        return None
    g = T_.groupby(k)
    C = g["Close"].last().reindex(range(b + 1)).ffill().bfill().to_numpy()
    fill = lambda col, how: getattr(g[col], how)().reindex(range(b + 1)).to_numpy()
    O, H, L = (np.where(np.isnan(x), C, x) for x in (fill("Open", "first"), fill("High", "max"), fill("Low", "min")))
    V = np.nan_to_num(fill("Volume", "sum"))
    cv = np.cumsum(V)
    VW = np.where(cv > 0, np.cumsum((H + L + C) / 3 * V) / np.where(cv > 0, cv, 1), C)
    return dict(b=b, O=O, H=H, L=L, C=C, VW=VW, o0=float(T_["Open"].iloc[0]))


class MrnaTrader(ScalpTrader):
    """MRNA 전문 (모나) — 모더나 한 종목만, 계좌 전부로. 규칙은 mrna_lab.py (일봉 추세에 따라 두 가지 모드):
    추세 모드 (전일 종가 > 50일선): 15분마다, 가격이 '평소 이 시각 변동 범위' 위로 나가 있으면 사고, 그 선이나 VWAP 아래로 내려오면 팖.
    받아먹기 모드 (50일선 아래): 매분 'min(VWAP, 직전 종가) − 5×평소 1분 변동폭'에 매수 지정가를 걸어 둠. 체결되면 +6배에 익절 지정가,
        −16배에 손절, 60분 지나면 정리. 지정가·익절·손절은 Alpaca 실시간 체결가로 몇 초마다 확인 (키가 없으면 30초마다 1분봉으로).
    15:50 ET 전량 정리. 백테스트로 검증을 통과한 규칙이 아님 — 2,300여 개 중 통과가 없어, 기간마다 벌었던 두 방식을 추세로 나눠 쓴 것."""
    SYM = "MRNA"

    def __init__(self, key: str, st: dict | None):
        super().__init__(key, st)
        self.hist: dict = {}
        self.hist_day, self.hist_try, self.tick_px = None, 0.0, None

    def plan(self, snaps: dict, mk: dict, disc: dict, today: dt.date, top_news: list | None = None):
        a = snaps.get(self.SYM)
        mode = ("추세 모드 (전일 종가 {} > 50일선 {})" if a and a["pdc"] > a["sma50d"] else "받아먹기 모드 (전일 종가 {} ≤ 50일선 {})").format(
            f2(a["pdc"]), f2(a["sma50d"])) if a else "모드는 개장 후 결정"
        self.log("plan", f"오늘 계획 — {self.P['motto']}\n오늘은 {mode}." + (f" 프리마켓 {pc(a['chg'])}." if a else ""))
        self.day["planned"] = True

    def review(self, snaps: dict, mk: dict, today: dt.date):
        self.idle_txt = f"매매 없음 — 오늘({'추세' if self.day.get('mode') == 'trend' else '받아먹기'} 모드) 조건이 한 번도 안 맞았어."
        super().review(snaps, mk, today)

    def wants_ticks(self) -> bool:
        """몇 초 간격 실시간 가격이 필요한 상태인지 (걸어 둔 지정가가 있거나, 익절·손절선이 있는 포지션)."""
        return bool(self.day.get("lim")) or any(p.get("tgt") for p in self.pos.values())

    def load_hist(self, today: dt.date, df: pd.DataFrame | None):
        if time.time() - self.hist_try < 600:
            return
        self.hist_try = time.time()
        try:
            self.hist, self.hist_day = mrna_hist(self.SYM, today, alpaca_keys(), df), today
        except Exception as e:
            try:
                self.hist, self.hist_day = mrna_hist(self.SYM, today, (None, None), df), today
                self.log("think", f"Alpaca 1분봉을 못 받아 ({type(e).__name__}) 야후 며칠치로 기준값을 계산했어 — 평소보다 거칠어.")
            except Exception as e2:
                self.hist = {}
                self.log("think", f"기준값(평소 변동폭)을 못 구함 — {type(e2).__name__}: {e2}. 10분 뒤 다시.")

    # ── 체결 ──
    def enter(self, px: float, setup: str, why: str, now: dt.datetime, a: dict, bar: int, limit: bool):
        px = px if limit else px * (1 + SLIP)  # 지정가 체결은 스프레드를 안 냄
        n = int((self.cash - self.fee) / px)
        if n < 1:
            return
        P, u = self.P, px * self.hist["s1"]
        fade = setup == "MFADE"
        self.cash -= n * px + self.fee
        self.pos[self.SYM] = dict(shares=n, init=n, entry=px, stop=px - P["stop"] * u if fade else 0.0, stop0=px - P["stop"] * u,
                                  t1=px + P["tp"] * u, t2=px + P["tp"] * u, tgt=px + P["tp"] * u if fade else None, until=bar + P["hold"] if fade else None,
                                  bar=bar, setup=setup, src="관심종목", opened=now.isoformat(), checked=a["t"], half=True, realized=-self.fee,
                                  sold=0, sold_val=0.0, mfe=px, mae=px, why=why, score=100,
                                  feat=dict(mkt_score=np.nan, rvol=a["rvol"], rs=a["rs"], atr_pct=a["atr_pct"], gap=a["gap"]))
        self.day["trades"] += 1
        self.day["lim"] = None
        self.log("buy", f"{self.SYM} {n}주 {'지정가 ' if limit else ''}매수 @ {f2(px)} — {why}. " + (
            f"익절 지정가 {f2(px + P['tp'] * u)}, 손절 {f2(px - P['stop'] * u)}, 늦어도 {P['hold']}분 뒤 정리." if fade
            else f"{P['step']}분마다 다시 보고, 변동 범위 상단이나 VWAP 아래로 내려오면 팔아."), sym=self.SYM, price=px, shares=n)

    def on_price(self, px: float, now: dt.datetime) -> bool:
        """실시간 체결가 한 건 — 걸어 둔 매수 지정가, 익절 지정가, 손절을 확인. 매매가 있었으면 True."""
        if self.day.get("date") != now.date().isoformat():  # 오늘 첫 틱(new_day) 전에는 어제 상태로 판단하지 않음
            return False
        p, lim = self.pos.get(self.SYM), self.day.get("lim")
        self.tick_px = (px, now.isoformat())
        if p and p.get("tgt"):
            if px <= p["stop"]:
                self.sell(self.SYM, p["shares"], px, f"손절선 도달 ({f2(p['stop'])})", now, final=True)
                return True
            if px > p["tgt"]:
                self.sell(self.SYM, p["shares"], p["tgt"], f"익절 지정가 체결 ({f2(p['tgt'])})", now, final=True, slip=0.0)
                return True
        elif (not p and lim and self.hist and px < lim["px"] and now.hour * 60 + now.minute - 570 == lim["bar"]
              and now.isoformat() >= lim.get("at", "")):  # 그 지정가가 걸려 있던 1분 안에, 건 뒤에 나온 체결만
            a = LAST_SNAP.get(self.SYM)
            if a:
                self.enter(lim["px"], "MFADE", lim["why"], now, a, lim["bar"], limit=True)
                return True
        return False

    def bar_triggers(self, v: dict, a: dict, now: dt.datetime):
        """새로 끝난 1분봉으로 같은 확인 — 실시간 가격이 없거나 놓쳤을 때. 백테스트와 같은 가정(뚫어야 체결, 한 봉에 둘 다 닿으면 손절).
        백테스트와 다른 점: 체결된 그 1분 안의 손절·익절은 실시간 가격으로만 확인함 (백테스트는 그 봉에서 손절만 가능하다고 가정)."""
        for j in range(self.day.get("bar", -1) + 1, v["b"] + 1):
            p, lim = self.pos.get(self.SYM), self.day.get("lim")
            if p and p.get("tgt") and j > p["bar"]:
                if v["L"][j] <= p["stop"]:
                    self.sell(self.SYM, p["shares"], min(v["O"][j], p["stop"]), f"손절선 도달 ({f2(p['stop'])})", now, final=True)
                elif v["H"][j] > p["tgt"]:
                    self.sell(self.SYM, p["shares"], p["tgt"], f"익절 지정가 체결 ({f2(p['tgt'])})", now, final=True, slip=0.0)
            elif not p and lim and lim["bar"] == j and v["L"][j] < lim["px"]:
                self.enter(min(lim["px"], v["O"][j]), "MFADE", lim["why"], now, a, j, limit=True)

    # ── 30초마다: 분 단위 판단 ──
    def step(self, now: dt.datetime, snaps: dict, bars: dict, mk: dict, disc: dict, news=None):
        P, m, today, sym = self.P, now.hour * 60 + now.minute, now.date(), self.SYM
        cm = close_min(today)
        a, df = snaps.get(sym), bars.get(sym)
        LAST_SNAP[sym] = a
        for p in self.pos.values():
            last = self.price(sym, snaps, p["entry"])
            p["mfe"], p["mae"] = max(p["mfe"], last), min(p["mae"], last)
        if m >= cm - 10:
            self.day["lim"] = None
            for s in list(self.pos):
                self.sell(s, self.pos[s]["shares"], self.price(s, snaps, self.pos[s]["entry"]), "장 마감 전 청산 (단타 원칙)", now, final=True)
            return self.think("마감", "마감 10분 전 — 오늘 매매 끝. 장 끝나면 회고를 쓸게.", "close", "오늘 매매 끝")
        if self.hist_day != today:
            self.load_hist(today, df)
        done = now - dt.timedelta(seconds=15)  # 1분봉은 끝나고 15초 지나야 '끝난 봉'으로 봄 (야후 마지막 봉이 늦게 확정됨)
        v = mrna_view(df, today, done.hour * 60 + done.minute) if a else None
        if v is not None and self.hist_day != today and self.pos:  # 기준값을 못 구한 채 들고 있을 수는 없음 (장중 재시작 등)
            self.sell(sym, self.pos[sym]["shares"], a["last"], "기준값(평소 변동폭)을 못 구해 관리할 수 없음 — 정리", now, final=True)
        if v is None or self.hist_day != today:
            self.day["lim"] = None
            return self.think("보유" if self.pos else "관망", "1분봉이나 기준값이 아직 준비 안 됨 — 기다리는 중.", "wait", "데이터 대기")
        mode = self.day.setdefault("mode", "trend" if a["pdc"] > a["sma50d"] else "fade")
        b, s1, c, vw = v["b"], self.hist["s1"], float(v["C"][-1]), float(v["VW"][-1])
        up = lambda t: max(v["o0"], a["pdc"]) * (1 + P["k"] * float(self.hist["move"][t]))
        if mode == "trend":
            t = b - (b - 19) % P["step"] if b >= 19 else None  # 판단하는 봉: 09:49, 10:04, … (15분마다)
            if t is not None and self.day.get("chk", -1) < t:
                self.day["chk"] = t
                ct, line = float(v["C"][t]), max(up(t), float(v["VW"][t]))
                px = self.px_at(sym, snaps, bars, 571 + t, today, a["last"])
                if b - t > 3:
                    pass  # 판단 시각을 놓침 — 다음 15분에
                elif sym in self.pos and ct < line:
                    self.sell(sym, self.pos[sym]["shares"], px, f"종가 {f2(ct)}가 청산선 {f2(line)} (변동 범위 상단·VWAP 중 높은 쪽) 아래 — 추세 꺾임", now, final=True)
                elif sym not in self.pos and t < 360 and ct > up(t) and self.day["trades"] < P["max_trades"]:
                    self.enter(px, "MTREND", f"{hm(571 + t)} ET 종가 {f2(ct)}가 평소 변동 범위 상단 {f2(up(t))} 위 — 오늘은 추세가 붙은 날", now, a, t + 1, limit=False)
            nxt = 571 + (t + P["step"] if t is not None else 19)
            body = (f"추세 모드 (전일 종가 {f2(a['pdc'])} > 50일선 {f2(a['sma50d'])}). 지금 {f2(c)} · 변동 범위 상단 {f2(up(b))} · VWAP {f2(vw)}. "
                    + (f"보유 중 — {f2(max(up(b), vw))} 아래로 마감하면 팔아. " if sym in self.pos else f"상단까지 {pc(up(b) / c - 1, 2)}. ")
                    + f"다음 판단 {hm(nxt)} ET ({ams(nxt, today)} 암스테르담).")
            head = f"추세 — {'보유, ' + ams(nxt, today) + ' 재판단' if sym in self.pos else '상단까지 ' + pc(up(b) / c - 1, 1)}"
            self.watch = [dict(sym=sym, setup="MTREND", score=round(min(99, max(0, 100 - (up(b) / c - 1) * 5000))), ok=c > up(b), src="관심종목",
                               why=f"지금 {f2(c)} · 상단 {f2(up(b))} · VWAP {f2(vw)}")]
        else:
            self.bar_triggers(v, a, now)
            p = self.pos.get(sym)
            timed = bool(p and p.get("until") and b + 1 >= p["until"])
            if timed:
                self.sell(sym, p["shares"], a["last"], f"{P['hold']}분 지나도 익절선에 안 닿음 — 시간 청산", now, final=True)
            if sym not in self.pos and not timed and 5 <= b < 369 and self.day.get("bar", -1) < b and self.day["trades"] < P["max_trades"]:
                lp = min(vw, c) - P["fk"] * c * s1
                self.day["lim"] = dict(px=lp, bar=b + 1, at=now.isoformat(), why=f"급락을 지정가 {f2(lp)}에 받음 (VWAP {f2(vw)}·직전 종가 {f2(c)} 중 낮은 쪽에서 평소 1분 변동폭의 {P['fk']:g}배 아래)")
            elif sym in self.pos or not 5 <= b < 369:
                self.day["lim"] = None
            lim, p = self.day.get("lim"), self.pos.get(sym)
            body = (f"받아먹기 모드 (전일 종가 {f2(a['pdc'])} ≤ 50일선 {f2(a['sma50d'])}). 지금 {f2(c)} · VWAP {f2(vw)} · 평소 1분 변동폭 {s1 * 1e4:.0f}bp. "
                    + (f"보유 중 — 익절 {f2(p['tgt'])} / 손절 {f2(p['stop'])} / {hm(570 + p['until'])} ET까지." if p else
                       f"매수 지정가 {f2(lim['px'])} ({pc(lim['px'] / c - 1, 2)}) 걸어 둠 — 매분 다시 계산." if lim else "지금은 지정가를 안 거는 시간."))
            head = "받아먹기 — " + (f"보유, 익절 {f2(p['tgt'])}" if p else f"지정가 {f2(lim['px'])} 대기" if lim else "대기")
            self.watch = [dict(sym=sym, setup="MFADE", score=round(min(99, max(0, 100 - (c / lim["px"] - 1) * 5000))) if lim else 0, ok=bool(p), src="관심종목",
                               why=f"지금 {f2(c)} · 매수 지정가 {f2(lim['px'])}" if lim else f"지금 {f2(c)}")]
        self.day["bar"] = b
        self.think("보유" if self.pos else "탐색", body, f"{mode}{b // 5}{sym in self.pos}{bool(self.day.get('lim'))}", head)


LAST_SNAP: dict = {}  # 모나가 실시간 체결(다른 스레드)에서 쓸 마지막 분석 스냅샷


# ───────────────────────── engine ─────────────────────────
class Engine:
    def __init__(self):
        DATA.mkdir(parents=True, exist_ok=True)
        st = {}
        if (DATA / "state.json").exists():
            try:
                st = json.loads((DATA / "state.json").read_text())
            except Exception:
                st = {}
        cls = lambda P: (MomentumTrader if P.get("mode") == "rotation" else DeskTrader if P.get("mode") == "desk"
                         else SwingTrader if P.get("kind") == "swing" else ScalpTrader if P.get("kind") == "scalp"
                         else MrnaTrader if P.get("kind") == "mrna" else Trader)
        self.T = {k: cls(P)(k, st.get("traders", {}).get(k)) for k, P in TRADERS.items()}
        self.disc: dict = st.get("disc", {})
        self.disc_at = 0
        self.daily: dict = {}
        self.daily_day = None
        self.earn: dict = {}
        self.dflags: dict = {}
        self.dflags_day = None
        self.news = news_mod.NewsBook(alpaca_keys())
        self.snaps: dict = {}
        self.mk = dict(score=0, bias="-", vix=np.nan, breadth=0, notes=[])
        self.system: deque = deque(maxlen=60)
        self.last_fetch, self.fetch_sec, self.error = None, 0.0, ""
        self.desk_day, self.desk_try = None, 0.0
        self.pub_at, self.pub_err = 0.0, ""
        self.cold_at = 0.0
        self.trader_err: dict = {}
        self.mlock = threading.RLock()  # 트레이더 상태를 바꾸는 두 스레드(30초 엔진, 몇 초 간격 실시간 가격) 사이의 잠금
        self.no_daily: set = set()
        self.vprof_at = 0.0
        self.load_feed()

    def desk(self):
        """장 마감 20분 뒤 하루 한 번 퀀트 데스크(paper_desk.py — Alpaca 모의계좌 주문)를 별도 프로세스로 실행.
        실패해도 트레이더에는 영향 없음. 일봉이 아직 없으면(종료 코드 2) 15분 뒤 다시."""
        now = now_ny()
        today, m = now.date(), now.hour * 60 + now.minute
        if (not is_trading_day(today) or m < close_min(today) + 20 or self.desk_day == today.isoformat()
                or time.time() - self.desk_try < 900 or not (APPS / "paper_desk.py").exists()):
            return
        self.desk_try = time.time()
        try:
            r = subprocess.run([sys.executable, str(APPS / "paper_desk.py"), "--send"], capture_output=True, text=True, timeout=900, cwd=str(APPS))
        except Exception as e:
            return self.sys_log("desk", f"퀀트 데스크 실행 실패: {type(e).__name__}: {e}")
        if r.returncode == 2:
            return
        self.desk_day = today.isoformat()
        tail = (r.stdout.strip().splitlines() or [r.stderr.strip()[-200:] or "출력 없음"])[-1]
        self.sys_log("desk", ("퀀트 데스크: " if r.returncode == 0 else f"퀀트 데스크 오류({r.returncode}): ") + tail)

    def publish(self):
        """사이트용 요약 올리기 — 장중(~마감 1시간 뒤)엔 30분마다, 그 밖엔 6시간마다."""
        now = now_ny()
        m = now.hour * 60 + now.minute
        busy = is_trading_day(now.date()) and 570 <= m < close_min(now.date()) + 60
        stale = busy and (not self.last_fetch or (now - self.last_fetch).total_seconds() > 300)
        if (DATA / "NO_PUBLISH").exists() or not LIVE.get("traders") or time.time() - self.pub_at < (1800 if busy else 6 * 3600):
            return
        if self.error or not self.snaps or stale:  # 가격이 없거나 오래됐으면 틀린 숫자가 몇 시간 걸려 있게 되므로 다음 틱에
            return
        self.pub_at = time.time()
        try:
            publish_public(self)
            self.pub_err = ""
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            if err != self.pub_err:
                self.sys_log("system", f"사이트용 요약 올리기 실패 — {err}")
            self.pub_err = err

    def load_feed(self):
        p = DATA / "events" / f"{now_ny().date().isoformat()}.jsonl"
        if p.exists():
            for line in p.read_text().splitlines()[-600:]:
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                (self.T[ev["trader"]].feed if ev["trader"] in self.T else self.system).appendleft(ev)

    def sys_log(self, kind: str, text: str):
        t = now_ny()
        ev = dict(ts=t.isoformat(), day=t.date().isoformat(), trader="sys", kind=kind, text=text)
        self.system.appendleft(ev)
        write_event(ev)

    def save(self):
        with self.mlock:  # 두 스레드와 종료 처리(SIGTERM·재시작)가 동시에 쓰지 않게 + 쓰는 쪽마다 다른 임시 파일
            tmp = DATA / f"state.json.{threading.get_ident()}.tmp"
            tmp.write_text(json.dumps(dict(traders={k: t.dump() for k, t in self.T.items()}, disc=dict(self.disc),
                                           saved=now_ny().isoformat()), ensure_ascii=False, default=float))
            tmp.replace(DATA / "state.json")

    def universe(self) -> list[str]:
        held = [s for t in self.T.values() for s in list(t.pos)]  # list(): 실시간 스레드가 그 사이 포지션을 바꿔도 안전
        pend = [s for t in self.T.values() if isinstance(t, SwingTrader) for s in t.pending()]
        return list(dict.fromkeys(MARKET + [VIX] + WATCHLIST + list(self.disc) + held + pend))

    def tick(self):
        now = now_ny()
        today, m = now.date(), now.hour * 60 + now.minute
        if not is_trading_day(today) or m < 240 or m >= 1200:
            nx = next_open(now)
            why = "오늘은 휴장" if not is_trading_day(today) else "장 열리기 전" if m < 240 else "오늘 장 끝 — 회고는 피드에 있어"
            for t in self.T.values():
                t.status, t.thought = "휴식", f"{why}. 다음 개장 {nx.astimezone(AMS):%m/%d %H:%M} (암스테르담)."
                t.head = f"휴식 — 다음 개장 {nx.astimezone(AMS):%m/%d %H:%M}"
                if isinstance(t, SwingTrader):
                    t.status = "보유" if t.pos else "휴식"
                    t.thought += f" 보유: {t.held_txt(self.snaps)}." + (f" 다음 개장 {t.order_txt()}." if t.order_txt() else "")
            if not self.snaps and time.time() - self.cold_at > 3600:  # 장이 닫힌 동안 켜졌으면 마지막 종가로 평가 (안 그러면 매수가로 보임)
                self.cold_at = time.time()
                self.snaps = {s: dict(sym=s, n=0, last=float(df["Close"].iloc[-1]), chg=float(df["Close"].iloc[-1] / df["Close"].iloc[-2] - 1))
                              for s, df in fetch_daily(self.universe()).items() if len(df) >= 2}
            return

        if self.daily_day != today:
            self.disc = {s: d for s, d in self.disc.items() if d.get("day") == today.isoformat()}
            uni = self.universe()
            self.daily = fetch_daily(uni)
            self.earn = fetch_earnings()
            self.daily_day = today
            for t in self.T.values():
                if isinstance(t, ScalpTrader):
                    t.vprof = {}
        scalpers = [t for t in self.T.values() if isinstance(t, ScalpTrader)]
        if any(not t.vprof for t in scalpers) and time.time() - self.vprof_at > 3600:  # 번개용 '평소 이 시각 5분 거래량' — 실패하면 1시간 뒤 다시
            self.vprof_at = time.time()
            try:
                vp = fetch_vprof(MARKET + WATCHLIST, today)
            except Exception as e:
                traceback.print_exc()
                vp = {}
                self.sys_log("system", f"평소 거래량 자료를 못 받음 ({type(e).__name__}) — 번개는 그동안 매수 안 함, 1시간 뒤 다시 시도")
            for t in scalpers:
                t.vprof = vp
        miss = [s for s in self.universe() if s not in self.daily and s not in self.no_daily]
        if miss:  # 오늘 일봉을 받은 뒤에 생긴 주문·보유 종목 (없으면 하루 종일 매수가로 평가됨)
            got = fetch_daily(miss)
            self.daily.update(got)
            self.no_daily |= set(miss) - set(got)
        due = (m >= 540 and m < 900 and time.time() - self.disc_at > 1800) or (not self.disc and m >= 540 and time.time() - self.disc_at > 300)
        if due:
            self.disc_at = time.time()
            found = discover()
            fresh = [d for d in found if d["sym"] not in self.disc]
            for d in found:
                d["day"] = today.isoformat()
                self.disc.setdefault(d["sym"], d)
            if len(self.disc) > 25:
                self.disc = dict(sorted(self.disc.items(), key=lambda kv: kv[1]["chg"], reverse=True)[:25])
            miss = [d["sym"] for d in fresh if d["sym"] not in self.daily]
            if miss:
                self.daily.update(fetch_daily(miss))
                self.dflags_day = None  # 새 종목도 일봉 추세 플래그 다시 계산
            if fresh:
                self.sys_log("scan", "스캐너가 새 종목을 찾음: " + ", ".join(
                    f"{d['sym']} {pc(d['chg'])}" + (f" 거래량 {d['rvol']:.1f}배" if d.get("rvol") else "") for d in fresh[:8]))

        t0 = time.time()
        bars = fetch_1m(self.universe())
        self.fetch_sec, self.last_fetch = time.time() - t0, now
        vix_df = bars.get(VIX)
        vix = float(vix_df["Close"].iloc[-1]) if vix_df is not None else (
            float(self.daily[VIX]["Close"].iloc[-1]) if VIX in self.daily else np.nan)
        spy = analyze("SPY", bars.get("SPY"), self.daily.get("SPY"), today, None, None)
        snaps = {"SPY": spy} if spy else {}
        for s in self.universe():
            if s in (VIX, "SPY"):
                continue
            try:
                a = analyze(s, bars.get(s), self.daily.get(s), today, spy["chg"] if spy else None, self.earn.get(s))
            except Exception:
                a = None
            if a:
                snaps[s] = a
        add_sector(snaps)
        if self.dflags_day != today:
            self.dflags, self.dflags_day = daily_trend(self.daily, today), today
        for s, a in snaps.items():
            a.update(self.dflags.get(s, {}))
        self.snaps, self.mk = snaps, regime(snaps, vix)
        self.pull_news(now, today)

        cm = close_min(today)
        with self.mlock:
            for t in self.T.values():
                try:
                    self.run_trader(t, now, today, m, cm, snaps, bars)
                except Exception as e:  # 한 명의 오류가 나머지와 저장을 막지 않게
                    traceback.print_exc()
                    err = f"{t.P['nick']} 오류 — {type(e).__name__}: {e}"
                    if err != self.trader_err.get(t.key):
                        self.sys_log("system", err)
                    self.trader_err[t.key] = err
            self.save()

    def fast_loop(self):
        """모나용 — Alpaca 실시간 체결가(IEX)를 3초마다 받아 지정가·익절·손절을 확인. 키가 없으면 돌지 않고, 그땐 30초마다 1분봉으로 확인됨."""
        k, sec = alpaca_keys()
        ms = [t for t in self.T.values() if isinstance(t, MrnaTrader)]
        if not (k and sec and ms):
            return
        hdr, seen, bad = {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": sec}, None, 0
        while True:
            now = now_ny()
            m = now.hour * 60 + now.minute
            if not (is_trading_day(now.date()) and 570 <= m < close_min(now.date()) - 10):
                time.sleep(30)
                continue
            try:
                with self.mlock:
                    want = any(t.wants_ticks() for t in ms)
                if not want:
                    time.sleep(2)
                    continue
                tr = requests.get(f"https://data.alpaca.markets/v2/stocks/{MrnaTrader.SYM}/trades/latest", params={"feed": "iex"}, headers=hdr, timeout=5).json()["trade"]
                bad = 0
            except Exception as e:
                bad += 1
                if bad == 20:
                    self.sys_log("system", f"실시간 체결가를 계속 못 받음 ({type(e).__name__}) — 모나는 30초마다 1분봉으로 확인 중")
                time.sleep(3)
                continue
            try:
                at = pd.Timestamp(tr["t"]).tz_convert(NY).to_pydatetime()  # 판단은 '받은 시각'이 아니라 '체결된 시각' 기준
                if tr["t"] != seen and (now_ny() - at).total_seconds() < 20:  # 오래된 체결가로는 판단하지 않음
                    seen = tr["t"]
                    with self.mlock:
                        if any([t.on_price(float(tr["p"]), at) for t in ms]):
                            self.save()
            except Exception as e:  # 여기서 죽으면 스레드가 사라져 실시간 확인이 조용히 멈춤 — 기록하고 계속
                traceback.print_exc()
                err = f"모나 실시간 처리 오류 — {type(e).__name__}: {e}"
                if err != self.trader_err.get("fast"):
                    self.sys_log("system", err)
                self.trader_err["fast"] = err
            time.sleep(3)

    def run_trader(self, t, now: dt.datetime, today: dt.date, m: int, cm: int, snaps: dict, bars: dict):
        if t.day.get("date") != today.isoformat():
            t.new_day(today, snaps, bars)
        if isinstance(t, SwingTrader):
            t.run_tick(now, today, m, snaps, bars)
        elif m < 570:
            if not t.day["planned"] and m >= 480:
                t.plan(snaps, self.mk, self.disc, today, self.top_news())
            left = 570 - m
            t.think("준비", f"개장 전 준비 중 — 개장까지 {left // 60}시간 {left % 60}분 ({ams(570, today)} 암스테르담). "
                            f"프리마켓 시장 점수 {self.mk['score']} ({self.mk['bias']}).", "pre",
                    f"개장 전 준비 — {left // 60}시간 {left % 60}분 남음")
        elif m < cm:
            if not t.day["planned"]:
                t.plan(snaps, self.mk, self.disc, today, self.top_news())
            t.step(now, snaps, bars, self.mk, self.disc, self.news)
        else:
            for s in list(t.pos):
                t.sell(s, t.pos[s]["shares"], t.price(s, snaps, t.pos[s]["entry"]), "장 마감 정리", now, final=True)
            if not t.day["reviewed"]:
                t.review(snaps, self.mk, today)
            t.think("마감", "오늘 장 끝. 회고를 남겼어 — 피드 맨 위를 봐.", "done", "오늘 장 끝 — 회고 남김")
        if 570 <= m <= cm:
            eq = t.equity(snaps)
            if not t.curve or t.curve[-1][0] != hm(m):
                t.curve.append([hm(m), round(eq, 2)])

    def pull_news(self, now: dt.datetime, today: dt.date):
        """새 뉴스를 받아 해석 → 시스템 기록 · 트레이더 반응 · 강한 직접 호재 종목은 후보에 추가."""
        uni = set(self.universe())
        new = self.news.fetch(None)
        added = []
        for it in new:
            age = (now.timestamp() - news_mod._ts(it["t"])) / 60
            for s, v in it["imp"].items():  # 관심 밖 종목의 강한 직접 호재 → 오늘 후보로
                if (s not in uni and v >= 0.9 and s.isalpha() and s.isupper() and len(s) <= 5 and len(added) < 5
                        and sum(d.get("src") == "news" for d in self.disc.values()) < 10 and age < 120):
                    self.disc[s] = dict(sym=s, name=it["head"][:80], chg=0.0, px=0, cap=0, rvol=None, src="news", day=today.isoformat())
                    added.append(s)
            imp = {s: v for s, v in it["imp"].items() if s in uni or s in added}
            if not imp or age > 90 or max(abs(v) for v in imp.values()) < 0.5:
                continue
            view = dict(it, imp=imp)
            tops = sorted(imp.items(), key=lambda kv: -abs(kv[1]))[:8]
            self.sys_log("news", f"\"{it['head']}\"" + (f" [{it['themes'][0]['name']}]" if it["themes"] else "") + " → "
                         + ", ".join(f"{s} {v:+.1f}" for s, v in tops))
            if max(abs(v) for v in imp.values()) >= 0.7:
                for t in self.T.values():
                    t.react(view, set(t.pos))
        if added:
            self.daily.update(fetch_daily(added))
            self.dflags_day = None  # 새 종목도 일봉 추세 플래그 다시 계산
            self.sys_log("scan", f"뉴스로 새 후보 추가: {', '.join(added)}")

    def top_news(self) -> list:
        uni = set(self.universe())
        items = self.news.recent(uni, 60)
        return sorted([i for i in items if max(abs(v) for v in i["imp"].values()) >= 0.6],
                      key=lambda i: -max(abs(v) for v in i["imp"].values()))[:3]

    def run(self):
        started = time.time()
        while True:
            try:
                self.tick()
                self.error = ""
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"
                traceback.print_exc()
                if isinstance(e, OSError) and e.errno == errno.EMFILE:
                    restart(self, "열린 파일 수 한도 초과")
            if open_files() > FD_RESTART:  # yfinance가 스레드마다 캐시 DB를 열어 두고 안 닫음 — 한도 전에 새로 시작
                restart(self, f"열린 파일 {open_files()}개")
            nyt = now_ny()
            if time.time() - started > 20 * 3600 and 240 <= nyt.hour * 60 + nyt.minute < 300:
                restart(self, "하루 한 번 새로 시작 (새벽 4시 ET)")
            try:
                self.desk()
            except Exception:
                traceback.print_exc()
            with self.mlock:
                build_live(self)
            try:
                self.publish()
            except Exception:
                traceback.print_exc()
            m = now_ny().hour * 60 + now_ny().minute
            regular = is_trading_day(now_ny().date()) and 565 <= m < close_min(now_ny().date()) + 2
            time.sleep(30 if regular else 120 if 240 <= m < 1200 else 300)


# ───────────────────────── 계속 돌기 (launchd) ─────────────────────────
# launchd(~/Library/LaunchAgents/com.isaac-waffle.traders.plist, apps/traders_service.sh)가 로그인 때 띄우고,
# 비정상 종료(코드 ≠ 0)면 다시 띄움. 허브의 '끄기'(SIGTERM)는 저장 후 0으로 끝나서 다시 안 뜸.
FD_RESTART = 2000


def open_files() -> int:
    try:
        return len(os.listdir("/dev/fd"))
    except OSError:
        return 10 ** 6


def raise_fd_limit():
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    want = 10240 if hard == resource.RLIM_INFINITY else min(10240, hard)
    if soft < want:
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
        except (ValueError, OSError):
            pass


def restart(E: "Engine", why: str):
    """상태 저장 후 같은 프로세스 자리에서 새로 시작(exec) — 쌓인 파일·메모리가 비워짐. 실패하면 종료(launchd가 다시 띄움)."""
    try:
        E.save()
        E.sys_log("system", f"트레이더 서버 재시작 — {why}")
    except Exception:
        pass
    print(f"{now_ny():%m-%d %H:%M} ET 재시작: {why}", flush=True)
    try:
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__), *sys.argv[1:]])
    finally:
        os._exit(75)


def build_live(E: Engine):
    now = now_ny()
    today, m = now.date(), now.hour * 60 + now.minute
    if not is_trading_day(today):
        state = "휴장"
    elif m < 240:
        state = "장 시작 전"
    elif m < 570:
        state = "프리마켓"
    elif m < close_min(today):
        state = "정규장"
    elif m < 1200:
        state = "애프터마켓"
    else:
        state = "장 마감"
    traders = []
    for k, t in E.T.items():
        eq = t.equity(E.snaps)
        start = t.day.get("start", eq)
        pos = []
        for s, p in t.pos.items():
            last = t.price(s, E.snaps, p["entry"])
            rps = p["entry"] - p["stop0"]
            sw = isinstance(t, SwingTrader)
            pos.append(dict(sym=s, shares=p["shares"], init=p["init"], entry=p["entry"], last=last, stop=p["stop"], swing=sw,
                            rot=p.get("setup") in ("MOM", "DESK", "DIP10", "MTREND"),
                            stop0=p["stop0"], t1=max(p.get("hi", p["t1"]), last) if sw else p["t1"],
                            t2=max(p.get("hi", p["t2"]), last) if sw else p["t2"], half=p["half"] and not sw or sw,
                            days=(now.date() - dt.date.fromisoformat(p["opened"][:10])).days, setup=SETUP_KO.get(p["setup"], p["setup"]),
                            src=p["src"], why=p["why"], upl=p["shares"] * (last - p["entry"]) + p["realized"],
                            r=(last - p["entry"]) / rps if rps > 0 else 0, opened=p["opened"]))
        traders.append(dict(key=k, kind=t.P.get("kind", "day"), mode=t.P.get("mode", "day"), name=t.P["name"], nick=t.P["nick"], color=t.P["color"], motto=t.P["motto"],
                            equity=eq, cash=t.cash, day_pnl=eq - start, total_pnl=eq - t.start_cash, start_cash=t.start_cash, status=t.status, head=t.head,
                            thought=t.thought, pos=pos, watch=t.watch, feed=list(t.feed)[:60], curve=t.curve,
                            trades=t.day.get("trades", 0), max_trades=t.P["max_trades"], wins=t.day.get("wins", 0),
                            losses=t.day.get("losses", 0), halted=t.day.get("halted", ""),
                            risk=t.P["risk"], daily_loss=t.P["daily_loss"], start=start))
    idx = {s: dict(chg=E.snaps[s]["chg"], last=E.snaps[s]["last"]) for s in MARKET if s in E.snaps}
    nx = next_open(now)
    with LOCK:
        LIVE.clear()
        LIVE.update(dict(now=now.isoformat(), state=state, next_open=nx.isoformat(), open_ams=f"{nx.astimezone(AMS):%m/%d %H:%M}",
                         mk=E.mk, idx=idx, traders=traders, system=list(E.system)[:30], disc=list(E.disc.values()),
                         last_fetch=E.last_fetch.isoformat() if E.last_fetch else None, fetch_sec=E.fetch_sec,
                         error=E.error, rules=RULES, start_cash=START_CASH, news_err=E.news.error,
                         news=[dict(t=i["t"], head=i["head"], url=i["url"], themes=[x["name"] for x in i["themes"]],
                                    imp=sorted(i["imp"].items(), key=lambda kv: -abs(kv[1]))[:8])
                               for i in E.news.recent(set(E.universe()), 40)]))


# ───────────────────────── 기록·개선 분석 ─────────────────────────
def history() -> dict:
    trades = read_csv(DATA / "trades.csv")
    days = read_csv(DATA / "days.csv")
    for t in trades:
        for c in ("pnl", "r", "hold_min", "mkt_score", "rvol", "score"):
            try:
                t[c] = float(t[c])
            except (TypeError, ValueError):
                t[c] = None
        t["mfe_r"] = float(t["mfe_r"]) if t.get("mfe_r") not in (None, "") else None

    def stats(rows):
        n = len(rows)
        if not n:
            return dict(n=0)
        w = [r for r in rows if r["pnl"] > 0]
        gw, gl = sum(r["pnl"] for r in w), -sum(r["pnl"] for r in rows if r["pnl"] <= 0)
        return dict(n=n, win=len(w) / n, avg_r=float(np.mean([r["r"] for r in rows])), pnl=sum(r["pnl"] for r in rows),
                    pf=gw / gl if gl > 0 else None, hold=float(np.mean([r["hold_min"] for r in rows])))

    def bucket(t):
        h, mm = map(int, t["entry_t"].split(":"))
        x = h * 60 + mm
        return "오전 (~11:30)" if x < 690 else "점심 (11:30~14:00)" if x < 840 else "오후 (14:00~)"

    with LOCK:
        live = {t["key"]: t["equity"] for t in LIVE.get("traders", [])}
        today = LIVE.get("now", "")[:10]
    out = dict(traders={}, rules=RULES)
    for k, P in TRADERS.items():
        rows = [t for t in trades if t["trader"] == k]
        dd = [d for d in days if d["trader"] == k]
        curve = [dict(date=d["date"], equity=float(d["end_equity"])) for d in dd]
        if k in live and today and is_trading_day(dt.date.fromisoformat(today)) and (not curve or curve[-1]["date"] < today):
            curve.append(dict(date=today, equity=live[k], live=True))  # 아직 마감 전인 오늘
        cash0 = P.get("cash", START_CASH)
        peak, mdd = cash0, 0.0
        for c in curve:
            peak = max(peak, c["equity"])
            mdd = min(mdd, c["equity"] / peak - 1)
        groups = {}
        for name, fn in (("셋업", lambda t: SETUP_KO.get(t["setup"], t["setup"])), ("종목 출처", lambda t: t["src"]),
                         ("시간대", bucket), ("청산 이유", lambda t: t["exit_reason"]), ("규칙 버전", lambda t: t["rules"])):
            g = {}
            for t in rows:
                g.setdefault(fn(t), []).append(t)
            groups[name] = sorted([dict(key=kk, **stats(v)) for kk, v in g.items()], key=lambda x: -x["n"])
        out["traders"][k] = dict(name=P["name"], nick=P["nick"], color=P["color"], stats=stats(rows), curve=curve,
                                 equity=curve[-1]["equity"] if curve else cash0, start_cash=cash0,
                                 mdd=mdd, days=len(dd), groups=groups, hints=hints(rows, groups, P))
    out["trades"] = sorted(trades, key=lambda t: (t["date"], t["entry_t"]), reverse=True)[:400]
    ev = DATA / "events"
    out["dates"] = sorted([p.stem for p in ev.glob("*.jsonl")], reverse=True) if ev.exists() else []
    return out


def hints(rows: list, groups: dict, P: dict) -> list[str]:
    n = len(rows)
    if n < 5:
        return [f"거래가 {n}건뿐이라 아직 판단하기 일러요. 5건 이상 쌓이면 개선 힌트가 나와요."]
    H = []
    for g in groups["셋업"]:
        if g["n"] >= 4 and g["avg_r"] < -0.2:
            H.append(f"'{g['key']}' 셋업 평균 {g['avg_r']:+.2f}R ({g['n']}건) — 기준을 높이거나 빼는 걸 검토.")
        if g["n"] >= 4 and g["avg_r"] > 0.5:
            H.append(f"'{g['key']}' 셋업이 강함: 평균 {g['avg_r']:+.2f}R ({g['n']}건) — 비중을 늘려볼 만함.")
    fast = [t for t in rows if t["r"] < 0 and t["hold_min"] <= 10]
    if len(fast) / n >= 0.3:
        H.append(f"진입 10분 안 손절이 {len(fast)}/{n}건 — 5분봉 마감 확인 후 진입하거나 손절폭을 조금 넓히는 걸 검토.")
    gave = [t for t in rows if t["mfe_r"] is not None and t["mfe_r"] >= 1 and t["r"] <= 0.1]
    if len(gave) >= 2:
        H.append(f"1R 이상 갔다가 본전 이하로 끝난 거래 {len(gave)}건 — 1차 익절 지점을 1R로 당기는 걸 검토.")
    ts = [t for t in rows if t["exit_reason"].startswith("시간 손절")]
    if len(ts) / n >= 0.3:
        H.append(f"시간 손절이 {len(ts)}/{n}건 — RVOL 기준({P.get('min_rvol', '-')}배)을 올려 더 활발한 종목만 고르는 걸 검토.")
    for g in groups["시간대"]:
        if g["n"] >= 4 and g["avg_r"] < -0.2:
            H.append(f"{g['key']} 성적이 나쁨 (평균 {g['avg_r']:+.2f}R, {g['n']}건) — 이 시간대 매매를 줄이는 걸 검토.")
    src = {g["key"]: g for g in groups["종목 출처"]}
    if "새로 발견" in src and "관심종목" in src and src["새로 발견"]["n"] >= 3 and src["관심종목"]["n"] >= 3:
        a, b = src["새로 발견"]["avg_r"], src["관심종목"]["avg_r"]
        H.append(f"새로 찾은 종목 평균 {a:+.2f}R vs 관심종목 {b:+.2f}R — {'스캐너 종목이 더 나음' if a > b else '관심종목이 더 나음'}.")
    return H or ["눈에 띄는 약점 없음 — 지금 규칙을 유지하면서 표본을 더 모으세요."]


# ───────────────────────── 사이트 공개용 요약 ─────────────────────────
# 포트폴리오 사이트(site/demos/traders.html)가 읽는 영어 요약. 생각 전문·뉴스는 빼고 숫자와 보유·거래만.
# 장중 30분마다(그 밖엔 6시간마다) traders-data 브랜치에 traders.json 한 파일로 올림 (커밋 하나, 강제 푸시 — 다른 도구의 *-data 브랜치와 같은 방식).
# journal/traders/NO_PUBLISH 파일이 있으면 올리지 않음.
PUBLIC_EN = {
    "agg": ("Isaac", "Momentum rotation", "Holds the 5 watchlist stocks with the best 6-month return, 20% each, and rotates once a month. No stops."),
    "bal": ("Timo", "Oversold pullback", "Buys stocks in an uptrend after a sharp 2 to 3 day drop (RSI(2) below 10). Sells on a close above the 5-day average, or after 5 days."),
    "def": ("Chacha", "Trend bounce", "Buys uptrending stocks that dip to their 20-day average and close back above it, calmest names first. Goes to cash when SPY is below its 200-day average."),
    "swing": ("Dolsoe", "Breakout", "Buys 20-day closing highs on 1.5x volume from the watchlist plus the Nasdaq-100. 2 ATR stop, then a 3.5 ATR trailing stop."),
    "desk": ("Alpha", "Multi-strategy desk", "Five published strategies at 20% each (trend following, volatility targeting, 12-month momentum, Minervini trend template, gap pivots), idle cash in T-bills. "
                                            "The same signals and order rules also run on an Alpaca paper account."),
    "scalp": ("Flash", "10-minute dip buyer", "Decides every 10 minutes. Buys a stock that is up on the day and drops sharply in 10 minutes on 3x volume, while SPY is above VWAP. Re-checks every 10 minutes, flat by 15:50 ET."),
    "mrna": ("Mona", "MRNA specialist", "Trades Moderna only, with the whole account. Above the 50-day average it follows intraday breakouts beyond the usual range; "
                                        "below it, it rests limit orders under sharp drops and sells the bounce. Prices are checked every few seconds. This rule did not pass validation."),
}
SETUP_EN = {"MOM": "Momentum", "BRK": "Breakout", "PB": "Pullback", "TP": "Trend bounce", "DESK": "Desk rebalance", "DIP10": "10-min dip", "MTREND": "MRNA trend", "MFADE": "MRNA limit dip"}
STATE_EN = {"휴장": "Market closed", "장 시작 전": "Before the open", "프리마켓": "Pre-market", "정규장": "Market open", "애프터마켓": "After hours", "장 마감": "Market closed"}


def public_snapshot(E: "Engine") -> dict:
    H = history()
    with LOCK:
        live = {t["key"]: t for t in LIVE.get("traders", [])}
        state = LIVE.get("state", "")
    out = []
    for k, (nick, style, rule) in PUBLIC_EN.items():
        t, h = live.get(k), H["traders"].get(k)
        if not t or not h:
            continue
        eq, c0 = t["equity"], t["start_cash"]
        pos = sorted(t["pos"], key=lambda p: -p["shares"] * p["last"])
        out.append(dict(key=k, name=nick, style=style, rule=rule, color=t["color"], start_cash=c0, equity=round(eq, 2), ret=eq / c0 - 1,
                        day_ret=t["day_pnl"] / t["start"] if t.get("start") else 0.0, cash_w=t["cash"] / eq if eq else 1.0, n_pos=len(pos),
                        pos=[dict(sym=p["sym"], w=p["shares"] * p["last"] / eq, ret=p["last"] / p["entry"] - 1, days=p["days"]) for p in pos[:8]],
                        trades=h["stats"]["n"], win=h["stats"].get("win"), mdd=h["mdd"], days=h["days"],
                        curve=[[c["date"], round(c["equity"] / c0 - 1, 5)] for c in h["curve"]]))
    rows = [t for t in H["trades"] if t["trader"] in PUBLIC_EN and t["setup"] in SETUP_EN][:20]
    trades = [dict(date=t["date"], trader=PUBLIC_EN[t["trader"]][0], key=t["trader"], sym=t["sym"], setup=SETUP_EN[t["setup"]],
                   ret=float(t["exit"]) / float(t["entry"]) - 1 if float(t["entry"] or 0) > 0 else None, pnl=t["pnl"],
                   hold_min=t["hold_min"]) for t in rows]
    return dict(updated=int(time.time()), state=STATE_EN.get(state, state), rules=RULES, traders=out, trades=trades)


def publish_public(E: "Engine"):
    """요약을 traders-data 브랜치에 올림. 실패하면 예외 (엔진이 한 번만 기록)."""
    remote = subprocess.run(["git", "-C", str(APPS), "remote", "get-url", "origin"], capture_output=True, text=True, timeout=20).stdout.strip()
    if not remote:
        raise RuntimeError("git origin 주소를 못 찾음")
    out = Path(tempfile.mkdtemp(prefix="traders-pub-"))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))} | {"GIT_TERMINAL_PROMPT": "0"}
    safe = ["-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]  # 내 전역 git 설정(서명·훅)이 끼어들지 않게
    try:
        (out / "traders.json").write_bytes(_dumps(public_snapshot(E)))
        for args in (["init", "-q", "-b", "traders-data"], ["add", "traders.json"],
                     ["-c", "user.name=virtual-traders", "-c", "user.email=virtual-traders@users.noreply.github.com", "commit", "-q", "-m",
                      f"traders {dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%MZ}"],
                     ["push", "-q", "-f", remote, "traders-data"]):
            r = subprocess.run(["git", *safe, *args], cwd=out, capture_output=True, text=True, timeout=90, env=env)
            if r.returncode:
                raise RuntimeError(f"git {next(a for a in args if a in ('init', 'add', 'commit', 'push'))}: {(r.stderr.strip().splitlines() or ['실패'])[-1][:160]}")
    finally:
        shutil.rmtree(out, ignore_errors=True)


def events_of(day: str) -> list[dict]:
    p = DATA / "events" / f"{day}.jsonl"
    if not p.exists() or "/" in day or ".." in day:
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


# ───────────────────────── server ─────────────────────────
def notebook(page: str) -> str:
    """공책 테마(apps/notebook.css + 손글씨 글꼴)를 <head> 끝에 끼워 넣음 — 허브와 같은 파일."""
    css = (APPS / "notebook.css").read_text() if (APPS / "notebook.css").exists() else ""
    font = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            '<link href="https://fonts.googleapis.com/css2?family=Gaegu:wght@400;700&display=swap" rel="stylesheet">')
    return page.replace("</head>", f"{font}<style>{css}</style></head>", 1)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj):
        self._send(200, _dumps(obj), "application/json; charset=utf-8")

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            self._send(200, notebook(PAGE.read_text()).encode(), "text/html; charset=utf-8")
        elif u.path == "/api/live":
            with LOCK:
                self._json(LIVE)
        elif u.path == "/api/history":
            self._json(history())
        elif u.path == "/api/events":
            self._json(events_of(parse_qs(u.query).get("day", [""])[0]))
        else:
            self._send(404, b"not found", "text/plain")


def _clean(x):
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.floating, np.integer)):
        return _clean(x.item())
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def _dumps(obj) -> bytes:
    return json.dumps(_clean(obj), ensure_ascii=False, default=str).encode()


def main():
    raise_fd_limit()
    E = Engine()

    def on_term(*_):  # 허브의 '끄기' — 저장하고 정상 종료(launchd가 다시 띄우지 않음)
        try:
            E.save()
        finally:
            os._exit(0)

    signal.signal(signal.SIGTERM, on_term)
    build_live(E)
    try:  # 켜 두는 동안 맥이 잠들지 않게 (이 프로세스가 끝나면 같이 끝남)
        subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    threading.Thread(target=E.run, daemon=True).start()
    threading.Thread(target=E.fast_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    if "--open" in sys.argv:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
