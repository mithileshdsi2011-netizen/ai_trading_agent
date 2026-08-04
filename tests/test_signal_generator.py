"""
Test cases for Signal Generator
"""
import pytest
from unittest.mock import Mock, patch
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.signal_generator import SignalGenerator


class TestSignalGenerator:
    """Test cases for SignalGenerator"""
    
    def _research(self, recommendation='BUY'):
        return {
            'recommendation': recommendation,
            'confidence': 0.8,
            'overall_score': 0.6,
            'technical_analysis': {
                'support': 2400.0,
                'resistance': 2600.0,
                'technical_score': 0.5
            },
            'reasoning': 'Test indicators'
        }

    def _ai_scores(self, action='BUY'):
        return {
            'recommendation': action,
            'action': action,
            'final_score': 80.0,
            'final_confidence': 85.0,
            'threshold': 65.0,
            'explain_text': 'test',
            'explain': {},
            'sub_scores': {},
            'score_components': {},
            'regime_threshold': 'BULL'
        }

    @patch('src.signal_generator.AIResearchAgent')
    @patch('src.signal_generator.EnterpriseAIDecisionEngine')
    def test_generate_signal_buy(self, mock_engine, mock_research):
        """Test generating a buy signal"""
        mock_research_instance = Mock()
        mock_research.return_value = mock_research_instance
        mock_research_instance.research_stock.return_value = self._research('BUY')
        mock_research_instance.market_data = Mock()
        mock_research_instance.market_data.get_realtime_price.return_value = 2500.0

        mock_engine_instance = Mock()
        mock_engine.return_value = mock_engine_instance
        mock_engine_instance.compute_scores.return_value = self._ai_scores('BUY')
        signal_generator = SignalGenerator()

        result = signal_generator.generate_signal('RELIANCE')

        assert result['symbol'] == 'RELIANCE'
        assert result['action'] == 'BUY'
        assert result['current_price'] == 2500.0
        assert result['position_size'] > 0
        assert result['stop_loss'] > 0
        assert result['target'] > 0
        assert result['confidence'] > 0

    @patch('src.signal_generator.AIResearchAgent')
    @patch('src.signal_generator.EnterpriseAIDecisionEngine')
    def test_generate_signal_sell(self, mock_engine, mock_research):
        """Test generating a sell signal"""
        mock_research_instance = Mock()
        mock_research.return_value = mock_research_instance
        mock_research_instance.research_stock.return_value = self._research('SELL')
        mock_research_instance.market_data = Mock()
        mock_research_instance.market_data.get_realtime_price.return_value = 2500.0

        mock_engine_instance = Mock()
        mock_engine.return_value = mock_engine_instance
        mock_engine_instance.compute_scores.return_value = self._ai_scores('SELL')
        signal_generator = SignalGenerator()

        result = signal_generator.generate_signal('RELIANCE')

        assert result['action'] == 'SELL'

    @patch('src.signal_generator.AIResearchAgent')
    @patch('src.signal_generator.EnterpriseAIDecisionEngine')
    def test_generate_signal_hold(self, mock_engine, mock_research):
        """Test generating a hold signal"""
        mock_research_instance = Mock()
        mock_research.return_value = mock_research_instance
        mock_research_instance.research_stock.return_value = self._research('HOLD')
        mock_research_instance.market_data = Mock()
        mock_research_instance.market_data.get_realtime_price.return_value = 2500.0

        mock_engine_instance = Mock()
        mock_engine.return_value = mock_engine_instance
        mock_engine_instance.compute_scores.return_value = self._ai_scores('HOLD')
        signal_generator = SignalGenerator()

        result = signal_generator.generate_signal('RELIANCE')

        assert result['action'] == 'HOLD'

    @patch('src.signal_generator.AIResearchAgent')
    def test_generate_signal_no_data(self, mock_research):
        """Test generating signal when no data available"""
        mock_research_instance = Mock()
        mock_research.return_value = mock_research_instance
        mock_research_instance.research_stock.return_value = {
            'recommendation': 'NO_DATA',
            'confidence': 0.0,
            'overall_score': 0.0,
            'technical_analysis': {},
            'reasoning': 'Insufficient data'
        }
        signal_generator = SignalGenerator()

        result = signal_generator.generate_signal('RELIANCE')

        assert result['action'] == 'SKIP'

    @patch('src.signal_generator.AIResearchAgent')
    def test_generate_signal_no_price(self, mock_research):
        """Test generating signal when price not available"""
        mock_research_instance = Mock()
        mock_research.return_value = mock_research_instance
        mock_research_instance.research_stock.return_value = self._research('BUY')
        mock_research_instance.market_data = Mock()
        mock_research_instance.market_data.get_realtime_price.return_value = None
        signal_generator = SignalGenerator()

        result = signal_generator.generate_signal('RELIANCE')

        assert result['action'] == 'SKIP'
    
    def test_calculate_position_size(self):
        """Test position size calculation"""
        signal_generator = SignalGenerator()
        # Test with different prices
        size_100 = signal_generator._calculate_position_size(100.0)
        size_1000 = signal_generator._calculate_position_size(1000.0)
        size_5000 = signal_generator._calculate_position_size(5000.0)
        
        assert size_100 > size_1000
        assert size_1000 > size_5000
        assert size_5000 >= 1
    
    def test_calculate_risk_parameters_buy(self):
        """Test risk parameter calculation for buy"""
        signal_generator = SignalGenerator()
        current_price = 2500.0
        support = 2400.0
        resistance = 2600.0
        recommendation = 'BUY'
        
        stop_loss, target = signal_generator._calculate_risk_parameters(
            current_price, support, resistance, recommendation
        )
        
        assert stop_loss < current_price
        assert target > current_price
        assert stop_loss >= support
    
    def test_calculate_risk_reward(self):
        """Test risk-reward ratio calculation"""
        signal_generator = SignalGenerator()
        entry = 2500.0
        stop_loss = 2450.0
        target = 2600.0
        
        ratio = signal_generator._calculate_risk_reward(entry, stop_loss, target)
        
        assert ratio > 0
        assert ratio == 2.0  # (2600-2500) / (2500-2450) = 100/50 = 2
    
    def test_determine_action(self):
        """Test action determination"""
        signal_generator = SignalGenerator()
        assert signal_generator._determine_action('STRONG_BUY') == 'BUY'
        assert signal_generator._determine_action('BUY') == 'BUY'
        assert signal_generator._determine_action('STRONG_SELL') == 'SELL'
        assert signal_generator._determine_action('SELL') == 'SELL'
        assert signal_generator._determine_action('HOLD') == 'HOLD'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
