from dataclasses import dataclass

@dataclass(frozen=True)
class DownloadConfig:
    """User preferences for downloads."""
    audio_format: str = "mp3"
    audio_quality: str = "192"
    bandwidth_limit: str = "Unlimited"
