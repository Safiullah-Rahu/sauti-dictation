# Deployment, backup, update and recovery

Target: one Linux VM on the existing Proxmox host (Ryzen 5 9600X, 128 GB RAM, no GPU).

## 1. Create the VM

On the Proxmox host:

```bash
git clone https://github.com/<owner>/sauti-dictation.git && cd sauti-dictation
VMID=210 STORAGE=local-lvm BRIDGE=vmbr0 CORES=8 MEMORY_MB=24576 ./deploy/proxmox/create-vm.sh
```

Recommended VM shape and why:

| Setting | Value | Reason |
|---|---|---|
| CPU type | `host` | Exposes AVX2/AVX-512 to the guest. With the default CPU type, speech and LLM inference run several times slower. |
| vCPUs | 8 (of 12 host threads) | Leaves headroom for the existing guests. Raise to 10 if they are light. |
| CPU weight (`cpuunits`) | 300 (3× the PVE 8 default) | When the host is busy, the dictation VM gets CPU first. Our measurements show why this matters (see [benchmarks.md](benchmarks.md)). |
| Memory | 24 GB, ballooning off | Models use ~8 GB; fixed memory keeps them resident. |
| Disk | 80 GB on NVMe | Models ~6–12 GB, OS, backups. |

If the host's other guests are latency-tolerant, an optional `--affinity 2-11` keeps logical
CPUs 0–1 for the host and pins the VM to the rest.

## 2. Install Sauti in the VM

```bash
ssh sauti-admin@<vm-ip>
git clone https://github.com/<owner>/sauti-dictation.git && cd sauti-dictation
sudo ./deploy/install.sh
```

The installer creates a `sauti` service account, a virtualenv in `/opt/sauti`, downloads the
models once (`scripts/download_models.py`), installs the llama.cpp CPU build, writes
`/etc/sauti/sauti.toml`, enables `sauti-llm.service` and `sauti.service`, configures Caddy and
schedules a nightly backup.

Then:

1. Set `public_url` in `/etc/sauti/sauti.toml` and the hostname in `/etc/caddy/Caddyfile`.
2. `sudo systemctl restart sauti caddy`
3. Create the first administrator:
   ```bash
   sudo -u sauti SAUTI_CONFIG=/etc/sauti/sauti.toml /opt/sauti/venv/bin/sauti-server create-user --admin alice
   ```
4. Open `https://<hostname>/admin`, create user accounts, and share the installers.

## 3. TLS and DNS

* **LAN only (default):** add a DNS record (router or internal DNS) such as
  `dictate.example.lan → <vm-ip>`. Caddy's `tls internal` issues a certificate from its own CA.
  Install the CA root certificate on devices once (path in the Caddyfile), or give its path in
  the desktop app's *Custom CA* field. Android needs it installed as a user certificate.
* **Public name:** point an internet DNS name at the office IP, forward 80/443 to the VM,
  remove `tls internal`. Caddy obtains and renews a Let's Encrypt certificate.

## 4. Remote access

Preferred: a WireGuard VPN (on the router, the Proxmox host, or a self-hosted Headscale).
Remote laptops and phones then use the LAN hostname and nothing is exposed publicly. See
[privacy-security.md](privacy-security.md) for the trade-offs.

Firewall inside the VM (if you expose it publicly):

```bash
sudo apt install ufw && sudo ufw default deny incoming && sudo ufw allow 22/tcp && sudo ufw allow 80,443/tcp && sudo ufw enable
```

## 5. Backups

`/usr/local/sbin/sauti-backup` runs nightly at 02:15 (cron). It writes
`/var/backups/sauti/sauti-<timestamp>.tar.gz` containing a consistent online copy of the
database and `/etc/sauti`, and keeps 14 days. To encrypt archives, install `age`, create a key
pair on a separate machine and put the public key in `/etc/default/sauti-backup`:

```bash
AGE_RECIPIENT=age1...
DEST=/var/backups/sauti
KEEP_DAYS=14
```

Also include the VM in your Proxmox backup schedule (vzdump to Proxmox Backup Server or NFS);
that gives whole-VM restore points in addition to the application backup.

## 6. Restore

On a fresh VM installed with `deploy/install.sh`:

```bash
sudo systemctl stop sauti
age -d -i key.txt sauti-20261101-021500.tar.gz.age > backup.tar.gz   # if encrypted
mkdir /tmp/r && tar -xzf backup.tar.gz -C /tmp/r
sudo cp /tmp/r/sauti-*.db /var/lib/sauti/sauti.db && sudo chown sauti:sauti /var/lib/sauti/sauti.db
sudo cp -a /tmp/r/etc-sauti/. /etc/sauti/
sudo systemctl start sauti
```

Users, devices, settings and encrypted history come back as they were; devices reconnect
with their existing tokens.

## 7. Update

```bash
cd ~/sauti-dictation && sudo ./deploy/update.sh
```

This takes a backup, pulls the new version, reinstalls and restarts. Clients reconnect
automatically and resend any dictation that was in flight.

**Changing a model** (no client update needed): download it (`scripts/download_models.py --set
...` or copy a CTranslate2/GGUF directory), change the path in `/etc/sauti/sauti.toml` (or
`sauti-llm.service`), and restart the service. Check `https://<hostname>/admin` → Overview →
Models.

## 8. Monitoring

* Admin dashboard → Overview: health, CPU, memory, queue depth, p50/p95 wait and run times,
  timeouts, "busy" rejections, model reachability.
* Admin dashboard → Events: failures by code and stage (no content).
* `journalctl -u sauti -u sauti-llm` for service logs.
* `GET /v1/health` for an external uptime check.

## 9. Adding a GPU later

Install the NVIDIA driver in the VM (PCI passthrough), then set `asr.compute_type = "float16"`
with device CUDA support (CTranslate2 CUDA wheels) and run llama.cpp's CUDA build. No client
changes. See [models.md](models.md) for when this is worth it.
