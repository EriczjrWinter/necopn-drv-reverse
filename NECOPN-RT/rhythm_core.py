# -*- coding: utf-8 -*-
"""YM2608 节奏通道（ADPCM-A 的 PCM 替代实现）。

**为什么不是真正的 ADPCM-A LLE？**
Nuked-OPN2 只覆盖 FM（YM3438）；YM2608 的 ADPCM-A 节奏通道是另一块硬件。
真做 LLE 需要 8 KB 的 YM2608 节奏 ROM（Yamaha 版权，不能随程序分发），
而且六件乐器的 ROM 数据解码出来就是本项目 `RHY/` 里那六个 WAV
（离线版 fmgen 也是直接读这六个 WAV）。所以这里做的是：

    YM2608 节奏寄存器接口（0x10 / 0x11 / 0x18-0x1D）
        → 触发用户自备的 6 个 WAV 采样
        → 按硬件语义做电平 / 声像 / 单次播放

听感与 fmgen 的原生节奏通道一致（同一批采样），代价是没有 ADPCM 的
量化噪声和 ROM 寻址细节。采样版权归原厂，**程序不打包**；
目录缺失时节奏静音，可退回 SSG 合成鼓（见 `necopn_driver.py`）。

寄存器语义（对照 fmgen fmgen_opna.cpp）：
    0x10     bits0-5 = 六件乐器的 key on
    0x11     bits0-5 = 总电平（取反）
    0x18+i   bits0-4 = 乐器电平（取反），bits6-7 = 声像（1=L, 2=R, 3=both）
"""
from __future__ import annotations

import os
import wave

import numpy as np

# YM2608 节奏通道的六件乐器（顺序 = 寄存器 0x18+index）
RHYTHM_NAMES = (
    "2608_bd",    # 0 Bass Drum
    "2608_sd",    # 1 Snare Drum
    "2608_top",   # 2 Top Cymbal
    "2608_hh",    # 3 Hi-Hat
    "2608_tom",   # 4 Tom
    "2608_rim",   # 5 Rim Shot
)

# 电平步进：fmgen 的 tltable 每个单位 = 0.75 dB；总电平同单位
LEVEL_DB_PER_STEP = 0.75
# 总增益：1.0 时与 fmgen 原生节奏通道的实测电平一致（单次 BD / vel 100）
MASTER_GAIN = 1.0


class RhythmCore:
    name = "ym2608-rhythm"

    def __init__(self, rate: int = 55_555, directory: str | None = None,
                 master_gain: float = MASTER_GAIN):
        self.rate = rate
        self.master_gain = master_gain
        self.samples: list[np.ndarray | None] = [None] * 6
        self.levels = [31] * 6
        self.pans = [3] * 6
        self.pos = [0] * 6
        self.playing = [False] * 6
        self.total_level = 0
        self.notes = 0
        self.triggers = [0] * 6
        self.frames_played = [0] * 6
        self.directory = directory or ""
        if directory:
            self.notes = self.load_dir(directory)

    # ---------- 加载 ----------
    @staticmethod
    def _read_wav(path: str) -> np.ndarray | None:
        try:
            with wave.open(path, "rb") as w:
                nch = w.getnchannels()
                sw = w.getsampwidth()
                sr = w.getframerate()
                raw = w.readframes(w.getnframes())
        except (OSError, wave.Error):
            return None
        if sw == 1:
            data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) * 256.0
        elif sw == 2:
            data = np.frombuffer(raw, dtype="<i2").astype(np.float64)
        elif sw == 3:
            b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
            v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
            v = np.where(v & 0x800000, v - 0x1000000, v)
            data = v.astype(np.float64) / 256.0
        elif sw == 4:
            data = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 65536.0
        else:
            return None
        if nch == 2:
            data = data.reshape(-1, 2).mean(axis=1)
        elif nch > 2:
            data = data.reshape(-1, nch).mean(axis=1)
        if data.size == 0:
            return None
        return data, sr

    def load_dir(self, directory: str) -> int:
        """加载 6 个 WAV；返回成功加载的件数（0-6）。文件名大小写不敏感。"""
        if not os.path.isdir(directory):
            return 0
        files = {}
        for fn in os.listdir(directory):
            low = fn.lower()
            if low.endswith(".wav"):
                files[low[:-4]] = os.path.join(directory, fn)
        n = 0
        for i, base in enumerate(RHYTHM_NAMES):
            path = files.get(base)
            if path is None and base == "2608_rim":
                path = files.get("2608_rym")     # 某些 dump 把 rim 写成 rym
            if path is None:
                continue
            r = self._read_wav(path)
            if r is None:
                continue
            data, sr = r
            if sr != self.rate and data.size > 1:
                n_out = max(2, int(round(data.size * self.rate / sr)))
                data = np.interp(np.arange(n_out) * (sr / self.rate),
                                 np.arange(data.size), data)
            self.samples[i] = np.clip(data, -32768, 32767).astype(np.float32)
            n += 1
        self.directory = directory
        self.notes = n
        return n

    def loaded(self) -> int:
        return sum(1 for s in self.samples if s is not None)

    # ---------- 寄存器 ----------
    def write(self, reg: int, val: int) -> None:
        reg &= 0xFF
        val &= 0xFF
        if reg == 0x10:
            for i in range(6):
                if val & (1 << i):
                    self.trigger(i)
        elif reg == 0x11:
            self.total_level = (~val) & 0x3F
        elif 0x18 <= reg <= 0x1D:
            i = reg - 0x18
            self.levels[i] = (~val) & 0x1F
            self.pans[i] = (val >> 6) & 3

    def trigger(self, index: int) -> None:
        if 0 <= index < 6 and self.samples[index] is not None:
            self.pos[index] = 0
            self.playing[index] = True
            self.triggers[index] += 1

    # ---------- 渲染 ----------
    def _gain(self, index: int) -> float:
        # 0x11 / 0x18+ 写入的都是**衰减码**（0 = 最响，31 = 最轻），
        # 和 fmgen 的 rhythmtl / level 一致，直接相加。
        att = (self.total_level + self.levels[index]) * LEVEL_DB_PER_STEP
        return self.master_gain * (10.0 ** (-att / 20.0))

    def generate(self, nframes: int) -> np.ndarray:
        """返回 (nframes,) int16 单声道；Renderer 会把它居中叠加。"""
        acc = np.zeros(nframes, dtype=np.float32)
        any_playing = False
        for i in range(6):
            if not self.playing[i]:
                continue
            data = self.samples[i]
            if data is None:
                self.playing[i] = False
                continue
            pos = self.pos[i]
            take = min(nframes, data.size - pos)
            if take > 0:
                g = self._gain(i)
                g *= {0: 0.0, 1: 1.0, 2: 1.0, 3: 1.0}[self.pans[i]]
                if self.pans[i] == 1:
                    acc[:take] += data[pos:pos + take] * g          # 左
                elif self.pans[i] == 2:
                    acc[:take] += data[pos:pos + take] * g          # 右（PCM 单声道，
                                                                    # 由 Renderer 居中；
                                                                    # 声像细节留给后续）
                else:
                    acc[:take] += data[pos:pos + take] * g
                self.pos[i] = pos + take
                self.frames_played[i] += take
            if self.pos[i] >= data.size:
                self.playing[i] = False
            else:
                any_playing = True
        if not any_playing and not acc.any():
            return np.zeros(nframes, dtype=np.int16)
        np.clip(acc, -32768.0, 32767.0, out=acc)
        return acc.astype(np.int16)

    def active(self) -> int:
        return sum(1 for p in self.playing if p)

    def close(self) -> None:
        self.playing = [False] * 6
        self.samples = [None] * 6
