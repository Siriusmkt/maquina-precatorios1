"""Regras de negocio puras: CNJ, preco justo, score e funil."""

from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal

import pytest

from app.dominio.cnj import formatar_cnj, normalizar_cnj
from app.dominio.formato import reais
from app.dominio.funil import TransicaoNegada, transicoes_permitidas, validar_transicao
from app.dominio.preco import AcordoEstatal, Ativo, Parametros, PremissaInvalida, precificar
from app.dominio.score import DadosDoAtivo, pontuar
from tests.apoio.banco import cnj_valido

HOJE = date(2026, 11, 3)
D = Decimal


class TestCnj:
    def test_valida_e_formata(self) -> None:
        numero = cnj_valido(1234567, 2019, 8, 26, 100)
        assert normalizar_cnj(formatar_cnj(numero)) == numero
        assert formatar_cnj(numero).count(".") == 4

    def test_digito_errado_e_lixo(self) -> None:
        numero = cnj_valido(1234567)
        errado = numero[:7] + f"{(int(numero[7:9]) + 1) % 100:02d}" + numero[9:]
        assert normalizar_cnj(errado) is None
        assert normalizar_cnj("123") is None
        assert normalizar_cnj("") is None


class TestFormato:
    @pytest.mark.parametrize(
        ("valor", "texto"),
        [
            (D("0"), "R$ 0,00"),
            (D("1234567.891"), "R$ 1.234.567,89"),
            (D("-50"), "-R$ 50,00"),
            (D("999.995"), "R$ 1.000,00"),
        ],
    )
    def test_reais(self, valor: Decimal, texto: str) -> None:
        assert reais(valor) == texto


# Premissas "limpas" para conferir as contas na mao: sem correcao, 12 meses.
SEM_CORRECAO = Parametros(taxa_alvo_aa=D("0.25"), tir_minima_aa=D("0.20"), ipca_aa=D(0), juros_aa=D(0))


def _ativo(**extra: object) -> Ativo:
    base = Ativo(valor_face=D("100000"), data_base=HOJE, prazo_meses=D(12))
    return dataclasses.replace(base, **extra)  # type: ignore[arg-type]


class TestPreco:
    def test_contas_basicas_conferidas_a_mao(self) -> None:
        r = precificar(_ativo(), SEM_CORRECAO, HOJE)
        assert r.valor_atualizado == D("100000.00")
        assert r.recebimento_esperado == D("100000.00")
        assert r.preco_alvo == D("80000.00")  # 100000 / 1,25
        assert r.preco_teto == D("83333.33")  # 100000 / 1,20
        assert r.preco_sugerido == D("80000.00")
        assert r.desagio_pct == D("20.00")
        assert r.tir_aa == D("25.00")
        assert r.viavel

    def test_piso_do_acordo_puxa_o_preco_para_cima(self) -> None:
        acordo = AcordoEstatal(desagio=D("0.18"), prazo_meses=D(6), descricao="Edital teste")
        r = precificar(_ativo(acordo=acordo), SEM_CORRECAO, HOJE)
        assert r.piso_acordo == D("82000.00")
        assert r.preco_sugerido == D("82000.00")
        assert r.tir_aa == D("21.95")  # 100000/82000 - 1
        assert any("piso" in a for a in r.alertas)

    def test_acordo_acima_do_teto_torna_inviavel(self) -> None:
        acordo = AcordoEstatal(desagio=D("0.10"), prazo_meses=D(6), descricao="Edital generoso")
        r = precificar(_ativo(acordo=acordo), SEM_CORRECAO, HOJE)
        assert not r.viavel
        assert r.preco_sugerido is None
        assert r.desagio_pct is None
        assert any("Inviável" in a for a in r.alertas)

    def test_acordo_ruim_nao_interfere(self) -> None:
        acordo = AcordoEstatal(desagio=D("0.40"), prazo_meses=D(6), descricao="Edital TJSP")
        r = precificar(_ativo(acordo=acordo), SEM_CORRECAO, HOJE)
        assert r.piso_acordo == D("60000.00")
        assert r.preco_sugerido == D("80000.00")
        assert r.credor.acordo_valor == D("60000.00")

    def test_honorarios_destacados_saem_da_base(self) -> None:
        r = precificar(_ativo(honorarios_destacados=D("20000")), SEM_CORRECAO, HOJE)
        assert r.base_do_credor == D("80000.00")
        assert r.preco_sugerido == D("64000.00")

    def test_correcao_ipca_mais_juros_simples(self) -> None:
        params = dataclasses.replace(SEM_CORRECAO, ipca_aa=D("0.05"), juros_aa=D("0.02"))
        dois_anos_atras = date(2024, 11, 3)
        r = precificar(_ativo(data_base=dois_anos_atras), params, HOJE)
        anos = D(730) / D("365.25")
        esperado = D("100000") * (D("1.05") ** anos) * (1 + D("0.02") * anos)
        assert r.valor_atualizado == esperado.quantize(D("0.01"))

    @pytest.mark.parametrize("campo", ["taxa_alvo_aa", "prazo_meses"])
    def test_monotonia(self, campo: str) -> None:
        # Mais taxa ou mais prazo => preco menor.
        if campo == "taxa_alvo_aa":
            baixo = precificar(_ativo(), SEM_CORRECAO, HOJE)
            alto = precificar(_ativo(), dataclasses.replace(SEM_CORRECAO, taxa_alvo_aa=D("0.35")), HOJE)
        else:
            baixo = precificar(_ativo(), SEM_CORRECAO, HOJE)
            alto = precificar(_ativo(prazo_meses=D(36)), SEM_CORRECAO, HOJE)
        assert alto.preco_alvo < baixo.preco_alvo

    def test_cenarios_e_visao_do_credor(self) -> None:
        params = dataclasses.replace(SEM_CORRECAO, aliquota_ir=D("0.03"))
        r = precificar(_ativo(prazo_meses=D(20)), params, HOJE)
        nomes = [c.nome for c in r.cenarios]
        assert nomes == ["otimista", "base", "pessimista"]
        tirs = [c.tir_aa for c in r.cenarios]
        assert tirs[0] is not None
        assert tirs[2] is not None
        assert tirs[0] > tirs[2]  # receber antes rende mais
        assert r.credor.esperar_valor == D("97000.00")
        assert r.credor.vender_hoje == r.preco_sugerido

    def test_memoria_de_calculo_explica_cada_numero(self) -> None:
        r = precificar(_ativo(), SEM_CORRECAO, HOJE)
        rotulos = [p.rotulo for p in r.memoria]
        assert rotulos[0] == "Valor de face"
        assert rotulos[-1] == "Preço sugerido"
        assert r.memoria[-1].valor == r.preco_sugerido

    @pytest.mark.parametrize(
        "ruim",
        [
            {"valor_face": D(0)},
            {"data_base": date(2030, 1, 1)},
            {"prazo_meses": D(0)},
            {"honorarios_destacados": D("100000")},
        ],
    )
    def test_premissas_invalidas(self, ruim: dict[str, object]) -> None:
        with pytest.raises(PremissaInvalida):
            precificar(_ativo(**ruim), SEM_CORRECAO, HOJE)

    def test_taxa_invalida(self) -> None:
        with pytest.raises(PremissaInvalida):
            precificar(_ativo(), dataclasses.replace(SEM_CORRECAO, taxa_alvo_aa=D("1.5")), HOJE)

    def test_resultado_e_sempre_decimal_em_centavos(self) -> None:
        params = dataclasses.replace(SEM_CORRECAO, ipca_aa=D("0.0437"), juros_aa=D("0.02"))
        r = precificar(_ativo(data_base=date(2019, 3, 17), prazo_meses=D(41)), params, HOJE)
        for valor in (r.valor_atualizado, r.recebimento_esperado, r.preco_alvo, r.preco_teto, r.preco_sugerido):
            assert isinstance(valor, Decimal)
            assert valor == valor.quantize(D("0.01"))


def _dados(**extra: object) -> DadosDoAtivo:
    base = DadosDoAtivo(
        valor_face=D("300000"),
        natureza="comum",
        esfera="estadual",
        prazo_medio_meses=D(48),
        regime_especial=False,
        ano_loa=2028,
        posicao_fila=None,
        status="inscrito",
        definitivo=True,
        data_base=date(2023, 1, 1),
    )
    return dataclasses.replace(base, **extra)  # type: ignore[arg-type]


class TestScore:
    def test_faixa_e_motivos(self) -> None:
        s = pontuar(_dados(), HOJE)
        assert 0 <= s.score <= 100
        assert s.elegivel_fidc
        assert s.motivos
        assert all(m.texto for m in s.motivos)

    def test_superpreferencia_nao_existe_na_entrada(self) -> None:
        campos = {f.name for f in dataclasses.fields(DadosDoAtivo)}
        assert not {c for c in campos if "prefer" in c or "doenca" in c or "saude" in c}

    def test_riscos_derrubam_o_score(self) -> None:
        limpo = pontuar(_dados(), HOJE)
        arriscado = pontuar(_dados(eventos=frozenset({"embargos", "penhora"})), HOJE)
        assert arriscado.risco > limpo.risco
        assert arriscado.score < limpo.score

    def test_cessao_anterior_pesa_muito(self) -> None:
        assert (
            pontuar(_dados(eventos=frozenset({"cessao_comunicada"})), HOJE).score < pontuar(_dados(), HOJE).score - 10
        )

    def test_pedido_de_acordo_aumenta_propensao(self) -> None:
        quer_vender = pontuar(_dados(eventos=frozenset({"acordo_direto_pedido"})), HOJE)
        assert quer_vender.propensao > pontuar(_dados(), HOJE).propensao

    def test_alimentar_federal_rapido_e_melhor_ativo(self) -> None:
        bom = pontuar(_dados(natureza="alimentar", esfera="federal", prazo_medio_meses=D(18), ano_loa=2027), HOJE)
        ruim = pontuar(_dados(prazo_medio_meses=D(96), regime_especial=True, ano_loa=2033), HOJE)
        assert bom.ativo > ruim.ativo

    def test_nao_definitivo_nao_e_elegivel_a_fidc(self) -> None:
        s = pontuar(_dados(definitivo=None), HOJE)
        assert not s.elegivel_fidc
        assert any("5.343" in m.texto for m in s.motivos)


class TestFunil:
    def test_operador_avanca_mas_nao_conclui_cessao(self) -> None:
        assert "qualificado" in transicoes_permitidas("novo", "operador")
        assert "cedido" not in transicoes_permitidas("escritura_agendada", "operador")
        assert "cedido" in transicoes_permitidas("escritura_agendada", "juridico")

    def test_leitura_nao_move_nada(self) -> None:
        assert transicoes_permitidas("novo", "leitura") == []
        with pytest.raises(TransicaoNegada, match="sem_permissao"):
            validar_transicao("novo", "qualificado", "leitura", None)

    def test_pular_etapa_e_negado(self) -> None:
        with pytest.raises(TransicaoNegada, match="estagio_invalido"):
            validar_transicao("novo", "cedido", "owner", None)

    def test_perda_exige_motivo_e_reabrir_exige_gestao(self) -> None:
        with pytest.raises(TransicaoNegada, match="motivo_obrigatorio"):
            validar_transicao("negociando", "perdido", "operador", "  ")
        validar_transicao("negociando", "perdido", "operador", "Credor vendeu para outro")
        assert transicoes_permitidas("perdido", "operador") == []
        assert transicoes_permitidas("perdido", "gestor") == ["novo"]

    def test_cedido_e_terminal(self) -> None:
        assert transicoes_permitidas("cedido", "owner") == []
