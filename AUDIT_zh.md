# NECOPN.DRV 复现工程 —— 疑点清单审计报告

审计对象：工作区 `D:\`（`NECOPN.DRV` + `necopn_reverse/` 逆向产物 + `necopn_player/` 复现实现）
审计依据：本文档所有反汇编结论都带地址，源码在 `necopn_reverse/out/seg1_listing.txt`、`seg3_listing.txt`；
静态数据结论全部由脚本直接读 `NECOPN.DRV` 原始字节得到（脚本见 §7）。
被审清单：`NECOPN.DRV 复现——完整疑点清单`（10 条疑点 + 零成本验证项 + 需真机 trace 项）。

**基线（本机实测，审计开始时）**：`python selftest.py` → 42/42 通过；`python core\smoke_test.py` → 24/24 通过；
`player.py testdata\demo_rich.mid -o x.wav` 正常出声（峰值 4498/32767）。

---

## 1. 结论总表

| # | 清单主张 | 审计结论 | 关键依据 |
|---|---|---|---|
| 1 | note_off 按 midi_ch 匹配会关错和弦音符；建议"先精确匹配再按 midi_ch 回退" | **不成立（清单描述的是旧代码）**。现实现已是 `(midi_ch, note, active)` 三元精确匹配、取首个；清单建议的**回退会偏离驱动**，不应加 | seg1:0x0350（note_off）+ seg1:0x01EC（查找器） |
| 2 | ref_count 语义和真机不同（真机 = "同 slot 同时响的音符数"） | **不成立**。代码里已无 `ref_count`；`VoiceState.key_alive` 对应驱动 `ws+0x14`，驱动本身就是 **0xFF/0 布尔**，不是计数器。清单引用的 `sub_1ACC/sub_1B34` 在本工程反汇编中**不存在** | seg3:0x1381（写 0xFF）、seg3:0x139E（写 0）；真实 key-on/key-off = seg3:0x130C→0x135A / 0x139A |
| 3 | `_allocate` pass 1 复用 slot 可能覆盖正在响的音符 | **不成立**。pass 1 条件 = `active && note==0 && midi_ch==req`；而本驱动里"note==0"只能由 note_off（同时清 active）或 note 值本身为 0 的音符造成 → 不构成"刚被占用还没 key_off" | seg1:0x0081–0x00D0（pass 1）；note_off 清 active = 0x038E/0x0391 |
| 4 | 119/121 音色参数映射异常（AR/DR/SR 两种取反方向都不对） | **静态部分全部核对一致；映射方向（取反）正确**。`cs:0x1239` 取反表与 `INVERT` 逐字节一致；119/121 的 head=0x38（FB=7/ALG=0）、slotmask=0xF、TL 源 0x6D/0x75/0x75/0x7F 等全部吻合。"119 无声/121 电噪"是**旧（未取反）映射**的产物，修正后消失。余下"听感"问题本环境无法判定（无实机 trace） | 取反表 seg3:0x1239；字段读取器 seg3:0x11F8；TL 路径 seg3:0x0BED；数据见 §3.1 |
| 5 | key-on slotmask 与真机不一致（真机 0x28=0xDF，Python 0xF0\|ch） | **不成立**。驱动 slotmask 直接取记录逻辑 5 = `rec[0x05]`，本库 128 条**全是 0x0F**；字段写入器 seg3:0x11C8 全驱动**无人调用** → 运行时不会改写该字节。故 0xDF 不可能由本驱动+本库产生 | seg3:0x1344–0x1377；`call 0x11c8` 0 次命中；数据见 §3.1 |
| 6 | program_change 副作用（频繁时会反复清响应中的 slot） | **是驱动的设计行为，不是缺陷**。逐句核对一致（只清本 MIDI 通道、跳过 note==0、一律 active=0、随后存 program）。"统计 CR028.MID 次数"因工作区无该文件无法做（§6） | seg1:0x0478（0x049E/0x04AA/0x04B0/0x04BF/0x04CC） |
| 7 | set_velocity / set_pitch 调用顺序可能与真机相反 | **不成立**。驱动顺序 = cmd 0x1F（力度→TL）先、cmd 0x25（key-on，内部先写 FNUM 再写 0x28）后；seg1 内**没有任何 cmd 0x20 发出点**（音高只由 key-on 内部写）→ Python 的 `set_velocity → set_pitch → key_on` 等价 | seg1:0x0010（0x1F 在 0x002C、0x25 在 0x004C）；`b020` 0 次命中 |
| 8 | LFO 的 TL 调制公式可能符号反了、会"抹掉"音符 | **公式与符号都没反**（逐句一致：`jae` → 进位才置 0，与 `s>0xFF→0` 等价）。**但顺带发现一个真实缺陷**：Python 的 LFO 计数器在 state 2 从不递增，方波/随机波形被冻结（见 §3.3） | seg3:0x0F75（TL 调制）；seg3:0x0EFB（`inc word [bx+0x19]`） |
| 9 | `_transpose` 负数环绕（note<12 变最高音） | **代码与汇编一致（这是驱动的真实行为）**，真正的后果在其下游：驱动在 key-on 前有 `note >= 0x5C(92) 就跳过 key-on` 的闸门 → **wrap 到 127 的音在真机上是"不发声"**，而 Python 会发出一个高音（见 §3.2） | seg1:0x0265–0x0273（移调/无符号 clamp）、seg1:0x0039（0x5C 闸门） |
| 10 | 鼓的 note_off 完全被忽略 | **成立且已正确实现**。"忽略"适用于鼓通道的**所有** note-off（不区分是否鼓音符）；0x9x 且力度 0 也走同一条 no-op。附带发现：Python 对"鼓通道 + 力度 0 的 note-on"仍会敲一下（§3.4） | seg1:0x04DE 分发、0x054A→0x0559→0x02CC（`ret 8`）、0x055F（力度 0 分支） |

---

## 2. 清单"一、已确认正确"部分的复核（抽查）

| 项 | 复核结果 | 依据 |
|---|---|---|
| 全局音高公式 `blk = note // 12` | ✔ 与汇编一致 | seg3:0x0C84–0x0CB4（cmd 0x20）、seg3:0x1320–0x1336（key-on 内） |
| 分配层 4 轮 + 抢音"低通道号优先" | ✔ 与汇编一致（含 `best_midi` 初值 0 的相等分支 0x014C） | seg1:0x0058–0x0192 |
| 移调 FMOCTAVE 表含义与取值 | ✔ 128 项 = -12×122 / -24×5 / -60×1（program 125） | 0x7510 实测 |
| 鼓映射表 | ✔ 35..81 全量逐项一致（含 39/53/54/56/58/60..81 哑音） | DGROUP:0x8B+note 实测 |
| 力度表 | ✔ `0（v<2）| 0x40+v/2`，128 项全对 | DGROUP:0x2E 实测 |
| 载波掩码表 | ⚠ **ALG6 行不一致**（见 §3.1-A），其余 7 行一致 | cs:0x0C55 实测 |
| SSG 未参与 | ✔ 全驱动**没有任何**对 0x00–0x0F 的写入（字面常量寄存器号只有 0x10/0x11/0x12/0x21/0x22/0x26/0x27/0x28/0x29/0x2D） | 全清单扫描，§7 脚本 4 |
| ADPCM-B 无关 | ✔ 驱动只碰 ADPCM-A（0x10 触发 / 0x18-0x1D 电平），未碰 ADPCM-B 银行 | 同上 + ymfm ADPCM-A 寄存器映射 |
| Timer-A / LFO 速率 836.5 Hz | ✔ `reg 0x26 = 0x0E3`、**`reg 0x25` 全驱动从未写**（"未写"从猜测升级为已证）；0x27=0x30；0x29 只影响 IRQ 掩码与音频无关 | seg3:0x0FE3–0x100E；scan 无 0x25 |
| 双 OPNA 端口路由 | ✔ ch>=3 → 偏移 -3 + 换端口对（0x1192 = 0x188/0x18A，0x11AB = 0x18C/0x18E） | seg3:0x135A、0x139A、0x14A6（`cmp al,3; sub al,3`） |
| 力度只作用于载波、范围 0x3F | ✔（TL 写入器 clamp 0x7F） | seg3:0x0BED、0x14A6 |

---

## 3. 本次审计新发现（可复现，均不在原清单里）

### 3.1-A `cs:0x0C55` 的 **ALG=6 行抄错**（真实缺陷，✅ 已修）

```
DRV  ALG6: [0x00, 0x7F, 0x7F, 0x7F]      # 载波 = S2,S3,S4（与 ALG5 同）
python   : [0x7F, 0x7F, 0x7F, 0x7F]      # 误写成"全载波"
```
`OPMASK` 其余 7 行与 DRV 完全一致，`REPORT.md §7.3` 与 `README.md` 的
"ALG6/7 = 全部"也只对 ALG7 成立。后果：ALG=6 的音色里，**op1（调制器）的 TL 会被
力度额外衰减**，而驱动不会 —— 实测同一音符（力度 60）写 0x40 的差异是
`python 0x2A` vs `驱动 0x09`（差 33 级 ≈ 25 dB），音色/力度响应都会不同。
本库 **ALG 分布**：{0:8, 1:8, 2:61, 3:4, 4:31, 5:12, 6:1, 7:3} → 受影响 1 条音色（program 18）。
`selftest.py` 用的是 `v0`（ALG=2），因此现有 42 条断言抓不到这个错。

### 3.2 缺少 `note >= 0x5C → 不 key-on` 闸门（真实缺陷，✅ 已修）

驱动 `seg1:0x0010` 里 cmd 0x25 之前有一道闸门：

```
0039  807e065c   cmp byte ptr [bp + 6], 0x5c     ; 移调后的 note
003D  7312       jae 0x51                        ; >= 92 → 直接跳过 key-on
```

即移调后音高 ≥ 92 时：TL 照写（cmd 0x1F 无此闸门）、**FNUM 不写、0x28 不写**
（FNUM 是写在 key-on 内部 seg3:0x130C 的）→ 真机**无声但声部已被占**。
Python 没有这道闸门，实测（FMOCTAVE[0] = -12）：

| MIDI note | 移调后 | 驱动 | Python |
|---|---|---|---|
| 5 | 127（wrap） | 不发声（跳过 key-on） | **发出高音** |
| 0 | 127（wrap） | 不发声 | **发出高音** |
| 104 | 92 | 不发声 | **发出高音** |
| 103 | 91 | 发声 | 发声 ✔ |

也就是说：清单疑点 9 描述的"低音区变最高音"在真机上的真实表现是**静音**，
Python 现在会真的发出错音。这条同时解释了"明明该有的音符没有 / 不该有的音符冒出来"。

### 3.3 LFO 计数器不递增 → **方波(shape1)/随机(shape3) 波形冻结**（真实缺陷，✅ 已修）

驱动在每个处理过的 tick 末尾对 `ws+0x19`（LFO 计数器）做 `inc`：

```
0EFB  ff4719   inc word ptr [bx + 0x19]
```

方波相位（seg3:0x108D：`[0x654]/period` 的奇偶）与随机步进（seg3:0x10AF：
`[0x654] % (period|1)`）都读这个计数器的副本 `[0x654]`（在 0x0E40 处从 `ws+0x19` 拷贝）。
Python 的 `_lfo_step` 只读 `vs.lfo_counter`，state 2 从不递增 → 计数恒为 note-on 那一刻的值。
修正前实测 8 个 tick（修后计数器随 tick 递增、方波相位交替，见 §4）：

```
prog  80 shape=1: LFO 值 = 7FFF 7FFF 7FFF 7FFF 7FFF 7FFF 7FFF 7FFF   计数器 = 1 1 1 1 1 1 1 1   (驱动应交替 7FFF/8000)
prog 102 shape=3: LFO 值 = 1234 1234 1234 1234 1234 1234 1234 1234   计数器 = 1 1 1 1 1 1 1 1   (驱动每 period 步进一次)
```

影响面：本库 shape 分布 {0:1, 1:2, 2:124, 3:1} → 受影响的只有 program 80、101（方波）、102（随机）；
其余 124 条用 shape2（三角带方向）走的是"累加器"路径，**不受影响**。
且这三条的调制深度都很小（101：vib_depth=2、trem=[0,0,0,2]；102：vib_depth=10；80：vib_depth=2），
所以听感影响是"该有的颤音变成恒定偏移/轻微走音"，不是大故障 —— 但确实是可修的一处偏差。

### 3.4 鼓通道"力度 0 的 note-on"被 Python 当鼓敲（✅ 已修）

驱动：`seg1:0x055F` 先判力度，`je 0x538` → 走 note-off 路径 → 鼓通道最终落到 0x2CC（`ret 8`，**完全无写入**）。
Python：`note_on()` 先判 `midi_ch in (9,15)` 再判 `velocity == 0` → 进入 `_drum_note_on`。
实测写寄存器：`vel=100 → (0x19,0xDC),(0x10,0x02)`；`vel=0 → (0x19,0xC0),(0x10,0x02)`（多了两次写入并触发）。
`level=0` 在 YM2608 是**最大衰减**（ymfm：`vol = (level ^ 0x1F) + (total_level ^ 0x3F)`），
所以听感影响很小；但 MIDI 里"note-off 用 note-on 力度 0"极其常见，建议按驱动改成纯 no-op。

### 3.5 低优先级 / 无害差异（记录备查，不必改）

- LFO state 3 且 `rate_a >= 2` 时，驱动跳到 0x0EFB **不做 LFO 步进**，Python 会多做一次步进 ——
  本库 `rec[0x0F]` 只有 {0:126, 1:2}，而 rate_a∈{0,1} 的两条分支驱动**确实同 tick 就步进**（0xE1E→0xE3D、0xDE0→0xE3D），
  所以实际影响 = 0。
- shape 4/5：驱动只把**返回值**钳到 0，累加器继续走；Python 直接把累加器写成 0。
  本库没有 shape 4/5 音色 → 影响 0。同理 `rate_a==1 && shape==4` 时驱动把累加器初值设 0x7FFF，Python 设 0（本库无此组合）。
- 随机波形初值：驱动 `DS:0x2876` 初值 = **0**（数据段镜像实测），LCG 恒 0（驱动自身的"哑"行为）；
  Python 原先用 0x1234 → program 102 有轻微恒定失谐。**已按驱动改 0**（见 §4 ⑤）。
- 数值细节：`imul/idiv`（向 0 截断）与 Python `//`（向下取整）在负值上会差 1 级 TL / 1 个 FNUM 单位（≤0.75 dB / ≤0.2 音分），可忽略。
- `cs:0x150C` BEND 表第 12 项（note%12 = 11）的**高字节落在 seg3 声明长度之外**（seg3 到文件 0x5283 结束，该字在 0x5282-0x5283），
  实测低字节 = 0x44 = 68 = Python 的值 ✔；真机读的是内存里相邻段的字节，属驱动怪癖，无需改。
- 初始化写流差异（无音频影响）：Python 多写一次 `0xB4..0xB6 = 0xC0`（源码 264/265 行重复）、
  多写 `0x25 = 0`（驱动从不写 0x25）；驱动写而 Python 没写的 `0x21 = 0`、`0x29 = 2/0x82`
  （ymfm 里 0x29 只是 IRQ 掩码，与音频无关）。

---

## 4. 已应用的改动（本次执行，全部有回归断言）

已按 §3 的结论直接改 `necopn_player/necopn_driver.py`，并同步修正
`necopn_reverse/REPORT.md` §7.3/§7.5/§7.7/§7.8、`necopn_player/README.md` §1/§6/§7 与 `STATUS.md` §2.2。

```python
# ① OPMASK：ALG6 行按 cs:0x0C55 改成 (0x00, 0x7F, 0x7F, 0x7F)
#    （REPORT §7.3 的"ALG6/7 = 全部"→"ALG6 = S2,S3,S4、ALG7 = 全部"；README 同步）

# ② note_on()：补上驱动 seg1:0x0039 的闸门（TL 仍写、slot 仍占）
self.set_velocity(ch, level)
if n2 >= 0x5C:            # 移调后 >= 92 → 不写 FNUM、不 key-on
    return
self.set_pitch(ch, n2, 0)
self.key_on(ch, n2)

# ③ tick()：走完步进的 tick 末尾递增 LFO 计数器（seg3:0x0EFB inc word [bx+0x19]）
#    含 st==0 分支；state 1 递减到非 0 的分支不递增（0x0E3A jmp 0x0EFE）；
#    state 3 且 rate_a>=2 的分支只置计数不步进

# ④ note_on()：鼓通道先判力度（驱动 0x055F）
if midi_ch in (9, 15):
    if velocity:                      # velocity == 0 → 驱动是空操作
        self._drum_note_on(velocity, note)
    return

# ⑤ _lfo_step() shape3：除数 = period|1（不是 rate_word|1）；
#    state = (state * 0x383) mod 0x7FFF（不是 & 0x7FFF）；VoiceState.random_state 初值 0x1234 → 0
```

**回归断言（`selftest.py`，42 → 56 项，全绿）**：`OPMASK`/`INVERT`/`VELOCITY_TABLE`/`DRUM_MAP`/
`FNUM_SEMITONE`/`BEND_DELTA` 六张表改为**从 `NECOPN.DRV` 字节直接对拍**；另加
"ALG=6 的 TL 与驱动一致""移调后 0x5C 不写 0x28/不写 FNUM 但仍占声部""0x5B 正常 key-on"
"LFO 计数器每 tick 递增""方波随时间交替 7FFF/8000""鼓通道力度 0 无写入"等行为断言。

**改动前后实测对照**（同一脚本、同一输入）：

| 检查 | 改动前 | 改动后 |
|---|---|---|
| program 18（ALG6）力度 60 写 0x40 | `0x2A` | `0x09`（= 驱动值） |
| MIDI note 104（移调 92） | 写出 0x28 + FNUM（发高音） | 不写 0x28/FNUM，只写 TL、占声部 |
| prog 80 方波 LFO（8 tick） | `7FFF` 恒定，计数器恒 1 | 计数器 2..9，400 tick 内交替 `7FFF/8000` |
| prog 102 随机 LFO | 恒 `1234`（恒定失谐） | 恒 `0000`（= 驱动，未播种） |
| 鼓 ch9 note-on vel 0 | 写 `0x19=0xC0` + `0x10=0x02` | 无写入 |
| `selftest.py` | 42/42 | **56/56** |
| `core/smoke_test.py` | 24/24 | 24/24（不受影响） |
| `demo_rich.mid` 渲染 | 峰值 4498 | 峰值 4498（该曲无 ALG6/移调越界/方形 LFO 音色，听感不变） |

---

## 5. 对清单"核心判断"的回应

清单结论"疑点 1、2 是最可能的广泛丢音来源"—— **审计不支持**：
1、2 两条在当前代码里都已经不是问题（1 已是精确匹配；2 的 ref_count 已不存在，且驱动本身就没有引用计数）。
真正能造成"丢音/错音"的可复现机制，按可能性排序是：

1. **驱动固有 + 已忠实复现**：只有 6 个 FM 声部，满载时 `seg1:0x0181`（抢不过更低 MIDI 通道号就放弃发音）→ 高通道号丢音；
   外加"同一 (通道,音高) 重叠 note-on"会留下孤儿 slot（单槽单归属模型，note_off 只关第一个）。
   这不是 bug，是驱动行为；要"更耐听"必须另加开关（STATUS.md §2.1 已记录同样的结论）。
2. **program_change 杀掉本通道正在响的音**（驱动设计，seg1:0x0478）→ GM 文件里换音色密集的段落会明显丢音。
3. **本次新发现 §3.2**：移调后 ≥92 的音在真机本就不发声，Python 现在会发出错音（听感上像"多出来的怪音"）。
4. **本次新发现 §3.1-A / §3.3**：单条音色层面的偏差（program 18 力度响应、program 80/101/102 颤音），不构成广泛丢音。

---

## 6. 零成本验证项与真机 trace 项的完成情况

**清单"四、零成本可验证项"**
1. `--no-lfo` 对比 → **已跑**（但对本项结论无判定力）：`demo_rich.mid` 峰值 4498（开）vs 4465（关），
   差异极小，因为该曲用 program 0（vib_depth=0/trem=0，无调制）。清单原本要用 CR028.MID，工作区**没有**该文件。
2. CR028.MID 事件统计 → **无法做**：工作区只有 `testdata/demo.mid`、`demo_rich.mid`（全盘搜索也只找到这两个）。
   统计脚本已写好并对这两个文件跑通（`program_change` 数、note<12 数、同 (通道,音高) 重复 note-on 数、各通道最大和弦数），拿到 CR028.MID 可直接复用。
3. `note_off` 改精确匹配 → **不需要**：现实现已是精确匹配（见疑点 1）；且"按 midi_ch 回退"与驱动不符。
4. `diag_timbre.py --all`（列 peak=0 音色）→ 该脚本**不存在**；用等价静态检查代替，结论：
   修正映射下、满力度时 **0/128** 条音色载波全静音（峰值不为 0）；用旧（未取反）映射则 82/128 条静音 ——
   这也反向证明取反映射方向是对的。

**清单"五、需要真机 trace 的项"** → 本环境无法执行（无 NP2/MAME 寄存器日志、无 `single_p119.mid`）：
0x28 写入值/次数、0x40–0x4C 与 0x50/0x60/0x70/0x80 的写入序列、0x00–0x0F 是否被写、0x25 是否有值、0x90–0x9E 初值。
其中三项已能用**代码+数据静态定论**替代：0x00–0x0F 驱动从不写（§2）；reg 0x25 驱动从不写（§2）；
本库 0x28 的 slotmask 恒 0xF（§3.1）。只有"119/121 听感是否与真机一致"必须靠 trace/录音。

---

## 7. 复现命令

```powershell
cd D:\galgame
python $env:PI_SCRATCH_DIR\audit_data2.py      # 取反表/掩码表/ALG 分布/鼓映射/表边界
python $env:PI_SCRATCH_DIR\audit_repro.py      # 和弦 note_off、ALG6 寄存器差异、LFO 冻结、0x5C 闸门
python $env:PI_SCRATCH_DIR\audit_midi_stats.py # MIDI 事件分布（当前仅 demo*.mid）
python $env:PI_SCRATCH_DIR\audit_regs.py       # 全清单扫描：驱动器写过的寄存器号
cd necopn_player; python selftest.py; python core\smoke_test.py
```
（`audit_*.py` 全部只读；本审计的代码改动只有 `necopn_player/necopn_driver.py` 与 `selftest.py`，
文档改动见 §4。）
