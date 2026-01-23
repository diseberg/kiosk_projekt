# Debian Kiosk Setup Guide

## Required Packages

Install these on your Debian system:

```bash
sudo apt-get update
sudo apt-get install -y xprintidle
```

## Screen Lock Settings

The autostart script will:
- Keep screen ON for 60 minutes
- Turn screen OFF after 60 minutes (DPMS power save)
- NO password required when you move mouse
- Auto-shutdown computer after 3 hours of inactivity

## Disable LightDM Lock Screen (if using LightDM)

If you still get password prompts, edit LightDM config:

```bash
sudo nano /etc/lightdm/lightdm.conf
```

Add under `[Seat:*]`:
```ini
[Seat:*]
allow-guest=false
user-session=openbox
autologin-user=incheckning
autologin-user-timeout=0
```

Then restart:
```bash
sudo systemctl restart lightdm
```

## Testing

Test DPMS settings:
```bash
xset q | grep -A 5 "DPMS"
```

Should show:
```
DPMS is Enabled
Standby: 3600    Suspend: 3600    Off: 3600
```

Test idle time:
```bash
xprintidle
```

Shows idle time in milliseconds.

## Troubleshooting

If screen still locks:
```bash
# Check what's running
ps aux | grep -i lock
ps aux | grep -i screen

# Kill any lockers
killall xscreensaver light-locker xfce4-screensaver
```

Check autostart log:
```bash
tail -f /home/incheckning/kiosk_startup.log
```
