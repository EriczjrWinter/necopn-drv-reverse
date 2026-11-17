# -*- coding: utf-8 -*-
r"""脱机自检 —— 不开声卡。

    python tool/selftest.py --midi D:\music\5.mid
    python tool/selftest.py --midi ... --limit 3 --rhythm-dir ...\RHY

覆盖：
  1. 三条原生 DLL 存在且能加载（opn2 / ssg / fmgen）
  2. 两个实时后端都能出声（fmgen / nuked）
  3. 驱动寄存器 trace 与 necopn-drv-reverse-main 的原版逐条一致
  4. fmgen 后端与离线 player.py 前 0.3s 逐字节一致（同一渲染链）
  5. 实时 Renderer 与块大小无关、事件落在精确采样点
  6. 重采样 55 555 → 48 000：长度正确且与分块方式无关
  7. Engine 生产者吞吐 >= 2x、按帧数结束
  8. 节奏：fmgen 原生 / Nuked PCM / 无 RHY 时 SSG 兜底，三条路都有输出
r"""
from __future__ import annotations

import argparse
import ctypes
import importlib.util
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from fixed_bank import FMOCTAVE, VOICES_DATA  # noqa: E402
from necopn_driver import TICK_HZ, NecopnSystem, Voice  # noqa: E402
from opn2_core import CoreMixer, NukedOpn2  # noqa: E402
from rt_engine import Engine, Renderer, Resampler, Ticker  # noqa: E402
from session import Config, Session, default_rhythm_dir  # noqa: E402
from smf import Event, parse_smf  # noqa: E402

BASE = os.path.join(HERE, "..", "necopn-drv-reverse-main")
FMGEN = os.path.join(HERE, "core", "necopna_fmgen.dll")

_ok = True
VOICES = [Voice.from_record(bytes(r)) for r in VOICES_DATA]


def check(name, cond, detail=""):
    global _ok
    if not cond:
        _ok = False
    print(f"[{'OK  ' if cond else 'FAIL'}] {name}{'  ' + detail if detail else ''}")


def skip(name, why=""):
    print(f"[SKIP] {name}{'  ' + why if why else ''}")


def load_events(midi, seconds):
    events, _tpq, total = parse_smf(open(midi, "rb").read())
    return [e for e in events if e.t <= seconds], min(total, seconds)


# ---------------------------------------------------------------------------
# 1/2. 核心加载与出声
# ---------------------------------------------------------------------------
def check_cores():
    for fn in ("opn2.dll", "ssg.dll", os.path.join("core", "necopna_fmgen.dll")):
        p = os.path.join(HERE, fn)
        check(f"文件 {fn}", os.path.exists(p), f"{p}")

    from necopn_core import YmfmCore
    c = YmfmCore(8_000_000, FMGEN)
    sr = c.sample_rate()
    buf = np.frombuffer(c.generate(2048), dtype="<i2").reshape(-1, 2)
    silent = int(np.abs(buf).max())
    for ch in range(3):
        w = lambda reg, val: c.write(0, reg, val)
        w(0xB0 + ch, 0)
        for i, off in enumerate((0x00, 0x08, 0x04, 0x0C)):
            s = off + ch
            w(0x30 + s, 0x01)
            w(0x40 + s, 0x20 if i < 3 else 0)
            w(0x50 + s, 0x1F)
            w(0x60 + s, 0x05)
            w(0x70 + s, 0x02)
            w(0x80 + s, 0x0A)
        w(0xA4 + ch, 0x2A)
        w(0xA0 + ch, 0x80)
        w(0x28, 0xF0 | ch)
    peak = int(np.abs(np.frombuffer(c.generate(4096), dtype="<i2")).max())
    c.close()
    check("fmgen 核心", sr == 55555 and silent == 0 and peak > 0,
          f"sr={sr} silence={silent} peak={peak}")

    n = NukedOpn2(8_000_000)
    buf = (ctypes.c_int16 * (2 * 4096))()
    n.generate_into(2048, buf)
    silent = max(abs(v) for v in buf)
    n.write(0, 0xB0, 0)
    for i, off in enumerate((0x00, 0x08, 0x04, 0x0C)):
        n.write(0, 0x30 + off, 0x01)
        n.write(0, 0x40 + off, 0x20 if i < 3 else 0)
        n.write(0, 0x50 + off, 0x1F)
        n.write(0, 0x60 + off, 0x05)
        n.write(0, 0x70 + off, 0x02)
        n.write(0, 0x80 + off, 0x0A)
    n.write(0, 0xA4, 0x2A)
    n.write(0, 0xA0, 0x80)
    n.write(0, 0x28, 0xF0)
    n.generate_into(4096, buf)
    peak = max(abs(v) for v in buf)
    n.close()
    check("Nuked OPN2 核心", n is not None and silent == 0 and peak > 0,
          f"sr=55555 silence={silent} peak={peak}")


# ---------------------------------------------------------------------------
# 3. 驱动 trace 与上游一致
# ---------------------------------------------------------------------------
class TraceCore:
    def __init__(self, chip, clock):
        self.chip = chip
        self.clock = clock          # [当前时间]，由测试在派发前更新
        self.writes = []

    def write(self, port, reg, val):
        self.writes.append((round(self.clock[0], 6), self.chip,
                            reg & 0xFF, val & 0xFF))


def load_base_driver():
    spec = importlib.util.spec_from_file_location(
        "base_necopn_driver", os.path.join(BASE, "necopn_driver.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    old = list(sys.path)
    sys.path.insert(0, BASE)
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = old
    return mod


def check_driver_trace(events, seconds):
    base_path = os.path.join(BASE, "necopn_driver.py")
    if not os.path.exists(base_path):
        skip("驱动寄存器 trace 与上游一致", "没有 ../necopn-drv-reverse-main")
        return
    base = load_base_driver()
    traces = []
    for mod in (base, sys.modules["necopn_driver"]):
        clk = [0.0]
        cores = [TraceCore(0, clk), TraceCore(1, clk)]
        sys_ = mod.NecopnSystem(cores, clock=8_000_000)
        sys_.load_bank(VOICES, FMOCTAVE)
        for e in events:
            sys_.time = e.t
            clk[0] = e.t
            if e.kind == "on":
                sys_.note_on(e.ch, e.d1, e.d2)
            elif e.kind == "off":
                sys_.note_off(e.ch, e.d1)
            elif e.kind == "cc":
                sys_.control_change(e.ch, e.d1, e.d2)
            elif e.kind == "prog":
                sys_.program_change(e.ch, e.d1)
            elif e.kind == "bend":
                sys_.pitch_bend(e.ch, e.d1, e.d2)
        traces.append(cores[0].writes + cores[1].writes)
    a, b = traces
    ok = len(a) == len(b) and all(x == y for x, y in zip(a, b))
    detail = f"{len(a)} vs {len(b)} 条写"
    if not ok:
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                detail += f"；首个不同 #{i}: {x} vs {y}"
                break
    check("驱动寄存器 trace 与上游一致", ok, detail)


# ---------------------------------------------------------------------------
# 4. fmgen 与离线渲染链一致
# ---------------------------------------------------------------------------
def check_offline_parity(midi, limit, rhythm_dir):
    from tool.test_fmgen_offline import offline, rt
    a = offline(midi, limit, rhythm_dir)
    b = rt(midi, limit, rhythm_dir)
    n = min(len(a), len(b))
    neq = np.any(a[:n] != b[:n], axis=1)
    first = int(np.argmax(neq)) if neq.any() else n
    seconds = first / 55555
    ra = float(np.sqrt(np.mean(a[:n].astype(np.float64) ** 2)))
    rb = float(np.sqrt(np.mean(b[:n].astype(np.float64) ** 2)))
    ok = (len(a) == len(b) and seconds >= 0.3
          and abs(ra - rb) / max(1.0, ra) < 0.05)
    check("fmgen 与离线 player.py 接线一致", ok,
          f"前缀 {seconds:.2f}s  RMS {ra:.1f}/{rb:.1f}")


# ---------------------------------------------------------------------------
# 5/6. Renderer 块无关 + 事件采样点 + 重采样
# ---------------------------------------------------------------------------
def render_session(cfg, seconds, block):
    s = Session(cfg)
    s.build()
    try:
        s.renderer.total_frames = int((seconds + cfg.tail) * s.renderer.sr)
        s.renderer.ei = 0
        out = []
        while not s.renderer.done:
            out.append(s.renderer.render(block))
        return np.concatenate(out)[:s.renderer.total_frames], s.renderer
    finally:
        s.close()


def check_renderer(midi, limit, rhythm_dir, core):
    cfg = Config(midi=midi, rhythm_dir=rhythm_dir, core=core, tail=0.0)
    a, r = render_session(cfg, limit, 137)
    b, _ = render_session(cfg, limit, 256)
    c, _ = render_session(cfg, limit, 512)
    same = np.array_equal(a, b) and np.array_equal(a, c)
    check(f"Renderer 块无关（{core}）", same, f"{a.shape}/{b.shape}/{c.shape}")

    events, _ = load_events(midi, limit)
    expect = [int(round(e.t * r.sr)) for e in events]
    got = [f for f, _k in r.dispatch_log]
    check(f"事件落在精确采样点（{core}）", got == expect,
          f"{len(got)}/{len(expect)}")


def check_resampler():
    sr_in, sr_out = 55555, 48000
    rng = np.random.default_rng(7)
    src = rng.integers(-20000, 20000, (30000, 2), dtype=np.int16)
    rs = Resampler(sr_in, sr_out)
    a = rs.process(src)
    rs2 = Resampler(sr_in, sr_out)
    parts = [rs2.process(src[i:i + 257]) for i in range(0, len(src), 257)]
    b = np.concatenate([p for p in parts if p.shape[0]])
    expect = len(src) * sr_out / sr_in
    check("重采样长度正确", abs(len(a) - expect) <= 3,
          f"{len(a)} vs {expect:.0f}")
    check("重采样与分块无关", len(a) == len(b) and np.array_equal(a, b),
          f"{len(a)} vs {len(b)}")


# ---------------------------------------------------------------------------
# 7. Engine 吞吐 + 长度
# ---------------------------------------------------------------------------
def check_engine(midi, limit, rhythm_dir, core):
    cfg = Config(midi=midi, rhythm_dir=rhythm_dir, core=core, tail=0.0)
    s = Session(cfg)
    s.build()
    try:
        s.renderer.total_frames = int(limit * s.renderer.sr)
        eng = Engine(s.renderer, sr_out=48000, blocksize=256, target_blocks=6)
        eng.start()
        drained = 0
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 1.0:
            if eng.buffered_frames() >= 1024:
                eng.pull(1024)
                drained += 1024
            else:
                time.sleep(0.001)
        el = time.perf_counter() - t0
        produced = drained + eng.buffered_frames()
        ratio = produced / 48000 / el
        check(f"Engine 生产者 >= 2x（{core}）", ratio >= 2.0,
              f"{ratio:.2f}x")

        max_blocks = int(eng.total_out / 256) + 200
        guard = 0
        while not eng.finished():
            eng.pull(256)
            guard += 1
            if guard > max_blocks:
                break
        check(f"Engine 按帧数结束（{core}）", eng.finished(),
              f"{eng.emitted}/{eng.total_out} 帧, guard={guard}")
        eng.stop()
    finally:
        s.close()


# ---------------------------------------------------------------------------
# 8. 节奏三条路径
# ---------------------------------------------------------------------------
def check_rhythm(midi, rhythm_dir):
    for core, has_rhy, ssg_only in (("fmgen", True, False), ("fmgen", False, False),
                                    ("nuked", True, False), ("nuked", False, False),
                                    ("nuked1", True, False), ("nuked1", False, False),
                                    ("fmgen", True, True), ("nuked", True, True),
                                    ("nuked1", True, True)):
        cfg = Config(midi=midi, rhythm_dir=(rhythm_dir if has_rhy else ""),
                     core=core, ssg_only=ssg_only, tail=0.0)
        s = Session(cfg)
        s.build()
        try:
            native = s.system.native_rhythm
            notes = s.rhythm_notes
            if has_rhy and not ssg_only and not os.path.isdir(rhythm_dir):
                skip(f"节奏 {core}/RHY", "RHY 目录不存在")
                continue
            # 清掉 MIDI 事件，只手动打一记底鼓，避免被旋律干扰
            s.renderer.ev = []
            s.renderer.ei = 0
            s.system.time = 0.0
            s.system.note_on(9, 35, 120)
            blk = s.renderer.render(4096)
            peak = int(np.abs(blk).max())
            want_notes = 6 if (has_rhy and not ssg_only) else 0
            if core == "fmgen":
                expect_native = has_rhy and not ssg_only
                ok = (native == expect_native) and (notes == want_notes) and peak > 0
            else:
                ok = (notes == want_notes) and peak > 0
            tag = f"{core}/{'RHY' if has_rhy else '无RHY'}"
            if ssg_only:
                tag += "/仅SSG"
            check(f"节奏 {tag}", ok,
                  f"native={native} notes={notes} peak={peak}")
        finally:
            s.close()


# ---------------------------------------------------------------------------
# 9. LFO tick 调度
# ---------------------------------------------------------------------------
def check_single_chip(midi, limit, rhythm_dir):
    """单芯片 6 声道映射：响度应和双芯片路径接近（±20%）。"""
    a, _ = render_session(Config(midi=midi, rhythm_dir=rhythm_dir,
                                 core="nuked", tail=0.0), limit, 512)
    b, _ = render_session(Config(midi=midi, rhythm_dir=rhythm_dir,
                                 core="nuked1", tail=0.0), limit, 512)
    ra = float(np.sqrt(np.mean(a.astype(np.float64) ** 2)))
    rb = float(np.sqrt(np.mean(b.astype(np.float64) ** 2)))
    ratio = rb / max(1.0, ra)
    check("单芯片 6 声道响度接近双芯片", 0.8 <= ratio <= 1.2,
          f"RMS {ra:.1f} vs {rb:.1f} ({ratio:.2f}x)")


def check_lfo(midi, limit, rhythm_dir):
    cfg = Config(midi=midi, rhythm_dir=rhythm_dir, core="fmgen",
                 tail=0.0, lfo=True)
    a, r = render_session(cfg, limit, 137)
    b, r2 = render_session(cfg, limit, 512)
    want = limit * TICK_HZ
    check("LFO tick 数量正确", abs(r.ticker.fired - want) <= 2,
          f"{r.ticker.fired} vs {want:.0f}")
    check("LFO 下输出仍与块大小无关", np.array_equal(a, b),
          f"{a.shape}/{b.shape}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--midi", default="", help="用作测试的 MIDI（必填）")
    ap.add_argument("--limit", type=float, default=3.0)
    ap.add_argument("--rhythm-dir", default=default_rhythm_dir(),
                    help="RHY 采样目录；没有就只测 SSG 兜底")
    ap.add_argument("--skip-offline", action="store_true")
    args = ap.parse_args(argv)
    if not args.midi:
        ap.error("需要 --midi（例如 --midi 5.mid）")
    if not os.path.exists(args.midi):
        ap.error(f"找不到 MIDI: {args.midi}")

    print(f"MIDI {args.midi}  前 {args.limit}s  RHY={args.rhythm_dir}\n")
    events, _ = load_events(args.midi, args.limit)

    check_cores()
    check_driver_trace(events, args.limit)
    if not args.skip_offline:
        check_offline_parity(args.midi, args.limit, args.rhythm_dir)
    check_renderer(args.midi, args.limit, args.rhythm_dir, "fmgen")
    check_renderer(args.midi, args.limit, args.rhythm_dir, "nuked")
    check_renderer(args.midi, args.limit, args.rhythm_dir, "nuked1")
    check_resampler()
    check_engine(args.midi, args.limit, args.rhythm_dir, "fmgen")
    check_engine(args.midi, args.limit, args.rhythm_dir, "nuked")
    check_engine(args.midi, args.limit, args.rhythm_dir, "nuked1")
    check_single_chip(args.midi, args.limit, args.rhythm_dir)
    check_rhythm(args.midi, args.rhythm_dir)
    check_lfo(args.midi, args.limit, args.rhythm_dir)

    print("\n" + ("全部通过" if _ok else "有失败项"))
    return 0 if _ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
