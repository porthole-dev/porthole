---
id: taimen-rear-module-is-three-i2c-parts
title: taimen's rear camera module is three I2C parts -- IMX362 (0x1a), LC898214XD focus (0x72) and an LC898123F40 OIS controller (0x3e) that also holds the module calibration
scope: device:google-taimen
subsystem: camera
severity: finding
confidence: proven
evidence: 2026-09-14, kernel 7.2.2 (#50). Vendor factory image rp1a.201005.004.a1 vendor.img /etc/camera/camera_config.xml (ActuatorName lc898214xd, EepromName onsemi_lc898123f40xc, OisName lc898123f40); libois_lc898123f40.so .data (i2c_addr 0x7C, 15 x {u32 count; reg_settings_ois_t[800]} blocks in the order libmmcamera2_sensor_modules.so prints "ENABLE OIS, DISABLE OIS, MOVIE MODE, STILL MODE, CENTERING ON, CENTERING OFF, PAN TILT, SCENE ..."); wahoo kernel drivers/media/platform/msm/camera_v2/sensor/fw_update/fw_update.c (OIS_COMPONENT_I2C_ADDR_WRITE 0x7C, FW version = low byte of RAM 0x8000, 0xF100 == 0 when a command completed, 0xF01B word read of the calibration area). Live on CCI master 0 (i2c-5): 0x8000 = 0x090e0819 (fw 0x19 = OIS_CUR_FW_VERSION, no reflash needed); libmmcamera_onsemi_lc898123f40xc_eeprom.so memory map = READ 0xb4c bytes from word 0x1A00 at slave 0x7C, decompiled with Ghidra 11 headless. Driver drivers/media/i2c/lc898123f40.c loaded against the running kernel: nvmem readback byte-identical to the i2c dump. Front: 24c32-compatible EEPROM at 0x51 on CCI master 1 (vendor primax_g802l, READ 0x758 bytes).
refutes: LC898214XD and LC898123F40 are the same chip or one of them is misidentified; the OIS controller needs a firmware download at camera open; the 0xE001 shift readout proves stabilisation engages; the rear module has no readable calibration; the VL53L0 laser is dead (it is unpowered: l19 off and XSHUT gpio39 low until a DT node claims them)
first-learned: 2026-09-14
---

**The question** -- the rear camera had a working sensor and a working focus
actuator. Is there OIS, where is it, what does it need, and where did Android's
per-module calibration (AWB, lens shading, AF, PDAF) come from?

**The answer** -- a third part on the same CCI bus: ON Semi LC898123F40 at 7-bit
0x3e. It boots its own firmware from flash (the vendor's init block is EMPTY)
and takes 32-bit big-endian "high level commands" on 16-bit addresses:
wait for `0xF100 == 0`, `0xF012 = 1` + `0xF01C = 1` enables stabilisation,
`0xF012 = 0` disables, `0xF013 = 2` movie / `3` still, `0xF010 = 3/0`
centering on/off, `0xF011 = 1` pan-tilt. Writes survive the IMX362's XCLR
toggle and streaming (it is not on the sensor reset).

The module calibration lives in its flash: write word index `0x1A00 + n` to
`0xF01B`, read `0xF01B` back. The vendor blob stores each word byte-reversed
(LE of the 32-bit value); in that order every field is a big-endian u16:
`0x02` maker (`L` = LG Innotek), `0x18/0x1a/0x1c` date (2017-10-28), AWB
r/gr b/gr at `0x56/0x58` (5800 K, + gr/gb `0x5a`), `0x5e/0x60` (4000 K),
`0x66/0x68` (3100 K), all x 1/1024; lens shading 17x13 per channel at
`0x6e` R, `0x228` Gr, `0x3e2` Gb, `0x59c` B (max 1023 = centre; the vendor
reverses the rear mesh); AF 12-bit `s16(0x758)>>4` infinity, `s16(0x75c)>>4`
macro (vendor margins -0.15 / +0.3 of the span); PDAF gain maps 17x13 L at
`0x76a`, R at `0x924` (version `0x764`), DCC 8x6 at `0xae4` (>>4). The front
EEPROM uses the same AWB/LSC offsets, unreversed.

**What stays open** -- the E001 "x/y shift" readout cannot show whether
stabilisation works: at rest it is noise with OIS on or off, and under the
155 Hz haptic motor it swings the same +-190 counts either way. Only slow,
hand-like motion (a few Hz) or a handheld long-exposure blur comparison
separates the two. The OTP AF codes (268 / 1112) are not in the LC898214XD's
signed +324..-257 code space; the mapping between them is not established.

Related: [[taimen-libcamera-drifted-not-pinned]].
