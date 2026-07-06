# ProLOG

ProLOG is an MVP Windows desktop application for unified production work logs.

The central entity is `WorkLogEntry`. Reports and shift assignments are derived
from work log entries, so the codebase can later grow into a broader production
platform without rewriting the foundation.

## Stack

- Python 3.12
- PySide6
- SQLite
- openpyxl
- JSON configuration
- PyInstaller

## Run

```powershell
python -m pip install -r requirements.txt
python main.py
```

## Build

```powershell
python -m pip install pyinstaller
pyinstaller ProLOG.spec --noconfirm --clean
```

