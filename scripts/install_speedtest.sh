#!/usr/bin/env bash
# Install the official Ookla Speedtest CLI on the Raspberry Pi (arm64/armhf),
# used by the hourly speed test job (app/services/speedtest_service.py).
# Installs a single static binary to /usr/local/bin/speedtest - no apt repo.
#
#   sudo ./scripts/install_speedtest.sh
#
# Using the CLI implies accepting Ookla's EULA and privacy policy
# (https://www.speedtest.net/about/eula) - the collector passes
# --accept-license --accept-gdpr on every run.
set -euo pipefail

VERSION="${SPEEDTEST_VERSION:-1.2.0}"
case "$(uname -m)" in
  aarch64|arm64) ARCH="aarch64" ;;
  armv7l|armv6l) ARCH="armhf" ;;
  x86_64)        ARCH="x86_64" ;;
  *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac
URL="https://install.speedtest.net/app/cli/ookla-speedtest-${VERSION}-linux-${ARCH}.tgz"

if [[ $EUID -ne 0 ]]; then
  echo "run with sudo" >&2; exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "==> Downloading $URL"
curl -fsSL -o "$TMP/speedtest.tgz" "$URL"
tar -xzf "$TMP/speedtest.tgz" -C "$TMP" speedtest
install -m 0755 "$TMP/speedtest" /usr/local/bin/speedtest
echo "==> Installed: $(/usr/local/bin/speedtest --version | head -1)"
echo "    Nearby servers (set speedtest.server_id in config/config.yaml to pin one):"
/usr/local/bin/speedtest --accept-license --accept-gdpr -L | head -15 || true
