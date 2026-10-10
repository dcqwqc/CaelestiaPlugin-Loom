import assert from 'node:assert/strict';
import test from 'node:test';
globalThis.JSWindowActorChild = class {};
const { QwqcHeyTabbyChild } = await import('../bridge/zen/actors/QwqcHeyTabbyChild.sys.mjs');

function el(text='',label='',href='') {
  return {innerText:text,textContent:text,href,
    getAttribute(key){return key==='href'?href:key==='aria-label'?label:'';}};
}
function actorFixture({id='target', title='Original', ambiguous=false}={}) {
  const href='https://chatgpt.com/c/'+id;
  const a=Object.create(QwqcHeyTabbyChild.prototype);
  const link=el(title,'',href), actions=el('','Chat actions'), rename=el('Rename','Rename'), save=el('Save','Save');
  const editor={value:title,disabled:false,focus(){},dispatchEvent(){}};
  const row={parentElement:null,querySelectorAll:()=>[actions]};
  link.parentElement=row;
  let step=0;
  const clicks=[];
  a.document={querySelectorAll(selector){
    if (selector.startsWith('a[href')) return ambiguous ? [link,el('Other','',href)] : [link];
    if (selector.startsWith('[role="menuitem"]')) return step>=1?[rename]:[];
    if (selector.includes('input[aria-label')) return step>=2?[editor]:[];
    if (selector.startsWith('[role="dialog"] button')) return step>=2?[save]:[];
    return [];
  }};
  a.contentWindow={location:{href},setTimeout(fn){fn();},Event:class{},KeyboardEvent:class{}};
  a.visible=()=>true;
  a.trustedClick=element=>{
    clicks.push(element);
    if(element===actions) step=1;
    if(element===rename) step=2;
    if(element===save){link.innerText=editor.value;link.textContent=editor.value;step=3;}
    return true;
  };
  return {a,clicks,link,editor};
}

test('verified chat name renames only exact linked conversation',async()=>{
  const {a,link,clicks}=actorFixture();
  const result=await a.renameChat('target','Implement durable callbacks');
  assert.equal(result.ok,true, JSON.stringify(result));
  assert.equal(result.result,'chat-name-verified');
  assert.equal(link.innerText,'Implement durable callbacks');
  assert.equal(clicks.length,3);
});

test('already matching chat name performs no mutation',async()=>{
  const {a,clicks}=actorFixture({title:'Already right'});
  assert.equal((await a.renameChat('target','Already right')).result,'chat-name-already-matches');
  assert.equal(clicks.length,0);
});

test('wrong conversation or duplicated sidebar rows cannot be renamed',async()=>{
  const first=actorFixture();
  assert.equal((await first.a.renameChat('not-target','New title')).result,'rename-identity-invalid');
  assert.equal(first.clicks.length,0);
  const duplicate=actorFixture({ambiguous:true});
  assert.equal((await duplicate.a.renameChat('target','New title')).result,'rename-chat-link-ambiguous');
  assert.equal(duplicate.clicks.length,0);
});
