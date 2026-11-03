"""Verificacao dos JWT emitidos pelo Supabase Auth.

Aceita HS256 (Supabase self-hosted, segredo compartilhado) ou chaves
assimetricas publicadas no JWKS (Supabase Cloud). O algoritmo permitido e
fixado pela configuracao; o cabecalho do token nunca decide como verifica-lo
(evita confusao de algoritmo e "alg: none").
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import PyJWKClient


class TokenInvalido(Exception):
    pass


@dataclass(frozen=True)
class Identidade:
    usuario_id: uuid.UUID
    email: str | None
    aal: str
    sessao_id: str | None
    expira_em: int
    claims: dict[str, Any]

    def claims_para_banco(self) -> dict[str, Any]:
        """Somente o que a RLS precisa (auth.uid(), papel) - nada alem disso."""
        return {"sub": str(self.usuario_id), "role": "authenticated", "aal": self.aal}


class VerificadorJwt:
    def __init__(
        self,
        *,
        audiencia: str,
        emissor: str,
        segredo_hs256: str | None = None,
        url_jwks: str | None = None,
        tolerancia_s: int = 10,
    ) -> None:
        if not segredo_hs256 and not url_jwks:
            raise ValueError("informe segredo_hs256 ou url_jwks")
        self._audiencia = audiencia
        self._emissor = emissor
        self._segredo = segredo_hs256
        self._tolerancia = tolerancia_s
        self._jwks = PyJWKClient(url_jwks, cache_keys=True, lifespan=3600) if url_jwks and not segredo_hs256 else None

    async def verificar(self, token: str) -> Identidade:
        try:
            if self._segredo:
                chave: Any = self._segredo
                algoritmos = ["HS256"]
            else:
                if self._jwks is None:
                    raise TokenInvalido("sem_jwks")
                assinatura = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, token)
                chave = assinatura.key
                algoritmos = ["ES256", "RS256"]
            claims = jwt.decode(
                token,
                chave,
                algorithms=algoritmos,
                audience=self._audiencia,
                issuer=self._emissor,
                leeway=self._tolerancia,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except (jwt.PyJWTError, ValueError) as exc:
            raise TokenInvalido(type(exc).__name__) from exc

        if claims.get("role") != "authenticated" or claims.get("is_anonymous"):
            raise TokenInvalido("papel")
        try:
            usuario_id = uuid.UUID(str(claims["sub"]))
        except ValueError as exc:
            raise TokenInvalido("sub") from exc

        return Identidade(
            usuario_id=usuario_id,
            email=claims.get("email"),
            aal=str(claims.get("aal", "aal1")),
            sessao_id=claims.get("session_id"),
            expira_em=int(claims["exp"]),
            claims=claims,
        )
