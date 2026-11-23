/* Copyright 2026 Ed Coffey
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at https://mozilla.org/MPL/2.0/.
 */

/* Verified MC-101 v1.82 Core 0 (App1_Main) Thumb Symbols for C/Rust Patches.
 * Function addresses include the Thumb bit (| 1).
 */

PROVIDE(fw_get_param        = 0x6009CB2D);
PROVIDE(fw_set_param        = 0x6009CB9D);
PROVIDE(fw_set_dsp_param    = 0x6006C82D);
PROVIDE(fw_get_trk_type     = 0x60050D71);
PROVIDE(fw_get_cur_clip     = 0x60054BC1);
PROVIDE(fw_get_sys_val      = 0x6009BFCD);
PROVIDE(fw_is_trk_alloc     = 0x60052159);
PROVIDE(fw_ui_dispatch_evt  = 0x6001DA19);
PROVIDE(fw_ui_drain_queue   = 0x6001DE39);
PROVIDE(fw_post_dial        = 0x6001D975);
PROVIDE(fw_get_ms           = 0x6001CC51);
PROVIDE(fw_is_btn_pressed   = 0x6001CBD9);
PROVIDE(fw_set_led          = 0x6001E6B9);
PROVIDE(fw_led_refresh_all  = 0x60097A03);

PROVIDE(fw_cur_track_byte   = 0x61800200);
PROVIDE(fw_btn_bitmap       = 0x605C40B0);
PROVIDE(fw_led_mgr          = 0x605C4E10);
