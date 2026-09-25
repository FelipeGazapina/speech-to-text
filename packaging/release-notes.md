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
