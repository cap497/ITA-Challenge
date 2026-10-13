# Popular Pet · Precificação Inteligente

O motor calcula, o analista decide, a gestora aprova, tudo fica registrado.
Quatro telas, um comando, nenhuma infraestrutura.

---

## Como rodar

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

O navegador abre em **http://127.0.0.1:8000**.

**A aplicação sobe em um segundo e abre o navegador direto no painel.** O
pipeline não é uma tela e não espera clique: ele roda sozinho quando chega dado
novo, que é como um serviço em nuvem funciona. Na primeira subida o dado chega
junto com a planilha, então ele dispara em segundo plano e o painel mostra o
progresso numa faixa no alto — "Atualizando o cenário · Selecionar a carteira".
Terminado, a faixa passa a dizer de quando é o cenário e quando ele se atualiza
sozinho de novo. O botão **Atualizar agora** existe só para forçar antes da hora.

```
Popular Pet · Precificação Inteligente
──────────────────────────────────────────────
  dado novo detectado — pipeline rodando em segundo plano
  o progresso aparece no painel; a tela já pode ser aberta
──────────────────────────────────────────────
  Aberto em http://127.0.0.1:8000
```

**Não precisa de Docker, Postgres, Node, internet nem modelo de IA.** Python 3.10
ou mais novo.

| Precisa de… | Comando |
|---|---|
| outra porta | `python app.py --porta 8080` |
| recomeçar do zero | `python app.py --refazer` |
| não abrir o navegador sozinho | `python app.py --sem-navegador` |

A cadência é a do coletor da Popular Pet: coleta de concorrência diária, ERP de madrugada. Não há motivo para recalcular preço entre duas coletas.

### Se algo der errado

| Sintoma | O que fazer |
|---|---|
| `ModuleNotFoundError` | o venv não está ativo, ou faltou `pip install -r requirements.txt` |
| "A porta 8000 já está em uso" | `python app.py --porta 8080` (no macOS a 5000 é do AirPlay) |
| Números estranhos depois de mexer na planilha | `python app.py --refazer` |

---

## As quatro telas

| Tela | Para que serve |
|---|---|
| **Painel** | o dia de hoje, sem rolagem: onde estamos caro, o que está retido, o que vale mais, e um botão — Iniciar Análise |
| **Análise** | um item por vez, com o histórico de 12 meses que situa a decisão e a aposta explícita |
| **Histórico** | o agregado de 12 meses em cima, o item escolhido e o placar das decisões aplicadas embaixo |
| **Base** | CSV cru do ERP com descoberta de colunas, qualidade do dado, governança verificada e envelope operacional |

Quem decide é escolhido no canto superior direito. Trocar para a **Carla Menezes
(Gestora)** libera os itens retidos por alçada — é assim que se demonstra a R04.

Na Análise, as setas ← e → andam na fila sem tirar a mão do teclado.

---

## Modelo de linguagem: opcional, e desligado por padrão

**A plataforma roda inteira sem modelo nenhum.** A tela **Base** diz "Nenhum
modelo conectado" e a justificativa é composta de forma determinística, a partir
dos mesmos fatores. Nada fica faltando na demonstração.

Ligar um modelo depois não exige reiniciar: suba o servidor e clique em
**Procurar modelo** na tela Base.

O modelo entra **sob demanda, um item por vez**, pelo botão *Reescrever com o
modelo local* na tela de Análise. Nunca em lote: 120 itens × alguns segundos
cada travaria a aplicação antes de qualquer tela existir.

### Qual ferramenta usar

| Ferramenta | Serve aqui? |
|---|---|
| **LM Studio** | **Sim, é o caminho mais curto no Windows.** Carregue um modelo e vá em Developer → Start Server (porta 1234). |
| **Ollama** | Sim, se preferir linha de comando: `ollama pull llama3.2:3b` e `ollama serve` (porta 11434). |
| **llama.cpp / llamafile** | Funciona: `llama-server -m modelo.gguf --port 8080`. Mais trabalho manual, nenhum ganho aqui. |
| **vLLM** | **Não.** É servidor para GPU em escala, pensado para atender muitos usuários num servidor Linux com CUDA. Não roda GGUF, é penoso no Windows, e o ganho dele — throughput alto — não existe quando o uso é um parágrafo por vez. |

Qualquer servidor com API compatível com OpenAI serve. A plataforma procura
sozinha nas portas 1234, 11434, 8080, 8000 e 1337, com sonda de 0,35 s — e só
quando alguém abre a tela, nunca dentro do pipeline. Para apontar para outro
endereço:

```bash
export PP_LLM_BASE=http://192.168.0.10:1234/v1
export PP_LLM_MODELO=llama-3.2-3b-instruct    # opcional
export PP_SEM_LLM=1                           # desliga de vez
```

### O modelo pequeno erra número, e o sistema conta isso

Um 3B quantizado troca dígito, arredonda sozinho e inventa valor que "parece
certo". Por isso **todo valor em reais e todo percentual do texto é conferido
contra os fatores de origem** antes de aparecer na tela. Número que não bate
reprova o texto inteiro, a versão determinística assume e a tela diz qual número
foi citado errado.

Isso não é defeito escondido: é a razão de a camada 3 ser separada das camadas 1
e 2. Sem servidor nenhum a plataforma funciona igual, com a justificativa
composta de forma determinística.

| Modelo | Tamanho | Observação |
|---|---|---|
| Llama 3.2 3B Instruct | ~1,8 GB | recomendado · bom português, rápido em CPU |
| Llama 3 8B Instruct | ~4,4 GB | texto melhor, mas a rodada demora bem mais em CPU |
| Llama 3.2 1B Instruct | ~1,2 GB | roda em qualquer máquina; reprova mais na conferência |
| TinyLlama 1.1B | ~1,1 GB | serve para mostrar o conferidor pegando erro |

Nenhum deles é necessário para a demonstração funcionar.

---

## As três camadas

| Camada | O que faz | Onde está |
|---|---|---|
| **1 · Estimar** | demanda, anomalia de coleta, chance de aceite | `nucleo/modelos.py` |
| **2 · Decidir** | referência de mercado, piso, R01–R12, preço, risco, carteira | `nucleo/motor.py`, `nucleo/carteira.py` |
| **3 · Explicar** | justificativa em linguagem de negócio | `nucleo/texto.py`, `nucleo/llm.py` |

A camada 2 é determinística: o mesmo dado sempre produz a mesma recomendação, e
nenhuma sugestão pode violar uma regra dura. As camadas 1 e 3 acrescentam
informação e legibilidade — a aplicação funciona sem elas.

**Por que não usar modelo de linguagem para decidir o preço.** Dado estruturado
e numérico pede modelo clássico: é mais barato, mais rápido, reprodutível e
auditável. Pedir a um LLM que escolha preço seria impossível de defender diante
de um analista que pergunta "por que esse número?".

---

## Recomendação e retenção são coisas diferentes

Duas perguntas que não podem morar no mesmo campo:

1. **Qual é a recomendação de preço?** SUBIR, BAIXAR ou MANTER — sempre com preço.
2. **O que precisa acontecer antes de ela valer?** Nada, aprovação da gestora, ou
   espera até uma data.

**REVISAR tem um significado só: o motor não tem base para opinar.** Sem
concorrente válido, com coleta velha, ou com regras se contradizendo. Aí
"revisar" quer dizer olhar o dado, não o preço.

Campanha ativa não apaga a opinião do motor — ela adia a aplicação dela. Item de
curva A ou estratégico não vira "REVISAR": mantém a recomendação e sobe para a
gestora.

### Confiança baixa encurta o passo, não cala o motor

Referência fraca não é falta de base: é base com margem de erro maior. Com
confiança na metade do mínimo exigido, anda-se metade do caminho até o alvo, e a
decisão sobe para a gestora. É a mesma ideia de encolher estimativa ruidosa na
direção do valor conhecido — aqui o valor conhecido é o preço que já está na
gôndola.

---

## As regras

| Regra | O que faz | Como entra |
|---|---|---|
| R01 margem mínima | **bloqueia**: nenhum preço abaixo do piso entra, nem digitado à mão | dura |
| R02 variação máxima | limita a ±5% por decisão; editar além disso passa com aviso registrado | dura |
| R03 curva A > 3% | sobe para a gestora | retenção |
| R04 estratégico | sobe para a gestora, exige justificativa escrita e entra na rodada na marra | retenção |
| R05 concorrente indisponível | zera o peso daquela coleta | modulador |
| R06 coleta > 48 h | descarta a coleta; se todas forem velhas, vira REVISAR | modulador |
| R07 coerência de canal | loja e e-commerce entram ou saem juntos da rodada | restrição no MILP |
| R08 alterado há < 3 dias | **bloqueia** até a data — é a única regra que a Popular Pet marcou como "Bloquear" | dura |
| R09 campanha ativa | sobe para a gestora, com a campanha, o desconto e a data de fim na tela | retenção |
| R10 anomalia de coleta | z robusto por MAD descarta o preço destoante | modulador |
| R11 cobertura < 15 dias | trava redução, exceto com pedido chegando em até 7 dias | modulador |
| R12 cobertura > 90 dias | empurra o alvo para baixo, olhando a cobertura projetada | modulador |

---

## O problema de otimização

Depois que o preço viável de cada item é calculado, o sistema escolhe **quais**
alterar:

```
max   Σ |impacto_i| · x_i
s.a.  Σ x_i ≤ N                   capacidade de validação da rodada
      x_loja − x_ecom = 0         quando mover um canal sozinho quebraria a R07
      x_i = 1                     produto estratégico entra por obrigação
      x_i ∈ {0,1}
```

Resolvido com HiGHS via `scipy.optimize.milp`. A restrição de capacidade é o que
transforma isto numa seleção de carteira: a pergunta deixa de ser *qual o preço
deste item* e passa a ser *quais N preços mudar hoje, entre milhares*.

---

## Comandos de verificação

```bash
python testar.py      # 31 verificações: regras, contas, alçada, auditoria
python casos.py       # os 21 casos representativos, em HTML e PNG
python envelope.py    # o envelope operacional, medido nesta máquina
python telas.py       # as maquetes das quatro telas
```

`testar.py`, `casos.py` e `envelope.py` rodam em banco descartável e não sujam a
base de trabalho.

---

## Os arquivos

```
app.py              servidor e API — é o que você roda
testar.py           verificação automática
casos.py            bateria de casos representativos
envelope.py         medição dos limites operacionais
telas.py            maquetes estáticas das telas
nucleo/
  banco.py          esquema SQLite, gatilhos de auditoria e migrações
  erp.py            leitura e sincronização da planilha
  motor.py          camada 2 — referência, piso, R01–R12, decisão, risco
  carteira.py       camada 2 — seleção da rodada por MILP
  modelos.py        camada 1 — demanda, anomalia, chance de aceite
  texto.py          camada 3 — justificativa e conferência numérica
  llm.py            camada 3 — descoberta do servidor de modelo local
  series.py         12 meses de preço praticado: item, categoria, agregado
  acompanhamento.py impacto pós-decisão: previsto × realizado
  linha_base.py     os 8 indicadores declarados pela Popular Pet
  ingestao.py       descoberta de colunas de CSV cru
  governanca.py     papéis, inventário de dados e garantias verificadas
  semente.py        histórico de demonstração
  servicos.py       rodadas, decisões, métricas, exportações
web/                as quatro telas, sem framework e sem build
dados/
  base_popular_pet.xlsx    o ERP simulado
  popularpet.db            criado na primeira execução
```

---

## O que este protótipo não faz

- **Não aplica preço em sistema nenhum.** A exportação do CSV é a fronteira.
- **Não estima elasticidade-preço.** O preço quase não varia na base, então a
  sensibilidade da demanda não é identificável. Todo impacto é apresentado sob
  volume constante, e o ponto de equilíbrio existe para tornar essa aposta
  explícita.
- **Não compara preço entregue com o nosso.** A base traz o frete como custo
  rateado, não como valor cobrado do cliente. O frete do concorrente é usado para
  reponderar a referência, não para reindexar a nossa posição.
- **O "depois" da tela de Histórico é projeção sintética**, marcada como tal em
  três lugares. A base da Popular Pet termina na data de referência. Quando o ERP
  publicar venda posterior a uma aplicação, o realizado substitui a projeção sem
  tocar em código.
- **Não usa dado de concorrente que não seja preço publicado ao consumidor.**
