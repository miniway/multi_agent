# TODO

## Socket Mode 안정성
- [ ] 이벤트 중복 방지 — event_id 기반 dedup (같은 이벤트 이중 처리 방어)
- [ ] reconnect 로직 강화 — WebSocket 끊김 시 graceful reconnect
- [ ] HTTP mode 전환 검토 — Socket Mode 대신 Events API (HTTP endpoint 필요, 더 안정적)

## 봇-봇 통신
- [ ] bot_message + app_mention 동시 수신 시 이중 처리 방어
- [ ] 좀비 프로세스 감지 — 같은 토큰으로 중복 연결 시 이벤트 라우팅 문제

## Cron
- [ ] 신규 task 추가 시 재시작 없이 반영 (현재는 enabled/disabled만 hot-reload)
- [ ] cron 실행 이력/통계 로깅

## 리마인더
- [ ] macOS Automation 권한 문제 해결 (subprocess 체인에서 osascript → Reminders 권한 전파)

## 브라우저
- [ ] Playwright MCP에 쿠키 주입 방식 검토 (openclaw CDP 없이 paywall 통과)
- [ ] Chrome-Debug 프로세스 자동 시작/관리
