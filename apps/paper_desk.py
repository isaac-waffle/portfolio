"""퀀트 데스크 — strategy_lab에서 검증을 통과한 전략들을 묶어 Alpaca '모의계좌(paper)'에 실제 주문으로 내보냄.

  .venv/bin/python apps/paper_desk.py            # 계산만: 오늘 목표 비중과 낼 주문을 보여 줌 (주문 안 냄)
  .venv/bin/python apps/paper_desk.py --send     # 모의계좌에 주문 (같은 신호일에 두 번 내지 않음, --force로 다시)
  .venv/bin/python apps/paper_desk.py status     # 계좌·보유·SPY 대비 성적

하루 한 번, 장 마감 뒤 일봉이 올라오면 traders.py(항상 켜져 있는 서비스)가 --send로 실행함. 주문은 다음 개장 때 체결.

규칙 (안전장치)
  · 모의계좌 전용 — 주소가 paper-api로 고정이고, 키가 모의계좌용(PK…)이 아니면 거부. 실전 전환은 이 파일에 없음.
  · 현금만 — 총 보유 ≤ 계좌의 95%, 공매도·레버리지 없음. 전략이 안 쓰는 현금은 초단기 국채 ETF(BIL)에 둠.
  · 지정가만 — 매수는 전일 종가 +2%, 매도는 −2% 안에서만 체결 (갭이 그보다 크면 안 사고 다음 날 다시 판단).
  · 주문 한 건 ≤ 계좌의 30%, 하루 주문 ≤ 60건.
  · 고점 대비 −15%면 신규 매수 중단(매도만). journal/paper/STOP 파일이 있으면 아무 주문도 안 냄.
  · 모든 판단을 journal/paper/decisions.jsonl 에 남기고, 금요일마다 'SPY를 그냥 들고 있었을 때'와 비교해 스스로 채점.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import sys
import warnings

import numpy as np
import pandas as pd
import requests
import yfinance as yf

import strategy_lab as L
import swing_lab
import traders as T

warnings.filterwarnings("ignore")
BASE = "https://paper-api.alpaca.markets"  # 모의계좌 고정
OUT = T.APPS / "journal" / "paper"
SLEEVES = {  # 검증 통과 전략 → 계좌에서 맡는 몫
    "tsmom": ("etf", 0.2), "voltgt": ("etf", 0.2), "mom12v": ("stock", 0.2), "minerviniv": ("stock", 0.2), "ep": ("stock", 0.2),
}
MAX_GROSS, MAX_ORDER, MAX_ORDERS, LIMIT_BAND, DD_HALT = 0.95, 0.30, 60, 0.02, 0.15
ETFS = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "SHY", "GLD", "DBC", "VNQ", "LQD", "BIL"]
PARK, PARK_TO = "BIL", 0.90  # 전략들이 안 쓰는 현금은 초단기 국채 ETF에 (계좌의 90%까지)


def hdr() -> dict:
    k, s = T.alpaca_keys()
    if not k or not s:
        sys.exit("Alpaca 키가 없음 (~/.trading_desk.env)")
    if not k.startswith("PK"):
        sys.exit("모의계좌 키(PK…)가 아님 — 이 프로그램은 모의계좌 전용")
    return {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s}


def api(method: str, path: str, **kw):
    r = requests.request(method, BASE + path, headers=hdr(), timeout=30, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} → {r.status_code} {r.text[:200]}")
    return r.json() if r.text else None


def state() -> dict:
    p = OUT / "state.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_state(st: dict):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "state.json").write_text(json.dumps(st, ensure_ascii=False, indent=1))


def note(kind: str, text: str, **extra):
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "decisions.jsonl", "a") as f:
        f.write(json.dumps(dict(ts=T.now_ny().isoformat(), kind=kind, text=text, **extra), ensure_ascii=False) + "\n")
    print(f"[{kind}] {text}")


def add_csv(name: str, row: dict):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    new = not p.exists()
    with open(p, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


# ───────────────────────── 신호 ─────────────────────────
def signal_day(now: dt.datetime) -> dt.date:
    """신호를 계산할 '마지막으로 끝난 거래일'."""
    d = now.date()
    if T.is_trading_day(d) and now.hour * 60 + now.minute >= T.close_min(d) + 10:
        return d
    return T.prev_trading_day(d)


def fresh(day: dt.date):
    stocks = sorted(set(T.WATCHLIST) | set(swing_lab.NDX))
    d = yf.download(ETFS + stocks, period="3y", interval="1d", auto_adjust=True, group_by="ticker", progress=False, threads=True)
    have = set(d.columns.get_level_values(0))

    def panel(syms):
        F = {k: pd.DataFrame({s: d[s][k] for s in syms if s in have}) for k in ("Open", "High", "Low", "Close", "Volume")}
        for k in F:
            F[k].index = pd.DatetimeIndex(F[k].index).tz_localize(None)
            F[k] = F[k][F[k].index <= pd.Timestamp(day)].dropna(how="all", axis=1)
        return F
    E, S = panel(ETFS), panel(stocks)
    last = E["Close"]["SPY"].dropna().index[-1].date()
    if last != day:
        print(f"일봉이 아직 안 올라옴 (마지막 {last}, 필요 {day})")
        sys.exit(2)
    return E, S


def targets(E: dict, S: dict, day: dt.date) -> tuple[pd.Series, dict]:
    """전략별 목표 비중 → 합친 계좌 비중."""
    L.NEXT_DAY = T.next_open(dt.datetime.combine(day, dt.time(17), T.NY)).date()
    strat = L.etf_strategies(E) | L.stock_strategies(S)
    total, per = pd.Series(dtype=float), {}
    for name, (_, share) in SLEEVES.items():
        w = strat[name][0].iloc[-1]
        w = w[w > 1e-6]
        per[name] = {s: round(float(x), 4) for s, x in w.items()}
        total = total.add(w * share, fill_value=0)
    idle = PARK_TO - float(total.sum())
    if idle > 0.01 and PARK in E["Close"].columns:
        total[PARK] = total.get(PARK, 0.0) + idle
        per["현금 대기"] = {PARK: round(idle, 4)}
    return total.sort_values(ascending=False), per


# ───────────────────────── 주문 ─────────────────────────
def plan(tgt: pd.Series, px: pd.Series, equity: float, held: dict, buys_ok: bool) -> list[dict]:
    gross = float(tgt.sum())
    if gross > MAX_GROSS:
        tgt = tgt * MAX_GROSS / gross
    orders = []
    for s in sorted(set(tgt.index) | set(held)):
        if s not in px.index or not np.isfinite(px[s]):
            if s in held:
                note("skip", f"{s}: 보유 중이지만 가격 데이터가 없어 그대로 둠", sym=s)
            continue
        want = int(tgt.get(s, 0.0) * equity / px[s])
        diff = want - held.get(s, 0)
        if diff == 0 or (want > 0 and abs(diff) * px[s] < max(300, 0.005 * equity)):
            continue  # 자잘한 비중 조정은 건너뜀 (전량 매도는 항상 실행)
        if diff > 0 and not buys_ok:
            continue
        if abs(diff) * px[s] > MAX_ORDER * equity and s != PARK:  # 현금 대기용 국채 ETF는 한도 예외
            note("skip", f"{s}: 주문 금액이 계좌의 {MAX_ORDER:.0%}를 넘어 건너뜀", sym=s)
            continue
        side = "buy" if diff > 0 else "sell"
        lim = round(px[s] * (1 + LIMIT_BAND if diff > 0 else 1 - LIMIT_BAND), 2)
        orders.append(dict(sym=s, side=side, qty=abs(diff), limit=lim, ref=round(float(px[s]), 2), weight=round(float(tgt.get(s, 0.0)), 4)))
    orders.sort(key=lambda o: o["side"] != "sell")  # 매도 먼저
    return orders[:MAX_ORDERS]


def grade(st: dict, equity: float, spy: float, day: dt.date):
    """금요일(또는 그 주 마지막 거래일)마다 SPY 보유와 비교."""
    st.setdefault("start", dict(day=day.isoformat(), equity=equity, spy=spy))
    nxt = T.next_open(dt.datetime.combine(day, dt.time(17), T.NY)).date()
    if nxt.isocalendar()[1] == day.isocalendar()[1] or st.get("graded") == day.isoformat():
        return
    me, bh = equity / st["start"]["equity"] - 1, spy / st["start"]["spy"] - 1
    note("grade", f"주간 채점 — 시작({st['start']['day']}) 이후 데스크 {me:+.2%}, SPY 보유 {bh:+.2%} → {'앞섬' if me > bh else '뒤짐'} ({me - bh:+.2%}p)",
         desk=me, spy=bh)
    st["graded"] = day.isoformat()


def run(send: bool, force: bool):
    now = T.now_ny()
    if (OUT / "STOP").exists():
        note("halt", "STOP 파일이 있어 아무것도 하지 않음")
        return
    clock = api("GET", "/v2/clock")
    if clock["is_open"]:
        print("장중에는 실행하지 않음 (일봉이 끝난 뒤에만)")
        sys.exit(2)
    day = signal_day(now)
    st = state()
    if send and st.get("sent_for") == day.isoformat() and not force:
        print(f"{day} 신호는 이미 주문함")
        return
    E, S = fresh(day)
    tgt, per = targets(E, S, day)
    px = pd.concat([E["Close"].iloc[-1], S["Close"].iloc[-1]])
    px = px[~px.index.duplicated()]
    acct = api("GET", "/v2/account")
    equity = float(acct["equity"])
    held = {p["symbol"]: int(float(p["qty"])) for p in api("GET", "/v2/positions")}
    st["peak"] = max(st.get("peak", equity), equity)
    buys_ok = equity >= st["peak"] * (1 - DD_HALT)
    if not buys_ok:
        note("halt", f"계좌 ${equity:,.0f}가 고점 ${st['peak']:,.0f} 대비 −{DD_HALT:.0%} 아래 — 신규 매수 중단, 매도만")
    orders = plan(tgt, px, equity, held, buys_ok)
    print(f"\n신호일 {day} · 계좌 ${equity:,.0f} · 목표 총 비중 {min(tgt.sum(), MAX_GROSS):.0%} ({len(tgt)}종목)")
    for name, w in per.items():
        print(f"  {L.KO.get(name, name):24s} {', '.join(f'{s} {x:.0%}' for s, x in sorted(w.items(), key=lambda kv: -kv[1])) or '현금'}")
    print(f"\n주문 {len(orders)}건:")
    for o in orders:
        print(f"  {'매수' if o['side'] == 'buy' else '매도'} {o['sym']:5s} {o['qty']:4d}주  지정가 {o['limit']:.2f} (전일 종가 {o['ref']:.2f}, 목표 {o['weight']:.1%})")
    if not send:
        print("\n(계산만 — 주문하려면 --send)")
        return
    for o in api("GET", "/v2/orders", params={"status": "open", "limit": 500}):  # 지난번에 안 체결된 데스크 주문은 취소
        if (o.get("client_order_id") or "").startswith("qd-"):
            api("DELETE", f"/v2/orders/{o['id']}")
    tag = f"{day:%y%m%d}-{now:%H%M%S}"
    ok = 0
    for o in orders:
        try:
            r = api("POST", "/v2/orders", json=dict(symbol=o["sym"], qty=str(o["qty"]), side=o["side"], type="limit", limit_price=str(o["limit"]),
                                                    time_in_force="day", client_order_id=f"qd-{tag}-{o['sym']}"))
            ok += 1
            add_csv("orders.csv", dict(ts=now.isoformat(), signal_day=day.isoformat(), **o, id=r["id"], status=r["status"]))
        except Exception as e:
            note("error", f"{o['sym']} 주문 실패: {e}", sym=o["sym"])
    add_csv("equity.csv", dict(day=day.isoformat(), equity=round(equity, 2), cash=acct["cash"], positions=len(held), spy=round(float(px["SPY"]), 2)))
    grade(st, equity, float(px["SPY"]), day)
    st.update(sent_for=day.isoformat(), targets={s: round(float(x), 4) for s, x in tgt.items()}, sleeves=per)
    save_state(st)
    note("send", f"{day} 신호 → 모의계좌 주문 {ok}/{len(orders)}건 (매수 {sum(o['side'] == 'buy' for o in orders)}, 매도 {sum(o['side'] == 'sell' for o in orders)}), 계좌 ${equity:,.0f}")


def status():
    acct = api("GET", "/v2/account")
    st = state()
    print(f"모의계좌 ${float(acct['equity']):,.2f} (현금 ${float(acct['cash']):,.2f}) · 마지막 주문 신호일 {st.get('sent_for', '-')}")
    if st.get("start"):
        print(f"시작 {st['start']['day']} ${st['start']['equity']:,.0f} → {float(acct['equity']) / st['start']['equity'] - 1:+.2%}")
    for p in sorted(api("GET", "/v2/positions"), key=lambda p: -float(p["market_value"])):
        print(f"  {p['symbol']:5s} {int(float(p['qty'])):4d}주  ${float(p['market_value']):>10,.0f}  {float(p['unrealized_plpc']):+.1%}")
    op = api("GET", "/v2/orders", params={"status": "open", "limit": 500})
    print(f"대기 중인 주문 {len(op)}건")


if __name__ == "__main__":
    a = sys.argv[1:]
    status() if "status" in a else run("--send" in a, "--force" in a)
