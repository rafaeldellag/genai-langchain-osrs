# Conselheiro de bosses de OSRS (LangChain + LLM local)

Agente LangChain (no navegador ou no terminal) que monta o setup com mais dano para um boss de Old School
RuneScape dentro do seu orçamento, diz em que ordem comprar os upgrades, avalia o que você já tem, diz se vale a
pena comprar um item, consulta preços do GE e responde dúvidas de mecânica. Roda com uma LLM local pelo Ollama.

```text
Você: quero fazer cerberus com 80m, sou 90 de ataque, forca e defesa, 75 prayer. tenho slayer helmet, dragon defender e arclight
  [ferramenta] calcular_setup(orcamento=80m, ataque=90, forca=90, defesa=90, prayer=75, itens_que_tenho=[...])

== Cerberus · orçamento 80m gp ==
[melee] Cerberus
  Prayer: Piety · poção: Super combat potion
  Recomendado: 8.84 DPS (~68 s de dano por kill) · custo 77.9m
  Estilo: Lunge (stab) · defesa do boss (menor é melhor): stab 50, slash 100, crush 25, magic 65, ranged 100
    weapon  Ghrazi rapier
    head    Slayer helmet (já tem)
    ...
  Ordem de compra:
    1. legs    Mixed hide legs  +0.05 DPS  23.2k
    2. hands   Regen bracelet  +0.93 DPS  2m
    ...

Avaliação do seu setup atual: FRACO
  - DPS atual é 58% do recomendado

Conselheiro: Comece pelas Mixed hide legs e pelo Regen bracelet. ...

Você: vale a pena comprar a ghrazi rapier?
  [ferramenta] avaliar_compra(item=Ghrazi rapier)
Conselheiro: Com o que você tem hoje, a rapier leva seu DPS de 5.15 para ...
```

Bosses com guia: **Vorkath, Zulrah, General Graardor, Cerberus e Giant Mole**.

## Conteúdo da aula aplicado

| Aula | Onde está |
| --- | --- |
| LLM local com Ollama (`ChatOllama`) | `conselheiro/chains.py` → `criar_llm` |
| Prompt estruturado com Pydantic (`BaseModel`, `Field`) | `Pedido` (argumentos da ferramenta) e `Avaliacao` |
| `ChatPromptTemplate` + `PydanticOutputParser`, chain `prompt \| llm \| parser` | `chain_avaliar`, chamada de dentro da ferramenta `calcular_setup` |
| Histórico da conversa | checkpointer do LangGraph, uma thread por conversa e por boss |
| Exercício do agente avaliador | `chain_avaliar` classifica o setup atual em adequado/fraco/arriscado; "indo além": o agente responde a partir da avaliação e do cálculo |

## Por que LangChain aqui

O modelo decide o que fazer com cada mensagem: calcular um setup, avaliar uma compra, ver um preço ou responder
pelo guia. Isso é um agente com ferramentas (`create_agent`), e o LangChain entrega as peças que dariam muito
trabalho escrever na mão:

| Peça | Para quê, neste projeto |
| --- | --- |
| `@tool` com `args_schema=Pedido` | o esquema Pydantic vira a definição da ferramenta que o modelo preenche (orçamento, níveis, itens) |
| `ToolRuntime` | as ferramentas recebem a sessão, o boss e a mensagem original sem o modelo ver esses parâmetros |
| `response_format="content_and_artifact"` | o modelo recebe o texto do cálculo e a interface recebe a tabela e a avaliação estruturadas |
| `InMemorySaver` (checkpointer) | memória de cada conversa; uma thread por boss, então o histórico do Zulrah não vaza para o Vorkath |
| `@dynamic_prompt` | o guia do boss da conversa entra no system prompt, e só ele |
| `ContextEditingMiddleware` | cada tabela tem ~900 tokens; as antigas saem do contexto e fica só a última |
| `trim_messages` num `@wrap_model_call` | conversa longa não estoura o `num_ctx` do Ollama |
| `@wrap_model_call` (`corrigir_argumentos`) | o modelo de 4B inventava níveis, task e estilo ("90 magic" virava ataque 90) e errava valores ("80 defesa" virou 77); a chamada de `calcular_setup` é conferida contra a mensagem antes de a ferramenta rodar e antes de a tela mostrar |
| `@wrap_model_call` (`exigir_ferramenta`) | o modelo de 4B dizia "calculei o setup" sem chamar a ferramenta; se ele responde com números sem ter usado ferramenta, o middleware pede de novo |
| `@wrap_model_call` (`conferir_itens`) | a resposta citava item que não estava no cálculo (DHCB no lugar do Dragon crossbow no Zulrah); o middleware pede para reescrever com as armas do cálculo e, se o modelo insistir, tira a frase |
| `@after_model` (`limpar_texto`) | tira markdown e corta resposta em loop ou interrompida pelo limite de tokens, direto no estado da conversa |
| `ModelCallLimitMiddleware` | o modelo pequeno não fica chamando ferramenta para sempre |
| `stream(stream_mode=["updates", "messages"])` | a interface mostra cada ferramenta chamada, o cálculo e o texto chegando em tempo real |

## Como funciona

```text
mensagem ──► boss citado? (Python) ──► thread "<sessão>:<boss>" no checkpointer
                                            │
                    guias/<boss>.md ──► system prompt (@dynamic_prompt)
                                            ▼
                ┌──────────── agente (create_agent + middlewares) ────────────┐
                │  o modelo escolhe:                                          │
                │   calcular_setup ─► otimizador + preços ─► tabela           │
                │                     └► chain_avaliar ─► adequado/fraco/...  │
                │   avaliar_compra ─► DPS com e sem o item                    │
                │   preco_item     ─► preço no GE                             │
                │   (nenhuma)      ─► responde pelo guia                      │
                └──────────────────────────┬──────────────────────────────────┘
                                           ▼
                     eventos em streaming: ferramenta, cálculo, texto, fim
```

**Os números nunca vêm do modelo.** Uma LLM local pequena não conhece a meta do jogo nem tem acesso a dados
externos. O DPS e os preços saem das ferramentas; o modelo escolhe a ferramenta, preenche os argumentos e explica
o resultado. Onde o modelo de 4B erra com frequência, o código confere: o orçamento e os níveis são lidos da
mensagem ("120kk" é 120 milhões, "80 defesa" é 80), estilo, task e Rigour só valem se a mensagem citar, e item
citado na resposta tem que ter vindo do cálculo, do guia ou da conversa.

- **Cálculo de DPS** (`conselheiro/calc.py`): porte para Python de parte da
  [calculadora de DPS da OSRS Wiki](https://github.com/weirdgloop/osrs-dps-calc). `tests/test_calc.py`
  compara 29 setups com a calculadora original (`tests/gabarito/`), cobrindo cada efeito portado; todos
  batem exatamente.
- **Otimizador** (`conselheiro/otimizador.py`): para cada arma possível, compra armadura pelo maior ganho de
  DPS por gp até o dinheiro acabar. Depois ordena as trocas a partir do que você já tem.
- **Guias** (`guias/*.md`): um arquivo por boss. O cabeçalho YAML diz os estilos, as formas do boss, se exige
  task, os itens que não se compram no GE e o inventário. O texto vai no system prompt.
- **Contexto separado por boss**: cada boss tem a própria thread, o próprio guia no prompt e o próprio
  orçamento. Perguntar do Zulrah no meio de uma conversa sobre Vorkath não mistura nada.

## Como rodar

Requer Python 3.13+ e [Ollama](https://ollama.com).

```bash
ollama pull qwen3.5:4b

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python servidor.py   # interface web em http://localhost:8080
python agente.py     # ou no terminal
```

Na interface web, cada aba do navegador é uma conversa separada, e o botão "Nova conversa" zera o histórico.
Para mudar o endereço, use as variáveis `HOST` e `PORT`. A API fica documentada em `/docs`.

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

Os testes não precisam do Ollama: o agente recebe um modelo falso que segue um roteiro de chamadas de
ferramenta (`GenericFakeChatModel`) e o resto roda de verdade, inclusive os middlewares e o checkpointer. Para regenerar o gabarito com a calculadora original (precisa de Node 20+):

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
- O texto do modelo de 4B às vezes erra detalhes que o código não confere, como para que serve um item ou uma
  conta que ele mesmo fez. A tabela é a fonte confiável.

## Créditos e licença

- Fórmulas de dano: [weirdgloop/osrs-dps-calc](https://github.com/weirdgloop/osrs-dps-calc) (GPL-3.0). Por
  incluir um porte desse código, este projeto também é **GPL-3.0** (ver `LICENSE`).
- Dados de itens, monstros e estratégias: [OSRS Wiki](https://oldschool.runescape.wiki), CC BY-NC-SA 3.0.
- Preços: [API de preços da OSRS Wiki](https://oldschool.runescape.wiki/w/RuneScape:Real-time_Prices).
