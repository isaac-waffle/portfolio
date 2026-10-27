"""미국 주식 어닝 대시보드 (실시간 데이터: Yahoo Finance, Nasdaq 어닝 캘린더, Google News RSS)

실행:  .venv/bin/streamlit run apps/earnings_app.py   (또는 apps/start.sh 로 허브에서)
"""
from __future__ import annotations

import datetime as dt
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

from notebook_st import apply as notebook_theme
import yfinance as yf
from plotly.subplots import make_subplots

WATCHLIST = [
    "VIST", "BA", "GE", "AAPL", "MCD", "NKE", "STLD", "QNT", "BTC-USD", "IBM", "ASML", "NVDA",
    "SPCX", "AMZN", "MSFT", "TTWO", "TSLA", "TSM", "GOOGL", "RBLX", "KARO", "QCOM", "META", "AVGO",
    "DELL", "ORCL", "AMAT", "LRCX", "MRVL", "MU", "INTC", "AMD", "BE", "SKHY", "U", "SNDK",
    "WDC", "ARM", "CRWD", "PLTR", "STX", "PANW", "PENG", "MRNA", "SNOW", "IONQ", "SMCI", "IREN",
    "CRWV", "NBIS",
]
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
UP, DOWN = "#16a34a", "#dc2626"

st.set_page_config(page_title="어닝 대시보드", layout="wide")
notebook_theme()  # 공책 테마 (apps/notebook_st.py)


# ───────────────────────── helpers ─────────────────────────
def cap_class(mc: float | None) -> str:
    if not mc:
        return "-"
    b = mc / 1e9
    if b >= 200:
        return "초대형주 (Mega)"
    if b >= 10:
        return "대형주 (Large)"
    if b >= 2:
        return "중형주 (Mid)"
    if b >= 0.3:
        return "소형주 (Small)"
    return "초소형주 (Micro)"


def fmt_big(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    a = abs(x)
    for unit, d in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if a >= d:
            return f"${x / d:,.2f}{unit}"
    return f"${x:,.0f}"


def pct(x, digits=1) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    return f"{x * 100:+.{digits}f}%"


def num(x, digits=2) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    return f"{x:,.{digits}f}"


def parse_cap(s: str) -> float | None:
    try:
        return float(s.replace("$", "").replace(",", ""))
    except Exception:
        return None


# ───────────────────────── data ─────────────────────────
@st.cache_data(ttl=3 * 3600, show_spinner=False)
def nasdaq_calendar(start: dt.date, days: int) -> pd.DataFrame:
    rows = []
    for i in range(days):
        d = start + dt.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        try:
            r = requests.get(f"https://api.nasdaq.com/api/calendar/earnings?date={d}", headers=UA, timeout=15)
            for row in (r.json().get("data") or {}).get("rows") or []:
                row["date"] = d
                rows.append(row)
        except Exception:
            continue
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["cap"] = df["marketCap"].map(parse_cap)
    df["timing"] = df["time"].map({"time-pre-market": "장전 (BMO)", "time-after-hours": "장후 (AMC)"}).fillna("미정")
    return df


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def watch_overview(tickers: tuple[str, ...]) -> pd.DataFrame:
    def one(s):
        out = {"티커": s}
        try:
            t = yf.Ticker(s)
            fi = t.fast_info
            price, prev = fi["lastPrice"], fi["previousClose"]
            out["현재가"] = price
            out["일간"] = price / prev - 1 if prev else np.nan
            mc = fi.get("marketCap") if hasattr(fi, "get") else None
            try:
                mc = fi["marketCap"]
            except Exception:
                pass
            out["시가총액"] = mc
            if not s.endswith("-USD"):
                cal = t.calendar or {}
                ed = cal.get("Earnings Date") or []
                out["다음 어닝"] = ed[0] if ed else None
                out["EPS 예상"] = cal.get("Earnings Average")
        except Exception:
            pass
        return out

    with ThreadPoolExecutor(12) as ex:
        df = pd.DataFrame(list(ex.map(one, tickers)))
    df["규모"] = df["시가총액"].map(cap_class)
    today = dt.date.today()
    df["D-day"] = df["다음 어닝"].map(lambda d: (d - today).days if isinstance(d, dt.date) else np.nan)
    return df


@st.cache_data(ttl=900, show_spinner=False)
def load_ticker(s: str):
    t = yf.Ticker(s)
    hist = t.history(period="5y", auto_adjust=False)
    try:
        info = t.info
    except Exception:
        info = {}
    try:
        ed = t.get_earnings_dates(limit=24)
    except Exception:
        ed = None
    try:
        cal = t.calendar or {}
    except Exception:
        cal = {}
    try:
        rec = t.recommendations
    except Exception:
        rec = None
    try:
        intraday = t.history(period="5d", interval="15m")
    except Exception:
        intraday = pd.DataFrame()
    try:
        q = t.quarterly_income_stmt
    except Exception:
        q = None
    return hist, info, ed, cal, rec, intraday, q


@st.cache_data(ttl=3600, show_spinner=False)
def spy_hist():
    return yf.Ticker("SPY").history(period="2y")["Close"]


@st.cache_data(ttl=900, show_spinner=False)
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


@st.cache_data(ttl=1800, show_spinner=False)
def google_news(query: str, n: int = 15):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query + " when:14d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    try:
        root = ET.fromstring(requests.get(url, headers=UA, timeout=15).content)
    except Exception:
        return []
    items = []
    for it in root.iter("item"):
        try:
            when = parsedate_to_datetime(it.findtext("pubDate"))
        except Exception:
            when = None
        items.append({"title": it.findtext("title"), "link": it.findtext("link"),
                      "source": it.findtext("source") or "", "when": when})
    items.sort(key=lambda x: x["when"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
    return items[:n]


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
        d = pd.Timestamp(local.date())
        amc = local.hour >= 12
        pos = dates.searchsorted(d)
        if pos >= len(dates):
            continue
        if amc:
            if dates[pos] != d:
                continue
            base_i, react_i = pos, pos + 1
        else:
            if dates[pos] != d or pos == 0:
                continue
            base_i, react_i = pos - 1, pos
        if react_i >= len(dates):
            continue
        base = h["Close"].iloc[base_i]
        gap = h["Open"].iloc[react_i] / base - 1
        day = h["Close"].iloc[react_i] / base - 1
        w5_i = min(react_i + 4, len(dates) - 1)
        w5 = h["Close"].iloc[w5_i] / base - 1
        sur = r.get("Surprise(%)")
        rows.append({
            "발표일": local.date(), "시점": "장후" if amc else "장전",
            "EPS 예상": r.get("EPS Estimate"), "EPS 실제": r.get("Reported EPS"),
            "서프라이즈": sur / 100 if pd.notna(sur) else np.nan,
            "갭(시가)": gap, "반응일 종가": day, "5일 누적": w5,
        })
    return pd.DataFrame(rows)


def rsi(close: pd.Series, n=14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def color_pct(v):
    if isinstance(v, (int, float)) and not np.isnan(v):
        return f"color: {UP if v > 0 else DOWN if v < 0 else 'inherit'}"
    return ""


TABLE_CSS = """
.wrap { overflow: auto; border: 1px solid var(--st-border-color, rgba(128,128,128,.25)); border-radius: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 14px; font-variant-numeric: tabular-nums; }
th { position: sticky; top: 0; background: var(--st-secondary-background-color); text-align: left;
     font-weight: 500; padding: 8px 10px; white-space: nowrap; color: var(--st-text-color); opacity: .85; }
td { padding: 7px 10px; border-top: 1px solid var(--st-border-color, rgba(128,128,128,.15)); white-space: nowrap;
     color: var(--st-text-color); }
td.n { text-align: right; }
tbody tr { cursor: pointer; user-select: none; }
tbody tr:hover { background: var(--st-secondary-background-color); }
.up { color: #16a34a; } .down { color: #dc2626; } .star { color: var(--st-primary-color); font-weight: 600; }
"""

TABLE_JS = """
export default function(component) {
    const { data, setTriggerValue, parentElement } = component;
    const old = parentElement.querySelector('.wrap');
    if (old) old.remove();
    const wrap = document.createElement('div');
    wrap.className = 'wrap';
    if (data.height) wrap.style.maxHeight = data.height + 'px';
    const table = document.createElement('table');
    const head = table.createTHead().insertRow();
    data.columns.forEach((c) => { const th = document.createElement('th'); th.textContent = c; head.appendChild(th); });
    const body = table.createTBody();
    data.rows.forEach((row, i) => {
        const tr = body.insertRow();
        tr.title = '더블클릭하면 ' + data.tickers[i] + ' 상세 분석';
        row.forEach((v, j) => {
            const td = tr.insertCell();
            td.textContent = v;
            const cls = data.classes[i][j];
            if (cls) td.className = cls;
        });
        tr.ondblclick = () => setTriggerValue('open', data.tickers[i]);
    });
    wrap.appendChild(table);
    parentElement.appendChild(wrap);
}
"""

_dbl_table = st.components.v2.component("dbl_table", css=TABLE_CSS, js=TABLE_JS)


def dbl_table(df: pd.DataFrame, tickers: list[str], fmt: dict, key: str, num_cols=(), sign_cols=(), height=None):
    """행을 더블클릭하면 그 종목의 상세 분석 화면으로 이동하는 표."""
    rows, classes = [], []
    for _, r in df.iterrows():
        cells, cl = [], []
        for c in df.columns:
            v = r[c]
            cells.append(fmt[c](v) if c in fmt else ("-" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v)))
            k = "n" if c in num_cols else ""
            if c in sign_cols and isinstance(v, (int, float)) and not np.isnan(v) and v != 0:
                k += " up" if v > 0 else " down"
            if c == "관심" and v:
                k += " star"
            cl.append(k.strip())
        rows.append(cells)
        classes.append(cl)
    res = _dbl_table(key=key, data={"columns": list(df.columns), "rows": rows, "classes": classes,
                                    "tickers": tickers, "height": height}, on_open_change=lambda: None)
    if res.open:
        st.session_state["_goto"] = res.open
        st.rerun()


# ───────────────────────── sidebar ─────────────────────────
if goto := st.session_state.pop("_goto", None):
    if goto in WATCHLIST:
        st.session_state["pick"], st.session_state["custom"] = goto, ""
    else:
        st.session_state["custom"] = goto
    st.session_state["page"] = "종목 분석"
st.session_state.setdefault("pick", "NVDA")
st.session_state.setdefault("custom", "")
st.sidebar.title("어닝 대시보드")
page = st.sidebar.radio("화면", ["어닝 캘린더", "종목 분석"], key="page", label_visibility="collapsed")
st.sidebar.caption(f"데이터: Yahoo Finance · Nasdaq · Google News\n\n기준: {dt.datetime.now():%Y-%m-%d %H:%M}")
if st.sidebar.button("데이터 새로고침"):
    st.cache_data.clear()
    st.rerun()


# ───────────────────────── page: calendar ─────────────────────────
if page == "어닝 캘린더":
    st.title("다가오는 어닝")
    tab_w, tab_all = st.tabs(["내 관심종목", "미국 전체"])

    with tab_w:
        with st.spinner("관심종목 불러오는 중…"):
            ov = watch_overview(tuple(WATCHLIST))
        ov["_k"] = ov["D-day"].where(ov["D-day"] >= 0, ov["D-day"] + 10000)
        ov = ov.sort_values("_k", na_position="last").drop(columns="_k")
        soon = ov[(ov["D-day"] >= 0) & (ov["D-day"] <= 14)]
        c1, c2, c3 = st.columns(3)
        c1.metric("2주 내 어닝", f"{len(soon)}개")
        c2.metric("이번 주", f"{int((ov['D-day'].between(0, 6)).sum())}개")
        nxt = soon.iloc[0] if len(soon) else None
        c3.metric("가장 가까운 어닝", f"{nxt['티커']} · {nxt['다음 어닝']}" if nxt is not None else "-")

        show = ov[["티커", "다음 어닝", "D-day", "EPS 예상", "현재가", "일간", "시가총액", "규모"]].copy()
        dbl_table(show, show["티커"].tolist(), key="tbl_watch", height=620,
                  fmt={"현재가": lambda v: num(v), "일간": lambda v: pct(v, 2), "시가총액": fmt_big,
                       "EPS 예상": lambda v: num(v), "다음 어닝": lambda v: str(v) if v else "-",
                       "D-day": lambda v: "-" if pd.isna(v) else f"발표됨 ({-int(v)}일 전)" if v < 0 else "오늘" if v == 0 else f"D-{int(v)}"},
                  num_cols=("EPS 예상", "현재가", "일간", "시가총액"), sign_cols=("일간",))
        st.caption("행을 더블클릭하면 상세 분석으로 이동합니다. 어닝 날짜는 회사 확정 전에는 예상치일 수 있습니다. BTC-USD는 어닝이 없습니다.")

    with tab_all:
        c1, c2, c3 = st.columns([1, 1, 2])
        days = c1.slider("기간(일)", 3, 21, 10)
        min_cap = c2.selectbox("최소 시가총액", ["전체", "$300M+", "$2B+", "$10B+", "$200B+"], index=2)
        q = c3.text_input("티커/회사명 검색", "")
        with st.spinner("Nasdaq 어닝 캘린더 불러오는 중…"):
            cal = nasdaq_calendar(dt.date.today(), days)
        if cal.empty:
            st.warning("Nasdaq 캘린더를 불러오지 못했습니다. 잠시 후 새로고침해 주세요.")
        else:
            thr = {"전체": 0, "$300M+": 3e8, "$2B+": 2e9, "$10B+": 1e10, "$200B+": 2e11}[min_cap]
            df = cal[cal["cap"].fillna(0) >= thr].copy()
            if q:
                df = df[df["symbol"].str.contains(q, case=False) | df["name"].str.contains(q, case=False)]
            df["관심"] = df["symbol"].isin(WATCHLIST).map({True: "관심", False: ""})
            df["규모"] = df["cap"].map(cap_class)
            df = df.sort_values(["date", "cap"], ascending=[True, False])
            st.caption(f"{len(df)}개 기업 · 시가총액 큰 순 · 행을 더블클릭하면 상세 분석으로 이동합니다")
            for d, g in df.groupby("date"):
                st.subheader(f"{d:%m/%d} ({'월화수목금토일'[d.weekday()]}) · {len(g)}개")
                dbl_table(
                    g[["관심", "symbol", "name", "timing", "cap", "규모", "epsForecast", "lastYearEPS", "noOfEsts"]]
                    .rename(columns={"symbol": "티커", "name": "회사", "timing": "발표 시점", "cap": "시가총액",
                                     "epsForecast": "EPS 예상", "lastYearEPS": "전년 EPS", "noOfEsts": "추정 수"}),
                    g["symbol"].tolist(), key=f"tbl_{d}", fmt={"시가총액": fmt_big},
                    num_cols=("시가총액", "EPS 예상", "전년 EPS", "추정 수"))
    st.stop()


# ───────────────────────── page: ticker analysis ─────────────────────────
pick = st.sidebar.selectbox("관심종목", WATCHLIST, key="pick")
custom = st.sidebar.text_input("다른 티커 직접 입력", key="custom").strip().upper()
sym = custom or pick

with st.spinner(f"{sym} 데이터 불러오는 중…"):
    hist, info, ed, cal, rec, intraday, qinc = load_ticker(sym)

if hist.empty:
    st.error(f"'{sym}' 데이터를 찾지 못했습니다. 티커를 확인해 주세요.")
    st.stop()

close = hist["Close"]
price = info.get("regularMarketPrice") or info.get("currentPrice") or float(close.iloc[-1])
prev = info.get("regularMarketPreviousClose") or float(close.iloc[-2])
mc = info.get("marketCap")
name = info.get("longName") or info.get("shortName") or sym
next_ed = (cal.get("Earnings Date") or [None])[0]

# 헤더
st.title(f"{name} ({sym})")
h1, h2, h3, h4, h5 = st.columns(5)
h1.metric("현재가", f"${price:,.2f}", pct(price / prev - 1, 2))
h2.metric("시가총액", fmt_big(mc))
h3.metric("규모", cap_class(mc).split(" ")[0])
h4.metric("다음 어닝", str(next_ed) if next_ed else "-",
          f"D-{(next_ed - dt.date.today()).days}" if next_ed else None, delta_color="off")
h5.metric("섹터", info.get("sectorDisp") or info.get("sector") or "-")

tabs = st.tabs(["회사 분석", "주가 분석", "과거 어닝 반응", "어닝 후 예측",
                "규모", "투자 지표", "최근 뉴스", "최근 주가 변동"])

# 회사 분석
with tabs[0]:
    l, r = st.columns([3, 2])
    with l:
        st.subheader("사업 개요")
        st.write(info.get("longBusinessSummary") or "정보 없음")
        st.caption(" · ".join(filter(None, [info.get("industryDisp") or info.get("industry"), info.get("country"),
                                            f"직원 {info['fullTimeEmployees']:,}명" if info.get("fullTimeEmployees") else None,
                                            info.get("website")])))
    with r:
        st.subheader("핵심 재무")
        st.table(pd.DataFrame({
            "항목": ["매출(TTM)", "매출 성장(YoY)", "이익 성장(YoY)", "매출총이익률", "영업이익률", "순이익률", "ROE", "잉여현금흐름"],
            "값": [fmt_big(info.get("totalRevenue")), pct(info.get("revenueGrowth")), pct(info.get("earningsGrowth")),
                  pct(info.get("grossMargins")).lstrip("+"), pct(info.get("operatingMargins")).lstrip("+"),
                  pct(info.get("profitMargins")).lstrip("+"), pct(info.get("returnOnEquity")).lstrip("+"),
                  fmt_big(info.get("freeCashflow"))],
        }).set_index("항목"))
    if qinc is not None and not qinc.empty and "Total Revenue" in qinc.index:
        st.subheader("분기 매출 · 순이익")
        qq = qinc.loc[[x for x in ["Total Revenue", "Net Income"] if x in qinc.index]].T.sort_index().tail(8)
        fig = go.Figure()
        fig.add_bar(x=qq.index.strftime("%Y-%m"), y=qq["Total Revenue"], name="매출", marker_color="#3b82f6")
        if "Net Income" in qq:
            fig.add_bar(x=qq.index.strftime("%Y-%m"), y=qq["Net Income"], name="순이익", marker_color="#10b981")
        fig.update_layout(barmode="group", height=320, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h"))
        st.plotly_chart(fig, use_container_width=True)

# 주가 분석
with tabs[1]:
    rng = st.radio("기간", ["3개월", "6개월", "1년", "2년", "5년"], index=2, horizontal=True)
    n = {"3개월": 63, "6개월": 126, "1년": 252, "2년": 504, "5년": 1260}[rng]
    h = hist.copy()
    h["MA50"], h["MA200"], h["RSI"] = close.rolling(50).mean(), close.rolling(200).mean(), rsi(close)
    h = h.tail(n)
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.62, 0.18, 0.2], vertical_spacing=0.03)
    fig.add_candlestick(x=h.index, open=h["Open"], high=h["High"], low=h["Low"], close=h["Close"], name=sym,
                        increasing_line_color=UP, decreasing_line_color=DOWN, row=1, col=1)
    fig.add_scatter(x=h.index, y=h["MA50"], name="50일선", line=dict(color="#f59e0b", width=1.4), row=1, col=1)
    fig.add_scatter(x=h.index, y=h["MA200"], name="200일선", line=dict(color="#8b5cf6", width=1.4), row=1, col=1)
    if ed is not None:
        for ts in ed.index:
            d = ts.tz_convert("America/New_York").tz_localize(None)
            if h.index[0].tz_localize(None) <= d <= h.index[-1].tz_localize(None) + pd.Timedelta(days=1):
                fig.add_vline(x=d, line_dash="dot", line_color="#94a3b8", row=1, col=1)
    fig.add_bar(x=h.index, y=h["Volume"], name="거래량", marker_color="#94a3b8", row=2, col=1)
    fig.add_scatter(x=h.index, y=h["RSI"], name="RSI(14)", line=dict(color="#0ea5e9"), row=3, col=1)
    fig.add_hline(y=70, line_dash="dot", line_color=DOWN, row=3, col=1)
    fig.add_hline(y=30, line_dash="dot", line_color=UP, row=3, col=1)
    fig.update_layout(height=640, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=10, b=10),
                      legend=dict(orientation="h", y=1.02))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("점선 세로선 = 어닝 발표일")

    def ret(days):
        return close.iloc[-1] / close.iloc[-1 - days] - 1 if len(close) > days else np.nan

    ytd_base = close[close.index.year < close.index[-1].year]
    spy = spy_hist()
    spy_1y = spy.iloc[-1] / spy.iloc[-253] - 1 if len(spy) > 253 else np.nan
    cols = st.columns(7)
    for c, (lab, v) in zip(cols, [("1주", ret(5)), ("1개월", ret(21)), ("3개월", ret(63)), ("6개월", ret(126)),
                                  ("YTD", close.iloc[-1] / ytd_base.iloc[-1] - 1 if len(ytd_base) else np.nan),
                                  ("1년", ret(252)), ("1년 vs S&P500", ret(252) - spy_1y)]):
        c.metric(lab, pct(v))
    last = hist.iloc[-1]
    ma50, ma200, r14 = close.rolling(50).mean().iloc[-1], close.rolling(200).mean().iloc[-1], rsi(close).iloc[-1]
    hi52, lo52 = close.tail(252).max(), close.tail(252).min()
    pos52 = (price - lo52) / (hi52 - lo52) if hi52 > lo52 else np.nan
    trend = ("상승 추세 (주가 > 50일 > 200일)" if price > ma50 > ma200 else
             "하락 추세 (주가 < 50일 < 200일)" if price < ma50 < ma200 else "혼조 / 추세 전환 구간")
    vol = close.pct_change().tail(63).std() * np.sqrt(252)
    st.markdown(f"""
- **추세**: {trend} — 50일선 ${ma50:,.2f} ({pct(price / ma50 - 1)}), 200일선 ${ma200:,.2f} ({pct(price / ma200 - 1)})
- **RSI(14)**: {r14:.0f} → {"과매수 구간" if r14 >= 70 else "과매도 구간" if r14 <= 30 else "중립"}
- **52주 범위**: ${lo52:,.2f} ~ ${hi52:,.2f}, 현재 위치 {pos52 * 100:.0f}% (고점 대비 {pct(price / hi52 - 1)})
- **변동성(3개월 연율)**: {vol * 100:.0f}% · 베타 {num(info.get("beta"))}
""")

# 과거 어닝 반응
react = earnings_reactions(hist, ed)
with tabs[2]:
    if react.empty:
        st.info("과거 어닝 기록이 없습니다 (신규 상장 또는 어닝이 없는 자산).")
    else:
        a = react["반응일 종가"]
        c = st.columns(5)
        c[0].metric("기록된 어닝", f"{len(react)}회")
        c[1].metric("반응일 상승 확률", f"{(a > 0).mean() * 100:.0f}%")
        c[2].metric("평균 절대 변동", f"{a.abs().mean() * 100:.1f}%")
        c[3].metric("EPS 상회 비율", f"{(react['서프라이즈'] > 0).mean() * 100:.0f}%")
        c[4].metric("최대 상승 / 하락", f"{a.max() * 100:+.1f}% / {a.min() * 100:+.1f}%")
        rr = react.sort_values("발표일")
        fig = go.Figure(go.Bar(x=rr["발표일"].astype(str), y=rr["반응일 종가"] * 100,
                               marker_color=[UP if v > 0 else DOWN for v in rr["반응일 종가"]],
                               text=[f"{v * 100:+.1f}%" for v in rr["반응일 종가"]], textposition="outside",
                               customdata=rr["서프라이즈"] * 100,
                               hovertemplate="%{x}<br>반응 %{y:.1f}%<br>EPS 서프라이즈 %{customdata:.1f}%<extra></extra>"))
        fig.update_layout(height=340, yaxis_title="반응일 변동(%)", margin=dict(l=10, r=10, t=20, b=10))
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(
            react.style.format({"EPS 예상": "{:.2f}", "EPS 실제": "{:.2f}", "서프라이즈": lambda v: pct(v),
                                "갭(시가)": lambda v: pct(v), "반응일 종가": lambda v: pct(v), "5일 누적": lambda v: pct(v)})
            .map(color_pct, subset=["서프라이즈", "갭(시가)", "반응일 종가", "5일 누적"]),
            hide_index=True, use_container_width=True)
        st.caption("반응일: 장전 발표 → 당일 종가 / 전일 종가, 장후 발표 → 다음날 종가 / 발표일 종가")

# 어닝 후 예측
with tabs[3]:
    st.warning("아래는 옵션 시장 가격과 과거 통계에 근거한 **예상 변동 범위**입니다. 방향 예측이나 투자 권유가 아닙니다.")
    if not next_ed:
        st.info("예정된 어닝 날짜가 없습니다.")
    else:
        im = implied_move(sym, next_ed, price)
        hist_move = react["반응일 종가"].abs().mean() if not react.empty else np.nan
        c = st.columns(4)
        c[0].metric("다음 어닝", f"{next_ed}", f"D-{(next_ed - dt.date.today()).days}", delta_color="off")
        c[1].metric("옵션 내재 변동폭", f"±{im['move'] * 100:.1f}%" if im else "-",
                    f"만기 {im['expiry']} · 행사가 {im['strike']:g}" if im else None, delta_color="off")
        c[2].metric("과거 평균 변동폭", f"±{hist_move * 100:.1f}%" if not np.isnan(hist_move) else "-")
        if im and not np.isnan(hist_move):
            ratio = hist_move / im["move"]
            c[3].metric("과거 / 내재 비율", f"{ratio:.2f}x",
                        "옵션이 과거보다 큰 변동 반영" if ratio < 1 else "과거 변동이 옵션 예상보다 큼", delta_color="off")
        move = im["move"] if im else hist_move
        if move and not np.isnan(move):
            st.subheader("예상 주가 범위 (발표 다음 거래일)")
            lo, hi = price * (1 - move), price * (1 + move)
            lo2, hi2 = price * (1 - 2 * move), price * (1 + 2 * move)
            # 도형(shape)만 있으면 x축이 자동으로 안 잡혀(-1~6) 빈 차트가 됨 → 점 trace를 넣고 범위도 직접 지정
            pts = [(lo2, "-2σ"), (lo, "-1σ"), (price, "현재"), (hi, "+1σ"), (hi2, "+2σ")]
            fig = go.Figure(go.Scatter(
                x=[x for x, _ in pts], y=[0.5] * 5, mode="markers+text",
                marker=dict(size=[9, 9, 16, 9, 9], color=["#8a90aa", "#5a6fd6", "#eb6834", "#5a6fd6", "#8a90aa"]),
                text=[f"<b>{t}</b><br>${x:,.2f}" for x, t in pts], textposition="top center", textfont=dict(size=14),
                customdata=[f"{x / price - 1:+.1%}" for x, _ in pts],
                hovertemplate="%{text}<br>현재가 대비 %{customdata}<extra></extra>", showlegend=False))
            fig.add_shape(type="rect", x0=lo2, x1=hi2, y0=0.3, y1=0.7, fillcolor="#8a90aa", opacity=0.22, line_width=0, layer="below")
            fig.add_shape(type="rect", x0=lo, x1=hi, y0=0.3, y1=0.7, fillcolor="#5a6fd6", opacity=0.35, line_width=0, layer="below")
            fig.add_shape(type="line", x0=price, x1=price, y0=0.3, y1=0.5, line=dict(color="#eb6834", width=3))
            pad = (hi2 - lo2) * 0.06
            fig.update_layout(height=200, yaxis=dict(visible=False, range=[0.15, 1.25], fixedrange=True),
                              xaxis=dict(range=[lo2 - pad, hi2 + pad], tickprefix="$", title="주가($)"),
                              margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)
            st.caption("1σ 범위 ≈ 약 68% 확률 구간. 옵션 내재 변동폭이 없으면 과거 평균으로 대체합니다.")
        if not react.empty:
            st.subheader("시나리오별 과거 반응")
            beat = react[react["서프라이즈"] > 0]["반응일 종가"]
            miss = react[react["서프라이즈"] <= 0]["반응일 종가"]
            s = pd.DataFrame({
                "시나리오": ["EPS 상회 (Beat)", "EPS 부합/하회 (Miss)"],
                "횟수": [len(beat), len(miss)],
                "평균 반응": [beat.mean(), miss.mean()],
                "상승 확률": [(beat > 0).mean() if len(beat) else np.nan, (miss > 0).mean() if len(miss) else np.nan],
            })
            st.dataframe(s.style.format({"평균 반응": lambda v: pct(v), "상승 확률": lambda v: "-" if pd.isna(v) else f"{v * 100:.0f}%"}),
                         hide_index=True, use_container_width=True)
            st.caption("EPS를 상회해도 주가가 하락하는 경우가 많다면, 기대치가 이미 주가에 반영돼 있다는 뜻일 수 있습니다.")
        if cal:
            st.subheader("컨센서스")
            st.table(pd.DataFrame({
                "": ["EPS", "매출"],
                "평균": [num(cal.get("Earnings Average")), fmt_big(cal.get("Revenue Average"))],
                "최저": [num(cal.get("Earnings Low")), fmt_big(cal.get("Revenue Low"))],
                "최고": [num(cal.get("Earnings High")), fmt_big(cal.get("Revenue High"))],
            }).set_index(""))

# 규모
with tabs[4]:
    st.metric("분류", cap_class(mc), fmt_big(mc), delta_color="off")
    bands = [("초소형 Micro", 0, 0.3), ("소형 Small", 0.3, 2), ("중형 Mid", 2, 10), ("대형 Large", 10, 200), ("초대형 Mega", 200, 6000)]
    fig = go.Figure()
    for i, (lab, a, b) in enumerate(bands):
        fig.add_bar(y=[""], x=[np.log10(b) - np.log10(max(a, 0.05))], base=[np.log10(max(a, 0.05))], orientation="h",
                    name=lab, text=lab, textposition="inside",
                    marker_color=["#e2e8f0", "#cbd5e1", "#94a3b8", "#64748b", "#475569"][i])
    if mc:
        fig.add_vline(x=np.log10(mc / 1e9), line_color=DOWN, line_width=3,
                      annotation_text=f"{sym} {fmt_big(mc)}", annotation_position="top")
    fig.update_layout(barmode="stack", height=180, showlegend=False, margin=dict(l=10, r=10, t=40, b=10),
                      xaxis=dict(tickvals=np.log10([0.3, 2, 10, 200, 1000, 5000]),
                                 ticktext=["$300M", "$2B", "$10B", "$200B", "$1T", "$5T"]))
    st.plotly_chart(fig, use_container_width=True)
    st.markdown("""
| 분류 | 시가총액 | 일반적 특성 |
|---|---|---|
| 초대형주 | $200B 이상 | 유동성 최고, 지수 영향 큼, 어닝 반응 상대적으로 작음 |
| 대형주 | $10B ~ $200B | 안정적, 애널리스트 커버리지 많음 |
| 중형주 | $2B ~ $10B | 성장성과 안정성 중간 |
| 소형주 | $300M ~ $2B | 변동성 큼, 어닝 반응이 큼 |
| 초소형주 | $300M 미만 | 유동성 낮음, 고위험 |
""")

# 투자 지표
with tabs[5]:
    l, m, r = st.columns(3)
    with l:
        st.subheader("밸류에이션")
        st.table(pd.DataFrame({"값": {
            "PER (TTM)": num(info.get("trailingPE"), 1), "PER (선행)": num(info.get("forwardPE"), 1),
            "PEG": num(info.get("trailingPegRatio") or info.get("pegRatio")), "PSR": num(info.get("priceToSalesTrailing12Months")),
            "PBR": num(info.get("priceToBook")), "EV/EBITDA": num(info.get("enterpriseToEbitda"), 1),
            "EPS (TTM)": num(info.get("trailingEps")), "배당수익률": f"{info['dividendYield']:.2f}%" if info.get("dividendYield") else "-",
        }}))
    with m:
        st.subheader("재무 건전성 · 수급")
        st.table(pd.DataFrame({"값": {
            "현금": fmt_big(info.get("totalCash")), "부채": fmt_big(info.get("totalDebt")),
            "부채비율(D/E)": num(info.get("debtToEquity"), 1), "유동비율": num(info.get("currentRatio")),
            "공매도 비율(유통)": pct(info.get("shortPercentOfFloat")).lstrip("+"), "숏 커버 일수": num(info.get("shortRatio"), 1),
            "기관 보유": pct(info.get("heldPercentInstitutions")).lstrip("+"), "내부자 보유": pct(info.get("heldPercentInsiders")).lstrip("+"),
        }}))
    with r:
        st.subheader("애널리스트")
        tgt = info.get("targetMeanPrice")
        st.metric("평균 목표가", f"${tgt:,.2f}" if tgt else "-", pct(tgt / price - 1) if tgt else None)
        st.caption(f"범위 ${num(info.get('targetLowPrice'))} ~ ${num(info.get('targetHighPrice'))} · "
                   f"{info.get('numberOfAnalystOpinions', '-')}명 · 의견 {str(info.get('recommendationKey', '-')).upper()}")
        if rec is not None and not rec.empty:
            r0 = rec.iloc[0]
            fig = go.Figure(go.Bar(x=["강력매수", "매수", "보유", "매도", "강력매도"],
                                   y=[r0["strongBuy"], r0["buy"], r0["hold"], r0["sell"], r0["strongSell"]],
                                   marker_color=[UP, "#86efac", "#cbd5e1", "#fca5a5", DOWN]))
            fig.update_layout(height=220, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)
    st.caption(f"평균 거래량(3개월) {info.get('averageVolume', 0):,} · 거래소 {info.get('fullExchangeName', '-')}")

# 최근 뉴스
with tabs[6]:
    short = (info.get("shortName") or sym).split(",")[0].replace(" Inc.", "").replace(" Corporation", "")
    news = google_news(f"\"{sym}\" OR \"{short}\" stock")
    if not news:
        st.info("최근 2주 뉴스를 찾지 못했습니다.")
    for it in news:
        when = it["when"].astimezone().strftime("%m/%d %H:%M") if it["when"] else ""
        st.markdown(f"**[{it['title']}]({it['link']})**  \n<small>{it['source']} · {when}</small>", unsafe_allow_html=True)

# 최근 주가 변동
with tabs[7]:
    if not intraday.empty:
        fig = go.Figure(go.Scatter(x=intraday.index, y=intraday["Close"], fill="tozeroy",
                                   line=dict(color=UP if intraday["Close"].iloc[-1] >= intraday["Close"].iloc[0] else DOWN)))
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                          yaxis=dict(range=[intraday["Low"].min() * 0.995, intraday["High"].max() * 1.005]),
                          xaxis=dict(rangebreaks=[dict(bounds=["sat", "mon"]), dict(bounds=[16, 9.5], pattern="hour")]))
        st.subheader("최근 5거래일 (15분봉)")
        st.plotly_chart(fig, use_container_width=True)
    avgv = hist["Volume"].tail(63).mean()
    last = hist.tail(15).copy()
    last["등락"] = last["Close"].pct_change()
    last.loc[last.index[0], "등락"] = hist["Close"].iloc[-15] / hist["Close"].iloc[-16] - 1
    last["거래량/3M평균"] = last["Volume"] / avgv
    tbl = last[["Open", "High", "Low", "Close", "등락", "Volume", "거래량/3M평균"]].iloc[::-1]
    tbl.index = tbl.index.strftime("%Y-%m-%d (%a)")
    st.subheader("최근 15거래일")
    st.dataframe(tbl.rename(columns={"Open": "시가", "High": "고가", "Low": "저가", "Close": "종가", "Volume": "거래량"})
                 .style.format({"시가": "{:,.2f}", "고가": "{:,.2f}", "저가": "{:,.2f}", "종가": "{:,.2f}",
                                "등락": lambda v: pct(v, 2), "거래량": "{:,.0f}", "거래량/3M평균": "{:.2f}x"})
                 .map(color_pct, subset=["등락"]), use_container_width=True)
    big = last[last["등락"].abs() > 2 * close.pct_change().tail(252).std()]
    if len(big):
        st.caption("큰 움직임(2σ 이상): " + ", ".join(f"{d:%m/%d} {v * 100:+.1f}%" for d, v in big["등락"].items()))
