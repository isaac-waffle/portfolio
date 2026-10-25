#!/bin/zsh
# This is not AI 허브 실행 (표준 라이브러리만 사용) — 브라우저에서 localhost:8500 을 엽니다.
cd "$(dirname "$0")/.."
PY=.venv/bin/python; [ -x "$PY" ] || PY=python3
if nc -z 127.0.0.1 8500 2>/dev/null; then open http://localhost:8500; else exec "$PY" apps/hub.py --open; fi
