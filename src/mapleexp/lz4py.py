"""純 Python 的 LZ4 block 解壓，介面跟 ``lz4.block.decompress`` 一樣。

``lz4`` 是 C 寫的套件，剛出的 Python 版本常常還沒有 wheel（3.15 在 Windows 上就裝不到）。
:mod:`mapleexp.gamedata` 沒有它就讀不了客戶端的 bundle，匯出腳本整個不能跑。這裡照格式自己解一遍，
慢很多（幾 MB 要幾秒），所以只給匯出腳本在裝不到 ``lz4`` 時頂著用，平常還是用 ``lz4``。

格式（一個 block 就是一串 sequence）::

    token（高 4 位元：literal 長度；低 4 位元：match 長度 - 4）
    [literal 長度是 15 → 後面接著加，每個位元組加到不是 255 為止]
    literal
    offset（u16 小端序；最後一個 sequence 沒有 offset 與 match）
    [match 長度是 15 → 同上接著加]

match 是「從已經解出來的結尾往回 offset 個位元組的地方，複製 match 長度個位元組」；offset 比長度小的時候
來源與目的會重疊，等於把那一小段一直重複鋪下去。
"""

from __future__ import annotations


class Lz4Error(ValueError):
    """資料不是合法的 LZ4 block，或解出來的長度跟說好的不一樣。"""


def _extra(src: bytes, i: int, length: int) -> tuple[int, int]:
    """長度寫 15 的話繼續往後加；回 (長度, 新的位置)。"""
    while True:
        byte = src[i]
        i += 1
        length += byte
        if byte != 255:
            return length, i


def decompress(src: bytes, uncompressed_size: int) -> bytes:
    out = bytearray()
    i, end = 0, len(src)
    try:
        while i < end:
            token = src[i]
            i += 1
            literal = token >> 4
            if literal == 15:
                literal, i = _extra(src, i, literal)
            if literal:
                if i + literal > end:
                    raise Lz4Error("lz4：literal 超出資料結尾")
                out += src[i:i + literal]
                i += literal
            if i >= end:
                break
            offset = src[i] | (src[i + 1] << 8)
            i += 2
            length = token & 15
            if length == 15:
                length, i = _extra(src, i, length)
            length += 4
            start = len(out) - offset
            if offset == 0 or start < 0:
                raise Lz4Error("lz4：offset 指到資料開頭之前")
            if offset >= length:
                out += out[start:start + length]
            else:
                piece = bytes(out[start:])
                out += (piece * (length // offset + 1))[:length]
    except IndexError:
        raise Lz4Error("lz4：資料不完整") from None
    if len(out) != uncompressed_size:
        raise Lz4Error(f"lz4：解出 {len(out)} 位元組，應該是 {uncompressed_size}")
    return bytes(out)
