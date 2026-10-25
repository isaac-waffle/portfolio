# 로컬 앱

| 앱 | 파일 | 포트 |
|---|---|---|
| 내 앱 허브 (모든 앱·사이트·파이프라인·저장소 한눈에) | `hub.py` | 8500 |
| 트레이딩 데스크 (단타 보조) | `trading_app.py` | 8501 |
| 어닝 대시보드 | `earnings_app.py` | 8502 |

시작: `apps/start.sh` 또는 Finder에서 `Open Hub.command` 더블클릭 → 허브에서 각 앱 켜기/끄기.

- 매매 일지·알림 데이터는 `apps/journal/`에 저장되고 git에 올라가지 않습니다.
- 실시간 데이터: `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY` 환경변수가 있으면 Alpaca(무료 IEX 실시간), 없으면 Yahoo.
- 텔레그램 알림: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
