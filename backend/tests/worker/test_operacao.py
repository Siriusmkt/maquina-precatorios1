"""Operacao do worker: batimento para o healthcheck e a sonda de diagnostico do DJEN."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from tests.apoio.banco import BancoDeTeste
from tests.apoio.worker import DjenFalso, config_worker, novo_cnj
from worker.__main__ import bater, sonda
from worker.pipeline import agora_sp

SEM_BANCO = BancoDeTeste(dsn_admin="", dsn_api="", dsn_worker="postgresql://ninguem@127.0.0.1:1/nada")


async def test_batimento_toca_o_arquivo_e_para_junto_com_o_worker(tmp_path: Path) -> None:
    arquivo = tmp_path / "vivo"
    parar = asyncio.Event()
    tarefa = asyncio.create_task(bater(parar, arquivo, intervalo_s=0.01))
    await asyncio.sleep(0.05)
    assert arquivo.exists()
    parar.set()
    await asyncio.wait_for(tarefa, timeout=1)


async def test_sonda_mostra_o_formato_sem_dado_pessoal_e_sem_banco(capsys: pytest.CaptureFixture[str]) -> None:
    djen = DjenFalso()
    cnj = novo_cnj()
    ontem = agora_sp().date() - timedelta(days=1)
    djen.publicar("TJSP", ontem, "Expeça-se o requisitório em favor de Credora Ficticia.", numero_processo=cnj)

    codigo = await sonda(config_worker(SEM_BANCO), "TJSP", transporte=httpx.MockTransport(djen))

    saida = capsys.readouterr().out
    relatorio = json.loads(saida)
    assert codigo == 0
    assert relatorio["acesso"] == "ok"
    assert relatorio["itens_com_texto"] == 1
    assert relatorio["numero_processo_reconhecido"] is True
    assert "numeroprocessocommascara" in relatorio["campos_do_item"]
    for segredo in ("Credora", "Ficticia", cnj, cnj[:7]):
        assert segredo not in saida


@pytest.mark.parametrize(("resposta", "codigo"), [(httpx.Response(403), 2), (httpx.Response(404), 1)])
async def test_sonda_diz_se_foi_bloqueada(
    resposta: httpx.Response, codigo: int, capsys: pytest.CaptureFixture[str]
) -> None:
    djen = DjenFalso(forcadas=[resposta])
    assert await sonda(config_worker(SEM_BANCO), "TJSP", transporte=httpx.MockTransport(djen)) == codigo
    assert ("BLOQUEADO" if codigo == 2 else "FALHOU") in capsys.readouterr().out
