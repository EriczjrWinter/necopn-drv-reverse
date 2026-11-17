# -*- coding: utf-8 -*-
"""核心吞吐基准：Nuked OPN2（本项目） vs Nuked OPM（OPMMIDI-RT，对照组）。

只测 `generate` 本身，不含 MIDI 驱动 / 混音 / 重采样，用来回答
"Nuked OPNA/OPN2 的 ms/音频秒，实时余量够不够"。

    python tool/bench_core.py
    python tool/bench_core.py --seconds 60 --channels 3
"""
from __future__ import annotations

import argparse
import ctypes
import os
import statistics
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPN2_DLL = os.path.join(HERE, "opn2.dll")
OPM_DLL = os.path.join(HERE, "..", "OPMMIDI-RT", "opm.dll")


def bind_opn2():
    d = ctypes.CDLL(OPN2_DLL)
    d.nopn2_create.restype = ctypes.c_void_p
    d.nopn2_create.argtypes = [ctypes.c_uint32]
    d.nopn2_destroy.argtypes = [ctypes.c_void_p]
    d.nopn2_write.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8]
    d.nopn2_sample_rate.restype = ctypes.c_uint32
    d.nopn2_sample_rate.argtypes = [ctypes.c_void_p]
    d.nopn2_generate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16),
                                 ctypes.c_uint32]
    return d


def bind_opm():
    if not os.path.exists(OPM_DLL):
        return None
    d = ctypes.CDLL(OPM_DLL)
    d.opm_create.restype = ctypes.c_void_p
    d.opm_create.argtypes = [ctypes.c_uint32]
    d.opm_destroy.argtypes = [ctypes.c_void_p]
    d.opm_write.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8]
    d.opm_generate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16),
                               ctypes.c_uint32]
    return d


def setup_opn2_voice(d, h, ch):
    base, c = (0x100, ch - 3) if ch >= 3 else (0x000, ch)
    w = lambda reg, val: d.nopn2_write(h, reg, val)
    w(0xB0 + base + c, 0x00)
    for i, off in enumerate((0x00, 0x08, 0x04, 0x0C)):
        s = base + off + c
        w(0x30 + s, 0x01)
        w(0x40 + s, 0x20 if i < 3 else 0x00)
        w(0x50 + s, 0x1F)
        w(0x60 + s, 0x05)
        w(0x70 + s, 0x02)
        w(0x80 + s, 0x0A)
    w(0xA4 + base + c, 0x2A)
    w(0xA0 + base + c, 0x80)
    w(0x28, 0xF0 | (ch if ch < 3 else ch + 1))


def setup_opm_voice(d, h, ch):
    """OPM 的 4 个算子 + 通道寄存器（ALG 0，最后一个算子做载波）。"""
    w = lambda reg, val: d.opm_write(h, 0, reg & 0xFF)
    w(0x20 + ch, 0x00)          # ALG/FB
    for i, off in enumerate((0x00, 0x08, 0x10, 0x18)):
        w(0x40 + off + ch, 0x20 if i < 3 else 0x00)  # DT1/MUL
        w(0x60 + off + ch, 0x1F)                     # TL
        w(0x80 + off + ch, 0x1F)                     # KS/AR
        w(0xA0 + off + ch, 0x05)                     # AME/D1R
        w(0xC0 + off + ch, 0x02)                     # DT2/D2R
        w(0xE0 + off + ch, 0x0A)                     # SL/RR
    w(0x28 + ch, 0x2A)          # KC
    w(0x30 + ch, 0x80)          # KF
    w(0x08, 0x78 | ch)          # KEY ON


def bench_opn2(seconds, chips, block, repeat=5):
    d = bind_opn2()
    h = [(d.nopn2_create(8_000_000), d) for _ in range(chips)]
    for hi, _ in h:
        for ch in range(3):
            setup_opn2_voice(d, hi, ch)
    sr = 55555
    n = int(seconds * sr)
    buf = (ctypes.c_int16 * (2 * block))()
    times = []
    for _ in range(repeat):
        done = 0
        t0 = time.perf_counter()
        while done < n:
            k = min(block, n - done)
            for hi, _ in h:
                d.nopn2_generate(hi, buf, k)
            done += k
        times.append(time.perf_counter() - t0)
    for hi, _ in h:
        d.nopn2_destroy(hi)
    best = min(times)
    ms = best / seconds * 1000
    return ms, seconds / best


def bench_opm(seconds, chips, block, repeat=5):
    d = bind_opm()
    if d is None:
        return None
    h = [d.opm_create(0) for _ in range(chips)]
    for hi in h:
        for ch in range(3):
            setup_opm_voice(d, hi, ch)
    sr = 55930
    n = int(seconds * sr)
    buf = (ctypes.c_int16 * (2 * block))()
    times = []
    for _ in range(repeat):
        done = 0
        t0 = time.perf_counter()
        while done < n:
            k = min(block, n - done)
            for hi in h:
                d.opm_generate(hi, buf, k)
            done += k
        times.append(time.perf_counter() - t0)
    for hi in h:
        d.opm_destroy(hi)
    best = min(times)
    return best / seconds * 1000, seconds / best


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--channels", type=int, default=3, help="每颗芯片点亮的通道数")
    ap.add_argument("--repeat", type=int, default=5)
    args = ap.parse_args(argv)

    print(f"本机 Python {os.sys.version.split()[0]}  block=512  "
          f"每颗芯片 {args.channels} 个音")
    print(f"{'核心':<28}{'ms/音频秒':>12}{'x 实时':>10}")
    print("-" * 50)

    for chips in (1, 2):
        ms, x = bench_opn2(args.seconds, chips, 512, args.repeat)
        label = f"Nuked OPN2 ×{chips}"
        print(f"{label:<28}{ms:>12.1f}{x:>10.2f}")

    for chips in (1, 2):
        r = bench_opm(args.seconds, chips, 512, args.repeat)
        if r:
            ms, x = r
            print(f"{'Nuked OPM ×' + str(chips):<28}{ms:>12.1f}{x:>10.2f}")

    # 块大小的影响（每块 ctypes 调用 + 循环开销）
    print("\n块大小敏感性（Nuked OPN2 ×2）：")
    for block in (64, 128, 256, 512, 1024, 4096):
        ms, x = bench_opn2(max(5.0, args.seconds / 4), 2, block, 3)
        print(f"  block={block:<5} {ms:8.1f} ms/s  {x:5.2f}x")

    # 和音频回调 256 帧对齐的"每回调成本"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
