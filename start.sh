#!/bin/bash
# Install dependencies if needed
pip install -r requirements.txt

# Initialize DB if needed
python sync_members.py init-db

# Run with Gunicorn
# -w 4: Use 4 worker processes (good for handling multiple requests)
# -b 0.0.0.0:5000: Bind to all network interfaces on port 5000
echo "Starting Kiosk Server on port 5000..."
gunicorn -w 4 -b 0.0.0.0:5000 app:app
