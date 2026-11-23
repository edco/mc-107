/* Copyright 2026 Ed Coffey
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at https://mozilla.org/MPL/2.0/.
 */

/* Default Linker Script for MC-101 Core 0 Patches in Free SDRAM0 (0x60F8_0000).
 *
 * - 512 KiB contiguous executable/writable SDRAM (0x60F8_0000..0x60FF_FFFF).
 * - Within ±16 MiB of stock App1_Main (0x6000_0000..0x6035_65AF), so direct
 *   4-byte Thumb-2 `bl` / `b.w` hooks work without veneers.
 * - `.bss` is emitted by `mkpatch.py` as an MC1P v2 `Fill(0)` record.
 */

MEMORY
{
    PATCH_SDRAM0 (rwx) : ORIGIN = 0x60F80000, LENGTH = 0x00080000
}

SECTIONS
{
    .text : ALIGN(4)
    {
        *(.text.entry*)
        *(.chop_entry*)
        *(.chop_slice*)
        *(.chop_detect*)
        *(.text*)
        *(.rodata*)
        . = ALIGN(4);
    } > PATCH_SDRAM0

    .data : ALIGN(4)
    {
        __sdata = .;
        *(.data*)
        . = ALIGN(4);
        __edata = .;
    } > PATCH_SDRAM0

    .bss (NOLOAD) : ALIGN(4)
    {
        __sbss = .;
        *(.chop_state*)
        *(.nav_state*)
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        __ebss = .;
    } > PATCH_SDRAM0

    /DISCARD/ :
    {
        *(.ARM.exidx*)
        *(.ARM.extab*)
        *(.comment)
        *(.note*)
    }
}
