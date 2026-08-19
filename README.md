# Mind

[Download the latest Windows release](https://github.com/rileously/Mind/releases/latest/download/Mind.exe)

Mind is a modern Windows writing assistant that transforms text directly inside any app.
Type a trigger such as `?fix` after a sentence and Mind replaces the text in place using
Gemini, Groq, Ollama, LM Studio, or another OpenAI-compatible provider.

Mind combines a fast Windows Raw Input engine with a modern desktop layer that adds a
visual setup wizard, dashboard, encrypted API-key storage, provider testing, command
management, themes, diagnostics, and system-tray controls.

## Development preview

Requirements:

- Windows 10 or 11 (64-bit)
- Python 3.10+

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe mind_app.pyw
```

Mind stores its settings in `%APPDATA%\Mind`. Your existing `%USERPROFILE%\.swiftslate`
installation is not modified. On first launch, Mind can import its provider configuration
and commands; imported API keys are immediately protected with Windows DPAPI.

## Standalone Windows executable

Build the portable, single-file Windows application with:

```powershell
.\build_exe.ps1
```

The result is written to `artifacts\Mind.exe`. The destination PC does not need Python;
double-click `Mind.exe` and complete the setup wizard. On its first run Mind offers to
install itself into `%LOCALAPPDATA%\Programs\Mind` and add a Start Menu entry, so it does
not have to keep running from the Downloads folder. Declining is remembered and Mind does
not ask again. The executable is currently unsigned, so Windows may show an
unknown-publisher warning.

Mind unpacks its bundled Python runtime into `%LOCALAPPDATA%\Mind\Runtime` and removes it
on exit. Folders stranded by a crash or a forced End Task are cleaned up on the next
launch; a folder still in use is never touched.

## Current preview features

- Four-step visual onboarding
- Optional SwiftSlate configuration migration
- Gemini, Groq, Ollama, LM Studio, and custom providers
- Read-only provider connection test
- Windows-encrypted API-key storage
- Start, pause, and monitor the background engine
- Visual command editor with duplicate validation and shell warnings
- Optional Mind Palette (`Ctrl+Alt+M`) for transforming selected text
- Optional automatic Palette popup after mouse-dragging or double-clicking selected text
- Automatic English definitions above single-word selections in other apps, skipped while
  you are editing a text field, with an on/off switch in Preferences
- Backspace or Delete dismisses the Palette and erases the original selection
- Customizable Palette actions, order, columns, width, and text preview
- Offline Palette tools for spacing cleanup, line-to-bullet conversion, duplicate-line
  removal, uppercase/lowercase conversion, and writing statistics
- Bundled AI commands for summarizing, extracting action items, translating to English,
  and turning prose into structured bullets
- Local Windows OCR for copied images, with extract, Dhivehi, writing-fix, summary, number-sum, and equation-solving actions
- Optional local English spelling correction after Space, with Conservative, Balanced,
  and Strong modes plus immediate Backspace undo
- Teal, blue, purple, rose, and orange accent colors
- Light, dark, and system themes
- Per-user Windows startup registration
- First-run offer to install into the Programs folder with a Start Menu entry
- Automatic cleanup of runtime folders left behind by a crash or forced shutdown
- Ask from Telegram what sails between two islands today, with departure times, fare
  and how many seats are left, straight from RTL's own API and without an account
- Pair a phone by showing it a QR code, instead of typing an address and a code
- Read the phone's messages on the desktop, searchable, with unread marked - no app
  on the phone and nothing stored on the PC
- Telegram button that turns this PC into a Wi-Fi hotspot for the rooms the router
  does not reach, optionally carrying the home network's own name so a phone moves
  onto it by itself or a name and password of its own, switching off once nothing
  has used it for five minutes, on 2.4 GHz for reach or 5 GHz for speed (Windows
  offers no open hotspot, only WPA2 and WPA3), saying so when Windows is handing
  out addresses for another network and joining devices will fail
- Diagnostics view with no API-key logging

## Privacy

Mind does not include telemetry and does not store a history of transformed text. Realtime
spelling, image text extraction, and offline Palette text tools run locally. Cloud providers
receive text only when you invoke an AI transformation, including an image action such as
translate, fix, or summarize; the image itself is not uploaded. Local replacer commands run
on the computer. If Word definitions is enabled, only the selected word is sent to the
[Datamuse API](https://www.datamuse.com/api/) (with Wiktionary as a fallback); phrases and
sentences are not sent. Definitions may draw on WordNet and Wiktionary, and the popup links
to its source. Shell replacers
execute with the current user's permissions and should only be created from trusted commands.

## Testing

```powershell
python -m unittest discover -s tests -v
python -c "import py_compile; py_compile.compile('SwiftSlate.pyw', doraise=True)"
```

The keyboard-capture and inline-replacement path must also be tested manually on Windows
in Notepad, browsers, Office apps, and messaging clients.

## License and attribution

This project is distributed under the MIT License. See [LICENSE](LICENSE) and
[NOTICE.md](NOTICE.md). SwiftSlate Desktop copyright remains with its original author.
