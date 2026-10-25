"""'내 앱 허브'를 Mac 앱(.app)으로 만들어 /Applications에 설치 — Spotlight·Launchpad에서 눌러서 실행.

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

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
NAME = "내 앱 허브"
DEST = Path("/Applications") if os.access("/Applications", os.W_OK) else Path.home() / "Applications"
APP = DEST / f"{NAME}.app"
S = 4  # 슈퍼샘플링 배율

LAUNCHER = r'''#!/bin/zsh
ROOT="__ROOT__"
PORT=8500
URL="http://localhost:$PORT"
export PATH="__PATH__:$PATH"
[ -f "$HOME/.trading_desk.env" ] && source "$HOME/.trading_desk.env"
if ! nc -z 127.0.0.1 $PORT 2>/dev/null; then
  mkdir -p "$ROOT/apps/.hub/logs"
  if [ ! -x "$ROOT/.venv/bin/streamlit" ]; then
    python3 -m venv "$ROOT/.venv" && "$ROOT/.venv/bin/pip" install -q -r "$ROOT/apps/requirements.txt"
  fi
  cd "$ROOT"
  nohup "$ROOT/.venv/bin/streamlit" run apps/hub.py --server.port $PORT --server.headless true \
    --browser.gatherUsageStats false >> "$ROOT/apps/.hub/logs/hub.log" 2>&1 &!
  for i in {1..80}; do nc -z 127.0.0.1 $PORT 2>/dev/null && break; sleep 0.25; done
fi
if [ -d "/Applications/Google Chrome.app" ]; then
  open -na "Google Chrome" --args --app="$URL"
else
  open "$URL"
fi
'''


def rounded(draw, box, r, fill):
    draw.rounded_rectangle([int(v * S) for v in box], radius=int(r * S), fill=fill)


def icon(path: Path):
    W = 1024 * S
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    # 그림자 + 본체 (Apple 아이콘 그리드: 1024 캔버스에 824 둥근 사각형)
    sh = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([100 * S, 112 * S, 924 * S, 936 * S], radius=186 * S, fill=(0, 0, 0, 90))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(14 * S)))
    grad = Image.new("RGBA", (W, W))
    gd = ImageDraw.Draw(grad)
    top, bot = (37, 42, 74), (12, 15, 33)
    for y in range(W):
        t = y / W
        gd.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)) + (255,))
    mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mask).rounded_rectangle([100 * S, 100 * S, 924 * S, 924 * S], radius=186 * S, fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    tiles = [((232, 232), (255, 122, 89)), ((532, 232), (255, 196, 61)), ((232, 532), (64, 156, 255)), ((532, 532), (45, 212, 170))]
    for (x, y), col in tiles:
        rounded(d, (x, y, x + 260, y + 260), 64, col + (255,))
    w = lambda v: int(v * S)
    white, lw = (255, 255, 255, 255), w(22)
    # 차트 (트레이딩)
    x, y = 232, 232
    d.line([(w(x + 58), w(y + 186)), (w(x + 108), w(y + 128)), (w(x + 150), w(y + 160)), (w(x + 206), w(y + 80))],
           fill=white, width=lw, joint="curve")
    for px, py in ((x + 58, y + 186), (x + 206, y + 80)):
        d.ellipse([w(px - 15), w(py - 15), w(px + 15), w(py + 15)], fill=white)
    # 캘린더 (어닝)
    x, y = 532, 232
    d.rounded_rectangle([w(x + 60), w(y + 70), w(x + 200), w(y + 200)], radius=w(22), outline=white, width=lw)
    d.line([(w(x + 60), w(y + 112)), (w(x + 200), w(y + 112))], fill=white, width=lw)
    for cx in (x + 98, x + 162):
        d.line([(w(cx), w(y + 50)), (w(cx), w(y + 84))], fill=white, width=lw)
    d.ellipse([w(x + 112), w(y + 136), w(x + 148), w(y + 172)], fill=white)
    # 지구본 (웹)
    x, y = 232, 532
    cx, cy, r = x + 130, y + 130, 72
    d.ellipse([w(cx - r), w(cy - r), w(cx + r), w(cy + r)], outline=white, width=lw)
    d.ellipse([w(cx - 30), w(cy - r), w(cx + 30), w(cy + r)], outline=white, width=w(16))
    d.line([(w(cx - r), w(cy)), (w(cx + r), w(cy))], fill=white, width=w(16))
    # 다이어리 (iOS)
    x, y = 532, 532
    d.rounded_rectangle([w(x + 72), w(y + 56), w(x + 188), w(y + 204)], radius=w(20), outline=white, width=lw)
    d.polygon([(w(x + 140), w(y + 56)), (w(x + 172), w(y + 56)), (w(x + 172), w(y + 128)), (w(x + 156), w(y + 112)),
               (w(x + 140), w(y + 128))], fill=white)
    img.resize((1024, 1024), Image.LANCZOS).save(path)


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
    if APP.exists():
        shutil.rmtree(APP)
    (APP / "Contents" / "MacOS").mkdir(parents=True)
    (APP / "Contents" / "Resources").mkdir()
    png = ROOT / "apps" / "mac" / "AppIcon.png"
    icon(png)
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
