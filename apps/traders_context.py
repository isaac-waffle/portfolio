"""가상 트레이더용 '주변 정보' — 종목마다 애널리스트 의견·은행 목표가·실적·옵션·공매도·계절성을 하루 한 번 모아 둠.

  .venv/bin/python apps/traders_context.py NVDA MRNA     # 지금 값 확인

출처는 전부 Yahoo Finance(무료). 종목당 요청이 여러 번이라 느려서, 엔진과 다른 스레드에서 천천히 받아 journal/traders/context.json에 저장하고
트레이더는 저장된 값만 읽음 (없으면 '정보 없음'으로 넘어감 — 매매가 이것 때문에 멈추지 않음).

무엇이 매매에 쓰이고 무엇이 참고용인지는 analyst_lab.py의 검증 결과로 정함 (score 참고). 검증 안 된 항목은 화면과 트레이더 설명에만 나옴.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

TTL_H = 20  # 시간 — 이보다 오래된 값은 다시 받음
WINDOW = 60  # 일 — 최근 의견 변경을 세는 기간
SKIP = {"SPY", "QQQ", "IWM", "BIL", "EFA", "EEM", "TLT", "IEF", "SHY", "GLD", "DBC", "VNQ", "LQD"}  # ETF는 애널리스트 의견·실적이 없음


def _num(x):
    try:
        x = float(x)
        return x if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def analyst(tk: yf.Ticker, now: pd.Timestamp) -> dict:
    """최근 60일 등급 상향/하향, 목표가 상향/하향 건수와 가장 최근 변경."""
    ud = tk.upgrades_downgrades
    if ud is None or not len(ud):
        return {}
    ud = ud[~ud.index.isna()].sort_index(ascending=False)  # 최신이 맨 위
    idx = pd.DatetimeIndex(ud.index)  # Yahoo는 시각을 UTC로 줌 → 뉴욕 시각으로 (now와 같은 기준)
    idx = (idx.tz_localize("UTC") if idx.tz is None else idx).tz_convert("America/New_York").tz_localize(None)
    ud = ud[(idx >= now - pd.Timedelta(days=WINDOW)) & (idx <= now)]
    if not len(ud):
        return dict(up=0, down=0, pt_up=0, pt_down=0)
    cur, pri = pd.to_numeric(ud.get("currentPriceTarget"), errors="coerce"), pd.to_numeric(ud.get("priorPriceTarget"), errors="coerce")
    okp = (cur > 0) & (pri > 0)
    top = ud.iloc[0]
    return dict(up=int((ud["Action"] == "up").sum()), down=int((ud["Action"] == "down").sum()),
                pt_up=int((okp & (cur > pri)).sum()), pt_down=int((okp & (cur < pri)).sum()),
                last=dict(day=str(ud.index[0])[:10], firm=str(top["Firm"]), grade=str(top["ToGrade"]), action=str(top["Action"]),
                          pt=_num(top.get("currentPriceTarget")), pt_prev=_num(top.get("priorPriceTarget"))))


def consensus(tk: yf.Ticker) -> dict:
    """평균 투자의견(1 적극 매수 ~ 5 적극 매도), 애널리스트 수, 평균 목표가, 공매도 비율."""
    i = tk.info or {}
    px, tgt = _num(i.get("currentPrice") or i.get("regularMarketPrice")), _num(i.get("targetMeanPrice"))
    return dict(rec=_num(i.get("recommendationMean")), n=int(_num(i.get("numberOfAnalystOpinions")) or 0), target=tgt,
                upside=(tgt / px - 1) if px and tgt else None, short=_num(i.get("shortPercentOfFloat")), px=px)


def earnings(tk: yf.Ticker, now: pd.Timestamp) -> dict:
    """가장 최근 실적 서프라이즈(%)와 다음 발표일."""
    ed = tk.get_earnings_dates(limit=12)
    if ed is None or not len(ed):
        return {}
    idx = pd.DatetimeIndex(ed.index).tz_localize(None)
    past, nxt = ed[(idx <= now) & ed["Surprise(%)"].notna().to_numpy()], idx[idx > now]
    out = {}
    if len(past):
        out.update(surprise=_num(past["Surprise(%)"].iloc[0]), last=str(past.index[0])[:10])
    if len(nxt):
        out.update(next=str(nxt.min())[:10])
    return out


def options(tk: yf.Ticker, px: float | None, today: dt.date) -> dict:
    """7일 이상 남은 가장 가까운 만기: 풋/콜 거래량 비율, 등가격 내재변동성, 옵션이 가격에 반영한 만기까지 예상 변동폭(등가격 콜+풋 값 ÷ 주가)."""
    exps = [e for e in (tk.options or ()) if (dt.date.fromisoformat(e) - today).days >= 7]
    if not exps or not px:
        return {}
    ch = tk.option_chain(exps[0])
    c, p = ch.calls, ch.puts
    cv, pv = float(c["volume"].fillna(0).sum()), float(p["volume"].fillna(0).sum())
    out = dict(exp=exps[0], pc=(pv / cv) if cv > 0 else None)
    k = sorted(set(c["strike"]) & set(p["strike"]), key=lambda x: abs(x - px))
    if k:
        cc, pp = c[c["strike"] == k[0]].iloc[0], p[p["strike"] == k[0]].iloc[0]
        mid = lambda r: (r["bid"] + r["ask"]) / 2 if r["bid"] > 0 and r["ask"] > 0 else r["lastPrice"]
        out.update(iv=_num((cc["impliedVolatility"] + pp["impliedVolatility"]) / 2), move=_num((mid(cc) + mid(pp)) / px))
    return out


def seasonality(tk: yf.Ticker, today: dt.date) -> dict:
    """이 종목의 과거 같은 달 수익률 — 평균과 오른 해의 비율 (최대 15년)."""
    h = tk.history(period="15y", interval="1mo", auto_adjust=True)["Close"].dropna()
    r = h.pct_change().dropna()
    r = r[(r.index.month == today.month) & (r.index.year < today.year)]
    return dict(month=today.month, avg=float(r.mean()), win=float((r > 0).mean()), years=int(len(r))) if len(r) >= 3 else {}


def fetch_one(sym: str, now: dt.datetime) -> dict:
    """한 종목의 주변 정보. 항목 하나가 실패해도 나머지는 채움."""
    tk, ts = yf.Ticker(sym), pd.Timestamp(now.replace(tzinfo=None))
    out = dict(sym=sym, at=now.isoformat())
    cons = {}
    for key, fn in (("cons", lambda: consensus(tk)), ("an", lambda: analyst(tk, ts)), ("earn", lambda: earnings(tk, ts)),
                    ("opt", lambda: options(tk, cons.get("px"), now.date())), ("season", lambda: seasonality(tk, now.date()))):
        try:
            out[key] = fn()
            if key == "cons":
                cons = out[key]
        except Exception as e:  # Yahoo 한도·빈 응답 — 이 항목만 비움
            out[key] = {}
            out.setdefault("err", []).append(f"{key}: {type(e).__name__}")
        time.sleep(0.5)  # 가격을 받는 엔진과 같은 Yahoo 한도를 쓰므로 천천히
    return out


def earn_days(c: dict | None, today: dt.date) -> int | None:
    """다음 실적 발표까지 남은 날 (읽는 날 기준으로 계산 — 저장된 값이 하루 묵어도 맞게). 지났거나 모르면 None."""
    try:
        d = (dt.date.fromisoformat(((c or {}).get("earn") or {})["next"]) - today).days
        return d if d >= 0 else None
    except (KeyError, TypeError, ValueError):
        return None


REC_KO = ((1.5, "적극 매수"), (2.5, "매수"), (3.5, "중립"), (4.5, "매도"), (9, "적극 매도"))


def score(c: dict | None) -> float:
    """매매에 쓰는 점수 (−1 ~ +1) — analyst_lab.py에서 검증된 항목만 들어감. 검증된 게 없으면 0 (참고만)."""
    return 0.0


def line(c: dict | None, today: dt.date | None = None) -> str:
    """트레이더 설명과 화면에 붙는 한 줄. 저장된 값이 이상해도 예외를 내지 않음 (엔진·화면이 이것 때문에 멈추면 안 됨)."""
    try:
        return _line(c, today or dt.date.today())
    except Exception:
        return ""


def _line(c: dict | None, today: dt.date) -> str:
    if not c:
        return ""
    cons, an, earn, opt, sea = (c.get(k) or {} for k in ("cons", "an", "earn", "opt", "season"))
    out = []
    if cons.get("rec") and cons.get("n"):
        txt = f"의견 {next(k for lim, k in REC_KO if cons['rec'] <= lim)}({cons['rec']:.1f}, {cons['n']}곳)"
        if cons.get("upside") is not None:
            txt += f" 목표가 {cons['upside'] * 100:+.0f}%"
        out.append(txt)
    if any(an.get(k) for k in ("up", "down", "pt_up", "pt_down")):
        out.append(f"{WINDOW}일 등급 ↑{an.get('up', 0)}/↓{an.get('down', 0)} 목표가 ↑{an.get('pt_up', 0)}/↓{an.get('pt_down', 0)}")
    if earn.get("surprise") is not None:
        out.append(f"지난 실적 {earn['surprise']:+.0f}%")
    days = earn_days(c, today)
    if days is not None and days <= 14:
        out.append(f"실적 발표 {days}일 뒤")
    if opt.get("pc") is not None:
        out.append(f"풋/콜 {opt['pc']:.2f}" + (f" 예상 변동 ±{opt['move'] * 100:.0f}%" if opt.get("move") else ""))
    if cons.get("short"):
        out.append(f"공매도 {cons['short'] * 100:.0f}%")
    if sea.get("years"):
        out.append(f"{sea['month']}월 평균 {sea['avg'] * 100:+.0f}%({sea['win'] * 100:.0f}% 상승)")
    return " · ".join(out)


class ContextBook:
    """종목별 주변 정보를 파일에 두고, 오래된 것만 뒤에서 천천히 다시 받음."""

    def __init__(self, path: Path):
        self.path, self.lock = path, threading.Lock()
        self.busy, self.error = False, None
        try:
            raw = json.loads(path.read_text()) if path.exists() else {}
            self.data: dict = {k: v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            self.data = {}

    def get(self, sym: str) -> dict | None:
        with self.lock:
            return self.data.get(sym)

    def stale(self, syms: list, now: dt.datetime) -> list:
        """없거나 TTL이 지난 종목. 저장된 시각을 못 읽으면 오래된 것으로 봄."""
        def old(c) -> bool:
            try:
                return (now - dt.datetime.fromisoformat(c["at"]).replace(tzinfo=None)).total_seconds() > TTL_H * 3600
            except (KeyError, TypeError, ValueError):
                return True
        with self.lock:
            return [s for s in dict.fromkeys(syms) if s not in self.data or old(self.data[s])]

    def refresh(self, syms: list, now: dt.datetime, quiet: bool = True):
        """오래된 종목을 새 스레드에서 받음. 이미 받는 중이면 아무것도 안 함.
        quiet=False(장중·장 전후): 가격을 받는 엔진과 Yahoo 한도를 나눠 쓰므로, 아예 없는 종목만 받고 갱신은 장이 쉬는 시간으로 미룸."""
        todo = self.stale(syms, now)
        if not quiet:
            with self.lock:
                todo = [s for s in todo if s not in self.data]
        if self.busy or not todo:
            return
        self.busy = True
        try:
            threading.Thread(target=self._work, args=(todo, now), daemon=True).start()
        except Exception:
            self.busy = False
            raise

    def _work(self, todo: list, now: dt.datetime):
        fails = 0
        try:
            for i, s in enumerate(todo):
                c = fetch_one(s, now)
                time.sleep(2)
                failed = {e.split(":")[0] for e in c.get("err", [])}
                fails = fails + 1 if len(failed) >= 4 else 0
                if fails >= 3:  # Yahoo가 막는 중 — 그만두고 다음 기회에 (가격 수집까지 막히지 않게)
                    self.error = "Yahoo 요청이 연달아 실패 — 주변 정보 갱신을 미룸"
                    return
                with self.lock:
                    old = self.data.get(s)
                    if old and failed:  # 실패한 항목은 예전 값을 유지하고, 전부 실패했으면 시각도 그대로 둬서 곧 다시 받음
                        c = {**c, **{k: old.get(k, {}) for k in failed}, "at": old.get("at", c["at"]) if len(failed) >= 4 else c["at"]}
                    self.data = {**self.data, s: c}
                if i % 10 == 9 or i == len(todo) - 1:
                    self.save()
            self.archive(now.date())
            self.error = None
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
        finally:
            self.busy = False

    def archive(self, day: dt.date):
        """그날 값을 날짜별로 남김 — 옵션·공매도처럼 과거 이력을 구할 수 없는 항목을 나중에 우리 기록으로 검증하려고."""
        d = self.path.parent / "context_log"
        d.mkdir(parents=True, exist_ok=True)
        with self.lock:
            txt = json.dumps(self.data, ensure_ascii=False)
        (d / f"{day.isoformat()}.json").write_text(txt)

    def save(self):
        with self.lock:
            txt = json.dumps(self.data, ensure_ascii=False)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(txt)
        tmp.replace(self.path)


if __name__ == "__main__":
    for s in sys.argv[1:] or ["NVDA"]:
        c = fetch_one(s, dt.datetime.now())
        print(s, "→", line(c) or "정보 없음", c.get("err") or "")
