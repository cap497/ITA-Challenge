/* Núcleo da interface: estado, navegação, formatação e gráficos.
   Sem framework e sem build — o navegador carrega os três arquivos e pronto.

   Os gráficos leem a cor de `--roxo` em vez de repetir o hexadecimal. Trocar a
   cor do grupo passou a ser uma linha no CSS, não uma caçada por sete arquivos. */

const S = {
  contexto: null,
  usuario: localStorage.getItem('pp_usuario') || '',
  pagina: 'painel',
  filtros: {},
  fila: [],            // ids da fila de análise
  indice: 0,           // onde estamos na fila
  relogio: 0,          // quando o item atual apareceu na tela
};

const $ = (s, raiz = document) => raiz.querySelector(s);
const $$ = (s, raiz = document) => [...raiz.querySelectorAll(s)];
const esc = (t) => String(t ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const css = (nome) => getComputedStyle(document.documentElement)
  .getPropertyValue(nome).trim() || '#804996';

/* ------------------------------------------------------------ formatação */
const ehNum = (v) => v !== null && v !== undefined && v !== '' && !Number.isNaN(Number(v));
const brl = (v, casas = 2) => !ehNum(v) ? '—'
  : 'R$ ' + Number(v).toLocaleString('pt-BR', { minimumFractionDigits: casas, maximumFractionDigits: casas });
const n2 = (v, casas = 2) => !ehNum(v) ? '—'
  : Number(v).toLocaleString('pt-BR', { minimumFractionDigits: casas, maximumFractionDigits: casas });
const pct = (v, casas = 1) => !ehNum(v) ? '—'
  : (100 * Number(v)).toLocaleString('pt-BR', { minimumFractionDigits: casas, maximumFractionDigits: casas }) + '%';
const pctS = (v, casas = 1) => !ehNum(v) ? '—' : (v > 0 ? '+' : '') + pct(v, casas);
const dataBr = (s) => {
  if (!s) return '—';
  const p = String(s).slice(0, 10).split('-');
  return p.length === 3 ? `${p[2]}/${p[1]}/${p[0]}` : s;
};
const dataHora = (s) => !s ? '—' : String(s).replace('T', ' ').slice(0, 16);

/* -------------------------------------------------------------- rede */
async function api(rota, opcoes) {
  const r = await fetch('/api/' + rota, opcoes);
  const corpo = await r.json().catch(() => ({ erro: 'Resposta inválida do servidor.' }));
  if (!r.ok) throw new Error(corpo.erro || ('Erro ' + r.status));
  return corpo;
}

let tToast;
function toast(msg, tipo = '') {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast on ' + tipo;
  clearTimeout(tToast);
  tToast = setTimeout(() => { t.className = 'toast ' + tipo; }, 4200);
}

const selo = (txt, classe) => `<span class="selo ${esc(classe || txt)}">${esc(txt)}</span>`;

/* ===================================================================== */
/*                               gráficos                                */
/* ===================================================================== */

/* Faixa de composição: uma barra, as partes em proporção, legenda embaixo.
   A legenda fica fora do SVG porque dentro os rótulos se sobrepunham quando
   uma fatia era pequena. */
function faixa(partes, altura = 26) {
  const total = partes.reduce((a, b) => a + b.valor, 0) || 1;
  let x = 0;
  const blocos = partes.map((p) => {
    const w = 100 * p.valor / total;
    const s = `<rect x="${x.toFixed(2)}%" y="0" width="${Math.max(0, w - 0.4).toFixed(2)}%"
      height="${altura}" rx="3" fill="${p.cor}"><title>${esc(p.rotulo)}: ${p.valor}</title></rect>`;
    x += w;
    return s;
  }).join('');
  const leg = partes.map((p) => `<span><i style="background:${p.cor}"></i>${esc(p.rotulo)}
    <b>${p.valor}</b></span>`).join('');
  return `<svg viewBox="0 0 100 ${altura}" width="100%" height="${altura}"
    preserveAspectRatio="none" role="img" aria-label="composição">${blocos}</svg>
    <div class="legenda">${leg}</div>`;
}

/* Barras horizontais. `base` desloca a origem: sem isso, valores próximos entre
   si (índices de preço perto de 1,0) viram barras idênticas e o gráfico não
   informa nada. `marca` desenha a referência. */
function barras(dados, { rotulo = 118, cor = null, formato = n2, base = 0, marca = null,
  altura = 24 } = {}) {
  if (!dados.length) return '<div class="vazio">Sem dados.</div>';
  const C = cor || css('--roxo-dado');
  const L = 560;
  const vs = dados.map((d) => Math.abs(d.valor));
  const piso = base || 0;
  const teto = Math.max(...vs, piso + 1e-6);
  const faixaV = (teto - piso) || 1;
  const alt = dados.length * altura + 6;
  const barra = L - rotulo - 70;
  const x = (v) => rotulo + ((Math.abs(v) - piso) / faixaV) * barra;
  return `<svg viewBox="0 0 ${L} ${alt}" width="100%" height="${alt}" role="img"
    aria-label="gráfico de barras">
    ${marca !== null ? `<line x1="${x(marca).toFixed(1)}" y1="1" x2="${x(marca).toFixed(1)}"
      y2="${alt - 5}" stroke="${css('--lima')}" stroke-width="2"/>` : ''}
    ${dados.map((d, k) => {
    const w = Math.max(2, x(d.valor) - rotulo);
    const y = k * altura;
    return `<text x="0" y="${y + altura / 2 + 4}" font-size="11.5" fill="${css('--tinta-med')}"
        >${esc(d.rotulo)}</text>
      <rect x="${rotulo}" y="${y + 5}" width="${w.toFixed(1)}" height="${altura - 11}" rx="2"
            fill="${d.cor || C}"><title>${esc(d.rotulo)}: ${formato(d.valor)}</title></rect>
      <text x="${(rotulo + w + 7).toFixed(1)}" y="${y + altura / 2 + 4}" font-size="11"
            fill="${css('--tinta')}">${formato(d.valor)}</text>`;
  }).join('')}</svg>`;
}

/* Série temporal. `bandas` desenha área sombreada entre dois limites — é o que
   mostra que uma categoria parada na média tem itens andando dentro dela.
   `marcos` desenha linha vertical onde houve decisão, ligando "mudamos o preço
   aqui" a "o volume fez isso depois". */
function serie(linhas, { altura = 190, marcos = [], formato = brl, legenda = true,
  bandas = [], rotulosX = 2 } = {}) {
  const pontos = linhas.flatMap((l) => l.pontos);
  bandas.forEach((b) => b.pontos.forEach((p) => {
    pontos.push({ x: p.x, v: p.lo, rotulo: '' }, { x: p.x, v: p.hi, rotulo: '' });
  }));
  if (pontos.length < 2) return '<div class="vazio">Histórico insuficiente.</div>';

  const L = 640, mE = 56, mD = 14, mT = 14, mB = 30;
  const vs = pontos.map((p) => p.v);
  const mn = Math.min(...vs), mx = Math.max(...vs);
  const folga = (mx - mn) * 0.14 || Math.abs(mx) * 0.1 || 1;
  const lo = mn - folga, hi = mx + folga;
  const xs = [...new Set(pontos.map((p) => p.x))].sort((a, b) => a - b);
  const px = (x) => mE + (xs.length > 1 ? (x - xs[0]) / (xs[xs.length - 1] - xs[0]) : .5) * (L - mE - mD);
  const py = (v) => mT + (1 - (v - lo) / (hi - lo)) * (altura - mT - mB);
  /* Faixa pequena com zero casas decimais deixaria os três rótulos iguais. */
  const amplitude = Math.abs(hi - lo);
  const casas = amplitude >= 50 ? 0 : (amplitude >= 5 ? 1 : 2);

  const eixo = [lo, (lo + hi) / 2, hi].map((v) => `
    <line x1="${mE}" y1="${py(v).toFixed(1)}" x2="${L - mD}" y2="${py(v).toFixed(1)}"
      stroke="${css('--linha')}" stroke-dasharray="2 3"/>
    <text x="${mE - 6}" y="${(py(v) + 3.5).toFixed(1)}" font-size="10" text-anchor="end"
      fill="${css('--tinta-fraca')}">${formato(v, casas)}</text>`).join('');

  const areas = bandas.map((b) => {
    const cima = b.pontos.map((p, i) => (i ? 'L' : 'M') + px(p.x).toFixed(1) + ' ' + py(p.hi).toFixed(1)).join(' ');
    const baixo = [...b.pontos].reverse().map((p) => 'L' + px(p.x).toFixed(1) + ' ' + py(p.lo).toFixed(1)).join(' ');
    return `<path d="${cima} ${baixo} Z" fill="${b.cor || css('--roxo')}" opacity="0.16"/>`;
  }).join('');

  const marcasV = marcos.map((m) => `
    <line x1="${px(m.x).toFixed(1)}" y1="${mT}" x2="${px(m.x).toFixed(1)}"
      y2="${altura - mB}" stroke="${css('--lima')}" stroke-width="2"/>
    <text x="${(px(m.x) + 4).toFixed(1)}" y="${mT + 10}" font-size="9.5"
      fill="${css('--lima-txt')}">${esc(m.rotulo || '')}</text>`).join('');

  /* Ordem de desenho é ordem de empilhamento no SVG: a linha mais grossa vai por
     último, senão as curvas de fundo passam por cima da que interessa. */
  const ordenadas = [...linhas].sort((a, b) => (a.largura || 2) - (b.largura || 2));
  const tracos = ordenadas.map((l) => {
    const d = l.pontos.map((p, i) => (i ? 'L' : 'M') + px(p.x).toFixed(1) + ' ' + py(p.v).toFixed(1)).join(' ');
    const bolas = (l.pontos.length <= 14 && !l.semPontos) ? l.pontos.map((p) =>
      `<circle cx="${px(p.x).toFixed(1)}" cy="${py(p.v).toFixed(1)}" r="${l.pontos.length > 8 ? 2.5 : 3}"
        fill="${l.cor}"><title>${esc(p.rotulo)}: ${formato(p.v)}</title></circle>`).join('') : '';
    return `<path d="${d}" fill="none" stroke="${l.cor}" stroke-width="${l.largura || 2}"
      stroke-dasharray="${l.tracejada ? '5 4' : ''}" stroke-linejoin="round"/>${bolas}`;
  }).join('');

  const baseRot = linhas[0] ? linhas[0].pontos : pontos;
  const passo = Math.max(1, Math.floor(baseRot.length / Math.max(rotulosX, 1)));
  const rotulos = baseRot.map((p, k) => (k % passo && k !== baseRot.length - 1) ? '' :
    `<text x="${px(p.x).toFixed(1)}" y="${altura - 10}" font-size="9.5" text-anchor="middle"
       fill="${css('--tinta-fraca')}">${esc(p.rotulo)}</text>`).join('');

  const visiveis = linhas.filter((l) => !l.semLegenda);
  const leg = (legenda && visiveis.length > 1) ? `<div class="legenda">${visiveis.map((l) =>
    `<span><i style="background:${l.cor}"></i>${esc(l.nome)}</span>`).join('')}</div>` : '';

  /* Camada de leitura: uma faixa invisível por posição do eixo x, com os valores
     de TODAS as séries naquele ponto. O <title> do SVG só funcionava em cima da
     bolinha, demorava um segundo para aparecer e mostrava uma série por vez —
     para ler um gráfico de comparação, isso é o mesmo que não ter leitura. */
  const dados = xs.map((x) => ({
    r: (baseRot.find((p) => p.x === x) || {}).rotulo || '',
    v: linhas.filter((l) => !l.semLegenda || linhas.length === 1).map((l) => {
      const p = l.pontos.find((q) => q.x === x);
      return p ? { n: l.nome || '', c: l.cor, t: formato(p.v) } : null;
    }).filter(Boolean),
  }));
  const meia = xs.length > 1 ? (L - mE - mD) / (xs.length - 1) / 2 : (L - mE - mD) / 2;
  const alvos = xs.map((x, k) => `<rect class="gr-hit" data-k="${k}"
    x="${Math.max(mE, px(x) - meia).toFixed(1)}" y="${mT}"
    width="${Math.min(meia * 2, L - mD - Math.max(mE, px(x) - meia)).toFixed(1)}"
    height="${(altura - mB - mT).toFixed(1)}" fill="transparent"/>`).join('');
  const guias = xs.map((x, k) => `<line class="gr-guia" data-k="${k}"
    x1="${px(x).toFixed(1)}" y1="${mT}" x2="${px(x).toFixed(1)}" y2="${altura - mB}"
    stroke="${css('--roxo-dado')}" stroke-width="1" opacity="0"/>`).join('');

  return `<div class="gr" data-pontos='${esc(JSON.stringify(dados))}'>
    <svg viewBox="0 0 ${L} ${altura}" width="100%" height="${altura}" role="img"
      aria-label="série temporal">${eixo}${areas}${marcasV}${guias}${tracos}${rotulos}${alvos}</svg>
    <div class="gr-dica" hidden></div></div>${leg}`;
}

/* Um ouvinte só, no documento: os gráficos são redesenhados a cada troca de item
   e religar evento em cada um deixaria ouvinte órfão em toda navegação. */
document.addEventListener('mouseover', (e) => {
  const alvo = e.target.closest ? e.target.closest('.gr-hit') : null;
  if (!alvo) return;
  const cx = alvo.closest('.gr');
  const dica = cx.querySelector('.gr-dica');
  let dados;
  try { dados = JSON.parse(cx.dataset.pontos); } catch (err) { return; }
  const d = dados[Number(alvo.dataset.k)];
  if (!d) return;
  cx.querySelectorAll('.gr-guia').forEach((g) => {
    g.setAttribute('opacity', g.dataset.k === alvo.dataset.k ? '0.5' : '0');
  });
  dica.innerHTML = `<b>${esc(d.r)}</b>${d.v.map((x) =>
    `<span><i style="background:${x.c}"></i>${x.n ? esc(x.n) + ' ' : ''}<b>${esc(x.t)}</b></span>`)
    .join('')}`;
  dica.hidden = false;
  const cr = cx.getBoundingClientRect();
  const ar = alvo.getBoundingClientRect();
  const meio = ar.left + ar.width / 2 - cr.left;
  dica.style.left = Math.min(Math.max(meio, 66), cr.width - 66) + 'px';
});
document.addEventListener('mouseout', (e) => {
  const alvo = e.target.closest ? e.target.closest('.gr-hit') : null;
  if (!alvo) return;
  const cx = alvo.closest('.gr');
  if (cx.contains(e.relatedTarget)) return;
  cx.querySelector('.gr-dica').hidden = true;
  cx.querySelectorAll('.gr-guia').forEach((g) => g.setAttribute('opacity', '0'));
});


/* Paleta estável por nome: a mesma marca recebe a mesma cor em toda a tela.
   Sem isso, trocar de item trocava as cores e a comparação visual se perdia. */
const PALETA = ['#804996', '#B4C805', '#2E86AB', '#D4731F', '#59A14F', '#B8336A',
  '#7B6CA6', '#8A8F00'];
function corDe(nome) {
  let h = 0;
  for (const c of String(nome)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return PALETA[h % PALETA.length];
}

/* =============================================================== janela */
function abrirJanela(titulo, corpo, pe) {
  fecharJanela();
  const sombra = document.createElement('div');
  sombra.className = 'sombra-fundo';
  sombra.onclick = fecharJanela;
  const j = document.createElement('div');
  j.className = 'janela';
  j.setAttribute('role', 'dialog');
  j.setAttribute('aria-modal', 'true');
  j.innerHTML = `<header><h3>${esc(titulo)}</h3>
      <button class="x" onclick="fecharJanela()" aria-label="Fechar">×</button></header>
    <div class="corpo">${corpo}</div>${pe ? `<div class="pe">${pe}</div>` : ''}`;
  document.body.append(sombra, j);
  document.addEventListener('keydown', escFecha);
  const foco = j.querySelector('input, select, button.primario');
  if (foco) foco.focus();
  return j;
}
function fecharJanela() {
  $$('.sombra-fundo, .janela').forEach((e) => e.remove());
  document.removeEventListener('keydown', escFecha);
}
function escFecha(e) { if (e.key === 'Escape') fecharJanela(); }

/* ============================================================ navegação */
const PAGINAS = [
  ['painel', 'Painel'], ['analise', 'Análise'], ['camara', 'Câmara'],
  ['historico', 'Histórico'], ['base', 'Base'],
];

function irPara(pagina, filtros) {
  location.hash = '#/' + pagina + (filtros ? '?' + new URLSearchParams(filtros) : '');
}

function lerHash() {
  const [caminho, busca] = (location.hash.replace(/^#\/?/, '') || 'painel').split('?');
  return {
    pagina: caminho || 'painel',
    filtros: Object.fromEntries(new URLSearchParams(busca || '')),
  };
}

async function navegar() {
  const { pagina, filtros } = lerHash();
  S.pagina = PAGINAS.some(([p]) => p === pagina) ? pagina : 'painel';
  S.filtros = filtros;
  fecharJanela();
  $$('.topo nav a').forEach((a) => a.classList.toggle('ativa', a.dataset.p === S.pagina));
  const m = $('#conteudo');
  m.className = S.pagina === 'painel' ? 'painel' : '';
  m.innerHTML = '<div class="carregando">Carregando…</div>';
  try {
    await PAG[S.pagina]();
  } catch (e) {
    m.innerHTML = `<div class="aviso"><b>Não foi possível carregar.</b> ${esc(e.message)}</div>`;
  }
}

function montarTopo() {
  const c = S.contexto;
  if (!S.usuario) S.usuario = (c.usuarios[0] || {}).nome || 'Ana Souza';
  $('#usuario').innerHTML = c.usuarios.map((u) =>
    `<option value="${esc(u.nome)}" ${u.nome === S.usuario ? 'selected' : ''}
      >${esc(u.nome)} · ${esc(u.papel)}</option>`).join('');
  $('#usuario').onchange = (e) => {
    S.usuario = e.target.value;
    localStorage.setItem('pp_usuario', S.usuario);
    const papel = papelAtual();
    toast(papel === 'Analista'
      ? `${S.usuario} é analista: dá parecer e vota, e o preço final vai ao gestor.`
      : `${S.usuario} é gestor: vê a apuração da câmara e homologa o preço final.`);
    /* Redesenha a tela. Sem isso a troca de usuário só valia no próximo clique —
       e a tela do gestor continuava mostrando a do analista, que é justamente a
       confusão de papéis que a câmara existe para desfazer. */
    navegar();
  };
}

function papelAtual() {
  const u = (S.contexto.usuarios || []).find((x) => x.nome === S.usuario);
  return u ? u.papel : 'Analista';
}

async function iniciar() {
  $('#abas').innerHTML = PAGINAS.map(([p, t]) =>
    `<a href="#/${p}" data-p="${p}">${t}</a>`).join('');
  S.contexto = await api('contexto');
  montarTopo();
  window.addEventListener('hashchange', navegar);
  await navegar();
}

window.addEventListener('DOMContentLoaded', () => {
  iniciar().catch((e) => {
    $('#conteudo').innerHTML = `<div class="aviso"><b>O servidor não respondeu.</b>
      ${esc(e.message)}<br>Confira se o <code>python app.py</code> continua rodando.</div>`;
  });
});
