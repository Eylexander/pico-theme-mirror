"""LZ77 in the DS BIOS format (type 0x10), so the DS decompresses with
swiDecompressLZSSWram and needs no decoder of its own."""

MIN_MATCH = 3
MAX_MATCH = 18
WINDOW = 4096
MAX_CHAIN = 64  # positions tried per match; more compresses slightly better but slower


def compress(data: bytes) -> bytes:
    if len(data) >= 1 << 24:
        raise ValueError("LZ10 can't hold 16 MB or more")
    out = bytearray((0x10 | (len(data) << 8)).to_bytes(4, "little"))
    chains = {}  # 3-byte prefix -> recent positions, newest last
    pos = 0
    while pos < len(data):
        flag_index = len(out)
        out.append(0)
        for bit in range(8):
            if pos >= len(data):
                break
            best_len, best_disp = 0, 0
            key = data[pos:pos + MIN_MATCH]
            if len(key) == MIN_MATCH:
                for start in reversed(chains.get(key, ())):
                    disp = pos - start
                    if disp > WINDOW:
                        break
                    if disp < 2:  # keeps the stream valid for the VRAM-safe BIOS call too
                        continue
                    length = MIN_MATCH
                    limit = min(MAX_MATCH, len(data) - pos)
                    while length < limit and data[start + length] == data[pos + length]:
                        length += 1
                    if length > best_len:
                        best_len, best_disp = length, disp
                        if length == MAX_MATCH:
                            break
            step = best_len if best_len >= MIN_MATCH else 1
            if best_len >= MIN_MATCH:
                out[flag_index] |= 0x80 >> bit
                code = ((best_len - MIN_MATCH) << 12) | (best_disp - 1)
                out += bytes((code >> 8, code & 0xFF))
            else:
                out.append(data[pos])
            for p in range(pos, pos + step):
                k = data[p:p + MIN_MATCH]
                if len(k) == MIN_MATCH:
                    chain = chains.setdefault(k, [])
                    chain.append(p)
                    if len(chain) > MAX_CHAIN:
                        del chain[0]
            pos += step
    while len(out) % 4:
        out.append(0)
    return bytes(out)


def decompress(stream: bytes) -> bytes:
    header = int.from_bytes(stream[:4], "little")
    if header & 0xFF != 0x10:
        raise ValueError("not an LZ10 stream")
    size = header >> 8
    out = bytearray()
    pos = 4
    while len(out) < size:
        flags = stream[pos]
        pos += 1
        for bit in range(8):
            if len(out) >= size:
                break
            if flags & (0x80 >> bit):
                code = (stream[pos] << 8) | stream[pos + 1]
                pos += 2
                length = (code >> 12) + MIN_MATCH
                start = len(out) - (code & 0xFFF) - 1
                for i in range(length):
                    out.append(out[start + i])
            else:
                out.append(stream[pos])
                pos += 1
    return bytes(out)


if __name__ == "__main__":
    import os
    samples = [b"", b"a", b"abcabcabcabcabcabc", bytes(1000), os.urandom(5000),
               b"hello world " * 500, bytes(range(256)) * 40]
    for sample in samples:
        packed = compress(sample)
        assert decompress(packed) == sample, sample[:20]
        assert len(packed) % 4 == 0
    print("lz10 self test ok")
