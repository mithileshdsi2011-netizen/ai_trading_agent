"""
Test cases for Risk Manager
"""
import pytest
from datetime import datetime
from unittest.mock import Mock, patch

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.risk_manager import RiskManager, Position, PositionStatus
from config import config


class TestRiskManager:
    """Test cases for RiskManager"""
    
    @pytest.fixture
    def risk_manager(self):
        """Create a RiskManager instance"""
        return RiskManager()
    
    @pytest.fixture
    def sample_signal(self):
        """Create a sample trading signal"""
        return {
            'symbol': 'RELIANCE',
            'action': 'BUY',
            'current_price': 2500.0,
            'position_size': 1,
            'investment_amount': 2500.0,
            'stop_loss': 2450.0,
            'target': 2600.0,
            'risk_reward_ratio': 2.0,
            'confidence': 80,
            'overall_score': 0.6,
            '_research': {'sector': 'Energy'},
            'market_regime': 'BULL'
        }
    
    def test_initialization(self, risk_manager):
        """Test risk manager initialization"""
        assert risk_manager is not None
        assert len(risk_manager.positions) == 0
        assert risk_manager.daily_pnl == 0.0
        assert risk_manager.daily_trades == 0
    
    def test_can_open_position_valid(self, risk_manager, sample_signal):
        """Test can open position with valid signal"""
        result = risk_manager.can_open_position(sample_signal)
        assert result is True
    
    def test_can_open_position_max_positions(self, risk_manager, sample_signal):
        """Test can open position when max positions reached"""
        # Add maximum positions
        for i in range(config.MAX_POSITIONS):
            position = Position(
                symbol=f'STOCK{i}',
                entry_price=100.0,
                quantity=10,
                stop_loss=95.0,
                target=110.0,
                entry_time=datetime.now()
            )
            risk_manager.positions.append(position)
        
        result = risk_manager.can_open_position(sample_signal)
        assert result is False
    
    def test_can_open_position_low_risk_reward(self, risk_manager, sample_signal):
        """Test can open position with low risk-reward ratio"""
        sample_signal['risk_reward_ratio'] = 1.0
        result = risk_manager.can_open_position(sample_signal)
        assert result is False
    
    def test_can_open_position_low_confidence(self, risk_manager, sample_signal):
        """Test can open position with low confidence"""
        sample_signal['confidence'] = 50
        result = risk_manager.can_open_position(sample_signal)
        assert result is False
    
    def test_can_open_position_exceeds_capital(self, risk_manager, sample_signal):
        """Test can open position when investment exceeds capital"""
        sample_signal['investment_amount'] = config.TRADING_AMOUNT + 1000
        result = risk_manager.can_open_position(sample_signal)
        assert result is False
    
    def test_open_position(self, risk_manager, sample_signal):
        """Test opening a position"""
        position = risk_manager.open_position(sample_signal)
        
        assert position.symbol == sample_signal['symbol']
        assert position.entry_price == sample_signal['current_price']
        assert position.quantity == sample_signal['position_size']
        assert position.stop_loss == sample_signal['stop_loss']
        assert position.target == sample_signal['target']
        assert position.status == PositionStatus.OPEN
        assert len(risk_manager.positions) == 1
        assert risk_manager.daily_trades == 1
    
    def test_check_positions_stop_loss(self, risk_manager):
        """Test checking positions for stop loss"""
        position = Position(
            symbol='RELIANCE',
            entry_price=2500.0,
            quantity=2,
            stop_loss=2450.0,
            target=2600.0,
            entry_time=datetime.now()
        )
        risk_manager.positions.append(position)
        
        current_prices = {'RELIANCE': 2440.0}  # Below stop loss
        exit_signals = risk_manager.check_positions(current_prices)
        
        assert len(exit_signals) == 1
        assert exit_signals[0]['symbol'] == 'RELIANCE'
        assert exit_signals[0]['status'] == 'STOPPED_OUT'
    
    def test_check_positions_target_hit(self, risk_manager):
        """Test checking positions for target hit"""
        position = Position(
            symbol='RELIANCE',
            entry_price=2500.0,
            quantity=2,
            stop_loss=2450.0,
            target=2600.0,
            entry_time=datetime.now()
        )
        risk_manager.positions.append(position)
        
        current_prices = {'RELIANCE': 2610.0}  # Above target
        exit_signals = risk_manager.check_positions(current_prices)
        
        assert len(exit_signals) == 1
        assert exit_signals[0]['symbol'] == 'RELIANCE'
        assert exit_signals[0]['status'] == 'TARGET_HIT'
    
    def test_check_positions_no_exit(self, risk_manager):
        """Test checking positions when no exit condition met"""
        position = Position(
            symbol='RELIANCE',
            entry_price=2500.0,
            quantity=2,
            stop_loss=2450.0,
            target=2600.0,
            entry_time=datetime.now()
        )
        risk_manager.positions.append(position)
        
        current_prices = {'RELIANCE': 2520.0}  # Between SL and target
        exit_signals = risk_manager.check_positions(current_prices)
        
        assert len(exit_signals) == 0
    
    def test_close_all_positions(self, risk_manager):
        """Test closing all positions"""
        position1 = Position(
            symbol='RELIANCE',
            entry_price=2500.0,
            quantity=2,
            stop_loss=2450.0,
            target=2600.0,
            entry_time=datetime.now()
        )
        position2 = Position(
            symbol='TCS',
            entry_price=3500.0,
            quantity=1,
            stop_loss=3450.0,
            target=3600.0,
            entry_time=datetime.now()
        )
        risk_manager.positions.extend([position1, position2])
        
        current_prices = {'RELIANCE': 2550.0, 'TCS': 3550.0}
        exit_signals = risk_manager.close_all_positions(current_prices)
        
        assert len(exit_signals) == 2
        assert all(signal['status'] == 'CLOSED' for signal in exit_signals)
    
    def test_get_position_summary(self, risk_manager):
        """Test getting position summary"""
        # Add some closed positions
        position1 = Position(
            symbol='RELIANCE',
            entry_price=2500.0,
            quantity=2,
            stop_loss=2450.0,
            target=2600.0,
            entry_time=datetime.now(),
            status=PositionStatus.TARGET_HIT,
            exit_price=2600.0,
            exit_time=datetime.now(),
            pnl=200.0,
            pnl_percentage=4.0
        )
        
        position2 = Position(
            symbol='TCS',
            entry_price=3500.0,
            quantity=1,
            stop_loss=3450.0,
            target=3600.0,
            entry_time=datetime.now(),
            status=PositionStatus.STOPPED_OUT,
            exit_price=3450.0,
            exit_time=datetime.now(),
            pnl=-50.0,
            pnl_percentage=-1.43
        )
        
        risk_manager.positions.extend([position1, position2])
        
        summary = risk_manager.get_position_summary()
        
        assert summary['open_positions'] == 0
        assert summary['closed_positions'] == 2
        assert summary['total_pnl'] == 150.0
        assert summary['winning_trades'] == 1
        assert summary['losing_trades'] == 1
        assert summary['win_rate'] == 0.5
    
    def test_should_stop_trading_daily_loss(self, risk_manager):
        """Test stop trading due to daily loss limit"""
        risk_manager.daily_pnl = -config.TRADING_AMOUNT * 0.06  # Exceeds 5% limit
        
        result = risk_manager.should_stop_trading()
        assert result is True
    
    def test_should_stop_trading_consecutive_losses(self, risk_manager):
        """Test stop trading due to consecutive losses"""
        # Add 3 consecutive losing trades
        for i in range(3):
            position = Position(
                symbol=f'STOCK{i}',
                entry_price=100.0,
                quantity=10,
                stop_loss=95.0,
                target=110.0,
                entry_time=datetime.now(),
                status=PositionStatus.STOPPED_OUT,
                exit_price=95.0,
                exit_time=datetime.now(),
                pnl=-50.0,
                pnl_percentage=-5.0
            )
            risk_manager.positions.append(position)
        
        result = risk_manager.should_stop_trading()
        assert result is True
    
    def test_reset_daily(self, risk_manager):
        """Test daily reset"""
        risk_manager.daily_pnl = -100.0
        risk_manager.daily_trades = 5
        
        risk_manager.reset_daily()
        
        assert risk_manager.daily_pnl == 0.0
        assert risk_manager.daily_trades == 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
