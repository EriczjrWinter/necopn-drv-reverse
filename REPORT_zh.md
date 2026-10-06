# NECOPN.DRV 逆向报告 —— 阶段 1：OPNA 音色库提取

对象：`NECOPN.DRV`（30,096 字节，SHA256 `cae5234a9ca88ac51495a065e8b85e2a7e7a11d98d213b95fa056e10b542dae8`）
产物：`out/NECOPN_GM.wopn`（WOPN v2，chip_type=OPNA，128 GM 音色）+ `out/voices_dump.txt`（全量 dump）

---

## 1. 文件身份

- Windows 3.x/95 **NE 格式 16 位 DLL**，NE 头 @0x90，4 段：
  - seg1 代码 0x1106 @0x240（初始化：LibMain=seg1:0x0B6C、MYFAIRLADY=seg1:0x063E、___EXPORTEDSTUB=seg1:0x0F8C）
  - seg2 代码 0x0054 @0x1400（WEP=seg2:0x0000）
  - seg3 代码 0x1523 @0x3D60（运行时主体：DRIVERPROC=seg3:0x0000、MODMESSAGE=seg3:0x0D23、音色装载/寄存器输出/定时器）
  - seg4 数据 0x2894 @0x1480（端口表、资源名字符串、DGROUP 初始化数据）
- 版本资源：`necopn.drv` 4.05.00.0129，Copyright NEC 1997；设备名 **"MIDI:NEC Synthesizer Driver"**
- 导出：WEP(1)、DRIVERPROC(2)、MODMESSAGE(3)、MYFAIRLADY(4)、___EXPORTEDSTUB(5)；导入 KERNEL/USER/MMSYSTEM
- DOS stub 带 `Cert DX2 <hash>` 水印；MZ 头字段不合理、重定位计数词全部为 0、真实重定位表在**各段数据之后**（计数词在表首）——**该文件被后处理工具重排过**，非标准 NE 加载流程（不影响本分析，重定位已按其格式解析）

## 2. 自定义资源（文件尾部 0x5320–0x7590）

| 文件偏移 | 大小 | 内容 | 证据 |
|---|---|---|---|
| 0x5320 | 0x30 | Pascal 串 "NEC ｼﾝｾｻｲｻﾞ ﾄﾞﾗｲﾊﾞ" | SJIS 解码 |
| 0x5350 | 0x1C0 | VS_VERSION_INFO | 内嵌字符串 |
| 0x5510 | 0x2000 | **FM 音色库：128 × 64 字节** | seg3:0x0776 加载器，`cmp ax,0x2000` 校验尺寸，`rep movsw cx=0x1000` 复制到 DS:0x6F4 |
| 0x7510 | 0x0080 | 128 项表（多 0xF4，稀殊项）| seg3:0x02E0 加载器，`cmp ax,0x80` 校验，复制到 DS:0x656（FMOCTAVE）|

资源类型/名字符串（seg4，FindResource 参数）：`FMPARA`@ds:0xF9 + `VoicePara`@ds:0x100；`FMOCTAVE`@ds:0x18 + `VoiceOctave`@ds:0x21。

## 3. 64 字节音色记录 → OPN 寄存器映射（核心成果）

> **⚠️ 阶段 2 修正（重要）**：本节表格里的 TL 位置与取值方向有误，已由第 7 节更正。
> 记录里的 AR/DR/SR/SL/RR/TL 是**取反存储**（`cs:0x1239` 取反表：`AR=0x1F-rec`、
> `SL/RR=0x0F-rec`、`TL=0x7F-rec`），TL 的真实来源是记录 **0x1B-0x1E**；
> 记录 0x05/0x0A/0x0F/0x14 是 key-on 槽位掩码 / LFO 波形 / LFO 起相位 / LFO 速率。
> 详见第 7.1 节；`extract_bank.py` 已按更正后映射重出 WOPN。

装载路径：seg1:0x1CE `voice = DS:0x6F4 + prog*64` → seg3:0x042A → 分发器 seg3:0x0A90（cmd=0x16）→ **seg3:0x0BB9**：
把记录前 0x33=51 字节复制到每通道工作区 `DS:0x26F4 + ch*0x34`，再调用 **seg3:0x1430** 写寄存器。
字段读取器 seg3:0x11F8 对**逻辑索引 >0x14 的访问 +1**（0x14 是运行时字段，被跳过）。
组写入器 seg3:0x126D/0x12DE 把两个字段打包进一个寄存器；算子槽位偏移表 `cs:0x142C = [0x00,0x08,0x04,0x0C]`。

**记录字段表（记录内偏移，op0..3 对应槽位偏移 +0/+8/+4/+C）：**

| 偏移 | 含义 | 打包去向 |
|---|---|---|
| 0x00 | RL(pan)<<6 \| FB<<3 \| ALG | 原样 → 0x20+ch 组（全部 128 条 FB/ALG 值合法）|
| 0x01–0x04 | AR[4] | (KS<<6)\|AR → 0x50 组 |
| 0x05 / 0x0A / 0x0F / 0x14 | TL[0..3]（可 >0x7F，运行时 clamp 到 0x7F）| 0x40 组（写入器 seg3:0x14A6）|
| 0x06–0x09 | DR[4]（bit7=AM，本库全为 0）| AM<<7\|DR → 0x60 组 |
| 0x0B–0x0E | SR[4] | 0x70 组 |
| 0x10–0x13 | SL[4] | (SL<<4)\|RR → 0x80 组 |
| 0x14 | （运行时字段；作为 TL[3] 源，>0x7F 的记录很多，如 0xAD）| |
| 0x16–0x19 | RR[4]（逻辑索引 0x15–0x18）| 同上 |
| 0x20–0x23 | KS[4]（逻辑 0x1F–0x22）| 0x50 组 |
| 0x25–0x28 | ML[4]（逻辑 0x24–0x27）| (DT<<4)\|ML → 0x30 组 |
| 0x2A–0x2D | DT[4]（逻辑 0x29–0x2C）| 0x30 组 |
| 0x1A, 0x1F, 0x2F–0x32 | 缩放旋钮（seg3:0x0F16 乘除运算，默认值≈直通）| 运行时调制 |
| 0x15, 0x1B–0x1E, 0x29 | 未见读取（保留）| |
| 0x33–0x3F | 未复制，填充 | |

## 4. 硬件层

- 端口表 seg4:0x10：`0x0188 / 0x018A / 0x018C / 0x018E`
- 写寄存器 seg3:0x1192：忙等待（读 0x188 位 7）→ 写 0x188 → `out 0x5F` ×2 等待 → 写 0x18A
- **双 OPNA**：FM 通道 0–2 走 0x188/0x18A，通道 3–5 走 0x18C/0x18E（seg3:0x14A6、0x147F、0x126D 中 `ch>=3 → 偏移-3 → 换端口对`）
- 时钟：**8.000 MHz**。音高表 `cs:0x14F4`（12 半音 FNUM：617,654,692,734,777,824,873,924,979,1038,1099,1165）按 `FNUM = f×144×2^17/clk` 与 8MHz 精确吻合（A4→1038=1038）
- 弯音：`cs:0x150C` 每半音增量表，pitch bend 0–255 线性内插（seg3:0x0C75）
- 鼓：cmd 0x24 → **YM2608 节奏通道**（0x18+乐器 = 0xC0|电平，0x10 使能位）——采样在板载 ROM，不在本文件
- 定时：OPNA Timer-A 中断（0x27 寄存器应答，seg3:0x100C；IRQ 服务入口 seg3:0x11F8 附近的轮询）

## 5. 已知未解（阶段 2 素材）

- MIDI 音量/力度 → TL 曲线（cmd 0x1F，表 `cs:0xC55` 为 8×4 的 0x00/0x7F 掩码，语义待定）
- ch10 鼓的完整路由（节奏 6 乐器 ↔ MIDI 鼓音符映射）
- FMOCTAVE 表（DS:0x656，128B）的精确用途
- 0x7510 的 128 项表（多 0xF4，疑似程序号/鼓映射）
- `MYFAIRLADY`（seg1:0x063E，cmp 0x67 分支）私有 API
- 音色轮换/抢音策略、SysEx 处理、初始化寄存器序列（seg1）

## 6. 转换实现

`extract_bank.py`：解析记录 → 按 WOPN v2（"WOPN2-B2NK\0"，chip=OPNA）输出。要点：
- WOPN 算子顺序 = 芯片偏移 +0/+4/+8/+C（libOPNMIDI `0x30 + d*0x10 + op*4`），故 `wopn_op = [f0, f2, f1, f3]`
- fbalg = 记录 0x00 且清 pan 位（本库 pan 全 0=居中）
- v2 中 delay 全 0 会被判为空白音色 → 用 AR/RR 估算非零毫秒值
- 69 处 TL>0x7F 均为驱动运行时 clamp 的静音算子，输出时同样取 0x7F
- 校验：`verify_wopn.py` 按 wopn_file.c 加载逻辑回读，128 音色全部字段值域合法；prog0 与原始记录逐字节核对一致

---

## 7. 阶段 2：驱动动态行为（seg1 MIDI 事件层 + seg3 运行时）

> 交付物：`../necopn_player/`（独立播放器 + ymfm OPNA 核心 + 多核心模拟）。
> 本节是行为复现的权威依据，全部结论都有反汇编地址；完整清单另见
> `../necopn_player/README.md`。完整反汇编清单：`out/seg1_listing.txt`、`out/seg3_listing.txt`。

### 7.1 字段映射更正：取反表 `cs:0x1239` + TL 的真实位置

`seg3:0x11F8` 字段读取器在读字节字段后做一次变换：

```
121B  mov al, [bx+di]                  ; 工作区字节
121D  cmp byte ptr cs:[bx+0x1239], 0   ; 取反表[索引]
1223  je  原样返回
1225  mov bl, cs:[bx+0x1239]
122A  sub bl, al                       ; 返回 (表值 - 存储值)
```

取反表（索引 = 逻辑索引，>0x14 时已 +1 跳过 16 位运行时字段）：

| 索引 | 0x00 | 0x01-04 | 0x05 | 0x06-09 | 0x0A | 0x0B-0E | 0x0F | 0x10-13 | 0x14 | 0x15 | 0x16-19 | 0x1A | 0x1B-1E | 0x1F+ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 表值 | 0 | 0x1F | 0 | 0x1F | 0 | 0x1F | 0 | 0x0F | 0 | 0 | 0x0F | 0 | **0x7F** | 0 |
| 含义 | FB/ALG | AR | key-on 槽位掩码 | DR | LFO 波形 | SR | LFO 起相位 | SL | LFO 速率(字) | — | RR | 颤音深度A | **TL** | KS/ML/DT 等 |

因此：**`AR = 0x1F-rec`、`DR = 0x1F-rec`、`SR = 0x1F-rec`、`SL = 0x0F-rec`、
`RR = 0x0F-rec`、`TL = 0x7F-rec`**；KS/ML/DT/key-on 掩码等原样。

**TL 的真实来源是记录 0x1B-0x1E**（逻辑 0x1A-0x1D），由 `seg3:0x0BED`（cmd 0x1F
力度路径）读取后交给 TL 写入器 `seg3:0x14A6`。第 3 节原表把 0x05/0x0A/0x0F/0x14
当成 TL 是错的 —— 那 4 个字段分别是 key-on 槽位掩码、LFO 波形、LFO 起相位、
LFO 速率字，值域证据：全库 512 个算子里有 **69 处 >0x7F**（最大 0xCF），
而 0x1B-0x1E 取反后恰好落在 0..50 的正常 TL 区间。

修正后 voice#0 的包络为 AR=29/29/29/30、SL=3/3/3/5、RR=14/11/11/15、
TL=33/47/35/0（钢琴型），而旧映射给出 AR=2/2/2/1、SL=12、RR=1、TL=15/2/0/12。
`extract_bank.py` 已改用 `field_inv()`，旧产物保留为 `out/*_prevfields.wopn` 供 A/B。

### 7.2 MIDI 事件层（`seg1:0x04DE` 分发）

消息 → 处理（只有这 5 类被处理，其余忽略）：

| 状态 | 处理 | 地址 |
|---|---|---|
| 0x8x note off | `seg1:0x0350`：查声部 → key-off → 清状态 | |
| 0x9x note on | 力度 0 = note off；ch9/ch15 走鼓 | `seg1:0x02D0` |
| 0xBx CC | **只有 CC≥0x7B**（All Notes Off 等）→ 全部 key-off | `seg1:0x05AE` |
| 0xCx program | 先杀该 MIDI 通道全部音，再记 program | `seg1:0x0478` |
| 0xEx pitch bend | 14 位弯音 → 逐声部改 FNUM | `seg1:0x03B2` |

音量/声像 CC **被忽略**，响度只由力度决定。

### 7.3 力度 → TL（`seg3:0x0BED`）

力度先过 DGROUP:0x2E 的 128 项表：`level = 0（v<2）| 0x40 + v/2`（单调、只覆盖上半区）。
然后：

```
TL[i] = clamp8(mask[ALG][i] & (0x7F - level)) + (0x7F - rec[0x1B+i])
```

`mask` = `cs:0x0C55`（8 算法 × 4 算子，0x00/0x7F）＝**载波算子**集合：

| ALG | 0-3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|
| 吃力度的算子(S1..S4) | S4 | S2,S4 | S2,S3,S4 | S2,S3,S4 | 全部 |

只有载波吃力度，调制器音量固定；力度影响范围 0x3F（≈47 dB）。
`TL>0x7F` 时写入器 `seg3:0x14A6` clamp 到 0x7F（静音算子）。

### 7.4 通道分配与抢音（`seg1:0x0058`，6 个 FM 声部）

通道记录在实例结构 `+0x4A`（stride 0x14）：`+0=note, +1=MIDI通道, +3=active, +6..9=序号`。
四轮查找：

1. 同 MIDI 通道、`note==0` 的活跃声部 → 先 key-off 再复用（**不重装音色**）
2. 空闲声部 → 用
3. 其它 MIDI 通道、`note==0` 的活跃声部 → 用
4. 抢音：取占用中 **MIDI 通道号最大**的声部（同号取序号最小＝最旧）；
   `seg1:0x0181` 若 `该声部MIDI通道 < 请求通道` → **返回 0xFF 放弃发音**

即**低 MIDI 通道号优先级高**，满载时高通道可能丢音。
音色装载（`seg1:0x01C7` → cmd 0x16）只在"声部非本 MIDI 通道占用"时执行，
指针 = `DS:0x6F4 + program*64`。

### 7.5 移调 FMOCTAVE（`seg1:0x0232`，资源 0x7510）

`note += FMOCTAVE[program]`（**有符号**字节），随后按**无符号**比较 clamp 到 0x7F。
本库 128 项只有 3 种值：`0xF4=-12`（122 条）、`0xE8=-24`（5 条）、`0xC4=-60`（1 条，
program 125 Gunshot）。第 5 节"0x7510 表用途未解"至此解决。
ch9/ch15 不移调。存储到声部记录的是**移调前**的原始音高（`seg1:0x0301`）。

移调结果在经 `seg1:0x0010` 发 cmd 0x25 之前还有一道闸门（`seg1:0x0039`：
`cmp byte ptr [bp+6], 0x5C` + `jae 0x51`，bp+6 = 移调后的 note）：**移调后音高 ≥ 0x5C(92) 时
不 key-on**（不写 FNUM、不写 0x28），而力度（cmd 0x1F）照写 → 该音在真机上**无声，但声部已被分配占用**。
即"低音区 `note + FMOCTAVE < 0` 环绕到 0x7F"的音，实际表现是静音而不是最高音。

### 7.6 音高 / 弯音（`seg3:0x0C75`、`seg1:0x03B2`）

```
FNUM = cs:0x14F4[note%12] + cs:0x150C[note%12] * bend255 / 255     block = note/12
写 0xA4+ch（block<<3|fnum9:8）后写 0xA0+ch
```

弯音：`bend14 = msb<<7|lsb`，`step = 8191/range + 1`，`base = step*range - 8192`
（range = DGROUP:0xE2，默认 2 半音），`note' = note + (bend14+base)/step - range`，
余数线性转 0-255。中位 8192 → 原音高。

### 7.7 鼓（`seg3:0x0CCB`，表 DGROUP:0x8B+note）

ch9/ch15，音域 35..81，映射到 YM2608 板载 6 种节奏（**0x7F = 哑音**）：

| 乐器 | GM 音符 |
|---|---|
| BD(0) | 35, 36 |
| SD(1) | 38, 40 |
| Top Cymbal(2) | 49, 51, 52, 55, 57, 59 |
| Hi-Hat(3) | 42, 44, 46 |
| Tom(4) | 41, 43, 45, 47, 48, 50 |
| Rim Shot(5) | 37 |
| 哑音 | 39(Hand Clap), 53, 54, 56, 58, **60-81 全部** |

电平 = `(0x40+v/2)>>2`（0..31），写 `0x18+乐器 = 0xC0|电平`，再 `0x10 = 1<<乐器` 触发。
鼓**没有释放**（`seg1:0x2CC` = `ret 8`）。
分发器在 `seg1:0x055F` 先判力度：力度 0 走 note-off 路径 → 鼓通道同样落到 0x2CC，
所以 `0x9x + 力度0` 的 note-on 在本驱动里**完全无写入**（不会敲一下鼓）。

### 7.8 软件包络 + LFO（`seg3:0x0D59`，Timer-A tick）

`seg3:0x0D27` 轮询 OPNA 状态位 1（Timer-A 标志）→ `seg3:0x100C` 应答 → `seg3:0x0D59`。
Timer-A = 0x0E3（reg 0x26；reg 0x25 从未写，按 0）→ `(1024-0xE3)*12/8MHz` = 1.1955 ms
≈ **836.5 Hz**。

每通道状态机（`ws+0x18` 低 2 位 = 0..3，bit7 = 曾开过音；`ws+0x14` = 是否在响）：

- state 3（key-on 后）→ 依字段 0x0F（LFO 起相位）：1=从 0 起，0=接全局计数，其余=延时计数
- state 1 → 减计数到 0 → state 2
- state 2 → 每 tick 步进 LFO 并调制

LFO 波形（字段 0x0A）：0=三角（溢出取反值）、1=方波（`counter/period` 的奇偶）、
2=三角（溢出翻方向，`ws+0x648` = ±1）、3=随机（`seg3:0x10AF`，乘数 0x383）、
4/5=锯齿钳 0。速率 = 字段 0x14（16 位），周期 `0xFFFF/速率` 个 tick。

每个走完步进的 tick 末尾 `ws+0x19`（LFO 计数器）自增一次（`seg3:0x0EFB inc word ptr [bx+0x19]`），
只有 state 1 递减到非 0 的那条路跳过它（`0x0E3A jmp 0x0EFE`）。方波相位（`0x108D`：`[0x654]/period` 的奇偶）
与随机步进（`0x10AF`：`[0x654] % (period|1)`）都读这个计数器的副本 `[0x654]`（`0x0E40` 处拷贝）；
随机波形状态存在 `DS:0x2876`，数据段初值为 **0** 且驱动从不播种 → 该波形在本驱动里恒为 0（哑行为）。

调制（每个 tick）：

```
颤音:  fnum += (LFO * 深度 / 0x7FFF) * fnum / 0x7FF      深度 = 字段0x19 × 字段0x23
TL:    TL[i] = clamp8(TLbase[i] + TLbase[i] * (ws[i] * LFO / 0x7FFF) / 0x7F)
       ws[i] = (记录0x2F+i & 0xF) * 记录0x1F / 15       （seg3:0x0F16 scale_knob）
```

TL 公式按 `seg3:0x0F75` 逐字节复刻（含 `xor ah,ah` 截断与 `add` 进位钳位）。

### 7.9 硬件初始化（`seg3:0x0B22` / `0x0BA4` / `0x1133`）

- 0x90-0x9E（跳过 0x93/0x97/0x9B）= 0（SSG-EG）
- 0xB4-0xB6 = 0xC0（pan L+R）；**记录里的 RL 位写进 0xB0 被硬件忽略，声像恒居中**
- 节奏：0x12=0、0x11=0x30、0x18-0x1D=0xC4
- Timer：0x27=0x30、0x26=0xE3
- key-on：`0x28 = (记录0x05 & 0x0F)<<4 | ch`（ch≥3 时 +1，对应 OPN 通道编码 0,1,2,4,5,6）；
  key-off：`0x28 = ch`

### 7.10 仍未解 / 不确定

1. Timer-A 的 reg 0x25 从未写；若真机另有初值，LFO 速率整体缩放
2. `seg3:0x10AF` 随机波形的乘数 0x383 与初值语义只能从汇编推断
3. 弯音小数部分走 C 运行时辅助例程（`0x0FA2/0x0FAE`），按 `*255/step` 实现，
   与 `*256/step` 可能差 1/255 半音
4. `MYFAIRLADY`（`seg1:0x063E`）的 0x64-0x67 扩展消息、MODM_OPEN 的实例结构初始化
   未逐行展开（不影响 MIDI 播放路径）
5. 通道记录 `+0x4C` 字节未见读取
