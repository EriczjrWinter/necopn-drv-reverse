# -*- coding: utf-8 -*-
"""NECOPN 播放器 —— 模拟核心抽象层。

一个 `ChipCore` = 一颗 YM2608(OPNA)。port=0 对应硬件 0x188/0x18A，
port=1 对应 0x18C/0x18E（NECOPN 真驱动的第二颗芯片）。

实现：
- `YmfmCore`   : 走 necopn_player/core/necopna_ymfm.dll（ymfm OPNA 核心，首选）
- `NullCore`   : 不出声，只记录寄存器写（用于无 DLL 时的 trace / 回归测试）
- `VgmTap`     : 装饰器，把寄存器写录成 VGM（可直接给 foobar2000 的 vgmplay 播）

核心接口刻意保持最小：write / read / sample_rate / generate / reset。
换 Nuked-OPNA、MAME ym2608 等其它核心时只要再写一个 ChipCore 子类。
"""
from __future__ import annotations

import ctypes
import os
import struct
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple, Union

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DLL = os.path.join(HERE, "core", "necopna_ymfm.dll")



def load_rhythm_rom(source: Union[str, bytes, bytearray]) -> bytes:
    """读入并校验原生 YM2608 打击乐 ROM（ADPCM-A，8192 字节）。

    source：ROM 文件路径，或已经读好的 bytes。返回恰好 8192 字节
    （更长的取前 8192，短的抛 ValueError）。
    """
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
        origin = "<bytes>"
    else:
        with open(source, "rb") as f:
            data = f.read()
        origin = str(source)
    if len(data) < 0x2000:
        raise ValueError(f"打击乐 ROM 太小: {origin} = {len(data)} 字节（需要 8192）")
    if len(data) > 0x2000:
        print(f"警告: {origin} 有 {len(data)} 字节，只使用前 8192 字节")
    return data[:0x2000]

class ChipCore(ABC):
    """一颗 OPNA 模拟核心。"""

    name = "abstract"

    @abstractmethod
    def write(self, port: int, reg: int, val: int) -> None:
        """写寄存器。port: 0=0x188/0x18A, 1=0x18C/0x18E。"""

    @abstractmethod
    def read(self, port: int, reg: int) -> int:
        """读状态/寄存器（Timer 标志等）。"""

    @abstractmethod
    def sample_rate(self) -> int:
        """输出采样率（Hz）。"""

    @abstractmethod
    def generate(self, nframes: int) -> bytes:
        """生成 nframes 帧立体声交错 int16，返回 4*nframes 字节。"""

    def reset(self) -> None:  # pragma: no cover - 可选
        pass


class NullCore(ChipCore):
    """寄存器黑洞：只记录写序列，供 trace / VGM / 单元测试使用。"""

    name = "null"

    def __init__(self, clock: int = 8_000_000):
        self.clock = clock
        self.writes: List[Tuple[int, int, int]] = []  # (port, reg, val)

    def write(self, port: int, reg: int, val: int) -> None:
        self.writes.append((port & 1, reg & 0xFF, val & 0xFF))

    def read(self, port: int, reg: int) -> int:
        return 0

    def sample_rate(self) -> int:
        return self.clock // 144

    def generate(self, nframes: int) -> bytes:
        return b"\x00" * (4 * nframes)


class YmfmCore(ChipCore):
    """ymfm OPNA 核心（DLL 封装）。一个 YmfmCore = 一颗 YM2608。"""

    name = "ymfm"

    # C API: extern "C" cdecl
    #   void*    nopna_create(uint32_t clock_hz);
    #   void     nopna_destroy(void* h);
    #   void     nopna_reset(void* h);
    #   void     nopna_write(void* h, uint32_t port, uint8_t addr, uint8_t data);
    #   uint8_t  nopna_read(void* h, uint32_t port, uint8_t addr);
    #   uint32_t nopna_sample_rate(void* h);
    #   void     nopna_generate(void* h, int16_t* out, uint32_t nframes);
    _dll = None
    _dll_lock = None

    @classmethod
    def load(cls, path: str = DEFAULT_DLL) -> ctypes.CDLL:
        if cls._dll is None:
            if not os.path.exists(path):
                raise FileNotFoundError(f"找不到 OPNA 核心 DLL: {path}")
            dll = ctypes.CDLL(path)
            dll.nopna_create.restype = ctypes.c_void_p
            dll.nopna_create.argtypes = [ctypes.c_uint32]
            dll.nopna_destroy.restype = None
            dll.nopna_destroy.argtypes = [ctypes.c_void_p]
            dll.nopna_write.restype = None
            dll.nopna_write.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                        ctypes.c_uint8, ctypes.c_uint8]
            dll.nopna_read.restype = ctypes.c_uint8
            dll.nopna_read.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8]
            dll.nopna_sample_rate.restype = ctypes.c_uint32
            dll.nopna_sample_rate.argtypes = [ctypes.c_void_p]
            dll.nopna_generate.restype = None
            dll.nopna_generate.argtypes = [ctypes.c_void_p,
                                           ctypes.POINTER(ctypes.c_int16), ctypes.c_uint32]
            try:
                dll.nopna_generate_rate.restype = None
                dll.nopna_generate_rate.argtypes = [ctypes.c_void_p,
                                                    ctypes.POINTER(ctypes.c_int16),
                                                    ctypes.c_uint32, ctypes.c_uint32]
            except AttributeError:
                dll.nopna_generate_rate = None
            try:
                dll.nopna_set_rhythm_rom.restype = ctypes.c_int
                dll.nopna_set_rhythm_rom.argtypes = [ctypes.c_void_p,
                                                     ctypes.c_char_p, ctypes.c_uint32]
                dll.nopna_clear_rhythm_rom.restype = ctypes.c_int
                dll.nopna_clear_rhythm_rom.argtypes = [ctypes.c_void_p]
            except AttributeError:
                dll.nopna_set_rhythm_rom = None
                dll.nopna_clear_rhythm_rom = None
            cls._dll = dll
        return cls._dll

    def __init__(self, clock: int = 8_000_000, dll_path: str = DEFAULT_DLL,
                 handle=None, port: int = 0, own: bool = True,
                 out_rate: Optional[int] = None,
                 rhythm_rom: Optional[Union[str, bytes]] = None):
        """own=True 自己创建句柄；否则复用外部句柄（一个句柄可带 2 颗芯片）。

        out_rate：输出采样率。ymfm 原生是 clock/8（8MHz = 1MHz 的 ZOH 过采样），
        默认用 clock/144（FM 引擎速率 = 55555Hz）由 DLL 内加权抽取。
        rhythm_rom：原生 YM2608 打击乐 ROM（8192 字节 dump），路径或 bytes；
        仅 own=True 时生效（共享句柄由创建方安装）。
        """
        self.clock = clock
        self._dll = self.load(dll_path)
        self._own = own
        self.port = port & 1
        self._h = handle if handle is not None else self._dll.nopna_create(clock)
        if not self._h:
            raise RuntimeError("nopna_create 失败")
        self._out_rate = int(out_rate or (clock // 144))

        if rhythm_rom is not None and own:
            self.set_rhythm_rom(rhythm_rom)

    # -- 原生打击乐 ROM ------------------------------------------------
    def set_rhythm_rom(self, source) -> None:
        """装入原生 YM2608 打击乐 ROM（8192 字节 dump）。

        source 可以是文件路径或 bytes。文件不足 8192 字节时抛 ValueError。
        """
        data = load_rhythm_rom(source)
        rc = self._dll.nopna_set_rhythm_rom(self._h, data, len(data))
        if rc == -2:
            raise ValueError("打击乐 ROM 至少需要 8192 字节")
        if rc != 0:
            raise RuntimeError(f"nopna_set_rhythm_rom 失败（rc={rc}）")

    def clear_rhythm_rom(self) -> None:
        """撤掉原生 ROM，回落到内置合成打击乐。"""
        rc = self._dll.nopna_clear_rhythm_rom(self._h)
        if rc != 0:
            raise RuntimeError(f"nopna_clear_rhythm_rom 失败（rc={rc}）")

    # -- ChipCore -----------------------------------------------------
    def write(self, port: int, reg: int, val: int) -> None:
        self._dll.nopna_write(self._h, (port & 1) or self.port, reg & 0xFF, val & 0xFF)

    def read(self, port: int, reg: int) -> int:
        return self._dll.nopna_read(self._h, (port & 1) or self.port, reg & 0xFF)

    def sample_rate(self) -> int:
        return self._out_rate

    def native_sample_rate(self) -> int:
        return int(self._dll.nopna_sample_rate(self._h))

    def generate(self, nframes: int) -> bytes:
        buf = (ctypes.c_int16 * (2 * nframes))()
        fn = self._dll.nopna_generate_rate
        if fn is not None:
            fn(self._h, buf, nframes, self._out_rate)
        else:
            self._dll.nopna_generate(self._h, buf, nframes)
        return bytes(buf)

    def reset(self) -> None:
        self._dll.nopna_reset(self._h)

    def close(self) -> None:
        if self._own and self._h:
            self._dll.nopna_destroy(self._h)
            self._h = None


class YmfmPool:
    """按需分配若干颗 YM2608。

    DLL 句柄可能内部带 2 颗芯片（port 0/1），这里透明地把 chip index
    映射到 (handle, port)，所以多核心模拟不依赖 DLL 的具体打包方式。
    """

    def __init__(self, n_chips: int, clock: int = 8_000_000,
                 dll_path: str = DEFAULT_DLL, ports_per_handle: int = 1,
                 rhythm_rom: Optional[Union[str, bytes]] = None):
        self.chips: List[YmfmCore] = []
        self._handles = []
        dll = YmfmCore.load(dll_path)
        per = max(1, ports_per_handle)
        remaining = n_chips
        idx = 0
        while remaining > 0:
            h = dll.nopna_create(clock)
            if not h:
                raise RuntimeError("nopna_create 失败")
            self._handles.append(h)
            if rhythm_rom is not None:
                data = load_rhythm_rom(rhythm_rom)
                rc = dll.nopna_set_rhythm_rom(h, data, len(data))
                if rc != 0:
                    raise RuntimeError(f"nopna_set_rhythm_rom 失败（rc={rc}）")
            for p in range(per):
                if remaining <= 0:
                    break
                self.chips.append(YmfmCore(clock, dll_path, handle=h, port=p, own=False))
                remaining -= 1
                idx += 1

    def close(self) -> None:
        dll = YmfmCore.load()
        for h in self._handles:
            dll.nopna_destroy(h)
        self._handles.clear()
        self.chips.clear()


class VgmTap:
    """把 ChipCore 的寄存器写录成 VGM 1.61（OPNA 双芯片）。

    VGM 里 YM2608 的写命令：
      0x56 aa dd  -> 第 1 颗 OPNA 的寄存器 aa
      0xD7 aa dd  -> 第 2 颗 OPNA（0xD7 的低 2 位 = 芯片索引偏移）
    等待命令 0x61 nn nn（单位 1/44100 秒）。
    """

    def __init__(self, inner: ChipCore, clock: int = 8_000_000):
        self.inner = inner
        self.clock = clock
        self.chunks: List[bytes] = []
        self._last_sample = 0.0
        self.sample_pos = 0.0   # 以 1/44100 秒为单位

    # 采样率无关的等待换算：调用方按时间轴推进
    def advance(self, seconds: float) -> None:
        self.sample_pos += seconds * 44100.0

    def set_time(self, seconds: float) -> None:
        """把记录位置定位到绝对时间（播放器按事件/tick 时间轴调用）。"""
        self.sample_pos = seconds * 44100.0

    def _flush_wait(self) -> None:
        delta = int(self.sample_pos) - int(self._last_sample)
        if delta <= 0:
            return
        self._last_sample = int(self.sample_pos)
        delta = int(delta)
        while delta > 0:
            n = min(delta, 0xFFFF)
            self.chunks.append(struct.pack("<BH", 0x61, n))
            delta -= n

    def write(self, port: int, reg: int, val: int) -> None:
        self._flush_wait()
        # 单芯片 VGM：寄存器组 0 用 0x56，寄存器组 1（扩展口）用 0x57
        cmd = 0x56 + ((port >> 1) & 1)
        self.chunks.append(bytes([cmd, reg & 0xFF, val & 0xFF]))
        self.inner.write(port, reg, val)

    def read(self, port: int, reg: int) -> int:
        return self.inner.read(port, reg)

    def sample_rate(self) -> int:
        return self.inner.sample_rate()

    def generate(self, nframes: int) -> bytes:
        return self.inner.generate(nframes)

    # -- 输出 --------------------------------------------------------
    def build(self, total_seconds: float, extra: bytes = b"") -> bytes:
        self.sample_pos = max(self.sample_pos, total_seconds * 44100.0)
        self._flush_wait()
        data = b"".join(self.chunks) + extra
        hdr = bytearray(0x40)
        hdr[0:4] = b"Vgm "
        # 数据偏移：0x40 处开始（头部 0x40 字节 + 时钟字段已含）
        struct.pack_into("<I", hdr, 0x04, 0x40 + len(data) - 4)
        struct.pack_into("<I", hdr, 0x08, 0x00000161)   # v1.61
        struct.pack_into("<I", hdr, 0x0C, int(total_seconds * 44100.0))
        struct.pack_into("<I", hdr, 0x24, 44100)
        struct.pack_into("<I", hdr, 0x2C, self.clock)    # YM2608 #1 时钟
        struct.pack_into("<I", hdr, 0x30, self.clock)    # YM2608 #2 时钟
        hdr[0x34] = 0x00                                # 数据压缩: 无
        return bytes(hdr) + data

def make_core(kind: str = "auto", clock: int = 8_000_000,
              dll_path: str = DEFAULT_DLL,
              rhythm_rom: Optional[Union[str, bytes]] = None) -> Tuple[ChipCore, str]:
    """按名字创建核心；auto = 优先 ymfm，失败退化为 null。"""
    if kind in ("auto", "ymfm"):
        try:
            return YmfmCore(clock, dll_path, rhythm_rom=rhythm_rom), "ymfm"
        except Exception:
            if kind == "ymfm":
                raise
    return NullCore(clock), "null"
