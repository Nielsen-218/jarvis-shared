# J.A.R.V.I.S. — your own PC assistant

Jarvis is a voice assistant that runs **on your own Windows PC**. You talk to it ("Hey Jarvis…") and it talks back. It can open and close apps, read your screen, remember things you tell it, keep lists and reminders, check the weather, search the web, teach languages, coach workouts, plan meals, and more.

The brain runs **locally on your graphics card** through a free program called Ollama, so it works with **no accounts and no API keys**. If you want, you can add your own Google Gemini or Anthropic Claude key later as an extra "smarter fallback". Those cost you money (or use your free tier), on your own account.

This copy is a fresh install. It has no one else's memories, settings, keys or devices. Everything Jarvis learns about you stays in the `data\` folder on your PC.

---

## What you need

| | Minimum | Recommended |
|---|---|---|
| Windows | Windows 10 (64-bit) | **Windows 11** |
| Graphics card | NVIDIA with **8 GB** VRAM (RTX 3060 / 4060 class) | NVIDIA with 12–16 GB (RTX 4070 / 5070 class) |
| Memory (RAM) | 16 GB | 32 GB |
| Free disk space | 25 GB | 40 GB |
| Other | Microphone and speakers/headset. Internet for the install and for web/weather questions | A webcam (only for the "eyes" features) |

**No NVIDIA card?** It still installs (`setup.ps1 -Cpu`), but the voice and brain will be slow. AMD and Intel GPUs aren't supported for the speech model. Ollama may still use them for the brain.

---

## Install (about 20–40 minutes, mostly downloads)

1. **Unzip it somewhere simple,** such as `C:\Jarvis`. Right-click the ZIP → **Extract All**. Avoid Desktop/OneDrive folders.
2. **Double-click `INSTALL.bat`** in that folder. If Windows shows "Windows protected your PC", click **More info** → **Run anyway**.
3. Answer the two questions (your name, and how Jarvis should address you), then wait.

   It will:
   - install **Python 3.12** and **Ollama** with `winget` if they're missing (you may see a Windows "allow" prompt),
   - create a private Python environment (`.venv`) and install Jarvis's packages. This includes PyTorch with CUDA, a few GB.
   - download the local brain model (`qwen3.5:9b`, about 6.6 GB),
   - ask **your name** and how Jarvis should address you (sir / ma'am / your name),
   - write your own fresh `.env` (settings) and `data\` (your memory) folder,
   - ask Jarvis one test question and show its answer.

   When it ends with `[hello] OK`, you're set.

**Smaller graphics card (8 GB or less)?** Pick a smaller model that supports tool calling from [ollama.com/library](https://ollama.com/library) and pass its name: `.\setup.ps1 -Model <name>`. Jarvis was tuned and tested on `qwen3.5:9b`; other models work but may misread commands more often.

---

## Using it

| To | Do this |
|---|---|
| Start with voice | Double-click **`jarvis.bat`**, then say **"Hey Jarvis"** or press **Ctrl+Alt+J** |
| Type instead of talk | `jarvis.bat --text` |
| No voice output | `jarvis.bat --mute` |
| Start hidden in the background (tray icon) | `start-jarvis.bat` |
| Stop it | `stop-jarvis.bat`, or the tray icon's **Quit** |
| Dictate into any app | Hold **Right Ctrl** and talk |

Things to try:
- "What's the weather tomorrow?"
- "Open Notepad."
- "Remember my favorite team is the Lakers."
- "Remind me in 10 minutes to check the oven."
- "What's on my screen?"
- "Teach me some Spanish."
- "Search the web for the best budget SSD."

Jarvis speaks **English, Haitian Creole (Kreyòl) and French**, and answers in the language you use.

> **Heads-up: Jarvis does what you tell it.** When **you** (the owner) give it a direct command, that command counts as the approval. It won't ask "are you sure?" for things like installing or uninstalling a program. It still asks before sending, buying, or deleting things someone else asked for. If you'd rather it double-check, tell it: "ask me first before you do things".

---

## Adding your own API keys (optional)

Everything works offline with the local model. Keys only add extras. Open **`.env`** in Notepad, fill in a line, save, and restart Jarvis.

| Key in `.env` | What it adds | Where to get it | Cost |
|---|---|---|---|
| `GEMINI_API_KEY` | A cloud brain for hard questions when the local one struggles | [aistudio.google.com/api-keys](https://aistudio.google.com/api-keys) | Free tier, then paid |
| `ANTHROPIC_API_KEY` | Claude as the cloud brain | [console.anthropic.com](https://console.anthropic.com/) | Paid |
| `TMDB_API_KEY` | Movie ratings, cast, and "where to stream" | [themoviedb.org](https://www.themoviedb.org/settings/api) | Free |
| `TELEGRAM_BOT_TOKEN` | Message Jarvis from Telegram | @BotFather in Telegram | Free |
| `MESHY_API_KEY`, `HYPER3D_API_KEY` | 3D model generation (only with the Blender skills) | meshy.ai / hyper3d.ai | Paid |

**To let Jarvis use a cloud key**, also change these two lines in `.env`:

```
JARVIS_LOCAL_ONLY=0
JARVIS_CLOUD_FALLBACK=gemini      (or: claude)
```

`JARVIS_MONTHLY_BUDGET` (default `5`, in US dollars) is a hard monthly cap. Once it's reached, Jarvis stops calling paid APIs. Ask "How much have you cost me this month?" to see the total.

**Keep your `.env` private.** It holds your keys. Never send it to anyone or upload it.

---

## What works offline vs. what needs the internet or a key

| Works fully offline (after install) | Needs internet (free, no key) | Needs your own key |
|---|---|---|
| The brain (local model), wake word, speech recognition, the Kokoro voice | Weather (Open-Meteo) | Cloud brain fallback (Gemini / Claude) |
| Opening and closing apps, volume, media keys, timers | Web search and page reading | Movie details (TMDB) |
| Memory, notes, lists, reminders | Maps, places and directions (OpenStreetMap) | Telegram messages |
| Reading your screen (local OCR), the webcam "eyes" (local model) | The Microsoft neural voices (edge-tts) for English, French and Creole | 3D generation (Meshy / Hyper3D) |
| Language lessons, tutor, study quizzes | News, prices and grocery deals | |
| Fitness plans, kitchen and meal plans, budgets | Installing software through winget | |

---

## Optional pieces (off by default)

None of these are needed. Jarvis runs fine without them and skips what isn't installed.

| Piece | What it's for | How to turn it on |
|---|---|---|
| **Blender** | 3D modeling and character rigging by voice | Install Blender 4.x or 5.x, then set `JARVIS_SKILLS_ON=rigging,charjobs,blenderskills,makechar` in `.env` |
| **OBS Studio** | Streaming and recording helpers | Install OBS, enable its WebSocket server (Tools → WebSocket Server Settings) |
| **Edge tab extension** | Lets Jarvis close and read browser tabs | In Edge, go to `edge://extensions` → Developer mode → **Load unpacked** → pick the `core\browserbridge_ext` folder |
| **Phone app** (web app on port 8765) | Talk to Jarvis from your phone | Install [Tailscale](https://tailscale.com) on the PC and phone (your own account), then run `tailscale serve 8765` on the PC. Make a pairing code with `.venv\Scripts\python.exe manage.py pair "YourName"` and enter it in the phone app |
| **Second machine / laptop server** | An always-on helper that relays your phone to the PC | Advanced: see `server.py` and the `JARVIS_SERVER_*` settings |
| **Desktop companion robot** | A 3D robot on your desktop | `JARVIS_COMPANION=1` |
| **Storm Watch** | Spoken alerts for hurricanes, earthquakes and severe weather near your towns | Edit `PLACES` in `core\stormwatch.py`, then set `JARVIS_STORMWATCH=on` |

### PC-specific skill groups that are off by default

These were built for one particular setup (a home server, a game project, and so on). They stay hidden from the brain unless you name them in `JARVIS_SKILLS_ON` (comma-separated, or `all`):

| Name | What it does | Needs |
|---|---|---|
| `gaming` | Game mode, clipping, and an Apex Legends coach | The game, plus OBS for clips |
| `unreal` | Unreal Engine project helpers | Unreal Engine 5 and a project, with `JARVIS_LSW_ROOT` pointing at it |
| `gamestudio` | A "studio manager" that turns notes about a game into coding work orders | A game repo (`JARVIS_LSW_REPO`) plus a coding agent |
| `homelab` | Controls a Raspberry Pi / home server (Docker, Jellyfin) over SSH | A home server: `HOMELAB_HOST`, `HOMELAB_USER`, `JELLYFIN_API_KEY` |
| `qbittorrent` | Manages torrents on that server | qBittorrent WebUI: `QBIT_USER`, `QBIT_PASS` |
| `pcview`, `missions` | Phone → laptop → PC relay and a background mission queue | The second-machine setup above |
| `rigging`, `charjobs`, `makechar`, `blenderskills`, `dojo` | Blender character rigging, concept images, and practice mode | Blender (and a GPU for image generation) |

To turn **off** any other group, list it in `JARVIS_SKILLS_OFF` (the names are the files in `tools\`). For example, `JARVIS_SKILLS_OFF=military,fitness`.

---

## Updating

If you cloned with Git:

```powershell
git pull
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

If you used a ZIP: download the new ZIP and unzip it **over** your folder. Keep your `.env` and `data\`. Then double-click `INSTALL.bat` again.

Your `.env` and `data\` are never overwritten by an update or by re-running setup.

**Back up `data\` now and then.** It's everything Jarvis knows about you.

---

## Troubleshooting

- **`[hello] FAILED` / "local intelligence is unavailable".** Ollama isn't running, or the model isn't downloaded. Open the Ollama app (or run `ollama serve`), then run `ollama pull qwen3.5:9b`. After that, run `.venv\Scripts\python.exe scripts\hello_check.py` again.
- **It doesn't hear "Hey Jarvis".** Check that Windows has the right default microphone (Settings → System → Sound). You can also press **Ctrl+Alt+J** instead. To make the wake word easier to trigger, lower `WAKE_THRESHOLD` in `.env` (for example, to `0.4`).
- **Very slow answers.** The model may be too big for your GPU. Try a smaller one: `ollama pull <name>`, then set every `qwen3.5:9b` in `.env` to that name.
- **"CUDA" errors on start.** Set `WHISPER_DEVICE=cpu` and `WHISPER_MODEL=small` in `.env`.
- **The installer stopped partway.** Double-click `INSTALL.bat` again. It picks up where it left off.

---

## Running the tests (for tinkerers)

```powershell
.venv\Scripts\python.exe -m pytest -q -x -p no:cacheprovider tests --ignore=tests/live
```

`tests\live` drives your real PC (it opens apps, for example), so it's skipped above.

## Privacy

- Your conversations, memories and voice prints stay in `data\` on your PC.
- With `JARVIS_LOCAL_ONLY=1` (the default), nothing goes to a paid cloud AI.
- Web searches and weather questions go to those public services. Jarvis sends only the question, plus a town name for local questions.
- `.env` and `data\` are in `.gitignore`, so Git never commits them.
