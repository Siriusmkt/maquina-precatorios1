"""Testes unitarios das pecas de seguranca."""

from __future__ import annotations

import base64
import json
import logging
from ipaddress import ip_network
from typing import Any, ClassVar

import pytest
from pydantic import ValidationError
from redis.asyncio import Redis

from app.config import Configuracoes
from app.logs import FormatadorJson, redigir, request_id_atual
from app.seguranca.cripto import (
    CifraDeCampo,
    FalhaDeCifra,
    indice_cego,
    normalizar_documento,
    normalizar_telefone,
)
from app.seguranca.limite import Limitador, Politica
from app.seguranca.rede import ip_do_cliente

K1, K2 = b"1" * 32, b"2" * 32


class TestCifra:
    def test_ida_e_volta(self) -> None:
        cifra = CifraDeCampo({1: K1}, 1)
        dado = cifra.cifrar("+55 11 98888-7777", "contatos:tenant-a:telefone")
        assert b"98888" not in dado
        assert cifra.decifrar(dado, "contatos:tenant-a:telefone") == "+55 11 98888-7777"

    def test_nonce_aleatorio(self) -> None:
        cifra = CifraDeCampo({1: K1}, 1)
        assert cifra.cifrar("x", "c") != cifra.cifrar("x", "c")

    def test_contexto_diferente_nao_decifra(self) -> None:
        # Copiar o valor cifrado de um tenant para outro nao funciona.
        cifra = CifraDeCampo({1: K1}, 1)
        dado = cifra.cifrar("segredo", "contatos:tenant-a:telefone")
        with pytest.raises(FalhaDeCifra):
            cifra.decifrar(dado, "contatos:tenant-b:telefone")

    def test_adulteracao_e_detectada(self) -> None:
        cifra = CifraDeCampo({1: K1}, 1)
        dado = bytearray(cifra.cifrar("segredo", "c"))
        dado[-1] ^= 0x01
        with pytest.raises(FalhaDeCifra):
            cifra.decifrar(bytes(dado), "c")

    def test_rotacao_de_chave(self) -> None:
        antiga = CifraDeCampo({1: K1}, 1).cifrar("valor", "c")
        nova = CifraDeCampo({1: K1, 2: K2}, 2)
        assert nova.decifrar(antiga, "c") == "valor"
        assert nova.precisa_recifrar(antiga)
        assert not nova.precisa_recifrar(nova.cifrar("valor", "c"))

    def test_chave_desconhecida_e_dado_curto(self) -> None:
        cifra = CifraDeCampo({1: K1}, 1)
        with pytest.raises(FalhaDeCifra):
            cifra.decifrar(CifraDeCampo({9: K2}, 9).cifrar("x", "c"), "c")
        with pytest.raises(FalhaDeCifra):
            cifra.decifrar(b"\x01abc", "c")


class TestIndiceCego:
    def test_deterministico_com_chave_e_separado_por_tipo(self) -> None:
        a = indice_cego(b"chave-1", "cpf", "12345678909")
        assert a == indice_cego(b"chave-1", "cpf", "12345678909")
        assert a != indice_cego(b"chave-2", "cpf", "12345678909")
        assert a != indice_cego(b"chave-1", "telefone", "12345678909")
        assert "12345678909" not in a

    def test_normalizacao(self) -> None:
        assert normalizar_documento("123.456.789-09") == "12345678909"
        assert normalizar_telefone("(11) 98888-7777") == "5511988887777"
        assert normalizar_telefone("+55 11 98888-7777") == "5511988887777"


class TestLogs:
    @pytest.mark.parametrize(
        ("texto", "proibido"),
        [
            ("cpf 123.456.789-09 do credor", "123.456.789-09"),
            ("cpf 12345678909", "12345678909"),
            ("cnpj 12.345.678/0001-90", "12.345.678/0001-90"),
            ("ligar para (11) 98888-7777", "98888-7777"),
            ("email maria.silva@gmail.com", "maria.silva@gmail.com"),
            ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N", "eyJhbGci"),
            ("senha=hunter2 enviada", "hunter2"),
        ],
    )
    def test_redacao_de_dados_pessoais(self, texto: str, proibido: str) -> None:
        assert proibido not in redigir(texto)

    def test_numero_cnj_nao_e_confundido_com_cpf(self) -> None:
        assert "00012345620198260001" in redigir("processo 00012345620198260001")

    def test_formatador_json_com_request_id_e_extras_redigidos(self) -> None:
        token = request_id_atual.set("abc123")
        try:
            registro = logging.LogRecord("t", logging.INFO, "f", 1, "login de %s", ("ana@x.com",), None)
            registro.detalhe = {"telefone": "(11) 98888-7777"}
            evento = json.loads(FormatadorJson().format(registro))
        finally:
            request_id_atual.reset(token)
        assert evento["request_id"] == "abc123"
        assert "ana@x.com" not in evento["msg"]
        assert "98888" not in json.dumps(evento)


class TestRede:
    redes: ClassVar = [ip_network("10.0.0.0/8")]

    def test_sem_proxy_confiavel_ignora_xff(self) -> None:
        assert ip_do_cliente("203.0.113.9", "1.2.3.4", self.redes) == "203.0.113.9"

    def test_proxy_confiavel_usa_primeiro_ip_nao_confiavel_da_direita(self) -> None:
        # Cliente forjou "1.2.3.4" na frente; o proxy acrescentou o IP real.
        assert ip_do_cliente("10.0.0.2", "1.2.3.4, 198.51.100.7, 10.0.0.5", self.redes) == "198.51.100.7"

    def test_xff_lixo(self) -> None:
        assert ip_do_cliente("10.0.0.2", "nao-e-ip", self.redes) == "10.0.0.2"


class TestLimitador:
    async def test_permite_o_limite_e_bloqueia_o_excedente(self, redis_url: str) -> None:
        redis = Redis.from_url(redis_url)
        try:
            await redis.flushdb()
            limitador = Limitador(redis, prefixo="teste-unid")
            politica = Politica("unid", limite=3, periodo_s=60)
            resultados = [await limitador.consumir(politica, "ip-1") for _ in range(4)]
            assert [r.permitido for r in resultados] == [True, True, True, False]
            assert [r.restantes for r in resultados[:3]] == [2, 1, 0]
            assert resultados[3].tentar_em_s >= 1
            # Outra identidade tem balde proprio.
            assert (await limitador.consumir(politica, "ip-2")).permitido
            # A chave nao guarda a identidade em claro.
            chaves = [c.decode() for c in await redis.keys("teste-unid:*")]
            assert chaves
            assert not any("ip-1" in c for c in chaves)
        finally:
            await redis.aclose()


class TestConfiguracoes:
    base: ClassVar[dict[str, Any]] = {
        "database_url": "postgresql://x",
        "supabase_url": "https://auth.exemplo.com.br",
        "supabase_anon_key": "anon",
        "chaves_cifra": {1: base64.b64encode(b"k" * 32).decode()},
        "chave_cifra_ativa": 1,
        "chave_indice": "i" * 40,
    }

    def test_producao_insegura_nao_sobe(self) -> None:
        with pytest.raises(ValidationError, match="cookie_seguro"):
            Configuracoes(
                **self.base, ambiente="producao", cookie_seguro=False, origens_permitidas=["https://app.exemplo.com.br"]
            )
        with pytest.raises(ValidationError, match="https"):
            Configuracoes(**self.base, ambiente="producao", origens_permitidas=["http://app.exemplo.com.br"])

    def test_chave_de_cifra_invalida(self) -> None:
        with pytest.raises(ValidationError, match="32 bytes"):
            Configuracoes(**{**self.base, "chaves_cifra": {1: base64.b64encode(b"curta").decode()}})
        with pytest.raises(ValidationError, match="chave_cifra_ativa"):
            Configuracoes(**{**self.base, "chave_cifra_ativa": 7})

    def test_listas_vindas_do_ambiente(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAQUINA_ORIGENS_PERMITIDAS", "https://a.com.br/, https://b.com.br")
        monkeypatch.setenv("MAQUINA_PROXIES_CONFIAVEIS", "10.0.0.0/8,172.16.0.0/12")
        config = Configuracoes(**self.base)
        assert config.origens_permitidas == ["https://a.com.br", "https://b.com.br"]
        assert len(config.proxies_confiaveis) == 2
