"""
Genera icono.ico (cuadro azul con flecha de subida) sin dependencias externas.

Uso:  python herramientas/crear_icono.py
"""

import struct
from pathlib import Path

AZUL = (0x1F, 0x4E, 0x79)
BLANCO = (0xFF, 0xFF, 0xFF)
DESTINO = Path(__file__).resolve().parent.parent / "icono.ico"


def _cubierto(x: float, y: float) -> tuple[bool, bool]:
    """(dentro del cuadro redondeado, dentro de la flecha) en coordenadas 0..1."""
    r = 0.18
    cx, cy = min(max(x, r), 1 - r), min(max(y, r), 1 - r)
    en_cuadro = (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    punta = 0.20 <= y <= 0.52 and abs(x - 0.5) <= (y - 0.20) * 0.95
    asta = 0.52 <= y <= 0.72 and abs(x - 0.5) <= 0.10
    base = 0.76 <= y <= 0.84 and 0.24 <= x <= 0.76
    return en_cuadro, punta or asta or base


def _imagen(n: int) -> bytes:
    """BMP 32 bits (BGRA, filas de abajo hacia arriba) + mascara AND, con 4x4 muestras."""
    pix = bytearray()
    for fila in range(n - 1, -1, -1):
        for col in range(n):
            cuadro = flecha = 0
            for sy in range(4):
                for sx in range(4):
                    c, f = _cubierto((col + (sx + 0.5) / 4) / n, (fila + (sy + 0.5) / 4) / n)
                    cuadro += c
                    flecha += c and f
            a = cuadro / 16
            t = flecha / cuadro if cuadro else 0
            rgb = [round(AZUL[i] * (1 - t) + BLANCO[i] * t) for i in range(3)]
            pix += bytes((rgb[2], rgb[1], rgb[0], round(a * 255)))
    mascara = bytes(((n + 31) // 32) * 4 * n)
    cabecera = struct.pack("<IiiHHIIiiII", 40, n, n * 2, 1, 32, 0, len(pix) + len(mascara), 0, 0, 0, 0)
    return cabecera + bytes(pix) + mascara


def main():
    tamanos = (16, 24, 32, 48, 64, 256)
    imagenes = [_imagen(n) for n in tamanos]
    salida = struct.pack("<HHH", 0, 1, len(tamanos))
    desplazamiento = 6 + 16 * len(tamanos)
    for n, img in zip(tamanos, imagenes):
        salida += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(img), desplazamiento)
        desplazamiento += len(img)
    DESTINO.write_bytes(salida + b"".join(imagenes))
    print(f"creado {DESTINO}")


if __name__ == "__main__":
    main()
