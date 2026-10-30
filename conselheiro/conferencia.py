"""O que o código confere no que o modelo de 4B escreve. Os middlewares do agente chamam estas funções.

- conferir_pedido: os argumentos de calcular_setup contra a mensagem do jogador. O modelo preenchia níveis, task e
  estilo que o jogador nem citou ("90 magic" virava ataque 90 também) e errava valores ("80 defesa" virou 77).
- itens_sem_fonte: equipamento citado na resposta que não veio do cálculo, do guia nem da conversa (o cálculo dizia
  Dragon crossbow e a resposta falava em Dragon hunter crossbow).
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from pydantic import ValidationError

from conselheiro.chains import Pedido
from conselheiro.texto import orcamento_na_mensagem

NIVEIS_PEDIDO = {
    "ataque": "atk", "forca": "str", "defesa": "def", "ranged": "ranged",
    "magia": "magic", "vida": "hp", "prayer": "prayer",
}
NOMES_DOS_CAMPOS = {
    "ataque": r"ataque|attack|atk|att",
    "forca": r"for[çc]a|strength|str",
    "defesa": r"defesa|defen[cs]e|def",
    "ranged": r"ranged?|rng",
    "magia": r"magia|magic|mage|mag",
    "vida": r"vida|hp|hitpoints?",
    "prayer": r"prayer|pray|reza|ora[çc][ãa]o",
    "rigour": r"rigour",
    "augury": r"augury",
    "na_task": r"task",
}
# um campo só vale se a mensagem falar dele
CAMPO_CITADO = {campo: re.compile(rf"\b({nomes})\b", re.IGNORECASE) for campo, nomes in NOMES_DOS_CAMPOS.items()}
# "85 range", "80 de defesa", "atk 90", "prayer: 77"; o número antes do nome tem prioridade
NIVEL_CITADO = {
    campo: (
        re.compile(rf"\b(\d{{1,2}})\s*(?:de\s+)?(?:{NOMES_DOS_CAMPOS[campo]})\b", re.IGNORECASE),
        re.compile(rf"\b(?:{NOMES_DOS_CAMPOS[campo]})\s*[:=]?\s*(\d{{1,2}})\b", re.IGNORECASE),
    )
    for campo in NIVEIS_PEDIDO
}
NIVEIS_EM_TUDO_RE = re.compile(r"\b(tudo|todos|todas|all)\b", re.IGNORECASE)
# o estilo escolhido precisa estar na mensagem: "90 magic" não autoriza o modelo a escolher melee
ESTILO_CITADO = {
    "melee": re.compile(r"\bmelee\b", re.IGNORECASE),
    "ranged": re.compile(r"\branged?\b", re.IGNORECASE),
    "magic": re.compile(r"\bmag(?:e|ia|ic)\b", re.IGNORECASE),
}


def _nivel_citado(campo: str, mensagem: str) -> int | None:
    for padrao in NIVEL_CITADO[campo]:
        if (match := padrao.search(mensagem)) and 1 <= int(match.group(1)) <= 99:
            return int(match.group(1))
    return None


def conferir_pedido(args: dict, mensagem: str) -> dict:
    """Argumentos de calcular_setup corrigidos pelo que a mensagem diz de fato."""
    try:
        pedido = Pedido.model_validate(args)
    except ValidationError:
        return args  # a ferramenta devolve o erro de validação para o modelo
    if gp := orcamento_na_mensagem(mensagem):
        pedido.orcamento = gp  # "120kk": o regex vale mais que o modelo
    if pedido.estilo and not ESTILO_CITADO[pedido.estilo].search(mensagem):
        pedido.estilo = None
    em_tudo = bool(NIVEIS_EM_TUDO_RE.search(mensagem))
    for campo, citado in CAMPO_CITADO.items():
        nivel = campo in NIVEIS_PEDIDO
        if nivel and (numero := _nivel_citado(campo, mensagem)) is not None:
            setattr(pedido, campo, numero)
        elif getattr(pedido, campo) is not None and not citado.search(mensagem) and not (nivel and em_tudo):
            setattr(pedido, campo, None)
    return pedido.model_dump(exclude_defaults=True)


def _normalizar(texto: str) -> str:
    return texto.casefold().replace("’", "'")


def itens_sem_fonte(texto: str, fonte: str, equipamentos: Mapping[str, str]) -> list[str]:
    """Equipamentos citados em `texto` que não aparecem em `fonte`, escritos como estão no texto.

    `equipamentos` vai de nome ou apelido ("dhcb") para o nome do item, tudo em minúsculas.
    """
    normal, fonte = _normalizar(texto), _normalizar(fonte)
    achados = {}
    for nome in equipamentos:
        if nome in normal and (match := re.search(rf"(?<![\w']){re.escape(nome)}(?![\w'])", normal)):
            achados[nome] = texto[match.start():match.end()] if len(normal) == len(texto) else nome
    # "dragon hunter crossbow" também contém "crossbow": fica só o nome mais longo
    return [
        escrito
        for nome, escrito in achados.items()
        if not any(nome != outro and nome in outro for outro in achados)
        and nome not in fonte
        and equipamentos[nome] not in fonte
    ]
