#!/usr/bin/env python3
"""
Complete Dashboard Audit Script
Validates all dashboard tabs against backend data sources
"""
import sys
import json
import logging
from datetime import datetime, date
from typing import Dict, List, Any
import pandas as pd

sys.path.insert(0, 'src')
logging.basicConfig(level=logging.WARNING)

from market_data import MarketDataFetcher
from order_executor import OrderExecutor
from risk_manager import RiskManager
from trade_journal import TradeJournal
from ai_research_agent import AIResearchAgent
from trading_orchestrator import TradingOrchestrator
from config import config

class DashboardAuditor:
    def __init__(self):
        self.md = MarketDataFetcher()
        self.oe = OrderExecutor()
        self.rj = TradeJournal()
        self.rm = RiskManager()
        self.ai = AIResearchAgent()
        self.to = TradingOrchestrator()
        self.audit_results = {}
        
    def audit_dashboard_tab(self):
        """Audit main Dashboard tab metrics"""
        print("\n🏠 AUDITING DASHBOARD TAB")
        print("=" * 60)
        
        audit = {
            'portfolio_value': {},
            'cash': {},
            'invested': {},
            'pnl': {},
            'positions': {},
            'market_status': {},
            'bot_status': {}
        }
        
        # Get broker data
        try:
            if self.md.kite:
                margins = self.md.kite.margins()
                holdings = self.md.kite.holdings()
                positions = self.md.kite.positions()
                
                # Portfolio metrics
                total_value = 0
                for h in holdings:
                    total_value += h.get('quantity', 0) * h.get('last_price', 0)
                
                for p in positions.get('net', []):
                    if p.get('quantity', 0) != 0:
                        total_value += p.get('quantity', 0) * p.get('last_price', 0)
                
                available_cash = margins.get('equity', {}).get('available', {}).get('live_balance', 0)
                invested = total_value - available_cash
                
                audit['portfolio_value'] = {
                    'calculated': total_value,
                    'source': 'broker holdings + positions'
                }
                
                audit['cash'] = {
                    'calculated': available_cash,
                    'source': 'broker margins'
                }
                
                audit['invested'] = {
                    'calculated': invested,
                    'source': 'calculated from portfolio - cash'
                }
                
                # Position count
                open_positions = len([p for p in positions.get('net', []) if p.get('quantity', 0) != 0])
                open_holdings = len([h for h in holdings if h.get('quantity', 0) > 0])
                
                audit['positions'] = {
                    'open_positions': open_positions,
                    'holdings': open_holdings,
                    'total': open_positions + open_holdings,
                    'source': 'broker data'
                }
                
        except Exception as e:
            audit['error'] = str(e)
        
        # Market status
        try:
            market_open = self.md.is_market_open()
            audit['market_status'] = {
                'is_open': market_open,
                'source': 'market_data.is_market_open()'
            }
        except Exception as e:
            audit['market_status']['error'] = str(e)
        
        # Bot status
        audit['bot_status'] = {
            'mode': 'SWING LIVE',
            'paper_trading': config.PAPER_TRADING,
            'source': 'config.PAPER_TRADING'
        }
        
        self.audit_results['dashboard'] = audit
        return audit
    
    def audit_morning_intel(self):
        """Audit Morning Intel tab"""
        print("\n🌅 AUDITING MORNING INTEL TAB")
        print("=" * 60)
        
        audit = {
            'top_gainers': {},
            'ai_picks': {},
            'gap_ups': {},
            'high_volume': {}
        }
        
        try:
            # Load morning report cache
            with open('data/morning_report_cache.json', 'r') as f:
                morning_data = json.load(f)
            
            # Validate top gainers
            gainers = morning_data.get('top_gainers', [])
            audit['top_gainers'] = {
                'count': len(gainers),
                'sample': gainers[:3] if gainers else [],
                'source': 'morning_report_cache.json'
            }
            
            # Validate AI top picks
            ai_picks = morning_data.get('ai_top_picks', [])
            audit['ai_picks'] = {
                'count': len(ai_picks),
                'sample': ai_picks[:3] if ai_picks else [],
                'source': 'morning_report_cache.json'
            }
            
            # Check for truncation in reasons
            truncated = []
            for pick in ai_picks:
                reason = pick.get('reason', '')
                if len(reason) > 100 and '...' in reason:
                    truncated.append(pick.get('symbol', 'Unknown'))
            
            audit['truncated_reasons'] = {
                'count': len(truncated),
                'symbols': truncated
            }
            
        except Exception as e:
            audit['error'] = str(e)
        
        self.audit_results['morning_intel'] = audit
        return audit
    
    def audit_portfolio(self):
        """Audit Portfolio tab"""
        print("\n📈 AUDITING PORTFOLIO TAB")
        print("=" * 60)
        
        audit = {
            'holdings': {},
            'calculations': {},
            'allocations': {}
        }
        
        try:
            if self.md.kite:
                holdings = self.md.kite.holdings()
                positions = self.md.kite.positions().get('net', [])
                
                # Combine holdings and positions
                all_positions = []
                
                # Add holdings
                for h in holdings:
                    if h.get('quantity', 0) > 0:
                        all_positions.append({
                            'symbol': h.get('tradingsymbol'),
                            'quantity': h.get('quantity'),
                            'avg_price': h.get('average_price'),
                            'ltp': h.get('last_price'),
                            'value': h.get('quantity', 0) * h.get('last_price', 0),
                            'pnl': (h.get('last_price', 0) - h.get('average_price', 0)) * h.get('quantity', 0),
                            'pnl_pct': ((h.get('last_price', 0) - h.get('average_price', 0)) / h.get('average_price', 0) * 100) if h.get('average_price', 0) > 0 else 0
                        })
                
                # Add positions
                for p in positions:
                    if p.get('quantity', 0) != 0:
                        all_positions.append({
                            'symbol': p.get('tradingsymbol'),
                            'quantity': abs(p.get('quantity', 0)),
                            'avg_price': p.get('average_price'),
                            'ltp': p.get('last_price'),
                            'value': abs(p.get('quantity', 0)) * p.get('last_price', 0),
                            'pnl': p.get('pnl', 0),
                            'pnl_pct': p.get('pnl_percentage', 0)
                        })
                
                # Calculate totals
                total_value = sum(p['value'] for p in all_positions)
                total_pnl = sum(p['pnl'] for p in all_positions)
                
                audit['holdings'] = {
                    'count': len(all_positions),
                    'total_value': total_value,
                    'total_pnl': total_pnl,
                    'positions': all_positions,
                    'source': 'broker holdings + positions'
                }
                
                # Calculate allocations
                allocations = {}
                for p in all_positions:
                    allocations[p['symbol']] = (p['value'] / total_value * 100) if total_value > 0 else 0
                
                audit['allocations'] = {
                    'percentages': allocations,
                    'total_percentage': sum(allocations.values()),
                    'source': 'calculated from holdings'
                }
                
        except Exception as e:
            audit['error'] = str(e)
        
        self.audit_results['portfolio'] = audit
        return audit
    
    def audit_positions(self):
        """Audit Positions tab"""
        print("\n📋 AUDITING POSITIONS TAB")
        print("=" * 60)
        
        audit = {
            'risk_manager_positions': {},
            'broker_positions': {},
            'sync_status': {}
        }
        
        try:
            # Get risk manager positions
            rm_positions = self.rm.positions
            audit['risk_manager_positions'] = {
                'count': len(rm_positions),
                'positions': [
                    {
                        'symbol': p.symbol,
                        'quantity': p.quantity,
                        'entry_price': p.entry_price,
                        'stop_loss': p.stop_loss,
                        'target': p.target,
                        'status': p.status.value,
                        'product_type': p.product_type
                    } for p in rm_positions
                ],
                'source': 'risk_manager.positions'
            }
            
            # Get broker positions
            if self.md.kite:
                broker_positions = self.md.kite.positions().get('net', [])
                broker_holdings = self.md.kite.holdings()
                
                audit['broker_positions'] = {
                    'positions_count': len([p for p in broker_positions if p.get('quantity', 0) != 0]),
                    'holdings_count': len([h for h in broker_holdings if h.get('quantity', 0) > 0]),
                    'source': 'broker API'
                }
                
                # Check sync status
                rm_symbols = {p.symbol for p in rm_positions if p.status.value in ['OPEN', 'PARTIAL']}
                broker_symbols = set()
                
                for p in broker_positions:
                    if p.get('quantity', 0) != 0:
                        broker_symbols.add(p.get('tradingsymbol'))
                
                for h in broker_holdings:
                    if h.get('quantity', 0) > 0:
                        broker_symbols.add(h.get('tradingsymbol'))
                
                audit['sync_status'] = {
                    'risk_manager_symbols': list(rm_symbols),
                    'broker_symbols': list(broker_symbols),
                    'missing_in_rm': list(broker_symbols - rm_symbols),
                    'extra_in_rm': list(rm_symbols - broker_symbols),
                    'synced': rm_symbols == broker_symbols
                }
                
        except Exception as e:
            audit['error'] = str(e)
        
        self.audit_results['positions'] = audit
        return audit
    
    def audit_trade_journal(self):
        """Audit Trade Journal tab - recalculate all metrics"""
        print("\n📓 AUDITING TRADE JOURNAL TAB")
        print("=" * 60)
        
        audit = {
            'raw_data': {},
            'calculated_metrics': {},
            'displayed_metrics': {},
            'verification': {}
        }
        
        try:
            # Get raw trade data
            trades = self.rj.all_entries()
            
            audit['raw_data'] = {
                'total_trades': len(trades),
                'trades': trades
            }
            
            if trades:
                # Calculate metrics from raw data - only count BUY entries like analytics()
                closed_trades = [t for t in trades if t.get('status') == 'CLOSED' and t.get('action') == 'BUY']
                open_trades = [t for t in trades if t.get('status') == 'OPEN' and t.get('action') == 'BUY']
                
                # P&L calculations
                pnls = [float(t.get('net_pnl') or 0) for t in closed_trades]
                total_pnl = sum(pnls)
                winning_trades = [p for p in pnls if p > 0]
                losing_trades = [p for p in pnls if p < 0]
                
                win_rate = (len(winning_trades) / len(closed_trades)) if closed_trades else 0
                
                avg_win = sum(winning_trades) / len(winning_trades) if winning_trades else 0
                avg_loss = abs(sum(losing_trades) / len(losing_trades)) if losing_trades else 0
                
                # Profit factor - match analytics() logic
                gross_profit = sum(winning_trades)
                gross_loss = abs(sum(losing_trades))
                profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
                
                # Holding period
                holding_periods = []
                for t in closed_trades:
                    if 'entry_time' in t and 'exit_time' in t:
                        entry = datetime.fromisoformat(t['entry_time'])
                        exit = datetime.fromisoformat(t['exit_time'])
                        holding_periods.append((exit - entry).days)
                
                avg_holding = sum(holding_periods) / len(holding_periods) if holding_periods else 0
                
                audit['calculated_metrics'] = {
                    'total_trades': len(trades),
                    'closed_trades': len(closed_trades),
                    'open_trades': len(open_trades),
                    'win_rate': win_rate,
                    'net_pnl': total_pnl,
                    'gross_profit': gross_profit,
                    'gross_loss': gross_loss,
                    'profit_factor': profit_factor,
                    'avg_win': avg_win,
                    'avg_loss': avg_loss,
                    'avg_holding_days': avg_holding,
                    'max_profit': max([t.get('pnl', 0) for t in closed_trades]) if closed_trades else 0,
                    'max_loss': min([t.get('pnl', 0) for t in closed_trades]) if closed_trades else 0
                }
                
                # Get analytics from trade journal
                analytics = self.rj.analytics()
                audit['displayed_metrics'] = analytics
                
                # Verification
                audit['verification'] = {
                    'win_rate_match': abs(win_rate - analytics.get('win_rate', 0)) < 0.01,
                    'pnl_match': abs(total_pnl - analytics.get('net_pnl', 0)) < 0.01,
                    'profit_factor_match': abs(profit_factor - analytics.get('profit_factor', 0)) < 0.01,
                    'avg_win_match': abs(avg_win - analytics.get('avg_win', 0)) < 0.01,
                    'avg_loss_match': abs(avg_loss - analytics.get('avg_loss', 0)) < 0.01
                }
                
        except Exception as e:
            audit['error'] = str(e)
        
        self.audit_results['trade_journal'] = audit
        return audit
    
    def audit_analytics(self):
        """Audit Analytics tab"""
        print("\n📊 AUDITING ANALYTICS TAB")
        print("=" * 60)
        
        audit = {
            'daily_pnl': {},
            'monthly_pnl': {},
            'performance_metrics': {},
            'calculations': {}
        }
        
        try:
            # Get trade journal analytics
            analytics = self.rj.analytics()
            
            audit['performance_metrics'] = analytics
            
            # Verify calculations
            trades = self.rj.all_entries()
            if trades:
                # Daily P&L
                daily_pnl = {}
                for trade in trades:
                    if trade.get('status') == 'CLOSED' and 'exit_time' in trade:
                        date_key = trade['exit_time'][:10]
                        pnl = trade.get('pnl', 0)
                        daily_pnl[date_key] = daily_pnl.get(date_key, 0) + pnl
                
                audit['daily_pnl'] = {
                    'calculated': daily_pnl,
                    'total_days': len(daily_pnl)
                }
                
                # Monthly P&L
                monthly_pnl = {}
                for trade in trades:
                    if trade.get('status') == 'CLOSED' and 'exit_time' in trade:
                        month_key = trade['exit_time'][:7]  # YYYY-MM
                        pnl = trade.get('pnl', 0)
                        monthly_pnl[month_key] = monthly_pnl.get(month_key, 0) + pnl
                
                audit['monthly_pnl'] = {
                    'calculated': monthly_pnl,
                    'total_months': len(monthly_pnl)
                }
                
        except Exception as e:
            audit['error'] = str(e)
        
        self.audit_results['analytics'] = audit
        return audit
    
    def audit_bot_status(self):
        """Audit Bot Status tab"""
        print("\n⚙️ AUDITING BOT STATUS TAB")
        print("=" * 60)
        
        audit = {
            'scheduler': {},
            'api_health': {},
            'memory': {},
            'processes': {}
        }
        
        try:
            # Check API health
            if self.md.kite:
                try:
                    profile = self.md.kite.profile()
                    audit['api_health'] = {
                        'status': 'connected',
                        'user': profile.get('user_name'),
                        'last_check': datetime.now().isoformat()
                    }
                except Exception as e:
                    audit['api_health'] = {
                        'status': 'error',
                        'error': str(e)
                    }
            
            # Check trading orchestrator status
            audit['scheduler'] = {
                'is_running': self.to.is_running,
                'mode': 'SWING',
                'paper_trading': config.PAPER_TRADING
            }
            
        except Exception as e:
            audit['error'] = str(e)
        
        self.audit_results['bot_status'] = audit
        return audit
    
    def run_full_audit(self):
        """Run complete audit of all tabs"""
        print("🔍 DASHBOARD AUDIT STARTING")
        print("=" * 80)
        print(f"Timestamp: {datetime.now().isoformat()}")
        print()
        
        # Run all audits
        self.audit_dashboard_tab()
        self.audit_morning_intel()
        self.audit_portfolio()
        self.audit_positions()
        self.audit_trade_journal()
        self.audit_analytics()
        self.audit_bot_status()
        
        # Save results
        with open('/tmp/dashboard_audit_results.json', 'w') as f:
            json.dump({
                'timestamp': datetime.now().isoformat(),
                'audit_results': self.audit_results
            }, f, indent=2, default=str)
        
        print("\n✅ AUDIT COMPLETE")
        print(f"Results saved to: /tmp/dashboard_audit_results.json")
        
        return self.audit_results
    
    def generate_report(self):
        """Generate human-readable audit report"""
        report = []
        report.append("# DASHBOARD AUDIT REPORT")
        report.append(f"Generated: {datetime.now().isoformat()}")
        report.append("")
        
        for tab, data in self.audit_results.items():
            report.append(f"## {tab.upper()} TAB")
            report.append("")
            
            if 'error' in data:
                report.append(f"❌ ERROR: {data['error']}")
            else:
                # Check for issues
                issues = []
                
                if tab == 'trade_journal' and 'verification' in data:
                    verif = data['verification']
                    for key, passed in verif.items():
                        if not passed:
                            issues.append(f"❌ {key}: Mismatch")
                
                if tab == 'morning_intel' and data.get('truncated_reasons', {}).get('count', 0) > 0:
                    issues.append(f"⚠️ {data['truncated_reasons']['count']} symbols have truncated reasons")
                
                if tab == 'positions' and not data.get('sync_status', {}).get('synced', False):
                    issues.append("❌ Positions not synced between risk manager and broker")
                
                if issues:
                    report.extend(issues)
                else:
                    report.append("✅ No issues found")
            
            report.append("")
        
        return "\n".join(report)

if __name__ == "__main__":
    auditor = DashboardAuditor()
    auditor.run_full_audit()
    print(auditor.generate_report())
