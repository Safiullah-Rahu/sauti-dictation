#!/usr/bin/env bash
# Nightly backup of everything that cannot be re-downloaded: the database and the config.
# Models are not backed up (scripts/download_models.py restores them).
#
# Set AGE_RECIPIENT (an age public key) in /etc/default/sauti-backup to encrypt archives.
# Transcript history and personal dictionaries are already encrypted per user inside the
# database; archive encryption additionally protects account metadata and usage records.
set -euo pipefail
[ -f /etc/default/sauti-backup ] && . /etc/default/sauti-backup
DEST="${DEST:-/var/backups/sauti}"
KEEP_DAYS="${KEEP_DAYS:-14}"
TS="$(date +%Y%m%d-%H%M%S)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

install -d -m 0700 "$DEST"
SAUTI_CONFIG=/etc/sauti/sauti.toml /opt/sauti/venv/bin/sauti-server backup --dest "$WORK" >/dev/null
cp -a /etc/sauti "$WORK/etc-sauti"
tar -C "$WORK" -czf "$DEST/sauti-$TS.tar.gz" .
if [ -n "${AGE_RECIPIENT:-}" ]; then
  age -r "$AGE_RECIPIENT" -o "$DEST/sauti-$TS.tar.gz.age" "$DEST/sauti-$TS.tar.gz"
  rm -f "$DEST/sauti-$TS.tar.gz"
fi
find "$DEST" -name 'sauti-*.tar.gz*' -mtime +"$KEEP_DAYS" -delete
echo "backup written to $DEST"
