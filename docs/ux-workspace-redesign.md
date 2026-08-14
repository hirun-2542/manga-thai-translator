# Workspace Redesign

เอกสารนี้บันทึกพฤติกรรมของ workspace ที่สรุปแล้วสำหรับ MVP ของ Manga Thai Translator
โดยเน้นงานตรวจ OCR แก้คำแปล และตรวจ Preview ในหน้าจอเดียว

## โครงสร้าง Workspace

หน้าต่างหลักแบ่งเป็นสามส่วน:

- **Navigator** ด้านซ้าย ค่าเริ่มต้นประมาณ `240 px`
  - แท็บ **Pages** แสดงหน้าทั้งโปรเจกต์และจำนวนบล็อก
  - แท็บ **Blocks** แสดงเฉพาะบล็อกของหน้าปัจจุบันเป็นแถวแนวตั้ง มีภาพย่อ ลำดับการอ่าน
    สถานะที่แสดงทั้งข้อความและไอคอน และข้อความ OCR ไม่เกินสองบรรทัด
- **Viewer** ตรงกลาง เป็นพื้นที่ยืดตามขนาดหน้าต่าง มีภาพ **Original** และ **Thai Preview**
  พร้อม divider ที่ลากได้
- **Inspector** ด้านขวา ค่าเริ่มต้นประมาณ `360 px` แบ่งเป็นแท็บ **Text**, **Layout** และ
  **Details**

การเลือกบล็อกจาก Navigator, กรอบบนภาพ หรือ Inspector ใช้ Block ID เดียวกันเสมอ การลบบล็อก
จึงอัปเดตทั้งสามส่วนพร้อมกัน การเลือกบล็อกใหม่ไม่เปลี่ยนแท็บ Inspector อัตโนมัติ; หากมีปัญหา
จะแสดง badge หรือไอคอนเตือนที่แท็บที่เกี่ยวข้อง

## Wheel และ Zoom

- ล้อเมาส์ปกติเลื่อนภาพในแนวตั้งของ Viewer ที่ pointer อยู่ และไม่ซูม/ไม่เปลี่ยนหน้า
- `Shift+Wheel` เลื่อนภาพในแนวนอน
- `Ctrl+Wheel` ซูมรอบตำแหน่ง pointer และซิงก์ Original กับ Thai Preview
- Zoom แบบกำหนดเองจำกัดที่ `10%` ถึง `800%`
- Fit-to-window เป็นข้อยกเว้น: ภาพแนวยาวอาจแสดงต่ำกว่า `10%` เพื่อให้เห็นทั้งหน้า
- ปุ่ม/คำสั่ง `+`, `-`, Fit และรีเซ็ตเป็น `100%` ยังคงใช้ได้ โดยคำสั่งจาก toolbar หรือ
  keyboard ยึดจุดกึ่งกลาง viewport
- Middle-button pan ยังคงใช้ได้

การซ่อน pane, ลบ Block, อัปเดต Preview หรือเปลี่ยนโหมดมุมมองต้องคงหน้า Block ที่เลือก,
zoom และตำแหน่ง scroll ไว้ เมื่อ pane กลับมาใน Split View จะใช้ขนาด divider เดิม

## View Modes และ Shortcuts

มีปุ่มควบคุมที่เห็นได้ชัดสามสถานะ:

| โหมด | Shortcut | พฤติกรรม |
| --- | --- | --- |
| Split | `Alt+0` | แสดง Original และ Thai Preview พร้อมกัน |
| Original | `Alt+1` | ซ่อน Thai Preview ชั่วคราว |
| Thai Preview | `Alt+2` | ซ่อน Original ชั่วคราว |

Focus mode ในเอกสารนี้หมายถึงโหมด **Original** หรือ **Thai Preview** ที่ซ่อน pane อีกฝั่ง
โดยไม่ unload ภาพหรือรีเซ็ต state การกลับไป **Split** จะกู้ divider และ view state เดิม
ทุก session ใหม่เริ่มที่ **Split** เสมอ และไม่บันทึก Focus mode ลง settings

## Inspector

แท็บ **Text** รวม Source Language, Writing Mode, ข้อความ OCR, คำแปลไทย, OCR, Translate
และปุ่ม **Confirm OCR**/**Confirm Translation**

แท็บ **Layout** รวมฟอนต์ไทย การจัดแนว สี fill/stroke ความกว้างเส้นขอบ ขนาดฟอนต์ ระยะห่างบรรทัด
Region rotation, mirror และค่าที่ Renderer ใช้จริง

แท็บ **Details** รวม Speaker, Note, Status, OCR Provider และคำสั่งลบ Block

คำว่า `Auto` (รวม alias เดิม `auto` และ `อัตโนมัติ`) ในช่อง override แบบข้อความหมายถึงไม่มี override และให้ระบบใช้ค่า
ส่วนกลาง/ค่าที่มีผลตามลำดับปกติ

## Activity

Activity/Log ถูกพับไว้เป็นค่าเริ่มต้นเพื่อให้ Viewer มีพื้นที่มากที่สุด ระหว่างงานจะแสดง progress
แบบบางใน status bar ส่วนปุ่ม **Cancel Workflow** จะแสดงบน main toolbar เฉพาะตอนที่งานกำลังทำงาน

เมื่อ workflow สำเร็จโดยไม่มีปัญหา Activity จะยังคงพับอยู่ หากมี error หรือรายการที่ทำไม่สำเร็จ
Activity จะเปิดอัตโนมัติและเก็บข้อความ raw technical error ไว้ใน log เพื่อแก้ไขต่อได้ งานหนึ่ง
หน้าหรือหนึ่ง Block ที่ผิดพลาดไม่ควรทำให้รายการถัดไปหยุดโดยไม่จำเป็น

## Focus และ Accessibility

ลำดับ focus หลักคือ `Navigator -> Viewer -> Inspector -> Activity` มี focus ring ที่มองเห็นได้
ชื่อที่เข้าถึงได้และ tooltip ภาษาอังกฤษสำหรับ control สำคัญ ปุ่มที่เป็น icon หรือปุ่มลบมี hit target
อย่างน้อย `36 px` และสถานะของ Block ใช้ทั้งข้อความและไอคอน ไม่พึ่งสีเพียงอย่างเดียว

workspace รองรับขนาดขั้นต่ำเป้าหมาย `1366x768` โดยปุ่มหลักบน toolbar ยังเข้าถึงได้โดยไม่ต้อง
เลื่อนหน้าต่างหลัก

## QSettings UI State

`QSettings` ใช้เก็บเฉพาะ state ของ UI ไม่ใช่ข้อมูลโปรเจกต์ โดยเก็บ:

- ขนาด main splitter
- ขนาด divider Original/Thai Preview
- แท็บปัจจุบันของ Navigator
- แท็บปัจจุบันของ Inspector
- การแสดง Activity

หากค่า missing, malformed หรืออยู่นอกช่วง ระบบใช้ค่าเริ่มต้นที่ปลอดภัย ไม่ทำให้เปิดโปรแกรมไม่ได้
และไม่เคยบันทึก Focus mode, API key, Provider secret หรือค่า IOPaint ลง `project.json`
