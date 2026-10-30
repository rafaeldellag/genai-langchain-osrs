"""Testes sem Ollama: o agente e a chain recebem modelos falsos e o resto roda de verdade."""

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel, GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from pydantic import Field

from conselheiro.agente import criar_agente
from conselheiro.chains import Avaliacao, Pedido, chain_avaliar
from conselheiro.conferencia import conferir_pedido, itens_sem_fonte
from conselheiro.config import Settings
from conselheiro.conselheiro import Conselheiro, mesclar
from conselheiro.dados import GameData
from conselheiro.guias import carregar_guias, identificar_boss
from conselheiro.otimizador import AMMO_STACK, Otimizador, Perfil
from conselheiro.texto import (
    MAX_FRASES,
    limitar_frases,
    limpar_resposta,
    orcamento_na_mensagem,
    parse_gp,
    tirar_frases_com,
)


class ModeloFalso(GenericFakeChatModel):
    """Segue um roteiro de respostas (texto ou chamada de ferramenta) e guarda o que recebeu em cada chamada."""

    recebidas: list = Field(default_factory=list)
    # sem streaming o fake devolve as tool_calls inteiras
    disable_streaming: bool = True

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.recebidas.append(list(messages))
        return super()._generate(messages, stop, run_manager, **kwargs)


def chamada(nome: str, **args) -> AIMessage:
    return AIMessage("", tool_calls=[{"name": nome, "args": args, "id": f"{nome}-{len(args)}"}])


def conselheiro_falso(data, guias, *roteiro) -> tuple[Conselheiro, ModeloFalso]:
    modelo = ModeloFalso(messages=iter(roteiro))
    avaliar = RunnableLambda(lambda _: Avaliacao(veredito="fraco", pontos_fortes=["a"], pontos_fracos=["b"]))
    return Conselheiro(Settings(), data, guias, criar_agente(Settings(), modelo), avaliar), modelo


@pytest.fixture(scope="module")
def data() -> GameData:
    return GameData.load()


@pytest.fixture(scope="module")
def guias():
    return carregar_guias()


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("50m", 50_000_000), ("1.2b", 1_200_000_000), ("300k", 300_000), ("80kk", 80_000_000),
        ("2kkk", 2_000_000_000), ("50 milhões", 50_000_000), ("1,5m", 1_500_000), ("50", 50_000_000),
        ("2500000", 2_500_000), ("muito", None), (None, None),
    ],
)
def test_parse_gp(texto, esperado):
    assert parse_gp(texto) == esperado


@pytest.mark.parametrize(
    ("mensagem", "esperado"),
    [
        ("tenho 50m e 90 range", "50m"),
        ("e se eu tiver 120kk?", "120kk"),
        ("90 range e 77 prayer", None),
        ("uso 50 magic", None),
        ("orçamento de 1.2b", "1.2b"),
    ],
)
def test_orcamento_na_mensagem(mensagem, esperado):
    assert orcamento_na_mensagem(mensagem) == esperado


def test_mesclar_mantem_o_que_a_mensagem_nao_disse():
    anterior = Pedido(orcamento="50m", itens_que_tenho=["Toxic blowpipe"], ranged=90)
    novo = Pedido(itens_que_tenho=["Salve amulet(ei)"], prayer=77)
    junto = mesclar(anterior, novo)
    assert junto.orcamento == "50m"
    assert junto.itens_que_tenho == ["Toxic blowpipe", "Salve amulet(ei)"]
    assert (junto.ranged, junto.prayer) == (90, 77)


def test_pedido_aceita_null_e_texto_do_modelo_pequeno():
    pedido = Pedido.model_validate({"orcamento": "null", "itens_que_tenho": None, "ranged": "90", "rigour": "True"})
    assert pedido.orcamento is None and pedido.itens_que_tenho == []
    assert pedido.ranged == 90 and pedido.rigour is True


@pytest.mark.parametrize(
    ("texto", "boss"),
    [("quero matar vork", "Vorkath"), ("bandos solo", "General Graardor"), ("cerb na task", "Cerberus"),
     ("30m p/ zulrah", "Zulrah"), ("giant mole", "Giant Mole"), ("kree'arra", None)],
)
def test_identificar_boss(texto, boss, guias):
    assert identificar_boss(texto, guias) == boss


def test_guias_batem_com_os_dados(data, guias):
    for guia in guias.values():
        for setup in guia.setups:
            for nome, versao in setup.formas:
                data.monster(nome, versao)
        for item in guia.inventario:
            assert data.ge_price(item.item) is not None, f"{guia.boss}: {item.item} sem preço no GE"
        for nome in guia.intransferiveis:
            assert nome in data.usable, f"{guia.boss}: {nome} não é equipamento"


def test_otimizador_respeita_orcamento_e_itens(data):
    blowpipe, salve = data.find_item("bp"), data.find_item("salve ei")
    perfil = Perfil(orcamento=30_000_000, itens=[blowpipe, salve], rigour=True)
    resultado = Otimizador(data, perfil, [data.monster("Vorkath", "Post-quest")], "ranged", 30_000_000).resolver()
    assert resultado.atual.equipment["weapon"].name == "Toxic blowpipe"
    assert resultado.atual.custo == 0
    assert resultado.recomendado.custo <= 30_000_000
    assert resultado.recomendado.dps >= resultado.atual.dps
    # aplicar todos os upgrades leva exatamente ao recomendado
    ganho = sum(u.ganho_dps for u in resultado.upgrades)
    assert resultado.atual.dps + ganho == pytest.approx(resultado.recomendado.dps)


def test_arma_de_arremesso_custa_uma_pilha(data):
    otimizador = Otimizador(data, Perfil(), [data.monster("Giant Mole")], "ranged", 0)
    faca, blowpipe = data.find_item("Dragon knife"), data.find_item("Toxic blowpipe")
    assert otimizador.custo(faca) == data.price(faca) * AMMO_STACK
    # o blowpipe também é "Thrown", mas não se gasta
    assert otimizador.custo(blowpipe) == data.price(blowpipe)


def test_itens_deadman_usam_o_nome_do_ge(data):
    assert "Morrigan's javelin" in data.usable
    assert not [n for n in data.usable if n.endswith("(Deadman Mode)") and data.price(data.usable[n])]


def test_sem_arma_no_estilo_a_arma_vem_primeiro(data):
    perfil = Perfil(orcamento=30_000_000, itens=[data.find_item("bp")])
    resultado = Otimizador(data, perfil, [data.monster("General Graardor")], "melee", 30_000_000).resolver()
    assert "weapon" not in resultado.atual.equipment
    assert resultado.upgrades[0].slot == "weapon"


def test_limitar_frases_corta_loop_e_mantem_linhas():
    assert limitar_frases("Uma. Duas.\nTrês com 9.38 DPS.", maximo=2) == "Uma. Duas."
    loop = "Compre a arma.\n" + "O Shark é mais barato.\n" * 30
    assert limitar_frases(loop).count("\n") == MAX_FRASES - 1
    assert limitar_frases("Curta e sem ponto") == "Curta e sem ponto"
    # o "1." de uma lista não conta como frase
    assert limitar_frases("Ordem:\n1. Fang (13m)\n2. Amulet", maximo=3) == "Ordem:\n1. Fang (13m)\n2. Amulet"


def test_zulrah_so_aceita_alabarda_no_melee(data):
    perfil = Perfil(orcamento=50_000_000)
    resultado = Otimizador(data, perfil, [data.monster("Zulrah", "Tanzanite")], "melee", 50_000_000).resolver()
    assert resultado.recomendado.equipment["weapon"].category == "Polearm"


def test_chain_avaliar_com_modelo_falso():
    avaliacao = json.dumps({"veredito": "fraco", "pontos_fortes": ["a"], "pontos_fracos": ["b"]})
    chain = chain_avaliar(Settings(), llm=FakeListChatModel(responses=[avaliacao]))
    assert chain.invoke({"guia": "g", "resumo": "r"}).veredito == "fraco"


def test_agente_chama_a_ferramenta_e_devolve_o_calculo(data, guias):
    conselheiro, modelo = conselheiro_falso(
        data, guias, chamada("calcular_setup", orcamento="50m", itens_que_tenho=["Toxic blowpipe"]), "**Compre** a dhcb."
    )
    resposta = conselheiro.responder("vorkath com 50m, tenho bp")
    assert resposta.boss == "Vorkath" and resposta.ferramentas == ["calcular_setup"]
    assert "Vorkath" in resposta.tabela and resposta.avaliacao.veredito == "fraco"
    assert resposta.texto == "Compre a dhcb."  # o middleware tira o markdown
    # o resultado da ferramenta voltou para o modelo antes da resposta final
    assert "Ordem de compra" in modelo.recebidas[1][-1].content


def test_guia_e_historico_separados_por_boss(data, guias):
    conselheiro, modelo = conselheiro_falso(
        data,
        guias,
        chamada("calcular_setup", orcamento="50m", itens_que_tenho=["Toxic blowpipe"]),
        "resposta vorkath",
        chamada("calcular_setup", orcamento="30m"),
        "resposta zulrah",
        "resposta ácido",
    )
    conselheiro.responder("vorkath com 50m, tenho blowpipe")
    conselheiro.responder("e zulrah com 30m?")
    conselheiro.responder("voltando ao vorkath, como passo do ácido?")

    sistema_zulrah, *conversa_zulrah = modelo.recebidas[2]
    assert isinstance(sistema_zulrah, SystemMessage)
    assert guias["Zulrah"].texto in sistema_zulrah.content and guias["Vorkath"].texto not in sistema_zulrah.content
    perguntas_zulrah = [m.content for m in conversa_zulrah if isinstance(m, HumanMessage)]
    assert perguntas_zulrah == ["e zulrah com 30m gp?"]  # nada do Vorkath, e "30m" normalizado

    perguntas_vorkath = [m.content for m in modelo.recebidas[4] if isinstance(m, HumanMessage)]
    assert perguntas_vorkath == ["vorkath com 50m gp, tenho blowpipe", "voltando ao vorkath, como passo do ácido?"]
    # o orçamento do Zulrah não substitui o do Vorkath
    assert conselheiro.estado.pedido.orcamento == "50m"


def test_exige_ferramenta_quando_o_modelo_inventa_numeros(data, guias):
    conselheiro, modelo = conselheiro_falso(
        data, guias, "Com 50m você chega a 9.4 DPS.", chamada("calcular_setup", orcamento="50m"), "ok"
    )
    resposta = conselheiro.responder("vorkath com 50m")
    assert resposta.ferramentas == ["calcular_setup"] and resposta.texto == "ok"
    assert "Não invente números" in modelo.recebidas[1][-1].content


def test_conferir_pedido_ignora_o_que_a_mensagem_nao_cita():
    args = {"orcamento": "120k", "estilo": "melee", "na_task": "True", "rigour": "true", "magia": "90",
            "ataque": 90, "prayer": 90}
    assert conferir_pedido(args, "zulrah com 120kk, 90 magic e rigour") == {
        "orcamento": "120kk", "magia": 90, "rigour": True  # o regex vale mais que o modelo
    }
    assert conferir_pedido({"ataque": 70, "prayer": 70}, "giant mole, 70 em tudo") == {"ataque": 70, "prayer": 70}
    # o número da mensagem vale mais que o do modelo, e nível esquecido entra
    mensagem = "vorkath, tenho 30m, 85 range, 80 defesa, 77 prayer e rigour"
    assert conferir_pedido({"orcamento": "30m", "ranged": 85, "defesa": 77, "rigour": True}, mensagem) == {
        "orcamento": "30m", "defesa": 80, "ranged": 85, "prayer": 77, "rigour": True
    }
    assert conferir_pedido({"forca": 90}, "atk 92 e str: 90") == {"ataque": 92, "forca": 90}


def test_argumentos_conferidos_antes_da_ferramenta_e_da_tela(data, guias):
    conselheiro, _ = conselheiro_falso(
        data, guias, chamada("calcular_setup", orcamento="30m", defesa=77, ataque=85, na_task=True), "ok"
    )
    eventos = list(conselheiro.eventos("vorkath com 30m e 80 defesa"))
    assert eventos[0]["tipo"] == "ferramenta" and eventos[0]["args"] == {"orcamento": "30m", "defesa": 80}
    p = conselheiro.estado.pedido
    assert p.defesa == 80 and p.ataque is None and p.na_task is None


def test_itens_sem_fonte(data):
    equipamentos = data.equipamentos_citaveis
    fonte = "weapon  Dragon crossbow\nammo    Ruby dragon bolts (e)"
    texto = "Compre a Dragon hunter crossbow com Ruby dragon bolts (e). A DHCB é ótima. Salve, boa sorte!"
    assert itens_sem_fonte(texto, fonte, equipamentos) == ["Dragon hunter crossbow", "DHCB"]
    assert itens_sem_fonte("Use o Dragon crossbow.", fonte, equipamentos) == []


def test_item_sem_fonte_pede_para_reescrever_e_depois_tira_a_frase(data, guias):
    conselheiro, modelo = conselheiro_falso(data, guias, "Use a Twisted bow.", "Use a Twisted bow. Boa sorte.")
    resposta = conselheiro.responder("como passo do ácido do vorkath?")
    assert "Twisted bow" in modelo.recebidas[1][-1].content  # o lembrete cita o item
    assert resposta.texto == "Boa sorte."


def test_resposta_cortada_fica_ate_a_ultima_frase():
    texto = "Magia: Sanguinesti staff.\n- Ranged: Dragon crossbow.\n\nItens essenciais:"
    assert limpar_resposta(texto) == "Magia: Sanguinesti staff.\n- Ranged: Dragon crossbow."
    assert limpar_resposta("Compre a arma. Depois o", cortada=True) == "Compre a arma."
    assert tirar_frases_com("Uma. Use a DHCB. Três.", ["dhcb"]) == "Uma. Três."


def test_limpar_resposta_tira_markdown():
    texto = "## Dica\nCompre as *Mixed hide* e o **Regen bracelet**:\n*   Magic: 0 DPS\n- Ranged: 3.25 DPS"
    assert limpar_resposta(texto) == "Dica\nCompre as Mixed hide e o Regen bracelet:\n- Magic: 0 DPS\n- Ranged: 3.25 DPS"


def test_veredito_segue_a_regra_mesmo_se_o_modelo_errar(data, guias):
    conselheiro, _ = conselheiro_falso(data, guias)  # a avaliação falsa diz sempre "fraco"
    conselheiro.estado.pedido = Pedido(orcamento="120m", itens_que_tenho=["Toxic blowpipe"])
    _, artefato = conselheiro.calcular_para_agente("Zulrah", Pedido())
    # sem arma de magia, o setup de magia tem DPS 0
    assert artefato["avaliacao"]["veredito"] == "arriscado"


@pytest.mark.parametrize(
    ("mensagem", "esperado"),
    [
        ("e kree'arra?", "Kree'arra"),
        ("quero fazer kraken", "Kraken"),
        ("tenho blood moon chestplate e kraken tentacle", None),
        ("vale a pena o venator bow?", None),
    ],
)
def test_boss_sem_guia(data, guias, mensagem, esperado):
    conselheiro, _ = conselheiro_falso(data, guias)
    assert conselheiro._boss_sem_guia(mensagem) == esperado


def test_boss_sem_guia_responde_sem_chamar_o_modelo(data, guias):
    conselheiro, modelo = conselheiro_falso(data, guias)
    resposta = conselheiro.responder("e kree'arra?")
    assert resposta.texto.startswith("Ainda não tenho guia para Kree'arra") and modelo.recebidas == []


def test_avaliar_compra_e_preco(data, guias):
    conselheiro, _ = conselheiro_falso(data, guias)
    conselheiro.estado.pedido = Pedido(itens_que_tenho=["Toxic blowpipe"])
    texto = conselheiro.avaliar_compra("Vorkath", "Masori mask")
    assert texto.startswith("Masori mask: custa") and "DPS" in texto
    assert "já tem" in conselheiro.avaliar_compra("Vorkath", "bp")
    assert conselheiro.preco("Twisted bow").startswith("Twisted bow: ")
    assert conselheiro.preco("shark").startswith("Shark: ")
