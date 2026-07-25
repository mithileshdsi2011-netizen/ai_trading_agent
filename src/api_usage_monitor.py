"""
Kite API Usage Monitor
Comprehensive audit and optimization of API calls
"""
import time
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Dict, List, Any
import logging

logger = logging.getLogger(__name__)

class APIUsageMonitor:
    """Monitors and analyzes Kite API usage patterns"""
    
    def __init__(self):
        self.api_calls = defaultdict(list)  # endpoint -> list of timestamps
        self.call_counts = defaultdict(int)  # endpoint -> count
        self.call_times = defaultdict(list)  # endpoint -> list of response times
        self.circuit_breaker_activations = defaultdict(int)  # endpoint -> count
        self.batch_efficiency = defaultdict(list)  # endpoint -> batch_size list
        self.cache_hits = defaultdict(int)  # endpoint -> cache hits
        self.cache_misses = defaultdict(int)  # endpoint -> cache misses
        
        self.lock = threading.Lock()
        self.start_time = datetime.now()
        
        # Kite API rate limits (per minute)
        self.RATE_LIMITS = {
            'quote': 60,           # 60 requests per minute
            'ltp': 60,             # 60 requests per minute  
            'historical_data': 60, # 60 requests per minute
            'instruments': 10,     # 10 requests per minute
            'positions': 60,       # 60 requests per minute
            'holdings': 60,        # 60 requests per minute
            'orders': 60,          # 60 requests per minute
            'margins': 60,         # 60 requests per minute
            'profile': 60,         # 60 requests per minute
            'generate_session': 10, # 10 requests per minute
            'corporate_actions': 10 # 10 requests per minute
        }
    
    def record_api_call(self, endpoint: str, batch_size: int = 1, 
                       response_time: float = 0, cache_hit: bool = False):
        """Record an API call for monitoring"""
        with self.lock:
            timestamp = datetime.now()
            self.api_calls[endpoint].append(timestamp)
            self.call_counts[endpoint] += 1
            self.call_times[endpoint].append(response_time)
            
            if batch_size > 1:
                self.batch_efficiency[endpoint].append(batch_size)
            
            if cache_hit:
                self.cache_hits[endpoint] += 1
            else:
                self.cache_misses[endpoint] += 1
    
    def record_circuit_breaker(self, endpoint: str):
        """Record circuit breaker activation"""
        with self.lock:
            self.circuit_breaker_activations[endpoint] += 1
            logger.warning(f"Circuit breaker activated for {endpoint}")
    
    def get_current_usage(self, window_minutes: int = 1) -> Dict[str, Any]:
        """Get current API usage in the last N minutes"""
        with self.lock:
            cutoff_time = datetime.now() - timedelta(minutes=window_minutes)
            current_usage = {}
            
            for endpoint, timestamps in self.api_calls.items():
                recent_calls = [t for t in timestamps if t > cutoff_time]
                current_usage[endpoint] = {
                    'calls': len(recent_calls),
                    'rate_limit': self.RATE_LIMITS.get(endpoint, 60),
                    'utilization': len(recent_calls) / self.RATE_LIMITS.get(endpoint, 60) * 100,
                    'circuit_breakers': self.circuit_breaker_activations[endpoint]
                }
            
            return current_usage
    
    def get_optimization_recommendations(self) -> List[Dict[str, Any]]:
        """Analyze usage patterns and suggest optimizations"""
        recommendations = []
        current_usage = self.get_current_usage()
        
        # Check for high utilization endpoints
        for endpoint, usage in current_usage.items():
            if usage['utilization'] > 80:
                recommendations.append({
                    'priority': 'HIGH',
                    'endpoint': endpoint,
                    'issue': f'High utilization: {usage["utilization"]:.1f}%',
                    'suggestion': self._get_optimization_suggestion(endpoint),
                    'potential_savings': self._estimate_savings(endpoint)
                })
            elif usage['utilization'] > 60:
                recommendations.append({
                    'priority': 'MEDIUM',
                    'endpoint': endpoint,
                    'issue': f'Moderate utilization: {usage["utilization"]:.1f}%',
                    'suggestion': self._get_optimization_suggestion(endpoint),
                    'potential_savings': self._estimate_savings(endpoint)
                })
        
        # Check for inefficient batching
        for endpoint, batch_sizes in self.batch_efficiency.items():
            if batch_sizes:
                avg_batch_size = sum(batch_sizes) / len(batch_sizes)
                if avg_batch_size < 5 and endpoint in ['quote', 'ltp']:
                    recommendations.append({
                        'priority': 'MEDIUM',
                        'endpoint': endpoint,
                        'issue': f'Low batch efficiency: avg {avg_batch_size:.1f} items/call',
                        'suggestion': 'Increase batch size to 10-50 items per call',
                        'potential_savings': f'{int(50/avg_batch_size * 100 - 100)}% reduction'
                    })
        
        # Check cache effectiveness
        for endpoint in self.cache_hits:
            total_requests = self.cache_hits[endpoint] + self.cache_misses[endpoint]
            if total_requests > 0:
                hit_rate = self.cache_hits[endpoint] / total_requests
                if hit_rate < 0.3 and endpoint in ['instruments', 'historical_data']:
                    recommendations.append({
                        'priority': 'LOW',
                        'endpoint': endpoint,
                        'issue': f'Low cache hit rate: {hit_rate*100:.1f}%',
                        'suggestion': 'Increase cache TTL or implement smarter caching',
                        'potential_savings': f'{int(hit_rate * 100)}% reduction'
                    })
        
        return sorted(recommendations, key=lambda x: (
            {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}[x['priority']],
            -x['utilization'] if 'utilization' in x else 0
        ))
    
    def _get_optimization_suggestion(self, endpoint: str) -> str:
        """Get specific optimization suggestion for endpoint"""
        suggestions = {
            'quote': 'Batch quote requests for 10-50 symbols at once',
            'ltp': 'Batch LTP requests and cache for 30 seconds',
            'historical_data': 'Cache historical data for 1 hour, reuse across scans',
            'instruments': 'Cache instruments list for entire trading day',
            'positions': 'Cache for 30 seconds during position monitoring',
            'holdings': 'Cache for 5 minutes, only refresh on trades',
            'orders': 'Cache for 30 seconds, batch order status checks',
            'margins': 'Cache for 1 minute, only refresh on trades'
        }
        return suggestions.get(endpoint, 'Consider batching and caching')
    
    def _estimate_savings(self, endpoint: str) -> str:
        """Estimate potential savings from optimization"""
        current_usage = self.get_current_usage()
        usage = current_usage.get(endpoint, {})
        
        if usage.get('utilization', 0) > 80:
            return '50-70% reduction possible'
        elif usage.get('utilization', 0) > 60:
            return '30-50% reduction possible'
        else:
            return '10-30% reduction possible'
    
    def generate_report(self) -> Dict[str, Any]:
        """Generate comprehensive API usage report"""
        current_usage = self.get_current_usage()
        recommendations = self.get_optimization_recommendations()
        
        # Calculate statistics
        total_calls = sum(self.call_counts.values())
        total_circuit_breakers = sum(self.circuit_breaker_activations.values())
        
        # Average response times
        avg_response_times = {}
        for endpoint, times in self.call_times.items():
            if times:
                avg_response_times[endpoint] = sum(times) / len(times)
        
        # Cache effectiveness
        cache_stats = {}
        for endpoint in self.cache_hits:
            total = self.cache_hits[endpoint] + self.cache_misses[endpoint]
            if total > 0:
                cache_stats[endpoint] = {
                    'hit_rate': self.cache_hits[endpoint] / total,
                    'total_requests': total
                }
        
        return {
            'timestamp': datetime.now().isoformat(),
            'monitoring_duration': str(datetime.now() - self.start_time),
            'total_api_calls': total_calls,
            'total_circuit_breakers': total_circuit_breakers,
            'current_usage': current_usage,
            'call_counts': dict(self.call_counts),
            'avg_response_times': avg_response_times,
            'cache_stats': cache_stats,
            'recommendations': recommendations,
            'health_score': self._calculate_health_score()
        }
    
    def _calculate_health_score(self) -> int:
        """Calculate API usage health score (0-100)"""
        current_usage = self.get_current_usage()
        
        # Base score starts at 100
        score = 100
        
        # Deduct points for high utilization
        for usage in current_usage.values():
            utilization = usage.get('utilization', 0)
            if utilization > 90:
                score -= 30
            elif utilization > 80:
                score -= 20
            elif utilization > 60:
                score -= 10
        
        # Deduct points for circuit breakers
        total_circuit_breakers = sum(self.circuit_breaker_activations.values())
        if total_circuit_breakers > 10:
            score -= 20
        elif total_circuit_breakers > 5:
            score -= 10
        elif total_circuit_breakers > 0:
            score -= 5
        
        return max(0, score)

# Global instance for monitoring
api_monitor = APIUsageMonitor()

def monitored_api_call(endpoint: str):
    """Decorator to monitor API calls"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            start_time = time.time()
            cache_hit = kwargs.pop('cache_hit', False)
            batch_size = kwargs.pop('batch_size', 1)
            
            try:
                result = func(*args, **kwargs)
                response_time = time.time() - start_time
                api_monitor.record_api_call(endpoint, batch_size, response_time, cache_hit)
                return result
            except Exception as e:
                if 'circuit' in str(e).lower() or 'rate' in str(e).lower():
                    api_monitor.record_circuit_breaker(endpoint)
                raise
        
        return wrapper
    return decorator
