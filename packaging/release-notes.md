**What's new in 0.3.1:** the microphone no longer gets stuck. Audio is now read from the app's own thread instead of a Python callback on macOS's audio thread, so a busy app (a transcription, the window refreshing) can't make the audio system overload; one such overload at the moment a dictation stopped left the microphone hung inside macOS, and every start after it failed with "The microphone didn't respond". **Restart** from the menu (and the "Restart now" button) opens a fresh copy of the app instead of replacing the running one, which used to come back without its menu bar icon.

**What's new in 0.3.0:** meeting notes transcribe about 5x faster (Whisper gets 30 s of speech per call instead of one short remark), every finished part is saved so quitting or a crash resumes where it stopped instead of starting over, dictations are no longer dropped while a meeting is being transcribed, a second click while a meeting is starting or stopping no longer starts another one, and the menu bar icon and the window show what is happening: live loudness, a spinner, the percentage done and the time left. Updates wait for a meeting to end before installing, and the update check works again in the packaged app.

**Download `SpeechToText.dmg` below**, open it and drag **Speech to Text** into Applications.
Requires a Mac with Apple silicon (M1 or newer) and macOS 14 or later.

🇧🇷 **Guia de instalação em português:** [docs/INSTALAR.md](https://github.com/FelipeGazapina/speech-to-text/blob/claude/mac-speech-to-text-eypfm6/docs/INSTALAR.md)

**First launch:** the app isn't signed with a paid Apple Developer ID, so macOS shows *"Speech to Text" Not Opened*.
Click **Done** (not *Move to Trash*), then go to **System Settings → Privacy & Security**, scroll to **Security** and
click **Open Anyway**. Or run `xattr -dr com.apple.quarantine "/Applications/Speech to Text.app"` in Terminal once. Then allow **Microphone**, **Accessibility** and
**Input Monitoring**, and choose **Restart** from the 🎙 menu bar icon. The first start downloads the speech model (~1.6 GB).

**Smart cleanup (optional):** install the free [Ollama](https://ollama.com/download) app and open it once.
Speech to Text downloads its cleanup model by itself.

**Features:**
- Tap **right Option** to start dictating, tap again to paste the text at your cursor.
- Local Whisper transcription: free, and your audio never leaves your Mac.
- English and Portuguese, including mixed; tuned for how software engineers talk.
- **Meeting notes (Notetaker):** one click records you and the other people in the call, then transcribes and summarizes it on your Mac. Open them in the app window, which has **Notetaker** and **Dictations** tabs. The audio is never kept.
- Every dictation is saved to a local history, which you can search in the app window.
- **Updates itself** from the 🎙 menu, keeping macOS permissions (from 0.2.0 on).
- It learns your vocabulary and the corrections you make.
