#!/usr/bin/env python3
#
# Copyright 2026 Ed Coffey
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v.2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
"""Assemble the MC-101 v1.82 8-track patches and generate manifest/sites.json."""

from __future__ import annotations

import json
import struct
import subprocess
import tempfile
from pathlib import Path

import capstone

REPO_ROOT = Path(__file__).resolve().parents[2]
MC107_DIR = Path(__file__).resolve().parent
MC101_IMAGE_PATH = REPO_ROOT / "vendor/mc101_sys_v182/extracts/RPG69_C0A/App1_Main_decrypted.bin"
MANIFEST_PATH = REPO_ROOT / "mc-107/mc107/build/sites.json"

BASE_VA = 0x60000000


def assemble_thumb(base_va: int, asm_body: str, ext_syms: dict[str, int] | None = None) -> tuple[bytes, dict[str, int]]:
    """Assemble Thumb-2 code linked at `base_va` and return (raw_bytes, symbol_map)."""
    ext_defs = ""
    if ext_syms:
        for name, addr in ext_syms.items():
            offset = (addr & ~1) - base_va
            ext_defs += f"    .type {name}, %function\n    .thumb_set {name}, _start + ({offset})\n"
    full_asm = f"""
    .syntax unified
    .cpu cortex-m7
    .thumb
    .section .text,"ax",%progbits
{ext_defs}
    .balign 2
    .thumb_func
    .global _start
_start:
{asm_body}
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        s_file = tmp / "patch.s"
        o_file = tmp / "patch.o"
        elf_file = tmp / "patch.elf"
        bin_file = tmp / "patch.bin"
        ld_file = tmp / "patch.ld"

        s_file.write_text(full_asm, encoding="utf-8")
        ld_file.write_text(
            f"""
SECTIONS {{
    . = 0x{base_va:08x};
    .text : SUBALIGN(2) {{ *(.text*) }}
}}
""",
            encoding="utf-8",
        )
        subprocess.run(["arm-none-eabi-as", "-mcpu=cortex-m7", "-mthumb", str(s_file), "-o", str(o_file)], check=True)
        subprocess.run(["arm-none-eabi-ld", "-T", str(ld_file), str(o_file), "-o", str(elf_file)], check=True)
        subprocess.run(["arm-none-eabi-objcopy", "-O", "binary", str(elf_file), str(bin_file)], check=True)
        nm_out = subprocess.check_output(["arm-none-eabi-nm", "-n", str(elf_file)], text=True)
        syms: dict[str, int] = {}
        for line in nm_out.splitlines():
            parts = line.split()
            if len(parts) == 3 and not parts[2].startswith("$") and parts[2] != "_start":
                if ext_syms and parts[2] in ext_syms:
                    continue
                syms[parts[2]] = int(parts[0], 16)
        return bin_file.read_bytes(), syms


def compile_c_module(
    c_path: Path,
    caves: list[tuple[str, int, int]],
    ram_va: int,
    ram_max: int,
) -> tuple[dict[str, bytes], dict[str, int]]:
    """Compile freestanding Cortex-M7 Thumb-2 C file across verified code caves with .bss in ram_va.

    `caves` is [(section_label, origin_va, max_len), ...] for CODE1 (.text.entry + .text + .rodata),
    CODE2 (.text.slice), CODE3 (.text.detect).
    Returns ({section_label: raw_bytes}, symbol_map).
    """
    assert len(caves) == 3
    (_, va1, max1), (_, va2, max2), (_, va3, max3) = caves
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        o_file = tmp / "mod.o"
        elf_file = tmp / "mod.elf"
        ld_file = tmp / "mod.ld"

        ld_file.write_text(
            f"""
MEMORY {{
    CODE1 (rx)  : ORIGIN = 0x{va1:08x}, LENGTH = 0x{max1:x}
    CODE2 (rx)  : ORIGIN = 0x{va2:08x}, LENGTH = 0x{max2:x}
    CODE3 (rx)  : ORIGIN = 0x{va3:08x}, LENGTH = 0x{max3:x}
    RAM   (rwx) : ORIGIN = 0x{ram_va:08x}, LENGTH = 0x{ram_max:x}
}}
SECTIONS {{
    .code1 : {{
        *(.chop_entry)
        *(.text*)
        . = ALIGN(4);
    }} > CODE1
    .code2 : {{
        *(.chop_slice*)
        *(.rodata*)
        . = ALIGN(4);
    }} > CODE2
    .code3 : {{
        *(.chop_detect*)
        . = ALIGN(4);
    }} > CODE3
    .data : {{
        *(.data*)
    }} > RAM
    .bss (NOLOAD) : {{
        *(.chop_state)
        *(.bss*)
        *(COMMON)
    }} > RAM
    /DISCARD/ : {{ *(.ARM.exidx*) *(.ARM.extab*) *(.comment) *(.note*) }}
}}
""",
            encoding="utf-8",
        )
        subprocess.run(
            [
                "arm-none-eabi-gcc",
                "-mcpu=cortex-m7",
                "-mthumb",
                "-Os",
                "-ffreestanding",
                "-fno-builtin",
                "-fno-stack-protector",
                "-fno-common",
                "-ffunction-sections",
                "-fdata-sections",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-c",
                str(c_path),
                "-o",
                str(o_file),
            ],
            check=True,
        )
        subprocess.run(
            [
                "arm-none-eabi-ld",
                "--gc-sections",
                "-e",
                "sample_chop_event",
                "-T",
                str(ld_file),
                str(o_file),
                "-o",
                str(elf_file),
            ],
            check=True,
        )
        # Ensure no initialized .data section exists (all RW state must live in zero-init .bss)
        sec_hdrs = subprocess.check_output(["arm-none-eabi-objdump", "-h", str(elf_file)], text=True)
        for line in sec_hdrs.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[1] == ".data":
                data_sz = int(parts[2], 16)
                if data_sz != 0:
                    raise ValueError(f"{c_path.name}: non-empty .data section ({data_sz} B); use const or zero-init static")

        blobs: dict[str, bytes] = {}
        for idx, (sec_name, _, max_len) in enumerate(caves, start=1):
            bin_file = tmp / f"code{idx}.bin"
            subprocess.run(
                ["arm-none-eabi-objcopy", "-O", "binary", "-j", f".code{idx}", str(elf_file), str(bin_file)],
                check=True,
            )
            raw = bin_file.read_bytes()
            if len(raw) > max_len:
                raise ValueError(f"{c_path.name} {sec_name}: {len(raw)} B exceeds budget {max_len} B")
            blobs[sec_name] = raw

        nm_out = subprocess.check_output(["arm-none-eabi-nm", "-n", str(elf_file)], text=True)
        syms: dict[str, int] = {}
        for line in nm_out.splitlines():
            parts = line.split()
            if len(parts) == 3 and not parts[2].startswith("$"):
                syms[parts[2]] = int(parts[0], 16)
        return blobs, syms


def compile_single_cave_c(
    c_path: Path,
    code_va: int,
    code_max: int,
    ram_va: int,
    ram_max: int,
    state_sec: str,
) -> tuple[bytes, dict[str, int]]:
    """Compile a freestanding Cortex-M7 Thumb-2 C module into one verified code cave + .bss cave."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        o_file = tmp / "mod.o"
        elf_file = tmp / "mod.elf"
        bin_file = tmp / "mod.bin"
        ld_file = tmp / "mod.ld"
        ld_file.write_text(
            f"""
MEMORY {{
    CODE (rx)  : ORIGIN = 0x{code_va:08x}, LENGTH = 0x{code_max:x}
    RAM  (rwx) : ORIGIN = 0x{ram_va:08x}, LENGTH = 0x{ram_max:x}
}}
SECTIONS {{
    .code : {{
        *(.text*)
        *(.rodata*)
        . = ALIGN(4);
    }} > CODE
    .data : {{
        *(.data*)
    }} > RAM
    .bss (NOLOAD) : {{
        *({state_sec})
        *(.bss*)
        *(COMMON)
    }} > RAM
    /DISCARD/ : {{ *(.ARM.exidx*) *(.ARM.extab*) *(.comment) *(.note*) }}
}}
""",
            encoding="utf-8",
        )
        subprocess.run(
            [
                "arm-none-eabi-gcc",
                "-mcpu=cortex-m7",
                "-mthumb",
                "-Os",
                "-ffreestanding",
                "-fno-builtin",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-c",
                str(c_path),
                "-o",
                str(o_file),
            ],
            check=True,
        )
        subprocess.run(
            ["arm-none-eabi-ld", "-T", str(ld_file), str(o_file), "-o", str(elf_file)],
            check=True,
        )
        subprocess.run(
            ["arm-none-eabi-objcopy", "-O", "binary", "-j", ".code", str(elf_file), str(bin_file)],
            check=True,
        )
        nm_out = subprocess.check_output(["arm-none-eabi-nm", "-n", str(elf_file)], text=True)
        syms: dict[str, int] = {}
        for line in nm_out.splitlines():
            parts = line.split()
            if len(parts) == 3 and not parts[2].startswith("$"):
                syms[parts[2]] = int(parts[0], 16)
        blob = bin_file.read_bytes()
        assert len(blob) <= code_max, f"{c_path.name} ({len(blob)}B) exceeds cave budget ({code_max}B)"
        return blob, syms


def pad_nops(code: bytes, target_len: int) -> bytes:
    if len(code) > target_len:
        raise ValueError(f"assembled code ({len(code)}B) exceeds target length ({target_len}B)")
    rem = target_len - len(code)
    if rem % 2 != 0:
        raise ValueError(f"odd padding length {rem}")
    return code + (b"\x00\xbf" * (rem // 2))


def read_stock(image: bytes, va: int, length: int) -> str:
    off = va - BASE_VA
    return image[off : off + length].hex()


def build_sys_midi_table(image: bytes) -> bytes:
    """Build the 23-entry SYSTEM(MIDI) ITEM_LIST_PRM table with Track Ch1..8."""
    orig_va = 0x60219F48
    orig_off = orig_va - BASE_VA
    # Entries 0..8 (9 entries = 252 bytes, ending with Track Ch4 rel_pid=0x00e)
    part1 = image[orig_off : orig_off + 9 * 28]
    # New entries for Track Ch5..8 (grp=4, rel_pid=0x00f..0x012, sub_idx=-1, 0, 0, 0, 0)
    extra = b"".join(
        struct.pack("<7I", 4, rel_pid, 0xFFFFFFFF, 0, 0, 0, 0)
        for rel_pid in (0x00F, 0x010, 0x011, 0x012)
    )
    # Remaining entries 9..18 (10 entries = 280 bytes)
    part2 = image[orig_off + 9 * 28 : orig_off + 19 * 28]
    table = part1 + extra + part2
    assert len(table) == 23 * 28 == 644
    return table


def build_drum_pad_edit_blob(base_va: int, max_size: int) -> tuple[bytes, int]:
    """Build the 11-section MC-707-depth Drum Pad Edit descriptor array, tables, and labels.

    Layout starting at `base_va` (0x60FC1000):
      - 11 x 12-byte section descriptors (132 bytes)
      - 9 new ITEM_LIST_PRM tables (147 x 28 = 4116 bytes; PAD EDIT & EQ EDIT reuse stock)
      - NUL-terminated deduplicated ASCII strings (section titles & short LCD labels)
    """
    # Section definitions: (title_or_stock_va, [(grp, rel_pid, label), ...] or stock_tbl_va, stock_count)
    wmt_fields = [
        (0x00, "Switch"),
        (0x01, "Type"),
        (0x02, "Bank"),
        (0x03, "Wave L"),
        (0x04, "Wave R"),
        (0x05, "Gain"),
        (0x0E, "Level"),
        (0x0B, "Pan"),
        (0x09, "Coarse"),
        (0x0A, "Fine"),
        (0x13, "Velo Lo"),
        (0x14, "Velo Hi"),
        (0x15, "Fade Lo"),
        (0x16, "Fade Hi"),
        (0x06, "FXM Sw"),
        (0x07, "FXM Col"),
        (0x08, "FXM Dpt"),
        (0x0C, "Rnd Pan"),
        (0x0D, "Alt Pan"),
        (0x0F, "DlyMode"),
        (0x10, "DlySync"),
        (0x11, "DlyNote"),
        (0x12, "DlyTime"),
    ]

    sections = [
        # Sec 0: stock PAD EDIT (13 items)
        ("stock", 0x602D1118, 13, 0x602C443C),
        # Sec 1: expanded INST EDIT (kept at index 1 so 0x6004E1E8 Env Mode <-> TVA T4 sync stays intact)
        (
            "new",
            0x602C4448,  # stock 'INST EDIT:'
            [
                (0xFA, 0x17, "Voice"),
                (0xFA, 0x18, "Env Mode"),
                (0xFA, 0x19, "WMT Velo"),
                (0xFA, 0x11, "Inst Lvl"),
                (0xFA, 0x12, "Src Key"),
                (0xFA, 0x13, "FineTune"),
                (0xFA, 0x14, "RndPitch"),
                (0xFA, 0x15, "Rnd Pan"),
                (0xFA, 0x16, "Alt Pan"),
                (0xFA, 0x7E, "WaveSync"),
            ],
        ),
        # Sec 2..5: WMT1..WMT4 (23 params each, grp=0xFA)
        *[
            (
                "new",
                f"WMT{w + 1} EDIT:",
                [(0xFA, 0x1A + w * 0x17 + off, lbl) for off, lbl in wmt_fields],
            )
            for w in range(4)
        ],
        # Sec 6: TVF/TVA filter & velocity (8 params, grp=0xFA)
        (
            "new",
            "TVF/TVA:  ",
            [
                (0xFA, 0x76, "Filter"),
                (0xFA, 0x77, "Cutoff"),
                (0xFA, 0x7A, "Reso"),
                (0xFA, 0x78, "Cut VCrv"),
                (0xFA, 0x79, "Cut VSns"),
                (0xFA, 0x7B, "Res VSns"),
                (0xFA, 0x7C, "Lvl VCrv"),
                (0xFA, 0x7D, "Lvl VSns"),
            ],
        ),
        # Sec 7: Pitch Env (14 params, grp=0x103)
        (
            "new",
            "PITCH ENV:",
            [
                (0x103, 0x00, "Depth"),
                (0x103, 0x01, "VeloSens"),
                (0x103, 0x0D, "VeloCrv"),
                (0x103, 0x02, "T1 Velo"),
                (0x103, 0x03, "T4 Velo"),
                (0x103, 0x04, "Time 1"),
                (0x103, 0x05, "Time 2"),
                (0x103, 0x06, "Time 3"),
                (0x103, 0x07, "Time 4"),
                (0x103, 0x08, "Level 0"),
                (0x103, 0x09, "Level 1"),
                (0x103, 0x0A, "Level 2"),
                (0x103, 0x0B, "Level 3"),
                (0x103, 0x0C, "Level 4"),
            ],
        ),
        # Sec 8: TVF Env (14 params, grp=0x10C)
        (
            "new",
            "TVF  ENV: ",
            [
                (0x10C, 0x00, "Depth"),
                (0x10C, 0x01, "VeloCrv"),
                (0x10C, 0x02, "VeloSens"),
                (0x10C, 0x03, "T1 Velo"),
                (0x10C, 0x04, "T4 Velo"),
                (0x10C, 0x05, "Time 1"),
                (0x10C, 0x06, "Time 2"),
                (0x10C, 0x07, "Time 3"),
                (0x10C, 0x08, "Time 4"),
                (0x10C, 0x09, "Level 0"),
                (0x10C, 0x0A, "Level 1"),
                (0x10C, 0x0B, "Level 2"),
                (0x10C, 0x0C, "Level 3"),
                (0x10C, 0x0D, "Level 4"),
            ],
        ),
        # Sec 9: TVA Env (9 params, grp=0x115)
        (
            "new",
            "TVA  ENV: ",
            [
                (0x115, 0x00, "T1 Velo"),
                (0x115, 0x01, "T4 Velo"),
                (0x115, 0x02, "Time 1"),
                (0x115, 0x03, "Time 2"),
                (0x115, 0x04, "Time 3"),
                (0x115, 0x05, "Time 4"),
                (0x115, 0x06, "Level 1"),
                (0x115, 0x07, "Level 2"),
                (0x115, 0x08, "Level 3"),
            ],
        ),
        # Sec 10: stock EQ EDIT (8 items)
        ("stock", 0x602D12BC, 8, 0x602C4430),
    ]

    num_sections = len(sections)
    desc_size = num_sections * 12
    total_new_entries = sum(len(s[2]) for s in sections if s[0] == "new")
    tables_offset = desc_size
    strings_offset = tables_offset + total_new_entries * 28

    str_pool = bytearray()
    str_vas: dict[str, int] = {}

    def intern(s: str) -> int:
        if s not in str_vas:
            str_vas[s] = base_va + strings_offset + len(str_pool)
            str_pool.extend(s.encode("ascii") + b"\x00")
        return str_vas[s]

    descs = bytearray()
    tables = bytearray()
    for sec in sections:
        if sec[0] == "stock":
            _, tbl_va, cnt, title_va = sec
            descs.extend(struct.pack("<3I", tbl_va, cnt, title_va))
        else:
            _, title, items = sec
            title_va = title if isinstance(title, int) else intern(title)
            tbl_va = base_va + tables_offset + len(tables)
            descs.extend(struct.pack("<3I", tbl_va, len(items), title_va))
            for grp, rel_pid, lbl in items:
                lbl_va = intern(lbl)
                tables.extend(struct.pack("<7I", grp, rel_pid, 0, 0x00030000, 0, lbl_va, 0))

    blob = bytes(descs) + bytes(tables) + bytes(str_pool)
    # Pad to 4-byte boundary
    if len(blob) % 4:
        blob += b"\x00" * (4 - (len(blob) % 4))
    assert len(blob) <= max_size, f"DRUM-PAD-EDIT-TBL ({len(blob)}B) exceeds cave budget ({max_size}B)"
    return blob, num_sections


def build_pad_sound_menu_blob(base_va: int, max_size: int) -> tuple[bytes, int]:
    """Build the 13-entry Drum Pad SOUND menu adding WMT1..4 WAVE loaders, WMT1..4 SMPL editors, and SMPL CHOP.

    Each entry is 12 bytes: (label_ptr, screen_id, mode_param).
    For screen_id 0x4A (WAVE FILE):
      mode_param = 0 -> stock full-pad reset + load into WMT1
      mode_param = 1..4 -> non-destructive load into WMT1..WMT4 (preserving other layers)
    For screen_id 0x5D (SAMPLE EDIT):
      mode_param = 1..4 -> edit User Sample assigned to WMT1..WMT4
    For screen_id 0x68 (SAMPLE CHOP):
      mode_param = 0 -> open custom 16x2 LCD waveform & sample chop screen
    """
    entries_spec = [
        (0x602C3FA0, 0x48, 3),  # PRESET (stock)
        (0x602A8414, 0x4D, 2),  # PROJECT (stock)
        (0x602C3F80, 0x4A, 0),  # WAVE FILE (stock WMT1 + pad init)
        ("WMT1 WAVE", 0x4A, 1),  # Non-destructive WMT1 WAV import
        ("WMT2 WAVE", 0x4A, 2),  # Non-destructive WMT2 WAV import
        ("WMT3 WAVE", 0x4A, 3),  # Non-destructive WMT3 WAV import
        ("WMT4 WAVE", 0x4A, 4),  # Non-destructive WMT4 WAV import
        (0x602C3FA8, 0x34, 0),  # PAD EDIT (stock + Phase 1 11 sections)
        ("WMT1 SMPL", 0x5D, 1),  # Sample Edit for WMT1 user sample
        ("WMT2 SMPL", 0x5D, 2),  # Sample Edit for WMT2 user sample
        ("WMT3 SMPL", 0x5D, 3),  # Sample Edit for WMT3 user sample
        ("WMT4 SMPL", 0x5D, 4),  # Sample Edit for WMT4 user sample
        ("SMPL CHOP", 0x68, 0),  # Sample Chop waveform & slicing screen (Screen 0x68)
    ]
    num_entries = len(entries_spec)
    str_offset = num_entries * 12
    str_pool = bytearray()
    tbl = bytearray()
    for lbl, screen_id, mode in entries_spec:
        if isinstance(lbl, int):
            lbl_va = lbl
        else:
            lbl_va = base_va + str_offset + len(str_pool)
            str_pool.extend(lbl.encode("ascii") + b"\x00")
        tbl.extend(struct.pack("<3I", lbl_va, screen_id, mode))
    blob = bytes(tbl) + bytes(str_pool)
    if len(blob) % 4:
        blob += b"\x00" * (4 - (len(blob) % 4))
    assert len(blob) <= max_size, f"PAD-SND-MENU-TBL ({len(blob)}B) exceeds budget ({max_size}B)"
    return blob, num_entries


def build_smpl_edit_descs_blob(base_va: int, max_size: int) -> bytes:
    """Build 4 x 12-byte USER SAMPLE section descriptors ('WMT1 SAMPLE:'..'WMT4 SAMPLE:')."""
    titles = [f"WMT{w + 1} SAMPLE:" for w in range(4)]
    str_offset = 4 * 12
    str_pool = bytearray()
    descs = bytearray()
    for t in titles:
        t_va = base_va + str_offset + len(str_pool)
        str_pool.extend(t.encode("ascii") + b"\x00")
        # Reuse stock 8-entry USER SAMPLE table @ 0x602D7BE4
        descs.extend(struct.pack("<3I", 0x602D7BE4, 8, t_va))
    blob = bytes(descs) + bytes(str_pool)
    if len(blob) % 4:
        blob += b"\x00" * (4 - (len(blob) % 4))
    assert len(blob) <= max_size, f"SMPL-EDIT-DESCS ({len(blob)}B) exceeds budget ({max_size}B)"
    return blob


def main() -> None:
    image = MC101_IMAGE_PATH.read_bytes()
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)

    # 0. SDRAM cave 2 at 0x60FC1000 (256 B of verified zero padding) is currently
    #    unused/stock; diagnostic probes were removed once their results were
    #    recorded (README §4 Attempt 9).

    # 1. Assemble SDRAM cave helpers at 0x60FC0000 (safe zero-padded region)
    cave_va = 0x60FC0000
    sdram_ext = {
        "fw_get_param": 0x6009CB2C,
        "fw_set_param": 0x6009CB9C,
        "fw_set_dsp_param": 0x6006C82C,
        "fw_is_btn_pressed": 0x6001CBD8,
        "fw_get_active_screen": 0x6001CC44,
        "fw_is_trk_alloc": 0x60052158,
        "fw_get_trk_type": 0x60050D70,
        "fw_select_track": 0x600344CC,
        "fw_sel_led_off": 0x600977FA,
    }
    cave_asm = """

    .thumb_func
    .global get_cur_track
get_cur_track:
    movw    r0, #0x03fc
    movt    r0, #0x60fc
    ldr     r0, [r0]
    cmp     r0, #8
    it      hs
    movhs   r0, #0
    bx      lr

    .thumb_func
    .global resolve_sel_track
resolve_sel_track:
    push    {r2, r3, r5, r6, lr}
    bl      get_cur_track
    mov     r5, r0
    subs    r6, r4, #6
    and.w   r2, r5, #4
    adds    r6, r6, r2
    cmp     r6, r5
    bne     2f
    movs    r0, #0x12
    bl      fw_is_btn_pressed
    cbnz    r0, 2f
    movs    r0, #0
    bl      fw_is_btn_pressed
    cbz     r0, 1f
    bl      fw_get_active_screen
    cmp     r0, #0x36
    bne     2f
1:
    eor.w   r6, r6, #4
2:
    adds    r4, r6, #6
    movw    r0, #0x03fc
    movt    r0, #0x60fc
    str     r6, [r0]
    movw    r0, #0x0200
    movt    r0, #0x6180
    strb    r6, [r0]
    mov     r1, r6
    mov     r0, r6
    pop     {r2, r3, r5, r6, pc}

    .thumb_func
    .global tramp_sel_main
tramp_sel_main:
    push    {r2, r3, lr}
    bl      resolve_sel_track
    mov     r0, r5
    pop     {r2, r3, pc}

    .thumb_func
    .global tramp_sel_empty
tramp_sel_empty:
    push    {r1, r2, r3, lr}
    bl      get_cur_track
    and.w   r0, r0, #4
    subs    r1, r4, #6
    adds    r0, r0, r1
    bl      fw_is_trk_alloc
    pop     {r1, r2, r3, pc}

    .thumb_func
    .global tramp_sel_led_check
tramp_sel_led_check:
    and.w   r0, r6, #3
    cmp     r4, r0
    ite     eq
    moveq   r0, #1
    movne   r0, #0
    lsrs    r1, r6, #2
    eors    r0, r0, r1
    cbz     r0, 1f
    bx      lr
1:
    b.w     fw_sel_led_off

    .thumb_func
    .global tramp_fdr_top
tramp_fdr_top:
    @ FDR-BANK-TOP (0x60099212): stock loop head computed
    @     r0 = *[r5+0xc] (record base 0x61991810, 8 records of 0x100) + r4 * 0x100
    @ with r4 the plain loop counter 0..3, so the whole function only ever looked
    @ at bank A (tracks 1..4).  Here the loop counter is r7 (physical fader 0..3)
    @ and r4 is rebuilt every iteration as the track this fader drives:
    @     r4 = r7 + bank,  bank = bit 2 of the selected-track byte.
    @ The bank is read from *[*r5+0x10] == 0x61800200, the very byte the stock
    @ selected/record gate at 0x60099224 compares against r4, so gate and record
    @ pointer can never disagree.  It must NOT come from fw_get_param: this
    @ function runs from a high-frequency timer ISR, where fw_get_param fails its
    @ mutex and returns -1 (that is what made the first loop-remap attempt always
    @ read bank B: -1 & 4 == 4).
    @ Scratch: r0/r2 (dead at the loop head).  r4 = banked track 0..7, used by
    @ the record pointer, the pid 0x90 + r4 state queries (all 8 pids 0x90..0x97
    @ are dispatched, arms at 0x6003395C..0x60033998 -> UI_STATE +0x438..+0x454)
    @ and the engine track-index queries.  Only the two fader RGB LED ids
    @ (0x1c + physical) need the unbanked index; see tramp_fdr_ledid1/2.
    ldr     r2, [r5, #0x10]
    ldrsb.w r0, [r2]
    movs    r2, #4
    ands    r0, r0, r2
    add.w   r4, r7, r0
    ldr     r0, [r5, #0xc]
    add.w   r0, r0, r4, lsl #8
    bx      lr

    .thumb_func
    .global tramp_fdr_bot
tramp_fdr_bot:
    @ FDR-BANK-BOT (0x600992BC): stock advanced its counter and looped with
    @     adds r4, #1 / cmp r4, #4 / blt 0x60099212
    @ The `blt` at 0x600992C0 is left stock, so this routine only has to do the
    @ same thing to the *physical* loop base r7 and leave the flags of
    @ `cmp r7, #4` behind.  r4 is not advanced at all: the loop head rebuilds it
    @ as r7 + bank, and it is popped on return, so nothing reads it here.
    @ Do NOT derive the bank from r4 (e.g. `r4 & 4`): in bank A the last value
    @ r4 == 4 has bit 2 set, which reads as "bank B", and the counter then wraps
    @ to 0 and the loop never terminates - inside the timer ISR that is a freeze.
    @ A masking counter (`r4 & 3`) wraps the same way.
    adds    r7, r7, #1
    cmp     r7, #4
    bx      lr

    .thumb_func
    .global tramp_fdr_ledid1
tramp_fdr_ledid1:
    @ FDR-LED-ID1 (0x600992B0): stock computes the fader RGB LED id as
    @ 0x1c + r4.  With r4 banked that would address 0x20..0x23 (pad LEDs) in
    @ bank B, so strip the bank.  r0 = LED id, r2 = colour and r6 = mode stay as
    @ stock; r1/r3 are dead at this point (r1 is reloaded from r6 at 0x600992B4).
    movs    r3, #3
    mov     r1, r4
    ands    r1, r1, r3
    add.w   r0, r1, #0x1c
    bx      lr

    .thumb_func
    .global tramp_fdr_ledid2
tramp_fdr_ledid2:
    @ FDR-LED-ID2 (0x60099356): same unbanking for the flash/red path, and it
    @ also supplies the LED mode #3 that stock took from r7 (`mov r1, r7`),
    @ because r7 is now the physical loop base.
    movs    r3, #3
    mov     r1, r4
    ands    r1, r1, r3
    add.w   r0, r1, #0x1c
    movs    r1, #3
    bx      lr

    .thumb_func
    .global tramp_drum_mute_led
tramp_drum_mute_led:
    push    {r1, r2, r3, r5, lr}
    bl      get_cur_track
    and.w   r5, r0, #4
    adds    r0, r4, r5
    bl      fw_get_trk_type
    cmp     r0, #1
    it      eq
    addeq   r4, r4, r5
    pop     {r1, r2, r3, r5, pc}

    .thumb_func
    .global tramp_drum_mute_press
tramp_drum_mute_press:
    push    {r1, r2, r3, r5, lr}
    bl      get_cur_track
    and.w   r5, r0, #4
    adds    r0, r6, r5
    bl      fw_get_trk_type
    cmp     r0, #1
    it      eq
    addeq   r6, r6, r5
    pop     {r1, r2, r3, r5, pc}

    .thumb_func
    .global tramp_knob_multi_r6
tramp_knob_multi_r6:
    @ KNOB-UI-MULTI1 (0x60036A16): replaces MULTI-only `subs r5, r0, #1; subs r0, r4, #7`
    @ BEFORE 0x60036A1A (`mov r6, r0`), because Single knob mode (`0x60036A8A`) branches
    @ directly to 0x60036A1A.
    push    {r1, r2, r3, lr}
    subs    r5, r0, #1
    bl      get_cur_track
    and.w   r0, r0, #4
    subs    r1, r4, #7
    adds    r0, r0, r1
    pop     {r1, r2, r3, pc}

    .thumb_func
    .global tramp_knob_multi_r5
tramp_knob_multi_r5:
    @ KNOB-UI-MULTI2 (0x60036B6A): replaces MULTI-only `mov r8, r0; subs r5, r4, #7`
    @ BEFORE 0x60036B6E (`movs r0, #0x36`), because Single knob mode (`0x60036B8E`)
    @ branches directly to 0x60036B6E.
    push    {r1, r2, r3, lr}
    mov     r8, r0
    bl      get_cur_track
    and.w   r0, r0, #4
    subs    r5, r4, #7
    adds    r5, r5, r0
    pop     {r1, r2, r3, pc}

    .thumb_func
    .global tramp_prj_load_init
tramp_prj_load_init:
    @ Reached by "b.w" from PRJ-POST-LOAD (0x60046424), which replaces the
    @ enclosing function's epilogue (stock: add sp, sp, #0x28 / pop.w {r4-r10,pc}).
    @ This is a TAIL CALL: the "sub sp, sp, #8" below is also the frame
    @ adjustment (sub 0x08 + add 0x30 == the stock add sp, sp, #0x28) and the
    @ final pop.w {r4-r10, pc} is byte-for-byte the stock register list.
    @ Do not add pushes here and do not change the sp constants: the pair
    @ (sub #8, add #0x30) and the 7-register pop are load-bearing.
    sub     sp, sp, #8

    @ Reset active track to 0 in both variables
    movw    r0, #0x03fc
    movt    r0, #0x60fc
    movs    r1, #0
    str     r1, [r0]
    movw    r0, #0x0200
    movt    r0, #0x6180
    strb    r1, [r0]

    @ Initialize tone level cache to 100 for all 8 tracks
    movw    r0, #0x99f8
    movt    r0, #0x2001
    movs    r1, #100
    movs    r2, #0
1:
    str     r1, [r0, r2, lsl #2]
    adds    r2, r2, #1
    cmp     r2, #8
    blo     1b

    @ Initialize volume for tracks 5..8 to 255 (unity gain)
    movs    r4, #0
2:
    @ Set Area 2 param 0x4B + r4 * 3 (track 5..8 fader) in UI/param table
    movs    r3, #0
    str     r3, [sp]
    subs    r3, r3, #1
    movs    r2, #255
    add.w   r1, r4, r4, lsl #1
    adds    r1, r1, #0x4b
    movs    r0, #2
    bl      fw_set_param

    @ Send Fader parameter 255 to Sound Engine DSP via IPC queue.
    @ This is the step that unmutes tracks 5..8 at project load; without it the
    @ upper bank starts silent/zero (hardware verified both ways).
    movs    r1, #255
    add.w   r2, r4, r4, lsl #1
    adds    r2, r2, #0x4b
    bl      fw_set_dsp_param

    adds    r4, r4, #1
    cmp     r4, #4
    blo     2b

    add     sp, sp, #0x30
    pop.w   {r4, r5, r6, r7, r8, sb, sl, pc}

"""

    cave_bytes_raw, cave_syms = assemble_thumb(cave_va, cave_asm, sdram_ext)
    cave_code = pad_nops(cave_bytes_raw, 508) + b"\x00\x00\x00\x00"
    print(f"[+] SDRAM cave code: {len(cave_bytes_raw)} / {len(cave_code)} bytes used")
    for k, v in cave_syms.items():
        if not k.startswith("$"):
            print(f"    {k:24s} = 0x{v:08X}")

    # 2. Assemble ITCM Fader + MULTI knob handler at exec VA 0x01024FD2 (file VA 0x6033776E)
    itcm_exec_va = 0x01024FD2
    itcm_file_va = 0x6033776E
    itcm_ext = {
        "itcm_msg_init": 0x01018A66,
        "itcm_msg_set": 0x01018B6C,
        "itcm_msg_send": 0x01018AEC,
        "itcm_fader_done": 0x01025120,
        "itcm_knob_resume": 0x01025068,
    }
    itcm_asm = """
    .thumb_func
    .global itcm_fader_entry
itcm_fader_entry:
    add     r0, sp, #4
    movs    r2, #2
    movs    r3, #0
    movw    r1, #0x4a08
    movt    r1, #0x6039
    str     r5, [sp]
    bl      itcm_msg_init
    movw    r0, #0x03fc
    movt    r0, #0x60fc
    ldr     r0, [r0]
    and.w   r0, r0, #4
    subs    r6, r6, #1
    adds    r6, r6, r0
    add.w   r1, r6, r6, lsl #1
    adds    r1, #0x3f
    mov     r2, r7
    add     r0, sp, #4
    bl      itcm_msg_set
    add     r0, sp, #4
    bl      itcm_msg_send
    b.w     itcm_fader_done

    .balign 2
    .thumb_func
    .global itcm_knob_multi
itcm_knob_multi:
    movw    r0, #0x03fc
    movt    r0, #0x60fc
    ldr     r0, [r0]
    and.w   r0, r0, #4
    subs    r1, r6, #7
    add.w   r8, r0, r1
    b.w     itcm_knob_resume
"""
    itcm_bytes_raw, itcm_syms = assemble_thumb(itcm_exec_va, itcm_asm, itcm_ext)
    itcm_code = pad_nops(itcm_bytes_raw, 128)
    print(f"[+] ITCM fader/knob code: {len(itcm_bytes_raw)} / {len(itcm_code)} bytes used")
    for k, v in itcm_syms.items():
        if not k.startswith("$"):
            print(f"    {k:24s} = 0x{v:08X}")

    # 3. Button Navigation Mode (Hold '>' to map '<'/'>' -> VALUE dial, 'TEMPO' -> ENTER, blink '<'/'>'/'TEMPO'/'EXIT')
    #    Verified zero-padded SDRAM cave 0x60FB0000..0x602DAE7F (0 incoming refs, non-overlapping with other features):
    #      0x60FB0000..0x602DA6FF (256 B)   : NAV-RAM (.bss, g_nav pinned first via .nav_state)
    #      0x60FB0200..0x602DAE3F (1,856 B) : NAV-CODE (compiled mc-107/mc107/src/button_nav.c)
    #      0x60FC3200..0x602DAE7F (64 B)    : NAV-LED-TRAMP (tramp_nav_led_override)
    nav_ram_va, nav_ram_max = 0x60FB0000, 0x0100
    nav_code_va, nav_code_max = 0x60FB0200, 0x0740
    nav_tramp_va, nav_tramp_max = 0x60FC3200, 0x0040
    nav_c_path = REPO_ROOT / "mc-107/mc107/src/button_nav.c"
    nav_code_blob, nav_syms = compile_single_cave_c(
        nav_c_path, nav_code_va, nav_code_max, nav_ram_va, nav_ram_max, ".nav_state"
    )
    nav_state_va = nav_syms.get("g_nav")
    assert nav_state_va == nav_ram_va, f"g_nav expected at 0x{nav_ram_va:08X}, got {hex(nav_state_va or 0)}"
    assert "nav_tick_and_drain" in nav_syms and "nav_dispatch_event" in nav_syms
    print(
        f"[+] Button Nav Mode C module @0x{nav_code_va:08X}: {len(nav_code_blob)}/{nav_code_max} B "
        f"(tick=0x{nav_syms['nav_tick_and_drain']:08X}, dispatch=0x{nav_syms['nav_dispatch_event']:08X}, g_nav=0x{nav_state_va:08X})"
    )

    nav_tramp_ext = {"fw_set_led_cont": 0x6001E6BC}
    nav_tramp_asm = f"""
    .balign 2
    .thumb_func
    .global tramp_nav_led_override
tramp_nav_led_override:
    @ Hooked at 0x6001E6B8 (stock 4B: push {{r4, r5, r6, lr}}; mov r5, r1)
    @ r0 = led_id, r1 = mode, r2 = color; r3 is caller-saved scratch
    movw    r3, #0x{nav_state_va & 0xFFFF:04x}
    movt    r3, #0x{(nav_state_va >> 16) & 0xFFFF:04x}
    ldrb    r3, [r3, #0]        @ g_nav.active
    cbz     r3, 1f
    cmp     r0, #3              @ LED_MEAS_L (<)
    beq     2f
    cmp     r0, #4              @ LED_MEAS_R (>)
    beq     2f
    cmp     r0, #15             @ LED_TEMPO
    beq     2f
    cmp     r0, #16             @ LED_EXIT
    bne     1f
2:
    movs    r1, #3              @ mode = 3 (BLINK)
    movs    r2, #0xff           @ full brightness
1:
    push    {{r4, r5, r6, lr}}
    mov     r5, r1
    b.w     fw_set_led_cont
"""
    nav_tramp_raw, nav_tramp_syms = assemble_thumb(nav_tramp_va, nav_tramp_asm, nav_tramp_ext)
    nav_tramp_code = pad_nops(nav_tramp_raw, (len(nav_tramp_raw) + 3) & ~3)
    assert len(nav_tramp_code) <= nav_tramp_max, f"NAV-LED-TRAMP ({len(nav_tramp_code)}B) exceeds {nav_tramp_max}B"
    print(f"[+] Button Nav LED trampoline @0x{nav_tramp_va:08X}: {len(nav_tramp_raw)}/{nav_tramp_max} B")

    all_ext = {
        **sdram_ext,
        **itcm_ext,
        **cave_syms,
        **itcm_syms,
        **nav_syms,
        **nav_tramp_syms,
        "target_exit": 0x6005B450,
        # Fall-through targets inside UpdateFaderRgbLeds for the banked loop sites.
        "fdr_loop_top": 0x60099218,
        "fdr_led_id2_ret": 0x6009935C,
        "midi_ch_alloff_exit": 0x6003D6DE,
    }

    def asm_at(va: int, body: str, length: int) -> str:
        raw, _ = assemble_thumb(va, body, all_ext)
        return pad_nops(raw, length).hex()

    sys_midi_table = build_sys_midi_table(image)

    # 3. Assemble Drum Pad Edit envelope clip-sync trampoline at 0x60FC0800
    drum_env_tramp_va = 0x60FC0800
    drum_env_ext = {
        "fw_prm_id_init": 0x600996F8,
        "fw_prm_set_clip": 0x60038540,
    }
    drum_env_asm = """
    .thumb_func
    .global tramp_pad_edit_env_clip
tramp_pad_edit_env_clip:
    push    {r4, r5, lr}
    @ First execute stock SetClipIndex for Drum Inst (track + 0xFB)
    bl      fw_prm_set_clip
    @ Also set active clip index on Pitch Env (0x104), TVF Env (0x10D), TVA Env (0x116)
    movw    r5, #0x104
1:
    ldr.w   r0, [r4, #0x9f8]
    add     r1, r0, r5
    movs    r2, #0
    movs    r3, #0
    add.w   r0, r4, #0x9f0
    bl      fw_prm_id_init
    ldrh.w  r0, [r4, #0x9fc]
    sxth    r2, r0
    add.w   r1, r4, #0x9f0
    movw    r0, #0xadc4
    movt    r0, #0x602c
    bl      fw_prm_set_clip
    adds    r5, r5, #9
    cmp     r5, #0x116
    ble     1b
    pop     {r4, r5, pc}
"""
    drum_env_raw, drum_env_syms = assemble_thumb(drum_env_tramp_va, drum_env_asm, drum_env_ext)
    drum_env_code = pad_nops(drum_env_raw, (len(drum_env_raw) + 3) & ~3)
    assert drum_env_tramp_va + len(drum_env_code) <= 0x60FC1000, "DRUM-ENV-CLIP-HOOK overflows into 0x60FC1000"
    print(f"[+] Drum Env clip trampoline @0x{drum_env_tramp_va:08X}: {len(drum_env_raw)} bytes")

    drum_pad_edit_va = 0x60FC1000
    drum_pad_edit_blob, drum_pad_sec_cnt = build_drum_pad_edit_blob(drum_pad_edit_va, 0x60FC2380 - drum_pad_edit_va)
    print(f"[+] Drum Pad Edit blob @0x{drum_pad_edit_va:08X}: {len(drum_pad_edit_blob)} / {0x60FC2380 - drum_pad_edit_va} bytes ({drum_pad_sec_cnt} sections)")

    # 4. Phase 2+3 + Sample Chop Milestone 1:
    #    - CHOP-CAVE-0 (0x60FC2400..0x602D4EFF, 3,296 B, verified zero knob-table padding):
    #        0x60FC2400..0x602D48BF: 105-entry relocated screen table (0x00..0x67 + 0x68 SMPL CHOP + NULL)
    #        0x60FC2B00..0x602D48CF: Screen 0x68 descriptor { sample_chop_event | 1, 0, 0, 0 }
    #        0x60FC2C00..0x602D49CF: 13-entry Drum Pad SOUND menu (relocated from 0x60FC2380)
    #        0x60FC2DF0..0x602D4EFF: CHOP_RAM (1,280 B zero-init .bss for sample_chop.c)
    #    - CHOP-CAVE-1 (0x60F80000..0x602D5CCF, 3,312 B, verified zero knob-table padding):
    #        0x60F80000..: compiled C code + rodata from mc-107/mc107/src/sample_chop.c
    scr_tbl_va = 0x60FC2400
    scr_68_desc_va = 0x60FC2B00
    pad_snd_menu_va = 0x60FC2C00
    chop_ram_va = 0x60FA0000
    chop_ram_max = 0x1000
    chop_code1_va, chop_code1_max = 0x60F80000, 0x0CF0  # 3,312 B
    chop_code2_va, chop_code2_max = 0x60F88000, 0x0CF0  # 3,312 B
    chop_code3_va, chop_code3_max = 0x60F90000, 0x0CE0  # 3,296 B

    chop_c_path = REPO_ROOT / "mc-107/mc107/src/sample_chop.c"
    chop_blobs, chop_syms = compile_c_module(
        chop_c_path,
        [
            ("CHOP-CODE", chop_code1_va, chop_code1_max),
            ("CHOP-SLICE", chop_code2_va, chop_code2_max),
            ("CHOP-DETECT", chop_code3_va, chop_code3_max),
        ],
        chop_ram_va,
        chop_ram_max,
    )
    chop_code_blob = chop_blobs["CHOP-CODE"]
    chop_slice_blob = chop_blobs["CHOP-SLICE"]
    chop_detect_blob = chop_blobs["CHOP-DETECT"]
    assert chop_syms.get("sample_chop_event") == chop_code1_va, (
        f"sample_chop_event expected at 0x{chop_code1_va:08X}, got {hex(chop_syms.get('sample_chop_event', 0))}"
    )
    chop_state_va = chop_syms.get("g_chop")
    assert chop_state_va == chop_ram_va, (
        f"g_chop expected at 0x{chop_ram_va:08X}, got {hex(chop_state_va or 0)}"
    )
    print(
        f"[+] Sample Chop C module: CODE1={len(chop_code_blob)}/{chop_code1_max}B @0x{chop_code1_va:08X}, "
        f"SLICE={len(chop_slice_blob)}/{chop_code2_max}B @0x{chop_code2_va:08X}, "
        f"DETECT={len(chop_detect_blob)}/{chop_code3_max}B @0x{chop_code3_va:08X}"
    )

    # Build extended 105-entry screen table (stock 104 entries @ 0x601D06D4 + Screen 0x68 + NULL terminator)
    stock_scr_tbl_off = 0x601D06D4 - BASE_VA
    scr_tbl_blob = (
        image[stock_scr_tbl_off : stock_scr_tbl_off + 104 * 16]
        + struct.pack("<4I", scr_68_desc_va, 0x0000FF0D, 0, 7)
        + struct.pack("<4I", 0, 0, 0, 0)
    )
    assert len(scr_tbl_blob) == 106 * 16 == 1696
    scr_68_desc_blob = struct.pack("<4I", chop_code1_va | 1, 0, 0, 0)

    pad_snd_menu_blob, pad_snd_menu_cnt = build_pad_sound_menu_blob(pad_snd_menu_va, 0x200)
    print(f"[+] Drum Pad SOUND menu blob @0x{pad_snd_menu_va:08X}: {len(pad_snd_menu_blob)} bytes ({pad_snd_menu_cnt} items)")

    smpl_edit_descs_va = 0x60FC2E00
    smpl_edit_descs_blob = build_smpl_edit_descs_blob(smpl_edit_descs_va, 0x60FC2EA0 - smpl_edit_descs_va)
    print(f"[+] WMT1..4 Sample Edit descs @0x{smpl_edit_descs_va:08X}: {len(smpl_edit_descs_blob)} bytes")

    drum_p2_va = 0x60FC2EA0
    drum_p2_ext = {
        "fw_bind_pad_wav": 0x600299A4,
        "fw_mark_clip_dirty": 0x6001A1A2,
        "fw_get_user_sample_id": 0x60054AF0,
        "fw_get_sys_val": 0x6009BFCC,
        "fw_pad_to_note": 0x6003744C,
        "fw_get_inst_prm": 0x6009C064,
        "fw_list_set_descs": 0x6006DB94,
    }
    drum_p2_asm = f"""
    .thumb_func
    .global tramp_snd_clear_wmt
tramp_snd_clear_wmt:
    movw    r1, #0x2af8
    movt    r1, #0x60fc
    movs    r2, #0
    str     r2, [r1, #0]
    str     r2, [r1, #4]
    ldr.w   r0, [r5, #0x9f0]
    bx      lr

    .balign 2
    .thumb_func
    .global tramp_pad_snd_enter
tramp_pad_snd_enter:
    ldrd    r4, r5, [r0, #4]
    movw    r1, #0x2af8
    movt    r1, #0x60fc
    cmp     r4, #0x4a
    it      eq
    streq   r5, [r1, #4]
    cmp     r4, #0x5d
    it      eq
    streq   r5, [r1, #0]
    bx      lr

    .balign 2
    .thumb_func
    .global tramp_bind_pad_wav_wmt
tramp_bind_pad_wav_wmt:
    movw    ip, #0x2afc
    movt    ip, #0x60fc
    ldr.w   ip, [ip]
    cmp.w   ip, #0
    beq     9f
    cmp.w   ip, #4
    bhi     9f
    push    {{r4, r5, r6, r7, lr}}
    mov     r4, r0
    mov     r5, r1
    mov     r6, r2
    mov     r7, r3
    sub.w   ip, ip, #1
    @ Compute DrumInst ptr = DrumInstTable[track] + clip * 0x4A40 + pad * 0xD8
    movw    r0, #0x0774
    movt    r0, #0x6021
    ldr.w   r0, [r0, r4, lsl #2]
    movw    r1, #0x4a40
    muls    r1, r5, r1
    add     r0, r0, r1
    movs    r1, #0xd8
    muls    r1, r6, r1
    add     r0, r0, r1
    @ wmt_ptr = DrumInst + 0x1C + wmt_idx * 0x1C
    adds    r0, r0, #0x1c
    movs    r1, #0x1c
    mla     r0, ip, r1, r0
    @ Write WMTx: SW=1, GTYPE=2 (SAMP), GID=0, WAV_NUM_L=sample_id+1, WAV_NUM_R=0
    movs    r1, #1
    strb    r1, [r0, #0]
    movs    r1, #2
    strb    r1, [r0, #1]
    movs    r1, #0
    strh    r1, [r0, #2]
    adds    r2, r7, #1
    strh    r2, [r0, #4]
    strh    r1, [r0, #6]
    @ Ensure audible defaults if this layer was previously zeroed
    ldrb    r2, [r0, #0x11]
    cbnz    r2, 2f
    movs    r2, #127
    strb    r2, [r0, #0x11]
2:
    ldrh    r2, [r0, #0x18]
    cbnz    r2, 3f
    movw    r2, #0x7f01
    strh    r2, [r0, #0x18]
3:
    @ Ensure KitPad.InstNum != 0 (if blank pad, enable InstNum=1; preserve existing pads)
    movw    r0, #0x0754
    movt    r0, #0x6021
    ldr.w   r0, [r0, r4, lsl #2]
    movw    r1, #0x0d00
    muls    r1, r5, r1
    add     r0, r0, r1
    movs    r1, #20
    muls    r1, r6, r1
    adds    r1, r1, #0xa0
    ldrh    r2, [r0, r1]
    cbnz    r2, 4f
    movs    r2, #1
    strh    r2, [r0, r1]
4:
    @ Mark clip dirty so edits save cleanly (do NOT call 0x6001A1B6 which resets KitPad/EQ!)
    mov     r0, r4
    mov     r1, r5
    bl      fw_mark_clip_dirty
    pop     {{r4, r5, r6, r7, pc}}
9:
    b.w     fw_bind_pad_wav

    .balign 2
    .thumb_func
    .global tramp_sample_del_wmt_scan
tramp_sample_del_wmt_scan:
    ldrb    r3, [r2, #0x1d]
    cmp     r3, #2
    bne     3f
    ldrsh.w r0, [r2, #0x20]
    cmp     r0, #0
    ble     1f
    cmp.w   r0, #0x1f4
    bgt     1f
    add     r0, r0, r6
    strb.w  fp, [r0, #0xa03]
1:
    ldrsh.w r0, [r2, #0x22]
    cmp     r0, #0
    ble     3f
    cmp.w   r0, #0x1f4
    bgt     3f
    add     r0, r0, r6
    strb.w  fp, [r0, #0xa03]
3:
    bx      lr

    .balign 2
    .thumb_func
    .global tramp_smpl_edit_get_id
tramp_smpl_edit_get_id:
    @ Input: r0=clip, r1=track, r2=0, r3=0
    movw    ip, #0x2af8
    movt    ip, #0x60fc
    ldr.w   ip, [ip]
    cmp.w   ip, #2
    blo     8f
    cmp.w   ip, #4
    bhi     8f
    push    {{r4, r5, r6, r7, r8, lr}}
    sub     sp, #8
    mov     r7, r0              @ r7 = clip
    mov     r5, r1              @ r5 = track
    sub.w   r0, ip, #1          @ wmt_idx = 1..3
    movs    r1, #0x17
    mul     r8, r0, r1          @ r8 = wmt_idx * 23 (0x17, 0x2E, 0x45)
    @ Fetch current drum pad index (same as 0x60054B7A..0x60054B88)
    movs    r0, #0x63
    bl      fw_get_sys_val
    mov     r1, r0
    movw    r0, #0x4430
    movt    r0, #0x60a9
    bl      fw_pad_to_note
    sub.w   r6, r0, #0x15       @ r6 = pad_idx
    mov.w   r4, #-1
    @ Query WMT[wmt_idx].WAV_GTYPE (0x8E2 + r8)
    movw    r3, #0x8e2
    add     r3, r3, r8
    movs    r2, #0xfb
    mov     r1, r7
    mov     r0, r5
    str     r6, [sp]
    bl      fw_get_inst_prm
    cmp     r0, #1              @ SAMP?
    bne     7f
    @ Query WMT[wmt_idx].WAV_NUM_L (0x8E4 + r8)
    movw    r3, #0x8e4
    add     r3, r3, r8
    movs    r2, #0xfb
    mov     r1, r7
    mov     r0, r5
    str     r6, [sp]
    bl      fw_get_inst_prm
    cmp     r0, #0
    it      gt
    movgt   r4, r0
7:
    mov     r0, r4
    add     sp, #8
    pop     {{r4, r5, r6, r7, r8, pc}}
8:
    b.w     fw_get_user_sample_id

    .balign 2
    .thumb_func
    .global tramp_smpl_edit_hdr
tramp_smpl_edit_hdr:
    @ Hooked at 0x6007C468: r0=widget, r1=0x6021EBC8 (stock), r2=1
    movw    ip, #0x2af8
    movt    ip, #0x60fc
    ldr.w   ip, [ip]
    cmp.w   ip, #1
    blo     1f
    cmp.w   ip, #4
    bhi     1f
    sub.w   ip, ip, #1
    movs    r3, #12
    movw    r1, #0x{smpl_edit_descs_va & 0xFFFF:04x}
    movt    r1, #0x{(smpl_edit_descs_va >> 16) & 0xFFFF:04x}
    mla     r1, ip, r3, r1
1:
    b.w     fw_list_set_descs

    .balign 2
    .thumb_func
    .global tramp_pad_audio_gate
tramp_pad_audio_gate:
    @ Hooked at 0x60099B16 (stock: ldrb.w r0, [r0, #0x114]):
    @ Suppress stock physical pad audio when SMPL CHOP is active with a loaded sample
    @ (except MODE_SRC=6 where tapping pads switches & auditions source pads).
    movw    r1, #0x{chop_state_va & 0xFFFF:04x}
    movt    r1, #0x{(chop_state_va >> 16) & 0xFFFF:04x}
    ldrb    r2, [r1, #0]        @ g_chop.active
    cbz     r2, 1f
    ldrb    r2, [r1, #2]        @ g_chop.valid
    cbz     r2, 1f
    ldrb    r2, [r1, #3]        @ g_chop.mode
    cmp     r2, #6              @ MODE_SRC?
    beq     1f
    movs    r0, #0
    bx      lr
1:
    ldrb.w  r0, [r0, #0x114]
    bx      lr
"""
    drum_p2_raw, drum_p2_syms = assemble_thumb(drum_p2_va, drum_p2_asm, drum_p2_ext)
    drum_p2_code = pad_nops(drum_p2_raw, (len(drum_p2_raw) + 3) & ~3)
    assert drum_p2_va + len(drum_p2_code) <= 0x60FC3100, "DRUM-PHASE2-HOOKS exceeds cave end 0x60FC3100"
    print(f"[+] Drum Phase 2 hooks @0x{drum_p2_va:08X}: {len(drum_p2_raw)} bytes (free tail: {0x60FC3100 - (drum_p2_va + len(drum_p2_code))} B)")
    for k, v in drum_p2_syms.items():
        if not k.startswith("$"):
            print(f"    {k:28s} = 0x{v:08X}")

    all_ext.update(drum_env_syms)
    all_ext.update(drum_p2_syms)
    all_ext["sample_del_wmt_next"] = 0x600877F4

    patch_defs = [
        # Cave & Tables
        (
            "CAVE-01",
            0x60FC0000,
            len(cave_code),
            cave_code.hex(),
            "trampoline",
            "code",
            "SDRAM trampoline cave in zero-padded region (atomic track lookup, bank toggle, LED, drum-mute, knob helpers, NARROW-REG router writers).",
        ),
        (
            "MENU-MIDI-TBL",
            0x60FC0400,
            len(sys_midi_table),
            sys_midi_table.hex(),
            "menu",
            "table",
            "Extended 23-entry SYSTEM(MIDI) ITEM_LIST_PRM table adding Track Ch5..8 (rel_pid 0x00F..0x012).",
        ),
        (
            "MENU-MIDI-DESC",
            0x6021A214,
            8,
            struct.pack("<II", 0x60FC0400, 23).hex(),
            "menu",
            "table",
            "Repoint SYSTEM(MIDI) descriptor from (0x60219F48, 19) to (0x60FC0400, 23).",
        ),
        # Track selection hooks
        (
            "UI-03",
            0x60035CBC,
            8,
            asm_at(0x60035CBC, "bl tramp_sel_main\nbl fw_select_track", 8),
            "panel",
            "hook",
            "HandleButtonPress SEL1..4: resolve track 0..7 with bank toggle (including [SHIFT]+[SEL1..4] EDIT mode toggle) and update r4.",
        ),
        (
            "UI-DIRTY-FDR",
            0x60035E74,
            4,
            "41f05101",
            "ui",
            "code",
            "HandleButtonPress SEL1..4: set UI dirty bit 4 (0x10) to immediately trigger UpdateFaderRgbLeds on bank/track selection.",
        ),
        (
            "SEL-03",
            0x60065E04,
            6,
            asm_at(0x60065E04, "bl tramp_sel_empty", 6),
            "sel",
            "hook",
            "HandleEmptyTrackScreen SEL1..4: query IsTrackAllocated for track candidate without mutating bank state.",
        ),
        # SEL1..4 LED & Fader RGB LED bank hooks
        (
            "LED-SEL-CHECK",
            0x600977C0,
            4,
            asm_at(0x600977C0, "bl tramp_sel_led_check", 4),
            "led",
            "hook",
            "UpdateSelButtonLeds: illuminate/flash selected button for tracks 1..4; invert (other 3 illuminated/flashing, selected dark) for tracks 5..8.",
        ),
        (
            "LED-SEL-BLINK",
            0x600977CC,
            4,
            asm_at(0x600977CC, "add.w r0, r6, #0x90", 4),
            "led",
            "code",
            "UpdateSelButtonLeds: query blink state 0x90 + CurTrack (r6 = 0..7).",
        ),
        # UpdateFaderRgbLeds banked fader loop (record/flash fidelity in bank B)
        (
            "FDR-BANK-LOOPBASE",
            0x6009920A,
            2,
            asm_at(0x6009920A, "movs r7, #0", 2),
            "led",
            "code",
            "UpdateFaderRgbLeds entry: stock initialises the LED mode constant in r7 (`movs r7, #3`); r7 becomes the physical loop base 0..3 (mode #3 is re-supplied at FDR-LED-ID2), r4 is rebuilt per iteration as the banked track by FDR-BANK-TOP.",
        ),
        (
            "FDR-BANK-TOP",
            0x60099212,
            6,
            asm_at(0x60099212, "bl tramp_fdr_top\nb.n fdr_loop_top", 6),
            "led",
            "hook",
            "UpdateFaderRgbLeds loop head: r4 = physical fader + bank, bank = bit 2 of the selected-track byte *[*r5+0x10] == 0x61800200 (same lockless byte the stock gate at 0x60099224 tests), then r0 = record base + r4 * 0x100. Makes the record-mode/colour reads, the pid 0x90+r4 state queries (0x90..0x97 all dispatched, 0x6003395C..0x60033998), the record-armed query 0x60031b2c (u16 table @0x20004C84, 8 wide) and the selected/record gate bank-aware, so the selected fader flashes red for tracks 5..8.",
        ),
        (
            "FDR-BANK-BOT",
            0x600992BC,
            4,
            asm_at(0x600992BC, "bl tramp_fdr_bot", 4),
            "led",
            "hook",
            "UpdateFaderRgbLeds loop bottom: advance the physical loop base (r7 += 1) and leave the flags of `cmp r7, #4` for the stock `blt 0x60099212` at 0x600992C0 - the loop body's r4 is rebuilt at the head, so this is stock's own loop control applied to the physical fader: 4 iterations, one per fader, in either bank. Advancing the banked r4 here and masking it back would wrap the counter (r4 == 4 in bank A looks like bank B) and spin the ISR forever.",
        ),
        (
            "FDR-LED-ID1",
            0x600992B0,
            4,
            asm_at(0x600992B0, "bl tramp_fdr_ledid1", 4),
            "led",
            "hook",
            "UpdateFaderRgbLeds steady-colour LED write: fader RGB LED id = 0x1c + (r4 - bank). With banked r4 the stock 0x1c + r4 would hit pad LED ids 0x20..0x23.",
        ),
        (
            "FDR-LED-ID2",
            0x60099356,
            6,
            asm_at(0x60099356, "bl tramp_fdr_ledid2\nb.n fdr_led_id2_ret", 6),
            "led",
            "hook",
            "UpdateFaderRgbLeds flash/red LED write: same unbanked LED id, plus the LED mode #3 that stock took from r7 (`mov r1, r7`), now that r7 is the loop base.",
        ),
        # Drum-pad mute combo (SEL1..4 + Pad)
        (
            "MUTE-LED-BANK",
            0x60097AE8,
            6,
            asm_at(0x60097AE8, "bl tramp_drum_mute_led", 6),
            "sel",
            "hook",
            "UpdatePadLeds drum-mute check: offset track index r4 by active bank (CurTrack & 4).",
        ),
        (
            "SEL-01",
            0x60097AF4,
            8,
            "641c042cf1db3720",
            "sel",
            "code",
            "UpdatePadLeds drum-mute loop bound: clamp physical button scan to 4 (SEL1..4, IDs 6..9).",
        ),
        (
            "MUTE-PAD-BANK",
            0x600377F6,
            6,
            asm_at(0x600377F6, "bl tramp_drum_mute_press", 6),
            "sel",
            "hook",
            "HandlePadPress drum-mute check: offset track index r6 by active bank (CurTrack & 4).",
        ),
        # Hardware Volume Faders (LEVEL1..4) & MULTI Knobs (C1..C4)
        (
            "FDR-ITCM-TBB",
            0x60337742,
            6,
            "06161616169a",
            "mixer",
            "table",
            "ITCM fader TBB table (exec 0x01024FA6): route fader IDs 1..4 to unified banked handler at 0x01024FD2.",
        ),
        (
            "FDR-ITCM-CODE",
            0x6033776E,
            128,
            itcm_code.hex(),
            "mixer",
            "code",
            "ITCM unified bank-aware fader handler (0x01024FD2) and MULTI knob helper (0x01025010).",
        ),
        (
            "KNOB-ITCM-MULTI",
            0x60337800,
            4,
            asm_at(0x01025064, "b.w itcm_knob_multi", 4),
            "mixer",
            "hook",
            "ITCM MULTI knob handler (exec 0x01025064): jump to bank-aware track resolver.",
        ),
        (
            "KNOB-UI-MULTI1",
            0x60036A16,
            4,
            asm_at(0x60036A16, "bl tramp_knob_multi_r6", 4),
            "mixer",
            "hook",
            "HandleKnobEvent MULTI popup (0x60036A16): offset knob track index by active bank (CurTrack & 4) before Single-mode merge target 0x60036A1A.",
        ),
        (
            "KNOB-UI-MULTI2",
            0x60036B6A,
            4,
            asm_at(0x60036B6A, "bl tramp_knob_multi_r5", 4),
            "mixer",
            "hook",
            "HandleKnobEvent MULTI edit (0x60036B6A): offset knob track index r5 by active bank (CurTrack & 4) before Single-mode merge target 0x60036B6E.",
        ),
        # Menu Parameter Providers: 1. RECORDING: RecSource (GPID 0x23F)
        (
            "PRM-RECSRC-FMT",
            0x6006F088,
            18,
            asm_at(0x6006F088, "adds r6, #1\nldr r0, [r4]", 18),
            "menu",
            "code",
            "RecSource FormatValue: map UI index 0..9 linearly to engine value 1..10 (PC, TRK1..8, MIXOUT).",
        ),
        (
            "PRM-RECSRC-GET",
            0x6006F1F8,
            32,
            asm_at(0x6006F1F8, "subs r0, #1\nit mi\nmovmi r0, #0", 32),
            "menu",
            "code",
            "RecSource GetValue: map engine value 0..10 to UI index max(0, val - 1).",
        ),
        (
            "PRM-RECSRC-SET",
            0x6006F258,
            16,
            asm_at(0x6006F258, "adds r6, #1", 16),
            "menu",
            "code",
            "RecSource SetValue: map UI index 0..9 to engine value idx + 1.",
        ),
        (
            "PRM-RECSRC-MAX",
            0x6006F334,
            2,
            "0920",
            "menu",
            "code",
            "RecSource GetMinMax: increase max UI index from 5 to 9 (10 options: PC, TRK1..8, MIXOUT).",
        ),
        # Menu Parameter Providers: 2. SCATTER EDIT: Position (GPID 0x483)
        (
            "PRM-SCATPOS-FMT",
            0x6007D0C4,
            18,
            asm_at(0x6007D0C4, "adds r7, #1\nldr r0, [r5]", 18),
            "menu",
            "code",
            "Scatter Position FormatValue: map UI index 0..9 to engine value 1..10 (PC, TRK1..8, MIXOUT).",
        ),
        (
            "PRM-SCATPOS-GET",
            0x6007D214,
            30,
            asm_at(0x6007D214, "subs r0, #1\nit mi\nmovmi r0, #0", 30),
            "menu",
            "code",
            "Scatter Position GetValue: map engine value 0..10 to UI index max(0, val - 1).",
        ),
        (
            "PRM-SCATPOS-SET",
            0x6007D262,
            14,
            asm_at(0x6007D262, "add.w r8, r8, #1", 14),
            "menu",
            "code",
            "Scatter Position SetValue: map UI index 0..9 to engine value idx + 1.",
        ),
        (
            "PRM-SCATPOS-MAX",
            0x6007D32C,
            2,
            "0920",
            "menu",
            "code",
            "Scatter Position GetMinMax: increase max UI index from 5 to 9.",
        ),
        # Menu Parameter Providers: 3. SCAT EDIT: DT1 Pos (GPID 0x4B6)
        (
            "PRM-SCATDT1-FMT",
            0x6007E9F0,
            18,
            asm_at(0x6007E9F0, "cmp r7, #1\nit ge\naddge r7, #1\nldr r0, [r5]", 18),
            "menu",
            "code",
            "Scat DT1 Pos FormatValue: map UI index 0..10 to engine value (OFF=0, PC=2, TRK1..8=3..10, MIXOUT=11).",
        ),
        (
            "PRM-SCATDT1-GET",
            0x6007EB3A,
            24,
            asm_at(0x6007EB3A, "cmp r1, #2\nite ge\nsubge r1, #1\nmovlt r1, #0", 24),
            "menu",
            "code",
            "Scat DT1 Pos GetValue: map engine value 0..11 to UI index 0..10.",
        ),
        (
            "PRM-SCATDT1-SET",
            0x6007EB86,
            14,
            asm_at(0x6007EB86, "cmp.w r8, #1\nit ge\naddge.w r8, r8, #1", 14),
            "menu",
            "code",
            "Scat DT1 Pos SetValue: map UI index 0..10 to engine value (idx >= 1 ? idx + 1 : 0).",
        ),
        (
            "PRM-SCATDT1-MAX",
            0x6007EC50,
            2,
            "0a20",
            "menu",
            "code",
            "Scat DT1 Pos GetMinMax: increase max UI index from 6 to 10 (11 options: OFF, PC, TRK1..8, MIXOUT).",
        ),
        # Menu Parameter Providers: 4. TRACK(GEN): Insert (GPID 0x402)
        (
            "PRM-TRKINS-FMT",
            0x60070558,
            18,
            asm_at(0x60070558, "cmp r7, #1\nit ge\naddge r7, #6\nldr r0, [r4]", 18),
            "menu",
            "code",
            "Track Insert FormatValue: map UI index 0..10 to engine value (0=OFF, 7..9=PC IN, 10..16=TRACK1..7).",
        ),
        (
            "PRM-TRKINS-GET",
            0x6007078E,
            24,
            asm_at(0x6007078E, "subs r0, r3, #6\nit le\nmovle r0, #0", 24),
            "menu",
            "code",
            "Track Insert GetValue: map engine value 0..16 to UI index max(0, val - 6).",
        ),
        (
            "PRM-TRKINS-SET",
            0x600708C6,
            14,
            asm_at(0x600708C6, "cmp r5, #1\nit ge\naddge r5, #6", 14),
            "menu",
            "code",
            "Track Insert SetValue: map UI index 0..10 to engine value (idx >= 1 ? idx + 6 : 0).",
        ),
        # Project Load Track Preservation (tracks 5..8 survive the stock wipe loop)
        (
            "PRJ-LOAD-PRESERVE",
            0x6005B422,
            16,
            asm_at(0x6005B422, "b.w target_exit", 16),
            "project",
            "hook",
            "Project load: preserve tracks 5..8 clips/allocations (bypass stock wiping loop).",
        ),
        (
            "PRJ-POST-LOAD",
            0x60046424,
            6,
            asm_at(0x60046424, "b.w tramp_prj_load_init\nnop", 6),
            "project",
            "hook",
            "Project load completion exit (tail-calls the enclosing epilogue): init tracks 5..8 to unity 255 via fw_set_param + fw_set_dsp_param. Required: without it the upper bank starts at zero volume.",
        ),
        # Sequencer Active Track Count (4 -> 8)
        (
            "SEQ-TRK-CNT",
            0x60342EBA,
            2,
            "0822",
            "seq",
            "code",
            "Sequencer constructor: set active track count from 4 to 8 (movs r2, #8).",
        ),
        # External MIDI RX Track Channel Loop & Channel-Change All-Sound-Off (4 -> 8)
        (
            "MIDI-RX-TRK-CNT",
            0x603234B6,
            2,
            "082c",
            "midi",
            "code",
            "ITCM incoming MIDI RX track-channel loop (exec 0x01010D1A): extend Track Ch1..4 scan to Track Ch1..8 (cmp r4, #4 -> cmp r4, #8).",
        ),
        (
            "MIDI-CH-ALLOFF",
            0x6003D674,
            20,
            asm_at(0x6003D674, "sub.w r5, r0, #0xdb\ncmp r5, #8\nbhs midi_ch_alloff_exit", 20),
            "midi",
            "code",
            "SYSTEM(MIDI) Track Ch edit All-Sound-Off check (0x6003D674): accept GPID 0xDB..0xE2 (Track Ch1..8) instead of 0xDB..0xDE.",
        ),
        # USB Vendor Mode 20-Channel Audio Output (Interface 2 AltSetting 1)
        (
            "USB-DESC-CH",
            0x602CA730,
            1,
            "14",
            "usb",
            "table",
            "USB High-Speed Vendor Audio Streaming IN Format Type I descriptor bNrChannels: 10 (0x0A) -> 20 (0x14).",
        ),
        (
            "USB-DESC-PKT",
            0x602CA741,
            2,
            "0c02",
            "usb",
            "table",
            "USB High-Speed Vendor Isochronous IN Endpoint (0x8E) wMaxPacketSize: 264 (0x0108) -> 524 (0x020C).",
        ),
        (
            "USB-INIT-CH",
            0x600AADE6,
            2,
            "1426",
            "usb",
            "code",
            "USB_OnBusResetOrConnect: configure hardware USB Audio IN bridge (0x40080900) for 20 channels (movs r6, #0x14) in Vendor mode.",
        ),
        (
            "USB-INIT-FIFO3",
            0x600AADE8,
            2,
            "0625",
            "usb",
            "code",
            "USB_OnBusResetOrConnect: configure hardware USB Audio IN bridge FIFO parameter 3 = 6 (movs r5, #6) for 20 channels.",
        ),
        (
            "USB-INIT-BURST",
            0x600AADEE,
            2,
            "4821",
            "usb",
            "code",
            "USB_OnBusResetOrConnect: configure hardware USB Audio IN bridge burst size = 72 bytes (movs r1, #0x48) for 20 channels.",
        ),
        (
            "USB-IF1-BURST",
            0x600AB4AC,
            2,
            "4826",
            "usb",
            "code",
            "USB_OnSetInterface: configure hardware USB Audio IN bridge burst size = 72 bytes (movs r6, #0x48) in Vendor mode.",
        ),
        (
            "USB-IF2-CH",
            0x600AB51E,
            2,
            "1422",
            "usb",
            "code",
            "USB_OnSetInterface: configure hardware USB Audio IN bridge (0x40080900) for 20 channels (moveq r2, #0x14) when Interface 2 AltSetting 1 is selected in Vendor mode.",
        ),
        (
            "USB-IF2-FIFO3",
            0x600AB58C,
            2,
            "0622",
            "usb",
            "code",
            "USB_OnSetInterface: configure hardware USB Audio IN bridge FIFO parameter 3 = 6 (moveq r2, #6) when Interface 2 AltSetting 1 is selected in Vendor mode.",
        ),
        # Splash Screen Model Name String ("Roland MC-107")
        (
            "STR-NAME-02",
            0x6004F1D4,
            17,
            b"Roland MC-107   \x00".hex(),
            "ui",
            "table",
            "LCD splash screen line 1: 'Roland MC-101   ' -> 'Roland MC-107   '.",
        ),
        # Phase 1: MC-707-Depth Drum Pad Editing (11 sections: PAD, INST, WMT1..4, TVF/TVA, PITCH/TVF/TVA ENV, EQ)
        (
            "DRUM-ENV-CLIP-HOOK",
            drum_env_tramp_va,
            len(drum_env_code),
            drum_env_code.hex(),
            "drum",
            "code",
            "Trampoline extending Pad Edit OnCreate clip registration to Pitch Env (trk+0x104), TVF Env (trk+0x10D), and TVA Env (trk+0x116).",
        ),
        (
            "DRUM-PAD-EDIT-TBL",
            drum_pad_edit_va,
            len(drum_pad_edit_blob),
            drum_pad_edit_blob.hex(),
            "drum",
            "table",
            "11-section MC-707-depth Drum Pad Edit descriptor array, 147 new ITEM_LIST_PRM entries (INST GEN, WMT1..4, TVF/TVA, Pitch/TVF/TVA Env), and LCD labels.",
        ),
        (
            "PAD-EDIT-ENV-CLIP",
            0x6004E108,
            4,
            asm_at(0x6004E108, "bl tramp_pad_edit_env_clip", 4),
            "drum",
            "hook",
            "Pad Edit OnCreate: hook SetClipIndex(track+0xFB) to also register active clip on Pitch/TVF/TVA Env providers.",
        ),
        (
            "PAD-EDIT-SEC-CNT",
            0x6004E12A,
            2,
            struct.pack("<H", 0x2200 | drum_pad_sec_cnt).hex(),
            "drum",
            "code",
            "Pad Edit OnCreate: widen section count passed to 0x6006DB94 from 3 to 11.",
        ),
        (
            "PAD-EDIT-SEC-PTR",
            0x6004E1A8,
            4,
            struct.pack("<I", drum_pad_edit_va).hex(),
            "drum",
            "table",
            "Pad Edit OnCreate: repoint section descriptor array literal from 0x6021E2EC to 0x60FC1000.",
        ),
        (
            "WMT4-FMT-BUGFIX",
            0x6008BB0A,
            4,
            "424600bf",
            "drum",
            "code",
            "DrumInst::FormatVal (WMT4 Wave Group ID rel_pid 0x61): fix stock mov.w r2, #-1 bug -> mov r2, r8; nop (pass pad_idx like WMT1..3).",
        ),
        (
            "WMT4-SET-BUGFIX",
            0x6008BE04,
            4,
            "1a4600bf",
            "drum",
            "code",
            "DrumInst::SetVal (WMT4 Wave Group ID rel_pid 0x61): fix stock mov.w r2, #-1 bug -> mov r2, r3; nop (pass pad_idx like WMT1..3).",
        ),
        # Phase 2+3: Direct SD WAVE FILE import & SAMPLE EDIT for WMT1..4 + SAMPLE DELETE WMT1..4 audit fix
        (
            "PAD-SND-MENU-TBL",
            pad_snd_menu_va,
            len(pad_snd_menu_blob),
            pad_snd_menu_blob.hex(),
            "drum",
            "table",
            "13-entry Drum Pad SOUND menu (relocated to CHOP-CAVE-0) adding WMT1..4 WAVE, WMT1..4 SMPL, and SMPL CHOP (Screen 0x68).",
        ),
        (
            "SMPL-EDIT-DESCS",
            smpl_edit_descs_va,
            len(smpl_edit_descs_blob),
            smpl_edit_descs_blob.hex(),
            "drum",
            "table",
            "4 USER SAMPLE section descriptors ('WMT1 SAMPLE:'..'WMT4 SAMPLE:') reusing stock 8-entry table 0x602D7BE4.",
        ),
        (
            "DRUM-PHASE2-HOOKS",
            drum_p2_va,
            len(drum_p2_code),
            drum_p2_code.hex(),
            "drum",
            "code",
            "Phase 2+3 trampolines: SOUND menu WMT target capture/clear, non-destructive WMT1..4 WAV binder, SAMPLE DELETE WMT1..4 L/R scanner, and WMT1..4 Sample Edit resolver/header.",
        ),
        (
            "PAD-SND-HDR-SPLIT",
            0x6006A376,
            2,
            "0728",
            "drum",
            "code",
            "Drum Pad SOUND menu header split: cmp r0, #3 -> #7 so PRESET/PROJECT/WAVE FILE/WMT1..4 WAVE show BROWSER and PAD EDIT/WMT1..4 SMPL/SMPL CHOP show EDIT.",
        ),
        (
            "PAD-SND-MENU-PTR1",
            0x6006A430,
            4,
            struct.pack("<I", pad_snd_menu_va).hex(),
            "drum",
            "table",
            "Drum Pad SOUND menu draw literal: repoint 0x6021C168 -> 0x60FC2C00.",
        ),
        (
            "PAD-SND-MENU-PTR2",
            0x6006A648,
            4,
            struct.pack("<I", pad_snd_menu_va).hex(),
            "drum",
            "table",
            "Drum Pad SOUND menu Enter literal: repoint 0x6021C168 -> 0x60FC2C00.",
        ),
        (
            "PAD-SND-CNT-DIAL",
            0x6006A46C,
            24,
            asm_at(
                0x6006A46C,
                "cmp.w ip, #1\nit eq\nmoveq r0, #5\ncmp.w ip, #2\nit eq\nmoveq r0, #2\ncmp.w ip, #3\nit eq\nmoveq r0, #13",
                24,
            ),
            "drum",
            "code",
            "Drum Pad SOUND menu dial scroll bound: widen mode 3 (Drum Pad) item count from 5 to 13 while keeping Drum Kit = 5 and Looper = 2.",
        ),
        (
            "PAD-SND-CNT-LEFT",
            0x6006A4FA,
            24,
            asm_at(
                0x6006A4FA,
                "cmp.w ip, #1\nit eq\nmoveq r1, #5\ncmp.w ip, #2\nit eq\nmoveq r1, #2\ncmp.w ip, #3\nit eq\nmoveq r1, #13",
                24,
            ),
            "drum",
            "code",
            "Drum Pad SOUND menu LEFT button bound: widen mode 3 (Drum Pad) item count from 5 to 13.",
        ),
        (
            "PAD-SND-CNT-RIGHT",
            0x6006A546,
            24,
            asm_at(
                0x6006A546,
                "cmp.w ip, #1\nit eq\nmoveq r1, #5\ncmp.w ip, #2\nit eq\nmoveq r1, #2\ncmp.w ip, #3\nit eq\nmoveq r1, #13",
                24,
            ),
            "drum",
            "code",
            "Drum Pad SOUND menu RIGHT button bound: widen mode 3 (Drum Pad) item count from 5 to 13.",
        ),
        (
            "PAD-SND-CLR-HOOK",
            0x6006A574,
            4,
            asm_at(0x6006A574, "bl tramp_snd_clear_wmt", 4),
            "drum",
            "hook",
            "SOUND menu Enter handler entry: clear g_pad_smpl_wmt_target (0x602D2AF8) and g_pad_wav_wmt_target (0x602D2AFC) before track-type dispatch.",
        ),
        (
            "PAD-SND-ENTER-HOOK",
            0x6006A5E0,
            4,
            asm_at(0x6006A5E0, "bl tramp_pad_snd_enter", 4),
            "drum",
            "hook",
            "Drum Pad SOUND menu Enter: record target WMT slot when opening Screen 0x4A (WAVE FILE) or Screen 0x5D (SAMPLE EDIT).",
        ),
        (
            "PAD-WAV-BIND-WMT",
            0x6002132E,
            4,
            asm_at(0x6002132E, "bl tramp_bind_pad_wav_wmt", 4),
            "drum",
            "hook",
            "Async cmd 4 (WAVE FILE import on Drum track): route non-destructive WMT1..4 imports without resetting other pad layers.",
        ),
        (
            "SAMPLE-DEL-WMT-FIX",
            0x600877D6,
            30,
            asm_at(0x600877D6, "bl tramp_sample_del_wmt_scan\nb sample_del_wmt_next", 30),
            "drum",
            "hook",
            "SAMPLE DELETE used-sample scanner: fix stock bug that always read WMT1.WAV_NUM_L instead of WMT[r5].WAV_NUM_L/R across layers 1..4.",
        ),
        (
            "SMPL-EDIT-GET1",
            0x6007C3BE,
            4,
            asm_at(0x6007C3BE, "bl tramp_smpl_edit_get_id", 4),
            "drum",
            "hook",
            "Screen 0x5D (SAMPLE EDIT) OnCreate: fetch User Sample ID from targeted WMT1..4 layer (fallback stock for Tone/WMT1).",
        ),
        (
            "SMPL-EDIT-GET2",
            0x6007C504,
            4,
            asm_at(0x6007C504, "bl tramp_smpl_edit_get_id", 4),
            "drum",
            "hook",
            "Screen 0x5D (SAMPLE EDIT) OnPadSwitch: fetch User Sample ID from targeted WMT1..4 layer.",
        ),
        (
            "SMPL-EDIT-HDR",
            0x6007C468,
            4,
            asm_at(0x6007C468, "bl tramp_smpl_edit_hdr", 4),
            "drum",
            "hook",
            "Screen 0x5D (SAMPLE EDIT) OnCreate: show 'WMT1 SAMPLE:'..'WMT4 SAMPLE:' header when editing a Drum Pad WMT layer.",
        ),
        # Sample Chop Milestone 1: Relocated 105-entry Screen Table + Screen 0x68 (SMPL CHOP) C module
        (
            "SCR-TBL-EXT",
            scr_tbl_va,
            len(scr_tbl_blob),
            scr_tbl_blob.hex(),
            "chop",
            "table",
            "Relocated 105-entry Screen Table (stock 0x00..0x67 + new Screen 0x68 SMPL CHOP + NULL) in verified zero padding CHOP-CAVE-0.",
        ),
        (
            "SCR-68-DESC",
            scr_68_desc_va,
            len(scr_68_desc_blob),
            scr_68_desc_blob.hex(),
            "chop",
            "table",
            "Screen 0x68 (SMPL CHOP) descriptor pointing to sample_chop_event @ 0x602D4FE1.",
        ),
        (
            "SCR-TBL-PTR1",
            0x6001D4C4,
            4,
            struct.pack("<I", scr_tbl_va).hex(),
            "chop",
            "table",
            "Screen Init literal: repoint screen table 0x601D06D4 -> 0x60FC2400.",
        ),
        (
            "SCR-TBL-PTR2",
            0x6001D7A8,
            4,
            struct.pack("<I", scr_tbl_va).hex(),
            "chop",
            "table",
            "Screen Switch literal: repoint screen table 0x601D06D4 -> 0x60FC2400.",
        ),
        (
            "SCR-TBL-PTR3",
            0x6001DF1C,
            4,
            struct.pack("<I", scr_tbl_va).hex(),
            "chop",
            "table",
            "DispatchScreenEvent literal: repoint screen table 0x601D06D4 -> 0x60FC2400.",
        ),
        (
            "SCR-TBL-PTR4",
            0x60026008,
            4,
            struct.pack("<I", scr_tbl_va).hex(),
            "chop",
            "table",
            "Screen LED/Key map literal: repoint screen table 0x601D06D4 -> 0x60FC2400.",
        ),
        (
            "CHOP-CODE",
            chop_code1_va,
            len(chop_code_blob),
            chop_code_blob.hex(),
            "chop",
            "code",
            "Sample Chop UI/waveform C module (16x2 CGRAM waveform + slice/transient markers, zoom, audition, Screen 0x68 event loop) in CHOP-CAVE-1.",
        ),
        (
            "CHOP-SLICE",
            chop_code2_va,
            len(chop_slice_blob),
            chop_slice_blob.hex(),
            "chop",
            "code",
            "Sample Chop Wave RAM truncator, User Sample slot creator, Core 1 IPC sync, and Auto/Manual Pad assignment in CHOP-CAVE-2.",
        ),
        (
            "CHOP-DETECT",
            chop_code3_va,
            len(chop_detect_blob),
            chop_detect_blob.hex(),
            "chop",
            "code",
            "Sample Chop equal-step slicer (1/4/8/16/32), Auto Transient onset detector, and manual slice add/delete/nudge in CHOP-CAVE-3.",
        ),
        (
            "CHOP-PAD-AUDIO-GATE",
            0x60099B16,
            4,
            asm_at(0x60099B16, "bl tramp_pad_audio_gate", 4),
            "chop",
            "hook",
            "Physical Pad audio gate (0x60099AF0): suppress stock kit pad playback while SMPL CHOP is active so Pads 1..16 audition slices cleanly.",
        ),
        # Button Navigation Mode (Hold '>' to enter; '<'/'>' = VALUE dial w/ repeat, TEMPO = ENTER, blink 4 LEDs; SHIFT+'<' = exit)
        (
            "NAV-CODE",
            nav_code_va,
            len(nav_code_blob),
            nav_code_blob.hex(),
            "nav",
            "code",
            "Button Navigation Mode C module (hold '>' enter, '<'/'>' VALUE dial w/ auto-repeat, TEMPO->ENTER, SHIFT+'<' exit) in verified zero-padded cave 0x60FB0200.",
        ),
        (
            "NAV-LED-TRAMP",
            nav_tramp_va,
            len(nav_tramp_code),
            nav_tramp_code.hex(),
            "nav",
            "code",
            "Button Navigation Mode LED blink override trampoline for MEAS_L(3), MEAS_R(4), TEMPO(15), EXIT(16).",
        ),
        (
            "NAV-TICK-HOOK",
            0x6002EC24,
            4,
            asm_at(0x6002EC24, "bl nav_tick_and_drain", 4),
            "nav",
            "hook",
            "Main UI loop tick hook (0x6002EC24): check 600ms '>' hold and '<'/'>' auto-repeat before draining UI event queue.",
        ),
        (
            "NAV-DISPATCH-HOOK",
            0x6001DE66,
            4,
            asm_at(0x6001DE66, "bl nav_dispatch_event", 4),
            "nav",
            "hook",
            "Central UI event dispatch hook (0x6001DE66): remap '<'/'>'->VALUE dial and TEMPO->ENTER while Button Nav Mode is active.",
        ),
        (
            "NAV-LED-HOOK",
            0x6001E6B8,
            4,
            asm_at(0x6001E6B8, "b.w tramp_nav_led_override", 4),
            "nav",
            "hook",
            "fw_set_led entry hook (0x6001E6B8): keep '<', '>', 'TEMPO', and 'EXIT' LEDs blinking continuously while Button Nav Mode is active.",
        ),
    ]

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["description"] = (
        "MC-101 v1.82 8-track patch-site manifest. Includes all active patches (track bank toggle, "
        "fader/knob bank switching, LED bank switching, SYSTEM(MIDI) Track Ch1..8, 8-track parameter ranges, "
        "20-channel USB Vendor audio output; NARROW-REG router routing DISABLED - hardware write-mask fuse, "
        "see 8-TRACK.md §11.9) "
        "plus verification anchors."
    )

    # Keep non-overlapping verification sites from existing manifest
    patched_ranges = [(va, va + length) for _, va, length, _, _, _, _ in patch_defs]

    def overlaps(va: int, length: int) -> bool:
        return any(not (va + length <= s or va >= e) for s, e in patched_ranges)

    kept_verify_sites = []
    for s in manifest.get("sites", []):
        if s.get("patch") is not None:
            continue
        va = int(s["address"], 0)
        length = int(s["length"])
        if not overlaps(va, length):
            kept_verify_sites.append(s)

    new_sites = []
    for site_id, va, length, patch_hex, area, kind, desc in patch_defs:
        expected_hex = read_stock(image, va, length)
        assert len(bytes.fromhex(patch_hex)) == length, f"{site_id}: patch length mismatch"
        new_sites.append(
            {
                "id": site_id,
                "address": f"0x{va:08X}",
                "length": length,
                "expected": expected_hex,
                "patch": patch_hex,
                "area": area,
                "kind": kind,
                "phase": 1,
                "status": "implemented",
                "description": desc,
            }
        )

    manifest.pop("reference_sites", None)
    manifest["sites"] = new_sites + kept_verify_sites
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"[+] Wrote {MANIFEST_PATH} with {len(new_sites)} active patches and {len(kept_verify_sites)} verify sites.")


if __name__ == "__main__":
    main()
