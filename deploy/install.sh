#!/usr/bin/env bash
# Install Sauti on a fresh Debian 12 or Ubuntu 24.04 VM. Run as root from a checkout:
#   sudo ./deploy/install.sh              # CPU only
#   sudo GPU=1 ./deploy/install.sh        # NVIDIA GPU passed through to the VM (driver installed first)
# Re-running is safe: it updates the code and keeps config, data and models.
#
# GPU options:
#   LLAMA_BACKEND=vulkan (default with GPU=1) uses llama.cpp's prebuilt Vulkan build - no CUDA toolkit needed.
#   LLAMA_BACKEND=cuda builds llama.cpp from source with CUDA (needs nvidia-cuda-toolkit, a few minutes).
set -euo pipefail

PREFIX=/opt/sauti
LLAMA_TAG="${LLAMA_TAG:-b12032}"
GPU="${GPU:-0}"
LLAMA_BACKEND="${LLAMA_BACKEND:-$([ "$GPU" = 1 ] && echo vulkan || echo cpu)}"

if [ "$GPU" = 1 ] && ! command -v nvidia-smi >/dev/null; then
  echo "GPU=1 but nvidia-smi is missing: install the NVIDIA driver in this VM first (see docs/deployment.md)." >&2
  exit 1
fi
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

if [ "$GPU" = 1 ]; then
  echo "==> CUDA libraries for CTranslate2 (cuBLAS + cuDNN 9, from PyPI)"
  "$PREFIX/venv/bin/pip" install -q nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
  LIBS="$("$PREFIX/venv/bin/python" -c 'import os, nvidia.cublas.lib as a, nvidia.cudnn.lib as b; print(os.path.dirname(a.__file__) + ":" + os.path.dirname(b.__file__))')"
  echo "LD_LIBRARY_PATH=$LIBS" > /etc/sauti/gpu.env
fi

echo "==> Models (one-time download from Hugging Face; nothing is fetched at run time)"
SETS="--set asr-turbo --set asr-small --set llm-qwen3-4b"
[ "$GPU" = 1 ] && SETS="$SETS --set asr-large-v3"
sudo -u sauti "$PREFIX/venv/bin/python" "$PREFIX/app/scripts/download_models.py" --dest "$PREFIX/models" $SETS

echo "==> llama.cpp ($LLAMA_TAG, $LLAMA_BACKEND)"
if [ ! -x "$PREFIX/llama/llama-server" ] || [ "${REINSTALL_LLAMA:-0}" = 1 ]; then
  case "$LLAMA_BACKEND" in
    cpu)    ASSET="llama-$LLAMA_TAG-bin-ubuntu-x64.zip" ;;          # runtime selection of AVX2/AVX-512 kernels
    vulkan) ASSET="llama-$LLAMA_TAG-bin-ubuntu-vulkan-x64.zip"; apt-get install -y -qq libvulkan1 >/dev/null ;;
    cuda)   ASSET="" ;;
  esac
  if [ -n "$ASSET" ]; then
    curl -fsSL -o /tmp/llama.zip "https://github.com/ggml-org/llama.cpp/releases/download/$LLAMA_TAG/$ASSET"
    rm -rf /tmp/llama && unzip -q -o /tmp/llama.zip -d /tmp/llama
    cp -r /tmp/llama/*/* "$PREFIX/llama/" 2>/dev/null || cp -r /tmp/llama/* "$PREFIX/llama/"
  else
    apt-get install -y -qq git cmake build-essential nvidia-cuda-toolkit >/dev/null
    rm -rf /tmp/llama-src && git clone -q --depth 1 --branch "$LLAMA_TAG" https://github.com/ggml-org/llama.cpp /tmp/llama-src
    cmake -S /tmp/llama-src -B /tmp/llama-src/build -DGGML_CUDA=ON -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release >/dev/null
    cmake --build /tmp/llama-src/build --target llama-server -j "$(nproc)" >/dev/null
    cp /tmp/llama-src/build/bin/* "$PREFIX/llama/"
  fi
  chown -R sauti:sauti "$PREFIX/llama"
fi
if [ "$GPU" = 1 ] && [ ! -f /etc/sauti/llm.env ]; then
  echo 'LLAMA_ARGS=-ngl 99 -c 16384 --parallel 4' > /etc/sauti/llm.env
fi

echo "==> Configuration"
PROFILE="$REPO_DIR/deploy/sauti.toml"
[ "$GPU" = 1 ] && PROFILE="$REPO_DIR/deploy/sauti-gpu.toml"
[ -f /etc/sauti/sauti.toml ] || install -m 0640 -g sauti "$PROFILE" /etc/sauti/sauti.toml
install -m 0644 "$REPO_DIR"/deploy/systemd/sauti.service "$REPO_DIR"/deploy/systemd/sauti-llm.service /etc/systemd/system/
if ! grep -q "reverse_proxy 127.0.0.1:8700" /etc/caddy/Caddyfile 2>/dev/null; then
  install -m 0644 "$REPO_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile
fi
install -m 0755 "$REPO_DIR/deploy/backup.sh" /usr/local/sbin/sauti-backup
echo "15 2 * * * root /usr/local/sbin/sauti-backup" > /etc/cron.d/sauti-backup

systemctl daemon-reload
systemctl enable --now sauti-llm.service sauti.service
systemctl reload caddy || systemctl restart caddy

sudo -u sauti env SAUTI_CONFIG=/etc/sauti/sauti.toml $(cat /etc/sauti/gpu.env 2>/dev/null)   "$PREFIX/venv/bin/sauti-server" doctor || true

cat <<EOF

Sauti is installed.
  1. Edit /etc/sauti/sauti.toml (public_url) and /etc/caddy/Caddyfile (hostname), then:
       systemctl restart sauti caddy
  2. Create the first administrator:
       sudo -u sauti SAUTI_CONFIG=/etc/sauti/sauti.toml $PREFIX/venv/bin/sauti-server create-user --admin <name>
  3. Open https://<your hostname>/admin
EOF
