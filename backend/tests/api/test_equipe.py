"""Equipe, convites, preferencias e senha definida pelo link do e-mail."""

from __future__ import annotations

import time
import uuid

import httpx
import pytest

from app.main import criar_app
from tests.api.conftest import Ambiente
from tests.apoio.api import ORIGEM, URL_PUBLICA, GoTrueFalso, Navegador, config_de_teste, emitir_jwt
from tests.apoio.banco import BancoDeTeste, Cenario


async def _logar(navegador: Navegador, usuario: uuid.UUID) -> None:
    navegador.http.cookies.set("__Host-mq_sessao", emitir_jwt(usuario))
    await navegador.preparar_csrf()


def _email(cenario: Cenario, usuario: uuid.UUID) -> str:
    return str(cenario.sql("select email from auth.users where id = %s", [usuario])[0][0])


@pytest.fixture
def contas(cenario: Cenario, gotrue: GoTrueFalso) -> GoTrueFalso:
    """O Auth falso cria a conta em auth.users, como o Supabase faz no convite."""

    def criar(email: str) -> uuid.UUID:
        uid = uuid.uuid4()
        cenario.sql("insert into auth.users (id, email, invited_at) values (%s, %s, now())", [uid, email])
        return uid

    gotrue.criar_conta = criar
    return gotrue


# --------------------------------------------------------------------- equipe
async def test_convite_cria_a_conta_manda_o_link_e_aparece_na_equipe(
    navegador: Navegador, cenario: Cenario, contas: GoTrueFalso
) -> None:
    t = cenario.tenant(assentos=3)
    gestor = cenario.membro(t, "gestor")
    await _logar(navegador, gestor)

    r = await navegador.http.post(
        f"/api/t/{t}/equipe", json={"email": " Nova.Pessoa@Exemplo.com ", "papel": "operador", "nome_publico": "Nova"}
    )
    assert r.status_code == 201, r.text
    novo = r.json()
    assert (novo["email"], novo["papel"], novo["nome_publico"]) == ("nova.pessoa@exemplo.com", "operador", "Nova")
    assert novo["convite_pendente"] is True
    assert contas.convites == [("nova.pessoa@exemplo.com", f"{URL_PUBLICA}/definir-senha")]

    equipe = (await navegador.http.get(f"/api/t/{t}/equipe")).json()
    assert equipe["assentos"] == 3
    assert equipe["meu_papel"] == "gestor"
    assert [m["papel"] for m in equipe["membros"]] == ["gestor", "operador"]
    assert [m["sou_eu"] for m in equipe["membros"]] == [True, False]


async def test_quem_ja_tem_conta_entra_sem_email_e_a_resposta_e_a_mesma(
    navegador: Navegador, cenario: Cenario, contas: GoTrueFalso
) -> None:
    t = cenario.tenant()
    dono = cenario.membro(t, "owner")
    existente = cenario.usuario()
    await _logar(navegador, dono)
    r = await navegador.http.post(f"/api/t/{t}/equipe", json={"email": _email(cenario, existente), "papel": "leitura"})
    assert r.status_code == 201
    assert set(r.json()) == {
        "user_id", "email", "papel", "nome_publico", "criado_em", "ultimo_acesso_em", "convite_pendente", "sou_eu"
    }  # fmt: skip
    assert contas.convites == []
    repetido = await navegador.http.post(f"/api/t/{t}/equipe", json={"email": _email(cenario, existente)})
    assert repetido.status_code == 409
    assert repetido.json()["codigo"] == "ja_e_membro"


async def test_so_gestao_convida_e_nem_chama_o_auth(
    navegador: Navegador, cenario: Cenario, contas: GoTrueFalso
) -> None:
    t = cenario.tenant()
    operador = cenario.membro(t, "operador")
    await _logar(navegador, operador)
    r = await navegador.http.post(f"/api/t/{t}/equipe", json={"email": "alguem@exemplo.com"})
    assert r.status_code == 403
    assert contas.convites == []


async def test_convite_sem_chave_de_servico_explica_o_que_falta(
    banco: BancoDeTeste, redis_url: str, cenario: Cenario
) -> None:
    app = criar_app(
        config_de_teste(banco, redis_url, supabase_service_role_key=None),
        transporte_http=httpx.MockTransport(GoTrueFalso()),
    )
    t = cenario.tenant()
    dono = cenario.membro(t, "owner")
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGEM, headers={"origin": ORIGEM}) as http,
    ):
        navegador = Navegador(http)
        await _logar(navegador, dono)
        r = await http.post(f"/api/t/{t}/equipe", json={"email": "sem.conta@exemplo.com"})
    assert r.status_code == 503
    assert r.json()["codigo"] == "convites_indisponiveis"


async def test_mudar_papel_remover_e_ultimo_dono(navegador: Navegador, cenario: Cenario) -> None:
    t = cenario.tenant()
    dono = cenario.membro(t, "owner")
    operador = cenario.membro(t, "operador")
    await _logar(navegador, dono)

    r = await navegador.http.patch(f"/api/t/{t}/equipe/{operador}", json={"papel": "juridico", "nome_publico": "Bia"})
    assert r.status_code == 204
    membros = (await navegador.http.get(f"/api/t/{t}/equipe")).json()["membros"]
    assert {(m["papel"], m["nome_publico"]) for m in membros if not m["sou_eu"]} == {("juridico", "Bia")}

    assert (await navegador.http.delete(f"/api/t/{t}/equipe/{operador}")).status_code == 204
    ultimo = await navegador.http.patch(f"/api/t/{t}/equipe/{dono}", json={"papel": "gestor"})
    assert ultimo.status_code == 409
    assert "pelo menos um dono" in ultimo.json()["title"]
    invalido = await navegador.http.patch(f"/api/t/{t}/equipe/{dono}", json={"papel": "imperador"})
    assert invalido.status_code == 422


# --------------------------------------------------------------------- preferencias
async def test_preferencias_padrao_salvar_e_voltar_ao_padrao(navegador: Navegador, cenario: Cenario) -> None:
    t = cenario.tenant()
    gestor = cenario.membro(t, "gestor")
    await _logar(navegador, gestor)

    padrao = (await navegador.http.get(f"/api/t/{t}/preferencias")).json()
    assert (padrao["taxa_alvo_aa"], padrao["prazo_padrao_meses"], padrao["personalizadas"]) == ("0.22", 48, [])
    assert padrao["nao_cobramos_texto"].startswith("Nunca cobramos")
    assert (padrao["pode_editar"], padrao["pode_editar_alcada"]) == (True, False)

    salvo = await navegador.http.patch(
        f"/api/t/{t}/preferencias", json={"taxa_alvo_aa": "0.25", "prazo_padrao_meses": 36}
    )
    assert salvo.status_code == 200, salvo.text
    assert (salvo.json()["taxa_alvo_aa"], salvo.json()["prazo_padrao_meses"]) == ("0.25", 36)
    assert salvo.json()["personalizadas"] == ["prazo_padrao_meses", "taxa_alvo_aa"]

    volta = (await navegador.http.patch(f"/api/t/{t}/preferencias", json={"taxa_alvo_aa": None})).json()
    assert volta["taxa_alvo_aa"] == "0.22"

    for corpo, status in (({"taxa_alvo_aa": "0.95"}, 422), ({"alcada_gestor_desagio_min": 10}, 403), ({}, 422)):
        assert (await navegador.http.patch(f"/api/t/{t}/preferencias", json=corpo)).status_code == status


async def test_preferencias_sao_so_leitura_para_operador(navegador: Navegador, cenario: Cenario) -> None:
    t = cenario.tenant()
    operador = cenario.membro(t, "operador")
    await _logar(navegador, operador)
    assert (await navegador.http.get(f"/api/t/{t}/preferencias")).json()["pode_editar"] is False
    r = await navegador.http.patch(f"/api/t/{t}/preferencias", json={"taxa_alvo_aa": "0.3"})
    assert r.status_code == 403


# --------------------------------------------------------------------- senha
async def test_recuperar_senha_nao_revela_se_o_email_existe(
    navegador: Navegador, cenario: Cenario, gotrue: GoTrueFalso
) -> None:
    await navegador.preparar_csrf()
    existe = _email(cenario, cenario.usuario())
    for email in (existe, "ninguem@exemplo.com"):
        r = await navegador.http.post("/api/auth/recuperar", json={"email": email})
        assert r.status_code == 204
    assert [e for e, _ in gotrue.recuperacoes] == [existe, "ninguem@exemplo.com"]
    assert {d for _, d in gotrue.recuperacoes} == {f"{URL_PUBLICA}/definir-senha"}
    for _ in range(2):
        await navegador.http.post("/api/auth/recuperar", json={"email": existe})
    assert (await navegador.http.post("/api/auth/recuperar", json={"email": existe})).status_code == 429


def _token_do_link(usuario: uuid.UUID, email: str, metodo: str = "invite", **extra: object) -> str:
    return emitir_jwt(usuario, email, amr=[{"method": metodo, "timestamp": int(time.time())}], **extra)


async def test_definir_senha_pelo_link_grava_e_ja_entra(
    ambiente: Ambiente, cenario: Cenario, gotrue: GoTrueFalso
) -> None:
    t = cenario.tenant(nome="Compradora do Convite")
    convidado = cenario.membro(t, "operador")
    email = _email(cenario, convidado)
    gotrue.cadastrar(convidado, email, "senha-provisoria-que-ninguem-sabe")
    navegador = ambiente.navegador
    await navegador.preparar_csrf()

    r = await navegador.http.post(
        "/api/auth/definir-senha",
        json={"access_token": _token_do_link(convidado, email), "senha": "Precatorio2026!"},
    )
    assert r.status_code == 200, r.text
    assert [x["nome"] for x in r.json()["tenants"]] == ["Compradora do Convite"]
    assert gotrue.usuarios[email][1] == "Precatorio2026!"
    assert (await navegador.http.get("/api/eu")).status_code == 200


@pytest.mark.parametrize(
    ("senha", "metodo", "segredo", "status", "codigo"),
    [
        ("curta1", "invite", None, 422, "senha_fraca"),
        ("somenteletrasaqui", "recovery", None, 422, "senha_fraca"),
        ("Precatorio2026!", "password", None, 401, "link_invalido"),
        ("Precatorio2026!", "recovery", "outro-segredo-qualquer-com-mais-de-32-chars", 401, "link_invalido"),
    ],
)
async def test_definir_senha_recusa_senha_fraca_e_token_que_nao_veio_do_link(
    navegador: Navegador, cenario: Cenario, *, senha: str, metodo: str, segredo: str | None, status: int, codigo: str
) -> None:
    usuario = cenario.usuario()
    extra = {"_segredo": segredo} if segredo else {}
    await navegador.preparar_csrf()
    r = await navegador.http.post(
        "/api/auth/definir-senha",
        json={"access_token": _token_do_link(usuario, _email(cenario, usuario), metodo, **extra), "senha": senha},
    )
    assert r.status_code == status
    assert r.json()["codigo"] == codigo
