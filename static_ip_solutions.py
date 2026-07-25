#!/usr/bin/env python3
"""
Static IP Solutions for AI Trading Bot
Provides multiple options to achieve static IP for trading operations
"""
import os
import json
import subprocess
import requests
from datetime import datetime

class StaticIPManager:
    """Manages static IP solutions for trading bot"""
    
    def __init__(self):
        self.config_file = 'data/static_ip_config.json'
        self.load_config()
    
    def load_config(self):
        """Load static IP configuration"""
        if os.path.exists(self.config_file):
            with open(self.config_file, 'r') as f:
                self.config = json.load(f)
        else:
            self.config = {
                'solution': None,
                'static_ip': None,
                'setup_date': None,
                'notes': ''
            }
    
    def save_config(self):
        """Save static IP configuration"""
        os.makedirs('data', exist_ok=True)
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=2)
    
    def get_current_ip(self):
        """Get current public IP"""
        urls = [
            'https://api.ipify.org',
            'https://ifconfig.me/ip',
            'https://icanhazip.com',
            'https://checkip.amazonaws.com'
        ]
        
        for url in urls:
            try:
                response = requests.get(url, timeout=5)
                ip = response.text.strip()
                if ip and '.' in ip and len(ip) < 20:
                    return ip
            except:
                continue
        return None
    
    def check_tailscale_status(self):
        """Check if Tailscale is running and get IP"""
        try:
            result = subprocess.run(['tailscale', 'status', '--json'], 
                                  capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                if data.get('Self'):
                    tailscale_ips = data['Self'].get('TailscaleIPs', [])
                    if tailscale_ips:
                        return {
                            'status': 'active',
                            'ip': tailscale_ips[0],
                            'name': data['Self'].get('HostName', ''),
                            'online': data['Self'].get('Online', False)
                        }
        except Exception as e:
            return {'status': 'error', 'error': str(e)}
        return {'status': 'not_installed'}
    
    def setup_tailscale_static_ip(self):
        """Setup Tailscale for static IP (recommended solution)"""
        print("🔧 SETTING UP TAILSCALE STATIC IP SOLUTION")
        print("=" * 60)
        
        # Check current status
        status = self.check_tailscale_status()
        
        if status['status'] == 'active':
            print(f"✅ Tailscale already active")
            print(f"   Static IP: {status['ip']}")
            print(f"   Hostname: {status['name']}")
            print(f"   Online: {status['online']}")
            
            # Update config
            self.config.update({
                'solution': 'tailscale',
                'static_ip': status['ip'],
                'setup_date': datetime.now().isoformat(),
                'notes': f'Tailscale hostname: {status["name"]}'
            })
            self.save_config()
            
            print(f"\n📋 NEXT STEPS:")
            print(f"1. Add this IP to Kite whitelist: {status['ip']}")
            print(f"2. This IP will remain static as long as Tailscale runs")
            print(f"3. Install Tailscale on mobile for dashboard access")
            
            return True
            
        elif status['status'] == 'not_installed':
            print("❌ Tailscale not installed")
            print("\n📋 INSTALLATION INSTRUCTIONS:")
            print("1. Download Tailscale from: https://tailscale.com/download/")
            print("2. Install and login with your account")
            print("3. Run: tailscale up")
            print("4. Re-run this script to configure")
            
        else:
            print(f"❌ Tailscale error: {status.get('error', 'Unknown')}")
            print("\n📋 TROUBLESHOOTING:")
            print("1. Ensure Tailscale is running: tailscale up")
            print("2. Check login: tailscale status")
            print("3. Re-authenticate if needed")
        
        return False
    
    def setup_vpn_static_ip(self):
        """Setup VPN for static IP"""
        print("\n🔧 VPN STATIC IP SOLUTION")
        print("=" * 60)
        
        print("📋 VPN OPTIONS FOR STATIC IP:")
        print("1. ExpressVPN - Dedicated IP option")
        print("2. NordVPN - Dedicated IP option") 
        print("3. CyberGhost - Dedicated IP option")
        print("4. PureVPN - Dedicated IP option")
        
        print("\n📋 SETUP STEPS:")
        print("1. Subscribe to VPN service with dedicated IP")
        print("2. Install VPN client")
        print("3. Connect to your dedicated IP server")
        print("4. Verify IP is static")
        print("5. Add IP to Kite whitelist")
        
        current_ip = self.get_current_ip()
        if current_ip:
            print(f"\nCurrent IP: {current_ip}")
            print("After VPN setup, this should change to your dedicated IP")
        
        return False
    
    def setup_cloud_server(self):
        """Setup cloud server for static IP (advanced)"""
        print("\n🔧 CLOUD SERVER STATIC IP SOLUTION")
        print("=" * 60)
        
        print("📋 CLOUD SERVER OPTIONS:")
        print("1. AWS EC2 - Elastic IP")
        print("2. DigitalOcean - Floating IP")
        print("3. Vultr - Static IP")
        print("4. Linode - Static IP")
        
        print("\n📋 SETUP STEPS:")
        print("1. Create cloud server instance")
        print("2. Allocate static IP")
        print("3. Deploy trading bot on server")
        print("4. Add static IP to Kite whitelist")
        print("5. Access dashboard via server IP")
        
        print("\n⚠️  This is an advanced solution requiring:")
        print("- Linux server administration")
        print("- Bot deployment on cloud")
        print("- Ongoing server maintenance")
        
        return False
    
    def check_isp_static_ip(self):
        """Check if ISP provides static IP"""
        print("\n🔧 ISP STATIC IP CHECK")
        print("=" * 60)
        
        current_ip = self.get_current_ip()
        if not current_ip:
            print("❌ Could not determine current IP")
            return False
        
        print(f"Current IP: {current_ip}")
        
        # Check IP history
        ip_file = 'data/ip_history.json'
        ip_history = []
        
        if os.path.exists(ip_file):
            with open(ip_file, 'r') as f:
                ip_history = json.load(f)
        
        # Add current IP to history
        ip_history.append({
            'ip': current_ip,
            'timestamp': datetime.now().isoformat()
        })
        
        # Keep only last 30 days
        ip_history = ip_history[-30:]
        
        with open(ip_file, 'w') as f:
            json.dump(ip_history, f, indent=2)
        
        # Analyze IP changes
        unique_ips = set(entry['ip'] for entry in ip_history)
        if len(unique_ips) == 1:
            print("✅ Your IP appears to be static!")
            print("   No changes detected in recorded history")
            
            self.config.update({
                'solution': 'isp_static',
                'static_ip': current_ip,
                'setup_date': datetime.now().isoformat(),
                'notes': 'ISP provides static IP'
            })
            self.save_config()
            return True
        else:
            print(f"⚠️  Your IP has changed {len(unique_ips)} times")
            print("   ISP does not provide static IP")
            print("   Consider using Tailscale solution")
            return False
    
    def generate_whitelist_request(self):
        """Generate IP whitelist request text"""
        current_ip = self.get_current_ip()
        if not current_ip:
            return None
        
        request_text = f"""
IP WHITELIST REQUEST FOR AI TRADING BOT
========================================
Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Current IP: {current_ip}

REQUESTED IP ADDRESSES:
1. {current_ip} - Primary trading IP

PURPOSE:
- Automated trading operations
- Buy/Sell order execution
- Real-time market data fetching
- Position monitoring
- Risk management

FREQUENCY:
- Continuous monitoring (24/7 for swing trades)
- Intraday operations during market hours
- API calls every 15 minutes

NOTES:
- This is a static IP solution setup
- IP will not change frequently
- Required for automated trading compliance

Please add this IP to the app whitelist at:
https://developers.kite.trade/profile
        """.strip()
        
        return request_text
    
    def show_status(self):
        """Show current static IP status"""
        print("\n📊 STATIC IP STATUS")
        print("=" * 60)
        
        if self.config.get('solution'):
            print(f"✅ Solution: {self.config['solution']}")
            print(f"   Static IP: {self.config['static_ip']}")
            print(f"   Setup Date: {self.config['setup_date']}")
            print(f"   Notes: {self.config['notes']}")
        else:
            print("❌ No static IP solution configured")
            print("\n📋 RECOMMENDED SOLUTIONS:")
            print("1. Tailscale (Free, Easy, Recommended)")
            print("2. VPN with Dedicated IP (Paid)")
            print("3. Cloud Server (Advanced)")
            print("4. ISP Static IP (Check availability)")
        
        current_ip = self.get_current_ip()
        if current_ip:
            print(f"\nCurrent Public IP: {current_ip}")

def main():
    """Main function to manage static IP solutions"""
    manager = StaticIPManager()
    
    print("🌐 AI TRADING BOT - STATIC IP MANAGER")
    print("=" * 60)
    print("Solutions for avoiding frequent IP whitelist updates")
    print()
    
    # Show current status
    manager.show_status()
    
    print("\n" + "=" * 60)
    print("🔧 AVAILABLE SOLUTIONS")
    print("=" * 60)
    print("1. Check ISP Static IP")
    print("2. Setup Tailscale (Recommended)")
    print("3. VPN with Dedicated IP")
    print("4. Cloud Server (Advanced)")
    print("5. Generate Whitelist Request")
    print("6. Show Current Status")
    print()
    
    choice = input("Select option (1-6): ").strip()
    
    if choice == '1':
        manager.check_isp_static_ip()
    elif choice == '2':
        manager.setup_tailscale_static_ip()
    elif choice == '3':
        manager.setup_vpn_static_ip()
    elif choice == '4':
        manager.setup_cloud_server()
    elif choice == '5':
        request = manager.generate_whitelist_request()
        if request:
            print("\n" + request)
            print("\n💡 Copy this text and send to your broker if needed")
    elif choice == '6':
        manager.show_status()
    else:
        print("Invalid choice")

if __name__ == "__main__":
    main()
