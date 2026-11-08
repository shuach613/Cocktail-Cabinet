(()=>{
'use strict';
const root=document.documentElement;
document.querySelectorAll('[data-theme-toggle]').forEach(button=>button.addEventListener('click',()=>{
 const next=root.dataset.theme==='dark'?'light':'dark';
 window.CabinetTheme?.select(next);
}));
window.CabinetTheme?.refresh();
document.querySelectorAll('[data-confirm]').forEach(f=>f.addEventListener('submit',e=>{if(!confirm(f.dataset.confirm))e.preventDefault();}));
document.querySelectorAll('[data-copy-input]').forEach(b=>b.addEventListener('click',async()=>{const e=document.getElementById(b.dataset.copyInput);if(!e)return;try{await navigator.clipboard.writeText(e.value);b.textContent='Copied ✓';}catch(_){e.select();document.execCommand('copy');b.textContent='Copied ✓';}}));

// Shared settings UI: separated panels avoid one overwhelming ingredients page.
const tabButtons=[...document.querySelectorAll('[data-settings-tab]')];
function switchTab(tab, focus=false){
 tabButtons.forEach(button=>{
  const isActive=button.dataset.settingsTab===tab;
  button.setAttribute('aria-selected',String(isActive));
  button.tabIndex=isActive?0:-1;
  if(isActive&&focus)button.focus();
 });
 document.querySelectorAll('[data-settings-panel]').forEach(panel=>{
  panel.hidden=panel.dataset.settingsPanel!==tab;
 });
}
if(tabButtons.length){
 const params=new URLSearchParams(location.search);
 switchTab(params.get('tab')==='products'?'products':'categories');
 tabButtons.forEach((button,index)=>{
  button.addEventListener('click',()=>switchTab(button.dataset.settingsTab));
  button.addEventListener('keydown',event=>{
   let next=-1;
   if(event.key==='ArrowRight')next=(index+1)%tabButtons.length;
   if(event.key==='ArrowLeft')next=(index+tabButtons.length-1)%tabButtons.length;
   if(event.key==='Home')next=0;
   if(event.key==='End')next=tabButtons.length-1;
   if(next<0)return;
   event.preventDefault();
   switchTab(tabButtons[next].dataset.settingsTab,true);
  });
 });
}
const catalogSearch=document.getElementById('catalog-search');
if(catalogSearch){catalogSearch.addEventListener('input',()=>{const q=catalogSearch.value.trim().toLocaleLowerCase();document.querySelectorAll('.catalog-item').forEach(el=>{el.hidden=!((el.dataset.search||'').includes(q));});});}

// The shelf is a proper hierarchy. A specific brand is shown under its assigned
// categories; if a bottle belongs to two categories both controls stay in sync.
const shelfForm=document.getElementById('pantry-form');
if(shelfForm){
 const checkboxes=[...shelfForm.querySelectorAll('input[type=checkbox]')];
 const count=document.getElementById('shelf-selected-count');
 const search=document.getElementById('pantry-search');
 function updateCount(){const ids=new Set(checkboxes.filter(c=>c.checked).map(c=>c.name+':'+c.value));if(count)count.textContent=ids.size+' selected';}
 function doSearch(){
  const q=search.value.trim().toLocaleLowerCase();
  shelfForm.querySelectorAll('.shelf-option').forEach(item=>{item.hidden=!!q&&!item.dataset.search.includes(q);});
  const branches=[...shelfForm.querySelectorAll('.shelf-branch')].reverse();
  branches.forEach(branch=>{
   const ownMatch=branch.dataset.branchName.includes(q);
   const visible=[...branch.querySelectorAll(':scope > .shelf-branch-content > .shelf-option')].some(e=>!e.hidden);
   const visibleBranch=[...branch.querySelectorAll(':scope > .shelf-branch-content > .shelf-branch')].some(e=>!e.hidden);
   branch.hidden=!!q&&!(ownMatch||visible||visibleBranch);
   if(q&&!branch.hidden)branch.open=true;
  });
 }
 shelfForm.addEventListener('change',event=>{
  const target=event.target;
  if(target.matches('input[name="ingredient"]')){
   shelfForm.querySelectorAll('input[name="ingredient"]').forEach(other=>{if(other.value===target.value)other.checked=target.checked;});
  }
  updateCount();
 });
 search.addEventListener('input',doSearch);
 updateCount();
}

// Touch-friendly recipe editor. New classifications are transient until the
// recipe is submitted; server inserts everything in one SQLite transaction.
const editor=document.getElementById('editor-data');
if(editor){
 const categories=JSON.parse(editor.dataset.categories),ingredients=JSON.parse(editor.dataset.ingredients);
 const initialItems=JSON.parse(editor.dataset.items),initialSteps=JSON.parse(editor.dataset.steps);
 const byName=new Map(ingredients.map(i=>[i.name.toLocaleLowerCase(),i]));
 const itemRoot=document.getElementById('ingredient-rows'),stepRoot=document.getElementById('step-rows');
 const itemTemplate=document.getElementById('ingredient-template'),stepTemplate=document.getElementById('step-template');
 const modal=document.getElementById('new-category-dialog'),modalForm=document.getElementById('new-category-form');
 const newName=document.getElementById('new-category-name'),newParent=document.getElementById('new-category-parent'),newError=document.getElementById('new-category-error');
 let activeRow=null,categoryCounter=0;
 const pending=[];
 function familyName(category){return categories.find(c=>String(c.id)===String(category.parent_id))?.name||'';}
 function displayCategory(category){const parent=familyName(category);return parent?category.name+' · '+parent:category.name;}
 function categorySelections(row){return [...row.querySelectorAll('.category-pick-list input:checked')].map(c=>c.value.startsWith('new:')?c.value:Number(c.value));}
 function renderCategories(row,selected){
  const list=row.querySelector('.category-pick-list');const name=row.querySelector('.ingredient-entry-name');
  const existing=byName.get(name.value.trim().toLocaleLowerCase());
  const chosen=existing?existing.category_ids.map(String):(selected||categorySelections(row)).map(String);
  list.replaceChildren();
  categories.forEach(cat=>{
   if(cat.name.toLocaleLowerCase()==='ice')return;
   const label=document.createElement('label');label.className='category-pick-option';label.dataset.search=(cat.name+' '+familyName(cat)).toLocaleLowerCase();
   const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.value=String(cat.id);checkbox.checked=chosen.includes(String(cat.id));checkbox.disabled=!!existing;
   const text=document.createElement('span');text.textContent=displayCategory(cat);
   label.append(checkbox,text);list.append(label);
  });
  row.querySelector('.existing-ingredient-note').hidden=!existing;
  row.querySelector('.ingredient-entry-brand').disabled=!!existing;
  if(existing)row.querySelector('.ingredient-entry-brand').checked=!!existing.is_brand;
  row.querySelector('.add-inline-category').hidden=!!existing;
  filterCategoryList(row);
 }
 function filterCategoryList(row){
  const q=row.querySelector('.category-search').value.trim().toLocaleLowerCase();
  row.querySelectorAll('.category-pick-option').forEach(e=>{e.hidden=!!q&&!e.dataset.search.includes(q);});
 }
 function itemRow(data={}){
  const row=itemTemplate.content.firstElementChild.cloneNode(true);
  const name=row.querySelector('.ingredient-entry-name');
  name.value=data.name||'';row.querySelector('.ingredient-entry-quantity').value=data.quantity||'';
  row.querySelector('.ingredient-entry-kind').value=data.kind||'ingredient';
  row.querySelector('.ingredient-entry-optional').checked=!!data.optional;
  row.querySelector('.ingredient-entry-brand').checked=data.is_brand!==false;
  renderCategories(row,data.categories||[]);
  name.addEventListener('input',()=>renderCategories(row,categorySelections(row)));
  row.querySelector('.category-search').addEventListener('input',()=>filterCategoryList(row));
  row.querySelector('.add-inline-category').addEventListener('click',()=>openCategoryDialog(row));
  row.querySelector('.remove-row').addEventListener('click',()=>row.remove());
  itemRoot.append(row);
 }
 function stepRow(value=''){
  const row=stepTemplate.content.firstElementChild.cloneNode(true);
  row.querySelector('textarea').value=value;
  row.querySelector('.remove-row').addEventListener('click',()=>{row.remove();renumberSteps();});
  stepRoot.append(row);renumberSteps();
 }
 function renumberSteps(){stepRoot.querySelectorAll('.step-order').forEach((e,i)=>e.textContent=String(i+1).padStart(2,'0'));}
 function openCategoryDialog(row){
  activeRow=row;newName.value='';newError.hidden=true;
  newParent.replaceChildren();
  const blank=document.createElement('option');blank.value='';blank.textContent='Choose an existing category';newParent.append(blank);
  categories.forEach(c=>{
   if(c.name.toLocaleLowerCase()==='ice')return;
   const opt=document.createElement('option');opt.value=String(c.id);opt.textContent=displayCategory(c);newParent.append(opt);
  });
  if(typeof modal.showModal==='function'){modal.showModal();}else{modal.setAttribute('open','');}
  newName.focus();
 }
 function closeCategoryDialog(){modal.close?.();modal.removeAttribute('open');activeRow=null;}
 document.getElementById('cancel-new-category').addEventListener('click',closeCategoryDialog);
 document.getElementById('cancel-new-category-bottom').addEventListener('click',closeCategoryDialog);
 modalForm.addEventListener('submit',event=>{
  event.preventDefault();
  const name=newName.value.trim(),parent=newParent.value;
  if(!name||name.length>65||!parent){newError.textContent='Enter a name and choose an existing parent category.';newError.hidden=false;return;}
  if(categories.some(cat=>cat.name.toLocaleLowerCase()===name.toLocaleLowerCase())){newError.textContent='That category already exists. Select it from the list instead.';newError.hidden=false;return;}
  const category={id:'new:'+(++categoryCounter),name,parent_id:parent};
  const row=activeRow;pending.push({temp_id:category.id,name,parent_id:parent});categories.push(category);
  itemRoot.querySelectorAll('.ingredient-editor-row').forEach(other=>renderCategories(other,categorySelections(other)));
  if(row){const input=[...row.querySelectorAll('.category-pick-list input')].find(c=>c.value===category.id);if(input)input.checked=true;}
  closeCategoryDialog();
 });
 function usedCategories(items){
  const used=new Set(items.flatMap(item=>item.categories.filter(v=>typeof v==='string'&&v.startsWith('new:'))));
  let changed=true;
  while(changed){changed=false;for(const c of pending){if(used.has(c.temp_id)&&String(c.parent_id).startsWith('new:')&&!used.has(c.parent_id)){used.add(c.parent_id);changed=true;}}}
  return pending.filter(c=>used.has(c.temp_id));
 }
 document.getElementById('add-ingredient').addEventListener('click',()=>{itemRow();itemRoot.lastElementChild?.querySelector('.ingredient-entry-name')?.focus();});
 document.getElementById('add-step').addEventListener('click',()=>stepRow());
 (initialItems.length?initialItems:[{},{}]).forEach(itemRow);
 (initialSteps.length?initialSteps:['']).forEach(stepRow);
 document.getElementById('recipe-form').addEventListener('submit',async event=>{
  event.preventDefault();
  const button=document.getElementById('save-recipe'),error=document.getElementById('editor-error');error.hidden=true;
  const items=[...itemRoot.querySelectorAll('.ingredient-editor-row')].map(row=>({
   name:row.querySelector('.ingredient-entry-name').value.trim(),quantity:row.querySelector('.ingredient-entry-quantity').value.trim(),
   kind:row.querySelector('.ingredient-entry-kind').value,optional:row.querySelector('.ingredient-entry-optional').checked,
   is_brand:row.querySelector('.ingredient-entry-brand').checked,categories:categorySelections(row)
  }));
  const steps=[...stepRoot.querySelectorAll('textarea')].map(e=>e.value.trim()).filter(Boolean);
  const payload={title:document.getElementById('recipe-title').value,technique:document.getElementById('recipe-technique').value,
   glass:document.getElementById('recipe-glass').value,notes:document.getElementById('recipe-notes').value,steps,items,new_categories:usedCategories(items)};
  const photo=document.getElementById('recipe-photo-upload').files[0];
  if(photo&&photo.size>4*1024*1024){error.textContent='Choose a photo smaller than 4 MB.';error.hidden=false;return;}
  button.disabled=true;button.textContent='Saving…';
  try{
   const id=event.currentTarget.dataset.recipeId;
   const response=await fetch(id?`/recipes/${id}/edit`:'/recipes/new',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':document.querySelector('meta[name=csrf-token]').content},body:JSON.stringify(payload),credentials:'same-origin'});
   const result=await response.json();if(!response.ok)throw Error(result.error||'Unable to save recipe.');
   if(photo){
    const form=new FormData();form.append('image',photo);
    const upload=await fetch(`/recipes/${result.id}/image`,{method:'POST',headers:{'X-CSRF-Token':document.querySelector('meta[name=csrf-token]').content,'Accept':'application/json'},body:form,credentials:'same-origin'});
    if(!upload.ok){const detail=await upload.json().catch(()=>({}));throw Error((detail.error||'Recipe saved, but photo upload failed.')+' You can replace the photo from Settings → Manage recipes.');}
   }
   window.location.assign(id?'/settings/recipes':`/recipes/${result.id}`);
  }catch(ex){error.textContent=ex.message;error.hidden=false;error.scrollIntoView({behavior:'smooth',block:'center'});button.disabled=false;button.textContent='Save recipe ↗';}
 });
}

// No private recipe pages or photos are cached by the service worker.
if('serviceWorker' in navigator&&(location.protocol==='https:'||location.hostname==='localhost')){navigator.serviceWorker.register('/sw.js').catch(()=>{});}
})();
