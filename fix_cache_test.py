import sys

with open("tests/test_downloader.py", "r") as f:
    code = f.read()

# Fix the test with id="abc"
old = """        track = spotify.SpotifyTrack(
            id="abc", title="Song", artists=(), album="", duration_ms=None
        )"""
new = """        track = spotify.SpotifyTrack(
            id="def", title="Song", artists=(), album="", duration_ms=None
        )"""

code = code.replace(old, new)

with open("tests/test_downloader.py", "w") as f:
    f.write(code)

