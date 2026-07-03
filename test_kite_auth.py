"""
Test Kite Connect Authentication
"""
from kiteconnect import KiteConnect
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

API_KEY = os.getenv("KITE_API_KEY")
API_SECRET = os.getenv("KITE_API_SECRET")

print(f"API Key: {API_KEY}")
print(f"API Secret: {API_SECRET[:10]}..." if API_SECRET else "API Secret not set")

if not API_KEY or not API_SECRET:
    print("ERROR: KITE_API_KEY and KITE_API_SECRET must be set in .env")
    sys.exit(1)

# Initialize Kite Connect
kite = KiteConnect(api_key=API_KEY)

# Generate login URL
print("\n" + "="*60)
print("STEP 1: Login to get Request Token")
print("="*60)
print(f"\nOpen this URL in your browser:\n{kite.login_url()}")

print("\n" + "="*60)
print("STEP 2: After login, you'll be redirected to your redirect URL")
print("="*60)
print("The redirect URL will contain 'request_token' parameter")
print("Example: http://127.0.0.1:5000/login?request_token=xxxxx&status=success")
print("\nCopy the request_token value from the URL")

# Get request token from user
request_token = input("\nEnter the request_token from the redirect URL: ").strip()

print(f"\nRequest Token: {request_token}")

# Generate session
print("\n" + "="*60)
print("STEP 3: Generating Session")
print("="*60)

try:
    session = kite.generate_session(request_token, api_secret=API_SECRET)
    kite.set_access_token(session["access_token"])
    
    print(f"✓ Session generated successfully!")
    print(f"✓ Access Token: {session['access_token']}")
    
    # Test connection by getting profile
    print("\n" + "="*60)
    print("STEP 4: Testing Connection")
    print("="*60)
    
    profile = kite.profile()
    print(f"✓ Connected successfully!")
    print(f"User ID: {profile['user_id']}")
    print(f"User Name: {profile['user_name']}")
    print(f"Email: {profile['email']}")
    
    # Get margins
    margins = kite.margins()
    print(f"\n✓ Available Margins:")
    print(f"  Equity: {margins['equity']['available']['live_margin']}")
    
    # Get holdings
    holdings = kite.holdings()
    print(f"\n✓ Holdings: {len(holdings)} positions")
    
    print("\n" + "="*60)
    print("SUCCESS: Kite Connect is working!")
    print("="*60)
    print(f"\nAdd this to your .env file:")
    print(f"KITE_REQUEST_TOKEN={request_token}")
    
except Exception as e:
    print(f"✗ Error: {e}")
    print("\nPossible issues:")
    print("1. Invalid request token (token expires in few minutes)")
    print("2. Incorrect API credentials")
    print("3. Network issues")
    sys.exit(1)
