# -*- coding: utf-8 -*-
r"""OPN_Chipset 里几个 OPNA 核心的吞吐对比（含 Nuked YM2608 LLE）。

    python tool/bench_chipset.py --dll D:\gf\OPN_Chipset-main\build\libopnemuchipset.dll
r"""
from __future__ import annotations

import argparse
import ctypes
import time


EMU = {
    0: "MAME YM2612",
    1: "Nuked YM3438",
    2: "GENS",
    3: "YMFM OPN2",
    4: "NP2 OPNA",
    5: "MAME YM2608",
    6: "YMFM OPNA",
    9: "Nuked YM2612 LLE",
    10: "Nuked YM2608 LLE",
    11: "Nuked YM3438 LLE",
    12: "Nuked YMF276 LLE",
}


def bind(path):
    d = ctypes.CDLL(path)
    d.opn_chipset_init.restype = ctypes.c_void_p
    d.opn_chipset_init.argtypes = [ctypes.c_int, ctypes.c_int]
    d.opn_chipset_free.argtypes = [ctypes.c_void_p]
    d.opn_chipset_setOutputSampleRate.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    d.opn_chipset_writeReg.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                       ctypes.c_uint16, ctypes.c_uint8]
    d.opn_chipset_generate.argtypes = [ctypes.c_void_p,
                                       ctypes.POINTER(ctypes.c_int16),
                                       ctypes.c_size_t]
    d.opn_chipset_nativeGenerate.argtypes = [ctypes.c_void_p,
                                             ctypes.POINTER(ctypes.c_int16)]
    d.opn_chipset_nativeRate.restype = ctypes.c_uint32
    d.opn_chipset_nativeRate.argtypes = [ctypes.c_void_p]
    d.opn_chipset_effectiveRate.restype = ctypes.c_uint32
    d.opn_chipset_effectiveRate.argtypes = [ctypes.c_void_p]
    d.opn_chipset_clockRate.restype = ctypes.c_uint32
    d.opn_chipset_clockRate.argtypes = [ctypes.c_void_p]
    d.opn_chipset_nativeClockRate.restype = ctypes.c_uint32
    d.opn_chipset_nativeClockRate.argtypes = [ctypes.c_void_p]
    d.opn_chipset_canRunAtPcmRate.restype = ctypes.c_int
    d.opn_chipset_canRunAtPcmRate.argtypes = [ctypes.c_void_p]
    return d


def voice(d, h, ch):
    """3 个 FM 声部（OPNA ch0-2，bank 0）。"""
    w = lambda reg, val: d.opn_chipset_writeReg(h, 0, reg, val)
    w(0x29, 0x80)                      # OPNA 模式
    w(0xB0 + ch, 0x00)
    for i, off in enumerate((0x00, 0x08, 0x04, 0x0C)):
        s = off + ch
        w(0x30 + s, 0x01)
        w(0x40 + s, 0x20 if i < 3 else 0x00)
        w(0x50 + s, 0x1F)
        w(0x60 + s, 0x05)
        w(0x70 + s, 0x02)
        w(0x80 + s, 0x0A)
    w(0xA4 + ch, 0x2A)
    w(0xA0 + ch, 0x80)
    w(0x28, 0xF0 | ch)


def bench(d, emu, family, seconds=3.0, repeat=2, native=False):
    h = d.opn_chipset_init(emu, family)
    if not h:
        return None
    d.opn_chipset_setOutputSampleRate(h, 48_000)
    for ch in range(3):
        voice(d, h, ch)
    sr = 48_000
    n = int(seconds * sr)
    block = 512
    buf = (ctypes.c_int16 * (2 * block))()
    times = []
    for _ in range(repeat):
        done = 0
        t0 = time.perf_counter()
        if native:
            nb = (ctypes.c_int16 * 2)()
            for _ in range(n):
                d.opn_chipset_nativeGenerate(h, nb)
        else:
            while done < n:
                k = min(block, n - done)
                d.opn_chipset_generate(h, buf, k)
                done += k
        times.append(time.perf_counter() - t0)
    info = {
        "native": d.opn_chipset_nativeRate(h),
        "clock": d.opn_chipset_clockRate(h),
        "natclock": d.opn_chipset_nativeClockRate(h),
        "effective": d.opn_chipset_effectiveRate(h),
        "can_pcm": d.opn_chipset_canRunAtPcmRate(h),
    }
    d.opn_chipset_free(h)
    best = min(times)
    return best / seconds * 1000, seconds / best, info


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dll", default=r"D:\gf\OPN_Chipset-main\build\libopnemuchipset.dll")
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--repeat", type=int, default=2)
    args = ap.parse_args(argv)
    d = bind(args.dll)
    print(f"{'核心':<22}{'ms/音频秒':>10}{'x实时':>8}   native/clock/effective  canPCM")
    for emu, name in EMU.items():
        if emu not in (4, 5, 6, 10):
            continue
        try:
            r = bench(d, emu, 1, args.seconds, args.repeat)
        except Exception as e:
            print(f"{name:<22} 失败 {type(e).__name__}: {e}")
            continue
        if not r:
            print(f"{name:<22} 初始化失败")
            continue
        ms, x, info = r
        print(f"{name:<22}{ms:>10.1f}{x:>8.2f}   "
              f"{info['native']}/{info['clock']}/{info['effective']}  {info['can_pcm']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
