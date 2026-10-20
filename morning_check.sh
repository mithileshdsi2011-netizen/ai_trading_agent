#!/bin/bash
# morning_check.sh — READ-ONLY daily health check for ai_trading_agent.
# Prints process/auth/position status and one OK / ACTION NEEDED verdict.
# Writes nothing. Safe to run any time.

cd "$(dirname "$0")"
DASH_PORT=5001
ISSUES=0

ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$1"; }
fail() { printf "  \033[31m✗\033[0m %s\n" "$1"; ISSUES=$((ISSUES+1)); }
hdr()  { printf "\n\033[1m%s\033[0m\n" "$1"; }

echo "════════════════════════════════════════════════"
echo " ai_trading_agent — Morning Health Check"
echo " $(date '+%Y-%m-%d %H:%M %Z')"
echo "════════════════════════════════════════════════"

# ── 1. Processes ────────────────────────────────────────────────
hdr "1. Processes"
DASH_PID=$(pgrep -f "python.*dashboard\.py" | head -1)
ORCH_PID=$(pgrep -f "trading_orchestrator\.py" | head -1)
MASTER_PID=$(pgrep -f "start_trading\.sh" | head -1)

[ -n "$DASH_PID" ]  && ok "Dashboard running (PID $DASH_PID)"    || fail "Dashboard NOT running — run ./start_trading.sh"
[ -n "$ORCH_PID" ]  && ok "Swing orchestrator running (PID $ORCH_PID)" || fail "Swing orchestrator NOT running — run ./start_trading.sh"
[ -n "$MASTER_PID" ] && ok "Master script + watchdogs active (PID $MASTER_PID)" || warn "Master script not running — no watchdog recovery"

DUP=$(pgrep -f "trading_orchestrator\.py" | wc -l | tr -d ' ')
[ "$DUP" -gt 1 ] && fail "$DUP duplicate orchestrators detected — investigate!" || true

# ── 2. Dashboard API + Kite ─────────────────────────────────────
hdr "2. Dashboard & Kite"
if [ -n "$DASH_PID" ]; then
  HEALTH=$(curl -s --max-time 5 "http://localhost:$DASH_PORT/api/health" 2>/dev/null)
fi

KITE_STATE=$(./venv/bin/python - <<'PYEOF' 2>/dev/null
import json, os, datetime as dt
try:
    d = json.load(open('data/kite_token.json'))
    exp = dt.datetime.fromisoformat(str(d['expiry']))
    now = dt.datetime.now()
    if exp > now:
        print(f"VALID|{exp:%Y-%m-%d %H:%M}")
    else:
        print(f"EXPIRED|{exp:%Y-%m-%d %H:%M}")
except Exception as e:
    print(f"MISSING|{e}")
PYEOF
)
case "$KITE_STATE" in
  VALID\|*)   ok "Kite token valid until ${KITE_STATE#VALID|}" ;;
  EXPIRED\|*) fail "Kite token EXPIRED (${KITE_STATE#EXPIRED|}) — click 'Re-authenticate Kite' on the dashboard" ;;
  *)          fail "Kite token unreadable: ${KITE_STATE#MISSING|}" ;;
esac

if [ -n "$HEALTH" ]; then
  echo "$HEALTH" | grep -q '"kite_ok":true' && ok "Kite API responding" || fail "kite_ok=false — Kite auth needed"
  echo "$HEALTH" | grep -q '"circuit_open":true' && fail "Circuit breaker OPEN — check drawdown state" || ok "Circuit breaker closed"
  SCAN=$(echo "$HEALTH" | ./venv/bin/python -c "import json,sys; print(json.load(sys.stdin).get('last_scan') or 'never')" 2>/dev/null)
  ok "Last Swing scan: $SCAN"
else
  [ -n "$DASH_PID" ] && fail "Dashboard up but /api/health unreachable" || warn "Skipped API checks (dashboard down)"
fi

# ── 3. Angel Intraday (PAPER) ────────────────────────────────────
hdr "3. Angel Intraday (PAPER)"
INTRADAY=$(curl -s --max-time 5 "http://localhost:$DASH_PORT/api/intraday-data" 2>/dev/null)
if [ -n "$INTRADAY" ]; then
  AUTH=$(echo "$INTRADAY" | ./venv/bin/python -c "import json,sys; print(json.load(sys.stdin).get('auth_status','?'))" 2>/dev/null)
  case "$AUTH" in
    CONNECTED) ok "Angel auth: CONNECTED (auto-TOTP)" ;;
    "AUTH PENDING") warn "Angel auth pending — resolves on first cycle" ;;
    "?") warn "Angel auth status unavailable" ;;
    *) fail "Angel auth: $AUTH" ;;
  esac
fi

if [ -f data/intraday/engine_status.json ]; then
  MD=$(./venv/bin/python -c "import json;print(json.load(open('data/intraday/engine_status.json')).get('market_data_status',''))" 2>/dev/null)
  KILL=$(./venv/bin/python -c "import json;print(json.load(open('data/intraday/engine_status.json')).get('kill_switch'))" 2>/dev/null)
  LAST_SCAN=$(./venv/bin/python -c "import json;print(json.load(open('data/intraday/engine_status.json')).get('last_scan_time','never'))" 2>/dev/null)
  [ "$KILL" = "True" ] && fail "Intraday KILL SWITCH is ON" || ok "Kill switch off"
  ok "Market data: ${MD:-unknown} • last scan: $LAST_SCAN"
else
  warn "No intraday engine status file yet"
fi

# ── 4. Positions & risk state ───────────────────────────────────
hdr "4. Positions & Risk"
SWING_N=$(./venv/bin/python -c "
import json
try:
    d = json.load(open('data/positions.json'))
    items = d.get('positions', []) if isinstance(d, dict) else d
    print(len(items) if isinstance(items, list) else len(items))
except Exception:
    print('0')" 2>/dev/null)
PAPER_N=$(./venv/bin/python -c "
import json
try:
    d = json.load(open('data/intraday/positions.json'))
    items = d.get('positions', []) if isinstance(d, dict) else d
    print(len(items) if isinstance(items, list) else len(items))
except Exception:
    print('0')" 2>/dev/null)
ok "Swing LIVE positions: ${SWING_N:-0} • Angel PAPER positions: ${PAPER_N:-0}"

if [ -f data/peak_value.json ]; then
  CB=$(./venv/bin/python -c "import json;d=json.load(open('data/peak_value.json'));print(d.get('circuit_breaker_fired'))" 2>/dev/null)
  [ "$CB" = "True" ] && fail "Circuit breaker FIRED today" || true
fi

# ── Verdict ─────────────────────────────────────────────────────
echo
echo "════════════════════════════════════════════════"
if [ "$ISSUES" -eq 0 ]; then
  printf " \033[32mVERDICT: OK — system fully automated, nothing needed\033[0m\n"
else
  printf " \033[31mVERDICT: ACTION NEEDED — %s issue(s) above\033[0m\n" "$ISSUES"
fi
echo "════════════════════════════════════════════════"
exit $ISSUES
