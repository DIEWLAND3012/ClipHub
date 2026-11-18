<p align="center"><img src="assets/icons/cliphub.png" width="128" alt="ClipHUB icon"></p>

<h1 align="center">ClipHUB</h1>

<p align="center">
ดาวน์โหลด แปลง บีบอัด ตัด และถอดซับไตเติล ครบในแอปเดียว สำหรับ Windows และ macOS
</p>

<p align="center">
<a href="https://github.com/DIEWLAND3012/ClipHub/actions/workflows/build.yml"><img src="https://github.com/DIEWLAND3012/ClipHub/actions/workflows/build.yml/badge.svg" alt="ClipHUB CI"></a>
<a href="https://github.com/DIEWLAND3012/ClipHub/releases/latest"><img src="https://img.shields.io/github/v/release/DIEWLAND3012/ClipHub" alt="Latest release"></a>
</p>

## ดาวน์โหลด

[![Download for Windows](https://img.shields.io/badge/Download-Windows%20(.exe)-0078D6?style=for-the-badge&logo=windows&logoColor=white)](https://github.com/DIEWLAND3012/ClipHub/releases/download/v2.1.0/ClipHUB-Windows-v2.1.0.exe)
[![Download for macOS](https://img.shields.io/badge/Download-macOS%20(.dmg)-000000?style=for-the-badge&logo=apple&logoColor=white)](https://github.com/DIEWLAND3012/ClipHub/releases/download/v2.1.0/ClipHUB-macOS-v2.1.0.dmg)

| ระบบ | ไฟล์ | วิธีติดตั้ง |
|---|---|---|
| Windows 10 / 11 | `ClipHUB-Windows-v2.1.0.exe` | ดับเบิลคลิกเปิดได้เลย ไม่ต้องติดตั้ง |
| macOS (Apple Silicon) | `ClipHUB-macOS-v2.1.0.dmg` | เปิดไฟล์ แล้วลาก ClipHUB ไปไว้ในโฟลเดอร์ Applications |

**ไม่ต้องติดตั้งอะไรเพิ่ม** เปิดแอปครั้งแรก ClipHUB จะตรวจว่าเครื่องมี FFmpeg หรือยัง ถ้ายังไม่มีจะดาวน์โหลดให้เองระหว่างหน้าโหลด (Windows ประมาณ 80 MB, macOS ประมาณ 30 MB ต้องต่ออินเทอร์เน็ต) ครั้งต่อไปเปิดได้ทันที และแอปจะอัปเดต FFmpeg ให้เองเมื่อมีเวอร์ชันใหม่

> **ไฟล์ติดตั้งยังไม่ได้ลงนามดิจิทัล** Windows อาจขึ้นคำเตือน SmartScreen ให้กด *More info → Run anyway* ส่วน macOS ให้คลิกขวาที่ ClipHUB แล้วเลือก *Open* หรือไปที่ *System Settings → Privacy & Security → Open Anyway*

## สเปคคอมพิวเตอร์

### Windows

| | ขั้นต่ำ | แนะนำ |
|---|---|---|
| ระบบปฏิบัติการ | Windows 10 64 บิต | Windows 11 |
| CPU | 4 คอร์ เช่น Intel Core i3 รุ่นที่ 8 / AMD Ryzen 3 | 6 คอร์ขึ้นไป เช่น Intel Core i5 รุ่นที่ 10 / AMD Ryzen 5 3600 |
| RAM | 4 GB (ซับไตเติล AI ต้องการ 8 GB) | 16 GB |
| การ์ดจอ | ไม่ต้องมี (ใช้ CPU ทำงานแทน) | NVIDIA GTX 1650 ขึ้นไป, Intel Quick Sync หรือ AMD Radeon RX 5000 ขึ้นไป |
| พื้นที่ว่าง | 1 GB | 5 GB บน SSD |
| อินเทอร์เน็ต | ต้องใช้ตอนเปิดครั้งแรกและตอนดาวน์โหลดคลิป | — |

### macOS

| | ขั้นต่ำ | แนะนำ |
|---|---|---|
| ระบบปฏิบัติการ | macOS 12 Monterey | macOS 14 Sonoma ขึ้นไป |
| ชิป | Apple M1 (ยังไม่รองรับ Mac ชิป Intel) | Apple M2 ขึ้นไป |
| RAM | 8 GB | 16 GB |
| พื้นที่ว่าง | 1 GB | 5 GB |
| อินเทอร์เน็ต | ต้องใช้ตอนเปิดครั้งแรกและตอนดาวน์โหลดคลิป | — |

> สเปคขั้นต่ำใช้งานได้ทุกเครื่องมือ แต่การแปลง บีบอัด และถอดซับไตเติลจะช้ากว่า ถ้ามีการ์ดจอที่รองรับ แอปจะใช้เร่งความเร็วให้เอง ถ้าเครื่องสเปคไม่สูง ในหน้าตัดคลิปให้ปรับคุณภาพพรีวิวเป็น 1/2 หรือ 1/4

## ฟีเจอร์

| | เครื่องมือ | ทำอะไรได้ |
|---|---|---|
| ⬇️ | **ดาวน์โหลด** | วางลิงก์ YouTube แล้วข้อมูลคลิปขึ้นเอง เลือกวิดีโอ (MP4, MOV, MKV, WebM · 144p–4K) หรือเสียง (MP3, M4A, WAV… · เลือกบิตเรต) แล้วตั้งชื่อไฟล์ |
| 🔄 | **แปลงไฟล์** | วางไฟล์วิดีโอหรือเสียง ดูรายละเอียดไฟล์ แล้วเลือกฟอร์แมตและ codec |
| 🗜️ | **บีบอัด** | บีบได้ทุกไฟล์: วิดีโอ (ประเมินขนาดก่อนทำ), รูปภาพ (JPG, PNG, WebP…), PDF, เอกสาร Office (DOCX, XLSX, PPTX…), เสียง และไฟล์อื่น ๆ เป็น .zip ไฟล์ที่ได้ไม่มีวันใหญ่กว่าเดิม |
| ✂️ | **ตัดคลิป** (FastCut) | ดูพรีวิวแล้วเลือกช่วงบนแถบเสียง ลากกล่องย้าย ยืดหัวท้าย หรือพิมพ์จำนวนวินาที ตัดตรงเฟรม ปรับคุณภาพพรีวิว Full–1/8 สำหรับเครื่องสเปกไม่สูง |
| 💬 | **ซับไตเติล** | ถอดเสียงพูดหรือเนื้อเพลงเป็น `.srt` / `.vtt` ด้วย AI (faster-whisper) ตรวจภาษาอัตโนมัติ รองรับไทย อังกฤษ ญี่ปุ่น เกาหลี จีน |

และ

- **คิวงาน** ทำหลายงานพร้อมกัน ดูความคืบหน้า กด "แสดงไฟล์" เพื่อเปิดไฟล์ที่ทำเสร็จ ลบทีละงานหรือล้างทั้งหมด
- **ใช้การ์ดจอเร่งความเร็ว** NVIDIA NVENC, Intel Quick Sync, AMD AMF, Apple VideoToolbox (เลือกการ์ดได้ที่ **การตั้งค่า**)
- **ไทย / อังกฤษ** สลับภาษาได้ทันทีในหน้าการตั้งค่า
- **หน้าตาแบบ Liquid Glass** ธีมสว่าง มืด หรือตามระบบ

### ปุ่มลัด

| ปุ่ม | ทำอะไร |
|---|---|
| `Ctrl` + `1`–`5` | สลับไปเครื่องมือที่ 1–5 |
| `Ctrl` + `J` | เปิด / ปิดคิวงาน |
| `Space` | เล่น / หยุด (หน้าตัดคลิป) |
| `←` `→` หรือ `A` `D` | เลื่อนกล่องตัดทีละ 1 วินาที (กด `Shift` ค้างไว้: 5 วินาที) |

## มีอะไรใหม่ใน v2.1

- **ตัดคลิป (FastCut)** เครื่องมือใหม่ ตัดวิดีโอหรือเพลงบนไทม์ไลน์ พร้อมพรีวิว ปุ่มลัด และคุณภาพพรีวิว Full / 1/2 / 1/4 / 1/8
- **บีบอัดได้ทุกไฟล์** ไม่ใช่แค่วิดีโอ: รูปภาพ PDF เอกสาร Office เสียง และไฟล์อื่น ๆ
- **FFmpeg ติดตั้งและอัปเดตเอง** ไม่ต้องติดตั้งเองก่อนใช้งานอีกต่อไป
- **คิวงาน** มีปุ่ม "แสดงไฟล์" และปุ่มลบทีละงาน
- แก้ปุ่มเปลี่ยนธีมที่ยืดผิดขนาดตอนเต็มจอ และเพิ่มเครดิตบนหน้าโหลด

ดูรายละเอียดทุกเวอร์ชันได้ที่ [CHANGELOG.md](CHANGELOG.md)

## สำหรับนักพัฒนา

### รันจากซอร์ส

ต้องใช้ Python 3.10 ขึ้นไป

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

ถ้ามีการ์ดจอ NVIDIA และอยากให้ซับไตเติล AI ทำงานบน GPU (เร็วขึ้นราว 2.5 เท่า) ให้ติดตั้งไลบรารี CUDA เพิ่ม ประมาณ 2 GB (ไม่ติดตั้งก็ใช้ได้ แอปจะใช้ CPU แทน)

```bash
pip install -r requirements-gpu.txt
```

### ทดสอบ

```bash
python -m unittest discover -s tests -t . -v
```

### สร้างไฟล์ติดตั้งเอง

```bash
pip install pyinstaller
pyinstaller --noconfirm packaging/ClipHUB.spec
```

Windows จะได้ `dist/ClipHUB.exe` (ไฟล์เดียว) ส่วน macOS จะได้ `dist/ClipHUB.app`

### ปล่อยเวอร์ชันใหม่

สร้าง tag ที่ `main` แล้ว push ขึ้น GitHub ระบบ CI จะทดสอบ build ทั้ง Windows และ macOS แล้วสร้าง Release ให้อัตโนมัติ ชื่อไฟล์มีเลขเวอร์ชันกำกับ (เช่น `ClipHUB-Windows-v2.1.0.exe`) ก่อนปล่อยเวอร์ชันใหม่ให้แก้เลขเวอร์ชันใน `main.py` และลิงก์ปุ่มดาวน์โหลดด้านบน

```bash
git tag -a v2.1.0 -m "ClipHUB v2.1.0"
git push origin v2.1.0
```

### โครงสร้าง

| โฟลเดอร์ | เนื้อหา |
|---|---|
| `core/` | คิวงาน, FFmpeg (ค้นหา ติดตั้ง เรียกใช้), การ์ดจอ |
| `tasks/` | งานของแต่ละเครื่องมือ: ดาวน์โหลด แปลง บีบอัด ตัด ซับไตเติล |
| `ui/` | หน้าต่าง หน้าแต่ละเครื่องมือ ธีม และคำแปลไทย / อังกฤษ |
| `tests/` | เทส (ใช้ `unittest` ของ Python) |
| `packaging/` | สเปก PyInstaller สำหรับ .exe และ .app |

## เอกสาร

- ข้อกำหนดความต้องการซอฟต์แวร์ (SRS): [cliphub-srs.md](cliphub-srs.md)
- บันทึกการเปลี่ยนแปลง: [CHANGELOG.md](CHANGELOG.md)

## ทีมพัฒนา

1. นางสาววรัทยา รอดเมล์ รหัสนักศึกษา 68030258
2. นายวศิน แก้วมรกต รหัสนักศึกษา 68030262
3. นายวสุรัชต์ สมเด็จ รหัสนักศึกษา 68030263
4. นายวัฒนพงศ์ พรหมภิราม รหัสนักศึกษา 68030265

<p align="center"><sub>© 2026 DIEWLAND STUDIO · โครงงานรายวิชา SDD2</sub></p>
