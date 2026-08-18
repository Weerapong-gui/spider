# spider — Design Spec

วันที่: 2026-08-19
สถานะ: อนุมัติแล้ว รอเขียน implementation plan
Repo: https://github.com/Weerapong-gui/spider

---

## 1. ภาพรวม

spider คือระบบส่งข้อความและไฟล์ข้ามเครื่องส่วนตัว รันบน Docker บนเครื่อง Linux ที่บ้าน
เข้าถึงได้จาก Mac, Windows, Linux ผ่าน CLI และเบราเซอร์

**ปัญหาที่แก้:** อยากคัดลอกข้อความยาวๆ หรือส่งไฟล์จากเครื่องหนึ่งไปอีกเครื่องหนึ่ง
โดยไม่ต้องพึ่ง cloud ของคนอื่น ไม่ต้องส่งอีเมลหาตัวเอง ไม่ต้องเสียบ USB

**เคสหลักที่ต้องทำได้:**

```
# บน Mac (คัดลอกข้อความไว้แล้ว)
spider copy

# บน PC หรือ Linux
spider paste        # ข้อความอยู่ใน clipboard แล้ว
```

**สิ่งนี้ไม่ใช่:** ไม่ใช่ Nextcloud ไม่ใช่ Dropbox ไม่ใช่ระบบ sync
ไม่มี sync daemon ไม่มี conflict resolution ไม่มี version history ไม่มีระบบ user
มันคือ "ตู้ฝากของออนไลน์" ที่มี HTTP API — สั่ง push สั่ง pull เท่านั้น

---

## 2. ข้อกำหนดที่ยืนยันแล้ว

| ข้อ | ค่า |
|---|---|
| ผู้ใช้ | คนเดียว หลายเครื่อง ไม่มีระบบ user account |
| ชนิดข้อมูล | ข้อความ และ ไฟล์ |
| ความครบของไฟล์ | **byte-for-byte เหมือนเดิม** ห้ามบีบอัด ห้ามแปลง ห้ามย่อภาพ |
| การส่งข้อมูล | สั่งเองทั้งขาขึ้นและขาลง ไม่มี auto-sync |
| เครือข่าย | LAN + Tailscale เท่านั้น ไม่เปิดออกอินเทอร์เน็ต |
| GUI | Web UI ในเบราเซอร์ |
| ภาษา | Python |
| Deploy | Docker Compose บน Ubuntu 24.04 |

---

## 3. เครื่องปลายทาง

ตรวจสอบเมื่อ 2026-08-19 ผ่าน SSH

| | |
|---|---|
| Hostname | `nas-su` |
| Tailscale IP | `100.95.121.2` |
| OS | Ubuntu 24.04.4 LTS, kernel 6.8, x86_64 |
| CPU | Intel i3-2120 (2C/4T) |
| RAM | 7.6 GB (ว่าง 5.1 GB) + swap 4 GB |
| Docker | 29.7.2, Compose v5.4.0 |
| ผู้ใช้ deploy | `park` uid=1000 gid=1000 อยู่ในกลุ่ม `docker` |

**ความเร็วที่วัดได้:**

- เขียนดิสก์ `/mnt/nas` (ext4 บน `/dev/sdb1`) = 100 MB/s
- sha256 บน CPU นี้ = ~240 MB/s

hash เร็วกว่าดิสก์ 2.4 เท่า จึงคำนวณ checksum ระหว่าง stream ได้โดยไม่กระทบความเร็ว

**พื้นที่ดิสก์:**

```
/          914G  ใช้ 671G  เหลือ 206G  (77%)
/mnt/nas   916G  ใช้ 761G  เหลือ 109G  (88%)
```

**ข้อจำกัดของเครื่องที่ต้องเคารพ:**

เครื่องนี้ไม่ใช่ NAS เปล่า มี 17 container รันงานจริงอยู่แล้ว รวมถึงฐานข้อมูล
MariaDB และ PostgreSQL ที่มีข้อมูลของระบบอื่น และมี `cloudflared` tunnel 2 ตัว
ที่เปิดทางเข้าจากอินเทอร์เน็ต

1. **พอร์ตที่ถูกจองแล้ว:** 80, 3000, 3002, 3306, 3307, 5432, 8000, 8080, 8100
   spider ใช้ **8181** (ตรวจแล้วว่าง)
2. **ห้ามเพิ่ม spider เข้า cloudflared tunnel เด็ดขาด** ระบบนี้ออกแบบมาให้อยู่หลัง
   Tailscale เท่านั้น ไม่มีการป้องกันระดับที่เพียงพอสำหรับการเปิดออกอินเทอร์เน็ต
3. **`/mnt/nas` ถูกแชร์ผ่าน Samba** ในชื่อแชร์ `[NAS]` ทั้งก้อน
4. **`/mnt/nas` เหลือ 109 GB** ถ้า spider ทำดิสก์เต็ม container อื่นทั้งหมดพังตาม

---

## 4. โครงสร้าง Repo

Monorepo เดียว แบ่งเป็น 3 ส่วนตามหน้าที่

```
spider/
├─ src/spider/
│  ├─ core/
│  │  ├─ models.py       # pydantic: Item, ItemKind และ schema ของ API
│  │  ├─ config.py       # อ่าน env (server) และ config.toml (cli)
│  │  └─ errors.py       # error code ที่ทั้งสองฝั่งใช้ร่วมกัน
│  ├─ server/
│  │  ├─ app.py          # FastAPI app factory
│  │  ├─ routes.py       # endpoints
│  │  ├─ storage.py      # blob บนดิสก์ + SQLite index
│  │  ├─ auth.py         # bearer token + session cookie
│  │  └─ static/         # web UI: index.html, app.js, style.css
│  └─ cli/
│     ├─ main.py         # typer app
│     ├─ client.py       # httpx wrapper
│     └─ clipboard.py    # pbcopy / xclip / wl-copy / win32
├─ tests/
│  ├─ test_storage.py
│  ├─ test_api.py
│  ├─ test_cli.py
│  └─ test_roundtrip.py
├─ docker/
│  ├─ Dockerfile
│  └─ compose.yml
├─ docs/superpowers/specs/
├─ pyproject.toml
├─ .env.example
└─ README.md
```

### กฎการพึ่งพา (บังคับ)

```
core  <-  server
core  <-  cli
```

- `core` ไม่ import อะไรจาก `server` หรือ `cli`
- `server` กับ `cli` ไม่รู้จักกัน คุยผ่าน HTTP เท่านั้น

**เหตุผลที่เลือก monorepo:** `core/models.py` เป็นแหล่งความจริงเดียวของ schema
ทั้งสองฝั่ง import ตัวเดียวกัน จึงไม่มีทางเกิดกรณี CLI ส่ง field ชื่อเก่าไปหา server
ที่เปลี่ยน schema ไปแล้ว ซึ่งเป็น bug ที่หายากที่สุดในระบบ client-server
ถ้าวันหน้าต้องแยก repo ตัดตามขอบ 3 โฟลเดอร์นี้ได้ทันที

### Dependency

```toml
requires-python = ">=3.11"          # StrEnum ต้อง 3.11 ขึ้นไป

[project.optional-dependencies]
server = ["fastapi", "uvicorn", "python-multipart"]
cli    = ["typer", "httpx", "rich", "pyperclip"]
dev    = ["pytest", "pytest-cov", "hypothesis", "ruff"]
```

`core` ใช้แค่ `pydantic` เครื่อง client จึงไม่ต้องลง FastAPI
`hypothesis` ใช้กับ property test ใน `test_roundtrip.py` (ดูหัวข้อ 10)

---

## 5. Data Model และ Storage

### แนวคิดหลัก

ทุกอย่างที่ส่งข้ามเครื่องคือ **item** เดียวกัน ไม่แยกโลกข้อความกับไฟล์
item มี field `kind` เป็น `text` หรือ `file` ผลคือมี list เดียว API เดียว หน้าจอเดียว

### Model

```python
class ItemKind(StrEnum):
    text = "text"
    file = "file"

class Item(BaseModel):
    id: str                  # ULID
    kind: ItemKind
    name: str                # ชื่อไฟล์ หรือ label ของข้อความ
    size: int                # bytes
    sha256: str
    content_type: str
    created_at: datetime
    source_device: str
    preview: str | None      # 200 ตัวแรกของ text ใช้แสดงใน list
```

**ทำไม ULID:** เรียงตามเวลาได้ในตัวโดยไม่ต้อง sort ไม่ชนกันแม้ push พร้อมกันจากหลายเครื่อง
ไม่ต้องพึ่ง sequence ของ DB — ยาว 26 ตัวอักษร CLI จึงรับ prefix ได้

### โครงสร้างบนดิสก์

```
/mnt/nas/.spider/
├─ db.sqlite
└─ blobs/
   ├─ tmp/                    # ที่พักระหว่างอัปโหลด
   └─ 01/JD/01JD3K7X...       # ไฟล์ดิบ ชื่อ = ULID แตกโฟลเดอร์ตาม prefix 2 ชั้น
```

แตกโฟลเดอร์ 2 ชั้นเพื่อไม่ให้ไดเรกทอรีเดียวมีไฟล์เป็นแสน

**ชื่อไฟล์ตาม ULID ไม่ใช่ตาม sha256 (ไม่ทำ dedup)** — content-addressing ได้ dedup ฟรี
แต่ต้องนับ reference ตอนลบ ถ้านับพลาดคือลบ blob ที่ item อื่นยังใช้อยู่ แปลว่าข้อมูลหาย
ผู้ใช้คนเดียว dedup ไม่คุ้มความเสี่ยงนั้น เก็บ sha256 ไว้ในคอลัมน์เพื่อตรวจสอบอย่างเดียว

**ตำแหน่ง `/mnt/nas/.spider/`** ขึ้นต้นด้วยจุดเพื่อซ่อนจาก Finder และแนะนำให้เพิ่ม
`veto files = /.spider/` ในแชร์ Samba `[NAS]` เพื่อกันการลบโดยไม่ตั้งใจ

### ข้อความเก็บเป็น blob เหมือนไฟล์

ไม่เก็บลงคอลัมน์ TEXT เก็บเป็น UTF-8 bytes ใน `blobs/` เส้นทางเดียวกับไฟล์
เหตุผล: มี code path เดียว ลบเหมือนกัน อ่านเหมือนกัน และไม่เจอปัญหาตอน paste log ขนาดใหญ่

### SQLite

```sql
CREATE TABLE items (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  content_type TEXT NOT NULL,
  created_at TEXT NOT NULL,
  source_device TEXT NOT NULL,
  preview TEXT
);
CREATE INDEX idx_items_created ON items(created_at DESC);
```

เปิด WAL mode ผู้ใช้คนเดียว writer เดียวเพียงพอ ไม่ต้องใช้ Postgres

### เส้นทางรับประกันว่าไฟล์ครบ

**ขาขึ้น:**

1. stream ลง `blobs/tmp/<ulid>.part` คำนวณ sha256 ไปพร้อมกันระหว่าง stream
   ไม่โหลดทั้งไฟล์เข้า RAM
2. เสร็จแล้ว `os.rename()` ไปตำแหน่งจริง — atomic บน filesystem เดียวกัน
   จึงไม่มีทางที่ใครจะเห็นไฟล์ครึ่งท่อน
3. ถ้า client ส่ง sha256 ที่คาดไว้มาด้วยแล้วไม่ตรง ลบ temp ตอบ 422
4. เขียนแถวลง DB หลัง rename สำเร็จเท่านั้น

**ขาลง:**

5. stream bytes ดิบออก แนบ `Content-Length` และ header `X-Spider-Sha256`
6. CLI ตรวจ sha256 หลังโหลดเสร็จ ไม่ตรง = exit code ไม่เป็นศูนย์

**ไม่มีจุดใดในระบบที่บีบอัด แปลงรูปแบบ หรือย่อภาพ**

### การป้องกันดิสก์เต็ม

ก่อนรับทุกอัปโหลด server ตรวจพื้นที่ว่างของ filesystem ปลายทาง
ถ้าเหลือน้อยกว่า `SPIDER_MIN_FREE_GB` (default **10**) ปฏิเสธด้วย 507 ทันที
ไม่ว่าไฟล์จะเล็กแค่ไหน

เหตุผล: `/mnt/nas` ใช้ไปแล้ว 88% และมี container อื่นอีก 16 ตัวใช้เครื่องเดียวกันอยู่
ถ้า spider ทำดิสก์เต็ม MariaDB จะเขียนไม่ได้ ซึ่งทำให้ข้อมูลของระบบอื่นเสียหายจริง
spider ต้องยอมพังตัวเองก่อนที่จะลากระบบอื่นพังไปด้วย

### การจัดการความผิดปกติ

| เหตุ | ผล |
|---|---|
| server ตายกลางอัปโหลด | เหลือไฟล์ใน `tmp/` ไม่มีแถวใน DB ไม่มีใครเห็น |
| ตายระหว่าง rename กับ insert DB | blob กำพร้า ถูกกวาดตอน server start (ไม่มีแถว DB และเก่ากว่า 1 ชม.) |
| blob หายไปเพราะถูกลบผ่าน Samba | `spider verify` รายงานรายการที่ blob หาย ไม่ปล่อยให้ 500 ตอนกดโหลด |
| ดิสก์เต็ม | 507 ลบ temp CLI แจ้งชัดเจน |
| sha256 ไม่ตรง | 422 ขาขึ้น / exit code ไม่เป็นศูนย์ ขาลง ไม่เงียบ |

### นโยบายการเก็บข้อมูล

ค่าเริ่มต้นคือเก็บถาวร ไม่ลบอะไรเองอัตโนมัติ
`SPIDER_RETENTION_DAYS` มีให้เปิดถ้าต้องการ แต่ปิดไว้เป็นค่าเริ่มต้น
`SPIDER_MAX_ITEM_MB` default 0 = ไม่จำกัดขนาดต่อไฟล์

---

## 6. HTTP API

### Endpoints

```
GET    /healthz                  # ไม่ต้อง auth ใช้กับ docker healthcheck
POST   /api/session              # แลก token เป็น cookie (สำหรับเบราเซอร์)
GET    /api/items                # list
POST   /api/items                # upload
GET    /api/items/{id}           # metadata
GET    /api/items/{id}/content   # ดาวน์โหลด bytes ดิบ
DELETE /api/items/{id}
GET    /                         # web UI
```

### Upload

`POST /api/items` รับ `multipart/form-data` เท่านั้น ไม่มีทางเลือก JSON

| field | ค่า |
|---|---|
| `content` | bytes ดิบ (required) |
| `kind` | `text` หรือ `file` (default `file`) |
| `name` | ชื่อไฟล์ หรือ label |
| `sha256` | client คำนวณมา (optional) server ตรวจให้ |
| `device` | ชื่อเครื่องที่ส่ง |

ตอบ `201` พร้อม Item json

**ทำไมไม่มี JSON endpoint แยกสำหรับข้อความ:** ถ้ารับสองรูปแบบจะมี code path แยกสองเส้น
เส้นหนึ่ง streaming อีกเส้นไม่ streaming แล้ววันหนึ่งจะมีคน paste log 50 MB
แล้ว server กิน RAM 50 MB — เส้นเดียวที่ streaming เสมอปลอดภัยกว่า
Web UI ใช้ `FormData` ส่งได้ตรงๆ อยู่แล้ว

### List

```
GET /api/items?limit=50&before=<ulid>&q=<คำค้น>&kind=text|file
```

ใช้ cursor (`before=<ulid>`) ไม่ใช้ offset เพราะระหว่างที่เลื่อนดู อาจมีของใหม่
push เข้ามาจากอีกเครื่อง ทำให้ offset แสดงของซ้ำหรือกระโดดข้าม
`q` ค้นจาก `name` และ `preview` ด้วย `LIKE` ผู้ใช้คนเดียวไม่ต้องมี full-text index

### การอ้าง ID แบบสั้น

server เป็นฝ่ายแก้ปัญหาให้ ไม่ใช่ client

- `latest` = ตัวล่าสุด
- prefix ไม่ครบ เจอตัวเดียว = คืนตัวนั้น
- prefix เจอหลายตัว = `409` พร้อมรายชื่อผู้เข้าชิง
- ไม่เจอ = `404`

ทำฝั่ง server ทำให้ CLI ไม่ต้องดึง list มา match เอง และ Web UI ได้พฤติกรรมเดียวกันฟรี

### Authentication

ระบบใช้ shared secret token ตัวเดียว ไม่มีตาราง user ไม่มี password hashing
ไม่มี session store เพราะมีผู้ใช้คนเดียว และเซิร์ฟเวอร์อยู่หลัง Tailscale
ซึ่งจัดการ encryption และการยืนยันตัวตนระดับเครือข่ายให้แล้ว
token เป็นชั้นที่สองสำหรับกันเครื่องอื่นในวง LAN เดียวกัน

- token อ่านจาก env `SPIDER_TOKEN`
- **เซิร์ฟเวอร์ปฏิเสธการสตาร์ท** ถ้า `SPIDER_TOKEN` ไม่ถูกตั้ง หรือสั้นกว่า 32 ตัวอักษร
  ไม่มี default token ไม่มีโหมด `--no-auth`
- CLI ส่งผ่าน header `Authorization: Bearer <token>`
- เปรียบเทียบด้วย `secrets.compare_digest()` เท่านั้น ไม่ใช้ `==`
  เพื่อไม่ให้รั่วข้อมูลผ่านเวลาที่ใช้เปรียบเทียบ
- ไม่ผ่าน = `401` ทุก endpoint ยกเว้น `/healthz`

**ฝั่งเบราเซอร์:** ไม่เก็บ token ใน `localStorage` ผู้ใช้กรอก token ครั้งเดียวที่
`POST /api/session` แล้วเซิร์ฟเวอร์ตั้ง cookie แบบ `HttpOnly` + `SameSite=Strict`
JavaScript บนหน้าเว็บอ่าน cookie นั้นไม่ได้ ต่อให้มีช่อง XSS หลุดเข้ามา
token ก็ไม่ถูกขโมยออกไป auth dependency รับได้ทั้ง Bearer header และ cookie

**Network binding:** compose ผูก port กับ Tailscale IP โดยเฉพาะ ไม่ใช่ `0.0.0.0`

```yaml
ports:
  - "100.95.121.2:8181:8181"
```

ถ้าเขียน `- "8181:8181"` เฉยๆ Docker จะเปิด port ออกทุก interface
ซึ่งบนเครื่องนี้หมายถึงเปิดให้ทั้งวง LAN เห็น

**ไม่ใส่ CORS middleware** — Web UI เสิร์ฟจาก origin เดียวกับ API จึงไม่จำเป็น
และการไม่เปิดคือการปิดช่องหนึ่งช่อง

**ไม่ทำ rate limiting** — ผู้ใช้คนเดียวหลัง VPN ไม่มี attack surface ให้จำกัด

### รูปแบบ error

```json
{"error": {"code": "checksum_mismatch", "message": "sha256 ไม่ตรงกับที่ส่งมา"}}
```

`code` นิยามที่ `core/errors.py` ที่เดียว CLI แปลงเป็นข้อความอ่านง่ายและ exit code
ไม่พ่น traceback ใส่หน้าผู้ใช้

| code | HTTP | CLI exit |
|---|---|---|
| `unauthorized` | 401 | 3 |
| `not_found` | 404 | 4 |
| `ambiguous_id` | 409 | 5 |
| `checksum_mismatch` | 422 | 6 |
| `disk_full` | 507 | 7 |
| ติดต่อ server ไม่ได้ | — | 8 |

---

## 7. CLI

### คำสั่ง

```
spider init                  # ตั้ง server URL และ token (ครั้งเดียวต่อเครื่อง)

spider copy                  # clipboard เครื่องนี้ -> server
spider paste                 # server (ตัวล่าสุด) -> clipboard เครื่องนี้
spider paste 01JD3           # ระบุ item

spider push report.pdf       # ส่งไฟล์
spider push *.png            # หลายไฟล์
cat log.txt | spider push -  # จาก stdin
spider push -t "ข้อความ"      # ข้อความตรงๆ

spider ls                    # ดูรายการ
spider ls -q invoice         # ค้นหา
spider ls --files            # เฉพาะไฟล์

spider pull 01JD3            # โหลดลงโฟลเดอร์ปัจจุบัน
spider pull latest
spider cat 01JD3             # พ่นออก stdout

spider rm 01JD3
spider verify                # ตรวจว่ามี item ไหน blob หายไปบ้าง
```

**`copy` / `paste` เป็นคำสั่งหลัก** ใช้คำเดียวกับที่นิ้วคุ้นอยู่แล้ว
เคสหลักจึงจบใน 1 คำสั่งต่อเครื่อง ไม่ต้องพิมพ์ ID

**`cat` มีไว้สำหรับเครื่องที่ไม่มี clipboard** เช่นเครื่องที่ SSH เข้าไป
`spider paste` บนเครื่องแบบนั้นจะแจ้งว่าไม่มี clipboard backend และแนะนำให้ใช้ `cat` แทน
ไม่ crash เป็น traceback

### Clipboard ต่อ OS

| OS | เบื้องหลัง |
|---|---|
| macOS | `pbcopy` / `pbpaste` |
| Windows | Win32 clipboard API |
| Linux X11 | `xclip` หรือ `xsel` |
| Linux Wayland | `wl-copy` / `wl-paste` |

ใช้ `pyperclip` ห่อ ตรวจตอน `spider init` ว่าเครื่องมี backend หรือไม่
ถ้าไม่มีแจ้งทันทีว่าต้องติดตั้งอะไร ไม่รอไปพังตอนใช้จริง

**ข้อจำกัดที่ยอมรับ:** `copy` / `paste` รองรับข้อความเท่านั้น
ภาพใน clipboard ไม่รองรับใน v1 เพราะแต่ละ OS จัดการภาพคนละแบบสิ้นเชิง
ภาพให้ใช้ `spider push shot.png` หรือลากใส่หน้าเว็บ (เบราเซอร์รองรับภาพจาก clipboard ได้)

### Config

`~/.config/spider/config.toml` (Mac/Linux) หรือ `%APPDATA%\spider\config.toml` (Windows)

```toml
server = "http://100.95.121.2:8181"
token  = "..."
device = "parks-macbook-air"
```

ไฟล์นี้เก็บ token แบบข้อความธรรมดา จึงต้องตั้งสิทธิ์ `0600` ตอนสร้าง
และ CLI ตรวจทุกครั้งที่รัน ถ้าเจอสิทธิ์กว้างกว่านั้นจะเตือน

ไม่เก็บลง keyring ของ OS เพราะพังบนเครื่องที่ SSH เข้าไปแบบไม่มี desktop session
ซึ่งเป็นเคสที่ต้องใช้บ่อย ตั้ง env `SPIDER_TOKEN` แทนได้ และถ้าตั้ง จะชนะค่าในไฟล์เสมอ

### Output

`ls` ใช้ตาราง `rich` เมื่อ stdout เป็น TTY:

```
ID       KIND  NAME              SIZE    FROM               WHEN
01JD3K7  text  api-notes         2.1 KB  parks-macbook-air  2 นาทีที่แล้ว
01JD3H2  file  report.pdf        4.2 MB  archlinux          1 ชม.ที่แล้ว
```

ถ้าไม่ใช่ TTY (ต่อ pipe หรือใน script) พ่นเป็น TSV เปล่า ไม่มีสี ไม่มีเส้นตาราง
เพื่อให้ `spider ls | grep pdf | awk '{print $1}'` ใช้ได้จริง

---

## 8. Web UI

หน้าเดียว ไม่มี build step ไม่มี `node_modules` — HTML + CSS + vanilla JS
เสิร์ฟจาก `server/static/`

```
+------------------------------------------+
|  spider                       [ค้นหา...]  |
+------------------------------------------+
|  +------------------------------------+  |
|  |  วางข้อความที่นี่...                  |  |
|  |                            [ส่ง]    |  |
|  +------------------------------------+  |
|      ลากไฟล์มาวางตรงไหนก็ได้ หรือ [เลือกไฟล์]  |
+------------------------------------------+
|  [T] api-notes           2.1 KB  2 นาที   |
|      "ข้อความ 200 ตัวแรก..."  [คัดลอก] [ลบ] |
+------------------------------------------+
|  [F] report.pdf          4.2 MB  1 ชม.    |
|                        [เปิด] [โหลด] [ลบ]  |
+------------------------------------------+
```

**พฤติกรรม:**

- เข้าครั้งแรกแสดงหน้ากรอก token ครั้งเดียว หลังจากนั้นจำผ่าน cookie
- ลากไฟล์มาวางตรงไหนของหน้าก็ได้ = อัปโหลด มีแถบ progress (`XHR.upload.onprogress`)
- กด Cmd/Ctrl+V บนหน้าเว็บ = แปะข้อความหรือภาพจาก clipboard แล้วอัปโหลดเลย
- ข้อความ: กดที่แถวเพื่อกางดูเต็ม ปุ่มคัดลอกใช้ `navigator.clipboard`
- refresh รายการทุก 10 วินาที และทุกครั้งที่หน้าต่างกลับมาโฟกัส ไม่ใช้ WebSocket
- responsive ใช้จากมือถือในบ้านได้

**การเปิดไฟล์ดูในเบราเซอร์:** ปุ่มเปิดมีเฉพาะชนิดไฟล์ที่ปลอดภัย

ไฟล์ทั้งหมดถูกเสิร์ฟจาก origin เดียวกับตัวแอป ถ้าเปิดไฟล์ HTML หรือ SVG แบบ inline
ให้เบราเซอร์รัน สคริปต์ในไฟล์นั้นจะทำงานภายใต้ origin ของ spider
และเข้าถึงทุกอย่างในหน้าได้เหมือนโค้ดของแอปเอง

การป้องกัน:

- `/api/items/{id}/content` ตอบ `Content-Disposition: attachment` เป็นค่าเริ่มต้นเสมอ
- เปลี่ยนเป็น `inline` ได้เฉพาะเมื่อ content-type อยู่ใน whitelist:
  `image/*` (ยกเว้น `image/svg+xml`), `application/pdf`, `text/plain`
- แนบ `X-Content-Type-Options: nosniff` ทุก response
- ตั้ง `Content-Security-Policy` ที่หน้าหลัก ไม่อนุญาต inline script

---

## 9. Deployment

### Dockerfile

`python:3.14-slim` สองสเตจ builder ติดตั้ง dependency ด้วย `uv`
แล้ว copy เฉพาะ venv เข้า runtime image

```dockerfile
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra server

FROM python:3.14-slim
RUN useradd -r -u 1000 spider
COPY --from=builder /app/.venv /app/.venv
COPY src/ /app/src/
USER spider
VOLUME /data
EXPOSE 8181
HEALTHCHECK --interval=30s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8181/healthz')"
CMD ["/app/.venv/bin/uvicorn", "spider.server.app:app", "--host", "0.0.0.0", "--port", "8181"]
```

**ไม่ใช้ gunicorn** ผู้ใช้คนเดียว uvicorn ตัวเดียวพอ
worker หลายตัวจะทำให้ SQLite เขียนชนกันโดยไม่ได้ประโยชน์กลับมา

**รันเป็น non-root** (`USER spider`) ถ้ามีช่องโหว่ในโค้ด
ผู้ที่เข้ามาได้จะไม่ได้สิทธิ์ root ในคอนเทนเนอร์ ซึ่งเป็นก้าวแรกของการหลุดออกไปที่ host

build เฉพาะ `linux/amd64` เครื่องปลายทางเป็น x86_64 ไม่ต้องทำ multi-arch

### compose.yml

```yaml
services:
  spider:
    build: .
    restart: unless-stopped
    ports:
      - "${SPIDER_BIND_IP:?ต้องระบุ IP ของ Tailscale}:8181:8181"
    volumes:
      - ${SPIDER_HOST_DATA_DIR:?ต้องระบุ path บนเครื่อง host}:/data
    environment:
      SPIDER_TOKEN: ${SPIDER_TOKEN:?ต้องตั้ง token}
      SPIDER_DATA_DIR: /data
      SPIDER_MIN_FREE_GB: ${SPIDER_MIN_FREE_GB:-10}
    user: "${SPIDER_UID:-1000}:${SPIDER_GID:-1000}"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8181/healthz')"]
      interval: 30s
```

สามตัวแปรที่ใส่ `:?` ไว้ ทำให้ compose ปฏิเสธการสตาร์ทพร้อมบอกว่าขาดอะไร:

- `SPIDER_BIND_IP` บังคับให้ระบุ interface ไม่ให้เผลอเปิดทุก interface
- `SPIDER_TOKEN` ไม่มีทางรันแบบไม่มีรหัสผ่าน
- `SPIDER_HOST_DATA_DIR` ไม่ให้เผลอเก็บลง anonymous volume แล้วหายตอน `docker compose down -v`

**ใช้ bind mount ไม่ใช้ named volume** ข้อมูลต้องอยู่บนดิสก์ที่มองเห็น
ชี้ backup ไปได้ และกู้ได้แม้ Docker พัง
`user:` ตั้ง UID ตรงกับ `park` (1000:1000) ไฟล์ที่เขียนออกมาจะไม่กลายเป็นของ root

### ค่าที่ใช้จริงบน nas-su

```env
SPIDER_BIND_IP=100.95.121.2
SPIDER_HOST_DATA_DIR=/mnt/nas/.spider
SPIDER_TOKEN=<openssl rand -base64 32>
SPIDER_UID=1000
SPIDER_GID=1000
SPIDER_MIN_FREE_GB=10
```

### ขั้นตอนติดตั้ง

```bash
git clone https://github.com/Weerapong-gui/spider.git
cd spider
cp .env.example .env

openssl rand -base64 32          # ใส่ใน SPIDER_TOKEN
tailscale ip -4                  # ใส่ใน SPIDER_BIND_IP

docker compose up -d
docker compose logs -f
```

`.env` อยู่ใน `.gitignore` ตั้งแต่ commit แรก
`.env.example` มีแต่ชื่อตัวแปรกับคำอธิบาย ไม่มีค่าจริง

หลังติดตั้ง แนะนำเพิ่ม `veto files = /.spider/` ในแชร์ Samba `[NAS]`
เพื่อกันการลบ blob โดยไม่ตั้งใจจาก Finder

### ติดตั้ง client

```bash
uv tool install git+https://github.com/Weerapong-gui/spider.git
# หรือ
pipx install git+https://github.com/Weerapong-gui/spider.git

spider init
```

ชื่อ `spider` บน PyPI ยังว่าง ถ้าวันหน้าต้องการเผยแพร่ใช้ชื่อนี้ได้

### CI

- push / PR: `ruff check`, `ruff format --check`, `pytest`
- ทดสอบบน Python 3.11 และ 3.14
- tag `v*`: build image ขึ้น `ghcr.io` (ทำที่ M7 ไม่ใช่ M1)

---

## 10. Testing

เขียนแบบ TDD เขียน test ก่อน implementation ทุกไฟล์

### test_roundtrip.py — เทสต์ที่สำคัญที่สุด

พิสูจน์ข้อบังคับหลักของโปรเจ็ค: ไฟล์ต้องครบทุก byte

```python
@given(payload=st.binary(min_size=0, max_size=10_000_000))
def test_bytes_survive_roundtrip(payload):
    item = push(payload)
    assert pull(item.id) == payload
    assert item.sha256 == sha256(payload).hexdigest()
```

เคสเจาะจงที่ต้องมี: ไฟล์ 0 byte, ไฟล์ 1 byte, null bytes กลางไฟล์,
ข้อความไทย + emoji + CJK, CRLF ที่ห้ามกลายเป็น LF, ชื่อไฟล์ภาษาไทย,
ชื่อไฟล์ที่มีช่องว่างและ emoji

### test_storage.py

ทดสอบกับ `tmp_path` จริง ไม่ mock filesystem

- ระหว่างอัปโหลดยังไม่จบ ต้องยังไม่มีไฟล์ในตำแหน่งจริง มีแต่ใน `tmp/`
- อัปโหลดล้มกลางคัน ไม่มีแถวใน DB และ temp ถูกลบ
- GC: `.part` เก่ากว่า 1 ชม. ถูกเก็บกวาด อันที่ใหม่กว่าไม่ถูกแตะ
- ยืนยันว่า stream จริง: นับจำนวนครั้งที่ `.write()` ถูกเรียกตอนส่งไฟล์ 50 MB
  ต้องมากกว่า 1 ครั้ง (ถ้าโหลดทั้งก้อนเข้า RAM จะเป็น 1)
- disk guard: mock พื้นที่ว่างให้ต่ำกว่า `SPIDER_MIN_FREE_GB` แล้วอัปโหลดต้องถูกปฏิเสธ
- `verify`: ลบ blob ทิ้งโดยไม่ลบแถว DB แล้ว `verify` ต้องรายงาน item นั้น

### test_api.py

ใช้ FastAPI `TestClient`

- ไม่มี token = 401 ทุก endpoint ยกเว้น `/healthz`
- token ผิด = 401
- server ปฏิเสธสตาร์ทเมื่อ `SPIDER_TOKEN` ว่างหรือสั้นกว่า 32 ตัวอักษร
- prefix ID: เจอตัวเดียว 200, ซ้ำ 409, ไม่เจอ 404, `latest` ได้ตัวล่าสุด
- cursor pagination: แทรกของใหม่ระหว่างเลื่อนหน้า ต้องไม่เห็นซ้ำและไม่ข้าม
- `/content` ตอบ `attachment` เป็นค่าเริ่มต้น และปฏิเสธ `inline`
  กับ `text/html` และ `image/svg+xml`
- session cookie ที่ตั้งจาก `/api/session` ต้องมี `HttpOnly` และ `SameSite=Strict`

### test_cli.py

ใช้ typer `CliRunner` + `httpx.ASGITransport` ชี้ตรงเข้า FastAPI app

CLI คุยกับ server จริงในโปรเซสเดียวกัน ไม่ต้อง mock HTTP ไม่ต้องเปิด port
ถ้า schema สองฝั่งไม่ตรงกันเมื่อไหร่ test แดงทันที ซึ่งเป็นเหตุผลทั้งหมดที่เลือก monorepo

- exit code ตรงตามตารางในหัวข้อ 6
- server ล่ม: ข้อความอ่านรู้เรื่อง exit 8 ไม่มี traceback
- `ls` ต่อ pipe: TSV เปล่า ไม่มี escape code
- clipboard mock ผ่าน `pyperclip`: `copy` แล้ว `paste` ได้ข้อความเดิม
- config file สิทธิ์กว้างเกินไป: CLI เตือน

### ไม่ทดสอบอัตโนมัติ (checklist ลองมือใน README)

- Mac -> PC จริงผ่าน Tailscale
- ไฟล์ขนาด 1 GB
- ลากไฟล์ใส่เบราเซอร์
- เปิดจากมือถือในบ้าน

---

## 11. ลำดับการสร้าง

แต่ละขั้นจบแล้วใช้งานได้จริง ไม่ใช่ครึ่งๆ กลางๆ

| # | ได้อะไร | จบแล้วทำอะไรได้ |
|---|---|---|
| M1 | `core/` + `storage.py` + tests | เก็บและอ่าน item ได้ พิสูจน์ว่า byte ครบ |
| M2 | server + auth + disk guard + tests | `curl` push/pull ได้ |
| M3 | CLI `init/push/pull/ls/cat/rm/verify` | ใช้งานจริงได้ผ่าน terminal |
| M4 | `copy` / `paste` + clipboard | **เคสหลักทำงาน** |
| M5 | Web UI | เปิดจากเบราเซอร์ได้ |
| M6 | Docker + compose + deploy บน nas-su | รันถาวร |
| M7 | README + CI | ส่งมอบ |

M4 คือจุดที่ระบบเริ่มมีประโยชน์จริง

---

## 12. ไม่อยู่ในขอบเขต v1

ตัดออกอย่างตั้งใจ ไม่ใช่ลืม

- auto-sync / sync daemon / conflict resolution
- version history
- ระบบ user หลายคน / permission / การแชร์ระหว่างผู้ใช้
- dedup แบบ content-addressed
- ภาพจาก clipboard ฝั่ง CLI (ฝั่งเบราเซอร์ทำได้)
- โฟลเดอร์ซ้อนกัน (v1 เป็น list แบน + ค้นหา)
- การเปิดออกอินเทอร์เน็ต / HTTPS cert / reverse proxy
- rate limiting
- desktop app / tray icon / global hotkey
- mount เป็น network drive (WebDAV)

---

## 13. สรุปเหตุผลของการตัดสินใจสำคัญ

| ตัดสินใจ | เหตุผล |
|---|---|
| Monorepo | `core/models.py` เป็น schema เดียว กัน bug schema drift ระหว่าง client กับ server |
| item เดียวรวม text กับ file | list เดียว API เดียว หน้าจอเดียว code path เดียว |
| ไม่ทำ dedup | ต้องนับ reference ตอนลบ นับพลาด = ข้อมูลหาย ไม่คุ้มสำหรับผู้ใช้คนเดียว |
| text เก็บเป็น blob | code path เดียวกับไฟล์ streaming เสมอ |
| multipart อย่างเดียว | กันการมี code path ที่ไม่ streaming แล้วกิน RAM ตอน paste ก้อนใหญ่ |
| cursor ไม่ใช่ offset | ของใหม่ push เข้ามาระหว่างเลื่อนหน้า ทำให้ offset เพี้ยน |
| แก้ prefix ID ที่ server | CLI ไม่ต้องดึง list มา match เอง Web UI ได้ฟรี |
| token เดียว ไม่มีตาราง user | ผู้ใช้คนเดียวหลัง Tailscale ซึ่งจัดการ identity ระดับเครือข่ายแล้ว |
| cookie `HttpOnly` แทน `localStorage` | ต่อให้มี XSS token ก็ไม่ถูกขโมย |
| bind กับ Tailscale IP | เครื่องนี้มี cloudflared tunnel อยู่แล้ว ต้องไม่เผลอเปิดออก |
| disk guard 10 GB | มี 16 container อื่นใช้ดิสก์เดียวกัน ดิสก์เต็ม = ข้อมูลระบบอื่นเสียหาย |
| ข้อมูลที่ `/mnt/nas/.spider/` | ดิสก์ข้อมูลโดยเฉพาะ ซ่อนจาก Samba ด้วยจุดนำหน้า + `veto files` |
| uvicorn ตัวเดียว ไม่ใช้ gunicorn | หลาย worker ทำให้ SQLite เขียนชนกันโดยไม่ได้อะไร |
| Web UI vanilla JS | 3 หน้าจอ ไม่คุ้มกับการลาก Node toolchain เข้าโปรเจ็ค Python |
