#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  AI TRADING AGENT — DAILY TERMINAL SCRIPT
#  Usage: ./trade.sh
#  One command. Does everything automatically.
# ═══════════════════════════════════════════════════════════════
set -e
cd "$(dirname "$0")"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'

echo ""
echo -e "${CYAN}══════════════════════════════════════════════${NC}"
echo -e "${CYAN}   AI TRADING AGENT  |  $(date '+%d %b %Y  %I:%M %p')${NC}"
echo -e "${CYAN}══════════════════════════════════════════════${NC}"

# ── Activate virtualenv ──────────────────────────────────────
if   [ -f "venv/bin/activate" ];    then source venv/bin/activate
elif [ -f "../venv/bin/activate" ]; then source ../venv/bin/activate
else echo -e "${RED}ERROR: venv not found. Run: python3 -m venv venv && pip install -r requirements.txt${NC}"; exit 1
fi
export PYTHONPATH="$(pwd):$(pwd)/src"
mkdir -p data logs

# ── Kill stale processes ─────────────────────────────────────
lsof -ti:5001 | xargs kill -9 2>/dev/null || true
lsof -ti:8080 | xargs kill -9 2>/dev/null || true
pkill -f "trading_orchestrator.py" 2>/dev/null || true
sleep 1

# ── Check / refresh Kite token ───────────────────────────────
TOKEN_FILE="data/kite_token.json"
TOKEN_VALID=false

if [ -f "$TOKEN_FILE" ]; then
    TOKEN_VALID=$(python3 -c "
import json, datetime
try:
    d = json.load(open('$TOKEN_FILE'))
    e = datetime.datetime.fromisoformat(d.get('expiry',''))
    print('true' if e > datetime.datetime.now() else 'false')
except: print('false')
" 2>/dev/null || echo "false")
fi

if [ "$TOKEN_VALID" = "true" ]; then
    USER=$(python3 -c "import json; print(json.load(open('$TOKEN_FILE')).get('user_name',''))" 2>/dev/null)
    EXPIRY=$(python3 -c "import json; print(json.load(open('$TOKEN_FILE')).get('expiry','')[:16].replace('T',' '))" 2>/dev/null)
    echo -e "${GREEN}  ✓ Kite token valid  |  $USER  |  expires $EXPIRY${NC}"
else
    echo -e "${YELLOW}  ⚠  Token expired — opening Kite login in browser...${NC}"
    python3 get_kite_token.py &
    TOKEN_PID=$!
    sleep 2
    API_KEY=$(grep KITE_API_KEY .env | cut -d'=' -f2 | tr -d ' \r')
    open "https://kite.zerodha.com/connect/login?api_key=${API_KEY}&v=3" 2>/dev/null || true

    WAITED=0
    while [ $WAITED -lt 120 ]; do
        sleep 3; WAITED=$((WAITED+3))
        if [ -f "$TOKEN_FILE" ]; then
            FRESH=$(python3 -c "
import json, datetime
try:
    d = json.load(open('$TOKEN_FILE'))
    e = datetime.datetime.fromisoformat(d.get('expiry',''))
    print('true' if e > datetime.datetime.now() else 'false')
except: print('false')
" 2>/dev/null || echo "false")
            if [ "$FRESH" = "true" ]; then
                echo -e "${GREEN}  ✓ Login successful!${NC}"
                TOKEN_VALID=true
                break
            fi
        fi
        echo -ne "\r  ⏳ Waiting for login... ${WAITED}s "
    done
    kill $TOKEN_PID 2>/dev/null || true
    if [ "$TOKEN_VALID" != "true" ]; then
        echo -e "${RED}  ✗ Login timed out. Re-run ./trade.sh after logging in.${NC}"; exit 1
    fi
fi

# ── Show account info ─────────────────────────────────────────
echo ""
python3 -c "
import sys; sys.path.insert(0,'src')
import logging; logging.disable(logging.CRITICAL)
try:
    from token_manager import TokenManager
    kite = TokenManager().get_kite()
    f = kite.margins()
    cash = f.get('equity',{}).get('available',{}).get('live_balance',0)
    h = kite.holdings()
    p = kite.positions().get('net',[])
    invested = sum(x.get('average_price',0)*x.get('quantity',0) for x in p if x.get('quantity',0)!=0)
    invested += sum(x.get('average_price',0)*x.get('quantity',0) for x in h)
    print(f'  💰 Cash: ₹{cash:,.2f}  |  Invested: ₹{invested:,.2f}  |  Total: ₹{cash+invested:,.2f}')
    pos = [x for x in p if x.get('quantity',0)!=0]
    if pos:
        print(f'  📦 Open positions: {len(pos)}')
        for x in pos:
            print(f'     {x[\"tradingsymbol\"]:15s}  qty={x[\"quantity\"]}  avg=₹{x[\"average_price\"]:.2f}')
    else:
        print('  📦 No open positions')
except Exception as e:
    print(f'  ⚠  Could not fetch account: {e}')
" 2>/dev/null
echo ""

# ── Cleanup handler ───────────────────────────────────────────
cleanup() {
    echo ""
    echo -e "${YELLOW}  Shutting down...${NC}"
    kill $DASH_PID $BOT_PID 2>/dev/null || true
    echo -e "${GREEN}  ✓ Stopped. Logs saved to logs/trading.log${NC}"
    exit 0
}
trap cleanup SIGINT SIGTERM

# ── Start dashboard ───────────────────────────────────────────
python3 dashboard.py >> logs/dashboard.log 2>&1 &
DASH_PID=$!
sleep 2
echo -e "${GREEN}  ✓ Dashboard running  →  http://localhost:5001${NC}"

# ── Start trading bot ─────────────────────────────────────────
python3 src/trading_orchestrator.py scheduled 15 &
BOT_PID=$!
sleep 2

if kill -0 $BOT_PID 2>/dev/null; then
    echo -e "${GREEN}  ✓ Trading bot running  |  cycle every 15 min  |  9:15 AM – 3:00 PM IST${NC}"
else
    echo -e "${RED}  ✗ Bot failed to start. Check logs/trading.log${NC}"; exit 1
fi

echo ""
echo -e "${CYAN}══════════════════════════════════════════════${NC}"
echo -e "${CYAN}  🚀 LIVE  |  Dashboard: http://localhost:5001${NC}"
echo -e "${CYAN}  📋 Logs:  tail -f logs/trading.log${NC}"
echo -e "${CYAN}  🛑 Stop:  Ctrl+C${NC}"
echo -e "${CYAN}══════════════════════════════════════════════${NC}"
echo ""

wait
