"""Monta o setup com mais DPS dentro do orçamento e a ordem de compra dos upgrades.

Nada aqui usa LLM: os números saem de calc.py com os dados da Wiki e os preços do GE.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from conselheiro.calc import MELEE_TYPES, Calc, Item, Monster, Player, Style
from conselheiro.dados import GameData

ARMOR_SLOTS = ("head", "cape", "neck", "body", "legs", "hands", "feet", "ring", "shield")
# munição e dardos são consumidos: o custo considera uma pilha para começar
AMMO_STACK = 500
MAX_WEAPONS = 14

POWERED_STAVES = {
    "Trident of the Seas", "Trident of the Seas (e)", "Trident of the Swamp", "Trident of the Swamp (e)",
    "Sanguinesti staff", "Holy sanguinesti staff", "Tumeken's shadow", "Thammaron's sceptre",
    "Accursed sceptre", "Eye of Ayak", "Warped sceptre",
}

# itens cujo valor vem de efeito especial, não dos bônus visíveis
EFFECT_ITEMS = {
    "melee": {
        "Salve amulet", "Salve amulet(i)", "Salve amulet (e)", "Salve amulet(ei)", "Black mask",
        "Black mask (i)", "Slayer helmet", "Slayer helmet (i)", "Inquisitor's great helm",
        "Inquisitor's hauberk", "Inquisitor's plateskirt", "Berserker necklace",
        "Void melee helm", "Void knight top", "Void knight robe", "Elite void top", "Elite void robe",
        "Void knight gloves", "Obsidian helmet", "Obsidian platebody", "Obsidian platelegs",
    },
    "ranged": {
        "Salve amulet(i)", "Salve amulet(ei)", "Black mask (i)", "Slayer helmet (i)", "Crystal helm",
        "Crystal body", "Crystal legs", "Dizana's quiver", "Blessed Dizana's quiver", "Void ranger helm",
        "Void knight top", "Void knight robe", "Elite void top", "Elite void robe", "Void knight gloves",
    },
    "magic": {
        "Salve amulet(i)", "Salve amulet(ei)", "Black mask (i)", "Slayer helmet (i)", "Void mage helm",
        "Void knight top", "Void knight robe", "Elite void top", "Elite void robe", "Void knight gloves",
    },
}

# conjuntos que só dão bônus completos
BUNDLES = {
    "melee": [
        ("Void (melee)", ("Void melee helm", "Elite void top", "Elite void robe", "Void knight gloves")),
        ("Void (melee)", ("Void melee helm", "Void knight top", "Void knight robe", "Void knight gloves")),
        ("Obsidian", ("Obsidian helmet", "Obsidian platebody", "Obsidian platelegs")),
    ],
    "ranged": [
        ("Elite void (ranged)", ("Void ranger helm", "Elite void top", "Elite void robe", "Void knight gloves")),
        ("Void (ranged)", ("Void ranger helm", "Void knight top", "Void knight robe", "Void knight gloves")),
    ],
    "magic": [
        ("Elite void (mage)", ("Void mage helm", "Elite void top", "Elite void robe", "Void knight gloves")),
        ("Void (mage)", ("Void mage helm", "Void knight top", "Void knight robe", "Void knight gloves")),
    ],
}

DEFAULT_LEVELS = {"atk": 99, "str": 99, "def": 99, "ranged": 99, "magic": 99, "hp": 99, "prayer": 99}


@dataclass
class Perfil:
    niveis: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_LEVELS))
    rigour: bool = False
    augury: bool = False
    orcamento: int = 0
    itens: list[Item] = field(default_factory=list)
    excluidos: list[Item] = field(default_factory=list)
    # nomes como o jogador escreveu, para itens que não são equipamento (ex.: saturated heart)
    itens_texto: list[str] = field(default_factory=list)
    task: bool = False


@dataclass
class Setup:
    style: Style
    equipment: dict[str, Item]
    dart: Item | None
    dps: float
    custo: int
    detalhes: list[dict] = field(default_factory=list)

    def nomes(self) -> dict[str, str]:
        names = {slot: item.name for slot, item in self.equipment.items()}
        if self.dart:
            names["weapon"] = f"{names['weapon']} ({self.dart.name})"
        return names


@dataclass
class Upgrade:
    slot: str
    de: str | None
    para: str
    custo: int
    ganho_dps: float
    # o jogador já tem o item: é só equipar
    ja_tem: bool = False


@dataclass
class Resultado:
    familia: str
    formas: list[str]
    prayers: tuple[str, ...]
    boosts: dict[str, int]
    pocao: str
    atual: Setup
    recomendado: Setup
    upgrades: list[Upgrade]
    custo_municao: int | None = None


def prayers_para(familia: str, perfil: Perfil) -> tuple[str, ...]:
    prayer, defence = perfil.niveis["prayer"], perfil.niveis["def"]
    if familia == "melee":
        if prayer >= 70 and defence >= 70:
            return ("Piety",)
        if prayer >= 60 and defence >= 65:
            return ("Chivalry",)
        if prayer >= 34:
            return ("Ultimate Strength", "Incredible Reflexes")
        if prayer >= 16:
            return ("Superhuman Strength", "Improved Reflexes")
        return ()
    if familia == "ranged":
        for name, level, needs in (("Rigour", 74, perfil.rigour), ("Eagle Eye", 44, True),
                                   ("Hawk Eye", 26, True), ("Sharp Eye", 8, True)):
            if prayer >= level and needs:
                return (name,)
        return ()
    for name, level, needs in (("Augury", 77, perfil.augury), ("Mystic Might", 45, True),
                               ("Mystic Lore", 27, True), ("Mystic Will", 9, True)):
        if prayer >= level and needs:
            return (name,)
    return ()


def boosts_para(familia: str, perfil: Perfil) -> tuple[str, dict[str, int]]:
    lv = perfil.niveis
    if familia == "melee":
        return "Super combat potion", {"atk": 5 + lv["atk"] * 15 // 100, "str": 5 + lv["str"] * 15 // 100}
    if familia == "ranged":
        return "Ranging potion", {"ranged": 4 + lv["ranged"] // 10}
    texto = " ".join(perfil.itens_texto).casefold()
    if "saturated heart" in texto:
        return "Saturated heart", {"magic": 4 + lv["magic"] // 10}
    if "imbued heart" in texto:
        return "Imbued heart", {"magic": 1 + lv["magic"] // 10}
    return "Magic potion", {"magic": 4}


class Otimizador:
    def __init__(self, data: GameData, perfil: Perfil, formas: list[Monster], familia: str, orcamento: int):
        self.data = data
        self.perfil = perfil
        self.formas = formas
        self.familia = familia
        self.orcamento = orcamento
        self.prayers = prayers_para(familia, perfil)
        self.pocao, self.boosts = boosts_para(familia, perfil)
        self.owned = {item.name for item in perfil.itens}
        self.excluded = {item.name for item in perfil.excluidos}
        self._cache: dict[tuple, float] = {}

    # -- custo e disponibilidade ------------------------------------------------------

    def custo(self, item: Item | None) -> int | None:
        """Quanto custa comprar o item; munição e dardos ficam fora (são gasto por viagem)."""
        if item is None or item.name in self.owned or self._consumivel(item):
            return 0
        return self.data.price(item)

    @staticmethod
    def _consumivel(item: Item) -> bool:
        return item.slot == "ammo" or (item.category == "Thrown" and item.name.endswith(" dart"))

    def disponivel(self, item: Item) -> bool:
        if item.name in self.excluded:
            return False
        return item.name in self.owned or self.data.price(item) is not None

    def custo_total(self, equipment: dict[str, Item], dart: Item | None) -> int:
        return sum(self.custo(i) or 0 for i in equipment.values()) + (self.custo(dart) or 0)

    def custo_municao(self, setup: Setup) -> int | None:
        """Preço de uma pilha de munição/dardos para começar (AMMO_STACK unidades)."""
        ammo = setup.dart or setup.equipment.get("ammo")
        if ammo is None or ammo.name in self.owned:
            return None
        price = self.data.price(ammo)
        return price * AMMO_STACK if price is not None else None

    # -- avaliação --------------------------------------------------------------------

    def _player(self, equipment: dict[str, Item], dart: Item | None, style: Style) -> Player:
        weapon = equipment.get("weapon")
        if weapon is not None and weapon.two_handed and "shield" in equipment:
            # arma de duas mãos não deixa usar escudo
            equipment = {slot: item for slot, item in equipment.items() if slot != "shield"}
        return Player(
            skills=dict(self.perfil.niveis),
            boosts=self.boosts,
            style=style,
            equipment=equipment,
            prayers=self.prayers,
            dart=dart,
            on_slayer_task=self.perfil.task,
        )

    def avaliar(self, equipment: dict[str, Item], dart: Item | None, style: Style) -> float:
        key = (tuple(sorted((s, i.id) for s, i in equipment.items())), dart.id if dart else None, style)
        if key not in self._cache:
            player = self._player(equipment, dart, style)
            dps = [Calc(player, m, self.data.tables, current_hp=m.skills["hp"] // 2).dps() for m in self.formas]
            self._cache[key] = sum(dps) / len(dps)
        return self._cache[key]

    def detalhes(self, setup: Setup) -> list[dict]:
        player = self._player(setup.equipment, setup.dart, setup.style)
        out = []
        for m in self.formas:
            calc = Calc(player, m, self.data.tables, current_hp=m.skills["hp"] // 2)
            out.append({
                "forma": f"{m.name} ({m.version})" if m.version else m.name,
                "dps": round(calc.dps(), 2),
                "max_hit": calc.max_hit(),
                "acerto": round(calc.hit_chance(), 3),
                "velocidade_ticks": calc.attack_speed(),
            })
        return out

    # -- candidatos -------------------------------------------------------------------

    def _relevante(self, item: Item) -> bool:
        if item.name in EFFECT_ITEMS[self.familia]:
            return True
        o, b = item.offensive, item.bonuses
        if self.familia == "melee":
            return max(o["stab"], o["slash"], o["crush"]) > 0 or b["str"] > 0
        if self.familia == "ranged":
            return o["ranged"] > 0 or b["ranged_str"] > 0
        return o["magic"] > 0 or b["magic_str"] > 0

    def _vetor(self, item: Item) -> tuple[int, ...]:
        o, b = item.offensive, item.bonuses
        if self.familia == "melee":
            return (o["stab"], o["slash"], o["crush"], b["str"])
        if self.familia == "ranged":
            return (o["ranged"], b["ranged_str"])
        return (o["magic"], b["magic_str"])

    def _pareto(self, items: list[Item]) -> list[Item]:
        effect = [i for i in items if i.name in EFFECT_ITEMS[self.familia]]
        plain = sorted((i for i in items if i not in effect), key=lambda i: (self.custo(i), [-v for v in self._vetor(i)]))
        kept: list[Item] = []
        for item in plain:
            vec, cost = self._vetor(item), self.custo(item)
            dominated = any(
                self.custo(k) <= cost and all(a >= b for a, b in zip(self._vetor(k), vec)) for k in kept
            )
            if not dominated:
                kept.append(item)
        return kept + effect

    def candidatos_armadura(self) -> dict[str, list[Item]]:
        by_slot: dict[str, list[Item]] = {slot: [] for slot in ARMOR_SLOTS}
        for item in self.data.usable.values():
            if item.slot in by_slot and self._relevante(item) and self.disponivel(item):
                by_slot[item.slot].append(item)
        return {slot: self._pareto(items) for slot, items in by_slot.items()}

    def _municoes(self, weapon: Item) -> list[Item | None]:
        valid = self.data.tables.ammo.get(weapon.id)
        if not valid:
            return [None]
        by_name: dict[str, Item] = {}
        for item in self.data.items:
            if item.id in valid and item.name not in by_name:
                by_name[item.name] = item
        return [a for a in by_name.values() if self.disponivel(a)]

    def _dardos(self) -> list[Item]:
        return [
            i for i in self.data.usable.values()
            if i.category == "Thrown" and i.name.endswith(" dart") and i.name != "Atlatl dart" and self.disponivel(i)
        ]

    def opcoes_arma(self, budget: int) -> list[tuple[Item, Item | None, Item | None, Style]]:
        """Melhor (arma, munição, dardo, estilo) por arma que cabe no orçamento, só com a arma equipada."""
        options = []
        for weapon in self.data.usable.values():
            if weapon.slot != "weapon" or not self.disponivel(weapon):
                continue
            styles = self.data.tables.styles.get(weapon.category, [])
            if self.familia == "melee":
                if not self._relevante(weapon):
                    continue
                styles = [s for s in styles if s.type in MELEE_TYPES]
            elif self.familia == "ranged":
                if weapon.category not in ("Bow", "Crossbow", "Thrown"):
                    continue
                styles = [s for s in styles if s.type == "ranged" and s.stance in ("Rapid", "Accurate")]
            else:
                if weapon.name not in POWERED_STAVES:
                    continue
                styles = [s for s in styles if s.type == "magic" and s.stance == "Accurate"][:1]
            if not styles:
                continue

            is_blowpipe = weapon.id in self.data.tables.blowpipe_ids
            ammo_options = self._municoes(weapon)
            dart_options: list[Item | None] = self._dardos() if is_blowpipe else [None]
            best = None
            for style in styles:
                for ammo in ammo_options:
                    for dart in dart_options:
                        eq = {"weapon": weapon} | ({"ammo": ammo} if ammo else {})
                        cost = self.custo_total(eq, dart)
                        if cost > budget:
                            continue
                        dps = self.avaliar(eq, dart, style)
                        if dps > 0 and (best is None or dps > best[0]):
                            best = (dps, cost, weapon, ammo, dart, style)
            if best:
                options.append(best)

        # fronteira custo x DPS, mais as melhores em DPS
        options.sort(key=lambda o: (o[1], -o[0]))
        frontier, best_dps = [], -1.0
        for opt in options:
            if opt[0] > best_dps:
                frontier.append(opt)
                best_dps = opt[0]
        top = sorted(options, key=lambda o: -o[0])[:4]
        chosen = {id(o): o for o in frontier[-MAX_WEAPONS:] + top}
        return [o[2:] for o in chosen.values()]

    # -- busca ------------------------------------------------------------------------

    def _preencher(self, equipment: dict[str, Item], dart: Item | None, style: Style, budget: int,
                   armor: dict[str, list[Item]]) -> dict[str, Item]:
        """Compra armadura gulosamente pelo maior ganho de DPS por gp até o dinheiro acabar."""
        equipment = dict(equipment)
        two_handed = equipment["weapon"].two_handed if "weapon" in equipment else False
        while True:
            base = self.avaliar(equipment, dart, style)
            spent = self.custo_total(equipment, dart)
            best = None
            moves: list[dict[str, Item]] = []
            for slot, items in armor.items():
                if slot == "shield" and two_handed:
                    continue
                moves.extend({slot: item} for item in items if equipment.get(slot) is not item)
            for _, names in BUNDLES[self.familia]:
                pieces = [self.data.usable.get(n) for n in names]
                if all(p is not None and self.disponivel(p) for p in pieces):
                    moves.append({p.slot: p for p in pieces})
            for move in moves:
                trial = equipment | move
                cost = self.custo_total(trial, dart)
                if cost > budget:
                    continue
                gain = self.avaliar(trial, dart, style) - base
                if gain <= 1e-9:
                    continue
                delta = cost - spent
                ratio = math.inf if delta <= 0 else gain / delta
                key = (ratio, gain)
                if best is None or key > best[0]:
                    best = (key, trial)
            if best is None:
                return equipment
            equipment = best[1]

    def melhor_setup(self, budget: int, base: dict[str, Item] | None = None) -> Setup:
        armor = self.candidatos_armadura()
        base = {s: i for s, i in (base or {}).items() if s in ARMOR_SLOTS}
        best: Setup | None = None
        for weapon, ammo, dart, style in self.opcoes_arma(budget):
            start = base | {"weapon": weapon} | ({"ammo": ammo} if ammo else {})
            if weapon.two_handed:
                start.pop("shield", None)
            if self.custo_total(start, dart) > budget:
                start = {"weapon": weapon} | ({"ammo": ammo} if ammo else {})
            equipment = self._preencher(start, dart, style, budget, armor)
            setup = Setup(style, equipment, dart, self.avaliar(equipment, dart, style), self.custo_total(equipment, dart))
            if best is None or setup.dps > best.dps + 1e-9:
                best = setup
        if best is None:
            # nenhuma arma possível: sem setup para este estilo
            style = self.data.tables.styles[""][0]
            best = Setup(style, {}, None, 0.0, 0)
            return best
        best.detalhes = self.detalhes(best)
        return best

    def ordem_de_compra(self, atual: Setup, alvo: Setup) -> list[Upgrade]:
        """Ordena as trocas atual -> alvo pelo ganho de DPS por gp, aplicando uma de cada vez."""
        moves: dict[str, dict[str, Item | None]] = {}
        weapon_keys = ("weapon", "ammo")
        if any(atual.equipment.get(k) is not alvo.equipment.get(k) for k in weapon_keys) or atual.dart is not alvo.dart:
            moves["weapon"] = {k: alvo.equipment.get(k) for k in weapon_keys}
            if alvo.equipment.get("weapon") and alvo.equipment["weapon"].two_handed:
                moves["weapon"]["shield"] = None
        for slot in ARMOR_SLOTS:
            if slot not in moves.get("weapon", {}) and atual.equipment.get(slot) is not alvo.equipment.get(slot):
                moves[slot] = {slot: alvo.equipment.get(slot)}

        equipment, dart, style = dict(atual.equipment), atual.dart, atual.style
        upgrades: list[Upgrade] = []
        while moves:
            base, spent = self.avaliar(equipment, dart, style), self.custo_total(equipment, dart)
            ranked = []
            for slot, change in moves.items():
                trial = {k: v for k, v in (equipment | change).items() if v is not None}
                t_dart, t_style = (alvo.dart, alvo.style) if slot == "weapon" else (dart, style)
                gain = self.avaliar(trial, t_dart, t_style) - base
                delta = self.custo_total(trial, t_dart) - spent
                ratio = gain / delta if delta > 0 else (math.inf if gain > 0 else -math.inf)
                ranked.append(((ratio, gain), slot, trial, t_dart, t_style, gain, delta))
            _, slot, trial, dart_new, style_new, gain, delta = max(ranked, key=lambda r: r[0])
            old = equipment.get(slot)
            new = trial.get(slot)
            label = new.name if new else "(nada)"
            if slot == "weapon" and dart_new:
                label = f"{label} ({dart_new.name})"
            if slot == "weapon" and trial.get("ammo"):
                label = f"{label} + {trial['ammo'].name}"
            ja_tem = new is not None and new.name in self.owned
            upgrades.append(Upgrade(slot, old.name if old else None, label, max(delta, 0), gain, ja_tem))
            equipment, dart, style = trial, dart_new, style_new
            del moves[slot]
        return upgrades

    def resolver(self) -> Resultado:
        atual = self.melhor_setup(0)
        recomendado = self.melhor_setup(self.orcamento, base=atual.equipment)
        if recomendado.dps < atual.dps:
            recomendado = atual
        return Resultado(
            familia=self.familia,
            formas=[f"{m.name} ({m.version})" if m.version else m.name for m in self.formas],
            prayers=self.prayers,
            boosts=self.boosts,
            pocao=self.pocao,
            atual=atual,
            recomendado=recomendado,
            upgrades=self.ordem_de_compra(atual, recomendado),
            custo_municao=self.custo_municao(recomendado),
        )
