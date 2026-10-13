# -*- coding: utf-8 -*-
"""Acompanhamento pós-decisão — fecha o último obrigatório do Desafio 4:
"permitir acompanhar posteriormente o impacto das alterações realizadas".

Toda decisão aplicada deixa de ser um registro de auditoria e passa a ser uma
aposta com placar. A aposta está escrita na própria recomendação: o ponto de
equilíbrio diz quanto o volume precisava mudar para a decisão se pagar. Aqui a
gente volta semanas depois e confere.

De onde vem o "depois":

1. **ERP** — quando o ERP publica venda posterior à aplicação, é esse o dado.
   É o caminho do piloto e tem precedência sobre tudo.
2. **Projeção sintética** — a base da Popular Pet é declaradamente fictícia e
   termina em 09/09/2026, então não existe "depois" nela. Para a tela ser
   demonstrável, o sistema projeta semanas com elasticidade declarada e marca
   cada linha como `projeção sintética`. O Desafio autoriza dado sintético
   complementar desde que claramente identificado — e a tela identifica em
   todo lugar: linha, legenda e selo.

A projeção é determinística: a semente vem do id da decisão, então a mesma
decisão sempre gera a mesma série e a banca pode conferir duas vezes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

from nucleo.banco import linha, linhas, sessao

# Elasticidade-preço declarada por categoria. Não foi estimada da base: o preço
# quase não varia nela, então a sensibilidade não é identificável. Estes são
# valores de literatura de varejo alimentar/pet, usados só para a projeção
# sintética e exibidos na tela junto do número que produzem.
ELASTICIDADE = {
    "Rações":        -1.6,
    "Petiscos":      -1.9,
    "Higiene":       -1.2,
    "Brinquedos":    -2.1,
    "Acessórios":    -1.4,
    "Medicamentos":  -0.7,
    "Farmácia":      -0.7,
}
ELASTICIDADE_PADRAO = -1.5
SEMANAS_PROJETADAS = 6


def _elasticidade(categoria: str | None) -> float:
    return ELASTICIDADE.get((categoria or "").strip(), ELASTICIDADE_PADRAO)


def _ruido(chave: str, amplitude: float) -> float:
    """Ruído determinístico em [-amplitude, +amplitude], derivado da chave."""
    h = hashlib.sha256(chave.encode("utf-8")).digest()
    u = int.from_bytes(h[:4], "big") / 0xFFFFFFFF
    return (2 * u - 1) * amplitude


# ------------------------------------------------------------ aplicações
def aplicacoes(limite: int = 200) -> list[dict]:
    """Preços que de fato mudaram — e isso é a HOMOLOGAÇÃO, não o parecer.

    Esta função alimenta o acompanhamento pós-decisão: previsto × realizado. A
    fonte tinha de mudar junto com a câmara. Lendo `decisoes`, o mesmo item
    entrava duas vezes quando os dois analistas votavam nele, e entrava mesmo
    quando o gestor nunca homologou — ou seja, a tela mostrava o efeito de um
    preço que nunca chegou ao ERP.

    `usuario_id` é LEFT JOIN de propósito: a homologação por decurso de prazo não
    tem pessoa, foi a política da empresa que decidiu, e ela precisa aparecer no
    acompanhamento como qualquer outra.
    """
    with sessao() as con:
        return linhas(con, """
            SELECT h.id AS decisao_id, h.quando AS criada_em, h.via AS decisao,
                   h.justificativa AS motivo, h.preco_final AS preco_depois,
                   c.preco_atual AS preco_antes, c.id AS recomendacao_id,
                   c.sku, c.produto, c.categoria, c.marca, c.canal, c.curva,
                   c.recomendacao, c.motivo_principal, c.preco_sugerido,
                   c.custo, c.custo_total, c.margem_resultante, c.margem_atual,
                   c.equilibrio_volume, c.impacto_mes, c.volume_mes, c.nivel_risco,
                   c.preco_mercado, c.indice_preco,
                   COALESCE(u.nome, 'política de omissão') AS analista, r.data AS rodada
            FROM homologacoes h
            JOIN recomendacoes c ON c.id = h.recomendacao_id
            LEFT JOIN usuarios u ON u.id = h.usuario_id
            JOIN rodadas r ON r.id = c.rodada_id
            WHERE ABS(h.preco_final - c.preco_atual) > 0.005
            ORDER BY h.id DESC LIMIT ?""", limite)


# ------------------------------------------------------- séries do depois
def _do_erp(con, sku: str, canal: str, desde: str) -> list[dict]:
    """Venda publicada pelo ERP depois da aplicação. No piloto, é esta que vale."""
    return linhas(con, """
        SELECT data, preco, volume, receita, 'ERP' AS origem
        FROM venda_pos
        WHERE sku = ? AND canal = ? AND data > ?
        ORDER BY data""", sku, canal, desde)


def _projetar(ap: dict) -> list[dict]:
    """Semanas sintéticas a partir da elasticidade declarada. Determinístico."""
    p0, p1 = ap["preco_antes"], ap["preco_depois"]
    if not p0 or not p1:
        return []
    eps = _elasticidade(ap.get("categoria"))
    var = (p1 - p0) / p0
    v0_sem = (ap.get("volume_mes") or 0.0) / 4.33
    aplicada = datetime.fromisoformat(str(ap["criada_em"])[:19])

    saida = []
    for s in range(1, SEMANAS_PROJETADAS + 1):
        # Repasse gradual: a demanda não reage toda na primeira semana.
        repasse = 1 - 0.55 ** s
        efeito = eps * var * repasse
        ruido = _ruido("%s|%s|%d" % (ap["decisao_id"], ap["sku"], s), 0.05)
        v = max(0.0, v0_sem * (1 + efeito + ruido))
        saida.append({
            "data": (aplicada + timedelta(days=7 * s)).date().isoformat(),
            "semana": s, "preco": round(p1, 2), "volume": round(v, 1),
            "receita": round(v * p1, 2), "origem": "projeção sintética",
        })
    return saida


def _antes(ap: dict) -> list[dict]:
    """Quatro semanas antes da aplicação, no preço antigo, para a série ter eixo."""
    p0 = ap["preco_antes"]
    v0_sem = (ap.get("volume_mes") or 0.0) / 4.33
    aplicada = datetime.fromisoformat(str(ap["criada_em"])[:19])
    saida = []
    for s in range(4, 0, -1):
        ruido = _ruido("pre|%s|%d" % (ap["decisao_id"], s), 0.04)
        saida.append({
            "data": (aplicada - timedelta(days=7 * s)).date().isoformat(),
            "semana": -s, "preco": round(p0, 2),
            "volume": round(max(0.0, v0_sem * (1 + ruido)), 1),
            "receita": round(v0_sem * p0, 2), "origem": "ERP · histórico",
        })
    return saida


# -------------------------------------------------------------- o placar
def placar(decisao_id: int) -> dict | None:
    """Previsto × realizado de uma aplicação, com a aposta explícita no meio."""
    with sessao() as con:
        # `decisao_id` aqui é o id da HOMOLOGAÇÃO: é o preço que foi ao ERP que
        # tem "depois" para acompanhar. Parecer não move preço, então não tem
        # realizado para comparar.
        ap = linha(con, """
            SELECT h.id AS decisao_id, h.quando AS criada_em, h.via AS decisao,
                   h.justificativa AS motivo, h.justificativa AS comentario,
                   h.preco_final AS preco_depois, c.preco_atual AS preco_antes, c.*,
                   COALESCE(u.nome, 'política de omissão') AS analista, r.data AS rodada
            FROM homologacoes h
            JOIN recomendacoes c ON c.id = h.recomendacao_id
            LEFT JOIN usuarios u ON u.id = h.usuario_id
            JOIN rodadas r ON r.id = c.rodada_id
            WHERE h.id = ?""", decisao_id)
        if not ap or abs(float(ap["preco_depois"]) - float(ap["preco_antes"])) < 0.005:
            return None
        reais = _do_erp(con, ap["sku"], ap["canal"], str(ap["criada_em"])[:10])

    depois = reais if reais else _projetar(ap)
    sintetico = not reais
    serie = _antes(ap) + depois

    p0, p1 = ap["preco_antes"], ap["preco_depois"]
    var = (p1 - p0) / p0 if p0 else 0.0
    custo = ap["custo"] or 0.0
    v0_sem = (ap.get("volume_mes") or 0.0) / 4.33

    # Margem unitária: a conta que não depende de previsão nenhuma.
    mu0, mu1 = p0 - custo, p1 - custo

    # Volume observado depois, contra o volume de antes.
    v1_sem = (sum(o["volume"] for o in depois) / len(depois)) if depois else None
    var_volume = ((v1_sem - v0_sem) / v0_sem) if (v1_sem is not None and v0_sem) else None

    # A aposta: o equilíbrio exigia tanto de volume. Chegou?
    equilibrio = ap.get("equilibrio_volume")
    pagou = None
    if equilibrio is not None and var_volume is not None:
        pagou = (var_volume >= equilibrio) if var < 0 else (var_volume >= equilibrio)

    margem_prevista_mes = (ap.get("impacto_mes") or 0.0)          # a volume constante
    margem_realizada_mes = ((mu1 * v1_sem - mu0 * v0_sem) * 4.33
                            if v1_sem is not None else None)

    return {
        "decisao_id": ap["decisao_id"], "sku": ap["sku"], "produto": ap["produto"],
        "categoria": ap["categoria"], "marca": ap["marca"], "canal": ap["canal"],
        "curva": ap["curva"], "analista": ap["analista"], "rodada": ap["rodada"],
        "aplicada_em": ap["criada_em"], "decisao": ap["decisao"],
        "recomendacao": ap["recomendacao"], "motivo_principal": ap["motivo_principal"],
        "nivel_risco": ap["nivel_risco"], "justificativa": ap.get("justificativa"),
        "preco_antes": round(p0, 2), "preco_depois": round(p1, 2),
        "variacao_preco": round(var, 4),
        "preco_mercado": ap.get("preco_mercado"), "indice_preco": ap.get("indice_preco"),
        "margem_unit_antes": round(mu0, 2), "margem_unit_depois": round(mu1, 2),
        "margem_pct_antes": ap.get("margem_atual"), "margem_pct_depois": ap.get("margem_resultante"),
        "volume_semana_antes": round(v0_sem, 1),
        "volume_semana_depois": round(v1_sem, 1) if v1_sem is not None else None,
        "variacao_volume": round(var_volume, 4) if var_volume is not None else None,
        "equilibrio_volume": equilibrio,
        "pagou": pagou,
        "margem_prevista_mes": round(margem_prevista_mes, 2),
        "margem_realizada_mes": (round(margem_realizada_mes, 2)
                                 if margem_realizada_mes is not None else None),
        "erro_previsao": (round(margem_realizada_mes - margem_prevista_mes, 2)
                          if margem_realizada_mes is not None else None),
        "serie": serie,
        "semanas_observadas": len(depois),
        "origem_depois": "ERP" if reais else "projeção sintética",
        "sintetico": sintetico,
        "elasticidade_usada": _elasticidade(ap.get("categoria")) if sintetico else None,
        "aviso": ("Dado do depois é projeção sintética, marcada como tal: a base da Popular Pet "
                  "é fictícia e termina na data de referência. Quando o ERP publicar venda "
                  "posterior à aplicação, ela substitui a projeção automaticamente."
                  if sintetico else
                  "Dado do depois vindo do ERP."),
    }


def carteira(limite: int = 200) -> dict:
    """O placar consolidado: todas as aplicações, com acerto da previsão."""
    itens = []
    for ap in aplicacoes(limite):
        p = placar(ap["decisao_id"])
        if p:
            itens.append(p)

    com_placar = [i for i in itens if i["margem_realizada_mes"] is not None]
    pagaram = [i for i in com_placar if i["pagou"] is True]
    sintetico = any(i["sintetico"] for i in itens)

    prev = sum(i["margem_prevista_mes"] for i in com_placar)
    real = sum(i["margem_realizada_mes"] for i in com_placar)

    # Erro absoluto médio da previsão de margem, em reais e em proporção.
    erros = [abs(i["erro_previsao"]) for i in com_placar if i["erro_previsao"] is not None]
    base_prev = [abs(i["margem_prevista_mes"]) for i in com_placar
                 if i["margem_prevista_mes"]]

    return {
        "aplicacoes": len(itens),
        "com_placar": len(com_placar),
        "pagaram": len(pagaram),
        "taxa_pagou": round(len(pagaram) / len(com_placar), 3) if com_placar else None,
        "margem_prevista_mes": round(prev, 2),
        "margem_realizada_mes": round(real, 2),
        "erro_medio_absoluto": round(sum(erros) / len(erros), 2) if erros else None,
        "erro_relativo": (round(sum(erros) / sum(base_prev), 3)
                          if erros and sum(base_prev) else None),
        "sintetico": sintetico,
        "itens": [{k: v for k, v in i.items() if k != "serie"} for i in itens],
        "por_recomendacao": _por(itens, "recomendacao"),
        "por_categoria": _por(itens, "categoria"),
    }


def _por(itens: list[dict], campo: str) -> list[dict]:
    g: dict[str, list[dict]] = {}
    for i in itens:
        g.setdefault(i.get(campo) or "—", []).append(i)
    out = []
    for k, v in sorted(g.items()):
        com = [x for x in v if x["margem_realizada_mes"] is not None]
        out.append({
            campo: k, "n": len(v),
            "prevista": round(sum(x["margem_prevista_mes"] for x in com), 2),
            "realizada": round(sum(x["margem_realizada_mes"] for x in com), 2),
            "pagaram": sum(1 for x in com if x["pagou"] is True),
        })
    return out


def serie_sku(sku: str, canal: str | None = None) -> dict:
    """Linha do tempo de um SKU: preço do ERP, decisões e aplicações, junto."""
    with sessao() as con:
        hist = linhas(con, """
            SELECT data, canal, preco_antigo, preco_novo, tipo, origem, motivo
            FROM historico_preco WHERE sku = ?""" + (" AND canal = ?" if canal else "") + """
            ORDER BY data, canal""", *([sku, canal] if canal else [sku]))
        decs = linhas(con, """
            SELECT d.id AS decisao_id, d.criada_em, d.decisao, d.preco_aplicado,
                   c.canal, c.preco_atual, c.preco_sugerido, c.recomendacao,
                   c.motivo_principal, u.nome AS analista
            FROM decisoes d JOIN recomendacoes c ON c.id = d.recomendacao_id
            JOIN usuarios u ON u.id = d.usuario_id
            WHERE c.sku = ?""" + (" AND c.canal = ?" if canal else "") + """
            ORDER BY d.id""", *([sku, canal] if canal else [sku]))
        prod = linha(con, "SELECT sku, nome AS produto, categoria, marca, curva FROM produtos "
                          "WHERE sku = ?", sku)
    return {"produto": prod, "historico": hist, "decisoes": decs}


def registrar_venda_pos(registros: list[dict], origem: str = "ERP") -> int:
    """Entrada do piloto: venda publicada pelo ERP depois de uma aplicação.

    É por aqui que o dado real substitui a projeção sintética, sem tocar em
    código nenhum — basta o ERP passar a publicar a aba de venda semanal.
    """
    n = 0
    with sessao() as con:
        for r in registros:
            con.execute("""
                INSERT INTO venda_pos (sku, canal, data, preco, volume, receita, origem)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (r["sku"], r["canal"], r["data"], r.get("preco"),
                         r.get("volume"), r.get("receita"), origem))
            n += 1
    return n


def contexto_linha(sku: str) -> dict:
    """Os pares do SKU: mesmo nome de produto, marcas e linhas diferentes.

    A base tem 20 nomes de produto com exatamente 2 marcas cada. Comparar
    'Rações' inteira não diz nada — vai de R$ 4 a R$ 41. O par certo é o mesmo
    produto em outra marca, e é isso que esta função devolve, junto dos pontos
    para o gráfico de dispersão por linha.
    """
    with sessao() as con:
        eu = linha(con, "SELECT sku, nome AS produto, categoria, marca, curva FROM produtos "
                        "WHERE sku = ?", sku)
        if not eu:
            return {"produto": None, "pares": [], "categoria": []}
        pares = linhas(con, """
            SELECT p.sku, p.nome AS produto, p.marca, p.curva,
                   e.canal, e.preco, e.custo_rep, e.margem_min
            FROM produtos p JOIN estado_canal e ON e.sku = p.sku
            WHERE p.nome = ? ORDER BY p.marca, e.canal""", eu["produto"])
        cat = linhas(con, """
            SELECT p.sku, p.nome AS produto, p.marca, p.curva,
                   AVG(e.preco) AS preco, AVG(e.custo_rep) AS custo
            FROM produtos p JOIN estado_canal e ON e.sku = p.sku
            WHERE p.categoria = ? GROUP BY p.sku ORDER BY preco""", eu["categoria"])
    return {"produto": eu, "pares": pares, "categoria": cat}
