"""
Intraday Trading Module Tests

Tests for the Intraday Intelligence & Trading Engine.
Completely separate from Swing Trading tests.
"""
import pytest
import sys
import os
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
