# -*- coding: utf-8 -*-
"""Popular Pet · Precificação Inteligente — aplicação completa.

    python app.py

Abre em http://127.0.0.1:8000. Na primeira execução cria o banco, lê a planilha
do ERP e gera a primeira rodada — nada precisa ser configurado antes.

Opções: --porta 9000 · --refazer (apaga o banco e recomeça) · --sem-navegador
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import traceback
import webbrowser

PASTA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PASTA)


def _exigir_dependencias() -> None:
    faltando = []
    for modulo, pacote in [("flask", "flask"), ("pandas", "pandas"),
                           ("numpy", "numpy"), ("openpyxl", "openpyxl")]:
        try:
            __import__(modulo)
        except ImportError:
            faltando.append(pacote)
    if faltando:
        print("\nFaltam pacotes para a aplicação subir: %s\n" % ", ".join(faltando))
        print("Instale com:\n    pip install -r requirements.txt\n")
        sys.exit(1)


_exigir_dependencias()

from flask import Flask, Response, jsonify, request, send_from_directory  # noqa: E402

from nucleo import (acompanhamento, banco, camara, erp, governanca,  # noqa: E402
                    ingestao, linha_base, llm, motor, pipeline, series, servicos)
from nucleo.carteira import TEM_SCIPY, valor_de_ampliar  # noqa: E402
from nucleo.modelos import TEM_SKLEARN  # noqa: E402

app = Flask(__name__, static_folder=None)
WEB = os.path.join(PASTA, "web")


# ------------------------------------------------------------- preparação
def preparar() -> None:
    """Garante o banco e a planilha. Nada de pipeline aqui.

    Isto é tudo que acontece antes de a tela abrir, e leva milissegundos. O
    pipeline é disparado da interface, onde dá para mostrar em que etapa está —
    terminal parado por minutos é indistinguível de travamento.
    """
    planilha = os.path.join(banco.PASTA_DADOS, "base_popular_pet.xlsx")
    if not os.path.exists(planilha):
        print("\nA planilha do ERP não foi encontrada em:\n    %s\n" % planilha)
        print("Coloque o arquivo base_popular_pet.xlsx nessa pasta e rode de novo.\n")
        sys.exit(1)
    banco.criar_esquema()

    # A plataforma é um serviço: o pipeline roda quando o dado chega, não quando
    # alguém clica. Na primeira subida o dado "chega" junto com a planilha, então
    # o pipeline dispara sozinho, em segundo plano. O boot não espera por ele — a
    # tela abre na hora e mostra o progresso no próprio painel.
    if banco.banco_vazio() or not servicos.listar_rodadas():
        pipeline.disparar([pipeline.passo_sincronizar("Coletor automático"),
                           pipeline.passo_estrategicos(),
                           pipeline.passo_rodada(None, semear=True)])


def recomecar() -> None:
    for sufixo in ("", "-wal", "-shm"):
        caminho = banco.ARQUIVO_BANCO + sufixo
        if os.path.exists(caminho):
            os.remove(caminho)
    print("  banco anterior apagado")


# ------------------------------------------------------------------- erros
@app.errorhandler(Exception)
def erro(e):
    if isinstance(e, ValueError):
        return jsonify({"erro": str(e)}), 400
    if isinstance(e, erp.ErroErp):
        return jsonify({"erro": str(e)}), 400
    traceback.print_exc()
    return jsonify({"erro": "Erro interno: %s" % e}), 500


def _rodada_pedida() -> dict | None:
    rid = request.args.get("rodada_id", type=int)
    return servicos.rodada_atual(rid)


def _config_da_query() -> motor.Config:
    cfg = motor.Config()
    for campo, tipo in [("regua", str), ("variacao_max", float), ("banda_morta", float),
                        ("cobertura_min_dias", int), ("capacidade_rodada", int),
                        ("posicionamento", float), ("confianca_minima", float)]:
        v = request.args.get(campo)
        if v not in (None, ""):
            setattr(cfg, campo, tipo(v))
    return cfg


# -------------------------------------------------------------- contexto
@app.get("/api/contexto")
def api_contexto():
    """Tudo que a interface precisa para montar o cabeçalho."""
    with banco.sessao() as con:
        usuarios = banco.linhas(con, "SELECT nome, papel FROM usuarios ORDER BY papel, nome")
    return jsonify({
        "usuarios": usuarios,
        "rodadas": servicos.listar_rodadas(),
        "rodada_atual": servicos.rodada_atual(),
        "erp": erp.situacao(),
        "motores": {"otimizacao": "MILP (HiGHS)" if TEM_SCIPY else "guloso — instale scipy",
                    "modelos_tabulares": TEM_SKLEARN,
                    "demanda": servicos.modelo_demanda().situacao(),
                    "aceite": servicos.modelo_aceite().situacao(),
                    "llm": llm.situacao()},
    })


# -------------------------------------------------------------- pipeline
@app.get("/api/pipeline")
def api_pipeline():
    """Estado da execução. A tela pergunta a cada segundo."""
    e = pipeline.estado()
    rodadas = servicos.listar_rodadas()
    e["tem_rodada"] = bool(rodadas)
    e["tem_base"] = not banco.banco_vazio()
    # Sem execução nesta sessão, a última atualização é a da rodada no banco: o
    # serviço não começou a existir quando este processo subiu.
    if not e["ultima"] and rodadas:
        # `listar_rodadas` já traz a contagem de itens; `rodada_atual` lê a tabela
        # crua, que não tem essa coluna.
        r = rodadas[0]
        e["ultima"] = {"quando": r.get("criada_em"),
                       "resumo": "rodada de %s · %s itens" % (r.get("data"),
                                                              r.get("itens", "?")),
                       "segundos": None}
    return jsonify(e)


@app.post("/api/pipeline")
def api_pipeline_rodar():
    """Dispara o pipeline. O corpo diz o que incluir."""
    d = request.get_json(silent=True) or {}
    passos = []
    if d.get("sincronizar") or banco.banco_vazio():
        passos.append(pipeline.passo_sincronizar(d.get("usuario") or "Sistema"))
        if d.get("demonstracao", True):
            passos.append(pipeline.passo_estrategicos())
    cfg = _config_do_corpo(d.get("config") or {})
    passos.append(pipeline.passo_rodada(cfg, semear=d.get("demonstracao", True)
                                        and banco.banco_vazio()))
    return jsonify(pipeline.disparar(passos))


def _config_do_corpo(d: dict) -> motor.Config:
    cfg = motor.Config()
    for k, v in (d or {}).items():
        if hasattr(cfg, k) and v is not None:
            setattr(cfg, k, v)
    return cfg


# --------------------------------------------------------------- rodadas
@app.post("/api/rodadas")
def api_gerar_rodada():
    cfg = _config_da_query()
    dados = request.get_json(silent=True) or {}
    for k, v in dados.items():
        if hasattr(cfg, k) and v is not None:
            setattr(cfg, k, v)
    return jsonify(servicos.gerar_rodada(cfg))


@app.get("/api/recomendacoes")
def api_recomendacoes():
    r = _rodada_pedida()
    if not r:
        return jsonify({"rodada": None, "itens": [], "resumo": {"itens": 0}})
    filtros = {k: request.args.get(k) for k in
               ("canal", "curva", "recomendacao", "risco", "status", "busca")}
    filtros["exige_aprovacao"] = request.args.get("exige_aprovacao") == "1"
    return jsonify({"rodada": r, "resumo": servicos.resumo_rodada(r["id"]),
                    "itens": servicos.recomendacoes(r["id"], filtros)})


@app.get("/api/recomendacoes/<int:rec_id>")
def api_recomendacao(rec_id):
    r = servicos.recomendacao(rec_id)
    if not r:
        return jsonify({"erro": "Recomendação não encontrada."}), 404
    return jsonify(r)


@app.get("/api/valor-de-ampliar")
def api_valor_ampliar():
    r = _rodada_pedida()
    if not r:
        return jsonify({"erro": "Nenhuma rodada."}), 404
    cfg = motor.Config()
    recs = servicos.recomendacoes(r["id"])
    import pandas as pd
    return jsonify(valor_de_ampliar(pd.DataFrame(recs), cfg))


# -------------------------------------------------------------- decisões
@app.post("/api/decisoes")
def api_decidir():
    d = request.get_json(force=True)
    out = servicos.registrar_decisao(
        int(d["recomendacao_id"]), d.get("usuario") or "Ana Souza", d["decisao"],
        d.get("preco_aplicado"), d.get("motivo", ""), d.get("comentario", ""),
        d.get("segundos_analise"))
    return jsonify(out)


# --------------------------------------------------------------- análise
# ------------------------------------------------------ câmara de preços
@app.get("/api/camara")
def api_camara():
    """A pauta do dia: cada item com a cédula, os votos e o que falta."""
    r = _rodada_pedida()
    if not r:
        return jsonify({"rodada": None, "itens": [], "contagem": {}, "total": 0,
                        "politica": camara.politica(), "prazo": camara.prazo()})
    p = camara.pauta(r["id"], request.args.get("fase", ""), request.args.get("busca", ""))
    p["rodada"] = r
    p["pendencias"] = camara.pendencias(r["id"])
    p["lotes"] = camara.lotes()
    return jsonify(p)


@app.get("/api/camara/<int:rec_id>")
def api_camara_item(rec_id):
    a = camara.apurar_rec(rec_id)
    if not a:
        return jsonify({"erro": "Item não encontrado."}), 404
    a["thread"] = camara.thread(rec_id)
    return jsonify(a)


@app.post("/api/camara/votar")
def api_votar():
    d = request.get_json(force=True)
    return jsonify(camara.votar(int(d["recomendacao_id"]), int(d["candidato_id"]),
                                d.get("usuario") or "Ana Souza", d.get("comentario", ""),
                                d.get("segundos")))


@app.post("/api/camara/propor")
def api_propor():
    d = request.get_json(force=True)
    return jsonify(camara.propor(int(d["recomendacao_id"]), float(d["preco"]),
                                 d.get("usuario") or "Ana Souza",
                                 d.get("justificativa", "")))


@app.post("/api/camara/falar")
def api_falar():
    d = request.get_json(force=True)
    if d.get("ao_agente"):
        return jsonify(camara.responder_agente(int(d["recomendacao_id"]), d["texto"],
                                               d.get("usuario") or "Ana Souza"))
    return jsonify(camara.falar(int(d["recomendacao_id"]), d.get("usuario") or "Ana Souza",
                                d["texto"]))


@app.post("/api/camara/homologar")
def api_homologar():
    """Só o gestor passa daqui. Sem candidato, homologa o consenso da câmara."""
    d = request.get_json(force=True)
    usuario = d.get("usuario") or "Carla Menezes"
    if d.get("lote"):
        r = _rodada_pedida()
        if not r:
            raise ValueError("Nenhuma rodada.")
        return jsonify(camara.homologar_consenso(r["id"], usuario))
    cid = d.get("candidato_id")
    return jsonify(camara.homologar(int(d["recomendacao_id"]), usuario,
                                    int(cid) if cid else None,
                                    d.get("justificativa", "")))


@app.get("/api/camara/politica")
def api_politica():
    return jsonify(camara.politica())


@app.post("/api/camara/politica")
def api_definir_politica():
    d = request.get_json(force=True)
    return jsonify(camara.definir_politica(d["valor"], d.get("usuario") or ""))


@app.post("/api/camara/fechar")
def api_fechar_pauta():
    """O que as 18h fariam. Na tela serve para mostrar o efeito da política."""
    r = _rodada_pedida()
    if not r:
        raise ValueError("Nenhuma rodada.")
    d = request.get_json(silent=True) or {}
    return jsonify(camara.fechar_pauta(r["id"], d.get("usuario") or "Sistema"))



@app.get("/api/analise")
def api_analise():
    """A fila de análise, um item por vez, com o contexto da linha de produto."""
    r = _rodada_pedida()
    if not r:
        return jsonify({"rodada": None, "fila": [], "total": 0})
    itens = servicos.recomendacoes(r["id"])
    pendentes = [i for i in itens if i["situacao"] == "Pendente"
                 and i["recomendacao"] in ("SUBIR", "BAIXAR", "REVISAR")]
    pendentes.sort(key=lambda i: -i["valor_em_jogo"])
    return jsonify({
        "rodada": r, "fila": [i["id"] for i in pendentes],
        "total": len(pendentes),
        "valor_restante": round(sum(i["valor_em_jogo"] for i in pendentes), 2),
        "decididos": sum(1 for i in itens if i["situacao"] != "Pendente"),
        "itens_rodada": len(itens),
    })


@app.get("/api/analise/<int:rec_id>")
def api_analise_item(rec_id):
    i = servicos.recomendacao(rec_id)
    if not i:
        return jsonify({"erro": "Item não encontrado."}), 404
    # A cédula vai junto: a Análise e a Câmara mostram a MESMA votação, e o
    # analista vota de onde estiver olhando o item.
    a = camara.apurar_rec(rec_id) or {}
    i["apuracao"] = a
    i["n_mensagens"] = a.get("mensagens") or 0
    i["bloqueado_r08"] = bool(a.get("bloqueado"))
    return jsonify({"item": i, "contexto": acompanhamento.contexto_linha(i["sku"])})


# ------------------------------------------------------------ linha de base
@app.get("/api/linha-base")
def api_linha_base():
    r = servicos.rodada_atual()
    return jsonify(linha_base.comparativo(r["id"] if r else None))


@app.get("/api/tempos")
def api_tempos():
    return jsonify({"itens": linha_base.amostra_tempos()})


# ------------------------------------------------------ impacto pós-decisão
@app.get("/api/impacto")
def api_impacto():
    return jsonify(acompanhamento.carteira())


@app.get("/api/impacto/<int:decisao_id>")
def api_impacto_um(decisao_id):
    p = acompanhamento.placar(decisao_id)
    if not p:
        return jsonify({"erro": "Essa decisão não alterou preço — não há impacto a medir."}), 404
    return jsonify(p)


@app.get("/api/series/panorama")
def api_series_panorama():
    return jsonify(series.panorama())


@app.get("/api/series/item/<sku>")
def api_series_item(sku):
    canal = request.args.get("canal")
    return jsonify({"item": series.serie_item(sku, canal),
                    "pares": series.serie_pares(sku, canal),
                    "volume": series.serie_volume_categoria(sku, canal)})


@app.get("/api/llm")
def api_llm():
    """`?procurar=1` força uma nova varredura — é o botão Procurar modelo."""
    if request.args.get("procurar"):
        llm.descobrir(forcar=True)
    return jsonify(llm.situacao())


@app.post("/api/recomendacoes/<int:rec_id>/reescrever")
def api_reescrever(rec_id):
    """Reescreve a justificativa de UM item com o modelo local, sob demanda.

    Sob demanda e um por vez porque é assim que faz sentido: alguém está olhando
    a tela e pode esperar alguns segundos. Em lote, na geração da rodada, isso
    multiplicava a espera por 120 e travava a aplicação antes de qualquer tela
    existir — foi exatamente o erro que esta rota existe para não repetir.
    """
    from nucleo import texto as _texto
    r = servicos.recomendacao(rec_id)
    if not r:
        return jsonify({"erro": "Item não encontrado."}), 404
    if not llm.situacao()["ativo"]:
        return jsonify({"erro": "Nenhum modelo conectado. Suba um servidor local e clique "
                                "em Procurar modelo na tela Base."}), 400
    g = _texto.gerar(r)
    return jsonify({"determinista": _texto.justificar(r), "texto": g["texto"],
                    "origem": g["origem"], "diagnostico": g.get("diagnostico")})


@app.post("/api/llm/testar")
def api_llm_testar():
    """Pede uma justificativa de verdade e mostra se passou na conferência."""
    from nucleo import texto as _texto
    rid = request.args.get("recomendacao_id", type=int)
    r = servicos.recomendacao(rid) if rid else None
    if not r:
        rod = servicos.rodada_atual()
        itens = servicos.recomendacoes(rod["id"]) if rod else []
        alvo = [i for i in itens if i["recomendacao"] in ("SUBIR", "BAIXAR")]
        if not alvo:
            raise ValueError("Nenhuma recomendação com alteração de preço para testar.")
        r = servicos.recomendacao(alvo[0]["id"])
    g = _texto.gerar(r)
    return jsonify({"sku": r["sku"], "canal": r["canal"], "determinismo": _texto.justificar(r),
                    "texto": g["texto"], "origem": g["origem"],
                    "diagnostico": g.get("diagnostico"), "llm": llm.situacao()})


@app.get("/api/serie/<sku>")
def api_serie(sku):
    return jsonify(acompanhamento.serie_sku(sku, request.args.get("canal")))


@app.post("/api/venda-pos")
def api_venda_pos():
    """Entrada do piloto: venda do ERP posterior a uma aplicação de preço."""
    d = request.get_json(force=True)
    n = acompanhamento.registrar_venda_pos(d.get("registros") or [], d.get("origem", "ERP"))
    return jsonify({"ok": True, "registros": n})


# ----------------------------------------------------------- estratégicos
@app.get("/api/estrategicos")
def api_estrategicos():
    return jsonify({"itens": servicos.estrategicos()})


@app.post("/api/estrategicos")
def api_marcar_estrategico():
    d = request.get_json(force=True)
    return jsonify(servicos.marcar_estrategico(
        d["sku"], bool(d.get("estrategico", True)),
        d.get("usuario") or "Sistema", d.get("nota", "")))


# ------------------------------------------------------------- governança
@app.get("/api/governanca")
def api_governanca():
    return jsonify(governanca.situacao())


@app.get("/api/envelope")
def api_envelope():
    """O envelope medido por `python envelope.py --salvar dados/envelope.json`."""
    caminho = os.path.join(banco.PASTA_DADOS, "envelope.json")
    if not os.path.exists(caminho):
        return jsonify({"medido": False,
                        "como": "rode `python envelope.py --salvar dados/envelope.json`"})
    import json
    with open(caminho, encoding="utf-8") as f:
        d = json.load(f)
    d["medido"] = True
    return jsonify(d)


@app.get("/api/casos")
def api_casos():
    caminho = os.path.join(PASTA, "casos", "casos.json")
    if not os.path.exists(caminho):
        return jsonify({"gerado": False, "como": "rode `python casos.py`"})
    import json
    with open(caminho, encoding="utf-8") as f:
        d = json.load(f)
    d["gerado"] = True
    d["casos"] = [{k: v for k, v in c.items() if k not in ("instancia", "acompanhantes")}
                  | {"sku": (c.get("instancia") or {}).get("sku"),
                     "canal": (c.get("instancia") or {}).get("canal"),
                     "saiu": (c.get("instancia") or {}).get("recomendacao")}
                  for c in d.get("casos", [])]
    return jsonify(d)


# ---------------------------------------------------- ingestão de CSV cru
@app.post("/api/ingestao/descobrir")
def api_ingestao_descobrir():
    """Recebe o CSV cru do ERP e devolve o mapa de colunas proposto."""
    arq = request.files.get("arquivo")
    if arq is None:
        raise ValueError("Envie um arquivo .csv exportado do ERP.")
    import io

    import pandas as pd
    bruto = arq.read()
    texto = None
    for cod in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            texto = bruto.decode(cod)
            break
        except UnicodeDecodeError:
            continue
    if texto is None:
        raise ValueError("Não consegui ler o arquivo: codificação desconhecida.")
    try:
        df = pd.read_csv(io.StringIO(texto), sep=None, engine="python")
    except Exception as e:                                        # noqa: BLE001
        raise ValueError("Não consegui interpretar o CSV: %s" % e)
    if df.empty:
        raise ValueError("O arquivo não tem linhas.")

    r = ingestao.descobrir(df)
    r["arquivo"] = arq.filename
    r["previa"] = df.head(5).astype(str).to_dict("records")
    r["colunas"] = list(df.columns)
    return jsonify(r)


@app.get("/api/historico")
def api_historico():
    f = {k: request.args.get(k) for k in ("analista", "decisao", "canal", "de", "ate")}
    return jsonify({"registros": servicos.historico(f)})


# -------------------------------------------------------------- métricas
@app.get("/api/metricas")
def api_metricas():
    r = _rodada_pedida()
    if not r:
        return jsonify({"erro": "Nenhuma rodada."}), 404
    return jsonify(servicos.metricas(r["id"]))


@app.get("/api/painel")
def api_painel():
    """Tudo que o painel mostra, numa chamada — a tela não pode piscar em partes."""
    r = servicos.rodada_atual()
    rid = r["id"] if r else None
    itens = servicos.recomendacoes(rid) if rid else []
    pendentes = [i for i in itens if i["situacao"] == "Pendente"
                 and i["recomendacao"] in ("SUBIR", "BAIXAR", "REVISAR")]
    return jsonify({
        "rodada": r,
        "resumo": servicos.resumo_rodada(rid) if rid else {"itens": 0},
        "metricas": servicos.metricas(rid) if rid else {},
        "linha_base": linha_base.comparativo(rid),
        "impacto": acompanhamento.carteira(limite=60),
        "erp": erp.situacao(),
        "analistas": servicos.analistas(),
        "atividade": servicos.atividade_recente(8),
        "pendentes": [{k: i[k] for k in ("id", "sku", "produto", "categoria", "canal",
                                         "recomendacao", "impacto_mes", "valor_em_jogo")}
                      for i in pendentes],
        "camara": (lambda p: {"contagem": p["contagem"], "total": p["total"],
                              "em_aberto": p["em_aberto"], "risco_omissao": p["risco_omissao"],
                              "a_enviar": p["a_enviar"], "politica": p["politica"],
                              "prazo": p["prazo"]})(camara.pauta(rid)) if rid else {},
        "fila": {
            "pendentes": len(pendentes),
            "retidos": sum(1 for i in pendentes if i.get("exige_aprovacao")),
            "valor_em_jogo": round(sum(i["valor_em_jogo"] for i in pendentes), 2),
            "primeiro": pendentes[0]["id"] if pendentes else None,
        },
    })


# ------------------------------------------------------ produtos e pessoas
@app.get("/api/produtos")
def api_produtos():
    return jsonify({"itens": servicos.produtos(request.args.get("busca", ""),
                                               request.args.get("ordem", "sku"))})


@app.get("/api/produtos/<sku>")
def api_produto(sku):
    p = servicos.produto(sku)
    if not p:
        return jsonify({"erro": "Produto não encontrado."}), 404
    return jsonify(p)


@app.get("/api/analistas")
def api_analistas():
    return jsonify({"itens": servicos.analistas()})


@app.get("/api/analistas/<nome>")
def api_analista(nome):
    a = servicos.analista(nome)
    if not a:
        return jsonify({"erro": "Analista não encontrado."}), 404
    return jsonify(a)


# ------------------------------------------------------------------- ERP
@app.get("/api/erp")
def api_erp():
    return jsonify(erp.situacao())


@app.post("/api/erp/sincronizar")
def api_erp_sync():
    d = request.get_json(silent=True) or {}
    return jsonify(erp.sincronizar(rotulo_origem="manual",
                                   usuario=d.get("usuario") or "Sistema"))


@app.post("/api/erp/importar")
def api_erp_importar():
    arq = request.files.get("arquivo")
    if arq is None:
        raise ValueError("Envie o arquivo .xlsx exportado do ERP.")
    import io
    conteudo = io.BytesIO(arq.read())
    return jsonify(erp.sincronizar(conteudo, rotulo_origem="arquivo",
                                   usuario=request.form.get("usuario") or "Sistema",
                                   fonte="Arquivo · %s" % arq.filename))


# ------------------------------------------------------------ exportações
def _csv(nome: str, conteudo: str) -> Response:
    return Response("﻿" + conteudo, mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % nome})


@app.get("/api/exportar/aprovadas")
def api_exportar_aprovadas():
    """O arquivo do ERP. Sai só do que foi homologado — nunca de um parecer solto.

    `novos=1` exclui o que já foi entregue em lote anterior; `registrar=1` marca
    os itens como enviados, que é o que o botão da tela de homologação usa.
    """
    r = _rodada_pedida()
    if not r:
        raise ValueError("Nenhuma rodada para exportar.")
    return _csv(*servicos.exportar_aprovadas(
        r["id"], apenas_novos=request.args.get("novos") == "1",
        usuario=request.args.get("usuario") or "Sistema",
        registrar=request.args.get("registrar") == "1"))


@app.get("/api/exportar/historico")
def api_exportar_historico():
    f = {k: request.args.get(k) for k in ("analista", "decisao", "canal", "de", "ate")}
    return _csv(*servicos.exportar_historico(f))


# ------------------------------------------------------------------- web
@app.get("/")
def home():
    return send_from_directory(WEB, "index.html")


@app.get("/<path:arquivo>")
def estatico(arquivo):
    return send_from_directory(WEB, arquivo)


# ------------------------------------------------------------------ main
def main() -> None:
    p = argparse.ArgumentParser(description="Popular Pet · Precificação Inteligente")
    p.add_argument("--porta", type=int, default=8000)
    p.add_argument("--refazer", action="store_true",
                   help="apaga o banco e recomeça da planilha")
    p.add_argument("--sem-navegador", action="store_true")
    args = p.parse_args()

    print("\nPopular Pet · Precificação Inteligente")
    print("─" * 46)
    if args.refazer:
        recomecar()
    preparar()
    if pipeline.estado()["rodando"]:
        print("  dado novo detectado — pipeline rodando em segundo plano")
        print("  o progresso aparece no painel; a tela já pode ser aberta")

    url = "http://127.0.0.1:%d" % args.porta
    print("─" * 46)
    print("  Aberto em %s" % url)
    print("  Para encerrar: Ctrl+C\n")
    if not args.sem_navegador:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    try:
        app.run(host="127.0.0.1", port=args.porta, debug=False)
    except OSError as e:
        if "Address already in use" in str(e) or getattr(e, "errno", None) in (48, 98):
            print("\nA porta %d já está em uso. Rode com outra porta:" % args.porta)
            print("    python app.py --porta %d\n" % (args.porta + 1))
            sys.exit(1)
        raise


if __name__ == "__main__":
    main()
