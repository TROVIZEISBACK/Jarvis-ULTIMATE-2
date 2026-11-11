"""Ears and voice.

Listening is adapted from the original J.A.R.V.I.S `takeCommand()`: energy-gated
phrase capture handed to speech_recognition's Google recognizer. Push-to-talk records
with macOS's AVAudioRecorder on a Mac (see MicRecorder) and with sounddevice on Windows
and Linux; the always-on wake mode uses sounddevice everywhere. Understanding happens in
the brain.

Speaking uses Kokoro, a local neural voice (British male by default), with the system
voice as a fallback (macOS `say`, Windows SAPI, Linux espeak-ng). Audio plays out of
process (afplay, winsound, pw-play/paplay/aplay), so it can't stutter when Python is
busy, and the loudness envelope is streamed to the UI so the 3D core pulses with the voice.
"""
import asyncio
import hashlib
import os
import re
import subprocess
import tempfile
import threading
import time
import wave

import numpy as np
import speech_recognition as sr

from . import config, plat

try:
    import sounddevice as sd
except Exception as _e:  # no PortAudio (e.g. a Linux server without libportaudio2): no local mic
    sd = None
    print(f"[voice] no audio input/output library ({_e}); the local microphone is off")

SAMPLE_RATE = 16000
CHUNK = 0.1


class MacRecorder:
    """One push-to-talk recording through AVAudioRecorder, macOS's own recorder.

    PortAudio (sounddevice) used to do this, but its macOS backend can deadlock when a
    stream is stopped: its start/stop listener and AudioOutputUnitStop take two CoreAudio
    locks in opposite orders. A quick stop (⌘S) hit it reliably, leaving the mic held and
    JARVIS deaf until restarted. AVAudioRecorder records natively, so no audio passes
    through Python while recording; we only read its level meter ten times a second."""

    def __init__(self, path: str):
        from AVFoundation import (AVAudioRecorder, AVFormatIDKey, AVLinearPCMBitDepthKey, AVLinearPCMIsBigEndianKey,
                                  AVLinearPCMIsFloatKey, AVNumberOfChannelsKey, AVSampleRateKey)
        from Foundation import NSURL
        settings = {
            AVFormatIDKey: 1819304813,  # kAudioFormatLinearPCM ('lpcm')
            AVSampleRateKey: float(SAMPLE_RATE), AVNumberOfChannelsKey: 1, AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: False, AVLinearPCMIsBigEndianKey: False,
        }
        self.path = path
        self.rec, err = AVAudioRecorder.alloc().initWithURL_settings_error_(NSURL.fileURLWithPath_(path), settings, None)
        if self.rec is None:
            raise RuntimeError(f"recorder unavailable: {err}")
        self.rec.setMeteringEnabled_(True)
        if not (self.rec.prepareToRecord() and self.rec.record()):
            raise RuntimeError("the microphone didn't start (is JARVIS allowed to use it?)")

    def level_db(self) -> float:
        """Average input level in dBFS (-160 silence ... 0 full scale)."""
        self.rec.updateMeters()
        return float(self.rec.averagePowerForChannel_(0))

    def time(self) -> float:
        return float(self.rec.currentTime())

    def stop(self):
        self.rec.stop()

    def read(self) -> tuple[np.ndarray, int]:
        import soundfile as sf
        data, rate = sf.read(self.path, dtype="int16")
        if data.ndim > 1:
            data = data[:, 0]
        return data, rate


class StreamRecorder:
    """One push-to-talk recording through sounddevice (PortAudio), for Windows and Linux.
    Same interface as MacRecorder; the audio is collected in memory by PortAudio's callback."""

    def __init__(self, path: str):
        if sd is None:
            raise RuntimeError("no audio library: install PortAudio (Linux: sudo apt install libportaudio2)")
        self.frames: list[np.ndarray] = []
        self.level = 1e-9
        self.started = time.monotonic()

        def callback(indata, frames, t, status):
            block = indata[:, 0].copy()
            self.frames.append(block)
            self.level = float(np.sqrt(np.mean(block.astype(np.float32) ** 2))) + 1e-9

        self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=callback)
        self.stream.start()

    def level_db(self) -> float:
        return 20 * float(np.log10(self.level / 32768.0))

    def time(self) -> float:
        return time.monotonic() - self.started

    def stop(self):
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass

    def read(self) -> tuple[np.ndarray, int]:
        data = np.concatenate(self.frames) if self.frames else np.zeros(0, dtype=np.int16)
        return data, SAMPLE_RATE


MicRecorder = MacRecorder if plat.MAC else StreamRecorder


class Listener(threading.Thread):
    """Microphone input.

    push mode (default): the mic stays closed until `activate()` (⌘H), then records until
    `stop()` (⌘S), closes the mic again and calls on_phrase(text).
    wake mode: the mic is always open and every phrase is passed on (the hub filters
    for the wake word).
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, on_phrase, on_level, on_state):
        super().__init__(daemon=True)
        self.loop = loop
        self.on_phrase = on_phrase
        self.on_level = on_level
        self.on_state = on_state
        self.paused = threading.Event()      # wake mode: muted while JARVIS speaks
        self.stopped = threading.Event()
        self.trigger = threading.Event()     # push mode: ⌘H pressed
        self.cancel = threading.Event()      # push mode: ⌘H pressed again while recording (discard)
        self.done = threading.Event()        # push mode: ⌘S, finish recording and send
        self.listening = False               # mic open
        self.active = False                  # mic open or the phrase still being recognised
        self.recognizer = sr.Recognizer()
        self.push = config.MIC_MODE != "wake"

    def _emit(self, fn, *args):
        self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(fn(*args)))

    def activate(self):
        """⌘H: start listening, or stop if already listening."""
        if self.listening:
            self.cancel.set()
        else:
            self.trigger.set()

    def stop(self) -> bool:
        """⌘S: finish recording and send what was said. Returns True while JARVIS is recording
        or still recognising (the key is meant for JARVIS), False otherwise (it goes to the front app)."""
        if not (self.push and self.active):
            return False
        if self.listening and not self.cancel.is_set():
            self.done.set()
        return True

    def run(self):
        if self.push:
            self._run_push()
        else:
            self._run_wake()

    # --- push to talk ---------------------------------------------------------
    def _run_push(self):
        """⌘H starts recording; it runs until ⌘S (then the speech is sent) or ⌘H again (discarded)."""
        import contextlib
        if plat.MAC:
            import objc
            pool = objc.autorelease_pool
        else:
            pool = contextlib.nullcontext
        tmpdir = tempfile.mkdtemp(prefix="jarvis-mic-")
        while not self.stopped.is_set():
            self.trigger.wait()
            self.trigger.clear()
            self.cancel.clear()
            self.done.clear()
            self.active = self.listening = True
            raw = b""
            try:
                with pool():
                    raw = self._record(os.path.join(tmpdir, "phrase.wav"))
            except Exception as e:
                self._emit(self.on_state, f"mic-error: {e}")
            self.listening = False
            if self.cancel.is_set():
                self.active = False
                chime("Bottle")
                self._emit(self.on_state, "idle")
                continue
            if not raw:
                self.active = False
                self._emit(self.on_state, "not-heard")
                continue
            chime("Pop")
            self._emit(self.on_state, "recognizing")
            text = self._recognize(raw)
            self.active = False
            if text:
                self._emit(self.on_phrase, text)
            else:
                self._emit(self.on_state, "not-heard")

    def _record(self, path: str) -> bytes:
        """Record until ⌘S (done), ⌘H again (cancel) or the length limit. Returns 16 kHz int16 audio."""
        rec = MicRecorder(path)
        try:
            self._emit(self.on_state, "listening")
            chime("Tink")
            noise = []
            for _ in range(3):  # room tone, to tell when the user starts talking (for the HUD)
                time.sleep(CHUNK)
                noise.append(rec.level_db())
            threshold = max(float(np.median(noise)) + 8.0, -40.0)
            heard = False
            while not (self.cancel.is_set() or self.done.is_set()):
                time.sleep(CHUNK)
                db = rec.level_db()
                self._emit(self.on_level, min(10 ** (db / 20) * 32768 / 4000.0, 1.0))
                if not heard and db > threshold:
                    heard = True
                    self._emit(self.on_state, "hearing")
                if rec.time() >= config.MAX_RECORDING:
                    print(f"[voice] recording reached the {config.MAX_RECORDING:.0f}s limit; sending it")
                    break
        finally:
            rec.stop()  # mic off
            self._emit(self.on_level, 0.0)
        if self.cancel.is_set():
            return b""
        data, rate = rec.read()
        if rate != SAMPLE_RATE and len(data):
            n = int(len(data) * SAMPLE_RATE / rate)
            data = np.interp(np.linspace(0, len(data) - 1, n), np.arange(len(data)), data).astype(np.int16)
        if len(data) < SAMPLE_RATE * 0.4:
            return b""
        return data.tobytes()

    # --- always-on wake word ----------------------------------------------------
    def _run_wake(self):
        chunk = int(SAMPLE_RATE * CHUNK)
        try:
            if sd is None:
                raise RuntimeError("no audio library (Linux: sudo apt install libportaudio2)")
            stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16")
            stream.start()
        except Exception as e:  # no mic / permission denied
            self._emit(self.on_state, f"mic-error: {e}")
            return

        noise = 300.0
        frames, started, silent, last_level = [], False, 0.0, 0.0
        while not self.stopped.is_set():
            block, _ = stream.read(chunk)
            if self.paused.is_set():
                frames, started, silent = [], False, 0.0
                continue
            level = float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))
            now = time.time()
            if now - last_level > 0.1:
                self._emit(self.on_level, min(level / 4000.0, 1.0))
                last_level = now
            threshold = max(noise * 2.5, 350)
            if not started:
                noise = noise * 0.95 + level * 0.05  # track ambient noise
                if level > threshold:
                    started, silent, frames = True, 0.0, [block.copy()]
                    self._emit(self.on_state, "hearing")
                continue
            frames.append(block.copy())
            silent = silent + CHUNK if level <= threshold else 0.0
            if silent >= 0.9 or len(frames) * CHUNK >= 15:
                raw = np.concatenate(frames).tobytes()
                frames, started, silent = [], False, 0.0
                if len(raw) / 2 / SAMPLE_RATE < 0.5:
                    continue
                self._emit(self.on_state, "recognizing")
                text = self._recognize(raw)
                if text:
                    self._emit(self.on_phrase, text)
                else:
                    self._emit(self.on_state, "idle")
        stream.stop()

    def _recognize(self, raw: bytes) -> str:
        return recognize(raw)


def recognize(raw: bytes) -> str:
    """16 kHz int16 speech to text. Google's free recogniser takes about a minute of audio per
    request, so long recordings are cut at pauses into pieces of up to 45 s, recognised side by side."""
    pieces = split_at_pauses(np.frombuffer(raw, dtype=np.int16), SAMPLE_RATE)
    if len(pieces) == 1:
        return _recognize_piece(pieces[0])
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as pool:
        texts = list(pool.map(_recognize_piece, pieces))
    return " ".join(t for t in texts if t)


def _recognize_piece(data: np.ndarray) -> str:
    if len(data) < SAMPLE_RATE * 0.3:
        return ""
    try:
        audio = sr.AudioData(data.tobytes(), SAMPLE_RATE, 2)
        return sr.Recognizer().recognize_google(audio, language=config.STT_LANGUAGE).strip()
    except sr.UnknownValueError:
        return ""
    except Exception as e:
        print(f"[voice] recognition error: {e}")
        return ""


def split_at_pauses(data: np.ndarray, rate: int, max_s: float = 45.0, min_s: float = 25.0) -> list[np.ndarray]:
    """Cut audio into pieces of at most max_s seconds, each cut at the quietest 0.1 s
    between min_s and max_s, so words aren't split."""
    pieces, start, hop = [], 0, int(rate * 0.1)
    while len(data) - start > max_s * rate:
        lo, hi = start + int(min_s * rate), start + int(max_s * rate)
        seg = data[lo:hi].astype(np.float32)
        frames = len(seg) // hop
        energy = (seg[: frames * hop].reshape(frames, hop) ** 2).mean(axis=1)
        cut = lo + int(np.argmin(energy)) * hop + hop // 2
        pieces.append(data[start:cut])
        start = cut
    pieces.append(data[start:])
    return pieces


_CHIMES = {"Tink": (1320, 0.06), "Pop": (880, 0.08), "Bottle": (440, 0.14)}  # recording on / sent / cancelled
_chime_files: dict[str, str] = {}


def chime(name: str):
    """Play a short sound without blocking: a macOS system sound, or a synthesised blip elsewhere."""
    path = f"/System/Library/Sounds/{name}.aiff"
    if not (plat.MAC and os.path.exists(path)):
        path = _chime_files.get(name)
        if not path:
            freq, dur = _CHIMES.get(name, (880, 0.08))
            rate = 22050
            t = np.arange(int(rate * dur)) / rate
            wave_ = np.sin(2 * np.pi * freq * t) * np.exp(-t / (dur / 3)) * 0.35
            path = os.path.join(tempfile.gettempdir(), f"jarvis-chime-{name}.wav")
            write_wav(path, (wave_ * 32767).astype(np.int16), rate)
            _chime_files[name] = path
    cmd = plat.player_cmd(path, volume=0.6)
    if cmd:
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


WAKE_RE = None


def strip_wake_word(text: str) -> tuple[bool, str]:
    """Return (woke, remainder). 'Jarvis, open Safari' -> (True, 'open Safari')."""
    global WAKE_RE
    if WAKE_RE is None:
        words = "|".join(re.escape(w) for w in config.WAKE_WORDS if w)
        WAKE_RE = re.compile(rf"\b(hey |ok |okay )?({words})\b[\s,.!?]*", re.IGNORECASE)
    m = WAKE_RE.search(text)
    if not m:
        return False, text
    rest = (text[: m.start()] + " " + text[m.end():]).strip(" ,.")
    return True, rest


class KokoroVoice:
    """Local neural text-to-speech (Kokoro, ONNX). Loaded once; renders on a worker thread."""

    def __init__(self):
        import onnxruntime as ort
        from kokoro_onnx import Kokoro
        name = "kokoro-v1.0.onnx" if config.KOKORO_MODEL == "full" else "kokoro-v1.0.int8.onnx"
        opts = ort.SessionOptions()
        opts.add_session_config_entry("session.intra_op.allow_spinning", "0")  # don't burn CPU between sentences
        # More threads than this renders no faster on Apple silicon (measured), it just takes
        # cores away from the audio, the interface and everything else on the Mac.
        opts.intra_op_num_threads = config.KOKORO_THREADS
        opts.inter_op_num_threads = 1
        session = ort.InferenceSession(str(config.MODELS_DIR / name), opts, providers=["CPUExecutionProvider"])
        self.kokoro = Kokoro.from_session(session, str(config.MODELS_DIR / "voices-v1.0.bin"))
        if config.KOKORO_VOICE not in self.kokoro.get_voices():
            raise ValueError(f"unknown Kokoro voice {config.KOKORO_VOICE!r}")
        self.render("Online.")  # warm up the phonemizer and model so the first reply isn't slow

    def render(self, text: str) -> tuple[np.ndarray, int]:
        audio, rate = self.kokoro.create(text, voice=config.KOKORO_VOICE, speed=config.KOKORO_SPEED, lang="en-gb")
        return (np.clip(audio, -1, 1) * 32767).astype(np.int16), rate


def write_wav(path, data: np.ndarray, rate: int):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())


def read_wav(path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16), w.getframerate()


class PhraseCache:
    """Rendered audio for short lines JARVIS says often ("Right away, sir.", "Of course."),
    kept on disk so they play instantly instead of costing a second of CPU every time."""

    MAX_CHARS = 80
    MAX_FILES = 400

    def __init__(self):
        self.dir = config.DATA_DIR / "tts-cache"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.tag = f"{config.KOKORO_VOICE}|{config.KOKORO_SPEED}|{config.KOKORO_MODEL}"

    def _path(self, text: str):
        return self.dir / (hashlib.sha1(f"{self.tag}|{text}".encode()).hexdigest()[:24] + ".wav")

    def get(self, text: str):
        if len(text) > self.MAX_CHARS:
            return None
        p = self._path(text)
        if p.exists():
            os.utime(p)  # most recently used
            return p
        return None

    def put(self, text: str, data: np.ndarray, rate: int):
        if len(text) > self.MAX_CHARS:
            return None
        p = self._path(text)
        tmp = p.with_suffix(".part")
        write_wav(tmp, data, rate)
        os.replace(tmp, p)
        files = sorted(self.dir.glob("*.wav"), key=lambda f: f.stat().st_mtime)
        for old in files[: max(0, len(files) - self.MAX_FILES)]:
            old.unlink(missing_ok=True)
        return p


CLAUSE = re.compile(r"(?<=[,;:—–])\s+")


def split_for_speech(text: str, max_len: int = 110, first: bool = False) -> list[str]:
    """Break long sentences at clause boundaries. Kokoro renders a sentence before any of it
    plays, so shorter pieces start sooner. The first piece of a reply is kept short."""
    pieces = CLAUSE.split(text)
    out, cur = [], ""
    for p in pieces:
        limit = 45 if first and not out else max_len
        if cur and len(cur) + 1 + len(p) > limit:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}".strip()
    if cur:
        out.append(cur)
    # a short word cut off by the split ("sir; the potato...") belongs with the piece before it
    merged = []
    for piece in out:
        m = re.match(r"^(\S{1,8}[.!?,;:])(?:\s+(.*))?$", piece, re.S)
        if merged and m:
            merged[-1] = f"{merged[-1]} {m.group(1)}"
            piece = (m.group(2) or "").strip()
        if piece:
            merged.append(piece)
    return merged


class Speaker:
    """Queued, interruptible text-to-speech.

    Two stages run side by side: the next piece is rendered while the current one plays.
    Playback goes through the system's own player (afplay, winsound, pw-play/paplay/aplay) in
    a separate process, so the audio never depends on Python getting CPU time; when the
    computer is busy rendering speech, an in-process audio callback misses its deadlines and
    the voice stutters ("g-g-g-good").
    """

    def __init__(self, on_level, on_state, listener_pause: threading.Event | None):
        self.on_level = on_level
        self.on_state = on_state
        self.pause = listener_pause
        self.queue: asyncio.Queue[str] = asyncio.Queue()          # text waiting to be rendered
        self.ready: asyncio.Queue = asyncio.Queue()                # (generation, path, audio, rate, temp) waiting to play
        self.generation = 0
        self.speaking = False
        self.rendering = False
        self.voice = config.VOICE
        self.engine = None   # KokoroVoice once loaded; None means the system voice
        self.engine_ready = asyncio.Event()
        self.cache: PhraseCache | None = None
        self.proc: asyncio.subprocess.Process | None = None
        # Remote devices: remote_out() returns an async send(path) for the device that should
        # hear JARVIS (the one that last spoke to it), or None for this computer's speakers.
        self.remote_out = None
        self.on_interrupt = None
        self.tmpdir = tempfile.mkdtemp(prefix="jarvis-voice-")

    def start(self):
        asyncio.create_task(self._load_engine())
        asyncio.create_task(self._renderer())
        asyncio.create_task(self._player())

    async def _load_engine(self):
        if config.TTS == "kokoro":
            try:
                self.engine = await asyncio.to_thread(KokoroVoice)
                self.cache = PhraseCache()
                print(f"[voice] Kokoro voice {config.KOKORO_VOICE} ready ({config.KOKORO_THREADS} threads)")
            except Exception as e:
                print(f"[voice] Kokoro unavailable ({e}); using the system voice")
        self.engine_ready.set()

    def _quiet(self) -> bool:
        return not self.speaking and not self.rendering and self.queue.empty() and self.ready.empty()

    def say(self, text: str):
        text = text.strip()
        if not text:
            return
        if self.cache and self.cache.get(text):
            self.queue.put_nowait(text)  # pre-rendered: plays at once, whole
            return
        for piece in split_for_speech(text, first=self._quiet()):
            self.queue.put_nowait(piece)

    async def precache(self, phrases: list[str]):
        """Render common lines into the phrase cache while JARVIS is idle (once; kept on disk)."""
        await self.engine_ready.wait()
        if not self.cache:
            return
        for text in phrases:
            if self.cache.get(text):
                continue
            while not self._quiet():
                await asyncio.sleep(2)
            try:
                data, rate = await asyncio.to_thread(self.engine.render, text)
                await asyncio.to_thread(self.cache.put, text, data, rate)
            except Exception as e:
                print(f"[voice] precache failed: {e}")
                return
            await asyncio.sleep(0.5)

    def interrupt(self):
        self.generation += 1
        while not self.queue.empty():
            self.queue.get_nowait()
        while not self.ready.empty():
            self._discard(self.ready.get_nowait())
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
        if self.on_interrupt:
            self.on_interrupt()

    @staticmethod
    def _discard(item):
        _, path, _, _, temp = item
        if temp:
            try:
                os.unlink(path)
            except OSError:
                pass

    async def idle(self):
        """Wait until everything queued has been spoken."""
        while self.speaking or self.rendering or not self.queue.empty() or not self.ready.empty():
            await asyncio.sleep(0.1)

    # --- rendering ----------------------------------------------------------------
    def _prepare(self, text: str):
        """Text -> (wav path, samples, rate, is_temp_file). Uses the phrase cache when it can."""
        if self.engine:
            cached = self.cache.get(text) if self.cache else None
            if cached:
                data, rate = read_wav(cached)
                return cached, data, rate, False
            try:
                data, rate = self.engine.render(text)
                if self.cache:
                    path = self.cache.put(text, data, rate)
                    if path:
                        return path, data, rate, False
                return self._temp_wav(data, rate), data, rate, True
            except Exception as e:
                print(f"[voice] Kokoro failed on {text[:40]!r} ({e}); using the system voice")
        data, rate = self._render_say(text)
        return self._temp_wav(data, rate), data, rate, True

    def _temp_wav(self, data: np.ndarray, rate: int) -> str:
        fd, path = tempfile.mkstemp(suffix=".wav", dir=self.tmpdir)
        os.close(fd)
        write_wav(path, data, rate)
        return path

    def _render_say(self, text: str) -> tuple[np.ndarray, int]:
        """The system voice, rendered to a WAV: macOS `say`, Windows SAPI, or espeak-ng on Linux."""
        fd, path = tempfile.mkstemp(suffix=".wav", dir=self.tmpdir)
        os.close(fd)
        try:
            if plat.MAC:
                cmd = ["say", "-v", self.voice, "-r", str(config.VOICE_RATE), "-o", path,
                       "--file-format=WAVE", "--data-format=LEI16@22050", text]
            elif plat.WINDOWS:
                safe = text.replace("'", "''")
                rate = max(-10, min(10, round((config.VOICE_RATE - 180) / 12)))
                script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                          "$v = $s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo } | "
                          "Where-Object { $_.Culture.Name -eq 'en-GB' -and $_.Gender -eq 'Male' } | Select-Object -First 1; "
                          "if ($v) { $s.SelectVoice($v.Name) }; "
                          f"$s.Rate = {rate}; $s.SetOutputToWaveFile('{path}'); $s.Speak('{safe}'); $s.Dispose()")
                plat.powershell(script, timeout=60)
                return read_wav(path)
            else:
                exe = plat.which("espeak-ng", "espeak")
                if not exe:
                    raise RuntimeError("no voice: run the Kokoro download in run.sh, or install espeak-ng")
                cmd = [exe, "-v", "en-gb", "-s", str(config.VOICE_RATE - 20), "-w", path, text]
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
            return read_wav(path)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    async def _renderer(self):
        await self.engine_ready.wait()
        while True:
            text = await self.queue.get()
            gen = self.generation
            self.rendering = True
            item = None
            try:
                item = await asyncio.to_thread(self._prepare, text)
            except Exception as e:
                print(f"[voice] speech failed ({e})")
                if plat.MAC and self.voice != "Samantha":
                    self.voice = "Samantha"
            finally:
                self.rendering = False
            if item and gen == self.generation and len(item[1]) > 1:
                await self.ready.put((gen, *item))
            else:
                if item:
                    self._discard((gen, *item))
                if self._all_done():
                    await self._finish()

    # --- playback -----------------------------------------------------------------
    def _all_done(self, playing: bool = False) -> bool:
        return (playing or not self.speaking) and not self.rendering and self.queue.empty() and self.ready.empty()

    async def _finish(self):
        await self.on_level(0.0)
        if self.pause:
            await asyncio.sleep(0.35)  # let the room echo die down
            self.pause.clear()
        await self.on_state("idle")

    async def _play(self, gen: int, path, data: np.ndarray, rate: int):
        """Play one piece (the system player here, or on the remote device that asked), streaming
        its loudness to the UI meanwhile."""
        send = self.remote_out() if self.remote_out else None
        proc = None
        if not (send and await send(path)):
            cmd = plat.player_cmd(str(path))
            if cmd:
                self.proc = proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            # else: no player installed; the HUD still animates for the length of the line
        hop = int(rate * 0.033)
        total = len(data) / rate + 0.12  # the device needs a moment to decode
        start = time.monotonic()
        while gen == self.generation and (proc.returncode is None if proc else time.monotonic() - start < total):
            i = int((time.monotonic() - start) * rate)
            seg = data[i:i + hop]
            lvl = float(np.sqrt(np.mean(seg.astype(np.float32) ** 2))) / 6000.0 if len(seg) else 0.0
            await self.on_level(min(lvl, 1.0))
            await asyncio.sleep(0.033)
        if proc:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()

    async def _player(self):
        while True:
            item = await self.ready.get()
            gen, path, data, rate, _ = item
            try:
                if gen != self.generation:
                    continue
                if not self.speaking:
                    self.speaking = True
                    if self.pause:
                        self.pause.set()
                    await self.on_state("speaking")
                try:
                    await self._play(gen, path, data, rate)
                except Exception as e:
                    print(f"[voice] playback failed ({e})")
            finally:
                self._discard(item)
            # Keep "speaking" while the next piece is still rendering, so the UI doesn't flicker.
            while (self.rendering or not self.queue.empty()) and self.ready.empty() and gen == self.generation:
                await asyncio.sleep(0.03)
            if self._all_done(playing=True):
                self.speaking = False
                await self._finish()


SENTENCE_END = re.compile(r"(.+?[.!?])(\s+|$)", re.S)


class SentenceChunker:
    """Turns streamed text deltas into speakable sentences."""

    def __init__(self):
        self.buf = ""

    def feed(self, delta: str) -> list[str]:
        self.buf += delta
        out = []
        while True:
            m = SENTENCE_END.match(self.buf)
            if not m or (m.end() == len(self.buf) and not m.group(2)):
                break
            out.append(m.group(1))
            self.buf = self.buf[m.end():]
        return [clean_for_speech(s) for s in out if s.strip()]

    def flush(self) -> list[str]:
        rest, self.buf = self.buf, ""
        return [clean_for_speech(rest)] if rest.strip() else []


_TEX_WORDS = [(r"\\(?:d|t)?frac\{([^{}]*)\}\{([^{}]*)\}", r"\1 over \2"), (r"\\sqrt\{([^{}]*)\}", r"root \1"),
              (r"\^\{?2\}?", " squared"), (r"\^\{?3\}?", " cubed"), (r"\^\{([^{}]*)\}", r" to the \1"),
              (r"\^(\w)", r" to the \1"), (r"\\(?:cdot|times)", " times "), (r"\\pi", "pi"), (r"\\infty", "infinity"),
              (r"\\(?:approx)", " approximately "), (r"\\(?:le|leq)\b", " at most "), (r"\\(?:ge|geq)\b", " at least "),
              (r"\\pm", " plus or minus "), (r"=", " equals "), (r"\\[a-zA-Z]+", " "), (r"[{}_]", " ")]


def _speak_tex(tex: str) -> str:
    """Short inline maths ($x = 3$) read as words; anything long is left on screen."""
    if len(tex) > 40:
        return " (shown on screen) "
    for rx, rep in _TEX_WORDS:
        tex = re.sub(rx, rep, tex)
    return tex


def clean_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", " (code shown on screen) ", text, flags=re.S)
    # Maths belongs on the HUD; never read LaTeX aloud.
    text = re.sub(r"\$\$.*?\$\$|\\\[.*?\\\]", " (shown on screen) ", text, flags=re.S)
    text = re.sub(r"\\\((.*?)\\\)|\$([^\s$](?:[^$\n]*?[^\s$])?)\$",
                  lambda m: _speak_tex(m.group(1) or m.group(2)), text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_#>|]+", "", text)
    return re.sub(r"\s+", " ", text).strip()
