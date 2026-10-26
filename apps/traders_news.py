"""가상 트레이더용 뉴스 — Alpaca(Benzinga) 실시간 뉴스를 받아 규칙·테마 지도로 '어떤 종목에 호재/악재인지' 해석.

해석 방식 (무료·항상 같은 결과):
  1) 직접: 기사에 태그된 종목 + 헤드라인 키워드(실적 상회·상향·계약 / 하회·하향·조사·유상증자 …)로 방향과 세기
  2) 테마: 기사에 태그가 없어도 키워드로 관련 종목 묶음에 파급
     예) Anthropic·OpenAI 상장·투자·컴퓨팅 계약 → ORCL·NVDA·CRWV·NBIS·IREN·MU … 호재
테마 지도(THEMES)는 직접 고쳐도 됨. 영향 점수는 -1(강한 악재) ~ +1(강한 호재), 시간이 지나면 줄어듦(반감기 45분).
"""
from __future__ import annotations

import datetime as dt
import math
import re
import time
from collections import deque

import requests

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
HALF_LIFE = 45  # 분
KEEP_MIN = 6 * 60  # 이보다 오래된 뉴스는 버림

AI_INFRA = {"NVDA": .7, "AVGO": .6, "AMD": .5, "ORCL": .7, "CRWV": .9, "NBIS": .8, "IREN": .6, "SMCI": .5, "DELL": .5,
            "MU": .5, "SKHY": .5, "TSM": .4, "ARM": .3, "BE": .4, "PENG": .3}
SEMI_EXPORT = {"NVDA": .8, "AMD": .7, "AMAT": .6, "LRCX": .6, "ASML": .5, "MU": .4, "TSM": .3, "ARM": .3, "AVGO": .3, "SKHY": .4}
MEMORY = {"MU": .8, "SKHY": .8, "SNDK": .7, "WDC": .5, "STX": .4}
SEMIS = {"NVDA": .5, "AMD": .5, "AVGO": .5, "QCOM": .4, "INTC": .4, "MU": .5, "TSM": .5, "ASML": .4, "AMAT": .5,
         "LRCX": .5, "MRVL": .5, "ARM": .4, "SKHY": .5}

R = lambda p: re.compile(p, re.I)
NEG_TURN = R(r"\b(delay|postpone|scrap|cancel|pause|cuts?|slash|slows?|withdraw|halt|lawsuit|probe|ban|blocks?|rejects?)")

# 테마: when(헤드라인+요약에 모두 맞아야 함) → targets(종목: 세기). sign은 기본 방향, flip에 걸리면 반대
THEMES = [
    dict(key="ai_lab", name="AI 연구소 상장·투자·컴퓨팅 계약",
         when=[R(r"\b(anthropic|openai|xai|x\.ai|mistral|safe superintelligence|thinking machines|perplexity)\b"),
               R(r"\b(ipo|initial public offering|files? for|funding|raises?|valuation|compute|gpus?|data cent(er|re)s?|"
                 r"gigawatts?|chips?|deal|agreement|contract|partnership|cluster|stargate|invest)")],
         targets=AI_INFRA, sign=1, flip=NEG_TURN,
         extra={R(r"anthropic"): {"AMZN": .7, "GOOGL": .7},
                R(r"openai"): {"MSFT": .6, "ORCL": 1.0, "AMD": .8, "AVGO": .7, "NVDA": .9},
                R(r"\bxai\b|x\.ai"): {"DELL": .7, "SMCI": .7, "NVDA": .8, "TSLA": .3}},
         why="AI 연구소에 돈이 몰리면 GPU·데이터센터·클라우드 수요로 이어짐"),
    dict(key="capex", name="빅테크 AI 설비투자",
         when=[R(r"\b(microsoft|meta|alphabet|google|amazon|aws|oracle|hyperscalers?|big tech)\b"),
               R(r"\b(capex|capital expenditures?|capital spending|ai spending|funding push|spend(ing)? on ai)"),
               R(r"(\b(rais|increas|boost|hik|record|higher|expand|lift|cut|lower|reduc|slash|trim)|\$\d)")],
         targets={**AI_INFRA, **MEMORY, "STX": .3, "WDC": .4}, sign=1, flip=R(r"\b(cut|lower|reduce|slash|trim|pause)"),
         why="하이퍼스케일러 설비투자 = 반도체·메모리·네오클라우드 매출"),
    dict(key="export", name="반도체 수출 규제",
         when=[R(r"\b(export (controls?|restrictions?|curbs?|bans?|rules?|licen[cs]es?)|entity list|chip (bans?|curbs?)|bis rule)")],
         targets=SEMI_EXPORT, sign=-1, flip=R(r"\b(ease|eases|eased|lift|lifts|relax|approves?|grants?|waive|allows?)\b"),
         why="중국 매출 비중 큰 반도체에 타격"),
    dict(key="memory", name="메모리 가격",
         when=[R(r"\b(dram|nand|hbm|memory (chip )?(prices?|pricing|shortage|supply|demand))")],
         targets=MEMORY, sign=1, flip=R(r"\b(fall|falls|decline|drop|glut|oversupply|lower|weak|slump)"),
         why="메모리 가격이 오르면 메모리 업체 이익 급증"),
    dict(key="chip_tariff", name="반도체 관세",
         when=[R(r"\btariffs?\b"), R(r"\b(semiconductors?|chips?|chipmakers?)\b")],
         targets=SEMIS, sign=-1, flip=R(r"\b(exempt|exemption|pause|delay|relief|lift|lifts|cut|cuts|lower)"),
         why="반도체 관세는 업종 전체에 부담"),
    dict(key="quantum", name="양자컴퓨팅",
         when=[R(r"\bquantum\b"), R(r"\b(breakthrough|record|contract|deal|milestone|error.correct|qubits?|award)")],
         targets={"IONQ": .6, "QNT": .4}, sign=1, flip=NEG_TURN, why="양자 테마 순환매"),
    dict(key="dc_power", name="데이터센터 전력",
         when=[R(r"\b(fuel cells?|data cent(er|re)s? power|power demand|electricity demand|grid (capacity|shortage))")],
         targets={"BE": .6}, sign=1, flip=NEG_TURN, why="AI 데이터센터 전력 부족 = 연료전지 수요"),
]
# 대장주 실적·전망이 좋으면/나쁘면 같은 업종이 따라 움직임
SYMPATHY = {"NVDA": {"AMD": .4, "AVGO": .4, "MU": .4, "TSM": .4, "SMCI": .4, "DELL": .3, "CRWV": .5, "NBIS": .4, "IREN": .3, "ARM": .3},
            "TSM": {"NVDA": .3, "AMD": .3, "ASML": .4, "AMAT": .4, "LRCX": .4},
            "MU": {"SKHY": .6, "SNDK": .5, "WDC": .3, "STX": .2},
            "ORCL": {"CRWV": .4, "NBIS": .4, "NVDA": .2},
            "ASML": {"AMAT": .5, "LRCX": .5}}

RATING_UP = r"upgrades? .{0,60}?\bto (strong )?(buy|overweight|outperform|positive|accumulate|add|sector outperform)|upgraded"
RATING_DN = r"downgrades? .{0,60}?\bto (sell|underweight|underperform|neutral|hold|equal.weight|market perform|reduce|sector perform)|downgraded"
POS = R(r"\b(beats?|tops|exceeds?|above estimates?|raises? (fy|full.year|guidance|outlook|forecast|price target)|" + RATING_UP + r"|"
        r"price target (raised|increased)|record (revenue|sales|quarter|profit|earnings|high|results|deliveries|orders)|wins?|awarded|secures?|partnership|partners with|collaborat\w*|agreement|"
        r"contract|approv\w*|expands?|buyback|repurchase|surges?|soars?|jumps?|rall(y|ies)|outperform|overweight|"
        r"buy rating|strong demand)\b")
NEG = R(r"\b(miss(es|ed)?|falls? short|below estimates?|lowers? (fy|guidance|outlook|forecast|price target)|cuts? (guidance|outlook|forecast|price target)|"
        + RATING_DN + r"|probe|investigat\w*|lawsuit|sues|sued|recall\w*|delays?|halts?|plunges?|sinks?|tumbles?|slumps?|crash(es)?|"
        r"(stock|share)s? (fall|drop|slide)|(?<!initial )(public|secondary|stock|share|follow-on) offering|dilut\w*|short seller|short report|bankrupt\w*|"
        r"layoffs?|warns?|underperform|underweight|sell rating|weak (demand|guidance))\b")
STRONG = R(r"\b(raises? (fy|full.year|guidance|outlook)|lowers? (guidance|outlook)|cuts? (guidance|outlook)|" + RATING_UP + "|" + RATING_DN + "|"
           r"(public|secondary) offering|bankrupt|investigat|probe|acquir|buyout|takeover|beats? .* raises?)")
WEAK = R(r"\b(maintains|reiterates|reaffirms)\b")  # 기존 의견 유지 — 약하게
MARKET_ETF = {"SPY", "QQQ", "IWM", "DIA"}  # 시장 전체는 시장 점수가 따로 봄
EARN = R(r"\b(q[1-4]|quarter|eps|earnings|revenue|sales|guidance|outlook)\b")


def classify(item: dict, universe: set | None) -> dict | None:
    """기사 하나 → {종목: 영향(-1~1)} + 이유. universe=None이면 태그된 모든 종목. 영향이 없으면 None."""
    inu = (lambda s: True) if universe is None else universe.__contains__
    head = item.get("headline", "")
    text = head  # 요약문까지 보면 엉뚱한 단어에 테마가 걸려서 헤드라인만
    tags = [s for s in item.get("symbols", []) if s and s not in MARKET_ETF]
    imp: dict[str, float] = {}
    why: dict[str, str] = {}
    themes = []

    p, n = len(POS.findall(head)), len(NEG.findall(head))
    tone = (p > n) - (n > p)
    if tone:
        w = (1.0 if STRONG.search(head) else 0.6) * (0.5 if len(tags) > 3 else 1.0)  # 여러 종목 묶음 기사는 약하게
        w *= 0.5 if WEAK.search(head) else 1.0
        for s in tags:
            if inu(s):
                imp[s] = imp.get(s, 0) + tone * w
                why[s] = "직접 " + ("호재" if tone > 0 else "악재")
        for lead, peers in SYMPATHY.items():
            if lead in tags and EARN.search(head):
                for s, v in peers.items():
                    if inu(s) and s not in tags:
                        imp[s] = imp.get(s, 0) + tone * v * w
                        why.setdefault(s, f"{lead} 실적 따라감")
    for th in THEMES:
        if not all(r.search(text) for r in th["when"]):
            continue
        sign = th["sign"] * (-1 if th["flip"].search(head) else 1)
        tg = dict(th["targets"])
        for rx, ex in th.get("extra", {}).items():
            if rx.search(text):
                tg.update(ex)
        hit = False
        for s, v in tg.items():
            if inu(s):
                imp[s] = imp.get(s, 0) + sign * v
                why.setdefault(s, th["name"])
                hit = True
        if hit:
            themes.append(dict(key=th["key"], name=th["name"], sign=sign, why=th["why"]))
    imp = {s: max(-1.0, min(1.0, v)) for s, v in imp.items() if abs(v) >= 0.2}
    if not imp:
        return None
    return dict(id=item["id"], t=item["created_at"], head=head, url=item.get("url", ""), src=item.get("source", ""),
                tags=tags, imp=imp, why=why, themes=themes)


class NewsBook:
    """최근 뉴스와 종목별 뉴스 점수 (시간이 지나면 줄어듦)."""

    def __init__(self, keys: tuple):
        self.keys = keys
        self.items: deque = deque(maxlen=3000)
        self.seen: set = set()
        self.last: str | None = None
        self.error = ""

    def fetch(self, universe: set | None = None, hours_back: float = 12) -> list[dict]:
        """새 기사만 받아 해석. 처음엔 hours_back 시간 전부터."""
        if not all(self.keys):
            self.error = "Alpaca 키 없음 — 뉴스 꺼짐"
            return []
        start = self.last or (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
        hdr = {"APCA-API-KEY-ID": self.keys[0], "APCA-API-SECRET-KEY": self.keys[1]}
        got, token = [], None
        try:
            for _ in range(40):
                q = {"start": start, "limit": 50, "sort": "desc", "include_content": "false"}
                if token:
                    q["page_token"] = token
                r = requests.get(NEWS_URL, params=q, headers=hdr, timeout=15)
                r.raise_for_status()
                js = r.json()
                got += js.get("news", [])
                token = js.get("next_page_token")
                if not token:
                    break
            self.error = ""
        except Exception as e:
            self.error = f"뉴스 받기 실패: {type(e).__name__}"
            return []
        new = []
        for it in sorted(got, key=lambda x: x["created_at"]):
            if it["id"] in self.seen:
                continue
            self.seen.add(it["id"])
            c = classify(it, universe)
            if c:
                self.items.append(c)
                new.append(c)
        if got:
            self.last = max(x["created_at"] for x in got)
        cut = time.time() - KEEP_MIN * 60
        while self.items and _ts(self.items[0]["t"]) < cut:
            self.items.popleft()
        return new

    def score(self, sym: str, now: float | None = None) -> tuple[float, dict | None]:
        """종목 뉴스 점수(-1~1, 시간 감쇠)와 가장 영향 큰 최근 기사."""
        now = now or time.time()
        tot, top, best = 0.0, None, 0.0
        for it in self.items:
            v = it["imp"].get(sym)
            if v is None:
                continue
            age = (now - _ts(it["t"])) / 60
            if age < -5:
                continue
            d = v * math.pow(0.5, max(age, 0) / HALF_LIFE)
            tot += d
            if abs(d) > best:
                best, top = abs(d), dict(it, age=age, v=v)
        return max(-1.0, min(1.0, tot)), top

    def recent(self, universe: set, n: int = 40) -> list[dict]:
        """관심 범위 종목에 영향이 있는 최근 기사 (영향은 관심 종목만 남김)."""
        out = []
        for it in reversed(self.items):
            imp = {s: v for s, v in it["imp"].items() if s in universe}
            if imp:
                out.append(dict(it, imp=imp))
                if len(out) >= n:
                    break
        return out


def _ts(iso: str) -> float:
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
