# 로컬 앱

| 앱 | 파일 | 포트 |
|---|---|---|
| 내 앱 허브 (모든 앱·사이트·파이프라인·저장소 한눈에) | `hub.py` | 8500 |
| 트레이딩 데스크 (단타 보조) | `trading_app.py` | 8501 |
| 어닝 대시보드 | `earnings_app.py` | 8502 |

시작: Spotlight·Launchpad에서 **내 앱 허브** 클릭 (Chrome 단독 창으로 열림). 또는 `apps/start.sh`, `Open Hub.command`.

Mac 앱 다시 만들기 (경로를 옮겼거나 아이콘을 바꿨을 때): `.venv/bin/pip install pillow && .venv/bin/python apps/mac/build_app.py` → `/Applications/내 앱 허브.app`

- 매매 일지·알림 데이터는 `apps/journal/`에 저장되고 git에 올라가지 않습니다.
- 실시간 데이터: `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY` 환경변수가 있으면 Alpaca(무료 IEX 실시간), 없으면 Yahoo.
- 텔레그램 알림: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- Mac 앱으로 실행할 때 환경변수는 `~/.trading_desk.env`에 `export 이름=값`으로 적어 두면 전달됩니다.
