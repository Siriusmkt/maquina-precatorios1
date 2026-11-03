"""Tenant ativo da requisicao: papel do usuario e direitos do plano."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.banco.sessao import Conexao
from app.dominio.funil import EDITORES, Papel
from app.dominio.preco import Parametros
from app.erros import ErroApi

# Premissas padrao do comprador (o cliente ajusta em tenants.config).
PADROES_PRECO = {
    "taxa_alvo_aa": Decimal("0.22"),
    "tir_minima_aa": Decimal("0.16"),
    "ipca_aa": Decimal("0.045"),
    "juros_aa": Decimal("0.02"),
    "aliquota_ir": Decimal("0.03"),
}
PRAZO_PADRAO_MESES = Decimal(48)


@dataclass(frozen=True)
class TenantAtivo:
    id: uuid.UUID
    nome: str
    papel: Papel
    tribunais_permitidos: list[str] | None
    limite_radar_dia: int | None
    limite_leads_mes: int | None
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def editor(self) -> bool:
        return self.papel in EDITORES

    def exigir_editor(self) -> None:
        if not self.editor:
            raise ErroApi(403, "sem_permissao")

    def parametros_preco(self, **sobrescrever: Decimal | None) -> Parametros:
        valores: dict[str, Decimal] = {}
        for chave, padrao in PADROES_PRECO.items():
            escolhido = sobrescrever.get(chave)
            if escolhido is None:
                bruto = self.config.get(chave)
                escolhido = Decimal(str(bruto)) if bruto is not None else padrao
            valores[chave] = escolhido
        return Parametros(**valores)

    @property
    def prazo_padrao_meses(self) -> Decimal:
        bruto = self.config.get("prazo_padrao_meses")
        return Decimal(str(bruto)) if bruto is not None else PRAZO_PADRAO_MESES


async def carregar_tenant(conn: Conexao, tenant_id: uuid.UUID) -> TenantAtivo:
    """404 se o usuario nao for membro (nao revela se o tenant existe)."""
    cursor = await conn.execute(
        """select t.id, t.nome, m.papel::text as papel, t.tribunais_permitidos,
                  t.limite_radar_dia, t.limite_leads_mes, t.config
             from app.membros m
             join app.tenants t on t.id = m.tenant_id
            where m.tenant_id = %s and m.user_id = (select auth.uid())""",
        [tenant_id],
    )
    linha = await cursor.fetchone()
    if linha is None:
        raise ErroApi(404, "nao_encontrado")
    return TenantAtivo(**linha)
