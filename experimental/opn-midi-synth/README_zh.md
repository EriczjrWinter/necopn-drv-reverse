# OPN MIDI Synth（3 FM + 3 SSG）— 实验版

一个“OPN 编制”的 MIDI 合成器原型：**丢掉鼓组，6 个声部全部给旋律**，
其中 3 个 FM、3 个 SSG。用 NECOPN 的逆向驱动逻辑 + 组内大逃杀分配，
固定每个 MIDI 通道的 FM/SSG 归属，避免 GAI 那套“同一通道一会儿 FM 一会儿 SSG”的音色横跳。

> 位于本仓库 `experimental/opn-midi-synth`，是 `necopn-drv-reverse-gai` 的后续实验：
> 不再按运行时负载动态溢出，而是把 SSG 当成固定编制。
> 核心不是字面上的 YM2203，而是把 fmgen OPNA 当 OPN 用：只用 **ch0-2 的 3 个 FM + 3 个 SSG**；
> ch3-5、ADPCM、节奏通道全部不碰。听感上等价于 3 FM + 3 SSG 的 OPN 编制。

## 用法

```bash
# 自动分析通道，选 3 个走 SSG，其余走 FM
python opn_synth.py your_song.mid -o out.wav

# 只看通道统计和分配计划，不渲染
python opn_synth.py your_song.mid --list-channels

# 手动指定 SSG 通道（推荐：把 bass/arp/pluck 放 SSG）
python opn_synth.py your_song.mid -o out.wav --ssg-channels 3,4,5

# 试听前 10 秒
python opn_synth.py your_song.mid -o demo.wav --duration 10

# 调 SSG 混音音量（默认 2.0 dB 衰减）
python opn_synth.py your_song.mid --ssg-atten-db 6

# 单独试听 SSG / FM，用来调音色和平衡
python opn_synth.py your_song.mid --solo ssg --duration 10 -o ssg.wav
python opn_synth.py your_song.mid --solo fm --duration 10 -o fm.wav
```

输出是芯片原生 **55 555 Hz** 立体声 WAV；大多数播放器会自动重采样。

## 分配策略

- 6 个统一槽位：`slot 0-2 = FM`、`slot 3-5 = SSG`。
- 每个 MIDI 通道在加载时锁定到一组（`ch_group`），**只在组内分配**；
  同一通道永远不会同时出现 FM 和 SSG 音色。
- 组内抢音顺序沿用 NECOPN 的“大逃杀”：
  1. 同通道的释放槽；
  2. 组内空闲槽；
  3. 组内最高 MIDI 通道里最老的槽；如果被抢的通道号比新音符低，就丢音。
- 鼓组通道 9/15 直接忽略。

### 自动分配

`--list-channels` 会按每通道统计：

- 最大复音、音符数、平均时值、平均力度、CC7、program、bend 次数；
- 优先 `max_poly <= 1`（单声道）、长音、低 CC7、bass/square lead/pad 类 program；
- 避开复音 >3、bend 多、钢琴/弦乐/铜管类 program。

这只是一个启发式；**手动 `--ssg-channels` 通常更好**，尤其是你明确知道哪条是贝斯/琶音时。

## FM / SSG 音色

- FM 用 `fixed_bank.py` 的 128 条 NECOPN 音色，走原来的 4-op 寄存器；
- SSG 是方波 + 4-bit 音量。包络参考 **MID2VGM beta 5** 的 SSG instrument table：
  每个 GM program 4 个字节 = 初始音量 / decay1（高 nibble 速度，低 nibble 保持音量）/
  decay2 速度 / 最大音量；包络步进 **psgtick = 512/44100 ≈ 11.6 ms**。
- key-on 从 12–14 起步，attack 到 15，decay1 落到 12–14 的 sustain，
  再按 decay2 长衰减：钢琴/贝斯 `$30`（约 0.56 s/步），
  风琴/弦乐/合唱/lead/pad `$90`（约 1.67 s/步）。
  所以 SSG 会持续/拖尾，不是上一版那种短促 pluck。
- 默认全局衰减 **2 dB**；没有 per-program gain，平衡靠 instrument table 的初始/最大音量。
- 包络表取自 **MID2VGM beta 5（DamageX）** 的 SSG instrument table，仅用于本实验的声音设计；
  若发布请自行确认该工具的授权。
- SSG 只适合 bass、arp、pluck、square lead、简单 pad；钢琴/弦乐/铜管/长音 pad
  放 SSG 仍会明显“电子化”，包络只能缓解、不能变成 FM。
- 默认全局衰减 **2 dB**；如果还觉得 SSG 亮/响，`--ssg-atten-db 6` 或更大；
  如果觉得 SSG 太轻，试 `--ssg-atten-db 1~2`。

## 已知限制

- GM MIDI 不是为 3 FM + 3 SSG 写的：8 个旋律通道的曲子会丢音/抢音；
  这是 6 声部编制的固有代价，不是 bug。
- 鼓组被完全放弃；需要节奏时请另外接 PCM/SSG 鼓层。
- 自动分配只是启发式；想稳定就手动锁通道。
- SSG 的 decay2 很长（`$90` 约 1.67 s/步），长音会拖尾并占槽；新的 SSG 音符
  可能提前切断旧尾音，这是 3 个 SSG 槽的物理限制。
- 用的是 OPNA 核心的 ch0-2 + SSG 来模拟 OPN；真正的 YM2203 核心可以以后再换，
  音色/寄存器行为基本一致。
- 输出 55 555 Hz；没做 48 kHz 重采样。

## 文件

```
opn_synth.py    CLI / 通道分析 / 自动分配 / 渲染
opn_driver.py   3 FM + 3 SSG 的 6 槽统一分配器
ssg_env.py      MID2VGM 的 SSG 包络表（128 GM program）
necopn_base.py  NECOPN 逆向驱动的音色/常量/通道状态（Voice/OPMASK/...）
necopn_core.py  fmgen OPNA 的 ctypes 封装
fixed_bank.py   从 NECOPN.DRV 提取的 128 条 GM 音色
smf.py          SMF 解析
core/           necopna_fmgen.dll（fmgen，cisc 自由软件条款）
```

## 许可

- 本项目代码 MIT（见 `LICENSE`）。
- `core/necopna_fmgen.dll` / fmgen：cisc 的自由软件条款（非商用、保留版权与许可证原文，
  见 `core/LICENSE-fmgen.txt`）。
- `fixed_bank.py` 是从 NECOPN.DRV 提取的寄存器参数，不是录音。
- 不含任何 RHY/WAV/ROM 采样。
