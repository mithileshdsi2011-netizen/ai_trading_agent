#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  AI TRADING AGENT — AUTO-STOP WRAPPER (called by launchd)
# ═══════════════════════════════════════════════════════════════
PROJ="/Users/mithileshsinha/CascadeProjects/ai_trading_agent"
LOG="$PROJ/logs/autostart.log"

echo "" >> "$LOG"
echo "$(date '+%Y-%m-%d %H:%M:%S')  AUTO-STOP (market closed)" >> "$LOG"

# Kill by saved PIDs first
for PID_FILE in "$PROJ/data/bot.pid" "$PROJ/data/dash.pid"; do
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        kill "$PID" 2>/dev/null && echo "$(date '+%H:%M:%S')  Killed PID $PID" >> "$LOG" || true
        rm -f "$PID_FILE"
    fi
done

# Fallback: kill by process name
pkill -f "trading_orchestrator.py" 2>/dev/null || true
lsof -ti:5001 | xargs kill -9 2>/dev/null || true

echo "$(date '+%H:%M:%S')  All services stopped" >> "$LOG"

osascript -e 'display notification "All positions monitored. See logs/trading.log for today'\''s activity." with title "📴 AI Trading Agent Stopped"' 2>/dev/null || true
