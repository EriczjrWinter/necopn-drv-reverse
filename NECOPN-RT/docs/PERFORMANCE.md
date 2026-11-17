# 性能实测

> 命令里的 `song.mid`、`OPN_Chipset-main` 是示例路径，按你的环境替换。


测试机：i5 级 CPU，Windows 10/11 x64，Python 3.14，gcc 16.2（w64devkit）。
所有数字都是**最佳值**（重复跑取最小），单位 **ms CPU / 音频秒**；
`x 实时` = 1000 / ms。

## 1. 核心本身

| 核心 | 芯片数 | ms/音频秒 | x 实时 | 备注 |
|---|---:|---:|---:|---|
| Nuked OPN2（FM only，`opn2.dll`） | 1 | 49.6 | 20.2 | `-O2 -static`，每芯片 3 个音 |
| Nuked OPN2（单芯片 6 声道） | 1 | 49.6 | 20.2 | 同一颗芯片开满 6 通道，核心成本不变 |
| Nuked OPN2 | 2 | 97.5 | 10.3 | NECOPN 双芯片 |
| Nuked OPM（对照，`opm.dll`） | 1 | 320.1 | 3.12 | 和 OPMMIDI-RT 的 3.15x 基线一致 |
| Nuked OPM | 2 | 644.6 | 1.55 | — |
| fmgen（离线 `necopna_fmgen.dll`） | 1 | 1.5 | 672 | `-O2`，完整 OPNA |
| fmgen | 2 | 2.9 | **347** | 完整 OPNA |
| Nuked YM2608 **LLE**（`-O2`） | 1 | 4517 | **0.22** | 完整 LLE，实时不可行 |
| Nuked YM2608 LLE（`-O3 -march=native`） | 1 | 4243 | 0.24 | 编译优化到顶也只有 0.24x |
| Nuked YM2608 LLE（`-O2`） | 2 | 8641 | 0.12 | — |
| Nuked YM2608 LLE（chipset DLL，无优化） | 1 | 10235 | 0.10 | `libopnemuchipset.dll` |
| NP2 OPNA（chipset DLL，无优化） | 1 | 8.2 | 121.9 | fmgen 系 |
| MAME YM2608（chipset DLL，无优化） | 1 | 18.6 | 53.7 | GPL |
| YMFM OPNA（chipset DLL，无优化） | 1 | 83.6 | 12.0 | BSD |

复现：

```powershell
python tool/bench_core.py --seconds 20 --repeat 3   # Nuked OPN2 vs OPM
python tool/bench_chipset.py --seconds 2            # NP2/MAME/YMFM/LLE（chipset DLL）
gcc -O2 -o tool\bench_lle.exe tool\bench_lle.c `
    "OPN_Chipset-main\nuked_lle\nopna.c" `
    "OPN_Chipset-main\nuked_lle\fmopna_2608.c" `
    -I "OPN_Chipset-main\nuked_lle" -include stdio.h -include stdlib.h
.\tool\bench_lle.exe 2 1
```

> `opna.c` 里有作者加的调试 `printf`（每 44100 样本一次）且没有 `#include <stdio.h>`；
> 用 `-include stdio.h` 编译即可。这点打印对吞吐影响 <0.1%。

## 2. 端到端（驱动 + 2 芯片 + SSG/节奏 + 重采样）

`python rtplayer.py <midi> --bench N`，`sr_out=48000`：

| 后端 | MIDI | ms/音频秒 | x 实时 |
|---|---|---:|---:|
| fmgen | 5.mid 前 5s | 10.8 | **92.7** |
| fmgen | 灵长新益京 MORE_FM 前 8s | 11.2 | **89.5** |
| nuked1 | 5.mid 前 5s | 65.6 | **15.3** |
| nuked1 | 灵长新益京 MORE_FM 前 8s | 68.1 | **14.7** |
| nuked | 5.mid 前 5s | 119.2 | 8.4 |
| nuked | 灵长新益京 MORE_FM 前 8s | 121.2 | 8.3 |

`nuked1` 是单颗 Nuked OPN2 开满 6 个 FM 声道（unit1 走 bank 1），
比双芯片快 1.85x；响度和 `nuked` 对齐（5.mid RMS 262.7 vs 241.2，
鼓点段 861 vs 811）。想更快又不想用 fmgen 时选它。

端到端包含：SMF 事件派发、驱动寄存器写、2 颗芯片、SSG、节奏、Catmull-Rom
重采样、int16 混音。fmgen 的 Python 驱动/混音开销约 8 ms/s，核心本身只占 2.9 ms/s。

`Engine` 生产者线程实测 8.4x（fmgen）/ 6.7x（nuked）。这个数字被
`Engine._run` 里“缓冲够就 `sleep(2ms)`”的节流压低了，**不是**核心上限；
真实播放只需要 1x，缓冲水位始终在 1500–2000 帧（≈32–42 ms）。

## 3. 真实声卡

| 项目 | 结果 |
|---|---|
| 设备 | Realtek(R) Audio，Windows WASAPI 共享模式 |
| 采样率 / 块 | 48 000 Hz / 256 帧 |
| 延迟 | **22.0 ms** |
| 3 秒播放 | fmgen：0 underrun / 0 xrun |
| 3 秒播放 | nuked：0 underrun / 0 xrun |

```
python rtplayer.py "song.mid" --duration 3
python rtplayer.py "...MORE_FM.mid" --duration 3 --core nuked
```

打包后的单文件 **GUI** EXE（PyInstaller onefile，24.8 MB）：

| 项目 | 结果 |
|---|---|
| 启动 / `--selfcheck` | OK（结果写进 `dist\selfcheck.log`） |
| GUI 播放（同一 Session/Engine） | 22.0 ms 延迟，0 underrun |
| CLI 端到端（`python rtplayer.py --bench`） | 92.7x |

EXE 只带 `opn2.dll`、`ssg.dll`、`core/necopna_fmgen.dll`，不带采样。

## 4. 结论

- 实时余量：fmgen **~90x**、nuked1 **~15x**、nuked 组合 **~8x**，
  都远高于 1x；低配机（1/10 性能）也够。
- Nuked 完整 LLE 的 **0.22x**（`-O3 -march=native` 也只到 0.24x）是架构性的；
  它的 FM/SSG/ADPCM 都在 16M clock/s 上逐时钟跑，不能靠编译选项救。
  想要“Nuked 但不是 LLE”，正确做法就是 `nuked1`/`nuked`：
  Nuked OPN2 的样本级 FM + emu2149 + PCM 节奏。
- `-O2` 仍是甜点；`opn2.dll`/`ssg.dll` 必须 `-static`，否则要带 MinGW 运行时。
- 采样率统一在 55 555 Hz 渲染，最后一次性重采样到 48 kHz；不要每块重采样。
