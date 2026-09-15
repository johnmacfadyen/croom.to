'use strict';
let csrf = '', polling;
const $ = id => document.getElementById(id);
const form = $('settings-form');
function notice(text) { $('notice').textContent = text; $('notice').hidden = false; }
async function api(path, data) {
  const options = {credentials: 'same-origin', headers: {}};
  if (data !== undefined) { options.method = 'POST'; options.headers = {'Content-Type':'application/json','X-Croom-CSRF':csrf}; options.body=JSON.stringify(data); }
  const response = await fetch('/api/'+path, options);
  const result = await response.json();
  if (response.status === 401) { showLogin(); throw new Error(result.error || 'Please sign in.'); }
  if (!response.ok) throw new Error(result.error || 'Request failed');
  return result;
}
function showLogin() { $('setup').hidden=true; $('login').hidden=false; $('logout').hidden=true; clearInterval(polling); }
function fill(settings) {
  for (const [key,value] of Object.entries(settings)) {
    const input = form.elements.namedItem(key);
    if (!input) continue;
    if (input.type === 'checkbox') input.checked=value; else input.value=value;
  }
  form.elements.client_secret.value='';
  $('secret-help').textContent=settings.has_secret ? 'A secret is saved. Leave blank to keep it.' : 'Enter the secret value, not its ID.';
  toggleCalendar();
}
function screens(status, settings) {
  for (const key of ['controller_output','meeting_output']) {
    const select=form.elements.namedItem(key), value=settings[key] || '';
    select.replaceChildren(new Option(key==='controller_output'?'Auto-detect touchscreen':'Choose TV display',''));
    const names=new Set();
    for (const screen of status.screens) { names.add(screen.name); select.add(new Option(screen.name+(screen.connected?` · ${screen.width}×${screen.height}`:' · not connected'),screen.name)); }
    if(value&&!names.has(value)) select.add(new Option(value+' · not connected',value));
    select.value=value;
  }
}
function renderStatus(status) {
  $('room-heading').textContent=status.room_name;
  const calendar=status.calendar;
  const labels=[['Displays',status.display_message || 'Touchscreen and TV ready'],['Calendar',calendar.error || (calendar.last_sync?`${calendar.bookings} bookings · synced ${new Date(calendar.last_sync).toLocaleTimeString()}`:calendar.configured?'Waiting for first sync':'Not connected')],['Room services',status.service_error || (status.reconfiguring?'Applying settings…':status.meeting.platforms.length?'Meeting provider ready':'Meeting provider not ready')]];
  $('status-cards').replaceChildren(...labels.map(([title,value])=>{const card=document.createElement('div'), heading=document.createElement('strong');heading.textContent=title;card.append(heading,document.createTextNode(value));return card;}));
  // Keep hotplug options current without discarding unsaved display choices.
  const choices={controller_output:form.elements.controller_output.value,meeting_output:form.elements.meeting_output.value};
  screens(status,choices);
}
async function openSetup() {
  const [status,settings]=await Promise.all([api('status'),api('settings')]); csrf=status.csrf;
  screens(status,settings);fill(settings);renderStatus(status);
  $('login').hidden=true;$('setup').hidden=false;$('logout').hidden=false;
  clearInterval(polling);polling=setInterval(()=>api('status').then(renderStatus).catch(e=>notice(e.message)),5000);
}
function toggleCalendar(){ $('calendar-fields').hidden=!form.elements.calendar_enabled.checked; }
form.elements.calendar_enabled.addEventListener('change',toggleCalendar);
$('login-form').addEventListener('submit',async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{const result=await api('login',{password:$('password').value});csrf=result.csrf;$('password').value='';$('notice').hidden=true;await openSetup();}catch(e){notice(e.message);}finally{button.disabled=false;}});
form.addEventListener('submit',async event=>{event.preventDefault();$('save').disabled=true;try{const data={};for(const key of ['room_name','timezone','controller_output','meeting_output','tenant_id','client_id','room_mailbox','client_secret'])data[key]=form.elements.namedItem(key).value;for(const key of ['calendar_enabled','camera_default_on','mic_default_on'])data[key]=form.elements.namedItem(key).checked;const result=await api('settings',data);fill(result.settings);notice(result.message);}catch(e){notice(e.message);}finally{$('save').disabled=false;}});
$('identify').addEventListener('click',()=>api('displays/identify',{}).then(()=>notice('Display labels are shown for four seconds.')).catch(e=>notice(e.message)));
$('test-calendar').addEventListener('click',async()=>{$('test-calendar').disabled=true;$('calendar-result').textContent='Testing…';try{const result=await api('calendar/test',{});$('calendar-result').textContent=result.message;}catch(e){$('calendar-result').textContent=e.message;}finally{$('test-calendar').disabled=false;}});
$('logout').addEventListener('click',()=>api('logout',{}).then(showLogin).catch(e=>notice(e.message)));
openSetup().catch(()=>showLogin());
