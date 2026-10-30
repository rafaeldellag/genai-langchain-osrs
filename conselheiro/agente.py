"""O agente: `create_agent` com as ferramentas, memória por conversa no checkpointer do LangGraph e middlewares.

Cada middleware resolve um problema que apareceu testando com o modelo local de 4B:
- prompt_do_boss: o guia do boss da conversa entra no system prompt, e só ele (nada de misturar bosses).
- ContextEditingMiddleware: cada tabela tem ~900 tokens; as antigas saem do contexto e fica só a última.
- janela_de_historico: conversa longa não estoura o num_ctx do Ollama.
- corrigir_argumentos: níveis, task e estilo que o jogador não disse saem da chamada de calcular_setup, e o
  nível vale o número da mensagem ("80 defesa" tinha virado 77).
- exigir_ferramenta: o modelo dizia "calculei o setup" com números inventados sem chamar a ferramenta.
- conferir_itens: a resposta citava item que não estava no cálculo (Dragon hunter crossbow no lugar do Dragon
  crossbow); pede para reescrever e, se insistir, tira a frase.
- limpar_texto: markdown, respostas em loop (a mesma frase repetida 30 vezes) e resposta cortada no meio.
- ModelCallLimitMiddleware: o modelo pequeno não fica chamando ferramenta para sempre.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ClearToolUsesEdit,
    ContextEditingMiddleware,
    ModelCallLimitMiddleware,
    ModelRequest,
    ModelResponse,
    after_model,
    dynamic_prompt,
    wrap_model_call,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import trim_messages
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from conselheiro.chains import criar_llm
from conselheiro.conferencia import conferir_pedido, itens_sem_fonte
from conselheiro.config import Settings
from conselheiro.ferramentas import FERRAMENTAS, Contexto
from conselheiro.texto import limpar_resposta, orcamento_na_mensagem, tirar_frases_com

# o num_ctx é 8192: ~2k do system prompt com o guia, ~600 das ferramentas, o resto para a conversa
MAX_TOKENS_HISTORICO = 4500
LIMPAR_TABELAS_ACIMA_DE = 3000
# "9.38 DPS", "50m", "1.8k gp"...: número de jogo numa resposta que não chamou ferramenta é invenção
NUMERO_DE_JOGO_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:dps|gp|kk|k|m|b)\b", re.IGNORECASE)
# linha de arma na tabela de calcular_setup: "    weapon  Dragon crossbow"
ARMA_NO_CALCULO_RE = re.compile(r"^ +weapon +(.+?)(?: \(já tem\))?$", re.MULTILINE)

SISTEMA_AGENTE = (
    "Você é um conselheiro de Old School RuneScape e responde em português do Brasil.\n"
    "Boss desta conversa: {boss}. Bosses com guia: {bosses}.\n"
    "Ferramentas:\n"
    "- calcular_setup: sempre que o jogador falar de orçamento, níveis, itens que tem, o que comprar, upgrades "
    "ou se o setup dele serve. Passe só o que a mensagem nova disse.\n"
    "- avaliar_compra: quando perguntar se vale a pena comprar um item específico.\n"
    "- preco_item: quando perguntar o preço de um item.\n"
    "Regras:\n"
    "- Nunca invente números (DPS, preços, níveis): use só o que as ferramentas devolveram nesta conversa.\n"
    "- Pergunta de mecânica ou estratégia: responda com o guia abaixo, sem ferramenta e sem falar de compras.\n"
    "- Se perguntarem de um boss fora da lista, diga que ainda não tem guia para ele e cite os que tem.\n"
    "- O jogador só tem os itens da linha 'Você tem' do cálculo. A 'Ordem de compra' já está na ordem certa: "
    "o primeiro upgrade é o item 1.\n"
    "- Para explicar a arma, use a linha 'Estilo' (tipo de ataque e defesas do boss; menor é mais fácil).\n"
    "- Não invente para que serve um item: fale do ganho de DPS e do preço que vieram da ferramenta.\n"
    "- O jogador já vê a tabela do cálculo; não a repita. Até 6 frases curtas, texto simples, sem markdown.\n\n"
    "Guia do {boss}:\n{guia}"
)


@dynamic_prompt
def prompt_do_boss(request: ModelRequest) -> str:
    ctx: Contexto = request.runtime.context
    guias = ctx.conselheiro.guias
    guia = guias[ctx.boss].texto if ctx.boss else "(nenhum boss escolhido: pergunte qual o jogador quer fazer)"
    return SISTEMA_AGENTE.format(boss=ctx.boss or "nenhum", bosses=", ".join(guias), guia=guia)


@wrap_model_call
def janela_de_historico(request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
    mensagens = trim_messages(
        request.messages,
        max_tokens=MAX_TOKENS_HISTORICO,
        token_counter="approximate",
        strategy="last",
        start_on="human",
    )
    return handler(request.override(messages=mensagens))


def _turno_atual(mensagens: list) -> list:
    """Mensagens depois da última pergunta do jogador."""
    for i in range(len(mensagens) - 1, -1, -1):
        if isinstance(mensagens[i], HumanMessage):
            return mensagens[i:]
    return mensagens


@wrap_model_call
def corrigir_argumentos(request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
    """Confere a chamada de calcular_setup contra a mensagem antes de a ferramenta rodar (e antes de a tela mostrar)."""
    resposta = handler(request)
    ai = resposta.result[-1]
    if not isinstance(ai, AIMessage) or not ai.tool_calls:
        return resposta
    mensagem = request.runtime.context.mensagem
    chamadas = [
        {**c, "args": conferir_pedido(c["args"], mensagem)} if c["name"] == "calcular_setup" else c
        for c in ai.tool_calls
    ]
    return ModelResponse(result=[*resposta.result[:-1], ai.model_copy(update={"tool_calls": chamadas})])


@wrap_model_call
def exigir_ferramenta(request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
    """Se o modelo responde com números sem ter usado nenhuma ferramenta neste turno, pede de novo."""
    resposta = handler(request)
    ai = resposta.result[-1]
    turno = _turno_atual(request.messages)
    if not isinstance(ai, AIMessage) or ai.tool_calls or any(isinstance(m, ToolMessage) for m in turno):
        return resposta
    pergunta = turno[0].text if turno and isinstance(turno[0], HumanMessage) else ""
    if not (NUMERO_DE_JOGO_RE.search(ai.text) or orcamento_na_mensagem(pergunta)):
        return resposta
    lembrete = HumanMessage(
        "Antes de responder, chame a ferramenta adequada (calcular_setup, avaliar_compra ou preco_item). "
        "Não invente números."
    )
    return handler(request.override(messages=[*request.messages, lembrete]))


def _fonte(request: ModelRequest) -> str:
    """Tudo que o modelo leu: guia, mensagens do jogador, resultados das ferramentas e respostas anteriores."""
    sistema = request.system_message.text if request.system_message else ""
    return "\n".join([sistema, *(m.text for m in request.messages)])


@wrap_model_call
def conferir_itens(request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
    """Item citado na resposta tem que ter vindo de algum lugar; senão, uma chance de reescrever."""
    resposta = handler(request)
    ai = resposta.result[-1]
    if not isinstance(ai, AIMessage) or ai.tool_calls:
        return resposta
    equipamentos = request.runtime.context.conselheiro.data.equipamentos_citaveis
    fonte = _fonte(request)
    if not (inventados := itens_sem_fonte(ai.text, fonte, equipamentos)):
        return resposta
    # a confusão mais comum é a arma ("Dragon crossbow" virava DHCB): o lembrete diz quais o cálculo escolheu
    armas = list(dict.fromkeys(ARMA_NO_CALCULO_RE.findall(fonte)))
    lembrete = HumanMessage(
        f"Estes itens não estão no cálculo, no guia nem na conversa: {', '.join(inventados)}. "
        + (f"As armas do cálculo são: {', '.join(armas)}. " if armas else "")
        + "Reescreva a resposta citando só itens que vieram das ferramentas ou do guia, com o nome exato."
    )
    resposta = handler(request.override(messages=[*request.messages, lembrete]))
    ai = resposta.result[-1]
    if not isinstance(ai, AIMessage) or ai.tool_calls:
        return resposta
    if inventados := itens_sem_fonte(ai.text, fonte, equipamentos):
        ai = ai.model_copy(update={"content": tirar_frases_com(ai.text, inventados)})
    return ModelResponse(result=[*resposta.result[:-1], ai])


@after_model
def limpar_texto(state: dict, runtime) -> dict | None:
    ultima = state["messages"][-1]
    if not isinstance(ultima, AIMessage) or ultima.tool_calls:
        return None
    texto = limpar_resposta(ultima.text, cortada=ultima.response_metadata.get("done_reason") == "length")
    if texto == ultima.text:
        return None
    # mesmo id: o reducer do LangGraph substitui a mensagem em vez de adicionar outra
    return {"messages": [ultima.model_copy(update={"content": texto})]}


def criar_agente(
    settings: Settings, modelo: BaseChatModel | None = None, checkpointer: BaseCheckpointSaver | None = None
):
    return create_agent(
        modelo or criar_llm(settings, num_predict=500),
        tools=FERRAMENTAS,
        middleware=[
            prompt_do_boss,
            janela_de_historico,
            ContextEditingMiddleware(
                edits=[
                    ClearToolUsesEdit(
                        trigger=LIMPAR_TABELAS_ACIMA_DE,
                        keep=1,
                        placeholder="[cálculo antigo removido; chame calcular_setup de novo se precisar]",
                    )
                ]
            ),
            corrigir_argumentos,
            exigir_ferramenta,
            conferir_itens,
            limpar_texto,
            ModelCallLimitMiddleware(run_limit=5, exit_behavior="end"),
        ],
        context_schema=Contexto,
        checkpointer=checkpointer or InMemorySaver(),
    )
