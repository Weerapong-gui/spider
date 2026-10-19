# Devices, Send-to-Device, File History and Previews — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** spider-gui shows which machines are running it, sends an item to a chosen machine which then pops a notification (click → file into Downloads / text onto the clipboard), shows each item's upload/download history, and shows 48px previews; the CLI can send with `--to` and list devices.

**Architecture:** The server gains two tables (`devices`, `downloads`) and two `items` columns (`to_device`, `notified_at`) behind a `PRAGMA user_version` migration, plus a lazily built thumbnail cache beside `blobs/`. Clients identify themselves with two headers on every request; the GUI heartbeats and polls an inbox on its existing 10-second timer. All non-Qt logic lives in `gui/actions.py` so it is testable without a display.

**Tech Stack:** Python ≥3.11, FastAPI, SQLite, httpx, Typer, PySide6-Essentials, Pillow (new, server only).

**Spec:** `docs/superpowers/specs/2026-10-19-devices-history-preview-design.md`

## Global Constraints

- Original blobs under `blobs/` are never modified; thumbnails live in `<data_dir>/thumbs/`.
- A file is never overwritten in Downloads; a clash becomes `name (1).ext`, `name (2).ext`, …
- Nothing lands on the receiving machine unless the user clicks.
- A notification for an item is shown at most once (server-side `notified_at`).
- Online = `heartbeat_at` within **30 seconds** of server time. GUI heartbeat/poll interval stays `REFRESH_MS = 10_000`.
- Headers on every client request: `X-Spider-Device: <percent-encoded name>` (`urllib.parse.quote(name, safe="")`, so Thai names survive latin-1 headers) and `X-Spider-Client: gui|cli`.
- A request is from a browser iff it authenticated with the session cookie, not a Bearer token.
- Endpoints needing the caller's device (heartbeat, inbox, ack, downloads) without `X-Spider-Device` → `400 bad_request`.
- `to_device` unknown → `400` listing known device names; `to_device` equal to the sender → `400`.
- Thumbnails: PNG, longest side **96px**; source types `image/jpeg`, `image/png`, `image/gif`, `image/webp`, `image/bmp`; source ≤ **50 MB** and ≤ **50,000,000** pixels; EXIF orientation applied; failure writes an empty marker and later answers 404 without retrying.
- Notification text: files from the same sender in one poll are grouped; text items are never grouped.
- GUI copy stays in English (the Thai in the spec describes meaning).
- Every new endpoint sits behind the existing auth and uses the existing error shape.
- Old server + new GUI: 404 from new endpoints hides the device sidebar and history panel and falls back to icons, with no repeating error.
- Ruff line length 100; CI runs `ruff check`, `ruff format --check`, `pytest -m "not slow"`.

## Review Focus

1. **A Thai device name** (`เครื่องบ้าน`) must round-trip through the headers, the device list, `--to`, the inbox and history. → Task 4 `test_thai_device_name_roundtrips`.
2. **`~/Downloads` does not exist** (fresh Linux user): receiving must create it, not fail. → Task 8 `test_receive_creates_the_downloads_folder`.
3. **Item deleted between notification and click**: clicking reports "no longer on the server" and nothing crashes. → Task 8 `test_receive_of_a_deleted_item_raises_not_found`.
4. **A decompression-bomb or truncated image** must give 404 for the thumbnail, never a 500 or a hung worker. → Task 3 `test_thumbnail_of_a_pixel_bomb_is_none`, `test_thumbnail_of_a_truncated_jpeg_is_none_and_not_retried`, Task 4 `test_thumbnail_endpoint_returns_png_or_404`.
5. **History reporting fails after a successful save** (network blip): the save still counts as success and the user gets only a warning. → Task 8 `test_a_failed_report_does_not_fail_the_save`, Task 6 `test_pull_warns_when_the_report_fails`.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/spider/core/models.py` | modify | `Item.to_device`, `ClientKind`, `Device`, `Download`, `ItemHistory`, header names + encode/decode |
| `src/spider/server/storage.py` | modify | migration, devices, inbox/ack, downloads/history, thumbnail cache paths |
| `src/spider/server/thumbnails.py` | create | Pillow rendering with limits — the only module importing PIL |
| `src/spider/server/auth.py` | modify | record auth method on `request.state` |
| `src/spider/server/routes.py` | modify | caller identity, new endpoints, browser download logging |
| `src/spider/server/static/app.js` | modify | Copy button adds `purpose=clipboard` |
| `src/spider/cli/client.py` | modify | headers, `to_device`, new endpoint methods |
| `src/spider/cli/main.py` | modify | `--to`, `devices`, `ls` arrow, download reporting |
| `src/spider/gui/actions.py` | modify | unique paths, receive, poll, grouping, icon choice |
| `src/spider/gui/icons/` | create | Material Icon Theme SVG subset + `LICENSE` |
| `src/spider/gui/window.py` | modify | splitter layout, sidebar, preview column, history panel, notifications |
| `src/spider/gui/app.py` | modify | build GUI clients with `ClientKind.gui` |
| `pyproject.toml` | modify | Pillow in `server`, `spider.gui` package-data |
| `README.md` | modify | new commands, desktop features, manual checks |

---

### Task 1: Models and schema migration

**Files:**
- Modify: `src/spider/core/models.py`, `src/spider/server/storage.py`
- Test: `tests/test_models.py`, `tests/test_storage.py`

**Interfaces:**
- Produces:
  - `Item.to_device: str | None = None`
  - `class ClientKind(StrEnum): gui, cli, browser`
  - `class Device(BaseModel): name: str; client: ClientKind; last_seen: datetime; online: bool`
  - `class Download(BaseModel): device: str; client: ClientKind; destination: str; at: datetime`
  - `class ItemHistory(BaseModel): item: Item; downloads: list[Download]`
  - `DEVICE_HEADER = "X-Spider-Device"`, `CLIENT_HEADER = "X-Spider-Client"`, `BROWSER_DEVICE = "browser"`
  - `encode_device(name: str) -> str`, `decode_device(value: str) -> str` (quote/unquote, `safe=""`)
  - `storage.SCHEMA_VERSION = 1`; `Storage.init()` migrates; `Storage.save(..., to_device: str | None = None)`; `_row_to_item` fills `to_device`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_models.py
def test_device_header_roundtrips_thai():
    assert decode_device(encode_device("เครื่องบ้าน")) == "เครื่องบ้าน"
    assert encode_device("เครื่องบ้าน").isascii()

def test_item_to_device_defaults_to_none():
    assert Item(**SAMPLE_ITEM_FIELDS).to_device is None   # SAMPLE: existing fields only

# tests/test_storage.py
def test_migration_keeps_existing_rows(tmp_path):
    # build a db with the 2026-08-19 schema (copy the old CREATE TABLE verbatim), insert one row,
    # then Storage(...).init()
    assert store.get(old_id).name == "old.txt"
    assert store.get(old_id).to_device is None
    assert user_version(store) == 1
    assert {"devices", "downloads"} <= table_names(store)

def test_init_twice_after_migration_is_a_no_op(store):
    store.init(); store.init()
    assert user_version(store) == 1

def test_save_stores_to_device(store):
    item = store.save(stream=[b"x"], kind=ItemKind.file, name="a", content_type="x/y",
                      source_device="mac", to_device="pc")
    assert store.get(item.id).to_device == "pc"
```

Update `test_schema_has_the_expected_columns` to include `to_device` and `notified_at`.

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_models.py tests/test_storage.py -q`
Expected: FAIL (ImportError on `encode_device`, missing columns)

- [ ] **Step 3: Implement**

Models as in Interfaces. In `storage.py` add a `_MIGRATIONS: list[str]` whose entry 0 is the spec §8 SQL (`ALTER TABLE items ADD COLUMN to_device TEXT`, `ADD COLUMN notified_at TEXT`, `idx_items_inbox`, `devices`, `downloads`, `idx_downloads_item`). `init()` runs `_SCHEMA`, then each migration with index ≥ `PRAGMA user_version` inside one transaction (`BEGIN` … `PRAGMA user_version = n` … `COMMIT`; use `isolation_level=None` for that connection so the PRAGMA is inside the transaction). `save()` writes `to_device`.

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_models.py tests/test_storage.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/spider/core/models.py src/spider/server/storage.py tests/test_models.py tests/test_storage.py
git commit -m "feat: add device, download and target-device storage with a schema migration"
```

---

### Task 2: Storage — devices, inbox, downloads, history

**Files:**
- Modify: `src/spider/server/storage.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: Task 1 models.
- Produces (every `now` defaults to `datetime.now(UTC)`; tests inject it):
  - `ONLINE_WINDOW = timedelta(seconds=30)`
  - `touch_device(name: str, client: ClientKind, *, now=None) -> None` — upsert `client`, `last_seen`
  - `heartbeat(name: str, *, now=None) -> None` — touch as `gui` and set `heartbeat_at`
  - `clear_heartbeat(name: str) -> None`
  - `list_devices(*, now=None) -> list[Device]` — ordered `last_seen DESC`; `online = heartbeat_at >= now - ONLINE_WINDOW`
  - `device_names() -> list[str]`
  - `remove_device(name: str) -> None` — `not_found` if absent
  - `inbox(device: str) -> list[Item]` — `to_device = device AND notified_at IS NULL`, oldest first
  - `ack(item_id: str, device: str, *, now=None) -> None` — `not_found` unless the item exists and `to_device == device`
  - `record_download(item_id: str, device: str, client: ClientKind, destination: str, *, now=None) -> None`
  - `history(item_id: str) -> ItemHistory` — downloads oldest first

- [ ] **Step 1: Write the failing tests**

```python
def test_device_is_online_within_30_seconds_of_a_heartbeat(store):
    t0 = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    store.heartbeat("pc", now=t0)
    assert store.list_devices(now=t0 + timedelta(seconds=30))[0].online is True
    assert store.list_devices(now=t0 + timedelta(seconds=31))[0].online is False

def test_cli_touch_is_never_online(store):
    store.touch_device("linux", ClientKind.cli)
    [device] = store.list_devices()
    assert device.client is ClientKind.cli and device.online is False

def test_clear_heartbeat_goes_offline_immediately(store):
    store.heartbeat("pc"); store.clear_heartbeat("pc")
    assert store.list_devices()[0].online is False

def test_remove_device_keeps_history(store):
    item = save(store, to_device=None); store.touch_device("pc", ClientKind.cli)
    store.record_download(item.id, "pc", ClientKind.cli, "/tmp/a")
    store.remove_device("pc")
    assert store.history(item.id).downloads[0].device == "pc"

def test_inbox_returns_only_unacknowledged_items_for_that_device(store):
    a = save(store, to_device="pc"); save(store, to_device="mac"); save(store)
    assert [i.id for i in store.inbox("pc")] == [a.id]
    store.ack(a.id, "pc")
    assert store.inbox("pc") == []

def test_ack_by_another_device_is_not_found(store):
    a = save(store, to_device="pc")
    with pytest.raises(SpiderError) as exc: store.ack(a.id, "mac")
    assert exc.value.code is ErrorCode.not_found

def test_deleting_an_item_deletes_its_history(store):
    item = save(store); store.record_download(item.id, "pc", ClientKind.gui, "/x")
    store.delete(item.id)
    assert count_rows(store, "downloads") == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_storage.py -q`
Expected: FAIL (`AttributeError: 'Storage' object has no attribute 'heartbeat'`)

- [ ] **Step 3: Implement the methods listed in Interfaces**

Store times as ISO strings like `created_at`. Upsert with `INSERT … ON CONFLICT(name) DO UPDATE`.

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_storage.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/spider/server/storage.py tests/test_storage.py
git commit -m "feat: track devices, inbox acknowledgements and download history"
```

---

### Task 3: Thumbnails (storage + rendering)

**Files:**
- Create: `src/spider/server/thumbnails.py`
- Modify: `src/spider/server/storage.py`, `pyproject.toml` (`"pillow>=10.4,<13.0"` in `server`; CI and the Docker image install `server`, so nothing else changes)
- Test: `tests/test_thumbnails.py`

**Interfaces:**
- Produces:
  - `thumbnails.THUMB_SIZE = 96`, `MAX_SOURCE_BYTES = 50 * 1024**2`, `MAX_PIXELS = 50_000_000`, `SUPPORTED_TYPES: frozenset[str]`
  - `thumbnails.render(source: Path, target: Path) -> bool` — writes PNG to `target` and returns True, or returns False on any failure
  - `Storage.thumbs_dir = data_dir / "thumbs"`, `Storage.thumb_path(item_id) -> Path` (`thumbs/<2>/<2>/<id>.png`)
  - `Storage.thumbnail(item_id: str) -> Path | None` — None for unsupported type, missing blob, or failure; on failure writes a zero-byte marker at `thumb_path` and returns None on later calls without rendering
  - `Storage.delete` also unlinks the thumbnail; `Storage.gc` removes thumbnails whose id has no row (same age rule as blobs)

- [ ] **Step 1: Write the failing tests** (build images with Pillow in the test)

```python
def test_png_thumbnail_is_a_png_no_larger_than_96(store): ...   # 800x400 source → size (96, 48)
def test_thumbnail_does_not_touch_the_blob(store):
    before = sha256(store.blob_path(item.id).read_bytes())
    store.thumbnail(item.id)
    assert sha256(store.blob_path(item.id).read_bytes()) == before == item.sha256
def test_exif_orientation_is_applied(store): ...    # 100x50 JPEG with Orientation=6 → thumb is portrait
def test_gif_uses_the_first_frame(store): ...
def test_pdf_has_no_thumbnail(store): assert store.thumbnail(pdf.id) is None
def test_thumbnail_of_a_truncated_jpeg_is_none_and_not_retried(store, monkeypatch):
    assert store.thumbnail(bad.id) is None
    monkeypatch.setattr(thumbnails, "render", boom)   # would raise if called
    assert store.thumbnail(bad.id) is None
def test_thumbnail_of_a_pixel_bomb_is_none(store): ...   # PNG header claiming 10000x10000
def test_source_over_50_mb_is_not_rendered(store, monkeypatch): ...   # patch MAX_SOURCE_BYTES small
def test_delete_and_gc_remove_thumbnails(store): ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_thumbnails.py -q`
Expected: FAIL (`ModuleNotFoundError: spider.server.thumbnails`)

- [ ] **Step 3: Implement**

`render`: set `Image.MAX_IMAGE_PIXELS = MAX_PIXELS` and turn `DecompressionBombWarning` into an error (`warnings.simplefilter("error", Image.DecompressionBombWarning)` inside a `warnings.catch_warnings()`); `Image.open` → `ImageOps.exif_transpose` → `seek(0)` → `convert("RGBA")` → `thumbnail((96, 96))` → save PNG to `target.with_suffix(".tmp")` then `replace`. Catch `Exception` → False. `Storage.thumbnail` checks content type base against `SUPPORTED_TYPES` and `item.size <= MAX_SOURCE_BYTES` before calling `render`.

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_thumbnails.py tests/test_storage.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/spider/server/thumbnails.py src/spider/server/storage.py pyproject.toml tests/test_thumbnails.py
git commit -m "feat: build cached 96px thumbnails for images beside the blobs"
```

---

### Task 4: HTTP API

**Files:**
- Modify: `src/spider/server/auth.py`, `src/spider/server/routes.py`, `src/spider/server/static/app.js:143`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `require_auth` sets `request.state.auth_method = "bearer" | "cookie"`
  - `@dataclass(frozen=True) class Caller: device: str | None; client: ClientKind` and a dependency `caller(request) -> Caller` in `routes.py`: cookie → `Caller(None, browser)`; bearer → device from decoded `X-Spider-Device` (None if absent), client from `X-Spider-Client` (default `cli`, unknown → `cli`); when a device is present it calls `storage.touch_device`. Applied to every authenticated route (router-level dependency storing on `request.state.caller`).
  - Endpoints (spec §8): `POST/DELETE /api/devices/heartbeat` (204), `GET /api/devices` → `list[Device]`, `DELETE /api/devices/{name}` (204), `GET /api/inbox` → `list[Item]`, `POST /api/inbox/{ref}/ack` (204), `GET /api/items/{ref}/history` → `ItemHistory`, `POST /api/items/{ref}/downloads` body `{"destination": str}` (204), `GET /api/items/{ref}/thumbnail` → `FileResponse(media_type="image/png")` or 404
  - `POST /api/items` accepts form `to_device`
  - `GET /api/items/{ref}/content` accepts `purpose: str | None`; browser caller + `disposition=attachment` → `record_download(..., BROWSER_DEVICE, browser, request.client.host)`; browser + `purpose=clipboard` → destination `f"clipboard ({ip})"`; inline → nothing; bearer → nothing

- [ ] **Step 1: Write the failing tests**

```python
H = {**AUTH, "X-Spider-Device": encode_device("mac"), "X-Spider-Client": "gui"}

def test_any_request_with_a_device_header_registers_the_device(client): ...
def test_thai_device_name_roundtrips(client):
    hdr = {**AUTH, "X-Spider-Device": encode_device("เครื่องบ้าน"), "X-Spider-Client": "gui"}
    client.post("/api/devices/heartbeat", headers=hdr)
    item = upload(client, b"x", headers=H, to_device="เครื่องบ้าน")
    assert [i["id"] for i in client.get("/api/inbox", headers=hdr).json()] == [item["id"]]
    assert any(d["name"] == "เครื่องบ้าน" and d["online"] for d in client.get("/api/devices", headers=AUTH).json())
def test_heartbeat_then_delete_goes_offline(client): ...
def test_heartbeat_without_device_header_is_400(client): ...
def test_upload_to_an_unknown_device_is_400_and_names_known_devices(client):
    r = upload(client, b"x", headers=H, to_device="nope")
    assert r.status_code == 400 and "mac" in r.json()["error"]["message"]
def test_upload_to_yourself_is_400(client): ...
def test_inbox_ack_hides_the_item(client): ...
def test_ack_from_another_device_is_404(client): ...
def test_history_lists_reported_downloads(client): ...   # POST /downloads then GET /history
def test_report_download_without_device_is_400(client): ...
def test_browser_attachment_is_recorded_with_ip(client): ...   # session cookie, default disposition
def test_browser_inline_is_not_recorded(client): ...
def test_browser_copy_is_recorded_as_clipboard(client): ...    # ?purpose=clipboard
def test_bearer_download_is_never_recorded_as_browser(client):  # with and without headers
    ...
    assert client.get(f"/api/items/{id}/history", headers=AUTH).json()["downloads"] == []
def test_thumbnail_endpoint_returns_png_or_404(client): ...
def test_remove_device_is_204_then_404(client): ...
```

`upload` helper gains `headers=` and passes extra fields through (it already takes `**fields`).

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_api.py -q`
Expected: FAIL (404 on `/api/devices/heartbeat`)

- [ ] **Step 3: Implement** as listed in Interfaces. Literal routes (`/devices/heartbeat`, `/inbox`) are declared before parameterised ones, following the existing comment on `/verify`. In `app.js:143` change the URL to `` `/api/items/${item.id}/content?purpose=clipboard` ``.

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/spider/server tests/test_api.py
git commit -m "feat: expose devices, inbox, history and thumbnails over the API"
```

---

### Task 5: Client methods

**Files:**
- Modify: `src/spider/cli/client.py`
- Test: `tests/test_cli.py` (client section; uses the existing in-process `api` fixture)

**Interfaces:**
- Consumes: Task 4 endpoints.
- Produces on `SpiderClient`:
  - `__init__(self, config, transport=None, timeout=30.0, client_kind: ClientKind = ClientKind.cli)` — default headers add `X-Spider-Device: encode_device(config.device)` and `X-Spider-Client: client_kind.value`
  - `push_bytes(..., to_device: str | None = None)`, `push_file(..., to_device: str | None = None)` — send the form field only when set
  - `devices() -> list[Device]`, `heartbeat() -> None`, `stop_heartbeat() -> None`, `remove_device(name: str) -> None`
  - `inbox() -> list[Item]`, `ack(item_id: str) -> None`
  - `report_download(item_id: str, destination: str) -> None`
  - `history(ref: str) -> ItemHistory`
  - `thumbnail(item_id: str) -> bytes | None` — `None` on `not_found`
  - `def supports_devices(self) -> bool` — `GET /api/devices`; `False` on `not_found` (old server), re-raises other errors

- [ ] **Step 1: Write the failing tests**

```python
def test_client_sends_identity_headers(api, store):
    api.list_items()
    assert [d.name for d in store.list_devices()] == ["test-device"]
def test_push_to_device_lands_in_its_inbox(api, store, config): ...
def test_thumbnail_of_a_text_item_is_none(api): ...
def test_supports_devices_is_false_against_an_old_server(): ...   # MockTransport returning the 404 error shape
def test_report_download_appears_in_history(api): ...
```

- [ ] **Step 2: Run to verify they fail** — `pytest tests/test_cli.py -q` → FAIL (`unexpected keyword 'to_device'`)
- [ ] **Step 3: Implement** the methods in Interfaces.
- [ ] **Step 4: Run to verify they pass** — `pytest tests/test_cli.py -q` → PASS
- [ ] **Step 5: Commit**

```bash
git add src/spider/cli/client.py tests/test_cli.py
git commit -m "feat: identify the client and call the device, inbox and history endpoints"
```

---

### Task 6: CLI commands

**Files:**
- Modify: `src/spider/cli/main.py`, `README.md` (Commands section)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 5.
- Produces:
  - `push --to NAME`, `copy --to NAME` (option name `to`, flag `--to`)
  - `spider devices` — TTY: Rich table `Name | Client | Status | Last seen` (status `online` / `offline`, this device marked `(this device)`); pipe: TSV `name, client, online|offline, last_seen ISO`
  - `ls`: From column shows `source → to_device` when set; TSV appends a 7th column `to_device` (empty when none)
  - `def report_quietly(client: SpiderClient, item_id: str, destination: str) -> None` in `main.py` — calls `report_download`, on `SpiderError` prints `warning could not record this download: <message>` to stderr and returns
  - `pull` reports `str(target.resolve())`; `paste` reports `"clipboard"`; `cat` reports `"stdout"` (warning goes to stderr so stdout stays clean)

- [ ] **Step 1: Write the failing tests**

```python
def test_push_to_sends_to_that_device(...): ...      # `spider push f --to other` → item in other's inbox
def test_copy_to_unknown_device_fails_with_the_known_names(...): ...   # exit code != 0, stderr lists names
def test_devices_pipe_output_is_tsv(...): ...
def test_ls_pipe_has_a_to_device_column(...): ...
def test_pull_reports_the_absolute_path(...): ...     # history destination == str(tmp_path / "f.bin")
def test_cat_reports_stdout_and_keeps_stdout_clean(...): ...
def test_pull_warns_when_the_report_fails(...): ...   # patch SpiderClient.report_download to raise; exit 0, file saved, "warning" in stderr
```

- [ ] **Step 2: Run to verify they fail** — `pytest tests/test_cli.py -q` → FAIL (`No such option: --to`)
- [ ] **Step 3: Implement**, then document `--to` and `spider devices` in README Commands.
- [ ] **Step 4: Run to verify they pass** — `pytest tests/test_cli.py -q` → PASS
- [ ] **Step 5: Commit**

```bash
git add src/spider/cli/main.py README.md tests/test_cli.py
git commit -m "feat: add --to, spider devices, and record CLI downloads"
```

---

### Task 7: File-type icons

**Files:**
- Create: `src/spider/gui/icons/*.svg`, `src/spider/gui/icons/LICENSE`, `src/spider/gui/icons/__init__.py` (empty, so `importlib.resources` finds it)
- Modify: `src/spider/gui/actions.py`, `pyproject.toml` (`"spider.gui" = ["icons/*"]` under package-data)
- Test: `tests/test_gui_actions.py`

**Interfaces:**
- Produces:
  - `actions.ICON_DIR: Path`
  - `actions.icon_name(name: str, content_type: str, kind: ItemKind) -> str` — returns an SVG stem that exists in `ICON_DIR`; extension first (handles `.tar.gz`), then content-type family (`image/`, `audio/`, `video/`, `text/`), text items → `"text"`, unknown → `"file"`

Copy SVGs from Material Icon Theme (MIT, https://github.com/material-extensions/vscode-material-icon-theme/tree/main/icons) for the spec's minimum set: image, pdf, text/markdown, python, javascript, typescript, html, css, json, yaml, shell, zip (zip/tar/gz/7z), audio, video, word, excel, powerpoint, csv, file. Rename to short stems (`python.svg`, `pdf.svg`, …). Copy that repo's LICENSE verbatim into `icons/LICENSE`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("name,ctype,kind,expected", [
    ("a.py", "text/x-python", ItemKind.file, "python"),
    ("b.tar.gz", "application/gzip", ItemKind.file, "zip"),
    ("c.JPG", "image/jpeg", ItemKind.file, "image"),
    ("noext", "audio/mpeg", ItemKind.file, "audio"),
    ("notes", "text/plain; charset=utf-8", ItemKind.text, "text"),
    ("x.weird", "application/octet-stream", ItemKind.file, "file"),
])
def test_icon_name(name, ctype, kind, expected): ...
def test_every_icon_name_has_an_svg(): ...   # every value in the mapping exists in ICON_DIR
def test_icons_ship_with_a_license(): assert (actions.ICON_DIR / "LICENSE").is_file()
```

- [ ] **Step 2: Run to verify they fail** — `pytest tests/test_gui_actions.py -q` → FAIL
- [ ] **Step 3: Implement** and add the SVGs.
- [ ] **Step 4: Run to verify they pass** — `pytest tests/test_gui_actions.py -q` → PASS; also `pip wheel . -w /tmp/w --no-deps && unzip -l /tmp/w/spider-*.whl | grep icons/python.svg` shows the file.
- [ ] **Step 5: Commit**

```bash
git add src/spider/gui/icons src/spider/gui/actions.py pyproject.toml tests/test_gui_actions.py
git commit -m "feat: ship file-type icons for the desktop list"
```

---

### Task 8: GUI actions — receive, poll, notifications

**Files:**
- Modify: `src/spider/gui/actions.py`, `src/spider/gui/app.py`
- Test: `tests/test_gui_actions.py`

**Interfaces:**
- Consumes: Task 5 client.
- Produces:
  - `unique_path(directory: Path, filename: str) -> Path` — `report.pdf` → `report (1).pdf`; `archive.tar.gz` → `archive (1).tar.gz`; `noext` → `noext (1)`; `.bashrc` → `.bashrc (1)`
  - `downloads_dir() -> Path` — `Path.home() / "Downloads"`
  - `@dataclass class Received: item: Item; path: Path | None; text: str | None; warning: str | None`
  - `receive(client, item_id: str, downloads: Path) -> Received` — text → `fetch_text` (sets `text`); file → `mkdir(parents=True, exist_ok=True)`, `unique_path(downloads, safe_filename(item.name))`, `save_item`; then reports (`"clipboard"` or `str(path)`) via `report_quietly` returning the warning instead of printing
  - `report_quietly(client, item_id, destination) -> str | None` — `None` on success, the message on `SpiderError`
  - `save_item(...)` keeps its signature and now also returns after reporting `str(target.resolve())`; it returns `tuple[Item, str | None]` (item, warning) — update the window caller in Task 9
  - `fetch_text(..., report: bool = True)` reports `"clipboard"` when `report`
  - `@dataclass class Notice: title: str; message: str; item_ids: list[str]`
  - `group_notices(items: list[Item]) -> list[Notice]` — files grouped per `source_device` (1 file: `"report.pdf (2.1 MB) — click to save to Downloads"`; n files: `"3 files from park-macbook — click to receive all"`), each text item its own notice (`"click to copy to the clipboard"`); title `f"spider · from {source_device}"`
  - `@dataclass class Poll: devices: list[Device] | None; notices: list[Notice]`
  - `poll(client) -> Poll` — `heartbeat()`, `devices()`, `inbox()`, `ack()` each inbox item, `group_notices`; on `not_found` from heartbeat (old server) returns `Poll(None, [])`
  - `app.Session.make_client` builds `SpiderClient(self.config, client_kind=ClientKind.gui)`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("existing,name,expected", [
    ([], "report.pdf", "report.pdf"),
    (["report.pdf"], "report.pdf", "report (1).pdf"),
    (["report.pdf", "report (1).pdf"], "report.pdf", "report (2).pdf"),
    (["a.tar.gz"], "a.tar.gz", "a (1).tar.gz"),
    (["noext"], "noext", "noext (1)"),
    ([".bashrc"], ".bashrc", ".bashrc (1)"),
])
def test_unique_path(tmp_path, existing, name, expected): ...
def test_receive_saves_into_downloads_without_overwriting(api, tmp_path): ...  # pre-create report.pdf; original bytes unchanged
def test_receive_creates_the_downloads_folder(api, tmp_path): ...             # downloads = tmp_path / "missing"
def test_receive_text_returns_text_and_records_clipboard(api): ...
def test_receive_records_the_saved_path_in_history(api, tmp_path): ...
def test_receive_of_a_deleted_item_raises_not_found(api, tmp_path): ...
def test_a_failed_report_does_not_fail_the_save(api, tmp_path, monkeypatch): ...  # file exists, warning set
def test_group_notices_groups_files_but_not_texts(): ...   # 3 files + 2 texts from mac → 3 notices
def test_poll_acks_so_the_next_poll_is_empty(api, other_api): ...   # other_api sends to api's device
def test_poll_against_an_old_server_returns_no_devices(): ...
```

- [ ] **Step 2: Run to verify they fail** — `pytest tests/test_gui_actions.py -q` → FAIL
- [ ] **Step 3: Implement** as in Interfaces.
- [ ] **Step 4: Run to verify they pass** — `pytest tests/test_gui_actions.py -q` → PASS
- [ ] **Step 5: Commit**

```bash
git add src/spider/gui/actions.py src/spider/gui/app.py tests/test_gui_actions.py
git commit -m "feat: receive items into Downloads and poll the inbox"
```

---

### Task 9: GUI window — sidebar, previews, history panel

**Files:**
- Modify: `src/spider/gui/window.py`
- Test: `tests/test_gui_window.py`

**Interfaces:**
- Consumes: Tasks 7–8.
- Produces on `MainWindow`:
  - `self.devices: QTreeWidget` with top-level groups `"Server"`, `"Running"`, `"Offline"`; children carry the device name in `Qt.UserRole`; this device suffixed `" (this device)"`; offline children show `humanize_age(last_seen)`
  - `self.history: QListWidget` (selectable text) below the table inside a vertical `QSplitter`; sidebar + right side inside a horizontal `QSplitter`
  - Table gains a leading preview column (`COLUMNS = ("", "Name", "Kind", "Size", "From", "When", "ID")`), `verticalHeader().setDefaultSectionSize(52)`, icon size 48
  - `self._thumbs: dict[str, QIcon]` — filled by background `Task`s for rows not yet cached; icon from `actions.icon_name` shown until (or instead of, on `None`) the thumbnail arrives
  - `show_devices(devices: list[Device] | None) -> None` — `None` hides the sidebar
  - `_load_history(item_id)` with its own serial counter like `refresh`; empty selection shows `"Select an item to see its history"`; `not_found` from an old server hides the panel
  - From column renders `f"{source} → {to}"` when `to_device` is set
  - Drop on a device row → `send_paths(paths, to_device=name)`; drop elsewhere unchanged
  - Device context menu: `"Send clipboard to this device"`, `"Send files to this device…"`, and for offline rows `"Remove from list"`
  - `send_clipboard(to_device: str | None = None)`, `send_paths(paths, to_device: str | None = None)`; `actions.send_text/send_files` gain `to_device`; status bar note `"<name> has no desktop app running; it will see this when it opens one."` when the target is not online
  - `refresh()` now also runs `actions.poll` (Task 10 consumes its notices; here only `poll.devices` is used)

- [ ] **Step 1: Write the failing tests** (existing `window` fixture; a second `SpiderClient` with another device name plays the other machine)

```python
def test_sidebar_groups_running_and_offline_devices(window, other): ...
def test_sidebar_is_hidden_against_an_old_server(qapp, old_server_window): ...  # app built without new routes via a MockTransport that 404s them
def test_rows_are_tall_enough_for_a_48px_preview(window): ...
def test_a_png_row_gets_its_thumbnail(window): ...
def test_selecting_a_row_shows_its_history(window): ...
def test_dropping_a_file_on_a_device_sends_it_there(window, other, tmp_path): ...  # other.inbox() has it
def test_from_column_shows_the_target(window, other): ...
```

Update `names(window)` to read column 1.

- [ ] **Step 2: Run to verify they fail** — `QT_QPA_PLATFORM=offscreen pytest tests/test_gui_window.py -q` → FAIL
- [ ] **Step 3: Implement** as in Interfaces.
- [ ] **Step 4: Run to verify they pass** — same command → PASS
- [ ] **Step 5: Commit**

```bash
git add src/spider/gui/window.py src/spider/gui/actions.py tests/test_gui_window.py
git commit -m "feat: add the device sidebar, previews and history panel to the desktop app"
```

---

### Task 10: GUI window — notifications and receiving

**Files:**
- Modify: `src/spider/gui/window.py`, `README.md` (Desktop app section + manual checks)
- Test: `tests/test_gui_window.py`

**Interfaces:**
- Consumes: `actions.poll`, `actions.receive`, `Notice`, `Received` (Task 8).
- Produces on `MainWindow`:
  - `show_notice(notice: Notice) -> None` — always shows (unlike `notify`), via `tray.showMessage` when a tray exists; remembers it as `self._last_notice`; adds it to `self.banner` and to the tray menu as `f"Receive {first item name}"` (keep the newest 5)
  - `self.banner: QWidget` above the table, one row per pending notice: label + `"Save to Downloads"` / `"Copy"` button + `"Dismiss"`; a notice leaves the banner when received or dismissed
  - `tray.messageClicked` → `receive_notice(self._last_notice)`
  - `receive_notice(notice: Notice) -> None` — runs `actions.receive` for each id on a worker; file → notify `f"Saved to {path}"`; text → set clipboard, notify `"Copied “name” to the clipboard"`; `warning` → status bar `f"Saved, but history was not updated: {warning}"`; `not_found` → `"“name” is no longer on the server"`
  - `quit_for_real` fires `stop_heartbeat` on a worker and does not wait for it

- [ ] **Step 1: Write the failing tests**

```python
def test_an_item_sent_here_shows_a_banner_once(window, other):
    other.push_bytes(b"x", kind=ItemKind.file, name="r.pdf", to_device="gui-test")
    window.refresh(); settle(...)
    assert banner_texts(window) == ["r.pdf (1 B) — click to save to Downloads"]   # match group_notices copy
    window.refresh(); settle(...)
    assert len(banner_texts(window)) == 1
def test_banner_save_puts_the_file_in_downloads(window, other, tmp_path, monkeypatch): ...  # monkeypatch actions.downloads_dir
def test_banner_copy_puts_text_on_the_clipboard(window, other): ...
def test_receiving_a_deleted_item_says_so(window, other, store): ...
def test_quit_clears_the_heartbeat(window, store): ...
```

- [ ] **Step 2: Run to verify they fail** — `QT_QPA_PLATFORM=offscreen pytest tests/test_gui_window.py -q` → FAIL
- [ ] **Step 3: Implement**, then README: describe the sidebar, sending to a device (drag onto its name / right-click), notifications and the banner, history, previews; add manual checks:
  - [ ] Send from the Mac to the PC; the PC notification appears within ~10 s and clicking it saves to Downloads (record per OS whether clicking the notification itself works, or only the banner / tray menu)
  - [ ] Quit the GUI on one machine; it turns grey on the other within ~30 s
  - [ ] Drop a file onto a device name in the sidebar
  - [ ] Photos taken in portrait on a phone show upright previews
- [ ] **Step 4: Run to verify they pass** — same command → PASS; then the full suite `QT_QPA_PLATFORM=offscreen pytest -m "not slow" -q`, `ruff check src tests`, `ruff format --check src tests`, `docker build -f docker/Dockerfile -t spider:dev .` → all clean
- [ ] **Step 5: Commit**

```bash
git add src/spider/gui/window.py README.md tests/test_gui_window.py
git commit -m "feat: notify on items sent to this machine and receive them in one click"
```
