#!/bin/bash
# Wrapper script to activate virtual environment and run Python commands

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Activate virtual environment
source "$SCRIPT_DIR/venv/bin/activate"

# Add project root to PYTHONPATH so imports work from src/
export PYTHONPATH="${PYTHONPATH}:${SCRIPT_DIR}"

# Run the Python command with all arguments
python "$@"
