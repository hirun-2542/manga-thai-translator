# Codex Instructions — Manga Thai Translator

> Document version: `3.1.0`  
> Updated: `2026-07-31`  
> Status: Active repository instructions  
> Revision focus: multilingual OCR/translation + verified Lead–Worker Codex workflow

ไฟล์นี้เป็นคำสั่งหลักสำหรับ Codex เมื่อทำงานใน repository นี้

## เอกสารที่ต้องอ่านก่อนเริ่มงาน

อ่านไฟล์ต่อไปนี้ทั้งหมดก่อนแก้โค้ด:

1. `CONTEXT.md`
2. `PROJECT_SPEC.md`
3. `.codex/config.toml`
4. `.codex/agents/implementation-worker.toml`
5. `README.md` และ `pyproject.toml` หากมีอยู่แล้ว

หากเอกสารขัดกัน ให้ใช้ลำดับความสำคัญดังนี้:

1. คำสั่งล่าสุดจากผู้ใช้
2. `AGENTS.md`
3. `PROJECT_SPEC.md`
4. `CONTEXT.md`
5. โค้ดหรือเอกสารเดิมใน repository

ค่ารุ่นเอกสารใน Header ใช้ตรวจว่ากำลังอ่านไฟล์ชุดเดียวกัน หากเลขรุ่นของ
`AGENTS.md`, `CONTEXT.md` และ `PROJECT_SPEC.md` ไม่ตรงกัน ให้หยุดและแจ้ง
Lead Agent ก่อนแก้โค้ด

## เป้าหมาย

พัฒนา Desktop Application ชื่อ **Manga Thai Translator** สำหรับ Linux/Pop!_OS เพื่อ:

- Import ภาพมังงะที่ผู้ใช้มีอยู่แล้ว
- ตรวจจับ Text Block และทำ OCR ต้นฉบับภาษาญี่ปุ่น อังกฤษ เกาหลี จีนตัวย่อ หรือจีนตัวเต็ม
- ให้ผู้ใช้ตรวจและแก้ข้อความ OCR
- แปลภาษาต้นฉบับที่เลือกเป็นภาษาไทยผ่าน Provider ที่เปลี่ยนได้
- เลือกภาษาระดับโปรเจกต์ และ Override ระดับ Page หรือ Text Block ได้
- รองรับโปรเจกต์หรือหน้าที่มีหลายภาษาปนกัน
- แก้คำแปล จัดลำดับ Block และบันทึกโปรเจกต์
- Export ข้อมูลและภาพ Preview ที่มีข้อความไทย

ให้ทำ MVP ที่เสถียรและทดสอบได้ก่อน Phase 2

## Multi-agent Operating Model

โปรเจกต์นี้ใช้รูปแบบ **Lead–Worker**:

### Lead Agent

Agent หลักใช้ค่าจาก `.codex/config.toml`:

- Model: `gpt-5.6-sol`
- Reasoning effort: `xhigh`
- หน้าที่หลัก: ทำความเข้าใจโจทย์ วางแผน ตัดสินใจด้าน Architecture แบ่งงาน สั่งงาน ตรวจสอบ และสรุปผล

ค่าข้างต้นเป็นการตั้งค่า Codex ที่ใช้พัฒนาโปรเจกต์ ไม่ใช่ Default Model
ของ Translation Provider ภายในแอป ห้ามนำค่า Model ของ Agent ไป Hardcode
ใน Application Runtime

Lead Agent ต้อง:

1. อ่านเอกสารและตรวจ repository ด้วยตัวเองก่อนแบ่งงาน
2. แยกงานออกเป็น Task ที่มีขอบเขตชัดเจนและตรวจสอบได้
3. ระบุให้ Worker ทราบอย่างน้อย:
   - เป้าหมายและ Acceptance Criteria
   - ไฟล์หรือโมดูลที่รับผิดชอบ
   - ไฟล์ที่ห้ามแก้
   - Tests หรือคำสั่งตรวจสอบที่ต้องรัน
   - รูปแบบผลลัพธ์ที่ต้องรายงานกลับ
4. ใช้ Worker สำหรับการ Implement, เขียน Test, สำรวจโค้ด หรือวิเคราะห์ Log ที่แบ่งออกจากงานหลักได้
5. ห้ามให้ Worker หลายตัวแก้ไฟล์หรือโมดูลเดียวกันพร้อมกัน
6. รอผลจาก Worker ที่เกี่ยวข้องทั้งหมดก่อน Integrate หรือสรุปงาน
7. ตรวจ `git diff` และอ่านโค้ดที่ Worker เปลี่ยนด้วยตัวเอง ห้ามยอมรับงานจาก Summary เพียงอย่างเดียว
8. รัน Tests และ Lint ที่สำคัญจาก Main Thread อีกครั้ง
9. หากพบข้อผิดพลาด ให้ส่งกลับ Worker เดิมแก้พร้อมหลักฐาน หรือแก้เฉพาะ Integration เล็กน้อยด้วยตัวเอง
10. เป็นผู้ตัดสินใจขั้นสุดท้ายเรื่อง Architecture, Public API, Data Model, Dependency และ Definition of Done
11. ตรวจว่า Worker ใช้ Role `implementation_worker` และไม่ได้รับ Model/Effort Override ที่ขัดกับไฟล์ Role
12. จำกัด Worker ที่เปิดพร้อมกันไม่เกินค่าจาก `.codex/config.toml` และลดจำนวนลงเมื่อ Write Scope อาจชนกัน

### Implementation Worker

Agent รองใช้ Agent Role `implementation_worker` จาก
`.codex/agents/implementation-worker.toml`:

- Model: `gpt-5.6-sol`
- Reasoning effort: `medium`
- หน้าที่หลัก: ลงมือทำ Task ที่ Lead Agent มอบหมายตามขอบเขต

Worker ต้อง:

- อ่าน `AGENTS.md`, `CONTEXT.md`, `PROJECT_SPEC.md` และไฟล์ที่เกี่ยวข้องก่อนแก้โค้ด
- แก้เฉพาะไฟล์และขอบเขตที่ได้รับมอบหมาย
- ไม่เปลี่ยน Architecture, Public API, Data Model หรือ Production Dependency เอง
- หากพบว่าต้องขยายขอบเขต ให้หยุดและรายงาน Lead Agent ก่อน
- รักษาการเปลี่ยนแปลงเดิมของผู้ใช้และ Worker อื่น
- รัน Tests/Lint ที่เกี่ยวข้องกับ Task ของตน
- รายงานไฟล์ที่เปลี่ยน คำสั่งที่รัน ผลทดสอบ ข้อจำกัด และความเสี่ยง
- ห้ามประกาศว่างานทั้ง Milestone เสร็จ เพราะ Lead Agent เป็นผู้ตรวจและอนุมัติ

### Delegation Rules

- งาน Implement ที่ไม่ใช่การแก้เล็กน้อยมาก ให้ Lead Agent Delegate ไปยัง `implementation_worker`
- ใช้ Worker สูงสุด 3 ตัวพร้อมกัน โดยไม่นับ Lead Agent
- Spawn Worker หลายตัวได้เฉพาะ Task ที่เป็นอิสระต่อกันและมี Write Scope ไม่ทับกัน
- งานที่มี Dependency ต่อกันต้องทำตามลำดับ ไม่บังคับ Parallel
- งานสำรวจหรือ Review ที่ไม่ต้องแก้ไฟล์ ให้กำหนด Worker เป็น Read-only Task ใน Prompt
- Lead Agent ต้องเก็บบริบทการตัดสินใจและ User Communication ไว้ที่ Main Thread
- Worker ส่งกลับเฉพาะข้อสรุป หลักฐาน และผลทดสอบที่จำเป็น ไม่ส่ง Log ยาวโดยไม่จำเป็น
- หากระบบ Subagent ใช้งานไม่ได้ Lead Agent ต้องแจ้งข้อจำกัดตามจริง แล้วจึงทำงานแบบ Single Agent

Task ที่ส่งให้ Worker ต้องมี Contract แบบย่อ:

```text
Goal:
Acceptance criteria:
Owned files/directories:
Forbidden files/directories:
Required tests/checks:
Expected return:
```

ห้ามใช้ข้อความกว้าง ๆ เช่น “ทำ Milestone นี้ให้เสร็จ” โดยไม่กำหนด File
Ownership และ Acceptance Criteria

Workflow มาตรฐาน:

1. Lead ตรวจ repository และสรุปสถานะ
2. Lead สร้างแผนและแบ่ง Task
3. Lead Spawn `implementation_worker` พร้อมขอบเขตที่ไม่ทับกัน
4. Worker Implement และทดสอบ
5. Lead รอและรวบรวมผล
6. Lead ตรวจ Diff, Architecture และ Regression
7. Lead รัน Test/Lint ซ้ำ
8. Lead ส่งงานกลับให้ Worker แก้หากยังไม่ผ่าน
9. Lead อนุมัติและสรุปผลต่อผู้ใช้

### Multi-agent Safety

- Subagent สืบทอด Sandbox และ Permission จาก Parent Session
- ห้ามให้ Worker ขอสิทธิ์เพิ่มหรือเปลี่ยน Security Policy เพื่อให้งานผ่าน
- ห้ามให้ Worker Commit, Push, Merge, Publish หรือส่งข้อมูลออกภายนอก เว้นแต่ผู้ใช้สั่งชัดเจนและ Lead ยืนยันขอบเขต
- ห้ามให้ Worker แก้ไฟล์เดียวกันพร้อมกัน แม้จะอยู่คนละ Worktree หากผลลัพธ์ต้อง Merge กลับจุดเดียวกัน
- งาน Read-only เช่นสำรวจโค้ด ตรวจ Tests หรือวิเคราะห์ Log ควรแยกออกจากงาน Write เมื่อช่วยลดความเสี่ยง

## วิธีทำงาน

ก่อนเขียนโค้ด:

1. ตรวจสอบโครงสร้าง repository และสถานะ Git
2. อ่านเอกสารที่ระบุด้านบน
3. สรุปสิ่งที่มีอยู่แล้ว สิ่งที่ขาด และความเสี่ยงของ Dependency
4. เสนอแผนสั้น ๆ แบ่งเป็น Milestone
5. เริ่มแก้โค้ดเมื่อขอบเขตชัดเจนแล้ว

ระหว่างพัฒนา:

- ทำทีละ Milestone ที่รันและตรวจสอบได้
- เริ่มจาก Domain Model, Persistence และ Mock Provider
- แยก Core, Services, Persistence และ UI ออกจากกัน
- ห้ามผูก Business Logic ไว้ใน PySide6 Widget
- งาน OCR, Translation และ Export แบบ Batch ต้องไม่ทำให้ UI ค้าง
- งานที่ใช้เวลานานต้องยกเลิกได้และรายงาน Progress ได้
- Error ของหน้าเดียวหรือ Block เดียวต้องไม่ทำให้ Batch ทั้งหมดหยุด
- ห้ามเขียนทับไฟล์ต้นฉบับ
- ห้าม Hardcode API key, Token หรือข้อมูลลับ
- ห้ามบันทึก API key ลง Log หรือ `project.json`
- ห้ามใช้ `except Exception: pass`
- ห้ามทำ Placeholder ให้ดูเหมือนฟีเจอร์ใช้งานได้จริง
- ห้ามอ้างว่าฟีเจอร์ทำงานแล้วหากยังไม่ได้ทดสอบ
- รักษาการเปลี่ยนแปลงเดิมของผู้ใช้และไม่แก้ไฟล์นอกขอบเขตโดยไม่จำเป็น

หลังแต่ละ Milestone:

1. รัน Automated Tests ที่เกี่ยวข้อง
2. รัน `ruff check .`
3. รัน `ruff format --check .` หากตั้งค่า Formatter แล้ว
4. สรุปไฟล์ที่เปลี่ยน
5. สรุปสิ่งที่ทดสอบผ่าน
6. ระบุข้อจำกัดหรือสิ่งที่ยังไม่รองรับตามจริง

## คำสั่งมาตรฐาน

ใช้คำสั่งของโปรเจกต์ที่มีอยู่ก่อน หากยังไม่มี ให้ตั้งค่าให้รองรับคำสั่งประมาณนี้:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
python -m app.main
pytest
ruff check .
ruff format --check .
```

Dependency ของ OCR/ML ที่มีขนาดใหญ่ควรเป็น Optional Extra เช่น:

```bash
python -m pip install -e ".[dev,ocr]"
```

โปรแกรมและ Automated Tests พื้นฐานต้องเปิดหรือรันได้แม้ยังไม่ได้ติดตั้ง Model OCR จริง

## Architecture Guardrails

- ใช้ Python 3.12 ตามสเปก หาก Dependency OCR สำคัญยังไม่รองรับ ให้รายงานหลักฐานและเสนอ Python 3.11 ก่อนเปลี่ยน
- ใช้ Type Hint ใน Public API
- ใช้ Pydantic สำหรับข้อมูลที่บันทึกและข้อมูลจาก Provider
- ใช้ Protocol หรือ Abstract Interface สำหรับ OCR, Text Detection และ Translation Provider
- ใช้รหัสภาษามาตรฐานภายในระบบ ได้แก่ `ja`, `en`, `ko`, `zh-Hans`, `zh-Hant` และ `auto`
- แยก `OcrProviderRegistry` หรือ `OcrRouter` ออกจาก OCR Adapter แต่ละภาษา
- ให้ Project มีภาษาต้นฉบับเริ่มต้น และอนุญาตให้ Page/Text Block Override ได้
- ห้ามเลือก Reading Order จากภาษาเพียงอย่างเดียว ต้องใช้ค่า Project/Page Setting ที่ผู้ใช้แก้ได้
- ใช้ UUID ที่เสถียรสำหรับ Page และ Text Block
- เก็บ Bounding Box ด้วยพิกัดของภาพจริง ไม่ใช้พิกัดหลัง Zoom
- แยก Secret Configuration ออกจากข้อมูลโปรเจกต์
- ใช้ Atomic Write สำหรับ `project.json`
- ใช้ Mock Provider ใน Test และห้ามเรียก API หรือดาวน์โหลด Model จริง
- หลีกเลี่ยง Global Mutable State
- เพิ่ม Migration หรือ Version Field ให้รูปแบบไฟล์โปรเจกต์

## Definition of Done

งานจะถือว่าเสร็จเฉพาะเมื่อ:

- Acceptance Criteria ใน `PROJECT_SPEC.md` ผ่าน
- Tests ที่เกี่ยวข้องผ่านจริง
- README อธิบายการติดตั้ง Run Test และข้อจำกัดบน Ubuntu/Pop!_OS
- ไม่มี Secret หรือไฟล์ต้นฉบับของผู้ใช้ถูก Commit
- รายงานสิ่งที่ยังไม่รองรับอย่างตรงไปตรงมา

ก่อนรายงานว่า Milestone เสร็จ Lead Agent ต้องยืนยันเองว่า:

- อ่าน Diff จริง ไม่รับรองจาก Summary ของ Worker อย่างเดียว
- Tests/Lint ที่เกี่ยวข้องผ่านจาก Main Thread
- ไม่มี Placeholder, Silent Fallback หรือ Mock ถูกเปิดใช้ใน Production Path โดยไม่ตั้งใจ
- User-visible behavior ตรง Acceptance Criteria

## สิ่งที่ห้ามเพิ่ม

- ระบบดาวน์โหลดมังงะ
- Web Scraping แหล่งมังงะ
- การแกะหรือหลบ DRM
- การอัปโหลดภาพไป Cloud โดยไม่ได้รับอนุญาตจากผู้ใช้
- ฟีเจอร์เผยแพร่หรือแจกจ่ายไฟล์มังงะอัตโนมัติ

## Session Start Prompt

ใช้ Prompt นี้เมื่อเริ่ม Session ใหม่:

```text
อ่าน AGENTS.md, CONTEXT.md, PROJECT_SPEC.md และ .codex configuration ให้ครบ
ตรวจว่าเอกสารเป็น version 3.1.0 และ repository เป็น trusted project
จากนั้นตรวจ repository, สรุปสถานะ และเริ่ม Milestone ถัดไป
ให้ Lead Agent วางแผนและตรวจรับงาน ส่วน implementation_worker ลงมือทำงาน
เฉพาะ Task ที่มีขอบเขตและไฟล์รับผิดชอบชัดเจน
```
