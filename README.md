# Speech to Text

A free, local dictation app for macOS, in the spirit of Wispr Flow and tuned for how software engineers talk.

**[⬇️ Download for Mac](https://github.com/FelipeGazapina/speech-to-text/releases/latest)** · [Install guide](#install-on-your-mac) · [Guia em português](docs/INSTALAR.md)

**Tap a key → talk → tap again.** A moment later, the cleaned-up text is pasted wherever your cursor is: your editor, the terminal, Slack, a browser, a Claude Code prompt.

- **$0, no subscription, no API keys.** Transcription runs on your Mac with OpenAI's open-source Whisper model. Your audio never leaves the machine.
- **Understands dev speech.** Whisper gets a glossary of engineering terms, such as `kubectl`, `useState`, `async/await`, `PostgreSQL` and `CI/CD`, so it spells them right.
- **A "reasoning" pass (optional, also free and local).** A small LLM running in [Ollama](https://ollama.com) turns what you *said* into what you *meant to type*:

| You say | It pastes |
|---|---|
| "um so we should uh refactor the user service to use async await" | So we should refactor the user service to use async/await. |
| "call it get user by ID no wait fetch user by ID and put it in utils dot ts" | Call it fetchUserById and put it in utils.ts. |
| "rename it to snake case max retry count" | Rename it to max_retry_count. |
| "run git push dash dash force with lease origin feature slash login" | Run git push --force-with-lease origin feature/login |
| "can you check why CI is failing" | Can you check why CI is failing? *(it doesn't answer; it only cleans up)* |
| "é então tipo a gente faz o deploy na sexta não pera na quinta" | Então, a gente faz o deploy na quinta. |

- **Speaks English and Portuguese, and mixes them.** Each dictation is auto-detected, but only between the languages you configure, so short Portuguese clips don't come out as Spanish. English tech terms inside Portuguese sentences are kept as they are, and nothing is translated.
- **Remembers everything and learns from it.** Every dictation is saved in a local SQLite database, so nothing is lost if a paste fails. The app also learns from that history how you talk: the names you use, the fixes you make and your style in each language. See [How it learns](#how-it-learns).

## Install on your Mac

🇧🇷 **Em português:** [docs/INSTALAR.md](docs/INSTALAR.md)

No Terminal and no developer tools needed. Setup takes about 5 minutes, plus the one-time model download.

### Requirements

- A Mac with **Apple Silicon** (M1, M2, M3, M4 or newer). To check, open  → About This Mac and look for "Chip: Apple M…".
- **macOS 14 Sonoma** or later.
- About **4 GB of free disk space**: 1.6 GB for the speech model, plus about 2 GB if you add the optional smart cleanup.

### 1. Download

Go to the **[latest release](https://github.com/FelipeGazapina/speech-to-text/releases/latest)** and, under **Assets**, click **`SpeechToText.dmg`**.

### 2. Install

1. Open `SpeechToText.dmg` from your Downloads folder.
2. Drag **Speech to Text** onto the **Applications** folder in the window that opens.
3. Eject the disk image (the ⏏ next to "Speech to Text" in Finder's sidebar). You can delete the .dmg afterwards.

### 3. Open it the first time

Open **Speech to Text** from Applications (or search for it with ⌘ Space).

The app isn't signed with a paid Apple Developer ID, so the first time macOS shows **"Speech to Text" Not Opened**. That's Gatekeeper being cautious; the app didn't crash. To allow it:

1. Click **Done**. Don't click *Move to Trash*.
2. Open **System Settings → Privacy & Security** and scroll down to **Security**.
3. Next to *"Speech to Text" was blocked…*, click **Open Anyway**, then confirm with your password or Touch ID.
4. Click **Open Anyway** once more in the dialog that follows.

The **Open Anyway** button only appears for about an hour after you tried to open the app. If you don't see it, open the app again and go back to Privacy & Security.

<details>
<summary>Prefer Terminal? One command does the same.</summary>

```bash
xattr -dr com.apple.quarantine "/Applications/Speech to Text.app"
```

</details>

You only do this once. Later updates install from inside the app and don't ask again.

### 4. Allow the permissions

A **🎙** (or ⏳) appears in the menu bar at the top right of your screen. The app has no Dock icon; that menu bar icon is the app.

macOS asks for these permissions. Allow each one:

| Permission | Why | Needed for |
|---|---|---|
| **Microphone** | To hear you | Dictation and meeting notes |
| **Accessibility** | To paste the text at your cursor (⌘V) | Dictation |
| **Input Monitoring** | To notice when you tap the hotkey | Dictation |
| **Screen & System Audio Recording** | To hear the other people in a call. Only sound is recorded, never your screen. | Meeting notes only, asked the first time you start one |

If a prompt doesn't appear or you clicked *Don't Allow*, go to **System Settings → Privacy & Security**, open each item above and switch **Speech to Text** on. While something is missing, the 🎙 menu shows ⚠️ with a button that opens the right settings page.

Once Input Monitoring is allowed, the app restarts by itself. If the hotkey still doesn't respond, choose **Restart** from the 🎙 menu.

### 5. Wait for the model

On the first start the menu bar shows **⏳** with a percentage while the speech model (~1.6 GB) downloads. When the icon turns into **🎙**, it's ready. This happens only once.

### 6. Try it

Click into any text field, **tap the right Option (⌥) key** once, say something, and tap it again. After a moment, the text is pasted where your cursor is.

### 7. Recommended extras

- **Open at login:** 🎙 menu → **Open at login**, so it's always there.
- **Smart cleanup:** install **[Ollama](https://ollama.com/download)**, a free app, and open it once. Speech to Text notices it and downloads its cleanup model by itself (~2 GB, one time), with the progress shown in the 🎙 menu. This is what removes the "um"s, applies "no wait…" self-corrections and formats code terms, and it also writes the meeting summaries. Without it you still get the plain transcription.

### Updating

From **v0.2.0** on, updates install inside the app. When a new version is out, the 🎙 menu shows **⬆️ Update to v…**. Click it, confirm with **Update**, and the app downloads, verifies and installs the new version, then restarts. You can also check any time with 🎙 → **Check for updates**.

Updates keep your permissions, history and settings, and they don't show the Gatekeeper dialog again.

If you're on **v0.1.x**, install v0.2.0 once by hand: quit the app, repeat steps 1–2 and choose **Replace**. macOS may ask for the permissions again this one time; if the hotkey then does nothing, see [Troubleshooting](#troubleshooting).

If you're on **v0.2.0**, its update check fails (no TLS certificates in that build), so install the next version once from a checkout of this repo, with the [`gh` CLI](https://cli.github.com) logged in. It downloads the release, checks it is signed like the installed app (so the permissions stay), waits for a meeting that is being recorded or transcribed to finish, then quits, swaps and relaunches the app:

```sh
python3 packaging/install_release.py          # latest release, after the current meeting
python3 packaging/install_release.py --now    # right away; the new app resumes any unfinished meeting
```

### Quitting and uninstalling

- **Quit:** 🎙 menu → **Quit**. If the app ever stops responding, open **Activity Monitor**, select *Speech to Text* and click **✕ → Force Quit**.
- **Stop it from opening at login:** 🎙 menu → uncheck **Open at login**, or remove it under System Settings → General → Login Items.
- **Uninstall:** quit the app and drag **Speech to Text** from Applications to the Trash. To remove your data too, delete these as well:
  - `~/Library/Application Support/speech-to-text`: dictation history and meeting notes
  - `~/.config/speech-to-text`: settings
  - `~/.cache/huggingface/hub/models--mlx-community--whisper-large-v3-turbo`: the speech model, 1.6 GB
  - `~/Library/Logs/speech-to-text*.log`: logs

  In Finder, use **Go → Go to Folder…** (⇧⌘G) to open these paths. To also free the cleanup model, run `ollama rm qwen2.5:3b` or uninstall Ollama.
- **Permissions:** remove Speech to Text with **–** from each list in System Settings → Privacy & Security.

## How it works

```
tap ⌥ → record → tap ⌥ → detect en/pt → Whisper → cleanup LLM → save to history → paste at cursor
                                           ↑            ↑                 │
                                           └── your vocabulary, fixes and style (learned) ◄──┘
```

The app lives in the menu bar. Its icon shows what it's doing:

| Icon | Meaning |
|---|---|
| 🎙 | Ready |
| 🔴 0:07 | Recording |
| 💭 | Transcribing and cleaning up |
| ⏳ | Loading the model |
| ⚠️ | Something failed (open the menu for details) |

## Install from source (developers)

```bash
brew install uv ollama
git clone https://github.com/felipegazapina/speech-to-text.git && cd speech-to-text
uv tool install .
brew services start ollama && ollama pull qwen2.5:3b   # optional smart cleanup
stt
```

`stt` runs in Terminal, so macOS asks for the permissions for your terminal app instead: Microphone, Input Monitoring and Accessibility. To build the .dmg yourself on an Apple Silicon Mac, run `packaging/build_dmg.sh`. GitHub Actions also builds it on every push (`.github/workflows/macos-app.yml`). When the default branch gets a version (`pyproject.toml`) that has no release yet, the workflow publishes the .dmg as release `v<version>`. So to ship an update, bump the version in `pyproject.toml` and `src/speech_to_text/__init__.py`.

## Use

| Action | What happens |
|---|---|
| Tap **right Option (⌥)** | Start recording (you'll hear a *tink*) |
| Tap **right Option** again | Stop, then transcribe, clean up and paste (*pop*) |
| **Esc** while recording | Throw the recording away |
| 🎙 menu → **Copy last transcription** | If the paste landed in the wrong place |
| 🎙 menu → **Recent** | Your last 10 dictations; click one to copy it, or click a ❌ one to retry it |
| 🎙 menu → **📝 Start meeting notes** | Record a meeting (you + the other people); transcript and summary when you stop |
| 🎙 menu → **Open Speech to Text…** | The app window: **Notetaker** (meetings) and **Dictations** (history) tabs |
| 🎙 menu → **Fix last transcription…** | Correct what it got wrong; the fix is copied and learned |
| 🎙 menu → **Hotkey** | Pick another key (right Option, Fn/🌐, right Command…) |
| 🎙 menu → **Smart cleanup** | Toggle the LLM pass on/off for this session |

The hotkey only fires when right Option is tapped **alone**, so ⌥-shortcuts and special characters keep working.

**Spoken formatting the cleanup model understands:**

- "camel case / snake case / pascal case / kebab case / all caps *words*"
- "dot", "slash", "dash dash"
- "new line", "new paragraph", "bullet point"
- Self-corrections like "no wait…" and "scratch that"
- The Portuguese equivalents: "ponto", "barra", "nova linha", "não, pera…", "quer dizer…"

## Meeting notes (Notetaker)

Click 🎙 → **📝 Start meeting notes** when a call begins, then **⏹ Stop meeting notes** when it ends. You can also use the button in the app window. Speech to Text records two tracks at once:

- **your microphone**, shown as "You"/"Você";
- **the computer's audio**, which is the other people in Zoom, Meet, Teams, Slack and so on, shown as "Others"/"Outros".

When you stop, it:

1. transcribes both tracks on your Mac with timestamps, skipping the silences;
2. drops the microphone's copy of what came out of your speakers, so no lines appear twice when you're not on headphones;
3. writes a summary with Ollama: a title, the summary, key points, decisions and action items, in the meeting's language.

Dictation keeps working during a meeting. Open the notes from 🎙 → **Open Speech to Text…**:

- The **Notetaker** tab lists your meetings. For each one you get the **Summary** and the original **Transcript**, and you can copy, rename, regenerate the summary or delete.
- The **Dictations** tab is your searchable dictation history.

**Storage:** the transcript and summary are saved in the same local SQLite database as your dictations. The audio is never kept: it goes to temporary files that are deleted as soon as the meeting is transcribed. If the app quits mid-meeting, what was recorded is transcribed the next time it starts.

**Permission:** recording the computer's audio needs **Screen & System Audio Recording** (System Settings → Privacy & Security). macOS asks the first time; restart the app after allowing it. Nothing of your screen is recorded, only the sound. Without that permission, meetings record your microphone only, and the notes say so.

## History

**Every** dictation is stored in `~/Library/Application Support/speech-to-text/history.db`, whether or not it was pasted. It's a plain SQLite file that never leaves your Mac. Each entry records:

- what Whisper heard
- the final text
- your correction, if you made one
- the language
- the app it went to
- what happened to it

The text is saved **before** the paste, so it survives even if the paste or the app fails. The only recordings not saved are silent ones, where nothing was said.

| Status | Menu icon | Meaning |
|---|---|---|
| pasted | | Pasted at your cursor |
| paste_failed | ⚠️ | Pasting didn't work; the text was left on your clipboard |
| filtered | 🔇 | Whisper returned something that usually means noise ("Thank you.", "Obrigado."), so it wasn't pasted, but it's saved in case you really said it |
| failed | ❌ | Transcription itself crashed. The **audio** is kept; click it under Recent (or run `stt retry`) to transcribe it again |
| recovered | ♻️ | A failed dictation that was retried successfully |

```bash
stt history              # last 20 dictations
stt history deploy -n 50 # search
stt history --lang pt    # only Portuguese
stt history --raw        # also show what Whisper heard before cleanup
stt copy                 # copy the last dictation to the clipboard
stt copy 128             # copy dictation #128
stt history --status failed
stt retry                # re-transcribe every failed dictation from its saved audio
```

To stop saving, set `[history] enabled = false` in the config. To erase everything, delete `history.db`.

## How it learns

Like Wispr Flow, it gets better the more you use it. Nothing is sent anywhere and nothing is "trained". Each dictation simply reuses what your history says about you:

| It learns | From | Used for |
|---|---|---|
| **Your vocabulary**: names and identifiers you repeat (Supabase, fetchUserById, Marina…) | Your past dictations | Whisper's prompt, so it spells them right, and the cleanup model's prompt |
| **Your fixes**: "get hub" → "GitHub" | **Fix last transcription…**, or `stt learn` | Hinted to the cleanup model right away; applied automatically once you've made the same fix twice |
| **Your style**: how you phrase and punctuate | The texts you corrected, and your recent dictations in each language | Examples for the cleanup model, plus priming Whisper with your recent text in the detected language |
| **Your languages** | The detected language of each dictation | `stt profile` |

```bash
stt profile                  # what it has learned so far
stt learn "cube control" kubectl   # teach a fix directly (applied immediately)
stt unlearn "cube control"   # forget one
```

The more you use **Fix last transcription…** when something comes out wrong, the faster it adapts.

## Configure

Use 🎙 → **Advanced → Open config file** (it's `~/.config/speech-to-text/config.toml`). Every option is commented. The ones you'll most likely touch:

```toml
hotkey = "right_option"   # or fn, right_command, right_control, f13…f19

[transcription]
model = "large-v3-turbo"  # "small" is faster on older/Intel Macs (avoid ".en" models: English only)
languages = ["en", "pt"]  # detected per dictation, only among these; ["pt"] to always use Portuguese

[cleanup]
model = "qwen2.5:3b"      # any Ollama model; try "gemma3:4b" or "llama3.2:3b"
vocabulary = ["Supabase", "Zod", "tRPC", "MyCompanyName"]   # names to spell right from day one

[history]
enabled = true            # save dictations to the local SQLite history
learn = true              # adapt to your vocabulary, fixes and style

[replacements]            # hard fixes for words Whisper keeps mishearing
"cube control" = "kubectl"
"get hub" = "GitHub"
```

Then use 🎙 → **Restart**. (The hotkey can also be changed from 🎙 → **Hotkey**, no file editing needed.)

**Tips:**

- **Want the 🌐 fn key like Wispr?** Set `hotkey = "fn"` and set System Settings → Keyboard → *Press 🌐 key to* → **Do Nothing**.
- **Want a dedicated key?** Map Caps Lock to F18 with [Karabiner-Elements](https://karabiner-elements.pqrs.org) and set `hotkey = "f18"`.

## Troubleshooting

- **Nothing happens when I tap the key.** Input Monitoring isn't granted. The 🎙 menu shows ⚠️ with a button that opens the right settings page, and the app restarts by itself once you allow it. If Speech to Text is already switched on there but the key still does nothing (common after installing a new version), remove it with **–**, add it again with **+**, and do the same under Accessibility.
- **The app froze.** It protects itself:
  - Starting the microphone has a deadline, and stopping it never waits on the audio system.
  - A transcription that doesn't finish in time is saved under Recent (❌, click to retry), and the model restarts.
  - If the app stops responding for 45 seconds anyway, it saves the recording in progress, notes where it was stuck in the log, and restarts itself.
- **The app quit or crashed.** Use 🎙 → Advanced → **Show log files in Finder** and send `speech-to-text.log` and `speech-to-text-console.log`. The console log includes native crash traces.
- **It transcribes but nothing gets pasted.** Accessibility isn't granted. The text is left on your clipboard, and you can also get it from **Recent** or `stt copy`.
- **The wrong language was detected.** Detection gets unreliable on very short clips (one or two words). If you only dictate in one language for a while, set `languages = ["pt"]` (or `["en"]`).
- **The first dictation is slow.** The models load once at startup (⏳), and later dictations are fast.
- **The text isn't cleaned up.** Check that Ollama is running (`ollama list`) and that you pulled the model named in the config.
- **Where are the logs?** 🎙 → Advanced → Open log (`~/Library/Logs/speech-to-text.log`). It includes the raw and final text of each dictation, which helps when tuning `vocabulary` and `replacements`.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest
```

| Module | Role |
|---|---|
| `hotkey.py` | Quartz event tap and tap-alone detection |
| `recorder.py` | Microphone capture at 16 kHz |
| `transcriber.py` | Whisper via MLX (Apple Silicon) or faster-whisper, and en/pt language detection |
| `history.py` | The SQLite history of dictations and learned fixes |
| `learning.py` | Builds your profile (vocabulary, fixes, style) from the history |
| `prompts.py` | Dev glossary and cleanup instructions |
| `cleanup.py` | Filler/replacement rules and the Ollama pass |
| `paster.py` | Clipboard, ⌘V, and restoring your clipboard |
| `meeting_audio.py` | Meeting recording: microphone + the computer's audio (ScreenCaptureKit) to temporary files |
| `meeting_notes.py` | Speech detection, per-track transcription, echo removal, Ollama summaries |
| `meetings.py`, `meeting_controller.py` | Meetings in SQLite; start/stop, background processing, crash recovery |
| `webui.py`, `web_page.py`, `window.py`, `backend.py` | The app window: a local web UI (token-protected, 127.0.0.1 only) in a native WKWebView |
| `updater.py` | In-app updates from GitHub Releases |
| `app.py` | The menu bar app that ties it together |
