# SEEK

SEEK is a colourful Python desktop application for saving audio from one or
more YouTube video, playlist, or channel links — or Spotify track, album, or
playlist links. Paste one link per line; every downloaded track gets its own
folder:

```text
Track title [source ID]/
├── audio.mp3
├── thumbnail.jpg
└── info.txt
```

`info.txt` contains the track's title and description. Each input line can be
a YouTube video, playlist, or channel link, or a Spotify track, album, or
playlist link. A direct video URL downloads only that video, even when the
copied URL also contains a playlist query. Explicit playlist and channel URLs
download all available videos. Spotify links are resolved to matching YouTube
audio — see [Spotify links](#spotify-links) below.

## Interface

SEEK presents the queue, destination, progress, and activity log in one
dashboard. The live cards show how many unique links are queued, how many new
videos were saved, and how many completed videos were skipped.

**New & Improved Features:**
- **Drag & Drop:** Simply drag URLs from your browser directly into the link box.
- **Auto-Theming:** Automatically matches your operating system's Dark or Light mode.
- **Recent Destinations:** Remembers your last used download folders and window state across sessions.
- **Link Management:** Import and export your queue to text files from the File menu.
- **Bandwidth Controls:** Limit your download speed via the Options menu.
- **Desktop Notifications:** Get alerted the moment your batch download finishes.

The File, Edit, Download, View, and Help menus provide access to the main
actions. Useful keyboard shortcuts include:

- `Ctrl+V` — paste links
- `Ctrl+Enter` — start downloading
- `Esc` — cancel the active job
- `Ctrl+L` — focus the link box
- `Ctrl+O` — open the destination folder
- `Ctrl+N` — start a new session

Downloads are resumable and idempotent for the selected destination. Before
downloading an item, the app checks its YouTube video ID against the completed
folders in that output directory. A folder is skipped only when `audio.mp3`,
`thumbnail.jpg`, and `info.txt` are all present and non-empty. Partial folders
are processed again.

The destination contains a small `.youtube-audio-completed.json` journal. For
each video it is updated atomically through three durable phases:

- `downloaded` — the source audio download finished
- `converted` — a non-empty `audio.mp3` exists
- `complete` — the MP3, thumbnail, and information file all validate

Only a filesystem-validated `complete` entry is skipped. A `downloaded` or
`converted` entry is resumed so yt-dlp can reuse the existing media and finish
the missing work. If a file fails FFmpeg conversion 3 times, it is marked as skipped to prevent infinite retry loops. If the journal is missing, the app rebuilds it automatically
from existing folders. Version 1 completion indexes are migrated automatically.

## Spotify links

Spotify's own streams are DRM-protected, so SEEK cannot and does not download
audio directly from Spotify. Instead it reads public track/playlist metadata
(title, artist, album, duration) and downloads the closest-matching audio from
YouTube through the same pipeline used for YouTube links — same per-track
folder, same resumable completion tracking. SEEK uses multi-threaded concurrent resolution and aggressive caching to map massive Spotify playlists to YouTube URLs in seconds. Matching uses each track's
duration to avoid obviously wrong results, but an occasional mismatch is
possible; check the Activity log if a track sounds wrong.

- **A single track link** (`open.spotify.com/track/...` or a `spotify:track:…`
  URI) works with no setup, using Spotify's public oEmbed and embed-page data.
  Because that page's artist metadata is best-effort and undocumented on
  Spotify's side, match quality for a bare track link can be lower than an
  album or playlist link.
- **Album and playlist links** (`open.spotify.com/album/...` or
  `.../playlist/...`) need the official Spotify Web API to list every track,
  which requires a free Client ID and Client Secret:
  1. Sign in at the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
     and create an app (any name/description; any placeholder redirect URI,
     e.g. `http://127.0.0.1:9090/callback`, works since SEEK never logs a user
     in — it only reads public catalog data).
  2. Copy the app's Client ID and Client Secret.
  3. In SEEK, open **File → Spotify settings…** and paste them in, or set the
     `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` environment variables
     instead.

  Credentials are saved locally in `~/.config/seek/spotify.json` on
  Linux/macOS or `%APPDATA%\SEEK\spotify.json` on Windows (permissions
  restricted to your user) — they are never committed to the app or sent
  anywhere besides Spotify's own API.

Local-only tracks in a playlist (ones without a real Spotify catalog entry)
are skipped, since there is nothing to match on YouTube.

## Requirements

- Python 3.10 or newer
- [FFmpeg and FFprobe](https://ffmpeg.org/download.html) available on `PATH`
- A supported JavaScript runtime. `requirements.txt` installs
  [Deno](https://deno.com/) 2.3+ automatically. Node.js 22+, Bun 1.2.11+,
  QuickJS 2023-12-09+, and current QuickJS-ng releases are also supported.
- (Optional) A free Spotify Client ID/Secret to download Spotify albums or
  playlists — see [Spotify links](#spotify-links).

`yt-dlp`, Deno, and the Python dependencies are installed by
`requirements.txt`. FFmpeg means the actual command-line programs, not the
similarly named Python package.

## One-command install

The installers are safe to run again whenever SEEK needs updating or
repairing. They install system requirements when necessary, create a private
Python environment, install SEEK in a stable per-user folder, add launch
shortcuts, run a health check, and open the application.

### Windows PowerShell

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-windows.ps1
```

The default installation folder is `%LOCALAPPDATA%\SEEK`. A SEEK shortcut is
added to both the Desktop and Start Menu. The command above deliberately uses
`-ExecutionPolicy Bypass` for this process only, so virtual-environment
activation is not required.

### Linux

```bash
bash ./install-linux.sh
```

The default installation folder is
`${XDG_DATA_HOME:-$HOME/.local/share}/seek`. The installer adds SEEK to the
desktop application menu and creates a `seek` command in `~/.local/bin`.
Supported package managers are APT, DNF, YUM, Pacman, Zypper, and APK.

Both installers launch SEEK when they finish. Use `-NoLaunch` on Windows or
`--no-launch` on Linux to suppress that. Each installer also supports a custom
installation folder and options for skipping system packages or shortcuts;
run it with `Get-Help .\install-windows.ps1 -Detailed` or
`bash ./install-linux.sh --help` for details.

### Manual install

If automatic system-package installation is not appropriate, first install
Python 3.10+, Tk, FFmpeg, and FFprobe, then use:

On Windows:

```powershell
python -m venv .app-venv
.\.app-venv\Scripts\python.exe -m pip install -r requirements.txt
.\.app-venv\Scripts\python.exe app.py
```

On Linux or macOS:

```bash
python3 -m venv .app-venv
./.app-venv/bin/python -m pip install -r requirements.txt
./.app-venv/bin/python app.py
```

## Notes

- Active and upcoming livestreams are skipped because they do not have a
  finite file to convert.
- Private, deleted, region-blocked, or otherwise unavailable playlist items
  are skipped; the activity box reports errors and warnings.
- Cancelling is cooperative. A conversion already running in FFmpeg may take a
  moment to stop, and a resumable `.part` file may remain.
- YouTube changes frequently. If downloads stop working, update with:

  ```text
  python -m pip install -U --pre "yt-dlp[default]"
  ```
- Spotify's key-free, single-track path scrapes a public but undocumented
  page, so it can break if Spotify changes that page; the album/playlist path
  (official Web API) is the more stable option.

Download only content that you own or have permission to use, and in
accordance with YouTube's and Spotify's terms of service.
