"""watch_video: JARVIS watches a video (a YouTube or other link, or a file on this computer).

The video is downloaded at 480p (yt-dlp), pictures are taken at evenly spaced moments (ffmpeg),
and what is said comes from the captions, or from speech recognition when there are none. The model
gets the pictures with their timestamps plus the transcript, which is how it "watches". Nothing is
kept afterwards: everything is in a temporary folder.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

MAX_FRAMES = 20
TRANSCRIPT_CHARS = 20_000
LISTEN_SECONDS = 600  # speech recognition when there are no captions: the first 10 minutes
FRAME_WIDTH = 768


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg  # ships its own ffmpeg on every OS
    return imageio_ffmpeg.get_ffmpeg_exe()


def stamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def parse_vtt(text: str) -> list[tuple[float, str]]:
    """Caption cues as (start seconds, text), with the repeats of YouTube's rolling captions removed."""
    cues, start, seen = [], None, set()
    for line in text.splitlines():
        m = re.match(r"(?:(\d+):)?(\d+):(\d+)[.,](\d+)\s+-->", line)
        if m:
            h, mi, s, ms = m.groups()
            start = int(h or 0) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if start is None or not line or line in seen or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        seen.add(line)
        cues.append((start, line))
    return cues


async def _run(*args, timeout: float = 300) -> tuple[int, bytes, str]:
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"{Path(args[0]).name} took longer than {timeout:.0f} seconds")
    return proc.returncode, out, err.decode(errors="replace")


async def _duration(path: Path) -> float:
    _, _, err = await _run(ffmpeg(), "-hide_banner", "-i", str(path), timeout=60)  # prints the length, then fails
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0


async def _download(url: str, folder: Path, progress) -> tuple[Path | None, dict, list[tuple[float, str]], str]:
    """The video file (None if only the details and captions could be fetched), its details, its captions."""
    await progress("Downloading the video…")
    _, _, err = await _run(
        sys.executable, "-m", "yt_dlp", "-q", "--no-warnings", "--ignore-errors", "--no-playlist",
        "-f", "b[height<=480]/bv*[height<=480]+ba/b", "--ffmpeg-location", ffmpeg(),
        "--write-subs", "--write-auto-subs", "--sub-langs", "en", "--sub-format", "vtt", "--write-info-json",
        "-P", str(folder), "-o", "video.%(ext)s", "--", url, timeout=600)
    info = {}
    if (folder / "video.info.json").exists():
        info = json.loads((folder / "video.info.json").read_text(encoding="utf-8"))
    subs = next(folder.glob("video*.vtt"), None)
    cues = parse_vtt(subs.read_text(encoding="utf-8", errors="replace")) if subs else []
    video = next((p for p in folder.glob("video.*") if p.suffix not in (".json", ".vtt", ".part", ".ytdl")), None)
    if not video and not info:
        raise RuntimeError("couldn't open that link: " + (err.strip().splitlines() or ["unknown error"])[-1][:300])
    return video, info, cues, err


async def _listen(path: Path, start: float, end: float) -> list[tuple[float, str]]:
    """Speech recognition of the soundtrack (Google's free recogniser, as for voice commands)."""
    from .voice import recognize
    length = min(end - start, LISTEN_SECONDS)
    code, raw, _ = await _run(ffmpeg(), "-v", "error", "-ss", str(start), "-t", str(length), "-i", str(path),
                              "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-", timeout=300)
    if code or len(raw) < 16000 * 2:
        return []
    text = await asyncio.to_thread(recognize, raw)
    return [(start, text)] if text else []


async def watch(source: str, frames: int = 10, start: float | None = None, end: float | None = None,
                progress=None) -> list[dict]:
    """Tool result content: details, timestamped pictures and the transcript."""
    async def note(text):
        if progress:
            await progress(text)
    source = source.strip()
    frames = max(1, min(int(frames or 10), MAX_FRAMES))
    with tempfile.TemporaryDirectory(prefix="jarvis-video-") as tmp:
        folder = Path(tmp)
        if re.match(r"https?://", source, re.I):
            video, info, cues, err = await _download(source, folder, note)
        else:
            video, info, cues, err = Path(source).expanduser(), {}, [], ""
            if not video.is_file():
                raise FileNotFoundError(f"no video file at {video}")
        length = (await _duration(video) if video else 0.0) or float(info.get("duration") or 0)
        a = max(0.0, float(start or 0))
        b = min(float(end), length) if end and length else (float(end) if end else length)
        if length and a >= length:
            raise ValueError(f"start is past the end of the video ({stamp(length)})")

        head = [f"Video: {info.get('title') or Path(source).name}"]
        if info.get("uploader"):
            head.append(f"Channel: {info['uploader']}")
        if length:
            head.append(f"Length: {stamp(length)}" + (f" · watching {stamp(a)} to {stamp(b)}" if start or end else ""))
        if info.get("chapters"):
            head.append("Chapters: " + "; ".join(f"{stamp(c['start_time'])} {c['title']}" for c in info["chapters"][:40]))
        if info.get("description"):
            head.append("Description: " + info["description"][:1500])
        content = [{"type": "text", "text": "\n".join(head)}]

        if video and b > a:
            await note(f"Watching: taking {frames} pictures…")
            for i in range(frames):
                t = a + (b - a) * (i + 0.5) / frames
                pic = folder / f"frame{i}.jpg"
                await _run(ffmpeg(), "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                           "-vf", f"scale='min({FRAME_WIDTH},iw)':-2", "-q:v", "5", "-y", str(pic), timeout=60)
                if pic.exists():
                    content += [{"type": "text", "text": f"Frame at {stamp(t)}"},
                                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                             "data": base64.b64encode(pic.read_bytes()).decode()}}]
        else:
            content.append({"type": "text", "text": "(The video itself couldn't be downloaded, so there are no "
                                                    "pictures; only its details and captions.)"
                            + (f" yt-dlp said: {err.strip().splitlines()[-1][:200]}" if err.strip() else "")})

        heard = "captions"
        if not cues and video:
            await note("Listening to the soundtrack…")
            cues, heard = await _listen(video, a, b), "speech recognition"
        lines = [f"[{stamp(t)}] {text}" for t, text in cues if a - 5 <= t <= (b or t) + 1]
        transcript = "\n".join(lines)
        if len(transcript) > TRANSCRIPT_CHARS:
            transcript = transcript[:TRANSCRIPT_CHARS] + "\n[… transcript cut short; watch a later part with start/end]"
        content.append({"type": "text", "text": f"Transcript ({heard}):\n{transcript}" if transcript
                        else "Transcript: nothing was said, or it couldn't be made out."})
        return content
