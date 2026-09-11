#!/usr/bin/env python3
"""
Media Manager
A simple file explorer for macOS that lets you:
  - Browse folders
  - Change the date/time on photos and videos (EXIF date + file dates)
  - Edit song info (title/artist/album/year/genre/track) on MP3/WAV files
  - Add or replace cover art on MP3/WAV files

Run with:  python3 media_manager.py
"""

import os
import io
import sys
import shutil
import subprocess
import tempfile
import datetime
from pathlib import Path

try:
    import customtkinter as ctk
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except ImportError as e:
    print("Missing GUI dependency:", e)
    print("Install with: pip3 install customtkinter")
    sys.exit(1)

try:
    from PIL import Image, ImageOps
except ImportError:
    print("Missing dependency: Pillow. Install with: pip3 install Pillow")
    sys.exit(1)

try:
    import piexif
except ImportError:
    print("Missing dependency: piexif. Install with: pip3 install piexif")
    sys.exit(1)

try:
    from mutagen.id3 import (
        ID3, ID3NoHeaderError, TIT2, TPE1, TALB, TDRC, TCON, TRCK, APIC
    )
    from mutagen.mp3 import MP3
    from mutagen.wave import WAVE
except ImportError:
    print("Missing dependency: mutagen. Install with: pip3 install mutagen")
    sys.exit(1)


# Constants

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".heic", ".bmp"}
EXIF_CAPABLE_EXTS = {".jpg", ".jpeg", ".tiff", ".tif"} 
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
AUDIO_EXTS = {".mp3", ".wav"}

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
SETFILE = shutil.which("SetFile") 


def file_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if path.is_dir():
        return "folder"
    return "other"


def human_size(num_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024:
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


# Metadata helpers - Photos & Video

def get_exif_date(path: Path):
    """Return a datetime from EXIF DateTimeOriginal, or None."""
    if path.suffix.lower() not in EXIF_CAPABLE_EXTS:
        return None
    try:
        exif_dict = piexif.load(str(path))
        raw = exif_dict.get("Exif", {}).get(piexif.ExifIFD.DateTimeOriginal)
        if not raw:
            raw = exif_dict.get("0th", {}).get(piexif.ImageIFD.DateTime)
        if raw:
            text = raw.decode() if isinstance(raw, bytes) else raw
            return datetime.datetime.strptime(text, "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return None


def get_video_date(path: Path):
    """Read creation_time from a video's metadata via ffprobe, if available."""
    if not FFPROBE:
        return None
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "quiet", "-show_entries", "format_tags=creation_time",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=15
        )
        raw = result.stdout.strip()
        if raw:
            raw = raw.replace("Z", "")
            for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
                try:
                    return datetime.datetime.strptime(raw, fmt)
                except ValueError:
                    continue
    except Exception:
        pass
    return None


def get_filesystem_dates(path: Path):
    """Return (modified, created) datetimes for a file."""
    stat = path.stat()
    modified = datetime.datetime.fromtimestamp(stat.st_mtime)
    created_ts = getattr(stat, "st_birthtime", stat.st_ctime)
    created = datetime.datetime.fromtimestamp(created_ts)
    return modified, created


def set_filesystem_dates(path: Path, when: datetime.datetime, set_created: bool = True):
    """Set modified/accessed dates always; try to set creation date on macOS if possible."""
    ts = when.timestamp()
    os.utime(path, (ts, ts)) 
    if set_created and SETFILE:
        # SetFile wants MM/DD/YYYY HH:MM:SS
        stamp = when.strftime("%m/%d/%Y %H:%M:%S")
        try:
            subprocess.run([SETFILE, "-d", stamp, str(path)], check=True,
                           capture_output=True, timeout=15)
        except Exception:
            pass 


def set_exif_date(path: Path, when: datetime.datetime):
    """Write DateTimeOriginal/DateTimeDigitized/DateTime into EXIF."""
    if path.suffix.lower() not in EXIF_CAPABLE_EXTS:
        raise ValueError(
            f"{path.suffix.upper()} files don't support EXIF dates. "
            "Only the file's modified/created date will be changed."
        )
    stamp = when.strftime("%Y:%m:%d %H:%M:%S").encode()
    try:
        exif_dict = piexif.load(str(path))
    except Exception:
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}
    exif_dict.setdefault("Exif", {})
    exif_dict.setdefault("0th", {})
    exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = stamp
    exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = stamp
    exif_dict["0th"][piexif.ImageIFD.DateTime] = stamp
    exif_bytes = piexif.dump(exif_dict)
    piexif.insert(exif_bytes, str(path))


def set_video_date(path: Path, when: datetime.datetime):
    """Rewrite creation_time metadata on a video using ffmpeg (stream copy, no re-encode)."""
    if not FFMPEG:
        raise RuntimeError(
            "ffmpeg is required to edit video metadata dates.\n"
            "Install it with: brew install ffmpeg"
        )
    stamp = when.strftime("%Y-%m-%dT%H:%M:%S")
    fd, tmp_path = tempfile.mkstemp(suffix=path.suffix)
    os.close(fd)
    try:
        cmd = [
            FFMPEG, "-y", "-i", str(path),
            "-c", "copy", "-map_metadata", "0",
            "-metadata", f"creation_time={stamp}",
            tmp_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-800:] if result.stderr else "ffmpeg failed")
        shutil.move(tmp_path, str(path))
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def make_video_thumbnail(path: Path, size=(240, 240)):
    """Extract a frame from a video as a PIL Image, or None if unavailable."""
    if not FFMPEG:
        return None
    fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        cmd = [FFMPEG, "-y", "-i", str(path), "-ss", "00:00:00.5",
               "-frames:v", "1", "-vf", f"scale={size[0]}:-1", tmp_path]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode == 0 and os.path.getsize(tmp_path) > 0:
            img = Image.open(tmp_path)
            img.load()
            return img
    except Exception:
        pass
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return None


# Metadata helpers - Audio

class AudioTags:
    """Small wrapper that reads/writes common tags + cover art for MP3 and WAV."""

    def __init__(self, path: Path):
        self.path = path
        self.ext = path.suffix.lower()
        self._id3 = self._load()

    def _load(self):
        if self.ext == ".mp3":
            try:
                return ID3(str(self.path))
            except ID3NoHeaderError:
                return ID3()
        elif self.ext == ".wav":
            w = WAVE(str(self.path))
            if w.tags is None:
                w.add_tags()
            self._wave = w
            return w.tags
        else:
            raise ValueError(f"Unsupported audio type: {self.ext}")

    @staticmethod
    def _text(frame):
        return str(frame.text[0]) if frame and frame.text else ""

    def get_title(self):
        return self._text(self._id3.get("TIT2"))

    def get_artist(self):
        return self._text(self._id3.get("TPE1"))

    def get_album(self):
        return self._text(self._id3.get("TALB"))

    def get_year(self):
        return self._text(self._id3.get("TDRC"))

    def get_genre(self):
        return self._text(self._id3.get("TCON"))

    def get_track(self):
        return self._text(self._id3.get("TRCK"))

    def get_cover_bytes(self):
        for key in self._id3.keys():
            if key.startswith("APIC"):
                return self._id3[key].data
        return None

    def set_fields(self, title=None, artist=None, album=None, year=None,
                    genre=None, track=None):
        if title is not None:
            self._id3.setall("TIT2", [TIT2(encoding=3, text=title)])
        if artist is not None:
            self._id3.setall("TPE1", [TPE1(encoding=3, text=artist)])
        if album is not None:
            self._id3.setall("TALB", [TALB(encoding=3, text=album)])
        if year is not None:
            self._id3.setall("TDRC", [TDRC(encoding=3, text=year)])
        if genre is not None:
            self._id3.setall("TCON", [TCON(encoding=3, text=genre)])
        if track is not None:
            self._id3.setall("TRCK", [TRCK(encoding=3, text=track)])

    def set_cover(self, image_path: Path):
        img = Image.open(image_path)
        img = ImageOps.exif_transpose(img).convert("RGB")
        # keep cover art reasonably sized
        img.thumbnail((1000, 1000))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        # remove existing pictures first
        for key in list(self._id3.keys()):
            if key.startswith("APIC"):
                del self._id3[key]
        self._id3.add(APIC(encoding=3, mime="image/jpeg", type=3,
                            desc="Cover", data=buf.getvalue()))

    def save(self):
        if self.ext == ".mp3":
            self._id3.save(str(self.path), v2_version=3)
        elif self.ext == ".wav":
            self._wave.save()


# GUI

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

THUMB_SIZE = (220, 220)


class DetailPanel(ctk.CTkFrame):
    """Right-hand panel: shows a preview + editor depending on selected file type."""

    def __init__(self, master, app):
        super().__init__(master, corner_radius=12)
        self.app = app
        self.current_path = None
        self.current_kind = None
        self._thumb_image = None  
        self._pending_cover_path = None
        self._build_empty_state()

    def _clear(self):
        for widget in self.winfo_children():
            widget.destroy()

    def _build_empty_state(self):
        self._clear()
        label = ctk.CTkLabel(
            self, text="Select a photo, video, or audio file\nto view and edit its details.",
            text_color="gray60", font=ctk.CTkFont(size=14), justify="center"
        )
        label.pack(expand=True, padx=20, pady=20)

    def show(self, path: Path):
        self.current_path = path
        self.current_kind = file_kind(path)
        self._pending_cover_path = None
        if self.current_kind == "image":
            self._show_image()
        elif self.current_kind == "video":
            self._show_video()
        elif self.current_kind == "audio":
            self._show_audio()
        else:
            self._build_empty_state()

    # Image / Video (date editing)

    def _thumb_widget(self, pil_image, fallback_text):
        if pil_image is not None:
            pil_image = pil_image.copy()
            pil_image.thumbnail(THUMB_SIZE)
            self._thumb_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image,
                                              size=pil_image.size)
            return ctk.CTkLabel(self, image=self._thumb_image, text="")
        else:
            return ctk.CTkLabel(self, text=fallback_text, width=THUMB_SIZE[0],
                                 height=THUMB_SIZE[1], fg_color="gray20",
                                 text_color="gray70", corner_radius=8)

    def _show_image(self):
        self._clear()
        path = self.current_path
        ctk.CTkLabel(self, text=path.name, font=ctk.CTkFont(size=15, weight="bold"),
                     wraplength=280).pack(pady=(16, 8), padx=16)

        try:
            pil_image = Image.open(path)
            pil_image = ImageOps.exif_transpose(pil_image)
        except Exception:
            pil_image = None
        self._thumb_widget(pil_image, "Preview\nunavailable").pack(pady=8)

        exif_date = get_exif_date(path)
        modified, created = get_filesystem_dates(path)
        current = exif_date or modified

        self._build_date_editor(current, is_photo=True, has_exif=(path.suffix.lower() in EXIF_CAPABLE_EXTS))

    def _show_video(self):
        self._clear()
        path = self.current_path
        ctk.CTkLabel(self, text=path.name, font=ctk.CTkFont(size=15, weight="bold"),
                     wraplength=280).pack(pady=(16, 8), padx=16)

        pil_image = make_video_thumbnail(path)
        self._thumb_widget(pil_image, "Video preview\nneeds ffmpeg\n(brew install ffmpeg)").pack(pady=8)

        video_date = get_video_date(path)
        modified, created = get_filesystem_dates(path)
        current = video_date or modified

        self._build_date_editor(current, is_photo=False, has_exif=False)

        if not FFMPEG:
            ctk.CTkLabel(self, text="ffmpeg not found — only file dates can be changed.\n"
                                     "Install with: brew install ffmpeg",
                         text_color="#e0a030", font=ctk.CTkFont(size=11),
                         wraplength=280, justify="center").pack(pady=(4, 0))

    def _build_date_editor(self, current: datetime.datetime, is_photo: bool, has_exif: bool):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(pady=(12, 4), padx=16, fill="x")

        ctk.CTkLabel(frame, text="Current date:", text_color="gray60",
                     font=ctk.CTkFont(size=12)).grid(row=0, column=0, sticky="w", columnspan=6)
        ctk.CTkLabel(frame, text=current.strftime("%b %d, %Y  %H:%M:%S") if current else "Unknown",
                     font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=1, column=0, sticky="w", columnspan=6, pady=(0, 12))

        ctk.CTkLabel(frame, text="New date:", text_color="gray60",
                     font=ctk.CTkFont(size=12)).grid(row=2, column=0, sticky="w", columnspan=6)

        base = current or datetime.datetime.now()
        self.year_var = tk.StringVar(value=str(base.year))
        self.month_var = tk.StringVar(value=f"{base.month:02d}")
        self.day_var = tk.StringVar(value=f"{base.day:02d}")
        self.hour_var = tk.StringVar(value=f"{base.hour:02d}")
        self.min_var = tk.StringVar(value=f"{base.minute:02d}")
        self.sec_var = tk.StringVar(value=f"{base.second:02d}")

        entries = [
            (self.year_var, 5, "YYYY"), (self.month_var, 3, "MM"), (self.day_var, 3, "DD"),
            (self.hour_var, 3, "HH"), (self.min_var, 3, "MM"), (self.sec_var, 3, "SS"),
        ]
        for i, (var, width, placeholder) in enumerate(entries):
            e = ctk.CTkEntry(frame, textvariable=var, width=width * 10,
                              placeholder_text=placeholder, justify="center")
            e.grid(row=3, column=i, padx=2, pady=(2, 12), sticky="w")

        ctk.CTkButton(frame, text="Set to now", fg_color="gray30", hover_color="gray40",
                      width=100, command=self._set_now).grid(row=4, column=0, columnspan=3,
                                                               sticky="w", pady=(0, 10))

        self.set_created_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(frame, text="Also set file 'created' date (macOS)",
                        variable=self.set_created_var, font=ctk.CTkFont(size=11)).grid(
            row=5, column=0, columnspan=6, sticky="w", pady=(0, 4))

        if is_photo and not has_exif:
            ctk.CTkLabel(frame, text="This format has no EXIF date field — only the\n"
                                      "file's modified/created date will be updated.",
                         text_color="#e0a030", font=ctk.CTkFont(size=11),
                         justify="left").grid(row=6, column=0, columnspan=6, sticky="w", pady=(0, 6))

        apply_btn = ctk.CTkButton(self, text="Apply Date", command=self._apply_date)
        apply_btn.pack(pady=(8, 16))

    def _set_now(self):
        now = datetime.datetime.now()
        self.year_var.set(str(now.year))
        self.month_var.set(f"{now.month:02d}")
        self.day_var.set(f"{now.day:02d}")
        self.hour_var.set(f"{now.hour:02d}")
        self.min_var.set(f"{now.minute:02d}")
        self.sec_var.set(f"{now.second:02d}")

    def _apply_date(self):
        try:
            when = datetime.datetime(
                int(self.year_var.get()), int(self.month_var.get()), int(self.day_var.get()),
                int(self.hour_var.get()), int(self.min_var.get()), int(self.sec_var.get())
            )
        except ValueError as e:
            messagebox.showerror("Invalid date", f"Please check the date/time values.\n{e}")
            return

        path = self.current_path
        try:
            if self.current_kind == "image":
                if path.suffix.lower() in EXIF_CAPABLE_EXTS:
                    set_exif_date(path, when)
                set_filesystem_dates(path, when, set_created=self.set_created_var.get())
            elif self.current_kind == "video":
                set_video_date(path, when)
                set_filesystem_dates(path, when, set_created=self.set_created_var.get())
        except Exception as e:
            messagebox.showerror("Couldn't update date", str(e))
            return

        self.app.set_status(f"Updated date for {path.name}")
        self.app.refresh_file_list(keep_selection=path)

    # Audio (tags + cover art)

    def _show_audio(self):
        self._clear()
        path = self.current_path
        ctk.CTkLabel(self, text=path.name, font=ctk.CTkFont(size=15, weight="bold"),
                     wraplength=280).pack(pady=(16, 8), padx=16)

        try:
            self._audio_tags = AudioTags(path)
        except Exception as e:
            ctk.CTkLabel(self, text=f"Couldn't read tags:\n{e}", text_color="#e05050",
                         wraplength=280, justify="center").pack(pady=20)
            return

        cover_bytes = self._audio_tags.get_cover_bytes()
        cover_img = None
        if cover_bytes:
            try:
                cover_img = Image.open(io.BytesIO(cover_bytes))
            except Exception:
                cover_img = None

        self._cover_label = self._thumb_widget(cover_img, "No cover art")
        self._cover_label.pack(pady=8)

        ctk.CTkButton(self, text="Change Cover Art…", command=self._pick_cover,
                      fg_color="gray30", hover_color="gray40").pack(pady=(0, 12))

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="x", padx=16)

        self.title_var = tk.StringVar(value=self._audio_tags.get_title())
        self.artist_var = tk.StringVar(value=self._audio_tags.get_artist())
        self.album_var = tk.StringVar(value=self._audio_tags.get_album())
        self.year_var2 = tk.StringVar(value=self._audio_tags.get_year())
        self.genre_var = tk.StringVar(value=self._audio_tags.get_genre())
        self.track_var = tk.StringVar(value=self._audio_tags.get_track())

        fields = [
            ("Title", self.title_var), ("Artist", self.artist_var),
            ("Album", self.album_var), ("Year", self.year_var2),
            ("Genre", self.genre_var), ("Track #", self.track_var),
        ]
        for label, var in fields:
            ctk.CTkLabel(form, text=label, text_color="gray60",
                         font=ctk.CTkFont(size=11)).pack(anchor="w", pady=(6, 0))
            ctk.CTkEntry(form, textvariable=var).pack(fill="x", pady=(2, 0))

        ctk.CTkButton(self, text="Save Tags", command=self._save_audio).pack(pady=18)

    def _pick_cover(self):
        file_path = filedialog.askopenfilename(
            title="Choose cover art image",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.tiff")]
        )
        if not file_path:
            return
        self._pending_cover_path = Path(file_path)
        try:
            preview = Image.open(self._pending_cover_path)
        except Exception as e:
            messagebox.showerror("Couldn't open image", str(e))
            return
        preview = preview.copy()
        preview.thumbnail(THUMB_SIZE)
        self._thumb_image = ctk.CTkImage(light_image=preview, dark_image=preview, size=preview.size)
        self._cover_label.configure(image=self._thumb_image, text="")

    def _save_audio(self):
        try:
            self._audio_tags.set_fields(
                title=self.title_var.get(), artist=self.artist_var.get(),
                album=self.album_var.get(), year=self.year_var2.get(),
                genre=self.genre_var.get(), track=self.track_var.get(),
            )
            if self._pending_cover_path is not None:
                self._audio_tags.set_cover(self._pending_cover_path)
            self._audio_tags.save()
        except Exception as e:
            messagebox.showerror("Couldn't save tags", str(e))
            return
        self.app.set_status(f"Saved tags for {self.current_path.name}")


class FileExplorerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Media Manager")
        self.geometry("1080x680")
        self.minsize(820, 520)

        self.current_dir = Path.home()

        self._build_layout()
        self.refresh_file_list()

    # Layout

    def _build_layout(self):
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(1, weight=1)

        # Top bar
        top = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(12, 6))
        top.grid_columnconfigure(2, weight=1)

        ctk.CTkButton(top, text="⌂ Home", width=80, command=self._go_home).grid(row=0, column=0, padx=(0, 6))
        ctk.CTkButton(top, text="↑ Up", width=60, command=self._go_up).grid(row=0, column=1, padx=(0, 6))

        self.path_var = tk.StringVar()
        path_entry = ctk.CTkEntry(top, textvariable=self.path_var)
        path_entry.grid(row=0, column=2, sticky="ew", padx=(0, 6))
        path_entry.bind("<Return>", self._go_to_typed_path)

        ctk.CTkButton(top, text="Choose Folder…", width=130,
                      command=self._choose_folder).grid(row=0, column=3)

        # File list (left)
        list_frame = ctk.CTkFrame(self, corner_radius=12)
        list_frame.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(0, 6))
        list_frame.grid_rowconfigure(0, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)

        style = ttk.Style()
        style.theme_use("default")
        is_dark = ctk.get_appearance_mode() == "Dark"
        bg = "#2b2b2b" if is_dark else "#ffffff"
        fg = "#e5e5e5" if is_dark else "#1a1a1a"
        style.configure("Treeview", background=bg, fieldbackground=bg, foreground=fg,
                         rowheight=26, borderwidth=0, font=("Helvetica", 12))
        style.configure("Treeview.Heading", font=("Helvetica", 12, "bold"))
        style.map("Treeview", background=[("selected", "#3a7ebf")])

        columns = ("kind", "modified", "size")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="tree headings", style="Treeview")
        self.tree.heading("#0", text="Name")
        self.tree.heading("kind", text="Kind")
        self.tree.heading("modified", text="Date Modified")
        self.tree.heading("size", text="Size")
        self.tree.column("#0", width=320)
        self.tree.column("kind", width=90, anchor="center")
        self.tree.column("modified", width=150, anchor="center")
        self.tree.column("size", width=90, anchor="e")

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)
        vsb.grid(row=0, column=1, sticky="ns", pady=4)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double_click)

        # Detail panel (right)
        self.detail_panel = DetailPanel(self, self)
        self.detail_panel.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=(0, 6))

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status = ctk.CTkLabel(self, textvariable=self.status_var, anchor="w",
                               text_color="gray60", font=ctk.CTkFont(size=11))
        status.grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 8))

    # Navigation

    def _go_home(self):
        self.current_dir = Path.home()
        self.refresh_file_list()

    def _go_up(self):
        self.current_dir = self.current_dir.parent
        self.refresh_file_list()

    def _choose_folder(self):
        folder = filedialog.askdirectory(initialdir=str(self.current_dir))
        if folder:
            self.current_dir = Path(folder)
            self.refresh_file_list()

    def _go_to_typed_path(self, _event=None):
        candidate = Path(self.path_var.get()).expanduser()
        if candidate.is_dir():
            self.current_dir = candidate
            self.refresh_file_list()
        else:
            self.set_status(f"Not a folder: {candidate}")

    # File list

    def refresh_file_list(self, keep_selection: Path = None):
        self.path_var.set(str(self.current_dir))
        self.tree.delete(*self.tree.get_children())

        try:
            entries = sorted(
                self.current_dir.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower())
            )
        except PermissionError:
            self.set_status(f"Permission denied: {self.current_dir}")
            entries = []
        except FileNotFoundError:
            self.set_status(f"Folder not found: {self.current_dir}")
            entries = []

        icon = {"folder": "📁", "image": "🖼", "video": "🎬", "audio": "🎵", "other": "📄"}
        select_iid = None
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                kind = file_kind(entry)
                stat = entry.stat()
                modified = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                size = "—" if entry.is_dir() else human_size(stat.st_size)
            except (PermissionError, FileNotFoundError):
                continue

            label = f"{icon.get(kind, '📄')}  {entry.name}"
            iid = self.tree.insert("", "end", text=label,
                                    values=(kind.capitalize(), modified, size),
                                    tags=(str(entry),))
            if keep_selection is not None and entry == keep_selection:
                select_iid = iid

        if select_iid:
            self.tree.selection_set(select_iid)
            self.tree.see(select_iid)
            self._on_select()

    def _selected_path(self):
        selection = self.tree.selection()
        if not selection:
            return None
        tags = self.tree.item(selection[0], "tags")
        return Path(tags[0]) if tags else None

    def _on_select(self, _event=None):
        path = self._selected_path()
        if path is None:
            return
        if path.is_file():
            self.detail_panel.show(path)

    def _on_double_click(self, _event=None):
        path = self._selected_path()
        if path is not None and path.is_dir():
            self.current_dir = path
            self.refresh_file_list()

    # Status

    def set_status(self, text: str):
        self.status_var.set(text)


def _check_tk_version():
    """macOS ships a very old, buggy system Tk (8.5) that renders completely
    blank windows on modern macOS instead of erroring out. Catch that early
    with a clear message instead of a silent blank window."""
    tk_version = tk.TkVersion
    if tk_version < 8.6:
        message = (
            f"Media Manager needs Tk 8.6 or newer to display its window "
            f"correctly, but this Python is using Tk {tk_version}.\n\n"
            "This usually happens with macOS's built-in Python. Fix it by "
            "installing a Python with a modern Tk, e.g. in Terminal:\n\n"
            "    brew install python-tk\n\n"
            "Then run this app again using that Python "
            "(the included launcher does this automatically)."
        )
        print("=" * 70)
        print("MEDIA MANAGER — CANNOT START")
        print("=" * 70)
        print(message)
        print("=" * 70)
        # Use a native macOS dialog (not Tk) since Tk itself is the broken part.
        if sys.platform == "darwin" and shutil.which("osascript"):
            safe_msg = message.replace('"', '\\"')
            subprocess.run([
                "osascript", "-e",
                f'display alert "Outdated Tk version" message "{safe_msg}"'
            ])
        sys.exit(1)


def main():
    _check_tk_version()
    app = FileExplorerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
