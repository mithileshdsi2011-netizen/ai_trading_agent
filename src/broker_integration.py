"""
Broker Integration Module
Integrates with Zerodha Kite Connect for order execution
"""
from typing import Dict, Optional, List
import logging
import os
import json
from datetime import datetime
from enum import Enum

from config import config
from token_manager import TokenManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Module-level cache of NSE tick sizes (lazy-loaded from Kite instruments)
_TICK_SIZE_CACHE: Dict[str, float] = {}


class BrokerErrorCategory(Enum):
    """Broker-facing error categories used to decide retry/notification behaviour."""
    SUCCESS = "success"
    CDSL_AUTH = "cdsl_auth"
    INSUFFICIENT_QUANTITY = "insufficient_quantity"
    EXCHANGE_CLOSED = "exchange_closed"
    RMS_REJECTION = "rms_rejection"
    NETWORK_FAILURE = "network_failure"
    RATE_LIMIT = "rate_limit"
    INVALID_TOKEN = "invalid_token"
    ORDER_FROZEN = "order_frozen"
    IP_WHITELIST = "ip_whitelist"
    OTHER = "other"


# Per-error-category policy: retry, notify, continue trading, retry schedule, recovery path
BROKER_ERROR_POLICY = {
    BrokerErrorCategory.CDSL_AUTH: {
        'retry': True,
        'notify': True,
        'continue_trading': True,
        'retry_policy': 'every_cycle',
        'recovery_path': 'Complete CDSL/TPIN authorisation in Kite → Portfolio → Holdings → Authorise. Bot will retry automatically.',
    },
    BrokerErrorCategory.NETWORK_FAILURE: {
        'retry': True,
        'notify': False,
        'continue_trading': True,
        'retry_policy': 'exponential_backoff',
        'recovery_path': 'Wait for network recovery; bot will back off and retry.',
    },
    BrokerErrorCategory.RATE_LIMIT: {
        'retry': True,
        'notify': False,
        'continue_trading': True,
        'retry_policy': 'exponential_backoff',
        'recovery_path': 'Wait for Kite rate-limit window; bot will back off and retry.',
    },
    BrokerErrorCategory.EXCHANGE_CLOSED: {
        'retry': True,
        'notify': False,
        'continue_trading': True,
        'retry_policy': 'next_session',
        'recovery_path': 'Markets closed; bot will retry at the next market open.',
    },
    BrokerErrorCategory.INVALID_TOKEN: {
        'retry': False,
        'notify': True,
        'continue_trading': False,
        'retry_policy': 'manual',
        'recovery_path': 'Refresh Kite access token / login before the bot can place orders.',
    },
    BrokerErrorCategory.IP_WHITELIST: {
        'retry': False,
        'notify': True,
        'continue_trading': False,
        'retry_policy': 'manual',
        'recovery_path': 'Add current public IP to Kite Developer Console whitelist.',
    },
    BrokerErrorCategory.INSUFFICIENT_QUANTITY: {
        'retry': False,
        'notify': True,
        'continue_trading': True,
        'retry_policy': 'manual',
        'recovery_path': 'Verify holdings/positions; bot will retry once available quantity is corrected.',
    },
    BrokerErrorCategory.RMS_REJECTION: {
        'retry': True,
        'notify': True,
        'continue_trading': True,
        'retry_policy': 'exponential_backoff',
        'recovery_path': 'Review RMS/margin reason in Kite; bot will retry transient rejections after a short backoff.',
    },
    BrokerErrorCategory.ORDER_FROZEN: {
        'retry': True,
        'notify': True,
        'continue_trading': True,
        'retry_policy': 'exponential_backoff',
        'recovery_path': 'Order frozen by exchange; bot will retry after a short backoff.',
    },
    BrokerErrorCategory.OTHER: {
        'retry': True,
        'notify': True,
        'continue_trading': True,
        'retry_policy': 'every_cycle',
        'recovery_path': 'Unknown broker error; bot will retry next cycle.',
    },
}


def get_error_policy(category_value: str) -> Dict:
    """Return the policy for a given broker error category value (string)."""
    try:
        cat = BrokerErrorCategory(category_value)
    except ValueError:
        cat = BrokerErrorCategory.OTHER
    return BROKER_ERROR_POLICY.get(cat, BROKER_ERROR_POLICY[BrokerErrorCategory.OTHER])


class BrokerIntegration:
    """Handles broker integration for order execution"""
    
    def __init__(self):
        self.kite = None
        self.paper_trading = config.PAPER_TRADING
        self.live_ready = False
        self.token_manager = None
        self.paper_portfolio = {
            'cash': config.TRADING_AMOUNT,
            'positions': {},
            'orders': []
        }
        self.startup_timestamp = datetime.now().isoformat()

        mode = 'PAPER' if self.paper_trading else 'LIVE'
        error = None
        try:
            if not self.paper_trading:
                self.token_manager = TokenManager()
                self._init_kite_connect()
                self._verify_static_ip()
                self.live_ready = True
                mode = 'LIVE'
                logger.info(f"Broker initialized in LIVE mode at {self.startup_timestamp}")
            else:
                logger.info(f"Broker initialized in PAPER mode at {self.startup_timestamp}")
            self._write_broker_status(mode, self.live_ready, None)
        except Exception as e:
            error = str(e)
            self._write_broker_status('FAILED', False, error)
            logger.error(f"Broker live initialization failed: {error}")
            raise
    
    def _init_kite_connect(self):
        """Initialize Kite Connect connection with token management (one-shot, no fallback)."""
        from kiteconnect import KiteConnect
        self.kite = self.token_manager.initialize_kite()
        logger.info("Kite Connect initialized successfully with existing token")

    def _verify_static_ip(self):
        """Verify the current public IP matches the configured static/whitelisted IP."""
        import urllib.request
        import json as _json
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'data', 'static_ip_config.json'
        )
        if not os.path.exists(cfg_path):
            logger.warning("Static IP config not found; skipping static IP verification")
            return
        with open(cfg_path) as f:
            cfg = _json.load(f)
        whitelisted = cfg.get('whitelisted_ip') or cfg.get('static_ip')
        if not whitelisted:
            logger.warning("No whitelisted_ip/static_ip in static_ip_config.json; skipping verification")
            return
        current_ip = None
        for url in ('https://api.ipify.org', 'https://ifconfig.me/ip', 'https://icanhazip.com'):
            try:
                current_ip = urllib.request.urlopen(url, timeout=5).read().decode().strip()
                break
            except Exception:
                continue
        if not current_ip:
            raise RuntimeError("Could not determine current public IP for static IP verification")
        if current_ip != whitelisted:
            raise RuntimeError(
                f"Current public IP {current_ip} does not match whitelisted IP {whitelisted}. "
                "Update data/static_ip_config.json or Kite Developer Console before starting."
            )
        logger.info(f"Static IP verified: {current_ip} matches whitelisted IP")

    def _write_broker_status(self, mode: str, live_ready: bool, error: Optional[str] = None):
        """Persist broker mode and startup status for the dashboard."""
        try:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            status_path = os.path.join(root, 'data', 'broker_status.json')
            os.makedirs(os.path.dirname(status_path), exist_ok=True)
            with open(status_path, 'w') as f:
                json.dump({
                    'mode': mode,
                    'live_ready': live_ready,
                    'startup_timestamp': getattr(self, 'startup_timestamp', datetime.now().isoformat()),
                    'error': error,
                    'updated_at': datetime.now().isoformat()
                }, f)
        except Exception as e:
            logger.warning(f"Could not write broker_status.json: {e}")
    
    def place_order(self, signal: Dict) -> Dict:
        """
        Place an order based on trading signal
        
        Args:
            signal: Trading signal dictionary
        
        Returns:
            Order response dictionary
        """
        if self.paper_trading:
            return self._place_paper_order(signal)
        else:
            return self._place_real_order(signal)
    
    def _place_paper_order(self, signal: Dict) -> Dict:
        """
        Place a paper trading order
        
        Args:
            signal: Trading signal
        
        Returns:
            Order response
        """
        symbol = signal['symbol']
        action = signal['action']
        price = signal['current_price']
        quantity = signal['position_size']
        
        # Check if we have enough cash
        required_amount = price * quantity
        if required_amount > self.paper_portfolio['cash']:
            logger.warning(f"Insufficient cash for paper order: {required_amount} > {self.paper_portfolio['cash']}")
            return {
                'success': False,
                'error': 'Insufficient funds',
                'order_id': None
            }
        
        # Execute paper order
        if action == 'BUY':
            self.paper_portfolio['cash'] -= required_amount
            self.paper_portfolio['positions'][symbol] = {
                'quantity': quantity,
                'entry_price': price,
                'stop_loss': signal['stop_loss'],
                'target': signal['target'],
                'entry_time': datetime.now().isoformat()
            }
        elif action == 'SELL':
            # Check if we have enough position to sell
            if symbol not in self.paper_portfolio['positions']:
                logger.warning(f"No position to sell for {symbol}")
                return {
                    'success': False,
                    'error': 'No position to sell',
                    'order_id': None,
                    'error_category': BrokerErrorCategory.INSUFFICIENT_QUANTITY.value,
                    'retry': False,
                }
            position = self.paper_portfolio['positions'][symbol]
            available = position['quantity']
            if quantity > available:
                logger.warning(
                    f"Insufficient quantity to sell for {symbol}: requested {quantity}, available {available}"
                )
                return {
                    'success': False,
                    'error': f'Insufficient quantity: requested {quantity}, available {available}',
                    'order_id': None,
                    'error_category': BrokerErrorCategory.INSUFFICIENT_QUANTITY.value,
                    'retry': False,
                }
            self.paper_portfolio['cash'] += price * quantity
            position['quantity'] -= quantity
            if position['quantity'] == 0:
                del self.paper_portfolio['positions'][symbol]
        
        order_id = f"PAPER_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        order_record = {
            'order_id': order_id,
            'symbol': symbol,
            'action': action,
            'price': price,
            'quantity': quantity,
            'timestamp': datetime.now().isoformat(),
            'status': 'COMPLETED'
        }
        
        self.paper_portfolio['orders'].append(order_record)
        
        logger.info(f"Paper order placed: {action} {quantity} {symbol} @ {price}")
        
        return {
            'success': True,
            'order_id': order_id,
            'status': 'COMPLETED',
            'paper_trading': True,
            'error_category': BrokerErrorCategory.SUCCESS.value,
            'retry': False,
        }
    
    @staticmethod
    def _classify_error(error_message: str) -> BrokerErrorCategory:
        """Classify a Kite error message into a structured error category."""
        if not error_message:
            return BrokerErrorCategory.OTHER
        err = error_message.lower()
        if any(k in err for k in ('cdsl', 'tpin', 'authoris', 'authorize', 'depository')):
            return BrokerErrorCategory.CDSL_AUTH
        if any(k in err for k in ('insufficient quantity', 'insufficient qty', 'quantity not enough')):
            return BrokerErrorCategory.INSUFFICIENT_QUANTITY
        if any(k in err for k in ('exchange closed', 'market closed', 'not a trading day', 'holiday')):
            return BrokerErrorCategory.EXCHANGE_CLOSED
        if any(k in err for k in ('rms', 'risk management', 'margin', 'exposure', 'limit exceeded')):
            return BrokerErrorCategory.RMS_REJECTION
        if any(k in err for k in ('network', 'timeout', 'connection', 'unreachable', 'timed out')):
            return BrokerErrorCategory.NETWORK_FAILURE
        if any(k in err for k in ('rate limit', 'too many requests', 'throttled')):
            return BrokerErrorCategory.RATE_LIMIT
        if any(k in err for k in ('invalid token', 'token expired', 'unauthorized', 'authentication')):
            return BrokerErrorCategory.INVALID_TOKEN
        if any(k in err for k in ('frozen', 'freeze', 'blocked', 'disclosed')):
            return BrokerErrorCategory.ORDER_FROZEN
        if 'ip' in err and 'not allowed' in err:
            return BrokerErrorCategory.IP_WHITELIST
        return BrokerErrorCategory.OTHER

    def _get_tick_size(self, symbol: str) -> float:
        """Return NSE tick size for a symbol, defaulting to 0.05."""
        if not _TICK_SIZE_CACHE and self.kite:
            try:
                instruments = self.kite.instruments("NSE")
                for inst in instruments:
                    _TICK_SIZE_CACHE[inst['tradingsymbol']] = inst.get('tick_size', 0.05)
            except Exception as e:
                logger.warning(f"Could not load NSE tick sizes: {e}")
        return _TICK_SIZE_CACHE.get(symbol, 0.05)

    def _place_real_order(self, signal: Dict) -> Dict:
        """
        Place a real order via Kite Connect
        
        Args:
            signal: Trading signal
        
        Returns:
            Order response
        """
        try:
            symbol = signal['symbol']
            action = signal['action']
            price = signal['current_price']
            quantity = signal['position_size']
            
            # Kite Connect uses plain NSE symbols (e.g., RELIANCE not RELIANCE.NS)
            kite_symbol = symbol.replace(".NS", "")  # strip suffix if accidentally present
            
            # Determine transaction type
            transaction_type = 'BUY' if action == 'BUY' else 'SELL'
            
            # Determine product type based on trading mode
            product = self.kite.PRODUCT_CNC if config.TRADING_MODE == "swing" else self.kite.PRODUCT_MIS
            validity = self.kite.VALIDITY_DAY
            
            # Use the correct tick size for this symbol
            tick_size = self._get_tick_size(kite_symbol)
            if tick_size <= 0:
                tick_size = 0.05
            
            # Kite blocks market orders without market protection via API.
            # Use limit orders with a small buffer to act like market orders.
            if action == 'BUY':
                raw_price = price * 1.01  # 1% above current price
            else:
                raw_price = price * 0.99  # 1% below current price
            limit_price = round(raw_price / tick_size) * tick_size
            limit_price = round(limit_price, 2)
            
            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=self.kite.EXCHANGE_NSE,
                tradingsymbol=kite_symbol,
                transaction_type=transaction_type,
                quantity=quantity,
                product=product,
                order_type=self.kite.ORDER_TYPE_LIMIT,
                price=limit_price,
                validity=validity
            )
            
            logger.info(f"Real order placed: {action} {quantity} {symbol} @ {price}, Order ID: {order_id}")
            
            return {
                'success': True,
                'order_id': order_id,
                'status': 'PENDING',
                'paper_trading': False,
                'error_category': BrokerErrorCategory.SUCCESS.value,
                'retry': False,
            }
        
        except Exception as e:
            err = str(e)
            logger.error(f"Error placing real order: {err}")
            # Detect IP whitelist error and show actionable fix
            if 'IP' in err and 'not allowed' in err:
                current_ip = self._get_public_ip()
                logger.error(
                    f"\n{'='*60}\n"
                    f"  ❌ IP WHITELIST ERROR — ORDERS BLOCKED\n"
                    f"  Current IP : {current_ip}\n"
                    f"  ACTION     : Add {current_ip} to Kite Developer Console\n"
                    f"  URL        : https://developers.kite.trade/apps\n"
                    f"{'='*60}"
                )
                # Save current IP so startup script can check
                self._save_ip(current_ip)
            category = self._classify_error(err)
            policy = get_error_policy(category.value)
            return {
                'success': False,
                'error': err,
                'order_id': None,
                'error_category': category.value,
                'retry': policy['retry'],
                'continue_trading': policy['continue_trading'],
                'retry_policy': policy['retry_policy'],
                'recovery_path': policy['recovery_path'],
            }
    
    def _get_public_ip(self) -> str:
        """Fetch current public IPv4 address."""
        try:
            import urllib.request
            return urllib.request.urlopen('https://api.ipify.org', timeout=5).read().decode().strip()
        except Exception:
            return 'unknown'

    def _save_ip(self, ip: str):
        """Save current IP to data/last_known_ip.txt for startup checks."""
        try:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            with open(os.path.join(root, 'data', 'last_known_ip.txt'), 'w') as f:
                f.write(ip)
        except Exception:
            pass

    def cancel_order(self, order_id: str) -> Dict:
        """
        Cancel an order
        
        Args:
            order_id: Order ID to cancel
        
        Returns:
            Cancellation response
        """
        if self.paper_trading:
            return self._cancel_paper_order(order_id)
        else:
            return self._cancel_real_order(order_id)
    
    def _cancel_paper_order(self, order_id: str) -> Dict:
        """Cancel a paper trading order"""
        # Find and remove order
        for i, order in enumerate(self.paper_portfolio['orders']):
            if order['order_id'] == order_id:
                # Reverse the transaction
                if order['action'] == 'BUY':
                    self.paper_portfolio['cash'] += order['price'] * order['quantity']
                    if order['symbol'] in self.paper_portfolio['positions']:
                        del self.paper_portfolio['positions'][order['symbol']]
                elif order['action'] == 'SELL':
                    self.paper_portfolio['cash'] -= order['price'] * order['quantity']
                
                self.paper_portfolio['orders'].pop(i)
                logger.info(f"Paper order cancelled: {order_id}")
                return {'success': True}
        
        return {'success': False, 'error': 'Order not found'}
    
    def _cancel_real_order(self, order_id: str) -> Dict:
        """Cancel a real order via Kite Connect"""
        try:
            self.kite.cancel_order(order_id=order_id, variety=self.kite.VARIETY_REGULAR)
            logger.info(f"Real order cancelled: {order_id}")
            return {'success': True}
        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_positions(self) -> List[Dict]:
        """
        Get current positions
        
        Returns:
            List of positions
        """
        if self.paper_trading:
            return self._get_paper_positions()
        else:
            return self._get_real_positions()
    
    def _get_paper_positions(self) -> List[Dict]:
        """Get paper trading positions"""
        positions = []
        for symbol, pos in self.paper_portfolio['positions'].items():
            positions.append({
                'symbol': symbol,
                'quantity': pos['quantity'],
                'entry_price': pos['entry_price'],
                'stop_loss': pos['stop_loss'],
                'target': pos['target'],
                'entry_time': pos['entry_time']
            })
        return positions
    
    def _get_real_positions(self) -> List[Dict]:
        """Get real positions from Kite Connect"""
        try:
            positions = self.kite.positions()
            return positions.get('day', [])
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return []
    
    def get_holdings(self) -> Dict:
        """
        Get portfolio holdings and cash
        
        Returns:
            Portfolio summary
        """
        if self.paper_trading:
            return {
                'cash': self.paper_portfolio['cash'],
                'positions': self._get_paper_positions(),
                'total_value': self.paper_portfolio['cash'] + sum(
                    pos['quantity'] * pos['entry_price'] 
                    for pos in self._get_paper_positions()
                )
            }
        else:
            try:
                holdings = self.kite.holdings()
                total_value = sum(h['quantity'] * h['last_price'] for h in holdings)
                # Read available cash from Kite margins
                try:
                    margins = self.kite.margins()
                    eq = margins.get("equity", {})
                    avail = eq.get("available", {})
                    available_cash = avail.get("live_balance") or avail.get("cash") or eq.get("net", 0)
                except Exception:
                    available_cash = 0
                return {
                    'cash': available_cash,
                    'positions': holdings,
                    'total_value': total_value + available_cash
                }
            except Exception as e:
                logger.error(f"Error getting holdings: {e}")
                return {'cash': 0, 'positions': [], 'total_value': 0}
    
    def get_order_status(self, order_id: str) -> Dict:
        """
        Get status of an order
        
        Args:
            order_id: Order ID
        
        Returns:
            Order status
        """
        if self.paper_trading:
            for order in self.paper_portfolio['orders']:
                if order['order_id'] == order_id:
                    return order
            return {'error': 'Order not found'}
        else:
            try:
                orders = self.kite.orders()
                for order in orders:
                    if order['order_id'] == order_id:
                        return order
                return {'error': 'Order not found'}
            except Exception as e:
                logger.error(f"Error getting order status: {e}")
                return {'error': str(e)}
