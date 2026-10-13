/* As quatro telas. Cada função desenha uma e devolve o controle ao app.js.

   Painel     o dia de hoje, sem rolagem, com um botão só: Iniciar Análise
   Análise    um item por vez, com o histórico que situa a decisão
   Histórico  o agregado de 12 meses em cima, o item e o placar embaixo
   Base       CSV cru do ERP, qualidade, governança e envelope operacional   */

const PAG = {};

const itensTxt = (n) => (n === 1 ? '1 item' : n + ' itens');

/* ===================================================================== */
/*                           tela 1 · painel                             */
/* ===================================================================== */
PAG.painel = async function () {
  const pl = await api('pipeline');

  /* Enquanto a primeira rodada não existe não há o que desenhar, mas a tela
     continua sendo o painel: mesma moldura, mesma faixa de estado, só sem os
     números ainda. Trocar o painel por outra tela escondia justamente o que a
     pessoa abriu a plataforma para ver. */
  if (!pl.tem_rodada) {
    $('#conteudo').className = 'painel';
    $('#conteudo').innerHTML = `<div class="painel-grade">
      ${faixaPipeline(pl)}
      <div class="cartao pa-indice"><div class="vazio">Preparando a primeira rodada…</div></div>
      <div class="cartao pa-saidas"><div class="vazio">—</div></div>
      <div class="cartao pa-valor"><div class="vazio">—</div></div>
    </div>`;
    if (pl.rodando) agendarPipeline();
    return;
  }

  const d = await api('painel');
  if (!d.rodada) return;
  const { resumo: r, metricas: m, fila, linha_base: lb } = d;

  /* Onde estamos caro e onde estamos barato. O índice cru (1,026) não diz nada
     na tela; lido como "+2,6% acima do mercado" já traz a unidade. */
  const idx = m.indice_por_categoria || [];
  const minimo = idx.length ? Math.min(...idx.map((c) => c.indice)) : 1;
  const dadosIdx = idx.map((c) => ({
    rotulo: `${c.categoria} · ${itensTxt(c.n)}`,
    valor: c.indice,
    cor: c.indice > 1.02 ? '#D4731F' : (c.indice < 0.98 ? '#59A14F' : '#B6AEBF'),
  }));

  /* Efeito na margem, com sinal. É a conta que o Desafio pede em "oportunidade
     de ganho de margem" — e é diferente de receita afetada. */
  const porCat = {};
  (d.pendentes || []).forEach((x) => {
    const a = porCat[x.categoria] || (porCat[x.categoria] = [0, 0]);
    a[0] += x.impacto_mes; a[1] += 1;
  });
  const dadosValor = Object.entries(porCat)
    .sort((a, b) => Math.abs(b[1][0]) - Math.abs(a[1][0])).slice(0, 6)
    .map(([c, [v, n]]) => ({ rotulo: `${c} · ${itensTxt(n)}`, valor: v,
      cor: v > 0 ? '#59A14F' : '#D4731F' }));
  const ganho = Object.values(porCat).reduce((s, [v]) => s + (v > 0 ? v : 0), 0);
  const perda = Object.values(porCat).reduce((s, [v]) => s + (v < 0 ? v : 0), 0);

  const pr = r.por_recomendacao || {};
  const retidos = (r.retidos || []).slice(0, 5)
    .map(([k, n]) => ({ rotulo: k, valor: n, cor: css('--roxo') }));
  const porRet = r.por_retencao || {};

  const chips = (lb.itens || []).filter((i) => i.agora !== null).map((i) => {
    const cls = i.melhorou ? 'bom' : (i.melhorou === false ? 'ruim' : '');
    const inteiro = i.unidade === 'SKUs' || i.unidade === '/mês';
    const un = inteiro ? '' : (i.unidade === 'h/sem' ? 'h' : '%');
    return `<span class="chip"><span class="rot">${esc(i.indicador)}</span>
      <span class="de">${esc(i.base_texto)}</span>→<b class="${cls}">${n2(i.agora,
    inteiro ? 0 : 1)}${un}</b>
      <span class="mt">meta ${esc(i.meta_texto)}</span></span>`;
  }).join('');

  $('#conteudo').innerHTML = `<div class="painel-grade">
    ${faixaPipeline(pl)}
    <div class="chamada pa-acao">
      <div class="frase">
        <b>${fila.pendentes === 0 ? 'Rodada decidida' :
          fila.pendentes + ' decisões esperam você'}</b>
        <span>Rodada de ${dataBr(d.rodada.data)} · ${r.itens} itens triados pelo motor ·
          ${fila.retidos} retidos para a gestora</span>
      </div>
      <div class="mini">
        <div><b>${brl(fila.valor_em_jogo, 0)}</b><span>margem em jogo</span></div>
        <div><b>${pct(m.taxa_aceite, 0)}</b><span>aceite</span></div>
      </div>
      <button class="btn-grande" onclick="irPara('analise')"
        ${fila.pendentes ? '' : 'disabled'}>Iniciar Análise →</button>
    </div>

    <div class="cartao pa-indice">
      <h3>Onde estamos caro e onde estamos barato</h3>
      <p class="leg">Distância entre o nosso preço e o do mercado, por categoria. Zero é
      estar no mercado; a faixa neutra é de ±2%.</p>
      ${barras(dadosIdx, { rotulo: 160, altura: 26, base: Math.min(0.995, minimo - 0.004),
        marca: 1, formato: (v) => (v > 1 ? '+' : '') + n2(100 * (v - 1), 1) + '%' })}
      <div class="legenda" style="margin-top:4px">
        <span><i style="background:#D4731F"></i>acima do mercado</span>
        <span><i style="background:#B6AEBF"></i>na faixa</span>
        <span><i style="background:#59A14F"></i>abaixo do mercado</span>
        <span style="margin-left:auto;color:var(--tinta-fraca)">${pct(r.na_faixa, 0)} dos
          ${r.itens} itens na faixa</span>
      </div>
      <hr class="fina">
      <h3>Efeito na margem se as pendentes forem aprovadas</h3>
      <p class="leg">Quanto a margem bruta mensal muda, por categoria, a volume constante.
      Verde entra; laranja é o que se abre mão para voltar ao mercado ou girar estoque.
      Saldo do dia: <b>+${brl(ganho, 0)}</b> contra <b>−${brl(Math.abs(perda), 0)}</b>.</p>
      ${barras(dadosValor, { rotulo: 160, altura: 25,
        formato: (v) => (v > 0 ? '+' : '−') + brl(Math.abs(v), 0) })}
    </div>

    <div class="cartao pa-saidas">
      <h3>O que o motor decidiu hoje</h3>
      <p class="leg">${r.itens} itens triados. Só ${(pr.SUBIR || 0) + (pr.BAIXAR || 0)} exigem
      mudança de preço — o resto está no ponto ou precisa de gente.</p>
      ${faixa([
    { rotulo: 'Manter', valor: pr.MANTER || 0, cor: '#B6AEBF' },
    { rotulo: 'Revisar', valor: pr.REVISAR || 0, cor: '#E3B33E' },
    { rotulo: 'Baixar', valor: pr.BAIXAR || 0, cor: '#D4731F' },
    { rotulo: 'Subir', valor: pr.SUBIR || 0, cor: '#59A14F' },
  ])}
      <hr class="fina">
      <h3>O que está retido, e por quê</h3>
      <p class="leg">O motor tem recomendação de preço para todos estes — nenhuma está
      errada nem suspensa. O que falta é assinatura: ${porRet.aprovacao || 0} precisam da
      gestora${(porRet.bloqueado || 0) ? `, e ${porRet.bloqueado} têm o preço travado por
      terem sido alterados há menos de 3 dias (R08)` : ''}.</p>
      ${barras(retidos, { rotulo: 150, altura: 22, formato: (v) => String(Math.round(v)) })}
    </div>

    <div class="cartao pa-valor">
      <h3>Maior valor em jogo</h3>
      <p class="leg">Ordenado por quanto a decisão move de margem no mês.</p>
      <table><tbody>${(m.top_valor || []).slice(0, 8).map((t) => `
        <tr class="clicavel" onclick="irPara('analise',{item:${t.id}})">
          <td><b>${esc(t.produto)}</b><div class="sku">${esc(t.sku)} · ${esc(t.canal)}</div></td>
          <td>${selo(t.recomendacao)}</td>
          <td class="r">${brl(t.valor_em_jogo, 0)}</td>
          <td class="r">${selo(t.situacao)}</td></tr>`).join('')}</tbody></table>
    </div>

    <div class="rodape-base pa-base">
      <span class="titulo">Adoção · linha de base declarada pela Popular Pet
        <small>${lb.metas_atingidas} de ${lb.metas_possiveis} metas</small></span>
      <div class="chips">${chips}</div>
    </div>
  </div>`;
};

/* ===================================================================== */
/*                           tela 2 · análise                            */
/* ===================================================================== */
PAG.analise = async function () {
  const f = await api('analise');
  if (!f.total) {
    $('#conteudo').innerHTML = `<div class="analise"><div class="cartao">
      <h3>Fila vazia</h3><p class="leg">Nenhum item pendente nesta rodada.</p>
      <button class="btn primario" onclick="irPara('painel')">Voltar ao painel</button>
    </div></div>`;
    return;
  }
  S.fila = f.fila;
  const pedido = Number(S.filtros.item);
  const k = S.fila.indexOf(pedido);
  if (pedido && k < 0) {
    /* Link direto para item que já saiu da fila — veio do painel, de um favorito,
       de alguém que mandou o endereço. Mostrar o primeiro da fila no lugar, em
       silêncio, é pior que não abrir: a pessoa decide achando que é outro item. */
    S.fila = [pedido, ...S.fila];
    S.indice = 0;
    toast('Este item já foi decidido. Abrindo assim mesmo — redecidir grava um registro novo.');
  } else {
    S.indice = Math.max(0, k);
  }
  await desenharItem(S.fila[S.indice], f);
};

async function desenharItem(recId, f) {
  const { item: i } = await api('analise/' + recId);
  const hist = await api('series/item/' + encodeURIComponent(i.sku)
    + '?canal=' + encodeURIComponent(i.canal));
  /* O cronômetro começa quando o item aparece, não quando a página carrega: é o
     tempo de análise que vira linha de base, e ele tem de ser por decisão. */
  S.relogio = Date.now();

  const delta = i.variacao;
  const cls = delta < -1e-9 ? 'desce' : (delta > 1e-9 ? 'sobe' : 'igual');
  const ret = i.retencao || 'livre';
  const papel = papelAtual();

  const vizinhos = (k) => {
    const alvo = S.fila[S.indice + k];
    if (!alvo) return `<span class="nav vazia">${k < 0 ? '◀ primeiro' : 'fim da fila ▶'}</span>`;
    return `<a class="nav" href="#" onclick="pular(${k});return false">
      <span class="r">${k < 0 ? '◀ anterior' : 'próximo ▶'}</span>
      <b>item ${S.indice + 1 + k}</b>
      <span class="s">← →</span></a>`;
  };

  const aposta = i.equilibrio_volume === null || i.recomendacao === 'MANTER' ? '' : (delta < 0
    ? `<div class="aposta">A margem mensal cai <b>${brl(Math.abs(i.impacto_mes))}</b> se o
        volume não mudar. Para não perder nada, o volume precisa crescer
        <b>+${pct(Math.abs(i.equilibrio_volume), 1)}</b>.<br>
        A decisão é essa: <b>${pct(Math.abs(delta), 1)} a menos de preço traz mais de
        ${pct(Math.abs(i.equilibrio_volume), 1)} de volume neste item?</b></div>`
    : `<div class="aposta">A margem mensal sobe <b>${brl(Math.abs(i.impacto_mes))}</b> se o
        volume não mudar. A alta só deixa de compensar se o volume cair mais de
        <b>${pct(Math.abs(i.equilibrio_volume), 1)}</b>.<br>
        A decisão é essa: <b>${pct(Math.abs(delta), 1)} a mais de preço faz este item perder
        mais de ${pct(Math.abs(i.equilibrio_volume), 1)} de volume?</b></div>`);

  /* A cédula, aqui e na Câmara, é a mesma coisa: uma decisão, um mecanismo. Antes
     esta tela tinha "Aprovar/Outro preço" gravando parecer direto e a Câmara tinha
     voto — dois caminhos para a mesma decisão, e nenhum analista saberia qual vale. */
  const ap = i.apuracao || {};
  const meuVoto = (ap.votos || []).find((v) => v.usuario === S.usuario);
  const fechado = ['homologada', 'enviada'].includes(ap.fase);
  const gestor = papel !== 'Analista';
  const cedula = !(ap.candidatos || []).length ? '' : `
    <div class="cedula-an">
      <div class="cab-ced">
        <b>${gestor ? 'Apuração da câmara' : 'Seu voto'}</b>
        <span>${esc(ap.falta || '')}</span>
      </div>
      ${ap.candidatos.map((c) => {
    const meu = meuVoto && meuVoto.candidato_id === c.id;
    const venceu = ap.consenso === c.id;
    const hom = ap.homologada && Math.abs(ap.homologada.preco_final - c.preco) < 0.005;
    return `<div class="ced ${venceu ? 'venceu' : ''} ${hom ? 'homologado' : ''}">
      <div class="pr num">${brl(c.preco)}</div>
      <div class="qq"><span class="rot">${esc(c.rotulo)}</span>
        <span class="vt">${c.n ? esc(c.votos.join(', ')) : 'sem voto'}</span></div>
      <div class="nv">${c.n ? `<b>${c.n}</b> ${c.n === 1 ? 'voto' : 'votos'}` : ''}</div>
      ${fechado || ap.bloqueado ? ''
      : gestor
        ? `<button class="btn pequeno" onclick="homologarDaAnalise(${i.id},${c.id},
            ${venceu ? 'false' : 'true'})">${venceu ? 'Homologar' : 'Homologar este'}</button>`
        : `<button class="btn pequeno ${meu ? 'ativo' : ''}"
            onclick="votarNaAnalise(${i.id},${c.id},${i.exige_justificativa})"
          >${meu ? '✓ seu voto' : 'Votar'}</button>`}
    </div>`;
  }).join('')}
    </div>`;

  /* Com a câmara, TODO item passa pelo gestor — então "precisa da gestora" deixou
     de distinguir coisa alguma. A retenção agora significa o que sempre quis dizer:
     este item carrega um motivo para olhar com mais atenção antes de votar. */
  const retencao = ret === 'livre' ? '' : `
    <div class="retido ${esc(ret)}">
      <b>${ret === 'aprovacao' ? 'Item sensível — vote com atenção'
    : 'Preço travado por ora'}</b>
      <span>${esc(i.retencao_motivo)}</span>
      <small>${ret === 'aprovacao'
    ? `A recomendação vale e entra em votação normalmente. O aviso é sobre o motivo acima:
       é o que a gestora vai querer ler no seu parecer antes de homologar.`
    : `R08: o preço não pode oscilar duas vezes na mesma semana. Este item não entra em
       pauta e ninguém homologa enquanto durar.`}${i.liberado_em
    ? ` Libera em ${dataBr(i.liberado_em)}.` : ''}</small>
    </div>`;

  const esquerda = `<div class="cartao">
    <div class="item-cab">
      <div class="quem">
        <h2>${esc(i.produto)}</h2>
        <div class="meta">${esc(i.sku)} · ${esc(i.canal)} · ${esc(i.categoria)} /
          ${esc(i.marca)} · curva ${esc(i.curva)}</div>
      </div>
      ${selo(i.recomendacao)} ${selo('risco ' + i.nivel_risco.toLowerCase(), i.nivel_risco)}
      ${i.estrategico ? '<span class="selo estrategico">★ estratégico</span>' : ''}
    </div>
    <div class="mov">
      <div class="lado"><span class="rot">preço hoje</span>
        <span class="n num">${brl(i.preco_atual)}</span></div>
      <span class="seta">→</span>
      <div class="lado"><span class="rot">sugerido</span>
        <span class="n num" style="color:var(--roxo)">${brl(i.preco_sugerido)}</span></div>
      <span class="delta ${cls}">${pctS(delta, 1)}</span>
      <div class="lado" style="margin-left:auto;text-align:right">
        <span class="rot">mercado</span>
        <span class="n num" style="font-size:19px;color:var(--tinta-med)">
          ${brl(i.preco_mercado)}</span></div>
    </div>
    ${retencao}
    <div class="porque" id="justificativa">${esc(i.justificativa)}
      <button class="btn pequeno" style="margin-top:9px"
        onclick="reescrever(${i.id})">Reescrever com o modelo local</button>
    </div>
    ${aposta}
    <div class="fatos">
      <div><div class="r">Índice vs mercado</div><div class="v num">${n2(i.indice_preco, 3)}</div></div>
      <div><div class="r">Margem</div><div class="v num">${pct(i.margem_atual)}
        <small>→ ${pct(i.margem_resultante)}</small></div></div>
      <div><div class="r">Custo</div><div class="v num">${brl(i.custo)}
        <small>total ${brl(i.custo_total)}</small></div></div>
      <div><div class="r">Piso (R01)</div><div class="v num">${brl(i.piso)}</div></div>
      <div><div class="r">Cobertura</div><div class="v num">${n2(i.cobertura, 0)} d
        <small>${n2(i.cobertura_projetada, 0)} d com o pedido a caminho</small></div></div>
      <div><div class="r">Concorrentes</div><div class="v num">${i.n_validos}
        <small>de ${i.n_total} · conf. ${n2(i.confianca_dados, 2)}</small></div></div>
      <div><div class="r">Desconto habitual</div><div class="v num">${pct(i.desconto_medio, 1)}
        <small>margem efetiva ${pct(i.margem_efetiva)}</small></div></div>
      <div><div class="r">Volume/mês</div><div class="v num">${n2(i.volume_mes, 0)}
        <small>un</small></div></div>
      <div><div class="r">Mercado entregue</div>
        <div class="v num">${brl(i.preco_mercado_entregue)}
        <small>frete médio ${brl(i.frete_medio_concorrentes)}</small></div></div>
    </div>
    ${cedula}
    <div class="acoes" style="margin-top:9px">
      ${i.bloqueado_r08 ? '' : `<button class="btn pequeno"
        onclick="janelaPropor(${i.id},${i.preco_sugerido},${i.piso})"
        >Propor outro preço</button>`}
      <button class="btn pequeno" onclick="janelaThread(${i.id})"
        >Discussão${i.n_mensagens ? ` (${i.n_mensagens})` : ''}</button>
      <button class="btn pequeno ${i.estrategico ? 'ativo' : ''}"
        onclick="janelaEstrategico('${esc(i.sku)}',${i.estrategico ? 'true' : 'false'})"
        title="Marcar como estratégico (R04)">★ Estratégico</button>
      <span style="margin-left:auto;font-size:11.5px;color:var(--tinta-fraca)"
        id="cronometro">tempo nesta tela: 00:00 · medido e gravado com o voto</span>
    </div>
  </div>`;

  $('#conteudo').innerHTML = `<div class="analise">
    <div class="navbar">
      <div class="busca"><input type="text" id="busca" placeholder="Ir direto a um SKU ou produto…"
        oninput="filtrarFila(this.value)"></div>
      <div class="ordena"><label>Ordem</label>
        <select onchange="reordenar(this.value)">
          <option value="valor">Maior valor em jogo</option>
          <option value="margem">Maior margem em risco</option>
          <option value="gestora">Esperando a gestora</option>
          <option value="categoria">Por categoria</option>
          <option value="sku">SKU</option></select></div>
      <button class="btn pequeno" onclick="janelaFila()">Ver a fila inteira</button>
      <div class="pular">${vizinhos(-1)}${vizinhos(1)}</div>
      <div class="passo">
        <span class="conta"><b>${S.indice + 1}º</b> de ${S.fila.length} na fila</span>
        <span class="barra"><i style="width:${100 * S.indice / S.fila.length}%"></i></span>
        <span class="valor">${f ? brl(f.valor_restante, 0) + ' ainda em jogo' : ''}</span>
      </div>
    </div>
    <div class="an-grade">${esquerda}<div class="grade">${graficosItem(i, hist)}</div></div>
  </div>`;

  clearInterval(S.tCron);
  S.tCron = setInterval(() => {
    const el = $('#cronometro');
    if (!el) return clearInterval(S.tCron);
    const s = Math.floor((Date.now() - S.relogio) / 1000);
    el.textContent = `tempo nesta tela: ${String(Math.floor(s / 60)).padStart(2, '0')}:`
      + `${String(s % 60).padStart(2, '0')} · medido e gravado com a decisão`;
  }, 1000);
}

/* Os três gráficos que situam a decisão no tempo. */
function graficosItem(i, h) {
  const item = h.item || {}, pares = h.pares || {}, vol = h.volume || {};
  let saida = '';

  if ((item.pontos || []).length > 2) {
    const pts = item.pontos.filter((p) => p.preco)
      .map((p, n) => ({ x: n, v: p.preco, rotulo: p.rotulo }));
    const ult = pts[pts.length - 1];
    /* O sugerido entra como ponto à frente da série, tracejado: é proposta, não
       histórico, e misturar os dois na mesma linha mentiria sobre o passado. */
    const linhas = [{ nome: 'preço praticado', cor: css('--roxo'), pontos: pts }];
    if (i.preco_mercado) {
      linhas.push({ nome: 'mercado hoje', cor: '#B6AEBF', tracejada: true, semPontos: true,
        pontos: pts.map((p) => ({ x: p.x, v: i.preco_mercado, rotulo: '' })) });
    }
    linhas.push({ nome: 'sugerido', cor: css('--lima'), tracejada: true, pontos: [
      { x: ult.x, v: ult.v, rotulo: 'hoje' },
      { x: ult.x + 1, v: i.preco_sugerido, rotulo: 'sugerido' }] });
    saida += `<div class="cartao">
      <h3>Como o preço deste item andou</h3>
      <p class="leg">Preço praticado mês a mês nos últimos 12 meses — o que saiu no caixa,
      não o de tabela. ${item.variacao_12m > 0 ? 'Subiu ' + pctS(item.variacao_12m, 1)
    : item.variacao_12m < 0 ? 'Caiu ' + pctS(item.variacao_12m, 1) : 'Ficou parado'} no
      período, com mínimo de ${brl(item.minimo)} e máximo de ${brl(item.maximo)}.</p>
      ${serie(linhas, { altura: 180, rotulosX: 4 })}</div>`;
  }

  if ((pares.itens || []).length) {
    const linhas = pares.itens.map((x) => {
      const pts = x.pontos.map((q, n) => ({ x: n, v: q.indice, rotulo: q.rotulo }));
      if (x.destaque) return { nome: `${x.produto} · ${x.marca} (este item)`,
        cor: css('--roxo'), pontos: pts, largura: 3 };
      if (x.par) return { nome: `${x.produto} · ${x.marca} (mesmo produto)`,
        cor: css('--lima'), pontos: pts, largura: 2.2 };
      return { nome: x.produto, cor: '#DED7E4', pontos: pts, semPontos: true,
        largura: 1.1, semLegenda: true };
    });
    saida += `<div class="cartao">
      <h3>A categoria nos últimos 12 meses</h3>
      <p class="leg">Preço de cada item de ${esc(pares.categoria)}, em base 100 no primeiro
      mês — no eixo em reais as curvas baratas sumiriam. Este item em roxo, o mesmo produto
      na outra marca em lima, o resto da categoria em cinza.</p>
      ${serie(linhas, { altura: 215, formato: (v) => n2(v, 0), rotulosX: 4 })}</div>`;
  }

  if ((vol.pontos || []).length) {
    const linhas = [
      { nome: 'este item', cor: css('--roxo'),
        pontos: vol.pontos.map((q, n) => ({ x: n, v: q.indice_item, rotulo: q.rotulo })) },
      { nome: 'categoria ' + vol.categoria, cor: '#B6AEBF', tracejada: true, semPontos: true,
        pontos: vol.pontos.map((q, n) => ({ x: n, v: q.indice_categoria, rotulo: q.rotulo })) },
    ];
    saida += `<div class="cartao">
      <h3>Volume vendido · item contra categoria</h3>
      <p class="leg">Base 100 no primeiro mês. O item fez ${pctS(vol.variacao_item, 1)} no
      período e a categoria ${pctS(vol.variacao_categoria, 1)} — a comparação separa o que é
      deste produto do que é do mercado inteiro.</p>
      ${serie(linhas, { altura: 185, formato: (v) => n2(v, 0), rotulosX: 4 })}</div>`;
  }

  /* Composição do preço: de onde sai cada centavo entre o custo e o preço. Sem
     isso o analista via "custo R$ 98,45 · total R$ 109,77" e tinha de acreditar
     nos R$ 11,32 do meio — que são imposto, frete rateado e taxa do canal, e
     mudam de canal para canal no mesmo SKU. */
  const k = i.custos || {};
  if (k.custo_total) {
    const base = i.preco_atual || 1;
    const partes = [
      { r: 'Custo de reposição', v: k.custo_rep, c: '#5A2F6B',
        o: 'o que a Popular Pet paga ao fornecedor' },
      { r: 'Impostos estimados', v: k.impostos, c: '#9A62AE', o: 'sobre a venda' },
      { r: 'Frete rateado', v: k.frete, c: '#B6AEBF', o: 'logística atribuída ao item' },
      { r: 'Taxa do canal', v: (k.taxa_canal || 0) * base, c: '#DED7E4',
        o: `${pct(k.taxa_canal, 1)} sobre o preço · específica de ${esc(i.canal)}` },
    ].filter((x) => x.v > 0.0001);
    const custoTotal = partes.reduce((a, x) => a + x.v, 0);
    const margemR$ = base - custoTotal;
    const todas = [...partes, { r: 'Margem', v: margemR$, c: css('--lima'),
      o: 'o que sobra para a empresa' }];
    const nova = (i.preco_sugerido || base) - custoTotal;

    saida += `<div class="cartao">
      <h3>Como o preço de hoje se reparte</h3>
      <p class="leg">Cada centavo de ${brl(base)} em ${esc(i.canal)}. O custo total muda de
      canal para canal no mesmo SKU: a taxa de ${pct(k.taxa_canal, 1)} só existe aqui.</p>
      <div class="barra-comp">${todas.map((x) => `<i style="width:${100 * x.v / base}%;
        background:${x.c}" title="${esc(x.r)}: ${brl(x.v)}"></i>`).join('')}</div>
      <table class="comp"><tbody>${todas.map((x) => `<tr>
        <td><span class="pt" style="background:${x.c}"></span>${esc(x.r)}
          <small>${x.o}</small></td>
        <td class="r num">${brl(x.v)}</td>
        <td class="r num" style="color:var(--tinta-med)">${pct(x.v / base, 1)}</td>
      </tr>`).join('')}
        <tr class="tot"><td>Preço de hoje</td><td class="r num">${brl(base)}</td>
          <td class="r num">100%</td></tr>
      </tbody></table>
      <p class="leg" style="margin:9px 0 0">Com o preço sugerido de
      ${brl(i.preco_sugerido)}, o custo não muda e a margem vai de
      <b>${brl(margemR$)}</b> para <b>${brl(nova)}</b> por unidade —
      ${pctS((nova - margemR$) / Math.abs(margemR$ || 1), 1)}. Multiplicado por
      ${n2(i.volume_mes, 0)} unidades/mês, é o impacto de
      <b>${brl(i.impacto_mes)}</b> que aparece na decisão.
      ${k.data_ref ? ` Custos da carga de ${dataBr(k.data_ref)}.` : ''}</p></div>`;
  }

  /* Estoque e reposição: a decisão de esvaziar estoque ou segurar preço depende
     do que está no CD e do que já foi comprado. Estava tudo no banco e nada na
     tela — o analista decidia sobre cobertura sem ver o pedido em trânsito. */
  const es = i.estoque_detalhe || {};
  if (es.quantidade !== undefined && es.quantidade !== null) {
    const diaria = (i.volume_mes || 0) / 30;
    const curto = (i.cobertura || 99) < 15;
    const sobra = (i.cobertura || 0) > 90;
    saida += `<div class="cartao">
      <h3>Estoque e reposição</h3>
      <p class="leg">O estoque é do SKU, não do canal: ${esc(i.canal)} divide estas unidades
      com os outros canais. Por isso cobertura curta trava a baixa de preço em todos eles
      (R11) e sobra de estoque é argumento para baixar (R12).</p>
      <div class="fatos estoque">
        <div><div class="r">Em estoque</div><div class="v num">${n2(es.quantidade, 0)}
          <small>un · ${esc(es.local || 'CD')}</small></div></div>
        <div><div class="r">Cobertura</div><div class="v num ${curto ? 'alerta' : ''}">
          ${n2(i.cobertura, 0)} d<small>${n2(diaria, 1)} un/dia de venda</small></div></div>
        <div><div class="r">Pedido em aberto</div><div class="v num">
          ${es.pedido_aberto ? n2(es.pedido_aberto, 0) : '—'}
          <small>${es.pedido_aberto ? (es.previsao ? 'chega em ' + dataBr(es.previsao)
    : 'sem data prevista') : 'nada a caminho'}</small></div></div>
        <div><div class="r">Cobertura projetada</div><div class="v num">
          ${n2(i.cobertura_projetada, 0)} d<small>contando o pedido a caminho</small></div></div>
      </div>
      <p class="leg" style="margin:9px 0 0">${curto
    ? `<b>Cobertura curta.</b> Baixar preço agora acelera a saída de um estoque que já
       acaba em ${n2(i.cobertura, 0)} dias${es.pedido_aberto
      ? ' — mas há pedido a caminho, e a projetada sobe para '
        + n2(i.cobertura_projetada, 0) + ' dias.'
      : ' e não há reposição comprada. É isso que a R11 trava.'}`
    : sobra
      ? `<b>Estoque sobrando:</b> ${n2(i.cobertura, 0)} dias de cobertura, acima dos 90 que
         a R12 usa como excesso. Baixar preço aqui é escoar capital parado, não dar
         desconto.`
      : `Cobertura dentro da faixa de trabalho. O estoque não limita nem empurra esta
         decisão.`}</p></div>`;
  }

  saida += `<div class="cartao">
    <h3>O mesmo produto na concorrência, hoje</h3>
    <table class="conc"><thead><tr><th>Concorrente</th><th class="r">Preço</th>
      <th class="r">Frete</th><th class="r">Entregue</th><th class="r">Idade</th>
      <th class="r">Peso</th></tr></thead>
      <tbody>${(i.concorrentes || []).map((c) => `<tr>
        <td>${esc(c.nome)}</td><td class="r">${brl(c.preco)}</td>
        <td class="r">${brl(c.frete)}</td><td class="r">${brl(c.entregue)}</td>
        <td class="r">${n2(c.idade_h, 0)} h</td><td class="r">${n2(c.peso, 2)}</td>
      </tr>`).join('')}</tbody></table>
    <p class="leg" style="margin:9px 0 0">Coleta recente pesa mais (meia-vida de 24 h) e preço
    destoante sai pelo z robusto. <b>Frete fora da curva também pesa menos</b>: preço baixo com
    frete alto não é a alternativa que o cliente escolhe. A referência que decide é o preço
    publicado — o nosso entregue a base não informa, e isso está declarado.</p></div>`;

  if ((i.alertas_dados || []).length || (i.motivos_revisao || []).length) {
    saida += `<div class="cartao"><h3>Observações do motor</h3>
      <ul class="checklist">${[...(i.motivos_revisao || []), ...(i.alertas_dados || [])]
    .map((x) => `<li class="na">${esc(x)}</li>`).join('')}</ul></div>`;
  }
  return saida;
}

/* ------------------------------------------------------- ações da análise */
/* `decidir`, `janelaPreco` e `janelaRejeitar` saíram daqui: a decisão virou voto
   na cédula (`votarNaAnalise`, no fim do arquivo), que é o mesmo mecanismo da
   Câmara. Manter os dois caminhos era deixar o analista escolher qual vale. */
async function pular(k) {
  const novo = S.indice + k;
  if (novo < 0 || novo >= S.fila.length) return;
  S.indice = novo;
  await desenharItem(S.fila[S.indice]);
}

function janelaEstrategico(sku, marcado) {
  abrirJanela(marcado ? 'Desmarcar produto estratégico' : 'Marcar produto estratégico', `
    <p class="leg">Estratégico é decisão comercial, não dado de ERP: são os itens em que o
    cliente julga a loja inteira pelo preço. O efeito é de roteamento — a decisão sobe para a
    gestora e exige justificativa escrita — e <b>não muda o preço calculado</b>.</p>
    ${marcado ? '' : `<div class="campo"><label>Por que este item é estratégico</label>
      <input type="text" id="notaEstr" placeholder="obrigatório"></div>`}`,
  `<button class="btn" onclick="fecharJanela()">Cancelar</button>
   <button class="btn primario" onclick="salvarEstrategico('${sku}',${!marcado})">
     ${marcado ? 'Desmarcar' : 'Marcar'}</button>`);
}

async function salvarEstrategico(sku, valor) {
  try {
    const n = $('#notaEstr');
    const r = await api('estrategicos', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sku, estrategico: valor, usuario: S.usuario,
        nota: n ? n.value : '' }),
    });
    fecharJanela();
    toast(r.aviso);
  } catch (e) { toast(e.message, 'erro'); }
}

async function janelaFila() {
  const d = await api('recomendacoes');
  const alvo = d.itens.filter((i) => i.situacao === 'Pendente'
    && i.recomendacao !== 'MANTER');
  abrirJanela('A fila inteira', `<div class="tab"><table>
    <thead><tr><th>Item</th><th>Saída</th><th class="r">Preço</th><th>Situação</th>
      <th class="r">Em jogo</th></tr></thead><tbody>${alvo.map((i) => `
      <tr class="clicavel" onclick="irFila(${i.id})">
        <td><b>${esc(i.produto)}</b><div class="sku">${esc(i.sku)} · ${esc(i.canal)}</div></td>
        <td>${selo(i.recomendacao)}</td>
        <td class="r">${brl(i.preco_atual)} → ${brl(i.preco_sugerido)}</td>
        <td>${i.retencao === 'livre' ? '<span class="selo ok">livre</span>'
    : i.retencao === 'aprovacao' ? '<span class="selo alerta">gestora</span>'
      : '<span class="selo nao">bloqueado</span>'}</td>
        <td class="r">${brl(i.valor_em_jogo, 0)}</td></tr>`).join('')}</tbody></table></div>`);
}

async function irFila(id) {
  fecharJanela();
  const k = S.fila.indexOf(id);
  if (k >= 0) { S.indice = k; await desenharItem(id); } else { irPara('analise', { item: id }); }
}

function filtrarFila(texto) {
  S.busca = (texto || '').toLowerCase();
}

function reordenar(modo) {
  S.ordem = modo;
  toast('A fila foi reordenada por ' + modo + '.');
}

/* Setas do teclado andam na fila: quem decide cinquenta itens por dia não quer
   tirar a mão do teclado para clicar. */
document.addEventListener('keydown', (e) => {
  if (S.pagina !== 'analise' || e.target.tagName === 'INPUT') return;
  if (e.key === 'ArrowRight') pular(1);
  if (e.key === 'ArrowLeft') pular(-1);
});

/* ===================================================================== */
/*                          tela 3 · histórico                           */
/* ===================================================================== */
PAG.historico = async function () {
  const [pan, imp] = await Promise.all([api('series/panorama'), api('impacto')]);
  const cats = pan.categorias.categorias || [];

  /* Curvas em base 100, com a faixa de dispersão da categoria que mais se mexeu
     por dentro — é onde se vê que a média parada esconde item andando. */
  const linhas = cats.map((c) => ({
    nome: c.categoria, cor: corDe(c.categoria), semPontos: true,
    pontos: c.pontos.map((q, n) => ({ x: n, v: q.indice, rotulo: q.rotulo })),
  }));
  const maiorAmp = cats.reduce((a, b) => ((b.amplitude || 0) > (a.amplitude || 0) ? b : a),
    cats[0] || {});
  const bandas = maiorAmp.pontos ? [{ cor: corDe(maiorAmp.categoria),
    pontos: maiorAmp.pontos.filter((q) => q.p10 !== null)
      .map((q, n) => ({ x: n, lo: q.p10, hi: q.p90 })) }] : [];

  const r = pan.resumo;
  const agregado = `<div class="hist-grade">
    <div class="cartao pl-col">
      <h3>Os últimos 12 meses</h3>
      <p class="leg">Preço praticado, não o de tabela.</p>
      <div class="pl"><span class="r">Receita no período</span>
        <span class="v num">${brl(r.receita_12m, 0)}</span>
        <span class="o">${n2(r.volume_12m, 0)} unidades vendidas</span></div>
      <div class="pl"><span class="r">Preço médio da operação</span>
        <span class="v num">${brl(r.preco_medio_inicial)} → ${brl(r.preco_medio_final)}</span>
        <span class="o">média ponderada por volume</span></div>
      <div class="pl"><span class="r">Desconto médio praticado</span>
        <span class="v num">${pct(r.desconto_medio, 1)}</span>
        <span class="o">já descontado do preço acima</span></div>
      <div class="pl"><span class="r">Quem mais subiu</span>
        <span class="v num">${esc((r.categoria_que_mais_subiu || {}).categoria)}</span>
        <span class="o">${pctS((r.categoria_que_mais_subiu || {}).variacao, 1)} no período</span></div>
      <div class="pl"><span class="r">Maior movimento interno</span>
        <span class="v num">${esc(maiorAmp.categoria)}</span>
        <span class="o">${pct(maiorAmp.amplitude, 1)} entre o SKU que mais subiu e o que mais
        caiu</span></div>
    </div>
    <div class="cartao">
      <h3>Curva de preço por categoria</h3>
      <p class="leg">Base 100 no primeiro mês, para categorias de níveis diferentes caberem
      no mesmo eixo. A área sombreada é a faixa do 10º ao 90º percentil dos SKUs de
      ${esc(maiorAmp.categoria)} — a categoria está parada na média e os itens dentro dela
      não estão.</p>
      ${serie(linhas, { altura: 210, formato: (v) => n2(v, 0), bandas, rotulosX: 4 })}
      <hr class="fina">
      <h3>Alterações de preço por mês</h3>
      <p class="leg">Mais decisões não quer dizer mais alterações — e é por isso que o
      indicador da Popular Pet para este é "monitorar", não "aumentar".</p>
      ${barras((pan.alteracoes || []).map((a) => ({ rotulo: a.rotulo, valor: a.total,
    cor: css('--roxo') })), { rotulo: 70, altura: 21, formato: (v) => String(Math.round(v)) })}
    </div>
    <div class="cartao">
      <h3>Nível de preço de cada categoria</h3>
      <p class="leg">O que o índice esconde: o preço em reais.</p>
      <table><thead><tr><th>Categoria</th><th class="r">Há 12 meses</th><th class="r">Hoje</th>
        <th class="r">Variação</th><th class="r">Amplitude</th></tr></thead>
        <tbody>${cats.map((c) => `<tr>
          <td><span class="ponto" style="background:${corDe(c.categoria)}"></span>
            ${esc(c.categoria)}</td>
          <td class="r">${brl(c.preco_inicial)}</td><td class="r">${brl(c.preco_final)}</td>
          <td class="r">${pctS(c.variacao, 1)}</td><td class="r">${pct(c.amplitude, 1)}</td>
        </tr>`).join('')}</tbody></table>
      <p class="leg" style="margin-top:11px">Amplitude é a distância entre o SKU que mais
      subiu e o que mais caiu dentro da categoria. É aí que mora decisão de preço, não na
      média.</p>
    </div>
  </div>`;

  const escolhido = Number(S.filtros.decisao)
    || (imp.itens.length ? imp.itens.reduce((a, b) =>
      (Math.abs(b.margem_prevista_mes) > Math.abs(a.margem_prevista_mes) ? b : a)).decisao_id
      : null);

  $('#conteudo').innerHTML = `
    <div class="navbar">
      <div class="busca"><input type="text" placeholder="Ver o histórico de um SKU ou produto…"></div>
      <div class="ordena"><label>Período</label>
        <select><option>Últimos 12 meses</option><option>Últimos 6 meses</option>
          <option>Ano corrente</option></select></div>
      <div class="ordena"><label>Visão</label>
        <select><option>Agregado + item</option><option>Só o agregado</option>
          <option>Só o item</option></select></div>
      <a class="btn pequeno" href="/api/exportar/historico">Exportar CSV</a>
      <div class="passo"><span class="valor">${imp.aplicacoes} aplicações ·
        ${imp.com_placar} com placar</span></div>
    </div>
    ${agregado}
    <div id="detalheItem"></div>
    <div class="cartao" style="margin-top:13px">
      <h3>Todas as aplicações</h3>
      <p class="leg">Clique para trocar o item mostrado acima.</p>
      <div class="tab"><table><thead><tr><th>Quando</th><th>Item</th><th>Saída</th>
        <th class="r">Preço</th><th class="r">Prevista</th><th class="r">Realizada</th>
        <th class="c">Veredito</th></tr></thead><tbody>${imp.itens
    .sort((a, b) => Math.abs(b.margem_prevista_mes) - Math.abs(a.margem_prevista_mes))
    .map((x) => `<tr class="clicavel" onclick="verPlacar(${x.decisao_id})">
      <td>${dataBr(x.aplicada_em)}</td>
      <td><b>${esc(x.produto)}</b><div class="sku">${esc(x.sku)} · ${esc(x.canal)}</div></td>
      <td>${selo(x.recomendacao)}</td>
      <td class="r">${brl(x.preco_antes)} → ${brl(x.preco_depois)}</td>
      <td class="r">${brl(x.margem_prevista_mes, 0)}</td>
      <td class="r">${brl(x.margem_realizada_mes, 0)}</td>
      <td class="c">${x.pagou ? '<span class="selo ok">pagou</span>'
    : '<span class="selo nao">não</span>'}</td></tr>`).join('')}</tbody></table></div>
    </div>`;

  if (escolhido) await verPlacar(escolhido, imp);
};

async function verPlacar(decisaoId, imp) {
  const alvo = $('#detalheItem');
  if (!alvo) return;
  const k = imp || await api('impacto');
  const [p, hist] = await Promise.all([
    api('impacto/' + decisaoId),
    api('impacto/' + decisaoId).then((x) =>
      api('series/item/' + encodeURIComponent(x.sku) + '?canal=' + encodeURIComponent(x.canal))),
  ]);
  const antes = p.serie.filter((x) => x.semana < 0).length;
  const pts12 = (hist.item.pontos || []).filter((q) => q.preco)
    .map((q, n) => ({ x: n, v: q.preco, rotulo: q.rotulo }));

  const pl = (rot, val, obs, cls) => `<div class="pl ${cls || ''}">
    <span class="r">${esc(rot)}</span><span class="v num">${val}</span>
    ${obs ? `<span class="o">${esc(obs)}</span>` : ''}</div>`;

  alvo.innerHTML = `
    <h3 class="secao">${esc(p.produto)} · ${esc(p.canal)}</h3>
    <div class="hist-grade">
      <div class="cartao pl-col">
        <h3>Placar de todas as aplicações</h3>
        <p class="leg">${k.sintetico
    ? '<span class="selo alerta">depois: projeção sintética marcada</span> Quando o ERP '
      + 'publicar venda posterior à aplicação, o realizado substitui a projeção.'
    : 'Depois vindo do ERP.'}</p>
        ${pl('Aplicadas', String(k.aplicacoes), 'decisões que mudaram preço')}
        ${pl('Bateram o equilíbrio', `${k.pagaram} <small>de ${k.com_placar}</small>`,
    'a aposta se pagou')}
        ${pl('Margem prevista', brl(k.margem_prevista_mes, 0), 'por mês, volume constante')}
        ${pl('Margem realizada', brl(k.margem_realizada_mes, 0),
    'por mês, com o volume observado')}
        ${pl('Erro da previsão', pct(k.erro_relativo, 0), 'em módulo, sobre a prevista',
    (k.erro_relativo || 0) > 0.3 ? 'alerta' : '')}
      </div>
      <div class="cartao">
        <h3>Preço praticado nos últimos 12 meses</h3>
        <p class="leg">O histórico longo situa a decisão: um item que subiu o ano inteiro e
        recebe outra alta não é o mesmo caso de um parado há meses.</p>
        ${serie([{ nome: 'preço', cor: css('--roxo'), pontos: pts12 }],
    { altura: 165, legenda: false, rotulosX: 4 })}
        <hr class="fina">
        <h3>Volume semanal em torno da aplicação</h3>
        <p class="leg">A linha vertical é a aplicação de ${esc(p.recomendacao)} em
        ${dataBr(p.aplicada_em)}.</p>
        ${serie([{ nome: 'volume', cor: css('--lima'), tracejada: true,
    pontos: p.serie.map((x, n) => ({ x: n, v: x.volume, rotulo: dataBr(x.data) })) }],
    { altura: 145, formato: (v) => n2(v, 0), legenda: false,
      marcos: [{ x: antes - 0.5, rotulo: 'aplicação' }] })}
      </div>
      <div class="cartao">
        <h3>A aposta e o resultado</h3>
        <table class="pares">
          <tr><td>Preço</td><td>${brl(p.preco_antes)} → ${brl(p.preco_depois)}
            (${pctS(p.variacao_preco, 1)})</td></tr>
          <tr><td>Margem unitária</td><td>${brl(p.margem_unit_antes)} →
            ${brl(p.margem_unit_depois)}</td></tr>
          <tr><td>Volume semanal</td><td>${n2(p.volume_semana_antes, 0)} →
            ${n2(p.volume_semana_depois, 0)} (${pctS(p.variacao_volume, 1)})</td></tr>
          <tr><td>Equilíbrio exigido</td><td>${pctS(p.equilibrio_volume, 1)}</td></tr>
          <tr><td>Margem prevista no mês</td><td>${brl(p.margem_prevista_mes)}</td></tr>
          <tr><td>Margem realizada no mês</td><td>${brl(p.margem_realizada_mes)}</td></tr>
          <tr><td>Veredito</td><td>${p.pagou ? '<span class="selo ok">pagou</span>'
    : '<span class="selo nao">não pagou</span>'}</td></tr>
        </table>
        <p class="leg" style="margin-top:11px">${esc(p.aviso)}</p>
      </div>
    </div>`;
}

/* ===================================================================== */
/*                             tela 4 · base                             */
/* ===================================================================== */
PAG.base = async function () {
  const [erp, gov, env, llmS] = await Promise.all([
    api('erp'), api('governanca'), api('envelope'), api('llm'),
  ]);

  const testes = gov.testes.map((t) => `<li class="${t.ok === true ? 'ok'
    : t.ok === false ? 'nao' : 'na'}">${esc(t.garantia)}<small>${esc(t.detalhe)}</small></li>`)
    .join('');

  const limites = (env.limites || []).map((l) => `<tr>
    <td>${esc(l.limite)}<small style="display:block;color:var(--tinta-fraca)">${esc(l.base)}</small></td>
    <td>${esc(l.valor)}</td></tr>`).join('');
  const degrada = (env.degradacao || []).map((x) => `<li class="${x.para ? 'nao' : 'ok'}">
    sem ${esc(x.falta)}<small>${esc(x.efeito)}</small></li>`).join('');

  $('#conteudo').innerHTML = `
    <div class="an-grade">
      <div class="cartao">
        <h3>Entrada: CSV cru do ERP</h3>
        <p class="leg">Nenhuma coluna precisa ser renomeada antes. O sistema lê o arquivo,
        descobre o que é cada coluna e pede confirmação do que não tem certeza.</p>
        <div class="solta" id="solta" onclick="$('#arquivo').click()">
          <b>Solte o CSV aqui ou clique para escolher</b>
          <span>qualquer separador · acentuação em UTF-8 ou Latin-1</span>
        </div>
        <input type="file" id="arquivo" accept=".csv,.txt" hidden onchange="descobrirCsv(this)">
        <div id="mapaColunas"></div>
        <hr class="fina">
        <h3>Sincronizar a planilha do ERP</h3>
        <p class="leg">Última: ${erp.ultima ? dataHora(erp.ultima.quando) : '—'} ·
        ${erp.produtos || 0} produtos na base.</p>
        <div class="acoes">
          <button class="btn primario" onclick="sincronizar()">Sincronizar agora</button>
          <button class="btn" onclick="gerarRodada()">Gerar nova rodada</button>
        </div>
      </div>
      <div class="grade">
        <div class="cartao">
          <h3>Camada 3 · modelo de linguagem local</h3>
          <p class="leg">${esc(llmS.como)}</p>
          <table class="pares">
            <tr><td>Situação</td><td>${llmS.ativo
    ? '<span class="selo ok">ativo</span>' : '<span class="selo">desligado</span>'}</td></tr>
            ${llmS.ativo ? `<tr><td>Servidor</td><td>${esc(llmS.servidor)}</td></tr>
            <tr><td>Modelo</td><td>${esc(llmS.modelo)}</td></tr>` : ''}
          </table>
          <div class="acoes" style="margin-top:11px">
            <button class="btn" onclick="procurarModelo()">Procurar modelo</button>
            <button class="btn" onclick="testarLlm()" ${llmS.ativo ? '' : 'disabled'}>
              Testar a justificativa</button>
          </div>
          ${llmS.ativo ? '' : `<p class="leg" style="margin-top:11px">Como ligar um:</p>
            <ul class="checklist">${(llmS.receita || []).map((x) => `<li class="na">
              <b>${esc(x.ferramenta)}</b>${x.recomendado
    ? ' <span class="selo ok">mais simples</span>' : ''}<small>${esc(x.passos)}</small>
              </li>`).join('')}</ul>
            <p class="leg">A plataforma procura sozinha em ${(llmS.onde_procura || [])
    .map((o) => o.base.replace('http://localhost:', 'porta ').replace('/v1', ''))
    .join(', ')}. Nada precisa ser reiniciado.</p>`}
          <div id="saidaLlm"></div>
        </div>
        <div class="cartao">
          <h3>Governança · ${gov.testes_ok} de ${gov.testes_total} garantias verificadas agora</h3>
          <p class="leg">Não é declaração: a tela tenta apagar e alterar uma decisão a cada
          carregamento, e mostra o banco recusando.</p>
          <ul class="checklist">${testes}</ul>
          <hr class="fina">
          <table class="pares">
            <tr><td>Dado pessoal na plataforma</td><td>só o nome de quem decide</td></tr>
            <tr><td>O que sai da máquina</td><td>nada</td></tr>
            <tr><td>Decisões registradas</td><td>${gov.decisoes}</td></tr>
            <tr><td>Itens retidos para a gestora</td><td>${gov.retidos_para_gestora}</td></tr>
          </table>
        </div>
      </div>
    </div>

    <div class="an-grade" style="margin-top:13px">
      <div class="cartao">
        <h3>Produtos estratégicos · R04</h3>
        <p class="leg">A única marcação que não vem do ERP. Marcar exige dizer por quê, e
        fica registrado quem marcou e quando.</p>
        <table><thead><tr><th>Produto</th><th>Por quê</th><th class="r">Quem marcou</th>
          </tr></thead><tbody id="listaEstrategicos"></tbody></table>
        <p class="leg" style="margin-top:11px"><b>O que a marcação faz.</b> É roteamento, não
        cálculo: a recomendação sobe para a gestora e exige justificativa escrita. O preço
        sugerido sai igual ao de qualquer item.</p>
      </div>
      <div class="cartao">
        <h3>Envelope operacional · ${env.medido ? 'medido' : 'não medido'}</h3>
        <p class="leg">${env.medido ? 'Onde funciona e onde para. Medido nesta máquina em '
    + esc(String(env.medido_em || '').slice(0, 16).replace('T', ' ')) + ', não estimado.'
    : esc(env.como)}</p>
        <table class="pares">${limites}</table>
        ${degrada ? `<hr class="fina"><p class="leg">O que acontece quando falta cada peça</p>
          <ul class="checklist">${degrada}</ul>` : ''}
      </div>
    </div>`;

  const estr = await api('estrategicos');
  $('#listaEstrategicos').innerHTML = estr.itens.length ? estr.itens.map((x) => `<tr>
    <td><b>${esc(x.produto)}</b><div class="sku">${esc(x.sku)} · ${esc(x.categoria)} /
      ${esc(x.marca)}</div></td>
    <td>${esc(x.estrategico_nota)}</td>
    <td class="r">${esc(x.estrategico_por)}<div class="sku">${dataBr(x.estrategico_em)}</div></td>
    </tr>`).join('') : '<tr><td colspan="3" class="vazio">Nenhum item marcado.</td></tr>';
};

async function descobrirCsv(input) {
  const arq = input.files[0];
  if (!arq) return;
  const fd = new FormData();
  fd.append('arquivo', arq);
  $('#mapaColunas').innerHTML = '<div class="carregando">Descobrindo as colunas…</div>';
  try {
    const d = await api('ingestao/descobrir', { method: 'POST', body: fd });
    $('#mapaColunas').innerHTML = `
      <p class="leg" style="margin:13px 0 7px"><b>${d.achados.length} de ${d.colunas_lidas}
      colunas reconhecidas</b> · ${esc(d.metodo)} ·
      ${d.faltando.length ? `faltam: ${d.faltando.map((f) => esc(f.rotulo)).join(', ')}`
    : 'nenhum campo obrigatório ficou faltando'}</p>
      <div class="mapa">
        <div class="cab">Coluna do arquivo</div><div class="cab"></div>
        <div class="cab">Campo da plataforma</div><div class="cab">Confiança</div>
        ${d.achados.map((a) => `
          <div><span class="de">${esc(a.coluna)}</span></div>
          <div style="color:var(--tinta-fraca)">→</div>
          <div class="campo-alvo"><b>${esc(a.rotulo)}</b>
            <span class="ev">${esc(a.evidencia)}</span></div>
          <div><span class="barra-conf"><i class="${a.confirmar ? 'baixa' : ''}"
            style="width:${100 * a.confianca}%"></i></span>
            <span style="font-size:11.5px">${n2(a.confianca, 2)}</span></div>`).join('')}
      </div>
      <p class="leg" style="margin-top:11px">A descoberta não é um modelo de linguagem: cada
      coluna vira evidência de nome e de conteúdo, e o casamento coluna↔campo é resolvido como
      problema de atribuição. Abaixo de ${n2(d.limiar, 2)} de confiança, a tela exige
      confirmação humana antes de aplicar.</p>`;
    toast(`${d.achados.length} colunas reconhecidas em ${esc(arq.name)}.`);
  } catch (e) {
    $('#mapaColunas').innerHTML = `<div class="aviso">${esc(e.message)}</div>`;
  }
}

async function sincronizar() {
  try {
    const r = await api('erp/sincronizar', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ usuario: S.usuario }),
    });
    toast(r.mensagem);
    navegar();
  } catch (e) { toast(e.message, 'erro'); }
}

async function gerarRodada() {
  try {
    const r = await api('rodadas', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    toast(`Rodada de ${dataBr(r.data)} · ${r.itens} itens · ${r.otimizacao.metodo}.`);
    S.contexto = await api('contexto');
    irPara('painel');
  } catch (e) { toast(e.message, 'erro'); }
}

async function testarLlm() {
  $('#saidaLlm').innerHTML = '<div class="carregando">Pedindo ao modelo…</div>';
  try {
    const d = await api('llm/testar', { method: 'POST' });
    const reprovado = d.diagnostico && d.diagnostico.reprovado;
    $('#saidaLlm').innerHTML = `
      <div class="${reprovado ? 'aviso' : 'porque'}" style="margin-top:11px">
        <b>${esc(d.sku)} · ${esc(d.canal)}</b><br>${esc(d.texto)}
      </div>
      <p class="leg">${esc(d.origem)}${d.diagnostico && d.diagnostico.ms
    ? ` · ${d.diagnostico.ms} ms` : ''}</p>
      ${reprovado ? `<p class="leg"><b>O texto do modelo foi descartado.</b> Ele citou
        ${esc((d.diagnostico.numeros_errados || []).join(', '))}, que não existe entre os
        fatores da decisão. É para isso que a conferência existe: modelo pequeno quantizado
        troca dígito, e preço errado na tela do analista é pior que texto seco.</p>` : ''}`;
  } catch (e) {
    $('#saidaLlm').innerHTML = `<div class="aviso">${esc(e.message)}</div>`;
  }
}

/* ===================================================================== */
/*               o pipeline, como estado — não como tela                 */
/* ===================================================================== */
/* A plataforma é um serviço: o pipeline roda quando o dado chega, não quando
   alguém clica. Então ele não é uma tela, é uma faixa de estado no alto do
   painel — "o que você está vendo é de quando, e está atualizado?". O botão de
   atualizar fica ali para o caso de alguém querer forçar antes da hora, mas o
   caminho normal é não precisar dele. */
function faixaPipeline(pl) {
  const rodando = pl.rodando;
  const etapa = (pl.etapas || []).find((e) => e.chave === pl.etapa);
  const feitas = new Set((pl.historico || []).map((h) => h.etapa));
  const total = Math.max((pl.etapas || []).length - 1, 1);
  const progresso = Math.min(100, Math.round(100 * feitas.size / total));

  if (rodando) {
    return `<div class="faixa-pipe rodando pa-pipe">
      <span class="pulso"></span>
      <div class="txt"><b>Atualizando o cenário · ${esc(etapa ? etapa.titulo : '…')}</b>
        <span>${esc(pl.texto || '')}</span></div>
      <span class="barra"><i style="width:${progresso}%"></i></span>
      <span class="t">${pl.segundos != null ? n2(pl.segundos, 0) + ' s' : ''}</span>
    </div>`;
  }

  if (pl.erro) {
    return `<div class="faixa-pipe erro pa-pipe">
      <span class="ponto"></span>
      <div class="txt"><b>A última execução falhou</b><span>${esc(pl.erro)}</span></div>
      <button class="btn pequeno" onclick="rodarPipeline()">Tentar de novo</button>
    </div>`;
  }

  const q = pl.ultima || {};
  const quando = q.quando ? `${dataBr(q.quando)} às ${String(q.quando).slice(11, 16)}` : '';
  return `<div class="faixa-pipe pa-pipe">
    <span class="ponto ok"></span>
    <div class="txt"><b>Cenário atualizado${quando ? ' · ' + quando : ''}</b>
      <span>${esc(q.resumo || '')}${pl.proxima
    ? ' · o pipeline roda sozinho quando o coletor publicar dado novo, previsto para '
      + esc(pl.proxima) : ''}</span></div>
    <button class="btn pequeno" onclick="rodarPipeline()">Atualizar agora</button>
  </div>`;
}

function agendarPipeline() {
  clearTimeout(S.tPipe);
  S.tPipe = setTimeout(async () => {
    if (S.pagina !== 'painel') return;
    const novo = await api('pipeline');
    if (!novo.rodando && novo.tem_rodada) return navegar();
    PAG.painel();
  }, 800);
}

async function rodarPipeline() {
  try {
    await api('pipeline', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sincronizar: true, demonstracao: false, usuario: S.usuario }),
    });
    toast('Coletando dado novo e recalculando o cenário.');
    PAG.painel();
  } catch (e) { toast(e.message, 'erro'); }
}

async function procurarModelo() {
  toast('Procurando servidor local…');
  try {
    const s = await api('llm?procurar=1');
    toast(s.ativo ? `Encontrei: ${s.servidor} · ${s.modelo}`
      : 'Nenhum servidor local respondeu. Suba o LM Studio ou o Ollama e tente de novo.');
    navegar();
  } catch (e) { toast(e.message, 'erro'); }
}

/* Reescreve a justificativa de um item só. O texto determinístico continua na
   tela até o modelo responder — e volta se a conferência numérica reprovar. */
async function reescrever(recId) {
  const alvo = $('#justificativa');
  if (!alvo) return;
  const antes = alvo.innerHTML;
  alvo.innerHTML = '<span class="leg">Pedindo ao modelo local…</span>';
  try {
    const d = await api(`recomendacoes/${recId}/reescrever`, { method: 'POST' });
    const reprovado = d.diagnostico && d.diagnostico.reprovado;
    alvo.innerHTML = `${esc(d.texto)}
      <div class="leg" style="margin-top:9px">${esc(d.origem)}${d.diagnostico
      && d.diagnostico.ms ? ` · ${d.diagnostico.ms} ms` : ''}</div>
      ${reprovado ? `<div class="leg"><b>O texto do modelo foi descartado</b> porque citou
        ${esc((d.diagnostico.numeros_errados || []).join(', '))}, que não existe entre os
        fatores. É para isso que a conferência existe.</div>` : ''}
      <button class="btn pequeno" style="margin-top:9px"
        onclick="reescrever(${recId})">Pedir de novo</button>`;
  } catch (e) {
    alvo.innerHTML = antes;
    toast(e.message, 'erro');
  }
}

/* ===================================================================== */
/*                       tela 5 · câmara de preços                       */
/* ===================================================================== */
/* Uma tela, dois modos. O analista vê a cédula e vota; o gestor vê a apuração
   e homologa. A diferença não é cosmética: são papéis diferentes no mesmo
   processo, e misturar os dois foi exatamente o erro que esta tela corrige. */

const FASE_ROTULO = {
  'aguardando gestor': 'esperando o gestor',
  'em votacao': 'em votação',
  'sem voto': 'sem voto',
  travada: 'travada por regra',
  homologada: 'homologada',
  enviada: 'no ERP',
};

PAG.camara = async function () {
  const p = await api('camara' + (S.filtros.fase ? `?fase=${encodeURIComponent(S.filtros.fase)}` : ''));
  const gestor = papelAtual() !== 'Analista';
  S.camara = p;

  if (!p.rodada) {
    $('#conteudo').innerHTML = `<div class="vazio">Nenhuma rodada ainda. O pipeline roda
      quando a coleta da noite chega — ou você pode disparar pelo painel.</div>`;
    return;
  }

  const pr = p.prazo || {};
  const apertado = pr.segundos != null && pr.segundos < 2 * 3600;
  const meus = gestor ? p.contagem['aguardando gestor'] || 0
    : (p.itens || []).filter((i) => ['sem voto', 'em votacao'].includes(i.fase)
      && !(i.votos || []).some((v) => v.usuario === S.usuario)).length;

  /* ----------------------------------------------------- faixa de contexto */
  const faixa = `<div class="cam-faixa ${apertado ? 'apertado' : ''}">
    <div class="pz">
      <span class="r">Pauta do dia</span>
      <b>${pr.fechado ? `fecha amanhã às ${esc(pr.hora_fechamento)}`
    : `fecha hoje às ${esc(pr.hora_fechamento)}`}</b>
      <small>${pr.fechado
    ? `passou das ${esc(pr.hora_fechamento)}: o que ficou aberto entra na apuração de amanhã`
    : `faltam ${pr.horas}h${String(pr.minutos).padStart(2, '0')}`}</small>
    </div>
    <div class="pol">
      <span class="r">Se ninguém decidir até as ${esc(pr.hora_fechamento)}</span>
      <div class="chave ${p.politica.valor === 'aplicar' ? 'on' : ''}
        ${gestor ? '' : 'travada'}" ${gestor
    ? `onclick="trocarPolitica('${p.politica.valor === 'aplicar' ? 'manter' : 'aplicar'}')"`
    : ''} title="${gestor ? 'Clique para trocar a política'
    : 'A política de omissão é do gestor'}">
        <i></i><span>${esc(p.politica.rotulo)}</span>
      </div>
      <small>${esc(p.politica.explicacao)}${p.politica.por
    ? ` · definida por ${esc(p.politica.por)}` : ''}${gestor ? ''
    : ' · só o gestor troca'}</small>
    </div>
    <div class="risco">
      <span class="r">Em jogo no silêncio</span>
      <b class="num">${brl(Math.abs(p.risco_omissao), 0)}</b>
      <small>${p.em_aberto} ${p.em_aberto === 1 ? 'item ainda sem decisão'
    : 'itens ainda sem decisão'} · ${p.politica.valor === 'aplicar'
    ? 'sairiam sozinhos às ' + esc(pr.hora_fechamento)
    : 'ficariam como estão'}</small>
    </div>
  </div>`;

  /* ----------------------------------------------------- chamada por papel */
  const chamada = `<div class="chamada">
    <div class="frase">
      <b>${gestor ? `${meus} ${meus === 1 ? 'item espera' : 'itens esperam'} sua homologação`
    : meus ? `${meus} ${meus === 1 ? 'item espera' : 'itens esperam'} seu voto`
      : 'Você votou em tudo que está aberto'}</b>
      <span>${gestor
    ? 'A câmara fechou o parecer. Nada vai ao ERP sem você assinar.'
    : 'Você dá parecer e vota. O preço final é homologado pelo gestor.'}</span>
    </div>
    <div class="mini">
      <div><b class="num">${p.total}</b><span>na pauta</span></div>
      <div><b class="num">${p.contagem['aguardando gestor'] || 0}</b><span>com consenso</span></div>
      <div><b class="num">${(p.contagem.homologada || 0) + (p.contagem.enviada || 0)}</b>
        <span>homologadas</span></div>
      <div><b class="num">${p.a_enviar}</b><span>a enviar</span></div>
    </div>
    ${gestor ? `<div style="display:flex;flex-direction:column;gap:7px">
      <button class="btn-grande" ${p.contagem['aguardando gestor'] ? '' : 'disabled'}
        onclick="homologarLote()">Homologar o consenso (${p.contagem['aguardando gestor'] || 0})</button>
      <button class="btn-grande" style="background:rgba(255,255,255,.14)"
        ${p.a_enviar ? '' : 'disabled'} onclick="enviarErp()">Gerar arquivo do ERP
        (${p.a_enviar})</button>
    </div>` : ''}
  </div>`;

  /* --------------------------------------------------------------- filtros */
  const fases = ['aguardando gestor', 'em votacao', 'sem voto', 'travada',
    'homologada', 'enviada'];
  const filtro = `<div class="navbar">
    <div class="busca"><input type="text" id="buscaCam" value="${esc(S.filtros.busca || '')}"
      placeholder="Ir direto a um SKU ou produto…" oninput="filtrarCamara(this.value)"></div>
    <div class="cam-chips">
      <a href="#/camara" class="${S.filtros.fase ? '' : 'ativo'}">todas · ${p.total}</a>
      ${fases.filter((f) => p.contagem[f]).map((f) =>
    `<a href="#/camara?fase=${encodeURIComponent(f)}"
        class="${S.filtros.fase === f ? 'ativo' : ''} f-${f.replace(/ /g, '-')}"
      >${FASE_ROTULO[f]} · ${p.contagem[f]}</a>`).join('')}
    </div>
    <div class="cam-quem">${(p.pendencias || []).map((q) =>
    `<span title="${esc(q.nome)} votou em ${q.votou} e falta ${q.falta}">
        ${esc(q.nome.split(' ')[0])} <b>${q.votou}</b>/<small>${q.votou + q.falta}</small></span>`)
    .join('')}</div>
  </div>`;

  const lista = (p.itens || []).length
    ? p.itens.map((i) => cartaoCamara(i, gestor)).join('')
    : `<div class="vazio">Nada nesta faixa.</div>`;

  $('#conteudo').innerHTML = `<div class="camara">
    ${faixa}${chamada}${filtro}<div class="cam-lista">${lista}</div></div>`;
};

/* Um item da pauta: a cédula, os votos e o que falta. */
function cartaoCamara(i, gestor) {
  const meu = (i.votos || []).find((v) => v.usuario === S.usuario);
  const fechado = ['homologada', 'enviada'].includes(i.fase);
  const cands = (i.candidatos || []).map((c) => {
    const meuVoto = meu && meu.candidato_id === c.id;
    const venceu = i.consenso === c.id;
    const homologado = i.homologada
      && Math.abs(i.homologada.preco_final - c.preco) < 0.005;
    return `<div class="ced ${venceu ? 'venceu' : ''} ${homologado ? 'homologado' : ''}">
      <div class="pr num">${brl(c.preco)}</div>
      <div class="qq"><span class="rot">${esc(c.rotulo)}</span>
        <span class="vt">${c.n ? esc(c.votos.join(', ')) : 'sem voto'}</span></div>
      <div class="nv">${c.n ? `<b>${c.n}</b> ${c.n === 1 ? 'voto' : 'votos'}` : ''}</div>
      ${fechado || i.bloqueado ? ''
    : gestor
      ? `<button class="btn pequeno" onclick="homologarItem(${i.recomendacao_id},${c.id},
          ${venceu ? 'false' : 'true'})">${venceu ? 'Homologar' : 'Homologar este'}</button>`
      : `<button class="btn pequeno ${meuVoto ? 'ativo' : ''}"
          onclick="votar(${i.recomendacao_id},${c.id},${i.exige_justificativa})"
        >${meuVoto ? '✓ seu voto' : 'Votar'}</button>`}
    </div>`;
  }).join('');

  return `<div class="cam-item f-${i.fase.replace(/ /g, '-')}">
    <div class="cab">
      <div class="quem">
        <b>${esc(i.produto)}</b>
        <span class="meta">${esc(i.sku)} · ${esc(i.canal)} · ${esc(i.categoria)} ·
          curva ${esc(i.curva)}</span>
      </div>
      ${selo(i.recomendacao)}
      ${selo('risco ' + String(i.nivel_risco).toLowerCase(), i.nivel_risco)}
      ${i.estrategico ? '<span class="selo estrategico">★ estratégico</span>' : ''}
      <span class="selo fase f-${i.fase.replace(/ /g, '-')}">${FASE_ROTULO[i.fase]}</span>
      <span class="jogo num">${brl(i.valor_em_jogo, 0)} <small>em jogo</small></span>
    </div>
    <div class="cedula">${cands}</div>
    <div class="pe">
      <span class="falta">${esc(i.falta)}</span>
      ${i.homologada && i.homologada.justificativa
    ? `<span class="falta" style="color:var(--baixar)"><b>Gestor contrariou a câmara:</b>
        ${esc(i.homologada.justificativa)}</span>` : ''}
      <div class="acs">
        ${i.bloqueado || fechado ? '' : `<button class="btn pequeno"
          onclick="janelaPropor(${i.recomendacao_id},${i.preco_sugerido},${i.piso})"
          >Propor outro preço</button>`}
        <button class="btn pequeno" onclick="janelaThread(${i.recomendacao_id})"
          >Discussão${i.mensagens ? ` (${i.mensagens})` : ''}</button>
        <a class="btn pequeno" href="#/analise?item=${i.recomendacao_id}">Ver a análise</a>
      </div>
    </div>
  </div>`;
}

/* ------------------------------------------------------ ações da câmara */
async function filtrarCamara(txt) {
  clearTimeout(S.tBusca);
  S.tBusca = setTimeout(() => {
    irPara('camara', Object.assign({}, S.filtros, { busca: txt || undefined }));
  }, 300);
}

async function trocarPolitica(valor) {
  try {
    const d = await api('camara/politica', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ valor, usuario: S.usuario }),
    });
    toast(`Política de omissão: ${d.rotulo}.`);
    PAG.camara();
  } catch (e) { toast(e.message, 'erro'); }
}

async function votar(recId, candId, exigeNota) {
  if (exigeNota) return janelaVoto(recId, candId);
  try {
    const a = await api('camara/votar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ recomendacao_id: recId, candidato_id: candId,
        usuario: S.usuario }),
    });
    toast(a.consenso ? `Consenso em ${brl(a.preco_consenso)} — vai para o gestor.`
      : a.falta);
    PAG.camara();
  } catch (e) { toast(e.message, 'erro'); }
}

function janelaVoto(recId, candId) {
  const j = abrirJanela('Parecer obrigatório (R04)', `
    <p class="leg">Produto estratégico: o voto só entra com parecer escrito. É esse
    registro que explica a decisão seis meses depois.</p>
    <textarea id="notaVoto" rows="4" placeholder="Por que este preço?"></textarea>`,
  `<button class="btn" onclick="fecharJanela()">Cancelar</button>
   <button class="btn primario" onclick="confirmarVoto(${recId},${candId})">Votar</button>`);
  const t = j.querySelector('#notaVoto');
  if (t) t.focus();
}

async function confirmarVoto(recId, candId) {
  const txt = ($('#notaVoto') || {}).value || '';
  try {
    const a = await api('camara/votar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ recomendacao_id: recId, candidato_id: candId,
        usuario: S.usuario, comentario: txt }),
    });
    fecharJanela();
    toast(a.consenso ? `Consenso em ${brl(a.preco_consenso)}.` : a.falta);
    PAG.camara();
  } catch (e) { toast(e.message, 'erro'); }
}

function janelaPropor(recId, sugerido, piso) {
  abrirJanela('Propor outro preço', `
    <p class="leg">O preço entra na cédula e todos passam a poder votar nele. Não pode
    ficar abaixo do piso de margem mínima (${brl(piso)} · R01).</p>
    <div class="campo"><label>Preço</label>
      <input type="number" step="0.01" id="novoPreco" value="${sugerido}"></div>
    <div class="campo" style="margin-top:11px"><label>Por quê</label>
      <input type="text" id="porque" placeholder="o argumento que os outros vão ler"></div>`,
  `<button class="btn" onclick="fecharJanela()">Cancelar</button>
   <button class="btn primario" onclick="confirmarProposta(${recId})">Pôr na cédula</button>`);
  const e = $('#novoPreco');
  if (e) { e.focus(); e.select(); }
}

async function confirmarProposta(recId) {
  try {
    await api('camara/propor', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ recomendacao_id: recId,
        preco: parseFloat(($('#novoPreco') || {}).value),
        justificativa: ($('#porque') || {}).value || '', usuario: S.usuario }),
    });
    fecharJanela();
    toast('Preço na cédula. Agora vote nele, se for o seu.');
    PAG.camara();
  } catch (e) { toast(e.message, 'erro'); }
}

async function homologarItem(recId, candId, pedeJustificativa) {
  if (pedeJustificativa) {
    abrirJanela('Homologar outro preço', `
      <p class="leg">Você está homologando um preço diferente do que a câmara apurou.
      A justificativa é obrigatória e vai no arquivo do ERP, ao lado do preço.</p>
      <textarea id="jst" rows="4" placeholder="Por que contrariar a câmara?"></textarea>`,
    `<button class="btn" onclick="fecharJanela()">Cancelar</button>
     <button class="btn primario" onclick="confirmarHomologacao(${recId},${candId})"
       >Homologar</button>`);
    const t = $('#jst');
    if (t) t.focus();
    return;
  }
  confirmarHomologacao(recId, candId);
}

async function confirmarHomologacao(recId, candId) {
  try {
    const d = await api('camara/homologar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ recomendacao_id: recId, candidato_id: candId,
        usuario: S.usuario, justificativa: (($('#jst') || {}).value || '') }),
    });
    fecharJanela();
    toast(`Homologado a ${brl(d.preco_final)}. Entra no próximo arquivo do ERP.`);
    PAG.camara();
  } catch (e) { toast(e.message, 'erro'); }
}

async function homologarLote() {
  try {
    const d = await api('camara/homologar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lote: true, usuario: S.usuario }),
    });
    toast(`${d.n} item(ns) homologado(s)${d.recusados.length
      ? ` · ${d.recusados.length} recusado(s)` : ''}.`);
    PAG.camara();
  } catch (e) { toast(e.message, 'erro'); }
}

function enviarErp() {
  window.location = `/api/exportar/aprovadas?novos=1&registrar=1&usuario=${
    encodeURIComponent(S.usuario)}`;
  setTimeout(() => { toast('Arquivo gerado. Os itens saem da fila de envio.'); PAG.camara(); },
    1200);
}

/* ------------------------------------------------------------- discussão */
async function janelaThread(recId) {
  const d = await api(`camara/${recId}`);
  const msgs = (d.thread || []).map((m) => `<div class="msg ${esc(m.papel)} t-${esc(m.tipo)}">
    <div class="ab"><b>${esc(m.autor)}</b>
      <span>${esc(m.papel)}${m.tipo === 'voto' ? ' · voto' : ''}</span>
      <small>${esc(String(m.criado_em).slice(11, 16))}</small></div>
    <p>${esc(m.texto)}</p>
    ${m.candidato_preco ? `<span class="cp num">${brl(m.candidato_preco)}</span>` : ''}
  </div>`).join('');

  abrirJanela(`${d.produto} · ${d.sku} ${d.canal}`, `
    <div class="thread" id="thread">${msgs || '<div class="vazio">Sem mensagens.</div>'}</div>
    <div class="dizer">
      <textarea id="fala" rows="2" placeholder="Seu parecer…"></textarea>
      <div class="bts">
        <button class="btn pequeno" onclick="perguntarAgente(${recId})"
          >Perguntar ao agente</button>
        <button class="btn primario pequeno" onclick="dizer(${recId})">Enviar parecer</button>
      </div>
    </div>`);
  const t = $('#thread');
  if (t) t.scrollTop = t.scrollHeight;
}

async function dizer(recId, aoAgente) {
  const el = $('#fala');
  const texto = (el || {}).value || '';
  if (!texto.trim()) return toast('Escreva algo antes de enviar.', 'erro');
  if (el) { el.disabled = true; }
  try {
    await api('camara/falar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ recomendacao_id: recId, texto,
        usuario: S.usuario, ao_agente: !!aoAgente }),
    });
    fecharJanela();
    janelaThread(recId);
  } catch (e) {
    if (el) el.disabled = false;
    toast(e.message, 'erro');
  }
}

async function perguntarAgente(recId) {
  const el = $('#fala');
  if (!((el || {}).value || '').trim()) {
    el.value = 'Por que esse preço?';
  }
  const t = $('#thread');
  if (t) {
    t.insertAdjacentHTML('beforeend',
      '<div class="msg Agente"><div class="ab"><b>Motor de precificação</b></div>'
      + '<p class="leg">pensando…</p></div>');
    t.scrollTop = t.scrollHeight;
  }
  dizer(recId, true);
}


/* --------------------------------- voto e homologação a partir da Análise */
/* Votar aqui avança a fila: o analista está percorrendo itens, e parar para
   rever o que acabou de votar é o oposto do que a tela serve. O cronômetro
   acompanha o voto, que é o que vira a linha de base de tempo. */
async function votarNaAnalise(recId, candId, exigeNota) {
  if (exigeNota) {
    abrirJanela('Parecer obrigatório (R04)', `
      <p class="leg">Produto estratégico: o voto só entra com parecer escrito.</p>
      <textarea id="notaVoto" rows="4" placeholder="Por que este preço?"></textarea>`,
    `<button class="btn" onclick="fecharJanela()">Cancelar</button>
     <button class="btn primario" onclick="votarNaAnaliseJa(${recId},${candId})"
       >Votar</button>`);
    return;
  }
  votarNaAnaliseJa(recId, candId);
}

async function votarNaAnaliseJa(recId, candId) {
  try {
    const a = await api('camara/votar', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ recomendacao_id: recId, candidato_id: candId,
        usuario: S.usuario, comentario: (($('#notaVoto') || {}).value || ''),
        segundos: (Date.now() - S.relogio) / 1000 }),
    });
    fecharJanela();
    toast(a.consenso ? `Consenso em ${brl(a.preco_consenso)} — vai para o gestor.`
      : a.falta);
    S.fila = S.fila.filter((x) => x !== recId);
    if (!S.fila.length) return irPara('camara');
    S.indice = Math.min(S.indice, S.fila.length - 1);
    await desenharItem(S.fila[S.indice]);
  } catch (e) { toast(e.message, 'erro'); }
}

async function homologarDaAnalise(recId, candId, pedeJustificativa) {
  await homologarItem(recId, candId, pedeJustificativa);
  await desenharItem(recId);
}
