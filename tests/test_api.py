"""A API web com modelos falsos: sessões separadas e erro do Ollama vira mensagem clara."""

import httpx
from fastapi.testclient import TestClient
from langchain_core.runnables import RunnableLambda

from conselheiro.api import criar_app
from conselheiro.chains import Avaliacao, Pedido
from conselheiro.conselheiro import Conselheiro
from conselheiro.dados import GameData
from conselheiro.guias import carregar_guias


def cliente(responder) -> TestClient:
    data, guias = GameData.load(), carregar_guias()
    interpretar = RunnableLambda(lambda entrada: Pedido(boss="Vorkath", orcamento="50m", itens_que_tenho=["Toxic blowpipe"]))
    avaliar = RunnableLambda(lambda _: Avaliacao(veredito="fraco", pontos_fortes=["a"], pontos_fracos=["b"]))
    app = criar_app(lambda: Conselheiro(None, data, guias, interpretar, avaliar, RunnableLambda(responder)))
    return TestClient(app)


def test_pagina_info_e_mensagem():
    api = cliente(lambda entrada: f"vi {len(entrada['historico'])} mensagens antes")
    assert "Conselheiro de bosses" in api.get("/").text
    assert "Vorkath" in api.get("/api/info").json()["bosses"]

    r = api.post("/api/mensagem", json={"sessao": "a", "mensagem": "vorkath com 50m"}).json()
    assert r["boss"] == "Vorkath" and "Vorkath" in r["tabela"] and r["avaliacao"]["veredito"] == "fraco"
    assert api.post("/api/mensagem", json={"sessao": "a", "mensagem": "e agora?"}).json()["texto"] == "vi 2 mensagens antes"
    # outra aba começa do zero, e "nova conversa" zera a sessão
    assert api.post("/api/mensagem", json={"sessao": "b", "mensagem": "oi vorkath"}).json()["texto"] == "vi 0 mensagens antes"
    assert api.delete("/api/sessoes/a").status_code == 204
    assert api.post("/api/mensagem", json={"sessao": "a", "mensagem": "vorkath"}).json()["texto"] == "vi 0 mensagens antes"


def test_ollama_fora_do_ar_vira_503():
    def responder(_):
        raise httpx.ConnectError("Connection refused")

    r = cliente(responder).post("/api/mensagem", json={"sessao": "a", "mensagem": "vorkath com 50m"})
    assert r.status_code == 503 and "Ollama" in r.json()["detail"]
