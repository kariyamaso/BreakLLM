import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync,existsSync,mkdtempSync} from 'node:fs';
import {resolve,dirname,join} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..'),read=p=>readFileSync(join(root,p),'utf8');
function context(script='layouts.js'){const ctx=vm.createContext({});ctx.window=ctx;vm.runInContext(read('components.js'),ctx);if(script==='starter.js')vm.runInContext(read('evidence.js'),ctx);vm.runInContext(read(script),ctx);return ctx.S;}
test('18 named reusable layouts match the registry and have notes and provenance',()=>{
 const slides=context().slides,registry=JSON.parse(read('layouts.json')).layouts;assert.equal(slides.length,18);assert.equal(registry.length,18);
 slides.forEach((s,i)=>{assert.equal(s.layout,registry[i].id);assert.ok(s.notes);assert.ok(s.source);});
});
test('the research deck has 34 sourced pages and separates published and measured results',()=>{
 const slides=context('starter.js').slides;assert.equal(slides.length,34);
 for(const slide of slides){assert.ok(slide.source);assert.match(slide.notes,/\[Sources\]/);}
 const evidence=JSON.parse(read('evidence.json'));
 assert.equal(evidence.heretic_published.remeasured_in_this_project,false);
 assert.equal(evidence.server_inventory.files_catalogued,47);
 assert.equal(evidence.llm_assessment.known_cases,6);
 assert.equal(evidence.comparison.methods.baseline.lexical_pass_count,2);
 assert.ok(slides.some(s=>s.title.includes('LLM判定でも')));
});
test('all rendered visual assets are local and bundled',()=>{
 for(const source of ['layouts.js','starter.js'])for(const s of context(source).slides)for(const [,p]of s.body.matchAll(/src="([^"]+)"/g)){assert.ok(!p.includes('://'));assert.ok(existsSync(join(root,p)),p);}
 assert.ok(existsSync(join(root,'assets/fonts/NotoSansJP.ttf')));assert.match(read('assets/fonts/OFL.txt'),/SIL OPEN FONT LICENSE/);
 for(const p of ['index.html','starter.html']){assert.doesNotMatch(read(p),/src="https?:/);for(const [,asset]of read(p).matchAll(/(?:src|href)="([^"]+\.(?:js|css))"/g))assert.ok(existsSync(join(root,asset)),asset);}
});
test('original palette and source design are explicit',()=>{
 const css=read('theme.css');for(const hex of ['#132c96','#0054b5','#008579','#ed3131','#17aabd'])assert.ok(css.includes(hex));
 assert.match(read('SOURCES.md'),/speakerdeck.com\/yu4u/);assert.match(read('DESIGN.md'),/2:1/);
});
test('charts show missing measurements as missing and reject out-of-range data',()=>{
 const s=context();assert.match(s.bars(['A'],[{name:'B',values:[null]}]),/未測定/);assert.throws(()=>s.bars(['A'],[{values:[-1]}]));assert.throws(()=>s.bars(['A'],[{values:[Infinity]}]));assert.throws(()=>s.bars(['A'],[{values:[101]}]));
 assert.equal(s.esc('<x>'), '&lt;x&gt;');assert.throws(()=>s.link('javascript:alert(1)','bad'));assert.ok(s.link('https://example.com','source'));
});
test('printing shows every slide including when overview is active',()=>{
 const css=read('slides.css');assert.match(css,/@page\{size:15in 8\.4375in;margin:0\}/);assert.match(css,/\.slide,\.slide.active,\.overview \.slide\{display:block!important/);assert.match(css,/break-after:page/);
 assert.match(read('viewer.js'),/beforeprint/);assert.match(read('viewer.js'),/e.target.isContentEditable/);
});
test('new-deck copies a portable starter and refuses an existing destination',()=>{
 const parent=mkdtempSync(join(tmpdir(),'kariyama-template-test-')),destination=join(parent,'new-deck'),tool=join(root,'tools/new-deck.mjs');
 const first=spawnSync(process.execPath,[tool,destination],{encoding:'utf8'});assert.equal(first.status,0,first.stderr);assert.ok(existsSync(join(destination,'assets/fonts/NotoSansJP.ttf')));assert.match(readFileSync(join(destination,'index.html'),'utf8'),/starter.js/);
 for(const name of ['gallery.html','layouts.js','layouts.json','tools/verify.mjs','tests/template.test.mjs'])assert.ok(existsSync(join(destination,name)),name);
 assert.match(readFileSync(join(destination,'gallery.html'),'utf8'),/layouts.js/);assert.match(readFileSync(join(destination,'README.md'),'utf8'),/\(gallery.html\)/);
 const second=spawnSync(process.execPath,[tool,destination],{encoding:'utf8'});assert.notEqual(second.status,0);assert.match(second.stderr,/refusing to overwrite/);
});
