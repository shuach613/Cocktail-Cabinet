(()=>{
'use strict';
const root=document.documentElement;
function setTheme(theme){root.dataset.theme=theme;localStorage.setItem('cabinet-theme',theme);document.querySelectorAll('[data-theme-icon]').forEach(el=>el.textContent=theme==='dark'?'☀':'☾');}
const saved=localStorage.getItem('cabinet-theme');setTheme(saved||((window.matchMedia&&matchMedia('(prefers-color-scheme: dark)').matches)?'dark':'light'));
document.querySelectorAll('[data-theme-toggle]').forEach(b=>b.addEventListener('click',()=>setTheme(root.dataset.theme==='dark'?'light':'dark')));
document.querySelectorAll('[data-confirm]').forEach(f=>f.addEventListener('submit',e=>{if(!confirm(f.dataset.confirm))e.preventDefault();}));
document.querySelectorAll('[data-copy-input]').forEach(b=>b.addEventListener('click',async()=>{const e=document.getElementById(b.dataset.copyInput);if(!e)return;try{await navigator.clipboard.writeText(e.value);b.textContent='Copied ✓';}catch(_){e.select();document.execCommand('copy');b.textContent='Copied ✓';}}));
function filterList(inputId,itemSelector){const inp=document.getElementById(inputId);if(!inp)return;inp.addEventListener('input',()=>{const q=inp.value.trim().toLocaleLowerCase();document.querySelectorAll(itemSelector).forEach(el=>{const hay=(el.dataset.search||el.textContent).toLocaleLowerCase();el.hidden=!hay.includes(q);if(q&&hay.includes(q)){const p=el.closest('details');if(p)p.open=true;}});});}
filterList('pantry-search','[data-search].shelf-option');filterList('catalog-search','[data-search].catalog-item');
const editor=document.getElementById('editor-data');
if(editor){
 const categories=JSON.parse(editor.dataset.categories), ingredients=JSON.parse(editor.dataset.ingredients), initialItems=JSON.parse(editor.dataset.items), initialSteps=JSON.parse(editor.dataset.steps);
 const byName=new Map(ingredients.map(i=>[i.name.toLocaleLowerCase(),i]));
 const itemRoot=document.getElementById('ingredient-rows'),stepRoot=document.getElementById('step-rows');
 const itemTemplate=document.getElementById('ingredient-template'),stepTemplate=document.getElementById('step-template');
 function itemRow(data={}){
  const node=itemTemplate.content.firstElementChild.cloneNode(true),name=node.querySelector('.ingredient-entry-name'),qty=node.querySelector('.ingredient-entry-quantity'),tags=node.querySelector('.ingredient-entry-categories'),kind=node.querySelector('.ingredient-entry-kind'),brand=node.querySelector('.ingredient-entry-brand');
  categories.forEach(c=>{const opt=document.createElement('option');opt.value=c.id;opt.textContent=c.name;tags.append(opt);});
  name.value=data.name||'';qty.value=data.quantity||'';kind.value=data.kind||'ingredient';node.querySelector('.ingredient-entry-optional').checked=!!data.optional;brand.checked=data.is_brand!==false;
  function updateCategories(){const existing=byName.get(name.value.trim().toLocaleLowerCase());const chosen=existing?existing.category_ids:(data.categories||[]);Array.from(tags.options).forEach(o=>o.selected=chosen.includes(Number(o.value)));node.querySelector('.existing-ingredient-note').hidden=!existing;tags.disabled=!!existing;brand.disabled=!!existing;if(existing)brand.checked=!!existing.is_brand;}
  name.addEventListener('change',()=>{data={};updateCategories();});updateCategories();
  node.querySelector('.remove-row').addEventListener('click',()=>node.remove());itemRoot.append(node);
 }
 function stepRow(value=''){const node=stepTemplate.content.firstElementChild.cloneNode(true);node.querySelector('textarea').value=value;node.querySelector('.remove-row').addEventListener('click',()=>{node.remove();renumberSteps();});stepRoot.append(node);renumberSteps();}
 function renumberSteps(){stepRoot.querySelectorAll('.step-order').forEach((e,i)=>e.textContent=String(i+1).padStart(2,'0'));}
 document.getElementById('add-ingredient').addEventListener('click',()=>itemRow());document.getElementById('add-step').addEventListener('click',()=>stepRow());
 (initialItems.length?initialItems:[{},{}]).forEach(itemRow);(initialSteps.length?initialSteps:['']).forEach(stepRow);
 document.getElementById('recipe-form').addEventListener('submit',async e=>{
  e.preventDefault();const form=e.currentTarget,button=document.getElementById('save-recipe'),error=document.getElementById('editor-error');
  error.hidden=true;
  const items=Array.from(itemRoot.querySelectorAll('.ingredient-editor-row')).map(row=>({
   name:row.querySelector('.ingredient-entry-name').value.trim(),quantity:row.querySelector('.ingredient-entry-quantity').value.trim(),kind:row.querySelector('.ingredient-entry-kind').value,optional:row.querySelector('.ingredient-entry-optional').checked,is_brand:row.querySelector('.ingredient-entry-brand').checked,
   categories:Array.from(row.querySelector('.ingredient-entry-categories').selectedOptions).map(o=>Number(o.value))}));
  const steps=Array.from(stepRoot.querySelectorAll('textarea')).map(e=>e.value.trim()).filter(Boolean);
  const payload={title:document.getElementById('recipe-title').value,technique:document.getElementById('recipe-technique').value,glass:document.getElementById('recipe-glass').value,notes:document.getElementById('recipe-notes').value,steps,items};
  button.disabled=true;button.textContent='Saving…';
  try{
   const id=form.dataset.recipeId;const res=await fetch(id?`/recipes/${id}/edit`:'/recipes/new',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':document.querySelector('meta[name=csrf-token]').content},body:JSON.stringify(payload),credentials:'same-origin'});
   const result=await res.json();if(!res.ok)throw Error(result.error||'Could not save recipe.');window.location.assign(`/recipes/${result.id}`);
  }catch(ex){error.textContent=ex.message;error.hidden=false;error.scrollIntoView({behavior:'smooth',block:'center'});button.disabled=false;button.textContent='Save recipe ↗';}
 });
}
// The service worker caches ONLY static assets. Authentication and recipes always require a live session.
if('serviceWorker' in navigator && (location.protocol==='https:' || location.hostname==='localhost')){navigator.serviceWorker.register('/sw.js').catch(()=>{});}
})();
