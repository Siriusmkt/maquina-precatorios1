"""Equipe e preferencias do cliente: /api/t/{tenant_id}/equipe e /preferencias.

As regras (quem convida, quem mexe em owner, sempre sobra um owner, faixas
das preferencias) moram nas funcoes do banco; aqui ficam o convite pelo Auth
e as mensagens para a tela.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.auth.gotrue import AuthIndisponivel, AuthLimitado, JaRegistrado
from app.auth.sessao import Rec, Usuario
from app.banco.sessao import Conexao
from app.consultas.tenant import PADROES_PRECO, PRAZO_PADRAO_MESES, carregar_tenant
from app.erros import ErroApi
from app.recursos import aplicar_limite, contexto
from app.seguranca.cripto import normalizar_email
from app.seguranca.limite import CONVITE_POR_USUARIO

router = APIRouter(prefix="/api/t/{tenant_id}", tags=["equipe"])

Papel = Literal["owner", "gestor", "operador", "juridico", "leitura"]
Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=3, max_length=254, pattern=r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}$"
    ),
]
NomePublico = Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)]

# O mesmo texto padrao da funcao app.consultar_verificacao (pagina publica do credor).
AVISO_PADRAO = (
    "Nunca cobramos nenhum valor do credor: nem taxa, nem adiantamento, nem custas. "
    "Se alguém pedir dinheiro em nosso nome, é golpe."
)


class Membro(BaseModel):
    user_id: uuid.UUID
    email: str
    papel: Papel
    nome_publico: str | None
    criado_em: datetime
    ultimo_acesso_em: datetime | None
    convite_pendente: bool
    sou_eu: bool = False


class Equipe(BaseModel):
    membros: list[Membro]
    assentos: int
    meu_papel: Papel


class NovoMembro(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: Email
    papel: Papel = "operador"
    nome_publico: NomePublico | None = None


class MudarMembro(BaseModel):
    model_config = ConfigDict(extra="forbid")
    papel: Papel | None = None
    nome_publico: NomePublico | None = None


async def _equipe(conn: Conexao, tenant_id: uuid.UUID, eu: uuid.UUID) -> list[Membro]:
    cursor = await conn.execute(
        """select user_id, email, papel::text as papel, nome_publico, criado_em, ultimo_acesso_em, convite_pendente
             from app.equipe(%s)""",
        [tenant_id],
    )
    return [Membro.model_validate({**linha, "sou_eu": linha["user_id"] == eu}) for linha in await cursor.fetchall()]


@router.get("/equipe")
async def listar(tenant_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec) -> Equipe:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        cursor = await conn.execute("select assentos from app.tenants where id = %s", [tenant_id])
        linha = await cursor.fetchone()
        membros = await _equipe(conn, tenant_id, usuario.usuario_id)
    return Equipe(membros=membros, assentos=linha["assentos"] if linha else 0, meu_papel=tenant.papel)


@router.post("/equipe", status_code=201)
async def convidar(tenant_id: uuid.UUID, dados: NovoMembro, usuario: Usuario, request: Request, rec: Rec) -> Membro:
    """Adiciona quem ja tem conta; para quem nao tem, cria a conta e manda o convite por e-mail.

    A resposta e a mesma nos dois casos: a tela nao revela se o e-mail ja existia no sistema.
    """
    await aplicar_limite(rec, CONVITE_POR_USUARIO, str(usuario.usuario_id))
    email = normalizar_email(dados.email)
    ctx = contexto(request)

    async def adicionar() -> uuid.UUID | None:
        async with rec.banco.como_usuario(usuario, ctx) as conn:
            await carregar_tenant(conn, tenant_id)
            cursor = await conn.execute(
                "select app.adicionar_membro(%s, %s, %s::app.papel_membro, %s) as id",
                [tenant_id, email, dados.papel, dados.nome_publico],
            )
            linha = await cursor.fetchone()
            return linha["id"] if linha else None

    novo = await adicionar()
    if novo is None:
        if not rec.auth.convites_ligados:
            raise ErroApi(503, "convites_indisponiveis")
        destino = f"{rec.config.url_publica}/definir-senha" if rec.config.url_publica else None
        try:
            await rec.auth.convidar(email, destino)
        except JaRegistrado:
            pass  # outro convite criou a conta entre as duas chamadas: segue o fluxo
        except AuthLimitado as exc:
            raise ErroApi(429, "limite_excedido", cabecalhos={"Retry-After": "60"}) from exc
        except AuthIndisponivel as exc:
            raise ErroApi(503, "servico_indisponivel") from exc
        novo = await adicionar()
        if novo is None:  # pragma: no cover - o Auth confirmou a conta, ela tem de existir
            raise ErroApi(503, "servico_indisponivel")

    async with rec.banco.como_usuario(usuario, ctx) as conn:
        membros = await _equipe(conn, tenant_id, usuario.usuario_id)
    return next(m for m in membros if m.user_id == novo)


@router.patch("/equipe/{user_id}", status_code=204)
async def alterar(
    tenant_id: uuid.UUID, user_id: uuid.UUID, dados: MudarMembro, usuario: Usuario, request: Request, rec: Rec
) -> None:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        await conn.execute(
            "select app.alterar_membro(%s, %s, %s::app.papel_membro, %s)",
            [tenant_id, user_id, dados.papel, dados.nome_publico],
        )


@router.delete("/equipe/{user_id}", status_code=204)
async def remover(tenant_id: uuid.UUID, user_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec) -> None:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        await conn.execute("select app.remover_membro(%s, %s)", [tenant_id, user_id])


# --------------------------------------------------------------------- preferencias
class Preferencias(BaseModel):
    taxa_alvo_aa: Decimal
    tir_minima_aa: Decimal
    ipca_aa: Decimal
    juros_aa: Decimal
    aliquota_ir: Decimal
    prazo_padrao_meses: int
    alcada_gestor_desagio_min: Decimal | None
    nao_cobramos_texto: str
    personalizadas: list[str]
    pode_editar: bool
    pode_editar_alcada: bool


class MudarPreferencias(BaseModel):
    """Faixas espelham a funcao do banco (que valida de novo). null volta ao padrao."""

    model_config = ConfigDict(extra="forbid")
    taxa_alvo_aa: Decimal | None = Field(default=None, ge=Decimal("0.05"), le=Decimal("0.80"))
    tir_minima_aa: Decimal | None = Field(default=None, ge=0, le=Decimal("0.80"))
    ipca_aa: Decimal | None = Field(default=None, ge=0, le=Decimal("0.25"))
    juros_aa: Decimal | None = Field(default=None, ge=0, le=Decimal("0.25"))
    aliquota_ir: Decimal | None = Field(default=None, ge=0, le=Decimal("0.30"))
    prazo_padrao_meses: int | None = Field(default=None, ge=1, le=240)
    alcada_gestor_desagio_min: Decimal | None = Field(default=None, ge=0, le=95)
    nao_cobramos_texto: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=300)] | None
    ) = None


def _preferencias(config: dict[str, Any], papel: str) -> Preferencias:
    def numero(chave: str, padrao: Decimal) -> Decimal:
        bruto = config.get(chave)
        return Decimal(str(bruto)) if bruto is not None else padrao

    alcada = config.get("alcada_gestor_desagio_min")
    return Preferencias(
        **{chave: numero(chave, padrao) for chave, padrao in PADROES_PRECO.items()},
        prazo_padrao_meses=int(numero("prazo_padrao_meses", PRAZO_PADRAO_MESES)),
        alcada_gestor_desagio_min=Decimal(str(alcada)) if alcada is not None else None,
        nao_cobramos_texto=str(config.get("nao_cobramos_texto") or AVISO_PADRAO),
        personalizadas=sorted(k for k in config if k in MudarPreferencias.model_fields),
        pode_editar=papel in ("owner", "gestor"),
        pode_editar_alcada=papel == "owner",
    )


@router.get("/preferencias")
async def ler_preferencias(tenant_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec) -> Preferencias:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
    return _preferencias(tenant.config, tenant.papel)


@router.patch("/preferencias")
async def mudar_preferencias(
    tenant_id: uuid.UUID, dados: MudarPreferencias, usuario: Usuario, request: Request, rec: Rec
) -> Preferencias:
    # So os campos enviados; null explicito volta ao padrao do sistema.
    valores = {
        chave: (float(valor) if isinstance(valor, Decimal) else valor)
        for chave, valor in dados.model_dump(exclude_unset=True).items()
    }
    if not valores:
        raise ErroApi(422, "dados_invalidos", "Nada para alterar.")
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        cursor = await conn.execute("select app.atualizar_preferencias(%s, %s) as config", [tenant_id, Jsonb(valores)])
        linha = await cursor.fetchone()
    return _preferencias(linha["config"] if linha else {}, tenant.papel)
