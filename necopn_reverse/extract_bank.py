# -*- coding: utf-8 -*-
"""
NECOPN.DRV 音色库提取器 & WOPN v2 生成器 (双模版)
- 模式 1 (推荐开源分发): 自动读取由 DRVBANK.py 生成的 fixed_bank.py (硬编码)，彻底摆脱版权风险。
- 模式 2 (原始逆向): 读取 NECOPN.DRV 二进制文件。

用法: 
  1. 提取硬编码 WOPN: py extract_bank.py
  2. 从 DRV 提取 WOPN: py extract_bank.py NECOPN.DRV [输出目录]
"""
import struct
import sys
import os
import math

# ---------- 数据加载模块 ----------
IN_PATH = sys.argv[1] if len(sys.argv) > 1 else "NECOPN.DRV"
OUT_DIR = sys.argv[2] if len(sys.argv) > 2 else "out"
os.makedirs(OUT_DIR, exist_ok=True)

BANK = b""
OCT = b""

if os.path.exists("fixed_bank.py"):
    # 模式 1：优先加载硬编码数据
    print("发现 fixed_bank.py，使用硬编码音色数据...")
    from fixed_bank import VOICES_DATA, FMOCTAVE
    # 将 tuple 元组转换成 bytes，方便后续索引操作
    BANK = b"".join(bytes([b & 0xFF for b in rec]) for rec in VOICES_DATA)
    OCT = bytes([b & 0xFF for b in FMOCTAVE])
else:
    # 模式 2：回退到读取 DRV 文件
    print(f"未找到 fixed_bank.py，尝试从 {IN_PATH} 提取...")
    if not os.path.exists(IN_PATH):
        print(f"错误: 找不到 {IN_PATH}，请提供 NECOPN.DRV 或运行 DRVBANK.py 生成硬编码。")
        sys.exit(1)
    data = open(IN_PATH, "rb").read()
    BANK = data[0x5510:0x5510 + 0x2000]
    OCT = data[0x7510:0x7510 + 0x80]

assert len(BANK) == 0x2000, "音色库数据长度错误"
assert len(OCT) == 0x80, "FMOCTAVE 数据长度错误"

# 基准频率参考值
BASE_OCTAVE = 244

# WOPN v2 试听时长常量
KEY_ON_DELAY_MS = 5000
KEY_OFF_DELAY_MS = 200

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

# ---------- 核心解析逻辑 ----------

def field(rec, logical):
    t = logical + (1 if logical > 0x14 else 0)
    return rec[t]

# seg3:0x1239 取反表
INVERT_MAX = {}
for _i in (0x01, 0x02, 0x03, 0x04, 0x06, 0x07, 0x08, 0x09, 0x0B, 0x0C, 0x0D, 0x0E):
    INVERT_MAX[_i] = 0x1F
for _i in (0x1B, 0x1C, 0x1D, 0x1E):
    INVERT_MAX[_i] = 0x7F

def field_inv(rec, logical):
    t = logical + (1 if logical > 0x14 else 0)
    v = rec[t]
    m = INVERT_MAX.get(t)
    return (m - v) if m is not None else v

def op_fields(rec):
    out = []
    for k in range(4):                     # k: 寄存器偏移顺序 [0,8,4,C]
        ar = field_inv(rec, 0x01 + k)
        dr = field_inv(rec, 0x06 + k)
        sr = field_inv(rec, 0x0B + k)
        sl = field_inv(rec, 0x10 + k)      # 无取反
        rr = field_inv(rec, 0x15 + k)      # 无取反
        ks = field(rec, 0x1F + k)
        ml = field(rec, 0x24 + k)
        dt = field(rec, 0x29 + k)
        tl = field_inv(rec, 0x1A + k)      # 力度基准 TL
        out.append(dict(ar=ar, dr=dr, sr=sr, sl=sl, rr=rr, ks=ks, ml=ml, dt=dt, tl=tl))
    return out

def parse_rec(n):
    rec = BANK[n * 0x40:(n + 1) * 0x40]
    head = rec[0]
    pan = (head >> 6) & 3
    fb = (head >> 3) & 7
    alg = head & 7
    ops = op_fields(rec)
    return dict(head=head, pan=pan, fb=fb, alg=alg, ops=ops)

def is_blank(voice):
    for o in voice["ops"]:
        if o["tl"] < 0x7F:
            return False
    return True

# ---------- 校验 + 文本 dump ----------
lines = ["# NECOPN voice dump (128 x 64B)",
         "# op order = register slot +0/+8/+4/+C (driver order); slotAdj = [0,8,4,0xC]",
         "prog pan fb alg | op: DT ML TL KS AR AM DR SR SL RR"]
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
    print(f"!! {len(problems)} 个字段越界 (前 10 个已打印)")
else:
    print("OK: 128 条音色字段全部在 OPN 寄存器值域内")


# ---------- WOPN v2 输出 ----------
def u16be(v):
    return struct.pack(">H", v)

def u16le(v):
    return struct.pack("<H", v)

op_order = [0, 2, 1, 3]        # 驱动字段序 [0,+8,+4,+C] -> 硬件序 [0,+4,+8,+C]

def build_instrument(n, voice=None):
    if voice is None:
        voice = parse_rec(n)
    blank = is_blank(voice)

    body = bytearray(69)
    name = GM_NAMES[n].encode("ascii")[:31]
    body[0:32] = name.ljust(32, b"\0")
    
    # 【修复 1：计算正确的 note_offset (移调)】
    # 基准值 244，根据 FMOCTAVE 计算半音偏差
    octave_val = OCT[n] if OCT[n] > 0 else BASE_OCTAVE
    note_offset = round(12 * math.log2(BASE_OCTAVE / octave_val))
    # 限制在合理范围，避免越界 (WOPN 使用的是 16位 signed int)
    note_offset = max(-32768, min(32767, note_offset))
    body[32:34] = struct.pack(">h", note_offset)  
    
    body[34] = 0                               # percussion_key_number
    body[35] = voice["head"] & 0x3F            # fbalg（清 pan 位）
    body[36] = 0x80 if not blank else 0        # chip type = OPNA
    
    for w, k in enumerate(op_order):
        o = voice["ops"][k]
        off = 37 + w * 7
        
        # 1. 释放率 (RR) 补丁
        rr_val = o["rr"]
        if rr_val == 0 and not (16 <= n <= 23):
            rr_val = 1  # 改成 1，最慢的非零释放
            
        # 2. Sustain Level (SL) 补丁
        # SL 值越大，声音在按键时衰减得越深（越小）。为了听感，我们强制压低它！
        sl_val = o["sl"]
        if not (16 <= n <= 23) and not (112 <= n <= 127): 
            # 除了管风琴和打击乐，把 SL 最大限制在 4，保证松手时有足够的音量
            sl_val = min(sl_val, 4)
            
        # 3. D1R (Decay 1) 补丁
        # 如果原数据 D1R 太快（比如大于 15），我们把它强制减半，让声音掉得慢一点
        d1r_val = o["dr"]
        if not (16 <= n <= 23) and not (112 <= n <= 127):
             d1r_val = min(d1r_val, 15) # 或者除以 2: d1r_val // 2

        # 4. D2R 强制为 0，SR 保持原样
        dr2_val = 0
        sr_val = o["sr"] & 31

        body[off + 0] = ((o["dt"] & 7) << 4) | (o["ml"] & 15)          # 0x30 DT/ML
        body[off + 1] = min(o["tl"], 0x7F)                             # 0x40 TL
        body[off + 2] = ((o["ks"] & 3) << 6) | (o["ar"] & 31)          # 0x50 KS/AR
        body[off + 3] = (d1r_val & 0x7F)                               # 0x60 AM/D1R (使用了修修补补的d1r)
        body[off + 4] = ((dr2_val & 0xF) << 4) | (sr_val & 0x1F)       # 0x70 D2R/SR
        body[off + 5] = ((sl_val & 15) << 4) | (rr_val & 15)           # 0x80 SL/RR (使用了修修补补的sl)
        body[off + 6] = 0                                              # 0x90 SSG-EG
        
    if blank:
        body[65:67] = u16be(0)
        body[67:69] = u16be(0)
    else:
        body[65:67] = u16be(KEY_ON_DELAY_MS)
        body[67:69] = u16be(KEY_OFF_DELAY_MS)
    return bytes(body)

melodic = b"".join(build_instrument(n) for n in range(128))

percussion = b""
for n in range(128):
    blank = bytearray(69)
    blank[0:32] = b"(blank)".ljust(32, b"\0")
    percussion += bytes(blank)

out = bytearray()
out += b"WOPN2-B2NK\0"             # magic
out += u16le(2)                    # version = 2
out += u16be(1)                    # melodic banks = 1
out += u16be(1)                    # percussion banks = 1
out += bytes([0x10])               # LFO freq=0 | chip_type<<4 = OPNA
out += b"NECOPN GM".ljust(32, b"\0") + bytes([0, 0])
out += b"(empty)".ljust(32, b"\0") + bytes([0, 0])
out += melodic
out += percussion

path = os.path.join(OUT_DIR, "NECOPN_GM.wopn")
open(path, "wb").write(out)
print(f"WOPN v2 生成成功: {path} ({len(out)} 字节)")