"""마켓 레이더 — 요즘 개인 트레이더들이 많이 보는 네 가지를 한 화면에: 히트맵 · 레딧 인기 종목 · 이상 옵션 거래 · 내부자 매매.

    .venv/bin/python apps/radar.py [--open]      → http://localhost:8505
    python apps/radar.py --out DIR               → DIR/radar.json (포트폴리오 사이트의 demos/radar.html 이 읽는 파일)

- 화면은 사이트와 같은 파일(site/demos/radar.html)을 씀 — 로컬에서는 공책 테마와 한국어로, 데이터는 /api/data 로 바꿔서 보여 줌
- 종목: 어닝 대시보드와 같은 관심종목(WATCHLIST) + 지수·섹터 ETF
- 데이터: Yahoo Finance(가격·옵션 체인·내부자 거래), ApeWisdom(레딧 언급 수). 모두 무료이고 늦거나 틀릴 수 있음
- 이상 옵션 거래는 '거래량이 미결제약정보다 유난히 많은 계약'을 고른 근사치 — 누가 샀는지 팔았는지는 알 수 없음
- 의회 매매는 믿을 만한 무료 데이터가 없어 넣지 않음 (2026-11 기준 공개 소스가 모두 막혀 있었음)
"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
import re
import sys
import threading
import time
import urllib.request
import warnings
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yfinance as yf

warnings.filterwarnings("ignore")

APPS = Path(__file__).resolve().parent
ROOT = APPS.parent
PAGE = ROOT / "site" / "demos" / "radar.html"
JOURNAL = APPS / "journal"
CACHE = JOURNAL / "radar_cache.json"
PORT = 8505
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
MARKET = [("SPY", "S&P 500", "S&P 500"), ("QQQ", "Nasdaq 100", "나스닥 100"), ("IWM", "Russell 2000", "러셀 2000"), ("DIA", "Dow 30", "다우 30"),
          ("XLK", "Technology", "기술"), ("XLC", "Communication", "커뮤니케이션"), ("XLY", "Consumer Disc.", "임의소비재"),
          ("XLF", "Financials", "금융"), ("XLV", "Health Care", "헬스케어"), ("XLI", "Industrials", "산업재"), ("XLE", "Energy", "에너지"),
          ("XLP", "Staples", "필수소비재"), ("XLB", "Materials", "소재"), ("XLU", "Utilities", "유틸리티"), ("XLRE", "Real Estate", "부동산")]


class _MemTzCache:
    """yfinance 시간대 캐시를 메모리로 (기본 SQLite 캐시는 스레드마다 연결을 열고 안 닫음 — traders.py 와 같은 처리)."""
    tz_db = None

    def __init__(self):
        self.d = {}

    def lookup(self, tkr):
        return self.d.get(tkr)

    def store(self, tkr, tz):
        self.d[tkr] = tz


yf.cache._TzCacheManager._tz_cache = _MemTzCache()


def watchlist() -> list[str]:
    """어닝 대시보드(earnings_app.py)의 WATCHLIST 를 그대로 읽음 — 관심종목은 한 곳에서만 관리."""
    try:
        body = re.search(r"^WATCHLIST = \[(.*?)\]", (APPS / "earnings_app.py").read_text(), re.S | re.M).group(1)
        return re.findall(r'"([^"]+)"', body)
    except Exception:
        return ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA"]


def num(x):
    try:
        x = float(x)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def ret(c: list[float], n: int):
    return c[-1] / c[-1 - n] - 1 if len(c) > n and c[-1 - n] else None


# ───────────────────────── 히트맵 ─────────────────────────
def closes(syms: list[str]) -> dict[str, tuple[list[float], list[str]]]:
    out = {}
    try:
        d = yf.download(sorted(set(syms)), period="1y", interval="1d", progress=False, group_by="ticker", auto_adjust=True, threads=True)
    except Exception:
        return out
    for s in set(syms):
        try:
            c = d[s]["Close"].dropna()
            if len(c):
                out[s] = ([float(v) for v in c.values], [str(i.date()) for i in c.index])
        except Exception:
            pass
    return out


def cap_of(sym: str):
    try:
        i = yf.Ticker(sym).info or {}
        return num(i.get("marketCap")), i.get("shortName") or sym
    except Exception:
        return None, sym


def heat_row(sym: str, name: str, px: tuple[list[float], list[str]] | None, cap=None, ko: str = "") -> dict | None:
    if not px:
        return None
    c, days = px
    ytd = next((i for i, d in enumerate(days) if d[:4] == days[-1][:4]), 0)
    return dict(sym=sym, name=name, ko=ko, cap=cap, price=c[-1],
                r=dict(d=ret(c, 1), w=ret(c, 5), m=ret(c, 21), q=ret(c, 63), y=(c[-1] / c[ytd - 1] - 1 if ytd > 0 else None)))


def build_heat(wl: list[str]) -> dict:
    px = closes(wl + [m[0] for m in MARKET])
    with ThreadPoolExecutor(8) as ex:
        caps = dict(zip(wl, ex.map(cap_of, wl)))
    market = [r for s, en, ko in MARKET if (r := heat_row(s, en, px.get(s), ko=ko))]
    stocks = [r for s in wl if (r := heat_row(s, caps[s][1], px.get(s), caps[s][0]))]
    stocks.sort(key=lambda r: -(r["cap"] or 0))
    return dict(market=market, stocks=stocks, asof=max((p[1][-1] for p in px.values()), default=""))


# ───────────────────────── 레딧 인기 종목 ─────────────────────────
def build_reddit(wl: list[str], prices: dict[str, float | None]) -> list[dict]:
    """ApeWisdom — 주식 관련 레딧(월스트리트베츠 등)에서 지난 24시간 동안 언급된 횟수 순위."""
    req = urllib.request.Request("https://apewisdom.io/api/v1.0/filter/all-stocks/page/1", headers=UA)
    rows = json.loads(urllib.request.urlopen(req, timeout=20).read())["results"][:30]
    need = [r["ticker"] for r in rows if r["ticker"] not in prices]
    if need:
        prices = dict(prices)
        for s, (c, _) in closes(need).items():
            prices[s] = ret(c, 1)
    out = []
    for r in rows:
        m, m0 = num(r.get("mentions")) or 0, num(r.get("mentions_24h_ago")) or 0
        out.append(dict(rank=int(r["rank"]), rank0=int(r.get("rank_24h_ago") or 0), sym=r["ticker"], name=html.unescape(r.get("name") or ""),
                        mentions=int(m), mentions0=int(m0), surge=(m / m0 - 1 if m0 else None), upvotes=int(num(r.get("upvotes")) or 0),
                        mine=r["ticker"] in wl, chg=prices.get(r["ticker"])))
    return out


# ───────────────────────── 이상 옵션 거래 ─────────────────────────
def chain_of(sym: str) -> list[dict]:
    """가까운 만기 3개에서 거래량이 미결제약정의 2배 이상이고 거래 대금이 25만 달러 이상인 계약."""
    out = []
    try:
        t = yf.Ticker(sym)
        spot = num(t.fast_info.get("lastPrice"))
        for exp in list(t.options)[:3]:
            ch = t.option_chain(exp)
            for kind, df in (("C", ch.calls), ("P", ch.puts)):
                for r in df.itertuples():
                    vol, oi, last = num(r.volume) or 0, num(r.openInterest) or 0, num(r.lastPrice) or 0
                    prem = vol * last * 100
                    otm = (num(r.strike) / spot - 1) * (1 if kind == "C" else -1) if spot else None
                    if otm is not None and otm < -0.10:  # 깊은 내가격은 마지막 체결가가 낡아 대금이 부풀려짐
                        continue
                    if vol >= 500 and vol >= 2 * max(oi, 1) and prem >= 250_000:
                        out.append(dict(sym=sym, kind=kind, strike=num(r.strike), exp=exp, vol=int(vol), oi=int(oi), ratio=vol / max(oi, 1),
                                        last=last, prem=prem, iv=num(r.impliedVolatility),
                                        otm=otm))
    except Exception:
        pass
    return out


def build_options(wl: list[str]) -> dict:
    syms = [s for s in wl if "-" not in s]
    with ThreadPoolExecutor(6) as ex:
        rows = [r for part in ex.map(chain_of, syms) for r in part]
    rows.sort(key=lambda r: -r["prem"])
    return dict(rows=rows[:60], calls=sum(r["prem"] for r in rows if r["kind"] == "C"), puts=sum(r["prem"] for r in rows if r["kind"] == "P"),
                n=len(rows))


# ───────────────────────── 내부자 매매 ─────────────────────────
def insiders_of(sym: str) -> list[dict]:
    """최근 90일 임원·이사의 장내 매수와 매도 (주식 보상·증여·옵션 행사는 뺌)."""
    out = []
    try:
        df = yf.Ticker(sym).insider_transactions
        if df is None or df.empty:
            return out
        cut = dt.date.today() - dt.timedelta(days=90)
        for r in df.to_dict("records"):
            day = r.get("Start Date")
            day = day.date() if hasattr(day, "date") else None
            text = str(r.get("Text") or "")
            side = "buy" if text.startswith("Purchase") else "sell" if text.startswith("Sale") else None
            if not side or not day or day < cut:
                continue
            out.append(dict(sym=sym, side=side, date=str(day), who=str(r.get("Insider") or "").title(), role=str(r.get("Position") or ""),
                            shares=int(num(r.get("Shares")) or 0), value=num(r.get("Value")) or 0))
    except Exception:
        pass
    return out


def build_insiders(wl: list[str]) -> dict:
    syms = [s for s in wl if "-" not in s]
    with ThreadPoolExecutor(6) as ex:
        rows = [r for part in ex.map(insiders_of, syms) for r in part]
    by = {}
    for r in rows:
        b = by.setdefault(r["sym"], dict(sym=r["sym"], buys=0, buy_value=0.0, sells=0, sell_value=0.0))
        b["buys" if r["side"] == "buy" else "sells"] += 1
        b["buy_value" if r["side"] == "buy" else "sell_value"] += r["value"]
    rows.sort(key=lambda r: (r["side"] != "buy", -r["value"]))
    return dict(rows=[r for r in rows if r["side"] == "buy"][:30] + [r for r in rows if r["side"] == "sell"][:40],
                by=sorted(by.values(), key=lambda b: -(b["buy_value"] + b["sell_value"])),
                buys=sum(r["side"] == "buy" for r in rows), sells=sum(r["side"] == "sell" for r in rows),
                buy_value=sum(r["value"] for r in rows if r["side"] == "buy"), sell_value=sum(r["value"] for r in rows if r["side"] == "sell"))


# ───────────────────────── 모으기 ─────────────────────────
def build(parts=("heat", "reddit", "options", "insiders"), old: dict | None = None) -> dict:
    """parts 에 있는 것만 새로 받고 나머지는 old 값을 유지. 한 부분이 실패해도 나머지는 살림."""
    data = dict(old or {})
    wl = watchlist()
    errs = {}
    if "heat" in parts:
        try:
            data["heat"] = build_heat(wl)
        except Exception as e:
            errs["heat"] = str(e)
    if "reddit" in parts:
        try:
            prices = {r["sym"]: r["r"]["d"] for r in data.get("heat", {}).get("stocks", []) + data.get("heat", {}).get("market", [])}
            data["reddit"] = build_reddit(wl, prices)
        except Exception as e:
            errs["reddit"] = str(e)
    if "options" in parts:
        try:
            data["options"] = build_options(wl)
        except Exception as e:
            errs["options"] = str(e)
    if "insiders" in parts:
        try:
            data["insiders"] = build_insiders(wl)
        except Exception as e:
            errs["insiders"] = str(e)
    data.update(updated=time.time(), errors=errs)
    return data


def clean(x):
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    return x


LOCK = threading.Lock()
STATE = dict(data={}, loading=False)
EVERY = dict(heat=15 * 60, reddit=15 * 60, options=30 * 60, insiders=12 * 3600)
LAST = dict.fromkeys(EVERY, 0.0)


def refresh(parts):
    with LOCK:
        if STATE["loading"]:
            return
        STATE["loading"] = True
    try:
        data = clean(build(parts, STATE["data"]))
        now = time.time()
        for p in parts:
            if p not in data["errors"]:
                LAST[p] = now
        with LOCK:
            STATE["data"] = data
        JOURNAL.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(dict(data=data, last=LAST), ensure_ascii=False))
    finally:
        with LOCK:
            STATE["loading"] = False


def keeper():
    try:
        c = json.loads(CACHE.read_text())
        STATE["data"] = c["data"]
        LAST.update({k: v for k, v in c.get("last", {}).items() if k in LAST})
    except Exception:
        pass
    while True:
        due = [p for p in EVERY if time.time() - LAST[p] > EVERY[p]]
        if due:
            refresh(due)
        time.sleep(60)


# ───────────────────────── 서버 ─────────────────────────
def page() -> str:
    """사이트의 demos/radar.html 을 그대로 쓰되, 공책 테마를 끼워 넣고 한국어·로컬 데이터로 시작하게 함."""
    css = (APPS / "notebook.css").read_text() if (APPS / "notebook.css").exists() else ""
    font = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            '<link href="https://fonts.googleapis.com/css2?family=Gaegu:wght@400;700&display=swap" rel="stylesheet">')
    local = '<script>window.RADAR_LOCAL={data:"/api/data",lang:"ko"}</script>'
    return PAGE.read_text().replace("</head>", f"{font}<style>{css}</style>{local}</head>", 1)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self._send(200, page().encode(), "text/html; charset=utf-8")
        elif path == "/api/data":
            with LOCK:
                body = dict(STATE["data"], loading=STATE["loading"])
            self._send(200, json.dumps(body, ensure_ascii=False).encode(), "application/json; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path == "/api/refresh":
            threading.Thread(target=refresh, args=(tuple(EVERY),), daemon=True).start()
            self._send(200, b'{"ok":true}', "application/json")
        else:
            self._send(404, b"not found", "text/plain")


def export(out: Path) -> int:
    data = clean(build())
    if not data.get("heat", {}).get("stocks"):
        print("no prices came back from Yahoo; keeping the previous file", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    (out / "radar.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {out / 'radar.json'}: heat {len(data['heat']['stocks'])}, reddit {len(data.get('reddit', []))}, "
          f"options {len(data.get('options', {}).get('rows', []))}, insiders {len(data.get('insiders', {}).get('rows', []))}, errors {data['errors']}")
    return 0


def main():
    if "--out" in sys.argv:
        sys.exit(export(Path(sys.argv[sys.argv.index("--out") + 1])))
    threading.Thread(target=keeper, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    if "--open" in sys.argv:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
