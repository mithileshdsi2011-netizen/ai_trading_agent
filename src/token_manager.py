"""
Token Manager Module
Handles automated Zerodha Kite Connect token management
"""
import json
import os
from datetime import datetime, timedelta
from typing import Optional
import logging
from kiteconnect import KiteConnect

from config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TokenManager:
    """Manages Kite Connect access tokens with automatic refresh"""
    
    _token_invalidated: bool = False  # set True when Zerodha rejects mid-session
    
    def __init__(self):
        # Get the project root directory (parent of src)
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.token_file = os.path.join(project_root, "data", "kite_token.json")
        self.kite = None
        self.access_token = None
        self.token_expiry = None
        self._ensure_data_directory()
        self._load_token()
    
    def _ensure_data_directory(self):
        """Ensure data directory exists"""
        # Get the project root directory
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(project_root, "data")
        os.makedirs(data_dir, exist_ok=True)
    
    def _load_token(self):
        """Load token from file"""
        try:
            if os.path.exists(self.token_file):
                with open(self.token_file, 'r') as f:
                    token_data = json.load(f)
                    self.access_token = token_data.get('access_token')
                    expiry_str = token_data.get('expiry')
                    if expiry_str:
                        self.token_expiry = datetime.fromisoformat(expiry_str)
                    
                    logger.info(f"Loaded token from file, expires at: {self.token_expiry}")
        except Exception as e:
            logger.error(f"Error loading token: {e}")
    
    def _save_token(self, access_token: str):
        """Save token to file"""
        try:
            # Kite access tokens typically last for 24 hours
            self.token_expiry = datetime.now() + timedelta(hours=23)
            
            token_data = {
                'access_token': access_token,
                'expiry': self.token_expiry.isoformat(),
                'saved_at': datetime.now().isoformat()
            }
            
            with open(self.token_file, 'w') as f:
                json.dump(token_data, f, indent=2)
            
            logger.info(f"Token saved, expires at: {self.token_expiry}")
        except Exception as e:
            logger.error(f"Error saving token: {e}")
    
    def is_token_valid(self) -> bool:
        """Check if current token is valid"""
        if not self.access_token or not self.token_expiry:
            return False
        if TokenManager._token_invalidated:
            return False
        # Add 1 hour buffer before expiry
        return datetime.now() < (self.token_expiry - timedelta(hours=1))
    
    def mark_token_invalid(self):
        """Mark token as rejected by Zerodha mid-session (auth errors)"""
        TokenManager._token_invalidated = True
        logger.error(
            "Kite token rejected mid-session. "
            "Run: python get_kite_token.py to generate a fresh token."
        )
    
    def get_access_token(self, request_token: Optional[str] = None) -> str:
        """
        Get valid access token from stored file

        Args:
            request_token: New request token for refresh (if needed)

        Returns:
            Valid access token

        Raises:
            ValueError: If token is expired and no request token provided
        """
        self._load_token()  # Always pick the latest file token
        if self.is_token_valid():
            logger.info("Using existing valid token from storage")
            return self.access_token

        # Token expired or invalid
        # Request tokens from .env cannot be reused (they expire within minutes)
        # User must run get_kite_token.py to get a fresh request token
        raise ValueError(
            "Access token expired. Please run: python get_kite_token.py\n"
            "This will open a browser for you to login and generate a fresh request token.\n"
            "The new access token will be automatically saved to data/kite_token.json."
        )
    
    def _refresh_token(self, request_token: str) -> str:
        """
        Refresh access token using request token
        
        Args:
            request_token: Request token from OAuth flow
        
        Returns:
            New access token
        """
        try:
            kite = KiteConnect(api_key=config.KITE_API_KEY)
            session = kite.generate_session(request_token, api_secret=config.KITE_API_SECRET)
            access_token = session['access_token']
            
            # Save new token
            self.access_token = access_token
            self._save_token(access_token)
            
            logger.info("Token refreshed successfully")
            return access_token
        
        except Exception as e:
            logger.error(f"Error refreshing token: {e}")
            raise
    
    def initialize_kite(self, request_token: Optional[str] = None) -> KiteConnect:
        """
        Initialize KiteConnect instance with valid token
        
        Args:
            request_token: Request token for initial setup or refresh
        
        Returns:
            Initialized KiteConnect instance
        """
        try:
            access_token = self.get_access_token(request_token)
            
            self.kite = KiteConnect(api_key=config.KITE_API_KEY)
            self.kite.set_access_token(access_token)
            
            # Test connection
            profile = self.kite.profile()
            logger.info(f"Kite initialized for user: {profile['user_name']}")
            
            return self.kite
        
        except Exception as e:
            logger.error(f"Error initializing Kite: {e}")
            raise
    
    def clear_token(self):
        """Clear stored token (for testing or manual reset)"""
        self.access_token = None
        self.token_expiry = None
        if os.path.exists(self.token_file):
            os.remove(self.token_file)
        logger.info("Token cleared")
