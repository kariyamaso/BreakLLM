import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {resolve,dirname,join} from 'node:path';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url),root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
let output,base=pathToFileURL(join(root,existsSync(join(root,'gallery.html'))?'gallery.html':'index.html')).href;
for(let i=2;i<process.argv.length;i++){const arg=process.argv[i];if(arg==='--output')output=resolve(process.argv[++i]);else if(arg==='--base')base=process.argv[++i];else throw Error('Unknown argument '+arg);}
if(!output)throw Error('Pass --output /path/to/qa-output (generated screenshots and PDF)');
let chromium;try{({chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright'));}catch{throw Error('Install Playwright separately or set PLAYWRIGHT_MODULE to its package directory');}
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.env.CHROME_BIN?{executablePath:process.env.CHROME_BIN}:{})});
const result={base,slides:[],starter:[],errors:[],failedRequests:[],widths:[]};
try{
 const page=await browser.newPage({viewport:{width:1464,height:934},reducedMotion:'reduce'});
 page.on('pageerror',e=>result.errors.push(e.message));page.on('requestfailed',r=>result.failedRequests.push(r.url()));
 await page.goto(base);await page.waitForFunction(()=>window.kariyamaDeck?.slides.length>0);await page.evaluate(()=>document.fonts.ready);
 async function captureDeck(prefix,states){
  const total=await page.evaluate(()=>window.kariyamaDeck.slides.length);
  for(let i=0;i<total;i++){
  await page.evaluate(i=>window.kariyamaDeck.show(i),i);await page.waitForTimeout(60);
  const state=await page.evaluate(()=>{
   const slide=document.querySelector('.slide.active'),body=slide.querySelector('.slide-body'),bounds=body.getBoundingClientRect(),issues=[];
   const walker=document.createTreeWalker(body,NodeFilter.SHOW_TEXT);let text;
   while(text=walker.nextNode()){
    if(!text.textContent.trim()||text.parentElement.closest('svg,script'))continue;
    const range=document.createRange();range.selectNodeContents(text);
    for(const r of range.getClientRects())if(r.width&&r.height&&(r.left<bounds.left-1||r.right>bounds.right+1||r.top<bounds.top-1||r.bottom>bounds.bottom+1))issues.push('Text outside body: '+text.textContent.trim().slice(0,75));
   }
   for(const el of body.querySelectorAll('.stack,.node,pre,td,th,.takeaway,.annotation'))if(el.scrollWidth>el.clientWidth+2||el.scrollHeight>el.clientHeight+2)issues.push('Overflow: '+el.tagName+' '+el.textContent.trim().slice(0,60));
   const h=slide.querySelector('h1');if(getComputedStyle(slide.querySelector('.slide-head')).display!=='none'&&h.scrollWidth>h.clientWidth+1)issues.push('Title overflow');
   const images=[...slide.querySelectorAll('img')].map(el=>({src:el.getAttribute('src'),loaded:el.complete&&el.naturalWidth>0}));
   if(images.some(img=>!img.loaded))issues.push('Unloaded image');
   return {number:Number(slide.id.split('-')[1]),title:h.textContent,issues,images};
  });
   await page.locator('.slide.active').screenshot({path:join(output,prefix+String(i+1).padStart(2,'0')+'.png')});states.push(state);
  }
 }
 const count=await page.evaluate(()=>window.kariyamaDeck.slides.length);
 await captureDeck('slide-',result.slides);
 await page.evaluate(()=>window.kariyamaDeck.show(0));await page.keyboard.press('ArrowRight');assert.equal(await page.locator('#position').textContent(),'2 / '+count);
 await page.keyboard.press('End');assert.equal(await page.locator('#position').textContent(),count+' / '+count);
 await page.keyboard.press('Home');await page.keyboard.press('g');assert.equal(await page.locator('#overview').getAttribute('aria-pressed'),'true');
 await page.locator('#slide-4').focus();await page.keyboard.press('Enter');assert.equal(await page.locator('#position').textContent(),'4 / '+count);
 await page.locator('#notes').click();assert.equal(await page.locator('#speaker').isVisible(),true);await page.locator('#notes').click();
 await page.evaluate(()=>window.kariyamaDeck.overview(true));await page.pdf({path:join(output,'print-check.pdf'),printBackground:true,preferCSSPageSize:true});
 await page.evaluate(()=>window.kariyamaDeck.overview(false));
 for(const [width,height]of[[3440,1440],[1440,900],[1024,768],[768,1024],[390,844],[320,640]]){
  await page.setViewportSize({width,height});await page.waitForTimeout(50);
  const size=await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth}));result.widths.push(size);assert.equal(size.width,size.scrollWidth);
 }
 await page.goto(new URL('starter.html',base).href);await page.waitForFunction(()=>window.kariyamaDeck?.slides.length>0);result.starterSlides=await page.evaluate(()=>window.kariyamaDeck.slides.length);assert.ok(result.starterSlides>0);
 await page.reload();await page.waitForFunction(n=>window.kariyamaDeck?.slides.length===n,result.starterSlides);
 await page.setViewportSize({width:1464,height:934});await page.evaluate(()=>document.fonts.ready);
 await captureDeck('starter-',result.starter);
 await page.pdf({path:join(output,'starter-print-check.pdf'),printBackground:true,preferCSSPageSize:true});
 assert.deepEqual(result.errors,[]);assert.deepEqual(result.failedRequests,[]);assert.ok([...result.slides,...result.starter].every(s=>s.issues.length===0),'Inspect layout issues in validation.json');
 result.passed=true;
}catch(e){result.failure=e.stack;throw e;}finally{await writeFile(join(output,'validation.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));await browser.close();}
