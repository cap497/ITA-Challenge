# -*- coding: utf-8 -*-
"""Histórico de demonstração — decisões plausíveis para as telas nascerem cheias.

Sem isto, a primeira execução mostra aceite "—", linha de base sem medida e a
tela de impacto vazia, e quem abre a plataforma não vê o que ela faz. Com isto,
a demonstração começa em regime.

Três cuidados para não virar número inventado:

* cada decisão semeada é marcada no comentário como **demonstração**, e a tela
  de governança conta quantas são;
* a decisão depende do item: aprova mais quando o risco é baixo e a confiança do
  dado é alta, rejeita quando a cobertura é curta — é o comportamento que se
  espera de um analista, não sorteio uniforme;
* o cronômetro semeado sai de uma lognormal em torno de 40 s, que é a ordem de
  grandeza de quem lê uma justificativa pronta e clica. Esse número é medido de
  novo assim que alguém usar a tela de verdade, e aí o semeado deixa de pesar.

`python app.py --limpo` sobe sem nada disso.
"""

from __future__ import annotations

import hashlib
import math

from nucleo.banco import linha, sessao

MOTIVOS_REJEICAO = [
    "fornecedor já sinalizou reajuste para a semana que vem",
    "item usado como referência de preço na vitrine",
    "aguardando confirmação do custo com o comercial",
    "campanha regional não registrada no sistema",
]


def _u(chave: str) -> float:
    """Aleatório determinístico em [0,1): a mesma base gera a mesma demonstração."""
    h = hashlib.sha256(chave.encode("utf-8")).digest()
    return int.from_bytes(h[:6], "big") / float(1 << 48)


def _segundos(chave: str) -> float:
    """Lognormal em torno de 40 s, truncada em [8, 240]."""
    u1 = max(1e-9, _u("t1|" + chave))
    u2 = _u("t2|" + chave)
    z = math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
    return round(min(240.0, max(8.0, math.exp(math.log(40) + 0.55 * z))), 1)


def semear(rodada_id: int, fracao: float = 0.72) -> dict:
    """Decide parte da rodada como um time decidiria. Devolve o que fez."""
    from nucleo import servicos

    with sessao() as con:
        ja = linha(con, "SELECT COUNT(*) AS n FROM decisoes") or {}
        if (ja.get("n") or 0) > 0:
            return {"semeadas": 0, "motivo": "já existem decisões registradas"}
        pessoas = [(r["nome"], r["papel"]) for r in con.execute(
            "SELECT nome, papel FROM usuarios ORDER BY id")]
    if not pessoas:
        return {"semeadas": 0, "motivo": "nenhum usuário cadastrado"}

    analistas = [n for n, p in pessoas if p == "Analista"] or [pessoas[0][0]]
    gestoras = [n for n, p in pessoas if p != "Analista"] or [pessoas[0][0]]

    itens = [i for i in servicos.recomendacoes(rodada_id)
             if i["recomendacao"] in ("SUBIR", "BAIXAR", "REVISAR")]
    itens.sort(key=lambda i: -i["valor_em_jogo"])

    contagem = {"aprovada": 0, "editada": 0, "rejeitada": 0, "adiada": 0}
    for i in itens:
        chave = "%s|%s|%s" % (rodada_id, i["sku"], i["canal"])
        if _u("fazer|" + chave) > fracao:
            continue

        # A alçada vale aqui também: item retido para aprovação só a gestora
        # resolve, e item bloqueado ninguém aplica — no máximo adia. Se a
        # demonstração ignorasse isso, a tela mostraria um estado que a própria
        # plataforma recusa.
        retencao = i.get("retencao") or "livre"
        quem = (gestoras[int(_u("g|" + chave) * len(gestoras))] if retencao != "livre"
                else analistas[int(_u("a|" + chave) * len(analistas))])

        # A propensão a aprovar sobe com a confiança do dado e cai com o risco.
        p = 0.62 + 0.3 * (i.get("confianca_dados") or 0.5)
        p -= {"Alto": 0.3, "Médio": 0.12}.get(i.get("nivel_risco"), 0.0)
        if (i.get("cobertura") or 99) < 15:
            p -= 0.2
        if i["recomendacao"] == "REVISAR":
            p -= 0.25

        s, preco, motivo = _u("d|" + chave), None, ""
        if retencao == "bloqueado":
            # Bloqueado não vira preço: campanha em curso ou alteração recente
            # demais. O que o time faz é adiar ou dispensar.
            decisao = "adiada" if s < 0.75 else "rejeitada"
            if decisao == "rejeitada":
                motivo = i.get("retencao_motivo") or "bloqueado por regra de negócio"
        elif s < p:
            decisao = "aprovada"
        elif s < p + 0.13:
            decisao = "editada"
            # Meio do caminho entre o preço atual e o sugerido: o analista
            # concorda com a direção e dosa o tamanho.
            preco = round((i["preco_atual"] + i["preco_sugerido"]) / 2, 2)
        elif s < p + 0.26:
            decisao = "rejeitada"
            motivo = MOTIVOS_REJEICAO[int(_u("m|" + chave) * len(MOTIVOS_REJEICAO))]
        else:
            decisao = "adiada"

        # Estratégico exige justificativa escrita mesmo para aprovar.
        comentario = ("demonstração · item de imagem de preço conferido com o comercial"
                      if i.get("exige_justificativa") else "demonstração")
        try:
            servicos.registrar_decisao(i["id"], quem, decisao, preco, motivo,
                                       comentario, _segundos(chave))
            contagem[decisao] += 1
        except ValueError:
            continue

    total = sum(contagem.values())
    return {"semeadas": total, "detalhe": contagem,
            "nota": "decisões de demonstração, marcadas como tal no comentário"}


ESTRATEGICOS_DEMO = [
    ("PP-0001", "Ração de entrada: é por ela que o cliente compara a loja inteira"),
    ("PP-0007", "Areia higiênica é compra de reposição; preço alto espanta o cliente "
                "recorrente"),
]


def marcar_estrategicos_demo() -> int:
    """Marca dois SKUs como estratégicos para a R04 ter o que demonstrar.

    A escolha não é aleatória: os dois são itens de imagem de preço — o cliente
    usa o preço deles para julgar se a loja inteira é cara. É o caso clássico em
    que mexer no preço custa mais do que a margem do item.
    """
    from nucleo import servicos
    n = 0
    for sku, nota in ESTRATEGICOS_DEMO:
        try:
            servicos.marcar_estrategico(sku, True, "Carla Menezes", nota)
            n += 1
        except ValueError:
            continue
    return n
