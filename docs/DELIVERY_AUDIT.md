# Auditoria de entrega

Data da auditoria: 21/09/2026.

## Escopo auditado

- Arquivo de entrada: `../lai-triagem-main.zip`.
- Modelo promovido: `artifacts/registered/model_arrival.txt`.
- Metadados promovidos: `artifacts/registered/preprocessor.json`.
- Rubrica da atividade: `../mingostexto.txt`, lida em 21/09/2026.

## Controles verificáveis

1. `lai-validate-artifact --artifact-dir artifacts/registered` recalcula ambos
   os hashes SHA-256, carrega LightGBM e confirma a compatibilidade entre o
   número de features do booster e o preprocessor.
2. A API recusa campos listados em `LEAKAGE_FIELDS` antes de chamar o modelo.
3. A promoção valida o artefato e não sobrescreve o destino.
4. O log opcional de telemetria não recebe campos de entrada nem identificadores.
5. `tests/` executa sobre o mesmo artefato que a API usa por padrão.

## Limitação de reprodutibilidade factual

O snapshot bruto da CGU não acompanhava a atividade. Portanto, o pipeline está
implementado e parametrizado, mas a reprodução numérica do modelo fornecido só
é possível depois de disponibilizar os CSVs autorizados e o snapshot original.
Para a rubrica SR1, o modelo pré-treinado fornecido é suficiente: o foco é o
serviço BentoML em volta dele, protegido pelo manifesto.
