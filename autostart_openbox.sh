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

# 2. Inställningar - Skärmsläckning men INGEN lösenordsskärm
# Stäng av screensaver men tillåt DPMS att släcka skärmen
xset s off          # Ingen screensaver
xset s noblank      # Ingen blanking
# DPMS: standby efter 60 min, suspend efter 60 min, off efter 60 min
xset dpms 3600 3600 3600

# Döda och förhindra alla screenlockers (aggressivt)
killall -9 xscreensaver light-locker xfce4-screensaver gnome-screensaver 2>/dev/null
# Kontinuerligt döda light-locker om den försöker starta
(
  while true; do
    killall -9 light-locker 2>/dev/null
    sleep 30
  done
) &

# Inaktivera screen locking i alla möjliga desktop environments
gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null
gsettings set org.gnome.desktop.screensaver idle-activation-enabled false 2>/dev/null
gsettings set org.gnome.desktop.lockdown disable-lock-screen true 2>/dev/null
gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null
xfconf-query -c xfce4-screensaver -p /lock/enabled -s false 2>/dev/null
xfconf-query -c xfce4-screensaver -p /saver/enabled -s false 2>/dev/null
xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/dpms-enabled -s true 2>/dev/null
xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/dpms-on-ac-sleep -s 60 2>/dev/null
xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/lock-screen-suspend-hibernate -s false 2>/dev/null

unclutter -idle 5 &

# Stäng av datorn efter 3 timmars inaktivitet
(
  # Vänta lite innan vi startar inaktivitetskontrollen
  sleep 60
  while true; do
    # Kolla idle time (i millisekunder)
    IDLE_TIME=$(xprintidle 2>/dev/null || echo 0)
    # 3 timmar = 10800000 millisekunder
    if [ "$IDLE_TIME" -gt 10800000 ]; then
      echo "3 hours of inactivity detected, shutting down..." >> "$LOGFILE"
      /sbin/shutdown -h now
      exit 0
    fi
    # Kolla var 5:e minut
    sleep 300
  done
) &

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
