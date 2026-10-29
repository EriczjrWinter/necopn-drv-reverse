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

SSG 扩展
--------
物理目标机器只有一颗 YM2608，FM 6 通道被驱动拆成两个 3 通道实例（规避 0x28
通道编码怪癖）。SSG 没有这个问题，直接挂第一颗芯片上，共 3 个方波通道。
SSG 只走 units[0]，绝不触碰 units[1] 的 SSG 寄存器。
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

VELOCITY_TABLE = tuple(0 if v < 2 else min(0x7F, 0x40 + (v >> 1)) for v in range(128))
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
    raw: bytes = b""
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
        return self.vib_a * self.vib_b

    def op_tremolo(self) -> Tuple[int, ...]:
        return tuple((k & 0xF) * self.tlscale // 15 for k in self.knob)


# ---------------------------------------------------------------- 通道状态
@dataclass
class Slot:
    note: int = 0
    midi_ch: int = 0
    unknown: int = 0
    active: bool = False
    serial: int = 0


@dataclass
class VoiceState:
    started: bool = False
    env_state: int = 0
    key_alive: int = 0
    lfo_counter: int = 0
    vib_depth: int = 0
    fnum_word: int = 0
    lfo_value: int = 0
    lfo_dir: int = 1
    tl_base: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    trem: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    random_state: int = 0


# ---------------------------------------------------------------- SSG 状态
@dataclass
class SsgSlot:
    note: int = 0
    midi_ch: int = 0
    active: bool = False
    serial: int = 0


@dataclass
class SsgState:
    volume: int = 0
    target_volume: int = 0
    env_state: str = "idle"      # idle / attack / sustain / release
    base_tp: int = 0


class ChipUnit:
    def __init__(self, core: ChipCore, index: int, channels: int, first_chan: int):
        self.core = core
        self.index = index
        self.channels = channels
        self.first_chan = first_chan

    def write(self, reg: int, val: int) -> None:
        self.core.write(0, reg & 0xFF, val & 0xFF)


# ---------------------------------------------------------------- 系统
class NecopnSystem:
    def __init__(self, cores: Sequence[ChipCore],
                 clock: int = 8_000_000, bend_range: int = BEND_RANGE_DEFAULT,
                 lfo_enabled: bool = False,
                 lfo_divisor: int = 7,
                 lfo_rate_scale: float = 1.0,
                 lfo_depth_scale: float = 1.0,
                 ssg_mode: str = "off",
                 pure_ssg: bool = False,
                 ssg_midi_channels=(),
                 ssg_atten_db: float = 0.0,
                 drum_atten_db: float = 0.0,
                 fm_ssg_threshold: int = 4,
                 ssg_plan=None):
        
        self.cores = list(cores)
        self.clock = clock
        self.lfo_enabled = lfo_enabled
        self.lfo_divisor = max(1, int(lfo_divisor))
        self.lfo_rate_scale = max(0.01, float(lfo_rate_scale))
        self.lfo_depth_scale = max(0.0, float(lfo_depth_scale))

        # --- 1. 纯 SSG 模式拦截 ---
        self.pure_ssg = pure_ssg
        if self.pure_ssg:
            ssg_mode = "manual"
            ssg_midi_channels = [c for c in range(16) if c != 9]

        # --- 2. SSG 状态初始化 ---
        self.ssg_mode = ssg_mode
        self.ssg_atten_db = float(ssg_atten_db)
        self.drum_atten_db = float(drum_atten_db)
        self.ssg_midi_channels = set(ssg_midi_channels)
        self.fm_ssg_threshold = max(1, int(fm_ssg_threshold))
        self.ssg_plan = ssg_plan or {}
        self.ssg_chan_range = {ch: (start, start + peak)
                               for ch, (start, peak) in self.ssg_plan.items()}
                               
        self.n_ssg = 3 if self.ssg_mode != "off" else 0
        self.ssg_slots: List[SsgSlot] = [SsgSlot() for _ in range(self.n_ssg)]
        self.ssg_states: List[SsgState] = [SsgState() for _ in range(self.n_ssg)]
        self._ssg_tp_table = self._build_ssg_tp_table()

        # --- 3. FM 状态初始化 ---
        channels_per_core = 3
        self.cpc = channels_per_core
        self.bend_range = bend_range
        self.n_ch = len(self.cores) * self.cpc
        
        # 关键点：必须有这一行，_init_hardware 才不会报错
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
        self.random_state = 0

        # --- 4. 最后调用硬件初始化 ---
        self._init_hardware()

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
        for u in self.units:
            for reg in range(0x90, 0x9F):
                if reg in (0x93, 0x97, 0x9B):
                    continue
                self._w(u, reg, 0x00)
            for ch in range(3):
                self._w(u, 0xB4 + ch, 0xC0)
                self._w(u, 0xB4 + ch, 0xC0)
            self._w(u, 0x12, 0x00)
            self._w(u, 0x11, 0x30)
            for i in range(6):
                self._w(u, 0x18 + i, 0xC4)
            self._w(u, 0x27, 0x30)
            self._w(u, 0x26, TIMER_A & 0xFF)
            self._w(u, 0x25, (TIMER_A >> 8) & 0x03)

        # ---- SSG 初始化：只对第一颗（唯一一颗物理）芯片 ----
        if self.ssg_mode != "off":
            u0 = self.units[0]
            # reg 0x07 低有效：0x3F = tone/noise 全禁
            self._w(u0, 0x07, 0x3F)
            for i in range(3):
                self._w(u0, 0x08 + i, 0x00)
                self._w(u0, 0x00 + i * 2, 0x00)
                self._w(u0, 0x01 + i * 2, 0x00)
            self._w(u0, 0x06, 0x1F)    # 噪声频率（本轮不用）
            self._w(u0, 0x0B, 0x00)    # 硬件包络关闭

    # ---------------------------------------------------------------- SSG 核心
    def _build_ssg_tp_table(self):
        """SSG_clock = clock / 4；TP = SSG_clock / (16*freq) - 1。
        存未 clamp 的原始值，供范围判断。"""
        ssg_clock = self.clock / 4.0
        table = []
        for note in range(128):
            freq = 440.0 * 2 ** ((note - 69) / 12.0)
            table.append(int(round(ssg_clock / (16.0 * freq))) - 1)
        return table

    def _ssg_note_in_range(self, note: int) -> bool:
        if not (0 <= note < 128):
            return False
        return self._ssg_tp_table[note] <= 0xFFF

    def _ssg_reg(self, gch: int):
        # 单 OPNA：所有 SSG 走 units[0]
        return self.units[0], gch

    def _write_ssg_pitch(self, gch: int, note_float: float) -> None:
        freq = 440.0 * 2 ** ((note_float - 69) / 12.0)
        tp = int(round(self.clock / 4.0 / (16.0 * freq))) - 1
        tp = max(0, min(0xFFF, tp))
        unit, ssg_ch = self._ssg_reg(gch)
        self._w(unit, ssg_ch * 2, tp & 0xFF)
        self._w(unit, ssg_ch * 2 + 1, (tp >> 8) & 0x0F)
        self.ssg_states[gch].base_tp = tp

    def _ssg_volume_from_velocity(self, level: int) -> int:
        lin = 15.0 * (level & 0x7F) / 127.0
        lin *= 10 ** (-self.ssg_atten_db / 20.0)
        return max(0, min(15, int(round(lin))))

    def _write_ssg_volume(self, gch: int, vol: int) -> None:
        unit, ssg_ch = self._ssg_reg(gch)
        self._w(unit, 0x08 + ssg_ch, vol & 0x0F)

    def _update_ssg_mixer(self) -> None:
        """reg 0x07 低有效。env_state != "idle" 视为发声；noise 全禁。"""
        mask = 0
        for ch in range(3):
            if self.ssg_states[ch].env_state != "idle":
                mask |= (1 << ch)
        reg7 = 0x38 | ((~mask) & 0x07)
        self._w(self.units[0], 0x07, reg7 & 0x3F)

    def _ssg_find(self, midi_ch: int, note: int) -> int:
        for i, s in enumerate(self.ssg_slots):
            if s.midi_ch == midi_ch and s.note == note and s.active:
                return i
        return -1

    def _ssg_allocate(self, midi_ch: int, note: int) -> int:
        # 1. 优先找完全空闲（idle）的槽位
        for i in range(self.n_ssg):
            if self.ssg_states[i].env_state == "idle":
                return i
                
        # 2. 其次找正在 release 的槽位
        for i in range(self.n_ssg):
            if self.ssg_states[i].env_state == "release":
                return i
                
        # 3. 如果全在响
        if self.pure_ssg:
            # 纯 SSG 模式：踢掉最老的音符（LRKO）
            oldest_gch = 0
            oldest_serial = 9999999
            for i in range(self.n_ssg):
                if self.ssg_slots[i].serial < oldest_serial:
                    oldest_serial = self.ssg_slots[i].serial
                    oldest_gch = i
            return oldest_gch
            
        # 否则返回 -1，交给 FM
        return -1

    def ssg_note_on(self, midi_ch: int, note: int, velocity: int) -> bool:
        if self.ssg_mode == "off":
            return False
        n2 = self._transpose(midi_ch, note, self.programs[midi_ch & 0x0F])
        if not self._ssg_note_in_range(n2):
            return False
        level = VELOCITY_TABLE[velocity & 0x7F]
        target = self._ssg_volume_from_velocity(level)
        if target == 0:
            return False   # 音量映射后为 0，回退 FM（3 个槽不浪费）
        gch = self._ssg_allocate(midi_ch, note)
        if gch < 0:
            return False
        slot = self.ssg_slots[gch]
        ss = self.ssg_states[gch]
        # 复用槽前先静音
        self._write_ssg_volume(gch, 0)
        ss.volume = 0
        ss.target_volume = target
        slot.note = note
        slot.midi_ch = midi_ch
        slot.active = True
        self.serial += 1
        slot.serial = self.serial
        self._write_ssg_pitch(gch, float(n2))
        ss.env_state = "attack"
        self._update_ssg_mixer()
        return True

    def ssg_note_off(self, midi_ch: int, note: int) -> None:
        if self.ssg_mode == "off":
            return
        gch = self._ssg_find(midi_ch, note)
        if gch < 0:
            return
        slot = self.ssg_slots[gch]
        slot.active = False
        slot.note = 0
        self.ssg_states[gch].env_state = "release"
        self.serial += 1
        slot.serial = self.serial

    def _tick_ssg(self) -> None:
        """两段式包络：attack 每 tick +2，release 每 tick -1。"""
        for gch in range(self.n_ssg):
            ss = self.ssg_states[gch]
            if ss.env_state == "attack":
                ss.volume = min(ss.target_volume, ss.volume + 2)
                self._write_ssg_volume(gch, ss.volume)
                if ss.volume >= ss.target_volume:
                    ss.env_state = "sustain"
            elif ss.env_state == "release":
                ss.volume = max(0, ss.volume - 1)
                self._write_ssg_volume(gch, ss.volume)
                if ss.volume == 0:
                    ss.env_state = "idle"
                    self._update_ssg_mixer()

    # ---------------------------------------------------------------- FM 通道
    def _reg_chan(self, ch: int, base: int, off: int = 0) -> Tuple[ChipUnit, int]:
        unit = self.units[ch // self.cpc]
        chip_ch = ch % self.cpc
        return unit, base + chip_ch + off

    def write_chan(self, ch: int, base: int, val: int, off: int = 0) -> None:
        u, reg = self._reg_chan(ch, base, off)
        self._w(u, reg, val)

    def load_voice(self, ch: int, voice: Voice) -> None:
        u = self.units[ch // self.cpc]
        chip_ch = ch % self.cpc
        self._w(u, 0xB0 + chip_ch, voice.head & 0xFF)
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
        vs = self.vstates[ch]
        vs.vib_depth = voice.vib_depth
        trem = voice.op_tremolo()
        if self.lfo_depth_scale != 1.0:
            trem = tuple(min(127, int(t * self.lfo_depth_scale)) for t in trem)
        vs.trem = list(trem)

    def set_velocity(self, ch: int, level: int) -> None:
        vs = self.vstates[ch]
        voice = self.current_voice(ch)
        alg = voice.head & 7
        atten = 0x7F - (level & 0x7F)
        for i in range(4):
            base = voice.tl[i]
            v = (OPMASK[alg][i] & atten) + base
            vs.tl_base[i] = v & 0xFF
        self._write_tl(ch, vs.tl_base, force=True)

    def _write_tl(self, ch: int, values: Sequence[int], force: bool = False) -> None:
        vs = self.vstates[ch]
        key = tuple(int(v) & 0x7F for v in values)
        if not force and getattr(vs, "last_tl", None) == key:
            return
        vs.last_tl = key
        u = self.units[ch // self.cpc]
        chip_ch = ch % self.cpc
        for i in range(4):
            self._w(u, 0x40 + SLOT_OFFSET[i] + chip_ch, key[i])

    def current_voice(self, ch: int) -> Voice:
        prog = self.programs[self.slots[ch].midi_ch & 0x0F]
        if not self.voices:
            raise RuntimeError("音色库未装载")
        return self.voices[prog % len(self.voices)]

    def set_pitch(self, ch: int, note: int, bend255: int) -> None:
        note &= 0xFF
        bend255 &= 0xFF
        st = note % 12
        blk = note // 12
        fnum = FNUM_SEMITONE[st] + (BEND_DELTA[st] * bend255) // 0xFF
        word = (fnum & 0x7FF) | ((blk & 7) << 11)
        self._write_fnum(ch, word)

    def _write_fnum(self, ch: int, word: int, update_state: bool = True) -> None:
        u = self.units[ch // self.cpc]
        chip_ch = ch % self.cpc
        self._w(u, 0xA4 + chip_ch, (word >> 8) & 0xFF)
        self._w(u, 0xA0 + chip_ch, word & 0xFF)
        if update_state:
            self.vstates[ch].fnum_word = word

    def key_on(self, ch: int, note: int) -> None:
        vs = self.vstates[ch]
        voice = self.current_voice(ch)
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

    def drum(self, instrument: int, level: int) -> None:
        u = self.units[0]
        if instrument >= 0x20 or level >= 0x20:
            return
        if self.drum_atten_db != 0.0:
            scaled = level * 10 ** (-self.drum_atten_db / 20.0)
            level = max(0, min(0x1F, int(round(scaled))))
        self._w(u, 0x18 + instrument, 0xC0 | (level & 0x1F))
        self._w(u, 0x10, 1 << (instrument & 7))

    # ---------------------------------------------------------------- MIDI 事件
    def note_on(self, midi_ch: int, note: int, velocity: int) -> None:
        midi_ch &= 0x0F
        note &= 0x7F
        velocity &= 0x7F
        if midi_ch in (9, 15):
            if velocity:
                self._drum_note_on(velocity, note)
            return
        if velocity == 0:
            self.note_off(midi_ch, note)
            return

        # SSG 优先路径（ssg_first / manual）
        try_ssg_first = (self.ssg_mode == "ssg_first" or
                         (self.ssg_mode == "manual" and midi_ch in self.ssg_midi_channels))
        # === 1. SSG 优先 ===
        if self.pure_ssg:
            # 纯 SSG 模式：所有非鼓音符，直接交给 SSG，不管接不接得住
            self.ssg_note_on(midi_ch, note, velocity)
            return
        if try_ssg_first:
            if self.ssg_note_on(midi_ch, note, velocity):
                return

        # fm_first：FM 活跃音符数达到阈值时优先 SSG，而不是直接抢占 FM
        if self.ssg_mode == "fm_first":
            active_fm = sum(1 for s in self.slots if s.active)
            if active_fm >= self.fm_ssg_threshold:
                if self.ssg_note_on(midi_ch, note, velocity):
                    return

        # FM 路径
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
            return
        # SSG 通道一并处理（同一音符可能同时存在于 FM 和 SSG）
        self.ssg_note_off(midi_ch, note)
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
        for ch, slot in enumerate(self.slots):
            if not (slot.active and slot.midi_ch == midi_ch):
                continue
            if slot.note != 0:
                self.key_off(ch)
            slot.active = False
        if self.ssg_mode != "off":
            for slot in list(self.ssg_slots):
                if slot.active and slot.midi_ch == midi_ch and slot.note != 0:
                    self.ssg_note_off(midi_ch, slot.note)
        self.programs[midi_ch] = program

    def pitch_bend(self, midi_ch: int, lsb: int, msb: int) -> None:
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

        if self.ssg_mode != "off":
            for gch, slot in enumerate(self.ssg_slots):
                if not (slot.active and slot.midi_ch == midi_ch and slot.note != 0):
                    continue
                n2 = self._transpose(midi_ch, slot.note, self.programs[midi_ch])
                note_final = n2 + total // step - rng
                bend255 = ((total % step) * 255) // step
                note_float = note_final + bend255 / 255.0
                if self._ssg_note_in_range(int(note_float)):
                    self._write_ssg_pitch(gch, note_float)
                else:
                    clamped = max(24, min(127, int(note_float)))
                    self._write_ssg_pitch(gch, float(clamped))

    def control_change(self, midi_ch: int, cc: int, value: int) -> None:
        if cc < 0x7B:
            return
        for ch, slot in enumerate(self.slots):
            if slot.active:
                self.note_off(slot.midi_ch, slot.note)
        if self.ssg_mode != "off":
            for slot in list(self.ssg_slots):
                if slot.active:
                    self.note_off(slot.midi_ch, slot.note)

    # ---------------------------------------------------------------- 分配器
    def _transpose(self, midi_ch: int, note: int, program: int) -> int:
        if midi_ch in (9, 15) or program > 0x7F:
            return note
        v = (note + self.fmoctave[program]) & 0xFF
        return 0x7F if v > 0x7F else v

    def _find(self, midi_ch: int, note: int) -> int:
        for i, s in enumerate(self.slots):
            if s.midi_ch == midi_ch and s.note == note and s.active:
                return i
        return -1

    def _allocate(self, midi_ch: int, note: int) -> int:
        for i, s in enumerate(self.slots):
            if s.active and s.note == 0 and s.midi_ch == midi_ch:
                self.key_off(i)
                return i
        found = -1
        for i, s in enumerate(self.slots):
            if not s.active:
                found = i
                break
        if found < 0:
            for i, s in enumerate(self.slots):
                if s.active and s.midi_ch != midi_ch and s.note == 0:
                    found = i
                    break
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
            if self.slots[found].midi_ch < midi_ch:
                return -1
        s = self.slots[found]
        if s.active and s.note != 0:
            self.key_off(found)
        if not (s.active and s.midi_ch == midi_ch):
            self.load_voice(found, self.voices[self.programs[midi_ch] % len(self.voices)])
        return found

    # ---------------------------------------------------------------- tick
    def tick(self) -> None:
        self.global_tick = (self.global_tick + 1) & 0xFFFF
        if self.ssg_mode != "off":
            self._tick_ssg()
        if not self.lfo_enabled:
            return

        # 实机 LFO 推进由 Win95 MIDI 回调驱动（~120 Hz），不是 Timer-A（836.5 Hz）。
        # lfo_divisor=7 把两者对齐（836.5 / 7 ≈ 120）。
        # 包络状态机仍在每个 tick（836.5 Hz）推进，只有 LFO 步进被分频。
        do_lfo = (self.global_tick % self.lfo_divisor) == 0

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
                vs.lfo_counter = (vs.lfo_counter + 1) & 0xFFFF
                continue
            if st == 3:
                vs.env_state = (vs.env_state & ~3) | 1
                if voice.rate_a == 1:
                    vs.lfo_counter = 0
                    vs.lfo_value = 0x7FFF if voice.shape == 4 else 0
                    vs.lfo_dir = -1 if voice.shape == 4 else 1
                    vs.env_state = (vs.env_state & ~3) | 2
                elif voice.rate_a == 0:
                    vs.lfo_counter = self.global_tick
                    vs.lfo_value = self.global_tick
                    vs.env_state = (vs.env_state & ~3) | 2
                else:
                    # 修正 1：实机在计算完后会跳到 loc_206B 执行 inc word ptr [bx+19h]
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
                    continue

            if do_lfo:
                self._lfo_step(ch, voice, vs)

            vs.lfo_counter = (vs.lfo_counter + 1) & 0xFFFF

    def _lfo_step(self, ch: int, voice: Voice, vs: VoiceState) -> None:
        rate_word = max(1, int(voice.rate_word * self.lfo_rate_scale))
        period = 0xFFFF // rate_word
        shape = voice.shape
        overflow = False

        # 等效 tick：lfo_counter 每 836.5 Hz tick +1，但本函数每 120 Hz 才跑一次。
        # 为了让方波/噪声的相位分频与实机（836.5 Hz 上下文）一致，这里除以 lfo_divisor。
        eff_counter = vs.lfo_counter // self.lfo_divisor

        if shape == 1:
            phase = (eff_counter // max(1, period)) & 1
            vs.lfo_value = 0x7FFF if phase == 0 else (-32768 & 0xFFFF)
        elif shape == 3:
            step = period | 1
            if (eff_counter % step) == 0:
                # 修正 3：使用系统级全局随机状态，而不是 per-channel
                self.random_state = (self.random_state * 0x383) % 0x7FFF
            vs.lfo_value = self.random_state
        else:
            step = rate_word * vs.lfo_dir
            old = vs.lfo_value
            new = (old + step) & 0xFFFF
            if ((old ^ new) & 0x8000) != 0:
                overflow = True
            else:
                vs.lfo_value = new

            # 修正 2：严格区分 shape 4 和 shape 5
            if shape == 2 and overflow:
                vs.lfo_dir = -vs.lfo_dir
            elif shape == 0 and overflow:
                vs.lfo_value = (-vs.lfo_value) & 0xFFFF
            elif shape == 4:  # 实机 shape 4 才有裁掉负值的逻辑
                if self._s16(vs.lfo_value) < 0:
                    vs.lfo_value = 0
            # shape 5 什么都不做，允许负值通过（实机完全透明）

        word = vs.fnum_word
        fnum = word & 0x7FF
        if vs.vib_depth:
            lfo = self._s16(vs.lfo_value)
            mod = int((lfo * vs.vib_depth) / 0x7FFF)
            fnum = fnum + int((mod * fnum) / 0x7FF)
            if fnum & 0xF800:
                fnum = 0x7FF
            if getattr(vs, 'last_fnum_written', -1) != fnum:
                self._write_fnum(ch, (word & 0xF800) | fnum, update_state=False)
                vs.last_fnum_written = fnum

        # --- TL 计算：严格对照 tl_write seg3:0x0F92-0x0FC8 ---
        out = []
        lfo = self._s16(vs.lfo_value)
        for i in range(4):
            b = vs.tl_base[i] & 0xFF
            prod = vs.trem[i] * lfo
            t1 = prod // 0x7FFF if prod >= 0 else -((-prod) // 0x7FFF)
            t1 &= 0xFF
            t2 = (t1 * b) // 0x7F
            s = t2 + b
            if s > 0xFF:
                s = 0
            elif s > 0x7F:
                s = 0x7F
            out.append(s)
        self._write_tl(ch, out)

    @staticmethod
    def _s16(v: int) -> int:
        v &= 0xFFFF
        return v - 0x10000 if v >= 0x8000 else v

    def send_midi(self, status: int, d1: int = 0, d2: int = 0) -> None:
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