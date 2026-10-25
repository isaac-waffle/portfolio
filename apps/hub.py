"""내 앱 허브 — 지금까지 만든 앱·사이트·자동 파이프라인·저장소를 한 화면에서 보고 켜고 끄기.

실행:  apps/start.sh   (또는 Finder에서 apps/Open Hub.command 더블클릭)
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

import requests
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent  # portfolio 저장소
GH_DIR = ROOT.parent  # ~/Documents/GitHub
APPS = ROOT / "apps"
LOGS = APPS / ".hub" / "logs"
REPO = "isaac-waffle/portfolio"
SITE = "https://isaac-waffle.github.io/portfolio/"
STREAMLIT = str(ROOT / ".venv" / "bin" / "streamlit")

LOCAL = [
    dict(key="trading", icon="📈", name="트레이딩 데스크", port=8501,
         cmd=[STREAMLIT, "run", "apps/trading_app.py"],
         desc="미국 주식 단타 보조 — 시장 판단(3가지 전략), 스캐너, 종목 분석, 매매 일지, 백테스트, 알림"),
    dict(key="earnings", icon="🗓️", name="어닝 대시보드", port=8502,
         cmd=[STREAMLIT, "run", "apps/earnings_app.py"],
         desc="미국 어닝 캘린더와 관심종목별 어닝 분석 (과거 반응, 예측, 뉴스)"),
    dict(key="site", icon="🌐", name="포트폴리오 사이트 (로컬 미리보기)", port=8765,
         cmd=["python3", "-m", "http.server", "8765", "--directory", "site"],
         desc="GitHub Pages에 올라가는 site/ 폴더를 내 컴퓨터에서 미리 보기"),
]
WEB = [
    ("포트폴리오 홈", SITE, "자기소개·프로젝트 카드"),
    ("Forecast Lab", SITE + "lab.html", "16개 종목 다음 5분·1시간·세션 예측과 채점"),
    ("Earnings Desk", SITE + "demos/earnings.html", "어닝 캘린더 웹 버전 (3시간마다 갱신)"),
    ("DEGIRO P&L Analyzer", SITE + "demos/pnl.html", "DEGIRO 거래내역 손익 분석"),
    ("Woning Scout", SITE + "demos/housing.html", "네덜란드 안전한 집 찾기"),
]
PIPES = [("deploy.yml", "사이트 배포", "main에 push할 때"), ("forecast.yml", "Forecast Lab 예측", "장중 15분마다"),
         ("earnings.yml", "Earnings Desk 데이터", "3시간마다")]

st.set_page_config(page_title="내 앱 허브", page_icon="🧭", layout="wide")


# ───────────────────────── helpers ─────────────────────────
def is_up(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def start(app: dict):
    LOGS.mkdir(parents=True, exist_ok=True)
    cmd = list(app["cmd"])
    if cmd[0] == STREAMLIT:
        cmd += ["--server.port", str(app["port"]), "--server.headless", "true", "--browser.gatherUsageStats", "false"]
    log = open(LOGS / f"{app['key']}.log", "a")
    subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(40):
        if is_up(app["port"]):
            return
        time.sleep(0.25)


def stop(app: dict):
    out = subprocess.run(["lsof", "-ti", f"tcp:{app['port']}", "-sTCP:LISTEN"], capture_output=True, text=True).stdout
    for pid in out.split():
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (ProcessLookupError, ValueError):
            pass
    for _ in range(20):
        if not is_up(app["port"]):
            return
        time.sleep(0.25)


def ago(t: dt.datetime) -> str:
    s = (dt.datetime.now(dt.timezone.utc) - t).total_seconds()
    return f"{int(s // 60)}분 전" if s < 3600 else f"{int(s // 3600)}시간 전" if s < 86400 else f"{int(s // 86400)}일 전"


@st.cache_data(ttl=300, show_spinner=False)
def web_status() -> dict:
    out = {}
    for _, url, _ in WEB:
        try:
            out[url] = requests.get(url, timeout=6).status_code
        except Exception:
            out[url] = None
    return out


@st.cache_data(ttl=300, show_spinner=False)
def pipe_status() -> dict:
    out = {}
    for wf, _, _ in PIPES:
        try:
            r = subprocess.run(["gh", "run", "list", "-R", REPO, "--workflow", wf, "-L", "1",
                                "--json", "status,conclusion,createdAt,url"], capture_output=True, text=True, timeout=20)
            runs = json.loads(r.stdout or "[]")
            out[wf] = runs[0] if runs else None
        except Exception:
            out[wf] = None
    return out


@st.cache_data(ttl=60, show_spinner=False)
def repos() -> list[dict]:
    rows = []
    for d in sorted(GH_DIR.iterdir()):
        if not (d / ".git").exists():
            continue
        g = lambda *a: subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True).stdout.strip()
        st_ = g("status", "--porcelain").splitlines()
        last = g("log", "-1", "--format=%ct|%s")
        ts, msg = (last.split("|", 1) + [""])[:2] if last else ("", "")
        url = g("remote", "get-url", "origin").removesuffix(".git").replace("git@github.com:", "https://github.com/")
        rows.append(dict(name=d.name, path=d, branch=g("rev-parse", "--abbrev-ref", "HEAD"),
                         changed=sum(not x.startswith("??") for x in st_), untracked=[x[3:] for x in st_ if x.startswith("??")],
                         last=dt.datetime.fromtimestamp(int(ts), dt.timezone.utc) if ts else None, msg=msg, url=url))
    return rows


@st.cache_data(ttl=600, show_spinner=False)
def earnings_soon() -> list[str]:
    try:
        js = requests.get("https://raw.githubusercontent.com/isaac-waffle/portfolio/earnings-data/calendar.json", timeout=8).json()
    except Exception:
        return []
    today = dt.date.today()
    w = sorted((x for x in js.get("watch", []) if x.get("next") and dt.date.fromisoformat(x["next"]) >= today), key=lambda x: x["next"])
    return [f"{x['sym']} {x['next'][5:]}" for x in w[:5]]


def trading_stats() -> list[str]:
    out = []
    j = APPS / "journal" / "trades.csv"
    if j.exists():
        with open(j, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        if rows:
            out.append(f"매매 일지 {len(rows)}건 · 마지막 {max(r['date'] for r in rows)}")
    a = APPS / "journal" / "alerts.json"
    if a.exists():
        try:
            n = sum(x.get("active", True) for x in json.loads(a.read_text(encoding="utf-8")))
            out.append(f"켜진 알림 {n}개 (앱이 켜져 있어야 울림)")
        except json.JSONDecodeError:
            pass
    return out or ["아직 매매 일지·알림 없음"]


# ───────────────────────── page ─────────────────────────
st.title("🧭 내 앱 허브")
st.caption(f"{ROOT} · {dt.datetime.now():%Y-%m-%d %H:%M}")
if st.button("상태 새로고침"):
    st.cache_data.clear()
    st.rerun()

ws, ps, rp = web_status(), pipe_status(), repos()
m = st.columns(4)
m[0].metric("실행 중인 로컬 앱", f"{sum(is_up(a['port']) for a in LOCAL)}/{len(LOCAL)}")
m[1].metric("공개 페이지 정상", f"{sum(v == 200 for v in ws.values())}/{len(WEB)}")
m[2].metric("자동 파이프라인 최근 성공", f"{sum(bool(r) and r.get('conclusion') == 'success' for r in ps.values())}/{len(PIPES)}")
m[3].metric("저장소", f"{len(rp)}개", f"정리할 변경 {sum(1 for r in rp if r['changed'] or r['untracked'])}곳", delta_color="off")

st.subheader("내 컴퓨터에서 돌리는 앱")
cols = st.columns(len(LOCAL))
for col, app in zip(cols, LOCAL):
    with col.container(border=True):
        up = is_up(app["port"])
        st.markdown(f"### {app['icon']} {app['name']}")
        st.caption(app["desc"])
        st.markdown(f"{'🟢 실행 중' if up else '⚪ 꺼짐'} · `localhost:{app['port']}`")
        if app["key"] == "trading":
            for t in trading_stats():
                st.caption("· " + t)
        if app["key"] == "earnings":
            soon = earnings_soon()
            if soon:
                st.caption("· 다가오는 어닝: " + ", ".join(soon))
        b = st.columns(2)
        if up:
            b[0].link_button("열기", f"http://localhost:{app['port']}", type="primary", width="stretch")
            if b[1].button("끄기", key=f"stop_{app['key']}", width="stretch"):
                stop(app)
                st.rerun()
        else:
            if b[0].button("켜기", key=f"start_{app['key']}", type="primary", width="stretch"):
                with st.spinner("켜는 중…"):
                    start(app)
                st.rerun()
        log = LOGS / f"{app['key']}.log"
        if log.exists():
            with st.expander("로그"):
                st.code("".join(log.read_text(errors="ignore").splitlines(keepends=True)[-15:]) or "(비어 있음)")

st.subheader("공개 웹 (GitHub Pages)")
cols = st.columns(len(WEB))
for col, (name, url, desc) in zip(cols, WEB):
    with col.container(border=True):
        code = ws.get(url)
        st.markdown(f"**{name}**  \n{'🟢' if code == 200 else '🔴'} {code or '응답 없음'}")
        st.caption(desc)
        st.link_button("열기", url, width="stretch")

st.subheader("자동 데이터 파이프라인 (GitHub Actions)")
cols = st.columns(len(PIPES))
for col, (wf, name, when) in zip(cols, PIPES):
    with col.container(border=True):
        r = ps.get(wf)
        if r:
            t = dt.datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00"))
            icon = {"success": "🟢", "failure": "🔴", "cancelled": "⚪"}.get(r.get("conclusion"), "🟡")
            st.markdown(f"**{name}**  \n{icon} {r.get('conclusion') or r.get('status')} · {ago(t)}")
            st.caption(when)
            st.link_button("실행 기록", r["url"], width="stretch")
        else:
            st.markdown(f"**{name}**  \n⚪ 기록 없음 (gh 로그인 필요할 수 있음)")
            st.caption(when)

st.subheader("iOS 앱")
pd_dir = ROOT / "PhotoDiary"
with st.container(border=True):
    c = st.columns([3, 1, 1])
    c[0].markdown("### 📔 PhotoDiary\n하루 최대 사진 3장 + 짧은 글, 친구와 함께 쓰는 공용 다이어리 (SwiftUI)")
    c[0].caption(f"{pd_dir} · ⚠️ portfolio 저장소 안에 커밋되지 않은 상태 — 별도 저장소로 분리하는 것을 권장")
    if c[1].button("Xcode로 열기", width="stretch", disabled=not (pd_dir / "PhotoDiary.xcodeproj").exists()):
        subprocess.run(["open", str(pd_dir / "PhotoDiary.xcodeproj")])
    if c[2].button("Finder에서 보기", width="stretch"):
        subprocess.run(["open", str(pd_dir)])

st.subheader("GitHub 저장소")
for r in rp:
    with st.container(border=True):
        c = st.columns([2, 2, 3, 1])
        c[0].markdown(f"**{r['name']}**  \n`{r['branch']}`")
        state = []
        if r["changed"]:
            state.append(f"수정 {r['changed']}개")
        if r["untracked"]:
            state.append(f"새 파일·폴더 {len(r['untracked'])}개: " + ", ".join(r["untracked"][:5]))
        c[1].markdown("✅ 깨끗함" if not state else "📝 " + " · ".join(state))
        c[2].caption(f"마지막 커밋 {ago(r['last']) if r['last'] else '-'} — {r['msg'][:70]}")
        if r["url"].startswith("http"):
            c[3].link_button("GitHub", r["url"], width="stretch")

old = [r["name"] for r in rp if r["last"] and (dt.datetime.now(dt.timezone.utc) - r["last"]).days > 365 * 2]
if old:
    st.info("2년 넘게 손대지 않은 저장소: " + ", ".join(old) + " — 쓰지 않는다면 GitHub에서 Archive 하면 목록이 깔끔해집니다.")
