"""Codificador QR mínimo (modo byte, versiones 1–10) con salida SVG.

Implementación propia basada en la norma ISO/IEC 18004, sin dependencias externas,
para que el QR se genere localmente sin servicios de terceros.
"""

from __future__ import annotations

# Nivel de corrección -> (bits de formato, índice de tabla)
_ECL = {"L": (1, 0), "M": (0, 1), "Q": (3, 2), "H": (2, 3)}

# Índice 0 sin uso; versiones 1..10
_ECC_PER_BLOCK = (
    (-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18),  # L
    (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26),  # M
    (-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24),  # Q
    (-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28),  # H
)
_NUM_BLOCKS = (
    (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4),  # L
    (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5),  # M
    (-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8),  # Q
    (-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8),  # H
)
MAX_VERSION = 10


class QRError(ValueError):
    """El texto no cabe en un QR soportado."""


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z & 0xFF


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _num_raw_data_modules(ver: int) -> int:
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        numalign = ver // 7 + 2
        result -= (25 * numalign - 10) * numalign - 55
        if ver >= 7:
            result -= 36
    return result


def _num_data_codewords(ver: int, ecl_idx: int) -> int:
    return _num_raw_data_modules(ver) // 8 - _ECC_PER_BLOCK[ecl_idx][ver] * _NUM_BLOCKS[ecl_idx][ver]


def _alignment_positions(ver: int, size: int) -> list[int]:
    if ver == 1:
        return []
    numalign = ver // 7 + 2
    step = (ver * 8 + numalign * 3 + 5) // (numalign * 4 - 4) * 2
    result = [size - 7 - i * step for i in range(numalign - 1)] + [6]
    return list(reversed(result))


class QRCode:
    """Matriz QR: ``modules[y][x]`` es True para módulo oscuro."""

    def __init__(self, text: str, ecl: str = "M") -> None:
        if ecl not in _ECL:
            raise QRError("Nivel de corrección no válido")
        fmt_bits, ecl_idx = _ECL[ecl]
        payload = text.encode("utf-8")
        for ver in range(1, MAX_VERSION + 1):
            cap_bits = _num_data_codewords(ver, ecl_idx) * 8
            count_bits = 8 if ver <= 9 else 16
            if 4 + count_bits + len(payload) * 8 <= cap_bits:
                break
        else:
            raise QRError("Texto demasiado largo para el QR")
        self.version = ver
        self.size = ver * 4 + 17
        self._fmt_bits = fmt_bits
        self._ecl_idx = ecl_idx

        bits: list[int] = []

        def append(val: int, n: int) -> None:
            bits.extend((val >> i) & 1 for i in range(n - 1, -1, -1))

        append(0b0100, 4)
        append(len(payload), count_bits)
        for b in payload:
            append(b, 8)
        append(0, min(4, cap_bits - len(bits)))
        append(0, (-len(bits)) % 8)
        pad = 0xEC
        while len(bits) < cap_bits:
            append(pad, 8)
            pad ^= 0xEC ^ 0x11
        data = [int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(bits), 8)]

        n = self.size
        self.modules = [[False] * n for _ in range(n)]
        self._is_func = [[False] * n for _ in range(n)]
        self._draw_function_patterns()
        self._draw_codewords(self._add_ecc_and_interleave(data))

        best_mask, best_penalty = 0, None
        for mask in range(8):
            self._apply_mask(mask)
            self._draw_format_bits(mask)
            penalty = self._penalty()
            if best_penalty is None or penalty < best_penalty:
                best_mask, best_penalty = mask, penalty
            self._apply_mask(mask)  # XOR deshace la máscara
        self.mask = best_mask
        self._apply_mask(best_mask)
        self._draw_format_bits(best_mask)

    # -------------------------------------------------------------- patrones fijos
    def _set_func(self, x: int, y: int, dark: bool) -> None:
        self.modules[y][x] = dark
        self._is_func[y][x] = True

    def _draw_function_patterns(self) -> None:
        n = self.size
        for i in range(n):
            self._set_func(6, i, i % 2 == 0)
            self._set_func(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        self._set_func(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _alignment_positions(self.version, n)
        last = len(pos) - 1
        for i, ax in enumerate(pos):
            for j, ay in enumerate(pos):
                if (i == 0 and j == 0) or (i == 0 and j == last) or (i == last and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self._set_func(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self._draw_format_bits(0)  # reserva las zonas de formato
        if self.version >= 7:
            rem = self.version
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            vbits = self.version << 12 | rem
            for i in range(18):
                bit = (vbits >> i) & 1 == 1
                a, b = n - 11 + i % 3, i // 3
                self._set_func(a, b, bit)
                self._set_func(b, a, bit)

    def _draw_format_bits(self, mask: int) -> None:
        data = self._fmt_bits << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        n = self.size

        def bit(i: int) -> bool:
            return (bits >> i) & 1 == 1

        for i in range(6):
            self._set_func(8, i, bit(i))
        self._set_func(8, 7, bit(6))
        self._set_func(8, 8, bit(7))
        self._set_func(7, 8, bit(8))
        for i in range(9, 15):
            self._set_func(14 - i, 8, bit(i))
        for i in range(8):
            self._set_func(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self._set_func(8, n - 15 + i, bit(i))
        self._set_func(8, n - 8, True)

    # -------------------------------------------------------------- datos
    def _add_ecc_and_interleave(self, data: list[int]) -> list[int]:
        ver, e = self.version, self._ecl_idx
        numblocks = _NUM_BLOCKS[e][ver]
        blockecclen = _ECC_PER_BLOCK[e][ver]
        rawcodewords = _num_raw_data_modules(ver) // 8
        numshort = numblocks - rawcodewords % numblocks
        shortlen = rawcodewords // numblocks
        divisor = _rs_divisor(blockecclen)
        blocks = []
        k = 0
        for i in range(numblocks):
            dlen = shortlen - blockecclen + (0 if i < numshort else 1)
            dat = data[k : k + dlen]
            k += dlen
            ecc = _rs_remainder(dat, divisor)
            if i < numshort:
                dat = dat + [0]
            blocks.append(dat + ecc)
        result = []
        for i in range(len(blocks[0])):
            for j, blk in enumerate(blocks):
                if i != shortlen - blockecclen or j >= numshort:
                    result.append(blk[i])
        return result

    def _draw_codewords(self, data: list[int]) -> None:
        n = self.size
        i = 0
        right = n - 1
        total = len(data) * 8
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = n - 1 - vert if upward else vert
                    if not self._is_func[y][x] and i < total:
                        self.modules[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def _apply_mask(self, mask: int) -> None:
        funcs = (
            lambda x, y: (x + y) % 2 == 0,
            lambda x, y: y % 2 == 0,
            lambda x, y: x % 3 == 0,
            lambda x, y: (x + y) % 3 == 0,
            lambda x, y: (x // 3 + y // 2) % 2 == 0,
            lambda x, y: x * y % 2 + x * y % 3 == 0,
            lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
            lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
        )
        f = funcs[mask]
        n = self.size
        for y in range(n):
            row, frow = self.modules[y], self._is_func[y]
            for x in range(n):
                if not frow[x] and f(x, y):
                    row[x] = not row[x]

    def _penalty(self) -> int:
        n = self.size
        m = self.modules
        result = 0
        lines = [m[y] for y in range(n)] + [[m[y][x] for y in range(n)] for x in range(n)]
        pat1 = [True, False, True, True, True, False, True, False, False, False, False]
        pat2 = list(reversed(pat1))
        for line in lines:
            run = 1
            for i in range(1, n):
                if line[i] == line[i - 1]:
                    run += 1
                else:
                    if run >= 5:
                        result += 3 + run - 5
                    run = 1
            if run >= 5:
                result += 3 + run - 5
            for i in range(n - 10):
                seg = line[i : i + 11]
                if seg == pat1 or seg == pat2:
                    result += 40
        for y in range(n - 1):
            for x in range(n - 1):
                c = m[y][x]
                if c == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    result += 3
        dark = sum(sum(row) for row in m)
        total = n * n
        k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
        result += k * 10
        return result

    # -------------------------------------------------------------- salida
    def to_svg(self, border: int = 4, dark: str = "#000", light: str = "#fff") -> str:
        dim = self.size + border * 2
        parts = []
        for y in range(self.size):
            for x in range(self.size):
                if self.modules[y][x]:
                    parts.append(f"M{x + border},{y + border}h1v1h-1z")
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {dim} {dim}" '
            f'shape-rendering="crispEdges" role="img" aria-label="Código QR">'
            f'<rect width="100%" height="100%" fill="{light}"/>'
            f'<path d="{"".join(parts)}" fill="{dark}"/></svg>'
        )


def make_svg(text: str, ecl: str = "M") -> str:
    """Genera el SVG de un QR para ``text``."""
    return QRCode(text, ecl).to_svg()
