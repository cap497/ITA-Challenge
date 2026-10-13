# -*- coding: utf-8 -*-
"""Séries históricas — doze meses de preço praticado, que a base tinha e a
plataforma não usava.

A aba `Vendas_12m` traz, por SKU e canal e por mês, o preço médio realizado. É o
histórico de preço de verdade: não o de tabela, o que saiu no caixa. Até aqui o
motor só o usava para calcular volume e tendência, e jogava fora a série.

Duas leituras diferentes saem do mesmo dado:

**Um item.** A curva do SKU no canal, com o preço sugerido marcado à frente.
Serve ao analista no momento da decisão: um item que subiu três meses seguidos e
agora recebe outra alta não é a mesma situação de um que está parado há um ano.

**O agregado.** A curva de cada categoria, em base 100 no primeiro mês. Rações
custa R$ 4 e Acessórios R$ 220 — no mesmo eixo em reais, uma vira uma linha reta
no chão. Indexadas, as duas mostram o que interessa: quem andou e quanto.

Indexar tem um custo que vale declarar: a base 100 esconde o nível de preço e
amplifica variação de categoria barata. Por isso o número em reais continua na
tabela ao lado do gráfico.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

from nucleo.banco import PASTA_DADOS, linhas, sessao

PLANILHA = os.path.join(PASTA_DADOS, "base_popular_pet.xlsx")


def _vendas() -> pd.DataFrame:
    v = pd.read_excel(PLANILHA, sheet_name="Vendas_12m", header=2).dropna(how="all")
    v["Mês"] = v["Mês"].astype(str)
    return v


def _cadastro() -> pd.DataFrame:
    with sessao() as con:
        return pd.read_sql_query(
            "SELECT sku, nome AS produto, categoria, marca, curva FROM produtos", con)


def meses() -> list[str]:
    return sorted(_vendas()["Mês"].unique())


def _rotulo(m: str) -> str:
    """'2026-01' → 'jan/26'. O eixo precisa caber doze rótulos."""
    nomes = ["jan", "fev", "mar", "abr", "mai", "jun",
             "jul", "ago", "set", "out", "nov", "dez"]
    try:
        ano, mes = m.split("-")
        return "%s/%s" % (nomes[int(mes) - 1], ano[2:])
    except (ValueError, IndexError):
        return m


def serie_item(sku: str, canal: str | None = None) -> dict:
    """Doze meses do item: preço praticado, volume e desconto."""
    v = _vendas()
    d = v[v["SKU"] == sku]
    if canal:
        d = d[d["Canal"] == canal]
    if d.empty:
        return {"sku": sku, "canal": canal, "pontos": [], "canais": []}

    def _por(df):
        g = df.groupby("Mês").agg(
            quantidade=("Quantidade", "sum"), receita=("Receita", "sum"),
            desconto=("Desconto médio", "mean")).reset_index().sort_values("Mês")
        # Preço médio ponderado pela quantidade, não média das médias: mês de
        # volume alto tem de pesar mais no preço que o cliente de fato pagou.
        g["preco"] = np.where(g["quantidade"] > 0, g["receita"] / g["quantidade"], np.nan)
        return [{"mes": r["Mês"], "rotulo": _rotulo(r["Mês"]),
                 "preco": round(float(r["preco"]), 2) if pd.notna(r["preco"]) else None,
                 "volume": int(r["quantidade"]),
                 "desconto": round(float(r["desconto"] or 0), 4)}
                for _, r in g.iterrows()]

    pontos = _por(d)
    por_canal = [{"canal": c, "pontos": _por(g)} for c, g in v[v["SKU"] == sku].groupby("Canal")]

    precos = [p["preco"] for p in pontos if p["preco"]]
    return {
        "sku": sku, "canal": canal, "pontos": pontos, "canais": por_canal,
        "primeiro": precos[0] if precos else None,
        "ultimo": precos[-1] if precos else None,
        "variacao_12m": (round(precos[-1] / precos[0] - 1, 4)
                         if len(precos) > 1 and precos[0] else None),
        "minimo": min(precos) if precos else None,
        "maximo": max(precos) if precos else None,
    }


def serie_categorias(indexado: bool = True) -> dict:
    """Uma curva por categoria, em base 100 no primeiro mês.

    O agregado por categoria é média ponderada por receita: sem isso, um SKU de
    giro baixo move a curva da categoria tanto quanto o carro-chefe.
    """
    v = _vendas().merge(_cadastro(), left_on="SKU", right_on="sku", how="left")
    g = v.groupby(["categoria", "Mês"]).agg(
        receita=("Receita", "sum"), quantidade=("Quantidade", "sum")).reset_index()
    g["preco"] = np.where(g["quantidade"] > 0, g["receita"] / g["quantidade"], np.nan)

    # Índice de cada SKU contra o próprio primeiro mês. É daqui que sai a faixa
    # de dispersão: a média da categoria pode estar parada enquanto metade dos
    # itens sobe 5% e a outra metade cai 5%. Mostrar só a média esconderia
    # exatamente o movimento que o analista precisa ver.
    s = v.groupby(["categoria", "SKU", "Mês"]).agg(
        receita=("Receita", "sum"), quantidade=("Quantidade", "sum")).reset_index()
    s["preco"] = np.where(s["quantidade"] > 0, s["receita"] / s["quantidade"], np.nan)
    s = s.sort_values("Mês")
    primeiro = s.groupby(["categoria", "SKU"])["preco"].transform("first")
    s["indice_sku"] = 100 * s["preco"] / primeiro

    saida = []
    for cat, d in g.groupby("categoria"):
        d = d.sort_values("Mês")
        precos = d["preco"].to_numpy(float)
        if not len(precos) or not np.isfinite(precos[0]) or precos[0] == 0:
            continue
        base = precos[0]
        faixa = s[s["categoria"] == cat].groupby("Mês")["indice_sku"].quantile(
            [0.1, 0.5, 0.9]).unstack()
        pontos = []
        for m, p in zip(d["Mês"], precos):
            q = faixa.loc[m] if m in faixa.index else None
            pontos.append({
                "mes": m, "rotulo": _rotulo(m),
                "preco": round(float(p), 2),
                "indice": round(100 * float(p) / base, 1),
                "p10": round(float(q[0.1]), 1) if q is not None else None,
                "p50": round(float(q[0.5]), 1) if q is not None else None,
                "p90": round(float(q[0.9]), 1) if q is not None else None,
            })
        ult = pontos[-1]
        saida.append({
            "categoria": cat, "pontos": pontos,
            "preco_inicial": round(float(base), 2),
            "preco_final": round(float(precos[-1]), 2),
            "variacao": round(float(precos[-1] / base - 1), 4),
            # Amplitude interna: distância entre o SKU que mais subiu e o que
            # mais caiu dentro da categoria, no último mês.
            "amplitude": (round((ult["p90"] - ult["p10"]) / 100, 4)
                          if ult["p90"] is not None else None),
            "receita_12m": round(float(d["receita"].sum()), 2),
            "skus": int(s[s["categoria"] == cat]["SKU"].nunique()),
        })
    saida.sort(key=lambda x: -x["variacao"])
    return {"indexado": indexado, "meses": [_rotulo(m) for m in sorted(v["Mês"].unique())],
            "categorias": saida,
            "nota": "Base 100 no primeiro mês. Indexar deixa Rações (R$ 4) e Acessórios "
                    "(R$ 220) comparáveis no mesmo eixo, ao custo de esconder o nível de "
                    "preço — que fica na tabela ao lado. A faixa sombreada é do 10º ao 90º "
                    "percentil dos SKUs da categoria: é onde se vê que uma categoria parada "
                    "na média pode ter itens andando bastante."}


def serie_geral() -> dict:
    """O total da operação mês a mês: receita, volume e desconto praticado."""
    v = _vendas()
    g = v.groupby("Mês").agg(
        receita=("Receita", "sum"), quantidade=("Quantidade", "sum"),
        desconto=("Desconto médio", "mean")).reset_index().sort_values("Mês")
    g["preco"] = np.where(g["quantidade"] > 0, g["receita"] / g["quantidade"], np.nan)
    return {"pontos": [{"mes": r["Mês"], "rotulo": _rotulo(r["Mês"]),
                        "receita": round(float(r["receita"]), 2),
                        "volume": int(r["quantidade"]),
                        "preco_medio": round(float(r["preco"]), 2),
                        "desconto": round(float(r["desconto"] or 0), 4)}
                       for _, r in g.iterrows()]}


def alteracoes_por_mes() -> list[dict]:
    """Quantas vezes o preço mudou, por mês, somando ERP e plataforma.

    É a linha de base "alterações de preço/mês" vista no tempo, e serve para a
    conversa que o dado sozinho não resolve: mais decisões não quer dizer mais
    alterações, e o gráfico deixa isso visível em vez de deixar implícito.
    """
    with sessao() as con:
        hist = linhas(con, """
            SELECT substr(data, 1, 7) AS mes, COUNT(*) AS n
            FROM historico_preco WHERE tipo IN ('AUMENTO', 'REDUCAO')
            GROUP BY mes ORDER BY mes""")
        dec = linhas(con, """
            SELECT substr(criada_em, 1, 7) AS mes, COUNT(*) AS n
            FROM decisoes WHERE decisao IN ('aprovada', 'editada')
            GROUP BY mes ORDER BY mes""")
    por_mes: dict[str, dict] = {}
    for r in hist:
        por_mes.setdefault(r["mes"], {"mes": r["mes"], "erp": 0, "plataforma": 0})["erp"] = r["n"]
    for r in dec:
        por_mes.setdefault(r["mes"], {"mes": r["mes"], "erp": 0,
                                      "plataforma": 0})["plataforma"] = r["n"]
    out = sorted(por_mes.values(), key=lambda x: x["mes"])
    for o in out:
        o["rotulo"] = _rotulo(o["mes"])
        o["total"] = o["erp"] + o["plataforma"]
    return out


def panorama() -> dict:
    """Tudo que a tela de Histórico mostra no modo agregado."""
    cat = serie_categorias()
    ger = serie_geral()
    alt = alteracoes_por_mes()
    pontos = ger["pontos"]
    return {
        "categorias": cat, "geral": ger, "alteracoes": alt,
        "resumo": {
            "meses": len(pontos),
            "receita_12m": round(sum(p["receita"] for p in pontos), 2),
            "volume_12m": sum(p["volume"] for p in pontos),
            "preco_medio_inicial": pontos[0]["preco_medio"] if pontos else None,
            "preco_medio_final": pontos[-1]["preco_medio"] if pontos else None,
            "desconto_medio": (round(sum(p["desconto"] for p in pontos) / len(pontos), 4)
                               if pontos else None),
            "categoria_que_mais_subiu": cat["categorias"][0] if cat["categorias"] else None,
            "categoria_que_mais_caiu": cat["categorias"][-1] if cat["categorias"] else None,
        },
    }


def serie_pares(sku: str, canal: str | None = None, limite: int = 12) -> dict:
    """Doze meses dos itens da mesma categoria, para situar um SKU entre os pares.

    Em base 100 no primeiro mês: dentro de Higiene o preço vai de R$ 65 a R$ 112,
    e no eixo em reais as curvas baratas viram uma linha no chão. Indexadas, a
    pergunta que o gráfico responde é a certa — *quem andou, e para onde* — e o
    nível em reais continua no cartão do item.

    O SKU pedido vem marcado `destaque`, para a tela desenhá-lo grosso e os
    pares finos: comparar exige que o item em questão seja achável de relance.
    """
    cad = _cadastro()
    eu = cad[cad["sku"] == sku]
    if eu.empty:
        return {"categoria": None, "itens": []}
    categoria = eu.iloc[0]["categoria"]
    nome = eu.iloc[0]["produto"]

    v = _vendas()
    if canal:
        v = v[v["Canal"] == canal]
    skus = cad[cad["categoria"] == categoria]["sku"].tolist()
    v = v[v["SKU"].isin(skus)]

    g = v.groupby(["SKU", "Mês"]).agg(
        receita=("Receita", "sum"), quantidade=("Quantidade", "sum")).reset_index()
    g["preco"] = np.where(g["quantidade"] > 0, g["receita"] / g["quantidade"], np.nan)

    itens = []
    for s, d in g.groupby("SKU"):
        d = d.sort_values("Mês")
        precos = d["preco"].to_numpy(float)
        if not len(precos) or not np.isfinite(precos[0]) or precos[0] == 0:
            continue
        linha_cad = cad[cad["sku"] == s].iloc[0]
        itens.append({
            "sku": s, "produto": linha_cad["produto"], "marca": linha_cad["marca"],
            "destaque": s == sku,
            # Mesmo nome de produto em outra marca é o par de verdade; o resto da
            # categoria é só pano de fundo.
            "par": bool(linha_cad["produto"] == nome and s != sku),
            "pontos": [{"mes": m, "rotulo": _rotulo(m), "preco": round(float(p), 2),
                        "indice": round(100 * float(p) / precos[0], 1)}
                       for m, p in zip(d["Mês"], precos)],
            "variacao": round(float(precos[-1] / precos[0] - 1), 4),
            "preco_final": round(float(precos[-1]), 2),
        })
    itens.sort(key=lambda x: (not x["destaque"], not x["par"], -abs(x["variacao"])))
    return {"categoria": categoria, "produto": nome, "canal": canal,
            "itens": itens[:limite], "total": len(itens)}


def serie_volume_categoria(sku: str, canal: str | None = None) -> dict:
    """Volume do item contra o volume da categoria, ambos em base 100.

    Serve para separar o que é do item do que é da categoria inteira: uma queda
    de volume logo depois de uma alta de preço quer dizer uma coisa se a
    categoria toda caiu junto, e outra bem diferente se só este item caiu.
    """
    cad = _cadastro()
    eu = cad[cad["sku"] == sku]
    if eu.empty:
        return {"pontos": []}
    categoria = eu.iloc[0]["categoria"]
    skus = set(cad[cad["categoria"] == categoria]["sku"])

    v = _vendas()
    if canal:
        v = v[v["Canal"] == canal]
    item = v[v["SKU"] == sku].groupby("Mês")["Quantidade"].sum().sort_index()
    cat = v[v["SKU"].isin(skus)].groupby("Mês")["Quantidade"].sum().sort_index()
    if item.empty or cat.empty:
        return {"pontos": []}

    b_i = float(item.iloc[0]) or 1.0
    b_c = float(cat.iloc[0]) or 1.0
    return {
        "categoria": categoria,
        "pontos": [{"mes": m, "rotulo": _rotulo(m),
                    "item": int(item.get(m, 0)),
                    "indice_item": round(100 * float(item.get(m, 0)) / b_i, 1),
                    "indice_categoria": round(100 * float(cat.get(m, 0)) / b_c, 1)}
                   for m in item.index],
        "variacao_item": round(float(item.iloc[-1] / b_i - 1), 4),
        "variacao_categoria": round(float(cat.iloc[-1] / b_c - 1), 4),
    }
