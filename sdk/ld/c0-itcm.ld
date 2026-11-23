/* Copyright 2026 Ed Coffey
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at https://mozilla.org/MPL/2.0/.
 */

/* Default Linker Script for MC-101 Core 0 Patches in Free ITCM0 (0x0104_30A0).
 *
 * - 51.8 KiB zero-wait-state executable RAM between stock ITCM0 end (0x0104_30A0)
 *   and the bottom of Core 0 initial MSP stack (0x0105_0000).
 * - Note: Calls from 0x60xx_xxxx into 0x010x_xxxx exceed ±16 MiB and require
 *   an 8-byte `ldr pc` / `movw+movt+bx` veneer (generated automatically by mkpatch.py).
 */

MEMORY
{
    PATCH_ITCM0 (rwx) : ORIGIN = 0x010430A0, LENGTH = 0x0000CF60
}

SECTIONS
{
    .text : ALIGN(4)
    {
        *(.text.entry*)
        *(.text*)
        *(.rodata*)
        . = ALIGN(4);
    } > PATCH_ITCM0

    .data : ALIGN(4)
    {
        *(.data*)
        . = ALIGN(4);
    } > PATCH_ITCM0

    .bss (NOLOAD) : ALIGN(4)
    {
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
    } > PATCH_ITCM0

    /DISCARD/ :
    {
        *(.ARM.exidx*)
        *(.ARM.extab*)
        *(.comment)
        *(.note*)
    }
}
