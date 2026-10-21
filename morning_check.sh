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

# Staleness checks only apply during market hours (Mon–Fri 09:15–15:30):
# a quiet overnight scheduler must never raise ACTION NEEDED.
DAY=$(date +%u); HM=$(date +%H%M)
IN_MARKET=0
[ "$DAY" -le 5 ] && [ "$HM" -ge 915 ] && [ "$HM" -le 1530 ] && IN_MARKET=1

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
  SCAN_AGE=$(echo "$HEALTH" | ./venv/bin/python -c "
import json,sys,datetime as dt
try:
    s=json.load(sys.stdin).get('last_scan')
    if not s: print(-1); raise SystemExit
    t=dt.datetime.fromisoformat(str(s))
    now=dt.datetime.now(t.tzinfo) if t.tzinfo else dt.datetime.now()
    print(int((now-t).total_seconds()))
except SystemExit: pass
except Exception: print(-1)" 2>/dev/null)
  if [ -n "$SCAN_AGE" ] && [ "$SCAN_AGE" -ge 0 ] 2>/dev/null; then
    if [ "$IN_MARKET" = "1" ] && [ "$SCAN_AGE" -gt 1200 ]; then
      fail "Swing scheduler NOT advancing — last scan $((SCAN_AGE/60))min ago (frozen?)"
    else
      ok "Scheduler advancing — last scan $((SCAN_AGE/60))min ago"
    fi
  else
    [ "$IN_MARKET" = "1" ] && warn "No Swing scan timestamp" || ok "Last Swing scan: none yet"
  fi
  # Reconcile thread writes reconciliation.log ~every 60s — its mtime is
  # the liveness probe that would have caught the frozen scheduler.
  if [ -f logs/reconciliation.log ]; then
    RECON_AGE=$(( $(date +%s) - $(stat -f %m logs/reconciliation.log 2>/dev/null || echo 0) ))
    if [ "$IN_MARKET" = "1" ] && [ "$RECON_AGE" -gt 300 ]; then
      fail "Broker reconciliation STALLED — last write $((RECON_AGE/60))min ago"
    else
      ok "Reconciliation fresh — last write $((RECON_AGE/60))min ago"
    fi
  fi
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
  ISTAT=$(./venv/bin/python - <<'PYEOF' 2>/dev/null
import json, datetime as dt
d = json.load(open('data/intraday/engine_status.json'))
s = d.get('last_scan_time')
age = -1
if s:
    try:
        t = dt.datetime.fromisoformat(str(s))
        now = dt.datetime.now(t.tzinfo) if t.tzinfo else dt.datetime.now()
        age = int((now - t).total_seconds())
    except Exception:
        pass
print(f"{d.get('market_data_status','')}|{d.get('kill_switch')}|{age}|{d.get('last_data_time') or ''}")
PYEOF
)
  MD=${ISTAT%%|*}; REST=${ISTAT#*|}; KILL=${REST%%|*}; REST=${REST#*|}; LSCAN_AGE=${REST%%|*}
  [ "$KILL" = "True" ] && fail "Intraday KILL SWITCH is ON" || ok "Kill switch off"
  case "$MD" in
    LIVE) ok "Market data: LIVE" ;;
    DISCONNECTED) warn "Market data DISCONNECTED (may be mid-cycle)" ;;
    *) [ -n "$MD" ] && ok "Market data: $MD" || warn "Market data status unknown" ;;
  esac
  # In-flight stuck scan (incident fires >600s; surface it here too)
  SCAN_S=$(echo "$INTRADAY" | ./venv/bin/python -c "import json,sys;d=json.load(sys.stdin);print(int(d.get('scan_seconds') or 0))" 2>/dev/null)
  if [ -n "$SCAN_S" ] && [ "$SCAN_S" -gt 900 ] 2>/dev/null; then
    warn "Angel scan running ${SCAN_S}s — possible stall (alert fires at 600s)"
  fi
  if [ -n "$LSCAN_AGE" ] && [ "$LSCAN_AGE" -ge 0 ] 2>/dev/null; then
    if [ "$IN_MARKET" = "1" ] && [ "$LSCAN_AGE" -gt 1200 ]; then
      warn "Angel last completed scan $((LSCAN_AGE/60))min ago — overdue"
    else
      ok "Last completed scan $((LSCAN_AGE/60))min ago"
    fi
  fi
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
