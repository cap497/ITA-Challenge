# -*- coding: utf-8 -*-
"""Execução do pipeline em segundo plano, com as etapas visíveis na tela.

Antes, o pipeline rodava no boot: `python app.py` ficava parado no terminal
enquanto o motor trabalhava, e o navegador só abria depois. Isso está errado por
dois motivos. O primeiro é de desenho: quem usa a plataforma não olha terminal,
e um processo que não dá sinal de vida parece travado — porque, do ponto de
vista de quem espera, está mesmo. O segundo é prático: qualquer etapa lenta
segurava a aplicação inteira antes de existir qualquer tela para explicar o que
estava acontecendo.

Agora a aplicação sobe vazia e em um segundo. O pipeline é disparado da tela, em
uma thread, e publica a etapa em que está. A interface pergunta o estado a cada
segundo e mostra a etapa, o tempo decorrido e o que saiu no fim.

Uma execução por vez: duas rodadas simultâneas gravariam na mesma data e a
segunda morreria no meio do caminho.
"""

from __future__ import annotations

import threading
import time
import traceback

# As etapas na ordem em que acontecem. A tela desenha a trilha inteira desde o
# começo, com as próximas em cinza — assim quem espera sabe quanto falta, e não
# só onde está.
ETAPAS = [
    ("ler", "Ler a base", "cadastro, custos, estoque e coleta de concorrência"),
    ("decidir", "Decidir item a item",
     "referência de mercado ponderada, piso de margem, R01–R12, preço e risco"),
    ("carteira", "Selecionar a carteira",
     "otimização inteira: quais preços mudar hoje, dentro da capacidade do dia"),
    ("estimar", "Estimar", "volume esperado e chance de aceite"),
    ("escrever", "Escrever as justificativas", "em linguagem de negócio, item a item"),
    ("pronto", "Pronto", ""),
]

_trava = threading.Lock()
_estado: dict = {"rodando": False, "etapa": None, "texto": "", "inicio": None,
                 "fim": None, "resultado": None, "erro": None, "historico": []}


# Cadência do coletor da Popular Pet: a coleta de concorrência é diária e os
# dados de custo e estoque vêm do ERP de madrugada. O pipeline acompanha essa
# cadência — não existe motivo para recalcular preço entre duas coletas.
COLETA_DIARIA_HORA = 7


def _proxima_coleta() -> str:
    from datetime import datetime, timedelta
    agora = datetime.now()
    alvo = agora.replace(hour=COLETA_DIARIA_HORA, minute=0, second=0, microsecond=0)
    if alvo <= agora:
        alvo += timedelta(days=1)
    return alvo.strftime("%d/%m às %Hh")


def estado() -> dict:
    with _trava:
        e = dict(_estado)
        e["historico"] = list(_estado["historico"])
    e["etapas"] = [{"chave": c, "titulo": t, "detalhe": d} for c, t, d in ETAPAS]
    e["segundos"] = (round((e["fim"] or time.time()) - e["inicio"], 1)
                     if e["inicio"] else None)

    # O que a faixa do painel mostra quando nada está rodando: de quando é o
    # cenário na tela e quando ele se atualiza sozinho de novo.
    ultima = None
    if e["fim"]:
        from datetime import datetime
        ultima = {
            "quando": datetime.fromtimestamp(e["fim"]).isoformat(timespec="seconds"),
            "resumo": (e["resultado"] or {}).get("resumo") or e["texto"],
            "segundos": e["segundos"],
        }
    e["ultima"] = ultima
    e["proxima"] = _proxima_coleta()
    e["cadencia"] = "coleta de concorrência diária às %dh; o pipeline dispara sozinho " \
                    "assim que o dado novo chega" % COLETA_DIARIA_HORA
    return e


def _marcar(etapa: str, texto: str) -> None:
    with _trava:
        _estado["etapa"] = etapa
        _estado["texto"] = texto
        agora = time.time()
        hist = _estado["historico"]
        if hist and hist[-1]["etapa"] == etapa:
            hist[-1]["texto"] = texto
        else:
            if hist:
                hist[-1]["segundos"] = round(agora - hist[-1]["quando"], 2)
            hist.append({"etapa": etapa, "texto": texto, "quando": agora,
                         "segundos": None})


def _correr(passos) -> None:
    try:
        resultado = None
        for passo in passos:
            resultado = passo(_marcar) or resultado
        with _trava:
            _estado["resultado"] = resultado
    except Exception as e:                                        # noqa: BLE001
        traceback.print_exc()
        with _trava:
            _estado["erro"] = "%s: %s" % (type(e).__name__, e)
            _estado["etapa"] = "erro"
            _estado["texto"] = str(e)
    finally:
        with _trava:
            _estado["rodando"] = False
            _estado["fim"] = time.time()
            if _estado["historico"] and _estado["historico"][-1]["segundos"] is None:
                _estado["historico"][-1]["segundos"] = round(
                    _estado["fim"] - _estado["historico"][-1]["quando"], 2)


def disparar(passos) -> dict:
    """Começa o pipeline numa thread. Devolve o estado inicial."""
    with _trava:
        if _estado["rodando"]:
            return dict(_estado, ja_rodando=True)
        _estado.update({"rodando": True, "etapa": "ler", "texto": "começando…",
                        "inicio": time.time(), "fim": None, "resultado": None,
                        "erro": None, "historico": []})
    threading.Thread(target=_correr, args=(passos,), daemon=True).start()
    return estado()


# ------------------------------------------------------------------ passos
def passo_sincronizar(usuario: str = "Sistema"):
    def _p(marcar):
        from nucleo import erp
        marcar("ler", "lendo a planilha do ERP")
        r = erp.sincronizar(rotulo_origem="carga inicial", usuario=usuario)
        marcar("ler", r["mensagem"])
        return None
    return _p


def passo_rodada(cfg=None, semear: bool = False):
    def _p(marcar):
        from nucleo import motor, servicos
        r = servicos.gerar_rodada(cfg or motor.Config(), aviso=marcar)
        if semear:
            from nucleo import semente
            marcar("escrever", "semeando o histórico de demonstração")
            s = semente.semear(r["rodada_id"])
            r["semeadas"] = s.get("semeadas", 0)
        marcar("pronto", "rodada de %s · %d itens · otimização %s em %.0f ms"
               % (r["data"], r["itens"], r["otimizacao"]["metodo"], r["otimizacao"]["ms"]))
        return r
    return _p


def passo_estrategicos():
    def _p(marcar):
        from nucleo import semente
        marcar("ler", "marcando os produtos estratégicos da demonstração")
        semente.marcar_estrategicos_demo()
        return None
    return _p
