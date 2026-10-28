"""Junta guias, otimizador e chains numa conversa com histórico separado por boss."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from langchain_core.exceptions import OutputParserException
from langchain_core.runnables import Runnable

from conselheiro.chains import Avaliacao, Pedido, chain_avaliar, chain_interpretar, chain_responder
from conselheiro.config import Settings
from conselheiro.dados import GameData
from conselheiro.guias import Guia, carregar_guias, identificar_boss
from conselheiro.otimizador import AMMO_STACK, DEFAULT_LEVELS, Otimizador, Perfil, Resultado

# janela do histórico: as 4 interações (pergunta + resposta) mais recentes de cada boss
JANELA_HISTORICO = 4
# o prompt pede até 6 frases; acima de 8 é o modelo pequeno repetindo a mesma frase em loop
MAX_FRASES = 8
FIM_DE_FRASE = re.compile(r"[.!?]\n+|[.!?](?=\s|$)|\n+")
SLOT_ORDER = ("weapon", "ammo", "head", "cape", "neck", "body", "shield", "legs", "hands", "feet", "ring")
NIVEIS_PEDIDO = {
    "ataque": "atk", "forca": "str", "defesa": "def", "ranged": "ranged",
    "magia": "magic", "vida": "hp", "prayer": "prayer",
}
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



def limitar_frases(texto: str, maximo: int = MAX_FRASES) -> str:
    """Corta o texto depois da enésima frase (ou linha), mantendo a formatação."""
    fins = [m.end() for m in FIM_DE_FRASE.finditer(texto)]
    return texto[: fins[maximo - 1]].strip() if len(fins) >= maximo else texto.strip()


@dataclass
class Resposta:
    boss: str | None
    intencao: str
    tabela: str | None
    avaliacao: Avaliacao | None
    texto: str


@dataclass
class Estado:
    pedido: Pedido = field(default_factory=Pedido)
    boss: str | None = None
    # tudo por boss, para uma conversa não vazar para a outra
    historicos: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    orcamentos: dict[str, str] = field(default_factory=dict)
    avaliacoes: dict[str, tuple[str, Avaliacao]] = field(default_factory=dict)


def mesclar(anterior: Pedido, novo: Pedido) -> Pedido:
    """O que veio na mensagem nova vale; o que ela não disse continua como estava."""
    dados = anterior.model_dump()
    for campo, valor in novo.model_dump().items():
        if campo == "niveis":
            dados["niveis"].update({k: v for k, v in valor.items() if v is not None})
        elif campo in ("itens_que_tenho", "itens_excluidos"):
            dados[campo] = list(dict.fromkeys(dados[campo] + valor))
        elif campo == "intencao" or valor is not None:
            dados[campo] = valor
    return Pedido(**dados)


class Conselheiro:
    def __init__(
        self,
        settings: Settings | None = None,
        data: GameData | None = None,
        guias: dict[str, Guia] | None = None,
        interpretar: Runnable | None = None,
        avaliar: Runnable | None = None,
        responder: Runnable | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.data = data or GameData.load(self.settings)
        self.guias = guias or carregar_guias()
        self.interpretar = interpretar or chain_interpretar(self.settings)
        self.avaliar = avaliar or chain_avaliar(self.settings)
        self.responder_chain = responder or chain_responder(self.settings)
        self.estado = Estado()

    # -- passo 1: entender a mensagem ------------------------------------------------

    def _interpretar(self, mensagem: str) -> Pedido:
        anterior = self.estado.pedido.model_dump(exclude_defaults=True, exclude={"intencao"})
        try:
            return self.interpretar.invoke(
                {
                    "bosses": ", ".join(self.guias),
                    "pedido_anterior": json.dumps(anterior, ensure_ascii=False) or "{}",
                    "mensagem": mensagem,
                }
            )
        except OutputParserException:
            # o modelo devolveu algo fora do esquema: segue só com o que já sabíamos
            return Pedido()

    # -- passo 2: calcular -------------------------------------------------------------

    def _perfil(self, guia: Guia) -> tuple[Perfil, list[str], list[str]]:
        pedido = self.estado.pedido
        niveis = dict(DEFAULT_LEVELS)
        informados = []
        for campo, chave in NIVEIS_PEDIDO.items():
            valor = getattr(pedido.niveis, campo)
            if valor is not None:
                niveis[chave] = max(1, min(99, valor))
                informados.append(chave)
        itens, desconhecidos = [], []
        for nome in pedido.itens_que_tenho:
            item = self.data.find_item(nome)
            if item:
                itens.append(item)
            else:
                desconhecidos.append(nome)
        excluidos = [i for i in (self.data.find_item(n) for n in pedido.itens_excluidos) if i]
        perfil = Perfil(
            niveis=niveis,
            rigour=bool(pedido.rigour),
            augury=bool(pedido.augury),
            orcamento=parse_gp(pedido.orcamento) or 0,
            itens=itens,
            excluidos=excluidos,
            itens_texto=list(pedido.itens_que_tenho),
            task=guia.task == "sempre" or bool(pedido.na_task),
        )
        return perfil, informados, desconhecidos

    def calcular(self, guia: Guia) -> tuple[list[Resultado], Perfil, str]:
        perfil, informados, desconhecidos = self._perfil(guia)
        setups = guia.setups
        estilo = self.estado.pedido.estilo
        if not guia.multi_setup and estilo and any(s.estilo == estilo for s in setups):
            setups = [s for s in setups if s.estilo == estilo]

        resultados = []
        for setup in setups:
            formas = [self.data.monster(nome, versao) for nome, versao in setup.formas]
            orcamento = round(perfil.orcamento * setup.peso) if guia.multi_setup else perfil.orcamento
            resultados.append(Otimizador(self.data, perfil, formas, setup.estilo, orcamento).resolver())
        if not guia.multi_setup:
            resultados.sort(key=lambda r: -r.recomendado.dps)
        return resultados, perfil, self._tabela(guia, resultados, perfil, informados, desconhecidos)

    def _tabela(self, guia: Guia, resultados: list[Resultado], perfil: Perfil,
                informados: list[str], desconhecidos: list[str]) -> str:
        linhas = [f"== {guia.boss} · orçamento {fmt_gp(perfil.orcamento)} gp =="]
        premissas = []
        faltando = [k for k in DEFAULT_LEVELS if k not in informados]
        if faltando:
            premissas.append(f"nível 99 assumido em {', '.join(faltando)}")
        premissas.append("na task" if perfil.task else "fora de task")
        if not perfil.rigour:
            premissas.append("sem Rigour")
        if not perfil.augury:
            premissas.append("sem Augury")
        linhas.append("Premissas: " + " · ".join(premissas))
        if perfil.itens:
            linhas.append("Você tem: " + ", ".join(i.name for i in perfil.itens))
        if desconhecidos:
            linhas.append("Não reconheci como equipamento: " + ", ".join(desconhecidos))
        tenho = {i.name for i in perfil.itens}
        nao_tem = [n for n in guia.intransferiveis if n not in tenho]
        if nao_tem:
            linhas.append("Fora do GE (diga se tiver, muda a conta): " + ", ".join(nao_tem))

        hp_boss = {}
        for setup in guia.setups:
            for nome, versao in setup.formas:
                hp_boss[f"{nome} ({versao})" if versao else nome] = self.data.monster(nome, versao).skills["hp"]

        for r in resultados:
            rec, atual = r.recomendado, r.atual
            titulo = f"[{r.familia}] {' / '.join(r.formas)}"
            if guia.multi_setup:
                titulo += f" · parte do orçamento: {fmt_gp(round(perfil.orcamento * self._peso(guia, r)))}"
            linhas.append("")
            linhas.append(titulo)
            linhas.append(f"  Prayer: {', '.join(r.prayers) or 'nenhuma'} · poção: {r.pocao}")
            if rec.dps == 0:
                linhas.append("  Nenhuma arma possível com esse orçamento e esses itens.")
                continue
            if guia.multi_setup:
                # numa luta com troca de estilo, o tempo de kill de um setup sozinho engana
                hp_boss = {}
            linhas.append(f"  Recomendado: {rec.dps:.2f} DPS{self._kill(rec, hp_boss)} · custo {fmt_gp(rec.custo)}")
            linhas.append("  " + self.linha_estilo(rec, r))
            for slot in SLOT_ORDER:
                if slot in rec.equipment:
                    linhas.append(f"    {slot:7} {rec.nomes(tenho)[slot]}")
            for d in rec.detalhes:
                linhas.append(f"    · {d['forma']}: {d['dps']} DPS, max hit {d['max_hit']}, acerto {d['acerto']:.0%}")
            if atual.dps > 0:
                linhas.append(f"  Atual (só o que você tem): {atual.dps:.2f} DPS{self._kill(atual, hp_boss)}")
            if r.upgrades:
                linhas.append("  Ordem de compra:")
                for n, u in enumerate(r.upgrades, 1):
                    preco = "já tem" if u.ja_tem else fmt_gp(u.custo)
                    linhas.append(f"    {n}. {u.slot:7} {u.para}  {u.ganho_dps:+.2f} DPS  {preco}")
            if r.custo_municao:
                ammo = rec.dart or rec.equipment.get("ammo")
                linhas.append(f"  Munição: {AMMO_STACK}x {ammo.name} ≈ {fmt_gp(r.custo_municao)} (fora do orçamento)")

        estilos = {r.familia for r in resultados}
        inventario = guia.inventario_para(estilos)
        if inventario:
            linhas.append("")
            linhas.append("Inventário do guia (preço GE):")
            total = 0
            for i in inventario:
                preco = self.data.ge_price(i.item)
                total += (preco or 0) * i.qtd
                linhas.append(f"  {i.qtd:>3}x {i.item} ≈ {fmt_gp(preco * i.qtd if preco else None)} — {i.motivo}")
            linhas.append(f"  Total ≈ {fmt_gp(total)} por viagem")
        linhas.append("")
        linhas.append("DPS de máximo dano (não considera defesa). Preços: OSRS Wiki, atualizados a cada 10 min.")
        return "\n".join(linhas)

    def linha_estilo(self, setup, r: Resultado) -> str:
        """Tipo de ataque usado e as defesas do boss, para o modelo não precisar deduzir."""
        defesas = []
        for forma in r.formas:
            nome, _, versao = forma.partition(" (")
            d = self.data.monster(nome, versao.rstrip(")")).defensive
            ranged = d["heavy"] if d["heavy"] == d["standard"] == d["light"] else f"{d['light']}/{d['standard']}/{d['heavy']}"
            defesas.append(
                f"stab {d['stab']}, slash {d['slash']}, crush {d['crush']}, magic {d['magic']}, ranged {ranged}"
            )
        texto = "; ".join(f"{f}: {d}" for f, d in zip(r.formas, defesas)) if len(defesas) > 1 else defesas[0]
        return f"Estilo: {setup.style.name} ({setup.style.type}) · defesa do boss (menor é melhor): {texto}"

    @staticmethod
    def _peso(guia: Guia, r: Resultado) -> float:
        return next(s.peso for s in guia.setups if s.estilo == r.familia)

    @staticmethod
    def _kill(setup, hp_boss: dict[str, int]) -> str:
        if len(setup.detalhes) != 1 or setup.dps <= 0:
            return ""
        hp = hp_boss.get(setup.detalhes[0]["forma"])
        return f" (~{hp / setup.dps:.0f} s de dano por kill)" if hp else ""

    # -- passo 3: avaliar e responder ----------------------------------------------------

    def resumo_avaliacao(self, guia: Guia, resultados: list[Resultado]) -> str:
        if not guia.multi_setup:
            # basta um estilo: avalia só os que o jogador consegue usar hoje
            usaveis = [r for r in resultados if r.atual.dps > 0]
            resultados = usaveis[:1] if usaveis else resultados
        usados = list(dict.fromkeys(i.name for r in resultados for i in r.atual.equipment.values()))
        linhas = [f"Boss: {guia.boss}", "O jogador tem: " + (", ".join(usados) or "nada útil para este boss")]
        if guia.multi_setup:
            linhas.append("A luta usa TODOS os setups abaixo (troca de estilo).")
        for r in resultados:
            atual, rec = r.atual, r.recomendado
            proporcao = atual.dps / rec.dps if rec.dps else 0
            usados = ", ".join(atual.nomes().values()) or "nenhum (sem arma para este estilo)"
            linhas.append(
                f"- setup {r.familia}: DPS atual {atual.dps:.2f} com: {usados}; "
                f"DPS recomendado com o orçamento {rec.dps:.2f}; proporção atual/recomendado {proporcao:.0%}"
            )
            if rec.dps > 0:
                linhas.append("  " + self.linha_estilo(rec, r))
        return "\n".join(linhas)

    def _avaliar(self, guia: Guia, resultados: list[Resultado], perfil: Perfil) -> Avaliacao | None:
        if not perfil.itens:
            return None
        resumo = self.resumo_avaliacao(guia, resultados)
        anterior = self.estado.avaliacoes.get(guia.boss)
        if anterior and anterior[0] == resumo:
            return anterior[1]
        try:
            avaliacao = self.avaliar.invoke({"guia": guia.texto, "resumo": resumo})
        except OutputParserException:
            return None
        self.estado.avaliacoes[guia.boss] = (resumo, avaliacao)
        return avaliacao

    def _sem_guia(self, novo: Pedido) -> Resposta:
        texto = (
            f"Ainda não tenho guia para {novo.boss or 'esse boss'}. Por enquanto eu ajudo com: "
            f"{', '.join(self.guias)}. Diga qual deles e, se quiser, seu orçamento e o que você já tem."
        )
        return Resposta(None, novo.intencao, None, None, texto)

    def responder(self, mensagem: str) -> Resposta:
        novo = self._interpretar(mensagem)
        mensagem_para_modelo = mensagem
        if gp := orcamento_na_mensagem(mensagem):
            novo.orcamento = gp
            if novo.intencao == "duvida":
                novo.intencao = "setup"
            # "120kk" vira "120m gp" para o modelo não ler como 120 mil
            mensagem_para_modelo = mensagem.replace(gp, f"{fmt_gp(parse_gp(gp))} gp")
        citado = identificar_boss(mensagem, self.guias) or identificar_boss(novo.boss or "", self.guias)
        if citado is None and novo.boss:
            # citou um boss que não tem guia: não responde com o guia do boss anterior
            return self._sem_guia(novo)
        boss = citado or self.estado.boss
        self.estado.pedido = mesclar(self.estado.pedido, novo)
        if boss != self.estado.boss:
            # task, estilo e orçamento valem para um boss; níveis e itens são do jogador
            self.estado.pedido.na_task = novo.na_task
            self.estado.pedido.estilo = novo.estilo
            if novo.orcamento is None and boss in self.estado.orcamentos:
                self.estado.pedido.orcamento = self.estado.orcamentos[boss]
        if boss is None:
            return self._sem_guia(novo)
        self.estado.boss = boss
        if self.estado.pedido.orcamento:
            self.estado.orcamentos[boss] = self.estado.pedido.orcamento
        guia = self.guias[boss]

        tabela, avaliacao = None, None
        if novo.intencao == "duvida":
            calculos = "(a pergunta é sobre mecânica: responda só com o guia)"
        else:
            resultados, perfil, tabela = self.calcular(guia)
            calculos = tabela
            avaliacao = self._avaliar(guia, resultados, perfil)

        historico = self.estado.historicos.setdefault(boss, [])
        texto = self.responder_chain.invoke(
            {
                "guia": guia.texto,
                "calculos": calculos,
                "avaliacao": avaliacao.model_dump_json() if avaliacao else "(sem setup atual para avaliar)",
                "historico": historico,
                "mensagem": mensagem_para_modelo,
            }
        )
        texto = limitar_frases(texto.replace("**", ""))
        historico.extend([("human", mensagem), ("ai", texto)])
        del historico[: max(0, len(historico) - 2 * JANELA_HISTORICO)]
        return Resposta(boss, novo.intencao, tabela, avaliacao, texto)
