"""紙娃娃資料的測試：連結要走到真的圖、動作要收齊、圖層名稱要洗乾淨、髮型臉型要依款式分組。"""

from __future__ import annotations

import unittest

import helpers  # noqa: F401

from mapleexp import avatar
from mapleexp.wzjs import Vector


def canvas(z="mail", **points):
    return {"origin": Vector(1.0, 2.0), "z": z, "map": {k: Vector(*v) for k, v in points.items()}}


class Resolve(unittest.TestCase):
    TREE = {
        "default": {"hair": canvas("hair", brow=(0, 0)), "shade": {"0": canvas("hairShade"), "1": "0"}},
        "stand1": {"0": {"hair": "../../default/hair", "shade": "../../default/shade", "broken": "../../nope/hair"},
                   "1": {"hair": "../0/hair"}},
    }

    def test_follows_chained_links_to_the_canvas(self):
        path, node = avatar.resolve(self.TREE, ["stand1", "1", "hair"])
        self.assertEqual(path, ["default", "hair"])
        self.assertEqual(node["z"], "hair")

    def test_group_by_skin_falls_to_the_first(self):
        self.assertEqual(avatar.resolve(self.TREE, ["stand1", "0", "shade"])[0], ["default", "shade", "0"])

    def test_broken_link_gives_nothing(self):
        self.assertEqual(avatar.resolve(self.TREE, ["stand1", "0", "broken"]), ([], None))

    def test_link_above_the_root_gives_nothing(self):
        self.assertEqual(avatar.resolve({"a": "../../b"}, ["a"]), ([], None))


class Collect(unittest.TestCase):
    def test_frames_share_parts_and_are_in_numeric_order(self):
        tree = {"walk1": {str(i): {"mail": canvas(navel=(i, 0))} for i in (10, 2)}, "stand1": {"0": {"mail": "../../walk1/2/mail"}}}
        parts = avatar.Parts()
        out = avatar.collect_actions(tree, [], parts)
        self.assertEqual(out["walk1"], [[parts.index["walk1/2/mail"]], [parts.index["walk1/10/mail"]]])
        self.assertEqual(out["stand1"], [[parts.index["walk1/2/mail"]]])
        self.assertEqual(len(parts.rows), 2)
        self.assertEqual(parts.rows[parts.index["walk1/10/mail"]]["map"], {"navel": [10, 0]})

    def test_weapon_groups_nest_under_their_type(self):
        tree = {"info": {}, "30": {"stand1": {"0": {"weapon": canvas("weapon", hand=(1, 1))}}}, "99": {"unknown": {}}}
        self.assertEqual(avatar.weapon_groups(tree), ["30"])
        parts = avatar.Parts()
        self.assertEqual(avatar.collect_actions(tree, ["30"], parts), {"stand1": [[0]]})
        self.assertEqual(parts.rows[0]["path"], "30/stand1/0/weapon")

    def test_weapon_types_follow_the_link_table_unless_excepted(self):
        link = {"21": 30, "31": 30, "45": 30, "404": 30}
        shared = {"info": {}, "30": {"stand1": {}}}
        self.assertEqual(avatar.weapon_types(shared, link), ([30, 31, 45], {"31": 30, "45": 30}))
        own = {"info": {"exceptLinkCashWeaponData": 1}, "30": {"stand1": {}}, "32": {"stand1": {}}}
        self.assertEqual(avatar.weapon_types(own, link), ([30, 32], {}))
        no_base = {"info": {}, "33": {"stand1": {}}}
        self.assertEqual(avatar.weapon_types(no_base, link), ([33], {}))

    def test_type_attacks_take_what_most_plain_weapons_draw(self):
        weapons = {
            1302000: {"info": {}, "swingO1": {}, "stabO1": {}},
            1302001: {"info": {}, "swingO1": {}},
            1302002: {"info": {}, "swingO1": {}, "shoot1": {}},
            1452000: {"info": {}, "shoot1": {}},
            1702000: {"info": {"cash": 1}, "swingT1": {}},
        }
        self.assertEqual(avatar.type_attacks(weapons), {"30": ["swingO1"], "45": ["shoot1"]})

    def test_action_with_no_pictures_is_left_out(self):
        tree = {"sit": {"0": {"delay": 100}}}
        self.assertEqual(avatar.collect_actions(tree, [], avatar.Parts()), {})

    def test_default_only_items_become_one_frame(self):
        parts = avatar.Parts()
        self.assertEqual(avatar.collect_default({"default": {"face": canvas("face", brow=(0, 0))}}, parts), {"default": [[0]]})
        self.assertEqual(parts.rows[0]["path"], "default/face")
        self.assertEqual(avatar.collect_default({"info": {}}, avatar.Parts()), {})

    def test_item_effect_frames_follow_links_and_carry_delays(self):
        frame = {"origin": Vector(5.0, 9.0), "z": 0, "delay": 120}
        eff = {"1102513": {"effect": {"z": -2, "default": {"0": frame, "1": {"origin": Vector(5.0, 9.0)}},
                                      "ladder2": {"0": "../ladder/0"}, "ladder": {"0": frame}, "move": {"0": frame}}}}
        parts = avatar.Parts()
        out = avatar.collect_effect(eff, "1102513", parts, "Effect/ItemEff")
        self.assertEqual(out, {"ladder": [[0, 120]], "default": [[1, 120], [2, avatar.EFFECT_DELAY]]})
        self.assertEqual(parts.rows[1]["path"], "1102513/effect/default/0")
        self.assertEqual(parts.rows[0]["z"], avatar.EFFECT_BELOW)
        self.assertEqual(parts.rows[0]["sheet"], "Effect/ItemEff")
        self.assertEqual(avatar.collect_effect(eff, "1102005", parts, "Effect/ItemEff"), {})

    def test_item_effect_without_z_goes_in_front(self):
        eff = {"1": {"effect": {"default": {"0": {"origin": Vector(0.0, 0.0)}}}}}
        parts = avatar.Parts()
        avatar.collect_effect(eff, "1", parts, "x")
        self.assertEqual(parts.rows[0]["z"], avatar.EFFECT_OVER)

    def test_pack_keys_group_by_kind_and_id_range(self):
        self.assertEqual(avatar.pack_key(2000), "base")
        self.assertEqual(avatar.pack_key(21000), "base")
        self.assertEqual(avatar.pack_key(30000), avatar.pack_key(31999))
        self.assertNotEqual(avatar.pack_key(31999), avatar.pack_key(32000))
        self.assertEqual(avatar.pack_key(1102513), avatar.pack_key(1102005))
        self.assertNotEqual(avatar.pack_key(1102005), avatar.pack_key(1002005))
        self.assertNotEqual(avatar.pack_key(1702000), avatar.pack_key(1702200))

    def test_delays_fill_in_frames_without_one(self):
        body = {"stand1": {"0": {"delay": 300}, "1": {}}}
        self.assertEqual(avatar.delays(body), {"stand1": [300, 500]})


class Layers(unittest.TestCase):
    def test_client_typos_and_spaces_are_cleaned(self):
        self.assertEqual(avatar.clean_z(" backCape"), "backCape")
        self.assertEqual(avatar.clean_z("weapnBelowBody"), "weaponBelowBody")

    def test_unknown_goes_on_top(self):
        self.assertEqual(avatar.clean_z("somethingNew"), "characterEnd")
        self.assertEqual(avatar.clean_z(0), "characterEnd")

    def test_order_has_no_duplicates_and_keeps_the_basics(self):
        self.assertEqual(len(avatar.ORDER), len(set(avatar.ORDER)))
        at = avatar.ORDER.index
        self.assertLess(at("body"), at("mailChest"))
        self.assertLess(at("head"), at("face"))
        self.assertLess(at("face"), at("cap"))
        self.assertLess(at("head"), at("hairShade"))
        self.assertLess(at("hairShade"), at("hair"))
        self.assertLess(at("hair"), at("hairOverHead"))
        self.assertLess(at("arm"), at("weaponOverHand"))
        self.assertLess(at("itemEffectBelow"), at("body"))
        self.assertLess(at("backWeaponOverHead"), at("itemEffectOver"))


class Styles(unittest.TestCase):
    NAMES = {30000: "黑色基本造型", 30001: "紅色基本造型", 30010: "棕色馬尾", 20000: "挑戰的臉型", 20100: "挑戰的臉型", 21000: "挑戰的臉型"}

    def test_hair_groups_by_style_without_the_color_word(self):
        out = avatar.hair_styles([30001, 30000, 30010, 30020], self.NAMES.get)
        self.assertEqual(out, [{"n": "基本造型", "c": {0: 30000, 1: 30001}}, {"n": "馬尾", "c": {0: 30010}}])

    def test_face_color_is_the_hundreds_digit(self):
        out = avatar.face_styles([20000, 20100, 21000], self.NAMES.get)
        self.assertEqual(out, [{"n": "挑戰的臉型", "c": {0: 20000, 1: 20100}}, {"n": "挑戰的臉型", "c": {0: 21000}}])


if __name__ == "__main__":
    unittest.main()
