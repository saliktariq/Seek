import re

with open("tests/test_downloader.py", "r") as f:
    code = f.read()

# Replace the downloader import block
code = re.sub(
    r"import downloader.*?import spotify",
    """import seek.core.engine as engine
import seek.core.journal as journal
import seek.models.links as links
import seek.utils.system as system
import seek.core.spotify as spotify

from seek.utils.system import DependencyReport, format_bytes
from seek.core.engine import (
    DownloadEvent, DownloadFailedError, UserCancelledError,
    _duration_tolerance, _select_best_match,
    build_ydl_options, download_url, download_urls, write_info_file
)
from seek.models.links import (
    is_single_video_url, is_youtube_url, normalize_youtube_url,
    parse_url_entries, parse_url_list
)
from seek.core.journal import CompletionIndex""",
    code,
    flags=re.DOTALL,
)

# Replace usage of downloader.X
code = code.replace("downloader.CompletionIndex", "CompletionIndex")
code = code.replace("downloader.check_dependencies", "system.check_dependencies")
code = code.replace(
    "downloader.detect_javascript_runtimes", "system.detect_javascript_runtimes"
)
code = code.replace("downloader.expand_input_urls", "engine.expand_input_urls")
code = code.replace(
    "downloader.search_youtube_for_track", "engine.search_youtube_for_track"
)
code = code.replace("downloader.UserCancelledError", "UserCancelledError")

# Also replace patch points
code = code.replace(
    '"downloader"', '"seek.utils.system"'
)  # For check_dependencies, etc.
code = code.replace(
    'mock.patch.object(\n                    downloader,\n                    "check_dependencies"',
    'mock.patch.object(\n                    system,\n                    "check_dependencies"',
)
code = code.replace(
    'mock.patch.object(\n                    downloader,\n                    "detect_javascript_runtimes"',
    'mock.patch.object(\n                    system,\n                    "detect_javascript_runtimes"',
)

with open("tests/test_downloader.py", "w") as f:
    f.write(code)

with open("tests/test_spotify.py", "r") as f:
    scode = f.read()

scode = scode.replace("import spotify", "import seek.core.spotify as spotify")
scode = scode.replace("import downloader", "import seek.core.engine as engine")
with open("tests/test_spotify.py", "w") as f:
    f.write(scode)

print("test imports fixed")
