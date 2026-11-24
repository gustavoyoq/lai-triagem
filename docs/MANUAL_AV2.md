# Manual da AV2

## Preparar o ambiente

O ambiente de referência é Ubuntu 24.04 (x86-64), Python 3.12.3 e
`uv 0.13.10`. No Windows, abra o terminal do Ubuntu no WSL2 e use os mesmos
comandos. A instalação inicial precisa de acesso à internet para baixar as
dependências públicas.

Use a pasta completa da entrega. Abra o terminal na pasta que contém
`Makefile` e `uv.lock`. Confira que ela também contém o modelo em
`artifacts/registered/`, os pedidos em `data/` e a rodada publicada em
`reports/av2/`. A cópia completa contém tudo o que é necessário para reproduzir
a AV2 com o modelo registrado.

Em uma máquina Ubuntu sem as ferramentas instaladas, execute

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl libgomp1 make tzdata
curl -LsSf https://astral.sh/uv/0.13.10/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version
```

Na raiz da entrega, crie o ambiente com as versões travadas em `uv.lock` e
valide o modelo. O `uv` baixa o Python 3.12.3 se não o encontrar.

```bash
uv sync --locked --python 3.12.3 --extra dashboard
uv run --locked python --version
make validate
```

Confira `Python 3.12.3` e `artifact_valid version=provided-2026-09-21` na
saída. [Instalação do uv](https://docs.astral.sh/uv/getting-started/installation/) · [Dependências travadas](https://docs.astral.sh/uv/concepts/projects/sync/)

## Reproduzir os números do PDF

```bash
make reproduce-av2
```

Esse comando roda com a API desligada. Ele gera novamente os 100 pedidos de
cada lote com a semente 42 e confere seu conteúdo e sua ordem. Em seguida,
recalcula os percentis sobre os 100 tempos individuais e o PSI e o KS sobre
as 100 respostas arquivadas de cada lote. O comando confere os valores com
os resultados publicados, incluindo os quartis de latência e as medianas do
escore, e gera novamente o boxplot e o violino.

A saída esperada inclui estas linhas. O ponto é o separador decimal do
terminal e do JSON.

```text
Lotes reproduzidos e conferidos · 100 pedidos em cada lote
HTTP 200 na medição de latência · 100
p90 = 32.2042 ms
p95 = 37.5054 ms
p99 = 115.5906 ms
PSI de OrgaoDestinatario = 1.1289
KS D de prediction_raw = 0.4800
```

Ao final, o comando exibe a pasta nova em `monitoring/telemetry/runs/`.
Ela contém `latency_percentiles.json`, `drift.json`, `latency_distribution.png`
e os `score_inputs.jsonl` regenerados em `reference/` e `production/`. Os
arquivos publicados em `reports/av2/` continuam sendo a fonte da rodada
original. Qualquer divergência nos valores conferidos encerra o comando com
erro. [Relatório final em PDF](RELATORIO_AV2.pdf)

## Abrir o dashboard

```bash
make dashboard
```

Abra `http://127.0.0.1:8501` no navegador. O comando instala o extra `dashboard`
se necessário. O painel abre a rodada da entrega em `reports/av2/` e recalcula
as métricas a partir das medições individuais e das respostas registradas. A
API pode estar desligada durante essa consulta.

- **Latência** mostra p90, p95, p99, a comparação com 400 ms, boxplot, violino
  e a tabela ordenável de chamadas. Os botões exportam os gráficos em PNG e
  as amostras em CSV.
- **Referência e produção** mostra o PSI por distribuição de órgão e o KS
  dos escores brutos, com contagens por órgão e quartis dos dois lotes.

Para abrir uma rodada própria, informe `RUN_DIR`. Use `DASHBOARD_PORT` para
mudar a porta e `Ctrl+C` para encerrar o painel.

```bash
make dashboard RUN_DIR=monitoring/telemetry/runs/av2-XXXXXXXX DASHBOARD_PORT=8502
```

## Fazer uma nova coleta HTTP

Esta seção repete o experimento no seu computador. Os percentis de uma nova
coleta variam com a máquina e a carga. Para obter exatamente os números de
latência do PDF, use `make reproduce-av2` com as medições arquivadas. Com os
mesmos pedidos e o mesmo modelo, a nova coleta deve reproduzir PSI e KS na
precisão publicada.

### 1. Preparar os lotes e a pasta da rodada

Os dois arquivos `data/reference/score_inputs.jsonl` e
`data/production/score_inputs.jsonl` já contêm 100 pedidos cada. Cada linha é
um corpo JSON com a chave `pedido`, pronto para `POST /score`. Para regenerar
os dois arquivos a partir do pré-processador registrado e da semente 42, use
`make datasets`. O comando regrava esses arquivos. Para gravar cópias em outro
local ou usar outra semente, indique `--reference-out`, `--production-out`
e `--seed` em `scripts/build_monitor_sets.py`.

Abra dois terminais ou duas abas, A e B. Em ambos, acesse a raiz da entrega.
No terminal B, crie uma pasta vazia para esta execução.

```bash
export RUN_DIR="$(make new-monitor-run)"
printf '%s\n' "$RUN_DIR"
```

No terminal A, exporte a mesma variável com o caminho exibido no terminal B.

```bash
export RUN_DIR=monitoring/telemetry/runs/av2-XXXXXXXX
```

A porta padrão da API é 3000. Se ela estiver ocupada, execute
`export PORT=3001` nos dois terminais.

### 2. Medir a latência

No terminal A, inicie a API.

```bash
MLOPS_ENABLE_TELEMETRY=true \
MLOPS_TELEMETRY_PATH="$RUN_DIR/latency/scoring.jsonl" make serve
```

No terminal B, confira a disponibilidade e rode a medição.

```bash
curl -fsS "http://127.0.0.1:${PORT:-3000}/readyz"
make latency LATENCY_OUTPUT="$RUN_DIR/latency_percentiles.json"
```

O script imprime as estatísticas e grava o JSON com cada chamada, status e
tempo em milissegundos. A medição usa 100 chamadas sequenciais e exige ao
menos 100 respostas HTTP 200 para calcular os percentis. Falhas continuam
registradas e a saída do processo é 1 se faltarem sucessos. Encerre o servidor
com `Ctrl+C` no terminal A antes da próxima etapa.

Para ajustar endereço, volume de chamadas, timeout por pedido e caminho de
saída, rode o script diretamente. Informe a URL base sem `/score`.

```bash
uv run python scripts/latency_report.py --url "http://127.0.0.1:${PORT:-3000}" \
  --requests 120 --timeout 30 --output "$RUN_DIR/latency_percentiles.json"
```

### 3. Submeter referência e produção separadamente

No terminal A, inicie `make serve-reference`. No terminal B, confira a
disponibilidade e envie o lote de referência.

```bash
curl -fsS "http://127.0.0.1:${PORT:-3000}/readyz"
make replay-reference
```

Encerre a API com `Ctrl+C` no terminal A e inicie `make serve-production`.
No terminal B, confira a disponibilidade e envie o lote de produção.

```bash
curl -fsS "http://127.0.0.1:${PORT:-3000}/readyz"
make replay-production
```

Confira `http_200: 100`, `non_200: 0` e `prediction_raw_present: 100` na
saída de cada replay. Depois, encerre a API com `Ctrl+C` no terminal A.

Cada replay verifica os status HTTP e cruza os `X-Request-ID` das respostas
com os eventos gravados pelo serviço. As pastas `reference/` e `production/`
ficam dentro de `RUN_DIR` e recebem um JSONL por processo. As linhas guardam
`input`, `http_status`, `prediction_raw`, `response` e a versão do modelo. O
arquivo `latency_percentiles.json` mede o tempo no cliente.

Se um replay falhar, consulte a saída e o log do servidor, corrija a causa e
crie uma nova rodada. Cada pasta de lote aceita um replay para evitar a
mistura de chamadas. Se precisar usar outros inputs ou outro arquivo de
telemetria, especifique `--inputs`, `--url` e `--telemetry` no
`scripts/replay_inputs.py`, com o servidor configurado para gravar no local
indicado.

### 4. Calcular PSI e KS e gerar os gráficos

No terminal B, execute

```bash
make drift-sets
uv run --extra dashboard python scripts/plot_latency.py \
  --input "$RUN_DIR/latency_percentiles.json" \
  --output "$RUN_DIR/latency_distribution.png"
make dashboard RUN_DIR="$RUN_DIR"
```

`make drift-sets` exige 100 respostas completas e 100 observações de cada
variável por lote. Grava `drift.json` em `RUN_DIR` com as métricas, contagens e
quartis. No objeto `summary`, leia `input_psi` para `OrgaoDestinatario` e
`output_ks_d` para `prediction_raw`. Para passar caminhos diretamente ou
trabalhar com lotes maiores, ajuste `--expected-count` no script.

```bash
uv run python scripts/monitor_shift.py --reference "$RUN_DIR/reference" \
  --current "$RUN_DIR/production" --expected-count 100 --min-samples 100 \
  --output "$RUN_DIR/drift.json"
```

`plot_latency.py` lê o JSON de latência e gera os dois gráficos em um único
PNG. O boxplot usa bigodes de até 1,5 × IQR e o violino suaviza a distribuição
dos tempos em milissegundos. Ambos usam a mesma escala no eixo horizontal.

## Se um comando falhar

| Sintoma | O que conferir |
| --- | --- |
| `uv: command not found` | Rode `export PATH="$HOME/.local/bin:$PATH"` no terminal atual e confira `uv --version`. |
| `libgomp.so.1` ausente | Instale `libgomp1` e execute `make validate` novamente. |
| `uv.lock` ausente ou desatualizado | Use a cópia completa da entrega com o `uv.lock` original. |
| `/readyz` indisponível | Aguarde o servidor concluir a inicialização. Se a porta estiver ocupada, use o mesmo `PORT` nos dois terminais. |
| Replay já reservado ou com eventos extras | Encerre a API, crie uma nova `RUN_DIR` e reinicie o procedimento de coleta. |
| Telemetria arquivada ausente | Confira `reports/av2/reference/scoring.jsonl` e `reports/av2/production/scoring.jsonl`. |
