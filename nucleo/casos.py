# -*- coding: utf-8 -*-
"""Casos representativos — uma instância real da base para cada combinação
possível de entrada e saída.

Serve a duas exigências diferentes que costumam ser confundidas:

**Demonstrar.** O Desafio pede quatro saídas (SUBIR, BAIXAR, MANTER, REVISAR) e
doze regras. A combinação saída × motivo é o que o sistema de fato decide, e
cada uma precisa aparecer com um SKU de verdade, não com um exemplo inventado.
São os cartões que vão para os slides.

**Validar fora da amostra.** O critério 4.02 da banca pede casos que não foram
usados para ajustar a solução. Aqui a resposta é honesta e tem duas partes:

* a camada que decide o preço **não tem parâmetro ajustado a dado nenhum** —
  é piso de margem, R01–R12 e aritmética. Não existe treino, logo não existe
  vazamento. O que ela tem é conformidade, e isso se testa tentando violar.
* a camada que **estima** (volume e chance de aceite) tem parâmetro ajustado,
  e essa sim precisa de reserva. A divisão é por SKU, com semente fixa, e os
  casos marcados `reserva` nunca entram no ajuste.

A divisão é por SKU e não por linha: SKU com dois canais iria metade para cada
lado e o modelo veria, no treino, quase tudo do item que seria testado.
"""

from __future__ import annotations

import hashlib

RESERVA = 0.25          # fração de SKUs guardada fora do ajuste
SEMENTE = "popularpet-g11"


def grupo(sku: str) -> str:
    """'calibracao' ou 'reserva', estável e reproduzível a partir do SKU."""
    h = hashlib.sha256((SEMENTE + "|" + str(sku)).encode("utf-8")).digest()
    u = int.from_bytes(h[:4], "big") / 0xFFFFFFFF
    return "reserva" if u < RESERVA else "calibracao"


# Cada caso: o que entra, o que tem de sair, e como achar na base.
# `achar` recebe o dict de uma recomendação e devolve True quando serve.
CATALOGO = [
    # ------------------------------------------------------------- SUBIR
    {"id": "S1", "saida": "SUBIR", "titulo": "Preço abaixo do mercado",
     "entrada": "índice de preço < 0,98 · concorrência válida e fresca · margem acima do piso",
     "espera": "SUBIR até o alvo, limitado a +5% (R02)",
     "achar": lambda i: i["recomendacao"] == "SUBIR"
                        and i["motivo_principal"] == "preço abaixo do mercado"},
    {"id": "S2", "saida": "SUBIR", "titulo": "Preço atual abaixo do piso de margem",
     "entrada": "preço praticado viola a margem mínima do SKU/canal",
     "espera": "SUBIR por obrigação da R01, ainda que o mercado esteja mais barato",
     "achar": lambda i: i["recomendacao"] == "SUBIR"
                        and i["motivo_principal"] == "margem abaixo do piso"},
    {"id": "S3", "saida": "SUBIR", "titulo": "Espaço de margem sem estar barato",
     "entrada": "índice dentro da faixa, mas margem alvo distante da praticada",
     "espera": "SUBIR modesto, dentro da banda competitiva",
     "achar": lambda i: i["recomendacao"] == "SUBIR"
                        and i["motivo_principal"] == "espaço de margem"},

    # ------------------------------------------------------------ BAIXAR
    {"id": "B1", "saida": "BAIXAR", "titulo": "Preço acima do mercado",
     "entrada": "índice de preço > 1,02 · concorrência válida · cobertura saudável",
     "espera": "BAIXAR até a faixa, nunca abaixo do piso (R01) nem além de −5% (R02)",
     "achar": lambda i: i["recomendacao"] == "BAIXAR"
                        and i["motivo_principal"] == "preço acima do mercado"},
    {"id": "B2", "saida": "BAIXAR", "titulo": "Estoque parado",
     "entrada": "cobertura acima de 90 dias (R12) com venda em queda",
     "espera": "BAIXAR para girar, com o ponto de equilíbrio explícito",
     "achar": lambda i: i["recomendacao"] == "BAIXAR"
                        and i["motivo_principal"] == "estoque parado"},
    {"id": "B3", "saida": "BAIXAR", "titulo": "Ajuste fino ao mercado",
     "entrada": "índice levemente acima, sem excesso de estoque",
     "espera": "BAIXAR pequeno, acima da banda morta de 2%",
     "achar": lambda i: i["recomendacao"] == "BAIXAR"
                        and i["motivo_principal"] == "ajuste ao mercado"},

    # ------------------------------------------------------------ MANTER
    {"id": "M1", "saida": "MANTER", "titulo": "Já está no ponto",
     "entrada": "índice de preço dentro da faixa competitiva · sem regra acionada",
     "espera": "MANTER, sem gastar atenção do analista",
     "achar": lambda i: i["recomendacao"] == "MANTER" and not i.get("regras")
                        and i.get("indice_preco") is not None
                        and 0.98 <= i["indice_preco"] <= 1.02},
    {"id": "M2", "saida": "MANTER", "titulo": "Variação abaixo do limiar de percepção",
     "entrada": "alvo fora da faixa, mas a distância até ele é menor que a banda morta de 2%",
     "espera": "MANTER: mexer custa confiança do cliente e não muda decisão de compra",
     "achar": lambda i: i["recomendacao"] == "MANTER" and not i.get("regras")
                        and i.get("indice_preco") is not None
                        and not (0.98 <= i["indice_preco"] <= 1.02)},
    {"id": "M3", "saida": "MANTER", "titulo": "Redução bloqueada por estoque curto",
     "entrada": "alvo abaixo do preço atual, mas cobertura < 15 dias (R11)",
     "espera": "MANTER: o estoque acaba antes do ganho de volume se materializar",
     "achar": lambda i: "R11" in (i.get("regras") or []) and i["recomendacao"] == "MANTER"},

    # ----------------------------------------------------------- REVISAR
    {"id": "R1", "saida": "REVISAR", "titulo": "Sem preço de concorrente válido",
     "entrada": "nenhuma coleta aproveitável no SKU/canal (R05)",
     "espera": "REVISAR com confiança zerada — o sistema não opina sem referência",
     "achar": lambda i: "R05" in (i.get("regras") or [])},
    {"id": "R2", "saida": "REVISAR", "titulo": "Coleta de concorrência velha",
     "entrada": "coleta mais recente com mais de 48 h (R06)",
     "espera": "REVISAR: dado velho não move preço automaticamente",
     "achar": lambda i: "R06" in (i.get("regras") or [])},
    {"id": "R3", "saida": "REVISAR", "titulo": "Curva A com variação acima de 3%",
     "entrada": "SKU curva A e |variação| > 3% (R03)",
     "espera": "REVISAR e exigir aprovação de gestor",
     "achar": lambda i: "R03" in (i.get("regras") or [])},
    {"id": "R4", "saida": "REVISAR", "titulo": "Campanha ativa",
     "entrada": "SKU dentro de promoção vigente (R09)",
     "espera": "REVISAR: reprecificar durante campanha quebra a oferta anunciada",
     "achar": lambda i: "R09" in (i.get("regras") or [])},
    {"id": "R5", "saida": "REVISAR", "titulo": "Alterado há menos de 3 dias",
     "entrada": "última alteração de preço dentro da janela da R08",
     "espera": "REVISAR: oscilação rápida destrói a imagem de preço",
     "achar": lambda i: "R08" in (i.get("regras") or [])},
    {"id": "R6", "saida": "REVISAR", "titulo": "Piso de margem contra limite de variação",
     "entrada": "o piso exige mais que +5%, e a R02 só permite 5% (R01 × R02)",
     "espera": "REVISAR: o conjunto viável é vazio e nenhum preço atende às duas regras",
     "achar": lambda i: "R01" in (i.get("regras") or []) and "R02" in (i.get("regras") or [])},
    {"id": "R7", "saida": "REVISAR", "titulo": "Confiança do dado abaixo do mínimo",
     "entrada": "poucos concorrentes válidos ou match fraco · confiança < 0,50",
     "espera": "REVISAR com o motivo e o número de coletas aproveitadas na tela",
     "achar": lambda i: (i.get("confianca_dados") or 1) < 0.50
                        and i["recomendacao"] == "REVISAR"},
    {"id": "R8", "saida": "REVISAR", "titulo": "Anomalia de coleta",
     "entrada": "variação de concorrente acima de 20% em 24 h (R10)",
     "espera": "REVISAR: trata-se como erro de coleta até alguém confirmar",
     "achar": lambda i: "R10" in (i.get("regras") or [])
                        or any("destoante" in str(a).lower()
                               for a in (i.get("alertas_dados") or []))},
    {"id": "R9", "saida": "REVISAR", "titulo": "Produto em descontinuação",
     "entrada": "status do cadastro indica descontinuado",
     "espera": "REVISAR: pede plano de liquidação, não reprecificação de rotina",
     "achar": lambda i: any("descontinua" in m for m in (i.get("motivos_revisao") or []))},

    # -------------------------------------------------------- estruturais
    {"id": "E1", "saida": "qualquer", "titulo": "Coerência entre canais (R07)",
     "entrada": "loja física e e-commerce do mesmo SKU, com alteração em um só canal "
                "abrindo diferença > 5%",
     "espera": "os dois canais entram ou saem juntos da rodada — restrição de igualdade "
               "no MILP, não ajuste posterior",
     "achar": None, "especial": "canal"},
    {"id": "E2", "saida": "qualquer", "titulo": "Exige aprovação de gestor",
     "entrada": "curva A com variação > 3% ou risco Alto",
     "espera": "a decisão fica retida com selo de aprovação e só a gestora libera",
     "achar": lambda i: bool(i.get("exige_aprovacao"))},
    {"id": "E3", "saida": "qualquer", "titulo": "Capacidade da rodada esgotada",
     "entrada": "mais itens elegíveis do que a capacidade de validação do dia",
     "espera": "o MILP escolhe a carteira de maior impacto e o preço-sombra diz quanto "
               "valeria ampliar",
     "achar": None, "especial": "capacidade"},
]


def montar(itens: list[dict], diagnostico: dict | None = None) -> dict:
    """Casa cada caso do catálogo com uma instância real da rodada."""
    por_sku_canal = {}
    for i in itens:
        por_sku_canal.setdefault(i["sku"], []).append(i)

    casos = []
    for c in CATALOGO:
        achados = []
        if c.get("especial") == "canal":
            for sku, linhas in por_sku_canal.items():
                canais = {l["canal"]: l for l in linhas}
                if len(canais) >= 2:
                    precos = [l["preco_sugerido"] for l in linhas]
                    if max(precos) and abs(max(precos) - min(precos)) / max(precos) > 0.0:
                        achados = linhas
                        break
        elif c.get("especial") == "capacidade":
            if diagnostico:
                achados = [i for i in itens if i.get("selecionado")][:3]
        elif c["achar"]:
            achados = [i for i in itens if _seguro(c["achar"], i)]

        # Prefere instância da reserva: é o caso que não ajustou nada.
        reserva = [i for i in achados if grupo(i["sku"]) == "reserva"]
        escolhido = (reserva or achados)
        casos.append({
            "id": c["id"], "saida": c["saida"], "titulo": c["titulo"],
            "entrada": c["entrada"], "espera": c["espera"],
            "encontrados": len(achados),
            "grupo": grupo(escolhido[0]["sku"]) if escolhido else None,
            "instancia": escolhido[0] if escolhido else None,
            "acompanhantes": escolhido[1:3] if len(escolhido) > 1 else [],
            "coberto": bool(escolhido),
        })

    cobertos = [c for c in casos if c["coberto"]]
    return {
        "casos": casos,
        "total": len(casos),
        "cobertos": len(cobertos),
        "vazios": [c["id"] for c in casos if not c["coberto"]],
        "da_reserva": sum(1 for c in cobertos if c["grupo"] == "reserva"),
        "reserva_pct": RESERVA,
        "semente": SEMENTE,
    }


def _seguro(fn, i: dict) -> bool:
    try:
        return bool(fn(i))
    except (KeyError, TypeError):
        return False


def particao(itens: list[dict]) -> dict:
    """Quantos SKUs e linhas em cada grupo — o que a banca confere primeiro."""
    skus = sorted({i["sku"] for i in itens})
    res = [s for s in skus if grupo(s) == "reserva"]
    return {
        "skus": len(skus), "skus_reserva": len(res),
        "linhas": len(itens),
        "linhas_reserva": sum(1 for i in itens if grupo(i["sku"]) == "reserva"),
        "lista_reserva": res,
        "semente": SEMENTE, "fracao": RESERVA,
        "nota": "Divisão por SKU, não por linha: o mesmo SKU tem duas linhas (loja e "
                "e-commerce) e separá-las deixaria o modelo ver no ajuste quase tudo do "
                "item que seria testado.",
    }
