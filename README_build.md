# How to build AmbienZ.exe
## Requirements
- Windows 10/11
- Python 3.10+ (download from https://python.org — check "Add to PATH" during install)
## Steps
1. Put these files in the **same folder** as `main.py`:
   - `AmbienZ.spec`
   - `build_AmbienZ.bat`
2. Double-click **`build_AmbienZ.bat`**
3. Wait ~2 minutes. The finished EXE will appear at:
   ```
   dist\AmbienZ.exe
   ```
4. Copy `AmbienZ.exe` anywhere you like — it runs without Python installed.
---
## Manual build (if the bat fails)
Open a terminal in the project folder and run:
```
pip install pyinstaller PySide6 opencv-python-headless numpy mss
pyinstaller AmbienZ.spec --clean --noconfirm
```
## Troubleshooting
| Problem | Fix |
|---|---|
| `python not found` | Re-install Python and tick "Add to PATH" |
| App crashes on launch | Run `AmbienZ.exe` from a terminal to see the error |
| Antivirus flags the EXE | This is a false positive — PyInstaller-packed apps trigger AVs. Add an exclusion. |
| App is slow to start | Normal — PyInstaller single-file EXEs unpack to a temp folder first |