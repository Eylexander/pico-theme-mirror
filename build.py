#!/usr/bin/env python3
"""Builds a compact mirror of the Pico Launcher themes on themes.flashcarts.net for
the launcher's theme store. Formats are described in README.md.

Run in the Docker image (see README.md). Downloads are cached in cache/, the mirror
is written to out/. Archives are untrusted: they are unpacked in a temporary folder
and only files with validated names are copied into the packs."""

import json
import lzma
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # run with python -I
import lz10

SOURCE_REPO = "https://github.com/flashcarts/kernel-themes.git"
DOWNLOAD_BASE = "https://dl.themes.flashcarts.net/pico/"
CACHE = "cache"
OUT = "out"
INDEX_VERSION = 1
PACK_MAGIC = b"PTPK"
PACK_VERSION = 1
METHOD_STORE, METHOD_LZ10 = 0, 1
PREVIEW_SIZE = 256 * 192 * 2  # 15 bpp bitmap, like topbg.bin and preview.bin
MAX_FIELD = {"name": 48, "author": 32, "description": 120}

# Only these files end up on the SD card. Anything else in an archive (readme
# files, previews for the website, OS junk) is dropped.
NAME_PART = re.compile(r"^[A-Za-z0-9 _.()+\-]{1,64}$")
ALLOWED_EXTENSIONS = {".bin", ".json", ".bmp", ".bcstm"}


def log(*args):
    print(*args, flush=True)


def fetch_metadata():
    repo = os.path.join(CACHE, "kernel-themes")
    if os.path.isdir(repo):
        subprocess.run(["git", "-C", repo, "pull", "-q", "--depth", "1"], check=True)
    else:
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse",
                        SOURCE_REPO, repo], check=True)
        subprocess.run(["git", "-C", repo, "sparse-checkout", "set", "_pico"], check=True)
    folder = os.path.join(repo, "_pico")
    entries = []
    for file_name in sorted(os.listdir(folder)):
        if file_name.endswith(".md"):
            with open(os.path.join(folder, file_name), encoding="utf-8") as f:
                meta = parse_front_matter(f.read())
            meta["slug"] = slugify(file_name[:-3])
            entries.append(meta)
    return entries


def parse_front_matter(text):
    """Just enough YAML for these files: scalars and lists of scalars or `- url:` items."""
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    meta, key = {}, None
    for line in (match.group(1) if match else "").splitlines():
        item = re.match(r"^\s*-\s*(?:url:\s*)?(.*)$", line)
        pair = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)
        if pair:
            key, value = pair.group(1), unquote(pair.group(2))
            meta[key] = value if value else []
        elif item and key and isinstance(meta.get(key), list):
            meta[key].append(unquote(item.group(1)))
    return meta


def unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "theme"


def download(file_name):
    path = os.path.join(CACHE, "dl", file_name)
    if os.path.exists(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    url = DOWNLOAD_BASE + urllib.parse.quote(file_name)
    log("  downloading", url)
    with urllib.request.urlopen(url, timeout=60) as response, open(path + ".part", "wb") as f:
        shutil.copyfileobj(response, f)
    os.replace(path + ".part", path)
    return path


def unpack(archive, folder):
    result = subprocess.run(["7z", "x", "-y", "-bd", "-o" + folder, archive],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError("7z failed: " + result.stderr.strip()[:200])


def find_themes(folder):
    """Every folder with a theme.json is a theme. Returns {theme folder: {relative path: bytes}}."""
    themes = {}
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != "__MACOSX"]
        if "theme.json" in (f.lower() for f in files):
            themes[root] = collect_files(root)
    return themes


def collect_files(theme_folder):
    files, skipped = {}, []
    for root, dirs, names in os.walk(theme_folder):
        for name in names:
            full = os.path.join(root, name)
            relative = os.path.relpath(full, theme_folder).replace(os.sep, "/")
            parts = relative.split("/")
            ok = (not os.path.islink(full) and len(parts) <= 2
                  and all(NAME_PART.match(p) and p not in (".", "..") for p in parts)
                  and os.path.splitext(name)[1].lower() in ALLOWED_EXTENSIONS
                  and (len(parts) == 1 or parts[0].lower() == "bgm"))
            if ok:
                with open(full, "rb") as f:
                    files[relative] = f.read()
            else:
                skipped.append(relative)
    if skipped:
        log("    skipped:", ", ".join(sorted(skipped)))
    return files


def sd_folder_name(name):
    """Folder name for /_pico/themes/ on FAT: no reserved characters, ASCII only."""
    name = re.sub(r"[^A-Za-z0-9 _.()+\-]", "", name).strip(" .")
    return name[:48] or "theme"


def is_music(path):
    return path.lower().startswith("bgm/")


def build_pack(files):
    out = bytearray(PACK_MAGIC + struct.pack("<HH", PACK_VERSION, len(files)))
    for path in sorted(files):
        data = files[path]
        method, stored = METHOD_STORE, data
        if not is_music(path):  # music is ADPCM, it doesn't compress
            packed = lz10.compress(data)
            assert lz10.decompress(packed) == data, path
            if len(packed) < len(data):
                method, stored = METHOD_LZ10, packed
        name = path.encode("ascii")
        out += struct.pack("<B", len(name)) + name
        out += struct.pack("<BII", method, len(data), len(stored)) + stored
    return bytes(out)


def build_preview(files):
    """Top screen preview as the launcher shows it: preview.bin, else topbg.bin."""
    lower = {p.lower(): p for p in files}
    for candidate in ("preview.bin", "topbg.bin"):
        if candidate in lower and len(files[lower[candidate]]) == PREVIEW_SIZE:
            packed = lz10.compress(files[lower[candidate]])
            assert lz10.decompress(packed) == files[lower[candidate]]
            return packed
    return None


def clean_field(value, key):
    value = re.sub(r"[\t\r\n]+", " ", str(value or "")).strip()
    return value[:MAX_FIELD[key]]


def theme_info(files):
    path = next(p for p in files if p.lower() == "theme.json")
    try:
        return json.loads(files[path].decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


def main():
    os.makedirs(os.path.join(OUT, "themes"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "previews"), exist_ok=True)
    entries = fetch_metadata()
    log(f"{len(entries)} themes in the catalog")

    index_lines, used_ids = [], set()
    stats = {"archives": 0, "core": 0, "core_lzma": 0, "music": 0, "previews": 0, "themes": 0}
    for entry in entries:
        for download_name in entry.get("downloads") or []:
            log(entry.get("title", entry["slug"]), "-", download_name)
            try:
                archive = download(download_name)
            except Exception as e:
                log("  download failed:", e)
                continue
            stats["archives"] += os.path.getsize(archive)
            with tempfile.TemporaryDirectory() as tmp:
                try:
                    unpack(archive, tmp)
                except RuntimeError as e:
                    log("  ", e)
                    continue
                themes = find_themes(tmp)
            if not themes:
                log("  no theme.json in archive")
            for theme_folder, files in sorted(themes.items()):
                theme_id = entry["slug"] if len(themes) == 1 else slugify(entry["slug"] + "-" + os.path.basename(theme_folder))
                while theme_id in used_ids:
                    theme_id += "-2"
                used_ids.add(theme_id)

                core = {p: d for p, d in files.items() if not is_music(p)}
                music = {p: d for p, d in files.items() if is_music(p)}
                core_pack = build_pack(core)
                with open(os.path.join(OUT, "themes", theme_id + ".pak"), "wb") as f:
                    f.write(core_pack)
                music_size = 0
                if music:
                    music_pack = build_pack(music)
                    music_size = len(music_pack)
                    with open(os.path.join(OUT, "themes", theme_id + ".bgm.pak"), "wb") as f:
                        f.write(music_pack)
                preview = build_preview(files)
                if preview:
                    with open(os.path.join(OUT, "previews", theme_id + ".lz"), "wb") as f:
                        f.write(preview)

                info = theme_info(files)
                fields = [
                    theme_id,
                    sd_folder_name(os.path.basename(theme_folder)),
                    clean_field(info.get("name") or entry.get("title"), "name"),
                    clean_field(entry.get("creator") or info.get("author"), "author"),
                    str(len(core_pack)), str(music_size), str(len(preview) if preview else 0),
                    (entry.get("updated") or entry.get("created") or "")[:10],
                    clean_field(entry.get("description") or info.get("description"), "description"),
                ]
                index_lines.append("\t".join(fields))

                stats["themes"] += 1
                stats["core"] += len(core_pack)
                stats["core_lzma"] += len(lzma.compress(b"".join(core.values()), preset=9))
                stats["music"] += music_size
                stats["previews"] += len(preview) if preview else 0

    index = (f"PTIDX {INDEX_VERSION} {len(index_lines)}\n" + "\n".join(index_lines) + "\n").encode("utf-8")
    with open(os.path.join(OUT, "index.lz"), "wb") as f:
        f.write(lz10.compress(index))
    with open(os.path.join(OUT, "index.txt"), "wb") as f:  # for humans and debugging
        f.write(index)

    kb = lambda n: f"{n / 1024:,.0f} KB"
    log("")
    log(f"themes: {stats['themes']}")
    log(f"index: {kb(len(index))} raw, {kb(os.path.getsize(os.path.join(OUT, 'index.lz')))} compressed")
    log(f"original archives: {kb(stats['archives'])}")
    log(f"core packs (LZ77): {kb(stats['core'])}, avg {kb(stats['core'] / max(1, stats['themes']))}"
        f"  (LZMA would be {kb(stats['core_lzma'])})")
    log(f"music packs: {kb(stats['music'])}")
    log(f"previews: {kb(stats['previews'])}, avg {kb(stats['previews'] / max(1, stats['themes']))}")


if __name__ == "__main__":
    main()
