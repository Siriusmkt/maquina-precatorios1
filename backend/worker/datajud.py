"""DataJud (API publica do CNJ): dados e movimentos do processo de origem.

Pedido conforme a documentacao publica do CNJ: POST em
``{base}/api_publica_{tribunal}/_search`` com o cabecalho
``Authorization: APIKey <chave publica>`` e uma consulta Elasticsearch pelo
``numeroProcesso``. A chave publica e divulgada pelo CNJ e muda de tempos em
tempos: fica na configuracao, nunca no codigo. A leitura da resposta e
tolerante e a sonda (python -m worker sonda-datajud) mostra o formato que
voltou, sem dado de parte (a API publica nao traz partes).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from worker.triagem import normalizar

# Movimento (nome + complementos, sem acento) -> tipo de evento do radar.
REGRAS_MOVIMENTO: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(expedi|emiss|remessa)\w*.*(precatori|requisit|rpv|pequeno valor)"), "requisitorio_expedido"),
    (re.compile(r"(precatori|requisit)\w*.*expedi"), "requisitorio_expedido"),
    (re.compile(r"homolog\w*.*calculo|calculo\w*.*homolog"), "calculos_homologados"),
    (re.compile(r"habilita\w*.*(herdeir|sucess)|sucessao processual"), "habilitacao_herdeiros"),
    (re.compile(r"cess(ao|oes) de credito"), "cessao_comunicada"),
    (re.compile(r"\bpenhora"), "penhora"),
    (re.compile(r"\bembargos"), "embargos"),
    (re.compile(r"levantamento|alvara"), "levantamento"),
    (re.compile(r"pagamento\w*.*(precatori|requisit|rpv)|(precatori|requisit|rpv)\w*.*\bpag"), "pagamento"),
)
TRANSITO = re.compile(r"transito em julgado")
FUSO = ZoneInfo("America/Sao_Paulo")


class ErroDatajud(Exception):
    def __init__(self, motivo: str, *, retentavel: bool, chave_invalida: bool = False) -> None:
        super().__init__(motivo)
        self.motivo = motivo
        self.retentavel = retentavel
        self.chave_invalida = chave_invalida


@dataclass(frozen=True)
class Movimento:
    codigo: int | None
    nome: str
    quando: datetime
    tipo: str | None  # tipo de evento do radar, ou None se o movimento nao interessa
    transito: bool = False


@dataclass
class ProcessoDatajud:
    numero: str
    classe_codigo: int | None = None
    classe_nome: str | None = None
    orgao_julgador: str | None = None
    assuntos: list[dict[str, Any]] = field(default_factory=list)
    sigiloso: bool = False
    data_ajuizamento: date | None = None
    grau: str | None = None
    movimentos: list[Movimento] = field(default_factory=list)

    @property
    def ultimo_movimento_em(self) -> datetime | None:
        return max((m.quando for m in self.movimentos), default=None)


def alias_do_tribunal(sigla: str) -> str | None:
    """'TJSP' -> 'tjsp' (indice api_publica_tjsp). So siglas simples de tribunal."""
    s = sigla.strip().lower()
    return (
        s
        if re.fullmatch(r"(tj[a-z]{2,3}|trf[1-6]|trt[1-9][0-9]?|tre[a-z]{2}|tjm[a-z]{2}|stj|tst|tse|stm)", s)
        else None
    )


def _data_hora(valor: Any) -> datetime | None:
    """Aceita ISO ('2018-10-30T14:06:24.000Z') e o formato compacto ('20181030140624').

    Sem fuso (o compacto nunca tem), vale o horario de Brasilia: sempre devolve com fuso.
    """
    if not isinstance(valor, str) or not valor:
        return None
    texto = valor.strip()
    try:
        if re.fullmatch(r"\d{14}", texto):
            lido = datetime.strptime(texto, "%Y%m%d%H%M%S")  # noqa: DTZ007 - o fuso entra logo abaixo
        else:
            lido = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None
    return lido if lido.tzinfo else lido.replace(tzinfo=FUSO)


def classificar(nome: str, complementos: list[dict[str, Any]]) -> tuple[str | None, bool]:
    partes = [nome] + [str(c.get(k) or "") for c in complementos for k in ("nome", "descricao")]
    texto = normalizar(" ".join(partes))
    tipo = next((t for padrao, t in REGRAS_MOVIMENTO if padrao.search(texto)), None)
    return tipo, bool(TRANSITO.search(texto))


def _objeto(valor: Any) -> dict[str, Any]:
    return valor if isinstance(valor, dict) else {}


def ler_processo(fonte: Any) -> ProcessoDatajud | None:
    """Converte o _source do DataJud. Campo que falta ou vem torto e ignorado."""
    if not isinstance(fonte, dict) or not isinstance(fonte.get("numeroProcesso"), str):
        return None
    classe = _objeto(fonte.get("classe"))
    orgao = _objeto(fonte.get("orgaoJulgador"))
    ajuizamento = _data_hora(fonte.get("dataAjuizamento"))
    sigilo = fonte.get("nivelSigilo")
    processo = ProcessoDatajud(
        numero=re.sub(r"\D", "", fonte["numeroProcesso"]),
        classe_codigo=classe.get("codigo") if isinstance(classe.get("codigo"), int) else None,
        classe_nome=str(classe["nome"])[:200] if isinstance(classe.get("nome"), str) else None,
        orgao_julgador=str(orgao["nome"])[:200] if isinstance(orgao.get("nome"), str) else None,
        assuntos=[
            {"codigo": a.get("codigo"), "nome": str(a.get("nome"))[:200]}
            for a in fonte.get("assuntos") or []
            if isinstance(a, dict) and a.get("nome")
        ][:20],
        sigiloso=isinstance(sigilo, int) and not isinstance(sigilo, bool) and sigilo > 0,
        data_ajuizamento=ajuizamento.date() if ajuizamento else None,
        grau=str(fonte["grau"])[:10] if isinstance(fonte.get("grau"), str) else None,
    )
    for m in fonte.get("movimentos") or []:
        if not isinstance(m, dict) or not isinstance(m.get("nome"), str):
            continue
        quando = _data_hora(m.get("dataHora"))
        if quando is None:
            continue
        complementos = [c for c in m.get("complementosTabelados") or [] if isinstance(c, dict)]
        tipo, transito = classificar(m["nome"], complementos)
        codigo = m.get("codigo") if isinstance(m.get("codigo"), int) else None
        processo.movimentos.append(Movimento(codigo, m["nome"][:200], quando, tipo, transito))
    return processo


def descrever(status_http: int, dados: Any) -> dict[str, Any]:
    """Para a sonda: formato e contagens, sem textos do processo."""
    hits = (dados.get("hits") or {}).get("hits") if isinstance(dados, dict) else None
    fonte = hits[0].get("_source") if isinstance(hits, list) and hits and isinstance(hits[0], dict) else None
    processo = ler_processo(fonte)
    mapeados: dict[str, int] = {}
    for m in processo.movimentos if processo else []:
        if m.tipo:
            mapeados[m.tipo] = mapeados.get(m.tipo, 0) + 1
    return {
        "http": status_http,
        "achou": processo is not None,
        "campos": sorted(fonte) if isinstance(fonte, dict) else [],
        "movimentos": len(processo.movimentos) if processo else 0,
        "mapeados": mapeados,
        "transito_em_julgado": bool(processo and any(m.transito for m in processo.movimentos)),
        "sigiloso": bool(processo and processo.sigiloso),
    }


async def _dormir(segundos: float) -> None:
    await asyncio.sleep(segundos)


class ClienteDatajud:
    def __init__(
        self,
        http: httpx.AsyncClient,
        url_base: str,
        chave: str,
        *,
        intervalo_s: float = 1.0,
        dormir: Callable[[float], Awaitable[None]] = _dormir,
    ) -> None:
        self._http = http
        self._base = url_base.rstrip("/")
        self._chave = chave
        self._intervalo = intervalo_s
        self._dormir = dormir
        self._ultimo = 0.0

    async def _pedir(self, tribunal: str, numero: str) -> httpx.Response:
        alias = alias_do_tribunal(tribunal)
        if alias is None:
            raise ErroDatajud("tribunal_sem_indice", retentavel=False)
        laco = asyncio.get_running_loop()
        espera = self._ultimo + self._intervalo - laco.time()
        if espera > 0:  # educado com a fonte: no maximo uma consulta por intervalo
            await self._dormir(espera)
        self._ultimo = laco.time()
        try:
            return await self._http.post(
                f"{self._base}/api_publica_{alias}/_search",
                json={"query": {"match": {"numeroProcesso": numero}}, "size": 1},
                headers={"authorization": f"APIKey {self._chave}", "accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise ErroDatajud(f"rede: {type(exc).__name__}", retentavel=True) from exc

    @staticmethod
    def _conferir(resposta: httpx.Response) -> Any:
        if resposta.status_code in (401, 403):
            raise ErroDatajud(f"datajud_{resposta.status_code}", retentavel=False, chave_invalida=True)
        if resposta.status_code == 404:
            raise ErroDatajud("tribunal_sem_indice", retentavel=False)
        if resposta.status_code == 429 or resposta.status_code >= 500:
            raise ErroDatajud(f"datajud_{resposta.status_code}", retentavel=True)
        if resposta.status_code != 200:
            raise ErroDatajud(f"datajud_{resposta.status_code}", retentavel=False)
        try:
            return resposta.json()
        except ValueError as exc:
            raise ErroDatajud("resposta_nao_json", retentavel=True) from exc

    async def consultar(self, tribunal: str, numero: str) -> ProcessoDatajud | None:
        dados = self._conferir(await self._pedir(tribunal, numero))
        hits = (dados.get("hits") or {}).get("hits") if isinstance(dados, dict) else None
        if not isinstance(hits, list) or not hits or not isinstance(hits[0], dict):
            return None
        return ler_processo(hits[0].get("_source"))

    async def sondar(self, tribunal: str, numero: str) -> dict[str, Any]:
        try:
            resposta = await self._pedir(tribunal, numero)
            dados = self._conferir(resposta)
        except ErroDatajud as exc:
            estado = "chave_invalida" if exc.chave_invalida else "falhou"
            return {"acesso": estado, "motivo": exc.motivo}
        return {"acesso": "ok", **descrever(resposta.status_code, dados)}
