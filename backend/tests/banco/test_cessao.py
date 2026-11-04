"""Sala de cessao no banco: abrir, ordem das etapas, papeis, funil do lead e documentos."""

from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal

from tests.apoio.banco import BancoDeTeste, Cenario, como
from tests.apoio.erros import erro_sql


def _lead(cenario: Cenario, t: uuid.UUID, estagio: str = "negociando") -> uuid.UUID:
    parte = cenario.parte("Credora da Cessao")
    lead = cenario.lead(t, cenario.requisicao(credor=parte), parte)
    cenario.sql("update app.leads set estagio = %s where id = %s", [estagio, lead])
    return lead


def _abrir(banco: BancoDeTeste, usuario: uuid.UUID, t: uuid.UUID, lead: uuid.UUID) -> uuid.UUID:
    with como(banco, usuario) as conn:
        return conn.execute("select app.abrir_cessao(%s, %s, %s)", [t, lead, Decimal("80000")]).fetchone()[0]


def _avancar(
    banco: BancoDeTeste, usuario: uuid.UUID, t: uuid.UUID, c: uuid.UUID, para: str, *, motivo: str | None = None
) -> None:
    with como(banco, usuario) as conn:
        conn.execute("select app.avancar_cessao(%s, %s, %s, %s)", [t, c, para, motivo])


def _estagio(cenario: Cenario, lead: uuid.UUID) -> str:
    return cenario.sql("select estagio::text from app.leads where id = %s", [lead])[0][0]


def test_abrir_exige_negociacao_e_papel(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    gestor, operador = cenario.membro(t, "gestor"), cenario.membro(t, "operador")
    with erro_sql("MQ409", "lead_fora_da_negociacao"):
        _abrir(banco, gestor, t, _lead(cenario, t, "em_contato"))
    lead = _lead(cenario, t)
    with erro_sql("MQ403", "sem_permissao"):
        _abrir(banco, operador, t, lead)
    cessao = _abrir(banco, gestor, t, lead)
    assert _estagio(cenario, lead) == "documentacao"
    with erro_sql("MQ409", "cessao_ja_aberta"):
        _abrir(banco, gestor, t, lead)
    with como(banco, gestor) as conn, erro_sql("42501"):  # status so pela funcao
        conn.execute("update app.cessoes set status = 'paga' where id = %s", [cessao])
    with como(banco, cenario.membro(cenario.tenant(), "owner")) as conn:
        assert conn.execute("select count(*) from app.cessoes where id = %s", [cessao]).fetchone() == (0,)


def test_etapas_em_ordem_com_papel_e_funil_acompanhando(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    gestor, juridico, operador = (cenario.membro(t, p) for p in ("gestor", "juridico", "operador"))
    lead = _lead(cenario, t)
    c = _abrir(banco, juridico, t, lead)

    with erro_sql("MQ409", "etapa_fora_de_ordem"):
        _avancar(banco, gestor, t, c, "escritura_assinada")
    with erro_sql("MQ422", "agenda_incompleta"):
        _avancar(banco, gestor, t, c, "escritura_agendada")
    with como(banco, operador) as conn:  # operador le, mas nao preenche a sala
        assert conn.execute("update app.cessoes set cartorio = 'x' where id = %s", [c]).rowcount == 0
    with como(banco, juridico) as conn:
        conn.execute(
            """update app.cessoes set cartorio = '1º Tabelionato de Notas', escritura_em = now() + interval '3 days',
                      link_enotariado = 'https://e-notariado.exemplo/ato/123' where id = %s""",
            [c],
        )
    with erro_sql("MQ403", "sem_permissao"):
        _avancar(banco, operador, t, c, "escritura_agendada")
    _avancar(banco, juridico, t, c, "escritura_agendada")
    assert _estagio(cenario, lead) == "escritura_agendada"
    _avancar(banco, juridico, t, c, "escritura_assinada")
    assert _estagio(cenario, lead) == "cedido"
    with erro_sql("MQ409", "cessao_nao_cancelavel"):
        _avancar(banco, gestor, t, c, "cancelada", motivo="desistiu")
    with erro_sql("MQ422", "comunicacao_incompleta"):
        _avancar(banco, juridico, t, c, "protocolada")
    with como(banco, juridico) as conn:
        conn.execute(
            """update app.cessoes set protocolo_tribunal = 'PET 123/2026', comunicado_ente_em = current_date
                where id = %s""",
            [c],
        )
    _avancar(banco, juridico, t, c, "protocolada")
    _avancar(banco, juridico, t, c, "homologada")
    with erro_sql("MQ403", "sem_permissao"):  # dar como paga: so dono ou gestor
        _avancar(banco, juridico, t, c, "paga")
    _avancar(banco, gestor, t, c, "paga")
    assert cenario.sql("select status::text, assinada_em is not null from app.cessoes where id = %s", [c]) == [
        ("paga", True)
    ]
    acoes = [
        a for (a,) in cenario.sql("select acao from app.auditoria where entidade = 'cessoes' and tenant_id = %s", [t])
    ]
    assert acoes.count("cessoes.update") >= 6


def test_cancelar_volta_o_lead_e_a_sala_pode_reabrir(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    gestor, juridico = cenario.membro(t, "gestor"), cenario.membro(t, "juridico")
    lead = _lead(cenario, t)
    c = _abrir(banco, gestor, t, lead)
    with erro_sql("MQ403", "sem_permissao"):
        _avancar(banco, juridico, t, c, "cancelada", motivo="credor desistiu")
    with erro_sql("MQ422", "motivo_obrigatorio"):
        _avancar(banco, gestor, t, c, "cancelada", motivo=" ")
    _avancar(banco, gestor, t, c, "cancelada", motivo="credor desistiu")
    assert _estagio(cenario, lead) == "negociando"
    with como(banco, juridico) as conn:  # cancelada: ninguem preenche
        assert conn.execute("update app.cessoes set cartorio = 'Cartorio X' where id = %s", [c]).rowcount == 0
    assert _abrir(banco, gestor, t, lead) == c  # uma sala por lead: reabre a mesma
    assert cenario.sql("select status::text, motivo_cancelamento from app.cessoes where id = %s", [c]) == [
        ("preparando", None)
    ]


def _documento(t: uuid.UUID, lead: uuid.UUID, usuario: uuid.UUID, **extra: object) -> dict[str, object]:
    caminho = f"{t}/{uuid.uuid4()}/{uuid.uuid4()}"
    return {
        "tenant_id": t, "lead_id": lead, "tipo": "identidade", "storage_path": caminho, "nome_arquivo": "rg.pdf",
        "mime": "application/pdf", "tamanho": 1234, "sha256": hashlib.sha256(caminho.encode()).hexdigest(),
        "status": "enviado", "enviado_por": usuario, **extra,
    }  # fmt: skip


def _inserir(banco: BancoDeTeste, usuario: uuid.UUID, doc: dict[str, object]) -> uuid.UUID:
    colunas = ", ".join(doc)
    marcadores = ", ".join(f"%({k})s" for k in doc)
    with como(banco, usuario) as conn:
        return conn.execute(
            f"insert into app.documentos ({colunas}, enviado_em) values ({marcadores}, now()) returning id",  # noqa: S608
            doc,
        ).fetchone()[0]


def test_documentos_quem_envia_revisa_e_apaga(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    operador, juridico, leitor = (cenario.membro(t, p) for p in ("operador", "juridico", "leitura"))
    lead = _lead(cenario, t)
    doc = _inserir(banco, operador, _documento(t, lead, operador))
    with erro_sql("42501"):  # leitura nao envia
        _inserir(banco, leitor, _documento(t, lead, leitor))
    with erro_sql("42501"):  # enviado_por tem de ser quem esta logado
        _inserir(banco, operador, _documento(t, lead, juridico))
    with erro_sql("42501"):  # ninguem envia ja aprovado
        _inserir(banco, operador, _documento(t, lead, operador, status="aprovado"))
    with erro_sql("23514"):  # caminho fora do formato do servidor
        _inserir(banco, operador, _documento(t, lead, operador, storage_path="../../etc/passwd"))

    revisar = (
        "update app.documentos set status = %s, observacao = %s, revisado_por = %s, revisado_em = now() where id = %s"
    )
    with como(banco, operador) as conn:  # operador nao aprova
        assert conn.execute(revisar, ["aprovado", None, operador, doc]).rowcount == 0
    with como(banco, juridico) as conn, erro_sql("23514"):  # recusa sem motivo
        conn.execute(revisar, ["rejeitado", None, juridico, doc])
    with como(banco, juridico) as conn:
        assert conn.execute(revisar, ["aprovado", None, juridico, doc]).rowcount == 1
    with como(banco, operador) as conn:  # aprovado nao se apaga
        assert conn.execute("delete from app.documentos where id = %s", [doc]).rowcount == 0

    outro = _inserir(banco, operador, _documento(t, lead, operador, tipo="outro"))
    with como(banco, leitor) as conn:
        assert conn.execute("delete from app.documentos where id = %s", [outro]).rowcount == 0
    with como(banco, operador) as conn:
        assert conn.execute("delete from app.documentos where id = %s", [outro]).rowcount == 1
    with como(banco, cenario.membro(cenario.tenant(), "owner")) as conn:
        assert conn.execute("select count(*) from app.documentos where lead_id = %s", [lead]).fetchone() == (0,)


def test_download_de_documento_entra_na_trilha(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    leitor = cenario.membro(t, "leitura")
    doc = uuid.uuid4()
    with como(banco, leitor) as conn:
        conn.execute("select app.registrar_exportacao(%s, 'documentos', %s, 'jpeg')", [t, doc])
    with como(banco, leitor) as conn, erro_sql("MQ422", "dados_invalidos"):
        conn.execute("select app.registrar_exportacao(%s, 'documentos', %s, 'exe')", [t, doc])
    assert cenario.sql("select acao, usuario_id from app.auditoria where entidade_id = %s", [str(doc)]) == [
        ("documentos.exportado", leitor)
    ]
