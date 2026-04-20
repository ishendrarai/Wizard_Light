import sys
import os
import json
import time
import socket
import numpy as np
import cv2
import mss
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSlider, QLabel, QPushButton, QLineEdit, QGroupBox, QComboBox,
    QFrame, QSystemTrayIcon, QMenu, QListWidget, QListWidgetItem,
    QAbstractItemView, QSizePolicy
)
from PySide6.QtGui import QAction, QIcon, QColor
from PySide6.QtCore import Qt, QThread, Signal, Slot, QEvent

from color_temperature import kelvin_to_multipliers


CONFIG_FILE = "ambienz_config.json"
BULB_PORT = 38899
CHANGE_THRESHOLD = 5.0    # min Euclidean distance to trigger a send


# ---------------------------------------------------------------------------
# COLOR SCIENCE UTILS
# ---------------------------------------------------------------------------
def to_linear(image: np.ndarray) -> np.ndarray:
    return np.power(np.clip(image / 255.0, 1e-6, 1.0), 2.2)


def to_srgb(linear_color: np.ndarray) -> np.ndarray:
    return np.power(np.clip(linear_color, 0.0, 1.0), 1.0 / 2.2) * 255.0


# ---------------------------------------------------------------------------
# DOMINANT COLOR  – histogram-based, no KMeans
# ---------------------------------------------------------------------------
def histogram_dominant(img: np.ndarray, dark_threshold: int = 20) -> np.ndarray:
    """
    Quantise pixels into 8×8×8 colour bins and return the centre of
    the most-populated non-dark bin.  ~5-10× faster than KMeans.
    """
    pixels = img.reshape(-1, 3).astype(np.uint16)

    # Drop dark pixels early
    brightness = pixels.sum(axis=1) // 3
    pixels = pixels[brightness > dark_threshold]
    if len(pixels) < 50:
        return np.array([0.0, 0.0, 0.0])

    # 3-D histogram with 8 bins per channel (32-step quantisation)
    r_bin = (pixels[:, 0] >> 5).astype(np.uint16)   # 0-7
    g_bin = (pixels[:, 1] >> 5).astype(np.uint16)
    b_bin = (pixels[:, 2] >> 5).astype(np.uint16)

    flat_idx = r_bin * 64 + g_bin * 8 + b_bin          # 512 possible bins
    hist = np.bincount(flat_idx, minlength=512)

    dominant = int(np.argmax(hist))
    r = ((dominant >> 6) & 7) * 32 + 16
    g = ((dominant >> 3) & 7) * 32 + 16
    b = (dominant & 7) * 32 + 16
    return np.array([r, g, b], dtype=np.float32)


# ---------------------------------------------------------------------------
# SYNC WORKER  (QThread)
# ---------------------------------------------------------------------------
class SyncWorker(QThread):
    preview_signal = Signal(dict)   # {"rgb": (r,g,b), "time": ms, "skipped": bool}

    def __init__(self):
        super().__init__()
        self.running = False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.params = {
            "bulb_ips": ["192.168.0.100"],
            "fps": 40,
            "saturation": 1.4,
            "smoothness": 0.6,
            "brightness": 100,
            "mode": "Dominant",
            "dark_threshold": 20,
            "monitor_idx": 1,
            "gamma": 1.0,
            "kelvin": 6_500,          # NEW – neutral daylight by default
        }
        self.prev_rgb = np.zeros(3, dtype=np.float64)
        self.prev_sent_rgb = np.full(3, -999.0)        # force first send

    # ------------------------------------------------------------------
    def crop_black_bars(self, img: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        _, thresh = cv2.threshold(
            gray, self.params["dark_threshold"], 255, cv2.THRESH_BINARY
        )
        coords = cv2.findNonZero(thresh)
        if coords is not None:
            x, y, w, h = cv2.boundingRect(coords)
            if w > 10 and h > 10:
                return img[y : y + h, x : x + w]
        return img

    # ------------------------------------------------------------------
    def run(self):
        self.running = True
        with mss.mss() as sct:
            while self.running:
                t0 = time.perf_counter()

                # --- Capture ---
                m_idx = self.params.get("monitor_idx", 1)
                if m_idx >= len(sct.monitors):
                    m_idx = 1
                monitor = sct.monitors[m_idx]
                img = np.array(sct.grab(monitor))
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2RGB)
                img_small = cv2.resize(img, (160, 90), interpolation=cv2.INTER_AREA)
                img_small = self.crop_black_bars(img_small)

                # --- Color extraction ---
                mode = self.params["mode"]
                if mode == "Dominant":
                    rgb = histogram_dominant(img_small, self.params["dark_threshold"])
                elif mode == "Edge Weighted":
                    rgb = self._extract_edge_weighted(img_small)
                else:
                    rgb = np.mean(img_small, axis=(0, 1))

                # --- Color science pipeline ---
                lin_rgb = to_linear(rgb)

                # Gamma correction
                gamma = float(self.params.get("gamma", 1.0))
                if gamma != 1.0:
                    lin_rgb = np.power(np.clip(lin_rgb, 1e-9, 1.0), gamma)

                # Saturation
                mean_lin = np.mean(lin_rgb)
                lin_rgb = mean_lin + (lin_rgb - mean_lin) * self.params["saturation"]
                np.clip(lin_rgb, 0.0, 1.0, out=lin_rgb)

                # ── NEW: Colour temperature white-point adjustment ────────
                kelvin = float(self.params.get("kelvin", 6_500))
                r_k, g_k, b_k = kelvin_to_multipliers(kelvin)
                lin_rgb = lin_rgb * np.array([r_k, g_k, b_k])
                np.clip(lin_rgb, 0.0, 1.0, out=lin_rgb)
                # ─────────────────────────────────────────────────────────

                # Temporal smoothing  (operates on adjusted linear colours)
                smooth = self.params["smoothness"]
                self.prev_rgb = self.prev_rgb * smooth + lin_rgb * (1.0 - smooth)

                final_rgb = to_srgb(self.prev_rgb).astype(int)

                # --- Frame-skip optimisation ---
                skipped = False
                if np.linalg.norm(final_rgb - self.prev_sent_rgb) > CHANGE_THRESHOLD:
                    self.send_to_wiz(final_rgb)
                    self.prev_sent_rgb = final_rgb.copy()
                else:
                    skipped = True

                elapsed = time.perf_counter() - t0
                self.preview_signal.emit(
                    {"rgb": tuple(final_rgb), "time": elapsed, "skipped": skipped}
                )

                wait = (1.0 / self.params["fps"]) - (time.perf_counter() - t0)
                if wait > 0:
                    time.sleep(wait)

    # ------------------------------------------------------------------
    def _extract_edge_weighted(self, img: np.ndarray) -> np.ndarray:
        h, w, _ = img.shape
        mask = np.ones((h, w), dtype=np.float32)
        cv2.rectangle(
            mask,
            (int(w * 0.2), int(h * 0.2)),
            (int(w * 0.8), int(h * 0.8)),
            0.2,
            -1,
        )
        return np.average(img, axis=(0, 1), weights=mask)

    # ------------------------------------------------------------------
    def send_to_wiz(self, rgb):
        r, g, b = (int(np.clip(v, 0, 255)) for v in rgb)
        payload = json.dumps(
            {
                "method": "setPilot",
                "params": {
                    "r": r,
                    "g": g,
                    "b": b,
                    "dimming": int(self.params["brightness"]),
                },
            }
        ).encode()
        for ip in self.params.get("bulb_ips", []):
            if ip:
                try:
                    self.sock.sendto(payload, (ip, BULB_PORT))
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# MAIN UI
# ---------------------------------------------------------------------------
class AmbienZUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AmbienZ")
        self.setMinimumWidth(500)
        self.worker = SyncWorker()

        central = QWidget()
        self.setCentralWidget(central)
        self._root_layout = QVBoxLayout(central)
        self._root_layout.setContentsMargins(20, 20, 20, 20)
        self._root_layout.setSpacing(10)

        self._setup_ui()
        self._setup_tray()
        self._load_config()

        self.worker.preview_signal.connect(self._update_ui)
        self.setStyleSheet(self._get_theme())

    # -----------------------------------------------------------------------
    # UI CONSTRUCTION
    # -----------------------------------------------------------------------
    def _setup_ui(self):
        # ---- Preview strip ----
        self.preview_frame = QFrame()
        self.preview_frame.setFixedHeight(72)
        self.preview_frame.setObjectName("preview")
        self._root_layout.addWidget(self.preview_frame)

        # ---- Status row ----
        status_row = QHBoxLayout()
        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("dot_idle")
        self.status_dot.setFixedWidth(20)
        self.status_label = QLabel("Ready")
        self.bulb_count_label = QLabel("Bulbs: 0")
        self.fps_readout = QLabel("FPS: –")
        for w in (self.status_dot, self.status_label, self.bulb_count_label, self.fps_readout):
            status_row.addWidget(w)
        status_row.addStretch()
        self._root_layout.addLayout(status_row)

        # ---- Bulbs group ----
        bulb_group = QGroupBox("Bulbs")
        bulb_layout = QVBoxLayout(bulb_group)

        self.bulb_list = QListWidget()
        self.bulb_list.setFixedHeight(90)
        self.bulb_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        bulb_layout.addWidget(self.bulb_list)

        bulb_input_row = QHBoxLayout()
        self.bulb_input = QLineEdit()
        self.bulb_input.setPlaceholderText("192.168.x.x")
        btn_add = QPushButton("+  Add")
        btn_add.setObjectName("smallBtn")
        btn_add.clicked.connect(self._add_bulb)
        btn_remove = QPushButton("−  Remove")
        btn_remove.setObjectName("smallBtn")
        btn_remove.clicked.connect(self._remove_bulb)
        for w in (self.bulb_input, btn_add, btn_remove):
            bulb_input_row.addWidget(w)
        bulb_layout.addLayout(bulb_input_row)

        self._root_layout.addWidget(bulb_group)

        # ---- Settings group ----
        ctrl_group = QGroupBox("Settings")
        ctrl_layout = QVBoxLayout(ctrl_group)

        # Monitor selector
        self.monitor_combo = QComboBox()
        with mss.mss() as sct:
            for i in range(1, len(sct.monitors)):
                m = sct.monitors[i]
                self.monitor_combo.addItem(f"Display {i}  ({m['width']}×{m['height']})", i)
        self.monitor_combo.currentIndexChanged.connect(self._sync_params)
        ctrl_layout.addWidget(QLabel("Monitor:"))
        ctrl_layout.addWidget(self.monitor_combo)

        # Sliders
        self.fps_slider    = self._add_slider(ctrl_layout, "FPS",               10, 60,     40,    divisor=1,   suffix="")
        self.bright_slider = self._add_slider(ctrl_layout, "Brightness",        10, 100,    100,   divisor=1,   suffix="%")
        self.sat_slider    = self._add_slider(ctrl_layout, "Saturation",        10, 30,     14,    divisor=10,  suffix="×")
        self.smooth_slider = self._add_slider(ctrl_layout, "Smoothing",         0,  99,     60,    divisor=100, suffix="")
        self.gamma_slider  = self._add_slider(ctrl_layout, "Gamma",             8,  22,     10,    divisor=10,  suffix="")
        # NEW – Colour Temperature slider
        self.kelvin_slider = self._add_slider(ctrl_layout, "Color Temp",        1_000, 20_000, 6_500, divisor=1, suffix=" K")

        # Extraction mode
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Dominant", "Average", "Edge Weighted"])
        self.mode_combo.currentTextChanged.connect(self._sync_params)
        ctrl_layout.addWidget(QLabel("Extraction Mode:"))
        ctrl_layout.addWidget(self.mode_combo)

        self._root_layout.addWidget(ctrl_group)

        # ---- Toggle button ----
        self.btn_toggle = QPushButton("▶  START SYNC")
        self.btn_toggle.setObjectName("startBtn")
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.clicked.connect(self._toggle_engine)
        self._root_layout.addWidget(self.btn_toggle)

    # -----------------------------------------------------------------------
    # SLIDER HELPER
    # -----------------------------------------------------------------------
    def _add_slider(self, layout, name: str, mn: int, mx: int, val: int,
                    divisor: int = 1, suffix: str = "") -> QSlider:
        """Create a labeled slider. `divisor` converts int value to display float."""
        def fmt(v):
            return f"{v / divisor:.2g}{suffix}" if divisor != 1 else f"{v}{suffix}"

        lbl = QLabel(f"{name}: {fmt(val)}")
        slider = QSlider(Qt.Horizontal)
        slider.setRange(mn, mx)
        slider.setValue(val)

        def on_change(v):
            lbl.setText(f"{name}: {fmt(v)}")
            self._sync_params()

        slider.valueChanged.connect(on_change)
        layout.addWidget(lbl)
        layout.addWidget(slider)
        return slider

    # -----------------------------------------------------------------------
    # BULB LIST MANAGEMENT
    # -----------------------------------------------------------------------
    def _add_bulb(self):
        ip = self.bulb_input.text().strip()
        if ip and not self._bulb_exists(ip):
            self.bulb_list.addItem(ip)
            self.bulb_input.clear()
            self._sync_params()

    def _remove_bulb(self):
        row = self.bulb_list.currentRow()
        if row >= 0:
            self.bulb_list.takeItem(row)
            self._sync_params()

    def _bulb_exists(self, ip: str) -> bool:
        for i in range(self.bulb_list.count()):
            if self.bulb_list.item(i).text() == ip:
                return True
        return False

    def _get_bulb_ips(self) -> list:
        return [self.bulb_list.item(i).text() for i in range(self.bulb_list.count())]

    # -----------------------------------------------------------------------
    # PARAMS SYNC
    # -----------------------------------------------------------------------
    def _sync_params(self):
        monitor_data = self.monitor_combo.currentData()
        self.worker.params.update({
            "bulb_ips":    self._get_bulb_ips(),
            "fps":         self.fps_slider.value(),
            "brightness":  self.bright_slider.value(),
            "saturation":  self.sat_slider.value() / 10.0,
            "smoothness":  self.smooth_slider.value() / 100.0,
            "gamma":       self.gamma_slider.value() / 10.0,
            "kelvin":      self.kelvin_slider.value(),          # NEW
            "mode":        self.mode_combo.currentText(),
            "monitor_idx": monitor_data if monitor_data is not None else 1,
        })
        self.bulb_count_label.setText(f"Bulbs: {len(self._get_bulb_ips())}")

    # -----------------------------------------------------------------------
    # ENGINE TOGGLE
    # -----------------------------------------------------------------------
    def _toggle_engine(self):
        if self.btn_toggle.isChecked():
            self._sync_params()
            self.worker.start()
            self.btn_toggle.setText("■  STOP SYNC")
            self._set_status("syncing")
        else:
            self.worker.running = False
            self.btn_toggle.setText("▶  START SYNC")
            self._set_status("idle", "Stopped.")

    # -----------------------------------------------------------------------
    # STATUS INDICATOR
    # -----------------------------------------------------------------------
    def _set_status(self, state: str, msg: str = ""):
        state_cfg = {
            "idle":    ("#aaaaaa", "Idle"),
            "syncing": ("#0078d4", "Syncing"),
            "error":   ("#d83b01", "Error"),
        }
        color, label = state_cfg.get(state, ("#aaaaaa", state))
        self.status_dot.setStyleSheet(f"color: {color}; font-size: 18px;")
        self.status_label.setText(msg if msg else label)

    # -----------------------------------------------------------------------
    # UI UPDATE SLOT
    # -----------------------------------------------------------------------
    @Slot(dict)
    def _update_ui(self, data):
        r, g, b = data["rgb"]
        self.preview_frame.setStyleSheet(
            f"background-color: rgb({r},{g},{b}); border-radius: 8px;"
        )
        ms = data["time"] * 1000
        actual_fps = 1000 / ms if ms > 0 else 0
        self.fps_readout.setText(f"FPS: {actual_fps:.0f}")
        suffix = " (skip)" if data.get("skipped") else ""
        self.status_label.setText(f"RGB ({r}, {g}, {b})  |  {ms:.1f} ms{suffix}")
        self._set_status("syncing")

    # -----------------------------------------------------------------------
    # CONFIG  SAVE / LOAD
    # -----------------------------------------------------------------------
    def _load_config(self):
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)

            for ip in cfg.get("bulb_ips", []):
                if ip and not self._bulb_exists(ip):
                    self.bulb_list.addItem(ip)

            if cfg.get("monitor_idx") is not None:
                idx = self.monitor_combo.findData(cfg["monitor_idx"])
                if idx >= 0:
                    self.monitor_combo.setCurrentIndex(idx)

            _s = lambda key, slider: slider.setValue(cfg[key]) if cfg.get(key) is not None else None
            _s("fps",        self.fps_slider)
            _s("brightness", self.bright_slider)
            _s("saturation", self.sat_slider)
            _s("smoothness", self.smooth_slider)
            _s("gamma",      self.gamma_slider)
            _s("kelvin",     self.kelvin_slider)    # NEW

            if cfg.get("mode"):
                self.mode_combo.setCurrentText(cfg["mode"])

        except Exception as e:
            print(f"[Config] load error: {e}")

    def _save_config(self):
        cfg = {
            "bulb_ips":    self._get_bulb_ips(),
            "fps":         self.fps_slider.value(),
            "brightness":  self.bright_slider.value(),
            "saturation":  self.sat_slider.value(),
            "smoothness":  self.smooth_slider.value(),
            "gamma":       self.gamma_slider.value(),
            "kelvin":      self.kelvin_slider.value(),   # NEW
            "mode":        self.mode_combo.currentText(),
            "monitor_idx": self.monitor_combo.currentData(),
        }
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(cfg, f, indent=2)
        except Exception as e:
            print(f"[Config] save error: {e}")

    # -----------------------------------------------------------------------
    # SYSTEM TRAY
    # -----------------------------------------------------------------------
    def _setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        icon = QIcon("Movie.ico")
        if icon.isNull():
            icon = self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon)
        self.tray_icon.setIcon(icon)

        tray_menu = QMenu()
        show_act = QAction("Show Settings", self)
        show_act.triggered.connect(self.showNormal)
        quit_act = QAction("Quit AmbienZ", self)
        quit_act.triggered.connect(QApplication.instance().quit)
        tray_menu.addAction(show_act)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_act)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._tray_activated)
        self.tray_icon.show()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.showNormal()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            if self.windowState() & Qt.WindowState.WindowMinimized:
                event.ignore()
                self.hide()
                self.tray_icon.showMessage(
                    "AmbienZ", "Running in the background.",
                    QSystemTrayIcon.MessageIcon.Information, 2000,
                )
                return
        super().changeEvent(event)

    def closeEvent(self, event):
        self._save_config()
        self.worker.running = False
        self.worker.wait()
        event.accept()

    # -----------------------------------------------------------------------
    # STYLESHEET
    # -----------------------------------------------------------------------
    def _get_theme(self) -> str:
        return """
        QMainWindow, QWidget {
            background-color: #121212;
            font-family: 'Segoe UI', Arial, sans-serif;
        }
        QGroupBox {
            color: #e0e0e0;
            border: 1px solid #2a2a2a;
            margin-top: 14px;
            padding: 12px 10px 10px 10px;
            font-weight: bold;
            border-radius: 6px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 6px;
        }
        QLabel {
            color: #c8c8c8;
            font-size: 12px;
        }
        QLabel#dot_idle { color: #888; font-size: 18px; }
        QLineEdit {
            background: #1e1e1e;
            color: #ffffff;
            border: 1px solid #333;
            padding: 5px 8px;
            border-radius: 4px;
        }
        QLineEdit:focus { border: 1px solid #0078d4; }
        QComboBox {
            background: #1e1e1e;
            color: white;
            border: 1px solid #333;
            padding: 5px 8px;
            border-radius: 4px;
        }
        QComboBox:focus { border: 1px solid #0078d4; }
        QComboBox::drop-down { border-left: 1px solid #333; width: 22px; }
        QComboBox QAbstractItemView {
            background-color: #1e1e1e;
            color: white;
            selection-background-color: #0078d4;
        }
        QListWidget {
            background: #1e1e1e;
            color: #dddddd;
            border: 1px solid #333;
            border-radius: 4px;
            font-size: 12px;
        }
        QListWidget::item:selected { background: #0078d4; color: white; }
        QSlider::groove:horizontal {
            height: 4px;
            background: #3a3a3a;
            border-radius: 2px;
            margin: 8px 0;
        }
        QSlider::sub-page:horizontal {
            background: #0078d4;
            border-radius: 2px;
        }
        QSlider::add-page:horizontal {
            background: #3a3a3a;
            border-radius: 2px;
        }
        QSlider::handle:horizontal {
            background: #1e1e1e;
            border: 2px solid #0078d4;
            width: 12px;
            height: 12px;
            margin: -6px 0;
            border-radius: 7px;
        }
        #preview {
            border: 1px solid #2a2a2a;
            background-color: #000;
            border-radius: 8px;
        }
        #startBtn {
            background-color: #0078d4;
            color: white;
            padding: 11px;
            font-weight: bold;
            font-size: 13px;
            border-radius: 5px;
            border: none;
        }
        #startBtn:hover   { background-color: #1a8ae6; }
        #startBtn:checked { background-color: #d83b01; }
        #smallBtn {
            background-color: #1e1e1e;
            color: #cccccc;
            border: 1px solid #444;
            padding: 5px 10px;
            border-radius: 4px;
            font-size: 11px;
        }
        #smallBtn:hover    { background-color: #2a2a2a; border-color: #0078d4; }
        #smallBtn:disabled { color: #555; }
        """


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)

    icon = QIcon("Movie.ico")
    if not icon.isNull():
        app.setWindowIcon(icon)

    window = AmbienZUI()
    window.show()
    sys.exit(app.exec())
