"""Bot de moderacion para Twitch.

La consola de Windows arranca en cp1252 y rompe los acentos de los mensajes
(los deja como 'fall?'). Forzamos UTF-8 apenas se importa el paquete, antes
de que cualquier entrypoint imprima algo.
"""
from __future__ import annotations

import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        # stdout redirigido a algo que no es un TextIOWrapper: no pasa nada.
        pass
