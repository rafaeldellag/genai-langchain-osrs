"""API e página web do conselheiro (FastAPI). Cada aba do navegador tem a própria conversa.

/api/mensagem devolve NDJSON em streaming: as ferramentas que o agente chama, o cálculo, os pedaços do texto e
a resposta final, na ordem em que acontecem.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from conselheiro.conselheiro import Conselheiro, explicar_erro

PAGINA = Path(__file__).resolve().parent / "static" / "index.html"
# conversas guardadas na memória; a mais antiga sai quando passa disso
MAX_SESSOES = 50
log = logging.getLogger(__name__)


class Mensagem(BaseModel):
    sessao: str = Field(min_length=1, max_length=64)
    mensagem: str = Field(min_length=1, max_length=2000)


def criar_app(novo_conselheiro: Callable[[], Conselheiro]) -> FastAPI:
    """`novo_conselheiro` cria a conversa de uma sessão; agente, dados e guias podem ser compartilhados."""
    app = FastAPI(title="Conselheiro de bosses de OSRS")
    base = novo_conselheiro()
    sessoes: OrderedDict[str, Conselheiro] = OrderedDict()
    trava = threading.Lock()

    def conversa(sessao: str) -> Conselheiro:
        with trava:
            conselheiro = sessoes.pop(sessao, None) or novo_conselheiro()
            sessoes[sessao] = conselheiro
            while len(sessoes) > MAX_SESSOES:
                sessoes.popitem(last=False)[1].encerrar()
            return conselheiro

    @app.get("/", include_in_schema=False)
    def pagina() -> FileResponse:
        return FileResponse(PAGINA)

    @app.get("/api/info")
    def info() -> dict:
        return {"modelo": base.settings.ollama_model, "bosses": list(base.guias)}

    @app.post("/api/mensagem")
    def mensagem(pedido: Mensagem) -> StreamingResponse:
        conselheiro = conversa(pedido.sessao)

        def gerar() -> Iterator[str]:
            try:
                for evento in conselheiro.eventos(pedido.mensagem.strip()):
                    yield json.dumps(evento, ensure_ascii=False) + "\n"
            except Exception as erro:
                # o streaming já começou: o erro vai como evento, não como status HTTP
                explicacao = explicar_erro(erro, conselheiro.settings)
                if explicacao is None:
                    log.exception("erro no agente")
                    explicacao = f"Erro interno ({type(erro).__name__}). Veja o log do servidor."
                yield json.dumps({"tipo": "erro", "texto": explicacao}, ensure_ascii=False) + "\n"

        return StreamingResponse(gerar(), media_type="application/x-ndjson")

    @app.delete("/api/sessoes/{sessao}", status_code=204)
    def nova_conversa(sessao: str) -> None:
        with trava:
            conselheiro = sessoes.pop(sessao, None)
        if conselheiro:
            conselheiro.encerrar()

    return app
