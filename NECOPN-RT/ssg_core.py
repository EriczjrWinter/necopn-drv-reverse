# -*- coding: utf-8 -*-
"""YM2608 内置 SSG（YM2149 兼容）封装。

OPNA 的 SSG 时钟 = 主时钟 / 4（8 MHz → 2 MHz），和 YM2149 的 1.789772 MHz
不是同一个数。emu2149 的 PSG_new(clock, rate) 会按 clock/(8*rate) 做内部
相位步进，所以只要传 (2 MHz, 55 555) 就能和 Nuked FM 的 55 555 Hz 对齐，
不需要额外重采样。

单通道满幅 0xFF<<4 = 4080，三通道合计约 12240（int16 的 37%）。
"""
from __future__ import annotations

import ctypes
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DLL = os.path.join(HERE, "ssg.dll")


class SsgCore:
    name = "emu2149"

    def __init__(self, clock: int = 2_000_000, rate: int = 55_555,
                 dll_path: str = DEFAULT_DLL, channels_on: int = 0x3F):
        self.clock = clock
        self.rate = rate
        self.dll = ctypes.CDLL(dll_path)
        self.dll.ssg_create_ex.restype = ctypes.c_void_p
        self.dll.ssg_create_ex.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        self.dll.ssg_create.restype = ctypes.c_void_p
        self.dll.ssg_create.argtypes = []
        self.dll.ssg_write.argtypes = [ctypes.c_void_p, ctypes.c_uint8,
                                       ctypes.c_uint8]
        self.dll.ssg_generate.argtypes = [ctypes.c_void_p,
                                          ctypes.POINTER(ctypes.c_int16),
                                          ctypes.c_int]
        self.dll.ssg_destroy.argtypes = [ctypes.c_void_p]

        self.handle = self.dll.ssg_create_ex(clock, rate)
        if not self.handle:
            raise RuntimeError("ssg_create_ex 失败")
        self.write(0x07, channels_on)      # 三通道全开

    def write(self, reg: int, val: int) -> None:
        self.dll.ssg_write(self.handle, reg & 0xFF, val & 0xFF)

    def generate_into(self, nframes: int, buf) -> None:
        """写进调用方预分配的 ctypes int16 缓冲区（单声道）。"""
        self.dll.ssg_generate(self.handle, buf, nframes)

    def generate(self, nframes: int) -> np.ndarray:
        buf = (ctypes.c_int16 * nframes)()
        self.generate_into(nframes, buf)
        return np.frombuffer(buf, dtype="<i2", count=nframes).astype(np.int16)

    def close(self) -> None:
        if self.handle:
            self.dll.ssg_destroy(self.handle)
            self.handle = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
