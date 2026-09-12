#!/usr/bin/env python3
"""
Media Manager
A simple file explorer for macOS that lets you view and edit metadata on
common file types, all from one easy-to-read panel:

  - Photos (JPEG/TIFF/PNG/etc.): date taken, camera info, description, copyright
  - Videos (MP4/MOV/MKV/AVI):    date, title, artist, album, genre, comment
  - Audio (MP3/WAV/FLAC/OGG/M4A): title, artist, album, year, genre, track,
                                   composer, comment, and cover art
  - PDF documents:                title, author, subject, keywords, creator
  - Word/Excel/PowerPoint files:  title, author, subject, keywords, comments
  - Any other file:               its Modified/Created date on disk

Run with:  python3 media_manager.py
"""

import os
import io
import sys
import json
import base64
import shutil
import threading
import subprocess
import tempfile
import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict

try:
    import customtkinter as ctk
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except ImportError as e:
    print("Missing GUI dependency:", e)
    print("Install with: pip3 install customtkinter")
    sys.exit(1)

try:
    from PIL import Image, ImageOps, ImageTk
    from PIL.PngImagePlugin import PngInfo
except ImportError:
    print("Missing dependency: Pillow. Install with: pip3 install Pillow")
    sys.exit(1)

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:
    HEIF_SUPPORTED = False

try:
    import piexif
except ImportError:
    print("Missing dependency: piexif. Install with: pip3 install piexif")
    sys.exit(1)

try:
    from mutagen.id3 import (
        ID3, ID3NoHeaderError, TIT2, TPE1, TALB, TPE2, TDRC, TCON, TRCK,
        TCOM, COMM, APIC
    )
    from mutagen.mp3 import MP3
    from mutagen.wave import WAVE
    from mutagen.flac import FLAC, Picture
    from mutagen.oggvorbis import OggVorbis
    from mutagen.mp4 import MP4, MP4Cover
except ImportError:
    print("Missing dependency: mutagen. Install with: pip3 install mutagen")
    sys.exit(1)

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    print("Missing dependency: pypdf. Install with: pip3 install pypdf")
    sys.exit(1)

try:
    from docx import Document
except ImportError:
    print("Missing dependency: python-docx. Install with: pip3 install python-docx")
    sys.exit(1)

try:
    from openpyxl import load_workbook
except ImportError:
    print("Missing dependency: openpyxl. Install with: pip3 install openpyxl")
    sys.exit(1)

try:
    from pptx import Presentation
except ImportError:
    print("Missing dependency: python-pptx. Install with: pip3 install python-pptx")
    sys.exit(1)

# Constants

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".heic", ".bmp", ".webp"}
EXIF_CAPABLE_EXTS = {".jpg", ".jpeg", ".tiff", ".tif"}
PNG_EXTS = {".png"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}
PDF_EXTS = {".pdf"}
DOCX_EXTS = {".docx"}
XLSX_EXTS = {".xlsx"}
PPTX_EXTS = {".pptx"}
OFFICE_EXTS = DOCX_EXTS | XLSX_EXTS | PPTX_EXTS

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
PDFTOPPM = shutil.which("pdftoppm")
SETFILE = shutil.which("SetFile")  

# Image formats we can convert between, and the file extension each one uses.
CONVERTIBLE_IMAGE_FORMATS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "TIFF": ".tiff",
    "BMP": ".bmp",
    "WEBP": ".webp",
}

LIST_THUMB_SIZE = (28, 28)


def file_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if path.is_dir():
        return "folder"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in DOCX_EXTS:
        return "docx"
    if ext in XLSX_EXTS:
        return "xlsx"
    if ext in PPTX_EXTS:
        return "pptx"
    return "other"


def human_size(num_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024:
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


# Filesystem date helpers (apply to any file)
def get_filesystem_dates(path: Path):
    stat = path.stat()
    modified = datetime.datetime.fromtimestamp(stat.st_mtime)
    created_ts = getattr(stat, "st_birthtime", stat.st_ctime)
    created = datetime.datetime.fromtimestamp(created_ts)
    return modified, created


def set_filesystem_dates(path: Path, when: datetime.datetime, set_created: bool = True):
    ts = when.timestamp()
    os.utime(path, (ts, ts))
    if set_created and SETFILE:
        stamp = when.strftime("%m/%d/%Y %H:%M:%S")
        try:
            subprocess.run([SETFILE, "-d", stamp, str(path)], check=True,
                           capture_output=True, timeout=15)
        except Exception:
            pass


# Field spec used to describe an editable metadata field in the UI

@dataclass
class FieldSpec:
    key: str
    label: str
    value: str = ""


# Metadata handlers — one per file type, all sharing a common interface:
#   preview()                -> PIL.Image or None
#   preview_placeholder()    -> str shown when there's no preview image
#   supports_embedded_date() -> bool (does this format store a date inside it?)
#   get_embedded_date()      -> datetime or None
#   embedded_date_note()     -> str explaining any limitation, or None
#   fields()                 -> list[FieldSpec] of other editable metadata
#   supports_cover_art()     -> bool
#   get_cover_bytes()        -> bytes or None
#   save(values, date, cover_path) -> writes everything in one go

class BaseHandler:
    def __init__(self, path: Path):
        self.path = path

    def preview(self):
        return None

    def preview_placeholder(self):
        return "No preview\navailable"

    def supports_embedded_date(self):
        return False

    def get_embedded_date(self):
        return None

    def embedded_date_note(self):
        return None

    def fields(self) -> List[FieldSpec]:
        return []

    def supports_cover_art(self):
        return False

    def get_cover_bytes(self):
        return None

    def save(self, values: Dict[str, str], date: Optional[datetime.datetime], cover_path: Optional[Path]):
        pass


# Images

class ExifImageHandler(BaseHandler):
    """JPEG/TIFF — full EXIF read/write."""

    FIELD_TAGS = {
        "make": piexif.ImageIFD.Make,
        "model": piexif.ImageIFD.Model,
        "artist": piexif.ImageIFD.Artist,
        "copyright": piexif.ImageIFD.Copyright,
        "description": piexif.ImageIFD.ImageDescription,
    }

    def _load_exif(self):
        try:
            return piexif.load(str(self.path))
        except Exception:
            return {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}

    def preview(self):
        try:
            img = Image.open(self.path)
            return ImageOps.exif_transpose(img)
        except Exception:
            return None

    def preview_placeholder(self):
        return "Preview\nunavailable"

    def supports_embedded_date(self):
        return True

    def get_embedded_date(self):
        exif_dict = self._load_exif()
        raw = exif_dict.get("Exif", {}).get(piexif.ExifIFD.DateTimeOriginal)
        if not raw:
            raw = exif_dict.get("0th", {}).get(piexif.ImageIFD.DateTime)
        if raw:
            text = raw.decode() if isinstance(raw, bytes) else raw
            try:
                return datetime.datetime.strptime(text, "%Y:%m:%d %H:%M:%S")
            except ValueError:
                return None
        return None

    def fields(self):
        exif_dict = self._load_exif()
        zeroth = exif_dict.get("0th", {})
        result = []
        for key, tag in self.FIELD_TAGS.items():
            raw = zeroth.get(tag, b"")
            text = raw.decode(errors="ignore") if isinstance(raw, bytes) else str(raw)
            label = {
                "make": "Camera Make", "model": "Camera Model", "artist": "Photographer",
                "copyright": "Copyright", "description": "Description",
            }[key]
            result.append(FieldSpec(key, label, text.strip("\x00")))
        return result

    def save(self, values, date, cover_path=None):
        exif_dict = self._load_exif()
        exif_dict.setdefault("Exif", {})
        exif_dict.setdefault("0th", {})
        if date is not None:
            stamp = date.strftime("%Y:%m:%d %H:%M:%S").encode()
            exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = stamp
            exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = stamp
            exif_dict["0th"][piexif.ImageIFD.DateTime] = stamp
        for key, tag in self.FIELD_TAGS.items():
            if key in values:
                exif_dict["0th"][tag] = values[key].encode()
        exif_bytes = piexif.dump(exif_dict)
        piexif.insert(exif_bytes, str(self.path))


class PngImageHandler(BaseHandler):
    """PNG — text chunk metadata (no EXIF date)."""

    TEXT_KEYS = {
        "title": "Title", "author": "Author",
        "description": "Description", "copyright": "Copyright",
    }

    def preview(self):
        try:
            return Image.open(self.path)
        except Exception:
            return None

    def embedded_date_note(self):
        return "PNG files don't store a date internally — only the file's date on disk will change."

    def fields(self):
        try:
            img = Image.open(self.path)
            text = getattr(img, "text", {}) or {}
        except Exception:
            text = {}
        return [FieldSpec(key, label, text.get(label, text.get(key, "")))
                for key, label in self.TEXT_KEYS.items()]

    def save(self, values, date, cover_path=None):
        img = Image.open(self.path)
        info = PngInfo()
        for key, label in self.TEXT_KEYS.items():
            val = values.get(key, "")
            if val:
                info.add_text(label, val)
        img.save(self.path, pnginfo=info)


class PlainImageHandler(BaseHandler):
    """Any other image format (BMP, HEIC, WEBP, ...) — no metadata support."""

    def preview(self):
        try:
            return Image.open(self.path)
        except Exception:
            return None

    def embedded_date_note(self):
        return "This image format doesn't support embedded metadata — only the file's date on disk will change."


# Video

def _read_video_tags() -> dict:
    return {}


class VideoHandler(BaseHandler):
    TAG_KEYS = {
        "title": "Title", "artist": "Artist", "album": "Album",
        "genre": "Genre", "comment": "Comment",
    }

    def preview(self):
        if not FFMPEG:
            return None
        fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        try:
            cmd = [FFMPEG, "-y", "-i", str(self.path), "-ss", "00:00:00.5",
                   "-frames:v", "1", "-vf", "scale=240:-1", tmp_path]
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

    def preview_placeholder(self):
        if not FFMPEG:
            return "Video preview\nneeds ffmpeg\n(brew install ffmpeg)"
        return "Preview\nunavailable"

    def _read_format_tags(self) -> dict:
        if not FFPROBE:
            return {}
        try:
            result = subprocess.run(
                [FFPROBE, "-v", "quiet", "-print_format", "json", "-show_format", str(self.path)],
                capture_output=True, text=True, timeout=15
            )
            data = json.loads(result.stdout or "{}")
            return {k.lower(): v for k, v in data.get("format", {}).get("tags", {}).items()}
        except Exception:
            return {}

    def supports_embedded_date(self):
        return FFMPEG is not None

    def embedded_date_note(self):
        if not FFMPEG:
            return "ffmpeg isn't installed — only the file's date on disk will change.\nInstall with: brew install ffmpeg"
        return None

    def get_embedded_date(self):
        tags = self._read_format_tags()
        raw = tags.get("creation_time")
        if raw:
            raw = raw.replace("Z", "")
            for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
                try:
                    return datetime.datetime.strptime(raw, fmt)
                except ValueError:
                    continue
        return None

    def fields(self):
        tags = self._read_format_tags()
        return [FieldSpec(key, label, tags.get(key, ""))
                for key, label in self.TAG_KEYS.items()]

    def save(self, values, date, cover_path=None):
        if not FFMPEG:
            raise RuntimeError(
                "ffmpeg is required to edit video metadata.\nInstall it with: brew install ffmpeg"
            )
        fd, tmp_path = tempfile.mkstemp(suffix=self.path.suffix)
        os.close(fd)
        try:
            cmd = [FFMPEG, "-y", "-i", str(self.path), "-c", "copy", "-map_metadata", "0"]
            if date is not None:
                cmd += ["-metadata", f"creation_time={date.strftime('%Y-%m-%dT%H:%M:%S')}"]
            for key in self.TAG_KEYS:
                if key in values:
                    cmd += ["-metadata", f"{key}={values[key]}"]
            cmd.append(tmp_path)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if result.returncode != 0:
                raise RuntimeError(result.stderr[-800:] if result.stderr else "ffmpeg failed")
            shutil.move(tmp_path, str(self.path))
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass


# Audio

class AudioHandler(BaseHandler):
    """Normalizes MP3 / WAV / FLAC / OGG / M4A tag editing behind one interface."""

    COMMON_FIELDS = [
        ("title", "Title"), ("artist", "Artist"), ("album", "Album"),
        ("albumartist", "Album Artist"), ("year", "Year"), ("genre", "Genre"),
        ("track", "Track #"), ("composer", "Composer"), ("comment", "Comment"),
    ]

    def __init__(self, path: Path):
        super().__init__(path)
        self.ext = path.suffix.lower()

    def supports_cover_art(self):
        return True

    # reading

    def fields(self):
        getter = {
            ".mp3": self._read_id3, ".wav": self._read_id3,
            ".flac": self._read_vorbis, ".ogg": self._read_vorbis,
            ".m4a": self._read_mp4,
        }.get(self.ext)
        values = getter() if getter else {}
        return [FieldSpec(key, label, values.get(key, "")) for key, label in self.COMMON_FIELDS]

    def _read_id3(self):
        try:
            tags = ID3(str(self.path)) if self.ext == ".mp3" else WAVE(str(self.path)).tags
        except Exception:
            tags = None
        if tags is None:
            return {}
        def text(frame_id):
            frame = tags.get(frame_id)
            return str(frame.text[0]) if frame and frame.text else ""
        comment = ""
        for key in tags.keys():
            if key.startswith("COMM"):
                comment = str(tags[key].text[0]) if tags[key].text else ""
                break
        return {
            "title": text("TIT2"), "artist": text("TPE1"), "album": text("TALB"),
            "albumartist": text("TPE2"), "year": text("TDRC"), "genre": text("TCON"),
            "track": text("TRCK"), "composer": text("TCOM"), "comment": comment,
        }

    def _read_vorbis(self):
        try:
            audio = FLAC(str(self.path)) if self.ext == ".flac" else OggVorbis(str(self.path))
        except Exception:
            return {}
        def get(key):
            vals = audio.get(key)
            return vals[0] if vals else ""
        return {
            "title": get("title"), "artist": get("artist"), "album": get("album"),
            "albumartist": get("albumartist"), "year": get("date"), "genre": get("genre"),
            "track": get("tracknumber"), "composer": get("composer"), "comment": get("comment"),
        }

    def _read_mp4(self):
        try:
            audio = MP4(str(self.path))
        except Exception:
            return {}
        def get(atom):
            vals = audio.get(atom)
            return str(vals[0]) if vals else ""
        track = ""
        if audio.get("trkn"):
            track = str(audio["trkn"][0][0])
        return {
            "title": get("\xa9nam"), "artist": get("\xa9ART"), "album": get("\xa9alb"),
            "albumartist": get("aART"), "year": get("\xa9day"), "genre": get("\xa9gen"),
            "track": track, "composer": get("\xa9wrt"), "comment": get("\xa9cmt"),
        }

    def get_cover_bytes(self):
        try:
            if self.ext in (".mp3", ".wav"):
                tags = ID3(str(self.path)) if self.ext == ".mp3" else WAVE(str(self.path)).tags
                if tags:
                    for key in tags.keys():
                        if key.startswith("APIC"):
                            return tags[key].data
            elif self.ext == ".flac":
                audio = FLAC(str(self.path))
                if audio.pictures:
                    return audio.pictures[0].data
            elif self.ext == ".ogg":
                audio = OggVorbis(str(self.path))
                blocks = audio.get("metadata_block_picture")
                if blocks:
                    pic = Picture(base64.b64decode(blocks[0]))
                    return pic.data
            elif self.ext == ".m4a":
                audio = MP4(str(self.path))
                covers = audio.get("covr")
                if covers:
                    return bytes(covers[0])
        except Exception:
            pass
        return None

    # writing

    def save(self, values, date, cover_path):
        cover_bytes = None
        if cover_path is not None:
            img = Image.open(cover_path)
            img = ImageOps.exif_transpose(img).convert("RGB")
            img.thumbnail((1000, 1000))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            cover_bytes = buf.getvalue()

        if self.ext in (".mp3", ".wav"):
            self._save_id3(values, cover_bytes)
        elif self.ext in (".flac", ".ogg"):
            self._save_vorbis(values, cover_bytes)
        elif self.ext == ".m4a":
            self._save_mp4(values, cover_bytes)

    def _save_id3(self, values, cover_bytes):
        if self.ext == ".mp3":
            try:
                tags = ID3(str(self.path))
            except ID3NoHeaderError:
                tags = ID3()
        else:
            wav = WAVE(str(self.path))
            if wav.tags is None:
                wav.add_tags()
            tags = wav.tags

        def set_text(frame_id, frame_cls, val):
            if val:
                tags.setall(frame_id, [frame_cls(encoding=3, text=val)])
            else:
                tags.delall(frame_id)

        set_text("TIT2", TIT2, values.get("title", ""))
        set_text("TPE1", TPE1, values.get("artist", ""))
        set_text("TALB", TALB, values.get("album", ""))
        set_text("TPE2", TPE2, values.get("albumartist", ""))
        set_text("TDRC", TDRC, values.get("year", ""))
        set_text("TCON", TCON, values.get("genre", ""))
        set_text("TRCK", TRCK, values.get("track", ""))
        set_text("TCOM", TCOM, values.get("composer", ""))
        comment = values.get("comment", "")
        tags.delall("COMM")
        if comment:
            tags.add(COMM(encoding=3, lang="eng", desc="", text=comment))

        if cover_bytes is not None:
            for key in list(tags.keys()):
                if key.startswith("APIC"):
                    del tags[key]
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover_bytes))

        if self.ext == ".mp3":
            tags.save(str(self.path), v2_version=3)
        else:
            wav.save()

    def _save_vorbis(self, values, cover_bytes):
        audio = FLAC(str(self.path)) if self.ext == ".flac" else OggVorbis(str(self.path))
        mapping = {
            "title": "title", "artist": "artist", "album": "album",
            "albumartist": "albumartist", "year": "date", "genre": "genre",
            "track": "tracknumber", "composer": "composer", "comment": "comment",
        }
        for key, vorbis_key in mapping.items():
            val = values.get(key, "")
            if val:
                audio[vorbis_key] = [val]
            elif vorbis_key in audio:
                del audio[vorbis_key]

        if cover_bytes is not None:
            pic = Picture()
            pic.data = cover_bytes
            pic.type = 3
            pic.mime = "image/jpeg"
            if self.ext == ".flac":
                audio.clear_pictures()
                audio.add_picture(pic)
            else:
                encoded = base64.b64encode(pic.write()).decode("ascii")
                audio["metadata_block_picture"] = [encoded]

        audio.save()

    def _save_mp4(self, values, cover_bytes):
        audio = MP4(str(self.path))
        mapping = {
            "title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb",
            "albumartist": "aART", "year": "\xa9day", "genre": "\xa9gen",
            "composer": "\xa9wrt", "comment": "\xa9cmt",
        }
        for key, atom in mapping.items():
            val = values.get(key, "")
            if val:
                audio[atom] = [val]
            elif atom in audio:
                del audio[atom]

        track = values.get("track", "")
        if track:
            try:
                audio["trkn"] = [(int(track), 0)]
            except ValueError:
                pass
        elif "trkn" in audio:
            del audio["trkn"]

        if cover_bytes is not None:
            audio["covr"] = [MP4Cover(cover_bytes, imageformat=MP4Cover.FORMAT_JPEG)]

        audio.save()


# PDF

class PdfHandler(BaseHandler):
    FIELDS = [
        ("title", "Title"), ("author", "Author"), ("subject", "Subject"),
        ("keywords", "Keywords"), ("creator", "Creator"),
    ]

    def preview(self):
        if not PDFTOPPM:
            return None
        fd, tmp_prefix = tempfile.mkstemp()
        os.close(fd)
        try:
            cmd = [PDFTOPPM, "-jpeg", "-f", "1", "-l", "1", "-scale-to", "300",
                   str(self.path), tmp_prefix]
            result = subprocess.run(cmd, capture_output=True, timeout=20)
            candidate = f"{tmp_prefix}-1.jpg"
            if not os.path.exists(candidate):
                candidate = f"{tmp_prefix}.jpg"
            if result.returncode == 0 and os.path.exists(candidate):
                img = Image.open(candidate)
                img.load()
                return img
        except Exception:
            pass
        finally:
            for p in Path(tempfile.gettempdir()).glob(os.path.basename(tmp_prefix) + "*"):
                try:
                    p.unlink()
                except OSError:
                    pass
        return None

    def preview_placeholder(self):
        if not PDFTOPPM:
            return "PDF\n(install poppler for\na page preview:\nbrew install poppler)"
        return "PDF preview\nunavailable"

    def fields(self):
        try:
            meta = PdfReader(str(self.path)).metadata or {}
        except Exception:
            meta = {}
        result = []
        for key, label in self.FIELDS:
            attr = f"/{key.capitalize()}" if key != "keywords" else "/Keywords"
            val = getattr(meta, key, None) if hasattr(meta, key) else meta.get(attr, "")
            result.append(FieldSpec(key, label, val or ""))
        return result

    def save(self, values, date, cover_path=None):
        reader = PdfReader(str(self.path))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        existing = dict(reader.metadata or {})
        updates = {
            "/Title": values.get("title", ""), "/Author": values.get("author", ""),
            "/Subject": values.get("subject", ""), "/Keywords": values.get("keywords", ""),
            "/Creator": values.get("creator", ""),
        }
        existing.update(updates)
        writer.add_metadata(existing)
        fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            with open(tmp_path, "wb") as f:
                writer.write(f)
            shutil.move(tmp_path, str(self.path))
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass


# Office documents

class DocxHandler(BaseHandler):
    FIELDS = [
        ("title", "Title"), ("author", "Author"), ("subject", "Subject"),
        ("keywords", "Keywords"), ("comments", "Comments"), ("category", "Category"),
    ]

    def preview_placeholder(self):
        return "Word\ndocument"

    def fields(self):
        try:
            cp = Document(str(self.path)).core_properties
            return [FieldSpec(k, l, getattr(cp, k, "") or "") for k, l in self.FIELDS]
        except Exception:
            return [FieldSpec(k, l, "") for k, l in self.FIELDS]

    def save(self, values, date, cover_path=None):
        doc = Document(str(self.path))
        cp = doc.core_properties
        for key, _ in self.FIELDS:
            setattr(cp, key, values.get(key, ""))
        doc.save(str(self.path))


class XlsxHandler(BaseHandler):
    FIELDS = [
        ("title", "Title", "title"), ("author", "Author", "creator"),
        ("subject", "Subject", "subject"), ("keywords", "Keywords", "keywords"),
        ("comments", "Comments", "description"), ("category", "Category", "category"),
    ]

    def preview_placeholder(self):
        return "Excel\nworkbook"

    def fields(self):
        try:
            props = load_workbook(str(self.path)).properties
            return [FieldSpec(k, l, getattr(props, attr, "") or "") for k, l, attr in self.FIELDS]
        except Exception:
            return [FieldSpec(k, l, "") for k, l, _ in self.FIELDS]

    def save(self, values, date, cover_path=None):
        wb = load_workbook(str(self.path))
        props = wb.properties
        for key, _, attr in self.FIELDS:
            setattr(props, attr, values.get(key, ""))
        wb.save(str(self.path))


class PptxHandler(BaseHandler):
    FIELDS = [
        ("title", "Title"), ("author", "Author"), ("subject", "Subject"),
        ("keywords", "Keywords"), ("comments", "Comments"), ("category", "Category"),
    ]

    def preview_placeholder(self):
        return "PowerPoint\npresentation"

    def fields(self):
        try:
            cp = Presentation(str(self.path)).core_properties
            return [FieldSpec(k, l, getattr(cp, k, "") or "") for k, l in self.FIELDS]
        except Exception:
            return [FieldSpec(k, l, "") for k, l in self.FIELDS]

    def save(self, values, date, cover_path=None):
        prs = Presentation(str(self.path))
        cp = prs.core_properties
        for key, _ in self.FIELDS:
            setattr(cp, key, values.get(key, ""))
        prs.save(str(self.path))


class FallbackHandler(BaseHandler):
    """Any file type we don't have a specific editor for — file dates only."""

    def preview_placeholder(self):
        return "No metadata\neditor for this\nfile type"

    def embedded_date_note(self):
        return "There's no specific metadata editor for this file type — only its date on disk can be changed here."


HANDLER_BY_KIND = {
    "video": VideoHandler,
    "pdf": PdfHandler,
    "docx": DocxHandler,
    "xlsx": XlsxHandler,
    "pptx": PptxHandler,
}


def get_handler(path: Path) -> BaseHandler:
    kind = file_kind(path)
    ext = path.suffix.lower()
    if kind == "image":
        if ext in EXIF_CAPABLE_EXTS:
            return ExifImageHandler(path)
        if ext in PNG_EXTS:
            return PngImageHandler(path)
        return PlainImageHandler(path)
    if kind == "audio":
        return AudioHandler(path)
    handler_cls = HANDLER_BY_KIND.get(kind)
    if handler_cls:
        return handler_cls(path)
    return FallbackHandler(path)


def get_list_thumbnail(path: Path):
    """Small preview image for the file list, reusing each handler's own
    preview()/cover-art logic so the list and the detail panel always agree."""
    try:
        handler = get_handler(path)
        img = handler.preview()
        if img is None and handler.supports_cover_art():
            cover = handler.get_cover_bytes()
            if cover:
                img = Image.open(io.BytesIO(cover))
        return img
    except Exception:
        return None


def convert_image(path: Path, target_format: str, output_path: Path):
    """Convert an image to another format, handling the common gotchas
    (alpha channels on formats that don't support them, EXIF orientation)."""
    if path.suffix.lower() == ".heic" and not HEIF_SUPPORTED:
        raise RuntimeError(
            "HEIC support isn't installed. Add it with:\n"
            "    pip3 install pillow-heif\n"
            "(inside this app's .venv if you're using the launcher)"
        )
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    save_kwargs = {}
    if target_format == "JPEG":
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            img = background
        else:
            img = img.convert("RGB")
        save_kwargs["quality"] = 95
    elif target_format == "BMP":
        img = img.convert("RGB")
    img.save(output_path, format=target_format, **save_kwargs)


# GUI

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

THUMB_SIZE = (220, 220)


class DetailPanel(ctk.CTkScrollableFrame):
    """Right-hand panel: preview + one unified editor for whatever the
    selected file supports (dates, tags/EXIF/document properties, cover art)."""

    def __init__(self, master, app):
        super().__init__(master, corner_radius=12, label_text="")
        self.app = app
        self.current_path = None
        self.handler = None
        self._thumb_image = None
        self._pending_cover_path = None
        self.field_vars: Dict[str, tk.StringVar] = {}
        self._build_empty_state()

    def _clear(self):
        for widget in self.winfo_children():
            widget.destroy()

    def _build_empty_state(self):
        self._clear()
        ctk.CTkLabel(
            self, text="Select a file to view and edit its details.",
            text_color="gray60", font=ctk.CTkFont(size=14), justify="center"
        ).pack(expand=True, padx=20, pady=40)

    def show(self, path: Path):
        self.current_path = path
        self.handler = get_handler(path)
        self._pending_cover_path = None
        self.field_vars = {}
        self._render()

    # Rendering

    def _thumb_widget(self, pil_image, fallback_text):
        if pil_image is not None:
            pil_image = pil_image.copy()
            pil_image.thumbnail(THUMB_SIZE)
            self._thumb_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image,
                                              size=pil_image.size)
            return ctk.CTkLabel(self, image=self._thumb_image, text="")
        return ctk.CTkLabel(self, text=fallback_text, width=THUMB_SIZE[0], height=THUMB_SIZE[1],
                             fg_color="gray20", text_color="gray70", corner_radius=8)

    def _render(self):
        self._clear()
        path, handler = self.current_path, self.handler

        self.filename_var = tk.StringVar(value=path.name)
        ctk.CTkEntry(self, textvariable=self.filename_var,
                     font=ctk.CTkFont(size=15, weight="bold"),
                     justify="center").pack(pady=(16, 8), padx=16, fill="x")

        preview_img = handler.preview()
        if preview_img is None and handler.supports_cover_art():
            cover_bytes = handler.get_cover_bytes()
            if cover_bytes:
                try:
                    preview_img = Image.open(io.BytesIO(cover_bytes))
                except Exception:
                    preview_img = None
        self._cover_label = self._thumb_widget(preview_img, handler.preview_placeholder())
        self._cover_label.pack(pady=8)

        if handler.supports_cover_art():
            ctk.CTkButton(self, text="Change Cover Art…", command=self._pick_cover,
                          fg_color="gray30", hover_color="gray40").pack(pady=(0, 12))

        if file_kind(path) == "image":
            self._build_convert_section()

        self._build_date_section()

        fields = handler.fields()
        if fields:
            self._build_fields_section(fields)

        ctk.CTkButton(self, text="Save Changes", height=36,
                      font=ctk.CTkFont(size=14, weight="bold"),
                      command=self._save_all).pack(pady=(8, 20))

    def _build_convert_section(self):
        path = self.current_path
        current_ext = path.suffix.lower()
        options = [fmt for fmt, ext in CONVERTIBLE_IMAGE_FORMATS.items() if ext != current_ext]
        if not options:
            return

        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="x", padx=16, pady=(4, 8))
        ctk.CTkLabel(frame, text="CONVERT FORMAT", text_color="gray50",
                     font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", pady=(0, 4))

        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x")
        self.convert_var = tk.StringVar(value=options[0])
        ctk.CTkOptionMenu(row, values=options, variable=self.convert_var, width=110).pack(
            side="left")
        ctk.CTkButton(row, text="Convert…", width=100,
                      command=self._convert_image).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(frame, text="Creates a new file alongside this one — the original is untouched.",
                     text_color="gray50", font=ctk.CTkFont(size=10),
                     wraplength=280, justify="left").pack(anchor="w", pady=(4, 0))

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=16, pady=(8, 4))

    def _convert_image(self):
        target_format = self.convert_var.get()
        ext = CONVERTIBLE_IMAGE_FORMATS[target_format]
        output_path = self.current_path.with_suffix(ext)

        if output_path.exists():
            if not messagebox.askyesno(
                "File already exists",
                f'"{output_path.name}" already exists in this folder. Overwrite it?'
            ):
                return

        try:
            convert_image(self.current_path, target_format, output_path)
        except Exception as e:
            messagebox.showerror("Conversion failed", str(e))
            return

        self.app.set_status(f"Converted to {output_path.name}")
        self.app.refresh_file_list(keep_selection=output_path)

    def _build_date_section(self):
        handler = self.handler
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(pady=(4, 4), padx=16, fill="x")

        ctk.CTkLabel(frame, text="DATE", text_color="gray50",
                     font=ctk.CTkFont(size=11, weight="bold")).grid(
            row=0, column=0, sticky="w", columnspan=6, pady=(0, 4))

        embedded = handler.get_embedded_date() if handler.supports_embedded_date() else None
        modified, _ = get_filesystem_dates(self.current_path)
        current = embedded or modified
        self._original_date = current

        ctk.CTkLabel(frame, text="Current:", text_color="gray60",
                     font=ctk.CTkFont(size=12)).grid(row=1, column=0, sticky="w", columnspan=6)
        ctk.CTkLabel(frame, text=current.strftime("%b %d, %Y  %H:%M:%S"),
                     font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=2, column=0, sticky="w", columnspan=6, pady=(0, 10))

        base = current
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
            ctk.CTkEntry(frame, textvariable=var, width=width * 10,
                         placeholder_text=placeholder, justify="center").grid(
                row=3, column=i, padx=2, pady=(2, 10), sticky="w")

        ctk.CTkButton(frame, text="Set to now", fg_color="gray30", hover_color="gray40",
                      width=100, command=self._set_now).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(0, 8))

        self.set_created_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(frame, text="Also set file 'created' date (macOS)",
                        variable=self.set_created_var, font=ctk.CTkFont(size=11)).grid(
            row=5, column=0, columnspan=6, sticky="w", pady=(0, 4))

        note = handler.embedded_date_note()
        if note:
            ctk.CTkLabel(frame, text=note, text_color="#e0a030", font=ctk.CTkFont(size=11),
                         wraplength=280, justify="left").grid(
                row=6, column=0, columnspan=6, sticky="w", pady=(2, 4))

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=16, pady=(8, 4))

    def _build_fields_section(self, fields: List[FieldSpec]):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="x", padx=16, pady=(4, 4))

        ctk.CTkLabel(frame, text="DETAILS", text_color="gray50",
                     font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", pady=(0, 6))

        for spec in fields:
            var = tk.StringVar(value=spec.value)
            self.field_vars[spec.key] = var
            ctk.CTkLabel(frame, text=spec.label, text_color="gray60",
                         font=ctk.CTkFont(size=11)).pack(anchor="w", pady=(6, 0))
            ctk.CTkEntry(frame, textvariable=var).pack(fill="x", pady=(2, 0))

    def _set_now(self):
        now = datetime.datetime.now()
        self.year_var.set(str(now.year))
        self.month_var.set(f"{now.month:02d}")
        self.day_var.set(f"{now.day:02d}")
        self.hour_var.set(f"{now.hour:02d}")
        self.min_var.set(f"{now.minute:02d}")
        self.sec_var.set(f"{now.second:02d}")

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

    # ---------------- Save ----------------

    def _save_all(self):
        path, handler = self.current_path, self.handler

        new_name = self.filename_var.get().strip()
        if not new_name:
            messagebox.showerror("Invalid name", "File name can't be empty.")
            return

        try:
            when = datetime.datetime(
                int(self.year_var.get()), int(self.month_var.get()), int(self.day_var.get()),
                int(self.hour_var.get()), int(self.min_var.get()), int(self.sec_var.get())
            )
        except ValueError as e:
            messagebox.showerror("Invalid date", f"Please check the date/time values.\n{e}")
            return

        # Rename first, if the name changed, so everything else operates on the new path.
        if new_name != path.name:
            new_path = path.with_name(new_name)
            if new_path.exists():
                messagebox.showerror(
                    "Rename failed", f'A file named "{new_name}" already exists here.'
                )
                return
            try:
                path.rename(new_path)
            except Exception as e:
                messagebox.showerror("Rename failed", str(e))
                return
            path = new_path
            handler = get_handler(new_path)
            self.current_path = new_path
            self.handler = handler

        date_changed = when != self._original_date
        field_values = {key: var.get() for key, var in self.field_vars.items()}
        has_detail_work = bool(field_values) or self._pending_cover_path is not None

        try:
            if has_detail_work or (date_changed and handler.supports_embedded_date()):
                handler.save(
                    field_values,
                    when if (date_changed and handler.supports_embedded_date()) else None,
                    self._pending_cover_path,
                )
            if date_changed:
                set_filesystem_dates(path, when, set_created=self.set_created_var.get())
        except Exception as e:
            messagebox.showerror("Couldn't save changes", str(e))
            return

        self.app.set_status(f"Saved changes to {path.name}")
        self.app.refresh_file_list(keep_selection=path)


class FileExplorerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Media Manager")
        self.geometry("1080x720")
        self.minsize(860, 560)

        self.current_dir = Path.home()
        self.sort_column = "name"     # one of: name, kind, modified, size
        self.sort_reverse = False
        self._explicit_sort = False   # becomes True once the user clicks a column header
        self._thumb_generation = 0
        self._tree_images = {}        # iid -> ImageTk.PhotoImage (must keep a reference alive)

        self._build_layout()
        self.refresh_file_list()

    # ---------------- Layout ----------------

    def _build_layout(self):
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(1, weight=1)

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
                         rowheight=34, borderwidth=0, font=("Helvetica", 12))
        style.configure("Treeview.Heading", font=("Helvetica", 12, "bold"))
        style.map("Treeview", background=[("selected", "#3a7ebf")])

        columns = ("kind", "modified", "size")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="tree headings", style="Treeview")
        self.tree.column("#0", width=300)
        self.tree.column("kind", width=90, anchor="center")
        self.tree.column("modified", width=150, anchor="center")
        self.tree.column("size", width=90, anchor="e")
        self._update_heading_labels()

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)
        vsb.grid(row=0, column=1, sticky="ns", pady=4)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double_click)

        self.detail_panel = DetailPanel(self, self)
        self.detail_panel.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=(0, 6))

        self.status_var = tk.StringVar(value="Ready")
        ctk.CTkLabel(self, textvariable=self.status_var, anchor="w",
                     text_color="gray60", font=ctk.CTkFont(size=11)).grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 8))

    #  Navigation 

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

    # Sorting 

    _COLUMN_LABELS = {"name": "Name", "kind": "Kind", "modified": "Date Modified", "size": "Size"}

    def _update_heading_labels(self):
        for col, label in self._COLUMN_LABELS.items():
            heading_col = "#0" if col == "name" else col
            suffix = ""
            if self.sort_column == col:
                suffix = "  ▼" if self.sort_reverse else "  ▲"
            self.tree.heading(heading_col, text=label + suffix,
                               command=lambda c=col: self._sort_by(c))

    def _sort_by(self, column):
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = False
        self._explicit_sort = True
        self._update_heading_labels()
        self.refresh_file_list()

    def _sort_key(self, info):
        if self.sort_column == "kind":
            return info["kind"]
        if self.sort_column == "modified":
            return info["modified"]
        if self.sort_column == "size":
            return -1 if info["is_dir"] else info["size"]
        return info["path"].name.lower()

    def _sorted_entries(self, entries_info):
        if self.sort_column == "name" and not self._explicit_sort:
            # Default view: folders first, alphabetical — until the user clicks a header.
            return sorted(entries_info, key=lambda i: (not i["is_dir"], i["path"].name.lower()),
                          reverse=self.sort_reverse)
        return sorted(entries_info, key=self._sort_key, reverse=self.sort_reverse)

    # File list

    ICONS = {
        "folder": "📁", "image": "🖼", "video": "🎬", "audio": "🎵",
        "pdf": "📕", "docx": "📄", "xlsx": "📊", "pptx": "📽", "other": "📄",
    }
    THUMBNAIL_KINDS = {"image", "video", "audio", "pdf"}

    def refresh_file_list(self, keep_selection: Path = None):
        self._thumb_generation += 1
        generation = self._thumb_generation

        self.path_var.set(str(self.current_dir))
        self.tree.delete(*self.tree.get_children())
        self._tree_images.clear()

        try:
            raw_entries = list(self.current_dir.iterdir())
        except PermissionError:
            self.set_status(f"Permission denied: {self.current_dir}")
            raw_entries = []
        except FileNotFoundError:
            self.set_status(f"Folder not found: {self.current_dir}")
            raw_entries = []

        entries_info = []
        for entry in raw_entries:
            if entry.name.startswith("."):
                continue
            try:
                kind = file_kind(entry)
                stat = entry.stat()
                modified_dt = datetime.datetime.fromtimestamp(stat.st_mtime)
                size_bytes = 0 if entry.is_dir() else stat.st_size
            except (PermissionError, FileNotFoundError):
                continue
            entries_info.append({
                "path": entry, "kind": kind, "modified": modified_dt,
                "size": size_bytes, "is_dir": entry.is_dir(),
            })

        entries_info = self._sorted_entries(entries_info)

        select_iid = None
        thumb_targets = []
        for info in entries_info:
            entry, kind = info["path"], info["kind"]
            label = f"{self.ICONS.get(kind, '📄')}  {entry.name}"
            modified_str = info["modified"].strftime("%Y-%m-%d %H:%M")
            size_str = "—" if info["is_dir"] else human_size(info["size"])
            iid = self.tree.insert("", "end", text=label,
                                    values=(kind.capitalize(), modified_str, size_str),
                                    tags=(str(entry),))
            if kind in self.THUMBNAIL_KINDS:
                thumb_targets.append((iid, entry))
            if keep_selection is not None and entry == keep_selection:
                select_iid = iid

        if select_iid:
            self.tree.selection_set(select_iid)
            self.tree.see(select_iid)
            self._on_select()

        if thumb_targets:
            threading.Thread(target=self._load_thumbnails, args=(generation, thumb_targets),
                              daemon=True).start()

    def _load_thumbnails(self, generation, targets):
        """Runs on a background thread so browsing stays responsive; hands each
        finished thumbnail back to the main thread via `after()`."""
        for iid, entry in targets:
            if generation != self._thumb_generation:
                return 
            img = get_list_thumbnail(entry)
            if img is None:
                continue
            try:
                img = img.convert("RGB")
                img.thumbnail(LIST_THUMB_SIZE)
            except Exception:
                continue
            try:
                self.after(0, self._apply_thumbnail, generation, iid, img, entry.name)
            except RuntimeError:
                return 

    def _apply_thumbnail(self, generation, iid, pil_image, name):
        if generation != self._thumb_generation or not self.tree.exists(iid):
            return
        photo = ImageTk.PhotoImage(pil_image)
        self._tree_images[iid] = photo 
        self.tree.item(iid, image=photo, text=f"  {name}")

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
