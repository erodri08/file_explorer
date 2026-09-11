# Media Manager

A simple macOS desktop app for managing media files.

* **Photos & videos:** Change file dates and media metadata
* **MP3/WAV:** Edit title, artist, album, year, genre, and track number
* **MP3/WAV:** Add or replace cover art

## Setup

### 1. Install Python

Check if Python 3 is installed:

```bash
python3 --version
```

For the best macOS compatibility, install Python and Tk with Homebrew:

```bash
brew install python-tk
```

If you don't have Homebrew, install it from [brew.sh](https://brew.sh?utm_source=chatgpt.com).

### 2. Install ffmpeg (recommended)

ffmpeg enables video thumbnails and lets the app edit dates stored inside video files.

```bash
brew install ffmpeg
```

The app works without ffmpeg, but video thumbnails and internal video date editing will be unavailable.

### 3. Run the app

Double-click:

**`Run Media Manager.command`**

The launcher automatically creates a `.venv` and installs the required dependencies the first time you run it.

If macOS blocks the launcher, right-click it → **Open** → **Open**.

## Using the App

1. Click **Choose Folder…** to find your media files.
2. Select a file to view its details.
3. **Photos/Videos:** Enter a new date and click **Apply Date**.
4. **MP3/WAV:** Edit the song information and click **Save Tags**.
5. Use **Change Cover Art…** to add or replace album artwork.

## Date Editing

For supported files, **Apply Date** can update:

* Photo EXIF date
* Video internal creation date
* macOS file Modified date
* macOS file Created date when Apple's `SetFile` tool is available

JPEG and TIFF files support EXIF dates. Other image formats only have their file date changed.

Video dates require **ffmpeg**. Videos are not re-encoded, so there is no quality loss.

## Notes

* PNG, BMP, and HEIC files do not support EXIF dates.
* Cover art is converted to JPEG and stored directly in the MP3/WAV file.
* Hidden files (`.` files) are not displayed.
* Changing a video's internal date may take some time because the file must be rewritten.

## Troubleshooting

**Blank window / deprecated Tk warning**

Install modern Tk:

```bash
brew install python-tk
```

Then run the app using `Run Media Manager.command`.

**`No module named customtkinter` (or another dependency)**

Use the launcher instead of running the script directly. If using Terminal:

```bash
.venv/bin/python3 media_manager.py
```

**`externally-managed-environment`**

Don't use `--break-system-packages`. The launcher creates a `.venv` specifically to avoid this issue.

**Video date or thumbnail not working**

Install ffmpeg:

```bash
brew install ffmpeg
```

**macOS Created date isn't changing**

Install Apple's `SetFile` tool:

```bash
xcode-select --install
```
