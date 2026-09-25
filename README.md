# Speech to Text

A free, local dictation app for macOS, in the spirit of Wispr Flow and tuned for how software engineers talk.

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

## Install the app (no developer tools)

🇧🇷 **Em português:** [docs/INSTALAR.md](docs/INSTALAR.md)

You need an Apple Silicon Mac (M1 or newer) with macOS 14 or later.

1. **Download `SpeechToText.dmg`** from the [latest release](https://github.com/FelipeGazapina/speech-to-text/releases/latest).
2. **Open the .dmg** and drag **Speech to Text** onto **Applications**.
3. **Open it from Applications.** The app isn't signed with a paid Apple Developer ID, so the first time macOS shows *"Speech to Text" Not Opened*. That's Gatekeeper, not a crash.
   - Click **Done** (not *Move to Trash*).
   - Go to **System Settings → Privacy & Security**, scroll to **Security** and click **Open Anyway** next to "Speech to Text". Confirm with your password. The button only shows for about an hour after the blocked attempt.
   - Or, in Terminal, run `xattr -dr com.apple.quarantine "/Applications/Speech to Text.app"` once.
4. **Allow the three permissions** macOS asks for: **Microphone**, **Accessibility** and **Input Monitoring**. If a prompt doesn't appear, turn Speech to Text on in System Settings → Privacy & Security under each of them. Then choose **Restart** from the 🎙 menu bar icon.
5. **Wait for ⏳ to turn into 🎙.** The first start downloads the speech model (~1.6 GB).
6. *(Recommended)* **For the smart cleanup, install [Ollama](https://ollama.com/download)**, a free app, and open it once. Speech to Text notices it and downloads its cleanup model by itself (~2 GB, one time). The 🎙 menu shows the progress.

Then choose **Open at login** in the 🎙 menu, and you're done: tap **right Option**, talk, and tap again.

**Updating:** download the new .dmg and replace the app in Applications. Because the app isn't signed with a Developer ID, macOS may ask for the permissions again after an update.

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
