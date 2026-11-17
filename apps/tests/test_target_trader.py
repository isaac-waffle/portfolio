"""v12 리포(목표가 추종) — 순위·주간 리밸런싱·주변 정보 60거래일 건수를 네트워크 없이 확인.

  .venv/bin/python -m pytest apps/tests -q
"""
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import traders as T  # noqa: E402
import traders_context as C  # noqa: E402

FRI, WED = dt.date(2026, 11, 20), dt.date(2026, 11, 18)


def ctx(up, dn):
    return dict(an=dict(up=0, down=0, pt_up=0, pt_down=0, pt_up_q=up, pt_down_q=dn), at="2026-11-17T20:00:00")


def daily(px=100.0, vol=1e6, n=260, end=FRI):
    idx = pd.DatetimeIndex(pd.bdate_range(end=end, periods=n).date)
    return pd.DataFrame(dict(Open=px, High=px, Low=px, Close=px, Volume=vol), index=idx)


@pytest.fixture
def ripo(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "DATA", tmp_path)
    book = C.ContextBook(tmp_path / "context.json")
    monkeypatch.setattr(T, "CONTEXT", book)
    t = T.TargetTrader("pt", None)
    monkeypatch.setattr(t, "pool", lambda: ["AAA", "BBB", "CCC", "DDD", "EEE"])
    return t, book


# ── 순위 ──
def test_rank_orders_by_net_raises_then_raises_then_name():
    book = {"AAA": ctx(5, 1), "BBB": ctx(6, 2), "CCC": ctx(3, 0), "DDD": ctx(4, 0)}
    rows, top = T.pt_rank(book, list(book), set(book), 3)
    assert [r[0] for r in rows] == ["BBB", "AAA", "DDD", "CCC"] and top == ["BBB", "AAA", "DDD"]


def test_rank_skips_illiquid_missing_and_non_positive():
    book = {"AAA": ctx(2, 2), "BBB": ctx(1, 3), "CCC": ctx(9, 0), "DDD": dict(an={}), "EEE": None, "FFF": ctx(1, 0)}
    rows, top = T.pt_rank(book, list(book), {"AAA", "BBB", "DDD", "EEE", "FFF"}, 10)
    assert [r[0] for r in rows] == ["FFF", "AAA", "BBB"] and top == ["FFF"]


# ── 리밸런싱 ──
def test_waits_until_most_of_the_pool_has_target_history(ripo, monkeypatch):
    t, book = ripo
    book.data = {"AAA": ctx(3, 0), "BBB": dict(an=dict(up=1)), "CCC": ctx(1, 0)}
    monkeypatch.setattr(t, "daily_bars", lambda day: pytest.fail("가격까지 받으면 안 됨"))
    assert t.signals(FRI, {}) is False and t.sig_day is None


def test_retries_at_most_every_fifteen_minutes(ripo, monkeypatch):
    t, book = ripo
    t.signals(FRI, {})
    monkeypatch.setattr(T.TargetTrader, "pool", lambda self: pytest.fail("15분 안에 다시 보면 안 됨"))
    assert t.signals(FRI, {}) is False


def test_friday_rebalance_sells_dropouts_and_buys_the_top(ripo, monkeypatch):
    t, book = ripo
    book.data = {"AAA": ctx(5, 0), "BBB": ctx(4, 1), "CCC": ctx(1, 1), "DDD": ctx(9, 0), "EEE": ctx(2, 0)}
    F = {s: daily() for s in ["AAA", "BBB", "CCC", "DDD", "EEE", "SPY"]}
    F["DDD"] = daily(vol=1e3)  # 거래대금 미달
    monkeypatch.setattr(t, "daily_bars", lambda day: F)
    t.P = dict(t.P, max_pos=2)
    t.pos = {"CCC": dict(shares=1, entry=100.0), "AAA": dict(shares=1, entry=100.0)}
    t.orders["rebal_wk"] = "2026-11-09"
    assert t.signals(FRI, {}) is True
    o = t.orders
    assert o["for"] == "2026-11-23" and o["rebal_wk"] == "2026-11-16" and [x["sym"] for x in o["sell"]] == ["CCC"] and [x["sym"] for x in o["buy"]] == ["BBB"]
    assert o["buy"][0]["w"] == 0.5 and "↑4/↓1" in o["buy"][0]["why"]
    assert t.watch[0]["sym"] == "AAA" and t.watch[0]["ok"] and not any(w["sym"] == "DDD" for w in t.watch)


def test_midweek_keeps_holdings_without_orders(ripo, monkeypatch):
    t, book = ripo
    t.orders["rebal_wk"] = "2026-11-16"
    monkeypatch.setattr(t, "daily_bars", lambda day: pytest.fail("주중엔 순위를 다시 안 매김"))
    assert t.signals(WED, {}) is True
    assert t.orders["buy"] == [] and t.orders["sell"] == [] and t.orders["rebal_wk"] == "2026-11-16" and t.sig_day == "2026-11-18"


def test_buys_at_the_open_with_equal_weight_and_no_stop(ripo):
    t, _ = ripo
    t.new_day(dt.date(2026, 11, 23), {}, {})
    t.orders = {"for": "2026-11-23", "buy": [dict(sym="AAA", w=0.1, atr=0, why="x", src="나스닥100")], "sell": [], "done": False}
    now = dt.datetime(2026, 11, 23, 9, 31, tzinfo=T.NY)
    b = pd.DataFrame([dict(Open=50.0, High=50.0, Low=50.0, Close=50.0, Volume=1.0)], index=pd.DatetimeIndex([pd.Timestamp("2026-11-23 09:30", tz=T.NY)]))
    t.at_open(now, {"AAA": b})
    p = t.pos["AAA"]
    assert p["stop"] == 0 and p["setup"] == "PT" and p["shares"] == int(T.START_CASH * 0.1 / (50 * (1 + T.SLIP)))


def test_engine_builds_ripo_from_its_config():
    assert T.TRADERS["pt"]["mode"] == "target" and T.SETUP_OF["target"] == "PT" and "PT" in T.SETUP_KO and "PT" in T.SETUP_EN


# ── 주변 정보: 60거래일 건수 ──
class FakeTicker:
    def __init__(self, ud):
        self.upgrades_downgrades = ud


def test_analyst_counts_target_moves_over_sixty_trading_days():
    now = pd.Timestamp("2026-11-17 12:00")
    when = [now - pd.Timedelta(days=d) for d in (1, 30, 70, 86, 100)]
    ud = pd.DataFrame(dict(Firm="X", ToGrade="Buy", Action=["main", "up", "main", "main", "main"],
                           currentPriceTarget=[110, 90, 120, 130, 140], priorPriceTarget=[100, 100, 100, 100, 100]),
                      index=pd.DatetimeIndex(when).tz_localize("America/New_York").tz_convert("UTC").tz_localize(None))
    a = C.analyst(FakeTicker(ud), now)
    assert (a["pt_up"], a["pt_down"], a["pt_up_q"], a["pt_down_q"], a["up"]) == (1, 1, 3, 1, 1)


def test_analyst_keeps_quarter_counts_when_nothing_in_the_last_sixty_days():
    now = pd.Timestamp("2026-11-17 12:00")
    ud = pd.DataFrame(dict(Firm="X", ToGrade="Buy", Action=["main"], currentPriceTarget=[120], priorPriceTarget=[100]),
                      index=pd.DatetimeIndex([now - pd.Timedelta(days=75)]).tz_localize("America/New_York").tz_convert("UTC").tz_localize(None))
    assert C.analyst(FakeTicker(ud), now) == dict(up=0, down=0, pt_up=0, pt_down=0, pt_up_q=1, pt_down_q=0)


def test_entries_saved_before_v12_count_as_stale(tmp_path):
    book = C.ContextBook(tmp_path / "context.json")
    now = dt.datetime(2026, 11, 17, 21, 0)
    book.data = {"OLD": dict(an=dict(pt_up=1), at="2026-11-17T20:00:00"), "NEW": ctx(1, 0), "NOAN": dict(an={}, at="2026-11-17T20:00:00"),
                 "ODD": dict(an="x", at="2026-11-17T20:00:00"),
                 "NONE": dict(an=dict(none=True), at="2026-11-17T20:00:00")}
    assert book.stale(["OLD", "NEW", "NOAN", "ODD", "NONE"], now) == ["OLD", "NOAN", "ODD"]


@pytest.mark.parametrize("day,nxt,done,due", [
    (FRI, dt.date(2026, 11, 23), None, True),           # 처음
    (FRI, dt.date(2026, 11, 23), "2026-11-09", True),   # 금요일, 이번 주 아직
    (FRI, dt.date(2026, 11, 23), "2026-11-16", False),  # 이미 함
    (WED, dt.date(2026, 11, 19), "2026-11-09", False),  # 주중, 지난주에 함
    (WED, dt.date(2026, 11, 19), "2026-11-02", True),   # 지난주 놓침 → 바로
    (dt.date(2026, 11, 23), dt.date(2026, 11, 24), "2026-11-09", True),  # 금요일 실패 뒤 월요일 마감
    (dt.date(2026, 11, 25), dt.date(2026, 11, 27), "2026-11-16", False),  # 추수감사절 주 수요일 (목 휴장, 금 조기 마감)
    (dt.date(2026, 11, 27), dt.date(2026, 11, 30), "2026-11-16", True),
])
def test_rebalance_is_due_at_week_end_or_after_a_missed_week(day, nxt, done, due):
    assert T.rebal_due(day, nxt, done) is due


def test_failed_or_old_analyst_values_do_not_count_as_covered():
    assert C.has_targets(ctx(1, 0)) and C.has_targets(dict(an=dict(none=True)))
    assert not any(C.has_targets(c) for c in (None, {}, dict(an={}), dict(an=dict(pt_up=1)), dict(an="x")))


def test_ticker_without_any_rating_history_is_marked_none():
    assert C.analyst(FakeTicker(None), pd.Timestamp("2026-11-17")) == dict(none=True)
