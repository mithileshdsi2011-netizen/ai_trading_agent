"""
Live Analysis — shows account balance, today's best BUY candidates,
expected targets/stop-losses, and trade history P&L.
Run: ./run_with_venv.sh src/live_analysis.py
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

def rupee(v): return f"₹{v:,.2f}"

# ── 1. Connect ────────────────────────────────────────────────────────────────
tm = TokenManager()
kite = tm.initialize_kite()

print("\n" + "="*65)
print("  AI TRADING AGENT — LIVE ANALYSIS DASHBOARD")
print(f"  {datetime.now(IST).strftime('%A, %d %b %Y  %I:%M %p IST')}")
print("="*65)

# ── 2. Account Balance ────────────────────────────────────────────────────────
print("\n📊 ACCOUNT SUMMARY")
print("-"*40)
try:
    eq = kite.margins(segment="equity")
    available = eq.get("available", {})
    net        = eq.get("net", 0)
    cash       = available.get("live_balance", available.get("cash", 0))
    collateral = available.get("collateral", 0)
    used       = eq.get("utilised", {}).get("debits", 0)
    print(f"  Available Cash   : {rupee(cash)}")
    print(f"  Net Balance      : {rupee(net)}")
    print(f"  Collateral       : {rupee(collateral)}")
    print(f"  Used / Debits    : {rupee(used)}")
    budget = min(cash if cash > 0 else net, config.TRADING_AMOUNT)
except Exception as e:
    print(f"  [Error fetching margins: {e}]")
    budget = config.TRADING_AMOUNT

print(f"\n  🎯 Trading Budget Today : {rupee(budget)}")
print(f"  📦 Max Positions        : {config.MAX_POSITIONS}")
print(f"  💰 Per Stock Allocation : {rupee(budget / config.MAX_POSITIONS)}")

# ── 3. Today's Positions ──────────────────────────────────────────────────────
print("\n📈 TODAY'S OPEN POSITIONS")
print("-"*40)
try:
    positions = kite.positions()
    day_positions = positions.get("day", [])
    if not day_positions:
        print("  No open intraday positions.")
    else:
        total_pnl = 0
        for p in day_positions:
            sym   = p.get("tradingsymbol","")
            qty   = p.get("quantity", 0)
            avg   = p.get("average_price", 0)
            ltp   = p.get("last_price", 0)
            pnl   = p.get("pnl", 0)
            total_pnl += pnl
            direction = "LONG" if qty > 0 else "SHORT"
            pnl_str = f"+{rupee(pnl)}" if pnl >= 0 else rupee(pnl)
            print(f"  {sym:<15} {direction}  Qty:{qty}  Avg:{rupee(avg)}  LTP:{rupee(ltp)}  P&L: {pnl_str}")
        print(f"\n  Total Intraday P&L : {'+'if total_pnl>=0 else ''}{rupee(total_pnl)}")
except Exception as e:
    print(f"  [Error fetching positions: {e}]")

# ── 4. Trade History (Today's Orders) ────────────────────────────────────────
print("\n📋 TODAY'S ORDER HISTORY")
print("-"*40)
try:
    orders = kite.orders()
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    today_orders = [o for o in orders if str(o.get("order_timestamp","")).startswith(today_str)]
    if not today_orders:
        print("  No orders placed today.")
    else:
        for o in today_orders:
            sym    = o.get("tradingsymbol","")
            txn    = o.get("transaction_type","")
            qty    = o.get("quantity",0)
            price  = o.get("average_price", o.get("price",0))
            status = o.get("status","")
            t      = str(o.get("order_timestamp",""))[-8:][:5]
            print(f"  {t}  {txn:<4} {sym:<15} x{qty}  @ {rupee(price)}  [{status}]")
except Exception as e:
    print(f"  [Error fetching orders: {e}]")

# ── 5. Scan & Score Top BUY Candidates ───────────────────────────────────────
print("\n🔍 TODAY'S TOP BUY CANDIDATES (Live Analysis)")
print("-"*65)
print("  Scanning NSE with Kite data — technical indicators + momentum...")

scanner  = DynamicUniverse(kite=kite)
mdf      = MarketDataFetcher(kite=kite)
analyzer = TechnicalAnalyzer()

candidates = scanner.get_candidates_with_details(top_n=50)

results = []
for c in candidates:
    sym = c["symbol"]
    try:
        hist = mdf.get_stock_data(sym, period="1mo", interval="1d")
        if hist.empty or len(hist) < 20:
            continue
        signals = analyzer.generate_signals(hist)
        if signals.get("signal") not in ("BUY",):
            continue

        price      = c["last_price"]
        score      = signals.get("technical_score", 0)
        confidence = signals.get("confidence", 0)
        support    = signals.get("support", 0)
        resistance = signals.get("resistance", 0)

        stop_loss  = price * (1 - config.STOP_LOSS_PERCENTAGE)
        if support > 0 and support < price:
            stop_loss = max(support, stop_loss)

        target = price * (1 + config.TARGET_PERCENTAGE)
        if resistance > 0 and resistance > price:
            target = min(resistance, target * 1.2)

        per_stock  = budget / config.MAX_POSITIONS
        qty        = max(1, int(per_stock / price))
        invest     = qty * price
        exp_profit = qty * (target - price)
        exp_loss   = qty * (price - stop_loss)
        rr         = (target - price) / (price - stop_loss) if price != stop_loss else 0

        results.append({
            "symbol":     sym,
            "price":      price,
            "momentum":   c["momentum_pct"],
            "volume":     c["volume"],
            "tech_score": round(score, 2),
            "confidence": round(confidence * 100),
            "stop_loss":  round(stop_loss, 2),
            "target":     round(target, 2),
            "qty":        qty,
            "invest":     round(invest, 2),
            "exp_profit": round(exp_profit, 2),
            "exp_loss":   round(exp_loss, 2),
            "rr":         round(rr, 2),
            "trend":      signals.get("trend",""),
        })
    except Exception:
        continue

results.sort(key=lambda x: x["confidence"], reverse=True)
top_buys = results[:config.MAX_POSITIONS]

if not top_buys:
    print("\n  ⚠️  No strong BUY signals found right now.")
    print("  This is normal outside market hours or in sideways markets.")
    print("  The bot will automatically find and act during market hours.")
else:
    total_invest = sum(r["invest"] for r in top_buys)
    total_exp_profit = sum(r["exp_profit"] for r in top_buys)
    total_exp_loss   = sum(r["exp_loss"] for r in top_buys)

    print(f"\n  {'#':<3} {'Symbol':<14} {'Price':>8} {'Target':>8} {'SL':>8} "
          f"{'Qty':>4} {'Invest':>9} {'Exp.Profit':>11} {'Conf':>5} {'R:R':>5}")
    print("  " + "-"*95)
    for i, r in enumerate(top_buys, 1):
        print(
            f"  {i:<3} {r['symbol']:<14} {rupee(r['price']):>8} "
            f"{rupee(r['target']):>8} {rupee(r['stop_loss']):>8} "
            f"{r['qty']:>4} {rupee(r['invest']):>9} "
            f"+{rupee(r['exp_profit']):>10} "
            f"{r['confidence']:>4}% "
            f"{r['rr']:>5.1f}x"
        )
    print("  " + "-"*95)
    print(f"  {'TOTAL':<17} {'':<8} {'':<8} {'':<8} {'':<4} "
          f"{rupee(total_invest):>9} +{rupee(total_exp_profit):>10}")

    print(f"\n  📌 Strategy Summary:")
    print(f"     Total Capital Deployed : {rupee(total_invest)}")
    print(f"     Expected Profit (4%)   : +{rupee(total_exp_profit)}")
    print(f"     Max Risk (2% SL)       : -{rupee(total_exp_loss)}")
    print(f"     Stop Loss              : {config.STOP_LOSS_PERCENTAGE*100:.0f}% below entry")
    print(f"     Target                 : {config.TARGET_PERCENTAGE*100:.0f}% above entry")
    print(f"     Exit By                : {config.INTRADAY_CUTOFF} IST (forced intraday close)")

# ── 6. Portfolio Holdings ─────────────────────────────────────────────────────
print("\n💼 PORTFOLIO HOLDINGS (Delivery)")
print("-"*40)
try:
    holdings = kite.holdings()
    if not holdings:
        print("  No delivery holdings.")
    else:
        total_invested = 0
        total_current  = 0
        for h in holdings:
            sym      = h.get("tradingsymbol","")
            qty      = h.get("quantity",0)
            avg      = h.get("average_price",0)
            ltp      = h.get("last_price",0)
            pnl      = (ltp - avg) * qty
            pct      = ((ltp - avg) / avg * 100) if avg else 0
            invested = avg * qty
            current  = ltp * qty
            total_invested += invested
            total_current  += current
            pnl_str = f"+{rupee(pnl)}" if pnl >= 0 else rupee(pnl)
            print(f"  {sym:<15} Qty:{qty}  Avg:{rupee(avg)}  LTP:{rupee(ltp)}"
                  f"  P&L:{pnl_str} ({pct:+.1f}%)")
        overall_pnl = total_current - total_invested
        print(f"\n  Invested : {rupee(total_invested)}")
        print(f"  Current  : {rupee(total_current)}")
        print(f"  Total P&L: {'+'if overall_pnl>=0 else ''}{rupee(overall_pnl)}")
except Exception as e:
    print(f"  [Error fetching holdings: {e}]")

print("\n" + "="*65)
print("  Run './run_with_venv.sh src/trading_orchestrator.py once'")
print("  during market hours to execute trades automatically.")
print("="*65 + "\n")
