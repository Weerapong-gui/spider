# spider

Move text and files between your own machines. Not a web crawler.

Push from one machine, pull on another. A Python server in Docker on a
machine you own, a CLI for macOS, Windows, and Linux, and a page in the
browser. Files come back byte-for-byte identical, checked with sha256 at
both ends.

The thing it exists for:

```bash
# on the Mac, having copied some text
spider copy

# on the PC
spider paste          # the text is on the clipboard, ready to paste
```

## What it is not

No background sync, no conflict resolution, no version history, no user
accounts. You push, you pull. It is a shared drawer with an HTTP API.

## Server

Runs on a Linux machine you control, reachable over Tailscale or your LAN.
It is not built to face the internet, and should not be put behind a
Cloudflare tunnel or a public reverse proxy.

```bash
git clone https://github.com/Weerapong-gui/spider.git
cd spider
cp .env.example .env

openssl rand -base64 32      # put this in SPIDER_TOKEN
tailscale ip -4              # put this in SPIDER_BIND_IP

docker compose --env-file .env -f docker/compose.yml up -d --build
docker compose --env-file .env -f docker/compose.yml logs -f
```

`--env-file .env` is required: Compose reads `.env` from the folder of the
compose file (`docker/`), not from where you run the command, and would
otherwise report every variable as missing.

The server refuses to start without a token of at least 32 characters, and
Compose refuses to start without `SPIDER_BIND_IP` and `SPIDER_HOST_DATA_DIR`.

### If the data directory sits inside a Samba share

`/mnt/nas` is shared, so add this to the `[NAS]` share in `smb.conf` to keep
the blobs out of Finder, where a stray drag could delete them:

```ini
veto files = /.spider/
```

`spider verify` reports any item whose file has gone missing.

## Client

```bash
pip install "git+https://github.com/Weerapong-gui/spider.git#egg=spider[cli]"
spider init
```

On Linux without a desktop session there is no clipboard. `spider copy` and
`spider paste` will say so; use `spider cat` and `spider push -` instead. On
X11 install `xclip`, on Wayland install `wl-clipboard`.

### Commands

```
spider init                  set the server address and token
spider copy                  clipboard -> server
spider paste [ref]           server -> clipboard
spider push FILE...          send files
spider push -                send stdin
spider push -t "text"        send text
spider push FILE --to NAME   send to one device's inbox (also: copy --to NAME)
spider devices               list devices, online or offline
spider ls [-q SEARCH]        list, newest first
spider pull [ref] [-o PATH]  download
spider cat [ref]             write to stdout
spider rm REF                delete
spider verify                check every item still has its file
```

Update the server before the clients: a server older than `--to` silently
ignores it and the item reaches nobody's inbox.

`ref` is an item id, any unambiguous prefix of one, or `latest`.

Exit codes: 3 unauthorized, 4 not found, 5 ambiguous id, 6 checksum
mismatch, 7 disk full, 8 server unreachable.

## Desktop app (Linux and macOS)

A window with the same list, plus a tray icon:

```bash
pip install "git+https://github.com/Weerapong-gui/spider.git#egg=spider[gui]"
spider-gui
```

The first launch asks for the server address and token (the same values as
`spider init`; both share `~/.config/spider/config.toml`).

- **Send clipboard** / **Paste latest**: the `spider copy` / `spider paste` pair
- **Send files…**, or drop files onto the window
- Double-click a text item to put it on the clipboard, a file to save it
  (checksum verified, written to a `.part` file first)
- Right-click for Copy, Save as…, Delete
- The sidebar lists the server (green when reachable) and every device that
  has talked to it: **Running** for desktop apps seen in the last 30 seconds,
  **Offline** with when each was last seen. Right-click an offline device to
  remove it from the list
- Send to one device: drop files onto its name in the sidebar, or right-click
  it for **Send clipboard to this device** / **Send files to this device…**.
  The item still shows in everyone's list; only that device is notified
- When something is sent to this machine, a notification appears even with
  the window open, and a banner above the list offers **Save to Downloads**
  (files; a clash becomes `name (1).ext`, nothing is overwritten) or **Copy**
  (text), plus **Dismiss**. The tray menu also lists the newest five as
  **Receive …**. Nothing is saved until you click. Each item notifies once
- Select a row to see its history: where it was uploaded from and every
  download since (device, and the path or "clipboard")
- Images show a thumbnail in the list; other files show a file-type icon
- Closing the window keeps spider running in the tray; Quit is in the tray
  menu and under File. Where no tray exists (some Wayland sessions), closing
  the window quits.

Not included: a global hotkey. macOS needs an Accessibility permission for
one and Wayland forbids them, so it is left out rather than half-working.

## Browser

Open `http://<server>:8181`, enter the token once. Drop files anywhere on
the page to upload, or press Ctrl/Cmd+V to send whatever is on the
clipboard — including images, which the CLI does not handle.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[server,cli,gui,dev]"
QT_QPA_PLATFORM=offscreen .venv/bin/pytest -m "not slow"
.venv/bin/ruff check src tests
```

## Manual checks before calling a release good

These are not covered by the test suite:

- [ ] Copy text on the Mac, run `spider copy`, run `spider paste` on the PC over Tailscale, and confirm the text is identical including line endings
- [ ] Push a file larger than 1 GB and confirm `spider pull` reports the checksum verified
- [ ] Drag a file onto the browser page and watch the progress bar run
- [ ] Open the page on a phone on the home network and send some text
- [ ] Push a file whose name is in Thai and confirm it downloads with the name intact
- [ ] Stop the container mid-upload and confirm `spider ls` does not show a partial item
- [ ] Delete a blob directly on the server and confirm `spider verify` reports it
- [ ] Send from the Mac to the PC; the PC notification appears within ~10 s and clicking it saves to Downloads (record per OS whether clicking the notification itself works, or only the banner / tray menu)
- [ ] Quit the GUI on one machine; it turns grey on the other within ~30 s
- [ ] Drop a file onto a device name in the sidebar
- [ ] Photos taken in portrait on a phone show upright previews
