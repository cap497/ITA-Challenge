# -*- coding: utf-8 -*-
"""Bateria de casos representativos — roda, confere e desenha as telas.

    python casos.py                 # roda tudo e gera HTML + PNG em casos/
    python casos.py --sem-imagem    # só o HTML (não precisa de navegador)
    python casos.py --json          # o resultado cru

O que ela faz: para cada combinação de entrada e saída do catálogo
(`nucleo/casos.py`), procura uma instância de verdade. Procura em três lugares,
nesta ordem, e cada cartão diz de onde veio:

  base         a rodada padrão, com a base da Popular Pet como está
  parâmetro    a mesma base com outro parâmetro do gestor (régua de margem,
               posicionamento, capacidade da rodada) — dado real, decisão outra
  construído   um insumo alterado de propósito, um por vez, para provocar a
               situação que a base não contém (concorrente sem estoque, coleta
               velha, preço destoante, SKU alterado ontem)

Caso construído não é dado inventado solto: é um SKU real com **um** campo
mexido, e o cartão escreve qual. É assim que se testa "como a solução reage
quando não possui informação suficiente" — a pergunta da Semana 4 — sem esperar
que a base fictícia coincidentemente tenha o caso.

A reserva (25% dos SKUs, semente fixa) é preferida na escolha da instância. O
cartão marca `reserva` ou `calibração`.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import timedelta

PASTA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PASTA)

import pandas as pd                                              # noqa: E402

from nucleo import banco, carteira, motor, servicos               # noqa: E402
from nucleo import casos as catalogo                              # noqa: E402

SAIDA = os.path.join(PASTA, "casos")


# ----------------------------------------------------------------- cenários
def _rodar(cfg: motor.Config) -> list[dict]:
    d = motor.decidir(cfg)
    return d.to_dict("records")


def _com_coleta(transforma, cfg: motor.Config) -> list[dict]:
    """Roda o motor com a coleta alterada por `transforma`, sem tocar no banco."""
    original = motor.carregar_coleta
    try:
        motor.carregar_coleta = lambda: transforma(original())
        return _rodar(cfg)
    finally:
        motor.carregar_coleta = original


def _com_cadastro(transforma, cfg: motor.Config) -> list[dict]:
    original = motor.carregar_cadastro
    try:
        motor.carregar_cadastro = lambda: transforma(original())
        return _rodar(cfg)
    finally:
        motor.carregar_cadastro = original


def _sku_qualquer() -> str:
    """Um SKU da reserva, para o caso construído não vir da calibração."""
    with banco.sessao() as con:
        skus = [r["sku"] for r in banco.linhas(con, "SELECT sku FROM produtos ORDER BY sku")]
    res = [s for s in skus if catalogo.grupo(s) == "reserva"]
    return (res or skus)[0]


def _sem_concorrente(sku: str):
    def f(c):
        c = {k: (v.copy() if hasattr(v, "copy") else v) for k, v in c.items()}
        m = c["concorrencia"]["SKU"] == sku
        c["concorrencia"].loc[m, "Disponível"] = "Não"
        return c
    return f


def _coleta_velha(sku: str):
    def f(c):
        c = {k: (v.copy() if hasattr(v, "copy") else v) for k, v in c.items()}
        m = c["concorrencia"]["SKU"] == sku
        c["concorrencia"].loc[m, "Data/hora coleta"] = (
            c["concorrencia"]["Data/hora coleta"].max() - timedelta(hours=72))
        return c
    return f


def _destoante(sku: str):
    def f(c):
        c = {k: (v.copy() if hasattr(v, "copy") else v) for k, v in c.items()}
        idx = c["concorrencia"].index[c["concorrencia"]["SKU"] == sku]
        if len(idx):
            c["concorrencia"].loc[idx[0], "Preço"] = float(
                c["concorrencia"].loc[idx[0], "Preço"]) * 1.35
        return c
    return f


def _alterado_ontem(sku: str):
    def f(d):
        d = d.copy()
        ref = pd.Timestamp("2026-09-09")
        d.loc[d["sku"] == sku, "ultima_alteracao"] = (ref - timedelta(days=1)).isoformat()
        return d
    return f


def cenarios() -> list[dict]:
    sku = _sku_qualquer()
    base = motor.Config()
    return [
        {"chave": "base", "fonte": "base", "rotulo": "Rodada padrão",
         "nota": "base da Popular Pet, parâmetros padrão",
         "itens": _rodar(base), "cfg": base.dict()},

        {"chave": "liquida", "fonte": "parametro", "rotulo": "Régua de margem líquida",
         "nota": "mesmo dado, custo total (impostos, frete e taxa de canal) em vez de "
                 "custo de reposição",
         "itens": _rodar(motor.Config(regua="liquida")),
         "cfg": {"regua": "liquida"}},

        {"chave": "premium", "fonte": "parametro", "rotulo": "Posicionamento 4% acima",
         "nota": "o gestor decide ficar acima do mercado",
         "itens": _rodar(motor.Config(posicionamento=1.04)),
         "cfg": {"posicionamento": 1.04}},

        {"chave": "agressivo", "fonte": "parametro", "rotulo": "Posicionamento 4% abaixo",
         "nota": "o gestor decide ficar abaixo do mercado",
         "itens": _rodar(motor.Config(posicionamento=0.96)),
         "cfg": {"posicionamento": 0.96}},

        {"chave": "sem_concorrente", "fonte": "construido",
         "rotulo": "Concorrência indisponível em %s" % sku,
         "nota": "campo alterado: Disponível = Não em todas as coletas do SKU %s" % sku,
         "itens": _com_coleta(_sem_concorrente(sku), base), "cfg": {}, "foco": sku},

        {"chave": "coleta_velha", "fonte": "construido",
         "rotulo": "Coleta de 72 h em %s" % sku,
         "nota": "campo alterado: Data/hora coleta recuada 72 h no SKU %s" % sku,
         "itens": _com_coleta(_coleta_velha(sku), base), "cfg": {}, "foco": sku},

        {"chave": "destoante", "fonte": "construido",
         "rotulo": "Preço de concorrente 35%% fora em %s" % sku,
         "nota": "campo alterado: Preço de um concorrente do SKU %s multiplicado por 1,35" % sku,
         "itens": _com_coleta(_destoante(sku), base), "cfg": {}, "foco": sku},

        {"chave": "alterado_ontem", "fonte": "construido",
         "rotulo": "Preço alterado ontem em %s" % sku,
         "nota": "campo alterado: última alteração de preço do SKU %s movida para ontem" % sku,
         "itens": _com_cadastro(_alterado_ontem(sku), base), "cfg": {}, "foco": sku},

        {"chave": "capacidade", "fonte": "parametro", "rotulo": "Capacidade da rodada em 5",
         "nota": "mais itens elegíveis do que cabem no dia: o MILP tem de escolher",
         "itens": _rodar(motor.Config(capacidade_rodada=5)),
         "cfg": {"capacidade_rodada": 5}},
    ]


def _acoplados(itens: list[dict], cfg: motor.Config) -> list[list[dict]]:
    """SKUs em que mover um canal sozinho abriria diferença acima da R07.

    É a mesma condição que a seleção de carteira usa para amarrar os dois canais
    numa restrição de igualdade. Reproduzida aqui para o cartão poder mostrar o
    par, que é o que torna a R07 visível.
    """
    por_sku: dict[str, dict] = {}
    for i in itens:
        por_sku.setdefault(i["sku"], {})[i["canal"]] = i
    pares = []
    for _, canais in por_sku.items():
        nomes = [c for c in ("Loja física", "E-commerce") if c in canais]
        if len(nomes) < 2:
            continue
        a, b = canais[nomes[0]], canais[nomes[1]]
        if (carteira._quebra_coerencia(a["preco_sugerido"], b["preco_atual"],
                                       cfg.coerencia_canal)
                or carteira._quebra_coerencia(b["preco_sugerido"], a["preco_atual"],
                                              cfg.coerencia_canal)):
            pares.append([a, b])
    return pares


# ----------------------------------------------------------------- casamento
def bateria() -> dict:
    cens = cenarios()
    por_chave = {c["chave"]: c for c in cens}

    # A seleção de carteira roda em todo cenário: é dela que saem o acoplamento
    # de canal (R07) e a capacidade esgotada, que não existem no item isolado.
    for cen in cens:
        cfg = motor.Config(**cen["cfg"]) if cen["cfg"] else motor.Config()
        sel, diag = carteira.selecionar(pd.DataFrame(cen["itens"]), cfg)
        cen["itens"] = sel.to_dict("records")
        cen["diagnostico"] = diag
        cen["acoplados"] = _acoplados(cen["itens"], cfg)

    base_itens = por_chave["base"]["itens"]
    resultados = []
    for c in catalogo.CATALOGO:
        achado = None

        # E1 e E3 não existem no item isolado: saem da seleção de carteira.
        if c.get("especial") == "canal":
            for cen in cens:
                if cen["acoplados"]:
                    a, b = cen["acoplados"][0]
                    achado = {"id": c["id"], "saida": c["saida"], "titulo": c["titulo"],
                              "entrada": c["entrada"], "espera": c["espera"], "coberto": True,
                              "instancia": a, "acompanhantes": [b],
                              "grupo": catalogo.grupo(a["sku"]),
                              "encontrados": len(cen["acoplados"]),
                              "cenario": cen["chave"], "fonte": cen["fonte"],
                              "rotulo": cen["rotulo"],
                              "nota_cenario": "%s · %d par(es) acoplado(s) nesta rodada"
                                              % (cen["nota"], len(cen["acoplados"]))}
                    break
            resultados.append(achado or dict(c, coberto=False, instancia=None, fonte=None,
                                             cenario=None))
            continue

        if c.get("especial") == "capacidade":
            for cen in cens:
                d = cen.get("diagnostico") or {}
                if d.get("restricao_ativa"):
                    sel = [i for i in cen["itens"] if i.get("selecionado")]
                    fora = d.get("elegiveis", 0) - len(sel)
                    if not sel:
                        continue
                    achado = {"id": c["id"], "saida": c["saida"], "titulo": c["titulo"],
                              "entrada": c["entrada"], "espera": c["espera"], "coberto": True,
                              "instancia": sel[0], "acompanhantes": sel[1:3],
                              "grupo": catalogo.grupo(sel[0]["sku"]),
                              "encontrados": len(sel), "cenario": cen["chave"],
                              "fonte": cen["fonte"], "rotulo": cen["rotulo"],
                              "nota_cenario": "%s · %d elegíveis, %d cabem, %d ficam para "
                                              "a próxima rodada"
                                              % (cen["nota"], d.get("elegiveis", 0),
                                                 len(sel), max(0, fora))}
                    break
            resultados.append(achado or dict(c, coberto=False, instancia=None, fonte=None,
                                             cenario=None))
            continue

        for cen in cens:
            m = catalogo.montar(cen["itens"],
                                cen.get("diagnostico") if cen["chave"] == "capacidade" else None)
            caso = next(x for x in m["casos"] if x["id"] == c["id"])
            if caso["coberto"]:
                # Num cenário construído, só vale a instância do SKU mexido.
                if cen["fonte"] == "construido" and cen.get("foco"):
                    if caso["instancia"]["sku"] != cen["foco"]:
                        alvo = [i for i in cen["itens"]
                                if i["sku"] == cen["foco"] and catalogo._seguro(c["achar"], i)] \
                               if c.get("achar") else []
                        if not alvo:
                            continue
                        caso = dict(caso, instancia=alvo[0],
                                    grupo=catalogo.grupo(alvo[0]["sku"]))
                achado = dict(caso, cenario=cen["chave"], fonte=cen["fonte"],
                              rotulo=cen["rotulo"], nota_cenario=cen["nota"])
                break
        resultados.append(achado or {
            "id": c["id"], "saida": c["saida"], "titulo": c["titulo"],
            "entrada": c["entrada"], "espera": c["espera"], "coberto": False,
            "cenario": None, "fonte": None, "instancia": None,
        })

    cobertos = [r for r in resultados if r["coberto"]]
    return {
        "casos": resultados,
        "total": len(resultados), "cobertos": len(cobertos),
        "por_fonte": {f: sum(1 for r in cobertos if r["fonte"] == f)
                      for f in ("base", "parametro", "construido")},
        "da_reserva": sum(1 for r in cobertos if r.get("grupo") == "reserva"),
        "vazios": [r["id"] for r in resultados if not r["coberto"]],
        "particao": catalogo.particao(base_itens),
        "cenarios": [{"chave": c["chave"], "fonte": c["fonte"], "rotulo": c["rotulo"],
                      "nota": c["nota"], "itens": len(c["itens"]), "cfg": c["cfg"]}
                     for c in cens],
        "diagnostico_capacidade": por_chave["capacidade"].get("diagnostico"),
    }


# -------------------------------------------------------------------- telas
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  background:#F7F5FA;color:#241733;padding:28px}
.folha{max-width:1040px;margin:0 auto}
h1{font-size:21px;font-weight:650;letter-spacing:-.01em;margin-bottom:4px}
.sub{color:#6B5C7B;font-size:13px;margin-bottom:22px}
.resumo{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px}
.chip{background:#fff;border:1px solid #E2D9ED;border-radius:9px;padding:9px 13px}
.chip b{display:block;font-size:19px;font-weight:650;color:#804996}
.chip span{font-size:11px;color:#6B5C7B;text-transform:uppercase;letter-spacing:.05em}
.cartao{background:#fff;border:1px solid #E2D9ED;border-radius:13px;padding:0;
  margin-bottom:16px;overflow:hidden;break-inside:avoid}
.cab{display:flex;align-items:center;gap:11px;padding:13px 17px;
  border-bottom:1px solid #EFE9F5;background:#FBF9FD}
.id{font:600 11px ui-monospace,SFMono-Regular,Menlo,monospace;background:#804996;color:#fff;
  padding:3px 7px;border-radius:5px;letter-spacing:.04em}
.cab h2{font-size:15px;font-weight:600;flex:1}
.tag{font-size:10.5px;font-weight:600;padding:3px 8px;border-radius:20px;
  text-transform:uppercase;letter-spacing:.05em}
.SUBIR{background:#E8F3DC;color:#4C6B16}
.BAIXAR{background:#FBE9DC;color:#8A4A14}
.MANTER{background:#EDEAF2;color:#554266}
.REVISAR{background:#FDF0D4;color:#7A5A0B}
.qualquer{background:#EDEAF2;color:#554266}
.fonte{font-size:10.5px;color:#6B5C7B;border:1px solid #E2D9ED;padding:3px 8px;
  border-radius:20px;background:#fff}
.corpo{padding:15px 17px}
.par{display:grid;grid-template-columns:86px 1fr;gap:5px 13px;margin-bottom:11px}
.par dt{font-size:11px;color:#6B5C7B;text-transform:uppercase;letter-spacing:.05em;
  padding-top:2px}
.par dd{font-size:13px}
table{width:100%;border-collapse:collapse;margin-top:11px;font-size:12.5px}
th{text-align:left;font-size:10.5px;color:#6B5C7B;text-transform:uppercase;
  letter-spacing:.05em;padding:6px 9px;border-bottom:1px solid #E2D9ED;font-weight:600}
td{padding:6px 9px;border-bottom:1px solid #F2EDF7}
td.n{text-align:right;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:0}
.just{background:#F7F3FB;border-left:3px solid #804996;padding:10px 13px;
  border-radius:0 7px 7px 0;font-size:12.5px;margin-top:11px;color:#3F2460}
.regras{display:flex;gap:5px;flex-wrap:wrap;margin-top:9px}
.regra{font:600 10.5px ui-monospace,Menlo,monospace;background:#F1ECF7;color:#5E3A85;
  padding:2px 7px;border-radius:4px}
.vazio{border-style:dashed;opacity:.72}
.aviso{font-size:11.5px;color:#8A4A14;background:#FDF4EC;border:1px solid #F3DFCB;
  padding:9px 13px;border-radius:8px;margin-top:11px}
.rodape{font-size:11.5px;color:#6B5C7B;margin-top:26px;padding-top:15px;
  border-top:1px solid #E2D9ED}
@media print{body{background:#fff;padding:0}.cartao{page-break-inside:avoid}}
"""


def _num(v):
    """None, NaN e texto vazio são a mesma coisa na tela: não há valor."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f          # NaN != NaN


def _brl(v) -> str:
    f = _num(v)
    return "—" if f is None else ("R$ %0.2f" % f).replace(".", ",")


def _pct(v, casas=1) -> str:
    f = _num(v)
    return "—" if f is None else ("%+.*f%%" % (casas, 100 * f)).replace(".", ",")


def _e(s) -> str:
    return html.escape(str(s if s is not None else "—"))


def _cartao(c: dict) -> str:
    i = c.get("instancia")
    if not i:
        return """<div class="cartao vazio"><div class="cab"><span class="id">%s</span>
          <h2>%s</h2><span class="tag %s">%s</span></div><div class="corpo">
          <dl class="par"><dt>Entrada</dt><dd>%s</dd><dt>Esperado</dt><dd>%s</dd></dl>
          <div class="aviso">Nenhuma instância encontrada em nenhum cenário. Caso não
          coberto — declarado como lacuna, não escondido.</div></div></div>""" % (
            _e(c["id"]), _e(c["titulo"]), _e(c["saida"]), _e(c["saida"]),
            _e(c["entrada"]), _e(c["espera"]))

    regras = "".join('<span class="regra">%s</span>' % _e(r) for r in (i.get("regras") or []))
    alertas = "".join("<li>%s</li>" % _e(a) for a in (i.get("alertas_dados") or []))
    motivos = "".join("<li>%s</li>" % _e(m) for m in (i.get("motivos_revisao") or []))

    eq = _num(i.get("equilibrio_volume"))
    linha_eq = ("<tr><td>Ponto de equilíbrio de volume</td><td class='n'>%s</td></tr>"
                % _pct(eq)) if eq is not None else ""

    fonte_txt = {"base": "base da Popular Pet", "parametro": "outro parâmetro",
                 "construido": "construído"}.get(c.get("fonte"), "—")

    return """<div class="cartao"><div class="cab">
      <span class="id">%(id)s</span><h2>%(titulo)s</h2>
      <span class="fonte">%(fonte)s · %(grupo)s</span>
      <span class="tag %(rec)s">%(rec)s</span></div>
      <div class="corpo">
      <dl class="par">
        <dt>Entrada</dt><dd>%(entrada)s</dd>
        <dt>Esperado</dt><dd>%(espera)s</dd>
        <dt>Cenário</dt><dd>%(rotulo)s — %(nota)s</dd>
        <dt>Instância</dt><dd><b>%(sku)s</b> · %(produto)s · %(canal)s ·
            %(categoria)s / %(marca)s · curva %(curva)s</dd>
      </dl>
      <table>
        <tr><th>Fator</th><th style="text-align:right">Valor</th></tr>
        <tr><td>Preço atual</td><td class="n">%(p0)s</td></tr>
        <tr><td>Preço de mercado · índice</td><td class="n">%(pm)s · %(idx)s</td></tr>
        <tr><td>Custo de reposição · custo total</td><td class="n">%(cr)s · %(ct)s</td></tr>
        <tr><td>Piso de margem (R01)</td><td class="n">%(piso)s</td></tr>
        <tr><td>Margem atual → resultante</td><td class="n">%(m0)s → %(m1)s</td></tr>
        <tr><td>Estoque · cobertura</td><td class="n">%(est)s un · %(cob)s dias</td></tr>
        <tr><td>Concorrentes válidos</td><td class="n">%(nv)s de %(nt)s ·
            confiança %(conf)s</td></tr>
        <tr><td><b>Preço sugerido · variação</b></td>
            <td class="n"><b>%(p1)s · %(var)s</b></td></tr>
        <tr><td>Efeito na margem mensal</td><td class="n">%(imp)s</td></tr>
        %(eq)s
        <tr><td>Risco · exige aprovação</td><td class="n">%(risco)s · %(aprov)s</td></tr>
      </table>
      %(regras_bloco)s
      %(just)s
      %(motivos)s
      %(alertas)s
      %(junto)s
      </div></div>""" % {
        "id": _e(c["id"]), "titulo": _e(c["titulo"]),
        "fonte": _e(fonte_txt), "grupo": _e(c.get("grupo") or "—"),
        "rec": _e(i["recomendacao"]),
        "entrada": _e(c["entrada"]), "espera": _e(c["espera"]),
        "rotulo": _e(c.get("rotulo")), "nota": _e(c.get("nota_cenario")),
        "sku": _e(i["sku"]), "produto": _e(i["produto"]), "canal": _e(i["canal"]),
        "categoria": _e(i["categoria"]), "marca": _e(i["marca"]), "curva": _e(i["curva"]),
        "p0": _brl(i["preco_atual"]), "pm": _brl(i.get("preco_mercado")),
        "idx": (("%.3f" % _num(i.get("indice_preco"))).replace(".", ",")
                if _num(i.get("indice_preco")) is not None else "—"),
        "cr": _brl(i.get("custo")), "ct": _brl(i.get("custo_total")),
        "piso": _brl(i.get("piso")),
        "m0": _pct(i.get("margem_atual"), 1), "m1": _pct(i.get("margem_resultante"), 1),
        "est": i.get("estoque"), "cob": ("%.0f" % (i.get("cobertura") or 0)),
        "nv": i.get("n_validos"), "nt": i.get("n_total"),
        "conf": ("%.2f" % (i.get("confianca_dados") or 0)).replace(".", ","),
        "p1": _brl(i["preco_sugerido"]), "var": _pct(i.get("variacao"), 2),
        "imp": _brl(i.get("impacto_mes")), "eq": linha_eq,
        "risco": _e(i.get("nivel_risco")),
        "aprov": "sim" if i.get("exige_aprovacao") else "não",
        "regras_bloco": ('<div class="regras">%s</div>' % regras) if regras else "",
        "just": ('<div class="just">%s</div>' % _e(i.get("justificativa"))
                 if i.get("justificativa") else ""),
        "motivos": ('<div class="aviso"><b>Por que foi para revisão</b><ul>%s</ul></div>'
                    % motivos) if motivos else "",
        "alertas": ('<div class="aviso"><b>Alertas de dado</b><ul>%s</ul></div>'
                    % alertas) if alertas else "",
        "junto": _junto(c.get("acompanhantes") or []),
    }


def _junto(itens: list[dict]) -> str:
    """As linhas que vêm amarradas à instância — o par de canal, o resto da carteira."""
    if not itens:
        return ""
    linhas = "".join(
        """<tr><td>%s · %s</td><td class="n">%s → %s</td><td class="n">%s</td>
           <td>%s</td></tr>""" % (
            _e(i["sku"]), _e(i["canal"]), _brl(i["preco_atual"]),
            _brl(i["preco_sugerido"]), _pct(i.get("variacao"), 2), _e(i["recomendacao"]))
        for i in itens)
    return """<table><tr><th>Vem junto na mesma decisão</th>
      <th style="text-align:right">Preço</th><th style="text-align:right">Variação</th>
      <th>Saída</th></tr>%s</table>""" % linhas


def pagina(b: dict) -> str:
    chips = [
        ("%d de %d" % (b["cobertos"], b["total"]), "combinações cobertas"),
        (str(b["por_fonte"]["base"]), "da base como está"),
        (str(b["por_fonte"]["parametro"]), "com outro parâmetro"),
        (str(b["por_fonte"]["construido"]), "construídas"),
        ("%d" % b["da_reserva"], "instâncias da reserva"),
        ("%d de %d" % (b["particao"]["skus_reserva"], b["particao"]["skus"]),
         "SKUs fora do ajuste"),
    ]
    return """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<title>Casos representativos · Popular Pet</title><style>%s</style></head><body>
<div class="folha">
<h1>Casos representativos</h1>
<div class="sub">Uma instância real da base para cada combinação de entrada e saída do
motor. %d combinações, %d cobertas. A reserva é de %.0f%% dos SKUs, sorteada por
semente fixa (<code>%s</code>) e nunca usada para ajustar as camadas de estimativa.</div>
<div class="resumo">%s</div>
%s
<div class="rodape">
<b>Como ler a origem de cada cartão.</b> <i>base da Popular Pet</i>: a situação existe na
base entregue. <i>outro parâmetro</i>: mesma base, parâmetro de gestor diferente — a
situação é real, a decisão é outra. <i>construído</i>: um campo de um SKU real alterado de
propósito, escrito no cartão, para provocar a situação que a base não contém.<br><br>
<b>Sobre validação fora da amostra.</b> A camada que decide o preço não tem parâmetro
ajustado a dado: é piso de margem, R01–R12 e aritmética. Não existe treino, logo não existe
vazamento — o que ela tem é conformidade, testada tentando violar as regras
(<code>python testar.py</code>). A camada que estima volume e chance de aceite tem
parâmetro ajustado, e é dela que a reserva de %.0f%% dos SKUs é guardada.<br><br>
%s
</div></div></body></html>""" % (
        CSS, b["total"], b["cobertos"], 100 * b["particao"]["fracao"],
        _e(b["particao"]["semente"]),
        "".join('<div class="chip"><b>%s</b><span>%s</span></div>' % (v, k) for v, k in chips),
        "".join(_cartao(c) for c in b["casos"]),
        100 * b["particao"]["fracao"], _e(b["particao"]["nota"]))


def imagens(arquivo: str, ids: list[str]) -> list[str]:
    """Um PNG por cartão, mais a folha inteira.

    Um PNG por cartão porque é assim que cada caso entra como slide de apoio no
    fim da apresentação: a banca pergunta "e quando falta o concorrente?" e o
    slide R1 existe, com SKU, números e justificativa.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  playwright não instalado — só o HTML foi gerado")
        print("  para as imagens: pip install playwright")
        return []
    feitos = []
    pasta = os.path.dirname(arquivo)
    with sync_playwright() as p:
        nav = p.chromium.launch()
        pag = nav.new_page(viewport={"width": 1120, "height": 1000},
                           device_scale_factor=2)
        pag.goto("file://" + arquivo)
        pag.wait_for_timeout(300)

        for n, cid in enumerate(ids):
            el = pag.locator(".cartao").nth(n)
            png = os.path.join(pasta, "caso-%s.png" % cid)
            try:
                el.screenshot(path=png)
                feitos.append(png)
            except Exception as e:                                # noqa: BLE001
                print("  cartão %s não virou imagem: %s" % (cid, e))

        folha = arquivo.replace(".html", ".png")
        pag.screenshot(path=folha, full_page=True)
        feitos.append(folha)
        nav.close()
    return feitos


def main() -> None:
    p = argparse.ArgumentParser(description="Bateria de casos representativos")
    p.add_argument("--json", action="store_true")
    p.add_argument("--sem-imagem", action="store_true")
    a = p.parse_args()

    banco.criar_esquema()
    if not servicos.listar_rodadas():
        from nucleo import erp
        if banco.banco_vazio():
            erp.sincronizar(rotulo_origem="carga inicial", usuario="Sistema")
        servicos.gerar_rodada(motor.Config())

    print("\nCASOS REPRESENTATIVOS")
    print("─" * 46)
    b = bateria()
    for c in b["casos"]:
        i = c.get("instancia")
        print("  %-3s %-8s %-40s %-12s %s" % (
            c["id"], c["saida"], c["titulo"][:40],
            (c.get("fonte") or "—"),
            ("%s · %s → %s" % (i["sku"], i["canal"], i["recomendacao"]) if i
             else "NÃO COBERTO")))
    print("─" * 46)
    print("  %d de %d cobertas · %d da base · %d por parâmetro · %d construídas"
          % (b["cobertos"], b["total"], b["por_fonte"]["base"],
             b["por_fonte"]["parametro"], b["por_fonte"]["construido"]))
    print("  reserva: %d de %d SKUs fora do ajuste (semente %s)"
          % (b["particao"]["skus_reserva"], b["particao"]["skus"], b["particao"]["semente"]))

    os.makedirs(SAIDA, exist_ok=True)
    alvo = os.path.join(SAIDA, "casos.html")
    with open(alvo, "w", encoding="utf-8") as f:
        f.write(pagina(b))
    with open(os.path.join(SAIDA, "casos.json"), "w", encoding="utf-8") as f:
        json.dump(b, f, ensure_ascii=False, indent=2, default=str)
    print("\n  %s" % alvo)
    if not a.sem_imagem:
        feitos = imagens(alvo, [c["id"] for c in b["casos"]])
        print("  %d imagens em %s/" % (len(feitos), SAIDA))
    if a.json:
        print(json.dumps(b, ensure_ascii=False, indent=2, default=str))
    print()


if __name__ == "__main__":
    main()
