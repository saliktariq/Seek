import sys

with open("seek/core/journal.py", "r") as f:
    code = f.read()

if "TYPE_CHECKING" not in code:
    code = code.replace(
        "from typing import Callable",
        "from typing import Callable, TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from seek.core.engine import DownloadEvent"
    )

with open("seek/core/journal.py", "w") as f:
    f.write(code)

