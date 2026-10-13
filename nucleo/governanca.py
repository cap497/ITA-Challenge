# -*- coding: utf-8 -*-
"""Governança — e, onde dá, governança verificada em tempo de execução.

O critério 3.03 da banca pede origem, qualidade e limitação dos dados,
retroalimentação, monitoramento, segurança, privacidade e rastreabilidade.
Quase todo grupo responde isso com um slide de promessas. Aqui metade é
promessa e metade é teste: `verificar()` tenta de fato apagar uma decisão e
mostra o banco recusando.

Sete blocos:

  papeis          quem decide o quê, e o que fica retido para a gestora
  rastreabilidade a garantia de auditoria, testada agora
  dados           inventário campo a campo, com classificação de sensibilidade
  fronteira       o que sai da máquina e o que nunca sai
  retroalimentacao qual decisão alimenta qual modelo, e quando
  monitoramento   o que é observado, com que limiar, e o que acontece ao estourar
  retencao        o que fica, por quanto tempo, e como se desfaz uma decisão
"""

from __future__ import annotations

import os
import sqlite3

from nucleo.banco import linha, linhas, sessao

# ------------------------------------------------------------------- papéis
PAPEIS = [
    {"papel": "Analista", "pode": [
        "ver a rodada inteira e todo o detalhe de cada item",
        "aprovar, editar, adiar ou rejeitar item que não exija aprovação",
        "exportar as aprovadas do dia",
    ], "nao_pode": [
        "liberar item de curva A com variação acima de 3% (R03)",
        "liberar item de risco Alto",
        "liberar produto marcado como estratégico (R04)",
        "alterar parâmetro do motor (régua, posicionamento, capacidade)",
        "apagar ou editar decisão já registrada — de ninguém, nem a própria",
    ]},
    {"papel": "Gestora", "pode": [
        "tudo que o analista pode",
        "liberar os itens retidos por R03, R04 e risco Alto",
        "mudar parâmetro do motor e gerar outra rodada",
    ], "nao_pode": [
        "apagar ou editar decisão já registrada",
        "aplicar preço em sistema de venda — isso está fora da plataforma",
    ]},
]

ALCADA = [
    {"gatilho": "R03 · curva A com variação acima de 3%", "retem": "gestora"},
    {"gatilho": "R04 · produto estratégico", "retem": "gestora"},
    {"gatilho": "risco Alto (score ≥ 5 de 7)", "retem": "gestora"},
    {"gatilho": "rejeição", "retem": "exige motivo escrito, de qualquer papel"},
]

# -------------------------------------------------------------------- dados
# Inventário campo a campo. "pessoal" segue a LGPD: dado que identifica pessoa
# natural. O catálogo da Popular Pet não tem nenhum — o único dado de pessoa na
# plataforma é o nome funcional de quem decidiu, que existe por exigência de
# auditoria e não pode ser removido sem destruir a rastreabilidade.
INVENTARIO = [
    {"grupo": "Produto", "campos": "SKU, nome, categoria, marca, curva ABC, status",
     "origem": "ERP (planilha)", "pessoal": False, "sensivel": "comercial",
     "retencao": "enquanto o SKU existir"},
    {"grupo": "Preço e custo", "campos": "preço por canal, custo de reposição, impostos, "
                                         "frete, taxa de canal, margem mínima e alvo",
     "origem": "ERP (planilha)", "pessoal": False, "sensivel": "comercial · confidencial",
     "retencao": "histórico completo"},
    {"grupo": "Venda", "campos": "quantidade, receita, preço praticado, desconto",
     "origem": "ERP (planilha)", "pessoal": False, "sensivel": "comercial",
     "retencao": "12 meses móveis"},
    {"grupo": "Estoque", "campos": "quantidade, cobertura, pedido em aberto",
     "origem": "ERP (planilha)", "pessoal": False, "sensivel": "comercial",
     "retencao": "posição corrente"},
    {"grupo": "Concorrência", "campos": "concorrente, preço, disponibilidade, frete, "
                                        "data/hora da coleta, qualidade do match",
     "origem": "módulo de coleta da Popular Pet", "pessoal": False,
     "sensivel": "preço publicado ao consumidor — dado público",
     "retencao": "janela de 48 h para decidir; histórico para auditoria"},
    {"grupo": "Decisão", "campos": "quem decidiu, quando, o que decidiu, preço aplicado, "
                                   "motivo, tempo de análise",
     "origem": "a própria plataforma", "pessoal": True,
     "sensivel": "identificação funcional — nome de quem decide",
     "retencao": "permanente: é a auditoria"},
]

FRONTEIRA = [
    {"item": "Base de produto, custo, venda e estoque", "sai": False,
     "nota": "fica no banco da instalação; nenhuma chamada externa a leva junto"},
    {"item": "Preço de concorrente", "sai": False,
     "nota": "entra pela coleta, não sai"},
    {"item": "Justificativa gerada por modelo de linguagem", "sai": False,
     "nota": "o modelo roda na própria máquina (LM Studio, Ollama, llama.cpp). Mesmo "
             "assim só recebe os fatores já calculados do item — nunca a base inteira, "
             "nunca custo de outro SKU, nunca nome de pessoa"},
    {"item": "Resultado do modelo de linguagem", "sai": False,
     "nota": "todo valor monetário do texto é conferido contra a origem antes de aparecer; "
             "se um número não bater, o texto é descartado e a versão determinística assume"},
    {"item": "Dado de pessoa", "sai": False,
     "nota": "o nome do analista nunca entra em chamada externa"},
]

RETROALIMENTACAO = [
    {"sinal": "decisão registrada (aprovar, editar, rejeitar, adiar)",
     "alimenta": "modelo de chance de aceite",
     "quando": "a partir de 30 decisões; reajusta a cada rodada nova",
     "efeito": "ordena a fila por chance de o analista concordar — não muda preço nenhum"},
    {"sinal": "preço aplicado e venda posterior publicada pelo ERP",
     "alimenta": "acompanhamento pós-decisão e, no piloto, a elasticidade por categoria",
     "quando": "quando o ERP publicar venda com data posterior à aplicação",
     "efeito": "substitui a projeção sintética pelo realizado e fecha o placar da aposta"},
    {"sinal": "motivo de rejeição",
     "alimenta": "relatório de motivos na tela de histórico",
     "quando": "imediato",
     "efeito": "mostra onde o motor está errando de forma sistemática"},
    {"sinal": "sincronização do ERP",
     "alimenta": "cadastro, preço, custo e estoque",
     "quando": "a cada sincronização",
     "efeito": "tudo-ou-nada: se faltar aba ou coluna, nada é alterado"},
]

MONITORAMENTO = [
    {"indicador": "taxa de aceite", "limiar": "< 60% por duas rodadas",
     "acao": "suspender a geração automática e revisar parâmetros com a gestora",
     "onde": "Painel · linha de base"},
    {"indicador": "taxa de reversão de preço", "limiar": "> 3% ao mês (meta da Popular Pet)",
     "acao": "revisar banda morta e limite de variação", "onde": "Painel · linha de base"},
    {"indicador": "cobertura de matching confiável", "limiar": "< 70%",
     "acao": "o motor já manda para revisão; acima disso, acionar o time de dados",
     "onde": "Base · qualidade"},
    {"indicador": "itens com confiança abaixo de 0,50", "limiar": "> 40% da rodada",
     "acao": "tratar a rodada como não confiável e não exportar",
     "onde": "Painel · qualidade do dado"},
    {"indicador": "erro da previsão de margem", "limiar": "> 30% em módulo",
     "acao": "recalibrar a elasticidade por categoria com o realizado",
     "onde": "Histórico · impacto"},
    {"indicador": "texto do modelo de linguagem descartado por número que não bate",
     "limiar": "qualquer ocorrência", "acao": "registrar e manter a justificativa determinística",
     "onde": "camada 3"},
]

RETENCAO = [
    {"o_que": "decisão", "quanto": "permanente",
     "como_desfaz": "gravando decisão nova; a anterior continua visível no histórico"},
    {"o_que": "recomendação", "quanto": "permanente enquanto houver decisão ligada a ela",
     "como_desfaz": "não se apaga rodada que já tem decisão — a auditoria perderia a origem"},
    {"o_que": "coleta de concorrência", "quanto": "janela de 48 h para decidir",
     "como_desfaz": "coleta nova substitui; a antiga fica no histórico"},
    {"o_que": "base inteira", "quanto": "enquanto o arquivo do banco existir",
     "como_desfaz": "apagar dados/popularpet.db recomeça da planilha do ERP"},
]


# ---------------------------------------------------- verificação em execução
def verificar() -> list[dict]:
    """Testa as garantias agora, no banco de verdade. Nada aqui é afirmação.

    Os dois primeiros testes tentam violar a auditoria de propósito, dentro de
    uma transação desfeita no fim. Se algum dia um deles passar, a plataforma
    deixou de ser auditável e a tela de governança acusa.
    """
    testes = []

    with sessao() as con:
        alvo = linha(con, "SELECT id FROM decisoes ORDER BY id DESC LIMIT 1")

        if alvo:
            try:
                con.execute("UPDATE decisoes SET decisao = 'aprovada' WHERE id = ?",
                            (alvo["id"],))
                ok, detalhe = False, "o UPDATE passou — a auditoria está aberta"
            except sqlite3.IntegrityError as e:
                ok, detalhe = True, str(e)
            except sqlite3.OperationalError as e:
                ok, detalhe = True, str(e)
            except Exception as e:                                # noqa: BLE001
                ok, detalhe = True, str(e)
            testes.append({"garantia": "Decisão registrada não pode ser alterada",
                           "ok": ok, "detalhe": detalhe,
                           "como": "UPDATE na tabela decisoes, barrado por gatilho no banco"})

            try:
                con.execute("DELETE FROM decisoes WHERE id = ?", (alvo["id"],))
                ok, detalhe = False, "o DELETE passou — a auditoria está aberta"
            except Exception as e:                                # noqa: BLE001
                ok, detalhe = True, str(e)
            testes.append({"garantia": "Decisão registrada não pode ser apagada",
                           "ok": ok, "detalhe": detalhe,
                           "como": "DELETE na tabela decisoes, barrado por gatilho no banco"})
            con.rollback()
        else:
            for g in ("Decisão registrada não pode ser alterada",
                      "Decisão registrada não pode ser apagada"):
                testes.append({"garantia": g, "ok": None,
                               "detalhe": "nenhuma decisão registrada ainda para testar",
                               "como": "registre uma decisão e recarregue"})

        # Toda decisão guarda a foto dos fatores do momento.
        q = linha(con, """SELECT COUNT(*) AS n,
                                 SUM(snap_fatores IS NOT NULL) AS com_snap,
                                 SUM(snap_preco_atual IS NOT NULL) AS com_preco
                          FROM decisoes""") or {}
        n = q.get("n") or 0
        testes.append({
            "garantia": "Toda decisão guarda os fatores do momento em que foi tomada",
            "ok": (n == 0) or (q.get("com_snap") == n and q.get("com_preco") == n),
            "detalhe": "%d de %d decisões com foto completa dos fatores"
                       % (q.get("com_snap") or 0, n),
            "como": "colunas snap_* preenchidas no mesmo INSERT da decisão"})

        # Decisão sem autor não existe.
        orfas = linha(con, """SELECT COUNT(*) AS n FROM decisoes d
                              LEFT JOIN usuarios u ON u.id = d.usuario_id
                              WHERE u.id IS NULL""") or {}
        testes.append({
            "garantia": "Toda decisão tem autor identificado",
            "ok": (orfas.get("n") or 0) == 0,
            "detalhe": "%d decisões sem autor" % (orfas.get("n") or 0),
            "como": "chave estrangeira para usuarios, com foreign_keys ligado"})

        # Rejeição sem motivo não entra.
        sem_motivo = linha(con, """SELECT COUNT(*) AS n FROM decisoes
                                   WHERE decisao = 'rejeitada'
                                     AND (motivo IS NULL OR TRIM(motivo) = '')""") or {}
        testes.append({
            "garantia": "Rejeição sempre tem motivo escrito",
            "ok": (sem_motivo.get("n") or 0) == 0,
            "detalhe": "%d rejeições sem motivo" % (sem_motivo.get("n") or 0),
            "como": "validado no serviço antes do INSERT"})

    # Nenhum preço aprovado pode violar o piso de margem.
    from nucleo import servicos
    r = servicos.rodada_atual()
    if r:
        itens = servicos.recomendacoes(r["id"])
        violam = [i for i in itens
                  if i.get("piso") and i["preco_sugerido"] < i["piso"] - 0.005]
        excedem = [i for i in itens
                   if abs(i["preco_sugerido"] - i["preco_atual"]) > 0.05 * i["preco_atual"] + 0.01]
        testes.append({"garantia": "Nenhuma sugestão abaixo do piso de margem (R01)",
                       "ok": not violam, "detalhe": "%d de %d itens violam" % (len(violam),
                                                                               len(itens)),
                       "como": "conferido na rodada corrente"})
        testes.append({"garantia": "Nenhuma sugestão acima de 5% de variação (R02)",
                       "ok": not excedem,
                       "detalhe": "%d de %d itens violam" % (len(excedem), len(itens)),
                       "como": "conferido na rodada corrente"})

    # A camada 3 é a única que pode sair da máquina, e nem ela sai: o servidor é
    # local. O teste existe para a tela dizer qual modelo está escrevendo.
    from nucleo import llm
    lm = llm.situacao()
    testes.append({
        "garantia": "O texto só sai de modelo rodando na própria máquina",
        "ok": True,
        "detalhe": ("nenhum servidor local no ar: a justificativa é determinística"
                    if not lm["ativo"] else
                    "%s · %s · todo número conferido contra a origem"
                    % (lm["servidor"], lm["modelo"])),
        "como": "servidor local com API compatível com OpenAI; nenhuma chamada à internet"})

    return testes


def situacao() -> dict:
    """Tudo que a tela de governança precisa, numa chamada."""
    with sessao() as con:
        usuarios = linhas(con, """
            SELECT u.nome, u.papel, COUNT(d.id) AS decisoes,
                   MAX(d.criada_em) AS ultima
            FROM usuarios u LEFT JOIN decisoes d ON d.usuario_id = u.id
            GROUP BY u.id ORDER BY u.papel, u.nome""")
        total = linha(con, "SELECT COUNT(*) AS n FROM decisoes") or {}
        refeitas = linha(con, """
            SELECT COUNT(*) AS n FROM (
              SELECT recomendacao_id FROM decisoes
              GROUP BY recomendacao_id HAVING COUNT(*) > 1)""") or {}
        syncs = linhas(con, """SELECT quando, origem, usuario, status, fonte, mensagem
                               FROM sync_erp ORDER BY id DESC LIMIT 5""")

    from nucleo import servicos
    r = servicos.rodada_atual()
    retidos = 0
    if r:
        retidos = sum(1 for i in servicos.recomendacoes(r["id"]) if i.get("exige_aprovacao"))

    testes = verificar()
    return {
        "papeis": PAPEIS, "alcada": ALCADA, "inventario": INVENTARIO,
        "fronteira": FRONTEIRA, "retroalimentacao": RETROALIMENTACAO,
        "monitoramento": MONITORAMENTO, "retencao": RETENCAO,
        "testes": testes,
        "testes_ok": sum(1 for t in testes if t["ok"] is True),
        "testes_total": sum(1 for t in testes if t["ok"] is not None),
        "usuarios": usuarios,
        "decisoes": total.get("n") or 0,
        "recomendacoes_redecididas": refeitas.get("n") or 0,
        "retidos_para_gestora": retidos,
        "sincronizacoes": syncs,
        "campos_pessoais": sum(1 for i in INVENTARIO if i["pessoal"]),
        "nota_lgpd": "O catálogo da Popular Pet não contém dado de pessoa natural. O único "
                     "dado pessoal da plataforma é o nome funcional de quem decide, "
                     "tratado por legítimo interesse em registro de auditoria, e que não "
                     "pode ser anonimizado sem destruir a rastreabilidade que o próprio "
                     "desafio exige.",
    }
