> 中文版见 [README_zh.md](README_zh.md)。
# Audio Comparison

Two recordings of the same track from **Crystal Rinal - Ouma no Meikyuu -** 
(クリスタルリナール －逢魔の迷宮－), a 1994 PC-98 dungeon RPG developed and 
published by D.O.

The MIDI file is labelled `CR028` internally.

| File | Source |
|---|---|
| `Crystal_Rinal_CR028_real_pc9821_rom.mp3` | Recorded on emulated PC-9821 (Neko Project 21/W) running Windows 95 with the original NECOPN.DRV, using the native YM2608 ADPCM-A rhythm ROM |
| `Crystal_Rinal_CR028_python_render_rom.mp3` | Rendered by `necopn_player/player.py` from the same MIDI, also with the native rhythm ROM |

Both files were produced from the same `CR028.MID` input, no editing applied.

## Known difference

The Python render **loses entire tracks at fixed positions** — some MIDI 
channels drop out for a few seconds while the real hardware plays them 
continuously. The difference is audible on a first listen.

This is the single remaining problem the project has not solved. See the 
"Help Wanted" section in the main README.