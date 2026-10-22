# NECOPN.DRV 逆向工程与 Python 渲染器

> **语言**: [[English](README.md) | 简体中文]

本项目是对 NEC PC-9821 (Windows 95) 专属 MIDI 驱动 `NECOPN.DRV` 的一次深度逆向与行为复现尝试。项目通过 Python 结合底层 C++ 核心，实现了一套离线渲染方案，旨在尽可能还原当年实机的听感与声部调度逻辑。

> **🎯 项目状态**：目前在音高、包络、通道分配和鼓组行为上已基本完全复现实机效果。软件 LFO 的明显听感问题（电磁噪音、过度颤动）已经解决，但仍可能存在尚未探明的怪癖，因此默认关闭。
> 
> **📦 开箱即用**：仓库已内置逆向提取的硬编码音色库 `fixed_bank.py`，**你不需要原版 `NECOPN.DRV` 即可直接运行播放器。**

## 🏗️ 复现架构与核心说明

**1. 关于"双核心"的软件映射**
PC-98 真实硬件虽然搭载了 YM2608 (OPNA) 芯片，但官方驱动在软件层为了规避芯片特性和干扰，严格限制了单颗芯片只使用 CH0-CH2。因此，本项目在复现时采用了 **2 个核心实例 × 每核 3 个通道（共 6 声部）** 的软件架构，完美映射了原驱动的逻辑，而非硬件物理上的 12 声道。

**2. 为什么从 ymfm 换到了 fmgen？**
在初期开发中，本项目使用 `ymfm` 作为底层音频合成核心。但在后续与实机录音对比时，发现 `ymfm` 在某些长音延音（如 SR 值较低的音色）的表现与预期期望有偏差。为了追求更极致的实机听感，最终将底层封装替换为 `NP21/W` 模拟器所使用的 `fmgen` 核心。
*注：为了保持 Python 侧代码接口兼容，`necopn_core.py` 中的封装类名仍保留为 `YmfmCore`，但实际加载的是 `necopna_fmgen.dll`。*

## 📦 资源准备

1. **内置音色库**
   * 128 个 GM 音色已通过逆向提取并硬编码在 `fixed_bank.py` 中，无需任何外部库文件。
   * *（可选）* 如果你拥有原版 `NECOPN.DRV`，将其放在根目录下，播放器会自动优先从原文件读取以进行验证。
2. **原生打击乐采样 (`RHY` 文件夹)**
   * **获取**：受版权限制，你需要自行准备 6 个 16-bit PCM 格式的 WAV 文件，文件名必须严格为：`2608_bd.wav`、`2608_sd.wav`、`2608_top.wav`、`2608_hh.wav`、`2608_tom.wav`、`2608_rim.wav`。
   * **放置**：建议放在一个文件夹（如 `RHY/`）中。运行时**必须通过 `--rhythm-rom <文件夹路径>` 手动指定**，否则鼓组将静音。

## 🚀 快速开始

### 渲染为 WAV（无需原版 DRV！）
```bash
python player.py your_song.mid -o output.wav --rhythm-rom ./RHY
```

### 开启软件 LFO（实验性；可能仍有未探明的怪癖）
```bash
python player.py your_song.mid -o output.wav --rhythm-rom ./RHY --enable-lfo
```

### 调整 LFO 速率（更慢 / 更快）
```bash
python player.py your_song.mid -o output.wav --enable-lfo --lfo-divisor 10
python player.py your_song.mid -o output.wav --enable-lfo --lfo-divisor 5
```

### 导出为 VGM (旁支功能)
```bash
python player.py your_song.mid -o output.vgm --vgm
```

### 🎛️ LFO 参数调优（进阶）
软件 LFO 默认关闭。通过 --enable-lfo 开启后，可使用以下参数微调：

| 参数 | 默认值 | 说明 |
|-----|-------|------|
| `--lfo-divisor N` | 7 | LFO 推进频率 = 836.5 / N Hz。1 = Timer-A 原始速率（约 16 Hz，"涡轮"感强烈，旧行为）。7 ≈ 119 Hz，对齐 Win95 MIDI 回调频率。10–14 = 更慢更柔和。|
| `--lfo-rate-scale F` | 1.0 | 缩放每个音色的 rate_word。一般优先用 --lfo-divisor；仅在需要针对个别音色调速时使用。|
| `--lfo-depth-scale F` | 1.0 | 缩放 trem 幅度（tremolo 深度）。0.5 减半深度，适用于修复后仍觉得某音色摆动过大的情况。|
| `--lfo-jitter-us U` | 6.0 | 给 Timer-A tick 加入 ±U µs 的均匀抖动。修复后目前没有明显效果，保留作为诊断选项。|

**推荐调试路径：**先单独用 `--enable-lfo`（默认 `lfo_divisor = 7`）试听。如果颤音仍然偏快，改 `--lfo-divisor 10`；如果偏静态，改 `--lfo-divisor 5`。只有在个别音色幅度摆动明显过大时才动 `--lfo-depth-scale`。

### 🗂️ WOPN 音色包（逆向副产品）
在文件夹中，你可以找到三个 WOPN 文件：

- **NECOPN_Dynamic.wopn**：*强烈推荐用于 libOPNMIDI / 理想状态* 由 `necopn_to_wopn.py` 通过“伪芯片”动态快照技术生成（集成了 Wohlstand 的载波音量归一化算法）。它精准截获了原驱动内部由动态 `OPMASK` 算出的真实泛音结构，音色**极度接近实机驱动的内部状态**。不过，由于静态 WOPN 无法完全复现原生的 6 通道动态环境（如丢音机制、实时包络互动），其听感与 Python 渲染器/实机依旧存在一定区别。你可以将其视为脱离极端硬件限制后的**“理想状态”**。该音色包音量已经修正，非常适合直接在 libOPNMIDI 中演奏。

- **NECOPN_GM-fixed.wopn**：*推荐用于 libOPNMIDI* 这是一个由 Wohlstand 亲自进行修复与调优的版本。他手动调整了包络的衰减率（Decay），并添加了打击乐鼓组，使其在 libOPNMIDI 中听起来更自然、更好听。（注：由于静态 WOPN 无法执行驱动的动态包络逻辑，其效果仍会与 Python 渲染器存在一定区别）。

- **NECOPN_GM.wopn**：这是由 `extract_bank.py` 基于原始驱动数据（或硬编码表）直接提取的原始 WOPN。由于静态格式的限制（如无法处理 RR=0），它仅供可视化参考（例如在 OPN2-BE 中查看），不推荐用于直接播放。

你无需原版 `.drv` 文件，即可自己生成原始 WOPN：
- 将 `extract_bank.py` 与 `fixed_bank.py` 放在同一个文件夹中。
- 运行：`python extract_bank.py`

**生成动态快照 WOPN：**
- 将 `necopn_to_wopn.py` 与 `fixed_bank.py` 放在同一个文件夹中。
- 运行：`python necopn_to_wopn.py`

### ⚠️ 已知限制
- **软件 LFO：**此前出现的电磁噪音、过度颤动已修复。LFO 目前功能可用，但默认关闭，因为可能仍存在尚未探明的怪癖。若遇到异常的 vibrato/tremolo 表现，请先尝试通过上面的参数微调，再考虑报告问题。
- **VGM 导出异常：**通过 --vgm 导出的文件在部分严格校验的播放器（如 foobar2000 vgmplay）中可能无法发声。作为旁支辅助功能，目前暂未修复。

### 🔬 技术细节
- **音色解码：**直接从驱动读取 64 字节记录，按 cs:0x1239 取反表进行硬件映射。已与原始 NECOPN.DRV 逐字节比对验证。
- **力度与包络：**严格遵循 0x0BED 的算子掩码逻辑及硬件 SR/DR 机制。
- **Timer 节拍：**836.5 Hz 的 Timer-A 驱动，处理逻辑精确对齐反汇编指令。包络状态机在此速率上运行。

### 软件 LFO：
- **推进频率：**约 120 Hz，由 `lfo_divisor = 7` 分频得到（836.5 / 7）。这与 Win95 MIDI 回调频率相符，而不是 Timer-A 的速率——驱动在每个 MIDI 事件粒度上轮询 irq_poll，所以 LFO 更新频率相对 Timer-A 是分频的。
- **TL 计算：**逐字节对照 seg3:0x0F92-0x0FC8，包括对中间量 t1 的无符号字节处理——这正是旧版"电磁噪音"的根因。
- **Shape 1/3 相位：**计数器使用 `eff_counter = lfo_counter // lfo_divisor`，确保方波/噪声 LFO 在分频后仍保持正确的频率。

### 🙏 致谢与声明
- 特别感谢 **Wohlstand** 在 WOPN 格式和核心对比上的耐心解答，亲自进行 WOPN 音色库调优，以及提供跨平台 CMake/Linux 构建的代码修复。
- 感谢 **cisc** 编写的经典 **fmgen** 核心，以及 NP21/W 团队 的现代接口维护。
- 感谢 **Aaron Giles** 提供的 **ymfm** 框架。
- 本项目仅供个人学习、复古技术研究与交流，所有原厂硬件与软件的版权归 NEC 及 YAMAHA 所有。
