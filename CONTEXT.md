# Project Context — Manga Thai Translator

> Document version: `3.1.0`  
> Updated: `2026-08-11`
> Target language: Thai (`th`)  
> Revision focus: mixed-language input, provider routing, privacy, and Codex agent/runtime separation

## ภาพรวม

ผู้ใช้ต้องการสร้างโปรแกรม Desktop สำหรับ Linux/Pop!_OS เพื่อช่วยแปลมังงะ คอมิก และเว็บตูนจากภาษาญี่ปุ่น อังกฤษ เกาหลี หรือจีนเป็นภาษาไทย สำหรับไฟล์ภาพที่ผู้ใช้ Import เข้ามาเอง

ภาษาต้นฉบับที่ต้องรองรับใน MVP:

- ญี่ปุ่น: `ja`
- อังกฤษ: `en`
- เกาหลี: `ko`
- จีนตัวย่อ: `zh-Hans`
- จีนตัวเต็ม: `zh-Hant`
- เลือกอัตโนมัติ/หลายภาษาปนกัน: `auto`

ผู้ใช้ต้องเลือกภาษาเริ่มต้นระดับโปรเจกต์ได้ และ Override เฉพาะ Page หรือ Text Block ได้ ห้ามออกแบบโดยสมมติว่าทุกหน้าเป็นภาษาญี่ปุ่น

ภาษาปลายทางของ MVP คือภาษาไทย (`th`) แต่ Data Model และ Provider Interface
ต้องไม่ผูกติดกับข้อความภาษาไทยในชื่อ Field เพื่อให้เพิ่มภาษาเป้าหมายในอนาคตได้

โปรแกรมควรช่วยลดงานซ้ำใน Workflow นี้:

1. เตรียมภาพต้นฉบับ
2. ตรวจจับบริเวณข้อความ
3. เลือก OCR Provider ตามภาษาของ Project, Page หรือ Text Block
4. ตรวจและแก้ผล OCR
5. แปลเป็นภาษาไทย
6. ตรวจและเกลาบทแปล
7. วางข้อความไทยเป็น Preview
8. Export ข้อมูลหรือภาพ

แนวทางการทำงานอ้างอิงจากวิดีโอ:

- https://www.youtube.com/watch?v=CF1svvcuTdk

ให้อ้างอิงแนวคิดของ Workflow เท่านั้น ไม่ต้องจำลอง Adobe Illustrator, Photoshop หรือ PhotoScape X Pro ทุกฟีเจอร์ โปรแกรมนี้ต้องใช้เครื่องมือที่ทำงานบน Linux ได้

## ผู้ใช้และสภาพแวดล้อม

- ผู้ใช้มีพื้นฐาน Programmer
- ระบบหลัก: Linux/Pop!_OS
- ต้องทำงานได้โดยไม่มี NVIDIA GPU
- ต้องมี CPU fallback
- ต้องรองรับ Path และชื่อไฟล์ภาษาไทย
- ต้องมี GUI ที่ใช้ได้จริง ไม่ใช่ Command Line อย่างเดียว
- ผู้ใช้อาจใช้ ChatGPT Plus แบบ Manual
- การเรียกอัตโนมัติใช้ OpenAI-compatible API หรือ Ollama
- ChatGPT Plus ไม่ถือเป็น API quota และไม่ควรถูกออกแบบให้เป็น Dependency ของโปรแกรม

## Codex Agent Configuration Is Not App Configuration

- Agent หลักสำหรับพัฒนาโปรเจกต์: `gpt-5.6-sol` + `xhigh`
- Agent รองสำหรับ Implement: `gpt-5.6-sol` + `medium`
- ค่าทั้งสองอยู่ใน `.codex/` และมีผลกับ Workflow การพัฒนา
- Model ที่ผู้ใช้เลือกสำหรับแปลในแอปเป็น Configuration คนละส่วน
- ห้าม Copy API key, Model ID หรือ Provider Setting จาก Codex config เข้า `project.json`
- แอปต้องทำงานผ่าน Mock Provider ได้แม้ไม่มี OpenAI API key

## เป้าหมายของ MVP

MVP เน้น Workflow แบบ **อ่านข้อความ → ตรวจ → แปล → ตรวจ → Export**

สิ่งสำคัญที่สุด:

- ข้อมูลโปรเจกต์ไม่หาย
- ลำดับข้อความไม่สลับ
- ผู้ใช้แก้ OCR และคำแปลได้ทุก Block
- Provider เปลี่ยนได้
- เลือกและบันทึกภาษาต้นฉบับได้ถูกต้อง
- เปลี่ยนภาษาเฉพาะหน้า/เฉพาะ Block ได้โดยข้อมูลเดิมไม่หาย
- รองรับข้อความหลายภาษาปนกันในโปรเจกต์เดียว
- UI ไม่ค้างขณะทำงาน
- โปรแกรมไม่เขียนทับภาพต้นฉบับ
- Core Logic ทดสอบได้โดยไม่ต้องใช้ Model หรือ API จริง
- โหมด `auto` ต้องแสดงผลที่ตรวจพบและ Confidence; เมื่อไม่มั่นใจต้องให้ผู้ใช้เลือก ห้ามเดาแล้วส่งเข้า OCR Adapter เงียบ ๆ

## ขอบเขต Phase 2

สิ่งเหล่านี้ยังไม่ใช่เงื่อนไขบังคับของ MVP:

- Inpainting ซับซ้อนเพื่อลบข้อความเดิม
- Speech-bubble detection ขั้นสูง
- SFX detection และแปลเสียงเอฟเฟกต์
- Typesetting พร้อมเผยแพร่แบบอัตโนมัติเต็มรูปแบบ
- PDF และ CBZ
- Translation Memory
- Character voice consistency แบบอัตโนมัติ
- ตรวจจับภาษาอัตโนมัติที่แม่นยำระดับ Text Block โดยไม่ต้องให้ผู้ใช้ช่วยเลือก
- รองรับภาษาอื่นนอกเหนือจาก `ja`, `en`, `ko`, `zh-Hans` และ `zh-Hant`
- AppImage หรือ Flatpak

MVP อาจมี Language Detection แบบ Optional ได้ แต่หากยังไม่ได้ติดตั้ง Detector
ให้ `auto` เปลี่ยนเป็นสถานะที่ต้องการการยืนยันจากผู้ใช้ ไม่ใช่ Silent Fallback

## ขอบเขตด้านไฟล์และลิขสิทธิ์

โปรแกรมรับเฉพาะไฟล์ที่ผู้ใช้ Import เข้ามาเอง

Out of scope:

- ดาวน์โหลดมังงะ
- Scraping เว็บไซต์
- แกะ DRM
- ค้นหาแหล่งดาวน์โหลด
- เผยแพร่หรือแจกไฟล์อัตโนมัติ

## Technology Direction

- Python 3.12
- PySide6
- Pillow
- OpenCV
- manga-ocr สำหรับ OCR ภาษาญี่ปุ่น
- PaddleOCR หรือ OCR Adapter อื่นที่เหมาะสมสำหรับภาษาอังกฤษ เกาหลี จีนตัวย่อ และจีนตัวเต็ม
- `OcrProviderRegistry`/`OcrRouter` สำหรับเลือก Adapter ตามรหัสภาษา
- Text Detector ที่สลับ Provider ได้
- OpenAI-compatible API
- Ollama
- Pydantic
- pytest
- ruff
- `pyproject.toml`

Dependency OCR/ML ต้องเป็น Optional เพื่อให้เปิด Core Application และรัน Test ได้โดยไม่ต้องดาวน์โหลด Model จริง

Provider ทุกตัวต้องประกาศ Capability อย่างน้อย:

- ภาษาที่รองรับ
- CPU/GPU requirement
- รองรับข้อความแนวตั้งหรือไม่
- ต้องใช้ Network หรือไม่
- ส่งภาพออกนอกเครื่องหรือไม่

## Translation Principles

- แปลให้เป็นภาษาไทยที่เป็นธรรมชาติ
- รักษาความหมาย อารมณ์ มุก และบุคลิกตัวละคร
- ใช้ชื่อ คำเรียก และศัพท์เฉพาะตาม Glossary
- ไม่เพิ่มข้อความที่ไม่มีในต้นฉบับ
- ไม่รวม แยก หรือลำดับ Text Block เอง
- หากกำกวม ให้เก็บ Note สั้น ๆ เพื่อให้ผู้ใช้ตัดสินใจ
- ห้ามส่ง `auto` เป็นภาษาจริงไปยัง Translation Provider ต้อง Resolve หรือขอให้ผู้ใช้ยืนยันก่อน

## Translation System Prompt

```text
คุณคือนักแปลมังงะ คอมิก และเว็บตูนเป็นภาษาไทย

ภาษาต้นฉบับของแต่ละ Text Block จะระบุในฟิลด์ source_language
และอาจเป็นภาษาญี่ปุ่น อังกฤษ เกาหลี จีนตัวย่อ หรือจีนตัวเต็ม
หากหลายภาษาปนกัน ให้แปลแต่ละ Block ตาม source_language ของ Block นั้น

หน้าที่ของคุณคือแปลข้อความตามลำดับที่ได้รับ โดยรักษาความหมาย อารมณ์
บุคลิกตัวละคร ความสัมพันธ์ มุก และบริบทของเรื่อง

กฎสำคัญ:
- ตอบเป็น JSON ตาม Schema ที่กำหนดเท่านั้น
- ห้ามเพิ่ม ลบ รวม แยก หรือสลับ Text Block
- ใช้ ID เดิมทุก Block
- ใช้ source_language ของแต่ละ Block ห้ามสมมติว่าทุก Block เป็นภาษาเดียวกัน
- รักษาชื่อและคำเฉพาะตาม Glossary
- ใช้ภาษาไทยที่เป็นธรรมชาติ เหมาะกับวัยและบุคลิกตัวละคร
- ไม่แปลชื่อคนตามความหมาย เว้นแต่ Glossary ระบุไว้
- ใช้คำแทนตัวและคำลงท้ายให้สม่ำเสมอ
- หากต้นฉบับกำกวม ให้แปลจากบริบทและใส่คำอธิบายสั้น ๆ ใน note
- ห้ามแต่งข้อความที่ไม่มีในต้นฉบับ
- หาก source_text ว่างหรืออ่านไม่ได้ ให้คืนคำแปลว่างและอธิบายสั้น ๆ ใน note
- ห้ามอธิบายนอก JSON

รูปแบบผลลัพธ์:
{
  "translations": [
    {
      "id": "ID เดิม",
      "translated_text": "คำแปลภาษาไทย",
      "note": ""
    }
  ]
}
```

## Translation Request Context

Request สำหรับการแปลควรรองรับข้อมูลต่อไปนี้:

- Default source language ของ Project
- Source language Override ของ Page และ Text Block
- Target language
- Text Blocks พร้อม ID และ Reading Order
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

Cloud Provider ต้องรับเฉพาะข้อความและ Context ตามค่าเริ่มต้น ห้ามส่งภาพไป Cloud หากผู้ใช้ไม่ได้อนุญาตอย่างชัดเจน

การอนุญาตส่งภาพต้องเป็น Opt-in ที่อธิบาย Provider และข้อมูลที่จะส่งอย่างชัดเจน
และต้องไม่ถูกบันทึกเป็นค่าเปิดใช้งานแบบเงียบ ๆ สำหรับโปรเจกต์ใหม่

## ตัวอย่าง Translation Response

```json
{
  "translations": [
    {
      "id": "block-01",
      "translated_text": "กลับมาแล้วเหรอ",
      "note": ""
    },
    {
      "id": "block-02",
      "translated_text": "อืม กลับมาแล้ว",
      "note": "ใช้คำพูดกันเองตามความสัมพันธ์ของตัวละคร"
    }
  ]
}
```

ระบบต้องตรวจ Missing ID, Duplicate ID, Unknown ID และผลลัพธ์ที่ไม่ใช่ JSON ก่อนนำข้อมูลไปเขียนลงโปรเจกต์

## Current Product Decisions

- Desktop-first; ไม่ทำ Web App ใน MVP
- Offline-capable core; Cloud Translation เป็น Optional
- Manual correction สำคัญกว่า Full Automation
- Reading Order เป็น Setting แยกจากภาษา
- Source files immutable
- Mock-first development ก่อนเชื่อม Model จริง
- Phase 2 ไม่เริ่มจนกว่า MVP Acceptance Criteria ผ่าน

## Geometry Language

**Rotated Text Region**:
พื้นที่ข้อความบนหน้าที่มีทิศทางเป็นส่วนหนึ่งของขอบเขต การเลือก การรู้จำ การทำความสะอาด
และการจัดวางข้อความต้องอ้างอิงขอบเขตเอียงเดียวกัน Region เป็นเจ้าของมุมเพียงค่าเดียว
และทุกขั้นตอนใช้มุมนั้นร่วมกัน
_Avoid_: หมุนเฉพาะข้อความ, กรอบตรงที่แสดงผลเหมือนหมุน

**Text Mirror**:
การกลับข้อความไทยของแต่ละ Text Block ตามแนวนอนหรือแนวตั้ง โดยมีผลใน Thai Preview
และ Export เท่านั้น ไม่กลับภาพพื้นหลัง ขอบเขต OCR หรือพื้นที่ Clean แกน Mirror อ้างอิง
coordinate space ภายใน Region ตามแนวข้อความก่อนใช้มุมหมุนของ Region ทั้งสองแกนเป็นอิสระ
และเปิดพร้อมกันได้
_Avoid_: Invert color, กลับทั้งหน้า, กลับภาพต้นฉบับ
