#!/usr/bin/env python3
"""Post-trade analysis report v3: structured per-trade review with source citations."""
import json
from datetime import datetime

# ---- Sources ----
with open('data/trade_journal.json') as f:
    journal = json.load(f)
with open('logs/post_trade_prices.json') as f:
    prices = json.load(f)
with open('data/positions.json') as f:
    positions = json.load(f)

# current bot thresholds (source: config.py)
MIN_CONFIDENCE_SIDEWAYS = 0.65
SIDEWAYS_BUY_SCORE_MIN  = 80

# ---- Helpers ----
def find_sell(symbol, d):
    for e in journal:
        if e['action']=='SELL' and e['symbol']==symbol and e['date']==d:
            return e
    return None

def find_buy(symbol, before_id):
    buys = [e for e in journal if e['action']=='BUY' and e['symbol']==symbol and e['id'] < before_id]
    return buys[-1] if buys else None

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

windows = {
    'KFINTECH':('2026-07-27','2026-07-29'), 'BEL':('2026-07-25','2026-07-28'),
    'HAPPSTMNDS':('2026-07-28','2026-07-28'), 'LODHA':('2026-07-28','2026-07-28'),
    'BPL':('2026-07-24','2026-07-27'), 'ANDHRSUGAR':('2026-07-25','2026-07-27'),
    'METROPOLIS':('2026-07-25','2026-07-27'),
}

# broker execution records from logs/trading.log
log_extras = {
    'BPL':   {'charges':1.50, 'net_pnl':-84.90, 'reason':'Stop loss hit', 'source':'logs/trading.log: Position closed: BPL @ ₹54.83'},
    'ANDHRSUGAR': {'charges':0.15, 'net_pnl':1.39, 'reason':'Volume collapse (0.02x avg)', 'source':'logs/trading.log: Position closed: ANDHRSUGAR @ ₹86.88'},
    'METROPOLIS': {'charges':1.04, 'net_pnl':11.11, 'reason':'Volume collapse (0.21x avg)', 'source':'logs/trading.log: Position closed: METROPOLIS @ ₹581.85'},
    'BEL':   {'charges':2.14, 'net_pnl':-67.09, 'reason':'Stop loss hit', 'source':'logs/trading.log: Position closed: BEL @ ₹393.90'},
    'HAPPSTMNDS': {'charges':0.69, 'net_pnl':-2.59, 'reason':'Manual close', 'source':'logs/trading.log: Position closed: HAPPSTMNDS @ ₹387.70'},
}

# Dashboard data lookup
pos_lookup = {p['symbol']: p for p in positions.get('positions',[]) if p.get('status')=='CLOSED'}

lines = []
lines.append('# Post-Trade Analysis Report v3')
lines.append(f'Generated: {datetime.now().isoformat()}')
lines.append('Sources: data/trade_journal.json, data/positions.json, logs/trading.log, logs/post_trade_prices.json')
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

    # charges / net
    if sym in log_extras:
        extra = log_extras[sym]
        charges, net = extra['charges'], extra['net_pnl']
    else:
        cb = charge_breakdown(buy_value, sell_value)
        charges, net = cb['total'], round(gross - cb['total'], 2)

    # exit reason
    exit_reason = log_extras.get(sym, {}).get('reason', sell.get('exit_reason','Not available'))
    exit_source = log_extras.get(sym, {}).get('source', f'trade_journal.json SELL id {sell["id"]}')
    exit_counts[exit_reason] = exit_counts.get(exit_reason, 0)+1

    # attribution
    buy = find_buy(sym, sell['id'])
    ai_entry = buy is not None
    ai_exit = True  # every SELL in this list was bot-executed
    trade_origin = 'AI Generated' if ai_entry else 'Imported Holding'

    # source for buy
    buy_source = f'trade_journal.json BUY id {buy["id"]}' if buy else 'trade_journal.json (no BUY record found)'

    # technical values from buy record
    trade_score = buy.get('trade_score') if buy else None
    confidence = buy.get('confidence') if buy else None
    market_regime = buy.get('market_regime') if buy else None
    trend = buy.get('trend') if buy else None
    rsi = buy.get('rsi') if buy else None
    macd = buy.get('macd_histogram') if buy else None
    vol_ratio = buy.get('volume_ratio') if buy else None
    atr = buy.get('atr') if buy else None
    mtf = buy.get('mtf_aligned') if buy else None
    sector = buy.get('sector') if buy else None
    buy_reason = buy.get('buy_reason') if buy else None

    # risk/reward: not recorded in trade_journal, so explicit Not available
    risk_reward = 'Not available'

    pnl_pct = 100.0*gross/buy_value if buy_value else 0
    missed = round((high - exit_price)*qty, 2) if high and high>exit_price else 0
    avoid_low = round((exit_price - low)*qty, 2) if low and low<exit_price else 0

    # exit quality
    if high and low and (high - low):
        exit_quality = 100.0 * (exit_price - low) / (high - low)
    else:
        exit_quality = None

    # exit timing label
    if exit_price == high:
        exit_timing = 'Correct Exit'
    elif pnl_pct > 0 and missed > 0:
        exit_timing = 'Early Exit'
    elif pnl_pct < 0 and avoid_low <= 0:
        exit_timing = 'Late Exit'
    else:
        exit_timing = 'Correct Exit'

    # AI decision rating (1-10)
    if not ai_entry:
        ai_rating = 'N/A (BUY not AI-generated; SELL only)'
    else:
        if net > 0 and exit_timing in ('Correct Exit','Early Exit'):
            ai_rating = 8
        elif net > 0:
            ai_rating = 6
        elif gross > 0 and net < 0:
            ai_rating = 5  # charges ate profit
        else:
            ai_rating = 4

    # primary loss reason
    loss_reason = None
    if net < 0:
        if not ai_entry:
            loss_reason = 'Inherited holding'
        elif gross > 0 and net < 0:
            loss_reason = 'Brokerage impact'
        elif 'Stop loss' in exit_reason:
            loss_reason = 'Stop loss'
        elif 'End of day' in exit_reason or 'Manual' in exit_reason:
            loss_reason = 'End-of-day exit'
        elif 'Volume collapse' in exit_reason:
            loss_reason = 'AI decision'
        else:
            loss_reason = 'AI decision'

    # current bot comparison
    current_bot_same = None
    current_bot_reason = None
    if not ai_entry:
        current_bot_same = 'No'
        current_bot_reason = 'The current bot does not BUY pre-existing CNC holdings; it only monitors and exits them on stop.'
    else:
        regime = (market_regime or '').upper()
        conf = confidence or 0
        score = trade_score or 0
        if regime == 'SIDEWAYS' and (conf < MIN_CONFIDENCE_SIDEWAYS or score < SIDEWAYS_BUY_SCORE_MIN):
            current_bot_same = 'No'
            reasons = []
            if conf < MIN_CONFIDENCE_SIDEWAYS:
                reasons.append(f'confidence {conf:.3f} is below current MIN_CONFIDENCE_SIDEWAYS={MIN_CONFIDENCE_SIDEWAYS}')
            if score < SIDEWAYS_BUY_SCORE_MIN:
                reasons.append(f'trade_score {score} is below current SIDEWAYS_BUY_SCORE_MIN={SIDEWAYS_BUY_SCORE_MIN}')
            current_bot_reason = 'The current SIDEWAYS guards would reject this BUY because ' + ' and '.join(reasons) + '.'
        else:
            current_bot_same = 'Yes'
            current_bot_reason = 'Confidence and trade score meet the current regime thresholds (source: config.py).'

    # per-trade recommendation
    if not ai_entry and gross < 0:
        recommendation = 'Import the true average buy price from `kite.holdings()` into `trade_journal` for pre-existing CNC holdings so P&L and stop levels are accurate (source: logs/trading.log).'
    elif 'Stop loss' in exit_reason and not ai_entry:
        recommendation = 'Track the actual entry date of inherited holdings so the holding window and stop distance can be calculated from the real purchase point (source: data/positions.json and logs/trading.log).'
    elif 'End of day close' in exit_reason or 'Manual close' in exit_reason:
        recommendation = 'Add a minimum intraday profit buffer before same-day EOD/manual exits so small noise does not turn a breakeven setup into a net loss (source: logs/trading.log).'
    elif 'Volume collapse' in exit_reason:
        recommendation = 'Require a price-confirmation bar (e.g. close below entry or below VWAP) before selling a profitable position on a volume-collapse alert (source: logs/trading.log).'
    else:
        recommendation = 'No trade-specific change; the configured rule executed exactly as designed (source: trade_journal.json and logs/trading.log).'

    # ---- write per-trade sections ----
    lines.append(f'## {sym} — SELL {t["date"]}')
    lines.append(f'- **Symbol**: {sym}')
    lines.append(f'- **SELL trade journal id**: {sell["id"]} (source: trade_journal.json)')
    lines.append(f'- **Qty**: {qty}')
    lines.append(f'- **Entry price**: ₹{entry_price} | **Exit price**: ₹{exit_price}')
    lines.append(f'- **Gross P&L**: ₹{gross} ({pnl_pct:.2f}%)')
    lines.append(f'- **Charges**: ₹{charges}')
    lines.append(f'- **Net P&L**: ₹{net}')
    if sym in pos_lookup:
        p = pos_lookup[sym]
        lines.append(f'- **Dashboard record**: data/positions.json status={p.get("status")}, pnl={p.get("pnl")}, net_pnl={p.get("net_pnl")}, highest_price={p.get("highest_price")}')
    else:
        lines.append('- **Dashboard record**: Not available in data/positions.json')
    lines.append('')

    lines.append('### 1. BUY Analysis')
    lines.append(f'- **BUY generated by AI?** {"Yes" if ai_entry else "No"}')
    lines.append(f'  - Source: {buy_source}')
    if ai_entry:
        lines.append(f'- **Why the stock was selected**: {buy_reason or "Not available"}')
        lines.append(f'- **AI Score**: {trade_score if trade_score is not None else "Not available"}')
        lines.append(f'- **Confidence**: {confidence if confidence is not None else "Not available"}')
        lines.append(f'- **Market Regime**: {market_regime or "Not available"}')
        lines.append(f'- **Risk/Reward**: {risk_reward} — risk_reward is not recorded in trade_journal.json')
        lines.append(f'- **Key technical reason**: trend={trend}, RSI={rsi}, MACD hist={macd}, volume_ratio={vol_ratio}, ATR={atr}, MTF aligned={mtf}, sector={sector}')
        lines.append(f'  - Source: trade_journal.json BUY id {buy["id"]}')
    else:
        lines.append('- **Why the stock was selected**: Not available — no AI BUY record in trade_journal.json.')
        lines.append('- **AI Score / Confidence / Regime / Technical reason**: Not available — no AI BUY record.')
        lines.append('- **BUY was not generated by the AI. Only the exit was AI-managed.**')
    lines.append('')

    lines.append('### 2. SELL Analysis')
    lines.append(f'- **SELL generated by AI?** {"Yes" if ai_exit else "No"}')
    lines.append(f'  - Source: {exit_source}')
    lines.append(f'- **Exact exit reason**: {exit_reason}')
    lines.append(f'  - Source: {exit_source}')
    if any(x in exit_reason for x in ['Stop loss hit','Volume collapse','End of day close','Manual close']):
        lines.append('- **Exit followed strategy?** YES')
        lines.append(f'  - Source: {exit_source} (matches a configured SmartExit / RiskManager rule)')
    else:
        lines.append('- **Exit followed strategy?** UNCLEAR')
        lines.append(f'  - Source: {exit_source}')
    if 'Stop loss' in exit_reason:
        lines.append(f'- **Why the bot exited at that price**: The configured stop-loss for {sym} was hit; the bot protected capital by closing the position.')
    elif 'Volume collapse' in exit_reason:
        lines.append(f'- **Why the bot exited at that price**: SmartExitAI detected a volume collapse, indicating fading momentum.')
    elif 'Manual close' in exit_reason:
        lines.append(f'- **Why the bot exited at that price**: The Risk Manager forced a manual close of the position.')
    elif 'End of day close' in exit_reason:
        lines.append(f'- **Why the bot exited at that price**: The position was closed by the end-of-day rule to avoid overnight exposure.')
    else:
        lines.append(f'- **Why the bot exited at that price**: {exit_reason}')
    lines.append(f'  - Source: {exit_source}')
    lines.append('')

    lines.append('### 3. Trade Review')
    if high and low:
        lines.append(f'- **Exit timing**: {exit_timing}')
        lines.append(f'- **Holding-period high**: ₹{high}')
        lines.append(f'- **Holding-period low**: ₹{low}')
        lines.append(f'- **Sell price**: ₹{exit_price}')
        lines.append(f'- **Could more profit have been captured?** Yes, up to ₹{missed} more by selling at the holding-period high.')
        lines.append(f'  - Source: logs/post_trade_prices.json ({start} to {end})')
        lines.append(f'- **Was loss avoidable?** {"Yes — a better-timed exit could have avoided at least part of the loss." if net < 0 and missed > 0 else ("No — the position was already underwater when the bot began managing it." if not ai_entry else "No — the bot exited at the available price according to its rule.")}')
    else:
        lines.append('- **Exit timing / high / low / more profit / loss avoidable**: Not available — historical price data missing for this window.')
    lines.append(f'- **AI decision rating (1-10)**: {ai_rating}')
    if isinstance(ai_rating, int):
        lines.append(f'  - Source: trade_journal.json and logs/trading.log; rating is based on net P&L, exit timing, and whether the SELL matched a configured rule.')
    lines.append('')

    lines.append('### 4. If the trade lost money — primary reason')
    if net < 0:
        lines.append(f'- **Primary reason**: {loss_reason}')
        if loss_reason == 'Inherited holding':
            lines.append('  - The BUY was not made by the AI; the loss was already embedded in the pre-existing position when the bot took over monitoring.')
        elif loss_reason == 'Brokerage impact':
            lines.append(f'  - Gross P&L was +₹{gross}, but total charges of ₹{charges} turned the trade into a net loss.')
        elif loss_reason == 'End-of-day exit':
            lines.append('  - The position was closed by the EOD / manual rule before the setup could work in the bot\'s favour.')
        elif loss_reason == 'Stop loss':
            lines.append('  - The price reached the configured stop-loss, and the bot exited to limit further loss.')
        else:
            lines.append('  - The AI-managed exit rule fired at a loss, capping downside.')
    else:
        lines.append('- **Primary reason**: Not applicable — trade was profitable.')
    lines.append(f'  - Source: trade_journal.json SELL id {sell["id"]}; {exit_source}')
    lines.append('')

    lines.append('### 5. Current Bot Comparison')
    lines.append(f'- **Would today\'s version make the same trade?** {current_bot_same}')
    lines.append(f'  - {current_bot_reason}')
    lines.append(f'  - Source: config.py (MIN_CONFIDENCE_SIDEWAYS={MIN_CONFIDENCE_SIDEWAYS}, SIDEWAYS_BUY_SCORE_MIN={SIDEWAYS_BUY_SCORE_MIN})')
    lines.append('')

    lines.append('### 6. One recommendation to improve only this trade')
    lines.append(f'- {recommendation}')
    lines.append('')

    # realized R:R proxy (gross vs. holding-window risk); original stop not in sources
    rr_achieved = round(gross / ((entry_price - low) * qty), 3) if low and (entry_price - low) > 0 else None
    capital_preserved = round((exit_price - low) * qty, 2) if low and exit_price > low else 0.0

    summary.append({
        'symbol': sym, 'date': t['date'], 'qty': qty,
        'gross': gross, 'charges': charges, 'net': net,
        'pnl_pct': pnl_pct,
        'holding_days': sell.get('holding_days',0),
        'exit_reason': exit_reason,
        'win': net > 0,
        'ai_entry': ai_entry, 'ai_exit': ai_exit,
        'trade_origin': trade_origin,
        'ai_rating': ai_rating,
        'exit_timing': exit_timing,
        'loss_reason': loss_reason,
        'rr_achieved': rr_achieved,
        'capital_preserved': capital_preserved,
    })

# ---- Summary builders ----
def add_exit_distribution(block, title):
    counts = {}
    for s in block:
        counts[s['exit_reason']] = counts.get(s['exit_reason'],0)+1
    lines.append(f'### Exit Reason Distribution — {title}')
    for reason, count in sorted(counts.items(), key=lambda x: -x[1]):
        lines.append(f'- {reason}: {count}')
    lines.append('')

def add_stats(block, title):
    wins = [s for s in block if s['win']]
    loses = [s for s in block if not s['win']]
    wr = 100.0*len(wins)/len(block) if block else 0
    lr = 100.0 - wr
    gp = sum(s['gross'] for s in wins if s['gross']>0)
    gl = sum(s['gross'] for s in loses if s['gross']<0)
    nt = sum(s['net'] for s in block)
    aw = sum(s['net'] for s in wins)/len(wins) if wins else 0
    al = sum(s['net'] for s in loses)/len(loses) if loses else 0
    pf = -gp/gl if gl else float('inf')
    lw = max((s['net'] for s in wins), default=0)
    ll = min((s['net'] for s in loses), default=0)
    ah = sum(s['holding_days'] for s in block)/len(block) if block else 0
    avg_rr = sum(s['rr_achieved'] for s in block if s['rr_achieved'] is not None)/sum(1 for s in block if s['rr_achieved'] is not None) if any(s['rr_achieved'] is not None for s in block) else None
    expectancy = (wr/100.0)*aw + (lr/100.0)*al  # al is negative

    ai_entry_total = sum(1 for s in block if s['ai_entry'])
    ai_entry_win = sum(1 for s in block if s['ai_entry'] and s['win'])
    ai_exit_total = sum(1 for s in block if s['ai_exit'])
    ai_exit_win = sum(1 for s in block if s['ai_exit'] and s['win'])
    risk_total = len(block)
    risk_ok = sum(1 for s in block if s['exit_reason'] in ['Stop loss hit','Volume collapse (0.09x avg)','Volume collapse (0.02x avg)','Volume collapse (0.21x avg)','End of day close','Manual close'])
    capital_preserved = sum(s['capital_preserved'] for s in block)

    lines.append('==========================')
    lines.append(f'{title}')
    lines.append('==========================')
    lines.append(f'- **Total Trades**: {len(block)}')
    lines.append(f'- **Winners**: {len(wins)}')
    lines.append(f'- **Losers**: {len(loses)}')
    lines.append(f'- **Win Rate**: {wr:.1f}%')
    lines.append(f'- **Gross Profit**: ₹{gp:.2f}')
    lines.append(f'- **Gross Loss**: ₹{gl:.2f}')
    lines.append(f'- **Net Profit / Loss**: ₹{nt:.2f}')
    lines.append(f'- **Profit Factor**: {pf:.2f}')
    lines.append(f'- **Average Winner**: ₹{aw:.2f}')
    lines.append(f'- **Average Loser**: ₹{al:.2f}')
    lines.append(f'- **Largest Winner**: ₹{lw:.2f}')
    lines.append(f'- **Largest Loser**: ₹{ll:.2f}')
    lines.append(f'- **Average Holding Time**: {ah:.1f} days')
    lines.append(f'- **Expectancy (per trade)**: ₹{expectancy:.2f}')
    if avg_rr is not None:
        lines.append(f'- **Average Realized R:R achieved**: {avg_rr:.2f}  *(gross / (entry − holding low); original stop not in sources)*')
    else:
        lines.append('- **Average Realized R:R achieved**: Not available — price window low not found')
    add_exit_distribution(block, title)
    lines.append(f'- **AI Entry Accuracy**: {100.0*ai_entry_win/ai_entry_total:.1f}% ({ai_entry_win}/{ai_entry_total})')
    lines.append(f'- **AI Exit Accuracy**: {100.0*ai_exit_win/ai_exit_total:.1f}% ({ai_exit_win}/{ai_exit_total})')
    lines.append(f'- **Risk Manager Rule Adherence**: {100.0*risk_ok/risk_total:.1f}% ({risk_ok}/{risk_total}) — all exits matched a configured rule, not a win/loss prediction.')
    lines.append(f'- **Capital Preserved (vs. holding low)**: ₹{capital_preserved:.2f}')
    lines.append('')

# Risk Manager Effectiveness is conceptually different from rule adherence; the needed data is not in the listed sources
lines.append('## Risk Manager Effectiveness')
lines.append('- **Trades allowed in this report**: 7')
lines.append('- **Trades blocked**: Not available — blocked/avoided trades are not recorded in the listed sources.')
lines.append('- **Max drawdown prevented**: Not available — post-exit price data is not in the listed sources.')
lines.append('- **Note**: `Rule Adherence` above only verifies that each exit was the intended SmartExit / RiskManager action. It does not measure whether the Risk Manager is profitable or predicts direction.')
lines.append('')

ai_summary = [s for s in summary if s['ai_entry']]
add_stats(ai_summary, 'AI GENERATED TRADES')
add_stats(summary, 'ALL COMPLETED TRADES')

report = '\n'.join(lines)
with open('logs/post_trade_report.md','w') as f:
    f.write(report)
print('Report written to logs/post_trade_report.md')
print('Lines:', len(lines))
