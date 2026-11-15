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


def bars(sym_open: dict, later: dict | None = None) -> dict:
    """09:30 봉(시가 = 값) + 선택적으로 09:31 봉 {종목: (시가, 고가, 저가)}."""
    out = {}
    for s, o in sym_open.items():
        rows, idx = [dict(Open=o, High=o, Low=o, Close=o, Volume=1000.0)], [pd.Timestamp("2026-11-13 09:30", tz=T.NY)]
        if later and s in later:
            op, hi, lo = later[s]
            rows.append(dict(Open=op, High=hi, Low=lo, Close=op, Volume=1000.0))
            idx.append(pd.Timestamp("2026-11-13 09:31", tz=T.NY))
        out[s] = pd.DataFrame(rows, index=pd.DatetimeIndex(idx))
    return out


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


def test_desk_leaves_a_buy_working_when_the_open_gaps_above_the_limit(data_dir):
    t = desk([order("SPY", 10, 100.0, "buy")])
    t.at_open(NOW, bars({"SPY": 102.5}))
    assert not t.pos and t.cash == 20_000 and not t.orders["done"]


def test_desk_fills_a_gapped_buy_at_the_limit_when_price_comes_back(data_dir):
    t = desk([order("SPY", 10, 100.0, "buy")])
    b = bars({"SPY": 102.5}, {"SPY": (102.4, 102.6, 101.5)})
    t.at_open(NOW, {"SPY": b["SPY"].iloc[:1]})
    t.at_open(NOW.replace(minute=32), b)
    assert t.pos["SPY"]["shares"] == 10 and t.pos["SPY"]["entry"] == pytest.approx(102.0 * (1 + T.SLIP)) and t.orders["done"]


def test_desk_does_not_fill_the_same_order_twice(data_dir):
    t = desk([order("SPY", 10, 100.0, "buy")])
    b = bars({"SPY": 101.0}, {"SPY": (100.5, 100.6, 100.4)})
    t.at_open(NOW, b)
    t.at_open(NOW.replace(minute=32), b)
    assert t.pos["SPY"]["shares"] == 10


def test_desk_leaves_a_sell_working_when_the_open_gaps_below_the_limit(data_dir):
    t = desk(orders_sell=[order("SPY", 10, 100.0, "sell")], pos={"SPY": held(10, 95.0)})
    t.at_open(NOW, bars({"SPY": 97.0}))
    assert t.pos["SPY"]["shares"] == 10 and not t.orders["done"]


def test_desk_trims_a_position_and_adds_to_another_at_average_cost(data_dir):
    t = desk([order("QQQ", 5, 200.0, "buy")], [order("SPY", 4, 100.0, "sell")], pos={"SPY": held(10, 95.0), "QQQ": held(5, 180.0)}, cash=2_000.0)
    t.at_open(NOW, bars({"SPY": 100.0, "QQQ": 200.0}))
    assert t.pos["SPY"]["shares"] == 6
    assert t.pos["QQQ"]["shares"] == 10 and t.pos["QQQ"]["entry"] == pytest.approx((180.0 + 200.0 * (1 + T.SLIP)) / 2)
    assert t.cash == pytest.approx(2_000 + 4 * 100.0 * (1 - T.SLIP) - 5 * 200.0 * (1 + T.SLIP))


def test_desk_buys_wait_for_sell_prices_until_0945(data_dir):
    t = desk([order("QQQ", 5, 200.0, "buy")], [order("SPY", 4, 100.0, "sell")], pos={"SPY": held(10, 95.0)})
    t.at_open(NOW, bars({"QQQ": 200.0}))
    assert "QQQ" not in t.pos
    t.at_open(NOW.replace(minute=46), bars({"QQQ": 200.0}))
    assert t.pos["QQQ"]["shares"] == 5 and not t.orders["done"]


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
    a = {"AAA": dict(atr=2.0, pdc=98.0, dvol=5e7)}
    assert t.feats("AAA", a, {"AAA": df}, 600, dt.date(2026, 11, 13)) is None
    assert t.feats("AAA", a, {"AAA": df.iloc[:-1]}, 590, dt.date(2026, 11, 13)) is None  # 직전 1분봉이 아직 안 옴
    assert t.feats("AAA", a, {"AAA": df}, 590, dt.date(2026, 11, 13)) is not None


def test_scalp_fills_at_the_open_of_the_first_bar_after_the_decision(data_dir):
    t = T.ScalpTrader("scalp", None)
    idx = pd.date_range("2026-11-13 09:30", periods=12, freq="1min", tz=T.NY)
    df = pd.DataFrame(dict(Open=np.arange(100.0, 112.0), High=200.0, Low=50.0, Close=150.0, Volume=1.0), index=idx)
    day = dt.date(2026, 11, 13)
    assert t.px_at("AAA", {"AAA": dict(last=150.0)}, {"AAA": df}, 580, day, 1.0) == 110.0
    assert t.px_at("AAA", {"AAA": dict(last=150.0)}, {"AAA": df.iloc[:10]}, 580, day, 1.0) == 150.0


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


# ── 모나: MRNA 전문 ──
DAY = dt.date(2026, 11, 13)
SNAP = dict(sym="MRNA", pdc=100.0, sma50d=90.0, last=100.0, t="2026-11-13T09:30:00-05:00", rvol=1.0, rs=0.0, atr_pct=0.05, gap=0.0, chg=0.0)


def minute_bars(closes, vol=1000.0, lows=None, highs=None):
    idx = pd.date_range("2026-11-13 09:30", periods=len(closes), freq="1min", tz=T.NY)
    c = np.asarray(closes, float)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame(dict(Open=o, High=np.maximum(o, c) if highs is None else highs, Low=np.minimum(o, c) if lows is None else lows,
                             Close=c, Volume=vol), index=idx)


def mona(mode, cash=20_000.0):
    t = T.MrnaTrader("mrna", dict(cash=cash, day=dict(date=DAY.isoformat(), start=cash, trades=0, wins=0, losses=0, realized=0.0, mode=mode)))
    t.hist, t.hist_day = dict(s1=0.001, move=np.full(390, 0.01)), DAY
    T.LAST_SNAP["MRNA"] = SNAP
    return t


def at(minute):
    return dt.datetime(2026, 11, 13, minute // 60, minute % 60, 20, tzinfo=T.NY)


def test_view_uses_only_finished_minutes_and_fills_gaps():
    df = minute_bars([100, 101, 102, 103, 104]).drop(pd.Timestamp("2026-11-13 09:32", tz=T.NY))
    v = T.mrna_view(df, DAY, 574)  # 09:34 지금 → 09:33 봉까지만
    assert v["b"] == 3 and list(v["C"]) == [100, 101, 101, 103] and v["o0"] == 100
    assert T.mrna_view(df, DAY, 580) is None  # 3분 넘게 늦은 데이터


def test_mona_picks_the_mode_from_the_50_day_average(data_dir):
    df = minute_bars(np.full(12, 100.0))
    for snap, want in ((SNAP, "trend"), (dict(SNAP, sma50d=110.0), "fade")):
        t = mona(None)
        del t.day["mode"]
        t.step(at(582), {"MRNA": snap}, {"MRNA": df}, dict(score=50), {})
        assert t.day["mode"] == want


def test_mona_rests_a_limit_five_units_under_the_lower_of_vwap_and_last_close(data_dir):
    t = mona("fade")
    t.step(at(582), {"MRNA": SNAP}, {"MRNA": minute_bars(np.full(12, 100.0))}, dict(score=50), {})
    lim = t.day["lim"]
    assert lim["px"] == pytest.approx(100.0 - 5 * 100.0 * 0.001) and lim["bar"] == 12


def test_mona_fills_the_limit_only_when_a_trade_prints_through_it(data_dir):
    t = mona("fade")
    t.day["lim"] = dict(px=99.5, bar=12, why="테스트")
    assert not t.on_price(99.5, at(582)) and not t.pos
    assert t.on_price(99.4, at(582))
    p = t.pos["MRNA"]
    assert p["entry"] == 99.5 and p["tgt"] == pytest.approx(99.5 * 1.006) and p["stop"] == pytest.approx(99.5 * 0.984) and p["until"] == 72
    assert t.day["lim"] is None and t.cash == pytest.approx(20_000 - p["shares"] * 99.5 - T.FEE)


def test_mona_ignores_an_expired_limit(data_dir):
    t = mona("fade")
    t.day["lim"] = dict(px=99.5, bar=12, why="테스트")
    assert not t.on_price(99.0, at(584)) and not t.pos


def test_mona_takes_profit_at_the_limit_price_without_slippage(data_dir):
    t = mona("fade")
    t.day["lim"] = dict(px=99.5, bar=12, why="테스트")
    t.on_price(99.4, at(582))
    n, tgt = t.pos["MRNA"]["shares"], t.pos["MRNA"]["tgt"]
    cash = t.cash
    assert not t.on_price(tgt, at(585))  # 같으면 미체결
    assert t.on_price(tgt + 0.3, at(586)) and not t.pos
    assert t.cash == pytest.approx(cash + n * tgt - T.FEE)


def test_mona_stops_out_at_the_market_with_slippage(data_dir):
    t = mona("fade")
    t.day["lim"] = dict(px=99.5, bar=12, why="테스트")
    t.on_price(99.4, at(582))
    n, cash = t.pos["MRNA"]["shares"], t.cash
    assert t.on_price(97.5, at(585)) and not t.pos
    assert t.cash == pytest.approx(cash + n * 97.5 * (1 - T.SLIP) - T.FEE)


def test_mona_falls_back_to_minute_bars_when_no_live_price_arrives(data_dir):
    t = mona("fade")
    flat = np.full(12, 100.0)
    t.step(at(582), {"MRNA": SNAP}, {"MRNA": minute_bars(flat)}, dict(score=50), {})
    dip = minute_bars(np.r_[flat, 99.8], lows=np.r_[flat, 99.2])  # 09:42 봉이 지정가 99.5 아래로 뚫음
    t.step(at(583), {"MRNA": SNAP}, {"MRNA": dip}, dict(score=50), {})
    assert t.pos["MRNA"]["entry"] == pytest.approx(99.5)
    up = minute_bars(np.r_[flat, 99.8, 100.5], lows=np.r_[flat, 99.2, 99.8], highs=np.r_[flat, 100.0, 100.6])
    t.step(at(584), {"MRNA": SNAP}, {"MRNA": up}, dict(score=50), {})
    assert not t.pos and t.day["wins"] == 1


def test_mona_time_exit_after_an_hour(data_dir):
    t = mona("fade")
    t.day["lim"] = dict(px=99.5, bar=12, why="테스트")
    t.on_price(99.4, at(582))
    t.day["bar"] = 70
    t.step(at(642), {"MRNA": dict(SNAP, last=99.6)}, {"MRNA": minute_bars(np.full(72, 99.6))}, dict(score=50), {})
    assert not t.pos


def test_mona_trend_mode_buys_above_the_band_and_sells_below_the_line(data_dir):
    t = mona("trend")
    rise = np.r_[np.full(10, 100.0), np.linspace(100.2, 102.0, 10)]  # 09:49 종가 102 > 상단 101
    t.step(at(590), {"MRNA": dict(SNAP, last=102.0)}, {"MRNA": minute_bars(rise)}, dict(score=50), {})
    assert t.pos["MRNA"]["setup"] == "MTREND" and t.pos["MRNA"]["tgt"] is None and t.day["chk"] == 19
    assert not t.on_price(50.0, at(591))  # 추세 모드는 실시간 손절·익절 없음
    hold = np.r_[rise, np.full(14, 102.0)]
    t.step(at(604), {"MRNA": dict(SNAP, last=102.0)}, {"MRNA": minute_bars(hold)}, dict(score=50), {})
    assert "MRNA" in t.pos  # 아직 판단 시각 아님
    fall = np.r_[hold, 100.5]  # 10:04 종가 100.5 < 청산선
    t.step(at(605), {"MRNA": dict(SNAP, last=100.5)}, {"MRNA": minute_bars(fall)}, dict(score=50), {})
    assert not t.pos and t.day["chk"] == 34


def test_mona_trend_mode_does_not_buy_inside_the_usual_range(data_dir):
    t = mona("trend")
    t.step(at(590), {"MRNA": dict(SNAP, last=100.5)}, {"MRNA": minute_bars(np.linspace(100.0, 100.5, 20))}, dict(score=50), {})
    assert not t.pos and t.day["chk"] == 19


def test_mona_is_flat_ten_minutes_before_the_close(data_dir):
    t = mona("fade")
    t.day["lim"] = dict(px=99.5, bar=12, why="테스트")
    t.on_price(99.4, at(582))
    t.step(at(950), {"MRNA": dict(SNAP, last=99.9)}, {"MRNA": minute_bars(np.full(380, 99.9))}, dict(score=50), {})
    assert not t.pos and not t.day["lim"] and not t.wants_ticks()


def test_mona_waits_fifteen_seconds_before_trusting_a_finished_bar(data_dir):
    t = mona("fade")
    early = dt.datetime(2026, 11, 13, 9, 42, 5, tzinfo=T.NY)
    t.step(early, {"MRNA": SNAP}, {"MRNA": minute_bars(np.full(12, 100.0))}, dict(score=50), {})
    assert t.day["lim"]["bar"] == 11  # 09:41 봉은 아직 덜 끝난 것으로 봄 → 09:40 봉 기준


def test_mona_ignores_trades_printed_before_the_limit_was_placed_or_on_another_day(data_dir):
    t = mona("fade")
    t.day["lim"] = dict(px=99.5, bar=12, at=at(582).isoformat(), why="테스트")
    assert not t.on_price(99.0, at(582).replace(second=10)) and not t.pos
    assert not t.on_price(99.0, at(582).replace(day=14, second=40)) and not t.pos
    assert t.on_price(99.0, at(582).replace(second=40))


def test_mona_does_not_rest_a_new_limit_in_the_minute_of_a_time_exit(data_dir):
    t = mona("fade")
    t.day["lim"] = dict(px=99.5, bar=12, why="테스트")
    t.on_price(99.4, at(582))
    t.day["bar"] = 70
    t.step(at(642), {"MRNA": dict(SNAP, last=99.6)}, {"MRNA": minute_bars(np.full(72, 99.6))}, dict(score=50), {})
    assert not t.pos and not t.day["lim"]


def test_mona_closes_a_position_it_can_no_longer_manage(data_dir):
    t = mona("trend")
    rise = np.r_[np.full(10, 100.0), np.linspace(100.2, 102.0, 10)]
    t.step(at(590), {"MRNA": dict(SNAP, last=102.0)}, {"MRNA": minute_bars(rise)}, dict(score=50), {})
    assert t.pos
    t.hist, t.hist_day, t.hist_try = {}, None, 1e18  # 재시작 뒤 기준값을 못 받은 상황
    t.step(at(591), {"MRNA": dict(SNAP, last=102.0)}, {"MRNA": minute_bars(np.r_[rise, 102.0])}, dict(score=50), {})
    assert not t.pos


# ── 틱: MRNA 초단타 ──
def ticker(cash=20_000.0):
    t = T.TickTrader("tick", dict(cash=cash, day=dict(date=DAY.isoformat(), start=cash, trades=0, wins=0, losses=0, realized=0.0)))
    t.hist, t.hist_day = dict(s1=0.001), DAY
    T.LAST_SNAP["MRNA"] = SNAP
    return t


def tk_at(sec):
    return dt.datetime(2026, 11, 13, 9, 30, tzinfo=T.NY) + dt.timedelta(seconds=sec)


def warm(t, px=101.0, start=0, end=400, step=4):
    for s in range(start, end, step):
        t.on_price(px, tk_at(s))


KT = T.TRADERS["tick"]["k"]
BAND = 101.0 * (1 + KT * 0.001 * 3 ** 0.5)  # 3분 평균 101 × (1 + k·u)


def test_tick_buys_on_the_print_after_a_jump_through_the_band(data_dir):
    t = ticker()
    warm(t)
    assert not t.on_price(BAND + 0.05, tk_at(402)) and t.arm and not t.pos  # 신호 체결에서는 안 삼
    assert t.on_price(BAND + 0.10, tk_at(403))
    p = t.pos["MRNA"]
    raw = BAND + 0.10  # 익절·손절선은 체결가 기준, 매수 단가에만 슬리피지
    u = raw * 0.001 * 3 ** 0.5
    assert p["entry"] == pytest.approx(raw * (1 + T.SLIP)) and p["setup"] == "MTICK"
    assert p["stop"] == pytest.approx(raw - T.TRADERS["tick"]["st"] * u) and p["tgt"] is None and p["until"] is None  # 익절선·시한 없음


@pytest.mark.parametrize("why, kw", [("gap over 10 seconds", dict(gap=15)),
                                     ("before 09:35", dict(end=280)), ("window too short", dict(start=300))])
def test_tick_ignores_a_jump_when(data_dir, why, kw):
    t = ticker()
    px, end = kw.get("px", 101.0), kw.get("end", 400)
    warm(t, px=px, start=kw.get("start", 0), end=end)
    band = px * (1 + KT * 0.001 * 3 ** 0.5)
    t.on_price(band + 0.05, tk_at(end - 4 + kw.get("gap", 2)))
    assert not t.arm


def test_tick_can_require_price_above_yesterdays_close(data_dir):
    for up, want in ((True, False), (False, True)):
        t = ticker()
        t.P = dict(t.P, up=up)
        warm(t, px=99.0)  # 전일 종가 100 아래
        t.on_price(99.0 * (1 + KT * 0.001 * 3 ** 0.5) + 0.05, tk_at(402))
        assert t.arm is want


def test_tick_holds_through_a_big_gain_and_exits_only_at_the_stop(data_dir):
    t = ticker()
    warm(t)
    t.on_price(BAND + 0.05, tk_at(402))
    t.on_price(BAND + 0.10, tk_at(403))
    stop = t.pos["MRNA"]["stop"]
    assert not t.on_price(BAND + 5.0, tk_at(2000)) and t.pos  # 크게 올라도, 시간이 지나도 안 팖
    assert t.on_price(stop - 0.01, tk_at(2010)) and not t.pos


def test_tick_does_not_rearm_while_price_stays_above_the_band(data_dir):
    t = ticker()
    warm(t)
    t.on_price(BAND + 0.05, tk_at(402))
    t.arm = False  # 매수 기회를 건너뛴 경우
    t.on_price(BAND + 0.06, tk_at(404))
    assert not t.arm  # 직전 체결이 이미 선 위 → 새 돌파가 아님


def test_tick_exits_at_target_stop_and_deadline(data_dir):
    for move, reason in ((+1.0, "익절"), (-1.0, "손절"), (0.0, "시간")):
        t = ticker()
        t.P = dict(t.P, tp=4.0, hold=600)  # 익절선·시한을 쓰는 설정도 계속 동작해야 함
        warm(t)
        t.on_price(BAND + 0.05, tk_at(402))
        t.on_price(BAND + 0.10, tk_at(403))
        p = dict(t.pos["MRNA"])
        cash = t.cash
        px = p["tgt"] + 0.2 if move > 0 else p["stop"] - 0.2 if move < 0 else p["entry"]
        assert t.on_price(px, tk_at(410 if move else 1004)) and not t.pos
        got = p["tgt"] if move > 0 else px * (1 - T.SLIP)
        assert t.cash == pytest.approx(cash + p["shares"] * got - T.FEE), reason


def test_tick_does_nothing_without_baselines_or_on_a_stale_day(data_dir):
    t = ticker()
    t.hist = {}
    assert not t.on_price(101.0, tk_at(400)) and t.n_ticks == 0
    t = ticker()
    assert not t.on_price(101.0, tk_at(400).replace(day=14)) and t.n_ticks == 0


def test_tick_keeps_its_position_through_a_quiet_stream_and_guards_the_stop_with_quotes(data_dir):
    t = ticker()
    warm(t)
    t.on_price(BAND + 0.05, tk_at(402))
    t.on_price(BAND + 0.10, tk_at(403))
    stop = t.pos["MRNA"]["stop"]
    t.step(tk_at(403 + 2000), {"MRNA": dict(SNAP, last=stop + 0.5)}, {}, dict(score=50), {})
    assert t.pos  # 체결이 30분 넘게 안 와도 안 팖
    t.last_at = None  # 재시작 직후와 같은 상태
    t.step(tk_at(403 + 2030), {"MRNA": dict(SNAP, last=stop + 0.5)}, {}, dict(score=50), {})
    assert t.pos
    t.step(tk_at(403 + 2060), {"MRNA": dict(SNAP, last=stop - 0.1)}, {}, dict(score=50), {})
    assert not t.pos  # 손절선은 30초 시세로 지킴


def test_tick_flattens_on_the_first_print_after_1550(data_dir):
    t = ticker()
    warm(t)
    t.on_price(BAND + 0.05, tk_at(402))
    t.on_price(BAND + 0.10, tk_at(403))
    n, cash = t.pos["MRNA"]["shares"], t.cash
    assert not t.on_price(BAND + 0.3, tk_at(22799))
    assert t.on_price(BAND + 0.4, tk_at(22801)) and not t.pos
    assert t.cash == pytest.approx(cash + n * (BAND + 0.4) * (1 - T.SLIP) - T.FEE)


# ── 실시간 체결 스트림 ──
def test_stream_passes_each_mrna_trade_to_the_mrna_traders(monkeypatch):
    import asyncio
    import json
    import types

    clock = [dt.datetime(2026, 11, 13, 10, 0, 1, tzinfo=T.NY)]
    monkeypatch.setattr(T, "now_ny", lambda: clock[0])

    class FakeWs:
        def __init__(self):
            self.sent, self.q = [], [
                [{"T": "success", "msg": "connected"}], [{"T": "success", "msg": "authenticated"}],
                [{"T": "subscription", "trades": ["MRNA"]}],
                [{"T": "t", "S": "MRNA", "p": 223.5, "s": 10, "t": "2026-11-13T15:00:00.5Z"},
                 {"T": "t", "S": "SPY", "p": 1.0, "s": 1, "t": "2026-11-13T15:00:00.6Z"},
                 {"T": "t", "S": "MRNA", "p": 223.6, "s": 5, "t": "2026-11-13T14:59:00Z"}]]  # 마지막은 1분 전 체결 → 버림

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def send(self, m):
            self.sent.append(json.loads(m))

        async def recv(self):
            if not self.q:
                clock[0] = clock[0].replace(hour=16)  # 장 끝 → 스트림이 스스로 돌아감
                return json.dumps([])
            return json.dumps(self.q.pop(0))

    ws = FakeWs()
    monkeypatch.setitem(sys.modules, "websockets", types.SimpleNamespace(connect=lambda *a, **k: ws))
    got = []
    E = T.Engine.__new__(T.Engine)
    E.mlock, E.trader_err, E.stream_ok, E.save, E.trades = T.threading.RLock(), {}, 0.0, lambda: got.append("saved"), T.queue.Queue()
    mona_ = types.SimpleNamespace(on_price=lambda px, at: got.append((px, at.hour, at.minute)) or True)
    asyncio.run(E.stream([mona_], "key", "secret"))
    assert ws.sent == [dict(action="auth", key="key", secret="secret"), dict(action="subscribe", trades=["MRNA"])]
    assert E.trades.qsize() == 1 and E.stream_ok > 0  # SPY와 오래된 체결은 큐에 안 들어감
    E.on_trade([mona_], *E.trades.get())
    assert got == [(223.5, 10, 0), "saved"]


def test_stream_refuses_a_failed_login(monkeypatch):
    import asyncio
    import json
    import types

    class Ws:
        n = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def send(self, m):
            pass

        async def recv(self):
            Ws.n += 1
            return json.dumps([{"T": "success", "msg": "connected"}] if Ws.n == 1 else [{"T": "error", "code": 402, "msg": "auth failed"}])

    monkeypatch.setitem(sys.modules, "websockets", types.SimpleNamespace(connect=lambda *a, **k: Ws()))
    E = T.Engine.__new__(T.Engine)
    with pytest.raises(RuntimeError):
        asyncio.run(E.stream([], "k", "s"))


def test_tick_starts_each_day_with_an_empty_window(data_dir):
    t = ticker()
    warm(t, end=23000, step=50)  # 어제 하루치
    t.day["date"] = "2026-11-16"
    nxt = lambda sec: dt.datetime(2026, 11, 16, 9, 30, tzinfo=T.NY) + dt.timedelta(seconds=sec)
    for s_ in range(0, 400, 4):
        t.on_price(101.0, nxt(s_))
    assert t.win[0][0] > 200 and t.n_ticks == 100
    t.on_price(BAND + 0.05, nxt(402))
    assert t.arm


def test_tick_uses_polled_prices_for_exits_only(data_dir):
    t = ticker()
    warm(t)
    assert not t.on_price(BAND + 0.05, tk_at(402), sparse=True) and not t.arm and not t.win  # 신호로 안 씀, 창도 비움
    warm(t, start=404, end=800)
    t.on_price(BAND + 0.05, tk_at(802))
    t.on_price(BAND + 0.10, tk_at(803))
    stop = t.pos["MRNA"]["stop"]
    assert t.on_price(stop - 0.1, tk_at(806), sparse=True) and not t.pos


def test_tick_waits_for_a_full_window_after_a_restart(data_dir):
    t = ticker()
    warm(t, start=1000, end=1100)
    t.on_price(BAND + 0.05, tk_at(1102))
    assert not t.arm
    t2 = ticker()
    warm(t2, start=1000, end=1200)
    t2.on_price(BAND + 0.05, tk_at(1202))
    assert t2.arm


def test_tick_rejects_out_of_order_prints(data_dir):
    t = ticker()
    warm(t)
    t.on_price(BAND + 0.05, tk_at(390))
    assert not t.arm


def test_engine_has_one_definition_of_each_method():
    import inspect
    src = inspect.getsource(T.Engine)
    for name in ("fast_loop", "tick", "save", "publish", "universe"):
        assert src.count(f"    def {name}(") == 1, name
    assert "스트림" in T.Engine.fast_loop.__doc__
