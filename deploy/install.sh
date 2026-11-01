#!/usr/bin/env bash
# Install Sauti on a fresh Debian 12 or Ubuntu 24.04 VM. Run as root from a checkout:
#   sudo ./deploy/install.sh
# Re-running is safe: it updates the code and keeps config, data and models.
set -euo pipefail

PREFIX=/opt/sauti
LLAMA_TAG="${LLAMA_TAG:-b12032}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> System packages"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip curl unzip rsync caddy age sqlite3 >/dev/null

echo "==> Service account and folders"
id sauti >/dev/null 2>&1 || useradd --system --home-dir "$PREFIX" --shell /usr/sbin/nologin sauti
install -d -o sauti -g sauti -m 0750 "$PREFIX" "$PREFIX/models" "$PREFIX/llama"
install -d -o sauti -g sauti -m 0700 /var/lib/sauti
install -d -m 0755 /etc/sauti

echo "==> Application"
rsync -a --delete --exclude tests "$REPO_DIR/server/" "$PREFIX/app/server/"
rsync -a "$REPO_DIR/scripts/" "$PREFIX/app/scripts/"
[ -d "$PREFIX/venv" ] || python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q "$PREFIX/app/server[asr,mt]" huggingface_hub
chown -R sauti:sauti "$PREFIX"

echo "==> Models (one-time download from Hugging Face; nothing is fetched at run time)"
sudo -u sauti "$PREFIX/venv/bin/python" "$PREFIX/app/scripts/download_models.py" --dest "$PREFIX/models"

echo "==> llama.cpp ($LLAMA_TAG, CPU build with runtime selection of AVX2/AVX-512 kernels)"
if [ ! -x "$PREFIX/llama/llama-server" ]; then
  curl -fsSL -o /tmp/llama.zip "https://github.com/ggml-org/llama.cpp/releases/download/$LLAMA_TAG/llama-$LLAMA_TAG-bin-ubuntu-x64.zip"
  unzip -q -o /tmp/llama.zip -d /tmp/llama
  cp -r /tmp/llama/*/* "$PREFIX/llama/" 2>/dev/null || cp -r /tmp/llama/* "$PREFIX/llama/"
  chown -R sauti:sauti "$PREFIX/llama"
fi

echo "==> Configuration"
[ -f /etc/sauti/sauti.toml ] || install -m 0640 -g sauti "$REPO_DIR/deploy/sauti.toml" /etc/sauti/sauti.toml
install -m 0644 "$REPO_DIR"/deploy/systemd/sauti.service "$REPO_DIR"/deploy/systemd/sauti-llm.service /etc/systemd/system/
if ! grep -q "reverse_proxy 127.0.0.1:8700" /etc/caddy/Caddyfile 2>/dev/null; then
  install -m 0644 "$REPO_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile
fi
install -m 0755 "$REPO_DIR/deploy/backup.sh" /usr/local/sbin/sauti-backup
echo "15 2 * * * root /usr/local/sbin/sauti-backup" > /etc/cron.d/sauti-backup

systemctl daemon-reload
systemctl enable --now sauti-llm.service sauti.service
systemctl reload caddy || systemctl restart caddy

cat <<EOF

Sauti is installed.
  1. Edit /etc/sauti/sauti.toml (public_url) and /etc/caddy/Caddyfile (hostname), then:
       systemctl restart sauti caddy
  2. Create the first administrator:
       sudo -u sauti SAUTI_CONFIG=/etc/sauti/sauti.toml $PREFIX/venv/bin/sauti-server create-user --admin <name>
  3. Open https://<your hostname>/admin
EOF
