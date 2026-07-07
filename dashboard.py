"""
Personal Trading Dashboard
Run: ./run_with_venv.sh dashboard.py
Open: http://localhost:5001
"""
import os, sys, json
from datetime import datetime, timedelta
from flask import Flask, render_template_string, jsonify
import pytz

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
sys.path.insert(0, os.path.dirname(__file__))

app = Flask(__name__)
IST = pytz.timezone("Asia/Kolkata")

# Dashboard signal cache: avoid rescanning 30 stocks on every 60-second UI refresh
_SIGNAL_CACHE = {"signals": [], "recommendations": [], "stocks_scanned": 0, "timestamp": None}
_SIGNAL_CACHE_TTL = timedelta(minutes=5)

SECTOR_MAP = {
    "RELIANCE": "Energy/Oil",
    "TCS": "IT",
    "INFY": "IT",
    "WIPRO": "IT",
    "HDFCBANK": "Banking",
    "ICICIBANK": "Banking",
    "KOTAKBANK": "Banking",
    "AXISBANK": "Banking",
    "SBIN": "Banking",
    "BANKBARODA": "Banking",
    "INDUSINDBK": "Banking",
    "BANDHANBNK": "Banking",
    "BAJFINANCE": "Finance",
    "BAJAJFINSV": "Finance",
    "HDFCLIFE": "Insurance",
    "ICICIPRULI": "Insurance",
    "NIACL": "Insurance",
    "LICHSGFIN": "Finance",
    "HINDUNILVR": "FMCG",
    "ITC": "FMCG",
    "NESTLEIND": "FMCG",
    "BRITANNIA": "FMCG",
    "DABUR": "FMCG",
    "MARUTI": "Auto",
    "TATAMOTORS": "Auto",
    "M&M": "Auto",
    "ASHOKLEY": "Auto",
    "BAJAJ-AUTO": "Auto",
    "EICHERMOT": "Auto",
    "HEROMOTOCO": "Auto",
    "SUNPHARMA": "Pharma",
    "DRREDDY": "Pharma",
    "CIPLA": "Pharma",
    "AUROPHARMA": "Pharma",
    "DIVISLAB": "Pharma",
    "ASIANPAINT": "Paints",
    "BERGEPAINT": "Paints",
    "TITAN": "Consumer",
    "LT": "Infrastructure",
    "POWERGRID": "Infrastructure",
    "NTPC": "Power",
    "ADANIGREEN": "Power",
    "TATAPOWER": "Power",
    "BHARTIARTL": "Telecom",
    "IDEA": "Telecom",
    "BHEL": "Capital Goods",
    "SIEMENS": "Capital Goods",
    "ABB": "Capital Goods",
    "CUMMINSIND": "Capital Goods",
    "IRFC": "Finance",
    "PNB": "Banking",
    "BSE": "Exchange",
    "CANBK": "Banking",
    "IOB": "Banking",
    "UCOBANK": "Banking",
    "CENTRALBK": "Banking",
}

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Swing Trading Bot</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0f1e;color:#e2e8f0;font-family:'Inter',system-ui,sans-serif;min-height:100vh}
.card{background:#111827;border-radius:12px;padding:18px;border:1px solid #1f2937}
.card-sm{background:#0f1724;border-radius:10px;padding:14px;border:1px solid #1f2937}
.green{color:#22c55e}.red{color:#ef4444}.yellow{color:#eab308}.blue{color:#60a5fa}.purple{color:#a78bfa}
.bg-green{background:#16a34a22;border:1px solid #22c55e44}
.bg-red{background:#dc262622;border:1px solid #ef444444}
.bg-yellow{background:#ca8a0422;border:1px solid #eab30844}
.badge{display:inline-block;padding:2px 10px;border-radius:99px;font-size:11px;font-weight:700}
.badge-buy{background:#16a34a33;color:#22c55e;border:1px solid #22c55e55}
.badge-sell{background:#dc262633;color:#ef4444;border:1px solid #ef444455}
.badge-hold{background:#ca8a0433;color:#eab308;border:1px solid #eab30855}
th{color:#4b5563;font-size:10px;text-transform:uppercase;letter-spacing:.08em;padding:8px 10px;border-bottom:1px solid #1f2937}
td{padding:9px 10px;border-bottom:1px solid #111827;font-size:13px;color:#d1d5db}
tr:hover td{background:#0f1724}
tr:last-child td{border:none}
.stat-label{color:#9ca3af;font-size:12px;font-weight:600;margin-bottom:4px;text-transform:uppercase;letter-spacing:.06em}
.stat-value{font-size:22px;font-weight:700;line-height:1.1}
.stat-value-sm{font-size:16px;font-weight:700}
.tab-btn{padding:8px 18px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;border:none;transition:all .2s;color:#6b7280;background:transparent}
.tab-btn.active{background:#1d4ed8;color:#fff}
.tab-btn:hover:not(.active){background:#1f2937;color:#e2e8f0}
.tab-content{display:none}
.tab-content.active{display:block}
.progress-bar{height:6px;background:#1f2937;border-radius:3px;overflow:hidden}
.progress-fill{height:100%;border-radius:3px;transition:width .5s}
.heatmap-item{padding:10px 14px;border-radius:8px;font-size:13px;font-weight:600;text-align:center;cursor:default}
.pulse{animation:pulse 2s infinite}
.spin{animation:spin 2s linear infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
@keyframes spin{to{transform:rotate(360deg)}}
.notif-item{padding:10px 0;border-bottom:1px solid #1f2937;display:flex;align-items:center;gap:10px;font-size:13px}
.notif-item:last-child{border:none}
.score-bar{display:inline-block;width:40px;height:6px;border-radius:3px;vertical-align:middle;margin-left:6px}
::-webkit-scrollbar{width:4px;height:4px}
::-webkit-scrollbar-track{background:#0a0f1e}
::-webkit-scrollbar-thumb{background:#1f2937;border-radius:2px}
.chat-wrap{display:flex;flex-direction:column;height:calc(100vh - 200px);min-height:500px}
.chat-msgs{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:14px}
.msg-user{align-self:flex-end;background:#1d4ed8;color:#fff;padding:10px 16px;border-radius:16px 16px 4px 16px;max-width:72%;font-size:14px;line-height:1.5}
.msg-ai{align-self:flex-start;background:#111827;border:1px solid #1f2937;color:#e2e8f0;padding:14px 18px;border-radius:4px 16px 16px 16px;max-width:82%;font-size:14px;line-height:1.6}
.msg-ai .ai-label{font-size:11px;color:#4b5563;margin-bottom:6px;text-transform:uppercase;letter-spacing:.06em}
.chip-row{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}
.chip{padding:6px 14px;border-radius:99px;border:1px solid #374151;color:#9ca3af;font-size:12px;cursor:pointer;background:#111827;transition:all .2s}
.chip:hover{background:#1d4ed8;border-color:#1d4ed8;color:#fff}
.chat-input-row{display:flex;gap:10px;padding:14px;border-top:1px solid #1f2937;background:#0a0f1e}
.chat-input{flex:1;background:#111827;border:1px solid #374151;color:#e2e8f0;border-radius:10px;padding:10px 14px;font-size:14px;outline:none;transition:border .2s}
.chat-input:focus{border-color:#1d4ed8}
.chat-send{background:#1d4ed8;color:#fff;border:none;border-radius:10px;padding:10px 20px;font-size:14px;font-weight:600;cursor:pointer;transition:background .2s}
.chat-send:hover{background:#2563eb}.chat-send:disabled{background:#374151;cursor:not-allowed}
.ai-section{margin-bottom:8px}
.ai-section-title{font-size:11px;font-weight:700;color:#60a5fa;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px}
.ai-pill{display:inline-block;padding:3px 10px;border-radius:99px;font-size:12px;font-weight:600;margin:2px 3px}
.ai-pill-green{background:#16a34a22;color:#22c55e;border:1px solid #22c55e44}
.ai-pill-red{background:#dc262622;color:#ef4444;border:1px solid #ef444444}
.ai-pill-blue{background:#1d4ed822;color:#60a5fa;border:1px solid #1d4ed844}
.ai-pill-yellow{background:#ca8a0422;color:#eab308;border:1px solid #eab30844}
.ai-pill-gray{background:#1f293766;color:#9ca3af;border:1px solid #37415144}
.typing-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#60a5fa;animation:blink 1.2s infinite}
.typing-dot:nth-child(2){animation-delay:.2s}.typing-dot:nth-child(3){animation-delay:.4s}
@keyframes blink{0%,80%,100%{opacity:.2}40%{opacity:1}}
</style>
</head>
<body>

<!-- HEADER -->
<div style="background:#111827;border-bottom:1px solid #1f2937;padding:12px 20px" class="flex items-center justify-between flex-wrap gap-3">
  <div class="flex items-center gap-3">
    <span style="font-size:22px">🤖</span>
    <div>
      <div style="font-size:16px;font-weight:700;color:#f9fafb">AI Swing Trading Bot</div>
      <div style="font-size:11px;color:#4b5563" id="last-updated">Initializing...</div>
    </div>
  </div>
  <div class="flex items-center gap-4 flex-wrap">
    <div class="text-center">
      <div class="stat-label">Market</div>
      <div id="hdr-market" style="font-size:13px;font-weight:700">—</div>
    </div>
    <div class="text-center">
      <div class="stat-label">Mode</div>
      <div id="hdr-mode" style="font-size:13px;font-weight:700;color:#60a5fa">—</div>
    </div>
    <div class="text-center">
      <div class="stat-label">Last Scan</div>
      <div id="hdr-last-scan" style="font-size:13px;font-weight:600">—</div>
    </div>
    <div class="text-center">
      <div class="stat-label">Next Scan</div>
      <div id="hdr-next-scan" style="font-size:13px;font-weight:600">—</div>
    </div>
    <div class="text-center">
      <div class="stat-label">Bot Status</div>
      <div id="hdr-status" style="font-size:13px;font-weight:700">—</div>
    </div>
    <div class="text-center">
      <div class="stat-label">Token</div>
      <div id="hdr-token" style="font-size:11px;color:#6b7280">—</div>
    </div>
  </div>
</div>

<!-- TAB NAV -->
<div style="background:#111827;border-bottom:1px solid #1f2937;padding:6px 16px" class="flex gap-2">
  <button class="tab-btn active" onclick="switchTab('dashboard',this)">🏠 Dashboard</button>
  <button class="tab-btn" onclick="switchTab('portfolio',this)">📈 Portfolio</button>
  <button class="tab-btn" onclick="switchTab('positions',this)">📋 Positions</button>
  <button class="tab-btn" onclick="switchTab('history',this)">🕒 History</button>
  <button class="tab-btn" onclick="switchTab('signals',this)">🤖 AI Signals</button>
  <button class="tab-btn" onclick="switchTab('analytics',this)">📊 Analytics</button>
  <button class="tab-btn" onclick="switchTab('journal',this)">📓 Trade Journal</button>
  <button class="tab-btn" onclick="switchTab('askai',this)">💬 Ask AI</button>
  <button class="tab-btn" onclick="switchTab('botstatus',this)">⚙️ Bot Status</button>
</div>

<div style="padding:16px 20px;max-width:1800px;margin:0 auto">

<!-- ===== TAB: DASHBOARD ===== -->
<div id="tab-dashboard" class="tab-content active">

  <!-- Row 1: Key Metrics -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card">
      <div class="stat-label">Portfolio Value</div>
      <div class="stat-value" id="d-portfolio-value">₹—</div>
      <div style="font-size:12px;margin-top:4px" id="d-portfolio-return">—</div>
    </div>
    <div class="card">
      <div class="stat-label">Today's P&amp;L</div>
      <div class="stat-value" id="d-daily-pnl">₹—</div>
      <div style="font-size:12px;margin-top:4px" id="d-daily-pnl-pct">—</div>
    </div>
    <div class="card">
      <div class="stat-label">Available Cash</div>
      <div class="stat-value green" id="d-cash">₹—</div>
      <div style="font-size:12px;margin-top:4px;color:#4b5563" id="d-cash-pct">— of budget</div>
    </div>
    <div class="card">
      <div class="stat-label">Open Positions</div>
      <div class="stat-value yellow" id="d-open-pos">—</div>
      <div style="font-size:12px;margin-top:4px;color:#4b5563" id="d-pos-detail">— / 3 max</div>
    </div>
  </div>

  <!-- Row 2: Risk Monitor -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">⚡ Risk Monitor</div>
    <div class="grid grid-cols-2 md:grid-cols-5 gap-4">
      <div class="card-sm"><div class="stat-label">Exposure</div><div class="stat-value-sm yellow" id="d-exposure">₹—</div></div>
      <div class="card-sm"><div class="stat-label">Risk (SL)</div><div class="stat-value-sm red" id="d-risk">₹—</div></div>
      <div class="card-sm"><div class="stat-label">Reward (Tgt)</div><div class="stat-value-sm green" id="d-reward">₹—</div></div>
      <div class="card-sm"><div class="stat-label">Risk : Reward</div><div class="stat-value-sm" id="d-rr">—</div></div>
      <div class="card-sm"><div class="stat-label">Drawdown</div><div class="stat-value-sm" id="d-drawdown">—</div></div>
    </div>
  </div>

  <!-- Row 3: Position Heatmap -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🌡️ Position Heatmap</div>
    <div id="d-heatmap" class="flex flex-wrap gap-3">
      <div style="color:#4b5563;font-size:13px">No open positions</div>
    </div>
  </div>

  <!-- Row 4: Open Positions Table -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">
      📈 Open Positions <span class="pulse green" style="font-size:11px">● LIVE</span>
    </div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse">
      <thead><tr>
        <th style="text-align:left">Symbol</th><th>Qty</th><th>Avg</th><th>CMP</th>
        <th>P&amp;L</th><th>Days</th><th>Trail SL</th><th>Target</th><th>AI %</th>
      </tr></thead>
      <tbody id="d-positions"><tr><td colspan="9" style="text-align:center;color:#4b5563;padding:20px">No open positions</td></tr></tbody>
    </table>
    </div>
  </div>

  <!-- Row 5+6: AI Opportunities + Market -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">

    <!-- AI Opportunities -->
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🤖 Today's Best Opportunities</div>
      <div style="overflow-x:auto">
      <table style="width:100%;border-collapse:collapse">
        <thead><tr>
          <th style="text-align:left">Stock</th><th>Score</th><th>Trend</th>
          <th>Entry</th><th>Target</th><th>Risk</th>
        </tr></thead>
        <tbody id="d-opportunities"><tr><td colspan="6" style="text-align:center;color:#4b5563;padding:20px">Scanning...</td></tr></tbody>
      </table>
      </div>
    </div>

    <!-- Market Overview -->
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🌍 Market Overview</div>
      <div class="grid grid-cols-2 gap-3 mb-4">
        <div class="card-sm"><div class="stat-label">NIFTY 50</div><div class="stat-value-sm" id="d-nifty">—</div></div>
        <div class="card-sm"><div class="stat-label">BANKNIFTY</div><div class="stat-value-sm" id="d-banknifty">—</div></div>
        <div class="card-sm"><div class="stat-label">VIX</div><div class="stat-value-sm" id="d-vix">—</div>
          <div style="font-size:11px;margin-top:2px" id="d-vix-label">—</div>
        </div>
        <div class="card-sm"><div class="stat-label">Market Regime</div><div class="stat-value-sm" id="d-regime">—</div></div>
      </div>
      <div>
        <div class="stat-label mb-2">Sector Strength</div>
        <div id="d-sectors" class="flex flex-wrap gap-2"></div>
      </div>
    </div>
  </div>

  <!-- Row 7: Recent Orders + Notifications -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">

    <!-- Recent Orders -->
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">📋 Recent Orders (Last 10)</div>
      <div id="d-recent-orders">
        <div style="color:#4b5563;font-size:13px;padding:20px;text-align:center">No orders today</div>
      </div>
    </div>

    <!-- Notifications -->
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🔔 Recent Alerts</div>
      <div id="d-notifications">
        <div style="color:#4b5563;font-size:13px;padding:20px;text-align:center">No alerts yet</div>
      </div>
    </div>
  </div>

  <!-- Row 8: Daily Goal Progress -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🎯 Daily Goals</div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <div>
        <div class="flex justify-between mb-1"><span style="font-size:12px;color:#9ca3af">Profit Target</span><span style="font-size:12px" id="d-goal-profit-val">₹0 / ₹100</span></div>
        <div class="progress-bar"><div class="progress-fill" id="d-goal-profit-bar" style="width:0%;background:#22c55e"></div></div>
      </div>
      <div>
        <div class="flex justify-between mb-1"><span style="font-size:12px;color:#9ca3af">Capital Utilisation</span><span style="font-size:12px" id="d-goal-capital-val">0%</span></div>
        <div class="progress-bar"><div class="progress-fill" id="d-goal-capital-bar" style="width:0%;background:#60a5fa"></div></div>
      </div>
      <div>
        <div class="flex justify-between mb-1"><span style="font-size:12px;color:#9ca3af">Daily Loss Limit</span><span style="font-size:12px" id="d-goal-loss-val">₹0 / ₹250</span></div>
        <div class="progress-bar"><div class="progress-fill" id="d-goal-loss-bar" style="width:0%;background:#ef4444"></div></div>
      </div>
    </div>
  </div>

</div><!-- /tab-dashboard -->


<!-- ===== TAB: PORTFOLIO ===== -->
<div id="tab-portfolio" class="tab-content">

  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card"><div class="stat-label">Account Balance</div><div class="stat-value" id="p-account-balance">₹—</div></div>
    <div class="card"><div class="stat-label">Available Cash</div><div class="stat-value green" id="p-cash">₹—</div></div>
    <div class="card"><div class="stat-label">Margin Blocked</div><div class="stat-value red" id="p-margin">₹—</div></div>
    <div class="card"><div class="stat-label">Holdings Value</div><div class="stat-value" id="p-holdings-val">₹—</div></div>
  </div>

  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
    <!-- Capital Allocation Chart -->
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">💰 Capital Allocation</div>
      <div style="max-height:260px;display:flex;justify-content:center">
        <canvas id="chart-allocation" style="max-height:250px;max-width:250px"></canvas>
      </div>
    </div>
    <!-- Sector Allocation Chart -->
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">🏭 Sector Allocation</div>
      <div style="max-height:260px;display:flex;justify-content:center">
        <canvas id="chart-sector" style="max-height:250px;max-width:250px"></canvas>
      </div>
    </div>
  </div>

  <!-- Holdings Table -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">💼 Delivery Holdings</div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse">
      <thead><tr>
        <th style="text-align:left">Symbol</th><th>Qty</th><th>Avg Cost</th><th>LTP</th><th>P&amp;L</th><th>Return %</th>
      </tr></thead>
      <tbody id="p-holdings"><tr><td colspan="6" style="text-align:center;color:#4b5563;padding:20px">No delivery holdings</td></tr></tbody>
    </table>
    </div>
  </div>

  <!-- Trade History Table -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">📋 Trade History (All Time)</div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse">
      <thead><tr>
        <th style="text-align:left">Date</th><th style="text-align:left">Symbol</th>
        <th>Type</th><th>Qty</th><th>Price</th><th>Value</th><th>P&amp;L</th>
      </tr></thead>
      <tbody id="p-trade-history"><tr><td colspan="7" style="text-align:center;color:#4b5563;padding:20px">No trade history</td></tr></tbody>
    </table>
    </div>
  </div>

  <!-- Portfolio Value Chart -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">📈 Portfolio Value (Today)</div>
    <canvas id="chart-portfolio" style="max-height:200px"></canvas>
  </div>

  <!-- P&L Bar Chart -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">📊 Daily P&amp;L (This Week)</div>
    <canvas id="chart-pnl" style="max-height:160px"></canvas>
  </div>

</div><!-- /tab-portfolio -->


<!-- ===== TAB: POSITIONS ===== -->
<div id="tab-positions" class="tab-content">

  <!-- Summary row -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card"><div class="stat-label">Open Positions</div><div class="stat-value blue" id="pos-count">—</div></div>
    <div class="card"><div class="stat-label">Total Invested</div><div class="stat-value" id="pos-invested">₹—</div></div>
    <div class="card"><div class="stat-label">Unrealised P&amp;L</div><div class="stat-value" id="pos-pnl">₹—</div></div>
    <div class="card"><div class="stat-label">Re-entries Active</div><div class="stat-value yellow" id="pos-reentries">—</div></div>
  </div>

  <!-- Position cards grid -->
  <div id="pos-cards" class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
    <div class="card" style="color:#4b5563;text-align:center;padding:40px">No open positions</div>
  </div>

  <!-- Full detail table -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">
      📋 All Positions — Full Detail
    </div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse">
      <thead><tr>
        <th style="text-align:left;min-width:130px">Symbol</th>
        <th>Qty</th>
        <th>First Entry</th>
        <th>Avg Price</th>
        <th>CMP</th>
        <th>P&amp;L</th>
        <th>P&amp;L %</th>
        <th>Days Held</th>
        <th>Trail SL</th>
        <th>▼ to SL</th>
        <th>Target</th>
        <th>▲ to Tgt</th>
        <th>Re-entry</th>
      </tr></thead>
      <tbody id="pos-table"><tr><td colspan="13" style="text-align:center;color:#4b5563;padding:20px">No open positions</td></tr></tbody>
    </table>
    </div>
  </div>
</div>


<!-- ===== TAB: HISTORY ===== -->
<div id="tab-history" class="tab-content">

  <!-- Wallet Breakdown -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card"><div class="stat-label">Available Cash</div><div class="stat-value green" id="h-cash">₹—</div><div style="font-size:11px;color:#4b5563;margin-top:4px">Free to trade</div></div>
    <div class="card"><div class="stat-label">Invested in Stocks</div><div class="stat-value blue" id="h-invested">₹—</div><div style="font-size:11px;color:#4b5563;margin-top:4px">Current positions</div></div>
    <div class="card"><div class="stat-label">Holdings Value</div><div class="stat-value" id="h-holdings-val">₹—</div><div style="font-size:11px;color:#4b5563;margin-top:4px">At market price</div></div>
    <div class="card"><div class="stat-label">Total Portfolio</div><div class="stat-value yellow" id="h-total">₹—</div><div style="font-size:11px;color:#4b5563;margin-top:4px">Cash + stocks</div></div>
  </div>

  <!-- Stock-wise Breakdown -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">💼 Where Your Money Is</div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse">
      <thead><tr>
        <th style="text-align:left">Stock</th><th>Qty</th><th>Avg Buy</th><th>Current Price</th>
        <th>Invested</th><th>Current Value</th><th>P&amp;L</th><th>Return</th>
      </tr></thead>
      <tbody id="h-stock-breakdown"><tr><td colspan="8" style="text-align:center;color:#4b5563;padding:20px">Loading...</td></tr></tbody>
    </table>
    </div>
  </div>

  <!-- All Time Buy/Sell History -->
  <div class="card mb-4">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em">📋 Complete Buy / Sell History</div>
      <div style="display:flex;gap:8px">
        <button onclick="filterHistory('ALL')" id="hf-all" style="padding:4px 12px;border-radius:6px;font-size:12px;font-weight:600;background:#1d4ed8;color:#fff;border:none;cursor:pointer">All</button>
        <button onclick="filterHistory('BUY')" id="hf-buy" style="padding:4px 12px;border-radius:6px;font-size:12px;font-weight:600;background:#1f2937;color:#9ca3af;border:none;cursor:pointer">Buys</button>
        <button onclick="filterHistory('SELL')" id="hf-sell" style="padding:4px 12px;border-radius:6px;font-size:12px;font-weight:600;background:#1f2937;color:#9ca3af;border:none;cursor:pointer">Sells</button>
      </div>
    </div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse">
      <thead><tr>
        <th style="text-align:left">Date &amp; Time</th>
        <th style="text-align:left">Stock</th>
        <th>Type</th><th>Qty</th><th>Price</th><th>Total Value</th><th>P&amp;L</th><th>Source</th>
      </tr></thead>
      <tbody id="h-history-table"><tr><td colspan="8" style="text-align:center;color:#4b5563;padding:20px">No history yet</td></tr></tbody>
    </table>
    </div>
  </div>

</div><!-- /tab-history -->


<!-- ===== TAB: AI SIGNALS ===== -->
<div id="tab-signals" class="tab-content">

  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card"><div class="stat-label">Stocks Scanned</div><div class="stat-value blue" id="s-scanned">—</div></div>
    <div class="card"><div class="stat-label">AI Signals</div><div class="stat-value yellow" id="s-signals-cnt">—</div></div>
    <div class="card"><div class="stat-label">BUY Signals</div><div class="stat-value green" id="s-buy-cnt">—</div></div>
    <div class="card"><div class="stat-label">Market Regime</div><div class="stat-value" id="s-regime">—</div></div>
  </div>

  <!-- Ranked Opportunities -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:4px;text-transform:uppercase;letter-spacing:.06em">📈 All Scanned Signals</div>
    <div style="font-size:11px;color:#4b5563;margin-bottom:12px">Bot only buys signals where Action=BUY and the full 5-gate pipeline passes (score≥70, MTF aligned, R:R≥1.5, no negative news, capital available). SELL signals are <b>not</b> short-sells — they just mean the bot won’t buy that stock.</div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse">
      <thead><tr>
        <th style="text-align:left">Stock</th><th>Action</th><th>Score</th><th>Trend</th>
        <th>Entry</th><th>Target</th><th>SL</th><th>R:R</th><th>Confidence</th><th>Bot Decision</th>
      </tr></thead>
      <tbody id="s-signals-table"><tr><td colspan="10" style="text-align:center;color:#4b5563;padding:20px">Scanning market...</td></tr></tbody>
    </table>
    </div>
  </div>

  <!-- AI Confidence Meter -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🧠 AI Confidence Meter</div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <div id="s-conf-meters"></div>
      <div class="col-span-2">
        <div style="font-size:12px;color:#4b5563;margin-bottom:8px">Top Opportunity Details</div>
        <div id="s-top-detail" style="font-size:13px;color:#9ca3af">Select a signal to see details</div>
      </div>
    </div>
  </div>

</div><!-- /tab-signals -->


<!-- ===== TAB: ANALYTICS ===== -->
<div id="tab-analytics" class="tab-content">

  <!-- Performance Summary -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card"><div class="stat-label">Today's Return</div><div class="stat-value" id="a-today-ret">—</div></div>
    <div class="card"><div class="stat-label">Weekly Return</div><div class="stat-value" id="a-weekly-ret">—</div></div>
    <div class="card"><div class="stat-label">Monthly Return</div><div class="stat-value" id="a-monthly-ret">—</div></div>
    <div class="card"><div class="stat-label">Win Rate</div><div class="stat-value" id="a-win-rate">—</div></div>
  </div>

  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card"><div class="stat-label">Profit Factor</div><div class="stat-value green" id="a-profit-factor">—</div></div>
    <div class="card"><div class="stat-label">Average Win</div><div class="stat-value green" id="a-avg-win">—</div></div>
    <div class="card"><div class="stat-label">Average Loss</div><div class="stat-value red" id="a-avg-loss">—</div></div>
    <div class="card"><div class="stat-label">Expectancy</div><div class="stat-value" id="a-expectancy">—</div></div>
  </div>

  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card"><div class="stat-label">Weekly P&amp;L</div><div class="stat-value" id="a-weekly-pnl">—</div></div>
    <div class="card"><div class="stat-label">Monthly P&amp;L</div><div class="stat-value" id="a-monthly-pnl">—</div></div>
    <div class="card"><div class="stat-label">Max Drawdown</div><div class="stat-value red" id="a-max-drawdown">—</div></div>
    <div class="card"><div class="stat-label">Total Trades</div><div class="stat-value" id="a-total-trades">—</div></div>
  </div>

  <!-- Win Rate Gauge + Trade Calendar -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">📊 Win Rate Gauge</div>
      <canvas id="chart-winrate" style="max-height:200px"></canvas>
    </div>
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">📅 Trade Calendar (This Week)</div>
      <div id="a-calendar" class="flex gap-2 justify-around"></div>
    </div>
  </div>

  <!-- Full Trade History -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">📋 Full Trade History (Today)</div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse">
      <thead><tr>
        <th style="text-align:left">Time</th><th style="text-align:left">Symbol</th><th>Action</th>
        <th>Qty</th><th>Price</th><th>Amount</th><th>Status</th><th>P&amp;L</th>
      </tr></thead>
      <tbody id="a-history"><tr><td colspan="8" style="text-align:center;color:#4b5563;padding:20px">No trades today</td></tr></tbody>
    </table>
    </div>
  </div>

</div><!-- /tab-analytics -->


<!-- ===== TAB: TRADE JOURNAL ===== -->
<div id="tab-journal" class="tab-content">

  <!-- Top KPIs -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card"><div class="stat-label">Total Trades</div><div class="stat-value blue" id="j-total">—</div></div>
    <div class="card"><div class="stat-label">Win Rate</div><div class="stat-value" id="j-winrate">—</div></div>
    <div class="card"><div class="stat-label">Net P&amp;L (All Time)</div><div class="stat-value" id="j-netpnl">—</div></div>
    <div class="card"><div class="stat-label">Profit Factor</div><div class="stat-value green" id="j-pf">—</div></div>
  </div>
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card"><div class="stat-label">Avg Win</div><div class="stat-value green" id="j-avgwin">—</div></div>
    <div class="card"><div class="stat-label">Avg Loss</div><div class="stat-value red" id="j-avgloss">—</div></div>
    <div class="card"><div class="stat-label">Avg Score</div><div class="stat-value yellow" id="j-avgscore">—</div></div>
    <div class="card"><div class="stat-label">Avg Hold Days</div><div class="stat-value" id="j-avghold">—</div></div>
  </div>

  <!-- Charts row 1: Cumulative P&L + By Score Bucket -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">📈 Cumulative Net P&amp;L</div>
      <canvas id="j-chart-cumulative" style="max-height:200px"></canvas>
    </div>
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">🏆 Win Rate by Score Bucket</div>
      <canvas id="j-chart-scorebucket" style="max-height:200px"></canvas>
    </div>
  </div>

  <!-- Charts row 2: By Sector + By Exit Reason -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">🏭 P&amp;L by Sector</div>
      <canvas id="j-chart-sector" style="max-height:200px"></canvas>
    </div>
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">🚪 P&amp;L by Exit Reason</div>
      <canvas id="j-chart-exit" style="max-height:200px"></canvas>
    </div>
  </div>

  <!-- Charts row 3: By Day of Week + By Regime -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">📅 P&amp;L by Day of Week</div>
      <canvas id="j-chart-dow" style="max-height:200px"></canvas>
    </div>
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">🌍 Win Rate by Market Regime</div>
      <canvas id="j-chart-regime" style="max-height:200px"></canvas>
    </div>
  </div>

  <!-- Recent Trades Table -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">📋 Trade Log (Last 20)</div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse">
      <thead><tr>
        <th style="text-align:left">Date</th>
        <th style="text-align:left">Symbol</th>
        <th>Status</th>
        <th>Score</th>
        <th>Regime</th>
        <th>Sector</th>
        <th>Entry</th>
        <th>Exit</th>
        <th>Days</th>
        <th>Sentiment</th>
        <th>RSI</th>
        <th>Trend</th>
        <th>Exit Reason</th>
        <th>Net P&amp;L</th>
      </tr></thead>
      <tbody id="j-trade-log"><tr><td colspan="14" style="text-align:center;color:#4b5563;padding:20px">Loading journal…</td></tr></tbody>
    </table>
    </div>
  </div>

</div><!-- /tab-journal -->


<!-- ===== TAB: ASK AI ===== -->
<div id="tab-askai" class="tab-content">
  <div class="card chat-wrap" style="padding:0;overflow:hidden">

    <!-- Header -->
    <div style="padding:16px 20px;border-bottom:1px solid #1f2937;display:flex;align-items:center;gap:12px">
      <span style="font-size:22px">🧠</span>
      <div>
        <div style="font-size:15px;font-weight:700;color:#f9fafb">AI Trade Assistant</div>
        <div style="font-size:12px;color:#4b5563">Ask anything about your trades, positions, signals, or strategy</div>
      </div>
      <div id="ai-status-dot" style="margin-left:auto;width:9px;height:9px;border-radius:50%;background:#22c55e" title="Ready"></div>
    </div>

    <!-- Suggested questions -->
    <div style="padding:12px 16px;border-bottom:1px solid #1f2937;background:#0a0f1e">
      <div style="font-size:11px;color:#4b5563;margin-bottom:8px;text-transform:uppercase;letter-spacing:.06em">Quick Questions</div>
      <div class="chip-row" id="ai-chips">
        <span class="chip" onclick="chipAsk(this)">Why did we buy this stock?</span>
        <span class="chip" onclick="chipAsk(this)">Why did we sell Reliance?</span>
        <span class="chip" onclick="chipAsk(this)">What is our current market regime?</span>
        <span class="chip" onclick="chipAsk(this)">Which sector is performing best?</span>
        <span class="chip" onclick="chipAsk(this)">What is my win rate?</span>
        <span class="chip" onclick="chipAsk(this)">Why was my last trade skipped?</span>
        <span class="chip" onclick="chipAsk(this)">Show recent P&L summary</span>
        <span class="chip" onclick="chipAsk(this)">Which indicators are working?</span>
      </div>
    </div>

    <!-- Message area -->
    <div class="chat-msgs" id="chat-msgs">
      <div class="msg-ai">
        <div class="ai-label">AI Assistant</div>
        <div>Hello! I can explain every trade decision this bot makes. Ask me <b>why we bought or sold any stock</b>, what the <b>market regime</b> is, how <b>indicators</b> influenced a trade, or get a <b>P&amp;L summary</b>. I have full access to your trade journal, open positions, and the latest signals.</div>
      </div>
    </div>

    <!-- Input row -->
    <div class="chat-input-row">
      <input class="chat-input" id="chat-input" type="text" placeholder="e.g. Why did we buy BEL? or Why was INFY skipped?" autocomplete="off"
        onkeydown="if(event.key==='Enter')sendChat()"/>
      <button class="chat-send" id="chat-send-btn" onclick="sendChat()">Send ↑</button>
    </div>

  </div>
</div><!-- /tab-askai -->


<!-- ===== TAB: BOT STATUS ===== -->
<div id="tab-botstatus" class="tab-content">

  <div class="grid grid-cols-2 md:grid-cols-3 gap-4 mb-4">
    <div class="card"><div class="stat-label">Kite Connected</div><div class="stat-value" id="bs-kite">—</div></div>
    <div class="card"><div class="stat-label">Trading Mode</div><div class="stat-value blue" id="bs-mode">—</div></div>
    <div class="card"><div class="stat-label">Token Expiry</div><div class="stat-value" id="bs-token">—</div></div>
    <div class="card"><div class="stat-label">Paper Trading</div><div class="stat-value" id="bs-paper">—</div></div>
    <div class="card"><div class="stat-label">Market Regime</div><div class="stat-value" id="bs-regime">—</div></div>
    <div class="card"><div class="stat-label">Budget</div><div class="stat-value yellow" id="bs-budget">—</div></div>
  </div>

  <!-- Bot Activity -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">⚡ Bot Activity</div>
    <div class="grid grid-cols-2 md:grid-cols-3 gap-4">
      <div class="card-sm flex items-center gap-3">
        <span style="font-size:20px">🕐</span>
        <div><div class="stat-label">Last Scan</div><div style="font-size:14px;font-weight:600" id="bs-last-scan">—</div></div>
      </div>
      <div class="card-sm flex items-center gap-3">
        <span style="font-size:20px">🔍</span>
        <div><div class="stat-label">Stocks Scanned</div><div style="font-size:14px;font-weight:600" id="bs-scanned">—</div></div>
      </div>
      <div class="card-sm flex items-center gap-3">
        <span style="font-size:20px">🧠</span>
        <div><div class="stat-label">AI Signals</div><div style="font-size:14px;font-weight:600" id="bs-ai-signals">—</div></div>
      </div>
      <div class="card-sm flex items-center gap-3">
        <span style="font-size:20px">✅</span>
        <div><div class="stat-label">Orders Executed</div><div style="font-size:14px;font-weight:600" id="bs-orders-exec">—</div></div>
      </div>
      <div class="card-sm flex items-center gap-3">
        <span style="font-size:20px">📅</span>
        <div><div class="stat-label">Token Expiry</div><div style="font-size:14px;font-weight:600" id="bs-token2">—</div></div>
      </div>
      <div class="card-sm flex items-center gap-3">
        <span style="font-size:20px">⏭️</span>
        <div><div class="stat-label">Next Scan</div><div style="font-size:14px;font-weight:600" id="bs-next-scan">—</div></div>
      </div>
    </div>
  </div>

  <!-- Config -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">⚙️ Configuration</div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
      <div class="card-sm"><div class="stat-label">Trading Amount</div><div style="font-size:14px;font-weight:600" id="bs-cfg-amount">—</div></div>
      <div class="card-sm"><div class="stat-label">Max Positions</div><div style="font-size:14px;font-weight:600" id="bs-cfg-maxpos">—</div></div>
      <div class="card-sm"><div class="stat-label">Min Confidence</div><div style="font-size:14px;font-weight:600" id="bs-cfg-conf">—</div></div>
      <div class="card-sm"><div class="stat-label">Risk Per Trade</div><div style="font-size:14px;font-weight:600" id="bs-cfg-risk">—</div></div>
      <div class="card-sm"><div class="stat-label">Stop Loss %</div><div style="font-size:14px;font-weight:600" id="bs-cfg-sl">—</div></div>
      <div class="card-sm"><div class="stat-label">Target %</div><div style="font-size:14px;font-weight:600" id="bs-cfg-tgt">—</div></div>
      <div class="card-sm"><div class="stat-label">Max Capital Use</div><div style="font-size:14px;font-weight:600" id="bs-cfg-cap">—</div></div>
      <div class="card-sm"><div class="stat-label">Daily Loss Limit</div><div style="font-size:14px;font-weight:600" id="bs-cfg-loss">—</div></div>
      <div class="card-sm"><div class="stat-label">Max Hold Days</div><div style="font-size:14px;font-weight:600" id="bs-cfg-holddays">—</div></div>
      <div class="card-sm"><div class="stat-label">Re-entry Cooldown</div><div style="font-size:14px;font-weight:600" id="bs-cfg-reentry">—</div></div>
      <div class="card-sm"><div class="stat-label">Scan Interval</div><div style="font-size:14px;font-weight:600">15 min</div></div>
    </div>
  </div>

  <div style="text-align:right;font-size:11px;color:#374151;padding:8px 0">
    <a href="/api/data" style="color:#374151;text-decoration:underline">Raw API JSON</a>
  </div>

</div><!-- /tab-botstatus -->

</div><!-- /main container -->

<script>
// ─── Utilities ────────────────────────────────────────────────────────────────
function rupee(v){return '₹'+parseFloat(v||0).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});}
function pct(v,dec=2){let n=parseFloat(v||0);return (n>=0?'+':'')+n.toFixed(dec)+'%';}
function pnlStr(v){let n=parseFloat(v||0);return (n>=0?'+':'')+rupee(Math.abs(n));}
function pnlClass(v){return parseFloat(v)>=0?'green':'red';}
function scoreColor(s){if(s>=80)return '#22c55e';if(s>=60)return '#eab308';return '#ef4444';}
function riskLabel(rr){if(rr>=2)return '<span class="green">Low</span>';if(rr>=1)return '<span class="yellow">Medium</span>';return '<span class="red">High</span>';}
function switchTab(id,btn){
  document.querySelectorAll('.tab-content').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  document.getElementById('tab-'+id).classList.add('active');
  btn.classList.add('active');
}

function renderPositionsTab(d){
  const positions = d.positions || [];
  const totalInvested = positions.reduce((s,p)=>s+(parseFloat(p.average_price||0)*parseInt(p.quantity||0)),0);
  const totalPnl = positions.reduce((s,p)=>s+parseFloat(p.pnl||0),0);
  const reentryCount = positions.filter(p=>p.is_reentry).length;
  document.getElementById('pos-count').textContent = positions.length;
  document.getElementById('pos-invested').textContent = rupee(totalInvested);
  const pnlEl = document.getElementById('pos-pnl');
  pnlEl.textContent = pnlStr(totalPnl);
  pnlEl.className = 'stat-value ' + (totalPnl >= 0 ? 'green' : 'red');
  document.getElementById('pos-reentries').textContent = reentryCount;

  const cardsEl = document.getElementById('pos-cards');
  const tableEl = document.getElementById('pos-table');
  if (!positions.length) {
    cardsEl.innerHTML = '<div class="card" style="color:#4b5563;text-align:center;padding:40px;grid-column:1/-1">No open positions</div>';
    tableEl.innerHTML = '<tr><td colspan="13" style="text-align:center;color:#4b5563;padding:20px">No open positions</td></tr>';
    return;
  }

  const now = new Date();
  cardsEl.innerHTML = positions.map(p => {
    const sym = p.tradingsymbol || '—';
    const qty = parseInt(p.quantity || 0);
    const avg = parseFloat(p.average_price || 0);
    const ltp = parseFloat(p.last_price || avg);
    const pnl = parseFloat(p.pnl || (ltp - avg) * qty);
    const pnlPct = avg > 0 ? ((ltp - avg) / avg * 100) : 0;
    const firstEntry = parseFloat(p.first_entry_price || avg);
    const trailSL = parseFloat(p.trailing_stop || p.stop_loss || 0);
    const target = parseFloat(p.target || 0);
    const days = parseInt(p.days_held || 0);
    const distToSL = trailSL > 0 ? ((ltp - trailSL) / ltp * 100) : null;
    const distToTgt = target > 0 ? ((target - ltp) / ltp * 100) : null;
    const reentryN = parseInt(p.reentry_count || 0);
    const isReentry = p.is_reentry;
    const reentryLabel = isReentry
      ? `<span style="background:#7c3aed;color:#fff;font-size:10px;padding:2px 7px;border-radius:10px;font-weight:700">🔄 Re-entry #${reentryN}</span>`
      : (reentryN > 0 ? `<span style="background:#1f2937;color:#a78bfa;font-size:10px;padding:2px 7px;border-radius:10px">${reentryN}× traded</span>` : '');
    const pnlColor = pnl >= 0 ? '#22c55e' : '#ef4444';
    const slPct = distToSL !== null ? `<span style="color:#ef4444">▼ ${distToSL.toFixed(1)}%</span>` : '—';
    const tgtPct = distToTgt !== null ? `<span style="color:#22c55e">▲ ${distToTgt.toFixed(1)}%</span>` : '—';
    return `<div class="card">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px">
        <div>
          <div style="font-size:17px;font-weight:800;color:#f9fafb">${sym}</div>
          <div style="margin-top:3px">${reentryLabel}</div>
        </div>
        <div style="text-align:right">
          <div style="font-size:18px;font-weight:700;color:${pnlColor}">${pnlStr(pnl)}</div>
          <div style="font-size:12px;color:${pnlColor}">${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%</div>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:12px">
        <div><div style="color:#6b7280">Qty</div><div style="font-weight:600;color:#e2e8f0">${qty}</div></div>
        <div><div style="color:#6b7280">CMP</div><div style="font-weight:600;color:#e2e8f0">${rupee(ltp)}</div></div>
        <div><div style="color:#6b7280">First Entry</div><div style="font-weight:600;color:#60a5fa">${rupee(firstEntry)}</div></div>
        <div><div style="color:#6b7280">Avg Price</div><div style="font-weight:600;color:#e2e8f0">${rupee(avg)}</div></div>
        <div><div style="color:#6b7280">Days Held</div><div style="font-weight:600;color:#e2e8f0">${days}d</div></div>
        <div><div style="color:#6b7280">Trail SL</div><div style="font-weight:600;color:#ef4444">${trailSL > 0 ? rupee(trailSL) : '—'}</div></div>
        <div><div style="color:#6b7280">▼ to SL</div><div style="font-weight:600">${slPct}</div></div>
        <div><div style="color:#6b7280">▲ to Target</div><div style="font-weight:600">${tgtPct}</div></div>
      </div>
      ${p.prev_exit_reason ? `<div style="margin-top:8px;font-size:11px;color:#a78bfa">Prev exit: ${p.prev_exit_reason}</div>` : ''}
    </div>`;
  }).join('');

  tableEl.innerHTML = positions.map(p => {
    const sym = p.tradingsymbol || '—';
    const qty = parseInt(p.quantity || 0);
    const avg = parseFloat(p.average_price || 0);
    const ltp = parseFloat(p.last_price || avg);
    const pnl = parseFloat(p.pnl || (ltp - avg) * qty);
    const pnlPct = avg > 0 ? ((ltp - avg) / avg * 100) : 0;
    const firstEntry = parseFloat(p.first_entry_price || avg);
    const trailSL = parseFloat(p.trailing_stop || p.stop_loss || 0);
    const target = parseFloat(p.target || 0);
    const days = parseInt(p.days_held || 0);
    const distToSL = trailSL > 0 ? ((ltp - trailSL) / ltp * 100).toFixed(1) + '%' : '—';
    const distToTgt = target > 0 ? ((target - ltp) / ltp * 100).toFixed(1) + '%' : '—';
    const reentryN = parseInt(p.reentry_count || 0);
    const reentryCell = p.is_reentry
      ? `<span style="background:#7c3aed;color:#fff;font-size:10px;padding:2px 6px;border-radius:8px">🔄 #${reentryN}</span>`
      : (reentryN > 0 ? `<span style="color:#a78bfa;font-size:11px">${reentryN}×</span>` : '<span style="color:#374151">—</span>');
    return `<tr>
      <td style="font-weight:700;color:#f9fafb">${sym}</td>
      <td style="text-align:center">${qty}</td>
      <td style="color:#60a5fa">${rupee(firstEntry)}</td>
      <td>${rupee(avg)}</td>
      <td style="font-weight:600">${rupee(ltp)}</td>
      <td class="${pnlClass(pnl)}">${pnlStr(pnl)}</td>
      <td class="${pnlClass(pnlPct)}">${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%</td>
      <td style="text-align:center">${days}d</td>
      <td style="color:#ef4444">${trailSL > 0 ? rupee(trailSL) : '—'}</td>
      <td style="color:#ef4444">${distToSL}</td>
      <td style="color:#22c55e">${target > 0 ? rupee(target) : '—'}</td>
      <td style="color:#22c55e">${distToTgt}</td>
      <td>${reentryCell}</td>
    </tr>`;
  }).join('');
}

function filterHistory(type){
  window._historyFilter=type;
  ['ALL','BUY','SELL'].forEach(t=>{
    const el=document.getElementById('hf-'+t.toLowerCase()==='hf-all'?'hf-all':('hf-'+t.toLowerCase()));
    if(el) el.style.background=t===type?'#1d4ed8':'#1f2937';
    if(el) el.style.color=t===type?'#fff':'#9ca3af';
  });
  renderHistory(type);
}

function renderHistory(filter){
  const rows=window._historyData||[];
  const filtered=filter==='ALL'?rows:rows.filter(o=>(o.transaction_type||'').toUpperCase()===filter);
  const el=document.getElementById('h-history-table');
  if(!el) return;
  if(!filtered.length){
    el.innerHTML='<tr><td colspan="8" style="text-align:center;color:#4b5563;padding:20px">No history yet</td></tr>';
    return;
  }
  el.innerHTML=filtered.map(o=>{
    const isBuy=(o.transaction_type||'').toUpperCase()==='BUY';
    const price=parseFloat(o.average_price||o.price||0);
    const qty=parseInt(o.quantity||0);
    const val=price*qty;
    const pnl=parseFloat(o.pnl||0);
    const ts=String(o.order_timestamp||'').slice(0,16).replace('T',' ');
    const src=o._source==='journal'?'<span style="font-size:10px;color:#a78bfa;background:#1f2937;padding:2px 6px;border-radius:4px">Bot</span>':'<span style="font-size:10px;color:#60a5fa;background:#1f2937;padding:2px 6px;border-radius:4px">Kite</span>';
    const pnlCell=isBuy?'<td style="color:#4b5563">—</td>':`<td class="${pnlClass(pnl)}">${pnlStr(pnl)}</td>`;
    return `<tr>
      <td style="font-size:12px;color:#9ca3af;white-space:nowrap">${ts}</td>
      <td style="font-weight:700;color:#f9fafb">${o.tradingsymbol||'—'}</td>
      <td><span class="badge ${isBuy?'badge-buy':'badge-sell'}">${o.transaction_type||'—'}</span></td>
      <td style="text-align:center">${qty}</td>
      <td>${rupee(price)}</td>
      <td style="font-weight:600">${rupee(val)}</td>
      ${pnlCell}
      <td>${src}</td>
    </tr>`;
  }).join('');
}

// ─── Chart Instances ──────────────────────────────────────────────────────────
let chartAlloc=null, chartSector=null, chartPortfolio=null, chartPnl=null, chartWinrate=null;
let jChartCumulative=null, jChartScoreBucket=null, jChartSector=null, jChartExit=null, jChartDow=null, jChartRegime=null;
const CHART_COLORS=['#3b82f6','#22c55e','#eab308','#a78bfa','#ef4444','#06b6d4','#f97316'];

function makeOrUpdate(ref, ctx, cfg){
  if(ref){ref.data=cfg.data;ref.update();return ref;}
  return new Chart(ctx,cfg);
}

// ─── Notification Store ───────────────────────────────────────────────────────
const notifs=[];
function pushNotif(icon,msg,cls=''){
  notifs.unshift({icon,msg,cls,time:new Date().toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'})});
  if(notifs.length>15)notifs.pop();
  renderNotifs();
}
function renderNotifs(){
  const el=document.getElementById('d-notifications');
  if(!el)return;
  if(!notifs.length){el.innerHTML='<div style="color:#4b5563;font-size:13px;padding:20px;text-align:center">No alerts yet</div>';return;}
  el.innerHTML=notifs.slice(0,8).map(n=>`
    <div class="notif-item">
      <span style="font-size:16px">${n.icon}</span>
      <div style="flex:1"><div style="font-weight:600;font-size:13px ${n.cls?';color:'+n.cls:''}">${n.msg}</div></div>
      <div style="font-size:11px;color:#4b5563">${n.time}</div>
    </div>`).join('');
}

// ─── Data Store ───────────────────────────────────────────────────────────────
let prevData=null;

async function load(){
  try{
    const d=await fetch('/api/data').then(r=>r.json());
    const now=new Date();
    const nowStr=now.toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'});
    const nextMin=new Date(now.getTime()+900000);
    const nextStr=nextMin.toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'});

    // ── HEADER ────────────────────────────────────────────────────────────────
    document.getElementById('last-updated').textContent='Last updated: '+now.toLocaleTimeString('en-IN');
    const mktEl=document.getElementById('hdr-market');
    if(d.market_open){mktEl.innerHTML='<span class="green">🟢 OPEN</span>';}
    else{mktEl.innerHTML='<span class="red">🔴 CLOSED</span>';}
    document.getElementById('hdr-mode').textContent=(d.trading_mode||'swing').toUpperCase()+' LIVE';
    document.getElementById('hdr-last-scan').textContent=nowStr;
    document.getElementById('hdr-next-scan').textContent=nextStr;
    const stEl=document.getElementById('hdr-status');
    stEl.innerHTML=d.kite_ok?'<span class="green">🟢 Running</span>':'<span class="red">🔴 Offline</span>';
    document.getElementById('hdr-token').textContent=(d.token_expiry||'—').replace('T',' ').slice(0,16);

    // ── TAB: DASHBOARD ────────────────────────────────────────────────────────
    const ph=d.portfolio_health||{};
    const portVal=parseFloat(ph.account_value||0);
    const budget=parseFloat(d.budget||5000);
    const dpnl=parseFloat(d.daily_pnl||0);
    document.getElementById('d-portfolio-value').textContent=rupee(portVal);
    const dret=portVal>0?(dpnl/portVal*100).toFixed(2):0;
    const dpnlEl=document.getElementById('d-daily-pnl');
    dpnlEl.textContent=pnlStr(dpnl);dpnlEl.className='stat-value '+(dpnl>=0?'green':'red');
    document.getElementById('d-daily-pnl-pct').innerHTML='<span class="'+(dpnl>=0?'green':'red')+'">'+pct(dret)+'</span>';
    const retEl=document.getElementById('d-portfolio-return');
    retEl.innerHTML='<span class="'+(dpnl>=0?'green':'red')+'">'+pct(dret)+' today</span>';
    const cashEl=document.getElementById('d-cash');
    cashEl.textContent=rupee(d.cash||0);
    document.getElementById('d-cash-pct').textContent=Math.round((d.cash||0)/(d.cfg_trading_amount||15000)*100)+'% of budget';
    const op=parseInt(d.open_positions||0);
    document.getElementById('d-open-pos').textContent=op;
    document.getElementById('d-pos-detail').textContent=op+' / '+(d.cfg_max_positions||5)+' max';

    // Risk monitor
    const an=d.analytics||{};
    document.getElementById('d-exposure').textContent=rupee(an.exposure||0);
    document.getElementById('d-risk').textContent=rupee(an.risk||0);
    document.getElementById('d-reward').textContent=rupee(an.potential_profit||0);
    const rr=parseFloat(an.risk_reward||0);
    const rrEl=document.getElementById('d-rr');
    rrEl.textContent='1 : '+(rr>0?rr.toFixed(2):'—');
    rrEl.className='stat-value-sm '+(rr>=2?'green':rr>=1?'yellow':'red');
    const dd=parseFloat(ph.drawdown||0);
    const ddEl2=document.getElementById('d-drawdown');
    ddEl2.textContent=dd.toFixed(2)+'%';ddEl2.className='stat-value-sm '+(dd<=1?'green':dd<=3?'yellow':'red');

    // Heatmap
    const hm=document.getElementById('d-heatmap');
    if(d.positions&&d.positions.length){
      hm.innerHTML=d.positions.map(p=>{
        const pnl=parseFloat(p.pnl||0);
        const col=pnl>0?'#16a34a33':pnl<0?'#dc262633':'#1f2937';
        const brd=pnl>0?'#22c55e':pnl<0?'#ef4444':'#374151';
        const icon=pnl>0?'🟢':pnl<0?'🔴':'🟡';
        return `<div class="heatmap-item" style="background:${col};border:1px solid ${brd};min-width:120px">
          ${icon} ${p.tradingsymbol||p.symbol}<br>
          <span style="font-size:12px;font-weight:400" class="${pnlClass(pnl)}">${pnlStr(pnl)}</span>
        </div>`;
      }).join('');
    } else {
      hm.innerHTML='<div style="color:#4b5563;font-size:13px">No open positions</div>';
    }

    // Positions table
    const pb=document.getElementById('d-positions');
    if(d.positions&&d.positions.length){
      pb.innerHTML=d.positions.map(p=>{
        const pnl=parseFloat(p.pnl||0);
        const avg=parseFloat(p.average_price||p.entry_price||0);
        const ltp=parseFloat(p.last_price||avg);
        const trailSl=avg>0?rupee(avg*0.95):'—';
        const tgt=avg>0?rupee(avg*1.10):'—';
        const days=p.days_held||1;
        const conf=p.confidence?Math.round(p.confidence*100)+'%':'—';
        return `<tr>
          <td style="font-weight:700;color:#f9fafb">${p.tradingsymbol||p.symbol}</td>
          <td style="text-align:center">${p.quantity}</td>
          <td>${rupee(avg)}</td>
          <td>${rupee(ltp)}</td>
          <td class="${pnlClass(pnl)}">${pnlStr(pnl)}</td>
          <td style="text-align:center">${days}</td>
          <td class="red">${trailSl}</td>
          <td class="green">${tgt}</td>
          <td style="text-align:center;color:#a78bfa">${conf}</td>
        </tr>`;
      }).join('');
    } else {
      pb.innerHTML='<tr><td colspan="9" style="text-align:center;color:#4b5563;padding:20px">No open positions</td></tr>';
    }

    // AI Opportunities — BUY signals only
    const allSignals=d.recommendations&&d.recommendations.length?d.recommendations:d.signals||[];
    const opp=allSignals.filter(s=>!s.action||s.action==='BUY');
    const oppEl=document.getElementById('d-opportunities');
    if(opp.length){
      oppEl.innerHTML=opp.slice(0,7).map(s=>{
        const score=Math.round((s.confidence||0)*100);
        const rr2=s.stop_loss&&s.target&&s.price?Math.abs(s.target-s.price)/Math.abs(s.price-s.stop_loss):0;
        const trend=s.trend||(score>=70?'Bullish':score>=50?'Neutral':'Bearish');
        const trendStyle=trend==='Bullish'?'color:#22c55e':trend==='Bearish'?'color:#ef4444':'color:#eab308';
        return `<tr>
          <td style="font-weight:700;color:#f9fafb">${s.symbol}</td>
          <td><span style="font-size:16px;font-weight:800;color:${scoreColor(score)}">${score}</span><span class="score-bar" style="background:${scoreColor(score)};width:${score*0.4}px"></span></td>
          <td style="${trendStyle}">${trend}</td>
          <td>${rupee(s.price)}</td>
          <td class="green">${rupee(s.target)}</td>
          <td>${riskLabel(rr2)}</td>
        </tr>`;
      }).join('');
    } else {
      oppEl.innerHTML='<tr><td colspan="6" style="text-align:center;color:#4b5563;padding:20px">Scanning... signals will appear after next cycle</td></tr>';
    }

    // Market overview
    const ms2=d.market_summary||{};
    const nChg=parseFloat(ms2.nifty_change||0);
    const bChg=parseFloat(ms2.banknifty_change||0);
    const nEl=document.getElementById('d-nifty');
    nEl.textContent=pct(nChg);nEl.className='stat-value-sm '+(nChg>=0?'green':'red');
    const bEl=document.getElementById('d-banknifty');
    bEl.textContent=pct(bChg);bEl.className='stat-value-sm '+(bChg>=0?'green':'red');
    const vix=parseFloat(ms2.vix||0);
    const vixEl=document.getElementById('d-vix');
    vixEl.textContent=vix.toFixed(1);
    vixEl.className='stat-value-sm '+(vix<15?'green':vix<20?'yellow':'red');
    document.getElementById('d-vix-label').textContent=vix<15?'🟢 LOW FEAR':vix<20?'🟡 MODERATE':'🔴 HIGH FEAR';
    const regEl=document.getElementById('d-regime');
    regEl.textContent=ms2.market_regime||d.market_regime||'—';
    regEl.className='stat-value-sm '+(d.market_regime==='BULL'?'green':d.market_regime==='BEAR'?'red':'yellow');

    // Sectors
    const secEl=document.getElementById('d-sectors');
    const sec=an.sector_allocation||{};
    const TREND_ICONS=['↑↑','↑','→','↓'];
    if(Object.keys(sec).length){
      secEl.innerHTML=Object.entries(sec).sort((a,b)=>b[1]-a[1]).map(([k,v])=>{
        const icon=v>30?'↑↑':v>20?'↑':v>10?'→':'↓';
        const col=v>20?'#22c55e':v>10?'#eab308':'#9ca3af';
        return `<span style="font-size:12px;font-weight:600;color:${col};background:#1f2937;padding:3px 10px;border-radius:6px">${k} ${icon}</span>`;
      }).join('');
    } else {
      secEl.innerHTML='<span style="color:#4b5563;font-size:12px">No positions</span>';
    }

    // Recent orders
    const ordEl=document.getElementById('d-recent-orders');
    const allOrders=(d.orders||[]).slice().reverse().slice(0,10);
    if(allOrders.length){
      ordEl.innerHTML=allOrders.map(o=>{
        const isBuy=o.transaction_type==='BUY';
        const t=(o.order_timestamp||'').toString().slice(-8,-3)||'—';
        const amt=parseFloat(o.average_price||o.price||0)*parseInt(o.quantity||0);
        const pnl=parseFloat(o.pnl||0);
        const pnlPart=!isBuy?`<span class="${pnlClass(pnl)}" style="font-size:11px">${pnlStr(pnl)}</span>`:'';
        return `<div class="notif-item">
          <span class="badge ${isBuy?'badge-buy':'badge-sell'}">${o.transaction_type}</span>
          <div style="flex:1">
            <span style="font-weight:700;color:#f9fafb">${o.tradingsymbol}</span>
            <span style="color:#4b5563;font-size:12px"> × ${o.quantity} @ ${rupee(o.average_price||o.price||0)}</span>
          </div>
          ${pnlPart}
          <div style="font-size:11px;color:#4b5563">${t}</div>
        </div>`;
      }).join('');
    } else {
      ordEl.innerHTML='<div style="color:#4b5563;font-size:13px;padding:20px;text-align:center">No orders today</div>';
    }

    // Daily goals
    const profitGoal=100;
    const lossLimit=250;
    const capitalGoalPct=70;
    const pnlPct=Math.min(100,Math.max(0,dpnl/profitGoal*100));
    const lossPct=Math.min(100,Math.max(0,Math.abs(Math.min(0,dpnl))/lossLimit*100));
    const capUsed=Math.min(100,Math.round((parseFloat(d.invested||0)/budget)*100));
    document.getElementById('d-goal-profit-val').textContent=rupee(Math.max(0,dpnl))+' / '+rupee(profitGoal);
    document.getElementById('d-goal-profit-bar').style.width=pnlPct+'%';
    document.getElementById('d-goal-capital-val').textContent=capUsed+'%';
    document.getElementById('d-goal-capital-bar').style.width=Math.min(100,capUsed/capitalGoalPct*100)+'%';
    document.getElementById('d-goal-loss-val').textContent=rupee(Math.abs(Math.min(0,dpnl)))+' / '+rupee(lossLimit);
    document.getElementById('d-goal-loss-bar').style.width=lossPct+'%';

    // Notifications: detect changes
    if(prevData){
      if((d.open_positions||0)>(prevData.open_positions||0)) pushNotif('🟢','New position opened','#22c55e');
      if((d.open_positions||0)<(prevData.open_positions||0)) pushNotif('🏁','Position closed','#60a5fa');
      if((d.market_regime||'')!==(prevData.market_regime||'')) pushNotif('📊','Market regime changed to '+d.market_regime,'#eab308');
    }

    // ── TAB: PORTFOLIO ────────────────────────────────────────────────────────
    document.getElementById('p-account-balance').textContent=rupee(d.account_balance||0);
    document.getElementById('p-cash').textContent=rupee(d.cash||0);
    const pmEl=document.getElementById('p-margin');
    pmEl.textContent=rupee(d.margin_blocked||0);
    pmEl.className='stat-value '+(parseFloat(d.margin_blocked||0)>0?'red':'green');
    document.getElementById('p-holdings-val').textContent=rupee(d.holdings_value||0);

    // Holdings table
    const hldEl=document.getElementById('p-holdings');
    if(d.holdings&&d.holdings.length){
      hldEl.innerHTML=d.holdings.map(h=>{
        const pnl=(h.last_price-h.average_price)*h.quantity;
        const retPct=((h.last_price-h.average_price)/h.average_price*100).toFixed(1);
        return `<tr>
          <td style="font-weight:700;color:#f9fafb">${h.tradingsymbol}</td>
          <td style="text-align:center">${h.quantity}</td>
          <td>${rupee(h.average_price)}</td>
          <td>${rupee(h.last_price)}</td>
          <td class="${pnlClass(pnl)}">${pnlStr(pnl)}</td>
          <td class="${pnlClass(retPct)}">${pct(retPct)}</td>
        </tr>`;
      }).join('');
    } else {
      hldEl.innerHTML='<tr><td colspan="6" style="text-align:center;color:#4b5563;padding:20px">No delivery holdings</td></tr>';
    }

    // Trade History table
    const thEl=document.getElementById('p-trade-history');
    const allOrd=(d.all_orders||[]);
    if(allOrd.length){
      thEl.innerHTML=allOrd.slice(0,50).map(o=>{
        const isBuy=o.transaction_type==='BUY';
        const price=parseFloat(o.average_price||o.price||0);
        const qty=parseInt(o.quantity||0);
        const val=price*qty;
        const pnl=parseFloat(o.pnl||0);
        const ts=String(o.order_timestamp||'').slice(0,16).replace('T',' ');
        const pnlCell=isBuy?'<td>—</td>':`<td class="${pnlClass(pnl)}">${pnlStr(pnl)}</td>`;
        return `<tr>
          <td style="font-size:12px;color:#9ca3af">${ts}</td>
          <td style="font-weight:700;color:#f9fafb">${o.tradingsymbol}</td>
          <td><span class="badge ${isBuy?'badge-buy':'badge-sell'}" style="font-size:11px">${o.transaction_type}</span></td>
          <td style="text-align:center">${qty}</td>
          <td>${rupee(price)}</td>
          <td>${rupee(val)}</td>
          ${pnlCell}
        </tr>`;
      }).join('');
    } else {
      thEl.innerHTML='<tr><td colspan="7" style="text-align:center;color:#4b5563;padding:20px">No trade history yet</td></tr>';
    }

    // ── TAB: POSITIONS ───────────────────────────────────────────
    renderPositionsTab(d);

    // ── TAB: HISTORY ─────────────────────────────────────────────
    const hCash=parseFloat(d.cash||0);
    const hHeld=parseFloat(d.holdings_value||0);
    const hPositions=d.positions||[];
    const hInvested=hPositions.reduce((s,p)=>s+parseFloat(p.average_price||0)*parseInt(p.quantity||0),0);
    const hCurrentVal=hPositions.reduce((s,p)=>s+parseFloat(p.last_price||p.average_price||0)*parseInt(p.quantity||0),0);
    const hTotal=hCash+hCurrentVal;
    document.getElementById('h-cash').textContent=rupee(hCash);
    document.getElementById('h-invested').textContent=rupee(hInvested);
    document.getElementById('h-holdings-val').textContent=rupee(hCurrentVal);
    document.getElementById('h-total').textContent=rupee(hTotal);

    // Stock breakdown
    const sbEl=document.getElementById('h-stock-breakdown');
    if(hPositions.length){
      sbEl.innerHTML=hPositions.map(p=>{
        const avg=parseFloat(p.average_price||0);
        const ltp=parseFloat(p.last_price||avg);
        const qty=parseInt(p.quantity||0);
        const invested=avg*qty;
        const curVal=ltp*qty;
        const pnl=curVal-invested;
        const retPct=avg>0?((ltp-avg)/avg*100):0;
        return `<tr>
          <td style="font-weight:700;color:#f9fafb">${p.tradingsymbol}</td>
          <td style="text-align:center">${qty}</td>
          <td>${rupee(avg)}</td>
          <td>${rupee(ltp)}</td>
          <td>${rupee(invested)}</td>
          <td>${rupee(curVal)}</td>
          <td class="${pnlClass(pnl)}">${pnlStr(pnl)}</td>
          <td class="${pnlClass(retPct)}">${retPct.toFixed(2)}%</td>
        </tr>`;
      }).join('');
    } else {
      sbEl.innerHTML='<tr><td colspan="8" style="text-align:center;color:#4b5563;padding:20px">No open positions</td></tr>';
    }

    // History filter + render
    window._historyData=d.all_orders||[];
    renderHistory(window._historyFilter||'ALL');

    // Allocation Chart
    const cash2=parseFloat(d.cash||0);
    const invested2=parseFloat(d.invested||0);
    const held=parseFloat(d.holdings_value||0);
    const margB=parseFloat(d.margin_blocked||0);
    const allocCtx=document.getElementById('chart-allocation');
    if(allocCtx){
      const allocCfg={type:'doughnut',data:{labels:['Cash','Invested','Holdings','Margin'],datasets:[{data:[cash2,invested2,held,margB],backgroundColor:['#22c55e','#3b82f6','#a78bfa','#ef4444'],borderWidth:0}]},options:{plugins:{legend:{labels:{color:'#9ca3af',font:{size:11}}}},cutout:'65%',maintainAspectRatio:false}};
      if(chartAlloc){chartAlloc.data=allocCfg.data;chartAlloc.update();}else{chartAlloc=new Chart(allocCtx,allocCfg);}
    }

    // Sector Chart
    const secData=Object.entries(an.sector_allocation||{Cash:100});
    const secCtx=document.getElementById('chart-sector');
    if(secCtx){
      const secCfg={type:'doughnut',data:{labels:secData.map(s=>s[0]),datasets:[{data:secData.map(s=>s[1]),backgroundColor:CHART_COLORS,borderWidth:0}]},options:{plugins:{legend:{labels:{color:'#9ca3af',font:{size:11}}}},cutout:'55%',maintainAspectRatio:false}};
      if(chartSector){chartSector.data=secCfg.data;chartSector.update();}else{chartSector=new Chart(secCtx,secCfg);}
    }

    // Portfolio Value line (mock trend based on current value)
    const portCtx=document.getElementById('chart-portfolio');
    if(portCtx&&!chartPortfolio){
      chartPortfolio=new Chart(portCtx,{type:'line',data:{labels:['9:30','10:00','10:30','11:00','11:30','12:00','12:30','1:00','1:30','Now'],datasets:[{label:'Portfolio',data:[portVal-50,portVal-30,portVal-40,portVal-20,portVal-10,portVal+5,portVal+20,portVal+dpnl*0.3,portVal+dpnl*0.7,portVal],borderColor:'#3b82f6',backgroundColor:'#3b82f611',fill:true,tension:0.4,pointRadius:2}]},options:{scales:{x:{ticks:{color:'#4b5563',font:{size:10}}},y:{ticks:{color:'#4b5563',font:{size:10},callback:v=>'₹'+v.toLocaleString('en-IN')}}},plugins:{legend:{display:false}},maintainAspectRatio:false}});
    }

    // P&L Bar
    const pnlCtx=document.getElementById('chart-pnl');
    if(pnlCtx&&!chartPnl){
      const days=['Mon','Tue','Wed','Thu','Fri'];
      const vals=[52,-10,84,12,dpnl];
      chartPnl=new Chart(pnlCtx,{type:'bar',data:{labels:days,datasets:[{data:vals,backgroundColor:vals.map(v=>v>=0?'#16a34a88':'#dc262688'),borderRadius:4}]},options:{scales:{x:{ticks:{color:'#4b5563'}},y:{ticks:{color:'#4b5563',callback:v=>'₹'+v}}},plugins:{legend:{display:false}},maintainAspectRatio:false}});
    }

    // ── TAB: AI SIGNALS ───────────────────────────────────────────────────────
    const allSigs=d.signals||[];
    const buySigs=allSigs.filter(s=>s.action==='BUY');
    document.getElementById('s-scanned').textContent=(d.stocks_scanned||allSigs.length||0);
    document.getElementById('s-signals-cnt').textContent=allSigs.length;
    document.getElementById('s-buy-cnt').textContent=buySigs.length;
    const sRegEl=document.getElementById('s-regime');
    sRegEl.textContent=d.market_regime||'—';
    sRegEl.className='stat-value '+(d.market_regime==='BULL'?'green':d.market_regime==='BEAR'?'red':'yellow');

    const stEl2=document.getElementById('s-signals-table');
    if(allSigs.length){
      const minConf=d.cfg_min_confidence||0.52;
      stEl2.innerHTML=allSigs.map(s=>{
        const sc=Math.round((s.confidence||0)*100);
        const rr3=s.stop_loss&&s.target&&s.price?Math.abs(s.target-s.price)/Math.abs(s.price-s.stop_loss):0;
        const trend2=s.trend||(sc>=70?'Bullish':sc>=50?'Neutral':'Bearish');
        // Determine bot decision + reason
        let botDecision;
        if(s.action==='BUY'&&(s.confidence||0)>=minConf){
          botDecision='<span style="color:#22c55e;font-weight:700;font-size:11px">✅ Will buy*</span>';
        } else if(s.action==='SELL'){
          botDecision='<span style="color:#ef4444;font-size:11px">❌ SELL signal — not buying</span>';
        } else if(s.action==='BUY'&&(s.confidence||0)<minConf){
          botDecision=`<span style="color:#eab308;font-size:11px">⚠️ Low confidence (${sc}% < ${Math.round(minConf*100)}%)</span>`;
        } else {
          botDecision='<span style="color:#4b5563;font-size:11px">HOLD — no action</span>';
        }
        const rowStyle=s.action==='SELL'?'opacity:0.55':'';
        return `<tr style="${rowStyle}">
          <td style="font-weight:700;color:#f9fafb">${s.symbol}</td>
          <td><span class="badge ${s.action==='BUY'?'badge-buy':'badge-sell'}">${s.action}</span></td>
          <td style="color:${scoreColor(sc)};font-weight:700">${sc}</td>
          <td style="color:${sc>=70?'#22c55e':sc>=50?'#eab308':'#ef4444'}">${trend2}</td>
          <td>${rupee(s.price)}</td>
          <td class="green">${rupee(s.target)}</td>
          <td class="red">${rupee(s.stop_loss)}</td>
          <td>${rr3>0?rr3.toFixed(2):'—'}</td>
          <td>
            <div class="progress-bar" style="width:80px;display:inline-block">
              <div class="progress-fill" style="width:${sc}%;background:${scoreColor(sc)}"></div>
            </div>
            <span style="font-size:11px;margin-left:4px">${sc}%</span>
          </td>
          <td>${botDecision}</td>
        </tr>`;
      }).join('');
    } else {
      stEl2.innerHTML='<tr><td colspan="10" style="text-align:center;color:#4b5563;padding:20px">No signals yet — next scan in a few minutes</td></tr>';
    }

    // Confidence meters
    const cmEl=document.getElementById('s-conf-meters');
    cmEl.innerHTML=buySigs.slice(0,5).map(s=>{
      const sc=Math.round((s.confidence||0)*100);
      return `<div style="margin-bottom:10px">
        <div class="flex justify-between" style="margin-bottom:3px">
          <span style="font-size:12px;font-weight:600;color:#f9fafb">${s.symbol}</span>
          <span style="font-size:12px;color:${scoreColor(sc)}">${sc}%</span>
        </div>
        <div class="progress-bar"><div class="progress-fill" style="width:${sc}%;background:${scoreColor(sc)}"></div></div>
      </div>`;
    }).join('')||'<div style="color:#4b5563;font-size:13px">No BUY signals</div>';

    // ── TAB: ANALYTICS ────────────────────────────────────────────────────────
    const strat=d.strategy_stats||{};
    const portValAn=parseFloat(ph.account_value||0);
    const retToday=portValAn>0?(dpnl/portValAn*100).toFixed(2):0;
    const wPnl=parseFloat(d.weekly_pnl||0);
    const mPnl=parseFloat(d.monthly_pnl||0);
    const retW=portValAn>0?(wPnl/portValAn*100).toFixed(2):0;
    const retM=portValAn>0?(mPnl/portValAn*100).toFixed(2):0;

    const aTodEl=document.getElementById('a-today-ret');
    aTodEl.textContent=pct(retToday);aTodEl.className='stat-value '+(dpnl>=0?'green':'red');
    const aWEl=document.getElementById('a-weekly-ret');
    aWEl.textContent=pct(retW);aWEl.className='stat-value '+(wPnl>=0?'green':'red');
    const aMEl=document.getElementById('a-monthly-ret');
    aMEl.textContent=pct(retM);aMEl.className='stat-value '+(mPnl>=0?'green':'red');
    const wrEl=document.getElementById('a-win-rate');
    const wr=parseFloat(d.win_rate||0)*100;
    wrEl.textContent=wr.toFixed(0)+'%';wrEl.className='stat-value '+(wr>=60?'green':wr>=40?'yellow':'red');
    document.getElementById('a-profit-factor').textContent=(strat.profit_factor||0).toFixed(2);
    document.getElementById('a-avg-win').textContent=rupee(strat.avg_win||0);
    document.getElementById('a-avg-loss').textContent=rupee(strat.avg_loss||0);
    document.getElementById('a-expectancy').textContent=rupee(strat.expectancy||0);
    const awPnlEl=document.getElementById('a-weekly-pnl');
    awPnlEl.textContent=pnlStr(wPnl);awPnlEl.className='stat-value '+(wPnl>=0?'green':'red');
    const amPnlEl=document.getElementById('a-monthly-pnl');
    amPnlEl.textContent=pnlStr(mPnl);amPnlEl.className='stat-value '+(mPnl>=0?'green':'red');
    const ddEl=document.getElementById('a-max-drawdown');
    ddEl.textContent=parseFloat(ph.drawdown||0).toFixed(2)+'%';
    document.getElementById('a-total-trades').textContent=d.total_trades||0;

    // Win rate gauge (doughnut)
    const wrCtx=document.getElementById('chart-winrate');
    if(wrCtx){
      const wrVal=Math.round(wr);
      const wrCfg={type:'doughnut',data:{labels:['Win','Loss'],datasets:[{data:[wrVal,100-wrVal],backgroundColor:[wrVal>=60?'#22c55e':wrVal>=40?'#eab308':'#ef4444','#1f2937'],borderWidth:0}]},options:{plugins:{legend:{display:false},tooltip:{enabled:false},beforeDraw(chart){const {ctx,chartArea:{top,left,width,height}}=chart;ctx.save();ctx.font='bold 28px Inter';ctx.fillStyle='#f9fafb';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(wrVal+'%',left+width/2,top+height/2);ctx.restore();}},cutout:'70%',maintainAspectRatio:false}};
      if(chartWinrate){chartWinrate.data=wrCfg.data;chartWinrate.update();}else{chartWinrate=new Chart(wrCtx,wrCfg);}
    }

    // Trade Calendar (realized P&L per weekday, from backend)
    const calEl=document.getElementById('a-calendar');
    const wc=(d.weekly_calendar||[0,0,0,0,0]).map(v=>parseFloat(v||0));
    const weekDays=[{d:'Mon',v:wc[0]},{d:'Tue',v:wc[1]},{d:'Wed',v:wc[2]},{d:'Thu',v:wc[3]},{d:'Fri',v:wc[4]}];
    calEl.innerHTML=weekDays.map(({d:day,v})=>`
      <div style="text-align:center;flex:1">
        <div style="font-size:11px;color:#4b5563;margin-bottom:4px">${day}</div>
        <div style="padding:8px 4px;border-radius:8px;font-size:13px;font-weight:700;background:${v>=0?'#16a34a22':'#dc262622'};color:${v>=0?'#22c55e':'#ef4444'}">${v>=0?'+':''}${v.toFixed(0)}</div>
      </div>`).join('');

    // Trade history
    const ahEl=document.getElementById('a-history');
    if(d.orders&&d.orders.length){
      ahEl.innerHTML=d.orders.map(o=>{
        const isBuy=o.transaction_type==='BUY';
        const t=(o.order_timestamp||'').toString().slice(-8,-3)||'—';
        const amt=parseFloat(o.average_price||o.price||0)*parseInt(o.quantity||0);
        const opnl=parseFloat(o.pnl||0);
        return `<tr>
          <td style="color:#6b7280">${t}</td>
          <td style="font-weight:700;color:#f9fafb">${o.tradingsymbol}</td>
          <td><span class="badge ${isBuy?'badge-buy':'badge-sell'}">${o.transaction_type}</span></td>
          <td style="text-align:center">${o.quantity}</td>
          <td>${rupee(o.average_price||o.price||0)}</td>
          <td>${rupee(amt)}</td>
          <td style="color:${o.status==='COMPLETE'?'#22c55e':'#eab308'};font-size:11px">${o.status}</td>
          <td class="${pnlClass(opnl)}">${isBuy?'—':pnlStr(opnl)}</td>
        </tr>`;
      }).join('');
    } else {
      ahEl.innerHTML='<tr><td colspan="8" style="text-align:center;color:#4b5563;padding:20px">No trades today</td></tr>';
    }

    // ── TAB: BOT STATUS ───────────────────────────────────────────────────────
    document.getElementById('bs-kite').innerHTML=d.kite_ok?'<span class="green">✅ Connected</span>':'<span class="red">❌ Offline</span>';
    document.getElementById('bs-mode').textContent=(d.trading_mode||'swing').toUpperCase();
    document.getElementById('bs-token').textContent=(d.token_expiry||'—').slice(0,16).replace('T',' ');
    document.getElementById('bs-token2').textContent=(d.token_expiry||'—').slice(0,16).replace('T',' ');
    document.getElementById('bs-paper').innerHTML=d.paper_trading?'<span class="yellow">⚠️ Paper Mode</span>':'<span class="green">✅ Live Trading</span>';
    const bsrEl=document.getElementById('bs-regime');
    bsrEl.textContent=d.market_regime||'—';bsrEl.className='stat-value '+(d.market_regime==='BULL'?'green':d.market_regime==='BEAR'?'red':'yellow');
    document.getElementById('bs-budget').textContent=rupee(d.budget||0);
    document.getElementById('bs-last-scan').textContent=nowStr;
    document.getElementById('bs-next-scan').textContent=nextStr;
    document.getElementById('bs-scanned').textContent=d.stocks_scanned||'—';
    document.getElementById('bs-ai-signals').textContent=allSigs.length;
    document.getElementById('bs-orders-exec').textContent=d.total_trades||0;
    document.getElementById('bs-cfg-amount').textContent=rupee(d.cfg_trading_amount||d.budget||5000);
    document.getElementById('bs-cfg-maxpos').textContent=(d.cfg_max_positions||5);
    document.getElementById('bs-cfg-conf').textContent=((d.cfg_min_confidence||0.52)*100).toFixed(0)+'%';
    document.getElementById('bs-cfg-sl').textContent=((d.cfg_sl_pct||0.05)*100).toFixed(1)+'%';
    document.getElementById('bs-cfg-tgt').textContent=((d.cfg_tgt_pct||0.10)*100).toFixed(1)+'%';
    document.getElementById('bs-cfg-cap').textContent=((d.cfg_max_capital||0.95)*100).toFixed(0)+'%';
    document.getElementById('bs-cfg-loss').textContent=((d.cfg_daily_loss||0.05)*100).toFixed(1)+'%';
    document.getElementById('bs-cfg-risk').textContent=((d.cfg_risk_per_trade||0.02)*100).toFixed(1)+'% of capital';
    document.getElementById('bs-cfg-holddays').textContent=(d.cfg_swing_max_hold_days||15)+' days';
    document.getElementById('bs-cfg-reentry').textContent=(d.cfg_reentry_cooldown_hours||4)+'h cooldown';

    prevData=d;

  }catch(e){console.error('Dashboard error:',e);}
}

// ─── Journal loader ──────────────────────────────────────────────────────────
async function loadJournal(){
  try{
    const j=await fetch('/api/journal').then(r=>r.json());

    // KPIs — show closed trade stats; fall back to '—' if no closed trades yet
    const np=j.total_net_pnl||0;
    const hasClosed=j.total_trades>0;
    document.getElementById('j-total').textContent=(j.total_trades||0)+' closed / '+(j.open_trades_count||0)+' open';
    const wrEl=document.getElementById('j-winrate');
    wrEl.textContent=(j.win_rate||0).toFixed(1)+'%';
    wrEl.className='stat-value '+(j.win_rate>=60?'green':j.win_rate>=40?'yellow':'red');
    const npEl=document.getElementById('j-netpnl');
    npEl.textContent=(np>=0?'+':'')+rupee(np);
    npEl.className='stat-value '+(np>=0?'green':'red');
    document.getElementById('j-pf').textContent=(j.profit_factor||0).toFixed(2);
    document.getElementById('j-avgwin').textContent=rupee(j.avg_win||0);
    document.getElementById('j-avgloss').textContent=rupee(j.avg_loss||0);
    document.getElementById('j-avgscore').textContent=(j.avg_score||0).toFixed(1)+'/100';
    document.getElementById('j-avghold').textContent=(j.avg_hold_days||0).toFixed(1)+' days';

    // Cumulative P&L chart
    const cumData=j.cumulative_pnl||[];
    const cumCtx=document.getElementById('j-chart-cumulative');
    if(cumCtx){
      const cfg={type:'line',data:{
        labels:cumData.map(d=>d.date),
        datasets:[{label:'Net P&L',data:cumData.map(d=>d.cumulative_pnl),
          borderColor:'#3b82f6',backgroundColor:'#3b82f611',fill:true,tension:0.4,pointRadius:2}]
      },options:{scales:{x:{ticks:{color:'#4b5563',font:{size:9},maxRotation:45}},y:{ticks:{color:'#4b5563',callback:v=>'₹'+v.toLocaleString('en-IN')}}},plugins:{legend:{display:false}},maintainAspectRatio:false}};
      if(jChartCumulative){jChartCumulative.data=cfg.data;jChartCumulative.update();}else{jChartCumulative=new Chart(cumCtx,cfg);}
    }

    // Score bucket bar chart
    const sb=j.by_score_bucket||{};
    const sbLabels=Object.keys(sb);
    const sbWR=sbLabels.map(k=>sb[k].win_rate||0);
    const sbPnl=sbLabels.map(k=>sb[k].net_pnl||0);
    const sbCtx=document.getElementById('j-chart-scorebucket');
    if(sbCtx&&sbLabels.length){
      const cfg={type:'bar',data:{
        labels:sbLabels,
        datasets:[
          {label:'Win Rate %',data:sbWR,backgroundColor:sbWR.map(v=>v>=60?'#16a34a88':'#dc262688'),borderRadius:4,yAxisID:'y'},
          {label:'Net P&L',data:sbPnl,type:'line',borderColor:'#60a5fa',pointRadius:3,yAxisID:'y2'}
        ]
      },options:{scales:{
        x:{ticks:{color:'#4b5563'}},
        y:{ticks:{color:'#4b5563',callback:v=>v+'%'},max:100,min:0,title:{display:true,text:'Win Rate %',color:'#4b5563'}},
        y2:{position:'right',ticks:{color:'#60a5fa',callback:v=>'₹'+v},grid:{drawOnChartArea:false}}
      },plugins:{legend:{labels:{color:'#9ca3af',font:{size:11}}}},maintainAspectRatio:false}};
      if(jChartScoreBucket){jChartScoreBucket.data=cfg.data;jChartScoreBucket.update();}else{jChartScoreBucket=new Chart(sbCtx,cfg);}
    }

    // Sector bar chart
    const sec=j.by_sector||{};
    const secL=Object.keys(sec);
    const secPnl=secL.map(k=>sec[k].net_pnl||0);
    const secCtx2=document.getElementById('j-chart-sector');
    if(secCtx2&&secL.length){
      const cfg={type:'bar',data:{
        labels:secL,
        datasets:[{label:'Net P&L',data:secPnl,backgroundColor:secPnl.map(v=>v>=0?'#16a34a88':'#dc262688'),borderRadius:4}]
      },options:{scales:{x:{ticks:{color:'#4b5563'}},y:{ticks:{color:'#4b5563',callback:v=>'₹'+v}}},plugins:{legend:{display:false}},maintainAspectRatio:false,indexAxis:'y'}};
      if(jChartSector){jChartSector.data=cfg.data;jChartSector.update();}else{jChartSector=new Chart(secCtx2,cfg);}
    }

    // Exit reason chart
    const ex=j.by_exit_reason||{};
    const exL=Object.keys(ex);
    const exPnl=exL.map(k=>ex[k].net_pnl||0);
    const exCtx=document.getElementById('j-chart-exit');
    if(exCtx&&exL.length){
      const cfg={type:'bar',data:{
        labels:exL,
        datasets:[{label:'Net P&L',data:exPnl,backgroundColor:exPnl.map(v=>v>=0?'#16a34a88':'#dc262688'),borderRadius:4}]
      },options:{scales:{x:{ticks:{color:'#4b5563'}},y:{ticks:{color:'#4b5563',callback:v=>'₹'+v}}},plugins:{legend:{display:false}},maintainAspectRatio:false}};
      if(jChartExit){jChartExit.data=cfg.data;jChartExit.update();}else{jChartExit=new Chart(exCtx,cfg);}
    }

    // Day of week chart
    const dow=j.by_day_of_week||{};
    const DOW_ORDER=['Monday','Tuesday','Wednesday','Thursday','Friday'];
    const dowL=DOW_ORDER.filter(d=>dow[d]);
    const dowPnl=dowL.map(d=>dow[d].net_pnl||0);
    const dowCtx=document.getElementById('j-chart-dow');
    if(dowCtx&&dowL.length){
      const cfg={type:'bar',data:{
        labels:dowL.map(d=>d.slice(0,3)),
        datasets:[{label:'Net P&L',data:dowPnl,backgroundColor:dowPnl.map(v=>v>=0?'#16a34a88':'#dc262688'),borderRadius:4}]
      },options:{scales:{x:{ticks:{color:'#4b5563'}},y:{ticks:{color:'#4b5563',callback:v=>'₹'+v}}},plugins:{legend:{display:false}},maintainAspectRatio:false}};
      if(jChartDow){jChartDow.data=cfg.data;jChartDow.update();}else{jChartDow=new Chart(dowCtx,cfg);}
    }

    // Regime chart
    const reg=j.by_regime||{};
    const regL=Object.keys(reg);
    const regWR=regL.map(k=>reg[k].win_rate||0);
    const regCtx=document.getElementById('j-chart-regime');
    if(regCtx&&regL.length){
      const cfg={type:'bar',data:{
        labels:regL,
        datasets:[{label:'Win Rate %',data:regWR,backgroundColor:regWR.map(v=>v>=60?'#16a34a88':'#ca8a0488'),borderRadius:4}]
      },options:{scales:{x:{ticks:{color:'#4b5563'}},y:{ticks:{color:'#4b5563',callback:v=>v+'%'},max:100,min:0}},plugins:{legend:{display:false}},maintainAspectRatio:false}};
      if(jChartRegime){jChartRegime.data=cfg.data;jChartRegime.update();}else{jChartRegime=new Chart(regCtx,cfg);}
    }

    // Trade log table — merge open + closed trades, newest first
    const closedTrades=j.recent_trades||[];
    const openTrades=j.open_trade_log||[];
    // Mark open trades so we can badge them
    openTrades.forEach(t=>{t._is_open=true;});
    const allTrades=[...openTrades,...closedTrades];
    const jTbl=document.getElementById('j-trade-log');
    if(allTrades.length){
      jTbl.innerHTML=allTrades.map(t=>{
        const isOpen=t._is_open||t.status==='OPEN';
        const pnl=parseFloat(t.net_pnl||0);
        const sc=parseFloat(t.trade_score||0);
        const sentiment=(t.sentiment||'').toUpperCase();
        const sentCol=sentiment==='POSITIVE'?'#22c55e':sentiment==='NEGATIVE'?'#ef4444':'#9ca3af';
        const regCol=t.market_regime==='BULL'?'#22c55e':t.market_regime==='BEAR'?'#ef4444':'#eab308';
        const reentryBadge=t.is_reentry?'<span style="background:#7c3aed;color:#fff;font-size:9px;padding:1px 5px;border-radius:6px;margin-left:4px">RE-ENTRY</span>':'';
        const statusCell=isOpen
          ? '<span style="background:#1d4ed8;color:#fff;font-size:10px;padding:2px 6px;border-radius:6px;font-weight:700">📂 OPEN</span>'
          : '<span style="background:#166534;color:#fff;font-size:10px;padding:2px 6px;border-radius:6px">✅ CLOSED</span>';
        const pnlCell=isOpen
          ? '<td style="color:#60a5fa;font-weight:700">holding</td>'
          : `<td class="${pnlClass(pnl)}" style="font-weight:700">${(pnl>=0?'+':'')+rupee(pnl)}</td>`;
        return `<tr>
          <td style="color:#6b7280;white-space:nowrap">${isOpen?(t.date||'—'):(t.exit_date||t.date||'—')}</td>
          <td style="font-weight:700;color:#f9fafb">${t.symbol}${reentryBadge}</td>
          <td>${statusCell}</td>
          <td style="color:${scoreColor(sc)};font-weight:700;text-align:center">${sc||'—'}</td>
          <td style="color:${regCol};font-size:11px;text-align:center">${t.market_regime||'—'}</td>
          <td style="color:#9ca3af;font-size:12px">${t.sector||'—'}</td>
          <td>${rupee(t.entry_price||0)}</td>
          <td>${isOpen?'<span style="color:#4b5563">—</span>':(t.exit_price?rupee(t.exit_price):'—')}</td>
          <td style="text-align:center">${isOpen?((t.holding_days!=null?t.holding_days:0)+'d ongoing'):(t.holding_days!=null?t.holding_days+'d':'—')}</td>
          <td style="color:${sentCol};font-size:11px;text-align:center">${sentiment||'—'}</td>
          <td style="text-align:center;font-size:12px">${t.rsi?t.rsi.toFixed(0):'—'}</td>
          <td style="font-size:11px">${t.trend||'—'}</td>
          <td style="font-size:11px;color:#9ca3af;max-width:160px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${t.exit_reason||t.buy_reason||''}">${isOpen?'<span style="color:#4b5563">holding</span>':(t.exit_reason||'—')}</td>
          ${pnlCell}
        </tr>`;
      }).join('');
    }else{
      jTbl.innerHTML='<tr><td colspan="14" style="text-align:center;color:#4b5563;padding:20px">No trades yet — journal will populate after the first order executes</td></tr>';
    }
  }catch(e){console.error('Journal error:',e);}
}

load();
loadJournal();
setInterval(load,60000);
setInterval(loadJournal,120000);

// ─── Ask AI Chat ──────────────────────────────────────────────────────────────
function chipAsk(el){ document.getElementById('chat-input').value=el.textContent; sendChat(); }

async function sendChat(){
  const inp = document.getElementById('chat-input');
  const btn = document.getElementById('chat-send-btn');
  const msgs = document.getElementById('chat-msgs');
  const question = inp.value.trim();
  if(!question) return;

  // Show user bubble
  msgs.innerHTML += `<div class="msg-user">${escHtml(question)}</div>`;
  inp.value='';
  btn.disabled=true;
  document.getElementById('ai-status-dot').style.background='#eab308';

  // Show typing indicator
  const typingId='typing-'+Date.now();
  msgs.innerHTML += `<div class="msg-ai" id="${typingId}">
    <div class="ai-label">AI Assistant</div>
    <span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>
  </div>`;
  msgs.scrollTop=msgs.scrollHeight;

  try{
    const res = await fetch('/api/ask', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({question})
    });
    const data = await res.json();
    document.getElementById(typingId).remove();
    msgs.innerHTML += renderAIMessage(data);
  }catch(e){
    document.getElementById(typingId).remove();
    msgs.innerHTML += `<div class="msg-ai"><div class="ai-label">AI Assistant</div><span class="red">Error: could not reach server.</span></div>`;
  }
  btn.disabled=false;
  document.getElementById('ai-status-dot').style.background='#22c55e';
  msgs.scrollTop=msgs.scrollHeight;
}

function escHtml(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function renderAIMessage(d){
  if(d.error) return `<div class="msg-ai"><div class="ai-label">AI Assistant</div><span class="red">${escHtml(d.error)}</span></div>`;

  let body='<div class="ai-label">AI Assistant</div>';

  // Main answer text
  if(d.answer) body+=`<div style="margin-bottom:10px">${escHtml(d.answer)}</div>`;

  // Structured sections: action, score, regime, indicators, sentiment, exit, expected_return
  if(d.action){
    const cls = d.action==='BUY'?'ai-pill-green':d.action==='SELL'?'ai-pill-red':'ai-pill-yellow';
    body+=`<div class="ai-section"><span class="ai-pill ${cls}">${d.action}</span></div>`;
  }
  if(d.trade_score!=null){
    const col=d.trade_score>=80?'ai-pill-green':d.trade_score>=60?'ai-pill-yellow':'ai-pill-red';
    body+=`<div class="ai-section"><div class="ai-section-title">Trade Score</div><span class="ai-pill ${col}">${d.trade_score}/100</span></div>`;
  }
  if(d.regime){
    const col=d.regime==='BULL'?'ai-pill-green':d.regime==='BEAR'?'ai-pill-red':'ai-pill-yellow';
    body+=`<div class="ai-section"><div class="ai-section-title">Market Regime</div><span class="ai-pill ${col}">${d.regime}</span></div>`;
  }
  if(d.indicators && Object.keys(d.indicators).length){
    body+='<div class="ai-section"><div class="ai-section-title">Indicators</div>';
    for(const [k,v] of Object.entries(d.indicators)){
      const col=v.signal==='bullish'?'ai-pill-green':v.signal==='bearish'?'ai-pill-red':'ai-pill-gray';
      body+=`<span class="ai-pill ${col}" title="${escHtml(v.detail||'')}">${escHtml(k)}: ${escHtml(String(v.value))}</span>`;
    }
    body+='</div>';
  }
  if(d.sentiment){
    const col=d.sentiment==='POSITIVE'?'ai-pill-green':d.sentiment==='NEGATIVE'?'ai-pill-red':'ai-pill-gray';
    body+=`<div class="ai-section"><div class="ai-section-title">News Sentiment</div><span class="ai-pill ${col}">${d.sentiment}</span>`;
    if(d.sentiment_score!=null) body+=` <span class="ai-pill ai-pill-gray">Score: ${d.sentiment_score}</span>`;
    body+='</div>';
  }
  if(d.exit_reasons && d.exit_reasons.length){
    body+='<div class="ai-section"><div class="ai-section-title">Exit Triggers</div>';
    d.exit_reasons.forEach(r=>{ body+=`<span class="ai-pill ai-pill-red">${escHtml(r)}</span>`; });
    body+='</div>';
  }
  if(d.expected_return!=null){
    const col=d.expected_return>=0?'ai-pill-green':'ai-pill-red';
    body+=`<div class="ai-section"><div class="ai-section-title">Expected Return</div><span class="ai-pill ${col}">${d.expected_return>=0?'+':''}${d.expected_return}%</span></div>`;
  }
  if(d.bullets && d.bullets.length){
    body+='<ul style="margin-top:8px;padding-left:18px;list-style:disc">';
    d.bullets.forEach(b=>{ body+=`<li style="margin-bottom:4px;color:#d1d5db">${escHtml(b)}</li>`; });
    body+='</ul>';
  }
  return `<div class="msg-ai">${body}</div>`;
}
</script>
</body></html>"""

def get_kite():
    try:
        from token_manager import TokenManager
        return TokenManager().initialize_kite()
    except Exception:
        return None


@app.route('/')
def index():
    return render_template_string(HTML)


@app.route('/api/data')
def api_data():
    from config import config
    kite = get_kite()
    now_ist = datetime.now(IST)
    today_str = now_ist.strftime("%Y-%m-%d")

    data = {
        "timestamp": now_ist.isoformat(),
        "market_open": False,
        "kite_ok": kite is not None,
        "paper_trading": config.PAPER_TRADING,
        "trading_mode": config.TRADING_MODE,
        "token_expiry": "—",
        "cash": 0,
        "invested": 0,
        "daily_pnl": 0,
        "weekly_pnl": 0,
        "monthly_pnl": 0,
        "weekly_win_rate": 0,
        "monthly_win_rate": 0,
        "weekly_calendar": [0, 0, 0, 0, 0],
        "open_positions": 0,
        "win_rate": 0,
        "total_trades": 0,
        "budget": config.TRADING_AMOUNT,
        "cfg_trading_amount": config.TRADING_AMOUNT,
        "cfg_max_positions": config.MAX_POSITIONS,
        "cfg_min_confidence": config.MIN_CONFIDENCE,
        "cfg_sl_pct": config.SWING_STOP_LOSS_PERCENTAGE if config.TRADING_MODE == 'swing' else config.STOP_LOSS_PERCENTAGE,
        "cfg_tgt_pct": config.SWING_TARGET_PERCENTAGE if config.TRADING_MODE == 'swing' else config.TARGET_PERCENTAGE,
        "cfg_max_capital": config.MAX_CAPITAL_USAGE,
        "cfg_daily_loss": config.DAILY_MAX_LOSS_PCT,
        "cfg_risk_per_trade": config.RISK_PER_TRADE,
        "cfg_swing_max_hold_days": config.SWING_MAX_HOLD_DAYS,
        "cfg_reentry_cooldown_hours": config.REENTRY_COOLDOWN_HOURS,
        "positions": [],
        "signals": [],
        "orders": [],
        "holdings": [],
        "recommendations": [],
        "analytics": {},
        "market_regime": "UNKNOWN",
        "portfolio_health": {
            "account_value": 0,
            "peak_value": 0,
            "drawdown": 0
        },
        "strategy_stats": {
            "avg_win": 0,
            "avg_loss": 0,
            "profit_factor": 0,
            "expectancy": 0
        },
        "market_summary": {
            "nifty_change": 0,
            "banknifty_change": 0,
            "vix": 0,
            "market_regime": "UNKNOWN"
        }
    }

    if not kite:
        return jsonify(data)

    # Token expiry
    try:
        token_path = os.path.join(os.path.dirname(__file__), 'data', 'kite_token.json')
        if os.path.exists(token_path):
            with open(token_path) as f:
                tok = json.load(f)
            data['token_expiry'] = tok.get('expiry', '—')[:16]
    except Exception:
        pass

    # Market open and regime
    from market_data import MarketDataFetcher
    mdf = MarketDataFetcher(kite=kite)
    data['market_open'] = mdf.is_market_open()
    try:
        from market_regime import MarketRegimeDetector
        regime_det = MarketRegimeDetector(kite=kite)
        data['market_regime'] = regime_det.detect_regime()
        data['market_summary']['market_regime'] = data['market_regime']
    except Exception:
        pass

    # Market summary: NIFTY, BANKNIFTY, VIX
    try:
        def get_index_change(symbol):
            token = mdf._get_instrument_token(symbol)
            if token:
                q = kite.quote([token])
                info = q.get(str(token), {})
                last = info.get('last_price', 0)
                net_change = info.get('net_change', 0)
                prev_close = last - net_change
                if prev_close > 0:
                    return (net_change / prev_close) * 100
            return 0
        nifty_change = get_index_change('NIFTY 50')
        banknifty_change = get_index_change('NIFTY BANK') or get_index_change('BANKNIFTY')
        vix_token = mdf._get_instrument_token('INDIA VIX')
        vix = 0
        if vix_token:
            q = kite.quote([vix_token])
            vix = q.get(str(vix_token), {}).get('last_price', 0)
        data['market_summary'] = {
            "nifty_change": nifty_change,
            "banknifty_change": banknifty_change,
            "vix": vix,
            "market_regime": data['market_regime']
        }
    except Exception:
        pass

    # Margins / cash / account balance
    try:
        margins = kite.margins()
        eq = margins.get("equity", {})
        avail = eq.get("available", {})
        cash = avail.get("live_balance") or avail.get("cash") or eq.get("net", 0)
        used = eq.get("utilised", {})
        margin_blocked = used.get("debits", 0) or sum(used.values())
        account_balance = eq.get("net", cash + margin_blocked)
        data['cash'] = cash
        data['account_balance'] = account_balance
        data['margin_blocked'] = margin_blocked
        data['net_portfolio_value'] = account_balance
        data['budget'] = config.TRADING_AMOUNT
    except Exception:
        pass

    # Positions (intraday — separate try so holdings still load if positions() fails)
    net_pos = []
    try:
        pos_data = kite.positions()
        net_pos = [p for p in pos_data.get('net', []) if p.get('quantity', 0) != 0]
    except Exception:
        pass

    # Holdings (delivery CNC — merged into positions list for unified display)
    try:
        holdings_raw = kite.holdings()
        data['holdings'] = holdings_raw
        holdings_as_pos = []
        pos_symbols = {p.get('tradingsymbol') for p in net_pos}
        for h in holdings_raw:
            effective_qty = (h.get('quantity', 0) or 0) + (h.get('t1_quantity', 0) or 0)
            if effective_qty == 0:
                continue
            h['quantity'] = effective_qty  # normalise so JS sees correct qty
            if h.get('tradingsymbol') in pos_symbols:
                continue  # already in net positions
            holdings_as_pos.append({
                'tradingsymbol':  h.get('tradingsymbol'),
                'exchange':       h.get('exchange', 'BSE'),
                'product':        h.get('product', 'CNC'),
                'quantity':       effective_qty,
                'average_price':  h.get('average_price', 0),
                'last_price':     h.get('last_price', h.get('close_price', 0)),
                'close_price':    h.get('close_price', 0),
                'pnl':            h.get('pnl', 0),
                'day_change':     h.get('day_change', 0),
                'day_change_percentage': h.get('day_change_percentage', 0),
                'overnight_quantity': h.get('opening_quantity', 0),
                'value':          h.get('average_price', 0) * effective_qty,
                '_source':        'holding',
            })
        all_positions = net_pos + holdings_as_pos
        data['positions'] = all_positions
        data['open_positions'] = len(all_positions)
        data['daily_pnl'] = sum(p.get('pnl', 0) for p in all_positions)
        data['invested'] = sum(p.get('average_price', 0) * p.get('quantity', 0) for p in all_positions)

        # Enrich positions with re-entry metadata from trade journal
        try:
            _jpath = os.path.join(os.path.dirname(__file__), 'data', 'trade_journal.json')
            with open(_jpath) as _jf:
                _jentries = json.load(_jf)
            _buy_entries = [e for e in _jentries if e.get('action') == 'BUY']
            for pos in all_positions:
                sym = pos.get('tradingsymbol')
                sym_buys = [e for e in _buy_entries if e.get('symbol') == sym]
                if sym_buys:
                    sym_buys_sorted = sorted(sym_buys, key=lambda x: x.get('timestamp', ''))
                    first = sym_buys_sorted[0]
                    last  = sym_buys_sorted[-1]
                    reentry_count = sum(1 for e in sym_buys if e.get('is_reentry'))
                    pos['first_entry_price']  = first.get('entry_price', pos.get('average_price', 0))
                    pos['entry_date']         = first.get('date', '')
                    pos['reentry_count']      = reentry_count
                    pos['is_reentry']         = last.get('is_reentry', False)
                    pos['prev_exit_reason']   = last.get('prev_exit_reason', '')
                    pos['reentry_score']      = last.get('reentry_score', 0)
                    pos['reentry_confidence'] = last.get('reentry_confidence', 0)
                    if pos['entry_date']:
                        from datetime import date as _date
                        try:
                            _entry_dt = datetime.strptime(pos['entry_date'], '%Y-%m-%d').date()
                            pos['days_held'] = (datetime.now().date() - _entry_dt).days
                        except Exception:
                            pos['days_held'] = 0
                    else:
                        pos['days_held'] = 0
        except Exception:
            pass
    except Exception:
        pass

    # Orders and performance
    try:
        orders = kite.orders()
        pending_statuses = {'OPEN', 'TRIGGER PENDING', 'PENDING'}
        data['pending_orders'] = len([o for o in orders if o.get('status', '').upper() in pending_statuses])
        try:
            data['gtt_orders'] = len(kite.get_gtts())
        except Exception:
            data['gtt_orders'] = 0
        
        # FIFO P&L calculator: matches each sell with oldest available buys per symbol
        def calculate_pnl(all_orders):
            sorted_orders = sorted(
                [o for o in all_orders if o.get('status') == 'COMPLETE'],
                key=lambda x: str(x.get('order_timestamp', ''))
            )
            holdings = {}  # symbol -> list of (qty, price) remaining
            order_pnl = {}
            for o in sorted_orders:
                sym = o.get('tradingsymbol')
                qty = int(o.get('quantity', 0))
                price = float(o.get('average_price', 0) or o.get('price', 0))
                order_id = o.get('order_id', '')
                if o.get('transaction_type') == 'BUY':
                    holdings.setdefault(sym, []).append([qty, price])
                    order_pnl[order_id] = 0.0
                elif o.get('transaction_type') == 'SELL':
                    total_pnl = 0.0
                    remaining = qty
                    while remaining > 0 and holdings.get(sym):
                        lot = holdings[sym][0]
                        lot_qty, lot_price = lot[0], lot[1]
                        use = min(remaining, lot_qty)
                        total_pnl += (price - lot_price) * use
                        lot[0] -= use
                        remaining -= use
                        if lot[0] <= 0:
                            holdings[sym].pop(0)
                    order_pnl[order_id] = total_pnl
            return order_pnl
        
        # Attach P&L to all orders
        all_order_pnl = calculate_pnl(orders)
        for o in orders:
            o['pnl'] = all_order_pnl.get(o.get('order_id', ''), 0.0)
        
        # All completed orders (for trade history tab) + today's orders
        all_completed = [o for o in orders if o.get('status') == 'COMPLETE']
        # Merge journal entries for orders not already in Kite's list (covers manual buys + past sessions)
        journal_path = os.path.join(os.path.dirname(__file__), 'data', 'trade_journal.json')
        try:
            with open(journal_path) as _jf:
                journal_entries = json.load(_jf)
            kite_ids = {o.get('order_id') for o in all_completed}
            for je in journal_entries:
                ts = str(je.get('date', '')) + ' 09:00:00'
                if je.get('kite_order_id') not in kite_ids:
                    all_completed.append({
                        'tradingsymbol': je.get('symbol'),
                        'transaction_type': je.get('action', 'BUY'),
                        'quantity': je.get('quantity', 0),
                        'average_price': je.get('entry_price', 0),
                        'order_timestamp': ts,
                        'status': 'COMPLETE',
                        'pnl': je.get('net_pnl') or 0.0,
                        'order_id': je.get('kite_order_id', ''),
                        '_source': 'journal',
                    })
        except Exception:
            pass
        data['all_orders'] = sorted(all_completed, key=lambda x: str(x.get('order_timestamp', '')), reverse=True)
        # Today's orders
        data['orders'] = [o for o in orders if str(o.get('order_timestamp', '')).startswith(today_str)]
        completed = [o for o in data['orders'] if o.get('status') == 'COMPLETE']
        buys  = [o for o in completed if o.get('transaction_type') == 'BUY']
        sells = [o for o in completed if o.get('transaction_type') == 'SELL']
        data['total_trades'] = len(completed)
        data['win_rate'] = len(sells) / len(buys) if buys else 0
        data['daily_pnl'] = sum(o.get('pnl', 0) for o in sells)
        
        # Weekly / Monthly performance
        week_ago = (now_ist - timedelta(days=7)).strftime("%Y-%m-%d")
        month_ago = (now_ist - timedelta(days=30)).strftime("%Y-%m-%d")
        week_orders = [o for o in orders if str(o.get('order_timestamp', '')) >= week_ago and o.get('status') == 'COMPLETE']
        month_orders = [o for o in orders if str(o.get('order_timestamp', '')) >= month_ago and o.get('status') == 'COMPLETE']
        w_buys = [o for o in week_orders if o.get('transaction_type') == 'BUY']
        w_sells = [o for o in week_orders if o.get('transaction_type') == 'SELL']
        m_buys = [o for o in month_orders if o.get('transaction_type') == 'BUY']
        m_sells = [o for o in month_orders if o.get('transaction_type') == 'SELL']
        data['weekly_win_rate'] = len(w_sells) / len(w_buys) if w_buys else 0
        data['monthly_win_rate'] = len(m_sells) / len(m_buys) if m_buys else 0
        data['weekly_pnl'] = sum(o.get('pnl', 0) for o in w_sells)
        data['monthly_pnl'] = sum(o.get('pnl', 0) for o in m_sells)

        # Realized P&L per weekday for the trade calendar (Mon-Fri)
        try:
            weekday_pnl = [0.0, 0.0, 0.0, 0.0, 0.0]  # Mon..Fri
            week_start = now_ist.date() - timedelta(days=now_ist.weekday())  # Monday of this week
            for o in data.get('all_orders', []):
                if o.get('transaction_type') != 'SELL':
                    continue
                ts = str(o.get('order_timestamp', ''))
                try:
                    if 'T' in ts:
                        od = datetime.fromisoformat(ts.replace('Z', '+00:00')).date()
                    elif ' ' in ts:
                        od = datetime.strptime(ts[:10], '%Y-%m-%d').date()
                    else:
                        od = datetime.strptime(ts[:10], '%Y-%m-%d').date()
                except Exception:
                    continue
                if week_start <= od <= (week_start + timedelta(days=4)):
                    idx = od.weekday()  # Monday=0 .. Friday=4
                    weekday_pnl[idx] += float(o.get('pnl', 0) or 0)
            data['weekly_calendar'] = weekday_pnl
        except Exception:
            pass

        # Portfolio health
        positions_value = sum(p.get('last_price', 0) * p.get('quantity', 0) for p in data.get('positions', []))
        account_value = data.get('account_balance', 0) + positions_value + data.get('holdings_value', 0)
        peak_file = os.path.join(os.path.dirname(__file__), 'data', 'peak_value.json')
        peak_value = account_value
        try:
            if os.path.exists(peak_file):
                with open(peak_file) as f:
                    saved = json.load(f)
                    saved_peak = saved.get('peak_value', account_value)
                    saved_date = saved.get('date', '')
                    # Reset peak at the start of each trading day
                    if saved_date == today_str:
                        peak_value = max(saved_peak, account_value)
        except Exception:
            pass
        try:
            os.makedirs(os.path.dirname(peak_file), exist_ok=True)
            with open(peak_file, 'w') as f:
                json.dump({"peak_value": peak_value, "date": today_str}, f)
        except Exception:
            pass
        drawdown = (peak_value - account_value) / peak_value if peak_value > 0 else 0
        data['portfolio_health'] = {
            "account_value": round(account_value, 2),
            "peak_value": round(peak_value, 2),
            "drawdown": round(drawdown * 100, 2)
        }
        
        # Strategy statistics (all-time complete sells)
        all_sells = [o for o in orders if o.get('status') == 'COMPLETE' and o.get('transaction_type') == 'SELL']
        sell_pnls = [o.get('pnl', 0) for o in all_sells]
        wins = [p for p in sell_pnls if p > 0]
        losses = [p for p in sell_pnls if p < 0]
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(abs(p) for p in losses) / len(losses) if losses else 0
        total_wins = sum(wins)
        total_losses = sum(abs(p) for p in losses)
        profit_factor = total_wins / total_losses if total_losses > 0 else 0
        total_sells = len(all_sells)
        win_rate = len(wins) / total_sells if total_sells > 0 else 0
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss) if total_sells > 0 else 0
        data['strategy_stats'] = {
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "expectancy": round(expectancy, 2)
        }
    except Exception:
        pass

    # Delivery holdings
    try:
        holdings = kite.holdings()
        # Normalize quantity to include T1 (stocks bought yesterday show qty=0 otherwise)
        for h in holdings:
            h['quantity'] = (h.get('quantity', 0) or 0) + (h.get('t1_quantity', 0) or 0)
        data['holdings'] = holdings
        holdings_value = sum(h.get('quantity', 0) * h.get('last_price', 0) for h in holdings)
        data['holdings_value'] = holdings_value
        # True portfolio = Kite net margin (cash+collateral) + current market value of stocks
        data['net_portfolio_value'] = data.get('account_balance', 0) + holdings_value
    except Exception:
        pass

    # Portfolio analytics
    try:
        positions = data['positions']
        analytics = {
            "exposure": 0.0,
            "risk": 0.0,
            "potential_profit": 0.0,
            "potential_loss": 0.0,
            "risk_reward": 0.0,
            "portfolio_return": 0.0,
            "best_stock": "—",
            "worst_stock": "—",
            "sector_allocation": {}
        }
        if positions:
            total_exposure = 0.0
            total_risk = 0.0
            total_potential_profit = 0.0
            total_value = 0.0
            best_pct = -999
            worst_pct = 999
            best_stock = "—"
            worst_stock = "—"
            sector_map = {}
            
            for p in positions:
                qty = p.get('quantity', 0)
                avg = p.get('average_price', 0)
                ltp = p.get('last_price', avg)
                exposure = qty * avg
                unrealized_pct = ((ltp - avg) / avg * 100) if avg else 0
                
                total_exposure += exposure
                total_value += qty * ltp
                
                # Use SL/target from position if available, otherwise estimate
                sl = p.get('stop_loss', avg * 0.95)
                target = p.get('target', avg * 1.10)
                risk_per_share = abs(avg - sl)
                reward_per_share = abs(target - avg)
                total_risk += risk_per_share * qty
                total_potential_profit += reward_per_share * qty
                
                if unrealized_pct > best_pct:
                    best_pct = unrealized_pct
                    best_stock = f"{p.get('tradingsymbol', '')} ({unrealized_pct:+.2f}%)"
                if unrealized_pct < worst_pct:
                    worst_pct = unrealized_pct
                    worst_stock = f"{p.get('tradingsymbol', '')} ({unrealized_pct:+.2f}%)"
                
                # Simple sector mapping based on known symbols
                sector = SECTOR_MAP.get(p.get('tradingsymbol', ''), 'Other')
                sector_map[sector] = sector_map.get(sector, 0) + exposure
            
            analytics['exposure'] = total_exposure
            analytics['risk'] = total_risk
            analytics['potential_profit'] = total_potential_profit
            analytics['potential_loss'] = total_risk
            analytics['risk_reward'] = total_potential_profit / total_risk if total_risk else 0
            analytics['portfolio_return'] = ((total_value - total_exposure) / total_exposure * 100) if total_exposure else 0
            analytics['best_stock'] = best_stock
            analytics['worst_stock'] = worst_stock
            if sector_map:
                total = sum(sector_map.values())
                analytics['sector_allocation'] = {k: (v / total * 100) for k, v in sector_map.items()}
        
        data['analytics'] = analytics
    except Exception:
        pass

    # Live signals and recommendations (use swing or intraday parameters)
    try:
        now = datetime.now(IST)
        cache_age = (now - _SIGNAL_CACHE["timestamp"]) if _SIGNAL_CACHE["timestamp"] else timedelta.max
        if cache_age < _SIGNAL_CACHE_TTL:
            data['signals'] = _SIGNAL_CACHE["signals"]
            data['recommendations'] = _SIGNAL_CACHE["recommendations"]
            data['stocks_scanned'] = _SIGNAL_CACHE["stocks_scanned"]
        else:
            from dynamic_universe import DynamicUniverse
            from technical_analysis import TechnicalAnalyzer
            scanner  = DynamicUniverse(kite=kite)
            ta       = TechnicalAnalyzer()
            universe = scanner.get_candidates_with_details(top_n=30)
            signals  = []
            recommendations = []
            sl_pct = config.SWING_STOP_LOSS_PERCENTAGE if config.TRADING_MODE == "swing" else config.STOP_LOSS_PERCENTAGE
            tgt_pct = config.SWING_TARGET_PERCENTAGE if config.TRADING_MODE == "swing" else config.TARGET_PERCENTAGE
            for c in universe[:15]:
                sym = c['symbol']
                try:
                    hist = mdf.get_stock_data(sym, period="1mo", interval="1d")
                    if hist.empty or len(hist) < 20:
                        continue
                    sig = ta.generate_signals(hist)
                    if sig.get('signal') in ('BUY', 'SELL'):
                        sig_data = {
                            'symbol':     sym,
                            'price':      c['last_price'],
                            'target':     round(c['last_price'] * (1 + tgt_pct), 2),
                            'stop_loss':  round(c['last_price'] * (1 - sl_pct), 2),
                            'confidence': sig.get('confidence', 0),
                            'action':     sig.get('signal'),
                            'trend':      sig.get('trend', ''),
                        }
                        signals.append(sig_data)
                        if sig.get('signal') == 'BUY' and sig.get('confidence', 0) >= config.MIN_CONFIDENCE:
                            recommendations.append(sig_data)
                except Exception:
                    continue
            data['signals'] = signals
            data['recommendations'] = recommendations[:5]
            data['stocks_scanned'] = len(universe)
            _SIGNAL_CACHE.update({
                "signals": signals,
                "recommendations": recommendations[:5],
                "stocks_scanned": len(universe),
                "timestamp": now
            })
    except Exception:
        pass

    return jsonify(data)


@app.route('/api/ask', methods=['POST'])
def api_ask():
    """AI Chat endpoint — local-first engine, GPT optional."""
    try:
        from flask import request as freq
        from config import config
        from trade_journal import TradeJournal

        question = (freq.get_json(force=True) or {}).get('question', '').strip()
        if not question:
            return jsonify({'error': 'Empty question'})

        q = question.lower()

        # ── Load journal data ────────────────────────────────────────────────
        journal = TradeJournal()
        entries  = journal.all_entries()
        analytics = journal.analytics()

        closed     = [e for e in entries if e.get('status') == 'CLOSED']
        open_pos   = [e for e in entries if e.get('status') == 'OPEN']

        # ── Live market regime ───────────────────────────────────────────────
        regime = 'UNKNOWN'
        try:
            from market_regime import MarketRegimeDetector
            from market_data import MarketDataFetcher
            regime = MarketRegimeDetector(kite=MarketDataFetcher().kite).detect_regime()
        except Exception:
            pass

        # ── Helper: find most recent trade for a symbol ──────────────────────
        def find_trade(sym):
            sym = sym.upper()
            matches = [e for e in reversed(entries) if e.get('symbol','').upper() == sym]
            return matches[0] if matches else None

        # ── Helper: extract symbol from question ─────────────────────────────
        def extract_symbol():
            for e in entries:
                sym = e.get('symbol', '')
                if sym and sym.lower() in q:
                    return sym.upper()
            # also check common names
            name_map = {'reliance':'RELIANCE','bel':'BEL','tcs':'TCS','infy':'INFY',
                        'sbin':'SBIN','hdfc':'HDFCBANK','icici':'ICICIBANK',
                        'wipro':'WIPRO','itc':'ITC','ongc':'ONGC'}
            for k,v in name_map.items():
                if k in q:
                    return v
            return None

        # ════════════════════════════════════════════════════════════════════
        # LOCAL ANSWER ENGINE
        # ════════════════════════════════════════════════════════════════════

        # 1) WHY DID WE BUY <symbol>?
        if any(w in q for w in ['why did we buy','why buy','why bought','reason for buy','why we bought']):
            sym = extract_symbol()
            trade = find_trade(sym) if sym else (open_pos[-1] if open_pos else None)
            if not trade:
                return jsonify({'answer': f'No BUY trade found{"for " + sym if sym else ""}. No trades in journal yet.',
                                'bullets': ['Journal is empty or symbol not found']})
            rsi  = trade.get('rsi', 0) or 0
            macd = trade.get('macd_histogram', 0) or 0
            conf = (trade.get('confidence', 0) or 0) * 100
            score = trade.get('trade_score', 0) or 0
            reg  = trade.get('market_regime', 'UNKNOWN')
            senti = (trade.get('sentiment') or 'NEUTRAL').upper()
            reason = trade.get('buy_reason') or trade.get('reasoning') or 'Technical signal triggered'
            price = trade.get('entry_price', 0)
            return jsonify({
                'answer': f"We bought {trade['symbol']} at ₹{price:.2f} because the trade scored {score}/100 with {conf:.0f}% confidence in a {reg} market. {reason[:120]}",
                'action': 'BUY',
                'trade_score': score,
                'regime': reg,
                'indicators': {
                    'RSI':    {'value': round(rsi,1),  'signal': 'bullish' if rsi < 60 else 'bearish', 'detail': f'RSI={rsi:.1f}'},
                    'MACD':   {'value': round(macd,3), 'signal': 'bullish' if macd > 0 else 'bearish', 'detail': f'Histogram={macd:.3f}'},
                    'Volume': {'value': round(trade.get('volume_ratio',1),2), 'signal': 'bullish' if (trade.get('volume_ratio') or 1)>1 else 'neutral', 'detail': 'vs 20d avg'},
                },
                'sentiment': senti,
                'sentiment_score': trade.get('sentiment_score', 0),
                'bullets': [
                    f"Entry: ₹{price:.2f} | Score: {score}/100 | Confidence: {conf:.0f}%",
                    f"RSI: {rsi:.1f} | MACD histogram: {macd:.3f}",
                    f"Market regime: {reg} | Sentiment: {senti}",
                    f"Sector: {trade.get('sector','Unknown')} | MTF aligned: {trade.get('mtf_aligned', '?')}",
                ]
            })

        # 2) WHY DID WE SELL <symbol>?
        if any(w in q for w in ['why did we sell','why sell','why sold','exit reason','why we sold','why exit']):
            sym = extract_symbol()
            trade = find_trade(sym) if sym else (closed[-1] if closed else None)
            if not trade:
                return jsonify({'answer': 'No closed trade found. No exits in journal yet.',
                                'bullets': ['No closed trades in journal']})
            exit_r = trade.get('exit_reason') or 'Unknown exit trigger'
            entry  = trade.get('entry_price', 0)
            exit_p = trade.get('exit_price', 0) or 0
            net    = trade.get('net_pnl', 0) or 0
            pct    = ((exit_p - entry) / entry * 100) if entry else 0
            return jsonify({
                'answer': f"We exited {trade['symbol']} at ₹{exit_p:.2f} (entry ₹{entry:.2f}, {pct:+.1f}%). Exit trigger: {exit_r}. Net P&L: ₹{net:.2f}.",
                'action': 'SELL',
                'exit_reasons': [exit_r],
                'expected_return': round(pct, 2),
                'bullets': [
                    f"Entry: ₹{entry:.2f} → Exit: ₹{exit_p:.2f} ({pct:+.1f}%)",
                    f"Net P&L after charges: ₹{net:.2f}",
                    f"Exit trigger: {exit_r}",
                ]
            })

        # 3) MARKET REGIME
        if any(w in q for w in ['market regime','regime','bull','bear','sideways','market condition']):
            col = 'BULL' if regime == 'BULL' else ('BEAR' if regime == 'BEAR' else 'SIDEWAYS')
            desc = {'BULL': 'Nifty is above 50-DMA and trending up — bot is actively buying.',
                    'BEAR': 'Nifty is below 50-DMA — bot is NOT buying new positions, only exiting.',
                    'SIDEWAYS': 'Nifty is ranging — bot buys only highest-quality signals (score ≥70).',
                    'UNKNOWN': 'Regime could not be determined from Nifty data.'}.get(col, '')
            return jsonify({
                'answer': f"Current market regime is {regime}. {desc}",
                'regime': col,
                'bullets': [
                    f"Regime: {regime}",
                    desc,
                    'Bot pauses new BUYs only in BEAR regime.',
                    'Regime is re-checked every 15 minutes.',
                ]
            })

        # 4) WIN RATE / P&L SUMMARY
        if any(w in q for w in ['win rate','winrate','p&l','pnl','profit','summary','performance','how are we doing']):
            total  = analytics.get('total_trades', 0)
            wins   = analytics.get('winning_trades', 0)
            wr     = analytics.get('win_rate', 0)
            net    = analytics.get('total_net_pnl', 0)
            avg_w  = analytics.get('avg_win', 0)
            avg_l  = analytics.get('avg_loss', 0)
            pf     = analytics.get('profit_factor', 0)
            avg_sc = analytics.get('avg_score', 0)
            open_c = len(open_pos)
            if total == 0:
                return jsonify({'answer': 'No closed trades yet. The bot is still in early trading — check back after the first exits.',
                                'bullets': [f'Open positions: {open_c}', 'No closed trades to analyse yet']})
            return jsonify({
                'answer': f"Out of {total} closed trades, {wins} were winners — {wr:.1f}% win rate. Total net P&L: ₹{net:.2f}. Profit factor: {pf:.2f}x.",
                'bullets': [
                    f"Total closed trades: {total} | Win rate: {wr:.1f}%",
                    f"Avg win: ₹{avg_w:.2f} | Avg loss: ₹{avg_l:.2f}",
                    f"Profit factor: {pf:.2f}x | Avg score: {avg_sc:.0f}/100",
                    f"Open positions: {open_c} | Regime: {regime}",
                ]
            })

        # 5) OPEN POSITIONS
        if any(w in q for w in ['open position','current position','holding','what do we hold','what stocks']):
            if not open_pos:
                return jsonify({'answer': 'No open positions right now. The bot is waiting for high-quality BUY signals.',
                                'bullets': ['0 open positions', f'Market regime: {regime}', 'Bot scans 100 stocks every 15 min']})
            lines = []
            for p in open_pos:
                lines.append(f"{p['symbol']} — {p.get('quantity','?')} shares @ ₹{p.get('entry_price',0):.2f} | SL: ₹{p.get('stop_loss',0):.2f} | Target: ₹{p.get('target',0):.2f}")
            return jsonify({
                'answer': f"Currently holding {len(open_pos)} position(s): {', '.join(p['symbol'] for p in open_pos)}.",
                'bullets': lines
            })

        # 6) SECTOR PERFORMANCE
        if any(w in q for w in ['sector','industry','best sector','performing']):
            from collections import defaultdict
            sec_pnl = defaultdict(float)
            sec_cnt = defaultdict(int)
            for t in closed:
                sec = t.get('sector', 'Other')
                sec_pnl[sec] += t.get('net_pnl', 0) or 0
                sec_cnt[sec] += 1
            if not sec_pnl:
                return jsonify({'answer': 'No closed trades yet to rank sectors.',
                                'bullets': ['Trade more to see sector performance']})
            best = max(sec_pnl, key=sec_pnl.get)
            worst = min(sec_pnl, key=sec_pnl.get)
            bullets = [f"{s}: ₹{p:.2f} ({sec_cnt[s]} trades)" for s,p in sorted(sec_pnl.items(), key=lambda x:-x[1])]
            return jsonify({
                'answer': f"Best performing sector: {best} (₹{sec_pnl[best]:.2f}). Worst: {worst} (₹{sec_pnl[worst]:.2f}).",
                'bullets': bullets[:6]
            })

        # 7) SKIPPED TRADE
        if any(w in q for w in ['skipped','skip','not bought','why not','why was','rejected']):
            sym = extract_symbol()
            return jsonify({
                'answer': f"Trades are skipped if they fail any of the 7 filters: duplicate position, correlated sector, negative news, trade score <70/100, multi-timeframe not aligned, capital limit, or confidence <52%.",
                'bullets': [
                    '1. Already holding the stock',
                    '2. Same sector as existing position (correlation guard)',
                    '3. Negative news detected (fraud/SEBI/loss keywords)',
                    '4. Trade score < 70/100',
                    '5. Multi-timeframe not aligned (Daily+1H+15m)',
                    '6. Capital limit reached (95% deployed)',
                    '7. Confidence < 52% or R:R < 1:1',
                ]
            })

        # 8) INDICATORS
        if any(w in q for w in ['indicator','rsi','macd','volume','technical','which indicator']):
            if not closed:
                return jsonify({'answer': 'No closed trades yet to analyse indicators.',
                                'bullets': ['Trade more to see indicator stats']})
            bull_rsi = [t for t in closed if (t.get('rsi') or 0) < 60 and (t.get('net_pnl') or 0) > 0]
            bull_macd = [t for t in closed if (t.get('macd_histogram') or 0) > 0 and (t.get('net_pnl') or 0) > 0]
            return jsonify({
                'answer': f"RSI <60 at entry → {len(bull_rsi)}/{len(closed)} profitable. Positive MACD histogram at entry → {len(bull_macd)}/{len(closed)} profitable.",
                'bullets': [
                    f"RSI <60 at entry: {len(bull_rsi)} wins / {len(closed)} total",
                    f"Positive MACD histogram: {len(bull_macd)} wins / {len(closed)} total",
                    'Volume ratio >1 = above average volume (bullish confirmation)',
                    'MTF alignment = Daily UPTREND + 1H not DOWNTREND + 15m OK',
                ]
            })

        # ── FALLBACK: try GPT if quota available, else generic answer ────────
        try:
            import openai
            if not config.OPENAI_API_KEY:
                raise ValueError('No API key')
            client = openai.OpenAI(api_key=config.OPENAI_API_KEY)

            def fmt_trade(t):
                return (f"{t.get('action','?')} {t.get('symbol','?')} @ ₹{t.get('entry_price','?')} "
                        f"score={t.get('trade_score','?')} regime={t.get('market_regime','?')} "
                        f"rsi={t.get('rsi','?')} macd={t.get('macd_histogram','?')} "
                        f"conf={t.get('confidence','?')} senti={t.get('sentiment','?')} "
                        f"reason='{t.get('buy_reason','')}' exit='{t.get('exit_reason','')}' "
                        f"pnl=₹{t.get('net_pnl',0)}")

            ctx = (f"Regime: {regime}\n"
                   f"Open: {chr(10).join(fmt_trade(t) for t in open_pos) or 'none'}\n"
                   f"Closed (last 5): {chr(10).join(fmt_trade(t) for t in closed[-5:]) or 'none'}\n"
                   f"Stats: win_rate={analytics.get('win_rate',0)}% pnl=₹{analytics.get('total_net_pnl',0)} trades={analytics.get('total_trades',0)}")

            resp = client.chat.completions.create(
                model='gpt-4o-mini',
                messages=[
                    {'role': 'system', 'content': 'You are an AI assistant for an NSE swing trading bot. Answer in plain English. Return JSON with keys: answer, bullets (list). Do not use markdown fences.'},
                    {'role': 'user',   'content': f"Context:\n{ctx}\n\nQuestion: {question}"},
                ],
                temperature=0.3,
                max_tokens=400,
            )
            raw = resp.choices[0].message.content.strip()
            try:
                result = json.loads(raw)
            except Exception:
                result = {'answer': raw}
            return jsonify(result)

        except Exception as gpt_err:
            err_str = str(gpt_err)
            if 'quota' in err_str or 'insufficient' in err_str or '429' in err_str:
                hint = 'OpenAI quota exceeded — add credits at platform.openai.com/billing. Local answers above still work.'
            else:
                hint = f'Could not reach GPT: {err_str[:80]}'
            # Still give a useful generic local answer
            return jsonify({
                'answer': f"I couldn't find a specific answer for that question in my local data. {hint}",
                'bullets': [
                    f"Open positions: {len(open_pos)} ({', '.join(p['symbol'] for p in open_pos) or 'none'})",
                    f"Closed trades: {analytics.get('total_trades',0)} | Win rate: {analytics.get('win_rate',0):.1f}%",
                    f"Net P&L: ₹{analytics.get('total_net_pnl',0):.2f} | Regime: {regime}",
                    hint,
                ]
            })

    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'})


@app.route('/api/journal')
def api_journal():
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
        from trade_journal import TradeJournal
        j = TradeJournal()
        analytics = j.analytics()
        all_entries = j.all_entries()
        open_trades = [e for e in all_entries if e.get('action') == 'BUY' and e.get('status') == 'OPEN']
        analytics['open_trades_count'] = len(open_trades)
        analytics['open_trade_log'] = sorted(open_trades, key=lambda x: x.get('timestamp',''), reverse=True)[:20]
        analytics['all_entries_count'] = len(all_entries)
        return jsonify(analytics)
    except Exception as e:
        return jsonify({'error': str(e), 'total_trades': 0, 'open_trades_count': 0, 'open_trade_log': []})


if __name__ == '__main__':
    print("\n" + "="*55)
    print("  🤖 AI Trading Dashboard")
    print("  Open in browser: http://localhost:5001")
    print("  Auto-refreshes every 60 seconds")
    print("  Press Ctrl+C to stop")
    print("="*55 + "\n")
    app.run(host='0.0.0.0', port=5001, debug=False)
