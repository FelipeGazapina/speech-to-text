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

## Install

You need macOS 13+ and ideally an Apple Silicon (M1–M4) Mac. Intel works too, just slower.

**1. Install [uv](https://docs.astral.sh/uv/), the Python installer, and this app:**

```bash
brew install uv
git clone https://github.com/felipegazapina/speech-to-text.git
cd speech-to-text
uv tool install .
```

**2. (Recommended) Install the free cleanup model:**

```bash
brew install ollama
brew services start ollama      # keeps Ollama running in the background
ollama pull qwen2.5:3b          # ~2 GB, one-time download
```

You can skip this step. The app still works without it and pastes the raw Whisper transcript, with filler words stripped.

**3. Run it:**

```bash
stt
```

On first launch it downloads the Whisper model (~1.6 GB, one time). macOS will ask for three permissions for the app you launched it from (e.g. Terminal/iTerm). Grant all three in **System Settings → Privacy & Security**:

| Permission | Why |
|---|---|
| **Microphone** | To hear you. |
| **Input Monitoring** | To notice the hotkey. |
| **Accessibility** | To press ⌘V for you. |

After granting them, choose **Reload config** from the 🎙 menu (or restart `stt`).

**4. (Optional) Start it at login without a Terminal window:**

```bash
./scripts/make_app.sh
```

This creates `~/Applications/Speech to Text.app`. Open it once, grant it the same three permissions, then add it under **System Settings → General → Login Items**.

## Use

| Action | What happens |
|---|---|
| Tap **right Option (⌥)** | Start recording (you'll hear a *tink*) |
| Tap **right Option** again | Stop, then transcribe, clean up and paste (*pop*) |
| **Esc** while recording | Throw the recording away |
| 🎙 menu → **Copy last transcription** | If the paste landed in the wrong place |
| 🎙 menu → **Recent** | Your last 10 dictations; click one to copy it, or click a ❌ one to retry it |
| 🎙 menu → **Fix last transcription…** | Correct what it got wrong; the fix is copied and learned |
| 🎙 menu → **Smart cleanup** | Toggle the LLM pass on/off for this session |

The hotkey only fires when right Option is tapped **alone**, so ⌥-shortcuts and special characters keep working.

**Spoken formatting the cleanup model understands:**

- "camel case / snake case / pascal case / kebab case / all caps *words*"
- "dot", "slash", "dash dash"
- "new line", "new paragraph", "bullet point"
- Self-corrections like "no wait…" and "scratch that"
- The Portuguese equivalents: "ponto", "barra", "nova linha", "não, pera…", "quer dizer…"

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

Run `stt --config-path` to find the file (`~/.config/speech-to-text/config.toml`), or use 🎙 → **Open config**. Every option is commented. The ones you'll most likely touch:

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

Then use 🎙 → **Reload config**.

**Tips:**

- **Want the 🌐 fn key like Wispr?** Set `hotkey = "fn"` and set System Settings → Keyboard → *Press 🌐 key to* → **Do Nothing**.
- **Want a dedicated key?** Map Caps Lock to F18 with [Karabiner-Elements](https://karabiner-elements.pqrs.org) and set `hotkey = "f18"`.

## Troubleshooting

- **Nothing happens when I tap the key.** Input Monitoring isn't granted. Grant it, then Reload config.
- **It transcribes but nothing gets pasted.** Accessibility isn't granted. The text is left on your clipboard, and you can also get it from **Recent** or `stt copy`.
- **The wrong language was detected.** Detection gets unreliable on very short clips (one or two words). If you only dictate in one language for a while, set `languages = ["pt"]` (or `["en"]`).
- **The first dictation is slow.** The models load once at startup (⏳), and later dictations are fast.
- **The text isn't cleaned up.** Check that Ollama is running (`ollama list`) and that you pulled the model named in the config.
- **Where are the logs?** `~/Library/Logs/speech-to-text.log`. It includes the raw and final text of each dictation, which helps when tuning `vocabulary` and `replacements`.

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
| `app.py` | The menu bar app that ties it together |
