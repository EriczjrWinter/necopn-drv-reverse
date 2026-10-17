# -*- coding: utf-8 -*-
"""
NECOPN.DRV NE(16-bit Windows) 解析器
- 解析 MZ/NE 头、段表、重定位、入口表、名称表
- 生成可供 Ghidra 以 raw 16-bit x86 导入的平坦镜像 (base 0x1000:0000)
- 按文件偏移提取 4 个自定义资源（资源表布局非标准，类型名绑定由反汇编确认）
用法: py ne_parse.py [NECOPN.DRV 的路径] [输出目录]
"""
import json
import os
import struct
import sys

IN_PATH = sys.argv[1] if len(sys.argv) > 1 else "NECOPN.DRV"
OUT_DIR = sys.argv[2] if len(sys.argv) > 2 else "out"
os.makedirs(OUT_DIR, exist_ok=True)

data = open(IN_PATH, "rb").read()
u16 = lambda o: struct.unpack_from("<H", data, o)[0]
u32 = lambda o: struct.unpack_from("<I", data, o)[0]

ne = u16(0x3C)
assert data[ne:ne + 2] == b"NE", "not NE"
entoff, cbent = u16(ne + 0x04), u16(ne + 0x06)
flags = u16(ne + 0x0C)
csip = u16(ne + 0x14), u16(ne + 0x16)
cseg, cmod = u16(ne + 0x1C), u16(ne + 0x1E)
segtab, rsrctab, restab = u16(ne + 0x22), u16(ne + 0x24), u16(ne + 0x26)
modtab, imptab = u16(ne + 0x28), u16(ne + 0x2A)
nrestab, cbnres = u32(ne + 0x2C), u16(ne + 0x20)
align = u16(ne + 0x32)
sector = 1 << align
print(f"NE@{ne:#x} flags={flags:#06x} cseg={cseg} cmod={cmod} align={align} sector={sector}")
print(f"CS:IP=seg{csip[1]}:{csip[0]:#06x}  entoff={entoff:#x} len={cbent}")

# ---- 模块导入表 (modtab: 连续的 len+name) ----
mods = []
p = 0x1BA  # 实测模块名表位置（该文件 modtab 表项字段顺序非标准）
for _ in range(cmod):
    n = data[p]
    mods.append(data[p + 1:p + 1 + n].decode("ascii", "replace"))
    p += 1 + n
print("imports(modref):", list(enumerate(mods, 1)))

# ---- 段表 ----
segs = []
for i in range(cseg):
    o = ne + segtab + i * 8
    soff, slen, sfl, smin = u16(o), u16(o + 2), u16(o + 4), u16(o + 6)
    foff = soff * sector
    nreloc = u16(foff - 2) if foff >= 2 else 0
    segs.append(dict(idx=i + 1, foff=foff, len=slen, flags=sfl, min=smin, nreloc=nreloc,
                     data=data[foff:foff + slen]))
    print(f"seg{i+1}: file={foff:#x} len={slen:#x} flags={sfl:#06x} min={smin:#x} relocs={nreloc}")

# ---- 重定位解析: 非附加项 8 字节 srctype|flags|target(4)|segoff(2) ----
relocs = []       # (seg#, segoff, kind, detail)
imported = []     # (seg#, segoff, modref, what)
for s in segs:
    p = s["foff"] + s["len"]
    for _ in range(s["nreloc"]):
        if p + 8 > len(data):
            break
        st, tgt = data[p], data[p + 1]
        if st & 0x80:  # additive
            relocs.append((s["idx"], data[p + 1], "additive", None))
            p += 2
            continue
        tseg, toff = u16(p + 2), u16(p + 4)
        segoff = u16(p + 6)
        kind = tgt & 3
        if kind in (1, 3):  # imported by ordinal / by name
            mod = mods[tseg - 1] if 0 < tseg <= len(mods) else f"?{tseg}"
            what = ("ord", toff) if kind == 1 else ("name_off", toff)
            imported.append((s["idx"], segoff, mod, what))
            relocs.append((s["idx"], segoff, "import", f"{mod}!{what}", st))
        else:  # internal
            relocs.append((s["idx"], segoff, "internal", (tseg, toff), st))
        p += 8

# ---- 平坦镜像: base para 0x1000, 各段 16 对齐 ----
IMAGE_BASE_PARA = 0x1000
segs.sort(key=lambda s: s["foff"])
img_off = {}
off = 0
for s in segs:
    off = (off + 15) & ~15
    s["imgoff"] = off
    img_off[s["idx"]] = off
    off += s["len"]
image = bytearray((off + 15) & ~15)
for s in segs:
    image[s["imgoff"]:s["imgoff"] + s["len"]] = s["data"]

def seg_val(segidx):
    return IMAGE_BASE_PARA + img_off[segidx] // 16

def fo(segidx, off16):
    return img_off[segidx] + off16

# 应用重定位到镜像; 导入项用假段值 0xF000+modref 便于识别
IMPORT_SEG_BASE = 0xF000
for (sidx, segoff, kind, det, st) in relocs:
    if kind == "additive":
        image[fo(sidx, segoff)] += seg_val(sidx) & 0xFF
        image[fo(sidx, segoff) + 1] += (seg_val(sidx) >> 8) & 0xFF
    elif kind == "internal":
        tseg, toff = det
        v = seg_val(tseg)
        if st == 2:      # OFFSET: 目标 16 位偏移; toff==0 表示偏移已在代码里(fixed)
            if toff:
                image[fo(sidx, segoff):fo(sidx, segoff) + 2] = struct.pack("<H", toff)
        elif st == 3:    # POINTER 16:16: segoff=offset 字, segoff+2=段字
            if toff:
                image[fo(sidx, segoff):fo(sidx, segoff) + 2] = struct.pack("<H", toff)
            image[fo(sidx, segoff) + 2:fo(sidx, segoff) + 4] = struct.pack("<H", v)
        elif st == 5:    # BASE: 段值
            image[fo(sidx, segoff):fo(sidx, segoff) + 2] = struct.pack("<H", v)
    else:  # import
        mod, what = det.split("!", 1)
        modref = mods.index(mod) + 1 if mod in mods else 1
        fake = IMPORT_SEG_BASE + modref
        if st == 3:
            image[fo(sidx, segoff) + 2:fo(sidx, segoff) + 4] = struct.pack("<H", fake)
        else:
            image[fo(sidx, segoff):fo(sidx, segoff) + 2] = struct.pack("<H", fake)

open(os.path.join(OUT_DIR, "image_flat.bin"), "wb").write(image)
meta = dict(
    image_base_para=IMAGE_BASE_PARA,
    import_seg_base=IMPORT_SEG_BASE,
    modules=mods,
    segments=[{k: (v.hex() if k == "data" else v) for k, v in s.items()} for s in segs],
    entry_csip=dict(seg=csip[1], off=csip[0]),
    imports=imported,
)
json.dump(meta, open(os.path.join(OUT_DIR, "ne_meta.json"), "w"), indent=1)
print(f"image_flat.bin: {len(image)} bytes (base para {IMAGE_BASE_PARA:#x})")
for s in segs:
    print(f"  seg{s['idx']}: image_off={s['imgoff']:#x} -> para {seg_val(s['idx']):#x}")
print(f"imported fixups: {len(imported)}")
for imp in imported[:40]:
    print("   ", imp)

# ---- 入口表 ----
p, order = ne + entoff, 1
print("entry table:")
while p < ne + entoff + cbent:
    cnt = data[p]
    if cnt == 0:
        break
    segi = data[p + 1]
    if segi == 0:  # movable bundle
        for j in range(cnt):
            flg = data[p + 2 + j * 6]
            segv = u16(p + 2 + j * 6)
            ofv = u16(p + 4 + j * 6)
            print(f"  ord{order}: MOVABLE seg{segv}:{ofv:#06x} flags={flg:#04x}")
            order += 1
        p += 2 + cnt * 6
    else:
        for j in range(cnt):
            flg = data[p + 2 + j * 3]
            ofv = u16(p + 3 + j * 3)
            print(f"  ord{order}: seg{segi}:{ofv:#06x} flags={flg:#04x}")
            order += 1
        p += 2 + cnt * 3

# ---- 驻留/非驻留名 ----
p = ne + restab
print("resident names:")
while data[p] != 0:
    n = data[p]
    print(f"  ord{u16(p + 1 + n)}: {data[p + 1:p + 1 + n].decode('ascii', 'replace')}")
    p += n + 3
p = nrestab
print("non-resident names:")
while data[p] != 0 and p < nrestab + cbnres:
    n = data[p]
    print(f"  ord{u16(p + 1 + n)}: {data[p + 1:p + 1 + n].decode('ascii', 'replace')}")
    p += n + 3

# ---- 按已知偏移提取资源 ----
RES = [(0x5320, 0x0030, "r1_desc48"), (0x5350, 0x01C0, "r2_versioninfo"),
       (0x5510, 0x2000, "r3_voicebank_8k"), (0x7510, 0x0080, "r4_table128")]
for off_, ln, nm in RES:
    open(os.path.join(OUT_DIR, nm + ".bin"), "wb").write(data[off_:off_ + ln])
    print(f"resource {nm}: {off_:#x} +{ln:#x} -> {os.path.join(OUT_DIR, nm + '.bin')}")
print("done")
