# -*- coding: utf-8 -*-
"""生成整个代码段的线性反汇编清单（带已知符号标注），用于人工/脚本化分析。

用法:
    python listing.py [seg=3] [outfile]

输出到 out/seg<N>_listing.txt。遇到无法解码的字节按 .byte 输出并前进 1 字节，
保证清单覆盖整段，不跳空。
"""
import os
import struct
import sys

from capstone import Cs, CS_ARCH_X86, CS_MODE_16

HERE = os.path.dirname(os.path.abspath(__file__))
DRV = os.path.join(HERE, "..", "NECOPN.DRV")
SEGS = {1: 0x240, 2: 0x1400, 3: 0x3D60, 4: 0x1480}
LENS = {1: 0x1106, 2: 0x0054, 3: 0x1523, 4: 0x2894}

# seg3 已知例程/数据（来自 REPORT.md 阶段 1）
SYMS3 = {
    0x0000: "DRIVERPROC",
    0x02E0: "load_FMOCTAVE",
    0x0776: "load_FMPARA",
    0x0A90: "dispatch",
    0x0AD3: "dispatch_table",
    0x0BB9: "cmd_16_load_voice",
    0x0BED: "cmd_1F_volume",
    0x0C55: "tbl_C55_opmask",
    0x0C75: "pitch_calc",
    0x0CCB: "cmd_24_drum",
    0x0D23: "MODMESSAGE",
    0x0D27: "irq_poll",
    0x0F16: "scale_knob",
    0x100C: "timer_ack",
    0x1033: "reg_read",
    0x1192: "reg_write",
    0x11F8: "field_reader",
    0x126D: "group_write_a",
    0x12DE: "group_write_b",
    0x142C: "tbl_slot_offset",
    0x1430: "voice_write_regs",
    0x147F: "helper_147F",
    0x14A6: "tl_write",
    0x14F4: "tbl_fnum_semitone",
    0x150C: "tbl_bend_delta",
}
SYMS1 = {
    0x063E: "MYFAIRLADY",
    0x0B6C: "LibMain",
    0x0F8C: "___EXPORTEDSTUB",
}


def main():
    seg = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        HERE, "out", f"seg{seg}_listing.txt")
    data = open(DRV, "rb").read()
    base, ln = SEGS[seg], LENS[seg]
    code = data[base:base + ln]
    md = Cs(CS_ARCH_X86, CS_MODE_16)
    md.detail = False
    syms = {1: SYMS1, 3: SYMS3}.get(seg, {})

    lines = [f"; seg{seg} base=0x{base:05X} len=0x{ln:04X} ({ln} bytes)",
             f"; source: NECOPN.DRV  (seg{seg}:off -> file 0x{{off+base:05X}})", ""]
    pos = 0
    while pos < ln:
        if pos in syms:
            lines.append(f"\n{pos:04X} <{syms[pos]}>:")
        window = code[pos:pos + 15]
        insns = list(md.disasm(window, pos, count=1))
        if insns:
            i = insns[0]
            lines.append(f"  {i.address:04X}  {i.bytes.hex():<16} {i.mnemonic} {i.op_str}")
            pos += i.size
        else:
            lines.append(f"  {pos:04X}  {code[pos]:<16} .byte 0x{code[pos]:02X}")
            pos += 1

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {out} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
