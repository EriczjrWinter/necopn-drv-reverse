# -*- coding: utf-8 -*-
"""
NECOPN.DRV 音色库提取器: 8KB FMPARA/"VoicePara" 资源 -> WOPN v2 bank (OPNA)

字段映射来源(逆向 seg3:0xBB9 装载器 / 0x126D 组写入器 / 0x11F8 读取器 / 0x1239 取反表):
  记录(64B) --copy 51B--> 工作区(0x26F4+ch*0x34), 逻辑索引>0x14 时读取器 +1 跳过运行时字段
  **记录存的是取反值**：AR/DR/SR = 0x1F-rec，SL/RR = 0x0F-rec，TL = 0x7F-rec
  TL 真实来源 = 记录 0x1B-0x1E（逻辑 0x1A-0x1D，见 seg3:0x0BED 力度路径）；
  记录 0x05/0x0A/0x0F/0x14 是 key-on 槽位掩码 / LFO 波形 / LFO 起相位 / LFO 速率，不是 TL
  WOPN 算子顺序 = 寄存器偏移 +0/+4/+8/+C (libOPNMIDI: 0x30 + d*0x10 + op*4)
  NECOPN 字段顺序 = 槽位偏移 +0/+8/+4/+C (cs:0x142C = [0,8,4,0xC])

用法: py extract_bank.py [NECOPN.DRV路径] [输出目录]
"""
import struct
import sys
import os

IN_PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join("..", "NECOPN.DRV")
OUT_DIR = sys.argv[2] if len(sys.argv) > 2 else "out"
os.makedirs(OUT_DIR, exist_ok=True)

data = open(IN_PATH, "rb").read()
BANK = data[0x5510:0x5510 + 0x2000]          # FMPARA/"VoicePara", 128×64B
OCT = data[0x7510:0x7510 + 0x80]             # FMOCTAVE/"VoiceOctave", 128B
assert len(BANK) == 0x2000

GM_NAMES = [
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano",
    "Honky-tonk Piano", "Electric Piano 1", "Electric Piano 2", "Harpsichord",
    "Clavi", "Celesta", "Glockenspiel", "Music Box", "Vibraphone", "Marimba",
    "Xylophone", "Tubular Bells", "Dulcimer", "Drawbar Organ", "Percussive Organ",
    "Rock Organ", "Church Organ", "Reed Organ", "Accordion", "Harmonica",
    "Tango Accordion", "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)",
    "Electric Guitar (jazz)", "Electric Guitar (clean)", "Electric Guitar (muted)",
    "Overdriven Guitar", "Distortion Guitar", "Guitar harmonics", "Acoustic Bass",
    "Electric Bass (finger)", "Electric Bass (pick)", "Fretless Bass", "Slap Bass 1",
    "Slap Bass 2", "Synth Bass 1", "Synth Bass 2", "Violin", "Viola", "Cello",
    "Contrabass", "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp",
    "Timpani", "String Ensemble 1", "String Ensemble 2", "SynthStrings 1",
    "SynthStrings 2", "Choir Aahs", "Voice Oohs", "Synth Voice", "Orchestra Hit",
    "Trumpet", "Trombone", "Tuba", "Muted Trumpet", "French Horn", "Brass Section",
    "SynthBrass 1", "SynthBrass 2", "Soprano Sax", "Alto Sax", "Tenor Sax",
    "Baritone Sax", "Oboe", "English Horn", "Bassoon", "Clarinet", "Piccolo",
    "Flute", "Recorder", "Pan Flute", "Blown Bottle", "Shakuhachi", "Whistle",
    "Ocarina", "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)",
    "Lead 4 (chiff)", "Lead 5 (charang)", "Lead 6 (voice)", "Lead 7 (fifths)",
    "Lead 8 (bass+lead)", "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)",
    "Pad 4 (choir)", "Pad 5 (bowed)", "Pad 6 (metallic)", "Pad 7 (halo)",
    "Pad 8 (sweep)", "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)",
    "FX 4 (atmosphere)", "FX 5 (brightness)", "FX 6 (goblins)", "FX 7 (echoes)",
    "FX 8 (sci-fi)", "Sitar", "Banjo", "Shamisen", "Koto", "Kalimba", "Bag pipe",
    "Fiddle", "Shanai", "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock",
    "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal", "Guitar Fret Noise",
    "Breath Noise", "Seashore", "Bird Tweet", "Telephone Ring", "Helicopter",
    "Applause", "Gunshot",
]

def field(rec, logical):
    """工作区逻辑索引 -> 记录字节 (>0x14 时表内偏移+1, 跳过运行时字段 0x14)"""
    t = logical + (1 if logical > 0x14 else 0)
    return rec[t]

# seg3:0x1239 取反表：这些字段在记录里存的是 (最大值 - 真值)
INVERT_MAX = {}
for _i in (0x01, 0x02, 0x03, 0x04, 0x06, 0x07, 0x08, 0x09, 0x0B, 0x0C, 0x0D, 0x0E):
    INVERT_MAX[_i] = 0x1F
for _i in (0x10, 0x11, 0x12, 0x13, 0x16, 0x17, 0x18, 0x19):
    INVERT_MAX[_i] = 0x0F
for _i in (0x1B, 0x1C, 0x1D, 0x1E):
    INVERT_MAX[_i] = 0x7F


def field_inv(rec, logical):
    """逻辑索引 -> 寄存器真值（应用 cs:0x1239 取反表）"""
    t = logical + (1 if logical > 0x14 else 0)
    v = rec[t]
    m = INVERT_MAX.get(t)
    return (m - v) if m is not None else v


def op_fields(rec):
    """返回按芯片槽位偏移 +0/+8/+4/+C 排列的 (AR,DR,SR,SL,RR,KS,ML,DT,TL)"""
    out = []
    for k in range(4):                     # k: 寄存器偏移顺序 [0,8,4,C]
        ar = field_inv(rec, 0x01 + k)
        dr = field_inv(rec, 0x06 + k)
        sr = field_inv(rec, 0x0B + k)
        sl = field_inv(rec, 0x10 + k)
        rr = field_inv(rec, 0x15 + k)
        ks = field(rec, 0x1F + k)
        ml = field(rec, 0x24 + k)
        dt = field(rec, 0x29 + k)
        tl = field_inv(rec, 0x1A + k)      # 记录 0x1B-0x1E = 力度基准 TL（取反）
        out.append(dict(ar=ar, dr=dr, sr=sr, sl=sl, rr=rr, ks=ks, ml=ml, dt=dt, tl=tl))
    return out

def est_delay(op):
    """v2 里 delay 全 0 = 空白音色, 用包络估算毫秒 (非零即可, 这里给粗略值)"""
    on = min(2000, 1 << max(0, 15 - op["ar"]))
    off = min(10000, 1 << max(0, 15 - op["rr"]))
    return max(on, 1), max(off, 1)

def parse_rec(n):
    rec = BANK[n * 0x40:(n + 1) * 0x40]
    head = rec[0]
    pan = (head >> 6) & 3          # 0=center 1=right? 2=left? (OPN RL: bit7=L, bit6=R)
    fb = (head >> 3) & 7
    alg = head & 7
    ops = op_fields(rec)
    return dict(head=head, pan=pan, fb=fb, alg=alg, ops=ops)

# ---------- 校验 + 文本 dump ----------
lines = ["# NECOPN voice dump (128 x 64B @ file 0x5510)",
         "# op order = register slot +0/+8/+4/+C (driver order); slotAdj = [0,8,4,0xC]",
         "prog pan fb alg | op: DT ML TL KS AR AM DR SR SL RR | raw[0x00..0x33]"]
problems = []
for n in range(128):
    v = parse_rec(n)
    for i, o in enumerate(v["ops"]):
        if o["ml"] > 15 or o["dt"] > 7 or o["ks"] > 3 or o["ar"] > 31 or o["dr"] > 31 \
           or o["sr"] > 31 or o["sl"] > 15 or o["rr"] > 15 or o["tl"] > 0x7F:
            problems.append((n, i, o))
    ops_txt = " | ".join(
        f"{o['dt']:X}/{o['ml']:2d}/{o['tl']:3d}/{o['ks']}/{o['ar']:2d}/"
        f"{(o['dr'] >> 7)}/{o['dr'] & 0x1F}/{o['sr']:2d}/{o['sl']:X}/{o['rr']:X}"
        for o in v["ops"])
    raw = " ".join(f"{b:02X}" for b in BANK[n * 0x40:n * 0x40 + 0x34])
    lines.append(f"{n:3d}  {v['pan']}  {v['fb']}  {v['alg']} | {ops_txt} | {raw}")
open(os.path.join(OUT_DIR, "voices_dump.txt"), "w", encoding="utf-8").write("\n".join(lines))
if problems:
    print(f"!! {len(problems)} 个字段越界:")
    for p in problems[:10]:
        print("   prog", p[0], "op", p[1], p[2])
else:
    print("OK: 128 条音色字段全部在 OPN 寄存器值域内")

# ---------- WOPN v2 输出 ----------
def u16be(v):
    return struct.pack(">H", v)

def u16le(v):
    return struct.pack("<H", v)

op_order = [0, 2, 1, 3]        # 驱动字段序 [0,+8,+4,+C] -> 芯片偏移 +0/+4/+8/+C

instrs = b""
for n in range(128):
    v = parse_rec(n)
    name = GM_NAMES[n].encode("ascii")[:31]
    body = bytearray(69)
    body[0:32] = name.ljust(32, b"\0")
    body[32:34] = u16be(0)                     # note_offset
    body[34] = 0                               # percussion_key_number
    body[35] = v["head"] & 0x3F                # fbalg = FB<<3|ALG, pan 位清零(居中)
    body[36] = 0                               # lfosens
    for w, k in enumerate(op_order):
        o = v["ops"][k]
        off = 37 + w * 7
        body[off + 0] = ((o["dt"] & 7) << 4) | (o["ml"] & 15)          # 0x30 DT/ML
        body[off + 1] = min(o["tl"], 0x7F)                             # 0x40 TL
        body[off + 2] = ((o["ks"] & 3) << 6) | (o["ar"] & 31)          # 0x50 KS/AR
        body[off + 3] = (o["dr"] & 0x7F) | ((o["dr"] & 0x80))          # 0x60 AM/DR
instrs_v2 = b""
instrs_v1 = b""
for n in range(128):
    v = parse_rec(n)
    name = GM_NAMES[n].encode("ascii")[:31]
    body = bytearray(65)                       # v1 条目 = 65 字节
    body[0:32] = name.ljust(32, b"\0")
    body[32:34] = u16be(0)                     # note_offset
    body[34] = 0                               # percussion_key_number
    body[35] = v["head"] & 0x3F                # fbalg = FB<<3|ALG, pan 位清零(居中)
    body[36] = 0                               # lfosens
    for w, k in enumerate(op_order):
        o = v["ops"][k]
        off = 37 + w * 7
        body[off + 0] = ((o["dt"] & 7) << 4) | (o["ml"] & 15)          # 0x30 DT/ML
        body[off + 1] = min(o["tl"], 0x7F)                             # 0x40 TL
        body[off + 2] = ((o["ks"] & 3) << 6) | (o["ar"] & 31)          # 0x50 KS/AR
        body[off + 3] = (o["dr"] & 0x7F) | ((o["dr"] & 0x80))          # 0x60 AM/DR
        body[off + 4] = o["sr"] & 31                                   # 0x70 SR
        body[off + 5] = ((o["sl"] & 15) << 4) | (o["rr"] & 15)         # 0x80 SL/RR
        body[off + 6] = 0                                              # 0x90 SSG-EG
    don, doff = est_delay(v["ops"][0])
    instrs_v1 += bytes(body)
    instrs_v2 += bytes(body) + u16be(don) + u16be(doff)

def bank(magic, version, lfo_byte, body):
    out = bytearray()
    out += magic
    if version > 1:
        out += u16le(version)
    out += u16be(1)                # melodic banks = 1
    out += u16be(0)                # percussive banks = 0
    out += bytes([lfo_byte])
    if version >= 2:
        out += b"NECOPN GM".ljust(32, b"\0") + bytes([0, 0])   # bank 名 + lsb/msb
    out += body
    return bytes(out)
b2 = bank(b"WOPN2-B2NK\0", 2, 0x10, instrs_v2)
b1 = bank(b"WOPN2-BANK\0", 1, 0x00, instrs_v1)
p2 = os.path.join(OUT_DIR, "NECOPN_GM.wopn")
open(p2, "wb").write(b2)
print(f"WOPN v2 (推荐): {p2} ({len(b2)} 字节)")
p1 = os.path.join(OUT_DIR, "NECOPN_GM_v1.wopn")
open(p1, "wb").write(b1)
print(f"WOPN v1 (旧库兼容): {p1} ({len(b1)} 字节)")
fnum = struct.unpack("<12H", data[0x3D60 + 0x14F4:0x3D60 + 0x14F4 + 24])
print("FNUM 表:", fnum)
print("时钟验证: FNUM = f*144*2^17/clk, A4=440 ->",
      round(440 * 144 * (2 ** 17) / 8000000), "(表值 %d, clk=8MHz)" % fnum[9])
