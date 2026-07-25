"""
Decision Logger Module
Tracks detailed decision-making process for all evaluated stocks
Provides transparency for why stocks were bought, held, or skipped
"""
import json
import os
from datetime import datetime, date
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class DecisionRecord:
    """Complete decision record for a stock evaluation"""
    symbol: str
    timestamp: str
    overall_score: float
    confidence: float
    technical_score: float
    news_sentiment_score: float
    sector_strength: float
    market_regime: str
    risk_reward_ratio: float
    position_size_calculated: float
    available_cash: float
    current_open_positions: List[str]
    existing_holdings: List[str]
    cooldown_status: bool
    portfolio_exposure: float
    max_position_size: float
    final_decision: str  # BUY / HOLD / SKIP
    rejection_reason: str
    detailed_factors: Dict[str, float]
    entry_price: float
    stop_loss: float
    target: float
    sector: str


class DecisionLogger:
    """Logs and manages detailed decision records"""
    
    def __init__(self):
        self.decisions_today: List[DecisionRecord] = []
        self.log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_file = os.path.join(self.log_dir, f'decisions_{date.today().strftime("%Y%m%d")}.json')
        self._load_today_decisions()
    
    def _load_today_decisions(self):
        """Load existing decisions for today"""
        try:
            if os.path.exists(self.log_file):
                with open(self.log_file, 'r') as f:
                    data = json.load(f)
                    self.decisions_today = [DecisionRecord(**record) for record in data]
        except Exception as e:
            logger.warning(f"Could not load today's decisions: {e}")
            self.decisions_today = []
    
    def log_decision(self, decision: DecisionRecord):
        """Log a new decision record"""
        self.decisions_today.append(decision)
        self._save_decisions()
    
    def _save_decisions(self):
        """Save decisions to file"""
        try:
            data = [asdict(decision) for decision in self.decisions_today]
            with open(self.log_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Could not save decisions: {e}")
    
    def get_skipped_opportunities(self) -> List[DecisionRecord]:
        """Get all skipped opportunities with reasons"""
        return [d for d in self.decisions_today if d.final_decision == 'SKIP']
    
    def get_executed_trades(self) -> List[DecisionRecord]:
        """Get all executed trades"""
        return [d for d in self.decisions_today if d.final_decision == 'BUY']
    
    def get_decision_summary(self) -> Dict:
        """Get summary of today's decisions"""
        total = len(self.decisions_today)
        skipped = len(self.get_skipped_opportunities())
        executed = len(self.get_executed_trades())
        
        # Group by rejection reasons
        rejection_reasons = {}
        for decision in self.get_skipped_opportunities():
            reason = decision.rejection_reason
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
        
        return {
            'total_evaluated': total,
            'skipped': skipped,
            'executed': executed,
            'skip_rate': skipped / total if total > 0 else 0,
            'rejection_reasons': rejection_reasons,
            'timestamp': datetime.now().isoformat()
        }
    
    def clear_old_logs(self, days_to_keep: int = 30):
        """Clear old decision logs"""
        try:
            current_date = date.today()
            for filename in os.listdir(self.log_dir):
                if filename.startswith('decisions_') and filename.endswith('.json'):
                    file_date_str = filename.replace('decisions_', '').replace('.json', '')
                    try:
                        file_date = datetime.strptime(file_date_str, '%Y%m%d').date()
                        if (current_date - file_date).days > days_to_keep:
                            os.remove(os.path.join(self.log_dir, filename))
                    except:
                        continue
        except Exception as e:
            logger.warning(f"Could not clear old logs: {e}")


def create_decision_record(
    symbol: str,
    research_data: Dict,
    signal_data: Dict,
    risk_data: Dict,
    final_decision: str,
    rejection_reason: str = ""
) -> DecisionRecord:
    """Create a decision record from various data sources"""
    
    return DecisionRecord(
        symbol=symbol,
        timestamp=datetime.now().isoformat(),
        overall_score=research_data.get('overall_score', 0),
        confidence=research_data.get('confidence', 0),
        technical_score=research_data.get('technical_score', 0),
        news_sentiment_score=research_data.get('news_sentiment_score', 0),
        sector_strength=research_data.get('sector_momentum', 0),
        market_regime=research_data.get('market_regime', 'UNKNOWN'),
        risk_reward_ratio=signal_data.get('risk_reward_ratio', 0),
        position_size_calculated=signal_data.get('position_size', 0),
        available_cash=risk_data.get('available_cash', 0),
        current_open_positions=risk_data.get('open_positions', []),
        existing_holdings=risk_data.get('holdings', []),
        cooldown_status=risk_data.get('cooldown_status', False),
        portfolio_exposure=risk_data.get('portfolio_exposure', 0),
        max_position_size=risk_data.get('max_position_size', 0),
        final_decision=final_decision,
        rejection_reason=rejection_reason,
        detailed_factors=research_data.get('detailed_factors', {}),
        entry_price=signal_data.get('entry_price', 0),
        stop_loss=signal_data.get('stop_loss', 0),
        target=signal_data.get('target', 0),
        sector=research_data.get('sector', 'Unknown')
    )
