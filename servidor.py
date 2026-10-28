"""Conselheiro de bosses de OSRS no navegador.

Rode `python servidor.py` e abra http://localhost:8080. HOST e PORT mudam o endereço.
"""

import os
import sys

import httpx
import uvicorn

from conselheiro.api import criar_app
from conselheiro.chains import chain_avaliar, chain_interpretar, chain_responder
from conselheiro.config import Settings
from conselheiro.conselheiro import Conselheiro
from conselheiro.dados import GameData
from conselheiro.guias import carregar_guias

settings = Settings()
try:
    data = GameData.load(settings)
except httpx.HTTPError as erro:
    sys.exit(f"Não consegui baixar os dados da OSRS Wiki ({erro}). Confira a conexão e tente de novo.")
guias = carregar_guias()
# as chains não guardam estado: o histórico fica no Conselheiro de cada sessão
chains = chain_interpretar(settings), chain_avaliar(settings), chain_responder(settings)

app = criar_app(lambda: Conselheiro(settings, data, guias, *chains))

if __name__ == "__main__":
    host, port = os.getenv("HOST", "127.0.0.1"), int(os.getenv("PORT", "8080"))
    print(f"Abra http://localhost:{port} no navegador")
    uvicorn.run(app, host=host, port=port)
