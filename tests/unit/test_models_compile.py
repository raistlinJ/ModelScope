"""
Tests for compile_gguf subprocess execution paths in core/models.py.

Covers subprocess success, conversion failure, quantization failure,
timeout paths, and on_log callback usage.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from core.models import compile_gguf


def _make_fake_binaries(tmp_path):
    """Create fake convert script and quantize binary."""
    src = tmp_path / "model"
    src.mkdir()
    convert = tmp_path / "convert.py"
    convert.write_text("# fake convert")
    quantize = tmp_path / "llama-quantize"
    quantize.write_text("#!/bin/sh\nexit 0")
    quantize.chmod(0o755)
    out = tmp_path / "out"
    return src, convert, quantize, out


class TestCompileGgufSubprocess:
    def test_conversion_success_then_quantize_success(self, tmp_path):
        src, convert, quantize, out = _make_fake_binaries(tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "conversion line 1\nconversion line 2"

        logs = []
        with patch("subprocess.run", return_value=mock_result):
            success, msg = compile_gguf(
                source_path=str(src),
                output_dir=str(out),
                quantization="Q4_K_M",
                convert_script=str(convert),
                quantize_bin=str(quantize),
                on_log=lambda m: logs.append(m),
            )

        assert success is True
        assert str(out) in msg
        assert any("[COMPILE]" in l for l in logs)
        assert any("[CONVERT]" in l for l in logs)

    def test_conversion_failure_returns_false(self, tmp_path):
        src, convert, quantize, out = _make_fake_binaries(tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error: model not found"

        logs = []
        with patch("subprocess.run", return_value=mock_result):
            success, msg = compile_gguf(
                source_path=str(src),
                output_dir=str(out),
                quantization="Q4_K_M",
                convert_script=str(convert),
                quantize_bin=str(quantize),
                on_log=lambda m: logs.append(m),
            )

        assert success is False
        assert "Conversion failed" in msg
        assert any("[CONVERT ERROR]" in l for l in logs)

    def test_conversion_timeout_returns_false(self, tmp_path):
        src, convert, quantize, out = _make_fake_binaries(tmp_path)

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 1800)):
            success, msg = compile_gguf(
                source_path=str(src),
                output_dir=str(out),
                quantization="Q4_K_M",
                convert_script=str(convert),
                quantize_bin=str(quantize),
            )

        assert success is False
        assert "timed out" in msg.lower()

    def test_conversion_exception_returns_false(self, tmp_path):
        src, convert, quantize, out = _make_fake_binaries(tmp_path)

        with patch("subprocess.run", side_effect=OSError("no such file")):
            success, msg = compile_gguf(
                source_path=str(src),
                output_dir=str(out),
                quantization="Q4_K_M",
                convert_script=str(convert),
                quantize_bin=str(quantize),
            )

        assert success is False
        assert "Conversion error" in msg

    def test_quantize_failure_returns_false(self, tmp_path):
        src, convert, quantize, out = _make_fake_binaries(tmp_path)

        convert_ok = MagicMock()
        convert_ok.returncode = 0
        convert_ok.stdout = ""
        convert_ok.stderr = ""

        quantize_fail = MagicMock()
        quantize_fail.returncode = 1
        quantize_fail.stdout = ""
        quantize_fail.stderr = "quantize error: bad input"

        logs = []
        with patch("subprocess.run", side_effect=[convert_ok, quantize_fail]):
            success, msg = compile_gguf(
                source_path=str(src),
                output_dir=str(out),
                quantization="Q4_K_M",
                convert_script=str(convert),
                quantize_bin=str(quantize),
                on_log=lambda m: logs.append(m),
            )

        assert success is False
        assert "Quantization failed" in msg
        assert any("[QUANTIZE ERROR]" in l for l in logs)

    def test_quantize_timeout_returns_false(self, tmp_path):
        src, convert, quantize, out = _make_fake_binaries(tmp_path)

        convert_ok = MagicMock()
        convert_ok.returncode = 0
        convert_ok.stdout = ""
        convert_ok.stderr = ""

        with patch("subprocess.run",
                   side_effect=[convert_ok,
                                 subprocess.TimeoutExpired("cmd", 3600)]):
            success, msg = compile_gguf(
                source_path=str(src),
                output_dir=str(out),
                quantization="Q4_K_M",
                convert_script=str(convert),
                quantize_bin=str(quantize),
            )

        assert success is False
        assert "Quantization timed out" in msg

    def test_quantize_exception_returns_false(self, tmp_path):
        src, convert, quantize, out = _make_fake_binaries(tmp_path)

        convert_ok = MagicMock()
        convert_ok.returncode = 0
        convert_ok.stdout = ""
        convert_ok.stderr = ""

        with patch("subprocess.run", side_effect=[convert_ok, OSError("gone")]):
            success, msg = compile_gguf(
                source_path=str(src),
                output_dir=str(out),
                quantization="Q4_K_M",
                convert_script=str(convert),
                quantize_bin=str(quantize),
            )

        assert success is False
        assert "Quantization error" in msg

    def test_no_quantization_returns_f16_path(self, tmp_path):
        src, convert, quantize, out = _make_fake_binaries(tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            success, msg = compile_gguf(
                source_path=str(src),
                output_dir=str(out),
                quantization="",
                convert_script=str(convert),
                quantize_bin=str(quantize),
            )

        assert success is True
        assert "F16" in msg

    def test_on_log_none_does_not_crash(self, tmp_path):
        src, convert, quantize, out = _make_fake_binaries(tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "output"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            success, _ = compile_gguf(
                source_path=str(src),
                output_dir=str(out),
                quantization="Q4_K_M",
                convert_script=str(convert),
                quantize_bin=str(quantize),
                on_log=None,
            )
        # No exception is the assertion
