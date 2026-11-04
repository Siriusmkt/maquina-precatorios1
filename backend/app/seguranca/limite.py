"""Limite de requisicoes com GCRA (Generic Cell Rate Algorithm) no Redis.

Uma chave por balde, um script Lua atomico, relogio do proprio Redis (sem
desvio entre instancias da API). Se o Redis cair, falha fechado: melhor
recusar do que abrir a porta para forca bruta e raspagem.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

_GCRA = """
local intervalo = tonumber(ARGV[1])
local tolerancia = tonumber(ARGV[2])
local custo = tonumber(ARGV[3])
local t = redis.call('TIME')
local agora = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local tat = tonumber(redis.call('GET', KEYS[1]) or agora)
if tat < agora then tat = agora end
local novo_tat = tat + intervalo * custo
local liberado_em = novo_tat - tolerancia
if agora < liberado_em then
  return {0, liberado_em - agora, 0}
end
redis.call('SET', KEYS[1], novo_tat, 'PX', math.ceil(novo_tat - agora))
return {1, 0, math.floor((tolerancia - (novo_tat - agora)) / intervalo)}
"""


@dataclass(frozen=True)
class Politica:
    """``limite`` acoes por ``periodo_s`` segundos, permitindo rajada de ``limite``."""

    nome: str
    limite: int
    periodo_s: int

    @property
    def intervalo_ms(self) -> float:
        return self.periodo_s * 1000 / self.limite

    @property
    def tolerancia_ms(self) -> float:
        return self.periodo_s * 1000


@dataclass(frozen=True)
class Resultado:
    permitido: bool
    restantes: int
    tentar_em_s: int


class LimiteIndisponivel(Exception):
    """Redis fora do ar: o chamador deve recusar a requisicao (falha fechada)."""


class Limitador:
    def __init__(self, redis: Redis, prefixo: str = "limite") -> None:
        self._redis = redis
        self._prefixo = prefixo
        self._script: Any = redis.register_script(_GCRA)

    def _chave(self, politica: Politica, identidade: str) -> str:
        # Identidades podem ser e-mails/IPs: guardamos so o hash.
        resumo = hashlib.sha256(identidade.encode()).hexdigest()[:32]
        return f"{self._prefixo}:{politica.nome}:{resumo}"

    async def consumir(self, politica: Politica, identidade: str, custo: int = 1) -> Resultado:
        try:
            permitido, espera_ms, restantes = await self._script(
                keys=[self._chave(politica, identidade)],
                args=[politica.intervalo_ms, politica.tolerancia_ms, custo],
            )
        except RedisError as exc:
            raise LimiteIndisponivel from exc
        return Resultado(
            permitido=bool(permitido),
            restantes=max(int(restantes), 0),
            tentar_em_s=max(1, -(-int(espera_ms) // 1000)) if not permitido else 0,
        )


# Politicas da aplicacao (ajustaveis por plano no futuro).
LOGIN_POR_IP = Politica("login_ip", limite=20, periodo_s=60)
LOGIN_POR_EMAIL = Politica("login_email", limite=5, periodo_s=900)
RENOVACAO_POR_IP = Politica("renovacao_ip", limite=30, periodo_s=60)
API_POR_USUARIO = Politica("api_usuario", limite=600, periodo_s=60)
RADAR_POR_USUARIO = Politica("radar_usuario", limite=90, periodo_s=60)
REVELAR_CONTATO_POR_USUARIO = Politica("revelar_contato", limite=60, periodo_s=3600)
VERIFICACAO_POR_IP = Politica("verificacao_ip", limite=10, periodo_s=60)
VERIFICACAO_POR_IP_DIA = Politica("verificacao_ip_dia", limite=100, periodo_s=86400)
SIMULADOR_POR_USUARIO = Politica("simulador", limite=120, periodo_s=60)
CONVITE_POR_USUARIO = Politica("convite", limite=30, periodo_s=3600)
RECUPERAR_POR_IP = Politica("recuperar_ip", limite=10, periodo_s=900)
RECUPERAR_POR_EMAIL = Politica("recuperar_email", limite=3, periodo_s=3600)
DEFINIR_SENHA_POR_IP = Politica("definir_senha_ip", limite=10, periodo_s=900)
PDF_POR_USUARIO = Politica("pdf", limite=120, periodo_s=3600)
WHATSAPP_POR_USUARIO = Politica("whatsapp", limite=120, periodo_s=3600)
WEBHOOK_POR_IP = Politica("webhook_ip", limite=1200, periodo_s=60)
ENRIQUECER_POR_USUARIO = Politica("enriquecer", limite=120, periodo_s=3600)
PLANILHA_POR_USUARIO = Politica("planilha", limite=60, periodo_s=3600)
DOCUMENTO_POR_USUARIO = Politica("documento", limite=200, periodo_s=3600)
IA_POR_USUARIO = Politica("copiloto", limite=60, periodo_s=3600)
