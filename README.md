<div align="center">

# 💡 AmbienZ

### Real-Time Screen-to-WiZ Ambient Lighting Sync

**A sleek desktop app that captures your screen colors and syncs them live to WiZ smart bulbs over your local network — no cloud, no account, zero latency.**

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![PySide6](https://img.shields.io/badge/GUI-PySide6-41cd52?style=flat-square&logo=qt&logoColor=white)](https://doc.qt.io/qtforpython/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey?style=flat-square)]()

[Features](#-features) · [Requirements](#-requirements) · [Installation](#-installation) · [Usage](#-usage) · [Configuration](#-configuration) · [Troubleshooting](#-troubleshooting)

---

</div>

## ✨ Features

| Feature | Description |
|---|---|
| 🎨 **3 Color Algorithms** | Dominant (KMeans), Average, and Edge-Weighted color extraction |
| 🌊 **Adaptive Smoothing** | Configurable exponential smoothing to prevent jarring flicker |
| 🖥️ **Dark Modern UI** | PySide6 GUI with live color preview and real-time RGB readout |
| 📡 **Auto Discovery** | UDP broadcast scan to automatically find WiZ bulbs on your network |
| 🖥️ **Multi-Monitor** | Select which display to capture from a dropdown |
| 💡 **Brightness Control** | Adjustable max brightness sent directly to the bulb |
| 🔔 **System Tray** | Minimize to tray and run silently in the background |
| 💾 **Config Persistence** | All settings auto-saved/loaded from `AmbienZ_config.json` |

---

## 📋 Requirements

- **Python** 3.10 or higher
- **WiZ smart bulb** connected to your local Wi-Fi network
- Windows 10/11 (primary), macOS, or Linux

---

## 🚀 Installation

### 1. Clone the repository

```bash
git clone https://github.com/ishendrarai/AmbienZ.git
cd AmbienZ
```

### 2. Install dependencies

```bash
pip install PySide6 mss opencv-python numpy
```

### 3. Run

```bash
python main.py
```

---

## 🖱️ Usage

### First-time setup

1. **Find your bulb** — Click **🔍 Scan** to auto-discover WiZ bulbs on your network, or add the IP manually
2. **Select your monitor** — Pick the display you want to capture from the dropdown
3. **Tune your settings** — Adjust brightness, saturation, smoothing, and extraction mode
4. **Click START SYNC** — Your bulb will immediately begin mirroring your screen

### Controls overview

| Control | What it does |
|---|---|
| **🔍 Scan** | UDP broadcast to auto-discover WiZ bulbs and populate the dropdown |
| **Select Monitor** | Choose which display to capture (all connected monitors listed) |
| **Max Brightness** | Sets the `dimming` value sent to the bulb (10–100%) |
| **Saturation Boost** | Multiplies color saturation for more vivid output (1.0–3.0×) |
| **Smoothing** | Controls temporal smoothing between frames (0 = instant, 0.99 = very slow) |
| **Extraction Mode** | Algorithm used to pick the screen color |
| **START / STOP SYNC** | Toggle the live sync loop on or off |

---

## 🎨 Color Extraction Modes

### Dominant (KMeans)
Uses OpenCV's K-Means clustering to find the most prominent color in the frame. Dark pixels are filtered out before clustering. Best for movies and games with distinct color regions.

### Average
Simple mean of all pixels in the captured frame. Lowest CPU usage — ideal if performance is a priority.

### Edge Weighted
Weights pixels near the edges and borders of the screen more heavily. Great for content where the action sits at the frame edges.

---

## ⚙️ Configuration

Settings are auto-saved to `AmbienZ_config.json` next to `main.py` whenever the app closes. The file is loaded automatically on next launch.

| Key | Description |
|-----|-------------|
| `bulb_ip` | IP address of the selected WiZ bulb |
| `monitor_idx` | Index of the monitor to capture (1 = primary) |
| `brightness` | Max brightness value sent to bulb (10–100) |
| `saturation` | Saturation multiplier (stored as slider integer, divided by 10 on use) |
| `smoothness` | Smoothing factor (stored as 0–99, divided by 100 on use) |
| `mode` | Extraction algorithm: `Dominant`, `Average`, or `Edge Weighted` |

---

## 🧠 How It Works

AmbienZ runs a high-frequency capture loop in a background thread (QThread):

```
Screen Frame (mss)
      │
      ▼
Resize to 160×90          (fast, low-memory processing)
      │
      ▼
Crop black bars           (threshold-based edge detection)
      │
      ▼
Color extraction          (KMeans / Average / Edge Weighted)
      │
      ▼
Gamma correction          (sRGB ↔ linear RGB pipeline, γ=2.2)
      │
      ▼
Saturation boost          (linear RGB space)
      │
      ▼
Exponential smoothing     (blends current frame with previous)
      │
      ▼
UDP → WiZ Bulb            (setPilot JSON over port 38899)
```

The WiZ protocol is a simple JSON-over-UDP API on port `38899`. No cloud required.

---

## 🔔 System Tray

Minimizing the window hides AmbienZ to the system tray — sync continues running in the background. A notification confirms it's still active.

- **Double-click** the tray icon to restore the window
- **Right-click** for a menu with *Show Settings* and *Quit AmbienZ*

---

## 🛠️ Troubleshooting

| Problem | Solution |
|---------|----------|
| Bulb not found by Scan | Some routers block UDP broadcast. Enter the IP manually in the dropdown |
| Bulb not responding | Confirm the bulb and PC are on the same Wi-Fi network |
| High CPU usage | Switch to **Average** mode — it skips clustering entirely |
| Colors feel washed out | Increase **Saturation Boost** (try 2.0–2.5) |
| Too much flickering | Increase **Smoothing** toward 0.9 |
| Slow / laggy response | Lower **Smoothing** to 0.2–0.4 |
| Monitor not listed | Reconnect the display and restart the app |
| Settings not saved | Ensure the app is closed normally (not force-quit) |

---

## 📡 Finding Your Bulb's IP

### Option 1 — Use the Scan button *(easiest)*
Click **🔍 Scan** in the UI. AmbienZ broadcasts a UDP packet and auto-populates discovered bulbs with their IP and MAC address.

### Option 2 — WiZ mobile app
`App → Device → Settings → Device Info → IP Address`

### Option 3 — Router admin panel
Log into your router (usually `192.168.0.1` or `192.168.1.1`) and look for a device named **WiZ** or **ESP** in the connected devices list.

### Option 4 — Network scanner
Use [Advanced IP Scanner](https://www.advanced-ip-scanner.com/) (Windows) or run:
```bash
nmap -sn 192.168.0.0/24
```

---

## 🤝 Contributing

Contributions are welcome!

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature-name`
3. Make your changes and test them
4. Commit: `git commit -m "Add your feature"`
5. Push: `git push origin feature/your-feature-name`
6. Open a Pull Request

### Ideas for contributions
- [ ] Multi-bulb sync (send to multiple IPs simultaneously)
- [ ] Kalman filter for temporal stabilization
- [ ] Scene presets (Movie / Gaming / Music / Ambient)
- [ ] Color temperature (Kelvin) white-point adjustment
- [ ] Custom screen region selector (drag-to-select)
- [ ] Audio reactive mode (mic input → color)
- [ ] WLED / Govee / Tapo protocol support

---

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgements

- [pywizlight](https://github.com/sbidy/pywizlight) — WiZ UDP protocol reference
- [mss](https://github.com/BoboTiG/python-mss) — Fast cross-platform screen capture
- [OpenCV](https://opencv.org/) — KMeans clustering and image processing
- [PySide6 / Qt](https://doc.qt.io/qtforpython/) — GUI framework

---

<div align="center">

Made with ❤️ for smart home enthusiasts

⭐ **Star this repo if you find it useful!**

</div>
