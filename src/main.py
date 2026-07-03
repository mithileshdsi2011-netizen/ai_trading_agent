"""
Main entry point for AI Trading Agent
"""
import sys
import os

# Add src directory to path for imports
src_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, src_dir)

# Change to src directory to ensure relative imports work
os.chdir(src_dir)

from trading_orchestrator import main

if __name__ == "__main__":
    main()
