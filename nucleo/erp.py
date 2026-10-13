# -*- coding: utf-8 -*-
"""Integração com o ERP (simulado pela planilha).

O "ERP" da Popular Pet é a planilha `dados/base_popular_pet.xlsx`. Cada
sincronização lê as abas de cadastro, preço, custo, estoque e histórico,
atualiza o banco e registra toda mudança de preço no histórico do SKU.

Três formas de atualizar, como na especificação: carga inicial (primeira
execução), sincronizar agora (relê a planilha) e importar arquivo (uma planilha
exportada do ERP, enviada pela tela).

Validação é tudo-ou-nada: se faltar aba ou coluna, nada é alterado.
"""

from __future__ import annotations

import time
from datetime import datetime, date

import pandas as pd

from nucleo.banco import sessao

ABAS_OBRIGATORIAS = {
    "Produtos": ["SKU", "Produto", "Categoria", "Marca", "Curva ABC", "Status"],
    "Precos_Atuais": ["SKU", "Canal", "Preço atual", "Data referência"],
    "Custos_Margens": ["SKU", "Canal", "Custo reposição", "Margem mínima",
                       "Margem alvo", "Custo total referência"],
    "Estoque": ["SKU", "Estoque atual", "Cobertura dias"],
}
ABAS_OPCIONAIS = ["Historico_Precos", "Vendas_12m", "Concorrencia",
                  "Promocoes", "Regras_Negocio", "Indicadores_Atuais"]

CANAIS = ["Loja física", "E-commerce", "Marketplace"]


class ErroErp(Exception):
    """Problema na planilha do ERP. A mensagem é exibida ao usuário."""


def _ler_aba(caminho_ou_buffer, aba: str) -> pd.DataFrame:
    """As abas têm duas linhas de título antes do cabeçalho."""
    df = pd.read_excel(caminho_ou_buffer, sheet_name=aba, header=2)
    return df.dropna(how="all")


def ler_planilha(origem) -> dict[str, pd.DataFrame]:
    """Lê e valida a planilha inteira antes de tocar no banco."""
    try:
        xls = pd.ExcelFile(origem)
    except Exception as e:
        raise ErroErp("Arquivo do ERP inválido: não foi possível ler como planilha "
                      "Excel (.xlsx). Detalhe: %s" % type(e).__name__) from e

    faltando = [a for a in ABAS_OBRIGATORIAS if a not in xls.sheet_names]
    if faltando:
        raise ErroErp("Planilha sem a(s) aba(s): %s." % ", ".join(faltando))

    base: dict[str, pd.DataFrame] = {}
    for aba, colunas in ABAS_OBRIGATORIAS.items():
        df = _ler_aba(xls, aba)
        ausentes = [c for c in colunas if c not in df.columns]
        if ausentes:
            raise ErroErp("Aba %s sem a(s) coluna(s): %s." % (aba, ", ".join(ausentes)))
        base[aba] = df
    for aba in ABAS_OPCIONAIS:
        if aba in xls.sheet_names:
            base[aba] = _ler_aba(xls, aba)

    canais_vistos = set(base["Precos_Atuais"]["Canal"].dropna().unique())
    desconhecidos = canais_vistos - set(CANAIS)
    if desconhecidos:
        raise ErroErp("Canal não reconhecido em Precos_Atuais: %s. "
                      "Use %s." % (", ".join(map(str, desconhecidos)), ", ".join(CANAIS)))
    return base


def _data_texto(v) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y-%m-%d")
    try:
        return pd.to_datetime(v, dayfirst=True).strftime("%Y-%m-%d")
    except Exception:
        return str(v)


def sincronizar(origem=None, rotulo_origem: str = "manual",
                usuario: str = "Sistema", fonte: str | None = None) -> dict:
    """Lê a planilha e atualiza o banco. Registra a execução em `sync_erp`."""
    from nucleo.banco import PASTA_DADOS
    import os

    t0 = time.perf_counter()
    if origem is None:
        origem = os.path.join(PASTA_DADOS, "base_popular_pet.xlsx")
        fonte = fonte or "ERP Popular Pet (simulado) · base_popular_pet.xlsx"
    fonte = fonte or "arquivo enviado"

    try:
        base = ler_planilha(origem)
    except ErroErp as e:
        with sessao() as con:
            con.execute(
                "INSERT INTO sync_erp (quando, origem, usuario, status, fonte, duracao_s, mensagem)"
                " VALUES (?,?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), rotulo_origem, usuario,
                 "erro", fonte, round(time.perf_counter() - t0, 2), str(e)))
        raise

    novos = atualizados = precos_alterados = 0

    with sessao() as con:
        skus_antes = {r["sku"] for r in con.execute("SELECT sku FROM produtos")}

        for r in base["Produtos"].to_dict("records"):
            sku = str(r["SKU"])
            dados = (sku, str(r["Produto"]), str(r["Categoria"]), str(r["Marca"]),
                     str(r["Curva ABC"]), str(r["Status"]))
            if sku in skus_antes:
                con.execute("UPDATE produtos SET nome=?, categoria=?, marca=?, curva=?, status=?"
                            " WHERE sku=?", (*dados[1:], sku))
                atualizados += 1
            else:
                con.execute("INSERT INTO produtos (sku, nome, categoria, marca, curva, status)"
                            " VALUES (?,?,?,?,?,?)", dados)
                novos += 1

        custos = base["Custos_Margens"].set_index(["SKU", "Canal"])
        for r in base["Precos_Atuais"].to_dict("records"):
            sku, canal = str(r["SKU"]), str(r["Canal"])
            preco = float(r["Preço atual"])
            try:
                c = custos.loc[(sku, canal)]
            except KeyError:
                raise ErroErp("Custos_Margens não tem a linha %s · %s." % (sku, canal))

            anterior = con.execute(
                "SELECT preco FROM estado_canal WHERE sku=? AND canal=?", (sku, canal)).fetchone()
            data_ref = _data_texto(r.get("Data referência"))

            con.execute("""
                INSERT INTO estado_canal (sku, canal, preco, custo_rep, impostos, frete,
                                          taxa_canal, custo_total, margem_min, margem_alvo, data_ref)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(sku, canal) DO UPDATE SET
                    preco=excluded.preco, custo_rep=excluded.custo_rep,
                    impostos=excluded.impostos, frete=excluded.frete,
                    taxa_canal=excluded.taxa_canal, custo_total=excluded.custo_total,
                    margem_min=excluded.margem_min, margem_alvo=excluded.margem_alvo,
                    data_ref=excluded.data_ref
            """, (sku, canal, preco, float(c["Custo reposição"]),
                  float(c.get("Impostos estimados", 0) or 0), float(c.get("Frete rateado", 0) or 0),
                  float(c.get("Taxa canal", 0) or 0), float(c["Custo total referência"]),
                  float(c["Margem mínima"]), float(c["Margem alvo"]), data_ref))

            if anterior is None:
                con.execute("INSERT INTO historico_preco (sku, canal, data, preco_antigo,"
                            " preco_novo, tipo, origem, motivo) VALUES (?,?,?,?,?,?,?,?)",
                            (sku, canal, data_ref or _data_texto(datetime.now()), None, preco,
                             "INICIAL", "ERP_SYNC", "Registro inicial"))
            elif abs(float(anterior["preco"]) - preco) > 0.005:
                con.execute("INSERT INTO historico_preco (sku, canal, data, preco_antigo,"
                            " preco_novo, tipo, origem, motivo) VALUES (?,?,?,?,?,?,?,?)",
                            (sku, canal, data_ref or _data_texto(datetime.now()),
                             float(anterior["preco"]), preco,
                             "AUMENTO" if preco > anterior["preco"] else "REDUCAO",
                             "ERP_SYNC", "Preço alterado no ERP"))
                precos_alterados += 1

        def _int(v, padrao=0):
            try:
                return int(float(v))
            except (TypeError, ValueError):
                return padrao

        for r in base["Estoque"].to_dict("records"):
            con.execute("""
                INSERT INTO estoque (sku, quantidade, cobertura_erp, pedido_aberto, previsao, local)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(sku) DO UPDATE SET
                    quantidade=excluded.quantidade, cobertura_erp=excluded.cobertura_erp,
                    pedido_aberto=excluded.pedido_aberto, previsao=excluded.previsao,
                    local=excluded.local
            """, (str(r["SKU"]), _int(r.get("Estoque atual")),
                  float(r.get("Cobertura dias") or 0), _int(r.get("Pedido compra aberto")),
                  _data_texto(r.get("Previsão recebimento")), str(r.get("Local") or "")))

        # Histórico vindo do ERP (a aba não traz canal: é o preço de loja física).
        if "Historico_Precos" in base:
            ja = con.execute("SELECT COUNT(*) FROM historico_preco WHERE origem='ERP_HISTORICO'"
                             ).fetchone()[0]
            if ja == 0:
                h = base["Historico_Precos"].copy()
                h["_d"] = pd.to_datetime(h["Data"], dayfirst=True, errors="coerce")
                h = h.sort_values(["SKU", "_d"])
                anterior_por_sku: dict[str, float] = {}
                for r in h.to_dict("records"):
                    sku, preco = str(r["SKU"]), float(r["Preço"])
                    ant = anterior_por_sku.get(sku)
                    con.execute("INSERT INTO historico_preco (sku, canal, data, preco_antigo,"
                                " preco_novo, tipo, origem, motivo) VALUES (?,?,?,?,?,?,?,?)",
                                (sku, "Loja física", _data_texto(r["Data"]), ant, preco,
                                 "INICIAL" if ant is None else
                                 ("AUMENTO" if preco > ant else "REDUCAO"),
                                 "ERP_HISTORICO", str(r.get("Motivo") or "")))
                    anterior_por_sku[sku] = preco

        total = con.execute("SELECT COUNT(*) FROM produtos").fetchone()[0]
        dur = round(time.perf_counter() - t0, 2)
        msg = "%d produtos lidos · %d novo(s) · %d preço(s) alterado(s)" % (
            total, novos, precos_alterados)
        con.execute(
            "INSERT INTO sync_erp (quando, origem, usuario, status, fonte, produtos, novos,"
            " atualizados, precos_alterados, duracao_s, mensagem)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), rotulo_origem, usuario, "sucesso",
             fonte, total, novos, atualizados, precos_alterados, dur, msg))

    return {"produtos": total, "novos": novos, "atualizados": atualizados,
            "precos_alterados": precos_alterados, "duracao_s": dur, "mensagem": msg}


def situacao() -> dict:
    """Resumo da integração, para a tela de ERP e o painel inicial."""
    with sessao() as con:
        ultima = con.execute(
            "SELECT * FROM sync_erp ORDER BY id DESC LIMIT 1").fetchone()
        ultimo_sucesso = con.execute(
            "SELECT * FROM sync_erp WHERE status='sucesso' ORDER BY id DESC LIMIT 1").fetchone()
        return {
            "produtos": con.execute("SELECT COUNT(*) FROM produtos").fetchone()[0],
            "precos_por_canal": con.execute("SELECT COUNT(*) FROM estado_canal").fetchone()[0],
            "alteracoes_registradas": con.execute(
                "SELECT COUNT(*) FROM historico_preco").fetchone()[0],
            "data_referencia": con.execute(
                "SELECT MAX(data_ref) FROM estado_canal").fetchone()[0],
            "ultima": dict(ultima) if ultima else None,
            "ultimo_sucesso": dict(ultimo_sucesso) if ultimo_sucesso else None,
            "historico": [dict(r) for r in con.execute(
                "SELECT * FROM sync_erp ORDER BY id DESC LIMIT 20")],
        }
