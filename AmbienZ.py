import sys
import os
import json
import time
import socket
import numpy as np
import cv2
import mss
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QSlider, QLabel, QPushButton, QLineEdit,
                               QGroupBox, QComboBox, QFrame, QSystemTrayIcon,
                               QMenu, QStyle)
from PySide6.QtGui import QAction, QIcon
from PySide6.QtCore import Qt, QThread, Signal, Slot, QEvent


CONFIG_FILE = "ambienz_config.json"
BULB_PORT = 38899


# --- COLOR SCIENCE UTILS ---
def to_linear(image):
    return np.power(image / 255.0, 2.2)


def to_srgb(linear_color):
    return np.power(np.clip(linear_color, 0, 1), 1 / 2.2) * 255


# --- CORE SYNC ENGINE ---
class SyncWorker(QThread):
    preview_signal = Signal(dict)

    def __init__(self):
        super().__init__()
        self.running = False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.params = {
            "bulb_ip": "192.168.0.100", # Default IP
            "fps": 40,
            "saturation": 1.4,
            "smoothness": 0.6,
            "brightness": 100,
            "mode": "Dominant",
            "clusters": 3,
            "dark_threshold": 20,
            "monitor_idx": 1
        }
        self.prev_rgb = np.array([0.0, 0.0, 0.0])

    def crop_black_bars(self, img):
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        _, thresh = cv2.threshold(gray, self.params["dark_threshold"], 255, cv2.THRESH_BINARY)
        coords = cv2.findNonZero(thresh)
        if coords is not None:
            x, y, w, h = cv2.boundingRect(coords)
            if w > 10 and h > 10:
                return img[y:y + h, x:x + w]
        return img

    def run(self):
        self.running = True
        with mss.mss() as sct:
            while self.running:
                start_time = time.perf_counter()

                m_idx = self.params.get("monitor_idx", 1)
                if m_idx >= len(sct.monitors): m_idx = 1
                monitor = sct.monitors[m_idx]

                img = np.array(sct.grab(monitor))
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2RGB)
                img_small = cv2.resize(img, (160, 90))
                img_small = self.crop_black_bars(img_small)

                if self.params["mode"] == "Dominant":
                    rgb = self.extract_dominant(img_small)
                elif self.params["mode"] == "Edge Weighted":
                    rgb = self.extract_edge_weighted(img_small)
                else:
                    rgb = np.mean(img_small, axis=(0, 1))

                lin_rgb = to_linear(rgb)
                mean_lin = np.mean(lin_rgb)
                lin_rgb = mean_lin + (lin_rgb - mean_lin) * self.params["saturation"]

                smooth = self.params["smoothness"]
                current_rgb = self.prev_rgb * smooth + lin_rgb * (1 - smooth)
                self.prev_rgb = current_rgb

                final_rgb = to_srgb(current_rgb).astype(int)
                self.send_to_wiz(final_rgb)

                self.preview_signal.emit({"rgb": tuple(final_rgb), "time": time.perf_counter() - start_time})

                wait_time = (1 / self.params["fps"]) - (time.perf_counter() - start_time)
                if wait_time > 0: time.sleep(wait_time)

    def extract_dominant(self, img):
        pixels = img.reshape(-1, 3)
        brightness = np.sum(pixels, axis=1) / 3
        pixels = pixels[brightness > self.params["dark_threshold"]]
        if len(pixels) < 50: return np.array([0.0, 0.0, 0.0])
        pixels = np.float32(pixels)
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 5, 0.2)
        _, labels, centers = cv2.kmeans(pixels, self.params["clusters"], None, criteria, 1, cv2.KMEANS_PP_CENTERS)
        return centers[np.argmax(np.bincount(labels.flatten()))]

    def extract_edge_weighted(self, img):
        h, w, _ = img.shape
        mask = np.ones((h, w), dtype=np.float32)
        cv2.rectangle(mask, (int(w * 0.2), int(h * 0.2)), (int(w * 0.8), int(h * 0.8)), 0.2, -1)
        return np.average(img, axis=(0, 1), weights=mask)

    def send_to_wiz(self, rgb):
        r, g, b = np.clip(rgb, 0, 255)
        payload = {"method": "setPilot",
                   "params": {"r": int(r), "g": int(g), "b": int(b), "dimming": int(self.params["brightness"])}}
        try:
            # Use the dynamic IP from self.params instead of the global constant
            self.sock.sendto(json.dumps(payload).encode(), (self.params["bulb_ip"], BULB_PORT))
        except Exception:
            pass


# --- MAIN UI ---
class AmbienZUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AmbienZ")
        self.setMinimumWidth(450)
        self.worker = SyncWorker()

        central = QWidget()
        self.setCentralWidget(central)
        self.layout = QVBoxLayout(central)
        self.layout.setContentsMargins(20, 20, 20, 20)

        self.setup_ui()
        self.setup_tray()
        self.load_config()

        self.worker.preview_signal.connect(self.update_ui)
        self.setStyleSheet(self.get_theme())

    def setup_ui(self):
        # Preview Panel
        self.preview_frame = QFrame()
        self.preview_frame.setFixedHeight(80)
        self.preview_frame.setObjectName("preview")
        self.layout.addWidget(self.preview_frame)

        # Control Group
        ctrl_group = QGroupBox("Settings")
        ctrl_layout = QVBoxLayout(ctrl_group)
        
        # IP Input Box
        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("e.g. 192.168.0.100")
        self.ip_input.setText("192.168.0.100") # Default value
        self.ip_input.textChanged.connect(self.sync_params)
        ctrl_layout.addWidget(QLabel("Bulb IP Address:"))
        ctrl_layout.addWidget(self.ip_input)

        self.monitor_combo = QComboBox()
        with mss.mss() as sct:
            for i in range(1, len(sct.monitors)):
                monitor = sct.monitors[i]
                self.monitor_combo.addItem(f"Display {i} ({monitor['width']}x{monitor['height']})", i)

        self.monitor_combo.currentIndexChanged.connect(self.sync_params)
        ctrl_layout.addWidget(QLabel("Select Monitor:"))
        ctrl_layout.addWidget(self.monitor_combo)

        self.bright_slider = self.add_labeled_slider(ctrl_layout, "Max Brightness", 10, 100, 100)
        self.sat_slider = self.add_labeled_slider(ctrl_layout, "Saturation Boost", 10, 30, 14)
        self.smooth_slider = self.add_labeled_slider(ctrl_layout, "Smoothing", 0, 99, 60)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Dominant", "Average", "Edge Weighted"])
        self.mode_combo.currentTextChanged.connect(self.sync_params)
        ctrl_layout.addWidget(QLabel("Extraction Mode:"))
        ctrl_layout.addWidget(self.mode_combo)

        self.layout.addWidget(ctrl_group)

        # Footer
        self.status_label = QLabel(f"Ready. Target IP: {self.ip_input.text()}")
        self.btn_toggle = QPushButton("START SYNC")
        self.btn_toggle.setObjectName("startBtn")
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.clicked.connect(self.toggle_engine)

        self.layout.addWidget(self.status_label)
        self.layout.addWidget(self.btn_toggle)

    def setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        icon = QIcon("Movie.ico") 
        self.tray_icon.setIcon(icon)
        tray_menu = QMenu()
        show_action = QAction("Show Settings", self)
        show_action.triggered.connect(self.showNormal)
        quit_action = QAction("Quit AmbienZ", self)
        quit_action.triggered.connect(QApplication.instance().quit)

        tray_menu.addAction(show_action)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.tray_activated)
        self.tray_icon.show()

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.showNormal()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            if self.windowState() & Qt.WindowState.WindowMinimized:
                event.ignore()
                self.hide()
                self.tray_icon.showMessage("AmbienZ", "Running in background.",
                                           QSystemTrayIcon.MessageIcon.Information, 2000)
                return
        super().changeEvent(event)

    def closeEvent(self, event):
        self.save_config()
        self.worker.running = False
        self.worker.wait()
        event.accept()

    def add_labeled_slider(self, layout, name, mn, mx, val):
        is_percent = (mx == 100 and mn == 10)
        lbl_text = f"{name}: {val if is_percent else (val / 10 if mx == 30 else val / 100)}"
        lbl = QLabel(lbl_text)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(mn, mx)
        slider.setValue(val)

        def on_change(v):
            lbl.setText(f"{name}: {v if is_percent else (v / 10 if mx == 30 else v / 100)}")
            self.sync_params()

        slider.valueChanged.connect(on_change)
        layout.addWidget(lbl)
        layout.addWidget(slider)
        return slider

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    config = json.load(f)
                if config.get("bulb_ip"): self.ip_input.setText(config["bulb_ip"])
                if config.get("monitor_idx") is not None:
                    idx = self.monitor_combo.findData(config["monitor_idx"])
                    if idx >= 0: self.monitor_combo.setCurrentIndex(idx)
                if config.get("brightness"): self.bright_slider.setValue(config["brightness"])
                if config.get("saturation"): self.sat_slider.setValue(config["saturation"])
                if config.get("smoothness"): self.smooth_slider.setValue(config["smoothness"])
                if config.get("mode"): self.mode_combo.setCurrentText(config["mode"])
            except Exception as e:
                print("Failed to load config:", e)

    def save_config(self):
        config = {
            "bulb_ip": self.ip_input.text().strip(),
            "monitor_idx": self.monitor_combo.currentData(),
            "brightness": self.bright_slider.value(),
            "saturation": self.sat_slider.value(),
            "smoothness": self.smooth_slider.value(),
            "mode": self.mode_combo.currentText()
        }
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f)
        except Exception as e:
            print("Failed to save config:", e)

    def sync_params(self):
        monitor_data = self.monitor_combo.currentData()
        current_ip = self.ip_input.text().strip()
        
        self.worker.params.update({
            "bulb_ip": current_ip,
            "brightness": self.bright_slider.value(),
            "saturation": self.sat_slider.value() / 10.0,
            "smoothness": self.smooth_slider.value() / 100.0,
            "mode": self.mode_combo.currentText(),
            "monitor_idx": monitor_data if monitor_data is not None else 1
        })
        
        # Only update status label if not actively running so it doesn't fight the preview updates
        if not self.btn_toggle.isChecked():
            self.status_label.setText(f"Ready. Target IP: {current_ip}")

    def toggle_engine(self):
        if self.btn_toggle.isChecked():
            self.sync_params()
            self.worker.start()
            self.btn_toggle.setText("STOP SYNC")
        else:
            self.worker.running = False
            self.btn_toggle.setText("START SYNC")
            self.status_label.setText(f"Ready. Target IP: {self.ip_input.text().strip()}")

    @Slot(dict)
    def update_ui(self, data):
        self.preview_frame.setStyleSheet(f"background-color: rgb{data['rgb']}; border-radius: 8px;")
        self.status_label.setText(f"Active [{self.ip_input.text().strip()}] | RGB: {data['rgb']} | Frame: {data['time'] * 1000:.1f}ms")

    def get_theme(self):
        return """
            /* Main Window & Background */
            QMainWindow { 
                background-color: #121212; 
            }

            /* Group Box (Settings area) */
            QGroupBox { 
                color: #e0e0e0; 
                border: 1px solid #2a2a2a; 
                margin-top: 15px; 
                padding: 15px; 
                font-weight: bold; 
                border-radius: 5px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }

            /* Text Labels */
            QLabel { 
                color: #ffffff; 
                font-size: 13px; 
            }

            /* Text Input Box */
            QLineEdit { 
                background: #1a1a1a; 
                color: white; 
                border: 1px solid #333; 
                padding: 6px; 
                border-radius: 4px; 
            }
            QLineEdit:focus { 
                border: 1px solid #0078d4; /* Unified Blue */
            }

            /* Dropdown Menus */
            QComboBox { 
                background: #1a1a1a; 
                color: white; 
                border: 1px solid #333; 
                padding: 5px; 
                border-radius: 4px; 
            }
            QComboBox:focus { 
                border: 1px solid #0078d4; /* Unified Blue */
            }
            QComboBox::drop-down {
                border-left: 1px solid #333;
                width: 25px;
            }
            QComboBox QAbstractItemView {
                background-color: #1a1a1a;
                color: white;
                selection-background-color: #0078d4; /* Unified Blue */
            }

            /* Custom Sliders */
            QSlider::groove:horizontal {
                border: none;
                height: 4px;
                background: #444444;
                border-radius: 2px;
                margin: 10px 0;
            }
            QSlider::sub-page:horizontal {
                background: #0078d4; /* Unified Blue */
                border-radius: 2px;
            }
            QSlider::add-page:horizontal {
                background: #555555; 
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #1a1a1a;
                border: 2px solid #0078d4; /* Unified Blue */
                width: 12px;
                height: 12px;
                margin: -6px 0; 
                border-radius: 8px; 
            }

            /* Preview Box */
            #preview { 
                border: 1px solid #2a2a2a; 
                background-color: #000; 
                border-radius: 6px;
            }

            /* Start/Stop Button */
            #startBtn { 
                background-color: #0078d4; /* Unified Blue */
                color: white; 
                padding: 12px; 
                font-weight: bold; 
                border-radius: 4px; 
                border: none;
            }
            #startBtn:hover {
                background-color: #008be8; /* Slightly lighter blue for hover */
            }
            #startBtn:checked { 
                background-color: #d83b01; /* Warning Orange/Red when running */
            }
        """


if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # 1. Apply your specific .ico file globally
    app_icon = QIcon("Movie.ico") 
    app.setWindowIcon(app_icon)
    
    window = AmbienZUI()
    window.show()
    sys.exit(app.exec())