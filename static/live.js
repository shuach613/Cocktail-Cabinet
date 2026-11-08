/* Auto-refresh private collection views after any API or browser edit.
   The endpoint reads a small SQLite revision counter; no open SSE sockets or
   additional container services are necessary for a Synology deployment. */
(()=>{
 'use strict';
 if(document.body.dataset.auth!=='yes')return;
 const path=location.pathname;
 if(!(/^(\/$|\/pantry\/?$|\/recipes\/\d+\/?$|\/settings\/recipes\/?$|\/settings\/ingredients\/?$)/).test(path))return;
 const meta=document.querySelector('meta[name="collection-revision"]');
 if(!meta)return;
 const initial=Number(meta.content);
 if(!Number.isSafeInteger(initial))return;
 const shelfMeta=document.querySelector('meta[name="shelf-revision"]');
 let shelfRevision=shelfMeta?Number(shelfMeta.content):0;
 let revision=initial, busy=false,changed=false;
 let unsavedShelf=false;
 const shelf=document.getElementById('pantry-form');
 shelf?.addEventListener('change',()=>{unsavedShelf=true;});
 // Recipe checkboxes are a temporary preparation checklist, not shelf ownership.
 // Do not clear someone's checked-off ingredients during an inventory sync.
 const activeRecipeChecklist=()=>path.startsWith('/recipes/')&&
  !!document.querySelector('.ingredient-line input[data-prep-item]:checked');
 function showUpdateNotice(){
  if(document.getElementById('cabinet-live-notice'))return;
  const notice=document.createElement('div');
  notice.id='cabinet-live-notice';
  notice.setAttribute('role','status');
  notice.className='flash-stack';
  const link=document.createElement('button');
  link.className='flash';
  link.textContent=activeRecipeChecklist()?'Your shelf changed. Refresh when finished to see updated ingredients.':'The collection changed. Refresh to see the latest recipes.';
  link.style.cursor='pointer';
  link.addEventListener('click',()=>location.reload());
  notice.append(link);document.body.append(notice);
 }
 async function check(){
  if(document.visibilityState!=='visible'||busy||changed)return;
  busy=true;
  try{
   const response=await fetch('/live/revision',{credentials:'same-origin',cache:'no-store'});
   if(response.status===401||response.redirected)return;
   if(!response.ok)return;
   const data=await response.json();
   if((typeof data.revision==='number'&&data.revision!==revision)||
      (typeof data.shelf_revision==='number'&&data.shelf_revision!==shelfRevision)){
    revision=data.revision;shelfRevision=data.shelf_revision;changed=true;
    if(unsavedShelf||activeRecipeChecklist()){showUpdateNotice();return;}
    location.reload();
   }
  }catch(_){/* offline or network hiccup; retry next interval */}
  finally{busy=false;}
 }
 window.setInterval(check,5000);
 document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')check();});
})();
