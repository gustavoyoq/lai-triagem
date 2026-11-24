from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from mlops_lai.features.preprocessor import Preprocessor

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "data" / "reference" / "score_inputs.jsonl"
PRODUCTION = ROOT / "data" / "production" / "score_inputs.jsonl"
LOCATIONS = {
    "DISTRITO FEDERAL": "Brasília",
    "RIO DE JANEIRO": "Rio de Janeiro",
    "SÃO PAULO": "São Paulo",
    "MINAS GERAIS": "Belo Horizonte",
    "PARANÁ": "Curitiba",
    "PERNAMBUCO": "Recife",
    "BAHIA": "Salvador",
    "CEARÁ": "Fortaleza",
}


def _load(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line)["pedido"])
    return rows


def _preprocessor() -> Preprocessor:
    return Preprocessor.from_file(ROOT / "artifacts" / "registered" / "preprocessor.json")


def test_monitor_sets_contract() -> None:
    reference = _load(REFERENCE)
    production = _load(PRODUCTION)
    assert len(reference) == 100
    assert len(production) == 100

    prep = _preprocessor()
    for pedido in reference + production:
        prep.validate_payload(pedido)

    for pedido in reference:
        assert prep.history_state(pedido).supplied is True
        assert prep.organ_is_known(pedido["OrgaoDestinatario"]) is True

    assert sum(prep.history_state(p).supplied for p in production) == 25
    assert sum(prep.history_state(p).partial_ignored for p in production) == 15
    assert sum(not prep.organ_is_known(p["OrgaoDestinatario"]) for p in production) == 10
    minimal_known = [
        p for p in production if len(p) == 2 and prep.organ_is_known(p["OrgaoDestinatario"])
    ]
    assert len(minimal_known) == 40
    assert sum(bool(prep.monitoring_state(p)["unknown_categories"]) for p in production) == 20

    for pedido in reference + production:
        if not prep.history_state(pedido).supplied:
            continue
        assert LOCATIONS[pedido["UF"]] == pedido["Municipio"]
        assert LOCATIONS[pedido["UF_sol"]] == pedido["Municipio_sol"]
        assert pedido["prev_reenc_neste_orgao"] <= pedido["prev_reenc_solicitante"]

    ref_organs = Counter(p["OrgaoDestinatario"] for p in reference)
    prod_known_organs = Counter(
        p["OrgaoDestinatario"] for p in production if prep.organ_is_known(p["OrgaoDestinatario"])
    )
    assert ref_organs.keys() == prod_known_organs.keys()
    assert max(ref_organs.values()) > min(ref_organs.values())

    ref_fps = {json.dumps(p, sort_keys=True, ensure_ascii=False) for p in reference}
    prod_fps = {json.dumps(p, sort_keys=True, ensure_ascii=False) for p in production}
    assert ref_fps.isdisjoint(prod_fps)
    assert len(ref_fps) == len(prod_fps) == 100
