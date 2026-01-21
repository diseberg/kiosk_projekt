#!/bin/bash
# Stop kiosk application cleanly

echo "Stopping kiosk..."

# Kill browser
pkill -f chromium-browser

# Kill gunicorn
pkill -f "gunicorn.*app:app"

# Wait a moment
sleep 2

# Force kill if still running
pkill -9 -f "gunicorn.*app:app" 2>/dev/null
pkill -9 -f chromium-browser 2>/dev/null

echo "Kiosk stopped"
