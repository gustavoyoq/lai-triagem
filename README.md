# lai-triagem

Serviço MLOps para sinalizar o risco de reencaminhamento interno de pedidos de
acesso à informação (LAI) recebidos pelo Fala.BR.

O sistema expõe um modelo LightGBM por meio do BentoML. A resposta classifica o
pedido como `ALTO RISCO` ou `BAIXO RISCO` e fornece informações para auditoria.
O modelo não toma a decisão administrativa: ele prioriza a fila para análise
humana.

> **Limitação importante:** antes de usar o escore em produção, leia a
> [auditoria da entrega](docs/DELIVERY_AUDIT.md). O artefato pré-treinado está
> versionado, mas a reprodução numérica depende do snapshot autorizado dos
> dados da CGU.

## Repositório e tempo esperado

Este é o repositório oficial da entrega:

```bash
git clone https://github.com/gustavoyoq/lai-triagem.git
cd lai-triagem
```

Requer Python ≥ 3.11 e < 3.14. Com `uv` já instalado, uma máquina comum leva
aproximadamente **3 a 10 minutos entre o clone e a primeira predição**, conforme
a velocidade da rede e o cache de dependências. O modelo já vem registrado, por
isso não é necessário treinar antes de servir.

## Instalação

Instale o `uv` caso ainda não o tenha e sincronize o ambiente:

```bash
uv sync --extra dev
```

No Ubuntu/WSL, o LightGBM pode exigir a biblioteca nativa:

```bash
sudo apt-get update && sudo apt-get install -y libgomp1
```

Valide o artefato fornecido:

```bash
make validate
# ou:
uv run lai-validate-artifact --artifact-dir artifacts/registered
```

Saída esperada:

```text
artifact_valid version=provided-2026-09-21 sha256=...
```

## Processo de negócio: antes e depois

### Antes da solução

1. Um pedido chega ao Fala.BR com os campos disponíveis no momento da abertura.
2. O pedido entra na fila de atendimento sem priorização baseada em risco de reencaminhamento.
3. Analistas precisam examinar a fila de forma predominantemente manual.
4. O resultado do atendimento (`FoiReencaminhado`) só aparece depois e não pode
   ser usado como entrada na decisão inicial.

### Depois da solução

1. O Fala.BR envia à API o pedido e, quando autorizado, os oito campos de
   histórico do solicitante.
2. O contrato valida datas, tipos e campos permitidos.
3. O pré-processador transforma a chegada nas mesmas features do treinamento e
   rejeita campos posteriores à triagem para impedir vazamento.
4. O modelo calcula o escore e o serviço devolve o risco, o threshold e sinais
   de auditabilidade, incluindo `model_version`.
5. Analistas seniores usam o resultado para priorizar a fila; a decisão final
   continua humana.
6. Resultados operacionais podem alimentar avaliação, monitoramento e novo ciclo
   de treinamento, sem que dados pessoais sejam gravados na telemetria.

## Subindo a API

Para fazer instalação, validação do artefato e inicialização do servidor em um
único comando:

```bash
make start
```

Esse comando executa `uv sync --extra dev`, valida `artifacts/registered` e só
então inicia o BentoML na porta 3000. O terminal fica ocupado pelo servidor;
use `Ctrl+C` para encerrá-lo.

Se o ambiente já estiver instalado e o artefato validado, também é possível
iniciar somente o servidor:

```bash
make serve
```

O serviço fica em `http://localhost:3000`. Deixe esse terminal aberto. Em outro
terminal, verifique a sonda do servidor:

```bash
curl -sS http://localhost:3000/health
```

A documentação interativa do BentoML fica em
`http://localhost:3000`.

Também é possível executar com Docker:

```bash
docker compose up --build
```

## Primeira predição

Com histórico completo do solicitante:

```bash
make test-1
```

Ou diretamente:

```bash
curl -sS -X POST http://localhost:3000/score \
  -H 'Content-Type: application/json' \
  -d '{"pedido":{"OrgaoDestinatario":"CC-PR – Casa Civil da Presidência da República","DataRegistro":"15/09/2026","Escolaridade":"Ensino Fundamental","UF_sol":"PE","n_pedidos_previos":80,"prev_reenc_solicitante":3,"prev_reenc_solicitante_den":80,"n_pedidos_previos_neste_orgao":12,"prev_reenc_neste_orgao":0,"prev_reenc_neste_orgao_den":12,"n_orgaos_distintos_previos":14,"dias_desde_ultimo_pedido":5}}'
```

Resposta representativa:

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
  "historico_parcial_ignorado": false,
  "model_version": "provided-2026-09-21"
}
```

Sem o histórico, são necessários apenas `OrgaoDestinatario` e `DataRegistro`:

```bash
curl -sS -X POST http://localhost:3000/score \
  -H 'Content-Type: application/json' \
  -d '{"pedido":{"OrgaoDestinatario":"CC-PR – Casa Civil da Presidência da República","DataRegistro":"15/09/2026"}}'
```

Nesse caso, a resposta informa `historico_informado: false`. Se apenas parte dos
oito campos for enviada, o conjunto inteiro é ignorado e
`historico_parcial_ignorado` será `true`.

### Caso 2: órgão conhecido com baixo risco

Este caso usa um órgão conhecido com baixa taxa histórica:

```bash
make test-2
```

O resultado esperado é aproximadamente:

```json
{
  "probabilidade_reencaminhamento": 0.00371,
  "alerta": "BAIXO RISCO",
  "orgao_conhecido": true,
  "orgao_rate_historica": 0.003393,
  "historico_informado": false,
  "model_version": "provided-2026-09-21"
}
```

### Caso 3: órgão conhecido com alto risco, sem histórico

Este caso mostra uma probabilidade acima de 0,3 mesmo sem enviar os oito campos
de histórico:

```bash
make test-3
```

O resultado esperado é aproximadamente:

```json
{
  "probabilidade_reencaminhamento": 0.354094,
  "alerta": "ALTO RISCO",
  "threshold": 0.159391,
  "orgao_conhecido": true,
  "historico_informado": false,
  "model_version": "provided-2026-09-21"
}
```

O endpoint `score_baseline` permite comparar o modelo com uma consulta simples à
taxa histórica do órgão:

```bash
curl -sS -X POST http://localhost:3000/score_baseline \
  -H 'Content-Type: application/json' \
  -d '{"pedido":{"OrgaoDestinatario":"CC-PR – Casa Civil da Presidência da República","DataRegistro":"15/09/2026"}}'
```

Sua resposta inclui, por exemplo:

```json
{
  "probabilidade_reencaminhamento": 0.478643,
  "alerta": "ALTO RISCO",
  "metodo": "lookup histórico por órgão (sem modelo)",
  "orgao_conhecido": true,
  "model_version": "provided-2026-09-21"
}
```

## Contrato de entrada

Obrigatórios:

- `OrgaoDestinatario`
- `DataRegistro`, no formato `DD/MM/AAAA`

Os demais campos do pedido são opcionais. O histórico do solicitante é formado
por oito campos e é atômico: ou os oito são enviados ou todos são tratados como
ausentes. Esses dados não ficam embarcados no artefato; são fornecidos pelo
chamador no momento da predição.

O serviço recusa campos que só existem depois da triagem, como
`FoiReencaminhado`, `DataResposta`, `Decisao` e `PrazoAtendimento`. Essa barreira
evita data leakage.

## Endpoints

- `POST /score`: previsão com o modelo LightGBM.
- `POST /score_baseline`: taxa histórica do órgão, sem modelo.
- `POST /health`: informações de saúde e integridade do artefato.
- `POST /metadata`: contrato e metadados do modelo.
- `GET /healthz`: sonda de liveness fornecida pelo BentoML.

## Estrutura principal

```text
src/mlops_lai/
├── service.py                 # API BentoML e adaptador do serviço
├── domain/schemas.py          # contratos Pydantic de entrada e saída
├── features/preprocessor.py   # validação e transformação das features
├── model/loader.py            # carregamento, hashes e execução do modelo
├── core/settings.py           # configuração por ambiente
├── core/errors.py             # erros de contrato e artefato
├── pipelines/                 # preparação, treino, avaliação e promoção
└── monitoring/                # telemetria e detecção de drift
artifacts/registered/           # modelo e metadados usados pela API
tests/                          # testes automatizados
docs/                           # auditorias e evidências da entrega
```

O modelo registrado é composto por `model_arrival.txt`,
`preprocessor.json` e `manifest.json`. O manifesto contém os hashes SHA-256
usados para verificar integridade no startup.

## Desenvolvimento e validação

```bash
make lint
make test
make validate
```

Os comandos de pipeline disponíveis são:

```bash
uv run lai-prepare
uv run lai-train
uv run lai-evaluate
uv run lai-validate-artifact
uv run lai-promote --target <diretorio-de-destino>
uv run lai-drift-report
```

O treinamento completo depende dos dados autorizados da CGU e não é necessário
para executar o artefato fornecido.

## Uso de IA

Utilizamos o **OpenAI Codex** como apoio durante o desenvolvimento e a revisão
da entrega. A ferramenta foi usada para:

- Identificar e corrigir eventuais bugs e erros no código, nos comandos e na
  documentação;
- Comparar alternativas de implementação e de organização do projeto;
- Validar os requisitos da atividade, incluindo contrato da API, exemplos de
  uso, dependências, documentação, tag de entrega e evidências de execução.

As sugestões da IA foram conferidas pela equipe no código, nos testes, no
artefato registrado e na execução da API. A ferramenta não foi considerada
fonte única de verdade: as decisões finais e a validação do funcionamento
permaneceram sob responsabilidade da equipe.

## Documentação da entrega

- [Auditoria da entrega](docs/DELIVERY_AUDIT.md)
- [Evidência de execução local](docs/EXECUTION_EVIDENCE.md)

## Dados e licença

Os dados de treinamento vêm do portal de Dados Abertos da CGU. Dados brutos e
dados pessoais não são versionados neste repositório.

O código está sob [GPL-3.0-or-later](LICENSE).
