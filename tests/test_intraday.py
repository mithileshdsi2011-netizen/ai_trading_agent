"""
Intraday Trading Module Tests

Tests for the Intraday Intelligence & Trading Engine.
Completely separate from Swing Trading tests.
"""
import pytest
import sys
import os
import threading
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from intraday.models import (
    IntradaySignal, IntradayPosition, IntradayTrade, IntradayConfig,
    MarketRegime, SignalDirection, SignalClassification, PositionStatus
)
from intraday.config import IntradayConfigLoader
from intraday.features import IntradayFeatures
from intraday.scorer import IntradayScorer
from intraday.risk_manager import IntradayRiskManager
from intraday.position_sizer import IntradayPositionSizer
from intraday.paper_executor import PaperTradeExecutor
from intraday.live_executor import LiveTradeExecutor
from intraday.trade_manager import IntradayTradeManager
from intraday.metrics import IntradayMetrics
from intraday.validators import IntradayValidator, signal_idempotency_key, IST
from intraday.state import IntradayStateStore
from intraday.angel.websocket import AngelWebSocket
from intraday.angel.auth import AngelAuth


def make_signal(**overrides) -> IntradaySignal:
    """Build a valid LONG signal; override any field via kwargs."""
    defaults = dict(
        symbol="RELIANCE",
        direction=SignalDirection.LONG,
        score=75.0,
        classification=SignalClassification.GOOD_SETUP,
        entry_price=2500.0,
        stop_loss=2475.0,
        target=2550.0,
        risk_reward=2.0,
        vwap_score=80.0,
        ema_score=75.0,
        volume_score=70.0,
        momentum_score=65.0,
        breakout_score=60.0,
        market_regime_score=50.0,
        market_regime=MarketRegime.BULLISH,
        current_price=2500.0,
        vwap=2490.0,
        ema_9=2495.0,
        ema_20=2480.0,
        rsi=55.0,
        volume=1000000,
        relative_volume=1.5,
        validation_passed=True,
    )
    defaults.update(overrides)
    return IntradaySignal(**defaults)


def make_position(**overrides) -> IntradayPosition:
    """Build an open LONG paper position; override any field via kwargs."""
    defaults = dict(
        id="test-pos",
        symbol="RELIANCE",
        direction=SignalDirection.LONG,
        entry_price=2500.0,
        quantity=10,
        stop_loss=2475.0,
        target=2550.0,
        entry_time=datetime.now(),
        current_price=2500.0,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
        is_paper=True,
    )
    defaults.update(overrides)
    return IntradayPosition(**defaults)


class TestIntradayModels:
    """Test intraday data models."""
    
    def test_intraday_config_defaults(self):
        """Test IntradayConfig default values."""
        config = IntradayConfig()
        
        assert config.angel_live_trading == False
        assert config.paper_trading == True
        assert config.intraday_capital == 10000.0
        assert config.max_trades_per_day == 5
        assert config.max_daily_loss_pct == 0.02
        assert config.risk_per_trade_pct == 0.01
        assert config.min_risk_reward == 1.5
        assert config.max_concurrent_positions == 2
    
    def test_intraday_config_to_dict(self):
        """Test IntradayConfig serialization."""
        config = IntradayConfig(
            angel_api_key="test_key",
            angel_client_code="test_client",
            intraday_capital=50000.0
        )
        
        result = config.to_dict()
        
        # test_key is 8 chars, so it shows "test_key..." (full + ...)
        # test_client is 11 chars, so it shows "test_cli..." ([:8] + ...)
        assert result['angel_api_key'] == 'test_key...'
        assert result['angel_client_code'] == 'test_cli...'
        assert result['intraday_capital'] == 50000.0
        assert result['paper_trading'] == True
    
    def test_intraday_signal_creation(self):
        """Test IntradaySignal creation."""
        signal = IntradaySignal(
            symbol="RELIANCE",
            direction=SignalDirection.LONG,
            score=75.5,
            classification=SignalClassification.GOOD_SETUP,
            entry_price=2500.0,
            stop_loss=2475.0,
            target=2550.0,
            risk_reward=2.0,
            vwap_score=80.0,
            ema_score=75.0,
            volume_score=70.0,
            momentum_score=65.0,
            breakout_score=60.0,
            market_regime_score=50.0,
            market_regime=MarketRegime.BULLISH,
            current_price=2500.0,
            vwap=2490.0,
            ema_9=2495.0,
            ema_20=2480.0,
            rsi=55.0,
            volume=1000000,
            relative_volume=1.5,
            validation_passed=True
        )
        
        assert signal.symbol == "RELIANCE"
        assert signal.direction == SignalDirection.LONG
        assert signal.score == 75.5
        assert signal.risk_reward == 2.0
        assert signal.validation_passed == True
    
    def test_intraday_position_creation(self):
        """Test IntradayPosition creation."""
        position = IntradayPosition(
            id="test-id",
            symbol="TCS",
            direction=SignalDirection.LONG,
            entry_price=3500.0,
            quantity=10,
            stop_loss=3475.0,
            target=3550.0,
            entry_time=datetime.now(),
            current_price=3500.0,
            unrealized_pnl=0.0,
            unrealized_pnl_pct=0.0,
            is_paper=True
        )
        
        assert position.symbol == "TCS"
        assert position.quantity == 10
        assert position.status == PositionStatus.OPEN
        assert position.is_paper == True


class TestIntradayConfigLoader:
    """Test intraday configuration loader."""
    
    def test_load_config_with_defaults(self, monkeypatch):
        """Test config loading with default values."""
        # Mock environment variables
        monkeypatch.setenv("ANGEL_API_KEY", "")
        monkeypatch.setenv("ANGEL_CLIENT_CODE", "")
        monkeypatch.setenv("ANGEL_LIVE_TRADING", "false")
        monkeypatch.setenv("ANGEL_PAPER_TRADING", "true")
        
        config = IntradayConfigLoader.load()
        
        assert config.angel_live_trading == False
        assert config.paper_trading == True
        assert config.intraday_capital == 10000.0
    
    def test_paper_trading_forced_true(self, monkeypatch):
        """Test that paper trading is forced to true for V1."""
        monkeypatch.setenv("ANGEL_PAPER_TRADING", "false")
        
        config = IntradayConfigLoader.load()
        
        # Should be forced to true for V1
        assert config.paper_trading == True
    
    def test_live_trading_forced_false(self, monkeypatch):
        """Test that live trading is forced to false for V1."""
        monkeypatch.setenv("ANGEL_LIVE_TRADING", "true")
        
        config = IntradayConfigLoader.load()
        
        # Should be forced to false for V1
        assert config.angel_live_trading == False


class TestIntradayFeatures:
    """Test intraday technical features."""
    
    def test_calculate_ema(self):
        """Test EMA calculation."""
        import pandas as pd
        
        prices = pd.Series([100, 101, 102, 103, 104, 105])
        ema = IntradayFeatures.calculate_ema(prices, 3)
        
        assert len(ema) == len(prices)
        assert ema.iloc[-1] > prices.iloc[-2]  # EMA should be higher than previous price
    
    def test_calculate_rsi(self):
        """Test RSI calculation."""
        import pandas as pd
        
        prices = pd.Series([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114])
        rsi = IntradayFeatures.calculate_rsi(prices, 14)
        
        assert len(rsi) == len(prices)
        assert 0 <= rsi.iloc[-1] <= 100  # RSI should be between 0 and 100
    
    def test_calculate_vwap(self):
        """Test VWAP calculation."""
        import pandas as pd
        
        df = pd.DataFrame({
            'high': [105, 110, 115],
            'low': [95, 100, 105],
            'close': [100, 105, 110],
            'volume': [1000, 1500, 2000]
        })
        
        vwap = IntradayFeatures.calculate_vwap(df)
        
        assert vwap > 0
        assert vwap > df['low'].min()
        assert vwap < df['high'].max()
    
    def test_calculate_atr(self):
        """Test ATR calculation."""
        import pandas as pd
        
        df = pd.DataFrame({
            'high': [105, 110, 115, 120],
            'low': [95, 100, 105, 110],
            'close': [100, 105, 110, 115]
        })
        
        atr = IntradayFeatures.calculate_atr(df, 14)
        
        assert atr >= 0
    
    def test_ema_alignment(self):
        """Test EMA alignment determination."""
        # Bullish
        alignment = IntradayFeatures.calculate_ema_alignment(100, 95)
        assert alignment == "BULLISH"
        
        # Bearish
        alignment = IntradayFeatures.calculate_ema_alignment(95, 100)
        assert alignment == "BEARISH"
        
        # Neutral
        alignment = IntradayFeatures.calculate_ema_alignment(100, 100)
        assert alignment == "NEUTRAL"
    
    def test_vwap_structure(self):
        """Test VWAP structure determination."""
        # Above VWAP
        structure = IntradayFeatures.calculate_vwap_structure(100, 95)
        assert structure == "ABOVE_VWAP"
        
        # Below VWAP
        structure = IntradayFeatures.calculate_vwap_structure(95, 100)
        assert structure == "BELOW_VWAP"
        
        # At VWAP
        structure = IntradayFeatures.calculate_vwap_structure(100, 100)
        assert structure == "AT_VWAP"


class TestIntradayScorer:
    """Test intraday scoring engine."""
    
    def test_calculate_score(self):
        """Test signal scoring."""
        config = IntradayConfig()
        scorer = IntradayScorer(config)
        
        symbol_data = {
            'symbol': 'RELIANCE',
            'features': {
                'current_price': 2500.0,
                'vwap': 2490.0,
                'ema_9': 2495.0,
                'ema_20': 2480.0,
                'rsi': 55.0,
                'atr': 10.0,
                'or_high': 2510.0,
                'or_low': 2480.0,
                'momentum': 0.5,
                'volume': 1000000,
                'relative_volume': 1.5,
                'ema_alignment': 'BULLISH',
                'vwap_structure': 'ABOVE_VWAP'
            }
        }
        
        signal = scorer.calculate_score(symbol_data, MarketRegime.BULLISH)
        
        assert signal is not None
        assert signal.symbol == 'RELIANCE'
        assert 0 <= signal.score <= 100
        assert signal.direction in [SignalDirection.LONG, SignalDirection.SHORT]
    
    def test_short_direction_scoring(self):
        """Bearish feature set produces a SHORT signal."""
        config = IntradayConfig()
        scorer = IntradayScorer(config)

        symbol_data = {
            'symbol': 'RELIANCE',
            'features': {
                'current_price': 2450.0,
                'vwap': 2470.0,
                'ema_9': 2455.0,
                'ema_20': 2480.0,
                'rsi': 35.0,
                'atr': 10.0,
                'or_high': 2490.0,
                'or_low': 2440.0,
                'momentum': -0.8,
                'volume': 2000000,
                'relative_volume': 2.0,
                'ema_alignment': 'BEARISH',
                'vwap_structure': 'BELOW_VWAP'
            }
        }

        signal = scorer.calculate_score(symbol_data, MarketRegime.BEARISH)
        assert signal is not None
        assert signal.direction == SignalDirection.SHORT
        # SHORT: stop above entry, target below entry
        assert signal.stop_loss > signal.entry_price
        assert signal.target < signal.entry_price

    def test_classify_signal(self):
        """Test signal classification."""
        config = IntradayConfig()
        scorer = IntradayScorer(config)
        
        # Strong setup
        classification = scorer._classify_signal(85.0)
        assert classification == SignalClassification.STRONG_SETUP
        
        # Good setup
        classification = scorer._classify_signal(75.0)
        assert classification == SignalClassification.GOOD_SETUP
        
        # Watch
        classification = scorer._classify_signal(65.0)
        assert classification == SignalClassification.WATCH
        
        # No trade
        classification = scorer._classify_signal(50.0)
        assert classification == SignalClassification.NO_TRADE


class TestIntradayRiskManager:
    """Test intraday risk manager."""
    
    def test_can_enter_trade(self):
        """Test trade entry permission."""
        config = IntradayConfig()
        risk_manager = IntradayRiskManager(config)
        
        signal = IntradaySignal(
            symbol="RELIANCE",
            direction=SignalDirection.LONG,
            score=75.0,
            classification=SignalClassification.GOOD_SETUP,
            entry_price=2500.0,
            stop_loss=2475.0,
            target=2550.0,
            risk_reward=2.0,
            vwap_score=80.0,
            ema_score=75.0,
            volume_score=70.0,
            momentum_score=65.0,
            breakout_score=60.0,
            market_regime_score=50.0,
            market_regime=MarketRegime.BULLISH,
            current_price=2500.0,
            vwap=2490.0,
            ema_9=2495.0,
            ema_20=2480.0,
            rsi=55.0,
            volume=1000000,
            relative_volume=1.5,
            validation_passed=True
        )
        
        can_enter, reason = risk_manager.can_enter_trade(signal)
        
        assert can_enter == True
        assert reason is None
    
    def test_max_trades_per_day_limit(self):
        """Test max trades per day limit."""
        config = IntradayConfig(max_trades_per_day=1)
        risk_manager = IntradayRiskManager(config)
        
        # Add one trade
        risk_manager._daily_state.trades_today = 1
        
        signal = IntradaySignal(
            symbol="RELIANCE",
            direction=SignalDirection.LONG,
            score=75.0,
            classification=SignalClassification.GOOD_SETUP,
            entry_price=2500.0,
            stop_loss=2475.0,
            target=2550.0,
            risk_reward=2.0,
            vwap_score=80.0,
            ema_score=75.0,
            volume_score=70.0,
            momentum_score=65.0,
            breakout_score=60.0,
            market_regime_score=50.0,
            market_regime=MarketRegime.BULLISH,
            current_price=2500.0,
            vwap=2490.0,
            ema_9=2495.0,
            ema_20=2480.0,
            rsi=55.0,
            volume=1000000,
            relative_volume=1.5,
            validation_passed=True
        )
        
        can_enter, reason = risk_manager.can_enter_trade(signal)
        
        assert can_enter == False
        assert "Max trades per day" in reason
    
    def test_daily_loss_limit(self):
        """Test daily loss limit."""
        config = IntradayConfig(intraday_capital=10000.0, max_daily_loss_pct=0.02)
        risk_manager = IntradayRiskManager(config)
        
        # Set realized P&L to exceed loss limit
        risk_manager._daily_state.realized_pnl = -250.0  # -2.5% of 10000
        
        signal = IntradaySignal(
            symbol="RELIANCE",
            direction=SignalDirection.LONG,
            score=75.0,
            classification=SignalClassification.GOOD_SETUP,
            entry_price=2500.0,
            stop_loss=2475.0,
            target=2550.0,
            risk_reward=2.0,
            vwap_score=80.0,
            ema_score=75.0,
            volume_score=70.0,
            momentum_score=65.0,
            breakout_score=60.0,
            market_regime_score=50.0,
            market_regime=MarketRegime.BULLISH,
            current_price=2500.0,
            vwap=2490.0,
            ema_9=2495.0,
            ema_20=2480.0,
            rsi=55.0,
            volume=1000000,
            relative_volume=1.5,
            validation_passed=True
        )
        
        can_enter, reason = risk_manager.can_enter_trade(signal)
        
        assert can_enter == False
        assert "Daily loss limit" in reason
    
    def test_get_daily_state(self):
        """Test daily state retrieval."""
        config = IntradayConfig()
        risk_manager = IntradayRiskManager(config)
        
        state = risk_manager.get_daily_state()
        
        assert 'trades_today' in state
        assert 'max_trades_per_day' in state
        assert 'realized_pnl' in state
        assert 'blocked' in state


class TestIntradayPositionSizer:
    """Test intraday position sizer."""
    
    def test_calculate_quantity(self):
        """Test position quantity calculation."""
        config = IntradayConfig(
            intraday_capital=10000.0,
            risk_per_trade_pct=0.01
        )
        sizer = IntradayPositionSizer(config)
        
        signal = IntradaySignal(
            symbol="RELIANCE",
            direction=SignalDirection.LONG,
            score=75.0,
            classification=SignalClassification.GOOD_SETUP,
            entry_price=2500.0,
            stop_loss=2475.0,
            target=2550.0,
            risk_reward=2.0,
            vwap_score=80.0,
            ema_score=75.0,
            volume_score=70.0,
            momentum_score=65.0,
            breakout_score=60.0,
            market_regime_score=50.0,
            market_regime=MarketRegime.BULLISH,
            current_price=2500.0,
            vwap=2490.0,
            ema_9=2495.0,
            ema_20=2480.0,
            rsi=55.0,
            volume=1000000,
            relative_volume=1.5,
            validation_passed=True
        )
        
        quantity, capital = sizer.calculate_quantity(signal, 10000.0)
        
        assert quantity >= 1
        assert capital > 0
        assert capital <= 10000.0


class TestPaperTradeExecutor:
    """Test paper trading executor."""
    
    def test_execute_entry(self):
        """Test paper trade entry."""
        config = IntradayConfig()
        risk_manager = IntradayRiskManager(config)
        position_sizer = IntradayPositionSizer(config)
        executor = PaperTradeExecutor(risk_manager, position_sizer, config)
        
        signal = IntradaySignal(
            symbol="RELIANCE",
            direction=SignalDirection.LONG,
            score=75.0,
            classification=SignalClassification.GOOD_SETUP,
            entry_price=2500.0,
            stop_loss=2475.0,
            target=2550.0,
            risk_reward=2.0,
            vwap_score=80.0,
            ema_score=75.0,
            volume_score=70.0,
            momentum_score=65.0,
            breakout_score=60.0,
            market_regime_score=50.0,
            market_regime=MarketRegime.BULLISH,
            current_price=2500.0,
            vwap=2490.0,
            ema_9=2495.0,
            ema_20=2480.0,
            rsi=55.0,
            volume=1000000,
            relative_volume=1.5,
            validation_passed=True
        )
        
        position = executor.execute_entry(signal)
        
        assert position is not None
        assert position.symbol == "RELIANCE"
        assert position.is_paper == True
        assert position.status == PositionStatus.OPEN
    
    def test_execute_exit(self):
        """Test paper trade exit."""
        config = IntradayConfig()
        risk_manager = IntradayRiskManager(config)
        position_sizer = IntradayPositionSizer(config)
        executor = PaperTradeExecutor(risk_manager, position_sizer, config)
        
        # Create a position
        position = IntradayPosition(
            id="test-id",
            symbol="RELIANCE",
            direction=SignalDirection.LONG,
            entry_price=2500.0,
            quantity=10,
            stop_loss=2475.0,
            target=2550.0,
            entry_time=datetime.now(),
            current_price=2550.0,
            unrealized_pnl=500.0,
            unrealized_pnl_pct=2.0,
            is_paper=True
        )
        
        risk_manager.add_position(position)
        
        trade = executor.execute_exit(position, 2550.0, "TARGET_HIT")
        
        assert trade is not None
        assert trade.symbol == "RELIANCE"
        assert trade.exit_reason == "TARGET_HIT"
        assert trade.net_pnl > 0
        assert trade.is_paper == True


class TestIntradayTradeManager:
    """Test intraday trade manager."""
    
    def test_add_trade(self, tmp_path):
        """Test adding a trade."""
        # Create temporary directory
        temp_dir = tmp_path / "intraday_test"
        temp_dir.mkdir()
        
        # Use temporary file
        data_file = str(temp_dir / "test_trades.json")
        manager = IntradayTradeManager(data_file=data_file)
        
        trade = IntradayTrade(
            id="test-id",
            symbol="RELIANCE",
            direction=SignalDirection.LONG,
            entry_price=2500.0,
            exit_price=2550.0,
            quantity=10,
            entry_time=datetime.now(),
            exit_time=datetime.now(),
            exit_reason="TARGET_HIT",
            gross_pnl=500.0,
            charges=5.0,
            net_pnl=495.0,
            pnl_pct=2.0,
            stop_loss=2475.0,
            target=2550.0,
            risk_reward=2.0,
            signal_score=75.0,
            signal_classification=SignalClassification.GOOD_SETUP,
            strategy="ORB",
            is_paper=True
        )
        
        manager.add_trade(trade)
        
        trades = manager.get_trades()
        assert len(trades) == 1
        assert trades[0].symbol == "RELIANCE"
    
    def test_get_performance_metrics(self, tmp_path):
        """Test performance metrics calculation."""
        # Create temporary directory
        temp_dir = tmp_path / "intraday_test2"
        temp_dir.mkdir()
        
        # Use temporary file
        data_file = str(temp_dir / "test_trades2.json")
        manager = IntradayTradeManager(data_file=data_file)
        
        # Add some trades
        trade1 = IntradayTrade(
            id="test-id-1",
            symbol="RELIANCE",
            direction=SignalDirection.LONG,
            entry_price=2500.0,
            exit_price=2550.0,
            quantity=10,
            entry_time=datetime.now(),
            exit_time=datetime.now(),
            exit_reason="TARGET_HIT",
            gross_pnl=500.0,
            charges=5.0,
            net_pnl=495.0,
            pnl_pct=2.0,
            stop_loss=2475.0,
            target=2550.0,
            risk_reward=2.0,
            signal_score=75.0,
            signal_classification=SignalClassification.GOOD_SETUP,
            strategy="ORB",
            is_paper=True
        )
        
        trade2 = IntradayTrade(
            id="test-id-2",
            symbol="TCS",
            direction=SignalDirection.LONG,
            entry_price=3500.0,
            exit_price=3450.0,
            quantity=10,
            entry_time=datetime.now(),
            exit_time=datetime.now(),
            exit_reason="STOP_LOSS",
            gross_pnl=-500.0,
            charges=5.0,
            net_pnl=-505.0,
            pnl_pct=-1.44,
            stop_loss=3475.0,
            target=3550.0,
            risk_reward=2.0,
            signal_score=70.0,
            signal_classification=SignalClassification.GOOD_SETUP,
            strategy="VWAP_MOMENTUM",
            is_paper=True
        )
        
        manager.add_trade(trade1)
        manager.add_trade(trade2)
        
        metrics = manager.get_performance_metrics()
        
        assert metrics['total_trades'] == 2
        assert metrics['winning_trades'] == 1
        assert metrics['losing_trades'] == 1
        assert metrics['net_pnl'] == -10.0


class TestIntradayMetrics:
    """Test intrayday metrics calculator."""
    
    def test_calculate_metrics_empty(self):
        """Test metrics calculation with no trades."""
        metrics_calc = IntradayMetrics()
        metrics = metrics_calc.calculate_metrics([])
        
        assert metrics['total_trades'] == 0
        assert metrics['win_rate'] == 0.0
        assert metrics['net_pnl'] == 0.0
    
    def test_calculate_metrics_with_trades(self):
        """Test metrics calculation with trades."""
        metrics_calc = IntradayMetrics()
        
        trades = [
            IntradayTrade(
                id="test-id-1",
                symbol="RELIANCE",
                direction=SignalDirection.LONG,
                entry_price=2500.0,
                exit_price=2550.0,
                quantity=10,
                entry_time=datetime.now(),
                exit_time=datetime.now(),
                exit_reason="TARGET_HIT",
                gross_pnl=500.0,
                charges=5.0,
                net_pnl=495.0,
                pnl_pct=2.0,
                stop_loss=2475.0,
                target=2550.0,
                risk_reward=2.0,
                signal_score=75.0,
                signal_classification=SignalClassification.GOOD_SETUP,
                strategy="ORB",
                is_paper=True
            )
        ]
        
        metrics = metrics_calc.calculate_metrics(trades)
        
        assert metrics['total_trades'] == 1
        assert metrics['winning_trades'] == 1
        assert metrics['net_pnl'] == 495.0
        assert metrics['win_rate'] == 100.0


class TestIntradayValidator:
    """Hard entry validation rules."""

    def test_market_hours_weekday_open(self):
        """Market open on a weekday mid-session."""
        v = IntradayValidator(IntradayConfig())
        monday = IST.localize(datetime(2026, 10, 5, 10, 0))  # Monday
        assert v.is_market_open(monday) is True

    def test_market_hours_rejects_weekend(self):
        """Market closed on Saturday."""
        v = IntradayValidator(IntradayConfig())
        saturday = IST.localize(datetime(2026, 10, 3, 11, 0))  # Saturday
        assert v.is_market_open(saturday) is False

    def test_market_hours_rejects_after_close(self):
        """Market closed after 15:30 IST."""
        v = IntradayValidator(IntradayConfig())
        evening = IST.localize(datetime(2026, 10, 5, 16, 0))
        assert v.is_market_open(evening) is False

    def test_entry_window_respects_last_entry_time(self):
        """Entries blocked after configured last_entry_time."""
        config = IntradayConfig(entry_start_time="09:30", last_entry_time="14:30")
        v = IntradayValidator(config)
        assert v.is_entry_window_open(IST.localize(datetime(2026, 10, 5, 10, 0))) is True
        assert v.is_entry_window_open(IST.localize(datetime(2026, 10, 5, 15, 0))) is False
        assert v.is_entry_window_open(IST.localize(datetime(2026, 10, 5, 9, 0))) is False

    def test_square_off_time(self):
        """Square-off flag set after configured square_off_time."""
        config = IntradayConfig(square_off_time="15:00")
        v = IntradayValidator(config)
        assert v.is_square_off_time(IST.localize(datetime(2026, 10, 5, 15, 5))) is True
        assert v.is_square_off_time(IST.localize(datetime(2026, 10, 5, 10, 0))) is False

    def test_stale_data_rejected(self):
        """No entry when market data is stale or missing."""
        v = IntradayValidator(IntradayConfig())
        signal = make_signal()
        mid_session = IST.localize(datetime(2026, 10, 5, 11, 0))
        ok, reason = v.validate_entry_conditions(signal, None, now=mid_session)
        assert ok is False
        assert "stale" in reason.lower()

    def test_missing_stop_loss_rejected(self):
        """Every intraday trade MUST have a stop loss."""
        v = IntradayValidator(IntradayConfig())
        signal = make_signal(stop_loss=0.0)
        ok, reason = v.validate_signal(signal)
        assert ok is False
        assert "stop loss" in reason.lower()

    def test_bad_rr_rejected(self):
        """Trades below minimum R:R are rejected."""
        v = IntradayValidator(IntradayConfig(min_risk_reward=1.5))
        signal = make_signal(risk_reward=1.0)
        ok, reason = v.validate_signal(signal)
        assert ok is False
        assert "Risk:Reward" in reason

    def test_no_trade_classification_rejected(self):
        """NO_TRADE signals are rejected at validation."""
        v = IntradayValidator(IntradayConfig())
        signal = make_signal(classification=SignalClassification.NO_TRADE)
        ok, reason = v.validate_signal(signal)
        assert ok is False

    def test_market_hours_gate_in_entry_conditions(self):
        """Entry conditions reject outside market hours."""
        v = IntradayValidator(IntradayConfig())
        signal = make_signal()
        ok, reason = v.validate_entry_conditions(
            signal, datetime.now(), now=IST.localize(datetime(2026, 10, 3, 11, 0))
        )
        assert ok is False
        assert "market hours" in reason.lower()


class TestConsecutiveLossProtection:
    """Consecutive-loss circuit breaker."""

    def test_consecutive_loss_limit(self):
        config = IntradayConfig(max_consecutive_losses=3)
        rm = IntradayRiskManager(config)
        rm._daily_state.consecutive_losses = 3
        ok, reason = rm.can_enter_trade(make_signal())
        assert ok is False
        assert "Consecutive loss" in reason

    def test_consecutive_losses_reset_on_win(self):
        config = IntradayConfig()
        rm = IntradayRiskManager(config)
        rm._daily_state.consecutive_losses = 2
        pos = make_position(realized_pnl=100.0)
        rm.record_trade_exit(pos)
        assert rm._daily_state.consecutive_losses == 0


class TestKillSwitch:
    """Intraday-only kill switch."""

    def test_kill_switch_blocks_entries(self):
        rm = IntradayRiskManager(IntradayConfig())
        rm.set_kill_switch(True)
        ok, reason = rm.can_enter_trade(make_signal())
        assert ok is False
        assert "Kill switch" in reason

    def test_kill_switch_clear_resumes(self):
        rm = IntradayRiskManager(IntradayConfig())
        rm.set_kill_switch(True)
        rm.set_kill_switch(False)
        ok, _ = rm.can_enter_trade(make_signal())
        assert ok is True


class TestDuplicateOrderPrevention:
    """Idempotency-key duplicate protection."""

    def test_duplicate_pending_key_rejected(self):
        rm = IntradayRiskManager(IntradayConfig())
        signal = make_signal()
        key = signal_idempotency_key(signal)
        rm.register_pending(key)
        ok, reason = rm.can_enter_trade(signal, idempotency_key=key)
        assert ok is False
        assert "Duplicate" in reason

    def test_existing_position_blocks_duplicate(self):
        rm = IntradayRiskManager(IntradayConfig())
        rm.add_position(make_position())
        ok, reason = rm.can_enter_trade(make_signal())
        assert ok is False
        assert "already exists" in reason


class TestPaperExecutorExits:
    """Paper exit conditions and EOD square-off."""

    def _executor(self, config=None):
        config = config or IntradayConfig()
        rm = IntradayRiskManager(config)
        return PaperTradeExecutor(rm, IntradayPositionSizer(config), config)

    def test_stop_loss_exit_long(self):
        ex = self._executor()
        pos = make_position(current_price=2470.0)  # below 2475 SL
        result = ex.check_exit_conditions(pos)
        assert result is not None
        assert result[2] == "STOP_LOSS"

    def test_target_exit_long(self):
        ex = self._executor()
        pos = make_position(current_price=2560.0)  # above 2550 target
        result = ex.check_exit_conditions(pos)
        assert result is not None
        assert result[2] == "TARGET_HIT"

    def test_stop_loss_exit_short(self):
        ex = self._executor()
        pos = make_position(
            direction=SignalDirection.SHORT,
            entry_price=2500.0, stop_loss=2525.0, target=2450.0,
            current_price=2530.0,
        )
        result = ex.check_exit_conditions(pos)
        assert result is not None
        assert result[2] == "STOP_LOSS"

    def test_eod_square_off(self):
        """EOD square-off closes all open paper positions."""
        ex = self._executor()
        ex.execute_entry(make_signal(symbol="AAA"))
        ex.execute_entry(make_signal(symbol="BBB"))
        assert len(ex.get_open_positions()) == 2
        trades = ex.eod_square_off()
        assert len(trades) == 2
        assert len(ex.get_open_positions()) == 0
        assert all(t.exit_reason == "EOD_SQUARE_OFF" for t in trades)


class TestLiveExecutorDisabled:
    """Live executor must always reject in V1."""

    def test_entry_rejected(self):
        ex = LiveTradeExecutor(MagicMock(), IntradayConfig())
        assert ex.execute_entry(make_signal()) is None

    def test_exit_rejected(self):
        ex = LiveTradeExecutor(MagicMock(), IntradayConfig())
        assert ex.execute_exit(make_position(), 2500.0, "TEST") is False

    def test_not_enabled(self):
        ex = LiveTradeExecutor(MagicMock(), IntradayConfig())
        assert ex.is_enabled() is False
        assert ex.get_status()['enabled'] is False

    def test_client_order_methods_disabled(self):
        """AngelClient order calls are hard-blocked for V1."""
        from intraday.angel.client import AngelClient
        client = AngelClient(AngelAuth('', '', '', ''))
        result = client.place_order({'variety': 'NORMAL'})
        assert result['success'] is False
        assert result['paper_mode'] is True


class TestAngelWebSocket:
    """WebSocket disconnect / no-auth handling."""

    def test_connect_fails_without_auth(self):
        ws = AngelWebSocket(AngelAuth('', '', '', ''))
        assert ws.connect() is False
        assert ws.is_connected() is False

    def test_status_disconnected(self):
        ws = AngelWebSocket(AngelAuth('', '', '', ''))
        status = ws.get_status()
        assert status['connected'] is False
        assert status['status'] == 'DISCONNECTED'

    def test_subscribe_fails_when_disconnected(self):
        ws = AngelWebSocket(AngelAuth('', '', '', ''))
        assert ws.subscribe(['3045']) is False

    def test_clean_disconnect(self):
        ws = AngelWebSocket(AngelAuth('', '', '', ''))
        assert ws.disconnect() is True
        assert ws.is_connected() is False

    def test_no_duplicate_connect(self):
        """Second connect() while running is ignored."""
        ws = AngelWebSocket(AngelAuth('', '', '', ''))
        ws._running = True  # simulate in-flight connection
        ws._connected = True
        assert ws.connect() is True
        assert ws._ws is None  # no new socket was created


class TestStateStore:
    """Intraday state persistence roundtrip."""

    def test_daily_state_roundtrip(self, tmp_path):
        store = IntradayStateStore(str(tmp_path / "state"))
        store.save_daily_state({'trades_today': 3, 'realized_pnl': -50.0,
                                'wins_today': 1, 'losses_today': 1,
                                'consecutive_losses': 1, 'blocked': False,
                                'block_reason': None})
        loaded = store.load_daily_state()
        assert loaded['trades_today'] == 3
        assert loaded['realized_pnl'] == -50.0

    def test_positions_roundtrip(self, tmp_path):
        store = IntradayStateStore(str(tmp_path / "state"))
        store.save_positions([make_position()])
        loaded = store.load_positions()
        assert len(loaded) == 1
        assert loaded[0].symbol == "RELIANCE"
        assert loaded[0].direction == SignalDirection.LONG
        assert loaded[0].status == PositionStatus.OPEN

    def test_risk_manager_restores_state(self, tmp_path):
        store = IntradayStateStore(str(tmp_path / "state"))
        rm1 = IntradayRiskManager(IntradayConfig(), state_store=store)
        rm1._daily_state.trades_today = 4
        rm1._save_daily_state()
        rm2 = IntradayRiskManager(IntradayConfig(), state_store=store)
        assert rm2.get_daily_state()['trades_today'] == 4


class TestIntradayEngineIsolation:
    """Engine degrades safely when Angel is unavailable."""

    def test_cycle_skipped_without_credentials(self, tmp_path):
        from intraday.engine import IntradayEngine
        engine = IntradayEngine(config=IntradayConfig(), state_dir=str(tmp_path))
        summary = engine.run_cycle()
        assert summary['skipped_reason'] == "Angel credentials not configured"
        assert engine.get_status()['auth_status'] == "NOT CONFIGURED"
        assert engine.get_status()['mode'] == "PAPER"

    def test_angel_auth_failure_isolated(self, tmp_path, monkeypatch):
        """Angel auth failure must not crash the engine."""
        from intraday.engine import IntradayEngine
        config = IntradayConfig(
            angel_api_key="k", angel_client_code="c",
            angel_password_or_mpin="p", angel_totp_secret="s",
        )
        engine = IntradayEngine(config=config, state_dir=str(tmp_path))
        monkeypatch.setattr(engine.auth, 'authenticate',
                            lambda: {'success': False, 'error': 'simulated failure'})
        summary = engine.run_cycle()
        assert summary['skipped_reason'] == "ANGEL AUTHENTICATION FAILED"
        assert engine.get_status()['auth_status'] == "ANGEL AUTHENTICATION FAILED"

    def test_auth_pending_when_configured(self, tmp_path):
        """Configured-but-not-yet-attempted auth must read AUTH PENDING,
        not NOT CONFIGURED (which means credentials are absent)."""
        from intraday.engine import IntradayEngine
        config = IntradayConfig(
            angel_api_key="k", angel_client_code="c",
            angel_password_or_mpin="p", angel_totp_secret="s",
        )
        engine = IntradayEngine(config=config, state_dir=str(tmp_path))
        assert engine.get_status()['auth_status'] == "AUTH PENDING"

    def test_market_closed_skips_cycle(self, tmp_path, monkeypatch):
        from intraday.engine import IntradayEngine
        config = IntradayConfig(
            angel_api_key="k", angel_client_code="c",
            angel_password_or_mpin="p", angel_totp_secret="s",
        )
        engine = IntradayEngine(config=config, state_dir=str(tmp_path))
        monkeypatch.setattr(engine.auth, 'authenticate',
                            lambda: {'success': True, 'authenticated': True})
        engine.auth.authenticated = True
        engine.auth.jwt_token = "x"
        monkeypatch.setattr(engine.validator, 'is_market_open', lambda *a, **k: False)
        summary = engine.run_cycle()
        assert summary['skipped_reason'] == "Outside market hours"

    def test_kill_switch_persists(self, tmp_path):
        from intraday.engine import IntradayEngine
        engine = IntradayEngine(config=IntradayConfig(), state_dir=str(tmp_path))
        engine.set_kill_switch(True)
        engine2 = IntradayEngine(config=IntradayConfig(), state_dir=str(tmp_path))
        assert engine2.risk_manager.is_killed() is True

    def test_status_shape_for_dashboard(self, tmp_path):
        from intraday.engine import IntradayEngine
        engine = IntradayEngine(config=IntradayConfig(), state_dir=str(tmp_path))
        status = engine.get_status()
        for key in ('auth_status', 'market_data_status', 'mode', 'market_regime',
                    'trading_blocked', 'kill_switch', 'static_ip_status',
                    'daily_state', 'opportunities', 'positions', 'history',
                    'metrics'):
            assert key in status
        assert status['mode'] == 'PAPER'
        assert status['static_ip_status'] == 'NOT REQUIRED FOR CURRENT PAPER MODE'


class TestPendingKeyRelease:
    """Fix #2: failed entry must release the dedup key; success keeps it."""

    def _engine(self, tmp_path):
        from intraday.engine import IntradayEngine
        engine = IntradayEngine(config=IntradayConfig(), state_dir=str(tmp_path))
        # Bypass market-hours/freshness gates — this test isolates dedup lifecycle
        engine.validator.validate_entry_conditions = lambda *a, **k: (True, None)
        return engine

    def test_failed_execution_releases_pending_key(self, tmp_path):
        engine = self._engine(tmp_path)
        signal = make_signal()
        key = signal_idempotency_key(signal)
        engine.paper_executor.execute_entry = MagicMock(return_value=None)
        engine._try_enter(signal)
        assert key not in engine.risk_manager._pending_keys

    def test_exception_during_execution_releases_pending_key(self, tmp_path):
        engine = self._engine(tmp_path)
        signal = make_signal()
        key = signal_idempotency_key(signal)
        engine.paper_executor.execute_entry = MagicMock(side_effect=RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            engine._try_enter(signal)
        assert key not in engine.risk_manager._pending_keys

    def test_successful_execution_remains_deduplicated(self, tmp_path):
        engine = self._engine(tmp_path)
        signal = make_signal()
        key = signal_idempotency_key(signal)
        engine._try_enter(signal)  # real paper executor → creates position
        assert engine.paper_executor.get_open_positions()
        # Successful entry must NOT release the key
        assert key in engine.risk_manager._pending_keys
        ok, reason = engine.risk_manager.can_enter_trade(signal, idempotency_key=key)
        assert ok is False

    def test_simultaneous_duplicate_rejected(self, tmp_path):
        engine = self._engine(tmp_path)
        signal = make_signal()
        key = signal_idempotency_key(signal)
        engine.risk_manager.register_pending(key)
        ok, reason = engine.risk_manager.can_enter_trade(signal, idempotency_key=key)
        assert ok is False
        assert "Duplicate" in reason


class TestQuantitySerialization:
    """Dashboard Qty column: quantity must flow verbatim from the
    position/trade records into get_status() — never recomputed."""

    def test_position_dict_includes_quantity(self):
        from intraday.engine import IntradayEngine
        pos = make_position(quantity=44)
        d = IntradayEngine._position_to_dict(pos)
        assert d['quantity'] == 44

    def test_history_includes_executed_quantity(self, tmp_path):
        from intraday.engine import IntradayEngine
        engine = IntradayEngine(config=IntradayConfig(), state_dir=str(tmp_path))
        trade = IntradayTrade(
            id="t1", symbol="ONGC", direction=SignalDirection.LONG,
            entry_price=225.49, exit_price=230.0, quantity=44,
            entry_time=datetime.now(), exit_time=datetime.now(),
            exit_reason="TARGET_HIT", gross_pnl=198.0, charges=4.0,
            net_pnl=194.0, pnl_pct=2.0, stop_loss=220.0, target=230.0,
            risk_reward=2.0, signal_score=80.0,
            signal_classification=SignalClassification.GOOD_SETUP,
            strategy="ORB", is_paper=True)
        engine.trade_manager.get_today_trades = MagicMock(return_value=[trade])
        hist = engine._history()
        assert hist[0]['quantity'] == 44

    def test_get_status_positions_carry_quantity(self, tmp_path):
        from intraday.engine import IntradayEngine
        engine = IntradayEngine(config=IntradayConfig(), state_dir=str(tmp_path))
        pos = make_position(quantity=44)
        engine.paper_executor.get_open_positions = MagicMock(return_value=[pos])
        status = engine.get_status()
        assert status['positions'][0]['quantity'] == 44


class TestDataFreshness:
    """Fix #3: last-data timestamp only advances on real fetch success."""

    def test_successful_fetch_updates_timestamp(self, tmp_path):
        from intraday.engine import IntradayEngine
        from intraday.angel.market_data import AngelMarketData
        engine = IntradayEngine(config=IntradayConfig(), state_dir=str(tmp_path))

        client = MagicMock()
        client.get_market_quotes.return_value = {
            'success': True,
            'data': {'fetched': [{'symbolToken': '1', 'ltp': 100.0,
                                  'tradingSymbol': 'RELIANCE-EQ', 'exchange': 'NSE'}]}
        }
        inst = MagicMock()
        inst.get_instrument.return_value = MagicMock(symbol_token='1')
        engine.market_data = AngelMarketData(client, inst)

        assert engine.market_data.get_quotes(['RELIANCE'])
        engine._record_data_time()
        assert engine._last_data_time is not None

    def test_empty_fetch_does_not_update_timestamp(self, tmp_path):
        from intraday.engine import IntradayEngine
        from intraday.angel.market_data import AngelMarketData
        engine = IntradayEngine(config=IntradayConfig(), state_dir=str(tmp_path))

        client = MagicMock()
        client.get_market_quotes.return_value = {
            'success': True, 'data': {'fetched': []}
        }
        inst = MagicMock()
        inst.get_instrument.return_value = MagicMock(symbol_token='1')
        engine.market_data = AngelMarketData(client, inst)

        assert engine.market_data.get_quotes(['RELIANCE']) == {}
        engine._record_data_time()
        assert engine._last_data_time is None

    def test_failed_fetch_does_not_update_timestamp(self, tmp_path):
        from intraday.engine import IntradayEngine
        from intraday.angel.market_data import AngelMarketData
        engine = IntradayEngine(config=IntradayConfig(), state_dir=str(tmp_path))

        client = MagicMock()
        client.get_market_quotes.return_value = {'success': False, 'error': 'down'}
        inst = MagicMock()
        inst.get_instrument.return_value = MagicMock(symbol_token='1')
        engine.market_data = AngelMarketData(client, inst)

        engine.market_data.get_quotes(['RELIANCE'])
        engine._record_data_time()
        assert engine._last_data_time is None

    def test_stale_timestamp_survives_zero_data_cycle(self, tmp_path):
        from intraday.engine import IntradayEngine
        engine = IntradayEngine(config=IntradayConfig(), state_dir=str(tmp_path))
        old = datetime.now() - timedelta(hours=1)
        engine._last_data_time = old
        # market_data has never fetched -> last_update_time() is None
        engine._record_data_time()
        assert engine._last_data_time == old
        assert engine.validator.is_data_fresh(engine._last_data_time) is False

    def test_validator_rejects_no_data(self, tmp_path):
        from intraday.engine import IntradayEngine
        engine = IntradayEngine(config=IntradayConfig(), state_dir=str(tmp_path))
        assert engine._last_data_time is None
        mid_session = IST.localize(datetime(2026, 10, 5, 11, 0))
        ok, reason = engine.validator.validate_entry_conditions(
            make_signal(), engine._last_data_time, now=mid_session)
        assert ok is False
        assert "stale" in reason.lower()

    def _engine_with_quote_client(self, tmp_path, quotes_result):
        from intraday.engine import IntradayEngine
        from intraday.angel.market_data import AngelMarketData
        engine = IntradayEngine(config=IntradayConfig(), state_dir=str(tmp_path))
        client = MagicMock()
        client.get_market_quotes.return_value = quotes_result
        inst = MagicMock()
        inst.get_instrument.return_value = MagicMock(symbol_token='1')
        engine.market_data = AngelMarketData(client, inst)
        return engine

    def test_status_live_on_mid_scan_fetch(self, tmp_path):
        """Dashboard status reflects a fresh quote even before cycle-end
        stamps _last_data_time (the DISCONNECTED-during-scan symptom)."""
        engine = self._engine_with_quote_client(tmp_path, {
            'success': True,
            'data': {'fetched': [{'symbolToken': '1', 'ltp': 100.0,
                                  'tradingSymbol': 'RELIANCE-EQ', 'exchange': 'NSE'}]}
        })
        assert engine._last_data_time is None
        engine.market_data.get_quotes(['RELIANCE'])
        assert engine._last_data_time is None  # entry gate untouched
        assert engine.market_data_status() == "LIVE"

    def test_status_stale_when_no_fetch_within_scan_window(self, tmp_path):
        """STALE = data genuinely stopped flowing (>2x scan interval),
        not 'no quote in 30s' — the entry gate keeps its own 30s check."""
        engine = self._engine_with_quote_client(tmp_path, {'success': True, 'data': {'fetched': []}})
        engine.market_data._last_fetch = datetime.now() - timedelta(seconds=200)
        assert engine.market_data_status() == "STALE"

    def test_status_disconnected_when_never_fetched(self, tmp_path):
        from intraday.engine import IntradayEngine
        engine = IntradayEngine(config=IntradayConfig(), state_dir=str(tmp_path))
        assert engine.market_data_status() == "DISCONNECTED"

    def test_status_ignores_failed_fetch(self, tmp_path):
        engine = self._engine_with_quote_client(tmp_path, {'success': False, 'error': 'down'})
        engine.market_data.get_quotes(['RELIANCE'])
        assert engine.market_data_status() == "DISCONNECTED"

    # ── Per-symbol freshness for entry validation ──────────────────────

    def _engine_with_symbol_tokens(self, tmp_path, fetched_tokens):
        """Engine whose AngelMarketData maps RELIANCE->1, INFY->2 and
        returns `fetched_tokens` in the batch response."""
        from intraday.engine import IntradayEngine
        from intraday.angel.market_data import AngelMarketData
        engine = IntradayEngine(config=IntradayConfig(), state_dir=str(tmp_path))
        client = MagicMock()
        client.get_market_quotes.return_value = {
            'success': True,
            'data': {'fetched': [
                {'symbolToken': t, 'ltp': 100.0, 'tradingSymbol': 'X-EQ',
                 'exchange': 'NSE'} for t in fetched_tokens]}
        }
        inst = MagicMock()
        inst.get_instrument.side_effect = lambda s: MagicMock(
            symbol_token={'RELIANCE': '1', 'INFY': '2'}.get(s))
        engine.market_data = AngelMarketData(client, inst)
        return engine

    def test_symbol_stamp_only_for_returned_symbols(self, tmp_path):
        """A batch fetch stamps only symbols actually returned."""
        engine = self._engine_with_symbol_tokens(tmp_path, ['1'])
        engine.market_data.get_quotes(['RELIANCE', 'INFY'])
        assert engine.market_data.symbol_last_update('RELIANCE') is not None
        assert engine.market_data.symbol_last_update('INFY') is None

    def test_entry_gate_accepts_fresh_symbol_stamp(self, tmp_path, monkeypatch):
        """Signal whose symbol was fetched mid-scan passes freshness even
        when the previous cycle-end stamp is stale — the reported bug."""
        engine = self._engine_with_symbol_tokens(tmp_path, ['1'])
        mid_session = IST.localize(datetime(2026, 10, 5, 11, 0))
        monkeypatch.setattr('intraday.validators.ist_now', lambda: mid_session)
        engine._last_data_time = datetime.now() - timedelta(minutes=5)
        engine.market_data.get_quotes(['RELIANCE'])  # fresh stamp for RELIANCE
        signal = make_signal()
        engine._try_enter(signal)
        assert signal.rejection_reason is None or \
            'stale' not in signal.rejection_reason.lower()

    def test_entry_gate_rejects_unfetched_symbol(self, tmp_path, monkeypatch):
        """Signal whose symbol was never fetched stays rejected."""
        engine = self._engine_with_symbol_tokens(tmp_path, ['1'])
        mid_session = IST.localize(datetime(2026, 10, 5, 11, 0))
        monkeypatch.setattr('intraday.validators.ist_now', lambda: mid_session)
        engine._last_data_time = datetime.now() - timedelta(minutes=5)
        engine.market_data.get_quotes(['RELIANCE'])  # INFY not returned
        signal = make_signal(symbol='INFY')
        engine._try_enter(signal)
        assert 'stale' in signal.rejection_reason.lower()


class TestAngelSessionExpiry:
    """Session-expiry detection from API responses + controlled re-auth."""

    def _client_with_auth(self):
        from intraday.angel.client import AngelClient
        auth = AngelAuth('k', 'c', 'p', 's')
        auth.authenticated = True
        auth.jwt_token = 'jwt'
        auth.refresh_token = 'rt'
        auth.auth_time = datetime.now()
        client = AngelClient(auth)
        api = MagicMock()
        auth._smartapi = api
        return client, auth, api

    def test_token_expired_response_invalidates_session(self):
        client, auth, api = self._client_with_auth()
        api.getProfile.return_value = {
            'status': False, 'errorcode': 'AG8002', 'message': 'Token Expired'}
        result = client.get_profile()
        assert result['success'] is False
        assert auth.is_authenticated() is False
        # A dead session must not be retried MAX_RETRIES times
        assert api.getProfile.call_count == 1

    def test_invalid_token_exception_invalidates_session(self):
        client, auth, api = self._client_with_auth()
        api.getProfile.side_effect = Exception('InvalidTokenError: token expired')
        result = client.get_profile()
        assert result['success'] is False
        assert auth.is_authenticated() is False
        assert api.getProfile.call_count == 1

    def test_generic_error_does_not_invalidate(self, monkeypatch):
        monkeypatch.setattr('intraday.angel.client.time.sleep', lambda *_: None)
        client, auth, api = self._client_with_auth()
        api.getProfile.side_effect = Exception('connection reset')
        result = client.get_profile()
        assert result['success'] is False
        assert auth.is_authenticated() is True
        assert api.getProfile.call_count == client.MAX_RETRIES

    def test_rate_limit_still_retries(self, monkeypatch):
        monkeypatch.setattr('intraday.angel.client.time.sleep', lambda *_: None)
        client, auth, api = self._client_with_auth()
        api.getProfile.side_effect = [
            {'status': False, 'errorcode': 'AB1004',
             'message': 'Access denied because of exceeding access rate'},
            {'status': True, 'data': {'name': 'U'}}]
        result = client.get_profile()
        assert result['success'] is True
        assert api.getProfile.call_count == 2
        assert auth.is_authenticated() is True

    def test_stale_day_token_not_authenticated(self):
        _, auth, _ = self._client_with_auth()
        auth.auth_time = datetime.now() - timedelta(days=1)
        assert auth.is_authenticated() is False

    def _credentialed_engine(self, tmp_path):
        from intraday.engine import IntradayEngine
        config = IntradayConfig(
            angel_api_key="k", angel_client_code="c",
            angel_password_or_mpin="p", angel_totp_secret="s")
        return IntradayEngine(config=config, state_dir=str(tmp_path))

    def test_relogin_after_session_invalidated(self, tmp_path, monkeypatch):
        """Invalidated session triggers controlled re-auth on next cycle."""
        engine = self._credentialed_engine(tmp_path)
        engine.auth.mark_session_invalid()
        calls = []
        def fake_auth():
            calls.append(1)
            return {'success': True, 'authenticated': True}
        monkeypatch.setattr(engine.auth, 'authenticate', fake_auth)
        assert engine.authenticate() is True
        assert len(calls) == 1
        assert engine.get_status()['auth_status'] == "CONNECTED"

    def test_failed_relogin_is_bounded_by_cooldown(self, tmp_path, monkeypatch):
        """Repeated auth failures must not spin into a login loop."""
        engine = self._credentialed_engine(tmp_path)
        engine.auth.mark_session_invalid()
        calls = []
        def fake_auth():
            calls.append(1)
            return {'success': False, 'error': 'bad credentials'}
        monkeypatch.setattr(engine.auth, 'authenticate', fake_auth)
        assert engine.authenticate() is False
        assert engine.get_status()['auth_status'] == "ANGEL AUTHENTICATION FAILED"
        # Subsequent attempts within the 5-minute cooldown do not re-login
        assert engine.authenticate() is False
        assert engine.authenticate() is False
        assert len(calls) == 1

    def test_stale_day_session_relogins(self, tmp_path, monkeypatch):
        """Yesterday's session triggers re-login without an API failure."""
        engine = self._credentialed_engine(tmp_path)
        engine.auth.authenticated = True
        engine.auth.jwt_token = 'jwt'
        engine.auth.auth_time = datetime.now() - timedelta(days=1)
        calls = []
        monkeypatch.setattr(
            engine.auth, 'authenticate',
            lambda: calls.append(1) or {'success': True, 'authenticated': True})
        assert engine.authenticate() is True
        assert len(calls) == 1


class TestPositionRefresh:
    """Batch-quote refresh drives current_price and exit evaluation."""

    def _engine(self, tmp_path):
        from intraday.engine import IntradayEngine
        return IntradayEngine(config=IntradayConfig(), state_dir=str(tmp_path))

    def _open(self, engine, symbol='RELIANCE'):
        sig = make_signal(symbol=symbol)
        pos = engine.paper_executor.execute_entry(sig)
        assert pos is not None
        return pos

    def _client(self):
        from intraday.angel.client import AngelClient
        auth = AngelAuth('k', 'c', 'p', 's')
        auth.authenticated = True
        auth.jwt_token = 'j'
        auth.auth_time = datetime.now()
        client = AngelClient(auth)
        api = MagicMock()
        auth._smartapi = api
        return client, api

    # ── SDK method compatibility ────────────────────────────────────

    def test_batch_quotes_use_getMarketData(self):
        """Current smartapi-python exposes getMarketData(mode, tokens)."""
        client, api = self._client()
        api.getMarketData.return_value = {
            'status': True,
            'data': {'fetched': [{'symbolToken': '2885', 'ltp': 2500.0,
                                  'tradingSymbol': 'RELIANCE-EQ', 'exchange': 'NSE'}]}}
        del api.marketData  # installed SDK has no marketData attr
        res = client.get_market_quotes({'NSE': ['2885']})
        assert res['success'] is True
        assert res['data']['fetched'][0]['ltp'] == 2500.0
        api.getMarketData.assert_called_once_with('FULL', {'NSE': ['2885']})

    def test_batch_quotes_fallback_to_marketData(self):
        """Older SDKs that only have marketData(params) still work."""
        client, api = self._client()
        api.marketData.return_value = {
            'status': True,
            'data': {'fetched': [{'symbolToken': '2885', 'ltp': 2500.0}]}}
        del api.getMarketData
        res = client.get_market_quotes({'NSE': ['2885']})
        assert res['success'] is True
        api.marketData.assert_called_once_with(
            {'mode': 'FULL', 'exchangeTokens': {'NSE': ['2885']}})

    # ── position refresh + exits ────────────────────────────────────

    def test_refresh_updates_current_price(self, tmp_path):
        engine = self._engine(tmp_path)
        self._open(engine)
        engine.market_data = MagicMock()
        engine.market_data.get_quotes.return_value = {'RELIANCE': {'ltp': 2510.0}}
        exited = engine._refresh_positions()
        pos = engine.paper_executor.get_open_positions()[0]
        assert pos.current_price == 2510.0
        assert pos.unrealized_pnl > 0
        assert exited == 0

    def test_stop_loss_exit_on_fresh_price(self, tmp_path):
        engine = self._engine(tmp_path)
        self._open(engine)
        engine.market_data = MagicMock()
        engine.market_data.get_quotes.return_value = {'RELIANCE': {'ltp': 2400.0}}
        assert engine._refresh_positions() == 1
        trade = engine.trade_manager.get_trades()[0]
        assert trade.exit_reason == 'STOP_LOSS'
        assert trade.exit_price == 2400.0
        assert engine.paper_executor.get_open_positions() == []

    def test_target_exit_on_fresh_price(self, tmp_path):
        engine = self._engine(tmp_path)
        self._open(engine)
        engine.market_data = MagicMock()
        engine.market_data.get_quotes.return_value = {'RELIANCE': {'ltp': 2560.0}}
        assert engine._refresh_positions() == 1
        trade = engine.trade_manager.get_trades()[0]
        assert trade.exit_reason == 'TARGET_HIT'
        assert trade.exit_price == 2560.0

    def test_missing_symbol_exit_deferred(self, tmp_path):
        """A symbol absent from fresh quotes keeps its position untouched."""
        engine = self._engine(tmp_path)
        self._open(engine)
        self._open(engine, symbol='INFY')
        # INFY's stored price below its SL — stale data must not exit it
        infy = [p for p in engine.paper_executor.get_open_positions()
                if p.symbol == 'INFY'][0]
        infy.current_price = 2400.0
        engine.market_data = MagicMock()
        engine.market_data.get_quotes.return_value = {'RELIANCE': {'ltp': 2510.0}}
        exited = engine._refresh_positions()
        remaining = {p.symbol for p in engine.paper_executor.get_open_positions()}
        assert exited == 0
        assert remaining == {'RELIANCE', 'INFY'}
        assert infy.current_price == 2400.0  # preserved, not fabricated

    def test_no_quotes_preserves_positions(self, tmp_path):
        engine = self._engine(tmp_path)
        pos = self._open(engine)
        pos.current_price = 2400.0  # stale, below SL — must not exit
        engine.market_data = MagicMock()
        engine.market_data.get_quotes.return_value = {}
        exited = engine._refresh_positions()
        assert exited == 0
        assert len(engine.paper_executor.get_open_positions()) == 1
        assert engine._last_data_time is None

    def test_eod_square_off_refreshes_prices(self, tmp_path, monkeypatch):
        engine = self._engine(tmp_path)
        self._open(engine)
        monkeypatch.setattr(engine.validator, 'is_square_off_time', lambda *a, **k: True)
        engine.market_data = MagicMock()
        engine.market_data.get_quotes.return_value = {'RELIANCE': {'ltp': 2530.0}}
        engine._maybe_square_off()
        assert engine.paper_executor.get_open_positions() == []
        trade = engine.trade_manager.get_trades()[0]
        assert trade.exit_reason == 'EOD_SQUARE_OFF'
        assert trade.exit_price == 2530.0

    def test_eod_square_off_without_quotes_warns_and_exits(self, tmp_path, monkeypatch):
        """EOD still closes positions but surfaces missing data clearly."""
        engine = self._engine(tmp_path)
        self._open(engine)
        monkeypatch.setattr(engine.validator, 'is_square_off_time', lambda *a, **k: True)
        engine.market_data = MagicMock()
        engine.market_data.get_quotes.return_value = {}
        engine._maybe_square_off()
        assert engine.paper_executor.get_open_positions() == []
        trade = engine.trade_manager.get_trades()[0]
        assert trade.exit_reason == 'EOD_SQUARE_OFF'
        assert trade.exit_price == 2500.0  # last known price, not fabricated


class TestQuoteFailureMonitoring:
    """Consecutive empty-quote cycles raise one deduplicated incident;
    recovery clears it. The alert hook is injected — no real
    store/email is touched under pytest (production sink is also
    gated on PYTEST_CURRENT_TEST)."""

    def _engine(self, tmp_path):
        from intraday.engine import IntradayEngine
        events = []
        engine = IntradayEngine(config=IntradayConfig(), state_dir=str(tmp_path),
                                alert_hook=lambda *a: events.append(a))
        return engine, events

    def _open(self, engine):
        sig = make_signal(symbol='RELIANCE')
        pos = engine.paper_executor.execute_entry(sig)
        assert pos is not None
        return pos

    def _fail_cycle(self, engine):
        engine.market_data = MagicMock()
        engine.market_data.get_quotes.return_value = {}
        return engine._refresh_positions()

    def test_no_notify_below_threshold(self, tmp_path):
        engine, events = self._engine(tmp_path)
        self._open(engine)
        self._fail_cycle(engine)
        self._fail_cycle(engine)
        assert [e for e in events if e[0] == 'notify'] == []

    def test_notify_after_three_failures(self, tmp_path):
        engine, events = self._engine(tmp_path)
        self._open(engine)
        for _ in range(3):
            assert self._fail_cycle(engine) == 0
        notifies = [e for e in events if e[0] == 'notify']
        assert len(notifies) == 1
        assert notifies[0][1] == 'intraday:angel:quote_refresh_failure'
        assert '3 consecutive' in notifies[0][2]

    def test_recovery_resolves_incident_and_resets_streak(self, tmp_path):
        engine, events = self._engine(tmp_path)
        self._open(engine)
        for _ in range(3):
            self._fail_cycle(engine)
        engine.market_data.get_quotes.return_value = {'RELIANCE': {'ltp': 2510.0}}
        engine._refresh_positions()
        assert ('resolve', 'intraday:angel:quote_refresh_failure', '') in events
        assert engine._quote_fail_streak == 0

    def test_unchanged_prices_count_as_success(self, tmp_path):
        """Quotes identical to current prices are still a healthy refresh —
        only a missing/empty response counts as a failure."""
        engine, events = self._engine(tmp_path)
        current = self._open(engine).current_price
        self._fail_cycle(engine)
        self._fail_cycle(engine)
        engine.market_data = MagicMock()
        engine.market_data.get_quotes.return_value = {'RELIANCE': {'ltp': current}}
        engine._refresh_positions()
        assert engine._quote_fail_streak == 0
        self._fail_cycle(engine)  # streak restarts — 1/3 again
        assert [e for e in events if e[0] == 'notify'] == []

    def test_no_incident_without_open_positions(self, tmp_path):
        """Refresh only runs with open positions — no positions, no streak."""
        engine, events = self._engine(tmp_path)
        engine.market_data = MagicMock()
        engine.market_data.get_quotes.return_value = {}
        assert engine._refresh_positions() == 0
        assert engine._quote_fail_streak == 0
        assert events == []

    def test_eod_quote_failure_notifies_immediately(self, tmp_path, monkeypatch):
        """EOD is terminal — a missing quote fires the incident at once."""
        engine, events = self._engine(tmp_path)
        self._open(engine)
        monkeypatch.setattr(engine.validator, 'is_square_off_time',
                            lambda *a, **k: True)
        engine.market_data = MagicMock()
        engine.market_data.get_quotes.return_value = {}
        engine._maybe_square_off()
        notifies = [e for e in events if e[0] == 'notify']
        assert [n[1] for n in notifies] == ['intraday:angel:eod_quote_failure']

    def test_stale_prices_never_exit_while_incident_open(self, tmp_path):
        """The stale-price exit guard stays intact during a quote incident."""
        engine, events = self._engine(tmp_path)
        pos = self._open(engine)
        pos.current_price = 100.0  # deep below SL — must not fire on stale data
        for _ in range(3):
            assert self._fail_cycle(engine) == 0
        assert engine.paper_executor.get_open_positions()  # still open
        assert engine.trade_manager.get_trades() == []     # no exits


class TestAngelHistoricalRateLimit:
    """SmartAPI's candle endpoint allows ~1 req/sec — the client must
    enforce that spacing (incl. retries/concurrent callers) and classify
    non-JSON rate-limit pages correctly."""

    def _client(self):
        from intraday.angel.client import AngelClient
        auth = AngelAuth('k', 'c', 'p', 's')
        auth.authenticated = True
        auth.jwt_token = 'j'
        auth.auth_time = datetime.now()
        client = AngelClient(auth)
        api = MagicMock()
        auth._smartapi = api
        return client, api

    def test_historical_min_interval_respects_one_per_sec(self):
        from intraday.angel.client import AngelClient
        assert AngelClient.RATE_LIMITS['historical'] >= 1.0

    def test_throttle_spaces_consecutive_calls(self, monkeypatch):
        client, api = self._client()
        api.getCandleData.return_value = {'status': True, 'data': []}
        sleeps = []
        monkeypatch.setattr('intraday.angel.client.time.sleep',
                            lambda s: sleeps.append(s))
        client.get_historical_data('1', 'ONE_MINUTE', 'a', 'b')
        client.get_historical_data('1', 'ONE_MINUTE', 'a', 'b')
        # second call must wait out the throttle interval
        assert any(s >= 1.0 for s in sleeps)
        assert api.getCandleData.call_count == 2

    def test_non_json_rate_limit_classified_and_bounded(self, monkeypatch):
        """'Couldn't parse... exceeding access rate' exceptions retry a
        bounded number of times, then fail closed — never infinite."""
        client, api = self._client()
        api.getCandleData.side_effect = Exception(
            "Couldn't parse the JSON response received from the server: "
            "b'Access denied because of exceeding access rate'")
        monkeypatch.setattr('intraday.angel.client.time.sleep', lambda s: None)
        res = client.get_historical_data('1', 'ONE_MINUTE', 'a', 'b')
        assert res['success'] is False
        assert api.getCandleData.call_count == client.MAX_RETRIES

    def test_cycle_completes_under_persistent_rate_limit(self, tmp_path, monkeypatch):
        """A scan cycle under total rate-limiting still terminates —
        per-call retry bounds mean no indefinite hang."""
        from intraday.engine import IntradayEngine
        engine = IntradayEngine(config=IntradayConfig(), state_dir=str(tmp_path))
        engine._auth_state = "CONNECTED"
        engine._auth_attempted = True
        monkeypatch.setattr(engine, 'authenticate', lambda *a, **k: True)
        monkeypatch.setattr(engine.validator, 'is_market_open', lambda *a, **k: True)
        monkeypatch.setattr(engine.validator, 'is_square_off_time', lambda *a, **k: False)
        engine._instruments_loaded = True
        monkeypatch.setattr(engine.instruments, 'get_all_symbols',
                            lambda: ['RELIANCE', 'TCS'])
        monkeypatch.setattr(engine.scanner, 'get_symbol_data',
                            lambda s: None)  # every fetch fails fast
        monkeypatch.setattr(engine.scanner, 'detect_market_regime',
                            lambda: MarketRegime.SIDEWAYS)
        summary = engine.run_cycle()
        assert summary is not None
        assert 'scanned' in summary


class TestScanStuckMonitoring:
    """Prolonged scans are surfaced — even with zero open positions."""

    def _engine(self, tmp_path):
        from intraday.engine import IntradayEngine
        events = []
        engine = IntradayEngine(config=IntradayConfig(), state_dir=str(tmp_path),
                                alert_hook=lambda *a: events.append(a))
        return engine, events

    def test_status_reports_scan_progress(self, tmp_path):
        engine, _ = self._engine(tmp_path)
        engine._cycle_running = True
        engine._cycle_started_at = datetime.now() - timedelta(seconds=30)
        s = engine.get_status()
        assert s['scan_seconds'] >= 30
        assert s['scan_started'] is not None
        assert 'last_cycle_duration_s' in s
        assert 'last_scan_time' in s  # separate from in-progress info

    def test_stuck_cycle_notifies_once(self, tmp_path):
        engine, events = self._engine(tmp_path)
        engine._cycle_running = True
        engine._cycle_started_at = datetime.now() - timedelta(seconds=900)
        engine.get_status()
        notifies = [e for e in events if e[0] == 'notify']
        assert [n[1] for n in notifies] == ['intraday:angel:scan_stuck']
        engine.get_status()  # second poll — deduplicated at engine level
        assert len([e for e in events if e[0] == 'notify']) == 1

    def test_recovery_resolves_stuck_incident(self, tmp_path):
        engine, events = self._engine(tmp_path)
        engine._cycle_running = True
        engine._cycle_started_at = datetime.now() - timedelta(seconds=900)
        engine.get_status()
        # Simulate cycle completion via the background wrapper path
        engine._last_cycle_duration_s = 700.0
        engine._cycle_started_at = None
        engine._cycle_running = False
        if engine._scan_stuck_notified:
            engine._scan_stuck_notified = False
            engine._incident('resolve', 'intraday:angel:scan_stuck', '')
        assert ('resolve', 'intraday:angel:scan_stuck', '') in events

    def test_normal_cycle_no_stuck_alert(self, tmp_path):
        engine, events = self._engine(tmp_path)
        engine._cycle_running = True
        engine._cycle_started_at = datetime.now() - timedelta(seconds=60)
        engine.get_status()
        assert [e for e in events if e[0] == 'notify'] == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestMarketDataStatusSemantics:
    """STALE must mean 'data stopped flowing', not 'no tick in 30s'.

    The engine is REST-polled: scans pull candles every ~1.1s and quotes
    per candidate, so a 30s tick window flickers STALE during healthy
    operation. Status uses last_fetch_time() (ANY fetch) within a
    scan-scaled window; the strict 30s per-symbol QUOTE gate for entries
    is untouched.
    """

    def _engine(self, tmp_path, scan_interval_seconds=60):
        from intraday.engine import IntradayEngine
        config = IntradayConfig(
            angel_api_key="k", angel_client_code="c",
            angel_password_or_mpin="p", angel_totp_secret="s")
        return IntradayEngine(config=config, state_dir=str(tmp_path),
                              scan_interval_seconds=scan_interval_seconds)

    def test_disconnected_when_nothing_fetched(self, tmp_path):
        engine = self._engine(tmp_path)
        assert engine.market_data_status() == "DISCONNECTED"

    def test_candle_fetch_counts_as_live_data_flow(self, tmp_path):
        """A scan running candles without quote fetches must still read
        LIVE — data IS flowing."""
        from datetime import datetime
        engine = self._engine(tmp_path)
        engine.market_data._last_fetch = datetime.now()  # candle stamp, no quote stamp
        assert engine.market_data.last_update_time() is None
        assert engine.market_data_status() == "LIVE"

    def test_stale_only_after_scan_scaled_window(self, tmp_path):
        from datetime import datetime, timedelta
        engine = self._engine(tmp_path, scan_interval_seconds=60)
        engine.market_data._last_fetch = datetime.now() - timedelta(seconds=45)
        assert engine.market_data_status() == "LIVE"   # within 120s window
        engine.market_data._last_fetch = datetime.now() - timedelta(seconds=200)
        assert engine.market_data_status() == "STALE"  # genuine outage

    def test_candle_fetch_does_not_weaken_entry_gate(self, tmp_path):
        """_last_update (quote stamp) must stay untouched by candles so
        validate_entry_conditions keeps its 30s strictness."""
        from unittest.mock import MagicMock
        import pandas as pd
        client = MagicMock()
        client.get_historical_data.return_value = {
            'success': True,
            'data': [['2026-10-22T10:00:00', 1, 2, 0.5, 1.5, 100]]}
        from intraday.angel.market_data import AngelMarketData
        md = AngelMarketData(client, instruments=None)
        df = md.get_historical_candles('TOKEN', 'ONE_MINUTE', 1)
        assert df is not None
        assert md.last_fetch_time() is not None      # coarse flow stamp
        assert md.last_update_time() is None          # quote stamp untouched
        engine = self._engine(tmp_path)
        engine.market_data = md
        ok, reason = engine.validator.validate_entry_conditions(
            signal=None, last_data_time=None)
        assert ok is False  # still rejected — no fresh quote


class TestSelfScheduler:
    """Oct-23 audit: engine was poll-driven — scans/exits only ran while
    /api/intraday-data was being requested. The self-scheduler keeps
    cycles running during market hours without a viewer."""

    @pytest.fixture(autouse=True)
    def _stop_schedulers(self):
        self._engines = []
        yield
        for e in self._engines:
            e.stop_scheduler()

    def _engine(self, tmp_path):
        from intraday.engine import IntradayEngine
        engine = IntradayEngine(config=IntradayConfig(), state_dir=str(tmp_path))
        self._engines.append(engine)
        return engine

    def test_tick_runs_cycle_when_market_open(self, tmp_path):
        import time
        engine = self._engine(tmp_path)
        engine.validator.is_market_open = Mock(return_value=True)
        engine.validator.is_square_off_time = Mock(return_value=False)
        engine.maybe_run_cycle = Mock()
        engine.start_scheduler(tick_seconds=0.05)
        time.sleep(0.2)
        assert engine.maybe_run_cycle.called

    def test_tick_skips_when_market_closed(self, tmp_path):
        import time
        engine = self._engine(tmp_path)
        engine.validator.is_market_open = Mock(return_value=False)
        engine.validator.is_square_off_time = Mock(return_value=False)
        engine.maybe_run_cycle = Mock()
        engine.start_scheduler(tick_seconds=0.05)
        time.sleep(0.2)
        engine.maybe_run_cycle.assert_not_called()

    def test_scheduler_starts_only_once(self, tmp_path):
        engine = self._engine(tmp_path)
        engine.validator.is_market_open = Mock(return_value=False)
        engine.start_scheduler(tick_seconds=10)
        assert engine._scheduler_started is True
        thread_count_before = sum(1 for t in threading.enumerate()
                                  if t.name == 'intraday-scheduler')
        engine.start_scheduler(tick_seconds=10)
        thread_count_after = sum(1 for t in threading.enumerate()
                                 if t.name == 'intraday-scheduler')
        assert thread_count_after == thread_count_before

    def test_tick_recovers_from_cycle_exceptions(self, tmp_path):
        """A failing cycle must not kill the scheduler — next tick retries."""
        import time
        engine = self._engine(tmp_path)
        engine.validator.is_market_open = Mock(return_value=True)
        engine.validator.is_square_off_time = Mock(return_value=False)
        engine.maybe_run_cycle = Mock(side_effect=RuntimeError("boom"))
        engine.start_scheduler(tick_seconds=0.05)
        time.sleep(0.25)
        assert engine.maybe_run_cycle.call_count >= 2

    def test_poll_during_scan_does_not_block(self, tmp_path):
        """/api/intraday-data polls must return immediately while a scan
        holds the engine lock — otherwise the UI freezes on stale data."""
        import time
        engine = self._engine(tmp_path)
        engine._last_scan_time = None  # make a cycle due
        engine.validator.is_market_open = Mock(return_value=True)
        engine.validator.is_square_off_time = Mock(return_value=False)
        with engine._lock:  # simulate in-flight scan holding the lock
            t0 = time.monotonic()
            engine.maybe_run_cycle()
            elapsed = time.monotonic() - t0
        assert elapsed < 0.5, f"poll blocked {elapsed:.2f}s behind engine lock"
        assert engine._cycle_running is False  # must not have spawned

    def test_no_overlapping_scans(self, tmp_path):
        """Two rapid maybe_run_cycle calls spawn exactly one cycle."""
        import time
        engine = self._engine(tmp_path)
        calls = []
        def slow_cycle():
            calls.append(1)
            time.sleep(0.3)
        engine.run_cycle = slow_cycle
        engine.validator.is_square_off_time = Mock(return_value=False)
        engine.maybe_run_cycle()
        engine.maybe_run_cycle()  # _cycle_running already set → no-op
        engine.maybe_run_cycle()
        time.sleep(0.5)
        assert len(calls) == 1
        assert engine._cycle_running is False

    def test_unattended_eod_square_off(self, tmp_path):
        """Square-off fires on open positions with no UI involved."""
        engine = self._engine(tmp_path)
        pos = make_position(symbol="DRREDDY")
        engine.validator.is_market_open = Mock(return_value=True)
        engine.validator.is_square_off_time = Mock(return_value=True)
        engine.paper_executor.get_open_positions = Mock(return_value=[pos])
        engine.paper_executor.eod_square_off = Mock(return_value=[])
        engine.market_data.get_quotes = Mock(return_value={
            'DRREDDY': {'ltp': 1237.8}})
        with engine._lock:
            engine._maybe_square_off()
        engine.paper_executor.eod_square_off.assert_called_once()

    def test_positions_survive_engine_restart(self, tmp_path):
        """Open paper positions rehydrate into a new engine on same state_dir."""
        engine1 = self._engine(tmp_path)
        engine1.state_store.save_positions([make_position(symbol="DRREDDY")])
        engine2 = self._engine(tmp_path)
        open_syms = [p.symbol for p in engine2.paper_executor.get_open_positions()]
        assert "DRREDDY" in open_syms

    def test_engine_factory_starts_scheduler(self, tmp_path, monkeypatch):
        """get_intraday_engine() — the boot-time path — activates the scheduler."""
        import intraday.engine as eng_mod
        monkeypatch.setattr(eng_mod, '_ENGINE', None)
        engine = eng_mod.get_intraday_engine(
            config=IntradayConfig(), state_dir=str(tmp_path))
        assert engine._scheduler_started is True
        monkeypatch.setattr(eng_mod, '_ENGINE', None)
