# Logs e métricas do serviço de inferência

## Registro em arquivo

As chamadas a `POST /score` e `POST /score_baseline` geram registros JSONL UTF-8
no disco do processo. Cada linha reúne os dados da entrada, a resposta e o
contexto da chamada. Rejeições de validação e erros também são registrados.

O caminho padrão é
`monitoring/telemetry/scoring.<execucao>.<pid>.jsonl`. Cada processo possui seu
arquivo. O gravador usa `logging.FileHandler` em modo append, com flush por
evento. Novas chamadas acrescentam linhas e preservam o conteúdo já gravado.

Os campos `started_at_utc` e `timestamp_utc` indicam início e conclusão em UTC,
no formato ISO 8601 com seis casas de microssegundos e o fuso `+00:00`.
`request_id` correlaciona a chamada com a resposta e também é devolvido no
cabeçalho `X-Request-ID`.

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

### Dados suficientes para as distribuições

Os valores numéricos individuais permitem construir histogramas e boxplots de
entradas e saídas. Para comparar períodos, selecionar a mesma variável, endpoint
e versão, tratando ausentes e sentinelas separadamente. Para categorias nominais,
usar frequências por categoria. Os códigos categóricos do modelo representam
rótulos e devem ser interpretados com seu dicionário.

Para KS, conservar as observações numéricas e comparar as distribuições acumuladas
da referência e da janela atual. Para PSI, definir faixas numéricas ou categorias
compartilhadas e comparar suas frequências relativas. Os escores individuais são
preservados além do campo agregado `score_bin`.

Quando os dados de treinamento estiverem indisponíveis, estabelecer uma referência
manual fixa e documentar a seleção, o período, o tamanho e a versão do modelo.
Usar observações independentes para referência e janela atual. Os timestamps
permitem selecionar os períodos posteriormente.

## Geração de carga e acompanhamento do rastro

Com a API iniciada conforme o README, executar na raiz do repositório

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

No Bash, acompanhar o arquivo de um dos processos com

```bash
tail -f "$(ls -t monitoring/telemetry/scoring.*.jsonl | head -n 1)"
```

No PowerShell, usar

```powershell
$log = Get-ChildItem monitoring/telemetry/scoring.*.jsonl |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-Content -LiteralPath $log.FullName -Encoding utf8 -Wait
```

## Como interpretar o GET /metrics

O BentoML expõe `GET /metrics` em formato compatível com Prometheus. Consultar com
`curl -sS http://localhost:3000/metrics` ou `curl.exe` no PowerShell.

| Métrica | Tipo | Interpretação |
| --- | --- | --- |
| `lai_http_requests_total` | Counter | Respostas por endpoint, método e classe de status. |
| `lai_http_request_duration_seconds` | Histogram | Duração HTTP até o envio final do corpo, em segundos. |
| `lai_score_requests_total` | Counter | Sucessos de `/score` por versão e órgão conhecido. |
| `lai_score_failures_total` | Counter | Chamadas de inferência rejeitadas, por tipo de falha. |
| `lai_score_latency_seconds` | Histogram | Duração de `model.score`, incluindo pré-processamento, predição, calibração e falhas. |
| `lai_prediction_score` | Histogram | Distribuição dos escores devolvidos por endpoint e versão. |
| `lai_risk_predictions_total` | Counter | Quantidade de alto e baixo risco por endpoint e versão. |
| `lai_input_quality_events_total` | Counter | Chamadas com órgão desconhecido, histórico ausente ou parcial e categorias desconhecidas. |
| `lai_telemetry_write_failures_total` | Counter | Falhas de persistência do JSONL durante atendimento. |

Os contadores acumulam eventos. Comparar seus incrementos na mesma janela.
Uma chamada pode ter vários indicadores de qualidade, contabilizados uma vez por
tipo. A coleta própria exclui `/metrics` para que scrapes não aumentem o volume
avaliado. Chamadas diretas por Python contribuem para métricas de inferência,
enquanto o contador HTTP acompanha respostas do servidor.

### Histograma da latência

A métrica `lai_http_request_duration_seconds` expõe buckets cumulativos, `_count`
e `_sum`. Cada bucket conta observações menores ou iguais ao limite `le`.
Para uma janela, obter o incremento de cada bucket e subtrair o incremento do
bucket anterior. O resultado é a contagem daquela faixa. `+Inf` deve coincidir
com `_count`.

A tabela mostra esse cálculo com os snapshots da carga sintética de 120 chamadas.
Os buckets intermediários acima de 0,1 segundo tiveram contagem adicional zero.

| Limite superior | Acumulado antes | Acumulado depois | Incremento cumulativo | Contagem na faixa |
| --- | ---: | ---: | ---: | ---: |
| 0,005 s | 0 | 24 | 24 | 24 |
| 0,010 s | 0 | 82 | 82 | 58 |
| 0,025 s | 0 | 111 | 111 | 29 |
| 0,050 s | 0 | 119 | 119 | 8 |
| 0,100 s | 0 | 120 | 120 | 1 |
| `+Inf` | 0 | 120 | 120 | 0 |

A latência média na janela é o incremento de `_sum` dividido pelo incremento de
`_count`. Aplicar `rate` ou `increase` a cada série antes de somar processos para
tratar resets dos contadores.

### Boxplot da latência

Extrair `latency_ms` individual dos eventos HTTP do endpoint e período escolhidos.
Calcular Q1, mediana e Q3 por interpolação linear e obter `IQR = Q3 - Q1`.
Os limites são `Q1 - 1,5 × IQR` e `Q3 + 1,5 × IQR`. Os bigodes ficam no menor e
no maior valor observado dentro desses limites. Os valores externos são outliers.

Os buckets de `/metrics` permitem estimar quartis pela distribuição acumulada,
com precisão limitada pelos intervalos. Os valores individuais do log permitem
calcular os quartis, bigodes e outliers diretamente.

A [planilha de apoio](../outputs/monitoramento/monitoramento.xlsx) reúne as 120
latências individuais, os snapshots dos buckets e as fórmulas de quartis, IQR,
bigodes, contagens por faixa e taxa de erros.

### Taxa de erros

Para a mesma janela e endpoint, calcular

- Taxa de rejeições como `respostas 4xx / total de respostas`.
- Taxa de falhas do servidor como `respostas 5xx / total de respostas`.
- Taxa combinada como `(respostas 4xx + respostas 5xx) / total de respostas`.

Uma janela sem respostas tem resultado indefinido. Cancelamentos sem resposta
possuem status nulo e classe `cancelled`, excluída desse denominador. Em PromQL,
a taxa combinada pode ser calculada com

```promql
sum by (endpoint) (rate(lai_http_requests_total{status_class=~"4xx|5xx"}[5m]))
/
sum by (endpoint) (rate(lai_http_requests_total{status_class=~"[1-5]xx"}[5m]))
```

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
| [Data e hora nas mensagens](https://docs.python.org/pt-br/3/howto/logging.html#displaying-the-date-time-in-messages) | Timestamps com fuso e precisão de microssegundos. |
| [Exemplo de KS](https://github.com/lgallindo/bentoML-processamento-imagens/tree/main/003_ks_drift_alert_streamlit) | Preservação de observações para comparação das distribuições acumuladas. |
| [Exemplo de PSI](https://github.com/lgallindo/bentoML-processamento-imagens/tree/main/008_psi_drift_alert_streamlit) | Faixas e categorias compartilhadas entre referência e janela atual. |
| [Formato de exposição Prometheus](https://prometheus.io/docs/instrumenting/exposition_formats/) | Interpretação das famílias, contadores e histogramas expostos. |
| [Prometheus FastAPI Instrumentator](https://github.com/trallnag/prometheus-fastapi-instrumentator) | Referência de métricas HTTP por endpoint, método, status e duração aplicada ao runtime BentoML. |
