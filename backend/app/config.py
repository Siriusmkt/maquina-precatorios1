"""Configuracao da aplicacao.

Tudo vem de variaveis de ambiente com prefixo ``MAQUINA_`` (nunca de arquivo
versionado). Em producao a validacao e estrita: a aplicacao nao sobe com
configuracao insegura.
"""

from __future__ import annotations

import base64
import binascii
from functools import cached_property
from ipaddress import IPv4Network, IPv6Network, ip_network
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Rede = IPv4Network | IPv6Network


def _decodificar_chave(valor: str, nome: str) -> bytes:
    try:
        chave = base64.b64decode(valor, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{nome}: base64 invalido") from exc
    if len(chave) != 32:
        raise ValueError(f"{nome}: precisa ter 32 bytes (256 bits)")
    return chave


class Configuracoes(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAQUINA_", extra="ignore", frozen=True)

    ambiente: Literal["dev", "teste", "producao"] = "dev"
    nome_servico: str = "maquina-api"

    # Banco: a API conecta como maquina_api (NOINHERIT); a RLS faz o resto.
    database_url: SecretStr
    db_pool_min: int = 2
    db_pool_max: int = 20
    # Poolers em modo transacao (Supavisor/PgBouncer) nao suportam prepared statements.
    db_prepared_statements: bool = False

    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")

    # Supabase Auth (GoTrue)
    supabase_url: str
    supabase_anon_key: SecretStr
    # HS256 (Supabase self-hosted). Vazio = chaves assimetricas via JWKS (Supabase Cloud).
    supabase_jwt_secret: SecretStr | None = None
    jwt_audiencia: str = "authenticated"
    jwt_tolerancia_segundos: int = 10

    # Navegador
    origens_permitidas: Annotated[list[str], NoDecode] = Field(default_factory=list)
    cookie_seguro: bool = True
    sessao_renovacao_dias: int = 7

    # Rede: X-Forwarded-For so e aceito destes proxies (ex.: Traefik do Easypanel).
    proxies_confiaveis: Annotated[list[Rede], NoDecode] = Field(default_factory=list)

    # Limites
    corpo_max_bytes: int = 64 * 1024

    # Criptografia de campos (AES-256-GCM) com rotacao: {"1": "<base64 32 bytes>", ...}
    chaves_cifra: dict[int, SecretStr]
    chave_cifra_ativa: int
    # Chave do HMAC para indices cegos (CPF, telefone): nunca reutilizar a de cifra.
    chave_indice: SecretStr

    @field_validator("origens_permitidas", mode="before")
    @classmethod
    def _separar_origens(cls, valor: object) -> object:
        if isinstance(valor, str):
            return [o.strip().rstrip("/") for o in valor.split(",") if o.strip()]
        return valor

    @field_validator("proxies_confiaveis", mode="before")
    @classmethod
    def _separar_redes(cls, valor: object) -> object:
        if isinstance(valor, str):
            return [ip_network(r.strip(), strict=False) for r in valor.split(",") if r.strip()]
        return valor

    @field_validator("supabase_url")
    @classmethod
    def _sem_barra_final(cls, valor: str) -> str:
        return valor.rstrip("/")

    @model_validator(mode="after")
    def _validar(self) -> Configuracoes:
        for versao, chave in self.chaves_cifra.items():
            _decodificar_chave(chave.get_secret_value(), f"chaves_cifra[{versao}]")
        if self.chave_cifra_ativa not in self.chaves_cifra:
            raise ValueError("chave_cifra_ativa nao existe em chaves_cifra")
        if len(self.chave_indice.get_secret_value()) < 32:
            raise ValueError("chave_indice precisa de pelo menos 32 caracteres")

        if self.ambiente == "producao":
            problemas = []
            if not self.cookie_seguro:
                problemas.append("cookie_seguro deve ser true")
            if not self.origens_permitidas:
                problemas.append("origens_permitidas vazio")
            if any(not o.startswith("https://") for o in self.origens_permitidas):
                problemas.append("origens_permitidas deve usar https")
            if not self.supabase_url.startswith("https://"):
                problemas.append("supabase_url deve usar https")
            segredo = self.supabase_jwt_secret
            if segredo is not None and len(segredo.get_secret_value()) < 32:
                problemas.append("supabase_jwt_secret curto demais")
            if problemas:
                raise ValueError("configuracao insegura para producao: " + "; ".join(problemas))
        return self

    @property
    def producao(self) -> bool:
        return self.ambiente == "producao"

    @cached_property
    def chaves_cifra_bytes(self) -> dict[int, bytes]:
        return {
            versao: _decodificar_chave(chave.get_secret_value(), f"chaves_cifra[{versao}]")
            for versao, chave in self.chaves_cifra.items()
        }
