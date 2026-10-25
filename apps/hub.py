"""This is not AI — 내가 만든 앱·사이트·자동 업데이트·저장소를 한 화면에서 보고 켜고 끄기.

실행:  python3 apps/hub.py --open      (표준 라이브러리만 사용, localhost:8500)
Mac 앱: apps/mac/build_app.py 로 만든 /Applications/This is not AI.app
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # portfolio 저장소
GH_DIR = ROOT.parent  # ~/Documents/GitHub
APPS = ROOT / "apps"
LOGS = APPS / ".hub" / "logs"
PORT = 8500
REPO = "isaac-waffle/portfolio"
SITE = "https://isaac-waffle.github.io/portfolio/"
STREAMLIT = str(ROOT / ".venv" / "bin" / "streamlit")
PHOTODIARY = ROOT / "PhotoDiary"
FORECAST = Path.home() / "Desktop" / "stock-forecast-intraday"  # SI Forecast Desk (별도 폴더, 코드는 건드리지 않음)

LOCAL = [
    dict(key="trading", name="트레이딩 데스크", port=8501, icon="chart", color="#ff9500",
         cmd=[STREAMLIT, "run", "apps/trading_app.py"]),
    dict(key="earnings", name="어닝 대시보드", port=8502, icon="calendar", color="#ff3b30",
         cmd=[STREAMLIT, "run", "apps/earnings_app.py"]),
    dict(key="forecast", name="SI Forecast Desk", port=8765, icon="wave", color="#af52de",
         cmd=["zsh", "start_live.sh"], stop=["zsh", "stop_live.sh"], cwd=FORECAST, wait=120),
    dict(key="site", name="포트폴리오 미리보기", port=8766, icon="globe", color="#007aff",
         cmd=["python3", "-m", "http.server", "8766", "--directory", "site"]),
]
WEB = [("포트폴리오", SITE, "자기소개 · 프로젝트"), ("Forecast Lab", SITE + "lab.html", "16개 종목 단기 예측과 채점"),
       ("Earnings Desk", SITE + "demos/earnings.html", "어닝 캘린더 웹 버전"),
       ("DEGIRO P&L", SITE + "demos/pnl.html", "거래내역 손익 분석"), ("Woning Scout", SITE + "demos/housing.html", "네덜란드 집 찾기")]
PIPES = [("deploy.yml", "사이트 배포", "site/ 변경 시"), ("forecast.yml", "Forecast Lab 예측", "장중 15분마다"),
         ("earnings.yml", "Earnings Desk 데이터", "3시간마다")]


# ───────────────────────── 상태 수집 ─────────────────────────
def is_up(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def start_app(app: dict):
    LOGS.mkdir(parents=True, exist_ok=True)
    cmd = list(app["cmd"])
    if cmd[0] == STREAMLIT:
        cmd += ["--server.port", str(app["port"]), "--server.headless", "true",
                "--browser.gatherUsageStats", "false", "--client.toolbarMode", "minimal"]
    log = open(LOGS / f"{app['key']}.log", "a")
    subprocess.Popen(cmd, cwd=app.get("cwd", ROOT), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(app.get("wait", 60)):
        if is_up(app["port"]):
            return
        time.sleep(0.25)


def stop_app(app: dict):
    if app.get("stop"):  # 여러 프로세스를 띄우는 앱은 자체 종료 스크립트로
        subprocess.run(app["stop"], cwd=app["cwd"], capture_output=True, timeout=30)
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


def ago(t: dt.datetime | None) -> str:
    if not t:
        return ""
    s = (dt.datetime.now(dt.timezone.utc) - t).total_seconds()
    if s < 3600:
        return f"{max(int(s // 60), 1)}분 전"
    if s < 86400:
        return f"{int(s // 3600)}시간 전"
    if s < 86400 * 365:
        return f"{int(s // 86400)}일 전"
    return f"{int(s // (86400 * 365))}년 전"


def fetch_json(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "This-is-not-AI-hub"}), timeout=8) as r:
        return json.loads(r.read())


def web_one(item):
    name, url, desc = item
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers={"User-Agent": "This-is-not-AI-hub"}), timeout=6) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:
        code = None
    return dict(name=name, url=url, desc=desc, ok=code == 200, code=code)


def pipe_one(item):
    wf, name, when = item
    try:
        r = subprocess.run(["gh", "run", "list", "-R", REPO, "--workflow", wf, "-L", "1",
                            "--json", "status,conclusion,createdAt,url"], capture_output=True, text=True, timeout=20)
        run = (json.loads(r.stdout or "[]") or [None])[0]
    except Exception:
        run = None
    if not run:
        return dict(name=name, when=when, state="unknown", label="기록 없음", ago="", url=f"https://github.com/{REPO}/actions")
    t = dt.datetime.fromisoformat(run["createdAt"].replace("Z", "+00:00"))
    c = run.get("conclusion") or run.get("status")
    label = {"success": "성공", "failure": "실패", "cancelled": "취소됨", "in_progress": "실행 중", "queued": "대기 중"}.get(c, c)
    state = "ok" if c == "success" else "bad" if c == "failure" else "wait"
    return dict(name=name, when=when, state=state, label=label, ago=ago(t), url=run["url"])


def repo_one(d: Path):
    g = lambda *a: subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True).stdout.strip()
    lines = g("status", "--porcelain").splitlines()
    last = g("log", "-1", "--format=%ct|%s")
    ts, msg = (last.split("|", 1) + [""])[:2] if last else ("", "")
    t = dt.datetime.fromtimestamp(int(ts), dt.timezone.utc) if ts else None
    url = g("remote", "get-url", "origin").removesuffix(".git").replace("git@github.com:", "https://github.com/")
    changed, new = sum(not x.startswith("??") for x in lines), sum(x.startswith("??") for x in lines)
    state = "변경 없음" if not (changed or new) else " · ".join(filter(None, [f"수정 {changed}" if changed else "", f"새 항목 {new}" if new else ""]))
    return dict(name=d.name, branch=g("rev-parse", "--abbrev-ref", "HEAD"), state=state, dirty=bool(changed or new),
                ago=ago(t), stale=bool(t and (dt.datetime.now(dt.timezone.utc) - t).days > 730), msg=msg[:60],
                url=url if url.startswith("http") else "")


def earnings_soon() -> str:
    try:
        js = fetch_json("https://raw.githubusercontent.com/isaac-waffle/portfolio/earnings-data/calendar.json")
    except Exception:
        return ""
    today = dt.date.today().isoformat()
    w = sorted((x for x in js.get("watch", []) if (x.get("next") or "") >= today), key=lambda x: x["next"])
    return "다가오는 어닝 " + ", ".join(f"{x['sym']} {int(x['next'][5:7])}/{int(x['next'][8:])}" for x in w[:4]) if w else ""


def trading_info() -> str:
    parts = []
    j = APPS / "journal" / "trades.csv"
    n = 0
    if j.exists():
        with open(j, encoding="utf-8") as f:
            n = sum(1 for _ in csv.DictReader(f))
    parts.append(f"매매 일지 {n}건")
    a = APPS / "journal" / "alerts.json"
    try:
        k = sum(x.get("active", True) for x in json.loads(a.read_text(encoding="utf-8"))) if a.exists() else 0
    except json.JSONDecodeError:
        k = 0
    parts.append(f"알림 {k}개")
    return " · ".join(parts)


_cache = {"t": 0.0, "data": None, "earn": ""}
_lock = threading.Lock()


def remote(force: bool = False) -> dict:
    with _lock:
        if not force and _cache["data"] and time.time() - _cache["t"] < 300:
            return _cache["data"]
        dirs = [d for d in sorted(GH_DIR.iterdir()) if (d / ".git").exists()]
        with ThreadPoolExecutor(12) as ex:
            web = ex.map(web_one, WEB)
            pipes = ex.map(pipe_one, PIPES)
            repos = ex.map(repo_one, dirs)
            earn = ex.submit(earnings_soon)
            data = dict(web=list(web), pipes=list(pipes), repos=list(repos))
            _cache["earn"] = earn.result()
        _cache.update(t=time.time(), data=data)
        return data


def forecast_info() -> str:
    if not (FORECAST / "start_live.sh").exists():
        return f"폴더 없음: {FORECAST}"
    try:
        d = json.loads((FORECAST / "live" / "forecast_live.json").read_text())
        t = dt.datetime.fromisoformat(d["updated_utc"])
        return f"16개 종목 1분~1일 예측 · 마지막 예측 {ago(t)}"
    except Exception:
        return "16개 종목 1분~1일 예측 · 실시간 시세 · 옵션 · 뉴스"


def local() -> dict:
    sub = {"trading": trading_info(), "earnings": _cache["earn"] or "미국 어닝 캘린더 · 종목별 분석",
           "forecast": forecast_info(), "site": "site/ 폴더를 내 컴퓨터에서 미리 보기"}
    apps = [dict(key=a["key"], name=a["name"], port=a["port"], icon=a["icon"], color=a["color"],
                 up=is_up(a["port"]), sub=sub[a["key"]],
                 ok=a.get("cwd", ROOT).exists()) for a in LOCAL]
    return dict(apps=apps, photodiary=(PHOTODIARY / "PhotoDiary.xcodeproj").exists())


# ───────────────────────── 서버 ─────────────────────────
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
        if self.path == "/":
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif self.path == "/api/local":
            self._json(local())
        elif self.path.startswith("/api/remote"):
            self._json(remote(force="force=1" in self.path))
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        # 다른 웹사이트가 이 로컬 서버를 조작하지 못하도록 사용자 정의 헤더를 요구 (교차 출처 요청은 사전 확인에서 막힘)
        if self.headers.get("X-ARK") != "1":
            return self._send(403, b"forbidden", "text/plain")
        parts = self.path.strip("/").split("/")
        app = next((a for a in LOCAL if len(parts) == 3 and a["key"] == parts[2]), None)
        if parts[:2] == ["api", "start"] and app:
            start_app(app)
        elif parts[:2] == ["api", "stop"] and app:
            stop_app(app)
        elif self.path == "/api/xcode" and PHOTODIARY.exists():
            subprocess.run(["open", str(PHOTODIARY / "PhotoDiary.xcodeproj")])
        elif self.path == "/api/finder" and PHOTODIARY.exists():
            subprocess.run(["open", str(PHOTODIARY)])
        else:
            return self._send(404, b"not found", "text/plain")
        self._json(local())


PAGE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>This is not AI</title>
<style>
:root{
  --bg:#f5f5f7;--card:#fff;--text:#1d1d1f;--sub:#6e6e73;--line:rgba(0,0,0,.08);
  --accent:#0071e3;--pill:#e8e8ed;--green:#34c759;--red:#ff3b30;--orange:#ff9500;--gray:#c7c7cc;
  --shadow:0 1px 2px rgba(0,0,0,.04),0 4px 16px rgba(0,0,0,.04);
}
@media (prefers-color-scheme:dark){:root{
  --bg:#000;--card:#1c1c1e;--text:#f5f5f7;--sub:#98989d;--line:rgba(255,255,255,.1);
  --accent:#0a84ff;--pill:#2c2c2e;--green:#30d158;--red:#ff453a;--orange:#ff9f0a;--gray:#48484a;--shadow:none;
}}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--text);-webkit-font-smoothing:antialiased;
  font:15px/1.4 -apple-system,BlinkMacSystemFont,"SF Pro Text","Apple SD Gothic Neo","Helvetica Neue",sans-serif}
.wrap{max-width:1160px;margin:0 auto;padding:44px 32px 40px}
header{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:28px;flex-wrap:wrap}
h1{font:700 40px/1.05 -apple-system,BlinkMacSystemFont,"SF Pro Display",sans-serif;letter-spacing:-.02em}
.date{color:var(--sub);font-size:15px;margin-top:6px}
.summary{display:flex;align-items:center;gap:10px;background:var(--card);border-radius:14px;padding:10px 14px;
  box-shadow:var(--shadow);font-size:14px}
.summary b{font-weight:600}
.summary .muted{color:var(--sub)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:28px;align-items:start}
@media (max-width:900px){.grid{grid-template-columns:1fr}.wrap{padding:28px 16px}}
section+section{margin-top:26px}
h2{font-size:13px;font-weight:600;color:var(--sub);letter-spacing:.01em;margin:0 0 8px 16px}
.group{background:var(--card);border-radius:14px;box-shadow:var(--shadow);overflow:hidden}
.row{display:flex;align-items:center;gap:12px;padding:10px 14px 10px 14px;min-height:58px;position:relative}
.row+.row:before{content:"";position:absolute;top:0;left:58px;right:0;border-top:1px solid var(--line)}
.ic{width:32px;height:32px;border-radius:8px;flex:none;display:grid;place-items:center}
.ic svg{width:18px;height:18px;stroke:#fff;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.txt{flex:1;min-width:0}
.t{font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.s{color:var(--sub);font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.st{display:flex;align-items:center;gap:6px;color:var(--sub);font-size:13px;flex:none}
.dot{width:8px;height:8px;border-radius:50%;background:var(--gray);flex:none}
.dot.ok{background:var(--green)}.dot.bad{background:var(--red)}.dot.wait{background:var(--orange)}
.act{display:flex;gap:6px;flex:none}
.btn{appearance:none;border:0;border-radius:980px;padding:5px 14px;font:600 13px/1.4 inherit;cursor:pointer;
  background:var(--pill);color:var(--accent);text-decoration:none;white-space:nowrap}
.btn.primary{background:var(--accent);color:#fff}
.btn.quiet{background:none;color:var(--sub);font-weight:500;padding:5px 8px}
.btn:hover{filter:brightness(.96)}.btn:disabled{opacity:.5;cursor:default}
.loading{color:var(--sub);font-size:13px;padding:18px 16px}
.link{background:none;border:0;color:var(--accent);font:inherit;font-size:14px;cursor:pointer;padding:0}
footer{color:var(--sub);font-size:12px;margin-top:28px;text-align:center}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div><h1>This is not AI</h1><div class="date" id="date"></div></div>
    <div class="summary"><span class="dot" id="sumdot"></span><b id="sumtext">확인 중</b>
      <span class="muted" id="sumdetail"></span><button class="link" id="refresh">새로고침</button></div>
  </header>
  <div class="grid">
    <div>
      <section><h2>앱</h2><div class="group" id="apps"><div class="loading">불러오는 중</div></div></section>
      <section><h2>iOS</h2><div class="group" id="ios"></div></section>
      <section><h2>자동 업데이트</h2><div class="group" id="pipes"><div class="loading">불러오는 중</div></div></section>
    </div>
    <div>
      <section><h2>웹</h2><div class="group" id="web"><div class="loading">불러오는 중</div></div></section>
      <section><h2>저장소</h2><div class="group" id="repos"><div class="loading">불러오는 중</div></div></section>
    </div>
  </div>
  <footer id="foot"></footer>
</div>
<script>
const I = {
  chart:'<path d="M4 16l5-6 4 3 7-8"/><path d="M15 5h5v5"/>',
  calendar:'<rect x="4" y="5" width="16" height="15" rx="3"/><path d="M4 10h16M9 3v4M15 3v4"/>',
  globe:'<circle cx="12" cy="12" r="8"/><path d="M4 12h16M12 4c2.5 2.5 2.5 13.5 0 16M12 4c-2.5 2.5-2.5 13.5 0 16"/>',
  book:'<path d="M6 4h10a2 2 0 0 1 2 2v14H8a2 2 0 0 1-2-2z"/><path d="M6 18a2 2 0 0 1 2-2h10"/>',
  link:'<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  cycle:'<path d="M20 12a8 8 0 0 1-14 5.3M4 12a8 8 0 0 1 14-5.3"/><path d="M18 3v4h-4M6 21v-4h4"/>',
  wave:'<path d="M3 15l4-5 4 3 4-6"/><path d="M15 7l2.5 3.5L21 6" stroke-dasharray="2 2.5"/><path d="M3 20h18"/>',
  branch:'<circle cx="7" cy="6" r="2"/><circle cx="7" cy="18" r="2"/><circle cx="17" cy="8" r="2"/><path d="M7 8v8M17 10c0 4-10 2-10 6"/>'
};
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const icon = (k, c) => `<div class="ic" style="background:${c}"><svg viewBox="0 0 24 24">${I[k]}</svg></div>`;
const row = (ic, t, s, st, act) => `<div class="row">${ic}<div class="txt"><div class="t">${esc(t)}</div>` +
  `<div class="s">${esc(s)}</div></div>${st}<div class="act">${act}</div></div>`;
const status = (cls, label) => `<div class="st"><span class="dot ${cls}"></span>${esc(label)}</div>`;
const go = (url, label = '열기') => `<a class="btn" href="${esc(url)}" target="_blank" rel="noopener">${label}</a>`;
let L = null, R = null;

async function post(path, btn){
  if (btn){ btn.disabled = true; btn.textContent = '…'; }
  const r = await fetch(path, {method:'POST', headers:{'X-ARK':'1'}});
  if (r.ok && path.startsWith('/api/s')){ L = await r.json(); drawLocal(); }
}

function drawLocal(){
  document.getElementById('apps').innerHTML = L.apps.map(a => row(icon(a.icon, a.color), a.name, a.sub,
    status(a.up ? 'ok' : '', a.up ? '실행 중' : '꺼짐'),
    a.up ? go(`http://localhost:${a.port}`) + `<button class="btn quiet" onclick="post('/api/stop/${a.key}',this)">끄기</button>`
         : a.ok ? `<button class="btn" onclick="post('/api/start/${a.key}',this)">켜기</button>` : '')).join('');
  document.getElementById('ios').innerHTML = row(icon('book', '#34c759'), 'PhotoDiary', '사진 일기 · 공용 다이어리 (SwiftUI)',
    status('', '로컬'), L.photodiary ? `<button class="btn" onclick="post('/api/xcode')">Xcode</button>` +
    `<button class="btn quiet" onclick="post('/api/finder')">Finder</button>` : '');
  drawSummary();
}

function drawRemote(){
  document.getElementById('web').innerHTML = R.web.map(w => row(icon('link', '#5856d6'), w.name, w.desc,
    status(w.ok ? 'ok' : 'bad', w.ok ? '정상' : (w.code ? `오류 ${w.code}` : '응답 없음')), go(w.url))).join('');
  document.getElementById('pipes').innerHTML = R.pipes.map(p => row(icon('cycle', '#30b0c7'), p.name,
    [p.when, p.ago].filter(Boolean).join(' · '), status(p.state, p.label), go(p.url, '기록'))).join('');
  document.getElementById('repos').innerHTML = R.repos.map(r => row(icon('branch', '#8e8e93'), r.name,
    `${r.branch} · ${r.ago}${r.msg ? ' · ' + r.msg : ''}`, status(r.dirty ? 'wait' : '', r.state),
    r.url ? go(r.url, 'GitHub') : '')).join('');
  const stale = R.repos.filter(r => r.stale).map(r => r.name);
  document.getElementById('foot').textContent = stale.length ?
    `2년 넘게 변경 없는 저장소 ${stale.length}개 (${stale.join(', ')}) — 쓰지 않으면 GitHub에서 보관(Archive) 처리하면 목록이 깔끔해집니다.` : '';
  drawSummary();
}

function drawSummary(){
  const parts = [], dot = document.getElementById('sumdot'), txt = document.getElementById('sumtext');
  if (L) parts.push(`앱 ${L.apps.filter(a => a.up).length}/${L.apps.length} 실행 중`);
  if (!R){ document.getElementById('sumdetail').textContent = parts.join(' · '); return; }
  const bad = R.web.filter(w => !w.ok).length + R.pipes.filter(p => p.state === 'bad').length;
  parts.push(`웹 ${R.web.filter(w => w.ok).length}/${R.web.length}`, `자동 업데이트 ${R.pipes.filter(p => p.state === 'ok').length}/${R.pipes.length}`);
  dot.className = 'dot ' + (bad ? 'bad' : 'ok');
  txt.textContent = bad ? `확인 필요 ${bad}` : '모두 정상';
  document.getElementById('sumdetail').textContent = parts.join(' · ');
}

async function loadLocal(){ L = await (await fetch('/api/local')).json(); drawLocal(); }
async function loadRemote(force){
  R = await (await fetch('/api/remote' + (force ? '?force=1' : ''))).json(); drawRemote(); loadLocal();
}
function clock(){
  const d = new Date();
  document.getElementById('date').textContent =
    d.toLocaleDateString('ko-KR', {year:'numeric', month:'long', day:'numeric', weekday:'long'}) + ' · ' +
    d.toLocaleTimeString('ko-KR', {hour:'2-digit', minute:'2-digit', hour12:false});
}
document.getElementById('refresh').onclick = () => { document.getElementById('sumtext').textContent = '확인 중'; loadRemote(true); };
clock(); setInterval(clock, 30000);
loadLocal(); loadRemote(false);
setInterval(loadLocal, 5000); setInterval(() => loadRemote(false), 300000);
</script>
</body>
</html>
"""


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    if "--open" in sys.argv:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
