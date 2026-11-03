"""Linha de comando dos workers.

python -m worker                      ciclo continuo (coleta -> triagem -> Leitor IA)
python -m worker coletar TJSP 2026-11-02 [--termo requisitório]
python -m worker triar
python -m worker extrair [--limite 50]
python -m worker sonda [TJSP]         testa o acesso ao DJEN e mostra o formato (sem dados pessoais)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import signal
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

from app.logs import configurar_logs
from worker.config import ConfiguracoesWorker
from worker.djen import DjenBloqueado, ErroDjen
from worker.montagem import cliente_djen, montar
from worker.pipeline import ResumoCiclo, agora_sp

log = logging.getLogger("maquina.worker")

# Healthcheck do container: o arquivo e tocado a cada 30 s enquanto o laco de eventos responde.
BATIMENTO = Path("/tmp/maquina-worker-vivo")  # noqa: S108 - tmpfs privado do container


async def bater(parar: asyncio.Event, arquivo: Path = BATIMENTO, intervalo_s: float = 30.0) -> None:
    while not parar.is_set():
        with contextlib.suppress(OSError):
            await asyncio.to_thread(arquivo.touch)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(parar.wait(), timeout=intervalo_s)


async def rodar(config: ConfiguracoesWorker) -> None:
    parar = asyncio.Event()
    laco = asyncio.get_running_loop()
    for sinal in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            laco.add_signal_handler(sinal, parar.set)
    batimento = asyncio.create_task(bater(parar))
    try:
        async with montar(config) as pipeline:
            while not parar.is_set():
                try:
                    await pipeline.ciclo()
                except Exception:
                    log.exception("ciclo falhou; nova tentativa no proximo")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(parar.wait(), timeout=config.ciclo_s)
    finally:
        parar.set()
        await batimento
    log.info("worker encerrado")


async def sonda(
    config: ConfiguracoesWorker, tribunal: str, *, transporte: httpx.AsyncBaseTransport | None = None
) -> int:
    """Uma consulta real ao DJEN: diz se o acesso funciona e quais campos a API devolve.

    Nao precisa do banco e nunca imprime texto de publicacao nem dado de parte.
    """
    dia = agora_sp().date() - timedelta(days=1)
    async with cliente_djen(config, transporte=transporte) as djen:
        try:
            async for pagina in djen.paginas(tribunal, dia, "requisitório"):
                relatorio = {
                    "acesso": "ok",
                    "tribunal": tribunal,
                    "dia": dia.isoformat(),
                    "itens_na_pagina": pagina.recebidos,
                    "itens_com_texto": len(pagina.itens),
                    "campos_do_item": sorted(pagina.itens[0].payload) if pagina.itens else [],
                    "numero_processo_reconhecido": bool(pagina.itens and pagina.itens[0].numero_processo),
                }
                print(json.dumps(relatorio, ensure_ascii=False, indent=2))
                return 0
        except DjenBloqueado as exc:
            print(f"BLOQUEADO pela fonte: {exc}. Rode a partir do servidor de producao ou use o plano B.")
            return 2
        except ErroDjen as exc:
            print(f"FALHOU: {exc}")
            return 1
    return 0


def principal(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m worker")
    sub = parser.add_subparsers(dest="comando")
    p_coletar = sub.add_parser("coletar")
    p_coletar.add_argument("tribunal")
    p_coletar.add_argument("dia", type=date.fromisoformat)
    p_coletar.add_argument("--termo", default="")
    sub.add_parser("triar")
    p_extrair = sub.add_parser("extrair")
    p_extrair.add_argument("--limite", type=int, default=50)
    p_sonda = sub.add_parser("sonda")
    p_sonda.add_argument("tribunal", nargs="?", default="TJSP")
    args = parser.parse_args(argv)

    configurar_logs()
    config = ConfiguracoesWorker()

    async def executar() -> int:
        if args.comando is None:
            await rodar(config)
            return 0
        if args.comando == "sonda":
            return await sonda(config, args.tribunal)
        async with montar(config) as pipeline:
            if args.comando == "coletar":
                print(await pipeline.coletar(args.tribunal.upper(), args.dia, args.termo), "publicacoes novas")
            elif args.comando == "triar":
                print(await pipeline.triar(), "(triadas, relevantes)")
            elif args.comando == "extrair":
                resumo = ResumoCiclo()
                await pipeline.extrair(resumo, args.limite)
                print(resumo)
        return 0

    return asyncio.run(executar())


if __name__ == "__main__":
    sys.exit(principal())
