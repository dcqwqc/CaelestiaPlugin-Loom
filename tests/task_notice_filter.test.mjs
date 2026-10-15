import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';
const js = readFileSync(new URL('../scripts/task_notice_filter.js', import.meta.url),'utf8');
const context = vm.createContext({});
vm.runInContext(js, context);
const select = context.visibleTaskNotices;
const t = (id,status,updatedAt=995) => ({id,status,updatedAt});
const n = (id,task_id,task_status,created_at=990) => ({
    id,task_id,task_status,created_at,expires_at:created_at+900,
    kind:'status',request_key:'agent-event-'+id,status:'unread'
});
const ids = results => Array.from(results,x=>x.id);
test('recent latest event wins without hiding unrelated alerts',()=>{
    const external={id:'decision',kind:'approval',status:'pending'};
    assert.deepEqual(ids(select([n('old','a','working',980),external,
        n('new','a','working'),n('block','b','blocked')],[t('a','working'),t('b','blocked')],1000)),
        ['decision','new','block']);
});
test('outdated/old/mismatched and offline working messages vanish',()=>{
    const old=n('old','a','working',99);
    const legacy={...old,id:'legacy',task_id:null,expires_at:null};
    assert.deepEqual(ids(select([old,legacy,n('wrong','a','working'),
        {id:'action',kind:'action',status:'unread'}],[t('a','blocked')],1000)),['action']);
    assert.deepEqual(ids(select([n('unconfirmed','a','working')],null,1000)),[]);
});
test('blocked and done transitions remain accurate and expire by wall clock',()=>{
    const blocked=n('blocked','a','blocked');
    const done=n('done','b','done');
    assert.deepEqual(ids(select([blocked,done],[t('a','blocked')],1000)),['blocked','done']);
    assert.deepEqual(ids(select([blocked,done],[t('a','blocked')],1900)),[]);
});
test('Panel runs the real projection on a timer',()=>{
    const q=readFileSync(new URL('../Panel.qml',import.meta.url),'utf8');
    assert.match(q,/TaskNotices\.visibleTaskNotices\(/);
    assert.match(q,/interval: 15000/);
    assert.match(q,/notificationCount: notificationItems\.length/);
});

test('Working cannot be shown from an old task heartbeat',()=>{
    assert.deepEqual(ids(select([n('recycled','a','working')],
        [t('a','working',100)],1000)),[]);
    assert.deepEqual(ids(select([n('recent','a','working')],
        [t('a','working',995)],1000)),['recent']);
});
