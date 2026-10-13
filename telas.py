# -*- coding: utf-8 -*-
"""Telas estáticas — as quatro telas desenhadas com os números reais da base.

    python telas.py            # gera HTML + PNG em telas/
    python telas.py --sem-imagem

Não é a aplicação: é a maquete, para decidir o desenho antes de implementar. Os
números não são ilustrativos — saem do motor rodando sobre a base da Popular Pet
e dos indicadores que a própria empresa declarou na aba Indicadores_Atuais.

    1 · Painel      a tela inicial, sem rolagem, com o botão de Iniciar Análise
    2 · Análise     um item por vez, com a linha de produto em dispersão
    3 · Histórico   séries no tempo e o placar de cada decisão aplicada
    4 · Base        CSV cru do ERP, descoberta de colunas, governança, envelope
"""

from __future__ import annotations

import argparse
import html
import os
import sys

PASTA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PASTA)

from nucleo import (acompanhamento, banco, governanca, ingestao,       # noqa: E402
                    linha_base, motor, series, servicos)

SAIDA = os.path.join(PASTA, "telas")
ROXO = "#804996"
LIMA = "#B4C805"


# ------------------------------------------------------------- formatação
def e(s) -> str:
    return html.escape(str(s if s is not None else "—"))


def _n(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def brl(v, casas=2) -> str:
    f = _n(v)
    if f is None:
        return "—"
    s = ("%0.*f" % (casas, f)).replace(",", "").replace(".", ",")
    inteiro, _, dec = s.partition(",")
    neg = inteiro.startswith("-")
    inteiro = inteiro.lstrip("-")
    grupos = []
    while len(inteiro) > 3:
        grupos.insert(0, inteiro[-3:])
        inteiro = inteiro[:-3]
    grupos.insert(0, inteiro)
    return ("−" if neg else "") + "R$ " + ".".join(grupos) + ("," + dec if dec else "")


def pct(v, casas=1) -> str:
    f = _n(v)
    return "—" if f is None else ("%.*f%%" % (casas, 100 * f)).replace(".", ",")


def pctS(v, casas=1) -> str:
    f = _n(v)
    return "—" if f is None else ("+" if f > 0 else "") + pct(f, casas)


def data_br(s) -> str:
    p = str(s or "")[:10].split("-")
    return "%s/%s/%s" % (p[2], p[1], p[0]) if len(p) == 3 else str(s)


# ---------------------------------------------------------------- gráficos
def faixa(partes, altura=26) -> str:
    total = sum(p["valor"] for p in partes) or 1
    x, blocos = 0.0, []
    for p in partes:
        w = 100 * p["valor"] / total
        blocos.append('<rect x="%.2f%%" y="0" width="%.2f%%" height="%d" rx="3" fill="%s"/>'
                      % (x, max(0.0, w - 0.4), altura, p["cor"]))
        x += w
    leg = "".join('<span><i style="background:%s"></i>%s <b>%d</b></span>'
                  % (p["cor"], e(p["rotulo"]), p["valor"]) for p in partes)
    return ('<svg viewBox="0 0 100 %d" width="100%%" height="%d" preserveAspectRatio="none">'
            '%s</svg><div class="legenda">%s</div>' % (altura, altura, "".join(blocos), leg))


def barras(dados, rotulo=130, formato=brl, base=0.0, marca=None, altura=24) -> str:
    if not dados:
        return '<div class="vazio">Sem dados.</div>'
    L = 560
    teto = max([abs(d["valor"]) for d in dados] + [base + 1e-6])
    faixa_v = (teto - base) or 1
    alt = len(dados) * altura + 6
    barra = L - rotulo - 76

    def x(v):
        return rotulo + ((abs(v) - base) / faixa_v) * barra

    partes = []
    if marca is not None:
        partes.append('<line x1="%.1f" y1="1" x2="%.1f" y2="%d" stroke="%s" stroke-width="2"/>'
                      % (x(marca), x(marca), alt - 5, LIMA))
    for k, d in enumerate(dados):
        w = max(2.0, x(d["valor"]) - rotulo)
        y = k * altura
        partes.append(
            '<text x="0" y="%.1f" font-size="11.5" fill="#6B6071">%s</text>'
            '<rect x="%d" y="%.1f" width="%.1f" height="%d" rx="2" fill="%s"/>'
            '<text x="%.1f" y="%.1f" font-size="11" fill="#211A26">%s</text>'
            % (y + altura / 2 + 4, e(d["rotulo"]), rotulo, y + 5, w, altura - 11,
               d.get("cor", "#9A62AE"), rotulo + w + 7, y + altura / 2 + 4,
               formato(d["valor"])))
    return ('<svg viewBox="0 0 %d %d" width="100%%" height="%d">%s</svg>'
            % (L, alt, alt, "".join(partes)))


def _casas(lo: float, hi: float) -> int:
    """Quantas casas o eixo precisa para os três rótulos não saírem iguais.

    Uma série de R$ 4,29 a R$ 4,33 com zero casas vira 'R$ 4 · R$ 4 · R$ 4' e o
    eixo deixa de informar. A escolha vem da amplitude, não do valor."""
    faixa = abs(hi - lo)
    if faixa >= 50:
        return 0
    return 1 if faixa >= 5 else 2


def serie(linhas, altura=190, marcos=(), formato=brl, legenda=True, bandas=(),
          rotulos_x=2) -> str:
    """Série temporal. `bandas` desenha área sombreada entre dois limites — é o
    que mostra que uma categoria parada na média tem itens andando dentro."""
    pontos = [p for l in linhas for p in l["pontos"]]
    pontos += [{"x": b["x"], "v": v, "rotulo": b.get("rotulo", "")}
               for banda in bandas for b in banda["pontos"] for v in (b["lo"], b["hi"])]
    if len(pontos) < 2:
        return '<div class="vazio">Histórico insuficiente.</div>'
    L, mE, mD, mT, mB = 640, 56, 14, 14, 30
    vs = [p["v"] for p in pontos]
    mn, mx = min(vs), max(vs)
    folga = (mx - mn) * 0.14 or abs(mx) * 0.1 or 1
    lo, hi = mn - folga, mx + folga
    xs = sorted({p["x"] for p in pontos})

    def px(v):
        return mE + ((v - xs[0]) / (xs[-1] - xs[0]) if len(xs) > 1 else .5) * (L - mE - mD)

    def py(v):
        return mT + (1 - (v - lo) / (hi - lo)) * (altura - mT - mB)

    out = []
    casas = _casas(lo, hi)
    for v in (lo, (lo + hi) / 2, hi):
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#E7E1EC" '
                   'stroke-dasharray="2 3"/><text x="%d" y="%.1f" font-size="10" '
                   'text-anchor="end" fill="#9A91A0">%s</text>'
                   % (mE, py(v), L - mD, py(v), mE - 6, py(v) + 3.5, formato(v, casas)))
    for m in marcos:
        out.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s" stroke-width="2"/>'
                   '<text x="%.1f" y="%d" font-size="9.5" fill="#7E9A0A">%s</text>'
                   % (px(m["x"]), mT, px(m["x"]), altura - mB, LIMA,
                      px(m["x"]) + 4, mT + 10, e(m["rotulo"])))
    for banda in bandas:
        cima = " ".join(("L" if i else "M") + "%.1f %.1f" % (px(b["x"]), py(b["hi"]))
                        for i, b in enumerate(banda["pontos"]))
        baixo = " ".join("L%.1f %.1f" % (px(b["x"]), py(b["lo"]))
                         for b in reversed(banda["pontos"]))
        out.append('<path d="%s %s Z" fill="%s" opacity="0.16"/>'
                   % (cima, baixo, banda.get("cor", ROXO)))

    for l in linhas:
        d = " ".join(("L" if i else "M") + "%.1f %.1f" % (px(p["x"]), py(p["v"]))
                     for i, p in enumerate(l["pontos"]))
        out.append('<path d="%s" fill="none" stroke="%s" stroke-width="%s" '
                   'stroke-dasharray="%s" stroke-linejoin="round"/>'
                   % (d, l["cor"], l.get("largura", 2),
                      "5 4" if l.get("tracejada") else ""))
        if len(l["pontos"]) <= 14 and not l.get("sem_pontos"):
            out += ['<circle cx="%.1f" cy="%.1f" r="%s" fill="%s"/>'
                    % (px(p["x"]), py(p["v"]), 2.5 if len(l["pontos"]) > 8 else 3, l["cor"])
                    for p in l["pontos"]]
    # Com doze meses, rotular todos vira sopa de letras; com dois, não dá para
    # situar o meio da série. O passo sai do número pedido.
    base_rot = linhas[0]["pontos"] if linhas else pontos
    passo = max(1, len(base_rot) // max(rotulos_x, 1))
    for k, p in enumerate(base_rot):
        if k % passo and k != len(base_rot) - 1:
            continue
        out.append('<text x="%.1f" y="%d" font-size="9.5" text-anchor="middle" '
                   'fill="#9A91A0">%s</text>' % (px(p["x"]), altura - 10, e(p["rotulo"])))
    visiveis = [l for l in linhas if not l.get("sem_legenda")]
    leg = ('<div class="legenda">%s</div>' % "".join(
        '<span><i style="background:%s"></i>%s</span>' % (l["cor"], e(l["nome"]))
        for l in visiveis)) if legenda and len(visiveis) > 1 else ""
    return ('<svg viewBox="0 0 %d %d" width="100%%" height="%d">%s</svg>%s'
            % (L, altura, altura, "".join(out), leg))


def dispersao(grupos, altura=230, destaque=None, formato=brl, rotulo_x="preço médio") -> str:
    todos = [p for g in grupos for p in g["pontos"]]
    if not todos:
        return '<div class="vazio">Sem pontos para comparar.</div>'
    # A viewBox é estreita de propósito: o SVG é esticado para a largura do
    # cartão, então um viewBox menor aumenta texto e bolinha na mesma
    # proporção. Com 640 de largura as letras saíam em 10 px reais.
    L, mE, mD, mT, mB = 452, 126, 12, 14, 30
    xs = [p["x"] for p in todos]
    mn, mx = min(xs), max(xs)
    folga = (mx - mn) * 0.08 or abs(mx) * 0.1 or 1
    lo, hi = mn - folga, mx + folga

    def px(v):
        return mE + ((v - lo) / (hi - lo)) * (L - mE - mD)

    faixas = altura - mT - mB

    def py(k):
        return mT + ((k + 0.5) / len(grupos) if len(grupos) > 1 else .5) * faixas

    out = []
    casas = _casas(lo, hi)
    for v in (lo, (lo + hi) / 2, hi):
        out.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#E7E1EC" '
                   'stroke-dasharray="2 3"/><text x="%.1f" y="%d" font-size="9.5" '
                   'text-anchor="middle" fill="#9A91A0">%s</text>'
                   % (px(v), mT, px(v), altura - mB, px(v), altura - 13, formato(v, casas)))
    marcas = {}
    for k, g in enumerate(grupos):
        y = py(k)
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#EFEBF3"/>'
                   '<text x="0" y="%.1f" font-size="10" fill="#211A26">%s</text>'
                   % (mE - 8, y, L - mD, y, y + 3.5, e(g["nome"][:26])))
        for p in g["pontos"]:
            forte = destaque and p.get("id") == destaque
            marcas[p["marca"]] = p["cor"]
            out.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s" stroke="%s" '
                       'stroke-width="%s"><title>%s · %s</title></circle>'
                       % (px(p["x"]), y, 7 if forte else 4.6,
                          LIMA if forte else p["cor"], "#5A2F6B" if forte else "#fff",
                          "2" if forte else "1.2", e(p["rotulo"]), formato(p["x"])))
    leg = "".join('<span><i style="background:%s"></i>%s</span>' % (c, e(m))
                  for m, c in sorted(marcas.items()))
    if destaque:
        leg = ('<span><i style="background:%s"></i><b>este item</b></span>' % LIMA) + leg
    return ('<svg viewBox="0 0 %d %d" width="100%%" height="%d">%s'
            '<text x="%d" y="%d" font-size="8.5" text-anchor="end" fill="#9A91A0">%s</text>'
            '</svg><div class="legenda">%s</div>'
            % (L, altura, altura, "".join(out), L - mD, altura - 2, e(rotulo_x), leg))


PALETA = ["#804996", "#B4C805", "#2E86AB", "#D4731F", "#59A14F", "#B8336A",
          "#7B6CA6", "#8A8F00"]


def itens_txt(n: int) -> str:
    return "%d item" % n if n == 1 else "%d itens" % n


def cor_de(nome: str) -> str:
    h = 0
    for c in str(nome):
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    return PALETA[h % len(PALETA)]


# =========================================================== tela 1 · painel
def tela_painel() -> str:
    r = servicos.rodada_atual()
    rid = r["id"]
    res = servicos.resumo_rodada(rid)
    met = servicos.metricas(rid)
    lb = linha_base.comparativo(rid)
    itens = servicos.recomendacoes(rid)
    pend = [i for i in itens if i["situacao"] == "Pendente"
            and i["recomendacao"] in ("SUBIR", "BAIXAR", "REVISAR")]
    pend.sort(key=lambda i: -i["valor_em_jogo"])
    valor = sum(i["valor_em_jogo"] for i in pend)

    chamada = """
    <div class="chamada pa-acao">
      <div class="frase">
        <b>%d decisões esperam você</b>
        <span>Rodada de %s · %d itens triados pelo motor · %d retidos para a gestora</span>
      </div>
      <div class="mini">
        <div><b>%s</b><span>margem em jogo</span></div>
        <div><b>%s</b><span>aceite</span></div>
      </div>
      <button class="btn-grande">Iniciar Análise →</button>
    </div>""" % (len(pend), data_br(r["data"]), res["itens"],
                 sum(1 for i in pend if i.get("exige_aprovacao")),
                 brl(valor, 0), pct(met["taxa_aceite"], 0))

    # Gráfico principal: onde estamos caro e onde estamos barato, por categoria.
    # É a resposta direta às duas primeiras perguntas do Desafio, e é o número
    # que muda todo dia — por isso ele, e não a linha de base, ocupa o destaque.
    idx = met["indice_por_categoria"]
    # O índice (1,026) não diz nada sozinho na tela. A mesma informação lida como
    # "+2,6% acima do mercado" já traz a unidade e dispensa legenda.
    dados_idx = [{"rotulo": "%s · %s" % (c["categoria"], itens_txt(c["n"])),
                  "valor": c["indice"],
                  "cor": ("#D4731F" if c["indice"] > 1.02
                          else "#59A14F" if c["indice"] < 0.98 else "#B6AEBF")}
                 for c in idx]
    minimo = min(c["indice"] for c in idx) if idx else 1.0

    # Efeito na margem, com sinal. Antes eu somava `valor_em_jogo`, que é receita
    # afetada, e rotulava como margem — duas contas diferentes. O que o Desafio
    # pede ("oportunidade de ganho de margem") é esta aqui.
    por_cat: dict[str, list] = {}
    for x in pend:
        a = por_cat.setdefault(x["categoria"], [0.0, 0])
        a[0] += x["impacto_mes"]
        a[1] += 1
    dados_valor = [{"rotulo": "%s · %s" % (c, itens_txt(n)), "valor": v,
                    "cor": "#59A14F" if v > 0 else "#D4731F"}
                   for c, (v, n) in sorted(por_cat.items(),
                                           key=lambda kv: -abs(kv[1][0]))[:6]]
    ganho = sum(v for v, _ in por_cat.values() if v > 0)
    perda = sum(v for v, _ in por_cat.values() if v < 0)

    indice_card = """
    <div class="cartao pa-indice">
      <h3>Onde estamos caro e onde estamos barato</h3>
      <p class="leg">Distância entre o nosso preço e o do mercado, por categoria. Zero é
      estar no mercado; a faixa neutra é de ±2%%.</p>
      %s
      <div class="legenda" style="margin-top:4px">
        <span><i style="background:#D4731F"></i>acima do mercado</span>
        <span><i style="background:#B6AEBF"></i>na faixa</span>
        <span><i style="background:#59A14F"></i>abaixo do mercado</span>
        <span style="margin-left:auto;color:#9A91A0">%s dos %d itens na faixa</span>
      </div>
      <hr class="fina">
      <h3>Efeito na margem se as pendentes forem aprovadas</h3>
      <p class="leg">Quanto a margem bruta mensal muda, por categoria, a volume constante.
      Verde entra; laranja é o que se abre mão para voltar ao mercado ou girar estoque.
      Saldo do dia: <b>+%s</b> contra <b>−%s</b>.</p>
      %s
    </div>""" % (barras(dados_idx, rotulo=160, altura=26,
                        base=min(0.995, minimo - 0.004), marca=1.0,
                        formato=lambda v, c=0: ("%+.1f%%" % (100 * (v - 1))).replace(".", ",")),
                 pct(res["na_faixa"], 0), res["itens"],
                 brl(ganho, 0).replace("R$ ", "R$ "), brl(abs(perda), 0).replace("R$ ", "R$ "),
                 barras(dados_valor, rotulo=160, altura=25,
                        formato=lambda v, c=0: ("+" if v > 0 else "−") + brl(abs(v), 0)))

    pr = res["por_recomendacao"]
    motivos = [{"rotulo": m, "valor": n, "cor": ROXO}
               for m, n in (res.get("retidos") or [])[:5]]
    saidas = """
    <div class="cartao pa-saidas">
      <h3>O que o motor decidiu hoje</h3>
      <p class="leg">%d itens triados. Só %d exigem mudança de preço — o resto está no ponto
      ou precisa de gente.</p>
      %s
      <hr class="fina">
      <h3>O que está retido, e por quê</h3>
      <p class="leg">O motor tem recomendação para todos estes. O que falta é quem assina
      ou o momento certo — %d esperam a gestora, %d estão bloqueados por regra.</p>
      %s
    </div>""" % (res["itens"], pr.get("SUBIR", 0) + pr.get("BAIXAR", 0), faixa([
        {"rotulo": "Manter", "valor": pr.get("MANTER", 0), "cor": "#B6AEBF"},
        {"rotulo": "Revisar", "valor": pr.get("REVISAR", 0), "cor": "#E3B33E"},
        {"rotulo": "Baixar", "valor": pr.get("BAIXAR", 0), "cor": "#D4731F"},
        {"rotulo": "Subir", "valor": pr.get("SUBIR", 0), "cor": "#59A14F"},
    ]), (res.get("por_retencao") or {}).get("aprovacao", 0),
         (res.get("por_retencao") or {}).get("bloqueado", 0),
         barras(motivos, rotulo=150, formato=lambda v, c=0: str(int(v)), altura=22))

    # Linha de base: fica na tela, fora do destaque. Ela serve para medir a
    # adoção — a diferença entre o antes e o depois de ligar a ferramenta. Passado
    # o período de adoção, esses números param de se mexer e a faixa vira rodapé.
    chips = []
    for i in lb["itens"]:
        if i["agora"] is None:
            continue
        cls = "bom" if i["melhorou"] else ("ruim" if i["melhorou"] is False else "")
        unid = "" if i["unidade"] in ("SKUs", "/mês") else ("h" if i["unidade"] == "h/sem" else "%")
        agora = ("%.1f" % i["agora"]).replace(".", ",").replace(",0", "") + unid
        chips.append('<span class="chip"><span class="rot">%s</span>'
                     '<span class="de">%s</span>→<b class="%s">%s</b>'
                     '<span class="mt">meta %s</span></span>'
                     % (e(i["indicador"]), e(i["base_texto"]), cls, agora,
                        e(i["meta_texto"])))
    base_card = """
    <div class="rodape-base pa-base">
      <span class="titulo">Adoção · linha de base declarada pela Popular Pet
        <small>%d de %d metas</small></span>
      <div class="chips">%s</div>
    </div>""" % (lb["metas_atingidas"], lb["metas_possiveis"], "".join(chips))

    top = met["top_valor"][:8]
    linhas_top = "".join(
        '<tr><td><b>%s</b><div class="sku">%s · %s</div></td>'
        '<td>%s</td><td class="r">%s</td><td class="r">%s</td></tr>'
        % (e(t["produto"]), e(t["sku"]), e(t["canal"]),
           '<span class="selo %s">%s</span>' % (e(t["recomendacao"]), e(t["recomendacao"])),
           brl(t["valor_em_jogo"], 0),
           '<span class="selo %s">%s</span>' % (e(t["situacao"]), e(t["situacao"])))
        for t in top)
    valor_card = """
    <div class="cartao pa-valor">
      <h3>Maior valor em jogo</h3>
      <p class="leg">Ordenado por quanto a decisão move de margem no mês.</p>
      <table>%s</table>
    </div>""" % linhas_top

    return ('<main class="painel"><div class="painel-grade">%s%s%s%s%s</div></main>'
            % (chamada, indice_card, saidas, valor_card, base_card))


# ========================================================== tela 2 · análise
def tela_analise() -> str:
    r = servicos.rodada_atual()
    itens = servicos.recomendacoes(r["id"])
    # Rações é a categoria que melhor mostra o ponto: quatro produtos de verdade
    # comparáveis entre si, duas marcas cada, e um intervalo de R$ 4 a R$ 42 que
    # prova por que a média da categoria não serve de referência.
    # Um estratégico retido para a gestora: é o caso que mostra as duas coisas
    # separadas — o motor recomenda um preço, e a retenção diz quem assina.
    i = next((x for x in itens if x.get("estrategico") and x["retencao"] == "aprovacao"
              and x["recomendacao"] in ("SUBIR", "BAIXAR")), None) or itens[0]
    i = servicos.recomendacao(i["id"])
    ctx = acompanhamento.contexto_linha(i["sku"])
    pend = [x for x in itens if x["recomendacao"] in ("SUBIR", "BAIXAR", "REVISAR")]
    total, feito = len(pend), 12

    delta = i["variacao"]
    cls = "desce" if delta < -1e-9 else ("sobe" if delta > 1e-9 else "igual")

    # Navegação. A fila ordenada por valor é uma sugestão de ordem, não uma
    # esteira: o analista chega com assunto próprio ("o fornecedor da ração
    # ligou hoje") e precisa ir direto ao item. Três saídas da ordem padrão:
    # anterior/próximo, busca por SKU ou nome, e a fila aberta como lista.
    vizinhos = [x for x in pend if x["id"] != i["id"]]
    anterior = vizinhos[feito - 1] if feito > 0 and feito - 1 < len(vizinhos) else None
    proximo = vizinhos[feito] if feito < len(vizinhos) else None

    def _mini(x, rot):
        if not x:
            return '<span class="nav vazia">%s</span>' % e(rot)
        return ('<a class="nav" href="#"><span class="r">%s</span>'
                '<b>%s</b><span class="s">%s · %s</span></a>'
                % (e(rot), e(x["produto"]), e(x["sku"]), e(x["canal"])))

    passo = """
    <div class="navbar">
      <div class="busca">
        <input type="text" placeholder="Ir direto a um SKU ou produto…">
      </div>
      <div class="ordena">
        <label>Ordem</label>
        <select>
          <option>Maior valor em jogo</option>
          <option>Maior margem em risco</option>
          <option>Esperando a gestora</option>
          <option>Por categoria</option>
          <option>SKU</option>
        </select>
      </div>
      <button class="btn pequeno">Ver a fila inteira</button>
      <div class="passo">
        <span class="conta"><b>%s</b> de %d na fila</span>
        <span class="barra"><i style="width:%.0f%%"></i></span>
        <span class="valor">%s ainda em jogo</span>
      </div>
      <div class="pular">%s%s</div>
    </div>""" % (itens_txt(feito + 1).replace(" item", "º item").replace(" itens", "º"),
                 total, 100 * feito / max(total, 1),
                 brl(sum(x["valor_em_jogo"] for x in pend[feito:]), 0),
                 _mini(anterior, "◀ anterior"), _mini(proximo, "próximo ▶"))

    cab = """
    <div class="item-cab">
      <div class="quem">
        <h2>%s</h2>
        <div class="meta">%s · %s · %s / %s · curva %s</div>
      </div>
      <span class="selo %s">%s</span>
      <span class="selo %s">risco %s</span>
      %s
    </div>""" % (e(i["produto"]), e(i["sku"]), e(i["canal"]), e(i["categoria"]),
                 e(i["marca"]), e(i["curva"]), e(i["recomendacao"]), e(i["recomendacao"]),
                 e(i["nivel_risco"]), e(i["nivel_risco"]).lower(),
                 '<span class="selo estrategico">★ estratégico</span>'
                 if i.get("estrategico") else "")

    mov = """
    <div class="mov">
      <div class="lado"><span class="rot">preço hoje</span>
        <span class="n num">%s</span></div>
      <span class="seta">→</span>
      <div class="lado"><span class="rot">sugerido</span>
        <span class="n num" style="color:%s">%s</span></div>
      <span class="delta %s">%s</span>
      <div class="lado" style="margin-left:auto;text-align:right">
        <span class="rot">mercado</span><span class="n num"
          style="font-size:19px;color:#6B6071">%s</span></div>
    </div>""" % (brl(i["preco_atual"]), ROXO, brl(i["preco_sugerido"]), cls,
                 pctS(delta, 1), brl(i["preco_mercado"]))

    # A aposta é espelhada: na redução o analista aposta que o volume sobe o
    # bastante; na alta, que ele não cai o bastante. Escrever os dois casos com
    # a mesma frase deixaria a alta parecendo prejuízo.
    if delta < 0:
        aposta = """
        <div class="aposta">A margem mensal cai <b>%s</b> se o volume não mudar. Para não
          perder nada, o volume precisa crescer <b>%s</b>.<br>
          A decisão é essa: <b>%s a menos de preço traz mais de %s de volume neste item?</b>
        </div>""" % (brl(abs(i["impacto_mes"])), pctS(abs(i["equilibrio_volume"]), 1),
                     pct(abs(delta), 1), pctS(abs(i["equilibrio_volume"]), 1))
    else:
        aposta = """
        <div class="aposta">A margem mensal sobe <b>%s</b> se o volume não mudar. A alta só
          deixa de compensar se o volume cair mais de <b>%s</b>.<br>
          A decisão é essa: <b>%s a mais de preço faz este item perder mais de %s de
          volume?</b>
        </div>""" % (brl(abs(i["impacto_mes"])), pct(abs(i["equilibrio_volume"]), 1),
                     pct(abs(delta), 1), pct(abs(i["equilibrio_volume"]), 1))

    fatos = """
    <div class="fatos">
      <div><div class="r">Índice vs mercado</div><div class="v num">%s</div></div>
      <div><div class="r">Margem</div><div class="v num">%s <small>→ %s</small></div></div>
      <div><div class="r">Custo</div><div class="v num">%s <small>total %s</small></div></div>
      <div><div class="r">Piso (R01)</div><div class="v num">%s</div></div>
      <div><div class="r">Cobertura</div><div class="v num">%s d
          <small>%s com o pedido a caminho</small></div></div>
      <div><div class="r">Concorrentes</div><div class="v num">%d <small>de %d · conf. %s</small></div></div>
      <div><div class="r">Desconto habitual</div><div class="v num">%s
          <small>margem efetiva %s</small></div></div>
      <div><div class="r">Volume/mês</div><div class="v num">%s <small>un</small></div></div>
      <div><div class="r">Mercado entregue</div><div class="v num">%s
          <small>frete médio %s</small></div></div>
    </div>""" % (("%.3f" % i["indice_preco"]).replace(".", ","),
                 pct(i["margem_atual"]), pct(i["margem_resultante"]),
                 brl(i["custo"]), brl(i["custo_total"]), brl(i["piso"]),
                 ("%.0f" % i["cobertura"]),
                 ("%.0f d" % (i.get("cobertura_projetada") or i["cobertura"])),
                 i["n_validos"], i["n_total"],
                 ("%.2f" % i["confianca_dados"]).replace(".", ","),
                 pct(i.get("desconto_medio") or 0, 1), pct(i.get("margem_efetiva")),
                 ("%.0f" % i["volume_mes"]), brl(i.get("preco_mercado_entregue")),
                 brl(i.get("frete_medio_concorrentes")))

    ret_a = i.get("retencao") or "livre"
    pode_aprovar = ret_a == "livre"
    nota = ('<input type="text" placeholder="justificativa obrigatória (R04)" '
            'style="flex:1;min-width:230px;height:38px;border:1px solid #E7E1EC;'
            'border-radius:6px;padding:0 11px">') if i.get("exige_justificativa") else ""
    acoes = """
    <div class="acoes">
      <button class="btn aprova"%s>Aprovar %s</button>
      <button class="btn"%s>Outro preço</button>
      <button class="btn">Adiar</button>
      <button class="btn">Rejeitar</button>
      %s
    </div>
    <div class="acoes" style="margin-top:9px">
      %s
      <span style="font-size:11.5px;color:#9A91A0">%s</span>
      <span style="margin-left:auto;font-size:11.5px;color:#9A91A0">
        tempo nesta tela: 00:38 · medido e gravado com a decisão</span>
    </div>""" % (
        "" if pode_aprovar else " disabled", brl(i["preco_sugerido"]),
        "" if pode_aprovar else " disabled",
        ('<button class="btn pequeno ativo" title="R04">★ Estratégico</button>'
         if i.get("estrategico")
         else '<button class="btn pequeno" title="Marcar como estratégico (R04)">'
              '★ Estratégico</button>'),
        nota,
        ("Ana Souza é analista: pode adiar ou rejeitar, mas aprovar este item é da gestora."
         if ret_a == "aprovacao" else
         "Ninguém aplica preço enquanto o bloqueio durar." if ret_a == "bloqueado" else
         "Item livre: o analista decide sozinho."))

    ret = i.get("retencao") or "livre"
    retencao_bloco = ""
    if ret != "livre":
        retencao_bloco = """
        <div class="retido %s">
          <b>%s</b>
          <span>%s</span>
          <small>A recomendação de preço continua valendo: a retenção é sobre quem assina e
          quando, não sobre quanto o item deveria custar.</small>
        </div>""" % (e(ret),
                     "Esperando a gestora" if ret == "aprovacao" else "Bloqueado agora",
                     e(i.get("retencao_motivo")))

    esquerda = """<div class="cartao">%s%s%s<div class="porque">%s</div>%s%s%s</div>""" % (
        cab, mov, retencao_bloco, e(i["justificativa"]), aposta, fatos, acoes)

    # Dispersão: linha de produto por nome, cor por marca, este item destacado.
    por_nome: dict[str, list] = {}
    for p in ctx["categoria"]:
        por_nome.setdefault(p["produto"], []).append(p)
    grupos = []
    for nome, ps in sorted(por_nome.items(), key=lambda kv: -max(x["preco"] for x in kv[1])):
        grupos.append({"nome": nome, "pontos": [
            {"x": p["preco"], "id": p["sku"], "marca": p["marca"], "cor": cor_de(p["marca"]),
             "rotulo": "%s · %s" % (p["produto"], p["marca"])} for p in ps]})

    conc = "".join(
        '<tr><td>%s</td><td class="r">%s</td><td class="r">%s</td><td class="r">%s</td>'
        '<td class="r">%s</td><td class="r">%s</td></tr>'
        % (e(c["nome"]), brl(c["preco"]), brl(c.get("frete")), brl(c.get("entregue")),
           "%.0f h" % c["idade_h"], ("%.2f" % c["peso"]).replace(".", ","))
        for c in (i.get("concorrentes") or []))

    pares = "".join(
        '<tr><td><b>%s</b><div class="sku">%s · %s</div></td><td>%s</td>'
        '<td class="r">%s</td></tr>'
        % (e(p["marca"]), e(p["sku"]), e(p["canal"]), e(p["produto"]), brl(p["preco"]))
        for p in ctx["pares"] if p["canal"] == i["canal"])

    hist = series.serie_item(i["sku"], i["canal"])
    evolucao = ""
    if len(hist["pontos"]) > 2:
        pts = [{"x": n, "v": p["preco"], "rotulo": p["rotulo"]}
               for n, p in enumerate(hist["pontos"]) if p["preco"]]
        # O sugerido entra como ponto à frente da série, tracejado: é proposta,
        # não histórico, e misturar os dois na mesma linha mentiria sobre o que
        # já aconteceu.
        proposta = [{"x": pts[-1]["x"], "v": pts[-1]["v"], "rotulo": "hoje"},
                    {"x": pts[-1]["x"] + 1, "v": i["preco_sugerido"], "rotulo": "sugerido"}]
        mercado = [{"x": p["x"], "v": i["preco_mercado"], "rotulo": ""} for p in pts] \
            if i.get("preco_mercado") else []
        linhas_ev = [{"nome": "preço praticado", "cor": ROXO, "pontos": pts}]
        if mercado:
            linhas_ev.append({"nome": "mercado hoje", "cor": "#B6AEBF",
                              "pontos": mercado, "tracejada": True, "sem_pontos": True})
        linhas_ev.append({"nome": "sugerido", "cor": LIMA, "pontos": proposta,
                          "tracejada": True})
        evolucao = """
        <div class="cartao">
          <h3>Como o preço deste item andou</h3>
          <p class="leg">Preço praticado mês a mês nos últimos 12 meses — o que saiu no
          caixa, não o de tabela. %s no período, com mínimo de %s e máximo de %s.</p>
          %s
        </div>""" % (
            ("Subiu %s" % pctS(hist["variacao_12m"], 1)) if (hist["variacao_12m"] or 0) > 0
            else ("Caiu %s" % pctS(hist["variacao_12m"], 1))
            if (hist["variacao_12m"] or 0) < 0 else "Ficou parado",
            brl(hist["minimo"]), brl(hist["maximo"]),
            serie(linhas_ev, altura=180, formato=brl, rotulos_x=4))

    pares = series.serie_pares(i["sku"], i["canal"])
    vol = series.serie_volume_categoria(i["sku"], i["canal"])

    # Curvas da categoria em base 100, com este item grosso e os pares finos.
    # O que o analista procura aqui é posição relativa no tempo: "a categoria
    # inteira subiu e eu fiquei parado" é uma decisão diferente de "subi sozinho".
    linhas_cat = []
    for x in pares["itens"]:
        pts = [{"x": n, "v": q["indice"], "rotulo": q["rotulo"]}
               for n, q in enumerate(x["pontos"])]
        if x["destaque"]:
            linhas_cat.append({"nome": "%s · %s (este item)" % (x["produto"], x["marca"]),
                               "cor": ROXO, "pontos": pts, "largura": 3})
        elif x["par"]:
            linhas_cat.append({"nome": "%s · %s (mesmo produto)" % (x["produto"], x["marca"]),
                               "cor": LIMA, "pontos": pts, "largura": 2.2})
        else:
            linhas_cat.append({"nome": x["produto"], "cor": "#DED7E4", "pontos": pts,
                               "sem_pontos": True, "largura": 1.1, "sem_legenda": True})
    # Ordem de desenho é ordem de empilhamento no SVG: o item em destaque tem de
    # ser o último, senão as curvas de fundo passam por cima justamente da que
    # interessa — e com preços parecidos elas se sobrepõem quase todas.
    linhas_cat.sort(key=lambda l: (l.get("largura", 2)))

    categoria_card = """
      <div class="cartao">
        <h3>A categoria nos últimos 12 meses</h3>
        <p class="leg">Preço de cada item de %s, em base 100 no primeiro mês — dentro da
        categoria o preço vai de %s a %s, e no eixo em reais as curvas baratas sumiriam.
        Este item em roxo, o mesmo produto na outra marca em lima, o resto da categoria em
        cinza.</p>
        %s
      </div>""" % (e(i["categoria"]),
                   brl(min(p["preco"] for p in ctx["categoria"]), 0),
                   brl(max(p["preco"] for p in ctx["categoria"]), 0),
                   serie(linhas_cat, altura=215, formato=lambda v, c=0: "%.0f" % v,
                         rotulos_x=4))

    volume_card = ""
    if vol.get("pontos"):
        linhas_vol = [
            {"nome": "este item", "cor": ROXO,
             "pontos": [{"x": n, "v": q["indice_item"], "rotulo": q["rotulo"]}
                        for n, q in enumerate(vol["pontos"])]},
            {"nome": "categoria %s" % vol["categoria"], "cor": "#B6AEBF", "tracejada": True,
             "sem_pontos": True,
             "pontos": [{"x": n, "v": q["indice_categoria"], "rotulo": q["rotulo"]}
                        for n, q in enumerate(vol["pontos"])]},
        ]
        volume_card = """
      <div class="cartao">
        <h3>Volume vendido · item contra categoria</h3>
        <p class="leg">Base 100 no primeiro mês. O item fez %s no período e a categoria %s —
        a comparação separa o que é deste produto do que é do mercado inteiro. Queda de
        volume logo depois de uma alta quer dizer uma coisa se a categoria caiu junto, e
        outra bem diferente se só este item caiu.</p>
        %s
      </div>""" % (pctS(vol["variacao_item"], 1), pctS(vol["variacao_categoria"], 1),
                   serie(linhas_vol, altura=185, formato=lambda v, c=0: "%.0f" % v,
                         rotulos_x=4))

    conc = "".join(
        '<tr><td>%s</td><td class="r">%s</td><td class="r">%s</td><td class="r">%s</td>'
        '<td class="r">%s</td><td class="r">%s</td></tr>'
        % (e(c["nome"]), brl(c["preco"]), brl(c.get("frete")), brl(c.get("entregue")),
           "%.0f h" % c["idade_h"], ("%.2f" % c["peso"]).replace(".", ","))
        for c in (i.get("concorrentes") or []))

    direita = """
    <div class="grade">
      %s
      %s
      %s
      <div class="cartao">
        <h3>O mesmo produto na concorrência, hoje</h3>
        <table><thead><tr><th>Concorrente</th><th class="r">Preço</th><th class="r">Frete</th>
          <th class="r">Entregue</th><th class="r">Idade</th><th class="r">Peso</th></tr></thead>
          <tbody>%s</tbody></table>
        <p class="leg" style="margin:9px 0 0">Coleta recente pesa mais (meia-vida de 24 h) e
        preço destoante sai pelo z robusto. <b>Frete fora da curva também pesa menos</b>:
        preço baixo com frete alto não é a alternativa que o cliente escolhe. A referência
        que decide é o preço publicado — o nosso entregue a base não informa, e isso está
        declarado.</p>
      </div>
    </div>""" % (evolucao, categoria_card, volume_card, conc)

    return ('<main><div class="analise">%s<div class="an-grade">%s%s</div></div></main>'
            % (passo, esquerda, direita))


# ======================================================== tela 3 · histórico
def tela_historico() -> str:
    """Duas leituras do passado na mesma tela.

    O agregado em cima: para onde a operação inteira andou nos últimos doze
    meses, categoria por categoria. O item embaixo: a história de um SKU e o
    placar das decisões tomadas nele. Só o item seria míope; só o agregado não
    ajudaria ninguém a decidir nada.
    """
    k = acompanhamento.carteira()
    maior = max(k["itens"], key=lambda y: abs(y["margem_prevista_mes"])) if k["itens"] else None
    p = acompanhamento.placar(maior["decisao_id"]) if maior else None
    pan = series.panorama()

    # ---------------------------------------------------- navegação
    navbar = """
    <div class="navbar">
      <div class="busca">
        <input type="text" placeholder="Ver o histórico de um SKU ou produto…"
          value="%s">
      </div>
      <div class="ordena">
        <label>Período</label>
        <select><option>Últimos 12 meses</option><option>Últimos 6 meses</option>
          <option>Ano corrente</option><option>Tudo</option></select>
      </div>
      <div class="ordena">
        <label>Visão</label>
        <select><option>Agregado + item</option><option>Só o agregado</option>
          <option>Só o item</option></select>
      </div>
      <button class="btn pequeno">Exportar CSV</button>
      <div class="passo">
        <span class="valor">%d decisões registradas · %d aplicações com placar</span>
      </div>
    </div>""" % (e("%s · %s" % (p["produto"], p["canal"])) if p else "",
                 k["aplicacoes"] + 0, k["com_placar"])

    # ---------------------------------------------- agregado: categorias
    cats = pan["categorias"]["categorias"]
    linhas_cat, bandas = [], []
    for c in cats:
        pts = [{"x": n, "v": q["indice"], "rotulo": q["rotulo"]}
               for n, q in enumerate(c["pontos"])]
        linhas_cat.append({"nome": c["categoria"], "cor": cor_de(c["categoria"]),
                           "pontos": pts, "sem_pontos": True})
    maior_amp = max(cats, key=lambda c: c.get("amplitude") or 0) if cats else None
    if maior_amp:
        bandas = [{"cor": cor_de(maior_amp["categoria"]), "pontos": [
            {"x": n, "lo": q["p10"], "hi": q["p90"]}
            for n, q in enumerate(maior_amp["pontos"]) if q["p10"] is not None]}]

    tabela_cat = "".join(
        '<tr><td><span class="ponto" style="background:%s"></span>%s</td>'
        '<td class="r">%s</td><td class="r">%s</td><td class="r">%s</td>'
        '<td class="r">%s</td></tr>'
        % (cor_de(c["categoria"]), e(c["categoria"]), brl(c["preco_inicial"], 2),
           brl(c["preco_final"], 2), pctS(c["variacao"], 1),
           pct(c.get("amplitude"), 1))
        for c in cats)

    agregado = """
    <div class="hist-grade">
      <div class="cartao pl-col">
        <h3>Os últimos 12 meses</h3>
        <p class="leg">Preço praticado, não o de tabela.</p>
        <div class="pl"><span class="r">Receita no período</span>
          <span class="v num">%s</span><span class="o">%s unidades vendidas</span></div>
        <div class="pl"><span class="r">Preço médio da operação</span>
          <span class="v num">%s → %s</span><span class="o">média ponderada por volume</span></div>
        <div class="pl"><span class="r">Desconto médio praticado</span>
          <span class="v num">%s</span><span class="o">já descontado do preço acima</span></div>
        <div class="pl"><span class="r">Quem mais subiu</span>
          <span class="v num">%s</span><span class="o">%s no período</span></div>
        <div class="pl"><span class="r">Maior movimento interno</span>
          <span class="v num">%s</span><span class="o">%s entre o SKU que mais subiu e o
          que mais caiu</span></div>
      </div>
      <div class="cartao">
        <h3>Curva de preço por categoria</h3>
        <p class="leg">Base 100 no primeiro mês, para Rações (R$ 25) e Acessórios (R$ 185)
        caberem no mesmo eixo. A área sombreada é a faixa do 10º ao 90º percentil dos SKUs
        de %s — a categoria está parada na média e os itens dentro dela não estão.</p>
        %s
        <hr class="fina">
        <h3>Alterações de preço por mês</h3>
        <p class="leg">Mais decisões não quer dizer mais alterações — e é por isso que o
        indicador da Popular Pet para este é "monitorar", não "aumentar".</p>
        %s
      </div>
      <div class="cartao">
        <h3>Nível de preço de cada categoria</h3>
        <p class="leg">O que o índice esconde: o preço em reais.</p>
        <table><thead><tr><th>Categoria</th><th class="r">Há 12 meses</th>
          <th class="r">Hoje</th><th class="r">Variação</th>
          <th class="r">Amplitude</th></tr></thead><tbody>%s</tbody></table>
        <p class="leg" style="margin-top:11px">Amplitude é a distância entre o SKU que mais
        subiu e o que mais caiu dentro da categoria. Petiscos andou 4,3%% por dentro com a
        média parada em +0,4%% — é aí que mora decisão de preço, não na média.</p>
      </div>
    </div>""" % (
        brl(pan["resumo"]["receita_12m"], 0),
        ("%d" % pan["resumo"]["volume_12m"]).replace(",", "."),
        brl(pan["resumo"]["preco_medio_inicial"]), brl(pan["resumo"]["preco_medio_final"]),
        pct(pan["resumo"]["desconto_medio"], 1),
        e(pan["resumo"]["categoria_que_mais_subiu"]["categoria"])
        if pan["resumo"]["categoria_que_mais_subiu"] else "—",
        pctS(pan["resumo"]["categoria_que_mais_subiu"]["variacao"], 1)
        if pan["resumo"]["categoria_que_mais_subiu"] else "—",
        e(maior_amp["categoria"]) if maior_amp else "—",
        pct(maior_amp.get("amplitude"), 1) if maior_amp else "—",
        e(maior_amp["categoria"]) if maior_amp else "—",
        serie(linhas_cat, altura=210, formato=lambda v, c=0: "%.0f" % v,
              bandas=bandas, rotulos_x=4),
        barras([{"rotulo": a["rotulo"], "valor": a["total"], "cor": ROXO}
                for a in pan["alteracoes"]], rotulo=70, altura=21,
               formato=lambda v, c=0: str(int(v))),
        tabela_cat)

    # ------------------------------------------------- o item escolhido
    detalhe = ""
    if p:
        antes = [x for x in p["serie"] if x["semana"] < 0]
        base_x = len(antes)
        hist = series.serie_item(p["sku"], p["canal"])
        pts12 = [{"x": n, "v": q["preco"], "rotulo": q["rotulo"]}
                 for n, q in enumerate(hist["pontos"]) if q["preco"]]
        linha_vol = {"nome": "volume semanal", "cor": LIMA, "tracejada": True, "pontos":
                     [{"x": n, "v": x["volume"], "rotulo": data_br(x["data"])}
                      for n, x in enumerate(p["serie"])]}

        def _placar(rot, val, obs="", cls=""):
            return ('<div class="pl %s"><span class="r">%s</span><span class="v num">%s</span>'
                    '%s</div>' % (cls, e(rot), val,
                                  '<span class="o">%s</span>' % e(obs) if obs else ""))

        coluna = "".join([
            _placar("Aplicadas", str(k["aplicacoes"]), "decisões que mudaram preço"),
            _placar("Bateram o equilíbrio", "%d <small>de %d</small>"
                    % (k["pagaram"], k["com_placar"]), "a aposta se pagou"),
            _placar("Margem prevista", brl(k["margem_prevista_mes"], 0),
                    "por mês, volume constante"),
            _placar("Margem realizada", brl(k["margem_realizada_mes"], 0),
                    "por mês, com o volume observado"),
            _placar("Erro da previsão", pct(k["erro_relativo"], 0),
                    "em módulo, sobre a margem prevista",
                    "alerta" if (k["erro_relativo"] or 0) > 0.3 else ""),
        ])

        detalhe = """
        <h3 class="secao">%s · %s</h3>
        <div class="hist-grade">
          <div class="cartao pl-col">
            <h3>Placar de todas as aplicações</h3>
            <p class="leg">%s</p>
            %s
          </div>
          <div class="cartao">
            <h3>Preço praticado nos últimos 12 meses</h3>
            <p class="leg">O histórico longo situa a decisão: um item que subiu o ano
            inteiro e recebe outra alta não é o mesmo caso de um parado há meses.</p>
            %s
            <hr class="fina">
            <h3>Volume semanal em torno da aplicação</h3>
            <p class="leg">A linha vertical é a aplicação de %s em %s.</p>
            %s
          </div>
          <div class="cartao">
            <h3>A aposta e o resultado</h3>
            <table class="pares">
              <tr><td>Preço</td><td>%s → %s (%s)</td></tr>
              <tr><td>Margem unitária</td><td>%s → %s</td></tr>
              <tr><td>Volume semanal</td><td>%s → %s (%s)</td></tr>
              <tr><td>Equilíbrio exigido</td><td>%s</td></tr>
              <tr><td>Margem prevista no mês</td><td>%s</td></tr>
              <tr><td>Margem realizada no mês</td><td>%s</td></tr>
              <tr><td>Veredito</td><td>%s</td></tr>
            </table>
            <p class="leg" style="margin-top:11px">%s</p>
          </div>
        </div>""" % (
            e(p["produto"]), e(p["canal"]),
            ('<span class="selo alerta">depois: projeção sintética marcada</span> '
             'Quando o ERP publicar venda posterior à aplicação, o realizado substitui a '
             'projeção.' if k["sintetico"] else "Depois vindo do ERP."),
            coluna,
            serie([{"nome": "preço", "cor": ROXO, "pontos": pts12}], altura=165,
                  formato=brl, legenda=False, rotulos_x=4),
            e(p["recomendacao"]), data_br(p["aplicada_em"]),
            serie([linha_vol], altura=145, formato=lambda v, c=0: "%.0f" % v,
                  marcos=[{"x": base_x - 0.5, "rotulo": "aplicação"}], legenda=False),
            brl(p["preco_antes"]), brl(p["preco_depois"]), pctS(p["variacao_preco"], 1),
            brl(p["margem_unit_antes"]), brl(p["margem_unit_depois"]),
            "%.0f" % p["volume_semana_antes"], "%.0f" % p["volume_semana_depois"],
            pctS(p["variacao_volume"], 1), pctS(p["equilibrio_volume"], 1),
            brl(p["margem_prevista_mes"]), brl(p["margem_realizada_mes"]),
            ('<span class="selo ok">pagou</span>' if p["pagou"]
             else '<span class="selo nao">não pagou</span>'),
            e(p["aviso"]))

    linhas_ap = "".join(
        '<tr><td>%s</td><td><b>%s</b><div class="sku">%s · %s</div></td>'
        '<td>%s</td><td class="r">%s → %s</td><td class="r">%s</td><td class="r">%s</td>'
        '<td class="c">%s</td></tr>'
        % (data_br(x["aplicada_em"]), e(x["produto"]), e(x["sku"]), e(x["canal"]),
           '<span class="selo %s">%s</span>' % (e(x["recomendacao"]), e(x["recomendacao"])),
           brl(x["preco_antes"]), brl(x["preco_depois"]),
           brl(x["margem_prevista_mes"], 0), brl(x["margem_realizada_mes"], 0),
           ('<span class="selo ok">pagou</span>' if x["pagou"]
            else '<span class="selo nao">não</span>'))
        for x in sorted(k["itens"], key=lambda y: -abs(y["margem_prevista_mes"]))[:10])

    tabela = """
    <div class="cartao" style="margin-top:13px">
      <h3>Todas as aplicações</h3>
      <p class="leg">Clique para trocar o item mostrado acima.</p>
      <div class="tab"><table><thead><tr><th>Quando</th><th>Item</th><th>Saída</th>
        <th class="r">Preço</th><th class="r">Prevista</th><th class="r">Realizada</th>
        <th class="c">Veredito</th></tr></thead><tbody>%s</tbody></table></div>
    </div>""" % linhas_ap

    return "<main>%s%s%s%s</main>" % (navbar, agregado, detalhe, tabela)


# ============================================================= tela 4 · base
CSV_EXEMPLO = """Cod. Produto;DESCRICAO DO ITEM;Familia;Fabricante;Classe;Ponto de Venda;\
Vl. Unit.;Preco Custo;ICMS;Vl Frete;Comissao;Marg Min;Marg Objetivo;Saldo;Dias Estoque;\
Qtd Vendida;Faturamento;Situacao;Dt Atualizacao
PP-0001;Racao Premium Caes Adultos 15kg;Racoes;AmigoPet;A;Loja fisica;4,38;2,95;0,31;0,42;\
0,00;0,22;0,32;1250;45;820;3591,60;Ativo;09/09/2026
PP-0002;Petisco Bifinho Carne 500g;Petiscos;VidaPet;B;E-commerce;14,58;9,10;1,02;1,20;1,46;\
0,20;0,30;430;18;312;4548,96;Ativo;09/09/2026
PP-0003;Shampoo Neutro 1L;Higiene;AmigoPet;C;Marketplace;25,74;16,80;1,80;2,10;3,86;0,18;\
0,28;95;12;141;3629,34;Ativo;09/09/2026
PP-0004;Brinquedo Corda 30cm;Brinquedos;PetFeliz;C;Loja fisica;37,92;24,10;2,65;1,90;0,00;\
0,25;0,35;610;120;88;3336,96;Descontinuado;09/09/2026"""


def tela_base() -> str:
    import io

    import pandas as pd
    df = pd.read_csv(io.StringIO(CSV_EXEMPLO), sep=";")
    desc = ingestao.descobrir(df)
    gov = governanca.situacao()

    env = {}
    caminho = os.path.join(banco.PASTA_DADOS, "envelope.json")
    if os.path.exists(caminho):
        import json
        with open(caminho, encoding="utf-8") as f:
            env = json.load(f)

    mapa = ['<div class="cab">Coluna do arquivo</div><div class="cab"></div>'
            '<div class="cab">Campo da plataforma</div><div class="cab">Confiança</div>']
    for a in desc["achados"][:12]:
        baixa = "baixa" if a["confirmar"] else ""
        mapa.append(
            '<div><span class="de">%s</span></div><div style="color:#9A91A0">→</div>'
            '<div class="campo-alvo"><b>%s</b><span class="ev">%s</span></div>'
            '<div><span class="barra-conf"><i class="%s" style="width:%.0f%%"></i></span>'
            '<span style="font-size:11.5px">%s</span></div>'
            % (e(a["coluna"]), e(a["rotulo"]), e(a["evidencia"]), baixa,
               100 * a["confianca"], ("%.2f" % a["confianca"]).replace(".", ",")))

    ingest = """
    <div class="cartao">
      <h3>Entrada: CSV cru do ERP</h3>
      <p class="leg">Nenhuma coluna precisa ser renomeada antes. O sistema lê o arquivo,
      descobre o que é cada coluna e pede confirmação do que não tem certeza.</p>
      <div class="solta"><b>%s</b><span>%d colunas · %d linhas · delimitador detectado</span></div>
      <p class="leg" style="margin:13px 0 7px"><b>%d de %d colunas reconhecidas</b> ·
      %s · nenhum campo obrigatório ficou faltando</p>
      <div class="mapa">%s</div>
      <p class="leg" style="margin-top:11px">A descoberta não é um modelo de linguagem: cada
      coluna vira evidência de nome e de conteúdo, e o casamento coluna↔campo é resolvido
      como problema de atribuição. Abaixo de %s de confiança, a tela exige confirmação
      humana antes de aplicar.</p>
    </div>""" % ("base_setembro_erp.csv", desc["colunas_lidas"], desc["linhas"],
                 len(desc["achados"]), desc["colunas_lidas"], e(desc["metodo"]),
                 "".join(mapa), ("%.2f" % desc["limiar"]).replace(".", ","))

    checks = "".join(
        '<li class="%s">%s<small>%s</small></li>'
        % ("ok" if t["ok"] else ("nao" if t["ok"] is False else "na"),
           e(t["garantia"]), e(t["detalhe"]))
        for t in gov["testes"])
    govern = """
    <div class="cartao">
      <h3>Governança · %d de %d garantias verificadas agora</h3>
      <p class="leg">Não é declaração: a tela tenta apagar e alterar uma decisão a cada
      carregamento, e mostra o banco recusando.</p>
      <ul class="checklist">%s</ul>
      <hr class="fina">
      <table class="pares">
        <tr><td>Dado pessoal na plataforma</td><td>só o nome de quem decide</td></tr>
        <tr><td>O que sai da máquina</td><td>nada, por padrão</td></tr>
        <tr><td>Decisões registradas</td><td>%d</td></tr>
        <tr><td>Itens retidos para a gestora</td><td>%d</td></tr>
        <tr><td>Recomendações redecididas</td><td>%d</td></tr>
      </table>
    </div>""" % (gov["testes_ok"], gov["testes_total"], checks, gov["decisoes"],
                 gov["retidos_para_gestora"], gov["recomendacoes_redecididas"])

    lim = "".join('<tr><td>%s<small style="display:block;color:#9A91A0">%s</small></td>'
                  '<td>%s</td></tr>' % (e(l["limite"]), e(l["base"]), e(l["valor"]))
                  for l in (env.get("limites") or []))
    deg = "".join('<li class="%s">sem %s<small>%s</small></li>'
                  % ("nao" if d["para"] else "ok", e(d["falta"]), e(d["efeito"]))
                  for d in (env.get("degradacao") or [])[:5])
    envelope = """
    <div class="cartao">
      <h3>Envelope operacional · medido</h3>
      <p class="leg">Onde funciona e onde para. Medido nesta máquina em %s, não estimado.</p>
      <table class="pares">%s</table>
      <hr class="fina">
      <p class="leg">O que acontece quando falta cada peça</p>
      <ul class="checklist">%s</ul>
    </div>""" % (e(str(env.get("medido_em", "—"))[:16].replace("T", " ")), lim, deg)

    qual = "".join(
        '<li class="%s">%s<small>%s</small></li>'
        % ("ok" if c["ok"] else "nao", e(c["titulo"]), e(c["detalhe"]))
        for c in ingestao.conferir(ingestao.aplicar(
            df, {a["coluna"]: a["papel"] for a in desc["achados"]})))
    qualidade = """
    <div class="cartao">
      <h3>Coerência depois do mapeamento</h3>
      <p class="leg">O que nenhum nome de coluna revela: preço abaixo do custo, margem fora
      da faixa, SKU repetido.</p>
      <ul class="checklist">%s</ul>
    </div>""" % qual

    estrat = servicos.estrategicos()
    por_sku: dict[str, dict] = {}
    for x in estrat:
        por_sku[x["sku"]] = x
    linhas_e = "".join(
        '<tr><td><b>%s</b><div class="sku">%s · %s / %s</div></td>'
        '<td>%s</td><td class="r">%s</td></tr>'
        % (e(x["produto"]), e(x["sku"]), e(x["categoria"]), e(x["marca"]),
           e(x["estrategico_nota"]), e((x["estrategico_por"] or "") + " · "
                                       + data_br(x["estrategico_em"])))
        for x in estrat)
    estrategicos_card = """
    <div class="cartao">
      <h3>Produtos estratégicos · R04</h3>
      <p class="leg">A única marcação que não vem do ERP: quais itens sustentam a imagem de
      preço da loja é decisão comercial. Marcar exige dizer por quê, e fica registrado quem
      marcou e quando.</p>
      <table><thead><tr><th>Produto</th><th>Por quê</th>
        <th class="r">Quem marcou</th></tr></thead><tbody>%s</tbody></table>
      <hr class="fina">
      <p class="leg" style="margin:0"><b>O que a marcação faz.</b> É roteamento, não cálculo —
      a regra é da própria Popular Pet: <i>"produtos estratégicos nunca são aplicados
      automaticamente"</i>. O preço sugerido sai igual ao de qualquer item; o que muda é que
      a recomendação vira REVISAR e só a gestora libera. Não existe desconto ou acréscimo
      automático por ser estratégico: inventar isso seria criar regra que a empresa não pediu.
      Há um parâmetro opcional, desligado por padrão, para apertar a banda de variação desses
      itens — se a Popular Pet quiser, vira uma linha de configuração, não de código.</p>
    </div>""" % linhas_e

    return ('<main><div class="an-grade">%s<div class="grade">%s%s</div></div>'
            '<div class="an-grade" style="margin-top:13px">%s%s</div></main>'
            % (ingest, qualidade, govern, estrategicos_card, envelope))


# ================================================================= geração
def pagina(titulo: str, ativa: str, corpo: str) -> str:
    abas = "".join('<a href="tela-%s.html" class="%s">%s</a>'
                   % (p, "ativa" if p == ativa else "", t)
                   for p, t in [("painel", "Painel"), ("analise", "Análise"),
                                ("historico", "Histórico"), ("base", "Base")])
    return """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<title>%s · Popular Pet</title><link rel="stylesheet" href="estilo.css"></head><body>
<header class="topo">
  <div style="width:28px;height:28px;border-radius:6px;background:%s;color:%s;
    display:flex;align-items:center;justify-content:center;font-weight:800;font-size:13px">PP</div>
  <h1>Precificação Inteligente</h1>
  <nav>%s</nav>
  <div class="dir"><select><option>Ana Souza · Analista</option>
    <option>Carla Menezes · Gestora</option></select></div>
</header>%s</body></html>""" % (e(titulo), LIMA, ROXO, abas, corpo)


TELAS = [("painel", "Painel", tela_painel), ("analise", "Análise", tela_analise),
         ("historico", "Histórico", tela_historico), ("base", "Base", tela_base)]

ALTURAS = {"painel": 860, "analise": 900, "historico": 900, "base": 900}


def imagens(arquivos: list[tuple[str, str]]) -> list[str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  playwright não instalado — só o HTML foi gerado")
        return []
    feitos = []
    with sync_playwright() as p:
        nav = p.chromium.launch()
        for chave, caminho in arquivos:
            pag = nav.new_page(viewport={"width": 1440, "height": ALTURAS[chave]},
                               device_scale_factor=2)
            pag.goto("file://" + caminho)
            pag.wait_for_timeout(300)
            png = caminho.replace(".html", ".png")
            # O painel é a única tela que promete caber sem rolagem: ela é
            # capturada no tamanho da janela, de propósito, para a promessa
            # poder ser conferida na imagem.
            pag.screenshot(path=png, full_page=(chave != "painel"))
            feitos.append(png)
            pag.close()
        nav.close()
    return feitos


def main() -> None:
    ap = argparse.ArgumentParser(description="Maquetes das quatro telas")
    ap.add_argument("--sem-imagem", action="store_true")
    a = ap.parse_args()

    banco.criar_esquema()
    if not servicos.listar_rodadas():
        from nucleo import erp, semente
        if banco.banco_vazio():
            erp.sincronizar(rotulo_origem="carga inicial", usuario="Sistema")
        r = servicos.gerar_rodada(motor.Config())
        semente.semear(servicos.rodada_atual()["id"])

    os.makedirs(SAIDA, exist_ok=True)
    import shutil
    shutil.copy(os.path.join(PASTA, "web", "estilo.css"), os.path.join(SAIDA, "estilo.css"))

    print("\nTELAS ESTÁTICAS")
    print("─" * 44)
    arquivos = []
    for chave, titulo, fn in TELAS:
        caminho = os.path.join(SAIDA, "tela-%s.html" % chave)
        with open(caminho, "w", encoding="utf-8") as f:
            f.write(pagina(titulo, chave, fn()))
        arquivos.append((chave, caminho))
        print("  %-11s %s" % (titulo, caminho))

    if not a.sem_imagem:
        for png in imagens(arquivos):
            print("  imagem    %s" % png)
    print()


if __name__ == "__main__":
    main()
