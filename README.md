# AudioBookSlicer: A collection of tools to create an MP3 or M4B audiobook file that includes proper chapter markers with associated cover images.

The toolkit includes:
- A **PyQt6 GUI** (`gui.py`) that turns a WAV with Adobe Audition markers, or a list of chapter WAVs, into a chaptered MP3 or iPod-compatible M4B, with a **Tools** menu for the helper scripts below
- `split_m4b_chapters.py`: split a DRM-free `.m4b`/`.m4a` into one WAV per chapter
- `wav_markers_to_mp3.py`: encode a marker WAV into a chaptered MP3 with per-chapter artwork
- `strip_chapter_number_prefix.py`, `prefix_book_title.py`, `rename_chapters_offset.py`: batch file-rename helpers

All tools use `ffprobe`/`ffmpeg` for audio work.

## AudioBook Slicer GUI

A PyQt6 graphical interface for turning WAV files into chaptered MP3 or M4B audiobooks.

### GUI Features

- **Import WAV files** with chapter markers (Adobe Audition cue point format) via **File → Import Adobe Audition WAV File…** (`Ctrl+O`)
- **Build a chapter list from individual WAV files** via **File → Add WAV Files…**. Each file becomes one chapter, and the files are joined in table order when you encode
- **Playlists** (`.abspl`): **File → Save Playlist…** (`Ctrl+S`) saves the current file order. **File → Open Playlist…** loads it again. A playlist is a UTF-8 text file: a `# AudioBookSlicer playlist v1` header followed by one WAV path per line. `.m3u`/`.m3u8`/`.txt` lists also open
- **Remove Selected Chapter** (File menu) drops a row from a file-based chapter list
- **Edit chapter titles** in-place (double-click any title to edit)
- **Reorder chapters** via drag/drop in the chapter list
- **Preview chapter artwork** by selecting chapters in the list
- **Preview chapter audio**: select a chapter and click **Start Preview**. Playback continues into the next chapter. Click inside the playing chapter's title to seek. The output device is chosen in **Tools → Options…**
- **Output format**: **MP3** (LAME) or **M4B** (AAC, iPod-compatible). See [MP3 vs M4B](#mp3-vs-m4b)
- **Encoding options**: VBR quality level (0 best to 9 smallest) or CBR bitrate, mono/stereo. Each format remembers its own settings
- **Live encode progress** with per-chapter progress bars. **Cancel Encode** stops ffmpeg immediately and removes partial files
- **Intermediate WAV reuse**: when chapters are joined or reordered, you can keep the joined WAV after encoding. A second encode with the same chapter order (for example MP3 and then M4B) skips rebuilding it. It is deleted on quit, Reset, or when the order changes
- **Size limit warnings**: Specify file size limit with unit selection (bytes, KB, MB, GB)
- **Options** (**Tools → Options…**): default format and encode settings, size limit, and preview device. Defaults load at startup and on **Reset**. **OK** also applies them to the current window
- **Persistent settings**: Options, last-opened directories, and the last chapter offset are remembered between sessions
- **Reset controls** in the **File** menu:
  - **Reset Markers**: restore the chapter list to the original WAV markers, or to the order the files or playlist were loaded in
  - **Reset**: clear the loaded file and return the encode settings to your saved defaults
- **Tools menu** for the helper scripts (see [Tools Menu](#tools-menu))
- **Quit protection**: quitting during an encode asks first, and an unsaved chapter order offers to save a playlist

The path of the loaded WAV or playlist is shown in the window title.

### Running the GUI

The GUI requires PyQt6 to be installed in the project's virtual environment.

**Activate the virtual environment and run:**

```powershell
& '.venv\Scripts\Activate.ps1'
python gui.py
```

**Or use the venv Python directly:**

```powershell
& '.venv\Scripts\python.exe' gui.py
```

**Create a shortcut (optional):**

Create `run_gui.ps1`:
```powershell
& '.venv\Scripts\Activate.ps1'
python gui.py
```

Then double-click `run_gui.ps1` to launch.

### GUI Workflow

1. Choose **File → Import Adobe Audition WAV File…** and select a WAV file with chapter markers
2. Enter your **Book Title** (used for the output filename and tags)
3. Edit chapter titles by double-clicking them in the table
4. (Optional) **Drag chapters** to reorder them. The output is encoded in your custom order
5. (Optional) Place cover images in the same folder as the WAV (e.g., `Book Title.jpg`, `Book Title.png`)
6. Click **Process Covers** to associate artwork with chapters
7. Choose the **Format** (MP3 or M4B) and encoding options (VBR/CBR, mono/stereo)
8. Set a **Size Limit** if desired (file size warning threshold)
9. Click **Encode MP3** / **Encode M4B**
10. Watch live progress bars update as the file is encoded with chapter boundaries

**From individual WAV files or a playlist:** use **File → Add WAV Files…** or **File → Open Playlist…** in place of step 1. Opening a playlist sets the Book Title and output folder from the playlist's name and location. Cover images are looked up next to each chapter WAV (`Book Title.jpg`, or `Cover.jpg` as a fallback), so chapters from different books can use different covers. Use **File → Save Playlist…** to keep a custom order for later.

### Tools Menu

Each tool asks for its input, runs the matching script, and shows the console output in a separate window. You can keep working in the main window while a tool runs.

| Menu item | Prompts for | Script |
|---|---|---|
| **Prefix Book Title to Files…** | Folder, book title (defaults to the current Book Title) | `prefix_book_title.py` |
| **Offset Chapter Numbers…** | Folder, offset to subtract | `rename_chapters_offset.py` |
| **Split M4B into WAV Files…** | `.m4b`/`.m4a` file, output folder (default or custom) | `split_m4b_chapters.py` |
| **Strip Chapter Number Prefix From WAV Files…** | Folder | `strip_chapter_number_prefix.py` |

The rename tools run in **preview mode** first. When the preview succeeds, click **Apply Changes** in the output window to rename the files. If you close the window without applying, nothing changes.

**Split M4B into WAV Files** asks where to write the WAVs after you pick the audiobook:
- **Use Default**: `<book>_chapters` next to the selected file
- **Choose Folder…**: any folder you pick (remembered for next time)

The split then runs immediately; there is no preview step.

**Rename WAV Files From Chapter Titles…** (file-based chapter lists only) renames each chapter's source WAV to match its **Title** column, along with a matching `.pkf` peak file. It shows the planned renames and asks before changing anything. If a playlist is open, save it afterwards so it points at the new names.

### MP3 vs M4B

| | MP3 | M4B |
|---|---|---|
| Codec | MP3 (LAME) | AAC-LC (FFmpeg's built-in encoder) |
| Chapters | ID3 `CHAP`/`CTOC` | QuickTime chapter track (read by iPods and Apple Books) plus Nero `chpl` |
| Artwork | One cover per chapter, so every book title needs a cover | One cover for the whole file (the first chapter's cover) |
| Players | Most apps; **iPods ignore MP3 chapters** | iPod Classic/Nano, Apple Books, most audiobook apps |

M4B files are tagged with the Book Title and **media kind = Audiobook**, so iTunes/Finder file them under Audiobooks and the iPod remembers your position. Sample rates above 48 kHz are lowered to 48 kHz for iPod playback.

**M4B VBR quality levels** use FFmpeg AAC's VBR mode on the same 0 to 9 scale as MP3. Measured bitrates on mono speech:

| Level | 0 | 1 | 2 | 3 | 4 | 5 | 6 | **7 (default)** | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|---|
| kbps | 190 | 160 | 125 | 110 | 92 | 86 | 73 | **60** | 54 | 43 |

Levels 8 and 9 save space partly by cutting treble. FFmpeg's AAC VBR mode trims high frequencies at low settings, while CBR 32 to 48 kbps mono kept the full frequency range in measurements (not a listening test). Try a chapter both ways if file size matters most. For comparison, MP3 VBR level 5 measured about 74 kbps on the same mono speech.

## Prerequisites

1. Python 3.9+
2. FFmpeg (must include both `ffmpeg` and `ffprobe`; `ffplay` is used for preview only if QtMultimedia is unavailable)
3. PyQt6 (GUI only)
4. `mutagen` (MP3 chapter artwork and M4B tags)

### Building the Windows executable

With the virtual environment active, run `pyinstaller AudioBookSlicer.spec`. The app is written to `dist/AudioBookSlicer/AudioBookSlicer.exe`. FFmpeg is not bundled; it must be on `PATH` or next to the exe. The Tools menu works in the built exe: it relaunches itself with `--run-tool <script>` to run each helper.

### Windows FFmpeg install options

1. `winget install Gyan.FFmpeg`
2. Or download a build from https://www.gyan.dev/ffmpeg/builds/ and add its `bin` folder to `PATH`

Verify:

```powershell
ffmpeg -version
ffprobe -version
```

## Split M4B Into Chapter WAV Files

```powershell
python .\split_m4b_chapters.py "C:\path\to\book.m4b"
```

Default behavior:
- Creates folder `<input_stem>_chapters` in the current directory
- Writes files like `001 - Chapter Name.wav`
- Decodes each chapter to PCM WAV at the source sample rate and bit depth

### Common options

```powershell
# Custom output folder
python .\split_m4b_chapters.py "book.m4b" -o ".\out"

# Show commands only (no file writes)
python .\split_m4b_chapters.py "book.m4b" --dry-run

# Overwrite existing output files instead of creating unique names
python .\split_m4b_chapters.py "book.m4b" --overwrite

# Select a different audio stream
python .\split_m4b_chapters.py "book.m4b" --audio-stream 1

# If ffmpeg/ffprobe are not on PATH
python .\split_m4b_chapters.py "book.m4b" --ffmpeg "C:\ffmpeg\bin\ffmpeg.exe" --ffprobe "C:\ffmpeg\bin\ffprobe.exe"
```

## Convert Marker WAV To Chaptered MP3

The GUI is the recommended frontend for this script.

If you have a single combined WAV with Adobe Audition markers (RIFF cue markers),
you can encode it to chaptered MP3 and carry chapters plus per-chapter artwork over.
The script expects chapter titles in the format `Book Title - *` and matching
cover images like `Book Title.jpg` or `Book Title.png` next to the WAV:

```powershell
python .\wav_markers_to_mp3.py "C:\path\to\combined.wav"
```

Default behavior:
- Reads WAV cue markers/labels and creates MP3 chapter tags
- Requires matching cover images before encoding starts
- Downmixes to mono (spoken-word friendly)
- Encodes as VBR MP3 with `-q:a 6`
- Preserves the input sample rate
- Writes `<input_stem>.mp3` next to input
- Warns if final output exceeds the effective 1.5% headroom budget, and again if it exceeds `1.0 GB`

Validation and inspection options:

```powershell
# Validate inputs and print VBR sizing notes, then continue converting
python .\wav_markers_to_mp3.py "combined.wav" --estimate

# Validate inputs only (no conversion)
python .\wav_markers_to_mp3.py "combined.wav" --estimate-only

# Change VBR quality and max-size warning threshold
python .\wav_markers_to_mp3.py "combined.wav" --vbr-quality 4 --max-size-gb 1.0

# Print marker/chapter timing debug info before conversion
python .\wav_markers_to_mp3.py "combined.wav" --debug-markers

# Preserve stereo instead of default mono downmix
python .\wav_markers_to_mp3.py "combined.wav" --stereo
```

You can also pass explicit FFmpeg paths if needed:

```powershell
python .\wav_markers_to_mp3.py "combined.wav" --ffmpeg "C:\ffmpeg\bin\ffmpeg.exe" --ffprobe "C:\ffmpeg\bin\ffprobe.exe"
```

## File Rename Helpers

These scripts share the same safety behavior:
- **Preview by default.** Nothing is renamed until you pass `--apply`.
- **Fail before renaming.** If any file has an unexpected name format, two files would get the same name, or a target name already exists, the script stops and no files are renamed.
- **Two-pass rename.** Each file is first moved to a temporary name, so swapping or shifting names within a folder is safe.
- `--filter` takes a filename glob (for example, `*.wav`).

### Strip Chapter Number Prefix From WAV Files

If you have files named like `024 - Chapter 24.wav`, `024 - Chapter 24 - Part 1.wav`,
`000 - Prologue.wav`, or `040 - Epilogue.wav`, you can remove the leading numeric
prefix with:

```powershell
# Preview only (current folder)
python .\strip_chapter_number_prefix.py

# Target a specific folder
python .\strip_chapter_number_prefix.py "C:\path\to\chapter\folder"
python .\strip_chapter_number_prefix.py "C:\path\to\chapter\folder" --apply

# Include subfolders
python .\strip_chapter_number_prefix.py "C:\path\to\library" --recurse
python .\strip_chapter_number_prefix.py "C:\path\to\library" --recurse --apply
```

### Prefix Book Title To Files

To prepend the book title to every filename in a folder, including
sidecar files like `.pkf`, run:

```powershell
# Preview only; you are prompted for the title
python .\prefix_book_title.py "C:\path\to\chapter\folder"

# Pass the title directly and rename
python .\prefix_book_title.py "C:\path\to\chapter\folder" --title "Role Model" --apply
```

Files that already start with `<title> - ` are skipped.

Example:

```text
Chapter 1.wav -> Role Model - Chapter 1.wav
Chapter 1.pkf -> Role Model - Chapter 1.pkf
```

### Offset Chapter Numbers

For files named `<NNN> - Chapter <N>.wav`, subtract an offset from both numbers:

```powershell
# Preview only (default offset: 29)
python .\rename_chapters_offset.py "C:\path\to\chapter\folder" --offset 29

# Rename
python .\rename_chapters_offset.py "C:\path\to\chapter\folder" --offset 29 --apply
```

Example (offset 29):

```text
030 - Chapter 30.wav -> 001 - Chapter 1.wav
```

## Notes

- Input must be DRM-free.
- If your file has multiple audio streams, use `--audio-stream` to select one.
