# Dados

Dados brutos, dados intermediários e dados processados não são versionados no Git.
Versione-os com DVC (ou um storage corporativo equivalente) e registre no `dvc.lock`.

Contrato de entrada do pipeline: `data/raw/training.csv` deve possuir `DataRegistro`,
`FoiReencaminhado` e todas as colunas de chegada usadas pelo modelo. Dados pessoais não
devem ser enviados para o repositório; use um armazenamento aprovado e controles de acesso.

O artefato fornecido em `artifacts/registered/` não contém o histórico pessoal de solicitantes.
