# Copyright Notice

This repository contains reverse-engineering tools and a Python reimplementation 
of **NECOPN.DRV**, NEC's Windows 95/98 MIDI driver for the PC-9821 with the 
86 sound board (YM2608 / OPNA).

## Not included

- `NECOPN.DRV` — copyright NEC Corporation. Not redistributed. Users must 
  obtain it themselves; see `README.md` for SHA256 and acquisition hints.
- `ym2608_adpcm_rom.bin` — YM2608 internal ADPCM-A rhythm ROM, copyright 
  Yamaha Corporation. Not redistributed.

## Included

- Python source code, C++ wrapper, documentation — original work of the 
  contributors, MIT License (see `LICENSE`).
- `necopn_reverse/NECOPN_GM.wopn` — a derived representation of the 128-tone 
  GM voice bank extracted from `NECOPN.DRV`, converted to the public WOPN 
  format used by libOPNMIDI. Provided for research and interoperability only. 
  All rights to the original data remain with NEC.
- `necopn_reverse/voices_dump.txt` — a human-readable table of the extracted 
  voice parameters.
- `necopn_player/core/vendor/ymfm/` — ymfm source, BSD-3-Clause, original 
  license included in that directory.

## Takedown

If you are a rights holder and wish for any content to be removed, please open 
an issue.