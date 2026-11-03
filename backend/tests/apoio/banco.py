"""Infraestrutura de testes do banco.

Cria um banco descartavel por sessao de testes, aplica o stub do Supabase
(quando necessario) e as migracoes, e oferece:

* ``Cenario``: fabrica de dados de teste com IDs unicos (testes independentes).
* ``como(usuario)``: executa como a API executa em producao - conecta como
  ``maquina_api`` e assume ``authenticated``/``anon`` com as claims do JWT.
"""

from __future__ import annotations

import json
import secrets
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

RAIZ = Path(__file__).resolve().parents[3]
STUB = RAIZ / "supabase" / "tests" / "supabase_stub.sql"
MIGRACOES = sorted((RAIZ / "supabase" / "migrations").glob("*.sql"))


def trocar_banco(dsn: str, banco: str, usuario: str | None = None) -> str:
    partes = conninfo_to_dict(dsn)
    partes["dbname"] = banco
    if usuario:
        partes["user"] = usuario
        partes.pop("password", None)
    return make_conninfo(**partes)


@dataclass(frozen=True)
class BancoDeTeste:
    dsn_admin: str
    dsn_api: str
    dsn_worker: str


def criar_banco(dsn_servidor: str) -> tuple[BancoDeTeste, str]:
    nome = f"teste_{secrets.token_hex(5)}"
    with psycopg.connect(dsn_servidor, autocommit=True) as c:
        c.execute(sql.SQL("create database {}").format(sql.Identifier(nome)))

    dsn_admin = trocar_banco(dsn_servidor, nome)
    with psycopg.connect(dsn_admin, autocommit=True) as c:
        supabase_real = c.execute("select to_regclass('auth.users') is not null").fetchone()
        if not (supabase_real and supabase_real[0]):
            c.execute(STUB.read_text())
        for migracao in MIGRACOES:
            c.execute(migracao.read_text())
        # Apenas no banco de teste: em producao a senha vem do deploy.
        c.execute("alter role maquina_api login")
        c.execute("alter role maquina_worker login")

    banco = BancoDeTeste(
        dsn_admin=dsn_admin,
        dsn_api=trocar_banco(dsn_servidor, nome, "maquina_api"),
        dsn_worker=trocar_banco(dsn_servidor, nome, "maquina_worker"),
    )
    return banco, nome


def remover_banco(dsn_servidor: str, nome: str) -> None:
    with psycopg.connect(dsn_servidor, autocommit=True) as c:
        c.execute(sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(nome)))


@contextmanager
def como(
    banco: BancoDeTeste,
    usuario: uuid.UUID | None,
    *,
    ip: str | None = None,
    request_id: str | None = None,
) -> Iterator[psycopg.Connection[Any]]:
    """Abre uma transacao exatamente como a API faz para um usuario (ou anonimo)."""
    papel = "authenticated" if usuario else "anon"
    claims: dict[str, str] = {"role": papel}
    if usuario:
        claims["sub"] = str(usuario)
    with psycopg.connect(banco.dsn_api) as conn:
        conn.execute("select set_config('request.jwt.claims', %s, true)", [json.dumps(claims)])
        if ip:
            conn.execute("select set_config('maquina.ip', %s, true)", [ip])
        if request_id:
            conn.execute("select set_config('maquina.request_id', %s, true)", [request_id])
        conn.execute(sql.SQL("set local role {}").format(sql.Identifier(papel)))
        yield conn


@contextmanager
def como_worker(banco: BancoDeTeste) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(banco.dsn_worker) as conn:
        yield conn


def cnj_valido(sequencial: int, ano: int = 2019, justica: int = 8, tribunal: int = 26, origem: int = 1) -> str:
    """Gera um numero CNJ com digito verificador correto (ISO 7064 mod 97-10)."""
    base = f"{sequencial:07d}{ano:04d}{justica:01d}{tribunal:02d}{origem:04d}"
    dv = 98 - (int(base + "00") % 97)
    return f"{sequencial:07d}{dv:02d}{ano:04d}{justica:01d}{tribunal:02d}{origem:04d}"


@dataclass
class Cenario:
    """Fabrica de dados: cada chamada cria registros novos e independentes."""

    banco: BancoDeTeste
    _conn: psycopg.Connection[Any] = field(init=False)

    def __post_init__(self) -> None:
        self._conn = psycopg.connect(self.banco.dsn_admin, autocommit=True)

    def fechar(self) -> None:
        self._conn.close()

    def sql(self, consulta: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
        cur = self._conn.execute(consulta, params)  # type: ignore[arg-type]
        return cur.fetchall() if cur.description else []

    def usuario(self) -> uuid.UUID:
        uid = uuid.uuid4()
        self.sql("insert into auth.users (id, email) values (%s, %s)", [uid, f"{uid.hex[:12]}@teste.local"])
        return uid

    def tenant(
        self,
        *,
        nome: str = "Compradora Teste",
        tribunais: list[str] | None = None,
        limite_leads_mes: int | None = None,
        assentos: int = 10,
        config: dict[str, Any] | None = None,
        ativo: bool = True,
    ) -> uuid.UUID:
        linhas = self.sql(
            """insert into app.tenants (nome, cnpj, tribunais_permitidos, limite_leads_mes, assentos, config, ativo)
               values (%s, %s, %s, %s, %s, %s, %s) returning id""",
            [nome, "12345678000190", tribunais, limite_leads_mes, assentos,
             json.dumps(config or {}), ativo],
        )
        return linhas[0][0]

    def membro(
        self, tenant: uuid.UUID, papel: str = "operador", *, nome_publico: str | None = None
    ) -> uuid.UUID:
        uid = self.usuario()
        self.sql(
            "insert into app.membros (tenant_id, user_id, papel, nome_publico) values (%s, %s, %s, %s)",
            [tenant, uid, papel, nome_publico],
        )
        return uid

    def ente(self, nome: str | None = None, esfera: str = "estadual", uf: str | None = "SP") -> uuid.UUID:
        nome = nome or f"Ente {secrets.token_hex(3)}"
        return self.sql(
            """insert into core.entes_devedores (nome, esfera, uf, prazo_medio_meses) values (%s, %s, %s, 36)
               on conflict (nome, esfera, uf) do update set nome = excluded.nome returning id""",
            [nome, esfera, uf],
        )[0][0]

    def parte(self, nome: str = "Credora Teste") -> uuid.UUID:
        return self.sql("insert into core.partes (nome, tipo) values (%s, 'pf') returning id", [nome])[0][0]

    def processo(self, tribunal: str = "TJSP", *, sigiloso: bool = False) -> str:
        numero = cnj_valido(secrets.randbelow(9_999_999))
        self.sql(
            "insert into core.processos (numero_cnj, tribunal, sigiloso) values (%s, %s, %s) on conflict do nothing",
            [numero, tribunal, sigiloso],
        )
        return numero

    def requisicao(
        self,
        *,
        tribunal: str = "TJSP",
        credor: uuid.UUID | None = None,
        processo: str | None = None,
        ente: uuid.UUID | None = None,
        valor: Decimal = Decimal("150000.00"),
        status: str = "inscrito",
        ja_cedido: bool = False,
        superpreferencia: bool = False,
    ) -> uuid.UUID:
        return self.sql(
            """insert into core.requisicoes
                 (tribunal, numero_requisicao, processo_origem, credor_parte_id, ente_id, valor_face,
                  data_base, status, ja_cedido, superpreferencia, natureza, ano_loa)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'alimentar', 2027) returning id""",
            [tribunal, f"REQ-{secrets.token_hex(6)}", processo, credor, ente, valor,
             date(2024, 1, 1), status, ja_cedido, superpreferencia],
        )[0][0]

    def vincular(self, processo: str, parte: uuid.UUID, papel: str = "credor") -> None:
        self.sql(
            "insert into core.processo_partes (numero_cnj, parte_id, polo, papel) values (%s, %s, 'ativo', %s)",
            [processo, parte, papel],
        )

    def lead(self, tenant: uuid.UUID, requisicao: uuid.UUID, credor: uuid.UUID | None = None) -> uuid.UUID:
        return self.sql(
            "insert into app.leads (tenant_id, requisicao_id, credor_parte_id) values (%s, %s, %s) returning id",
            [tenant, requisicao, credor],
        )[0][0]

    def contato(
        self, tenant: uuid.UUID, parte: uuid.UUID, *, opt_out: bool = False, cifrado: bytes = b"\x01cifrado"
    ) -> uuid.UUID:
        return self.sql(
            """insert into app.contatos (tenant_id, parte_id, tipo, valor_cifrado, valor_hash, origem, opt_out_em)
               values (%s, %s, 'telefone', %s, %s, 'bureau', case when %s then now() end) returning id""",
            [tenant, parte, cifrado, secrets.token_hex(16), opt_out],
        )[0][0]
