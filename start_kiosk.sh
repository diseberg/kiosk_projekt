#!/bin/bash
# Robust kiosk startup script for autostart

# Set working directory
cd "$(dirname "$0")"

# Log file for debugging
LOGFILE="$HOME/kiosk_startup.log"
echo "=== Kiosk startup at $(date) ===" >> "$LOGFILE"

# Kill any existing processes on port 5000
echo "Checking for existing processes on port 5000..." >> "$LOGFILE"
pkill -f "gunicorn.*app:app" 2>> "$LOGFILE"
sleep 2

# Wait for network to be ready (important for Google Sheets API)
echo "Waiting for network..." >> "$LOGFILE"
for i in {1..30}; do
    if ping -c 1 8.8.8.8 &> /dev/null; then
        echo "Network ready after $i seconds" >> "$LOGFILE"
        break
    fi
    sleep 1
done

# Install/update dependencies (skip if already done recently)
if [ ! -f .deps_installed ] || [ $(find .deps_installed -mtime +7) ]; then
    echo "Installing dependencies..." >> "$LOGFILE"
    pip install -r requirements.txt >> "$LOGFILE" 2>&1
    touch .deps_installed
fi

# Initialize database
echo "Initializing database..." >> "$LOGFILE"
python3 sync_members.py init-db >> "$LOGFILE" 2>&1

# Start Gunicorn in background
echo "Starting Gunicorn server..." >> "$LOGFILE"
gunicorn -w 4 -b 0.0.0.0:5000 app:app >> "$LOGFILE" 2>&1 &
GUNICORN_PID=$!
echo "Gunicorn started with PID: $GUNICORN_PID" >> "$LOGFILE"

# Wait for server to be ready
echo "Waiting for server to start..." >> "$LOGFILE"
for i in {1..30}; do
    if curl -s http://localhost:5000 > /dev/null; then
        echo "Server ready after $i seconds" >> "$LOGFILE"
        break
    fi
    sleep 1
done

# Give it an extra moment to stabilize
sleep 2

# Start browser in kiosk mode
echo "Starting browser..." >> "$LOGFILE"
DISPLAY=:0 chromium-browser --kiosk --noerrdialogs --disable-infobars \
    --disable-session-crashed-bubble --disable-restore-session-state \
    --disable-features=TranslateUI --no-first-run \
    http://localhost:5000 >> "$LOGFILE" 2>&1 &

echo "Kiosk startup complete" >> "$LOGFILE"
