# -*- coding: utf-8 -*-
"""Seleção da carteira da rodada — qual subconjunto de alterações aplicar hoje.

O preço de cada item já é o ótimo dentro do seu conjunto viável. O que resta é
escolher QUAIS alterar, e isso não é decisão item a item: a capacidade de
validação do analista é finita e a coerência entre canais (R07) amarra itens do
mesmo SKU.

    max   Σ |impacto_i| · x_i
    s.a.  Σ x_i ≤ N                      capacidade da rodada
          x_loja − x_ecom = 0            quando mover um canal sozinho quebraria a R07
          x_i = 0                        bloqueados, em revisão ou sem alteração
          x_i ∈ {0,1}

Resolvido com HiGHS via scipy. Sem scipy instalado, cai para uma seleção gulosa
por impacto — a resposta é a mesma na ausência de acoplamento, e o método usado
aparece no diagnóstico da tela.
"""

from __future__ import annotations

import time

import numpy as np

try:
    from scipy.optimize import Bounds, LinearConstraint, milp
    TEM_SCIPY = True
except Exception:                                      # pragma: no cover
    TEM_SCIPY = False


def _quebra_coerencia(p_a, p_b, tol) -> bool:
    if p_a is None or p_b is None or not (np.isfinite(p_a) and np.isfinite(p_b)):
        return False
    return abs(p_a - p_b) > tol * min(p_a, p_b) + 1e-9


def _pares_acoplados(d, pos, cfg) -> list[tuple[int, int]]:
    """SKUs em que alterar só um dos canais deixaria a diferença acima de 5%."""
    pares = []
    for _, g in d.groupby("sku"):
        canais = {r.canal: r for r in g.itertuples()}
        nomes = [c for c in ("Loja física", "E-commerce") if c in canais]
        if len(nomes) < 2:
            continue
        a, b = canais[nomes[0]], canais[nomes[1]]
        ka, kb = pos.get(a.chave), pos.get(b.chave)
        if ka is None or kb is None:
            continue
        if (_quebra_coerencia(a.preco_sugerido, b.preco_atual, cfg.coerencia_canal)
                or _quebra_coerencia(b.preco_sugerido, a.preco_atual, cfg.coerencia_canal)):
            pares.append((ka, kb))
    return pares


def selecionar(recs, cfg):
    """Devolve (recomendações com `selecionado` e `prioridade`, diagnóstico)."""
    t0 = time.perf_counter()
    d = recs.copy().reset_index(drop=True)
    d["chave"] = d.sku + "|" + d.canal
    d["selecionado"] = False

    elegivel = d.recomendacao.isin(["SUBIR", "BAIXAR"]) & (~d.chave.isin(set(cfg.bloqueados)))
    idx = np.where(elegivel.to_numpy())[0]

    diag = {"metodo": "nenhum item elegível", "elegiveis": int(len(idx)),
            "capacidade": int(cfg.capacidade_rodada), "pares_acoplados": 0,
            "variaveis": int(len(idx)), "restricoes": 0, "ms": 0.0,
            "impacto_selecionado": 0.0, "impacto_disponivel": 0.0,
            "restricao_ativa": False}

    if len(idx) == 0:
        d["prioridade"] = range(1, len(d) + 1)
        diag["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return d.drop(columns=["chave"]), diag

    pos = {d.chave[i]: k for k, i in enumerate(idx)}
    impacto = d.loc[idx, "impacto_mes"].abs().to_numpy(float)
    diag["impacto_disponivel"] = round(float(impacto.sum()), 2)
    pares = _pares_acoplados(d, pos, cfg)
    diag["pares_acoplados"] = len(pares)

    escolhidos = np.zeros(len(idx), dtype=bool)

    # Item estratégico entra na rodada por obrigação, não por mérito de impacto.
    # Sem isso a carteira escolhe pelos maiores números e um item de imagem de
    # preço, que costuma ter margem pequena, nunca chega à mesa da gestora — que
    # é justamente quem precisa vê-lo. Vira restrição de igualdade x_i = 1.
    forcados = []
    if "estrategico" in d.columns:
        forcados = [k for k, i in enumerate(idx) if bool(d.loc[i, "estrategico"])]
    diag["forcados_estrategicos"] = len(forcados)

    if TEM_SCIPY:
        A = [np.ones(len(idx))]
        lb, ub = [0.0], [float(max(cfg.capacidade_rodada, len(forcados)))]
        for k in forcados:
            v = np.zeros(len(idx))
            v[k] = 1.0
            A.append(v)
            lb.append(1.0)
            ub.append(1.0)
        for ka, kb in pares:
            v = np.zeros(len(idx))
            v[ka], v[kb] = 1.0, -1.0
            A.append(v)
            lb.append(0.0)
            ub.append(0.0)
        A = np.vstack(A)
        res = milp(c=-impacto, constraints=LinearConstraint(A, np.array(lb), np.array(ub)),
                   integrality=np.ones(len(idx)), bounds=Bounds(0, 1))
        diag["restricoes"] = int(A.shape[0])
        if res.success:
            escolhidos = np.round(res.x).astype(bool)
            diag["metodo"] = "MILP (HiGHS) — ótimo"
        else:
            diag["metodo"] = "MILP sem solução: %s" % res.message
    else:
        # Guloso por impacto, respeitando os pares acoplados como bloco.
        grupo = {k: k for k in range(len(idx))}
        for ka, kb in pares:
            grupo[kb] = grupo[ka]
        blocos: dict[int, list[int]] = {}
        for k, g in grupo.items():
            blocos.setdefault(g, []).append(k)
        # Estratégicos primeiro e fora da disputa por vaga, igual ao MILP.
        forcado = set(forcados)
        ordenados = sorted(blocos.values(),
                           key=lambda b: (not any(k in forcado for k in b), -impacto[b].sum()))
        vagas = cfg.capacidade_rodada
        for bloco in ordenados:
            if any(k in forcado for k in bloco):
                escolhidos[bloco] = True
                continue
            if len(bloco) <= vagas:
                escolhidos[bloco] = True
                vagas -= len(bloco)
        diag["metodo"] = "guloso (scipy ausente)"
        diag["restricoes"] = 1 + len(pares)

    d.loc[idx[escolhidos], "selecionado"] = True
    diag["impacto_selecionado"] = round(float(impacto[escolhidos].sum()), 2)
    diag["restricao_ativa"] = bool(escolhidos.sum() >= cfg.capacidade_rodada)

    # Ordem de exibição: impacto ponderado pela confiança, penalizado pelo risco.
    peso_risco = {"Baixo": 1.0, "Médio": 1.25, "Alto": 1.6}
    score = (d.impacto_mes.abs() * d.confianca_dados
             / d.nivel_risco.map(peso_risco).fillna(1.0))
    score = score + d.selecionado.astype(float) * 1e9
    d["prioridade"] = score.rank(ascending=False, method="first").astype(int)
    d = d.sort_values("prioridade").reset_index(drop=True)
    diag["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return d.drop(columns=["chave"]), diag


def valor_de_ampliar(recs, cfg, passo: int = 5) -> dict:
    """Preço-sombra da capacidade: quanto renderia revisar `passo` itens a mais."""
    from copy import copy
    c0 = copy(cfg)
    _, d0 = selecionar(recs, c0)
    c1 = copy(cfg)
    c1.capacidade_rodada = cfg.capacidade_rodada + passo
    _, d1 = selecionar(recs, c1)
    ganho = d1["impacto_selecionado"] - d0["impacto_selecionado"]
    return {"passo": passo, "ganho": round(ganho, 2),
            "ganho_por_item": round(ganho / passo, 2) if passo else 0.0,
            "capacidade": d0["capacidade"], "elegiveis": d0["elegiveis"],
            "restricao_ativa": d0["elegiveis"] > d0["capacidade"]}
