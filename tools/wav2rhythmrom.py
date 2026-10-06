# -*- coding: utf-8 -*-
"""
wav2rhythmrom.py —— 把 WAV 鼓样本打包成 YM2608 打击乐 ROM（ADPCM-A，8192 字节）。

芯片内那 8KB ROM 的布局是**硬连线**的（ym2608::reset() 预置，
vendor/ymfm/src/ymfm_opn.cpp:1002-1007），六个乐器各占固定区间：

    槽位  乐器     起始    结束    字节   通道  回放速率(8MHz 时钟)
      0   bd      0x0000  0x01BF   448    ch0   clock/432 = 18518.52 Hz
      1   sd      0x01C0  0x043F   640    ch1   clock/432 = 18518.52 Hz
      2   top     0x0440  0x1B7F  5952    ch2   clock/432 = 18518.52 Hz
      3   hh      0x1B80  0x1CFF   384    ch3   clock/432 = 18518.52 Hz
      4   tom     0x1D00  0x1F7F   640    ch4   clock/864 =  9259.26 Hz
      5   rim     0x1F80  0x1FFF   128    ch5   clock/864 =  9259.26 Hz

注意 ch4/ch5（tom/rim）**只有其它通道一半的回放速率**（ymfm_opn.cpp:1397-1400：
ADPCM-A 每 3 个 FM 时钟走一拍，ch4/5 每两拍才走一拍），所以同样字节数能装
两倍长的声音。速率是实测核对过的（见 STATUS.md §9.2）。

因此本工具做四件事：
  1. 读 WAV（8/16/24/32bit PCM，立体声自动混成单声道）；
  2. 按该槽位的回放速率**重采样**（保音高），窗函数 sinc 插值；
  3. 放不下的尾部按 --fit 处理（默认 fade 截断+淡出；speed 则整体加速塞进去），
     装不满的用编码后的 0 填充（ADPCM 没有"静音 nibble"，补 0 会让解码器在
     ±2/2048 抖动，等效静音）；
  4. 编码成 YM2608 ADPCM-A 4bit 流（编码器是 ymfm_adpcm.cpp:151-213 解码器的
     逆运算），写成 8192 字节 ROM，可直接给 `player.py --rhythm-rom` 用。

用法：
    python tools\\wav2rhythmrom.py D:\\galgame\\drum_samples -o out\\my_rhythm.bin
    python tools\\wav2rhythmrom.py --bd a.wav --sd b.wav --top c.wav ^
            --hh d.wav --tom e.wav --rim f.wav -o out\\my_rhythm.bin
"""
from __future__ import annotations

import argparse
import math
import os
import struct
import sys
import wave

# ---------------------------------------------------------------- 常量
# YM2608 ADPCM-A 步进表（ymfm_adpcm.cpp:192-201）
STEPS = [
    16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73,
    80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279,
    307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
    1060, 1166, 1282, 1411, 1552,
]
STEP_INC = [-1, -1, -1, -1, 2, 5, 7, 9]     # ymfm_adpcm.cpp:210

# 槽位：名字、起始、结束（含）、回放速率分子分母（rate = clock / div）
# div=432 -> ch0-3；div=864 -> ch4-5（半速）
SLOTS = [
    ("bd",  0x0000, 0x01BF, 432),
    ("sd",  0x01C0, 0x043F, 432),
    ("top", 0x0440, 0x1B7F, 432),
    ("hh",  0x1B80, 0x1CFF, 432),
    ("tom", 0x1D00, 0x1F7F, 864),
    ("rim", 0x1F80, 0x1FFF, 864),
]
ROM_SIZE = 0x2000

# 文件名关键字 -> 槽位（按顺序匹配，命中即停）
NAME_HINTS = {
    "bd":  ("bd", "bass", "kick"),
    "sd":  ("sd", "snare"),
    "top": ("top", "cym", "crash"),
    "hh":  ("hh", "hat"),
    "tom": ("tom",),
    "rim": ("rim", "rym"),
}


# ---------------------------------------------------------------- WAV 读取
def read_wav(path):
    """返回 (samples: list[float], rate: int)，samples 归一化到 ±1.0。"""
    with wave.open(path, "rb") as w:
        nch = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    if width == 1:                                   # 8bit 无符号
        vals = [(b - 128) / 128.0 for b in raw]
    elif width == 2:
        vals = [v / 32768.0 for v in struct.unpack("<%dh" % (len(raw) // 2), raw)]
    elif width == 3:
        vals = []
        for i in range(0, len(raw), 3):
            v = raw[i] | (raw[i + 1] << 8) | (raw[i + 2] << 16)
            if v & 0x800000:
                v -= 1 << 24
            vals.append(v / 8388608.0)
    elif width == 4:
        vals = [v / 2147483648.0 for v in struct.unpack("<%di" % (len(raw) // 4), raw)]
    else:
        raise ValueError("%s: 不支持的采样宽度 %d 字节" % (path, width))
    if nch > 1:                                      # 立体声 -> 单声道平均
        mono = []
        for i in range(0, len(vals), nch):
            mono.append(sum(vals[i:i + nch]) / nch)
        vals = mono
    return vals, rate


# ---------------------------------------------------------------- 重采样
def _sinc(x):
    if x == 0.0:
        return 1.0
    px = math.pi * x
    return math.sin(px) / px


def resample(x, in_rate, out_rate, taps=16):
    """窗函数 sinc 重采样（保音高）。taps 为半宽核点数（在输入序列单位上按比例放大）。"""
    if not x:
        return []
    if in_rate == out_rate:
        return list(x)
    ratio = in_rate / float(out_rate)          # >1 = 降采样
    fc = 0.5 / max(1.0, ratio)                 # 截止频率（输入采样率归一）
    half = taps * max(1.0, ratio)              # 核半宽（输入样本单位）
    n_out = max(1, int(round(len(x) * out_rate / float(in_rate))))
    out = []
    for i in range(n_out):
        t = i * ratio
        k0 = int(math.floor(t - half))
        k1 = int(math.ceil(t + half))
        acc = 0.0
        wsum = 0.0
        for k in range(k0, k1 + 1):
            d = k - t
            if abs(d) > half:
                continue
            w = 2.0 * fc * _sinc(2.0 * fc * d)
            w *= 0.42 + 0.5 * math.cos(math.pi * d / half) + 0.08 * math.cos(2.0 * math.pi * d / half)
            if 0 <= k < len(x):
                acc += x[k] * w
            wsum += w
        out.append(acc / wsum if wsum else 0.0)
    return out


# ---------------------------------------------------------------- ADPCM
def encode_adpcm(target):
    """把 ±2047 的目标序列编码成 ADPCM-A 字节流（高 nibble 在前）。"""
    out = bytearray((len(target) + 1) // 2)
    acc = 0
    step = 0
    for index, raw in enumerate(target):
        want = int(raw)
        if want > 2047:
            want = 2047
        elif want < -2047:
            want = -2047
        best = 0
        bestcost = None
        for cand in range(16):
            delta = (2 * (cand & 7) + 1) * STEPS[step] // 8
            if cand & 8:
                delta = -delta
            newacc = (acc + delta) & 0xFFF
            value = newacc - 4096 if newacc >= 2048 else newacc
            # 误差按**解码出来的值**线性比较，不做 mod 4096 环绕：
            # 解码端是符号扩展的 12 位，累加器绕回会输出反相的巨幅值，
            # 那种候选对听感是灾难（满幅时会让波形在 ±2047 之间跳变）。
            diff = want - value
            cost = -diff if diff < 0 else diff
            if bestcost is None or cost < bestcost:
                bestcost = cost
                best = cand
        delta = (2 * (best & 7) + 1) * STEPS[step] // 8
        if best & 8:
            delta = -delta
        acc = (acc + delta) & 0xFFF
        step += STEP_INC[best & 7]
        if step < 0:
            step = 0
        elif step > 48:
            step = 48
        if (index & 1) == 0:
            out[index // 2] = best << 4
        else:
            out[index // 2] |= best & 0x0F
    return bytes(out)


def decode_adpcm(data, count):
    """YM2608 ADPCM-A 解码（ymfm_adpcm.cpp:151-213），返回 ±2048 整数序列。"""
    out = []
    acc = 0
    step = 0
    for byte in data:
        for nib in (byte >> 4, byte & 0x0F):
            if len(out) >= count:
                return out
            delta = (2 * (nib & 7) + 1) * STEPS[step] // 8
            if nib & 8:
                delta = -delta
            acc = (acc + delta) & 0xFFF
            step += STEP_INC[nib & 7]
            if step < 0:
                step = 0
            elif step > 48:
                step = 48
            out.append(acc - 4096 if acc >= 2048 else acc)
    while len(out) < count:
        out.append(0)
    return out


# ---------------------------------------------------------------- 主流程
def trim_silence(samples, floor, max_lead=0.05, rate=1.0):
    """去掉头尾低于 ADPCM 量化底噪(±floor)的静音。返回 (裁剪后的序列, 头部裁掉, 尾部裁掉)。
    ADPCM 没有"绝对静音"，解码端底噪约 ±2/2048，所以低于底噪的部分留着也没用。"""
    lead = 0
    limit = int(max_lead * rate)
    while lead < len(samples) and lead < limit and abs(samples[lead]) <= floor:
        lead += 1
    trail = len(samples)
    while trail > lead and abs(samples[trail - 1]) <= floor:
        trail -= 1
    return samples[lead:trail], lead, len(samples) - trail


def fit_samples(samples, capacity, fade_ms, rate, mode):
    """把重采样后的样本塞进容量为 capacity 的槽位。
    返回 (samples, action)。mode: fade=截断+淡出, speed=整体加速塞入。"""
    if len(samples) <= capacity:
        return samples, ("ok" if samples else "empty")
    if mode == "speed":
        return resample(samples, len(samples), capacity), "speed"
    fade = min(int(rate * fade_ms / 1000.0), len(samples) // 2, capacity)
    out = samples[:capacity]
    if fade > 0:
        start = capacity - fade
        for i in range(fade):
            out[start + i] *= (fade - i) / float(fade + 1)
    return out, "fade"


def build_rom(inputs, clock, gain, fade_ms, fit_mode, verbose=True):
    """inputs: {槽位名: wav路径}。返回 (rom bytes, 报告行 list)。"""
    # 1) 读入 + 重采样
    per_slot = {}
    peak = 0.0
    for name, start, end, div in SLOTS:
        path = inputs.get(name)
        if not path:
            raise ValueError("缺少乐器 %s 的 WAV" % name)
        src, srate = read_wav(path)
        if not src:
            raise ValueError("%s 是空的" % path)
        rate = clock / float(div)
        rs = resample(src, srate, rate)
        per_slot[name] = {"path": path, "src": src, "src_rate": srate,
                          "rate": rate, "sig": rs, "capacity": (end - start + 1) * 2}
        peak = max(peak, max(abs(v) for v in rs))

    # 2) 增益：默认所有乐器同一增益推到 ADPCM 满度 ±2047（保留乐器间相对响度）
    if gain is None:
        scale = 2047.0 / peak if peak > 0 else 1.0
    else:
        scale = 10 ** (gain / 20.0) * (2047.0 / peak if peak > 0 else 1.0)

    rom = bytearray(ROM_SIZE)
    report = []
    floor = 2.5                      # ADPCM 解码底噪约 ±2，比它还小的样本等于静音
    for name, start, end, div in SLOTS:
        info = per_slot[name]
        target = [v * scale for v in info["sig"]]
        capacity = info["capacity"]
        target, lead_cut, trail_cut = trim_silence(target, floor, rate=info["rate"])
        before = len(target)
        target, action = fit_samples(target, capacity, fade_ms, info["rate"], fit_mode)
        n_real = len(target)
        if n_real < capacity:
            target = target + [0.0] * (capacity - n_real)
        data = encode_adpcm(target)
        rom[start:end + 1] = data[:end - start + 1]

        # 回环校验：解码回来与目标比信噪比（只算真实样本区，不含补零段）
        dec = decode_adpcm(data, capacity)[:n_real]
        num = sum(d * d for d in dec)
        err = sum((d - t) ** 2 for d, t in zip(dec, target[:n_real]))
        snr = 10 * math.log10(num / err) if err > 0 and num > 0 else 99.0

        note = action + ("+pad" if n_real < capacity else "")
        report.append({
            "name": name, "path": info["path"], "src_rate": info["src_rate"],
            "src_len": len(info["src"]), "rate": info["rate"],
            "need": before, "cap": capacity, "note": note, "snr": snr,
            "trim": (lead_cut, trail_cut),
        })
        if verbose:
            over = before > capacity
            trim = ""
            if lead_cut or trail_cut:
                trim = "  (裁掉静音 %d/%d 样本)" % (lead_cut, trail_cut)
            print("  %-3s %-28s %6.1fms@%4.1fk -> %6.1fms@%5.1fHz  槽位 %5d/%5d 样本  %-9s SNR %4.1f dB%s%s"
                  % (name, os.path.basename(info["path"]),
                     len(info["src"]) / info["src_rate"] * 1000, info["src_rate"] / 1000.0,
                     before / info["rate"] * 1000, info["rate"], before, capacity,
                     note, snr, "   <-- 超出，已处理" if over else "", trim))
    return bytes(rom), report


def guess_inputs(folder):
    """按文件名关键字猜 6 个乐器。"""
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".wav"))
    inputs = {}
    for f in files:
        stem = os.path.splitext(f)[0].lower()
        for name, hints in NAME_HINTS.items():
            if name in inputs:
                continue
            if any(h in stem for h in hints):
                inputs[name] = os.path.join(folder, f)
                break
    return inputs, files


def main(argv=None):
    ap = argparse.ArgumentParser(description="WAV 鼓样本 -> YM2608 打击乐 ROM (ADPCM-A 8KB)")
    ap.add_argument("folder", nargs="?", help="存放 6 个 WAV 的目录（按文件名自动识别乐器）")
    ap.add_argument("-o", "--out", required=True, help="输出 ROM 文件（8192 字节）")
    for name, _, _, _ in SLOTS:
        ap.add_argument("--" + name, help="%s 的 WAV（覆盖自动识别）" % name)
    ap.add_argument("--clock", type=int, default=8_000_000, help="OPNA 时钟（默认 8000000）")
    ap.add_argument("--gain-db", type=float, default=None,
                    help="额外增益 dB（默认 0：按最响的样本推到 ADPCM 满度）")
    ap.add_argument("--fade-ms", type=float, default=8.0,
                    help="超出槽位被截断时的淡出长度（默认 8ms）")
    ap.add_argument("--fit", choices=("fade", "speed"), default="fade",
                    help="放不下时：fade=截断+淡出（默认），speed=整体加速塞进槽位（会升调）")
    args = ap.parse_args(argv)

    inputs = {}
    if args.folder:
        if not os.path.isdir(args.folder):
            ap.error("目录不存在: %s" % args.folder)
        inputs, files = guess_inputs(args.folder)
        print("目录 %s：%d 个 WAV，识别出 %d 个乐器" % (args.folder, len(files), len(inputs)))
    for name, _, _, _ in SLOTS:
        v = getattr(args, name)
        if v:
            inputs[name] = v
    missing = [n for n, _, _, _ in SLOTS if n not in inputs]
    if missing:
        ap.error("缺少乐器: %s（可用 --名字 路径 指定）" % ", ".join(missing))

    print("打包中……")
    rom, report = build_rom(inputs, args.clock, args.gain_db, args.fade_ms, args.fit)

    outdir = os.path.dirname(os.path.abspath(args.out))
    if outdir and not os.path.isdir(outdir):
        os.makedirs(outdir, exist_ok=True)
    with open(args.out, "wb") as f:
        f.write(rom)
    print("写出 %s（%d 字节）" % (args.out, len(rom)))
    for r in report:
        if r["need"] > r["cap"]:
            print("  ! %s 槽位超出 %.0f%%：%d > %d 样本（%.1fms > %.1fms），已按 --fit %s 截掉尾部"
                  % (r["name"], (r["need"] - r["cap"]) * 100.0 / r["cap"], r["need"], r["cap"],
                     r["need"] / r["rate"] * 1000, r["cap"] / r["rate"] * 1000, args.fit))
    print("用法: python player.py song.mid -o out.wav --rhythm-rom \"%s\"" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
