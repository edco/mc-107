# MC-107 Roadmap, Verification, Assumptions & Hardware Test Plan

> **Status:** Core 0 (`mc107-0.pat` in `SDRAM0_FREE`), Core 1 (`mc107-1.pat` via `c1a_stub.rs`), the web patcher (`docs/index.html`), 8-track + downmix stereo USB audio, and the extended Drum Pad Edit menu are **hardware-verified** on MC-101 v1.82. `[PROJECT]` USB Mass Storage mode, `SMPL CHOP`, `WMT1–4`, and Button Nav still need on-device spot-checks.

This document tracks the architecture, completed implementation, host verification results, assumptions, and remaining on-device hardware testing for `mc-107` and `mc-101-boot`.

---

## Architecture & Clean-Room Invariants

1. **Zero Vendor Code in Any Distributable Artifact**:
   - **Bootloader patch (`docs/mc101-boot.xorpatch`)**: Distributed as an XOR/copy/append patch (`MC1X`) over stock encrypted `RPG69_C0A_up.bin` (`3,499,416` stock ciphertext bytes copied verbatim; only the changed header bytes, the single 8-byte 3DES-ECB hook block at `0x78..0x7F`, the encrypted `mc-101-boot` payload, and the updated 64-byte footer are supplied).
   - **Runtime `.pat` files (`MC1P` v2)**: Never embed `expected` vendor bytes; verify target firmware using `expected_crc32: u32` and construct repetitive/template blocks (e.g., `build_extra_channels`) via `Copy` + targeted `WriteRaw`/`Write` records.
2. **True Memory Offsets (No `0x20` Header Confusion)**:
   - Update containers (`RPG69_C0A_up.bin` 96-byte header, `RPG69_C1A_up.bin` 32-byte header before `"QSPI"`) are parsed as `(header, body)` and stripped on load.
   - All patch addresses in `MC1P` v2 are true physical RAM addresses (`0x60xx_xxxx`, `0x010x_xxxx`, `0x61xx_xxxx`, `0x011x_xxxx`).

---

## Verified Target Memory Map (Stock `App1_Main` + `sdram1.bin`)

| Core | Region | Address Range | Size | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **Core 0** | `SDRAM0_FREE` | `0x60F8_0000..0x60FF_FFFF` | **512 KiB** | Default Core 0 mod region; within `±16 MiB` of `App1_Main` (`bl`/`b.w` reachable directly). |
| **Core 0** | `ITCM0_LO` | `0x0104_30A0..0x0105_0000` | **51.8 KiB** | Between stock ITCM0 end and MSP stack; requires 8-byte `ldr pc` veneer from `0x60xx_xxxx`. |
| **Core 0** | `ITCM0_HI` | `0x0105_0800..0x0107_FFFF` | **190 KiB** | Above initial MSP stack (`0x0105_0800`); verify unused by RTOS task stacks on hardware. |
| **Core 1** | `SDRAM1_FREE` | `0x6139_2000..0x616F_FFFF` | **~3.43 MiB** | Default Core 1 mod region (above `sdram1.bin` + relocated DSP `0x6139_1BC8`). |
| **Core 1** | `C1_STAGING` | `0x6170_0000..0x617F_FFFF` | **1.0 MiB** | Temporary `mc-101-boot` Core 1 `.pat` staging buffer (untouched by `App1_Main` before Core 1 release; freed once Core 1 boots). |
| **Core 1** | `ITCM1_FREE` | `0x0113_B084..0x0113_FFFF` | **~19.9 KiB** | Top of Core 1 ITCM1 above `idm1.bin`. |

### Potential Future Memory Map Gains

- **`ITCM0_HI` (`0x0105_0800..0x0107_FFFF`, `+190 KiB`)**: Above the initial Core 0 MSP stack up to the 512 KiB ITCM0 boundary (`0x0108_0000`). If hardware testing confirms RTOS task stacks reside in DTCM (`0x2000_0000+`) and leave `0x0105_0800..0x0107_FFFF` untouched, this adds **190 KiB** of zero-wait-state ITCM0.
- **`SDRAM0_FREE` (below `0x60F8_0000`)**: `App1_Main_decrypted.bin` ends at `0x6035_65AF`, leaving `~12.1 MiB` (`0x6035_65B0..0x60F7_FFFF`) for BSS, RTOS heap, UI buffers, and Wave RAM. Mapping `App1_Main`'s allocator upper bound could expand `SDRAM0_FREE` downward from `0x60F8_0000` while staying within `±16 MiB` of `App1_Main` for direct `bl`/`b.w`.
- **`ITCM1_FREE` (`0x0113_B084..0x0113_FFFF`, `~19.9 KiB`)**: Stock `idm1.bin` (`237 KiB`) fills `0x0110_0000..0x0113_B083` of the 256 KiB ITCM1, so this region is already essentially maxed out unless dead code/tables are carved out of `idm1.bin`.

---

## `MC1P` v2 Binary Patch Format

- **16-byte Header (little-endian)**:
  - `magic: [u8; 4]` = `b"MC1P"`
  - `version: u16` = `2`
  - `record_count: u16`
  - `flags: u32` = `0`
  - `crc32: u32` = IEEE 802.3 CRC-32 of all bytes after header (`data[16..]`)
- **Records**:
  - Common 8-byte prefix: `addr: u32`, `len: u16`, `kind: u16`
  - `kind = 0` (**`Write`**, verified surgical edit): `expected_crc32: u32` (4 B) + `patch: [u8; len]` — verifies `crc32_ieee(mem[addr..addr+len]) == expected_crc32` before writing.
  - `kind = 1` (**`Copy`**, `memmove` clone): `src_addr: u32` (4 B) — copies `len` bytes from `src_addr` to `addr`.
  - `kind = 2` (**`Fill`**, `.bss` / padding): `fill_byte: u8` + `[0; 3]` (4 B) — fills `len` bytes at `addr`.
  - `kind = 3` (**`WriteRaw`**, new code/data blob): `data: [u8; len]` — writes directly to `addr` without pre-check (for empty/uninitialized memory regions).

---

## Completed Implementation (Host-Side)

### Phase 1: `mc-101-boot`
- [x] Upgraded `MC1P` to v2 in `mc-101-boot/shared/src/firmware_cfg.rs` and `mc-101-boot/core0/src/c1a_stub.rs`.
- [x] Raised `PATCH_BUF` in `core0/src/main.rs` from `32 KiB` → `64 KiB` (SRAM0) and moved `STAGED_PATCH_ADDR` in `c1a_stub.rs` from `0x602D_2C00` (`5 KiB`) → `0x6160_0000` (`2 MiB`).
- [x] Added `[PROJECT]` USB Mass Storage mode (`usb.rs`, `msc.rs` with ping-pong buffers at `0x6100_0000` in SDRAM, `time.rs`, `sd.rs`, `button::PROJECT` boot check).
- [ ] Minimal SD-boot anti-flash-read patch (`RPG69_C0A` `TEST MENU` CRC at `0x6004_F46C` — optional; normal runtime never reads `RPG69_C0A`, and `RPG69_C1A` is already intercepted by `c1a_stub.rs`).
- [x] Removed unused `mc-1r1` leftovers (`PLAN.md`, `USB_STORAGE_HW.md`, `DISPLAY.md`) and legacy v1 `xtask` code; `mc-101-boot` builds with zero compiler warnings.

### Phase 2: Patch SDK (`sdk/`)
- [x] Commented JSON manifest compiler & verifier ([`sdk/mkpatch.py`](sdk/mkpatch.py)).
- [x] Linker scripts (`sdk/ld/c0-sdram.ld`, `c0-itcm.ld`, `c1-sdram.ld`, `c1-itcm.ld`) and stock Core 0 symbols (`sdk/ld/stock_c0.ld`).

### Phase 3: Migrated `8-track` (`mc107/`)
- [x] Incorporated `RowdyVoyeur/mc101-rex` (`sample_chop.c`, `button_nav.c`, 40+ Core 0 sites, Core 1 `ROUTING_CLAMP_PATCHES`).
- [x] Relocated all C modules and tables out of zero-pad caves into `SDRAM0_FREE` (`0x60F8_0000..0x60FC_3300`).
- [x] Eliminated `0x20` offset issues in `mc107-1.pat` and [`mc107/build.py`](mc107/build.py).
- [x] Converted `build_extra_channels` to `5 × Copy` (`0x610B_58A4`) + `10 × WriteRaw` `u16` slot IDs (zero vendor bytes).

### Phase 4: Distributable Bootloader Patch & Web Patcher (`docs/`)
- [x] Pure-Rust 3DES-ECB encryptor (`mc-101-boot/xtask/src/des3.rs`) verified against `App1_Main_boot.bin`, emitting `docs/mc101-boot.xorpatch` (`47,388` bytes, zero vendor bytes).
- [x] Static GitHub Pages browser patcher ([`docs/index.html`](docs/index.html)).
- [x] Standalone `mc-107/` repository initialized with zero vendor `.bin`/ROM files.

---

## Host Verification Results

- **`mc-101-boot` (`make test && make package`)**: Exit `0`, zero warnings.
  - `shared` tests: `2 passed; 0 failed` (`verify_and_apply_mc1p_roundtrip`, `parse_firmware_ini_both_cores`).
  - `xtask` tests: `3 passed; 0 failed` (`bl_encoding_matches_stock_preinit`, `c0a_header_matches_app0_layout`, `des3_roundtrip_and_sha256`).
  - Built `mc101-boot.bin` (`47,168` bytes), `RPG69_C0A_up.bin` (`3,546,768` bytes), and `mc101-boot.xorpatch` (`47,388` bytes, 0 vendor bytes).
- **`mc-107` patch build (`python3 mc107/build.py`)**: Exit `0`.
  - Generated `88` active Core 0 patches + `31` verify sites in `mc107/build/sites.json`.
  - Verified `mc107-0.pat` (`91` records, `17,900` bytes, `0` vendor bytes) against `App1_Main_decrypted.bin`.
  - Verified `mc107-1.pat` (`49` records, `712` bytes, `0` vendor bytes) against `sdram1.bin`.
- **SDK end-to-end test**: Compiled a test C module with `arm-none-eabi-gcc` + `sdk/ld/c0-sdram.ld`, packaged with `sdk/mkpatch.py` (`hook`, `copy`, `fill`, `write_raw`), and verified application with `verify_and_apply_mc1p_v2` (`3` records, `56` bytes).

---

## Implementation Assumptions

- **USB MSC ping-pong buffer in SDRAM (`0x6100_0000`)**: `msc.rs` ping-pong buffers are placed in SDRAM so `PATCH_BUF` can remain `64 KiB` in `SRAM0`. Assumes USB DMA can target `0x6100_0000` safely prior to `App1` launch.
- **Core 1 staging buffer at `0x6160_0000`**: `STAGED_PATCH_ADDR` uses `0x6160_0000..0x617F_FFFF` (`2 MiB`) so `SDRAM1_FREE` (`0x6139_2000..0x615F_FFFF`) remains contiguous for Core 1 patches.
- **Optional `RPG69_C0A` `TEST MENU` CRC (`0x6004_F46C`)**: Left unpatched because normal runtime never reads `RPG69_C0A` from flash, and `RPG69_C1A` flash reads are intercepted by `c1a_stub.rs`.

---

## Remaining Work (Requires On-Device Hardware Testing)

Collaborators with hardware can split and verify the remaining items in parallel:

1. **Web Patcher (`docs/index.html`) + Bootloader Flash** — **VERIFIED ON HARDWARE**:
   - Generated `mc107-sdcard.zip` from stock v1.82 `RPG69_C0A_up.bin`, flashed via `[SHIFT]` + power-on (with `RPG69_C1A` stock), and booted `mc-101-boot`.
2. **`[PROJECT]` USB Mass Storage Mode** — *pending hardware test*:
   - Hold `[PROJECT]` on power-on and verify host USB enumeration, SD card read/write integrity (`PING_PONG` at `0x6100_0000`), and clean reset/eject.
3. **`MC1P` v2 SD-Card Boot of MC-107 (`mc107-0.pat` + `mc107-1.pat`)** — **PARTIALLY VERIFIED ON HARDWARE**:
   - **Verified**: Core 0 (`SDRAM0_FREE` `0x60F8_0000..0x60FC_3300`) and Core 1 (`c1a_stub.rs` staging `0x6160_0000`) both boot with patches applied; full 8-track + downmix stereo USB audio and extended Drum Pad Edit menu confirmed working.
   - **Pending spot-checks**: `sample_chop.c` (`SMPL CHOP`), `button_nav.c` (Button Nav), `WMT1–4` per-layer WAV/Sample Edit, and `RecSource` / `ScatterPos` routing.

---

## Attribution & License

- **Ed Coffey**: `mc-101-boot` SD bootloader, `MC1P` v2 Patch SDK, 8-track Core 0/Core 1 reverse engineering & clean-room migration, `mc101-boot.xorpatch`, and web patcher.
- **RowdyVoyeur** ([`RowdyVoyeur/mc101-rex`](https://github.com/RowdyVoyeur/mc101-rex)): `sample_chop.c` (`SMPL CHOP`), `button_nav.c` (Button Navigation Mode), MC-707-depth Drum Pad Edit, WMT1–4 per-layer WAV loading & Sample Edit, and Core 1 `ROUTING_CLAMP_PATCHES`.
- Ed Coffey's original code in this repository is licensed under the **Mozilla Public License 2.0 (MPL-2.0)**.
