# 第三方组件与数据

本项目**不打包任何音频采样/ROM**。下面是各目录/文件的来源与许可证。

## 核心

| 组件 | 位置 | 许可证 | 说明 |
|---|---|---|---|
| Nuked OPN2 (YM3438) | `core-opn2/ym3438.c`, `ym3438.h` | **LGPL-2.1** | 上游 `nukeykt/Nuked-OPN2` v1.0.9；`core-opn2/LICENSE` 为 LGPL 全文 |
| OPN2 wrapper | `core-opn2/opn2_wrapper.c` | MIT | 本项目原创（C ABI + 时分复用求和 + 写队列） |
| emu2149 | `core/emu2149-main/` | **MIT** | 上游 `sekigon-gonnoc/emu2149`；`LICENSE` 随带 |
| fmgen / necopna_fmgen.dll | `core/necopna_fmgen.dll` | **cisc 自由软件条款** | 来自 NP21/W 的 fmgen；`core/LICENSE-fmgen.txt` 为原文。允许修改/嵌入/再分发，要求署名、免费分发、注明改动、随带许可证原文；**商用需作者同意** |
| ssg wrapper | `core/emu2149-main/ssg_wrapper.c` | MIT | 本项目在原 wrapper 上增加 `ssg_create_ex(clock, rate)` |

## 只作参考、不随项目分发

| 组件 | 位置 | 许可证 | 说明 |
|---|---|---|---|
| Nuked YM2608 LLE | `OPN_Chipset-main\nuked_lle` | **GPL-2.0+** | 完整 OPNA LLE；内置 `rss_rom[8192]`（Yamaha 节奏 ROM）。实测 0.22x 实时，不用于播放器；只在文档/基准脚本里引用 |
| NP2 / MAME / YMFM OPNA | `OPN_Chipset-main` | 各自（MIT / GPL / BSD） | 只用于 `tool/bench_chipset.py` 的对照基准 |
| 离线逆向项目 | `..\necopn-drv-reverse-main` | 见其 README | 提供 NECOPN.DRV 行为、音色库与 fmgen 参考实现 |

## 数据

| 文件 | 说明 | 分发 |
|---|---|---|
| `fixed_bank.py` | 从 NECOPN.DRV 提取的 128 条 GM 音色**寄存器参数**（不是录音） | 随项目 |
| `RHY/*.wav` | YM2608 节奏 ROM 的 PCM 化结果，版权 Yamaha/NEC | **不随项目**，用户自备 |
| `YM2608_ADPCM_ROM.BIN` | 8 KB 节奏 ROM 原样 | **不随项目**，用户自备 |

## Python 依赖

- `numpy`（BSD）
- `sounddevice`（MIT；内含 PortAudio，MIT）
- 打包时 PyInstaller 需要 `--collect-data sounddevice`，否则会缺 PortAudio DLL。
