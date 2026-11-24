# MC-101 Patch SDK (`MC1P` v2)

A clean-room framework for writing **C** or **Rust** patches that run on stock MC-101 v1.82 firmware via `mc-101-boot`, without hunting for zero-padding caves or distributing any vendor bytes.

## Default Free Memory Regions

| File | Core | Address Range | Size | Use |
| :--- | :--- | :--- | :--- | :--- |
| `ld/c0-sdram.ld` | Core 0 | `0x60F8_0000..0x60FF_FFFF` | `512 KiB` | Default Core 0 code/data/BSS (direct `bl`/`b.w` from `App1_Main`) |
| `ld/c0-itcm.ld` | Core 0 | `0x0104_30A0..0x0105_0000` | `51.8 KiB` | Zero-wait-state Core 0 ITCM0 |
| `ld/c1-sdram.ld` | Core 1 | `0x6139_2000..0x616F_FFFF` | `~3.43 MiB` | Default Core 1 code/data/BSS |
| `ld/c1-itcm.ld` | Core 1 | `0x0113_B084..0x0113_FFFF` | `~20 KiB` | Zero-wait-state Core 1 ITCM1 |

You can override any `ORIGIN` / `LENGTH` in your own `.ld` / `link.x` if you identify additional free memory.

## Calling Stock Firmware Functions

Include `ld/stock_c0.ld` in your link command (`-T sdk/ld/c0-sdram.ld -T sdk/ld/stock_c0.ld`) and declare `extern` prototypes in C/Rust.

## Compiling to `.pat` (`MC1P` v2)

`sdk/mkpatch.py` takes a commented JSON manifest (`#` or `//` comments supported) + compiled ELF(s):
- ELF `PROGBITS` sections (`.text`, `.rodata`, `.data`) → `WriteRaw (3)` records
- ELF `NOBITS` sections (`.bss`) → `Fill (2)` zero records
- Manifest `hook` / `write` records → `Write (0)` verified by `expected_crc32` (never storing vendor bytes)
- Manifest `copy` / `fill` records → runtime `Copy (1)` / `Fill (2)`
