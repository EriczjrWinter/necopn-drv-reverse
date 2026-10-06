# -*- coding: utf-8 -*-
"""NECOPN 播放器 CLI —— MIDI -> WAV / VGM / 寄存器 trace。

用法示例：
    python player.py song.mid -o song.wav                 # 用 ymfm 核心出 WAV
    python player.py song.mid -o song.vgm --vgm           # 寄存器流（foobar vgmplay 可播）
    python player.py song.mid --cores 3 --mode full       # "假设满通道" 3 核 18 声部
    python player.py song.mid --core null --trace t.txt   # 无 DLL 时只看寄存器
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from necopn_core import ChipCore, NullCore, VgmTap, YmfmCore, make_core   # noqa: E402
from necopn_driver import NecopnSystem, Voice, TICK_HZ                     # noqa: E402
from smf import parse_smf                                                  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DRV = os.path.join(HERE, "NECOPN.DRV")


# ------------------------------------------------------------------ 音色库
def load_bank(drv_path: str):
    """从 NECOPN.DRV 直接读 FMPARA(128x64B) + FMOCTAVE(128B)。"""
    data = open(drv_path, "rb").read()
    bank = data[0x5510:0x5510 + 0x2000]
    oct_ = data[0x7510:0x7510 + 0x80]
    if len(bank) != 0x2000:
        raise ValueError("FMPARA 资源尺寸不对")
    voices = [Voice.from_record(bank[i * 0x40:(i + 1) * 0x40]) for i in range(128)]
    return voices, list(oct_)


# ------------------------------------------------------------------ 渲染
def mix_cores(cores, nframes: int) -> bytes:
    if len(cores) == 1:
        return cores[0].generate(nframes)
    acc = [0] * (2 * nframes)
    for c in cores:
        buf = c.generate(nframes)
        s = struct.unpack("<%dh" % (2 * nframes), buf)
        for i, v in enumerate(s):
            acc[i] += v
    out = struct.pack("<%dh" % (2 * nframes),
                      *(max(-32768, min(32767, v)) for v in acc))
    return out


def _sync_time(cores, t: float) -> None:
    """把带时间轴的核心（VgmTap）定位到 t，保证寄存器写的时间戳正确。"""
    for c in cores:
        f = getattr(c, "set_time", None)
        if f is not None:
            f(t)


def render(system: NecopnSystem, cores, events, total_sec: float, sr: int,
           tail: float = 1.0, chunk: int = 256):
    total_frames = int((total_sec + tail) * sr)
    out = bytearray()
    frames = 0
    ei = 0
    tick_iv = 1.0 / TICK_HZ
    next_tick = 0.0
    while frames < total_frames:
        now = frames / sr
        while ei < len(events) and events[ei].t <= now:
            e = events[ei]
            system.time = e.t
            _sync_time(cores, e.t)
            if e.kind == "on":
                system.note_on(e.ch, e.d1, e.d2)
            elif e.kind == "off":
                system.note_off(e.ch, e.d1)
            elif e.kind == "cc":
                system.control_change(e.ch, e.d1, e.d2)
            elif e.kind == "prog":
                system.program_change(e.ch, e.d1)
            elif e.kind == "bend":
                system.pitch_bend(e.ch, e.d1, e.d2)
            ei += 1
        while next_tick <= now:
            system.time = next_tick
            _sync_time(cores, next_tick)
            system.tick()
            next_tick += tick_iv
        _sync_time(cores, now)
        n = min(chunk, total_frames - frames)
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
    ap.add_argument("-o", "--out", help="输出文件（.wav 或 .vgm）")
    ap.add_argument("--drv", default=DEFAULT_DRV, help="NECOPN.DRV 路径")
    ap.add_argument("--core", default="auto", choices=["auto", "ymfm", "null"],
                    help="模拟核心（默认 auto：优先 ymfm）")
    ap.add_argument("--mode", default="necopn", choices=["necopn", "full"],
                    help="necopn=真驱动 2 核 x3 声部；full=满通道（每核 6 声部）")
    ap.add_argument("--cores", type=int, default=0,
                    help="核心数量（0=按 mode 自动：necopn 2 颗 / full 3 颗）")
    ap.add_argument("--clock", type=int, default=8_000_000, help="OPNA 时钟")
    ap.add_argument("--no-lfo", action="store_true", help="关闭软件包络/LFO（对照用）")
    ap.add_argument("--rhythm-rom", metavar="FILE", default=None,
                    help="原生 YM2608 打击乐 ROM（8192 字节 dump）；不指定用内置合成")
    ap.add_argument("--vgm", action="store_true", help="输出 VGM（等价于 -o *.vgm）")
    ap.add_argument("--trace", help="把寄存器写记录成文本")
    ap.add_argument("--tail", type=float, default=1.0, help="尾音秒数")
    args = ap.parse_args(argv)

    if not os.path.exists(args.drv):
        print(f"找不到驱动文件: {args.drv}", file=sys.stderr)
        return 2
    voices, fmoctave = load_bank(args.drv)
    print(f"音色库: 128 条（{os.path.basename(args.drv)}）；"
          f"FMOCTAVE 移调范围 {min(fmoctave)}..{max(fmoctave)}")

    data = open(args.midi, "rb").read()
    events, tpq, total_sec = parse_smf(data)
    print(f"MIDI: {len(events)} 事件，{total_sec:.2f}s，tpq={tpq}")

    n_cores = args.cores or (2 if args.mode == "necopn" else 3)
    cores: list = []
    used = args.core
    for _ in range(n_cores):
        c, used = make_core(args.core, args.clock, rhythm_rom=args.rhythm_rom)
        cores.append(c)
    print(f"核心: {used} x{n_cores}（mode={args.mode}）")
    if used == "ymfm":
        print(f"打击乐 ROM: {args.rhythm_rom if args.rhythm_rom else '内置合成 8KB'}")

    want_vgm = args.vgm or (args.out or "").lower().endswith(".vgm")
    taps = [VgmTap(c, args.clock) for c in cores] if want_vgm else []
    sys_cores = taps or cores          # 让驱动的寄存器写经过 VGM 记录器

    system = NecopnSystem(sys_cores, mode=args.mode, clock=args.clock,
                          lfo_enabled=not args.no_lfo)
    system.load_bank(voices, fmoctave)
    if args.trace:
        system.trace = []

    sr = cores[0].sample_rate()
    pcm = render(system, sys_cores, events, total_sec, sr, tail=args.tail)

    if want_vgm:
        out_path = args.out or os.path.splitext(args.midi)[0] + ".vgm"
        blobs = [t.build(total_sec + args.tail) for t in taps]
        if len(blobs) == 1:
            open(out_path, "wb").write(blobs[0])
        else:
            open(out_path, "wb").write(blobs[0])   # 多核心：每颗芯片一个 VGM
            for i, b in enumerate(blobs[1:], 1):
                open(f"{out_path}.chip{i}", "wb").write(b)
        print(f"VGM -> {out_path}（{len(blobs)} 个文件，每颗芯片一个）")
    elif args.out:
        write_wav(args.out, pcm, sr)
        peak = max(abs(v) for v in struct.unpack("<%dh" % (len(pcm) // 2), pcm)) if pcm else 0
        print(f"WAV -> {args.out}  {sr}Hz  峰值 {peak}/32767")

    if args.trace and system.trace is not None:
        with open(args.trace, "w", encoding="utf-8") as f:
            for t, chip, reg, val in system.trace:
                f.write(f"{t:10.4f}  chip{chip}  {reg:02X} = {val:02X}\n")
        print(f"trace -> {args.trace}（{len(system.trace)} 次寄存器写）")

    if not args.out and not args.trace and not want_vgm:
        print("未指定输出（用 -o out.wav 或 --trace t.txt）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
