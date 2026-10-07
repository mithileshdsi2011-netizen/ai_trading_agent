"""
Base IPO Data Provider Interface

Abstract base class for IPO data providers.
"""

from abc import ABC, abstractmethod
from typing import List, Optional
from ..models import IPO, IPOStatus


class IPODataProvider(ABC):
    """
    Abstract base class for IPO data providers.
    
    Implementations can fetch IPO data from various sources:
    - NSE/BSE official APIs
    - SEBI offer documents
    - Broker APIs
    - Authorized market data providers
    - Demo/mock data for testing
    """
    
    @abstractmethod
    def get_open_ipos(self) -> List[IPO]:
        """
        Get currently open IPOs.
        
        Returns:
            List of IPOs with status OPEN.
        """
        pass
    
    @abstractmethod
    def get_upcoming_ipos(self) -> List[IPO]:
        """
        Get upcoming IPOs.
        
        Returns:
            List of IPOs with status UPCOMING.
        """
        pass
    
    @abstractmethod
    def get_recent_ipos(self) -> List[IPO]:
        """
        Get recently closed IPOs.
        
        Returns:
            List of IPOs with status CLOSED or LISTED.
        """
        pass
    
    @abstractmethod
    def get_ipo_details(self, ipo_id: str) -> Optional[IPO]:
        """
        Get detailed information for a specific IPO.
        
        Args:
            ipo_id: Unique identifier for the IPO (symbol or internal ID).
            
        Returns:
            IPO object with full details, or None if not found.
        """
        pass
    
    @abstractmethod
    def is_demo_data(self) -> bool:
        """
        Check if this provider is using demo/mock data.
        
        Returns:
            True if using demo data, False for live data.
        """
        pass
    
    @abstractmethod
    def get_data_source_name(self) -> str:
        """
        Get the name of the data source.
        
        Returns:
            Name of the data source (e.g., "NSE", "BSE", "Demo").
        """
        pass
