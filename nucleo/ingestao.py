# -*- coding: utf-8 -*-
"""Descoberta de colunas — recebe um CSV cru de ERP e descobre o que é o quê.

O problema real: cada ERP exporta com nome de coluna diferente. `SKU`, `Cod.
Produto`, `ITEM_ID`, `codigo_interno` são a mesma coisa; `Vl. Unit`, `PRECO_VENDA`
e `price` também. Pedir ao cliente que renomeie 30 colunas antes de usar a
plataforma é pedir que ele desista.

Como funciona, e por que não é um modelo de linguagem: a aula de arquitetura do
Sprint foi direta — para dado estruturado e numérico, modelo clássico; usar LLM
aqui é caro, lento e não reprodutível. Então cada coluna vira um vetor de
evidência e o casamento com os papéis é resolvido como **problema de
atribuição**:

    max  Σ score(coluna_i, papel_j) · x_ij
    s.a. cada papel recebe no máximo uma coluna
         cada coluna assume no máximo um papel
         x_ij ∈ {0,1}

Resolvido pelo húngaro (`scipy.optimize.linear_sum_assignment`). A diferença
para o casamento guloso importa: guloso pode entregar "Preço" para `preco_custo`
só porque o nome bate, e aí sobra lixo para `preco_venda`. O ótimo global não
comete esse erro, porque paga o preço da escolha ruim no resto do casamento.

A evidência de cada coluna tem duas partes:

* **nome** — distância entre o nome normalizado e os sinônimos do papel
* **conteúdo** — tipo, faixa, cardinalidade, casas decimais, padrão de texto

Conteúdo é o que salva quando o nome não ajuda: uma coluna chamada `C7` cheia de
`PP-0001` é SKU, e uma coluna de inteiros entre 0 e 5.000 com nome `QTD` é
estoque, não preço.

Nada é aplicado sem confirmação: o resultado volta com confiança por coluna e a
tela pede revisão humana abaixo de 0,55.
"""

from __future__ import annotations

import re
import unicodedata

import numpy as np
import pandas as pd

try:
    from scipy.optimize import linear_sum_assignment
    TEM_SCIPY = True
except ImportError:                                               # pragma: no cover
    TEM_SCIPY = False

LIMIAR_CONFIANCA = 0.55


# Cada papel: sinônimos de nome e o formato esperado do conteúdo.
PAPEIS = {
    "sku": {
        "rotulo": "SKU / código do produto",
        "sinonimos": ["sku", "codigo", "cod produto", "cod item", "item id", "id produto",
                      "referencia", "ean", "codigo interno", "part number", "produto id"],
        "tipo": "texto", "unico": True,
        "padrao": r"^[A-Z]{1,4}[-_ ]?\d{3,}$|^\d{8,14}$",
        "obrigatorio": True,
    },
    "produto": {
        "rotulo": "Nome do produto",
        "sinonimos": ["produto", "descricao", "nome", "item", "descricao produto",
                      "titulo", "description"],
        "tipo": "texto", "unico": False, "texto_longo": True, "obrigatorio": True,
    },
    "categoria": {
        "rotulo": "Categoria",
        "sinonimos": ["categoria", "familia", "grupo", "linha", "departamento",
                      "segmento", "category"],
        "tipo": "texto", "cardinalidade_baixa": True,
    },
    "marca": {
        "rotulo": "Marca",
        "sinonimos": ["marca", "fabricante", "fornecedor", "brand"],
        "tipo": "texto", "cardinalidade_baixa": True,
    },
    "curva": {
        "rotulo": "Curva ABC",
        "sinonimos": ["curva", "curva abc", "abc", "classificacao", "classe"],
        "tipo": "texto", "valores": {"a", "b", "c"},
    },
    "canal": {
        "rotulo": "Canal de venda",
        "sinonimos": ["canal", "loja", "ecommerce", "e-commerce", "marketplace",
                      "channel", "ponto de venda", "pdv"],
        "tipo": "texto", "cardinalidade_baixa": True,
    },
    "preco": {
        "rotulo": "Preço de venda",
        "sinonimos": ["preco", "preco venda", "preco atual", "valor", "valor unitario",
                      "vl unit", "price", "preco praticado", "pvp"],
        "tipo": "numero", "min": 0.01, "decimais": True, "obrigatorio": True,
    },
    "custo": {
        "rotulo": "Custo de reposição",
        "sinonimos": ["custo", "custo reposicao", "custo unitario", "cmv", "cost",
                      "custo compra", "preco custo"],
        "tipo": "numero", "min": 0.01, "decimais": True, "obrigatorio": True,
    },
    "impostos": {
        "rotulo": "Impostos",
        "sinonimos": ["imposto", "impostos", "tributo", "icms", "pis cofins", "tax"],
        "tipo": "numero", "min": 0, "decimais": True,
    },
    "frete": {
        "rotulo": "Frete",
        "sinonimos": ["frete", "entrega", "shipping", "custo frete"],
        "tipo": "numero", "min": 0, "decimais": True,
    },
    "taxa_canal": {
        "rotulo": "Taxa do canal",
        "sinonimos": ["taxa", "taxa canal", "comissao", "comissao marketplace", "fee"],
        "tipo": "numero", "min": 0, "decimais": True,
    },
    "margem_min": {
        "rotulo": "Margem mínima",
        "sinonimos": ["margem minima", "margem min", "margem piso", "min margin"],
        "tipo": "numero", "min": 0, "max": 1.0, "fracao": True,
    },
    "margem_alvo": {
        "rotulo": "Margem alvo",
        "sinonimos": ["margem alvo", "margem meta", "margem objetivo", "target margin"],
        "tipo": "numero", "min": 0, "max": 1.0, "fracao": True,
    },
    "estoque": {
        "rotulo": "Estoque disponível",
        "sinonimos": ["estoque", "quantidade", "qtd", "saldo", "disponivel",
                      "stock", "qty", "saldo estoque"],
        "tipo": "numero", "min": 0, "inteiro": True,
    },
    "cobertura": {
        "rotulo": "Cobertura em dias",
        "sinonimos": ["cobertura", "cobertura dias", "dias estoque", "giro",
                      "days of supply", "dias cobertura"],
        "tipo": "numero", "min": 0, "max": 2000,
    },
    "volume": {
        "rotulo": "Volume vendido",
        "sinonimos": ["volume", "vendas", "qtd vendida", "quantidade vendida",
                      "unidades", "sales", "giro mensal"],
        "tipo": "numero", "min": 0,
    },
    "receita": {
        "rotulo": "Receita",
        "sinonimos": ["receita", "faturamento", "valor vendido", "revenue", "total"],
        "tipo": "numero", "min": 0, "decimais": True,
    },
    "status": {
        "rotulo": "Status do produto",
        "sinonimos": ["status", "situacao", "ativo", "descontinuado", "state"],
        "tipo": "texto", "cardinalidade_baixa": True,
    },
    "data": {
        "rotulo": "Data",
        "sinonimos": ["data", "data hora", "dt", "date", "atualizado em", "competencia"],
        "tipo": "data",
    },
}

OBRIGATORIOS = [k for k, v in PAPEIS.items() if v.get("obrigatorio")]


# ------------------------------------------------------------------ texto
def _normalizar(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    return re.sub(r"\s+", " ", s)


def _trigramas(s: str) -> set:
    s = " %s " % s
    return {s[i:i + 3] for i in range(max(1, len(s) - 2))}


def _similaridade(a: str, b: str) -> float:
    """Jaccard de trigramas, com atalho para igualdade e contenção.

    Trigramas em vez de distância de edição porque o erro típico aqui é
    abreviação e ordem trocada ('Vl. Unit.' x 'valor unitario'), não letra
    errada — e Jaccard de trigramas perdoa os dois.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.86
    ta, tb = _trigramas(a), _trigramas(b)
    inter = len(ta & tb)
    return inter / (len(ta) + len(tb) - inter) if inter else 0.0


def _score_nome(coluna: str, papel: dict) -> float:
    n = _normalizar(coluna)
    return max((_similaridade(n, _normalizar(s)) for s in papel["sinonimos"]), default=0.0)


# --------------------------------------------------------------- conteúdo
def perfil(serie: pd.Series) -> dict:
    """O que dá para saber de uma coluna só olhando os valores."""
    s = serie.dropna()
    n = len(s)
    p = {"n": n, "vazios": int(serie.isna().sum()),
         "unicos": int(s.nunique()) if n else 0,
         "cardinalidade": (s.nunique() / n) if n else 0.0,
         "amostra": [str(v) for v in s.head(3).tolist()]}

    num = pd.to_numeric(s.astype(str).str.replace(r"[R$\s.]", "", regex=True)
                        .str.replace(",", ".", regex=False), errors="coerce")
    frac_num = float(num.notna().mean()) if n else 0.0
    p["numerica"] = frac_num > 0.9
    if p["numerica"] and num.notna().any():
        v = num.dropna()
        p["min"], p["max"] = float(v.min()), float(v.max())
        p["media"] = float(v.mean())
        p["inteira"] = bool((v == v.round()).mean() > 0.98)
        p["decimais"] = not p["inteira"]
        p["fracao01"] = bool(v.min() >= 0 and v.max() <= 1.0)
    else:
        p.update({"min": None, "max": None, "media": None,
                  "inteira": False, "decimais": False, "fracao01": False})

    texto = s.astype(str)
    p["comprimento_medio"] = float(texto.str.len().mean()) if n else 0.0
    p["data"] = False
    if not p["numerica"] and n:
        try:
            d = pd.to_datetime(texto, dayfirst=True, errors="coerce",
                               format="mixed")
            p["data"] = bool(d.notna().mean() > 0.9)
        except Exception:                                         # noqa: BLE001
            p["data"] = False
    p["valores"] = {_normalizar(v) for v in texto.unique()[:12]} if n else set()
    return p


def _score_conteudo(p: dict, papel: dict) -> float:
    """Quanto o formato dos valores combina com o papel. Entre 0 e 1."""
    tipo = papel.get("tipo")
    if tipo == "numero":
        if not p["numerica"]:
            return 0.0
        s = 0.55
        if papel.get("min") is not None and p["min"] is not None and p["min"] >= papel["min"]:
            s += 0.12
        if papel.get("max") is not None and p["max"] is not None and p["max"] <= papel["max"]:
            s += 0.12
        if papel.get("fracao") and p["fracao01"]:
            s += 0.2
        if papel.get("fracao") and not p["fracao01"]:
            s -= 0.35
        if papel.get("inteiro") and p["inteira"]:
            s += 0.15
        if papel.get("decimais") and p["decimais"]:
            s += 0.12
        if papel.get("decimais") and p["inteira"]:
            s -= 0.08
        return float(np.clip(s, 0, 1))

    if tipo == "data":
        return 0.95 if p["data"] else 0.0

    # texto
    if p["numerica"] and not papel.get("padrao"):
        return 0.05
    s = 0.45
    if papel.get("unico") and p["cardinalidade"] > 0.95:
        s += 0.25
    if papel.get("unico") and p["cardinalidade"] < 0.5:
        s -= 0.3
    if papel.get("cardinalidade_baixa") and 0 < p["unicos"] <= 30:
        s += 0.22
    if papel.get("cardinalidade_baixa") and p["cardinalidade"] > 0.6:
        s -= 0.3
    if papel.get("texto_longo") and p["comprimento_medio"] > 12:
        s += 0.2
    if papel.get("texto_longo") and p["comprimento_medio"] < 6:
        s -= 0.25
    if papel.get("valores") and p["valores"]:
        if p["valores"] <= papel["valores"]:
            s += 0.35
    if papel.get("padrao") and p["amostra"]:
        bate = sum(1 for v in p["amostra"] if re.match(papel["padrao"], str(v).strip().upper()))
        s += 0.3 * (bate / len(p["amostra"]))
    return float(np.clip(s, 0, 1))


# -------------------------------------------------------------- atribuição
def descobrir(df: pd.DataFrame, peso_nome: float = 0.55) -> dict:
    """Casa colunas com papéis resolvendo o problema de atribuição."""
    colunas = list(df.columns)
    papeis = list(PAPEIS.keys())
    perfis = {c: perfil(df[c]) for c in colunas}

    nome = np.zeros((len(colunas), len(papeis)))
    conteudo = np.zeros_like(nome)
    for i, c in enumerate(colunas):
        for j, k in enumerate(papeis):
            nome[i, j] = _score_nome(c, PAPEIS[k])
            conteudo[i, j] = _score_conteudo(perfis[c], PAPEIS[k])
    score = peso_nome * nome + (1 - peso_nome) * conteudo

    if TEM_SCIPY:
        li, lj = linear_sum_assignment(-score)
        pares = list(zip(li, lj))
        metodo = "atribuição ótima (húngaro)"
    else:
        # Sem scipy, guloso por score — pior, e a tela diz que é pior.
        pares, usados_i, usados_j = [], set(), set()
        ordem = np.dstack(np.unravel_index(np.argsort(-score, axis=None), score.shape))[0]
        for i, j in ordem:
            if i not in usados_i and j not in usados_j:
                pares.append((i, j))
                usados_i.add(i)
                usados_j.add(j)
        metodo = "casamento guloso — instale scipy para o ótimo"

    achados = []
    for i, j in pares:
        c, k = colunas[i], papeis[j]
        s = float(score[i, j])
        if s < 0.25:
            continue
        achados.append({
            "coluna": c, "papel": k, "rotulo": PAPEIS[k]["rotulo"],
            "confianca": round(s, 3),
            "por_nome": round(float(nome[i, j]), 3),
            "por_conteudo": round(float(conteudo[i, j]), 3),
            "confirmar": s < LIMIAR_CONFIANCA,
            "amostra": perfis[c]["amostra"],
            "perfil": {kk: vv for kk, vv in perfis[c].items() if kk != "valores"},
            "evidencia": _evidencia(c, k, nome[i, j], conteudo[i, j], perfis[c]),
        })
    achados.sort(key=lambda a: -a["confianca"])

    mapeados = {a["papel"] for a in achados}
    ignoradas = [c for c in colunas if c not in {a["coluna"] for a in achados}]
    faltando = [k for k in OBRIGATORIOS if k not in mapeados]

    return {
        "metodo": metodo,
        "colunas_lidas": len(colunas), "linhas": len(df),
        "achados": achados,
        "ignoradas": ignoradas,
        "faltando": [{"papel": k, "rotulo": PAPEIS[k]["rotulo"]} for k in faltando],
        "confirmar": [a for a in achados if a["confirmar"]],
        "pronto": not faltando and not any(a["confirmar"] for a in achados),
        "limiar": LIMIAR_CONFIANCA,
        "peso_nome": peso_nome,
    }


def _evidencia(coluna: str, papel: str, s_nome: float, s_cont: float, p: dict) -> str:
    """Uma frase dizendo por que o sistema acha isso. Sem ela não dá para confirmar."""
    partes = []
    if s_nome > 0.8:
        partes.append("o nome da coluna é praticamente o do campo")
    elif s_nome > 0.45:
        partes.append("o nome lembra o do campo")
    else:
        partes.append("o nome não ajudou")

    if p.get("numerica"):
        partes.append("valores numéricos entre %s e %s"
                      % (_curto(p.get("min")), _curto(p.get("max"))))
        if p.get("inteira"):
            partes.append("sempre inteiros")
    elif p.get("data"):
        partes.append("os valores são datas")
    else:
        if p.get("cardinalidade", 0) > 0.95:
            partes.append("um valor diferente por linha")
        elif p.get("unicos", 0) <= 30:
            partes.append("só %d valores distintos" % p.get("unicos", 0))
        if p.get("amostra"):
            partes.append("exemplo: %s" % p["amostra"][0])
    return " · ".join(partes)


def _curto(v) -> str:
    if v is None:
        return "—"
    return ("%.0f" % v) if abs(v) >= 100 else ("%.2f" % v).replace(".", ",")


def aplicar(df: pd.DataFrame, mapa: dict) -> pd.DataFrame:
    """Renomeia conforme o mapa confirmado pelo analista. Nada é inferido aqui.

    A inferência termina em `descobrir`. Esta função só executa o que foi
    confirmado na tela — é o ponto em que a decisão deixa de ser do sistema e
    passa a ser de uma pessoa, que é exatamente onde o desafio pede que ela
    esteja.
    """
    ren = {c: p for c, p in mapa.items() if p and c in df.columns}
    out = df.rename(columns=ren)[list(ren.values())]
    for col in ("preco", "custo", "impostos", "frete", "taxa_canal", "margem_min",
                "margem_alvo", "estoque", "cobertura", "volume", "receita"):
        if col in out.columns:
            out[col] = pd.to_numeric(
                out[col].astype(str).str.replace(r"[R$\s.]", "", regex=True)
                .str.replace(",", ".", regex=False), errors="coerce")
    return out


def conferir(df: pd.DataFrame) -> list[dict]:
    """Coerência depois do mapeamento — o que nenhum nome de coluna revela."""
    aval = []

    def _add(ok, titulo, detalhe):
        aval.append({"ok": bool(ok), "titulo": titulo, "detalhe": detalhe})

    if "sku" in df.columns:
        dup = int(df["sku"].duplicated().sum())
        if "canal" in df.columns:
            dup = int(df.duplicated(subset=["sku", "canal"]).sum())
            _add(dup == 0, "SKU × canal sem repetição", "%d linhas repetidas" % dup)
        else:
            _add(dup == 0, "SKU sem repetição", "%d SKUs repetidos" % dup)

    if {"preco", "custo"} <= set(df.columns):
        abaixo = int((df["preco"] <= df["custo"]).sum())
        _add(abaixo == 0, "Preço acima do custo em toda linha",
             "%d linhas com preço menor ou igual ao custo" % abaixo)

    if "margem_min" in df.columns:
        fora = int(((df["margem_min"] < 0) | (df["margem_min"] > 1)).sum())
        _add(fora == 0, "Margem mínima entre 0 e 1",
             "%d linhas fora da faixa — talvez esteja em porcentagem" % fora)

    for col, rot in [("preco", "Preço"), ("custo", "Custo"), ("estoque", "Estoque")]:
        if col in df.columns:
            v = int(df[col].isna().sum())
            _add(v == 0, "%s preenchido em toda linha" % rot, "%d vazios" % v)

    if "estoque" in df.columns:
        neg = int((df["estoque"] < 0).sum())
        _add(neg == 0, "Estoque não negativo", "%d linhas negativas" % neg)

    return aval
