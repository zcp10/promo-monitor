'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || require.resolve('playwright',{paths:[path.join(__dirname,'../browser')]}));
const {readCards,toQuotes,money,blocked,gate,DEFAULTS,collect,shoppingURL,browserOptions}=require('../browser/google-shopping.cjs');
const card=(title,price,store='Amazon',extra='')=>`<article style="border:1px solid;padding:20px"><h3>${title}</h3><span>${price}</span><div>${store}</div>${extra}</article>`;
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true,chromiumSandbox:true});
  const results=[];
  try {
    const page=await browser.newPage();
    const cases=[
      ['mesmo produto em quatro lojas', ['Amazon','Magalu','KaBuM!','Mercado Livre'].map((s,i)=>card('JBL Tune 770NC Preto',`R$ ${400+i*10},90`,s)).join(''),4,[400.9,410.9,420.9,430.9]],
      ['preço antigo riscado',card('JBL Tune 770NC Azul','R$ 432,99','Amazon','<del><span>R$ 609,85</span></del>'),1,[432.99]],
      ['parcelamento explícito descartado',card('JBL Tune 770NC Azul','10x de <span>R$ 43,29</span>'),0,[]],
      ['frete não vira preço',card('JBL Tune 770NC Preto','R$ 432,99','Amazon','<span>Frete R$ 19,90</span>'),1,[432.99]],
      ['preço com milhar',card('Samsung Galaxy A55 256GB','R$ 1.899,90','Magalu'),1,[1899.9]],
      ['duplicatas removidas',card('JBL Tune 770NC Preto','R$ 432,99')+card('JBL Tune 770NC Preto','R$ 432,99'),1,[432.99]],
      ['loja ausente é descartada',card('JBL Tune 770NC Preto','R$ 432,99',''),0,[]],
      ['dois preços atuais ambíguos',card('JBL Tune 770NC Preto','R$ 432,99','Amazon','<span>R$ 474,90</span>'),0,[]],
      ['acessório é extraído para o matcher rejeitar',card('Capa compatível com JBL Tune 770NC','R$ 39,90'),1,[39.9]],
      ['Tune e Live preservados separadamente',card('JBL Tune 770NC Preto','R$ 432,99')+card('JBL Live 770NC Preto','R$ 673,49','Magalu'),2,[432.99,673.49]],
      ['grade real: mensalidade e loja fora da lista', '<div><div role="img" title="Fone de Ouvido Bluetooth JBL Tune 770NC"></div><div>Fone de Ouvido Bluetooth JBL Tune 770NC</div><div><span aria-label="R$ 332,40 agora.">R$ 332,40 agora</span><span aria-label="11 parcelas de R$ 49,18.">R$ 49,18/mês x 11</span></div><span class="WJMUdc">ibyte</span></div>',1,[332.4]],
    ];
    for(const [name,html,count,prices] of cases) {
      await page.setContent(html);
      const quotes=toQuotes(await page.evaluate(readCards));
      assert.equal(quotes.length,count,name); assert.deepEqual(quotes.map(q=>q.price),prices,name);
      results.push({name,status:'passed',quotes:quotes.length});
    }
    assert.equal(money('R$ 1.234,56'),1234.56);
    assert(blocked('https://www.google.com/sorry/index','',200));
    assert(blocked('https://www.google.com/','Nossos sistemas detectaram tráfego incomum',200));
    assert(blocked('https://www.google.com/','',429));
    const now=Date.now(), cfg=DEFAULTS;
    assert.equal(gate({blockedUntil:now+10000},now,cfg).status,'blocked_cooldown');
    assert.equal(gate({attempts:Array(100).fill(now-7200000)},now,cfg),null);
    assert.equal(gate({attempts:[...Array(100).fill(now-7200000),...Array(5).fill(now-10000)]},now,cfg).status,'hourly_limit');
    assert.equal(gate({attempts:Array(5).fill(now-10000)},now,cfg).status,'hourly_limit');
    assert.equal(gate({lastAttempt:now-1000},now,cfg).status,'interval');
    assert.equal(gate({},now,cfg),null);
    // Exercita o bloqueio persistente SEM uma requisição ao Google.
    const testDir=path.resolve(process.env.SHOPPING_TEST_STATE || 'work/test-state');
    fs.mkdirSync(testDir,{recursive:true});
    fs.writeFileSync(path.join(testDir,'state.json'),JSON.stringify({blockedUntil:now+86400000}));
    assert.equal((await collect('JBL Tune 770NC',{stateDir:testDir})).status,'blocked_cooldown');
    fs.writeFileSync(path.join(testDir,'state.json'),JSON.stringify({cache:{'jbl tune 770nc':{at:now,result:{quotes:[{title:'saved'}]}}}}));
    assert.equal((await collect('JBL Tune 770NC',{stateDir:testDir})).status,'cached');
    fs.writeFileSync(path.join(testDir,'state.json'),JSON.stringify({}));
    fs.mkdirSync(path.join(testDir,'collector.lock'));
    assert.equal((await collect('JBL Tune 770NC',{stateDir:testDir})).status,'busy');
    fs.rmdirSync(path.join(testDir,'collector.lock'));
    const url=new URL(shoppingURL('JBL Tune 520BT'));
    assert.equal(url.searchParams.get('q'),'JBL Tune 520BT');
    assert.equal(url.searchParams.get('udm'),'28');
    const options=browserOptions({},testDir);
    assert.equal(options.headless,false);
    assert.equal(options.chromiumSandbox,true);
    assert.equal(options.viewport,null);
    const report={kind:'controlled_dom_simulations_not_live_google',simulations:results,guardChecks:17};
    if(process.env.SHOPPING_TEST_REPORT) fs.writeFileSync(process.env.SHOPPING_TEST_REPORT,JSON.stringify(report,null,2));
    console.log(JSON.stringify(report,null,2));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
