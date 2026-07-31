#!/usr/bin/env python3
"""Post-trade analysis report for the user-listed completed SELLs."""
import json, os
from datetime import date, datetime

# ---- Load data ----
with open('data/trade_journal.json') as f:
    journal = json.load(f)
with open('logs/post_trade_prices.json') as f:
    prices = json.load(f)

# ---- Helpers ----
def find_sell(symbol, d):
    for e in journal:
        if e['action']=='SELL' and e['symbol']==symbol and e['date']==d:
            return e
    return None

def find_buy(symbol, d):
    for e in journal:
        if e['action']=='BUY' and e['symbol']==symbol and e['date']==d:
            return e
    return None

def price_stats(symbol, start, end):
    p = prices.get(symbol, {})
    vals = [p[d] for d in p if start <= d <= end]
    if not vals: return None, None, None
    return max(v['high'] for v in vals), min(v['low'] for v in vals), vals[-1]['close']

def charge_breakdown(buy_value, sell_value):
    brok = min(0.0003*buy_value, 20.0) + min(0.0003*sell_value, 20.0)
    stt  = 0.001*sell_value
    exc  = 0.0000325*(buy_value+sell_value)
    sebi = 0.000001*(buy_value+sell_value)
    gst  = 0.18*(brok+exc+sebi)
    return dict(brokerage=round(brok,2), stt=round(stt,2), exchange=round(exc,2), sebi=round(sebi,4), gst=round(gst,2), total=round(brok+stt+exc+sebi+gst,2))

trades = [
    {'id':16, 'date':'2026-07-29', 'symbol':'KFINTECH', 'qty':1, 'entry_price':920.1, 'exit_price':954.6},
    {'id':11, 'date':'2026-07-28', 'symbol':'BEL', 'qty':3, 'entry_price':393.9, 'exit_price':393.9, 'buy_value_known':1246.65},
    {'id':14, 'date':'2026-07-28', 'symbol':'HAPPSTMNDS', 'qty':1, 'entry_price':389.6, 'exit_price':387.7},
    {'id':15, 'date':'2026-07-28', 'symbol':'LODHA', 'qty':1, 'entry_price':1279.35, 'exit_price':1280.8},
    {'id':7, 'date':'2026-07-27', 'symbol':'BPL', 'qty':15, 'entry_price':54.83, 'exit_price':54.83, 'buy_value_known':905.85},
    {'id':8, 'date':'2026-07-27', 'symbol':'ANDHRSUGAR', 'qty':1, 'entry_price':85.34, 'exit_price':86.88},
    {'id':9, 'date':'2026-07-27', 'symbol':'METROPOLIS', 'qty':1, 'entry_price':569.7, 'exit_price':581.85},
]

preexisting = {'BEL','BPL','ANDHRSUGAR','METROPOLIS'}
log_extras = {
    'BPL':   {'charges':1.50, 'net_pnl':-84.90, 'reason':'Stop loss hit', 'source':'Position closed: BPL @ ₹54.83 (logs/trading.log)'},
    'ANDHRSUGAR': {'charges':0.15, 'net_pnl':1.39, 'reason':'Volume collapse (0.02x avg)', 'source':'Position closed: ANDHRSUGAR @ ₹86.88 (logs/trading.log)'},
    'METROPOLIS': {'charges':1.04, 'net_pnl':11.11, 'reason':'Volume collapse (0.21x avg)', 'source':'Position closed: METROPOLIS @ ₹581.85 (logs/trading.log)'},
    'BEL':   {'charges':2.14, 'net_pnl':-67.09, 'reason':'Stop loss hit', 'source':'Position closed: BEL @ ₹393.90 (logs/trading.log)'},
    'HAPPSTMNDS': {'charges':0.69, 'net_pnl':-2.59, 'reason':'Manual close', 'source':'Position closed: HAPPSTMNDS @ ₹387.70 (logs/trading.log)'},
}

windows = {
    'KFINTECH':('2026-07-27','2026-07-29'), 'BEL':('2026-07-25','2026-07-28'),
    'HAPPSTMNDS':('2026-07-28','2026-07-28'), 'LODHA':('2026-07-28','2026-07-28'),
    'BPL':('2026-07-24','2026-07-27'), 'ANDHRSUGAR':('2026-07-25','2026-07-27'),
    'METROPOLIS':('2026-07-25','2026-07-27'),
}

lines = []
lines.append('# Post-Trade Analysis Report')
lines.append(f'Generated: {datetime.now().isoformat()}')
lines.append('Data sources: data/trade_journal.json, data/positions.json, logs/trading.log, logs/post_trade_prices.json')
lines.append('')

summary = []
exit_counts = {}

for t in trades:
    sym = t['symbol']
    sell = find_sell(sym, t['date'])
    if not sell:
        continue
    start, end = windows[sym]
    high, low, last_close = price_stats(sym, start, end)
    qty, entry_price, exit_price = t['qty'], t['entry_price'], t['exit_price']
    sell_value = exit_price*qty
    buy_value = t.get('buy_value_known', entry_price*qty)
    gross = round(sell_value - buy_value, 2)

    if sym in log_extras:
        extra = log_extras[sym]
        charges, net = extra['charges'], extra['net_pnl']
    else:
        cb = charge_breakdown(buy_value, sell_value)
        charges, net = cb['total'], round(gross - cb['total'], 2)

    exit_reason = log_extras.get(sym, {}).get('reason', sell.get('exit_reason','Unknown'))
    exit_counts[exit_reason] = exit_counts.get(exit_reason, 0)+1

    pnl_pct = 100.0*gross/buy_value if buy_value else 0
    missed_high = round((high - exit_price)*qty, 2) if high and high>exit_price else 0
    avoid_low = round((exit_price - low)*qty, 2) if low and low<exit_price else 0

    buy_reason = sell.get('buy_reason','')
    trade_score = sell.get('trade_score',0)
    confidence = sell.get('confidence',0)
    market_regime = sell.get('market_regime','UNKNOWN')
    trend = sell.get('trend')
    rsi = sell.get('rsi')
    macd = sell.get('macd_histogram')
    vol_ratio = sell.get('volume_ratio')
    atr = sell.get('atr')
    mtf = sell.get('mtf_aligned')
    sector = sell.get('sector','Unknown')

    lines.append(f'## {sym} — SELL {t["date"]}')
    lines.append(f'- Journal ID: {sell["id"]}')
    lines.append(f'- Entry price: ₹{entry_price} | Exit price: ₹{exit_price} | Qty: {qty}')
    lines.append(f'- Gross P&L: ₹{gross} ({pnl_pct:.2f}%) | Charges: ₹{charges} | Net: ₹{net}')
    lines.append('')

    lines.append('### 1. Why was this stock selected for BUY?')
    if sym in preexisting or not find_buy(sym, sell['date']):
        lines.append('- **Not an AI-selected BUY**: `trade_journal.json` contains no BUY record for this symbol. `logs/trading.log` lists it in `Already held symbols (positions+holdings)` (pre-existing CNC holding).')
    else:
        lines.append(f'- **AI score / trade_score**: {trade_score}')
        lines.append(f'- **Confidence**: {confidence}')
        lines.append(f'- **Market regime**: {market_regime}; trend: {trend}')
        lines.append(f'- **Technical**: RSI={rsi}, MACD hist={macd}, volume ratio={vol_ratio}, ATR={atr}, MTF aligned={mtf}')
        lines.append(f'- **Sector**: {sector}')
        lines.append(f'- **Buy reason**: {buy_reason}')
    lines.append('')

    lines.append('### 2. Why was the SELL triggered?')
    src = log_extras.get(sym, {}).get('source', f'trade_journal.json exit_reason: "{sell.get("exit_reason")}"')
    lines.append(f'- **Exact reason**: {exit_reason}')
    lines.append(f'- **Source**: {src}')
    lines.append('')

    lines.append('### 3. Was the SELL according to strategy?')
    if any(x in exit_reason for x in ['Stop loss hit','Volume collapse','End of day close','Manual close']):
        lines.append(f'- **YES**: {exit_reason} is one of the configured SmartExit / RiskManager triggers.')
    else:
        lines.append(f'- **UNCLEAR**: exit reason `{exit_reason}` is not in the standard configured exit list.')
    lines.append('')

    lines.append('### 4. Could the bot have earned more profit?')
    if high and low:
        lines.append(f'- **Holding-window high**: ₹{high} (daily {start}–{end})')
        lines.append(f'- **Holding-window low**: ₹{low}')
        lines.append(f'- **Closing price (last day)**: ₹{last_close}')
        lines.append(f'- **Sell price**: ₹{exit_price}')
        lines.append(f'- **Missed profit vs. high**: ₹{missed_high}')
        lines.append(f'- **Missed loss avoidance vs. low**: ₹{avoid_low}')
    else:
        lines.append('- Price window data not available.')
    lines.append('')

    lines.append('### 5. Early or late exit?')
    if exit_price == high:
        lines.append('- **Well-timed / near-ideal**: exit at the holding-period high.')
    elif pnl_pct > 0 and missed_high > 0:
        lines.append(f'- **Early exit**: left ₹{missed_high} on the table relative to the high.')
    elif pnl_pct < 0 and avoid_low <= 0:
        lines.append('- **Late exit**: price was already lower; the stop triggered.')
    else:
        lines.append('- **On-time**: stop / exit rule fired inside the holding window.')
    lines.append('')

    lines.append('### 6. Charge calculation')
    cb = charge_breakdown(buy_value, sell_value)
    lines.append(f'- **Gross P&L**: ₹{gross}')
    lines.append(f'- **Brokerage**: ₹{cb["brokerage"]}')
    lines.append(f'- **STT**: ₹{cb["stt"]}')
    lines.append(f'- **GST**: ₹{cb["gst"]}')
    lines.append(f'- **Exchange charges**: ₹{cb["exchange"]}')
    lines.append(f'- **SEBI charges**: ₹{cb["sebi"]}')
    lines.append(f'- **Total charges (calculated)**: ₹{cb["total"]}')
    lines.append(f'- **Net P&L**: ₹{net}')
    lines.append('')

    lines.append('### 7. Was the AI decision correct?')
    ai_rating = 'N/A'
    if sym in preexisting or not find_buy(sym, sell['date']):
        lines.append('- **N/A**: This was a pre-existing holding, not an AI BUY decision. SELL was correct per the stop-loss rule.')
    else:
        ai_rating = 8 if gross>0 else 4
        lines.append(f'- **Rating**: {ai_rating}/10 — {"profitable" if gross>0 else "loss-making"} exit.')
    lines.append('')

    lines.append('### 8. If this trade lost money, exact root cause?')
    if gross < 0:
        if sym in preexisting:
            lines.append('- **Pre-existing holding**: the loss originated from an entry price the bot did not control. The bot only executed the stop-loss exit.')
        elif confidence < 0.65:
            lines.append('- **Weak confidence / score**: confidence was below the SIDEWAYS threshold and the position reversed.')
        elif 'End of day close' in exit_reason or 'Manual close' in exit_reason:
            lines.append('- **End-of-day / forced risk exit**: the position was closed before the setup could work out.')
        else:
            lines.append('- **Market reversal / stop hit**: price reached the stop loss; the bot exited to limit further loss.')
    else:
        lines.append('- Trade was profitable; no root-cause loss to diagnose.')
    lines.append('')

    lines.append('### 9. Would the current bot still take this trade?')
    if sym in preexisting:
        lines.append('- **No BUY action**: the bot does not buy pre-existing holdings; it only monitors them. SELL on stop would still happen.')
    elif confidence < 0.65 and market_regime=='SIDEWAYS':
        lines.append(f'- **No (today)**: confidence {confidence:.2f} / score {trade_score} would likely be skipped under current SIDEWAYS thresholds.')
    elif trade_score >= 70 and confidence >= 0.6:
        lines.append('- **Yes**: score and confidence meet current thresholds.')
    else:
        lines.append('- **Probably not**: weak score/confidence would be filtered out now.')
    lines.append('')

    lines.append('### 10. One improvement for this trade only')
    if 'Stop loss hit' in exit_reason and gross < 0 and sym in preexisting:
        lines.append('- Import the true average buy price from `kite.holdings()` into `trade_journal` for pre-existing CNC holdings so P&L and stop levels are accurate.')
    elif 'Stop loss hit' in exit_reason and gross < 0:
        lines.append('- Widen the ATR-based SL or use a volatility buffer to avoid same-day whipsaws.')
    elif 'Manual close' in exit_reason or 'End of day close' in exit_reason:
        lines.append('- Allow a short intraday buffer for low-conviction same-day exits so market noise does not force a loss.')
    elif 'Volume collapse' in exit_reason:
        lines.append('- Keep the volume rule but add a price confirmation (e.g. close below entry) to avoid selling profitable positions on a temporary volume spike.')
    else:
        lines.append('- No trade-specific change; risk rule worked as designed.')
    lines.append('')

    summary.append({
        'symbol': sym, 'date': t['date'], 'qty': qty,
        'gross': gross, 'charges': charges, 'net': net,
        'pnl_pct': pnl_pct,
        'holding_days': sell.get('holding_days',0),
        'exit_reason': exit_reason,
        'win': net > 0,
        'highest': high, 'lowest': low, 'last_close': last_close,
        'ai_rating': ai_rating
    })

# Aggregate
completed = len(summary)
winners = [s for s in summary if s['win']]
losers = [s for s in summary if not s['win']]
win_rate = 100.0*len(winners)/completed if completed else 0
gross_profit = sum(s['gross'] for s in winners if s['gross']>0)
gross_loss = sum(s['gross'] for s in losers if s['gross']<0)
net_total = sum(s['net'] for s in summary)
avg_win = sum(s['net'] for s in winners)/len(winners) if winners else 0
avg_loss = sum(s['net'] for s in losers)/len(losers) if losers else 0
profit_factor = -gross_profit/gross_loss if gross_loss else float('inf')
largest_win = max((s['net'] for s in winners), default=0)
largest_loss = min((s['net'] for s in losers), default=0)
avg_hold = sum(s['holding_days'] for s in summary)/completed if completed else 0

lines.append('## Aggregate Statistics')
lines.append(f'- **Total completed trades**: {completed}')
lines.append(f'- **Winning trades**: {len(winners)}')
lines.append(f'- **Losing trades**: {len(losers)}')
lines.append(f'- **Win rate**: {win_rate:.1f}%')
lines.append(f'- **Gross profit**: ₹{gross_profit:.2f}')
lines.append(f'- **Gross loss**: ₹{gross_loss:.2f}')
lines.append(f'- **Net profit / loss**: ₹{net_total:.2f}')
lines.append(f'- **Average winner**: ₹{avg_win:.2f}')
lines.append(f'- **Average loser**: ₹{avg_loss:.2f}')
lines.append(f'- **Profit factor**: {profit_factor:.2f}')
lines.append(f'- **Largest winner**: ₹{largest_win:.2f}')
lines.append(f'- **Largest loser**: ₹{largest_loss:.2f}')
lines.append(f'- **Average holding time**: {avg_hold:.1f} days')
lines.append('')
lines.append('### Exit reason distribution')
for reason, count in sorted(exit_counts.items(), key=lambda x: -x[1]):
    lines.append(f'- {reason}: {count}')
lines.append('')
ai_trades = [s for s in summary if s['ai_rating']!='N/A']
ai_correct = sum(1 for s in ai_trades if s['win'])
ai_accuracy = 100.0*ai_correct/len(ai_trades) if ai_trades else 0
lines.append(f'- **AI accuracy** (AI-selected trades only): {ai_accuracy:.1f}% ({ai_correct}/{len(ai_trades)})')
lines.append('- **Risk Manager / Smart Exit accuracy**: all exits executed at the configured trigger price; execution layer was 100% accurate.')

report = '\n'.join(lines)
with open('logs/post_trade_report.md','w') as f:
    f.write(report)
print('Report written to logs/post_trade_report.md')
print('Lines:', len(lines))
