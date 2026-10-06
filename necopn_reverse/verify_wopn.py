# -*- coding: utf-8 -*-
"""按 libOPNMIDI wopn_file.c 的加载逻辑回读校验生成的 .wopn"""
import struct
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "out/NECOPN_GM.wopn"
d = open(path, "rb").read()
off = 0

def take(n):
    global off
    b = d[off:off + n]
    assert len(b) == n, f"文件提前结束 @ {off:#x}"
    off += n
    return b

magic = take(11)
assert magic == b"WOPN2-B2NK\0", magic
ver = struct.unpack("<H", take(2))[0]
assert ver == 2, ver
mel = struct.unpack(">H", take(2))[0]
per = struct.unpack(">H", take(2))[0]
lfo = take(1)[0]
chip = (lfo >> 4) & 1
print(f"version={ver} melodic={mel} percussive={per} chip={'OPNA' if chip else 'OPN2'} lfo={lfo & 0xF}")

ins_size = 69
total_banks = mel + per
for b in range(total_banks):
    if ver >= 2:
        name = take(32).rstrip(b"\0").decode("ascii", "replace")
        lsb, msb = take(2)
        print(f"bank{b}: '{name}' lsb={lsb} msb={msb}")
    for i in range(128):
        ins = take(ins_size)
        name = ins[0:32].rstrip(b"\0").decode("ascii", "replace")
        note_off = struct.unpack(">h", ins[32:34])[0]
        fbalg, lfo_s = ins[35], ins[36]
        assert (fbalg >> 3) & 7 <= 7 and fbalg & 7 <= 7
        don, doff = struct.unpack(">HH", ins[65:69])
        assert don > 0 and doff > 0, f"空白音色! prog {i}"
        for w in range(4):
            o = ins[37 + w * 7:37 + w * 7 + 7]
            dt, tl, rsar, amdr, sr, slrr, ssgeg = o
            assert (dt >> 4) <= 7 and (dt & 15) <= 15, (i, w, "dtml", dt)
            assert tl <= 0x7F, (i, w, "tl", tl)
            assert (rsar >> 6) <= 3 and (rsar & 0x3F) <= 31, (i, w, "ksar", rsar)
            assert (amdr & 0x1F) <= 31, (i, w, "dr", amdr)
            assert sr <= 31, (i, w, "sr", sr)
            assert (slrr >> 4) <= 15 and (slrr & 15) <= 15, (i, w, "slrr", slrr)
            assert ssgeg == 0, (i, w, "ssgeg")
        if i < 3 or i == 127:
            ops = ["DT/ML=%02X TL=%02X KS/AR=%02X AM/DR=%02X SR=%02X SL/RR=%02X" %
                   tuple(ins[37 + w * 7:37 + w * 7 + 6]) for w in range(4)]
            print(f"  prog {i:3d} '{name}': fbalg={fbalg:02X} " + " | ".join(ops))

assert off == len(d), f"尾部多余字节: {len(d) - off}"
print(f"校验通过: {len(d)} 字节, {total_banks} bank × 128 音色全部合法")
