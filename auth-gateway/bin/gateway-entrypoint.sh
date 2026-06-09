#!/usr/bin/env sh
set -eu

JWKS_URL="${JWKS_URL:-http://auth-service:8000/.well-known/jwks.json}"
JWKS_PATH="${JWKS_PATH:-/etc/nginx/jwks/jwks.json}"
PEM_DIR="${PEM_DIR:-/etc/nginx/jwks}"

mkdir -p "$PEM_DIR"

echo "Fetching JWKS from $JWKS_URL"
for i in $(seq 1 50); do
  if wget -q -O "$JWKS_PATH" "$JWKS_URL"; then
    break
  fi
  if [ "$i" = "50" ]; then
    echo "Failed to fetch JWKS from $JWKS_URL" >&2
    exit 1
  fi
  sleep 0.2
done

exec /usr/local/openresty/bin/openresty -g "daemon off;"
