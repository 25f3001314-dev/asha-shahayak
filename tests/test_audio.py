import io
import shutil
import subprocess
import wave

import pytest

from asha_shahayak import audio


def test_no_ffmpeg_returns_original(monkeypatch):
    monkeypatch.setattr(audio.shutil, "which", lambda _: None)
    assert audio.to_wav_16k(b"abc") == b"abc"


def test_bad_audio_falls_back():
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    assert audio.to_wav_16k(b"not audio") == b"not audio"


def test_real_conversion_gives_16k_mono_wav():
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    src = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-ar", "8000", "-ac", "2", "-f", "wav", "pipe:1"],
        capture_output=True, check=True,
    ).stdout
    out = audio.to_wav_16k(src)
    with wave.open(io.BytesIO(out)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
