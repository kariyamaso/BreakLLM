'use strict';
/** Trusted local authoring API. body/captions/table cells are authored HTML, not an untrusted feed. */
window.S={slides:[],brand:'KARIYAMA',title:'Research Slides',
  add(title,body,options={}){this.slides.push({title,body,kind:'',status:'',source:'',notes:'',...options});}
};
S.esc=v=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
S.link=(url,label)=>{if(!/^(https?:|\.\.?\/|[^:/?#]+(?:\/|$))/.test(url))throw Error('Use an https or relative source URL');return '<a href="'+S.esc(url)+'" target="_blank" rel="noopener">'+S.esc(label)+'</a>';};
S.split=(left,right,kind='')=>'<div class="split '+kind+'"><div class="stack">'+left+'</div><div class="stack">'+right+'</div></div>';
S.take=(html,warning=false)=>'<div class="takeaway'+(warning?' warning':'')+'">'+html+'</div>';
S.fig=(path,caption,{alt='',kind=''}={})=>'<figure class="figure '+kind+'"><img src="'+S.esc(path)+'" alt="'+S.esc(alt||caption.replace(/<[^>]+>/g,''))+'"><figcaption>'+caption+'</figcaption></figure>';
S.table=(headers,rows,{numeric=[],highlight=-1,dense=false}={})=>'<table class="'+(dense?'dense-table':'')+'"><thead><tr>'+headers.map((h,i)=>'<th scope="col" class="'+(numeric.includes(i)?'numeric':'')+'">'+h+'</th>').join('')+'</tr></thead><tbody>'+rows.map((row,j)=>'<tr'+(j===highlight?' class="highlight"':'')+'>'+row.map((cell,i)=>'<'+(i===0?'th scope="row"':'td')+' class="'+(numeric.includes(i)?'numeric':'')+'">'+cell+'</'+(i===0?'th':'td')+'>').join('')+'</tr>').join('')+'</tbody></table>';
S.flow=(nodes,{compact=false}={})=>'<div class="flow'+(compact?' compact':'')+'">'+nodes.map((n,i)=>(i?'<span class="arrow" aria-hidden="true">→</span>':'')+'<div class="node'+(n[2]==='mint'?' mint-node':'')+'"><b>'+S.esc(n[0])+'</b><small>'+S.esc(n[1]).replace(/\n/g,'<br>')+'</small></div>').join('')+'</div>';
S.tokens=(masked=[],count=12)=>'<div class="token-strip" style="grid-template-columns:repeat('+count+',1fr)">'+Array.from({length:count},(_,i)=>'<span class="'+(masked.includes(i)?'masked':'')+'">'+(masked.includes(i)?'×':i+1)+'</span>').join('')+'</div>';
S.annotation=(html,tail=true)=>'<div class="annotation'+(tail?' tail':'')+'">'+html+'</div>';
S.metrics=rows=>'<div class="metrics">'+rows.map(([label,value,note])=>'<div class="metric"><p>'+S.esc(label)+'</p><b>'+S.esc(value)+'</b><p class="caption">'+S.esc(note)+'</p></div>').join('')+'</div>';
S.bars=(labels,series,{max=100,unit='%',title='指標',decimals=1}={})=>{
  if(!Number.isFinite(max)||max<=0||!labels.length||!series.length)throw Error('Chart needs labels, series and a positive maximum');
  const width=1280,height=360,left=280,right=140,top=22,bottom=44,plot=width-left-right,group=(height-top-bottom)/labels.length;
  let svg='<svg class="chart" viewBox="0 0 '+width+' '+height+'" role="img" aria-label="'+S.esc(title)+'"><title>'+S.esc(title)+'</title>';
  for(let n=0;n<=4;n++){const x=left+plot*n/4;svg+='<path class="gridline" d="M'+x+' '+top+'V'+(height-bottom)+'"/><text x="'+x+'" y="'+(height-6)+'" text-anchor="middle" font-size="20">'+S.esc(+(max*n/4).toFixed(2)+unit)+'</text>';}
  labels.forEach((label,i)=>{
    const cy=top+i*group;svg+='<text x="'+(left-24)+'" y="'+(cy+group/2+7)+'" text-anchor="end" font-size="24">'+S.esc(label)+'</text>';
    series.forEach((s,j)=>{
      const value=s.values[i],bh=Math.min(29,(group-16)/series.length),y=cy+(group-series.length*(bh+4)+4)/2+j*(bh+4);
      if(value!==null&&value!==undefined&&(!Number.isFinite(value)||value<0||value>max))throw Error('Chart value must be finite and in [0,max]');
      if(Number.isFinite(value))svg+='<rect x="'+left+'" y="'+y+'" width="'+(plot*value/max)+'" height="'+bh+'" fill="'+S.esc(s.color||'var(--brand-blue)')+'"/>';
      svg+='<text class="chart-value" x="'+(width-right+20)+'" y="'+(y+bh*.8)+'" font-size="22">'+(Number.isFinite(value)?S.esc(value.toFixed(decimals)+unit):'未測定')+'</text>';
    });
  });
  return '<div class="chart-name">'+S.esc(title)+'</div><div class="legend">'+series.map(s=>'<span><i style="--series-color:'+S.esc(s.color||'var(--brand-blue)')+'"></i>'+S.esc(s.name)+'</span>').join('')+'</div>'+svg+'</svg>';
};
