# Manga Thai Translator

Desktop application สำหรับช่วยตรวจ OCR แปล และตรวจแก้ข้อความจากภาพมังงะ คอมิก
และเว็บตูนเป็นภาษาไทยบน Linux/Pop!_OS

## สถานะ

- Milestone 0 และ Milestone 1 พัฒนาและตรวจสอบแล้ว
- ขอบเขตที่ส่งมอบ: Pydantic domain models และ schema version 1, language overrides,
  natural sort, coordinate utilities, deterministic reading order, atomic save/load และ unit tests

นี่เป็นเพียง Core foundation ยังไม่ใช่แอปหรือ MVP ที่เสร็จสมบูรณ์

## ติดตั้งสำหรับพัฒนา

ต้องใช้ Python 3.12 ขึ้นไป:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

## ตรวจสอบโค้ด

```bash
pytest
ruff check .
ruff format --check .
```

จัดรูปแบบโค้ดด้วย:

```bash
ruff format .
```

## ข้อจำกัดบน Ubuntu/Pop!_OS

- ต้องติดตั้ง Python 3.12 และเครื่องมือสร้าง virtual environment ให้พร้อม
- ยังไม่มี GUI, app entry point หรือ dependency ของระบบสำหรับ PySide6
- ยังไม่มี text detection, OCR/ML, translation providers, background jobs หรือ export
- ฟีเจอร์เหล่านี้เป็นงานของ Milestone ถัดไป และยังประมวลผลภาพจริงไม่ได้
- ห้ามนำ API key หรือภาพต้นฉบับของผู้ใช้เข้า repository
