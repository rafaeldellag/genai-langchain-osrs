"""Modelos de dados, o LLM e a chain de avaliação (prompt | model | parser).

O Pedido é o esquema dos argumentos da ferramenta calcular_setup: o agente preenche os campos a partir da
mensagem do jogador. A chain de avaliação roda dentro dessa ferramenta. Os números nunca vêm do modelo.
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.output_parsers import PydanticOutputParser
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


class Pedido(Tolerante):
    """Perfil do jogador. Plano de propósito: com níveis aninhados o modelo de 4B esquecia de preenchê-los."""

    orcamento: str | None = Field(
        None, description='dinheiro para gastar exatamente como o jogador escreveu: "50m", "1.2b", "300k", "120kk"'
    )
    estilo: Literal["melee", "ranged", "magic"] | None = Field(
        None, description="só se o jogador escolheu o estilo de combate"
    )
    ataque: int | None = Field(None, description="nível de Attack")
    forca: int | None = Field(None, description="nível de Strength")
    defesa: int | None = Field(None, description="nível de Defence")
    ranged: int | None = Field(None, description="nível de Ranged")
    magia: int | None = Field(None, description="nível de Magic")
    vida: int | None = Field(None, description="nível de Hitpoints")
    prayer: int | None = Field(None, description="nível de Prayer")
    rigour: bool | None = Field(None, description="true se tem Rigour desbloqueada")
    augury: bool | None = Field(None, description="true se tem Augury desbloqueada")
    itens_que_tenho: list[str] = Field(
        default_factory=list,
        description="itens que o jogador diz ter, com o nome oficial em inglês: bp -> Toxic blowpipe, "
        "dhcb -> Dragon hunter crossbow, salve ei -> Salve amulet(ei)",
    )
    itens_excluidos: list[str] = Field(
        default_factory=list, description="itens que o jogador não quer usar nem comprar, nome em inglês"
    )
    na_task: bool | None = Field(None, description="true se está numa slayer task desse boss")


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


def chain_avaliar(settings: Settings, llm: Runnable | None = None) -> Runnable:
    parser = PydanticOutputParser(pydantic_object=Avaliacao)
    prompt = ChatPromptTemplate(
        [("system", SISTEMA_AVALIAR), ("human", "Resumo:\n{resumo}")]
    ).partial(format_instructions=parser.get_format_instructions())
    return prompt | (llm or criar_llm(settings, Avaliacao)) | parser
