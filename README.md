# Manga Thai Translator

Desktop application สำหรับช่วยทำ OCR แปล และตรวจแก้ข้อความจากภาพมังงะ คอมิก
และเว็บตูนเป็นภาษาไทยบน Linux/Pop!_OS เอกสารและขอบเขตปัจจุบันอ้างอิง
project specification version `3.1.0`

## สถานะ

Repository มีขอบเขต Milestone 0–6 แล้ว:

- Core model, language override, reading order, coordinate utilities และ atomic save/load
- GUI สำหรับสร้าง/เปิดโปรเจกต์ Import ภาพ ดูภาพ วาดและแก้ Text Block
- Mock detection/OCR/translation พร้อม background progress และ cancellation
- OpenAI-compatible และ Ollama translation providers ที่ผู้ใช้ตั้งค่าเอง
- OCR router และ optional local adapters สำหรับ manga-ocr/PaddleOCR
- Export JSON, CSV, TXT และภาพ Preview PNG พร้อมตรวจข้อความล้น

## ติดตั้ง

ต้องใช้ Python 3.12 ขึ้นไป การติดตั้งแบบพื้นฐานสำหรับเปิดแอป:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
```

ติดตั้งเครื่องมือพัฒนา:

```bash
python -m pip install -e ".[dev]"
```

ติดตั้งเครื่องมือพัฒนาและ OCR ภายในเครื่อง:

```bash
python -m pip install -e ".[dev,ocr]"
```

ชุด `ocr` มี manga-ocr, PaddleOCR และ PaddlePaddle ซึ่งมีขนาดใหญ่และอาจ
ดาวน์โหลดโมเดลแบบ lazy เมื่อใช้งานครั้งแรก การติดตั้งพื้นฐานไม่รวมโมเดลเหล่านี้
และ automated tests ไม่เรียก API, ดาวน์โหลดโมเดล หรือใช้โมเดลจริง

## เปิดและใช้งาน

```bash
python -m app.main
```

Workflow หลัก:

1. ใช้ **File > New Project…** เพื่อเลือกภาพเริ่มต้นและโฟลเดอร์โปรเจกต์,
   **Open Project…** เพื่อเปิดโฟลเดอร์ที่มี `project.json` หรือ
   **Import Images…** เพื่อเพิ่ม PNG/JPG/JPEG/WebP
2. เลือกภาษาต้นฉบับตามลำดับ Project default → Page override → Block override
   ค่า `auto` ที่ resolve ไม่ได้ต้องเปลี่ยนเป็นภาษาจริงก่อน OCR หรือแปล
3. วาด/แก้ Text Block เอง หรือใช้ **Run Mock Workflow** สำหรับ workflow
   deterministic แบบ offline จากนั้นเลือก OCR mode เป็น **Mock (offline)**
   หรือ **Installed local**
4. สั่ง OCR เฉพาะ Block, หน้าปัจจุบัน หรือทุกหน้า แก้ Source Text แล้วกด
   **Confirm OCR** ก่อนใช้คำสั่งแปลจริง
5. ตั้งค่าที่ **Settings > Configure Translation Provider…** แล้วเลือก
   OpenAI-compatible หรือ Ollama พร้อม Base URL และ Model สำหรับ
   OpenAI-compatible ให้กรอกเฉพาะชื่อ environment variable เช่น
   `MANGA_TRANSLATION_API_KEY` ไม่ใช่ค่าของ secret
6. สั่งแปลเฉพาะ Block, หน้าปัจจุบัน หรือทุกหน้า ตรวจแก้คำแปล และกด
   **Confirm translation**
7. ใช้ **Navigate > Move Block Earlier/Later** เพื่อจัดลำดับ Block โดย ID เดิม
8. กด **File > Save** เพื่อบันทึกการแก้ไขลง `project.json` อย่างชัดเจน

`Run Mock Workflow` เป็นทางลัดทดสอบ end-to-end แบบ offline ส่วนคำสั่งแปล
ผ่าน provider ที่ตั้งค่าไว้จะรับเฉพาะ Block ที่ยืนยัน OCR แล้ว

การตั้งค่า translation provider อยู่ในหน่วยความจำของ runtime เท่านั้นและหายเมื่อ
ปิดแอป ไม่บันทึกลง `project.json` ส่วน API key อ่านจาก environment variable
ตามชื่อที่ผู้ใช้ระบุ เช่นตั้งค่าก่อนเปิดแอปด้วย

```bash
export MANGA_TRANSLATION_API_KEY="<your-api-key>"
python -m app.main
```

## Export

เลือก **File > Export…** แล้วเลือกโฟลเดอร์ปลายทางและไฟล์ฟอนต์ไทยแบบ
TrueType/OpenType แอปสร้าง:

- `project-export.json`
- `project-export.csv`
- `project-export.txt`
- `preview-page-....png` หนึ่งไฟล์ต่อหน้า

Preview จะวางข้อความไทยบนพื้นหลังสีภายใน Bounding Box เท่านั้น ไม่ทำ
inpainting ลบข้อความเดิมหรือ typesetting พร้อมเผยแพร่ หากข้อความไม่พอดีที่ขนาด
ฟอนต์ขั้นต่ำ แอปทำกรอบแดงและรายงาน overflow warning ไฟล์ภาพต้นฉบับจะไม่ถูก
เขียนทับ

## ตรวจสอบโค้ด

```bash
pytest
ruff check .
ruff format --check .
```

จัดรูปแบบโค้ดด้วย `ruff format .`

## Ubuntu/Pop!_OS

- ต้องมี Python 3.12, เครื่องมือสร้าง virtual environment และ graphical display
- หาก Qt แจ้งว่าโหลด xcb platform plugin ไม่ได้ ให้ติดตั้ง `libxcb-cursor0`
- สภาพแวดล้อม headless ใช้ `QT_QPA_PLATFORM=offscreen pytest`
- Export ต้องใช้ไฟล์ `.ttf`, `.otf` หรือ `.ttc` ที่มี glyph ภาษาไทย
- OCR จริงทำงานบน CPU ได้แต่อาจช้าและใช้หน่วยความจำมาก โดยเฉพาะครั้งแรก

## ข้อจำกัดและความเป็นส่วนตัว

- Production text detection ยังเป็น mock; ใช้การวาดกรอบเองสำหรับงานจริง
- Real OCR เป็น optional dependency และไม่ได้ติดตั้งในการติดตั้งพื้นฐาน
- Translation ไม่มี provider เริ่มต้น ผู้ใช้ต้องตั้งค่า OpenAI-compatible หรือ
  Ollama และเลือก Model เอง
- OpenAI-compatible อาจส่ง OCR text, Block ID, context, glossary และ instructions
  ผ่านเครือข่ายไปยัง server ที่ตั้งค่าไว้ โปรดตรวจนโยบายความเป็นส่วนตัวของ provider;
  adapters ปัจจุบันไม่ส่งภาพ
- Provider settings เป็น runtime-only จึงต้องตั้งใหม่หลังเปิดแอป
- ห้าม commit API key, secret, `project.json` ที่มีข้อมูลส่วนตัว หรือภาพต้นฉบับของผู้ใช้
- ยังไม่มี inpainting, production speech-bubble detection, PDF/CBZ หรือ
  export ภาพพร้อมเผยแพร่
