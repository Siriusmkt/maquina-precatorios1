"""Erros da API no formato Problem Details (RFC 9457).

Regra de ouro: o cliente recebe um codigo estavel e uma mensagem humana;
detalhes internos (SQL, stack trace, nomes de tabela) ficam so no log,
ligados pelo request_id.
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("maquina.erros")

MENSAGENS: dict[str, str] = {
    "sem_permissao": "Você não tem permissão para esta ação.",
    "nao_autenticado": "Sua sessão expirou. Entre novamente.",
    "credenciais_invalidas": "E-mail ou senha incorretos.",
    "limite_excedido": "Muitas tentativas. Aguarde um pouco e tente de novo.",
    "servico_indisponivel": "Serviço temporariamente indisponível.",
    "nao_encontrado": "Não encontrado.",
    "metodo_nao_permitido": "Método não permitido.",
    "dados_invalidos": "Dados inválidos.",
    "duplicado": "Esse registro já existe.",
    "referencia_invalida": "Referência inválida.",
    "tempo_esgotado": "A operação demorou demais. Tente novamente.",
    "erro_interno": "Algo deu errado do nosso lado. Já registramos o problema.",
    # Erros de negocio vindos do banco (SQLSTATE MQxxx)
    "requisicao_nao_encontrada": "Requisição não encontrada.",
    "requisicao_indisponivel": "Esse precatório não está mais disponível.",
    "cota_leads_mes_excedida": "A cota mensal de leads do seu plano acabou.",
    "lead_nao_encontrado": "Lead não encontrado.",
    "interacao_nao_encontrada": "Interação não encontrada.",
    "contato_nao_encontrado": "Contato não encontrado.",
    "contato_com_oposicao": "O titular pediu para não ser contatado.",
    "codigo_nao_encontrado": "Código não encontrado.",
    "validade_invalida": "Validade inválida.",
    "falha_ao_gerar_codigo": "Não foi possível gerar o código. Tente novamente.",
    "aprovacao_exige_gestor": "Só gestores podem aprovar propostas.",
    "fora_da_alcada": "Deságio abaixo da sua alçada: precisa da aprovação do owner.",
    "proposta_congelada": "Proposta aprovada não pode ser alterada. Crie uma nova.",
    "transicao_invalida": "Mudança de status não permitida.",
    "status_inicial_invalido": "Status inicial inválido.",
    "limite_assentos": "Todos os assentos do plano estão ocupados.",
    "auditoria_imutavel": "A trilha de auditoria não pode ser alterada.",
    "orcamento_radar_esgotado": "Limite diário de consultas ao radar atingido.",
    "estagio_invalido": "Mudança de estágio não permitida.",
    "motivo_obrigatorio": "Informe o motivo da perda.",
    # Equipe e preferencias
    "ja_e_membro": "Essa pessoa já faz parte da equipe.",
    "membro_nao_encontrado": "Essa pessoa não faz parte da equipe.",
    "ultimo_owner": "A equipe precisa de pelo menos um dono. Promova outra pessoa antes.",
    "preferencia_invalida": "Valor fora da faixa permitida.",
    "convites_indisponiveis": "Convites desligados: falta configurar a chave de serviço do Supabase.",
    "link_invalido": "Este link expirou ou já foi usado. Peça um novo.",
    "senha_fraca": "Use uma senha com pelo menos 10 caracteres, misturando letras e números.",
    # Contato
    "whatsapp_desligado": "O WhatsApp oficial não está ligado. Configure na tela Contato.",
    "sem_autorizacao_whatsapp": "O credor ainda não autorizou mensagens pelo WhatsApp. Registre a autorização antes.",
    "fora_da_janela": "Passaram mais de 24 horas desde a última mensagem do credor: use um modelo aprovado.",
    "modelo_nao_cadastrado": "Esse modelo não está cadastrado na integração do WhatsApp.",
    "cadencia_pausada": "Essa cadência está pausada.",
    "sem_endereco": "Cadastre o endereço do credor nos contatos para gerar a carta.",
    "sem_contato_publico": "Defina o contato oficial em Equipe para gerar cartas: é por ele que o credor responde.",
    "contato_ilegivel": "Não foi possível ler este contato. Cadastre de novo.",
}

_STATUS_NEGOCIO = {"MQ403": 403, "MQ404": 404, "MQ409": 409, "MQ422": 422, "MQ429": 429}
_STATUS_SQL = {
    "42501": (403, "sem_permissao"),
    "23503": (404, "referencia_invalida"),
    "23505": (409, "duplicado"),
    "23514": (422, "dados_invalidos"),
    "22P02": (422, "dados_invalidos"),
    "57014": (503, "tempo_esgotado"),
}


# Classe 08 (conexao) e 57P0x (servidor encerrando): indisponibilidade, nao bug.
_CONEXAO_PERDIDA = frozenset({"57P01", "57P02", "57P03"})


class ErroApi(Exception):
    def __init__(
        self,
        status: int,
        codigo: str,
        detalhe: str | None = None,
        *,
        cabecalhos: dict[str, str] | None = None,
    ) -> None:
        super().__init__(codigo)
        self.status = status
        self.codigo = codigo
        self.detalhe = detalhe
        self.cabecalhos = cabecalhos or {}


def erro_do_banco(exc: psycopg.Error) -> ErroApi | None:
    estado = exc.sqlstate or ""
    if estado in _STATUS_NEGOCIO:
        diag = exc.diag
        return ErroApi(_STATUS_NEGOCIO[estado], diag.message_primary or "erro_interno", diag.message_detail)
    if estado in _STATUS_SQL:
        status, codigo = _STATUS_SQL[estado]
        return ErroApi(status, codigo)
    if estado.startswith("08") or estado in _CONEXAO_PERDIDA:
        return ErroApi(503, "servico_indisponivel")
    return None


def resposta_problema(
    request: Request, status: int, codigo: str, detalhe: str | None = None, **extra: Any
) -> JSONResponse:
    corpo: dict[str, Any] = {
        "type": "about:blank",
        "title": MENSAGENS.get(codigo, MENSAGENS["erro_interno"] if status >= 500 else "Requisição recusada."),
        "status": status,
        "codigo": codigo,
        "request_id": getattr(request.state, "request_id", None),
    }
    if detalhe:
        corpo["detail"] = detalhe
    corpo.update(extra)
    return JSONResponse(corpo, status_code=status, media_type="application/problem+json")


def registrar_tratadores(app: FastAPI) -> None:
    @app.exception_handler(ErroApi)
    async def _erro_api(request: Request, exc: ErroApi) -> JSONResponse:
        resposta = resposta_problema(request, exc.status, exc.codigo, exc.detalhe)
        resposta.headers.update(exc.cabecalhos)
        return resposta

    @app.exception_handler(psycopg.Error)
    async def _erro_banco(request: Request, exc: psycopg.Error) -> JSONResponse:
        mapeado = erro_do_banco(exc)
        if mapeado is None:
            log.error("erro de banco nao mapeado", extra={"sqlstate": exc.sqlstate}, exc_info=exc)
            return resposta_problema(request, 500, "erro_interno")
        if mapeado.status >= 500:
            log.warning("banco: %s", mapeado.codigo, extra={"sqlstate": exc.sqlstate})
        return resposta_problema(request, mapeado.status, mapeado.codigo, mapeado.detalhe)

    @app.exception_handler(RequestValidationError)
    async def _validacao(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Nunca ecoar o valor enviado (pode conter dado pessoal ou payload malicioso).
        campos = [
            {"campo": ".".join(str(p) for p in erro.get("loc", ()) if p != "body"), "problema": erro.get("type")}
            for erro in exc.errors()
        ]
        return resposta_problema(request, 422, "dados_invalidos", campos=campos)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        codigo = {404: "nao_encontrado", 405: "metodo_nao_permitido", 401: "nao_autenticado"}.get(
            exc.status_code, "dados_invalidos"
        )
        return resposta_problema(request, exc.status_code, codigo)

    # Excecoes inesperadas sao tratadas em SegurancaHttp (garante cabecalhos e request_id).
