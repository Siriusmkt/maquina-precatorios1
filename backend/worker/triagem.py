"""Triagem barata por expressoes regulares, antes de qualquer custo com IA.

Uma publicacao e relevante quando tem pelo menos um sinal de evento E um
contexto de credito contra o poder publico (precatorio, RPV, requisitorio,
Fazenda). O contexto corta o ruido de "cessao de credito" ou "penhora" em
disputas privadas. Tudo roda sobre o texto sem acentos e em minusculas.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento.lower())


_P = re.compile

SINAIS: dict[str, tuple[re.Pattern[str], ...]] = {
    "requisitorio_expedido": (
        _P(
            r"expeca-se\s+(?:o\s+|os\s+|a\s+)?(?:oficio\s+)?"
            r"(?:precatorio|requisitorio|rpv|requisicao de pequeno valor)"
        ),
        _P(r"expedi(?:cao|do|da|r)\s+(?:do\s+|de\s+|o\s+|a\s+)?(?:oficio\s+)?(?:precatorio|requisitorio|rpv)"),
        _P(
            r"(?:oficio requisitorio|requisicao de pequeno valor|rpv|precatorio)\s+"
            r"(?:foi\s+)?(?:expedid[oa]|transmitid[oa])"
        ),
    ),
    "calculos_homologados": (
        _P(r"homolog(?:o|a-se|ados?|uei|ou)\s+(?:os\s+)?calculos"),
        _P(r"homologacao\s+dos\s+calculos"),
    ),
    "habilitacao_herdeiros": (
        _P(r"habilitac(?:ao|oes)\s+d(?:e|os)\s+(?:herdeiros|sucessores)"),
        _P(r"habilit(?:o|em-se|ados?)\s+(?:os\s+)?(?:herdeiros|sucessores)"),
        _P(r"sucessores\s+habilitados"),
    ),
    "cessao_comunicada": (
        _P(r"cessao\s+(?:de|do)\s+credito"),
        _P(r"\bcessionari[oa]s?\b"),
        _P(r"instrumento\s+(?:particular\s+|publico\s+)?de\s+cessao"),
    ),
    "penhora": (_P(r"penhora\s+(?:no\s+rosto\s+dos\s+autos|do\s+credito|sobre\s+o\s+credito)"),),
    "embargos": (
        _P(r"embargos\s+(?:a|de)\s+execucao"),
        _P(r"impugnacao\s+ao\s+cumprimento\s+de\s+sentenca"),
    ),
    "acordo_direto_pedido": (
        _P(r"acordo\s+direto"),
        _P(r"camara\s+de\s+conciliacao\s+de\s+precatorios"),
        _P(r"adesao\s+ao\s+(?:edital|acordo)"),
    ),
    "inscrito_loa": (
        _P(r"inscri(?:to|cao)\s+(?:no|na)\s+(?:orcamento|loa|proposta orcamentaria)"),
        _P(r"incluid[oa]\s+(?:no|na)\s+(?:orcamento|proposta orcamentaria)"),
    ),
    "levantamento": (_P(r"(?:mandado|alvara)\s+(?:eletronico\s+)?de\s+levantamento"),),
    "pagamento": (
        _P(r"pagamento\s+(?:do|da)\s+(?:precatorio|requisitorio|rpv|requisicao)"),
        _P(r"(?:precatorio|rpv|requisitorio)\s+(?:foi\s+)?(?:pago|quitado)"),
    ),
}

CONTEXTO = _P(
    r"precatori|requisitori|requisicao de pequeno valor|\brpv\b"
    r"|fazenda publica|fazenda (?:do estado|nacional|municipal)"
    r"|execucao contra a fazenda|cumprimento de sentenca contra|\bente publico\b|\binss\b|uniao federal"
)


@dataclass(frozen=True)
class Triagem:
    relevante: bool
    sinais: tuple[str, ...]


def triar(texto: str) -> Triagem:
    t = normalizar(texto)
    sinais = tuple(sorted(tipo for tipo, padroes in SINAIS.items() if any(p.search(t) for p in padroes)))
    return Triagem(relevante=bool(sinais) and CONTEXTO.search(t) is not None, sinais=sinais)
