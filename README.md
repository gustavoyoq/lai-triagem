# lai-triagem — risco de reencaminhamento de pedidos LAI (Fala.BR)

Sinaliza cada pedido de acesso à informação que chega à plataforma federal
**Fala.BR** como `ALTO RISCO` ou `BAIXO RISCO` de reencaminhamento interno
(campo `FoiReencaminhado`), para que analistas seniores do SIC sejam dirigidos
aos pedidos com maior chance de roteamento incorreto.

> ## Leia antes de usar o escore
>
> Este arquivo é o **manual de uso**: instala, sobe o serviço, faz a primeira
> chamada. Ele não discute se o modelo presta.
>
> Essa discussão existe e é desfavorável ao próprio modelo. Antes de usar o
> escore para decidir qualquer coisa, leia
> [`docs/RESULTADOS.md`](docs/RESULTADOS.md) — em uma frase: **o modelo não
> supera uma consulta à taxa histórica do órgão**, que é uma tabela de uma
> linha.
>
> *Termo desconhecido? O [glossário](#glossário) no fim deste arquivo explica 26
> deles, em uma frase cada.*

## Contrato de dados: o que o chamador informa

Os **oito** campos de histórico do solicitante são **dado pessoal** e por
decisão explícita **não são embarcados no artefato** — o chamador as informa, e o
Fala.BR já as possui. O artefato contém apenas tabelas de conduta de **órgãos**
(entidades públicas). Racional completo em
[`docs/DECISAO_ESTADO_SOLICITANTE.md`](docs/DECISAO_ESTADO_SOLICITANTE.md).

Omitir o histórico é permitido: os campos valem `-1`, o `LightGBM` os trata como
faltantes e `/score` devolve `historico_informado: false` para que a operação
degradada não passe silenciosa.

---

# Tutorial: carregar o modelo no BentoML desde o zero

Sequência completa, do clone até a primeira resposta HTTP. Ambiente gerido por
**uv** (não use `pip` diretamente).

## Pré-requisito — instalar o `uv`

Este projeto usa o `uv` para gerenciar pacotes e o ambiente virtual. Instale-o
antes de prosseguir:

```bash
# Linux / macOS / WSL
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Alternativa via pip (se já tiver Python instalado)
pip install uv
```

Após instalar, feche e reabra o terminal para que o `uv` fique disponível no PATH.

## Passo 1 — clonar e criar o ambiente

> **Se você já recebeu a pasta pronta, pule o `git clone`** e vá direto ao
> `uv sync`. Clonar dentro de uma cópia existente cria
> `lai-triagem/lai-triagem/` sem dar erro, e confunde.

```bash
git clone git@github.com:lgallindo/lai-triagem.git && cd lai-triagem
uv sync --extra dev

# Somente Ubuntu/WSL: dependência nativa exigida pelo LightGBM
sudo apt-get update && sudo apt-get install -y libgomp1
```

`uv sync` lê o `pyproject.toml` e instala as versões exatas do treinamento
registrado em `.venv/` (criado automaticamente). O extra `dev` acrescenta
ferramentas de desenvolvimento como `pytest` e `ruff`.

## Passo 2 — conferir que o artefato está presente

O repositório **já traz o modelo treinado**, então não é preciso treinar para
servir:

```bash
ls -la artifacts/registered/model_arrival.txt artifacts/registered/preprocessor.json
```

O formato é texto nativo do LightGBM mais um acompanhante JSON — sem `pickle`,
portanto sem acoplamento de versão no carregamento.

## Passo 3 — validar o artefato

```bash
uv run lai-validate-artifact --artifact-dir artifacts/registered
```

Este comando verifica a integridade do artefato (SHA-256), a ausência de
features com leakage e a ausência de dados pessoais. Saída esperada:

```
artifact_valid version=<versão> sha256=<hash>
```

> Se o comando terminar sem erro e imprimir `artifact_valid`, o artefato está
> íntegro e pronto para servir.

## Passo 4 — subir o serviço

```bash
uv run bentoml serve mlops_lai.service:LaiTriagem --port 3000
```

> ### Este comando não devolve o terminal
>
> Ele fica rodando e imprimindo log — é assim que tem de ser, é um servidor. Ele
> **não** vai voltar para o `$` de comando, e você **não** deve interrompê-lo.
>
> **Abra uma segunda janela de terminal** para os próximos passos, e deixe esta
> aqui de lado, servindo. No fim, volte nela e pressione `Ctrl+C` para encerrar.
>
> Se preferir não abrir outra janela, ponha o serviço em segundo plano:
>
> ```bash
> uv run bentoml serve mlops_lai.service:LaiTriagem --port 3000 > /tmp/servico.log 2>&1 &
> ```
>
> Nesse caso o log vai para `/tmp/servico.log`, e você encerra depois com
> `kill %1` na mesma janela.

O serviço sobe em `http://localhost:3000`. A documentação interativa fica em
`http://localhost:3000/docs`.

### Testando pelo navegador (Swagger UI)

Acesse `http://localhost:3000/docs` no navegador para ver e executar os endpoints
diretamente, sem precisar do `curl`.

1. Clique em **POST /score** → **Try it out**
2. Substitua o corpo pelo exemplo abaixo e clique em **Execute**:

```json
{
  "pedido": {
    "OrgaoDestinatario": "CC-PR – Casa Civil da Presidência da República",
    "DataRegistro": "15/09/2026",
    "Escolaridade": "Ensino Fundamental",
    "UF_sol": "PE",
    "n_pedidos_previos": 80,
    "prev_reenc_solicitante": 3,
    "prev_reenc_solicitante_den": 80,
    "n_pedidos_previos_neste_orgao": 12,
    "prev_reenc_neste_orgao": 0,
    "prev_reenc_neste_orgao_den": 12,
    "n_orgaos_distintos_previos": 14,
    "dias_desde_ultimo_pedido": 5
  }
}
```

Resposta esperada:

```json
{
  "probabilidade_reencaminhamento": 0.31659,
  "alerta": "ALTO RISCO",
  "threshold": 0.159391,
  "probabilidade_calibrada": 0.280263,
  "calibrada_apenas_para_leitura": true,
  "orgao_conhecido": true,
  "orgao_rate_historica": 0.478643,
  "orgao_rate_movel_90d": 0.257573,
  "base_rate_coorte": 0.080268,
  "historico_informado": true,
  "historico_parcial_ignorado": false
}
```

**Se aparecer `OSError: [Errno 98] Address already in use`:** alguém já está na
porta 3000. **A saída mais simples e segura é trocar de porta** — não precisa
descobrir quem é, nem encerrar nada:

```bash
uv run bentoml serve mlops_lai.service:LaiTriagem --port 3001
```

Se trocar de porta, troque também nos comandos seguintes: onde este README
escrever `localhost:3000`, use `localhost:3001`.

> **Por que não sair matando processo.** Se você está no WSL, a porta pode
> estar ocupada por um programa do **Windows**, não do Linux — o Docker Desktop
> é o caso mais comum, porque o WSL e o Windows compartilham o `localhost`.
> Nesse caso `ss -ltnp` mostra a porta ocupada **sem dizer de quem é**, assim:
>
> ```
> LISTEN 0  4096  *:3000  *:*
> ```
>
> A coluna do processo vem vazia porque o processo não está no Linux. Então
> `pkill` não resolve, e insistir só te faz encerrar algo que você não queria.
> Quem estiver no Windows pode conferir com
> `Get-NetTCPConnection -LocalPort 3000 -State Listen`.

Se `ss -ltnp` **mostrar** um nome de processo e for um `bentoml serve` que você
mesmo deixou para trás, aí sim encerre pelo PID que ele indicou:

```bash
ss -ltnp | grep :3000          # se aparecer um PID, é processo do Linux
kill <PID>                     # encerre pelo PID, não por nome
```

**Para confirmar que subiu**, antes de tentar o Passo 5 (ajuste a porta se você
trocou):

```bash
curl -sS http://localhost:3000/healthz && echo " -> de pé"
```

## Passo 5 — primeira chamada

Com o histórico do solicitante informado pelo chamador (precisão plena). São
**oito** campos de histórico, e é tudo-ou-nada: mandar sete faz o serviço
descartar o conjunto inteiro. Note os dois terminados em `_den`, que são os
denominadores maturados — eles **não** podem faltar:

```bash
curl -sS -X POST http://localhost:3000/score -H 'Content-Type: application/json' -d '{"pedido":{"OrgaoDestinatario":"CC-PR – Casa Civil da Presidência da República","DataRegistro":"15/09/2026","Escolaridade":"Ensino Fundamental","UF_sol":"PE","n_pedidos_previos":80,"prev_reenc_solicitante":3,"prev_reenc_solicitante_den":80,"n_pedidos_previos_neste_orgao":12,"prev_reenc_neste_orgao":0,"prev_reenc_neste_orgao_den":12,"n_orgaos_distintos_previos":14,"dias_desde_ultimo_pedido":5}}'
```

Resposta:

```json
{
  "probabilidade_reencaminhamento": 0.31659,
  "alerta": "ALTO RISCO",
  "threshold": 0.159391,
  "probabilidade_calibrada": 0.280263,
  "calibrada_apenas_para_leitura": true,
  "orgao_conhecido": true,
  "orgao_rate_historica": 0.478643,
  "orgao_rate_movel_90d": 0.257573,
  "base_rate_coorte": 0.080268,
  "historico_informado": true,
  "historico_parcial_ignorado": false
}
```

Confira sempre `historico_informado`. Se vier `false` com
`historico_parcial_ignorado: true`, faltou algum dos oito campos e o escore que
você recebeu é o de **sem histórico**, não o de precisão plena.

E **sem** o histórico — só os dois campos obrigatórios:

```bash
curl -sS -X POST http://localhost:3000/score -H 'Content-Type: application/json' -d '{"pedido":{"OrgaoDestinatario":"CC-PR – Casa Civil da Presidência da República","DataRegistro":"15/09/2026"}}'
```

Resposta:

```json
{
  "probabilidade_reencaminhamento": 0.354094,
  "alerta": "ALTO RISCO",
  "threshold": 0.159391,
  "probabilidade_calibrada": 0.280263,
  "calibrada_apenas_para_leitura": true,
  "orgao_conhecido": true,
  "orgao_rate_historica": 0.478643,
  "orgao_rate_movel_90d": 0.257573,
  "base_rate_coorte": 0.080268,
  "historico_informado": false,
  "historico_parcial_ignorado": false
}
```

O escore **sobe** de 0,316590 para 0,354094 e `historico_informado` vira
`false`. O órgão é o mesmo; a diferença é tudo o que se sabe sobre o
solicitante — e, neste caso, o histórico deste veterano **atenuava** o risco:
saber que ele já fez 80 pedidos, 12 neste órgão, e que nenhum foi reencaminhado
aqui, baixa o escore em relação a não saber nada.

**Regra prática:** ordene a fila pelo `probabilidade_reencaminhamento`, o
escore cru. O `probabilidade_calibrada` serve para leitura humana — dá para
lê-lo como probabilidade — mas **não** para ordenar: ele empata casos que o
escore cru separava, e ordenar por ele custa precisão. O quanto custa, e por
quê, está em [`docs/RESULTADOS.md`](docs/RESULTADOS.md).

> **O histórico é um conjunto de tudo-ou-nada.** Se você mandar alguns dos oito
> campos e não todos, o serviço **descarta o conjunto inteiro** e avisa em
> `historico_parcial_ignorado: true`. Meio histórico produziria uma combinação
> que nunca aparece no treinamento.

## Passo 6 — a consulta por órgão, sem modelo

```bash
curl -sS -X POST http://localhost:3000/score_baseline -H 'Content-Type: application/json' -d '{"pedido":{"OrgaoDestinatario":"CC-PR – Casa Civil da Presidência da República","DataRegistro":"15/09/2026"}}'
```

Resposta:

```json
{
  "probabilidade_reencaminhamento": 0.478643,
  "alerta": "ALTO RISCO",
  "metodo": "lookup histórico por órgão (sem modelo)",
  "orgao_conhecido": true
}
```

Retorna `0.478643`, a taxa histórica crua deste órgão, sem modelo nenhum: é uma
consulta a uma tabela. O endpoint existe para você poder comparar as duas
respostas a qualquer momento.

Vale a pena comparar, e o resultado surpreende — mas isso é assunto do relatório
de avaliação, não deste manual. Está em
[`docs/RESULTADOS.md`](docs/RESULTADOS.md).

## Passo 7 (opcional) — sem BentoML

Para conferir o caminho de código sem subir servidor:

```bash
uv run python examples/minimal_predict.py
```

## Passo 8 (opcional) — retreinar

Só é necessário para reproduzir o treinamento. Baixe os dados (~38 MB) e rode:

```bash
mkdir -p data/raw data/interim
# 2022-2026 formam a coorte; 2012-2021 servem SÓ para datar o nascimento de cada
# órgão (sem eles, dias_desde_primeiro_pedido_do_orgao fica censurado em 2022).
for y in $(seq 2012 2026); do
  curl -sS -o data/raw/Pedidos_csv_$y.zip "https://dadosabertos-download.cgu.gov.br/FalaBR/Arquivos_FalaBR/Pedidos_csv_$y.zip"
  unzip -oq data/raw/Pedidos_csv_$y.zip -d data/interim/
done
uv run python scripts/train.py
```

Leva cerca de **40 s** no total (8 s de carga e featurização, ≈1,7 s por
ajuste, mais a datação dos órgãos). Procedimento completo e auditável em
[`docs/TREINAMENTO.md`](docs/TREINAMENTO.md).

---

# Entradas

Todos os campos abaixo existem no instante em que o pedido chega à caixa de
entrada do Fala.BR. `OrgaoDestinatario` e `DataRegistro` são obrigatórios; o
resto é opcional e ausência é tratada como valor faltante.

| Campo | Origem | Tipo | Uso no modelo | Razão da inclusão |
|---|---|---|---|---|
| `OrgaoDestinatario` | Pedidos | categórico | usada; alimenta as tabelas por órgão | Órgão a que o cidadão endereçou. **Obrigatório.** É o endereçado, não o destinatário final |
| `DataRegistro` | Pedidos | data, **`dd/mm/aaaa`** | deriva `reg_month`, `reg_dow`, `reg_day` | Único carimbo temporal disponível na chegada. Formato obrigatório: `15/09/2026` |
| `Esfera` | Pedidos | categórico | baixo | Federal/estadual/municipal; separa regimes de competência |
| `UF` | Pedidos | categórico | baixo | UF do pedido quando não federal |
| `Municipio` | Pedidos | categórico | usada | Município do pedido quando não federal |
| `FormaResposta` | Pedidos | categórico | baixo | Escolhida pelo solicitante **na abertura** — logo, disponível |
| `OrigemSolicitacao` | Pedidos | categórico | usada | Balcão SIC vs Internet; definido na abertura |
| `TipoDemandante` | Solicitantes | categórico | usada | Pessoa física/jurídica |
| `Genero` | Solicitantes | categórico | baixo | Perfil; 70,7% ausente |
| `Escolaridade` | Solicitantes | categórico | baixo | Perfil; **76,2% ausente** — central na auditoria de equidade |
| `Profissao` | Solicitantes | categórico | usada | Perfil; 76,9% ausente |
| `TipoPessoaJuridica` | Solicitantes | categórico | baixo | Vazio para pessoa física |
| `Pais` | Solicitantes | categórico | baixo | País de residência |
| `UF_sol` | Solicitantes | categórico | usada | UF de residência; alimenta `uf_match` |
| `Municipio_sol` | Solicitantes | categórico | usada | Município de residência — quarto sinal mais forte |
| `DataNascimento` | Solicitantes | data, **`dd/mm/aaaa`** | deriva `idade` (0,87%) | Idade na data do registro; descartada fora de 10–110 anos |

## Variáveis derivadas — lado do órgão (embarcadas no artefato)

Conduta de entidade pública; sem dado pessoal. Reajustadas a cada retreinamento.

| Derivada | Fórmula |
|---|---|
| `orgao_rate` | taxa histórica suavizada do órgão, ajustada **só nos anos de treino** (prior 50 × taxa-base) |
| `orgao_rate_movel_90d` | taxa em janela móvel de 90 d, **defasada 60 d**; prior de suavização também só do treino |
| `orgao_rate_movel_365d` | idem, 365 d |
| `dias_desde_primeiro_pedido_do_orgao` | idade do órgão; datada de 2012 em diante |
| `idade` | `DataRegistro − DataNascimento`, em anos |
| `reg_month`, `reg_dow`, `reg_day` | componentes de `DataRegistro` |
| `uf_match` | `UF_sol == UF` |

## Variáveis de histórico — informadas pelo chamador

Dado pessoal; **não embarcadas** no artefato. São **oito campos, e o conjunto
é atômico**: ou você manda os oito, ou o serviço descarta todos e usa o padrão
`-1`. Não existe "mandar alguns". Meio histórico produziria uma combinação de
valores que nunca aparece no treinamento, e o escore não teria significado.

| Campo | Significado |
|---|---|
| `n_pedidos_previos_neste_orgao` | pedidos anteriores deste solicitante **a este órgão** |
| `n_pedidos_previos` | total de pedidos anteriores (agregado) |
| `n_orgaos_distintos_previos` | amplitude: quantos órgãos distintos já acionou |
| `dias_desde_ultimo_pedido` | recência da última interação; `-1` se não houver |
| `prev_reenc_solicitante` | reencaminhamentos anteriores, **numerador** |
| `prev_reenc_neste_orgao` | idem, restrito a este órgão, **numerador** |
| `prev_reenc_solicitante_den` | **denominador maturado** do numerador acima |
| `prev_reenc_neste_orgao_den` | **denominador maturado**, restrito a este órgão |

Os dois denominadores não entram no modelo como variáveis: o serviço usa cada
par numerador/denominador para calcular internamente as razões
`prev_reenc_rate_neste_orgao` e `prev_reenc_rate_solicitante`. Essas duas
**não** devem ser enviadas pelo chamador — eram entradas em versões anteriores
e deixaram de ser quando a maturação de 60 dias passou a valer para o
denominador. O motivo está em [`docs/RESULTADOS.md`](docs/RESULTADOS.md).

Por que o chamador manda numerador e denominador em vez da razão pronta: o
denominador conta só pedidos cujo desfecho já maturou (60 dias). O motivo
completo está em [`docs/RESULTADOS.md`](docs/RESULTADOS.md).

Solicitante anonimizado (`IdSolicitante == '0'`, 16,9% das linhas) não acumula
histórico.

# Campos que o serviço recusa

Catorze campos são **recusados** se você os enviar, porque só existem depois da
triagem — usá-los seria prever o passado. A lista, o teste que reprovou cada um
e os números estão em
[`docs/CAMPOS_POST_HOC.md`](docs/CAMPOS_POST_HOC.md).

Na prática: mande só os campos das tabelas acima. Se enviar um campo recusado,
o serviço devolve erro explicando qual foi — ver
[Erro de vazamento](#erro-de-vazamento) abaixo.

# Saídas

## `POST /score`

| Campo | Tipo | Significado |
|---|---|---|
| `probabilidade_reencaminhamento` | float 0–1 | Saída do LightGBM. **Não é calibrada** — serve para ordenar, não como probabilidade literal |
| `alerta` | `"ALTO RISCO"` \| `"BAIXO RISCO"` | Comparação com `threshold` |
| `threshold` | float | o valor em [`docs/METRICAS.md`](docs/METRICAS.md) — quantil 90 dos escores de validação, ponto de operação da fila de 10%. Reajustado a cada retreinamento |
| `orgao_rate_movel_90d` | float | Taxa do órgão na janela móvel de 90 d, exposta para auditoria |
| `probabilidade_calibrada` | float 0–1 \| `null` | O mesmo escore passado pela regressão isotônica, legível como probabilidade. **Não ordena a fila** — ver a linha seguinte |
| `calibrada_apenas_para_leitura` | bool | Sempre `true`, como lembrete: a calibração cria platôs que empatam casos que o escore cru separa, então quem ordena é sempre `probabilidade_reencaminhamento` |
| `historico_informado` | bool | `false` se o chamador omitiu o histórico do solicitante — o escore está degradado |
| `historico_parcial_ignorado` | bool | `true` quando você mandou **alguns** dos oito campos de histórico e não todos. Nesse caso o serviço descartou o conjunto inteiro: é erro de integração, e não ausência deliberada |
| `orgao_conhecido` | bool | `false` se o órgão não aparece nos anos de treino; nesse caso o escore recai na taxa-base |
| `orgao_rate_historica` | float | Taxa histórica do órgão, exposta para auditabilidade do escore |
| `base_rate_coorte` | float | 0,080268 — taxa-base da coorte de treino, para referência |

## `POST /score_baseline`

| Campo | Tipo | Significado |
|---|---|---|
| `probabilidade_reencaminhamento` | float | A taxa histórica do órgão, sem modelo |
| `alerta` | string | Mesmo limiar |
| `metodo` | string | `"lookup histórico por órgão (sem modelo)"` |
| `orgao_conhecido` | bool | Idem |

## `POST /health`

**É POST, não GET** — todo endpoint do BentoML declarado com `@bentoml.api` é
POST, e um `GET /health` devolve HTTP 405. Para a sonda de liveness use
`GET /healthz`, que é do próprio BentoML:

```bash
curl -sS -X POST http://localhost:3000/health -H 'Content-Type: application/json' -d '{}'
```

Retorna `model_tag`, `n_trees`, `n_features`, `features`, `threshold`,
`queue_fraction`, `caller_supplied_features`, `caller_supplied_default`,
`caller_supplied_rationale`, `embedded_organ_tables`, `contains_personal_data`,
`excluded_leakage_fields`, `train_years` e `data_snapshot`. É a forma de
descobrir o contrato dos oito campos sem ler o código.

## Erro de vazamento

Enviar campo posterior à triagem devolve `ValueError`:

```
post-hoc field(s) supplied, refusing to score: ['FoiProrrogado'].
These are unavailable when a request arrives; see docs/VERIFICATION.md.
```

# Estrutura

| Caminho | Papel |
|---|---|
| [`docs/RESULTADOS.md`](docs/RESULTADOS.md) | O relatório de pesquisa: o resultado, como ele encolheu, e as limitações. Este README é o tutorial; aquele é a avaliação |
| [`docs/LINHA_BASE_COMPLEXIDADE.md`](docs/LINHA_BASE_COMPLEXIDADE.md) | Medida do estado atual do código, para comparar com as versões em Temporian e Featuretools |
| [`docs/METRICAS.md`](docs/METRICAS.md) | **Gerado** por train.py; fonte única de todo número de desempenho |
| [`docs/DECISAO_ESTADO_SOLICITANTE.md`](docs/DECISAO_ESTADO_SOLICITANTE.md) | Onde vive o histórico do solicitante e por quê — decisão de proteção de dados |
| [`docs/auditorias/`](docs/auditorias/INDICE.md) | Auditorias, um arquivo por data: o que outros agentes — e o roteiro mecânico — encontraram neste trabalho |
| [`docs/CAMPOS_POST_HOC.md`](docs/CAMPOS_POST_HOC.md) | O que é campo *post hoc*, por que não serve para treinar, e o protocolo de identificação |
| [`docs/TREINAMENTO.md`](docs/TREINAMENTO.md) | Procedimento de treinamento reproduzível e configuração do LightGBM |
| [`docs/VERIFICATION.md`](docs/VERIFICATION.md) | Auditoria de vazamento e viabilidade — **comece aqui** |
| `scripts/experiment_features_hpo.py` | Ablação de variáveis derivadas e busca rápida de hiperparâmetros |
| [`docs/LITERATURE.md`](docs/LITERATURE.md) | Varredura da literatura e protocolo |
| [`docs/VENUE.md`](docs/VENUE.md) | Plano de publicação, com links auditados |
| `scripts/train.py` | Treinamento com corte temporal: variantes, diagnóstico vazado e linha de base |
| `scripts/verify_leakage_v2.py` | H1 `AssuntoPedido`; H2 duplicatas e junção com Recursos |
| `scripts/verify_h2_final.py`, `verify_h2_baserate.py` | H2, teste direcional |
| `scripts/verify_h3_prazo.py` | H3 vazamento do prazo; H4 disponibilidade demográfica |
| `scripts/lit_scan*.py` | Varredura bibliográfica (OpenAlex/Crossref) |
| `lai_triagem/featurize.py` | Featurização de chegada e barreira de vazamento, compartilhada por treino e serviço |
| `service.py`, `scripts/register_bento.py` | Serviço BentoML e registro do modelo |
| `examples/minimal_predict.py` | Exemplo mínimo de escoragem |
| `scripts/test_metrics.py` | Testa a precisão@k: empates, permutações, casos de borda |
| `scripts/check_docs_numbers.py` | Guarda: nenhum `.md` contradiz as contagens e o limiar do artefato |
| `scripts/check_prosa.py` | Guarda: executa os `curl` do README e compara com a resposta documentada |
| `scripts/check_artefato.py` | Guarda: o modelo entregue é coerente, carrega isolado e não traz dado pessoal |
| `scripts/check_comentarios.py` | Guarda: docstrings e comentários em pt_BR, sem caminho morto |

*Os nomes de arquivos, classes, funções e variáveis permanecem em en_US; a
documentação e os comentários estão em pt_BR.*

## Conferir o repositório antes de confiar nele

Cinco guardas, todas determinísticas e independentes de rede. Rode-as em
sequência; qualquer saída diferente de zero é motivo para não confiar no escore:

```bash
for g in test_metrics check_docs_numbers check_prosa check_artefato check_comentarios; do uv run python scripts/$g.py || echo "FALHOU: $g"; done
```

A mais severa é a `check_prosa.py`: ela **extrai os comandos `curl` deste
próprio README**, executa o payload pelo mesmo caminho de código que o serviço
embrulha, e compara campo a campo com a resposta que o README promete logo
abaixo. Existe porque em 18/09/2026 este README documentava, no seu exemplo
principal, uma classificação de risco **oposta** à que o modelo devolve — e
todas as guardas de então passavam. Ver
[`docs/auditorias/2026-09-18-camadas-0-1.md`](docs/auditorias/2026-09-18-camadas-0-1.md).

# Limitações conhecidas

São sete, e vale ler antes de confiar no escore: desde `Escolaridade` faltar em
76% dos pedidos até as tabelas por órgão envelhecerem sem reajuste. A lista
completa, com números, está em
[`docs/RESULTADOS.md`](docs/RESULTADOS.md#limitações-conhecidas).

# Dados e licença

Fonte: CGU Dados Abertos,
<https://dadosabertos-download.cgu.gov.br/FalaBR/Arquivos_FalaBR/>, retrato
`20260914`. Usa os arquivos **sem texto** (~7–9 MB/ano).

Código sob **GPL-3.0-or-later** (veja [`LICENSE`](LICENSE)). Todas as
dependências são compatíveis: LightGBM e pydantic sob MIT; pandas, NumPy e
scikit-learn sob BSD-3; PyArrow e BentoML sob Apache-2.0.

Nenhum dado é versionado: **git puro**, sem DVC e sem git-lfs.

---

# Glossário

Termos que este README usa e que não são óbvios no primeiro curso de aprendizado
de máquina. Em ordem de aparição, não alfabética.

| Termo | O que é, em uma frase |
|---|---|
| **BentoML** | Ferramenta que embala um modelo treinado e o expõe como serviço HTTP |
| **`uv`** | Gerenciador de pacotes e ambientes virtuais Python; substitui `pip` + `venv` |
| **artefato** | Os arquivos que saem do treinamento e são usados para pontuar: aqui, o modelo em texto mais o JSON acompanhante |
| **taxa-base** | Proporção de positivos no conjunto. Se 5,75% dos pedidos são reencaminhados, a taxa-base é 5,75% — é o que você acertaria chutando ao acaso |
| **coorte** | O conjunto de pedidos usado no estudo (aqui, 2022 a 2026) |
| **precisão@5%** | Dos 5% de pedidos que o modelo considera mais arriscados, que fração realmente foi reencaminhada. É **a** métrica que importa aqui, porque o produto é uma fila com capacidade limitada |
| **PR-AUC** | Resume a qualidade do modelo em todos os tamanhos de fila de uma vez, num número entre 0 e 1. Mais alto é melhor. Com poucos positivos, é mais informativa que a acurácia |
| **ROC-AUC** | Outra medida resumo, entre 0 e 1; 0,5 é o acaso. Tende a parecer boa mesmo quando o modelo é ruim em dados desbalanceados, então aqui é secundária |
| **ganho (de variável)** | Quanto uma variável contribuiu para as decisões da árvore, em % do total. Não é causalidade, é peso no ajuste |
| **ganho / lift (de fila)** | Quantas vezes o modelo é melhor que o acaso naquela fila. "4,3×" significa quatro vezes mais acertos que escolher aleatoriamente |
| **pp** | Pontos percentuais. Sair de 20% para 22% é +2 pp (e não +2%) |
| **limiar** (*threshold*) | O valor de corte acima do qual o pedido é marcado ALTO RISCO |
| **quantil 90** | O valor que 90% dos escores não ultrapassam. Usamos como limiar para que a fila fique com os 10% mais arriscados |
| **calibrado** | Um escore calibrado pode ser lido como probabilidade de verdade ("0,30" ≈ 30% de chance). O escore cru **não** pode: ele só serve para ordenar |
| **regressão isotônica** | A técnica usada aqui para calibrar: aprende uma função que só cresce, mapeando escore cru em probabilidade. Como ela é feita de degraus, vários escores diferentes caem no **mesmo** degrau — um platô — e ficam empatados. É por isso que a fila é ordenada pelo escore cru, e não por ela |
| **codificação de alvo** (*target encoding*) | Trocar uma categoria pela taxa histórica do que se quer prever naquela categoria. É o que `orgao_rate` faz: cada órgão vira sua própria taxa de reencaminhamento |
| **suavização com prior** | Ao calcular a taxa de um órgão com poucos pedidos, misturar com a taxa geral para não confiar em amostra pequena. "Prior 50" = equivale a acrescentar 50 pedidos médios |
| **vazamento** (*data leakage*) | Usar, para prever, uma informação que na hora real da decisão ainda não existiria. Faz o modelo parecer ótimo no teste e falhar em produção |
| ***post hoc*** | Campo preenchido **depois** do momento da decisão. Fonte mais comum de vazamento |
| **censura à direita** | Um pedido recente pode ainda vir a ser reencaminhado; o "não" dele é provisório. Por isso só avaliamos pedidos com pelo menos 60 dias |
| **maturação** | O tempo que se espera antes de confiar no rótulo de um pedido. Aqui, 60 dias |
| **H1 … H6** | As seis hipóteses testadas sobre os dados, cada uma sobre uma variável poder ou não ser usada. O resumo está em [`docs/VERIFICATION.md`](docs/VERIFICATION.md) |
| **featurização** | Transformar os campos brutos nas variáveis que o modelo consome |
| **ablação** | Remover uma variável de propósito para medir o quanto ela valia |
| **HPO** | Busca de hiperparâmetros: testar configurações do algoritmo para ver qual vai melhor |
| **linha de base** (*baseline*) | O jeito mais simples de resolver o problema, contra o qual o modelo tem de provar valor. Aqui: ordenar os pedidos pela taxa histórica do órgão, sem modelo nenhum |
