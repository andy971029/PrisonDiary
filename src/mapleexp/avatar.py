"""時裝紙娃娃：把客戶端的角色部位整理成網頁能自己疊的資料，網頁的「紙娃娃」分頁讀這個。

來源是 ``Character.wz`` 搬過來的 WZJS（跟裝備同一包，要用 ``canvas=True`` 才讀得到圖片底下的節點）：

- 一個部位是 ``<動作>/<格>/<部位名>``，有 ``origin``（圖的錨點）、``map``（``navel``、``neck``、``brow``、``hand``
  這些骨架點，兩個部位共用同一點就對得起來）、``z``（圖層名稱）。部位也可能是 ``../../default/hair`` 這種連結。
- 身體 ``00002xxx`` 與頭 ``00012xxx`` 是膚色；頭髮 ``0003xxxx``、``0004xxxx``；臉 ``0002xxxx`` 與臉飾只有 ``default``。
- 時裝武器多一層武器類型：``01702020/30/stand1/0/weapon``，``30`` 是單手劍的握法。
- 有些時裝（不少披風）在 ``Character`` 只放一張 1×1 的空圖，真正的圖在 ``Effect/ItemEff``：
  ``1102513/effect/<動作>/<格>``，沒列的動作用 ``default``（背面用 ``backDefault``），錨點是角色腳底，
  ``effect/z`` 小於 0 畫在角色後面。動畫有自己的每格毫秒，跟身體分開跑。

客戶端找不到 ``zmap``（圖層前後）與 ``smap``（圖層佔哪些欄位），所以 :data:`ORDER` 是照圖層名稱手排的，
新出現的名稱會由 :func:`clean_z` 放到最上層，``scripts/export_avatar.py`` 會印出來提醒補進去。

``web/avatar.json`` 是目錄，``packs`` 記每件放在 ``web/avatar/`` 哪一包的哪一段（見 :func:`pack_key`）。
一件是一段 JSON 緊接著它的 WebP 圖集，網頁用 Range 只抓那一段，選到才載。JSON 長這樣::

    {"p": [[x, y, 寬, 高, 錨x, 錨y, "z", {"navel": [x, y]}], …],      圖集上的部位
     "a": {"stand1": [[0, 1], [0, 2], …], …},                        動作 → 每一格用到的部位（臉、臉飾只有 default）
     "d": {"stand1": [500, 500, 500], …},                            每格幾毫秒（只有身體有）
     "w": {"30": {"stand1": …}, …},                                  武器類型（時裝武器才有，取代 a）
     "e": {"default": [[部位, 毫秒], …], "ladder": …}}              ItemEff 的特效（有才有）
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterator

STAND_ACTIONS = ("stand1", "stand2", "walk1", "walk2")
ATTACKS = (
    "swingO1", "swingO2", "swingO3", "swingOF", "swingT1", "swingT2", "swingT3", "swingTF",
    "swingP1", "swingP2", "swingPF", "stabO1", "stabO2", "stabOF", "stabT1", "stabT2", "stabTF",
    "shoot1", "shoot2", "shootF", "proneStab",
)
ACTIONS = (*STAND_ACTIONS, "alert", "jump", "fly", "sit", "prone", "ladder", "rope", "heal", *ATTACKS)
EFFECT_ACTIONS = (*ACTIONS, "default", "backDefault")
EFFECT_BELOW, EFFECT_OVER = "itemEffectBelow", "itemEffectOver"
EFFECT_DELAY = 100
MAX_LINK_HOPS = 8

# 由後到前。背面（爬梯子、繩子）的部位都是 back 開頭，放在一起；有些帽子正面的格子也混了 backCap，網頁面向前方時不畫背面這段
ORDER = """
characterStart itemEffectBelow backWing backTamingMobMid backSaddle
hairBelowBody capAccessoryBelowBody capBelowBody capeUnderBody capeBelowBody shieldBelowBody
weaponBelowBodyEffectUnder weaponBelowBody
mailChestBelowBody gloveBelowBody gloveWristBelowBody body gloveWristOverBody gloveOverBody capeOverBody emotionOverBody
mailChestBelowPants pantsBelowShoes shoes pants shoesOverPants pantsOverShoesBelowMailChest shoesTop
mailChest mail pantsOverMailChest mailChestOverPants mailChestTop
armBelowHead mailArmBelowHead armBelowHeadOverMailChest mailArmBelowHeadOverMailChest
gloveBelowHead gloveWristBelowHead mailChestOverHighest cape capeBelowHead weaponOverArmBelowHead
capBelowHead capBelowHair hair shield accessoryEar head accessoryFaceBelowFace accessoryEyeBelowFace face hairShade
accessoryFaceOverFaceBelowCap accessoryFace capAccessoryBelowAccFace accessoryEye
capBelowAccessory cap capAccessory accessoryFaceOverCap accessoryEyeOverCap hairOverHead accessoryEarOverHair capOverHair
shieldBelowArm weaponBelowArmEffectUnder weaponBelowArm arm weaponOverGloveBelowMailArm gloveBelowMailArm gloveWristBelowMailArm mailArm
handBelowWeapon gloveBelowWeapon gloveWristBelowWeapon armOverHairBelowWeapon mailArmOverHairBelowWeapon
weaponEffectUnder weapon weaponOverBodyEffectUnder weaponOverBody weaponOverArm armOverHair mailArmOverHair capeWeapon capeOverWepon
weaponBelowHand weaponOverHand handOverHair glove gloveWrist gloveOverHair gloveWristOverHair
weaponOverGloveEffectUnder weaponOverGlove weaponWristOverGloveEffectUnder weaponWristOverGlove shieldOverHair
capeOverHead capeOverFace
backBody backShoesBelowPants backGloveWrist backGlove backShoes backPantsBelowShoes backPants
backPantsOverShoesBelowMailChest backMailChestBelowPants backMailChest backPantsOverMailChest backMailChestOverPants
backMailChestAccessory backHairBelowHead backHead backAccessoryEar backHair backHairBelowCap backHairBelowCapNarrow
backHairBelowCapWide backCapAccessory backCap backCapOverHair backAccessoryOverHead backMailChestOverHead
backCape backHairOverCape backShieldBelowBody backShield backWeaponEffectUnder backWeapon backWeaponOverShield
backWeaponOverGlove backWeaponOverHead
itemEffectOver characterEnd
""".split()
_RANK = set(ORDER)
_TYPO = {"weapnBelowBody": "weaponBelowBody", "Cape": "cape", "Cp": "cap", "Weapon": "weapon"}

# 時裝的類別：ID 第 2～4 碼 → (頁籤鍵, 圖集資料夾, 中文)
CATEGORIES = {
    "100": ("cap", "Cap", "帽子"), "101": ("faceacc", "Accessory", "臉飾"), "102": ("eyeacc", "Accessory", "眼飾"),
    "103": ("earring", "Accessory", "耳環"), "104": ("coat", "Coat", "上衣"), "105": ("longcoat", "Longcoat", "套服"),
    "106": ("pants", "Pants", "褲裙"), "107": ("shoes", "Shoes", "鞋子"), "108": ("glove", "Glove", "手套"),
    "109": ("shield", "Shield", "盾牌"), "110": ("cape", "Cape", "披風"), "170": ("weapon", "Weapon", "武器"),
}
_COLOR_PREFIX = re.compile(r"^.{1,3}色")


def clean_z(z: Any) -> str:
    """圖層名稱：客戶端有前後多空白與拼錯的；數字（特效）與不認得的放最上層。"""
    name = _TYPO.get(z.strip(), z.strip()) if isinstance(z, str) else ""
    return name if name in _RANK else "characterEnd"


def _is_canvas(node: Any) -> bool:
    return isinstance(node, dict) and "origin" in node


def _node_at(root: dict, path: list[str]) -> Any:
    node: Any = root
    for segment in path:
        node = node.get(segment) if isinstance(node, dict) else None
    return node


def resolve(root: dict, path: list[str]) -> tuple[list[str], Any]:
    """照連結走到真正的圖片，回傳 ``(路徑, 節點)``；斷掉的連結回傳 ``([], None)``。"""
    node = _node_at(root, path)
    for _ in range(MAX_LINK_HOPS):
        if not isinstance(node, str):
            break
        path = list(path[:-1])
        for segment in node.split("/"):
            if segment == "..":
                if not path:
                    return [], None
                path.pop()
            else:
                path.append(segment)
        node = _node_at(root, path)
    if isinstance(node, dict) and not _is_canvas(node) and isinstance(node.get("0"), (dict, str)):
        return resolve(root, [*path, "0"])      # hairShade 是依膚色的一包，內容都連到 0
    return (path, node) if _is_canvas(node) else ([], None)


def _frames(action: dict) -> Iterator[tuple[str, dict]]:
    for key in sorted((k for k in action if k.isdigit()), key=int):
        if isinstance(action[key], dict):
            yield key, action[key]


class Parts:
    """一件東西用到的部位：同一張圖只留一筆，回傳編號。圖怎麼裁交給呼叫端（*path → frame 路徑*）。"""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.index: dict[str, int] = {}

    def add(self, path: list[str], node: dict, z: str | None = None, sheet: str | None = None) -> int:
        """*z* 蓋掉節點自己的圖層；*sheet* 是圖不在這件自己的圖集時，要去哪一包裁（``Effect/ItemEff``）。"""
        key = "/".join(path)
        if key not in self.index:
            self.index[key] = len(self.rows)
            mapping = {k: [round(v.x), round(v.y)] for k, v in (node.get("map") or {}).items() if hasattr(v, "x")}
            origin = node["origin"]
            row = {"path": key, "origin": [round(origin.x), round(origin.y)], "z": z or clean_z(node.get("z")), "map": mapping}
            if sheet:
                row["sheet"] = sheet
            self.rows.append(row)
        return self.index[key]


def collect_actions(root: dict, prefix: list[str], parts: Parts, actions: tuple[str, ...] = ACTIONS) -> dict[str, list[list[int]]]:
    """*prefix* 底下每個動作每一格用到的部位。沒有這個動作就不列，網頁就不畫這件。"""
    out: dict[str, list[list[int]]] = {}
    base = _node_at(root, prefix) if prefix else root
    for name in actions:
        action = base.get(name) if isinstance(base, dict) else None
        if not isinstance(action, dict):
            continue
        rows = []
        for key, frame in _frames(action):
            used = []
            for part, value in frame.items():
                if isinstance(value, (dict, str)):
                    path, node = resolve(root, [*prefix, name, key, part])
                    if node is not None:
                        used.append(parts.add(path, node))
            rows.append(used)
        if any(rows):
            out[name] = rows
    return out


def collect_default(root: dict, parts: Parts) -> dict[str, list[list[int]]]:
    """臉、臉飾只有一張 ``default``，沒有動作，每個動作都用它：輸出成只有一格的 ``default``。"""
    default = root.get("default")
    if not isinstance(default, dict):
        return {}
    used = []
    for part, value in default.items():
        if isinstance(value, (dict, str)):
            path, node = resolve(root, ["default", part])
            if node is not None:
                used.append(parts.add(path, node))
    return {"default": [used]} if used else {}


def collect_effect(item_eff: dict, item: str, parts: Parts, sheet: str) -> dict[str, list[list[int]]]:
    """``ItemEff`` 裡這件的特效：動作 → 每格 ``[部位, 毫秒]``。格可能連到別的動作（``ladder2/0`` → ``../ladder/0``）。"""
    effect = _node_at(item_eff, [item, "effect"])
    if not isinstance(effect, dict):
        return {}
    depth = effect.get("z")
    z = EFFECT_BELOW if isinstance(depth, int) and depth < 0 else EFFECT_OVER
    out = {}
    for name in EFFECT_ACTIONS:
        action = effect.get(name)
        if not isinstance(action, dict):
            continue
        rows = []
        for key in sorted((k for k in action if k.isdigit()), key=int):
            path, node = resolve(item_eff, [item, "effect", name, key])
            if node is not None:
                delay = node.get("delay")
                rows.append([parts.add(path, node, z, sheet), delay if isinstance(delay, int) and delay > 0 else EFFECT_DELAY])
        if rows:
            out[name] = rows
    return out


def pack_key(item_id: int) -> str:
    """這件併進哪一包。GitHub Pages 一次同步上萬個小檔會卡住，所以併成幾十包；
    照類別與 ID 區段分，新增或改版一件只會動到那一包。時裝武器最大，再細切。"""
    if item_id < 30000:
        return "base"                       # 膚色、臉型都很小
    if item_id < 50000:
        return f"hair{item_id // 2000}"
    if item_id >= 1700000:
        return f"weapon{item_id // 200}"
    return f"equip{item_id // 10000}"


def delays(body: dict) -> dict[str, list[int]]:
    """身體每格幾毫秒。坐下、趴下這種只有一格的客戶端沒寫，給個值就好。"""
    out = {}
    for name in ACTIONS:
        action = body.get(name)
        if isinstance(action, dict):
            out[name] = [frame.get("delay") if isinstance(frame.get("delay"), int) else 500 for _, frame in _frames(action)]
    return out


def weapon_groups(tree: dict) -> list[str]:
    """時裝武器有哪些武器類型（``30`` 單手劍…）：底下要有動作的才算。"""
    return sorted(k for k, v in tree.items() if k.isdigit() and isinstance(v, dict) and any(a in v for a in ACTIONS))


# 這一版遊戲有的武器類型（裝備 ID 的第 3、4 碼）：單手劍、單手斧、單手棍、短劍、雙刀、短杖、長杖、雙手劍、雙手斧、雙手棍、槍、矛、弓、弩、拳套、指虎、火槍
WEAPON_TYPES = (30, 31, 32, 33, 34, 37, 38, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49)


def weapon_types(tree: dict, link: dict[str, int]) -> tuple[list[int], dict[str, int]]:
    """時裝武器能當哪些武器類型用，跟遊戲一樣：``(類型, {借圖的類型: 用哪一組圖})``。

    自己有圖的類型都算；``info/exceptLinkCashWeaponData`` 沒開的，另外照客戶端的
    ``LinkCashWeaponData``（類型 → 用哪一組圖，現在都是 30）把表上的類型也算進去。
    """
    own = weapon_groups(tree)
    types = {int(g) for g in own}
    art: dict[str, int] = {}
    info = tree.get("info") if isinstance(tree.get("info"), dict) else {}
    if not info.get("exceptLinkCashWeaponData"):
        for kind, group in link.items():
            if kind.isdigit() and int(kind) in WEAPON_TYPES and int(kind) not in types and str(group) in own:
                types.add(int(kind))
                art[str(int(kind))] = int(group)
    return sorted(types), art


def type_attacks(weapons: dict[int, dict]) -> dict[str, list[str]]:
    """每種武器類型會用的攻擊姿勢：一般（非時裝）武器有一半以上畫了這個姿勢就算。

    客戶端沒有「哪個類型用哪些攻擊」的表，但一般武器只畫自己類型用得到的姿勢。
    *weapons* 是 ``{裝備 ID: 資料樹}``，時裝的會跳過。
    """
    seen: dict[int, list[dict]] = {}
    for item_id, tree in weapons.items():
        info = tree.get("info") if isinstance(tree.get("info"), dict) else {}
        kind = item_id // 10000 % 100
        if kind in WEAPON_TYPES and not info.get("cash"):
            seen.setdefault(kind, []).append(tree)
    return {str(kind): [a for a in ATTACKS if 2 * sum(a in tree for tree in trees) >= len(trees)]
            for kind, trees in sorted(seen.items())}


def style_name(name: str) -> str:
    """``黑色基本冒險造型`` → ``基本冒險造型``：同一款髮型只差顏色。"""
    return _COLOR_PREFIX.sub("", name.strip(), count=1) or name.strip()


def group_styles(ids: list[int], names: Callable[[int], str | None], key: Callable[[int], Any], color: Callable[[int], int], clean: Callable[[str], str]) -> list[dict[str, Any]]:
    """髮型、臉型依款式分組：``{"n": 名稱, "c": {顏色: ID}}``，有名字的才收。"""
    groups: dict[Any, dict[str, Any]] = {}
    for item in sorted(ids):
        name = names(item)
        if not name:
            continue
        group = groups.setdefault(key(item), {"n": clean(name), "c": {}})
        group["c"][color(item)] = item
    return list(groups.values())


def hair_styles(ids: list[int], names: Callable[[int], str | None]) -> list[dict[str, Any]]:
    return group_styles(ids, names, lambda i: i // 10, lambda i: i % 10, style_name)


def face_styles(ids: list[int], names: Callable[[int], str | None]) -> list[dict[str, Any]]:
    return group_styles(ids, names, lambda i: (i // 1000, i % 100), lambda i: i // 100 % 10, str.strip)
