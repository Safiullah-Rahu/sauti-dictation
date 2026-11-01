"""Run the server from a source checkout: python scripts/dev_server.py deploy/dev.toml"""
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "server"))
os.chdir(root)

from sauti_server.cli import main  # noqa: E402

if os.environ.get("SAUTI_PIN"):  # benchmarks: emulate the VM's CPU budget, e.g. SAUTI_PIN=0-11
    sys.path.insert(0, str(root / "bench"))
    from pinning import pin  # noqa: E402
    print("pinned:", pin(os.environ["SAUTI_PIN"], os.environ.get("SAUTI_PRIORITY", "high")), flush=True)

main(["--config", sys.argv[1] if len(sys.argv) > 1 else "deploy/dev-fake.toml", "run"])
