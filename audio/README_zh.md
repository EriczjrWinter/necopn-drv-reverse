>English version: [README.md](README.md).
# 音频对比

以下两份录音来自同一首曲子，出自 **《クリスタルリナール －逢魔の迷宮－》**
（Crystal Rinal - Ouma no Meikyuu -，D.O. 1994 年 PC-98 迷宫 RPG）。

MIDI 文件内部编号为 `CR028`。

| 文件 | 来源 |
|---|---|
| `Crystal_Rinal_CR028_real_pc9821_rom.mp3` | 在 Neko Project 21/W 模拟的 PC-9821 + Windows 95 上，由原版 NECOPN.DRV 播放并录制，使用原生 YM2608 ADPCM-A 打击乐 ROM |
| `Crystal_Rinal_CR028_python_render_rom.mp3` | 由本项目的 `necopn_player/player.py` 渲染同一份 MIDI，同样使用原生打击乐 ROM |

两份文件输入相同（同一 `CR028.MID`），未做任何编辑。

## 已知差异

Python 渲染版本**在固定位置丢失整条音轨** —— 某些 MIDI 通道会静音数秒，
而实机对应位置声音持续正常。差异一听即可分辨，不需要任何分析工具。

这是本项目目前唯一未解决的问题。详见主 README 的 "Help Wanted" 一节。