"""Configuracao dos workers (coleta + Leitor IA). Tudo por variavel de ambiente."""

from __future__ import annotations

from decimal import Decimal
from functools import cached_property
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.config import _decodificar_chave
from worker.leitor import Esforco

# So estes hosts podem ser consultados (evita SSRF por configuracao errada).
HOSTS_PERMITIDOS = frozenset({"comunicaapi.pje.jus.br"})
HOSTS_DO_BUREAU = frozenset({"plataforma.bigdatacorp.com.br"})

TERMOS_PADRAO = (
    "requisitório",
    "precatório",
    "requisição de pequeno valor",
    "homologo os cálculos",
    "habilitação de herdeiros",
    "cessão de crédito",
)


def _lista(valor: object) -> object:
    if isinstance(valor, str):
        return [p.strip() for p in valor.split(",") if p.strip()]
    return valor


class ConfiguracoesWorker(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAQUINA_", extra="ignore", frozen=True, env_ignore_empty=True)

    ambiente: Literal["dev", "teste", "producao"] = "dev"

    # Conexao como maquina_worker (privilegios so nas tabelas que alimenta).
    worker_database_url: SecretStr

    # DJEN (Comunica API do PJe)
    djen_url: str = "https://comunicaapi.pje.jus.br/api/v1/comunicacao"
    djen_tribunais: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["TJSP", "TRF3", "TRT2", "TRT15"])
    djen_termos: Annotated[list[str], NoDecode] = Field(default_factory=lambda: list(TERMOS_PADRAO))
    djen_intervalo_s: float = Field(default=2.0, ge=0.2)  # espaco minimo entre requisicoes (limite por IP)
    djen_itens_por_pagina: int = Field(default=100, ge=1, le=100)
    djen_max_paginas: int = Field(default=50, ge=1, le=1000)
    djen_reexecutar_dia_corrente_h: float = Field(default=3.0, gt=0)
    djen_dias_retroativos: int = Field(default=1, ge=0, le=30)  # D+1: ontem e hoje

    # Leitor IA (Claude). Desligado = so coleta e triagem; as relevantes esperam na fila.
    leitor_ativo: bool = False
    # Nome padrao da Anthropic (sem o prefixo MAQUINA_); so o worker recebe esta variavel.
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "MAQUINA_ANTHROPIC_API_KEY")
    )
    leitor_modelo: str = "claude-opus-5-5"
    leitor_esforco: Esforco = "low"
    leitor_concorrencia: int = Field(default=4, ge=1, le=32)
    leitor_orcamento_dia_usd: Decimal = Field(default=Decimal("20"), ge=0)
    leitor_max_tentativas: int = Field(default=5, ge=1, le=20)
    leitor_max_caracteres: int = Field(default=60_000, ge=1000)

    # Ciclo
    ciclo_s: float = Field(default=300.0, ge=5)
    # Mensageiro (WhatsApp e cadencias): bem mais frequente que a coleta.
    mensageiro_s: float = Field(default=15.0, ge=2)
    # Endereco publico do app (link de verificacao nas mensagens).
    url_publica: str | None = None
    # Resolver (bureau): as credenciais de cada cliente ficam cifradas no banco, nao aqui.
    resolvedor_s: float = Field(default=30.0, ge=2)
    bigdatacorp_url: str = "https://plataforma.bigdatacorp.com.br/pessoas"

    # Cifra do documento (CPF/CNPJ) das partes: as mesmas chaves da API.
    chaves_cifra: dict[int, SecretStr]
    chave_cifra_ativa: int
    chave_indice: SecretStr

    _separar_tribunais = field_validator("djen_tribunais", "djen_termos", mode="before")(_lista)

    @field_validator("djen_tribunais")
    @classmethod
    def _tribunais_validos(cls, valor: list[str]) -> list[str]:
        for t in valor:
            if not t.isalnum() or not t.isupper() or len(t) > 10:
                raise ValueError(f"tribunal invalido: {t!r}")
        return valor

    @model_validator(mode="after")
    def _validar(self) -> ConfiguracoesWorker:
        url = urlparse(self.djen_url)
        if self.ambiente != "teste" and (url.scheme != "https" or url.hostname not in HOSTS_PERMITIDOS):
            raise ValueError("djen_url precisa ser https em um host permitido")
        bureau = urlparse(self.bigdatacorp_url)
        if self.ambiente != "teste" and (bureau.scheme != "https" or bureau.hostname not in HOSTS_DO_BUREAU):
            raise ValueError("bigdatacorp_url precisa ser https em um host permitido")
        if self.url_publica:
            object.__setattr__(self, "url_publica", self.url_publica.rstrip("/"))
            if self.ambiente == "producao" and not self.url_publica.startswith("https://"):
                raise ValueError("url_publica deve usar https")
        for versao, chave in self.chaves_cifra.items():
            _decodificar_chave(chave.get_secret_value(), f"chaves_cifra[{versao}]")
        if self.chave_cifra_ativa not in self.chaves_cifra:
            raise ValueError("chave_cifra_ativa nao existe em chaves_cifra")
        if len(self.chave_indice.get_secret_value()) < 32:
            raise ValueError("chave_indice precisa de pelo menos 32 caracteres")
        if self.leitor_ativo and not self.anthropic_api_key:
            raise ValueError("leitor_ativo=true exige ANTHROPIC_API_KEY")
        return self

    @cached_property
    def chaves_cifra_bytes(self) -> dict[int, bytes]:
        return {v: _decodificar_chave(c.get_secret_value(), f"chaves_cifra[{v}]") for v, c in self.chaves_cifra.items()}
