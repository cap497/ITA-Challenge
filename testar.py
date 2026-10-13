# -*- coding: utf-8 -*-
"""Verificação automática — `python testar.py`.

Confere que o motor respeita as regras duras, que a auditoria é imutável e que
as contas batem. Não precisa de pytest nem de servidor rodando.
"""

from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nucleo import banco, erp, motor, servicos  # noqa: E402

falhas: list[str] = []


def checar(condicao: bool, descricao: str, detalhe: str = "") -> None:
    print(("  ok    " if condicao else "  FALHA ") + descricao + (" — " + detalhe if detalhe else ""))
    if not condicao:
        falhas.append(descricao)


def isolar() -> str:
    """Testa num banco descartável, montado da mesma planilha.

    Os testes gravam decisões de propósito — inclusive tentativas que têm de ser
    recusadas. Rodar isso no banco de trabalho sujava a auditoria e deixava
    registros inventados aparecendo nas telas como se fossem decisões reais.
    """
    import tempfile
    alvo = os.path.join(tempfile.mkdtemp(prefix="testar-"), "teste.db")
    banco.ARQUIVO_BANCO = alvo
    banco.criar_esquema()
    erp.sincronizar(rotulo_origem="carga inicial", usuario="Teste")
    from nucleo import semente
    semente.marcar_estrategicos_demo()
    return alvo


def main() -> int:
    print("\nPreparando base de teste…")
    isolar()
    rodadas = servicos.listar_rodadas()
    if not rodadas:
        servicos.gerar_rodada(motor.Config())
        rodadas = servicos.listar_rodadas()
    rid = rodadas[0]["id"]
    itens = servicos.recomendacoes(rid)

    print("\nCobertura da rodada")
    checar(len(itens) == 120, "uma recomendação por SKU × canal", "%d itens" % len(itens))
    chaves = {(i["sku"], i["canal"]) for i in itens}
    checar(len(chaves) == len(itens), "sem SKU × canal duplicado")
    checar(all(i["recomendacao"] in ("SUBIR", "BAIXAR", "MANTER", "REVISAR") for i in itens),
           "toda recomendação é uma das quatro")

    print("\nRegras duras")
    fora_piso = [i for i in itens if i["recomendacao"] in ("SUBIR", "BAIXAR")
                 and i["preco_sugerido"] < i["piso"] - 0.005]
    checar(not fora_piso, "R01 · nenhuma sugestão abaixo do piso de margem",
           "%d violações" % len(fora_piso))

    fora_banda = [i for i in itens
                  if abs(i["preco_sugerido"] - i["preco_atual"]) > 0.05 * i["preco_atual"] + 0.01]
    checar(not fora_banda, "R02 · nenhuma variação acima de 5%", "%d violações" % len(fora_banda))

    # O que a R09 de fato garante agora: ninguém aplica sozinho. Não é que a
    # recomendação suma — ela existe e sobe para quem tem alçada.
    em_campanha = [i for i in itens if "R09" in (i.get("regras") or [])]
    solto = [i for i in em_campanha if (i.get("retencao") or "livre") == "livre"]
    checar(not solto, "R09 · SKU em campanha nunca é aplicado sem alçada",
           "%d em campanha, %d sem retenção" % (len(em_campanha), len(solto)))

    # R11 bloqueia redução com cobertura curta, com uma exceção: pedido de compra
    # chegando em até 7 dias. A falta é temporária e travar a decisão por ela
    # deixaria dinheiro na mesa. A exceção precisa estar no teste, senão o teste
    # vira um retrato da versão antiga da regra.
    reduz_sem_estoque = [i for i in itens if i["recomendacao"] == "BAIXAR"
                         and i["cobertura"] < 15
                         and not (i.get("pedido_aberto") or 0)]
    checar(not reduz_sem_estoque,
           "R11 · redução com cobertura curta só com pedido a caminho",
           "%d violações" % len(reduz_sem_estoque))

    # O inverso: cobertura curta e nada a caminho tem de estar travado mesmo.
    travados = [i for i in itens if i["cobertura"] < 15
                and not (i.get("pedido_aberto") or 0)
                and "R11" in (i.get("regras") or [])]
    sem_trava = [i for i in itens if i["cobertura"] < 15
                 and not (i.get("pedido_aberto") or 0)
                 and i["recomendacao"] == "BAIXAR"]
    checar(not sem_trava, "R11 · trava de fato quando não há reposição",
           "%d itens travados corretamente" % len(travados))

    # Pedido em aberto só conta se a previsão de recebimento for posterior à
    # data de referência da base. Previsão vencida não é estoque a caminho.
    projetada_menor = [i for i in itens
                       if (i.get("cobertura_projetada") or 0) < (i.get("cobertura") or 0) - 0.05]
    checar(not projetada_menor,
           "cobertura projetada nunca é menor que a física",
           "%d violações" % len(projetada_menor))

    # Margem efetiva usa o preço já descontado, então nunca pode ser maior que a
    # margem de tabela — se for, o sinal do desconto entrou invertido.
    margem_invertida = [i for i in itens
                        if (i.get("desconto_medio") or 0) > 0
                        and (i.get("margem_efetiva") or 0) > (i.get("margem_atual") or 0) + 1e-6]
    checar(not margem_invertida, "margem efetiva nunca supera a margem de tabela",
           "%d violações" % len(margem_invertida))

    # O preço entregue do concorrente é sempre maior ou igual ao publicado: se
    # ficou menor, o frete entrou com sinal trocado em algum lugar.
    entregue_menor = [i for i in itens
                      if i.get("preco_mercado_entregue") and i.get("preco_mercado")
                      and i["preco_mercado_entregue"] < i["preco_mercado"] - 0.01]
    checar(not entregue_menor, "preço entregue do concorrente nunca é menor que o publicado",
           "%d violações" % len(entregue_menor))

    # REVISAR só pode significar uma coisa: o motor não tem base para opinar.
    # Se um REVISAR vier com preço sugerido diferente do atual, o campo voltou a
    # ser lixeira — que é exatamente o que esta separação veio consertar.
    revisar_com_preco = [i for i in itens if i["recomendacao"] == "REVISAR"
                         and abs(i["preco_sugerido"] - i["preco_atual"]) > 0.005]
    checar(not revisar_com_preco, "REVISAR nunca sugere preço — é falta de base, não retenção",
           "%d violações" % len(revisar_com_preco))

    # E o espelho: item retido mantém a recomendação de preço. Campanha ativa
    # não apaga a opinião do motor, só adia a aplicação dela.
    retidos = [i for i in itens if (i.get("retencao") or "livre") != "livre"]
    retido_sem_opiniao = [i for i in retidos if i["recomendacao"] == "REVISAR"
                          and not (i.get("motivos_revisao") or [])]
    checar(not retido_sem_opiniao, "item retido mantém a recomendação de preço",
           "%d retidos, %d sem opinião" % (len(retidos), len(retido_sem_opiniao)))

    # A tabela de regras da Popular Pet distingue "Revisar" de "Bloquear", e a
    # plataforma tem de respeitar a diferença. R09 diz "não pode ser reprecificado
    # AUTOMATICAMENTE" — num sistema todo manual, isso quer dizer que a decisão
    # sobe de nível, não que ninguém pode mexer. Quem manda bloquear é a R08.
    campanha = [i for i in itens if "R09" in (i.get("regras") or [])]
    travados = [i for i in campanha if (i.get("retencao") or "") == "bloqueado"]
    checar(not travados, "R09 · campanha retém para a gestora, não bloqueia",
           "%d em campanha, %d bloqueados indevidamente" % (len(campanha), len(travados)))

    sem_contexto = [i for i in campanha if not (i.get("campanha") or {}).get("fim")]
    checar(not sem_contexto, "R09 · quem aprova vê qual campanha e até quando",
           "%d sem contexto de campanha" % len(sem_contexto))

    bloqueados = [i for i in itens if (i.get("retencao") or "") == "bloqueado"]
    so_r08 = [i for i in bloqueados if "R08" not in (i.get("regras") or [])]
    checar(not so_r08, "bloqueio só existe onde a regra manda bloquear (R08)",
           "%d bloqueados, %d por outra regra" % (len(bloqueados), len(so_r08)))

    # R04: estratégico entra na rodada mesmo sem estar entre os maiores impactos.
    estrategicos = [i for i in itens if i.get("estrategico")]
    muda_preco = [i for i in estrategicos if i["recomendacao"] in ("SUBIR", "BAIXAR")]
    fora = [i for i in muda_preco if not i.get("selecionado")]
    checar(not fora, "R04 · estratégico com mudança sempre entra na rodada",
           "%d estratégicos, %d com mudança, %d ficaram de fora"
           % (len(estrategicos), len(muda_preco), len(fora)))

    # R04: e sempre sobe para a gestora.
    solto = [i for i in estrategicos if (i.get("retencao") or "livre") == "livre"]
    checar(not solto, "R04 · estratégico nunca fica livre de alçada",
           "%d violações" % len(solto))

    # Alçada: não basta marcar na tela, o serviço tem de recusar.
    def _recusa(cond, usuario, titulo, **extra):
        alvo = next((i for i in itens if cond(i)), None)
        if alvo is None:
            checar(True, titulo, "nenhum caso nesta rodada")
            return
        try:
            servicos.registrar_decisao(alvo["id"], usuario, "aprovada", **extra)
            checar(False, titulo, "a aprovação passou e não deveria")
        except ValueError as e:
            checar(True, titulo, str(e)[:60])

    # A alçada saiu daqui de propósito: `registrar_decisao` grava PARECER, e
    # parecer todo analista pode dar em qualquer item — inclusive nos retidos,
    # que são justamente os que a gestora precisa ler antes de assinar. Quem
    # barra é a homologação, verificada no bloco "Câmara de precificação".
    retido = next((i for i in itens if (i.get("retencao") or "") == "aprovacao"), None)
    if retido is None:
        checar(True, "parecer · analista opina em item retido", "nenhum caso nesta rodada")
    else:
        try:
            servicos.registrar_decisao(retido["id"], "Ana Souza", "aprovada",
                                       comentario="parecer do analista")
            checar(True, "parecer · analista opina em item retido",
                   "item retido aceita parecer; o preço final ainda depende da homologação")
        except ValueError as e:
            checar(False, "parecer · analista opina em item retido", str(e)[:70])

    _recusa(lambda i: i.get("exige_justificativa"), "Carla Menezes",
            "R04 · aprovar estratégico sem justificativa é recusado")

    banda_morta = [i for i in itens if i["recomendacao"] in ("SUBIR", "BAIXAR")
                   and abs(i["variacao"]) < 0.02 - 1e-9]
    checar(not banda_morta, "banda morta · nenhuma alteração abaixo de 2%",
           "%d violações" % len(banda_morta))

    manter_sem_mudanca = [i for i in itens if i["recomendacao"] == "MANTER"
                          and abs(i["preco_sugerido"] - i["preco_atual"]) > 0.005]
    checar(not manter_sem_mudanca, "MANTER mantém o preço atual")

    print("\nContas")
    erros_margem = [i for i in itens if i["preco_sugerido"] > 0 and abs(
        i["margem_resultante"] - (i["preco_sugerido"] - i["custo"]) / i["preco_sugerido"]) > 0.001]
    checar(not erros_margem, "margem resultante bate com preço e custo",
           "%d divergências" % len(erros_margem))

    # Preço é gravado ao centavo e volume com uma casa, então a tolerância soma os
    # dois arredondamentos: meio centavo por unidade, mais meio décimo de unidade
    # multiplicado pela diferença de preço.
    erros_impacto = []
    for i in itens:
        delta = abs(i["preco_sugerido"] - i["preco_atual"])
        tolerancia = 0.005 * i["volume_mes"] + 0.05 * delta + 0.01
        if abs(i["impacto_mes"] - (i["preco_sugerido"] - i["preco_atual"])
               * i["volume_mes"]) > tolerancia:
            erros_impacto.append(i)
    checar(not erros_impacto, "impacto bate com variação × volume",
           "%d divergências" % len(erros_impacto))

    # Mesma razão: em item de preço muito baixo, meio centavo pesa muito na razão
    # entre margens unitárias, então a tolerância é relativa à margem por unidade.
    com_eq = [i for i in itens if i["equilibrio_volume"] is not None]
    erros_eq = []
    for i in com_eq:
        m_novo = i["preco_sugerido"] - i["custo"]
        calc = (i["preco_atual"] - i["custo"]) / m_novo - 1
        if abs(i["equilibrio_volume"] - calc) > 0.01 / max(m_novo, 0.01) + 0.0005:
            erros_eq.append(i)
    checar(not erros_eq, "ponto de equilíbrio bate com as margens unitárias",
           "%d de %d conferidos" % (len(erros_eq), len(com_eq)))

    print("\nJustificativa e explicabilidade")
    checar(all(i["justificativa"] for i in itens), "toda recomendação tem justificativa")
    revisar = [i for i in itens if i["recomendacao"] == "REVISAR"]
    checar(all(i["motivos_revisao"] for i in revisar),
           "todo REVISAR diz por quê", "%d itens" % len(revisar))

    print("\nCâmara de precificação")
    from nucleo import camara

    pa = camara.pauta(rid)
    checar(pa["total"] > 0, "a pauta do dia tem itens",
           "%d itens selecionados pelo otimizador" % pa["total"])
    checar(pa["total"] <= len(itens),
           "a pauta é um recorte, não a base inteira",
           "%d de %d pares SKU×canal — o resto segue a política de omissão"
           % (pa["total"], len(itens)))

    alvo = next((i for i in pa["itens"] if not i["bloqueado"]
                 and not i["exige_justificativa"] and not i["homologada"]
                 and i["recomendacao"] in ("SUBIR", "BAIXAR")), None)
    if alvo is None:
        checar(True, "câmara · fluxo de voto", "nenhum item elegível nesta rodada")
    else:
        item_id = alvo["recomendacao_id"]
        a = camara.apurar_rec(item_id)
        sist = next(c for c in a["candidatos"] if c["origem"] == "sistema")
        checar(len(a["candidatos"]) >= 2, "cédula nasce com preço de hoje e do motor",
               "%d candidatos" % len(a["candidatos"]))

        a = camara.votar(item_id, sist["id"], "Ana Souza")
        checar(a["fase"] == "em votacao" and not a["consenso"],
               "um voto só não fecha a câmara", a["falta"][:60])

        try:
            camara.homologar(item_id, "Ana Souza")
            checar(False, "analista não homologa", "a homologação passou e não deveria")
        except ValueError as e:
            checar(True, "analista não homologa", str(e)[:60])

        a = camara.votar(item_id, sist["id"], "Bruno Lima")
        checar(a["fase"] == "aguardando gestor" and a["consenso"] == sist["id"],
               "dois votos no mesmo preço fecham o consenso",
               "consenso em R$ %.2f" % a["preco_consenso"])

        h = camara.homologar(item_id, "Carla Menezes")
        checar(abs(h["preco_final"] - sist["preco"]) < 0.005,
               "o gestor homologa o preço apurado", "R$ %.2f" % h["preco_final"])

        try:
            camara.votar(item_id, sist["id"], "Ana Souza")
            checar(False, "votação encerra ao homologar", "o voto passou e não deveria")
        except ValueError as e:
            checar(True, "votação encerra ao homologar", str(e)[:60])

    # Empate: o gestor desempata, mas tem de escrever por quê.
    outro = next((i for i in camara.pauta(rid)["itens"]
                  if not i["bloqueado"] and not i["exige_justificativa"]
                  and not i["homologada"] and i["recomendacao"] in ("SUBIR", "BAIXAR")), None)
    if outro is None:
        checar(True, "câmara · empate sobe para o gestor", "sem item elegível")
    else:
        r2 = outro["recomendacao_id"]
        meio = round((outro["preco_atual"] + outro["preco_sugerido"]) / 2, 2)
        pr = camara.propor(r2, meio, "Bruno Lima", "meio passo")
        a2 = camara.apurar_rec(r2)
        camara.votar(r2, next(c["id"] for c in a2["candidatos"]
                              if c["origem"] == "sistema"), "Ana Souza")
        a2 = camara.votar(r2, pr["candidato_id"], "Bruno Lima")
        checar(a2["empate"] and a2["fase"] == "aguardando gestor",
               "divergência vira empate e sobe para o gestor", a2["falta"][:60])
        try:
            camara.homologar(r2, "Carla Menezes", pr["candidato_id"])
            checar(False, "desempatar exige justificativa escrita", "passou sem justificativa")
        except ValueError as e:
            checar(True, "desempatar exige justificativa escrita", str(e)[:60])
        camara.homologar(r2, "Carla Menezes", pr["candidato_id"], "concorrente não reagiu")

    # R01 não depende de ninguém ter prestado atenção: nem na cédula entra.
    qualquer = camara.pauta(rid)["itens"][0]
    try:
        camara.propor(qualquer["recomendacao_id"], 0.01, "Ana Souza")
        checar(False, "R01 · preço abaixo do piso não entra na cédula", "entrou")
    except ValueError as e:
        checar(True, "R01 · preço abaixo do piso não entra na cédula", str(e)[:60])

    # Política de omissão: quem troca é o gestor.
    try:
        camara.definir_politica("aplicar", "Ana Souza")
        checar(False, "política de omissão é do gestor", "o analista trocou")
    except ValueError as e:
        checar(True, "política de omissão é do gestor", str(e)[:60])
    checar(camara.definir_politica("aplicar", "Carla Menezes")["valor"] == "aplicar",
           "o gestor troca a política de omissão", "aplicar a recomendação")

    # O fechamento das 18h e o arquivo do ERP.
    f = camara.fechar_pauta(rid)
    checar(f["aplicados"] > 0, "fechamento aplica a política nos itens sem decisão",
           "%d aplicados por decurso de prazo, %d mantidos" % (f["aplicados"], f["mantidos"]))

    nome, csv_erp = servicos.exportar_aprovadas(rid, apenas_novos=True,
                                                usuario="Carla Menezes", registrar=True)
    linhas_erp = [l for l in csv_erp.strip().split("\n")[1:] if l]
    checar(len(linhas_erp) > 0, "o arquivo do ERP sai só do que foi homologado",
           "%d linhas" % len(linhas_erp))
    vias = {l.split(";")[-4] for l in linhas_erp}
    checar("omissao_aplicar" in vias or "gestora" in vias,
           "o arquivo marca como cada preço foi decidido", " · ".join(sorted(vias)))
    _, csv2 = servicos.exportar_aprovadas(rid, apenas_novos=True)
    checar(len([l for l in csv2.strip().split("\n")[1:] if l]) == 0,
           "item já enviado não volta no próximo arquivo", "fila de envio zerada")

    with banco.sessao() as con:
        try:
            con.execute("UPDATE votos SET candidato_id = candidato_id")
            checar(False, "UPDATE em votos é bloqueado pelo banco", "passou")
        except Exception as e:
            checar(True, "UPDATE em votos é bloqueado pelo banco", str(e)[:60])

    print("\nAuditoria imutável")
    alvo = next((i for i in itens if i["recomendacao"] in ("SUBIR", "BAIXAR")), None)
    if alvo:
        servicos.registrar_decisao(alvo["id"], "Ana Souza", "aprovada")
        con = sqlite3.connect(banco.ARQUIVO_BANCO)
        bloqueou_update = bloqueou_delete = False
        try:
            con.execute("UPDATE decisoes SET decisao='rejeitada'")
        except sqlite3.DatabaseError:
            bloqueou_update = True
        try:
            con.execute("DELETE FROM decisoes")
        except sqlite3.DatabaseError:
            bloqueou_delete = True
        con.close()
        checar(bloqueou_update, "UPDATE em decisões é bloqueado pelo banco")
        checar(bloqueou_delete, "DELETE em decisões é bloqueado pelo banco")

        try:
            servicos.registrar_decisao(alvo["id"], "Ana Souza", "rejeitada")
            checar(False, "rejeitar sem motivo é recusado")
        except ValueError:
            checar(True, "rejeitar sem motivo é recusado")

        # R01 é "Bloquear" na tabela de regras da Popular Pet, não "Avisar": um
        # preço abaixo do piso não pode entrar no banco nem digitado à mão.
        try:
            servicos.registrar_decisao(alvo["id"], "Ana Souza", "editada", preco_aplicado=0.01)
            checar(False, "R01 · editar abaixo do piso é recusado",
                   "o preço entrou e não deveria")
        except ValueError as erro:
            checar("R01" in str(erro), "R01 · editar abaixo do piso é recusado",
                   str(erro)[:58])

        # E o espelho: editar acima do piso passa, com aviso se furar a R02.
        acima = max(alvo["piso"] * 1.001, alvo["preco_atual"] * 1.08)
        try:
            r = servicos.registrar_decisao(alvo["id"], "Ana Souza", "editada",
                                           preco_aplicado=round(acima, 2))
            checar(any("R02" in a for a in r["avisos"]),
                   "R02 · editar acima de 5% passa, mas fica registrado o aviso",
                   "; ".join(r["avisos"])[:58])
        except ValueError as erro:
            checar(False, "R02 · editar acima de 5% passa, mas fica registrado o aviso",
                   str(erro)[:58])

        # Nenhum preço abaixo do piso pode existir no banco, venha de onde vier.
        with banco.sessao() as con:
            furados = banco.linhas(con, """
                SELECT d.id FROM decisoes d JOIN recomendacoes c ON c.id = d.recomendacao_id
                WHERE d.preco_aplicado IS NOT NULL AND d.preco_aplicado < c.piso - 0.005""")
        checar(not furados, "nenhum preço gravado abaixo do piso de margem",
               "%d registros" % len(furados))

    print("\n" + ("Tudo certo." if not falhas else
                  "%d verificação(ões) falharam: %s" % (len(falhas), "; ".join(falhas))))
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
