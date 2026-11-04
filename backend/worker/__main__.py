"""Linha de comando dos workers.

python -m worker                      ciclo continuo (coleta -> triagem -> Leitor IA)
python -m worker coletar TJSP 2026-11-02 [--termo requisitório]
python -m worker triar
python -m worker extrair [--limite 50]
python -m worker sonda [TJSP]         testa o acesso ao DJEN e mostra o formato (sem dados pessoais)
python -m worker sonda-whatsapp <id-do-cliente>
                                      confere o token e o numero do WhatsApp na Meta (nao envia nada)
python -m worker sonda-bureau <id-do-cliente> <cpf>
                                      uma consulta real ao bureau; mostra so o formato e as contagens
python -m worker sonda-datajud TJSP <numero-cnj>
                                      uma consulta real ao DataJud; mostra o formato e os movimentos reconhecidos
python -m worker importar-lista <arquivo.csv> --fonte depre_tjsp --tribunal TJSP [--aplicar]
                                      lista oficial (DEPRE, TRT, LOA); sem --aplicar so mostra o que entendeu
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import re
import signal
import sys
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from pathlib import Path

import httpx

from app.dominio.cnj import normalizar_cnj
from app.dominio.documento import cpf_valido
from app.logs import configurar_logs
from worker.config import ConfiguracoesWorker
from worker.djen import DjenBloqueado, ErroDjen
from worker.listas import FONTES as FONTES_DE_LISTA
from worker.listas import importar as importar_lista
from worker.montagem import cliente_datajud, cliente_djen, montar
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


async def repetir(tarefa: Callable[[], Awaitable[object]], parar: asyncio.Event, intervalo_s: float, nome: str) -> None:
    while not parar.is_set():
        try:
            await tarefa()
        except Exception:
            log.exception("%s falhou; nova tentativa na proxima rodada", nome)
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
        async with montar(config) as servicos:
            # Mensagens e cadencias andam a cada poucos segundos; a coleta, a cada ciclo.
            await asyncio.gather(
                repetir(servicos.pipeline.ciclo, parar, config.ciclo_s, "ciclo"),
                repetir(servicos.mensageiro.ciclo, parar, config.mensageiro_s, "mensageiro"),
                repetir(servicos.resolvedor.ciclo, parar, config.resolvedor_s, "resolvedor"),
                repetir(servicos.enriquecedor.ciclo, parar, config.ciclo_s, "datajud"),
            )
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


async def sonda_datajud(
    config: ConfiguracoesWorker, tribunal: str, numero: str, *, transporte: httpx.AsyncBaseTransport | None = None
) -> int:
    """Uma consulta real ao DataJud (nao precisa do banco). A API publica nao traz partes."""
    if config.datajud_chave is None:
        print("Defina MAQUINA_DATAJUD_CHAVE com a chave publica divulgada pelo CNJ.")
        return 2
    async with cliente_datajud(config, transporte, forcar=True) as cliente:
        if cliente is None:  # pragma: no cover - forcar com chave sempre cria
            return 2
        relatorio = await cliente.sondar(tribunal, numero)
    print(json.dumps(relatorio, ensure_ascii=False, indent=2))
    return 0 if relatorio.get("acesso") == "ok" else 1


def cnj_do_argumento(valor: str) -> str:
    numero = normalizar_cnj(valor)
    if numero is None:
        raise argparse.ArgumentTypeError("numero CNJ invalido")
    return numero


def cpf_do_argumento(valor: str) -> str:
    cpf = re.sub(r"\D", "", valor)
    if not cpf_valido(cpf):
        raise argparse.ArgumentTypeError("CPF invalido")
    return cpf


def argumentos() -> argparse.ArgumentParser:
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
    p_sonda_wa = sub.add_parser("sonda-whatsapp")
    p_sonda_wa.add_argument("tenant", type=uuid.UUID)
    p_sonda_bureau = sub.add_parser("sonda-bureau")
    p_sonda_bureau.add_argument("tenant", type=uuid.UUID)
    p_sonda_bureau.add_argument("cpf", type=cpf_do_argumento)
    p_sonda_datajud = sub.add_parser("sonda-datajud")
    p_sonda_datajud.add_argument("tribunal")
    p_sonda_datajud.add_argument("numero", type=cnj_do_argumento)
    p_lista = sub.add_parser("importar-lista")
    p_lista.add_argument("arquivo", type=Path)
    p_lista.add_argument("--fonte", required=True, choices=FONTES_DE_LISTA)
    p_lista.add_argument("--tribunal", required=True, type=str.upper)
    p_lista.add_argument("--aplicar", action="store_true", help="grava; sem isto, so mostra o que entendeu")
    return parser


async def executar(args: argparse.Namespace, config: ConfiguracoesWorker) -> int:
    if args.comando is None:
        await rodar(config)
        return 0
    if args.comando == "sonda":
        return await sonda(config, args.tribunal)
    if args.comando == "sonda-datajud":
        return await sonda_datajud(config, args.tribunal.upper(), args.numero)
    async with montar(config) as servicos:
        if args.comando in ("sonda-whatsapp", "sonda-bureau"):
            if args.comando == "sonda-whatsapp":
                relatorio = await servicos.mensageiro.conferir_integracao(args.tenant)
            else:
                relatorio = await servicos.resolvedor.conferir_integracao(args.tenant, args.cpf)
            print(json.dumps(relatorio, ensure_ascii=False, indent=2))
            return 0 if relatorio.get("acesso") == "ok" else 1
        if args.comando == "importar-lista":
            importacao = await importar_lista(
                servicos.pool,
                args.arquivo.read_bytes(),
                fonte=args.fonte,
                tribunal=args.tribunal,
                arquivo=args.arquivo.name,
                hoje=agora_sp().date(),
                aplicar=args.aplicar,
            )
            print(json.dumps(importacao.como_dict(), ensure_ascii=False, indent=2, default=str))
            return 0
        pipeline = servicos.pipeline
        if args.comando == "coletar":
            print(await pipeline.coletar(args.tribunal.upper(), args.dia, args.termo), "publicacoes novas")
        elif args.comando == "triar":
            print(await pipeline.triar(), "(triadas, relevantes)")
        elif args.comando == "extrair":
            resumo = ResumoCiclo()
            await pipeline.extrair(resumo, args.limite)
            print(resumo)
    return 0


def principal(argv: list[str] | None = None) -> int:
    args = argumentos().parse_args(argv)
    configurar_logs()
    return asyncio.run(executar(args, ConfiguracoesWorker()))


if __name__ == "__main__":
    sys.exit(principal())
