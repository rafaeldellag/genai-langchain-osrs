"""Ferramentas do agente. O modelo decide quando chamar cada uma e com quais argumentos; os números saem do
otimizador e dos preços do GE, nunca do modelo.

O `ToolRuntime` injeta o contexto da conversa (sessão, boss, mensagem original) sem que o modelo veja esses
parâmetros, e `content_and_artifact` manda o texto para o modelo e a tabela/avaliação para a interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain.tools import ToolRuntime, tool

from conselheiro.chains import Pedido


@dataclass
class Contexto:
    """O que cada execução do agente recebe fora das mensagens."""

    # é um Conselheiro; com o tipo de verdade o pydantic tenta montar esquema dele e cai num import circular
    conselheiro: Any
    boss: str | None
    mensagem: str  # a mensagem como o jogador escreveu, para conferir os argumentos


@tool(args_schema=Pedido, response_format="content_and_artifact")
def calcular_setup(runtime: ToolRuntime[Contexto], **pedido: Any) -> tuple[str, dict | None]:
    """Calcula o setup de maior DPS para o boss da conversa dentro do orçamento, a ordem de compra dos upgrades,
    o inventário do guia com preços, e avalia o setup que o jogador já tem.
    Chame sempre que o jogador falar de orçamento, níveis, itens que tem, o que comprar, upgrades, ou perguntar se o
    setup dele serve. Passe só o que a mensagem nova disse: o que ele disse antes já está guardado.
    Exemplo: "tenho 50m, 90 range, rigour e bp" -> orcamento "50m", ranged 90, rigour true,
    itens_que_tenho ["Toxic blowpipe"]. Um número vale para todos os atributos listados:
    "90 de ataque, força e defesa" -> ataque 90, forca 90, defesa 90; "70 em tudo" -> todos os níveis 70."""
    ctx = runtime.context
    return ctx.conselheiro.calcular_para_agente(ctx.boss, Pedido(**pedido))


@tool
def avaliar_compra(item: str, runtime: ToolRuntime[Contexto]) -> str:
    """Quanto DPS o jogador ganha contra o boss da conversa se comprar um item específico, e quanto ele custa.
    Use quando perguntarem se vale a pena comprar um item. `item` é o nome oficial em inglês (ex.: Masori mask)."""
    ctx = runtime.context
    return ctx.conselheiro.avaliar_compra(ctx.boss, item)


@tool
def preco_item(nome: str, runtime: ToolRuntime[Contexto]) -> str:
    """Preço atual de um item no Grand Exchange (média das últimas negociações, atualizada a cada 10 minutos).
    `nome` é o nome oficial em inglês (ex.: Twisted bow, Shark, Prayer potion(4))."""
    return runtime.context.conselheiro.preco(nome)


FERRAMENTAS = [calcular_setup, avaliar_compra, preco_item]
