"""Resolver: achar telefone, e-mail e endereco do credor.

- Bureau por API: a API so grava o pedido; o worker consulta (credenciais
  cifradas, so o worker decifra) e grava os contatos.
- Planilha: a gestao exporta os CPFs (auditado) para rodar em qualquer bureau
  e importa o resultado, que so casa com credores dos proprios leads.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Request, Response
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.auth.sessao import Rec, Usuario
from app.consultas.tenant import carregar_tenant
from app.dominio import contatos
from app.dominio.contatos import TipoContato
from app.dominio.documento import cpf_valido
from app.dominio.funil import Estagio
from app.dominio.integracoes import contexto_segredos
from app.erros import ErroApi
from app.recursos import aplicar_limite, contexto
from app.rotas.espaco import hoje
from app.seguranca.cripto import FalhaDeCifra, indice_cego
from app.seguranca.limite import ENRIQUECER_POR_USUARIO, PLANILHA_POR_USUARIO

router = APIRouter(prefix="/api/t/{tenant_id}", tags=["resolver"])

Segredo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=8, max_length=512)]
INICIO_DO_DIA_SP = "date_trunc('day', now() at time zone 'America/Sao_Paulo') at time zone 'America/Sao_Paulo'"
CABECALHO_CSV = ["referencia", "nome", "cpf"]


def _gestao(papel: str) -> None:
    if papel not in ("owner", "gestor"):
        raise ErroApi(403, "sem_permissao")


def _so_digitos(valor: str) -> str:
    return "".join(c for c in valor if c.isdigit())


# --------------------------------------------------------------------- integracao com o bureau
class ConfigBureau(BaseModel):
    model_config = ConfigDict(extra="forbid")
    automatico: bool = False
    limite_dia: int = Field(default=50, ge=1, le=5000)
    # Credenciais: obrigatorias na primeira vez; depois, so para trocar.
    access_token: Segredo | None = None
    token_id: Segredo | None = None

    def publica(self) -> dict[str, Any]:
        return {"provedor": "bigdatacorp", "automatico": self.automatico, "limite_dia": self.limite_dia}


class IntegracaoBureau(BaseModel):
    configurada: bool
    ativa: bool
    provedor: str
    automatico: bool
    limite_dia: int
    uso_hoje: int
    erro: str | None
    atualizado_em: datetime | None


async def _uso_hoje(conn: Any, tenant_id: uuid.UUID) -> int:
    cursor = await conn.execute(
        f"""select count(*) as n from app.enriquecimentos
             where tenant_id = %s and status in ('concluido', 'sem_resultado', 'falhou')
               and concluido_em >= {INICIO_DO_DIA_SP}""",  # noqa: S608 - expressao fixa, sem entrada externa
        [tenant_id],
    )
    linha = await cursor.fetchone()
    return int(linha["n"]) if linha else 0


async def _bureau(conn: Any, tenant_id: uuid.UUID) -> IntegracaoBureau:
    cursor = await conn.execute(
        "select ativa, config, erro, atualizado_em from app.integracoes where tenant_id = %s and tipo = 'bureau'",
        [tenant_id],
    )
    linha = await cursor.fetchone()
    config = (linha or {}).get("config") or {}
    return IntegracaoBureau(
        configurada=linha is not None,
        ativa=bool(linha and linha["ativa"]),
        provedor=str(config.get("provedor") or "bigdatacorp"),
        automatico=bool(config.get("automatico")),
        limite_dia=int(config.get("limite_dia") or 50),
        uso_hoje=await _uso_hoje(conn, tenant_id),
        erro=linha["erro"] if linha else None,
        atualizado_em=linha["atualizado_em"] if linha else None,
    )


@router.get("/integracoes/bureau")
async def ver_bureau(tenant_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec) -> IntegracaoBureau:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        _gestao((await carregar_tenant(conn, tenant_id)).papel)
        return await _bureau(conn, tenant_id)


@router.put("/integracoes/bureau", status_code=204)
async def salvar_bureau(
    tenant_id: uuid.UUID, dados: ConfigBureau, usuario: Usuario, request: Request, rec: Rec
) -> None:
    if (dados.access_token is None) != (dados.token_id is None):
        raise ErroApi(422, "dados_invalidos", "access_token e token_id vao juntos")
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        _gestao((await carregar_tenant(conn, tenant_id)).papel)
        if dados.access_token and dados.token_id:
            segredos = rec.cifra.cifrar(
                json.dumps({"access_token": dados.access_token, "token_id": dados.token_id}),
                contexto_segredos(tenant_id, "bureau"),
            )
            await conn.execute(
                "select app.salvar_integracao(%s, 'bureau', %s, %s)", [tenant_id, Jsonb(dados.publica()), segredos]
            )
        else:
            await conn.execute(
                "select app.atualizar_config_integracao(%s, 'bureau', %s)", [tenant_id, Jsonb(dados.publica())]
            )


@router.delete("/integracoes/bureau", status_code=204)
async def desligar_bureau(tenant_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec) -> None:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        await conn.execute("select app.desligar_integracao(%s, 'bureau')", [tenant_id])


# --------------------------------------------------------------------- por lead
class DocumentoInformado(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cpf: Annotated[str, StringConstraints(strip_whitespace=True, min_length=11, max_length=14)]


@router.put("/leads/{lead_id}/documento", status_code=204)
async def informar_documento(
    tenant_id: uuid.UUID, lead_id: uuid.UUID, dados: DocumentoInformado, usuario: Usuario, request: Request, rec: Rec
) -> None:
    """CPF que a equipe achou nos autos: cifrado com o contexto do cliente; ninguem le de volta pela tela."""
    cpf = _so_digitos(dados.cpf)
    if not cpf_valido(cpf):
        raise ErroApi(422, "cpf_invalido")
    chave_indice = rec.config.chave_indice.get_secret_value().encode()
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        (await carregar_tenant(conn, tenant_id)).exigir_editor()
        cursor = await conn.execute("select 1 from app.leads where tenant_id = %s and id = %s", [tenant_id, lead_id])
        if await cursor.fetchone() is None:
            raise ErroApi(404, "lead_nao_encontrado")
        # Atualiza ou insere sem "on conflict": o upsert exigiria ler as colunas cifradas, que o front nao le.
        valores = {
            "t": tenant_id,
            "l": lead_id,
            "c": rec.cifra.cifrar(cpf, contatos.contexto_do_documento_informado(tenant_id)),
            "h": indice_cego(chave_indice, "cpf", cpf),
            "u": usuario.usuario_id,
        }
        cursor = await conn.execute(
            """update app.documentos_informados
                  set documento_cifrado = %(c)s, documento_hash = %(h)s, informado_por = %(u)s, informado_em = now()
                where tenant_id = %(t)s and lead_id = %(l)s""",
            valores,
        )
        if cursor.rowcount == 0:
            await conn.execute(
                """insert into app.documentos_informados
                     (tenant_id, lead_id, documento_cifrado, documento_hash, informado_por)
                   values (%(t)s, %(l)s, %(c)s, %(h)s, %(u)s)""",
                valores,
            )


@router.post("/leads/{lead_id}/enriquecer", status_code=202)
async def pedir_enriquecimento(
    tenant_id: uuid.UUID, lead_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec
) -> dict[str, object]:
    await aplicar_limite(rec, ENRIQUECER_POR_USUARIO, str(usuario.usuario_id))
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        (await carregar_tenant(conn, tenant_id)).exigir_editor()
        cursor = await conn.execute(
            """select coalesce(p.tem_documento, false) or d.lead_id is not null as tem_cpf,
                      coalesce(i.ativa, false) as bureau_ativo,
                      exists (select 1 from app.enriquecimentos e
                               where e.tenant_id = l.tenant_id and e.lead_id = l.id and e.status = 'pendente') as aberto
                 from app.leads l
                 left join core.partes p on p.id = l.credor_parte_id
                 left join app.documentos_informados d on d.tenant_id = l.tenant_id and d.lead_id = l.id
                 left join app.integracoes i on i.tenant_id = l.tenant_id and i.tipo = 'bureau'
                where l.tenant_id = %s and l.id = %s""",
            [tenant_id, lead_id],
        )
        alvo = await cursor.fetchone()
        if alvo is None:
            raise ErroApi(404, "lead_nao_encontrado")
        if not alvo["bureau_ativo"]:
            raise ErroApi(409, "bureau_desligado")
        if not alvo["tem_cpf"]:
            raise ErroApi(422, "credor_sem_cpf")
        if alvo["aberto"]:
            raise ErroApi(409, "enriquecimento_em_andamento")
        cursor = await conn.execute(
            "insert into app.enriquecimentos (tenant_id, lead_id, pedido_por) values (%s, %s, %s) returning id",
            [tenant_id, lead_id, usuario.usuario_id],
        )
        linha = await cursor.fetchone()
    return {"id": linha["id"] if linha else None, "status": "pendente"}


# --------------------------------------------------------------------- painel do resolver
class PedidoDeEnriquecimento(BaseModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    credor: str | None
    status: str
    achados: dict[str, int] | None
    erro: str | None
    automatico: bool
    criado_em: datetime
    concluido_em: datetime | None


class PainelResolver(BaseModel):
    bureau: IntegracaoBureau | None
    pode_gerir: bool
    leads_sem_telefone: int
    leads_sem_cpf: int
    recentes: list[PedidoDeEnriquecimento]


@router.get("/resolver")
async def painel_resolver(tenant_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec) -> PainelResolver:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        cursor = await conn.execute(
            """select e.id, e.lead_id, p.nome as credor, e.status, e.achados, e.erro,
                      e.pedido_por is null as automatico, e.criado_em, e.concluido_em
                 from app.enriquecimentos e
                 join app.leads l on l.tenant_id = e.tenant_id and l.id = e.lead_id
                 left join core.partes p on p.id = l.credor_parte_id
                where e.tenant_id = %s
                order by e.criado_em desc
                limit 30""",
            [tenant_id],
        )
        recentes = [PedidoDeEnriquecimento.model_validate(linha) for linha in await cursor.fetchall()]
        cursor = await conn.execute(
            """select count(*) filter (where not exists (
                        select 1 from app.contatos c where c.tenant_id = l.tenant_id and c.parte_id = l.credor_parte_id
                           and c.tipo = 'telefone' and c.opt_out_em is null)) as sem_telefone,
                      count(*) filter (where not coalesce(p.tem_documento, false) and not exists (
                        select 1 from app.documentos_informados d
                         where d.tenant_id = l.tenant_id and d.lead_id = l.id)) as sem_cpf
                 from app.leads l
                 left join core.partes p on p.id = l.credor_parte_id
                where l.tenant_id = %s and l.estagio in ('novo', 'qualificado', 'enriquecido', 'em_contato')""",
            [tenant_id],
        )
        contas = await cursor.fetchone() or {}
        bureau = await _bureau(conn, tenant_id)
    gestao = tenant.papel in ("owner", "gestor")
    return PainelResolver(
        bureau=bureau if bureau.configurada else None,
        pode_gerir=gestao,
        leads_sem_telefone=int(contas.get("sem_telefone") or 0),
        leads_sem_cpf=int(contas.get("sem_cpf") or 0),
        recentes=recentes,
    )


# --------------------------------------------------------------------- planilha do bureau
def _estagios_padrao() -> list[Estagio]:
    return ["qualificado"]


class PedidoExportacao(BaseModel):
    model_config = ConfigDict(extra="forbid")
    estagios: list[Estagio] = Field(default_factory=_estagios_padrao, min_length=1, max_length=12)
    so_sem_telefone: bool = True
    limite: int = Field(default=500, ge=1, le=2000)


def celula_segura(valor: str) -> str:
    """Planilha aberta no Excel nao executa formula vinda de texto externo (nome do credor)."""
    return "'" + valor if valor[:1] in ("=", "+", "-", "@", "\t", "\r") else valor


@router.post("/resolver/exportacao")
async def exportar_para_bureau(
    tenant_id: uuid.UUID, dados: PedidoExportacao, usuario: Usuario, request: Request, rec: Rec
) -> Response:
    """CSV com referencia do lead, nome e CPF. A funcao do banco confere o papel e grava na auditoria."""
    await aplicar_limite(rec, PLANILHA_POR_USUARIO, str(usuario.usuario_id))
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        cursor = await conn.execute(
            "select * from app.documentos_para_bureau(%s, %s, %s, %s)",
            [tenant_id, list(dados.estagios), dados.so_sem_telefone, dados.limite],
        )
        linhas = await cursor.fetchall()
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";", lineterminator="\r\n")
    escritor.writerow(CABECALHO_CSV)
    for linha in linhas:
        try:
            cpf = rec.cifra.decifrar(bytes(linha["documento_cifrado"]), linha["contexto"])
        except FalhaDeCifra:
            continue  # documento ilegivel nao derruba a exportacao
        escritor.writerow([str(linha["lead_id"]), celula_segura(linha["credor"] or ""), cpf])
    nome = f"credores-para-bureau-{hoje().isoformat()}.csv"
    return Response(
        content="﻿" + saida.getvalue(),  # BOM: o Excel abre com acentos certos
        media_type="text/csv; charset=utf-8",
        headers={"content-disposition": f'attachment; filename="{nome}"', "cache-control": "no-store"},
    )


Item = Annotated[str, StringConstraints(strip_whitespace=True, max_length=600)]


class LinhaImportada(BaseModel):
    model_config = ConfigDict(extra="forbid")
    referencia: uuid.UUID | None = None
    cpf: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] | None = None
    telefones: list[Item] = Field(default_factory=list, max_length=10)
    emails: list[Item] = Field(default_factory=list, max_length=5)
    enderecos: list[Item] = Field(default_factory=list, max_length=5)


class Importacao(BaseModel):
    model_config = ConfigDict(extra="forbid")
    linhas: list[LinhaImportada] = Field(min_length=1, max_length=100)


class ResultadoImportacao(BaseModel):
    linhas: int
    casadas: int
    sem_lead: int
    contatos_novos: int
    repetidos: int
    invalidos: int
    leads_enriquecidos: int


@router.post("/resolver/importacao")
async def importar_do_bureau(
    tenant_id: uuid.UUID, dados: Importacao, usuario: Usuario, request: Request, rec: Rec
) -> ResultadoImportacao:
    """Recebe a planilha em lotes de ate 100 linhas (o navegador le o arquivo e manda em partes)."""
    await aplicar_limite(rec, PLANILHA_POR_USUARIO, str(usuario.usuario_id))
    chave_indice = rec.config.chave_indice.get_secret_value().encode()
    cpfs = {i: _so_digitos(linha.cpf or "") for i, linha in enumerate(dados.linhas)}
    hashes = {i: indice_cego(chave_indice, "cpf", c) for i, c in cpfs.items() if cpf_valido(c)}
    resultado = ResultadoImportacao(
        linhas=len(dados.linhas), casadas=0, sem_lead=0, contatos_novos=0, repetidos=0, invalidos=0,
        leads_enriquecidos=0,
    )  # fmt: skip
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        _gestao((await carregar_tenant(conn, tenant_id)).papel)
        por_hash: dict[str, uuid.UUID] = {}
        partes: dict[uuid.UUID, uuid.UUID] = {}
        if hashes:
            cursor = await conn.execute(
                "select lead_id, parte_id, documento_hash from app.leads_por_documento(%s, %s)",
                [tenant_id, sorted(set(hashes.values()))],
            )
            for linha in await cursor.fetchall():
                por_hash[linha["documento_hash"]] = linha["lead_id"]
                partes[linha["lead_id"]] = linha["parte_id"]
        referencias = sorted({str(linha.referencia) for linha in dados.linhas if linha.referencia})
        if referencias:
            cursor = await conn.execute(
                """select id, credor_parte_id from app.leads
                    where tenant_id = %s and id = any(%s::uuid[]) and credor_parte_id is not null""",
                [tenant_id, referencias],
            )
            partes.update({linha["id"]: linha["credor_parte_id"] for linha in await cursor.fetchall()})

        com_novos: set[uuid.UUID] = set()
        for i, linha in enumerate(dados.linhas):
            lead = linha.referencia if linha.referencia in partes else por_hash.get(hashes.get(i, ""))
            if lead is None:
                resultado.sem_lead += 1
                continue
            resultado.casadas += 1
            brutos: list[tuple[TipoContato, str]] = [("telefone", v) for v in linha.telefones]
            brutos += [("email", v) for v in linha.emails] + [("endereco", v) for v in linha.enderecos]
            for tipo, valor in brutos:
                contato = contatos.normalizar(tipo, valor)
                if contato is None:
                    resultado.invalidos += 1
                    continue
                try:
                    # Savepoint por contato: repetido (mesmo indice cego) so conta como repetido.
                    # Sem "on conflict": ele exigiria ler o indice cego, que o front nao le.
                    async with conn.transaction():
                        await conn.execute(
                            """insert into app.contatos
                                 (tenant_id, parte_id, tipo, valor_cifrado, valor_hash, origem, base_legal)
                               values (%s, %s, %s, %s, %s, 'bureau', 'legitimo_interesse')""",
                            [
                                tenant_id,
                                partes[lead],
                                contato.tipo,
                                rec.cifra.cifrar(contato.valor, contatos.contexto_da_cifra(tenant_id, contato.tipo)),
                                indice_cego(chave_indice, contato.tipo, contato.comparavel),
                            ],
                        )
                except UniqueViolation:
                    resultado.repetidos += 1
                    continue
                resultado.contatos_novos += 1
                com_novos.add(lead)
        if com_novos:
            cursor = await conn.execute(
                """update app.leads set estagio = 'enriquecido'
                    where tenant_id = %s and id = any(%s::uuid[]) and estagio = 'qualificado'""",
                [tenant_id, [str(lead) for lead in com_novos]],
            )
            resultado.leads_enriquecidos = cursor.rowcount
    return resultado
