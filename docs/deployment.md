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

## 9. GPU

The server, the installer and the dashboard support an NVIDIA GPU. With `device = "auto"` (the
default) the speech models use the GPU as soon as one is visible and fall back to the CPU
otherwise, so a GPU can be added without changing the clients or the configuration.

**What a GPU changes.** Whisper runs in float16 on the GPU (the full large-v3 model becomes
affordable), live previews come from turbo instead of small, the cleanup LLM runs entirely on
the GPU (`-ngl 99`), and more dictations run in parallel. `deploy/sauti-gpu.toml` is the profile.

**Card.** 16–24 GB of VRAM is comfortable: Whisper large-v3 (fp16, ~3.5 GB) + turbo (~1.8 GB) +
Qwen3-4B (~3 GB) fit in 16 GB with room for parallel work; a 24 GB card can also run
Qwen3-30B-A3B (`scripts/download_models.py --set llm-qwen3-30b`) for stronger cleanup and
translation. Low-profile options: RTX 4000 SFF Ada (20 GB, 70 W). Check the server has a free PCIe
x16 slot, airflow and power-supply headroom.

### 9.1 Pass the GPU through (Proxmox host)

```bash
# 1. IOMMU on (AMD): add to GRUB_CMDLINE_LINUX_DEFAULT in /etc/default/grub, then update-grub
amd_iommu=on iommu=pt
# 2. vfio modules
printf 'vfio
vfio_iommu_type1
vfio_pci
' >> /etc/modules
# 3. keep the host's own drivers off the card
printf 'blacklist nouveau
blacklist nvidia
' > /etc/modprobe.d/blacklist-gpu.conf
update-initramfs -u -k all && reboot
# 4. find the card and create (or update) the VM
lspci -nn | grep -i nvidia             # e.g. 01:00.0 VGA ... and 01:00.1 Audio ...
GPU_PCI=0000:01:00 VMID=210 ./deploy/proxmox/create-vm.sh
#    existing VM instead: qm set 210 --machine q35 --hostpci0 0000:01:00,pcie=1
```

The GPU is then dedicated to this VM; other guests cannot use it at the same time.

### 9.2 Inside the VM

```bash
# Debian 12: enable contrib/non-free, then
sudo apt install -y linux-headers-amd64 nvidia-driver && sudo reboot
# (Ubuntu 24.04: sudo ubuntu-drivers install && sudo reboot)
nvidia-smi                              # the card must be listed
sudo GPU=1 ./deploy/install.sh          # CUDA libraries, large-v3, GPU llama.cpp, GPU profile
sudo -u sauti SAUTI_CONFIG=/etc/sauti/sauti.toml $(cat /etc/sauti/gpu.env) /opt/sauti/venv/bin/sauti-server doctor
```

`GPU=1` installs cuBLAS and cuDNN for CTranslate2 from PyPI (no system CUDA toolkit needed),
writes `/etc/sauti/gpu.env` and `/etc/sauti/llm.env` (`-ngl 99 --parallel 4`), installs
llama.cpp's prebuilt Vulkan build (or `LLAMA_BACKEND=cuda` to compile the CUDA build), downloads
Whisper large-v3 and, on a fresh install, uses `deploy/sauti-gpu.toml`. On an existing install,
set `device = "auto"` (or `"cuda"`), `compute_type = "auto"` and raise `workers` in
`/etc/sauti/sauti.toml` yourself.

`sauti-server doctor` prints the GPUs CTranslate2 can see, the device and compute type each model
will use, and whether the model files and the LLM server are reachable. The admin dashboard's
Server card shows GPU utilisation, memory and temperature.

`device = "cuda"` (as in the GPU profile) refuses to start if the GPU disappears, which makes a
driver problem obvious; `device = "auto"` keeps the service up on the CPU instead.
