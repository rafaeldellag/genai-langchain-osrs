"""Itens e monstros (dados da calculadora de DPS da Wiki) e preços do Grand Exchange."""

from __future__ import annotations

import difflib
import json
import time
from dataclasses import dataclass, replace
from functools import cached_property
from pathlib import Path
from typing import Any

import httpx

from conselheiro.calc import Item, Monster, Tables
from conselheiro.config import Settings

CDN_URL = "https://tools.runescape.wiki/osrs-dps/cdn/json"
PRICES_URL = "https://prices.runescape.wiki/api/v1/osrs/latest"
MAPPING_URL = "https://prices.runescape.wiki/api/v1/osrs/mapping"
TABLES_PATH = Path(__file__).resolve().parent / "dados" / "tabelas.json"

GAME_DATA_TTL = 7 * 24 * 3600
PRICES_TTL = 10 * 60

# Versões que não servem para lutar ou que são cópias de minigame.
UNUSABLE_VERSIONS = (
    "Locked", "Broken", "Inactive", "Uncharged", "Empty", "Deadman", "Emir's Arena",
    "Soul Wars", "Nightmare Zone", "Last Man Standing", "Bounty Hunter", "Beta",
)
PREFERRED_VERSIONS = {"": 3, "Charged": 3, "Active": 3, "Normal": 2}

# Apelidos usados pelos jogadores (o LLM às vezes devolve assim).
ITEM_ALIASES = {
    "dhcb": "Dragon hunter crossbow",
    "dhl": "Dragon hunter lance",
    "tbow": "Twisted bow",
    "bp": "Toxic blowpipe",
    "blowpipe": "Toxic blowpipe",
    "bowfa": "Bow of Faerdhinen",
    "sang": "Sanguinesti staff",
    "shadow": "Tumeken's shadow",
    "fang": "Osmumten's fang",
    "swamp trident": "Trident of the Swamp",
    "toxic trident": "Trident of the Swamp",
    "trident": "Trident of the Seas",
    "salve ei": "Salve amulet(ei)",
    "salve (ei)": "Salve amulet(ei)",
    "salve e": "Salve amulet (e)",
    "salve": "Salve amulet",
    "slayer helm": "Slayer helmet",
    "slayer helm (i)": "Slayer helmet (i)",
    "slayer helm i": "Slayer helmet (i)",
    "fire cape": "Fire cape",
    "infernal": "Infernal cape",
    "torso": "Fighter torso",
    "bgs": "Bandos godsword",
    "dwh": "Dragon warhammer",
    "acb": "Armadyl crossbow",
    "zcb": "Zaryte crossbow",
    "anguish": "Necklace of anguish",
    "torture": "Amulet of torture",
    "occult": "Occult necklace",
    "zenyte ring": "Ring of suffering",
    "b ring": "Berserker ring (i)",
    "berserker ring": "Berserker ring (i)",
    "archers ring": "Archers ring (i)",
    "seers ring": "Seers ring (i)",
    "barrows gloves": "Barrows gloves",
    "void": "Elite void top",
    "dizana": "Dizana's quiver",
    "quiver": "Dizana's quiver",
}


def _cached_json(url: str, path: Path, ttl: int, settings: Settings) -> Any:
    if path.exists() and time.time() - path.stat().st_mtime < ttl:
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        response = httpx.get(
            url, headers={"User-Agent": settings.user_agent}, timeout=60, follow_redirects=True
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        # um cache vencido é melhor do que nenhum dado
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        raise
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _version_rank(item: Item) -> tuple[int, int]:
    power = sum(item.offensive.values()) + sum(item.bonuses.values())
    return power, PREFERRED_VERSIONS.get(item.version, 1)


@dataclass
class GameData:
    items: list[Item]
    monsters: list[Monster]
    tables: Tables
    prices: dict[int, int]
    # nome -> id de todos os itens negociáveis (inclui poções e comida)
    ge_ids: dict[str, int]

    @classmethod
    def load(cls, settings: Settings | None = None, with_prices: bool = True) -> GameData:
        settings = settings or Settings()
        cache = settings.cache_dir
        raw_items = _cached_json(f"{CDN_URL}/equipment.json", cache / "equipment.json", GAME_DATA_TTL, settings)
        raw_monsters = _cached_json(f"{CDN_URL}/monsters.json", cache / "monsters.json", GAME_DATA_TTL, settings)
        items = [Item.from_json(raw) for raw in raw_items]
        prices: dict[int, int] = {}
        ge_ids: dict[str, int] = {}
        if with_prices:
            latest = _cached_json(PRICES_URL, cache / "latest.json", PRICES_TTL, settings)
            for raw_id, entry in latest.get("data", {}).items():
                values = [v for v in (entry.get("high"), entry.get("low")) if isinstance(v, int)]
                if values:
                    prices[int(raw_id)] = round(sum(values) / len(values))
            mapping = _cached_json(MAPPING_URL, cache / "mapping.json", GAME_DATA_TTL, settings)
            ge_ids = {entry["name"]: entry["id"] for entry in mapping if "name" in entry and "id" in entry}
            # a calculadora chama de "(Deadman Mode)" itens que o jogo normal negocia no GE com o nome simples
            ge_names = {item_id: name for name, item_id in ge_ids.items()}
            items = [
                replace(i, name=ge_names[i.id]) if i.name.endswith(" (Deadman Mode)") and i.id in ge_names else i
                for i in items
            ]
        return cls(
            items=items,
            monsters=[Monster.from_json(raw) for raw in raw_monsters],
            tables=Tables.from_json(json.loads(TABLES_PATH.read_text(encoding="utf-8"))),
            prices=prices,
            ge_ids=ge_ids,
        )

    def ge_price(self, name: str) -> int | None:
        """Preço de qualquer item negociável pelo nome exato (poções, comida, runas...)."""
        item_id = self.ge_ids.get(name)
        return self.prices.get(item_id) if item_id is not None else None

    @cached_property
    def _by_id(self) -> dict[int, Item]:
        return {item.id: item for item in self.items}

    @cached_property
    def _ids_by_name(self) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        for item in self.items:
            out.setdefault(item.name, []).append(item.id)
        return out

    @cached_property
    def usable(self) -> dict[str, Item]:
        """Uma versão de combate por nome, só entre itens canônicos (sem cosméticos)."""
        best: dict[str, Item] = {}
        for item in self.items:
            if item.id in self.tables.canonical:
                continue
            if any(bad in item.version for bad in UNUSABLE_VERSIONS):
                continue
            current = best.get(item.name)
            if current is None or _version_rank(item) > _version_rank(current):
                best[item.name] = item
        return best

    @cached_property
    def _lower_names(self) -> dict[str, str]:
        return {name.casefold(): name for name in self._ids_by_name}

    def canonical(self, item: Item) -> Item:
        base_id = self.tables.canonical.get(item.id)
        if base_id is None:
            return item
        base = self._by_id.get(base_id)
        return self.usable.get(base.name, base) if base else item

    def find_item(self, name: str) -> Item | None:
        """Busca tolerante: nome exato, apelido conhecido ou nome parecido."""
        key = name.strip().casefold()
        if not key:
            return None
        resolved = ITEM_ALIASES.get(key)
        exact = self._lower_names.get(resolved.casefold() if resolved else key)
        if exact is None:
            close = difflib.get_close_matches(key, self._lower_names, n=1, cutoff=0.85)
            if not close:
                return None
            exact = self._lower_names[close[0]]
        if exact in self.usable:
            return self.usable[exact]
        # variantes (ex.: "Black slayer helmet (i)") viram o item canônico
        for item_id in self._ids_by_name[exact]:
            canonical = self.canonical(self._by_id[item_id])
            if canonical.name in self.usable:
                return self.usable[canonical.name]
        return None

    def price(self, item: Item) -> int | None:
        """Menor preço no GE entre as versões com o mesmo nome; None se não é negociável."""
        values = [self.prices[i] for i in self._ids_by_name.get(item.name, []) if i in self.prices]
        return min(values) if values else None

    def monster(self, name: str, version: str = "") -> Monster:
        for monster in self.monsters:
            if monster.name == name and monster.version == version:
                return monster
        raise KeyError(f"Monstro não encontrado: {name} ({version or 'sem versão'})")
