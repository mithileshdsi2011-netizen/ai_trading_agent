#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  AI TRADING AGENT — AUTO-START WRAPPER (called by launchd)
#  Do NOT run manually. Use ./trade.sh for manual start.
# ═══════════════════════════════════════════════════════════════
PROJ="/Users/mithileshsinha/CascadeProjects/ai_trading_agent"
PYTHON="$PROJ/venv/bin/python3"
LOG="$PROJ/logs/autostart.log"
TOKEN="$PROJ/data/kite_token.json"

cd "$PROJ"
export PYTHONPATH="$PROJ:$PROJ/src"
mkdir -p "$PROJ/logs" "$PROJ/data"

echo "" >> "$LOG"
echo "════════════════════════════════════════" >> "$LOG"
echo "$(date '+%Y-%m-%d %H:%M:%S')  AUTO-START" >> "$LOG"
echo "════════════════════════════════════════" >> "$LOG"

# ── Prevent duplicate autostart instances ─────────────────────────
LOCKFILE="$PROJ/data/.autostart.lock"
if [ -f "$LOCKFILE" ]; then
    OLD_PID=$(cat "$LOCKFILE" 2>/dev/null)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "$(date '+%H:%M:%S')  Another autostart.sh already running (PID $OLD_PID). Exiting." >> "$LOG"
        exit 0
    fi
fi
echo $$ > "$LOCKFILE"

# ── Kill any stale processes ─────────────────────────────────
lsof -ti:5001 | xargs kill -9 2>/dev/null || true
lsof -ti:8080 | xargs kill -9 2>/dev/null || true
pkill -f "trading_orchestrator.py" 2>/dev/null || true
pkill -f "start_trading.sh" 2>/dev/null || true
pgrep -f "autostart.sh" | grep -v "^$$$" | xargs kill 2>/dev/null || true
sleep 1

# ── Check if Kite token is valid ─────────────────────────────
TOKEN_VALID=$("$PYTHON" -c "
import json, datetime, sys
try:
    d = json.load(open('$TOKEN'))
    e = datetime.datetime.fromisoformat(d.get('expiry',''))
    print('true' if e > datetime.datetime.now() else 'false')
except:
    print('false')
" 2>/dev/null || echo "false")

if [ "$TOKEN_VALID" != "true" ]; then
    echo "$(date '+%H:%M:%S')  TOKEN EXPIRED — starting login server" >> "$LOG"

    # Start token server in background
    "$PYTHON" "$PROJ/get_kite_token.py" >> "$LOG" 2>&1 &
    TOKEN_PID=$!
    sleep 2

    # Send macOS notification asking user to login
    osascript -e 'display notification "Kite token expired. Please login at http://localhost:8080 to start trading." with title "AI Trading Agent" sound name "Glass"' 2>/dev/null || true

    # Open browser to Kite login
    API_KEY=$(grep KITE_API_KEY "$PROJ/.env" | cut -d'=' -f2 | tr -d ' \r')
    open "https://kite.zerodha.com/connect/login?api_key=${API_KEY}&v=3" 2>/dev/null || true

    # Wait up to 5 minutes for token
    WAITED=0
    while [ $WAITED -lt 300 ]; do
        sleep 5; WAITED=$((WAITED+5))
        FRESH=$("$PYTHON" -c "
import json, datetime
try:
    d = json.load(open('$TOKEN'))
    e = datetime.datetime.fromisoformat(d.get('expiry',''))
    print('true' if e > datetime.datetime.now() else 'false')
except:
    print('false')
" 2>/dev/null || echo "false")
        if [ "$FRESH" = "true" ]; then
            echo "$(date '+%H:%M:%S')  Token received — continuing" >> "$LOG"
            TOKEN_VALID=true
            break
        fi
    done

    kill $TOKEN_PID 2>/dev/null || true

    if [ "$TOKEN_VALID" != "true" ]; then
        echo "$(date '+%H:%M:%S')  ERROR: Token not received in 5 min — aborting" >> "$LOG"
        osascript -e 'display notification "Trading NOT started — Kite login timed out." with title "AI Trading Agent ❌" sound name "Basso"' 2>/dev/null || true
        exit 1
    fi
fi

echo "$(date '+%H:%M:%S')  Token valid — starting services" >> "$LOG"

# ── Start dashboard ───────────────────────────────────────────
"$PYTHON" "$PROJ/dashboard.py" >> "$PROJ/logs/dashboard.log" 2>&1 &
DASH_PID=$!
echo $DASH_PID > "$PROJ/data/dash.pid"
sleep 2
echo "$(date '+%H:%M:%S')  Dashboard started (PID $DASH_PID)" >> "$LOG"

# ── Start trading bot ─────────────────────────────────────────
"$PYTHON" "$PROJ/src/trading_orchestrator.py" scheduled 15 >> "$PROJ/logs/trading.log" 2>&1 &
BOT_PID=$!
echo $BOT_PID > "$PROJ/data/bot.pid"
sleep 2

if kill -0 $BOT_PID 2>/dev/null; then
    echo "$(date '+%H:%M:%S')  Bot started (PID $BOT_PID)" >> "$LOG"
    osascript -e 'display notification "Dashboard: http://localhost:5001 | Logs: logs/trading.log" with title "✅ AI Trading Agent Started"' 2>/dev/null || true
else
    echo "$(date '+%H:%M:%S')  ERROR: Bot failed to start" >> "$LOG"
    osascript -e 'display notification "Bot failed to start. Check logs/autostart.log" with title "AI Trading Agent ❌" sound name "Basso"' 2>/dev/null || true
    exit 1
fi

echo "$(date '+%H:%M:%S')  All systems running" >> "$LOG"
wait
