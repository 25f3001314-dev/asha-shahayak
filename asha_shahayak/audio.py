"""Convert WhatsApp voice notes (ogg/opus) to 16 kHz mono WAV for ASR."""
import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)


def to_wav_16k(audio: bytes, timeout: int = 20) -> bytes:
    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg not found; passing original audio to ASR")
        return audio
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1"],
            input=audio, capture_output=True, timeout=timeout, check=True,
        )
        return proc.stdout or audio
    except (subprocess.SubprocessError, OSError) as error:
        logger.warning("ffmpeg conversion failed: %s", type(error).__name__)
        return audio
