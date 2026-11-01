#!/usr/bin/env bash
# Create the Sauti VM on the Proxmox host (run as root on the host).
#
#   VMID=210 STORAGE=local-lvm BRIDGE=vmbr0 ./create-vm.sh
#
# Choices that matter for speech workloads:
#   --cpu host      exposes AVX2/AVX-512 to the guest. The default kvm64/x86-64-v2 CPU types hide
#                   them, and CTranslate2 / llama.cpp then run several times slower.
#   --cpuunits      gives this VM a larger share of CPU time than other guests when the host is
#                   busy, so dictation stays responsive (PVE 8 / cgroup v2: default 100).
#   --balloon 0     fixed memory: models stay resident instead of being reclaimed.
#   --affinity      optional: keep a core or two for the host and other guests.
#
# GPU: set GPU_PCI to the card's PCI address (from `lspci -nn | grep -i nvidia`, e.g. 0000:01:00) after
# enabling IOMMU and vfio on the host (docs/deployment.md, "GPU"). The VM then uses the q35 machine
# type with the card passed through as a PCIe device (all functions, so the card's audio goes too).
set -euo pipefail

VMID="${VMID:-210}"
NAME="${NAME:-sauti}"
STORAGE="${STORAGE:-local-lvm}"
BRIDGE="${BRIDGE:-vmbr0}"
CORES="${CORES:-8}"
MEMORY_MB="${MEMORY_MB:-24576}"
DISK="${DISK:-80G}"
CPUUNITS="${CPUUNITS:-300}"
GPU_PCI="${GPU_PCI:-}"
IMG=debian-12-genericcloud-amd64.qcow2
IMG_URL="https://cloud.debian.org/images/cloud/bookworm/latest/$IMG"

cd /var/lib/vz/template
[ -f "$IMG" ] || wget -q -O "$IMG" "$IMG_URL"

qm create "$VMID" --name "$NAME" --ostype l26 \
  --cores "$CORES" --cpu host --numa 0 --cpuunits "$CPUUNITS" \
  --memory "$MEMORY_MB" --balloon 0 \
  --net0 virtio,bridge="$BRIDGE" --scsihw virtio-scsi-single --agent enabled=1
qm importdisk "$VMID" "$IMG" "$STORAGE" >/dev/null
qm set "$VMID" --scsi0 "$STORAGE:vm-$VMID-disk-0,iothread=1,discard=on,ssd=1" --boot order=scsi0
qm resize "$VMID" scsi0 "$DISK"
qm set "$VMID" --ide2 "$STORAGE:cloudinit" --ciuser sauti-admin --ipconfig0 ip=dhcp \
  --sshkeys /root/.ssh/authorized_keys
# Optional: qm set "$VMID" --affinity 2-11   # leave logical CPUs 0-1 to the host
if [ -n "$GPU_PCI" ]; then
  # q35 is required for PCIe passthrough; the default SeaBIOS firmware is fine for a compute GPU.
  qm set "$VMID" --machine q35 --hostpci0 "$GPU_PCI,pcie=1"
  echo "GPU $GPU_PCI passed through. In the VM: install the NVIDIA driver, then: sudo GPU=1 ./deploy/install.sh"
fi
qm start "$VMID"
echo "VM $VMID started. SSH in as sauti-admin, clone the repository and run: sudo ./deploy/install.sh"
