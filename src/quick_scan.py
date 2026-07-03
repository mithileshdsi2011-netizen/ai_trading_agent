"""
Quick Scan — Live intraday BUY recommendations for today.
Runs independent of account balance.
Usage: ./run_with_venv.sh src/quick_scan.py
"""
import os, sys
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.insert(0, current_dir)
sys.path.insert(0, project_root)

from token_manager import TokenManager
from dynamic_universe import DynamicUniverse
from technical_analysis import TechnicalAnalyzer
from market_data import MarketDataFetcher
from config import config
from datetime import datetime
import pytz

IST = pytz.timezone("Asia/Kolkata")

BUDGET = 2000.0   # ← your intraday budget today

kite = TokenManager().initialize_kite()
mdf  = MarketDataFetcher(kite=kite)
ta   = TechnicalAnalyzer()

print("\n" + "="*68)
print("  📡 LIVE INTRADAY SCAN — BUY RECOMMENDATIONS FOR TODAY")
print(f"  {datetime.now(IST).strftime('%d %b %Y  %I:%M %p IST')}")
print(f"  Budget: ₹{BUDGET:,.0f}")
print("="*68)

# Step 1 — get live universe
scanner    = DynamicUniverse(kite=kite)
candidates = scanner.get_candidates_with_details(top_n=50)
print(f"\n  Scanning {len(candidates)} high-volume NSE stocks...")

buys = []
holds = []

for c in candidates:
    sym = c["symbol"]
    try:
        # Use 15-min intraday data for more accurate same-day signals
        hist = mdf.get_intraday_data(sym, days=10)
        if hist.empty or len(hist) < 25:
            hist = mdf.get_stock_data(sym, period="1mo", interval="1d")
        if hist.empty or len(hist) < 20:
            continue

        sig = ta.generate_signals(hist)
        signal   = sig.get("signal", "HOLD")
        score    = sig.get("technical_score", 0)
        conf     = sig.get("confidence", 0)
        trend    = sig.get("trend", "NEUTRAL")
        support  = sig.get("support", 0)
        resist   = sig.get("resistance", 0)
        reason   = sig.get("reason", "")

        price     = c["last_price"]
        momentum  = c["momentum_pct"]

        sl_pct    = config.STOP_LOSS_PERCENTAGE          # 2%
        tgt_pct   = config.TARGET_PERCENTAGE             # 4%

        stop_loss = price * (1 - sl_pct)
        if support > 0 and support < price:
            stop_loss = max(support, stop_loss)

        target = price * (1 + tgt_pct)
        if resist > 0 and resist > price:
            target = min(resist, target * 1.1)

        qty        = max(1, int(BUDGET / price))
        invest     = qty * price
        exp_profit = qty * (target - price)
        exp_loss   = qty * (price - stop_loss)
        rr         = round((target - price) / max(price - stop_loss, 0.01), 1)

        entry = {
            "symbol":     sym,
            "price":      price,
            "momentum":   momentum,
            "volume":     c["volume"],
            "score":      round(score, 3),
            "conf":       round(conf * 100),
            "trend":      trend,
            "stop_loss":  round(stop_loss, 2),
            "target":     round(target, 2),
            "qty":        qty,
            "invest":     round(invest, 2),
            "exp_profit": round(exp_profit, 2),
            "exp_loss":   round(exp_loss, 2),
            "rr":         rr,
            "reason":     reason,
        }
        if signal == "BUY":
            buys.append(entry)
        else:
            holds.append(entry)
    except Exception:
        continue

buys.sort(key=lambda x: (x["conf"], x["score"]), reverse=True)

# ── BUY Recommendations ───────────────────────────────────────────────────────
if buys:
    print(f"\n  🟢 STRONG BUY SIGNALS ({len(buys)} found)\n")
    print(f"  {'#':<3} {'Symbol':<13} {'Price':>8} {'Target':>8} {'Stop':>8} "
          f"{'Qty':>4} {'Invest':>8} {'Profit':>8} {'Loss':>7} {'R:R':>4} {'Conf':>5}")
    print("  " + "─"*88)
    for i, r in enumerate(buys[:5], 1):
        print(
            f"  {i:<3} {r['symbol']:<13} "
            f"₹{r['price']:>7.2f} "
            f"₹{r['target']:>7.2f} "
            f"₹{r['stop_loss']:>7.2f} "
            f"{r['qty']:>4} "
            f"₹{r['invest']:>7.0f} "
            f"+₹{r['exp_profit']:>6.0f} "
            f"-₹{r['exp_loss']:>5.0f} "
            f"{r['rr']:>3.1f}x "
            f"{r['conf']:>3}%"
        )
        print(f"       ↳ {r['trend']} trend | Vol: {r['volume']:,} | {r['reason']}")
    print("  " + "─"*88)

    # Top pick
    top = buys[0]
    print(f"""
  ⭐ TOP PICK FOR ₹2000 TODAY: {top['symbol']}

     Entry Price  : ₹{top['price']:.2f}
     Buy Qty      : {top['qty']} shares  (₹{top['invest']:.0f} total)
     Target       : ₹{top['target']:.2f}  (+{config.TARGET_PERCENTAGE*100:.0f}%)
     Stop Loss    : ₹{top['stop_loss']:.2f}  (-{config.STOP_LOSS_PERCENTAGE*100:.0f}%)
     Expected P&L : +₹{top['exp_profit']:.0f} profit / -₹{top['exp_loss']:.0f} max loss
     Risk:Reward  : 1:{top['rr']}
     Trend        : {top['trend']}
     Confidence   : {top['conf']}%
     EXIT BY      : {config.INTRADAY_CUTOFF} IST (mandatory intraday close)
""")
else:
    print("\n  ⚠️  No BUY signals right now. Market may be in consolidation.")
    print("  Showing best HOLD candidates that could turn BUY soon:\n")
    holds.sort(key=lambda x: x["score"], reverse=True)
    for r in holds[:5]:
        print(f"  WATCH  {r['symbol']:<13} ₹{r['price']:.2f}  "
              f"Momentum:{r['momentum']:+.1f}%  Score:{r['score']}")

# ── Market Context ────────────────────────────────────────────────────────────
now_ist = datetime.now(IST)
mins_left = (15*60 + 30) - (now_ist.hour*60 + now_ist.minute)
print(f"  ⏱  Market closes in {mins_left} minutes ({now_ist.strftime('%I:%M %p')} now)")
print(f"  ⚠️  All MIS (intraday) positions auto-squared off at 3:20 PM by Zerodha")
print("\n" + "="*68 + "\n")
