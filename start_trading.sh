#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  AI TRADING AGENT — ONE-CLICK STARTER
#  Just run: ./start_trading.sh
#  This handles: Token login → Dashboard → Auto Trading
# ═══════════════════════════════════════════════════════════════════

set -e
cd "$(dirname "$0")"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  🤖 AI TRADING AGENT — Starting Up...${NC}"
echo -e "${CYAN}  $(date '+%A, %d %B %Y  %I:%M %p')${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
echo ""

# Activate virtual environment
if [ -f "../venv/bin/activate" ]; then
    source ../venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
else
    echo "Error: Virtual environment not found. Run: python3 -m venv venv"
    exit 1
fi

# Install flask if missing
venv/bin/pip install flask -q 2>/dev/null || true

# Kill any existing dashboard on port 5001
lsof -ti:5001 | xargs kill -9 2>/dev/null || true
sleep 1

# Add project root to PYTHONPATH so all modules resolve correctly
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

TOKEN_FILE="data/kite_token.json"
IP_FILE="data/last_known_ip.txt"

# ─── IP WHITELIST CHECK ────────────────────────────────────────────────────
echo -e "${YELLOW}━━━ IP Whitelist Check ━━━${NC}"
CURRENT_IP=$(curl -s -4 --max-time 8 https://api.ipify.org 2>/dev/null || echo "unknown")
echo -e "${GREEN}  ✓ Current Public IP: ${CURRENT_IP}${NC}"
# Save for reference only if fetch succeeded; preserve last known good IP otherwise
if [ "$CURRENT_IP" != "unknown" ]; then
    echo "$CURRENT_IP" > "$IP_FILE"
fi
if [ "$CURRENT_IP" != "unknown" ]; then
    echo -e "${CYAN}  → Make sure ${CURRENT_IP} is whitelisted at: https://developers.kite.trade/apps${NC}"
fi
echo ""

# ─── STEP 1: Kite Login & Token ────────────────────────────────────────────
echo -e "${YELLOW}━━━ STEP 1: Kite Connect Login ━━━${NC}"

# Check if token exists and is valid (compare full datetime, not just date)
TOKEN_VALID=false
if [ -f "$TOKEN_FILE" ]; then
    TOKEN_VALID=$(python -c "
import json, datetime
try:
    d = json.load(open('$TOKEN_FILE'))
    expiry_str = d.get('expiry', '')
    expiry = datetime.datetime.fromisoformat(expiry_str)
    if expiry > datetime.datetime.now():
        print('true')
    else:
        print('false')
except:
    print('false')
" 2>/dev/null || echo "false")
    if [ "$TOKEN_VALID" = "true" ]; then
        EXPIRY=$(python -c "import json; print(json.load(open('$TOKEN_FILE')).get('expiry',''))" 2>/dev/null)
        echo -e "${GREEN}  ✓ Valid token found (expires: $EXPIRY)${NC}"
    else
        echo -e "${RED}  ✗ Token expired. Need fresh login.${NC}"
        TOKEN_VALID=false
    fi
fi

if [ "$TOKEN_VALID" = false ]; then
    echo -e "${YELLOW}  → Opening Kite login in browser...${NC}"
    echo -e "${YELLOW}  → Please login to Zerodha when the browser opens.${NC}"
    echo ""

    # Start the token server in background
    venv/bin/python get_kite_token.py &
    TOKEN_PID=$!
    sleep 2

    # Open Kite login URL in browser
    API_KEY=$(grep KITE_API_KEY .env | cut -d'=' -f2)
    LOGIN_URL="https://kite.zerodha.com/connect/login?v=3&api_key=${API_KEY}"
    open "$LOGIN_URL" 2>/dev/null || xdg-open "$LOGIN_URL" 2>/dev/null

    echo -e "${YELLOW}  ⏳ Waiting for you to login...${NC}"

    # Wait for token file to be updated (max 120 seconds)
    WAITED=0
    while [ $WAITED -lt 120 ]; do
        sleep 3
        WAITED=$((WAITED + 3))
        if [ -f "$TOKEN_FILE" ]; then
            TOKEN_DATE=$(python -c "import json; f=open('$TOKEN_FILE'); d=json.load(f); print(d.get('expiry','')[:10])" 2>/dev/null || echo "")
            TODAY=$(date '+%Y-%m-%d')
            if [ "$TOKEN_DATE" == "$TODAY" ] || [[ "$TOKEN_DATE" > "$TODAY" ]]; then
                echo -e "${GREEN}  ✓ Token received! Login successful.${NC}"
                TOKEN_VALID=true
                break
            fi
        fi
        echo -ne "\r  ⏳ Waiting... ${WAITED}s "
    done

    # Kill the token server
    kill $TOKEN_PID 2>/dev/null || true
    wait $TOKEN_PID 2>/dev/null || true

    if [ "$TOKEN_VALID" = false ]; then
        echo -e "${RED}  ✗ Login timed out after 120s. Please try again.${NC}"
        exit 1
    fi
fi

echo ""

# ─── STEP 2: Dashboard (started via watchdog below) ──────────────────────
echo -e "${YELLOW}━━━ STEP 2: Dashboard ━━━${NC}"
echo -e "${GREEN}  ✓ Dashboard will run at http://localhost:5001 (auto-restart enabled)${NC}"
echo ""

# ─── STEP 3: Start Trading Bot ────────────────────────────────────────────
echo -e "${YELLOW}━━━ STEP 3: Starting Trading Bot ━━━${NC}"
echo -e "${GREEN}  ✓ Bot will trade every 15 minutes during market hours${NC}"
echo -e "${GREEN}  ✓ Auto-closes all positions at 2:55 PM${NC}"
echo -e "${GREEN}  ✓ Budget: ₹15,000 | Max Positions: 7 | Min Confidence: 60% | Universe: 150 stocks${NC}"
echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  🚀 SYSTEM LIVE — Trading will begin at 9:15 AM${NC}"
echo -e "${CYAN}  📊 Dashboard: http://localhost:5001${NC}"
echo -e "${CYAN}  🛑 Press Ctrl+C to stop everything${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
echo ""

# Ensure data directory exists (for position persistence)
mkdir -p data

# ─── Handle cleanup on exit ───────────────────────────────────────────────
cleanup() {
    echo ""
    echo -e "${YELLOW}  Shutting down...${NC}"
    # Kill watchdog loops by killing this whole process group
    kill -- -$$ 2>/dev/null || true
    kill $DASH_PID $BOT_PID 2>/dev/null || true
    echo -e "${GREEN}  ✓ Dashboard stopped${NC}"
    echo -e "${GREEN}  ✓ Trading bot stopped${NC}"
    echo -e "${GREEN}  All done. See you tomorrow! 👋${NC}"
    exit 0
}
trap cleanup SIGINT SIGTERM

# ─── Dashboard watchdog (auto-restart on crash) ───────────────────────────
dashboard_watchdog() {
    while true; do
        venv/bin/python dashboard.py &
        DASH_PID=$!
        set +e
        wait $DASH_PID
        EXIT_CODE=$?
        set -e
        if [ $EXIT_CODE -ne 0 ]; then
            echo -e "${RED}  ✗ Dashboard crashed (exit $EXIT_CODE). Restarting in 10s...${NC}"
            sleep 10
        else
            break   # clean exit means we're shutting down
        fi
    done
}

# ─── Bot watchdog (auto-restart on crash) ────────────────────────────────
bot_watchdog() {
    RESTART_COUNT=0
    while true; do
        venv/bin/python src/trading_orchestrator.py scheduled 15 &
        BOT_PID=$!
        set +e
        wait $BOT_PID
        EXIT_CODE=$?
        set -e
        if [ $EXIT_CODE -ne 0 ]; then
            RESTART_COUNT=$((RESTART_COUNT + 1))
            echo -e "${RED}  ✗ Bot crashed (exit $EXIT_CODE, restart #${RESTART_COUNT}). Restarting in 10s...${NC}"
            sleep 10
        else
            break   # clean exit
        fi
    done
}

# Start both watchdogs as background subshells
dashboard_watchdog &
DASH_WATCH_PID=$!

bot_watchdog &
BOT_WATCH_PID=$!

# Wait for either to exit (Ctrl+C triggers cleanup via trap)
wait
