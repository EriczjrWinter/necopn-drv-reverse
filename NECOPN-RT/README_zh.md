# NECOPN-RT — OPNA (YM2608) 实时播放器

基于 [`necopn-drv-reverse-main`](../necopn-drv-reverse-main) 的 NECOPN.DRV 逆向成果，
把 **离线渲染** 搬成 **实时播放**：MIDI → 寄存器写 → YM2608 → 55 555 Hz → 48 kHz → 声卡。

> 这个项目是 `OPMMIDI-RT`（OPM 实时版）的姊妹项目，不改动那边任何文件。

## 现在能做什么

```powershell
# 图形界面（默认 fmgen 完整 OPNA）
python app.py
python app.py --lang en
python app.py --selfcheck

# 命令行
python rtplayer.py "某个.mid"                 # 默认：fmgen 完整 OPNA，实时播放
python rtplayer.py "某个.mid" --ssg-only      # 仅 SSG 鼓
python rtplayer.py "某个.mid" --rhythm-dir "" # 没有 RHY 也能出声（退回 SSG 合成鼓）
python rtplayer.py "某个.mid" --core nuked    # Nuked 双芯片（3+3）
python rtplayer.py "某个.mid" --core nuked1   # Nuked 单芯片（6 声道，快 1.85x）
python rtplayer.py "某个.mid" --export out.wav
python rtplayer.py "某个.mid" --bench 20
python tool/selftest.py --midi "某个.mid" --limit 3
```

GUI 支持：**循环播放、指定 RHY 目录、仅 SSG 鼓、导出 WAV、中英文切换**、
音量实时调节、核心选择（fmgen / nuked）。**没有音色表切换**——NECOPN.DRV 的
128 条音色是驱动固化的，不需要也不应该换。

本机实测（i5 级，48 kHz，WASAPI，block=256）：

- 延迟 **22.0 ms**
- 连续播放 3 s：**0 underrun / 0 xrun**（fmgen 与 nuked 两个后端都测过）
- 打包成单文件 GUI EXE 后：启动 / `--selfcheck` 正常；播放链与 CLI 相同
  （22.0 ms，0 underrun）

### 单文件 EXE

```powershell
.\build_exe.ps1                 # 窗口版（默认）
.\build_exe.ps1 -Console        # 带控制台，排错用
.\dist\NECOPN-RT.exe            # 打开 GUI
.\dist\NECOPN-RT.exe --selfcheck   # 结果写进 dist\selfcheck.log
```

EXE 里只带 `opn2.dll` / `ssg.dll` / `core/necopna_fmgen.dll` 三个原生库，
**不带任何 WAV/ROM**；在界面上指定自己的 RHY 目录即可，或者勾“仅 SSG 鼓”。

## 三个实时后端

| `--core` | 组成 | 端到端吞吐 | 适用 |
|---|---|---:|---|
| `fmgen`（默认） | fmgen 一颗完整 OPNA：FM + SSG + ADPCM-B + 节奏 | **92.7x 实时** | 默认；和离线参考同核，A/B 最接近 |
| `nuked1` | **单颗 Nuked OPN2 的 6 个 FM 声道** + emu2149 + PCM 节奏 | **15.3x 实时** | Nuked 单芯片；比双芯片快 1.85x |
| `nuked` | 两颗 Nuked OPN2（3+3 声道）+ emu2149 + PCM 节奏 | 8.3x 实时 | Nuked 双芯片，保持 NECOPN 原始的 2×3 结构 |

`nuked1` 把 NECOPN 的两个逻辑 unit（各 3 声部）映射到一颗 YM2608 的
6 个 FM 通道：unit0 用物理 ch0-2，unit1 用物理 ch3-5（bank 1，key-on
通道码 4-6）。响度已和双芯片对齐（5.mid RMS 262.7 vs 241.2，鼓点段
861 vs 811），速度约 1.85x。这是最接近“Nuked 但非 LLE”的单芯片 OPNA。

两者共用同一套 `NecopnSystem`（逆向出的驱动行为）、`Renderer`（采样级事件 +
Timer-A tick）、`Resampler`（55 555 → 48 000）和 `Engine`（生产者线程）。

### 为什么不是直接用 Nuked OPNA LLE？

`OPN_Chipset-main\nuked_lle` 里确实有完整的 Nuked YM2608 LLE（FM+SSG+ADPCM+节奏），
但实测 **单芯片 -O2 要 4517 ms CPU / 音频秒 = 0.22x 实时**，双芯片 0.12x，
实时版跑不动；而且它是 **GPLv2+**，`fmopna_rom.h` 里还内嵌了 8 KB 的节奏 ROM
（`rss_rom[8192]`），和“不打包采样 / MIT 分发”冲突。

所以：
- **实时**用 fmgen 或 Nuked 组合；
- **Nuked LLE** 只留作离线参考/校验（`tool/bench_lle.c`、`tool/bench_chipset.py`）。

完整数据见 [docs/PERFORMANCE.md](docs/PERFORMANCE.md)。

## 采样率：55 555 / 55 466 / 48 000

YM2608 的 FM 原生速率 = 主时钟 / 144：

| 主时钟 | 原生速率 | 说明 |
|---:|---:|---|
| 8 000 000 | 55 555 Hz | 离线逆向项目一直用的值（默认） |
| 7 987 200 | **55 466 Hz** | PC-98 的 3.9936 MHz × 2，`OPN_Chipset` 里的精确值 |

不管哪个，都不是 48 000。播放链路是：

```
芯片 55 555 Hz  →  4 点 Catmull-Rom 重采样  →  48 000 Hz  →  WASAPI
```

想用精确的 55 466 Hz：`--clock 7987200`（两个后端都支持）。
音高差 0.16%（2.8 音分），默认保持 8 MHz 是为了和离线参考逐样本对得上。
细节见 [docs/SAMPLE_RATE.md](docs/SAMPLE_RATE.md)。

## 节奏 / ADPCM：不打包任何采样

NECOPN.DRV 的打击乐走 YM2608 节奏通道（ADPCM-A）。本项目 **不随程序分发任何采样**：

| 核 | 节奏来源 | 缺 RHY 时 |
|---|---|---|
| `fmgen` | `nopna_set_rhythm_rom(dir)` 读 6 个 WAV，芯片原生节奏 | SSG 合成鼓 |
| `nuked` | `RhythmCore` 读 6 个 WAV，按 0x10/0x11/0x18-0x1D 寄存器语义播放 | SSG 合成鼓 |

RHY 目录需要这 6 个文件（大小写不敏感）：

```
2608_bd.wav  2608_sd.wav  2608_top.wav  2608_hh.wav  2608_tom.wav  2608_rim.wav
```

没有目录时自动退回 SSG 合成鼓（底鼓/军鼓/踩镲），不会静音整首歌。
细节、版权与 A/B 校准见 [docs/RHYTHM.md](docs/RHYTHM.md)。

## 目录

```
app.py               tkinter 图形界面（循环 / RHY / 仅 SSG / 导出 / 中英文）
rtplayer.py          CLI：播放 / 导出 / 基准 / 设备列表
session.py           Config + Session：核心、驱动、SSG/节奏、Renderer、Engine
rt_engine.py         Renderer（采样级事件 + Ticker）/ Resampler / Engine
necopn_driver.py     NECOPN.DRV 行为（逆向，已加 SSG 兜底鼓）
necopn_core.py       fmgen OPNA 核心的 ctypes 绑定（YmfmCore）
opn2_core.py         Nuked OPN2 绑定 + CoreMixer + SsgSink
rhythm_core.py       YM2608 节奏寄存器 → PCM 采样（Nuked 路径）
ssg_core.py          emu2149 绑定（Nuked 路径）
fixed_bank.py        从 NECOPN.DRV 提取的 128 条 GM 音色（硬编码）
smf.py               SMF 解析
player_offline.py    离线参考实现（保留，供 A/B）
core-opn2/           Nuked OPN2 源码 + wrapper（LGPL-2.1）
core/                emu2149（MIT）+ necopna_fmgen.dll（cisc 自由软件条款）
tool/                自检 / 基准 / A/B / 诊断
docs/                性能、采样率、节奏说明
```

## 构建

需要 Python 3.10+、`numpy`、`sounddevice`，以及 gcc（只有重编 DLL 才需要）。

```powershell
pip install -r requirements.txt

# 重编 Nuked OPN2 / emu2149（可选；仓库已带预编译 DLL）
gcc -O2 -shared -static -o opn2.dll core-opn2\ym3438.c core-opn2\opn2_wrapper.c
gcc -O2 -shared -static -o ssg.dll core\emu2149-main\emu2149.c core\emu2149-main\ssg_wrapper.c
```

## 已知限制

- fmgen 的 SSG 在“没有 RHY、走 SSG 兜底鼓”时，输出波形对 `generate()` 的分块大小
  有极轻微差异（同一段音频最大差 ~10/32767）；有 RHY 时逐样本块无关。
- 跳转（seek）是“重建芯片 + 重放状态 + 重起按住音”的近似，不恢复相位/包络。
- 软件 LFO 默认关闭（和离线版一致），`--lfo` 可开；tick 用 Timer-A 836.55 Hz。
- 只验证了 WASAPI 48 kHz；其它采样率/设备需要自己试。

## 许可证

- 本项目代码：MIT（见 [LICENSE](LICENSE)）。
- Nuked OPN2：LGPL-2.1（`core-opn2/LICENSE`）。
- emu2149：MIT（`core/emu2149-main/LICENSE`）。
- fmgen / necopna_fmgen.dll：cisc 的自由软件条款（非商用、保留版权与许可证原文，
  见 `core/LICENSE-fmgen.txt`）。
- Nuked YM2608 LLE：GPLv2+，**不随本项目分发**，只在文档/基准里引用。

第三方组件汇总见 [NOTICE.md](NOTICE.md)。
