# Evidência de execução local

Data: 21/09/2026. Ambiente validado com Python 3.13.1 e `uv 0.12.17`; a
apresentação deve usar Python 3.12, conforme o README e a imagem Docker.

## Pacote BentoML

Comando executado:

```powershell
uv run bentoml build
```

Resultado:

```text
Successfully built Bento(tag="lai-triagem:4eytr3fwdgj4e5cw").
```

O bundle foi iniciado em seguida com `uv run bentoml serve
lai-triagem:4eytr3fwdgj4e5cw --port 3001`. A mesma chamada de predição retornou
HTTP 200; portanto, o modelo e o `preprocessor.json` foram incluídos no Bento.

## Predição HTTP

Com o comando de serving do README na porta 3000, foi executado:

```powershell
curl.exe -sS -X POST http://localhost:3000/score -H "Content-Type: application/json" -d '{"pedido":{"OrgaoDestinatario":"CC-PR – Casa Civil da Presidência da República","DataRegistro":"15/09/2026"}}'
```

O endpoint retornou HTTP 200, `probabilidade_reencaminhamento: 0.354094`,
`alerta: ALTO RISCO` e `model_version: provided-2026-09-21`.

`POST /health` retornou `status: ok`, 31 features e o SHA-256 do manifesto.

O envio do campo pós-triagem `FoiReencaminhado` retornou HTTP 400 com:

```json
[{"error":"campos post-hoc recusados: ['FoiReencaminhado']"}]
```

## Qualidade

```text
uv run pytest --basetemp .pytest-tmp  -> 11 passed
uv run ruff check src tests            -> All checks passed
uv run lai-validate-artifact ...       -> artifact_valid
```

## Percentis de latência (`POST /score`)

O [relatório final da AV2 em PDF](RELATORIO_AV2.pdf) interpreta os resultados e os dois
gráficos. O [manual da AV2](MANUAL_AV2.md) cobre o painel e a reprodução dos
cálculos.

A medição foi feita em 24/11/2026, às 20h04 no horário de Brasília, com
Python 3.12.3 e o modelo `provided-2026-09-21`. Foram 100 chamadas
sequenciais ao serviço local, com 100 respostas HTTP 200 e zero falhas.
O cliente mediu o tempo até ler a resposta inteira.

| Estatística | ms |
| --- | ---: |
| Mínimo | 6,1192 |
| Q1 (p25) | 13,8749 |
| Mediana (p50) | 20,5408 |
| Q3 (p75) | 27,0196 |
| IQR | 13,1447 |
| p90 | 32,2042 |
| p95 | 37,5054 |
| p99 | 115,5906 |
| Máximo | 268,1643 |

O [JSON desta medição](../reports/av2/latency_percentiles.json) contém
o pedido enviado, o status e o tempo individual das 100 chamadas. A tabela
usa `statistics.quantiles(..., n=100, method="inclusive")` sobre os tempos
registrados, com arredondamento no resultado.

O p95 de 37,51 ms ficou abaixo dos 400 ms do SLA de exemplo nesta máquina
e nesta carga. O p99 de 115,59 ms e o máximo de 268,16 ms mostram a cauda
da distribuição. Com 100 observações, poucas chamadas lentas têm influência
grande no p99.

Para medir novamente, inicie `make serve` em um terminal. No outro, crie uma
pasta para a nova rodada e grave a medição nela.

```bash
RUN_DIR="$(make new-monitor-run)"
make latency LATENCY_OUTPUT="$RUN_DIR/latency_percentiles.json"
```

## Conjuntos de referência e produção

`make datasets` gera os dois JSONLs com a semente 42 e o vocabulário do
pré-processador registrado. Os [100 pedidos de referência](../data/reference/score_inputs.jsonl)
usam órgãos conhecidos, histórico completo e categorias do vocabulário. Nos
[100 pedidos de produção simulada](../data/production/score_inputs.jsonl), a
composição é a seguinte.

| Cenário de produção | Pedidos |
| --- | ---: |
| Órgão conhecido e data, sem campos opcionais | 40 |
| Órgão conhecido, histórico completo e perfil do solicitante | 25 |
| Órgão conhecido e histórico parcial | 15 |
| Órgão conhecido e canal de origem fora do vocabulário | 10 |
| Órgão sintético desconhecido e data | 10 |

Para os perfis completos, o gerador associa cada órgão federal a uma
localidade fixa e sorteia a localidade do solicitante como um par válido
de UF e município no Brasil. A Casa Civil usa Brasília (DF) e o IBGE usa
Rio de Janeiro (RJ). Os perfis completos representam solicitantes adultos.
[Casa Civil](https://www.gov.br/casacivil/pt-br/composicao/) ·
[IBGE](https://www.ibge.gov.br/np_download/novoportal/documentos_institucionais/Termo_de_Adesao_A3P.pdf)

Os nove órgãos conhecidos aparecem em ambos os lotes. As contagens
priorizam alguns órgãos que atendem públicos amplos, variam moderadamente
entre lotes e incluem dois órgãos sintéticos na produção, com cinco pedidos
cada. Essas proporções são hipóteses da simulação. A tabela registra as
contagens usadas.

| Órgão | Referência | Produção |
| --- | ---: | ---: |
| INSS | 22 | 25 |
| MEC | 16 | 12 |
| Ministério da Saúde | 14 | 13 |
| Polícia Federal | 11 | 9 |
| CGU | 10 | 6 |
| Anvisa | 8 | 7 |
| MJSP | 8 | 7 |
| IBGE | 6 | 7 |
| Casa Civil | 5 | 4 |
| Órgãos sintéticos | 0 | 10 |
| **Total** | **100** | **100** |

## Replay e registro das respostas

Encerre o servidor da medição de latência antes de usar a mesma porta.
Copie o caminho da rodada nova para os dois terminais. Se já criou uma pasta
para a medição de latência, pode usar a mesma aqui.

```bash
make new-monitor-run
export RUN_DIR=monitoring/telemetry/runs/av2-XXXXXXXX  # use o caminho exibido

# Terminal A
make serve-reference
# Terminal B, quando o serviço estiver pronto
make replay-reference

# Encerre o serviço de referência no terminal A e inicie o de produção
make serve-production
# Terminal B, quando o serviço estiver pronto
make replay-production
make drift-sets
```

O replay confere os IDs HTTP com os da telemetria e exige uma resposta
completa para cada pedido. A rodada tem uma subpasta por lote. Para repetir
o envio, crie outra rodada com `make new-monitor-run`. Se usar outra porta,
informe o mesmo `PORT` no servidor e no cliente.

| Lote | HTTP 200 | `prediction_raw` | Órgão conhecido | Histórico completo | Histórico parcial ignorado | Idade numérica |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| [Referência](../reports/av2/reference/scoring.jsonl) | 100 | 100 | 100 | 100 | 0 | 100 |
| [Produção](../reports/av2/production/scoring.jsonl) | 100 | 100 | 90 | 25 | 15 | 25 |

## KS e PSI (entrada e saída)

O [relatório arquivado](../reports/av2/drift.json) compara exatamente 100
respostas por lote. Para recalculá-lo a partir dos registros acima, execute
`make drift-sets RUN_DIR=reports/av2`. O comando também exige 100 observações
de cada variável em cada lote.

| Variável | Tipo | Métrica | Resultado |
| --- | --- | --- | ---: |
| `OrgaoDestinatario` | Entrada categórica | PSI | 1,1289 |
| `prediction_raw` | Saída contínua | KS D | 0,4800 |

O PSI ficou acima do limiar exploratório de 0,2. Os dois órgãos sintéticos,
ausentes da referência, responderam por cerca de 96% do índice. Para a saída,
a distância KS indica uma diferença máxima de 48 pontos percentuais entre
as distribuições acumuladas dos escores. A mediana foi de 0,0529 na referência
e 0,1031 na produção simulada. Os valores descrevem a mudança de distribuição
entre estes dois lotes.
