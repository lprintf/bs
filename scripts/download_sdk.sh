#!/bin/bash
set -e

VENDOR_DIR="tabshare/server/static/vendor"
echo "Downloading LiveKit JS SDK to $VENDOR_DIR..."

curl -sL "https://cdn.jsdelivr.net/npm/livekit-client/dist/livekit-client.umd.min.js" -o "$VENDOR_DIR/livekit-client.umd.min.js"

echo "Done!"
