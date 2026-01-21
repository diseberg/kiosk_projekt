#!/bin/bash
export DISPLAY=:0

# Prevent multiple instances of this script
LOCKFILE="/tmp/kiosk_autostart.lock"
if [ -e "$LOCKFILE" ]; then
    echo "Script already running (lockfile exists), exiting" >> /home/incheckning/kiosk_startup.log
    exit 0
fi
touch "$LOCKFILE"
trap "rm -f $LOCKFILE" EXIT

# Logging
LOGFILE="/home/incheckning/kiosk_startup.log"
echo "=== Kiosk startup at $(date) ===" >> "$LOGFILE"

# 1. Städa - AGGRESSIVT
echo "Cleaning up old processes..." >> "$LOGFILE"

# Check what's on port 5000 BEFORE cleanup
echo "Before cleanup - processes on port 5000:" >> "$LOGFILE"
ss -tlnp | grep ":5000 " >> "$LOGFILE" 2>&1

killall -9 chromium chromium-browser 2>/dev/null
pkill -9 -f "gunicorn" 2>/dev/null
pkill -9 -f "python.*app.py" 2>/dev/null
pkill -9 python3 2>/dev/null
pkill -9 python 2>/dev/null

# Kill anything on port 5000 - FORCE
fuser -k -9 5000/tcp 2>/dev/null

# Wait for processes to fully die
sleep 5

# Verify port is free
if ss -tuln | grep -q ":5000 "; then
    echo "ERROR: Port 5000 STILL in use after cleanup!" >> "$LOGFILE"
    ss -tlnp | grep ":5000 " >> "$LOGFILE" 2>&1
    echo "Attempting FORCE kill again..." >> "$LOGFILE"
    fuser -k -9 5000/tcp 2>/dev/null
    sleep 3
    # Final check
    if ss -tuln | grep -q ":5000 "; then
        echo "CRITICAL: Cannot free port 5000, aborting!" >> "$LOGFILE"
        exit 1
    fi
fi

echo "Port 5000 is now free" >> "$LOGFILE"
rm -rf /tmp/kiosk_profile

# 2. Inställningar
xset s off
xset s noblank
xset -dpms

# Disable screen locking
gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null
gsettings set org.gnome.desktop.lockdown disable-lock-screen true 2>/dev/null
xfconf-query -c xfce4-screensaver -p /lock/enabled -s false 2>/dev/null
xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/dpms-enabled -s false 2>/dev/null

unclutter -idle 5 &

(
  while true; do
    xdotool mousemove_relative 1 1
    xdotool mousemove_relative -- -1 -1
    sleep 240
  done
) &

# 3. Vänta på nätverk (viktigt för Google Sheets!)
echo "Waiting for network..." >> "$LOGFILE"
for i in {1..30}; do
    if ping -c 1 8.8.8.8 &> /dev/null; then
        echo "Network ready after $i seconds" >> "$LOGFILE"
        break
    fi
    sleep 1
done

# 4. Starta appen
cd /home/incheckning/kiosk_projekt
source venv/bin/activate

echo "Initializing database..." >> "$LOGFILE"
python sync_members.py init-db >> "$LOGFILE" 2>&1

# Starta med GUNICORN (inte Flask dev server)
echo "Starting Gunicorn..." >> "$LOGFILE"
gunicorn -w 4 -b 0.0.0.0:5000 app:app >> "$LOGFILE" 2>&1 &
GUNICORN_PID=$!
echo "Gunicorn PID: $GUNICORN_PID" >> "$LOGFILE"

# 5. Vänta på att servern är REDO (inte bara 20 sek)
echo "Waiting for server..." >> "$LOGFILE"
for i in {1..60}; do
    if curl -s http://127.0.0.1:5000 > /dev/null 2>&1; then
        echo "Server ready after $i seconds" >> "$LOGFILE"
        break
    fi
    sleep 1
done

# Extra stabiliseringstid
sleep 3

# 6. Starta Chromium
echo "Starting browser..." >> "$LOGFILE"
chromium --kiosk --incognito --noerrdialogs --disable-infobars \
--user-data-dir="/tmp/kiosk_profile" \
--password-store=basic \
--no-first-run \
--disable-features=Translate,OnDeviceModel,OptimizationGuideModelDownloading \
--disable-component-update \
--disable-sync \
--disable-background-networking \
--disable-software-rasterizer \
--no-v8-untrusted-code-mitigations \
"http://127.0.0.1:5000" >> "$LOGFILE" 2>&1 &

echo "Startup complete!" >> "$LOGFILE"
