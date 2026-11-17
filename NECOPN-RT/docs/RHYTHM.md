# 节奏 / ADPCM 通道：方案与版权

## 1. 硬件事实

YM2608 的节奏通道是 **ADPCM-A**，6 件乐器：

| index | 乐器 | 寄存器 0x18+index |
|---:|---|---|
| 0 | Bass Drum | 0x18 |
| 1 | Snare Drum | 0x19 |
| 2 | Top Cymbal | 0x1A |
| 3 | Hi-Hat | 0x1B |
| 4 | Tom | 0x1C |
| 5 | Rim Shot | 0x1D |

驱动（NECOPN.DRV）只写三个寄存器：

```
0x18+i = 0xC0 | level    # level 0..31，越高越响；bit6-7 = 声像（3 = 双声道）
0x10   = 1 << i          # key on，触发第 i 件
0x11   = 0x30            # 总电平（取反后是衰减码）
```

`level` 和 `0x11` 都是**衰减码**：`~data & 31` / `~data & 63`，数值越大越轻，
每单位 0.75 dB（fmgen 的 `tltable` 一个 index = 0.75 dB）。

## 2. 三条实现路径

### A. fmgen 原生节奏（默认 `--core fmgen`）

`core/necopna_fmgen.dll` 的 `nopna_set_rhythm_rom(dir)` 会把 6 个 WAV 装进
fmgen 的 `OPNA::Rhythm`，之后驱动照常写 0x10/0x11/0x18-0x1D，芯片自己混音。
这是**和离线参考完全相同的路径**（同一颗 DLL、同一份采样、同一套寄存器语义），
所以 A/B 最接近；单次 BD 的峰值/RMS 与离线版逐位对得上。

### B. Nuked 组合的 PCM 节奏（`--core nuked`）

Nuked-OPN2 只做 FM，没有 ADPCM-A。`rhythm_core.RhythmCore` 实现同样的寄存器接口：

- `0x10` → 触发对应采样（pos=0）
- `0x18+i` → 存 `level = ~data & 31`、`pan = data>>6`
- `0x11` → 存 `total = ~data & 63`
- 增益 `10^(-(total+level)*0.75/20)`，`pan=3` 双声道
- 采样在**加载时**重采样到芯片速率（np.interp），实时只做整数 pos 前进

校准（fmgen 原生 vs RhythmCore，单次击打，1 秒）：

| 乐器 | fmgen peak/rms | RhythmCore peak/rms |
|---|---|---|
| BD | 6370 / 444.4 | 6402 / 446.2 |
| SD | 6237 / 279.9 | 6256 / 278.8 |
| TOP | 6888 / 265.4 | 6795 / 247.4 |
| HH | 6398 / 274.8 | 6162 / 243.1 |
| TOM | 6711 / 450.9 | 6586 / 451.7 |
| RIM | 6888 / 208.6 | 6559 / 207.5 |

8 秒鼓点密集段：fmgen 节奏净增量 RMS 891.4，RhythmCore 893.7。

### C. 没有 RHY 时的 SSG 兜底

`necopn_driver._play_ssg_drum` 把六件乐器映射到 YM2149 三个声部：
底鼓/筒鼓 → Tone A + 硬件包络；军鼓 → Tone B + Noise B；镲/踩镲/rim → Noise C。
- fmgen 路径：`SsgSink` 直接写 fmgen 内置 SSG 的 0x00-0x0F；
- nuked 路径：写独立的 `emu2149`（2 MHz / 55 555 Hz）。

这条路径**不需要任何采样**，所以“程序不打包采样”成立。

## 3. 为什么不用真正的 ADPCM-A LLE

`OPN_Chipset-main\nuked_lle` 的 Nuked YM2608 LLE 确实带完整 ADPCM-A + 节奏，
但：

1. **实时不可行**：单芯片 `-O2` 4517 ms/音频秒（0.22x），双芯片 0.12x。
2. **许可证**：GPLv2+，会传染整个播放器。
3. **内置 ROM**：`fmopna_rom.h` 里是 `rss_rom[8192]`，就是 8 KB 的 YM2608 节奏 ROM；
   随程序分发等于打包了 Yamaha 的采样数据，和“不打包采样”冲突。

所以 LLE 只作为离线参考/校验（`tool/bench_lle.c`、`tool/bench_chipset.py`）。

## 4. 版权

- 6 个 WAV 是 YM2608 节奏 ROM 的 PCM 化结果，版权归 Yamaha/NEC；
- 本项目**不打包**任何 WAV/ROM/BIN；
- 用户把自己的 `RHY/` 目录传给 `--rhythm-dir`；
- 仓库里只有代码和从 DRV 提取的 `fixed_bank.py` 音色参数（寄存器值，不是录音）。
