# -*- coding: utf-8 -*-
"""Motor de decisão — camada 2 da arquitetura.

Determinístico e auditável: o mesmo dado sempre produz a mesma recomendação, e
nenhuma sugestão pode violar uma regra dura. Nada aqui usa modelo de linguagem.

Cadastro, preço, custo e estoque vêm do banco (alimentado pelo ERP). Vendas e
coleta de concorrência vêm da planilha, que é onde o coletor publica.

Fluxo: referência de mercado → sinais → preço alvo → conjunto viável (R01–R12)
→ decisão → risco → impacto.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from nucleo.banco import PASTA_DADOS, sessao

PLANILHA = os.path.join(PASTA_DADOS, "base_popular_pet.xlsx")

PESO_MATCH = {"EAN exato": 1.0, "Correspondência alta": 0.6,
              "Correspondência média": 0.3, "Descrição similar": 0.3}


@dataclass
class Config:
    """Parâmetros que o gestor controla na tela. Mudá-los gera outra rodada."""
    regua: str = "simples"             # 'simples' = custo de reposição · 'liquida' = custo total
    variacao_max: float = 0.05         # R02
    banda_morta: float = 0.02          # abaixo do limiar de percepção, não vale alterar
    banda_competitiva: float = 0.05    # faixa considerada alinhada ao mercado
    coerencia_canal: float = 0.05      # R07
    cobertura_min_dias: int = 15       # R11
    cobertura_excesso_dias: int = 90   # R12
    meia_vida_coleta_h: float = 24.0
    idade_max_coleta_h: float = 48.0   # R06
    dias_min_entre_alteracoes: int = 3  # R08
    curva_a_limite: float = 0.03       # R03
    capacidade_rodada: int = 20        # quantas decisões cabem no dia
    posicionamento: float = 1.00       # alvo em relação ao mercado
    confianca_minima: float = 0.50     # abaixo disso vai para revisão
    bloqueados: list = field(default_factory=list)   # "SKU|Canal" travados pelo analista

    def dict(self) -> dict:
        return dict(self.__dict__)


# --------------------------------------------------------------------- leitura
def _aba(nome: str) -> pd.DataFrame:
    return pd.read_excel(PLANILHA, sheet_name=nome, header=2).dropna(how="all")


def carregar_coleta() -> dict:
    """Dados que não vêm do ERP: vendas, concorrência e promoções."""
    conc = _aba("Concorrencia")
    conc["Data/hora coleta"] = pd.to_datetime(conc["Data/hora coleta"], dayfirst=True,
                                              errors="coerce")
    promo = _aba("Promocoes")
    for c in ("Início", "Fim"):
        if c in promo.columns:
            promo[c] = pd.to_datetime(promo[c], dayfirst=True, errors="coerce")
    return {"vendas": _aba("Vendas_12m"), "concorrencia": conc, "promocoes": promo}


def carregar_cadastro() -> pd.DataFrame:
    """Cadastro, preço, custo e estoque, direto do banco."""
    with sessao() as con:
        df = pd.read_sql_query("""
            SELECT p.sku, p.nome AS produto, p.categoria, p.marca, p.curva, p.status,
                   COALESCE(p.estrategico, 0) AS estrategico,
                   e.canal, e.preco AS preco_atual, e.custo_rep, e.impostos, e.frete,
                   e.taxa_canal, e.custo_total, e.margem_min, e.margem_alvo,
                   s.quantidade AS estoque, s.cobertura_erp,
                   COALESCE(s.pedido_aberto, 0) AS pedido_aberto,
                   s.previsao AS previsao_recebimento
            FROM produtos p
            JOIN estado_canal e ON e.sku = p.sku
            LEFT JOIN estoque s ON s.sku = p.sku
            ORDER BY p.sku, e.canal
        """, con)
        # Só alteração de verdade conta para a R08; o registro inicial da carga não é mudança.
        ult = pd.read_sql_query("""
            SELECT sku, MAX(data) AS ultima_alteracao
            FROM historico_preco
            WHERE tipo IN ('AUMENTO', 'REDUCAO')
            GROUP BY sku
        """, con)
    return df.merge(ult, on="sku", how="left")


def data_referencia(coleta: dict) -> pd.Timestamp:
    """Momento da rodada: a coleta de concorrência mais recente."""
    return coleta["concorrencia"]["Data/hora coleta"].max()


# ----------------------------------------------------------------- mercado
def _referencia_ponderada(valores: np.ndarray, pesos: np.ndarray) -> float:
    """Média ponderada dos concorrentes válidos.

    A mediana ponderada seria mais robusta a preço destoante, mas com três
    coletas ela degenera: quando um concorrente concentra mais da metade do
    peso, a mediana vira o preço dele e os outros dois são ignorados. Como o
    descarte de destoante já acontece antes (z robusto por MAD), a média
    ponderada usa toda a informação válida sem perder proteção.
    """
    if len(valores) == 0 or pesos.sum() <= 0:
        return float("nan")
    return float(np.average(valores, weights=pesos))


def referencia_mercado(coleta: dict, cfg: Config) -> pd.DataFrame:
    """Preço de referência de mercado por SKU.

    Média simples é frágil: sofre com preço destoante, coleta velha e
    correspondência ruim. Cada observação recebe um peso que decai por idade
    (R06), cai com a qualidade do matching e zera quando o concorrente está sem
    estoque (R05); o z robusto por MAD descarta o preço destoante (R10). A
    referência é a média desses pesos sobre o que sobrou.
    """
    c = coleta["concorrencia"].copy()
    ref = data_referencia(coleta)
    c["idade_h"] = (ref - c["Data/hora coleta"]).dt.total_seconds() / 3600.0
    c["peso_frescor"] = np.power(2.0, -c["idade_h"] / cfg.meia_vida_coleta_h)
    c.loc[c["idade_h"] > cfg.idade_max_coleta_h, "peso_frescor"] = 0.0
    c["peso_match"] = c["Correspondência"].map(PESO_MATCH).fillna(0.3)
    c["peso_disp"] = np.where(
        c["Disponível"].astype(str).str.lower().str.startswith("s"), 1.0, 0.0)

    linhas = []
    for sku, g in c.groupby("SKU"):
        v = g["Preço"].to_numpy(float)
        med, mad = np.median(v), np.median(np.abs(v - np.median(v)))
        z = 0.6745 * (v - med) / mad if mad > 1e-9 else np.zeros_like(v)
        anomalo = np.abs(z) > 3.5

        peso = (g["peso_frescor"].to_numpy() * g["peso_match"].to_numpy()
                * g["peso_disp"].to_numpy() * (~anomalo))
        validos = peso > 0
        n_val = int(validos.sum())
        mercado = _referencia_ponderada(v[validos], peso[validos]) if n_val else float("nan")

        # Frete do concorrente. A tentação é somar frete ao preço dele e comparar
        # com o nosso preço — e isso está errado, porque a base da Popular Pet traz
        # o frete como custo rateado, não como o valor cobrado do cliente. Comparar
        # preço nu contra preço entregue faz todo item parecer barato e derruba as
        # reduções legítimas.
        #
        # O uso correto é dentro da própria concorrência: um preço baixo com frete
        # muito acima dos pares não é a alternativa que o cliente escolhe, então
        # pesa menos na referência. O preço entregue fica calculado e exibido, mas
        # como informação — a comparação com o nosso entregue é limitação declarada.
        fr = np.nan_to_num(g["Frete"].to_numpy(float)) if "Frete" in g.columns \
            else np.zeros_like(v)
        if n_val and fr[validos].size and fr[validos].max() > 0:
            fr_med = float(np.median(fr[validos]))
            if fr_med > 0:
                # Frete no dobro da mediana vale metade; piso em 0,5 para o sinal
                # nunca apagar um concorrente sozinho.
                ajuste = np.clip(fr_med / np.maximum(fr, 1e-9), 0.5, 1.0)
                # Normalizado pela média entre os válidos: isto reordena quem pesa
                # mais, não reduz a informação disponível. Sem normalizar, um SKU
                # com fretes dispersos perdia massa de peso e caía na régua de
                # confiança como se tivesse menos concorrentes — o sinal de frete
                # virava, por acidente, um sinal de dado ruim.
                m = float(ajuste[validos].mean()) if n_val else 1.0
                if m > 0:
                    peso = peso * (ajuste / m)
                validos = peso > 0
                n_val = int(validos.sum())
                mercado = _referencia_ponderada(v[validos], peso[validos]) if n_val \
                    else float("nan")

        entregue = _referencia_ponderada((v + fr)[validos], peso[validos]) if n_val \
            else float("nan")
        frete_medio = float(np.average(fr[validos], weights=peso[validos])) if n_val \
            else float("nan")

        if n_val >= 2:
            q1, q3 = np.percentile(v[validos], [25, 75])
            dispersao = (q3 - q1) / mercado if mercado else 1.0
        else:
            dispersao = 0.5 if n_val == 1 else 1.0
        confianca = float(np.clip((peso.sum() / max(len(g), 1)) * max(0.0, 1 - dispersao), 0, 1))

        alertas = []
        nao_ean = int((g["Correspondência"] != "EAN exato").sum())
        if nao_ean:
            alertas.append("%d de %d comparações sem EAN exato (confiança menor)"
                           % (nao_ean, len(g)))
        if anomalo.any():
            alertas.append("%d preço(s) de concorrente destoante(s), descartado(s) da mediana"
                           % int(anomalo.sum()))
        indisp = int((g["peso_disp"] == 0).sum())
        if indisp:
            alertas.append("%d concorrente(s) sem estoque, ignorado(s) (R05)" % indisp)
        velhas = int((g["idade_h"] > cfg.idade_max_coleta_h).sum())
        if velhas:
            alertas.append("%d coleta(s) com mais de 48 h, descartada(s) (R06)" % velhas)

        linhas.append({
            "sku": sku, "preco_mercado": mercado,
            "preco_mercado_entregue": entregue, "frete_medio_concorrentes": frete_medio,
            "menor_concorrente": float(v[validos].min()) if n_val else None,
            "n_validos": n_val, "n_total": int(len(g)),
            "idade_min_h": float(g["idade_h"].min()),
            "confianca_dados": confianca, "alertas_dados": alertas,
            "concorrentes": [{
                "nome": r["Concorrente"], "preco": round(float(r["Preço"]), 2),
                "match": r["Correspondência"], "disponivel": str(r["Disponível"]),
                "idade_h": round(float(r["idade_h"]), 1),
                "frete": (round(float(r["Frete"]), 2) if "Frete" in g.columns
                          and pd.notna(r["Frete"]) else None),
                "entregue": (round(float(r["Preço"]) + float(r["Frete"]), 2)
                             if "Frete" in g.columns and pd.notna(r["Frete"]) else None),
                # Peso exibido como proporção do maior do SKU. A normalização do
                # frete é relativa e pode deixar um peso acima de 1, que está
                # certo na conta e parece defeito na tela.
                "peso": round(float(p) / max(float(peso.max()), 1e-9), 3),
                "anomalo": bool(a),
            } for (_, r), p, a in zip(g.iterrows(), peso, anomalo)],
        })
    return pd.DataFrame(linhas)


# ------------------------------------------------------------------ sinais
def sinais(coleta: dict, cadastro: pd.DataFrame) -> pd.DataFrame:
    """Volume, tendência e cobertura recalculada a partir da venda recente."""
    v = coleta["vendas"].copy()
    v["Mês"] = v["Mês"].astype(str)
    meses = sorted(v["Mês"].unique())
    ult3, ant3 = meses[-3:], meses[-6:-3]

    out = []
    for (sku, canal), d in v.groupby(["SKU", "Canal"]):
        ult = d[d["Mês"].isin(ult3)]
        q3 = ult["Quantidade"].sum() / 3.0
        qa = d[d["Mês"].isin(ant3)]["Quantidade"].sum() / 3.0
        prim3 = d[d["Mês"].isin(meses[:3])]["Quantidade"].sum() / 3.0

        # Desconto habitual: o preço de tabela não é o preço que sai no caixa.
        # Ponderado por quantidade, porque mês de volume alto pesa mais no que o
        # cliente de fato pagou.
        desc = 0.0
        if "Desconto médio" in ult.columns and ult["Quantidade"].sum() > 0:
            dd = pd.to_numeric(ult["Desconto médio"], errors="coerce").fillna(0.0)
            desc = float(np.average(dd, weights=ult["Quantidade"].clip(lower=1e-9)))
            desc = float(np.clip(desc, 0.0, 0.9))

        out.append({
            "sku": sku, "canal": canal,
            "volume_mes": q3,
            "receita_mes": ult["Receita"].sum() / 3.0,
            "tendencia_mes": ((q3 / qa - 1.0) / 3.0) if qa > 0 else 0.0,
            "var_12m": (q3 / prim3 - 1.0) if prim3 > 0 else 0.0,
            "desconto_medio": desc,
        })
    s = pd.DataFrame(out)

    est = cadastro[["sku", "estoque"]].drop_duplicates("sku")
    s = s.merge(est, on="sku", how="left")
    venda_dia = s.groupby("sku")["volume_mes"].sum() / 30.0
    s["venda_dia_sku"] = s["sku"].map(venda_dia)
    s["cobertura"] = np.where(s["venda_dia_sku"] > 0,
                              s["estoque"] / s["venda_dia_sku"], 999.0).round(1)
    # A cobertura projetada depende do pedido em aberto já limpo dos vencidos, e
    # essa limpeza precisa da data de referência da coleta — que só existe em
    # `montar`. Daqui sai só a venda diária, que é o denominador dela.
    return s.drop(columns=["estoque"])


# -------------------------------------------------------------------- preço
def piso_preco(custo_rep, impostos, frete, taxa, margem_min, regua, preco_atual) -> float:
    """Menor preço que respeita a margem mínima (R01), na régua escolhida.

    'simples' olha só o custo de reposição. 'liquida' trata impostos e taxa de
    canal como proporcionais ao preço, resolvendo p(1 − τ − t) − c − f = m·p:
    o piso deixa de ser soma e vira divisão, e sobe bem mais.
    """
    if regua == "simples":
        return custo_rep / (1.0 - margem_min)
    tau = impostos / preco_atual if preco_atual else 0.0
    t = taxa / preco_atual if preco_atual else 0.0
    den = 1.0 - tau - t - margem_min
    return (custo_rep + frete) / (den if den > 0.05 else 0.05)


def montar(cfg: Config) -> pd.DataFrame:
    """Uma linha por SKU × canal com todos os fatores da decisão."""
    coleta = carregar_coleta()
    d = carregar_cadastro()
    d = d.merge(referencia_mercado(coleta, cfg), on="sku", how="left")
    d = d.merge(sinais(coleta, d), on=["sku", "canal"], how="left")

    d["margem_simples"] = (d.preco_atual - d.custo_rep) / d.preco_atual
    d["margem_liquida"] = (d.preco_atual - d.custo_total) / d.preco_atual
    d["margem_atual"] = d.margem_simples if cfg.regua == "simples" else d.margem_liquida
    d["piso"] = [piso_preco(r.custo_rep, r.impostos, r.frete, r.taxa_canal, r.margem_min,
                            cfg.regua, r.preco_atual) for r in d.itertuples()]
    d["indice_preco"] = d.preco_atual / d.preco_mercado

    # Preço efetivo: o de tabela menos o desconto que já se pratica. A margem que
    # importa é a desse preço, não a do cartaz — vender a lista menos 8% abaixo do
    # piso é violar o piso, ainda que a planilha diga que está tudo certo.
    d["desconto_medio"] = d.get("desconto_medio", pd.Series(0.0, index=d.index)).fillna(0.0)
    d["preco_efetivo"] = d.preco_atual * (1 - d.desconto_medio)
    custo_ef = d.custo_rep if cfg.regua == "simples" else d.custo_total
    d["margem_efetiva"] = (d.preco_efetivo - custo_ef) / d.preco_efetivo
    d["indice_entregue"] = d.preco_atual / d.preco_mercado_entregue

    ref = data_referencia(coleta)
    promo = coleta["promocoes"]
    # Guardar só "está em campanha" não bastava: para decidir com conhecimento de
    # causa, quem aprova precisa saber qual campanha, qual desconto e até quando.
    ativos: dict = {}
    if not promo.empty and "Início" in promo.columns:
        vigentes = promo.loc[(promo["Início"] <= ref) & (promo["Fim"] >= ref)]
        for _, pr in vigentes.iterrows():
            ativos[pr["SKU"]] = {
                "campanha": str(pr.get("Campanha") or "campanha"),
                "fim": (pr["Fim"].date().isoformat() if pd.notna(pr.get("Fim")) else None),
                "desconto": (float(pr["Desconto"]) if pd.notna(pr.get("Desconto")) else None),
                "dias_para_fim": (int((pr["Fim"] - ref).days)
                                  if pd.notna(pr.get("Fim")) else None),
            }
    d["em_campanha"] = d.sku.isin(ativos)
    d["campanha"] = d.sku.map(lambda k: ativos.get(k))

    def _dias(v):
        if not v:
            return 999
        try:
            return (ref - pd.to_datetime(v)).days
        except Exception:
            return 999
    d["dias_ultima_alteracao"] = d.ultima_alteracao.map(_dias)

    # Dias até o recebimento contados da data de referência da coleta, não de
    # hoje: a base é uma fotografia de 09/09 e o relógio do servidor não tem nada
    # a ver com ela. Previsão anterior à referência significa pedido vencido —
    # ou já entrou no estoque, ou atrasou, e nos dois casos não é estoque a
    # caminho. Isso é dado inconsistente, e o Desafio pede que seja apontado.
    def _ate(v):
        if not v:
            return None
        try:
            return float((pd.to_datetime(v) - ref).days)
        except Exception:
            return None
    d["dias_ate_recebimento"] = d.previsao_recebimento.map(_ate)
    d["pedido_vencido"] = (d.pedido_aberto > 0) & (d.dias_ate_recebimento.notna()) \
        & (d.dias_ate_recebimento < 0)
    d.loc[d.pedido_vencido, "pedido_aberto"] = 0

    # Cobertura projetada: estoque mais o que já está comprado e vai chegar.
    d["cobertura_projetada"] = np.where(
        d.venda_dia_sku > 0,
        (d.estoque.fillna(0) + d.pedido_aberto) / d.venda_dia_sku, 999.0).round(1)
    d["data_rodada"] = ref.date().isoformat()
    return d


def preco_alvo(r, cfg: Config) -> float:
    """Alvo antes das restrições: mercado ajustado pelo posicionamento.

    Estoque parado (R12) empurra para baixo; o piso de margem segura por baixo.
    """
    if not np.isfinite(r.preco_mercado):
        return r.preco_atual
    alvo = r.preco_mercado * cfg.posicionamento
    # R12 olha a cobertura projetada — o que já foi comprado e vai chegar conta
    # para o excesso, mesmo que ainda não esteja no depósito.
    projetada = getattr(r, "cobertura_projetada", r.cobertura)
    if max(r.cobertura, projetada or 0) > cfg.cobertura_excesso_dias:
        alvo *= 0.97
    return max(alvo, r.piso)


def decidir(cfg: Config) -> pd.DataFrame:
    """Aplica alvo, restrições duras e roteadores. Uma recomendação por item."""
    d = montar(cfg)
    recs = []

    for r in d.itertuples():
        motivos, regras, alertas = [], [], list(r.alertas_dados or [])
        custo_dec = r.custo_rep if cfg.regua == "simples" else r.custo_total
        alvo = preco_alvo(r, cfg)

        # Encolhimento por confiança. Referência fraca não cala o motor — ela
        # encurta o passo. Com confiança na metade do mínimo exigido, anda-se
        # metade do caminho até o alvo. É a mesma ideia de encolher estimativa
        # ruidosa na direção do valor conhecido: aqui o valor conhecido é o preço
        # que já está na gôndola.
        fator = min(1.0, float(r.confianca_dados) / max(cfg.confianca_minima, 1e-6))
        if fator < 1.0:
            alvo = r.preco_atual + (alvo - r.preco_atual) * fator
            motivos.append("confiança do dado em %.2f (mínimo %.2f): o passo até o alvo "
                           "foi encurtado para %.0f%% e a decisão sobe para a gestora"
                           % (r.confianca_dados, cfg.confianca_minima, 100 * fator))

        lim_inf = max(r.piso, r.preco_atual * (1 - cfg.variacao_max))
        lim_sup = r.preco_atual * (1 + cfg.variacao_max)
        viavel_vazio = lim_inf > lim_sup + 1e-9
        p = float(np.clip(alvo, lim_inf, max(lim_inf, lim_sup)))

        # R11 — cobertura curta bloqueia redução: o estoque acaba antes do ganho.
        # Olha o estoque físico, não o projetado: mercadoria comprada que chega
        # em três semanas não ajuda a atender a demanda que a redução cria hoje.
        # A exceção é a entrega iminente, aí a falta é temporária e não vale
        # travar a decisão por ela.
        chegando_ja = (getattr(r, "pedido_aberto", 0) or 0) > 0 and \
            (getattr(r, "dias_ate_recebimento", 999) or 999) <= 7
        if p < r.preco_atual and r.cobertura < cfg.cobertura_min_dias and not chegando_ja:
            p = r.preco_atual
            regras.append("R11")
            motivos.append("cobertura de %.0f dias bloqueia redução (R11)" % r.cobertura)

        delta = (p - r.preco_atual) / r.preco_atual if r.preco_atual else 0.0
        margem_res = ((p - custo_dec) / p) if p else 0.0

        # ------------------------------------------------------------------
        # Duas perguntas diferentes, que antes eu respondia num campo só.
        #
        #   1. Qual é a recomendação de preço?  SUBIR, BAIXAR ou MANTER.
        #   2. O que precisa acontecer antes de ela valer?  Nada, aprovação da
        #      gestora, ou espera até uma data.
        #
        # Misturar as duas custava caro: campanha ativa apagava a opinião do
        # motor, e o analista abria um item e via "REVISAR" sem preço nenhum —
        # sem nada para revisar. Campanha não muda o preço justo do item, ela
        # adia a aplicação dele.
        #
        # REVISAR, aqui, passa a ter um significado só: **o motor não tem base
        # para opinar**. Sem concorrente válido, com coleta velha, com confiança
        # abaixo do mínimo ou com as regras se contradizendo, não existe preço
        # recomendado — e aí "revisar" quer dizer olhar o dado, não o preço.
        # ------------------------------------------------------------------
        sem_base = False
        if viavel_vazio:
            sem_base = True
            regras += ["R01", "R02"]
            motivos.append("conflito entre o piso de margem e o limite de variação (R01/R02): "
                           "nenhum preço atende às duas regras ao mesmo tempo")
        if not np.isfinite(r.preco_mercado) or r.n_validos == 0:
            sem_base = True
            regras.append("R05")
            motivos.append("sem preço de concorrente válido (R05): não há referência de mercado")
        if r.idade_min_h > cfg.idade_max_coleta_h:
            sem_base = True
            regras.append("R06")
            motivos.append("coleta de concorrência com mais de 48 h (R06): a referência "
                           "está velha demais para sustentar uma recomendação")
        if str(r.status).lower().startswith("descont"):
            sem_base = True
            motivos.append("produto em descontinuação: preço de saída é plano de liquidação, "
                           "não reprecificação de rotina")

        # ---------------------------------------------------- retenção
        # A recomendação continua de pé; o que muda é quem assina e quando.
        retencao, retencao_motivo, liberado_em = "livre", "", None

        # R09 é "Revisar" na tabela da Popular Pet, não "Bloquear" — e a palavra que
        # importa na regra é *automaticamente*. Num sistema em que nada é aplicado
        # sem gente, bloquear um item em campanha seria mais duro do que a empresa
        # pediu: o analista pode muito bem ter um motivo para mexer, e tirar essa
        # possibilidade dele é decidir no lugar dele. O que a regra exige é que a
        # decisão suba de nível e que quem assina saiba o que está desfazendo.
        camp = getattr(r, "campanha", None) or {}
        if r.em_campanha:
            regras.append("R09")
            retencao = "aprovacao"
            retencao_motivo = (
                "%s em curso%s%s: mexer no preço agora desfaz a oferta anunciada. "
                "A decisão sobe para a gestora (R09)."
                % (camp.get("campanha", "Campanha"),
                   " com %.0f%% de desconto" % (100 * camp["desconto"])
                   if camp.get("desconto") else "",
                   ", termina em %d dia(s)" % camp["dias_para_fim"]
                   if camp.get("dias_para_fim") is not None else ""))
            liberado_em = camp.get("fim")
        elif r.dias_ultima_alteracao < cfg.dias_min_entre_alteracoes:
            retencao = "bloqueado"
            regras.append("R08")
            falta = cfg.dias_min_entre_alteracoes - int(r.dias_ultima_alteracao)
            retencao_motivo = ("preço alterado há %d dia(s): oscilação rápida destrói a "
                               "imagem de preço, faltam %d dia(s) (R08)"
                               % (int(r.dias_ultima_alteracao), falta))

        exige_nota = False
        if getattr(r, "estrategico", 0):
            regras.append("R04")
            if retencao == "livre":
                retencao = "aprovacao"
            exige_nota = True
            retencao_motivo = retencao_motivo or (
                "produto estratégico: sustenta a imagem de preço da loja, então a decisão "
                "sobe para a gestora e exige justificativa escrita (R04)")
        if str(r.curva) == "A" and abs(delta) > cfg.curva_a_limite:
            regras.append("R03")
            if retencao == "livre":
                retencao = "aprovacao"
            retencao_motivo = retencao_motivo or (
                "curva A com variação de %.1f%%, acima de 3%%: decisão sobe para a "
                "gestora (R03)" % (100 * delta))

        if r.confianca_dados < cfg.confianca_minima and retencao == "livre":
            retencao = "aprovacao"
            retencao_motivo = retencao_motivo or (
                "confiança do dado de mercado em %.2f, abaixo do mínimo de %.2f: a "
                "recomendação vale, mas quem assina é a gestora"
                % (r.confianca_dados, cfg.confianca_minima))

        # Margem efetiva: se o desconto habitual já derruba a margem abaixo do
        # mínimo, o problema não é o preço sugerido, é a política de desconto —
        # e isso é decisão de gente, não de motor.
        if (getattr(r, "desconto_medio", 0) or 0) > 0.02 and r.margem_efetiva < r.margem_min:
            if retencao == "livre":
                retencao = "aprovacao"
            motivos.append("desconto habitual de %.1f%% derruba a margem efetiva para "
                           "%.1f%%, abaixo do mínimo de %.1f%%"
                           % (100 * r.desconto_medio, 100 * r.margem_efetiva,
                              100 * r.margem_min))

        # Frete muito acima dos pares no SKU: alerta para o analista saber que a
        # referência foi reponderada, não motivo para mudar a decisão sozinho.
        if (np.isfinite(getattr(r, "frete_medio_concorrentes", float("nan")))
                and r.frete_medio_concorrentes > 0.15 * r.preco_atual):
            alertas.append("frete do concorrente pesa %.0f%% do preço: a referência "
                           "despriorizou quem cobra frete fora da curva"
                           % (100 * r.frete_medio_concorrentes / r.preco_atual))

        # ---------------------------------------------------- a saída
        if sem_base:
            acao = "REVISAR"
            p, delta = r.preco_atual, 0.0
            margem_res = (r.preco_atual - custo_dec) / r.preco_atual
        elif delta > cfg.banda_morta:
            acao = "SUBIR"
        elif delta < -cfg.banda_morta:
            acao = "BAIXAR"
        else:
            acao = "MANTER"
            if abs(delta) > 1e-9:
                motivos.append("variação abaixo do limiar de percepção de %.0f%%"
                               % (100 * cfg.banda_morta))
            p, delta = r.preco_atual, 0.0
            margem_res = (r.preco_atual - custo_dec) / r.preco_atual

        # Risco: tamanho da mudança, importância do item, qualidade do dado e estoque.
        pontos = 0
        pontos += 2 if abs(delta) > 0.03 else (1 if abs(delta) > 0.015 else 0)
        pontos += 2 if r.curva == "A" else (1 if r.curva == "B" else 0)
        pontos += 2 if r.confianca_dados < 0.6 else (1 if r.confianca_dados < 0.8 else 0)
        pontos += 1 if r.cobertura < cfg.cobertura_min_dias else 0
        risco = "Alto" if pontos >= 5 else ("Médio" if pontos >= 3 else "Baixo")
        risco_score = round(100 * pontos / 7.0, 1)

        impacto = (p - r.preco_atual) * (r.volume_mes or 0.0)
        valor_em_jogo = (r.receita_mes or 0.0) * abs(delta)

        # Quanto o volume precisaria variar para a margem total não mudar.
        # É a pergunta que o analista responde de fato ao aprovar uma redução.
        m_hoje, m_novo = r.preco_atual - custo_dec, p - custo_dec
        equilibrio = (m_hoje / m_novo - 1.0) if (m_novo > 0.01 and m_hoje > 0.01
                                                 and abs(delta) > 1e-9) else None

        if acao == "SUBIR":
            motivo_principal = ("margem abaixo do piso" if r.preco_atual < r.piso - 0.005
                                else ("preço abaixo do mercado"
                                      if np.isfinite(r.indice_preco) and r.indice_preco < 0.98
                                      else "espaço de margem"))
        elif acao == "BAIXAR":
            motivo_principal = ("preço acima do mercado"
                                if np.isfinite(r.indice_preco) and r.indice_preco > 1.02
                                else ("estoque parado" if r.cobertura > cfg.cobertura_excesso_dias
                                      else "ajuste ao mercado"))
        elif acao == "REVISAR":
            rs = set(regras)
            motivo_principal = ("dado de mercado insuficiente" if {"R05", "R06"} & rs else
                                "conflito entre regras" if "R01" in rs else
                                "em descontinuação")
        else:
            motivo_principal = "já está no ponto"

        if risco == "Alto" and retencao == "livre":
            retencao = "aprovacao"
            retencao_motivo = retencao_motivo or (
                "risco alto (%.0f de 100): mudança grande, item importante ou dado fraco"
                % risco_score)
        exige_aprovacao = retencao == "aprovacao"

        recs.append({
            "sku": r.sku, "produto": r.produto, "categoria": r.categoria, "marca": r.marca,
            "curva": r.curva, "status": r.status, "canal": r.canal,
            "preco_atual": round(r.preco_atual, 2),
            "preco_mercado": None if not np.isfinite(r.preco_mercado) else round(r.preco_mercado, 2),
            "preco_mercado_entregue": (None if not np.isfinite(r.preco_mercado_entregue)
                                       else round(r.preco_mercado_entregue, 2)),
            "indice_entregue": (None if not np.isfinite(r.indice_entregue)
                                else round(r.indice_entregue, 4)),
            "frete_medio_concorrentes": (None if not np.isfinite(r.frete_medio_concorrentes)
                                         else round(r.frete_medio_concorrentes, 2)),
            "desconto_medio": round(float(r.desconto_medio or 0), 4),
            "preco_efetivo": round(float(r.preco_efetivo), 2),
            "margem_efetiva": round(float(r.margem_efetiva), 4),
            "cobertura_projetada": float(getattr(r, "cobertura_projetada", r.cobertura) or 0),
            "pedido_aberto": int(getattr(r, "pedido_aberto", 0) or 0),
            "estrategico": bool(getattr(r, "estrategico", 0)),
            "menor_concorrente": r.menor_concorrente,
            "indice_preco": None if not np.isfinite(r.indice_preco) else round(r.indice_preco, 4),
            "custo": round(r.custo_rep, 2), "custo_total": round(r.custo_total, 2),
            "margem_atual": round(r.margem_simples, 4),
            "margem_liquida": round(r.margem_liquida, 4),
            "margem_min": round(r.margem_min, 4), "margem_alvo": round(r.margem_alvo, 4),
            "piso": round(r.piso, 2),
            "estoque": int(r.estoque or 0), "cobertura": float(r.cobertura),
            "tendencia_mes": round(float(r.tendencia_mes or 0), 4),
            "var_12m": round(float(r.var_12m or 0), 4),
            "volume_mes": round(float(r.volume_mes or 0), 1),
            "receita_mes": round(float(r.receita_mes or 0), 2),
            "confianca_dados": round(float(r.confianca_dados), 4),
            "n_validos": int(r.n_validos), "n_total": int(r.n_total),
            "concorrentes": r.concorrentes,
            "em_campanha": bool(r.em_campanha),
            "campanha": camp or None,
            "recomendacao": acao, "motivo_principal": motivo_principal,
            "retencao": retencao, "retencao_motivo": retencao_motivo,
            "fator_confianca": round(fator, 3),
            "liberado_em": liberado_em, "exige_justificativa": exige_nota,
            "preco_sugerido": round(p, 2), "variacao": round(delta, 4),
            "margem_resultante": round(margem_res, 4),
            "equilibrio_volume": None if equilibrio is None else round(equilibrio, 4),
            "nivel_risco": risco, "risco_score": risco_score,
            "exige_aprovacao": exige_aprovacao,
            "impacto_mes": round(impacto, 2), "valor_em_jogo": round(valor_em_jogo, 2),
            "motivos_revisao": motivos, "regras": sorted(set(regras)), "alertas_dados": alertas,
            "abaixo_do_piso": bool(r.preco_atual < r.piso - 0.005),
            "data_rodada": r.data_rodada,
        })
    return pd.DataFrame(recs)
