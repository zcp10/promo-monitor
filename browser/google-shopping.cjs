/* Fonte experimental. Navegação normal; sem proxies, stealth ou solução de CAPTCHA. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const DAY = 86400000, HOUR = 3600000;
const DEFAULTS = {intervalSeconds:60, hourlyLimit:5, cooldownHours:24,
  cacheHours:720, timeoutMs:45000, maxResults:30, headless:false, channel:'chrome'};

function blocked(url, text, status) {
  return [403,429].includes(status) || /\/sorry(?:\/|\?)/i.test(url) ||
    /tr[aá]fego incomum|unusual traffic|not a robot|n[aã]o sou um rob[oô]|verify you are human|automated queries/i.test(text);
}
function money(text) {
  const m=String(text).match(/R\$\s*(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})(?!\d)/);
  return m ? Number(m[1].replace(/\./g,'').replace(',','.')) : null;
}
function gate(state, now, cfg) {
  if ((state.blockedUntil || 0)>now) return {status:'blocked_cooldown', nextAt:state.blockedUntil};
  const attempts=(state.attempts || []).filter(t=>now-t<DAY);
  const hour=attempts.filter(t=>now-t<HOUR);
  if(hour.length>=cfg.hourlyLimit) return {status:'hourly_limit',nextAt:hour[0]+HOUR};
  const next=(state.lastAttempt || 0)+cfg.intervalSeconds*1000;
  if(next>now) return {status:'interval',nextAt:next};
  return null;
}

/* Executada no DOM. Nenhuma chamada a APIs privadas nem leitura de estado interno. */
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

function save(file, state) {
  fs.writeFileSync(file+'.tmp',JSON.stringify(state,null,2));
  fs.renameSync(file+'.tmp',file);
}


function shoppingURL(query) {
  const url=new URL('https://www.google.com/search');
  url.searchParams.set('q',query); url.searchParams.set('udm','28');
  return url.href;
}
function browserOptions(cfg,dir) {
  return {channel:cfg.channel || 'chrome',headless:false,chromiumSandbox:true,
    locale:'pt-BR',viewport:null,args:['--start-maximized']};
}
function readState(dir) {
  const file=path.join(dir,'state.json');
  return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file,'utf8')) : {attempts:[],cache:{}};
}
function remember(state,dir,query,result,cfg) {
  const now=Date.now(); state.cache ||= {};
  state.cache=Object.fromEntries(Object.entries(state.cache).filter(([,v])=>now-v.at<cfg.cacheHours*HOUR));
  state.cache[query.trim().toLowerCase().replace(/\s+/g,' ')]={at:now,result};
  save(path.join(dir,'state.json'),state);
}
async function readCurrentPage(page,query,cfg,dir) {
  const body=await page.locator('body').innerText();
  const isBlocked=blocked(page.url(),body);
  const cards=isBlocked?[]:await page.evaluate(readCards);
  const quotes=toQuotes(cards,cfg.maxResults);
  const evidenceDir=path.join(dir,'evidence',new Date().toISOString().replace(/[:.]/g,'-'));
  fs.mkdirSync(evidenceDir,{recursive:true});
  await page.screenshot({path:path.join(evidenceDir,isBlocked?'blocked.png':'shopping.png')});
  fs.writeFileSync(path.join(evidenceDir,'page.json'),JSON.stringify({query,url:page.url(),title:await page.title(),body,cards,quotes},null,2));
  return {status:isBlocked?'blocked':quotes.length?'ok':'layout_unrecognized',query,quotes,
    evidenceDir,stores:[...new Set(quotes.map(q=>q.store))]};
}
async function acknowledgeManualResume(page,cfg,dir) {
  const state=readState(dir);
  if(!state.blockedUntil) return false;
  let url;try{url=new URL(page.url());}catch{return false;}
  if(url.hostname!=='www.google.com' || url.pathname!=='/search' || url.searchParams.get('udm')!=='28') return false;
  const body=await page.locator('body').innerText();
  if(blocked(page.url(),body))return false;
  const cards=await page.evaluate(readCards);
  if(!toQuotes(cards,cfg.maxResults).length)return false;
  state.blockedUntil=0;
  state.manualResume={at:Date.now(),query:url.searchParams.get('q'),reason:'Shopping visível no mesmo perfil após intervenção manual'};
  save(path.join(dir,'state.json'),state);
  return true;
}
async function searchOnPage(page,query,options={}) {
  const cfg={...DEFAULTS,...options};
  const dir=path.resolve(cfg.stateDir);
  for(const key of ['intervalSeconds','hourlyLimit','cooldownHours','cacheHours'])
    if(!Number.isFinite(cfg[key]) || cfg[key]<=0)throw Error(`Configuração inválida: ${key}`);
  await acknowledgeManualResume(page,cfg,dir);
  let state=readState(dir),now=Date.now();
  const key=query.trim().toLowerCase().replace(/\s+/g,' ');
  const hit=state.cache?.[key];
  if(hit && now-hit.at<cfg.cacheHours*HOUR)return {...hit.result,status:'cached'};
  let denied=gate(state,now,cfg);
  if(denied?.status==='interval'){
    // A fila aguarda o intervalo; não perde uma oferta apenas porque chegou logo após outra.
    await new Promise(r=>setTimeout(r,Math.min(60000,Math.max(0,denied.nextAt-Date.now()))));
    state=readState(dir);now=Date.now();denied=gate(state,now,cfg);
  }
  if(denied)return {...denied,query,quotes:[]};
  state.attempts=(state.attempts || []).filter(t=>now-t<DAY);
  state.attempts.push(now);state.lastAttempt=now;save(path.join(dir,'state.json'),state);
  const response=await page.goto(shoppingURL(query),{waitUntil:'domcontentloaded',timeout:cfg.timeoutMs});
  if(!blocked(page.url(),await page.locator('body').innerText(),response?.status()))
    await page.getByText(/R\$\s*\d/).first().waitFor({timeout:15000}).catch(()=>{});
  const result=await readCurrentPage(page,query,cfg,dir);
  if([403,429].includes(response?.status()))result.status='blocked',result.quotes=[];
  if(result.status==='blocked'){
    state.blockedUntil=Date.now()+cfg.cooldownHours*HOUR;state.blockedQuery=query;
    save(path.join(dir,'state.json'),state);
    result.nextAt=state.blockedUntil;result.manualAction='A janela continua aberta. Resolva a verificação manualmente; não haverá novas buscas durante o bloqueio.';
  }else if(result.quotes.length)remember(state,dir,query,result,cfg);
  return result;
}
function workerAlive(dir) {
  try{
    const worker=JSON.parse(fs.readFileSync(path.join(dir,'visible-worker.json'),'utf8'));
    process.kill(worker.pid,0);
    return Date.now()-worker.heartbeat<15000;
  }catch{return false;}
}
async function waitFor(check,timeoutMs) {
  const until=Date.now()+timeoutMs;
  while(Date.now()<until){const value=check();if(value)return value;await new Promise(r=>setTimeout(r,250));}
  throw Error('Tempo limite aguardando a sessão visível do Shopping');
}
async function collect(query,options={}) {
  const cfg={...DEFAULTS,...options};
  const dir=path.resolve(cfg.stateDir || path.join(__dirname,'../.shopping-browser'));
  fs.mkdirSync(dir,{recursive:true});
  try{
    if(!workerAlive(dir)){
      const state=readState(dir),now=Date.now(),key=query.trim().toLowerCase().replace(/\s+/g,' ');
      const hit=state.cache?.[key];
      if(hit && now-hit.at<cfg.cacheHours*HOUR)return {...hit.result,status:'cached'};
      const denied=gate(state,now,cfg);if(denied)return {...denied,query,quotes:[]};
      if(fs.existsSync(path.join(dir,'collector.lock')))return {status:'busy',query,quotes:[]};
      const {spawn}=require('node:child_process');
      const fd=fs.openSync(path.join(dir,'visible-worker.log'),'a');
      const child=spawn(process.execPath,[path.join(__dirname,'sessao-visivel.cjs'),dir],{
        detached:true,windowsHide:true,stdio:['ignore',fd,fd],env:process.env
      });
      child.unref();fs.closeSync(fd);
      await waitFor(()=>workerAlive(dir),60000);
    }
    const id=`${Date.now()}-${process.pid}-${require('node:crypto').randomBytes(4).toString('hex')}`;
    const inbox=path.join(dir,'requests'),outbox=path.join(dir,'responses');
    fs.mkdirSync(inbox,{recursive:true});fs.mkdirSync(outbox,{recursive:true});
    const request=path.join(inbox,id+'.json'),response=path.join(outbox,id+'.json');
    fs.writeFileSync(request+'.tmp',JSON.stringify({id,query,options:cfg,createdAt:Date.now()}));
    fs.renameSync(request+'.tmp',request);
    await waitFor(()=>fs.existsSync(response),180000);
    const result=JSON.parse(fs.readFileSync(response,'utf8'));fs.unlinkSync(response);
    return result;
  }catch(e){return {status:'error',query,quotes:[],error:String(e.message).slice(0,500)};}
}
if(require.main===module){
  let request;
  try{request=JSON.parse(fs.readFileSync(0,'utf8'));}catch{console.error('Envie JSON via stdin: {"query":"JBL Tune 770NC","options":{}}');process.exit(2);}
  if(typeof request.query!=='string' || !request.query.trim())process.exit(2);
  collect(request.query,request.options).then(r=>console.log(JSON.stringify(r))).catch(e=>{console.error(e.message);process.exitCode=1;});
}
module.exports={collect,readCards,toQuotes,money,blocked,gate,DEFAULTS,shoppingURL,browserOptions,
  readState,save,readCurrentPage,acknowledgeManualResume,searchOnPage,remember};
