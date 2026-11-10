"""월가 전설 5인 — 버핏·멍거·그레이엄·린치·드러켄밀러의 알려진 기준을 규칙으로 옮겨, 종목마다 사자/중립/팔자와 이유를 보여 줌.

    .venv/bin/python apps/legends.py [--open]      → http://localhost:8504

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
pct = lambda x: f"{x * 100:.0f}%"
LEGENDS = [
    dict(key="buffett", name="버핏", full="워런 버핏", motto="좋은 사업을 적당한 가격에, 오래"),
    dict(key="munger", name="멍거", full="찰리 멍거", motto="훌륭한 기업만. 망하는 길부터 피한다"),
    dict(key="graham", name="그레이엄", full="벤저민 그레이엄", motto="싸게 사서 안전마진을 확보한다"),
    dict(key="lynch", name="린치", full="피터 린치", motto="성장에 비해 싼 주식 (PEG)"),
    dict(key="druck", name="드러켄밀러", full="스탠리 드러켄밀러", motto="추세와 유동성을 따라 크게, 틀리면 바로 손절"),
]


class Card:
    """한 사람의 채점표 — add(점수, 이유)로 쌓고 verdict()로 판정."""

    def __init__(self):
        self.items = []

    def add(self, pts: int, text: str):
        self.items.append((pts, text))

    def verdict(self, buy: int, sell: int, need: int = 3) -> dict:
        score = sum(p for p, _ in self.items)
        if len(self.items) < need:
            v, line = "보류", "판단할 숫자가 부족함"
        else:
            v = "사자" if score >= buy else "팔자" if score <= sell else "중립"
            side = [i for i in self.items if (i[0] > 0) == (v != "팔자") and i[0]] or self.items
            line = max(side, key=lambda i: abs(i[0]))[1]
        return dict(v=v, score=score, line=line, why=[dict(p=p, t=t) for p, t in self.items])


def fixed(v: str, line: str) -> dict:
    return dict(v=v, score=0, line=line, why=[dict(p=-1 if v == "팔자" else 0, t=line)])


def eff_pe(f):
    return f["fpe"] or f["pe"]


def buffett(f) -> dict:
    c = Card()
    if (x := f["roe"]) is not None:
        if x >= .20: c.add(2, f"ROE {pct(x)} — 주주 돈을 굴리는 솜씨가 뛰어남")
        elif x >= .15: c.add(1, f"ROE {pct(x)} — 괜찮은 자본 수익률")
        elif x < 0: c.add(-2, f"ROE {pct(x)} — 자본을 까먹는 중")
        elif x < .08: c.add(-1, f"ROE {pct(x)} — 은행 이자보다 조금 나은 수준")
        else: c.add(0, f"ROE {pct(x)} — 평범")
    if (x := f["om"]) is not None:
        if x >= .25: c.add(2, f"영업이익률 {pct(x)} — 가격 결정력, 해자가 보임")
        elif x >= .15: c.add(1, f"영업이익률 {pct(x)} — 준수")
        elif x < 0: c.add(-2, f"영업이익률 {pct(x)} — 본업에서 돈을 못 범")
        elif x < .05: c.add(-1, f"영업이익률 {pct(x)} — 해자가 얕음")
        else: c.add(0, f"영업이익률 {pct(x)} — 평범")
    if (x := f["de"]) is not None and f["sector"] != "Financial Services":
        if x < 50: c.add(1, f"부채비율 {x:.0f}% — 빚이 적어 불황에도 버팀")
        elif x > 300: c.add(-2, f"부채비율 {x:.0f}% — 빚이 너무 많음")
        elif x > 150: c.add(-1, f"부채비율 {x:.0f}% — 빚 부담")
        else: c.add(0, f"부채비율 {x:.0f}% — 무난")
    if (x := f["fcf"]) is not None:
        if x <= 0: c.add(-1, "잉여현금흐름이 마이너스 — 주주에게 돌아올 현금이 없음")
        elif (y := f["fcfy"]) is not None and y >= .04: c.add(2, f"잉여현금 수익률 {y * 100:.1f}% — 현금을 넉넉히 벌어 줌")
        else: c.add(1, "잉여현금흐름 플러스")
    if (x := eff_pe(f)) is not None:
        if x <= 15: c.add(2, f"PER {x:.0f}배 — 가격이 적당함")
        elif x <= 25: c.add(1, f"PER {x:.0f}배 — 좋은 사업이라면 낼 만한 값")
        elif x > 40: c.add(-2, f"PER {x:.0f}배 — 너무 비쌈, 기다린다")
        elif x > 30: c.add(-1, f"PER {x:.0f}배 — 비싼 편")
        else: c.add(0, f"PER {x:.0f}배 — 싸지는 않음")
    if (x := f["eps"]) is not None and x < 0:
        c.add(-1 if f["fpe"] else -2, "최근 1년 적자 — 이익을 예측하기 어려운 사업")
    return c.verdict(5, 0)


def munger(f) -> dict:
    c = Card()
    if (x := f["roe"]) is not None:
        if x >= .20: c.add(2, f"ROE {pct(x)} — 훌륭한 기업의 조건")
        elif x >= .12: c.add(1, f"ROE {pct(x)} — 괜찮음")
        elif x < .05: c.add(-1, f"ROE {pct(x)} — 훌륭한 기업이 아님")
        else: c.add(0, f"ROE {pct(x)} — 평범")
    if (x := f["gm"]) is not None:
        if x >= .50: c.add(2, f"매출총이익률 {pct(x)} — 경쟁자가 못 따라오는 사업")
        elif x >= .35: c.add(1, f"매출총이익률 {pct(x)} — 준수")
        elif x < .20: c.add(-1, f"매출총이익률 {pct(x)} — 가격 경쟁에 시달리는 사업")
        else: c.add(0, f"매출총이익률 {pct(x)} — 평범")
    if (x := f["pm"]) is not None:
        if x >= .20: c.add(1, f"순이익률 {pct(x)}")
        elif x < 0: c.add(-2, f"순이익률 {pct(x)} — 돈을 잃는 회사는 보지 않는다")
    if (x := f["rg"]) is not None:
        if x >= .10: c.add(1, f"매출 {x * 100:+.0f}% — 계속 크는 중")
        elif x < 0: c.add(-1, f"매출 {x * 100:+.0f}% — 줄어드는 사업")
    # 뒤집어 보기: 무엇이 이 회사를 죽일 수 있나
    if (x := f["de"]) is not None and x > 200 and f["sector"] != "Financial Services":
        c.add(-2, f"부채비율 {x:.0f}% — 망하는 길 1번은 빚")
    if (x := f["cr"]) is not None and x < 1:
        c.add(-1, f"유동비율 {x:.1f} — 단기 빚이 현금보다 많음")
    if (x := f["fcf"]) is not None and x < 0:
        c.add(-1, "현금이 계속 빠져나감 — 남의 돈에 기대야 하는 사업")
    if (x := eff_pe(f)) is not None:
        if x <= 30: c.add(1, f"PER {x:.0f}배 — 공정한 가격")
        elif x > 60: c.add(-2, f"PER {x:.0f}배 — 아무리 좋아도 이 값은 아님")
        elif x > 40: c.add(-1, f"PER {x:.0f}배 — 비쌈")
    return c.verdict(5, 0)


def graham(f) -> dict:
    c = Card()
    if (x := f["pe"]) is not None:
        if x <= 15: c.add(2, f"PER {x:.0f}배 — 기준(15배) 이내")
        elif x <= 20: c.add(1, f"PER {x:.0f}배 — 기준을 조금 넘음")
        elif x > 50: c.add(-2, f"PER {x:.0f}배 — 투기 영역")
        elif x > 30: c.add(-1, f"PER {x:.0f}배 — 비쌈")
        else: c.add(0, f"PER {x:.0f}배 — 기준(15배) 초과")
    elif f["eps"] is not None and f["eps"] <= 0:
        c.add(-2, "최근 1년 적자 — 이익이 없으면 가치를 잴 수 없음")
    if (x := f["pb"]) is not None and x > 0:
        if x <= 1.5: c.add(2, f"PBR {x:.1f}배 — 자산 가치에 가까움")
        elif x <= 3: c.add(1, f"PBR {x:.1f}배")
        elif x > 15: c.add(-2, f"PBR {x:.0f}배 — 장부 가치의 {x:.0f}배를 냄")
        elif x > 8: c.add(-1, f"PBR {x:.0f}배 — 자산에 비해 비쌈")
        else: c.add(0, f"PBR {x:.1f}배 — 기준(1.5배) 초과")
    if f["eps"] and f["eps"] > 0 and f["bv"] and f["bv"] > 0 and f["price"]:
        gn = math.sqrt(22.5 * f["eps"] * f["bv"])  # 그레이엄 수
        gap = f["price"] / gn
        if gap <= 1: c.add(2, f"적정가(그레이엄 수) ${gn:,.0f} 아래 — 안전마진 {pct(1 - gap)}")
        elif gap > 4: c.add(-2, f"적정가(그레이엄 수) ${gn:,.0f}의 {gap:.1f}배 — 안전마진 없음")
        elif gap > 2: c.add(-1, f"적정가(그레이엄 수) ${gn:,.0f}의 {gap:.1f}배")
        else: c.add(0, f"적정가(그레이엄 수) ${gn:,.0f}보다 {pct(gap - 1)} 비쌈")
    if (x := f["cr"]) is not None:
        if x >= 2: c.add(1, f"유동비율 {x:.1f} — 재무가 튼튼함")
        elif x < 1: c.add(-1, f"유동비율 {x:.1f} — 단기 지급 능력 부족")
    if (x := f["de"]) is not None and f["sector"] != "Financial Services":
        if x < 50: c.add(1, f"부채비율 {x:.0f}% — 보수적")
        elif x > 150: c.add(-1, f"부채비율 {x:.0f}% — 과함")
    if f["div"]:
        c.add(1, f"배당 {f['div']:.1f}% 지급")
    return c.verdict(5, 0, need=2)


def lynch(f) -> dict:
    c = Card()
    eg = f["eg"]
    if f["eps"] is not None and f["eps"] < 0:
        kind = "턴어라운드 후보" if f["feps"] and f["feps"] > 0 else "적자 기업"
        c.add(0 if f["feps"] and f["feps"] > 0 else -1, f"분류: {kind} — 흑자 전환을 숫자로 확인하고 산다")
    elif eg is not None:
        kind = "고성장주" if eg >= .20 else "대형 우량주" if eg >= .10 else "저성장주"
        c.add(0, f"분류: {kind}")
    if (x := f["peg"]) is not None and x > 0:
        if x <= 1: c.add(3, f"PEG {x:.1f} — 성장에 비해 쌈 (1 이하면 매력)")
        elif x <= 1.5: c.add(2, f"PEG {x:.1f} — 성장 대비 합리적")
        elif x > 3: c.add(-2, f"PEG {x:.1f} — 성장을 한참 앞서간 가격")
        elif x > 2: c.add(-1, f"PEG {x:.1f} — 성장 대비 비쌈")
        else: c.add(0, f"PEG {x:.1f} — 제값")
    if eg is not None:
        if eg >= .25: c.add(2, f"이익 {eg * 100:+.0f}% — 빠르게 크는 중")
        elif eg >= .10: c.add(1, f"이익 {eg * 100:+.0f}%")
        elif eg < 0: c.add(-1, f"이익 {eg * 100:+.0f}% — 줄고 있음")
        else: c.add(0, f"이익 {eg * 100:+.0f}% — 느린 성장")
    if (x := f["rg"]) is not None:
        if x >= .15: c.add(1, f"매출 {x * 100:+.0f}% — 이야기가 숫자로 확인됨")
        elif x < 0: c.add(-1, f"매출 {x * 100:+.0f}% — 줄고 있음")
    if (x := f["de"]) is not None and f["sector"] != "Financial Services":
        if x < 80: c.add(1, f"부채비율 {x:.0f}% — 빚 걱정 없음")
        elif x > 200: c.add(-1, f"부채비율 {x:.0f}% — 빚 많은 회사는 한 번 삐끗하면 끝")
    return c.verdict(4, 0)


def druckenmiller(f) -> dict:
    c, p = Card(), f["price"]
    if p and f["ma200"]:
        x = p / f["ma200"] - 1
        c.add(2 if x > 0 else -2, f"200일선 {'위' if x > 0 else '아래'} {abs(x) * 100:.0f}% — 큰 추세가 {'살아 있음' if x > 0 else '꺾임'}")
    if p and f["ma50"]:
        x = p / f["ma50"] - 1
        c.add(1 if x > 0 else -1, f"50일선 {'위' if x > 0 else '아래'} {abs(x) * 100:.0f}%")
    if (x := f["r6"]) is not None:
        if x >= .20: c.add(1, f"6개월 {x * 100:+.0f}% — 돈이 몰리는 종목")
        elif x <= -.20: c.add(-1, f"6개월 {x * 100:+.0f}% — 돈이 빠지는 종목")
        else: c.add(0, f"6개월 {x * 100:+.0f}%")
    if (x := f["rs3"]) is not None:
        if x >= .05: c.add(1, f"3개월 S&P500보다 {x * 100:+.0f}%p — 시장을 이김")
        elif x <= -.05: c.add(-1, f"3개월 S&P500보다 {x * 100:+.0f}%p — 시장에 짐")
    if (x := f["off_hi"]) is not None:
        if x >= -.10: c.add(1, f"52주 고점에서 {x * 100:.0f}% — 고점 근처")
        elif x <= -.35: c.add(-1, f"52주 고점에서 {x * 100:.0f}% — 떨어지는 칼")
    if (x := f["eg"]) is not None:
        if x >= .25: c.add(1, f"이익 {x * 100:+.0f}% — 실적이 주가를 밀어 줌")
        elif x < 0: c.add(-1, f"이익 {x * 100:+.0f}% — 실적이 꺾임")
    if f["spy_up"] is False:
        c.add(-1, "S&P500이 200일선 아래 — 시장 전체가 약세, 판을 줄일 때")
    return c.verdict(4, -2, need=2)


def judge(f: dict) -> dict:
    if f["kind"] == "CRYPTOCURRENCY":
        v = dict(buffett=fixed("팔자", "아무것도 만들어 내지 않는 자산 — 다음 사람이 더 비싸게 사 주길 바랄 뿐"),
                 munger=fixed("팔자", "가치를 따질 근거가 없는 자산은 쳐다보지 않는다"),
                 graham=fixed("팔자", "내재가치를 계산할 수 없음 — 투자가 아니라 투기"),
                 lynch=fixed("보류", "이익도 성장률도 없어 내 방식으로는 잴 수 없음"))
    elif f["kind"] != "EQUITY" or f["price"] is None:
        why = "가격 데이터를 못 받음" if f["price"] is None else "개별 기업이 아니라 재무 기준을 적용할 수 없음"
        v = {k: fixed("보류", why) for k in ("buffett", "munger", "graham", "lynch")}
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


def main():
    threading.Thread(target=keeper, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    if "--open" in sys.argv:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
