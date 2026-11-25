/*
 * MC-101 v1.82 Button Navigation Mode (Hands-Free VALUE Dial Replacement)
 *
 * Linked into verified zero-padded SDRAM cave (0x602DA600..0x602DAE7F):
 *   NAV-RAM  (BSS) : 0x602DA600 (256 B)   — NavState (g_nav pinned first via .nav_state)
 *   NAV-CODE (CODE): 0x602DA700 (1,920 B) — tick/dispatch handlers + LED blink override
 *
 * Behavior (global across all menus and screens):
 *   - Hold '>' (MEAS_R) alone for 600 ms : Enter Button Nav Mode
 *     (short tap < 600 ms still advances measures normally; combos like SHIFT+>, SEQ+> pass through)
 *   - While active ('<', '>', 'TEMPO', 'EXIT' LEDs blink continuously):
 *       '<'     : VALUE dial left  (-1); hold for continuous scroll (320 ms delay, 85 ms repeat)
 *       '>'     : VALUE dial right (+1); hold for continuous scroll (320 ms delay, 85 ms repeat)
 *       'TEMPO' : VALUE press / ENTER (OK / Select); SHIFT+TEMPO = SHIFT+ENTER
 *       'EXIT'  : unchanged (back / exit menu)
 *       All other buttons keep their exact stock functions.
 *   - Press SHIFT + '<' : Exit Button Nav Mode and restore normal button & LED behavior.
 */

#include <stdint.h>

typedef struct {
    int16_t type;
    int16_t arg1;
    int16_t arg2;
    int16_t arg3;
} UiEvent;

#define FW_FN(type, addr) ((type)((addr) | 1u))

#define fw_ui_dispatch_event    FW_FN(void     (*)(UiEvent *),             0x6001DA18u)
#define fw_ui_drain_queue       FW_FN(void     (*)(void),                  0x6001DE38u)
#define fw_post_dial            FW_FN(void     (*)(int, int),              0x6001D974u)
#define fw_get_ms               FW_FN(uint32_t (*)(void),                  0x6001CC50u)
#define fw_is_btn_pressed       FW_FN(int      (*)(int),                   0x6001CBD8u)
#define fw_set_led              FW_FN(void     (*)(int, int, int),         0x6001E6B8u)
#define fw_led_refresh_all      FW_FN(void     (*)(void *),                0x60097A02u)

#define FW_BTN_BITMAP           ((const volatile uint8_t *)0x605C40B0u)
#define FW_LED_MGR              ((void *)0x605C4E10u)
#define CUR_TRACK_VAR           (*(const volatile uint32_t *)0x60FC03FCu)

/* Event types */
#define EV_BTN_DOWN     5
#define EV_BTN_UP       6
#define EV_DIAL         7

/* Physical Key IDs (verified from 0x602CABA4) */
#define KEY_SHIFT       0x00
#define KEY_MEAS_L      0x03
#define KEY_MEAS_R      0x04
#define KEY_SEL_FIRST   0x06
#define KEY_SEL_LAST    0x09
#define KEY_TEMPO       0x0F
#define KEY_EXIT        0x10
#define KEY_ENTER       0x11

/* Physical LED IDs (verified from 0x602CAC44 & 0x601D0554) */
#define LED_MEAS_L      3
#define LED_MEAS_R      4
#define LED_TEMPO       15
#define LED_EXIT        16

/* Timing constants (ms) */
#define HOLD_ENTER_MS   600u
#define REPEAT_DELAY_MS 320u
#define REPEAT_RATE_MS   85u

typedef struct {
    uint8_t  active;           /* +0x00: 1 = Button Nav Mode active (read by tramp_nav_led_override) */
    uint8_t  hold_r_pending;   /* +0x01: 1 = waiting to see if '>' is short tap or 600ms hold */
    uint8_t  hold_r_consumed;  /* +0x02: 1 = '>' hold entered Nav Mode; swallow its release */
    uint8_t  rep_key;          /* +0x03: KEY_MEAS_L or KEY_MEAS_R while repeating, else 0 */
    uint8_t  tempo_mapped;     /* +0x04: 1 = TEMPO down was rewritten to ENTER; match its release */
    uint8_t  suppress_l_up;    /* +0x05: 1 = SHIFT+'<' exited Nav Mode; swallow '<' release */
    uint8_t  pad0[2];
    uint32_t hold_r_start_ms;   /* +0x08 */
    uint32_t rep_next_ms;       /* +0x0C */
    uint8_t  allow_bank_toggle; /* +0x10: 1 = allow resolve_sel_track to toggle bank */
    uint8_t  pending_sel_key;   /* +0x11: SEL1..4 key if last btn/pad event was its Down on cur track */
    uint8_t  pad1[2];
} NavState;

__attribute__((section(".nav_state"))) NavState g_nav;

static int any_btn_held(void) {
    /* Keys 0..47 live in bytes 0..5 of the global pressed-key bitmap at 0x605C40B0 */
    for (int i = 0; i < 6; i++) {
        if (FW_BTN_BITMAP[i] != 0u) return 1;
    }
    return 0;
}

static void nav_blink_leds(void) {
    fw_set_led(LED_MEAS_L, 3, 0xFF);
    fw_set_led(LED_MEAS_R, 3, 0xFF);
    fw_set_led(LED_TEMPO,  3, 0xFF);
    fw_set_led(LED_EXIT,   3, 0xFF);
}

static void nav_restore_leds(void) {
    /* Clear all 4 LEDs first (in case active screen doesn't redraw < or >), then full stock refresh */
    fw_set_led(LED_MEAS_L, 0, 0);
    fw_set_led(LED_MEAS_R, 0, 0);
    fw_set_led(LED_TEMPO,  0, 0);
    fw_set_led(LED_EXIT,   0, 0);
    fw_led_refresh_all(FW_LED_MGR);
}

static void dispatch_btn(int16_t type, int16_t key) {
    UiEvent e;
    e.type = type;
    e.arg1 = key;
    e.arg2 = 0;
    e.arg3 = 0;
    fw_ui_dispatch_event(&e);
}

static void dispatch_dial(int16_t delta) {
    UiEvent e;
    e.type = EV_DIAL;
    e.arg1 = 0; /* VALUE dial */
    e.arg2 = delta;
    e.arg3 = 0;
    fw_ui_dispatch_event(&e);
}

/* Hooked at 0x6002EC24 (replaces bl 0x6001de38 in main UI task loop) */
__attribute__((noinline))
void nav_tick_and_drain(void) {
    uint32_t now = fw_get_ms();

    /* 1. Check 600ms hold on '>' to enter Button Nav Mode */
    if (!g_nav.active && g_nav.hold_r_pending) {
        if (any_btn_held()) {
            /* Another key was pressed before 600ms: flush deferred '>' down */
            g_nav.hold_r_pending = 0u;
            dispatch_btn(EV_BTN_DOWN, KEY_MEAS_R);
        } else if ((uint32_t)(now - g_nav.hold_r_start_ms) >= HOLD_ENTER_MS) {
            g_nav.hold_r_pending  = 0u;
            g_nav.hold_r_consumed = 1u;
            g_nav.active          = 1u;
            g_nav.rep_key         = 0u;
            nav_blink_leds();
        }
    }

    /* 2. Continuous auto-repeat while holding '<' or '>' in Nav Mode */
    if (g_nav.active && g_nav.rep_key != 0u) {
        if (g_nav.rep_key == KEY_MEAS_L && fw_is_btn_pressed(KEY_SHIFT)) {
            g_nav.rep_key = 0u;
        } else if ((int32_t)(now - g_nav.rep_next_ms) >= 0) {
            g_nav.rep_next_ms = now + REPEAT_RATE_MS;
            fw_post_dial(0, (g_nav.rep_key == KEY_MEAS_R) ? 1 : -1);
        }
    }

    fw_ui_drain_queue();
}

/* Hooked at 0x6001DE66 (replaces bl 0x6001da18 in central UI event queue drainer) */
__attribute__((noinline))
void nav_dispatch_event(UiEvent *ev) {
    if (!ev) return;

    int16_t type = ev->type;
    int16_t key  = ev->arg1;

    /* Release-to-toggle bank switching for SEL1..4 (0x06..0x09):
     * Only toggle on SEL-N Up if the immediately preceding button/pad event
     * was SEL-N Down while N was already the selected track. */
    if (type == EV_BTN_DOWN) {
        if (key >= KEY_SEL_FIRST && key <= KEY_SEL_LAST &&
            (CUR_TRACK_VAR & 3u) == (uint32_t)(key - KEY_SEL_FIRST)) {
            g_nav.pending_sel_key = (uint8_t)key;
        } else {
            g_nav.pending_sel_key = 0u;
        }
    } else if (type == EV_BTN_UP) {
        if (key >= KEY_SEL_FIRST && key <= KEY_SEL_LAST &&
            key == (int16_t)g_nav.pending_sel_key) {
            g_nav.pending_sel_key = 0u;
            g_nav.allow_bank_toggle = 1u;
            dispatch_btn(EV_BTN_DOWN, key);
            g_nav.allow_bank_toggle = 0u;
        } else {
            g_nav.pending_sel_key = 0u;
        }
    }

    /* Always match TEMPO->ENTER release even if Nav Mode just exited */
    if (type == EV_BTN_UP && key == KEY_TEMPO && g_nav.tempo_mapped) {
        g_nav.tempo_mapped = 0u;
        ev->arg1 = KEY_ENTER;
        fw_ui_dispatch_event(ev);
        return;
    }

    /* Always swallow '<' release that matched a SHIFT+'<' Nav Mode exit */
    if (type == EV_BTN_UP && key == KEY_MEAS_L && g_nav.suppress_l_up) {
        g_nav.suppress_l_up = 0u;
        return;
    }

    if (!g_nav.active) {
        /* Normal mode: detect standalone hold of '>' to enter Nav Mode */
        if (type == EV_BTN_DOWN && key == KEY_MEAS_R) {
            if (!any_btn_held()) {
                g_nav.hold_r_pending  = 1u;
                g_nav.hold_r_consumed = 0u;
                g_nav.hold_r_start_ms = fw_get_ms();
                return;
            }
        } else if (type == EV_BTN_UP && key == KEY_MEAS_R) {
            if (g_nav.hold_r_pending) {
                /* Short tap (< 600ms): emit normal '>' press + release */
                g_nav.hold_r_pending = 0u;
                dispatch_btn(EV_BTN_DOWN, KEY_MEAS_R);
                dispatch_btn(EV_BTN_UP,   KEY_MEAS_R);
                return;
            }
            if (g_nav.hold_r_consumed) {
                g_nav.hold_r_consumed = 0u;
                return;
            }
        } else if (type == EV_BTN_DOWN && g_nav.hold_r_pending) {
            /* Combo press while '>' held < 600ms: cancel hold & flush '>' down first */
            g_nav.hold_r_pending = 0u;
            dispatch_btn(EV_BTN_DOWN, KEY_MEAS_R);
        }

        fw_ui_dispatch_event(ev);
        return;
    }

    /* ---- Button Nav Mode Active ---- */
    if (type == EV_BTN_DOWN) {
        if (key == KEY_MEAS_L) {
            if (fw_is_btn_pressed(KEY_SHIFT)) {
                /* SHIFT + '<' exits Button Nav Mode */
                g_nav.active        = 0u;
                g_nav.rep_key       = 0u;
                g_nav.suppress_l_up = 1u;
                nav_restore_leds();
                return;
            }
            g_nav.rep_key     = KEY_MEAS_L;
            g_nav.rep_next_ms = fw_get_ms() + REPEAT_DELAY_MS;
            dispatch_dial(-1);
            return;
        }
        if (key == KEY_MEAS_R) {
            g_nav.rep_key     = KEY_MEAS_R;
            g_nav.rep_next_ms = fw_get_ms() + REPEAT_DELAY_MS;
            dispatch_dial(1);
            return;
        }
        if (key == KEY_TEMPO) {
            g_nav.tempo_mapped = 1u;
            ev->arg1 = KEY_ENTER;
            fw_ui_dispatch_event(ev);
            return;
        }
    } else if (type == EV_BTN_UP) {
        if (key == KEY_MEAS_L) {
            if (g_nav.rep_key == KEY_MEAS_L) g_nav.rep_key = 0u;
            return;
        }
        if (key == KEY_MEAS_R) {
            if (g_nav.rep_key == KEY_MEAS_R) g_nav.rep_key = 0u;
            g_nav.hold_r_consumed = 0u;
            return;
        }
    }

    fw_ui_dispatch_event(ev);
}
