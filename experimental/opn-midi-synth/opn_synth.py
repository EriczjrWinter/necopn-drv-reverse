# -*- coding: utf-8 -*-
"""OPN MIDI 合成器 —— 3 FM + 3 SSG，6 个旋律声部，鼓组丢弃。

用法：
    python opn_synth.py song.mid -o out.wav
    python opn_synth.py song.mid --list-channels
    python opn_synth.py song.mid --ssg-channels 3,4,5 --ssg-atten-db 3
"""
from __future__ import annotations

import argparse
import os
import sys
import wave
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from fixed_bank import FMOCTAVE, VOICES_DATA  # noqa: E402
from necopn_base import TICK_HZ, Voice  # noqa: E402
from necopn_core import YmfmCore  # noqa: E402
from opn_driver import OpnSystem  # noqa: E402
from smf import parse_smf  # noqa: E402

DRUM_CHANNELS = (9, 15)


# ---------------------------------------------------------------- 通道分析
def analyze_channels(events, total_sec: float):
    """每通道：最大复音、音符数、平均时值/力度、CC7、program、bend 次数。"""
    stats = {ch: {
        "notes": 0, "max_poly": 0, "active": [], "durs": [],
        "vels": [], "cc7": [], "bends": 0, "program": 0,
    } for ch in range(16) if ch not in DRUM_CHANNELS}

    for e in events:
        ch = e.ch & 0x0F
        if ch in DRUM_CHANNELS or ch not in stats:
            continue
        st = stats[ch]
        if e.kind == "on":
            if e.d2 > 0:
                st["notes"] += 1
                st["vels"].append(e.d2)
                st["active"].append((e.d1, e.t))
                st["max_poly"] = max(st["max_poly"], len(st["active"]))
            else:
                _close(st, e.d1, e.t)
        elif e.kind == "off":
            _close(st, e.d1, e.t)
        elif e.kind == "cc" and e.d1 == 7:
            st["cc7"].append(e.d2)
        elif e.kind == "prog":
            st["program"] = e.d1
        elif e.kind == "bend":
            st["bends"] += 1

    out = {}
    for ch, st in stats.items():
        if st["notes"] == 0:
            continue
        n = st["notes"]
        out[ch] = {
            "notes": n,
            "max_poly": st["max_poly"],
            "avg_dur": (sum(st["durs"]) / len(st["durs"])) if st["durs"] else 0.0,
            "avg_vel": (sum(st["vels"]) / len(st["vels"])) if st["vels"] else 0.0,
            "avg_cc7": (sum(st["cc7"]) / len(st["cc7"])) if st["cc7"] else 127.0,
            "program": st["program"],
            "bends": st["bends"],
        }
    return out


def _close(st, note, t):
    for i, (n, t0) in enumerate(st["active"]):
        if n == note:
            st["durs"].append(max(0.0, t - t0))
            st["active"].pop(i)
            break


def score_channel(st) -> float:
    score = 0.0
    if st["max_poly"] <= 1:
        score += 35.0
    elif st["max_poly"] == 2:
        score += 10.0
    elif st["max_poly"] >= 4:
        score -= 25.0
    score += min(st["avg_dur"], 2.0) * 10.0
    score += (127.0 - st["avg_cc7"]) / 127.0 * 20.0
    score += (127.0 - st["avg_vel"]) / 127.0 * 5.0
    p = st["program"]
    if 32 <= p <= 39:
        score += 12.0
    elif p in (43, 44, 80, 81, 88, 89, 90, 91, 92, 93, 94, 95):
        score += 8.0
    elif 0 <= p <= 7 or p in (40, 41, 47, 48, 49, 50, 51,
                              56, 57, 58, 59, 60, 61,
                              72, 73, 74, 75, 76, 77, 78, 79):
        score -= 10.0
    score -= min(st["bends"], 30) * 1.5
    return score


def choose_ssg(stats, manual=None, limit: int = 3):
    if manual:
        return [ch for ch in manual if ch in stats][:limit]
    ranked = sorted(stats.keys(), key=lambda ch: -score_channel(stats[ch]))
    return ranked[:limit]


def print_channels(stats, plan):
    print(f"{'ch':>3} {'notes':>6} {'poly':>4} {'dur':>6} {'vel':>5} "
          f"{'cc7':>5} {'prog':>4} {'bend':>4}  group")
    for ch in sorted(stats):
        s = stats[ch]
        print(f"{ch:>3} {s['notes']:>6} {s['max_poly']:>4} "
              f"{s['avg_dur']:>6.2f} {s['avg_vel']:>5.0f} "
              f"{s['avg_cc7']:>5.0f} {s['program']:>4} {s['bends']:>4}  "
              f"{'SSG' if ch in plan else 'FM'}")


# ---------------------------------------------------------------- 渲染
def render(midi, out_path, clock=8_000_000, ssg_channels=(), ssg_atten_db=2.0,
           tail=1.0, chunk=512, duration=None, solo=None):
    events, _tpq, total_sec = parse_smf(open(midi, "rb").read())
    if duration:
        events = [e for e in events if e.t <= duration]
        total_sec = min(total_sec, float(duration))
    voices = [Voice.from_record(bytes(r)) for r in VOICES_DATA]

    core = YmfmCore(clock=clock)
    system = OpnSystem(core, clock=clock, ssg_channels=ssg_channels,
                       ssg_atten_db=ssg_atten_db,
                       mute_fm=(solo == "ssg"), mute_ssg=(solo == "fm"))
    system.load_bank(voices, FMOCTAVE)

    sr = core.sample_rate()
    total_frames = int((total_sec + tail) * sr)
    tick_iv = 1.0 / TICK_HZ
    next_tick = 0.0
    frames = 0
    ei = 0
    pcm = bytearray()

    while frames < total_frames:
        now = frames / sr
        while ei < len(events) and events[ei].t <= now:
            e = events[ei]
            system.time = e.t
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
            system.tick()
            next_tick += tick_iv

        next_boundary = int(next_tick * sr)
        if next_boundary <= frames:
            next_boundary = frames + 1
        n = min(next_boundary - frames, chunk, total_frames - frames)
        pcm += core.generate(n)
        frames += n

    with wave.open(out_path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)

    import numpy as np
    arr = np.frombuffer(pcm, dtype="<i2")
    peak = int(abs(arr).max()) if arr.size else 0
    rms = float((arr.astype("float64") ** 2).mean() ** 0.5) if arr.size else 0.0
    core.close()
    return {
        "path": out_path, "sr": sr, "frames": frames,
        "seconds": frames / sr, "peak": peak, "rms": rms,
        "total_sec": total_sec,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="OPN MIDI 合成器（3 FM + 3 SSG）")
    ap.add_argument("midi", help="SMF 文件")
    ap.add_argument("-o", "--out", default="out.wav")
    ap.add_argument("--ssg-channels", default="",
                    help="手动指定走 SSG 的 MIDI 通道，如 3,4,5；留空自动")
    ap.add_argument("--ssg-atten-db", type=float, default=2.0)
    ap.add_argument("--clock", type=int, default=8_000_000)
    ap.add_argument("--tail", type=float, default=1.0)
    ap.add_argument("--duration", type=float, default=None,
                    help="只渲染前 N 秒（试听/调试）")
    ap.add_argument("--solo", choices=("fm", "ssg"), default=None,
                    help="只留 FM 或只留 SSG，用来试听/调平衡")
    ap.add_argument("--list-channels", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.exists(args.midi):
        print(f"找不到 MIDI: {args.midi}", file=sys.stderr)
        return 2

    events, _tpq, total_sec = parse_smf(open(args.midi, "rb").read())
    stats = analyze_channels(events, total_sec)
    manual = []
    if args.ssg_channels.strip():
        manual = [int(x) for x in args.ssg_channels.replace(" ", "").split(",") if x]
    plan = choose_ssg(stats, manual)
    print_channels(stats, plan)
    print(f"\nSSG 通道: {plan if plan else '(无，全部 FM)'}   "
          f"FM 通道: {[ch for ch in stats if ch not in plan]}")
    print(f"鼓组通道 {DRUM_CHANNELS} 已丢弃；总时长 {total_sec:.1f}s")

    if args.list_channels:
        return 0

    info = render(args.midi, args.out, clock=args.clock,
                  ssg_channels=plan, ssg_atten_db=args.ssg_atten_db,
                  tail=args.tail, duration=args.duration, solo=args.solo)
    print(f"\nWAV -> {info['path']}  {info['sr']} Hz / {info['seconds']:.2f}s  "
          f"peak={info['peak']}/32767  rms={info['rms']:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
