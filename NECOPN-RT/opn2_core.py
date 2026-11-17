# -*- coding: utf-8 -*-
"""Nuked-OPN2 (YM3438) 核心的 Python 绑定 + 双芯片混音。

YM2608 的 FM 部分与 YM3438/OPN2 基本一致（6 通道、4 算子、LFO、SSG-EG），
所以这里用 Nuked-OPN2 作为 OPNA 的 FM LLE 核心：
  - FM      : 本文件（Nuked ym3438.c，LGPL-2.1）
  - SSG     : ssg_core.py（emu2149，MIT）
  - 节奏/ADPCM: rhythm_core.py（用户自备 RHY 采样，不随程序分发）

一个 `NukedOpn2` 实例 = 一颗 YM2608 的 FM 部分。NECOPN.DRV 用两颗芯片
（每颗只用 ch0-2），所以 Session 里创建两个实例，由 `CoreMixer` 相加。
"""
from __future__ import annotations

import ctypes
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DLL = os.path.join(HERE, "opn2.dll")

CLOCK_DEFAULT = 8_000_000
MAX_CHUNK = 8192


class NukedOpn2:
    """一颗 YM2608 的 FM 部分（Nuked OPN2 / YM3438 LLE）。"""

    name = "nuked-opn2"
    _dll = None
    _dll_path = None

    @classmethod
    def load(cls, path: str = DEFAULT_DLL) -> ctypes.CDLL:
        if cls._dll is None or cls._dll_path != os.path.abspath(path):
            if not os.path.exists(path):
                raise FileNotFoundError(f"找不到 OPN2 核心 DLL: {path}")
            d = ctypes.CDLL(path)
            d.nopn2_create.restype = ctypes.c_void_p
            d.nopn2_create.argtypes = [ctypes.c_uint32]
            d.nopn2_destroy.argtypes = [ctypes.c_void_p]
            d.nopn2_reset.argtypes = [ctypes.c_void_p]
            d.nopn2_write.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                      ctypes.c_uint8]
            d.nopn2_read.restype = ctypes.c_uint8
            d.nopn2_read.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            d.nopn2_sample_rate.restype = ctypes.c_uint32
            d.nopn2_sample_rate.argtypes = [ctypes.c_void_p]
            d.nopn2_pending.restype = ctypes.c_uint32
            d.nopn2_pending.argtypes = [ctypes.c_void_p]
            d.nopn2_overflow.restype = ctypes.c_uint32
            d.nopn2_overflow.argtypes = [ctypes.c_void_p]
            d.nopn2_generate.argtypes = [ctypes.c_void_p,
                                         ctypes.POINTER(ctypes.c_int16),
                                         ctypes.c_uint32]
            cls._dll = d
            cls._dll_path = os.path.abspath(path)
        return cls._dll

    def __init__(self, clock: int = CLOCK_DEFAULT, dll_path: str = DEFAULT_DLL,
                 rhythm=None, ssg=None):
        self.clock = clock
        self._dll = self.load(dll_path)
        self._h = self._dll.nopn2_create(clock)
        if not self._h:
            raise RuntimeError("nopn2_create 失败")
        self._sr = int(self._dll.nopn2_sample_rate(self._h))
        self.rhythm = rhythm      # 可选：RhythmCore，接 0x10/0x11/0x18-0x1D
        self.ssg = ssg            # 可选：SsgCore，接 0x00-0x0F
        self.write_count = 0
        self._buf = (ctypes.c_int16 * (2 * MAX_CHUNK))()

    # -- 寄存器 --
    def write(self, port: int, reg: int, val: int) -> None:
        """port 忽略（一个实例一颗芯片）；reg 的 bit8 = 第二寄存器区。

        节奏/SSG 寄存器在进 Nuked 之前先分流：Nuked 只做 FM，不认识这些。
        """
        reg &= 0x1FF
        val &= 0xFF
        if reg <= 0x0F and self.ssg is not None:
            self.ssg.write(reg, val)
            return
        if self.rhythm is not None and (reg in (0x10, 0x11) or 0x18 <= reg <= 0x1D):
            self.rhythm.write(reg, val)
            return
        self._dll.nopn2_write(self._h, reg, val)
        self.write_count += 1

    def read(self, port: int, reg: int) -> int:
        return int(self._dll.nopn2_read(self._h, reg & 0x1FF))

    def sample_rate(self) -> int:
        return self._sr

    def generate_into(self, nframes: int, buf) -> None:
        self._dll.nopn2_generate(self._h, buf, nframes)

    def generate(self, nframes: int) -> bytes:
        self.generate_into(nframes, self._buf)
        return bytes(self._buf)[:4 * nframes]

    # -- 状态 --
    def pending(self) -> int:
        return int(self._dll.nopn2_pending(self._h))

    def overflow(self) -> int:
        return int(self._dll.nopn2_overflow(self._h))

    def reset(self) -> None:
        self._dll.nopn2_reset(self._h)

    def close(self) -> None:
        if self._h:
            self._dll.nopn2_destroy(self._h)
            self._h = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class CoreMixer:
    """把 N 颗芯片的立体声输出相加。

    NECOPN 用 2 颗，每颗只开 ch0-2：
      - Nuked 组合：3+3 个声部满幅约 ±24500，直接相加（divide=False）
      - fmgen：与离线版 `mix_cores` 一致，相加后 /N（divide=True）
    """

    name = "core-mixer"

    def __init__(self, cores, divide: bool = False, gain: float = 1.0):
        self.cores = list(cores)
        if not self.cores:
            raise ValueError("CoreMixer 至少需要一颗芯片")
        self.divide = bool(divide)
        self.gain = float(gain)
        self._sr = self.cores[0].sample_rate()
        self._tmp = (ctypes.c_int16 * (2 * MAX_CHUNK))()
        self._acc = np.zeros((MAX_CHUNK, 2), np.int32)
        self.clipped = 0

    def sample_rate(self) -> int:
        return self._sr

    def generate_into(self, nframes: int, buf) -> None:
        cores = self.cores
        if len(cores) == 1:
            cores[0].generate_into(nframes, buf)
            if self.gain != 1.0:
                out = np.ctypeslib.as_array(buf)[:2 * nframes].reshape(nframes, 2)
                out[:] = np.clip(out.astype(np.float32) * self.gain,
                                 -32768, 32767).astype(np.int16)
            return

        acc = self._acc[:nframes]
        out = np.ctypeslib.as_array(buf)[:2 * nframes].reshape(nframes, 2)
        cores[0].generate_into(nframes, buf)
        acc[:] = out
        for c in cores[1:]:
            c.generate_into(nframes, self._tmp)
            acc += np.ctypeslib.as_array(self._tmp)[:2 * nframes].reshape(nframes, 2)
        if self.divide:
            # 和离线版 _sdiv 一致：向零截断（不是 Python 的向下取整）
            n = len(cores)
            acc = np.where(acc >= 0, acc // n, -((-acc) // n))
        if self.gain != 1.0:
            acc = (acc.astype(np.float32) * self.gain).astype(np.int32)
        over = int(np.count_nonzero((acc > 32767) | (acc < -32768)))
        if over:
            self.clipped += over
            np.clip(acc, -32768, 32767, out=acc)
        out[:] = acc.astype(np.int16)

    def reset(self) -> None:
        for c in self.cores:
            c.reset()

    def close(self) -> None:
        for c in self.cores:
            try:
                c.close()
            except Exception:
                pass

    def overflow(self) -> int:
        return sum(getattr(c, "overflow", lambda: 0)() for c in self.cores)


class SsgSink:
    """把 SSG 寄存器写转给一颗完整 OPNA 核心（fmgen 的 SSG 是芯片内置的）。

    Nuked 组合路径用独立的 emu2149；fmgen 路径用这个薄适配器，
    保证驱动里的 SSG 兜底鼓配方（R7/R11-13/Tone/Noise）仍然可用。
    """

    name = "ssg-sink"

    def __init__(self, core):
        self.core = core

    def write(self, reg: int, val: int) -> None:
        self.core.write(0, reg & 0x0F, val & 0xFF)

    def close(self) -> None:
        pass
