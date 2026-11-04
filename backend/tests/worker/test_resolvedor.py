"""Resolvedor no Postgres, como maquina_worker: fila, modo automatico, teto diario, credencial e gravacao."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import psycopg

from app.dominio.contatos import CONTEXTO_DOCUMENTO_DO_PROCESSO, contexto_do_documento_informado
from app.dominio.integracoes import contexto_segredos
from app.seguranca.cripto import CifraDeCampo, indice_cego
from tests.apoio.banco import BancoDeTeste
from tests.apoio.worker import (
    CHAVE_INDICE,
    URL_BUREAU,
    BureauFalso,
    config_worker,
    cpf_sintetico,
    endereco_bdc,
    resposta_bigdatacorp,
    telefone_bdc,
)
from worker.bureau import ClienteBigDataCorp
from worker.pipeline import Pool, agora_sp
from worker.resolvedor import Resolvedor, ResumoResolvedor

Admin = psycopg.Connection[dict[str, Any]]


@dataclass
class Cena:
    tenant: uuid.UUID
    lead: uuid.UUID
    parte: uuid.UUID
    cpf: str
    cifra: CifraDeCampo


def montar_cena(
    admin: Admin,
    banco: BancoDeTeste,
    *,
    estagio: str = "qualificado",
    automatico: bool = False,
    limite_dia: int = 50,
    cpf_no_processo: bool = True,
) -> Cena:
    cifra = CifraDeCampo(config_worker(banco).chaves_cifra_bytes, 1)
    cpf = cpf_sintetico()
    tenant = admin.execute("insert into app.tenants (nome) values ('Compradora Teste') returning id").fetchone()["id"]
    parte = admin.execute(
        """insert into core.partes (nome, tipo, documento_hash, documento_cifrado)
           values (%s, 'pf', %s, %s) returning id""",
        [
            "Credora Exemplo",
            indice_cego(CHAVE_INDICE.encode(), "cpf", cpf) if cpf_no_processo else None,
            cifra.cifrar(cpf, CONTEXTO_DOCUMENTO_DO_PROCESSO) if cpf_no_processo else None,
        ],
    ).fetchone()["id"]
    req = admin.execute(
        "insert into core.requisicoes (tribunal, valor_face) values ('TJSP', 100000) returning id"
    ).fetchone()["id"]
    lead = admin.execute(
        """insert into app.leads (tenant_id, requisicao_id, credor_parte_id, estagio)
           values (%s, %s, %s, %s) returning id""",
        [tenant, req, parte, estagio],
    ).fetchone()["id"]
    admin.execute(
        "insert into app.integracoes (tenant_id, tipo, config, segredos) values (%s, 'bureau', %s, %s)",
        [
            tenant,
            json.dumps({"provedor": "bigdatacorp", "automatico": automatico, "limite_dia": limite_dia}),
            cifra.cifrar(json.dumps({"access_token": "acesso", "token_id": "id"}), contexto_segredos(tenant, "bureau")),
        ],
    )
    return Cena(tenant=tenant, lead=lead, parte=parte, cpf=cpf, cifra=cifra)


def resolvedor(pool: Pool, banco: BancoDeTeste, bureau: BureauFalso) -> Resolvedor:
    cliente = ClienteBigDataCorp(httpx.AsyncClient(transport=httpx.MockTransport(bureau)), URL_BUREAU)
    return Resolvedor(pool, config_worker(banco), cliente, relogio=agora_sp)


def pedir(admin: Admin, cena: Cena) -> uuid.UUID:
    return admin.execute(
        "insert into app.enriquecimentos (tenant_id, lead_id) values (%s, %s) returning id", [cena.tenant, cena.lead]
    ).fetchone()["id"]


def pedido(admin: Admin, pedido_id: uuid.UUID) -> dict[str, Any]:
    return admin.execute(
        """select status, achados, erro, tentativas, proxima_tentativa_em > now() as adiado
             from app.enriquecimentos where id = %s""",
        [pedido_id],
    ).fetchone()


ACHADOS = resposta_bigdatacorp(
    telefones=[telefone_bdc("987654321", IsMain=True), telefone_bdc("33334444")],
    emails=[{"EmailAddress": "credora@exemplo.com.br"}],
    enderecos=[endereco_bdc()],
)


async def test_acha_grava_cifrado_anota_e_move_o_lead(pool: Pool, banco_worker: BancoDeTeste, admin: Admin) -> None:
    cena = montar_cena(admin, banco_worker)
    bureau = BureauFalso(respostas=[httpx.Response(200, json=ACHADOS)])
    pid = pedir(admin, cena)
    resumo = await resolvedor(pool, banco_worker, bureau).ciclo()

    assert bureau.cpfs() == [cena.cpf]
    assert (resumo.consultados, resumo.contatos_novos) == (1, 4)
    assert pedido(admin, pid)["status"] == "concluido"
    assert pedido(admin, pid)["achados"] == {"telefones": 2, "emails": 1, "enderecos": 1, "novos": 4}
    contatos = admin.execute(
        """select tipo::text, valor_cifrado, origem::text, base_legal
             from app.contatos where parte_id = %s order by tipo""",
        [cena.parte],
    ).fetchall()
    assert {(c["tipo"], c["origem"], c["base_legal"]) for c in contatos} == {
        ("telefone", "bureau", "legitimo_interesse"),
        ("email", "bureau", "legitimo_interesse"),
        ("endereco", "bureau", "legitimo_interesse"),
    }
    for c in contatos:
        assert b"987654321" not in bytes(c["valor_cifrado"])
    endereco = next(c for c in contatos if c["tipo"] == "endereco")
    assert cena.cifra.decifrar(bytes(endereco["valor_cifrado"]), f"contatos:{cena.tenant}:endereco").startswith(
        "Rua das Flores, 100"
    )
    assert admin.execute("select estagio::text from app.leads where id = %s", [cena.lead]).fetchone() == {
        "estagio": "enriquecido"
    }
    [nota] = admin.execute("select conteudo from app.interacoes where lead_id = %s", [cena.lead]).fetchall()
    assert nota["conteudo"] == "Bureau: 2 telefones, 1 e-mail e 1 endereço encontrados (4 novos)."

    # De novo, com os mesmos dados: nada duplica e o funil nao anda de novo.
    bureau.respostas.append(httpx.Response(200, json=ACHADOS))
    pid2 = pedir(admin, cena)
    await resolvedor(pool, banco_worker, bureau).ciclo()
    assert pedido(admin, pid2)["achados"]["novos"] == 0
    assert (
        admin.execute("select count(*) as n from app.contatos where parte_id = %s", [cena.parte]).fetchone()["n"] == 4
    )


async def test_oposicao_anterior_continua_valendo(pool: Pool, banco_worker: BancoDeTeste, admin: Admin) -> None:
    cena = montar_cena(admin, banco_worker)
    numero = "5511987654321"
    admin.execute(
        """insert into app.contatos (tenant_id, parte_id, tipo, valor_cifrado, valor_hash, origem, opt_out_em)
           values (%s, %s, 'telefone', %s, %s, 'credor_informou', now())""",
        [cena.tenant, cena.parte, cena.cifra.cifrar(numero, f"contatos:{cena.tenant}:telefone"),
         indice_cego(CHAVE_INDICE.encode(), "telefone", numero)],
    )  # fmt: skip
    pid = pedir(admin, cena)
    bureau = BureauFalso(
        respostas=[httpx.Response(200, json=resposta_bigdatacorp(telefones=[telefone_bdc("987654321")]))]
    )
    await resolvedor(pool, banco_worker, bureau).ciclo()
    assert pedido(admin, pid)["achados"]["novos"] == 0
    assert (
        admin.execute(
            "select count(*) as n from app.contatos where parte_id = %s and opt_out_em is not null", [cena.parte]
        ).fetchone()["n"]
        == 1
    )
    assert admin.execute("select estagio::text from app.leads where id = %s", [cena.lead]).fetchone()["estagio"] == (
        "qualificado"
    )


async def test_cpf_informado_pela_equipe_tem_preferencia(pool: Pool, banco_worker: BancoDeTeste, admin: Admin) -> None:
    cena = montar_cena(admin, banco_worker, cpf_no_processo=False)
    sem = pedir(admin, cena)
    bureau = BureauFalso()
    await resolvedor(pool, banco_worker, bureau).ciclo()
    assert pedido(admin, sem)["status"] == "sem_documento"
    assert bureau.pedidos == []  # nada de buscar pelo nome

    informado = cpf_sintetico()
    admin.execute(
        """insert into app.documentos_informados (tenant_id, lead_id, documento_cifrado, documento_hash)
           values (%s, %s, %s, %s)""",
        [cena.tenant, cena.lead, cena.cifra.cifrar(informado, contexto_do_documento_informado(cena.tenant)),
         indice_cego(CHAVE_INDICE.encode(), "cpf", informado)],
    )  # fmt: skip
    pid = pedir(admin, cena)
    await resolvedor(pool, banco_worker, bureau).ciclo()
    assert bureau.cpfs() == [informado]
    assert pedido(admin, pid)["status"] == "sem_resultado"
    notas = [
        n["conteudo"] for n in admin.execute("select conteudo from app.interacoes where lead_id = %s", [cena.lead])
    ]
    assert "Bureau: nada encontrado para o CPF do credor." in notas


async def test_teto_diario_segura_o_resto_para_amanha(pool: Pool, banco_worker: BancoDeTeste, admin: Admin) -> None:
    cena = montar_cena(admin, banco_worker, limite_dia=1)
    admin.execute(
        """insert into app.enriquecimentos (tenant_id, lead_id, status, concluido_em)
           values (%s, %s, 'concluido', now())""",
        [cena.tenant, cena.lead],
    )
    pid = pedir(admin, cena)
    bureau = BureauFalso()
    await resolvedor(pool, banco_worker, bureau).ciclo()
    assert bureau.pedidos == []
    estado = pedido(admin, pid)
    assert (estado["status"], estado["erro"], estado["adiado"], estado["tentativas"]) == (
        "pendente", "limite_diario", True, 0,
    )  # fmt: skip


async def test_credencial_recusada_marca_a_integracao_e_segura_a_fila(
    pool: Pool, banco_worker: BancoDeTeste, admin: Admin
) -> None:
    cena = montar_cena(admin, banco_worker)
    pid = pedir(admin, cena)
    bureau = BureauFalso(respostas=[httpx.Response(401)])
    resumo = await resolvedor(pool, banco_worker, bureau).ciclo()
    assert resumo.falhas == 1
    assert admin.execute("select erro from app.integracoes where tenant_id = %s", [cena.tenant]).fetchone() == {
        "erro": "credencial_invalida"
    }
    estado = pedido(admin, pid)
    assert (estado["status"], estado["adiado"], estado["tentativas"]) == ("pendente", True, 0)

    # Enquanto a gestao nao corrige, nada e consultado (mesmo vencida a espera).
    admin.execute("update app.enriquecimentos set proxima_tentativa_em = now() where id = %s", [pid])
    await resolvedor(pool, banco_worker, bureau).ciclo()
    assert len(bureau.pedidos) == 1


async def test_erro_temporario_espera_e_desiste_na_quinta(pool: Pool, banco_worker: BancoDeTeste, admin: Admin) -> None:
    cena = montar_cena(admin, banco_worker)
    pid = pedir(admin, cena)
    bureau = BureauFalso(respostas=[httpx.Response(503) for _ in range(5)])
    r = resolvedor(pool, banco_worker, bureau)
    for tentativa in range(1, 6):
        await r.processar_pendentes(ResumoResolvedor())
        estado = pedido(admin, pid)
        assert estado["tentativas"] == tentativa
        if tentativa < 5:
            assert (estado["status"], estado["erro"], estado["adiado"]) == ("pendente", "bureau_503", True)
            admin.execute("update app.enriquecimentos set proxima_tentativa_em = now() where id = %s", [pid])
    assert pedido(admin, pid)["status"] == "falhou"


async def test_integracao_desligada_cancela(pool: Pool, banco_worker: BancoDeTeste, admin: Admin) -> None:
    cena = montar_cena(admin, banco_worker)
    admin.execute("update app.integracoes set ativa = false where tenant_id = %s", [cena.tenant])
    pid = pedir(admin, cena)
    await resolvedor(pool, banco_worker, BureauFalso()).ciclo()
    assert (pedido(admin, pid)["status"], pedido(admin, pid)["erro"]) == ("cancelado", "bureau_desligado")


async def test_modo_automatico_so_pega_qualificados_com_cpf_e_sem_telefone(
    pool: Pool, banco_worker: BancoDeTeste, admin: Admin
) -> None:
    alvo = montar_cena(admin, banco_worker, automatico=True)
    manual = montar_cena(admin, banco_worker, automatico=False)
    novo = montar_cena(admin, banco_worker, automatico=True, estagio="novo")
    sem_cpf = montar_cena(admin, banco_worker, automatico=True, cpf_no_processo=False)
    com_telefone = montar_cena(admin, banco_worker, automatico=True)
    admin.execute(
        """insert into app.contatos (tenant_id, parte_id, tipo, valor_cifrado, valor_hash, origem)
           values (%s, %s, 'telefone', '\\x01', 'hash-qualquer', 'bureau')""",
        [com_telefone.tenant, com_telefone.parte],
    )
    resumo = ResumoResolvedor()
    r = resolvedor(pool, banco_worker, BureauFalso())
    await r.enfileirar_automaticos(resumo)
    fila = {linha["lead_id"] for linha in admin.execute("select lead_id from app.enriquecimentos").fetchall()}
    assert fila == {alvo.lead}
    assert resumo.enfileirados == 1
    assert {manual.lead, novo.lead, sem_cpf.lead, com_telefone.lead}.isdisjoint(fila)

    # Ja consultado nos ultimos 30 dias: nao entra de novo.
    await r.processar_pendentes(ResumoResolvedor())
    await r.enfileirar_automaticos(resumo)
    assert admin.execute("select count(*) as n from app.enriquecimentos").fetchone()["n"] == 1
