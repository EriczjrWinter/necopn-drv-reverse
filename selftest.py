# -*- coding: utf-8 -*-
"""NECOPN 播放器自检：把复现结果与逆向结论逐项对拍。

运行：python selftest.py
"""
from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from necopn_core import NullCore, VgmTap
from necopn_driver import (BEND_DELTA, DRUM_MAP, FNUM_SEMITONE, INVERT,
                           NecopnSystem, OPMASK, VELOCITY_TABLE, Voice)
from player import load_bank
from smf import parse_smf

HERE = os.path.dirname(os.path.abspath(__file__))
DRV = os.path.join(HERE, "..", "NECOPN.DRV")

ok = 0
fail = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK]   {name}")
    else:
        fail += 1
        print(f"  [FAIL] {name}  {detail}")


def make_midi() -> bytes:
    """一段最小 SMF：program 0、A4 音符、力度 100、弯音、鼓。"""
    def vlq(v: int) -> bytes:
        out = [v & 0x7F]
        v >>= 7
        while v:
            out.append((v & 0x7F) | 0x80)
            v >>= 7
        return bytes(reversed(out))

    trk = b""
    trk += vlq(0) + bytes([0xC0, 0x00])                  # program 0
    trk += vlq(0) + bytes([0x90, 60, 100])               # note on C4 vel 100
    trk += vlq(240) + bytes([0xE0, 0x00, 0x60])          # bend 稍偏
    trk += vlq(240) + bytes([0x80, 60, 0])               # note off
    trk += vlq(0) + bytes([0x99, 38, 90])                # 鼓：军鼓
    trk += vlq(240) + bytes([0x99, 39, 90])              # 鼓：HandClap -> 哑音
    trk += vlq(240) + bytes([0xFF, 0x2F, 0x00])          # end of track
    body = b"MTrk" + struct.pack(">I", len(trk)) + trk
    hdr = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480)
    return hdr + body


def main() -> int:
    print("== 音色库 / 字段映射 ==")
    voices, fmoctave = load_bank(DRV)
    v0 = voices[0]
    rec = v0.raw
    check("voice#0 head = 0x3A", v0.head == rec[0] == 0x3A, f"{v0.head:#x}")
    check("AR 取反表 (0x1F-rec)", all(v0.ar[k] == 0x1F - rec[1 + k] for k in range(4)),
          f"{v0.ar}")
    check("TL 在 0x1B-0x1E 且取反", all(v0.tl[k] == 0x7F - rec[0x1B + k] for k in range(4)),
          f"{v0.tl}")
    check("voice#0 TL = 0x21/2F/23/00", v0.tl == (0x21, 0x2F, 0x23, 0x00), f"{v0.tl}")
    check("key-on 槽位掩码恒 0x0F", all(v.slotmask == 0x0F for v in voices))
    signed = sorted({(v - 256 if v >= 128 else v) for v in fmoctave})
    check("FMOCTAVE = -12/-24/-60 三档", signed == [-60, -24, -12], f"{signed}")

    print("== 力度曲线 ==")
    check("vel<2 -> 0", VELOCITY_TABLE[0] == 0 and VELOCITY_TABLE[1] == 0)
    check("vel=100 -> 0x72", VELOCITY_TABLE[100] == 0x72, f"{VELOCITY_TABLE[100]:#x}")
    check("vel=127 -> 0x7F", VELOCITY_TABLE[127] == 0x7F)
    check("单调不减", all(VELOCITY_TABLE[i] <= VELOCITY_TABLE[i + 1] for i in range(127)))

    print("== FNUM / 弯音 ==")
    check("A4 FNUM=1038 @8MHz", FNUM_SEMITONE[9] == 1038)
    check("弯音增量表一致",
          all(BEND_DELTA[i] in (FNUM_SEMITONE[i + 1] - FNUM_SEMITONE[i],
                                FNUM_SEMITONE[i + 1] - FNUM_SEMITONE[i] + 1,
                                FNUM_SEMITONE[i + 1] - FNUM_SEMITONE[i] - 1)
              for i in range(11)))

    print("== 鼓映射 ==")
    check("35/36 -> BD", DRUM_MAP[35] == 0 and DRUM_MAP[36] == 0)
    check("38/40 -> SD", DRUM_MAP[38] == 1 and DRUM_MAP[40] == 1)
    check("42/44/46 -> HiHat", DRUM_MAP[42] == 3 and DRUM_MAP[44] == 3 and DRUM_MAP[46] == 3)
    check("39 HandClap 哑音", DRUM_MAP[39] == 0x7F)
    check("60..81 全哑音", all(DRUM_MAP[n] == 0x7F for n in range(60, 82)))

    print("== 寄存器序列（NullCore 抓取）==")
    core = NullCore()
    sysm = NecopnSystem([core, core], mode="necopn", lfo_enabled=False)
    sysm.load_bank(voices, fmoctave)
    sysm.trace = []
    sysm.time = 0.0
    sysm.note_on(0, 60, 100)          # MIDI ch0, C4
    w = {(r, v) for (_t, _c, r, v) in sysm.trace}
    pairs = [(r, v) for (_t, _c, r, v) in sysm.trace]
    check("写过 0xB0=0x3A（FB/ALG）", (0xB0, 0x3A) in pairs)
    check("写过 0x28 key-on（0x0F<<4|ch0）", (0x28, 0xF0) in pairs)
    # 力度 100 -> level 0x72；ALG=2 -> 只有 op4 吃力度
    alg = v0.head & 7
    lvl = VELOCITY_TABLE[100]
    atten = 0x7F - lvl
    expect_tl = []
    for i in range(4):
        base = v0.tl[i]
        expect_tl.append(min(0x7F, (OPMASK[alg][i] & atten) + base))
    tl_regs = {r: v for (r, v) in pairs if 0x40 <= r <= 0x4C}
    check("TL 寄存器 = 基准+力度衰减",
          all(tl_regs.get(0x40 + off) == expect_tl[i]
              for i, off in enumerate((0x00, 0x08, 0x04, 0x0C))),
          f"got {[(hex(k), hex(v)) for k, v in sorted(tl_regs.items())]} want {list(map(hex, expect_tl))}")
    # FNUM: note 60 经 FMOCTAVE[0]=-12 变 48 -> block 4, fnum 617
    check("FNUM 高位 = block<<3|fnum9:8（已移调 block=4）",
          (0xA4, ((4 << 3) | (617 >> 8)) & 0xFF) in pairs,
          f"{[(hex(r), hex(v)) for r, v in pairs if r in (0xA0, 0xA4)]}")
    check("FNUM 低位 = 617&0xFF", (0xA0, 617 & 0xFF) in pairs)

    print("== 移调 FMOCTAVE ==")
    core2 = NullCore()
    s2 = NecopnSystem([core2, core2], mode="necopn", lfo_enabled=False)
    s2.load_bank(voices, fmoctave)
    s2.trace = []
    s2.note_on(1, 60, 100)            # ch1 用 program 0 -> FMOCTAVE=-12
    f = [(r, v) for (_t, _c, r, v) in s2.trace if r in (0xA0, 0xA4)]
    check("note 60 移调 -12 -> block 4, fnum 617",
          (0xA0, 617 & 0xFF) in f and (0xA4, (4 << 3) | (617 >> 8)) in f,
          f"{[(hex(r), hex(v)) for r, v in f]}")

    print("== 鼓路由 ==")
    s2.trace = []
    s2.note_on(9, 38, 90)             # 军鼓
    d = [(r, v) for (_t, _c, r, v) in s2.trace]
    check("0x18+1 = 0xC0|level（SD）",
          any(r == 0x19 and (v & 0xE0) == 0xC0 for r, v in d), f"{d}")
    check("0x10 = 1<<1 触发", (0x10, 0x02) in d, f"{d}")
    s2.trace = []
    s2.note_on(9, 39, 90)             # HandClap -> 哑音
    check("HandClap 不写寄存器", len(s2.trace) == 0, f"{s2.trace}")

    print("== 通道分配 / 抢音 ==")
    core3 = NullCore()
    s3 = NecopnSystem([core3, core3], mode="necopn", lfo_enabled=False)
    s3.load_bank(voices, fmoctave)
    for i in range(6):
        s3.note_on(0, 50 + i, 100)
    check("6 个声部全部占满", all(s.active for s in s3.slots))
    s3.note_on(0, 70, 100)            # 第 7 个：同号抢音（pass4 取最旧）
    check("第 7 个音（同 MIDI 通道）触发抢音",
          any(s.note == 70 for s in s3.slots), f"{[(s.midi_ch, s.note) for s in s3.slots]}")
    core4 = NullCore()
    s4 = NecopnSystem([core4, core4], mode="necopn", lfo_enabled=False)
    s4.load_bank(voices, fmoctave)
    for i in range(6):
        s4.note_on(0, 50 + i, 100)
    s4.note_on(10, 70, 100)           # 高通道号抢不到低通道号 -> 丢弃
    check("MIDI ch10 抢不到 ch0-5（优先级）", not any(s.note == 70 for s in s4.slots))
    core4b = NullCore()
    s4b = NecopnSystem([core4b, core4b], mode="necopn", lfo_enabled=False)
    s4b.load_bank(voices, fmoctave)
    s4b.note_on(3, 40, 100)            # ch3 一个
    for i in range(5):
        s4b.note_on(0, 50 + i, 100)    # ch0 五个 -> 占满 6 声部
    s4b.note_on(3, 77, 100)            # pass4 取 midi_ch 最大者 -> 抢 ch3 那个
    check("pass4 取 midi_ch 最大者（ch3 抢 ch3，不抢更旧的 ch0）",
          sum(1 for s in s4b.slots if s.midi_ch == 0 and s.active) == 5
          and any(s.midi_ch == 3 and s.note == 77 for s in s4b.slots),
          f"{[(s.midi_ch, s.note, s.active) for s in s4b.slots]}")

    print("== program change（seg1:0x0478）==")
    core6 = NullCore()
    s6 = NecopnSystem([core6, core6], mode="necopn", lfo_enabled=False)
    s6.load_bank(voices, fmoctave)
    s6.note_on(0, 60, 100)
    s6.note_on(1, 62, 100)
    s6.program_change(0, 5)
    alive = [(s.midi_ch, s.note) for s in s6.slots if s.active]
    check("program_change 只清本 MIDI 通道的声部", alive == [(1, 62)], f"{alive}")
    check("program_change 后本通道音符不再可匹配", s6._find(0, 60) < 0)
    check("program_change 记录新音色", s6.programs[0] == 5)
    s6.note_off(1, 62)
    check("其它通道的音符仍能正常释放", not any(s.active for s in s6.slots))

    print("== 弯音 ==")
    core5 = NullCore()
    s5 = NecopnSystem([core5, core5], mode="necopn", lfo_enabled=False)
    s5.load_bank(voices, fmoctave)
    s5.note_on(0, 60, 100)
    s5.trace = []
    s5.pitch_bend(0, 0x00, 0x60)      # bend14 = 0x60<<7 = 12288 -> +1 半音附近
    f = [(r, v) for (_t, _c, r, v) in s5.trace if r in (0xA0, 0xA4)]
    check("弯音改写 FNUM", len(f) >= 2)
    s5.trace = []
    s5.pitch_bend(0, 0x00, 0x40)      # bend14 = 8192 = 中位
    f = [(r, v) for (_t, _c, r, v) in s5.trace if r in (0xA0, 0xA4)]
    check("中位弯音回到原 FNUM（note 60-12=48? 视移调）",
          (0xA0, 617 & 0xFF) in f or (0xA0, 0x00) in f, f"{[(hex(r), hex(v)) for r, v in f]}")

    print("== LFO tick ==")
    core6 = NullCore()
    s6 = NecopnSystem([core6, core6], mode="necopn", lfo_enabled=True)
    s6.load_bank(voices, fmoctave)
    s6.note_on(0, 60, 100)
    s6.trace = []
    for _ in range(2000):
        s6.tick()
    check("LFO 周期性改写 TL", any(0x40 <= r <= 0x4C for (_t, _c, r, _v) in s6.trace),
          f"{len(s6.trace)} writes")

    print("== 表与 NECOPN.DRV 原始字节对拍（审计修正回归）==")
    drv = open(DRV, "rb").read()
    SEG3, DG = 0x3D60, 0x1480
    drv_mask = [tuple(drv[SEG3 + 0x0C55 + i * 4: SEG3 + 0x0C55 + i * 4 + 4]) for i in range(8)]
    check("OPMASK 8 行 == cs:0x0C55（ALG6 = 00/7F/7F/7F）",
          [tuple(r) for r in OPMASK] == drv_mask, f"drv={drv_mask}")
    drv_inv = {i: drv[SEG3 + 0x1239 + i] for i in range(0x33)}
    check("INVERT == cs:0x1239（物理索引逐字节）",
          all(INVERT.get(i, 0) == drv_inv[i] for i in range(0x33)),
          f"{[i for i in range(0x33) if INVERT.get(i, 0) != drv_inv[i]]}")
    check("VELOCITY_TABLE == DGROUP:0x2E（128 项）",
          tuple(drv[DG + 0x2E: DG + 0x2E + 128]) == tuple(VELOCITY_TABLE))
    check("DRUM_MAP == DGROUP:0x8B+note（35..81）",
          all(DRUM_MAP[n] == drv[DG + 0x8B + n] for n in range(35, 82)),
          f"{[n for n in range(35, 82) if DRUM_MAP[n] != drv[DG + 0x8B + n]]}")
    check("FNUM_SEMITONE/BEND_DELTA == cs:0x14F4 / cs:0x150C（前 11 项）",
          tuple(struct.unpack_from("<12H", drv, SEG3 + 0x14F4)) == tuple(FNUM_SEMITONE)
          and tuple(struct.unpack_from("<11H", drv, SEG3 + 0x150C)) == tuple(BEND_DELTA[:11]))

    # cs:0x0C55 的 ALG6 行：只有 S2..S4 是载波（原先误写成 4 个全载波）
    alg6 = [p for p in range(128) if (voices[p].head & 7) == 6]
    check("本库 ALG=6 音色唯一（program 18）", alg6 == [18], f"{alg6}")
    coreR = NullCore()
    sR = NecopnSystem([coreR, coreR], mode="necopn", lfo_enabled=False)
    sR.load_bank(voices, fmoctave)
    sR.programs[0] = 18
    sR.trace = []
    sR.note_on(0, 60, 60)
    got = {r: v for (_t, _c, r, v) in sR.trace if 0x40 <= r <= 0x4C}
    lvl18 = VELOCITY_TABLE[60]
    rec18 = drv[0x5510 + 18 * 64: 0x5510 + 19 * 64]
    want18 = {0x40 + off: min(0x7F, (drv_mask[6][i] & (0x7F - lvl18)) + (0x7F - rec18[0x1B + i]))
              for i, off in enumerate((0x00, 0x08, 0x04, 0x0C))}
    check("ALG=6 的 TL 与驱动一致（op1 调制器不吃力度）",
          all(got.get(r) == want18[r] for r in want18),
          f"got={ {hex(k): hex(v) for k, v in got.items()} } want={ {hex(k): hex(v) for k, v in want18.items()} }")

    # seg1:0x0010 的闸门：移调后 >= 0x5C 只写 TL，不写 FNUM / 不写 0x28（但声部仍被占）
    coreG = NullCore()
    sG = NecopnSystem([coreG, coreG], mode="necopn", lfo_enabled=False)
    sG.load_bank(voices, fmoctave)
    sG.programs[0] = 0                     # FMOCTAVE[0] = -12
    sG.trace = []
    sG.note_on(0, 104, 100)                # 104 - 12 = 92 = 0x5C -> 跳过 key-on
    regs = [r for (_t, _c, r, _v) in sG.trace]
    check("移调后 note=0x5C 不写 0x28", 0x28 not in regs, f"{sorted(set(regs))}")
    check("移调后 note=0x5C 不写 FNUM", not any(r in (0xA0, 0xA4) for r in regs))
    check("跳过 key-on 仍占声部且已写 TL",
          any(s.active for s in sG.slots) and any(0x40 <= r <= 0x4C for r in regs))
    sG.trace = []
    sG.note_on(0, 103, 100)                # 103 - 12 = 91 -> 正常 key-on
    check("移调后 note=0x5B 正常 key-on",
          any(r == 0x28 for (_t, _c, r, _v) in sG.trace))

    # seg3:0x0EFB：LFO 计数器每个走完步进的 tick 自增（方波/随机波形靠它走相位）
    coreL = NullCore()
    sL = NecopnSystem([coreL, coreL], mode="necopn", lfo_enabled=True)
    sL.load_bank(voices, fmoctave)
    sL.programs[0] = 80                    # shape=1 方波，rate_word 0x01AD -> period = 152 tick
    sL.note_on(0, 60, 100)
    seq = []
    for _ in range(400):
        sL.tick()
        seq.append(sL.vstates[0].lfo_value & 0xFFFF)
    check("LFO 计数器每 tick 递增", sL.vstates[0].lfo_counter == 401,
          f"{sL.vstates[0].lfo_counter}")
    check("方波 LFO 随时间交替 7FFF/8000（不再冻结）",
          {0x7FFF, 0x8000} <= set(seq), f"{sorted({hex(x) for x in set(seq)})}")

    # seg1:0x055F：鼓通道先判力度，力度 0 走 note-off 路径 -> 0x2CC（ret 8）= 完全无写入
    coreD = NullCore()
    sD = NecopnSystem([coreD, coreD], mode="necopn", lfo_enabled=False)
    sD.load_bank(voices, fmoctave)
    sD.trace = []
    sD.note_on(9, 38, 0)
    check("鼓通道力度 0 的 note-on 无寄存器写入", len(sD.trace) == 0, f"{sD.trace}")

    print("== SMF 解析 ==")
    ev, tpq, total = parse_smf(make_midi())
    check("事件数 6", len(ev) == 6, f"{len(ev)}")
    check("包含 note on/off/prog/bend",
          {e.kind for e in ev} >= {"on", "off", "prog", "bend"})

    print("== VGM 输出 ==")
    tap = VgmTap(NullCore())
    tap.write(0, 0x28, 0xF0)          # 芯片 0 寄存器组 0 -> 0x56
    tap.advance(0.5)
    tap.write(1, 0x28, 0x00)          # 芯片 1（各自成文件，命令同 0x56）
    tap.write(2, 0x22, 0x00)          # 扩展寄存器组 -> 0x57
    blob = tap.build(1.0)
    check("VGM 头签名", blob[:4] == b"Vgm ")
    check("含 0x56/0x57 命令", 0x56 in blob and 0x57 in blob)
    check("含 0x61 等待命令（时间轴正确）", 0x61 in blob)

    print()
    print(f"结果: {ok} 通过, {fail} 失败")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
