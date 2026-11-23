#!/usr/bin/env python3
#
# Copyright 2026 Ed Coffey
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
"""
mkpatch.py — MC-101 Clean-Room Patch SDK (`MC1P` v2 Compiler & Verifier).

Ingests:
  1. Compiled ARM Thumb-2 ELFs (C via `arm-none-eabi-gcc` or Rust via `thumbv7em-none-eabihf`)
     -> Emits `WriteRaw (3)` for PROGBITS sections (.text, .rodata, .data)
     -> Emits `Fill (2)` with 0 for NOBITS sections (.bss)
  2. A commented patch manifest (JSON with `#` / `//` line comments)
     -> `write`: surgical edit verified by `expected_crc32` (zero vendor bytes stored!)
     -> `hook`: symbol-aware `bl` / `b.w` / `nop`-padded branch into an ELF symbol or address
     -> `copy`: `memmove` clone of existing runtime memory
     -> `fill`: byte fill
     -> `write_raw`: unverified raw bytes for empty memory

Outputs an `MC1P` v2 binary `.pat` file loadable by `mc-101-boot`.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path
from typing import Any

MC1P_MAGIC = b"MC1P"
MC1P_VERSION = 2

KIND_WRITE = 0
KIND_COPY = 1
KIND_FILL = 2
KIND_WRITE_RAW = 3


def crc32_ieee(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def strip_json_comments(text: str) -> str:
    """Strip `#` and `//` line comments outside double-quoted strings."""
    out_lines = []
    for line in text.splitlines():
        in_str = False
        esc = False
        cut = len(line)
        i = 0
        while i < len(line):
            ch = line[i]
            if esc:
                esc = False
            elif ch == "\\" and in_str:
                esc = True
            elif ch == '"':
                in_str = not in_str
            elif not in_str:
                if ch == "#" or line[i : i + 2] == "//":
                    cut = i
                    break
            i += 1
        out_lines.append(line[:cut])
    return "\n".join(out_lines)


def parse_int(val: int | str) -> int:
    if isinstance(val, int):
        return val
    s = val.strip()
    return int(s, 16) if s.lower().startswith("0x") else int(s, 10)


def encode_thumb_branch(from_va: int, to_va: int, link: bool) -> bytes:
    """Encode a 32-bit Thumb-2 `bl` (link=True) or `b.w` (link=False)."""
    offset = (to_va & ~1) - ((from_va & ~1) + 4)
    if offset < -0x0100_0000 or offset > 0x00FF_FFFE:
        raise ValueError(
            f"Thumb-2 branch out of ±16 MiB range: 0x{from_va:08X} -> 0x{to_va:08X}"
        )
    s = (offset >> 24) & 1
    i1 = (offset >> 23) & 1
    i2 = (offset >> 22) & 1
    j1 = (~i1 ^ s) & 1
    j2 = (~i2 ^ s) & 1
    imm10 = (offset >> 12) & 0x3FF
    imm11 = (offset >> 1) & 0x7FF
    hw1 = 0xF000 | (s << 10) | imm10
    hw2 = (0xD000 if link else 0x9000) | (j1 << 13) | (j2 << 11) | imm11
    return struct.pack("<HH", hw1, hw2)


def read_elf_symbols(elf_path: Path) -> dict[str, int]:
    """Extract defined symbol addresses using `arm-none-eabi-nm`."""
    res = subprocess.run(
        ["arm-none-eabi-nm", "-n", str(elf_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    syms: dict[str, int] = {}
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] not in ("U", "w"):
            syms[parts[2]] = int(parts[0], 16)
    return syms


def extract_elf_sections(elf_path: Path) -> list[tuple[str, int, int, bytes | None]]:
    """Parse ELF32 section headers directly (no external tools needed for extraction).

    Returns list of `(name, vaddr, size, data_or_None_for_nobits)` for ALLOC sections.
    """
    elf = elf_path.read_bytes()
    if elf[:4] != b"\x7fELF" or elf[4] != 1 or elf[5] != 1:
        raise ValueError(f"{elf_path}: expected 32-bit little-endian ELF")
    e_shoff, _, _, _, e_shentsize, e_shnum, e_shstrndx = struct.unpack_from(
        "<IIHHHHH", elf, 0x20
    )
    shdrs = [
        struct.unpack_from("<10I", elf, e_shoff + i * e_shentsize)
        for i in range(e_shnum)
    ]
    shstr_off = shdrs[e_shstrndx][4]
    shstr_sz = shdrs[e_shstrndx][5]
    shstr = elf[shstr_off : shstr_off + shstr_sz]

    def cstr(off: int) -> str:
        end = shstr.find(b"\x00", off)
        return shstr[off:end].decode("ascii", errors="replace")

    out: list[tuple[str, int, int, bytes | None]] = []
    for sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, *_ in shdrs:
        # SHF_ALLOC = 0x2; SHT_PROGBITS = 1, SHT_INIT_ARRAY = 14, SHT_NOBITS = 8
        if (sh_flags & 0x2) == 0 or sh_size == 0:
            continue
        name = cstr(sh_name)
        if sh_type == 8:  # SHT_NOBITS (.bss)
            out.append((name, sh_addr, sh_size, None))
        elif sh_type in (1, 14, 15, 16):
            out.append((name, sh_addr, sh_size, elf[sh_offset : sh_offset + sh_size]))
    return out


class Mc1pBuilder:
    def __init__(self, flags: int = 0) -> None:
        self.flags = flags
        self.records: list[bytes] = []

    def add_write(self, addr: int, expected_crc32: int, patch: bytes) -> None:
        if len(patch) > 0xFFFF:
            raise ValueError(f"Write record too large ({len(patch)} > 65535)")
        hdr = struct.pack("<IHHI", addr, len(patch), KIND_WRITE, expected_crc32)
        self.records.append(hdr + patch)

    def add_copy(self, dst: int, src: int, length: int) -> None:
        while length > 0:
            chunk = min(length, 0xFFFF)
            self.records.append(struct.pack("<IHHI", dst, chunk, KIND_COPY, src))
            dst += chunk
            src += chunk
            length -= chunk

    def add_fill(self, dst: int, byte_val: int, length: int) -> None:
        while length > 0:
            chunk = min(length, 0xFFFF)
            self.records.append(
                struct.pack("<IHHBBBB", dst, chunk, KIND_FILL, byte_val & 0xFF, 0, 0, 0)
            )
            dst += chunk
            length -= chunk

    def add_write_raw(self, addr: int, data: bytes) -> None:
        off = 0
        while off < len(data):
            chunk = data[off : off + 0x8000]
            self.records.append(
                struct.pack("<IHH", addr + off, len(chunk), KIND_WRITE_RAW) + chunk
            )
            off += len(chunk)

    def build(self) -> bytes:
        body = b"".join(self.records)
        crc = crc32_ieee(body)
        hdr = struct.pack(
            "<4sHHII", MC1P_MAGIC, MC1P_VERSION, len(self.records), self.flags, crc
        )
        return hdr + body


def build_from_manifest(
    manifest_path: Path,
    ref_image: bytes | None = None,
    ref_base: int = 0x6000_0000,
) -> tuple[bytes, dict[str, int]]:
    raw_text = manifest_path.read_text(encoding="utf-8")
    spec: dict[str, Any] = json.loads(strip_json_comments(raw_text))
    base_dir = manifest_path.parent

    builder = Mc1pBuilder(flags=parse_int(spec.get("flags", 0)))
    symbols: dict[str, int] = {}

    # 1. Ingest compiled ELFs first so hook targets can reference ELF symbols.
    for elf_rel in spec.get("elfs", []):
        elf_path = (base_dir / elf_rel).resolve()
        symbols.update(read_elf_symbols(elf_path))
        for sec_name, vaddr, size, data in extract_elf_sections(elf_path):
            if data is None:
                builder.add_fill(vaddr, 0, size)
            else:
                builder.add_write_raw(vaddr, data)

    # 2. Process explicit records.
    for rec in spec.get("records", []):
        op = rec["op"]
        if op == "write":
            addr = parse_int(rec["addr"])
            patch = bytes.fromhex(rec["patch"]) if isinstance(rec["patch"], str) else bytes(rec["patch"])
            if "expected_crc32" in rec and rec["expected_crc32"] is not None:
                exp_crc = parse_int(rec["expected_crc32"])
            elif ref_image is not None:
                off = addr - ref_base
                exp_crc = crc32_ieee(ref_image[off : off + len(patch)])
            else:
                raise ValueError(f"write at 0x{addr:08X} requires expected_crc32 or --ref")
            builder.add_write(addr, exp_crc, patch)
        elif op == "hook":
            addr = parse_int(rec["addr"])
            target_spec = rec["target"]
            target_va = (
                symbols[target_spec]
                if isinstance(target_spec, str) and target_spec in symbols
                else parse_int(target_spec)
            )
            kind = rec.get("kind", "bl")
            if kind == "bl":
                patch = encode_thumb_branch(addr, target_va, link=True)
            elif kind == "b.w":
                patch = encode_thumb_branch(addr, target_va, link=False)
            else:
                raise ValueError(f"Unknown hook kind: {kind}")
            if rec.get("pad_nop"):
                patch += b"\x00\xbf" * int(rec["pad_nop"])
            if "expected_crc32" in rec and rec["expected_crc32"] is not None:
                exp_crc = parse_int(rec["expected_crc32"])
            elif ref_image is not None:
                off = addr - ref_base
                exp_crc = crc32_ieee(ref_image[off : off + len(patch)])
            else:
                raise ValueError(f"hook at 0x{addr:08X} requires expected_crc32 or --ref")
            builder.add_write(addr, exp_crc, patch)
        elif op == "copy":
            builder.add_copy(parse_int(rec["dst"]), parse_int(rec["src"]), parse_int(rec["len"]))
        elif op == "fill":
            builder.add_fill(parse_int(rec["dst"]), parse_int(rec["byte"]), parse_int(rec["len"]))
        elif op == "write_raw":
            addr = parse_int(rec["addr"])
            data = bytes.fromhex(rec["data"]) if isinstance(rec["data"], str) else bytes(rec["data"])
            builder.add_write_raw(addr, data)
        else:
            raise ValueError(f"Unknown record op: {op}")

    return builder.build(), symbols


def verify_and_apply_mc1p_v2(pat: bytes, mem: bytearray, base_addr: int) -> int:
    """Reference Python verifier/applier for MC1P v2 (mirrors mc-101-boot firmware_cfg.rs)."""
    magic, ver, count, flags, crc = struct.unpack_from("<4sHHII", pat, 0)
    if magic != MC1P_MAGIC or ver != MC1P_VERSION:
        raise ValueError(f"Bad MC1P header: {magic!r} v{ver}")
    if crc32_ieee(pat[16:]) != crc:
        raise ValueError("MC1P CRC-32 mismatch")

    # Pass 1: verify all Write CRCs
    off = 16
    for idx in range(count):
        addr, plen, kind = struct.unpack_from("<IHH", pat, off)
        off += 8
        if kind == KIND_WRITE:
            (exp_crc,) = struct.unpack_from("<I", pat, off)
            off += 4 + plen
            m_off = addr - base_addr
            got_crc = crc32_ieee(bytes(mem[m_off : m_off + plen]))
            if got_crc != exp_crc:
                raise ValueError(
                    f"Record {idx} CRC mismatch at 0x{addr:08X}: got=0x{got_crc:08X} exp=0x{exp_crc:08X}"
                )
        elif kind in (KIND_COPY, KIND_FILL):
            off += 4
        elif kind == KIND_WRITE_RAW:
            off += plen
        else:
            raise ValueError(f"Unknown record kind {kind}")
    if off != len(pat):
        raise ValueError("Trailing bytes in MC1P")

    # Pass 2: apply
    off = 16
    for _ in range(count):
        addr, plen, kind = struct.unpack_from("<IHH", pat, off)
        off += 8
        m_off = addr - base_addr
        if kind == KIND_WRITE:
            patch = pat[off + 4 : off + 4 + plen]
            mem[m_off : m_off + plen] = patch
            off += 4 + plen
        elif kind == KIND_COPY:
            (src,) = struct.unpack_from("<I", pat, off)
            s_off = src - base_addr
            mem[m_off : m_off + plen] = bytes(mem[s_off : s_off + plen])
            off += 4
        elif kind == KIND_FILL:
            b = pat[off]
            mem[m_off : m_off + plen] = bytes([b]) * plen
            off += 4
        elif kind == KIND_WRITE_RAW:
            mem[m_off : m_off + plen] = pat[off : off + plen]
            off += plen
    return count


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("manifest", type=Path, help="Commented JSON patch manifest")
    ap.add_argument("-o", "--output", type=Path, required=True, help="Output .pat file")
    ap.add_argument("--ref", type=Path, help="Optional local reference binary for CRC computation")
    ap.add_argument("--ref-base", default="0x60000000", help="Base VA of --ref binary")
    args = ap.parse_args()

    ref_bytes = args.ref.read_bytes() if args.ref else None
    pat, _ = build_from_manifest(args.manifest, ref_bytes, parse_int(args.ref_base))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(pat)
    print(f"[OK] Wrote {args.output} ({len(pat)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
