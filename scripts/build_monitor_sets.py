"""Gera dois lotes sintéticos de 100 pedidos para POST /score."""

from __future__ import annotations

import argparse
import json
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts" / "registered" / "preprocessor.json"
REFERENCE_OUT = ROOT / "data" / "reference" / "score_inputs.jsonl"
PRODUCTION_OUT = ROOT / "data" / "production" / "score_inputs.jsonl"
N = 100
SEED = 42

BRASILIA = ("DISTRITO FEDERAL", "Brasília")
RIO = ("RIO DE JANEIRO", "Rio de Janeiro")
# Localidade do órgão e contagens hipotéticas nos lotes de referência e produção.
ORGAN_PROFILES = {
    "INSS – Instituto Nacional do Seguro Social": (BRASILIA, 22, 25),
    "MEC – Ministério da Educação": (BRASILIA, 16, 12),
    "MS – Ministério da Saúde": (BRASILIA, 14, 13),
    "PF – Polícia Federal": (BRASILIA, 11, 9),
    "CGU – Controladoria-Geral da União": (BRASILIA, 10, 6),
    "ANVISA – Agência Nacional de Vigilância Sanitária": (BRASILIA, 8, 7),
    "MJSP – Ministério da Justiça e Segurança Pública": (BRASILIA, 8, 7),
    "IBGE – Fundação Instituto Brasileiro de Geografia e Estatística": (RIO, 6, 7),
    "CC-PR – Casa Civil da Presidência da República": (BRASILIA, 5, 4),
}
REQUESTER_LOCATIONS = (
    BRASILIA,
    RIO,
    ("SÃO PAULO", "São Paulo"),
    ("MINAS GERAIS", "Belo Horizonte"),
    ("PARANÁ", "Curitiba"),
    ("PERNAMBUCO", "Recife"),
    ("BAHIA", "Salvador"),
    ("CEARÁ", "Fortaleza"),
)
UNKNOWN_ORGANS = ("Instituto Municipal de Serviços ao Cidadão", "Agência de Gestão Regional")
UNKNOWN_CHANNELS = ("Aplicativo móvel", "Chat do portal")


def load_vocab(path: Path) -> dict:
    meta = json.loads(path.read_text(encoding="utf-8"))
    codes = meta["category_codes"]
    missing_organs = ORGAN_PROFILES.keys() - (
        meta["organ_rate"].keys() & codes["OrgaoDestinatario"].keys()
    )
    if missing_organs:
        raise ValueError(f"órgãos do cenário ausentes do artefato: {sorted(missing_organs)}")
    if set(UNKNOWN_ORGANS) & (meta["organ_rate"].keys() | codes["OrgaoDestinatario"].keys()):
        raise ValueError("o cenário de órgãos desconhecidos precisa usar categorias inéditas")
    if set(UNKNOWN_CHANNELS) & codes["OrigemSolicitacao"].keys():
        raise ValueError("o cenário de canais desconhecidos precisa usar categorias inéditas")
    for (uf, city), _, _ in ORGAN_PROFILES.values():
        if uf not in codes["UF"] or city not in codes["Municipio"]:
            raise ValueError(f"localidade do órgão fora do vocabulário: {uf}, {city}")
    for uf, city in REQUESTER_LOCATIONS:
        if uf not in codes["UF_sol"] or city not in codes["Municipio_sol"]:
            raise ValueError(f"localidade do solicitante fora do vocabulário: {uf}, {city}")
    for field, value in (
        ("Esfera", "Federal"),
        ("Pais", "Brasil"),
        ("TipoDemandante", "Pessoa Física"),
    ):
        if value not in codes[field]:
            raise ValueError(f"{field}={value} fora do vocabulário")
    return {
        "codes": {name: sorted(values) for name, values in codes.items()},
        "history": meta["caller_supplied_features"],
        "base_rate": meta["base_rate"],
    }


def fmt_date(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def history_block(rng: random.Random, base_rate: float) -> dict:
    n_prev = rng.randint(1, 80)
    n_org = rng.randint(1, min(20, n_prev))
    n_other = n_prev - n_org
    n_orgs = 1 + rng.randint(int(n_other > 0), min(13, n_other))
    prev_reenc_org = sum(rng.random() < base_rate for _ in range(n_org))
    prev_reenc_other = sum(rng.random() < base_rate for _ in range(n_other))
    return {
        "n_pedidos_previos": n_prev,
        "n_pedidos_previos_neste_orgao": n_org,
        "n_orgaos_distintos_previos": n_orgs,
        "dias_desde_ultimo_pedido": rng.randint(1, 365),
        "prev_reenc_solicitante": prev_reenc_org + prev_reenc_other,
        "prev_reenc_solicitante_den": n_prev,
        "prev_reenc_neste_orgao": prev_reenc_org,
        "prev_reenc_neste_orgao_den": n_org,
    }


def complete_profile(rng: random.Random, vocab: dict, organ: str, registered: date) -> dict:
    uf, city = ORGAN_PROFILES[organ][0]
    requester_uf, requester_city = rng.choice(REQUESTER_LOCATIONS)
    born = date(registered.year - rng.randint(18, 75), registered.month, min(registered.day, 28))
    codes = vocab["codes"]
    return {
        "Esfera": "Federal",
        "UF": uf,
        "Municipio": city,
        "Pais": "Brasil",
        "UF_sol": requester_uf,
        "Municipio_sol": requester_city,
        "TipoDemandante": "Pessoa Física",
        "DataNascimento": fmt_date(born),
        **{
            field: rng.choice(codes[field])
            for field in (
                "FormaResposta",
                "OrigemSolicitacao",
                "Genero",
                "Escolaridade",
                "Profissao",
            )
        },
        **history_block(rng, vocab["base_rate"]),
    }


def build_reference(rng: random.Random, vocab: dict) -> list[dict]:
    organs = [organ for organ, (_, count, _) in ORGAN_PROFILES.items() for _ in range(count)]
    rng.shuffle(organs)
    rows = []
    for i, organ in enumerate(organs):
        registered = date(2024, 1, 1) + timedelta(days=i)
        rows.append(
            {
                "pedido": {
                    "OrgaoDestinatario": organ,
                    "DataRegistro": fmt_date(registered),
                    **complete_profile(rng, vocab, organ, registered),
                }
            }
        )
    return rows


def build_production(rng: random.Random, vocab: dict) -> list[dict]:
    organs = [organ for organ, (_, _, count) in ORGAN_PROFILES.items() for _ in range(count)]
    # Embaralha os cenários independentemente dos órgãos conhecidos.
    scenarios = ["minimal"] * 40 + ["complete"] * 25 + ["partial"] * 15 + ["unknown_channel"] * 10
    rng.shuffle(scenarios)
    cases = list(zip(organs, scenarios, strict=True))
    cases.extend((organ, "minimal") for organ in UNKNOWN_ORGANS for _ in range(5))
    rng.shuffle(cases)

    rows = []
    for i, (organ, scenario) in enumerate(cases):
        registered = date(2026, 1, 1) + timedelta(days=i)
        pedido = {"OrgaoDestinatario": organ, "DataRegistro": fmt_date(registered)}
        if scenario == "complete":
            pedido.update(complete_profile(rng, vocab, organ, registered))
        elif scenario == "partial":
            history = history_block(rng, vocab["base_rate"])
            fields = rng.sample(vocab["history"], k=rng.randint(1, 3))
            pedido.update({field: history[field] for field in fields})
        elif scenario == "unknown_channel":
            pedido["OrigemSolicitacao"] = rng.choice(UNKNOWN_CHANNELS)
        rows.append({"pedido": pedido})
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_sets(artifact: Path, seed: int) -> tuple[list[dict], list[dict]]:
    vocab = load_vocab(artifact)
    rng = random.Random(seed)
    reference = build_reference(rng, vocab)
    production = build_production(rng, vocab)
    if len(reference) != N or len(production) != N:
        raise ValueError(f"cada cenário deve gerar exatamente {N} pedidos")
    return reference, production


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, default=ARTIFACT)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--reference-out", type=Path, default=REFERENCE_OUT)
    parser.add_argument("--production-out", type=Path, default=PRODUCTION_OUT)
    args = parser.parse_args()

    reference, production = build_sets(args.artifact, args.seed)
    write_jsonl(args.reference_out, reference)
    write_jsonl(args.production_out, production)
    print(
        json.dumps(
            {
                "seed": args.seed,
                "reference": str(args.reference_out),
                "reference_rows": len(reference),
                "production": str(args.production_out),
                "production_rows": len(production),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
