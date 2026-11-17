# -*- coding: utf-8 -*-
"""实时播放引擎：采样级事件调度 + 55 555 → 48 000 重采样 + 生产者线程。

从 OPMMIDI-RT 的 rt_engine.py 移植，针对 OPNA 改了三处：
  1. Renderer 支持 **Ticker**：NECOPN 的软件 LFO / 包络状态机跑在
     Timer-A 的 836.55 Hz tick 上，必须和 MIDI 事件一样按采样点调度。
  2. `core_shift`：OPM 路径习惯把核心输出 >>1 留 -6 dB 余量；OPN2 的
     6 通道输出已经不高，OPNA 路径用 core_shift=0。
  3. `ssg` / `pcm` 的采样率必须与核心一致（55 555），不再假设 55 930。

刻意不依赖 sounddevice —— 这一层可以脱机自检，设备只是消费者。
"""
from __future__ import annotations

import ctypes
import threading
import time

import numpy as np

SR_OUT_DEFAULT = 48000


# --------------------------------------------------------------------------
# 0. Ticker：固定频率的芯片/驱动 tick
# --------------------------------------------------------------------------
class Ticker:
    """每 1/hz 秒触发一次回调（NECOPN 的 Timer-A = 836.55 Hz）。

    tick 的采样点用 round(start + i/hz) 计算，和 MIDI 事件一样精确。
    """

    def __init__(self, hz: float, fn, start_time: float = 0.0):
        self.hz = float(hz)
        self.fn = fn
        self.start = float(start_time)
        self.index = 0
        self.fired = 0

    def frame(self, sr: int) -> int:
        # 与离线 player.py 的 int(next_tick * sr) 对齐（向下取整）
        return int((self.start + self.index / self.hz) * sr)

    def fire(self) -> None:
        self.fn()
        self.index += 1
        self.fired += 1

    def skip_to_time(self, seconds: float) -> None:
        """跳到某个时刻（不触发回调）。seek 时若不想重放 tick 用这个。"""
        self.index = max(0, int(round((seconds - self.start) * self.hz)))

    def reset(self) -> None:
        self.index = 0
        self.fired = 0


# --------------------------------------------------------------------------
# 1. 渲染：把核心 + 驱动包成"给帧数就出 int16 块"
# --------------------------------------------------------------------------
class Renderer:
    """事件按**采样点**对齐，而不是像 player.py 那样按块边界派发。"""

    MAX_CHUNK = 8192

    def __init__(self, core, system, events, total_sec, tail=1.0, quantize=False,
                 ssg=None, ssg_gain=2.0, ssg_dc_block=True, pcm=None, pcm_gain=1.0,
                 master=1.0, ticker=None, core_shift=0):
        self.core = core
        self.system = system
        self.ssg = ssg
        self.ssg_gain = ssg_gain
        self.ssg_dc_block = ssg_dc_block
        self.pcm = pcm
        self.pcm_gain = pcm_gain
        self.master = master
        self.ticker = ticker
        self.core_shift = int(core_shift)
        self._dc_a = 0.999                  # 拐点 ≈ 8.9 Hz @55 555
        self._dc_x1 = 0.0
        self._dc_y1 = 0.0
        self.sr = core.sample_rate()
        self.total_frames = int((total_sec + tail) * self.sr)
        self.quantize = quantize

        self.ev = [(e.t, int(round(e.t * self.sr)), e.kind, e.ch, e.d1, e.d2)
                   for e in events]
        self.ei = 0
        self.frame = 0
        self.done = False
        self.dispatch_log = []

        self._buf = (ctypes.c_int16 * (2 * self.MAX_CHUNK))()
        self._ssg_buf = (ctypes.c_int16 * self.MAX_CHUNK)() if ssg is not None else None

    # -- 事件派发 --
    def _dispatch(self, e):
        _t, _f, kind, ch, d1, d2 = e
        self.dispatch_log.append((self.frame, kind))
        self.system.time = _t
        if kind == "on":
            self.system.note_on(ch, d1, d2)
        elif kind == "off":
            self.system.note_off(ch, d1)
        elif kind == "cc":
            self.system.control_change(ch, d1, d2)
        elif kind == "prog":
            self.system.program_change(ch, d1)
        elif kind == "bend":
            self.system.pitch_bend(ch, d1, d2)

    def _dc_filter(self, x):
        """一阶隔直（模拟 YM2608 SSG 输出耦合电容）。"""
        n = x.shape[0]
        if n == 0:
            return x
        a = self._dc_a
        k = np.arange(n, dtype=np.float64)
        b = np.diff(x.astype(np.float64), prepend=self._dc_x1)
        cs = np.cumsum(b * np.power(a, -k))
        y = np.power(a, k + 1) * self._dc_y1 + np.power(a, k) * cs
        self._dc_x1 = float(x[-1])
        self._dc_y1 = float(y[-1])
        return y

    def _chunk(self, n):
        self.core.generate_into(n, self._buf)
        core = np.frombuffer(self._buf, dtype="<i2", count=2 * n).reshape(n, 2)
        if self.ssg is None and self.pcm is None and self.master == 1.0:
            if self.core_shift:
                return core >> self.core_shift
            return core.copy()

        scale = 0.5 if self.core_shift else 1.0
        mix = core.astype(np.float32) * scale
        if self.ssg is not None:
            self.ssg.generate_into(n, self._ssg_buf)
            ssg = np.frombuffer(self._ssg_buf, dtype="<i2", count=n)
            ssg_f = self._dc_filter(ssg) if self.ssg_dc_block else ssg.astype(np.float32)
            mix += (ssg_f * self.ssg_gain)[:, None]
        if self.pcm is not None:
            mix += (self.pcm.generate(n).astype(np.float32) * self.pcm_gain)[:, None]
        if self.master != 1.0:
            mix *= np.float32(self.master)
        np.clip(mix, -32768.0, 32767.0, out=mix)
        return mix.astype(np.int16)

    def render(self, n):
        """产出 n 帧 int16 (n,2)。超过总时长后返回静音并把 done 置位。"""
        out = np.zeros((n, 2), np.int16)
        if self.frame >= self.total_frames:
            self.done = True
            return out

        n_real = min(n, self.total_frames - self.frame)
        pos = 0
        while pos < n_real:
            if self.quantize:
                while self.ei < len(self.ev) and self.ev[self.ei][0] <= self.frame / self.sr:
                    self._dispatch(self.ev[self.ei])
                    self.ei += 1
                if self.ticker is not None:
                    while self.ticker.frame(self.sr) <= self.frame:
                        self.ticker.fire()
                take = n_real - pos
                if self.ticker is not None:
                    take = min(take, self.ticker.frame(self.sr) - self.frame)
                take = max(1, min(take, self.MAX_CHUNK))
            else:
                while self.ei < len(self.ev) and self.ev[self.ei][1] <= self.frame:
                    self._dispatch(self.ev[self.ei])
                    self.ei += 1
                # tick 也按采样点派发；和离线 player.py 一样，事件先、tick 后
                while self.ticker is not None and self.ticker.frame(self.sr) <= self.frame:
                    self.ticker.fire()

                take = n_real - pos
                if self.ei < len(self.ev):
                    take = min(take, self.ev[self.ei][1] - self.frame)
                if self.ticker is not None:
                    take = min(take, self.ticker.frame(self.sr) - self.frame)
                take = max(1, min(take, self.MAX_CHUNK))

            out[pos:pos + take] = self._chunk(take)
            self.frame += take
            pos += take

        if self.frame >= self.total_frames:
            self.done = True
        return out


# --------------------------------------------------------------------------
# 2. 重采样：55 555 → 48 000，4 点 Catmull-Rom 插值
# --------------------------------------------------------------------------
class Resampler:
    """定点比例重采样。线性插值在 20 kHz 附近会有明显滚降，这里用 4 点三次插值。"""

    def __init__(self, sr_in, sr_out):
        self.step = sr_in / sr_out
        self.buf = np.zeros((1, 2), np.float32)
        self.pos = 1.0

    def process(self, block):
        b = np.asarray(block, dtype=np.float32)
        if b.shape[0]:
            self.buf = np.concatenate((self.buf, b))

        n = self.buf.shape[0]
        if n < 4 or self.pos + 2 >= n:
            return np.zeros((0, 2), np.int16)

        m = int(np.floor((n - 3 - self.pos) / self.step)) + 1
        if m <= 0:
            return np.zeros((0, 2), np.int16)

        p = self.pos + self.step * np.arange(m, dtype=np.float64)
        i = p.astype(np.int64)
        t = (p - i).astype(np.float32)[:, None]

        x0 = self.buf[i - 1]
        x1 = self.buf[i]
        x2 = self.buf[i + 1]
        x3 = self.buf[i + 2]

        c0 = x1
        c1 = 0.5 * (x2 - x0)
        c2 = x0 - 2.5 * x1 + 2.0 * x2 - 0.5 * x3
        c3 = -0.5 * x0 + 1.5 * x1 - 1.5 * x2 + 0.5 * x3
        y = ((c3 * t + c2) * t + c1) * t + c0

        self.pos += self.step * m
        drop = int(np.floor(self.pos)) - 1
        if drop > 0:
            self.buf = self.buf[drop:]
            self.pos -= drop

        return np.clip(y, -32768, 32767).astype(np.int16)


# --------------------------------------------------------------------------
# 3. 生产者线程 + 环形缓冲
# --------------------------------------------------------------------------
class Engine:
    """一个线程不停往缓冲里灌 48 kHz 块，音频回调只负责拷数据。"""

    CHIP_CHUNK = 512

    def __init__(self, renderer, sr_out=SR_OUT_DEFAULT, blocksize=256, target_blocks=4):
        self.r = renderer
        self.res = Resampler(renderer.sr, sr_out)
        self.sr_out = sr_out
        self.blocksize = blocksize
        self.target = blocksize * target_blocks

        # 结束条件用**帧数**判断，不看缓冲水位
        remaining = max(0, renderer.total_frames - renderer.frame)
        self.total_out = int(round(remaining * sr_out / renderer.sr))
        self.emitted = 0
        self.producer_done = False

        self._acc = np.zeros((0, 2), np.int16)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

        self.underruns = 0

    def start(self):
        self._thread = threading.Thread(target=self._run, name="opna-producer", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def _run(self):
        while not self._stop.is_set():
            if self.producer_done:
                return
            with self._lock:
                starving = self._acc.shape[0] < self.target
            if not starving:
                time.sleep(0.002)
                continue

            chip = self.r.render(self.CHIP_CHUNK)
            out = self.res.process(chip)

            with self._lock:
                if out.shape[0]:
                    self._acc = np.concatenate((self._acc, out))
                if self.r.done:
                    self.producer_done = True

            if out.shape[0] == 0 and self.r.done:
                return

    def pull(self, n):
        with self._lock:
            if self._acc.shape[0] >= n:
                blk = self._acc[:n]
                self._acc = self._acc[n:]
                self.emitted += n
                return blk
            have = self._acc.shape[0]
            blk = np.zeros((n, 2), np.int16)
            if have:
                blk[:have] = self._acc
                self._acc = np.zeros((0, 2), np.int16)
            # 只统计"流还没结束时"的欠载；播完之后的补零不算 underrun
            if self.emitted + have < self.total_out:
                self.underruns += 1
            self.emitted += n
            return blk

    def finished(self):
        return self.emitted >= self.total_out

    def buffered_frames(self):
        with self._lock:
            return self._acc.shape[0]
