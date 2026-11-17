#!/usr/bin/python3
# -*- coding: utf-8 -*-
r"""NECOPN / OPNA 实时播放器 CLI。

    python rtplayer.py <song.mid>                    # 默认 fmgen 完整 OPNA
    python rtplayer.py <song.mid> --core nuked       # Nuked 双芯片（3+3）
    python rtplayer.py <song.mid> --core nuked1      # Nuked 单芯片（6 声道，快 1.85x）
    python rtplayer.py <song.mid> --rhythm-dir DIR   # 用户自备 RHY 采样
    python rtplayer.py <song.mid> --export out.wav
    python rtplayer.py <song.mid> --bench 20
    python rtplayer.py <song.mid> --clock 7987200    # 精确 PC-98 55466 Hz
    python rtplayer.py --list-devices

默认走 WASAPI、48 kHz、256 帧。芯片在原生速率（8 MHz/144 = 55555 Hz）渲染，
再重采样到 48 kHz。不打包任何节奏采样；缺 RHY 时自动退回 SSG 合成鼓。
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time

import numpy as np

from session import (BASE, Config, Session, default_rhythm_dir,
                     render_wav)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RHY = default_rhythm_dir()


def pick_device(rule):
    import sounddevice as sd
    devs = sd.query_devices()
    if rule is not None:
        try:
            idx = int(rule)
            return idx, devs[idx]
        except (ValueError, IndexError):
            pass
        for i, d in enumerate(devs):
            if d["max_output_channels"] > 0 and str(rule).lower() in d["name"].lower():
                return i, d
        raise SystemExit(f"找不到匹配 '{rule}' 的输出设备")

    default_name = ""
    try:
        default_name = sd.query_devices(sd.default.device[1])["name"]
    except Exception:
        pass
    for i, d in enumerate(devs):
        if (d["max_output_channels"] >= 2
                and sd.query_hostapis(d["hostapi"])["name"] == "Windows WASAPI"
                and default_name and default_name == d["name"]):
            return i, d
    for i, d in enumerate(devs):
        if (d["max_output_channels"] >= 2
                and sd.query_hostapis(d["hostapi"])["name"] == "Windows WASAPI"):
            return i, d
    idx = sd.default.device[1]
    return idx, devs[idx]


def _print_setup(s: Session, cfg: Config):
    print(f"MIDI:    {os.path.basename(cfg.midi)}  {s.n_events} 事件  "
          f"{s.total_sec:.2f}s")
    core = ("fmgen · 完整 OPNA（FM+SSG+ADPCM-B+节奏）" if s.core_name == "fmgen"
            else ("Nuked 单芯片 · 6 声道（FM+SSG+PCM 节奏）" if s.core_name == "nuked1"
                  else "Nuked OPN2 FM ×2 + emu2149 SSG + PCM 节奏"))
    print(f"芯片:    {core}  {s.renderer.sr} Hz → 重采样 → {cfg.sr_out} Hz")
    if s.rhythm_notes:
        kind = "fmgen 原生节奏" if s.core_name == "fmgen" else "PCM 节奏"
        print(f"节奏:    {kind} {s.rhythm_notes}/6  "
              f"({os.path.basename(cfg.rhythm_dir or '')})")
    elif cfg.ssg_only:
        print("节奏:    仅 SSG 合成鼓（--ssg-only，忽略 RHY 采样）")
    else:
        print("节奏:    没有 RHY 采样 → SSG 合成鼓兜底（程序不打包任何采样）")
    if s.core_name in ("nuked", "nuked1"):
        print(f"SSG:     emu2149 @{cfg.clock // 4 / 1e6:.3f} MHz / "
              f"{s.renderer.sr} Hz，增益 {cfg.ssg_gain}")
    else:
        print("SSG:     fmgen 内置 YM2149（主时钟/4）")
    if cfg.lfo:
        print(f"LFO:     开启  lfo_divisor={cfg.lfo_divisor}")
    else:
        print("LFO:     关闭（NECOPN 实机默认；--lfo 可开）")


def cmd_list_devices() -> int:
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0:
            api = sd.query_hostapis(d["hostapi"])["name"]
            print(f"[{i:2d}] {d['name']}  ch={d['max_output_channels']}  "
                  f"sr={d['default_samplerate']:.0f}  {api}")
    return 0


def cmd_bench(args) -> int:
    """离线端到端吞吐：驱动 + 2 芯片 + SSG + 节奏 + 重采样。"""
    cfg = Config(midi=args.midi, rhythm_dir=args.rhythm_dir, ssg_only=args.ssg_only,
                 core=args.core, clock=args.clock,
                 sr_out=args.sr, tail=0.0, lfo=args.lfo,
                 lfo_divisor=args.lfo_divisor, ssg_gain=args.ssg_gain,
                 rhythm_gain=args.rhythm_gain, volume=args.volume)
    s = Session(cfg)
    s.build()
    try:
        seconds = min(float(args.bench), max(1.0, s.total_sec + cfg.tail))
        n_out = int(seconds * cfg.sr_out)
        from rt_engine import Resampler
        res = Resampler(s.renderer.sr, cfg.sr_out)

        # 预热（首次调用有 ctypes / numpy 分配）
        s.renderer.render(512)

        done = 0
        t0 = time.perf_counter()
        peak = 0
        while done < n_out:
            blk = s.renderer.render(1024)
            if res is not None:
                blk = res.process(blk)
            if blk.shape[0]:
                done += blk.shape[0]
                peak = max(peak, int(np.abs(blk).max()))
        el = time.perf_counter() - t0
        x = seconds / el
        print(f"端到端（含重采样）: {el / seconds * 1000:.1f} ms/音频秒  "
              f"{x:.2f}x 实时")
        print(f"峰值: {peak}/32767  输出 {done / cfg.sr_out:.2f}s")

        # 生产者线程单独测 3 秒
        s.renderer.frame = 0
        s.renderer.ei = 0
        s.renderer.done = False
        s.renderer._dc_x1 = s.renderer._dc_y1 = 0.0
        from rt_engine import Engine
        eng = Engine(s.renderer, sr_out=cfg.sr_out, blocksize=cfg.blocksize,
                     target_blocks=6)
        eng.start()
        drained = 0
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 2.0:
            if eng.buffered_frames() >= 1024:
                eng.pull(1024)
                drained += 1024
            else:
                time.sleep(0.001)
        el = time.perf_counter() - t0
        produced = drained + eng.buffered_frames()
        print(f"生产者线程: {produced / cfg.sr_out / el:.2f}x 实时  "
              f"(缓冲 {eng.buffered_frames()} 帧)")
        eng.stop()
    finally:
        s.close()
    return 0


def cmd_export(args) -> int:
    cfg = Config(midi=args.midi, rhythm_dir=args.rhythm_dir, ssg_only=args.ssg_only,
                 core=args.core, clock=args.clock,
                 sr_out=args.sr, tail=args.tail, lfo=args.lfo,
                 lfo_divisor=args.lfo_divisor, ssg_gain=args.ssg_gain,
                 rhythm_gain=args.rhythm_gain, volume=args.volume)
    print(f"导出 {args.export}  ({args.export_sr} Hz)…")
    info = render_wav(cfg, args.export, sr_out=args.export_sr)
    print(f"完成: {info['frames']} 帧 = {info['seconds']:.2f}s  "
          f"peak={info['peak']}/32767  芯片 {info['chip_sr']} Hz  "
          f"节奏 {info['rhythm_notes']}/6")
    return 0


def cmd_play(args) -> int:
    import sounddevice as sd
    cfg = Config(midi=args.midi, rhythm_dir=args.rhythm_dir, ssg_only=args.ssg_only,
                 core=args.core, clock=args.clock,
                 blocksize=args.blocksize, sr_out=args.sr, tail=args.tail,
                 lfo=args.lfo, lfo_divisor=args.lfo_divisor,
                 ssg_gain=args.ssg_gain, rhythm_gain=args.rhythm_gain,
                 volume=args.volume, loop=args.loop)
    while True:
        s = Session(cfg)
        s.build()
        _print_setup(s, cfg)
        dev_idx, dev = pick_device(args.device)
        api = sd.query_hostapis(dev["hostapi"])["name"]
        print(f"设备:    [{dev_idx}] {dev['name']}  ({api})  "
              f"block={cfg.blocksize}")
        stop = threading.Event()
        try:
            s.start(dev_idx)
            print(f"延迟:    {s.stream.latency * 1000:.1f} ms   [Ctrl+C 停止]")
            t0 = time.time()
            last = 0.0
            while not s.finished:
                time.sleep(0.2)
                el = time.time() - t0
                if args.duration and el >= args.duration:
                    break
                if el - last >= 1.0:
                    last = el
                    print(f"  {s.position:6.1f}/{s.total_sec:.1f}s  "
                          f"缓冲 {s.engine.buffered_frames():4d} 帧  "
                          f"underrun={s.underruns}  xrun={s.xruns}")
        except KeyboardInterrupt:
            print("\n已停止")
        finally:
            print(f"共输出 {s.position:.2f}s / underrun={s.underruns} / "
                  f"xrun={s.xruns}")
            s.close()
        if not args.loop:
            return 0
        print("[循环] 从头再来…")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="NECOPN / OPNA 实时播放器")
    ap.add_argument("midi", nargs="?", help="标准 MIDI 文件")
    ap.add_argument("--core", default="fmgen", choices=["fmgen", "nuked", "nuked1"],
                    help="fmgen = 完整 OPNA（默认）；nuked = Nuked FM + SSG + PCM 节奏")
    ap.add_argument("--clock", type=int, default=8_000_000,
                    help="芯片主时钟：8 MHz → 55555 Hz；精确 PC-98 用 7987200 → 55466 Hz")
    ap.add_argument("--rhythm-dir", default=DEFAULT_RHY,
                    help="RHY 采样目录（不打包；缺省自动找 ./RHY）")
    ap.add_argument("--ssg-only", action="store_true",
                    help="忽略 RHY，全部用 SSG 合成鼓")
    ap.add_argument("--device", default=None,
                    help="输出设备索引或名字子串（默认自动选 WASAPI）")
    ap.add_argument("--blocksize", type=int, default=256)
    ap.add_argument("--sr", type=int, default=48_000)
    ap.add_argument("--duration", type=float, default=None)
    ap.add_argument("--tail", type=float, default=1.0)
    ap.add_argument("--volume", type=float, default=1.0)
    ap.add_argument("--ssg-gain", type=float, default=2.0)
    ap.add_argument("--rhythm-gain", type=float, default=1.0)
    ap.add_argument("--lfo", action="store_true", help="开启 NECOPN 软件 LFO")
    ap.add_argument("--lfo-divisor", type=int, default=7)
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--export", default=None, help="导出 WAV（不播放）")
    ap.add_argument("--export-sr", type=int, default=48_000)
    ap.add_argument("--bench", type=float, default=None,
                    help="离线跑 N 秒，报告端到端 ms/音频秒")
    ap.add_argument("--loop", action="store_true")
    args = ap.parse_args(argv)

    if args.list_devices:
        return cmd_list_devices()
    if not args.midi:
        ap.error("需要 MIDI 文件")
    if not os.path.exists(args.midi):
        print(f"找不到 MIDI: {args.midi}", file=sys.stderr)
        return 2
    if args.rhythm_dir and not os.path.isdir(args.rhythm_dir):
        print(f"警告: RHY 目录不存在（{args.rhythm_dir}），节奏通道会退回 SSG 鼓")
        args.rhythm_dir = ""
    if args.bench:
        return cmd_bench(args)
    if args.export:
        return cmd_export(args)
    return cmd_play(args)


if __name__ == "__main__":
    raise SystemExit(main())
