"""Consola separada para enviar mensajes al bot ya iniciado.

Uso:
    python tools/chat_console.py
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import cfg  # noqa: E402


def send(text: str) -> None:
    payload = json.dumps({"text": text}).encode("utf-8")
    request = urllib.request.Request(
        f"http://{cfg.chat_control_host}:{cfg.chat_control_port}/send",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"el bot respondio HTTP {response.status}")


def main() -> int:
    print("Consola de chat. Escribi un mensaje o :quit para cerrar esta ventana.")
    while True:
        try:
            text = input("chat> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text.lower() in (":quit", ":salir"):
            return 0
        try:
            send(text)
        except (OSError, urllib.error.URLError) as exc:
            print(f"[X] No se pudo enviar: {exc}")
        except RuntimeError as exc:
            print(f"[X] {exc}")


if __name__ == "__main__":
    raise SystemExit(main())