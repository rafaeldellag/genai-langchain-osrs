# Conselheiro de bosses de OSRS (LangChain + LLM local)

Agente de terminal que monta o setup com mais dano para um boss de Old School RuneScape dentro do seu
orçamento, diz em que ordem comprar os upgrades, avalia o que você já tem e responde dúvidas de mecânica.
Roda com uma LLM local pelo Ollama.

```text
Você: quero fazer cerberus com 80m, sou 90 de ataque, forca e defesa, 75 prayer. tenho slayer helmet, dragon defender e arclight

== Cerberus · orçamento 80m gp ==
[melee] Cerberus
  Prayer: Piety · poção: Super combat potion
  Recomendado: 8.84 DPS (~68 s de dano por kill) · custo 77.9m
  Estilo: Lunge (stab) · defesa do boss (menor é melhor): stab 50, slash 100, crush 25, magic 65, ranged 100
    weapon  Ghrazi rapier
    head    Slayer helmet (já tem)
    ...
  Atual (só o que você tem): 5.15 DPS (~117 s de dano por kill)
  Ordem de compra:
    1. legs    Mixed hide legs  +0.05 DPS  23.2k
    2. hands   Regen bracelet  +0.93 DPS  2m
    ...

Avaliação do seu setup atual: FRACO
  - DPS atual é 58% do recomendado

Conselheiro: Comece comprando as Mixed hide legs e depois o Regen bracelet. ...
```

Bosses com guia: **Vorkath, Zulrah, General Graardor, Cerberus e Giant Mole**.

## Conteúdo da aula aplicado

| Aula | Onde está |
| --- | --- |
| LLM local com Ollama (`ChatOllama`) | `conselheiro/chains.py` → `criar_llm` |
| `ChatPromptTemplate` com parâmetros | as três chains em `conselheiro/chains.py` |
| `("placeholder", "{historico}")` com janela de 4 interações | `chain_responder` e `JANELA_HISTORICO` |
| Prompt estruturado com Pydantic (`BaseModel`, `Field`) | `Pedido` e `Avaliacao` + `PydanticOutputParser` |
| `StrOutputParser` | `chain_responder` |
| Chain `prompt \| llm \| parser` | `chain_interpretar`, `chain_avaliar`, `chain_responder` |
| Exercício do agente avaliador | `chain_avaliar` classifica o setup atual em adequado/fraco/arriscado; "indo além": `chain_responder` responde com base na avaliação |

## Como funciona

```text
mensagem ──► chain_interpretar ──► Pedido (boss, orçamento, níveis, itens...)
                                       │
                guias/<boss>.md ◄──────┤  só o guia do boss citado entra no prompt
                                       ▼
                     otimizador (Python, sem LLM): DPS com as fórmulas da
                     calculadora da Wiki + preços do GE ──► tabela
                                       │
                 chain_avaliar ◄───────┤  setup atual vs. recomendado
                                       ▼
                 chain_responder (guia + tabela + avaliação + histórico do boss) ──► resposta
```

**Os números nunca vêm do modelo.** Uma LLM local pequena não conhece a meta do jogo nem tem acesso a dados
externos. Por isso, o DPS é calculado em Python e o modelo só interpreta o pedido e explica o resultado.

- **Cálculo de DPS** (`conselheiro/calc.py`): porte para Python de parte da
  [calculadora de DPS da OSRS Wiki](https://github.com/weirdgloop/osrs-dps-calc). `tests/test_calc.py`
  compara 29 setups com a calculadora original (`tests/gabarito/`), cobrindo cada efeito portado; todos
  batem exatamente.
- **Otimizador** (`conselheiro/otimizador.py`): para cada arma possível, compra armadura pelo maior ganho de
  DPS por gp até o dinheiro acabar. Depois ordena as trocas a partir do que você já tem.
- **Guias** (`guias/*.md`): um arquivo por boss. O cabeçalho YAML diz os estilos, as formas do boss, se exige
  task, os itens que não se compram no GE e o inventário. O texto vai no prompt.
- **Contexto separado por boss**: a chain de interpretação não vê nenhum guia, e cada boss tem o próprio
  histórico e orçamento. Perguntar do Zulrah no meio de uma conversa sobre Vorkath não mistura nada.

## Como rodar

Requer Python 3.13+ e [Ollama](https://ollama.com).

```bash
ollama pull qwen3.5:4b

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python agente.py
```

Na primeira execução, os dados de itens e monstros (~5 MB) são baixados da Wiki e ficam em cache por 7 dias.
Os preços ficam em cache por 10 minutos. Para mudar o modelo ou o endereço do Ollama, copie `.env.example`
para `.env`.

Entre os modelos que já estavam instalados, testei o `qwen3.5:4b` e o `qwen3:8b`; o de 4B errou menos.
Numa GPU de 8 GB, cada resposta levou de 7 a 20 s, e a primeira mensagem demora mais porque carrega o
modelo.

## Testes

```bash
pip install -r requirements-dev.txt
pytest
```

Os testes não precisam do Ollama: as chains recebem modelos falsos (`FakeListChatModel`) e o resto roda de
verdade. Para regenerar o gabarito com a calculadora original (precisa de Node 20+):

```bash
python -m tests.gabarito.gerar_casos
./ferramentas/calc-wiki/rodar.sh gabarito
```

## Adicionar um boss

Crie `guias/<boss>.md` seguindo os existentes. `setups[].formas` usa o nome e a versão do monstro no
[monsters.json](https://tools.runescape.wiki/osrs-dps/cdn/json/monsters.json) da calculadora. Os itens do
inventário precisam do nome exato do GE. `pytest` confere as duas coisas.

## Limitações

- O setup maximiza dano e não considera defesa. Em bosses como o Graardor, você pode preferir trocar dano por
  armadura.
- Não confere requisitos de nível dos itens: com 80 de ataque, por exemplo, ainda pode sugerir a Osmumten's fang
  (pede 82). Confira antes de comprar.
- Ataques especiais, feitiços de spellbook e efeitos de raids ficam fora do cálculo. Em magia, só cajados com
  feitiço embutido (trident, sanguinesti, shadow...).
- Itens que não se compram no GE só entram se você disser que tem.
- Munição e dardos do blowpipe ficam fora do orçamento; a tabela mostra o preço de 500 unidades à parte. Armas de
  arremesso (facas, javelins, thrownaxes) entram no orçamento como uma pilha de 500.
- O texto do modelo de 4B às vezes erra detalhes. A tabela é a fonte confiável.

## Créditos e licença

- Fórmulas de dano: [weirdgloop/osrs-dps-calc](https://github.com/weirdgloop/osrs-dps-calc) (GPL-3.0). Por
  incluir um porte desse código, este projeto também é **GPL-3.0** (ver `LICENSE`).
- Dados de itens, monstros e estratégias: [OSRS Wiki](https://oldschool.runescape.wiki), CC BY-NC-SA 3.0.
- Preços: [API de preços da OSRS Wiki](https://oldschool.runescape.wiki/w/RuneScape:Real-time_Prices).
