import sys

with open("seek/core/engine.py", "r") as f:
    code = f.read()

# Add _SPOTIFY_TO_YOUTUBE_CACHE
if "_SPOTIFY_TO_YOUTUBE_CACHE" not in code:
    code = code.replace(
        "_DURATION_TOLERANCE_MIN_S = 5.0",
        "_SPOTIFY_TO_YOUTUBE_CACHE: dict[str, str] = {}\n\n_DURATION_TOLERANCE_MIN_S = 5.0"
    )

# Update search_youtube_for_track to use cache
search_fn_old = """def search_youtube_for_track(
    track: spotify.SpotifyTrack,
    *,
    javascript_runtimes: dict[str, dict[str, str]] | None = None,
    result_count: int = 5,
) -> str | None:
    \"\"\"Return the URL of the best available YouTube match for a Spotify track.\"\"\"

    import yt_dlp"""
search_fn_new = """def search_youtube_for_track(
    track: spotify.SpotifyTrack,
    *,
    javascript_runtimes: dict[str, dict[str, str]] | None = None,
    result_count: int = 5,
) -> str | None:
    \"\"\"Return the URL of the best available YouTube match for a Spotify track.\"\"\"

    if track.spotify_id in _SPOTIFY_TO_YOUTUBE_CACHE:
        return _SPOTIFY_TO_YOUTUBE_CACHE[track.spotify_id]

    import yt_dlp"""
if "if track.spotify_id in _SPOTIFY_TO_YOUTUBE_CACHE" not in code:
    code = code.replace(search_fn_old, search_fn_new)

# Cache result in search_youtube_for_track
cache_res_old = """    video_id = best.get("id")
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return best.get("url")"""
cache_res_new = """    video_id = best.get("id")
    if video_id:
        url = f"https://www.youtube.com/watch?v={video_id}"
    else:
        url = best.get("url")
    if url and track.spotify_id:
        _SPOTIFY_TO_YOUTUBE_CACHE[track.spotify_id] = url
    return url"""
if "_SPOTIFY_TO_YOUTUBE_CACHE[track.spotify_id] = url" not in code:
    code = code.replace(cache_res_old, cache_res_new)

# Concurrency in parse_url_entries
concurrency_old = """        for index, track in enumerate(tracks, start=1):
            if cancel_event.is_set():
                raise UserCancelledError("Download cancelled.")

            prefix = f"[{index}/{len(tracks)}]"
            callback(
                DownloadEvent(
                    "processing",
                    f'{prefix} Matching "{track.display_name}" on YouTube…',
                )
            )
            try:
                match = search_youtube_for_track(
                    track,
                    javascript_runtimes=javascript_runtimes,
                )
            except Exception as exc:
                callback(
                    DownloadEvent(
                        "warning",
                        f'{prefix} Could not search YouTube for '
                        f'"{track.display_name}": {exc}',
                    )
                )
                continue

            if match is None:
                callback(
                    DownloadEvent(
                        "warning",
                        f'{prefix} No YouTube match found for '
                        f'"{track.display_name}"',
                    )
                )
                continue

            callback(
                DownloadEvent(
                    "log",
                    f'{prefix} Matched "{track.display_name}" to {match}',
                )
            )
            add(match)"""

concurrency_new = """        def process_track(index: int, track: spotify.SpotifyTrack) -> str | None:
            if cancel_event.is_set():
                return None
                
            prefix = f"[{index}/{len(tracks)}]"
            callback(DownloadEvent("processing", f'{prefix} Matching "{track.display_name}" on YouTube…'))
            try:
                match = search_youtube_for_track(track, javascript_runtimes=javascript_runtimes)
            except Exception as exc:
                callback(DownloadEvent("warning", f'{prefix} Could not search YouTube for "{track.display_name}": {exc}'))
                return None

            if match is None:
                callback(DownloadEvent("warning", f'{prefix} No YouTube match found for "{track.display_name}"'))
                return None

            callback(DownloadEvent("log", f'{prefix} Matched "{track.display_name}" to {match}'))
            return match

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for index, track in enumerate(tracks, start=1):
                futures.append(executor.submit(process_track, index, track))
            
            for future in concurrent.futures.as_completed(futures):
                if cancel_event.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise UserCancelledError("Download cancelled.")
                match = future.result()
                if match:
                    add(match)"""

if "process_track(index: int, track:" not in code:
    code = code.replace(concurrency_old, concurrency_new)

with open("seek/core/engine.py", "w") as f:
    f.write(code)

