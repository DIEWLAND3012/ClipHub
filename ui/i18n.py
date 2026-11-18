"""Thai / English for everything ClipHUB shows. Thai is the source language.

How it works: install() wraps the Qt setters that put text on screen (QLabel.setText, button
text, placeholders, tooltips, combo items, table headers). Each wrapper remembers the Thai source
on the widget and shows tr(source). set_language() re-applies tr() to every remembered source, so
the whole app, including text set at run time (summaries, progress, toasts), switches live.

tr() tries an exact match, then patterns for messages with values in them, then translates the
parts of "A · B" and multi-line text one by one. Anything unknown (file names, paths, English
errors from yt-dlp or FFmpeg) is shown unchanged.
"""
from __future__ import annotations

import re
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QStandardItem
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QCheckBox, QComboBox, QLabel, QLineEdit, QPushButton, QTableWidget, QWidget,
)

LANGUAGES = ("th", "en")
SOURCE_ROLE = Qt.ItemDataRole.UserRole + 64  # where combo items keep their Thai source

EN: dict[str, str] = {
    # navigation and shell
    "ดาวน์โหลด": "Download", "แปลงไฟล์": "Convert", "บีบอัด": "Compress", "ซับไตเติล": "Subtitles",
    "คิวงาน": "Queue", "การตั้งค่า": "Settings", "ประสิทธิภาพ": "Performance", "ตามระบบ": "System",
    "สว่าง": "Light", "มืด": "Dark", "ปิด (Esc)": "Close (Esc)", "ภาษา": "Language",
    "ภาษาแอป": "App language", "ธีม": "Theme",
    "CPU / GPU ที่ใช้ประมวลผล": "Settings: graphics card and language",
    "ยังหยุดงานไม่เสร็จ": "Still stopping",
    "งานที่กำลังทำยังหยุดไม่เสร็จ (เช่น กำลังดึงข้อมูลวิดีโอ หรือโหลดโมเดล AI)\nโปรดรอสักครู่แล้วปิดหน้าต่างอีกครั้ง":
        "A running job hasn't stopped yet (e.g. loading video details or the AI model).\n"
        "Please wait a moment and close the window again.",
    # common
    "วิดีโอ": "Video", "เสียง": "Audio", "เสียงอย่างเดียว": "Audio only", "ไฟล์เสียง": "Audio file",
    "ต้นฉบับ": "Source", "คงเดิม": "Keep original", "ชื่อไฟล์": "File name", "ใช้ชื่อเดิม": "Use suggested name",
    "เปลี่ยนไฟล์": "Change file", "เลือกไฟล์": "Choose file", "หรือคลิกเพื่อเลือกไฟล์": "or click to choose a file",
    "เพิ่มเข้าคิวแล้ว ✓": "Added to the queue ✓",
    "เพิ่มเข้าคิวแล้ว ✓ ผลถอดความจะขึ้นทางขวาเมื่อเสร็จ": "Added to the queue ✓ The transcript appears on the right when done",
    "กำลังอ่านข้อมูลไฟล์…": "Reading the file…", "อ่านข้อมูลไม่ได้": "Couldn't read the file",
    "แสดงในโฟลเดอร์": "Show in folder", "ดูผลถอดความ": "View transcript",
    "วางได้เฉพาะไฟล์จากเครื่องนี้": "Only files from this computer can be dropped",
    "ไม่รองรับโฟลเดอร์ กรุณาเลือกไฟล์เดียว": "Folders aren't supported; choose one file",
    "ไม่ทราบชนิด": "unknown type", "ไม่ทราบสาเหตุ": "unknown reason", "(ไม่มีชื่อ)": "(untitled)",
    "สเตอริโอ": "Stereo", "โมโน": "Mono", "ซิงก์แล้ว": "synced", "พร้อมส่งออก": "ready to export",
    "ตัวอย่าง": "Example",
    # downloader
    "บันทึกที่": "Save to", "ลิงก์": "Link", "ประเภท": "Type", "รูปแบบไฟล์": "Format",
    "ความละเอียด": "Resolution", "คุณภาพเสียง": "Audio quality", "เลือกโฟลเดอร์…": "Choose folder…",
    "เลือกโฟลเดอร์ปลายทาง": "Choose a destination folder", "วาง": "Paste", "วางลิงก์ YouTube": "Paste a YouTube link",
    "ชื่อคลิปจะขึ้นที่นี่เมื่อวางลิงก์": "The clip name appears here once a link is pasted",
    "กำลังโหลดคลิป…": "Loading clip…", "ลิงก์ YouTube ไม่ถูกต้อง": "Invalid YouTube link",
    "ลิงก์ไม่ถูกต้อง": "Invalid link", "ยังไม่ได้เลือกโฟลเดอร์": "No folder chosen",
    "เลือกโฟลเดอร์ปลายทางก่อนดาวน์โหลด": "Choose a destination folder before downloading",
    "สร้างโฟลเดอร์ไม่ได้": "Couldn't create the folder", "ไม่มีในคลิปนี้": "not in this clip",
    "MOV เกิน 1080p ต้องแปลงเป็น H.264 หลังดาวน์โหลด ใช้เวลาเพิ่ม":
        "MOV above 1080p is converted to H.264 after downloading, which takes longer",
    "เล่นได้ทุกที่": "Plays everywhere", "สำหรับ Final Cut / iMovie": "For Final Cut / iMovie",
    "เก็บได้ทุกโค้ดเดก": "Holds any codec", "สำหรับเว็บ": "For the web",
    "AAC คุณภาพดีกว่า MP3 ที่ขนาดเท่ากัน": "AAC: better than MP3 at the same size",
    "ไม่บีบอัด ไฟล์ใหญ่": "Uncompressed, large files", "Lossless ไฟล์เล็กกว่า WAV": "Lossless, smaller than WAV",
    "คุณภาพสูงที่บิตเรตต่ำ": "High quality at low bitrates", "ดาวน์โหลดวิดีโอ": "Download video",
    "กำลังเตรียมดาวน์โหลด…": "Preparing download…", "กำลังรวมวิดีโอและเสียง…": "Merging video and audio…",
    "กำลังแปลงเป็น MOV (H.264)": "Converting to MOV (H.264)", "กำลังบันทึกเป็น MOV": "Saving as MOV",
    "ดาวน์โหลดไม่สำเร็จ: ไม่พบไฟล์ผลลัพธ์": "Download failed: the output file is missing",
    "ดึงข้อมูลวิดีโอไม่สำเร็จ: ไม่พบข้อมูลวิดีโอ": "Couldn't load video details: no video found",
    "ยังไม่ได้เลือกโฟลเดอร์ปลายทาง": "No destination folder chosen", "ชื่อไฟล์ไม่ถูกต้อง": "Invalid file name",
    # converter
    "ลากไฟล์วิดีโอหรือเสียงมาวางที่นี่": "Drop a video or audio file here", "แปลงเป็น": "Convert to",
    "โค้ดเดก": "Codec", "โค้ดเดกวิดีโอ": "Video codec", "เฟรมเรต": "Frame rate", "ขนาดเดิม": "Original size",
    "เฟรมเรตเดิม": "Original frame rate", "เสียงในวิดีโอ": "Audio track", "บิตเรตเสียง": "Audio bitrate",
    "แยกไฟล์เสียงออกมาด้วย": "Also save the audio separately", "เริ่มแปลงไฟล์": "Convert",
    "ความยาว": "Duration", "ขนาด": "Size", "บิตเรตรวม": "Total bitrate", "รายละเอียดเสียง": "Audio details",
    "บิตเรต": "Bitrate", "คงเดิม (ไม่แปลงเสียง)": "Keep original (no re-encode)",
    "คงเดิม (ไม่แปลงเสียง เร็วที่สุด)": "Keep original (no re-encode, fastest)",
    "คงเดิม (ไม่เข้ารหัสใหม่)": "Keep original (no re-encode)", "Lossless · เต็มคุณภาพ": "Lossless · full quality",
    "คงเดิม: คัดลอกภาพเดิมโดยไม่เข้ารหัสใหม่ เร็วมากและไม่เสียคุณภาพ แต่ปรับคุณภาพหรือขนาดไม่ได้":
        "Keep original copies the picture without re-encoding: very fast and lossless, but size and quality can't change",
    "สำหรับ Final Cut / iMovie / Apple": "For Final Cut / iMovie / Apple", "สำหรับโปรแกรมรุ่นเก่า": "For older software",
    "AAC แบบไฟล์ดิบ (ADTS)": "Raw AAC (ADTS)", "เข้ากันได้กว้างที่สุด": "Widest compatibility",
    "ไฟล์เล็กกว่า H.264 ราวครึ่งหนึ่ง แต่แปลงช้ากว่า": "About half the size of H.264, but slower to encode",
    "ไฟล์เล็กที่สุด แต่อุปกรณ์เก่าอาจเล่นไม่ได้": "Smallest files, but older devices may not play them",
    "โค้ดเดกของเว็บ / YouTube": "The web / YouTube codec", "สำหรับตัดต่อ ไฟล์ใหญ่มาก": "For editing, very large files",
    "ไม่เข้ารหัสใหม่ เร็วมากและไม่เสียคุณภาพ": "No re-encode: very fast and lossless",
    "ไฟล์นี้ไม่มีภาพ": "This file has no picture", "ไฟล์นี้ไม่มีภาพหรือเสียงให้แปลง": "This file has no picture or sound to convert",
    "กำลังแปลงไฟล์": "Converting", "กำลังแยกเสียง": "Extracting audio",
    "GPU ใช้ไม่ได้ ใช้ CPU แทน": "GPU unavailable, using the CPU", "ไฟล์นี้ไม่มีสตรีมเสียงให้แยก": "This file has no audio to extract",
    "FFmpeg ในเครื่องนี้ไม่มีตัวเข้ารหัสที่เลือก ลองเลือกโค้ดเดกอื่น": "This FFmpeg lacks the chosen encoder; try another codec",
    "ไฟล์ปลายทางซ้ำกับไฟล์ต้นทาง ระบบไม่เขียนทับต้นฉบับ": "The output is the source file; the original is never overwritten",
    "รูปแบบ (ไม่มีนามสกุล) ไม่รองรับ": "Files without an extension aren't supported",
    "คุณภาพมาตรฐาน": "Standard quality", "คุณภาพสูง": "High quality", "สมดุล": "Balanced", "ไฟล์เล็ก": "Small file",
    # compressor
    "ลากไฟล์วิดีโอมาวางที่นี่": "Drop a video file here",
    "ลากไฟล์มาวางที่นี่": "Drop a file here",
    "ตัดคลิป": "FastCut", "คุณภาพพรีวิว": "Preview quality", "เล่น / หยุด (Space)": "Play / Pause (Space)",
    "เลือกไฟล์วิดีโอหรือเสียง": "Choose a video or audio file", "ความยาวที่ตัด": "Clip length",
    "วินาที": "seconds", "วิธีตัด": "How to cut", "เร็ว": "Fast",
    "บันทึกที่": "Save to", "เลือก…": "Choose…", "เลือกโฟลเดอร์ปลายทาง": "Choose a destination folder",
    "เล่นช่วงที่เลือก (Space)": "Play the selection (Space)", "กำลังอ่านไฟล์…": "Reading the file…",
    "ไฟล์เสียงตัดได้ตรงจุดและเร็วเสมอ": "Audio is always cut fast and exactly",
    "เริ่มตรงเฟรมที่เลือก · เข้ารหัสภาพใหม่ ใช้เวลานานขึ้น": "Starts on the exact frame · re-encodes, takes longer",
    "เสร็จในไม่กี่วินาที ไม่เข้ารหัสใหม่ · จุดเริ่มอาจเร็วกว่าที่เลือกเล็กน้อย":
        "Done in seconds, no re-encoding · may start slightly early",
    "ไฟล์นี้ไม่มีภาพหรือเสียงให้ตัด": "This file has no picture or sound to cut",
    "ช่วงที่ตัดต้องยาวอย่างน้อย 0.5 วินาที": "The clip must be at least 0.5 seconds long",
    "กำลังตัด": "Cutting", "วิดีโอและเสียง (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.flv *.wmv *.ts *.mts *.3gp *.mpg "
    "*.mp3 *.m4a *.aac *.wav *.flac *.ogg *.opus *.wma *.aif *.aiff);;ทุกไฟล์ (*)":
        "Video and audio (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.flv *.wmv *.ts *.mts *.3gp *.mpg "
        "*.mp3 *.m4a *.aac *.wav *.flac *.ogg *.opus *.wma *.aif *.aiff);;All files (*)", "ทุกไฟล์ (*)": "All files (*)",
    "วิดีโอ": "Video", "รูปภาพ": "Image", "เอกสาร Office": "Office document", "เสียง": "Audio",
    "ไฟล์ทั่วไป": "File", "ประเภท": "Type", "รูปแบบ": "Format", "วิธีบีบอัด": "Method",
    "ขนาดภาพ": "Dimensions", "จำนวนหน้า": "Pages", "รูปภาพในเอกสาร": "Pictures inside",
    "ลดคุณภาพและขนาดภาพเล็กน้อย": "Lighter quality and size",
    "ย่อรูปภาพในเอกสาร และบีบข้อมูลใหม่": "Shrinks pictures, recompresses data",
    "ย่อรูปภาพในเอกสาร และบีบไฟล์ใหม่": "Shrinks pictures, repacks the file",
    "ลดบิตเรตเสียง": "Lower audio bitrate", "บีบเป็นไฟล์ .zip (ไม่เสียข้อมูล)": "Packs into a .zip (lossless)",
    "ลดขนาดเล็กน้อย คงคุณภาพไว้มากที่สุด": "A little smaller, quality kept",
    "สมดุลระหว่างคุณภาพกับขนาด": "Balance of quality and size",
    "ไฟล์เล็กที่สุด คุณภาพอาจลดลง": "Smallest file, quality may drop",
    "กำลังย่อรูปภาพในเอกสาร": "Shrinking pictures", "กำลังบีบไฟล์": "Packing", "กำลังบีบอัดเสียง": "Compressing audio",
    "วิดีโอใช้ตัวบีบอัดวิดีโอ": "Videos use the video compressor",
    "เปิดเอกสารไม่ได้ ไฟล์อาจเสียหายหรือถูกล็อกด้วยรหัสผ่าน": "Can't open the document; it may be damaged or password-locked",
    "PDF นี้มีรหัสผ่าน บีบอัดไม่ได้": "This PDF has a password and can't be compressed", "คุณภาพ": "Quality", "หรือกำหนดบิตเรตเอง": "Or set a bitrate",
    "ขนาดโดยประมาณ": "Estimated size", "หลังบีบอัด": "Compressed", "เริ่มบีบอัด": "Compress",
    "กรอกบิตเรตเป็นตัวเลขมากกว่า 0": "Enter a bitrate above 0", "นับรวมเสียง AAC 128 kbps": "Includes 128 kbps AAC audio",
    "คุณภาพใกล้เคียงต้นฉบับ ลดขนาดเล็กน้อย": "Close to the original, slightly smaller",
    "สมดุลระหว่างคุณภาพกับขนาด · เหมาะกับการอัปโหลดโซเชียล": "Balanced quality and size · good for social media",
    "ไฟล์เล็กที่สุด ภาพอาจคมชัดน้อยลง": "Smallest file, the picture may be softer",
    "ไฟล์นี้ไม่มีภาพวิดีโอให้บีบอัด": "This file has no video to compress", "กำลังบีบอัด": "Compressing",
    "บีบอัดไฟล์": "Compress file", "ไม่พบความยาวของไฟล์": "Couldn't find the file's duration",
    "ไฟล์ปลายทางต้องไม่ใช่ไฟล์เดียวกับไฟล์ต้นฉบับ": "The output must not be the source file",
    "กรุณาระบุ Target Bitrate (kbps) เป็นตัวเลขมากกว่า 0": "Enter a target bitrate (kbps) above 0",
    # subtitles
    "ลากไฟล์เสียงหรือวิดีโอมาวางที่นี่": "Drop an audio or video file here", "โมเดล AI": "AI model",
    "ไทย": "Thai", "ภาษาไทย": "Thai", "ภาษาอังกฤษ": "English", "ผลถอดความ": "Transcript",
    "ผลถอดความจะแสดงที่นี่": "The transcript appears here", "ถอดเสียงด้วย AI": "Transcribe with AI",
    "เร็วสุด": "Fastest", "แม่นยำ": "Accurate", "เวลา": "Time", "ข้อความ": "Text",
    "ครั้งแรกที่ใช้โมเดลแต่ละขนาด จะดาวน์โหลดโมเดลจากอินเทอร์เน็ต": "Each model size is downloaded the first time it's used",
    "ไม่พบเสียงพูดในไฟล์นี้": "No speech found in this file",
    "ไม่พบเสียงพูดในไฟล์นี้ ลองเลือกภาษาหรือไฟล์อื่น": "No speech found; try another language or file",
    "ส่งออกไม่สำเร็จ": "Export failed", "ถอดเสียงซับไตเติล": "Transcribe subtitles",
    "กำลังแยกเสียงจากไฟล์": "Extracting audio",
    "กำลังโหลดโมเดล AI (ครั้งแรกต้องดาวน์โหลดจากอินเทอร์เน็ต)": "Loading the AI model (downloaded the first time)",
    "กำลังบันทึกไฟล์ซับไตเติล": "Saving subtitles", "รองรับเฉพาะไฟล์ซับไตเติลนามสกุล .srt และ .vtt": "Only .srt and .vtt subtitles are supported",
    "ไฟล์ปลายทางต้องมีนามสกุล .srt หรือ .vtt": "The output must end in .srt or .vtt",
    "ภาษาที่เลือกไม่รองรับ เลือกได้เฉพาะภาษาไทย (th) หรือภาษาอังกฤษ (en)": "Unsupported language; choose Thai (th) or English (en)",
    "ขนาดโมเดลไม่ถูกต้อง เลือกได้เฉพาะ tiny, base หรือ small": "Invalid model size; choose tiny, base or small",
    "ไม่พบไลบรารี faster-whisper กรุณาติดตั้งด้วยคำสั่ง pip install faster-whisper": "faster-whisper is missing; install it with pip install faster-whisper",
    "GPU": "GPU",
    "ตรวจจับอัตโนมัติ": "Detect automatically", "ภาษาญี่ปุ่น": "Japanese", "ภาษาเกาหลี": "Korean",
    "ภาษาจีน": "Chinese", "เสียงในไฟล์": "Audio content", "เสียงพูด": "Speech", "เพลง": "Music",
    "คลิป สัมภาษณ์ บรรยาย: กรองช่วงเงียบออก": "Clips, interviews, talks: silence is filtered out",
    "เพลงและเสียงร้อง: ไม่ตัดช่วงที่มีดนตรี และไม่ให้ AI วนซ้ำเนื้อร้อง":
        "Songs and singing: music isn't cut out and lyrics aren't repeated",
    "เร็ว": "Fast", "พอใช้": "Fair", "ดี": "Good", "แม่นยำ · แนะนำ": "Accurate · recommended",
    "แม่นยำที่สุด · ช้า": "Most accurate · slow",
    "ครั้งแรกที่ใช้แต่ละโมเดลจะดาวน์โหลดก่อน (large ประมาณ 1.5–3 GB)":
        "Each model downloads the first time it's used (large: about 1.5–3 GB)",
    "ภาษาที่เลือกไม่รองรับ": "Unsupported language", "ขนาดโมเดลไม่ถูกต้อง": "Invalid model size",
    "ประเภทเสียงไม่ถูกต้อง": "Invalid audio type",
    # queue, pill, toasts
    "ยังไม่มีงาน": "No jobs yet", "ล้าง": "Clear", "ล้างงานที่เสร็จแล้วออกจากคิว": "Remove finished jobs",
    "ลบงานทั้งหมดออกจากคิว": "Remove all jobs", "แสดงไฟล์": "Show file", "ลบออกจากคิว": "Remove from queue",
    "เปิดโฟลเดอร์ที่เก็บไฟล์นี้": "Open the folder with this file",
    "รอในคิว": "Waiting", "กำลังประมวลผล": "Working", "เสร็จแล้ว": "Done", "ผิดพลาด": "Failed",
    "ยกเลิกแล้ว": "Cancelled", "ยกเลิกงาน": "Cancel job", "ยกเลิกงานนี้": "Cancel this job",
    "กำลังยกเลิก…": "Cancelling…", "กำลังเริ่ม…": "Starting…", "ยกเลิกงานแล้ว": "Job cancelled",
    "เสร็จสิ้น": "Finished", "งาน": "jobs", "งานสื่อ": "Media job",
    # settings panel
    "ใช้การ์ดจอช่วยประมวลผล": "Use the graphics card", "แปลงและบีบอัดวิดีโอเร็วขึ้น ใช้ CPU น้อยลง":
        "Faster converting and compressing, less CPU", "การ์ดจอที่ใช้": "Graphics card",
    "กำลังตรวจสอบการ์ดจอ…": "Checking graphics cards…", "ไม่พบการ์ดจอที่ใช้ได้": "No usable graphics card found",
    "ซับไตเติล AI ใช้ GPU (CUDA)": "AI subtitles use the GPU (CUDA)", "การ์ดจอ NVIDIA": "NVIDIA graphics card",
    "อ่านข้อมูลไฟล์ไม่ได้ ไฟล์อาจเสียหาย": "Couldn't read the file; it may be damaged",
    # app version, GPU pack, uninstall
    "เวอร์ชันโปรแกรม": "App version", "เป็นเวอร์ชันล่าสุด": "Up to date", "อัปเดต": "Update",
    "ติดตั้ง": "Install", "เปลี่ยนเวอร์ชัน": "Change version",
    "ทรัพยากรการ์ดจอเพิ่มเติม": "Extra GPU resources", "ดาวน์โหลด": "Download", "ถอนการติดตั้ง": "Uninstall",
    "ใช้ได้เฉพาะ Windows ที่มีการ์ดจอ NVIDIA": "Windows with an NVIDIA graphics card only",
    "ต้องมีการ์ดจอ NVIDIA จึงจะใช้ได้": "Needs an NVIDIA graphics card",
    "ให้ซับไตเติล AI ทำงานบนการ์ดจอ NVIDIA เร็วขึ้นราว 2.5 เท่า ขนาดดาวน์โหลดประมาณ 1.3 GB":
        "Runs AI subtitles on your NVIDIA card, about 2.5x faster. About 1.3 GB to download",
    "ถอนการติดตั้ง ClipHUB": "Uninstall ClipHUB", "ลบ": "Delete", "ทำต่อ": "Continue",
    "มีงานที่ยังทำอยู่": "Jobs are still running",
    "งานในคิวจะถูกยกเลิก ต้องการทำต่อหรือไม่": "Jobs in the queue will be cancelled. Continue?",
    "อัปเดตได้เฉพาะโปรแกรมที่ติดตั้งแล้ว (ไฟล์ .exe / .app) ดาวน์โหลดเวอร์ชันใหม่ได้ที่ GitHub":
        "Only the installed app (.exe / .app) can update itself. Download new versions from GitHub",
    "ดาวน์โหลดประมาณ 1.3 GB และใช้พื้นที่ประมาณ 2 GB ต้องการดาวน์โหลดตอนนี้หรือไม่":
        "About 1.3 GB to download and 2 GB on disk. Download now?",
    "กำลังดาวน์โหลดทรัพยากรการ์ดจอ…": "Downloading GPU resources…",
    "ติดตั้งทรัพยากรการ์ดจอแล้ว": "GPU resources installed", "ซับไตเติล AI ใช้การ์ดจอได้แล้ว":
        "AI subtitles can now use the graphics card",
    "ลบทรัพยากรการ์ดจอเพิ่มเติม ซับไตเติล AI จะกลับไปใช้ CPU":
        "Delete the extra GPU resources? AI subtitles will use the CPU again",
    "บางไฟล์ยังถูกใช้งานอยู่ จะลบเสร็จเมื่อเปิดโปรแกรมครั้งถัดไป":
        "Some files are in use; they will be deleted the next time ClipHUB starts",
    "ลบ ClipHUB, FFmpeg, ทรัพยากรการ์ดจอ และการตั้งค่าทั้งหมด ออกจากเครื่องนี้ ย้อนกลับไม่ได้":
        "Remove ClipHUB, FFmpeg, the GPU resources and all settings from this computer? This can't be undone",
    "กด Esc เพื่อยกเลิก": "Press Esc to cancel",
    "ลบ ClipHUB, FFmpeg, ทรัพยากรการ์ดจอ, โมเดล AI ซับไตเติล และการตั้งค่าทั้งหมด ออกจากเครื่องนี้ ย้อนกลับไม่ได้":
        "Removes ClipHUB, FFmpeg, the GPU resources, the AI subtitle models and all settings from this computer. "
        "This can't be undone",
    "ย้อนกลับไม่ได้": "This can't be undone", "ตกลง": "OK", "ยกเลิก": "Cancel", "กำลังถอนการติดตั้ง… อย่าปิดโปรแกรม": "Uninstalling… don't close ClipHUB",
    "ถอนการติดตั้งเสร็จแล้ว": "Uninstalled", "ไม่สำเร็จ: ": "Failed: ",
}

Pattern = tuple[re.Pattern, Callable[[re.Match], str]]


def _p(regex: str, build: Callable[..., str]) -> Pattern:
    return re.compile(regex, re.S), lambda m: build(*m.groups())


PATTERNS: list[Pattern] = [
    _p(r"^กำลังดาวน์โหลด ([\d.]+) / ([\d.]+) MB$", lambda a, b: f"Downloading {a} / {b} MB"),
    _p(r"^กำลังดาวน์โหลด ([\d.]+) MB$", lambda a: f"Downloading {a} MB"),
    _p(r"^กำลังถอดเสียง \((.+?)\)$", lambda a: f"Transcribing ({tr(a)})"),
    _p(r"^กำลังบันทึกเป็น (.+)…$", lambda a: f"Saving as {a}…"),
    _p(r"^(\d+) ประโยค$", lambda a: f"{a} sentence{'s' if a != '1' else ''}"),
    _p(r"^(\d+) งาน$", lambda a: f"{a} job{'s' if a != '1' else ''}"),
    _p(r"^\+(\d+) งาน$", lambda a: f"+{a} more"),
    _p(r"^(\d+) เธรด$", lambda a: f"{a} threads"),
    _p(r"^CPU (\d+) เธรด$", lambda a: f"CPU {a} threads"),
    _p(r"^(\d+) ช่อง$", lambda a: f"{a} channels"),
    _p(r"^ภาษา(.+)$", lambda a: tr(a) if tr(a) != a else a),
    _p(r"^มีเวอร์ชันใหม่ (v[\d.]+)$", lambda a: f"New version {a}"),
    _p(r"^อัปเดตเป็น (v[\d.]+)$", lambda a: f"Update to {a}"),
    _p(r"^กำลังดาวน์โหลด (v[\d.]+)…$", lambda a: f"Downloading {a}…"),
    _p(r"^(v[\d.]+) \(ปัจจุบัน\)$", lambda a: f"{a} (current)"),
    _p(r"^ย้อนกลับไปใช้ (v[\d.]+) ใช่ไหม โปรแกรมจะปิดและเปิดใหม่เอง$",
       lambda a: f"Go back to {a}? ClipHUB will close and reopen by itself"),
    _p(r"^ติดตั้ง (v[\d.]+)$", lambda a: f"Install {a}"),
    _p(r"^ติดตั้งแล้ว \(([\d.]+) GB\) ซับไตเติล AI ใช้การ์ดจอ NVIDIA$",
       lambda a: f"Installed ({a} GB). AI subtitles use the NVIDIA card"),
    _p(r"^ทุกการ์ด \((\d+) ใบ กระจายงาน\)$", lambda a: f"All {a} cards (work is shared)"),
    _p(r"^ธีม · (.+) \(คลิกเพื่อเปลี่ยน\)$", lambda a: f"Theme · {tr(a)} (click to change)"),
    _p(r"^บันทึก (.+) แล้ว$", lambda a: f"Saved {a}"),
    _p(r"^บันทึกใน (.+)$", lambda a: f"Saved in {a}"),
    _p(r"^บันทึกไฟล์แล้ว: (.+)$", lambda a: f"Saved: {a}"),
    _p(r"^บันทึกไฟล์ MOV ไม่สำเร็จ: (.+)$", lambda a: f"Couldn't save the MOV file: {a}"),
    _p(r"^บันทึกไฟล์ซับไตเติลไม่สำเร็จ: (.+)$", lambda a: f"Couldn't save the subtitles: {a}"),
    _p(r"^บันทึกไฟล์ปลายทางไม่สำเร็จ: (.+)$", lambda a: f"Couldn't save the output: {a}"),
    _p(r"^ประหยัดพื้นที่ (.+)$", lambda a: f"Saves {a}"),
    _p(r"^ไฟล์อาจใหญ่ขึ้น (.+) ลองลดบิตเรต$", lambda a: f"May grow by {a}; try a lower bitrate"),
    _p(r"^ลดลง (\d+)%$", lambda a: f"{a}% smaller"),
    _p(r"^ใหญ่ขึ้น (\d+)%$", lambda a: f"{a}% larger"),
    _p(r"^รับครั้งละ 1 ไฟล์ — ใช้ไฟล์แรก \((.+)\)$", lambda a: f"One file at a time — using the first ({a})"),
    _p(r"^รูปแบบ \.(.+) ไม่รองรับ$", lambda a: f"The .{a} format isn't supported"),
    _p(r"^รูปแบบเสียง \.(.+) ไม่รองรับในโหมดแยกเสียง \(รองรับเฉพาะ MP3 / WAV\)$",
       lambda a: f".{a} can't be used for separated audio (MP3 / WAV only)"),
    _p(r"^วิดีโอ (.+) ใส่ในไฟล์ \.(\w+) แบบคงเดิมไม่ได้ ให้เลือกโค้ดเดกอื่น$",
       lambda a, b: f"{a} video can't be kept as-is in a .{b} file; choose another codec"),
    _p(r"^เสียง (.+) ใส่ในไฟล์ \.(\w+) แบบคงเดิมไม่ได้ ให้เลือกแบบอื่น$",
       lambda a, b: f"{a} audio can't be kept as-is in a .{b} file; choose another option"),
    _p(r"^ไฟล์ \.(\w+) ใช้โค้ดเดกวิดีโอ (.+) ไม่ได้$", lambda a, b: f"A .{a} file can't use the {b} video codec"),
    _p(r"^ไฟล์ \.(\w+) ใช้โค้ดเดกเสียง (.+) ไม่ได้$", lambda a, b: f"A .{a} file can't use the {b} audio codec"),
    _p(r"^สร้างโฟลเดอร์ปลายทางไม่ได้: (.+)$", lambda a: f"Couldn't create the destination folder: {a}"),
    _p(r"^ส่งออก (.+)$", lambda a: f"Export {a}"),
    _p(r"^อ่านไฟล์ไม่ได้: (.+)$", lambda a: f"Couldn't read the file: {a}"),
    _p(r"^เกิดข้อผิดพลาดไม่คาดคิด: (.+)$", lambda a: f"Unexpected error: {a}"),
    _p(r"^เรียกใช้ FFmpeg ไม่ได้: (.+)$", lambda a: f"Couldn't start FFmpeg: {a}"),
    _p(r"^FFmpeg ทำงานไม่สำเร็จ: (.+)$", lambda a: f"FFmpeg failed: {a}"),
    _p(r"^FFmpeg ในเครื่องนี้ไม่มี (.+)$", lambda a: f"This FFmpeg has no {a}"),
    _p(r"^ดาวน์โหลดไม่สำเร็จ: (.+)$", lambda a: f"Download failed: {a}"),
    _p(r"^ดึงข้อมูลวิดีโอไม่สำเร็จ: (.+)$", lambda a: f"Couldn't load video details: {a}"),
    _p(r"^โหลดโมเดล AI \((.+?)\) ไม่สำเร็จ หากใช้งานครั้งแรกกรุณาตรวจสอบการเชื่อมต่ออินเทอร์เน็ต: (.+)$",
       lambda a, b: f"Couldn't load the AI model ({a}). On first use, check your internet connection: {b}"),
    _p(r"^ไม่พบโปรแกรม (\S+) กรุณาติดตั้ง FFmpeg ก่อนใช้งาน$", lambda a: f"{a} not found. Please install FFmpeg first"),
    _p(r"^ไม่พบไฟล์ต้นทาง: (.+)$", lambda a: f"Source file not found: {a}"),
    _p(r"^ไม่รองรับความละเอียด: (.+)$", lambda a: f"Unsupported resolution: {a}"),
    _p(r"^ไม่รองรับบิตเรตเสียง:? (.+?)( kbps)?$", lambda a, _k: f"Unsupported audio bitrate: {a}"),
    _p(r"^ไม่รองรับรูปแบบวิดีโอ: (.+)$", lambda a: f"Unsupported video format: {a}"),
    _p(r"^ไม่รองรับรูปแบบเสียง: (.+)$", lambda a: f"Unsupported audio format: {a}"),
    _p(r"^ไม่รู้จักระดับคุณภาพ: (.+)$", lambda a: f"Unknown quality level: {a}"),
    _p(r"^ไม่รู้จักโค้ดเดกวิดีโอ: (.+)$", lambda a: f"Unknown video codec: {a}"),
    _p(r"^ไม่รู้จักโค้ดเดกเสียง: (.+)$", lambda a: f"Unknown audio codec: {a}"),
    _p(r"^ไม่รู้จักโหมดการบีบอัด: (.+)$", lambda a: f"Unknown compression mode: {a}"),
    _p(r"^(\d+) หน้า$", lambda a: f"{a} page" if a == "1" else f"{a} pages"),
    _p(r"^([\d.]+) วินาที$", lambda a: f"{a} s"),
    _p(r"^กำลังตัด · GPU ใช้ไม่ได้ ใช้ CPU แทน$", lambda: "Cutting · GPU unavailable, using the CPU"),
    _p(r"^(\d+) รูป$", lambda a: f"{a} picture" if a == "1" else f"{a} pictures"),
    _p(r"^บีบอัดไฟล์นี้ไม่ได้: (.+)$", lambda a: f"Couldn't compress this file: {a}"),
    _p(r"^(.+): ต้องอัปเดตไดรเวอร์ NVIDIA เป็นเวอร์ชัน (.+) ขึ้นไป จึงจะใช้เร่งความเร็วได้$",
       lambda a, b: f"{a}: update the NVIDIA driver to {b} or newer to use acceleration"),
    _p(r"^(.+): ใช้ NVENC ไม่ได้$", lambda a: f"{a}: NVENC is unavailable"),
    _p(r"^(.+) — ยังใช้ไม่ได้$", lambda a: f"{a} — not usable yet"),
    _p(r"^(.+?)\s+—\s+ไม่มีในคลิปนี้$", lambda a: f"{tr(a)}  —  not in this clip"),
    _p(r"^ตัวอย่าง: (.+)$", lambda a: f"Example: {a}"),
    _p(r"^(.+?)\s+\((Ctrl\+.+)\)$", lambda a, b: f"{tr(a)}  ({b})"),
    _p(r"^(.+) เสร็จแล้ว$", lambda a: f"{a} finished"),
    _p(r"^(.+) ไม่สำเร็จ$", lambda a: f"{a} failed"),
]

_lang = "th"


class _Notifier(QObject):
    changed = Signal(str)


notifier = _Notifier()


def language() -> str:
    return _lang


def tr(text: str) -> str:
    """The text in the current language (Thai is returned as is)."""
    if _lang == "th" or not isinstance(text, str) or not text:
        return text
    if text in EN:
        return EN[text]
    for pattern, build in PATTERNS:
        match = pattern.match(text)
        if match:
            return build(match)
    for separator in ("\n", " · "):
        if separator in text:
            return separator.join(tr(part) for part in text.split(separator))
    return text


# --- hooks on the Qt text setters -----------------------------------------------------------

_raw: dict[str, Callable] = {}


def installed() -> bool:
    return bool(_raw)


def raw_label_text(label: QLabel, text: str) -> None:
    """Set a label's text without translating or remembering it (e.g. elided display text)."""
    _raw["QLabel.setText"](label, text)


def _wrap_init(cls) -> None:
    original = cls.__init__

    def __init__(self, *args, **kwargs):  # noqa: N807 - wrapper for the Qt constructor
        original(self, *args, **kwargs)
        text = next((a for a in args if isinstance(a, str)), None)
        if text:
            self.setText(text)  # goes through the translating setter

    cls.__init__ = __init__


def install() -> None:
    """Wrap the Qt text setters once, before any window is built."""
    if _raw:
        return
    _raw["QLabel.setText"] = QLabel.setText
    _raw["QAbstractButton.setText"] = QAbstractButton.setText
    _raw["QLineEdit.setPlaceholderText"] = QLineEdit.setPlaceholderText
    _raw["QWidget.setToolTip"] = QWidget.setToolTip
    _raw["QComboBox.addItem"] = QComboBox.addItem
    _raw["QStandardItem.__init__"] = QStandardItem.__init__
    _raw["QStandardItem.setText"] = QStandardItem.setText
    _raw["QTableWidget.setHorizontalHeaderLabels"] = QTableWidget.setHorizontalHeaderLabels

    def label_set_text(self, text):
        if isinstance(text, str):
            self.setProperty("_i18n_text", text)
            text = tr(text)
        _raw["QLabel.setText"](self, text)

    def button_set_text(self, text):
        if isinstance(text, str):
            self.setProperty("_i18n_text", text)
            text = tr(text)
        _raw["QAbstractButton.setText"](self, text)

    def set_placeholder(self, text):
        self.setProperty("_i18n_placeholder", text)
        _raw["QLineEdit.setPlaceholderText"](self, tr(text))

    def set_tooltip(self, text):
        self.setProperty("_i18n_tooltip", text)
        _raw["QWidget.setToolTip"](self, tr(text))

    def combo_add_item(self, *args):
        texts = [a for a in args if isinstance(a, str)]
        if not texts:
            return _raw["QComboBox.addItem"](self, *args)
        source = texts[0]
        args = tuple(tr(a) if a is source else a for a in args)
        _raw["QComboBox.addItem"](self, *args)
        self.setItemData(self.count() - 1, source, SOURCE_ROLE)

    def standard_item_init(self, *args):
        _raw["QStandardItem.__init__"](self, *args)
        text = next((a for a in args if isinstance(a, str)), None)
        if text:
            self.setText(text)

    def standard_item_set_text(self, text):
        self.setData(text, SOURCE_ROLE)  # combos built from models keep their source here
        _raw["QStandardItem.setText"](self, tr(text))

    def set_header_labels(self, labels):
        self.setProperty("_i18n_headers", list(labels))
        _raw["QTableWidget.setHorizontalHeaderLabels"](self, [tr(t) for t in labels])

    QLabel.setText = label_set_text
    QAbstractButton.setText = button_set_text
    QLineEdit.setPlaceholderText = set_placeholder
    QWidget.setToolTip = set_tooltip
    QComboBox.addItem = combo_add_item
    QStandardItem.__init__ = standard_item_init
    QStandardItem.setText = standard_item_set_text
    QTableWidget.setHorizontalHeaderLabels = set_header_labels
    for cls in (QLabel, QPushButton, QCheckBox):
        _wrap_init(cls)


def set_language(lang: str) -> None:
    """Switch every remembered text in every window to `lang` ('th' or 'en')."""
    global _lang
    if lang not in LANGUAGES:
        return
    _lang = lang
    app = QApplication.instance()
    if app is None:
        return
    for widget in app.allWidgets():
        _retranslate(widget)
    notifier.changed.emit(lang)


def _retranslate(widget: QWidget) -> None:
    from ui.widgets import ElidedLabel  # avoid an import cycle at module load

    if isinstance(widget, ElidedLabel):
        widget.retranslate()
    else:
        text = widget.property("_i18n_text")
        if isinstance(text, str):
            if isinstance(widget, QLabel):
                _raw["QLabel.setText"](widget, tr(text))
            elif isinstance(widget, QAbstractButton):
                _raw["QAbstractButton.setText"](widget, tr(text))
    tip = widget.property("_i18n_tooltip")
    if isinstance(tip, str):
        _raw["QWidget.setToolTip"](widget, tr(tip))
    placeholder = widget.property("_i18n_placeholder")
    if isinstance(placeholder, str) and isinstance(widget, QLineEdit):
        _raw["QLineEdit.setPlaceholderText"](widget, tr(placeholder))
    if isinstance(widget, QComboBox):
        for index in range(widget.count()):
            source = widget.itemData(index, SOURCE_ROLE)
            if isinstance(source, str):
                widget.setItemText(index, tr(source))
    headers = widget.property("_i18n_headers")
    if headers and isinstance(widget, QTableWidget):
        _raw["QTableWidget.setHorizontalHeaderLabels"](widget, [tr(t) for t in headers])
