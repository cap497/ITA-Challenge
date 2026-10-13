"""A câmara de precificação: parecer, voto, consenso, homologação.

O desenho vem de uma correção: eu tratava o clique do analista como se fosse o
preço final. São quatro coisas diferentes, e cada uma acontece em um lugar.

  1. PAUTA        — o que entra em discussão hoje. É o recorte do otimizador, não
                    a base inteira: votar 1.850 SKUs por dia é o oposto de cortar
                    18 horas semanais para 6.
  2. PARECER      — a opinião escrita de um analista, na thread do item. Entra como
                    mensagem, assinada e com hora. Não move preço.
  3. VOTO         — a escolha de um dos preços na mesa (a cédula). Dois analistas
                    "concordam" quando votam no MESMO candidato — e é por isso que
                    a cédula é fechada: com preço livre, 29,90 e 29,95 seriam
                    divergência eterna.
  4. HOMOLOGAÇÃO  — o gestor. Só daqui sai o arquivo do ERP. O consenso da câmara
                    é o parecer final que chega até ele, não a decisão.

E o que acontece quando ninguém decide, que é o caso mais comum numa operação
real: às 18h a pauta fecha e o item segue a POLÍTICA DE OMISSÃO da empresa —
manter o preço, ou aplicar a recomendação. A escolha é do gestor e fica na tela.
É aqui que a tese do produto encosta no chão: o silêncio tem custo nos dois
sentidos, e a empresa decide qual custo prefere pagar.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta

from .banco import linha, linhas, sessao, valor

CONSENSO_MIN = 2                 # analistas no mesmo candidato
HORA_FECHAMENTO = 18             # a pauta do dia fecha às 18h
PAPEL_GESTOR = "Gestora"
POLITICAS = {
    "manter": ("Manter o preço",
               "O que não for votado até as 18h fica como está. Conservadora: o "
               "sistema nunca mexe em preço sem gente, e o custo do silêncio é a "
               "margem que deixou de ser corrigida."),
    "aplicar": ("Aplicar a recomendação",
                "O que não for votado até as 18h sai com o preço recomendado pelo "
                "motor. Ativa: nada fica parado, e o custo do silêncio é um preço "
                "aplicado sem ninguém ter olhado."),
}


# ------------------------------------------------------------- parâmetros
def politica() -> dict:
    with sessao() as con:
        r = linha(con, """SELECT valor, usuario, criado_em FROM parametros
                          WHERE chave = 'politica_omissao' ORDER BY id DESC LIMIT 1""")
    v = (r or {}).get("valor") or "manter"
    rot, txt = POLITICAS.get(v, POLITICAS["manter"])
    return {"valor": v, "rotulo": rot, "explicacao": txt,
            "por": (r or {}).get("usuario"), "em": (r or {}).get("criado_em"),
            "opcoes": [{"valor": k, "rotulo": a, "explicacao": b}
                       for k, (a, b) in POLITICAS.items()]}


def definir_politica(valor: str, usuario: str) -> dict:
    """Só o gestor troca. O analista vê a política e o efeito dela, não mexe."""
    if valor not in POLITICAS:
        raise ValueError("Política inválida: %s" % valor)
    with sessao() as con:
        u = linha(con, "SELECT papel FROM usuarios WHERE nome = ?", usuario)
        if not u or u["papel"] != PAPEL_GESTOR:
            raise ValueError("A política de omissão é do gestor. %s é analista: "
                             "vê a política em vigor, mas não a troca." % usuario)
        con.execute("""INSERT INTO parametros (chave, valor, usuario, criado_em)
                       VALUES ('politica_omissao', ?, ?, ?)""",
                    (valor, usuario, datetime.now().isoformat(timespec="seconds")))
    return politica()


def prazo(agora: datetime | None = None) -> dict:
    """Quanto falta para a pauta fechar. A tela usa isso no alerta."""
    agora = agora or datetime.now()
    fecha = agora.replace(hour=HORA_FECHAMENTO, minute=0, second=0, microsecond=0)
    if agora >= fecha:
        fecha = fecha + timedelta(days=1)
    seg = int((fecha - agora).total_seconds())
    return {"fecha_em": fecha.isoformat(timespec="minutes"), "segundos": seg,
            "horas": seg // 3600, "minutos": (seg % 3600) // 60,
            "hora_fechamento": "%02d:00" % HORA_FECHAMENTO,
            "fechado": agora.time() >= time(HORA_FECHAMENTO)}


# -------------------------------------------------------------- cédula
def _cent(p):
    return int(round(float(p) * 100))


def garantir_candidatos(con, rec: dict) -> None:
    """Põe na mesa os dois candidatos que existem sem ninguém propor.

    Idempotente pelo UNIQUE em (recomendacao_id, centavos): quando o motor
    recomenda MANTER, os dois preços coincidem e sobra um candidato só — que é
    exatamente o que deveria acontecer.
    """
    agora = datetime.now().isoformat(timespec="seconds")
    for preco, origem in ((rec["preco_atual"], "atual"), (rec["preco_sugerido"], "sistema")):
        if preco is None:
            continue
        con.execute("""INSERT OR IGNORE INTO candidatos
                       (recomendacao_id, preco, centavos, origem, criado_em)
                       VALUES (?,?,?,?,?)""",
                    (rec["id"], round(float(preco), 2), _cent(preco), origem, agora))


def propor(rec_id: int, preco: float, usuario: str, justificativa: str = "") -> dict:
    """Um analista acrescenta um preço à cédula. Todos passam a poder votar nele."""
    preco = round(float(preco), 2)
    with sessao() as con:
        rec = linha(con, "SELECT * FROM recomendacoes WHERE id = ?", rec_id)
        if not rec:
            raise ValueError("Item não encontrado.")
        u = linha(con, "SELECT id, papel FROM usuarios WHERE nome = ?", usuario)
        if not u:
            raise ValueError("Usuário desconhecido: %s" % usuario)
        if preco < float(rec["piso"] or 0) - 0.005:
            raise ValueError(
                "R01 · %s fica abaixo do piso de margem mínima deste SKU, que é %s. "
                "Esse preço não entra na cédula."
                % (("R$ %.2f" % preco).replace(".", ","),
                   ("R$ %.2f" % (rec["piso"] or 0)).replace(".", ",")))
        garantir_candidatos(con, rec)
        ja = linha(con, """SELECT id FROM candidatos
                           WHERE recomendacao_id = ? AND centavos = ?""", rec_id, _cent(preco))
        if ja:
            return {"ok": True, "candidato_id": ja["id"], "novo": False}
        cur = con.execute("""INSERT INTO candidatos (recomendacao_id, preco, centavos, origem,
                                                     proposto_por, justificativa, criado_em)
                             VALUES (?,?,?,'analista',?,?,?)""",
                          (rec_id, preco, _cent(preco), u["id"], justificativa,
                           datetime.now().isoformat(timespec="seconds")))
        cid = cur.lastrowid
        _msg(con, rec_id, u["id"], usuario, u["papel"], "parecer",
             "Proponho %s. %s" % (("R$ %.2f" % preco).replace(".", ","),
                                  justificativa or ""), cid)
    return {"ok": True, "candidato_id": cid, "novo": True}


# ------------------------------------------------------------- mensagens
def _msg(con, rec_id, uid, autor, papel, tipo, texto, candidato_id=None):
    con.execute("""INSERT INTO mensagens (recomendacao_id, usuario_id, autor, papel, tipo,
                                          texto, candidato_id, criado_em)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (rec_id, uid, autor, papel, tipo, (texto or "").strip(), candidato_id,
                 datetime.now().isoformat(timespec="seconds")))


def thread(rec_id: int) -> list[dict]:
    """A conversa do item. Se estiver vazia, a recomendação do motor abre a thread."""
    with sessao() as con:
        rec = linha(con, "SELECT * FROM recomendacoes WHERE id = ?", rec_id)
        if not rec:
            return []
        n = valor(con, "SELECT COUNT(*) FROM mensagens WHERE recomendacao_id = ?", rec_id)
        if not n:
            _msg(con, rec_id, None, "Motor de precificação", "Agente", "recomendacao",
                 rec["justificativa"] or "Sem justificativa registrada.")
        return linhas(con, """SELECT m.*, c.preco AS candidato_preco FROM mensagens m
                              LEFT JOIN candidatos c ON c.id = m.candidato_id
                              WHERE m.recomendacao_id = ? ORDER BY m.id""", rec_id)


def falar(rec_id: int, usuario: str, texto: str, tipo: str = "parecer") -> dict:
    if not (texto or "").strip():
        raise ValueError("Mensagem vazia.")
    with sessao() as con:
        u = linha(con, "SELECT id, papel FROM usuarios WHERE nome = ?", usuario)
        if not u:
            raise ValueError("Usuário desconhecido: %s" % usuario)
        _msg(con, rec_id, u["id"], usuario, u["papel"], tipo, texto)
    return {"ok": True}


def responder_agente(rec_id: int, pergunta: str, usuario: str) -> dict:
    """O analista chama o agente na thread. Entra pergunta e resposta.

    O modelo local responde quando está ligado; quando não está, quem responde é
    a justificativa determinística do motor — a thread nunca fica sem resposta por
    causa de um servidor desligado.
    """
    from . import servicos, texto as mtexto
    rec = servicos.recomendacao(rec_id)
    if not rec:
        raise ValueError("Item não encontrado.")
    if not (pergunta or "").strip():
        raise ValueError("Pergunta vazia.")
    falar(rec_id, usuario, pergunta, tipo="pergunta")

    r = mtexto.responder(rec, pergunta)
    saida, fonte = r.get("texto"), r.get("origem") or ""
    if not saida:
        # Sem modelo, ou resposta reprovada na conferência de números: responde a
        # justificativa determinística. A thread não fica muda por causa de um
        # servidor desligado, e não entra número que ninguém conferiu.
        saida = mtexto.justificar(rec)
        fonte = "motor (%s)" % (r.get("origem") or "sem modelo local")
    with sessao() as con:
        _msg(con, rec_id, None, "Motor de precificação", "Agente", "resposta",
             saida + "\n\n— " + fonte)
    return {"ok": True, "resposta": saida, "fonte": fonte}


# ----------------------------------------------------------------- voto
def _parecer_equivalente(rec: dict, preco: float) -> tuple[str, float | None]:
    """Traduz o voto para o vocabulário de `decisoes`.

    `votos` é a cédula; `decisoes` é o registro de parecer que já alimenta o
    histórico, a taxa de aceite, o cronômetro e o modelo de aceite. Em vez de
    duplicar tudo isso numa tabela nova, o voto grava o parecer equivalente:
    votar no preço do motor é aprovar, votar no preço de hoje é rejeitar a
    mudança, votar numa contraproposta é editar.
    """
    if abs(float(preco) - float(rec["preco_sugerido"] or 0)) < 0.005:
        return "aprovada", rec["preco_sugerido"]
    if abs(float(preco) - float(rec["preco_atual"] or 0)) < 0.005:
        return "rejeitada", None
    return "editada", round(float(preco), 2)


def votar(rec_id: int, candidato_id: int, usuario: str, comentario: str = "",
          segundos: float | None = None) -> dict:
    """Um voto. Vale o último de cada pessoa; os anteriores ficam na trilha."""
    with sessao() as con:
        rec = linha(con, "SELECT * FROM recomendacoes WHERE id = ?", rec_id)
        if not rec:
            raise ValueError("Item não encontrado.")
        if (rec["retencao"] or "livre") == "bloqueado":
            raise ValueError("R08 · este item está com o preço travado: %s. Não entra em "
                             "votação enquanto durar." % (rec["retencao_motivo"] or ""))
        u = linha(con, "SELECT id, papel FROM usuarios WHERE nome = ?", usuario)
        if not u:
            raise ValueError("Usuário desconhecido: %s" % usuario)
        c = linha(con, "SELECT * FROM candidatos WHERE id = ? AND recomendacao_id = ?",
                  candidato_id, rec_id)
        if not c:
            raise ValueError("Esse preço não está na cédula deste item.")
        if valor(con, "SELECT COUNT(*) FROM homologacoes WHERE recomendacao_id = ?", rec_id):
            raise ValueError("Item já homologado: a votação dele está encerrada.")
        if rec["exige_justificativa"] and not (comentario or "").strip():
            raise ValueError("Produto estratégico (R04): o voto exige parecer escrito. "
                             "É esse registro que explica a decisão seis meses depois.")
        agora = datetime.now().isoformat(timespec="seconds")
        con.execute("""INSERT INTO votos (recomendacao_id, candidato_id, usuario_id,
                                          comentario, criado_em) VALUES (?,?,?,?,?)""",
                    (rec_id, candidato_id, u["id"], comentario, agora))

        # O parecer equivalente em `decisoes`, no mesmo INSERT da mesma transação:
        # é o que mantém histórico, taxa de aceite, cronômetro e modelo de aceite
        # funcionando sem uma segunda fonte de verdade.
        dec, preco_ap = _parecer_equivalente(rec, c["preco"])
        try:
            seg = float(segundos) if segundos is not None else None
            if seg is not None and not (0 < seg < 7200):
                seg = None
        except (TypeError, ValueError):
            seg = None
        fatores = {k: rec[k] for k in ("variacao", "impacto_mes", "confianca_dados",
                                       "cobertura", "margem_resultante", "curva",
                                       "nivel_risco", "recomendacao", "indice_preco")}
        con.execute("""
            INSERT INTO decisoes (recomendacao_id, usuario_id, decisao, preco_aplicado,
                                  motivo, comentario, criada_em, snap_recomendacao,
                                  snap_preco_atual, snap_preco_sugerido, snap_fatores,
                                  segundos_analise)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (rec_id, u["id"], dec, preco_ap,
                     "voto na câmara" if dec != "rejeitada" else
                     (comentario or "votou por manter o preço de hoje"),
                     comentario, agora, rec["recomendacao"], rec["preco_atual"],
                     rec["preco_sugerido"], json.dumps(fatores, ensure_ascii=False), seg))

        _msg(con, rec_id, u["id"], usuario, u["papel"], "voto",
             comentario or "Voto em %s." % ("R$ %.2f" % c["preco"]).replace(".", ","),
             candidato_id)
    return apurar_rec(rec_id)


# -------------------------------------------------------------- apuração
def _votos_vigentes(con, rec_id: int) -> list[dict]:
    todos = linhas(con, """
        SELECT v.id, v.candidato_id, v.comentario, v.criado_em, u.nome AS usuario, u.papel,
               c.preco
        FROM votos v JOIN usuarios u ON u.id = v.usuario_id
        JOIN candidatos c ON c.id = v.candidato_id
        WHERE v.recomendacao_id = ? ORDER BY v.id""", rec_id)
    por_pessoa = {}
    for v in todos:
        por_pessoa[v["usuario"]] = v
    return sorted(por_pessoa.values(), key=lambda v: v["id"])


def apurar(con, rec: dict) -> dict:
    """Estado da votação de um item. Só lê e conclui — ninguém "fecha" uma urna."""
    garantir_candidatos(con, rec)
    cands = linhas(con, """
        SELECT c.*, u.nome AS proposto_por_nome FROM candidatos c
        LEFT JOIN usuarios u ON u.id = c.proposto_por
        WHERE c.recomendacao_id = ? ORDER BY c.preco""", rec["id"])
    vs = _votos_vigentes(con, rec["id"])

    analistas = [v for v in vs if v["papel"] != PAPEL_GESTOR]
    do_gestor = next((v for v in reversed(vs) if v["papel"] == PAPEL_GESTOR), None)

    conta = {}
    for v in analistas:
        conta.setdefault(v["candidato_id"], []).append(v["usuario"])
    for c in cands:
        c["votos"] = conta.get(c["id"], [])
        c["n"] = len(c["votos"])
        c["rotulo"] = {"atual": "preço de hoje", "sistema": "recomendado pelo motor"}.get(
            c["origem"], "proposta de %s" % (c["proposto_por_nome"] or "analista"))

    elenco = valor(con, "SELECT COUNT(*) FROM usuarios WHERE papel != ?", PAPEL_GESTOR) or 0
    topo = max(cands, key=lambda c: c["n"]) if cands else None
    n = len(analistas)
    empate = bool(topo) and sum(1 for c in cands if c["n"] == topo["n"]) > 1 and topo["n"] > 0

    consenso, preco_consenso = None, None
    if topo and topo["n"] >= CONSENSO_MIN and not empate:
        consenso, preco_consenso = topo["id"], topo["preco"]

    hom = linha(con, """
        SELECT h.*, u.nome AS homologada_por,
               (SELECT lote FROM envios_erp WHERE homologacao_id = h.id) AS lote_erp
        FROM homologacoes h LEFT JOIN usuarios u ON u.id = h.usuario_id
        WHERE h.recomendacao_id = ?""", rec["id"])

    bloqueado = (rec["retencao"] or "livre") == "bloqueado"
    faltam = max(0, CONSENSO_MIN - n)
    if hom:
        fase, falta = ("enviada" if hom["lote_erp"] else "homologada"), ""
    elif bloqueado:
        fase = "travada"
        falta = "R08 · preço travado: %s" % (rec["retencao_motivo"] or "")
    elif consenso:
        fase = "aguardando gestor"
        falta = "A câmara fechou em %s. Falta a homologação do gestor." % _brl(preco_consenso)
    elif empate:
        fase = "aguardando gestor"
        falta = ("Empate em %s. Sem consenso, a decisão sobe para o gestor — ou um "
                 "terceiro voto desempata." % " × ".join(_brl(c["preco"]) for c in cands
                                                         if c["n"] == topo["n"]))
    elif n == 0:
        fase = "sem voto"
        falta = "Ninguém votou ainda. Precisa de %d votos para a câmara fechar." % CONSENSO_MIN
    else:
        fase = "em votacao"
        falta = ("%d de %d voto(s). Faltam %d para o consenso."
                 % (n, elenco, faltam) if faltam else
                 "%d voto(s), sem maioria em nenhum preço." % n)

    return {
        "recomendacao_id": rec["id"], "sku": rec["sku"], "canal": rec["canal"],
        "produto": rec.get("produto"), "categoria": rec.get("categoria"),
        "marca": rec.get("marca"), "curva": rec.get("curva"),
        "recomendacao": rec.get("recomendacao"), "preco_atual": rec.get("preco_atual"),
        "preco_sugerido": rec.get("preco_sugerido"), "piso": rec.get("piso"),
        "nivel_risco": rec.get("nivel_risco"), "estrategico": bool(rec.get("estrategico")),
        "valor_em_jogo": rec.get("valor_em_jogo"), "impacto_mes": rec.get("impacto_mes"),
        "margem_resultante": rec.get("margem_resultante"),
        "retencao": rec.get("retencao") or "livre", "retencao_motivo": rec.get("retencao_motivo"),
        "exige_justificativa": bool(rec.get("exige_justificativa")),
        "candidatos": cands, "votos": vs, "voto_gestor": do_gestor,
        "n_votos": n, "elenco": elenco, "consenso_min": CONSENSO_MIN,
        "consenso": consenso, "preco_consenso": preco_consenso, "empate": empate,
        "bloqueado": bloqueado, "fase": fase, "falta": falta,
        "homologada": hom, "enviada": bool(hom and hom["lote_erp"]),
        "pode_homologar": bool(not hom and not bloqueado and (consenso or empate or n)),
        "mensagens": valor(con, "SELECT COUNT(*) FROM mensagens WHERE recomendacao_id = ?",
                           rec["id"]) or 0,
    }


def _brl(v):
    return "—" if v is None else ("R$ %.2f" % float(v)).replace(".", ",")


def apurar_rec(rec_id: int) -> dict | None:
    with sessao() as con:
        rec = linha(con, "SELECT * FROM recomendacoes WHERE id = ?", rec_id)
        return apurar(con, rec) if rec else None


# ----------------------------------------------------------------- pauta
ORDEM_FASE = ["aguardando gestor", "em votacao", "sem voto", "travada",
              "homologada", "enviada"]


def pauta(rodada_id: int, fase: str = "", busca: str = "") -> dict:
    """A pauta do dia: o recorte do otimizador, com a votação de cada item.

    O corte é `selecionado = 1` — o que a carteira escolheu dentro do limite
    diário. O resto da base não vai a voto: segue a política de omissão, e é esse
    recorte que mantém a reunião possível em menos de uma hora.
    """
    with sessao() as con:
        recs = linhas(con, """SELECT * FROM recomendacoes
                              WHERE rodada_id = ? AND selecionado = 1
                              ORDER BY prioridade""", rodada_id)
        if not recs:                 # rodada sem otimização: cai no que exige ação
            recs = linhas(con, """SELECT * FROM recomendacoes
                                  WHERE rodada_id = ? AND recomendacao IN ('SUBIR','BAIXAR')
                                  ORDER BY prioridade LIMIT 60""", rodada_id)
        itens = [apurar(con, r) for r in recs]

    contagem = {f: sum(1 for i in itens if i["fase"] == f) for f in ORDEM_FASE}
    pol = politica()
    em_aberto = [i for i in itens if i["fase"] in ("sem voto", "em votacao")]
    # O que o silêncio faria: com 'aplicar', o impacto dos itens em aberto entra
    # sozinho; com 'manter', é a margem que deixa de ser corrigida. O mesmo número,
    # com sinal de leitura oposto — e é isso que a tela precisa dizer.
    risco_omissao = round(sum(float(i["impacto_mes"] or 0) for i in em_aberto), 2)

    saida = itens
    if busca:
        b = busca.lower()
        saida = [i for i in saida if b in (i["sku"] or "").lower()
                 or b in (i["produto"] or "").lower()]
    if fase:
        saida = [i for i in saida if i["fase"] == fase]
    saida = sorted(saida, key=lambda i: (ORDEM_FASE.index(i["fase"]),
                                         -float(i["valor_em_jogo"] or 0)))
    return {"itens": saida, "contagem": contagem, "total": len(itens),
            "em_aberto": len(em_aberto), "risco_omissao": risco_omissao,
            "politica": pol, "prazo": prazo(), "consenso_min": CONSENSO_MIN,
            "a_enviar": len([i for i in itens if i["homologada"] and not i["enviada"]]),
            "valor_homologado": round(sum(float(i["impacto_mes"] or 0) for i in itens
                                          if i["homologada"]), 2)}


def pendencias(rodada_id: int) -> list[dict]:
    """Quem ainda deve voto em quê. Alimenta o alerta e a métrica de tempo."""
    with sessao() as con:
        pessoas = linhas(con, "SELECT id, nome FROM usuarios WHERE papel != ?", PAPEL_GESTOR)
        recs = linhas(con, """SELECT id FROM recomendacoes
                              WHERE rodada_id = ? AND selecionado = 1""", rodada_id)
        abertos = []
        for r in recs:
            if not valor(con, "SELECT COUNT(*) FROM homologacoes WHERE recomendacao_id = ?",
                         r["id"]):
                abertos.append(r["id"])
        saida = []
        for p in pessoas:
            votou = {v["recomendacao_id"] for v in linhas(
                con, "SELECT DISTINCT recomendacao_id FROM votos WHERE usuario_id = ?", p["id"])}
            saida.append({"nome": p["nome"], "votou": len([x for x in abertos if x in votou]),
                          "falta": len([x for x in abertos if x not in votou])})
    return sorted(saida, key=lambda x: -x["falta"])


# ------------------------------------------------------------ homologação
def homologar(rec_id: int, usuario: str, candidato_id: int | None = None,
              justificativa: str = "") -> dict:
    """O gestor assina. É o único caminho até o arquivo do ERP.

    Sem `candidato_id`, homologa o consenso da câmara. Com `candidato_id`
    diferente do consenso, o gestor está contrariando a câmara — e aí a
    justificativa escrita é obrigatória. Não é burocracia: é o que faz a
    homologação ser uma decisão, e não um carimbo.
    """
    with sessao() as con:
        rec = linha(con, "SELECT * FROM recomendacoes WHERE id = ?", rec_id)
        if not rec:
            raise ValueError("Item não encontrado.")
        u = linha(con, "SELECT id, papel FROM usuarios WHERE nome = ?", usuario)
        if not u:
            raise ValueError("Usuário desconhecido: %s" % usuario)
        if u["papel"] != PAPEL_GESTOR:
            raise ValueError("%s é analista: pode dar parecer e votar, mas a homologação "
                             "é do gestor. O voto dele já está registrado." % usuario)
        a = apurar(con, rec)
        if a["homologada"]:
            raise ValueError("Já homologado em %s a %s."
                             % (a["homologada"]["quando"],
                                _brl(a["homologada"]["preco_final"])))
        if a["bloqueado"]:
            raise ValueError(a["falta"])

        escolhido = candidato_id or a["consenso"]
        if not escolhido:
            raise ValueError("Não há consenso e nenhum preço foi escolhido. Diga em qual "
                             "candidato homologar.")
        c = linha(con, "SELECT * FROM candidatos WHERE id = ? AND recomendacao_id = ?",
                  escolhido, rec_id)
        if not c:
            raise ValueError("Esse preço não está na cédula deste item.")

        # Duas situações exigem justificativa escrita, e as duas são a mesma coisa
        # no fundo: o gestor está decidindo contra o que a câmara disse.
        #   · houve consenso e ele homologa outro preço;
        #   · houve empate e ele escolhe um lado — os analistas discordaram entre
        #     si, então optar por um deles é decisão dele, não apuração.
        contra = bool(a["consenso"] and escolhido != a["consenso"])
        if contra and not (justificativa or "").strip():
            raise ValueError(
                "A câmara fechou em %s e você está homologando %s. Contrariar o consenso "
                "exige justificativa escrita — ela fica no arquivo do ERP."
                % (_brl(a["preco_consenso"]), _brl(c["preco"])))
        if a["empate"] and not (justificativa or "").strip():
            raise ValueError(
                "A câmara empatou e você está desempatando em %s. Escolher um lado exige "
                "justificativa escrita: é a sua decisão, não a apuração dos votos."
                % _brl(c["preco"]))
        if float(c["preco"]) < float(rec["piso"] or 0) - 0.005:
            raise ValueError("R01 · %s fica abaixo do piso de %s. Não homologa."
                             % (_brl(c["preco"]), _brl(rec["piso"])))

        cur = _gravar(con, rec_id, u["id"], c["preco"],
                      "gestora_contra" if contra else "gestora", a, justificativa)
        _msg(con, rec_id, u["id"], usuario, u["papel"], "sistema",
             "Homologado a %s.%s" % (_brl(c["preco"]),
                                     (" Contra o consenso da câmara: " + justificativa)
                                     if contra else ""), escolhido)
    return {"ok": True, "homologacao_id": cur, "preco_final": round(float(c["preco"]), 2),
            "contra_consenso": contra}


def _gravar(con, rec_id, uid, preco, via, apuracao, justificativa=""):
    enxuto = {"via": via, "consenso_min": CONSENSO_MIN,
              "n_votos": apuracao["n_votos"], "empate": apuracao["empate"],
              "preco_consenso": apuracao["preco_consenso"],
              "candidatos": [{"preco": c["preco"], "origem": c["origem"],
                              "votos": c["votos"]} for c in apuracao["candidatos"]],
              "votos": [{"usuario": v["usuario"], "papel": v["papel"], "preco": v["preco"],
                         "comentario": v["comentario"], "em": v["criado_em"]}
                        for v in apuracao["votos"]]}
    cur = con.execute("""INSERT INTO homologacoes (recomendacao_id, usuario_id, preco_final,
                                                   via, quando, apuracao, justificativa)
                         VALUES (?,?,?,?,?,?,?)""",
                      (rec_id, uid, round(float(preco), 2), via,
                       datetime.now().isoformat(timespec="seconds"),
                       json.dumps(enxuto, ensure_ascii=False), justificativa))
    return cur.lastrowid


def homologar_consenso(rodada_id: int, usuario: str) -> dict:
    """Homologa de uma vez tudo em que a câmara já fechou."""
    p = pauta(rodada_id)
    feitos, recusados = [], []
    for i in p["itens"]:
        if not i["consenso"] or i["homologada"]:
            continue
        try:
            r = homologar(i["recomendacao_id"], usuario)
            feitos.append({"sku": i["sku"], "canal": i["canal"], **r})
        except ValueError as e:
            recusados.append({"sku": i["sku"], "porque": str(e)})
    return {"n": len(feitos), "homologados": feitos, "recusados": recusados}


def fechar_pauta(rodada_id: int, usuario: str = "Sistema") -> dict:
    """Às 18h. O que não foi decidido segue a política de omissão da empresa.

    Com 'manter', o item só é registrado como vencido e volta à fila da próxima
    coleta — nada vai ao ERP. Com 'aplicar', o preço recomendado é homologado com
    `via = omissao_aplicar`, que é uma marca permanente: no arquivo do ERP dá para
    separar o que alguém decidiu do que passou por decurso de prazo.
    """
    pol = politica()["valor"]
    with sessao() as con:
        recs = linhas(con, """SELECT * FROM recomendacoes
                              WHERE rodada_id = ? AND selecionado = 1""", rodada_id)
        aplicados, mantidos, travados = [], [], []
        for r in recs:
            a = apurar(con, r)
            if a["homologada"]:
                continue
            if a["bloqueado"]:
                travados.append(r["sku"]); continue
            if pol == "aplicar" and r["recomendacao"] in ("SUBIR", "BAIXAR") \
                    and float(r["preco_sugerido"]) >= float(r["piso"] or 0) - 0.005:
                cid = linha(con, """SELECT id FROM candidatos WHERE recomendacao_id = ?
                                    AND centavos = ?""", r["id"], _cent(r["preco_sugerido"]))
                _gravar(con, r["id"], None, r["preco_sugerido"], "omissao_aplicar", a)
                _msg(con, r["id"], None, "Sistema", "Sistema", "sistema",
                     "Pauta fechada às %02d:00 sem decisão. Política em vigor: aplicar a "
                     "recomendação. Preço homologado por decurso de prazo a %s."
                     % (HORA_FECHAMENTO, _brl(r["preco_sugerido"])),
                     (cid or {}).get("id"))
                aplicados.append(r["sku"])
            else:
                _msg(con, r["id"], None, "Sistema", "Sistema", "sistema",
                     "Pauta fechada às %02d:00 sem decisão. Política em vigor: manter o "
                     "preço. O item volta à fila na próxima coleta." % HORA_FECHAMENTO)
                mantidos.append(r["sku"])
    return {"politica": pol, "aplicados": len(aplicados), "mantidos": len(mantidos),
            "travados": len(travados), "skus_aplicados": aplicados[:40]}


# -------------------------------------------------------------- envio ERP
def pendentes_de_envio(rodada_id: int) -> list[dict]:
    with sessao() as con:
        return linhas(con, """
            SELECT h.id AS homologacao_id, h.preco_final, h.via, h.quando,
                   c.id AS rec_id, c.sku, c.canal, c.produto, c.preco_atual,
                   u.nome AS homologada_por
            FROM homologacoes h
            JOIN recomendacoes c ON c.id = h.recomendacao_id
            LEFT JOIN usuarios u ON u.id = h.usuario_id
            WHERE c.rodada_id = ?
              AND NOT EXISTS (SELECT 1 FROM envios_erp e WHERE e.homologacao_id = h.id)
            ORDER BY c.prioridade""", rodada_id)


def registrar_envio(ids: list[int], usuario: str, lote: str) -> int:
    agora = datetime.now().isoformat(timespec="seconds")
    with sessao() as con:
        for h in ids:
            con.execute("""INSERT OR IGNORE INTO envios_erp (homologacao_id, lote, usuario,
                                                             quando) VALUES (?,?,?,?)""",
                        (int(h), lote, usuario, agora))
    return len(ids)


def lotes(limite: int = 12) -> list[dict]:
    with sessao() as con:
        return linhas(con, """SELECT lote, usuario, MIN(quando) AS quando, COUNT(*) AS itens
                              FROM envios_erp GROUP BY lote
                              ORDER BY quando DESC LIMIT ?""", limite)
