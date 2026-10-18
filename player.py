# -*- coding: utf-8 -*-
"""NECOPN 播放器 CLI —— MIDI -> WAV / VGM / 寄存器 trace。

用法示例：
    python player.py song.mid -o song.wav                 # 优先使用内置硬编码音色库，无库时找 DRV
    python player.py song.mid -o song.vgm --vgm           # 寄存器流（foobar vgmplay 可播）
    python player.py song.mid --core null --trace t.txt   # 无 DLL 时只看寄存器

    python player.py song.mid -o song.wav --enable-lfo --lfo-jitter-us 6
        # 开启软件 LFO，同时注入 ±6 µs 的 tick 抖动（推荐用于追求实机听感）
"""
from __future__ import annotations

import argparse
import os
import random
import struct
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from necopn_core import ChipCore, NullCore, VgmTap, YmfmCore, make_core   # noqa: E402
from necopn_driver import NecopnSystem, Voice, TICK_HZ                     # noqa: E402
from smf import parse_smf                                                  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DRV = os.path.join(HERE, "NECOPN.DRV")

# 实机 8254 中断 → CPU 响应 → I/O 等待 + DRAM 刷新争用，典型在 2-6 µs 抖动
DEFAULT_LFO_JITTER_US = 6.0
DEFAULT_JITTER_SEED   = 0x1CE   # 固定种子保证可重复


# ------------------------------------------------------------------ 音色库读取
def load_bank_from_drv(drv_path: str):
    """从 NECOPN.DRV 直接读 FMPARA(128x64B) + FMOCTAVE(128B)。"""
    data = open(drv_path, "rb").read()
    bank = data[0x5510:0x5510 + 0x2000]
    oct_ = data[0x7510:0x7510 + 0x80]
    if len(bank) != 0x2000:
        raise ValueError("FMPARA 资源尺寸不对")
    voices = [Voice.from_record(bank[i * 0x40:(i + 1) * 0x40]) for i in range(128)]
    return voices, list(oct_)


def load_bank(drv_path: str):
    if os.path.exists(drv_path):
        voices, fmoct = load_bank_from_drv(drv_path)
        print(f"音色库: 128 条（从 {os.path.basename(drv_path)} 提取）")
        return voices, fmoct
    else:
        raise FileNotFoundError(f"找不到 NECOPN.DRV ({drv_path})")


# ------------------------------------------------------------------ 渲染
def _sdiv(v: int, n: int) -> int:
    """对称整数除法：正数向下取整，负数向上取整（向零对齐）。"""
    return v // n if v >= 0 else -((-v) // n)


def mix_cores(cores, nframes: int) -> bytes:
    if len(cores) == 1:
        return cores[0].generate(nframes)
    acc = [0] * (2 * nframes)
    for c in cores:
        buf = c.generate(nframes)
        s = struct.unpack("<%dh" % (2 * nframes), buf)
        for i, v in enumerate(s):
            acc[i] += v
    n = len(cores)
    out = struct.pack("<%dh" % (2 * nframes),
                      *(max(-32768, min(32767, _sdiv(v, n))) for v in acc))
    return out


def _sync_time(cores, t: float) -> None:
    for c in cores:
        f = getattr(c, "set_time", None)
        if f is not None:
            f(t)


def render(system: NecopnSystem, cores, events, total_sec: float, sr: int,
           tail: float = 1.0, chunk_cap: int = 512,
           jitter_us: float = 0.0,
           jitter_seed: int = DEFAULT_JITTER_SEED) -> bytes:
    """渲染 MIDI 事件流为 PCM。

    参数
    ----
    jitter_us : float
        tick 抖动的半幅，单位微秒。以 0 为中心、[-jitter_us, +jitter_us]
        均匀分布，加在 Timer-A 周期（约 1.1955 ms）上。

        - 0     : 关闭抖动（与 fmgen 内部时序 1:1，但 836.5 Hz LFO 阶跃
                  会留下可闻的边带 / 底噪）
        - 6.0   : 模拟实机 8254 中断 → CPU 响应 → I/O 等待 + DRAM 刷新
                  争用带来的自然抖动，能明显打散 LFO 边带
        - >20   : 能听见节拍抖动本身，不建议

    jitter_seed : int
        抖动 RNG 种子。固定种子可保证同一 MIDI 反复渲染得到相同结果。
    """
    total_frames = int((total_sec + tail) * sr)
    out = bytearray()
    frames = 0
    ei = 0
    tick_iv = 1.0 / TICK_HZ
    jitter_s = max(0.0, float(jitter_us)) * 1e-6
    rng = random.Random(jitter_seed) if jitter_s > 0.0 else None
    next_tick = 0.0

    while frames < total_frames:
        now = frames / sr

        # ---- MIDI 事件 ----
        while ei < len(events) and events[ei].t <= now:
            e = events[ei]
            system.time = e.t
            _sync_time(cores, e.t)
            if e.kind == "on": system.note_on(e.ch, e.d1, e.d2)
            elif e.kind == "off": system.note_off(e.ch, e.d1)
            elif e.kind == "cc": system.control_change(e.ch, e.d1, e.d2)
            elif e.kind == "prog": system.program_change(e.ch, e.d1)
            elif e.kind == "bend": system.pitch_bend(e.ch, e.d1, e.d2)
            ei += 1

        # ---- Timer-A tick（软件 LFO / 包络） ----
        while next_tick <= now:
            system.time = next_tick
            _sync_time(cores, next_tick)
            system.tick()

            # 硬件抖动。实机上 Timer-A 中断时刻不是理想的等间隔：
            #   · 8254 计数值固定，但 CPU 响应延迟受 I/O 等待状态影响
            #   · DRAM 刷新周期与中断服务时间窗偶尔重叠
            # 二者叠加成 ~2-6 µs 的抖动量，恰好能覆盖 LFO 阶跃在
            # 836.5 Hz 上的边带。这里以 0 为中心均匀采样。
            step = tick_iv
            if rng is not None:
                step += rng.uniform(-jitter_s, jitter_s)
                # 保险：绝不允许 tick 倒退或挤成一坨
                min_step = tick_iv * 0.25
                if step < min_step:
                    step = min_step
            next_tick += step

        _sync_time(cores, now)

        # ---- 混音：切到下一个 tick 或 chunk_cap 为止 ----
        next_boundary = int(next_tick * sr)
        if next_boundary <= frames:
            next_boundary = frames + 1
        n = min(next_boundary - frames, chunk_cap, total_frames - frames)
        out += mix_cores(cores, n)
        frames += n

    return bytes(out)


def write_wav(path: str, pcm: bytes, sr: int) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)


# ------------------------------------------------------------------ 主流程
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="NECOPN 驱动复现播放器")
    ap.add_argument("midi", help="标准 MIDI 文件")
    ap.add_argument("-o", "--out", help="输出文件 (.wav 或 .vgm)")
    ap.add_argument("--drv", default=DEFAULT_DRV, help="NECOPN.DRV 路径")
    ap.add_argument("--core", default="auto", choices=["auto", "fmgen", "null"])
    ap.add_argument("--clock", type=int, default=8_000_000)
    ap.add_argument("--enable-lfo", action="store_true",
                    help="开启软件 LFO（默认关闭）")
    ap.add_argument("--lfo-jitter-us", type=float, default=DEFAULT_LFO_JITTER_US,
                    help=f"tick 抖动半幅（微秒），0=关闭（默认 {DEFAULT_LFO_JITTER_US}）")
    ap.add_argument("--rhythm-rom", metavar="DIR", default=None,
                    help="原生打击乐 WAV 文件夹")
    ap.add_argument("--vgm", action="store_true")
    ap.add_argument("--trace", help="记录寄存器写到文本")
    ap.add_argument("--lfo-divisor", type=int, default=7,
                    help="LFO 推进分频。实机 Win95 MIDI 回调约 120 Hz，"
                         "836.5/120≈7。默认 7=实机；1=旧行为（16 Hz，太颤）")
    ap.add_argument("--lfo-rate-scale", type=float, default=1.0,
                    help="rate_word 缩放（一般用不到，优先 --lfo-divisor）")
    ap.add_argument("--lfo-depth-scale", type=float, default=1.0,
                    help="trem 深度缩放（0.5 减半 tremolo 幅度）")
    ap.add_argument("--tail", type=float, default=1.0)
    args = ap.parse_args(argv)

    # 1. 优先尝试加载硬编码的 fixed_bank.py（脱离版权文件，且最精准）
    try:
        from fixed_bank import VOICES_DATA, FMOCTAVE
        voices = [Voice.from_record(bytes(r)) for r in VOICES_DATA]
        fmoctave = list(FMOCTAVE)
        print("音色库: 128 条（使用内置硬编码数据 / 无需 NECOPN.DRV）")
    except ImportError:
        # 2. 如果没有 fixed_bank.py，退化到从 DRV 读取
        try:
            voices, fmoctave = load_bank(args.drv)
        except FileNotFoundError as e:
            print(f"错误: {e}", file=sys.stderr)
            print("请提供 NECOPN.DRV，或确保 fixed_bank.py 与 player.py 在同一目录下。",
                  file=sys.stderr)
            return 2

    data = open(args.midi, "rb").read()
    events, tpq, total_sec = parse_smf(data)
    print(f"MIDI: {len(events)} 事件，{total_sec:.2f}s，tpq={tpq}")

    n_cores = 2
    cores: list = []
    used = args.core
    for _ in range(n_cores):
        c, used = make_core(args.core, args.clock, rhythm_rom=args.rhythm_rom)
        cores.append(c)
    print(f"核心: {used} x{n_cores}（双芯片 OPNA）")

    want_vgm = args.vgm or (args.out or "").lower().endswith(".vgm")
    taps = [VgmTap(c, args.clock) for c in cores] if want_vgm else []
    sys_cores = taps or cores

    system = NecopnSystem(sys_cores, clock=args.clock,
                          lfo_enabled=args.enable_lfo,
                          lfo_divisor=args.lfo_divisor,
                          lfo_rate_scale=args.lfo_rate_scale,
                          lfo_depth_scale=args.lfo_depth_scale)
    system.load_bank(voices, fmoctave)
    if args.trace: system.trace = []

    sr = cores[0].sample_rate()

    # 抖动语义：仅在软件 LFO 开启时才有意义；关闭 LFO 时置 0 保证 1:1 可重复性
    jitter_us = args.lfo_jitter_us if args.enable_lfo else 0.0
    if args.enable_lfo:
        est_hz = TICK_HZ / args.lfo_divisor
        print(f"软件 LFO: 开启  lfo_divisor={args.lfo_divisor} "
              f"(LFO 推进频率 ≈ {est_hz:.0f} Hz)  "
              f"rate_scale={args.lfo_rate_scale:.2f} "
              f"depth_scale={args.lfo_depth_scale:.2f}")
    else:
        print("软件 LFO: 关闭（纯净模式）")

    pcm = render(system, sys_cores, events, total_sec, sr,
                 tail=args.tail,
                 jitter_us=jitter_us,
                 jitter_seed=DEFAULT_JITTER_SEED)

    if want_vgm:
        out_path = args.out or os.path.splitext(args.midi)[0] + ".vgm"
        blobs = [t.build(total_sec + args.tail) for t in taps]
        open(out_path, "wb").write(blobs[0])
        for i, b in enumerate(blobs[1:], 1):
            open(f"{out_path}.chip{i}", "wb").write(b)
        print(f"VGM -> {out_path} 及 .chip1")
    elif args.out:
        write_wav(args.out, pcm, sr)
        peak = max(abs(v) for v in struct.unpack("<%dh" % (len(pcm) // 2), pcm)) if pcm else 0
        print(f"WAV -> {args.out}  {sr}Hz  峰值 {peak}/32767")

    if args.trace and system.trace is not None:
        with open(args.trace, "w", encoding="utf-8") as f:
            for t, chip, reg, val in system.trace:
                f.write(f"{t:10.4f}  chip{chip}  {reg:02X} = {val:02X}\n")
        print(f"trace -> {args.trace}")

    if not args.out and not args.trace and not want_vgm:
        print("未指定输出（用 -o out.wav）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())