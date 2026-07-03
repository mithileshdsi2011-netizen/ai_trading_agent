#!/bin/bash

# AI Trading Agent Setup Script

echo "=========================================="
echo "AI Trading Agent Setup"
echo "=========================================="
echo ""

# Check Python version
echo "Checking Python version..."
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "Python version: $python_version"

# Create virtual environment
echo ""
echo "Creating virtual environment..."
python3 -m venv venv

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Upgrade pip
echo ""
echo "Upgrading pip..."
pip install --upgrade pip

# Install dependencies
echo ""
echo "Installing dependencies..."
pip install -r requirements.txt

# Check TA-Lib installation
echo ""
echo "Checking TA-Lib installation..."
python -c "import talib" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "TA-Lib is already installed"
else
    echo "TA-Lib not found. Please install TA-Lib manually:"
    echo "  macOS: brew install ta-lib && pip install ta-lib"
    echo "  Linux: Follow instructions in README.md"
    echo "  Windows: Download wheel from lfd.uci.edu"
fi

# Create .env file if it doesn't exist
echo ""
if [ ! -f .env ]; then
    echo "Creating .env file from .env.example..."
    cp .env.example .env
    echo "Please edit .env file and add your API keys"
else
    echo ".env file already exists"
fi

# Create necessary directories
echo ""
echo "Creating necessary directories..."
mkdir -p logs data

# Run tests
echo ""
echo "Running tests..."
pytest tests/ -v

echo ""
echo "=========================================="
echo "Setup completed!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Edit .env file and add your API keys"
echo "2. Run: cd src && python trading_orchestrator.py once"
echo ""
