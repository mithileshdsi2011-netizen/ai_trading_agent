"""Check exact margin structure returned by Kite."""
import os, sys
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)
sys.path.insert(0, os.path.dirname(current_dir))
from token_manager import TokenManager
import json

kite = TokenManager().initialize_kite()

print("\n--- Full margins() response ---")
try:
    m = kite.margins()
    print(json.dumps(m, indent=2, default=str))
except Exception as e:
    print(f"Error: {e}")
