"""API e página web do conselheiro (FastAPI). Cada aba do navegador tem a própria conversa."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from conselheiro.chains import Avaliacao
from conselheiro.conselheiro import Conselheiro, explicar_erro

PAGINA = Path(__file__).resolve().parent / "static" / "index.html"
# conversas guardadas na memória; a mais antiga sai quando passa disso
MAX_SESSOES = 50


class Mensagem(BaseModel):
    sessao: str = Field(min_length=1, max_length=64)
    mensagem: str = Field(min_length=1, max_length=2000)


class RespostaApi(BaseModel):
    boss: str | None
    intencao: str
    tabela: str | None
    avaliacao: Avaliacao | None
    texto: str


def criar_app(novo_conselheiro: Callable[[], Conselheiro]) -> FastAPI:
    """`novo_conselheiro` cria a conversa de uma sessão; dados e chains podem ser compartilhados entre elas."""
    app = FastAPI(title="Conselheiro de bosses de OSRS")
    base = novo_conselheiro()
    sessoes: OrderedDict[str, Conselheiro] = OrderedDict()
    trava = threading.Lock()

    def conversa(sessao: str) -> Conselheiro:
        with trava:
            conselheiro = sessoes.pop(sessao, None) or novo_conselheiro()
            sessoes[sessao] = conselheiro
            while len(sessoes) > MAX_SESSOES:
                sessoes.popitem(last=False)
            return conselheiro

    @app.get("/", include_in_schema=False)
    def pagina() -> FileResponse:
        return FileResponse(PAGINA)

    @app.get("/api/info")
    def info() -> dict:
        return {"modelo": base.settings.ollama_model, "bosses": list(base.guias)}

    @app.post("/api/mensagem")
    def mensagem(pedido: Mensagem) -> RespostaApi:
        conselheiro = conversa(pedido.sessao)
        try:
            r = conselheiro.responder(pedido.mensagem.strip())
        except Exception as erro:
            if (explicacao := explicar_erro(erro, conselheiro.settings)) is None:
                raise
            raise HTTPException(503, explicacao) from erro
        return RespostaApi(boss=r.boss, intencao=r.intencao, tabela=r.tabela, avaliacao=r.avaliacao, texto=r.texto)

    @app.delete("/api/sessoes/{sessao}", status_code=204)
    def nova_conversa(sessao: str) -> None:
        with trava:
            sessoes.pop(sessao, None)

    return app
