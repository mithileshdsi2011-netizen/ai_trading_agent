"""
Main entry point for AI Trading Agent (run from project root)
"""
import sys
import os

# Add src directory to path
project_root = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.join(project_root, 'src')
sys.path.insert(0, src_dir)

# Change to src directory
os.chdir(src_dir)

from trading_orchestrator import main

if __name__ == "__main__":
    main()
