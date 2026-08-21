# Manga Thai Translator — Project Specification

> Specification version: `3.1.0`  
> Updated: `2026-08-11`
> Status: MVP baseline  
> Source languages: `ja`, `en`, `ko`, `zh-Hans`, `zh-Hant`, `auto`  
> Target language: Thai (`th`)

## 1. Product Goal

สร้าง Desktop Application ชื่อ **Manga Thai Translator** สำหรับ Linux/Pop!_OS ใช้แปลข้อความจากภาพมังงะ คอมิก และเว็บตูนภาษาญี่ปุ่น อังกฤษ เกาหลี หรือจีนเป็นภาษาไทย

ผู้ใช้ต้องสามารถ:

1. เปิดภาพมังงะหนึ่งไฟล์หรือทั้งโฟลเดอร์
2. ตรวจจับบริเวณข้อความ
3. เลือกภาษาต้นฉบับระดับ Project, Page หรือ Text Block
4. OCR ข้อความภาษาญี่ปุ่น อังกฤษ เกาหลี จีนตัวย่อ หรือจีนตัวเต็ม
5. ตรวจและแก้ผล OCR
6. แปลข้อความเป็นภาษาไทยด้วย AI
7. แก้บทแปลทีละ Text Block
8. จัด Reading Order ตามรูปแบบมังงะ คอมิก หรือเว็บตูน
9. บันทึกโปรเจกต์และกลับมาแก้ต่อ
10. ดู Preview ข้อความไทยบนภาพ
11. Export บทแปลและภาพ Preview

โปรแกรมต้องมี CPU fallback และต้องไม่บังคับให้มี NVIDIA GPU

MVP ต้องใช้งาน Core Workflow ผ่าน Mock Provider ได้โดยไม่ต้องมี API key,
Network หรือ OCR Model จริง

รหัสภาษาที่ใช้ภายในระบบ:

- `ja` — ญี่ปุ่น
- `en` — อังกฤษ
- `ko` — เกาหลี
- `zh-Hans` — จีนตัวย่อ
- `zh-Hant` — จีนตัวเต็ม
- `auto` — ให้ระบบช่วยเลือกหรือใช้กับเนื้อหาหลายภาษา

`auto` เป็นสถานะการ Resolve ภาษา ไม่ใช่ภาษาที่ OCR/Translation Adapter
จำเป็นต้องรองรับโดยตรง หากตรวจไม่ได้หรือ Confidence ต่ำ ระบบต้องให้ผู้ใช้ยืนยัน

## 2. Technology Stack

- Python 3.12
- PySide6 สำหรับ Desktop GUI
- Pillow และ OpenCV สำหรับจัดการภาพ
- manga-ocr เป็น OCR Provider หลักสำหรับภาษาญี่ปุ่น
- PaddleOCR หรือ OCR Adapter ที่เหมาะสมสำหรับภาษาอังกฤษ เกาหลี จีนตัวย่อ และจีนตัวเต็ม
- OCR Provider Registry/Router สำหรับเลือก Adapter ตามภาษา
- PaddleOCR หรือ Text Detector ที่เหมาะสมสำหรับตรวจตำแหน่งข้อความ
- OpenAI-compatible API สำหรับ Cloud Translation
- Ollama สำหรับ Local Translation
- Pydantic สำหรับ Data Model และ Validation
- pytest สำหรับ Automated Tests
- ruff สำหรับ Lint และ Format
- `pyproject.toml` สำหรับ Dependencies และ Tool Configuration

ข้อกำหนด:

- OCR และ ML Dependencies ต้องติดตั้งแบบ Optional Extra ได้
- Core, UI พื้นฐาน และ Tests ต้องไม่บังคับดาวน์โหลด Model
- หาก Python 3.12 ไม่เข้ากับ Dependency สำคัญ ให้บันทึกปัญหาและเสนอทางเลือกก่อนเปลี่ยน Version
- ห้าม Hardcode `gpt-5.6-sol` จาก Codex development configuration เป็น Translation Model ของแอป
- Translation Model ต้องเป็น User-configurable Provider Setting

## 3. Scope

### 3.1 Project Management

ต้องรองรับ:

- สร้างโปรเจกต์ใหม่
- เปิดโปรเจกต์เดิม
- Import PNG, JPG, JPEG และ WebP
- Import ภาพทั้งโฟลเดอร์
- เรียงชื่อไฟล์แบบ Natural Sort
- บันทึกเป็นโฟลเดอร์โปรเจกต์ที่มี `project.json`
- Auto-save หลังแก้ข้อมูล โดยใช้ Debounce ที่เหมาะสม
- Atomic Write เพื่อป้องกันไฟล์เสียหากโปรแกรมปิดระหว่าง Save
- Project Schema Version เพื่อรองรับ Migration ในอนาคต
- ตั้งค่า Default Source Language เป็น `ja`, `en`, `ko`, `zh-Hans`, `zh-Hant` หรือ `auto`
- ตั้งค่า Default Reading Order แยกจาก Source Language
- Override Source Language และ Reading Order ระดับ Page ได้
- เก็บ Source Path แบบที่รองรับ Path ภาษาไทย
- ห้ามแก้ไขหรือเขียนทับไฟล์ต้นฉบับ

โครงสร้างโปรเจกต์ที่แนะนำ:

```text
example-project/
├── project.json
├── source/
├── previews/
├── exports/
└── cache/
```

จะ Copy ภาพเข้า `source/` หรืออ้างอิงไฟล์เดิมต้องเป็นตัวเลือกที่ชัดเจน โดยค่าเริ่มต้นควร Copy เพื่อให้โปรเจกต์ย้ายเครื่องได้ง่ายและไม่กระทบต้นฉบับ

### 3.2 Image Viewer

ต้องรองรับ:

- แสดงภาพแต่ละหน้า
- Zoom in และ Zoom out
- Fit to window
- Reset zoom
- Pan ด้วยเมาส์
- เลือกหน้าจาก Sidebar
- แสดงหมายเลขหน้าและสถานะของหน้า
- วาด Text Block ด้วยเมาส์
- เลือก ย้าย และปรับขนาดกรอบ
- ลบหรือเพิ่มกรอบเอง
- แสดง Reading Order บนกรอบ
- เลือก Block จากภาพแล้ว Sync กับ Editor
- เลือก Block จาก Editor แล้ว Focus บนภาพ

พิกัด Bounding Box ต้องเก็บตามขนาดพิกเซลจริงของภาพ ไม่ใช่พิกัดหลัง Zoom

### 3.3 Coordinate Transform

ระบบต้องมีฟังก์ชันแยกสำหรับ:

- Image coordinate → Scene coordinate
- Scene coordinate → Image coordinate
- Clamp Bounding Box ไม่ให้ออกนอกภาพ
- Normalize Bounding Box ที่วาดย้อนทิศ

Logic นี้ต้องทดสอบได้โดยไม่เปิด GUI

### 3.4 Text Detection

รองรับสองรูปแบบ:

- **Auto Detect:** ตรวจหาตำแหน่งข้อความทั้งหน้า
- **Manual Select:** ผู้ใช้วาดกรอบเอง

สร้าง Interface ที่เปลี่ยน Provider ได้ เช่น:

```python
class TextDetectionProvider(Protocol):
    async def detect(self, image: ImageInput) -> list[DetectedRegion]:
        ...
```

ข้อกำหนด:

- มี Mock Provider สำหรับ Test
- Auto Detect ต้องไม่ลบ Block ที่ผู้ใช้แก้เองโดยไม่มี Confirmation
- ผลลัพธ์ต้อง Clamp ให้อยู่ในภาพ
- Detection Error ของหน้าเดียวต้องไม่หยุดทั้ง Batch

### 3.5 OCR

ใช้ OCR Provider แบบเปลี่ยน Adapter ได้ โดย `manga-ocr` เป็น Adapter หลักสำหรับภาษาญี่ปุ่น และใช้ PaddleOCR หรือ Adapter ที่เหมาะสมสำหรับภาษาอังกฤษ เกาหลี จีนตัวย่อ และจีนตัวเต็ม

สร้าง Registry/Router เพื่อเลือก Provider จาก Effective Source Language:

1. ค่า `source_language` ของ Text Block หากกำหนด
2. ค่า Override ของ Page หากกำหนด
3. ค่า Default ของ Project
4. หากเป็น `auto` ให้ใช้ Auto-detection Strategy ที่ติดตั้งอยู่ หรือขอให้ผู้ใช้เลือกเมื่อความมั่นใจต่ำ

ห้ามส่ง Text Block ไปยัง Provider ที่ไม่รองรับภาษานั้นอย่างเงียบ ๆ ต้องแจ้ง Error ที่แก้ไขได้
OCR Provider ต้องประกาศ Capability และ Requirement ของตนอย่างชัดเจน

Interface ขั้นต่ำ:

```python
class OcrProvider(Protocol):
    @property
    def supported_languages(self) -> frozenset[str]:
        ...

    async def recognize(
        self,
        image: ImageInput,
        bbox: BoundingBox,
        source_language: str,
    ) -> OcrResult:
        ...
```

Capability Model ขั้นต่ำ:

```python
class OcrCapabilities(BaseModel):
    supported_languages: frozenset[str]
    supports_vertical_text: bool
    supports_cpu: bool
    requires_network: bool
    uploads_images: bool
```

ต้องรองรับ:

- เลือกภาษาเริ่มต้นระดับ Project
- Override ภาษาระดับ Page และ Text Block
- หนึ่ง Page มี Text Block หลายภาษาได้
- แสดง Effective Source Language และ OCR Provider ที่เลือก
- ให้ผู้ใช้แก้ภาษาที่ตรวจผิดและสั่ง OCR ใหม่ได้
- OCR Block ที่เลือก
- OCR ทั้งหน้า
- OCR หลายหน้า
- OCR ใหม่เฉพาะ Block
- Mock OCR Provider
- CPU fallback
- แสดง Error ต่อ Block
- ยกเลิก Batch ได้

ผู้ใช้ต้องแก้ผล OCR ได้ และต้องมีการยืนยันผลก่อนแปลตามค่าเริ่มต้น แต่สามารถเปิดตัวเลือก Auto-translate หลัง OCR ได้ในอนาคต

### 3.6 Data Model

ตัวอย่าง Text Block:

```json
{
  "id": "block-uuid",
  "page_id": "page-uuid",
  "bbox": {
    "x": 100,
    "y": 200,
    "width": 300,
    "height": 180
  },
  "reading_order": 1,
  "source_language": "ja",
  "writing_mode": "vertical",
  "source_text": "",
  "translated_text": "",
  "ocr_confidence": null,
  "ocr_provider": "manga-ocr",
  "speaker": "",
  "note": "",
  "status": "detected",
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-01-01T00:00:00Z"
}
```

สถานะขั้นต่ำ:

- `detected`
- `language_review_required`
- `ocr_complete`
- `ocr_reviewed`
- `translated`
- `translation_reviewed`
- `error`

Model หลักควรมี:

- Project
- ProjectSettings
- Page
- TextBlock
- BoundingBox
- Character
- GlossaryEntry
- TranslationContext
- TranslationInput
- TranslationResult
- ProviderConfiguration

Secret เช่น API key ห้ามเก็บใน `project.json`

กฎการ Resolve ภาษา:

- `ProjectSettings.default_source_language` ต้องมีค่าเสมอ
- `Page.source_language` เป็น Optional Override
- `TextBlock.source_language` เป็น Optional Override
- Business Logic ต้องมีฟังก์ชันเดียวสำหรับหา Effective Source Language
- ถ้าผู้ใช้เปลี่ยนภาษาของ Page ห้ามเขียนทับ Block ที่มี Override อยู่แล้ว
- ผล OCR อาจรายงาน `detected_language` และ Confidence แยกจากภาษาที่ผู้ใช้ยืนยัน
- ค่า `auto` ต้องไม่ถูกส่งเข้า OCR Adapter ที่รับเฉพาะภาษาจริง ต้อง Resolve ก่อนเสมอ
- เมื่อ `auto` Resolve ไม่ได้ ให้ Block อยู่ในสถานะ `language_review_required`
  หรือ Error ที่เทียบเท่า และห้าม OCR/แปลต่อแบบเงียบ ๆ

### 3.7 Reading Order

Reading Order ต้องเป็นการตั้งค่าแยกจากภาษา เพราะงานภาษาเดียวกันอาจมี Layout ต่างกัน

Preset ขั้นต่ำ:

- `manga_rtl` — ขวาไปซ้าย แล้วบนลงล่าง
- `comic_ltr` — ซ้ายไปขวา แล้วบนลงล่าง
- `webtoon_vertical` — บนลงล่าง
- `custom` — ผู้ใช้จัดเอง

ค่าเริ่มต้นของโปรเจกต์ให้ผู้ใช้เลือกตอนสร้างโปรเจกต์ โปรแกรมอาจเสนอ Preset ตามประเภทงานได้ แต่ห้ามบังคับจากภาษาเพียงอย่างเดียว

ต้องรองรับ:

- สร้างลำดับเริ่มต้นแบบ Deterministic
- Override Preset ระดับ Page
- แสดงหมายเลขบนภาพ
- ลากเปลี่ยนลำดับใน UI
- Reorder ด้วยปุ่มหรือ Keyboard
- Normalize Order ให้เป็นเลขต่อเนื่อง
- ไม่เปลี่ยนลำดับระหว่างส่งไปแปลและรับผลกลับ
- Mapping ผลแปลด้วย Block ID ไม่ใช้ Array Index อย่างเดียว

### 3.8 Translation Providers

สร้าง Interface:

```python
class TranslationProvider(Protocol):
    async def translate_blocks(
        self,
        blocks: list[TranslationInput],
        context: TranslationContext,
    ) -> list[TranslationResult]:
        ...
```

รองรับอย่างน้อย:

- OpenAI-compatible API
- Ollama
- Mock Provider

Configuration:

- Base URL
- Model
- API key reference
- Default source language
- Target language
- Temperature
- Timeout
- Retry count
- Translation instructions

ข้อกำหนดด้าน Secret:

- ห้าม Hardcode API key
- ห้ามแสดง API key ใน Log
- ห้ามเก็บ API key ลง Git
- ใช้ Environment Variable หรือ OS Keyring หากทำได้

ข้อกำหนด Cloud Privacy:

- ค่าเริ่มต้นส่งเฉพาะข้อความ OCR, Block ID, Context และ Glossary
- ห้ามอัปโหลดภาพโดยไม่ได้รับอนุญาตจากผู้ใช้
- UI ต้องบอกให้ชัดว่าข้อมูลใดจะถูกส่งไป Provider
- การส่งภาพไป Cloud ต้องเป็น Opt-in ต่อ Provider และค่าเริ่มต้นต้องปิด

### 3.9 Translation Context

แต่ละโปรเจกต์ต้องเก็บ:

- Default Source Language
- Page/Block Language Override
- Reading Order Preset
- Glossary
- รายชื่อตัวละคร
- เพศและอายุโดยประมาณ
- บุคลิกการพูด
- ความสัมพันธ์
- คำเรียกแทนตัว
- คำลงท้าย
- คำเฉพาะที่ห้ามเปลี่ยน
- Summary ของตอนก่อนหน้า
- Translation Note

รองรับการแปล:

- Block เดียว
- ทั้งหน้า
- หลายหน้า
- ทั้งตอน

เมื่อต้องแปลหลาย Block ให้ส่ง Context รวม แต่ผลลัพธ์ต้องกลับมาตาม ID เดิมครบทุก Block

แต่ละ `TranslationInput` ต้องมีภาษาที่ Resolve แล้ว ห้ามส่ง `auto` ไปยัง Translation Provider:

```json
{
  "id": "block-uuid",
  "source_language": "ko",
  "source_text": "다녀왔어?"
}
```

### 3.10 Translation Output Contract

AI ต้องตอบ JSON ตามรูปแบบ:

```json
{
  "translations": [
    {
      "id": "block-uuid",
      "translated_text": "ข้อความภาษาไทย",
      "note": ""
    }
  ]
}
```

ระบบต้องตรวจ:

- Response เป็น JSON ที่ถูกต้อง
- ID ครบทุก Block ที่ร้องขอ
- ไม่มี Missing ID
- ไม่มี Duplicate ID
- ไม่มี Unknown ID
- ไม่มีการรวม Block
- ไม่มีการสลับ Mapping
- `translated_text` เป็น String
- จำนวนผลลัพธ์ตรงกับจำนวน Input
- Block ที่ `source_text` ว่างต้องไม่ถูก AI แต่งข้อความเพิ่ม

หาก Validation ไม่ผ่าน:

1. เก็บข้อความ Error ที่อ่านเข้าใจได้
2. Retry อย่างปลอดภัยไม่เกินค่าที่ตั้งไว้
3. ห้ามเขียนผลลัพธ์บางส่วนลงข้อมูลจริงจนกว่าจะผ่าน Validation
4. หากยังผิด ให้แจ้ง Error โดยโปรแกรมไม่ Crash

### 3.11 Editor UI

Layout หลัก:

- ซ้าย: Page Sidebar
- กลาง: Image Viewer และ Text Block Overlay
- ขวา: Text Block Editor
- ด้านล่าง: Progress และ Log

Text Block Editor ต้องมี:

- Source Language
- Effective OCR Provider
- Writing Mode
- Source Text
- Translated Text
- Speaker
- Note
- Status
- OCR ใหม่
- แปลใหม่
- ยืนยัน OCR
- ยืนยันคำแปล
- Previous Block
- Next Block

ควรมี Keyboard Shortcut สำหรับ:

- Save
- Previous/Next Page
- Previous/Next Block
- Confirm OCR สำหรับหน้าปัจจุบันหรือทุกหน้า
- Confirm Translation
- Delete Block
- Fit image
- Zoom

### 3.12 Background Work

- UI ต้องไม่ค้างระหว่าง Detection, OCR, Translation หรือ Export
- ใช้ Worker Thread, Thread Pool หรือ Async Integration ที่เหมาะกับ PySide6
- ต้องยกเลิก Batch ได้
- รายงาน Progress ตาม Page และ Block
- ห้ามแก้ Qt Widget จาก Worker Thread โดยตรง
- Cancellation ต้องไม่ทำให้ข้อมูลโปรเจกต์เสีย
- Error หนึ่งรายการต้องถูกบันทึกและให้ Batch ทำรายการถัดไปได้

### 3.13 Export

MVP ต้อง Export:

- JSON
- CSV
- TXT
- ภาพ Preview

JSON ต้องรักษา Page, Block ID, Bounding Box, Region Rotation, Text Mirror, Text Alignment, Reading Order,
Source Language, Writing Mode, OCR Provider, Source, Translation, Speaker, Note และ Status

CSV ต้องมีอย่างน้อย:

- Page
- Block
- Reading Order
- Source Language
- OCR Provider
- Speaker
- Source
- Translation
- Note
- Status
- Text Alignment
- Region Rotation
- Text Mirror แนวนอนและแนวตั้ง

TXT ต้องเรียงตาม:

1. Page
2. Reading Order

ภาพ Preview ต้อง:

- ใช้ฟอนต์ไทยที่ผู้ใช้เลือก
- ปรับขนาดให้พอดีกรอบ
- รองรับหลายบรรทัด
- ไม่ตัดคำไทยกลาง Grapheme
- แจ้งเตือนเมื่อข้อความล้น
- ทำเครื่องหมาย Block ที่ล้น
- ห้ามเขียนทับต้นฉบับ
- MVP ใช้พื้นหลังสีขาวหรือสีที่ผู้ใช้เลือกภายในกรอบได้
- หมุนทั้ง Region รอบจุดกึ่งกลางด้วยมุมเดียวสำหรับ Selection, Resize, Cleanup และ Export
- กลับเฉพาะข้อความปลายทางตามแกนภายใน Region ได้ทั้งแนวนอนและแนวตั้ง

### 3.14 Rotated Region and Local IOPaint Cleanup

- `bbox` เก็บกรอบก่อนหมุนในพิกัดภาพจริง และ Region มีมุมเดียวในช่วง `-180..180`
- UI มี Drag Handle แบบ Adobe และช่ององศาที่แก้ค่าเดียวกัน จุดหมุนยึดกึ่งกลางกรอบ
- โปรเจกต์ Schema v1 ต้องย้าย Thai text rotation เดิมเป็น canonical Region rotation และ
  บันทึกเป็น Schema v2 โดยไม่ทำข้อมูลอื่นหาย
- Mirror แนวนอนและแนวตั้งเป็นอิสระ เปิดพร้อมกันได้ และมีผลเฉพาะข้อความปลายทาง
- Text Block เลือกจัดข้อความไทยชิดซ้าย กึ่งกลาง หรือชิดขวาตามแกนภายใน Region ได้
- IOPaint ใช้ CLI โดยตรงหนึ่ง Process ต่อ Cleanup ด้วยค่าเริ่มต้น
  `iopaint run --model=lama --device=cpu --image INPUT --mask MASK --output OUTPUT`
- รองรับ Selected Block และ Current Page; งานระดับหน้าต้องมี Progress/Cancel และ Error
  ของ Block หนึ่งต้องไม่หยุด Block ถัดไป
- หาก CLI, Timeout หรือ Inference ล้มเหลว ต้องแจ้ง Error รักษาภาพเดิม และห้าม
  Silent Fallback ไป Cleanup วิธีอื่น
- Executable, Model, Device และ Operation Timeout แก้ได้ใน Runtime Settings แต่ห้ามบันทึกใน
  `project.json`

## 4. Suggested Architecture

```text
manga-thai-translator/
├── AGENTS.md
├── CONTEXT.md
├── PROJECT_SPEC.md
├── README.md
├── pyproject.toml
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── core/
│   │   ├── models.py
│   │   ├── exceptions.py
│   │   ├── coordinates.py
│   │   ├── reading_order.py
│   │   └── validation.py
│   ├── services/
│   │   ├── project_service.py
│   │   ├── text_detection/
│   │   │   ├── base.py
│   │   │   └── mock.py
│   │   ├── ocr/
│   │   │   ├── base.py
│   │   │   ├── registry.py
│   │   │   ├── router.py
│   │   │   ├── mock.py
│   │   │   ├── manga_ocr.py
│   │   │   └── paddle_ocr.py
│   │   ├── translation/
│   │   │   ├── base.py
│   │   │   ├── mock.py
│   │   │   ├── openai_compatible.py
│   │   │   └── ollama.py
│   │   └── export/
│   │       ├── structured.py
│   │       └── preview.py
│   ├── persistence/
│   │   ├── project_repository.py
│   │   └── secrets.py
│   └── ui/
│       ├── main_window.py
│       ├── image_viewer.py
│       ├── page_sidebar.py
│       ├── block_editor.py
│       └── workers.py
└── tests/
    ├── unit/
    ├── integration/
    └── fixtures/
```

สามารถปรับโครงสร้างได้หากมีเหตุผลทางเทคนิคที่ดีกว่า แต่ต้องรักษาการแยกหน้าที่และ Testability

## 5. Non-functional Requirements

- รองรับ Linux/Pop!_OS
- รองรับ CPU-only
- รองรับชื่อไฟล์และ Path ภาษาไทย
- UI ไม่ค้างระหว่างงานหนัก
- ผู้ใช้ยกเลิก Batch ได้
- Error หน้าเดียวไม่หยุดทั้ง Batch
- Optional Dependency ไม่ทำให้โปรแกรมเปิดไม่ได้
- Public API มี Type Hint
- หลีกเลี่ยง Global Mutable State
- ไม่มี `except Exception: pass`
- ไม่มี Secret ใน Log
- ไม่มีการแก้ภาพต้นฉบับ
- Save ต้องทนต่อการปิดโปรแกรมระหว่างเขียนไฟล์
- Tests ต้องไม่ใช้ Network หรือดาวน์โหลด Model จริง
- Runtime config ของแอปต้องแยกจาก `.codex/` ซึ่งเป็น Development Agent config

## 6. Automated Tests

สร้าง Test อย่างน้อยสำหรับ:

### Core

- Natural Sort ของชื่อหน้า
- Reading Order ขวาไปซ้ายและบนลงล่าง
- Reading Order ซ้ายไปขวาและบนลงล่าง
- Reading Order แบบ Webtoon
- Normalize Reading Order
- Resolve Source Language จาก Project → Page → Text Block
- OCR Router เลือก Provider ถูกต้องสำหรับ `ja`, `en`, `ko`, `zh-Hans` และ `zh-Hant`
- `auto` ไม่หลุดเข้า Provider ที่ไม่รองรับ
- `auto` ที่ Confidence ต่ำเข้าสู่สถานะรอผู้ใช้ยืนยัน
- Provider Capability Validation
- Bounding Box Validation
- Coordinate Transform ระหว่าง Image กับ Viewer
- Clamp Bounding Box
- Thai Text Wrapping
- Overflow Detection

### Persistence

- Project Save และ Load
- Atomic Save
- Unicode Path
- Unicode OCR Text ของญี่ปุ่น อังกฤษ เกาหลี จีนตัวย่อ และจีนตัวเต็ม
- Language Override Round-trip
- Schema Version
- Round-trip แล้วข้อมูลไม่หาย
- ไม่บันทึก API key

### Providers

- Mock OCR Provider
- Mock Translation Provider
- Translation JSON Validation
- Missing ID
- Duplicate ID
- Unknown ID
- Invalid JSON
- Timeout
- Network Error
- Retry Limit
- Cancellation

### Export

- JSON Field ครบ
- CSV Encoding เป็น UTF-8
- TXT เรียงตาม Page และ Reading Order
- Preview ไม่เขียนทับ Source

Automated Tests:

- ห้ามเรียก API จริง
- ห้ามดาวน์โหลด Model จริง
- ต้อง Deterministic

## 7. Acceptance Criteria

MVP ถือว่าเสร็จเมื่อ:

1. เปิดโปรแกรมบน Linux ได้
2. Import ภาพอย่างน้อย 3 หน้าได้
3. หน้าเรียงแบบ Natural Sort ถูกต้อง
4. วาด ย้าย และปรับขนาด Text Block ได้
5. พิกัด Block ถูกต้องเมื่อ Zoom และ Pan
6. เลือก Default Source Language ได้ครบ `ja`, `en`, `ko`, `zh-Hans` และ `zh-Hant`
7. Override ภาษาระดับ Page และ Text Block ได้
8. OCR Router เลือก Mock Provider ตาม Effective Source Language ได้ถูกต้อง
9. OCR Block ที่เลือกผ่าน Mock Provider ได้
10. แก้และยืนยัน Source Text ได้
11. แปลข้อความหลายภาษาปนกันผ่าน Mock Provider ได้โดย ID ไม่สลับ
12. OpenAI-compatible API และ Ollama ตั้งค่าผ่าน Configuration ได้
13. แก้และยืนยันคำแปลได้
14. Reorder Block ได้โดย ID ไม่เปลี่ยน
15. Save แล้วเปิดโปรเจกต์กลับมาได้โดยข้อมูลภาษาไม่หาย
16. Export JSON, CSV, TXT และ Preview ได้
17. Network Error ไม่ทำให้โปรแกรม Crash
18. Batch ยกเลิกได้
19. Automated Tests ผ่าน
20. `ruff check .` ผ่าน
21. README มีขั้นตอน Install, Run, Test และ Troubleshooting บน Ubuntu/Pop!_OS
22. README ระบุ Optional OCR Setup, Language Model และ CPU Limitation ตามจริง
23. ไม่มี API key หรือไฟล์ต้นฉบับถูก Commit
24. `auto` ที่ Resolve ไม่ได้ไม่ถูกส่งเข้า OCR หรือ Translation Provider
25. Codex Agent Model ไม่ถูกใช้เป็น Runtime Translation Default โดยไม่ตั้งใจ

## 8. Development Milestones

### Milestone 0 — Repository Bootstrap

- ตรวจ repository
- ตรวจว่า `AGENTS.md`, `CONTEXT.md` และ `PROJECT_SPEC.md` เป็น version เดียวกัน
- ตรวจว่า `.codex/config.toml` และ `implementation-worker.toml` ถูกโหลดจาก Trusted Project
- สร้าง `pyproject.toml`
- สร้าง Package Layout
- ตั้งค่า pytest และ ruff
- สร้าง README ขั้นต้น
- เพิ่ม `.gitignore`

### Milestone 1 — Core Models and Persistence

- Pydantic Models
- Project Schema Version
- Natural Sort
- Language Model และ Language Override
- Reading Order Preset
- Coordinate Utilities
- Save/Load แบบ Atomic
- Unit Tests

### Milestone 2 — Basic GUI

- Main Window
- Page Sidebar
- Image Viewer
- Zoom, Pan และ Fit
- วาด เลือก ย้าย ปรับขนาด และลบ Block
- Block Editor

### Milestone 3 — Mock Workflow

- Mock Detection
- Mock OCR
- Mock Translation
- Worker/Progress/Cancellation
- End-to-end Workflow โดยไม่ใช้ Network

### Milestone 4 — Real Translation Providers

- OpenAI-compatible Provider
- Ollama Provider
- Configuration UI
- Secret Handling
- JSON Validation และ Retry

### Milestone 5 — OCR Integration

- manga-ocr Adapter
- PaddleOCR หรือ Adapter สำหรับ `en`, `ko`, `zh-Hans` และ `zh-Hant`
- OCR Provider Registry/Router
- Project/Page/Block Language Selection
- Mixed-language Page
- Optional Dependency
- CPU Fallback
- OCR Block และ Batch
- Error Handling
- Capability Declaration และ Language Review State

### Milestone 6 — Export and Hardening

- JSON, CSV, TXT
- Preview Rendering
- Thai Text Wrapping
- Overflow Warning
- Integration Tests
- README และ Troubleshooting

อย่าเริ่ม Phase 2 จนกว่า MVP Acceptance Criteria จะผ่าน

## 9. Phase 2

เสนอและประเมินแยกจาก MVP:

- Speech-bubble detection ที่แม่นยำขึ้น
- Inpainting ขั้นสูงหรือ Batch ทั้งโปรเจกต์นอกเหนือ IOPaint ระดับ Block/Page
- จัดคำไทยลงบอลลูนอัตโนมัติขั้นสูง
- PDF และ CBZ
- Translation Memory
- Character voice consistency
- ตรวจจับภาษาอัตโนมัติระดับ Block ที่แม่นยำขึ้น
- รองรับภาษาอื่นนอกเหนือจาก MVP
- SFX detection
- Export ภาพพร้อมเผยแพร่
- AppImage หรือ Flatpak

## 10. Explicitly Out of Scope

ห้ามเพิ่ม:

- Manga Downloader
- Website Scraper
- DRM Bypass
- ระบบค้นหาแหล่งมังงะ
- การอัปโหลดภาพไป Cloud โดยไม่ขออนุญาต
- การเผยแพร่ไฟล์มังงะอัตโนมัติ

## 11. First Codex Task

เมื่อเริ่ม repository ให้ Codex:

1. อ่าน `AGENTS.md`, `CONTEXT.md` และ `PROJECT_SPEC.md`
2. ตรวจโครงสร้างและ Git status
3. สรุป Architecture และ Dependency Risk
4. เสนอแผน Milestone 0–1 แบบสั้น
5. Implement Milestone 0–1
6. รัน Tests และ Ruff
7. รายงานไฟล์ที่เปลี่ยน ผลทดสอบ และข้อจำกัด

Lead Agent ต้องมอบหมายงาน Implement ที่แบ่งขอบเขตได้ให้
`implementation_worker` และตรวจ Diff/Tests ด้วยตัวเองก่อนสรุป
