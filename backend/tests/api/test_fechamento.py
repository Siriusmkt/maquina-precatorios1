"""Sala de cessao pela API: abrir, documentos cifrados no Storage, revisao, etapas e minuta."""

from __future__ import annotations

import base64
import hashlib
import io
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from pypdf import PdfReader

from app.dominio import cessao as regras
from app.dominio.contatos import CONTEXTO_DOCUMENTO_DO_PROCESSO
from app.main import criar_app
from app.seguranca.cripto import CifraDeCampo, indice_cego
from tests.api.conftest import Ambiente
from tests.apoio.api import CHAVE_CIFRA, ORIGEM, GoTrueFalso, Navegador, config_de_teste, emitir_jwt
from tests.apoio.banco import BancoDeTeste, Cenario
from tests.apoio.worker import cpf_sintetico

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def _logar(navegador: Navegador, usuario: uuid.UUID) -> None:
    navegador.http.cookies.set("__Host-mq_sessao", emitir_jwt(usuario))
    await navegador.preparar_csrf()


def _lead_em_negociacao(cenario: Cenario, tenant: uuid.UUID, *, cpf: str | None = None) -> uuid.UUID:
    parte = cenario.parte("Credora da Sala")
    if cpf:
        cifra = CifraDeCampo({1: base64.b64decode(CHAVE_CIFRA)}, 1)
        cenario.sql(
            "update core.partes set documento_hash = %s, documento_cifrado = %s where id = %s",
            [indice_cego(b"i" * 40, "cpf", cpf), cifra.cifrar(cpf, CONTEXTO_DOCUMENTO_DO_PROCESSO), parte],
        )
    lead = cenario.lead(tenant, cenario.requisicao(credor=parte, valor=Decimal("150000")), parte)
    cenario.sql("update app.leads set estagio = 'negociando' where id = %s", [lead])
    return lead


async def _enviar(
    navegador: Navegador, tenant: uuid.UUID, sala: str, tipo: str, conteudo: bytes = PDF
) -> httpx.Response:
    return await navegador.http.post(
        f"/api/t/{tenant}/cessoes/{sala}/documentos",
        params={"tipo": tipo, "nome": f"{tipo}.pdf"},
        content=conteudo,
        headers={"content-type": "application/pdf"},
    )


async def test_sala_do_envio_a_escritura(ambiente: Ambiente, cenario: Cenario) -> None:
    navegador, storage = ambiente.navegador, ambiente.gotrue.arquivos
    tenant = cenario.tenant(nome="Compradora da Sala")
    gestor, juridico, operador, leitor = (
        cenario.membro(tenant, p) for p in ("gestor", "juridico", "operador", "leitura")
    )
    cpf = cpf_sintetico()
    lead = _lead_em_negociacao(cenario, tenant, cpf=cpf)

    await _logar(navegador, operador)
    assert (
        await navegador.http.post(f"/api/t/{tenant}/leads/{lead}/cessao", json={"valor": "90000"})
    ).status_code == 403
    await _logar(navegador, gestor)
    aberta = await navegador.http.post(f"/api/t/{tenant}/leads/{lead}/cessao", json={"valor": "90000.00"})
    assert aberta.status_code == 201
    sala = aberta.json()["id"]
    detalhe = (await navegador.http.get(f"/api/t/{tenant}/leads/{lead}")).json()
    assert (detalhe["lead"]["estagio"], detalhe["cessao"]["id"]) == ("documentacao", sala)

    # Operador envia; o arquivo vai cifrado para o Storage e o banco guarda so os metadados.
    await _logar(navegador, operador)
    enviado = await _enviar(navegador, tenant, sala, "identidade")
    assert enviado.status_code == 201, enviado.text
    doc = enviado.json()["id"]
    [(caminho, blob)] = storage["cessoes"].items()
    assert caminho == f"{tenant}/{sala}/{doc}"
    assert PDF not in blob
    assert cenario.sql("select mime, tamanho, sha256 from app.documentos where id = %s", [doc]) == [
        ("application/pdf", len(PDF), hashlib.sha256(PDF).hexdigest())
    ]
    falso = await _enviar(navegador, tenant, sala, "identidade", b"<html><script>alert(1)</script>")
    assert (falso.status_code, falso.json()["codigo"]) == (415, "arquivo_nao_aceito")
    assert (await _enviar(navegador, tenant, sala, "virus")).status_code == 422

    # Quem le baixa o original (e fica na trilha); outro cliente nao enxerga.
    await _logar(navegador, leitor)
    baixado = await navegador.http.get(f"/api/t/{tenant}/documentos/{doc}/arquivo")
    assert (baixado.status_code, baixado.content) == (200, PDF)
    assert baixado.headers["content-disposition"] == f'attachment; filename="identidade-{doc[:8]}.pdf"'
    assert cenario.sql("select usuario_id from app.auditoria where acao = 'documentos.exportado'") == [(leitor,)]
    intruso = cenario.membro(cenario.tenant(), "owner")
    await _logar(navegador, intruso)
    assert (await navegador.http.get(f"/api/t/{tenant}/documentos/{doc}/arquivo")).status_code == 404

    # Revisao: operador nao aprova; o juridico aprova.
    await _logar(navegador, operador)
    assert (
        await navegador.http.patch(f"/api/t/{tenant}/documentos/{doc}", json={"status": "aprovado"})
    ).status_code == 403
    await _logar(navegador, juridico)
    sem_motivo = await navegador.http.patch(f"/api/t/{tenant}/documentos/{doc}", json={"status": "rejeitado"})
    assert sem_motivo.status_code == 422
    assert (
        await navegador.http.patch(f"/api/t/{tenant}/documentos/{doc}", json={"status": "aprovado"})
    ).status_code == 204

    # Agendar exige todos os obrigatorios aprovados.
    pendente = await navegador.http.post(f"/api/t/{tenant}/cessoes/{sala}/etapa", json={"para": "escritura_agendada"})
    assert (pendente.status_code, pendente.json()["codigo"]) == (409, "documentos_pendentes")
    obrigatorios = [i.tipo for i in regras.checklist(espolio=False, pessoa_juridica=False) if i.obrigatorio]
    for tipo in obrigatorios:
        if tipo == "identidade":
            continue
        novo = (await _enviar(navegador, tenant, sala, tipo, PNG)).json()["id"]
        await navegador.http.patch(f"/api/t/{tenant}/documentos/{novo}", json={"status": "aprovado"})
    vista = (await navegador.http.get(f"/api/t/{tenant}/cessoes/{sala}")).json()
    assert vista["pendencias"] == []
    assert vista["proximas"] == ["escritura_agendada", "cancelada"] or vista["proximas"] == ["escritura_agendada"]

    # Cartorio e ato no e-Notariado; operador nao preenche a sala.
    dados = {
        "cartorio": "1º Tabelionato de Notas de São Paulo",
        "escritura_em": (datetime.now(UTC) + timedelta(days=3)).isoformat(),
        "link_enotariado": "https://e-notariado.exemplo/ato/123",
    }
    await _logar(navegador, operador)
    assert (await navegador.http.patch(f"/api/t/{tenant}/cessoes/{sala}", json=dados)).status_code == 404
    await _logar(navegador, juridico)
    assert (await navegador.http.patch(f"/api/t/{tenant}/cessoes/{sala}", json=dados)).status_code == 204
    invalido = await navegador.http.patch(f"/api/t/{tenant}/cessoes/{sala}", json={"link_enotariado": "javascript:x"})
    assert invalido.status_code == 422
    assert (
        await navegador.http.post(f"/api/t/{tenant}/cessoes/{sala}/etapa", json={"para": "escritura_agendada"})
    ).status_code == 204
    assert cenario.sql("select estagio::text from app.leads where id = %s", [lead]) == [("escritura_agendada",)]

    # Minuta: so juridico e gestao; leva o CPF completo e entra na trilha.
    await _logar(navegador, operador)
    assert (await navegador.http.get(f"/api/t/{tenant}/cessoes/{sala}/minuta")).status_code == 403
    await _logar(navegador, juridico)
    minuta = await navegador.http.get(f"/api/t/{tenant}/cessoes/{sala}/minuta")
    assert minuta.status_code == 200
    texto = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(minuta.content)).pages)
    assert f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}" in texto
    assert "1º Tabelionato de Notas de São Paulo" in texto
    assert cenario.sql(
        "select count(*) from app.auditoria where acao = 'cessoes.exportado' and entidade_id = %s", [sala]
    ) == [(1,)]

    assert (
        await navegador.http.post(f"/api/t/{tenant}/cessoes/{sala}/etapa", json={"para": "escritura_assinada"})
    ).status_code == 204
    assert cenario.sql("select estagio::text from app.leads where id = %s", [lead]) == [("cedido",)]
    lista = (await navegador.http.get(f"/api/t/{tenant}/cessoes")).json()
    assert [(c["status"], c["aprovados"] == c["obrigatorios"]) for c in lista] == [("escritura_assinada", True)]


async def test_apagar_documento_nao_aprovado_tira_o_arquivo(ambiente: Ambiente, cenario: Cenario) -> None:
    navegador, storage = ambiente.navegador, ambiente.gotrue.arquivos
    tenant = cenario.tenant()
    gestor, operador = cenario.membro(tenant, "gestor"), cenario.membro(tenant, "operador")
    lead = _lead_em_negociacao(cenario, tenant)
    await _logar(navegador, gestor)
    sala = (await navegador.http.post(f"/api/t/{tenant}/leads/{lead}/cessao", json={"valor": "1000"})).json()["id"]
    await _logar(navegador, operador)
    doc = (await _enviar(navegador, tenant, sala, "outro")).json()["id"]
    assert len(storage["cessoes"]) == 1
    assert (await navegador.http.delete(f"/api/t/{tenant}/documentos/{doc}")).status_code == 204
    assert storage["cessoes"] == {}
    aprovado = (await _enviar(navegador, tenant, sala, "outro")).json()["id"]
    await _logar(navegador, gestor)
    await navegador.http.patch(f"/api/t/{tenant}/documentos/{aprovado}", json={"status": "aprovado"})
    await _logar(navegador, operador)
    assert (await navegador.http.delete(f"/api/t/{tenant}/documentos/{aprovado}")).status_code == 404
    assert len(storage["cessoes"]) == 1


async def test_corpo_grande_so_na_rota_de_documentos(ambiente: Ambiente, cenario: Cenario) -> None:
    navegador = ambiente.navegador
    tenant = cenario.tenant()
    gestor = cenario.membro(tenant, "gestor")
    lead = _lead_em_negociacao(cenario, tenant)
    await _logar(navegador, gestor)
    sala = (await navegador.http.post(f"/api/t/{tenant}/leads/{lead}/cessao", json={"valor": "1000"})).json()["id"]
    grande = PDF + b"0" * (2 * 1024 * 1024)
    assert (await _enviar(navegador, tenant, sala, "outro", grande)).status_code == 201
    enorme = PDF + b"0" * (10 * 1024 * 1024)
    assert (await _enviar(navegador, tenant, sala, "outro", enorme)).status_code == 413
    em_outra_rota = await navegador.http.post(
        f"/api/t/{tenant}/tarefas", content=b'{"titulo": "' + b"x" * 100_000 + b'"}',
        headers={"content-type": "application/json"},
    )  # fmt: skip
    assert em_outra_rota.status_code == 413


async def test_sem_chave_de_servico_os_documentos_ficam_desligados(
    banco: BancoDeTeste, redis_url: str, cenario: Cenario
) -> None:
    app = criar_app(
        config_de_teste(banco, redis_url, supabase_service_role_key=None),
        transporte_http=httpx.MockTransport(GoTrueFalso()),
    )
    tenant = cenario.tenant()
    gestor = cenario.membro(tenant, "gestor")
    lead = _lead_em_negociacao(cenario, tenant)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGEM, headers={"origin": ORIGEM}) as http,
    ):
        navegador = Navegador(http)
        await _logar(navegador, gestor)
        sala = (await http.post(f"/api/t/{tenant}/leads/{lead}/cessao", json={"valor": "1000"})).json()["id"]
        assert (await http.get(f"/api/t/{tenant}/cessoes/{sala}")).json()["armazenamento"] is False
        r = await _enviar(navegador, tenant, sala, "outro")
    assert (r.status_code, r.json()["codigo"]) == (503, "armazenamento_indisponivel")
