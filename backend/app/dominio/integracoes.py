"""Segredos das integracoes (WhatsApp, bureau): cifrados com contexto do cliente e do tipo."""

from __future__ import annotations

import uuid
from typing import Literal

TipoIntegracao = Literal["whatsapp", "bureau"]


def contexto_segredos(tenant_id: uuid.UUID, tipo: TipoIntegracao) -> str:
    """AAD da cifra: o blob copiado para outro cliente (ou outro tipo) nao decifra."""
    return f"integracoes:{tenant_id}:{tipo}"
