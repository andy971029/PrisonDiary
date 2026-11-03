"""世界地圖合成測試：座標要對齊底圖樞紐、字串照 WorldMapId 查、壞掉的連結不要輸出。"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401

from mapleexp import spritesheet, worldmap


def sheet(**frames) -> spritesheet.Sheet:
    out = spritesheet.Sheet(name="", path="")
    for name, (w, h, px, py) in frames.items():
        out.frames[name.replace("__", "/")] = spritesheet.Frame(page=0, x=0, y=0, width=w, height=h, pivot_x=px, pivot_y=py)
    return out


WORLDS = {
    "WorldMap": {"WorldMapId": 0, "ParentMap": None,
                 "MapList": [{"SpotX": -178, "SpotY": -169, "Type": 3, "MapNo": [0, 10000], "Title": "0_title", "Desc": "0_desc"}],
                 "MapLinkList": [{"MapLinkIdx": 1, "LinkMap": "WorldMap010", "Tooltip": ""}, {"MapLinkIdx": 0, "LinkMap": "WorldMap000", "Tooltip": ""},
                                 {"MapLinkIdx": 2, "LinkMap": "WorldMapGone", "Tooltip": ""}]},
    "WorldMap000": {"WorldMapId": 0, "ParentMap": "WorldMap", "MapList": [{"SpotX": 0, "SpotY": 0, "Type": 1, "MapNo": [0, 0, 1]}], "MapLinkList": []},
    "WorldMap010": {"WorldMapId": 10, "ParentMap": "WorldMap",
                    "MapList": [{"SpotX": -28, "SpotY": 142, "Type": 0, "MapNo": [100000000]},
                                {"SpotX": 1, "SpotY": 2, "Type": 2, "MapNo": [105030000], "Title": "5_title", "Desc": "5_desc"}],
                    "MapLinkList": []},
    "WorldMapGone": {"WorldMapId": 99, "ParentMap": "WorldMap", "MapList": [], "MapLinkList": []},  # 沒有圖集
}
STRINGS = {
    "0": {"values": {"0_title": "楓之島", "0_desc": "初心者的島嶼", "0_toolTip": "楓之島", "1_toolTip": "維多利亞島"}},
    "10": {"values": {"5_title": "維多利亞中央地城", "5_desc": "內有通往#c弓箭手村肥肥農場#的傳點。"}},
}
SHEETS = {
    "WorldMap": sheet(BaseImg__0=(640, 470, 0.5, 0.5), MapLink__0__link__linkImg=(63, 49, 0.5, 0.5), MapLink__1__link__linkImg=(151, 87, -0.5, 2.0),
                      MapLink__2__link__linkImg=(10, 10, 0, 0), MapList__0__path=(20, 10, 0.5, 0.5)),
    "WorldMap000": sheet(BaseImg__0=(640, 470, 0.5, 0.5)),
    "WorldMap010": sheet(BaseImg__0=(640, 469, 0.5, 0.5010660886764526)),
}


class Build(unittest.TestCase):
    def setUp(self):
        self.out = worldmap.build(WORLDS, STRINGS, SHEETS)["worlds"]
        self.w = {w["id"]: w for w in self.out}

    def test_root_first_and_no_base_means_no_world(self):
        self.assertEqual([w["id"] for w in self.out], ["WorldMap", "WorldMap000", "WorldMap010"])

    def test_titles_come_from_the_parents_link_tooltip(self):
        self.assertEqual(self.w["WorldMap"]["title"], "楓之谷世界")
        self.assertEqual(self.w["WorldMap000"]["title"], "楓之島")
        self.assertEqual(self.w["WorldMap010"]["title"], "維多利亞島")
        self.assertIsNone(self.w["WorldMap"]["parent"])
        self.assertEqual(self.w["WorldMap010"]["parent"], "WorldMap")

    def test_node_position_is_relative_to_the_base_pivot(self):
        node = self.w["WorldMap"]["nodes"][0]
        self.assertEqual((node["x"], node["y"]), (142, 66))
        self.assertEqual(node["type"], 3)
        # 樞紐不在正中間的底圖：高 469、樞紐 0.501 從下面算，也就是上面起算 234
        self.assertEqual((self.w["WorldMap010"]["nodes"][0]["x"], self.w["WorldMap010"]["nodes"][0]["y"]), (292, 376))

    def test_layers_line_up_with_pivots_outside_themselves(self):
        links = {l["i"]: l for l in self.w["WorldMap"]["links"]}
        self.assertEqual(links[0]["box"], [288, 210, 63, 49])
        # 樞紐比例 -0.5 在圖的左邊，2.0 在圖的上面：圖往右、往下放
        self.assertEqual(links[1]["box"], [396, 322, 151, 87])
        self.assertEqual(self.w["WorldMap"]["nodes"][0]["path"], [310, 230, 20, 10])

    def test_links_sorted_named_and_dangling_dropped(self):
        links = self.w["WorldMap"]["links"]
        self.assertEqual([(l["i"], l["to"], l["tip"]) for l in links], [(0, "WorldMap000", "楓之島"), (1, "WorldMap010", "維多利亞島")])

    def test_strings_looked_up_by_world_id_and_cleaned(self):
        node = self.w["WorldMap010"]["nodes"][1]
        self.assertEqual(node["title"], "維多利亞中央地城")
        self.assertEqual(node["desc"], "內有通往弓箭手村肥肥農場的傳點。")
        self.assertEqual(self.w["WorldMap010"]["nodes"][0]["title"], "")

    def test_duplicate_map_ids_collapse_in_order(self):
        self.assertEqual(self.w["WorldMap000"]["nodes"][0]["maps"], [0, 1])

    def test_no_path_key_without_a_path_frame(self):
        self.assertNotIn("path", self.w["WorldMap000"]["nodes"][0])


class Tables(unittest.TestCase):
    def test_split_tells_the_two_same_named_tables_apart(self):
        structure, strings = worldmap.split_tables([STRINGS, {"Worlds": WORLDS}])
        self.assertIs(structure, WORLDS)
        self.assertIs(strings, STRINGS)
        self.assertEqual(worldmap.split_tables([]), ({}, {}))

    def test_load_scans_until_both_tables_are_found(self):
        blobs = [None, b"{broken", '﻿{"0": {"values": {}}}'.encode("utf-8"), b'{"Worlds": {"WorldMap": {}}}', b'{"never": "read"}']
        bundles = [Path(f"json_{n}.bundle") for n in range(len(blobs))]
        with mock.patch.object(worldmap.gamedata, "list_bundles", return_value=bundles), \
                mock.patch.object(worldmap.gamedata, "extract_table", side_effect=blobs) as extract:
            structure, strings = worldmap.load_tables("dir")
        self.assertEqual(structure, {"WorldMap": {}})
        self.assertEqual(strings, {"0": {"values": {}}})
        self.assertEqual(extract.call_count, 4)


if __name__ == "__main__":
    unittest.main()
