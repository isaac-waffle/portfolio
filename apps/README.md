# 로컬 앱

| 앱 | 파일 | 포트 |
|---|---|---|
| This is not AI 허브 (모든 앱·사이트·자동 업데이트·저장소 한눈에) | `hub.py` | 8500 |
| 주식 데스크 (주식 앱을 한 창에서 탭으로: 트레이딩 · 어닝 · 가상 트레이더 · 전설 5인 · 레이더 · 예측 · 손익(DEGIRO 거래 내역의 일·주·월 손익). 꺼진 앱은 탭을 누르면 켜짐) | `hub.py`의 `/stocks` 화면 | 8500/stocks |
| 트레이딩 데스크 (단타 보조) | `trading_app.py` | 8501 |
| 어닝 대시보드 | `earnings_app.py` | 8502 |
| 어닝 예측 기록·채점 (발표 전 예측 자동 저장 → 발표 후 적중률, 대시보드 '예측 적중률' 화면) | `earnings_track.py` (허브·대시보드가 백그라운드로 실행) | - |
| 가상 트레이더 (네 명 모두 일봉 스윙, v7부터 매수 규칙이 제각각: 아이작 모멘텀 순환 · 티모 RSI(2) 과매도 눌림목 · 차차 20일선 지지 반등+SPY 200일선 필터 · 돌쇠 20일 신고가 돌파, 생각·매매 실시간) | `traders.py` + `traders.html` | 8503 |
| 가상 트레이더 백테스트·튜닝 (과거 5분봉으로 같은 판단 코드 재생) | `traders_lab.py` (명령줄) | - |
| 가상 트레이더 뉴스 해석 (Alpaca 실시간 뉴스 → 규칙·테마 지도로 종목별 호재/악재) | `traders_news.py` (테마 지도는 직접 수정 가능) | - |
| 스윙·추세형 백테스트 (일봉, 돌파·눌림목·추세 눌림·모멘텀을 관심종목·나스닥100으로 검증, `long` 10년 돌쇠 · `trio` 네 명 규칙 비교) | `swing_lab.py` (명령줄) | - |
| 월가 전설 5인 (버핏·멍거·그레이엄·린치·드러켄밀러의 기준을 규칙으로 옮겨 관심종목 50개 + 직접 넣은 티커마다 사자/중립/팔자와 이유, Yahoo 재무·일봉, 6시간마다 갱신, Claude API 없음) | `legends.py` + `legends.html` | 8504 |
| 마켓 레이더 (히트맵 1일·1주·1달·3달·올해 · 레딧 인기 종목 · 이상 옵션 거래 · 내부자 매매. 화면은 사이트의 `site/demos/radar.html`을 공책 테마로 그대로 씀) | `radar.py` | 8505 |
| SI Forecast Desk (16개 종목 실시간 예측, `~/Desktop/stock-forecast-intraday`의 `start_live.sh`로 실행) | 별도 폴더 | 8765 |
| 포트폴리오 사이트 미리보기 | `site/` | 8766 |

시작: Spotlight·Launchpad에서 **This is not AI** 클릭 (Chrome 단독 창으로 열림). 또는 `apps/start.sh`, `Open Hub.command`.

허브는 파이썬 표준 라이브러리만 쓰는 작은 서버(`hub.py`)라 Streamlit 없이도 뜹니다. Mac 앱으로 켠 허브의 로그는 `~/Library/Logs/This is not AI.log`.

화면 테마 (공책): 원칙은 **1초에 다 읽히게** — 손글씨는 큰 제목에만, 숫자·라벨·표는 시스템 글꼴.
허브·가상 트레이더는 `apps/notebook.css`, 트레이딩 데스크·어닝 대시보드는 `apps/notebook_st.py` + `.streamlit/config.toml` 색, SI Forecast Desk는 `~/Desktop/stock-forecast-intraday/live/index.html` 안에 직접 (원래 어두운 화면은 `index.html.before-notebook`).

허브 아이콘(볼펜 낙서) 다시 그리기: `.venv/bin/python apps/mac/doodle.py hub` → `apps/mac/hub_icons/*.png` (그림은 `doodle.py`의 함수들)

Mac 앱 다시 만들기 (경로를 옮겼거나 아이콘을 바꿨을 때): `.venv/bin/pip install pillow && .venv/bin/python apps/mac/build_app.py` → `/Applications/This is not AI.app` (예전 이름의 앱은 자동으로 지움)

- 매매 일지·알림 데이터는 `apps/journal/`에 저장되고 git에 올라가지 않습니다.
- 가상 트레이더는 맥 서비스(launchd)로 돌립니다: `apps/traders_service.sh install` (한 번만) → 로그인하면 자동으로 켜지고, 죽으면 30초 안에 다시 뜨고, 하루 한 번(새벽 4시 ET) 스스로 새로 시작합니다. Claude·허브·터미널이 꺼져 있어도 돌고, 맥이 잠들면 멈춥니다(켜져 있는 동안은 caffeinate로 잠들기 방지). 허브의 끄기·켜기도 그대로 쓰면 되고, 로그는 `~/Library/Logs/traders.log`. 되돌리기: `apps/traders_service.sh uninstall`.
- 가상 트레이더 기록은 `apps/journal/traders/` (`trades.csv` 거래와 진입 근거, `days.csv` 일별 결과, `events/` 그날의 모든 생각). 규칙을 바꾸면 `traders.py`의 `RULES` 버전을 올려 두면 '기록·개선' 화면에서 버전별 성과를 비교할 수 있습니다. 켜 두는 동안 맥이 잠들지 않습니다(caffeinate).
- 어닝 예측 기록은 `apps/journal/earnings_preds.json`. 관심종목 발표 3일 전부터 20분마다 저장하고 발표 직전 값으로 고정, 반응일 장 마감 뒤 채점합니다. 기록 전 어닝은 `.venv/bin/python apps/earnings_track.py backfill 2026-10-12`로 재구성(Alpaca 과거 옵션 일봉).
- Alpaca 키는 가상 트레이더 뉴스·백테스트·어닝 재구성에도 쓰입니다 (`~/.trading_desk.env`).
- 실시간 데이터: `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY` 환경변수가 있으면 Alpaca(무료 IEX 실시간), 없으면 Yahoo.
- 텔레그램 알림: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- Mac 앱으로 실행할 때 환경변수는 `~/.trading_desk.env`에 `export 이름=값`으로 적어 두면 전달됩니다.
- 월가 전설 5인의 판정 기준은 `legends.py`의 `buffett()` … `druckenmiller()` 숫자를 고치면 바뀝니다. 직접 넣은 티커는 `apps/journal/legends_extra.json`, 받아 둔 데이터는 `apps/journal/legends_cache.json`.
- 주식 데스크에 탭을 추가하려면 `hub.py`의 `LOCAL`에서 그 앱에 `tab="탭 이름"`만 붙이면 됩니다. 각 앱은 그대로 따로 돌고, 주식 데스크는 그 화면을 한 창에 끼워 보여 줄 뿐입니다.
