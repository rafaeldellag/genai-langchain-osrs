"""Conselheiro de bosses de OSRS no terminal.

Exemplo: "vou fazer vorkath, tenho 50m, 90 range, rigour e já tenho blowpipe e salve ei"
Digite "sair" para encerrar.
"""

import sys

import httpx
from ollama import ResponseError

from conselheiro.conselheiro import Conselheiro

try:
    conselheiro = Conselheiro()
except httpx.HTTPError as erro:
    sys.exit(f"Não consegui baixar os dados da OSRS Wiki ({erro}). Confira a conexão e tente de novo.")

modelo = conselheiro.settings.ollama_model
print(f"Conselheiro de bosses ({modelo}). Bosses: {', '.join(conselheiro.guias)}.")
print('Digite "sair" para encerrar.\n')

while True:
    try:
        mensagem = input("Você: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        break
    if mensagem.lower() in ("sair", "exit"):
        break
    if not mensagem:
        continue

    try:
        resposta = conselheiro.responder(mensagem)
    except httpx.ConnectError:
        print(f"\nO Ollama não respondeu em {conselheiro.settings.ollama_base_url}. Ele está rodando?\n")
        continue
    except ResponseError as erro:
        if erro.status_code == 404:
            print(f"\nO modelo {modelo} não está instalado. Rode: ollama pull {modelo}\n")
        else:
            print(f"\nO Ollama devolveu um erro: {erro.error}\n")
        continue
    except KeyboardInterrupt:
        print("\n(cancelado)\n")
        continue

    if resposta.tabela:
        print("\n" + resposta.tabela)
    if resposta.avaliacao:
        a = resposta.avaliacao
        print(f"\nAvaliação do seu setup atual: {a.veredito.upper()}")
        for ponto in a.pontos_fortes:
            print(f"  + {ponto}")
        for ponto in a.pontos_fracos:
            print(f"  - {ponto}")
    print("\nConselheiro: " + resposta.texto.strip().replace("\n", "\n     ") + "\n")
