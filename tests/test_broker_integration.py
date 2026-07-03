"""
Test cases for Broker Integration
"""
import pytest
from unittest.mock import Mock, patch
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.broker_integration import BrokerIntegration
from src.config import config


class TestBrokerIntegration:
    """Test cases for BrokerIntegration"""
    
    @pytest.fixture
    def broker(self):
        """Create a BrokerIntegration instance"""
        return BrokerIntegration()
    
    @pytest.fixture
    def sample_signal(self):
        """Create a sample trading signal"""
        return {
            'symbol': 'RELIANCE',
            'action': 'BUY',
            'current_price': 2500.0,
            'position_size': 2,
            'investment_amount': 5000.0,
            'stop_loss': 2450.0,
            'target': 2600.0,
            'risk_reward_ratio': 2.0,
            'confidence': 0.8,
            'overall_score': 0.6
        }
    
    def test_initialization_paper_trading(self, broker):
        """Test broker initialization in paper trading mode"""
        assert broker is not None
        assert broker.paper_trading is True
        assert broker.paper_portfolio is not None
        assert broker.paper_portfolio['cash'] == config.TRADING_AMOUNT
    
    def test_place_paper_order_buy(self, broker, sample_signal):
        """Test placing a paper buy order"""
        result = broker.place_order(sample_signal)
        
        assert result['success'] is True
        assert result['order_id'] is not None
        assert result['paper_trading'] is True
        assert result['status'] == 'COMPLETED'
        
        # Check portfolio updated
        assert sample_signal['symbol'] in broker.paper_portfolio['positions']
        assert broker.paper_portfolio['cash'] < config.TRADING_AMOUNT
    
    def test_place_paper_order_sell(self, broker, sample_signal):
        """Test placing a paper sell order"""
        # First buy
        buy_signal = sample_signal.copy()
        broker.place_order(buy_signal)
        
        # Then sell
        sell_signal = sample_signal.copy()
        sell_signal['action'] = 'SELL'
        sell_signal['current_price'] = 2550.0
        
        result = broker.place_order(sell_signal)
        
        assert result['success'] is True
        assert sample_signal['symbol'] not in broker.paper_portfolio['positions']
    
    def test_place_paper_order_insufficient_funds(self, broker, sample_signal):
        """Test paper order with insufficient funds"""
        # Reduce cash
        broker.paper_portfolio['cash'] = 100.0
        
        result = broker.place_order(sample_signal)
        
        assert result['success'] is False
        assert result['error'] == 'Insufficient funds'
    
    def test_place_paper_order_sell_without_position(self, broker, sample_signal):
        """Test paper sell order without position"""
        sell_signal = sample_signal.copy()
        sell_signal['action'] = 'SELL'
        
        result = broker.place_order(sell_signal)
        
        assert result['success'] is False
        assert result['error'] == 'No position to sell'
    
    def test_cancel_paper_order(self, broker, sample_signal):
        """Test cancelling a paper order"""
        # Place order
        order_result = broker.place_order(sample_signal)
        order_id = order_result['order_id']
        
        # Cancel order
        cancel_result = broker.cancel_order(order_id)
        
        assert cancel_result['success'] is True
        assert sample_signal['symbol'] not in broker.paper_portfolio['positions']
    
    def test_cancel_paper_order_not_found(self, broker):
        """Test cancelling non-existent order"""
        result = broker.cancel_order('INVALID_ORDER_ID')
        
        assert result['success'] is False
        assert result['error'] == 'Order not found'
    
    def test_get_paper_positions(self, broker, sample_signal):
        """Test getting paper positions"""
        # Place order
        broker.place_order(sample_signal)
        
        positions = broker.get_positions()
        
        assert len(positions) == 1
        assert positions[0]['symbol'] == sample_signal['symbol']
        assert positions[0]['quantity'] == sample_signal['position_size']
    
    def test_get_paper_holdings(self, broker, sample_signal):
        """Test getting paper holdings"""
        # Place order
        broker.place_order(sample_signal)
        
        holdings = broker.get_holdings()
        
        assert 'cash' in holdings
        assert 'positions' in holdings
        assert 'total_value' in holdings
        assert holdings['cash'] < config.TRADING_AMOUNT
        assert len(holdings['positions']) == 1
    
    def test_get_paper_order_status(self, broker, sample_signal):
        """Test getting paper order status"""
        # Place order
        order_result = broker.place_order(sample_signal)
        order_id = order_result['order_id']
        
        # Get status
        status = broker.get_order_status(order_id)
        
        assert status['order_id'] == order_id
        assert status['status'] == 'COMPLETED'
    
    def test_get_paper_order_status_not_found(self, broker):
        """Test getting status of non-existent order"""
        status = broker.get_order_status('INVALID_ORDER_ID')
        
        assert 'error' in status


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
