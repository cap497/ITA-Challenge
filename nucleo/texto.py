# -*- coding: utf-8 -*-
"""Camada 3 — explicação.

Converte os fatores JÁ CALCULADOS em texto de negócio. Nunca calcula preço.

Por padrão a justificativa é composta de forma determinística: funciona offline,
sem modelo nenhum, e o mesmo dado sempre gera o mesmo texto. Se houver um
servidor de modelo local no ar (LM Studio, Ollama, llama.cpp — ver `nucleo/llm.py`),
o modelo reescreve o parágrafo a partir dos mesmos fatores.

**A reescrita só entra na tela se passar na conferência numérica.** Todo valor em
reais e todo percentual do texto é comparado contra os fatores de origem; um
número que não bate reprova o texto inteiro e a versão determinística assume.
Isso não é desconfiança gratuita: modelo pequeno quantizado troca dígito e
arredonda sozinho, e preço errado na tela de um analista é pior que texto seco.
"""

from __future__ import annotations

import os
import re


def brl(v) -> str:
    if v is None:
        return "—"
    s = "%0.2f" % abs(float(v))
    inteiro, dec = s.split(".")
    grupos = []
    while len(inteiro) > 3:
        grupos.insert(0, inteiro[-3:])
        inteiro = inteiro[:-3]
    grupos.insert(0, inteiro)
    sinal = "−" if float(v) < 0 else ""
    return sinal + "R$ " + ".".join(grupos) + "," + dec


def pct(v, casas=1) -> str:
    if v is None:
        return "—"
    return ("%.*f%%" % (casas, 100 * float(v))).replace(".", ",")


def justificar(rec: dict) -> str:
    """Parágrafo em linguagem de negócio, a partir dos fatores da decisão."""
    p0, ps = rec["preco_atual"], rec["preco_sugerido"]
    mkt, idx = rec.get("preco_mercado"), rec.get("indice_preco")
    partes = []

    if mkt and idx:
        if idx > 1.005:
            partes.append("O preço atual está %s acima da referência de mercado (%s, %d de %d "
                          "concorrentes válidos)." % (pct(idx - 1), brl(mkt),
                                                      rec["n_validos"], rec["n_total"]))
        elif idx < 0.995:
            partes.append("O preço atual está %s abaixo da referência de mercado (%s, %d de %d "
                          "concorrentes válidos)." % (pct(1 - idx), brl(mkt),
                                                      rec["n_validos"], rec["n_total"]))
        else:
            partes.append("O preço atual está alinhado à referência de mercado (%s)." % brl(mkt))
    else:
        partes.append("Não há preço de concorrente válido para este item nesta rodada.")

    folga = rec["margem_atual"] - rec["margem_min"]
    if rec.get("abaixo_do_piso"):
        partes.append("A margem de %s está abaixo do mínimo de %s exigido para o item."
                      % (pct(rec["margem_atual"]), pct(rec["margem_min"])))
    elif folga > 0.03:
        partes.append("A margem é de %s, %s acima do mínimo, o que dá espaço de manobra."
                      % (pct(rec["margem_atual"]), pct(folga)))
    else:
        partes.append("A margem é de %s, perto do mínimo de %s."
                      % (pct(rec["margem_atual"]), pct(rec["margem_min"])))

    cob = rec.get("cobertura") or 0
    if cob < 15:
        partes.append("A cobertura é de %.0f dias, o que desaconselha qualquer redução." % cob)
    elif cob > 90:
        partes.append("A cobertura é de %.0f dias, bem acima do normal — há espaço para "
                      "acelerar o giro." % cob)

    tend = rec.get("tendencia_mes") or 0
    if abs(tend) > 0.03:
        partes.append("As vendas vêm %s cerca de %s ao mês."
                      % ("subindo" if tend > 0 else "caindo", pct(abs(tend))))

    eq = rec.get("equilibrio_volume")
    if rec["recomendacao"] == "SUBIR":
        partes.append("Sugestão: %s, %s acima do preço de hoje. Se o volume não mudar, a margem "
                      "mensal cresce %s%s." % (
                          brl(ps), pct(rec["variacao"]), brl(abs(rec["impacto_mes"])),
                          "; a alta só deixa de compensar se as vendas caírem mais de %s"
                          % pct(abs(eq)) if eq else ""))
    elif rec["recomendacao"] == "BAIXAR":
        alvo = ("girar o estoque parado" if rec.get("motivo_principal") == "estoque parado"
                else "voltar à faixa competitiva do mercado")
        partes.append("Sugestão: %s, %s abaixo do preço de hoje. Isso custa %s de margem por mês "
                      "se o volume não subir — é o que se paga para %s%s." % (
                          brl(ps), pct(abs(rec["variacao"])), brl(abs(rec["impacto_mes"])), alvo,
                          ". Para não perder margem nenhuma, o volume precisaria crescer %s"
                          % pct(eq) if eq else ""))
    elif rec["recomendacao"] == "MANTER":
        partes.append("A variação calculada fica abaixo do limiar de percepção do cliente, "
                      "então alterar o preço geraria trabalho sem efeito.")
    else:
        motivos = rec.get("motivos_revisao") or []
        partes.append("A decisão vai para revisão humana: %s. O preço calculado de %s fica como "
                      "proposta." % ("; ".join(motivos) if motivos else "sinais em conflito",
                                     brl(ps)))

    if rec.get("prob_aceite") is not None and rec["prob_aceite"] < 0.4:
        partes.append("Recomendações parecidas costumam ser recusadas pelo time — vale conferir "
                      "antes de aprovar.")
    return " ".join(partes)


def _valores_permitidos(rec: dict) -> tuple[set, set]:
    """Os números que o texto pode citar: em reais e em porcentagem."""
    reais = set()
    for k in ("preco_atual", "preco_sugerido", "preco_mercado", "preco_mercado_entregue",
              "piso", "custo", "custo_total", "menor_concorrente", "preco_efetivo",
              "frete_medio_concorrentes"):
        v = rec.get(k)
        if isinstance(v, (int, float)):
            reais.add(round(float(v), 2))
    for k in ("impacto_mes", "valor_em_jogo", "receita_mes"):
        v = rec.get(k)
        if isinstance(v, (int, float)):
            reais.add(round(abs(float(v)), 2))
    for c in rec.get("concorrentes") or []:
        for campo in ("preco", "frete", "entregue"):
            if isinstance(c.get(campo), (int, float)):
                reais.add(round(float(c[campo]), 2))

    pcts = set()
    for k in ("variacao", "margem_atual", "margem_min", "margem_alvo", "margem_resultante",
              "margem_liquida", "margem_efetiva", "equilibrio_volume", "tendencia_mes",
              "var_12m", "confianca_dados", "desconto_medio", "indice_preco",
              "indice_entregue", "fator_confianca"):
        v = rec.get(k)
        if isinstance(v, (int, float)):
            pcts.add(round(abs(100 * float(v)), 1))
    # Diferenças entre margens aparecem no texto ("7,0% acima do mínimo") e são
    # legítimas, então entram na lista do que pode ser citado.
    ma, mm, mr = (rec.get("margem_atual"), rec.get("margem_min"), rec.get("margem_resultante"))
    if isinstance(ma, (int, float)) and isinstance(mm, (int, float)):
        pcts.add(round(abs(100 * (ma - mm)), 1))
    if isinstance(mr, (int, float)) and isinstance(mm, (int, float)):
        pcts.add(round(abs(100 * (mr - mm)), 1))
    if isinstance(rec.get("indice_preco"), (int, float)):
        pcts.add(round(abs(100 * (rec["indice_preco"] - 1)), 1))
    return reais, pcts


def _confere_numeros(texto: str, rec: dict) -> tuple[bool, list]:
    """Confere cada número citado. Devolve (passou, lista do que não bateu).

    Tolerância de 1 centavo em reais e de 0,15 ponto percentual: o modelo pode
    arredondar a última casa sem estar mentindo. Mais que isso é invenção.
    """
    reais, pcts = _valores_permitidos(rec)
    falhas = []

    for bruto in re.findall(r"R\$\s*([\d\.]+,\d{2})", texto):
        val = round(float(bruto.replace(".", "").replace(",", ".")), 2)
        if not any(abs(val - p) <= 0.011 for p in reais):
            falhas.append("R$ " + bruto)

    for bruto in re.findall(r"(\d{1,3}(?:,\d{1,2})?)\s*%", texto):
        val = round(float(bruto.replace(",", ".")), 2)
        if not any(abs(val - p) <= 0.15 for p in pcts):
            falhas.append(bruto + "%")

    return (not falhas), falhas


SISTEMA = (
    "Você escreve a justificativa de uma recomendação de preço para um analista de "
    "pricing de varejo de produtos para animais, no Brasil.\n"
    "Regras absolutas:\n"
    "1. Use SOMENTE os números que aparecem nos fatores. Nunca calcule, nunca arredonde, "
    "nunca invente um valor.\n"
    "2. Copie os números exatamente como estão escritos, com vírgula decimal.\n"
    "3. Escreva um único parágrafo em português do Brasil, de 3 a 5 frases.\n"
    "4. Sem listas, sem títulos, sem markdown, sem repetir o nome do produto.\n"
    "5. Quando a recomendação for BAIXAR, diga o que se ganha em troca da margem.\n"
    "6. Responda apenas com o parágrafo, sem introdução."
)


def _fatores_legiveis(rec: dict) -> str:
    """Os fatores já formatados como o texto deve citá-los.

    Entregar o número cru em ponto decimal e esperar que o modelo o converta para
    o formato brasileiro é pedir erro de digitação. Aqui ele recebe a string
    pronta e só precisa copiar.
    """
    L = []

    def _add(rot, valor):
        if valor not in (None, ""):
            L.append("- %s: %s" % (rot, valor))

    _add("recomendação", rec.get("recomendacao"))
    _add("motivo principal", rec.get("motivo_principal"))
    _add("preço atual", brl(rec.get("preco_atual")))
    _add("preço sugerido", brl(rec.get("preco_sugerido")))
    _add("variação", pct(rec.get("variacao")))
    _add("preço de mercado", brl(rec.get("preco_mercado")))
    _add("concorrentes válidos", "%s de %s" % (rec.get("n_validos"), rec.get("n_total")))
    _add("margem atual", pct(rec.get("margem_atual")))
    _add("margem mínima", pct(rec.get("margem_min")))
    _add("margem com o preço sugerido", pct(rec.get("margem_resultante")))
    _add("efeito na margem mensal", brl(abs(rec.get("impacto_mes") or 0)))
    if rec.get("equilibrio_volume") is not None:
        _add("variação de volume no ponto de equilíbrio",
             pct(abs(rec["equilibrio_volume"])))
    _add("cobertura de estoque em dias", "%.0f" % (rec.get("cobertura") or 0))
    if rec.get("retencao") and rec["retencao"] != "livre":
        _add("retenção", rec.get("retencao_motivo"))
    for m in (rec.get("motivos_revisao") or [])[:3]:
        L.append("- observação: %s" % m)
    return "\n".join(L)


SISTEMA_PERGUNTA = (
    "Você é o agente de precificação da Popular Pet, respondendo a um analista na "
    "discussão de um item. Responda em português do Brasil, em no máximo três frases "
    "curtas, direto ao ponto.\n"
    "Regras duras:\n"
    "— Use SOMENTE os números da lista de fatores. Nunca calcule nem estime um número "
    "que não esteja ali.\n"
    "— Se a pergunta pedir algo que os fatores não respondem, diga que o dado não está "
    "na base, em uma frase. Não invente.\n"
    "— Não dê ordem nem diga ao analista o que votar. Ele decide; você informa."
)


def responder(rec: dict, pergunta: str) -> dict:
    """Resposta do agente a uma pergunta do analista, sobre um item.

    Mesma disciplina da justificativa: os fatores são a única fonte de número, e a
    resposta do modelo é conferida antes de aparecer. Reprovada na conferência,
    volta a justificativa determinística — nunca um número inventado.
    """
    if os.environ.get("PP_SEM_LLM"):
        return {"texto": None, "origem": "modelo desligado por PP_SEM_LLM"}

    from nucleo import llm
    saida, diag = llm.conversar(
        SISTEMA_PERGUNTA,
        "Fatores da decisão:\n%s\n\nPergunta do analista: %s" % (_fatores_legiveis(rec),
                                                                 (pergunta or "").strip()),
        max_tokens=220)
    if not saida:
        return {"texto": None, "origem": "sem resposta do servidor local",
                "diagnostico": diag}

    ok, falhas = _confere_numeros(saida, rec)
    if not ok:
        return {"texto": None,
                "origem": "resposta descartada: citou %s" % ", ".join(falhas[:3]),
                "diagnostico": dict(diag, reprovado=True, numeros_errados=falhas)}
    return {"texto": saida,
            "origem": "modelo local %s · números conferidos" % diag.get("modelo", "?"),
            "diagnostico": diag}


def gerar(rec: dict) -> dict:
    """Devolve {'texto', 'origem', 'diagnostico'}.

    A versão determinística é sempre calculada primeiro: ela é a resposta, e o
    modelo é um melhorador opcional. Invertendo a ordem, qualquer problema no
    servidor local deixaria o analista sem justificativa nenhuma.
    """
    padrao = justificar(rec)
    base = {"texto": padrao, "origem": "composta dos fatores da decisão"}

    if os.environ.get("PP_SEM_LLM"):
        return base

    from nucleo import llm
    texto, diag = llm.conversar(SISTEMA, "Fatores da decisão:\n%s\n\nEscreva a "
                                         "justificativa." % _fatores_legiveis(rec))
    if not texto:
        return base

    ok, falhas = _confere_numeros(texto, rec)
    if ok:
        return {"texto": texto,
                "origem": "escrita pelo modelo local %s · números conferidos"
                          % diag.get("modelo", "?"),
                "diagnostico": diag}
    return {"texto": padrao,
            "origem": "composta dos fatores — o texto do modelo citou %s e foi descartado"
                      % ", ".join(falhas[:3]),
            "diagnostico": dict(diag, reprovado=True, numeros_errados=falhas)}
