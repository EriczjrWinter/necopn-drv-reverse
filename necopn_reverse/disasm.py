# -*- coding: utf-8 -*-
"""seg 反汇编小工具: py disasm.py <seg#> <off(hex)> [len] ; 以及扫描模式"""
import struct, sys
from capstone import Cs, CS_ARCH_X86, CS_MODE_16

data = open(r"..\NECOPN.DRV", "rb").read()
u16 = lambda o: struct.unpack_from("<H", data, o)[0]
SEGS = {1: 0x240, 2: 0x1400, 3: 0x3D60, 4: 0x1480}
LENS = {1: 0x1106, 2: 0x0054, 3: 0x1523, 4: 0x2894}
md = Cs(CS_ARCH_X86, CS_MODE_16)
md.detail = False

def dis(seg, off, n=48):
    fo = SEGS[seg] + off
    code = data[fo:fo + n]
    print(f"--- seg{seg}:{off:04x} ---")
    for i in md.disasm(code, off):
        print(f"  {i.address:04x}: {i.bytes.hex():<14} {i.mnemonic} {i.op_str}")

mode = sys.argv[1] if len(sys.argv) > 1 else ""

if mode == "dis":
    dis(int(sys.argv[2]), int(sys.argv[3], 16), int(sys.argv[4]) if len(sys.argv) > 4 else 48)

elif mode == "farcalls":
    # 扫描 call far (9A) / jmp far (EA) 的硬编码段:偏移
    for seg in (1, 2, 3):
        base, ln = SEGS[seg], LENS[seg]
        hits = {}
        i = 0
        code = data[base:base + ln]
        while i < ln - 4:
            b = code[i]
            if b == 0x9A or b == 0xEA:
                toff, tseg = struct.unpack_from("<HH", code, i + 1)
                hits.setdefault((tseg, b), []).append((i, toff))
            i += 1
        print(f"seg{seg} far transfers:")
        for (tseg, b), lst in sorted(hits.items()):
            print(f"  -> seg{tseg} ({'call' if b == 0x9A else 'jmp'}) x{len(lst)}  e.g. " +
                  ", ".join(f"from {o:04x}->{t:04x}" for o, t in lst[:5]))

elif mode == "imms":
    # 扫描 seg3 里 cmp xxx, imm16 的 0x050x (modMessage 消息值)
    base, ln = SEGS[3], LENS[3]
    code = data[base:base + ln]
    found = {}
    i = 0
    while i < ln - 3:
        b = code[i]
        if b in (0x3D,) or (0x81 <= b <= 0x83):
            # 3D iw = cmp ax,imm16 ; 81 /7 iw = cmp r/m16,imm16
            if b == 0x3D and i + 3 <= ln:
                v = struct.unpack_from("<H", code, i + 1)[0]
                if 0x500 <= v <= 0x51F:
                    found.setdefault(v, []).append(i)
                i += 3
                continue
            if b == 0x81 and i + 5 <= ln and (code[i + 1] & 0x38) == 0x38:
                v = struct.unpack_from("<H", code, i + 2)[0]
                if 0x500 <= v <= 0x51F:
                    found.setdefault(v, []).append(i)
                i += 5
                continue
        i += 1
    for v in sorted(found):
        print(f"cmp imm {v:#06x} (MODM?) at seg3:" + ",".join(f"{o:04x}" for o in found[v]))

elif mode == "prologues":
    # 找 MS C 函数序言 push bp; mov bp,sp (55 8B EC)
    for seg in (1, 3):
        base, ln = SEGS[seg], LENS[seg]
        code = data[base:base + ln]
        offs = [i for i in range(ln - 3) if code[i] == 0x55 and code[i + 1] == 0x8B and code[i + 2] == 0xEC]
        print(f"seg{seg} prologues ({len(offs)}): " + " ".join(f"{o:04x}" for o in offs))
