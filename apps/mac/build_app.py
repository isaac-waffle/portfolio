"""This is not AI 허브를 Mac 앱(.app)으로 만들어 /Applications에 설치 — Spotlight·Launchpad에서 눌러서 실행.

실행:  .venv/bin/python apps/mac/build_app.py        (Pillow 필요: .venv/bin/pip install pillow)

누르면: 허브 서버(localhost:8500)가 꺼져 있으면 백그라운드로 켜고, Chrome 앱 창(주소창 없는 단독 창)으로 엽니다.
API 키 등은 ~/.trading_desk.env 에 `export ALPACA_KEY_ID=...` 형태로 두면 허브와 그 안에서 켜는 앱에 전달됩니다.
"""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from doodle import render as draw_icon  # 줄 노트 위 볼펜 낙서 아이콘

ROOT = Path(__file__).resolve().parents[2]
NAME = "This is not AI"
LEGACY = ["내 앱 허브", "Isaac_ARK"]  # 예전 이름으로 설치된 앱은 지움
DEST = Path("/Applications") if os.access("/Applications", os.W_OK) else Path.home() / "Applications"
APP = DEST / f"{NAME}.app"
ICON_SUBJECT = "robot"  # doodle.py 의 SUBJECTS 중 하나

LAUNCHER = r'''#!/bin/zsh
ROOT="__ROOT__"
PORT=8500
URL="http://localhost:$PORT"
export PATH="__PATH__:$PATH"
[ -f "$HOME/.trading_desk.env" ] && source "$HOME/.trading_desk.env"
if ! nc -z 127.0.0.1 $PORT 2>/dev/null; then
  LOG="$HOME/Library/Logs/This is not AI.log"
  PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY=python3
  cd "$ROOT"
  nohup "$PY" apps/hub.py >> "$LOG" 2>&1 &!
  for i in {1..80}; do nc -z 127.0.0.1 $PORT 2>/dev/null && break; sleep 0.25; done
fi
if [ -d "/Applications/Google Chrome.app" ]; then
  open -na "Google Chrome" --args --app="$URL"
else
  open "$URL"
fi
'''


def icns(png: Path, out: Path):
    with tempfile.TemporaryDirectory() as t:
        iset = Path(t) / "AppIcon.iconset"
        iset.mkdir()
        for s in (16, 32, 128, 256, 512):
            for k in (1, 2):
                subprocess.run(["sips", "-z", str(s * k), str(s * k), str(png), "--out",
                                str(iset / f"icon_{s}x{s}{'@2x' if k == 2 else ''}.png")], check=True, capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", str(iset), "-o", str(out)], check=True)


def build():
    for old in [APP] + [d / f"{n}.app" for n in LEGACY for d in (Path("/Applications"), Path.home() / "Applications")]:
        if old.exists():
            shutil.rmtree(old)
    (APP / "Contents" / "MacOS").mkdir(parents=True)
    (APP / "Contents" / "Resources").mkdir()
    png = ROOT / "apps" / "mac" / "AppIcon.png"
    draw_icon(ICON_SUBJECT, str(png))
    icns(png, APP / "Contents" / "Resources" / "AppIcon.icns")
    exe = APP / "Contents" / "MacOS" / "launcher"
    exe.write_text(LAUNCHER.replace("__ROOT__", str(ROOT)).replace("__PATH__", os.environ.get("PATH", "")))
    exe.chmod(0o755)
    with open(APP / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({
            "CFBundleName": NAME, "CFBundleDisplayName": NAME, "CFBundleIdentifier": "com.isaac.apphub",
            "CFBundleExecutable": "launcher", "CFBundleIconFile": "AppIcon", "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "1.0", "CFBundleVersion": "1", "LSMinimumSystemVersion": "13.0",
            "LSUIElement": True, "LSApplicationCategoryType": "public.app-category.productivity",
        }, f)
    subprocess.run(["codesign", "--force", "--deep", "-s", "-", str(APP)], check=True, capture_output=True)
    lsreg = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
    subprocess.run([lsreg, "-f", str(APP)], check=False)
    subprocess.run(["mdimport", str(APP)], check=False, capture_output=True)
    print(f"설치됨: {APP}")


if __name__ == "__main__":
    sys.exit(build())
