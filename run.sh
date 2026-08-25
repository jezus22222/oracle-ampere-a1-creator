#!/bin/bash
# Oracle Cloud Ampere A1 Instance Creator - One-command startup (Linux/macOS)

set -e

echo "=========================================="
echo "Oracle Cloud Ampere A1 Instance Creator"
echo "=========================================="

# Check for Python
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 not found. Please install Python 3.8+"
    exit 1
fi

# Check for virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install/upgrade dependencies
echo "Installing dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

# Check for OCI config
if [ ! -f ~/.oci/config ]; then
    echo "Warning: ~/.oci/config not found. Please run 'oci setup config' first."
    echo "Or copy oci_config.example to ~/.oci/config and fill in your credentials."
fi

# Check for SSH private key file
SSH_KEY_FILE="${SSH_PRIVATE_KEY_FILE:-$HOME/.ssh/id_rsa}"
if [ ! -f "$SSH_KEY_FILE" ]; then
    echo "Warning: SSH private key not found at $SSH_KEY_FILE"
    echo "Set SSH_PRIVATE_KEY_FILE or place your key there."
fi

echo ""
echo "Starting dashboard on http://localhost:5050 ..."
python dashboard.py &
DASHBOARD_PID=$!

# Give dashboard time to start
sleep 2

echo "Starting instance creator..."
echo "Dashboard available at: http://localhost:5050"
echo "Press Ctrl+C to stop both processes"
echo ""

# Trap Ctrl+C to kill both processes
trap "kill $DASHBOARD_PID 2>/dev/null; exit 0" INT TERM

# Run instance creator
python create_ampere_a1.py

# Cleanup
kill $DASHBOARD_PID 2>/dev/null