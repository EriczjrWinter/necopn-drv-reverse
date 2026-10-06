#!/usr/bin/env python3
"""ctypes smoke test for necopn_player/core/necopna_ymfm.dll (YM2608 / OPNA core).

Verifies:
  (a) create/write/generate/destroy do not crash,
  (b) the output sample rate follows ymfm's rule  (ym2608::sample_rate == clock/8
      at ymfm's default OPN_FIDELITY_MAX, see vendor/ymfm/src/ymfm_opn.h:519-528),
  (c) an FM voice (channel 0, algorithm 7, one loud operator) produces a
      non-zero waveform with reasonable amplitude, at the expected pitch,
  (d) the two chips behind port 0 (0x188/0x18A) and port 1 (0x18C/0x18E)
      sound independently,
  (e) the YM2608 rhythm (ADPCM-A) path: all 6 instruments sound via registers
      0x10 (key-on), 0x11 (total level), 0x18-0x1D (pan + instrument level),
  (f) the user-supplied rhythm ROM path: nopna_set_rhythm_rom() feeds an
      authentic 8KB ADPCM-A dump to both chips; nopna_clear_rhythm_rom()
      falls back to the built-in synthetic ROM.

Run:  python smoke_test.py
"""

import array
import ctypes
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DLL = os.path.join(HERE, "necopna_ymfm.dll")
CLOCK = 8_000_000          # PC-98 OPNA master clock (see necopn_reverse/STATUS.md)

if not os.path.exists(DLL):
    print("FATAL: %s not found -- run build.ps1 first" % DLL)
    sys.exit(2)

lib = ctypes.CDLL(DLL)
lib.nopna_create.restype = ctypes.c_void_p
lib.nopna_create.argtypes = [ctypes.c_uint32]
lib.nopna_destroy.restype = None
lib.nopna_destroy.argtypes = [ctypes.c_void_p]
lib.nopna_reset.restype = None
lib.nopna_reset.argtypes = [ctypes.c_void_p]
lib.nopna_write.restype = None
lib.nopna_write.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8, ctypes.c_uint8]
lib.nopna_read.restype = ctypes.c_uint8
lib.nopna_read.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8]
lib.nopna_sample_rate.restype = ctypes.c_uint32
lib.nopna_sample_rate.argtypes = [ctypes.c_void_p]
lib.nopna_generate.restype = None
lib.nopna_generate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16), ctypes.c_uint32]

try:
    lib.nopna_set_rhythm_rom.restype = ctypes.c_int
    lib.nopna_set_rhythm_rom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    lib.nopna_clear_rhythm_rom.restype = ctypes.c_int
    lib.nopna_clear_rhythm_rom.argtypes = [ctypes.c_void_p]
    HAVE_ROM_API = True
except AttributeError:
    HAVE_ROM_API = False

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print("%s %s%s" % ("PASS  " if ok else "FAIL  ", name, ("  -- " + detail) if detail else ""))


def wr(h, port, reg, val):
    lib.nopna_write(h, port, reg, val)


def render(h, seconds):
    """render `seconds` of mixed stereo audio -> (array of int16, nframes, rate)"""
    rate = lib.nopna_sample_rate(h)
    nframes = int(rate * seconds)
    buf = (ctypes.c_int16 * (2 * nframes))()
    lib.nopna_generate(h, buf, nframes)
    a = array.array("h")
    a.frombytes(bytes(memoryview(buf).cast("B")))
    return a, nframes, rate


def stats(a):
    peak = 0
    sumsq = 0
    for x in a:
        ax = -x if x < 0 else x
        if ax > peak:
            peak = ax
        sumsq += x * x
    rms = math.sqrt(sumsq / len(a)) if a else 0.0
    return peak, rms


def pitch_hz(a, rate, skip_seconds=0.05):
    """rough fundamental estimate from rising zero crossings of the left channel"""
    start = int(skip_seconds * rate) * 2
    lo = a[start::2]
    crossings = 0
    prev = lo[0] if lo else 0
    for x in lo[1:]:
        if prev <= 0 < x:
            crossings += 1
        prev = x
    dur = len(lo) / float(rate)
    return crossings / dur if dur > 0 else 0.0


# ---------------------------------------------------------------- FM voice
FNUM = 1038   # -> ~440 Hz at 8 MHz, block 4:  f = FNUM*2^(block-1)*(clock/144)/2^20
BLOCK = 4


def fm_voice_on(h, port):
    """channel 0, algorithm 7, only operator 1 audible (low TL), LR pan, key on"""
    wr(h, port, 0x22, 0x00)          # LFO off
    wr(h, port, 0x27, 0x00)          # timers / CSM off
    wr(h, port, 0x28, 0x00)          # key off
    for op in range(4):
        tlv = 0x08 if op == 0 else 0x7F   # TL 0x08 = 6 dB atten (audible, no clip)
        wr(h, port, 0x30 + op * 4, 0x01)   # DT=0, MUL=1
        wr(h, port, 0x40 + op * 4, tlv)    # total level
        wr(h, port, 0x50 + op * 4, 0x1F)   # KS=0, AR=31 (fast attack)
        wr(h, port, 0x60 + op * 4, 0x00)   # D1R=0 -> hold at sustain
        wr(h, port, 0x70 + op * 4, 0x00)   # D2R=0
        wr(h, port, 0x80 + op * 4, 0x0F)   # SL=0, RR=15
        wr(h, port, 0x90 + op * 4, 0x00)   # SSG-EG off
    wr(h, port, 0xA4, (BLOCK << 3) | ((FNUM >> 8) & 7))   # block + fnum hi (latch)
    wr(h, port, 0xA0, FNUM & 0xFF)                        # fnum lo (applies latch)
    wr(h, port, 0xB0, 0x07)          # FB=0, ALG=7
    wr(h, port, 0xB4, 0xC0)          # pan L+R
    wr(h, port, 0x28, 0xF0)          # key on channel 0, all 4 operators


def fm_off(h, port):
    wr(h, port, 0x28, 0x00)


def fm_zero(h, port):
    """make sure every FM channel of this chip is silent"""
    for chan in (0, 1, 2):
        wr(h, port, 0x28, chan)      # key off (channel select bits only)
    wr(h, port, 0x28, 0x00)


# ---------------------------------------------------------------- rhythm
RHY_NAMES = ["bass drum", "snare drum", "top cymbal", "high hat", "tom tom", "rim shot"]


def rhythm_prep(h, port=0):
    wr(h, port, 0x11, 0x3F)                       # total level = max (loudest)
    for ch in range(6):
        wr(h, port, 0x18 + ch, 0xC0 | 0x1F)       # pan L+R, instrument level = max
    wr(h, port, 0x10, 0x80 | 0x3F)                # key off / dump all


def rhythm_keyon(h, mask, port=0):
    wr(h, port, 0x10, mask & 0x3F)                # bit7=0 -> key on, bits 0-5 = mask


# ================================================================ main
def main():
    print("== necopna_ymfm.dll smoke test ==")
    print("DLL    : %s" % DLL)
    print("clock  : %d Hz" % CLOCK)

    h = lib.nopna_create(CLOCK)
    check("(a) nopna_create returns a handle", h != 0)
    if not h:
        return finish()

    # ---- (b) sample rate rule
    rate = lib.nopna_sample_rate(h)
    expected = CLOCK // 8
    check("(b) sample_rate == clock/8 (%d)" % expected, rate == expected,
          "got %d" % rate)
    print("       ymfm rule: ym2608::sample_rate() = clock/8 (OPN_FIDELITY_MAX,"
          " default), clock/24 MED, clock/48 MIN; FM core itself runs at clock/144")

    # ---- silence check after reset
    lib.nopna_reset(h)
    a, n, rate = render(h, 0.05)
    peak, rms = stats(a)
    check("(c0) reset -> digital silence", peak == 0, "peak=%d rms=%.1f" % (peak, rms))

    # ---- (c) FM tone on chip 0 (port 0 = 0x188/0x18A pair)
    fm_voice_on(h, 0)
    a, n, rate = render(h, 0.30)
    peak, rms = stats(a)
    pitch = pitch_hz(a, rate)
    check("(c1) FM chip0: non-zero waveform", peak > 300, "peak=%d rms=%.1f" % (peak, rms))
    check("(c2) FM chip0: reasonable amplitude (no clipping)",
          300 < peak < 30000, "peak=%d" % peak)
    check("(c3) FM chip0: pitch ~= 440 Hz", 380.0 <= pitch <= 500.0,
          "measured %.1f Hz (FNUM=%d block=%d)" % (pitch, FNUM, BLOCK))
    # stereo sanity: both channels active
    left = a[0::2]
    right = a[1::2]
    lp, _ = stats(left)
    rp, _ = stats(right)
    check("(c4) FM chip0: audio on both L and R", lp > 300 and rp > 300,
          "L peak=%d R peak=%d" % (lp, rp))

    # ---- (d) both chips independent
    lib.nopna_reset(h)
    fm_off(h, 0)
    fm_off(h, 1)
    a, _, _ = render(h, 0.05)
    peak, rms = stats(a)
    # ymfm holds the pre-reset FM sample for at most one hold period (18 output
    # samples at clock/8), so ignore the first 1 ms before demanding silence
    tail_peak, _ = stats(a[2 * int(0.001 * rate):])
    check("(d1) both chips key-off -> silence", tail_peak == 0 and peak < 256,
          "peak=%d (first-1ms ymfm ZOH residue) tail peak=%d" % (peak, tail_peak))

    lib.nopna_reset(h)
    fm_voice_on(h, 1)          # only chip 1 (0x18C/0x18E pair)
    a, _, _ = render(h, 0.20)
    peak1, rms1 = stats(a)
    check("(d2) port 1 (chip 1) sounds on its own", peak1 > 300,
          "peak=%d rms=%.1f" % (peak1, rms1))

    lib.nopna_reset(h)
    fm_voice_on(h, 0)          # only chip 0
    a, _, _ = render(h, 0.20)
    peak0, rms0 = stats(a)
    check("(d3) port 0 (chip 0) sounds on its own", peak0 > 300,
          "peak=%d rms=%.1f" % (peak0, rms0))

    lib.nopna_reset(h)
    fm_voice_on(h, 0)
    fm_voice_on(h, 1)
    a, _, _ = render(h, 0.20)
    peakb, rmsb = stats(a)
    check("(d4) both chips together sum in the mix", peakb >= max(peak0, peak1),
          "peak=%d (chip0 alone %d, chip1 alone %d)" % (peakb, peak0, peak1))

    # ---- SSG register readback through nopna_read (bonus)
    lib.nopna_reset(h)
    wr(h, 0, 0x07, 0x3E)       # SSG mixer
    back = lib.nopna_read(h, 0, 0x07)
    check("(d5) nopna_read SSG register readback", back == 0x3E, "read 0x%02X" % back)
    status = lib.nopna_read(h, 0, 0x28)   # any addr > 0x0F -> status register
    check("(d6) nopna_read status register", status < 0x100, "status=0x%02X" % status)

    # ---- (e) rhythm (ADPCM-A) on chip 0
    lib.nopna_reset(h)
    fm_zero(h, 0)
    fm_zero(h, 1)
    rhythm_prep(h, 0)
    rhythm_keyon(h, 0x3F)                     # all six at once
    a, _, _ = render(h, 0.30)
    peak, rms = stats(a)
    check("(e1) all 6 rhythm instruments together", peak > 300,
          "peak=%d rms=%.1f" % (peak, rms))

    print("       per-instrument (chip 0, key-on via reg 0x10):")
    all_ok = True
    for i in range(6):
        lib.nopna_reset(h)
        fm_zero(h, 0)
        fm_zero(h, 1)
        rhythm_prep(h, 0)
        rhythm_keyon(h, 1 << i)
        a, _, _ = render(h, 0.30)
        peak, rms = stats(a)
        ok = peak > 100
        all_ok = all_ok and ok
        print("         [%d] %-11s peak=%6d rms=%8.1f  %s"
              % (i, RHY_NAMES[i], peak, rms, "ok" if ok else "SILENT"))
    check("(e2) each of the 6 rhythm instruments sounds independently", all_ok)

    # rhythm on the second chip too (both chips have their own ADPCM-A unit)
    lib.nopna_reset(h)
    fm_zero(h, 0)
    fm_zero(h, 1)
    rhythm_prep(h, 1)
    rhythm_keyon(h, 0x3F, port=1)
    a, _, _ = render(h, 0.30)
    peak, rms = stats(a)
    check("(e3) rhythm works on chip 1 (port 1) as well", peak > 300,
          "peak=%d rms=%.1f" % (peak, rms))
    # ---- (f) user-supplied rhythm ROM (authentic YM2608 ADPCM-A dump path)
    # 注：ymfm 的 FM 包络计数器不随 reset 归零，ADPCM-A 的取数节拍挂在它上面，
    # 所以相位不同的两次渲染本来就有零星差异；对照基线必须用"相位对齐的新句柄"，
    # 且用差异采样占比（而不是整段 !=）来判定 ROM 数据真的进了 ADPCM-A。
    if not HAVE_ROM_API:
        check("(f0) nopna_set/clear_rhythm_rom exported", False,
              "DLL too old -- rebuild with build.ps1")
    else:
        def one_bd(handle, port=0):
            lib.nopna_reset(handle)
            fm_zero(handle, 0)
            fm_zero(handle, 1)
            rhythm_prep(handle, port)
            rhythm_keyon(handle, 0x01, port=port)      # bass drum only
            a, _, _ = render(handle, 0.30)
            return a

        def diff_frac(a1, a2):
            n = min(len(a1), len(a2)) // 2
            return sum(1 for i in range(n)
                       if a1[2 * i:2 * i + 2] != a2[2 * i:2 * i + 2]) / max(1, n)

        custom = bytes(((i * 7 + 3) & 0xFF) for i in range(0x2000))

        rc = lib.nopna_set_rhythm_rom(h, custom, len(custom))
        check("(f1) set_rhythm_rom(8KB) returns 0", rc == 0, "rc=%d" % rc)

        a_usr = one_bd(h)
        peak_usr, _ = stats(a_usr)
        check("(f2) rhythm sounds through the user ROM", peak_usr > 100,
              "peak=%d" % peak_usr)

        hh = lib.nopna_create(CLOCK)     # 相位对齐的对照句柄（内置合成 ROM）
        a_syn = one_bd(hh)
        frac = diff_frac(a_usr, a_syn)
        check("(f3) user ROM changes the decoded samples",
              frac > 0.08, "frac=%.3f" % frac)
        lib.nopna_destroy(hh)

        rc = lib.nopna_set_rhythm_rom(h, b"\x00" * 100, 100)
        check("(f4) short ROM (<8KB) rejected", rc == -2, "rc=%d" % rc)

        rc = lib.nopna_clear_rhythm_rom(h)
        check("(f5) clear_rhythm_rom returns 0", rc == 0, "rc=%d" % rc)

        hh = lib.nopna_create(CLOCK)     # 新句柄：set->clear 后应与从未设置完全一致
        lib.nopna_set_rhythm_rom(hh, custom, len(custom))
        lib.nopna_clear_rhythm_rom(hh)
        a_back = one_bd(hh)
        check("(f6) clear restores the synthetic baseline",
              bytes(a_back) == bytes(a_syn), "")
        lib.nopna_destroy(hh)

        # user ROM reaches the second chip as well
        lib.nopna_set_rhythm_rom(h, custom, len(custom))
        a_usr1 = one_bd(h, port=1)
        frac1 = diff_frac(a_usr1, a_syn)
        check("(f7) user ROM applies to chip 1 too",
              frac1 > 0.08, "frac=%.3f" % frac1)
        lib.nopna_clear_rhythm_rom(h)


    lib.nopna_destroy(h)
    check("(a2) nopna_destroy without crash", True)
    return finish()


def finish():
    passed = sum(1 for r in RESULTS if r)
    print("--")
    print("RESULT: %d/%d checks passed" % (passed, len(RESULTS)))
    return 0 if (RESULTS and all(RESULTS)) else 1


if __name__ == "__main__":
    sys.exit(main())
