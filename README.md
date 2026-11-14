# J.A.R.V.I.S. Ultimate 2

A voice-driven assistant built on **Claude Opus 5.5**, with a 3D holographic interface, webcam vision and hand gestures. It runs on **macOS, Windows and Linux**, with full control of the computer. On **Android phones, iPhones and iPads** it runs as JARVIS Pocket, an installable web app.

| Platform | How JARVIS runs | Start it |
|---|---|---|
| **macOS** | Full JARVIS: native app window, ⌘H / ⌘S push-to-talk, AppleScript, Calendar & Reminders | `./run.sh`, then optionally `./make_app.sh` |
| **Windows 10 / 11** | Full JARVIS in an Edge / Chrome app window, Ctrl+Alt+J / Ctrl+Alt+S push-to-talk, PowerShell | double-click `run.bat` |
| **Linux** (X11 desktop) | Full JARVIS in a Chrome / Chromium app window, Ctrl+Alt+J / Ctrl+Alt+S push-to-talk, bash | `./run.sh` |
| **Android** | **JARVIS Pocket**: the HUD in Chrome, installed as an app, talking to Claude with your own API key. It can also be a **companion** of a JARVIS computer | [Pocket](#jarvis-pocket-android-iphone-ipad) |
| **iPhone / iPad** | **JARVIS Pocket** in Safari, added to the Home Screen; or a **companion** of a JARVIS computer | [Pocket](#jarvis-pocket-android-iphone-ipad) |

It builds on [GauravSingh9356/J.A.R.V.I.S](https://github.com/GauravSingh9356/J.A.R.V.I.S). Several features carry over from that project:
- The microphone loop: energy-gated phrase capture sent to Google speech recognition.
- The time-of-day greeting.
- Weather.
- "Remember that" memory.
- Opening apps and sites.
- Jokes, news and Wikipedia, which the brain now handles through web search.

The hard-coded `if 'youtube' in query` command matching is gone. Claude Opus 5.5 now interprets every request and acts on it with tools.

## Quick start

You need **Python 3.11+** (or [uv](https://docs.astral.sh/uv/), which fetches Python for you) and a way to power the brain (next section). The first run creates a virtualenv, installs the requirements and downloads the voice model (~120 MB).

**macOS**
```bash
./run.sh                          # first run sets everything up
macapp/make_signing_identity.sh   # optional, once: local signing certificate, so permissions survive rebuilds
./make_app.sh                     # optional: builds ~/Applications/JARVIS.app
```
Open **JARVIS** from Spotlight, Launchpad or `~/Applications`, like any other app. It starts the core itself, shows the interface in its own window and shuts everything down when you quit (⌘Q). Closing the window keeps JARVIS running, so ⌘H still works; click the Dock icon to bring the window back.

**Windows**: double-click **`run.bat`** (or run `.\run.ps1` in PowerShell). If Python is missing, install it from [python.org](https://www.python.org/downloads/) with *Add to PATH* ticked, or run `winget install astral-sh.uv`. JARVIS opens in an Edge or Chrome app window. Windows may ask whether Python may use the network the first time you turn on phone access: allow it on private networks.

**Linux** (Ubuntu / Debian names; Fedora and Arch have the same packages)
```bash
sudo apt install libportaudio2 playerctl pulseaudio-utils espeak-ng libnotify-bin network-manager
./run.sh
```
Only `libportaudio2` (the microphone) is needed to start. `playerctl` adds media control, `pactl` (pulseaudio-utils, works with PipeWire too) adds volume and audio switching, `espeak-ng` is the fallback voice, `notify-send` shows notifications, and `nmcli` toggles Wi-Fi. JARVIS tells you which one to install when you ask for something that needs it.

**Push-to-talk:** press **⌘H** (Mac) or **Ctrl+Alt+J** (Windows, Linux), wait for the *tink*, and speak. JARVIS keeps recording, through pauses, until you press **⌘S** / **Ctrl+Alt+S**. Then it closes the microphone (*pop*) and acts on everything you said. The status line shows **RECORDING** with a timer. Press the listen key again instead to throw the recording away. Pressing it while JARVIS is talking interrupts it. The send key is only taken while JARVIS is recording, so Save still works in other apps. Recordings stop and send by themselves after 5 minutes. You can also click the mic button (once to start, again to send) or type in the command bar.

### Two ways to power the brain
| `JARVIS_BRAIN` | Billed to | Needs | Notes |
|---|---|---|---|
| `claude-code` (default when available) | Your **Claude Pro/Max plan**, no API credits | Claude Code 2.1.280+ signed in to claude.ai | Uses your plan's usage limits. Each reply starts a few seconds slower. |
| `api` | **API credits** | `ANTHROPIC_API_KEY` in `.env` | Fastest. Uses the native computer-use toolset and supports fast mode. |

In `claude-code` mode, JARVIS runs `claude -p` in the background and resumes the same session so the conversation carries over. It strips any `ANTHROPIC_API_KEY` from that process so usage always goes to your plan. JARVIS's own tools (screen control, shell, camera, HUD, memory, timers, protocols and more) reach Claude Code through a local MCP endpoint protected by a token. Claude Code's built-in shell and file-editing tools are switched off, so every action passes through JARVIS's safety gate.

JARVIS uses the newest Claude Code it can find: the one on your `PATH`, `~/.local/bin`, npm's global folder on Windows, or the copy bundled with the Claude desktop app on a Mac. Set `JARVIS_CLAUDE_BIN` to choose a specific one.

### Permissions (one-time)
**macOS**: under System Settings → Privacy & Security, turn these on for **JARVIS** (or for your terminal app when running from `./run.sh`):
- **Accessibility**: for the global ⌘H / ⌘S hotkeys (from any app) and for mouse and keyboard control. Without it they only work while the JARVIS window is in front.
- **Microphone**, **Camera**, **Screen & System Audio Recording** (screenshots; reopen JARVIS after allowing it), **Automation** (each app JARVIS scripts) and **Calendars and Reminders**: macOS asks the first time each is used.

JARVIS.app can be signed with a self-signed certificate, **JARVIS Local Signing**, kept in your login keychain, so macOS recognises every rebuild as the same app and permissions stay granted. Create it once with `macapp/make_signing_identity.sh`; `./make_app.sh` uses it automatically. (`./make_app.sh` installs to `~/Applications/JARVIS.app` and replaces any earlier JARVIS app there.)

**Windows**: Windows asks for the microphone and camera the first time. Under Settings → Privacy & security → Microphone, *Let desktop apps access your microphone* must be on. Mouse and keyboard control can't reach windows of apps running as administrator unless JARVIS also runs as administrator; it doesn't need to.

**Linux**: global hotkeys, mouse and keyboard control and screenshots need an **X11** session (on Ubuntu, pick "Ubuntu on Xorg" at the login screen). On **Wayland**, apps aren't allowed to watch global keys or move the pointer: use the mic button or the hotkeys while the JARVIS window is focused; everything else (voice, shell, web, vision, HUD, devices) still works.

## What it can do

| Ability | How | Where |
|---|---|---|
| **Full device control** | The Claude computer-use toolset (screenshot, click, type, drag, scroll, keys, zoom) plus a shell (zsh / bash / PowerShell) and, on a Mac, AppleScript. It can drive any app, open things, change settings and work with files. | Mac, Windows, Linux |
| **Vision** | `look_through_camera` sends a webcam frame to Opus 5.5. Example: "Jarvis, what am I holding?" | everywhere, Pocket too |
| **Web** | Server-side web search and fetch for news, facts and anything current. | everywhere |
| **Protocols** | Named routines in `data/protocols.json`: House Party, Clean Slate, Workshop, Lockdown, Morning Briefing, Eyes On. Each has its own splash and colour theme. Add your own. | Mac, Windows, Linux |
| **Diagnostics** | Live CPU, cores, memory, disk, battery and network telemetry, plus a full `system_diagnostics` scan. | Mac, Windows, Linux |
| **Attach images** | Click 📎, paste or drag pictures onto the window (up to 4), then ask: "what's wrong with this circuit?", "turn this sketch into a to-do list". Location and other photo metadata are stripped first. On phones 📎 opens the photo library or camera. | everywhere |
| **Image generation** | "Jarvis, generate an image of a red fox in the snow" creates a picture with Cloudflare Workers AI, shows it on the HUD and saves it to `~/Pictures/JARVIS`. Needs a Cloudflare key in `.env` (below). | Mac, Windows, Linux |
| **Copy itself to a device** | "Jarvis, copy yourself onto my USB drive" puts a clean copy of JARVIS onto a connected drive or an Android phone over USB. Only the public source is copied, never your keys or data. | Mac, Windows, Linux |
| **Wired devices** | "What's plugged in?" lists everything connected by cable. JARVIS can eject drives, switch sound output, talk to Arduino-style boards over serial, and operate an Android phone over USB (see *Devices connected by cable*). | Mac, Windows, Linux |
| **Holo-globe** | "Jarvis, show me Tokyo on the globe" turns the hologram to the location and pins it. | everywhere |
| **Memory** | "Remember that…" is kept across sessions (`data/memory.json`; in Pocket, in the browser). | everywhere |
| **Remembers every conversation** | Everything you and JARVIS say is logged on your computer and carried into later conversations, across restarts. The context compacts itself at 60% full. See [Conversation memory](#conversation-memory). | Mac, Windows, Linux |
| **Timers** | "Remind me in 10 minutes to…" makes JARVIS speak the reminder when it's due. | everywhere (Pocket: while open) |
| **Calendar & Reminders** | "What's on today?", "Add lunch with Pepper tomorrow at 1", "Remind me Friday at 9 to call Happy". Uses the Calendar and Reminders apps. | Mac |
| **Music** | "Play AC/DC", "Pause", "Skip this", "What's playing?". Mac: Music or Spotify. Windows: media keys, Spotify or YouTube Music. Linux: any MPRIS player through `playerctl`. | Mac, Windows, Linux |
| **System controls** | Volume, mute, dark mode, lock screen, sleep the display, Wi-Fi on or off, and notifications. | Mac, Windows, Linux |
| **Situational awareness** | JARVIS speaks up on its own when the battery runs low, an app hogs the processor for a minute, memory or disk space runs short, or (Mac) a meeting starts within 10 minutes. Set `JARVIS_ALERTS=0` to silence this. | Mac, Windows, Linux |
| **Sentinel** | JARVIS checks in by itself on a schedule (a status check every 10 minutes and a screen check every 25, 08:00 to 12:30 by default) and speaks only when something needs you. See [Sentinel](#sentinel-always-on-checks). | Mac, Windows, Linux |
| **HUD** | Details appear as floating holographic panels. JARVIS keeps its spoken replies short. | everywhere |

### Modes

Say the phrase (or type it, or use the buttons in the top bar). Modes are remembered across restarts.

| Say | What changes |
|---|---|
| "**Math mode**" | A rigorous mathematician: arithmetic to proofs, calculus, linear algebra, statistics, competition problems. Every result is checked with the `compute` tool (Python with SymPy, NumPy and SciPy). The full worked solution goes on a HUD panel with rendered equations; JARVIS only speaks the answer. Hold a problem up to the camera and it will read it. Violet theme, effort at least high. |
| "**Science mode**" | Explains science projects and experiments: what it is, how it works (with equations and reactions), how it was made, the science behind it, safety, and how to take it further, including a science-fair hypothesis and variables. Green theme, effort at least high. |
| "**Jarvis, full power**" | Maximum reasoning effort (`max`) and a larger output budget. JARVIS thinks longer, checks more, and is slower. Gold Iron Man theme. "**Power down**" returns to normal. |
| "**Standard mode**" | Back to the everyday assistant. |

Math and science are learning modes. JARVIS drops its Iron Man combat flavour there. Full power combines with either focus ("math mode, full power"). You can also add the request straight after the phrase: "math mode, what's the derivative of x to the x?"

### Hand gestures (webcam)
| Gesture | Effect |
|---|---|
| ✋ Open palm (hold) | Clear the HUD (in `wake` mic mode: listen without the wake word) |
| ✊ Fist (hold) | Stop speaking or cancel the current task |
| 👍 / 👎 | Approve or deny a pending authorisation |
| ✌ Victory (hold) | Visual scan through the camera |
| 🤏 Pinch + move | Grab and rotate the hologram |
| 🤏🤏 Two-hand pinch | Zoom the hologram |
| ✋ Swipe | Cycle hologram views (core, globe, diagnostics) |
| 🤏 Pinch on a button | "Touch" the HUD: close panels, press buttons |

Your hands also appear as a 3D wireframe hologram in the scene. Faces get targeting brackets.

### Voice
- The listen key (or the mic button) opens the microphone; the send key closes it and sends what you said. Nothing is recorded outside that window.
- Prefer hands-free? Set `JARVIS_MIC_MODE=wake` to keep the mic open and wake JARVIS by saying "Jarvis, …". Follow-ups within about 8 seconds of a reply don't need the wake word.
- Pressing **Esc**, saying "stop" or making a fist interrupts it at any point.
- Speech is streamed sentence by sentence as Opus 5.5 writes it, and the 3D core pulses with the voice.
- **The voice** is [Kokoro](https://github.com/thewh1teagle/kokoro-onnx), a neural text-to-speech model that runs locally on every desktop OS: free, offline, nothing sent anywhere. The default is the British male voice `bm_george`; `bm_lewis`, `bm_fable` and `bm_daniel` are the alternatives (set `JARVIS_KOKORO_VOICE`).
- If the model is missing or fails, JARVIS falls back to the system voice: `say` on a Mac (`JARVIS_VOICE`, default Daniel), a British SAPI voice on Windows if one is installed, `espeak-ng` (en-gb) on Linux. Set `JARVIS_TTS=say` to use the system voice only.

## Conversation memory
JARVIS remembers everything you have talked about, including after a restart.

- **The log.** Every exchange is written to `data/history.jsonl`, word for word and for good: what you said, what JARVIS answered and what it did. Ask "what did I tell you about the dentist last week?" and JARVIS searches it with its `recall` tool.
- **The summary.** A running summary of the whole log is kept in `data/conversation.json`. Each new conversation starts with that summary plus the latest exchanges word for word, so JARVIS picks up where you left off.
- **Auto-compact at 60%.** When the live conversation fills **60%** of the model's context window, JARVIS folds everything said since the last summary into it and continues on a fresh context that starts from the summary. The same happens after 20 quiet minutes, in the background, so the next request starts quickly. The log is never shortened, so nothing is forgotten; the feed shows "Memory compacted" when it happens.
- **The meter.** `CTX` in the top bar shows how full the context is (hover for the token count and how many times it has compacted).
- The summary is written by a small request to Claude Sonnet 5.5, not by the model that answers your commands.
- **Privacy:** both files stay on your computer and are never part of the repository or of a copy JARVIS makes of itself. The transcript since the last summary goes to Anthropic when the summary is updated. To wipe JARVIS's memory of your conversations, quit it and delete the two files. `JARVIS_HISTORY=0` switches logging and memory off.
- Change the threshold with `JARVIS_COMPACT_AT` (percent) and the quiet period with `JARVIS_IDLE_RESET_MINUTES`.

JARVIS Pocket (phones) is separate: it keeps its own "remember that" notes in the browser and does not have this log.

## Sentinel (always-on checks)
Switch Sentinel on and JARVIS looks in on things by itself during part of the day, without being asked. Most checks end in silence; it speaks one short line only when something needs your attention.

| Check | Default | What it looks at |
|---|---|---|
| **Status check** | every 10 minutes | Text only: the time, battery, processor, memory and disk, the busiest apps, what's playing, the weather, today's calendar and what you asked JARVIS to remember ("tell me at eleven to call the dentist"). |
| **Screen check** | every 25 minutes, on its own | One screenshot: an error dialog, a crashed app, a long task that finished or failed. |

With the default hours, **08:00 to 12:30**, that is 27 status checks and 10 screen checks a day.

- **Turn it on:** the **◉ SENTINEL** button in the window, "Jarvis, sentinel on", or `JARVIS_SENTINEL=1` in `.env`. The panel shows the schedule, the next check and what the last one found, and can run a check right away.
- **Sonnet 5.5 only.** Every check is one small request to `claude-sonnet-5-5`. The model is fixed in `jarvis/sentinel.py`, is not a setting, and an answer from any other model is thrown away. The model that answers your commands (Opus 5.5) is never used for a check. A status check is roughly 1,500 tokens; with the Claude Code brain it is billed to your Claude plan, with the API brain to your credits.
- **It can't act.** A check has no tools: it can stay silent or say something, nothing else. Text on the screen is treated as information, never as instructions. If it offers to do something and you say "yes, do that", your next command carries what it said, so JARVIS knows what you mean.
- **It waits its turn.** A check never starts while you are talking or JARVIS is working, and a check missed while the computer slept isn't made up later.
- **Privacy:** a screen check sends a picture of your whole screen to Anthropic every 25 minutes while Sentinel is on and inside its hours. Set `JARVIS_SENTINEL_SCREEN_MINUTES=0` to keep the status checks only.
- Change the hours and intervals with `JARVIS_SENTINEL_HOURS`, `JARVIS_SENTINEL_TEXT_MINUTES` and `JARVIS_SENTINEL_SCREEN_MINUTES`.

## JARVIS Pocket (Android, iPhone, iPad)
Phones don't let an app take over other apps, so JARVIS can't run its full core there. **JARVIS Pocket** is the same holographic HUD running entirely in the phone's browser. It talks to Claude Opus 5.5 directly with **your own Anthropic API key**, and uses the phone's own speech recognition and voice.

Pocket can talk (tap the mic), search and read the web, look through the phone's camera, show HUD panels and the holo-globe, check the weather, remember things, set timers, open links, maps and phone / message / email apps, take attached photos, and switch modes (math, science, full power). It can't control other apps or settings; for that, use it as a [companion](#companion-mode-any-phone-tablet-or-pc) of a JARVIS computer.

**Open it**
- **Hosted:** open **https://trovizeisback.github.io/Jarvis-ULTIMATE-2/** on the phone. The [Pocket workflow](.github/workflows/pocket.yml) publishes it to GitHub Pages on every change. In a fork, it appears at `https://<your-github-user>.github.io/Jarvis-ULTIMATE-2/` once the fork is public and Settings → Pages → Source is *GitHub Actions*.
- **Self-hosted:** serve the `ui/` folder over HTTPS from any static host and open it with `?pocket` at the end of the address.

**Install it like an app**
- **Android** (Chrome): ⋮ menu → *Install app* (or *Add to Home screen*).
- **iPhone / iPad** (Safari): Share → *Add to Home Screen*.

**First run**: tap **START**, then ⚙ and paste an API key from [console.anthropic.com](https://console.anthropic.com/settings/keys). Usage is billed to that key. Giving Pocket a key with a [spending limit](https://console.anthropic.com/settings/limits) is a good idea.

**Privacy and security**: the key is stored only in that browser (`localStorage`) and sent only to `api.anthropic.com`. It is never part of the site or the repository. Anyone who can unlock your phone and open Pocket can use it; ⚙ → *Remove key* deletes it. Conversations, memory and settings stay on the device. Speech recognition is done by the phone's browser (Google on Android, Apple on iPhone).

## Companion mode (any phone, tablet or PC)
JARVIS keeps running on your computer; any phone, tablet or other computer on the same Wi-Fi can use it from its web browser, with its own microphone and speaker. The brain, your Claude plan and every tool stay on the computer, so this gives a phone **full JARVIS**, including control of the computer.

**Pair a device**
1. On the computer, click **📱 DEVICES** in the JARVIS window, then **TURN ON** and **PAIR A DEVICE**. A QR code and a 6-digit code appear (valid 2 minutes).
2. On the device, scan the QR code with the camera, or open the address shown (e.g. `https://192.168.1.20:8766`) and type the code.
3. The browser warns that the connection isn't private, because the certificate is made by your computer rather than a public authority. Choose **Show details → visit this website** (Safari) or **Advanced → Proceed** (Chrome, Edge). This is expected on first visit.
4. Tap the mic to start talking and tap it again to send. Replies are spoken on the device.

Install it like an app the same way as Pocket. On iPhone and iPad a home-screen app keeps its own cookies, so pair once more from inside it.

**Good to know**
- The computer must be awake with JARVIS open. The first time, the firewall may ask whether Python may accept incoming connections: allow it (on Windows, for private networks).
- Same network only. To use it away from home, put both on a private network such as Tailscale and open the computer's Tailscale address; don't forward the port on your router.
- The camera, hand gestures and the Devices panel stay on the computer. Remote devices can't turn remote access on or pair other devices.
- Security: off until you turn it on; HTTPS only; single-use pairing codes with 5 tries; each device gets a random 256-bit key in a Secure, HttpOnly cookie (only its hash is stored, in `data/remote/`); the WebSocket checks the page's origin; Claude Code's tool bridge is never served on this port. Remove a lost device in **📱 DEVICES**, or turn remote access off.
- Change the port with `JARVIS_REMOTE_PORT` (default 8766).

## Copying JARVIS to another device
Ask "copy yourself onto my USB drive" (or "…onto my phone") and JARVIS writes a copy of its own code onto a device plugged into the computer.

- **USB drive or SD card** → the project folder, as `Jarvis-ULTIMATE-2/`. On another computer, open it and run `./run.sh` (Mac, Linux) or `run.bat` (Windows). An existing copy isn't overwritten; a second copy gets a dated name.
- **Android phone or tablet** (USB) → a zip in the phone's Download folder, for transport or backup. On the phone itself, use JARVIS Pocket.

The copy is JARVIS's committed source, exactly what's on GitHub: your `.env` (API keys), memory, paired devices and downloaded voice model are **never** included, because they aren't tracked by git. Packaging needs `git`.

## Devices connected by cable
Ask "what's connected?" and JARVIS checks USB, drives, displays, sound devices, serial boards, printers and cameras.

| Device | What JARVIS can do |
|---|---|
| **Android phone or tablet** | See the screen (it appears on the HUD), tap, swipe, type, press buttons, open apps and links, check battery, run commands, copy files both ways. |
| **USB drives and SD cards** | List, browse, copy, move and organise files; eject safely. |
| **USB headsets, speakers, microphones** | Switch the sound output or microphone (Mac, Linux; on Windows JARVIS tells you where to switch it in Settings, since Windows apps can't change it directly). |
| **Arduino, ESP32, micro:bit and other serial boards** | Send text commands and read what the board replies. |
| **Printers** | Print files (through the shell). |
| **iPhone and iPad** | Detected only. Apple gives no way to operate them over the cable. |
| Keyboards, mice, hubs | Listed; nothing to control. |

**Android setup (once):**
1. Install Google's Android platform tools: `brew install --cask android-platform-tools` (Mac), `winget install Google.PlatformTools` (Windows), `sudo apt install adb` (Linux), or unzip Google's [platform-tools](https://developer.android.com/tools/releases/platform-tools) into the Android SDK folder.
2. On the phone: Settings → About phone → tap **Build number** 7 times. Then Settings → System → Developer options → turn on **USB debugging**.
3. Plug the phone in with a data cable, unlock it and tap **Allow** when it asks about USB debugging.

JARVIS won't enter a PIN or password on the phone, and asks before sending messages, calling, buying or deleting.

## Safety
This program gives an AI real control of your computer. Two safeguards are on by default:
- **Destructive commands need your approval.** Before running something like `rm -rf`, `sudo`, `shutdown`, `Remove-Item -Recurse`, `format`, `diskpart`, `reg delete`, `killall`, "empty trash", "send" or "delete", JARVIS asks out loud. Approve by voice, 👍 or the dialog. Set `JARVIS_CONFIRM_DANGEROUS=0` to turn this off.
- **Content is never treated as instructions.** The system prompt tells JARVIS that text on screen, in web pages or through the camera is information, never commands. It won't type passwords or payment details for you.

The mouse failsafe stays on as well: slam the pointer into a screen corner to abort a GUI action.

## Configuration (`.env`)
| Variable | Default | Notes |
|---|---|---|
| `JARVIS_BRAIN` | `auto` | `claude-code` (your plan), `api` (credits) or `auto` |
| `ANTHROPIC_API_KEY` | — | Only for `JARVIS_BRAIN=api` |
| `JARVIS_EFFORT` | `medium` | Standard-mode effort. Math and science modes raise it to at least `high`; full power uses `max`. |
| `JARVIS_FAST` | `0` | API brain only: Opus fast mode, up to 2.5× faster output at 2× the price |
| `JARVIS_OWNER` | `sir` | What JARVIS calls you |
| `JARVIS_TTS` | `kokoro` | `kokoro` (neural, local) or `say` (the system voice) |
| `JARVIS_KOKORO_VOICE` | `bm_george` | `bm_george`, `bm_lewis`, `bm_fable`, `bm_daniel` (British male) |
| `JARVIS_KOKORO_SPEED` | `1.0` | Speaking speed, e.g. `1.1` for a little faster |
| `JARVIS_KOKORO_THREADS` | `4` | CPU threads for the voice |
| `JARVIS_VOICE`, `JARVIS_VOICE_RATE` | `Daniel`, `190` | Fallback system voice (macOS: any voice from `say -v '?'`) |
| `JARVIS_WAKE_WORDS` | `jarvis,…` | Comma-separated |
| `JARVIS_STT_LANGUAGE` | `en-US` | Google speech-to-text language, e.g. `en-GB`, `en-IN`, `hi-IN` |
| `JARVIS_MAX_RECORDING` | `300` | Seconds a push-to-talk recording can run before it stops and sends by itself |
| `JARVIS_MIC_MODE` | `push` | `push`: mic opens only on the listen key · `wake`: always listening for the wake word |
| `JARVIS_MIC` | `1` | Set to `0` for typing only |
| `JARVIS_ALERTS` | `1` | Spoken heads-ups about battery, heavy apps, memory, disk and meetings |
| `JARVIS_HISTORY` | `1` | Log every exchange and carry the memory into new conversations; `0` switches it off |
| `JARVIS_COMPACT_AT` | `60` | Percent of the model's context window at which the conversation is compacted into the summary |
| `JARVIS_IDLE_RESET_MINUTES` | `20` | Quiet minutes after which the conversation is compacted in the background |
| `JARVIS_SENTINEL` | `0` | `1` starts with Sentinel on (the HUD button and "sentinel on / off" also switch it, and that choice is remembered) |
| `JARVIS_SENTINEL_HOURS` | `08:00-12:30` | Daily window for Sentinel checks, 24-hour clock |
| `JARVIS_SENTINEL_TEXT_MINUTES`, `JARVIS_SENTINEL_SCREEN_MINUTES` | `10`, `25` | Minutes between status checks and between screen checks; `0` switches that kind off |
| `JARVIS_OPEN_BROWSER` | `1` | Set to `0` to start without opening the interface window |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | — | For image generation. Create a token with the **Workers AI** permission at dash.cloudflare.com → My Profile → API Tokens |
| `JARVIS_IMAGE_DIR` | `~/Pictures/JARVIS` | Where generated images are saved |
| `JARVIS_PORT`, `JARVIS_REMOTE_PORT` | `8765`, `8766` | Local interface port; HTTPS port for companion devices |

## Architecture
```
jarvis/main.py        aiohttp server and websocket hub; routes voice, gestures, brain and UI
jarvis/plat.py        platform layer: OS detection, keys, shell, audio player, opening files and links
jarvis/system.py      picks the OS integration below
jarvis/macos.py       Mac: Music/Spotify, system controls (AppleScript), Calendar and Reminders (EventKit)
jarvis/windows.py     Windows: media keys, volume (Core Audio), dark mode, lock, notifications, Wi-Fi
jarvis/linux.py       Linux: playerctl, pactl, gsettings, loginctl, notify-send, nmcli
jarvis/brain.py       API brain: Opus 5.5 streaming agent loop (adaptive thinking, progress updates, fallbacks)
jarvis/brain_code.py  plan brain: Opus 5.5 through headless Claude Code, session resume
jarvis/mcp_bridge.py  local MCP endpoint exposing JARVIS tools to Claude Code
jarvis/tools.py       tool definitions and executors, plus the safety gate
jarvis/computer.py    executor for the computer-use toolset (HiDPI-aware coordinates)
jarvis/voice.py       push-to-talk / wake-word listener, Kokoro and system-voice synthesis
jarvis/hotkey.py      global push-to-talk keys (Quartz on a Mac, pynput elsewhere)
jarvis/window.py      the interface window (JARVIS.app, or a Chromium-family app window)
jarvis/history.py     conversation memory: the log of every exchange, the running summary, compaction at 60%
jarvis/sentinel.py    Sentinel: scheduled status and screen checks on Sonnet 5.5, with no tools
jarvis/imagegen.py    image generation through Cloudflare Workers AI
jarvis/uploads.py     attached images: checks, metadata stripping, the tray that sends them with the next command
jarvis/deploy.py      copy JARVIS's own source onto a connected drive or Android phone
jarvis/devices.py     wired devices: discovery, Android (adb), drives, audio switching, serial boards
jarvis/remote.py      companion access: pairing codes, device keys, self-signed HTTPS certificate
macapp/JARVIS.swift   native macOS app: window (WKWebView), starts and stops the Python core
ui/js/scene.js        Three.js hologram: core, geodesic cage, energy beams, rings, particle streams, globe, hand mirror
ui/js/hands.js        MediaPipe gesture and face tracking (runs locally in the browser)
ui/js/app.js          HUD, panels, telemetry, gesture → intent wiring
ui/js/pocket.js       JARVIS Pocket: the brain, tools and voice running in the phone's browser
ui/js/remote.js       companion microphone capture and speech playback
ui/js/devices.js      📱 DEVICES panel (turn on, QR pairing, remove devices)
ui/js/sentinel.js     ◉ SENTINEL panel (on/off, schedule, last result, check now)
ui/sw.js              service worker that makes Pocket installable
tests/smoke.py        cross-platform smoke test (CI runs it on Linux, Windows and macOS)
```

API-brain request settings: model `claude-opus-5-5`, `thinking: adaptive` with `display: "updates"` (shows progress notes in the HUD), and `effort` from `.env`. Server-side refusal fallbacks (`fallbacks: "default"`) and automatic prompt caching are also on. Conversations are compacted into the memory summary at 60% of the context window and after 20 idle minutes.

**Privacy:** in the default push-to-talk mode, only what you say between the listen and send keys is recorded, and it goes to Google's free speech recognition. In `wake` mode every phrase the mic picks up goes there, because that's how the wake word is detected. Requests, images you attach, screenshots (including a wired phone's screen) and camera frames go to Anthropic, through either Claude Code or the API. Image descriptions go to Cloudflare. With Sentinel on, its status snapshots and screen pictures go to Anthropic on its schedule.

The local server only accepts websocket connections from its own page. This stops other websites open in your browser from sending JARVIS commands. The companion port (8766, off unless you turn it on) serves only paired devices.

## License
[MIT](LICENSE)
