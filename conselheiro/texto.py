"""Dinheiro em gp e limpeza do texto do modelo."""

from __future__ import annotations

import re

# o prompt pede até 6 frases; acima de 12 (contando itens de lista) é o modelo pequeno repetindo a mesma frase
MAX_FRASES = 12
FIM_DE_FRASE = re.compile(r"[.!?]\n+|[.!?](?=\s|$)|\n+")

MULTIPLICADORES = {
    "k": 10**3, "mil": 10**3,
    "m": 10**6, "kk": 10**6, "mi": 10**6, "milhao": 10**6, "milhoes": 10**6,
    "b": 10**9, "bi": 10**9, "kkk": 10**9, "bilhao": 10**9, "bilhoes": 10**9,
}
ORCAMENTO_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(kkk|kk|k|mi|m|bi|b|mil|milh(?:ão|ao|ões|oes)|bilh(?:ão|ao|ões|oes))\b",
    re.IGNORECASE,
)


def orcamento_na_mensagem(mensagem: str) -> str | None:
    """Acha "50m", "120kk", "1.2b"... direto no texto; mais confiável que o modelo pequeno para isso."""
    match = ORCAMENTO_RE.search(mensagem)
    return match.group(0) if match else None


def parse_gp(texto: str | None) -> int | None:
    """'50m' -> 50_000_000; '1.2b', '300k', '80kk' (gíria BR para milhões) e '50 milhões' também valem."""
    if not texto:
        return None
    t = texto.casefold().replace("ã", "a").replace("õ", "o").replace(" ", "").replace("gp", "")
    match = re.fullmatch(r"(\d+(?:[.,]\d+)?)([a-z]*)", t)
    if not match:
        return None
    numero = float(match.group(1).replace(",", "."))
    sufixo = match.group(2)
    if sufixo:
        mult = MULTIPLICADORES.get(sufixo)
        return round(numero * mult) if mult else None
    # número solto pequeno quase sempre quer dizer milhões ("tenho 50")
    return round(numero * 10**6) if numero < 1000 else round(numero)


def fmt_gp(valor: int | None) -> str:
    if valor is None:
        return "?"
    for limite, sufixo in ((10**9, "b"), (10**6, "m"), (10**3, "k")):
        if abs(valor) >= limite:
            return f"{valor / limite:.1f}{sufixo}".replace(".0" + sufixo, sufixo)
    return str(valor)


def _marcador_de_lista(texto: str, pos: int) -> bool:
    """O "2." de uma lista numerada não termina frase."""
    inicio_da_linha = texto.rfind("\n", 0, pos) + 1
    return texto[inicio_da_linha:pos].strip().isdigit()


def _fins_de_frase(texto: str) -> list[int]:
    return [m.end() for m in FIM_DE_FRASE.finditer(texto) if not _marcador_de_lista(texto, m.start())]


def limitar_frases(texto: str, maximo: int = MAX_FRASES) -> str:
    """Corta o texto depois da enésima frase (ou linha), mantendo a formatação."""
    fins = _fins_de_frase(texto)
    return texto[: fins[maximo - 1]].strip() if len(fins) >= maximo else texto.strip()


def cortar_frase_incompleta(texto: str) -> str:
    """Resposta que parou no limite de tokens ("Itens essenciais:"): fica até a última frase terminada."""
    fins = [m.end() for m in re.finditer(r"[.!?](?=\s|$)", texto) if not _marcador_de_lista(texto, m.start())]
    return texto[: fins[-1]].strip() if fins else texto.strip()


def tirar_frases_com(texto: str, termos: list[str]) -> str:
    """Remove as frases (ou linhas) que citam algum dos termos."""
    termos = [t.casefold() for t in termos]
    inicios = [0, *_fins_de_frase(texto)]
    frases = [texto[a:b] for a, b in zip(inicios, [*inicios[1:], len(texto)])]
    return "".join(f for f in frases if not any(t in f.casefold().replace("’", "'") for t in termos)).strip()


def limpar_resposta(texto: str, cortada: bool = False) -> str:
    """Tira o markdown que o modelo insiste em usar e corta respostas em loop ou interrompidas."""
    texto = re.sub(r"^(\s*)\*\s+", r"\1- ", texto, flags=re.MULTILINE)  # "*   item" vira "- item"
    texto = limitar_frases(re.sub(r"^#+\s*", "", texto, flags=re.MULTILINE).replace("*", ""))
    return cortar_frase_incompleta(texto) if cortada or texto.endswith(":") else texto
