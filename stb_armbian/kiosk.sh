#!/bin/bash

KIOSK_DIR=/home/armbian/kiosk

# Wait for graphical environment ready
sleep 20

echo 'Hiding mouse cursor...'
unclutter -idle 0.1 -root &

# Kill any existing Firefox instance
pkill -f firefox-esr 2>/dev/null
sleep 2

# Remove profile lock if leftover from crash
rm -f "$KIOSK_DIR/firefox-profile/lock"
rm -f "$KIOSK_DIR/firefox-profile/.parentlock"

echo 'Starting Firefox in kiosk mode...'
/usr/bin/firefox-esr \
    --noerrdialogs \
    --kiosk \
    --no-remote \
    --profile "$KIOSK_DIR/firefox-profile" \
    "file://$KIOSK_DIR/index.html"
