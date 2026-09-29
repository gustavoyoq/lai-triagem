import pytest

from mlops_lai.core.errors import ContractError
from mlops_lai.features.preprocessor import Preprocessor

HISTORY = [
    "n_pedidos_previos",
    "n_pedidos_previos_neste_orgao",
    "n_orgaos_distintos_previos",
    "dias_desde_ultimo_pedido",
    "prev_reenc_solicitante",
    "prev_reenc_solicitante_den",
    "prev_reenc_neste_orgao",
    "prev_reenc_neste_orgao_den",
]


def preprocessor() -> Preprocessor:
    return Preprocessor(
        {
            "feature_order": [
                "UF",
                "reg_month",
                "orgao_rate",
                "n_pedidos_previos",
                "prev_reenc_rate_solicitante",
            ],
            "categorical_features": ["UF"],
            "numeric_features": [
                "reg_month",
                "orgao_rate",
                "n_pedidos_previos",
                "prev_reenc_rate_solicitante",
            ],
            "category_codes": {"UF": {"PE": 7}},
            "organ_rate": {"Órgão A": 0.4},
            "base_rate": 0.1,
            "threshold": 0.2,
            "train_years": [2022],
            "caller_supplied_features": HISTORY,
            "caller_supplied_default": -1,
            "derived_from_caller": ["prev_reenc_rate_solicitante"],
        }
    )


def base_payload() -> dict[str, object]:
    return {"OrgaoDestinatario": "Órgão A", "DataRegistro": "15/09/2026", "UF": "PE"}


def test_partial_history_is_discarded_atomically() -> None:
    payload = base_payload() | {"n_pedidos_previos": 7}
    features = preprocessor().transform(payload)
    assert features.loc[0, "n_pedidos_previos"] == -1
    assert features.loc[0, "prev_reenc_rate_solicitante"] == -1


def test_complete_history_derives_rate_from_mature_denominator() -> None:
    payload = base_payload() | dict(zip(HISTORY, [10, 4, 2, 3, 2, 8, 1, 4], strict=True))
    features = preprocessor().transform(payload)
    assert features.loc[0, "n_pedidos_previos"] == 10
    assert features.loc[0, "prev_reenc_rate_solicitante"] == 0.25


def test_post_hoc_field_is_refused() -> None:
    with pytest.raises(ContractError, match="post-hoc"):
        preprocessor().transform(base_payload() | {"FoiReencaminhado": "Sim"})
