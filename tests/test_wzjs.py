"""WZJS 解析測試。

跟 test_gamedata 一樣不碰真的遊戲檔案：自己照版面組一包出來再解，寫得出來才算真的看懂。
"""

from __future__ import annotations

import struct
import unittest

import helpers  # noqa: F401

from mapleexp import wzjs


# --------------------------------------------------------------------------- #
# 組一包假的 WZJS
# --------------------------------------------------------------------------- #


def _string_table(strings: list[str]) -> tuple[bytes, bytes]:
    content = bytearray()
    offsets = []
    for s in strings:
        offsets.append(len(content))
        content += s.encode("utf-8")
    offsets.append(len(content))
    return bytes(content), struct.pack(f"<{len(offsets)}i", *offsets)


def encode(name: str, tree: dict) -> bytes:
    """把巢狀 dict 編成 ``<名稱><header>WZJS…``，型別照值的種類決定。"""
    nodes: list[list[int]] = []
    names: list[str] = []
    ints: list[int] = []
    floats: list[float] = []
    vectors: list[float] = []
    values: list[str] = []

    def name_index(s: str) -> int:
        if s not in names:
            names.append(s)
        return names.index(s)

    def place(index: int, node_name: str, value, parent: int) -> None:
        """把 *value* 填進預留好的第 *index* 筆；容器的子節點先整層預留再逐一填，才會連續。"""
        node = [wzjs.TYPE_DIR, name_index(node_name), -1, -1, 0, parent, index, 0]
        if isinstance(value, dict):
            children = list(value.items())
            first = len(nodes)
            node[3] = first if children else -1
            node[4] = len(children)
            nodes.extend([None] * len(children))  # type: ignore[list-item]
            for offset, (child_name, child_value) in enumerate(children):
                place(first + offset, child_name, child_value, index)
        elif isinstance(value, bool):
            raise TypeError("bool 不是 WZJS 的型別")
        elif isinstance(value, int):
            node[0], node[2] = wzjs.TYPE_INT, len(ints)
            ints.append(value)
        elif isinstance(value, float):
            node[0], node[2] = wzjs.TYPE_FLOAT, len(floats)
            floats.append(value)
        elif isinstance(value, str):
            node[0], node[2] = wzjs.TYPE_STRING, len(values)
            values.append(value)
        elif isinstance(value, wzjs.Vector):
            node[0], node[2] = wzjs.TYPE_VECTOR, len(vectors) // 2
            vectors.extend([value.x, value.y])
        elif value is None:
            node[0] = wzjs.TYPE_SPRITE
        else:
            raise TypeError(type(value))
        nodes[index] = node

    nodes.append(None)  # type: ignore[arg-type]
    place(0, name, tree, -1)

    body = bytearray(struct.pack("<4sI", b"WZJS", 5))
    node_at = len(body)
    for node in nodes:
        body += struct.pack("<8i", *node)
    aux_at = len(body)
    aux = list(range(1, len(nodes)))
    body += struct.pack(f"<{len(aux)}i", *aux)
    int_at = len(body)
    body += struct.pack(f"<{len(ints)}i", *ints)
    float_at = len(body)
    body += struct.pack(f"<{len(floats)}f", *floats)
    vec_at = len(body)
    body += struct.pack(f"<{len(vectors)}f", *vectors)
    names_content, names_offsets = _string_table(names)
    names_at = len(body)
    body += names_content
    names_off_at = len(body)
    body += names_offsets
    paths_content, paths_offsets = _string_table([str(i) for i in range(len(nodes))])
    paths_at = len(body)
    body += paths_content
    paths_off_at = len(body)
    body += paths_offsets
    values_content, values_offsets = _string_table(values)
    values_at = len(body)
    body += values_content
    values_off_at = len(body)
    body += values_offsets
    total = len(body)

    header = [
        len(nodes), node_at, len(aux), aux_at, 0, int_at, 0, int_at, 0, int_at,
        len(ints), int_at, 0, float_at, len(floats), float_at, len(vectors) // 2, vec_at,
        0, names_at, 0, names_at, 0, names_at, 0, names_at,
        len(names), names_at, names_off_at, len(nodes), paths_at, paths_off_at,
        len(values), values_at, values_off_at, total,
    ]
    assert len(header) == wzjs.HEADER_INTS
    raw = name.encode("ascii")
    name_field = struct.pack("<i", len(raw)) + raw
    name_field += b"\x00" * (-len(name_field) % 4)
    return name_field + struct.pack(f"<{wzjs.HEADER_INTS}i", *header) + bytes(body)


SNAIL = {
    "info": {"level": 1, "maxHP": 8, "exp": 3, "fs": 10.0, "link": "0100101", "skill": {}},
    "stand": {"0": {"origin": wzjs.Vector(12.0, -7.0), "delay": 180, "$spritesheetitem": None}},
}


class EncodeDecode(unittest.TestCase):
    def test_round_trip(self):
        data = b"junk" * 5 + encode("0100100", SNAIL) + b"\x00" * 7
        found = list(wzjs.iter_assets(data))
        self.assertEqual(len(found), 1)
        name, tree = found[0]
        self.assertEqual(name, "0100100")
        self.assertEqual(tree["info"]["level"], 1)
        self.assertEqual(tree["info"]["maxHP"], 8)
        self.assertEqual(tree["info"]["fs"], 10.0)
        self.assertEqual(tree["info"]["link"], "0100101")
        self.assertEqual(tree["info"]["skill"], {})
        self.assertEqual(tree["stand"]["0"]["origin"], wzjs.Vector(12.0, -7.0))
        self.assertEqual(tree["stand"]["0"]["delay"], 180)
        self.assertIn("$spritesheetitem", tree["stand"]["0"])
        self.assertIsNone(tree["stand"]["0"]["$spritesheetitem"])

    def test_keeps_child_order(self):
        tree = {"map": {str(i): 1000 + i for i in range(40)}}
        _, decoded = next(wzjs.iter_assets(encode("MonsterBook", tree)))
        self.assertEqual(list(decoded["map"]), [str(i) for i in range(40)])
        self.assertEqual(list(decoded["map"].values()), [1000 + i for i in range(40)])

    def test_several_assets_and_stray_magic(self):
        data = encode("0100100", SNAIL) + b"WZJS" + b"\x00" * 30 + encode("MonsterBook", {"100100": {"reward": {"0": 4000019}}})
        found = dict(wzjs.iter_assets(data))
        self.assertEqual(set(found), {"0100100", "MonsterBook"})
        self.assertEqual(found["MonsterBook"]["100100"]["reward"]["0"], 4000019)
        self.assertEqual(wzjs.find_asset(data, "0100100")["info"]["exp"], 3)
        self.assertIsNone(wzjs.find_asset(data, "0100102"))

    def test_truncated_is_skipped(self):
        whole = encode("0100100", SNAIL)
        self.assertEqual(list(wzjs.iter_assets(whole[:-20])), [])
        with self.assertRaises(wzjs.WzError):
            wzjs.parse(whole[:-20], whole.find(b"WZJS"))

    def test_bad_index_is_an_error(self):
        whole = bytearray(encode("0100100", SNAIL))
        at = whole.find(b"WZJS")
        # 第二個節點（info/level 那類）的值索引改成很大的數
        node1 = at + 8 + wzjs.NODE_SIZE
        kind = struct.unpack_from("<i", whole, node1)[0]
        self.assertEqual(kind, wzjs.TYPE_DIR)
        leaf = at + 8 + wzjs.NODE_SIZE * 3
        struct.pack_into("<i", whole, leaf + 8, 9999)
        with self.assertRaises(wzjs.WzError):
            wzjs.parse(bytes(whole), at)

    def test_asset_name(self):
        whole = encode("MonsterBook", {"a": 1})
        self.assertEqual(wzjs.asset_name(whole, whole.find(b"WZJS") - wzjs.HEADER_SIZE), "MonsterBook")
        self.assertIsNone(wzjs.asset_name(b"\x00" * 200, 100))


if __name__ == "__main__":
    unittest.main()
