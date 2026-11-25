/* Sessão visível para o usuário operar; não envia buscas nem resolve verificações. */
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
(async()=>{
  const stateDir=path.resolve(process.argv[2] || path.join(__dirname,'../.shopping-browser'));
  fs.mkdirSync(stateDir,{recursive:true});
  const lock=path.join(stateDir,'collector.lock');
  try{fs.mkdirSync(lock);}catch(e){throw Error('Já há uma sessão usando o perfil do piloto. Feche-a antes de abrir outra.');}
  let context;
  try{
    context=await chromium.launchPersistentContext(path.join(stateDir,'profile'),{
      channel:'chrome',headless:false,chromiumSandbox:true,locale:'pt-BR',viewport:null,
      args:['--start-maximized']
    });
    const page=context.pages()[0] || await context.newPage();
    await page.goto('https://www.google.com/',{waitUntil:'domcontentloaded',timeout:45000});
    const input=page.locator('textarea[name="q"],input[name="q"]').first();
    if(await input.count()){
      await input.fill('JBL Tune 770NC');
      await input.focus();
    }
    await page.bringToFront();
    console.log('JANELA ABERTA. Aperte Enter para pesquisar manualmente. A sessão ficará aberta até você fechar o Chrome.');
    page.on('framenavigated',frame=>{
      if(frame===page.mainFrame()){
        try{const u=new URL(frame.url());console.log('Navegação manual:',u.origin+u.pathname);}catch{}
      }
    });
    await new Promise(resolve=>context.on('close',resolve));
  }finally{
    if(context)await context.close().catch(()=>{});
    fs.rmdirSync(lock);
  }
})().catch(e=>{console.error(e.message);process.exitCode=1;});
