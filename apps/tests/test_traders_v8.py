"""v8에서 추가한 알파(퀀트 데스크)·번개(10분 단타)·돌쇠 후보군의 핵심 판단을 네트워크 없이 확인.

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

P = T.TRADERS["scalp"]
NOW = dt.datetime(2026, 11, 13, 9, 31, tzinfo=T.NY)


def feat(sym, z, vw=0.01, day=0.02, rv=4.0, liquid=True):
    return dict(sym=sym, z=z, vw=vw, day=day, rv=rv, liquid=liquid, r10=z * 0.002, last=100.0)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "DATA", tmp_path)
    return tmp_path


def bars(sym_open: dict) -> dict:
    idx = pd.DatetimeIndex([pd.Timestamp("2026-11-13 09:30", tz=T.NY)])
    return {s: pd.DataFrame(dict(Open=o, High=o, Low=o, Close=o, Volume=1000.0), index=idx) for s, o in sym_open.items()}


# ── 번개: 10분 판단 ──
def test_scalp_buys_the_deepest_dip_that_meets_every_condition():
    F = {"AAA": feat("AAA", -1.6), "BBB": feat("BBB", -2.4), "CCC": feat("CCC", -3.0, rv=1.0)}
    assert T.scalp_pick(F, None, P, True)[:2] == (None, "BBB")


@pytest.mark.parametrize("bad", [dict(vw=-0.001), dict(day=-0.01), dict(rv=2.9), dict(rv=float("nan")), dict(liquid=False), dict(z=-1.4)])
def test_scalp_skips_a_stock_missing_one_condition(bad):
    F = {"AAA": feat("AAA", **{"z": -2.0, **bad})}
    assert T.scalp_pick(F, None, P, True)[:2] == (None, None)


def test_scalp_does_not_buy_when_the_market_is_below_vwap():
    assert T.scalp_pick({"AAA": feat("AAA", -2.0)}, None, P, False)[:2] == (None, None)


def test_scalp_keeps_a_holding_that_held_up_and_ignores_new_picks():
    F = {"AAA": feat("AAA", -0.5), "BBB": feat("BBB", -2.4)}
    assert T.scalp_pick(F, "AAA", P, True)[:2] == ("AAA", None)


def test_scalp_keeps_a_holding_even_when_the_market_filter_is_off():
    assert T.scalp_pick({"AAA": feat("AAA", 0.3)}, "AAA", P, False)[:2] == ("AAA", None)


def test_scalp_sells_a_holding_that_fell_again_and_switches():
    F = {"AAA": feat("AAA", -1.2, rv=1.0), "BBB": feat("BBB", -2.4)}
    keep, pick, why = T.scalp_pick(F, "AAA", P, True)
    assert (keep, pick) == (None, "BBB") and "AAA" in why


def test_scalp_sells_a_holding_below_vwap_or_without_data():
    assert T.scalp_pick({"AAA": feat("AAA", 0.5, vw=-0.002)}, "AAA", P, True)[0] is None
    assert T.scalp_pick({"AAA": None}, "AAA", P, True)[0] is None


def test_scalp_keeps_a_holding_that_qualifies_as_a_fresh_buy():
    assert T.scalp_pick({"AAA": feat("AAA", -2.0)}, "AAA", P, True)[:2] == ("AAA", None)


# ── 알파: 시가 지정가 체결 ──
def desk(orders_buy=(), orders_sell=(), pos=None, cash=20_000.0):
    t = T.DeskTrader("desk", dict(cash=cash, pos=pos or {}, day=dict(date="2026-11-13", start=cash, trades=0, wins=0, losses=0, realized=0.0)))
    t.orders = {"for": "2026-11-13", "buy": list(orders_buy), "sell": list(orders_sell), "done": False}
    return t


def order(sym, qty, ref, side):
    return dict(sym=sym, qty=qty, ref=ref, limit=round(ref * (1.02 if side == "buy" else 0.98), 2), w=0.1, why="테스트")


def held(shares, entry):
    return dict(shares=shares, init=shares, entry=entry, stop=0.0, stop0=0.0, t1=entry, t2=entry, hi=entry, setup="DESK", src="퀀트 데스크",
                opened="2026-11-12T09:30:00-05:00", checked="2026-11-12T09:30:00-05:00", half=True, realized=0.0, sold=0, sold_val=0.0,
                mfe=entry, mae=entry, why="", score=10, feat={})


def test_desk_starts_with_the_same_money_as_the_others_and_no_fee():
    t = T.DeskTrader("desk", None)
    assert (t.cash, t.fee) == (T.START_CASH, 0.0)


def test_desk_buys_fractional_shares(data_dir):
    t = desk([order("MU", 0.342, 400.0, "buy")])
    t.at_open(NOW, bars({"MU": 400.0}))
    assert t.pos["MU"]["shares"] == pytest.approx(0.342)


def test_desk_sells_the_whole_position_when_the_order_covers_it(data_dir):
    t = desk(orders_sell=[order("SPY", 3.2, 100.0, "sell")], pos={"SPY": held(3.2, 95.0)})
    t.at_open(NOW, bars({"SPY": 100.0}))
    assert "SPY" not in t.pos


def test_desk_buys_at_the_open_inside_the_limit(data_dir):
    t = desk([order("SPY", 10, 100.0, "buy")])
    t.at_open(NOW, bars({"SPY": 101.0}))
    assert t.pos["SPY"]["shares"] == 10 and t.orders["done"]
    assert t.cash == pytest.approx(20_000 - 10 * 101.0 * (1 + T.SLIP))


def test_desk_skips_a_buy_that_gaps_above_the_limit(data_dir):
    t = desk([order("SPY", 10, 100.0, "buy")])
    t.at_open(NOW, bars({"SPY": 102.5}))
    assert not t.pos and t.cash == 20_000 and t.orders["done"]


def test_desk_skips_a_sell_that_gaps_below_the_limit(data_dir):
    t = desk(orders_sell=[order("SPY", 10, 100.0, "sell")], pos={"SPY": held(10, 95.0)})
    t.at_open(NOW, bars({"SPY": 97.0}))
    assert t.pos["SPY"]["shares"] == 10


def test_desk_trims_a_position_and_adds_to_another_at_average_cost(data_dir):
    t = desk([order("QQQ", 5, 200.0, "buy")], [order("SPY", 4, 100.0, "sell")], pos={"SPY": held(10, 95.0), "QQQ": held(5, 180.0)}, cash=2_000.0)
    t.at_open(NOW, bars({"SPY": 100.0, "QQQ": 200.0}))
    assert t.pos["SPY"]["shares"] == 6
    assert t.pos["QQQ"]["shares"] == 10 and t.pos["QQQ"]["entry"] == pytest.approx((180.0 + 200.0 * (1 + T.SLIP)) / 2)
    assert t.cash == pytest.approx(2_000 + 4 * 100.0 * (1 - T.SLIP) - 5 * 200.0 * (1 + T.SLIP))


def test_desk_waits_for_missing_open_prices_then_gives_up_after_0945(data_dir):
    t = desk([order("SPY", 10, 100.0, "buy")])
    t.at_open(NOW, {})
    assert not t.orders["done"]
    t.at_open(NOW.replace(minute=46), {})
    assert t.orders["done"] and not t.pos


def test_desk_never_spends_more_cash_than_it_has(data_dir):
    t = desk([order("SPY", 100, 100.0, "buy")], cash=1_050.0)
    t.at_open(NOW, bars({"SPY": 100.0}))
    assert t.pos["SPY"]["shares"] == pytest.approx(10.494) and t.cash >= 0


# ── 돌쇠 후보군 · 설정 ──
def test_only_the_breakout_trader_looks_beyond_the_watchlist():
    wide, narrow = T.SwingTrader("swing", None).pool(), T.SwingTrader("def", None).pool()
    assert narrow == T.WATCHLIST and set(T.NDX) <= set(wide) and len(wide) == len(set(wide))


def test_pending_orders_are_reported_until_done():
    t = T.SwingTrader("swing", None)
    t.orders = {"for": "2026-11-16", "buy": [dict(sym="COST")], "sell": [dict(sym="AMD")], "done": False}
    assert t.pending() == ["AMD", "COST"]
    t.orders["done"] = True
    assert t.pending() == []


def test_volume_profile_bins_match_the_ten_minutes_before_the_decision(data_dir):
    t = T.ScalpTrader("scalp", None)
    idx = pd.date_range("2026-11-13 09:30", periods=20, freq="1min", tz=T.NY)
    px = np.r_[np.full(10, 100.0), np.linspace(100.0, 99.0, 10)]
    df = pd.DataFrame(dict(Open=px, High=px, Low=px, Close=px, Volume=1000.0), index=idx)
    t.vprof = {"AAA": np.r_[9e9, 9e9, 1000.0, 1500.0, np.zeros(74)]}  # 09:40~09:50 평소 거래량 2,500
    a = dict(atr=2.0, pdc=98.0, dvol=5e7)
    f = t.feats("AAA", {"AAA": a}, {"AAA": df}, 590, dt.date(2026, 11, 13))
    assert f["rv"] == pytest.approx(10_000 / 2_500)
    assert f["r10"] == pytest.approx(99.0 / 100.0 - 1)
    assert f["z"] == pytest.approx(f["r10"] / (2.0 / 98.0 * T.K10))
    assert f["day"] > 0 and f["vw"] < 0


def test_stale_minute_bars_give_no_reading(data_dir):
    t = T.ScalpTrader("scalp", None)
    idx = pd.date_range("2026-11-13 09:30", periods=20, freq="1min", tz=T.NY)
    df = pd.DataFrame(dict(Open=100.0, High=100.0, Low=100.0, Close=100.0, Volume=1000.0), index=idx)
    assert t.feats("AAA", {"AAA": dict(atr=2.0, pdc=98.0, dvol=5e7)}, {"AAA": df}, 600, dt.date(2026, 11, 13)) is None


# ── 알파: 계좌 크기가 달라도 같은 비중 ──
def test_small_virtual_account_gets_the_same_weights_as_the_alpaca_account():
    import paper_desk as D
    tgt = pd.Series({"BIL": 0.38, "SPY": 0.11, "MU": 0.0068, "ADP": 0.0125})
    px = pd.Series({"BIL": 91.5, "SPY": 754.0, "MU": 131.0, "ADP": 290.0})
    quiet = lambda *_, **__: None
    big = {o["sym"]: o["qty"] * px[o["sym"]] / 100_000 for o in D.plan(tgt, px, 100_000, {}, True, note=quiet)}
    small = {o["sym"]: o["qty"] * px[o["sym"]] / 20_000 for o in D.plan(tgt, px, 20_000, {}, True, note=quiet, frac=True)}
    assert set(small) == set(big) == set(tgt.index)
    for s in tgt.index:
        assert small[s] == pytest.approx(tgt[s], abs=0.0003) and big[s] == pytest.approx(tgt[s], abs=0.008)
    whole = {o["sym"] for o in D.plan(tgt, px, 20_000, {}, True, note=quiet)}
    assert "MU" not in whole  # 1주 단위로는 $20,000 계좌에서 작은 비중을 못 삼 — 소수점 주식이 필요한 이유
