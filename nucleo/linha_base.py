# -*- coding: utf-8 -*-
"""Linha de base — os indicadores que a própria Popular Pet declarou.

A aba `Indicadores_Atuais` da base entrega, de graça, o que o Manual pede na
Semana 2 e a banca cobra no critério 5: valor atual, meta, frequência e
responsável. Não é número que o grupo inventou — é número que o cliente
escreveu.

Cada indicador ganha aqui um `medidor`: a função que calcula o mesmo indicador
a partir do que a plataforma de fato produziu. Daí sai a coluna "agora" e a
comparação antes → agora → meta.

Indicador sem medidor (SKUs monitorados, por exemplo) fica declarado como fora
do alcance do protótipo em vez de receber número fabricado.
"""

from __future__ import annotations

import os
import re

import pandas as pd

from nucleo.banco import PASTA_DADOS, linha, linhas, sessao

PLANILHA = os.path.join(PASTA_DADOS, "base_popular_pet.xlsx")

# Chave curta ← nome exato do indicador na planilha.
CHAVES = {
    "Margem bruta média da amostra": "margem",
    "SKUs dentro da faixa competitiva": "faixa",
    "Tempo semanal de revisão manual": "horas",
    "Alterações de preço/mês": "alteracoes",
    "SKUs monitorados em concorrentes": "monitorados",
    "Cobertura de matching confiável": "matching",
    "Taxa de reversão de preços": "reversao",
    "Recomendações aceitas pelo time": "aceite",
}

# Como ler o indicador: unidade e sentido (maior é melhor ou pior).
FORMATO = {
    "margem":      ("%",     "cima"),
    "faixa":       ("%",     "cima"),
    "horas":       ("h/sem", "baixo"),
    "alteracoes":  ("/mês",  "neutro"),
    "monitorados": ("SKUs",  "cima"),
    "matching":    ("%",     "cima"),
    "reversao":    ("%",     "baixo"),
    "aceite":      ("%",     "cima"),
}


def _numero(texto: str) -> float | None:
    """'31,8%' → 31.8 · '1.850' → 1850 · '18 horas' → 18 · 'Não medido' → None."""
    if texto is None:
        return None
    t = str(texto).strip()
    if not t or t.lower().startswith("não medido"):
        return None
    t = re.sub(r"[^\d,.\-]", "", t)
    if not t:
        return None
    if "," in t:                       # 31,8 → 31.8 (ponto é separador de milhar)
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") == 1 and len(t.split(".")[1]) == 3:
        t = t.replace(".", "")         # 1.850 → 1850
    try:
        return float(t)
    except ValueError:
        return None


def declarada() -> list[dict]:
    """Lê a aba Indicadores_Atuais exatamente como a Popular Pet entregou."""
    df = pd.read_excel(PLANILHA, sheet_name="Indicadores_Atuais", header=2).dropna(how="all")
    out = []
    for r in df.to_dict("records"):
        nome = str(r.get("Indicador", "")).strip()
        chave = CHAVES.get(nome)
        if not chave:
            continue
        atual_txt = str(r.get("Valor fictício atual", "")).strip()
        meta_txt = str(r.get("Meta fictícia", "")).strip()
        unidade, sentido = FORMATO[chave]
        out.append({
            "chave": chave, "indicador": nome,
            "base_texto": atual_txt, "base": _numero(atual_txt),
            "meta_texto": meta_txt, "meta": _numero(meta_txt),
            "frequencia": str(r.get("Frequência", "")).strip(),
            "responsavel": str(r.get("Responsável", "")).strip(),
            "unidade": unidade, "sentido": sentido,
        })
    return out


# ------------------------------------------------------------------ medidores
def _medir(rodada_id: int | None) -> dict:
    """Calcula na plataforma o mesmo que a planilha declara.

    Cada valor volta como (número, nota) ou (None, motivo de não medir).
    """
    from nucleo import servicos

    m: dict[str, tuple] = {}

    if rodada_id:
        res = servicos.resumo_rodada(rodada_id)
        met = servicos.metricas(rodada_id)
        m["margem"] = (round(100 * res["margem_media"], 1),
                       "margem com o preço sugerido, nos %d itens da rodada" % res["itens"])
        m["faixa"] = (round(100 * res["na_faixa"], 1),
                      "índice de preço entre 0,95 e 1,05 do mercado")
        m["aceite"] = ((round(100 * met["taxa_aceite"], 1), "em %d decisões registradas"
                        % met["decididos"]) if met["taxa_aceite"] is not None
                       else (None, "nenhuma decisão registrada ainda"))
    else:
        for k in ("margem", "faixa", "aceite"):
            m[k] = (None, "nenhuma rodada gerada")

    with sessao() as con:
        # Tempo de análise: medido de verdade, item por item, pelo cronômetro da tela.
        # Mediana, não média: um cronômetro esquecido aberto arrastaria a média
        # e inflaria o ganho. A mediana ignora o caso isolado.
        vals = sorted(r["s"] for r in linhas(con, """
            SELECT segundos_analise AS s FROM decisoes
            WHERE segundos_analise IS NOT NULL
              AND segundos_analise BETWEEN 2 AND 1800"""))
        n_t = len(vals)
        media = (vals[n_t // 2] if n_t % 2 else (vals[n_t // 2 - 1] + vals[n_t // 2]) / 2) \
            if n_t else None
        if n_t >= 5 and media:
            # O ganho não vem de decidir mais rápido item a item: vem de o analista
            # não tocar nos itens que não precisam de decisão. O motor tria o
            # catálogo inteiro; a pessoa decide só os que a rodada selecionou.
            # Por isso a conta é capacidade da rodada × rodadas por semana × tempo
            # medido, e não catálogo inteiro × tempo medido.
            from nucleo import motor as _motor
            capacidade = _motor.Config().capacidade_rodada
            rodadas_semana = 5
            horas = capacidade * rodadas_semana * media / 3600
            m["horas"] = (round(horas, 1),
                          "%.0f s por decisão (mediana de %d decisões cronometradas) × %d "
                          "itens por rodada × %d rodadas por semana. O catálogo inteiro "
                          "continua sendo triado — só não passa por pessoa."
                          % (media, n_t, capacidade, rodadas_semana))
        else:
            m["horas"] = (None, "precisa de 5 decisões cronometradas (tem %d)" % n_t)

        # Alterações por mês: preço que de fato mudou, e isso é a HOMOLOGAÇÃO, não
        # o parecer. Contando `decisoes` eu contava duas vezes o mesmo item quando
        # os dois analistas votavam nele — a métrica inflava com a discussão, não
        # com a mudança de preço.
        alt = linha(con, """
            SELECT COUNT(*) AS n FROM homologacoes h
            JOIN recomendacoes c ON c.id = h.recomendacao_id
            WHERE h.quando >= date('now', '-30 day')
              AND ABS(h.preco_final - c.preco_atual) > 0.005
        """) or {}
        m["alteracoes"] = (alt.get("n") or 0,
                           "preços homologados e diferentes do atual nos últimos 30 dias")

        # Matching confiável: peso dos concorrentes aproveitados sobre os coletados.
        if rodada_id:
            q = linha(con, """
                SELECT SUM(n_validos) AS v, SUM(n_total) AS t FROM recomendacoes
                WHERE rodada_id = ? AND n_total > 0""", rodada_id) or {}
            if q.get("t"):
                m["matching"] = (round(100 * q["v"] / q["t"], 1),
                                 "%d de %d coletas aproveitadas após descarte de outlier e "
                                 "coleta velha" % (q["v"], q["t"]))
            else:
                m["matching"] = (None, "sem coleta de concorrência na rodada")
        else:
            m["matching"] = (None, "nenhuma rodada gerada")

        # Reversão: preço aplicado e desfeito em sentido contrário dentro de 30 dias.
        # Reversão também se mede na homologação: um parecer não reverte nada.
        rev = linha(con, """
            WITH apl AS (
              SELECT h.id, c.sku, c.canal, h.quando AS criada_em,
                     h.preco_final - c.preco_atual AS delta
              FROM homologacoes h JOIN recomendacoes c ON c.id = h.recomendacao_id
              WHERE ABS(h.preco_final - c.preco_atual) > 0.005)
            SELECT COUNT(*) AS n,
                   SUM(EXISTS (SELECT 1 FROM apl b
                               WHERE b.sku = apl.sku AND b.canal = apl.canal AND b.id > apl.id
                                 AND b.delta * apl.delta < 0
                                 AND julianday(b.criada_em) - julianday(apl.criada_em) <= 30))
                   AS revertidas
            FROM apl
        """) or {}
        n_a = rev.get("n") or 0
        if n_a:
            m["reversao"] = (round(100 * (rev.get("revertidas") or 0) / n_a, 1),
                             "%d de %d aplicações desfeitas em sentido contrário em até 30 dias"
                             % (rev.get("revertidas") or 0, n_a))
        else:
            m["reversao"] = (None, "nenhum preço aplicado ainda")

        n_sku = linha(con, "SELECT COUNT(*) AS n FROM produtos") or {}

    m["monitorados"] = (None, "o protótipo roda com %d SKUs da amostra; o número declarado "
                              "é do catálogo completo" % (n_sku.get("n") or 0))
    return m


def comparativo(rodada_id: int | None = None) -> dict:
    """Antes (planilha) → agora (medido) → meta (planilha), indicador por indicador."""
    medidos = _medir(rodada_id)
    itens = []
    for d in declarada():
        valor, nota = medidos.get(d["chave"], (None, "sem medidor"))
        item = dict(d)
        item["agora"] = valor
        item["nota"] = nota
        item["medido"] = valor is not None
        item["atingiu"] = None
        if valor is not None and d["meta"] is not None:
            item["atingiu"] = (valor >= d["meta"] if d["sentido"] == "cima"
                               else valor <= d["meta"] if d["sentido"] == "baixo" else None)
        if valor is not None and d["base"] is not None:
            delta = valor - d["base"]
            item["delta"] = round(delta, 2)
            item["melhorou"] = (delta > 0 if d["sentido"] == "cima"
                                else delta < 0 if d["sentido"] == "baixo" else None)
        else:
            item["delta"] = None
            item["melhorou"] = None
        itens.append(item)

    medidos_n = [i for i in itens if i["medido"]]
    return {
        "fonte": "Base_de_Dados_-_Popular_Pet.xlsx · aba Indicadores_Atuais",
        "aviso": "Base declaradamente fictícia pela Popular Pet. Os valores de referência são "
                 "os que a empresa entregou; os valores 'agora' são medidos nesta instalação.",
        "itens": itens,
        "medidos": len(medidos_n),
        "total": len(itens),
        "metas_atingidas": sum(1 for i in medidos_n if i["atingiu"] is True),
        "metas_possiveis": sum(1 for i in medidos_n if i["atingiu"] is not None),
    }


def amostra_tempos(limite: int = 60) -> list[dict]:
    """Os tempos cronometrados, para a banca ver que a medida é real."""
    with sessao() as con:
        return linhas(con, """
            SELECT d.id, d.criada_em, d.segundos_analise AS segundos, d.decisao,
                   u.nome AS analista, c.sku, c.canal, c.recomendacao
            FROM decisoes d
            JOIN usuarios u ON u.id = d.usuario_id
            JOIN recomendacoes c ON c.id = d.recomendacao_id
            WHERE d.segundos_analise IS NOT NULL
            ORDER BY d.id DESC LIMIT ?""", limite)
