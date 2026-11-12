# lai-triagem

Serviço MLOps para sinalizar o risco de reencaminhamento interno de pedidos de
acesso à informação (LAI) recebidos pelo Fala.BR.

O sistema expõe um modelo LightGBM por meio do BentoML. A resposta classifica o
pedido como `ALTO RISCO` ou `BAIXO RISCO` e fornece informações para auditoria.
O modelo não toma a decisão administrativa: ele prioriza a fila para análise
humana.

O artefato pré-treinado está versionado. Sua reprodução numérica depende do
snapshot autorizado dos dados da CGU.

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
   de treinamento. A telemetria registra as variáveis selecionadas do contrato,
   features e previsões para acompanhar o comportamento do serviço.

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
curl -sS http://localhost:3000/healthz
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
- `GET /metrics`: métricas HTTP e de inferência no formato Prometheus.

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
outputs/monitoramento/          # planilha de apoio ao monitoramento
docs/APRESENTACAO.md            # apresentação com tabelas e resultados
```

O modelo registrado é composto por `model_arrival.txt`,
`preprocessor.json` e `manifest.json`. O manifesto contém os hashes SHA-256
usados para verificar integridade no startup.

## Logs, carga e métricas

A telemetria vem habilitada. Cada processo grava um arquivo append-only UTF-8
`monitoring/telemetry/scoring.<execucao>.<pid>.jsonl`. Cada linha correlaciona
entrada, 31 features efetivamente usadas, previsão, resposta, qualidade da entrada,
latência e status HTTP. `/score_baseline` também é registrado. Rejeições de
contrato e de JSON deixam um evento com motivo padronizado.

As tabelas de métricas, os resultados da carga e a interpretação dos cálculos
estão no [documento de apresentação](docs/APRESENTACAO.md).


Com o servidor aberto, gere uma carga reproduzível de 120 chamadas em outro terminal.
O comando inclui 60 sucessos e 60 rejeições esperadas, distribuídos em 12 cenários.

```bash
uv run python scripts/generate_load.py --requests 120 --concurrency 4
```

No Bash, acompanhe o arquivo de um dos processos após iniciar a API.

```bash
tail -f "$(ls -t monitoring/telemetry/scoring.*.jsonl | head -n 1)"
```

No PowerShell, o comando equivalente é

```powershell
$log = Get-ChildItem monitoring/telemetry/scoring.*.jsonl |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-Content -LiteralPath $log.FullName -Encoding utf8 -Wait
```

Confira os contadores e os buckets antes e depois da carga.

```bash
curl -sS http://localhost:3000/metrics
```

No PowerShell, use `curl.exe -sS http://localhost:3000/metrics`.


Gere uma análise exploratória usando as primeiras 20 inferências `/score`
bem-sucedidas como referência e as restantes como janela atual.

```bash
uv run lai-drift-report --telemetry monitoring/telemetry --reference-rows 20
```

O comando salva `reports/drift.json` com identificação da referência, KS D, PSI,
histogramas e quartis das variáveis numéricas disponíveis. Também aceita um
arquivo de referência fixo por `--reference caminho/referencia.jsonl`.
As amostras são filtradas por endpoint, sucesso e versão do modelo.


Para subir a API e o Prometheus com volumes persistentes e coleta a cada 15 segundos,
execute

```bash
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up --build
```

O Prometheus fica em `http://localhost:9090`. Sua retenção está configurada em
15 dias. Os logs permanecem no volume `telemetry-data` até remoção explícita.
Para ler os arquivos dentro do container, use

```bash
docker compose exec api python -c "from pathlib import Path; print('\n'.join(p.read_text(encoding='utf-8') for p in Path('/app/monitoring/telemetry').glob('*.jsonl')))"
```

As variáveis de configuração estão em `.env.example`, incluindo a chave
`MLOPS_ENABLE_TELEMETRY` e o prazo `MLOPS_REQUEST_TIMEOUT_SECONDS`, de 20 segundos.

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

## Dados e licença

Os dados de treinamento vêm do portal de Dados Abertos da CGU. Dados brutos e
dados pessoais não são versionados neste repositório.

O código está sob [GPL-3.0-or-later](LICENSE).
