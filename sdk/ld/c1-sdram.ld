/* Copyright 2026 Ed Coffey
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at https://mozilla.org/MPL/2.0/.
 */

/* Default Linker Script for MC-101 Core 1 Patches in Free SDRAM1 (0x6139_2000).
 *
 * - ~2.43 MiB contiguous SDRAM1 (0x6139_2000..0x615F_FFFF) above stock sdram1.bin
 *   and MC-107's relocated DSP program (0x6138_9FE0..0x6139_1BC8).
 * - Leaves 0x6160_0000..0x617F_FFFF free for mc-101-boot Core 1 patch staging.
 */

MEMORY
{
    PATCH_SDRAM1 (rwx) : ORIGIN = 0x61392000, LENGTH = 0x0026E000
}

SECTIONS
{
    .text : ALIGN(4)
    {
        *(.text.entry*)
        *(.text*)
        *(.rodata*)
        . = ALIGN(4);
    } > PATCH_SDRAM1

    .data : ALIGN(4)
    {
        *(.data*)
        . = ALIGN(4);
    } > PATCH_SDRAM1

    .bss (NOLOAD) : ALIGN(4)
    {
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
    } > PATCH_SDRAM1

    /DISCARD/ :
    {
        *(.ARM.exidx*)
        *(.ARM.extab*)
        *(.comment)
        *(.note*)
    }
}
