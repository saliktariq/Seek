from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class DownloadConfig:
    """User preferences for downloads."""
    audio_format: str = "mp3"
    audio_quality: str = "192"
    bandwidth_limit: str = "Unlimited"
    recent_destinations: List[str] = field(default_factory=list)
    geometry: str = ""
