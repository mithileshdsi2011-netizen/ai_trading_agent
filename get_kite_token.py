"""
Simple web server to capture Kite Connect redirect and get request token
"""
from kiteconnect import KiteConnect
from flask import Flask, request, jsonify
import sys
import os
import json
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

API_KEY = os.getenv("KITE_API_KEY")
API_SECRET = os.getenv("KITE_API_SECRET")

app = Flask(__name__)

@app.route('/')
def home():
    return """
    <h1>Kite Connect Token Handler</h1>
    <p>Waiting for redirect from Kite...</p>
    """

@app.route('/login')
def login():
    request_token = request.args.get('request_token')
    status = request.args.get('status')

    if request_token and status == 'success':
        kite = KiteConnect(api_key=API_KEY)

        try:
            session = kite.generate_session(request_token, api_secret=API_SECRET)
            access_token = session['access_token']

            # Test connection
            kite.set_access_token(access_token)
            profile = kite.profile()

            # Save access token to data/kite_token.json
            os.makedirs('data', exist_ok=True)
            token_expiry = datetime.now() + timedelta(hours=23)
            token_data = {
                'access_token': access_token,
                'expiry': token_expiry.isoformat(),
                'saved_at': datetime.now().isoformat(),
                'user_id': profile['user_id'],
                'user_name': profile['user_name']
            }

            with open('data/kite_token.json', 'w') as f:
                json.dump(token_data, f, indent=2)

            print(f"\n✓ Access token saved to data/kite_token.json")
            print(f"✓ Token expires at: {token_expiry}")

            result = {
                'success': True,
                'request_token': request_token,
                'access_token': access_token,
                'user_id': profile['user_id'],
                'user_name': profile['user_name']
            }

            return f"""
            <h1>✓ Authentication Successful!</h1>
            <h2>Access Token Saved Automatically!</h2>
            <p>The access token has been saved to <code>data/kite_token.json</code></p>
            <h3>Access Token (for reference):</h3>
            <pre>{access_token}</pre>
            <h3>User Details:</h3>
            <pre>User ID: {profile['user_id']}
Name: {profile['user_name']}</pre>
            <h3>Token Details:</h3>
            <pre>Expires: {token_expiry.strftime('%Y-%m-%d %H:%M:%S')}</pre>
            <p>You can close this window now.</p>
            """

        except Exception as e:
            return f"""
            <h1>✗ Error: {str(e)}</h1>
            <p>Please try again with a fresh login.</p>
            """

    return """
    <h1>✗ Authentication Failed</h1>
    <p>No request token received or status was not success.</p>
    """

if __name__ == '__main__':
    if not API_KEY or not API_SECRET:
        print("ERROR: KITE_API_KEY and KITE_API_SECRET must be set in .env")
        sys.exit(1)
    
    kite = KiteConnect(api_key=API_KEY)
    
    print("="*60)
    print("Kite Connect Token Handler")
    print("="*60)
    print(f"\n1. Open this URL in your browser:\n{kite.login_url()}")
    print(f"\n2. Login to Zerodha")
    print(f"3. You'll be redirected to http://localhost:8080/login")
    print(f"4. The page will show your request token")
    print("\n" + "="*60)
    print("IMPORTANT: Make sure your Kite app redirect URL is set to:")
    print("http://localhost:8080/login")
    print("in https://developers.kite.trade/")
    print("="*60)
    print("Server running on http://localhost:8080")
    print("Press Ctrl+C to stop")
    print("="*60 + "\n")

    app.run(host='0.0.0.0', port=8080, debug=False)
