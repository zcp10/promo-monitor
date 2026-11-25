/* Uma janela e um perfil para toda a fila. Sem portas de depuração ou código remoto. */
'use strict';
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const stateDir=path.resolve(process.argv[2] || path.join(__dirname,'../.shopping-browser'));
const controlDir=path.resolve(process.argv[3] || path.join(__dirname,'../monitoramento-visual'));
const urlFile=process.argv[4]?path.resolve(process.argv[4]):null;
const lock=path.join(stateDir,'collector.lock');
const metadata=path.join(stateDir,'visible-worker.json');
let context,closing=false,heartbeat;
function core(){delete require.cache[require.resolve('./google-shopping.cjs')];return require('./google-shopping.cjs');}
function publish(){
  const data={pid:process.pid,heartbeat:Date.now(),visible:true,profile:path.join(stateDir,'profile')};
  fs.writeFileSync(metadata+'.tmp',JSON.stringify(data));fs.renameSync(metadata+'.tmp',metadata);
}
async function capture(page,id){
  const dir=path.join(controlDir,id);fs.mkdirSync(dir,{recursive:true});
  const e=core(),body=await page.locator('body').innerText();
  const cards=await page.evaluate(e.readCards);
  const data={url:page.url(),title:await page.title(),blocked:e.blocked(page.url(),body),
    quotes:e.toQuotes(cards),cards,body};
  fs.writeFileSync(path.join(dir,'page.json'),JSON.stringify(data,null,2));
  fs.writeFileSync(path.join(dir,'page.html'),await page.content());
  await page.screenshot({path:path.join(dir,'page.png')});
  console.log(JSON.stringify({id,title:data.title,blocked:data.blocked,quotes:data.quotes.length,dir}));
}
async function handle(page,file){
  const request=JSON.parse(fs.readFileSync(file,'utf8'));
  fs.unlinkSync(file);
  const id=String(request.id || '').replace(/[^a-zA-Z0-9_-]/g,'').slice(0,90);
  let result;
  try{
    if(!id || typeof request.query!=='string' || !request.query.trim() || request.query.length>1000)
      throw Error('Pedido de busca inválido');
    if(Date.now()-request.createdAt>180000)throw Error('Pedido expirado; não foi feita nova busca');
    const e=core();
    result=await e.searchOnPage(page,request.query,{...e.DEFAULTS,...request.options,stateDir});
    await capture(page,id);
  }catch(error){result={status:'error',query:request.query,quotes:[],error:error.message};}
  const out=path.join(stateDir,'responses',id+'.json');
  fs.writeFileSync(out+'.tmp',JSON.stringify(result));fs.renameSync(out+'.tmp',out);
  console.log(JSON.stringify({id,query:result.query,status:result.status,quotes:result.quotes?.length || 0}));
}
(async()=>{
  fs.mkdirSync(stateDir,{recursive:true});fs.mkdirSync(controlDir,{recursive:true});
  fs.mkdirSync(lock);fs.writeFileSync(path.join(lock,'owner.json'),JSON.stringify({pid:process.pid}));
  for(const name of ['requests','responses'])fs.mkdirSync(path.join(stateDir,name),{recursive:true});
  try{
    const e=core(),cfg={...e.DEFAULTS,stateDir};
    context=await chromium.launchPersistentContext(path.join(stateDir,'profile'),e.browserOptions(cfg,stateDir));
    context.on('close',()=>{closing=true;});
    const page=context.pages()[0] || await context.newPage();page.setDefaultTimeout(45000);
    if(urlFile){
      const url=new URL(fs.readFileSync(urlFile,'utf8').trim());
      if(url.hostname!=='www.google.com' || url.pathname!=='/search' || url.searchParams.get('udm')!=='28')
        throw Error('A restauração aceita apenas a página Shopping já confirmada');
      const state=e.readState(stateDir),now=Date.now();
      state.attempts=(state.attempts || []).filter(t=>now-t<86400000);state.attempts.push(now);state.lastAttempt=now;
      e.save(path.join(stateDir,'state.json'),state);
      await page.goto(url.href,{waitUntil:'domcontentloaded',timeout:45000});
      await page.getByText(/R\$\s*\d/).first().waitFor({timeout:15000}).catch(()=>{});
      await e.acknowledgeManualResume(page,cfg,stateDir);
      await capture(page,'sessao-restaurada');
    }
    await page.bringToFront();publish();heartbeat=setInterval(publish,2000);
    console.log('Sessão visível pronta. Buscas reutilizam a mesma janela e o mesmo perfil.');
    while(!closing){
      const manual=path.join(controlDir,'request.json');
      if(fs.existsSync(manual)){
        const request=JSON.parse(fs.readFileSync(manual,'utf8'));fs.unlinkSync(manual);
        if(request.action==='snapshot'){
          await core().acknowledgeManualResume(page,cfg,stateDir);
          await capture(page,String(request.id).replace(/[^a-zA-Z0-9_-]/g,'').slice(0,80));
        }
      }
      const inbox=path.join(stateDir,'requests');
      for(const name of fs.readdirSync(inbox).filter(n=>/^[a-zA-Z0-9_-]+\.json$/.test(n)).sort())
        await handle(page,path.join(inbox,name));
      await new Promise(r=>setTimeout(r,250));
    }
  }finally{
    clearInterval(heartbeat);
    if(context)await context.close().catch(()=>{});
    if(fs.existsSync(metadata))fs.unlinkSync(metadata);
    fs.unlinkSync(path.join(lock,'owner.json'));fs.rmdirSync(lock);
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
