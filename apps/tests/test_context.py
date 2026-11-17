"""주변 정보(traders_context) — 네트워크 없이 한 줄 요약·갱신 대상·트레이더 설명 연결을 확인.

  .venv/bin/python -m pytest apps/tests -q
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import traders as T  # noqa: E402
import traders_context as C  # noqa: E402

NOW = dt.datetime(2026, 11, 17, 10, 0)
FULL = dict(sym="MRNA", at=NOW.isoformat(),
            cons=dict(rec=2.9, n=19, target=160.0, upside=-0.26, short=0.10, px=215.0),
            an=dict(up=0, down=2, pt_up=7, pt_down=0),
            earn=dict(surprise=130.0, last="2026-11-05", next="2026-11-20"),
            opt=dict(exp="2026-11-27", pc=1.31, iv=0.9, move=0.12),
            season=dict(month=11, avg=0.21, win=0.71, years=7))


def test_line_mentions_every_source():
    txt = C.line(FULL, NOW.date())
    for part in ("의견 중립(2.9, 19곳)", "목표가 -26%", "등급 ↑0/↓2", "목표가 ↑7/↓0", "지난 실적 +130%", "실적 발표 3일 뒤",
                 "풋/콜 1.31", "예상 변동 ±12%", "공매도 10%", "11월 평균 +21%(71% 상승)"):
        assert part in txt


def test_line_skips_missing_parts():
    assert C.line(None) == ""
    assert C.line(dict(sym="X", at=NOW.isoformat(), cons={}, an={}, earn={}, opt={}, season={})) == ""
    far = dict(FULL, earn=dict(surprise=None, next="2027-02-01"))
    assert "실적" not in C.line(far, NOW.date())
    assert "실적 발표" not in C.line(FULL, dt.date(2026, 11, 25))  # 발표일이 지나면 안 보임


def test_score_is_zero_until_something_is_validated():
    assert C.score(FULL) == 0.0 and C.score(None) == 0.0


def test_stale_lists_missing_and_old_symbols(tmp_path):
    book = C.ContextBook(tmp_path / "context.json")
    book.data = {"NEW": dict(at=(NOW - dt.timedelta(hours=1)).isoformat()), "OLD": dict(at=(NOW - dt.timedelta(hours=30)).isoformat())}
    assert book.stale(["NEW", "OLD", "NONE", "OLD"], NOW) == ["OLD", "NONE"]


def test_book_saves_and_reloads(tmp_path):
    book = C.ContextBook(tmp_path / "context.json")
    book.data = {"MRNA": FULL}
    book.save()
    book.archive(NOW.date())
    assert C.ContextBook(tmp_path / "context.json").get("MRNA")["cons"]["n"] == 19
    assert (tmp_path / "context_log" / "2026-11-17.json").exists()


def test_broken_file_starts_empty(tmp_path):
    (tmp_path / "context.json").write_text("{not json")
    assert C.ContextBook(tmp_path / "context.json").data == {}


def test_trader_notes_use_the_book(tmp_path, monkeypatch):
    book = C.ContextBook(tmp_path / "context.json")
    book.data = {"MRNA": FULL, "NVDA": dict(FULL, earn=dict(next="2026-12-30"))}
    monkeypatch.setattr(T, "CONTEXT", book)
    monkeypatch.setattr(T, "now_ny", lambda: dt.datetime(2026, 11, 17, 10, 0, tzinfo=T.NY))
    assert T.ctx_note("MRNA").startswith(" 참고(MRNA) — 의견 중립")
    assert T.ctx_note("ZZZZ") == ""
    assert "MRNA(3일 뒤)" in T.earn_soon(["MRNA", "NVDA", "ZZZZ"]) and "NVDA" not in T.earn_soon(["MRNA", "NVDA"])
    monkeypatch.setattr(T, "CONTEXT", None)
    assert T.ctx_note("MRNA") == "" and T.earn_soon(["MRNA"]) == ""


def test_odd_saved_values_never_raise(tmp_path):
    assert C.line(dict(cons=dict(rec="x", n=3)), NOW.date()) == ""
    assert C.line(dict(an=dict(up=1)), NOW.date()) == "60일 등급 ↑1/↓0 목표가 ↑0/↓0"
    (tmp_path / "context.json").write_text('{"A": 1, "B": {"at": "nope"}, "C": {}}')
    book = C.ContextBook(tmp_path / "context.json")
    assert set(book.data) == {"B", "C"} and book.stale(["A", "B", "C"], NOW) == ["A", "B", "C"]
    aware = C.ContextBook(tmp_path / "x.json")
    aware.data = {"Z": dict(at="2026-11-17T09:30:00-05:00")}
    assert aware.stale(["Z"], NOW) == []


def test_in_session_refresh_only_fetches_unknown_symbols(tmp_path, monkeypatch):
    book = C.ContextBook(tmp_path / "context.json")
    book.data = {"OLD": dict(at=(NOW - dt.timedelta(hours=30)).isoformat())}
    got = []
    monkeypatch.setattr(book, "_work", lambda todo, now: (got.append(todo), setattr(book, "busy", False)))
    book.refresh(["OLD", "NEW"], NOW, quiet=False)
    import time; time.sleep(0.2)
    assert got == [["NEW"]]
    book.refresh(["OLD", "NEW"], NOW, quiet=True)
    time.sleep(0.2)
    assert got[-1] == ["OLD", "NEW"]


def test_failed_sections_keep_yesterdays_values(tmp_path, monkeypatch):
    book = C.ContextBook(tmp_path / "context.json")
    book.data = {"MRNA": FULL}
    monkeypatch.setattr(C.time, "sleep", lambda *_: None)
    later = NOW + dt.timedelta(hours=24)
    monkeypatch.setattr(C, "fetch_one", lambda s, now: dict(sym=s, at=now.isoformat(), cons=dict(rec=1.0, n=5), an={}, earn={}, opt={}, season={}, err=["opt: X"]))
    book._work(["MRNA"], later)
    assert book.get("MRNA")["opt"]["pc"] == 1.31 and book.get("MRNA")["cons"]["rec"] == 1.0 and book.get("MRNA")["at"] == later.isoformat()
    monkeypatch.setattr(C, "fetch_one", lambda s, now: dict(sym=s, at=now.isoformat(), cons={}, an={}, earn={}, opt={}, season={}, err=[f"{k}: X" for k in ("cons", "an", "earn", "opt", "season")]))
    book._work(["MRNA", "A", "B", "C"], later + dt.timedelta(hours=24))
    assert book.get("MRNA")["cons"]["rec"] == 1.0 and book.get("MRNA")["at"] == later.isoformat()  # 전부 실패 → 예전 값·시각 유지
    assert "C" not in book.data and "미룸" in book.error and book.busy is False
