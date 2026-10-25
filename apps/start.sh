#!/bin/zsh
# 내 앱 허브 실행: 필요하면 가상환경을 만들고, 브라우저에서 허브(localhost:8500)를 엽니다.
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/streamlit ]; then
  python3 -m venv .venv && .venv/bin/pip install -q -r apps/requirements.txt
fi
exec .venv/bin/streamlit run apps/hub.py --server.port 8500 --browser.gatherUsageStats false
