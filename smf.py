# -*- coding: utf-8 -*-
"""标准 MIDI 文件（SMF type 0/1/2）解析 —— 只取播放需要的部分。

产出绝对时间（秒）排序的事件列表：(t, kind, ch, d1, d2)
kind: 'on' | 'off' | 'cc' | 'prog' | 'bend' | 'tempo' | 'meta'

修复记录
--------
1. 早期版本在元事件分支 `continue` 时保留了 `status = 0xFF`，导致紧随其后的
   **运行状态（running status）音符事件**被误读为元事件，并按错误的长度跳过
   一大段数据 —— 症状是某些段落整段音符丢失、NOTE_OFF_MISS 密集出现。

2. 【新增】兼容 CSWare社等老GAL厂商非标准软件生成的 `FF 51 04` (Tempo 事件长度错误)。
   这会导致部分曲目的 BPM 计算全盘崩溃，音符挤在一起。此版本现在会自动提取
   前 3 字节作为有效数据，并正确跳过多余的脏字节，保持字节流完美对齐。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class Event:
    t: float
    kind: str
    ch: int = 0
    d1: int = 0
    d2: int = 0


def _read_vlq(buf: bytes, i: int, end: int) -> Tuple[int, int]:
    """读变长量。最多 4 字节（标准 SMF 上限）。越界抛 ValueError。"""
    v = 0
    for _ in range(4):
        if i >= end:
            raise ValueError(f"VLQ 越界: i={i} end={end}")
        b = buf[i]
        i += 1
        v = (v << 7) | (b & 0x7F)
        if not (b & 0x80):
            return v, i
    raise ValueError("VLQ 超过 4 字节")


def parse_smf(data: bytes, debug: bool = False) -> Tuple[List[Event], int, int]:
    """返回 (事件表, 每四分音符 tick 数, 曲长秒数)。

    debug=True 时打印每个 track 的解析摘要（事件计数、末端字节），
    便于诊断"某段音符整段丢失"之类的问题。
    """
    if data[:4] != b"MThd":
        raise ValueError("不是 SMF 文件")
    hdr_len, fmt, ntrk, division = struct.unpack_from(">IHHH", data, 4)
    if division & 0x8000:
        raise ValueError("SMPTE 时间格式暂不支持")
    tpq = division or 480

    if debug:
        print(f"[SMF] format={fmt} ntrk={ntrk} division={tpq} file={len(data)}B")

    pos = 8 + hdr_len
    tempo_map: List[Tuple[int, int]] = []                    # (tick, usec_per_quarter)
    raw_events: List[Tuple[int, int, int, int, int]] = []    # (tick, kind, ch, d1, d2)

    for track_i in range(ntrk):
        if pos + 8 > len(data) or data[pos:pos + 4] != b"MTrk":
            if debug:
                print(f"[SMF] !!! track {track_i} 提前退出: "
                      f"pos={pos:#x} tag={data[pos:pos+4]!r} "
                      f"剩余={len(data) - pos}B")
            break
        tlen = struct.unpack_from(">I", data, pos + 4)[0]
        i = pos + 8
        end = i + tlen
        if end > len(data):
            raise ValueError(f"track {track_i} 越界: end={end:#x} > file={len(data):#x}")

        tick = 0
        running_status = 0
        n_on = n_off = n_prog = n_cc = n_bend = n_meta = 0

        while i < end:
            delta, i = _read_vlq(data, i, end)
            tick += delta
            if i >= end:
                break
            b = data[i]

            # ---- 状态字节 vs 运行状态 ----
            if b & 0x80:
                if b < 0xF0:
                    running_status = b       # 只在通道消息时更新
                status = b
                i += 1
            else:
                status = running_status      # 运行状态：不读字节
                if status == 0:
                    raise ValueError(
                        f"track {track_i} @ {i:#x}: 无 running status 时收到数据字节 {b:#x}")

            # ---- 元事件 ----
            if status == 0xFF:
                if i >= end:
                    break
                meta = data[i]
                i += 1
                ln, i = _read_vlq(data, i, end)
                if i + ln > end:
                    raise ValueError(f"track {track_i}: 元事件 payload 越界")
                
                # 【核心修复：兼容 FF 51 04】
                if meta == 0x51 and ln >= 3:
                    # 不管 ln 是 3 还是 4，都只取前 3 字节计算 Tempo
                    payload = data[i:i + 3]
                    tempo_map.append(
                        (tick, (payload[0] << 16) | (payload[1] << 8) | payload[2]))
                
                # 步进指针时使用原始 ln，保证完美跳过脏数据，不破坏后续字节
                i += ln
                n_meta += 1
                # 元事件不改变 running_status
                continue

            # ---- 系统专用消息 / SysEx ----
            if status in (0xF0, 0xF7):
                ln, i = _read_vlq(data, i, end)
                if i + ln > end:
                    raise ValueError(f"track {track_i}: SysEx payload 越界")
                i += ln
                continue

            # ---- 系统实时消息（0xF8-0xFE，0 数据字节，SMF 中罕见）----
            if status >= 0xF8:
                continue

            # ---- 系统公共消息（0xF1-0xF6，SMF 中极罕见）----
            if status == 0xF1 or status == 0xF3:
                i += 1
                continue
            if status == 0xF2:
                i += 2
                continue
            if status == 0xF6:
                continue

            # ---- MIDI 通道消息 ----
            kind = status & 0xF0
            ch = status & 0x0F
            if kind in (0xC0, 0xD0):
                # 单数据字节
                if i >= end:
                    raise ValueError(f"track {track_i}: 通道消息 @ {i:#x} 截断")
                d1 = data[i]
                i += 1
                d2 = 0
            else:
                if i + 2 > end:
                    raise ValueError(f"track {track_i}: 通道消息 @ {i:#x} 截断")
                d1 = data[i]
                d2 = data[i + 1]
                i += 2

            if kind in (0x80, 0x90, 0xB0, 0xC0, 0xE0):
                raw_events.append((tick, kind, ch, d1, d2))
                if kind == 0x90 and d2 > 0:
                    n_on += 1
                elif kind == 0x80 or (kind == 0x90 and d2 == 0):
                    n_off += 1
                elif kind == 0xC0:
                    n_prog += 1
                elif kind == 0xB0:
                    n_cc += 1
                elif kind == 0xE0:
                    n_bend += 1
            # kind == 0xA0 (aftertouch) / 0xD0 (channel pressure) 忽略

        if debug:
            tail = data[max(0, end - 4):end].hex()
            print(f"[SMF] track {track_i}: len={tlen:#x} end={end:#x} tail={tail} "
                  f"on={n_on} off={n_off} prog={n_prog} cc={n_cc} "
                  f"bend={n_bend} meta={n_meta}")
        pos = end

    if not tempo_map:
        tempo_map = [(0, 500000)]
    tempo_map.sort()
    if tempo_map[0][0] > 0:
        tempo_map.insert(0, (0, 500000))

    def tick_to_sec(t: int) -> float:
        sec = 0.0
        last_tick, last_tempo = tempo_map[0]
        for idx in range(1, len(tempo_map)):
            mt, mtempo = tempo_map[idx]
            if mt >= t:
                break
            sec += (mt - last_tick) * last_tempo / 1e6 / tpq
            last_tick, last_tempo = mt, mtempo
        sec += (t - last_tick) * last_tempo / 1e6 / tpq
        return sec

    events: List[Event] = []
    name_map = {0x80: "off", 0x90: "on", 0xB0: "cc",
                0xC0: "prog", 0xE0: "bend"}
    for tick, kind, ch, d1, d2 in raw_events:
        events.append(Event(tick_to_sec(tick), name_map[kind], ch, d1, d2))
    events.sort(key=lambda e: e.t)
    total = max((e.t for e in events), default=0.0)

    if debug:
        print(f"[SMF] 总计: events={len(events)} tempo_marks={len(tempo_map)} "
              f"total={total:.2f}s")

    return events, tpq, total