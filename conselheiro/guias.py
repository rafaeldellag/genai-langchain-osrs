"""Guias por boss (guias/*.md): cabeçalho YAML com a configuração e o texto que vai no prompt."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

GUIAS_DIR = Path(__file__).resolve().parent.parent / "guias"


class ItemInventario(BaseModel):
    item: str
    qtd: int = 1
    motivo: str = ""
    # quando preenchido, o item só vale para esse estilo
    estilo: Literal["melee", "ranged", "magic"] | None = None


class SetupGuia(BaseModel):
    estilo: Literal["melee", "ranged", "magic"]
    formas: list[tuple[str, str]]
    # fração do orçamento quando a luta usa mais de um setup (ex.: Zulrah)
    peso: float = 1.0


class Guia(BaseModel):
    boss: str
    apelidos: list[str]
    fonte: str
    task: Literal["sempre", "opcional"] = "opcional"
    setups: list[SetupGuia]
    intransferiveis: list[str] = Field(default_factory=list)
    inventario: list[ItemInventario] = Field(default_factory=list)
    texto: str = ""

    @property
    def multi_setup(self) -> bool:
        """True quando a luta exige trocar de estilo (todos os setups são usados juntos)."""
        return sum(s.peso for s in self.setups) <= 1.0 + 1e-9 and len(self.setups) > 1

    def inventario_para(self, estilos: set[str]) -> list[ItemInventario]:
        return [i for i in self.inventario if i.estilo is None or i.estilo in estilos]


def _parse(path: Path) -> Guia:
    raw = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.DOTALL)
    if not match:
        raise ValueError(f"{path.name}: falta o cabeçalho YAML entre '---'")
    header = yaml.safe_load(match.group(1))
    return Guia(**header, texto=match.group(2).strip())


def carregar_guias(pasta: Path = GUIAS_DIR) -> dict[str, Guia]:
    guias = [_parse(p) for p in sorted(pasta.glob("*.md"))]
    return {g.boss: g for g in guias}


def identificar_boss(texto: str, guias: dict[str, Guia]) -> str | None:
    """Acha o boss pelo nome ou apelido; o mais longo vence ("general graardor" antes de "bandos")."""
    alvo = f" {re.sub(r'[^a-z0-9 ]', ' ', texto.casefold())} "
    candidatos = [
        (len(apelido), guia.boss)
        for guia in guias.values()
        for apelido in [guia.boss.casefold(), *guia.apelidos]
        if f" {apelido} " in alvo
    ]
    return max(candidatos)[1] if candidatos else None
