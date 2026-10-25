# 로컬 앱

| 앱 | 파일 | 포트 |
|---|---|---|
| This is not AI 허브 (모든 앱·사이트·자동 업데이트·저장소 한눈에) | `hub.py` | 8500 |
| 트레이딩 데스크 (단타 보조) | `trading_app.py` | 8501 |
| 어닝 대시보드 | `earnings_app.py` | 8502 |
| SI Forecast Desk (16개 종목 실시간 예측, `~/Desktop/stock-forecast-intraday`의 `start_live.sh`로 실행) | 별도 폴더 | 8765 |
| 포트폴리오 사이트 미리보기 | `site/` | 8766 |

시작: Spotlight·Launchpad에서 **This is not AI** 클릭 (Chrome 단독 창으로 열림). 또는 `apps/start.sh`, `Open Hub.command`.

허브는 파이썬 표준 라이브러리만 쓰는 작은 서버(`hub.py`)라 Streamlit 없이도 뜹니다. Mac 앱으로 켠 허브의 로그는 `~/Library/Logs/This is not AI.log`.

Mac 앱 다시 만들기 (경로를 옮겼거나 아이콘을 바꿨을 때): `.venv/bin/pip install pillow && .venv/bin/python apps/mac/build_app.py` → `/Applications/This is not AI.app` (예전 이름의 앱은 자동으로 지움)

- 매매 일지·알림 데이터는 `apps/journal/`에 저장되고 git에 올라가지 않습니다.
- 실시간 데이터: `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY` 환경변수가 있으면 Alpaca(무료 IEX 실시간), 없으면 Yahoo.
- 텔레그램 알림: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- Mac 앱으로 실행할 때 환경변수는 `~/.trading_desk.env`에 `export 이름=값`으로 적어 두면 전달됩니다.
