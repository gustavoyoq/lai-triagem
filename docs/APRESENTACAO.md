# Logs e métricas do serviço de inferência

## Registro em arquivo

As chamadas a `POST /score` e `POST /score_baseline` (além dos endpoints de saúde
e metadados quando acessados via HTTP) geram registros JSONL UTF-8 no disco do
processo. Cada linha reúne os dados da entrada, a resposta e o contexto da
chamada. Rejeições de validação, JSON malformado, timeouts e erros internos
também são registrados.

O caminho padrão é
`monitoring/telemetry/scoring.<execucao>.<pid>.jsonl`. Cada processo possui seu
arquivo. O gravador (`AppendHandler`) herda de `logging.FileHandler` em modo
append (`mode="a"`, `encoding="utf-8"`), com flush por evento. Novas chamadas
acrescentam linhas e preservam os bytes já gravados.

Os campos `started_at_utc` e `timestamp_utc` indicam início e conclusão em UTC,
no formato padrão de data/hora do `logging` (`YYYY-MM-DD HH:MM:SS,mmm`),
conforme `%(asctime)s` e `logging.Formatter.formatTime` com `time.gmtime`.
Snapshots gerados anteriormente à adoção de `formatTime` (como a carga
sintética de 11–12/11/2026 na planilha de apoio e amostras antigas em disco)
usam ISO 8601 UTC (`...Z` ou `+00:00`). Como ambos representam UTC em ordem
lexicográfica, não se deve misturar os dois formatos na mesma janela cronológica
de comparação. `request_id` correlaciona a chamada com a resposta e também é
devolvido no cabeçalho HTTP `X-Request-ID`.

### Análise e especificação da formatação do arquivo

A formatação do log em **JSON Lines (JSONL)**, com um arquivo por processo,
atende aos requisitos de integridade, concorrência e análise posterior pelas
seguintes razões técnicas:

- **Estrutura variável sem perda de campos:** uma requisição `200` em `/score`
  produz `features` (31 colunas) e `response`, enquanto `/score_baseline` tem
  `features=null` e uma rejeição `400` traz `error.validation` com `response` de
  erro. Um formato tabular rígido (como CSV) exigiria dezenas de colunas vazias
  ou serialização aninhada manual.
- **Append seguro e streaming em tempo real:** cada linha termina em `\n`,
  permitindo leitura contínua (`tail -f` / `Get-Content -Wait`) e parseamento
  incremental linha a linha sem reescrever o arquivo.
- **JSON estrito (`RFC 8259`):** `json_safe` converte escalares NumPy para tipos
  nativos e transforma `NaN`/`Inf` em `null` (`allow_nan=False`,
  `ensure_ascii=False`), evitando quebras em leitores JSON padrão.
- **Isolamento por processo:** gravar `scoring.<instance_id>.<pid>.jsonl`
  evita entrelaçamento de bytes entre múltiplos workers/processos e preserva os
  arquivos de execuções anteriores em reinícios. Na leitura, `load_events()` em
  `mlops_lai.monitoring.drift` agrega todos os `*.jsonl` do diretório e ordena
  os eventos por `(timestamp_utc, request_id)`.

#### Esquema do registro (`schema_version: 2`)

| Campo | Tipo | Obrigatório / Condição | Descrição e regras de formatação |
| --- | --- | --- | --- |
| `schema_version` | `int` | Sempre (`2`) | Versão do contrato de telemetria (`1` nos logs legados). |
| `event_type` | `str` | Sempre | `"inference_completed"` em `/score` e `/score_baseline`; `"http_completed"` nos demais endpoints. |
| `request_id` | `str` (UUID) | Sempre | Identificador único da chamada; espelhado no cabeçalho `X-Request-ID`. |
| `instance_id` | `str` (hex) | Sempre | Identificador da instância do `TelemetryRecorder`. |
| `process_id` | `int` | Sempre | PID do processo gravador. |
| `started_at_utc` | `str` | Sempre | Carimbo UTC de início da operação. |
| `timestamp_utc` | `str` | Sempre | Carimbo UTC de conclusão, preenchido na emissão pelo `JsonFormatter`. |
| `endpoint` | `str` | Sempre | Rota observada (`/score`, `/score_baseline`, `/health`, `/metadata`, `/healthz`, `/readyz`, `/livez` ou `"unmatched"`). |
| `method` | `str \| null` | Sempre | Método HTTP (`"POST"`, `"GET"`, etc.) ou `null` quando `transport="python"`. |
| `transport` | `str` | Sempre | `"http"` via servidor ASGI/BentoML ou `"python"` em chamadas diretas a `ScoringHandler`. |
| `http_status` | `int \| null` | Sempre | Código HTTP efetivamente enviado (`200`, `400`, `500`, `504`), ou `null` em cancelamento/chamada Python. |
| `outcome` | `str` | Sempre | `"success"`, `"validation_error"`, `"server_error"` ou `"cancelled"`. |
| `latency_ms` | `float` | Sempre | Duração total da requisição/operação em milissegundos (monotonic clock). |
| `inference_ms` | `float \| null` | `/score` | Duração de `model.score` em milissegundos (`null` em `/score_baseline` e rejeições antes do handler). |
| `model_version` | `str \| null` | Sempre | Versão do artefato carregado (`"provided-2026-09-21"`). |
| `artifact_sha256` | `str \| null` | Sempre | Hash SHA-256 de `model_arrival.txt`. |
| `categorical_features` | `list[str]` | Sempre | Lista das 14 features categóricas do modelo (usada para excluí-las de análises numéricas). |
| `input` | `dict` | Sempre | Campos escalares recebidos de `REQUEST_FIELDS - {"DataNascimento"}` com whitespace aparado (`{}` em JSON inválido). |
| `features` | `dict \| null` | `/score` 200 | As 31 features numéricas/codificadas efetivamente entregues ao booster (`null` em baseline e erros). |
| `quality` | `dict` | Sempre | Indicadores de qualidade: `orgao_conhecido`, `historico_informado`, `historico_parcial_ignorado`, `history_fields_received`, `history_field_count`, `missing_fields`, `unknown_categories`, `rejected_fields`, `leakage_fields`, `birth_date_supplied`, `age_state`, `default_features`, `missing_features`, `unknown_organ`, `history_absent`, `history_partial`. |
| `response` | `dict \| null` | Sempre | Corpo devolvido em `200`, `{"error": ..., "http_status": ...}` em `>=400`, ou `null` em cancelamento/exceção Python. |
| `prediction_raw` | `float \| null` | Sucesso | Escore bruto do LightGBM antes do arredondamento (ou taxa do órgão no baseline). |
| `error` | `dict \| null` | Erros | `{"kind", "stage", "fields"}` e opcionalmente `"validation"` (`type` e `loc` do Pydantic, sem copiar o valor rejeitado). |
| `score_bin` | `int` | `/score` 200 | Faixa inteira `0..9` (`min(int(score * 10), 9)`) para compatibilidade com PSI legado. |
| `organ_known` | `bool` | `/score` 200 | Espelho booleano de `response.orgao_conhecido`. |
| `history_supplied` | `bool` | `/score` 200 | Espelho booleano de `response.historico_informado`. |
| `request_body_truncated` | `bool` | Opcional | Presente em HTTP de inferência; `true` se o corpo ultrapassar `65.536` bytes. |

Exemplos representativos de linhas no arquivo `.jsonl` (campos longos abreviados com `...` apenas para leitura):

```json
{"schema_version": 2, "event_type": "inference_completed", "request_id": "86735fc2-d02d-4250-add3-8dc2e023a16b", "instance_id": "...", "process_id": 1234, "started_at_utc": "2026-11-13 19:09:07,432", "timestamp_utc": "2026-11-13 19:09:07,436", "endpoint": "/score", "method": "POST", "transport": "http", "http_status": 200, "outcome": "success", "latency_ms": 8.7758, "inference_ms": 5.1204, "model_version": "provided-2026-09-21", "artifact_sha256": "595b1773...", "categorical_features": ["Esfera", "..."], "input": {"OrgaoDestinatario": "CC-PR – Casa Civil da Presidência da República", "DataRegistro": "15/09/2026", "n_pedidos_previos": 80, "...": "..."}, "features": {"OrgaoDestinatario": 67.0, "reg_month": 9.0, "idade": 36.7, "orgao_rate": 0.4786427775662279, "...": "..."}, "quality": {"orgao_conhecido": true, "historico_informado": true, "unknown_categories": [], "age_state": "valid", "default_features": ["Esfera", "..."], "missing_features": []}, "response": {"probabilidade_reencaminhamento": 0.384964, "alerta": "ALTO RISCO", "threshold": 0.159391, "...": "..."}, "prediction_raw": 0.38496391, "error": null, "score_bin": 3, "organ_known": true, "history_supplied": true}
{"schema_version": 2, "event_type": "inference_completed", "request_id": "d5ac8cb0-e060-4b92-bb5e-b87dae8a7a26", "instance_id": "...", "process_id": 1234, "started_at_utc": "2026-11-13 19:09:08,101", "timestamp_utc": "2026-11-13 19:09:08,105", "endpoint": "/score", "method": "POST", "transport": "http", "http_status": 400, "outcome": "validation_error", "latency_ms": 4.7164, "inference_ms": null, "model_version": "provided-2026-09-21", "artifact_sha256": "595b1773...", "categorical_features": ["Esfera", "..."], "input": {"OrgaoDestinatario": "CC-PR – Casa Civil da Presidência da República", "DataRegistro": "15/09/2026", "n_pedidos_previos": "inválido"}, "features": null, "quality": {"orgao_conhecido": true, "historico_informado": false, "historico_parcial_ignorado": true, "history_partial": true, "history_absent": true, "age_state": "not_processed"}, "response": {"error": {"kind": "http_validation", "stage": "http_validation", "fields": ["n_pedidos_previos"], "validation": [{"type": "float_parsing", "loc": ["body", "pedido", "n_pedidos_previos"]}]}, "http_status": 400}, "prediction_raw": null, "error": {"kind": "http_validation", "stage": "http_validation", "fields": ["n_pedidos_previos"], "validation": [{"type": "float_parsing", "loc": ["body", "pedido", "n_pedidos_previos"]}]}}
```

### Variáveis registradas e sua relevância

| Grupo | Dados registrados | Análise possibilitada |
| --- | --- | --- |
| Rastro e proveniência | ID da chamada, execução, processo, endpoint, método, status HTTP, versão e hash do modelo | Relacionar entrada e saída e comparar o mesmo artefato em períodos distintos. |
| Tempo e erros | Timestamps, `latency_ms`, `inference_ms`, tipo de erro, etapa e campos envolvidos | Histogramas e boxplots de latência e taxa de erros por endpoint. |
| Órgão e localização | `OrgaoDestinatario`, `Esfera`, `UF`, `Municipio`, `Pais`, `UF_sol`, `Municipio_sol` e `orgao_conhecido` | Mudanças na composição institucional e geográfica e frequência de órgãos desconhecidos. |
| Perfil e canal | `FormaResposta`, `OrigemSolicitacao`, `TipoDemandante`, `TipoPessoaJuridica`, `Genero`, `Escolaridade` e `Profissao` | Mudanças nas categorias de entrada utilizadas pelo modelo. |
| Volume e recência do histórico | `n_pedidos_previos`, `n_pedidos_previos_neste_orgao`, `n_orgaos_distintos_previos` e `dias_desde_ultimo_pedido` | Distribuições do volume, diversidade e recência dos pedidos. |
| Reencaminhamentos anteriores | `prev_reenc_solicitante`, `prev_reenc_solicitante_den`, `prev_reenc_neste_orgao` e `prev_reenc_neste_orgao_den` | Distribuição dos reencaminhamentos e dos denominadores usados nas taxas. |
| Features derivadas | Idade, mês, dia e dia da semana, `uf_match`, idade do órgão, taxas do solicitante e taxas do órgão históricas e de 90/365 dias | Comparações das variáveis efetivamente utilizadas na inferência. |
| Qualidade da entrada | Histórico informado ou parcial, campos ausentes, categorias desconhecidas, defaults e estado da idade | Identificação de mudanças na cobertura dos dados e no uso de valores substitutos. |
| Saída | Escore original, probabilidade devolvida e calibrada, alerta, threshold e resposta do endpoint | Histogramas e boxplots dos escores e proporção de alto risco. |

A seleção acompanha as entradas usadas na triagem e os resultados devolvidos pelo
serviço. O histórico e as taxas descrevem o comportamento anterior relacionado ao
reencaminhamento. Os indicadores de qualidade mostram quando informações relevantes
chegam incompletas ou fora do vocabulário conhecido.

O objeto `input` guarda os campos selecionados recebidos. `features` guarda as 31
features efetivamente entregues ao booster. O escore anterior ao arredondamento
fica em `prediction_raw`, e a resposta de sucesso fica em `response`. No baseline,
a resposta e a taxa consultada são registradas com `features=null`.

Zero permanece um valor observado. Ausentes e valores não finitos são gravados
como `null`. O histórico parcial conserva os valores recebidos em `input`, enquanto
`quality` indica seu descarte e `features` mostra a sentinela aplicada pelo
pré-processador. No artefato registrado, a sentinela do histórico é `-1`.

Em rejeições, a resposta é representada pelo erro padronizado e pelo status emitido.
Os detalhes de validação conservam tipo e localização dos campos. As datas de
nascimento são representadas pela idade derivada e por indicadores de presença.

### Verificação de suficiência dos dados coletados

A verificação de suficiência deve responder a três perguntas: se os **campos**
atendem às análises planejadas, se os **valores** evitam distorções por
sentinelas/códigos e se o **volume amostral** é suficiente.

| Análise desejada | Suficiência dos dados coletados | Condição / Limitação identificada |
| --- | --- | --- |
| Taxa de erros por endpoint e causa | **Suficiente** | Usar `endpoint`, `http_status`, `outcome` e `error.kind`; excluir `status_class="cancelled"` (`http_status=null`) do denominador da taxa de erro HTTP. |
| Histograma e boxplot de latência por endpoint | **Suficiente** | `latency_ms` é registrado em 100% dos eventos HTTP (sucesso e erro); `inference_ms` cobre chamadas `/score` que atingem o handler. |
| Histograma, boxplot, KS e PSI de entradas e saídas | **Suficiente com filtragem** | Filtrar `endpoint == "/score"`, `outcome == "success"` e mesma `model_version`; excluir `categorical_features` das estatísticas numéricas e excluir `quality.default_features` para não misturar a sentinela `-1` com observações reais (`drift.py` já aplica esse filtro). |
| Cobertura de `DataNascimento` sem vazar PII | **Suficiente para drift** | A data bruta é omitida de `input`; a distribuição etária é preservada em `features.idade` e a taxa de preenchimento/validade em `quality.birth_date_supplied` e `quality.age_state`. |
| Desempenho supervisionado em produção (AUC, recall, `precision@k`) | **Insuficiente isoladamente** | O desfecho real (`FoiReencaminhado`) não existe na chegada e é bloqueado por `LEAKAGE_FIELDS`. Requer cruzamento offline posterior via `request_id` com o retorno do Fala.BR. |

#### Verificação do tamanho de amostra (`min_samples = 20`)

O analisador `lai-drift-report` (`mlops_lai.monitoring.drift.report`) exige por
padrão `min_samples = 20` observações válidas tanto na referência quanto na
janela atual para classificar KS e PSI como conclusivos (`"measured"` / `"ok"` ou
`"alert"`); abaixo disso, marca `"inconclusive"`.

Na carga sintética padrão de **120 chamadas** (`scripts/generate_load.py`):

- **110** vão para `/score` e **10** vão para `/score_baseline`.
- Em `/score`, há **50** sucessos `200` e **60** rejeições `400`. Para escores
  (`prediction_raw`, `response.probabilidade_reencaminhamento`), `latency_ms` e
  campos obrigatórios (`OrgaoDestinatario`, `reg_month`, `orgao_rate`), uma
  divisão de 20 referência + 30 atual atinge `min_samples = 20`.
- Porém, dos 50 sucessos `/score`, apenas **10** correspondem ao cenário
  `history` (que envia os 8 campos de histórico, `UF`, `UF_sol`, `Escolaridade`,
  `DataNascimento` e `Municipio_sol`). Logo, uma única execução de 120 chamadas
  é **insuficiente** para rodar KS/PSI conclusivos sobre variáveis opcionais de
  histórico e idade (`n < 20`), que permanecem `"inconclusive"`. Para validar
  drift dessas variáveis opcionais em ambiente de teste, executar carga maior
  (por exemplo, `--requests 480`, gerando 40 chamadas `history`: 20 de
  referência e 20 na janela atual).

Quando os dados de treinamento estiverem indisponíveis, estabelecer uma referência
manual fixa e documentar a seleção, o período, o tamanho e a versão do modelo.
Usar observações independentes para referência e janela atual. Os timestamps
permitem selecionar os períodos posteriormente.

## Geração de carga e acompanhamento do rastro

Com a API iniciada conforme o README, executar na raiz do repositório:

```bash
uv run python scripts/generate_load.py --requests 120 --concurrency 4
```

O script distribui os 120 pedidos entre 12 cenários determinísticos: 60 sucessos
em `POST /score` e `POST /score_baseline` (alto risco, baixo risco, órgão
desconhecido e histórico completo, ausente e parcial) e 60 rejeições (data
inválida, tipo inválido, campo obrigatório ausente, campo extra, campo de
leakage e JSON inválido). Como `OrgaoDestinatario` integra
`categorical_features`, o cenário `unknown` com órgão fora do vocabulário de
treino aciona simultaneamente `unknown_organ` e `unknown_categories`
(`["OrgaoDestinatario"]`) no objeto `quality` do JSONL e no contador
`lai_input_quality_events_total`.

No Bash, acompanhar o arquivo de um dos processos com:

```bash
tail -f "$(ls -t monitoring/telemetry/scoring.*.jsonl | head -n 1)"
```

No PowerShell, usar:

```powershell
$log = Get-ChildItem monitoring/telemetry/scoring.*.jsonl |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-Content -LiteralPath $log.FullName -Encoding utf8 -Wait
```

## O que é exibido por `GET /metrics` e como analisar

O BentoML expõe `GET /metrics` em formato de exposição texto do Prometheus
(`text/plain; version=0.0.4`). Consultar com `curl -sS http://localhost:3000/metrics`
ou `curl.exe -sS http://localhost:3000/metrics` no PowerShell.

Como o gravador registra as métricas no registry padrão compartilhado com o
servidor BentoML (e com `PROMETHEUS_MULTIPROC_DIR` quando há múltiplos workers),
a resposta de `GET /metrics` exibe:

1. **As 9 métricas customizadas `lai_*`** descritas na tabela abaixo.
2. **Métricas nativas do BentoML e do runtime Python/processo** (como
   `bentoml_api_server_request_duration_seconds_*`, `python_gc_*` e `process_*`).

Trecho representativo exibido por `GET /metrics` após a carga sintética de 120 chamadas:

```text
# HELP lai_http_requests_total Respostas HTTP concluídas
# TYPE lai_http_requests_total counter
lai_http_requests_total{endpoint="/score",method="POST",status_class="2xx"} 50.0
lai_http_requests_total{endpoint="/score",method="POST",status_class="4xx"} 60.0
lai_http_requests_total{endpoint="/score_baseline",method="POST",status_class="2xx"} 10.0
# HELP lai_http_request_duration_seconds Duração HTTP no servidor até o último envio de corpo
# TYPE lai_http_request_duration_seconds histogram
lai_http_request_duration_seconds_bucket{endpoint="/score",le="0.005",method="POST"} 22.0
lai_http_request_duration_seconds_bucket{endpoint="/score",le="0.01",method="POST"} 75.0
lai_http_request_duration_seconds_bucket{endpoint="/score",le="0.025",method="POST"} 101.0
lai_http_request_duration_seconds_bucket{endpoint="/score",le="0.05",method="POST"} 109.0
lai_http_request_duration_seconds_bucket{endpoint="/score",le="0.1",method="POST"} 110.0
lai_http_request_duration_seconds_bucket{endpoint="/score",le="+Inf",method="POST"} 110.0
lai_http_request_duration_seconds_count{endpoint="/score",method="POST"} 110.0
lai_http_request_duration_seconds_sum{endpoint="/score",method="POST"} 1.215099
lai_http_request_duration_seconds_bucket{endpoint="/score_baseline",le="0.005",method="POST"} 2.0
lai_http_request_duration_seconds_bucket{endpoint="/score_baseline",le="0.01",method="POST"} 7.0
lai_http_request_duration_seconds_bucket{endpoint="/score_baseline",le="0.025",method="POST"} 10.0
lai_http_request_duration_seconds_bucket{endpoint="/score_baseline",le="+Inf",method="POST"} 10.0
lai_http_request_duration_seconds_count{endpoint="/score_baseline",method="POST"} 10.0
lai_http_request_duration_seconds_sum{endpoint="/score_baseline",method="POST"} 0.078900
# HELP lai_score_requests_total Inferências /score concluídas
# TYPE lai_score_requests_total counter
lai_score_requests_total{known_organ="true",model_version="provided-2026-09-21"} 40.0
lai_score_requests_total{known_organ="false",model_version="provided-2026-09-21"} 10.0
# HELP lai_score_failures_total Chamadas de inferência rejeitadas
# TYPE lai_score_failures_total counter
lai_score_failures_total{kind="http_validation"} 30.0
lai_score_failures_total{kind="invalid_json"} 10.0
lai_score_failures_total{kind="unknown_fields"} 10.0
lai_score_failures_total{kind="leakage_fields"} 10.0
# HELP lai_risk_predictions_total Classificação de risco devolvida
# TYPE lai_risk_predictions_total counter
lai_risk_predictions_total{endpoint="/score",model_version="provided-2026-09-21",risk="ALTO RISCO"} 30.0
lai_risk_predictions_total{endpoint="/score",model_version="provided-2026-09-21",risk="BAIXO RISCO"} 20.0
lai_risk_predictions_total{endpoint="/score_baseline",model_version="provided-2026-09-21",risk="ALTO RISCO"} 10.0
# HELP lai_prediction_score Escores devolvidos por inferências concluídas
# TYPE lai_prediction_score histogram
lai_prediction_score_bucket{endpoint="/score",le="0.1",model_version="provided-2026-09-21"} 10.0
lai_prediction_score_bucket{endpoint="/score",le="0.2",model_version="provided-2026-09-21"} 20.0
lai_prediction_score_bucket{endpoint="/score",le="0.4",model_version="provided-2026-09-21"} 50.0
lai_prediction_score_bucket{endpoint="/score",le="+Inf",model_version="provided-2026-09-21"} 50.0
lai_prediction_score_count{endpoint="/score",model_version="provided-2026-09-21"} 50.0
lai_prediction_score_sum{endpoint="/score",model_version="provided-2026-09-21"} 12.426760
# HELP lai_telemetry_write_failures_total Falhas de gravação JSONL
# TYPE lai_telemetry_write_failures_total counter
lai_telemetry_write_failures_total 0.0
```

| Métrica | Tipo | Labels (cardinalidade limitada) | Buckets / Unidade e como analisar |
| --- | --- | --- | --- |
| `lai_http_requests_total` | Counter | `endpoint`, `method`, `status_class` (`2xx`, `4xx`, `5xx`, `cancelled`) | Volume HTTP por rota e classe de status; base da taxa de erros por endpoint. |
| `lai_http_request_duration_seconds` | Histogram | `endpoint`, `method` | 12 limites (`0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20`) + `+Inf` em segundos. Permite histograma, média (`Δ_sum / Δ_count`) e estimativa de quantis por endpoint. |
| `lai_score_requests_total` | Counter | `model_version`, `known_organ` (`true`, `false`) | Total de inferências `/score` bem-sucedidas por versão e cobertura de órgão. |
| `lai_score_failures_total` | Counter | `kind` (`invalid_json`, `http_validation`, `unknown_fields`, `leakage_fields`, `timeout`, `cancelled`, `model_error`, `server_error`) | Contabilizado uma vez por chamada de inferência não bem-sucedida; explica a causa das falhas. |
| `lai_score_latency_seconds` | Histogram | *(sem labels)* | Buckets padrão Prometheus (`0.005` a `10.0` + `+Inf`) em segundos; mede somente `model.score` (pré-processamento, booster e calibração). |
| `lai_prediction_score` | Histogram | `endpoint`, `model_version` | 11 limites (`0.0` a `1.0`, passo `0.1`) + `+Inf`; monitora deslocamento da distribuição de escores devolvidos. |
| `lai_risk_predictions_total` | Counter | `endpoint`, `model_version`, `risk` (`ALTO RISCO`, `BAIXO RISCO`) | Proporção de alertas emitidos acima e abaixo do threshold operacional (`0.159391`). |
| `lai_input_quality_events_total` | Counter | `kind` (`unknown_organ`, `history_absent`, `history_partial`, `unknown_categories`) | Chamadas com degradação ou ausência de entrada; uma mesma chamada pode incrementar mais de um `kind`. |
| `lai_telemetry_write_failures_total` | Counter | *(sem labels)* | Falhas de gravação JSONL no disco durante atendimento; deve permanecer em `0`. |

Os contadores e histogramas em `/metrics` acumulam valores desde o início do
processo. Para analisar uma janela sem servidor Prometheus, coletar dois
snapshots (`Antes` e `Depois`, sem reinício entre eles) e calcular o incremento
`Δ = Depois - Antes` em cada série. Em PromQL, aplicar `increase()` ou `rate()`
sobre cada série antes de usar `sum by (...)` para lidar com reinícios
(resets) e múltiplos workers. A coleta própria exclui `GET /metrics` para que os
scrapes não inflam o volume avaliado.

### Histograma da latência dos endpoints

A métrica `lai_http_request_duration_seconds` expõe buckets cumulativos
(`_bucket{le=...}`), `_count` e `_sum` separados por `endpoint` e `method`. Cada
bucket conta as observações com duração menor ou igual ao limite superior `le`.

Para calcular o histograma de uma janela para um endpoint:

1. Calcular o incremento cumulativo de cada bucket: `Δ_k = Depois_k - Antes_k`.
2. Calcular a contagem exata de cada faixa subtraindo o bucket anterior:
   `Faixa_1 = Δ_1` e `Faixa_k = Δ_k - Δ_{k-1}`.
3. Conferir integridade: `Δ_bucket(+Inf) == Δ_count == soma(Faixa_k)`.
4. Calcular a latência média em milissegundos na janela: `(Δ_sum / Δ_count) × 1000`.

Resultados obtidos na carga sintética de 120 chamadas **por endpoint** (os
buckets acima de `0,1 s` tiveram contagem zero em ambos os endpoints):

| Faixa (s) | `/score` acum. | `/score` na faixa | `/score_baseline` acum. | `/score_baseline` na faixa | Total acum. | Total na faixa |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `0 a 0,005` | 22 | 22 (20,0%) | 2 | 2 (20,0%) | 24 | 24 (20,0%) |
| `0,005 a 0,01` | 75 | 53 (48,2%) | 7 | 5 (50,0%) | 82 | 58 (48,3%) |
| `0,01 a 0,025` | 101 | 26 (23,6%) | 10 | 3 (30,0%) | 111 | 29 (24,2%) |
| `0,025 a 0,05` | 109 | 8 (7,3%) | 10 | 0 (0,0%) | 119 | 8 (6,7%) |
| `0,05 a 0,1` | 110 | 1 (0,9%) | 10 | 0 (0,0%) | 120 | 1 (0,8%) |
| `0,1 a +Inf` | 110 | 0 (0,0%) | 10 | 0 (0,0%) | 120 | 0 (0,0%) |
| **`_count` / `_sum` / Média** | **`110`** | **`1,215099 s` (`11,05 ms`)** | **`10`** | **`0,078900 s` (`7,89 ms`)** | **`120`** | **`1,293999 s` (`10,78 ms`)** |

### Boxplot da latência dos endpoints

Existem duas formas complementares de calcular o boxplot da latência por
endpoint, ambas implementadas e comparadas na planilha:

1. **Cálculo exato a partir do log JSONL (`latency_ms` por endpoint):**
   - Filtrar `latency_ms` dos eventos HTTP do endpoint desejado na janela.
   - Calcular `Q1` (percentil 25%), `Mediana` (50%) e `Q3` (75%) por
     interpolação linear (`PERCENTILE`/`MEDIAN`).
   - Calcular `IQR = Q3 - Q1` e os limites de Tukey: `Limite inf = Q1 - 1,5 × IQR`
     e `Limite sup = Q3 + 1,5 × IQR`.
   - Definir o **bigode inferior** como o menor valor observado `>= Limite inf` e
     o **bigode superior** como o maior valor observado `<= Limite sup`. Valores
     fora desse intervalo são contados como **outliers**.

| Estatística (ms) | `/score` (todos, `n=110`) | `/score` (`200`, `n=50`) | `/score` (`400`, `n=60`) | `/score_baseline` (`n=10`) | Total (`n=120`) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Mínimo observado | 2,2645 | 4,0366 | 2,2645 | 4,1358 | 2,2645 |
| Q1 (25%) | 5,5517 | 6,4869 | 4,7442 | 5,1330 | 5,3764 |
| Mediana (50%) | 7,3963 | 8,8666 | 6,5946 | 6,2612 | 7,3757 |
| Q3 (75%) | 12,8318 | 15,0542 | 9,4799 | 10,3227 | 12,2595 |
| IQR (`Q3 - Q1`) | 7,2801 | 8,5673 | 4,7357 | 5,1896 | 6,8832 |
| Limite inferior (`Q1 - 1,5×IQR`) | -5,3684 | -6,3640 | -2,3593 | -2,6514 | -4,9484 |
| Limite superior (`Q3 + 1,5×IQR`) | 23,7520 | 27,9051 | 16,5834 | 18,1071 | 22,5843 |
| Bigode inferior observado | 2,2645 | 4,0366 | 2,2645 | 4,1358 | 2,2645 |
| Bigode superior observado | 23,0114 | 27,8297 | 16,5367 | 16,6615 | 22,4641 |
| Outliers (quantidade) | 9 | 3 | 8 | 0 | 11 |
| Máximo observado | 81,1528 | 81,1528 | 39,5042 | 16,6615 | 81,1528 |

Separar `/score` em `200` e `400` mostra por que o endpoint apresenta cauda
assimétrica: requisições `400` são rejeitadas antes da execução do LightGBM
(mediana `6,59 ms`), enquanto inferências completas `200` têm mediana `8,87 ms`.

2. **Aproximação dos quartis a partir dos buckets de `/metrics` (`histogram_quantile`):**
   Quando apenas os buckets acumulados de `lai_http_request_duration_seconds_bucket`
   estão disponíveis, estima-se o quantil $\hat{q}_\phi$ ($\phi \in \{0{,}25;\ 0{,}50;\ 0{,}75\}$)
   localizando o primeiro bucket $k$ onde a contagem acumulada $C_k \ge \phi \cdot N$
   e interpolando linearmente entre o limite inferior $L_{k-1}$ e o superior $L_k$:

$$\hat{q}_\phi = L_{k-1} + \frac{\phi \cdot N - C_{k-1}}{C_k - C_{k-1}} \cdot (L_k - L_{k-1})$$

Exemplo com `/score` ($N = 110$):
- Para **Q1** ($\phi \cdot N = 27{,}5$): cai na faixa `(0,005; 0,01]`, onde $C_{k-1} = 22$ e $C_k = 75$.
  $\hat{q}_{0,25} = 0{,}005 + \frac{27{,}5 - 22}{75 - 22} \cdot (0{,}01 - 0{,}005) = 0{,}005519\text{ s} = 5{,}519\text{ ms}$ (exato: `5,552 ms`).
- Para **Mediana** ($\phi \cdot N = 55{,}0$): cai em `(0,005; 0,01]`.
  $\hat{q}_{0,50} = 0{,}005 + \frac{55{,}0 - 22}{53} \cdot 0{,}005 = 8{,}113\text{ ms}$ (exato: `7,396 ms`).
- Para **Q3** ($\phi \cdot N = 82{,}5$): cai em `(0,01; 0,025]`, onde $C_{k-1} = 75$ e $C_k = 101$.
  $\hat{q}_{0,75} = 0{,}01 + \frac{82{,}5 - 75}{101 - 75} \cdot 0{,}015 = 14{,}327\text{ ms}$ (exato: `12,832 ms`).

Como os buckets pressupõem distribuição uniforme dentro de cada intervalo e não
registram os extremos individuais dentro do bucket, **a aproximação estima Q1,
mediana, Q3 e IQR**, mas **os bigodes observados e a contagem de outliers exigem
os valores individuais do log JSONL**.

A [planilha de apoio](monitoramento.xlsx) implementa ambos os métodos nas abas
`Exposicao_metrics`, `Analise_Endpoints`, `Boxplot_Logs` e `Chamadas`, com
todas as fórmulas conferidas para os 120 registros reais da carga.

### Taxa de erros

Para a mesma janela e endpoint, com base nos incrementos `Δ = Depois - Antes`
de `lai_http_requests_total` (excluindo `status_class="cancelled"` do
denominador, pois cancelamentos sem resposta possuem `http_status=null`),
calculam-se três indicadores:

- **Taxa de rejeições (cliente):** `Δ_4xx / (Δ_2xx + Δ_4xx + Δ_5xx)` (ou sobre `[1-5]xx`).
- **Taxa de falhas do servidor:** `Δ_5xx / (Δ_2xx + Δ_4xx + Δ_5xx)`.
- **Taxa combinada de erros:** `(Δ_4xx + Δ_5xx) / (Δ_2xx + Δ_4xx + Δ_5xx)`.

Uma janela com zero respostas válidas tem resultado indefinido (`"n.a."`). Na
carga sintética de 120 chamadas:

| Endpoint | Respostas (`2xx+4xx+5xx`) | `2xx` | `4xx` | `5xx` | Taxa de rejeições (`4xx`) | Taxa servidor (`5xx`) | Taxa combinada |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `/score` | 110 | 50 | 60 | 0 | 54,55% | 0,00% | 54,55% |
| `/score_baseline` | 10 | 10 | 0 | 0 | 0,00% | 0,00% | 0,00% |
| **Total** | **120** | **60** | **60** | **0** | **50,00%** | **0,00%** | **50,00%** |

Em PromQL, a taxa combinada e as taxas separadas por endpoint são calculadas com:

```promql
sum by (endpoint) (rate(lai_http_requests_total{status_class=~"4xx|5xx"}[5m]))
/
sum by (endpoint) (rate(lai_http_requests_total{status_class=~"[1-5]xx"}[5m]))
```

Para isolar apenas falhas de servidor (`5xx`) ou detalhar o motivo da falha em
`/score` e `/score_baseline`, substituir o filtro por `status_class="5xx"` ou
consultar `rate(lai_score_failures_total[5m])` agrupado por `kind`.

## Preservação das informações

| Informação | Por quanto tempo é preservada |
| --- | --- |
| Acumulados expostos pela aplicação em `/metrics` | Durante a execução do servidor e seus workers. O histórico temporal depende da coleta externa. |
| Arquivos JSONL | Permanecem no disco até arquivamento ou exclusão explícita. Cada evento é acrescentado em modo append e o encerramento normal fecha os handlers. |
| Histórico coletado pelo Prometheus | A configuração do projeto define coleta a cada 15 segundos, retenção de 15 dias e armazenamento no volume `prometheus-data`. |

Na configuração Docker, os logs são gravados no volume `telemetry-data`. A
conservação dos volumes é necessária para manter os arquivos ao recriar os
containers. O projeto não configura expiração automática dos JSONL.

Uma falha de abertura impede a inicialização com telemetria habilitada. Uma falha
de escrita durante atendimento incrementa `lai_telemetry_write_failures_total`
e gera diagnóstico em stderr. Uma interrupção abrupta do processo pode impedir
a gravação do evento final da chamada.

## Referências

| Documentação indicada no enunciado | Aplicação |
| --- | --- |
| [Python Logging HOWTO](https://docs.python.org/pt-br/3/howto/logging.html) | Logger, handler de arquivo, formato e modo append. |
| [Data e hora nas mensagens](https://docs.python.org/pt-br/3/howto/logging.html#displaying-the-date-time-in-messages) | Timestamps UTC no formato `%(asctime)s` (`YYYY-MM-DD HH:MM:SS,mmm`) via `logging.Formatter.formatTime`. |
| [Exemplo de KS](https://github.com/lgallindo/bentoML-processamento-imagens/tree/main/003_ks_drift_alert_streamlit) | Preservação de observações para comparação das distribuições acumuladas. |
| [Exemplo de PSI](https://github.com/lgallindo/bentoML-processamento-imagens/tree/main/008_psi_drift_alert_streamlit) | Faixas e categorias compartilhadas entre referência e janela atual. |
| [Formato de exposição Prometheus](https://prometheus.io/docs/instrumenting/exposition_formats/) | Interpretação das famílias, contadores e histogramas expostos. |
| [Prometheus FastAPI Instrumentator](https://github.com/trallnag/prometheus-fastapi-instrumentator) | Referência de métricas HTTP por endpoint, método, status e duração aplicada ao runtime BentoML. |
