export const ARK_URL = 'https://ark.cn-beijing.volces.com/api/v3';
export const blankDraft = () => ({name:'',provider:'doubao_ark',base_url:ARK_URL,model:'',auth_mode:'bearer',api_key:'',clear_key:false});
export function draftFrom(item) {
  return item ? {...blankDraft(), ...Object.fromEntries(['id','name','provider','base_url','model','auth_mode'].map(k=>[k,item[k]]))} : blankDraft();
}
export function isDirty(form, saved) {
  const initial = draftFrom(saved);
  return Object.keys(initial).some(k=>form[k] !== initial[k]);
}
export function missingFields(form, saved) {
  const missing = [['name','连接名称'],['base_url','Base URL'],['model','Model ID']].filter(([k])=>!form[k]?.trim()).map(([,label])=>label);
  const kept = saved?.key_configured && saved.provider === form.provider && saved.base_url === form.base_url && !form.clear_key;
  if (form.auth_mode === 'bearer' && !form.api_key.trim() && !kept) missing.push('API Key');
  return missing;
}
export function requestMessages(history, content) {
  const messages = [...history.slice(-8).map(m=>({...m})), {role:'user',content}];
  while (messages.length > 1 && messages.reduce((n,m)=>n+m.content.length,0) > 12000) messages.splice(0,2);
  return messages;
}
