<p align="center"><img src="assets/icons/cliphub.png" width="128" alt="ClipHUB icon"></p>

# ClipHUB

[![ClipHUB CI](https://github.com/DIEWLAND3012/ClipHub/actions/workflows/build.yml/badge.svg)](https://github.com/DIEWLAND3012/ClipHub/actions/workflows/build.yml)
[![Latest release](https://img.shields.io/github/v/release/DIEWLAND3012/ClipHub)](https://github.com/DIEWLAND3012/ClipHub/releases/latest)

เดสก์ท็อปแอปจัดการและแปลงสื่อ พร้อมถอดซับไตเติลด้วย AI สำหรับ Windows 11 และ macOS
ทำด้วย PySide6, FFmpeg, yt-dlp และ faster-whisper (โครงงานรายวิชา SDD2)

## ดาวน์โหลด

[![Download for Windows](https://img.shields.io/badge/Download-Windows%20(.exe)-0078D6?style=for-the-badge&logo=windows&logoColor=white)](https://github.com/DIEWLAND3012/ClipHub/releases/latest/download/ClipHUB-Windows.exe)
[![Download for macOS](https://img.shields.io/badge/Download-macOS%20(.dmg)-000000?style=for-the-badge&logo=apple&logoColor=white)](https://github.com/DIEWLAND3012/ClipHub/releases/latest/download/ClipHUB-macOS.dmg)

| ระบบ | ไฟล์ | วิธีติดตั้ง |
|---|---|---|
| Windows 11 | `ClipHUB-Windows.exe` | ดับเบิลคลิกเพื่อเปิดได้เลย |
| macOS (Apple Silicon) | `ClipHUB-macOS.dmg` | เปิดไฟล์ แล้วลาก ClipHUB ไปไว้ในโฟลเดอร์ Applications |

> **FFmpeg ติดตั้งให้อัตโนมัติ:** เปิดแอปครั้งแรก ถ้าเครื่องยังไม่มี FFmpeg แอปจะดาวน์โหลดให้เองระหว่างหน้าโหลด (ประมาณ 110 MB บน Windows, 30 MB บน macOS ต้องต่ออินเทอร์เน็ต) ครั้งต่อไปเปิดได้ทันที และจะอัปเดต FFmpeg ให้เองเมื่อมีเวอร์ชันใหม่ ถ้าเครื่องมี FFmpeg อยู่แล้ว แอปจะใช้ตัวนั้นเลย
>
> **ไฟล์ติดตั้งยังไม่ได้ลงนามดิจิทัล:** Windows อาจขึ้นคำเตือน SmartScreen ให้กด *More info → Run anyway* ส่วน macOS ให้คลิกขวาที่ ClipHUB แล้วเลือก *Open* หรือไปที่ *System Settings → Privacy & Security → Open Anyway*

## มีอะไรใหม่ใน v2.0

ClipHUB เวอร์ชันใหม่ที่ออกแบบใหม่ทั้งหมด ([รายละเอียดทั้งหมด](CHANGELOG.md))

- **หน้าตาแบบ Liquid Glass** สไตล์ Apple มีธีมสว่าง มืด และตามระบบ พร้อมไอคอนใหม่และหน้าจอเปิดเครื่องแบบ Mac
- **ทำทีละขั้นตอน** ทุกหน้าใช้รูปแบบเดียวกัน ปุ่มเริ่มงานอยู่ตำแหน่งเดิมเสมอ
- **ใช้การ์ดจอเร่งความเร็ว** NVIDIA NVENC, Intel Quick Sync, AMD AMF, Apple VideoToolbox และทำหลายงานพร้อมกัน
- **ซับไตเติลเพลงแม่นขึ้นมาก** ตรวจภาษาอัตโนมัติ รองรับ ไทย อังกฤษ ญี่ปุ่น เกาหลี จีน และมีโหมดเพลง
- **สลับภาษาไทย / อังกฤษ** ได้ทันทีในหน้าการตั้งค่า

## ฟีเจอร์

1. **ดาวน์โหลดวิดีโอ YouTube** เลือกโฟลเดอร์ วางลิงก์ (โหลดข้อมูลคลิปเอง) เลือกวิดีโอหรือเสียง ฟอร์แมต (MP4, MOV, MKV, WEBM / MP3, M4A, WAV…) ความละเอียด 144p–4K หรือบิตเรตเสียง แล้วตั้งชื่อไฟล์
2. **แปลงไฟล์** วางไฟล์วิดีโอหรือเสียง ดูรายละเอียดไฟล์ เลือกฟอร์แมตและ codec แล้วตั้งชื่อไฟล์
3. **บีบอัดไฟล์ทุกประเภท** วิดีโอ (โหมด High, Balanced, Small หรือกำหนด kbps เอง พร้อมประเมินขนาด) รูปภาพ (JPG, PNG, WebP…) PDF เอกสาร Office (DOCX, XLSX, PPTX…) และเสียง ส่วนไฟล์อื่นบีบเป็น .zip แบบไม่เสียข้อมูล
4. **FastCut** ตัดวิดีโอหรือเพลงแบบเร็ว ๆ ดูพรีวิวพร้อมเล่นได้ เลือกช่วงด้วยกล่องบนแถบเสียงแบบเลือกเพลงใน IG (ลากย้าย ยืดหัวท้าย หรือพิมพ์จำนวนวินาที) แล้วเลือกที่เก็บ ตั้งชื่อ และ Export
5. **ซับไตเติลด้วย AI** ถอดเสียงพูดหรือเนื้อเพลงเป็นไฟล์ `.srt` และ `.vtt` ด้วย faster-whisper พร้อมดูตัวอย่างก่อนส่งออก

ทุกงานเข้าคิว (ปุ่ม **คิว** ในแถบด้านซ้าย) ดูความคืบหน้า ยกเลิก หรือรอให้เสร็จได้

## รันจากซอร์ส

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

ถ้ามีการ์ดจอ NVIDIA และอยากให้ซับไตเติล AI ทำงานบน GPU (เร็วขึ้นราว 2.5 เท่า) ให้ติดตั้งไลบรารี CUDA เพิ่ม (ประมาณ 2 GB ไม่ติดตั้งก็ใช้งานได้ แอปจะใช้ CPU แทน):

```bash
pip install -r requirements-gpu.txt
```

การแปลงและบีบอัดวิดีโอใช้ GPU ให้อัตโนมัติ (NVIDIA NVENC, Intel Quick Sync, AMD AMF, Apple VideoToolbox) เลือกการ์ดจอได้ที่ปุ่ม **การตั้งค่า** ในแถบด้านซ้าย การ์ด NVIDIA ต้องใช้ไดรเวอร์ที่รองรับ FFmpeg รุ่นที่ติดตั้ง

## สร้างไฟล์ติดตั้งเอง

```bash
pip install pyinstaller
pyinstaller --noconfirm packaging/ClipHUB.spec
```

macOS จะได้ `dist/ClipHUB.app` ส่วน Windows จะได้ `dist/ClipHUB.exe` (ไฟล์เดียว ประมาณ 155 MB)

## การปล่อยเวอร์ชัน (สำหรับทีม)

สร้าง tag ที่ `main` แล้วผลักขึ้น GitHub ระบบ CI จะ build ทั้งสองระบบ และสร้าง Release ให้อัตโนมัติ โดยใช้ชื่อไฟล์เดิมเสมอ เพื่อให้ปุ่มดาวน์โหลดด้านบนชี้ไปที่เวอร์ชันล่าสุดได้

```bash
git tag -a v2.0.0 -m "ClipHUB v2.0.0"
git push origin v2.0.0
```

## ทดสอบ

```bash
python3 -m unittest discover -s tests -t . -v
```

## เอกสาร

- ข้อกำหนดความต้องการซอฟต์แวร์ (SRS): [cliphub-srs.md](cliphub-srs.md)
- บันทึกการเปลี่ยนแปลง: [CHANGELOG.md](CHANGELOG.md)

## ทีมพัฒนา

1. นางสาววรัทยา รอดเมล์ รหัสนักศึกษา 68030258
2. นายวศิน แก้วมรกต รหัสนักศึกษา 68030262
3. นายวสุรัชต์ สมเด็จ รหัสนักศึกษา 68030263
4. นายวัฒนพงศ์ พรหมภิราม รหัสนักศึกษา 68030265
