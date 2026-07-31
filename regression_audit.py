"""Production regression audit — no trading logic changes.
Reports whether the system is production-ready after the recent TradeScorer
and Trading Orchestrator improvements.
"""
import os
import sys
import json
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from trade_journal import TradeJournal


def _run(cmd, shell=True):
    try:
        result = subprocess.run(
            cmd, shell=shell, capture_output=True, text=True, cwd=ROOT, timeout=120
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return -1, '', str(e)


def phase1_compile_and_targeted_tests():
    print('\n=== PHASE 1 — Trading Engine ===')
    rc, out, err = _run('venv/bin/python -m py_compile $(find src -name "*.py")')
    if rc != 0:
        print(f'FAIL: source compile failed\n{err}')
    else:
        print('PASS: all src/ files compile')

    rc, out, err = _run(
        'venv/bin/python -m pytest '
        'tests/test_trade_scorer.py tests/test_decision_explainer.py '
        'tests/test_attribution.py tests/test_sideways_validation.py -v'
    )
    print('\n-- Targeted regression tests --')
    print(out if out else err)
    if rc == 0:
        print('PASS: targeted tests for recent changes passed')
    else:
        print('FAIL: targeted tests failed')


def phase4_trade_journal_completeness():
    print('\n=== PHASE 4 — Trade Journal ===')
    j = TradeJournal()
    if not os.path.exists(j._path):
        print('INFO: trade_journal.json not found (no trades yet)')
        return
    entries = j._load()
    closed = [e for e in entries if e.get('status') == 'CLOSED' and e.get('action') == 'BUY']
    print(f'Found {len(closed)} closed BUY entries')
    required_fields = [
        'buy_reason', 'trade_score', 'score_components', 'market_regime', 'sector',
        'confidence', 'trend', 'rsi', 'volume_ratio', 'macd_histogram',
        'mtf_aligned', 'mtf_strict', 'exit_price', 'exit_date', 'holding_days',
        'exit_reason', 'gross_pnl', 'net_pnl', 'charges', 'attribution',
    ]
    missing = {f: 0 for f in required_fields}
    for e in closed:
        for f in required_fields:
            if f not in e:
                missing[f] += 1
    bad = {k: v for k, v in missing.items() if v > 0}
    if bad:
        print(f'WARN: closed trades missing fields: {bad}')
    else:
        print('PASS: all required fields present in every closed trade')
    if closed:
        last = closed[-1]
        print('Last attribution record:')
        print(json.dumps(last.get('attribution', {}), indent=2))


def phase5_risk_manager_api():
    print('\n=== PHASE 5 — Risk Manager ===')
    try:
        from risk_manager import RiskManager
        from config import config as cfg
        rm = RiskManager()
        public = [m for m in dir(rm) if not m.startswith('_')]
        checks = [
            ('MAX_POSITIONS', cfg.MAX_POSITIONS),
            ('DAILY_MAX_LOSS_PCT', cfg.DAILY_MAX_LOSS_PCT),
            ('MAX_CAPITAL_USAGE', cfg.MAX_CAPITAL_USAGE),
            ('TRADING_AMOUNT', cfg.TRADING_AMOUNT),
        ]
        print('Public RiskManager methods:', ', '.join(public[:20]))
        for name, val in checks:
            print(f'  {name} = {val}')
        print('PASS: RiskManager imports and config values readable')
    except Exception as e:
        print(f'FAIL: RiskManager/config error: {e}')


def phase6_logging_verification():
    print('\n=== PHASE 6 — Logging ===')
    log_path = os.path.join(ROOT, 'logs', 'trading.log')
    if not os.path.exists(log_path):
        print('INFO: trading.log not found')
        return
    with open(log_path, 'r', errors='ignore') as f:
        content = f.read()
    print(f'trading.log size: {len(content)} chars')
    print(f"'DECISION EXPLANATION' occurrences: {content.count('DECISION EXPLANATION')}")
    print(f"'TradeScore' occurrences: {content.count('TradeScore')}")
    print(f"'Executing BUY' occurrences: {content.count('Executing BUY')}")
    print(f"'Sell order' occurrences: {content.count('Sell order')}")


def phase7_dashboard_routes():
    print('\n=== PHASE 3/2 — Dashboard / APIs ===')
    dash = os.path.join(ROOT, 'dashboard.py')
    if not os.path.exists(dash):
        print('INFO: dashboard.py not found at project root')
        return
    with open(dash, 'r') as f:
        text = f.read()
    routes = [line.strip() for line in text.splitlines() if '@app.route' in line]
    print(f'Discovered {len(routes)} @app.route decorators')
    for r in routes:
        print('  ', r)


def main():
    phase1_compile_and_targeted_tests()
    phase4_trade_journal_completeness()
    phase5_risk_manager_api()
    phase6_logging_verification()
    phase7_dashboard_routes()
    print('\n=== END OF AUDIT ===')


if __name__ == '__main__':
    main()
