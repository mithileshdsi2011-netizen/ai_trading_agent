"""
Integration Test Cases for AI Trading Agent
"""
import pytest
from unittest.mock import Mock, patch
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.trading_orchestrator import TradingOrchestrator
from config import config


class TestTradingOrchestrator:
    """Integration tests for Trading Orchestrator"""
    
    @pytest.fixture
    def orchestrator(self):
        """Create a TradingOrchestrator instance"""
        return TradingOrchestrator()
    
    @patch('trading_orchestrator.MarketDataFetcher')
    @patch('trading_orchestrator.SignalGenerator')
    @patch('trading_orchestrator.OrderExecutor')
    def test_run_once_market_closed(self, mock_executor, mock_signal, mock_market, orchestrator):
        """Test running trading cycle when market is closed"""
        mock_market_instance = Mock()
        mock_market.return_value = mock_market_instance
        mock_market_instance.is_market_open.return_value = False
        
        result = orchestrator.run_once()
        
        assert result['market_open'] is False
        assert len(result['signals_generated']) == 0
        assert len(result['orders_executed']) == 0
    
    @patch('trading_orchestrator.MarketDataFetcher')
    @patch('trading_orchestrator.SignalGenerator')
    @patch('trading_orchestrator.OrderExecutor')
    def test_run_once_with_signal(self, mock_executor, mock_signal, mock_market, orchestrator):
        """Test running trading cycle with buy signal"""
        # Mock market open
        mock_market_instance = Mock()
        mock_market.return_value = mock_market_instance
        mock_market_instance.is_market_open.return_value = True
        
        # Mock signal generation
        mock_signal_instance = Mock()
        mock_signal.return_value = mock_signal_instance
        mock_signal_instance.generate_signals_for_watchlist.return_value = [
            {
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
        ]
        
        # Mock order execution
        mock_executor_instance = Mock()
        mock_executor.return_value = mock_executor_instance
        mock_executor_instance.should_stop_trading.return_value = False
        mock_executor_instance.execute_signal.return_value = {
            'success': True,
            'order_id': 'TEST_ORDER_001',
            'position': Mock(),
            'signal': Mock(),
            'timestamp': datetime.now().isoformat(),
            'paper_trading': True
        }
        mock_executor_instance.monitor_positions.return_value = []
        mock_executor_instance.get_execution_summary.return_value = {
            'position_summary': {
                'open_positions': 1,
                'closed_positions': 0,
                'total_pnl': 0.0,
                'daily_pnl': 0.0,
                'daily_trades': 1,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0.0
            },
            'broker_holdings': {},
            'total_orders': 1,
            'paper_trading': True
        }
        
        result = orchestrator.run_once()
        
        assert result['market_open'] is True
        assert len(result['signals_generated']) == 1
        assert len(result['orders_executed']) == 1
        assert result['orders_executed'][0]['success'] is True
    
    @patch('trading_orchestrator.MarketDataFetcher')
    @patch('trading_orchestrator.SignalGenerator')
    @patch('trading_orchestrator.OrderExecutor')
    def test_run_once_risk_limit_reached(self, mock_executor, mock_signal, mock_market, orchestrator):
        """Test running trading cycle when risk limit reached"""
        mock_market_instance = Mock()
        mock_market.return_value = mock_market_instance
        mock_market_instance.is_market_open.return_value = True
        
        mock_executor_instance = Mock()
        mock_executor.return_value = mock_executor_instance
        mock_executor_instance.should_stop_trading.return_value = True
        
        result = orchestrator.run_once()
        
        assert len(result['errors']) > 0
        assert 'risk limits' in result['errors'][0].lower()
    
    def test_get_performance_report(self, orchestrator):
        """Test getting performance report"""
        # Add some trade logs
        orchestrator.trade_log = [
            {'success': True, 'timestamp': datetime.now().isoformat()},
            {'success': True, 'timestamp': datetime.now().isoformat()},
            {'success': False, 'timestamp': datetime.now().isoformat()}
        ]
        
        report = orchestrator.get_performance_report()
        
        assert 'timestamp' in report
        assert 'total_trades' in report
        assert 'successful_trades' in report
        assert 'success_rate' in report
        assert report['total_trades'] == 3
        assert report['successful_trades'] == 2
        assert report['success_rate'] == 2/3
    
    def test_get_trade_log(self, orchestrator):
        """Test getting trade log"""
        orchestrator.trade_log = [
            {'symbol': 'RELIANCE', 'action': 'BUY'},
            {'symbol': 'TCS', 'action': 'SELL'}
        ]
        
        log = orchestrator.get_trade_log()
        
        assert len(log) == 2
        assert log[0]['symbol'] == 'RELIANCE'
        assert log[1]['symbol'] == 'TCS'


class TestEndToEnd:
    """End-to-end integration tests"""
    
    @patch('trading_orchestrator.MarketDataFetcher')
    @patch('trading_orchestrator.SignalGenerator')
    @patch('trading_orchestrator.OrderExecutor')
    def test_complete_trading_cycle(self, mock_executor, mock_signal, mock_market):
        """Test complete trading cycle from signal to execution"""
        orchestrator = TradingOrchestrator()
        
        # Mock market open
        mock_market_instance = Mock()
        mock_market.return_value = mock_market_instance
        mock_market_instance.is_market_open.return_value = True
        
        # Mock signal generation
        mock_signal_instance = Mock()
        mock_signal.return_value = mock_signal_instance
        mock_signal_instance.generate_signals_for_watchlist.return_value = [
            {
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
        ]
        
        # Mock order execution
        mock_executor_instance = Mock()
        mock_executor.return_value = mock_executor_instance
        mock_executor_instance.should_stop_trading.return_value = False
        mock_executor_instance.execute_signal.return_value = {
            'success': True,
            'order_id': 'TEST_ORDER_001',
            'position': Mock(),
            'signal': Mock(),
            'timestamp': datetime.now().isoformat(),
            'paper_trading': True
        }
        mock_executor_instance.monitor_positions.return_value = []
        mock_executor_instance.get_execution_summary.return_value = {
            'position_summary': {
                'open_positions': 1,
                'closed_positions': 0,
                'total_pnl': 0.0,
                'daily_pnl': 0.0,
                'daily_trades': 1,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0.0
            },
            'broker_holdings': {},
            'total_orders': 1,
            'paper_trading': True
        }
        
        # Run cycle
        result = orchestrator.run_once()
        
        # Verify complete flow
        assert result['market_open'] is True
        assert len(result['signals_generated']) == 1
        assert len(result['orders_executed']) == 1
        assert result['orders_executed'][0]['success'] is True
        assert len(orchestrator.trade_log) == 1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
