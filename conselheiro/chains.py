"""As três chains do conselheiro: prompt | model | parser.

1. interpretar: texto livre -> Pedido (PydanticOutputParser)
2. avaliar:     números do setup atual + guia -> Avaliacao (PydanticOutputParser)
3. responder:   guia + cálculos + avaliação + histórico -> texto (StrOutputParser)

Os números nunca vêm do modelo: ele só lê o que o otimizador calculou.
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.output_parsers import PydanticOutputParser, StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from langchain_ollama import ChatOllama
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from conselheiro.config import Settings


class Tolerante(BaseModel):
    @field_validator("*", mode="before")
    @classmethod
    def _nulo_vira_padrao(cls, value: Any, info: ValidationInfo) -> Any:
        # modelos pequenos às vezes mandam null (ou o texto "null") em campos que deveriam vir vazios
        if isinstance(value, str) and value.strip().casefold() in ("", "null", "none", "n/a"):
            value = None
        if value is None:
            return cls.model_fields[info.field_name].get_default(call_default_factory=True)
        return value


class Niveis(Tolerante):
    ataque: int | None = Field(None, description="nível de Attack")
    forca: int | None = Field(None, description="nível de Strength")
    defesa: int | None = Field(None, description="nível de Defence")
    ranged: int | None = Field(None, description="nível de Ranged")
    magia: int | None = Field(None, description="nível de Magic")
    vida: int | None = Field(None, description="nível de Hitpoints")
    prayer: int | None = Field(None, description="nível de Prayer")


class Pedido(Tolerante):
    boss: str | None = Field(None, description="boss citado na mensagem, como o usuário escreveu; null se nenhum")
    orcamento: str | None = Field(
        None, description='dinheiro para gastar exatamente como escrito: "50m", "1.2b", "300k", "50kk"; null se não disse'
    )
    estilo: Literal["melee", "ranged", "magic"] | None = Field(
        None, description="estilo de combate que o usuário quer usar; null se não disse"
    )
    niveis: Niveis = Field(default_factory=Niveis)
    rigour: bool | None = Field(None, description="true se disse que tem Rigour desbloqueada")
    augury: bool | None = Field(None, description="true se disse que tem Augury desbloqueada")
    itens_que_tenho: list[str] = Field(
        default_factory=list, description="itens que o usuário diz ter, com o nome oficial em inglês do jogo"
    )
    itens_excluidos: list[str] = Field(
        default_factory=list, description="itens que o usuário não quer usar ou comprar, nome em inglês"
    )
    na_task: bool | None = Field(None, description="true se disse que está numa slayer task desse boss")
    intencao: Literal["setup", "avaliar", "duvida"] = Field(
        "setup",
        description=(
            "setup: quer equipamento/upgrades/inventário; avaliar: quer saber se o setup que tem serve; "
            "duvida: pergunta sobre mecânica ou estratégia, sem pedir equipamento"
        ),
    )


class Avaliacao(BaseModel):
    veredito: Literal["adequado", "fraco", "arriscado"] = Field(
        description="adequado: dá para fazer bem; fraco: funciona mas lento; arriscado: falta algo essencial"
    )
    pontos_fortes: list[str] = Field(description="até 3 pontos fortes do setup atual")
    pontos_fracos: list[str] = Field(description="até 3 pontos fracos do setup atual")


def criar_llm(settings: Settings, schema: type[BaseModel] | None = None, num_predict: int | None = None) -> ChatOllama:
    return ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        num_ctx=settings.ollama_num_ctx,
        temperature=0.0 if schema else settings.temperature,
        # restringe a saída ao JSON do esquema (structured outputs do Ollama)
        format=schema.model_json_schema() if schema else None,
        # modelos com "thinking" (qwen3.x) ficam bem mais lentos pensando antes de responder
        reasoning=False,
        num_predict=num_predict,
    )


SISTEMA_INTERPRETAR = (
    "Você extrai informações de mensagens de jogadores de Old School RuneScape que pedem ajuda com bosses. "
    "Bosses conhecidos: {bosses}.\n"
    "Regras:\n"
    "- Preencha só o que a mensagem diz; o resto fica null ou lista vazia.\n"
    "- boss: qualquer boss citado na mensagem, mesmo que não esteja na lista de conhecidos.\n"
    "- Itens sempre com o nome oficial em inglês (ex.: dhcb -> Dragon hunter crossbow, bp -> Toxic blowpipe, "
    "salve ei -> Salve amulet(ei)).\n"
    '- Orçamento exatamente como escrito, sem converter (ex.: "50m", "1.2b", "80kk").\n'
    "- A mensagem pode completar o pedido anterior; não repita dados do pedido anterior.\n"
    '- Um número vale para todos os atributos listados: "90 de ataque, força e defesa" -> ataque 90, '
    'forca 90, defesa 90; "sou 70 de tudo" -> todos os níveis 70.\n'
    "Exemplos de intenção:\n"
    '- "e se eu tiver 100m?" -> orcamento "100m", intencao setup\n'
    '- "o que compro primeiro?" / "qual era o primeiro upgrade?" -> intencao setup\n'
    '- "meu setup atual serve?" -> intencao avaliar\n'
    '- "como passo do especial de ácido?" -> intencao duvida\n'
    "Pedido anterior (JSON): {pedido_anterior}\n\n"
    "{format_instructions}"
)

SISTEMA_AVALIAR = (
    "Você avalia o equipamento que um jogador de Old School RuneScape JÁ TEM para enfrentar um boss. "
    "Use apenas o guia e o resumo abaixo; não invente itens nem números.\n"
    "Veredito (siga à risca):\n"
    "- arriscado: o resumo diz que algum setup necessário tem DPS atual 0.\n"
    "- adequado: proporção atual/recomendado de pelo menos 70%.\n"
    "- fraco: qualquer outro caso.\n"
    "Pontos fortes e fracos: fale só dos itens listados como 'tem' e do DPS. "
    "Não comente comida, poções, inventário nem prayers. Português do Brasil, frases curtas.\n\n"
    "Guia do boss:\n{guia}\n\n"
    "{format_instructions}"
)

SISTEMA_RESPONDER = (
    "Você é um conselheiro de Old School RuneScape que responde em português do Brasil. "
    "O jogador já viu a tabela com os números; não a repita inteira.\n"
    "Regras:\n"
    "- Use só o guia e os números abaixo. Se algo não estiver neles, diga que não sabe.\n"
    "- Não invente itens, preços, níveis ou números de DPS; copie valores (orçamento, DPS, gp) "
    "exatamente como estão na tabela e não calcule nem estime números novos.\n"
    "- Se a pergunta for sobre mecânica, responda só a mecânica, sem falar de compras.\n"
    "- O jogador só tem os itens da linha 'Você tem'.\n"
    "- A 'Ordem de compra' já está na ordem certa: o primeiro upgrade é o item 1.\n"
    "- Para explicar a arma, use a linha 'Estilo': ela diz o tipo de ataque e as defesas do boss "
    "(defesa menor = mais fácil de acertar).\n"
    "- Responda à pergunta em até 6 frases curtas e dê só as dicas do guia que importam para ela.\n"
    "- Texto simples, sem markdown: nada de ** ou #; listas com '-' são permitidas.\n\n"
    "Guia do boss:\n{guia}\n\n"
    "Números calculados:\n{calculos}\n\n"
    "Avaliação do setup atual:\n{avaliacao}"
)


def chain_interpretar(settings: Settings, llm: Runnable | None = None) -> Runnable:
    parser = PydanticOutputParser(pydantic_object=Pedido)
    prompt = ChatPromptTemplate(
        [("system", SISTEMA_INTERPRETAR), ("human", "{mensagem}")]
    ).partial(format_instructions=parser.get_format_instructions())
    return prompt | (llm or criar_llm(settings, Pedido)) | parser


def chain_avaliar(settings: Settings, llm: Runnable | None = None) -> Runnable:
    parser = PydanticOutputParser(pydantic_object=Avaliacao)
    prompt = ChatPromptTemplate(
        [("system", SISTEMA_AVALIAR), ("human", "Resumo:\n{resumo}")]
    ).partial(format_instructions=parser.get_format_instructions())
    return prompt | (llm or criar_llm(settings, Avaliacao)) | parser


def chain_responder(settings: Settings, llm: Runnable | None = None) -> Runnable:
    # o placeholder recebe o histórico do boss da vez (janela de 4 interações)
    prompt = ChatPromptTemplate(
        [("system", SISTEMA_RESPONDER), ("placeholder", "{historico}"), ("human", "{mensagem}")]
    )
    # uma resposta normal tem ~200 tokens; o teto só pesa quando o modelo pequeno entra em loop
    return prompt | (llm or criar_llm(settings, num_predict=500)) | StrOutputParser()
