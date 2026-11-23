/* Copyright 2026 Ed Coffey
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at https://mozilla.org/MPL/2.0/.
 */

/* Default Linker Script for MC-101 Core 1 Patches in Free ITCM1 (0x0113_B084).
 *
 * - ~20 KiB zero-wait-state executable RAM at top of Core 1 ITCM1 (0x0113_B084..0x0113_FFFF).
 */

MEMORY
{
    PATCH_ITCM1 (rwx) : ORIGIN = 0x0113B084, LENGTH = 0x00004F7C
}

SECTIONS
{
    .text : ALIGN(4)
    {
        *(.text.entry*)
        *(.text*)
        *(.rodata*)
        . = ALIGN(4);
    } > PATCH_ITCM1

    .data : ALIGN(4)
    {
        *(.data*)
        . = ALIGN(4);
    } > PATCH_ITCM1

    .bss (NOLOAD) : ALIGN(4)
    {
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
    } > PATCH_ITCM1

    /DISCARD/ :
    {
        *(.ARM.exidx*)
        *(.ARM.extab*)
        *(.comment)
        *(.note*)
    }
}
