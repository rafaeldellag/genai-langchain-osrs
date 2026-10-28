"""Conselheiro de bosses de OSRS no terminal.

Exemplo: "vou fazer vorkath, tenho 50m, 90 range, rigour e já tenho blowpipe e salve ei"
Digite "sair" para encerrar.
"""

from conselheiro.conselheiro import Conselheiro

conselheiro = Conselheiro()
print(f"Conselheiro de bosses ({conselheiro.settings.ollama_model}). Bosses: {', '.join(conselheiro.guias)}.")
print('Digite "sair" para encerrar.\n')

while True:
    mensagem = input("Você: ").strip()
    if mensagem.lower() in ("sair", "exit"):
        break
    if not mensagem:
        continue

    resposta = conselheiro.responder(mensagem)
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
