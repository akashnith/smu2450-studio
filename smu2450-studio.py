"""Resistive Switching - SMU 2450.

Keithley 2450 has no hardware pulse generator: every pulse here is software
timed over VISA, so the shortest trustworthy width is MIN_PULSE_S (5 ms).
Shorter requests are clamped. Keep NPLC <= 0.2 for short reads.
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext, simpledialog
from tkinter import font as tkfont
import threading
import queue
import time
import csv
import math
import os
import sys
from datetime import datetime


def _enable_dpi_awareness():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_enable_dpi_awareness()

try:
    import pyvisa
    PYVISA_OK = True
except ImportError:
    PYVISA_OK = False

try:
    import numpy as np
    NUMPY_OK = True
except ImportError:
    NUMPY_OK = False
    class _NP:
        @staticmethod
        def linspace(a, b, n):
            if n < 2: return [a]
            return [a + (b - a) * i / (n - 1) for i in range(n)]
        @staticmethod
        def logspace(a, b, n):
            if n < 2: return [10 ** a]
            return [10 ** (a + (b - a) * i / (n - 1)) for i in range(n)]
        @staticmethod
        def geomspace(a, b, n):
            if n < 2: return [a]
            ratio = (b / a) ** (1 / (n - 1))
            return [a * ratio ** i for i in range(n)]
        @staticmethod
        def log10(x):
            if hasattr(x, '__iter__'):
                return [math.log10(abs(v)) if abs(v) > 0 else -15 for v in x]
            return math.log10(abs(x)) if abs(x) > 0 else -15
    np = _NP()

try:
    import matplotlib as mpl
    mpl.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

    mpl.rcParams.update({
        'font.family':                     'serif',
        'font.serif':                      ['Times New Roman', 'DejaVu Serif'],
        'font.sans-serif':                 ['Arial', 'Helvetica'],
        'font.size':                       9,
        'axes.titlesize':                  10,
        'axes.labelsize':                  9,
        'xtick.labelsize':                 8,
        'ytick.labelsize':                 8,
        'legend.fontsize':                 8,
        'figure.titlesize':                11,
        'mathtext.fontset':                'stix',
        'lines.linewidth':                 1.2,
        'lines.markersize':                4,
        'lines.markeredgewidth':           0.5,
        'axes.linewidth':                  0.8,
        'axes.grid':                       True,
        'grid.linestyle':                  '--',
        'grid.alpha':                      0.3,
        'xtick.direction':                 'in',
        'ytick.direction':                 'in',
        'figure.dpi':                      100,
        'figure.facecolor':                'white',
        'axes.facecolor':                  'white',
        'axes.edgecolor':                  '#444444',
        'grid.color':                      '#CCCCCC',
        'savefig.dpi':                     600,
        'savefig.bbox':                    'tight',
    })
    MATPLOTLIB_OK = True
except ImportError:
    MATPLOTLIB_OK = False


C = {
    "bg":      "#FFFFFF",
    "panel":   "#FFFFFF",
    "panel2":  "#F5F5F5",
    "strip":   "#EFEFEF",
    "border":  "#DCDCDC",
    "border2": "#C0C0C0",

    "text":    "#1A1A1A",
    "text2":   "#4D4D4D",
    "muted":   "#8A8A8A",

    "accent":  "#1A1A1A",
    "accent2": "#3D3D3D",
    "accent3": "#2B2B2B",
    "accent4": "#5A5A5A",
    "accent5": "#6E6E6E",

    "entry":   "#FFFFFF",
    "entry2":  "#FAFAFA",
    "btn":     "#FAFAFA",
    "btn_h":   "#ECECEC",
    "btn_act": "#E0E0E0",

    "red":     "#B3261E",
    "good":    "#2E7D32",
    "warn":    "#8A6100",
}

FONT_UI   = "Segoe UI"
FONT_CODE = "Consolas"

_FONT_BASE = {
    "FONT_MAIN":   (FONT_UI,   13, ""),
    "FONT_LABEL":  (FONT_UI,   12, ""),
    "FONT_SMALL":  (FONT_UI,   11, ""),
    "FONT_TITLE":  (FONT_UI,   18, "bold"),
    "FONT_HEADER": (FONT_UI,   13, "bold"),
    "FONT_MONO":   (FONT_CODE, 12, ""),
    "FONT_BIG":    (FONT_CODE, 15, "bold"),
}

UI_SCALE = 1.0
FIG_DPI  = 100
UI_MODE  = "auto"
MON_KEY  = ""

BIG_SCREEN_BOOST = 1.25
BIG_SCREEN_PX    = 2400

_SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".smu2450_studio.json")
_UI_PREFS = {}
_FONTS   = {}
_FIGURES = []
_SCALED  = []


def S(px):
    return int(round(px * UI_SCALE))


def scale_metric(widget, **opts):
    for k, v in opts.items():
        _SCALED.append((widget, k, v))
        try:
            widget.config(**{k: S(v)})
        except Exception:
            pass
    return widget


def mk_figure(figsize, dpi=None, **kw):
    fig = Figure(figsize=figsize, dpi=dpi or FIG_DPI, **kw)
    _FIGURES.append(fig)
    return fig


def monitor_key(root):
    dpi, w, h = _monitor_metrics(root)
    if not w:
        return "dpi%d" % int(dpi or 96)
    return "%dx%d@%d" % (w, h or 0, int(dpi or 96))


def load_ui_pref():
    global _UI_PREFS
    _UI_PREFS = {}
    try:
        import json
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("scales", {})
        if isinstance(raw, dict):
            for k, v in raw.items():
                _UI_PREFS[k] = "auto" if v == "auto" else float(v)
        old = data.get("ui_scale")
        if old is not None and not _UI_PREFS:
            _UI_PREFS["*"] = "auto" if old == "auto" else float(old)
    except Exception:
        _UI_PREFS = {}
    return _UI_PREFS


def save_ui_pref():
    try:
        import json
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump({"scales": _UI_PREFS}, f, indent=1)
    except Exception:
        pass


def pref_for(key):
    if key in _UI_PREFS:
        return _UI_PREFS[key]
    return _UI_PREFS.get("*", "auto")


def _monitor_metrics(root):
    if sys.platform != "win32":
        return (None, None, None)
    try:
        import ctypes

        hwnd = int(root.winfo_id())
        dpi = None
        try:
            d = ctypes.windll.user32.GetDpiForWindow(hwnd)
            if d:
                dpi = float(d)
        except Exception:
            pass

        class _RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class _MONINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                        ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]

        hmon = ctypes.windll.user32.MonitorFromWindow(hwnd, 2)
        if dpi is None:
            x = ctypes.c_uint(); y = ctypes.c_uint()
            if ctypes.windll.shcore.GetDpiForMonitor(
                    hmon, 0, ctypes.byref(x), ctypes.byref(y)) == 0:
                dpi = float(x.value)

        mi = _MONINFO(); mi.cbSize = ctypes.sizeof(_MONINFO)
        w = h = None
        if ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            w = mi.rcMonitor.right - mi.rcMonitor.left
            h = mi.rcMonitor.bottom - mi.rcMonitor.top
        return (dpi, w, h)
    except Exception:
        return (None, None, None)


def detect_scale(root):
    dpi, mw, mh = _monitor_metrics(root)
    if dpi is None:
        try:
            dpi = float(root.winfo_fpixels("1i"))
        except Exception:
            dpi = 96.0
    scale = dpi / 96.0
    big = (mw >= BIG_SCREEN_PX) if mw else (dpi >= 144.0)
    if scale < 1.05 and big and mw:
        scale = min(mw / 1920.0, (mh / 1080.0) if mh else 2.0)
    if big:
        scale *= BIG_SCREEN_BOOST
    return max(1.0, min(scale, 3.0))


def _rescale_figures():
    for fig in list(_FIGURES):
        try:
            cv = getattr(fig, "canvas", None)
            w_px = h_px = 0
            if cv is not None:
                try:
                    tkw = cv.get_tk_widget()
                    w_px, h_px = tkw.winfo_width(), tkw.winfo_height()
                except Exception:
                    pass
            try:
                fig._original_dpi = 100.0
            except Exception:
                pass
            fig.set_dpi(FIG_DPI)
            if w_px > 1 and h_px > 1:
                fig.set_size_inches(w_px / float(FIG_DPI),
                                    h_px / float(FIG_DPI), forward=False)
            if cv is not None:
                cv.draw_idle()
        except Exception:
            pass


def apply_scaling(root, scale=None, live=False):
    global UI_SCALE, FIG_DPI, UI_MODE, MON_KEY
    MON_KEY = monitor_key(root)
    UI_MODE = pref_for(MON_KEY)
    if scale is None:
        scale = detect_scale(root) if UI_MODE == "auto" else float(UI_MODE)
    UI_SCALE = max(0.8, min(float(scale), 3.0))
    FIG_DPI = int(round(100 * UI_SCALE))

    for name, (fam, px, style) in _FONT_BASE.items():
        size = -max(9, int(round(px * UI_SCALE)))
        weight = "bold" if style == "bold" else "normal"
        f = _FONTS.get(name)
        if f is None:
            try:
                f = tkfont.Font(root=root, name="NS_" + name, family=fam,
                                size=size, weight=weight, exists=False)
            except Exception:
                f = tkfont.Font(root=root, family=fam, size=size, weight=weight)
            _FONTS[name] = f
            globals()[name] = f
        else:
            f.configure(family=fam, size=size, weight=weight)

    try:
        root.tk.call("tk", "scaling", UI_SCALE * 96.0 / 72.0)
    except Exception:
        pass
    if MATPLOTLIB_OK:
        mpl.rcParams["figure.dpi"] = FIG_DPI

    if live:
        init_ttk_styles(root)
        for widget, opt, base in list(_SCALED):
            try:
                widget.config(**{opt: S(base)})
            except Exception:
                pass
        _rescale_figures()
    return UI_SCALE


def init_ttk_styles(root):
    sty = ttk.Style(root)
    try:
        sty.theme_use("clam")
    except Exception:
        pass

    sty.configure(".", background=C["bg"], foreground=C["text"],
                  fieldbackground=C["entry"], font=FONT_LABEL)

    sty.configure("TNotebook", background=C["panel2"], borderwidth=0,
                  tabmargins=[0, S(4), 0, 0])
    sty.configure("TNotebook.Tab", background=C["panel2"], foreground=C["text2"],
                  padding=[S(7), S(6)], font=FONT_SMALL, borderwidth=1)
    sty.map("TNotebook.Tab",
            background=[("selected", C["panel"]), ("active", C["btn_h"])],
            foreground=[("selected", C["text"]), ("active", C["text"])],
            expand=[("selected", [0, 0, 0, S(2)])])

    sty.configure("TCombobox", fieldbackground=C["entry"], background=C["btn"],
                  foreground=C["text"], arrowcolor=C["text2"],
                  bordercolor=C["border"], lightcolor=C["border"],
                  darkcolor=C["border"], selectbackground=C["entry"],
                  selectforeground=C["text"], padding=S(3))
    sty.map("TCombobox", fieldbackground=[("readonly", C["entry"])],
            background=[("active", C["btn_h"])])
    root.option_add("*TCombobox*Listbox.background", C["entry"])
    root.option_add("*TCombobox*Listbox.foreground", C["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", C["btn_act"])
    root.option_add("*TCombobox*Listbox.selectForeground", C["text"])

    sty.configure("Vertical.TScrollbar", background=C["btn"], troughcolor=C["panel2"],
                  bordercolor=C["panel2"], arrowcolor=C["text2"],
                  lightcolor=C["btn"], darkcolor=C["btn"], width=S(12))
    sty.configure("Horizontal.TScrollbar", background=C["btn"], troughcolor=C["panel2"],
                  bordercolor=C["panel2"], arrowcolor=C["text2"],
                  lightcolor=C["btn"], darkcolor=C["btn"])
    sty.map("Vertical.TScrollbar", background=[("active", C["btn_act"])])

    sty.configure("N.Horizontal.TProgressbar", troughcolor=C["panel2"],
                  background=C["text2"], bordercolor=C["border"],
                  lightcolor=C["text2"], darkcolor=C["text2"],
                  borderwidth=0, thickness=S(6))
    return sty


MIN_PULSE_S = 5e-3
MIN_DELAY_S = 1e-3


def clamp_pulse(t):
    try:
        t = float(t)
    except (TypeError, ValueError):
        return MIN_PULSE_S
    return t if t >= MIN_PULSE_S else MIN_PULSE_S


PUB_COLORS = [
    '#0077BB', '#EE7733', '#009988', '#CC3311',
    '#33BBEE', '#EE3377', '#BBBBBB', '#332288',
    '#44AA99', '#88CCEE', '#DDCC77', '#CC6677'
]
def pub_color(idx): return PUB_COLORS[int(idx) % len(PUB_COLORS)]

SPARSE_TARGETS = [1, 5, 10, 20, 50, 100, 200, 500, 1000, 1500, 2000, 5000, 10000]
def select_plot_indices(n_total, threshold=100):
    if n_total <= threshold: return list(range(1, n_total + 1))
    keep = set([c for c in SPARSE_TARGETS if c <= n_total])
    keep.add(n_total)
    return sorted(keep)


def safe_path(path: str) -> str:
    if not os.path.exists(path): return path
    base, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(f"{base}_{n}{ext}"): n += 1
    return f"{base}_{n}{ext}"

def test_dir(app_vars, test_name: str, seq_folder: str | None = None) -> str | None:
    base = app_vars["dir"].get()
    if not base or not os.path.isdir(base): return None
    device_id  = app_vars["device"].get().strip() or "Device_01"
    run_label  = seq_folder or app_vars["run_label"].get().strip() or "RUN1"
    d = os.path.join(base, device_id, run_label, test_name)
    try: os.makedirs(d, exist_ok=True); return d
    except Exception: return None

def _fname_stem(app_vars, test_name, run_num):
    s = app_vars["sample"].get().strip() or "Sample"
    d = app_vars["device"].get().strip() or "Device"
    dia = app_vars["dia"].get().strip() or "0"
    return f"{s}_{d}_{test_name}_dia{dia}_run{run_num:03d}"

def init_live_save(app_vars, test_name, run_num, headers, seq_folder=None):
    td = test_dir(app_vars, test_name, seq_folder)
    if td is None: return None
    path = safe_path(os.path.join(td, _fname_stem(app_vars, test_name, run_num) + ".csv"))
    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(headers)
        return path
    except Exception: return None

def append_live_save(filepath, row_data):
    if not filepath: return
    try:
        with open(filepath, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row_data)
    except Exception: pass

def auto_save_fig(app_vars, test_name, fig_id, fig, seq_folder=None):
    if not MATPLOTLIB_OK or fig is None: return
    td = test_dir(app_vars, test_name, seq_folder)
    if td is None: return
    s = app_vars["sample"].get().strip() or "Sample"
    d = app_vars["device"].get().strip() or "Device"
    dia = app_vars["dia"].get().strip() or "0"
    fname = f"{s}_{d}_{test_name}_dia{dia}_fig{fig_id:03d}.png"
    try: fig.savefig(safe_path(os.path.join(td, fname)), dpi=600, bbox_inches="tight")
    except Exception: pass


class MockSMU2450:
    def __init__(self, addr):
        self.addr = addr
        self._v = 0.0
        self._G = 5e-6
        self._G_lrs = 5e-5
        self._G_hrs = 5e-7
        self.timeout = 60000

    def write(self, cmd: str):
        c = cmd.upper()
        if "VOLT:LEV" in c:
            try:
                self._v = float(c.split()[-1])
                if self._v > 1.5: self._G = self._G_lrs * (1 + 0.05 * (hash(time.time()) % 10 - 5) / 100)
                elif self._v < -1.2: self._G = self._G_hrs * (1 + 0.05 * (hash(time.time()) % 10 - 5) / 100)
            except Exception: pass

    def query(self, cmd: str) -> str:
        c = cmd.upper()
        if "*IDN?" in c: return "KEITHLEY INSTRUMENTS,MODEL 2450,DEMO,1.7.7b"
        if "CURR?" in c or "READ?" in c or "MEAS:CURR?" in c:
            noise = (hash(f"{time.time():.3f}") % 100 - 50) / 100.0
            I = self._v * self._G * (1 + noise * 0.02)
            return f"{I:.8E}"
        if "SYST:ERR?" in c: return "0,No Error"
        return "0"

    def close(self): pass

class VisaManager:
    def __init__(self):
        self.rm = None; self._inst = None
        if PYVISA_OK:
            try: self.rm = pyvisa.ResourceManager()
            except Exception:
                try: self.rm = pyvisa.ResourceManager("@py")
                except Exception: self.rm = None

    def list_resources(self):
        if self.rm is None: return ["DEMO::SMU2450"]
        try:
            res = list(self.rm.list_resources())
            return res if res else ["DEMO::SMU2450"]
        except Exception: return ["DEMO::SMU2450"]

    def open(self, addr: str, timeout_ms: int = 60000):
        if addr.startswith("DEMO::"):
            self._inst = MockSMU2450(addr); return self._inst
        if self.rm is None: raise RuntimeError("No VISA resource manager.")
        inst = self.rm.open_resource(addr)
        inst.timeout = timeout_ms
        inst.read_termination = "\n"; inst.write_termination = "\n"
        self._inst = inst; return inst

    def close(self):
        if self._inst:
            try: self._inst.close()
            except Exception: pass
            self._inst = None


def gen_sweep_points(v_start, v_stop, v_step):
    step = abs(v_step)
    if step == 0: return [v_start]
    pts = []
    v = v_start
    if v_start <= v_stop:
        while v <= v_stop + 1e-9:
            pts.append(v); v += step
    else:
        while v >= v_stop - 1e-9:
            pts.append(v); v -= step
    return pts

class SMU2450Driver:
    def __init__(self, inst, log=None):
        self.inst = inst
        self.log = log
        self._clamp_warned = set()

    def _cp(self, t):
        out = clamp_pulse(t)
        try:
            raw = float(t)
        except (TypeError, ValueError):
            raw = None
        if raw is not None and raw < MIN_PULSE_S and raw not in self._clamp_warned:
            self._clamp_warned.add(raw)
            if self.log:
                self.log.log("Pulse width %.4g s is below the 2450 floor; using %.0f ms."
                             % (raw, MIN_PULSE_S * 1e3), "warn")
        return out

    def idn(self) -> str: return self.inst.query("*IDN?").strip()

    def reset(self):
        self.inst.write("*RST"); self.inst.write(":SYST:BEEP:STAT OFF"); time.sleep(0.5)

    def configure_voltage_source(self, compliance_A=0.1, nplc=1.0, source_delay=0.0,
                                  comp_neg=None):
        self.inst.write(":SOUR:FUNC VOLT")
        self.inst.write(":SENS:FUNC 'CURR'")
        self.inst.write(f":SENS:CURR:NPLC {nplc:.4f}")
        self._comp_pos = abs(compliance_A)
        self._comp_neg = abs(comp_neg) if comp_neg is not None else abs(compliance_A)
        self.inst.write(f":SOUR:VOLT:ILIM {self._comp_pos:.6E}")
        self.inst.write(f":SOUR:DEL {source_delay:.6E}")
        self.inst.write(":SENS:CURR:RANG:AUTO ON")
        self.inst.write(":OUTP:SMODE HIMP")
        self._asym_comp = (comp_neg is not None)

    def set_compliance(self, v_applied: float):
        if not getattr(self, "_asym_comp", False):
            return
        if v_applied >= 0:
            self.inst.write(f":SOUR:VOLT:ILIM {self._comp_pos:.6E}")
        else:
            self.inst.write(f":SOUR:VOLT:ILIM {self._comp_neg:.6E}")

    def set_voltage(self, v: float): self.inst.write(f":SOUR:VOLT:LEV {v:.8E}")
    def output_on(self): self.inst.write(":OUTP ON")
    def output_off(self): self.inst.write(":OUTP OFF"); self.inst.write(":SOUR:VOLT:LEV 0.0")
    def measure_current(self) -> float: return float(self.inst.query(":MEAS:CURR?").split(",")[0].strip())

    def read_pulse(self, v_read: float, t_read: float) -> float:
        self.set_voltage(v_read); self.output_on(); time.sleep(self._cp(t_read))
        I = self.measure_current()
        self.set_voltage(0.0); self.output_off()
        return I

    def sweep_true_zero(self, v_pos, v_neg, v_step, compliance_A, nplc,
                        delay, progress_cb, stop_event,
                        sweep_mode="pos_first", comp_neg=None):
        self.configure_voltage_source(compliance_A, nplc, delay, comp_neg=comp_neg)
        self.output_on()

        abs_step = abs(v_step) if v_step != 0 else 0.01
        if sweep_mode == "pos_first":
            s1 = gen_sweep_points(0.0, v_pos, abs_step)
            s2 = gen_sweep_points(v_pos, 0.0, abs_step)[1:]
            s3 = gen_sweep_points(0.0, v_neg, abs_step)[1:]
            s4 = gen_sweep_points(v_neg, 0.0, abs_step)[1:]
            v_seq = s1 + s2 + s3 + s4
        elif sweep_mode == "neg_first":
            s1 = gen_sweep_points(0.0, v_neg, abs_step)
            s2 = gen_sweep_points(v_neg, 0.0, abs_step)[1:]
            s3 = gen_sweep_points(0.0, v_pos, abs_step)[1:]
            s4 = gen_sweep_points(v_pos, 0.0, abs_step)[1:]
            v_seq = s1 + s2 + s3 + s4
        elif sweep_mode == "pos_only":
            s1 = gen_sweep_points(0.0, v_pos, abs_step)
            s2 = gen_sweep_points(v_pos, 0.0, abs_step)[1:]
            v_seq = s1 + s2
        elif sweep_mode == "neg_only":
            s1 = gen_sweep_points(0.0, v_neg, abs_step)
            s2 = gen_sweep_points(v_neg, 0.0, abs_step)[1:]
            v_seq = s1 + s2
        elif sweep_mode == "neg_to_pos":
            v_seq = gen_sweep_points(v_neg, v_pos, abs_step)
        elif sweep_mode == "pos_to_neg":
            v_seq = gen_sweep_points(v_pos, v_neg, abs_step)
        else:
            s1 = gen_sweep_points(0.0, v_pos, abs_step)
            s2 = gen_sweep_points(v_pos, 0.0, abs_step)[1:]
            s3 = gen_sweep_points(0.0, v_neg, abs_step)[1:]
            s4 = gen_sweep_points(v_neg, 0.0, abs_step)[1:]
            v_seq = s1 + s2 + s3 + s4

        if not v_seq:
            self.output_off()
            return {"voltages": [], "currents": [], "conductances": []}

        all_v, all_i, all_g = [], [], []
        for done, v in enumerate(v_seq):
            if stop_event and stop_event.is_set(): break
            self.set_compliance(v)
            self.set_voltage(v)
            time.sleep(max(delay, MIN_DELAY_S))
            I = self.measure_current()
            G = abs(I / v) if abs(v) > 1e-9 else 0.0
            all_v.append(v); all_i.append(I); all_g.append(G)
            if progress_cb:
                progress_cb((done + 1) / len(v_seq) * 100, (v, I, G), (all_v, all_i))
        self.output_off()
        return {"voltages": all_v, "currents": all_i, "conductances": all_g}

    def sweep_triangular(self, v_peak_pos, v_peak_neg, v_step, compliance_A, nplc,
                         delay, progress_cb, stop_event, comp_neg=None):
        self.configure_voltage_source(compliance_A, nplc, delay, comp_neg=comp_neg)
        self.output_on()
        abs_step = abs(v_step) if v_step != 0 else 0.01
        leg1 = gen_sweep_points(v_peak_neg, v_peak_pos, abs_step)
        leg2 = gen_sweep_points(v_peak_pos, v_peak_neg, abs_step)[1:]
        v_seq = leg1 + leg2
        all_v, all_i, all_g = [], [], []
        for done, v in enumerate(v_seq):
            if stop_event and stop_event.is_set(): break
            self.set_compliance(v)
            self.set_voltage(v)
            time.sleep(max(delay, MIN_DELAY_S))
            I = self.measure_current()
            G = abs(I / v) if abs(v) > 1e-9 else 0.0
            all_v.append(v); all_i.append(I); all_g.append(G)
            if progress_cb:
                progress_cb((done + 1) / len(v_seq) * 100, (v, I, G), (all_v, all_i))
        self.output_off()
        return {"voltages": all_v, "currents": all_i, "conductances": all_g,
                "g_final": all_g[-1] if all_g else 0.0}

    def run_pulse_iv(self, set_p, rst_p, t_write, t_read, v_read, delay, mode,
                     comp, nplc, progress_cb, stop_event):
        self.configure_voltage_source(comp, nplc, 0.0)

        v_set_seq = gen_sweep_points(set_p["start"], set_p["stop"], set_p["step"])
        v_rst_seq = gen_sweep_points(rst_p["start"], rst_p["stop"], rst_p["step"])

        set_dn = gen_sweep_points(v_set_seq[-1], 0.0, set_p["step"])[1:] if v_set_seq else []
        rst_dn = gen_sweep_points(v_rst_seq[-1], 0.0, rst_p["step"])[1:] if v_rst_seq else []

        v_seq = v_set_seq + set_dn + v_rst_seq + rst_dn

        all_v, all_i, all_g = [], [], []
        total = len(v_seq)

        for done, vw in enumerate(v_seq):
            if stop_event and stop_event.is_set(): break

            if mode == "Measure During Write":
                self.set_voltage(vw); self.output_on()
                time.sleep(self._cp(t_write))
                I = self.measure_current()
                self.set_voltage(0.0); self.output_off()
                time.sleep(max(delay, MIN_DELAY_S))
            else:
                self.set_voltage(vw); self.output_on()
                time.sleep(self._cp(t_write))
                self.set_voltage(0.0); self.output_off()
                time.sleep(max(delay, MIN_DELAY_S))

                I = self.read_pulse(v_read, t_read)
                time.sleep(max(delay, MIN_DELAY_S))

            G = abs(I / v_read) if (mode != "Measure During Write" and abs(v_read) > 1e-9) else (abs(I/vw) if abs(vw) > 1e-9 else 0)

            all_v.append(vw); all_i.append(I); all_g.append(G)
            if progress_cb:
                progress_cb((done + 1) / total * 100, (vw, I, G), (all_v, all_i, all_g))

        return {"voltages": all_v, "currents": all_i, "conductances": all_g}

    def run_ltp_ltd(self, pot_pulses, dep_pulses, v_read, t_read, delay,
                    comp, nplc, progress_cb, stop_event):
        self.configure_voltage_source(comp, nplc, 0.0)
        ps, gs, rs, vs, phs = [], [], [], [], []
        total = len(pot_pulses) + len(dep_pulses)
        pulse_count = 0

        def _do_pulse(v_amp, t_w, phase):
            nonlocal pulse_count
            if stop_event and stop_event.is_set(): return

            self.set_voltage(v_amp); self.output_on()
            time.sleep(self._cp(t_w))
            self.set_voltage(0.0); self.output_off()

            time.sleep(max(delay, MIN_DELAY_S))
            I = self.read_pulse(v_read, t_read)
            time.sleep(max(delay, MIN_DELAY_S))

            G = abs(I / v_read) if abs(v_read) > 1e-9 else 0.0
            R = 1.0 / G if G > 1e-15 else 1e15
            pulse_count += 1
            ps.append(pulse_count); gs.append(G); rs.append(R)
            vs.append(v_amp); phs.append(phase)
            if progress_cb:
                progress_cb(pulse_count / total * 100, (pulse_count, G, R, v_amp, phase), (ps, gs, phs))

        for (v_amp, t_w) in pot_pulses: _do_pulse(v_amp, t_w, "POT")
        for (v_amp, t_w) in dep_pulses: _do_pulse(v_amp, t_w, "DEP")
        return {"pulse_num": ps, "conductances": gs, "resistances": rs, "voltages": vs, "phases": phs}

    def run_mlc_states(self, v_rst, t_rst, v_start, v_stop, n_states, t_write,
                       v_read, t_read, ret_time, ret_interval, delay,
                       comp, nplc, progress_cb, stop_event):
        self.configure_voltage_source(comp, nplc, 0.0)
        v_prog_seq = np.linspace(v_start, v_stop, n_states)

        all_results = []
        for state_idx, v_prog in enumerate(v_prog_seq):
            if stop_event and stop_event.is_set(): break

            if abs(v_rst) > 1e-5:
                self.set_voltage(v_rst); self.output_on()
                time.sleep(self._cp(t_rst))
                self.set_voltage(0.0); self.output_off()
                time.sleep(max(delay, MIN_DELAY_S))

            self.set_voltage(v_prog); self.output_on()
            time.sleep(self._cp(t_write))
            self.set_voltage(0.0); self.output_off()
            time.sleep(max(delay, MIN_DELAY_S))

            n_reads = max(int(ret_time / ret_interval), 1)
            ts, gs = [], []
            t0 = time.time()
            for k in range(n_reads):
                if stop_event and stop_event.is_set(): break
                target = t0 + k * ret_interval
                wait = target - time.time()
                if wait > 0.005: time.sleep(wait - 0.004)

                I = self.read_pulse(v_read, t_read)
                t_now = time.time() - t0
                G = abs(I / v_read) if abs(v_read) > 1e-9 else 0.0
                ts.append(t_now); gs.append(G)

            state_data = {"state_idx": state_idx, "v_prog": v_prog, "time": ts, "conductances": gs}
            all_results.append(state_data)

            if progress_cb:
                progress_cb((state_idx + 1) / n_states * 100, state_data)

        return all_results

    def run_retention(self, v_read, t_read, total_time, interval, comp, nplc, progress_cb, stop_event):
        self.configure_voltage_source(comp, nplc, 0.0)
        n_reads = max(int(total_time / interval), 1)
        ts, gs, rs = [], [], []
        t0 = time.time()
        for k in range(n_reads):
            if stop_event and stop_event.is_set(): break
            wait = t0 + k * interval - time.time()
            if wait > 0.005: time.sleep(wait - 0.004)
            I = self.read_pulse(v_read, t_read)
            t_now = time.time() - t0
            G = abs(I / v_read) if abs(v_read) > 1e-9 else 0.0
            ts.append(t_now); gs.append(G); rs.append(1.0/G if G>1e-15 else 1e15)
            if progress_cb: progress_cb((k + 1)/n_reads * 100, (t_now, G, rs[-1]), (ts, gs))
        return {"time": ts, "conductances": gs, "resistances": rs}

    def run_forming(self, v_start, v_stop, v_step, t_step, compliance_A, nplc,
                    stop_on_form, v_form_thresh, v_read, t_read, delay, progress_cb, stop_event):
        self.configure_voltage_source(compliance_A, nplc, 0.0)
        self.output_on()
        v_steps = gen_sweep_points(v_start, v_stop, v_step)

        vs, is_, gs, formed_v = [], [], [], None
        formed = False
        n_tot = len(v_steps)

        for k, vv in enumerate(v_steps):
            if stop_event and stop_event.is_set(): break
            self.set_voltage(vv)
            time.sleep(max(t_step, MIN_PULSE_S))
            I = self.measure_current()
            G = abs(I / vv) if abs(vv) > 1e-9 else 0.0
            vs.append(vv); is_.append(I); gs.append(G)
            if progress_cb:
                progress_cb((k + 1) / n_tot * 100, (vv, I, G), (vs, is_))
            if stop_on_form and abs(I) >= v_form_thresh * compliance_A and not formed:
                formed = True
                formed_v = vv
                self.set_voltage(0.0); self.output_off()
                break

        self.set_voltage(0.0); self.output_off()
        time.sleep(max(delay, MIN_DELAY_S))

        g_post = None
        if v_read != 0.0:
            I_post = self.read_pulse(v_read, t_read)
            g_post = abs(I_post / v_read) if abs(v_read) > 1e-9 else 0.0

        return {"voltages": vs, "currents": is_, "conductances": gs,
                "formed": formed, "formed_v": formed_v, "g_post": g_post}

    def run_paired_pulse(self, v_pulse, t_pulse, isi_values, v_read, t_read, delay,
                         comp, nplc, progress_cb, stop_event):
        self.configure_voltage_source(comp, nplc, 0.0)
        isis_out, g1_out, g2_out, ratio_out = [], [], [], []
        for k, isi in enumerate(isi_values):
            if stop_event and stop_event.is_set(): break
            self.set_voltage(v_pulse); self.output_on(); time.sleep(self._cp(t_pulse))
            self.set_voltage(0.0); self.output_off(); time.sleep(max(delay, MIN_DELAY_S))
            I1 = self.read_pulse(v_read, t_read)
            time.sleep(max(delay, MIN_DELAY_S))

            G1 = abs(I1 / v_read) if abs(v_read) > 1e-9 else 1e-15

            time.sleep(max(isi, 0.001))

            self.set_voltage(v_pulse); self.output_on(); time.sleep(self._cp(t_pulse))
            self.set_voltage(0.0); self.output_off(); time.sleep(max(delay, MIN_DELAY_S))
            I2 = self.read_pulse(v_read, t_read)
            time.sleep(max(delay, MIN_DELAY_S))

            G2 = abs(I2 / v_read) if abs(v_read) > 1e-9 else 1e-15
            ratio = G2 / G1 if G1 > 1e-15 else 1.0

            isis_out.append(isi * 1000); g1_out.append(G1); g2_out.append(G2); ratio_out.append(ratio)
            if progress_cb: progress_cb((k + 1) / len(isi_values) * 100, (isi * 1000, G1, G2, ratio), (isis_out, ratio_out))
        return {"isi_ms": isis_out, "G_P1": g1_out, "G_P2": g2_out, "ratio": ratio_out}

    def run_stdp(self, v_pre, v_post, t_pulse, dt_values, v_read, t_read, delay,
                 comp, nplc, progress_cb, stop_event):
        self.configure_voltage_source(comp, nplc, 0.0)
        dts, dgs, gb, ga = [], [], [], []
        for k, dt_s in enumerate(dt_values):
            if stop_event and stop_event.is_set(): break

            I1 = self.read_pulse(v_read, t_read)
            time.sleep(max(delay, MIN_DELAY_S))
            G1 = abs(I1 / v_read) if abs(v_read) > 1e-9 else 1e-15

            if dt_s >= 0:
                self.set_voltage(v_pre); self.output_on(); time.sleep(self._cp(t_pulse))
                self.set_voltage(0.0); self.output_off()
                time.sleep(max(abs(dt_s), 0.001))
                self.set_voltage(v_post); self.output_on(); time.sleep(self._cp(t_pulse))
                self.set_voltage(0.0); self.output_off()
            else:
                self.set_voltage(v_post); self.output_on(); time.sleep(self._cp(t_pulse))
                self.set_voltage(0.0); self.output_off()
                time.sleep(max(abs(dt_s), 0.001))
                self.set_voltage(v_pre); self.output_on(); time.sleep(self._cp(t_pulse))
                self.set_voltage(0.0); self.output_off()

            time.sleep(max(delay, MIN_DELAY_S))
            I2 = self.read_pulse(v_read, t_read)
            time.sleep(max(delay, MIN_DELAY_S))
            G2 = abs(I2 / v_read) if abs(v_read) > 1e-9 else 1e-15
            dG = (G2 - G1) / G1 if G1 > 1e-15 else 0.0

            dts.append(dt_s * 1000); dgs.append(dG); gb.append(G1); ga.append(G2)
            if progress_cb: progress_cb((k + 1) / len(dt_values) * 100, (dt_s * 1000, dG, G1, G2), (dts, dgs))
        return {"dt_ms": dts, "dG_norm": dgs, "G_before": gb, "G_after": ga}

    def run_endurance_pulse(self, v_set, v_rst, t_set, t_rst, v_read, t_read, delay,
                             n_cyc, comp, nplc, progress_cb, stop_event,
                             comp_neg=None):
        self.configure_voltage_source(comp, nplc, 0.0, comp_neg=comp_neg)
        cycs, g_lrs, g_hrs, r_lrs, r_hrs = [], [], [], [], []
        for cyc in range(1, n_cyc + 1):
            if stop_event and stop_event.is_set(): break
            self.set_compliance(v_set); self.set_voltage(v_set); self.output_on()
            time.sleep(self._cp(t_set)); self.set_voltage(0.0); self.output_off()
            time.sleep(max(delay, MIN_DELAY_S))
            I_lrs = self.read_pulse(v_read, t_read); time.sleep(max(delay, MIN_DELAY_S))
            G_lrs = abs(I_lrs / v_read) if abs(v_read) > 1e-9 else 0.0
            self.set_compliance(v_rst); self.set_voltage(v_rst); self.output_on()
            time.sleep(self._cp(t_rst)); self.set_voltage(0.0); self.output_off()
            time.sleep(max(delay, MIN_DELAY_S))
            I_hrs = self.read_pulse(v_read, t_read); time.sleep(max(delay, MIN_DELAY_S))
            G_hrs = abs(I_hrs / v_read) if abs(v_read) > 1e-9 else 0.0
            R_lrs = 1.0 / G_lrs if G_lrs > 1e-15 else 1e15
            R_hrs = 1.0 / G_hrs if G_hrs > 1e-15 else 1e15
            cycs.append(cyc); g_lrs.append(G_lrs); g_hrs.append(G_hrs)
            r_lrs.append(R_lrs); r_hrs.append(R_hrs)
            if progress_cb:
                progress_cb(cyc / n_cyc * 100, (cyc, G_lrs, G_hrs, R_lrs, R_hrs), (cycs, g_lrs, g_hrs))
        return {"cycle": cycs, "G_LRS": g_lrs, "G_HRS": g_hrs, "R_LRS": r_lrs, "R_HRS": r_hrs}

    def run_endurance_from_pulse_iv(self, set_p, rst_p, t_write, t_read, v_read, delay,
                                     n_cycles, comp, nplc, progress_cb, stop_event,
                                     comp_neg=None):
        self.configure_voltage_source(comp, nplc, 0.0, comp_neg=comp_neg)
        cycs, g_lrs, g_hrs, r_lrs, r_hrs = [], [], [], [], []
        v_set_seq = gen_sweep_points(set_p["start"], set_p["stop"], abs(set_p["step"]))
        v_set_dn  = gen_sweep_points(set_p["stop"],  set_p["start"], abs(set_p["step"]))[1:]
        v_rst_seq = gen_sweep_points(rst_p["start"], rst_p["stop"], abs(rst_p["step"]))
        v_rst_dn  = gen_sweep_points(rst_p["stop"],  rst_p["start"], abs(rst_p["step"]))[1:]
        full_seq  = v_set_seq + v_set_dn + v_rst_seq + v_rst_dn

        for cyc in range(1, n_cycles + 1):
            if stop_event and stop_event.is_set(): break
            g_at_peak_set = 0.0; g_at_peak_rst = 0.0
            for vw in full_seq:
                if stop_event and stop_event.is_set(): break
                self.set_compliance(vw); self.set_voltage(vw); self.output_on()
                time.sleep(self._cp(t_write)); self.set_voltage(0.0); self.output_off()
                time.sleep(max(delay, MIN_DELAY_S))
                I = self.read_pulse(v_read, t_read); time.sleep(max(delay, MIN_DELAY_S))
                G = abs(I / v_read) if abs(v_read) > 1e-9 else 0.0
                if abs(vw - set_p["stop"]) < abs(set_p["step"]) * 0.6:
                    g_at_peak_set = G
                if abs(vw - rst_p["stop"]) < abs(rst_p["step"]) * 0.6:
                    g_at_peak_rst = G
            G_lrs = g_at_peak_set; G_hrs = g_at_peak_rst
            R_lrs = 1.0 / G_lrs if G_lrs > 1e-15 else 1e15
            R_hrs = 1.0 / G_hrs if G_hrs > 1e-15 else 1e15
            cycs.append(cyc); g_lrs.append(G_lrs); g_hrs.append(G_hrs)
            r_lrs.append(R_lrs); r_hrs.append(R_hrs)
            if progress_cb:
                progress_cb(cyc / n_cycles * 100, (cyc, G_lrs, G_hrs, R_lrs, R_hrs), (cycs, g_lrs, g_hrs))
        return {"cycle": cycs, "G_LRS": g_lrs, "G_HRS": g_hrs, "R_LRS": r_lrs, "R_HRS": r_hrs}

    def set_state_by_pulse(self, v_prog, t_prog, v_read, t_read, delay, comp, nplc,
                           verify_g_min=None, verify_g_max=None, max_tries=5):
        self.configure_voltage_source(comp, nplc, 0.0)
        for attempt in range(max_tries):
            self.set_voltage(v_prog); self.output_on()
            time.sleep(self._cp(t_prog)); self.set_voltage(0.0); self.output_off()
            time.sleep(max(delay, MIN_DELAY_S))
            I = self.read_pulse(v_read, t_read)
            G = abs(I / v_read) if abs(v_read) > 1e-9 else 0.0
            if verify_g_min is None and verify_g_max is None:
                return G, True
            ok = ((verify_g_min is None or G >= verify_g_min) and
                  (verify_g_max is None or G <= verify_g_max))
            if ok:
                return G, True
        return G, False

    def set_state_by_triangular(self, v_pos, v_neg, v_step, v_read, t_read,
                                 delay, comp, nplc, progress_cb, stop_event, comp_neg=None):
        res = self.sweep_triangular(v_pos, v_neg, v_step, comp, nplc,
                                    delay, progress_cb, stop_event, comp_neg=comp_neg)
        I_final = self.read_pulse(v_read, t_read)
        G_final = abs(I_final / v_read) if abs(v_read) > 1e-9 else 0.0
        res["g_final"] = G_final
        return G_final, res


def mk_entry(parent, width=12, textvariable=None, **kw):
    e = tk.Entry(parent, width=width, bg=C["entry"], fg=C["text"],
                 insertbackground=C["text"], relief="flat", bd=0,
                 font=FONT_MONO, textvariable=textvariable, **kw)
    e.config(highlightthickness=1, highlightbackground=C["border"],
             highlightcolor=C["text2"])
    return e


def mk_button(parent, text, command=None, color=None, fg=None, font=None, **kw):
    kw.setdefault("padx", S(9))
    kw.setdefault("pady", S(3))
    return tk.Button(parent, text=text, command=command,
                     bg=color or C["btn"], fg=fg or C["text"],
                     activebackground=C["btn_h"], activeforeground=C["text"],
                     relief="solid", bd=1, font=font or FONT_LABEL, cursor="hand2",
                     highlightthickness=0, **kw)


def mk_combobox(parent, values=None, width=16, **kw):
    cb = ttk.Combobox(parent, values=values or [], width=width, font=FONT_MONO, **kw)
    cb.configure(state="readonly")
    return cb


def mk_lf(parent, title, color=None, **kw):
    return tk.LabelFrame(parent, text=f"  {title}  ", bg=C["panel"],
                         fg=C["text"], font=FONT_HEADER, relief="flat",
                         highlightbackground=C["border"], highlightthickness=1,
                         padx=S(2), pady=S(2), **kw)


def param_row(parent, row, label, var, pady=3, label_width=16, row_offset=0):
    r = row + row_offset
    tk.Label(parent, text=label, bg=C["panel"], fg=C["text2"], font=FONT_LABEL,
             width=label_width, anchor="e"
             ).grid(row=r, column=0, sticky="e", padx=(S(8), S(4)), pady=S(pady))
    if isinstance(var, (tk.StringVar, tk.BooleanVar, tk.IntVar, tk.DoubleVar)):
        mk_entry(parent, width=12, textvariable=var
                 ).grid(row=r, column=1, sticky="ew", padx=(0, S(8)), pady=S(pady))
    else:
        var.grid(row=r, column=1, sticky="ew", padx=(0, S(8)), pady=S(pady))


def hrule(parent, pady=0):
    f = tk.Frame(parent, bg=C["border"], height=1)
    f.pack(fill="x", pady=pady)
    return f


def mk_strip(parent, title, subtitle=""):
    strip = tk.Frame(parent, bg=C["strip"])
    strip.pack(fill="x")
    tk.Label(strip, text=title, bg=C["strip"], fg=C["text"], font=FONT_HEADER
             ).pack(side="left", padx=(S(12), S(6)), pady=S(7))
    if subtitle:
        tk.Label(strip, text=subtitle, bg=C["strip"], fg=C["muted"], font=FONT_SMALL
                 ).pack(side="left", pady=S(7))
    return strip


def scrolled_frame(parent, width=290):
    outer = tk.Frame(parent, bg=C["panel"])
    scale_metric(outer, width=width)
    outer.pack(side="left", fill="y")
    outer.pack_propagate(False)
    canvas = tk.Canvas(outer, bg=C["panel"], bd=0, highlightthickness=0)
    scale_metric(canvas, width=width - 16)
    vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    inner = tk.Frame(canvas, bg=C["panel"])
    win = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))
    def _scroll(e): canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
    canvas.bind("<MouseWheel>", _scroll)
    inner.bind("<MouseWheel>", _scroll)
    return outer, inner


class ProgressBar(tk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=C["panel"], **kw)
        self._var = tk.DoubleVar(value=0.0)
        self._bar = ttk.Progressbar(self, variable=self._var, maximum=100,
                                    style="N.Horizontal.TProgressbar")
        self._bar.pack(fill="x", expand=True, padx=S(2), pady=S(2))
        self._lbl = tk.Label(self, text="0%", bg=C["panel"], fg=C["muted"], font=FONT_SMALL)
        self._lbl.pack(side="right", padx=S(6))

    def set(self, pct: float, text: str = ""):
        self._var.set(min(max(pct, 0), 100)); self._lbl.config(text=text or f"{pct:.0f}%")
    def reset(self): self._var.set(0); self._lbl.config(text="0%")

class StatusBar(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=C["panel2"])
        scale_metric(self, height=26)
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", side="top")
        self._lbl = tk.Label(self, text="Ready.", bg=C["panel2"], fg=C["text2"],
                             font=FONT_SMALL, anchor="w")
        self._lbl.pack(side="left", fill="x", expand=True, padx=S(10), pady=S(3))
    def ok(self, msg="Ready."):  self._lbl.config(text=msg, fg=C["text2"])
    def error(self, msg):        self._lbl.config(text=f"⚠  {msg}", fg=C["red"])
    def busy(self, msg):         self._lbl.config(text=f"⟳  {msg}", fg=C["text"])

class LogPane(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=C["panel"])
        scale_metric(self, height=130)
        self.pack_propagate(False)
        tk.Label(self, text="LOG", bg=C["panel"], fg=C["muted"], font=FONT_SMALL,
                 anchor="w").pack(fill="x", padx=S(2))
        self.txt = scrolledtext.ScrolledText(self, height=5, bg=C["entry2"], fg=C["text2"],
                                             font=FONT_MONO, bd=0, state="disabled",
                                             relief="flat", highlightthickness=1,
                                             highlightbackground=C["border"])
        self.txt.pack(fill="both", expand=True, padx=S(2), pady=(0, S(2)))
        for t, col in [("ok", C["text"]), ("err", C["red"]), ("info", C["text2"]), ("warn", C["warn"])]:
            self.txt.tag_config(t, foreground=col)
    def log(self, msg: str, tag: str = "info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.txt.config(state="normal"); self.txt.insert("end", f"[{ts}] {msg}\n", tag)
        self.txt.see("end"); self.txt.config(state="disabled")

class LivePlot(tk.Frame):
    MAX_PER_PAGE = 20
    def __init__(self, parent, title, xlabel, ylabel, log_y=False, figsize=(5.5, 3.6), dpi=None, **kw):
        super().__init__(parent, bg=C["panel"], **kw)
        self._title = title; self._xlabel = xlabel; self._ylabel = ylabel; self._log_y = log_y
        self._traces = []; self._page = 0
        if not MATPLOTLIB_OK: return
        nav = tk.Frame(self, bg=C["panel"]); nav.pack(fill="x", pady=(S(2), 0))
        mk_button(nav, "◀", self._prev, font=FONT_SMALL, padx=S(8), pady=S(2)).pack(side="left", padx=S(2))
        self._page_var = tk.StringVar(value="Pg 1 / 1")
        tk.Label(nav, textvariable=self._page_var, bg=C["panel"], fg=C["muted"], font=FONT_SMALL, width=10).pack(side="left")
        mk_button(nav, "▶", self._next, font=FONT_SMALL, padx=S(8), pady=S(2)).pack(side="left", padx=S(2))
        tk.Label(nav, text=title, bg=C["panel"], fg=C["text"], font=FONT_LABEL).pack(side="left", padx=S(12))
        self.fig = mk_figure(figsize, dpi=dpi, facecolor="white")
        self.ax = self.fig.add_subplot(111); self._setup_ax(self.ax)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        try: NavigationToolbar2Tk(self.canvas, self).pack(side="bottom", fill="x")
        except: pass
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def _setup_ax(self, ax):
        ax.set_facecolor("white"); ax.set_xlabel(self._xlabel); ax.set_ylabel(self._ylabel); ax.set_title(self._title)
        ax.tick_params(which="both", direction="in", top=True, right=True)
        if self._log_y:
            try: ax.set_yscale("log")
            except: pass

    def _n_pages(self): return max(1, math.ceil(len(self._traces) / self.MAX_PER_PAGE))
    def _prev(self):
        if self._page > 0: self._page -= 1; self._redraw()
    def _next(self):
        if self._page < self._n_pages() - 1: self._page += 1; self._redraw()

    def add_trace(self, x, y, label="", color_idx=None, ls="-", marker=None, lw=1.2, alpha=1.0, scatter=False):
        if not x or not hasattr(self, 'ax'): return
        color = pub_color(color_idx if color_idx is not None else len(self._traces))
        self._traces.append(dict(x=list(x), y=list(y), label=label, color=color, ls=ls, marker=marker, lw=lw, alpha=alpha, scatter=scatter))
        self._page = self._n_pages() - 1; self._redraw()

    def clear(self):
        self._traces.clear(); self._page = 0
        if hasattr(self, 'ax'): self._redraw()

    def _redraw(self):
        if not hasattr(self, 'ax'): return
        n = self._n_pages(); self._page = min(self._page, n - 1)
        self._page_var.set(f"Pg {self._page + 1} / {n}")
        self.ax.cla(); self._setup_ax(self.ax)
        has_label = False
        for t in self._traces[self._page * self.MAX_PER_PAGE : (self._page + 1) * self.MAX_PER_PAGE]:
            y_plot = [abs(v) if abs(v) > 1e-17 else 1e-17 for v in t["y"]] if self._log_y else t["y"]
            kw = dict(color=t["color"], linewidth=t["lw"], alpha=t["alpha"], linestyle=t["ls"])
            if t.get("label"): kw["label"] = t["label"]; has_label = True
            if t.get("marker"): kw["marker"] = t["marker"]; kw["markersize"] = 3
            if t.get("scatter"): self.ax.scatter(t["x"], y_plot, color=t["color"], s=14, alpha=t["alpha"], zorder=3, label=t.get("label",""))
            else: self.ax.plot(t["x"], y_plot, **kw)
        if has_label: self.ax.legend(fontsize=7, framealpha=0.9, edgecolor="0.8", handlelength=1.5)
        self.canvas.draw_idle()

    def hline(self, y, color="#9A9A9A", ls=":"):
        if hasattr(self, 'ax'): self.ax.axhline(y, color=color, linestyle=ls, lw=0.8); self.canvas.draw_idle()
    def vline(self, x, color="#9A9A9A", ls=":"):
        if hasattr(self, 'ax'): self.ax.axvline(x, color=color, linestyle=ls, lw=0.8); self.canvas.draw_idle()

class DualLivePlot(tk.Frame):
    MAX_PER_PAGE = 20
    def __init__(self, parent, title, xlabel, ylabel, figsize=(10.5, 3.6), dpi=None, **kw):
        super().__init__(parent, bg=C["panel"], **kw)
        self._title = title; self._xlabel = xlabel; self._ylabel = ylabel
        self._traces = []; self._page = 0; self._extras = []
        if not MATPLOTLIB_OK: return
        nav = tk.Frame(self, bg=C["panel"]); nav.pack(fill="x", pady=(S(2), 0))
        mk_button(nav, "◀", self._prev, font=FONT_SMALL, padx=S(8), pady=S(2)).pack(side="left", padx=S(2))
        self._page_var = tk.StringVar(value="Pg 1 / 1")
        tk.Label(nav, textvariable=self._page_var, bg=C["panel"], fg=C["muted"], font=FONT_SMALL, width=10).pack(side="left")
        mk_button(nav, "▶", self._next, font=FONT_SMALL, padx=S(8), pady=S(2)).pack(side="left", padx=S(2))
        tk.Label(nav, text=title, bg=C["panel"], fg=C["text"], font=FONT_LABEL).pack(side="left", padx=S(12))
        self.fig = mk_figure(figsize, dpi=dpi, facecolor="white")
        self.ax_lin = self.fig.add_subplot(121); self.ax_log = self.fig.add_subplot(122); self._setup_axes()
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        try: NavigationToolbar2Tk(self.canvas, self).pack(side="bottom", fill="x")
        except: pass
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def _setup_axes(self):
        for ax in (self.ax_lin, self.ax_log):
            ax.set_facecolor("white"); ax.set_xlabel(self._xlabel)
            ax.tick_params(which="both", direction="in", top=True, right=True)
        self.ax_lin.set_ylabel(self._ylabel); self.ax_lin.set_title(f"{self._title} - Linear")
        self.ax_log.set_ylabel(f"|{self._ylabel}|"); self.ax_log.set_title(f"{self._title} - Semilog |I|")
        try: self.ax_log.set_yscale("log")
        except: pass
        self.fig.tight_layout(pad=1.8)

    def _n_pages(self): return max(1, math.ceil(len(self._traces) / self.MAX_PER_PAGE))
    def _prev(self):
        if self._page > 0: self._page -= 1; self._redraw()
    def _next(self):
        if self._page < self._n_pages() - 1: self._page += 1; self._redraw()

    def add_trace(self, x, y, label="", color_idx=None, ls="-", marker=None, lw=1.2, alpha=1.0):
        if not x or not hasattr(self, 'ax_lin'): return
        color = pub_color(color_idx if color_idx is not None else len(self._traces))
        self._traces.append(dict(x=list(x), y=list(y), label=label, color=color, ls=ls, marker=marker, lw=lw, alpha=alpha))
        self._page = self._n_pages() - 1; self._redraw()

    def clear(self):
        self._traces.clear(); self._extras.clear(); self._page = 0
        if hasattr(self, 'ax_lin'): self._redraw()

    def _redraw(self):
        if not hasattr(self, 'ax_lin'): return
        n = self._n_pages(); self._page = min(self._page, n - 1)
        self._page_var.set(f"Pg {self._page + 1} / {n}")
        self.ax_lin.cla(); self.ax_log.cla(); self._setup_axes()
        has_label = False
        for t in self._traces[self._page * self.MAX_PER_PAGE : (self._page + 1) * self.MAX_PER_PAGE]:
            kw = dict(color=t["color"], linewidth=t["lw"], alpha=t["alpha"], linestyle=t["ls"])
            if t.get("label"): kw["label"] = t["label"]; has_label = True
            if t.get("marker"): kw["marker"] = t["marker"]; kw["markersize"] = 3
            self.ax_lin.plot(t["x"], t["y"], **kw)
            y_abs = [abs(v) if abs(v) > 1e-17 else 1e-17 for v in t["y"]]
            self.ax_log.plot(t["x"], y_abs, **kw)
        for ex in self._extras:
            if ex["type"] == "hline": self.ax_lin.axhline(ex["v"], color=ex["c"], ls=":", lw=0.8)
            elif ex["type"] == "vline":
                for ax in (self.ax_lin, self.ax_log): ax.axvline(ex["v"], color=ex["c"], ls=":", lw=0.8)
        if has_label:
            for ax in (self.ax_lin, self.ax_log): ax.legend(fontsize=7, framealpha=0.9, edgecolor="0.8")
        self.canvas.draw_idle()

    def hline(self, y, color="#9A9A9A"):
        self._extras.append({"type": "hline", "v": y, "c": color})
        if hasattr(self, 'ax_lin'): self.ax_lin.axhline(y, color=color, ls=":", lw=0.8); self.canvas.draw_idle()
    def vline(self, x, color="#9A9A9A"):
        self._extras.append({"type": "vline", "v": x, "c": color})
        if hasattr(self, 'ax_lin'):
            for ax in (self.ax_lin, self.ax_log): ax.axvline(x, color=color, ls=":", lw=0.8)
            self.canvas.draw_idle()


class ConnectionPanel(tk.Frame):
    def __init__(self, parent, visa_mgr, log, status):
        super().__init__(parent, bg=C["panel"])
        self.visa, self.log, self.status = visa_mgr, log, status
        self.driver = None; self._build()
    def get_driver(self): return self.driver
    def _build(self):
        tk.Label(self, text="SMU 2450 CONNECTION", bg=C["panel"], fg=C["text"],
                 font=FONT_HEADER).pack(anchor="w", padx=S(4), pady=(S(8), S(4)))
        af = mk_lf(self, "VISA Address"); af.pack(fill="x", padx=8, pady=4)
        self._addr_var = tk.StringVar(value="DEMO::SMU2450")
        r1 = tk.Frame(af, bg=C["panel"]); r1.pack(fill="x", padx=6, pady=4)
        self._addr_cb = mk_combobox(r1, values=["DEMO::SMU2450"], width=26, textvariable=self._addr_var)
        self._addr_cb.pack(side="left", padx=(0, 4))
        mk_button(r1, "Scan", self._scan).pack(side="left", padx=S(2))
        r2 = tk.Frame(af, bg=C["panel"]); r2.pack(fill="x", padx=6, pady=4)
        mk_button(r2, "Connect",    self._connect).pack(side="left", padx=S(2))
        mk_button(r2, "Disconnect", self._disconnect).pack(side="left", padx=S(2))
        self._st = tk.Label(self, text="●  DISCONNECTED", bg=C["panel"], fg=C["red"],
                            font=FONT_LABEL, anchor="w")
        self._st.pack(fill="x", padx=S(6), pady=S(5))

    def _scan(self):
        res = self.visa.list_resources(); self._addr_cb.config(values=res)
        if res: self._addr_var.set(res[0])
        self.log.log(f"Found {len(res)} VISA resources.", "info")

    def _connect(self):
        addr = self._addr_var.get().strip()
        try:
            inst = self.visa.open(addr, timeout_ms=60000); self.driver = SMU2450Driver(inst, self.log)
            idn = self.driver.idn(); self._st.config(text="●  CONNECTED", fg=C["good"])
            self.log.log(f"Connected: {idn[:40]}", "ok"); self.status.ok("Connected.")
        except Exception as e:
            self.log.log(f"Connection error: {e}", "err"); self.status.error(str(e))

    def _disconnect(self):
        self.visa.close(); self.driver = None
        self._st.config(text="●  DISCONNECTED", fg=C["red"]); self.status.ok("Disconnected.")

class AcqThread(threading.Thread):
    def __init__(self, task_fn, result_queue: queue.Queue):
        super().__init__(daemon=True); self.task_fn = task_fn; self.result_queue = result_queue
        self._stop_event = threading.Event()
    def stop(self): self._stop_event.set()
    def run(self):
        try:
            result = self.task_fn(self._stop_event)
            self.result_queue.put({"type": "done", "data": result})
        except Exception as e: self.result_queue.put({"type": "error", "message": str(e)})

class BaseMeasurementTab(tk.Frame):
    def __init__(self, parent, get_driver, log, status, app_vars, app_state):
        super().__init__(parent, bg=C["panel"])
        self.get_drv = get_driver; self.log = log; self.status = status
        self.app_vars = app_vars; self.app_state = app_state
        self._acq = None; self._q = queue.Queue(); self._last_plot = 0.0; self.on_complete = None
    def start_poll(self, poll_fn):
        if self._acq and self._acq.is_alive(): self.after(50, poll_fn)
    def safe_stop(self):
        if self._acq: self._acq.stop()
    def finish_run(self):
        if self.on_complete:
            cb = self.on_complete; self.on_complete = None; cb()
    def manual_export(self, test_name, headers, zip_data_fn):
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=f"{test_name}_Export.csv", filetypes=[("CSV", "*.csv")])
        if not path: return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f); w.writerow(["Run"] + headers); zip_data_fn(w)
            self.log.log(f"Exported: {path}", "ok")
        except Exception as e: self.log.log(f"Export failed: {e}", "err")


class FormingTab(BaseMeasurementTab):
    def __init__(self, parent, get_driver, log, status, app_vars, app_state):
        super().__init__(parent, get_driver, log, status, app_vars, app_state)
        self._reps = []; self._n_runs_var = tk.StringVar(value="1"); self._build()

    def _build(self):
        strip = tk.Frame(self, bg=C["strip"]); strip.pack(fill="x")
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x")
        tk.Label(strip, text="  FORMING STEP - Controlled Electroforming", bg=C["strip"], fg=C["text"], font=FONT_HEADER).pack(side="left", padx=10, pady=6)
        self._run_btn = mk_button(strip, "▶ FORM", self._run, color=C["btn"], fg=C["accent4"], font=FONT_HEADER); self._run_btn.pack(side="left", padx=4, pady=4)
        mk_button(strip, "■ STOP", self.safe_stop, color=C["btn"], fg=C["red"]).pack(side="left", padx=4)
        mk_button(strip, "CSV", self._export, color=C["btn"]).pack(side="left", padx=4)

        self._prog = ProgressBar(self); self._prog.pack(fill="x")

        live = tk.Frame(self, bg=C["panel2"]); live.pack(fill="x")
        self._lv_label = tk.Label(live, text="V: ---", bg=C["panel2"], fg=C["accent4"], font=FONT_BIG, width=14)
        self._lv_label.pack(side="left", padx=12, pady=3)
        self._li_label = tk.Label(live, text="I: ---", bg=C["panel2"], fg=C["text"], font=FONT_BIG, width=18)
        self._li_label.pack(side="left", padx=8, pady=3)
        self._status_label = tk.Label(live, text="Status: Idle", bg=C["panel2"], fg=C["text2"], font=FONT_LABEL, width=28)
        self._status_label.pack(side="left", padx=8, pady=3)

        body = tk.Frame(self, bg=C["panel"]); body.pack(fill="both", expand=True)
        _, L = scrolled_frame(body, width=305)
        R = tk.Frame(body, bg=C["panel"]); R.pack(side="left", fill="both", expand=True, padx=4, pady=4)

        ff = mk_lf(L, "Voltage Ramp", C["accent4"]); ff.pack(fill="x", padx=6, pady=4); ff.columnconfigure(1, weight=1)
        self._fp = {}
        for i, (lbl, key, dflt) in enumerate([
            ("V_start (V)",     "v_start",   "0.0"),
            ("V_stop (V)",      "v_stop",    "4.0"),
            ("V_step (V)",      "v_step",    "0.05"),
            ("t_step (s)",      "t_step",    "0.1"),
            ("Compliance (A)",  "comp",      "1e-3"),
            ("NPLC",            "nplc",      "1.0"),
        ]):
            self._fp[key] = tk.StringVar(value=dflt); param_row(ff, i, lbl, self._fp[key], label_width=17)

        det_f = mk_lf(L, "Forming Detection", C["accent3"]); det_f.pack(fill="x", padx=6, pady=4); det_f.columnconfigure(1, weight=1)
        self._stop_on_form = tk.BooleanVar(value=True)
        tk.Checkbutton(det_f, text="Stop on forming event", variable=self._stop_on_form, bg=C["panel"], fg=C["text"], selectcolor=C["entry"], activebackground=C["panel"], activeforeground=C["text"], highlightthickness=0, bd=0, font=FONT_LABEL).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=3)
        self._form_thresh = tk.StringVar(value="0.95")
        param_row(det_f, 1, "I_form / I_comp", self._form_thresh, label_width=17)

        vf = mk_lf(L, "Post-Form Verification", C["accent"]); vf.pack(fill="x", padx=6, pady=4); vf.columnconfigure(1, weight=1)
        self._vp = {}
        for i, (lbl, key, dflt) in enumerate([
            ("Delay (s)",   "delay",  "1e-3"),
            ("V_read (V)",  "v_read", "0.1"),
            ("t_read (s)",  "t_read", "5e-3"),
        ]):
            self._vp[key] = tk.StringVar(value=dflt); param_row(vf, i, lbl, self._vp[key], label_width=17)

        self._plot = DualLivePlot(R, "Forming: I vs V", "Voltage (V)", "Current (A)")
        self._plot.pack(fill="both", expand=True)

    def _run(self):
        drv = self.get_drv()
        if not drv: self.log.log("Not connected.", "err"); return
        v_start, v_stop, v_step = float(self._fp["v_start"].get()), float(self._fp["v_stop"].get()), abs(float(self._fp["v_step"].get()))
        t_step, comp, nplc = float(self._fp["t_step"].get()), float(self._fp["comp"].get()), float(self._fp["nplc"].get())
        stop_form, form_thr = self._stop_on_form.get(), float(self._form_thresh.get())
        v_read, t_read, delay = float(self._vp["v_read"].get()), float(self._vp["t_read"].get()), float(self._vp["delay"].get())
        runs = int(self._n_runs_var.get())

        if v_step <= 0: self.log.log("V_step must be > 0.", "err"); return
        self._q = queue.Queue(); self._reps = []; self._plot.clear()

        def _task(evt):
            for r in range(1, runs + 1):
                if evt.is_set(): break
                csv_path = init_live_save(self.app_vars, "Forming", r, ["V_step(V)", "Current(A)", "Conductance(S)"], self.app_state.get("seq_folder"))
                def _prog(pct, row, refs, _c=csv_path, _r=r):
                    append_live_save(_c, [row[0], row[1], row[2]])
                    self._q.put({"type": "prog", "pct": pct, "refs": refs, "v": row[0], "i": row[1]})
                res = drv.run_forming(v_start, v_stop, v_step, t_step, comp, nplc, stop_form, form_thr, v_read, t_read, delay, _prog, evt)
                self._q.put({"type": "run_done", "run": r, "data": res})
            return True

        self._acq = AcqThread(_task, self._q); self._acq.start()
        self._run_btn.config(state="disabled"); self.status.busy("Forming…"); self._poll()

    def _poll(self):
        try:
            while True:
                msg = self._q.get_nowait()
                if msg["type"] == "prog":
                    self._prog.set(msg["pct"]); self._lv_label.config(text=f"V: {msg['v']:+.3f}"); self._li_label.config(text=f"I: {msg['i']:.3E}")
                elif msg["type"] == "run_done":
                    d = msg["data"]; self._reps.append(msg)
                    self._plot.add_trace(d["voltages"], d["currents"], label=f"Run {msg['run']}", color_idx=msg["run"] - 1)
                    formed_str = f"FORMED @ {d['formed_v']:.3f} V" if d["formed"] else "Not formed in range"
                    g_str = f"  G_post={d['g_post']:.3e} S" if d["g_post"] is not None else ""
                    self._status_label.config(text=f"Run {msg['run']}: {formed_str}{g_str}", fg=C["accent3"] if d["formed"] else C["accent4"])
                    self.log.log(f"Forming run {msg['run']}: {formed_str}{g_str}", "ok")
                    auto_save_fig(self.app_vars, "Forming", msg["run"], self._plot.fig, self.app_state.get("seq_folder"))
                elif msg["type"] == "done":
                    self._prog.set(100, "Done"); self._run_btn.config(state="normal"); self.status.ok("Forming complete."); self.finish_run(); return
                elif msg["type"] == "error":
                    self.log.log(f"Forming error: {msg['message']}", "err"); self._prog.reset(); self._run_btn.config(state="normal"); return
        except queue.Empty: pass
        self.start_poll(self._poll)

    def _export(self):
        if not self._reps: return
        def _z(w):
            for item in self._reps:
                d = item["data"]
                for v, i, g in zip(d["voltages"], d["currents"], d["conductances"]): w.writerow([item["run"], v, i, g])
        self.manual_export("Forming", ["V_step(V)", "Current(A)", "Conductance(S)"], _z)

class DCIVTab(BaseMeasurementTab):
    def __init__(self, parent, get_driver, log, status, app_vars, app_state):
        super().__init__(parent, get_driver, log, status, app_vars, app_state)
        self._current_data = []; self._build()

    def _build(self):
        strip = tk.Frame(self, bg=C["strip"]); strip.pack(fill="x")
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x")
        tk.Label(strip, text="  DC I-V SWEEP - Full Direction & Asymmetric Compliance",
                 bg=C["strip"], fg=C["accent"], font=FONT_HEADER).pack(side="left", padx=10, pady=6)
        self._run_btn = mk_button(strip, "▶ RUN", self._run, color=C["btn"],
                                  fg=C["accent3"], font=FONT_HEADER)
        self._run_btn.pack(side="left", padx=4, pady=4)
        mk_button(strip, "■ STOP", self.safe_stop, color=C["btn"], fg=C["red"]).pack(side="left", padx=4, pady=4)
        mk_button(strip, "CSV", self._export, color=C["btn"]).pack(side="left", padx=4, pady=4)
        mk_button(strip, "Clear", lambda: (self._plot.clear(), self._current_data.clear()),
                  color=C["btn"]).pack(side="left", padx=4, pady=4)
        self._prog = ProgressBar(self); self._prog.pack(fill="x")

        live = tk.Frame(self, bg=C["panel2"]); live.pack(fill="x")
        self._lv = tk.Label(live, text="V: ---", bg=C["panel2"], fg=C["text"], font=FONT_BIG, width=14)
        self._lv.pack(side="left", padx=12, pady=3)
        self._li = tk.Label(live, text="I: ---", bg=C["panel2"], fg=C["text"], font=FONT_BIG, width=18)
        self._li.pack(side="left", padx=8, pady=3)
        self._lg = tk.Label(live, text="G: ---", bg=C["panel2"], fg=C["accent3"], font=FONT_BIG, width=18)
        self._lg.pack(side="left", padx=8, pady=3)
        self._l_comp = tk.Label(live, text="COMP: ---", bg=C["panel2"], fg=C["accent4"], font=FONT_LABEL, width=16)
        self._l_comp.pack(side="left", padx=8, pady=3)

        body = tk.Frame(self, bg=C["panel"]); body.pack(fill="both", expand=True)
        _, L = scrolled_frame(body, width=305)
        R = tk.Frame(body, bg=C["panel"]); R.pack(side="left", fill="both", expand=True, padx=4, pady=4)

        sp = mk_lf(L, "Sweep Parameters", C["accent"]); sp.pack(fill="x", padx=6, pady=(6,2))
        sp.columnconfigure(1, weight=1)
        self._sv = {}
        for i, (lbl, key, dflt) in enumerate([
            ("V_pos max (V)",   "v_pos",    "2.0"),
            ("V_neg max (V)",   "v_neg",    "-2.0"),
            ("V_step (V)",      "v_step",   "0.01"),
            ("N Cycles",        "n_cycles", "1"),
            ("Delay (s)",       "delay",    "1e-3"),
            ("NPLC",            "nplc",     "1.0"),
        ]):
            self._sv[key] = tk.StringVar(value=dflt)
            param_row(sp, i, lbl, self._sv[key], label_width=17)

        dir_f = mk_lf(L, "Sweep Direction", C["accent2"]); dir_f.pack(fill="x", padx=6, pady=2)
        self._sweep_mode = tk.StringVar(value="pos_first")
        modes = [
            ("0 → + → 0 → − → 0  (standard)",  "pos_first"),
            ("0 → − → 0 → + → 0  (neg first)",  "neg_first"),
            ("0 → + → 0  (unipolar positive)",   "pos_only"),
            ("0 → − → 0  (unipolar negative)",   "neg_only"),
            ("− → +  (direct, no zero)",          "neg_to_pos"),
            ("+ → −  (direct, no zero)",          "pos_to_neg"),
        ]
        for txt, val in modes:
            tk.Radiobutton(dir_f, text=txt, variable=self._sweep_mode, value=val,
                           bg=C["panel"], fg=C["text2"], font=FONT_SMALL,
                           selectcolor=C["entry"], activebackground=C["panel"]
                           ).pack(anchor="w", padx=8, pady=1)

        cf = mk_lf(L, "Compliance Control (Asymmetric)", C["accent4"]); cf.pack(fill="x", padx=6, pady=2)
        cf.columnconfigure(1, weight=1)
        self._sv["comp_pos"] = tk.StringVar(value="0.1")
        self._sv["comp_neg"] = tk.StringVar(value="0.1")
        self._asym_var = tk.BooleanVar(value=False)
        tk.Checkbutton(cf, text="Enable Asymmetric Compliance", variable=self._asym_var,
                       bg=C["panel"], fg=C["accent4"], font=FONT_SMALL,
                       selectcolor=C["entry"], activebackground=C["panel"],
                       command=self._toggle_asym).pack(anchor="w", padx=6, pady=2)
        self._comp_pos_row = tk.Frame(cf, bg=C["panel"]); self._comp_pos_row.pack(fill="x")
        tk.Label(self._comp_pos_row, text="I_comp+ (A):", bg=C["panel"], fg=C["text2"],
                 font=FONT_LABEL, width=14, anchor="e").pack(side="left", padx=(8,4))
        mk_entry(self._comp_pos_row, textvariable=self._sv["comp_pos"], width=12).pack(side="left")
        self._comp_neg_row = tk.Frame(cf, bg=C["panel"]); self._comp_neg_row.pack(fill="x")
        tk.Label(self._comp_neg_row, text="I_comp− (A):", bg=C["panel"], fg=C["text2"],
                 font=FONT_LABEL, width=14, anchor="e").pack(side="left", padx=(8,4))
        self._comp_neg_entry = mk_entry(self._comp_neg_row, textvariable=self._sv["comp_neg"], width=12)
        self._comp_neg_entry.pack(side="left")
        tk.Label(cf, text="  ↑ Positive branch | Negative branch ↑",
                 bg=C["panel"], fg=C["muted"], font=FONT_SMALL).pack(anchor="w", padx=8, pady=(0,4))
        self._toggle_asym()

        info_f = mk_lf(L, "Protocol Info", C["muted"]); info_f.pack(fill="x", padx=6, pady=2)
        tk.Label(info_f,
                 text="Asymmetric compliance:\n  + branch → I_comp+\n  − branch → I_comp−\n"
                      "(switched per voltage point)\nUseful for:\n  SET needs high compliance\n  RESET needs lower to avoid\n  hard breakdown.",
                 bg=C["panel"], fg=C["muted"], font=FONT_SMALL, justify="left").pack(padx=6, pady=4)

        self._plot = DualLivePlot(R, "I-V Characteristic", "Voltage (V)", "Current (A)")
        self._plot.pack(fill="both", expand=True)

    def _toggle_asym(self):
        state = "normal" if self._asym_var.get() else "disabled"
        self._comp_neg_entry.config(state=state)

    def _run(self):
        drv = self.get_drv()
        if not drv: self.log.log("Not connected.", "err"); return
        v_pos   = float(self._sv["v_pos"].get())
        v_neg   = float(self._sv["v_neg"].get())
        v_step  = float(self._sv["v_step"].get())
        runs    = int(self._sv["n_cycles"].get())
        delay   = float(self._sv["delay"].get())
        nplc    = float(self._sv["nplc"].get())
        comp_p  = float(self._sv["comp_pos"].get())
        comp_n  = float(self._sv["comp_neg"].get()) if self._asym_var.get() else None
        mode    = self._sweep_mode.get()

        comp_label = f"I+={comp_p:.2e}A  I−={comp_n:.2e}A" if comp_n else f"I={comp_p:.2e}A"
        self._l_comp.config(text=f"COMP: {comp_label}")

        plot_idxs = select_plot_indices(runs)
        self._q = queue.Queue(); self._current_data = []; self._plot.clear()

        def _task(evt):
            for r in range(1, runs + 1):
                if evt.is_set(): break
                csv_path = init_live_save(self.app_vars, "DC_Sweep", r,
                    ["Voltage(V)", "Current(A)", "Conductance(S)"], self.app_state.get("seq_folder"))
                def _prog(pct, row, refs, _c=csv_path):
                    append_live_save(_c, row)
                    self._q.put({"type": "prog", "pct": pct, "v": row[0], "i": row[1], "g": row[2]})
                res = drv.sweep_true_zero(v_pos, v_neg, v_step, comp_p, nplc, delay,
                                         _prog, evt, sweep_mode=mode, comp_neg=comp_n)
                self._q.put({"type": "run_done", "run": r, "data": res, "plot": r in plot_idxs})
            return True

        self._acq = AcqThread(_task, self._q); self._acq.start()
        self._run_btn.config(state="disabled"); self.status.busy("DC Sweep running…"); self._poll()

    def _poll(self):
        try:
            while True:
                msg = self._q.get_nowait()
                if msg["type"] == "prog":
                    self._prog.set(msg["pct"])
                    self._lv.config(text=f"V: {msg['v']:+.4f}")
                    self._li.config(text=f"I: {msg['i']:.3E}")
                    self._lg.config(text=f"G: {msg['g']:.3E}")
                elif msg["type"] == "run_done":
                    d = msg["data"]; self._current_data.append(msg)
                    if msg["plot"]:
                        self._plot.add_trace(d["voltages"], d["currents"],
                                             label=f"Run {msg['run']}", color_idx=msg["run"] - 1)
                        self._plot.vline(0); self._plot.hline(0)
                    auto_save_fig(self.app_vars, "DC_Sweep", msg["run"],
                                  self._plot.fig, self.app_state.get("seq_folder"))
                elif msg["type"] == "done":
                    self._prog.set(100, "Done"); self._run_btn.config(state="normal")
                    self.status.ok("DC Sweep complete."); self.finish_run(); return
                elif msg["type"] == "error":
                    self.log.log(f"DC Sweep error: {msg['message']}", "err")
                    self._prog.reset(); self._run_btn.config(state="normal"); return
        except queue.Empty: pass
        self.start_poll(self._poll)

    def _export(self):
        if not self._current_data: return
        def _z(w):
            for item in self._current_data:
                d = item["data"]
                for v, i, g in zip(d["voltages"], d["currents"], d["conductances"]):
                    w.writerow([item["run"], v, i, g])
        self.manual_export("DC_Sweep", ["Voltage(V)", "Current(A)", "Conductance(S)"], _z)


class PulseIVTab(BaseMeasurementTab):
    def __init__(self, parent, get_driver, log, status, app_vars, app_state):
        super().__init__(parent, get_driver, log, status, app_vars, app_state)
        self._reps = []; self._build()

    def _build(self):
        strip = tk.Frame(self, bg=C["strip"]); strip.pack(fill="x")
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x")
        tk.Label(strip, text="  PULSE I-V (Write-Read / Ferroelectric)", bg=C["strip"], fg=C["text"], font=FONT_HEADER).pack(side="left", padx=10, pady=6)
        self._run_btn = mk_button(strip, "▶ RUN", self._run, color=C["strip"], fg=C["accent2"], font=FONT_HEADER); self._run_btn.pack(side="left", padx=4, pady=4)
        mk_button(strip, "■ STOP", self.safe_stop, color=C["btn"], fg=C["red"]).pack(side="left", padx=4, pady=4)
        mk_button(strip, "CSV", self._export, color=C["btn"]).pack(side="left", padx=4, pady=4)
        self._prog = ProgressBar(self); self._prog.pack(fill="x")

        body = tk.Frame(self, bg=C["panel"]); body.pack(fill="both", expand=True)
        _, L = scrolled_frame(body, width=305)
        R = tk.Frame(body, bg=C["panel"]); R.pack(side="left", fill="both", expand=True, padx=4, pady=4)

        pf = mk_lf(L, "Pulse Sequences", C["accent2"]); pf.pack(fill="x", padx=6, pady=4); pf.columnconfigure(1, weight=1)
        self._p = {}
        for i, (lbl, key, dflt) in enumerate([
            ("V_SET start (V)", "v_set_s", "0.0"),
            ("V_SET stop (V)",  "v_set",   "2.0"),
            ("V_SET step (V)",  "v_set_st","0.05"),
            ("V_RST start (V)", "v_rst_s", "0.0"),
            ("V_RST stop (V)",  "v_rst",   "-2.0"),
            ("V_RST step (V)",  "v_rst_st","-0.05"),
        ]):
            self._p[key] = var = tk.StringVar(value=dflt)
            param_row(pf, i, lbl, var, label_width=17)

        rf = mk_lf(L, "Timings & Settings", C["accent3"]); rf.pack(fill="x", padx=6, pady=4); rf.columnconfigure(1, weight=1)
        tk.Label(rf, text="Mode", bg=C["panel"], fg=C["text2"], font=FONT_LABEL, width=17, anchor="e").grid(row=0, column=0, sticky="e", padx=(8, 4), pady=3)
        self._mode = tk.StringVar(value="Write-Read (Memory)")
        cb = mk_combobox(rf, values=["Write-Read (Memory)", "Measure During Write"], width=19, textvariable=self._mode)
        cb.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=3)

        for i, (lbl, key, dflt) in enumerate([
            ("t_write (s)",      "t_write", "5e-3"),
            ("Delay (s)",        "delay",   "1e-3"),
            ("V_read (V)",       "v_read",  "0.1"),
            ("t_read (s)",       "t_read",  "5e-3"),
            ("Compliance (A)",   "comp",    "0.1"),
            ("NPLC",             "nplc",    "0.1"),
            ("N Runs",           "n_runs",  "1"),
        ], start=1):
            self._p[key] = var = tk.StringVar(value=dflt)
            param_row(rf, i, lbl, var, label_width=17)

        self._plot = DualLivePlot(R, "Pulse I-V", "Voltage (V)", "Measured Current (A)")
        self._plot.pack(fill="both", expand=True)

    def _run(self):
        drv = self.get_drv()
        if not drv: self.log.log("Not connected.", "err"); return

        set_p = {"start": float(self._p["v_set_s"].get()), "stop": float(self._p["v_set"].get()), "step": float(self._p["v_set_st"].get())}
        rst_p = {"start": float(self._p["v_rst_s"].get()), "stop": float(self._p["v_rst"].get()), "step": float(self._p["v_rst_st"].get())}

        runs, t_write, t_read = int(self._p["n_runs"].get()), float(self._p["t_write"].get()), float(self._p["t_read"].get())
        v_read, delay, comp, nplc = float(self._p["v_read"].get()), float(self._p["delay"].get()), float(self._p["comp"].get()), float(self._p["nplc"].get())
        mode = self._mode.get()
        plot_idxs = select_plot_indices(runs)

        self._q = queue.Queue(); self._reps = []; self._plot.clear()

        def _task(evt):
            for r in range(1, runs + 1):
                if evt.is_set(): break
                csv_path = init_live_save(self.app_vars, "Pulse_IV", r, ["V_write(V)", "I_meas(A)", "Conductance(S)"], self.app_state.get("seq_folder"))
                def _prog(pct, row, refs, _c=csv_path):
                    append_live_save(_c, [row[0], row[1], row[2]])
                    self._q.put({"type": "prog", "pct": pct})
                res = drv.run_pulse_iv(set_p, rst_p, t_write, t_read, v_read, delay, mode, comp, nplc, _prog, evt)
                self._q.put({"type": "run_done", "run": r, "data": res, "plot": r in plot_idxs})
            return True

        self._acq = AcqThread(_task, self._q); self._acq.start()
        self._run_btn.config(state="disabled"); self.status.busy("Pulse IV running…"); self._poll()

    def _poll(self):
        try:
            while True:
                msg = self._q.get_nowait()
                if msg["type"] == "prog": self._prog.set(msg["pct"])
                elif msg["type"] == "run_done":
                    self._reps.append(msg)
                    if msg["plot"]:
                        d = msg["data"]; self._plot.add_trace(d["voltages"], d["currents"], label=f"Run {msg['run']}", color_idx=msg["run"] - 1)
                        self._plot.hline(0); self._plot.vline(0)
                    auto_save_fig(self.app_vars, "Pulse_IV", msg["run"], self._plot.fig, self.app_state.get("seq_folder"))
                elif msg["type"] == "done":
                    self._prog.set(100, "Done"); self._run_btn.config(state="normal"); self.status.ok("Pulse IV complete."); self.finish_run(); return
                elif msg["type"] == "error":
                    self.log.log(f"Pulse IV error: {msg['message']}", "err"); self._prog.reset(); self._run_btn.config(state="normal"); return
        except queue.Empty: pass
        self.start_poll(self._poll)

    def _export(self):
        if not self._reps: return
        def _z(w):
            for item in self._reps:
                for v, i, g in zip(item["data"]["voltages"], item["data"]["currents"], item["data"]["conductances"]): w.writerow([item["run"], v, i, g])
        self.manual_export("Pulse_IV", ["V_write(V)", "I_meas(A)", "Conductance(S)"], _z)


class LTPLTDTab(BaseMeasurementTab):
    SCHEMES = ["Constant Amp & Width", "Incremental Pulse Width", "Incremental Amplitude", "Logarithmic Amplitude"]
    def __init__(self, parent, get_driver, log, status, app_vars, app_state):
        super().__init__(parent, get_driver, log, status, app_vars, app_state)
        self._reps = []; self._n_runs_var = tk.StringVar(value="1"); self._build()

    def _build(self):
        strip = tk.Frame(self, bg=C["strip"]); strip.pack(fill="x")
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x")
        tk.Label(strip, text="  LTP / LTD", bg=C["strip"], fg=C["text"], font=FONT_HEADER).pack(side="left", padx=10, pady=6)
        self._run_btn = mk_button(strip, "▶ RUN LTP/LTD", self._run, color=C["btn"], fg=C["accent2"], font=FONT_HEADER); self._run_btn.pack(side="left", padx=4, pady=4)
        mk_button(strip, "■ STOP", self.safe_stop, color=C["btn"], fg=C["red"]).pack(side="left", padx=4)
        mk_button(strip, "CSV", self._export, color=C["btn"]).pack(side="left", padx=4)
        self._prog = ProgressBar(self); self._prog.pack(fill="x")

        body = tk.Frame(self, bg=C["panel"]); body.pack(fill="both", expand=True)
        _, L = scrolled_frame(body, width=310)
        R = tk.Frame(body, bg=C["panel"]); R.pack(side="left", fill="both", expand=True, padx=4, pady=4)

        sf = mk_lf(L, "Scheme", C["accent4"]); sf.pack(fill="x", padx=6, pady=(6, 2)); sf.columnconfigure(1, weight=1)
        tk.Label(sf, text="Scheme", bg=C["panel"], fg=C["text2"], font=FONT_LABEL, width=17, anchor="e").grid(row=0, column=0, sticky="e", padx=(8, 4), pady=3)
        self._scheme_var = tk.StringVar(value=self.SCHEMES[0])
        mk_combobox(sf, values=self.SCHEMES, width=22, textvariable=self._scheme_var).grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=3)

        pf = mk_lf(L, "Potentiation", C["accent3"]); pf.pack(fill="x", padx=6, pady=2); pf.columnconfigure(1, weight=1)
        self._pp = {}
        for i, (lbl, key, dflt) in enumerate([
            ("V_pot max (V)",   "v_pot",    "1.5"),
            ("V_pot start (V)", "v_pot_s",  "0.5"),
            ("V_pot step (V)",  "v_pot_st", "0.05"),
            ("t_pulse (s)",     "t_pot",    "5e-3"),
            ("t_start (s)",     "t_pot_s",  "5e-3"),
            ("N_pot pulses",    "n_pot",    "50"),
        ]):
            self._pp[key] = var = tk.StringVar(value=dflt); param_row(pf, i, lbl, var, label_width=17)

        df = mk_lf(L, "Depotentiation", C["red"]); df.pack(fill="x", padx=6, pady=2); df.columnconfigure(1, weight=1)
        self._dp = {}
        for i, (lbl, key, dflt) in enumerate([
            ("V_dep max (V)",   "v_dep",    "-1.5"),
            ("V_dep start (V)", "v_dep_s",  "-0.5"),
            ("V_dep step (V)",  "v_dep_st", "-0.05"),
            ("t_dep (s)",       "t_dep",    "5e-3"),
            ("t_start (s)",     "t_dep_s",  "5e-3"),
            ("N_dep pulses",    "n_dep",    "50"),
        ]):
            self._dp[key] = var = tk.StringVar(value=dflt); param_row(df, i, lbl, var, label_width=17)

        gf = mk_lf(L, "Read & Delays", C["accent"]); gf.pack(fill="x", padx=6, pady=2); gf.columnconfigure(1, weight=1)
        self._gp = {}
        for i, (lbl, key, dflt) in enumerate([
            ("Delay (s)",      "delay",   "1e-3"),
            ("V_read (V)",     "v_read",  "0.1"),
            ("t_read (s)",     "t_read",  "5e-3"),
            ("Compliance (A)", "comp",    "0.1"),
            ("NPLC",           "nplc",    "0.1"),
            ("N Runs",         "n_runs",  "1"),
        ]):
            self._gp[key] = var = tk.StringVar(value=dflt); param_row(gf, i, lbl, var, label_width=17)
        self._n_runs_var = self._gp["n_runs"]

        self._plot = LivePlot(R, "LTP/LTD - G vs Pulse Number", "Pulse Number", "Conductance (S)")
        self._plot.pack(fill="both", expand=True)

    def _build_pulse_lists(self):
        scheme = self._scheme_var.get()
        v_pot, v_pot_s, v_pot_st = float(self._pp["v_pot"].get()), float(self._pp["v_pot_s"].get()), float(self._pp["v_pot_st"].get())
        t_pot, t_pot_s, n_pot = float(self._pp["t_pot"].get()), float(self._pp["t_pot_s"].get()), int(self._pp["n_pot"].get())

        v_dep, v_dep_s, v_dep_st = float(self._dp["v_dep"].get()), float(self._dp["v_dep_s"].get()), float(self._dp["v_dep_st"].get())
        t_dep, t_dep_s, n_dep = float(self._dp["t_dep"].get()), float(self._dp["t_dep_s"].get()), int(self._dp["n_dep"].get())

        if scheme == "Constant Amp & Width":
            pot = [(v_pot, t_pot)] * n_pot; dep = [(v_dep, t_dep)] * n_dep
        elif scheme == "Incremental Pulse Width":
            pot = [(v_pot, t_pot_s + (t_pot - t_pot_s)*k/max(n_pot-1,1)) for k in range(n_pot)]
            dep = [(v_dep, t_dep_s + (t_dep - t_dep_s)*k/max(n_dep-1,1)) for k in range(n_dep)]
        elif scheme == "Incremental Amplitude":
            pot_amps = gen_sweep_points(v_pot_s, v_pot, v_pot_st)
            dep_amps = gen_sweep_points(v_dep_s, v_dep, v_dep_st)
            pot = [(a, t_pot) for a in pot_amps]; dep = [(a, t_dep) for a in dep_amps]
        else:
            pot = [(a*(1 if v_pot>0 else -1), t_pot) for a in np.logspace(math.log10(abs(v_pot_s)), math.log10(abs(v_pot)), n_pot)]
            dep = [(a*(1 if v_dep>0 else -1), t_dep) for a in np.logspace(math.log10(abs(v_dep_s)), math.log10(abs(v_dep)), n_dep)]
        return pot, dep

    def _run(self):
        drv = self.get_drv()
        if not drv: self.log.log("Not connected.", "err"); return
        runs, v_read, t_read = int(self._gp["n_runs"].get()), float(self._gp["v_read"].get()), float(self._gp["t_read"].get())
        comp, nplc, delay = float(self._gp["comp"].get()), float(self._gp["nplc"].get()), float(self._gp["delay"].get())
        try: pot_p, dep_p = self._build_pulse_lists()
        except Exception as e: self.log.log(f"Pulse config error: {e}", "err"); return

        plot_idxs = select_plot_indices(runs)
        self._q = queue.Queue(); self._reps = []; self._plot.clear()

        def _task(evt):
            for r in range(1, runs + 1):
                if evt.is_set(): break
                csv_path = init_live_save(self.app_vars, "LTP_LTD", r, ["Pulse", "G(S)", "R(Ohm)", "V_amp(V)", "Phase"], self.app_state.get("seq_folder"))
                def _prog(pct, row, refs, _c=csv_path):
                    append_live_save(_c, [row[0], row[1], row[2], row[3], row[4]])
                    self._q.put({"type": "prog", "pct": pct})
                res = drv.run_ltp_ltd(pot_p, dep_p, v_read, t_read, delay, comp, nplc, _prog, evt)

                self.app_state["ltp_last_run"] = res
                self.app_state["ltp_v_read"] = v_read
                self.app_state["ltp_t_read"] = t_read

                self._q.put({"type": "run_done", "run": r, "data": res, "plot": r in plot_idxs})
            return True

        self._acq = AcqThread(_task, self._q); self._acq.start()
        self._run_btn.config(state="disabled"); self.status.busy("LTP/LTD running…"); self._poll()

    def _poll(self):
        try:
            while True:
                msg = self._q.get_nowait()
                if msg["type"] == "prog": self._prog.set(msg["pct"])
                elif msg["type"] == "run_done":
                    d = msg["data"]; self._reps.append(msg)
                    if msg["plot"]:
                        ps, gs, phs = d["pulse_num"], d["conductances"], d["phases"]
                        pot_p, pot_g = [p for p,ph in zip(ps,phs) if ph=="POT"], [g for g,ph in zip(gs,phs) if ph=="POT"]
                        dep_p, dep_g = [p for p,ph in zip(ps,phs) if ph=="DEP"], [g for g,ph in zip(gs,phs) if ph=="DEP"]
                        if pot_p: self._plot.add_trace(pot_p, pot_g, label=f"POT R{msg['run']}", color_idx=msg["run"]-1, marker="o", lw=1.0)
                        if dep_p: self._plot.add_trace(dep_p, dep_g, label=f"DEP R{msg['run']}", color_idx=msg["run"]-1, ls="--", marker="s", lw=1.0)
                    auto_save_fig(self.app_vars, "LTP_LTD", msg["run"], self._plot.fig, self.app_state.get("seq_folder"))
                elif msg["type"] == "done":
                    self._prog.set(100, "Done"); self._run_btn.config(state="normal"); self.status.ok("LTP/LTD complete."); self.finish_run(); return
                elif msg["type"] == "error":
                    self.log.log(f"LTP/LTD error: {msg['message']}", "err"); self._prog.reset(); self._run_btn.config(state="normal"); return
        except queue.Empty: pass
        self.start_poll(self._poll)

    def _export(self):
        if not self._reps: return
        def _z(w):
            for item in self._reps:
                d = item["data"]
                for p, g, r2, v, ph in zip(d["pulse_num"], d["conductances"], d["resistances"], d["voltages"], d["phases"]): w.writerow([item["run"], p, g, r2, v, ph])
        self.manual_export("LTP_LTD", ["Pulse", "G(S)", "R(Ohm)", "V_amp(V)", "Phase"], _z)

class PPFPPDTab(BaseMeasurementTab):
    def __init__(self, parent, get_driver, log, status, app_vars, app_state):
        super().__init__(parent, get_driver, log, status, app_vars, app_state)
        self._build()

    def _build(self):
        nb = ttk.Notebook(self); nb.pack(fill="both", expand=True, padx=4, pady=4)
        self._t_ppf = tk.Frame(nb, bg=C["panel"])
        self._t_ppd = tk.Frame(nb, bg=C["panel"])
        nb.add(self._t_ppf, text="  PPF - Facilitation  ")
        nb.add(self._t_ppd, text="  PPD - Depression  ")
        self._bld_sub(self._t_ppf, "PPF", "Paired-Pulse Facilitation", "1.5", C["strip"], C["accent2"])
        self._bld_sub(self._t_ppd, "PPD", "Paired-Pulse Depression", "-1.5", C["strip"], C["accent3"])

    def _bld_sub(self, parent, key, title, v_dflt, strip_bg, fg):
        strip = tk.Frame(parent, bg=C["strip"]); strip.pack(fill="x")
        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x")
        tk.Label(strip, text=f"  {title}", bg=C["strip"], fg=fg, font=FONT_HEADER).pack(side="left", padx=10, pady=6)
        run_btn = mk_button(strip, f"▶ RUN {key}", lambda k=key: self._run(k), color=C["btn"], fg=fg, font=FONT_HEADER); run_btn.pack(side="left", padx=4, pady=4)
        mk_button(strip, "■ STOP", self.safe_stop, color=C["btn"], fg=C["red"]).pack(side="left", padx=4)
        mk_button(strip, "CSV", lambda k=key: self._export(k), color=C["btn"]).pack(side="left", padx=4)

        prog = ProgressBar(parent); prog.pack(fill="x")
        body = tk.Frame(parent, bg=C["panel"]); body.pack(fill="both", expand=True)
        _, L = scrolled_frame(body, width=295)
        R = tk.Frame(body, bg=C["panel"]); R.pack(side="left", fill="both", expand=True, padx=4, pady=4)

        pf = mk_lf(L, f"{key} Parameters", fg); pf.pack(fill="x", padx=6, pady=4); pf.columnconfigure(1, weight=1)
        p = {}
        for i, (lbl, k2, dflt) in enumerate([
            ("V_pulse (V)",  "vp",   v_dflt),
            ("t_pulse (s)",  "tp",   "5e-3"),
            ("Delay (s)",    "delay","1e-3"),
            ("ISI min (s)",  "imin", "0.002"),
            ("ISI max (s)",  "imax", "1.0"),
            ("N ISI pts",    "npts", "15"),
            ("Log ISI",      "log",  "1"),
            ("V_read (V)",   "vr",   "0.1"),
            ("t_read (s)",   "tr",   "5e-3"),
            ("Compliance",   "comp", "0.1"),
            ("NPLC",         "nplc", "0.1"),
            ("N Runs",       "nrun", "1"),
        ]):
            p[k2] = var = tk.StringVar(value=dflt); param_row(pf, i, lbl, var, label_width=15)

        plot = LivePlot(R, f"{title}: Ratio vs ISI", "ISI (ms)", "G₂ / G₁")
        plot.pack(fill="both", expand=True)

        if key == "PPF":
            self._ppf_p, self._ppf_prog, self._ppf_run_btn, self._ppf_plot, self._ppf_reps = p, prog, run_btn, plot, []
        else:
            self._ppd_p, self._ppd_prog, self._ppd_run_btn, self._ppd_plot, self._ppd_reps = p, prog, run_btn, plot, []

    def _run(self, key):
        drv = self.get_drv()
        if not drv: self.log.log("Not connected.", "err"); return
        p, prog, plot = (self._ppf_p, self._ppf_prog, self._ppf_plot) if key == "PPF" else (self._ppd_p, self._ppd_prog, self._ppd_plot)
        runs, vp, tp, delay = int(p["nrun"].get()), float(p["vp"].get()), float(p["tp"].get()), float(p["delay"].get())
        imin, imax, npts, use_log = float(p["imin"].get()), float(p["imax"].get()), int(p["npts"].get()), int(p["log"].get()) != 0
        vr, tr, comp, nplc = float(p["vr"].get()), float(p["tr"].get()), float(p["comp"].get()), float(p["nplc"].get())

        if use_log:
            try: isis = list(np.geomspace(imin, imax, npts))
            except: isis = [imin + k * (imax - imin) / max(npts - 1, 1) for k in range(npts)]
        else: isis = [imin + k * (imax - imin) / max(npts - 1, 1) for k in range(npts)]

        plot_idxs = select_plot_indices(runs)
        self._q = queue.Queue()
        if key == "PPF": self._ppf_reps = []
        else: self._ppd_reps = []
        plot.clear()

        def _task(evt):
            for r in range(1, runs + 1):
                if evt.is_set(): break
                csv_path = init_live_save(self.app_vars, key, r, ["ISI(ms)", "G_P1(S)", "G_P2(S)", "Ratio"], self.app_state.get("seq_folder"))
                def _prog(pct, row, refs, _c=csv_path):
                    append_live_save(_c, [row[0], row[1], row[2], row[3]])
                    self._q.put({"type": "prog", "pct": pct, "key": key})
                res = drv.run_paired_pulse(vp, tp, isis, vr, tr, delay, comp, nplc, _prog, evt)
                self._q.put({"type": "run_done", "run": r, "data": res, "key": key, "plot": r in plot_idxs})
            return True

        self._acq = AcqThread(_task, self._q); self._acq.start()
        prog.reset(); prog.set(0)
        if key == "PPF": self._ppf_run_btn.config(state="disabled")
        else: self._ppd_run_btn.config(state="disabled")
        self.status.busy(f"{key} running…"); self._poll_ppf(key)

    def _poll_ppf(self, key):
        prog, plot, run_btn = (self._ppf_prog, self._ppf_plot, self._ppf_run_btn) if key == "PPF" else (self._ppd_prog, self._ppd_plot, self._ppd_run_btn)
        reps = self._ppf_reps if key == "PPF" else self._ppd_reps
        try:
            while True:
                msg = self._q.get_nowait()
                if msg["type"] == "prog": prog.set(msg["pct"])
                elif msg["type"] == "run_done":
                    reps.append(msg)
                    if msg["plot"]:
                        d, cidx = msg["data"], msg["run"] - 1
                        plot.add_trace(d["isi_ms"], d["ratio"], label=f"Run {msg['run']}", color_idx=cidx, scatter=True)
                        plot.add_trace(d["isi_ms"], d["ratio"], color_idx=cidx, ls="--", lw=0.8)
                        plot.hline(1.0, color="#9A9A9A")
                    auto_save_fig(self.app_vars, key, msg["run"], plot.fig, self.app_state.get("seq_folder"))
                elif msg["type"] == "done":
                    prog.set(100, "Done"); run_btn.config(state="normal"); self.status.ok(f"{key} complete."); self.finish_run(); return
                elif msg["type"] == "error":
                    self.log.log(f"{key} error: {msg['message']}", "err"); prog.reset(); run_btn.config(state="normal"); return
        except queue.Empty: pass
        if self._acq and self._acq.is_alive(): self.after(50, lambda: self._poll_ppf(key))

    def _export(self, key):
        reps = self._ppf_reps if key == "PPF" else self._ppd_reps
        if not reps: return
        def _z(w):
            for item in reps:
                for isi, g1, g2, rat in zip(item["data"]["isi_ms"], item["data"]["G_P1"], item["data"]["G_P2"], item["data"]["ratio"]): w.writerow([item["run"], isi, g1, g2, rat])
        self.manual_export(key, ["ISI(ms)", "G_P1(S)", "G_P2(S)", "Ratio"], _z)

class STDPTab(BaseMeasurementTab):
    def __init__(self, parent, get_driver, log, status, app_vars, app_state):
        super().__init__(parent, get_driver, log, status, app_vars, app_state)
        self._reps = []; self._n_runs_var = tk.StringVar(value="1"); self._build()

    def _build(self):
        strip = tk.Frame(self, bg=C["strip"]); strip.pack(fill="x")
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x")
        tk.Label(strip, text="  STDP (EPSC)", bg=C["strip"], fg=C["text"], font=FONT_HEADER).pack(side="left", padx=10, pady=6)
        self._run_btn = mk_button(strip, "▶ RUN STDP", self._run, color=C["btn"], fg=C["accent4"], font=FONT_HEADER); self._run_btn.pack(side="left", padx=4, pady=4)
        mk_button(strip, "■ STOP", self.safe_stop, color=C["btn"], fg=C["red"]).pack(side="left", padx=4)
        mk_button(strip, "CSV", self._export, color=C["btn"]).pack(side="left", padx=4)

        self._prog = ProgressBar(self); self._prog.pack(fill="x")
        body = tk.Frame(self, bg=C["panel"]); body.pack(fill="both", expand=True)
        _, L = scrolled_frame(body, width=295)
        R = tk.Frame(body, bg=C["panel"]); R.pack(side="left", fill="both", expand=True, padx=4, pady=4)

        pf = mk_lf(L, "STDP Parameters", C["accent4"]); pf.pack(fill="x", padx=6, pady=4); pf.columnconfigure(1, weight=1)
        self._p = {}
        for i, (lbl, key, dflt) in enumerate([
            ("V_pre (V)",    "vpre",  "1.5"),
            ("V_post (V)",   "vpost", "-1.5"),
            ("t_pulse (s)",  "tp",    "5e-3"),
            ("Delay (s)",    "delay", "1e-3"),
            ("Δt max (s)",   "dtm",   "0.1"),
            ("N Δt points",  "ndt",   "21"),
            ("V_read (V)",   "vr",    "0.1"),
            ("t_read (s)",   "tr",    "5e-3"),
            ("Compliance",   "comp",  "0.1"),
            ("NPLC",         "nplc",  "0.1"),
            ("N Runs",       "nrun",  "1"),
        ]):
            self._p[key] = var = tk.StringVar(value=dflt); param_row(pf, i, lbl, var, label_width=15)
        self._n_runs_var = self._p["nrun"]

        self._plot = LivePlot(R, "STDP: ΔG/G₀ vs Δt", "Δt (ms)", "ΔG / G₀")
        self._plot.pack(fill="both", expand=True)

    def _run(self):
        drv = self.get_drv()
        if not drv: self.log.log("Not connected.", "err"); return
        runs, dtm, ndt = int(self._p["nrun"].get()), float(self._p["dtm"].get()), int(self._p["ndt"].get())
        dts = [-dtm + i * 2 * dtm / max(ndt - 1, 1) for i in range(ndt)]
        v_read, t_read, delay = float(self._p["vr"].get()), float(self._p["tr"].get()), float(self._p["delay"].get())
        plot_idxs = select_plot_indices(runs)

        self._q = queue.Queue(); self._reps = []; self._plot.clear()

        def _task(evt):
            for r in range(1, runs + 1):
                if evt.is_set(): break
                csv_path = init_live_save(self.app_vars, "STDP", r, ["dt(ms)", "dG/G0", "G_before(S)", "G_after(S)"], self.app_state.get("seq_folder"))
                def _prog(pct, row, refs, _c=csv_path):
                    append_live_save(_c, [row[0], row[1], row[2], row[3]])
                    self._q.put({"type": "prog", "pct": pct})
                res = drv.run_stdp(float(self._p["vpre"].get()), float(self._p["vpost"].get()), float(self._p["tp"].get()), dts, v_read, t_read, delay, float(self._p["comp"].get()), float(self._p["nplc"].get()), _prog, evt)
                self._q.put({"type": "run_done", "run": r, "data": res, "plot": r in plot_idxs})
            return True

        self._acq = AcqThread(_task, self._q); self._acq.start()
        self._run_btn.config(state="disabled"); self.status.busy("STDP running…"); self._poll()

    def _poll(self):
        try:
            while True:
                msg = self._q.get_nowait()
                if msg["type"] == "prog": self._prog.set(msg["pct"])
                elif msg["type"] == "run_done":
                    self._reps.append(msg)
                    if msg["plot"]:
                        d = msg["data"]
                        self._plot.add_trace(d["dt_ms"], d["dG_norm"], label=f"Run {msg['run']}", color_idx=msg["run"] - 1, scatter=True)
                        self._plot.hline(0.0)
                    auto_save_fig(self.app_vars, "STDP", msg["run"], self._plot.fig, self.app_state.get("seq_folder"))
                elif msg["type"] == "done":
                    self._prog.set(100, "Done"); self._run_btn.config(state="normal"); self.status.ok("STDP complete."); self.finish_run(); return
                elif msg["type"] == "error":
                    self.log.log(f"STDP error: {msg['message']}", "err"); self._prog.reset(); self._run_btn.config(state="normal"); return
        except queue.Empty: pass
        self.start_poll(self._poll)

    def _export(self):
        if not self._reps: return
        def _z(w):
            for item in self._reps:
                d = item["data"]
                for dt, dg, gb, ga in zip(d["dt_ms"], d["dG_norm"], d["G_before"], d["G_after"]): w.writerow([item["run"], dt, dg, gb, ga])
        self.manual_export("STDP", ["dt(ms)", "dG/G0", "G_before(S)", "G_after(S)"], _z)

class EnduranceTab(BaseMeasurementTab):
    def __init__(self, parent, get_driver, log, status, app_vars, app_state):
        super().__init__(parent, get_driver, log, status, app_vars, app_state)
        self._reps = []; self._build()

    def _build(self):
        strip = tk.Frame(self, bg=C["strip"]); strip.pack(fill="x")
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x")
        tk.Label(strip, text="  ENDURANCE - Adaptive HRS/LRS Tracking", bg=C["strip"],
                 fg=C["accent"], font=FONT_HEADER).pack(side="left", padx=10, pady=6)
        self._run_btn = mk_button(strip, "▶ RUN", self._run, color=C["strip"],
                                  fg=C["accent"], font=FONT_HEADER)
        self._run_btn.pack(side="left", padx=4, pady=4)
        mk_button(strip, "■ STOP", self.safe_stop, color=C["btn"], fg=C["red"]).pack(side="left", padx=4)
        mk_button(strip, "STATS", self._show_stats, color=C["btn"]).pack(side="left", padx=4)
        mk_button(strip, "CSV", self._export, color=C["btn"]).pack(side="left", padx=4)

        self._prog = ProgressBar(self); self._prog.pack(fill="x")

        live = tk.Frame(self, bg=C["panel2"]); live.pack(fill="x")
        self._l_cyc  = tk.Label(live, text="Cyc: ---", bg=C["panel2"], fg=C["text"],  font=FONT_BIG, width=12); self._l_cyc.pack(side="left",  padx=8, pady=3)
        self._l_lrs  = tk.Label(live, text="G_LRS: ---", bg=C["panel2"], fg=C["accent3"], font=FONT_BIG, width=18); self._l_lrs.pack(side="left", padx=6, pady=3)
        self._l_hrs  = tk.Label(live, text="G_HRS: ---", bg=C["panel2"], fg=C["accent4"], font=FONT_BIG, width=18); self._l_hrs.pack(side="left", padx=6, pady=3)
        self._l_oo   = tk.Label(live, text="ON/OFF: ---", bg=C["panel2"], fg=C["accent2"], font=FONT_BIG, width=14); self._l_oo.pack(side="left",  padx=6, pady=3)
        self._l_mode = tk.Label(live, text="Mode: ---", bg=C["panel2"], fg=C["muted"], font=FONT_LABEL, width=14); self._l_mode.pack(side="left", padx=6, pady=3)

        body = tk.Frame(self, bg=C["panel"]); body.pack(fill="both", expand=True)
        _, L = scrolled_frame(body, width=315)
        R = tk.Frame(body, bg=C["panel"]); R.pack(side="left", fill="both", expand=True, padx=4, pady=4)

        mode_f = mk_lf(L, "Protocol Mode", C["accent2"]); mode_f.pack(fill="x", padx=6, pady=(6,2))
        self._proto_var = tk.StringVar(value="Adaptive (Pulse → Sweep fallback)")
        for txt, val in [
            ("Standard Pulse (fast)",                    "pulse"),
            ("Pulse I-V Extraction (analog reliable)",   "pulse_iv"),
            ("Adaptive (Pulse → Sweep fallback)",        "adaptive"),
        ]:
            tk.Radiobutton(mode_f, text=txt, variable=self._proto_var, value=val,
                           bg=C["panel"], fg=C["text2"], font=FONT_SMALL,
                           selectcolor=C["entry"], activebackground=C["panel"]
                           ).pack(anchor="w", padx=8, pady=1)

        pf = mk_lf(L, "Pulse Parameters (Modes 1 & 3)", C["accent"]); pf.pack(fill="x", padx=6, pady=2)
        pf.columnconfigure(1, weight=1)
        self._p = {}
        for i, (lbl, key, dflt) in enumerate([
            ("V_SET (V)",      "vs",    "2.0"),
            ("V_RST (V)",      "vr",    "-2.0"),
            ("t_SET (s)",      "ts",    "5e-3"),
            ("t_RST (s)",      "tr",    "5e-3"),
            ("Delay (s)",      "delay", "1e-3"),
        ]):
            self._p[key] = tk.StringVar(value=dflt); param_row(pf, i, lbl, self._p[key], label_width=14)

        sf = mk_lf(L, "Sweep Params (Modes 2 & 3 fallback)", C["accent3"]); sf.pack(fill="x", padx=6, pady=2)
        sf.columnconfigure(1, weight=1)
        self._sp = {}
        for i, (lbl, key, dflt) in enumerate([
            ("V_SET_stop (V)",  "sv_set",  "2.0"),
            ("V_RST_stop (V)",  "sv_rst",  "-2.0"),
            ("V_step (V)",      "sv_step", "0.05"),
            ("t_write (s)",     "t_write", "5e-3"),
        ]):
            self._sp[key] = tk.StringVar(value=dflt); param_row(sf, i, lbl, self._sp[key], label_width=17)

        cf = mk_lf(L, "Compliance - Asymmetric", C["accent4"]); cf.pack(fill="x", padx=6, pady=2)
        cf.columnconfigure(1, weight=1)
        self._asym_var = tk.BooleanVar(value=False)
        tk.Checkbutton(cf, text="Asymmetric compliance", variable=self._asym_var,
                       bg=C["panel"], fg=C["accent4"], font=FONT_SMALL,
                       selectcolor=C["entry"], activebackground=C["panel"]).pack(anchor="w", padx=6, pady=2)
        self._p["comp_p"] = tk.StringVar(value="0.1")
        self._p["comp_n"] = tk.StringVar(value="0.1")
        for lbl, key, fg_ in [("I_comp+ (A)", "comp_p", C["text2"]), ("I_comp− (A)", "comp_n", C["text2"])]:
            row_ = tk.Frame(cf, bg=C["panel"]); row_.pack(fill="x")
            tk.Label(row_, text=lbl, bg=C["panel"], fg=fg_, font=FONT_LABEL, width=14, anchor="e").pack(side="left", padx=(8,4))
            mk_entry(row_, textvariable=self._p[key], width=12).pack(side="left")

        rf = mk_lf(L, "Read & Cycling", C["accent"]); rf.pack(fill="x", padx=6, pady=2)
        rf.columnconfigure(1, weight=1)
        for i, (lbl, key, dflt) in enumerate([
            ("V_read (V)",    "vread",  "0.1"),
            ("t_read (s)",    "tread",  "5e-3"),
            ("N cycles/run",  "cyc",    "1000"),
            ("NPLC",          "nplc",   "0.1"),
            ("N Runs",        "nrun",   "1"),
        ]):
            self._p[key] = tk.StringVar(value=dflt); param_row(rf, i, lbl, self._p[key], label_width=14)

        af = mk_lf(L, "Adaptive Threshold", C["accent2"]); af.pack(fill="x", padx=6, pady=2)
        af.columnconfigure(1, weight=1)
        self._p["oo_thresh"] = tk.StringVar(value="2.0")
        param_row(af, 0, "ON/OFF fallback < X", self._p["oo_thresh"], label_width=18)
        tk.Label(af, text="  If ON/OFF drops below threshold,\n  switch to Pulse I-V for that cycle.",
                 bg=C["panel"], fg=C["muted"], font=FONT_SMALL, justify="left").grid(
                 row=1, column=0, columnspan=2, sticky="w", padx=6, pady=2)

        nb_plot = ttk.Notebook(R); nb_plot.pack(fill="both", expand=True)
        self._pf_g   = tk.Frame(nb_plot, bg=C["panel"])
        self._pf_r   = tk.Frame(nb_plot, bg=C["panel"])
        self._pf_oo  = tk.Frame(nb_plot, bg=C["panel"])
        self._pf_pub = tk.Frame(nb_plot, bg=C["panel"])
        self._pf_hst = tk.Frame(nb_plot, bg=C["panel"])
        nb_plot.add(self._pf_g,   text="  G (LRS/HRS)  ")
        nb_plot.add(self._pf_r,   text="  R (LRS/HRS)  ")
        nb_plot.add(self._pf_oo,  text="  ON/OFF Ratio  ")
        nb_plot.add(self._pf_pub, text="  Publication Plot  ")
        nb_plot.add(self._pf_hst, text="  Cycle Histogram  ")

        self._g_plot  = LivePlot(self._pf_g,  "Endurance: Conductance", "Cycle #", "Conductance (S)")
        self._g_plot.pack(fill="both", expand=True)
        self._r_plot  = LivePlot(self._pf_r,  "Endurance: Resistance",  "Cycle #", "Resistance (Ω)", log_y=True)
        self._r_plot.pack(fill="both", expand=True)
        self._oo_plot = LivePlot(self._pf_oo, "ON/OFF Ratio vs Cycle",  "Cycle #", "ON/OFF Ratio",   log_y=True)
        self._oo_plot.pack(fill="both", expand=True)

        if MATPLOTLIB_OK:
            self._pub_fig = mk_figure((9, 4), facecolor="white")
            self._pub_canvas = FigureCanvasTkAgg(self._pub_fig, master=self._pf_pub)
            self._pub_canvas.get_tk_widget().pack(fill="both", expand=True)
            self._hst_fig = mk_figure((9, 4), facecolor="white")
            self._hst_canvas = FigureCanvasTkAgg(self._hst_fig, master=self._pf_hst)
            self._hst_canvas.get_tk_widget().pack(fill="both", expand=True)

    def _run(self):
        drv = self.get_drv()
        if not drv: self.log.log("Not connected.", "err"); return
        vs    = float(self._p["vs"].get());    vr    = float(self._p["vr"].get())
        ts    = float(self._p["ts"].get());    tr    = float(self._p["tr"].get())
        delay = float(self._p["delay"].get()); vread = float(self._p["vread"].get())
        tread = float(self._p["tread"].get()); cyc   = int(self._p["cyc"].get())
        nplc  = float(self._p["nplc"].get()); runs  = int(self._p["nrun"].get())
        comp_p= float(self._p["comp_p"].get())
        comp_n= float(self._p["comp_n"].get()) if self._asym_var.get() else None
        proto = self._proto_var.get()
        oo_thresh = float(self._p["oo_thresh"].get())
        sv_set  = float(self._sp["sv_set"].get())
        sv_rst  = float(self._sp["sv_rst"].get())
        sv_step = abs(float(self._sp["sv_step"].get()))
        t_write = float(self._sp["t_write"].get())
        set_p = {"start": 0.0, "stop": sv_set,  "step": sv_step}
        rst_p = {"start": 0.0, "stop": sv_rst, "step": sv_step}

        plot_run_idxs = select_plot_indices(runs)
        plot_cyc_idxs = set(select_plot_indices(cyc))
        self._q = queue.Queue(); self._reps = []
        self._g_plot.clear(); self._r_plot.clear(); self._oo_plot.clear()

        def _task(evt):
            for r in range(1, runs + 1):
                if evt.is_set(): break
                csv_path = init_live_save(self.app_vars, "Endurance", r,
                    ["Cycle", "G_LRS(S)", "G_HRS(S)", "R_LRS(Ohm)", "R_HRS(Ohm)", "ON_OFF", "Protocol"],
                    self.app_state.get("seq_folder"))
                cyc_buf = {"c":[],"gl":[],"gh":[],"rl":[],"rh":[],"oo":[],"mode":[]}
                last_oo = 999.0

                for cyc_n in range(1, cyc + 1):
                    if evt.is_set(): break
                    used_mode = "pulse"

                    if proto == "pulse" or (proto == "adaptive" and last_oo >= oo_thresh):
                        G_lrs, G_hrs = 0.0, 0.0
                        drv.configure_voltage_source(comp_p, nplc, 0.0, comp_neg=comp_n)
                        drv.set_compliance(vs); drv.set_voltage(vs); drv.output_on()
                        time.sleep(clamp_pulse(ts)); drv.set_voltage(0.0); drv.output_off()
                        time.sleep(max(delay, MIN_DELAY_S))
                        I_l = drv.read_pulse(vread, tread); time.sleep(max(delay, MIN_DELAY_S))
                        G_lrs = abs(I_l / vread) if abs(vread) > 1e-9 else 0.0
                        drv.set_compliance(vr); drv.set_voltage(vr); drv.output_on()
                        time.sleep(clamp_pulse(tr)); drv.set_voltage(0.0); drv.output_off()
                        time.sleep(max(delay, MIN_DELAY_S))
                        I_h = drv.read_pulse(vread, tread); time.sleep(max(delay, MIN_DELAY_S))
                        G_hrs = abs(I_h / vread) if abs(vread) > 1e-9 else 0.0
                        used_mode = "pulse"
                    else:
                        res = drv.run_endurance_from_pulse_iv(
                            set_p, rst_p, t_write, tread, vread, delay, 1,
                            comp_p, nplc, None, evt, comp_neg=comp_n)
                        G_lrs = res["G_LRS"][0] if res["G_LRS"] else 0.0
                        G_hrs = res["G_HRS"][0] if res["G_HRS"] else 0.0
                        used_mode = "pulse_iv"

                    if proto == "pulse_iv":
                        res = drv.run_endurance_from_pulse_iv(
                            set_p, rst_p, t_write, tread, vread, delay, 1,
                            comp_p, nplc, None, evt, comp_neg=comp_n)
                        G_lrs = res["G_LRS"][0] if res["G_LRS"] else 0.0
                        G_hrs = res["G_HRS"][0] if res["G_HRS"] else 0.0
                        used_mode = "pulse_iv"

                    R_lrs = 1.0 / G_lrs if G_lrs > 1e-15 else 1e15
                    R_hrs = 1.0 / G_hrs if G_hrs > 1e-15 else 1e15
                    oo    = G_lrs / G_hrs if G_hrs > 1e-15 else 1.0
                    last_oo = oo
                    pct   = cyc_n / cyc * 100.0
                    append_live_save(csv_path, [cyc_n, G_lrs, G_hrs, R_lrs, R_hrs, oo, used_mode])
                    if cyc_n in plot_cyc_idxs:
                        cyc_buf["c"].append(cyc_n); cyc_buf["gl"].append(G_lrs)
                        cyc_buf["gh"].append(G_hrs); cyc_buf["rl"].append(R_lrs)
                        cyc_buf["rh"].append(R_hrs); cyc_buf["oo"].append(oo)
                        cyc_buf["mode"].append(used_mode)
                    self._q.put({"type":"prog","pct":pct,"cyc_n":cyc_n,"gl":G_lrs,
                                 "gh":G_hrs,"oo":oo,"mode":used_mode,"buf":dict(cyc_buf)})
                self._q.put({"type":"run_done","run":r,"buf":dict(cyc_buf),"plot":r in plot_run_idxs})
            return True

        self._acq = AcqThread(_task, self._q)
        self._acq.start(); self._run_btn.config(state="disabled")
        self.status.busy("Endurance running…"); self._poll()

    def _poll(self):
        try:
            while True:
                msg = self._q.get_nowait()
                mtype = msg.get("type","")
                if mtype == "prog":
                    self._prog.set(msg["pct"])
                    self._l_cyc.config(text=f"Cyc: {msg['cyc_n']}")
                    self._l_lrs.config(text=f"G_LRS: {msg['gl']:.3E}")
                    self._l_hrs.config(text=f"G_HRS: {msg['gh']:.3E}")
                    self._l_oo.config(text=f"ON/OFF: {msg['oo']:.2f}")
                    self._l_mode.config(text=f"Mode: {msg['mode']}")
                elif mtype == "run_done":
                    buf  = msg["buf"]; cidx = msg["run"] - 1
                    self._reps.append(msg)
                    if msg["plot"] and buf["c"]:
                        self._g_plot.add_trace(buf["c"], buf["gl"], label=f"G_LRS R{msg['run']}", color_idx=cidx,   marker="o", lw=0.8)
                        self._g_plot.add_trace(buf["c"], buf["gh"], label=f"G_HRS R{msg['run']}", color_idx=cidx+7, marker="s", ls="--", lw=0.8)
                        self._r_plot.add_trace(buf["c"], buf["rl"], label=f"R_LRS R{msg['run']}", color_idx=cidx,   marker="o", lw=0.8)
                        self._r_plot.add_trace(buf["c"], buf["rh"], label=f"R_HRS R{msg['run']}", color_idx=cidx+7, marker="s", ls="--", lw=0.8)
                        self._oo_plot.add_trace(buf["c"], buf["oo"], label=f"ON/OFF R{msg['run']}", color_idx=cidx, marker="D", lw=1.0)
                        self._draw_pub_plot(buf, msg["run"])
                        self._draw_histogram(buf, msg["run"])
                    auto_save_fig(self.app_vars, "Endurance_G", msg["run"], self._g_plot.fig, self.app_state.get("seq_folder"))
                    if MATPLOTLIB_OK:
                        auto_save_fig(self.app_vars, "Endurance_Pub", msg["run"], self._pub_fig, self.app_state.get("seq_folder"))
                elif mtype == "done":
                    self._prog.set(100,"Done"); self._run_btn.config(state="normal")
                    self.status.ok("Endurance complete."); self.finish_run(); return
                elif mtype == "error":
                    self.log.log(f"Endurance error: {msg['message']}", "err")
                    self._prog.reset(); self._run_btn.config(state="normal"); return
        except queue.Empty: pass
        self.start_poll(self._poll)

    def _draw_pub_plot(self, buf, run_idx):
        if not MATPLOTLIB_OK or not buf["c"]: return
        self._pub_fig.clear()
        ax1 = self._pub_fig.add_subplot(121)
        ax2 = self._pub_fig.add_subplot(122)
        cycs = buf["c"]
        ax1.semilogy(cycs, buf["gl"], "o", color=PUB_COLORS[0], ms=4, lw=1.2, markerfacecolor="white",
                     markeredgewidth=1.2, label="LRS")
        ax1.semilogy(cycs, buf["gh"], "s", color=PUB_COLORS[1], ms=4, lw=1.2, markerfacecolor="white",
                     markeredgewidth=1.2, label="HRS", linestyle="--")
        ax1.set_xlabel("Cycle Number", fontsize=10); ax1.set_ylabel("Conductance (S)", fontsize=10)
        ax1.set_title(f"Endurance Run {run_idx}", fontsize=10, fontweight="bold")
        ax1.legend(fontsize=8, framealpha=0.9); ax1.grid(True, which="both", alpha=0.3, linestyle=":")
        ax1.tick_params(which="both", direction="in", top=True, right=True); ax1.set_facecolor("white")
        ax2.semilogy(cycs, buf["oo"], "D", color=PUB_COLORS[2], ms=4, lw=1.2)
        ax2.axhline(1.0, color="gray", lw=0.8, ls=":")
        ax2.set_xlabel("Cycle Number", fontsize=10); ax2.set_ylabel("ON/OFF Ratio", fontsize=10)
        ax2.set_title("ON/OFF vs Cycle", fontsize=10, fontweight="bold")
        ax2.grid(True, which="both", alpha=0.3, linestyle=":")
        ax2.tick_params(which="both", direction="in", top=True, right=True); ax2.set_facecolor("white")
        self._pub_fig.suptitle(f"Endurance Characterization - Run {run_idx}", fontsize=11, fontweight="bold")
        self._pub_fig.tight_layout(pad=1.5)
        self._pub_canvas.draw_idle()

    def _draw_histogram(self, buf, run_idx):
        if not MATPLOTLIB_OK or not buf["gl"]: return
        self._hst_fig.clear()
        axes = self._hst_fig.subplots(1, 3)
        for ax, data, label, color in [
            (axes[0], buf["gl"], "G_LRS (S)",   PUB_COLORS[0]),
            (axes[1], buf["gh"], "G_HRS (S)",   PUB_COLORS[1]),
            (axes[2], buf["oo"], "ON/OFF Ratio", PUB_COLORS[2]),
        ]:
            if not data: continue
            n_bins = max(5, len(data)//4)
            ax.hist(data, bins=n_bins, color=color, edgecolor="white", alpha=0.85, linewidth=0.5)
            if len(data) > 1:
                mu = sum(data)/len(data)
                import statistics as _st
                sig = _st.stdev(data)
                ax.axvline(mu, color="black", lw=1.5, ls="--", label=f"μ={mu:.3g}")
                ax.axvline(mu+sig, color="gray", lw=1.0, ls=":", label=f"σ={sig:.3g}")
                ax.axvline(mu-sig, color="gray", lw=1.0, ls=":")
                ax.legend(fontsize=7)
            ax.set_xlabel(label, fontsize=9); ax.set_ylabel("Count", fontsize=9)
            ax.set_title(label, fontsize=9, fontweight="bold")
            ax.tick_params(which="both", direction="in", top=True, right=True, labelsize=8)
            ax.set_facecolor("white")
        self._hst_fig.suptitle(f"Cycle-to-Cycle Distributions - Run {run_idx}", fontsize=11, fontweight="bold")
        self._hst_fig.tight_layout(pad=1.5)
        self._hst_canvas.draw_idle()

    def _show_stats(self):
        if not self._reps: messagebox.showinfo("Stats","No data yet."); return
        import statistics as _st
        lines = ["=== ENDURANCE STATISTICS ===\n"]
        for msg in self._reps:
            buf = msg["buf"]
            lines.append(f"Run {msg['run']}  Cycles plotted: {len(buf['c'])}")
            for lbl, vals in [("G_LRS(S)", buf["gl"]), ("G_HRS(S)", buf["gh"]), ("ON/OFF", buf["oo"])]:
                if vals:
                    mu = sum(vals)/len(vals); sig = _st.stdev(vals) if len(vals)>1 else 0.0
                    lines.append(f"  {lbl}: μ={mu:.4g}  σ={sig:.4g}  CV={100*sig/mu if mu else 0:.1f}%  min={min(vals):.4g}  max={max(vals):.4g}")
            lines.append("")
        win = tk.Toplevel(self); win.title("Endurance Statistics"); win.configure(bg=C["panel"])
        txt = scrolledtext.ScrolledText(win, bg=C["entry"], fg=C["text"], relief="flat", highlightthickness=1, highlightbackground=C["border"], font=FONT_MONO, width=65, height=24)
        txt.pack(fill="both", expand=True, padx=8, pady=8)
        txt.insert("end", "\n".join(lines))

    def _export(self):
        if not self._reps: return
        def _z(w):
            for msg in self._reps:
                buf = msg["buf"]
                for c, gl, gh, rl, rh, oo, md in zip(buf["c"],buf["gl"],buf["gh"],buf["rl"],buf["rh"],buf["oo"],buf["mode"]):
                    w.writerow([msg["run"],c,gl,gh,rl,rh,oo,md])
        self.manual_export("Endurance",["Cycle","G_LRS(S)","G_HRS(S)","R_LRS(Ohm)","R_HRS(Ohm)","ON_OFF","Protocol"], _z)

class RetentionTab(BaseMeasurementTab):
    def __init__(self, parent, get_driver, log, status, app_vars, app_state):
        super().__init__(parent, get_driver, log, status, app_vars, app_state)
        self._states = []; self._s_reps = []; self._build()

    def _build(self):
        nb = ttk.Notebook(self); nb.pack(fill="both", expand=True, padx=4, pady=4)
        self._t_single = tk.Frame(nb, bg=C["panel"])
        self._t_hrs_lrs = tk.Frame(nb, bg=C["panel"])
        self._t_multi  = tk.Frame(nb, bg=C["panel"])
        nb.add(self._t_single,  text="  Single State  ")
        nb.add(self._t_hrs_lrs, text="  HRS + LRS Adaptive  ")
        nb.add(self._t_multi,   text="  Multi-State  ")
        self._bld_single(); self._bld_hrs_lrs(); self._bld_multi()

    def _bld_single(self):
        p = self._t_single
        strip = tk.Frame(p, bg=C["strip"]); strip.pack(fill="x")
        tk.Frame(p, bg=C["border"], height=1).pack(fill="x")
        tk.Label(strip, text="  RETENTION - Single State", bg=C["strip"], fg=C["text"], font=FONT_HEADER).pack(side="left", padx=10, pady=6)
        self._s_run_btn = mk_button(strip, "▶ RUN", self._run_single, color=C["btn"], fg=C["accent3"], font=FONT_HEADER); self._s_run_btn.pack(side="left", padx=4, pady=4)
        mk_button(strip, "■ STOP", self.safe_stop, color=C["btn"], fg=C["red"]).pack(side="left", padx=4)
        mk_button(strip, "CSV", self._export_single, color=C["btn"]).pack(side="left", padx=4)

        self._s_prog = ProgressBar(p); self._s_prog.pack(fill="x")
        body = tk.Frame(p, bg=C["panel"]); body.pack(fill="both", expand=True)
        _, L = scrolled_frame(body, width=295)
        R = tk.Frame(body, bg=C["panel"]); R.pack(side="left", fill="both", expand=True, padx=4, pady=4)

        prog_f = mk_lf(L, "Programming Pulse (optional)", C["accent4"]); prog_f.pack(fill="x", padx=6, pady=4); prog_f.columnconfigure(1, weight=1)
        self._sp = {}
        for i, (lbl, key, dflt) in enumerate([("V_prog (V)", "v_prog", "1.5"), ("t_prog (s)", "t_prog", "5e-3"), ("N_prog pulses", "n_prog", "0")]):
            self._sp[key] = tk.StringVar(value=dflt); param_row(prog_f, i, lbl, self._sp[key], label_width=16)

        ret_f = mk_lf(L, "Retention Parameters", C["accent3"]); ret_f.pack(fill="x", padx=6, pady=4); ret_f.columnconfigure(1, weight=1)
        self._rp = {}
        for i, (lbl, key, dflt) in enumerate([
            ("Total time (s)",   "total_t",  "3600"),
            ("Read interval (s)","interval", "10"),
            ("V_read (V)",       "v_read",   "0.1"),
            ("t_read (s)",       "t_read",   "5e-3"),
            ("Compliance (A)",   "comp",     "0.01"),
            ("NPLC",             "nplc",     "1.0"),
            ("N Runs",           "n_runs",   "1"),
        ]):
            self._rp[key] = tk.StringVar(value=dflt); param_row(ret_f, i, lbl, self._rp[key], label_width=16)
        self._n_runs_var = self._rp["n_runs"]

        self._s_plot = LivePlot(R, "Retention: G vs Time", "Time (s)", "Conductance (S)")
        self._s_plot.pack(fill="both", expand=True)

    def _run_single(self):
        drv = self.get_drv()
        if not drv: self.log.log("Not connected.", "err"); return
        runs = int(self._rp["n_runs"].get()); v_read = float(self._rp["v_read"].get())
        t_read = float(self._rp["t_read"].get()); total_t = float(self._rp["total_t"].get())
        interv = float(self._rp["interval"].get()); comp = float(self._rp["comp"].get())
        nplc = float(self._rp["nplc"].get())
        v_prog = float(self._sp["v_prog"].get()); t_prog = float(self._sp["t_prog"].get())
        n_prog = int(self._sp["n_prog"].get())
        plot_idxs = select_plot_indices(runs)
        self._q = queue.Queue(); self._s_reps = []; self._s_plot.clear()

        def _task(evt):
            for r in range(1, runs + 1):
                if evt.is_set(): break
                if n_prog > 0:
                    drv.configure_voltage_source(comp, nplc, 0.0)
                    for _ in range(n_prog):
                        if evt.is_set(): break
                        drv.set_voltage(v_prog); drv.output_on(); time.sleep(clamp_pulse(t_prog))
                        drv.set_voltage(0.0); drv.output_off()
                csv_path = init_live_save(self.app_vars, "Retention_Single", r,
                    ["Time(s)", "G(S)", "R(Ohm)"], self.app_state.get("seq_folder"))
                def _prog(pct, row, refs, _c=csv_path):
                    append_live_save(_c, [row[0], row[1], row[2]])
                    self._q.put({"type": "prog", "pct": pct})
                res = drv.run_retention(v_read, t_read, total_t, interv, comp, nplc, _prog, evt)
                self._q.put({"type": "run_done", "run": r, "data": res, "plot": r in plot_idxs})
            return True

        self._acq = AcqThread(_task, self._q); self._acq.start()
        self._s_run_btn.config(state="disabled"); self.status.busy("Retention running…"); self._s_poll()

    def _s_poll(self):
        try:
            while True:
                msg = self._q.get_nowait()
                if msg["type"] == "prog": self._s_prog.set(msg["pct"])
                elif msg["type"] == "run_done":
                    self._s_reps.append(msg)
                    if msg["plot"]:
                        d = msg["data"]
                        self._s_plot.add_trace(d["time"], d["conductances"], label=f"Run {msg['run']}", color_idx=msg["run"]-1)
                    auto_save_fig(self.app_vars, "Retention_Single", msg["run"], self._s_plot.fig, self.app_state.get("seq_folder"))
                elif msg["type"] == "done":
                    self._s_prog.set(100,"Done"); self._s_run_btn.config(state="normal")
                    self.status.ok("Retention complete."); self.finish_run(); return
                elif msg["type"] == "error":
                    self.log.log(f"Retention error: {msg['message']}", "err")
                    self._s_prog.reset(); self._s_run_btn.config(state="normal"); return
        except queue.Empty: pass
        if self._acq and self._acq.is_alive(): self.after(100, self._s_poll)

    def _export_single(self):
        if not self._s_reps: return
        def _z(w):
            for item in self._s_reps:
                for t, g, r in zip(item["data"]["time"], item["data"]["conductances"], item["data"]["resistances"]):
                    w.writerow([item["run"], t, g, r])
        self.manual_export("Retention_Single", ["Time(s)", "G(S)", "R(Ohm)"], _z)

    def _bld_hrs_lrs(self):
        p = self._t_hrs_lrs
        strip = tk.Frame(p, bg=C["strip"]); strip.pack(fill="x")
        tk.Frame(p, bg=C["border"], height=1).pack(fill="x")
        tk.Label(strip, text="  ADAPTIVE HRS + LRS RETENTION - Pulse → Triangular Fallback",
                 bg=C["strip"], fg=C["accent3"], font=FONT_HEADER).pack(side="left", padx=10, pady=6)
        self._hl_run_btn = mk_button(strip, "▶ RUN", self._run_hrs_lrs, color=C["btn"],
                                     fg=C["accent3"], font=FONT_HEADER)
        self._hl_run_btn.pack(side="left", padx=4, pady=4)
        mk_button(strip, "■ STOP", self.safe_stop, color=C["btn"], fg=C["red"]).pack(side="left", padx=4)
        mk_button(strip, "KWW Fit", self._kww_fit, color=C["btn"]).pack(side="left", padx=4)
        mk_button(strip, "CSV", self._export_hl, color=C["btn"]).pack(side="left", padx=4)

        self._hl_prog = ProgressBar(p); self._hl_prog.pack(fill="x")
        live = tk.Frame(p, bg=C["panel2"]); live.pack(fill="x")
        self._hl_lstate = tk.Label(live, text="LRS prog: ---", bg=C["panel2"], fg=C["accent3"], font=FONT_BIG, width=18); self._hl_lstate.pack(side="left", padx=8, pady=3)
        self._hl_hstate = tk.Label(live, text="HRS prog: ---", bg=C["panel2"], fg=C["accent4"], font=FONT_BIG, width=18); self._hl_hstate.pack(side="left", padx=8, pady=3)
        self._hl_method = tk.Label(live, text="Method: ---", bg=C["panel2"], fg=C["muted"], font=FONT_LABEL, width=16); self._hl_method.pack(side="left", padx=8, pady=3)

        body = tk.Frame(p, bg=C["panel"]); body.pack(fill="both", expand=True)
        _, L = scrolled_frame(body, width=320)
        R = tk.Frame(body, bg=C["panel"]); R.pack(side="left", fill="both", expand=True, padx=4, pady=4)

        lrs_f = mk_lf(L, "LRS Programming (SET)", C["accent3"]); lrs_f.pack(fill="x", padx=6, pady=(6,2)); lrs_f.columnconfigure(1, weight=1)
        self._lrs = {}
        for i, (lbl, key, dflt) in enumerate([
            ("V_SET (V)",       "v_set",   "2.0"),
            ("t_SET (s)",       "t_set",   "5e-3"),
            ("G_LRS min (S)",   "g_min",   "1e-5"),
            ("G_LRS max (S)",   "g_max",   "1e-2"),
            ("Max tries",       "tries",   "5"),
        ]):
            self._lrs[key] = tk.StringVar(value=dflt); param_row(lrs_f, i, lbl, self._lrs[key], label_width=16)

        hrs_f = mk_lf(L, "HRS Programming (RESET)", C["accent4"]); hrs_f.pack(fill="x", padx=6, pady=2); hrs_f.columnconfigure(1, weight=1)
        self._hrs = {}
        for i, (lbl, key, dflt) in enumerate([
            ("V_RST (V)",       "v_rst",   "-2.0"),
            ("t_RST (s)",       "t_rst",   "5e-3"),
            ("G_HRS min (S)",   "g_min",   "1e-8"),
            ("G_HRS max (S)",   "g_max",   "1e-5"),
            ("Max tries",       "tries",   "5"),
        ]):
            self._hrs[key] = tk.StringVar(value=dflt); param_row(hrs_f, i, lbl, self._hrs[key], label_width=16)

        tri_f = mk_lf(L, "Triangular Fallback Sweep", C["accent2"]); tri_f.pack(fill="x", padx=6, pady=2); tri_f.columnconfigure(1, weight=1)
        self._tri = {}
        for i, (lbl, key, dflt) in enumerate([
            ("V_pos (V)",       "v_pos",   "2.0"),
            ("V_neg (V)",       "v_neg",   "-2.0"),
            ("V_step (V)",      "v_step",  "0.05"),
        ]):
            self._tri[key] = tk.StringVar(value=dflt); param_row(tri_f, i, lbl, self._tri[key], label_width=16)

        cf2 = mk_lf(L, "Compliance (Asymmetric)", C["accent4"]); cf2.pack(fill="x", padx=6, pady=2); cf2.columnconfigure(1, weight=1)
        self._hl_asym = tk.BooleanVar(value=False)
        tk.Checkbutton(cf2, text="Asymmetric", variable=self._hl_asym,
                       bg=C["panel"], fg=C["accent4"], font=FONT_SMALL,
                       selectcolor=C["entry"], activebackground=C["panel"]).pack(anchor="w", padx=6, pady=2)
        self._hl_comp = {}
        for lbl, key, dflt, fg_ in [("I_comp+ (A)","cp","0.1",C["text2"]),("I_comp− (A)","cn","0.1",C["text2"])]:
            row_ = tk.Frame(cf2, bg=C["panel"]); row_.pack(fill="x")
            tk.Label(row_, text=lbl, bg=C["panel"], fg=fg_, font=FONT_LABEL, width=12, anchor="e").pack(side="left", padx=(8,4))
            self._hl_comp[key] = tk.StringVar(value=dflt)
            mk_entry(row_, textvariable=self._hl_comp[key], width=12).pack(side="left")

        rr_f = mk_lf(L, "Retention Read", C["accent"]); rr_f.pack(fill="x", padx=6, pady=2); rr_f.columnconfigure(1, weight=1)
        self._hl_rp = {}
        for i, (lbl, key, dflt) in enumerate([
            ("Total time (s)",   "total_t",  "3600"),
            ("Read interval (s)","interval", "10"),
            ("V_read (V)",       "v_read",   "0.1"),
            ("t_read (s)",       "t_read",   "5e-3"),
            ("NPLC",             "nplc",     "1.0"),
            ("N Repeats",        "nrep",     "1"),
        ]):
            self._hl_rp[key] = tk.StringVar(value=dflt); param_row(rr_f, i, lbl, self._hl_rp[key], label_width=16)

        nb_p = ttk.Notebook(R); nb_p.pack(fill="both", expand=True)
        self._pf_hl_g   = tk.Frame(nb_p, bg=C["panel"])
        self._pf_hl_pub = tk.Frame(nb_p, bg=C["panel"])
        nb_p.add(self._pf_hl_g,   text="  G(t) HRS + LRS  ")
        nb_p.add(self._pf_hl_pub, text="  Publication  ")

        self._hl_g_plot = LivePlot(self._pf_hl_g, "HRS & LRS Retention", "Time (s)", "Conductance (S)", log_y=True)
        self._hl_g_plot.pack(fill="both", expand=True)

        if MATPLOTLIB_OK:
            self._hl_pub_fig = mk_figure((9, 4.5), facecolor="white")
            self._hl_pub_canvas = FigureCanvasTkAgg(self._hl_pub_fig, master=self._pf_hl_pub)
            self._hl_pub_canvas.get_tk_widget().pack(fill="both", expand=True)

        self._hl_data = []

    def _run_hrs_lrs(self):
        drv = self.get_drv()
        if not drv: self.log.log("Not connected.", "err"); return
        v_set  = float(self._lrs["v_set"].get());  t_set  = float(self._lrs["t_set"].get())
        g_lrs_min = float(self._lrs["g_min"].get()); g_lrs_max = float(self._lrs["g_max"].get())
        lrs_tries = int(self._lrs["tries"].get())
        v_rst  = float(self._hrs["v_rst"].get());  t_rst  = float(self._hrs["t_rst"].get())
        g_hrs_min = float(self._hrs["g_min"].get()); g_hrs_max = float(self._hrs["g_max"].get())
        hrs_tries = int(self._hrs["tries"].get())
        v_pos  = float(self._tri["v_pos"].get());  v_neg  = float(self._tri["v_neg"].get())
        v_step = abs(float(self._tri["v_step"].get()))
        comp_p = float(self._hl_comp["cp"].get())
        comp_n = float(self._hl_comp["cn"].get()) if self._hl_asym.get() else None
        total_t = float(self._hl_rp["total_t"].get()); interv = float(self._hl_rp["interval"].get())
        v_read  = float(self._hl_rp["v_read"].get()); t_read = float(self._hl_rp["t_read"].get())
        nplc    = float(self._hl_rp["nplc"].get()); nrep = int(self._hl_rp["nrep"].get())

        self._q = queue.Queue(); self._hl_data = []
        self._hl_g_plot.clear()
        if MATPLOTLIB_OK: self._hl_pub_fig.clear()

        def _program_state(state_name, v_prog, t_prog, g_min, g_max, max_tries):
            method = "pulse"
            G_actual, ok = drv.set_state_by_pulse(v_prog, t_prog, v_read, t_read, 0.001,
                                                    comp_p, nplc, g_min, g_max, max_tries)
            if not ok:
                self._q.put({"type":"log","msg":f"{state_name}: pulse failed (G={G_actual:.3e}), trying triangular sweep…","tag":"warn"})
                method = "triangular"
                G_actual, _ = drv.set_state_by_triangular(v_pos, v_neg, v_step, v_read, t_read,
                                                            0.001, comp_p, nplc, None, threading.Event(), comp_neg=comp_n)
            self._q.put({"type":"state_set","state":state_name,"G":G_actual,"method":method})
            return G_actual, method

        def _task(evt):
            for rep in range(1, nrep + 1):
                if evt.is_set(): break
                G_lrs, m_lrs = _program_state("LRS", v_set, t_set, g_lrs_min, g_lrs_max, lrs_tries)
                self._q.put({"type":"lrs_ready","G":G_lrs,"method":m_lrs})
                csv_lrs = init_live_save(self.app_vars, "Retention_LRS", rep,
                    ["Time(s)","G_LRS(S)","R_LRS(Ohm)"], self.app_state.get("seq_folder"))
                t_data_lrs = []; g_data_lrs = []
                t0 = time.time(); n_reads = max(int(total_t / interv), 1)
                for k in range(n_reads):
                    if evt.is_set(): break
                    wait = t0 + k * interv - time.time()
                    if wait > 0.005: time.sleep(wait - 0.004)
                    I = drv.read_pulse(v_read, t_read)
                    t_now = time.time() - t0
                    G = abs(I / v_read) if abs(v_read) > 1e-9 else 0.0
                    R = 1.0 / G if G > 1e-15 else 1e15
                    t_data_lrs.append(t_now); g_data_lrs.append(G)
                    append_live_save(csv_lrs, [t_now, G, R])
                    pct = (k + 1) / n_reads / 2 * 100
                    self._q.put({"type":"ret_point","state":"LRS","t":t_now,"G":G,"pct":pct,"rep":rep})

                G_hrs, m_hrs = _program_state("HRS", v_rst, t_rst, g_hrs_min, g_hrs_max, hrs_tries)
                self._q.put({"type":"hrs_ready","G":G_hrs,"method":m_hrs})
                csv_hrs = init_live_save(self.app_vars, "Retention_HRS", rep,
                    ["Time(s)","G_HRS(S)","R_HRS(Ohm)"], self.app_state.get("seq_folder"))
                t_data_hrs = []; g_data_hrs = []
                t0 = time.time()
                for k in range(n_reads):
                    if evt.is_set(): break
                    wait = t0 + k * interv - time.time()
                    if wait > 0.005: time.sleep(wait - 0.004)
                    I = drv.read_pulse(v_read, t_read)
                    t_now = time.time() - t0
                    G = abs(I / v_read) if abs(v_read) > 1e-9 else 0.0
                    R = 1.0 / G if G > 1e-15 else 1e15
                    t_data_hrs.append(t_now); g_data_hrs.append(G)
                    append_live_save(csv_hrs, [t_now, G, R])
                    pct = 50 + (k + 1) / n_reads / 2 * 100
                    self._q.put({"type":"ret_point","state":"HRS","t":t_now,"G":G,"pct":pct,"rep":rep})

                self._q.put({"type":"rep_done","rep":rep,
                             "t_lrs":t_data_lrs,"g_lrs":g_data_lrs,"m_lrs":m_lrs,
                             "t_hrs":t_data_hrs,"g_hrs":g_data_hrs,"m_hrs":m_hrs})
            return True

        self._acq = AcqThread(_task, self._q)
        self._acq.start(); self._hl_run_btn.config(state="disabled")
        self.status.busy("Adaptive HRS/LRS retention running…"); self._hl_poll()

    def _hl_poll(self):
        try:
            while True:
                msg = self._q.get_nowait()
                mtype = msg.get("type","")
                if mtype == "prog":   self._hl_prog.set(msg["pct"])
                elif mtype == "log":  self.log.log(msg["msg"], msg.get("tag","info"))
                elif mtype == "state_set":
                    st = msg["state"]; G = msg["G"]; meth = msg["method"]
                    self.log.log(f"{st} set: G={G:.3e}S via {meth}", "ok")
                elif mtype == "lrs_ready":
                    self._hl_lstate.config(text=f"LRS: {msg['G']:.3e}S [{msg['method']}]")
                elif mtype == "hrs_ready":
                    self._hl_hstate.config(text=f"HRS: {msg['G']:.3e}S [{msg['method']}]")
                elif mtype == "ret_point":
                    self._hl_prog.set(msg["pct"])
                elif mtype == "rep_done":
                    rep = msg["rep"]
                    self._hl_data.append(msg)
                    self._hl_g_plot.add_trace(msg["t_lrs"], msg["g_lrs"],
                                              label=f"LRS R{rep} [{msg['m_lrs']}]", color_idx=0, lw=1.5)
                    self._hl_g_plot.add_trace(msg["t_hrs"], msg["g_hrs"],
                                              label=f"HRS R{rep} [{msg['m_hrs']}]", color_idx=1, ls="--", lw=1.5)
                    if MATPLOTLIB_OK: self._draw_hl_pub(msg, rep)
                    auto_save_fig(self.app_vars, "Retention_HRS_LRS", rep, self._hl_g_plot.fig, self.app_state.get("seq_folder"))
                elif mtype == "done":
                    self._hl_prog.set(100,"Done"); self._hl_run_btn.config(state="normal")
                    self.status.ok("Adaptive retention complete."); self.finish_run(); return
                elif mtype == "error":
                    self.log.log(f"Retention error: {msg['message']}", "err")
                    self._hl_prog.reset(); self._hl_run_btn.config(state="normal"); return
        except queue.Empty: pass
        if self._acq and self._acq.is_alive(): self.after(200, self._hl_poll)

    def _draw_hl_pub(self, msg, rep):
        if not MATPLOTLIB_OK: return
        self._hl_pub_fig.clear()
        ax = self._hl_pub_fig.add_subplot(111)
        t_lrs = msg["t_lrs"]; g_lrs = msg["g_lrs"]
        t_hrs = msg["t_hrs"]; g_hrs = msg["g_hrs"]
        if t_lrs and g_lrs:
            ax.semilogy(t_lrs, g_lrs, "o-", color=PUB_COLORS[0], ms=3, lw=1.2,
                        markerfacecolor="white", markeredgewidth=1.0, label=f"LRS [{msg['m_lrs']}]")
        if t_hrs and g_hrs:
            ax.semilogy(t_hrs, g_hrs, "s--", color=PUB_COLORS[1], ms=3, lw=1.2,
                        markerfacecolor="white", markeredgewidth=1.0, label=f"HRS [{msg['m_hrs']}]")
        ax.axvline(3.15e8, color="gray", lw=0.8, ls=":", alpha=0.6, label="10 yr")
        ax.set_xlabel("Time (s)", fontsize=10); ax.set_ylabel("Conductance (S)", fontsize=10)
        ax.set_title(f"HRS & LRS Retention - Run {rep}", fontsize=10, fontweight="bold")
        ax.legend(fontsize=8, framealpha=0.9); ax.grid(True, which="both", alpha=0.25, linestyle=":")
        ax.tick_params(which="both", direction="in", top=True, right=True); ax.set_facecolor("white")
        self._hl_pub_fig.tight_layout(pad=1.5)
        self._hl_pub_canvas.draw_idle()

    def _kww_fit(self):
        if not self._hl_data: messagebox.showinfo("KWW Fit","No data yet."); return
        if not NUMPY_OK: messagebox.showinfo("KWW Fit","NumPy required."); return
        lines = ["=== KWW STRETCHED EXPONENTIAL FIT ===\nG(t) = G∞ + (G0-G∞)·exp(-(t/τ)^β)\n"]
        for msg in self._hl_data:
            for state, ts, gs in [("LRS", msg["t_lrs"], msg["g_lrs"]),
                                   ("HRS", msg["t_hrs"], msg["g_hrs"])]:
                if len(ts) < 4: continue
                try:
                    t_arr = [max(t, 1e-6) for t in ts]
                    G0 = gs[0]; Ginf = min(gs); G_rng = G0 - Ginf if G0 > Ginf else 1e-20
                    ys = []
                    for g, t in zip(gs, t_arr):
                        ratio = max((g - Ginf) / max(G_rng, 1e-30), 1e-10)
                        if ratio < 1.0: ys.append((math.log(t), math.log(-math.log(ratio))))
                    if len(ys) < 3: continue
                    xs_lf = [y[0] for y in ys]; ys_lf = [y[1] for y in ys]
                    n = len(xs_lf); sx=sum(xs_lf); sy=sum(ys_lf)
                    sxy=sum(x*y for x,y in zip(xs_lf,ys_lf)); sx2=sum(x*x for x in xs_lf)
                    denom=n*sx2-sx*sx
                    if abs(denom)>1e-30:
                        beta=(n*sxy-sx*sy)/denom
                        lntau=-(sy-beta*sx)/n/beta if beta!=0 else 0
                        tau=math.exp(lntau) if abs(lntau)<50 else 1e20
                        lines.append(f"Rep {msg['rep']} {state}: β={beta:.3f}  τ={tau:.3g}s  G0={G0:.3g}  G∞={Ginf:.3g}")
                    else:
                        lines.append(f"Rep {msg['rep']} {state}: Fit failed (degenerate)")
                except Exception as e:
                    lines.append(f"Rep {msg['rep']} {state}: Fit error - {e}")
        win = tk.Toplevel(self); win.title("KWW Fit Results"); win.configure(bg=C["panel"])
        txt = scrolledtext.ScrolledText(win, bg=C["entry"], fg=C["text"], relief="flat", highlightthickness=1, highlightbackground=C["border"], font=FONT_MONO, width=60, height=20)
        txt.pack(fill="both", expand=True, padx=8, pady=8)
        txt.insert("end", "\n".join(lines))

    def _export_hl(self):
        if not self._hl_data: return
        def _z(w):
            for msg in self._hl_data:
                for t, g in zip(msg["t_lrs"], msg["g_lrs"]):
                    w.writerow([msg["rep"], "LRS", t, g, msg["m_lrs"]])
                for t, g in zip(msg["t_hrs"], msg["g_hrs"]):
                    w.writerow([msg["rep"], "HRS", t, g, msg["m_hrs"]])
        self.manual_export("Retention_HRS_LRS", ["Rep","State","Time(s)","G(S)","Method"], _z)

    def _bld_multi(self):
        p = self._t_multi
        strip = tk.Frame(p, bg=C["strip"]); strip.pack(fill="x")
        tk.Frame(p, bg=C["border"], height=1).pack(fill="x")
        tk.Label(strip, text="  RETENTION - Multi-State", bg=C["strip"], fg=C["text"], font=FONT_HEADER).pack(side="left", padx=10, pady=6)
        self._m_run_btn = mk_button(strip, "▶ RUN ALL STATES", self._run_multi, color=C["strip"], fg=C["accent5"], font=FONT_HEADER); self._m_run_btn.pack(side="left", padx=4, pady=4)
        mk_button(strip, "■ STOP", self.safe_stop, color=C["btn"], fg=C["red"]).pack(side="left", padx=4)
        mk_button(strip, "CSV", self._export_multi, color=C["btn"]).pack(side="left", padx=4)

        self._m_prog = ProgressBar(p); self._m_prog.pack(fill="x")
        body = tk.Frame(p, bg=C["panel"]); body.pack(fill="both", expand=True)
        _, L = scrolled_frame(body, width=295)
        R = tk.Frame(body, bg=C["panel"]); R.pack(side="left", fill="both", expand=True, padx=4, pady=4)

        ctls = mk_lf(L, "State List", C["accent5"]); ctls.pack(fill="x", padx=6, pady=4)
        mk_button(ctls, "⊕ Load from LTP", self._load_from_ltp, color=C["btn_act"], fg=C["accent3"]).pack(fill="x", padx=4, pady=2)
        mk_button(ctls, "✕ Clear All", self._clear_states, color=C["btn"], fg=C["red"]).pack(fill="x", padx=4, pady=2)
        self._state_lb = tk.Listbox(ctls, bg=C["entry2"], fg=C["text"], font=FONT_MONO, relief="flat", height=8, highlightthickness=1, highlightbackground=C["border"], selectbackground=C["btn_act"], selectforeground=C["text"]); self._state_lb.pack(fill="x", padx=4, pady=4)

        rf = mk_lf(L, "Retention Parameters", C["accent3"]); rf.pack(fill="x", padx=6, pady=4); rf.columnconfigure(1, weight=1)
        self._mrp = {}
        for i, (lbl, key, dflt) in enumerate([
            ("Total time (s)",   "total_t",  "3600"),
            ("Read interval (s)","interval", "10"),
            ("V_read (V)",       "v_read",   "0.1"),
            ("t_read (s)",       "t_read",   "5e-3"),
            ("Compliance (A)",   "comp",     "0.01"),
            ("NPLC",             "nplc",     "1.0"),
        ]):
            self._mrp[key] = tk.StringVar(value=dflt); param_row(rf, i, lbl, self._mrp[key], label_width=16)

        self._m_plot = LivePlot(R, "Multi-State Retention", "Time (s)", "Conductance (S)", log_y=True)
        self._m_plot.pack(fill="both", expand=True); self._m_reps = []

    def _load_from_ltp(self):
        ltp = self.app_state.get("ltp_last_run")
        if not ltp: messagebox.showinfo("Load from LTP", "No LTP run found. Run LTP/LTD first."); return
        gs, phs = ltp["conductances"], ltp["phases"]
        pot_gs = [g for g, ph in zip(gs, phs) if ph == "POT"]
        if not pot_gs: pot_gs = gs
        idxs, pulses = list(range(len(pot_gs))), ltp.get("voltages", [1.5]*len(gs))
        pot_v = [v for v, ph in zip(pulses, phs) if ph == "POT"]
        v_read, t_read = self.app_state.get("ltp_v_read", 0.1), self.app_state.get("ltp_t_read", 0.005)
        for si, idx in enumerate(idxs):
            self._states.append({"label": f"S{si+1}", "v_prog": pot_v[idx] if pot_v else 1.5, "t_prog": MIN_PULSE_S, "n_pulses": idx+1, "g_expected": pot_gs[idx], "v_read": v_read, "t_read": t_read})
        self._refresh_state_list(); self.log.log(f"Loaded {len(pot_gs)} states from LTP.", "ok")

    def _clear_states(self): self._states.clear(); self._refresh_state_list()
    def _refresh_state_list(self):
        self._state_lb.delete(0, tk.END)
        for s in self._states:
            ge = f"{s['g_expected']:.2e}" if s["g_expected"] else "?"
            self._state_lb.insert(tk.END, f"{s['label']}: V={s['v_prog']:.2f}V  n={s['n_pulses']}  G≈{ge}")

    def _run_multi(self):
        if not self._states: messagebox.showinfo("Multi-State", "Add at least one state first."); return
        drv = self.get_drv()
        if not drv: self.log.log("Not connected.", "err"); return
        total_t, interv, v_read, t_read = float(self._mrp["total_t"].get()), float(self._mrp["interval"].get()), float(self._mrp["v_read"].get()), float(self._mrp["t_read"].get())
        comp, nplc, states = float(self._mrp["comp"].get()), float(self._mrp["nplc"].get()), list(self._states)
        self._q = queue.Queue(); self._m_reps = []; self._m_plot.clear()

        def _task(evt):
            for si, state in enumerate(states):
                if evt.is_set(): break
                drv.configure_voltage_source(comp, nplc, 0.0)
                for _ in range(state["n_pulses"]):
                    if evt.is_set(): break
                    drv.set_voltage(state["v_prog"]); drv.output_on(); time.sleep(clamp_pulse(state["t_prog"])); drv.set_voltage(0.0); drv.output_off()
                csv_path = init_live_save(self.app_vars, "Retention_Multi", si + 1, [f"Time(s)_{state['label']}", f"G(S)_{state['label']}", f"R(Ohm)_{state['label']}"], self.app_state.get("seq_folder"))
                def _prog(pct, row, refs, _c=csv_path, _si=si, _n=len(states)):
                    append_live_save(_c, [row[0], row[1], row[2]])
                    self._q.put({"type": "prog", "pct": (_si + pct / 100) / _n * 100})
                res = drv.run_retention(v_read, t_read, total_t, interv, comp, nplc, _prog, evt)
                self._q.put({"type": "state_done", "state_idx": si, "label": state["label"], "data": res})
            return True

        self._acq = AcqThread(_task, self._q); self._acq.start(); self._m_run_btn.config(state="disabled"); self.status.busy("Multi-state retention running…"); self._m_poll()

    def _m_poll(self):
        try:
            while True:
                msg = self._q.get_nowait()
                if msg["type"] == "prog": self._m_prog.set(msg["pct"])
                elif msg["type"] == "state_done":
                    d = msg["data"]; self._m_reps.append(msg)
                    self._m_plot.add_trace(d["time"], d["conductances"], label=msg["label"], color_idx=msg["state_idx"])
                    auto_save_fig(self.app_vars, "Retention_Multi", msg["state_idx"] + 1, self._m_plot.fig, self.app_state.get("seq_folder"))
                elif msg["type"] == "done":
                    self._m_prog.set(100, "Done"); self._m_run_btn.config(state="normal"); self.status.ok("Multi-state retention complete."); self.finish_run(); return
                elif msg["type"] == "error":
                    self.log.log(f"Multi-state error: {msg['message']}", "err"); self._m_prog.reset(); self._m_run_btn.config(state="normal"); return
        except queue.Empty: pass
        if self._acq and self._acq.is_alive(): self.after(200, self._m_poll)

    def _export_multi(self):
        if not self._m_reps: return
        def _z(w):
            for item in self._m_reps:
                for t, g, r in zip(item["data"]["time"], item["data"]["conductances"], item["data"]["resistances"]): w.writerow([item["label"], t, g, r])
        self.manual_export("Retention_Multi", ["Label", "Time(s)", "G(S)", "R(Ohm)"], _z)

class MLCTab(BaseMeasurementTab):
    def __init__(self, parent, get_driver, log, status, app_vars, app_state):
        super().__init__(parent, get_driver, log, status, app_vars, app_state)
        self._reps = []; self._build()

    def _build(self):
        strip = tk.Frame(self, bg=C["strip"]); strip.pack(fill="x")
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x")
        tk.Label(strip, text="  MLC (Multi-State) - 512+ Levels & Fan Retention", bg=C["strip"], fg=C["text"], font=FONT_HEADER).pack(side="left", padx=10, pady=6)
        self._run_btn = mk_button(strip, "▶ RUN MLC", self._run, color=C["strip"], fg=C["accent5"], font=FONT_HEADER); self._run_btn.pack(side="left", padx=4, pady=4)
        mk_button(strip, "■ STOP", self.safe_stop, color=C["btn"], fg=C["red"]).pack(side="left", padx=4)
        mk_button(strip, "CSV", self._export, color=C["btn"]).pack(side="left", padx=4)
        self._prog = ProgressBar(self); self._prog.pack(fill="x")

        body = tk.Frame(self, bg=C["panel"]); body.pack(fill="both", expand=True)
        _, L = scrolled_frame(body, width=310)
        R = tk.Frame(body, bg=C["panel"]); R.pack(side="left", fill="both", expand=True, padx=4, pady=4)

        pf = mk_lf(L, "MLC Programming", C["accent5"]); pf.pack(fill="x", padx=6, pady=4); pf.columnconfigure(1, weight=1)
        self._p = {}
        for i, (lbl, key, dflt) in enumerate([
            ("V_reset (V)",       "v_rst",   "-2.0"),
            ("t_reset (s)",       "t_rst",   "5e-3"),
            ("V_prog start (V)",  "v_start", "0.5"),
            ("V_prog stop (V)",   "v_stop",  "2.5"),
            ("N States",          "n_state", "64"),
            ("t_prog (s)",        "t_write", "5e-3"),
            ("Delay (s)",         "delay",   "1e-3"),
        ]):
            self._p[key] = var = tk.StringVar(value=dflt); param_row(pf, i, lbl, var, label_width=17)

        rf = mk_lf(L, "Retention per State", C["accent3"]); rf.pack(fill="x", padx=6, pady=4); rf.columnconfigure(1, weight=1)
        for i, (lbl, key, dflt) in enumerate([
            ("Ret. time/state (s)","ret_t",  "10"),
            ("Read interval (s)", "ret_i",  "0.5"),
            ("V_read (V)",        "v_read", "0.1"),
            ("t_read (s)",        "t_read", "5e-3"),
            ("Compliance (A)",    "comp",   "0.1"),
            ("NPLC",              "nplc",   "0.1"),
        ]):
            self._p[key] = var = tk.StringVar(value=dflt); param_row(rf, i, lbl, var, label_width=17)

        nb_plot = ttk.Notebook(R); nb_plot.pack(fill="both", expand=True)
        self._f_states = tk.Frame(nb_plot, bg=C["panel"]); self._f_fan = tk.Frame(nb_plot, bg=C["panel"])
        nb_plot.add(self._f_states, text=" Levels Programmed "); nb_plot.add(self._f_fan, text=" Fan Retention ")

        self._p_states = LivePlot(self._f_states, "MLC Conductance Levels", "State Index", "Conductance (S)")
        self._p_states.pack(fill="both", expand=True)
        self._p_fan = LivePlot(self._f_fan, "MLC Fan Retention", "Time (s)", "Conductance (S)", log_y=True)
        self._p_fan.pack(fill="both", expand=True)

    def _run(self):
        drv = self.get_drv()
        if not drv: self.log.log("Not connected.", "err"); return

        v_rst, t_rst = float(self._p["v_rst"].get()), float(self._p["t_rst"].get())
        v_start, v_stop, n_states = float(self._p["v_start"].get()), float(self._p["v_stop"].get()), int(self._p["n_state"].get())
        t_write, delay = float(self._p["t_write"].get()), float(self._p["delay"].get())
        ret_t, ret_i = float(self._p["ret_t"].get()), float(self._p["ret_i"].get())
        v_read, t_read = float(self._p["v_read"].get()), float(self._p["t_read"].get())
        comp, nplc = float(self._p["comp"].get()), float(self._p["nplc"].get())

        self._q = queue.Queue(); self._reps = []; self._p_states.clear(); self._p_fan.clear()

        def _task(evt):
            csv_path = init_live_save(self.app_vars, "MLC", 1, ["State_Idx", "V_prog(V)", "Time(s)", "G(S)"], self.app_state.get("seq_folder"))
            def _prog(pct, st_data):
                for t, g in zip(st_data["time"], st_data["conductances"]):
                    append_live_save(csv_path, [st_data["state_idx"], st_data["v_prog"], t, g])
                self._q.put({"type": "prog", "pct": pct, "state": st_data})
            drv.run_mlc_states(v_rst, t_rst, v_start, v_stop, n_states, t_write, v_read, t_read, ret_t, ret_i, delay, comp, nplc, _prog, evt)
            return True

        self._acq = AcqThread(_task, self._q); self._acq.start()
        self._run_btn.config(state="disabled"); self.status.busy("MLC Programming & Retention running…"); self._poll()

    def _poll(self):
        try:
            while True:
                msg = self._q.get_nowait()
                if msg["type"] == "prog":
                    self._prog.set(msg["pct"]); st = msg["state"]; self._reps.append(st)
                    if st["conductances"]:
                        avg_g = sum(st["conductances"][:3]) / min(3, len(st["conductances"]))
                        self._p_states.add_trace([st["state_idx"]], [avg_g], marker="o", scatter=True, color_idx=0)
                        self._p_fan.add_trace(st["time"], st["conductances"], label=f"S{st['state_idx']}", color_idx=st["state_idx"] % 12)
                elif msg["type"] == "done":
                    self._prog.set(100, "Done"); self._run_btn.config(state="normal"); self.status.ok("MLC complete.")
                    auto_save_fig(self.app_vars, "MLC_States", 1, self._p_states.fig, self.app_state.get("seq_folder"))
                    auto_save_fig(self.app_vars, "MLC_Fan", 1, self._p_fan.fig, self.app_state.get("seq_folder"))
                    self.finish_run(); return
                elif msg["type"] == "error":
                    self.log.log(f"MLC error: {msg['message']}", "err"); self._prog.reset(); self._run_btn.config(state="normal"); return
        except queue.Empty: pass
        self.start_poll(self._poll)

    def _export(self):
        if not self._reps: return
        def _z(w):
            for st in self._reps:
                for t, g in zip(st["time"], st["conductances"]): w.writerow([1, st["state_idx"], st["v_prog"], t, g])
        self.manual_export("MLC", ["State_Idx", "V_prog(V)", "Time(s)", "G(S)"], lambda w: _z(w))

class SequenceTab(tk.Frame):
    SEQ_TEST_NAMES = [
        "Forming", "DC I-V", "Pulse I-V", "LTP-Constant", "LTP-IncPulseWidth", "LTP-IncAmplitude", "LTP-LogAmplitude",
        "PPF", "PPD", "STDP", "Retention (Single)", "Retention (Multi-State from LTP)", "Endurance", "MLC (Multi-State)"
    ]

    def __init__(self, parent, tabs_dict, log, status, app_state):
        super().__init__(parent, bg=C["panel"])
        self.tabs_dict = tabs_dict; self.log = log; self.status = status; self.app_state = app_state
        self.sequence = []; self.seq_idx = 0; self._build()

    def _build(self):
        hdr = tk.Frame(self, bg=C["strip"]); hdr.pack(fill="x")
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x")
        tk.Label(hdr, text="SEQUENCE RUNNER", bg=C["strip"], fg=C["text"],
                 font=FONT_HEADER).pack(side="left", padx=S(12), pady=S(7))

        add_f = tk.Frame(self, bg=C["panel2"]); add_f.pack(fill="x", padx=10, pady=4)
        tk.Label(add_f, text="Test:", bg=C["panel2"], fg=C["text2"], font=FONT_LABEL).pack(side="left", padx=4)
        self._cb = mk_combobox(add_f, values=self.SEQ_TEST_NAMES, width=30); self._cb.pack(side="left", padx=4); self._cb.current(0)
        tk.Label(add_f, text="Runs:", bg=C["panel2"], fg=C["text2"], font=FONT_LABEL).pack(side="left", padx=4)
        self._runs_var = tk.StringVar(value="1"); mk_entry(add_f, width=5, textvariable=self._runs_var).pack(side="left", padx=2)
        mk_button(add_f, "+ Add with Params…", self._add_step, color=C["btn_act"]).pack(side="left", padx=8)
        mk_button(add_f, "Quick Add", self._add_quick, color=C["btn"]).pack(side="left", padx=2)

        list_f = tk.Frame(self, bg=C["panel"]); list_f.pack(fill="both", expand=True, padx=10, pady=4)
        scrollbar = ttk.Scrollbar(list_f, orient="vertical")
        self._lb = tk.Listbox(list_f, bg=C["entry"], fg=C["text"], font=FONT_MONO, relief="flat", highlightbackground=C["border"], yscrollcommand=scrollbar.set, height=14, selectbackground=C["btn_act"], selectforeground=C["accent"])
        scrollbar.config(command=self._lb.yview); scrollbar.pack(side="right", fill="y")
        self._lb.pack(side="left", fill="both", expand=True)
        self._lb.bind("<Double-Button-1>", lambda e: self._edit_selected())

        bot = tk.Frame(self, bg=C["panel"]); bot.pack(fill="x", padx=10, pady=4)
        mk_button(bot, "Edit Selected", self._edit_selected, color=C["btn"]).pack(side="left", padx=4)
        mk_button(bot, "↑ Move Up",       self._move_up,       color=C["btn"]).pack(side="left", padx=4)
        mk_button(bot, "↓ Move Down",     self._move_down,     color=C["btn"]).pack(side="left", padx=4)
        mk_button(bot, "✕ Remove",        self._remove,        color=C["btn"], fg=C["red"]).pack(side="left", padx=4)
        mk_button(bot, "✕ Clear All",     self._clear,         color=C["btn"], fg=C["red"]).pack(side="left", padx=4)
        self._run_btn = mk_button(bot, "▶ RUN SEQUENCE", self._start, color=C["btn"], fg=C["accent3"], font=FONT_HEADER); self._run_btn.pack(side="right", padx=4)
        mk_button(bot, "■ STOP", self._stop_seq, color=C["btn"], fg=C["red"]).pack(side="right", padx=4)

        info_f = tk.Frame(self, bg=C["panel2"]); info_f.pack(fill="x", padx=10, pady=(0, 6))
        self._folder_lbl = tk.Label(info_f, text="Sequencer saves to: Device / SEQ_<timestamp> / TestName /", bg=C["panel2"], fg=C["text2"], font=FONT_SMALL, anchor="w")
        self._folder_lbl.pack(fill="x", padx=8, pady=2)

    def _add_step(self):
        test, runs_str = self._cb.get(), self._runs_var.get()
        if not runs_str.isdigit() or int(runs_str) < 1: messagebox.showerror("Invalid", "Runs must be a positive integer."); return
        overrides = self._open_param_dialog(test, {})
        if overrides is None: return
        self.sequence.append({"test": test, "runs": int(runs_str), "params": overrides}); self._update_lb()

    def _add_quick(self):
        test, runs_str = self._cb.get(), self._runs_var.get()
        if not runs_str.isdigit() or int(runs_str) < 1: return
        self.sequence.append({"test": test, "runs": int(runs_str), "params": {}}); self._update_lb()

    def _edit_selected(self):
        sel = self._lb.curselection()
        if not sel: return
        step = self.sequence[sel[0]]
        new_params = self._open_param_dialog(step["test"], step["params"])
        if new_params is not None: step["params"] = new_params; self._update_lb()

    def _remove(self):
        sel = self._lb.curselection()
        if sel: del self.sequence[sel[0]]; self._update_lb()
    def _clear(self): self.sequence.clear(); self._update_lb()
    def _move_up(self):
        sel = self._lb.curselection()
        if not sel or sel[0] == 0: return
        i = sel[0]; self.sequence[i-1], self.sequence[i] = self.sequence[i], self.sequence[i-1]; self._update_lb(); self._lb.selection_set(i-1)
    def _move_down(self):
        sel = self._lb.curselection()
        if not sel or sel[0] >= len(self.sequence) - 1: return
        i = sel[0]; self.sequence[i], self.sequence[i+1] = self.sequence[i+1], self.sequence[i]; self._update_lb(); self._lb.selection_set(i+1)

    def _update_lb(self):
        self._lb.delete(0, tk.END)
        for i, step in enumerate(self.sequence):
            pcount = len(step.get("params", {}))
            pstr = f"  [{pcount} overrides]" if pcount else ""
            self._lb.insert(tk.END, f"  {i+1:>2}.  {step['test']:<32}  ×{step['runs']} runs{pstr}")

    def _open_param_dialog(self, test_name, existing_params):
        spec = self._param_spec(test_name)
        if not spec: return dict(existing_params)
        dlg = tk.Toplevel(self, bg=C["bg"]); dlg.title(f"Parameters - {test_name}"); dlg.resizable(False, True); dlg.grab_set(); dlg.focus_set()
        tk.Label(dlg, text=f"  Configure: {test_name}", bg=C["bg"], fg=C["accent"], font=FONT_HEADER).pack(anchor="w", padx=10, pady=(10, 4))

        canvas = tk.Canvas(dlg, bg=C["bg"], bd=0, highlightthickness=0, width=460, height=420)
        vsb = ttk.Scrollbar(dlg, orient="vertical", command=canvas.yview); canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y"); canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=C["bg"]); win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))

        vars_map = {}
        for section_title, params in spec:
            sf = mk_lf(inner, section_title, C["accent2"]); sf.pack(fill="x", padx=8, pady=4); sf.columnconfigure(1, weight=1)
            for row_i, (lbl, key, hint) in enumerate(params):
                tk.Label(sf, text=lbl, bg=C["panel"], fg=C["text2"], font=FONT_LABEL, width=22, anchor="e").grid(row=row_i, column=0, sticky="e", padx=(8, 4), pady=3)
                v = tk.StringVar(value=str(existing_params.get(key, hint)))
                mk_entry(sf, width=16, textvariable=v).grid(row=row_i, column=1, sticky="ew", padx=(0, 8), pady=3)
                vars_map[key] = v

        result = [None]
        def _ok(): result[0] = {k: v.get().strip() for k, v in vars_map.items() if v.get().strip()}; dlg.destroy()
        def _cancel(): dlg.destroy()
        btn_row = tk.Frame(dlg, bg=C["bg"]); btn_row.pack(fill="x", padx=10, pady=8)
        mk_button(btn_row, "✓ OK", _ok, color=C["btn_act"], fg=C["accent3"]).pack(side="right", padx=4)
        mk_button(btn_row, "Cancel", _cancel, color=C["btn"]).pack(side="right", padx=4)
        dlg.wait_window(); return result[0]

    def _param_spec(self, test_name):
        ltp_common = [("V_pot max (V)", "v_pot", "1.5"), ("V_pot start (V)", "v_pot_s", "0.5"), ("V_pot step (V)", "v_pot_st", "0.05"), ("t_pot (s)", "t_pot", "5e-3"), ("t_start (s)", "t_pot_s", "5e-3"), ("N_pot pulses", "n_pot", "50"), ("V_dep max (V)", "v_dep", "-1.5"), ("V_dep start (V)", "v_dep_s", "-0.5"), ("V_dep step (V)", "v_dep_st", "-0.05"), ("t_dep (s)", "t_dep", "5e-3"), ("t_start (s)", "t_dep_s", "5e-3"), ("N_dep pulses", "n_dep", "50")]
        ltp_read = [("Delay (s)", "delay", "1e-3"), ("V_read (V)", "v_read", "0.1"), ("t_read (s)", "t_read", "5e-3"), ("Compliance (A)", "comp", "0.1"), ("NPLC", "nplc", "0.1"), ("N Runs", "n_runs", "1")]
        specs = {
            "Forming": [("Voltage Ramp", [("V_start (V)", "v_start", "0.0"), ("V_stop (V)", "v_stop", "4.0"), ("V_step (V)", "v_step", "0.05"), ("t_step (s)", "t_step", "0.1"), ("Compliance (A)", "comp", "1e-3"), ("NPLC", "nplc", "1.0"), ("Stop on form (1/0)", "stop_form", "1"), ("I_form/I_comp thr.", "form_thr", "0.95"), ("Delay (s)", "delay", "1e-3"), ("V_read (V)", "v_read", "0.1"), ("t_read (s)", "t_read", "5e-3"), ("N Forming runs", "n_runs", "1")])],
            "DC I-V": [("DC Sweep", [("V_pos max (V)", "v_pos", "2.0"), ("V_neg max (V)", "v_neg", "-2.0"), ("V_step (V)", "v_step", "0.01"), ("Delay (s)", "delay", "1e-3"), ("N Cycles", "n_cycles", "1"), ("Compliance (A)", "comp", "0.1"), ("NPLC", "nplc", "1.0")])],
            "Pulse I-V": [("Pulse IV", [("V_SET start (V)", "v_set_s", "0.0"), ("V_SET stop (V)", "v_set", "2.0"), ("V_SET step (V)", "v_set_st", "0.05"), ("V_RST start (V)", "v_rst_s", "0.0"), ("V_RST stop (V)", "v_rst", "-2.0"), ("V_RST step (V)", "v_rst_st", "-0.05"), ("t_write (s)", "t_write", "5e-3"), ("Delay (s)", "delay", "1e-3"), ("V_read (V)", "v_read", "0.1"), ("t_read (s)", "t_read", "5e-3"), ("Compliance (A)", "comp", "0.1"), ("NPLC", "nplc", "0.1"), ("N Runs", "n_runs", "1")])],
            "PPF": [("PPF Parameters", [("V_pulse (V)", "vp", "1.5"), ("t_pulse (s)", "tp", "5e-3"), ("Delay (s)", "delay", "1e-3"), ("ISI min (s)", "imin", "0.002"), ("ISI max (s)", "imax", "1.0"), ("N ISI pts", "npts", "15"), ("Log ISI (1/0)", "log", "1"), ("V_read (V)", "vr", "0.1"), ("t_read (s)", "tr", "5e-3"), ("Compliance", "comp", "0.1"), ("NPLC", "nplc", "0.1"), ("N Runs", "nrun", "1")])],
            "PPD": [("PPD Parameters", [("V_pulse (V)", "vp", "-1.5"), ("t_pulse (s)", "tp", "5e-3"), ("Delay (s)", "delay", "1e-3"), ("ISI min (s)", "imin", "0.002"), ("ISI max (s)", "imax", "1.0"), ("N ISI pts", "npts", "15"), ("Log ISI (1/0)", "log", "1"), ("V_read (V)", "vr", "0.1"), ("t_read (s)", "tr", "5e-3"), ("Compliance", "comp", "0.1"), ("NPLC", "nplc", "0.1"), ("N Runs", "nrun", "1")])],
            "STDP": [("STDP", [("V_pre (V)", "vpre", "1.5"), ("V_post (V)", "vpost", "-1.5"), ("t_pulse (s)", "tp", "5e-3"), ("Delay (s)", "delay", "1e-3"), ("Δt max (s)", "dtm", "0.1"), ("N Δt points", "ndt", "21"), ("V_read (V)", "vr", "0.1"), ("t_read (s)", "tr", "5e-3"), ("Compliance", "comp", "0.1"), ("NPLC", "nplc", "0.1"), ("N Runs", "nrun", "1")])],
            "Retention (Single)": [("Programming Pulse", [("V_prog (V)", "v_prog", "1.5"), ("t_prog (s)", "t_prog", "5e-3"), ("N_prog pulses", "n_prog", "0")]), ("Retention", [("Total time (s)", "total_t", "3600"), ("Read interval(s)", "interval", "10"), ("V_read (V)", "v_read", "0.1"), ("t_read (s)", "t_read", "5e-3"), ("Compliance (A)", "comp", "0.01"), ("NPLC", "nplc", "1.0"), ("N Runs", "n_runs", "1")])],
            "Retention (Multi-State from LTP)": [("Retention", [("Total time (s)", "total_t", "3600"), ("Read interval(s)", "interval", "10"), ("V_read (V)", "v_read", "0.1"), ("t_read (s)", "t_read", "5e-3"), ("Compliance (A)", "comp", "0.01"), ("NPLC", "nplc", "1.0")])],
            "Endurance": [("Endurance", [("V_SET (V)", "vs", "2.0"), ("V_RST (V)", "vr", "-2.0"), ("t_SET (s)", "ts", "5e-3"), ("t_RST (s)", "tr", "5e-3"), ("Delay (s)", "delay", "1e-3"), ("V_read (V)", "vread", "0.1"), ("t_read (s)", "tread", "5e-3"), ("N cycles/run", "cyc", "1000"), ("Compliance (A)", "comp", "0.1"), ("NPLC", "nplc", "0.1"), ("N Runs", "nrun", "1")])],
            "MLC (Multi-State)": [("MLC Programming", [("V_reset (V)", "v_rst", "-2.0"), ("t_reset (s)", "t_rst", "5e-3"), ("V_prog start (V)", "v_start", "0.5"), ("V_prog stop (V)", "v_stop", "2.5"), ("N States", "n_state", "64"), ("t_prog (s)", "t_write", "5e-3"), ("Delay (s)", "delay", "1e-3")]), ("Retention", [("Ret. time/state (s)", "ret_t", "10"), ("Read interval (s)", "ret_i", "0.5"), ("V_read (V)", "v_read", "0.1"), ("t_read (s)", "t_read", "5e-3"), ("Compliance (A)", "comp", "0.1"), ("NPLC", "nplc", "0.1")])]
        }
        for ltp_name in ["LTP-Constant", "LTP-IncPulseWidth", "LTP-IncAmplitude", "LTP-LogAmplitude"]: specs[ltp_name] = [("LTP/LTD Pulses", ltp_common), ("Read & Delays", ltp_read)]
        return specs.get(test_name, [])

    def _apply_params(self, test_name, step_params, tab):
        if not step_params: return
        def _set(var, val):
            if val is not None:
                try: var.set(str(val))
                except: pass
        p = step_params

        if test_name == "Forming":
            _set(tab._fp["v_start"], p.get("v_start")); _set(tab._fp["v_stop"], p.get("v_stop")); _set(tab._fp["v_step"], p.get("v_step"))
            _set(tab._fp["t_step"], p.get("t_step")); _set(tab._fp["comp"], p.get("comp")); _set(tab._fp["nplc"], p.get("nplc")); _set(tab._form_thresh, p.get("form_thr"))
            _set(tab._vp["delay"], p.get("delay")); _set(tab._vp["v_read"], p.get("v_read")); _set(tab._vp["t_read"], p.get("t_read")); _set(tab._n_runs_var, p.get("n_runs"))
            if "stop_form" in p: tab._stop_on_form.set(p["stop_form"] != "0")
        elif test_name == "DC I-V":
            _set(tab._sv["v_pos"], p.get("v_pos")); _set(tab._sv["v_neg"], p.get("v_neg")); _set(tab._sv["v_step"], p.get("v_step"))
            _set(tab._sv["delay"], p.get("delay")); _set(tab._sv["n_cycles"], p.get("n_cycles")); _set(tab._sv["comp"], p.get("comp")); _set(tab._sv["nplc"], p.get("nplc"))
        elif test_name == "Pulse I-V":
            _set(tab._p["v_set_s"], p.get("v_set_s")); _set(tab._p["v_set"], p.get("v_set")); _set(tab._p["v_set_st"], p.get("v_set_st"))
            _set(tab._p["v_rst_s"], p.get("v_rst_s")); _set(tab._p["v_rst"], p.get("v_rst")); _set(tab._p["v_rst_st"], p.get("v_rst_st"))
            _set(tab._p["t_write"], p.get("t_write")); _set(tab._p["delay"], p.get("delay")); _set(tab._p["v_read"], p.get("v_read"))
            _set(tab._p["t_read"], p.get("t_read")); _set(tab._p["comp"], p.get("comp")); _set(tab._p["nplc"], p.get("nplc")); _set(tab._p["n_runs"], p.get("n_runs"))
        elif test_name.startswith("LTP-"):
            sm = {"LTP-Constant": "Constant Amp & Width", "LTP-IncPulseWidth": "Incremental Pulse Width", "LTP-IncAmplitude": "Incremental Amplitude", "LTP-LogAmplitude": "Logarithmic Amplitude"}
            if test_name in sm: tab._scheme_var.set(sm[test_name])
            _set(tab._pp["v_pot"], p.get("v_pot")); _set(tab._pp["v_pot_s"], p.get("v_pot_s")); _set(tab._pp["v_pot_st"], p.get("v_pot_st"))
            _set(tab._pp["t_pot"], p.get("t_pot")); _set(tab._pp["t_pot_s"], p.get("t_pot_s")); _set(tab._pp["n_pot"], p.get("n_pot"))
            _set(tab._dp["v_dep"], p.get("v_dep")); _set(tab._dp["v_dep_s"], p.get("v_dep_s")); _set(tab._dp["v_dep_st"], p.get("v_dep_st"))
            _set(tab._dp["t_dep"], p.get("t_dep")); _set(tab._dp["t_dep_s"], p.get("t_dep_s")); _set(tab._dp["n_dep"], p.get("n_dep"))
            _set(tab._gp["delay"], p.get("delay")); _set(tab._gp["v_read"], p.get("v_read")); _set(tab._gp["t_read"], p.get("t_read"))
            _set(tab._gp["comp"], p.get("comp")); _set(tab._gp["nplc"], p.get("nplc")); _set(tab._gp["n_runs"], p.get("n_runs"))
        elif test_name in ("PPF", "PPD"):
            pp = tab._ppf_p if test_name == "PPF" else tab._ppd_p
            _set(pp["vp"], p.get("vp")); _set(pp["tp"], p.get("tp")); _set(pp["delay"], p.get("delay")); _set(pp["imin"], p.get("imin")); _set(pp["imax"], p.get("imax"))
            _set(pp["npts"], p.get("npts")); _set(pp["log"], p.get("log")); _set(pp["vr"], p.get("vr")); _set(pp["tr"], p.get("tr"))
            _set(pp["comp"], p.get("comp")); _set(pp["nplc"], p.get("nplc")); _set(pp["nrun"], p.get("nrun"))
        elif test_name == "STDP":
            _set(tab._p["vpre"], p.get("vpre")); _set(tab._p["vpost"], p.get("vpost")); _set(tab._p["tp"], p.get("tp")); _set(tab._p["delay"], p.get("delay"))
            _set(tab._p["dtm"], p.get("dtm")); _set(tab._p["ndt"], p.get("ndt")); _set(tab._p["vr"], p.get("vr")); _set(tab._p["tr"], p.get("tr"))
            _set(tab._p["comp"], p.get("comp")); _set(tab._p["nplc"], p.get("nplc")); _set(tab._p["nrun"], p.get("nrun"))
        elif test_name == "Retention (Single)":
            _set(tab._sp["v_prog"], p.get("v_prog")); _set(tab._sp["t_prog"], p.get("t_prog")); _set(tab._sp["n_prog"], p.get("n_prog"))
            _set(tab._rp["total_t"], p.get("total_t")); _set(tab._rp["interval"], p.get("interval")); _set(tab._rp["v_read"], p.get("v_read"))
            _set(tab._rp["t_read"], p.get("t_read")); _set(tab._rp["comp"], p.get("comp")); _set(tab._rp["nplc"], p.get("nplc")); _set(tab._rp["n_runs"], p.get("n_runs"))
        elif test_name == "Retention (Multi-State from LTP)":
            _set(tab._mrp["total_t"], p.get("total_t")); _set(tab._mrp["interval"], p.get("interval")); _set(tab._mrp["v_read"], p.get("v_read"))
            _set(tab._mrp["t_read"], p.get("t_read")); _set(tab._mrp["comp"], p.get("comp")); _set(tab._mrp["nplc"], p.get("nplc"))
        elif test_name == "Endurance":
            _set(tab._p["vs"], p.get("vs")); _set(tab._p["vr"], p.get("vr")); _set(tab._p["ts"], p.get("ts")); _set(tab._p["tr"], p.get("tr")); _set(tab._p["delay"], p.get("delay"))
            _set(tab._p["vread"], p.get("vread")); _set(tab._p["tread"], p.get("tread")); _set(tab._p["cyc"], p.get("cyc")); _set(tab._p["comp"], p.get("comp"))
            _set(tab._p["nplc"], p.get("nplc")); _set(tab._p["nrun"], p.get("nrun"))
        elif test_name == "MLC (Multi-State)":
            _set(tab._p["v_rst"], p.get("v_rst")); _set(tab._p["t_rst"], p.get("t_rst")); _set(tab._p["v_start"], p.get("v_start")); _set(tab._p["v_stop"], p.get("v_stop"))
            _set(tab._p["n_state"], p.get("n_state")); _set(tab._p["t_write"], p.get("t_write")); _set(tab._p["delay"], p.get("delay")); _set(tab._p["ret_t"], p.get("ret_t"))
            _set(tab._p["ret_i"], p.get("ret_i")); _set(tab._p["v_read"], p.get("v_read")); _set(tab._p["t_read"], p.get("t_read"))
            _set(tab._p["comp"], p.get("comp")); _set(tab._p["nplc"], p.get("nplc"))

    def _stop_seq(self):
        tab_info = self.tabs_dict.get("_active_tab")
        if tab_info:
            try: tab_info.safe_stop()
            except: pass
        self.seq_idx = len(self.sequence)

    def _start(self):
        if not self.sequence: messagebox.showinfo("Sequence", "Add at least one test step first."); return
        ts = datetime.now().strftime("SEQ_%Y%m%d_%H%M%S")
        self.app_state["seq_folder"] = ts
        self._folder_lbl.config(text=f"Saving to: …/{self.tabs_dict.get('_device_id','Device')}/{ts}/")
        self.seq_idx = 0; self._run_btn.config(state="disabled")
        self.log.log(f"Sequence started - folder: {ts}", "ok"); self._run_next()

    def _run_next(self):
        if self.seq_idx >= len(self.sequence):
            self.app_state["seq_folder"] = None
            self.log.log("Sequence complete.", "ok"); self.status.ok("Sequence complete."); self._run_btn.config(state="normal"); return
        step = self.sequence[self.seq_idx]
        test, runs = step["test"], step["runs"]

        tab, run_fn, runs_var = self._resolve_tab_run(test)
        if tab is None:
            self.log.log(f"Unknown test: {test}", "err"); self.seq_idx += 1; self._run_next(); return

        if test == "Retention (Multi-State from LTP)":
            tab._clear_states(); tab._load_from_ltp()
            if not tab._states:
                self.log.log("Retention (Multi): No LTP data to load - skipping.", "warn"); self.seq_idx += 1; self._run_next(); return

        self._apply_params(test, step.get("params", {}), tab)
        if runs_var: runs_var.set(str(runs))
        tab.on_complete = self._step_done
        self.tabs_dict["_active_tab"] = tab

        self.log.log(f"Sequence step [{self.seq_idx + 1}/{len(self.sequence)}]: {test}  ×{runs}", "info")
        run_fn()

    def _resolve_tab_run(self, test_name):
        td = self.tabs_dict
        if test_name == "Forming": t = td.get("Forming"); return (t, t._run, t._n_runs_var) if t else (None, None, None)
        if test_name == "DC I-V": t = td.get("DC I-V"); return (t, t._run, t._sv["n_cycles"]) if t else (None, None, None)
        if test_name == "Pulse I-V": t = td.get("Pulse I-V"); return (t, t._run, t._p["n_runs"]) if t else (None, None, None)
        if test_name.startswith("LTP-"): t = td.get("LTP/LTD"); return (t, t._run, t._n_runs_var) if t else (None, None, None)
        if test_name == "PPF": t = td.get("PPF"); return (t, lambda: t._run("PPF"), t._ppf_p["nrun"]) if t else (None, None, None)
        if test_name == "PPD": t = td.get("PPD"); return (t, lambda: t._run("PPD"), t._ppd_p["nrun"]) if t else (None, None, None)
        if test_name == "STDP": t = td.get("STDP"); return (t, t._run, t._n_runs_var) if t else (None, None, None)
        if test_name == "Retention (Single)": t = td.get("Retention"); return (t, t._run_single, t._n_runs_var) if t else (None, None, None)
        if test_name == "Retention (Multi-State from LTP)": t = td.get("Retention"); return (t, t._run_multi, t._n_runs_var) if t else (None, None, None)
        if test_name == "Endurance": t = td.get("Endurance"); return (t, t._run, t._n_runs_var) if t else (None, None, None)
        if test_name == "MLC (Multi-State)": t = td.get("MLC"); return (t, t._run, None) if t else (None, None, None)
        return (None, None, None)

    def _step_done(self):
        self.seq_idx += 1; self.after(600, self._run_next)


class DeviceVariabilityTab(BaseMeasurementTab):
    def __init__(self, parent, get_driver, log, status, app_vars, app_state):
        super().__init__(parent, get_driver, log, status, app_vars, app_state)
        self._devices = []
        self._all_stats = []
        self._build()

    def _build(self):
        strip = tk.Frame(self, bg=C["strip"]); strip.pack(fill="x")
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x")
        tk.Label(strip, text="  DEVICE VARIABILITY & STATISTICS SUITE", bg=C["strip"],
                 fg=C["accent"], font=FONT_HEADER).pack(side="left", padx=10, pady=6)
        self._run_btn = mk_button(strip, "▶ RUN SELECTED", self._run_selected,
                                  color=C["strip"], fg=C["accent"], font=FONT_HEADER)
        self._run_btn.pack(side="left", padx=4, pady=4)
        mk_button(strip, "■ STOP", self.safe_stop, color=C["btn"], fg=C["red"]).pack(side="left", padx=4)
        mk_button(strip, "STATS", self._show_stats, color=C["btn"]).pack(side="left", padx=4)
        mk_button(strip, "CSV", self._export_all, color=C["btn"]).pack(side="left", padx=4)
        mk_button(strip, "CLEAR ALL", self._clear_all, color=C["btn"]).pack(side="left", padx=4)

        self._prog = ProgressBar(self); self._prog.pack(fill="x")
        body = tk.Frame(self, bg=C["panel"]); body.pack(fill="both", expand=True)

        _, L = scrolled_frame(body, width=320)
        R = tk.Frame(body, bg=C["panel"]); R.pack(side="left", fill="both", expand=True, padx=4, pady=4)

        reg_f = mk_lf(L, "Device Registry", C["accent3"]); reg_f.pack(fill="x", padx=6, pady=(6,2))
        row0 = tk.Frame(reg_f, bg=C["panel"]); row0.pack(fill="x", padx=4, pady=2)
        tk.Label(row0, text="Dev Name:", bg=C["panel"], fg=C["text2"], font=FONT_LABEL, width=10).pack(side="left")
        self._dev_name_var = tk.StringVar(value="D01")
        mk_entry(row0, textvariable=self._dev_name_var, width=10).pack(side="left", padx=4)
        tk.Label(row0, text="Dia(µm):", bg=C["panel"], fg=C["text2"], font=FONT_LABEL, width=8).pack(side="left")
        self._dev_dia_var = tk.StringVar(value="50")
        mk_entry(row0, textvariable=self._dev_dia_var, width=8).pack(side="left", padx=4)
        row1 = tk.Frame(reg_f, bg=C["panel"]); row1.pack(fill="x", padx=4, pady=2)
        tk.Label(row1, text="TE:", bg=C["panel"], fg=C["text2"], font=FONT_LABEL, width=4).pack(side="left")
        self._dev_te_var = tk.StringVar(value="W")
        mk_entry(row1, textvariable=self._dev_te_var, width=8).pack(side="left", padx=2)
        tk.Label(row1, text="BE:", bg=C["panel"], fg=C["text2"], font=FONT_LABEL, width=4).pack(side="left")
        self._dev_be_var = tk.StringVar(value="BE")
        mk_entry(row1, textvariable=self._dev_be_var, width=8).pack(side="left", padx=2)
        tk.Label(row1, text="d(nm):", bg=C["panel"], fg=C["text2"], font=FONT_LABEL, width=6).pack(side="left")
        self._dev_thick_var = tk.StringVar(value="8")
        mk_entry(row1, textvariable=self._dev_thick_var, width=6).pack(side="left", padx=2)
        row2 = tk.Frame(reg_f, bg=C["panel"]); row2.pack(fill="x", padx=4, pady=2)
        mk_button(row2, "+ Add Device", self._add_device, color=C["btn_h"]).pack(side="left", padx=4)
        mk_button(row2, "- Remove", self._remove_device, color=C["btn"]).pack(side="left", padx=4)
        self._dev_list = tk.Listbox(reg_f, bg=C["entry2"], fg=C["text"], font=FONT_SMALL,
                                     height=5, selectmode="extended", relief="flat")
        self._dev_list.pack(fill="x", padx=6, pady=4)

        st_f = mk_lf(L, "Study Type", C["accent2"]); st_f.pack(fill="x", padx=6, pady=2)
        self._study_var = tk.StringVar(value="D2D (Multi-Device)")
        studies = ["D2D (Multi-Device)", "Electrode Diameter Scaling",
                   "V-Step Size Sensitivity", "Cycle-to-Cycle (Single Device)",
                   "Temperature Series (I-V)"]
        mk_combobox(st_f, values=studies, width=28, textvariable=self._study_var).pack(padx=6, pady=4)

        mp_f = mk_lf(L, "Measurement", C["accent"]); mp_f.pack(fill="x", padx=6, pady=2); mp_f.columnconfigure(1, weight=1)
        self._mp = {}
        for i, (lbl, key, dflt) in enumerate([
            ("V_pos (V)",     "vpos",   "2.0"),
            ("V_neg (V)",     "vneg",  "-2.0"),
            ("V_step (V)",    "vstep",  "0.05"),
            ("V_step min",    "vs_min", "0.01"),
            ("V_step max",    "vs_max", "0.2"),
            ("N V-steps",     "vs_n",   "8"),
            ("Compliance (A)","comp",   "0.1"),
            ("NPLC",          "nplc",   "0.1"),
            ("Delay (s)",     "delay",  "1e-3"),
            ("N Repeats",     "nrep",   "5"),
            ("V_read (V)",    "vread",  "0.1"),
            ("t_read (s)",    "tread",  "5e-3"),
            ("Temp (°C)",     "temp",   "25"),
        ]):
            self._mp[key] = tk.StringVar(value=dflt); param_row(mp_f, i, lbl, self._mp[key], label_width=16)

        self._nb_plot = ttk.Notebook(R); self._nb_plot.pack(fill="both", expand=True)
        self._pf_iv   = tk.Frame(self._nb_plot, bg=C["panel"])
        self._pf_stat = tk.Frame(self._nb_plot, bg=C["panel"])
        self._pf_hist = tk.Frame(self._nb_plot, bg=C["panel"])
        self._pf_box  = tk.Frame(self._nb_plot, bg=C["panel"])
        self._nb_plot.add(self._pf_iv,   text="  I-V Overlay  ")
        self._nb_plot.add(self._pf_stat, text="  ON/OFF Stats  ")
        self._nb_plot.add(self._pf_hist, text="  Histograms  ")
        self._nb_plot.add(self._pf_box,  text="  Box / Violin  ")

        self._iv_plot = DualLivePlot(self._pf_iv, "I-V Overlay", "Voltage (V)", "Current (A)")
        self._iv_plot.pack(fill="both", expand=True)
        self._stat_plot = LivePlot(self._pf_stat, "ON/OFF vs Device", "Device Index", "ON/OFF Ratio")
        self._stat_plot.pack(fill="both", expand=True)

        if MATPLOTLIB_OK:
            self._hist_fig = mk_figure((9, 4), facecolor="white")
            self._hist_canvas = FigureCanvasTkAgg(self._hist_fig, master=self._pf_hist)
            self._hist_canvas.get_tk_widget().pack(fill="both", expand=True)
            self._box_fig = mk_figure((9, 4), facecolor="white")
            self._box_canvas = FigureCanvasTkAgg(self._box_fig, master=self._pf_box)
            self._box_canvas.get_tk_widget().pack(fill="both", expand=True)

    def _add_device(self):
        name = self._dev_name_var.get().strip()
        dia  = self._dev_dia_var.get().strip()
        te   = self._dev_te_var.get().strip()
        be   = self._dev_be_var.get().strip()
        d    = self._dev_thick_var.get().strip()
        if not name: return
        entry = {"name": name, "dia": dia, "te": te, "be": be, "thick": d, "data": []}
        self._devices.append(entry)
        self._dev_list.insert("end", f"{name} | Dia:{dia}µm | {te}/{d}nm/{be}")
        self.log.log(f"Device added: {name} dia={dia}µm", "ok")

    def _remove_device(self):
        sel = list(self._dev_list.curselection())
        for i in reversed(sel):
            self._dev_list.delete(i)
            if i < len(self._devices): self._devices.pop(i)

    def _clear_all(self):
        self._devices.clear(); self._all_stats.clear()
        self._dev_list.delete(0, "end"); self._iv_plot.clear(); self._stat_plot.clear()
        if MATPLOTLIB_OK:
            self._hist_fig.clear(); self._hist_canvas.draw()
            self._box_fig.clear(); self._box_canvas.draw()

    def _run_selected(self):
        drv = self.get_drv()
        if not drv: self.log.log("Not connected.", "err"); return
        study = self._study_var.get()
        sel = list(self._dev_list.curselection())
        if not sel and study != "V-Step Size Sensitivity":
            self.log.log("Select at least one device in registry.", "warn"); return

        v_pos  = float(self._mp["vpos"].get())
        v_neg  = float(self._mp["vneg"].get())
        v_step = float(self._mp["vstep"].get())
        comp   = float(self._mp["comp"].get())
        nplc   = float(self._mp["nplc"].get())
        delay  = float(self._mp["delay"].get())
        nrep   = int(self._mp["nrep"].get())
        v_read = float(self._mp["vread"].get())
        t_read = float(self._mp["tread"].get())

        devs = [self._devices[i] for i in sel] if sel else [{"name":"D01","dia":"50","te":"TE","be":"BE","thick":"8","data":[]}]
        self._q = queue.Queue()
        self._iv_plot.clear(); self._stat_plot.clear()
        if MATPLOTLIB_OK: self._hist_fig.clear(); self._box_fig.clear()

        if study == "V-Step Size Sensitivity":
            self._run_vstep_study(drv, v_pos, v_neg, comp, nplc, delay, nrep)
            return
        if study == "Temperature Series (I-V)":
            self._run_temp_series(drv, devs, v_pos, v_neg, v_step, comp, nplc, delay)
            return

        def _task(evt):
            for di, dev in enumerate(devs):
                if evt.is_set(): break
                dev_reps = {"on_off": [], "g_lrs": [], "g_hrs": [], "v_set_list": []}
                for rep in range(nrep):
                    if evt.is_set(): break
                    csv_path = init_live_save(self.app_vars, f"Variability_{dev['name']}", rep+1,
                        ["Voltage(V)", "Current(A)", "Conductance(S)"], self.app_state.get("seq_folder"))
                    def _prog(pct, row, refs, _c=csv_path):
                        append_live_save(_c, row)
                        self._q.put({"type":"prog","pct":(di*nrep+rep)/len(devs)/nrep*100+pct/len(devs)/nrep})
                    res = drv.sweep_true_zero(v_pos, v_neg, v_step, comp, nplc, delay, _prog, evt)
                    vs, cs, gs = res["voltages"], res["currents"], res["conductances"]
                    g_lrs = max(gs) if gs else 0.0
                    g_hrs = min([g for g in gs if g > 0], default=1e-12)
                    on_off = g_lrs / g_hrs if g_hrs > 1e-20 else 1.0
                    v_set = v_pos
                    if gs:
                        g_med = sorted(gs)[len(gs)//2]
                        for vv, gg in zip(vs, gs):
                            if vv > 0 and gg > g_med * 3:
                                v_set = vv; break
                    dev_reps["on_off"].append(on_off); dev_reps["g_lrs"].append(g_lrs)
                    dev_reps["g_hrs"].append(g_hrs); dev_reps["v_set_list"].append(v_set)
                    self._q.put({"type":"iv_done","dev":dev,"rep":rep,"di":di,"data":res})
                self._q.put({"type":"dev_done","dev":dev,"di":di,"stats":dev_reps})
            return True

        self._acq = AcqThread(_task, self._q)
        self._acq.start(); self._run_btn.config(state="disabled")
        self.status.busy("Variability study running…"); self._poll_var()

    def _run_vstep_study(self, drv, v_pos, v_neg, comp, nplc, delay, nrep):
        vs_min = float(self._mp["vs_min"].get()); vs_max = float(self._mp["vs_max"].get()); vs_n = int(self._mp["vs_n"].get())
        steps = list(np.linspace(vs_min, vs_max, vs_n)) if NUMPY_OK else [vs_min + (vs_max-vs_min)*i/max(vs_n-1,1) for i in range(vs_n)]

        def _task(evt):
            for si, vstep in enumerate(steps):
                if evt.is_set(): break
                for rep in range(nrep):
                    if evt.is_set(): break
                    csv_path = init_live_save(self.app_vars, f"VStep_{vstep:.4f}V", rep+1,
                        ["Voltage(V)","Current(A)","Conductance(S)"], self.app_state.get("seq_folder"))
                    def _prog(pct, row, refs, _c=csv_path): append_live_save(_c, row)
                    res = drv.sweep_true_zero(v_pos, v_neg, vstep, comp, nplc, delay, _prog, evt)
                    self._q.put({"type":"vstep_done","vstep":vstep,"si":si,"rep":rep,"data":res})
            return True

        self._acq = AcqThread(_task, self._q); self._acq.start()
        self._run_btn.config(state="disabled"); self.status.busy("V-step study…"); self._poll_var()

    def _run_temp_series(self, drv, devs, v_pos, v_neg, v_step, comp, nplc, delay):
        temps_str = self._mp["temp"].get()
        try: temps = [float(t.strip()) for t in temps_str.replace(";",",").split(",")]
        except: temps = [25.0]

        def _task(evt):
            for dev in devs:
                for ti, temp in enumerate(temps):
                    if evt.is_set(): break
                    self._q.put({"type":"temp_prompt","temp":temp})
                    time.sleep(2.0)
                    csv_path = init_live_save(self.app_vars, f"TempIV_{int(temp)}C", 1,
                        ["Voltage(V)","Current(A)","Conductance(S)"], self.app_state.get("seq_folder"))
                    def _prog(pct, row, refs, _c=csv_path): append_live_save(_c, row)
                    res = drv.sweep_true_zero(v_pos, v_neg, v_step, comp, nplc, 1e-3, _prog, evt)
                    self._q.put({"type":"temp_done","dev":dev,"temp":temp,"ti":ti,"data":res})
            return True

        self._acq = AcqThread(_task, self._q); self._acq.start()
        self._run_btn.config(state="disabled"); self.status.busy("Temp series…"); self._poll_var()

    def _poll_var(self):
        try:
            while True:
                msg = self._q.get_nowait()
                mtype = msg.get("type","")
                if mtype == "prog":
                    self._prog.set(msg["pct"])
                elif mtype == "iv_done":
                    dev = msg["dev"]; d = msg["data"]
                    lbl = f"{dev['name']} Dia:{dev['dia']}µm R{msg['rep']+1}"
                    self._iv_plot.add_trace(d["voltages"], d["currents"], label=lbl, color_idx=msg["di"]*5+msg["rep"])
                elif mtype == "dev_done":
                    dev = msg["dev"]; stats = msg["stats"]; di = msg["di"]
                    self._all_stats.append({"dev": dev, "stats": stats})
                    on_offs = stats["on_off"]
                    self._stat_plot.add_trace([di+1]*len(on_offs), on_offs, label=dev["name"],
                                              color_idx=di, scatter=True, lw=0)
                    self._update_histogram()
                    auto_save_fig(self.app_vars, "Variability", di+1, self._iv_plot.fig, self.app_state.get("seq_folder"))
                elif mtype == "vstep_done":
                    d = msg["data"]
                    self._iv_plot.add_trace(d["voltages"], d["currents"],
                                            label=f"Vstep={msg['vstep']:.3f}V R{msg['rep']+1}",
                                            color_idx=msg["si"]*3+msg["rep"])
                elif mtype == "temp_done":
                    d = msg["data"]
                    self._iv_plot.add_trace(d["voltages"], d["currents"],
                                            label=f"T={msg['temp']:.0f}°C", color_idx=msg["ti"])
                elif mtype == "temp_prompt":
                    self.log.log(f"[TEMP] Waiting for probe station to stabilize at {msg['temp']}°C", "warn")
                elif mtype == "done":
                    self._prog.set(100,"Done"); self._run_btn.config(state="normal")
                    self.status.ok("Variability study complete.")
                    self._update_histogram(); self._update_boxplot(); self.finish_run(); return
                elif mtype == "error":
                    self.log.log(f"Var error: {msg['message']}", "err")
                    self._run_btn.config(state="normal"); return
        except queue.Empty: pass
        self.start_poll(self._poll_var)

    def _update_histogram(self):
        if not MATPLOTLIB_OK or not self._all_stats: return
        self._hist_fig.clear()
        all_on_offs = [v for s in self._all_stats for v in s["stats"]["on_off"]]
        all_g_lrs   = [v for s in self._all_stats for v in s["stats"]["g_lrs"]]
        all_g_hrs   = [v for s in self._all_stats for v in s["stats"]["g_hrs"]]

        axes = self._hist_fig.subplots(1, 3)
        titles = ["ON/OFF Ratio", "G_LRS (S)", "G_HRS (S)"]
        dsets  = [all_on_offs, all_g_lrs, all_g_hrs]
        for ax, dat, ttl in zip(axes, dsets, titles):
            if not dat: continue
            ax.hist(dat, bins=max(5, len(dat)//3), color=PUB_COLORS[0], edgecolor="white",
                    alpha=0.85, linewidth=0.5)
            if NUMPY_OK and len(dat) > 1:
                import statistics
                mu, sig = sum(dat)/len(dat), statistics.stdev(dat)
                ax.axvline(mu, color=PUB_COLORS[1], lw=1.5, ls="--", label=f"μ={mu:.3g}")
                ax.axvline(mu+sig, color=PUB_COLORS[2], lw=1.0, ls=":", label=f"σ={sig:.3g}")
                ax.axvline(mu-sig, color=PUB_COLORS[2], lw=1.0, ls=":")
                ax.legend(fontsize=7)
            ax.set_xlabel(ttl, fontsize=9); ax.set_ylabel("Count", fontsize=9)
            ax.set_title(ttl, fontsize=9, fontweight="bold")
            ax.tick_params(which="both", direction="in", top=True, right=True, labelsize=8)
            ax.set_facecolor("white")
        self._hist_fig.suptitle("Device Parameter Distributions", fontsize=11, fontweight="bold")
        self._hist_fig.tight_layout(pad=1.5)
        self._hist_canvas.draw_idle()

    def _update_boxplot(self):
        if not MATPLOTLIB_OK or not self._all_stats: return
        self._box_fig.clear()
        axes = self._box_fig.subplots(1, 2)
        dev_names = [s["dev"]["name"] for s in self._all_stats]
        on_off_by_dev = [s["stats"]["on_off"] for s in self._all_stats]
        g_lrs_by_dev  = [s["stats"]["g_lrs"] for s in self._all_stats]
        for ax, dsets, ylabel, ttl in zip(
            axes, [on_off_by_dev, g_lrs_by_dev],
            ["ON/OFF Ratio", "G_LRS (S)"],
            ["ON/OFF Ratio by Device", "G_LRS by Device"]):
            valid = [(n, d) for n, d in zip(dev_names, dsets) if d]
            if not valid: continue
            ns, ds = zip(*valid)
            bp = ax.boxplot(ds, patch_artist=True, notch=False, vert=True,
                            medianprops=dict(color=PUB_COLORS[1], lw=2),
                            boxprops=dict(alpha=0.8))
            for i, (patch, dat) in enumerate(zip(bp["boxes"], ds)):
                patch.set_facecolor(PUB_COLORS[i % len(PUB_COLORS)])
                ax.scatter([i+1]*len(dat), dat, color="black", s=20, zorder=3, alpha=0.5)
            ax.set_xticks(range(1, len(ns)+1)); ax.set_xticklabels(ns, fontsize=7, rotation=30)
            ax.set_ylabel(ylabel, fontsize=9); ax.set_title(ttl, fontsize=9, fontweight="bold")
            ax.tick_params(which="both", direction="in", top=True, right=True, labelsize=8)
            ax.set_facecolor("white")
        self._box_fig.suptitle("Device-to-Device Statistical Comparison", fontsize=11, fontweight="bold")
        self._box_fig.tight_layout(pad=1.5)
        self._box_canvas.draw_idle()

    def _show_stats(self):
        if not self._all_stats: messagebox.showinfo("Stats", "No data yet."); return
        lines = ["=== VARIABILITY STATISTICS ===\n"]
        import statistics as stat
        for s in self._all_stats:
            dev = s["dev"]; st = s["stats"]
            lines.append(f"Device: {dev['name']} | Dia:{dev['dia']}µm | {dev['te']}/{dev['thick']}nm/{dev['be']}")
            for lbl, vals in [("ON/OFF", st["on_off"]), ("G_LRS(S)", st["g_lrs"]), ("G_HRS(S)", st["g_hrs"])]:
                if vals:
                    mu = sum(vals)/len(vals)
                    sig = stat.stdev(vals) if len(vals) > 1 else 0.0
                    lines.append(f"  {lbl}: μ={mu:.4g}  σ={sig:.4g}  σ/μ={100*sig/mu if mu else 0:.1f}%  n={len(vals)}")
            lines.append("")
        win = tk.Toplevel(self); win.title("Statistics Summary"); win.configure(bg=C["panel"])
        txt = scrolledtext.ScrolledText(win, bg=C["entry"], fg=C["text"], relief="flat", highlightthickness=1, highlightbackground=C["border"], font=FONT_MONO, width=70, height=30)
        txt.pack(fill="both", expand=True, padx=8, pady=8)
        txt.insert("end", "\n".join(lines))

    def _export_all(self):
        if not self._all_stats: return
        def _z(w):
            w.writerow(["Device","Dia(µm)","TE","Thick(nm)","BE","ON/OFF","G_LRS(S)","G_HRS(S)"])
            for s in self._all_stats:
                dev = s["dev"]
                for oo, gl, gh in zip(s["stats"]["on_off"], s["stats"]["g_lrs"], s["stats"]["g_hrs"]):
                    w.writerow([dev["name"], dev["dia"], dev["te"], dev["thick"], dev["be"], oo, gl, gh])
        self.manual_export("Variability_Stats", ["Device","Dia","TE","Thick","BE","ON/OFF","G_LRS","G_HRS"], lambda w: _z(w))


class ConductionMechanismTab(BaseMeasurementTab):
    def __init__(self, parent, get_driver, log, status, app_vars, app_state):
        super().__init__(parent, get_driver, log, status, app_vars, app_state)
        self._iv_data = {}
        self._fit_results = []
        self._build()

    def _build(self):
        strip = tk.Frame(self, bg=C["strip"]); strip.pack(fill="x")
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x")
        tk.Label(strip, text="  CONDUCTION MECHANISM ANALYSIS", bg=C["strip"],
                 fg=C["accent3"], font=FONT_HEADER).pack(side="left", padx=10, pady=6)
        self._run_btn = mk_button(strip, "▶ MEASURE I-V", self._run_iv,
                                  color=C["btn"], fg=C["accent3"], font=FONT_HEADER)
        self._run_btn.pack(side="left", padx=4, pady=4)
        mk_button(strip, "FIT ALL", self._fit_all, color=C["btn"]).pack(side="left", padx=4)
        mk_button(strip, "■ STOP", self.safe_stop, color=C["btn"], fg=C["red"]).pack(side="left", padx=4)
        mk_button(strip, "CSV", self._export_fit, color=C["btn"]).pack(side="left", padx=4)
        mk_button(strip, "Clear", self._clear_data, color=C["btn"]).pack(side="left", padx=4)

        self._prog = ProgressBar(self); self._prog.pack(fill="x")
        body = tk.Frame(self, bg=C["panel"]); body.pack(fill="both", expand=True)
        _, L = scrolled_frame(body, width=310)
        R = tk.Frame(body, bg=C["panel"]); R.pack(side="left", fill="both", expand=True, padx=4, pady=4)

        tf = mk_lf(L, "Temperature Series (Probe Station)", C["accent3"]); tf.pack(fill="x", padx=6, pady=(6,2))
        tk.Label(tf, text="Temps (°C), comma-sep:", bg=C["panel"], fg=C["text2"], font=FONT_LABEL).pack(anchor="w", padx=6)
        self._temps_var = tk.StringVar(value="25,50,75,100,125,150")
        mk_entry(tf, textvariable=self._temps_var, width=28).pack(fill="x", padx=6, pady=2)
        tk.Label(tf, text="Stabilization wait (s):", bg=C["panel"], fg=C["text2"], font=FONT_LABEL).pack(anchor="w", padx=6)
        self._wait_var = tk.StringVar(value="60")
        mk_entry(tf, textvariable=self._wait_var, width=10).pack(anchor="w", padx=6, pady=2)

        di_f = mk_lf(L, "Device Info", C["accent4"]); di_f.pack(fill="x", padx=6, pady=2); di_f.columnconfigure(1, weight=1)
        self._di = {}
        for i, (lbl, key, dflt) in enumerate([
            ("Area (µm²)",  "area",   "1963"),
            ("Thick d (nm)","thick",  "8"),
            ("ε_r (oxide)", "eps_r",  "25"),
            ("m* / m₀",     "mstar",  "0.3"),
            ("TE Work Φ(eV)","phi_m", "4.55"),
            ("χ oxide (eV)","chi",    "2.5"),
        ]):
            self._di[key] = tk.StringVar(value=dflt); param_row(di_f, i, lbl, self._di[key], label_width=15)

        sp_f = mk_lf(L, "Sweep Parameters", C["accent"]); sp_f.pack(fill="x", padx=6, pady=2); sp_f.columnconfigure(1, weight=1)
        self._sp = {}
        for i, (lbl, key, dflt) in enumerate([
            ("V_pos (V)",     "vpos",  "3.0"),
            ("V_neg (V)",     "vneg", "-3.0"),
            ("V_step (V)",    "vstep", "0.05"),
            ("Compliance (A)","comp",  "0.1"),
            ("NPLC",          "nplc",  "0.5"),
            ("Delay (s)",     "delay", "1e-3"),
        ]):
            self._sp[key] = tk.StringVar(value=dflt); param_row(sp_f, i, lbl, self._sp[key], label_width=15)

        fit_f = mk_lf(L, "Fit Modes", C["accent2"]); fit_f.pack(fill="x", padx=6, pady=2)
        self._fit_vars = {}
        for mode in ["Schottky (ln J vs E^0.5)", "Poole-Frenkel (ln J/E vs E^0.5)",
                     "SCLC (ln J vs ln V)", "Fowler-Nordheim (ln J/E² vs 1/E)",
                     "Ohmic (ln J vs ln V, slope=1)", "Barrier vs Temp (φ_Bn vs 1/T)"]:
            v = tk.BooleanVar(value=True); self._fit_vars[mode] = v
            tk.Checkbutton(fit_f, text=mode, variable=v, bg=C["panel"], fg=C["text2"],
                           font=FONT_SMALL, selectcolor=C["entry"], activebackground=C["panel"]).pack(anchor="w", padx=6, pady=1)

        nb_p = ttk.Notebook(R); nb_p.pack(fill="both", expand=True)
        self._pf_raw  = tk.Frame(nb_p, bg=C["panel"])
        self._pf_sch  = tk.Frame(nb_p, bg=C["panel"])
        self._pf_pf   = tk.Frame(nb_p, bg=C["panel"])
        self._pf_sclc = tk.Frame(nb_p, bg=C["panel"])
        self._pf_fn   = tk.Frame(nb_p, bg=C["panel"])
        self._pf_bar  = tk.Frame(nb_p, bg=C["panel"])
        nb_p.add(self._pf_raw,  text="  I-V Raw  ")
        nb_p.add(self._pf_sch,  text="  Schottky  ")
        nb_p.add(self._pf_pf,   text="  Poole-Frenkel  ")
        nb_p.add(self._pf_sclc, text="  SCLC  ")
        nb_p.add(self._pf_fn,   text="  F-N  ")
        nb_p.add(self._pf_bar,  text="  Barrier vs T  ")

        self._raw_plot = DualLivePlot(self._pf_raw, "I-V Temperature Series", "Voltage (V)", "Current (A)")
        self._raw_plot.pack(fill="both", expand=True)

        for attr, pf, ttl, xl, yl in [
            ("_sch_plot",  self._pf_sch,  "Schottky: ln(J) vs E^0.5",        "E^0.5 (V/m)^0.5", "ln(J)  [A/m²]"),
            ("_pf_plot",   self._pf_pf,   "Poole-Frenkel: ln(J/E) vs E^0.5", "E^0.5 (V/m)^0.5", "ln(J/E) [A·m/V]"),
            ("_sclc_plot", self._pf_sclc, "SCLC: ln(J) vs ln(V)",             "ln(V)",            "ln|J|"),
            ("_fn_plot",   self._pf_fn,   "Fowler-Nordheim: ln(J/E²) vs 1/E", "1/E  (m/V)",       "ln(J/E²)"),
        ]:
            pl = LivePlot(pf, ttl, xl, yl); pl.pack(fill="both", expand=True); setattr(self, attr, pl)

        if MATPLOTLIB_OK:
            self._bar_fig = mk_figure((9,4), facecolor="white")
            self._bar_canvas = FigureCanvasTkAgg(self._bar_fig, master=self._pf_bar)
            self._bar_canvas.get_tk_widget().pack(fill="both", expand=True)

    def _clear_data(self):
        self._iv_data.clear(); self._fit_results.clear()
        self._raw_plot.clear(); self._sch_plot.clear()
        self._pf_plot.clear(); self._sclc_plot.clear(); self._fn_plot.clear()
        if MATPLOTLIB_OK: self._bar_fig.clear(); self._bar_canvas.draw()

    def _run_iv(self):
        drv = self.get_drv()
        if not drv: self.log.log("Not connected.", "err"); return
        temps_str = self._temps_var.get()
        try: temps = [float(t.strip()) for t in temps_str.replace(";",",").split(",")]
        except: self.log.log("Invalid temperature list.", "err"); return
        wait_s = float(self._wait_var.get())
        v_pos = float(self._sp["vpos"].get()); v_neg = float(self._sp["vneg"].get())
        v_step = float(self._sp["vstep"].get()); comp = float(self._sp["comp"].get())
        nplc = float(self._sp["nplc"].get()); delay = float(self._sp["delay"].get())

        self._q = queue.Queue()
        self._iv_data.clear(); self._raw_plot.clear()

        def _task(evt):
            for ti, temp in enumerate(temps):
                if evt.is_set(): break
                self._q.put({"type":"wait_temp","temp":temp,"ti":ti,"total":len(temps)})
                deadline = time.time() + wait_s
                while time.time() < deadline and not evt.is_set():
                    remain = deadline - time.time()
                    self._q.put({"type":"countdown","remain":remain})
                    time.sleep(2.0)
                if evt.is_set(): break
                csv_path = init_live_save(self.app_vars, f"CondMech_T{int(temp)}C", ti+1,
                    ["Voltage(V)","Current(A)","Conductance(S)"], self.app_state.get("seq_folder"))
                def _prog(pct, row, refs, _c=csv_path):
                    append_live_save(_c, row)
                    self._q.put({"type":"prog","pct":(ti+pct/100)/len(temps)*100})
                res = drv.sweep_true_zero(v_pos, v_neg, v_step, comp, nplc, delay, _prog, evt)
                self._q.put({"type":"iv_done","temp":temp,"ti":ti,"data":res})
            return True

        self._acq = AcqThread(_task, self._q)
        self._acq.start(); self._run_btn.config(state="disabled")
        self.status.busy("Temperature I-V series running…"); self._poll_cond()

    def _poll_cond(self):
        try:
            while True:
                msg = self._q.get_nowait()
                mtype = msg.get("type","")
                if mtype == "prog": self._prog.set(msg["pct"])
                elif mtype == "wait_temp":
                    self.log.log(f"[PROBE] Set chuck to {msg['temp']}°C - waiting {self._wait_var.get()}s stabilization [{msg['ti']+1}/{msg['total']}]", "warn")
                elif mtype == "countdown":
                    self.status.busy(f"Stabilizing… {msg['remain']:.0f}s remaining")
                elif mtype == "iv_done":
                    d = msg["data"]; temp = msg["temp"]
                    self._iv_data[temp] = d
                    self._raw_plot.add_trace(d["voltages"], d["currents"],
                                             label=f"T={temp:.0f}°C", color_idx=msg["ti"])
                    self.log.log(f"I-V done at T={temp}°C", "ok")
                elif mtype == "done":
                    self._prog.set(100,"Done"); self._run_btn.config(state="normal")
                    self.status.ok("Temperature I-V series complete. Run FIT ALL.")
                    self.finish_run(); return
                elif mtype == "error":
                    self.log.log(f"Cond error: {msg['message']}", "err")
                    self._run_btn.config(state="normal"); return
        except queue.Empty: pass
        self.start_poll(self._poll_cond)

    def _fit_all(self):
        if not self._iv_data:
            messagebox.showinfo("Fit", "No I-V data collected yet."); return
        if not NUMPY_OK: messagebox.showinfo("Fit", "NumPy required for fitting."); return
        try:
            area_m2 = float(self._di["area"].get()) * 1e-12
            d_nm    = float(self._di["thick"].get())
            d_m     = d_nm * 1e-9
            eps_r   = float(self._di["eps_r"].get())
            eps_i   = eps_r * 8.854e-12
            mstar   = float(self._di["mstar"].get())
            phi_m   = float(self._di["phi_m"].get())
            chi_ox  = float(self._di["chi"].get())
        except Exception as e:
            messagebox.showerror("Fit", f"Device parameter error: {e}"); return

        self._sch_plot.clear(); self._pf_plot.clear()
        self._sclc_plot.clear(); self._fn_plot.clear()
        self._fit_results.clear()

        phi_bn_vals = []; temp_K_list = []

        for ti, (temp_c, d) in enumerate(sorted(self._iv_data.items())):
            temp_K = temp_c + 273.15
            vs = d["voltages"]; cs = d["currents"]
            pos_mask = [(v, c) for v, c in zip(vs, cs) if v > 0.1 and abs(c) > 1e-15]
            if len(pos_mask) < 3: continue
            vp = np.array([x[0] for x in pos_mask])
            cp = np.array([x[1] for x in pos_mask])
            Jp = np.array([abs(c) / area_m2 for c in cp])
            Ep = vp / d_m

            E_half = np.array([e**0.5 for e in Ep])
            lnJ    = np.array([math.log(max(j, 1e-30)) for j in Jp])
            lnJE   = np.array([math.log(max(j/e, 1e-30)) for j, e in zip(Jp, Ep)])
            lnV    = np.array([math.log(max(v, 1e-10)) for v in vp])
            lnJJ   = np.array([math.log(max(j, 1e-30)) for j in Jp])
            lnJE2  = np.array([math.log(max(j/max(e**2, 1e-30), 1e-30)) for j, e in zip(Jp, Ep)])
            invE   = np.array([1.0/max(e, 1e-30) for e in Ep])

            color_idx = ti

            if self._fit_vars.get("Schottky (ln J vs E^0.5)", tk.BooleanVar(value=True)).get():
                self._sch_plot.add_trace(list(E_half), list(lnJ),
                                         label=f"T={temp_c:.0f}°C", color_idx=color_idx, scatter=True, lw=0)
                m, b = self._linfit(list(E_half), list(lnJ))
                xfit = [min(E_half), max(E_half)]
                yfit = [m*x+b for x in xfit]
                self._sch_plot.add_trace(xfit, yfit, color_idx=color_idx, ls="--", lw=1.5)
                kB = 8.617e-5
                A_star = 1.2e6
                phi_bn = (math.log(A_star * temp_K**2) - b) * kB * temp_K if abs(b) < 1000 else None
                beta_sch_theory = (1.0/(kB*temp_K)) * math.sqrt(1.602e-19 / (4*math.pi*eps_i)) if eps_i > 0 else None
                result = {"temp_C": temp_c, "temp_K": temp_K, "model": "Schottky",
                          "slope": m, "intercept": b, "phi_Bn_eV": phi_bn,
                          "beta_theory": beta_sch_theory}
                self._fit_results.append(result)
                if phi_bn: phi_bn_vals.append(phi_bn); temp_K_list.append(temp_K)
                self.log.log(f"Schottky T={temp_c}°C: slope={m:.4g}, φ_Bn={phi_bn:.3f}eV" if phi_bn else f"Schottky T={temp_c}°C: slope={m:.4g}", "ok")

            if self._fit_vars.get("Poole-Frenkel (ln J/E vs E^0.5)", tk.BooleanVar(value=True)).get():
                self._pf_plot.add_trace(list(E_half), list(lnJE),
                                        label=f"T={temp_c:.0f}°C", color_idx=color_idx, scatter=True, lw=0)
                m, b = self._linfit(list(E_half), list(lnJE))
                xfit = [min(E_half), max(E_half)]
                self._pf_plot.add_trace(xfit, [m*x+b for x in xfit], color_idx=color_idx, ls="--", lw=1.5)
                kB = 8.617e-5
                phi_t = -b * kB * temp_K if abs(b) < 5000 else None
                self._fit_results.append({"temp_C": temp_c, "model":"PF", "slope":m, "intercept":b, "phi_trap_eV":phi_t})
                self.log.log(f"PF T={temp_c}°C: slope={m:.4g}, φ_trap={phi_t:.3f}eV" if phi_t else f"PF T={temp_c}°C: slope={m:.4g}", "ok")

            if self._fit_vars.get("SCLC (ln J vs ln V)", tk.BooleanVar(value=True)).get():
                self._sclc_plot.add_trace(list(lnV), list(lnJJ),
                                          label=f"T={temp_c:.0f}°C", color_idx=color_idx, scatter=True, lw=0)
                m, b = self._linfit(list(lnV), list(lnJJ))
                xfit = [min(lnV), max(lnV)]
                self._sclc_plot.add_trace(xfit, [m*x+b for x in xfit], color_idx=color_idx, ls="--", lw=1.5)
                self._fit_results.append({"temp_C": temp_c, "model":"SCLC", "slope_m":m,
                                          "interpretation": "ohmic" if abs(m-1)<0.3 else ("trap-free SCLC" if abs(m-2)<0.3 else f"trap-dist (m={m:.2f})")})
                self.log.log(f"SCLC T={temp_c}°C: slope m={m:.2f} → {self._fit_results[-1]['interpretation']}", "ok")

            if self._fit_vars.get("Fowler-Nordheim (ln J/E² vs 1/E)", tk.BooleanVar(value=True)).get():
                self._fn_plot.add_trace(list(invE), list(lnJE2),
                                        label=f"T={temp_c:.0f}°C", color_idx=color_idx, scatter=True, lw=0)
                m, b = self._linfit(list(invE), list(lnJE2))
                xfit = [min(invE), max(invE)]
                self._fn_plot.add_trace(xfit, [m*x+b for x in xfit], color_idx=color_idx, ls="--", lw=1.5)
                h = 6.626e-34; m0 = 9.109e-31; q = 1.602e-19
                B_fn = abs(m) if m < 0 else abs(m)
                phi_fn = (B_fn * 3 * q * h / (8 * math.pi * math.sqrt(2 * mstar * m0 * q)))**(2/3) / q if B_fn > 0 else None
                self._fit_results.append({"temp_C": temp_c, "model":"FN", "slope":m, "intercept":b, "phi_FN_eV":phi_fn})
                self.log.log(f"FN T={temp_c}°C: slope={m:.4g}, φ_FN={phi_fn:.3f}eV" if phi_fn else f"FN T={temp_c}°C: slope={m:.4g}", "ok")

        if phi_bn_vals and len(phi_bn_vals) > 1 and MATPLOTLIB_OK:
            self._plot_barrier_vs_temp(phi_bn_vals, temp_K_list, phi_m, chi_ox)

        messagebox.showinfo("Fit Complete", f"Fitted {len(self._fit_results)} models.\nCheck plot tabs for results.\nSee log for extracted parameters.")

    def _plot_barrier_vs_temp(self, phi_bn_list, temp_K_list, phi_m, chi_ox):
        self._bar_fig.clear()
        ax1 = self._bar_fig.add_subplot(121)
        ax2 = self._bar_fig.add_subplot(122)

        ax1.scatter(temp_K_list, phi_bn_list, color=PUB_COLORS[0], s=60, zorder=3, label="Exp. φ_Bn")
        if NUMPY_OK and len(phi_bn_list) > 2:
            m, b = self._linfit(temp_K_list, phi_bn_list)
            xf = [min(temp_K_list), max(temp_K_list)]
            ax1.plot(xf, [m*x+b for x in xf], color=PUB_COLORS[1], ls="--", lw=1.5, label=f"Linear fit")
        ax1.axhline(phi_m - chi_ox, color=PUB_COLORS[2], ls=":", lw=1.5, label=f"S-M φ_Bn={phi_m-chi_ox:.2f}eV")
        ax1.set_xlabel("Temperature (K)", fontsize=9); ax1.set_ylabel("φ_Bn (eV)", fontsize=9)
        ax1.set_title("Schottky Barrier vs Temperature", fontsize=9, fontweight="bold")
        ax1.legend(fontsize=7); ax1.tick_params(which="both", direction="in", top=True, right=True)
        ax1.set_facecolor("white")

        inv_T = [1000.0/t for t in temp_K_list]
        kB = 8.617e-5
        ax2.scatter(inv_T, phi_bn_list, color=PUB_COLORS[3], s=60, zorder=3)
        ax2.set_xlabel("1000/T  (K⁻¹)", fontsize=9); ax2.set_ylabel("φ_Bn (eV)", fontsize=9)
        ax2.set_title("Richardson Plot - Barrier Uniformity", fontsize=9, fontweight="bold")
        ax2.tick_params(which="both", direction="in", top=True, right=True); ax2.set_facecolor("white")

        self._bar_fig.suptitle("Band Alignment Analysis - Schottky Barrier", fontsize=11, fontweight="bold")
        self._bar_fig.tight_layout(pad=1.5)
        self._bar_canvas.draw_idle()
        auto_save_fig(self.app_vars, "ConductionMechanism_Barrier", 1, self._bar_fig, self.app_state.get("seq_folder"))

    @staticmethod
    def _linfit(xs, ys):
        n = len(xs)
        if n < 2: return (0.0, ys[0] if ys else 0.0)
        sx = sum(xs); sy = sum(ys); sxy = sum(x*y for x,y in zip(xs,ys)); sx2 = sum(x*x for x in xs)
        denom = n*sx2 - sx*sx
        if abs(denom) < 1e-30: return (0.0, sy/n)
        m = (n*sxy - sx*sy) / denom
        b = (sy - m*sx) / n
        return (m, b)

    def _export_fit(self):
        if not self._fit_results: return
        def _z(w):
            w.writerow(["Temp(C)","Model","Slope","Intercept","phi_Bn(eV)","phi_trap(eV)","phi_FN(eV)","SCLC_interpretation"])
            for r in self._fit_results:
                w.writerow([r.get("temp_C",""), r.get("model",""), r.get("slope",""),
                            r.get("intercept",""), r.get("phi_Bn_eV",""), r.get("phi_trap_eV",""),
                            r.get("phi_FN_eV",""), r.get("interpretation","")])
        self.manual_export("ConductionMechanism_Fits",
            ["Temp(C)","Model","Slope","Intercept","phi_Bn","phi_trap","phi_FN","SCLC_interp"], lambda w: _z(w))


class NeuronTestTab(BaseMeasurementTab):
    def __init__(self, parent, get_driver, log, status, app_vars, app_state):
        super().__init__(parent, get_driver, log, status, app_vars, app_state)
        self._runs = []; self._build()

    def _build(self):
        strip = tk.Frame(self, bg=C["strip"]); strip.pack(fill="x")
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x")
        tk.Label(strip, text="  NEURON TESTING - LIF / EPSC / Threshold / Integration", bg=C["strip"],
                 fg=C["accent2"], font=FONT_HEADER).pack(side="left", padx=10, pady=6)
        self._run_btn = mk_button(strip, "▶ RUN NEURON", self._run,
                                  color=C["btn"], fg=C["accent2"], font=FONT_HEADER)
        self._run_btn.pack(side="left", padx=4, pady=4)
        mk_button(strip, "■ STOP", self.safe_stop, color=C["btn"], fg=C["red"]).pack(side="left", padx=4)
        mk_button(strip, "CSV", self._export, color=C["btn"]).pack(side="left", padx=4)

        self._prog = ProgressBar(self); self._prog.pack(fill="x")
        body = tk.Frame(self, bg=C["panel"]); body.pack(fill="both", expand=True)
        _, L = scrolled_frame(body, width=310)
        R = tk.Frame(body, bg=C["panel"]); R.pack(side="left", fill="both", expand=True, padx=4, pady=4)

        wf = mk_lf(L, "Input Waveform (Defined)", C["accent2"]); wf.pack(fill="x", padx=6, pady=(6,2)); wf.columnconfigure(1, weight=1)
        tk.Label(wf, text="Mode", bg=C["panel"], fg=C["text2"], font=FONT_LABEL, width=15, anchor="e").grid(row=0, column=0, sticky="e", padx=(8,4), pady=3)
        self._wf_mode = tk.StringVar(value="Rectangular Pulses")
        mk_combobox(wf, values=["Rectangular Pulses", "Sinusoidal Burst", "Ramp Integrate",
                                 "Irregular Spike Train", "Custom (CSV)"], width=22,
                    textvariable=self._wf_mode).grid(row=0, column=1, sticky="ew", padx=(0,8), pady=3)
        self._wp = {}
        for i, (lbl, key, dflt) in enumerate([
            ("V_high (V)",     "vh",      "1.5"),
            ("V_low (V)",      "vl",      "0.0"),
            ("t_pulse (ms)",   "tp",      "5"),
            ("t_inter (ms)",   "ti",      "10"),
            ("N pulses",       "np",      "20"),
            ("V_threshold (V)","vth",     "0.5"),
            ("t_refract (ms)", "tref",    "20"),
            ("Compliance (A)", "comp",    "0.1"),
            ("NPLC",           "nplc",    "0.1"),
            ("V_read (V)",     "vread",   "0.1"),
            ("t_read (ms)",    "tread",   "5"),
            ("N Runs",         "nrun",    "1"),
        ], start=1):
            self._wp[key] = tk.StringVar(value=dflt); param_row(wf, i, lbl, self._wp[key], label_width=15)

        ei_f = mk_lf(L, "EPSC / IPSC", C["accent4"]); ei_f.pack(fill="x", padx=6, pady=2)
        self._epsc_var = tk.BooleanVar(value=True)
        self._ipsc_var = tk.BooleanVar(value=True)
        tk.Checkbutton(ei_f, text="EPSC (excitatory - positive pulses)", variable=self._epsc_var,
                       bg=C["panel"], fg=C["accent3"], font=FONT_SMALL, selectcolor=C["entry"],
                       activebackground=C["panel"]).pack(anchor="w", padx=6, pady=2)
        tk.Checkbutton(ei_f, text="IPSC (inhibitory - negative pulses)", variable=self._ipsc_var,
                       bg=C["panel"], fg=C["red"], font=FONT_SMALL, selectcolor=C["entry"],
                       activebackground=C["panel"]).pack(anchor="w", padx=6, pady=2)

        an_f = mk_lf(L, "Analysis", C["muted"]); an_f.pack(fill="x", padx=6, pady=2)
        self._fire_rate_var = tk.BooleanVar(value=True)
        self._tau_fit_var   = tk.BooleanVar(value=True)
        tk.Checkbutton(an_f, text="Extract firing rate vs V_in", variable=self._fire_rate_var,
                       bg=C["panel"], fg=C["text2"], font=FONT_SMALL, selectcolor=C["entry"],
                       activebackground=C["panel"]).pack(anchor="w", padx=6, pady=1)
        tk.Checkbutton(an_f, text="Fit integration time constant τ", variable=self._tau_fit_var,
                       bg=C["panel"], fg=C["text2"], font=FONT_SMALL, selectcolor=C["entry"],
                       activebackground=C["panel"]).pack(anchor="w", padx=6, pady=1)

        nb_p = ttk.Notebook(R); nb_p.pack(fill="both", expand=True)
        self._pf_wave  = tk.Frame(nb_p, bg=C["panel"])
        self._pf_resp  = tk.Frame(nb_p, bg=C["panel"])
        self._pf_fire  = tk.Frame(nb_p, bg=C["panel"])
        self._pf_epsc  = tk.Frame(nb_p, bg=C["panel"])
        nb_p.add(self._pf_wave, text="  Input V(t) + Response  ")
        nb_p.add(self._pf_resp, text="  G(t) / Integration  ")
        nb_p.add(self._pf_fire, text="  Firing Rate  ")
        nb_p.add(self._pf_epsc, text="  EPSC / IPSC  ")

        if MATPLOTLIB_OK:
            self._wave_fig = mk_figure((10, 4.5), facecolor="white")
            self._wave_canvas = FigureCanvasTkAgg(self._wave_fig, master=self._pf_wave)
            self._wave_canvas.get_tk_widget().pack(fill="both", expand=True)

        self._g_plot    = LivePlot(self._pf_resp, "Conductance vs Time (Integration)", "Time (ms)", "Conductance (S)")
        self._g_plot.pack(fill="both", expand=True)
        self._fire_plot = LivePlot(self._pf_fire, "Firing Rate vs Pulse Amplitude", "V_pulse (V)", "Spikes / s")
        self._fire_plot.pack(fill="both", expand=True)
        self._epsc_plot = LivePlot(self._pf_epsc, "EPSC / IPSC - Current vs Time", "Time (ms)", "Current (A)")
        self._epsc_plot.pack(fill="both", expand=True)

    def _build_waveform(self):
        mode  = self._wf_mode.get()
        vh    = float(self._wp["vh"].get()); vl = float(self._wp["vl"].get())
        tp    = float(self._wp["tp"].get());  ti = float(self._wp["ti"].get())
        np_   = int(self._wp["np"].get())
        pulses = []
        if mode == "Rectangular Pulses":
            for k in range(np_):
                pulses.append((vh, tp, f"P{k+1}"))
                if ti > 0: pulses.append((vl, ti, f"rest{k+1}"))
        elif mode == "Sinusoidal Burst":
            for k in range(np_):
                phase = math.pi * k / max(np_-1, 1)
                amp = vh * math.sin(phase)
                pulses.append((amp, tp, f"S{k+1}"))
                if ti > 0: pulses.append((vl, ti, f"rest{k+1}"))
        elif mode == "Ramp Integrate":
            for k in range(np_):
                amp = vl + (vh - vl) * k / max(np_-1, 1)
                pulses.append((amp, tp, f"R{k+1}"))
                if ti > 0: pulses.append((vl, ti/2, f"r{k+1}"))
        elif mode == "Irregular Spike Train":
            import random; random.seed(42)
            for k in range(np_):
                amp = random.uniform(vl, vh)
                dur = random.uniform(tp*0.5, tp*2.0)
                gap = random.uniform(ti*0.5, ti*2.0)
                pulses.append((amp, dur, f"I{k+1}"))
                pulses.append((vl, gap, f"g{k+1}"))
        return pulses

    def _run(self):
        drv = self.get_drv()
        if not drv: self.log.log("Not connected.", "err"); return
        runs    = int(self._wp["nrun"].get())
        v_thr   = float(self._wp["vth"].get())
        t_ref   = float(self._wp["tref"].get())
        comp    = float(self._wp["comp"].get())
        nplc    = float(self._wp["nplc"].get())
        v_read  = float(self._wp["vread"].get())
        t_read  = float(self._wp["tread"].get()) * 1e-3

        try: pulses = self._build_waveform()
        except Exception as e: self.log.log(f"Waveform error: {e}", "err"); return

        self._q = queue.Queue(); self._runs = []
        if MATPLOTLIB_OK: self._wave_fig.clear()
        self._g_plot.clear(); self._fire_plot.clear(); self._epsc_plot.clear()

        def _task(evt):
            for r in range(1, runs + 1):
                if evt.is_set(): break
                csv_path = init_live_save(self.app_vars, "Neuron_Test", r,
                    ["Time(ms)","V_defined(V)","I_measured(A)","G_meas(S)","Event"],
                    self.app_state.get("seq_folder"))
                drv.configure_voltage_source(comp, nplc, 0.0)
                t_cursor = 0.0
                t_list=[]; v_def=[]; i_meas=[]; g_meas=[]; events=[]
                charges = []; charge_acc = 0.0; spikes = []; in_refract = False; t_refract_end = 0.0
                total_ms = sum(p[1] for p in pulses)
                done_ms  = 0.0

                for v_amp, t_hold_ms, label in pulses:
                    if evt.is_set(): break
                    t_hold_s = max(t_hold_ms * 1e-3, 0.002)
                    drv.set_voltage(v_amp); drv.output_on()
                    time.sleep(t_hold_s)
                    I = drv.measure_current()
                    drv.set_voltage(0.0); drv.output_off()

                    I_read = drv.read_pulse(v_read, t_read)
                    G = abs(I_read / v_read) if abs(v_read) > 1e-9 else 0.0

                    t_cursor += t_hold_ms
                    done_ms  += t_hold_ms

                    dQ = abs(I) * t_hold_s
                    charge_acc += dQ

                    event = ""
                    if in_refract and t_cursor >= t_refract_end:
                        in_refract = False
                    if not in_refract and abs(v_amp) >= v_thr:
                        spikes.append(t_cursor)
                        in_refract = True; t_refract_end = t_cursor + t_ref
                        charge_acc = 0.0; event = "FIRE"

                    t_list.append(t_cursor); v_def.append(v_amp)
                    i_meas.append(I); g_meas.append(G)
                    events.append(event); charges.append(charge_acc)

                    append_live_save(csv_path, [t_cursor, v_amp, I, G, event])
                    pct = done_ms / max(total_ms, 1e-9) * 100
                    self._q.put({"type":"prog","pct":pct})
                    self._q.put({"type":"step","t":t_cursor,"v":v_amp,"I":I,"G":G,"ev":event})

                drv.output_off()
                fire_rate = len(spikes) / (total_ms * 1e-3) if total_ms > 0 else 0.0
                self._q.put({"type":"run_done","run":r,
                             "t":t_list,"v_def":v_def,"i_meas":i_meas,"g_meas":g_meas,
                             "events":events,"spikes":spikes,"fire_rate":fire_rate})
            return True

        self._acq = AcqThread(_task, self._q)
        self._acq.start(); self._run_btn.config(state="disabled")
        self.status.busy("Neuron test running…"); self._poll_neuron()

    def _poll_neuron(self):
        try:
            while True:
                msg = self._q.get_nowait()
                mtype = msg.get("type","")
                if mtype == "prog": self._prog.set(msg["pct"])
                elif mtype == "step":
                    self._g_plot.add_trace([msg["t"]], [msg["G"]], color_idx=0, lw=0, marker=".", alpha=0.7)
                elif mtype == "run_done":
                    d = msg; self._runs.append(d)
                    if MATPLOTLIB_OK: self._plot_waveform(d)
                    self._g_plot.clear()
                    self._g_plot.add_trace(d["t"], d["g_meas"], label=f"G(t) R{d['run']}", color_idx=d["run"]-1)
                    for sp in d["spikes"]:
                        self._g_plot.vline(sp, color=C["red"])
                    self._epsc_plot.clear()
                    self._epsc_plot.add_trace(d["t"], d["i_meas"], label=f"I_meas R{d['run']}", color_idx=d["run"]-1)
                    self.log.log(f"Neuron R{d['run']}: {len(d['spikes'])} spikes, fire rate={d['fire_rate']:.2f} Hz", "ok")
                    auto_save_fig(self.app_vars, "Neuron_Test", d["run"], self._wave_fig, self.app_state.get("seq_folder"))
                elif mtype == "done":
                    self._prog.set(100,"Done"); self._run_btn.config(state="normal")
                    self.status.ok("Neuron test complete."); self.finish_run(); return
                elif mtype == "error":
                    self.log.log(f"Neuron error: {msg['message']}", "err")
                    self._run_btn.config(state="normal"); return
        except queue.Empty: pass
        self.start_poll(self._poll_neuron)

    def _plot_waveform(self, d):
        if not MATPLOTLIB_OK: return
        self._wave_fig.clear()
        ax1 = self._wave_fig.add_subplot(211)
        ax2 = self._wave_fig.add_subplot(212, sharex=ax1)
        ax1.plot(d["t"], d["v_def"], color=PUB_COLORS[0], lw=1.5, label="Defined V_input(t)")
        ax1.set_ylabel("Voltage (V)", fontsize=9); ax1.set_title("Neuron Test - Input & Response", fontsize=10, fontweight="bold")
        ax1.legend(fontsize=8, loc="upper right"); ax1.tick_params(which="both", direction="in", top=True, right=True)
        ax1.set_facecolor("white"); ax1.grid(True, alpha=0.3, linestyle="--")
        ax2.plot(d["t"], [abs(i)*1e9 for i in d["i_meas"]], color=PUB_COLORS[1], lw=1.2, label="Measured |I|(t)")
        for sp in d["spikes"]:
            ax1.axvline(sp, color=PUB_COLORS[4], lw=1.0, ls="--", alpha=0.7)
            ax2.axvline(sp, color=PUB_COLORS[4], lw=1.0, ls="--", alpha=0.7)
        if d["spikes"]:
            ax2.axvline(d["spikes"][0], color=PUB_COLORS[4], lw=1.0, ls="--", label="Spike events")
        ax2.set_xlabel("Time (ms)", fontsize=9); ax2.set_ylabel("|Current| (nA)", fontsize=9)
        ax2.legend(fontsize=8, loc="upper right"); ax2.tick_params(which="both", direction="in", top=True, right=True)
        ax2.set_facecolor("white"); ax2.grid(True, alpha=0.3, linestyle="--")
        self._wave_fig.tight_layout(pad=1.2)
        self._wave_canvas.draw_idle()

    def _export(self):
        if not self._runs: return
        def _z(w):
            for item in self._runs:
                for t, v, i, g, ev in zip(item["t"], item["v_def"], item["i_meas"], item["g_meas"], item["events"]):
                    w.writerow([item["run"], t, v, i, g, ev])
        self.manual_export("Neuron_Test", ["Run","Time(ms)","V_def(V)","I_meas(A)","G(S)","Event"], lambda w: _z(w))


class AdaptiveLTPTab(BaseMeasurementTab):
    MAX_ATTEMPTS = 12
    NL_TARGET    = 1.0

    def __init__(self, parent, get_driver, log, status, app_vars, app_state):
        super().__init__(parent, get_driver, log, status, app_vars, app_state)
        self._attempts = []; self._best = None; self._build()

    def _build(self):
        strip = tk.Frame(self, bg=C["strip"]); strip.pack(fill="x")
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x")
        tk.Label(strip, text="  ADAPTIVE LTP/LTD - Auto-Optimization (Feedback)", bg=C["strip"],
                 fg=C["accent3"], font=FONT_HEADER).pack(side="left", padx=10, pady=6)
        self._run_btn = mk_button(strip, "▶ AUTO-OPTIMIZE", self._run,
                                  color=C["btn"], fg=C["accent3"], font=FONT_HEADER)
        self._run_btn.pack(side="left", padx=4, pady=4)
        mk_button(strip, "■ STOP", self.safe_stop, color=C["btn"], fg=C["red"]).pack(side="left", padx=4)
        mk_button(strip, "BEST", self._export_best, color=C["btn"]).pack(side="left", padx=4)
        mk_button(strip, "ALL", self._export_all, color=C["btn"]).pack(side="left", padx=4)

        self._prog = ProgressBar(self); self._prog.pack(fill="x")
        body = tk.Frame(self, bg=C["panel"]); body.pack(fill="both", expand=True)
        _, L = scrolled_frame(body, width=310)
        R = tk.Frame(body, bg=C["panel"]); R.pack(side="left", fill="both", expand=True, padx=4, pady=4)

        ip_f = mk_lf(L, "Initial Pulse Parameters", C["accent3"]); ip_f.pack(fill="x", padx=6, pady=(6,2)); ip_f.columnconfigure(1,weight=1)
        self._ip = {}
        for i, (lbl, key, dflt) in enumerate([
            ("V_pot (V)",      "v_pot",  "1.5"),
            ("V_dep (V)",      "v_dep",  "-1.5"),
            ("t_pot (ms)",     "t_pot",  "5"),
            ("t_dep (ms)",     "t_dep",  "5"),
            ("N_pot pulses",   "n_pot",  "50"),
            ("N_dep pulses",   "n_dep",  "50"),
            ("V_read (V)",     "vread",  "0.1"),
            ("t_read (ms)",    "tread",  "5"),
            ("Delay (ms)",     "delay",  "1"),
            ("Compliance (A)", "comp",   "0.1"),
            ("NPLC",           "nplc",   "0.1"),
        ]):
            self._ip[key] = tk.StringVar(value=dflt); param_row(ip_f, i, lbl, self._ip[key], label_width=16)

        og_f = mk_lf(L, "Optimization Goals", C["accent4"]); og_f.pack(fill="x", padx=6, pady=2); og_f.columnconfigure(1,weight=1)
        self._og = {}
        for i, (lbl, key, dflt) in enumerate([
            ("NL target (< X)",    "nl_tgt",  "1.0"),
            ("Max attempts",       "max_att", "12"),
            ("t_step factor",      "t_fact",  "1.5"),
            ("V_step factor",      "v_fact",  "1.1"),
            ("Min t_pulse (ms)",   "t_min",   "5"),
            ("Max t_pulse (ms)",   "t_max",   "50"),
            ("Min V_pulse (V)",    "v_min",   "0.5"),
            ("Max V_pulse (V)",    "v_max",   "3.0"),
        ]):
            self._og[key] = tk.StringVar(value=dflt); param_row(og_f, i, lbl, self._og[key], label_width=16)

        sf = mk_lf(L, "Search Strategy", C["accent2"]); sf.pack(fill="x", padx=6, pady=2)
        self._strat_var = tk.StringVar(value="Width → Amplitude → Scheme")
        mk_combobox(sf, values=["Width → Amplitude → Scheme",
                                 "Amplitude → Width → Scheme",
                                 "Width only", "Amplitude only"], width=26, textvariable=self._strat_var).pack(padx=6, pady=4)

        self._status_txt = scrolledtext.ScrolledText(L, height=8, bg=C["entry2"], fg=C["text2"], relief="flat", highlightthickness=1, highlightbackground=C["border"],
                                                      font=FONT_SMALL, bd=0, state="disabled")
        self._status_txt.pack(fill="x", padx=6, pady=4)

        nb_p = ttk.Notebook(R); nb_p.pack(fill="both", expand=True)
        self._pf_g   = tk.Frame(nb_p, bg=C["panel"])
        self._pf_nl  = tk.Frame(nb_p, bg=C["panel"])
        self._pf_v   = tk.Frame(nb_p, bg=C["panel"])
        nb_p.add(self._pf_g,  text="  G vs Pulse  ")
        nb_p.add(self._pf_nl, text="  NL vs Attempt  ")
        nb_p.add(self._pf_v,  text="  Pulse Trace (V vs t)  ")

        self._g_plot  = LivePlot(self._pf_g,  "LTP/LTD Conductance", "Pulse #", "G (S)")
        self._g_plot.pack(fill="both", expand=True)
        self._nl_plot = LivePlot(self._pf_nl, "NL vs Attempt", "Attempt #", "Nonlinearity NL")
        self._nl_plot.pack(fill="both", expand=True)

        if MATPLOTLIB_OK:
            self._vt_fig = mk_figure((9,3.5), facecolor="white")
            self._vt_canvas = FigureCanvasTkAgg(self._vt_fig, master=self._pf_v)
            self._vt_canvas.get_tk_widget().pack(fill="both", expand=True)

    def _opt_log(self, msg):
        self._status_txt.config(state="normal")
        self._status_txt.insert("end", f"{msg}\n")
        self._status_txt.see("end"); self._status_txt.config(state="disabled")
        self.log.log(msg, "info")

    @staticmethod
    def _compute_nl(gs, n_pot):
        pot_g = gs[:n_pot]; dep_g = gs[n_pot:]
        if len(pot_g) < 2: return 9999.0, 9999.0
        g_min = pot_g[0]; g_max = max(pot_g)
        g_rng = g_max - g_min if g_max > g_min else 1e-20
        nl_p = max(abs((pot_g[i] - (g_min + (i/(len(pot_g)-1))*g_rng)) / g_rng)
                   for i in range(len(pot_g)))
        if len(dep_g) < 2: return nl_p, 9999.0
        g_max2 = dep_g[0]; g_min2 = min(dep_g)
        g_rng2 = g_max2 - g_min2 if g_max2 > g_min2 else 1e-20
        nl_d = max(abs((dep_g[i] - (g_max2 - (i/(len(dep_g)-1))*g_rng2)) / g_rng2)
                   for i in range(len(dep_g)))
        return nl_p, nl_d

    def _run(self):
        drv = self.get_drv()
        if not drv: self.log.log("Not connected.", "err"); return

        v_pot = float(self._ip["v_pot"].get()); v_dep = float(self._ip["v_dep"].get())
        t_pot = float(self._ip["t_pot"].get()) * 1e-3
        t_dep = float(self._ip["t_dep"].get()) * 1e-3
        n_pot = int(self._ip["n_pot"].get()); n_dep = int(self._ip["n_dep"].get())
        v_read = float(self._ip["vread"].get()); t_read = float(self._ip["tread"].get()) * 1e-3
        delay  = float(self._ip["delay"].get()) * 1e-3
        comp   = float(self._ip["comp"].get()); nplc = float(self._ip["nplc"].get())
        nl_tgt = float(self._og["nl_tgt"].get()); max_att = int(self._og["max_att"].get())
        t_fact = float(self._og["t_fact"].get()); v_fact = float(self._og["v_fact"].get())
        t_min  = float(self._og["t_min"].get()) * 1e-3; t_max = float(self._og["t_max"].get()) * 1e-3
        v_min  = float(self._og["v_min"].get()); v_max  = float(self._og["v_max"].get())
        scheme_order = ["Constant Amp & Width", "Incremental Pulse Width", "Incremental Amplitude", "Logarithmic Amplitude"]

        self._q = queue.Queue(); self._attempts = []; self._best = None
        self._g_plot.clear(); self._nl_plot.clear()

        def _do_ltp_ltd_once(drv2, evt, v_p, v_d, t_p, t_d, n_p, n_d, scheme):
            SCHEMES = {"Constant Amp & Width": 0, "Incremental Pulse Width": 1,
                       "Incremental Amplitude": 2, "Logarithmic Amplitude": 3}
            si = SCHEMES.get(scheme, 0)
            if si == 0:
                pot_p = [(v_p, t_p)] * n_p; dep_p = [(v_d, t_d)] * n_d
            elif si == 1:
                t_s = t_min * 0.5
                pot_p = [(v_p, t_s + (t_p-t_s)*k/max(n_p-1,1)) for k in range(n_p)]
                dep_p = [(v_d, t_s + (t_d-t_s)*k/max(n_d-1,1)) for k in range(n_d)]
            elif si == 2:
                pot_p = [(v_min + (v_p-v_min)*k/max(n_p-1,1), t_p) for k in range(n_p)]
                dep_p = [(-v_min + (v_d+v_min)*k/max(n_d-1,1), t_d) for k in range(n_d)]
            else:
                import math as _m
                pot_p = [(v_min * (v_p/v_min)**((k/(max(n_p-1,1)))), t_p) for k in range(n_p)]
                dep_p = [(-v_min * (abs(v_d)/v_min)**((k/(max(n_d-1,1)))), t_d) for k in range(n_d)]

            gs, vs, phs, pulse_ts, pulse_vs = [], [], [], [], []
            t_cursor = 0.0
            drv2.configure_voltage_source(comp, nplc, 0.0)

            for phase, pulses in [("POT", pot_p), ("DEP", dep_p)]:
                for (v_amp, t_w) in pulses:
                    if evt.is_set(): return None
                    t_w_actual = clamp_pulse(t_w)
                    drv2.set_voltage(v_amp); drv2.output_on()
                    time.sleep(t_w_actual)
                    drv2.set_voltage(0.0); drv2.output_off()
                    pulse_ts.extend([t_cursor, t_cursor + t_w_actual*1000]); pulse_vs.extend([v_amp, 0.0])
                    t_cursor += t_w_actual * 1000 + delay * 1000
                    time.sleep(max(delay, MIN_DELAY_S))
                    I = drv2.read_pulse(v_read, t_read)
                    G = abs(I/v_read) if abs(v_read)>1e-9 else 0.0
                    gs.append(G); vs.append(v_amp); phs.append(phase)
            return gs, vs, phs, pulse_ts, pulse_vs

        def _task(evt):
            cur_t_p = t_pot; cur_t_d = t_dep
            cur_v_p = v_pot; cur_v_d = v_dep
            scheme_idx = 0
            best_nl = 9999.0; best_att = None

            for att in range(1, max_att + 1):
                if evt.is_set(): break
                scheme = scheme_order[min(scheme_idx, len(scheme_order)-1)]
                self._q.put({"type":"opt_log", "msg": f"Attempt {att}/{max_att}: {scheme}  V_pot={cur_v_p:.2f}V  t_pot={cur_t_p*1000:.1f}ms"})

                result = _do_ltp_ltd_once(drv, evt, cur_v_p, cur_v_d, cur_t_p, cur_t_d, n_pot, n_dep, scheme)
                if result is None: break
                gs, vs_out, phs, pt, pv = result

                csv_path = init_live_save(self.app_vars, f"AdaptiveLTP_att{att:02d}", att,
                    ["Pulse","G(S)","V_amp(V)","Phase","t_pot_ms","t_dep_ms","v_pot","v_dep","scheme"],
                    self.app_state.get("seq_folder"))
                for pi, (g, v_o, ph) in enumerate(zip(gs, vs_out, phs)):
                    append_live_save(csv_path, [pi+1, g, v_o, ph, cur_t_p*1000, cur_t_d*1000, cur_v_p, cur_v_d, scheme])

                nl_p, nl_d = self._compute_nl(gs, n_pot)
                nl = (nl_p + nl_d) / 2
                self._q.put({"type":"att_done","att":att,"gs":gs,"phs":phs,"nl_p":nl_p,"nl_d":nl_d,"nl":nl,
                             "params":{"scheme":scheme,"t_pot_ms":cur_t_p*1000,"t_dep_ms":cur_t_d*1000,
                                        "v_pot":cur_v_p,"v_dep":cur_v_d},"pt":pt,"pv":pv})
                if nl < best_nl:
                    best_nl = nl; best_att = {"att":att,"gs":gs,"phs":phs,"nl_p":nl_p,"nl_d":nl_d,
                                              "params":{"scheme":scheme,"t_pot_ms":cur_t_p*1000,"t_dep_ms":cur_t_d*1000,"v_pot":cur_v_p,"v_dep":cur_v_d}}
                if nl <= nl_tgt:
                    self._q.put({"type":"opt_log","msg":f"✓ NL={nl:.3f} ≤ target {nl_tgt} at attempt {att}. Stopping."})
                    break

                strat = self._strat_var.get()
                if "Width" in strat and att <= max_att // 2:
                    cur_t_p = min(cur_t_p * t_fact, t_max); cur_t_d = min(cur_t_d * t_fact, t_max)
                elif "Amplitude" in strat or att > max_att // 2:
                    cur_v_p = min(cur_v_p * v_fact, v_max); cur_v_d = max(cur_v_d * v_fact, -v_max)
                if att % 4 == 0:
                    scheme_idx = (scheme_idx + 1) % len(scheme_order)
                    cur_t_p = t_pot; cur_t_d = t_dep

            self._best = best_att
            if best_att: self._q.put({"type":"opt_log","msg":f"BEST: NL={best_att['nl_p']:.3f}/{best_att['nl_d']:.3f} at attempt {best_att['att']}"})
            return True

        self._acq = AcqThread(_task, self._q)
        self._acq.start(); self._run_btn.config(state="disabled")
        self.status.busy("Adaptive LTP/LTD optimizing…"); self._poll_adapt()

    def _poll_adapt(self):
        try:
            while True:
                msg = self._q.get_nowait()
                mtype = msg.get("type","")
                if mtype == "opt_log":
                    self._opt_log(msg["msg"])
                elif mtype == "att_done":
                    att = msg["att"]; gs = msg["gs"]; phs = msg["phs"]
                    n_p = sum(1 for p in phs if p=="POT")
                    pot_p = list(range(1,n_p+1)); pot_g = gs[:n_p]
                    dep_p = list(range(n_p+1,len(gs)+1)); dep_g = gs[n_p:]
                    if pot_p: self._g_plot.add_trace(pot_p, pot_g, label=f"POT A{att}", color_idx=att-1, lw=1.0)
                    if dep_p: self._g_plot.add_trace(dep_p, dep_g, label=f"DEP A{att}", color_idx=att-1, ls="--", lw=1.0)
                    self._nl_plot.add_trace([att], [msg["nl"]], label=f"NL A{att}", color_idx=att-1, scatter=True, lw=0)
                    self._nl_plot.hline(float(self._og["nl_tgt"].get()), color=C["accent3"])
                    if MATPLOTLIB_OK and msg.get("pt"):
                        self._plot_pulse_trace(msg["pt"], msg["pv"], att)
                    self._attempts.append(msg)
                elif mtype == "prog": self._prog.set(msg.get("pct",0))
                elif mtype == "done":
                    self._prog.set(100,"Done"); self._run_btn.config(state="normal")
                    self.status.ok("Adaptive LTP/LTD complete."); self.finish_run(); return
                elif mtype == "error":
                    self.log.log(f"Adaptive error: {msg['message']}", "err")
                    self._run_btn.config(state="normal"); return
        except queue.Empty: pass
        self.start_poll(self._poll_adapt)

    def _plot_pulse_trace(self, pt, pv, att):
        self._vt_fig.clear()
        ax = self._vt_fig.add_subplot(111)
        ax.plot(pt, pv, color=PUB_COLORS[att % len(PUB_COLORS)], lw=1.2, label=f"Attempt {att}")
        ax.axhline(0, color="gray", lw=0.5, ls=":")
        ax.set_xlabel("Time (ms)", fontsize=9); ax.set_ylabel("V_pulse (V)", fontsize=9)
        ax.set_title(f"Pulse Voltage vs Time - Attempt {att}", fontsize=9, fontweight="bold")
        ax.tick_params(which="both", direction="in", top=True, right=True); ax.set_facecolor("white")
        ax.legend(fontsize=7); self._vt_fig.tight_layout(pad=1.2)
        self._vt_canvas.draw_idle()

    def _export_best(self):
        if not self._best: messagebox.showinfo("Export","No best run found."); return
        d = self._best
        def _z(w):
            p = d["params"]
            w.writerow(["# BEST LTP/LTD RUN"])
            w.writerow(["Attempt", d["att"], "NL_p", d["nl_p"], "NL_d", d["nl_d"]])
            for k, v in p.items(): w.writerow([k, v])
            w.writerow(["Pulse","G(S)","Phase"])
            for pi, (g, ph) in enumerate(zip(d["gs"], d["phs"])): w.writerow([pi+1, g, ph])
        self.manual_export("AdaptiveLTP_BEST", ["Col1","Col2","Col3","Col4","Col5","Col6"], lambda w: _z(w))

    def _export_all(self):
        if not self._attempts: return
        def _z(w):
            w.writerow(["Attempt","NL_p","NL_d","NL_avg","scheme","t_pot_ms","v_pot"])
            for msg in self._attempts:
                p = msg["params"]
                w.writerow([msg["att"], msg["nl_p"], msg["nl_d"], msg["nl"],
                            p["scheme"], p["t_pot_ms"], p["v_pot"]])
        self.manual_export("AdaptiveLTP_AllAttempts",
            ["Attempt","NL_p","NL_d","NL_avg","scheme","t_pot_ms","v_pot"], lambda w: _z(w))


class NeuromorphicSuite(tk.Tk):
    def __init__(self):
        super().__init__()

        self._dpi_watch = None
        load_ui_pref()
        apply_scaling(self)

        self.title("Resistive Switching - SMU 2450")
        self.configure(bg=C["bg"])

        _, mw, mh = _monitor_metrics(self)
        sw = mw or self.winfo_screenwidth()
        sh = mh or self.winfo_screenheight()
        w = min(int(sw * 0.90), S(1760))
        h = min(int(sh * 0.86), S(1060))
        self.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2), max(0, (sh - h) // 3)))
        self.minsize(S(1180), S(760))

        self.app_state = {"ltp_last_run": None, "ltp_v_read": 0.1, "ltp_t_read": 0.005, "seq_folder": None}
        self.app_vars = {
            "dir":       tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Desktop")),
            "sample":    tk.StringVar(value="Sample"),
            "device":    tk.StringVar(value="D01"),
            "dia":       tk.StringVar(value="50"),
            "run_label": tk.StringVar(value="RUN1"),
        }

        init_ttk_styles(self)

        left = tk.Frame(self, bg=C["bg"])
        scale_metric(left, width=300)
        left.pack(side="left", fill="y", padx=(S(10), S(8)), pady=S(10))
        left.pack_propagate(False)
        tk.Frame(self, bg=C["border"], width=1).pack(side="left", fill="y")
        right = tk.Frame(self, bg=C["bg"])
        right.pack(side="left", fill="both", expand=True, padx=(S(8), S(10)), pady=S(10))

        self.status = StatusBar(self); self.status.pack(side="bottom", fill="x")
        self.log = LogPane(left); self.log.pack(side="bottom", fill="x", pady=(S(8), 0))

        tk.Label(left, text="DEVICE & STORAGE", bg=C["bg"], fg=C["muted"],
                 font=FONT_SMALL, anchor="w").pack(fill="x", pady=(0, S(2)))
        gf = tk.Frame(left, bg=C["panel"], highlightthickness=1,
                      highlightbackground=C["border"])
        gf.pack(side="top", fill="x", pady=(0, S(10)))

        dir_row = tk.Frame(gf, bg=C["panel"]); dir_row.pack(fill="x", padx=S(6), pady=S(5))
        tk.Label(dir_row, text="Folder", bg=C["panel"], fg=C["text2"],
                 font=FONT_LABEL, width=7, anchor="e").pack(side="left")
        mk_entry(dir_row, textvariable=self.app_vars["dir"], width=14
                 ).pack(side="left", fill="x", expand=True, padx=S(4))
        mk_button(dir_row, "…", command=lambda: self.app_vars["dir"].set(
            filedialog.askdirectory() or self.app_vars["dir"].get()),
            padx=S(7)).pack(side="left")

        params_f = tk.Frame(gf, bg=C["panel"]); params_f.pack(fill="x", padx=S(2), pady=(0, S(2)))
        params_f.columnconfigure(1, weight=1)
        for i, (lbl, key) in enumerate([("Sample","sample"),("Device ID","device"),("Dia (µm)","dia"),("Run Label","run_label")]):
            param_row(params_f, i, lbl, self.app_vars[key], label_width=12)
        tk.Label(gf, text="Saved as:  Folder / Device / RunLabel / Test /",
                 bg=C["panel"], fg=C["muted"], font=FONT_SMALL, anchor="w"
                 ).pack(fill="x", padx=S(8), pady=(S(2), S(6)))

        self.visa = VisaManager()
        self.conn = ConnectionPanel(left, self.visa, self.log, self.status)
        self.conn.pack(side="top", fill="x")

        tk.Label(left,
                 text=("Instrument limit - pulses are software timed over VISA,\n"
                       "so the shortest honest width is %.0f ms. Anything smaller\n"
                       "is clamped to %.0f ms. Keep NPLC ≤ 0.2 for short reads."
                       % (MIN_PULSE_S * 1e3, MIN_PULSE_S * 1e3)),
                 bg=C["bg"], fg=C["muted"], font=FONT_SMALL, anchor="w", justify="left"
                 ).pack(fill="x", pady=(S(10), 0))

        sc_row = tk.Frame(left, bg=C["bg"]); sc_row.pack(fill="x", pady=(S(12), 0))
        tk.Label(sc_row, text="UI scale", bg=C["bg"], fg=C["text2"],
                 font=FONT_LABEL).pack(side="left")
        self._scale_var = tk.StringVar(value="Auto")
        sc_cb = mk_combobox(sc_row, values=["Auto", "100%", "125%", "150%", "175%",
                                            "200%", "225%", "250%"],
                            width=6, textvariable=self._scale_var)
        sc_cb.pack(side="left", padx=S(6))
        sc_cb.bind("<<ComboboxSelected>>", self._on_scale_pick)
        self._scale_note = tk.Label(sc_row, text="", bg=C["bg"], fg=C["muted"],
                                    font=FONT_SMALL)
        self._scale_note.pack(side="left")
        self._scale_hint = tk.Label(left, text="", bg=C["bg"], fg=C["muted"],
                                    font=FONT_SMALL, anchor="w")
        self._scale_hint.pack(fill="x", pady=(S(3), 0))
        tk.Label(left, text="Saved per monitor.  Ctrl + / Ctrl − resize, Ctrl 0 = auto.",
                 bg=C["bg"], fg=C["muted"], font=FONT_SMALL, anchor="w", justify="left"
                 ).pack(fill="x")

        self.nb = ttk.Notebook(right); self.nb.pack(fill="both", expand=True)
        def _gd(): return self.conn.get_driver()

        self.tab_dc   = DCIVTab(   self.nb, _gd, self.log, self.status, self.app_vars, self.app_state)
        self.tab_frm  = FormingTab(self.nb, _gd, self.log, self.status, self.app_vars, self.app_state)
        self.tab_piv  = PulseIVTab(self.nb, _gd, self.log, self.status, self.app_vars, self.app_state)
        self.tab_ltp  = LTPLTDTab( self.nb, _gd, self.log, self.status, self.app_vars, self.app_state)
        self.tab_ppf  = PPFPPDTab( self.nb, _gd, self.log, self.status, self.app_vars, self.app_state)
        self.tab_stdp = STDPTab(   self.nb, _gd, self.log, self.status, self.app_vars, self.app_state)
        self.tab_ret  = RetentionTab(self.nb, _gd, self.log, self.status, self.app_vars, self.app_state)
        self.tab_end  = EnduranceTab(self.nb, _gd, self.log, self.status, self.app_vars, self.app_state)
        self.tab_mlc  = MLCTab(    self.nb, _gd, self.log, self.status, self.app_vars, self.app_state)

        self.tab_var   = DeviceVariabilityTab(  self.nb, _gd, self.log, self.status, self.app_vars, self.app_state)
        self.tab_cond  = ConductionMechanismTab(self.nb, _gd, self.log, self.status, self.app_vars, self.app_state)
        self.tab_neuron= NeuronTestTab(          self.nb, _gd, self.log, self.status, self.app_vars, self.app_state)
        self.tab_adapt = AdaptiveLTPTab(         self.nb, _gd, self.log, self.status, self.app_vars, self.app_state)

        self.nb.add(self.tab_dc,     text="DC I-V")
        self.nb.add(self.tab_frm,    text="Forming")
        self.nb.add(self.tab_piv,    text="Pulse I-V")
        self.nb.add(self.tab_ltp,    text="LTP/LTD")
        self.nb.add(self.tab_ppf,    text="PPF/PPD")
        self.nb.add(self.tab_stdp,   text="STDP")
        self.nb.add(self.tab_ret,    text="Retention")
        self.nb.add(self.tab_end,    text="Endurance")
        self.nb.add(self.tab_mlc,    text="MLC")
        self.nb.add(self.tab_var,    text="Variability")
        self.nb.add(self.tab_cond,   text="Conduction")
        self.nb.add(self.tab_neuron, text="Neuron")
        self.nb.add(self.tab_adapt,  text="Adaptive LTP")

        self.tabs_dict = {
            "Forming":     self.tab_frm,
            "DC I-V":      self.tab_dc,
            "Pulse I-V":   self.tab_piv,
            "LTP/LTD":     self.tab_ltp,
            "PPF":         self.tab_ppf,
            "PPD":         self.tab_ppf,
            "STDP":        self.tab_stdp,
            "Retention":   self.tab_ret,
            "Endurance":   self.tab_end,
            "MLC":         self.tab_mlc,
            "_device_id":  self.app_vars["device"].get(),
        }
        self.tab_seq = SequenceTab(self.nb, self.tabs_dict, self.log, self.status, self.app_state)
        self.nb.add(self.tab_seq, text="Sequence")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.bind("<Configure>", self._on_configure)
        for seq in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            self.bind_all(seq, lambda e: self._bump_scale(+0.125))
        for seq in ("<Control-minus>", "<Control-KP_Subtract>"):
            self.bind_all(seq, lambda e: self._bump_scale(-0.125))
        self.bind_all("<Control-Key-0>", lambda e: self._apply_scale("auto"))
        self._sync_scale_ui()
        self.after(250, _rescale_figures)

        self.log.log("Ready. UI scale %.2fx  ·  min pulse width %.0f ms (SMU 2450, software timed)."
                     % (UI_SCALE, MIN_PULSE_S * 1e3), "ok")
        self.status.ok("Ready - Resistive Switching - SMU 2450")

    def _on_configure(self, event=None):
        if self._dpi_watch is not None:
            try: self.after_cancel(self._dpi_watch)
            except Exception: pass
        self._dpi_watch = self.after(350, self._check_monitor)

    def _check_monitor(self):
        self._dpi_watch = None
        if not self.winfo_exists():
            return
        try:
            key = monitor_key(self)
        except Exception:
            return

        if key != MON_KEY:
            old = UI_SCALE
            apply_scaling(self, live=True)
            self._sync_scale_ui()
            if abs(UI_SCALE - old) > 0.02:
                self.log.log("Moved to display %s - UI scale %.2fx → %.2fx."
                             % (key, old, UI_SCALE), "info")
            return

        if UI_MODE != "auto":
            return
        try:
            want = detect_scale(self)
        except Exception:
            return
        if abs(want - UI_SCALE) > 0.02:
            old = UI_SCALE
            apply_scaling(self, want, live=True)
            self._sync_scale_ui()
            self.log.log("Display changed - UI scale %.2fx → %.2fx."
                         % (old, UI_SCALE), "info")

    def _on_scale_pick(self, event=None):
        txt = self._scale_var.get()
        self._apply_scale("auto" if txt == "Auto" else float(txt.rstrip("%")) / 100.0)

    def _apply_scale(self, mode):
        key = monitor_key(self)
        _UI_PREFS[key] = "auto" if mode == "auto" else float(mode)
        _UI_PREFS.pop("*", None)
        save_ui_pref()
        apply_scaling(self, live=True)
        self._sync_scale_ui()
        self.log.log("UI scale for %s: %s (%.2fx). Restart to re-flow spacing exactly."
                     % (key,
                        "auto" if UI_MODE == "auto" else "%d%%" % round(UI_SCALE * 100),
                        UI_SCALE), "ok")
        return "break"

    def _bump_scale(self, delta):
        return self._apply_scale(round(max(0.8, min(UI_SCALE + delta, 3.0)), 3))

    def _sync_scale_ui(self):
        try:
            self._scale_var.set("Auto" if UI_MODE == "auto"
                                else "%d%%" % round(float(UI_MODE) * 100))
            self._scale_note.config(text="→ %d%%" % round(UI_SCALE * 100))
            self._scale_hint.config(text="this screen: %s" % MON_KEY)
        except Exception:
            pass

    def destroy(self):
        if getattr(self, "_dpi_watch", None) is not None:
            try: self.after_cancel(self._dpi_watch)
            except Exception: pass
            self._dpi_watch = None
        try:
            super().destroy()
        except Exception:
            pass

    def _on_close(self):
        if self.conn.driver:
            try: self.conn.driver.output_off()
            except: pass
        self.visa.close(); self.destroy()

if __name__ == "__main__":
    app = NeuromorphicSuite()
    app.mainloop()
