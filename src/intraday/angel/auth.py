"""
Angel One Authentication Module

Handles Angel One SmartAPI authentication with TOTP.
Completely separate from Kite authentication.
"""
import logging
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class AngelAuth:
    """Angel One SmartAPI authentication handler."""
    
    def __init__(self, api_key: str, client_code: str, password: str, totp_secret: str):
        """
        Initialize Angel authentication.
        
        Args:
            api_key: Angel One API key
            client_code: Angel One client code
            password: Angel One password/MPIN
            totp_secret: TOTP secret for 2FA
        """
        self.api_key = api_key
        self.client_code = client_code
        self.password = password
        self.totp_secret = totp_secret
        
        self.jwt_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.feed_token: Optional[str] = None
        self.authenticated = False
        self.auth_time: Optional[datetime] = None
        
        # SmartAPI client (lazy loaded)
        self._smartapi = None
    
    def _get_smartapi_client(self):
        """Lazy load SmartAPI client."""
        if self._smartapi is None:
            try:
                from SmartApi import SmartConnect
                self._smartapi = SmartConnect(self.api_key)
                logger.info("[ANGEL AUTH] SmartAPI client initialized")
            except ImportError:
                logger.warning("[ANGEL AUTH] smartapi-python not installed. Install with: pip install smartapi-python")
                raise ImportError("smartapi-python package not installed")
        return self._smartapi
    
    def _generate_totp(self) -> str:
        """Generate TOTP code from secret."""
        try:
            import pyotp
            totp = pyotp.TOTP(self.totp_secret)
            return totp.now()
        except ImportError:
            logger.error("[ANGEL AUTH] pyotp not installed. Install with: pip install pyotp")
            raise ImportError("pyotp package not installed")
    
    def authenticate(self) -> Dict[str, Any]:
        """
        Authenticate with Angel One SmartAPI.
        
        Returns:
            Dict with authentication status and tokens
            
        Raises:
            ImportError: If required packages not installed
            Exception: If authentication fails
        """
        try:
            smartapi = self._get_smartapi_client()
            totp = self._generate_totp()
            
            logger.info("[ANGEL AUTH] Attempting authentication...")
            
            # Generate session
            data = smartapi.generateSession(self.client_code, self.password, totp)
            
            if data.get('status') == False:
                error_msg = data.get('message', 'Authentication failed')
                logger.error(f"[ANGEL AUTH] Authentication failed: {error_msg}")
                self.authenticated = False
                return {
                    'success': False,
                    'error': error_msg,
                    'authenticated': False
                }
            
            # Extract tokens
            self.jwt_token = data['data'].get('jwtToken')
            self.refresh_token = data['data'].get('refreshToken')
            self.authenticated = True
            self.auth_time = datetime.now()
            
            # Get feed token for WebSocket
            try:
                self.feed_token = smartapi.getfeedToken()
                logger.info("[ANGEL AUTH] Feed token obtained")
            except Exception as e:
                logger.warning(f"[ANGEL AUTH] Could not get feed token: {e}")
            
            logger.info(f"[ANGEL AUTH] Authentication successful at {self.auth_time}")
            
            return {
                'success': True,
                'authenticated': True,
                'jwt_token': self.jwt_token,
                'refresh_token': self.refresh_token,
                'feed_token': self.feed_token,
                'auth_time': self.auth_time.isoformat()
            }
            
        except ImportError as e:
            logger.error(f"[ANGEL AUTH] Import error: {e}")
            return {
                'success': False,
                'error': str(e),
                'authenticated': False
            }
        except Exception as e:
            logger.error(f"[ANGEL AUTH] Authentication error: {e}")
            self.authenticated = False
            return {
                'success': False,
                'error': str(e),
                'authenticated': False
            }
    
    def refresh_tokens(self) -> Dict[str, Any]:
        """
        Refresh JWT token using refresh token.
        
        Returns:
            Dict with refresh status and new tokens
        """
        if not self.refresh_token:
            logger.error("[ANGEL AUTH] No refresh token available")
            return {
                'success': False,
                'error': 'No refresh token available',
                'authenticated': False
            }
        
        try:
            smartapi = self._get_smartapi_client()
            
            logger.info("[ANGEL AUTH] Refreshing tokens...")
            
            # Generate new token
            data = smartapi.generateToken(self.refresh_token)
            
            if data.get('status') == False:
                error_msg = data.get('message', 'Token refresh failed')
                logger.error(f"[ANGEL AUTH] Token refresh failed: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg,
                    'authenticated': False
                }
            
            # Update tokens
            self.jwt_token = data['data'].get('jwtToken')
            self.auth_time = datetime.now()
            
            logger.info("[ANGEL AUTH] Token refresh successful")
            
            return {
                'success': True,
                'authenticated': True,
                'jwt_token': self.jwt_token,
                'auth_time': self.auth_time.isoformat()
            }
            
        except Exception as e:
            logger.error(f"[ANGEL AUTH] Token refresh error: {e}")
            return {
                'success': False,
                'error': str(e),
                'authenticated': False
            }
    
    def logout(self) -> bool:
        """
        Logout from Angel One SmartAPI.
        
        Returns:
            True if logout successful
        """
        try:
            smartapi = self._get_smartapi_client()
            result = smartapi.terminateSession(self.client_code)
            
            self.jwt_token = None
            self.refresh_token = None
            self.feed_token = None
            self.authenticated = False
            self.auth_time = None
            
            logger.info("[ANGEL AUTH] Logout successful")
            return True
            
        except Exception as e:
            logger.error(f"[ANGEL AUTH] Logout error: {e}")
            return False
    
    def is_authenticated(self) -> bool:
        """Check if currently authenticated."""
        return self.authenticated and self.jwt_token is not None
    
    def get_auth_status(self) -> Dict[str, Any]:
        """Get current authentication status."""
        return {
            'authenticated': self.authenticated,
            'has_jwt_token': self.jwt_token is not None,
            'has_refresh_token': self.refresh_token is not None,
            'has_feed_token': self.feed_token is not None,
            'auth_time':	self.auth_time.isoformat() if self.auth_time else None
        }
