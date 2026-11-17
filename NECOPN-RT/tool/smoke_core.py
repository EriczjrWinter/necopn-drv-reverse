# -*- coding: utf-8 -*-
"""最小冒烟测试：直接驱动 opn2.dll，不经过项目其它层。

    python tool/smoke_core.py

注意 YM2612/YM3438 的通道寄存器分两个区：
    ch1-3: 0x30.. / 0xA0.. / 0xB0.. / 0xB4..
    ch4-6: 0x130.. / 0x1A0.. / 0x1B0.. / 0x1B4..（地址 bit8 = 1）
写错区的话 Nuked 会静默丢掉（它按 cycle 的时分复用来匹配通道）。
"""
from __future__ import annotations

import ctypes
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DLL = os.path.join(HERE, "opn2.dll")


def bind():
    d = ctypes.CDLL(DLL)
    d.nopn2_create.restype = ctypes.c_void_p
    d.nopn2_create.argtypes = [ctypes.c_uint32]
    d.nopn2_destroy.argtypes = [ctypes.c_void_p]
    d.nopn2_reset.argtypes = [ctypes.c_void_p]
    d.nopn2_write.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8]
    d.nopn2_sample_rate.restype = ctypes.c_uint32
    d.nopn2_sample_rate.argtypes = [ctypes.c_void_p]
    d.nopn2_generate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16),
                                 ctypes.c_uint32]
    d.nopn2_pending.restype = ctypes.c_uint32
    d.nopn2_pending.argtypes = [ctypes.c_void_p]
    d.nopn2_overflow.restype = ctypes.c_uint32
    d.nopn2_overflow.argtypes = [ctypes.c_void_p]
    return d


def voice(d, h, ch, fnum_lo=0x80, block=5, fnum_hi=0x2A):
    """一个能出声的 4-op 音色（alg 0 = 串联，最后一个算子做载波）。"""
    base, c = (0x100, ch - 3) if ch >= 3 else (0x000, ch)
    w = lambda reg, val: d.nopn2_write(h, reg, val)
    w(0xB0 + base + c, 0x00)                  # alg=0, fb=0
    for i, off in enumerate((0x00, 0x08, 0x04, 0x0C)):
        s = base + off + c
        w(0x30 + s, 0x01)                     # DT=0 MUL=1
        w(0x40 + s, 0x20 if i < 3 else 0x00)  # TL：前三个算子衰减
        w(0x50 + s, 0x1F)                     # AR=31
        w(0x60 + s, 0x05)                     # D1R
        w(0x70 + s, 0x02)                     # D2R
        w(0x80 + s, 0x0A)                     # RR=0 SL=10
    w(0xA4 + base + c, fnum_hi)               # block/fnum 高
    w(0xA0 + base + c, fnum_lo)               # fnum 低


def peak(d, h, n=20000):
    buf = (ctypes.c_int16 * (2 * n))()
    d.nopn2_generate(h, buf, n)
    return max(abs(v) for v in buf), buf


def main() -> int:
    d = bind()
    h = d.nopn2_create(8_000_000)
    assert h, "nopn2_create 失败"
    print(f"sample_rate = {d.nopn2_sample_rate(h)}")

    buf = (ctypes.c_int16 * (2 * 4096))()
    d.nopn2_generate(h, buf, 4096)
    silent_peak = max(abs(v) for v in buf)
    print(f"静音 peak = {silent_peak}")

    for ch in range(3):
        voice(d, h, ch)
        d.nopn2_write(h, 0x28, 0xF0 | (ch if ch < 3 else ch + 1))
    print(f"pending = {d.nopn2_pending(h)}")
    peak1, _ = peak(d, h)
    print(f"三个音 peak = {peak1}  pending = {d.nopn2_pending(h)}"
          f"  overflow = {d.nopn2_overflow(h)}")

    # 每颗通道单独出声：验证 6 通道都通
    solos = []
    for ch in range(6):
        d.nopn2_reset(h)
        voice(d, h, ch)
        d.nopn2_write(h, 0x28, 0xF0 | (ch if ch < 3 else ch + 1))
        solos.append(peak(d, h)[0])
    print(f"单通道 peak（ch0..ch5）= {solos}")

    # 6 通道同音低音：验证时分复用求和
    d.nopn2_reset(h)
    for ch in range(6):
        voice(d, h, ch, fnum_lo=0x40, block=0, fnum_hi=0x01)
        d.nopn2_write(h, 0x28, 0xF0 | (ch if ch < 3 else ch + 1))
    six, _ = peak(d, h, 40000)
    print(f"6 通道同音 peak = {six}  预期 ≈ {solos[0] * 6}")

    d.nopn2_destroy(h)
    ok = silent_peak == 0 and peak1 > 0 and all(s > 0 for s in solos)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
