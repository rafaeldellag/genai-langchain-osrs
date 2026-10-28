"""Conselheiro de bosses de OSRS no terminal.

Exemplo: "vou fazer vorkath, tenho 50m, 90 range, rigour e já tenho blowpipe e salve ei"
Digite "sair" para encerrar. Para usar no navegador, rode servidor.py.
"""

import sys

import httpx

from conselheiro.conselheiro import Conselheiro, explicar_erro

try:
    conselheiro = Conselheiro()
except httpx.HTTPError as erro:
    sys.exit(f"Não consegui baixar os dados da OSRS Wiki ({erro}). Confira a conexão e tente de novo.")

print(f"Conselheiro de bosses ({conselheiro.settings.ollama_model}). Bosses: {', '.join(conselheiro.guias)}.")
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
    except KeyboardInterrupt:
        print("\n(cancelado)\n")
        continue
    except Exception as erro:
        if (explicacao := explicar_erro(erro, conselheiro.settings)) is None:
            raise
        print(f"\n{explicacao}\n")
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
