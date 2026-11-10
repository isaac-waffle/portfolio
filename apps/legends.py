"""월가 전설 5인 — 버핏·멍거·그레이엄·린치·드러켄밀러의 알려진 기준을 규칙으로 옮겨, 종목마다 사자/중립/팔자와 이유를 보여 줌.

    .venv/bin/python apps/legends.py [--open]      → http://localhost:8504
    python apps/legends.py --out DIR               → DIR/legends.json (포트폴리오 사이트의 demos/legends.html 이 읽는 파일)

- 종목: 어닝 대시보드와 같은 관심종목 50개(WATCHLIST) + 화면에서 직접 넣은 티커(apps/journal/legends_extra.json)
- 데이터: Yahoo Finance (재무 요약 + 1년 일봉). 6시간마다 새로 받고 apps/journal/legends_cache.json 에 저장
- Claude API를 쓰지 않는 규칙 기반 — 실제 인물의 의견이 아니고, 매매 권유도 아님
- 기준을 바꾸려면 아래 buffett() … druckenmiller() 의 숫자를 고치면 됨
"""
from __future__ import annotations

import json
import math
import re
import sys
import threading
import time
import warnings
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yfinance as yf

warnings.filterwarnings("ignore")

APPS = Path(__file__).resolve().parent
PAGE = APPS / "legends.html"
JOURNAL = APPS / "journal"
CACHE = JOURNAL / "legends_cache.json"
EXTRA = JOURNAL / "legends_extra.json"
PORT = 8504
TTL = 6 * 3600
SYM_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")


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
        src = (APPS / "earnings_app.py").read_text()
        body = re.search(r"^WATCHLIST = \[(.*?)\]", src, re.S | re.M).group(1)
        return re.findall(r'"([^"]+)"', body)
    except Exception:
        return ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA"]


def load_extra() -> list[str]:
    try:
        return [s for s in json.loads(EXTRA.read_text()) if SYM_RE.match(s)]
    except Exception:
        return []


def save_extra(syms: list[str]):
    JOURNAL.mkdir(parents=True, exist_ok=True)
    EXTRA.write_text(json.dumps(syms))


# ───────────────────────── 데이터 ─────────────────────────
FIELDS = ("shortName quoteType sector industry marketCap currency financialCurrency regularMarketPrice trailingPE forwardPE "
          "priceToBook pegRatio trailingPegRatio returnOnEquity grossMargins operatingMargins profitMargins debtToEquity "
          "currentRatio freeCashflow revenueGrowth earningsGrowth trailingEps forwardEps bookValue dividendYield "
          "fiftyTwoWeekHigh").split()


def num(x):
    try:
        x = float(x)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def fetch_info(sym: str) -> dict:
    try:
        i = yf.Ticker(sym).info or {}
    except Exception:
        i = {}
    return {k: i.get(k) for k in FIELDS}


def fetch_prices(syms: list[str]) -> dict[str, list[float]]:
    """1년 일봉 종가. SPY 는 상대 강도와 시장 추세용으로 항상 같이 받음."""
    want = sorted(set(syms) | {"SPY"})
    out = {}
    try:
        d = yf.download(want, period="1y", interval="1d", progress=False, group_by="ticker", auto_adjust=True, threads=True)
    except Exception:
        return out
    for s in want:
        try:
            c = d[s]["Close"].dropna()
            if len(c):
                out[s] = [float(v) for v in c.values]
        except Exception:
            pass
    return out


def ret(c: list[float], n: int):
    return c[-1] / c[-1 - n] - 1 if len(c) > n and c[-1 - n] else None


def sma(c: list[float], n: int):
    return sum(c[-n:]) / n if len(c) >= n else None


def facts(sym: str, info: dict, closes: list[float], spy: list[float]) -> dict:
    """규칙이 쓰는 숫자를 한데 모음 (없으면 None)."""
    g = lambda k: num(info.get(k))
    price = closes[-1] if closes else g("regularMarketPrice")
    eps, fpe, pe = g("trailingEps"), g("forwardPE"), g("trailingPE")
    fcf, mc = g("freeCashflow"), g("marketCap")
    same_ccy = info.get("financialCurrency") in (None, info.get("currency"))  # ADR 은 재무 통화가 달라 시총과 못 나눔
    hi = max(closes) if closes else g("fiftyTwoWeekHigh")
    r3, s3 = ret(closes, 63), ret(spy, 63)
    return dict(
        sym=sym, name=info.get("shortName") or sym, kind=info.get("quoteType") or "", sector=info.get("sector") or "",
        price=price, chg=ret(closes, 1), mc=mc,
        roe=g("returnOnEquity"), gm=g("grossMargins"), om=g("operatingMargins"), pm=g("profitMargins"),
        de=g("debtToEquity"), cr=g("currentRatio"), fcf=fcf, fcfy=(fcf / mc if fcf is not None and mc and same_ccy else None),
        pe=pe if pe and pe > 0 else None, fpe=fpe if fpe and fpe > 0 else None, pb=g("priceToBook"),
        peg=g("trailingPegRatio") or g("pegRatio"), eps=eps, feps=g("forwardEps"), bv=g("bookValue"),
        rg=g("revenueGrowth"), eg=g("earningsGrowth"), div=g("dividendYield"),
        ma50=sma(closes, 50), ma200=sma(closes, 200), r6=ret(closes, 126), r3=r3,
        rs3=(r3 - s3 if r3 is not None and s3 is not None else None),
        off_hi=(price / hi - 1 if price and hi else None),
        spy_up=(spy[-1] > sma(spy, 200) if len(spy) >= 200 else None),
    )


# ───────────────────────── 다섯 사람의 규칙 ─────────────────────────
# 이유 문구는 한국어(로컬 화면)와 영어(포트폴리오 사이트)를 나란히 적음: c.add(점수, 한국어, 영어)
pct = lambda x: f"{x * 100:.0f}%"
LEGENDS = [
    dict(key="buffett", name="버핏", full="워런 버핏", motto="좋은 사업을 적당한 가격에, 오래",
         name_en="Buffett", full_en="Warren Buffett", motto_en="A good business at a sensible price, held for years"),
    dict(key="munger", name="멍거", full="찰리 멍거", motto="훌륭한 기업만. 망하는 길부터 피한다",
         name_en="Munger", full_en="Charlie Munger", motto_en="Only great companies. First avoid what kills you"),
    dict(key="graham", name="그레이엄", full="벤저민 그레이엄", motto="싸게 사서 안전마진을 확보한다",
         name_en="Graham", full_en="Benjamin Graham", motto_en="Buy cheap, with a margin of safety"),
    dict(key="lynch", name="린치", full="피터 린치", motto="성장에 비해 싼 주식 (PEG)",
         name_en="Lynch", full_en="Peter Lynch", motto_en="Growth at a reasonable price (PEG)"),
    dict(key="druck", name="드러켄밀러", full="스탠리 드러켄밀러", motto="추세와 유동성을 따라 크게, 틀리면 바로 손절",
         name_en="Druckenmiller", full_en="Stanley Druckenmiller", motto_en="Ride trend and liquidity, cut losers fast"),
]


class Card:
    """한 사람의 채점표 — add(점수, 한국어 이유, 영어 이유)로 쌓고 verdict()로 판정."""

    def __init__(self):
        self.items = []

    def add(self, pts: int, ko: str, en: str):
        self.items.append((pts, ko, en))

    def verdict(self, buy: int, sell: int, need: int = 3) -> dict:
        score = sum(i[0] for i in self.items)
        if len(self.items) < need:
            v, line, line_en = "보류", "판단할 숫자가 부족함", "Not enough data to judge"
        else:
            v = "사자" if score >= buy else "팔자" if score <= sell else "중립"
            side = [i for i in self.items if (i[0] > 0) == (v != "팔자") and i[0]] or self.items
            _, line, line_en = max(side, key=lambda i: abs(i[0]))
        return dict(v=v, score=score, line=line, line_en=line_en, why=[dict(p=p, t=t, e=e) for p, t, e in self.items])


def fixed(v: str, ko: str, en: str) -> dict:
    return dict(v=v, score=0, line=ko, line_en=en, why=[dict(p=-1 if v == "팔자" else 0, t=ko, e=en)])


def eff_pe(f):
    return f["fpe"] or f["pe"]


def buffett(f) -> dict:
    c = Card()
    if (x := f["roe"]) is not None:
        if x >= .20: c.add(2, f"ROE {pct(x)} — 주주 돈을 굴리는 솜씨가 뛰어남", f"ROE {pct(x)} — excellent return on shareholders' money")
        elif x >= .15: c.add(1, f"ROE {pct(x)} — 괜찮은 자본 수익률", f"ROE {pct(x)} — decent return on equity")
        elif x < 0: c.add(-2, f"ROE {pct(x)} — 자본을 까먹는 중", f"ROE {pct(x)} — destroying capital")
        elif x < .08: c.add(-1, f"ROE {pct(x)} — 은행 이자보다 조금 나은 수준", f"ROE {pct(x)} — barely better than a savings account")
        else: c.add(0, f"ROE {pct(x)} — 평범", f"ROE {pct(x)} — ordinary")
    if (x := f["om"]) is not None:
        if x >= .25: c.add(2, f"영업이익률 {pct(x)} — 가격 결정력, 해자가 보임", f"Operating margin {pct(x)} — pricing power, a visible moat")
        elif x >= .15: c.add(1, f"영업이익률 {pct(x)} — 준수", f"Operating margin {pct(x)} — solid")
        elif x < 0: c.add(-2, f"영업이익률 {pct(x)} — 본업에서 돈을 못 범", f"Operating margin {pct(x)} — the core business loses money")
        elif x < .05: c.add(-1, f"영업이익률 {pct(x)} — 해자가 얕음", f"Operating margin {pct(x)} — a shallow moat")
        else: c.add(0, f"영업이익률 {pct(x)} — 평범", f"Operating margin {pct(x)} — ordinary")
    if (x := f["de"]) is not None and f["sector"] != "Financial Services":
        if x < 50: c.add(1, f"부채비율 {x:.0f}% — 빚이 적어 불황에도 버팀", f"Debt/equity {x:.0f}% — little debt, survives a downturn")
        elif x > 300: c.add(-2, f"부채비율 {x:.0f}% — 빚이 너무 많음", f"Debt/equity {x:.0f}% — far too much debt")
        elif x > 150: c.add(-1, f"부채비율 {x:.0f}% — 빚 부담", f"Debt/equity {x:.0f}% — heavy debt load")
        else: c.add(0, f"부채비율 {x:.0f}% — 무난", f"Debt/equity {x:.0f}% — acceptable")
    if (x := f["fcf"]) is not None:
        if x <= 0: c.add(-1, "잉여현금흐름이 마이너스 — 주주에게 돌아올 현금이 없음", "Negative free cash flow — no cash left for owners")
        elif (y := f["fcfy"]) is not None and y >= .04: c.add(2, f"잉여현금 수익률 {y * 100:.1f}% — 현금을 넉넉히 벌어 줌", f"Free-cash-flow yield {y * 100:.1f}% — throws off plenty of cash")
        else: c.add(1, "잉여현금흐름 플러스", "Positive free cash flow")
    if (x := eff_pe(f)) is not None:
        if x <= 15: c.add(2, f"PER {x:.0f}배 — 가격이 적당함", f"P/E {x:.0f} — a sensible price")
        elif x <= 25: c.add(1, f"PER {x:.0f}배 — 좋은 사업이라면 낼 만한 값", f"P/E {x:.0f} — fair for a good business")
        elif x > 40: c.add(-2, f"PER {x:.0f}배 — 너무 비쌈, 기다린다", f"P/E {x:.0f} — too expensive, wait")
        elif x > 30: c.add(-1, f"PER {x:.0f}배 — 비싼 편", f"P/E {x:.0f} — on the expensive side")
        else: c.add(0, f"PER {x:.0f}배 — 싸지는 않음", f"P/E {x:.0f} — not cheap")
    if (x := f["eps"]) is not None and x < 0:
        c.add(-1 if f["fpe"] else -2, "최근 1년 적자 — 이익을 예측하기 어려운 사업", "Loss over the last year — earnings are hard to predict")
    return c.verdict(5, 0)


def munger(f) -> dict:
    c = Card()
    if (x := f["roe"]) is not None:
        if x >= .20: c.add(2, f"ROE {pct(x)} — 훌륭한 기업의 조건", f"ROE {pct(x)} — the mark of a great business")
        elif x >= .12: c.add(1, f"ROE {pct(x)} — 괜찮음", f"ROE {pct(x)} — decent")
        elif x < .05: c.add(-1, f"ROE {pct(x)} — 훌륭한 기업이 아님", f"ROE {pct(x)} — not a great business")
        else: c.add(0, f"ROE {pct(x)} — 평범", f"ROE {pct(x)} — ordinary")
    if (x := f["gm"]) is not None:
        if x >= .50: c.add(2, f"매출총이익률 {pct(x)} — 경쟁자가 못 따라오는 사업", f"Gross margin {pct(x)} — competitors can't match it")
        elif x >= .35: c.add(1, f"매출총이익률 {pct(x)} — 준수", f"Gross margin {pct(x)} — solid")
        elif x < .20: c.add(-1, f"매출총이익률 {pct(x)} — 가격 경쟁에 시달리는 사업", f"Gross margin {pct(x)} — stuck in a price war")
        else: c.add(0, f"매출총이익률 {pct(x)} — 평범", f"Gross margin {pct(x)} — ordinary")
    if (x := f["pm"]) is not None:
        if x >= .20: c.add(1, f"순이익률 {pct(x)}", f"Net margin {pct(x)}")
        elif x < 0: c.add(-2, f"순이익률 {pct(x)} — 돈을 잃는 회사는 보지 않는다", f"Net margin {pct(x)} — I don't look at companies that lose money")
    if (x := f["rg"]) is not None:
        if x >= .10: c.add(1, f"매출 {x * 100:+.0f}% — 계속 크는 중", f"Revenue {x * 100:+.0f}% — still growing")
        elif x < 0: c.add(-1, f"매출 {x * 100:+.0f}% — 줄어드는 사업", f"Revenue {x * 100:+.0f}% — a shrinking business")
    # 뒤집어 보기: 무엇이 이 회사를 죽일 수 있나
    if (x := f["de"]) is not None and x > 200 and f["sector"] != "Financial Services":
        c.add(-2, f"부채비율 {x:.0f}% — 망하는 길 1번은 빚", f"Debt/equity {x:.0f}% — debt is the first way to go broke")
    if (x := f["cr"]) is not None and x < 1:
        c.add(-1, f"유동비율 {x:.1f} — 단기 빚이 현금보다 많음", f"Current ratio {x:.1f} — short-term debts exceed liquid assets")
    if (x := f["fcf"]) is not None and x < 0:
        c.add(-1, "현금이 계속 빠져나감 — 남의 돈에 기대야 하는 사업", "Burning cash — depends on other people's money")
    if (x := eff_pe(f)) is not None:
        if x <= 30: c.add(1, f"PER {x:.0f}배 — 공정한 가격", f"P/E {x:.0f} — a fair price")
        elif x > 60: c.add(-2, f"PER {x:.0f}배 — 아무리 좋아도 이 값은 아님", f"P/E {x:.0f} — no business is worth this much")
        elif x > 40: c.add(-1, f"PER {x:.0f}배 — 비쌈", f"P/E {x:.0f} — expensive")
    return c.verdict(5, 0)


def graham(f) -> dict:
    c = Card()
    if (x := f["pe"]) is not None:
        if x <= 15: c.add(2, f"PER {x:.0f}배 — 기준(15배) 이내", f"P/E {x:.0f} — within my limit of 15")
        elif x <= 20: c.add(1, f"PER {x:.0f}배 — 기준을 조금 넘음", f"P/E {x:.0f} — slightly over my limit")
        elif x > 50: c.add(-2, f"PER {x:.0f}배 — 투기 영역", f"P/E {x:.0f} — speculative territory")
        elif x > 30: c.add(-1, f"PER {x:.0f}배 — 비쌈", f"P/E {x:.0f} — expensive")
        else: c.add(0, f"PER {x:.0f}배 — 기준(15배) 초과", f"P/E {x:.0f} — over my limit of 15")
    elif f["eps"] is not None and f["eps"] <= 0:
        c.add(-2, "최근 1년 적자 — 이익이 없으면 가치를 잴 수 없음", "Loss over the last year — no earnings, no way to value it")
    if (x := f["pb"]) is not None and x > 0:
        if x <= 1.5: c.add(2, f"PBR {x:.1f}배 — 자산 가치에 가까움", f"P/B {x:.1f} — close to asset value")
        elif x <= 3: c.add(1, f"PBR {x:.1f}배", f"P/B {x:.1f}")
        elif x > 15: c.add(-2, f"PBR {x:.0f}배 — 장부 가치의 {x:.0f}배를 냄", f"P/B {x:.0f} — paying {x:.0f}x book value")
        elif x > 8: c.add(-1, f"PBR {x:.0f}배 — 자산에 비해 비쌈", f"P/B {x:.0f} — expensive against assets")
        else: c.add(0, f"PBR {x:.1f}배 — 기준(1.5배) 초과", f"P/B {x:.1f} — over my limit of 1.5")
    if f["eps"] and f["eps"] > 0 and f["bv"] and f["bv"] > 0 and f["price"]:
        gn = math.sqrt(22.5 * f["eps"] * f["bv"])  # 그레이엄 수
        gap = f["price"] / gn
        if gap <= 1: c.add(2, f"적정가(그레이엄 수) ${gn:,.0f} 아래 — 안전마진 {pct(1 - gap)}", f"Below the Graham number ${gn:,.0f} — {pct(1 - gap)} margin of safety")
        elif gap > 4: c.add(-2, f"적정가(그레이엄 수) ${gn:,.0f}의 {gap:.1f}배 — 안전마진 없음", f"{gap:.1f}x the Graham number ${gn:,.0f} — no margin of safety")
        elif gap > 2: c.add(-1, f"적정가(그레이엄 수) ${gn:,.0f}의 {gap:.1f}배", f"{gap:.1f}x the Graham number ${gn:,.0f}")
        else: c.add(0, f"적정가(그레이엄 수) ${gn:,.0f}보다 {pct(gap - 1)} 비쌈", f"{pct(gap - 1)} above the Graham number ${gn:,.0f}")
    if (x := f["cr"]) is not None:
        if x >= 2: c.add(1, f"유동비율 {x:.1f} — 재무가 튼튼함", f"Current ratio {x:.1f} — a strong balance sheet")
        elif x < 1: c.add(-1, f"유동비율 {x:.1f} — 단기 지급 능력 부족", f"Current ratio {x:.1f} — weak short-term liquidity")
    if (x := f["de"]) is not None and f["sector"] != "Financial Services":
        if x < 50: c.add(1, f"부채비율 {x:.0f}% — 보수적", f"Debt/equity {x:.0f}% — conservative")
        elif x > 150: c.add(-1, f"부채비율 {x:.0f}% — 과함", f"Debt/equity {x:.0f}% — excessive")
    if f["div"]:
        c.add(1, f"배당 {f['div']:.1f}% 지급", f"Pays a {f['div']:.1f}% dividend")
    return c.verdict(5, 0, need=2)


def lynch(f) -> dict:
    c = Card()
    eg = f["eg"]
    if f["eps"] is not None and f["eps"] < 0:
        turn = bool(f["feps"] and f["feps"] > 0)
        c.add(0 if turn else -1, f"분류: {'턴어라운드 후보' if turn else '적자 기업'} — 흑자 전환을 숫자로 확인하고 산다",
              f"Type: {'turnaround candidate' if turn else 'loss-maker'} — wait until the numbers show the turn")
    elif eg is not None:
        ko, en = ("고성장주", "fast grower") if eg >= .20 else ("대형 우량주", "stalwart") if eg >= .10 else ("저성장주", "slow grower")
        c.add(0, f"분류: {ko}", f"Type: {en}")
    if (x := f["peg"]) is not None and x > 0:
        if x <= 1: c.add(3, f"PEG {x:.1f} — 성장에 비해 쌈 (1 이하면 매력)", f"PEG {x:.1f} — cheap for its growth (under 1 is attractive)")
        elif x <= 1.5: c.add(2, f"PEG {x:.1f} — 성장 대비 합리적", f"PEG {x:.1f} — reasonable for its growth")
        elif x > 3: c.add(-2, f"PEG {x:.1f} — 성장을 한참 앞서간 가격", f"PEG {x:.1f} — the price is far ahead of the growth")
        elif x > 2: c.add(-1, f"PEG {x:.1f} — 성장 대비 비쌈", f"PEG {x:.1f} — expensive for its growth")
        else: c.add(0, f"PEG {x:.1f} — 제값", f"PEG {x:.1f} — fully priced")
    if eg is not None:
        if eg >= .25: c.add(2, f"이익 {eg * 100:+.0f}% — 빠르게 크는 중", f"Earnings {eg * 100:+.0f}% — growing fast")
        elif eg >= .10: c.add(1, f"이익 {eg * 100:+.0f}%", f"Earnings {eg * 100:+.0f}%")
        elif eg < 0: c.add(-1, f"이익 {eg * 100:+.0f}% — 줄고 있음", f"Earnings {eg * 100:+.0f}% — shrinking")
        else: c.add(0, f"이익 {eg * 100:+.0f}% — 느린 성장", f"Earnings {eg * 100:+.0f}% — slow growth")
    if (x := f["rg"]) is not None:
        if x >= .15: c.add(1, f"매출 {x * 100:+.0f}% — 이야기가 숫자로 확인됨", f"Revenue {x * 100:+.0f}% — the story shows up in the numbers")
        elif x < 0: c.add(-1, f"매출 {x * 100:+.0f}% — 줄고 있음", f"Revenue {x * 100:+.0f}% — shrinking")
    if (x := f["de"]) is not None and f["sector"] != "Financial Services":
        if x < 80: c.add(1, f"부채비율 {x:.0f}% — 빚 걱정 없음", f"Debt/equity {x:.0f}% — no debt worries")
        elif x > 200: c.add(-1, f"부채비율 {x:.0f}% — 빚 많은 회사는 한 번 삐끗하면 끝", f"Debt/equity {x:.0f}% — one stumble can finish an indebted company")
    return c.verdict(4, 0)


def druckenmiller(f) -> dict:
    c, p = Card(), f["price"]
    if p and f["ma200"]:
        x = p / f["ma200"] - 1
        up = x > 0
        c.add(2 if up else -2, f"200일선 {'위' if up else '아래'} {abs(x) * 100:.0f}% — 큰 추세가 {'살아 있음' if up else '꺾임'}",
              f"{abs(x) * 100:.0f}% {'above' if up else 'below'} the 200-day average — the major trend is {'intact' if up else 'broken'}")
    if p and f["ma50"]:
        x = p / f["ma50"] - 1
        up = x > 0
        c.add(1 if up else -1, f"50일선 {'위' if up else '아래'} {abs(x) * 100:.0f}%", f"{abs(x) * 100:.0f}% {'above' if up else 'below'} the 50-day average")
    if (x := f["r6"]) is not None:
        if x >= .20: c.add(1, f"6개월 {x * 100:+.0f}% — 돈이 몰리는 종목", f"{x * 100:+.0f}% in 6 months — money is flowing in")
        elif x <= -.20: c.add(-1, f"6개월 {x * 100:+.0f}% — 돈이 빠지는 종목", f"{x * 100:+.0f}% in 6 months — money is flowing out")
        else: c.add(0, f"6개월 {x * 100:+.0f}%", f"{x * 100:+.0f}% in 6 months")
    if (x := f["rs3"]) is not None:
        if x >= .05: c.add(1, f"3개월 S&P500보다 {x * 100:+.0f}%p — 시장을 이김", f"{x * 100:+.0f} pts vs the S&P 500 over 3 months — beating the market")
        elif x <= -.05: c.add(-1, f"3개월 S&P500보다 {x * 100:+.0f}%p — 시장에 짐", f"{x * 100:+.0f} pts vs the S&P 500 over 3 months — lagging the market")
    if (x := f["off_hi"]) is not None:
        if x >= -.10: c.add(1, f"52주 고점에서 {x * 100:.0f}% — 고점 근처", f"{x * 100:.0f}% from the 52-week high — near the highs")
        elif x <= -.35: c.add(-1, f"52주 고점에서 {x * 100:.0f}% — 떨어지는 칼", f"{x * 100:.0f}% from the 52-week high — a falling knife")
    if (x := f["eg"]) is not None:
        if x >= .25: c.add(1, f"이익 {x * 100:+.0f}% — 실적이 주가를 밀어 줌", f"Earnings {x * 100:+.0f}% — results are driving the stock")
        elif x < 0: c.add(-1, f"이익 {x * 100:+.0f}% — 실적이 꺾임", f"Earnings {x * 100:+.0f}% — results have rolled over")
    if f["spy_up"] is False:
        c.add(-1, "S&P500이 200일선 아래 — 시장 전체가 약세, 판을 줄일 때", "S&P 500 below its 200-day average — weak market, time to cut size")
    return c.verdict(4, -2, need=2)


def judge(f: dict) -> dict:
    if f["kind"] == "CRYPTOCURRENCY":
        v = dict(buffett=fixed("팔자", "아무것도 만들어 내지 않는 자산 — 다음 사람이 더 비싸게 사 주길 바랄 뿐", "An asset that produces nothing — you just hope the next buyer pays more"),
                 munger=fixed("팔자", "가치를 따질 근거가 없는 자산은 쳐다보지 않는다", "I don't look at assets with nothing to value"),
                 graham=fixed("팔자", "내재가치를 계산할 수 없음 — 투자가 아니라 투기", "No intrinsic value to compute — speculation, not investment"),
                 lynch=fixed("보류", "이익도 성장률도 없어 내 방식으로는 잴 수 없음", "No earnings or growth rate — my method can't measure it"))
    elif f["kind"] != "EQUITY" or f["price"] is None:
        ko, en = ("가격 데이터를 못 받음", "No price data") if f["price"] is None else \
            ("개별 기업이 아니라 재무 기준을 적용할 수 없음", "Not a single company, so the financial tests don't apply")
        v = {k: fixed("보류", ko, en) for k in ("buffett", "munger", "graham", "lynch")}
    else:
        v = dict(buffett=buffett(f), munger=munger(f), graham=graham(f), lynch=lynch(f))
    v["druck"] = druckenmiller(f)
    buys = sum(x["v"] == "사자" for x in v.values())
    sells = sum(x["v"] == "팔자" for x in v.values())
    cons = "사자 우세" if buys >= 3 else "팔자 우세" if sells >= 3 else "엇갈림"
    return dict(sym=f["sym"], name=f["name"], sector=f["sector"], price=f["price"], chg=f["chg"],
                buys=buys, sells=sells, cons=cons, v=v,
                m={k: f[k] for k in ("roe", "om", "gm", "de", "pe", "fpe", "pb", "peg", "eg", "rg", "r6", "off_hi", "fcfy")})


# ───────────────────────── 상태 ─────────────────────────
LOCK = threading.Lock()
STATE = dict(rows=[], updated=0.0, loading=False, spy_up=None, error="")


def build(syms: list[str]) -> tuple[list[dict], bool | None]:
    with ThreadPoolExecutor(8) as ex:
        infos = dict(zip(syms, ex.map(fetch_info, syms)))
    prices = fetch_prices(syms)
    spy = prices.get("SPY", [])
    rows = [judge(facts(s, infos[s], prices.get(s, []), spy)) for s in syms]
    return rows, (spy[-1] > sma(spy, 200) if len(spy) >= 200 else None)


def refresh():
    with LOCK:
        if STATE["loading"]:
            return
        STATE["loading"] = True
    try:
        wl = watchlist()
        syms = wl + [s for s in load_extra() if s not in wl]
        rows, spy_up = build(syms)
        if not any(r["price"] for r in rows):
            raise RuntimeError("Yahoo에서 데이터를 받지 못했습니다")
        with LOCK:
            STATE.update(rows=rows, updated=time.time(), spy_up=spy_up, error="")
        JOURNAL.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(dict(rows=rows, updated=STATE["updated"], spy_up=spy_up), ensure_ascii=False))
    except Exception as e:
        with LOCK:
            STATE["error"] = str(e)
    finally:
        with LOCK:
            STATE["loading"] = False


def keeper():
    """시작할 때 저장본을 먼저 보여 주고, 6시간이 지났으면 새로 받음."""
    try:
        c = json.loads(CACHE.read_text())
        with LOCK:
            STATE.update(rows=c["rows"], updated=c["updated"], spy_up=c.get("spy_up"))
    except Exception:
        pass
    while True:
        if time.time() - STATE["updated"] > TTL:
            refresh()
        time.sleep(300)


def add_symbol(sym: str) -> dict:
    """직접 입력한 티커 — 바로 판정해서 목록에 넣고 저장."""
    if not SYM_RE.match(sym):
        return dict(error="티커 형식이 아닙니다 (예: AAPL, BRK-B)")
    with LOCK:
        if any(r["sym"] == sym for r in STATE["rows"]):
            return dict(ok=True, sym=sym)
    rows, _ = build([sym])
    if rows[0]["price"] is None:
        return dict(error=f"{sym} — Yahoo에서 찾지 못했습니다")
    extra = load_extra()
    if sym not in extra and sym not in watchlist():
        save_extra(extra + [sym])
    rows[0]["extra"] = True
    with LOCK:
        STATE["rows"] = [r for r in STATE["rows"] if r["sym"] != sym] + rows
    return dict(ok=True, sym=sym)


def remove_symbol(sym: str) -> dict:
    save_extra([s for s in load_extra() if s != sym])
    with LOCK:
        STATE["rows"] = [r for r in STATE["rows"] if not (r["sym"] == sym and sym not in watchlist())]
    return dict(ok=True)


def snapshot() -> dict:
    extra = set(load_extra())
    with LOCK:
        rows = [dict(r, extra=r["sym"] in extra) for r in STATE["rows"]]
        return dict(rows=rows, updated=STATE["updated"], loading=STATE["loading"], spy_up=STATE["spy_up"],
                    error=STATE["error"], legends=LEGENDS)


# ───────────────────────── 서버 ─────────────────────────
def notebook(page: str) -> str:
    """공책 테마(apps/notebook.css + 손글씨 글꼴)를 <head> 끝에 끼워 넣음 — 허브·가상 트레이더와 같은 파일."""
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
        self._send(200, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            self._send(200, notebook(PAGE.read_text()).encode(), "text/html; charset=utf-8")
        elif u.path == "/api/data":
            self._json(snapshot())
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        u = urlparse(self.path)
        sym = parse_qs(u.query).get("sym", [""])[0].strip().upper()
        if u.path == "/api/refresh":
            threading.Thread(target=refresh, daemon=True).start()
            self._json(dict(ok=True))
        elif u.path == "/api/add":
            self._json(add_symbol(sym))
        elif u.path == "/api/remove":
            self._json(remove_symbol(sym))
        else:
            self._send(404, b"not found", "text/plain")


def export(out: Path) -> int:
    """포트폴리오 사이트용 — 관심종목 판정을 out/legends.json 으로 저장 (GitHub Actions 의 legends.yml 이 3시간마다 실행)."""
    rows, spy_up = build(watchlist())
    if sum(r["price"] is not None for r in rows) < len(rows) // 2:
        print("too few tickers came back from Yahoo; keeping the previous file", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    (out / "legends.json").write_text(json.dumps(dict(updated=time.time(), spy_up=spy_up, legends=LEGENDS, rows=rows),
                                                 ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {out / 'legends.json'} ({len(rows)} tickers)")
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
