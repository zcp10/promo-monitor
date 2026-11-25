() => {
function money(text) {
  const m=String(text).match(/R\$\s*(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})(?!\d)/);
  return m ? Number(m[1].replace(/\./g,'').replace(',','.')) : null;
}
function readCards() {
  const priceRe=/R\$\s*\d[\d.]*,\d{2}/;
  const visible=e=>!!e.getClientRects().length && getComputedStyle(e).visibility!=='hidden';
  const old=e=>!!e.closest('del,s,strike') || /line-through/.test(getComputedStyle(e).textDecorationLine);
  const installmentOrShipping=e=> {
    const bad=/(?:\d+\s*x\s*(?:de\s*)?|frete\s*(?:de\s*)?|entrega\s*|de\s*)R\$/i;
    if(/\/m[eê]s|parcelas?|\bpor m[eê]s\b/i.test(e.innerText+' '+(e.getAttribute('aria-label') || ''))) return true;
    if(bad.test(e.innerText)) return true;
    const parent=e.parentElement;
    return !!(parent && parent.innerText.length<100 && !parent.querySelector('h2,h3,[role="heading"]') && bad.test(parent.innerText));
  };
  const currentPrices=root=>Array.from(root.querySelectorAll('span,b,strong,div'))
    .filter(e=>visible(e) && !old(e) && e.innerText.length<100 && priceRe.test(e.innerText)
      && !Array.from(e.children).some(ch=>priceRe.test(ch.innerText || ''))
      && !installmentOrShipping(e));
  const known=['Amazon','Mercado Livre','Magazine Luiza','Magalu','KaBuM!','KaBuM',
    'Casas Bahia','Pichau','Terabyte','Carrefour','Fast Shop','JBL','Shopee',
    'Samsung','Extra','Pontofrio','Americanas','Dressinn','Latam Pass Shopping'];
  const out=[], seen=new Set();
  for(const priceNode of currentPrices(document.body)) {
    let card=priceNode;
    for(let depth=0; card && depth<9; depth++, card=card.parentElement) {
      const evidence=(card.innerText || '').trim();
      if(evidence.length>2200) break;
      const heading=card.querySelector('h3,h2,[role="heading"],[data-product-title]');
      const image=card.querySelector('img[alt]');
      const title=(heading?.innerText || card.querySelector('[role="img"][title]')?.getAttribute('title') || image?.alt || '').trim();
      if(title.length<10 || /R\$/.test(title)) continue;
      const prices=currentPrices(card);
      // Vários preços atuais no mesmo bloco: não adivinha qual pertence ao produto.
      if(prices.length!==1) continue;
      const links=Array.from(card.querySelectorAll('a[href]'));
      const external=links.find(a=> {
        try {const u=new URL(a.href);return /^https?:$/.test(u.protocol) &&
          !/(^|\.)(google\.[a-z.]+|gstatic\.com|googleusercontent\.com)$/.test(u.hostname);}
        catch {return false;}
      });
      const merchant=card.querySelector('[data-merchant-name],[data-source],[aria-label^="Vendido por"],.WJMUdc');
      const lines=evidence.split('\n').map(s=>s.trim()).filter(Boolean);
      let store=(merchant?.getAttribute('data-merchant-name') || merchant?.getAttribute('data-source') ||
        merchant?.innerText || '').trim();
      if(!store) store=lines.find(l=>known.some(k=>l.toLowerCase()===k.toLowerCase())) || '';
      if(!store) store=lines.find(l=>/^Vendido por\s+/i.test(l))?.replace(/^Vendido por\s+/i,'') || '';
      if(!store && external && external.innerText.trim()!==title && external.innerText.trim().length<70)
        store=external.innerText.trim();
      // Marca no título/imagem não prova quem é a loja.
      if(!store || !evidence.includes(store)) continue;
      const url=external?.href || links.find(a=>a.href.includes('/shopping/') || a.href.includes('/url?'))?.href;
      const priceText=prices[0].innerText;
      const key=[title.toLowerCase(),store.toLowerCase(),priceText].join('|');
      if(!seen.has(key)) {seen.add(key); out.push({title,priceText,store,url:url || null,evidence});}
      break;
    }
  }
  return out;
}

function toQuotes(cards, limit=30) {
  const seen=new Set(), out=[];
  for(const c of cards) {
    const price=money(c.priceText);
    if(!price || !c.title || !c.store || !c.evidence?.includes(c.store)) continue;
    const key=JSON.stringify([c.title.toLowerCase(),c.store.toLowerCase(),price]);
    if(seen.has(key)) continue;
    seen.add(key);
    out.push({provider:'google_browser',title:c.title,price,store:c.store,
      shipping:null,url:c.url || null});
  }
  return out.slice(0,limit);
}


return toQuotes(readCards(),30);
}