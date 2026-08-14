# Manga Thai Translator

Desktop application สำหรับช่วยทำ OCR แปล และตรวจแก้ข้อความจากภาพมังงะ คอมิก
และเว็บตูนเป็นภาษาไทยบน Linux/Pop!_OS เอกสารและขอบเขตปัจจุบันอ้างอิง
project specification version `3.1.0`

## สถานะ

Repository มีขอบเขต Milestone 0–6 และ direct image-file/folder workflow แล้ว:

- Core model, language override, reading order, coordinate utilities และ atomic save/load
- GUI สำหรับสร้าง/เปิดโปรเจกต์ Import ภาพ ดูภาพ วาดและแก้ Text Block
- Mock detection/OCR/translation พร้อม background progress และ cancellation
- OpenAI-compatible และ Ollama translation providers ที่ผู้ใช้ตั้งค่าเอง
- Codex CLI translation provider ที่ใช้บัญชี Codex ซึ่งล็อกอินอยู่และคืนผลแบบ JSON
- เปิดไฟล์ภาพหรือโฟลเดอร์ภาพโดยตรงและให้ Codex อ่านตำแหน่ง/OCR/แปลภาพแบบผู้ใช้อนุญาต
- OCR router และ optional local adapters สำหรับ manga-ocr/PaddleOCR
- Preview ต้นฉบับและภาษาไทยแบบคู่ในแอป เลือกฟอนต์ ล้างพื้นหลังเฉพาะกรอบ และ Export
  JSON, CSV, TXT, PNG พร้อมตรวจข้อความล้น

## Workspace ที่สรุปแล้ว

- Layout หลักเป็น Navigator ด้านซ้าย, Viewer ตรงกลาง และ Inspector ด้านขวา โดยค่าเริ่มต้น
  ใช้พื้นที่อย่างน้อย `1366x768` ได้โดยไม่ต้องเลื่อนหน้าต่างหลัก
- Navigator มีแท็บ **Pages** และ **Blocks**; แท็บ Blocks แสดงเฉพาะบล็อกของหน้าปัจจุบันพร้อมภาพย่อ
  ลำดับการอ่าน สถานะที่มีทั้งข้อความและไอคอน และข้อความ OCR ไม่เกินสองบรรทัด
- Inspector แบ่งเป็นแท็บ **Text**, **Layout** และ **Details** และจำแท็บล่าสุดไว้โดยไม่
  เปลี่ยนแท็บอัตโนมัติเมื่อเลือกบล็อกใหม่
- Viewer มีโหมด **Split**, **Original** และ **Thai Preview** ด้วย `Alt+0`, `Alt+1`, `Alt+2`;
  โหมดที่ซ่อน pane จะไม่ unload ภาพและกลับมาใช้ขนาด Split เดิม
- Activity ถูกพับไว้เป็นค่าเริ่มต้น แสดง progress แบบบางใน status bar และเปิดเองเมื่อเกิดข้อผิดพลาด
  การตั้งค่า UI เช่นขนาด splitter แท็บ Navigator/Inspector และการแสดง Activity เก็บด้วย `QSettings`
  เท่านั้น ไม่ปะปนกับ `project.json`

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

1. ใช้ **File > Open Images…** (`Ctrl+O`) แล้วเลือก PNG/JPG/JPEG/WebP หนึ่งไฟล์หรือ
   หลายไฟล์จากโฟลเดอร์เดียวกันได้ทันที หรือใช้ **Open Image Folder…** เพื่อเปิดทั้งโฟลเดอร์
   แอปสร้าง workspace ภายใน `.manga-thai-translator` ข้างไฟล์ภาพและดูแลสถานะให้เอง
   ผู้ใช้ไม่ต้องสร้างหรือเลือก `project.json`; เมนู **New/Open Project** เดิมยังใช้ได้กับ
   workflow ขั้นสูง
2. เลือกภาษาต้นฉบับตามลำดับ Project default → Page override → Block override
   ค่า `auto` ที่ resolve ไม่ได้ต้องเปลี่ยนเป็นภาษาจริงก่อน OCR หรือแปล
3. เลือก OCR mode เป็น **Mock (offline)** สำหรับการทดสอบ OCR แบบ deterministic
   หรือ **Installed local** สำหรับ OCR ที่ติดตั้งไว้
4. กด **Workflow > Run Workflow** (`Ctrl+R`) เพื่อทำ Text Detection และ OCR ตาม OCR mode
   ปัจจุบัน ผลสำเร็จจะรอให้ผู้ใช้ตรวจและกด **Confirm OCR** ก่อนแปล หากเลือก Codex CLI
   และอนุญาตส่งภาพอย่างชัดเจน คำสั่งเดียวกันจะใช้ workflow แปลภาพทั้งโฟลเดอร์แทน
5. สั่ง OCR เฉพาะ Block, หน้าปัจจุบัน หรือทุกหน้า แก้ Source Text แล้วกด
   **Confirm OCR for Current Page** หรือ **Confirm OCR for All Pages** ก่อนใช้คำสั่งแปลจริง
6. ตั้งค่าที่ **Settings > Configure Translation Provider…** แล้วเลือก
   Codex CLI, OpenAI-compatible หรือ Ollama สำหรับ Codex CLI แอปใช้การล็อกอิน
   ของคำสั่ง `codex` ที่มีอยู่และค่า Model `default`; ส่วน Provider อื่นใช้ Base URL และ Model
   สำหรับ
   OpenAI-compatible ให้กรอกเฉพาะชื่อ environment variable เช่น
   `MANGA_TRANSLATION_API_KEY` ไม่ใช่ค่าของ secret
7. สั่งแปลเฉพาะ Block, หน้าปัจจุบัน หรือทุกหน้า ตรวจแก้คำแปล และกด **Confirm Translation**
8. ใช้ **Navigate > Move Block Earlier/Move Block Later** เพื่อจัดลำดับ Block โดย ID เดิม
9. กด **File > Save** เพื่อบันทึกการแก้ไข; สำหรับ folder workflow แอปบันทึกแต่ละหน้าที่
   แปลสำเร็จแบบ atomic ให้อัตโนมัติด้วย

หน้ากลางแสดง **Original** และ **Thai Preview** คู่กัน Original ยังใช้เลือก ย้าย และ
ปรับขนาดกรอบได้ แก้ข้อความในช่องคำแปลไทย แล้วกด **View > Refresh Thai Preview**
เพื่ออัปเดตด้าน Preview โดยไม่สลับหรือแก้พิกเซลต้นฉบับ

การเลื่อนล้อเมาส์ปกติจะเลื่อนภาพแนวตั้ง, `Shift+Wheel` เลื่อนแนวนอน และ `Ctrl+Wheel` ซูมรอบ
ตำแหน่ง pointer โดย Original กับ Thai Preview ซิงก์กัน การซูมแบบกำหนดเองอยู่ในช่วง `10%–800%`
ยกเว้น Fit-to-window ของภาพแนวยาวที่ลดต่ำกว่า `10%` ได้ การ Zoom, Pan และเลื่อน Scrollbar ที่ Original หรือ Thai Preview จะซิงก์อีกฝั่งตามกัน
เพื่อเทียบตำแหน่งเดียวกัน ใน Text Block Editor ใช้ **Thai font size** เลือก `Auto` หรือ
กำหนดขนาดพิกเซล ใช้ **Region rotation** กำหนดมุมเดียวของทั้งกรอบ โดยค่าบวกหมุนตาม
เข็มนาฬิกา หรือลากจุดจับเหนือกรอบแบบ Adobe รอบจุดกึ่งกลาง (`Shift` ช่วย Snap ทีละ 15°)
Selection, Resize, OCR/Cleanup mask, Thai Preview และ Export ใช้มุมเดียวกันทั้งหมด
Checkbox **Horizontal** และ **Vertical** กลับเฉพาะข้อความไทยตามแกนภายในกรอบก่อนหมุน
และเปิดพร้อมกันได้ ใช้ **Alignment** เลือกชิดซ้าย กึ่งกลาง หรือชิดขวาภายในกรอบ
จากนั้นใช้ **Line spacing** เลือก `Auto` หรือกำหนดระยะห่าง
เพิ่มเติมเป็นพิกเซล โดย **Outline width** เริ่มต้นที่ `0 px` แล้วกด **View > Refresh Thai Preview**
ช่อง **Effective size**,
**Effective text color** และ **Effective outline color** แสดงค่าที่ renderer ใช้จริงหลัง Preview ล่าสุด

โปรเจกต์ Schema v1 ที่มี `typesetting_rotation_degrees` จะถูกย้ายเป็น Region rotation
อัตโนมัติเมื่อเปิด และบันทึกครั้งถัดไปเป็น Schema v2 โดยไม่รีเซ็ตมุมเดิม

แท็บ **Blocks** ใน Navigator แสดงเฉพาะ Block ที่มีข้อความ OCR แล้ว คลิกแถวเพื่อเลือกและเลื่อนไปยัง
กรอบนั้นในภาพ Original หรือกด **Delete** บนแถวเพื่อลบ Block ที่ไม่ต้องการ

ใช้ **Workflow > Undo Last Workflow** (`Ctrl+Z`) เพื่อย้อนผล workflow ที่นำมาใช้ล่าสุด
ได้ตามลำดับ คำสั่งนี้ย้อนเฉพาะผลระดับ workflow ไม่มี redo และไม่รวมการแก้ข้อความด้วยมือ;
เมื่อกำลังแก้ข้อความ `Ctrl+Z` ยังคงเป็น native text undo

การตั้งค่า translation provider อยู่ในหน่วยความจำของ runtime เท่านั้นและหายเมื่อ
ปิดแอป ไม่บันทึกลง `project.json` ส่วน API key อ่านจาก environment variable
ตามชื่อที่ผู้ใช้ระบุ เช่นตั้งค่าก่อนเปิดแอปด้วย

```bash
export MANGA_TRANSLATION_API_KEY="<your-api-key>"
python -m app.main
```

หากใช้ Codex CLI ให้ติดตั้ง/ล็อกอิน Codex ก่อน และตรวจสถานะด้วย:

```bash
codex login status
```

แอปเรียก `codex exec` แบบ ephemeral, read-only และ structured output โหมดแปลข้อความ
ส่งเฉพาะ OCR text, Block ID และบริบท ส่วนโหมด **Translate Folder Images** จะส่งภาพ
เฉพาะเมื่อผู้ใช้ติ๊กอนุญาตใน UI อย่างชัดเจน ภาพเว็บตูนแนวยาวถูกแบ่งเป็น PNG tiles
ชั่วคราวและส่งทีละ tile เพื่อรักษาความชัดของข้อความและพิกัด แล้วลบทิ้งหลัง Codex
ตอบกลับ แอปไม่บันทึกข้อมูลล็อกอินลงโปรเจกต์

## Export

เลือกฟอนต์ที่ **Settings > Choose Thai Font…** แล้วเลือก **File > Export…**
และโฟลเดอร์ปลายทาง แอปสร้าง:

- `project-export.json`
- `project-export.csv`
- `project-export.txt`
- `1.png`, `2.png`, `3.png`, ... เรียงตามลำดับหน้าในโปรเจกต์

ระหว่าง Export เลือก Watermark ได้เป็น **None**, **Text** หรือ **Logo image**. โหมดข้อความ
ตั้งต้นเป็น `แปลหลังเลิกงาน` และแก้ข้อความได้; ข้อความหรือโลโก้จะวางมุมขวาล่างทุกหน้ามังงะ
โดย Logo รักษาสัดส่วน/ความโปร่งใส ย่อไม่เกิน 20% ของขนาดหน้า และไม่ขยาย Logo ขนาดเล็ก
นอกจากนี้เลือก Banner image หนึ่งภาพเป็น **First page** หรือ **Last page** ได้ Banner จะคง
ขนาดเดิมและถูกนับรวมในชื่อ `1.png`, `2.png`, ... ตัวเลือกเหล่านี้ใช้เฉพาะ Export ครั้งนั้น
และไม่บันทึกลง `project.json`

Preview ใช้ OpenCV ที่ติดตั้งอยู่ลบ glyph ตามความต่างจากสีขอบกรอบเฉพาะพื้นเรียบหรือ
ไล่ระดับเรียบลื่น แล้วจัดข้อความไทยในรอบที่สองเพื่อไม่ให้ cleanup ทำให้ข้อความเบลอ
กรอบที่มี artwork ซับซ้อนจะคงพิกเซลต้นฉบับทั้งหมดไว้ ไม่วางข้อความไทยทับ และรายงาน
recoverable issue เพื่อให้ผู้ใช้ทำความสะอาดแบบ manual/context-aware ข้อความใช้ alignment
ของแต่ละ Block และมีระยะขอบ ปรับขนาดฟอนต์ให้พอดี หากข้อความไม่พอดีที่ขนาดฟอนต์ขั้นต่ำ แอปทำกรอบแดง
และรายงาน overflow warning ไฟล์ต้นฉบับจะไม่ถูกเขียนทับ

งานที่ทำความสะอาดเองใส่ PNG ที่
`<project_dir>/cleanups/page-<source-name>-<page UUID>/block-<reading-order>-<block UUID>.png`
โดยชื่อจะบอกแหล่งภาพและลำดับ Block ให้อ่านออก ภาพต้องเปิดเป็น RGB ได้และมีขนาดพิกเซลตรงกับ
พื้นที่ Context 16 พิกเซลรอบกรอบทุกด้านหลัง Clamp กับขอบหน้าทุกประการ แอปจะใช้ภาพนี้แทน
cleanup อัตโนมัติ แล้ววางข้อความไทยเฉพาะในกรอบเดิม ไฟล์รูปแบบเก่า
`cleanups/page-<page UUID>/block-<block UUID>.png` ยังถูกอ่านได้เพื่อไม่ให้ cleanup เดิมหาย

ไม่ต้องคำนวณขนาด Crop เอง: เลือก Block แล้วใช้ **View > Prepare Manual Cleanup for Selected
Block** หรือใช้ **Prepare Manual Cleanups for Current Page** เพื่อสร้างไฟล์ที่ขาด
และเปิดไฟล์/โฟลเดอร์ด้วยโปรแกรมของระบบ ลบเฉพาะข้อความต้นฉบับโดยห้าม Crop หรือ Resize
ภาพ บันทึก PNG เดิม แล้วใช้ **View > Refresh Thai Preview** เพื่อดูผล หากต้องการยกเลิก
manual override ให้ใช้คำสั่ง **Re-clean** ซึ่งจะเก็บไฟล์เดิมเป็น `.bak` ก่อนเสมอ

IOPaint เป็น Local Cleanup เริ่มต้น ติดตั้ง `iopaint` ให้เรียกได้จาก Environment ของแอป
หรือไว้ที่ `~/Apps/iopaint/venv/bin/iopaint` แล้วใช้ **Settings > Configure IOPaint…**
แอปจะเลือก executable จาก `PATH` ก่อนแล้วตรวจตำแหน่งดังกล่าวอัตโนมัติ ค่าเริ่มต้นอื่นคือ
model `lama`, device `cpu` และ timeout 300 วินาที จากนั้นใช้ **IOPaint Clean Selected Block** หรือ
**IOPaint Clean Current Page**. แต่ละ Cleanup เรียก CLI โดยตรงหนึ่งครั้งในรูปแบบ
`iopaint run --model=lama --device=cpu --image INPUT --mask MASK --output OUTPUT`
โดยไม่เปิด Server หรือ Port งานระดับหน้าทำทีละ Block พร้อม Progress/Cancel; Block ที่
ล้มเหลวไม่หยุด Block ถัดไปและไม่เขียนทับผลเดิม หาก IOPaint ล้มเหลว แอปจะแจ้ง Error
โดยไม่ Fallback เงียบ ๆ ผู้ใช้ยังเลือก Manual Cleanup เดิมได้เอง

ค่า IOPaint เป็น Runtime-only และไม่บันทึกลง `project.json`. แอปไม่ติดตั้ง IOPaint ให้;
หาก executable, model หรือ runtime ไม่พร้อม คำสั่ง Cleanup จะแจ้งข้อผิดพลาดให้แก้ก่อนลองใหม่

ตอนวางข้อความไทย แอปวัดสีตัวอักษร สีขอบ/เงา และความสูงของ glyph จากข้อความต้นฉบับ
ใน Block แล้วใช้ค่าเหล่านั้นกับฟอนต์ไทยที่เลือก จึงเปลี่ยนเฉพาะภาษาและฟอนต์เป็นหลัก
ถ้าภาพต้นฉบับมีลวดลายซับซ้อนมากจนวัด style ผิด ให้ใช้ manual cleanup และตรวจ Preview
ก่อน Export

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

- Local text detection ยังเป็น mock; งานจริงใช้การวาดกรอบเองหรือ Codex image แบบ opt-in
- Real OCR เป็น optional dependency และไม่ได้ติดตั้งในการติดตั้งพื้นฐาน
- Translation ไม่มี provider เริ่มต้น ผู้ใช้ต้องตั้งค่า OpenAI-compatible หรือ
  Ollama หรือเลือก Codex CLI ที่ล็อกอินไว้
- Codex CLI เป็นเครื่องมือเสริมภายนอกและต้องติดตั้ง/ล็อกอินแยก แอปไม่คัดลอก
  Token หรือการตั้งค่าจาก `.codex/` ลง `project.json`
- OpenAI-compatible อาจส่ง OCR text, Block ID, context, glossary และ instructions
  ผ่านเครือข่ายไปยัง server ที่ตั้งค่าไว้ โปรดตรวจนโยบายความเป็นส่วนตัวของ provider;
  OpenAI-compatible และ Ollama adapters ปัจจุบันไม่ส่งภาพ
- Provider settings เป็น runtime-only จึงต้องตั้งใหม่หลังเปิดแอป
- IOPaint settings เป็น runtime-only และ CPU inference อาจช้า; ต้องติดตั้ง IOPaint แยกก่อน
- ตำแหน่งกรอบจาก Codex image เป็นผลวิเคราะห์ของโมเดล ผู้ใช้ควรตรวจและปรับกรอบใน
  Original viewer ก่อน Export โดยเฉพาะบอลลูนซ้อนหรือข้อความที่ไม่มีกรอบ
- ห้าม commit API key, secret, `project.json` ที่มีข้อมูลส่วนตัว หรือภาพต้นฉบับของผู้ใช้
- การแยกพื้นเรียบและ mask glyph เป็น heuristic ไม่ใช่ semantic inpainting; หากไม่มี OpenCV
  หรือหา mask ไม่ได้ ระบบจะเติมสี median จากขอบเฉพาะกรอบที่พื้นเรียบ ส่วนกรอบที่มี
  artwork ซับซ้อนจะไม่ถูกแก้พิกเซลหรือวางข้อความ และจะถูกรายงานให้ทำความสะอาดเอง
- ยังไม่มี production speech-bubble detection, PDF/CBZ หรือ export ภาพพร้อมเผยแพร่
