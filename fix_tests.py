import sys

with open("tests/test_downloader.py", "r") as f:
    code = f.read()

# Make the track ID unique for each test so the cache doesn't bleed over.
code = code.replace(
    "track = spotify.SpotifyTrack(\n            id=\"test_id\",",
    "track = spotify.SpotifyTrack(\n            id=\"test_id_first\","
)

code = code.replace(
    "track = spotify.SpotifyTrack(\n            id=\"test_id\",",
    "track = spotify.SpotifyTrack(\n            id=\"test_id_closest\","
)

with open("tests/test_downloader.py", "w") as f:
    f.write(code)

