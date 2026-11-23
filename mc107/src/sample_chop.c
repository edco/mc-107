/*
 * MC-101 v1.82 Sample Chop — Full Suite (Waveforms, Step/Auto/Manual Chop, Pad Assign)
 *
 * Linked into verified zero-padded SDRAM knob-table tails:
 *   CHOP-CAVE-1 (CODE1): 0x602D4FE0 (3,312 B) — entry + event handler + LCD/waveform UI
 *   CHOP-CAVE-2 (CODE2): 0x602D5DB0 (3,312 B) — Wave RAM slicer + Core 1 sync + Pad assign
 *   CHOP-CAVE-3 (CODE3): 0x602D9400 (3,296 B) — Auto transient detector + slice editor
 *   CHOP-RAM    (BSS)  : 0x602D4A00 (1,280 B) — ChopState + Wave RAM scratch buffers
 *
 * Opened via SHIFT + PAD -> "SMPL CHOP" (Screen ID 0x68).
 * Controls (VALUE dial + VALUE press + SHIFT + EXIT + Pads 1..16; no < / > required):
 *   - VALUE press       : cycle mode STEP -> AUTO -> MOVE -> ADD -> ZOOM -> ASGN -> SRC
 *   - VALUE dial        : adjust active mode parameter / cursor / slice / assign target
 *   - SHIFT + VALUE dial: quick zoom (1x..32x) in MOVE/ADD; fine sens in AUTO; target WMT in ASGN
 *   - SHIFT + VALUE     : execute action (DEL slice in MOVE, INSERT slice in ADD, COMMIT in ASGN)
 *   - Pads 1..16        : live slice audition (SHIFT+Pad = slices 17..32); target pad in ASGN; source pad in SRC
 *   - EXIT              : restore source sample Start/End, invalidate CGRAM cache, return
 */

#include <stdint.h>

#define SEC_ENTRY __attribute__((section(".chop_entry"), noinline))
#define SEC_SLICE __attribute__((section(".chop_slice"), noinline))
#define SEC_DET   __attribute__((section(".chop_detect"), noinline))

/* ---- Firmware structs & function pointers (all Thumb bit | 1) ---- */
typedef struct {
    uint32_t frames;      /* +0x00 */
    uint32_t wramL;       /* +0x04: External Wave RAM byte address */
    uint32_t wramR;       /* +0x08: External Wave RAM byte address (stereo R) */
    uint16_t id;          /* +0x0C */
    uint16_t is_stereo;   /* +0x0E */
} UserSampleInfo;

typedef struct {
    char     name[16];    /* +0x00..0x0F */
    uint32_t num_frames;  /* +0x10 */
    uint32_t byte_len;    /* +0x14 */
    uint32_t flags;       /* +0x18: [0..6]=type(1=M,2=SL,3=SR), [7]=active, [11]=ready, [12..31]=sr */
    uint32_t uid;         /* +0x1C */
    uint16_t start_blk;   /* +0x20 */
    uint16_t num_blks;    /* +0x22 */
    uint32_t reserved;    /* +0x24 */
} SampleHeader;

typedef struct {
    int16_t type;         /* +0x00 */
    int16_t arg1;         /* +0x02 */
    int16_t arg2;         /* +0x04 */
    int16_t arg3;         /* +0x06 */
} UiEvent;

#define FW_FN(type, addr) ((type)((addr) | 1u))

/* UI / Param / Pad / LCD */
#define fw_get_param            FW_FN(int            (*)(int, int, int, int),          0x6009CB2Cu)
#define fw_set_param            FW_FN(void           (*)(int, int, int, int, int),     0x6009CB9Cu)
#define fw_get_trk_type         FW_FN(int            (*)(int),                         0x60050D70u)
#define fw_get_cur_clip         FW_FN(int            (*)(int),                         0x60054BC0u)
#define fw_get_sys_val          FW_FN(int            (*)(int),                         0x6009BFCCu)
#define fw_pad_to_note          FW_FN(int            (*)(void *, int),                 0x6003744Cu)
#define fw_is_btn_pressed       FW_FN(int            (*)(int),                         0x6001CBD8u)
#define fw_pop_screen           FW_FN(int            (*)(void),                        0x6001D7E0u)
#define fw_get_sample_info      FW_FN(int            (*)(UserSampleInfo *, int),       0x6004F880u)
#define fw_lcd_set_cgram        FW_FN(void           (*)(int, const uint8_t *),        0x600974A4u)
#define fw_lcd_putc             FW_FN(void           (*)(int, int, uint8_t),           0x60097554u)
#define fw_cgram_cache_inval    FW_FN(void           (*)(void *, int),                 0x6009DBA4u)
#define fw_midi_pkt_init        FW_FN(void           (*)(void *, const uint8_t *),     0x60019FD6u)
#define fw_midi_send            FW_FN(void           (*)(int, void *),                 0x60019FE0u)
#define fw_midi_pkt_free        FW_FN(void           (*)(void *),                      0x60019EFAu)

/* External Wave RAM hardware bridge (ITCM) */
#define fw_waveram_read16       FW_FN(uint16_t       (*)(uint32_t),                    0x01011D28u)
#define fw_waveram_read         FW_FN(void           (*)(void *, uint32_t, uint32_t),  0x01011D5Eu)
#define fw_waveram_write        FW_FN(void           (*)(uint32_t, const void *, uint32_t), 0x0102C138u)

/* User Sample pool & Core 1 DSP sync */
#define fw_pool_get_hdr         FW_FN(SampleHeader * (*)(void *, int),                 0x6001A198u)
#define fw_pool_set_blk_owner   FW_FN(void           (*)(void *, int, int),            0x6001A526u)
#define fw_alloc_blocks         FW_FN(int            (*)(int, int),                    0x6002645Cu)
#define fw_set_hdr_name         FW_FN(void           (*)(SampleHeader *, const char *),0x6004F814u)
#define fw_rand_byte            FW_FN(uint32_t       (*)(void),                        0x6000060Cu)
#define fw_sample_init_params   FW_FN(void           (*)(int),                         0x6002DEECu)
#define fw_ipc_get_token        FW_FN(uint32_t       (*)(void),                        0x60096510u)
#define fw_ipc_get_buf          FW_FN(uint8_t *      (*)(uint32_t),                    0x60096548u)
#define fw_build_sample_ipc     FW_FN(void           (*)(int, uint8_t *),              0x6002D1ACu)
#define fw_ipc_send             FW_FN(void           (*)(uint32_t, uint32_t, uint32_t),0x60019F36u)
#define fw_mark_clip_dirty      FW_FN(void           (*)(int, int),                    0x6001A1A2u)
#define fw_sync_clip_to_core1   FW_FN(void           (*)(void *, int, int),            0x60046434u)
#define fw_bind_pad_wav         FW_FN(void           (*)(int, int, int, int),          0x600299A4u)

#define FW_UI_MGR               ((void *)0x60A94430u)
#define FW_CGRAM_CACHE          ((void *)0x60AD3E8Au)
#define FW_SAMPLE_POOL          ((void *)0x602CC0F4u)
#define FW_CLIP_MGR             ((void *)0x602F1D38u)
#define FW_DRUM_INST_TBL        ((uint8_t * const *)0x60210774u)
#define FW_KIT_PAD_TBL          ((uint8_t * const *)0x60210754u)
#define FW_HW_TIMER_REG         ((volatile uint32_t *)0x4003C000u)

/* ---- Event & Key IDs ---- */
#define EV_INIT         0
#define EV_OPEN         1
#define EV_CLOSE        2
#define EV_TICK         3
#define EV_REDRAW       4
#define EV_BTN_DOWN     5
#define EV_BTN_UP       6
#define EV_DIAL         7

#define KEY_SHIFT       0x00
#define KEY_EXIT        0x10
#define KEY_VALUE       0x11
#define KEY_PAD_FIRST   0x16
#define KEY_PAD_LAST    0x25
#define KEY_LEFT1       0x29
#define KEY_RIGHT1      0x2A
#define KEY_LEFT2       0x2B
#define KEY_RIGHT2      0x2C

/* ---- UI Modes ---- */
#define MODE_STEP       0
#define MODE_AUTO       1
#define MODE_MOVE       2
#define MODE_ADD        3
#define MODE_ZOOM       4
#define MODE_ASGN       5
#define MODE_SRC        6
#define MODE_COUNT      7

#define MAX_SLICES      32
#define ZOOM_LEVELS     6
#define ENV_BINS        128

/* Assign options */
#define ASGN_AUTO_P1    0  /* Auto assign slices 1..16 -> Pads 1..16 (clears WMT2..4 & unused pads on W1) */
#define ASGN_AUTO_CUR   1  /* Auto assign slices 1..N -> starting at current pad */
#define ASGN_AUTO_HI    2  /* Auto assign slices 17..32 -> Pads 1..16 */
#define ASGN_CLR_ALL    3  /* Clear all waves/samples on Pads 1..16 (WMT1..4) */
#define ASGN_MAN_BASE   4  /* 4..19 -> Manual assign sel_slice -> Pad 1..16 */
#define ASGN_OPT_MAX    19

/* ---- Persistent state in CHOP_RAM (0x602D4A00, 1,280 B) ---- */
typedef struct {
    uint8_t  active;
    uint8_t  is_drum;
    uint8_t  valid;
    uint8_t  mode;
    uint8_t  track;
    uint8_t  clip;
    uint8_t  pad;          /* Source pad 0..15 */
    uint8_t  note;         /* Source pad MIDI note */
    uint8_t  wmt_idx;      /* Source WMT 0..3 */
    uint8_t  zoom_idx;     /* 0..5 -> 1x..32x */
    uint8_t  prm_dirty;    /* 1 if source sample Start/End temporarily changed */
    uint8_t  auditioning;  /* 1 while note held */
    uint8_t  num_slices;   /* 1..32 */
    uint8_t  sel_slice;    /* 0..num_slices-1 */
    uint8_t  step_idx;     /* 0..4 -> 1, 4, 8, 16, 32 */
    uint8_t  auto_sens;    /* 5..95 */
    uint8_t  asgn_opt;     /* 0..18 */
    uint8_t  asgn_wmt;     /* target WMT 0..3 */
    uint8_t  msg_active;   /* 1 if status banner shown on Row 0 */
    uint8_t  wmt_soloed;   /* 1 if other WMT layers on source pad are temporarily muted */
    uint8_t  orig_wmt_velo;/* saved DrumInst WMT Velo (0x8E0) for source pad */
    uint8_t  orig_wmt_sw[4]; /* saved WMT1..4 SW flags for source pad */
    int16_t  sample_id;    /* 0..499, or -1 */
    uint16_t peak_max;
    int32_t  orig_start;
    int32_t  orig_end;
    uint32_t cursor;       /* free cursor frame (MODE_ADD) */
    uint32_t view_start;
    uint32_t view_len;
    UserSampleInfo info;
    uint32_t marks[MAX_SLICES];
    uint8_t  col_h[16];    /* 0..6 bar height per LCD column */
    uint8_t  col_mark[16]; /* 1 if column contains a non-selected slice mark */
    char     msg[16];      /* temporary status message */
} ChopState;

__attribute__((section(".chop_state"))) ChopState g_chop;
/* Scratch buffers in CHOP_RAM (avoid RTOS stack pressure) */
static uint16_t g_env[ENV_BINS];
static uint8_t  g_chunk[256];

/* Forward declarations across code caves */
static void render_lcd(void);
static void recompute_waveform(void);
static void update_viewport_for(uint32_t focus);
static void center_viewport_on(uint32_t focus);
static void restore_orig_params(void);
static void set_msg(const char *s16);

/* ---- Shared tiny helpers (CODE1) ---- */
static void zero_mem(void *p, uint32_t n) {
    uint8_t *b = (uint8_t *)p;
    while (n--) *b++ = 0;
}

static uint32_t u32_min(uint32_t a, uint32_t b) { return a < b ? a : b; }
static uint32_t u32_max(uint32_t a, uint32_t b) { return a > b ? a : b; }

static void lcd_write_row(int row, const char *buf) {
    for (int c = 0; c < 16; c++) {
        fw_lcd_putc(c, row, (uint8_t)buf[c]);
    }
}

static void fmt_u32_fixed(char *dst, uint32_t val, int width) {
    for (int i = width - 1; i >= 0; i--) {
        dst[i] = (char)('0' + (val % 10u));
        val /= 10u;
    }
}

static void fmt_zoom(char *dst, uint8_t zidx) {
    static const char zlbl[ZOOM_LEVELS][3] = {
        "1x ", "2x ", "4x ", "8x ", "16x", "32x"
    };
    if (zidx >= ZOOM_LEVELS) zidx = 0;
    dst[0] = zlbl[zidx][0];
    dst[1] = zlbl[zidx][1];
    dst[2] = zlbl[zidx][2];
}

static void set_msg(const char *s16) {
    for (int i = 0; i < 16; i++) g_chop.msg[i] = s16[i];
    g_chop.msg_active = 1;
}

static uint32_t get_slice_end(uint8_t idx) {
    if ((uint32_t)(idx + 1u) < (uint32_t)g_chop.num_slices) {
        return g_chop.marks[idx + 1u];
    }
    return (uint32_t)g_chop.orig_end;
}

/* Upload CGRAM 1..6 (waveform bars heights 1,2,3,4,6,8) and 7 (slice/transient marker) */
static void upload_static_cgram(void) {
    static const uint8_t heights[6] = { 1, 2, 3, 4, 6, 8 };
    uint8_t glyph[8];
    for (int slot = 1; slot <= 6; slot++) {
        uint8_t h = heights[slot - 1];
        for (int r = 0; r < 8; r++) {
            glyph[r] = (r >= (8 - h)) ? 0x1Eu : 0x00u;
        }
        fw_lcd_set_cgram(slot, glyph);
    }
    /* Slot 7: Slice / Transient marker (crisp top tick + waveform stem) */
    glyph[0] = 0x0Eu; /* 3px top tick */
    glyph[1] = 0x04u; /* center dot */
    glyph[2] = 0x00u;
    glyph[3] = 0x04u;
    glyph[4] = 0x1Eu;
    glyph[5] = 0x1Eu;
    glyph[6] = 0x1Eu;
    glyph[7] = 0x1Eu;
    fw_lcd_set_cgram(7, glyph);
}

/* Upload dynamic CGRAM slot 0: active cursor / selected slice + exact waveform bar */
static void upload_cursor_cgram(uint8_t bar_h) {
    static const uint8_t heights[7] = { 0, 1, 2, 3, 4, 6, 8 };
    if (bar_h > 6u) bar_h = 6u;
    uint8_t h = heights[bar_h];
    uint8_t glyph[8];
    glyph[0] = 0x1Fu; /* solid 5px cursor cap */
    glyph[1] = (h >= 7u) ? 0x1Eu : 0x04u;
    for (int r = 2; r < 8; r++) {
        glyph[r] = (r >= (8 - h)) ? 0x1Eu : 0x04u;
    }
    fw_lcd_set_cgram(0, glyph);
}

static uint8_t *get_pad_drum_inst(uint8_t track, uint8_t clip, uint8_t pad) {
    int note = fw_pad_to_note(FW_UI_MGR, (int)pad);
    int pad_idx = note - 0x15;
    if (pad_idx < 0 || pad_idx >= 88) return 0;
    uint8_t *base = FW_DRUM_INST_TBL[track];
    if (!base) return 0;
    return base + (uint32_t)clip * 0x4A40u + (uint32_t)pad_idx * 0xD8u;
}

static void solo_source_wmt(void) {
    int pad_idx = (int)g_chop.note - 0x15;
    if (pad_idx < 0 || pad_idx >= 88) return;
    int grp  = 0xFB + (int)g_chop.track;
    int clip = (int)g_chop.clip;
    /* Use live fw_set_param so Core 1 DSP immediately mutes all other WMT layers (Preset INT & User SAMP) */
    for (uint32_t w = 0u; w < 4u; w++) {
        int pid  = 0x8E1 + (int)w * 0x17; /* WMT1..4 Switch */
        int cur  = fw_get_param(grp, pid, pad_idx, clip);
        g_chop.orig_wmt_sw[w] = (cur > 0) ? 1u : 0u;
        int want = (w == (uint32_t)g_chop.wmt_idx) ? 1 : 0;
        if (cur != want) {
            fw_set_param(grp, pid, want, pad_idx, clip);
        }
    }
    /* Temporarily disable WMT Velocity Control (0x8E0) so soloed WMT always sounds cleanly */
    int vcur = fw_get_param(grp, 0x8E0, pad_idx, clip);
    g_chop.orig_wmt_velo = (uint8_t)((vcur > 0) ? vcur : 0);
    if (vcur != 0) {
        fw_set_param(grp, 0x8E0, 0, pad_idx, clip);
    }
    g_chop.wmt_soloed = 1u;
}

static void restore_orig_params(void) {
    if (g_chop.prm_dirty && g_chop.sample_id >= 0) {
        fw_set_param(0x11F, 0x98D, g_chop.orig_start, -1, g_chop.sample_id);
        fw_set_param(0x11F, 0x98F, g_chop.orig_end,   -1, g_chop.sample_id);
        g_chop.prm_dirty = 0;
    }
    if (g_chop.wmt_soloed) {
        int pad_idx = (int)g_chop.note - 0x15;
        if (pad_idx >= 0 && pad_idx < 88) {
            int grp  = 0xFB + (int)g_chop.track;
            int clip = (int)g_chop.clip;
            for (uint32_t w = 0u; w < 4u; w++) {
                int pid  = 0x8E1 + (int)w * 0x17;
                int cur  = fw_get_param(grp, pid, pad_idx, clip);
                int want = (int)g_chop.orig_wmt_sw[w];
                if (cur != want) {
                    fw_set_param(grp, pid, want, pad_idx, clip);
                }
            }
            int vcur = fw_get_param(grp, 0x8E0, pad_idx, clip);
            if (vcur != (int)g_chop.orig_wmt_velo) {
                fw_set_param(grp, 0x8E0, (int)g_chop.orig_wmt_velo, pad_idx, clip);
            }
        }
        g_chop.wmt_soloed = 0u;
    }
}

static int get_wmt_sample_id(uint8_t track, uint8_t clip, uint8_t pad, uint8_t wmt_idx) {
    uint8_t *inst = get_pad_drum_inst(track, clip, pad);
    if (!inst) return -1;
    uint8_t *wmt = inst + 0x1Cu + (uint32_t)wmt_idx * 0x1Cu;
    uint8_t gtype = wmt[1];
    uint16_t wav_l = (uint16_t)wmt[4] | ((uint16_t)wmt[5] << 8);
    if (gtype == 2u && wav_l > 0u && wav_l <= 500u) {
        return (int)(wav_l - 1u);
    }
    return -1;
}

static uint16_t scan_peak(uint32_t start, uint32_t count) {
    uint32_t wramL = g_chop.info.wramL;
    uint32_t wramR = g_chop.info.is_stereo ? g_chop.info.wramR : 0u;
    if (wramL == 0u || count == 0u) return 0u;
    uint32_t stride = count >> 6;
    if (stride == 0u) stride = 1u;
    uint32_t end = start + count;
    if (end > g_chop.info.frames) end = g_chop.info.frames;
    uint32_t mx = 0u;
    for (uint32_t i = start; i < end; i += stride) {
        int32_t v = (int16_t)fw_waveram_read16(wramL + (i << 1));
        uint32_t av = (uint32_t)(v < 0 ? -v : v);
        if (av > mx) mx = av;
        if (wramR != 0u) {
            int32_t vr = (int16_t)fw_waveram_read16(wramR + (i << 1));
            uint32_t avr = (uint32_t)(vr < 0 ? -vr : vr);
            if (avr > mx) mx = avr;
        }
    }
    return (uint16_t)mx;
}

static void update_viewport_for(uint32_t focus) {
    uint32_t total = g_chop.info.frames;
    if (total == 0u) {
        g_chop.view_start = 0u;
        g_chop.view_len = 0u;
        return;
    }
    uint32_t vlen = total >> g_chop.zoom_idx;
    if (vlen < 16u) vlen = u32_min(16u, total);
    g_chop.view_len = vlen;

    if (focus >= total) focus = total - 1u;
    if (g_chop.view_len >= total) {
        g_chop.view_start = 0u;
    } else {
        if (focus < g_chop.view_start) {
            g_chop.view_start = focus;
        } else if (focus >= g_chop.view_start + g_chop.view_len) {
            g_chop.view_start = focus - g_chop.view_len + 1u;
        }
        if (g_chop.view_start + g_chop.view_len > total) {
            g_chop.view_start = total - g_chop.view_len;
        }
    }
}

static void center_viewport_on(uint32_t focus) {
    uint32_t total = g_chop.info.frames;
    if (total == 0u) return;
    uint32_t vlen = total >> g_chop.zoom_idx;
    if (vlen < 16u) vlen = u32_min(16u, total);
    g_chop.view_len = vlen;
    uint32_t half = vlen >> 1;
    if (focus <= half) {
        g_chop.view_start = 0u;
    } else if (focus + (vlen - half) >= total) {
        g_chop.view_start = total - vlen;
    } else {
        g_chop.view_start = focus - half;
    }
}

SEC_DET static void recompute_waveform(void) {
    if (!g_chop.valid || g_chop.view_len == 0u) {
        for (int c = 0; c < 16; c++) {
            g_chop.col_h[c] = 0u;
            g_chop.col_mark[c] = 0u;
        }
        return;
    }
    uint32_t scale = g_chop.peak_max;
    if (scale < 2048u) scale = 2048u;

    uint32_t vstart = g_chop.view_start;
    uint32_t vlen = g_chop.view_len;
    for (int c = 0; c < 16; c++) {
        uint32_t f0 = vstart + (vlen * (uint32_t)c) / 16u;
        uint32_t f1 = vstart + (vlen * (uint32_t)(c + 1)) / 16u;
        uint32_t cnt = (f1 > f0) ? (f1 - f0) : 1u;
        uint32_t pk = scan_peak(f0, cnt);
        uint32_t h = (pk * 6u + (scale >> 1)) / scale;
        if (pk > (scale >> 5) && h == 0u) h = 1u;
        if (h > 6u) h = 6u;
        g_chop.col_h[c] = (uint8_t)h;
        g_chop.col_mark[c] = 0u;
    }
}

/* ========================================================================
 * CODE3 (.text.detect): Step Chop, Auto Transient Detector, Manual Slice Edit
 * ======================================================================== */

SEC_DET static void apply_step_chop(uint8_t step_idx) {
    static const uint8_t counts[5] = { 1, 4, 8, 16, 32 };
    if (!g_chop.valid) return;
    if (step_idx > 4u) step_idx = 4u;
    g_chop.step_idx = step_idx;
    uint8_t n = counts[step_idx];
    uint32_t st = (uint32_t)g_chop.orig_start;
    uint32_t en = (uint32_t)g_chop.orig_end;
    uint32_t span = (en > st) ? (en - st) : 1u;
    if (span < (uint32_t)n * 64u) {
        n = (uint8_t)u32_max(1u, span / 64u);
    }
    g_chop.num_slices = n;
    for (uint8_t i = 0; i < n; i++) {
        g_chop.marks[i] = st + (span * (uint32_t)i) / (uint32_t)n;
    }
    if (g_chop.sel_slice >= n) g_chop.sel_slice = 0u;
    g_chop.cursor = g_chop.marks[g_chop.sel_slice];
}

SEC_DET static void run_transient_detect(uint8_t sens) {
    if (!g_chop.valid) return;
    if (sens < 5u) sens = 5u;
    if (sens > 95u) sens = 95u;
    g_chop.auto_sens = sens;

    uint32_t st = (uint32_t)g_chop.orig_start;
    uint32_t en = (uint32_t)g_chop.orig_end;
    uint32_t span = (en > st) ? (en - st) : 1u;
    uint32_t wramL = g_chop.info.wramL;
    uint32_t wramR = g_chop.info.is_stereo ? g_chop.info.wramR : 0u;

    /* 1. Compute 128 short-window peak envelopes across [orig_start .. orig_end] */
    uint32_t max_env = 0u;
    for (uint32_t b = 0; b < ENV_BINS; b++) {
        uint32_t f0 = st + (span * b) / ENV_BINS;
        uint32_t f1 = st + (span * (b + 1u)) / ENV_BINS;
        uint32_t cnt = (f1 > f0) ? (f1 - f0) : 1u;
        uint32_t stride = cnt >> 4; /* ~16 Wave RAM reads per bin -> ~2048 total (<1ms) */
        if (stride == 0u) stride = 1u;
        uint32_t pk = 0u;
        for (uint32_t i = f0; i < f1; i += stride) {
            int32_t v = (int16_t)fw_waveram_read16(wramL + (i << 1));
            uint32_t av = (uint32_t)(v < 0 ? -v : v);
            if (av > pk) pk = av;
            if (wramR != 0u) {
                int32_t vr = (int16_t)fw_waveram_read16(wramR + (i << 1));
                uint32_t avr = (uint32_t)(vr < 0 ? -vr : vr);
                if (avr > pk) pk = avr;
            }
        }
        g_env[b] = (uint16_t)pk;
        if (pk > max_env) max_env = pk;
    }

    if (max_env < 512u) {
        g_chop.num_slices = 1u;
        g_chop.marks[0] = st;
        g_chop.sel_slice = 0u;
        return;
    }

    /* 2. Compute positive onset flux & find max flux */
    uint32_t max_flux = 0u;
    for (uint32_t b = 1; b < ENV_BINS; b++) {
        uint32_t prev = g_env[b - 1u];
        if (b >= 2u && g_env[b - 2u] < prev) prev = g_env[b - 2u];
        uint32_t fl = (g_env[b] > prev) ? ((uint32_t)g_env[b] - prev) : 0u;
        if (fl > max_flux) max_flux = fl;
    }
    if (max_flux < 256u) max_flux = max_env;

    /* Threshold decreases as sensitivity rises (sens=95 -> ~5%, sens=5 -> ~95%) */
    uint32_t thresh = (max_flux * (uint32_t)(100u - sens)) / 100u;
    uint32_t noise_floor = max_env >> 4;
    if (thresh < noise_floor) thresh = noise_floor;

    /* Minimum gap: at least 2 bins (~1.5% of sample) and >= 1024 frames (~23ms) */
    uint32_t min_frames = 1024u;
    uint32_t bin_frames = span / ENV_BINS;
    uint32_t min_bins = (bin_frames > 0u) ? ((min_frames + bin_frames - 1u) / bin_frames) : 2u;
    if (min_bins < 2u) min_bins = 2u;

    /* 3. Always place slice 0 at first audible bin (or orig_start) */
    uint8_t n = 0u;
    uint32_t last_bin = 0u;
    g_chop.marks[n++] = st;

    for (uint32_t b = min_bins; b + 1u < ENV_BINS && n < MAX_SLICES; b++) {
        if (b < last_bin + min_bins) continue;
        uint32_t prev = g_env[b - 1u];
        if (b >= 2u && g_env[b - 2u] < prev) prev = g_env[b - 2u];
        uint32_t cur = g_env[b];
        uint32_t fl = (cur > prev) ? (cur - prev) : 0u;
        /* Local onset peak: flux exceeds threshold and energy is rising vs previous */
        if (fl >= thresh && cur >= g_env[b - 1u] && cur >= noise_floor) {
            /* Check if next bin has even stronger flux -> wait 1 bin for true attack */
            uint32_t next_fl = (g_env[b + 1u] > cur) ? ((uint32_t)g_env[b + 1u] - cur) : 0u;
            if (next_fl > fl) continue;
            uint32_t f = st + (span * b) / ENV_BINS;
            /* Step back slightly (~1/2 bin) so we catch the very start of the transient */
            if (f > st + (bin_frames >> 1)) {
                uint32_t back = f - (bin_frames >> 1);
                if (back > g_chop.marks[n - 1u] + 128u) f = back;
            }
            g_chop.marks[n++] = f;
            last_bin = b;
        }
    }

    g_chop.num_slices = n;
    if (g_chop.sel_slice >= n) g_chop.sel_slice = 0u;
    g_chop.cursor = g_chop.marks[g_chop.sel_slice];
}

SEC_DET static void manual_insert_slice(uint32_t frame) {
    if (!g_chop.valid || g_chop.num_slices >= MAX_SLICES) {
        set_msg("MAX 32 SLICES!  ");
        return;
    }
    uint32_t st = (uint32_t)g_chop.orig_start;
    uint32_t en = (uint32_t)g_chop.orig_end;
    if (frame <= st + 64u || frame + 64u >= en) {
        set_msg("TOO CLOSE EDGE! ");
        return;
    }
    /* Find sorted insertion position & reject duplicates within 64 frames */
    uint8_t pos = 0u;
    while (pos < g_chop.num_slices && g_chop.marks[pos] < frame) {
        pos++;
    }
    if (pos > 0u && (frame - g_chop.marks[pos - 1u]) < 64u) {
        set_msg("MARK EXISTS!    ");
        return;
    }
    if (pos < g_chop.num_slices && (g_chop.marks[pos] - frame) < 64u) {
        set_msg("MARK EXISTS!    ");
        return;
    }
    for (int i = (int)g_chop.num_slices; i > (int)pos; i--) {
        g_chop.marks[i] = g_chop.marks[i - 1];
    }
    g_chop.marks[pos] = frame;
    g_chop.num_slices++;
    g_chop.sel_slice = pos;
    set_msg("SLICE ADDED!    ");
}

SEC_DET static void manual_delete_slice(uint8_t idx) {
    if (!g_chop.valid || g_chop.num_slices <= 1u) {
        set_msg("NEED >= 1 SLICE ");
        return;
    }
    if (idx >= g_chop.num_slices) return;
    for (uint8_t i = idx; (uint32_t)(i + 1u) < (uint32_t)g_chop.num_slices; i++) {
        g_chop.marks[i] = g_chop.marks[i + 1u];
    }
    g_chop.num_slices--;
    if (g_chop.sel_slice >= g_chop.num_slices) {
        g_chop.sel_slice = (uint8_t)(g_chop.num_slices - 1u);
    }
    set_msg("SLICE DELETED!  ");
}

SEC_DET static void move_selected_slice(int delta) {
    if (!g_chop.valid || g_chop.num_slices == 0u) return;
    uint8_t idx = g_chop.sel_slice;
    uint32_t step = g_chop.view_len >> 4;
    if (step == 0u) step = 1u;
    int32_t lo = (idx == 0u) ? 0 : (int32_t)(g_chop.marks[idx - 1u] + 64u);
    int32_t hi = (int32_t)(get_slice_end(idx) - 64u);
    if (hi < lo) hi = lo;
    int32_t next = (int32_t)g_chop.marks[idx] + delta * (int32_t)step;
    if (next < lo) next = lo;
    if (next > hi) next = hi;
    g_chop.marks[idx] = (uint32_t)next;
    uint32_t old_vs = g_chop.view_start;
    update_viewport_for((uint32_t)next);
    if (g_chop.view_start != old_vs) {
        recompute_waveform();
    }
}

/* ========================================================================
 * CODE2 (.text.slice): Real Truncated User Sample Creation + Pad Assignment
 * ======================================================================== */

SEC_SLICE static void copy_wram_channel(uint32_t dst_blk, uint32_t src_wram_base,
                                        uint32_t start_frame, uint32_t num_frames) {
    uint32_t dst_base = dst_blk << 15;
    uint32_t src_addr = src_wram_base + (start_frame << 1);
    uint32_t dst_addr = dst_base + 0x40u;

    /* 1. Zero 64-byte lead-in padding for Core 1 interpolator */
    zero_mem(g_chunk, 64u);
    fw_waveram_write(dst_base, g_chunk, 64u);

    /* 2. Stream PCM via 256-byte CHOP_RAM chunk buffer */
    uint32_t rem_bytes = num_frames << 1;
    while (rem_bytes > 0u) {
        uint32_t n = (rem_bytes > 256u) ? 256u : rem_bytes;
        fw_waveram_read(g_chunk, src_addr, n);
        /* Pad odd halfword to 4-byte boundary for 32-bit Wave RAM FIFO */
        uint32_t wlen = (n + 3u) & ~3u;
        for (uint32_t p = n; p < wlen; p++) g_chunk[p] = 0u;
        fw_waveram_write(dst_addr, g_chunk, wlen);
        src_addr += n;
        dst_addr += wlen;
        rem_bytes -= n;
    }

    /* 3. Zero 64-byte tail padding for Core 1 interpolator */
    zero_mem(g_chunk, 64u);
    fw_waveram_write(dst_addr, g_chunk, 64u);
}

/* Creates a real truncated User Sample slot in Wave RAM & syncs to Core 1. Returns 0..499 or -1. */
SEC_SLICE static int create_sliced_sample(uint8_t slice_idx, uint32_t start_frame, uint32_t num_frames) {
    if (num_frames < 16u || g_chop.sample_id < 0) return -1;

    SampleHeader *src_hdr = fw_pool_get_hdr(FW_SAMPLE_POOL, (int)g_chop.sample_id * 2);
    if (!src_hdr) return -1;
    uint32_t sr = (src_hdr->flags >> 12) & 0xFFFFFu;
    if (sr == 0u) sr = 44100u;
    uint16_t is_stereo = g_chop.info.is_stereo;

    /* 1. Find free User Sample slot 0..499 */
    int dst_id = -1;
    for (int sid = 0; sid < 500; sid++) {
        SampleHeader *h = fw_pool_get_hdr(FW_SAMPLE_POOL, sid * 2);
        if (h && ((h->flags & 0x80u) == 0u)) {
            dst_id = sid;
            break;
        }
    }
    if (dst_id < 0) return -1;

    SampleHeader *hdrL = fw_pool_get_hdr(FW_SAMPLE_POOL, dst_id * 2);
    SampleHeader *hdrR = fw_pool_get_hdr(FW_SAMPLE_POOL, dst_id * 2 + 1);
    if (!hdrL || (is_stereo && !hdrR)) return -1;

    /* 2. Allocate contiguous 32 KB blocks (including 0x40 lead-in + tail frames) */
    uint32_t padded_bytes = (num_frames + 0x40u) << 1;
    int num_blks = (int)((padded_bytes + 0x7FFFu) >> 15);
    if (num_blks <= 0) num_blks = 1;

    int blkL = fw_alloc_blocks(0, num_blks);
    if (blkL < 0) return -1;
    int blkR = -1;
    if (is_stereo) {
        blkR = fw_alloc_blocks(blkL + num_blks, num_blks);
        if (blkR < 0) return -1;
    }

    /* 3. Copy PCM data to Wave RAM */
    copy_wram_channel((uint32_t)blkL, g_chop.info.wramL, start_frame, num_frames);
    if (is_stereo) {
        copy_wram_channel((uint32_t)blkR, g_chop.info.wramR, start_frame, num_frames);
    }

    /* 4. Build slice name: first 12 chars of source name (trimmed) + "_01" */
    char name[17];
    int nlen = 0;
    while (nlen < 12 && src_hdr->name[nlen] != '\0' && src_hdr->name[nlen] != ' ') {
        name[nlen] = src_hdr->name[nlen];
        nlen++;
    }
    if (nlen == 0) {
        name[0] = 'C'; name[1] = 'H'; name[2] = 'P';
        nlen = 3;
    }
    name[nlen++] = '_';
    uint32_t snum = (uint32_t)slice_idx + 1u;
    name[nlen++] = (char)('0' + (snum / 10u));
    name[nlen++] = (char)('0' + (snum % 10u));
    name[nlen] = '\0';

    /* 5. Populate 40-byte headers (exact match to 0x600608B2..0x60060966) */
    uint32_t uid = 0u;
    while (uid == 0u) {
        uid = ((fw_rand_byte() & 0xFFu) << 24) | ((*FW_HW_TIMER_REG) & 0x00FFFFFFu);
    }

    fw_set_hdr_name(hdrL, name);
    hdrL->num_frames = num_frames;
    hdrL->byte_len   = num_frames << 1;
    hdrL->flags      = (sr << 12) | 0x800u | 0x80u | (is_stereo ? 2u : 1u);
    hdrL->uid        = uid;
    hdrL->start_blk  = (uint16_t)blkL;
    hdrL->num_blks   = (uint16_t)num_blks;

    if (is_stereo) {
        fw_set_hdr_name(hdrR, name);
        hdrR->num_frames = num_frames;
        hdrR->byte_len   = num_frames << 1;
        hdrR->flags      = (sr << 12) | 0x800u | 0x80u | 3u;
        hdrR->uid        = 0u;
        hdrR->start_blk  = (uint16_t)blkR;
        hdrR->num_blks   = (uint16_t)num_blks;
    }

    /* 6. Register 1-to-1 block ownership (for Sample Delete / Optimize safety) */
    for (int b = 0; b < num_blks; b++) {
        fw_pool_set_blk_owner(FW_SAMPLE_POOL, blkL + b, dst_id * 2);
        if (is_stereo) {
            fw_pool_set_blk_owner(FW_SAMPLE_POOL, blkR + b, dst_id * 2 + 1);
        }
    }

    /* 7. Init User Sample params & sync to Core 1 DSP via IPC 0xFFFA */
    fw_sample_init_params(dst_id);
    uint32_t token = fw_ipc_get_token();
    uint8_t *ipc_buf = fw_ipc_get_buf(token);
    if (ipc_buf) {
        fw_build_sample_ipc(dst_id, ipc_buf + 0x10u);
        fw_ipc_send(0xFFFAu, 1u, token);
    }

    return dst_id;
}

static void clear_pad_all_wmt(uint8_t target_pad) {
    int note = fw_pad_to_note(FW_UI_MGR, (int)target_pad);
    int pad_idx = note - 0x15;
    if (pad_idx < 0 || pad_idx >= 88) return;

    int grp  = 0xFB + (int)g_chop.track;
    int clip = (int)g_chop.clip;
    uint8_t *inst_base = FW_DRUM_INST_TBL[g_chop.track];
    if (!inst_base) return;
    uint8_t *inst = inst_base + (uint32_t)g_chop.clip * 0x4A40u + (uint32_t)pad_idx * 0xD8u;

    for (uint32_t w = 0u; w < 4u; w++) {
        /* Call fw_set_param BEFORE zeroing wmt[0] so DrumInst::SetVal always pushes OFF to Core 1 */
        fw_set_param(grp, 0x8E1 + (int)w * 0x17, 0, pad_idx, clip);
        uint8_t *wmt = inst + 0x1Cu + w * 0x1Cu;
        wmt[0] = 0u; /* SW = OFF */
        wmt[4] = 0u; wmt[5] = 0u; /* WAV_NUM_L = 0 */
        wmt[6] = 0u; wmt[7] = 0u; /* WAV_NUM_R = 0 */
    }
}

SEC_SLICE static void bind_sample_to_pad(uint8_t target_pad, uint8_t target_wmt, int dst_sample_id) {
    int note = fw_pad_to_note(FW_UI_MGR, (int)target_pad);
    int pad_idx = note - 0x15;
    if (pad_idx < 0 || pad_idx >= 88) return;

    if (target_wmt == 0u) {
        /* W1 (default): use stock clean pad init + WMT1 bind (wipes WMT2..4, resets pitch/filter/EQ, sets pad name) */
        fw_bind_pad_wav(g_chop.track, g_chop.clip, pad_idx, dst_sample_id);
        return;
    }

    /* W2..W4: non-destructive layer bind (preserves other WMT layers for intentional layering) */
    uint8_t *inst_base = FW_DRUM_INST_TBL[g_chop.track];
    uint8_t *kit_base  = FW_KIT_PAD_TBL[g_chop.track];
    if (!inst_base || !kit_base) return;

    uint8_t *inst = inst_base + (uint32_t)g_chop.clip * 0x4A40u + (uint32_t)pad_idx * 0xD8u;
    uint8_t *wmt  = inst + 0x1Cu + (uint32_t)target_wmt * 0x1Cu;

    uint16_t wav_val = (uint16_t)(dst_sample_id + 1);
    wmt[0] = 1u;                         /* SW = ON */
    wmt[1] = 2u;                         /* GTYPE = SAMP */
    wmt[2] = 0u; wmt[3] = 0u;            /* GID = 0 */
    wmt[4] = (uint8_t)(wav_val & 0xFFu); /* WAV_NUM_L */
    wmt[5] = (uint8_t)(wav_val >> 8);
    wmt[6] = 0u; wmt[7] = 0u;            /* WAV_NUM_R = 0 */
    if (wmt[0x11] == 0u) wmt[0x11] = 127u; /* Level default */
    if (wmt[0x18] == 0u && wmt[0x19] == 0u) {
        wmt[0x18] = 1u;                  /* Velo Lo = 1 */
        wmt[0x19] = 127u;                /* Velo Hi = 127 */
    }

    uint8_t *kit_pad = kit_base + (uint32_t)g_chop.clip * 0x0D00u + 0xA0u + (uint32_t)pad_idx * 20u;
    if (kit_pad[0] == 0u && kit_pad[1] == 0u) {
        kit_pad[0] = 1u;
    }
}

SEC_SLICE static void execute_assign(void) {
    if (!g_chop.is_drum) return;

    uint8_t opt = g_chop.asgn_opt;
    uint8_t wmt = g_chop.asgn_wmt;

    /* Dedicated 1-click Clear All 16 Pads (WMT1..4) */
    if (opt == ASGN_CLR_ALL) {
        restore_orig_params();
        for (uint8_t p = 0u; p < 16u; p++) {
            clear_pad_all_wmt(p);
        }
        fw_mark_clip_dirty(g_chop.track, g_chop.clip);
        fw_sync_clip_to_core1(FW_CLIP_MGR, g_chop.track, g_chop.clip);
        set_msg("CLEARED 16 PADS!");
        return;
    }

    if (!g_chop.valid || g_chop.num_slices == 0u) return;

    /* Restore source sample Start/End before copying */
    restore_orig_params();

    int created = 0;

    if (opt >= ASGN_MAN_BASE) {
        /* Manual assign: single selected slice -> chosen Pad (0..15) */
        uint8_t dst_pad = (uint8_t)(opt - ASGN_MAN_BASE);
        uint8_t s = g_chop.sel_slice;
        uint32_t f0 = g_chop.marks[s];
        uint32_t f1 = get_slice_end(s);
        if (f1 > f0) {
            int sid = create_sliced_sample(s, f0, f1 - f0);
            if (sid < 0) {
                set_msg("ERR: WAVE FULL! ");
                return;
            }
            bind_sample_to_pad(dst_pad, wmt, sid);
            created = 1;
        }
        fw_mark_clip_dirty(g_chop.track, g_chop.clip);
        fw_sync_clip_to_core1(FW_CLIP_MGR, g_chop.track, g_chop.clip);
        if (dst_pad != g_chop.pad) {
            solo_source_wmt();
        }

        char ok[16] = "OK S01->P01 W1  ";
        fmt_u32_fixed(&ok[4], (uint32_t)s + 1u, 2);
        fmt_u32_fixed(&ok[9], (uint32_t)dst_pad + 1u, 2);
        ok[13] = (char)('1' + wmt);
        set_msg(ok);
        return;
    }

    /* Auto-assign multiple slices */
    uint8_t start_slice = (opt == ASGN_AUTO_HI) ? 16u : 0u;
    uint8_t start_pad   = (opt == ASGN_AUTO_CUR) ? g_chop.pad : 0u;

    for (uint8_t k = 0; k < 16u; k++) {
        uint8_t s = (uint8_t)(start_slice + k);
        uint8_t dst_pad = (uint8_t)(start_pad + k);
        if (dst_pad >= 16u) break;
        if (s >= g_chop.num_slices) {
            /* In ALL>P1 / 17+>P1 on W1: silence leftover unused pads cleanly */
            if (wmt == 0u && opt != ASGN_AUTO_CUR) {
                clear_pad_all_wmt(dst_pad);
            }
            continue;
        }
        uint32_t f0 = g_chop.marks[s];
        uint32_t f1 = get_slice_end(s);
        if (f1 <= f0) continue;

        int sid = create_sliced_sample(s, f0, f1 - f0);
        if (sid < 0) break;
        bind_sample_to_pad(dst_pad, wmt, sid);
        created++;
    }

    if (created > 0) {
        fw_mark_clip_dirty(g_chop.track, g_chop.clip);
        fw_sync_clip_to_core1(FW_CLIP_MGR, g_chop.track, g_chop.clip);
        char ok[16] = "DONE! 00 PADS OK";
        fmt_u32_fixed(&ok[6], (uint32_t)created, 2);
        set_msg(ok);
    } else {
        set_msg("ERR: WAVE FULL! ");
    }
}

/* ========================================================================
 * CODE1 (.text): Sample Loader, Live Audition, LCD Waveform Renderer, Entry
 * ======================================================================== */

SEC_DET static void load_pad_sample(uint8_t pad, int preferred_wmt) {
    restore_orig_params();
    g_chop.pad = pad;
    g_chop.note = (uint8_t)fw_pad_to_note(FW_UI_MGR, (int)pad);
    g_chop.valid = 0;
    g_chop.sample_id = -1;
    g_chop.msg_active = 0;

    int sid = -1;
    uint8_t chosen_wmt = (preferred_wmt >= 0 && preferred_wmt < 4) ? (uint8_t)preferred_wmt : 0u;

    if (preferred_wmt >= 0 && preferred_wmt < 4) {
        sid = get_wmt_sample_id(g_chop.track, g_chop.clip, pad, chosen_wmt);
    } else {
        for (uint8_t w = 0; w < 4u; w++) {
            int s = get_wmt_sample_id(g_chop.track, g_chop.clip, pad, w);
            if (s >= 0) {
                sid = s;
                chosen_wmt = w;
                break;
            }
        }
    }

    g_chop.wmt_idx = chosen_wmt;
    if (sid < 0) return;

    if (!fw_get_sample_info(&g_chop.info, sid) || g_chop.info.frames == 0u || g_chop.info.wramL == 0u) {
        return;
    }

    g_chop.sample_id = (int16_t)sid;
    g_chop.valid = 1;
    /* Solo the active WMT layer on the source pad so other WMT layers never bleed during audition */
    solo_source_wmt();
    g_chop.orig_start = fw_get_param(0x11F, 0x98D, -1, sid);
    g_chop.orig_end   = fw_get_param(0x11F, 0x98F, -1, sid);
    if (g_chop.orig_start < 0 || (uint32_t)g_chop.orig_start >= g_chop.info.frames) {
        g_chop.orig_start = 0;
    }
    if (g_chop.orig_end <= g_chop.orig_start || (uint32_t)g_chop.orig_end > g_chop.info.frames) {
        g_chop.orig_end = (int32_t)g_chop.info.frames;
    }

    /* Default to 4 equal slices right away so user sees slice marks immediately! */
    g_chop.auto_sens = 50u;
    g_chop.sel_slice = 0u;
    g_chop.zoom_idx = 0u;
    g_chop.view_start = 0u;
    g_chop.view_len = g_chop.info.frames;
    apply_step_chop(1u); /* 4 equal slices */

    /* Global peak scan (~512 Wave RAM reads) */
    uint32_t stride = g_chop.info.frames >> 9;
    if (stride == 0u) stride = 1u;
    uint32_t mx = 0u;
    uint32_t wramL = g_chop.info.wramL;
    uint32_t wramR = g_chop.info.is_stereo ? g_chop.info.wramR : 0u;
    for (uint32_t i = 0u; i < g_chop.info.frames; i += stride) {
        int32_t v = (int16_t)fw_waveram_read16(wramL + (i << 1));
        uint32_t av = (uint32_t)(v < 0 ? -v : v);
        if (av > mx) mx = av;
        if (wramR != 0u) {
            int32_t vr = (int16_t)fw_waveram_read16(wramR + (i << 1));
            uint32_t avr = (uint32_t)(vr < 0 ? -vr : vr);
            if (avr > mx) mx = avr;
        }
    }
    g_chop.peak_max = (uint16_t)u32_max(mx, 2048u);
    recompute_waveform();
}

static void send_realtime_note(uint8_t track, uint8_t note, uint8_t vel) {
    uint8_t pkt[4];
    uint32_t msg_obj[8];
    if (vel > 0u) {
        pkt[0] = 0x19u; /* cable 1, CIN 9 = Note On */
        pkt[1] = (uint8_t)(0x90u | (track & 0x0Fu));
        pkt[2] = note;
        pkt[3] = vel;
    } else {
        pkt[0] = 0x18u; /* cable 1, CIN 8 = Note Off */
        pkt[1] = (uint8_t)(0x80u | (track & 0x0Fu));
        pkt[2] = note;
        pkt[3] = 0x40u;
    }
    fw_midi_pkt_init(msg_obj, pkt);
    fw_midi_send(2, msg_obj); /* port 2 = internal Core 1 synth */
    fw_midi_pkt_free(msg_obj);
}

static void audition_range(uint32_t st, uint32_t en) {
    if (!g_chop.valid || g_chop.sample_id < 0) return;
    if (en <= st) en = u32_min(st + 64u, g_chop.info.frames);
    if (en > g_chop.info.frames) en = g_chop.info.frames;
    /* Choke previous slice cleanly before triggering new range */
    send_realtime_note(g_chop.track, g_chop.note, 0u);
    fw_set_param(0x11F, 0x98D, (int32_t)st, -1, g_chop.sample_id);
    fw_set_param(0x11F, 0x98F, (int32_t)en, -1, g_chop.sample_id);
    g_chop.prm_dirty = 1;
    g_chop.auditioning = 1;
    send_realtime_note(g_chop.track, g_chop.note, 110u);
}

SEC_SLICE static void render_lcd(void) {
    char row0[16];
    for (int i = 0; i < 16; i++) row0[i] = ' ';

    if (!g_chop.is_drum) {
        lcd_write_row(0, "CHOP: DRUM ONLY ");
        lcd_write_row(1, "PRESS EXIT      ");
        return;
    }

    if (!g_chop.valid) {
        row0[0] = (g_chop.mode == MODE_SRC) ? '>' : 'P';
        if (g_chop.mode == MODE_SRC) {
            row0[1] = 'W'; row0[2] = 'M'; row0[3] = 'T';
            row0[4] = (char)('1' + g_chop.wmt_idx);
            row0[5] = ':'; row0[6] = ' ';
            const char *ns = "NO SAMPLE";
            for (int i = 0; i < 9; i++) row0[7 + i] = ns[i];
            lcd_write_row(0, row0);
            lcd_write_row(1, "DIAL=WMT PAD=SRC");
        } else {
            fmt_u32_fixed(&row0[1], (uint32_t)(g_chop.pad + 1u), 2);
            row0[3] = ' '; row0[4] = 'W';
            row0[5] = (char)('1' + g_chop.wmt_idx);
            row0[6] = ':';
            const char *ns = "NO SAMPLE";
            for (int i = 0; i < 9; i++) row0[7 + i] = ns[i];
            lcd_write_row(0, row0);
            lcd_write_row(1, "TAP PAD OR EXIT ");
        }
        return;
    }

    /* Row 0: status message OR active mode header (16 chars exact) */
    if (g_chop.msg_active) {
        lcd_write_row(0, g_chop.msg);
    } else if (g_chop.mode == MODE_STEP) {
        /* ">STEP:16  S01/16" */
        const char *h = ">STEP:00  S00/00";
        for (int i = 0; i < 16; i++) row0[i] = h[i];
        fmt_u32_fixed(&row0[6],  (uint32_t)g_chop.num_slices, 2);
        fmt_u32_fixed(&row0[11], (uint32_t)g_chop.sel_slice + 1u, 2);
        fmt_u32_fixed(&row0[14], (uint32_t)g_chop.num_slices, 2);
        lcd_write_row(0, row0);
    } else if (g_chop.mode == MODE_AUTO) {
        /* ">AUTO:50  N:12  " */
        const char *h = ">AUTO:00  N:00  ";
        for (int i = 0; i < 16; i++) row0[i] = h[i];
        fmt_u32_fixed(&row0[6],  (uint32_t)g_chop.auto_sens, 2);
        fmt_u32_fixed(&row0[12], (uint32_t)g_chop.num_slices, 2);
        lcd_write_row(0, row0);
    } else if (g_chop.mode == MODE_MOVE) {
        /* ">M01 012345 Z1x " */
        row0[0] = '>'; row0[1] = 'M';
        fmt_u32_fixed(&row0[2], (uint32_t)g_chop.sel_slice + 1u, 2);
        row0[4] = ' ';
        uint32_t mf = g_chop.marks[g_chop.sel_slice];
        if (mf < 1000000u) {
            fmt_u32_fixed(&row0[5], mf, 6);
        } else {
            fmt_u32_fixed(&row0[5], mf / 44u, 5);
            row0[10] = 'm';
        }
        row0[11] = ' '; row0[12] = 'Z';
        fmt_zoom(&row0[13], g_chop.zoom_idx);
        lcd_write_row(0, row0);
    } else if (g_chop.mode == MODE_ADD) {
        /* ">ADD 012345 N:08" */
        const char *h = ">ADD 000000 N:00";
        for (int i = 0; i < 16; i++) row0[i] = h[i];
        if (g_chop.cursor < 1000000u) {
            fmt_u32_fixed(&row0[5], g_chop.cursor, 6);
        } else {
            fmt_u32_fixed(&row0[5], g_chop.cursor / 44u, 5);
            row0[10] = 'm';
        }
        fmt_u32_fixed(&row0[14], (uint32_t)g_chop.num_slices, 2);
        lcd_write_row(0, row0);
    } else if (g_chop.mode == MODE_ZOOM) {
        /* ">Z 4x  S01/16 ST" */
        const char *h = ">Z 1x  S00/00 MO";
        for (int i = 0; i < 16; i++) row0[i] = h[i];
        fmt_zoom(&row0[3], g_chop.zoom_idx);
        fmt_u32_fixed(&row0[8],  (uint32_t)g_chop.sel_slice + 1u, 2);
        fmt_u32_fixed(&row0[11], (uint32_t)g_chop.num_slices, 2);
        if (g_chop.info.is_stereo) { row0[14] = 'S'; row0[15] = 'T'; }
        lcd_write_row(0, row0);
    } else if (g_chop.mode == MODE_ASGN) {
        /* Assign target + WMT */
        if (g_chop.asgn_opt == ASGN_AUTO_P1) {
            const char *h = ">ASGN ALL>P1  W1";
            for (int i = 0; i < 16; i++) row0[i] = h[i];
            row0[15] = (char)('1' + g_chop.asgn_wmt);
        } else if (g_chop.asgn_opt == ASGN_AUTO_CUR) {
            const char *h = ">ASGN ALL>P01 W1";
            for (int i = 0; i < 16; i++) row0[i] = h[i];
            fmt_u32_fixed(&row0[11], (uint32_t)g_chop.pad + 1u, 2);
            row0[15] = (char)('1' + g_chop.asgn_wmt);
        } else if (g_chop.asgn_opt == ASGN_AUTO_HI) {
            const char *h = ">ASGN 17+>P1  W1";
            for (int i = 0; i < 16; i++) row0[i] = h[i];
            row0[15] = (char)('1' + g_chop.asgn_wmt);
        } else if (g_chop.asgn_opt == ASGN_CLR_ALL) {
            const char *h = ">ASGN CLR P1-16 ";
            for (int i = 0; i < 16; i++) row0[i] = h[i];
        } else {
            /* Manual single slice -> target Pad */
            const char *h = ">ASGN S01>P01 W1";
            for (int i = 0; i < 16; i++) row0[i] = h[i];
            fmt_u32_fixed(&row0[7],  (uint32_t)g_chop.sel_slice + 1u, 2);
            fmt_u32_fixed(&row0[11], (uint32_t)(g_chop.asgn_opt - ASGN_MAN_BASE) + 1u, 2);
            row0[15] = (char)('1' + g_chop.asgn_wmt);
        }
        lcd_write_row(0, row0);
    } else {
        /* MODE_SRC: ">SRC P01 W1 1.2s" */
        const char *h = ">SRC P00 W1 00.0";
        for (int i = 0; i < 16; i++) row0[i] = h[i];
        fmt_u32_fixed(&row0[6], (uint32_t)g_chop.pad + 1u, 2);
        row0[10] = (char)('1' + g_chop.wmt_idx);
        uint32_t tenths = g_chop.info.frames / 4410u;
        uint32_t secs = tenths / 10u;
        if (secs > 99u) secs = 99u;
        fmt_u32_fixed(&row0[12], secs, 2);
        row0[15] = (char)('0' + (tenths % 10u));
        lcd_write_row(0, row0);
    }

    /* Mark all visible slice columns on Row 1 */
    for (int c = 0; c < 16; c++) g_chop.col_mark[c] = 0u;
    uint32_t vstart = g_chop.view_start;
    uint32_t vlen   = g_chop.view_len;
    if (vlen > 0u) {
        for (uint8_t i = 0; i < g_chop.num_slices; i++) {
            uint32_t m = g_chop.marks[i];
            if (m >= vstart && m < vstart + vlen) {
                int mc = (int)(((m - vstart) * 16u) / vlen);
                if (mc > 15) mc = 15;
                g_chop.col_mark[mc] = 1u;
            }
        }
    }

    /* Active focus frame: free cursor in MODE_ADD, otherwise selected slice mark */
    uint32_t focus = (g_chop.mode == MODE_ADD) ? g_chop.cursor : g_chop.marks[g_chop.sel_slice];
    int cur_col = -1;
    if (vlen > 0u && focus >= vstart && focus < vstart + vlen) {
        cur_col = (int)(((focus - vstart) * 16u) / vlen);
        if (cur_col > 15) cur_col = 15;
    }

    if (cur_col >= 0) {
        upload_cursor_cgram(g_chop.col_h[cur_col]);
    }

    /* Row 1: 16-column waveform + active cursor (CGRAM 0) + other slice markers (CGRAM 7) */
    for (int c = 0; c < 16; c++) {
        uint8_t ch;
        if (c == cur_col) {
            ch = 0u; /* Selected slice / cursor */
        } else if (g_chop.col_mark[c]) {
            ch = 7u; /* Other slice / transient mark */
        } else if (g_chop.col_h[c] == 0u) {
            ch = (uint8_t)' ';
        } else {
            ch = g_chop.col_h[c]; /* CGRAM 1..6 */
        }
        fw_lcd_putc(c, 1, ch);
    }
}

static void adjust_zoom(int delta) {
    int z = (int)g_chop.zoom_idx + (delta > 0 ? 1 : -1);
    if (z < 0) z = 0;
    if (z >= ZOOM_LEVELS) z = ZOOM_LEVELS - 1;
    if ((uint8_t)z != g_chop.zoom_idx) {
        g_chop.zoom_idx = (uint8_t)z;
        uint32_t focus = (g_chop.mode == MODE_ADD) ? g_chop.cursor : g_chop.marks[g_chop.sel_slice];
        center_viewport_on(focus);
        recompute_waveform();
    }
}

static void move_free_cursor(int delta) {
    if (!g_chop.valid || g_chop.info.frames == 0u) return;
    uint32_t step = g_chop.view_len >> 4;
    if (step == 0u) step = 1u;
    int32_t next = (int32_t)g_chop.cursor + delta * (int32_t)step;
    if (next < 0) next = 0;
    if ((uint32_t)next >= g_chop.info.frames) next = (int32_t)(g_chop.info.frames - 1u);
    g_chop.cursor = (uint32_t)next;
    uint32_t old_vs = g_chop.view_start;
    update_viewport_for(g_chop.cursor);
    if (g_chop.view_start != old_vs) {
        recompute_waveform();
    }
}

/* ---- Screen 0x68 Entry Point (must be at 0x602D4FE0 via .text.entry) ---- */
SEC_ENTRY
int sample_chop_event(const UiEvent *ev) {
    if (!ev) return 0;

    switch (ev->type) {
    case EV_INIT:
        return 0;

    case EV_OPEN: {
        zero_mem(&g_chop, sizeof(g_chop));
        g_chop.active = 1;
        g_chop.sample_id = -1;

        int trk = fw_get_param(3, 0, -1, 0);
        if (trk < 0 || trk > 7) trk = 0;
        g_chop.track = (uint8_t)trk;
        g_chop.is_drum = (fw_get_trk_type(trk) == 1) ? 1u : 0u;
        g_chop.clip = (uint8_t)fw_get_cur_clip(-1);

        upload_static_cgram();

        if (g_chop.is_drum) {
            int cur_pad = fw_get_sys_val(0x63);
            if (cur_pad < 0 || cur_pad > 15) cur_pad = 0;
            load_pad_sample((uint8_t)cur_pad, -1);
        }

        render_lcd();
        return 0;
    }

    case EV_CLOSE:
        if (g_chop.auditioning) {
            send_realtime_note(g_chop.track, g_chop.note, 0u);
            g_chop.auditioning = 0;
        }
        restore_orig_params();
        fw_cgram_cache_inval(FW_CGRAM_CACHE, 8);
        g_chop.active = 0;
        return 0;

    case EV_REDRAW:
        if (g_chop.active) {
            render_lcd();
        }
        return 0;

    case EV_BTN_DOWN: {
        int key = ev->arg1;
        if (key == KEY_EXIT) {
            if (g_chop.auditioning) {
                send_realtime_note(g_chop.track, g_chop.note, 0u);
                g_chop.auditioning = 0;
            }
            restore_orig_params();
            fw_pop_screen();
            return 1;
        }

        g_chop.msg_active = 0;

        if (key == KEY_VALUE) {
            if (fw_is_btn_pressed(KEY_SHIFT) && g_chop.valid) {
                /* SHIFT + VALUE = execute mode action */
                if (g_chop.mode == MODE_MOVE) {
                    manual_delete_slice(g_chop.sel_slice);
                } else if (g_chop.mode == MODE_ADD) {
                    manual_insert_slice(g_chop.cursor);
                } else if (g_chop.mode == MODE_ASGN) {
                    execute_assign();
                } else if (g_chop.mode == MODE_AUTO) {
                    run_transient_detect(g_chop.auto_sens);
                }
            } else {
                /* Short VALUE press = cycle mode */
                g_chop.mode = (uint8_t)((g_chop.mode + 1u) % MODE_COUNT);
                if (g_chop.mode == MODE_ADD && g_chop.valid) {
                    g_chop.cursor = g_chop.marks[g_chop.sel_slice];
                }
            }
            render_lcd();
            return 1;
        }

        if (key == KEY_LEFT1 || key == KEY_LEFT2 || key == KEY_RIGHT1 || key == KEY_RIGHT2) {
            int d = (key == KEY_RIGHT1 || key == KEY_RIGHT2) ? 1 : -1;
            if (g_chop.valid && g_chop.num_slices > 0u) {
                int s = (int)g_chop.sel_slice + d;
                if (s < 0) s = 0;
                if (s >= (int)g_chop.num_slices) s = (int)g_chop.num_slices - 1;
                g_chop.sel_slice = (uint8_t)s;
                uint32_t old_vs = g_chop.view_start;
                update_viewport_for(g_chop.marks[s]);
                if (g_chop.view_start != old_vs) recompute_waveform();
            }
            render_lcd();
            return 1;
        }

        if (key >= KEY_PAD_FIRST && key <= KEY_PAD_LAST && g_chop.is_drum) {
            uint8_t tapped = (uint8_t)(key - KEY_PAD_FIRST);

            /* In MODE_SRC (or when no sample loaded): switch source pad */
            if (g_chop.mode == MODE_SRC || !g_chop.valid) {
                load_pad_sample(tapped, -1);
                render_lcd();
                return 0;
            }

            /* In MODE_ASGN: tapping a pad sets manual destination pad directly */
            if (g_chop.mode == MODE_ASGN) {
                g_chop.asgn_opt = (uint8_t)(ASGN_MAN_BASE + tapped);
                render_lcd();
                return 1;
            }

            /* In MODE_ADD: audition from free cursor */
            if (g_chop.mode == MODE_ADD) {
                audition_range(g_chop.cursor, (uint32_t)g_chop.orig_end);
                return 1;
            }

            /* In STEP / AUTO / MOVE / ZOOM: Pads 1..16 audition & select slices 1..16 (SHIFT = 17..32) */
            uint8_t slice_idx = tapped;
            if (fw_is_btn_pressed(KEY_SHIFT) && g_chop.num_slices > 16u) {
                slice_idx = (uint8_t)(tapped + 16u);
            }
            if (slice_idx < g_chop.num_slices) {
                g_chop.sel_slice = slice_idx;
                uint32_t f0 = g_chop.marks[slice_idx];
                uint32_t f1 = get_slice_end(slice_idx);
                uint32_t old_vs = g_chop.view_start;
                update_viewport_for(f0);
                if (g_chop.view_start != old_vs) recompute_waveform();
                audition_range(f0, f1);
                render_lcd();
            }
            return 1;
        }
        return 0;
    }

    case EV_BTN_UP: {
        int key = ev->arg1;
        if (key >= KEY_PAD_FIRST && key <= KEY_PAD_LAST && g_chop.auditioning) {
            send_realtime_note(g_chop.track, g_chop.note, 0u);
            g_chop.auditioning = 0;
            return 1;
        }
        return 0;
    }

    case EV_DIAL: {
        if (ev->arg1 != 0) return 0; /* VALUE dial only */
        int delta = ev->arg2;
        if (delta == 0 || !g_chop.is_drum) return 1;
        g_chop.msg_active = 0;

        int dir = (delta > 0) ? 1 : -1;
        int shift = fw_is_btn_pressed(KEY_SHIFT);

        if (g_chop.mode == MODE_SRC) {
            int w = (int)g_chop.wmt_idx + dir;
            if (w < 0) w = 0;
            if (w > 3) w = 3;
            if ((uint8_t)w != g_chop.wmt_idx) {
                load_pad_sample(g_chop.pad, w);
            }
        } else if (!g_chop.valid) {
            return 1;
        } else if (g_chop.mode == MODE_STEP) {
            int s = (int)g_chop.step_idx + dir;
            if (s < 0) s = 0;
            if (s > 4) s = 4;
            if ((uint8_t)s != g_chop.step_idx) {
                apply_step_chop((uint8_t)s);
            }
        } else if (g_chop.mode == MODE_AUTO) {
            int step = shift ? 1 : 5;
            int sens = (int)g_chop.auto_sens + dir * step;
            if (sens < 5) sens = 5;
            if (sens > 95) sens = 95;
            run_transient_detect((uint8_t)sens);
        } else if (g_chop.mode == MODE_MOVE) {
            if (shift) adjust_zoom(delta);
            else move_selected_slice(delta);
        } else if (g_chop.mode == MODE_ADD) {
            if (shift) adjust_zoom(delta);
            else move_free_cursor(delta);
        } else if (g_chop.mode == MODE_ZOOM) {
            adjust_zoom(delta);
        } else if (g_chop.mode == MODE_ASGN) {
            if (shift) {
                int w = (int)g_chop.asgn_wmt + dir;
                if (w < 0) w = 0;
                if (w > 3) w = 3;
                g_chop.asgn_wmt = (uint8_t)w;
            } else {
                int opt = (int)g_chop.asgn_opt + dir;
                if (opt == ASGN_AUTO_HI && g_chop.num_slices <= 16u) {
                    opt += dir; /* skip 17+ option when <= 16 slices */
                }
                if (opt < 0) opt = 0;
                if (opt > ASGN_OPT_MAX) opt = ASGN_OPT_MAX;
                g_chop.asgn_opt = (uint8_t)opt;
            }
        }

        render_lcd();
        return 1;
    }

    default:
        return 0;
    }
}
