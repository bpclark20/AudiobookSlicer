# AudioBookSlicer: A collection of tools to create an MP3 audiobook file that includes proper chapter markers with associated cover images.

The toolkit includes:
- A **PyQt6 GUI** (`gui.py`) that converts a WAV with Adobe Audition markers into a chaptered MP3, with a **Tools** menu for the helper scripts below
- `split_m4b_chapters.py`: split a DRM-free `.m4b`/`.m4a` into one WAV per chapter
- `wav_markers_to_mp3.py`: encode a marker WAV into a chaptered MP3 with per-chapter artwork
- `strip_chapter_number_prefix.py`, `prefix_book_title.py`, `rename_chapters_offset.py`: batch file-rename helpers

All tools use `ffprobe`/`ffmpeg` for audio work.

## AudioBook Slicer GUI

A PyQt6 graphical interface for converting WAV files with Adobe Audition markers into chaptered MP3 files.

### GUI Features

- **Import WAV files** with chapter markers (Adobe Audition cue point format) via **File → Import Adobe Audition WAV File…** (`Ctrl+O`)
- **Edit chapter titles** in-place (double-click any title to edit)
- **Reorder chapters** via drag/drop in the chapter list
- **Preview chapter artwork** by selecting chapters in the list
- **Live encode progress** with per-chapter progress bars
- **Adjust encoding options**: VBR quality, CBR bitrate, mono/stereo
- **Size limit warnings**: Specify file size limit with unit selection (bytes, KB, MB, GB)
- **Persistent settings**: Last-opened directories and the last chapter offset are remembered between sessions
- **Reset controls** in the **File** menu:
  - **Reset Markers**: restore the chapter list to the original WAV markers (available only once a WAV with markers is loaded)
  - **Reset**: clear the loaded file and return every setting to its default
- **Tools menu** for the helper scripts (see [Tools Menu](#tools-menu))

The path of the loaded WAV is shown in the window title.

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
2. Enter your **Book Title** (used for the output filename and MP3 tags)
3. Edit chapter titles by double-clicking them in the table
4. (Optional) **Drag chapters** to reorder them. The output is encoded in your custom order
5. (Optional) Place cover images in the same folder as the WAV (e.g., `Book Title.jpg`, `Book Title.png`)
6. Click **Process Covers** to associate artwork with chapters
7. Choose encoding options (VBR/CBR, mono/stereo)
8. Set a **Size Limit** if desired (file size warning threshold)
9. Click **Encode MP3**
10. Watch live progress bars update as the MP3 is encoded with chapter boundaries

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

## Prerequisites

1. Python 3.9+
2. FFmpeg (must include both `ffmpeg` and `ffprobe`)
3. PyQt6 (GUI only)

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
