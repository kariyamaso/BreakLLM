import {cp,mkdir,access,copyFile,readFile,writeFile} from 'node:fs/promises';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..'),arg=process.argv[2];
if(!arg||process.argv.length!==3){console.error('Usage: node tools/new-deck.mjs /absolute/path/to/new-deck');process.exit(1);}
const destination=resolve(arg);
try{await access(destination);throw Error('Destination already exists; refusing to overwrite: '+destination);}catch(e){if(e.code!=='ENOENT')throw e;}
await mkdir(destination); // Parent must exist; never creates or replaces a broad directory.
for(const name of ['theme.css','slides.css','components.js','viewer.js','starter.js','layouts.js','layouts.json','DESIGN.md','SOURCES.md','AGENTS.md','assets','tools','tests'])await cp(join(root,name),join(destination,name),{recursive:true,errorOnExist:true,force:false});
// Preserve layout examples alongside a clean, editable entry point.
const gallery=await readFile(join(root,'index.html'),'utf8');
await writeFile(join(destination,'gallery.html'),gallery.replace('src="starter.js"','src="layouts.js"'),{flag:'wx'});
await copyFile(join(root,'starter.html'),join(destination,'index.html'));
await copyFile(join(root,'starter.html'),join(destination,'starter.html'));
const guide=await readFile(join(root,'README.md'),'utf8');
await writeFile(join(destination,'README.md'),guide
 .replace('[18種類のレイアウト](index.html)','[18種類のレイアウト](gallery.html)')
 .replace('最終検証結果は `VALIDATION.json`。プレビューは `previews/` にあります。','作成した資料は改めて検証してください。原本の検証記録やプレビューは複製していません。')
 .replace('HTML をブラウザで直接開きます。','この複製では `index.html` が作成中の資料、`gallery.html` がレイアウト見本です。`starter.js` を編集します。\n\nHTML をブラウザで直接開きます。'),{flag:'wx'});
console.log('Created '+join(destination,'index.html'));
console.log('Edit starter.js. Layout examples: '+join(destination,'gallery.html'));
