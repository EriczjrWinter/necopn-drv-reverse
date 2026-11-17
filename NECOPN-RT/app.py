# -*- coding: utf-8 -*-
"""NECOPN-RT 图形界面（tkinter，零额外依赖）。

仿照 OPMMIDI-RT 的 app.py：
  - 不支持切换音色表（NECOPN.DRV 的 128 条音色是驱动的一部分）
  - 支持循环播放、指定 RHY 目录、仅 SSG 鼓、导出 WAV、中英文切换
  - EXE 里**不带任何 WAV/ROM**；RHY 目录由用户指定，缺失时自动退回 SSG 鼓

    python app.py
    python app.py --lang en
    python app.py --selfcheck
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from session import BASE, Config, default_rhythm_dir, render_wav
from session import Session

APP_TITLE = "NECOPN-RT"

STRINGS = {
    "zh": {
        "title": "OPNA 实时播放器",
        "midi": "MIDI",
        "rhythm": "RHY 目录",
        "rhythm_hint": "（不打包；缺失时自动用 SSG 鼓）",
        "ssg_only": "仅 SSG 鼓（忽略 RHY 采样）",
        "core": "合成核心",
        "core_fmgen": "fmgen · 完整 OPNA",
        "core_nuked": "Nuked 组合 · FM+SSG+PCM",
        "core_nuked1": "Nuked 单芯片 · 6 声道",
        "volume": "音量",
        "volume_hint": "（播放中实时生效）",
        "browse": "浏览…",
        "play": "▶ 播放",
        "stop": "■ 停止",
        "lang": "语言",
        "export": "导出 WAV…",
        "export_t": "导出 WAV",
        "export_done": "已导出：\n{path}\n\n{sr} Hz / {sec:.1f}s / peak {peak}/32767",
        "export_cancel": "导出已取消",
        "export_busy": "正在导出 {pct}%…",
        "loop": "循环播放",
        "err_no_midi": "先选一个 MIDI 文件。",
        "err_no_midi_t": "缺少 MIDI",
        "err_play": "播放失败",
        "status_playing": "核心 {core} | 鼓 {drums} | {n} 事件 {sec:.1f}s",
        "xrun": "underrun={u}  xrun={x}",
        "ready": "就绪",
        "drums_rhy": "RHY 采样 {n}/6",
        "drums_ssg": "SSG 合成鼓",
        "drums_ssg_fallback": "SSG 合成鼓（RHY 缺失）",
        "env_rhy": "RHY: {p}",
        "env_no_rhy": "RHY 未指定 → SSG 兜底",
        "env_ssg_only": "鼓：仅 SSG",
        "warn_rhy_t": "RHY 目录",
        "warn_rhy": "指定的 RHY 目录不可用，将自动退回 SSG 合成鼓。",
        "device_fail": "找不到可用的音频输出设备（需要 48 kHz 立体声）。",
    },
    "en": {
        "title": "OPNA Realtime Player",
        "midi": "MIDI",
        "rhythm": "RHY folder",
        "rhythm_hint": "(not bundled; SSG fallback if missing)",
        "ssg_only": "SSG drums only (ignore RHY samples)",
        "core": "Core",
        "core_fmgen": "fmgen · full OPNA",
        "core_nuked": "Nuked mix · FM+SSG+PCM",
        "core_nuked1": "Nuked single-chip · 6ch",
        "volume": "Volume",
        "volume_hint": "(applies live)",
        "browse": "Browse…",
        "play": "▶ Play",
        "stop": "■ Stop",
        "lang": "Language",
        "export": "Export WAV…",
        "export_t": "Export WAV",
        "export_done": "Exported:\n{path}\n\n{sr} Hz / {sec:.1f}s / peak {peak}/32767",
        "export_cancel": "Export cancelled",
        "export_busy": "Exporting {pct}%…",
        "loop": "Loop playback",
        "err_no_midi": "Pick a MIDI file first.",
        "err_no_midi_t": "Missing MIDI",
        "err_play": "Playback failed",
        "status_playing": "core {core} | drums {drums} | {n} events {sec:.1f}s",
        "xrun": "underrun={u}  xrun={x}",
        "ready": "Ready",
        "drums_rhy": "RHY samples {n}/6",
        "drums_ssg": "SSG drums",
        "drums_ssg_fallback": "SSG drums (RHY missing)",
        "env_rhy": "RHY: {p}",
        "env_no_rhy": "RHY unset → SSG fallback",
        "env_ssg_only": "Drums: SSG only",
        "warn_rhy_t": "RHY folder",
        "warn_rhy": "The selected RHY folder is unusable; falling back to SSG drums.",
        "device_fail": "No usable audio output device (48 kHz stereo).",
    },
}

CORE_LABEL = {"fmgen": "core_fmgen", "nuked": "core_nuked", "nuked1": "core_nuked1"}


def pick_wasapi():
    """优先选 WASAPI 的默认输出（延迟最低、48 kHz 最稳）。"""
    try:
        import sounddevice as sd
        devs = sd.query_devices()
        default_name = ""
        try:
            default_name = sd.query_devices(sd.default.device[1])["name"]
        except Exception:
            pass
        for i, d in enumerate(devs):
            if (d["max_output_channels"] >= 2
                    and sd.query_hostapis(d["hostapi"])["name"] == "Windows WASAPI"
                    and default_name and d["name"] == default_name):
                return i
        for i, d in enumerate(devs):
            if (d["max_output_channels"] >= 2
                    and sd.query_hostapis(d["hostapi"])["name"] == "Windows WASAPI"):
                return i
        return sd.default.device[1]
    except Exception:
        return None


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lang = cfg.lang if cfg.lang in STRINGS else "zh"
        self.session: Session | None = None
        self._exp = {"running": False, "frac": 0.0, "done": None}
        self._exp_cancel = threading.Event()
        self._device = pick_wasapi()
        self.root = tk.Tk()
        self.root.geometry("780x440")
        self.root.minsize(720, 420)
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._poll()

    # ---------------- i18n ----------------
    def t(self, key: str) -> str:
        return STRINGS[self.lang].get(key, key)

    # ---------------- UI ----------------
    def _build(self):
        self.root.title(f"{self.t('title')}  ·  {APP_TITLE}")
        if getattr(self, "frm", None) is not None:
            self.frm.destroy()
        if getattr(self, "status_bar", None) is not None:
            self.status_bar.destroy()

        pad = {"padx": 6, "pady": 4}
        frm = ttk.Frame(self.root, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        self.frm = frm

        def row(r, text):
            ttk.Label(frm, text=text).grid(row=r, column=0, sticky="w", **pad)

        # MIDI
        row(0, self.t("midi"))
        self.v_midi = tk.StringVar(value=self.v_midi.get() if hasattr(self, "v_midi") else self.cfg.midi)
        ttk.Entry(frm, textvariable=self.v_midi).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(frm, text=self.t("browse"), command=self.pick_midi).grid(row=0, column=2, **pad)

        # RHY
        row(1, self.t("rhythm"))
        self.v_rhythm = tk.StringVar(
            value=self.v_rhythm.get() if hasattr(self, "v_rhythm") else self.cfg.rhythm_dir)
        self.e_rhythm = ttk.Entry(frm, textvariable=self.v_rhythm)
        self.e_rhythm.grid(row=1, column=1, sticky="ew", **pad)
        self.b_rhythm = ttk.Button(frm, text=self.t("browse"), command=self.pick_rhythm)
        self.b_rhythm.grid(row=1, column=2, **pad)

        # 仅 SSG
        self.v_ssg = tk.BooleanVar(
            value=self.v_ssg.get() if hasattr(self, "v_ssg") else self.cfg.ssg_only)
        self.cb_ssg = ttk.Checkbutton(frm, text=self.t("ssg_only"), variable=self.v_ssg,
                                      command=self.on_ssg)
        self.cb_ssg.grid(row=2, column=1, sticky="w", **pad)
        ttk.Label(frm, text=self.t("rhythm_hint")).grid(row=2, column=2, sticky="w", **pad)

        # 核心
        row(3, self.t("core"))
        self.v_core = tk.StringVar(
            value=self.v_core.get() if hasattr(self, "v_core") else self.cfg.core)
        bar = ttk.Frame(frm)
        bar.grid(row=3, column=1, columnspan=2, sticky="w", **pad)
        for key in ("fmgen", "nuked", "nuked1"):
            ttk.Radiobutton(bar, text=self.t(CORE_LABEL[key]), value=key,
                            variable=self.v_core, command=self.on_core).pack(side="left", padx=6)

        # 音量
        row(4, self.t("volume"))
        self.v_vol = tk.DoubleVar(value=self.cfg.volume)
        ttk.Scale(frm, from_=0.0, to=1.5, variable=self.v_vol,
                  command=self.on_volume).grid(row=4, column=1, sticky="ew", **pad)
        ttk.Label(frm, text=self.t("volume_hint")).grid(row=4, column=2, sticky="w", **pad)

        # 语言
        row(5, self.t("lang"))
        self.v_lang = tk.StringVar(value="中文" if self.lang == "zh" else "English")
        lcb = ttk.Combobox(frm, textvariable=self.v_lang, state="readonly",
                           values=["中文", "English"], width=10)
        lcb.grid(row=5, column=1, sticky="w", **pad)
        lcb.bind("<<ComboboxSelected>>", lambda _e: self.on_lang())

        # 控制
        ctl = ttk.Frame(frm)
        ctl.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(10, 2))
        self.btn_play = ttk.Button(ctl, text=self.t("play"), command=self.on_play)
        self.btn_play.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(ctl, text=self.t("stop"), command=self.on_stop,
                                   state="disabled")
        self.btn_stop.pack(side="left", padx=4)
        self.btn_export = ttk.Button(ctl, text=self.t("export"), command=self.on_export)
        self.btn_export.pack(side="left", padx=4)

        # 循环
        self.v_loop = tk.BooleanVar(
            value=self.v_loop.get() if hasattr(self, "v_loop") else self.cfg.loop)
        ttk.Checkbutton(frm, text=self.t("loop"), variable=self.v_loop,
                        command=self.on_loop).grid(row=7, column=0, columnspan=3,
                                                   sticky="w", **pad)

        # 进度
        self.v_prog = tk.DoubleVar(value=0.0)
        ttk.Progressbar(frm, variable=self.v_prog, maximum=100).grid(
            row=8, column=0, columnspan=3, sticky="ew", **pad)
        self.v_time = tk.StringVar(value="0.0 / 0.0 s")
        ttk.Label(frm, textvariable=self.v_time).grid(row=9, column=0, columnspan=2,
                                                     sticky="w", **pad)

        # 状态栏
        self.v_status = tk.StringVar(value=self._env_status())
        bar2 = ttk.Label(self.root, textvariable=self.v_status, relief="sunken",
                         anchor="w", padding=4)
        bar2.pack(fill="x", side="bottom")
        self.status_bar = bar2

        self.on_ssg()

    def _env_status(self) -> str:
        parts = [BASE]
        if self.cfg.ssg_only:
            parts.append(self.t("env_ssg_only"))
        elif self.cfg.rhythm_dir and os.path.isdir(self.cfg.rhythm_dir):
            parts.append(self.t("env_rhy").format(p=os.path.basename(self.cfg.rhythm_dir)))
        else:
            parts.append(self.t("env_no_rhy"))
        return " | ".join(parts)

    # ---------------- 交互 ----------------
    def pick_midi(self):
        p = filedialog.askopenfilename(
            filetypes=[("MIDI", "*.mid *.midi"), ("All", "*.*")])
        if p:
            self.v_midi.set(p)

    def pick_rhythm(self):
        p = filedialog.askdirectory(initialdir=self.v_rhythm.get() or BASE)
        if p:
            self.v_rhythm.set(p)
            self.cfg.rhythm_dir = p
            self.v_status.set(self._env_status())

    def on_ssg(self):
        ssg = bool(self.v_ssg.get())
        state = "disabled" if ssg else "normal"
        try:
            self.e_rhythm.configure(state=state)
            self.b_rhythm.configure(state=state)
        except tk.TclError:
            pass
        self.cfg.ssg_only = ssg
        if not ssg and not self.v_rhythm.get().strip():
            self.v_rhythm.set(default_rhythm_dir())
            self.cfg.rhythm_dir = self.v_rhythm.get()
        self.v_status.set(self._env_status())

    def on_core(self):
        self.cfg.core = self.v_core.get()
        self.v_status.set(self._env_status())

    def on_volume(self, _v=None):
        self.cfg.volume = float(self.v_vol.get())
        if self.session and self.session.renderer:
            self.session.renderer.master = self.cfg.volume

    def on_loop(self):
        self.cfg.loop = bool(self.v_loop.get())
        self.cfg.save()

    def on_lang(self):
        self.lang = "en" if self.v_lang.get() == "English" else "zh"
        self.cfg.lang = self.lang
        self.cfg.save()
        self._build()

    # ---------------- 播放 ----------------
    def _cfg_from_ui(self) -> Config:
        c = Config(**{k: getattr(self.cfg, k) for k in self.cfg.__dataclass_fields__})
        c.midi = self.v_midi.get().strip()
        c.rhythm_dir = self.v_rhythm.get().strip()
        c.ssg_only = bool(self.v_ssg.get())
        c.core = self.v_core.get()
        c.volume = float(self.v_vol.get())
        c.loop = bool(self.v_loop.get())
        c.lang = self.lang
        return c

    def on_play(self):
        cfg = self._cfg_from_ui()
        if not cfg.midi or not os.path.exists(cfg.midi):
            messagebox.showerror(self.t("err_no_midi_t"), self.t("err_no_midi"))
            return
        if cfg.rhythm_dir and not os.path.isdir(cfg.rhythm_dir) and not cfg.ssg_only:
            messagebox.showwarning(self.t("warn_rhy_t"), self.t("warn_rhy"))
            cfg.rhythm_dir = ""
        self.on_stop()
        try:
            self.session = Session(cfg)
            self.session.build()
            self.session.start(device=self._device)
        except Exception as e:
            self.session = None
            messagebox.showerror(self.t("err_play"),
                                 f"{type(e).__name__}: {e}\n\n{traceback.format_exc(limit=2)}")
            return
        self.cfg = cfg
        cfg.save()
        self.btn_play["state"] = "disabled"
        self.btn_stop["state"] = "normal"
        s = self.session
        if cfg.ssg_only:
            drums = self.t("drums_ssg")
        elif s.rhythm_notes:
            drums = self.t("drums_rhy").format(n=s.rhythm_notes)
        else:
            drums = self.t("drums_ssg_fallback")
        self.v_status.set(self.t("status_playing").format(
            core=cfg.core, drums=drums, n=s.n_events, sec=s.total_sec))

    def on_stop(self):
        if self._exp["running"]:
            self._exp_cancel.set()
        if self.session:
            try:
                self.session.close()
            finally:
                self.session = None
        self.btn_play["state"] = "normal"
        self.btn_stop["state"] = "disabled"
        self.v_prog.set(0.0)

    # ---------------- 导出 ----------------
    def on_export(self):
        cfg = self._cfg_from_ui()
        if not cfg.midi or not os.path.exists(cfg.midi):
            messagebox.showerror(self.t("err_no_midi_t"), self.t("err_no_midi"))
            return
        if self._exp["running"]:
            return
        default = os.path.splitext(os.path.basename(cfg.midi))[0] + ".wav"
        path = filedialog.asksaveasfilename(
            defaultextension=".wav", initialfile=default,
            filetypes=[("WAV", "*.wav")],
            initialdir=os.path.dirname(cfg.midi) or BASE)
        if not path:
            return

        self.on_stop()
        self.cfg = cfg
        cfg.save()
        self._exp.update(running=True, frac=0.0, done=None)
        self._exp_cancel.clear()
        self.btn_play["state"] = "disabled"
        self.btn_export["state"] = "disabled"
        threading.Thread(target=self._export_worker, args=(cfg, path), daemon=True).start()

    def _export_worker(self, cfg, path):
        try:
            info = render_wav(cfg, path,
                              progress=lambda f: self._exp.__setitem__("frac", f),
                              cancelled=self._exp_cancel.is_set)
            self._exp["done"] = info
        except Exception as e:
            self._exp["done"] = {"error": f"{type(e).__name__}: {e}"}
        finally:
            self._exp["running"] = False

    def _poll_export(self):
        if self._exp["running"]:
            self.v_prog.set(self._exp["frac"] * 100)
            self.v_status.set(self.t("export_busy").format(
                pct=int(self._exp["frac"] * 100)))
            return
        info = self._exp.get("done")
        if not info:
            return
        self._exp["done"] = None
        self.btn_play["state"] = "normal"
        self.btn_export["state"] = "normal"
        if "error" in info:
            messagebox.showerror(self.t("export_t"), info["error"])
        elif not info.get("complete"):
            messagebox.showinfo(self.t("export_t"), self.t("export_cancel"))
        else:
            messagebox.showinfo(self.t("export_t"), self.t("export_done").format(
                path=info["path"], sr=info["sr"], sec=info["seconds"],
                peak=info["peak"]))
        self.v_status.set(self._env_status())

    # ---------------- 轮询 ----------------
    def _poll(self):
        self._poll_export()
        s = self.session
        if s and s.total_sec > 0 and not self._exp["running"]:
            pos = s.position
            self.v_prog.set(min(100.0, pos / s.total_sec * 100))
            self.v_time.set(f"{pos:.1f} / {s.total_sec:.1f} s   "
                            + self.t("xrun").format(u=s.underruns, x=s.xruns))
            if s.finished:
                if self.cfg.loop:
                    try:
                        s.seek(0.0)
                    except Exception:
                        self.on_stop()
                else:
                    self.on_stop()
        self.root.after(150, self._poll)

    def on_close(self):
        self.on_stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ---------------- 自检（EXE 打包后也能跑）----------------
def selfcheck() -> int:
    lines = []
    ok = True

    def log(s):
        nonlocal ok
        lines.append(str(s))

    from session import app_dir
    ad = app_dir()
    log(f"frozen={getattr(sys, 'frozen', False)}")
    log(f"APP_DIR={ad}")
    log(f"BASE={BASE}")
    log(f"python={sys.version.split()[0]}")
    for name in ("opn2.dll", "ssg.dll", os.path.join("core", "necopna_fmgen.dll")):
        p = os.path.join(ad, name)
        exists = os.path.exists(p)
        log(f"{name}: {'OK' if exists else 'MISSING'} ({p})")
        ok &= exists

    try:
        from necopn_core import YmfmCore
        c = YmfmCore(8_000_000)
        log(f"fmgen core: OK sr={c.sample_rate()}")
        c.close()
    except Exception as e:
        log(f"fmgen core: FAIL {e}")
        ok = False

    try:
        from opn2_core import NukedOpn2
        n = NukedOpn2(8_000_000)
        log(f"Nuked OPN2 core: OK sr={n.sample_rate()}")
        n.close()
    except Exception as e:
        log(f"Nuked OPN2 core: FAIL {e}")
        ok = False

    try:
        cfg = Config.load()
        log(f"RHY dir: {cfg.rhythm_dir or '(unset)'} "
            f"{'exists' if os.path.isdir(cfg.rhythm_dir or '') else 'missing'}")
        if os.path.isdir(cfg.rhythm_dir or ""):
            n = sum(1 for f in os.listdir(cfg.rhythm_dir)
                    if f.lower().endswith(".wav"))
            log(f"RHY wav files: {n}（需要 6 个）")
    except Exception as e:
        log(f"RHY: {e}")

    try:
        import sounddevice as sd
        outs = [d for d in sd.query_devices() if d["max_output_channels"] > 0]
        log(f"sounddevice: OK, {len(outs)} output devices")
    except Exception as e:
        log(f"sounddevice: FAIL {e}")
        ok = False

    text = "\n".join(lines)
    try:
        with open(os.path.join(BASE, "selfcheck.log"), "w", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass
    try:
        print(text)
    except Exception:
        pass
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--lang", choices=["zh", "en"], default=None)
    args = ap.parse_args(argv)
    if args.selfcheck:
        return selfcheck()
    cfg = Config.load()
    if args.lang:
        cfg.lang = args.lang
    App(cfg).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
