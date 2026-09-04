"""Deterministic safety checks that run before the LLM."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum


class GuardrailKind(str, Enum):
    """Preflight requests that must not be delegated to generation."""

    PERSONALIZED_FINANCIAL_ADVICE = "personalized_financial_advice"
    SENSITIVE_CREDENTIALS = "sensitive_credentials"
    OUT_OF_SCOPE = "out_of_scope"


@dataclass(frozen=True)
class GuardrailDecision:
    """Result of deterministic preflight inspection."""

    kind: GuardrailKind


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def inspect_message(text: str) -> GuardrailDecision | None:
    """Detect personalized advice and credential-handling requests."""

    normalized = _normalize(text)
    code_request = re.search(
        r"\b(?:script|codigo|programa|funcao)\b.*\b(?:python|javascript|java|sql|"
        r"somar|calcular)\b|\b(?:python|javascript|java|sql)\b.*\b(?:script|codigo|"
        r"programa|funcao)\b",
        normalized,
    )
    if code_request:
        return GuardrailDecision(GuardrailKind.OUT_OF_SCOPE)
    credential_terms = re.search(
        r"\b(?:senha|token|codigo de acesso|login|credencial)\b", normalized
    )
    account_action = re.search(
        r"\b(?:banco|conta|corretora|recuperar|acessar|entrar)\b", normalized
    )
    if credential_terms and account_action:
        return GuardrailDecision(GuardrailKind.SENSITIVE_CREDENTIALS)

    recommendation = re.search(
        r"\b(?:devo|qual|onde|recomenda|indica|melhor)\b", normalized
    )
    financial_action = re.search(
        r"\b(?:investir|comprar|vender|colocar|alocar|acao|acoes|tesouro|fundo|carteira|ativo)\b",
        normalized,
    )
    if recommendation and financial_action:
        return GuardrailDecision(GuardrailKind.PERSONALIZED_FINANCIAL_ADVICE)
    return None
