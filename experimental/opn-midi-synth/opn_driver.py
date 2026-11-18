# -*- coding: utf-8 -*-
"""OPN MIDI 合成器 —— 3 FM + 3 SSG，6 个统一旋律槽。

设计：
  - 只用一颗 OPN 等效核心（这里用 fmgen OPNA 的 ch0-2 + SSG，
    YM2203 的 FM 与 OPNA ch0-2 寄存器兼容）；
  - 鼓通道（MIDI ch9/15）直接丢弃，6 个声部全部给旋律；
  - slot 0-2 = FM，slot 3-5 = SSG；每个 MIDI 通道在加载时锁定到一组，
    分配器只在组内抢音，保证同一通道的音色不会 FM/SSG 横跳；
  - 组内抢音顺序沿用 NECOPN 的“大逃杀”：同通道释放槽 → 空闲槽 →
    最高 MIDI 通道中最老的槽；如果被抢的通道号比新音符低，就丢音。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from necopn_base import (
    BEND_DELTA, BEND_RANGE_DEFAULT, FNUM_SEMITONE, OPMASK, SLOT_OFFSET,
    TICK_HZ, TIMER_A, VELOCITY_TABLE, Voice, VoiceState,
)
from ssg_env import SSG_ENV


# ---------------------------------------------------------------- 槽位
@dataclass
class Slot:
    kind: str = "fm"          # fm / ssg
    note: int = 0
    midi_ch: int = 0
    active: bool = False
    serial: int = 0
    velocity: int = 0


@dataclass
class SsgState:
    level: float = 0.0          # 0..15 的包络音量
    target: float = 0.0         # 力度/衰减后的目标音量
    state: str = "idle"         # idle / attack / decay1 / decay2
    env: tuple = (14, 0x4D, 0x30, 15)
    d1_acc: int = 0
    d2_acc: int = 0
    note: float = 0.0


# MID2VGM：PSG 包络每 512/44100 秒推进一次（约 11.6 ms）。
PSG_TICK = 512.0 / 44100.0


class OpnSystem:
    """一颗 OPN（3 FM + 3 SSG）的 6 槽旋律合成器。"""

    def __init__(self, core, clock: int = 8_000_000,
                 ssg_channels: Sequence[int] = (),
                 ssg_atten_db: float = 2.0,
                 bend_range: int = BEND_RANGE_DEFAULT,
                 mute_fm: bool = False,
                 mute_ssg: bool = False):
        self.core = core
        self.clock = clock
        self.bend_range = bend_range
        self.ssg_atten_db = float(ssg_atten_db)
        self.mute_fm = bool(mute_fm)
        self.mute_ssg = bool(mute_ssg)

        self.n_ch = 6
        self.slots: List[Slot] = [
            Slot(kind="fm", midi_ch=0) for _ in range(3)
        ] + [
            Slot(kind="ssg", midi_ch=0) for _ in range(3)
        ]
        self.vstates: List[VoiceState] = [VoiceState() for _ in range(3)]
        self.ssg_states: List[SsgState] = [SsgState() for _ in range(3)]

        self.ch_group = {ch: ("ssg" if ch in set(ssg_channels) else "fm")
                         for ch in range(16)}
        self.programs = [0] * 16
        self.bend14 = [8192] * 16
        self.serial = 0
        self.voices: List[Voice] = []
        self.fmoctave = [0] * 128
        self.trace: Optional[List[tuple]] = None
        self.time = 0.0
        self.ssg_accum = 0.0

        self._ssg_tp_table = self._build_ssg_tp_table()
        self._init_hardware()

    # ---------------------------------------------------------------- 基础
    def _w(self, reg: int, val: int) -> None:
        if self.trace is not None:
            self.trace.append((self.time, reg & 0xFF, val & 0xFF))
        self.core.write(0, reg & 0xFF, val & 0xFF)

    @staticmethod
    def _s8(v: int) -> int:
        v &= 0xFF
        return v - 256 if v >= 0x80 else v

    def load_bank(self, voices: Sequence[Voice], fmoctave: Sequence[int]) -> None:
        self.voices = list(voices)
        self.fmoctave = [self._s8(v) for v in fmoctave]

    def _init_hardware(self) -> None:
        # FM：SSG-EG 全关、声像居中、Timer-A 照驱动设一遍
        for reg in range(0x90, 0x9F):
            if reg in (0x93, 0x97, 0x9B):
                continue
            self._w(reg, 0x00)
        for ch in range(3):
            self._w(0xB4 + ch, 0xC0)
        self._w(0x22, 0x00)          # LFO 关
        self._w(0x27, 0x30)
        self._w(0x26, TIMER_A & 0xFF)
        self._w(0x25, (TIMER_A >> 8) & 0x03)

        # SSG：R7 低有效，0x3F = 全关；硬件包络关
        self._w(0x07, 0x3F)
        for gch in range(3):
            self._w(0x08 + gch, 0x00)
            self._w(0x00 + gch * 2, 0x00)
            self._w(0x01 + gch * 2, 0x00)
        self._w(0x06, 0x1F)
        self._w(0x0B, 0x00)
        self._w(0x0C, 0x00)
        self._w(0x0D, 0x00)

    # ---------------------------------------------------------------- FM
    def current_voice(self, ch: int) -> Voice:
        prog = self.programs[self.slots[ch].midi_ch & 0x0F]
        return self.voices[prog % len(self.voices)]

    def _transpose(self, midi_ch: int, note: int, program: int) -> int:
        if midi_ch in (9, 15) or program > 0x7F:
            return note
        v = (note + self.fmoctave[program]) & 0xFF
        return 0x7F if v > 0x7F else v

    def load_voice(self, ch: int, voice: Voice) -> None:
        self._w(0xB0 + ch, voice.head & 0xFF)
        for i in range(4):
            s = SLOT_OFFSET[i]
            self._w(0x50 + s + ch, ((voice.ks[i] & 3) << 6) | (voice.ar[i] & 0x1F))
        for i in range(4):
            self._w(0x60 + SLOT_OFFSET[i] + ch, voice.dr[i] & 0x7F)
        for i in range(4):
            self._w(0x70 + SLOT_OFFSET[i] + ch, voice.sr[i] & 0x1F)
        for i in range(4):
            self._w(0x80 + SLOT_OFFSET[i] + ch,
                    ((voice.rr[i] & 0x0F) << 4) | (voice.sl[i] & 0x0F))
        for i in range(4):
            self._w(0x30 + SLOT_OFFSET[i] + ch,
                    ((voice.dt[i] & 7) << 4) | (voice.ml[i] & 0x0F))

        vs = self.vstates[ch]
        vs.vib_depth = voice.vib_depth
        vs.trem = list(voice.op_tremolo())
        vs.started = True
        vs.env_state = 0
        vs.key_alive = 0
        vs.lfo_counter = 0
        vs.lfo_value = 0
        vs.lfo_dir = 0
        vs.tl_base = [0, 0, 0, 0]
        vs.last_tl = None

    def set_velocity(self, ch: int, level: int) -> None:
        vs = self.vstates[ch]
        voice = self.current_voice(ch)
        alg = voice.head & 7
        atten = 0x7F - (level & 0x7F)
        for i in range(4):
            vs.tl_base[i] = ((OPMASK[alg][i] & atten) + voice.tl[i]) & 0xFF
        self._write_tl(ch, vs.tl_base, force=True)

    def _write_tl(self, ch: int, values, force: bool = False) -> None:
        vs = self.vstates[ch]
        key = tuple(int(v) & 0x7F for v in values)
        if not force and getattr(vs, "last_tl", None) == key:
            return
        vs.last_tl = key
        for i in range(4):
            self._w(0x40 + SLOT_OFFSET[i] + ch, key[i])

    def set_pitch(self, ch: int, note: int, bend255: int) -> None:
        note &= 0xFF
        bend255 &= 0xFF
        st = note % 12
        blk = note // 12
        fnum = FNUM_SEMITONE[st] + (BEND_DELTA[st] * bend255) // 0xFF
        word = (fnum & 0x7FF) | ((blk & 7) << 11)
        self._write_fnum(ch, word)

    def _write_fnum(self, ch: int, word: int) -> None:
        self._w(0xA4 + ch, (word >> 8) & 0xFF)
        self._w(0xA0 + ch, word & 0xFF)

    def key_on(self, ch: int, note: int) -> None:
        vs = self.vstates[ch]
        voice = self.current_voice(ch)
        self._w(0x28, ((voice.slotmask & 0x0F) << 4) | (ch & 7))
        vs.key_alive = 0xFF
        vs.started = True
        vs.env_state = 3

    def key_off(self, ch: int) -> None:
        vs = self.vstates[ch]
        self._w(0x28, ch & 7)
        vs.key_alive = 0

    # ---------------------------------------------------------------- SSG
    def _build_ssg_tp_table(self) -> List[int]:
        ssg_clock = self.clock / 4.0
        table = []
        for note in range(128):
            freq = 440.0 * 2 ** ((note - 69) / 12.0)
            table.append(int(round(ssg_clock / (16.0 * freq))) - 1)
        return table

    def _ssg_note(self, midi_ch: int, note: int) -> int:
        n = self._transpose(midi_ch, note, self.programs[midi_ch & 0x0F])
        while n < 128 and self._ssg_tp_table[n] > 0xFFF:
            n += 12
        return max(0, min(127, n))

    def _ssg_volume(self, level: int) -> float:
        lin = 15.0 * (level & 0x7F) / 127.0
        lin *= 10 ** (-self.ssg_atten_db / 20.0)
        return max(0.0, min(15.0, lin))

    def _write_ssg_volume(self, gch: int, vol: int) -> None:
        self._w(0x08 + gch, vol & 0x0F)

    def _write_ssg_pitch(self, gch: int, note_float: float) -> None:
        freq = 440.0 * 2 ** ((note_float - 69) / 12.0)
        tp = int(round(self.clock / 4.0 / (16.0 * freq))) - 1
        tp = max(0, min(0xFFF, tp))
        self._w(gch * 2, tp & 0xFF)
        self._w(gch * 2 + 1, (tp >> 8) & 0x0F)

    def _update_ssg_mixer(self) -> None:
        mask = 0
        for ch in range(3):
            if self.ssg_states[ch].state != "idle":
                mask |= (1 << ch)
        self._w(0x07, 0x38 | ((~mask) & 0x07))

    def _ssg_reg_vol(self, st: SsgState) -> int:
        return max(0, min(15, int(round(st.target * st.level / 15.0))))

    def _ssg_trigger(self, gch: int, midi_ch: int, note: int, velocity: int) -> None:
        n = self._ssg_note(midi_ch, note)
        level = VELOCITY_TABLE[velocity & 0x7F]
        env = SSG_ENV[self.programs[midi_ch & 0x0F] & 0x7F]
        target = self._ssg_volume(level)
        st = self.ssg_states[gch]
        st.env = env
        st.target = target
        st.level = float(env[0])
        st.state = "attack" if st.level < env[3] else "decay1"
        st.d1_acc = 0
        st.d2_acc = 0
        st.note = float(n)
        self._write_ssg_pitch(gch, float(n))
        self._write_ssg_volume(gch, self._ssg_reg_vol(st))
        self._update_ssg_mixer()

    def _ssg_release(self, gch: int) -> None:
        st = self.ssg_states[gch]
        if st.state != "idle":
            st.state = "decay2"
            st.d2_acc = 0

    def _tick_ssg(self) -> None:
        # MID2VGM 的 PSG 包络不是每个 Timer-A tick 都动，而是每 11.6 ms 一步。
        self.ssg_accum += 1.0 / TICK_HZ
        if self.ssg_accum < PSG_TICK:
            return
        self.ssg_accum -= PSG_TICK
        for gch in range(3):
            st = self.ssg_states[gch]
            if st.state == "idle":
                continue
            attack, d1, d2, param = st.env
            if st.state == "attack":
                st.level += 1
                if st.level >= param:
                    st.level = float(param)
                    st.state = "decay1"
                    st.d1_acc = 0
            elif st.state == "decay1":
                st.d1_acc += 1
                if st.d1_acc >= (d1 >> 4) + 1:
                    st.d1_acc = 0
                    st.level -= 1
                    sustain = d1 & 0x0F
                    if st.level <= sustain:
                        st.level = float(sustain)
                        st.state = "decay2"
                        st.d2_acc = 0
            elif st.state == "decay2":
                st.d2_acc += 1
                if st.d2_acc >= d2 + 1:
                    st.d2_acc = 0
                    st.level -= 1
                    if st.level <= 0:
                        st.level = 0.0
                        st.state = "idle"
                        self._update_ssg_mixer()
            self._write_ssg_volume(gch, self._ssg_reg_vol(st))

    # ---------------------------------------------------------------- 分配
    def _allocate(self, midi_ch: int) -> int:
        group = self.ch_group.get(midi_ch & 0x0F, "fm")
        idxs = range(0, 3) if group == "fm" else range(3, 6)

        # 1. 同通道的释放槽
        for i in idxs:
            s = self.slots[i]
            if not s.active and s.midi_ch == (midi_ch & 0x0F):
                return i
        # 2. 空闲槽
        for i in idxs:
            if not self.slots[i].active:
                return i
        # 3. NECOPN 大逃杀：最高 MIDI 通道里最老的槽
        best_midi = -1
        best_serial = 1 << 60
        found = -1
        for i in idxs:
            s = self.slots[i]
            if s.midi_ch > best_midi:
                best_midi = s.midi_ch
                best_serial = s.serial
                found = i
            elif s.midi_ch == best_midi and s.serial < best_serial:
                best_serial = s.serial
                found = i
        if found < 0 or best_midi < (midi_ch & 0x0F):
            return -1
        return found

    def _find(self, midi_ch: int, note: int) -> int:
        for i, s in enumerate(self.slots):
            if s.active and s.midi_ch == (midi_ch & 0x0F) and s.note == note:
                return i
        return -1

    def _stop_slot(self, ch: int) -> None:
        s = self.slots[ch]
        if ch < 3:
            self.key_off(ch)
        else:
            self._ssg_release(ch - 3)
        s.active = False
        s.note = 0
        self.serial += 1
        s.serial = self.serial

    # ---------------------------------------------------------------- MIDI
    def note_on(self, midi_ch: int, note: int, velocity: int) -> None:
        midi_ch &= 0x0F
        note &= 0x7F
        velocity &= 0x7F
        if midi_ch in (9, 15):
            return                      # 鼓组放弃
        if velocity == 0:
            self.note_off(midi_ch, note)
            return

        group = self.ch_group.get(midi_ch, "fm")
        if group == "fm" and self.mute_fm:
            return
        if group == "ssg" and self.mute_ssg:
            return

        ch = self._allocate(midi_ch)
        if ch < 0:
            return
        s = self.slots[ch]
        if s.active:
            self._stop_slot(ch)
        s.note = note
        s.midi_ch = midi_ch
        s.active = True
        s.velocity = velocity
        self.serial += 1
        s.serial = self.serial

        if ch < 3:
            prog = self.programs[midi_ch]
            voice = self.voices[prog % len(self.voices)]
            self.load_voice(ch, voice)
            level = VELOCITY_TABLE[velocity]
            self.set_velocity(ch, level)
            n2 = self._transpose(midi_ch, note, prog)
            if n2 < 0x5C:
                self.set_pitch(ch, n2, 0)
                self.key_on(ch, n2)
        else:
            self._ssg_trigger(ch - 3, midi_ch, note, velocity)

    def note_off(self, midi_ch: int, note: int) -> None:
        midi_ch &= 0x0F
        note &= 0x7F
        if midi_ch in (9, 15):
            return
        ch = self._find(midi_ch, note)
        if ch >= 0:
            self._stop_slot(ch)

    def program_change(self, midi_ch: int, program: int) -> None:
        midi_ch &= 0x0F
        if midi_ch in (9, 15):
            return
        self.programs[midi_ch] = program & 0x7F
        for i, s in enumerate(self.slots):
            if s.active and s.midi_ch == midi_ch:
                self._stop_slot(i)

    def pitch_bend(self, midi_ch: int, lsb: int, msb: int) -> None:
        midi_ch &= 0x0F
        if midi_ch in (9, 15):
            return
        bend14 = ((msb & 0x7F) << 7) | (lsb & 0x7F)
        self.bend14[midi_ch] = bend14
        rng = self.bend_range
        step = 8191 // rng + 1
        base = step * rng - 8192
        total = base + bend14
        for ch, s in enumerate(self.slots):
            if not s.active or s.midi_ch != midi_ch:
                continue
            n2 = self._transpose(midi_ch, s.note, self.programs[midi_ch])
            note_final = n2 + total // step - rng
            bend255 = ((total % step) * 255) // step
            if ch < 3:
                self.set_pitch(ch, note_final & 0xFF, bend255)
            else:
                self._write_ssg_pitch(ch - 3, note_final + bend255 / 255.0)

    def control_change(self, midi_ch: int, cc: int, value: int) -> None:
        midi_ch &= 0x0F
        if midi_ch in (9, 15):
            return
        if cc >= 123:
            for i, s in enumerate(self.slots):
                if s.active and s.midi_ch == midi_ch:
                    self._stop_slot(i)

    def tick(self) -> None:
        self._tick_ssg()

    # ---------------------------------------------------------------- 状态
    def active_slots(self) -> int:
        return sum(1 for s in self.slots if s.active)

    def group_of(self, midi_ch: int) -> str:
        return self.ch_group.get(midi_ch & 0x0F, "fm")
