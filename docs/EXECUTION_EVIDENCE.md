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
