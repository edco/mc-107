# MC-107 (8-Track + Sample Chop + Button Nav for Roland MC-101)

> **WARNING — PRELIMINARY, UNTESTED CODE:**
> This repository contains a clean-room `mc-101-boot` + `MC1P` v2 + `SDRAM0_FREE` build of MC-107. While all binaries and `.pat` files build and pass **host-side** verification checks, **this release has NOT yet been flashed or tested on MC-101 hardware**. Use at your own risk, and see [`ROADMAP.md`](ROADMAP.md) for the hardware test checklist.

Clean-room distribution of MC-107 for the Roland MC-101 (v1.82), powered by the `mc-101-boot` SD card bootloader and the `MC1P` v2 Patch SDK. **Contains zero Roland firmware bytes.**

---

## Attribution

- **Ed Coffey** — `mc-101-boot` SD card bootloader, `MC1P` v2 Patch SDK (`sdk/`), 8-track Core 0 & Core 1 reverse engineering and clean-room `SDRAM0_FREE` migration, `mc101-boot.xorpatch`, and browser patcher (`docs/index.html`).
- **RowdyVoyeur** ([`RowdyVoyeur/mc101-rex`](https://github.com/RowdyVoyeur/mc101-rex)) — `sample_chop.c` (`SMPL CHOP` with 16×2 LCD waveform, step/auto-transient/manual slicing, auditioning, and pad assignment), `button_nav.c` (Button Navigation Mode), MC-707-depth Drum Pad Edit, `WMT1–4` per-layer WAV loading & Sample Edit, and Core 1 `ROUTING_CLAMP_PATCHES`.

---

## Contents

- [`docs/`](docs/) — GitHub Pages static web patcher (`index.html`), `mc101-boot.xorpatch`, `firmware.ini`, `mc107-0.pat`, `mc107-1.pat`.
- [`patches/`](patches/) — Ready-to-use SD card files (`firmware.ini`, `mc107-0.pat`, `mc107-1.pat`).
- [`sdk/`](sdk/) — `MC1P` v2 Patch SDK (`mkpatch.py`, C/Rust linker templates for free ITCM/SDRAM regions, stock symbol headers).
- [`mc107/`](mc107/) — Clean-room C/assembly sources (`sample_chop.c`, `button_nav.c`, `gen_mc107.py`, `build.py`) that build `mc107-0.pat` and `mc107-1.pat` into free SDRAM (`0x60F8_0000+`) without zero-pad caves.
- [`ROADMAP.md`](ROADMAP.md) — Architecture, host verification results, assumptions, and remaining on-device hardware testing.

---

## Quick Start (End Users)

1. Open [`docs/index.html`](docs/index.html) (or your GitHub Pages deployment) and drop your stock v1.82 `RPG69_C0A_up.bin` to generate the bootloader-patched `RPG69_C0A_up.bin` locally in your browser.
2. Flash `RPG69_C0A_up.bin` once (hold `[SHIFT]` at power-on), leaving `RPG69_C1A` stock.
3. Copy to your SD card:
   - `/firmware/firmware.ini`
   - `/firmware/mc107/mc107-0.pat`
   - `/firmware/mc107/mc107-1.pat`
4. Power on normally to boot MC-107 (or hold `[MULTI FX]` at power-on for USB Mass Storage mode).

---

## Implemented Features

All 88 active Core 0 patches are generated reproducibly by [`mc107/gen_mc107.py`](mc107/gen_mc107.py) into `SDRAM0_FREE` (`0x60F8_0000..0x60FC_3300`), and the Core 1 DSP patch is built by [`mc107/build.py`](mc107/build.py):

- **Track selection toggle & bank switching (`UI-03`, `UI-DIRTY-FDR`, `SEL-03`, `CAVE-01`)**: Pressing the already-selected `SEL1..SEL4` button toggles between Bank A (Tracks 1–4) and Bank B (Tracks 5–8); pressing a different `SEL1..SEL4` button selects that slot within the active bank (`CurTrack & 4`). Holding `[SHIFT]` + `[SEL1..4]` enters `EDIT` (`Screen 0x36`) for that track, and pressing `[SHIFT]` + the same track button again toggles between `1..4` and `5..8`.
- **Volume faders & `MULTI` knobs (`FDR-ITCM-TBB`, `FDR-ITCM-CODE`, `KNOB-ITCM-MULTI`, `KNOB-UI-MULTI1`, `KNOB-UI-MULTI2`)**: Unified bank-aware ITCM fader handler and `C1..C4` `MULTI` knob banking so faders 1–4 and `C1..C4` control Tracks 5–8 whenever Track 5–8 is selected (`Single` knob mode also works across all 8 tracks).
- **`SEL1..SEL4` LEDs (inverted for tracks 5–8), fader RGB LEDs, & drum-pad mute**: Tracks 1–4 illuminate/flash the selected `SEL` button; Tracks 5–8 invert the illumination (the other 3 buttons are illuminated/flashing and the selected button is dark). `UpdateFaderRgbLeds` banks the fader loop natively so Bank A and Bank B display accurate track colours and flash red on the selected fader. Drum-pad mute (`SEL1..4 + Pad`) offsets its track index by the active bank.
- **Menu items, External MIDI RX, & Routing Clamps**: Extends `SYSTEM(MIDI)` with `Track Ch5..8` (`23` items total), widens incoming MIDI RX scan (`cmp r4, #8`) and All-Sound-Off checks so Tracks 5–8 receive external MIDI cleanly, unlocks `TRK1..TRK8` across `RecSource`, `Scatter Position`, `DT1 Pos`, and `Track Insert`, and lifts Core 1's 4-track routing clamps (`0x6101EDA6`, `0x6101F04E`).
- **20-Channel USB `Vendor` Audio Output**: Widens High-Speed `Vendor` USB Audio Streaming IN (Interface 2, AltSetting 1) to 20 channels (`wMaxPacketSize = 524`), configures the USB bridge (`0x40080900`) for 20 channels, and clones Track 4 L/R Core 1 DSP output slots to provide per-track stems on USB channels 11–18 (Tracks 5–8) and silenced channels 19–20.
- **8-Track Project (`.prj`) Load, Save, & Import (`PRJ-LOAD-PRESERVE`, `PRJ-POST-LOAD`)**: Preserves tracks 5–8 during project load and dispatches unity `255` to Area 2 fader params `0x4B..0x54` and the Sound Engine DSP mixer queue on project load completion.
- **MC-707-Depth Drum Pad Edit (`DRUM-ENV-CLIP-HOOK`, `DRUM-PAD-EDIT-TBL`, `PAD-EDIT-*`, `WMT4-*-BUGFIX`)**: Expands `SHIFT + PAD → PAD EDIT` from 3 sections to **11 sections** (147 newly exposed Zen-Core parameters across `INST EDIT`, `WMT1–WMT4`, `TVF/TVA`, `PITCH ENV`, `TVF ENV`, `TVA ENV`, and stock `PAD EDIT` / `EQ EDIT`), and fixes two stock Roland bugs on `WMT4`.
- **Per-Layer WAV Loading & Sample Editing (`PAD-SND-*`, `DRUM-PHASE2-HOOKS`, `PAD-WAV-BIND-WMT`, `SMPL-EDIT-*`, `SAMPLE-DEL-WMT-FIX`)**: Expands the `SHIFT + PAD` Drum Pad Sound Menu from 5 to **13 items**, adding non-destructive **`WMT1 WAVE`–`WMT4 WAVE`** loaders, **`WMT1 SMPL`–`WMT4 SMPL`** Sample Edit shortcuts, and Sample Delete reference scanning across all 4 WMT layers.
- **Sample Chop, 16×2 LCD Waveform, & Pad Assign ([`mc107/src/sample_chop.c`](mc107/src/sample_chop.c))**: Registers custom **Screen `0x68` (`SMPL CHOP`)** with live 16×2 CGRAM waveform & slice/transient markers (`1x`–`32x` zoom), equal-step (`1/4/8/16/32`), auto transient detection, manual slice add/delete/nudge, isolated real-time slice audition on Pads 1–16 (`SHIFT+Pad` for 17–32), and auto/manual pad assignment writing real truncated User Samples into Wave RAM and syncing Core 1 DSP.
- **Button Navigation Mode — Hands-Free VALUE Dial Alternative ([`mc107/src/button_nav.c`](mc107/src/button_nav.c))**: Global menu navigation mode using `<`, `>`, `TEMPO`, and `EXIT` in place of the VALUE encoder, with hold-to-scroll auto-repeat and blinking visual indicator LEDs.

---

## User Guide: Drum Pad Deep Edit, WMT1–4 Sample Tools, & Sample Chop

All features below are accessed on a **Drum Track** in **NOTE** mode by holding **`SHIFT` + tapping a Pad** to open the expanded 13-item Drum Pad Sound Menu:

`PRESET` · `PROJECT` · `WAVE FILE` · **`WMT1 WAVE`–`WMT4 WAVE`** · **`PAD EDIT`** · **`WMT1 SMPL`–`WMT4 SMPL`** · **`SMPL CHOP`**

### 1. Expanded Drum Pad Edit (`SHIFT + PAD → PAD EDIT`)

Stock MC-101 only exposes 3 sections in `PAD EDIT` (`PAD EDIT`, a 3-item `INST EDIT`, and `EQ EDIT`). MC-107 unlocks **11 sections** matching MC-707 drum instrument depth (use `SHIFT + VALUE` dial to jump quickly between sections):

1. **`PAD EDIT:`** *(stock, 13 params)* — Pad Level, Pan, Coarse/Fine Tune, Mute Group, Reverb/Chorus/Delay Sends, Comp/EQ Sw, Output Assign, etc.
2. **`INST EDIT:`** *(expanded, 10 params)* — Voice Assign, Env Mode, WMT Velo, Inst Lvl, Src Key, FineTune, RndPitch, Rnd Pan, Alt Pan, WaveSync.
3. **`WMT1 EDIT:` – `WMT4 EDIT:`** *(4 sections × 23 params each)* — Full per-layer control for all 4 Wave Multi-Tone layers: Switch, Type (`INT`/`SAMP`), Bank, Wave L, Wave R, Level, Pan, Coarse/Fine Tune, Gain, Velo Lo/Hi, Fade Lo/Hi, FXM Sw/Col/Dpt, Rnd/Alt Pan, Delay Mode/Sync/Note/Time.
4. **`TVF/TVA:`** *(8 params)* — Filter Type, Cutoff, Resonance, Cutoff Velo Curve/Sens, Reso Velo Sens, Level Velo Curve/Sens.
5. **`PITCH ENV:`** *(14 params)* — Depth, Velo Sens/Curve, T1/T4 Velo, Time 1–4, Level 0–4.
6. **`TVF ENV:`** *(14 params)* — Depth, Velo Sens/Curve, T1/T4 Velo, Time 1–4, Level 0–4.
7. **`TVA ENV:`** *(14 params)* — T1/T4 Velo, Time 1–4, Level 1–3, and envelope shape controls.
8. **`EQ EDIT:`** *(stock, 8 params)* — Pad 3-band EQ.

### 2. Per-Layer WAV Loading & Sample Edit (`WMT1–4 WAVE` & `WMT1–4 SMPL`)

- **`WAVE FILE`** *(stock behavior)*: Resets the entire pad (clears `WMT2`–`WMT4`, resets pitch/filter/EQ, renames the pad) and loads the chosen WAV into `WMT1`.
- **`WMT1 WAVE` – `WMT4 WAVE`**: Loads a WAV file **non-destructively** into that specific WMT layer (`WMT1`–`WMT4`), keeping all other layers, preset waves, filter/envelope settings, and pad settings intact — ideal for stacking drum hits (e.g., sub + click + acoustic snare + texture) on a single pad.
- **`WMT1 SMPL` – `WMT4 SMPL`**: Opens the stock Sample Edit screen (Start/End/Loop/Gain/Normalize/etc.) directly targeting the User Sample assigned to `WMT1`, `WMT2`, `WMT3`, or `WMT4`.
- **Safe Sample Delete:** The firmware's unused-sample scanner checks all 4 WMT layers (`WMT1`–`WMT4`), so samples loaded into `WMT2`–`WMT4` are properly protected.

### 3. Sample Chop & Pad Assign (`SHIFT + PAD → SMPL CHOP`)

`SMPL CHOP` lets you inspect any User Sample on a Drum Track pad as a live waveform on the 16×2 LCD, slice it into up to **32 chops** (by equal steps, automatic transient detection, or manual placement), audition every slice cleanly in isolation on the pads, and export slices as real standalone User Samples assigned to your Drum Kit pads (which save and reload with your project).

#### Opening & Reading the Display
1. On a **Drum Track** in **NOTE** mode, hold **`SHIFT` + tap the pad** containing your User Sample, scroll to **`SMPL CHOP`**, and press **`VALUE`**.
2. On open, `SMPL CHOP` automatically finds the first WMT layer (`WMT1`–`WMT4`) holding a User Sample, starts with **4 equal slices (`>STEP:04`)**, and isolates audio (gates other pads `1–16` and temporarily mutes other WMT layers on the source pad until `EXIT`).
3. **16×2 LCD Layout:**
   - **Row 1 (top):** Active mode (`>` prefix) and parameters, or temporary status banners (`SLICE ADDED!`, `DONE! 08 PADS OK`, etc.).
   - **Row 2 (bottom):** 16-column live **waveform** of the current zoom window (vertical bars for peak amplitude; solid flat top cap + vertical line for active marker; small top tick + stem for other markers).

#### Global Controls (Work in Every Mode)
- **Short-press `VALUE`:** Cycles through the 7 modes: `>STEP` → `>AUTO` → `>M01` (Move) → `>ADD` → `>Z` (Zoom) → `>ASGN` → `>SRC`.
- **Turn `VALUE` dial:** Adjusts the active mode's primary parameter or moves the marker/cursor.
- **`SHIFT + VALUE` press:** Executes the active mode's action (Delete slice in `Mxx`, Insert slice in `ADD`, Re-detect in `AUTO`, Commit in `ASGN`).
- **`SHIFT + VALUE` dial:** Secondary control (Quick Zoom in `Mxx`/`ADD`, fine sensitivity `±1` in `AUTO`, target layer `W1`–`W4` in `ASGN`).
- **Tap Pads 1–16** *(in `STEP`/`AUTO`/`Mxx`/`Z`)*: Selects and auditions slices `1–16` live; hold **`SHIFT` + Pad 1–16** for slices `17–32`. Releasing the pad stops playback.
- **`EXIT`:** Restores the source sample's original Start/End and original WMT layer switches, and exits (any slices already committed in `>ASGN` remain permanently assigned).

#### The 7 Modes
1. **`>STEP` (Equal-Step Chop)** — Divide evenly into `01`, `04`, `08`, `16`, or `32` slices.
2. **`>AUTO` (Auto Transient Detection)** — Sensitivity `05`–`95` (steps of `5`, or `SHIFT + dial` for `±1`); `SHIFT + VALUE` re-runs detection.
3. **`>M01..M32` (Move & Delete Slice)** — Nudge start point with `VALUE`, quick-zoom `1x`–`32x` with `SHIFT + dial`, delete slice with `SHIFT + VALUE`.
4. **`>ADD` (Manual Add Slice)** — Position cursor with `VALUE`, audition from cursor with Pads `1–16`, insert slice with `SHIFT + VALUE`.
5. **`>Z` (One-Handed Zoom)** — `1x`, `2x`, `4x`, `8x`, `16x`, or `32x` centered on the selected slice.
6. **`>ASGN` (Assign Slices to Pads & Clear Pads)** — `ALL>P1`, `ALL>Pxx`, `17+>P1`, `CLR P1-16`, or `Sxx>P01..P16` (tap any Pad `1–16` to select destination); `SHIFT + dial` selects target layer `W1`–`W4`; `SHIFT + VALUE` commits.
7. **`>SRC` (Source Pad / Layer)** — Switch source layer `W1`–`W4` with `VALUE` or tap another Pad `1–16` to chop its sample.

---

## User Guide: Button Navigation Mode (VALUE Dial Alternative)

Button Navigation Mode provides a global alternative to rotating and pressing the `VALUE` dial across every screen and menu on the MC-101:

- **Enter:** Hold **`>`** (Measure Right) **alone** for **~600 ms** (short-tapping `< 600 ms` and multi-button combos like `SHIFT + >` pass through normally).
- **Visual Indicator:** `<`, `>`, `TEMPO`, and `EXIT` button LEDs blink continuously.
- **Controls while active:**
  - **`<`** → Rotate `VALUE` dial left (`-1`, hold for auto-repeat).
  - **`>`** → Rotate `VALUE` dial right (`+1`, hold for auto-repeat).
  - **`TEMPO`** → Press `VALUE` / `ENTER` (`SHIFT + TEMPO` works as `SHIFT + VALUE`).
  - **`EXIT`** → Normal `EXIT`.
- **Exit:** Press **`SHIFT + <`**.

---

## License

Ed Coffey's original code in this repository is licensed under the **Mozilla Public License 2.0 (MPL-2.0)** — see the file headers and `https://mozilla.org/MPL/2.0/`.
