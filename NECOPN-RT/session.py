# -*- coding: utf-8 -*-
"""一次 OPNA 播放所需的全部组装：双 Nuked-OPN2 + SSG + 节奏 + 驱动 + 引擎。

CLI（rtplayer.py）和后续 GUI 共用这里，避免逻辑漂移。

硬规则（和 OPMMIDI-RT 一致）：**不打包任何 PCM/节奏采样**。
`RHY/` 目录由用户自备；缺失时自动退回 SSG 合成鼓，不会崩也不会静音整首歌。
"""
from __future__ import annotations

import json
import os
import sys
import wave
from dataclasses import asdict, dataclass

import numpy as np

from fixed_bank import FMOCTAVE, VOICES_DATA
from necopn_driver import TICK_HZ, NecopnSystem, Voice
from opn2_core import CoreMixer, NukedOpn2
from rhythm_core import RhythmCore
from rt_engine import Engine, Renderer, Resampler, Ticker
from smf import parse_smf
from ssg_core import SsgCore

CLOCK_DEFAULT = 8_000_000
SR_CHIP = CLOCK_DEFAULT // 144          # 55 555


def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def work_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


HERE = app_dir()
BASE = work_dir()
SEARCH_DIRS = [BASE] + ([HERE] if os.path.abspath(HERE) != os.path.abspath(BASE) else [])


def default_rhythm_dir() -> str:
    """找 RHY 目录：先项目内，再开发时同级的逆向项目（不随 EXE 分发）。"""
    for d in SEARCH_DIRS:
        cand = os.path.join(d, "RHY")
        if os.path.isdir(cand):
            return cand
    # 源码运行时 BASE 是项目根；打包后 BASE 是 EXE 所在目录（可能在 dist/）
    for rel in ("..", os.path.join("..", "..")):
        cand = os.path.abspath(os.path.join(
            BASE, rel, "necopn-drv-reverse-main", "RHY"))
        if os.path.isdir(cand):
            return cand
    return ""


@dataclass
class Config:
    midi: str = ""
    rhythm_dir: str = ""        # RHY/*.wav（用户自备，不打包）
    core: str = "fmgen"         # fmgen（完整 OPNA）| nuked（Nuked FM + SSG + PCM 节奏）
    clock: int = CLOCK_DEFAULT  # 8 MHz → 55555；精确 PC-98 用 7 987 200 → 55466
    device: int | None = None
    blocksize: int = 256
    sr_out: int = 48_000
    volume: float = 1.0
    tail: float = 1.0
    ssg_gain: float = 2.0
    rhythm_gain: float = 1.0
    lfo: bool = False
    lfo_divisor: int = 7
    loop: bool = False
    ssg_only: bool = False     # True = 忽略 RHY，全部用 SSG 合成鼓
    lang: str = "zh"

    @staticmethod
    def path() -> str:
        return os.path.join(BASE, "player_config.json")

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        p = cls.path()
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    for k, v in json.load(f).items():
                        if hasattr(cfg, k):
                            setattr(cfg, k, v)
            except (OSError, ValueError):
                pass
        if not cfg.rhythm_dir:
            cfg.rhythm_dir = default_rhythm_dir()
        return cfg

    def save(self) -> None:
        try:
            with open(self.path(), "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, ensure_ascii=False, indent=2)
        except OSError:
            pass


class Session:
    """组装好但还没出声的一次播放。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.core_name = cfg.core
        self.chips = []
        self.mixer: CoreMixer | None = None
        self.ssg = None
        self.rhythm: RhythmCore | None = None
        self.system: NecopnSystem | None = None
        self.renderer: Renderer | None = None
        self.engine: Engine | None = None
        self.ticker: Ticker | None = None
        self.total_sec = 0.0
        self.n_events = 0
        self.rhythm_notes = 0
        self._events = None
        self._offset = 0.0
        self.xruns = 0
        self.stream = None

    # ---------- 构建 ----------
    def build(self) -> None:
        cfg = self.cfg
        if not os.path.exists(cfg.midi):
            raise FileNotFoundError(f"找不到 MIDI: {cfg.midi}")

        events, tpq, total_sec = parse_smf(open(cfg.midi, "rb").read())
        self.total_sec = total_sec
        self.n_events = len(events)
        self._events = events
        self._offset = 0.0

        clock = int(cfg.clock or CLOCK_DEFAULT)
        sr = clock // 144
        voices = [Voice.from_record(bytes(r)) for r in VOICES_DATA]

        if cfg.core in ("nuked", "nuked1"):
            self._build_nuked(clock, sr, voices, single_chip=(cfg.core == "nuked1"))
            # 与 fmgen 路径的 /2 混音对齐：核心、SSG、节奏都按 0.5 缩放，
            # 这样切换 --core 时总响度不会差 6 dB。
            ssg_arg, pcm_arg = self.ssg, self.rhythm
            ssg_gain = cfg.ssg_gain * 0.5
            pcm_gain = cfg.rhythm_gain * 0.5
        else:
            self._build_fmgen(clock, sr, voices)
            ssg_arg, pcm_arg = self.ssg, None
            # SSG 兜底鼓现在统一走独立的 emu2149（fmgen 内置 SSG 在
            # 无 RHY 的兜底路径上对 generate 分块有轻微依赖）
            ssg_gain = cfg.ssg_gain * 0.5
            pcm_gain = cfg.rhythm_gain

        self.ticker = Ticker(TICK_HZ, self.system.tick) if cfg.lfo else None
        self.renderer = Renderer(
            self.mixer, self.system, events, total_sec, tail=cfg.tail,
            ticker=self.ticker, core_shift=0,
            ssg=ssg_arg, ssg_gain=ssg_gain,
            pcm=pcm_arg, pcm_gain=pcm_gain,
            master=cfg.volume)
        self.engine = Engine(self.renderer, sr_out=cfg.sr_out,
                             blocksize=cfg.blocksize, target_blocks=6)

    def _rhythm_dir(self) -> str:
        d = self.cfg.rhythm_dir
        return d if d and os.path.isdir(d) else ""

    def _build_fmgen(self, clock: int, sr: int, voices) -> None:
        """完整 OPNA：fmgen 内核自带 FM + SSG + ADPCM-B + 节奏。

        节奏采样通过 nopna_set_rhythm_rom 装进内核（用户自备 RHY 目录，不打包）；
        没有 RHY 时，驱动的 SSG 兜底鼓通过 SsgSink 写进 fmgen 自己的 SSG。
        """
        from necopn_core import YmfmCore

        dll = os.path.join(HERE, "core", "necopna_fmgen.dll")
        rhy = "" if self.cfg.ssg_only else self._rhythm_dir()
        self.chips = [YmfmCore(clock=clock, dll_path=dll, out_rate=sr,
                               rhythm_rom=(rhy or None)) for _ in range(2)]
        self.rhythm_notes = 6 if all(c.rhythm_loaded for c in self.chips) else 0
        self.mixer = CoreMixer(self.chips, divide=True)   # 与离线版 mix_cores 一致
        # 兜底 SSG 用独立 emu2149：fmgen 内置 SSG 对分块有轻微依赖，
        # 独立核和 nuked 路径一致，也让无 RHY 的自检块无关。
        self.ssg = SsgCore(clock=clock // 4, rate=sr)

        self.system = NecopnSystem(self.chips, clock=clock,
                                   lfo_enabled=self.cfg.lfo,
                                   lfo_divisor=self.cfg.lfo_divisor)
        self.system.load_bank(voices, FMOCTAVE)
        self.system.ssg = self.ssg
        self.system.ssg_drums = True
        self.system.native_rhythm = self.rhythm_notes > 0
        self.system.pcm = None
        self.core_name = "fmgen"

    def _build_nuked(self, clock: int, sr: int, voices,
                     single_chip: bool = False) -> None:
        """Nuked OPN2（FM）+ emu2149（SSG）+ RhythmCore（PCM 节奏）。

        single_chip=True 时只创建一颗 Nuked OPN2，用它的 6 个 FM 通道
        承载驱动逻辑的 2×3 声部（物理 ch3-5 走 bank 1），省掉第二颗芯片。
        """
        n_chips = 1 if single_chip else 2
        self.chips = [NukedOpn2(clock=clock) for _ in range(n_chips)]
        # 单芯片 6 声道 = 一颗芯片满电平；两颗芯片的旧路径按 /2 平均。
        # 单芯片再 ×0.5，保持和 fmgen / nuked 两条路径的总响度一致。
        self.mixer = CoreMixer(self.chips, divide=True,
                               gain=(0.55 if single_chip else 1.0))

        self.rhythm = RhythmCore(rate=sr, directory=self._rhythm_dir() or None) \
            if (self._rhythm_dir() and not self.cfg.ssg_only) else None
        if self.rhythm is not None:
            self.rhythm_notes = self.rhythm.loaded()
        self.ssg = SsgCore(clock=clock // 4, rate=sr)
        self.chips[0].ssg = self.ssg
        # 必须在 NecopnSystem 构造前挂上节奏，否则 _init_hardware 写的
        # 0x11=0x30 / 0x18+i=0xC4 不会进 RhythmCore，总电平会少 15 档（约 3.3 倍）
        if self.rhythm is not None and self.rhythm_notes:
            self.chips[0].rhythm = self.rhythm

        self.system = NecopnSystem(self.chips, clock=clock,
                                   lfo_enabled=self.cfg.lfo,
                                   lfo_divisor=self.cfg.lfo_divisor,
                                   single_chip=single_chip)
        self.system.load_bank(voices, FMOCTAVE)
        self.system.ssg = self.ssg
        self.system.ssg_drums = True
        self.system.pcm = self.rhythm
        self.system.native_rhythm = False
        self.core_name = "nuked1" if single_chip else "nuked"

    # ---------- 播放 ----------
    def start(self, device=None) -> None:
        import sounddevice as sd
        dev = device if device is not None else self.cfg.device
        self.stream = sd.OutputStream(device=dev, samplerate=self.cfg.sr_out,
                                      channels=2, dtype="int16",
                                      blocksize=self.cfg.blocksize,
                                      callback=self._callback)
        self.engine.start()
        import time
        t0 = time.time()
        while (self.engine.buffered_frames() < self.cfg.blocksize * 6
               and time.time() - t0 < 2.0):
            time.sleep(0.005)
        self.stream.start()

    def _callback(self, outdata, frames, time_info, status):
        if status:
            self.xruns += 1
        outdata[:] = self.engine.pull(frames)

    def stop(self) -> None:
        try:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
                self.stream = None
        except Exception:
            pass
        if self.engine is not None:
            self.engine.stop()

    def close(self) -> None:
        self.stop()
        for obj in (self.rhythm, self.ssg):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        for c in self.chips:
            try:
                c.close()
            except Exception:
                pass
        self.chips = []

    # ---------- 跳转 ----------
    def _event_index_at(self, frame: int) -> int:
        ev = self.renderer.ev
        lo, hi = 0, len(ev)
        while lo < hi:
            mid = (lo + hi) // 2
            if ev[mid][1] < frame:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def _replay_to(self, frame: int) -> None:
        """重建芯片后把 frame 之前的"状态类"事件与 tick 重放一遍，再把仍按住的音重新起音。

        OPNA 同样是有状态芯片（相位/包络/LFO），没法真的跳到中间。
        和 OPMMIDI-RT 的 seek 一样：恢复音高、音色、音量、弯音、按住哪些音，
        正在响的音重新起一次音。鼓（ch9/15）是一次性采样，不重新触发。
        """
        sys_ = self.system
        sr = self.renderer.sr
        held: dict[tuple[int, int], int] = {}
        order: list[tuple[int, int]] = []
        lfo = self.cfg.lfo
        tick_i = 0

        for e in self._events:
            if int(round(e.t * sr)) > frame:
                break
            sys_.time = e.t
            if e.kind == "prog":
                sys_.program_change(e.ch, e.d1)
            elif e.kind == "cc":
                sys_.control_change(e.ch, e.d1, e.d2)
            elif e.kind == "bend":
                sys_.pitch_bend(e.ch, e.d1, e.d2)
            elif e.ch not in (9, 15):
                if e.kind == "on":
                    key = (e.ch, e.d1)
                    if e.d2 > 0:
                        if key not in held:
                            order.append(key)
                        held[key] = e.d2
                    else:
                        held.pop(key, None)
                elif e.kind == "off":
                    held.pop((e.ch, e.d1), None)

            if lfo:
                t_end = min(e.t, frame / sr)
                while tick_i / TICK_HZ <= t_end:
                    sys_.time = tick_i / TICK_HZ
                    sys_.tick()
                    tick_i += 1

        if lfo:
            t_end = frame / sr
            while tick_i / TICK_HZ <= t_end:
                sys_.time = tick_i / TICK_HZ
                sys_.tick()
                tick_i += 1

        sys_.time = frame / sr
        for ch, note in order:
            if (ch, note) in held:
                sys_.note_on(ch, note, held[(ch, note)])

    def seek(self, seconds: float) -> None:
        if not self._events:
            return
        t = max(0.0, min(float(seconds), self.total_sec))
        playing = self.stream is not None
        self.stop()
        for c in self.chips:
            c.close()
        if self.ssg is not None:
            self.ssg.close()
        # 芯片状态全部重建
        self.build()
        frame = int(t * self.renderer.sr)
        self._replay_to(frame)
        self.renderer.frame = frame
        self.renderer.ei = self._event_index_at(frame)
        if self.ticker is not None:
            self.ticker.index = int(round(t * TICK_HZ))
            self.ticker.fired = self.ticker.index
        self.engine = Engine(self.renderer, sr_out=self.cfg.sr_out,
                             blocksize=self.cfg.blocksize, target_blocks=6)
        self._offset = t
        if playing:
            self.start()

    # ---------- 状态 ----------
    @property
    def finished(self) -> bool:
        return self.engine is not None and self.engine.finished()

    @property
    def position(self) -> float:
        if not self.engine:
            return self._offset
        return self._offset + self.engine.emitted / self.cfg.sr_out

    @property
    def underruns(self) -> int:
        return self.engine.underruns if self.engine else 0


# ---------------------------------------------------------------------------
# 离线导出：与实时播放同一条渲染链
# ---------------------------------------------------------------------------
def render_wav(cfg: Config, out_path: str, sr_out: int | None = None,
               progress=None, cancelled=None) -> dict:
    s = Session(cfg)
    s.build()
    try:
        sr_out = int(sr_out or cfg.sr_out or 48_000)
        chip_sr = s.renderer.sr
        res = Resampler(chip_sr, sr_out) if sr_out != chip_sr else None
        total = max(1, s.renderer.total_frames)
        peak = 0
        written = 0
        with wave.open(out_path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(sr_out)
            while not s.renderer.done:
                if cancelled is not None and cancelled():
                    break
                blk = s.renderer.render(512)
                if res is not None:
                    blk = res.process(blk)
                if blk.shape[0]:
                    w.writeframes(blk.tobytes())
                    written += blk.shape[0]
                    peak = max(peak, int(np.abs(blk).max()))
                if progress is not None:
                    progress(min(1.0, s.renderer.frame / total))
        return {
            "path": out_path,
            "sr": sr_out,
            "frames": written,
            "seconds": written / sr_out,
            "peak": peak,
            "chip_sr": chip_sr,
            "complete": bool(s.renderer.done),
            "rhythm_notes": s.rhythm_notes,
        }
    finally:
        s.close()
