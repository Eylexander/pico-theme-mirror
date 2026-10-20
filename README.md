# Pico theme mirror

A compact mirror of the Pico Launcher themes on [themes.flashcarts.net/pico](https://themes.flashcarts.net/pico/) for the launcher's built-in theme store (`../pico-launcher`, branch `theme-store`). The website is made for browsers: a 190 KB HTML catalog, `.7z` downloads that include background music, HTTPS only. On a DSi every byte goes over slow Wi-Fi, so the mirror serves:

- one small compressed index instead of the HTML catalog,
- theme files and background music as separate downloads (music is most of the size and optional),
- previews fetched one at a time, only for the theme being looked at,
- everything compressed with the DS BIOS's own LZ77, so the DS needs no decompressor code.

The site's content is licensed CC BY-NC-SA 4.0 (Sanras and contributors, see its license page). The themes belong to their authors and are only repackaged here; some include music from games.

Sizes (October 2026, 544 themes including variants): index 19 KB compressed, theme packs ~59 KB each (31 MB total), previews ~31 KB each, music ~660 MB after deduplication.

## Build

The tools only run in Docker:

```sh
docker build -t theme-repo-builder .
docker run --rm -v "<repo>:/repo" -w /repo theme-repo-builder python -I build.py
```

Downloads are cached in `cache/` (rerunning only fetches new themes), the mirror goes to `out/`. `python -I lz10.py` runs the compressor's self test. Archives are untrusted: they are unpacked inside the container in a temporary folder, and only files whose names pass the checks in `collect_files()` end up in a pack.

## Serving it to the DS (testing)

The DS speaks plain HTTP only. Serve `out/` from this PC:

```sh
docker run --rm -p 8080:8080 -v "<repo>/out:/srv:ro" -w /srv python:3.14 python -m http.server 8080
```

The DS then uses `http://<PC's LAN IP>:8080/`. Windows Firewall may need to allow inbound port 8080.

## Formats

All integers are little endian. "LZ77" means the DS BIOS format (type `0x10`, 4-byte header with the size, decoded by `swiDecompressLZSSWram`). Streams never use a displacement of 1, so the VRAM-safe BIOS call works too.

### `index.lz`

LZ77-compressed UTF-8 text. First line: `PTIDX <version> <count>`, currently version 1. Then one line per theme, tab-separated:

| # | Field | Example |
|---|---|---|
| 0 | id: `[a-z0-9-]`, unique, used in the URLs below | `joltik` |
| 1 | folder name to create under `/_pico/themes/` (FAT-safe ASCII) | `Joltik` |
| 2 | display name (≤ 48 chars) | `Joltik` |
| 3 | author (≤ 32 chars) | `cement____` |
| 4 | size of `themes/<id>.pak` in bytes | `24312` |
| 5 | music key: `music/<key>.pak`, empty if the theme has no music | `3f9a0c1d2e4b5a68` |
| 6 | size of the music pack, 0 if none | `3489080` |
| 7 | size of `previews/<id>.lz`, 0 if there is no preview | `61234` |
| 8 | last update, `YYYY-MM-DD` | `2026-08-27` |
| 9 | description (≤ 120 chars) | `joltik theme yippie` |

Variants (catalog entries with several downloads, like one per wallpaper) are separate themes named after their download.

`index.txt` is the same text uncompressed, for humans.

### `themes/<id>.pak` and `music/<key>.pak`

The theme files without music, and the music (`bgm/*.bcstm`) on its own. Music is stored once per distinct content (the key is the start of its SHA-256), because many variants share the same song: that takes the music from ~2.9 GB to ~660 MB. Both use the same container:

```
char[4] magic    "PTPK"
u16     version  1
u16     count
count × entry:
    u8      nameLength
    char[]  name        relative path, ASCII: "theme.json", "topbg.bin", "bgm/bgm.bcstm"
    u8      method      0 = stored, 1 = LZ77
    u32     size        size of the file once unpacked
    u32     storedSize  bytes that follow
    u8[]    data
```

Names have at most one folder level, and that folder is always `bgm`. Each part matches `[A-Za-z0-9 _.()+-]{1,64}`. The DS must still check this before writing anything, never trusting a pack, and must reject `..`. Music is always stored (ADPCM doesn't compress).

### `previews/<id>.lz`

LZ77-compressed 256×192 15 bpp bitmap (same format as `topbg.bin`): the theme's `preview.bin` if it has one, otherwise its `topbg.bin`. That is what the launcher's own theme selector shows.
