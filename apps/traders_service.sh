#!/bin/zsh
# 가상 트레이더를 맥 자체 서비스(launchd)로 — 로그인하면 자동으로 켜지고, 죽으면 다시 뜸. Claude·허브·터미널 없이 혼자 돔.
#   apps/traders_service.sh install    설치하고 지금 켜기 (허브로 켜 둔 게 있으면 먼저 끔)
#   apps/traders_service.sh uninstall  끄고 제거 (다시 허브에서 켜는 방식으로)
#   apps/traders_service.sh status     상태
# 맥이 잠들면 멈춤 — 켜져 있는 동안은 traders.py가 caffeinate로 잠들지 않게 막음. 뚜껑을 닫으면(전원 연결 없이) 잠듦.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.isaac-waffle.traders"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
LOG="$HOME/Library/Logs/traders.log"  # launchd는 ~/Documents에 직접 못 씀(macOS 개인정보 보호)

case "${1:-status}" in
install)
  mkdir -p "$(dirname "$PLIST")"
  # 허브가 띄운 트레이더가 있으면 정상 종료(SIGTERM → 저장 후 종료)
  for pid in $(lsof -ti tcp:8503 -sTCP:LISTEN 2>/dev/null); do kill -TERM "$pid" 2>/dev/null || true; done
  for i in {1..40}; do nc -z 127.0.0.1 8503 2>/dev/null || break; sleep 0.25; done
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$ROOT/.venv/bin/python</string><string>$ROOT/apps/traders.py</string></array>
  <key>WorkingDirectory</key><string>$HOME</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>SoftResourceLimits</key><dict><key>NumberOfFiles</key><integer>10240</integer></dict>
  <key>EnvironmentVariables</key><dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$PLIST"
  launchctl kickstart "$DOMAIN/$LABEL"
  for i in {1..120}; do nc -z 127.0.0.1 8503 2>/dev/null && break; sleep 0.5; done
  nc -z 127.0.0.1 8503 2>/dev/null && echo "설치 완료 — http://localhost:8503" || { echo "켜지지 않음 — 로그: $LOG"; exit 1; }
  ;;
uninstall)
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "제거 완료 — 이제 허브에서 켜고 끕니다"
  ;;
status)
  if [[ -f "$PLIST" ]]; then
    launchctl print "$DOMAIN/$LABEL" 2>/dev/null | grep -E "^\s*(state|pid|last exit code|runs) =" || echo "설치됨 · 실행 안 됨"
  else
    echo "설치 안 됨"
  fi
  nc -z 127.0.0.1 8503 2>/dev/null && echo "8503 응답 중" || echo "8503 꺼짐"
  ;;
*) echo "사용법: $0 install|uninstall|status"; exit 2 ;;
esac
