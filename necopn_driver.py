# -*- coding: utf-8 -*-
"""NECOPN.DRV 动态行为复现。

本模块把逆向出的驱动行为（seg1 MIDI 事件层 + seg3 寄存器/包络层）翻译成 Python，
供播放器调用。**所有算法常量与公式都来自 NECOPN.DRV 反汇编**，地址标注见
`necopn_reverse/REPORT.md` 第 7 节。

硬件事实
--------
- 时钟 8.000 MHz，双 OPNA：逻辑 FM 通道 0-2 走 0x188/0x18A，3-5 走 0x18C/0x18E
- 每颗芯片真驱动只用 ch0-2（共 6 个 FM 声部）+ 第一颗的 YM2608 板载节奏
- Timer-A = 0x0E3（reg 0x26），周期 (1024-0xE3)*12/8MHz = 1.1955ms ≈ 836.5Hz
  —— 驱动的软件 LFO/包络就是在这个 tick 上跑的（seg3:0x0D27 轮询 → 0x0D59）

音色记录（64B，只用前 0x33 字节）→ 寄存器
------------------------------------------
记录存的是**取反值**（seg3:0x1239 取反表）：AR/DR/SR = 0x1F-rec，
SL/RR = 0x0F-rec，TL = 0x7F-rec。逻辑索引 >0x14 时读取器 +1（0x14 是 16 位运行时字段）。

    逻辑 0x00        记录 0x00     RL<<6|FB<<3|ALG   -> 0xB0（RL 位被硬件忽略）
    逻辑 0x01-0x04   记录 0x01-04  AR[4]              -> 0x50+slot  (KS<<6|AR)
    逻辑 0x05        记录 0x05     key-on 槽位掩码    -> 0x28 高 4 位（本库恒 0x0F）
    逻辑 0x06-0x09   记录 0x06-09  DR[4]              -> 0x60+slot
    逻辑 0x0A        记录 0x0A     LFO 波形 0..5      -> 运行时（本库 0..3）
    逻辑 0x0B-0x0E   记录 0x0B-0E  SR[4]              -> 0x70+slot
    逻辑 0x0F        记录 0x0F     LFO 起相位模式     -> 运行时（0=续跑 1=重启）
    逻辑 0x10-0x13   记录 0x10-13  SL[4]              -> 0x80+slot (RR<<4|SL)
    逻辑 0x14(word)  记录 0x14-15  LFO 速率           -> 运行时
    逻辑 0x15-0x18   记录 0x16-19  RR[4]              -> 0x80+slot
    逻辑 0x19        记录 0x1A     颤音深度 A         -> 运行时
    逻辑 0x1A-0x1D   记录 0x1B-1E  TL[4]              -> 0x40+slot（力度基准）
    逻辑 0x1E        记录 0x1F     颤音/TL 深度旋钮   -> 运行时
    逻辑 0x1F-0x22   记录 0x20-23  KS[4]              -> 0x50+slot
    逻辑 0x23        记录 0x24     颤音深度 B         -> 运行时
    逻辑 0x24-0x27   记录 0x25-28  ML[4]              -> 0x30+slot (DT<<4|ML)
    逻辑 0x29-0x2C   记录 0x2A-2D  DT[4]              -> 0x30+slot
    逻辑 0x2E-0x31   记录 0x2F-32  每算子颤音深度     -> 运行时

力度 / 音量
-----------
DGROUP:0x2E 的 128 项表：level = 0（v<2）或 0x40 + v/2。
cmd 0x1F（seg3:0x0BED）对每个算子：
    TL[i] = clamp8(mask[ALG][i] & (0x7F - level)) + (0x7F - rec[0x1B+i])
mask 来自 cs:0x0C55（8 算法 × 4 算子，0x00/0x7F）—— 只有"载波"算子吃力度。

鼓
--
DGROUP:0x8B+note（note 35..81）→ YM2608 节奏乐器 0..5，0x7F = 哑音。
level = level>>2（0..31），写 0x18+instr = 0xC0|level，再写 0x10 = 1<<instr。
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

from necopn_core import ChipCore

# ---------------------------------------------------------------- 常量（来自 DRV）
FNUM_SEMITONE = (617, 654, 692, 734, 777, 824, 873, 924, 979, 1038, 1099, 1165)   # cs:0x14F4
BEND_DELTA = (37, 38, 42, 43, 47, 49, 51, 54, 59, 61, 66, 68)                     # cs:0x150C
SLOT_OFFSET = (0x00, 0x08, 0x04, 0x0C)                                            # cs:0x142C
OPMASK = (                                                                       # cs:0x0C55
    (0x00, 0x00, 0x00, 0x7F), (0x00, 0x00, 0x00, 0x7F),
    (0x00, 0x00, 0x00, 0x7F), (0x00, 0x00, 0x00, 0x7F),
    (0x00, 0x7F, 0x00, 0x7F), (0x00, 0x7F, 0x7F, 0x7F),
    (0x00, 0x7F, 0x7F, 0x7F), (0x7F, 0x7F, 0x7F, 0x7F),
)
INVERT = {0x01: 0x1F, 0x02: 0x1F, 0x03: 0x1F, 0x04: 0x1F,
          0x06: 0x1F, 0x07: 0x1F, 0x08: 0x1F, 0x09: 0x1F,
          0x0B: 0x1F, 0x0C: 0x1F, 0x0D: 0x1F, 0x0E: 0x1F,
          0x10: 0x0F, 0x11: 0x0F, 0x12: 0x0F, 0x13: 0x0F,
          0x16: 0x0F, 0x17: 0x0F, 0x18: 0x0F, 0x19: 0x0F,
          0x1B: 0x7F, 0x1C: 0x7F, 0x1D: 0x7F, 0x1E: 0x7F}          # cs:0x1239

# DGROUP:0x2E 力度表（128 项，0x40 + v/2，v<2 为 0）
VELOCITY_TABLE = tuple(0 if v < 2 else min(0x7F, 0x40 + (v >> 1)) for v in range(128))
# DGROUP:0x8B+note 鼓映射（note 35..81 -> 节奏乐器，0x7F = 哑音）
DRUM_MAP = {
    35: 0, 36: 0, 37: 5, 38: 1, 39: 0x7F, 40: 1, 41: 4, 42: 3, 43: 4, 44: 3,
    45: 4, 46: 3, 47: 4, 48: 4, 49: 2, 50: 4, 51: 2, 52: 2, 53: 0x7F, 54: 0x7F,
    55: 2, 56: 0x7F, 57: 2, 58: 0x7F, 59: 2, 60: 0x7F, 61: 0x7F, 62: 0x7F,
    63: 0x7F, 64: 0x7F, 65: 0x7F, 66: 0x7F, 67: 0x7F, 68: 0x7F, 69: 0x7F,
    70: 0x7F, 71: 0x7F, 72: 0x7F, 73: 0x7F, 74: 0x7F, 75: 0x7F, 76: 0x7F,
    77: 0x7F, 78: 0x7F, 79: 0x7F, 80: 0x7F, 81: 0x7F,
}
TIMER_A = 0x0E3                      # reg 0x26（reg 0x25 未写 = 0）
TICK_HZ = 8_000_000 / 12.0 / (1024 - TIMER_A)      # ≈ 836.55 Hz
BEND_RANGE_DEFAULT = 2               # DGROUP:0xE2，语义 = 弯音半音范围


# ---------------------------------------------------------------- 音色
@dataclass
class Voice:
    """一条 64B 记录解码后的音色（寄存器域，已应用取反表）。"""
    raw: bytes
    head: int = 0
    ar: Tuple[int, ...] = ()
    dr: Tuple[int, ...] = ()
    sr: Tuple[int, ...] = ()
    sl: Tuple[int, ...] = ()
    rr: Tuple[int, ...] = ()
    tl: Tuple[int, ...] = ()
    ks: Tuple[int, ...] = ()
    ml: Tuple[int, ...] = ()
    dt: Tuple[int, ...] = ()
    slotmask: int = 0x0F
    shape: int = 0
    rate_a: int = 0
    rate_word: int = 0
    vib_a: int = 0
    vib_b: int = 0
    tlscale: int = 0
    knob: Tuple[int, ...] = ()

    @staticmethod
    def _inv(idx: int, rec: bytes) -> int:
        t = idx + (1 if idx > 0x14 else 0)
        v = rec[t]
        m = INVERT.get(t, 0)
        return (m - v) & 0xFF if m else v

    @classmethod
    def from_record(cls, rec: bytes) -> "Voice":
        rec = bytes(rec[:0x40]).ljust(0x40, b"\x00")
        inv = lambda i: cls._inv(i, rec)
        return cls(
            raw=rec,
            head=inv(0x00),
            ar=tuple(inv(0x01 + k) for k in range(4)),
            dr=tuple(inv(0x06 + k) for k in range(4)),
            sr=tuple(inv(0x0B + k) for k in range(4)),
            sl=tuple(inv(0x10 + k) for k in range(4)),
            rr=tuple(inv(0x15 + k) for k in range(4)),
            tl=tuple(inv(0x1A + k) for k in range(4)),
            ks=tuple(rec[0x20 + k] for k in range(4)),
            ml=tuple(rec[0x25 + k] for k in range(4)),
            dt=tuple(rec[0x2A + k] for k in range(4)),
            slotmask=rec[0x05] & 0x0F,
            shape=rec[0x0A],
            rate_a=rec[0x0F],
            rate_word=struct.unpack_from("<H", rec, 0x14)[0],
            vib_a=rec[0x1A],
            vib_b=rec[0x24],
            tlscale=rec[0x1F],
            knob=tuple(rec[0x2F + k] & 0x0F for k in range(4)),
        )

    @property
    def fbalg(self) -> int:
        return self.head & 0x3F

    @property
    def vib_depth(self) -> int:
        """scale_knob(seg3:0x0F16)：颤音深度 = vib_a * vib_b。"""
        return self.vib_a * self.vib_b

    def op_tremolo(self) -> Tuple[int, ...]:
        """scale_knob：ws[i] = (knob[i]&0xF) * tlscale / 15。"""
        return tuple((k & 0xF) * self.tlscale // 15 for k in self.knob)


# ---------------------------------------------------------------- 通道状态
@dataclass
class Slot:
    """seg1 分配器的通道记录（state+0x4A，stride 0x14）。"""
    note: int = 0          # +0x4A
    midi_ch: int = 0       # +0x4B
    unknown: int = 0       # +0x4C
    active: bool = False   # +0x4D
    serial: int = 0        # +0x50..0x53


@dataclass
class VoiceState:
    """seg3 的通道工作区（DS:0x260+ch*0x20）+ 运行时变量。"""
    started: bool = False        # ws+0x18 bit7
    env_state: int = 0           # ws+0x18 低 2 位
    key_alive: int = 0           # ws+0x14（key-on 后 0xFF，key-off 后 0）
    lfo_counter: int = 0         # ws+0x19
    vib_depth: int = 0           # ws+0x1b
    fnum_word: int = 0           # ws+0x1d（block<<11 | fnum）
    lfo_value: int = 0           # DS:0x2882+ch*2
    lfo_dir: int = 1             # DS:0x648+ch*2
    tl_base: List[int] = field(default_factory=lambda: [0, 0, 0, 0])   # DS:0x6D8+ch*4
    trem: List[int] = field(default_factory=lambda: [0, 0, 0, 0])      # ws+0..3
    random_state: int = 0        # DS:0x2876：数据段初值 = 0（驱动从不播种，shape3 恒为 0）


class ChipUnit:
    """一颗 OPNA 上的 6 个（真驱动是 3 个）FM 声部 + 节奏。"""

    def __init__(self, core: ChipCore, index: int, channels: int, first_chan: int):
        self.core = core
        self.index = index
        self.channels = channels
        self.first_chan = first_chan        # 逻辑通道起始号

    def write(self, reg: int, val: int) -> None:
        self.core.write(0, reg & 0xFF, val & 0xFF)


# ---------------------------------------------------------------- 系统
class NecopnSystem:
    """NECOPN 驱动（可多核心）。

    mode="necopn" : 2 颗芯片，每颗只用 ch0-2 —— 完全对应真驱动的 6 声部
    mode="full"   : N 颗芯片 × 6 声部 —— “假设满通道 NECOPN 驱动”
    """

    def __init__(self, cores: Sequence[ChipCore], mode: str = "necopn",
                 channels_per_core: Optional[int] = None,
                 clock: int = 8_000_000, bend_range: int = BEND_RANGE_DEFAULT,
                 lfo_enabled: bool = True):
        self.cores = list(cores)
        self.clock = clock
        self.mode = mode
        self.lfo_enabled = lfo_enabled
        if channels_per_core is None:
            channels_per_core = 3 if mode == "necopn" else 6
        self.cpc = channels_per_core
        self.bend_range = bend_range
        self.n_ch = len(self.cores) * self.cpc
        self.units = [ChipUnit(c, i, self.cpc, i * self.cpc)
                      for i, c in enumerate(self.cores)]
        self.slots: List[Slot] = [Slot() for _ in range(self.n_ch)]
        self.vstates: List[VoiceState] = [VoiceState() for _ in range(self.n_ch)]
        self.programs = [0] * 16
        self.bend14 = [8192] * 16
        self.serial = 0
        self.global_tick = 0
        self.voices: List[Voice] = []
        self.fmoctave = [0] * 128
        self.trace: Optional[List[Tuple[float, int, int, int]]] = None
        self.time = 0.0
        self._init_hardware()

    # -- 初始化 ------------------------------------------------------
    def load_bank(self, voices: Sequence[Voice], fmoctave: Sequence[int]) -> None:
        self.voices = list(voices)
        self.fmoctave = [self._s8(v) for v in fmoctave]

    @staticmethod
    def _s8(v: int) -> int:
        v &= 0xFF
        return v - 256 if v >= 0x80 else v

    def _w(self, unit: ChipUnit, reg: int, val: int) -> None:
        if self.trace is not None:
            self.trace.append((self.time, unit.index, reg & 0xFF, val & 0xFF))
        unit.write(reg, val)

    def _init_hardware(self) -> None:
        """cmd 0x03/0x04（seg3:0x0B22 / 0x0BA4）+ seg3:0x1133 的寄存器初值。"""
        for u in self.units:
            # SSG-EG 全 0（跳过 0x93/0x97/0x9B）
            for reg in range(0x90, 0x9F):
                if reg in (0x93, 0x97, 0x9B):
                    continue
                self._w(u, reg, 0x00)
            # 0xB4/0xB5/0xB6 = 0xC0（pan L+R）
            for ch in range(3):
                self._w(u, 0xB4 + ch, 0xC0)
                self._w(u, 0xB4 + ch, 0xC0)
            # 节奏初始化（seg3:0x1162）
            self._w(u, 0x12, 0x00)
            self._w(u, 0x11, 0x30)
            for i in range(6):
                self._w(u, 0x18 + i, 0xC4)
            # Timer：0x27=0x30（开 Timer A/B 标志），0x26=0xE3
            self._w(u, 0x27, 0x30)
            self._w(u, 0x26, TIMER_A & 0xFF)
            self._w(u, 0x25, (TIMER_A >> 8) & 0x03)

    # -- 寄存器写入（chip 选择） -------------------------------------
    def _reg_chan(self, ch: int, base: int, off: int = 0) -> Tuple[ChipUnit, int]:
        unit = self.units[ch // self.cpc]
        chip_ch = ch % self.cpc
        return unit, base + chip_ch + off

    def write_chan(self, ch: int, base: int, val: int, off: int = 0) -> None:
        u, reg = self._reg_chan(ch, base, off)
        self._w(u, reg, val)

    # -- 音色装载 cmd 0x16（seg3:0x0BB9 + 0x1430） --------------------
    def load_voice(self, ch: int, voice: Voice) -> None:
        u = self.units[ch // self.cpc]
        chip_ch = ch % self.cpc
        self._w(u, 0xB0 + chip_ch, voice.head & 0xFF)          # RL 位硬件忽略
        for i in range(4):
            s = SLOT_OFFSET[i]
            self._w(u, 0x50 + s + chip_ch, ((voice.ks[i] & 3) << 6) | (voice.ar[i] & 0x1F))
        for i in range(4):
            self._w(u, 0x60 + SLOT_OFFSET[i] + chip_ch, voice.dr[i] & 0x7F)
        for i in range(4):
            self._w(u, 0x70 + SLOT_OFFSET[i] + chip_ch, voice.sr[i] & 0x1F)
        for i in range(4):
            self._w(u, 0x80 + SLOT_OFFSET[i] + chip_ch,
                    ((voice.rr[i] & 0x0F) << 4) | (voice.sl[i] & 0x0F))
        for i in range(4):
            self._w(u, 0x30 + SLOT_OFFSET[i] + chip_ch,
                    ((voice.dt[i] & 7) << 4) | (voice.ml[i] & 0x0F))
        # scale_knob(seg3:0x0F16)
        vs = self.vstates[ch]
        vs.vib_depth = voice.vib_depth
        vs.trem = list(voice.op_tremolo())

    # -- 力度/音量 cmd 0x1F（seg3:0x0BED） ---------------------------
    def set_velocity(self, ch: int, level: int) -> None:
        vs = self.vstates[ch]
        voice = self.current_voice(ch)
        alg = voice.head & 7
        atten = 0x7F - (level & 0x7F)
        for i in range(4):
            base = (0x7F - voice.raw[0x1B + i]) & 0x7F
            v = (OPMASK[alg][i] & atten) + base
            vs.tl_base[i] = v & 0xFF
        self._write_tl(ch, vs.tl_base)

    def _write_tl(self, ch: int, values: Sequence[int]) -> None:
        u = self.units[ch // self.cpc]
        chip_ch = ch % self.cpc
        for i in range(4):
            v = values[i]
            if v >= 0x7F:
                v = 0x7F
            self._w(u, 0x40 + SLOT_OFFSET[i] + chip_ch, v)

    def current_voice(self, ch: int) -> Voice:
        prog = self.programs[self.slots[ch].midi_ch & 0x0F]
        if not self.voices:
            raise RuntimeError("音色库未装载")
        return self.voices[prog % len(self.voices)]

    # -- 音高 cmd 0x20（seg3:0x0C75） --------------------------------
    def set_pitch(self, ch: int, note: int, bend255: int) -> None:
        note &= 0xFF
        bend255 &= 0xFF
        st = note % 12
        blk = note // 12
        fnum = FNUM_SEMITONE[st] + (BEND_DELTA[st] * bend255) // 0xFF
        word = (fnum & 0x7FF) | ((blk & 7) << 11)
        self._write_fnum(ch, word)

    def _write_fnum(self, ch: int, word: int) -> None:
        u = self.units[ch // self.cpc]
        chip_ch = ch % self.cpc
        self._w(u, 0xA4 + chip_ch, (word >> 8) & 0xFF)   # block<<3 | fnum[9:8]
        self._w(u, 0xA0 + chip_ch, word & 0xFF)
        self.vstates[ch].fnum_word = word

    # -- 键开/关 cmd 0x25 / 0x26（seg3:0x130C / 0x139A） -------------
    def key_on(self, ch: int, note: int) -> None:
        vs = self.vstates[ch]
        voice = self.current_voice(ch)
        word = vs.fnum_word
        u = self.units[ch // self.cpc]
        chip_ch = ch % self.cpc
        chan_code = chip_ch + 1 if chip_ch >= 3 else chip_ch
        self._w(u, 0x28, ((voice.slotmask & 0x0F) << 4) | (chan_code & 7))
        vs.key_alive = 0xFF
        vs.started = True
        vs.env_state = 3

    def key_off(self, ch: int) -> None:
        vs = self.vstates[ch]
        u = self.units[ch // self.cpc]
        chip_ch = ch % self.cpc
        chan_code = chip_ch + 1 if chip_ch >= 3 else chip_ch
        self._w(u, 0x28, chan_code & 7)
        vs.key_alive = 0

    # -- 鼓 cmd 0x24（seg3:0x0CCB） ----------------------------------
    def drum(self, instrument: int, level: int) -> None:
        u = self.units[0]
        if instrument >= 0x20 or level >= 0x20:
            return
        self._w(u, 0x18 + instrument, 0xC0 | (level & 0x1F))
        self._w(u, 0x10, 1 << (instrument & 7))

    # -- MIDI 事件（seg1:0x04DE 分发） -------------------------------
    def note_on(self, midi_ch: int, note: int, velocity: int) -> None:
        midi_ch &= 0x0F
        note &= 0x7F
        velocity &= 0x7F
        if midi_ch in (9, 15):
            # seg1:0x055F：鼓通道先判力度；力度 0 走 note-off 路径 → 落到 0x2CC（`ret 8`）= 完全无写入
            if velocity:
                self._drum_note_on(velocity, note)
            return
        if velocity == 0:
            self.note_off(midi_ch, note)
            return
        level = VELOCITY_TABLE[velocity]
        prog = self.programs[midi_ch]
        ch = self._allocate(midi_ch, note)
        if ch < 0:
            return
        slot = self.slots[ch]
        slot.note = note
        slot.midi_ch = midi_ch
        slot.active = True
        self.serial += 1
        slot.serial = self.serial
        n2 = self._transpose(midi_ch, note, prog)
        self.set_velocity(ch, level)
        # seg1:0x0010 的闸门：`cmp byte ptr [bp+6], 0x5C` + `jae 0x51`（bp+6 = 移调后的 note）。
        # 移调后音高 >= 0x5C(92) 时驱动不写 FNUM、不写 0x28 —— 真机无声，但声部已被分配占用。
        if n2 >= 0x5C:
            return
        self.set_pitch(ch, n2, 0)
        self.key_on(ch, n2)

    def _drum_note_on(self, velocity: int, note: int) -> None:
        if not (0x23 <= note <= 0x51):
            return
        inst = DRUM_MAP.get(note, 0x7F)
        if inst == 0x7F:
            return
        level = VELOCITY_TABLE[velocity] >> 2
        self.drum(inst, level)

    def note_off(self, midi_ch: int, note: int) -> None:
        midi_ch &= 0x0F
        note &= 0x7F
        if midi_ch in (9, 15):
            return                      # seg1:0x2CC = ret 8（鼓无释放）
        ch = self._find(midi_ch, note)
        if ch < 0:
            return
        slot = self.slots[ch]
        if slot.note == 0:
            return
        self.key_off(ch)
        slot.active = False
        slot.note = 0
        self.serial += 1
        slot.serial = self.serial

    def program_change(self, midi_ch: int, program: int) -> None:
        midi_ch &= 0x0F
        program &= 0x7F
        if midi_ch in (9, 15):
            return
        # seg1:0x0478：0x049E 先看 active，0x04AA `jne 0x4c3` **只清本 MIDI 通道**的声部；
        # note != 0 的先 key off（0x04B0 je 0x4bc 跳过 note==0 的），然后一律 active=0（0x04BF）。
        # 原实现把 slot.active = False 写在 if 外面，会把**所有**通道标记成空闲，
        # 后续 _allocate pass 2 直接把这些还在响的声部发出去 -> 重叠/错音，且 note_off 找不到槽。
        for ch, slot in enumerate(self.slots):
            if not (slot.active and slot.midi_ch == midi_ch):
                continue
            if slot.note != 0:
                self.key_off(ch)
            slot.active = False
        self.programs[midi_ch] = program

    def pitch_bend(self, midi_ch: int, lsb: int, msb: int) -> None:
        """seg1:0x03B2：bend14 = msb<<7|lsb，note' = note + (b+base)//step - range。"""
        midi_ch &= 0x0F
        bend14 = ((msb & 0x7F) << 7) | (lsb & 0x7F)
        self.bend14[midi_ch] = bend14
        rng = self.bend_range
        step = 8191 // rng + 1
        base = step * rng - 8192
        total = base + bend14
        for ch, slot in enumerate(self.slots):
            if not (slot.active and slot.midi_ch == midi_ch and slot.note != 0):
                continue
            n2 = self._transpose(midi_ch, slot.note, self.programs[midi_ch])
            note_final = n2 + total // step - rng
            bend255 = ((total % step) * 255) // step
            self.set_pitch(ch, note_final & 0xFF, bend255)

    def control_change(self, midi_ch: int, cc: int, value: int) -> None:
        """seg1:0x05AE：只有 CC>=0x7B（All Notes Off 等）有效。"""
        if cc < 0x7B:
            return
        for ch, slot in enumerate(self.slots):
            if slot.active:
                self.note_off(slot.midi_ch, slot.note)

    def _transpose(self, midi_ch: int, note: int, program: int) -> int:
        """seg1:0x0232：note += FMOCTAVE[program]（无符号比较后 clamp 0x7F）。"""
        if midi_ch in (9, 15) or program > 0x7F:
            return note
        v = (note + self.fmoctave[program]) & 0xFF
        return 0x7F if v > 0x7F else v

    # -- 通道分配（seg1:0x0058） ------------------------------------
    def _find(self, midi_ch: int, note: int) -> int:
        for i, s in enumerate(self.slots):
            if s.midi_ch == midi_ch and s.note == note and s.active:
                return i
        return -1

    def _allocate(self, midi_ch: int, note: int) -> int:
        # pass 1: 同 MIDI 通道且 note==0 的活跃通道 -> 释放后直接复用（不重装音色）
        for i, s in enumerate(self.slots):
            if s.active and s.note == 0 and s.midi_ch == midi_ch:
                self.key_off(i)
                return i
        # pass 2: 空闲通道
        found = -1
        for i, s in enumerate(self.slots):
            if not s.active:
                found = i
                break
        # pass 3: 其它 MIDI 通道、note==0 的活跃通道
        if found < 0:
            for i, s in enumerate(self.slots):
                if s.active and s.midi_ch != midi_ch and s.note == 0:
                    found = i
                    break
        # pass 4: 取 MIDI 通道号最大的，同号取最早（serial 最小）
        # sub_58 里 var_6（best_midi）初值是 **0**（0x0117 `sub ax,ax`），不是 -1；
        # best_serial 初值是 DGROUP:0xDE/0xE0 的全局 serial 计数器（0x0062 读入，
        # 每分配一个音 +1，seg1:0x0318），恒大于所有已分配 slot 的 serial。
        # 相等分支 0x014C 会把 found 从 -1 直接赋成 i，所以这里**不能**加
        # `found >= 0` 之类的保护：否则 6 个 slot 全是 midi_ch=0 时一个都选不中。
        if found < 0:
            best_midi = 0
            best_serial = self.serial + 1
            for i, s in enumerate(self.slots):
                if s.midi_ch > best_midi:
                    best_midi, found, best_serial = s.midi_ch, i, s.serial
                elif s.midi_ch == best_midi and s.serial < best_serial:
                    found, best_serial = i, s.serial
            if found < 0:
                return -1
            # seg1:0x0181：抢不到比自己"优先级更高"的通道就放弃
            if self.slots[found].midi_ch < midi_ch:
                return -1
        # 公共尾巴 seg1:0x0194
        s = self.slots[found]
        if s.active and s.note != 0:
            self.key_off(found)
        if not (s.active and s.midi_ch == midi_ch):
            self.load_voice(found, self.voices[self.programs[midi_ch] % len(self.voices)])
        return found

    # -- 定时器 tick（seg3:0x0D59 包络/LFO） -------------------------
    def tick(self) -> None:
        self.global_tick = (self.global_tick + 1) & 0xFFFF
        if not self.lfo_enabled:
            return
        for ch in range(self.n_ch):
            vs = self.vstates[ch]
            if not vs.started:
                continue
            if vs.key_alive == 0:
                vs.env_state = 3
                continue
            voice = self.current_voice(ch)
            if voice.rate_word == 0:
                vs.env_state = 3
                continue
            st = vs.env_state & 3
            if st == 0:
                # 0x0DA5 → 0xEFB：还没进包络态，只自增计数器
                vs.lfo_counter = (vs.lfo_counter + 1) & 0xFFFF
                continue
            if st == 3:
                vs.env_state = (vs.env_state & ~3) | 1
                if voice.rate_a == 1:
                    vs.lfo_counter = 0
                    vs.lfo_value = 0
                    vs.lfo_dir = -1 if voice.shape == 4 else 1
                    vs.env_state = (vs.env_state & ~3) | 2
                elif voice.rate_a == 0:
                    vs.lfo_counter = self.global_tick
                    vs.lfo_value = self.global_tick
                    vs.env_state = (vs.env_state & ~3) | 2
                else:
                    # 0x0DD5-0x0DDD：置延时计数后直接 0xEFB —— 本 tick 不步进 LFO，只 +1
                    vs.lfo_counter = ((voice.rate_a - 1) * 4 + 1) & 0xFFFF
                    continue
            elif st == 1:
                vs.lfo_counter = (vs.lfo_counter - 1) & 0xFF
                if vs.lfo_counter == 0:
                    vs.lfo_counter = 0
                    vs.lfo_value = 0
                    vs.lfo_dir = -1 if voice.shape == 4 else 1
                    vs.env_state = (vs.env_state & ~3) | 2
                else:
                    continue                    # 0x0E3A `jmp 0xEFE`：这条分支跳过末尾的 inc
            self._lfo_step(ch, voice, vs)
            # seg3:0x0EFB `inc word ptr [bx + 0x19]`：走完步进的 tick 末尾自增 LFO 计数器
            # （方波相位 0x108D 与随机步进 0x10AF 都读它）
            vs.lfo_counter = (vs.lfo_counter + 1) & 0xFFFF

    def _lfo_step(self, ch: int, voice: Voice, vs: VoiceState) -> None:
        period = 0xFFFF // max(1, voice.rate_word)
        shape = voice.shape
        overflow = False
        if shape == 1:                                   # 方波 seg3:0x108D
            phase = (vs.lfo_counter // max(1, period)) & 1
            vs.lfo_value = 0x7FFF if phase == 0 else (-32768 & 0xFFFF)
        elif shape == 3:                                 # 随机 seg3:0x10AF
            # 0x10B2：除数 = [0x2874]|1 = (0xFFFF/速率)|1（不是 速率|1）
            step = period | 1
            if (vs.lfo_counter % step) == 0:
                # 0x10C7-0x10D5：state = (state * 0x383) mod 0x7FFF（idiv 取余，不是按位与）
                vs.random_state = (vs.random_state * 0x383) % 0x7FFF
            vs.lfo_value = vs.random_state
        else:
            step = voice.rate_word * vs.lfo_dir
            old = vs.lfo_value
            new = (old + step) & 0xFFFF
            if ((old ^ new) & 0x8000) != 0:              # 溢出
                overflow = True
            else:
                vs.lfo_value = new
            if shape == 2 and overflow:                  # 三角 seg3:0x1052
                vs.lfo_dir = -vs.lfo_dir
            elif shape == 0 and overflow:                # 三角 seg3:0x1075
                vs.lfo_value = (-vs.lfo_value) & 0xFFFF
            elif shape in (4, 5):                        # 锯齿钳 0 seg3:0x1081/0x1060
                if self._s16(vs.lfo_value) < 0:
                    vs.lfo_value = 0
        # 颤音 seg3:0x0EAE
        word = vs.fnum_word
        fnum = word & 0x7FF
        if vs.vib_depth:
            lfo = self._s16(vs.lfo_value)
            mod = (lfo * vs.vib_depth) // 0x7FFF
            fnum = fnum + (mod * fnum) // 0x7FF
            if fnum < 0:
                fnum = 0
            if fnum > 0x7FF:
                fnum = 0x7FF
            self._write_fnum(ch, (word & 0xF800) | fnum)
        # 颤音/TL 调制 seg3:0x0F75（逐字节复刻：ah 清零、idiv、add 带进位钳位）
        out = []
        lfo = self._s16(vs.lfo_value)
        for i in range(4):
            b = vs.tl_base[i] & 0xFF
            t1 = (vs.trem[i] * lfo) // 0x7FFF        # ax = ws[i]*env/0x7FFF
            t1 &= 0xFF                               # xor ah,ah
            t2 = (t1 * b) // 0x7F                    # idiv 0x7F -> al
            t2 &= 0xFF
            s = t2 + b                               # add al,[0x6D8]
            v = 0 if s > 0xFF else (s & 0xFF)        # jae / mov al,0
            if v > 0x7F:
                v = 0x7F
            out.append(v)
        self._write_tl(ch, out)

    @staticmethod
    def _s16(v: int) -> int:
        v &= 0xFFFF
        return v - 0x10000 if v >= 0x8000 else v

    # -- 组合事件 ----------------------------------------------------
    def send_midi(self, status: int, d1: int = 0, d2: int = 0) -> None:
        """seg1:0x04DE 的 MIDI 消息分发。"""
        kind = status & 0xF0
        ch = status & 0x0F
        if kind == 0x80:
            self.note_off(ch, d1)
        elif kind == 0x90:
            self.note_on(ch, d1, d2)
        elif kind == 0xB0:
            self.control_change(ch, d1, d2)
        elif kind == 0xC0:
            self.program_change(ch, d1)
        elif kind == 0xE0:
            self.pitch_bend(ch, d1, d2)
