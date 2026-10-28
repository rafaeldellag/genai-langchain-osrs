"""Testes sem Ollama: as chains recebem modelos falsos e o resto roda de verdade."""

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.runnables import RunnableLambda

from conselheiro.chains import Avaliacao, Pedido, chain_avaliar, chain_interpretar, chain_responder
from conselheiro.config import Settings
from conselheiro.conselheiro import Conselheiro, limitar_frases, mesclar, orcamento_na_mensagem, parse_gp
from conselheiro.dados import GameData
from conselheiro.guias import carregar_guias, identificar_boss
from conselheiro.otimizador import AMMO_STACK, Otimizador, Perfil


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
    anterior = Pedido(orcamento="50m", itens_que_tenho=["Toxic blowpipe"], niveis={"ranged": 90})
    novo = Pedido(itens_que_tenho=["Salve amulet(ei)"], niveis={"prayer": 77})
    junto = mesclar(anterior, novo)
    assert junto.orcamento == "50m"
    assert junto.itens_que_tenho == ["Toxic blowpipe", "Salve amulet(ei)"]
    assert (junto.niveis.ranged, junto.niveis.prayer) == (90, 77)


def test_pedido_aceita_null_do_modelo_pequeno():
    pedido = Pedido.model_validate(
        {"orcamento": "null", "niveis": None, "itens_que_tenho": None, "intencao": None, "boss": "None"}
    )
    assert pedido.orcamento is None and pedido.boss is None
    assert pedido.itens_que_tenho == [] and pedido.intencao == "setup"


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
    assert limitar_frases(loop).count("\n") == 7
    assert limitar_frases("Curta e sem ponto") == "Curta e sem ponto"


def test_zulrah_so_aceita_alabarda_no_melee(data):
    perfil = Perfil(orcamento=50_000_000)
    resultado = Otimizador(data, perfil, [data.monster("Zulrah", "Tanzanite")], "melee", 50_000_000).resolver()
    assert resultado.recomendado.equipment["weapon"].category == "Polearm"


def test_chain_interpretar_com_modelo_falso():
    resposta = json.dumps({"boss": "vork", "orcamento": "50m", "itens_que_tenho": ["Toxic blowpipe"]})
    chain = chain_interpretar(Settings(), llm=FakeListChatModel(responses=[resposta]))
    pedido = chain.invoke({"bosses": "Vorkath", "pedido_anterior": "{}", "mensagem": "vork com 50m"})
    assert pedido.orcamento == "50m" and pedido.itens_que_tenho == ["Toxic blowpipe"]


def test_chain_avaliar_e_responder_com_modelo_falso():
    avaliacao = json.dumps({"veredito": "fraco", "pontos_fortes": ["a"], "pontos_fracos": ["b"]})
    chain = chain_avaliar(Settings(), llm=FakeListChatModel(responses=[avaliacao]))
    assert chain.invoke({"guia": "g", "resumo": "r"}).veredito == "fraco"

    chain = chain_responder(Settings(), llm=FakeListChatModel(responses=["ok"]))
    texto = chain.invoke(
        {"guia": "g", "calculos": "c", "avaliacao": "a", "historico": [("human", "oi"), ("ai", "olá")], "mensagem": "m"}
    )
    assert texto == "ok"


def test_historico_nao_vaza_entre_bosses(data, guias):
    pedidos = iter([
        Pedido(orcamento="50m", itens_que_tenho=["Toxic blowpipe"]),
        Pedido(orcamento="30m"),
        Pedido(intencao="duvida"),
    ])
    historicos_vistos = []

    def responder(entrada: dict) -> str:
        historicos_vistos.append(list(entrada["historico"]))
        return f"resposta {len(historicos_vistos)}"

    conselheiro = Conselheiro(
        data=data,
        guias=guias,
        interpretar=RunnableLambda(lambda _: next(pedidos)),
        avaliar=RunnableLambda(lambda _: Avaliacao(veredito="fraco", pontos_fortes=[], pontos_fracos=[])),
        responder=RunnableLambda(responder),
    )
    conselheiro.responder("vorkath com 50m, tenho blowpipe")
    conselheiro.responder("e zulrah com 30m?")
    resposta = conselheiro.responder("voltando ao vorkath, como passo do ácido?")

    assert historicos_vistos[1] == []  # o Zulrah começa sem nada do Vorkath
    assert [m for _, m in historicos_vistos[2]] == ["vorkath com 50m, tenho blowpipe", "resposta 1"]
    assert resposta.boss == "Vorkath" and resposta.tabela is None
    # o orçamento do Zulrah não substitui o do Vorkath
    assert conselheiro.estado.pedido.orcamento == "50m"
