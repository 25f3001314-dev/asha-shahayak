"""Convert WhatsApp voice notes (ogg/opus) to 16 kHz mono WAV for ASR."""
import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)

class AudioTooShortError(ValueError):
    """Raised when an audio clip is too short to send to ASR."""


def to_wav_16k(audio: bytes, timeout: int = 20) -> bytes:
    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg not found; ASR preprocessing is unavailable")
        return audio
    try:
        return _convert_to_wav_16k(audio, timeout)
    except (subprocess.SubprocessError, OSError, ValueError):
        logger.warning("audio conversion failed; retaining original audio")
        return audio


def _convert_to_wav_16k(audio: bytes, timeout: int = 20) -> bytes:
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1"],
            input=audio, capture_output=True, timeout=timeout, check=True,
        )
        if not proc.stdout:
            raise ValueError("ffmpeg returned empty WAV audio")
        return proc.stdout
    except (subprocess.SubprocessError, OSError) as error:
        logger.warning("ffmpeg audio conversion failed: %s", type(error).__name__)
        raise ValueError("invalid or unsupported audio") from error


def audio_duration_seconds(audio: bytes, timeout: int = 10) -> float:
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe is required for audio duration checks")
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", "-i", "pipe:0",
            ],
            input=audio,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
        return float(proc.stdout.strip())
    except (ValueError, subprocess.SubprocessError, OSError) as error:
        logger.warning("audio duration check failed: %s", type(error).__name__)
        raise ValueError("unable to read audio duration") from error


def prepare_for_asr(audio: bytes, minimum_seconds: float = 1.5) -> bytes:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required for WhatsApp audio preprocessing")
    wav = _convert_to_wav_16k(audio)
    duration = audio_duration_seconds(wav)
    if duration < minimum_seconds:
        raise AudioTooShortError("AUDIO_TOO_SHORT")
    return wav


def to_ogg_opus(audio: bytes, timeout: int = 20) -> bytes | None:
    """Convert any audio to OGG/Opus for WhatsApp voice notes."""
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-nostdin", "-loglevel", "error", "-i", "pipe:0",
                "-c:a", "libopus", "-b:a", "24k", "-ar", "16000", "-ac", "1",
                "-f", "ogg", "pipe:1",
            ],
            input=audio,
            capture_output=True,
            timeout=timeout,
            check=True,
        )
        return proc.stdout or None
    except (subprocess.SubprocessError, OSError) as error:
        logger.warning("ffmpeg voice conversion failed: %s", type(error).__name__)
        return None
