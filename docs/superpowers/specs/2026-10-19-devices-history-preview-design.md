# spider — รายชื่อเครื่อง, ส่งถึงเครื่อง, ประวัติไฟล์, ภาพตัวอย่าง

วันที่: 2026-10-19
สถานะ: ร่าง รอผู้ใช้อนุมัติ
ต่อยอดจาก: `docs/superpowers/specs/2026-08-19-spider-design.md` (เรียกว่า "spec เดิม")

---

## 1. ทำไมถึงทำ

ทุกวันนี้ spider เป็นตู้ฝากของ: ส่งจากเครื่องไหนก็ไปกองรวมกัน แล้วอีกเครื่องต้องเดินไปดึงเอง
ผู้ใช้อยาก **ดูว่าเครื่องไหนเปิดอยู่ก่อนส่งไฟล์ไปให้** แล้วให้ **เครื่องนั้นเด้งแจ้งเตือน**
กดแจ้งเตือนครั้งเดียว ไฟล์ลง Downloads หรือข้อความเข้า clipboard ทันที

สองอย่างที่มาด้วยกัน:

- **ประวัติไฟล์** — ไฟล์นี้มาจากเครื่องไหน และเครื่องไหนโหลดไปเก็บที่ไหน เมื่อไหร่
- **ภาพตัวอย่าง** — มองรายการแล้วรู้ทันทีว่าเป็นไฟล์อะไร โดยไม่ต้องอ่านชื่อ

**ความสำเร็จหน้าตาแบบนี้:** นั่งที่ Mac เห็น park-pc เป็นจุดเขียว ลากไฟล์ไปวางบนชื่อ park-pc
ไม่เกิน ~10 วินาทีต่อมา park-pc เด้งแจ้งเตือน กดแล้วไฟล์อยู่ใน `Downloads` และ Mac เห็นในแผงประวัติว่า
park-pc เก็บไว้ที่ `C:\Users\park\Downloads\report.pdf`

---

## 2. ขอบเขต

### ทำ

| ส่วน | GUI | CLI | Web UI |
|---|---|---|---|
| ส่งถึงเครื่องที่เลือก | ✅ | ✅ `--to` | — |
| รับแจ้งเตือน + กดแล้วบันทึก/คัดลอก | ✅ | — | — |
| ดูรายชื่อเครื่อง + สถานะ | ✅ แถบซ้าย | ✅ `spider devices` | — |
| ประวัติไฟล์ | ✅ แผงล่าง | — | — |
| ถูกนับในประวัติเวลาโหลด | ✅ พร้อม path | ✅ พร้อม path | ✅ IP เท่านั้น |
| ภาพตัวอย่าง / โลโก้ชนิดไฟล์ | ✅ | — | — |

### ไม่ทำ (ตั้งใจ)

- โหลดไฟล์ลงเครื่องปลายทางเองโดยไม่ต้องกด — ยังยึดหลัก spec เดิม "สั่งเองทั้งขาขึ้นและขาลง"
- ส่งถึงหลายเครื่องพร้อมกันในครั้งเดียว (item หนึ่งมีปลายทางได้เครื่องเดียว)
- แจ้งเตือนฝั่ง CLI / Web UI
- ปรับหน้าตา Web UI
- ภาพตัวอย่างของวิดีโอ, หน้าแรกของ PDF, HEIC, SVG
- WebSocket / push แบบ realtime — ใช้ polling 10 วินาทีแบบเดิม
- ย้อนสร้างประวัติของการโหลดที่เกิดก่อนเวอร์ชันนี้

### สิ่งที่ห้ามเกิด (ต่อให้ทำงานได้ก็ถือว่าล้มเหลว)

- **ไฟล์ต้นฉบับใน `blobs/` ถูกแตะ** — ภาพย่อแยกเก็บต่างหาก byte-for-byte ของเดิมต้องคงอยู่
- **ไฟล์ใน Downloads ถูกเขียนทับ** — ชื่อซ้ำต้องได้ชื่อใหม่เสมอ
- **ไฟล์ลงเครื่องโดยผู้ใช้ไม่ได้กด**
- **แจ้งเตือนเดิมเด้งซ้ำทุก 10 วินาที**
- **GUI ค้าง** ระหว่างโหลดภาพย่อหรือประวัติ

---

## 3. หน้าตา GUI (ผู้ใช้เลือกแล้วผ่าน mockup)

```
+--------------------------------------------------------------------+
| [Send clipboard] [Paste latest] [Send files…]   [Search........]   |
+----------------+---------------------------------------------------+
| เซิร์ฟเวอร์       |  [▣] mountain.jpg      3.4 MB  park-pc     2m    |
|  🖥️ nas        |  [py] scraper.py        4 KB  park-macbook 5m    |
| เปิดอยู่          |  [PDF] report.pdf     2.1 MB  park-macbook 1h → park-pc |
|  🟢 park-mac…  |  [T] api-notes        2.1 KB  park-pc     1h    |
|  🟢 park-pc    +---------------------------------------------------+
| ปิดอยู่           | ประวัติ · report.pdf                                |
|  ⚪ office-l… 12m| ⬆️ อัปโหลดจาก park-macbook · วันนี้ 09:12  → park-pc   |
|  ⚪ old-lap…  3d | ⬇️ park-pc → C:\Users\park\Downloads\report.pdf · 09:20 |
|                | ⬇️ office-linux → ~/work/report.pdf · 10:05          |
|                | ⬇️ เบราว์เซอร์ (192.168.1.40) · 11:30                  |
+----------------+---------------------------------------------------+
```

- **ซ้าย: แถบรายชื่อเครื่อง** (ตัวเลือก B) อยู่ตลอด แบ่งกลุ่ม เซิร์ฟเวอร์ / เปิดอยู่ / ปิดอยู่
  เครื่องที่ปิดแสดงว่าเห็นล่าสุดเมื่อไหร่ เครื่องนี้มีป้าย "(เครื่องนี้)"
- **ขวาบน: รายการไฟล์** แถวสูง ~48px (ตัวเลือก B) คอลัมน์แรกเป็นภาพตัวอย่าง
- **ขวาล่าง: แผงประวัติ** (ตัวเลือก A) ของแถวที่เลือกอยู่ อัปเดตทันทีเมื่อเปลี่ยนแถว
- ซ้าย/ขวา และ บน/ล่าง แบ่งด้วย splitter ลากปรับได้ — ขนาดเริ่มต้นและการจำขนาดไว้เป็นเรื่องของผู้สร้าง

---

## 4. รายชื่อเครื่องและสถานะ

### เครื่องถูกรู้จักอย่างไร

`SpiderClient` แนบ header ทุก request:

```
X-Spider-Device: park-pc       # ชื่อจาก config.device
X-Spider-Client: gui | cli
```

ทุก request ที่ผ่าน auth และมี `X-Spider-Device` → server upsert แถวในตาราง `devices`
อัปเดต `last_seen` และ `client` (ค่าล่าสุดที่เห็น) — เครื่องโผล่ในรายชื่อเองตั้งแต่ใช้ครั้งแรก
ไม่ต้องลงทะเบียน เบราว์เซอร์ไม่ส่ง header จึงไม่โผล่ในรายชื่อ

### "เปิดอยู่" คืออะไร

**เปิดอยู่ = แอป GUI ของเครื่องนั้นกำลังรัน** (รวมถึงซ่อนอยู่ใน tray)
วัดจาก `heartbeat_at` ไม่ใช่ `last_seen` เพราะการสั่ง CLI ครั้งเดียวไม่ได้แปลว่าเครื่องพร้อมรับแจ้งเตือน

- GUI เรียก `POST /api/devices/heartbeat` ทุก 10 วินาที (ไปพร้อมกับ poll รายการที่มีอยู่แล้ว)
  และตอนเปิดแอปทันที
- server ถือว่าเปิดอยู่เมื่อ `heartbeat_at` ห่างจากตอนนี้ไม่เกิน **30 วินาที** (พลาด 2 รอบยังไม่หลุด)
- GUI ตอนกด Quit ส่ง `DELETE /api/devices/heartbeat` เพื่อให้กลายเป็นเทาทันที (best effort ไม่รอผล)
- เครื่องที่ใช้แค่ CLI เป็นสีเทาเสมอ แสดงเห็นล่าสุดจาก `last_seen`

### เซิร์ฟเวอร์ (nas)

แถวบนสุดเป็นตัว server เอง สีเขียวถ้า request ล่าสุดของ GUI สำเร็จ สีแดงถ้าติดต่อไม่ได้
ชื่อที่แสดงคือ host จาก `config.server` — server ไม่ต้องรู้จักชื่อตัวเอง (ทางเลือกของผู้สร้าง)

### เปลี่ยนชื่อ / ลบเครื่อง

เปลี่ยนชื่อเครื่องใน Settings = server เห็นเป็นเครื่องใหม่ ชื่อเก่าค้างเป็นสีเทา
คลิกขวาที่เครื่องสีเทา → "ลบออกจากรายการ" (`DELETE /api/devices/{name}`)
ลบไม่กระทบ item หรือประวัติ — ประวัติเก็บชื่อเครื่องเป็นข้อความ ไม่ใช่ foreign key

---

## 5. ส่งถึงเครื่องที่เลือก

### ข้อมูล

item มี field ใหม่ `to_device: str | None` ไฟล์ยังอยู่ในรายการรวม ทุกเครื่องเห็นและโหลดได้ปกติ
`to_device` แค่บอกว่าใครควรได้แจ้งเตือน

`POST /api/items` รับ form field ใหม่ `to_device` (optional)
ถ้าชื่อนั้นไม่อยู่ในตาราง `devices` → `400 bad_request` พร้อมรายชื่อเครื่องที่รู้จัก — กันพิมพ์ผิดแล้วส่งไปหาเครื่องผี
ส่งหาเครื่องที่ปิดอยู่ได้ แจ้งเตือนจะเด้งตอนเครื่องนั้นเปิด GUI ครั้งถัดไป
ส่งหาตัวเองไม่ได้ (`400`)

### ฝั่ง GUI ผู้ส่ง

- ลากไฟล์ไปวางบนชื่อเครื่องในแถบซ้าย = ส่งไฟล์นั้นถึงเครื่องนั้น
  (ลากไปวางที่อื่นในหน้าต่าง = ส่งแบบไม่ระบุเครื่อง เหมือนเดิม)
- คลิกขวาที่ชื่อเครื่อง → "ส่ง clipboard ให้เครื่องนี้" / "ส่งไฟล์ให้เครื่องนี้…"
- รายการไฟล์แสดง `→ park-pc` ต่อท้ายคอลัมน์ From สำหรับ item ที่มีปลายทาง
- ถ้าส่งหาเครื่องที่เป็น CLI อย่างเดียว ยังส่งได้ แต่ status bar บอกว่า "เครื่องนี้ไม่มี GUI จะไม่เห็นแจ้งเตือน"

### ฝั่ง CLI ผู้ส่ง

```
spider push report.pdf --to park-pc
spider copy --to park-pc
spider push -t "hello" --to park-pc
spider devices            # ตารางเหมือนแถบซ้ายของ GUI: ชื่อ, client, สถานะ, เห็นล่าสุด
```

`spider ls` แสดงปลายทางในคอลัมน์ From แบบ `park-macbook → park-pc` (โหมด pipe เพิ่มคอลัมน์ท้าย `to_device` ว่างได้)

### ฝั่ง GUI ผู้รับ

**inbox:** `GET /api/inbox` คืน item ที่ `to_device = <X-Spider-Device>` และยังไม่ถูกแจ้ง
GUI เรียกทุก 10 วินาที (รอบเดียวกับ heartbeat) และตอนเปิดแอป

**แจ้งแล้วไม่แจ้งซ้ำ:** พอ GUI แสดงแจ้งเตือนของ item ไหน เรียก `POST /api/inbox/{id}/ack`
server ตั้ง `notified_at` item นั้นไม่กลับมาใน inbox อีก — ปิดแอป เปิดใหม่ ก็ไม่เด้งซ้ำ
ถ้าผู้ใช้ไม่ได้กดแจ้งเตือน item ยังอยู่ในรายการ โหลดเองได้ตามปกติ

**แจ้งเตือน:** ผ่าน tray `showMessage` ที่มีอยู่แล้ว แต่ **แสดงเสมอ** แม้หน้าต่างเปิดอยู่
(ต่างจาก `notify()` เดิมที่แสดงเฉพาะตอนซ่อน)

```
spider · จาก park-macbook
report.pdf (2.1 MB) — คลิกเพื่อบันทึกลง Downloads
```

ถ้า **ไฟล์** มาพร้อมกันหลายชิ้นในรอบเดียว รวมเป็นแจ้งเตือนเดียว "3 ไฟล์จาก park-macbook — คลิกเพื่อรับทั้งหมด"
**ข้อความไม่ถูกรวม** แต่ละชิ้นได้แจ้งเตือนของตัวเอง เพราะ clipboard เก็บได้ทีละอัน
ถ้ารวมกันจะมีข้อความหายเงียบ ๆ (การตัดสินใจของ Claude)

**กดแจ้งเตือน:**

- **ไฟล์** → บันทึกลง `~/Downloads/` ทันที ไม่ถามที่เก็บ ใช้ `actions.save_item` เดิม (`.part` → rename, ตรวจ sha256)
  - ชื่อผ่าน `safe_filename` เดิม
  - ชื่อซ้ำ → `report (1).pdf`, `report (2).pdf` … ไม่เขียนทับ
  - เสร็จแล้วแจ้งเตือนอีกรอบ "บันทึกไว้ที่ C:\Users\park\Downloads\report (1).pdf"
- **ข้อความ** → คัดลอกเข้า clipboard (ใช้ `actions.fetch_text` เดิม) แล้วแจ้ง "คัดลอกแล้ว"
- ทั้งสองแบบบันทึกประวัติ (ส่วนที่ 6)

**ข้อจำกัดที่ต้องรู้ — การคลิกแจ้งเตือน:** `QSystemTrayIcon.messageClicked` ทำงานไม่เท่ากันทุก OS
(Windows ทำงาน, macOS และ Linux บาง desktop อาจไม่ส่ง signal) ต้องมีทางสำรองเสมอ:

- แถบแจ้งเหนือรายการไฟล์ในหน้าต่างหลัก "report.pdf จาก park-macbook  [บันทึกลง Downloads] [ปิด]"
  ค้างจนกดหรือปิด — คลิกที่ tray icon แล้วหน้าต่างเปิดมาเห็นแถบนี้
- เมนู tray มีรายการ "รับ report.pdf" ล่าสุด
- ผู้สร้างต้องลองจริงบน macOS และ Windows แล้วบันทึกผลใน README ว่าคลิกแจ้งเตือนตรง ๆ ได้ที่ไหนบ้าง

---

## 6. ประวัติไฟล์

### อะไรถูกบันทึก

| เหตุการณ์ | เครื่อง | ที่เก็บ | ใครบันทึก |
|---|---|---|---|
| อัปโหลด | `source_device` | — | มีอยู่แล้วใน item |
| GUI บันทึกไฟล์ (Save as / กดแจ้งเตือน) | ชื่อเครื่อง | path เต็มบนเครื่องนั้น | client รายงานหลังโหลดเสร็จและ sha256 ผ่าน |
| GUI/CLI คัดลอกข้อความเข้า clipboard | ชื่อเครื่อง | `clipboard` | client รายงาน |
| CLI `pull` ลงไฟล์ | ชื่อเครื่อง | path เต็ม (absolute) | client รายงาน |
| CLI `cat` | ชื่อเครื่อง | `stdout` | client รายงาน |
| เบราว์เซอร์โหลด | `เบราว์เซอร์` | IP ผู้เรียก | server บันทึกเอง |
| เบราว์เซอร์กดปุ่ม Copy | `เบราว์เซอร์` | `clipboard (IP)` | server บันทึกเอง |

(`pull` ไม่มีโหมดเขียนลง stdout และรอบนี้ไม่เพิ่ม — การตัดสินใจของ Claude)

**ทำไม client รายงานเอง ไม่ใช่ server บันทึกตอนส่ง bytes:** มีแต่ client ที่รู้ path ปลายทาง
และรู้ว่าโหลดครบ sha256 ตรง — การโหลดที่พังกลางทางไม่ควรโผล่ในประวัติ

- client: หลังสำเร็จเรียก `POST /api/items/{id}/downloads` body `{"destination": "<path|clipboard|stdout>"}`
  ถ้า call นี้พัง **ไม่ถือว่าการโหลดพัง** — ไฟล์ลงเครื่องแล้ว แค่ warn ใน status bar / stderr
- server ฝั่งเบราว์เซอร์: **เบราว์เซอร์ = request ที่ผ่าน auth ด้วย session cookie** ไม่ใช่ Bearer token
  (การตัดสินใจของ Claude) — CLI/GUI ทุกรุ่นรวมถึงรุ่นเก่าใช้ Bearer จึงไม่ถูกนับเป็นเบราว์เซอร์
  auth dependency ต้องบอกต่อได้ว่าผ่านด้วยวิธีไหน (เช่นเก็บไว้ใน `request.state`)
  - `GET /content` ด้วย cookie และ `disposition=attachment` → บันทึก destination = IP
  - ปุ่ม Copy ของ Web UI (`static/app.js`) เพิ่ม query `purpose=clipboard` → บันทึก destination = `clipboard (IP)`
    เป็นการแก้ Web UI จุดเดียว ไม่เปลี่ยนหน้าตา
  - `disposition=inline` (เปิดดู) และภาพย่อไม่นับ
  - บันทึกตอนเริ่มส่ง ไม่รู้ว่าโหลดจบหรือไม่ — ยอมรับได้
- IP: `request.client.host` — ถ้า docker ไม่ส่ง IP จริงมา (เห็นเป็น IP ของ bridge) ก็แสดงตามนั้น ไม่แก้ในรอบนี้

### API

```
GET /api/items/{ref}/history
→ { "item": Item, "downloads": [ {"device", "client", "destination", "at"} ... ] }   # เก่า → ใหม่
```

### ฝั่ง GUI

แผงล่างแสดงบรรทัดอัปโหลด (พร้อม `→ ปลายทาง` ถ้ามี) ตามด้วยการโหลดแต่ละครั้ง เวลาแสดงเป็นเวลาท้องถิ่น
เปลี่ยนแถว → โหลดประวัติใหม่ (ใช้ serial กันผลเก่าทับผลใหม่ แบบเดียวกับ `refresh`)
ไม่มีแถวที่เลือก → แผงว่างพร้อมข้อความ "เลือกไฟล์เพื่อดูประวัติ"
path แสดงเต็ม เลือกคัดลอกได้

---

## 7. ภาพตัวอย่าง

### รูปภาพ → ภาพย่อจาก server

```
GET /api/items/{ref}/thumbnail     → image/png ด้านยาวสุด 96px (48px × 2 สำหรับจอ retina)
                                   → 404 ถ้าไม่ใช่รูปที่รองรับหรือสร้างไม่ได้
```

- รองรับ content-type: `image/jpeg`, `image/png`, `image/gif` (เฟรมแรก), `image/webp`, `image/bmp`
- สร้างตอนถูกขอครั้งแรก เก็บ cache ที่ `<data_dir>/thumbs/<2>/<2>/<id>.png` — **แยกจาก `blobs/`**
- สร้างไม่ได้ (ไฟล์เสีย, ใหญ่เกิน) → เขียนไฟล์ marker ว่างไว้ ครั้งต่อไปตอบ 404 ทันทีไม่ลองใหม่
- จำกัดต้นฉบับ ≤ 50 MB และ ≤ 50 ล้าน pixel (`Image.MAX_IMAGE_PIXELS` + ตั้งให้ DecompressionBombWarning เป็น error) — กัน CPU i3 ของ nas
- เคารพ EXIF orientation (`ImageOps.exif_transpose`) รูปจากมือถือจะได้ไม่ตะแคง
- ลบ item → ลบภาพย่อด้วย, `gc()` เก็บภาพย่อที่ไม่มี item แล้ว
- dependency ใหม่ฝั่ง server: **Pillow** (เพิ่มใน `server` extra และ Docker image)
- ไม่นับเป็นการโหลดในประวัติ

**ทำไมสร้างที่ server ไม่ใช่ที่ GUI:** ถ้า GUI ทำเอง ทุกเครื่องต้องโหลดรูปเต็ม 5–20 MB มาย่อเอง
ทุกรูป ทุกเครื่อง server ย่อครั้งเดียว ทุกเครื่องได้ไฟล์ไม่กี่ KB (การตัดสินใจของ Claude)

### GUI

- โหลดภาพย่อแบบ background ผ่าน `Task` เดิม เฉพาะแถวที่มองเห็น ไม่โหลดซ้ำ (cache ใน memory ตาม item id)
- ระหว่างรอ / 404 → แสดงโลโก้ชนิดไฟล์แทน

### ไฟล์อื่น → โลโก้ชนิดไฟล์

ผู้ใช้ต้องการ **โลโก้จริงของชนิดไฟล์** (Python, JavaScript, PDF …) ไม่ใช่กล่องสีมีตัวอักษร

- ใช้ SVG จาก [Material Icon Theme](https://github.com/material-extensions/vscode-material-icon-theme)
  (license MIT) คัดมาเฉพาะที่ใช้ เก็บที่ `src/spider/gui/icons/` พร้อมไฟล์ LICENSE (การตัดสินใจของ Claude)
- เลือกจากนามสกุลก่อน แล้วค่อย content-type
- ชุดขั้นต่ำ: รูป, PDF, ข้อความ/markdown, Python, JavaScript/TypeScript, HTML, CSS, JSON, YAML,
  shell, zip/tar/gz/7z, เสียง, วิดีโอ, Word, Excel, PowerPoint, csv, ไม่รู้จัก (ไอคอนไฟล์ทั่วไป)
- item ชนิด text ใช้ไอคอนข้อความ ส่วน tooltip 200 ตัวแรกแบบเดิมยังอยู่
- ชุดที่เกินขั้นต่ำเป็นเรื่องของผู้สร้าง
- ต้องเพิ่ม `package-data` ให้ไฟล์ SVG ติดไปกับ wheel และ PySide6-Essentials ต้อง render SVG ได้ (`QtSvg`) — ผู้สร้างตรวจ

---

## 8. Data model และ storage

### Model (`core/models.py`)

```python
class Item(BaseModel):
    ...                        # field เดิมทั้งหมด
    to_device: str | None = None

class Device(BaseModel):
    name: str
    client: str                # "gui" | "cli"
    last_seen: datetime
    online: bool               # server คำนวณจาก heartbeat_at

class Download(BaseModel):
    device: str                # "เบราว์เซอร์" สำหรับ browser
    client: str                # "gui" | "cli" | "browser"
    destination: str           # path, "clipboard", "stdout", หรือ IP
    at: datetime

class ItemHistory(BaseModel):
    item: Item
    downloads: list[Download]
```

`notified_at` อยู่ใน DB เท่านั้น ไม่ออกไปใน `Item` — client ไม่ต้องรู้

### SQLite

```sql
ALTER TABLE items ADD COLUMN to_device TEXT;
ALTER TABLE items ADD COLUMN notified_at TEXT;
CREATE INDEX idx_items_inbox ON items(to_device) WHERE notified_at IS NULL;

CREATE TABLE devices (
  name TEXT PRIMARY KEY,
  client TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  heartbeat_at TEXT
);

CREATE TABLE downloads (
  id INTEGER PRIMARY KEY,
  item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  device TEXT NOT NULL,
  client TEXT NOT NULL,
  destination TEXT NOT NULL,
  at TEXT NOT NULL
);
CREATE INDEX idx_downloads_item ON downloads(item_id);
```

**Migration:** DB ที่ใช้งานจริงบน nas มีข้อมูลอยู่แล้ว ใช้ `PRAGMA user_version`
(ตอนนี้ = 0) → ขั้น 1 รันคำสั่งข้างบน แล้วตั้งเป็น 1 ใน transaction เดียว รันใน `Storage.init()`
ต้องมีเทสต์ที่สร้าง DB แบบ schema เดิม ใส่ข้อมูล แล้ว migrate และข้อมูลเดิมครบ

`connect()` เปิด `foreign_keys=ON` อยู่แล้ว CASCADE จึงทำงาน

### Endpoint ใหม่ทั้งหมด

```
POST   /api/devices/heartbeat           # GUI เรียกทุก 10 วิ
DELETE /api/devices/heartbeat           # GUI ตอน quit
GET    /api/devices                     # list[Device]
DELETE /api/devices/{name}
GET    /api/inbox                       # list[Item] ของเครื่องที่ส่ง X-Spider-Device
POST   /api/inbox/{id}/ack
GET    /api/items/{ref}/history         # ItemHistory
POST   /api/items/{ref}/downloads       # {"destination": str} → 204
GET    /api/items/{ref}/thumbnail       # image/png หรือ 404
```

endpoint ที่ต้องรู้ว่า "เครื่องนี้คือใคร" (heartbeat, inbox, downloads) แล้วไม่มี `X-Spider-Device` → `400`
ทุกตัวอยู่หลัง auth เดิม error ใช้รูปแบบเดิม

### ความเข้ากันได้

- deploy server ก่อน แล้วค่อยอัปเดต client
- CLI เก่า + server ใหม่: ทำงานได้ (ไม่มี header → ไม่โผล่ในรายชื่อเครื่อง, ไม่มีประวัติ)
- GUI ใหม่ + server เก่า: endpoint ใหม่ตอบ 404 → GUI ซ่อนแถบเครื่อง/แผงประวัติ และใช้โลโก้แทนภาพย่อ
  ไม่ขึ้น error ซ้ำทุก 10 วินาที

---

## 9. Testing

ยึดแนวเดิม: logic อยู่นอก Qt (`gui/actions.py`) เทสต์ได้ไม่ต้องมีจอ

- **storage:** migration จาก schema เดิม, upsert device, online ภายใน/เกิน 30 วิ (ฉีดเวลาได้),
  inbox + ack ไม่คืนซ้ำ, CASCADE ลบประวัติตาม item, ภาพย่อถูกลบตาม item และ gc
- **api:** `to_device` ไม่รู้จัก → 400, ส่งหาตัวเอง → 400, ไม่มี header → 400,
  cookie + attachment ถูกบันทึก / cookie + inline ไม่ถูกบันทึก / cookie + `purpose=clipboard` บันทึกเป็น clipboard /
  Bearer (มีหรือไม่มี header ก็ตาม) ไม่ถูกบันทึกเป็นเบราว์เซอร์,
  thumbnail ของ PNG/JPEG ได้ PNG ≤ 96px, ของ PDF ได้ 404, รูปเสียได้ 404, **blob ต้นฉบับ sha256 เท่าเดิมหลังสร้างภาพย่อ**
- **gui actions:** ตั้งชื่อไม่ซ้ำใน Downloads (`report.pdf` มีแล้ว → `report (1).pdf`, ไม่มีนามสกุล, `.tar.gz`),
  รับ inbox แล้ว ack, รายงานประวัติพังไม่ทำให้การบันทึกพัง, เลือกไอคอนจากนามสกุล
- **cli:** `push --to`, `copy --to`, `devices`, `pull` รายงาน path absolute
- **roundtrip:** ส่งถึงเครื่อง → inbox → บันทึก → ประวัติมี path ที่ถูกต้อง
- **ลองมือ (README checklist):** คลิกแจ้งเตือนบน macOS / Windows, ลากไฟล์ไปวางบนชื่อเครื่อง, จุดเขียวหายภายใน ~30 วิหลังปิดแอป

---

## 10. สิ่งที่ปล่อยให้ผู้สร้างตัดสินใจ

- ขนาดเริ่มต้นของ splitter และการจำขนาดไว้ข้ามการเปิดแอป
- จะใช้ `QTreeWidget` หรือ `QListWidget` แบ่งหัวข้อสำหรับแถบเครื่อง
- ข้อความภาษาในหน้าจอ — GUI ปัจจุบันเป็นภาษาอังกฤษ ให้คงภาษาอังกฤษ ข้อความภาษาไทยในเอกสารนี้เป็นตัวอย่างความหมาย
- รูปแบบ output ของ `spider devices`
- ไอคอนเพิ่มเติมเกินชุดขั้นต่ำ
- วิธีจัด layout ของแถบแจ้งในหน้าต่างหลัก

---

## 11. สรุปการตัดสินใจ

| ตัดสินใจ | ใคร | เหตุผล |
|---|---|---|
| แถบเครื่องด้านซ้าย / ประวัติแผงล่าง / ภาพตัวอย่าง 48px | ผู้ใช้ (mockup) | |
| ส่งถึงเครื่อง = แจ้งเตือน ไม่โหลดเอง | ผู้ใช้ | คงหลัก "สั่งเอง" ของ spec เดิม |
| กดแจ้งเตือน → Downloads ทันที / ข้อความ → clipboard | ผู้ใช้ | |
| ส่งถึงเครื่องได้ทั้ง GUI และ CLI | ผู้ใช้ | |
| "เปิดอยู่" = GUI รัน วัดจาก heartbeat 30 วิ | Claude | CLI ไม่มีทางรับแจ้งเตือน จุดเขียวต้องแปลว่า "ส่งแล้วเห็น" |
| ปลายทางเดียวต่อ item | Claude | ไม่มีใครขอหลายเครื่อง และทำให้ inbox เป็นแค่ 2 คอลัมน์ |
| ack ที่ server ตอนแสดงแจ้งเตือน | Claude | ปิด/เปิดแอปแล้วไม่เด้งซ้ำ ไม่ต้องเก็บ state ที่ client |
| client รายงานการโหลดเอง | Claude | มีแต่ client ที่รู้ path และรู้ว่า sha256 ผ่าน |
| ภาพย่อสร้างที่ server ด้วย Pillow | Claude | ย่อครั้งเดียว ไม่ต้องโหลดรูปเต็มทุกเครื่อง |
| โลโก้จาก Material Icon Theme (MIT) | Claude | ผู้ใช้อยากได้โลโก้จริง ชุดนี้ครอบคลุมและ license ใช้ได้ |
| ชื่อซ้ำใน Downloads → `(1)` | Claude | ห้ามเขียนทับ และเป็นแบบที่เบราว์เซอร์ทำกันจนคุ้น |
| ประวัติเก็บชื่อเครื่องเป็นข้อความ | Claude | ลบเครื่องออกจากรายชื่อแล้วประวัติไม่หาย |
| เบราว์เซอร์ = auth ด้วย cookie | Claude | client รุ่นเก่าไม่ส่ง header ถ้าดูจาก header จะถูกนับเป็นเบราว์เซอร์ผิด ๆ |
| ข้อความไม่รวมแจ้งเตือน | Claude | clipboard เก็บได้อันเดียว รวมแล้วข้อความหาย |
