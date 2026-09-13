'use strict';
(()=>{
  const $=id=>document.getElementById(id),deck=$('deck');
  if(!window.S?.slides?.length){deck.textContent='スライドがありません。S.add(...) を追加してください。';return;}
  deck.innerHTML=S.slides.map((s,i)=>'<section class="slide '+S.esc(s.kind)+'" id="slide-'+(i+1)+'" aria-label="'+S.esc((i+1)+'. '+s.title)+'"><div class="slide-inner"><header class="slide-head"><h1>'+S.esc(s.title)+'</h1><span class="state">'+S.esc(s.status)+'</span></header><div class="slide-body">'+s.body+'</div><footer class="slide-foot"><span class="brand">'+S.esc(S.brand)+'</span><span class="source">'+s.source+'</span><span class="number">'+(i+1)+'</span></footer></div></section>').join('');
  const slides=[...deck.children];let index=0;
  S.slides.forEach((s,i)=>$('jump').add(new Option((i+1)+'  '+s.title,String(i))));
  function resize(){
    const r=deck.getBoundingClientRect();document.documentElement.style.setProperty('--scale',String(Math.max(.05,Math.min((r.width-24)/1440,(r.height-24)/810))));
    if(document.body.classList.contains('overview'))slides.forEach(el=>{el.style.setProperty('--thumb-scale',String(el.clientWidth/1440));el.style.setProperty('--thumb-height',el.clientWidth*810/1440+'px');});
  }
  function show(n,hash=true){
    index=Math.max(0,Math.min(slides.length-1,Number.isFinite(n)?Math.floor(n):0));const overview=document.body.classList.contains('overview');
    slides.forEach((el,i)=>{el.classList.toggle('active',i===index);el.setAttribute('aria-hidden',String(!overview&&i!==index));el.inert=!overview&&i!==index;});
    $('jump').value=String(index);$('position').textContent=(index+1)+' / '+slides.length;
    $('speakerText').textContent=S.slides[index].notes||'このスライドにノートはありません。';
    $('previous').disabled=index===0;$('next').disabled=index===slides.length-1;
    $('progress').style.width=(index+1)/slides.length*100+'%';document.title=(index+1)+'. '+S.slides[index].title+' — '+S.title;
    if(hash){try{history.replaceState(null,'','#slide-'+(index+1));}catch{location.hash='slide-'+(index+1);}}
    resize();
  }
  function overview(force){
    const on=force??!document.body.classList.contains('overview');document.body.classList.toggle('overview',on);$('overview').setAttribute('aria-pressed',String(on));
    slides.forEach(el=>{if(on){el.tabIndex=0;el.setAttribute('role','button');}else{el.removeAttribute('tabindex');el.removeAttribute('role');}});
    show(index,false);if(on)slides[index].scrollIntoView({block:'nearest'});
  }
  function notes(){const on=!document.body.classList.contains('show-notes');document.body.classList.toggle('show-notes',on);$('speaker').hidden=!on;$('notes').setAttribute('aria-pressed',String(on));resize();}
  async function fullscreen(){try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{$('fullscreen').textContent='非対応';}}
  $('previous').onclick=()=>show(index-1);$('next').onclick=()=>show(index+1);$('jump').onchange=e=>{overview(false);show(Number(e.target.value));};
  $('overview').onclick=()=>overview();$('notes').onclick=notes;$('fullscreen').onclick=fullscreen;$('print').onclick=()=>window.print();
  slides.forEach((el,i)=>{const select=()=>{if(document.body.classList.contains('overview')){overview(false);show(i);$('jump').focus();}};el.onclick=select;el.onkeydown=e=>{if(document.body.classList.contains('overview')&&['Enter',' '].includes(e.key)){e.preventDefault();e.stopPropagation();select();}};});
  document.addEventListener('keydown',e=>{
    if(e.ctrlKey||e.metaKey||e.altKey||e.target.isContentEditable||/INPUT|SELECT|TEXTAREA|BUTTON|VIDEO/.test(e.target.tagName))return;
    const k=e.key.toLowerCase();if(!['arrowleft','arrowright','pageup','pagedown',' ','home','end','g','n','f','p','escape'].includes(k))return;e.preventDefault();
    if(['arrowright','pagedown',' '].includes(k))show(index+1);if(['arrowleft','pageup'].includes(k))show(index-1);
    if(k==='home')show(0);if(k==='end')show(slides.length-1);if(k==='g')overview();if(k==='n')notes();if(k==='f')fullscreen();if(k==='p')window.print();if(k==='escape')overview(false);
  });
  function fromHash(){const m=location.hash.match(/^#slide-(\d+)$/);show(m?Number(m[1])-1:0,false);}
  const beforePrint=()=>slides.forEach(el=>{el.inert=false;el.removeAttribute('aria-hidden');});
  addEventListener('beforeprint',beforePrint);addEventListener('afterprint',()=>show(index,false));
  addEventListener('hashchange',fromHash);addEventListener('resize',resize);const observer=new ResizeObserver(resize);observer.observe(deck);document.fonts.ready.then(resize);
  window.kariyamaDeck={show,overview,resize,slides:S.slides};fromHash();
})();
