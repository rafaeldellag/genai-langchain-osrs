"""Porte para Python de parte da calculadora de DPS da OSRS Wiki.

Derivado de weirdgloop/osrs-dps-calc (GPL-3.0): src/lib/PlayerVsNPCCalc.ts,
BaseCalc.ts, Equipment.ts e HitDist.ts. Os nomes das funções seguem o original
para facilitar a comparação linha a linha.

Só foi portado o que importa para os bosses do guia (ver guias/): fórmulas base
de acerto e dano, prayers, salve, slayer helm/black mask, dragonbane, demonbane
de arma, void, set de crystal, inquisitor, obsidian, twisted bow, fang, scythe,
blowpipe, bolts encantados, Dizana's quiver, cajados com feitiço embutido e o
limite de dano do Zulrah. Ataques especiais, feitiços de spellbook e efeitos de
raids/wilderness ficam de fora; tests/gabarito compara o resultado com a
calculadora original.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

SECONDS_PER_TICK = 0.6
DEFAULT_ATTACK_SPEED = 4
CAST_STANCES = ("Autocast", "Defensive Autocast", "Manual Cast")
MELEE_TYPES = ("stab", "slash", "crush")

Factor = tuple[int, int]


def trunc_div(a: int, b: int) -> int:
    """Divisão inteira que trunca em direção a zero, como Math.trunc(a / b)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def factor(base: int, f: Factor) -> int:
    return trunc_div(base * f[0], f[1])


def add_factor(base: int, f: Factor) -> int:
    return base + trunc_div(base * f[0], f[1])


@dataclass(slots=True)
class Item:
    id: int
    name: str
    version: str
    slot: str
    category: str
    speed: int
    two_handed: bool
    bonuses: dict[str, int]
    offensive: dict[str, int]
    defensive: dict[str, int]

    @classmethod
    def from_json(cls, raw: dict) -> Item:
        return cls(
            id=raw["id"],
            name=raw["name"],
            version=raw.get("version") or "",
            slot=raw["slot"],
            category=raw.get("category") or "",
            speed=raw.get("speed") or DEFAULT_ATTACK_SPEED,
            two_handed=bool(raw.get("isTwoHanded")),
            bonuses=dict(raw["bonuses"]),
            offensive=dict(raw["offensive"]),
            defensive=dict(raw["defensive"]),
        )


@dataclass(slots=True)
class Monster:
    id: int
    name: str
    version: str
    size: int
    skills: dict[str, int]
    offensive: dict[str, int]
    defensive: dict[str, int]
    attributes: list[str]
    is_slayer_monster: bool

    @classmethod
    def from_json(cls, raw: dict) -> Monster:
        return cls(
            id=raw["id"],
            name=raw["name"],
            version=raw.get("version") or "",
            size=raw.get("size") or 1,
            skills=dict(raw["skills"]),
            offensive=dict(raw["offensive"]),
            defensive=dict(raw["defensive"]),
            attributes=list(raw.get("attributes") or []),
            is_slayer_monster=bool(raw.get("is_slayer_monster")),
        )


@dataclass(frozen=True, slots=True)
class Style:
    name: str
    type: str | None
    stance: str


@dataclass(frozen=True, slots=True)
class Prayer:
    combat_style: str
    accuracy: Factor | None = None
    strength: Factor | None = None
    magic_damage: int = 0


# src/enums/Prayer.ts (só as prayers ofensivas)
PRAYERS: dict[str, Prayer] = {
    "Burst of Strength": Prayer("melee", strength=(105, 100)),
    "Clarity of Thought": Prayer("melee", accuracy=(105, 100)),
    "Superhuman Strength": Prayer("melee", strength=(110, 100)),
    "Improved Reflexes": Prayer("melee", accuracy=(110, 100)),
    "Ultimate Strength": Prayer("melee", strength=(115, 100)),
    "Incredible Reflexes": Prayer("melee", accuracy=(115, 100)),
    "Chivalry": Prayer("melee", accuracy=(115, 100), strength=(118, 100)),
    "Piety": Prayer("melee", accuracy=(120, 100), strength=(123, 100)),
    "Sharp Eye": Prayer("ranged", accuracy=(105, 100), strength=(105, 100)),
    "Hawk Eye": Prayer("ranged", accuracy=(110, 100), strength=(110, 100)),
    "Eagle Eye": Prayer("ranged", accuracy=(115, 100), strength=(115, 100)),
    "Rigour": Prayer("ranged", accuracy=(120, 100), strength=(123, 100)),
    "Mystic Will": Prayer("magic", accuracy=(105, 100)),
    "Mystic Lore": Prayer("magic", accuracy=(110, 100), magic_damage=10),
    "Mystic Might": Prayer("magic", accuracy=(115, 100), magic_damage=20),
    "Augury": Prayer("magic", accuracy=(125, 100), magic_damage=40),
}


@dataclass(slots=True)
class Player:
    skills: dict[str, int]
    boosts: dict[str, int]
    style: Style
    equipment: dict[str, Item]
    prayers: tuple[str, ...] = ()
    dart: Item | None = None
    on_slayer_task: bool = False
    kandarin_diary: bool = True


@dataclass(slots=True)
class Totals:
    bonuses: dict[str, int] = field(
        default_factory=lambda: {"str": 0, "ranged_str": 0, "magic_str": 0, "prayer": 0}
    )
    offensive: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(("stab", "slash", "crush", "magic", "ranged"), 0)
    )


@dataclass(frozen=True, slots=True)
class Tables:
    """Tabelas exportadas da calculadora original (conselheiro/dados/tabelas.json)."""

    styles: dict[str, list[Style]]
    ammo: dict[int, frozenset[int]]
    canonical: dict[int, int]
    blowpipe_ids: frozenset[int]

    @classmethod
    def from_json(cls, raw: dict) -> Tables:
        canonical = {
            variant: int(base) for base, variants in raw["aliases"].items() for variant in variants
        }
        return cls(
            styles={
                category: [Style(s["name"], s["type"], s["stance"]) for s in styles]
                for category, styles in raw["styles"].items()
            },
            ammo={int(w): frozenset(ids) for w, ids in raw["ammo"].items()},
            canonical=canonical,
            blowpipe_ids=frozenset(raw["blowpipeIds"]),
        )

    def ammo_included(self, weapon: Item | None, ammo: Item | None) -> bool:
        if weapon is None or ammo is None:
            return False
        valid = self.ammo.get(weapon.id)
        return bool(valid) and ammo.id in valid

    def ammo_invalid(self, weapon: Item | None, ammo: Item | None) -> bool:
        valid = self.ammo.get(weapon.id) if weapon else None
        return bool(valid) and (ammo is None or ammo.id not in valid)


# ---------------------------------------------------------------------------
# Distribuições de dano (src/lib/HitDist.ts), restritas a um hitsplat por acerto.
# Cada HitDist é {(dano, acertou): probabilidade}; um ataque é uma lista delas.

HitDist = dict[tuple[int, bool], float]
Transformer = Callable[[int, bool], HitDist]


def linear(accuracy: float, minimum: int, maximum: int) -> HitDist:
    dist: HitDist = defaultdict(float)
    prob = accuracy / (maximum - minimum + 1)
    for dmg in range(minimum, maximum + 1):
        dist[(dmg, True)] += prob
    dist[(0, False)] += 1 - accuracy
    return dist


def transform(dist: HitDist, fn: Transformer, transform_inaccurate: bool = True) -> HitDist:
    out: HitDist = defaultdict(float)
    for (dmg, accurate), prob in dist.items():
        if not accurate and not transform_inaccurate:
            out[(dmg, accurate)] += prob
            continue
        for key, p in fn(dmg, accurate).items():
            out[key] += prob * p
    return out


def expected(dist: HitDist) -> float:
    return sum(dmg * prob for (dmg, _), prob in dist.items())


def _bonus_damage(chance: float, bonus: int, accurate_only: bool) -> Transformer:
    def fn(dmg: int, accurate: bool) -> HitDist:
        if not accurate and accurate_only:
            return {(dmg, accurate): 1.0}
        return {(dmg + bonus, accurate): chance, (dmg, accurate): 1 - chance}

    return fn


def _effect_or_hit(chance: float, effect: HitDist, only_accurate: bool = False) -> Transformer:
    def fn(dmg: int, accurate: bool) -> HitDist:
        if only_accurate and not accurate:
            return {(dmg, accurate): 1.0}
        out: HitDist = defaultdict(float)
        for key, p in effect.items():
            out[key] += p * chance
        out[(dmg, accurate)] += 1 - chance
        return out

    return fn


def _capped_reroll(limit: int, roll_max: int, offset: int) -> Transformer:
    # Zulrah: acertos acima de 50 viram 45..50
    def fn(dmg: int, accurate: bool) -> HitDist:
        if dmg <= limit:
            return {(dmg, accurate): 1.0}
        prob = 1.0 / (roll_max + 1)
        return {(i + offset, accurate): prob for i in range(roll_max + 1)}

    return fn


# ---------------------------------------------------------------------------


class Calc:
    """Equivalente a PlayerVsNPCCalc para um jogador e um monstro."""

    def __init__(self, player: Player, monster: Monster, tables: Tables, current_hp: int | None = None):
        self.player = player
        self.monster = monster
        self.tables = tables
        self.current_hp = monster.skills["hp"] if current_hp is None else current_hp
        self.names = {item.name for item in player.equipment.values()}
        self.totals = self._equipment_bonuses()

    # -- equipamento --------------------------------------------------------

    def wearing(self, *names: str) -> bool:
        return any(name in self.names for name in names)

    def _equipment_bonuses(self) -> Totals:
        """calculateEquipmentBonusesFromGear (Equipment.ts)."""
        totals = Totals()
        eq = self.player.equipment
        weapon, ammo = eq.get("weapon"), eq.get("ammo")
        ammo_included = self.tables.ammo_included(weapon, ammo)
        for item in eq.values():
            apply_ranged = item.slot != "ammo" or ammo_included
            for stat, value in item.bonuses.items():
                if stat == "ranged_str" and not apply_ranged:
                    continue
                totals.bonuses[stat] = totals.bonuses.get(stat, 0) + (value or 0)
            for stat, value in item.offensive.items():
                if stat == "ranged" and not apply_ranged:
                    continue
                totals.offensive[stat] += value or 0

        if weapon and weapon.id in self.tables.blowpipe_ids and self.player.dart:
            totals.bonuses["ranged_str"] += self.player.dart.bonuses["ranged_str"]

        if weapon and weapon.name == "Tumeken's shadow" and self.player.style.stance != "Manual Cast":
            totals.bonuses["magic_str"] = min(1000, totals.bonuses["magic_str"] * 3)
            totals.offensive["magic"] *= 3

        if (
            self._name("head") == "Void mage helm"
            and self._name("body") == "Elite void top"
            and self._name("legs") == "Elite void robe"
            and self._name("hands") == "Void knight gloves"
        ):
            totals.bonuses["magic_str"] += 50

        cape = eq.get("cape")
        quiver_charged = cape is not None and (
            cape.name in ("Dizana's max cape", "Blessed Dizana's quiver")
            or (cape.name == "Dizana's quiver" and cape.version == "Charged")
        )
        if quiver_charged and ammo_included:
            totals.offensive["ranged"] += 10
            totals.bonuses["ranged_str"] += 1
        return totals

    def _name(self, slot: str) -> str | None:
        item = self.player.equipment.get(slot)
        return item.name if item else None

    def _attrs(self, attr: str) -> bool:
        return attr in self.monster.attributes

    def is_melee(self) -> bool:
        return self.player.style.type in MELEE_TYPES

    def _combat_prayers(self, kind: str) -> list[Prayer]:
        style = self.player.style.type
        wanted = "melee" if self.is_melee() else ("ranged" if style == "ranged" else "magic")
        prayers = [PRAYERS[p] for p in self.player.prayers if PRAYERS[p].combat_style == wanted]
        return [p for p in prayers if getattr(p, kind)]

    def _void_robes(self) -> bool:
        return (
            self.wearing("Void knight top", "Void knight top (or)", "Elite void top", "Elite void top (or)")
            and self.wearing("Void knight robe", "Void knight robe (or)", "Elite void robe", "Elite void robe (or)")
            and self.wearing("Void knight gloves")
        )

    def _elite_void_robes(self) -> bool:
        return (
            self.wearing("Elite void top", "Elite void top (or)")
            and self.wearing("Elite void robe", "Elite void robe (or)")
            and self.wearing("Void knight gloves")
        )

    def _melee_void(self) -> bool:
        return self._void_robes() and self.wearing("Void melee helm", "Void melee helm (or)")

    def _ranged_void(self) -> bool:
        return self._void_robes() and self.wearing("Void ranger helm", "Void ranger helm (or)")

    def _elite_ranged_void(self) -> bool:
        return self._elite_void_robes() and self.wearing("Void ranger helm", "Void ranger helm (or)")

    def _magic_void(self) -> bool:
        return self._void_robes() and self.wearing("Void mage helm", "Void mage helm (or)")

    def _imbued_black_mask(self) -> bool:
        return self.wearing("Black mask (i)", "Slayer helmet (i)", "V's helm")

    def _black_mask(self) -> bool:
        return self._imbued_black_mask() or self.wearing("Black mask", "Slayer helmet")

    def _slayer_bonus(self) -> bool:
        return self.monster.is_slayer_monster and self.player.on_slayer_task

    def _tzhaar_weapon(self) -> bool:
        return self.wearing(
            "Tzhaar-ket-em", "Tzhaar-ket-om", "Tzhaar-ket-om (t)", "Toktz-xil-ak", "Toktz-xil-ek", "Toktz-mej-tal"
        )

    def _obsidian(self) -> bool:
        return all(n in self.names for n in ("Obsidian helmet", "Obsidian platelegs", "Obsidian platebody"))

    def _crystal_bow(self) -> bool:
        return self.wearing("Crystal bow") or any("Bow of Faerdhinen" in n for n in self.names)

    def _crystal_pieces(self) -> int:
        return (
            (1 if self.wearing("Crystal helm") else 0)
            + (2 if self.wearing("Crystal legs") else 0)
            + (3 if self.wearing("Crystal body") else 0)
        )

    def _fang(self) -> bool:
        return self.wearing("Osmumten's fang", "Osmumten's fang (or)")

    def _scythe(self) -> bool:
        return any("of vitur" in n.lower() for n in self.names)

    def _two_hit_weapon(self) -> bool:
        return self.wearing("Torag's hammers", "Sulphur blades", "Glacial temotli", "Earthbound tecpatl")

    def _inquisitor_bonus(self) -> int:
        return (
            (1 if self.wearing("Inquisitor's great helm") else 0)
            + (2 if self.wearing("Inquisitor's hauberk") else 0)
            + (2 if self.wearing("Inquisitor's plateskirt") else 0)
        )

    def _weapon(self) -> Item | None:
        return self.player.equipment.get("weapon")

    def ammo_invalid(self) -> bool:
        eq = self.player.equipment
        return self.tables.ammo_invalid(eq.get("weapon"), eq.get("ammo"))

    # -- defesa do monstro ----------------------------------------------------

    def npc_defence_roll(self) -> int:
        style = self.player.style.type
        level = self.monster.skills["magic"] if style == "magic" else self.monster.skills["def"]
        if style == "ranged":
            category = self._weapon().category if self._weapon() else ""
            ranged_type = {"Thrown": "light", "Bow": "standard", "Crossbow": "heavy", "Chinchompas": "heavy"}.get(
                category
            )
            d = self.monster.defensive
            bonus = trunc_div(d["light"] + d["standard"] + d["heavy"], 3) if ranged_type is None else d[ranged_type]
        else:
            bonus = self.monster.defensive[style or "crush"]
        return (level + 9) * (bonus + 64)

    # -- melee ------------------------------------------------------------------

    def _melee_attack_roll(self) -> int:
        p, style = self.player, self.player.style
        eff = p.skills["atk"] + p.boosts.get("atk", 0)
        for prayer in self._combat_prayers("accuracy"):
            eff = factor(eff, prayer.accuracy)
        eff += 8 + (3 if style.stance == "Accurate" else 1 if style.stance == "Controlled" else 0)
        if self._melee_void():
            eff = factor(eff, (11, 10))

        base = eff * ((self.totals.offensive[style.type] if style.type else 0) + 64)
        roll = base
        if self.wearing("Salve amulet (e)", "Salve amulet(ei)") and self._attrs("undead"):
            roll = factor(roll, (6, 5))
        elif self.wearing("Salve amulet", "Salve amulet(i)") and self._attrs("undead"):
            roll = factor(roll, (7, 6))
        elif self._black_mask() and self._slayer_bonus():
            roll = factor(roll, (7, 6))

        if self._tzhaar_weapon() and self._obsidian():
            roll += factor(base, (1, 10))
        if self.wearing("Arclight", "Emberlight") and self._attrs("demon"):
            roll = add_factor(roll, (70, 100))
        if self.wearing("Silverlight", "Darklight") and self._attrs("demon"):
            roll = add_factor(roll, (60, 100))
        if self.wearing("Bone claws", "Burning claws") and self._attrs("demon"):
            roll = add_factor(roll, (5, 100))
        if self._attrs("dragon"):
            if self.wearing("Dragon hunter lance"):
                roll = factor(roll, (6, 5))
            elif self.wearing("Dragon hunter wand"):
                roll = factor(roll, (7, 4))
        if style.type == "crush" and (inq := self._inquisitor_bonus()):
            roll = factor(roll, (200 + inq, 200))
        return roll

    def _melee_max_hit(self) -> tuple[int, int]:
        p, style = self.player, self.player.style
        eff = p.skills["str"] + p.boosts.get("str", 0)
        gear = self.totals.bonuses["str"] + 64
        for prayer in self._combat_prayers("strength"):
            if prayer is PRAYERS["Burst of Strength"] and eff <= 20:
                eff += 1
            else:
                eff = factor(eff, prayer.strength)
        eff += 8 + (3 if style.stance == "Aggressive" else 1 if style.stance == "Controlled" else 0)
        if self._melee_void():
            eff = factor(eff, (11, 10))

        base_max = trunc_div(eff * gear + 320, 640)
        min_hit, max_hit = 0, base_max
        if self.wearing("Salve amulet (e)", "Salve amulet(ei)") and self._attrs("undead"):
            max_hit = factor(max_hit, (6, 5))
        elif self.wearing("Salve amulet", "Salve amulet(i)") and self._attrs("undead"):
            max_hit = factor(max_hit, (7, 6))
        elif self._black_mask() and self._slayer_bonus():
            max_hit = factor(max_hit, (7, 6))

        if self.wearing("Arclight", "Emberlight") and self._attrs("demon"):
            max_hit = add_factor(max_hit, (70, 100))
        if self.wearing("Bone claws", "Burning claws") and self._attrs("demon"):
            max_hit = add_factor(max_hit, (5, 100))
        if self._tzhaar_weapon() and self._obsidian():
            max_hit += factor(base_max, (1, 10))
        if self.wearing("Dragon hunter lance") and self._attrs("dragon"):
            max_hit = factor(max_hit, (6, 5))
        if self.wearing("Dragon hunter wand") and self._attrs("dragon"):
            max_hit = factor(max_hit, (7, 5))
        if self.wearing("Silverlight", "Darklight", "Silverlight (dyed)") and self._attrs("demon"):
            max_hit = add_factor(max_hit, (60, 100))
        if self.wearing("Colossal blade"):
            max_hit += min(self.monster.size * 2, 10)
        if style.type == "crush" and (inq := self._inquisitor_bonus()):
            max_hit = factor(max_hit, (200 + inq, 200))
        if self._fang():
            shrink = trunc_div(max_hit * 3, 20)
            min_hit = shrink
            max_hit -= shrink
        return min_hit, max_hit

    # -- ranged -----------------------------------------------------------------

    @staticmethod
    def _tbow_scaling(current: int, magic: int, accuracy_mode: bool) -> int:
        f = 10 if accuracy_mode else 14
        base = 140 if accuracy_mode else 250
        t2 = trunc_div(3 * magic - f, 100)
        t3 = trunc_div((trunc_div(3 * magic, 10) - 10 * f) ** 2, 100)
        bonus = max(0, min(base, base + t2 - t3))
        return trunc_div(current * bonus, 100)

    def _tbow_magic(self) -> int:
        cap = 350 if self._attrs("xerician") else 250
        return min(cap, max(self.monster.skills["magic"], self.monster.offensive.get("magic", 0)))

    def _ranged_attack_roll(self) -> int:
        p, style = self.player, self.player.style
        eff = p.skills["ranged"] + p.boosts.get("ranged", 0)
        for prayer in self._combat_prayers("accuracy"):
            eff = factor(eff, prayer.accuracy)
        if style.stance == "Accurate":
            eff += 3
        eff += 8
        if self._ranged_void():
            eff = factor(eff, (11, 10))

        roll = eff * (self.totals.offensive["ranged"] + 64)
        if self._crystal_bow():
            roll = factor(roll, (20 + self._crystal_pieces(), 20))
        if self.wearing("Salve amulet(ei)") and self._attrs("undead"):
            roll = factor(roll, (6, 5))
        elif self.wearing("Salve amulet(i)") and self._attrs("undead"):
            roll = factor(roll, (7, 6))
        elif self._imbued_black_mask() and self._slayer_bonus():
            roll = factor(roll, (23, 20))

        if self.wearing("Twisted bow"):
            roll = self._tbow_scaling(roll, self._tbow_magic(), True)
        if self.wearing("Dragon hunter crossbow") and self._attrs("dragon"):
            roll = factor(roll, (13, 10))
        if self.wearing("Scorching bow") and self._attrs("demon"):
            roll = add_factor(roll, (30, 100))
        return roll

    def _ranged_max_hit(self) -> tuple[int, int]:
        p, style = self.player, self.player.style
        eff = p.skills["ranged"] + p.boosts.get("ranged", 0)
        for prayer in self._combat_prayers("strength"):
            if prayer is PRAYERS["Sharp Eye"] and eff <= 20:
                eff += 1
            else:
                eff = factor(eff, prayer.strength)
        if style.stance == "Accurate":
            eff += 3
        eff += 8
        if self._elite_ranged_void():
            eff = factor(eff, (9, 8))
        elif self._ranged_void():
            eff = factor(eff, (11, 10))

        max_hit = trunc_div(eff * (64 + self.totals.bonuses["ranged_str"]) + 320, 640)
        if self._crystal_bow():
            max_hit = factor(max_hit, (40 + self._crystal_pieces(), 40))

        dragonbane = self.wearing("Dragon hunter crossbow") and self._attrs("dragon")
        demonbane = self.wearing("Scorching bow") and self._attrs("demon")
        if self.wearing("Salve amulet(ei)") and self._attrs("undead"):
            max_hit = factor(max_hit, (6, 5))
        elif self.wearing("Salve amulet(i)") and self._attrs("undead"):
            max_hit = factor(max_hit, (7, 6))
        elif self._imbued_black_mask() and self._slayer_bonus():
            numerator = 23
            # aditivos com o bônus de slayer
            if dragonbane:
                dragonbane, numerator = False, numerator + 5
            if demonbane:
                demonbane, numerator = False, numerator + 6
            max_hit = factor(max_hit, (numerator, 20))

        if self.wearing("Twisted bow"):
            max_hit = self._tbow_scaling(max_hit, self._tbow_magic(), False)
        if dragonbane:
            max_hit = factor(max_hit, (5, 4))
        if demonbane:
            max_hit = add_factor(max_hit, (30, 100))
        return 0, max_hit

    # -- magia --------------------------------------------------------------------

    def _magic_attack_roll(self) -> int:
        p, style = self.player, self.player.style
        eff = p.skills["magic"] + p.boosts.get("magic", 0)
        for prayer in self._combat_prayers("accuracy"):
            eff = factor(eff, prayer.accuracy)
        if style.stance == "Accurate":
            eff += 2
        eff += 9
        if self._magic_void():
            eff = factor(eff, (29, 20))

        roll = eff * (self.totals.offensive["magic"] + 64)
        additive, black_mask = 0, False
        if self.wearing("Salve amulet(ei)") and self._attrs("undead"):
            additive += 20
        elif self.wearing("Salve amulet(i)") and self._attrs("undead"):
            additive += 15
        elif self._imbued_black_mask() and self._slayer_bonus():
            black_mask = True
        if additive:
            roll = factor(roll, (100 + additive, 100))
        if self._attrs("dragon"):
            if self.wearing("Dragon hunter crossbow"):
                roll = factor(roll, (13, 10))
            elif self.wearing("Dragon hunter lance"):
                roll = factor(roll, (6, 5))
            elif self.wearing("Dragon hunter wand"):
                roll = factor(roll, (7, 4))
        if black_mask:
            roll = factor(roll, (23, 20))
        return roll

    def _powered_staff_base(self, level: int) -> int:
        # Math.trunc(level / 3 - k) == trunc_div(level - 3k, 3)
        w = self._weapon().name if self._weapon() else ""
        if w in ("Trident of the Seas", "Trident of the Seas (e)"):
            return max(1, trunc_div(level - 15, 3))
        if w == "Thammaron's sceptre":
            return max(1, trunc_div(level - 24, 3))
        if w == "Accursed sceptre":
            return max(1, trunc_div(level - 18, 3))
        if w in ("Trident of the Swamp", "Trident of the Swamp (e)"):
            return max(1, trunc_div(level - 6, 3))
        if w in ("Sanguinesti staff", "Holy sanguinesti staff"):
            return max(1, trunc_div(level, 3))
        if w == "Tumeken's shadow":
            return max(1, trunc_div(level, 3) + 1)
        if w == "Eye of Ayak":
            return max(1, trunc_div(level, 3) - 6)
        if w == "Warped sceptre":
            return max(1, trunc_div(8 * level + 96, 37))
        return {
            "Crystal staff (basic)": 23,
            "Corrupted staff (basic)": 23,
            "Crystal staff (attuned)": 31,
            "Corrupted staff (attuned)": 31,
            "Crystal staff (perfected)": 39,
            "Corrupted staff (perfected)": 39,
        }.get(w, 0)

    def _magic_max_hit(self) -> tuple[int, int]:
        level = self.player.skills["magic"] + self.player.boosts.get("magic", 0)
        max_hit = self._powered_staff_base(level)
        if max_hit == 0:
            return 0, 0

        dmg = self.totals.bonuses["magic_str"]
        black_mask = False
        if self.wearing("Salve amulet(ei)") and self._attrs("undead"):
            dmg += 200
        elif self.wearing("Salve amulet(i)") and self._attrs("undead"):
            dmg += 150
        elif self._imbued_black_mask() and self._slayer_bonus():
            black_mask = True
        for prayer in self._combat_prayers("magic_damage"):
            dmg += prayer.magic_damage

        max_hit = add_factor(max_hit, (dmg, 1000))
        if black_mask:
            max_hit = factor(max_hit, (23, 20))
        if self._attrs("dragon"):
            if self.wearing("Dragon hunter lance"):
                max_hit = factor(max_hit, (6, 5))
            elif self.wearing("Dragon hunter wand"):
                max_hit = factor(max_hit, (7, 5))
            elif self.wearing("Dragon hunter crossbow"):
                max_hit = factor(max_hit, (5, 4))
        return 0, max_hit

    # -- agregados ---------------------------------------------------------------

    def max_attack_roll(self) -> int:
        if self.player.style.stance != "Manual Cast" and self.ammo_invalid():
            return 0
        style = self.player.style.type
        if self.is_melee():
            return self._melee_attack_roll()
        if style == "ranged":
            return self._ranged_attack_roll()
        if style == "magic":
            return self._magic_attack_roll()
        return 0

    def min_and_max(self) -> tuple[int, int]:
        if self.player.style.stance != "Manual Cast" and self.ammo_invalid():
            return 0, 0
        style = self.player.style.type
        lo, hi = 0, 0
        if self.is_melee():
            lo, hi = self._melee_max_hit()
        elif style == "ranged":
            lo, hi = self._ranged_max_hit()
        elif style == "magic":
            lo, hi = self._magic_max_hit()
        hi = max(hi, lo)
        return max(lo, 0), max(hi, 0)

    @staticmethod
    def normal_accuracy(atk: int, dfn: int) -> float:
        def std(a: int, d: int) -> float:
            return 1 - (d + 2) / (2 * (a + 1)) if a > d else a / (2 * (d + 1))

        if atk < 0:
            atk = min(0, atk + 2)
        if dfn < 0:
            dfn = min(0, dfn + 2)
        if atk >= 0 and dfn >= 0:
            return std(atk, dfn)
        if atk >= 0 > dfn:
            return 1 - 1 / (-dfn + 1) / (atk + 1)
        if atk < 0 and dfn < 0:
            return std(-dfn, -atk)
        return 0.0

    @staticmethod
    def fang_accuracy(atk: int, dfn: int) -> float:
        # Repete fielmente o original, inclusive a comparação "attack > def" de fora do escopo.
        outer_def = dfn

        def std(a: int, d: int) -> float:
            if a > outer_def:
                return 1 - (d + 2) * (2 * d + 3) / (a + 1) / (a + 1) / 6
            return a * (4 * a + 5) / 6 / (a + 1) / (d + 1)

        def rv(a: int, d: int) -> float:
            if a < outer_def:
                return a * (d * 6 - 2 * a + 5) / 6 / (d + 1) / (d + 1)
            return 1 - (d + 2) * (2 * d + 3) / 6 / (d + 1) / (a + 1)

        if atk < 0:
            atk = min(0, atk + 2)
        if dfn < 0:
            dfn = min(0, dfn + 2)
            outer_def = dfn
        if atk >= 0 and dfn >= 0:
            return std(atk, dfn)
        if atk >= 0 > dfn:
            return 1 - 1 / (-dfn + 1) / (atk + 1)
        if atk < 0 and dfn < 0:
            return rv(-dfn, -atk)
        return 0.0

    def hit_chance(self) -> float:
        atk, dfn = self.max_attack_roll(), self.npc_defence_roll()
        if self._fang() and self.player.style.type == "stab":
            return self.fang_accuracy(atk, dfn)
        return self.normal_accuracy(atk, dfn)

    def is_immune(self) -> bool:
        if self.monster.name == "Zulrah" and self.is_melee():
            return (self._weapon().category if self._weapon() else "") != "Polearm"
        if self._attrs("flying") and self.is_melee():
            return (self._weapon().category if self._weapon() else "") not in ("Polearm", "Salamander")
        return False

    def distribution(self) -> list[HitDist]:
        if self.is_immune():
            return [{(0, False): 1.0}]
        acc = self.hit_chance()
        lo, hi = self.min_and_max()
        style = self.player.style.type
        if hi == 0:
            return [{(0, False): 1.0}]

        standard = linear(acc, lo, hi)
        dists = [standard]
        if style == "ranged" and self.wearing("Dark bow"):
            dists = [standard, standard]
        if self.is_melee() and self._scythe():
            dists = [linear(acc, lo, max(lo, trunc_div(hi, 2**i))) for i in range(min(max(self.monster.size, 1), 3))]
        if self.is_melee() and self._two_hit_weapon():
            first = trunc_div(hi, 2)
            dists = [linear(acc, lo, max(lo, first)), linear(acc, lo, max(lo, hi - first))]

        def apply(fn: Transformer, inaccurate: bool = True) -> None:
            nonlocal dists
            dists = [transform(d, fn, inaccurate) for d in dists]

        if self.wearing("Sanguinesti staff", "Holy sanguinesti staff"):
            apply(lambda d, a: {(d, a): 0.8, (d + 8, a): 0.2}, inaccurate=False)
        if self.is_melee() and self.wearing("Berserker necklace", "Berserker necklace (or)") and self._tzhaar_weapon():
            apply(lambda d, a: {(trunc_div(d * 6, 5), a): 1.0})

        crossbow = style == "ranged" and self._weapon() is not None and self._weapon().category == "Crossbow"
        ranged_lvl = self.player.skills["ranged"] + self.player.boosts.get("ranged", 0)
        kandarin = 1.1 if self.player.kandarin_diary else 1.0
        if crossbow:
            if self.wearing("Opal bolts (e)", "Opal dragon bolts (e)"):
                apply(_bonus_damage(0.05 * kandarin, trunc_div(ranged_lvl, 10), False))
            elif self.wearing("Pearl bolts (e)", "Pearl dragon bolts (e)"):
                divisor = 15 if self._attrs("fiery") else 20
                apply(_bonus_damage(0.06 * kandarin, trunc_div(ranged_lvl, divisor), False))
            elif self.wearing("Diamond bolts (e)", "Diamond dragon bolts (e)"):
                apply(_effect_or_hit(0.1 * kandarin, linear(1.0, 0, trunc_div(hi * 115, 100))))
            elif self.wearing("Dragonstone bolts (e)", "Dragonstone dragon bolts (e)"):
                if not (self._attrs("fiery") or self._attrs("dragon")):
                    apply(_bonus_damage(0.06 * kandarin, trunc_div(ranged_lvl * 2, 10), True))
            elif self.wearing("Onyx bolts (e)", "Onyx dragon bolts (e)") and not self._attrs("undead"):
                apply(_effect_or_hit(0.11 * kandarin, linear(1.0, 0, trunc_div(hi * 120, 100)), only_accurate=True))

        # acertos com dano 0 viram 1
        apply(lambda d, a: {(max(d, 1), True): 1.0}, inaccurate=False)

        player_hp = self.player.skills.get("hp", 99) + self.player.boosts.get("hp", 0)
        if crossbow and self.wearing("Ruby bolts (e)", "Ruby dragon bolts (e)") and player_hp >= 10:
            effect = {(min(100, trunc_div(self.current_hp * 20, 100)), True): 1.0}
            apply(_effect_or_hit(0.06 * kandarin, effect))

        # transformações do monstro (applyNpcTransforms)
        if self.monster.name == "Zulrah":
            apply(_capped_reroll(50, 5, 45))
        flat_armour = self.monster.defensive.get("flat_armour") or 0
        if flat_armour and style != "magic":
            apply(lambda d, a: {(d - flat_armour, a): 1.0}, inaccurate=False)
        return dists

    def attack_speed(self) -> int:
        weapon = self._weapon()
        speed = weapon.speed if weapon else DEFAULT_ATTACK_SPEED
        style = self.player.style
        if style.type == "ranged" and style.stance == "Rapid":
            speed -= 1
        elif style.stance in CAST_STANCES:
            speed = 5
        return max(speed, 1)

    def max_hit(self) -> int:
        return sum(max(d for (d, _) in dist) for dist in self.distribution())

    def expected_hit(self) -> float:
        return sum(expected(dist) for dist in self.distribution())

    def dps(self) -> float:
        return self.expected_hit() / self.attack_speed() / SECONDS_PER_TICK
