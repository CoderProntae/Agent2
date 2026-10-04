"""Create a compact archive containing both independently runnable Windows tools."""

from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
OUTPUT = DIST / "Agent2-Windows.zip"
FILES = [
    DIST / "Agent2.exe",
    DIST / "UsageLimitEditor.exe",
    ROOT / "README.md",
]


def main() -> None:
    missing = [path for path in FILES if not path.is_file()]
    if missing:
        raise SystemExit("Paket için gereken dosyalar eksik: " + ", ".join(str(path) for path in missing))
    DIST.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in FILES:
            archive.write(path, arcname=path.name)
    print(f"Oluşturuldu: {OUTPUT} ({OUTPUT.stat().st_size:,} bayt)")


if __name__ == "__main__":
    main()
