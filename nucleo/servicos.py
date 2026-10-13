# -*- coding: utf-8 -*-
"""Rodadas, decisões, métricas, produtos e analistas.

Tudo que a interface pede passa por aqui. As consultas sempre consideram a
decisão VIGENTE de cada recomendação — a mais recente, já que a tabela é
append-only e refazer uma decisão grava um registro novo.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime


from nucleo import carteira, motor, texto
from nucleo.banco import linha, linhas, sessao, valor
from nucleo.modelos import ModeloAceite, ModeloDemanda

CAMPOS_JSON = ("motivos_revisao", "alertas_dados", "regras", "concorrentes",
               "campanha")

# O modelo de demanda é caro de treinar e não muda entre rodadas: treina uma vez.
_demanda: ModeloDemanda | None = None


def modelo_demanda() -> ModeloDemanda:
    global _demanda
    if _demanda is None:
        _demanda = ModeloDemanda().treinar(motor.carregar_coleta()["vendas"])
    return _demanda


def modelo_aceite() -> ModeloAceite:
    with sessao() as con:
        decs = linhas(con, "SELECT decisao, snap_fatores FROM decisoes")
    return ModeloAceite().treinar(decs)


# ----------------------------------------------------------------- rodadas
def gerar_rodada(cfg: motor.Config, substituir: bool = True, aviso=None) -> dict:
    """Roda o motor, seleciona a carteira e grava a rodada no banco.

    `aviso(etapa, texto)` é chamado a cada fase para a tela mostrar onde está.
    Sem ele a função roda calada, do mesmo jeito.
    """
    def _passo(etapa, txt):
        if aviso:
            aviso(etapa, txt)

    _passo("ler", "lendo a base e a coleta de concorrência")
    _passo("decidir", "calculando piso, regras R01–R12 e preço por item")
    recs = motor.decidir(cfg)

    _passo("carteira", "escolhendo quais preços mudar hoje (MILP)")
    recs, diag = carteira.selecionar(recs, cfg)

    _passo("estimar", "estimando volume e chance de aceite")
    dem, ace = modelo_demanda(), modelo_aceite()
    data = recs.iloc[0]["data_rodada"]

    with sessao() as con:
        existente = linha(con, "SELECT id FROM rodadas WHERE data = ?", data)
        if existente:
            if not substituir:
                raise ValueError("Já existe rodada em %s." % data)
            tem_decisao = valor(con, """
                SELECT COUNT(*) FROM decisoes d
                JOIN recomendacoes r ON r.id = d.recomendacao_id
                WHERE r.rodada_id = ?""", existente["id"])
            if tem_decisao:
                raise ValueError(
                    "A rodada de %s já tem %d decisão(ões) registrada(s) e não pode ser "
                    "substituída — a auditoria perderia a recomendação de origem. "
                    "Apague o arquivo dados/popularpet.db para recomeçar do zero." % (
                        data, tem_decisao))
            con.execute("DELETE FROM rodadas WHERE id = ?", (existente["id"],))

        cur = con.execute(
            "INSERT INTO rodadas (data, origem, criada_em, parametros) VALUES (?,?,?,?)",
            (data, "motor", datetime.now().isoformat(timespec="seconds"),
             json.dumps(cfg.dict(), ensure_ascii=False)))
        rodada_id = cur.lastrowid

        # A justificativa da rodada é SEMPRE a determinística. Chamar o modelo de
        # linguagem aqui era o erro: 120 itens × até 45 s de espera cada travava a
        # aplicação por mais de uma hora antes de a tela abrir. O modelo entra sob
        # demanda, num item por vez, pela tela de Análise — que é onde alguém está
        # olhando e pode esperar três segundos.
        _passo("escrever", "escrevendo a justificativa de cada item")
        linhas_recs = recs.to_dict("records")
        for n, r in enumerate(linhas_recs):
            if aviso and n % 20 == 0:
                _passo("escrever", "escrevendo a justificativa · %d de %d"
                       % (n, len(linhas_recs)))
            r["volume_estimado"] = dem.prever(r["preco_sugerido"], r["canal"])
            r["prob_aceite"] = ace.prever(r)
            r["justificativa"] = texto.justificar(r)
            con.execute("""
                INSERT INTO recomendacoes (
                  rodada_id, prioridade, sku, produto, categoria, marca, curva, canal,
                  preco_atual, preco_mercado, indice_preco, menor_concorrente, custo, custo_total,
                  margem_atual, margem_liquida, margem_min, margem_alvo, piso,
                  estoque, cobertura, tendencia_mes, var_12m, recomendacao, motivo_principal,
                  preco_sugerido, variacao, margem_resultante, equilibrio_volume,
                  nivel_risco, risco_score, confianca_dados, n_validos, n_total, exige_aprovacao,
                  receita_mes, impacto_mes, valor_em_jogo, volume_mes, volume_estimado,
                  prob_aceite, justificativa, motivos_revisao, alertas_dados, regras,
                  concorrentes, selecionado,
                  preco_mercado_entregue, indice_entregue, frete_medio_concorrentes,
                  desconto_medio, preco_efetivo, margem_efetiva,
                  cobertura_projetada, pedido_aberto, estrategico,
                  retencao, retencao_motivo, liberado_em, exige_justificativa,
                  fator_confianca, campanha)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (rodada_id, r["prioridade"], r["sku"], r["produto"], r["categoria"], r["marca"],
                  r["curva"], r["canal"], r["preco_atual"], r["preco_mercado"], r["indice_preco"],
                  r["menor_concorrente"], r["custo"], r["custo_total"], r["margem_atual"],
                  r["margem_liquida"], r["margem_min"], r["margem_alvo"], r["piso"],
                  r["estoque"], r["cobertura"], r["tendencia_mes"], r["var_12m"],
                  r["recomendacao"], r["motivo_principal"], r["preco_sugerido"], r["variacao"],
                  r["margem_resultante"], r["equilibrio_volume"], r["nivel_risco"],
                  r["risco_score"], r["confianca_dados"], r["n_validos"], r["n_total"],
                  int(r["exige_aprovacao"]),
                  r["receita_mes"], r["impacto_mes"], r["valor_em_jogo"], r["volume_mes"],
                  r["volume_estimado"], r["prob_aceite"], r["justificativa"],
                  json.dumps(r["motivos_revisao"], ensure_ascii=False),
                  json.dumps(r["alertas_dados"], ensure_ascii=False),
                  json.dumps(r["regras"], ensure_ascii=False),
                  json.dumps(r["concorrentes"], ensure_ascii=False),
                  int(r["selecionado"]),
                  r.get("preco_mercado_entregue"), r.get("indice_entregue"),
                  r.get("frete_medio_concorrentes"), r.get("desconto_medio"),
                  r.get("preco_efetivo"), r.get("margem_efetiva"),
                  r.get("cobertura_projetada"), r.get("pedido_aberto"),
                  int(bool(r.get("estrategico"))),
                  r.get("retencao", "livre"), r.get("retencao_motivo"),
                  r.get("liberado_em"), int(bool(r.get("exige_justificativa"))),
                  r.get("fator_confianca"),
                  json.dumps(r.get("campanha"), ensure_ascii=False)
                  if r.get("campanha") else None))

    _passo("pronto", "rodada de %s com %d itens" % (data, len(recs)))
    return {"rodada_id": rodada_id, "data": data, "itens": len(recs), "otimizacao": diag}


def listar_rodadas() -> list[dict]:
    with sessao() as con:
        return linhas(con, """
            SELECT r.id, r.data, r.origem, r.criada_em, COUNT(c.id) AS itens
            FROM rodadas r LEFT JOIN recomendacoes c ON c.rodada_id = r.id
            GROUP BY r.id ORDER BY r.data DESC""")


def rodada_atual(rodada_id: int | None = None) -> dict | None:
    with sessao() as con:
        if rodada_id:
            return linha(con, "SELECT * FROM rodadas WHERE id = ?", rodada_id)
        return linha(con, "SELECT * FROM rodadas ORDER BY data DESC LIMIT 1")


# -------------------------------------------------------------- consultas
SQL_VIGENTE = """
    LEFT JOIN decisoes d ON d.id = (
        SELECT id FROM decisoes WHERE recomendacao_id = c.id ORDER BY id DESC LIMIT 1)
    LEFT JOIN usuarios u ON u.id = d.usuario_id
"""


def _situacao(rec: dict) -> str:
    if rec.get("decisao") is None:
        return "Sem ação" if rec["recomendacao"] == "MANTER" else "Pendente"
    return {"aprovada": "Aprovada", "editada": "Editada",
            "rejeitada": "Rejeitada", "adiada": "Adiada"}[rec["decisao"]]


def _desempacotar(r: dict) -> dict:
    for c in CAMPOS_JSON:
        if isinstance(r.get(c), str):
            try:
                r[c] = json.loads(r[c])
            except Exception:
                r[c] = []
    r["exige_aprovacao"] = bool(r.get("exige_aprovacao"))
    r["selecionado"] = bool(r.get("selecionado"))
    r["abaixo_do_piso"] = bool(r.get("preco_atual", 0) < (r.get("piso") or 0) - 0.005)
    r["situacao"] = _situacao(r)
    return r


def recomendacoes(rodada_id: int, filtros: dict | None = None) -> list[dict]:
    f = filtros or {}
    where, args = ["c.rodada_id = ?"], [rodada_id]
    if f.get("canal"):
        where.append("c.canal = ?"); args.append(f["canal"])
    if f.get("curva"):
        where.append("c.curva = ?"); args.append(f["curva"])
    if f.get("recomendacao"):
        where.append("c.recomendacao = ?"); args.append(f["recomendacao"])
    if f.get("risco"):
        where.append("c.nivel_risco = ?"); args.append(f["risco"])
    if f.get("exige_aprovacao"):
        where.append("c.exige_aprovacao = 1")
    if f.get("busca"):
        where.append("(c.sku LIKE ? OR c.produto LIKE ?)")
        args += ["%%%s%%" % f["busca"], "%%%s%%" % f["busca"]]

    with sessao() as con:
        rows = linhas(con, """
            SELECT c.*, d.decisao, d.preco_aplicado, d.motivo AS motivo_decisao,
                   d.criada_em AS decidida_em, u.nome AS decidida_por
            FROM recomendacoes c %s
            WHERE %s ORDER BY c.prioridade
        """ % (SQL_VIGENTE, " AND ".join(where)), *args)

    itens = [_desempacotar(r) for r in rows]
    status = f.get("status")
    if status == "pendente":
        itens = [i for i in itens if i["situacao"] in ("Pendente", "Adiada")]
    elif status == "decidido":
        itens = [i for i in itens if i["situacao"] in ("Aprovada", "Editada", "Rejeitada")]
    elif status == "selecionados":
        itens = [i for i in itens if i["selecionado"]]
    return itens


def recomendacao(rec_id: int) -> dict | None:
    with sessao() as con:
        r = linha(con, """
            SELECT c.*, d.decisao, d.preco_aplicado, d.motivo AS motivo_decisao,
                   d.criada_em AS decidida_em, u.nome AS decidida_por
            FROM recomendacoes c %s WHERE c.id = ?""" % SQL_VIGENTE, rec_id)
        if not r:
            return None
        r = _desempacotar(r)
        r["historico_decisoes"] = linhas(con, """
            SELECT d.*, u.nome AS usuario FROM decisoes d
            JOIN usuarios u ON u.id = d.usuario_id
            WHERE d.recomendacao_id = ? ORDER BY d.id DESC""", rec_id)
        r["historico_preco"] = linhas(con, """
            SELECT data, preco_antigo, preco_novo, tipo, motivo FROM historico_preco
            WHERE sku = ? AND canal = ? ORDER BY data""", r["sku"], r["canal"])

        # A composição do custo e a situação do estoque estavam no banco e não
        # chegavam à tela: o analista via "custo R$ 3,20 · total R$ 3,57" sem saber
        # de onde vinham os 37 centavos, e decidia sobre estoque sem ver o pedido
        # em trânsito. São os dois contextos que mudam a decisão de esvaziar
        # estoque ou segurar preço.
        r["custos"] = linha(con, """
            SELECT custo_rep, impostos, frete, taxa_canal, custo_total, margem_min,
                   margem_alvo, data_ref
            FROM estado_canal WHERE sku = ? AND canal = ?""", r["sku"], r["canal"]) or {}
        r["estoque_detalhe"] = linha(con, """
            SELECT quantidade, cobertura_erp, pedido_aberto, previsao, local
            FROM estoque WHERE sku = ?""", r["sku"]) or {}
    r["influencias"] = modelo_aceite().influencias(r)
    return r


def resumo_rodada(rodada_id: int) -> dict:
    itens = recomendacoes(rodada_id)
    if not itens:
        return {"itens": 0}
    com_alteracao = [i for i in itens if i["recomendacao"] in ("SUBIR", "BAIXAR")]
    revisar = [i for i in itens if i["recomendacao"] == "REVISAR"]
    decididos = [i for i in itens if i["situacao"] in ("Aprovada", "Editada", "Rejeitada")]
    pendentes = [i for i in itens if i["situacao"] in ("Pendente", "Adiada")]
    com_indice = [i for i in itens if i.get("indice_preco")]
    na_faixa = [i for i in com_indice if abs(i["indice_preco"] - 1) <= 0.05]
    maior = max(com_alteracao, key=lambda i: i["valor_em_jogo"], default=None)

    motivos: dict[str, int] = {}
    for i in revisar:
        motivos[i["motivo_principal"]] = motivos.get(i["motivo_principal"], 0) + 1

    # Retenção é outra conta: aqui o motor tem opinião, o que falta é quem assina
    # ou o momento. Contar junto com REVISAR escondia a diferença.
    retidos: dict[str, int] = {}
    for i in itens:
        if (i.get("retencao") or "livre") == "livre":
            continue
        regra = next((x for x in (i.get("regras") or [])
                      if x in ("R03", "R04", "R08", "R09")), None)
        rotulo = {"R09": "campanha ativa", "R08": "alterado há menos de 3 dias",
                  "R04": "produto estratégico", "R03": "curva A acima de 3%"}.get(
                      regra, "dado pouco confiável" if i.get("fator_confianca", 1) < 1
                      else "risco alto")
        retidos[rotulo] = retidos.get(rotulo, 0) + 1

    return {
        "itens": len(itens),
        "com_alteracao": len(com_alteracao),
        "selecionados": sum(1 for i in itens if i["selecionado"]),
        "revisar": len(revisar), "manter": sum(1 for i in itens if i["recomendacao"] == "MANTER"),
        "exigem_aprovacao": sum(1 for i in itens if i["exige_aprovacao"]),
        "decididos": len(decididos), "pendentes": len(pendentes),
        "progresso": round(len(decididos) / max(len(com_alteracao) + len(revisar), 1), 3),
        "impacto_selecionado": round(sum(abs(i["impacto_mes"]) for i in itens
                                         if i["selecionado"]), 2),
        "impacto_disponivel": round(sum(abs(i["impacto_mes"]) for i in com_alteracao), 2),
        "cobertura_recomendacao": round(len(com_alteracao) / max(len(itens), 1), 3),
        "na_faixa": round(len(na_faixa) / max(len(com_indice), 1), 3),
        "abaixo_do_piso": sum(1 for i in itens if i["abaixo_do_piso"]),
        "confianca_media": round(sum(i["confianca_dados"] for i in itens) / len(itens), 3),
        "margem_media": round(sum(i["margem_atual"] for i in itens) / len(itens), 4),
        "margem_liquida_media": round(sum(i["margem_liquida"] for i in itens) / len(itens), 4),
        "motivos_revisao": sorted(motivos.items(), key=lambda kv: -kv[1]),
        "retidos": sorted(retidos.items(), key=lambda kv: -kv[1]),
        "por_retencao": {k: sum(1 for i in itens if (i.get("retencao") or "livre") == k)
                         for k in ("livre", "aprovacao", "bloqueado")},
        "por_recomendacao": {a: sum(1 for i in itens if i["recomendacao"] == a)
                             for a in ("SUBIR", "BAIXAR", "MANTER", "REVISAR")},
        "maior_valor": None if maior is None else {
            "sku": maior["sku"], "produto": maior["produto"], "canal": maior["canal"],
            "valor_em_jogo": maior["valor_em_jogo"]},
    }


# ------------------------------------------------------------- decisões
VALIDAS = {"aprovada", "editada", "rejeitada", "adiada"}


def registrar_decisao(rec_id: int, usuario: str, decisao: str,
                      preco_aplicado=None, motivo: str = "", comentario: str = "",
                      segundos_analise=None) -> dict:
    """`segundos_analise` é o cronômetro da tela de análise.

    Vai gravado no mesmo INSERT porque `decisoes` é append-only: um UPDATE
    posterior seria abortado pelo gatilho. É esse campo que transforma as
    "18 horas semanais" declaradas pela Popular Pet em medida, não em promessa.
    """
    if decisao not in VALIDAS:
        raise ValueError("Decisão inválida: %s" % decisao)
    if decisao == "rejeitada" and not (motivo or "").strip():
        raise ValueError("Rejeitar exige um motivo.")

    with sessao() as con:
        rec = linha(con, "SELECT * FROM recomendacoes WHERE id = ?", rec_id)
        if not rec:
            raise ValueError("Recomendação não encontrada.")
        u = linha(con, "SELECT id, papel FROM usuarios WHERE nome = ?", usuario)
        if not u:
            cur = con.execute("INSERT INTO usuarios (nome, papel) VALUES (?, 'Analista')",
                              (usuario,))
            uid = cur.lastrowid
        else:
            uid = u["id"]

        # Aqui grava-se um PARECER, não um preço final — e por isso não há alçada
        # nesta função. Qualquer analista opina sobre qualquer item, inclusive os
        # retidos e os bloqueados: o parecer dele é exatamente o insumo de que a
        # gestora precisa para assinar. Quem barra é `camara.homologar`, que
        # é o único caminho até o arquivo do ERP.
        #
        # O que continua valendo aqui são as duas exigências sobre o próprio
        # parecer: justificativa escrita em item estratégico (R04) e preço acima
        # do piso de margem (R01, logo abaixo). Nenhuma das duas é alçada — são
        # condições do registro.
        muda_preco = decisao in ("aprovada", "editada")
        if muda_preco and rec["exige_justificativa"] and not (comentario or "").strip():
            raise ValueError(
                "Produto estratégico: aprovar ou editar exige justificativa escrita (R04). "
                "Esse registro é o que permite explicar a decisão seis meses depois.")

        if decisao == "editada":
            if preco_aplicado is None:
                raise ValueError("Editar exige o novo preço.")
            preco = round(float(preco_aplicado), 2)
        elif decisao == "aprovada":
            preco = rec["preco_sugerido"]
        else:
            preco = None

        # R01 e R02 são duras na tabela de regras da Popular Pet: "Bloquear" e
        # "Limitar", criticidade alta. Até aqui eu só avisava, e um preço digitado
        # abaixo do piso entrava no banco — o que contradiz "nenhuma recomendação
        # poderá resultar em preço abaixo da margem mínima". Agora recusa.
        #
        # A R02 limita a variação por decisão. Editar acima dela não é erro de
        # digitação como o caso do piso, é uma escolha deliberada, então vira
        # aviso: o preço entra e o registro mostra que passou do limite.
        avisos = []
        if preco is not None:
            if preco < (rec["piso"] or 0) - 0.005:
                raise ValueError(
                    "R01 · o preço de %s fica abaixo do piso de margem mínima, que é %s "
                    "para este SKU neste canal. A regra é dura: nenhum preço abaixo do "
                    "piso entra no sistema."
                    % (("R$ %.2f" % preco).replace(".", ","),
                       ("R$ %.2f" % rec["piso"]).replace(".", ",")))
            if abs(preco - rec["preco_atual"]) > 0.05 * rec["preco_atual"] + 1e-9:
                avisos.append("R02 · a variação de %.1f%% passa do limite de 5%% por decisão"
                              % (100 * (preco / rec["preco_atual"] - 1)))

        fatores = {k: rec[k] for k in ("variacao", "impacto_mes", "confianca_dados", "cobertura",
                                       "margem_resultante", "curva", "nivel_risco",
                                       "recomendacao", "indice_preco")}
        try:
            seg = float(segundos_analise) if segundos_analise is not None else None
            if seg is not None and not (0 < seg < 7200):
                seg = None            # cronômetro esquecido aberto não é medida
        except (TypeError, ValueError):
            seg = None

        cur = con.execute("""
            INSERT INTO decisoes (recomendacao_id, usuario_id, decisao, preco_aplicado, motivo,
                                  comentario, criada_em, snap_recomendacao, snap_preco_atual,
                                  snap_preco_sugerido, snap_fatores, segundos_analise)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (rec_id, uid, decisao, preco, motivo, comentario,
              datetime.now().isoformat(timespec="seconds"), rec["recomendacao"],
              rec["preco_atual"], rec["preco_sugerido"],
              json.dumps(fatores, ensure_ascii=False), seg))
        decisao_id = cur.lastrowid

    return {"ok": True, "avisos": avisos, "decisao_id": decisao_id,
            "segundos_analise": seg}


def historico(filtros: dict | None = None) -> list[dict]:
    f = filtros or {}
    where, args = ["1=1"], []
    if f.get("analista"):
        where.append("u.nome = ?"); args.append(f["analista"])
    if f.get("decisao"):
        where.append("d.decisao = ?"); args.append(f["decisao"])
    if f.get("canal"):
        where.append("c.canal = ?"); args.append(f["canal"])
    if f.get("de"):
        where.append("date(d.criada_em) >= ?"); args.append(f["de"])
    if f.get("ate"):
        where.append("date(d.criada_em) <= ?"); args.append(f["ate"])

    with sessao() as con:
        return linhas(con, """
            SELECT d.id, d.criada_em, u.nome AS analista, c.id AS recomendacao_id, c.sku,
                   c.produto, c.canal, r.data AS rodada, d.snap_recomendacao AS recomendacao,
                   d.snap_preco_sugerido AS preco_sugerido, d.decisao, d.preco_aplicado,
                   d.motivo, d.comentario
            FROM decisoes d
            JOIN recomendacoes c ON c.id = d.recomendacao_id
            JOIN rodadas r ON r.id = c.rodada_id
            JOIN usuarios u ON u.id = d.usuario_id
            WHERE %s ORDER BY d.id DESC LIMIT 500""" % " AND ".join(where), *args)


# -------------------------------------------------------------- métricas
def metricas(rodada_id: int) -> dict:
    """Aceite é a métrica primária, mas só vale acompanhada de cobertura e impacto.

    Sem esse par, um sistema que só sugerisse MANTER teria aceite perfeito e
    valor zero.
    """
    itens = recomendacoes(rodada_id)
    alvo = [i for i in itens if i["recomendacao"] in ("SUBIR", "BAIXAR")]
    decididos = [i for i in alvo if i["situacao"] in ("Aprovada", "Editada", "Rejeitada")]

    aceitas = []
    for i in decididos:
        if i["situacao"] == "Aprovada":
            aceitas.append(i)
        elif i["situacao"] == "Editada":
            pa, ps, p0 = i["preco_aplicado"], i["preco_sugerido"], i["preco_atual"]
            if pa and ps and p0 and abs(pa - ps) <= 0.01 * p0:
                aceitas.append(i)

    ofertado = sum(abs(i["impacto_mes"]) for i in alvo)
    aceito = sum(abs(i["impacto_mes"]) for i in aceitas)
    resumo = resumo_rodada(rodada_id)

    motivos: dict[str, int] = {}
    with sessao() as con:
        for r in linhas(con, """
                SELECT d.motivo FROM decisoes d
                JOIN recomendacoes c ON c.id = d.recomendacao_id
                WHERE c.rodada_id = ? AND d.decisao = 'rejeitada'""", rodada_id):
            m = (r["motivo"] or "sem motivo informado").strip()
            motivos[m] = motivos.get(m, 0) + 1

    por_categoria: dict[str, list[float]] = {}
    for i in itens:
        if i.get("indice_preco"):
            por_categoria.setdefault(i["categoria"], []).append(i["indice_preco"])

    return {
        "taxa_aceite": round(len(aceitas) / len(decididos), 3) if decididos else None,
        "decididos": len(decididos), "aceitas": len(aceitas),
        "cobertura_recomendacao": resumo["cobertura_recomendacao"],
        "impacto_ofertado": round(ofertado, 2), "impacto_aceito": round(aceito, 2),
        "parcela_impacto": round(aceito / ofertado, 3) if ofertado > 0 else None,
        "na_faixa": resumo["na_faixa"], "margem_media": resumo["margem_media"],
        "margem_liquida_media": resumo["margem_liquida_media"],
        "abaixo_do_piso": resumo["abaixo_do_piso"],
        "por_recomendacao": resumo["por_recomendacao"],
        "motivos_rejeicao": sorted(motivos.items(), key=lambda kv: -kv[1]),
        "indice_por_categoria": sorted(
            [{"categoria": k, "indice": round(sum(v) / len(v), 4), "n": len(v)}
             for k, v in por_categoria.items()], key=lambda x: -x["indice"]),
        "top_valor": [{"sku": i["sku"], "produto": i["produto"], "canal": i["canal"],
                       "recomendacao": i["recomendacao"], "valor_em_jogo": i["valor_em_jogo"],
                       "situacao": i["situacao"], "id": i["id"]}
                      for i in sorted(alvo, key=lambda x: -x["valor_em_jogo"])[:10]],
    }


# -------------------------------------------------------- produtos e pessoas
def produtos(busca: str = "", ordem: str = "sku") -> list[dict]:
    with sessao() as con:
        rows = linhas(con, """
            SELECT p.sku, p.nome AS produto, p.categoria, p.marca, p.curva, p.status,
                   s.quantidade AS estoque, s.cobertura_erp,
                   (SELECT COUNT(*) FROM historico_preco h WHERE h.sku = p.sku) AS alteracoes,
                   (SELECT MAX(data) FROM historico_preco h
                    WHERE h.sku = p.sku AND tipo IN ('AUMENTO','REDUCAO')) AS ultima_alteracao,
                   (SELECT AVG(preco) FROM estado_canal e WHERE e.sku = p.sku) AS preco_medio
            FROM produtos p LEFT JOIN estoque s ON s.sku = p.sku
            WHERE (? = '' OR p.sku LIKE ? OR p.nome LIKE ?)
            ORDER BY %s""" % {"sku": "p.sku", "alteracao": "ultima_alteracao DESC",
                              "estoque": "s.cobertura_erp"}.get(ordem, "p.sku"),
            busca, "%%%s%%" % busca, "%%%s%%" % busca)
    return rows


def produto(sku: str) -> dict | None:
    with sessao() as con:
        p = linha(con, """
            SELECT p.*, s.quantidade AS estoque, s.cobertura_erp, s.pedido_aberto,
                   s.previsao, s.local
            FROM produtos p LEFT JOIN estoque s ON s.sku = p.sku WHERE p.sku = ?""", sku)
        if not p:
            return None
        p["canais"] = linhas(con, "SELECT * FROM estado_canal WHERE sku = ? ORDER BY canal", sku)
        p["historico_preco"] = linhas(con, """
            SELECT data, canal, preco_antigo, preco_novo, tipo, origem, motivo
            FROM historico_preco WHERE sku = ? ORDER BY data, id""", sku)
        p["recomendacoes"] = linhas(con, """
            SELECT c.id, r.data AS rodada, c.canal, c.recomendacao, c.preco_atual,
                   c.preco_sugerido, c.nivel_risco
            FROM recomendacoes c JOIN rodadas r ON r.id = c.rodada_id
            WHERE c.sku = ? ORDER BY r.data DESC, c.canal""", sku)
    return p


def analistas() -> list[dict]:
    with sessao() as con:
        return linhas(con, """
            SELECT u.id, u.nome, u.papel,
                   COUNT(d.id) AS decisoes,
                   SUM(CASE WHEN d.decisao='aprovada' THEN 1 ELSE 0 END) AS aprovadas,
                   SUM(CASE WHEN d.decisao='editada'  THEN 1 ELSE 0 END) AS editadas,
                   SUM(CASE WHEN d.decisao='rejeitada' THEN 1 ELSE 0 END) AS rejeitadas,
                   SUM(CASE WHEN d.decisao='adiada'   THEN 1 ELSE 0 END) AS adiadas,
                   MAX(d.criada_em) AS ultima
            FROM usuarios u LEFT JOIN decisoes d ON d.usuario_id = u.id
            GROUP BY u.id ORDER BY decisoes DESC, u.nome""")


def analista(nome: str) -> dict | None:
    with sessao() as con:
        u = linha(con, "SELECT * FROM usuarios WHERE nome = ?", nome)
        if not u:
            return None
        u["decisoes"] = linhas(con, """
            SELECT d.id, d.criada_em, c.sku, c.produto, c.canal, r.data AS rodada,
                   d.snap_recomendacao AS recomendacao, d.decisao, d.preco_aplicado, d.motivo,
                   c.impacto_mes
            FROM decisoes d
            JOIN recomendacoes c ON c.id = d.recomendacao_id
            JOIN rodadas r ON r.id = c.rodada_id
            WHERE d.usuario_id = ? ORDER BY d.id DESC LIMIT 200""", u["id"])
    alvo = [d for d in u["decisoes"] if d["recomendacao"] in ("SUBIR", "BAIXAR")
            and d["decisao"] in ("aprovada", "editada", "rejeitada")]
    aceitas = [d for d in alvo if d["decisao"] in ("aprovada", "editada")]
    u["taxa_aceite"] = round(len(aceitas) / len(alvo), 3) if alvo else None
    u["impacto_aceito"] = round(sum(abs(d["impacto_mes"] or 0) for d in aceitas), 2)
    return u


def atividade_recente(limite: int = 12) -> list[dict]:
    with sessao() as con:
        decs = linhas(con, """
            SELECT d.criada_em AS quando, u.nome AS quem, d.decisao AS o_que,
                   c.sku || ' · ' || c.canal AS alvo, 'decisao' AS tipo
            FROM decisoes d
            JOIN recomendacoes c ON c.id = d.recomendacao_id
            JOIN usuarios u ON u.id = d.usuario_id
            ORDER BY d.id DESC LIMIT ?""", limite)
        syncs = linhas(con, """
            SELECT quando, COALESCE(usuario,'Sistema') AS quem,
                   'sincronização ' || status AS o_que, origem AS alvo, 'sync' AS tipo
            FROM sync_erp ORDER BY id DESC LIMIT ?""", limite)
    return sorted(decs + syncs, key=lambda x: x["quando"], reverse=True)[:limite]


# ------------------------------------------------------------ exportações
def exportar_aprovadas(rodada_id: int, apenas_novos: bool = False,
                       usuario: str = "", registrar: bool = False) -> tuple[str, str]:
    """CSV do que vai ao ERP: só o que foi HOMOLOGADO.

    Antes este arquivo saía de qualquer parecer de "aprovada"/"editada", o que
    deixava o preço de um analista chegar sozinho ao ERP. Agora a fonte é a
    tabela `homologacoes`: cada linha tem preço final, o caminho que a autorizou
    (gestora ou maioria) e quem assinou.

    O arquivo não é só a lista de preços novos: carrega preço de mercado, custo,
    margem, estoque, justificativa e nível de risco. Quem recebe o arquivo do
    outro lado consegue auditar a decisão sem abrir a plataforma — que é o que a
    exigência de rastreabilidade quer dizer na prática.
    """
    with sessao() as con:
        # LEFT JOIN em `usuarios`: a homologação por decurso de prazo não tem
        # usuário — foi a política que decidiu. Com INNER JOIN, exatamente essas
        # linhas desapareciam do arquivo do ERP, que é o pior lugar para perder
        # um preço: o item ficava homologado no banco e nunca chegava ao ERP.
        homs = {h["recomendacao_id"]: h for h in linhas(con, """
            SELECT h.*, COALESCE(u.nome, 'política de omissão') AS homologada_por,
                   (SELECT lote FROM envios_erp WHERE homologacao_id = h.id) AS lote_erp
            FROM homologacoes h LEFT JOIN usuarios u ON u.id = h.usuario_id
            JOIN recomendacoes c ON c.id = h.recomendacao_id
            WHERE c.rodada_id = ?""", rodada_id)}
    itens = [i for i in recomendacoes(rodada_id) if i["id"] in homs]
    if apenas_novos:
        itens = [i for i in itens if not homs[i["id"]]["lote_erp"]]
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["sku", "produto", "categoria", "marca", "curva", "canal",
                "preco_atual", "preco_mercado", "indice_preco", "custo_reposicao",
                "custo_total", "margem_atual", "margem_resultante", "margem_minima",
                "piso_margem", "estoque", "cobertura_dias", "recomendacao",
                "preco_aprovado", "variacao", "nivel_risco", "exige_aprovacao",
                "regras_acionadas", "justificativa", "impacto_margem_mes",
                "equilibrio_volume", "decisao", "analista", "decidido_em",
                "homologado_via", "homologado_por", "homologado_em", "pareceres"])

    def _n(v, casas=2):
        return "" if v is None else ("%.*f" % (casas, v))

    for i in itens:
        h = homs[i["id"]]
        preco = h["preco_final"]          # o número homologado, não o do parecer
        try:
            ap = json.loads(h["apuracao"] or "{}")
        except Exception:
            ap = {}
        trilha = " | ".join("%s (%s): %s %s" % (
            p["usuario"], p["papel"], p["decisao"],
            "" if p.get("preco") is None else ("R$ %.2f" % p["preco"]).replace(".", ","))
            for p in ap.get("pareceres", []))
        w.writerow([
            i["sku"], i["produto"], i["categoria"], i["marca"], i["curva"], i["canal"],
            _n(i["preco_atual"]), _n(i.get("preco_mercado")), _n(i.get("indice_preco"), 4),
            _n(i.get("custo")), _n(i.get("custo_total")),
            _n(i.get("margem_atual"), 4), _n(i.get("margem_resultante"), 4),
            _n(i.get("margem_min"), 4), _n(i.get("piso")),
            i.get("estoque"), _n(i.get("cobertura"), 1),
            i["recomendacao"], _n(preco), _n((preco / i["preco_atual"]) - 1, 4),
            i["nivel_risco"], "sim" if i.get("exige_aprovacao") else "não",
            " ".join(i.get("regras") or []), (i.get("justificativa") or "").replace("\n", " "),
            _n(i.get("impacto_mes")), _n(i.get("equilibrio_volume"), 4),
            i["situacao"], i["decidida_por"] or "", i["decidida_em"] or "",
            h["via"], h["homologada_por"], h["quando"], trilha])
    with sessao() as con:
        data = valor(con, "SELECT data FROM rodadas WHERE id = ?", rodada_id)
    lote = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    nome = "precos_homologados_%s_%s.csv" % (data, lote)
    if registrar and itens:
        from . import camara
        camara.registrar_envio([homs[i["id"]]["id"] for i in itens],
                                    usuario or "Sistema", nome)
    return nome, buf.getvalue()


def exportar_historico(filtros: dict | None = None) -> tuple[str, str]:
    regs = historico(filtros)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["data_hora", "analista", "sku", "produto", "canal", "rodada", "recomendacao",
                "preco_sugerido", "decisao", "preco_aplicado", "motivo", "comentario"])
    for r in regs:
        w.writerow([r["criada_em"], r["analista"], r["sku"], r["produto"], r["canal"],
                    r["rodada"], r["recomendacao"],
                    "" if r["preco_sugerido"] is None else "%.2f" % r["preco_sugerido"],
                    r["decisao"],
                    "" if r["preco_aplicado"] is None else "%.2f" % r["preco_aplicado"],
                    r["motivo"] or "", r["comentario"] or ""])
    return "historico_%s.csv" % datetime.now().strftime("%Y-%m-%d_%H%M"), buf.getvalue()


# ----------------------------------------------------------- estratégicos
def marcar_estrategico(sku: str, estrategico: bool, usuario: str, nota: str = "") -> dict:
    """Marca ou desmarca um SKU como estratégico.

    É a única marcação da plataforma que não vem do ERP: quais itens sustentam a
    imagem de preço da loja é decisão comercial, não dado de sistema. Por isso
    guarda quem marcou, quando e por quê — sem isso vira um campo que ninguém
    sabe explicar seis meses depois.

    O efeito é de roteamento, não de cálculo, exatamente como a Popular Pet
    escreveu a R04: "produtos estratégicos nunca são aplicados automaticamente".
    O preço sugerido continua o mesmo; muda quem assina.
    """
    with sessao() as con:
        p = linha(con, "SELECT sku FROM produtos WHERE sku = ?", sku)
        if not p:
            raise ValueError("SKU não encontrado: %s" % sku)
        if estrategico and not (nota or "").strip():
            raise ValueError("Marcar como estratégico exige dizer por quê.")
        con.execute("""UPDATE produtos SET estrategico = ?, estrategico_por = ?,
                              estrategico_em = ?, estrategico_nota = ? WHERE sku = ?""",
                    (1 if estrategico else 0, usuario if estrategico else None,
                     datetime.now().isoformat(timespec="seconds") if estrategico else None,
                     (nota or "").strip() if estrategico else None, sku))
    return {"ok": True, "sku": sku, "estrategico": bool(estrategico),
            "aviso": "A marcação vale a partir da próxima rodada." if estrategico else
                     "SKU deixou de ser estratégico a partir da próxima rodada."}


def estrategicos() -> list[dict]:
    with sessao() as con:
        return linhas(con, """
            SELECT sku, nome AS produto, categoria, marca, curva,
                   estrategico_por, estrategico_em, estrategico_nota
            FROM produtos WHERE estrategico = 1 ORDER BY sku""")
