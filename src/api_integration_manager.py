"""
API Integration Manager
Centralized API call management with optimization and monitoring
"""
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from queue import Queue, PriorityQueue
import logging

from api_usage_monitor import api_monitor
from optimized_market_data import OptimizedMarketDataFetcher
from optimized_signal_generator import OptimizedSignalGenerator

logger = logging.getLogger(__name__)


class APIIntegrationManager:
    """Centralized API management with optimization and monitoring"""
    
    def __init__(self):
        self.market_data = OptimizedMarketDataFetcher()
        self.signal_generator = OptimizedSignalGenerator()
        self.request_queue = PriorityQueue()
        self.monitoring_active = True
        self.worker_thread = None
        self.start_monitoring()
    
    def start_monitoring(self):
        """Start the API monitoring worker thread"""
        if self.worker_thread is None or not self.worker_thread.is_alive():
            self.worker_thread = threading.Thread(target=self._monitoring_worker, daemon=True)
            self.worker_thread.start()
            logger.info("API monitoring started")
    
    def _monitoring_worker(self):
        """Background worker for API monitoring and optimization"""
        while self.monitoring_active:
            try:
                # Generate monitoring report every 30 seconds
                time.sleep(30)
                self._generate_health_report()
            except Exception as e:
                logger.error(f"Monitoring worker error: {e}")
    
    def _generate_health_report(self):
        """Generate periodic health report"""
        try:
            report = api_monitor.generate_report()
            health_score = report['health_score']
            
            if health_score < 70:
                logger.warning(f"API health degraded: {health_score}/100")
                self._trigger_optimization_alert(report)
            elif health_score < 85:
                logger.info(f"API health monitoring: {health_score}/100")
            
        except Exception as e:
            logger.error(f"Health report generation error: {e}")
    
    def _trigger_optimization_alert(self, report: Dict):
        """Trigger alert for optimization opportunities"""
        high_utilization = [
            endpoint for endpoint, usage in report['current_usage'].items()
            if usage['utilization'] > 80
        ]
        
        if high_utilization:
            logger.warning(f"High API utilization detected: {high_utilization}")
            logger.warning("Consider implementing additional optimizations")
    
    def get_optimized_signals(self, symbols: List[str], risk_data: Dict = None) -> List[Dict]:
        """
        Get optimized signals with minimal API usage
        
        Args:
            symbols: List of symbols to analyze
            risk_data: Risk management context
            
        Returns:
            List of trading signals
        """
        start_time = time.time()
        
        # Use optimized signal generator
        signals = self.signal_generator.generate_signals_for_watchlist(symbols, risk_data)
        
        elapsed = time.time() - start_time
        logger.info(f"Optimized signal generation completed in {elapsed:.1f}s")
        
        return signals
    
    def get_batch_market_data(self, symbols: List[str]) -> Dict[str, Any]:
        """
        Get comprehensive market data with minimal API calls
        
        Args:
            symbols: List of symbols
            
        Returns:
            Dictionary with prices, info, and historical data
        """
        start_time = time.time()
        
        # Batch fetch all data types
        prices = self.market_data.get_batch_realtime_prices(symbols)
        stock_info = self.market_data.get_batch_stock_info(symbols)
        historical_data = self.market_data.get_batch_historical_data(symbols)
        
        elapsed = time.time() - start_time
        api_monitor.record_api_call('batch_market_data', batch_size=len(symbols), response_time=elapsed)
        
        return {
            'prices': prices,
            'stock_info': stock_info,
            'historical_data': historical_data,
            'fetch_time': elapsed,
            'symbols_count': len(symbols)
        }
    
    def get_api_usage_report(self) -> Dict[str, Any]:
        """Get comprehensive API usage report"""
        return api_monitor.generate_report()
    
    def get_optimization_recommendations(self) -> List[Dict[str, Any]]:
        """Get real-time optimization recommendations"""
        return api_monitor.get_optimization_recommendations()
    
    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get performance and optimization metrics"""
        current_usage = api_monitor.get_current_usage()
        optimization_stats = self.signal_generator.get_optimization_stats()
        
        # Calculate efficiency metrics
        total_calls = sum(usage['calls'] for usage in current_usage.values())
        total_limits = sum(usage['rate_limit'] for usage in current_usage.values())
        efficiency = ((total_limits - total_calls) / total_limits * 100) if total_limits > 0 else 0
        
        return {
            'total_api_calls': total_calls,
            'total_rate_limit': total_limits,
            'efficiency_percentage': efficiency,
            'cache_hit_rate': self._calculate_cache_hit_rate(),
            'circuit_breaker_events': sum(usage['circuit_breakers'] for usage in current_usage.values()),
            'optimization_stats': optimization_stats,
            'current_usage': current_usage
        }
    
    def _calculate_cache_hit_rate(self) -> float:
        """Calculate overall cache hit rate"""
        try:
            report = api_monitor.generate_report()
            total_hits = sum(report['cache_stats'].get(endpoint, {}).get('hit_rate', 0) * 
                           report['cache_stats'].get(endpoint, {}).get('total_requests', 0)
                           for endpoint in report['cache_stats'])
            
            total_requests = sum(report['cache_stats'].get(endpoint, {}).get('total_requests', 0)
                               for endpoint in report['cache_stats'])
            
            return (total_hits / total_requests * 100) if total_requests > 0 else 0
        except:
            return 0
    
    def emergency_optimize(self):
        """Trigger emergency optimization when API limits are approached"""
        logger.warning("Emergency optimization triggered")
        
        # Clear caches to free memory
        self.market_data.clear_quote_cache()
        
        # Extend cache TTLs to reduce API calls
        self.market_data._QUOTE_CACHE_TTL = timedelta(minutes=2)
        
        # Log emergency action
        api_monitor.record_api_call('emergency_optimization', batch_size=1)
        
        logger.info("Emergency optimization completed")
    
    def reset_optimizations(self):
        """Reset optimizations to normal settings"""
        logger.info("Resetting optimizations to normal settings")
        
        # Reset cache TTL
        self.market_data._QUOTE_CACHE_TTL = timedelta(seconds=30)
        
        # Clear monitoring alerts
        logger.info("Optimizations reset to normal")
    
    def shutdown(self):
        """Shutdown the API integration manager"""
        self.monitoring_active = False
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=5)
        logger.info("API integration manager shutdown")


# Global instance for centralized API management
api_manager = APIIntegrationManager()
