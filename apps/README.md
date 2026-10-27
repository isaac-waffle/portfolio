# 로컬 앱

| 앱 | 파일 | 포트 |
|---|---|---|
| This is not AI 허브 (모든 앱·사이트·자동 업데이트·저장소 한눈에) | `hub.py` | 8500 |
| 트레이딩 데스크 (단타 보조) | `trading_app.py` | 8501 |
| 어닝 대시보드 | `earnings_app.py` | 8502 |
| 가상 트레이더 (v6부터 네 명 모두 일봉 스윙: 아이작 모멘텀 · 티모 돌파 8종목 · 차차 돌파+시장 필터 · 돌쇠 돌파, 생각·매매 실시간) | `traders.py` + `traders.html` | 8503 |
| 가상 트레이더 백테스트·튜닝 (과거 5분봉으로 같은 판단 코드 재생) | `traders_lab.py` (명령줄) | - |
| 가상 트레이더 뉴스 해석 (Alpaca 실시간 뉴스 → 규칙·테마 지도로 종목별 호재/악재) | `traders_news.py` (테마 지도는 직접 수정 가능) | - |
| 스윙·추세형 백테스트 (일봉, 돌파·눌림목·모멘텀 3종을 관심종목·나스닥100으로 검증) | `swing_lab.py` (명령줄) | - |
| SI Forecast Desk (16개 종목 실시간 예측, `~/Desktop/stock-forecast-intraday`의 `start_live.sh`로 실행) | 별도 폴더 | 8765 |
| 포트폴리오 사이트 미리보기 | `site/` | 8766 |

시작: Spotlight·Launchpad에서 **This is not AI** 클릭 (Chrome 단독 창으로 열림). 또는 `apps/start.sh`, `Open Hub.command`.

허브는 파이썬 표준 라이브러리만 쓰는 작은 서버(`hub.py`)라 Streamlit 없이도 뜹니다. Mac 앱으로 켠 허브의 로그는 `~/Library/Logs/This is not AI.log`.

화면 테마 (공책): 원칙은 **1초에 다 읽히게** — 손글씨는 큰 제목에만, 숫자·라벨·표는 시스템 글꼴.
허브·가상 트레이더는 `apps/notebook.css`, 트레이딩 데스크·어닝 대시보드는 `apps/notebook_st.py` + `.streamlit/config.toml` 색, SI Forecast Desk는 `~/Desktop/stock-forecast-intraday/live/index.html` 안에 직접 (원래 어두운 화면은 `index.html.before-notebook`).

허브 아이콘(볼펜 낙서) 다시 그리기: `.venv/bin/python apps/mac/doodle.py hub` → `apps/mac/hub_icons/*.png` (그림은 `doodle.py`의 함수들)

Mac 앱 다시 만들기 (경로를 옮겼거나 아이콘을 바꿨을 때): `.venv/bin/pip install pillow && .venv/bin/python apps/mac/build_app.py` → `/Applications/This is not AI.app` (예전 이름의 앱은 자동으로 지움)

- 매매 일지·알림 데이터는 `apps/journal/`에 저장되고 git에 올라가지 않습니다.
- 가상 트레이더 기록은 `apps/journal/traders/` (`trades.csv` 거래와 진입 근거, `days.csv` 일별 결과, `events/` 그날의 모든 생각). 규칙을 바꾸면 `traders.py`의 `RULES` 버전을 올려 두면 '기록·개선' 화면에서 버전별 성과를 비교할 수 있습니다. 켜 두는 동안 맥이 잠들지 않습니다(caffeinate).
- Alpaca 키는 가상 트레이더 뉴스·백테스트에도 쓰입니다 (`~/.trading_desk.env`).
- 실시간 데이터: `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY` 환경변수가 있으면 Alpaca(무료 IEX 실시간), 없으면 Yahoo.
- 텔레그램 알림: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- Mac 앱으로 실행할 때 환경변수는 `~/.trading_desk.env`에 `export 이름=값`으로 적어 두면 전달됩니다.
