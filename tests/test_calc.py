"""Compara o porte (conselheiro/calc.py) com a calculadora original da Wiki.

Os valores esperados vêm de ferramentas/calc-wiki/rodar.sh gabarito.
"""

import json
from pathlib import Path

import pytest

from conselheiro.calc import Calc, Player, Style, trunc_div
from conselheiro.dados import GameData

GABARITO = Path(__file__).parent / "gabarito"
CASOS = json.loads((GABARITO / "casos.json").read_text(encoding="utf-8"))
ESPERADO = {r["id"]: r for r in json.loads((GABARITO / "esperado.json").read_text(encoding="utf-8"))}


@pytest.fixture(scope="module")
def data() -> GameData:
    return GameData.load(with_prices=False)


def build(case: dict, data: GameData) -> Calc:
    by_id = {item.id: item for item in data.items}
    skills = {"atk": 99, "str": 99, "def": 99, "ranged": 99, "magic": 99, "hp": 99, "prayer": 99}
    skills.update(case.get("skills", {}))
    player = Player(
        skills=skills,
        boosts=case["boosts"],
        style=Style(**case["style"]),
        equipment={slot: by_id[item_id] for slot, item_id in case["equipment"].items()},
        prayers=tuple(p.replace("_", " ").title() for p in case["prayers"]),
        dart=by_id[case["dart"]] if "dart" in case else None,
        on_slayer_task=case["onSlayerTask"],
    )
    monster = data.monster(case["monster"]["name"], case["monster"]["version"])
    return Calc(player, monster, data.tables, current_hp=case["currentHp"])


@pytest.mark.parametrize("case", CASOS, ids=[c["id"] for c in CASOS])
def test_igual_a_calculadora_da_wiki(case: dict, data: GameData) -> None:
    calc = build(case, data)
    expected = ESPERADO[case["id"]]

    assert calc.attack_speed() == expected["attackSpeed"]
    assert calc.max_hit() == expected["maxHit"]
    if expected["maxHit"]:
        assert calc.max_attack_roll() == expected["maxAttackRoll"]
        assert calc.npc_defence_roll() == expected["npcDefRoll"]
        assert calc.hit_chance() == pytest.approx(expected["accuracy"], rel=1e-12)
    assert calc.dps() == pytest.approx(expected["dps"], rel=1e-9, abs=1e-12)


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [(7, 2, 3), (-7, 2, -3), (7, -2, -3), (-7, -2, 3), (0, 5, 0)],
)
def test_trunc_div_igual_math_trunc(a: int, b: int, expected: int) -> None:
    assert trunc_div(a, b) == expected
