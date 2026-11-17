"""Compress any file, not only video: images, PDF, Office documents, audio, and everything else.

Each kind gets the method that suits it:
  image   JPEG/WebP re-encoded at a lower quality, PNG palette-reduced, large photos scaled down (Pillow)
  pdf     pictures inside re-encoded and scaled down, streams recompressed (pikepdf + Pillow)
  office  .docx/.xlsx/.pptx/.odt… are zip files: pictures inside shrunk, then re-zipped at max level
  audio   re-encoded to a lower bitrate with FFmpeg (WAV/FLAC/AIFF become FLAC or MP3)
  other   packed into a .zip at the strongest standard level (lossless; opens anywhere)

A result is never bigger than the original: when a method cannot shrink a file, the original bytes
are kept and the report says it was already as small as it gets.
"""
from __future__ import annotations

import io
import os
import shutil
import zipfile
from collections.abc import Callable, Collection
from pathlib import Path
from typing import ClassVar

from core.base_task import BaseMediaTask
from core.errors import InputError, ToolFailedError
from core.ffmpeg import probe_format, run_ffmpeg
from tasks.compressor import PRESET_LABELS, PRESETS, CompressReport

IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff", "gif"}
PDF_EXTS = {"pdf"}
OFFICE_EXTS = {"docx", "xlsx", "pptx", "docm", "xlsm", "pptm", "odt", "ods", "odp", "epub"}
AUDIO_EXTS = {"mp3", "m4a", "aac", "ogg", "opus", "wav", "flac", "aif", "aiff", "wma"}
VIDEO_EXTS = {"mp4", "mov", "mkv", "avi", "webm", "m4v", "flv", "wmv", "ts", "mts", "m2ts", "3gp", "mpg", "mpeg"}

# Per mode: JPEG/WebP quality, longest picture side, PNG colours (0 = keep lossless), audio kbps.
SETTINGS: dict[str, dict[str, int]] = {
    "high": {"quality": 85, "max_side": 4096, "colors": 0, "audio": 192},
    "balanced": {"quality": 72, "max_side": 2560, "colors": 256, "audio": 128},
    "small": {"quality": 55, "max_side": 1600, "colors": 128, "audio": 96},
}
FILE_MODES = tuple(SETTINGS)

Progress = Callable[[float, str], None]


def ext_of(path: str | Path) -> str:
    return Path(path).suffix.lower().lstrip(".")


def kind_of(path: str | Path) -> str:
    """video | image | pdf | office | audio | other"""
    ext = ext_of(path)
    for kind, exts in (("video", VIDEO_EXTS), ("image", IMAGE_EXTS), ("pdf", PDF_EXTS),
                       ("office", OFFICE_EXTS), ("audio", AUDIO_EXTS)):
        if ext in exts:
            return kind
    return "other"


KIND_NAMES = {"video": "วิดีโอ", "image": "รูปภาพ", "pdf": "PDF", "office": "เอกสาร Office",
              "audio": "เสียง", "other": "ไฟล์ทั่วไป"}
KIND_METHODS = {
    "image": "ลดคุณภาพและขนาดภาพเล็กน้อย",
    "pdf": "ย่อรูปภาพในเอกสาร และบีบข้อมูลใหม่",
    "office": "ย่อรูปภาพในเอกสาร และบีบไฟล์ใหม่",
    "audio": "ลดบิตเรตเสียง",
    "other": "บีบเป็นไฟล์ .zip (ไม่เสียข้อมูล)",
}


def output_ext(path: str | Path, mode: str) -> str:
    """Extension of the compressed file for this source and mode."""
    ext = ext_of(path)
    kind = kind_of(path)
    if kind == "image":
        return {"bmp": "png", "jpeg": "jpg", "tif": "tiff"}.get(ext, ext)
    if kind == "audio":
        if ext in ("wav", "flac", "aif", "aiff"):
            return "flac" if mode == "high" else "mp3"
        return {"aac": "m4a", "wma": "mp3"}.get(ext, ext)
    if kind == "other":
        return "zip"
    return ext


def suggest_output_path(input_path: str, mode: str = "balanced", taken: Collection[str] = ()) -> Path:
    source = Path(input_path)
    ext = output_ext(source, mode)
    reserved = {os.path.normcase(item) for item in taken}
    number = 1
    while True:
        suffix = "" if number == 1 else f"_{number}"
        candidate = source.with_name(f"{source.stem}_compressed{suffix}.{ext}")
        if not candidate.exists() and os.path.normcase(str(candidate)) not in reserved:
            return candidate
        number += 1


# --- images ------------------------------------------------------------------------------------
def _fit(image, max_side: int):
    from PIL import Image
    if max(image.size) > max_side:
        image = image.copy()
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return image


def compress_image_bytes(data: bytes, fmt: str, mode: str) -> bytes:
    """Smaller bytes of the same picture format (fmt: jpg/png/webp/gif/tiff). Returns data if not smaller."""
    from PIL import Image, ImageOps
    s = SETTINGS[mode]
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        return data
    fmt = {"jpeg": "jpg", "tif": "tiff", "bmp": "png"}.get(fmt.lower(), fmt.lower())
    out = io.BytesIO()
    if fmt == "gif" or getattr(image, "is_animated", False):
        image.save(out, "GIF" if fmt == "gif" else image.format, save_all=True, optimize=True)
    else:
        if fmt in ("jpg", "webp"):
            image = ImageOps.exif_transpose(image)
        image = _fit(image, s["max_side"])
        if fmt == "jpg":
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            image.save(out, "JPEG", quality=s["quality"], optimize=True, progressive=True)
        elif fmt == "webp":
            image.save(out, "WEBP", quality=s["quality"], method=6)
        elif fmt == "png":
            if s["colors"] and image.mode in ("RGB", "RGBA", "P", "L", "LA"):
                rgba = image.convert("RGBA")
                image = rgba.quantize(colors=s["colors"], method=Image.Quantize.FASTOCTREE)
            image.save(out, "PNG", optimize=True)
        elif fmt == "tiff":
            image.save(out, "TIFF", compression="tiff_deflate")
        else:
            return data
    result = out.getvalue()
    return result if len(result) < len(data) else data


# --- office documents (zip containers) -----------------------------------------------------------
_PICTURE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "tif", "tiff", "bmp"}


def compress_zip_document(source: Path, target: Path, mode: str, progress: Progress | None = None,
                          cancelled: Callable[[], bool] = lambda: False) -> None:
    with zipfile.ZipFile(source) as zin:
        items = zin.infolist()
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zout:
            for index, item in enumerate(items):
                if cancelled():
                    return
                data = zin.read(item)
                ext = ext_of(item.filename)
                if ext in _PICTURE_EXTS and ext != "bmp":  # keep the name: the document points to it
                    data = compress_image_bytes(data, ext, mode)
                # "mimetype" in ODF/EPUB must stay first and uncompressed
                kind = zipfile.ZIP_STORED if item.filename == "mimetype" else zipfile.ZIP_DEFLATED
                info = zipfile.ZipInfo(item.filename, date_time=item.date_time)
                info.external_attr = item.external_attr
                info.compress_type = kind
                zout.writestr(info, data, compress_type=kind, compresslevel=9 if kind else None)
                if progress:
                    progress((index + 1) / len(items), "กำลังย่อรูปภาพในเอกสาร")


# --- PDF ---------------------------------------------------------------------------------------------
def compress_pdf(source: Path, target: Path, mode: str, progress: Progress | None = None,
                 cancelled: Callable[[], bool] = lambda: False) -> None:
    import pikepdf
    from pikepdf import Name, PdfImage
    s = SETTINGS[mode]
    with pikepdf.open(source) as pdf:
        images = []
        seen: set[tuple[int, int]] = set()
        for page in pdf.pages:
            for _name, raw in page.images.items():
                if raw.objgen not in seen:
                    seen.add(raw.objgen)
                    images.append(raw)
        for index, raw in enumerate(images):
            if cancelled():
                return
            if progress:
                progress(0.9 * (index + 1) / max(len(images), 1), "กำลังย่อรูปภาพในเอกสาร")
            try:
                if raw.get("/SMask") is not None or raw.get("/Mask") is not None or raw.get("/ImageMask"):
                    continue  # transparency: leave it alone
                if int(raw.get("/BitsPerComponent", 8)) != 8:
                    continue
                pil = PdfImage(raw).as_pil_image()
                if pil.mode not in ("RGB", "L"):
                    if pil.mode == "CMYK":
                        continue  # colours would shift
                    pil = pil.convert("RGB")
                pil = _fit(pil, s["max_side"])
                buffer = io.BytesIO()
                pil.save(buffer, "JPEG", quality=s["quality"], optimize=True)
                data = buffer.getvalue()
                if len(data) >= len(raw.read_raw_bytes()):
                    continue
                raw.write(data, filter=Name.DCTDecode)
                raw.Width, raw.Height = pil.size
                raw.ColorSpace = Name.DeviceGray if pil.mode == "L" else Name.DeviceRGB
                raw.BitsPerComponent = 8
                for key in ("/DecodeParms", "/Decode"):
                    if key in raw:
                        del raw[key]
            except Exception:
                continue  # an unusual image: keep the original
        pdf.remove_unreferenced_resources()
        pdf.save(target, compress_streams=True, recompress_flate=True,
                 object_stream_mode=pikepdf.ObjectStreamMode.generate)


# --- everything else -----------------------------------------------------------------------------------
def zip_file(source: Path, target: Path, progress: Progress | None = None,
             cancelled: Callable[[], bool] = lambda: False) -> None:
    total = max(source.stat().st_size, 1)
    done = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zout:
        with open(source, "rb") as src, zout.open(source.name, "w", force_zip64=True) as dst:
            while block := src.read(1024 * 1024):
                if cancelled():
                    return
                dst.write(block)
                done += len(block)
                if progress:
                    progress(done / total, "กำลังบีบไฟล์")


def audio_args(source: str, partial: str, out_ext: str, mode: str) -> list[str]:
    kbps = SETTINGS[mode]["audio"]
    codec = {
        "mp3": ["-c:a", "libmp3lame", "-b:a", f"{kbps}k"],
        "m4a": ["-c:a", "aac", "-b:a", f"{kbps}k"],
        "ogg": ["-c:a", "libvorbis", "-b:a", f"{kbps}k"],
        "opus": ["-c:a", "libopus", "-b:a", f"{min(kbps, 128)}k"],
        "flac": ["-c:a", "flac", "-compression_level", "12"],
    }[out_ext]
    muxer = {"m4a": "ipod", "ogg": "ogg", "opus": "opus", "flac": "flac", "mp3": "mp3"}[out_ext]
    return ["-i", source, "-map", "0:a:0", "-map_metadata", "0", "-vn", *codec, "-f", muxer, partial]


# --- task ------------------------------------------------------------------------------------------------
class FileCompressTask(BaseMediaTask):
    """Compress a non-video file. Same queue card and report as the video compressor."""

    kind: ClassVar[str] = "compress"
    title: ClassVar[str] = "บีบอัดไฟล์"

    def __init__(self, input_path: str, output_path: str, mode: str = "balanced") -> None:
        super().__init__(input_path, output_path)
        self.mode = mode
        self.file_kind = kind_of(input_path)
        self.result: CompressReport | None = None
        self.display_name = Path(input_path).name

    def summary(self) -> str:
        label = PRESET_LABELS.get(self.mode, self.mode)
        return f"{KIND_NAMES[self.file_kind]} · {label}"

    def validate(self) -> None:
        super().validate()
        if self.mode not in SETTINGS:
            raise InputError(f"ไม่รู้จักโหมดการบีบอัด: {self.mode}")
        if self.file_kind == "video":
            raise InputError("วิดีโอใช้ตัวบีบอัดวิดีโอ")
        if Path(self.input_path).resolve() == Path(self.output_path).resolve():
            raise InputError("ไฟล์ปลายทางต้องไม่ใช่ไฟล์เดียวกับไฟล์ต้นฉบับ")

    def _step(self, fraction: float, message: str) -> None:
        self.report_progress(min(99, fraction * 100), message)

    def process(self) -> Path:
        source = Path(self.input_path)
        original = source.stat().st_size
        partial = self.partial_output()
        cancelled = self._cancel_event.is_set
        kind = self.file_kind
        self._step(0.02, "กำลังบีบอัด")
        try:
            if kind == "image":
                data = source.read_bytes()
                partial.write_bytes(compress_image_bytes(data, output_ext(source, self.mode), self.mode)
                                    if ext_of(source) != "bmp" else _bmp_to_png(data))
            elif kind == "pdf":
                compress_pdf(source, partial, self.mode, self._step, cancelled)
            elif kind == "office":
                compress_zip_document(source, partial, self.mode, self._step, cancelled)
            elif kind == "audio":
                duration = float(probe_format(str(source)).get("duration") or 0) or None
                run_ffmpeg(audio_args(str(source), str(partial), output_ext(source, self.mode), self.mode),
                           duration=duration,
                           on_progress=lambda f: self._step(f, "กำลังบีบอัดเสียง"),
                           is_cancelled=cancelled)
            else:
                zip_file(source, partial, self._step, cancelled)
        except ToolFailedError:
            raise
        except zipfile.BadZipFile as exc:
            raise ToolFailedError("เปิดเอกสารไม่ได้ ไฟล์อาจเสียหายหรือถูกล็อกด้วยรหัสผ่าน") from exc
        except Exception as exc:
            if type(exc).__name__ == "PasswordError":
                raise ToolFailedError("PDF นี้มีรหัสผ่าน บีบอัดไม่ได้") from exc
            raise ToolFailedError(f"บีบอัดไฟล์นี้ไม่ได้: {exc}") from exc
        self.check_cancelled()

        same_format = output_ext(source, self.mode) == ext_of(source)
        if same_format and partial.stat().st_size >= original:
            shutil.copyfile(source, partial)  # could not do better: keep the original as is
        final = Path(self.output_path)
        partial.replace(final)
        self.result = CompressReport(original_bytes=original, new_bytes=final.stat().st_size)
        return final


def _bmp_to_png(data: bytes) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.open(io.BytesIO(data)).save(out, "PNG", optimize=True)
    return out.getvalue()


assert set(FILE_MODES) == set(PRESETS)  # same three presets as the video compressor
