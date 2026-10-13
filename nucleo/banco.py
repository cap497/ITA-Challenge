# -*- coding: utf-8 -*-
"""Banco de dados — SQLite, criado na primeira execução.

Escolha deliberada: SQLite em arquivo, sem servidor. A aplicação sobe com
`python app.py` e nada mais precisa estar instalado ou rodando.

As decisões são append-only: um gatilho bloqueia UPDATE e DELETE na tabela
`decisoes`, do mesmo jeito que um Postgres faria. Refazer uma decisão grava um
registro novo; a mais recente é a que vale.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

PASTA_DADOS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dados")
ARQUIVO_BANCO = os.path.join(PASTA_DADOS, "popularpet.db")

ESQUEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS usuarios (
    id      INTEGER PRIMARY KEY,
    nome    TEXT NOT NULL UNIQUE,
    papel   TEXT NOT NULL              -- 'Analista' | 'Gestora'
);

CREATE TABLE IF NOT EXISTS produtos (
    sku        TEXT PRIMARY KEY,
    nome       TEXT NOT NULL,
    categoria  TEXT,
    marca      TEXT,
    curva      TEXT,
    status     TEXT,
    -- Marcado por gente, não pelo ERP: é decisão comercial de quais itens
    -- sustentam a imagem de preço da loja. Dispara a R04.
    estrategico     INTEGER DEFAULT 0,
    estrategico_por TEXT,
    estrategico_em  TEXT,
    estrategico_nota TEXT
);

CREATE TABLE IF NOT EXISTS estado_canal (
    sku            TEXT NOT NULL,
    canal          TEXT NOT NULL,
    preco          REAL NOT NULL,
    custo_rep      REAL NOT NULL,
    impostos       REAL DEFAULT 0,
    frete          REAL DEFAULT 0,
    taxa_canal     REAL DEFAULT 0,
    custo_total    REAL NOT NULL,
    margem_min     REAL NOT NULL,
    margem_alvo    REAL NOT NULL,
    data_ref       TEXT,
    PRIMARY KEY (sku, canal),
    FOREIGN KEY (sku) REFERENCES produtos(sku)
);

CREATE TABLE IF NOT EXISTS estoque (
    sku            TEXT PRIMARY KEY,
    quantidade     INTEGER,
    cobertura_erp  REAL,
    pedido_aberto  INTEGER,
    previsao       TEXT,
    local          TEXT,
    FOREIGN KEY (sku) REFERENCES produtos(sku)
);

CREATE TABLE IF NOT EXISTS historico_preco (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sku           TEXT NOT NULL,
    canal         TEXT NOT NULL,
    data          TEXT NOT NULL,
    preco_antigo  REAL,
    preco_novo    REAL NOT NULL,
    tipo          TEXT,                -- 'AUMENTO' | 'REDUCAO' | 'INICIAL'
    origem        TEXT,                -- 'ERP_HISTORICO' | 'ERP_SYNC'
    motivo        TEXT,
    FOREIGN KEY (sku) REFERENCES produtos(sku)
);
CREATE INDEX IF NOT EXISTS ix_hist_sku ON historico_preco(sku, canal, data);

CREATE TABLE IF NOT EXISTS rodadas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    data        TEXT NOT NULL UNIQUE,
    origem      TEXT NOT NULL,         -- 'motor' | 'importacao'
    criada_em   TEXT NOT NULL,
    parametros  TEXT                   -- JSON da configuração usada
);

CREATE TABLE IF NOT EXISTS recomendacoes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    rodada_id          INTEGER NOT NULL,
    prioridade         INTEGER NOT NULL,
    sku                TEXT NOT NULL,
    produto            TEXT NOT NULL,
    categoria          TEXT,
    marca              TEXT,
    curva              TEXT,
    canal              TEXT NOT NULL,
    preco_atual        REAL NOT NULL,
    preco_mercado      REAL,
    indice_preco       REAL,
    menor_concorrente  REAL,
    custo              REAL NOT NULL,
    custo_total        REAL,
    margem_atual       REAL,
    margem_liquida     REAL,
    margem_min         REAL,
    margem_alvo        REAL,
    piso               REAL,
    estoque            INTEGER,
    cobertura          REAL,
    tendencia_mes      REAL,
    var_12m            REAL,
    recomendacao       TEXT NOT NULL,  -- SUBIR | BAIXAR | MANTER | REVISAR
    motivo_principal   TEXT,
    preco_sugerido     REAL NOT NULL,
    variacao           REAL NOT NULL,
    margem_resultante  REAL,
    equilibrio_volume  REAL,
    nivel_risco        TEXT NOT NULL,  -- Baixo | Médio | Alto
    risco_score        REAL,
    confianca_dados    REAL,
    n_validos          INTEGER,
    n_total            INTEGER,
    exige_aprovacao    INTEGER DEFAULT 0,
    receita_mes        REAL,
    impacto_mes        REAL,
    valor_em_jogo      REAL,
    volume_mes         REAL,
    volume_estimado    REAL,
    prob_aceite        REAL,
    justificativa      TEXT,
    motivos_revisao    TEXT,           -- JSON
    alertas_dados      TEXT,           -- JSON
    regras             TEXT,           -- JSON
    concorrentes       TEXT,           -- JSON
    selecionado        INTEGER DEFAULT 0,
    FOREIGN KEY (rodada_id) REFERENCES rodadas(id) ON DELETE CASCADE,
    UNIQUE (rodada_id, sku, canal)
);
CREATE INDEX IF NOT EXISTS ix_rec_rodada ON recomendacoes(rodada_id, prioridade);

CREATE TABLE IF NOT EXISTS decisoes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    recomendacao_id     INTEGER NOT NULL,
    usuario_id          INTEGER NOT NULL,
    decisao             TEXT NOT NULL,  -- aprovada | editada | rejeitada | adiada
    preco_aplicado      REAL,
    motivo              TEXT,
    comentario          TEXT,
    criada_em           TEXT NOT NULL,
    snap_recomendacao   TEXT,
    snap_preco_atual    REAL,
    snap_preco_sugerido REAL,
    snap_fatores        TEXT,           -- JSON: alimenta o modelo de aceite
    segundos_analise    REAL,           -- cronômetro da tela: alimenta a linha de base
    FOREIGN KEY (recomendacao_id) REFERENCES recomendacoes(id) ON DELETE CASCADE,
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
);
CREATE INDEX IF NOT EXISTS ix_dec_rec ON decisoes(recomendacao_id, id DESC);

-- Auditoria imutável: refazer uma decisão cria registro novo.
CREATE TRIGGER IF NOT EXISTS decisoes_sem_update
BEFORE UPDATE ON decisoes
BEGIN SELECT RAISE(ABORT, 'decisoes é append-only: grave uma decisão nova'); END;

CREATE TRIGGER IF NOT EXISTS decisoes_sem_delete
BEFORE DELETE ON decisoes
BEGIN SELECT RAISE(ABORT, 'decisoes é append-only: não é possível apagar'); END;

-- ------------------------------------------------- câmara de precificação
-- A cédula de cada item: os preços que estão em jogo. Nascem três de graça —
-- o preço de hoje, o que o motor recomenda — e cada analista pode acrescentar
-- uma contraproposta. Votar em candidato, e não digitar um preço livre, é o que
-- faz "dois analistas concordaram" ser uma pergunta respondível: ou votaram no
-- mesmo candidato ou não votaram.
CREATE TABLE IF NOT EXISTS candidatos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recomendacao_id INTEGER NOT NULL,
    preco           REAL NOT NULL,
    centavos        INTEGER NOT NULL,       -- chave de comparação: 29,90 é um só
    origem          TEXT NOT NULL,          -- 'atual' | 'sistema' | 'analista'
    proposto_por    INTEGER,                -- usuarios.id, nulo nos dois primeiros
    justificativa   TEXT,
    criado_em       TEXT NOT NULL,
    FOREIGN KEY (recomendacao_id) REFERENCES recomendacoes(id) ON DELETE CASCADE,
    UNIQUE (recomendacao_id, centavos)
);
CREATE INDEX IF NOT EXISTS ix_cand_rec ON candidatos(recomendacao_id);

-- Um voto por pessoa por item: vale o último. Append-only, então trocar de voto
-- grava linha nova e a anterior continua auditável.
CREATE TABLE IF NOT EXISTS votos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recomendacao_id INTEGER NOT NULL,
    candidato_id    INTEGER NOT NULL,
    usuario_id      INTEGER NOT NULL,
    comentario      TEXT,
    criado_em       TEXT NOT NULL,
    FOREIGN KEY (recomendacao_id) REFERENCES recomendacoes(id) ON DELETE CASCADE,
    FOREIGN KEY (candidato_id) REFERENCES candidatos(id) ON DELETE CASCADE,
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
);
CREATE INDEX IF NOT EXISTS ix_voto_rec ON votos(recomendacao_id, id DESC);

CREATE TRIGGER IF NOT EXISTS votos_sem_update
BEFORE UPDATE ON votos
BEGIN SELECT RAISE(ABORT, 'votos é append-only: trocar de voto grava um voto novo'); END;

CREATE TRIGGER IF NOT EXISTS votos_sem_delete
BEFORE DELETE ON votos
BEGIN SELECT RAISE(ABORT, 'votos é append-only: não é possível apagar um voto'); END;

-- A conversa de cada item. A primeira mensagem é sempre a recomendação do motor;
-- depois vêm os pareceres dos analistas, as perguntas ao agente e as respostas
-- dele. A thread não é anexo da trilha de auditoria: ela É a trilha.
CREATE TABLE IF NOT EXISTS mensagens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recomendacao_id INTEGER NOT NULL,
    usuario_id      INTEGER,               -- nulo quando quem fala é o agente
    autor           TEXT NOT NULL,         -- nome exibido
    papel           TEXT NOT NULL,         -- 'Analista' | 'Gestora' | 'Agente' | 'Sistema'
    tipo            TEXT NOT NULL,         -- recomendacao|parecer|pergunta|resposta|voto|sistema
    texto           TEXT NOT NULL,
    candidato_id    INTEGER,               -- preenchido quando a mensagem acompanha um voto
    criado_em       TEXT NOT NULL,
    FOREIGN KEY (recomendacao_id) REFERENCES recomendacoes(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_msg_rec ON mensagens(recomendacao_id, id);

CREATE TRIGGER IF NOT EXISTS mensagens_sem_update
BEFORE UPDATE ON mensagens
BEGIN SELECT RAISE(ABORT, 'mensagens é append-only: a conversa não se reescreve'); END;

-- Parâmetros da operação — hoje, a política de omissão. Também append-only: cada
-- mudança grava linha nova, então "quem mudou a política e quando" tem resposta.
CREATE TABLE IF NOT EXISTS parametros (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chave       TEXT NOT NULL,
    valor       TEXT NOT NULL,
    usuario     TEXT,
    criado_em   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_param ON parametros(chave, id DESC);

-- Homologação: o parecer do analista e o preço final são coisas diferentes.
--
-- `decisoes` guarda o PARECER — a opinião de uma pessoa sobre um item. Qualquer
-- analista pode dar parecer em qualquer item, e é por isso que ali não há alçada.
-- Esta tabela guarda a HOMOLOGAÇÃO: o preço que efetivamente sai para o ERP.
-- Ele só nasce de um dos dois caminhos previstos pela Popular Pet:
--   'gestora'  — quem tem alçada assina e encerra o assunto sozinha;
--   'maioria'  — os analistas convergiram no MESMO preço, com quórum e maioria.
-- O UNIQUE é o que impede dois preços finais para o mesmo item na mesma rodada.
CREATE TABLE IF NOT EXISTS homologacoes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recomendacao_id INTEGER NOT NULL,
    usuario_id      INTEGER,            -- quem homologou; nulo quando foi a omissão
    preco_final     REAL NOT NULL,
    via             TEXT NOT NULL,      -- gestora | gestora_contra | omissao_manter
                                        -- | omissao_aplicar
    quando          TEXT NOT NULL,
    apuracao        TEXT,               -- JSON: a votação como estava na hora
    justificativa   TEXT,               -- obrigatória quando o gestor contraria a câmara
    FOREIGN KEY (recomendacao_id) REFERENCES recomendacoes(id) ON DELETE CASCADE,
    UNIQUE (recomendacao_id)
);
CREATE INDEX IF NOT EXISTS ix_hom_rec ON homologacoes(recomendacao_id);

CREATE TRIGGER IF NOT EXISTS homologacoes_sem_update
BEFORE UPDATE ON homologacoes
BEGIN SELECT RAISE(ABORT, 'homologacoes é append-only: o preço final não se reescreve'); END;

CREATE TRIGGER IF NOT EXISTS homologacoes_sem_delete
BEFORE DELETE ON homologacoes
BEGIN SELECT RAISE(ABORT, 'homologacoes é append-only: não é possível apagar'); END;

-- Uma linha por item efetivamente entregue ao ERP. Fica fora de `homologacoes`
-- porque marcar o envio dentro dela seria um UPDATE, e o gatilho aborta.
CREATE TABLE IF NOT EXISTS envios_erp (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    homologacao_id INTEGER NOT NULL,
    lote           TEXT NOT NULL,       -- carimbo do arquivo gerado
    usuario        TEXT,
    quando         TEXT NOT NULL,
    FOREIGN KEY (homologacao_id) REFERENCES homologacoes(id) ON DELETE CASCADE,
    UNIQUE (homologacao_id)
);

-- Venda publicada pelo ERP DEPOIS de uma aplicação de preço. É o insumo do
-- acompanhamento pós-decisão: enquanto estiver vazia, a tela de impacto mostra
-- projeção sintética marcada; assim que o ERP publicar, o real assume.
CREATE TABLE IF NOT EXISTS venda_pos (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    sku       TEXT NOT NULL,
    canal     TEXT NOT NULL,
    data      TEXT NOT NULL,
    preco     REAL,
    volume    REAL,
    receita   REAL,
    origem    TEXT NOT NULL DEFAULT 'ERP',
    UNIQUE (sku, canal, data, origem)
);
CREATE INDEX IF NOT EXISTS ix_vpos ON venda_pos(sku, canal, data);

CREATE TABLE IF NOT EXISTS sync_erp (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    quando           TEXT NOT NULL,
    origem           TEXT NOT NULL,     -- 'carga inicial' | 'manual' | 'arquivo'
    usuario          TEXT,
    status           TEXT NOT NULL,     -- 'sucesso' | 'erro'
    fonte            TEXT,
    produtos         INTEGER DEFAULT 0,
    novos            INTEGER DEFAULT 0,
    atualizados      INTEGER DEFAULT 0,
    precos_alterados INTEGER DEFAULT 0,
    duracao_s        REAL,
    mensagem         TEXT
);
"""

# Dois analistas e uma gestora: o porte de time de pricing de um varejo médio.
# Os dois analistas precisam convergir; divergência sobe para a gestora.
USUARIOS_PADRAO = [
    ("Ana Souza", "Analista"),
    ("Bruno Lima", "Analista"),
    ("Carla Menezes", "Gestora"),
]


def conectar() -> sqlite3.Connection:
    os.makedirs(PASTA_DADOS, exist_ok=True)
    con = sqlite3.connect(ARQUIVO_BANCO, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


@contextmanager
def sessao():
    """Abre conexão, confirma no fim e desfaz em caso de erro."""
    con = conectar()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# Colunas acrescentadas depois da primeira versão do esquema. Bancos já criados
# recebem a coluna sem precisar recomeçar do zero.
MIGRACOES = [("recomendacoes", "indice_preco", "REAL"),
             ("recomendacoes", "n_validos", "INTEGER"),
             ("recomendacoes", "n_total", "INTEGER"),
             ("decisoes", "segundos_analise", "REAL"),
             ("produtos", "estrategico", "INTEGER DEFAULT 0"),
             ("produtos", "estrategico_por", "TEXT"),
             ("produtos", "estrategico_em", "TEXT"),
             ("produtos", "estrategico_nota", "TEXT"),
             ("recomendacoes", "preco_mercado_entregue", "REAL"),
             ("recomendacoes", "indice_entregue", "REAL"),
             ("recomendacoes", "frete_medio_concorrentes", "REAL"),
             ("recomendacoes", "desconto_medio", "REAL"),
             ("recomendacoes", "preco_efetivo", "REAL"),
             ("recomendacoes", "margem_efetiva", "REAL"),
             ("recomendacoes", "cobertura_projetada", "REAL"),
             ("recomendacoes", "pedido_aberto", "INTEGER"),
             ("recomendacoes", "estrategico", "INTEGER DEFAULT 0"),
             ("recomendacoes", "retencao", "TEXT DEFAULT 'livre'"),
             ("recomendacoes", "retencao_motivo", "TEXT"),
             ("recomendacoes", "liberado_em", "TEXT"),
             ("recomendacoes", "exige_justificativa", "INTEGER DEFAULT 0"),
             ("recomendacoes", "fator_confianca", "REAL"),
             ("recomendacoes", "campanha", "TEXT")]


def criar_esquema() -> None:
    with sessao() as con:
        con.executescript(ESQUEMA)
        for tabela, coluna, tipo in MIGRACOES:
            existentes = {r["name"] for r in con.execute("PRAGMA table_info(%s)" % tabela)}
            if coluna not in existentes:
                con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (tabela, coluna, tipo))
        existentes = {r["nome"] for r in con.execute("SELECT nome FROM usuarios")}
        for nome, papel in USUARIOS_PADRAO:
            if nome not in existentes:
                con.execute("INSERT INTO usuarios (nome, papel) VALUES (?, ?)", (nome, papel))


def banco_vazio() -> bool:
    if not os.path.exists(ARQUIVO_BANCO):
        return True
    with sessao() as con:
        try:
            n = con.execute("SELECT COUNT(*) AS n FROM produtos").fetchone()["n"]
        except sqlite3.OperationalError:
            return True
    return n == 0


def linhas(con, sql: str, *args) -> list[dict]:
    return [dict(r) for r in con.execute(sql, args)]


def linha(con, sql: str, *args) -> dict | None:
    r = con.execute(sql, args).fetchone()
    return dict(r) if r else None


def valor(con, sql: str, *args):
    r = con.execute(sql, args).fetchone()
    return r[0] if r else None
