"""Erros que representam falhas no contrato, não erros internos."""


class ContractError(ValueError):
    """A requisição viola o contrato de inferência."""


class ArtifactError(RuntimeError):
    """O modelo ou os metadados registrados são inválidos ou foram alterados."""
