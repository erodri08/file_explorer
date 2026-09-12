# Media Manager

A macOS app for browsing files and editing metadata.

### Supported Files

* **Photos:** JPG, TIFF, PNG, BMP, HEIC, WEBP
* **Videos:** MP4, MOV, MKV, AVI
* **Audio:** MP3, WAV, FLAC, OGG, M4A
* **Documents:** PDF, DOCX, XLSX, PPTX

Features include metadata editing, cover art, image conversion, file renaming, date editing, sorting, and previews.

## Setup

Install Python with Tk:

```bash
brew install python-tk
```

Install **ffmpeg** for video metadata and thumbnails:

```bash
brew install ffmpeg
```

Install **poppler** for PDF thumbnails (optional):

```bash
brew install poppler
```

### Run the App

Double-click **`Run Media Manager.command`**.

On first run, the launcher will:

1. Find a compatible Python installation.
2. Create a `.venv` in the app folder.
3. Install the required dependencies.
4. Launch the app.

After setup, simply double-click the launcher to start the app.

If macOS blocks the file, right-click → **Open** → **Open**.

### Manual Setup

If you prefer to run it from Terminal:

```bash
cd /path/to/media-manager
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python3 media_manager.py
```

## Usage

1. Choose a folder and select a file.
2. Edit the available metadata, filename, or date.
3. Use **Convert Format** for images or **Change Cover Art** for audio.
4. Click **Save Changes**.

## Notes

* Image conversion creates a new file and keeps the original.
* Video metadata editing requires ffmpeg.
* PNG/BMP/WEBP do not support embedded EXIF dates.
* DOCX/XLSX/PPTX files must be closed before saving.
* Changing the macOS Created date requires `SetFile`:

  ```bash
  xcode-select --install
  ```
