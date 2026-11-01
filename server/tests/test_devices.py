import pytest

from sauti_server import devices
from sauti_server.backends.base import BackendUnavailable
from sauti_server.config import load_config


def test_auto_uses_gpu_when_present_and_cpu_otherwise():
    assert devices.resolve_device("auto", count=1) == "cuda"
    assert devices.resolve_device("auto", count=0) == "cpu"
    assert devices.resolve_device("cpu", count=2) == "cpu"


def test_explicit_cuda_without_gpu_fails_loudly():
    with pytest.raises(BackendUnavailable, match="no CUDA GPU"):
        devices.resolve_device("cuda", count=0)
    with pytest.raises(ValueError):
        devices.resolve_device("tpu", count=0)


def test_compute_type_follows_device():
    assert devices.resolve_compute_type("auto", "cuda") == "float16"
    assert devices.resolve_compute_type("auto", "cpu") == "int8"
    assert devices.resolve_compute_type("int8_float16", "cuda") == "int8_float16"


def test_parse_nvidia_smi():
    out = "0, NVIDIA RTX 4000 SFF Ada Generation, 37, 6123, 20475, 54\n1, NVIDIA GeForce RTX 3090, [N/A], 10, 24576, 40\n"
    gpus = devices.parse_nvidia_smi(out)
    assert gpus[0]["name"].startswith("NVIDIA RTX 4000") and gpus[0]["util_percent"] == 37.0
    assert gpus[0]["memory_total_mb"] == 20475.0
    assert gpus[1]["util_percent"] is None and gpus[1]["index"] == 1


def test_env_override_selects_gpu():
    cfg = load_config(None, env={"SAUTI_ASR__DEVICE": "cuda", "SAUTI_ASR__DEVICE_INDEX": "1"})
    assert cfg.asr.device == "cuda" and cfg.asr.device_index == 1 and cfg.asr.compute_type == "auto"


def test_shipped_gpu_profile_parses():
    from pathlib import Path
    cfg = load_config(Path(__file__).resolve().parents[2] / "deploy" / "sauti-gpu.toml", env={})
    assert cfg.asr.device == "cuda" and cfg.asr.workers == 4 and cfg.llm.max_concurrency == 4


def test_overview_reports_gpu_list(client):
    from conftest import auth, web_token
    ov = client.get("/v1/admin/overview", headers=auth(web_token(client))).json()
    assert isinstance(ov["system"]["gpus"], list)
