"""Gera tests/gabarito/casos.json: setups que exercitam cada efeito portado em calc.py.

Depois de mudar os casos, rode ferramentas/calc-wiki/rodar.sh gabarito para recalcular
os valores esperados com a calculadora original.
"""

from __future__ import annotations

import json
from pathlib import Path

from conselheiro.dados import GameData

ARMA_RANGED = {
    "head": "Armadyl helmet", "body": "Armadyl chestplate", "legs": "Armadyl chainskirt",
    "cape": "Ava's assembler", "hands": "Barrows gloves", "feet": "Pegasian boots",
    "ring": "Archers ring (i)", "neck": "Necklace of anguish",
}
BANDOS_MELEE = {
    "head": "Neitiznot faceguard", "body": "Bandos chestplate", "legs": "Bandos tassets",
    "cape": "Fire cape", "hands": "Ferocious gloves", "feet": "Primordial boots",
    "ring": "Berserker ring (i)", "neck": "Amulet of torture", "shield": "Avernic defender",
}
ANCESTRAL = {
    "head": "Ancestral hat", "body": "Ancestral robe top", "legs": "Ancestral robe bottom",
    "cape": "Imbued Saradomin cape", "hands": "Tormented bracelet", "feet": "Eternal boots",
    "ring": "Magus ring", "neck": "Occult necklace", "shield": "Elidinis' ward (f)",
}
CRYSTAL = {"head": "Crystal helm", "body": "Crystal body", "legs": "Crystal legs"}
VOID_RANGED = {
    "head": "Void ranger helm", "body": "Elite void top", "legs": "Elite void robe", "hands": "Void knight gloves",
}
VOID_MELEE = {
    "head": "Void melee helm", "body": "Void knight top", "legs": "Void knight robe", "hands": "Void knight gloves",
}
INQ = {"head": "Inquisitor's great helm", "body": "Inquisitor's hauberk", "legs": "Inquisitor's plateskirt"}
OBSIDIAN = {"head": "Obsidian helmet", "body": "Obsidian platebody", "legs": "Obsidian platelegs"}

VORKATH = ("Vorkath", "Post-quest")
ZULRAH_GREEN = ("Zulrah", "Serpentine")
ZULRAH_RED = ("Zulrah", "Magma")
ZULRAH_BLUE = ("Zulrah", "Tanzanite")
GRAARDOR = ("General Graardor", "")
CERBERUS = ("Cerberus", "")
MOLE = ("Giant Mole", "")

RANGED = (["Rigour"], {"ranged": 13})
MELEE = (["Piety"], {"atk": 19, "str": 19})
MAGIC = (["Augury"], {"magic": 4})

# (id, boss, equipamento, estilo (nome, postura), prayers/boosts, extras)
CASOS = [
    ("vork-dhcb-ruby-salve", VORKATH, {**ARMA_RANGED, "neck": "Salve amulet(ei)",
     "weapon": "Dragon hunter crossbow", "ammo": "Ruby dragon bolts (e)", "shield": "Twisted buckler"},
     ("Rapid", "Rapid"), RANGED, {}),
    ("vork-dhcb-diamond-salve", VORKATH, {**ARMA_RANGED, "neck": "Salve amulet(ei)",
     "weapon": "Dragon hunter crossbow", "ammo": "Diamond dragon bolts (e)", "shield": "Twisted buckler"},
     ("Rapid", "Rapid"), RANGED, {}),
    ("vork-dhcb-slayer", VORKATH, {**ARMA_RANGED, "head": "Slayer helmet (i)",
     "weapon": "Dragon hunter crossbow", "ammo": "Ruby dragon bolts (e)", "shield": "Twisted buckler"},
     ("Rapid", "Rapid"), RANGED, {"onSlayerTask": True}),
    ("vork-dhl-salve", VORKATH, {**BANDOS_MELEE, "neck": "Salve amulet (e)", "weapon": "Dragon hunter lance"},
     ("Lunge", "Controlled"), MELEE, {}),
    ("vork-tbow-quiver", VORKATH, {**ARMA_RANGED, "neck": "Salve amulet(ei)", "cape": "Blessed Dizana's quiver",
     "weapon": "Twisted bow", "ammo": "Dragon arrow"}, ("Rapid", "Rapid"), RANGED, {}),
    ("vork-bp-dragon", VORKATH, {**ARMA_RANGED, "weapon": "Toxic blowpipe"},
     ("Rapid", "Rapid"), RANGED, {"dart": "Dragon dart"}),
    ("vork-bowfa-crystal", VORKATH, {**ARMA_RANGED, **CRYSTAL, "neck": "Salve amulet(ei)",
     "weapon": "Bow of Faerdhinen"}, ("Rapid", "Rapid"), RANGED, {}),
    ("vork-dhcb-sem-rigour", VORKATH, {**ARMA_RANGED, "weapon": "Dragon hunter crossbow",
     "ammo": "Ruby dragon bolts (e)"}, ("Accurate", "Accurate"), (["Eagle Eye"], {"ranged": 9}),
     {"skills": {"ranged": 80}}),
    ("zul-green-swamp", ZULRAH_GREEN, {**ANCESTRAL, "weapon": "Trident of the Swamp"},
     ("Accurate", "Accurate"), MAGIC, {}),
    ("zul-green-sang", ZULRAH_GREEN, {**ANCESTRAL, "weapon": "Sanguinesti staff"},
     ("Accurate", "Accurate"), MAGIC, {}),
    ("zul-red-shadow", ZULRAH_RED, {**ANCESTRAL, "weapon": "Tumeken's shadow", "shield": None},
     ("Accurate", "Accurate"), MAGIC, {}),
    ("zul-red-seas-lowlvl", ZULRAH_RED, {**ANCESTRAL, "weapon": "Trident of the Seas"},
     ("Accurate", "Accurate"), (["Mystic Might"], {"magic": 4}), {"skills": {"magic": 75}}),
    ("zul-blue-tbow", ZULRAH_BLUE, {**ARMA_RANGED, "weapon": "Twisted bow", "ammo": "Dragon arrow"},
     ("Rapid", "Rapid"), RANGED, {}),
    ("zul-blue-bp-amethyst", ZULRAH_BLUE, {**ARMA_RANGED, "weapon": "Toxic blowpipe"},
     ("Rapid", "Rapid"), RANGED, {"dart": "Amethyst dart"}),
    ("zul-blue-acb-diamond", ZULRAH_BLUE, {**ARMA_RANGED, "weapon": "Armadyl crossbow",
     "ammo": "Diamond bolts (e)"}, ("Rapid", "Rapid"), RANGED, {}),
    ("zul-blue-acb-ruby", ZULRAH_BLUE, {**ARMA_RANGED, "weapon": "Armadyl crossbow",
     "ammo": "Ruby bolts (e)"}, ("Rapid", "Rapid"), RANGED, {}),
    ("zul-whip-imune", ZULRAH_BLUE, {**BANDOS_MELEE, "weapon": "Abyssal whip"}, ("Lash", "Controlled"), MELEE, {}),
    ("graardor-scythe", GRAARDOR, {**BANDOS_MELEE, "weapon": "Scythe of Vitur", "shield": None},
     ("Chop", "Aggressive"), MELEE, {}),
    ("graardor-inq-mace", GRAARDOR, {**BANDOS_MELEE, **INQ, "weapon": "Inquisitor's mace"},
     ("Pummel", "Aggressive"), MELEE, {}),
    ("graardor-obsidian", GRAARDOR, {**BANDOS_MELEE, **OBSIDIAN, "neck": "Berserker necklace",
     "weapon": "Tzhaar-ket-om", "shield": None}, ("Pummel", "Aggressive"), MELEE, {}),
    ("graardor-void-whip", GRAARDOR, {**BANDOS_MELEE, **VOID_MELEE, "weapon": "Abyssal whip"},
     ("Lash", "Controlled"), MELEE, {}),
    ("cerb-arclight-task", CERBERUS, {**BANDOS_MELEE, "head": "Slayer helmet", "weapon": "Arclight"},
     ("Slash", "Aggressive"), MELEE, {"onSlayerTask": True}),
    ("cerb-emberlight", CERBERUS, {**BANDOS_MELEE, "weapon": "Emberlight"}, ("Slash", "Aggressive"), MELEE, {}),
    ("cerb-fang", CERBERUS, {**BANDOS_MELEE, "weapon": "Osmumten's fang"}, ("Lunge", "Aggressive"), MELEE, {}),
    ("cerb-scorching", CERBERUS, {**ARMA_RANGED, "head": "Slayer helmet (i)", "weapon": "Scorching bow",
     "ammo": "Dragon arrow"}, ("Rapid", "Rapid"), RANGED, {"onSlayerTask": True}),
    ("mole-torag", MOLE, {**BANDOS_MELEE, "weapon": "Torag's hammers", "shield": None},
     ("Pummel", "Aggressive"), MELEE, {}),
    ("mole-darkbow", MOLE, {**ARMA_RANGED, "weapon": "Dark bow", "ammo": "Dragon arrow"},
     ("Rapid", "Rapid"), RANGED, {}),
    ("mole-void-bp", MOLE, {**ARMA_RANGED, **VOID_RANGED, "weapon": "Toxic blowpipe"},
     ("Rapid", "Rapid"), RANGED, {"dart": "Adamant dart"}),
    ("mole-dwh-baixo", MOLE, {"weapon": "Dragon warhammer", "body": "Fighter torso", "legs": "Dragon platelegs"},
     ("Pummel", "Aggressive"), (["Ultimate Strength", "Incredible Reflexes"], {"atk": 8, "str": 8}),
     {"skills": {"atk": 70, "str": 70}}),
]


def main() -> None:
    data = GameData.load(with_prices=False)
    cases = []
    for case_id, (boss, version), equipment, (style_name, stance), (prayers, boosts), extra in CASOS:
        resolved = {}
        for slot, name in equipment.items():
            if name is None:
                continue
            item = data.usable.get(name)
            if item is None:
                raise SystemExit(f"{case_id}: item não encontrado: {name}")
            resolved[slot] = item
        weapon = resolved["weapon"]
        style = next(
            s for s in data.tables.styles[weapon.category] if s.name == style_name and s.stance == stance
        )
        monster = data.monster(boss, version)
        case = {
            "id": case_id,
            "monster": {"name": boss, "version": version},
            "currentHp": monster.skills["hp"],
            "equipment": {slot: item.id for slot, item in resolved.items()},
            "style": {"name": style.name, "type": style.type, "stance": style.stance},
            "prayers": [p.upper().replace(" ", "_") for p in prayers],
            "boosts": boosts,
            "onSlayerTask": bool(extra.get("onSlayerTask")),
        }
        if "dart" in extra:
            case["dart"] = data.usable[extra["dart"]].id
        if "skills" in extra:
            case["skills"] = extra["skills"]
        cases.append(case)
    out = Path(__file__).with_name("casos.json")
    out.write_text(json.dumps(cases, indent=1), encoding="utf-8")
    print(f"{len(cases)} casos em {out}")


if __name__ == "__main__":
    main()
