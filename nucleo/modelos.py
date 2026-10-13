# -*- coding: utf-8 -*-
"""Camada 1 — estimativas que nenhuma regra fixa captura.

Tudo aqui produz ESTIMATIVA, nunca decisão. As saídas entram como coluna na
fila e como frase na justificativa; o preço continua vindo do motor.

Partida fria é explícita: sem rótulos suficientes cada modelo devolve None e a
tela mostra "—". A aplicação funciona inteira sem esta camada.

Sem scikit-learn instalado, a camada simplesmente não liga e o resto segue.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

try:
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import KFold, cross_val_score
    TEM_SKLEARN = True
except Exception:                                      # pragma: no cover
    TEM_SKLEARN = False

MIN_ROTULOS_ACEITE = 30


# ------------------------------------------------------------------ demanda
class ModeloDemanda:
    """Volume mensal a partir de preço, categoria, canal, mês e desconto.

    Limitação declarada: o preço quase não varia na base, então a sensibilidade
    ao preço NÃO é identificável. A estimativa vale como ordem de grandeza — por
    isso o impacto financeiro é sempre apresentado sob volume constante.
    """

    def __init__(self):
        self.modelo = None
        self.colunas: list[str] = []
        self.r2 = None
        self.n = 0

    def treinar(self, vendas: pd.DataFrame):
        if not TEM_SKLEARN or len(vendas) < 60:
            return self
        v = vendas.copy()
        v["Mês"] = v["Mês"].astype(str)
        v["mes_num"] = v["Mês"].str[-2:].astype(int)
        v["log_preco"] = np.log(v["Preço médio"].clip(lower=0.01))
        v["desconto"] = v.get("Desconto médio", pd.Series(0, index=v.index)).fillna(0)

        X = pd.concat([v[["log_preco", "mes_num", "desconto"]],
                       pd.get_dummies(v["Canal"], prefix="can")], axis=1).astype(float)
        y = np.log(v["Quantidade"].clip(lower=1))
        m = GradientBoostingRegressor(n_estimators=160, max_depth=3, learning_rate=0.06,
                                      subsample=0.9, random_state=7)
        try:
            cv = cross_val_score(m, X, y, cv=KFold(4, shuffle=True, random_state=7), scoring="r2")
            self.r2 = round(float(cv.mean()), 3)
        except Exception:
            self.r2 = None
        m.fit(X, y)
        self.modelo, self.colunas, self.n = m, list(X.columns), int(len(X))
        return self

    def prever(self, preco: float, canal: str, mes_num: int = 9, desconto: float = 0.0):
        if self.modelo is None:
            return None
        linha = {c: 0.0 for c in self.colunas}
        linha["log_preco"] = float(np.log(max(preco, 0.01)))
        linha["mes_num"] = float(mes_num)
        linha["desconto"] = float(desconto)
        chave = "can_" + str(canal)
        if chave in linha:
            linha[chave] = 1.0
        x = pd.DataFrame([linha], columns=self.colunas).astype(float)
        try:
            return round(float(np.exp(self.modelo.predict(x)[0])), 1)
        except Exception:
            return None

    def situacao(self) -> dict:
        return {"disponivel": self.modelo is not None, "r2_cv": self.r2, "n": self.n,
                "aviso": ("o preço quase não varia na base: a sensibilidade ao preço não é "
                          "identificável e a estimativa vale como ordem de grandeza"),
                "motivo": None if TEM_SKLEARN else "scikit-learn não instalado"}


# ------------------------------------------------------- política do analista
class ModeloAceite:
    """Aprende, do registro de auditoria, quais recomendações o time aceita.

    Entra como coluna "chance de aceite" na fila: ordena e alerta, nunca decide.
    """

    FATORES = ["variação", "impacto", "confiança do dado", "cobertura",
               "margem resultante", "curva A", "risco", "é alta"]

    def __init__(self):
        self.modelo = None
        self.n = 0
        self.acuracia = None

    @staticmethod
    def _vetor(rec: dict) -> list[float]:
        risco = {"Baixo": 0, "Médio": 1, "Alto": 2}.get(rec.get("nivel_risco"), 1)
        return [
            float(rec.get("variacao") or 0),
            float(abs(rec.get("impacto_mes") or 0)),
            float(rec.get("confianca_dados") or 0),
            float(min(rec.get("cobertura") or 0, 200)),
            float(rec.get("margem_resultante") or 0),
            1.0 if rec.get("curva") == "A" else 0.0,
            float(risco),
            1.0 if rec.get("recomendacao") == "SUBIR" else 0.0,
        ]

    def treinar(self, decisoes: list[dict]):
        if not TEM_SKLEARN:
            return self
        X, y = [], []
        for d in decisoes:
            if d.get("decisao") not in ("aprovada", "editada", "rejeitada"):
                continue
            fat = d.get("snap_fatores")
            if isinstance(fat, str):
                try:
                    fat = json.loads(fat)
                except Exception:
                    continue
            if not fat:
                continue
            X.append(self._vetor(fat))
            y.append(0 if d["decisao"] == "rejeitada" else 1)

        self.n = len(X)
        if self.n < MIN_ROTULOS_ACEITE or len(set(y)) < 2:
            return self
        X, y = np.array(X, float), np.array(y, int)
        m = LogisticRegression(max_iter=1000, class_weight="balanced")
        try:
            k = max(2, min(4, int(np.bincount(y).min())))
            self.acuracia = round(float(cross_val_score(m, X, y, cv=k).mean()), 3)
        except Exception:
            self.acuracia = None
        m.fit(X, y)
        self.modelo = m
        return self

    def prever(self, rec: dict):
        if self.modelo is None:
            return None
        try:
            return round(float(self.modelo.predict_proba([self._vetor(rec)])[0][1]), 3)
        except Exception:
            return None

    def influencias(self, rec: dict) -> list[dict]:
        """Os três fatores que mais pesaram — para explicar a estimativa."""
        if self.modelo is None:
            return []
        contrib = self.modelo.coef_[0] * np.array(self._vetor(rec), float)
        ordem = np.argsort(-np.abs(contrib))[:3]
        return [{"fator": self.FATORES[i], "efeito": round(float(contrib[i]), 3)} for i in ordem]

    def situacao(self) -> dict:
        return {"disponivel": self.modelo is not None, "n_rotulos": self.n,
                "minimo": MIN_ROTULOS_ACEITE, "acuracia_cv": self.acuracia,
                "motivo": None if TEM_SKLEARN else "scikit-learn não instalado"}
