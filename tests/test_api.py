"""A API web com modelo falso: streaming dos eventos, sessões separadas e erro do Ollama vira mensagem clara."""

import json

import httpx
from fastapi.testclient import TestClient
from langchain_core.runnables import RunnableLambda

from conselheiro.agente import criar_agente
from conselheiro.api import criar_app
from conselheiro.chains import Avaliacao
from conselheiro.config import Settings
from conselheiro.conselheiro import Conselheiro
from conselheiro.dados import GameData
from conselheiro.guias import carregar_guias
from tests.test_conselheiro import ModeloFalso, chamada


def cliente(*roteiro) -> TestClient:
    data, guias = GameData.load(), carregar_guias()
    agente = criar_agente(Settings(), ModeloFalso(messages=iter(roteiro)))
    avaliar = RunnableLambda(lambda _: Avaliacao(veredito="fraco", pontos_fortes=["a"], pontos_fracos=["b"]))
    return TestClient(criar_app(lambda: Conselheiro(Settings(), data, guias, agente, avaliar)))


def enviar(api: TestClient, sessao: str, mensagem: str) -> list[dict]:
    resposta = api.post("/api/mensagem", json={"sessao": sessao, "mensagem": mensagem})
    assert resposta.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(linha) for linha in resposta.text.splitlines()]


def test_pagina_info_e_eventos_em_streaming():
    api = cliente(chamada("calcular_setup", orcamento="50m", itens_que_tenho=["Toxic blowpipe"]), "resposta")
    assert "Conselheiro de bosses" in api.get("/").text
    assert "Vorkath" in api.get("/api/info").json()["bosses"]

    eventos = enviar(api, "a", "vorkath com 50m, tenho bp")
    assert [e["tipo"] for e in eventos if e["tipo"] != "token"] == ["ferramenta", "calculo", "fim"]
    assert eventos[0]["nome"] == "calcular_setup"
    fim = eventos[-1]
    assert fim["boss"] == "Vorkath" and "Vorkath" in fim["tabela"] and fim["avaliacao"]["veredito"] == "fraco"
    assert api.delete("/api/sessoes/a").status_code == 204


class ModeloForaDoAr(ModeloFalso):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise httpx.ConnectError("Connection refused")


def test_ollama_fora_do_ar_vira_evento_de_erro():
    data, guias = GameData.load(), carregar_guias()
    agente = criar_agente(Settings(), ModeloForaDoAr(messages=iter([])))
    api = TestClient(criar_app(lambda: Conselheiro(Settings(), data, guias, agente, None)))
    eventos = enviar(api, "a", "vorkath com 50m")
    assert eventos[-1]["tipo"] == "erro" and "Ollama" in eventos[-1]["texto"]
