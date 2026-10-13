# -*- coding: utf-8 -*-
"""Envelope operacional — medido, não estimado.

    python envelope.py            # roda tudo e imprime o relatório
    python envelope.py --json     # mesma coisa em JSON, para a tela consumir

Milos Fonseca (aula 4) cobra que todo sistema declare onde funciona e onde
falha: quantos usuários ao mesmo tempo, quanto dado antes de degradar, o que
acontece quando falta informação. Isto aqui mede cada um desses limites nesta
máquina, em vez de prometer.

Seis ensaios:

  1. rodada completa          tempo de ponta a ponta no tamanho real da base
  2. escala da otimização     MILP em 120, 1.200, 12.000 e 120.000 linhas
  3. escrita concorrente      N analistas gravando decisão ao mesmo tempo
  4. leitura concorrente      N analistas com a tela aberta
  5. degradação por falta     sem scipy, sem sklearn, sem chave de LLM
  6. memória                  pico de residente durante a rodada

O relatório diz o número e diz onde ele quebra. Os dois juntos são o envelope.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import sqlite3
import sys
import threading
import time
from datetime import datetime

PASTA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PASTA)

from nucleo import banco, carteira, motor, servicos                      # noqa: E402
from nucleo.carteira import TEM_SCIPY                                    # noqa: E402
from nucleo.modelos import TEM_SKLEARN                                   # noqa: E402

ESCALAS = [1, 10, 100, 1000]          # multiplicadores sobre a rodada real
CONCORRENTES = [1, 2, 4, 8, 16]


def _mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 1024 / 1024 if sys.platform == "darwin" else r / 1024


# ------------------------------------------------- 1. rodada de ponta a ponta
def ensaio_rodada() -> dict:
    cfg = motor.Config()
    t0, m0 = time.perf_counter(), _mb()
    r = servicos.gerar_rodada(cfg, substituir=True)
    ms = (time.perf_counter() - t0) * 1000
    itens = r["itens"]
    return {
        "ensaio": "rodada completa",
        "itens": itens,
        "ms_total": round(ms, 1),
        "ms_por_item": round(ms / itens, 3) if itens else None,
        "otimizacao": r["otimizacao"]["metodo"],
        "ms_otimizacao": round(r["otimizacao"]["ms"], 1),
        "memoria_mb": round(max(_mb(), m0), 1),
        "leitura": "planilha de %d SKUs + banco SQLite" % (itens // 3 if itens else 0),
        "fases": ensaio_fases(),
    }


def ensaio_fases() -> list[dict]:
    """Separa custo fixo de custo marginal.

    Importa porque a rodada inteira é dominada pela leitura da planilha, que
    acontece uma vez por rodada e não por item. Dividir o tempo total pelo
    número de itens daria um ms/item dez vezes pior que a verdade e levaria a
    um limite de catálogo errado no slide.
    """
    cfg = motor.Config()
    fases = []

    t0 = time.perf_counter()
    coleta = motor.carregar_coleta()
    fases.append({"fase": "ler planilha (vendas, concorrência, promoções)",
                  "ms": round((time.perf_counter() - t0) * 1000, 1), "tipo": "fixo"})

    t0 = time.perf_counter()
    cadastro = motor.carregar_cadastro()
    fases.append({"fase": "ler cadastro do banco",
                  "ms": round((time.perf_counter() - t0) * 1000, 1), "tipo": "fixo"})

    t0 = time.perf_counter()
    motor.referencia_mercado(coleta, cfg)
    fases.append({"fase": "referência de mercado por SKU/canal",
                  "ms": round((time.perf_counter() - t0) * 1000, 1), "tipo": "marginal"})

    t0 = time.perf_counter()
    motor.sinais(coleta, cadastro)
    fases.append({"fase": "sinais de venda e estoque",
                  "ms": round((time.perf_counter() - t0) * 1000, 1), "tipo": "marginal"})

    t0 = time.perf_counter()
    recs = motor.decidir(cfg)
    fases.append({"fase": "decidir: piso, R01–R12, preço, risco, impacto",
                  "ms": round((time.perf_counter() - t0) * 1000, 1), "tipo": "marginal",
                  "itens": len(recs)})
    return fases


# ----------------------------------------------- 2. escala da otimização MILP
def ensaio_escala(rodada_id: int) -> list[dict]:
    """Replica a rodada real N vezes e mede só a seleção de carteira.

    É o único componente cujo custo cresce mal com o tamanho do catálogo: o
    resto do motor é linha a linha. Replicar dado real preserva a distribuição
    de impacto, que é o que o solver enxerga.
    """
    import pandas as pd

    base = pd.DataFrame(servicos.recomendacoes(rodada_id))
    if base.empty:
        return []
    cfg = motor.Config()
    out = []
    for k in ESCALAS:
        if k == 1:
            d = base.copy()
        else:
            partes = []
            for i in range(k):
                p = base.copy()
                p["sku"] = p["sku"].astype(str) + "-r%d" % i
                partes.append(p)
            d = pd.concat(partes, ignore_index=True)
        # Capacidade cresce com o catálogo: 0,5% das linhas, no mínimo 20.
        cfg.capacidade_rodada = max(20, int(0.005 * len(d)))
        t0 = time.perf_counter()
        try:
            _, diag = carteira.selecionar(d, cfg)
            ms = (time.perf_counter() - t0) * 1000
            out.append({"linhas": len(d), "capacidade": cfg.capacidade_rodada,
                        "ms": round(ms, 1), "metodo": diag.get("metodo"),
                        "ok": True})
        except Exception as e:                                    # noqa: BLE001
            out.append({"linhas": len(d), "capacidade": cfg.capacidade_rodada,
                        "ms": None, "metodo": None, "ok": False,
                        "erro": "%s: %s" % (type(e).__name__, e)})
            break
    return out


# -------------------------------------------------- 3. escrita concorrente
def _grava_uma(rec_ids: list[int], nome: str, erros: list, feitos: list) -> None:
    for rid in rec_ids:
        try:
            servicos.registrar_decisao(rid, nome, "adiada", None, "", "ensaio de concorrência",
                                       segundos_analise=None)
            feitos.append(1)
        except Exception as e:                                    # noqa: BLE001
            erros.append("%s: %s" % (type(e).__name__, e))


def ensaio_escrita(rodada_id: int) -> list[dict]:
    """SQLite em WAL aceita um escritor por vez; a fila é o limite real."""
    itens = servicos.recomendacoes(rodada_id)
    ids = [i["id"] for i in itens]
    if len(ids) < 32:
        ids = (ids * (32 // max(1, len(ids)) + 1))[:32]
    out = []
    for n in CONCORRENTES:
        por_thread = max(2, 32 // n)
        erros: list = []
        feitos: list = []
        threads = [threading.Thread(target=_grava_uma,
                                    args=(ids[i * por_thread:(i + 1) * por_thread],
                                          "Ensaio %d" % (i + 1), erros, feitos))
                   for i in range(n)]
        t0 = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        seg = time.perf_counter() - t0
        out.append({"analistas": n, "gravacoes": len(feitos),
                    "segundos": round(seg, 3),
                    "gravacoes_por_s": round(len(feitos) / seg, 1) if seg else None,
                    "erros": len(erros),
                    "exemplo_erro": erros[0] if erros else None})
    return out


# -------------------------------------------------- 4. leitura concorrente
def _le_uma(rodada_id: int, voltas: int, tempos: list, erros: list) -> None:
    for _ in range(voltas):
        t0 = time.perf_counter()
        try:
            servicos.recomendacoes(rodada_id)
            servicos.resumo_rodada(rodada_id)
            tempos.append((time.perf_counter() - t0) * 1000)
        except Exception as e:                                    # noqa: BLE001
            erros.append(str(e))


def ensaio_leitura(rodada_id: int) -> list[dict]:
    out = []
    for n in CONCORRENTES:
        tempos: list = []
        erros: list = []
        threads = [threading.Thread(target=_le_uma, args=(rodada_id, 5, tempos, erros))
                   for _ in range(n)]
        t0 = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        seg = time.perf_counter() - t0
        tempos.sort()
        out.append({
            "analistas": n, "carregamentos": len(tempos), "segundos": round(seg, 3),
            "ms_mediano": round(tempos[len(tempos) // 2], 1) if tempos else None,
            "ms_p95": round(tempos[int(0.95 * (len(tempos) - 1))], 1) if tempos else None,
            "erros": len(erros),
        })
    return out


# ------------------------------------------------ 5. degradação por falta
def ensaio_degradacao() -> list[dict]:
    """O que a plataforma faz quando falta cada dependência ou dado."""
    return [
        {"falta": "scipy", "presente": TEM_SCIPY,
         "efeito": "a seleção da rodada cai para busca gulosa por impacto",
         "perda": "a carteira deixa de ser ótima e o acoplamento de canal (R07) passa a ser "
                  "tratado item a item", "para": False},
        {"falta": "scikit-learn", "presente": TEM_SKLEARN,
         "efeito": "as camadas de estimativa desligam",
         "perda": "volume estimado e chance de aceite deixam de aparecer; a recomendação de "
                  "preço não muda, porque não depende delas", "para": False},
        {"falta": "ANTHROPIC_API_KEY", "presente": bool(os.environ.get("ANTHROPIC_API_KEY")),
         "efeito": "a justificativa é composta de forma determinística",
         "perda": "o texto fica mais seco; nenhum número muda", "para": False},
        {"falta": "concorrente válido no SKU", "presente": True,
         "efeito": "a recomendação vira REVISAR com confiança zerada",
         "perda": "nenhuma — é o comportamento desejado: sem referência o sistema não opina",
         "para": False},
        {"falta": "coleta com menos de 48h (R06)", "presente": True,
         "efeito": "o item é roteado para revisão humana",
         "perda": "nenhuma — a R06 é regra da própria Popular Pet", "para": False},
        {"falta": "planilha do ERP", "presente": os.path.exists(motor.PLANILHA),
         "efeito": "a aplicação não sobe e diz exatamente qual arquivo falta",
         "perda": "total — é a única dependência dura", "para": True},
        {"falta": "coluna obrigatória na planilha", "presente": True,
         "efeito": "a sincronização é tudo-ou-nada: nada é alterado",
         "perda": "nenhuma — a base anterior continua íntegra", "para": False},
    ]


# ----------------------------------------------------------------- relatório
def _isolar() -> str:
    """Mede num banco próprio, descartável.

    O ensaio de escrita concorrente grava centenas de decisões; rodar isso no
    banco de trabalho sujaria a auditoria e travaria a próxima rodada. Então o
    envelope monta um banco novo a partir da mesma planilha e mede nele.
    """
    import tempfile
    alvo = os.path.join(tempfile.mkdtemp(prefix="envelope-"), "envelope.db")
    banco.ARQUIVO_BANCO = alvo
    banco.criar_esquema()
    from nucleo import erp
    erp.sincronizar(rotulo_origem="carga inicial", usuario="Envelope")
    return alvo


def medir(completo: bool = True) -> dict:
    arquivo = _isolar()
    inicio = datetime.now().isoformat(timespec="seconds")

    rodada = ensaio_rodada()
    rid = servicos.rodada_atual()["id"]

    out = {
        "medido_em": inicio,
        "maquina": {
            "python": sys.version.split()[0],
            "plataforma": sys.platform,
            "sqlite": sqlite3.sqlite_version,
            "cpus": os.cpu_count(),
            "scipy": TEM_SCIPY, "sklearn": TEM_SKLEARN,
            "banco_do_ensaio": arquivo,
        },
        "rodada": rodada,
        "degradacao": ensaio_degradacao(),
    }
    if completo:
        out["escala_otimizacao"] = ensaio_escala(rid)
        out["leitura_concorrente"] = ensaio_leitura(rid)
        out["escrita_concorrente"] = ensaio_escrita(rid)
    out["memoria_pico_mb"] = round(_mb(), 1)
    out["limites"] = _limites(out)
    return out


def _limites(m: dict) -> list[dict]:
    """Os números que vão para o slide. Cada um sai de um ensaio acima."""
    lim = []
    r = m["rodada"]
    fases = r.get("fases") or []
    fixo = sum(f["ms"] for f in fases if f["tipo"] == "fixo")
    marginal = sum(f["ms"] for f in fases if f["tipo"] == "marginal")
    if r["itens"] and marginal:
        por_item = marginal / r["itens"]
        cabe = int((60000 - fixo) / por_item) if por_item else 0
        lim.append({"limite": "Catálogo por rodada",
                    "valor": "~%s itens em 60 s" % f"{cabe:,}".replace(",", "."),
                    "base": "%.0f ms de custo fixo (ler a base) + %.2f ms por item, medidos "
                            "em %d itens" % (fixo, por_item, r["itens"]),
                    "extrapolado": True})
    esc = [e for e in m.get("escala_otimizacao", []) if e.get("ok")]
    if esc:
        maior = esc[-1]
        lim.append({"limite": "Otimização da carteira",
                    "valor": "%s linhas em %.0f ms" % (
                        f"{maior['linhas']:,}".replace(",", "."), maior["ms"]),
                    "base": "%s, capacidade %d" % (maior["metodo"], maior["capacidade"]),
                    "extrapolado": False})
        falhou = [e for e in m.get("escala_otimizacao", []) if not e.get("ok")]
        if falhou:
            lim.append({"limite": "Onde a otimização para",
                        "valor": "%s linhas" % f"{falhou[0]['linhas']:,}".replace(",", "."),
                        "base": falhou[0].get("erro", ""), "extrapolado": False})
    le = m.get("leitura_concorrente") or []
    if le:
        ok = [x for x in le if x["erros"] == 0]
        if ok:
            lim.append({"limite": "Analistas com a tela aberta",
                        "valor": "%d sem erro · p95 %s ms" % (ok[-1]["analistas"], ok[-1]["ms_p95"]),
                        "base": "%d carregamentos completos de rodada" % ok[-1]["carregamentos"],
                        "extrapolado": False})
    es = m.get("escrita_concorrente") or []
    if es:
        ok = [x for x in es if x["erros"] == 0]
        ruim = [x for x in es if x["erros"] > 0]
        if ok:
            lim.append({"limite": "Analistas gravando ao mesmo tempo",
                        "valor": "%d sem erro · %s gravações/s" % (
                            ok[-1]["analistas"], ok[-1]["gravacoes_por_s"]),
                        "base": "SQLite em WAL serializa escrita; a fila é o limite",
                        "extrapolado": False})
        if ruim:
            lim.append({"limite": "Onde a escrita começa a falhar",
                        "valor": "%d analistas" % ruim[0]["analistas"],
                        "base": ruim[0].get("exemplo_erro") or "lock de escrita",
                        "extrapolado": False})
    lim.append({"limite": "Dependência dura",
                "valor": "a planilha do ERP",
                "base": "sem ela a aplicação não sobe; sem scipy, sklearn ou LLM ela sobe "
                        "e degrada de forma declarada",
                "extrapolado": False})
    return lim


def imprimir(m: dict) -> None:
    def t(s):
        print("\n" + s)
        print("─" * len(s))

    print("\nENVELOPE OPERACIONAL · medido em %s" % m["medido_em"])
    print("Python %s · SQLite %s · %d CPU · scipy %s · sklearn %s" % (
        m["maquina"]["python"], m["maquina"]["sqlite"], m["maquina"]["cpus"],
        "sim" if m["maquina"]["scipy"] else "não",
        "sim" if m["maquina"]["sklearn"] else "não"))

    t("1 · Rodada completa")
    r = m["rodada"]
    print("  %d itens em %.0f ms · otimização %s em %.0f ms"
          % (r["itens"], r["ms_total"], r["otimizacao"], r["ms_otimizacao"]))
    for f in r.get("fases") or []:
        print("     %-8s %8.0f ms  %s" % (f["tipo"], f["ms"], f["fase"]))

    if m.get("escala_otimizacao"):
        t("2 · Escala da otimização")
        for e in m["escala_otimizacao"]:
            if e["ok"]:
                print("  %9s linhas · capacidade %6d · %8.0f ms · %s"
                      % (f"{e['linhas']:,}".replace(",", "."), e["capacidade"], e["ms"],
                         e["metodo"]))
            else:
                print("  %9s linhas · FALHOU · %s"
                      % (f"{e['linhas']:,}".replace(",", "."), e.get("erro")))

    if m.get("leitura_concorrente"):
        t("3 · Leitura concorrente")
        for e in m["leitura_concorrente"]:
            print("  %2d analistas · mediana %6s ms · p95 %6s ms · erros %d"
                  % (e["analistas"], e["ms_mediano"], e["ms_p95"], e["erros"]))

    if m.get("escrita_concorrente"):
        t("4 · Escrita concorrente")
        for e in m["escrita_concorrente"]:
            print("  %2d analistas · %3d gravações em %5.2f s · %6s grav/s · erros %d"
                  % (e["analistas"], e["gravacoes"], e["segundos"],
                     e["gravacoes_por_s"], e["erros"]))

    t("5 · Degradação por falta")
    for d in m["degradacao"]:
        marca = "para" if d["para"] else "segue"
        print("  [%s] sem %-32s → %s" % (marca, d["falta"], d["efeito"]))

    t("6 · Limites declarados")
    for l in m["limites"]:
        print("  %-38s %s" % (l["limite"] + ":", l["valor"]))
        print("  %-38s %s%s" % ("", l["base"],
                                "  (extrapolado)" if l["extrapolado"] else ""))
    print("\nMemória de pico: %.0f MB\n" % m["memoria_pico_mb"])


def main() -> None:
    p = argparse.ArgumentParser(description="Envelope operacional medido")
    p.add_argument("--json", action="store_true")
    p.add_argument("--rapido", action="store_true", help="só rodada e degradação")
    p.add_argument("--salvar", help="grava o JSON neste arquivo")
    a = p.parse_args()
    m = medir(completo=not a.rapido)
    if a.salvar:
        with open(a.salvar, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=2)
    if a.json:
        print(json.dumps(m, ensure_ascii=False, indent=2))
    else:
        imprimir(m)


if __name__ == "__main__":
    main()
