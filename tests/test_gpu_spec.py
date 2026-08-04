"""Tests for GPU visibility/index parsing (CUDA_VISIBLE_DEVICES / Slurm / CLI)."""
from ground_control.utils.system_metrics import _parse_visible_gpu_spec
from ground_control.main import _parse_gpu_indices


def test_parse_visible_gpu_spec_none_and_empty():
    assert _parse_visible_gpu_spec(None) is None
    assert _parse_visible_gpu_spec("") == "none"
    assert _parse_visible_gpu_spec("  ") == "none"


def test_parse_visible_gpu_spec_sentinels():
    assert _parse_visible_gpu_spec("-1") == "none"
    assert _parse_visible_gpu_spec("NoDevFiles") == "none"


def test_parse_visible_gpu_spec_indices():
    spec = _parse_visible_gpu_spec("0,1,3")
    assert spec["indices"] == {0, 1, 3}
    assert spec["uuids"] == set()


def test_parse_visible_gpu_spec_uuids_lowercased():
    spec = _parse_visible_gpu_spec("GPU-AbC123")
    assert "gpu-abc123" in spec["uuids"]


def test_parse_gpu_indices_cli():
    assert _parse_gpu_indices(None) is None
    assert _parse_gpu_indices("0") == [0]
    assert _parse_gpu_indices("0,1,2") == [0, 1, 2]
    assert _parse_gpu_indices(" 1 , 3 ") == [1, 3]


def test_parse_gpu_indices_invalid_raises():
    import click
    try:
        _parse_gpu_indices("0,x")
    except click.BadParameter:
        return
    raise AssertionError("expected BadParameter for non-integer GPU index")
