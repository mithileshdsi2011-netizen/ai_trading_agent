#!/usr/bin/env python3
"""
Configure trading bot to use Tailscale static IP
"""
import json
import os
from datetime import datetime

# Static IP configuration
static_ip_config = {
    "solution": "tailscale",
    "static_ip": "100.118.142.85",
    "setup_date": datetime.now().isoformat(),
    "notes": "Tailscale static IP - will never change",
    "whitelist_url": "https://developers.kite.trade/profile",
    "mobile_access": "http://100.118.142.85:5001"
}

# Save configuration
os.makedirs('data', exist_ok=True)
with open('data/static_ip_config.json', 'w') as f:
    json.dump(static_ip_config, f, indent=2)

# Update last_known_ip.txt to static IP
with open('data/last_known_ip.txt', 'w') as f:
    f.write("100.118.142.85")

print("✅ Static IP Configuration Complete!")
print("=" * 50)
print(f"Static IP: {static_ip_config['static_ip']}")
print(f"Mobile Access: {static_ip_config['mobile_access']}")
print(f"Whitelist URL: {static_ip_config['whitelist_url']}")
print()
print("📋 NEXT STEPS:")
print("1. Add 100.118.142.85 to Kite whitelist")
print("2. Remove old dynamic IP from whitelist")
print("3. Restart trading bot if needed")
print("4. Enjoy static IP - no more weekly updates!")
