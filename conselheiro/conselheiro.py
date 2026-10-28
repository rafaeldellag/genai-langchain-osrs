"""Uma conversa com o conselheiro: o agente LangChain em cima dos guias, do otimizador e dos preços.

Cada boss tem a própria thread no checkpointer do agente, então o histórico de um não vaza para o outro.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from uuid import uuid4

import httpx
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable
from ollama import ResponseError

from conselheiro.agente import criar_agente
from conselheiro.chains import Avaliacao, Pedido, chain_avaliar
from conselheiro.config import Settings
from conselheiro.dados import GameData
from conselheiro.ferramentas import Contexto
from conselheiro.guias import Guia, carregar_guias, identificar_boss
from conselheiro.otimizador import AMMO_STACK, DEFAULT_LEVELS, Otimizador, Perfil, Resultado, arremesso
from conselheiro.texto import fmt_gp, orcamento_na_mensagem, parse_gp

SLOT_ORDER = ("weapon", "ammo", "head", "cape", "neck", "body", "shield", "legs", "hands", "feet", "ring")
NIVEIS_PEDIDO = {
    "ataque": "atk", "forca": "str", "defesa": "def", "ranged": "ranged",
    "magia": "magic", "vida": "hp", "prayer": "prayer",
}
# o modelo de 4B às vezes preenche nível, task ou Rigour que o jogador nem citou ("90 magic" virava
# ataque 90 também); um campo só vale se a mensagem falar dele
CAMPO_CITADO = {
    campo: re.compile(rf"\b({padrao})\b", re.IGNORECASE)
    for campo, padrao in {
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
    }.items()
}
NIVEIS_EM_TUDO_RE = re.compile(r"\b(tudo|todos|todas|all)\b", re.IGNORECASE)
# o estilo escolhido precisa estar na mensagem: "90 magic" não autoriza o modelo a escolher melee
ESTILO_CITADO = {
    "melee": re.compile(r"\bmelee\b", re.IGNORECASE),
    "ranged": re.compile(r"\branged?\b", re.IGNORECASE),
    "magic": re.compile(r"\bmag(?:e|ia|ic)\b", re.IGNORECASE),
}


def explicar_erro(erro: Exception, settings: Settings) -> str | None:
    """Mensagem para o jogador quando o Ollama falha; None se o erro é outro."""
    if isinstance(erro, httpx.ConnectError):
        return f"O Ollama não respondeu em {settings.ollama_base_url}. Ele está rodando?"
    if isinstance(erro, ResponseError):
        if erro.status_code == 404:
            return f"O modelo {settings.ollama_model} não está instalado. Rode: ollama pull {settings.ollama_model}"
        return f"O Ollama devolveu um erro: {erro.error}"
    return None


@dataclass
class Resposta:
    boss: str | None
    ferramentas: list[str]
    tabela: str | None
    avaliacao: Avaliacao | None
    texto: str


@dataclass
class Estado:
    pedido: Pedido = field(default_factory=Pedido)
    boss: str | None = None
    # orçamento e avaliação por boss; o histórico de cada boss fica na thread dele no checkpointer
    orcamentos: dict[str, str] = field(default_factory=dict)
    avaliacoes: dict[str, tuple[str, Avaliacao]] = field(default_factory=dict)
    threads: set[str] = field(default_factory=set)


def mesclar(anterior: Pedido, novo: Pedido) -> Pedido:
    """O que veio agora vale; o que não veio continua como estava. Listas de itens acumulam."""
    dados = anterior.model_dump()
    for campo, valor in novo.model_dump().items():
        if campo in ("itens_que_tenho", "itens_excluidos"):
            dados[campo] = list(dict.fromkeys(dados[campo] + valor))
        elif valor is not None:
            dados[campo] = valor
    return Pedido(**dados)


class Conselheiro:
    """Uma conversa. O agente, os dados e os guias podem ser compartilhados entre conversas."""

    def __init__(
        self,
        settings: Settings | None = None,
        data: GameData | None = None,
        guias: dict[str, Guia] | None = None,
        agente: Runnable | None = None,
        avaliar: Runnable | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.data = data or GameData.load(self.settings)
        self.guias = guias or carregar_guias()
        self.agente = agente or criar_agente(self.settings)
        self.avaliar = avaliar or chain_avaliar(self.settings)
        self.sessao = uuid4().hex
        self.estado = Estado()

    # -- conversa -----------------------------------------------------------------------

    def _escolher_boss(self, mensagem: str) -> str | None:
        citado = identificar_boss(mensagem, self.guias)
        if citado and citado != self.estado.boss:
            self.estado.boss = citado
            # task e estilo valem para um boss; níveis e itens são do jogador
            self.estado.pedido.na_task = None
            self.estado.pedido.estilo = None
            if citado in self.estado.orcamentos:
                self.estado.pedido.orcamento = self.estado.orcamentos[citado]
        return self.estado.boss

    def _boss_sem_guia(self, mensagem: str) -> str | None:
        """Boss citado que não tem guia ("e kree'arra?"); o modelo pequeno às vezes inventava sobre ele."""
        texto = mensagem.casefold().replace("’", "'")
        com_guia = {g.boss.casefold() for g in self.guias.values()}
        com_guia |= {forma.casefold() for g in self.guias.values() for s in g.setups for forma, _ in s.formas}
        for nome in self.data.monstros_fortes:
            chave = nome.casefold()
            if chave in com_guia or not re.search(rf"(?<![\w']){re.escape(chave)}(?![\w'])", texto):
                continue
            # "blood moon chestplate", "kraken tentacle": é item, não boss
            if any(chave in item and item in texto for item in self.data.nomes_de_itens):
                continue
            return nome
        return None

    def eventos(self, mensagem: str) -> Iterator[dict]:
        """Roda o agente em streaming: ferramentas chamadas, cálculo, pedaços do texto e a resposta final."""
        if identificar_boss(mensagem, self.guias) is None and (outro := self._boss_sem_guia(mensagem)):
            texto = (
                f"Ainda não tenho guia para {outro}. Por enquanto eu ajudo com: {', '.join(self.guias)}. "
                "Diga qual deles e, se quiser, seu orçamento e o que você já tem."
            )
            yield {"tipo": "fim", "boss": None, "ferramentas": [], "texto": texto, "tabela": None, "avaliacao": None}
            return
        boss = self._escolher_boss(mensagem)
        thread = f"{self.sessao}:{boss or 'geral'}"
        self.estado.threads.add(thread)
        para_modelo = mensagem
        if gp := orcamento_na_mensagem(mensagem):
            # "120kk" vira "120m gp" para o modelo não ler como 120 mil
            para_modelo = mensagem.replace(gp, f"{fmt_gp(parse_gp(gp))} gp")

        final, calculo, ferramentas = "", {"tabela": None, "avaliacao": None}, []
        for modo, dado in self.agente.stream(
            {"messages": [HumanMessage(para_modelo)]},
            {"configurable": {"thread_id": thread}},
            context=Contexto(self, boss, mensagem),
            stream_mode=["updates", "messages"],
        ):
            if modo == "messages":
                pedaco, meta = dado
                # só o texto do agente; a chain de avaliação dentro da ferramenta também gera tokens
                if meta.get("langgraph_node") == "model" and isinstance(pedaco, AIMessageChunk) and pedaco.text:
                    yield {"tipo": "token", "texto": pedaco.text}
                continue
            for atualizacao in dado.values():
                for m in (atualizacao or {}).get("messages", []):
                    if isinstance(m, AIMessage) and m.tool_calls:
                        for chamada in m.tool_calls:
                            ferramentas.append(chamada["name"])
                            yield {"tipo": "ferramenta", "nome": chamada["name"], "args": chamada["args"]}
                    elif isinstance(m, ToolMessage) and m.artifact:
                        calculo = m.artifact
                        yield {"tipo": "calculo", **calculo}
                    elif isinstance(m, AIMessage):
                        final = m.text
        yield {"tipo": "fim", "boss": boss, "ferramentas": ferramentas, "texto": final, **calculo}

    def responder(self, mensagem: str) -> Resposta:
        fim = list(self.eventos(mensagem))[-1]
        avaliacao = Avaliacao(**fim["avaliacao"]) if fim["avaliacao"] else None
        return Resposta(fim["boss"], fim["ferramentas"], fim["tabela"], avaliacao, fim["texto"])

    def encerrar(self) -> None:
        """Apaga as threads desta conversa do checkpointer."""
        for thread in self.estado.threads:
            self.agente.checkpointer.delete_thread(thread)
        self.estado = Estado()

    # -- o que as ferramentas do agente chamam ---------------------------------------------

    def calcular_para_agente(self, boss: str | None, novo: Pedido, mensagem: str) -> tuple[str, dict | None]:
        if boss is None:
            return f"Nenhum boss escolhido. Pergunte ao jogador qual destes: {', '.join(self.guias)}.", None
        if gp := orcamento_na_mensagem(mensagem):
            novo.orcamento = gp
        if novo.estilo and not ESTILO_CITADO[novo.estilo].search(mensagem):
            novo.estilo = None
        em_tudo = bool(NIVEIS_EM_TUDO_RE.search(mensagem))
        for campo, citado in CAMPO_CITADO.items():
            nivel = campo in NIVEIS_PEDIDO
            if getattr(novo, campo) is not None and not citado.search(mensagem) and not (nivel and em_tudo):
                setattr(novo, campo, None)
        self.estado.pedido = mesclar(self.estado.pedido, novo)
        if self.estado.pedido.orcamento:
            self.estado.orcamentos[boss] = self.estado.pedido.orcamento

        guia = self.guias[boss]
        resultados, perfil, tabela = self.calcular(guia)
        avaliacao = self._avaliar(guia, resultados, perfil)
        conteudo = tabela
        if avaliacao:
            conteudo += (
                f"\n\nAvaliação do setup atual: {avaliacao.veredito}. "
                f"Fortes: {'; '.join(avaliacao.pontos_fortes) or '-'}. "
                f"Fracos: {'; '.join(avaliacao.pontos_fracos) or '-'}."
            )
        artefato = {"tabela": tabela, "avaliacao": avaliacao.model_dump() if avaliacao else None}
        return conteudo, artefato

    def avaliar_compra(self, boss: str | None, nome: str) -> str:
        if boss is None:
            return f"Nenhum boss escolhido. Pergunte ao jogador qual destes: {', '.join(self.guias)}."
        item = self.data.find_item(nome)
        if item is None:
            return f"Não achei o equipamento {nome}."
        guia = self.guias[boss]
        perfil, _, _ = self._perfil(guia)
        if item.name in {i.name for i in perfil.itens}:
            return f"O jogador já tem {item.name}."
        preco = self.data.price(item)
        if preco is not None and arremesso(item):
            preco *= AMMO_STACK
        linhas = [
            f"{item.name}: " + (f"custa {fmt_gp(preco)} no GE" if preco is not None else "não se compra no GE")
        ]
        com_item = replace(perfil, itens=[*perfil.itens, item])
        for setup in guia.setups:
            formas = [self.data.monster(nome_forma, versao) for nome_forma, versao in setup.formas]
            antes = Otimizador(self.data, perfil, formas, setup.estilo, 0).melhor_setup(0)
            depois = Otimizador(self.data, com_item, formas, setup.estilo, 0).melhor_setup(0)
            ganho = depois.dps - antes.dps
            if ganho <= 0.005:
                linhas.append(f"- setup {setup.estilo}: não muda nada com o que o jogador tem hoje")
                continue
            por_milhao = f", {ganho / preco * 10**6:.2f} DPS por milhão" if preco else ""
            linhas.append(
                f"- setup {setup.estilo}: {antes.dps:.2f} -> {depois.dps:.2f} DPS ({ganho:+.2f}{por_milhao}), "
                "comparando só com o que o jogador já tem"
            )
        return "\n".join(linhas)

    def preco(self, nome: str) -> str:
        item = self.data.find_item(nome)
        if item is not None:
            preco = self.data.price(item)
            if preco is None:
                return f"{item.name} não é vendido no GE."
            extra = f" (uma pilha de {AMMO_STACK}: {fmt_gp(preco * AMMO_STACK)})" if arremesso(item) else ""
            return f"{item.name}: {fmt_gp(preco)} no GE{extra}."
        nome_ge = self.data.nome_no_ge(nome)
        if nome_ge is None:
            return f"Não achei {nome} no GE."
        return f"{nome_ge}: {fmt_gp(self.data.ge_price(nome_ge))} no GE."

    # -- cálculo ----------------------------------------------------------------------------

    def _perfil(self, guia: Guia) -> tuple[Perfil, list[str], list[str]]:
        pedido = self.estado.pedido
        niveis = dict(DEFAULT_LEVELS)
        informados = []
        for campo, chave in NIVEIS_PEDIDO.items():
            valor = getattr(pedido, campo)
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

    # -- avaliação (chain prompt | llm | parser, chamada de dentro da ferramenta) ---------

    @staticmethod
    def _avaliados(guia: Guia, resultados: list[Resultado]) -> list[Resultado]:
        if guia.multi_setup:
            return resultados
        # basta um estilo: avalia só o melhor que o jogador consegue usar hoje
        usaveis = [r for r in resultados if r.atual.dps > 0]
        return usaveis[:1] or resultados

    def veredito_pela_regra(self, guia: Guia, resultados: list[Resultado]) -> str:
        avaliados = self._avaliados(guia, resultados)
        if any(r.atual.dps == 0 for r in avaliados):
            return "arriscado"
        proporcoes = [r.atual.dps / r.recomendado.dps for r in avaliados if r.recomendado.dps]
        return "adequado" if proporcoes and min(proporcoes) >= 0.7 else "fraco"

    def resumo_avaliacao(self, guia: Guia, resultados: list[Resultado]) -> str:
        resultados = self._avaliados(guia, resultados)
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
        # a regra do veredito é objetiva e o modelo de 4B às vezes a ignora (DPS 0 no setup de magia virou
        # "fraco"); o modelo escreve os pontos fortes e fracos, o código confere o veredito
        avaliacao = avaliacao.model_copy(update={"veredito": self.veredito_pela_regra(guia, resultados)})
        self.estado.avaliacoes[guia.boss] = (resumo, avaliacao)
        return avaliacao
