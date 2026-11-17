# -*- coding: utf-8 -*-
r"""回归：fmgen 后端应与离线 player.py 的渲染链一致。

离线 player.py 按 Timer-A tick 边界切块（还会出现 1 帧的块），实时 Renderer
是采样级/块无关的，两者在"事件正好落在 tick 边界"时会差 1 帧。所以这里检查：
  - 前 0.3 秒逐字节一致（证明核心/驱动/混音/节奏接线完全正确）
  - 总长度一致、整体 RMS 差 < 5%
  - 峰值差 < 5%

    python tool/test_fmgen_offline.py --midi D:\music\5.mid --limit 5
r"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import player_offline  # noqa: E402
from fixed_bank import FMOCTAVE, VOICES_DATA  # noqa: E402
from necopn_core import YmfmCore  # noqa: E402
from necopn_driver import TICK_HZ, NecopnSystem, Voice  # noqa: E402
from rt_engine import Renderer, Ticker  # noqa: E402
from session import Config, Session, default_rhythm_dir  # noqa: E402
from smf import parse_smf  # noqa: E402

FMGEN = os.path.join(HERE, "core", "necopna_fmgen.dll")
VOICES = [Voice.from_record(bytes(r)) for r in VOICES_DATA]


def offline(midi, seconds, rhythm_dir, tail=0.2):
    events, _tpq, _total = parse_smf(open(midi, "rb").read())
    events = [e for e in events if e.t <= seconds]
    rhy = rhythm_dir if (rhythm_dir and os.path.isdir(rhythm_dir)) else None
    cores = [YmfmCore(8_000_000, FMGEN, rhythm_rom=rhy) for _ in range(2)]
    system = NecopnSystem(cores, clock=8_000_000)
    system.load_bank(VOICES, FMOCTAVE)
    sr = cores[0].sample_rate()
    data = player_offline.render(system, cores, events, seconds, sr, tail=tail)
    for c in cores:
        c.close()
    return np.frombuffer(data, dtype="<i2").reshape(-1, 2)


def rt(midi, seconds, rhythm_dir, tail=0.2):
    rhy = rhythm_dir if (rhythm_dir and os.path.isdir(rhythm_dir)) else ""
    cfg = Config(midi=midi, rhythm_dir=rhy, core="fmgen", tail=tail)
    s = Session(cfg)
    s.build()
    try:
        # 用 Session 组装好的核心/驱动，但换一个 quantize + ticker 的 Renderer，
        # 复刻离线 player.py 的块边界（Timer-A 每 66 帧左右切一刀）。
        r = Renderer(s.mixer, s.system, s._events, seconds, tail=tail,
                     quantize=True, ticker=Ticker(TICK_HZ, s.system.tick),
                     core_shift=0, ssg=None, pcm=None)
        out = []
        while not r.done:
            out.append(r.render(512))
        return np.concatenate(out)[:r.total_frames]
    finally:
        s.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--midi", default="", help="用作测试的 MIDI（必填）")
    ap.add_argument("--limit", type=float, default=5.0)
    ap.add_argument("--rhythm-dir", default=default_rhythm_dir())
    args = ap.parse_args(argv)
    if not args.midi:
        ap.error("需要 --midi")
    if not os.path.exists(args.midi):
        ap.error(f"找不到 MIDI: {args.midi}")
    a = offline(args.midi, args.limit, args.rhythm_dir)
    b = rt(args.midi, args.limit, args.rhythm_dir)
    n = min(len(a), len(b))
    same = np.array_equal(a[:n], b[:n])
    diff = np.abs(a[:n].astype(np.int32) - b[:n].astype(np.int32))
    neq = np.any(a[:n] != b[:n], axis=1)
    first = int(np.argmax(neq)) if neq.any() else n
    seconds = first / 55555
    ra = float(np.sqrt(np.mean(a[:n].astype(np.float64) ** 2)))
    rb = float(np.sqrt(np.mean(b[:n].astype(np.float64) ** 2)))
    pa, pb = int(np.abs(a).max()), int(np.abs(b).max())
    ok = (len(a) == len(b) and seconds >= 0.3
          and abs(ra - rb) / max(1.0, ra) < 0.05
          and abs(pa - pb) / max(1, pa) < 0.05)
    print(f"offline {a.shape}  rt {b.shape}  n={n}")
    print(f"逐字节一致前缀: {seconds:.2f}s  "
          f"（之后最大差 {int(diff.max()) if n else 0}）")
    print(f"peak  {pa} / {pb}   RMS {ra:.1f} / {rb:.1f}")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
