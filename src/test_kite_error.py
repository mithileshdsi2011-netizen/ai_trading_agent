"""Check the exact Kite error code to diagnose the permission issue."""
import os, sys
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.insert(0, current_dir)
sys.path.insert(0, project_root)

from token_manager import TokenManager
from kiteconnect import exceptions as kite_ex

tm = TokenManager()
kite = tm.initialize_kite()

try:
    kite.quote(["NSE:RELIANCE"])
except kite_ex.PermissionException as e:
    print(f"PermissionException: {e}")
    print(f"  message : {e.message if hasattr(e,'message') else str(e)}")
    print(f"  code    : {e.code if hasattr(e,'code') else 'N/A'}")
except Exception as e:
    print(f"Other error ({type(e).__name__}): {e}")
