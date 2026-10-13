#!/usr/bin/env bash
# Install the edge agent on a Debian/Ubuntu (x86_64 or arm64) thin client.
#
#   sudo deploy/edge/install.sh --server https://analytics.example.com --key dooh_xxx [--device /dev/video0]
#
# Re-running upgrades the agent in place and keeps the existing config.
set -euo pipefail

SERVER=""
KEY=""
DEVICE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --server) SERVER="$2"; shift 2 ;;
    --key) KEY="$2"; shift 2 ;;
    --device) DEVICE="$2"; shift 2 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "run as root (sudo)" >&2; exit 1; }
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

echo "==> System packages"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip curl v4l-utils >/dev/null

echo "==> Service user"
id dooh >/dev/null 2>&1 || useradd --system --home /var/lib/dooh-edge --shell /usr/sbin/nologin dooh
usermod -aG video dooh
install -d -o dooh -g dooh /var/lib/dooh-edge /var/lib/dooh-edge/models
install -d /etc/dooh-edge /opt/dooh-edge

echo "==> Python environment"
[[ -d /opt/dooh-edge/venv ]] || python3 -m venv /opt/dooh-edge/venv
/opt/dooh-edge/venv/bin/pip install -q --upgrade pip
/opt/dooh-edge/venv/bin/pip install -q "$REPO/edge"

echo "==> Models"
"$REPO/scripts/fetch_models.sh" /var/lib/dooh-edge/models
chown -R dooh:dooh /var/lib/dooh-edge

echo "==> Configuration"
CONF=/etc/dooh-edge/edge.toml
if [[ ! -f $CONF ]]; then
  cp "$REPO/deploy/edge/edge.example.toml" "$CONF"
  [[ -n $SERVER ]] && sed -i "s#^url = .*#url = \"$SERVER\"#" "$CONF"
  [[ -n $KEY ]] && sed -i "s#^device_key = .*#device_key = \"$KEY\"#" "$CONF"
  [[ -n $DEVICE ]] && sed -i "s#^device = .*#device = \"$DEVICE\"#" "$CONF"
fi
# The config holds the device key: readable by the service only.
chown root:dooh "$CONF"
chmod 640 "$CONF"

echo "==> systemd"
install -m 644 "$REPO/deploy/edge/dooh-edge.service" /etc/systemd/system/dooh-edge.service
systemctl daemon-reload
systemctl enable --now dooh-edge
systemctl restart dooh-edge

echo
/opt/dooh-edge/venv/bin/dooh-edge --config "$CONF" check || true
echo
echo "Installed. Logs: journalctl -u dooh-edge -f    Local dashboard: http://127.0.0.1:8080/"
