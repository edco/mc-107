#!/usr/bin/env python3
#
# Copyright 2026 Ed Coffey
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
"""
build.py — Build clean-room MC1P v2 patches (`mc107-0.pat`, `mc107-1.pat`) for MC-107.

- Core 0: Compiles `sample_chop.c` + `button_nav.c` + trampolines + extended tables
  into `SDRAM0_FREE` (`0x60F8_0000..0x60FF_FFFF`), using zero-pad caves nowhere.
  All surgical hooks/edits are verified via `expected_crc32` (zero vendor bytes).
- Core 1: Relocates DSP output program (`0x610A_DEE8..0x610B_59D0`) to `0x6138_9FE0`,
  clones Track 4 L/R template via `5 × Copy` + `10 × Write` (zero vendor bytes),
  updates pointers/offsets, and lifts `RecSource` / `ScatterPos` 4-track clamps.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

MC107_DIR = Path(__file__).resolve().parent
MC107_REPO = MC107_DIR.parent
WORKSPACE_ROOT = MC107_REPO.parent

sys.path.insert(0, str(MC107_REPO / "sdk"))
from mkpatch import Mc1pBuilder, crc32_ieee, verify_and_apply_mc1p_v2  # noqa: E402


def build_core0_pat(sites_json: Path, app1_ref: bytes) -> bytes:
    manifest = json.loads(sites_json.read_text(encoding="utf-8"))
    b = Mc1pBuilder()

    # Zero-init C module BSS & cur_track_var in SDRAM0_FREE
    b.add_fill(0x60FA_0000, 0, 0x0500)  # g_chop (.bss)
    b.add_fill(0x60FB_0000, 0, 0x0100)  # g_nav (.bss)
    b.add_fill(0x60FC_03FC, 0, 4)       # cur_track_var

    for s in manifest["sites"]:
        if s.get("status") != "implemented" or not s.get("patch"):
            continue
        addr = int(s["address"], 16)
        patch = bytes.fromhex(s["patch"])

        if 0x60F8_0000 <= addr <= 0x60FF_FFFF:
            # New code/table in free SDRAM0: emit WriteRaw (no expected bytes/CRC needed)
            b.add_write_raw(addr, patch)
        else:
            expected = bytes.fromhex(s["expected"])
            assert len(patch) == len(expected), f"len mismatch at {s['id']}"
            off = addr - 0x6000_0000
            ref_slice = app1_ref[off : off + len(patch)]
            if ref_slice != expected:
                raise ValueError(f"Reference mismatch at {s['id']} (0x{addr:08X})")
            # Surgical edit in stock App1_Main: emit Write with expected_crc32
            b.add_write(addr, crc32_ieee(expected), patch)

    pat = b.build()

    # Self-verify against a fresh copy of App1_Main_decrypted.bin
    sim = bytearray(0x0100_0000)
    sim[: len(app1_ref)] = app1_ref
    applied = verify_and_apply_mc1p_v2(pat, sim, 0x6000_0000)
    print(f"[OK] Verified mc107-0.pat ({applied} records, {len(pat)} bytes, 0 vendor bytes)")
    return pat


def build_core1_pat(sdram1_ref: bytes) -> bytes:
    # sdram1.bin[0x20..] is loaded at physical SDRAM 0x6100_0000.
    SDRAM1_BASE = 0x6100_0000 - 0x20
    DSP_ORIG_START = 0x610A_DEE8
    DSP_ORIG_END = 0x610B_59D0
    DSP_INSERT_VA = 0x610B_58C4
    DSP_LEN_VA = 0x610B_59D4
    DSP_ORIG_LEN = 0x0000_7A86
    DSP_TEMPLATE_VA = DSP_INSERT_VA - 0x20  # 0x610B_58A4 (Track 4 L/R, 32 bytes)
    NEW_DSP_VA = SDRAM1_BASE + 0x0038_A000  # 0x6138_9FE0
    TOTAL_INSERT_SIZE = 0xA0

    def rd_slice(va: int, n: int) -> bytes:
        off = va - SDRAM1_BASE
        return sdram1_ref[off : off + n]

    def rd32(va: int) -> int:
        return struct.unpack_from("<I", sdram1_ref, va - SDRAM1_BASE)[0]

    b = Mc1pBuilder()

    # 1. Zero-pad 0x6138_9B20..0x6138_9FE0 (0x4C0 bytes)
    b.add_fill(0x6138_9B20, 0, 0x04C0)

    # 2. Copy pre-insert DSP program (0x610A_DEE8..0x610B_58C4) -> 0x6138_9FE0
    pre_len = DSP_INSERT_VA - DSP_ORIG_START
    b.add_copy(NEW_DSP_VA, DSP_ORIG_START, pre_len)

    # 3. Clone 32-byte Track 4 L/R template via 5 × Copy + patch only the 10 u16 slot IDs
    #    (zero vendor template bytes embedded in mc107-1.pat!)
    extra_slots = [
        (0x258C, 0x258D),
        (0x258E, 0x258F),
        (0x2590, 0x2591),
        (0x2592, 0x2593),
        (0x25A2, 0x25A3),
    ]
    ins_base = NEW_DSP_VA + pre_len
    for i, (slot_l, slot_r) in enumerate(extra_slots):
        pair_va = ins_base + i * 0x20
        b.add_copy(pair_va, DSP_TEMPLATE_VA, 0x20)
        b.add_write_raw(pair_va + 8, struct.pack("<H", slot_l))
        b.add_write_raw(pair_va + 24, struct.pack("<H", slot_r))

    # 4. Copy post-insert DSP program (0x610B_58C4..0x610B_59D0)
    post_len = DSP_ORIG_END - DSP_INSERT_VA
    b.add_copy(ins_base + TOTAL_INSERT_SIZE, DSP_INSERT_VA, post_len)

    # 5. Update DSP length word
    assert rd32(DSP_LEN_VA) == DSP_ORIG_LEN
    b.add_write(
        DSP_LEN_VA,
        crc32_ieee(struct.pack("<I", DSP_ORIG_LEN)),
        struct.pack("<I", DSP_ORIG_LEN + TOTAL_INSERT_SIZE),
    )

    # 6. Update PTR_UPDATES
    PTR_UPDATES = [
        (0x610A3764, 0x610ADEE8, 0),
        (0x610A3EF4, 0x610ADF08, 0),
        (0x610B59DC, 0x610ADF14, 0),
        (0x610B59EC, 0x610B599C, TOTAL_INSERT_SIZE),
        (0x610B59FC, 0x610B59A4, TOTAL_INSERT_SIZE),
        (0x610B5A0C, 0x610B59AC, TOTAL_INSERT_SIZE),
        (0x610B5A1C, 0x610B59B4, TOTAL_INSERT_SIZE),
        (0x610B5A2C, 0x610B59BC, TOTAL_INSERT_SIZE),
    ]
    for va, old_target, shift in PTR_UPDATES:
        assert rd32(va) == old_target
        new_target = NEW_DSP_VA + (old_target - DSP_ORIG_START) + shift
        b.add_write(
            va,
            crc32_ieee(struct.pack("<I", old_target)),
            struct.pack("<I", new_target),
        )

    # 7. Update PARAM_OFF_UPDATES
    PARAM_OFF_UPDATES = [
        (0x610B5AC4, 0x7A40),
        (0x610B5AC8, 0x7A20),
        (0x610B5AD0, 0x7A4C),
        (0x610B5AD4, 0x7A2C),
        (0x610B5ADC, 0x7A24),
        (0x610B5AE0, 0x7A04),
        (0x610B5AE8, 0x7A32),
        (0x610B5AEC, 0x7A12),
        (0x610B5AF4, 0x79EE),
        (0x610B5AF8, 0x79CE),
        (0x610B5B00, 0x79FA),
        (0x610B5B04, 0x79DA),
        (0x610B5B0C, 0x7A08),
        (0x610B5B10, 0x79E8),
        (0x610B5B18, 0x7A16),
        (0x610B5B1C, 0x79F6),
        (0x610B6028, 0x79BA),
        (0x610B6048, 0x79CA),
        (0x610B63A8, 0x7A60),
        (0x610B63AC, 0x7A40),
    ]
    for va, old_off in PARAM_OFF_UPDATES:
        assert rd32(va) == old_off
        b.add_write(
            va,
            crc32_ieee(struct.pack("<I", old_off)),
            struct.pack("<I", old_off + TOTAL_INSERT_SIZE),
        )

    # 8. Lift Core 1 4-track routing clamps (RecSource & ScatterPos/DT1)
    ROUTING_CLAMPS = [
        (0x6101EDA6, b"\x05\x29\xc8\xbf\x0a\x21", b"\x09\x29\xc8\xbf\x0a\x21"),
        (0x6101F04E, b"\x05\x2d\xc8\xbf\x0a\x25", b"\x09\x2d\xc8\xbf\x0a\x25"),
    ]
    for va, exp, rep in ROUTING_CLAMPS:
        assert rd_slice(va, len(exp)) == exp
        b.add_write(va, crc32_ieee(exp), rep)

    pat = b.build()

    # Self-verify against sdram1.bin
    sim = bytearray(0x0040_0000)
    sim[: len(sdram1_ref)] = sdram1_ref
    applied = verify_and_apply_mc1p_v2(pat, sim, SDRAM1_BASE)
    print(f"[OK] Verified mc107-1.pat ({applied} records, {len(pat)} bytes, 0 vendor bytes)")
    return pat


def main() -> int:
    app1_path = WORKSPACE_ROOT / "vendor/mc101_sys_v182/extracts/RPG69_C0A/App1_Main_decrypted.bin"
    sdram1_path = WORKSPACE_ROOT / "vendor/mc101_sys_v182/extracts/RPG69_C1A/sdram1.bin"
    sites_json = MC107_DIR / "build/sites.json"
    if not sites_json.exists():
        sites_json.parent.mkdir(parents=True, exist_ok=True)
        seed = WORKSPACE_ROOT / "external/mc101-rex-8track/8-track/manifest/sites.json"
        if not seed.exists():
            raise FileNotFoundError(f"Missing {sites_json} and seed {seed}")
        sites_json.write_bytes(seed.read_bytes())
    import subprocess
    subprocess.run([sys.executable, str(MC107_DIR / "gen_mc107.py")], check=True)

    app1_ref = app1_path.read_bytes()
    sdram1_ref = sdram1_path.read_bytes()

    pat0 = build_core0_pat(sites_json, app1_ref)
    pat1 = build_core1_pat(sdram1_ref)

    for out_dir in (MC107_REPO / "patches", MC107_REPO / "docs"):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "mc107-0.pat").write_bytes(pat0)
        (out_dir / "mc107-1.pat").write_bytes(pat1)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
