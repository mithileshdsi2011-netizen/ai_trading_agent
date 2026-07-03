"""
Broker Integration Module
Integrates with Zerodha Kite Connect for order execution
"""
from typing import Dict, Optional, List
import logging
from datetime import datetime

from config import config
from token_manager import TokenManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class BrokerIntegration:
    """Handles broker integration for order execution"""
    
    def __init__(self):
        self.kite = None
        self.paper_trading = config.PAPER_TRADING
        self.token_manager = None
        self.paper_portfolio = {
            'cash': config.TRADING_AMOUNT,
            'positions': {},
            'orders': []
        }
        
        if not self.paper_trading:
            self.token_manager = TokenManager()
            self._init_kite_connect()
    
    def _init_kite_connect(self):
        """Initialize Kite Connect connection with token management"""
        try:
            from kiteconnect import KiteConnect
            
            # Try to initialize with existing token
            try:
                self.kite = self.token_manager.initialize_kite()
                logger.info("Kite Connect initialized successfully with existing token")
            except ValueError as e:
                # Token expired, need request token
                logger.warning(f"Token expired: {e}")
                logger.info("Please run token refresh: python get_kite_token.py")
                logger.warning("Falling back to paper trading mode")
                self.paper_trading = True
        
        except ImportError:
            logger.warning("kiteconnect not installed, using paper trading")
            self.paper_trading = True
        except Exception as e:
            logger.error(f"Error initializing Kite Connect: {e}")
            self.paper_trading = True
    
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
            # Check if we have position to sell
            if symbol in self.paper_portfolio['positions']:
                position = self.paper_portfolio['positions'][symbol]
                self.paper_portfolio['cash'] += price * quantity
                del self.paper_portfolio['positions'][symbol]
            else:
                logger.warning(f"No position to sell for {symbol}")
                return {
                    'success': False,
                    'error': 'No position to sell',
                    'order_id': None
                }
        
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
            'paper_trading': True
        }
    
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
            
            # Kite blocks market orders without market protection via API.
            # Use limit orders with a small buffer to act like market orders.
            # Round to nearest tick size (0.05 for most NSE stocks).
            if action == 'BUY':
                limit_price = round(price * 1.01 / 0.05) * 0.05  # 1% above current price
            else:
                limit_price = round(price * 0.99 / 0.05) * 0.05  # 1% below current price
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
                'paper_trading': False
            }
        
        except Exception as e:
            logger.error(f"Error placing real order: {e}")
            return {
                'success': False,
                'error': str(e),
                'order_id': None
            }
    
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
