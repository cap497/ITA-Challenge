# -*- coding: utf-8 -*-
"""Modelo de linguagem local — LM Studio, Ollama, llama.cpp ou qualquer servidor
com API compatível com OpenAI.

Por que local e por que pequeno. A camada 3 não decide preço nenhum: ela pega
fatores já calculados e escreve um parágrafo. Isso é tarefa de redação curta com
todos os números dados de bandeja — não precisa de modelo de fronteira, precisa
de um que escreva português direito. Um 3B quantizado dá conta, roda na máquina
de quem está demonstrando e não manda dado de custo da Popular Pet para servidor
de terceiro.

Por que não usar modelo de linguagem para decidir. A aula de arquitetura do
Sprint foi explícita: dado estruturado e numérico pede modelo clássico. Pedir a
um LLM que escolha preço seria caro, lento, não reprodutível e impossível de
auditar. Aqui ele faz o que sabe fazer — frase.

**O modelo pequeno erra número, e isso é esperado.** Um 3B quantizado troca
dígito, arredonda sozinho e inventa valor que "parece certo". Por isso todo
valor monetário e todo percentual do texto passa por conferência contra os
fatores de origem antes de aparecer na tela. Texto que não bate é descartado e
a versão determinística assume. O painel de governança conta quantas vezes isso
aconteceu — é um número honesto sobre a qualidade do modelo escolhido, não algo
a esconder.

Descoberta automática: a plataforma procura um servidor local nas portas
conhecidas e usa o primeiro que responder. Nada para configurar quando o LM
Studio já está rodando.
"""

from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.request

# Servidores locais, na ordem em que são procurados. Todos falam o mesmo
# protocolo (/v1/chat/completions), então o resto do código não muda.
SERVIDORES = [
    {"nome": "LM Studio", "base": "http://localhost:1234/v1"},
    {"nome": "Ollama", "base": "http://localhost:11434/v1"},
    {"nome": "llama.cpp / llamafile", "base": "http://localhost:8080/v1"},
    {"nome": "vLLM / TGI", "base": "http://localhost:8000/v1"},
    {"nome": "Jan", "base": "http://localhost:1337/v1"},
]

TEMPO_LIMITE = 45.0        # um 8B em CPU é lento; melhor esperar que falhar à toa
SONDA_S = 0.35             # só para saber se há algo escutando na porta
_cache: dict = {"quando": 0.0, "achado": None}
CACHE_ACHADO_S = 30.0      # com servidor no ar, reconfere de vez em quando
CACHE_VAZIO_S = 120.0      # sem servidor, não adianta insistir a cada clique


def _porta_aberta(base: str) -> bool:
    """Tem alguém escutando? Socket cru, antes de falar HTTP.

    Importa no Windows: porta fechada nem sempre é recusada na hora — o firewall
    engole o pacote e a conexão espera o tempo inteiro. Com uma sonda de 0,35 s a
    varredura das cinco portas custa no máximo dois segundos, e só acontece
    quando alguém abre a tela, nunca dentro do pipeline.
    """
    try:
        _, resto = base.split("://", 1)
        hostporta = resto.split("/")[0]
        host, _, porta = hostporta.partition(":")
    except ValueError:
        return False
    try:
        with socket.create_connection((host or "127.0.0.1", int(porta or 80)),
                                      timeout=SONDA_S):
            return True
    except (OSError, ValueError):
        return False


def _pedir(url: str, corpo: dict | None = None, timeout: float = 4.0) -> dict | None:
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(
        url, data=dados,
        headers={"Content-Type": "application/json",
                 # Servidores locais ignoram a chave; alguns exigem o cabeçalho.
                 "Authorization": "Bearer local"},
        method="POST" if corpo is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError,
            json.JSONDecodeError):
        return None


def descobrir(forcar: bool = False) -> dict | None:
    """Procura um servidor local. Resultado em cache por 30 s.

    Sem cache, cada item da rodada tentaria cinco portas antes de desistir, e
    gerar 120 justificativas viraria 600 conexões recusadas.
    """
    agora = time.time()
    validade = CACHE_ACHADO_S if _cache["achado"] else CACHE_VAZIO_S
    if not forcar and agora - _cache["quando"] < validade:
        return _cache["achado"]

    base_env = os.environ.get("PP_LLM_BASE")
    candidatos = ([{"nome": "configurado em PP_LLM_BASE", "base": base_env.rstrip("/")}]
                  if base_env else SERVIDORES)

    achado = None
    for s in candidatos:
        # Sonda primeiro: sem isso, cada porta fechada custava o timeout do HTTP.
        if not _porta_aberta(s["base"]):
            continue
        r = _pedir(s["base"] + "/models", timeout=2.5)
        if not r:
            continue
        modelos = [m.get("id") for m in (r.get("data") or []) if m.get("id")]
        if not modelos:
            continue
        achado = {"nome": s["nome"], "base": s["base"], "modelos": modelos,
                  "modelo": _escolher(modelos)}
        break

    _cache.update({"quando": agora, "achado": achado})
    return achado


# Preferência entre os modelos carregados. Instruct acima de base, e tamanho
# médio acima do mínimo: um 1B escreve português sofrível, e um 8B em CPU faz a
# rodada demorar. O 3B é o ponto certo para escrever três frases.
PREFERIDOS = ["3b", "3.2-3b", "4b", "7b", "8b", "mini", "small", "1b"]


def _escolher(modelos: list[str]) -> str:
    env = os.environ.get("PP_LLM_MODELO")
    if env:
        exato = [m for m in modelos if m == env]
        if exato:
            return exato[0]
        parcial = [m for m in modelos if env.lower() in m.lower()]
        if parcial:
            return parcial[0]

    instruct = [m for m in modelos if "instruct" in m.lower() or "chat" in m.lower()] or modelos
    for chave in PREFERIDOS:
        for m in instruct:
            if chave in m.lower().replace("_", "-"):
                return m
    return instruct[0]


def situacao() -> dict:
    """O que a tela de governança mostra sobre a camada 3."""
    s = descobrir()
    if not s:
        return {"ativo": False, "servidor": None, "modelo": None,
                "como": "Nenhum modelo conectado. A plataforma funciona assim mesmo: a "
                        "justificativa é composta de forma determinística, a partir dos "
                        "mesmos fatores. Para ligar um modelo, suba um servidor local e "
                        "clique em Procurar modelo — nada precisa ser reiniciado.",
                "onde_procura": [{"nome": x["nome"], "base": x["base"]} for x in SERVIDORES],
                "receita": [
                    {"ferramenta": "LM Studio",
                     "passos": "carregue um modelo e vá em Developer → Start Server "
                               "(porta 1234). É o caminho mais curto no Windows.",
                     "recomendado": True},
                    {"ferramenta": "Ollama",
                     "passos": "`ollama pull llama3.2:3b` e `ollama serve` (porta 11434).",
                     "recomendado": False},
                    {"ferramenta": "llama.cpp / llamafile",
                     "passos": "`llama-server -m modelo.gguf --port 8080`.",
                     "recomendado": False},
                ]}
    return {"ativo": True, "servidor": s["nome"], "base": s["base"],
            "modelo": s["modelo"], "modelos_carregados": s["modelos"],
            "como": "o modelo escreve o texto a partir dos fatores já calculados; todo "
                    "número é conferido contra a origem antes de aparecer."}


def conversar(sistema: str, usuario: str, max_tokens: int = 320,
              temperatura: float = 0.2) -> tuple[str | None, dict]:
    """Uma rodada de conversa. Devolve (texto, diagnóstico)."""
    s = descobrir()
    if not s:
        return None, {"erro": "nenhum servidor local"}
    t0 = time.perf_counter()
    r = _pedir(s["base"] + "/chat/completions", {
        "model": s["modelo"],
        "messages": [{"role": "system", "content": sistema},
                     {"role": "user", "content": usuario}],
        "max_tokens": max_tokens,
        # Temperatura baixa porque a tarefa é relatar número, não inventar nada.
        "temperature": temperatura,
        "stream": False,
    }, timeout=TEMPO_LIMITE)
    ms = (time.perf_counter() - t0) * 1000
    if not r:
        _cache["quando"] = 0.0          # servidor caiu: redescobrir na próxima
        return None, {"erro": "sem resposta", "ms": round(ms, 0)}
    try:
        texto = r["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        return None, {"erro": "resposta fora do formato", "ms": round(ms, 0)}

    # Modelos pequenos gostam de abrir com "Claro!" e de fechar com bloco de
    # raciocínio. Nada disso vai para a tela.
    texto = re.sub(r"<think>.*?</think>", "", texto, flags=re.S | re.I).strip()
    texto = re.sub(r"^(claro|certo|com certeza|aqui está|segue)[!,:.]?\s*", "", texto,
                   flags=re.I).strip()
    texto = texto.strip('"').strip()
    return (texto or None), {"servidor": s["nome"], "modelo": s["modelo"],
                             "ms": round(ms, 0)}
