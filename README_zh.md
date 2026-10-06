>English version: [README.md](README.md).
# NECOPN 播放器（驱动动态行为复现）

把 `NECOPN.DRV`（NEC "MIDI:NEC Synthesizer Driver" 4.05.00.0129）的**动态行为**
复现成一个独立播放器：MIDI 进 → OPNA 寄存器流 → WAV / VGM 出。

因为 libOPNMIDI 只吃 WOPN 音色库（静态参数），驱动里的力度曲线、通道抢夺、
软件包络/LFO、鼓路由这些**行为**没法做成它的插件，所以按要求单独复现为播放器。
模拟核心抽象成接口，默认接 **ymfm 的 OPNA(YM2608) 核心**。

## 快速开始

```bat
cd necopn_player
python selftest.py                                  :: 56 项自检（对拍逆向结论）
python core\smoke_test.py                           :: 24 项核心冒烟
python player.py testdata\demo_rich.mid -o out.wav  :: 出 WAV（55555Hz）
python player.py testdata\demo_rich.mid -o out.vgm  :: 出 VGM（foobar2000 + vgmplay 可播）
python player.py testdata\demo_rich.mid --trace t.txt --core null   :: 只看寄存器
```

| 选项 | 说明 |
|---|---|
| `--mode necopn` | **真驱动**：2 颗 OPNA，每颗只用 ch0-2（共 6 声部），鼓走第 1 颗节奏 |
| `--mode full` | **假设满通道 NECOPN 驱动**：每颗 OPNA 用满 6 声部，`--cores N` 定核心数 |
| `--cores N` | 核心数（默认 necopn=2、full=3，即 18 声部） |
| `--core ymfm\|null\|auto` | 模拟核心；null 只出寄存器（无 DLL 也能跑） |
| `--no-lfo` | 关掉软件包络/LFO，方便 A/B 对比 |
| `--rhythm-rom FILE` | **装入原生 YM2608 打击乐 ROM**（8192 字节 ADPCM-A dump，MAME 的 `ym2608_adpcm_rom.bin` 可直接用）；不指定时用内置合成打击乐 |
| `--clock` | OPNA 时钟，默认 8000000（驱动 FNUM 表实测吻合） |

## 目录

| 文件 | 作用 |
|---|---|
| `necopn_core.py` | **模拟核心抽象**（`ChipCore`）+ ymfm DLL 封装 + Null 核心 + VGM 记录 |
| `necopn_driver.py` | 驱动行为复现（音色装载/力度/音高/鼓/分配/抢音/LFO） |
| `smf.py` | 标准 MIDI 文件解析 |
| `player.py` | CLI 播放器（渲染循环、WAV/VGM/trace 输出） |
| `selftest.py` | 56 项自检，逐条对拍逆向结论（其中 14 项直接从 `NECOPN.DRV` 字节对拍） |
| `testdata/demo.mid` `demo_rich.mid` | 测试 MIDI（单音+鼓 / 和弦+鼓+弯音） |
| `core/necopna_ymfm.dll` | **ymfm OPNA 核心**（已构建，24/24 冒烟通过） |
| `core/vendor/ymfm/` `ymfm_SOURCE.txt` | ymfm 源码存档（commit 81aec25）与来源记录 |
| `core/smoke_test.py` | 核心冒烟测试（FM 发声/双芯片/6 种节奏/打击乐 ROM 装卸） |
| `tools/wav2rhythmrom.py` | **WAV 鼓样本 → YM2608 打击乐 ROM**（8KB ADPCM-A 打包器） |
| `out/ym2608_rhythm_drum_samples.bin` | 用上面的工具从用户鼓样本打好的 ROM（8192 字节） |

输出格式：**WAV 固定 55555 Hz**（= clock/144，ymfm 原生 clock/8 由 DLL 内加权抽取）；
**VGM 每颗芯片一个文件**（`.vgm`、`.vgm.chip1`…），单芯片时命令 0x56/0x57，
可直接给 foobar2000 的 vgmplay 播。

## 求助

目前唯一未解决的问题是 Python 渲染版本在**固定位置丢失整条音轨**。
所有静态分析都已完成（`selftest.py` 56/56，每条行为都带反汇编地址），
但本项目手上没有能**抓取真机寄存器 I/O trace** 的环境。

能推进这件事的帮助：

1. **真驱动的寄存器 trace。** MAME 的 PC-9821 驱动在 `-debug` 下支持
   `trace 188,18a,18c,18e,1,0`。NP2 的 debug 版**没有** I/O trace 功能（已确认）。
   **一个音符的写入序列（30–50 次）就够。**
2. **能用的 PC-98 MIDI→WAV 工具。** 通用 OPL3 工具（Audio Composer + `OPLERO82.DLL`）
   **不兼容 OPNA**。日本本土工具如 **htsfms** 可用，如果你手上有。
3. **逐秒 RMS 对比。** 把真机录音和 Python 渲染同一 MIDI 的 WAV，
   用 `examples/rms_diff.py` 跑一遍即可。

如果你能提供以上任意一项，请开 issue 并附上 trace/音频 + 对应的 MIDI 片段。

## 音频对比

`audio/` 目录下提供了同一首曲子的两份录音，出自 **《クリスタルリナール －逢魔の迷宮－》**
（D.O. 1994 年 PC-98 作品）：

- `Crystal_Rinal_CR028_real_pc9821_rom.mp3` —— 模拟 PC-9821 上的原版 NECOPN.DRV
- `Crystal_Rinal_CR028_python_render_rom.mp3` —— 本项目的 Python 复现

Python 版本在固定位置丢失整条音轨。详见 `audio/README_zh.md`。

## 模拟核心接口

`ChipCore` 刻意只留 5 个方法，换核心不用动驱动层：

```python
class ChipCore:
    def write(self, port, reg, val)   # port 0=0x188/0x18A, 1=0x18C/0x18E
    def read(self, port, reg) -> int
    def sample_rate(self) -> int
    def generate(self, nframes) -> bytes   # 立体声交错 int16
    def reset(self)
```

- `YmfmCore`：`core/necopna_ymfm.dll`（ymfm `ym2608` 核心，首选）
- `NullCore`：寄存器黑洞，出 trace / VGM / 跑测试
- 想换 Nuked-OPNA、MAME ym2608，只要再写一个子类；`YmfmPool` 已经把
  "一个句柄带 2 颗芯片" 的差异屏蔽掉，多核心就是多开几个 `ChipCore`。

## 复现到的动态行为（全部有反汇编证据）

### 1. 力度 → TL（`seg3:0x0BED`，表 `cs:0x0C55`）
MIDI 力度先过 DGROUP:0x2E 的 128 项表：`level = 0（v<2）| 0x40 + v/2`。
然后按**算法**选"载波算子"加衰减：

```
TL[i] = clamp8(mask[ALG][i] & (0x7F - level)) + (0x7F - rec[0x1B+i])
```

`mask` 是 8 算法 × 4 算子的 0x00/0x7F 表（ALG0-3 只有 op4，ALG4=op2/op4，
ALG5=op2/3/4、ALG6=op2/3/4、ALG7=全部）。也就是说**只有载波吃力度，调制器音量固定**。
力度只影响 0x3F（约 47 dB）范围 —— 不是 libOPNMIDI 那种全量程曲线。

### 2. 通道分配与抢音（`seg1:0x0058`）
4 轮查找，6 个 FM 声部：
1. 同 MIDI 通道、`note==0` 的活跃声部 → 先 key-off 再复用（不重装音色）
2. 空闲声部 → 用
3. 其它 MIDI 通道、`note==0` 的活跃声部 → 用
4. 抢音：取**占用中 MIDI 通道号最大**的声部（同号取最早开的）；
   若它比自己"优先级更低"（MIDI 通道号更小）→ **放弃发音**

结论：**低 MIDI 通道号优先级高**；16 通道复音在真机上只有 6 声部，
第 7 个音会抢最旧的同号声部，而 ch3 满载时抢不过 ch0-5。
另外程序变更（`seg1:0x0478`）会先杀掉该 MIDI 通道的全部音。

### 3. 移调 FMOCTAVE（`seg1:0x0232`，资源 0x7510）
`note += FMOCTAVE[program]`（有符号半音），再按**无符号**比较 clamp 到 0x7F。
本库 128 项里只有 3 种值：`-12`（122 条）、`-24`（5 条）、`-60`（1 条，
program 125 = Gunshot）。这就是那张"128 项表（多 0xF4）"的用途。

### 4. 音高与弯音（`seg3:0x0C75`、`seg1:0x03B2`）
```
FNUM = FNUM表[note%12] + 弯音增量表[note%12] * bend255 / 255     block = note/12
```
MIDI 弯音 14 位 → `step = 8191/range + 1`（range = DGROUP:0xE2，默认 2 半音），
`note' = note + (bend14 + base)/step - range`，小数部分转成 0-255 线性。
写 FNUM 时先 0xA4 后 0xA0（OPN 锁存顺序）。

### 5. 鼓（`seg3:0x0CCB`，表 DGROUP:0x8B+note）
MIDI ch9/ch15，音域 35..81：

| GM 鼓 | NECOPN |
|---|---|
| 35/36 Bass Drum | BD |
| 38/40 Snare | SD |
| 42/44/46 Hi-Hat | Hi-Hat |
| 41/43/45/47/48/50 Tom | Tom |
| 37 Side Stick | Rim Shot |
| 49/51/52/55/57/59 Cymbal | Top Cymbal |
| **39 Hand Clap、53/54/56/58、60-81** | **哑音（0x7F）** |

电平 = `level>>2`（0..31），写 `0x18+乐器 = 0xC0|电平`，再 `0x10 = 1<<乐器` 触发。
**没有鼓释放**（`seg1:0x2CC` 直接 `ret`）。

### 6. 软件包络 + LFO（`seg3:0x0D59`，Timer-A tick ≈ 836.5 Hz）
这是 WOPN 里完全不存在的"动态"部分：每个发声通道跑一个软件 LFO，
同时调制 **FNUM（颤音）** 和 **TL（颤音/音量）**：

- LFO 波形 = 音色记录 0x0A：0=三角 1=方波 2=三角(带方向) 3=随机
- 速率 = 记录 0x14 的 16 位字（周期 `0xFFFF/速率` 个 tick）
- 起相位 = 记录 0x0F（0=接着全局计数跑，1=从 0 重新起）
- 计数器 `ws+0x19` 每个走完步进的 tick 末尾自增（`seg3:0x0EFB`）——方波/随机波形靠它走相位
- 颤音深度 = 记录 0x1A × 记录 0x24，`fnum += (LFO*深度/0x7FFF) * fnum/0x7FF`
- TL 深度 = 每算子 `(记录 0x2F+i & 0xF) * 记录 0x1F / 15`
  `TL = clamp8(TL_base + TL_base*(深度*LFO/0x7FFF)/0x7F)`（原样复刻 8 位进位语义）

Timer-A = 0x0E3（reg 0x26，reg 0x25 未写 = 0）→ `(1024-0xE3)*12/8MHz` = 1.1955 ms。

### 7. 其它
- MIDI CC 里**只有 CC≥0x7B 有效**（All Notes Off 等 → 全部 key-off）；
  音量/声像 CC 被忽略，响度只由力度决定
- 音色记录里的 RL（pan）位写进 0xB0 被硬件忽略，**声像恒为 L+R 居中**（与 WOPN 一致）
- key-on 槽位掩码 = 记录 0x05 低 4 位（本库 128 条全 0x0F，即四算子齐开）
- **移调后音高 ≥ 0x5C(92) 时不 key-on**（`seg1:0x0039` 闸门）：不写 FNUM、不写 0x28，
  力度照写 → 该音不发声但声部被占（低音区环绕到 0x7F 的音因此是静音）
- 鼓通道先判力度（`seg1:0x055F`）：力度 0 的 note-on 完全无写入（`ret 8`，不会敲鼓）
- 记录里 AR/DR/SR/SL/RR/TL 是**取反存储**（`seg3:0x1239` 取反表）：
  `AR = 0x1F - rec`，`SL/RR = 0x0F - rec`，`TL = 0x7F - rec`

## 多核心：假设满通道 NECOPN 驱动

真机是双 OPNA、每颗只用 ch0-2（6 声部）。`--mode full --cores N` 把每颗芯片
按"满 6 声部"用起来，得到 `6N` 个声部（3 核 = 18 声部 ≈ 16 MIDI 通道全复音），
用来回答"如果 NECOPN 当年做满通道会怎样"。分配算法、力度、LFO 行为不变，
只是候选声部变多。

## 原生打击乐 ROM（YM2608 ADPCM-A）

真芯片的 6 种节奏音色（BD/SD/TopCym/HH/Tom/Rim）存在**芯片内 8KB ROM** 里，
ymfm 不带这份数据。封装层默认用内置的**合成**打击乐 ROM（能出声、可测试，但不是原厂音色）。
有原生 dump 时一条命令换上：

```bat
python player.py song.mid -o song.wav --rhythm-rom ym2608_adpcm_rom.bin
```

- 要求：≥ 8192 字节的原始 dump（恰好 8192 最好；更大会取前 8KB 并提示）。
  MAME 的 `ym2608_adpcm_rom.bin` 就是这个布局，直接可用。
- 原理：dump 通过 `nopna_set_rhythm_rom()` 复制进核心句柄，`ymfm_external_read(ACCESS_ADPCM_A)`
  改从它取数；两颗 OPNA 都生效。`ym2608::reset()` 预置的六种乐器起止地址
  （`ymfm_opn.cpp:1002-1007`）就是原厂 ROM 的固定布局，所以 dump 不需要任何重排。
- `nopna_clear_rhythm_rom()` 撤回内置合成（Python 侧 `YmfmCore.clear_rhythm_rom()`），
  方便 A/B 对照。冒烟测试 (f1)-(f7) 会用测试数据验证整条装入-解码-清除链路。

注：ymfm 的 FM 包络计数器不随 reset 归零，ADPCM-A 取数节拍挂在它上面，
所以不同"相位"的两次渲染尾部采样本就有零星差异——对比 ROM A/B 时要用
同样新建的句柄各渲一次（冒烟测试就是这么做的）。

### 手里只有 WAV 鼓样本（没有 dump）

真芯片 ROM 的布局是**硬连线**的，六个乐器各占固定区间，而且**回放速率不完全一样**：

| 槽位 | 乐器 | 地址 | 字节 | 通道 | 回放速率（8MHz 时钟） | 槽位时长 |
|---|---|---|---|---|---|---|
| 0 | bd | `0x0000-0x01BF` | 448 | ch0 | 18518.52 Hz（clock/432） | 48.4 ms |
| 1 | sd | `0x01C0-0x043F` | 640 | ch1 | 18518.52 Hz | 69.1 ms |
| 2 | top | `0x0440-0x1B7F` | 5952 | ch2 | 18518.52 Hz | 642.8 ms |
| 3 | hh | `0x1B80-0x1CFF` | 384 | ch3 | 18518.52 Hz | 41.5 ms |
| 4 | tom | `0x1D00-0x1F7F` | 640 | ch4 | **9259.26 Hz（clock/864）** | 138.2 ms |
| 5 | rim | `0x1F80-0x1FFF` | 128 | ch5 | **9259.26 Hz** | 27.7 ms |

tom/rim 所在的 ch4/ch5 **只有其它通道一半的回放速率**（`ymfm_opn.cpp:1397-1400`：
ADPCM-A 每 3 个 FM 时钟走一拍，ch4/5 每两拍才走一拍），所以同样字节数能装两倍长的声音。
上表速率是拿本项目核心**实测**核对过的（满槽位数据按出声时长反推，误差 < 0.1%）。

用 `tools/wav2rhythmrom.py` 把 WAV 打包成 ROM，然后照常 `--rhythm-rom`：

```bat
python tools\wav2rhythmrom.py D:\galgame\drum_samples -o out\my_rhythm.bin
python player.py song.mid -o song.wav --rhythm-rom out\my_rhythm.bin
```

- **认乐器**：按文件名关键字（`bd/bass/kick`、`sd/snare`、`top/cym`、`hh/hat`、`tom`、
  `rim/rym`）自动匹配；认不出的用 `--bd xxx.wav --sd ...` 显式指定。
- **重采样**：按该槽位的回放速率**保音高**重采样，44.1kHz 的 WAV 直接用；
  头尾低于 ADPCM 量化底噪的静音会裁掉（留着只占槽位）。
- **编码**：YM2608 ADPCM-A 4bit（编码器是 `ymfm_adpcm.cpp:151-213` 解码器的逆）；
  默认把最响的样本推到 ADPCM 满度 ±2047，乐器间相对响度不变（`--gain-db` 可调）。
- **装不下的尾部**：槽位长度是死的。默认 `--fit fade` 截掉尾部并淡出 8ms
  （`--fade-ms` 可调）；`--fit speed` 整体加速塞进去（会升调，慎用）。
  打包报告会打印每个乐器的"样本数/槽位容量"、截断比例与编码信噪比，逐条核对。


## 已知不确定项

1. **LFO 随机波形（shape 3）** 的精确算法按 `seg3:0x10AF` 复刻，
   但乘数 0x383 与初值的语义只能从汇编推断。
2. **抢音第 4 轮**的"放弃发音"条件（`seg1:0x0181`）按汇编原样实现；
   表现为高 MIDI 通道在满载时可能丢音。
3. 弯音小数部分用 `*255/step`，原代码走的是 C 运行时辅助例程，
   与 `*256/step` 可能差 1/255 半音。
