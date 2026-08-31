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


def _stub_run_once_deps(orchestrator, mock_market, mock_signal, mock_executor,
                        market_open=True, should_stop_trading=False):
    """Wire deterministic test doubles into the orchestrator for run_once."""
    mock_market.is_market_open.return_value = market_open
    mock_market.kite = None
    mock_market.new_cycle.return_value = None
    mock_market._get_instruments.return_value = None
    mock_market.get_cycle_metrics.return_value = {}
    mock_market.get_realtime_price.return_value = 2500.0
    mock_market.prefetch_quotes.return_value = None
    mock_market.prefetch_historical.return_value = None

    mock_executor.broker = Mock()
    mock_executor.broker.get_holdings.return_value = {
        'cash': 20000.0,
        'positions': [],
        'total_value': 20000.0
    }
    mock_executor.broker.kite = Mock()
    mock_executor.broker.kite.positions.return_value = {'net': []}
    mock_executor.risk_manager = Mock()
    mock_executor.risk_manager.positions = []
    mock_executor.risk_manager.daily_pnl = 0.0
    mock_executor.risk_manager.max_daily_loss = 1000.0
    mock_executor.should_stop_trading.return_value = should_stop_trading
    mock_executor.execute_signal.return_value = {
        'success': True,
        'order_id': 'TEST_ORDER_001',
        'position': Mock(),
        'signal': Mock(),
        'timestamp': datetime.now().isoformat(),
        'paper_trading': True
    }
    mock_executor.monitor_positions.return_value = []
    mock_executor.monitor_holdings.return_value = []
    mock_executor.get_execution_summary.return_value = {
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

    mock_signal.generate_signals_for_watchlist.return_value = [
        {
            'symbol': 'RELIANCE',
            'action': 'BUY',
            'current_price': 2500.0,
            'position_size': 2,
            'investment_amount': 5000.0,
            'stop_loss': 2450.0,
            'target': 2600.0,
            'risk_reward_ratio': 2.0,
            'confidence': 80,
            'overall_score': 0.6
        }
    ]

    orchestrator.market_data = mock_market
    orchestrator.signal_generator = mock_signal
    orchestrator.order_executor = mock_executor
    orchestrator.market_regime = Mock()
    orchestrator.market_regime.detect_regime.return_value = 'BULL'
    orchestrator.dynamic_universe = Mock()
    orchestrator.dynamic_universe.get_top_candidates.return_value = {'scan_universe': ['RELIANCE']}
    orchestrator.smart_exit = Mock()
    orchestrator.smart_exit.check_all.return_value = []
    orchestrator.mtf = Mock()
    orchestrator.mtf.confirm.return_value = {'aligned': True, 'strict': True}
    orchestrator.scorer = Mock()
    orchestrator.scorer.score.return_value = {
        'skip': False,
        'total_score': 80,
        'components': {},
        'size_fraction': 1.0
    }
    orchestrator.position_sizing = Mock()
    orchestrator.position_sizing.calculate.return_value = {
        'qty': 2,
        'investment_amount': 5000.0,
        'reason': 'deterministic test sizing',
    }
    orchestrator.explainer = Mock()
    orchestrator.explainer.format_skip.return_value = ''
    orchestrator.explainer.format_buy.return_value = ''
    orchestrator.telegram = Mock()
    orchestrator.email = Mock()
    orchestrator._store.get_broker_state = Mock(return_value={})
    mock_market.is_market_holiday.return_value = False

    # Disable external-service-dependent guards
    orchestrator._is_correlated_with_open = lambda *a, **kw: False
    orchestrator._sector_concentration_exceeded = lambda *a, **kw: False
    orchestrator._has_negative_news = lambda *a, **kw: False
    orchestrator._near_earnings = lambda *a, **kw: False
    orchestrator._order_too_large_vs_adv = lambda *a, **kw: False
    orchestrator._check_reentry_eligibility = lambda *a, **kw: True


class TestTradingOrchestrator:
    """Integration tests for Trading Orchestrator"""
    
    @patch('trading_orchestrator.MarketDataFetcher')
    @patch('trading_orchestrator.SignalGenerator')
    @patch('trading_orchestrator.OrderExecutor')
    def test_run_once_market_closed(self, mock_executor, mock_signal, mock_market):
        """Test running trading cycle when market is closed"""
        mock_market_instance = Mock()
        mock_signal_instance = Mock()
        mock_executor_instance = Mock()
        orchestrator = TradingOrchestrator()
        _stub_run_once_deps(orchestrator, mock_market_instance, mock_signal_instance,
                            mock_executor_instance, market_open=False)

        result = orchestrator.run_once()

        assert result['market_open'] is False
        assert len(result['signals_generated']) == 0
        assert len(result['orders_executed']) == 0

    @patch('trading_orchestrator.MarketDataFetcher')
    @patch('trading_orchestrator.SignalGenerator')
    @patch('trading_orchestrator.OrderExecutor')
    def test_run_once_known_nse_holiday(self, mock_executor, mock_signal, mock_market):
        """Holiday handling is deterministic and independent of today's date."""
        mock_market_instance = Mock()
        mock_signal_instance = Mock()
        mock_executor_instance = Mock()
        orchestrator = TradingOrchestrator()
        _stub_run_once_deps(orchestrator, mock_market_instance, mock_signal_instance,
                            mock_executor_instance, market_open=False)
        mock_market_instance.is_market_holiday.return_value = True

        result = orchestrator.run_once()

        assert result['market_open'] is False
        assert result['signals_generated'] == []
        assert result['orders_executed'] == []
    
    @patch('trading_orchestrator.MarketDataFetcher')
    @patch('trading_orchestrator.SignalGenerator')
    @patch('trading_orchestrator.OrderExecutor')
    def test_run_once_with_signal(self, mock_executor, mock_signal, mock_market):
        """Test running trading cycle with buy signal"""
        mock_market_instance = Mock()
        mock_signal_instance = Mock()
        mock_executor_instance = Mock()
        orchestrator = TradingOrchestrator()
        _stub_run_once_deps(orchestrator, mock_market_instance, mock_signal_instance,
                            mock_executor_instance, market_open=True, should_stop_trading=False)

        result = orchestrator.run_once()

        assert result['market_open'] is True
        assert len(result['signals_generated']) == 1
        assert len(result['orders_executed']) == 1
        assert result['orders_executed'][0]['success'] is True
    
    @patch('trading_orchestrator.MarketDataFetcher')
    @patch('trading_orchestrator.SignalGenerator')
    @patch('trading_orchestrator.OrderExecutor')
    def test_run_once_risk_limit_reached(self, mock_executor, mock_signal, mock_market):
        """Test running trading cycle when risk limit reached"""
        mock_market_instance = Mock()
        mock_signal_instance = Mock()
        mock_executor_instance = Mock()
        orchestrator = TradingOrchestrator()
        _stub_run_once_deps(orchestrator, mock_market_instance, mock_signal_instance,
                            mock_executor_instance, market_open=True, should_stop_trading=True)

        result = orchestrator.run_once()

        assert len(result['errors']) > 0
        assert 'trading halted' in result['errors'][0].lower()
    
    def test_get_performance_report(self):
        """Test getting performance report"""
        orchestrator = TradingOrchestrator()
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

    def test_get_trade_log(self):
        """Test getting trade log"""
        orchestrator = TradingOrchestrator()
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
        mock_market_instance = Mock()
        mock_signal_instance = Mock()
        mock_executor_instance = Mock()
        _stub_run_once_deps(orchestrator, mock_market_instance, mock_signal_instance,
                            mock_executor_instance, market_open=True, should_stop_trading=False)

        result = orchestrator.run_once()

        # Verify complete flow
        assert result['market_open'] is True
        assert len(result['signals_generated']) == 1
        assert len(result['orders_executed']) == 1
        assert result['orders_executed'][0]['success'] is True
        assert len(orchestrator.trade_log) == 1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
