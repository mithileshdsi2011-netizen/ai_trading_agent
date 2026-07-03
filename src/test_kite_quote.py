"""Quick diagnostic: test which Kite market data endpoints are permitted."""
import os, sys
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.insert(0, current_dir)
sys.path.insert(0, project_root)

from token_manager import TokenManager

tm = TokenManager()
kite = tm.initialize_kite()

print("\n--- Kite Market Data Permission Test ---\n")

# Test 1: quote() with NSE:SYMBOL
try:
    q = kite.quote(["NSE:RELIANCE"])
    price = q.get("NSE:RELIANCE", {}).get("last_price")
    print(f"[OK]  quote('NSE:RELIANCE')  =>  last_price={price}")
except Exception as e:
    print(f"[ERR] quote('NSE:RELIANCE')  =>  {e}")

# Test 2: ltp() — lighter endpoint, often allowed on basic plans
try:
    ltp = kite.ltp(["NSE:RELIANCE"])
    price = ltp.get("NSE:RELIANCE", {}).get("last_price")
    print(f"[OK]  ltp('NSE:RELIANCE')    =>  last_price={price}")
except Exception as e:
    print(f"[ERR] ltp('NSE:RELIANCE')    =>  {e}")

# Test 3: ohlc()
try:
    ohlc = kite.ohlc(["NSE:RELIANCE"])
    data = ohlc.get("NSE:RELIANCE", {})
    print(f"[OK]  ohlc('NSE:RELIANCE')   =>  {data}")
except Exception as e:
    print(f"[ERR] ohlc('NSE:RELIANCE')   =>  {e}")

# Test 4: historical_data() — always available on Kite Connect paid plans
try:
    from datetime import datetime, timedelta
    instruments = kite.instruments("NSE")
    rel = next(i for i in instruments if i["tradingsymbol"] == "RELIANCE")
    hist = kite.historical_data(
        instrument_token=rel["instrument_token"],
        from_date=datetime.now() - timedelta(days=3),
        to_date=datetime.now(),
        interval="day"
    )
    print(f"[OK]  historical_data(RELIANCE, day)  =>  {len(hist)} candles, last close={hist[-1]['close'] if hist else 'N/A'}")
except Exception as e:
    print(f"[ERR] historical_data(RELIANCE, day)  =>  {e}")

# Test 5: positions() — needed for live trading
try:
    pos = kite.positions()
    print(f"[OK]  positions()  =>  {len(pos.get('day', []))} intraday positions")
except Exception as e:
    print(f"[ERR] positions()  =>  {e}")

# Test 6: margins()
try:
    m = kite.margins(segment="equity")
    print(f"[OK]  margins(equity)  =>  net={m.get('net')}")
except Exception as e:
    print(f"[ERR] margins(equity)  =>  {e}")

print("\n--- Done ---")
