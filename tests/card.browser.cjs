// Optional visual/interaction check: PLAYWRIGHT_MODULE can point to a local install.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('https://climado.test/**', route => {
      if (route.request().url().endsWith('card.js')) return route.fulfill({ contentType: 'text/javascript',
        body: fs.readFileSync(path.join(__dirname, '../custom_components/climado/frontend/climado-card.js'), 'utf8') });
      return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html><head><style>
        body { margin: 12px; background: #fafafa; font: 16px Arial; --primary-color:#009ac1; --primary-text-color:#222; --secondary-text-color:#666; --card-background-color:white; --divider-color:#ddd; }
        climado-card { display:block; max-width:600px; margin:auto; }
      </style></head><body><script type="module">
        class Switch extends HTMLElement {
          constructor() { super(); this.attachShadow({mode:'open'}).innerHTML='<input type="checkbox">'; this.shadowRoot.querySelector('input').onchange=()=>this.dispatchEvent(new Event('change')); }
          set checked(value) { this.shadowRoot.querySelector('input').checked=value; }
          set disabled(value) { this.shadowRoot.querySelector('input').disabled=value; }
        }
        customElements.define('ha-switch',Switch);
        await import('/card.js');
        const states={
          'select.climado_mode':{state:'auto',attributes:{}},
          'sensor.climado_effective_mode':{state:'inactive',attributes:{main_temp:18,bedroom_temp:18.5,hvac_mode:'off',hvac_action:'idle',running_source:'idle',selected_source:'off',system_control:{entry_id:'test',hvac_mode:'off',modes:['off','cool','heat'],can_start:true,can_stop:true,source_available:true}}},
          'sensor.climado_control_reason':{state:'thermostat_off',attributes:{}},
          'sensor.climado_rate_tier':{state:'Mid-peak',attributes:{tier_id:'mid_peak',profile:'weekday'}},
          'sensor.climado_presence':{state:'occupied',attributes:{}},
          'sensor.climado_fuel_cost_recommendation':{state:'unknown',attributes:{status:'disabled'}},
          'switch.climado_climado_control':{state:'on',attributes:{}},
          'switch.climado_windows_open':{state:'off',attributes:{}},
          'switch.climado_vacation':{state:'off',attributes:{}}
        };
        for(const [key,value] of Object.entries({heat_home:20,heat_away:17,heat_vacation:15,heat_prearrival:20})) states['number.climado_'+key]={state:String(value),attributes:{climado_key:key,min:10,max:28,step:.5}};
        const card=document.createElement('climado-card');
        card.setConfig({entity:'select.climado_mode'});
        window.calls=[];
        card.hass={states,services:{climado:{set_system_mode:{}}},entities:Object.fromEntries(Object.keys(states).map(id=>[id,{device_id:'test'}])),callService:async(...args)=>window.calls.push(args)};
        document.body.append(card);
        window.card=card;
        window.report=async(attributes)=>{ const id='sensor.climado_effective_mode'; const old=card.hass.states[id]; card.hass={...card.hass,states:{...card.hass.states,[id]:{...old,attributes:{...old.attributes,...attributes}}}}; await card.updateComplete; };
      </script></body></html>` });
    });
    await page.goto('https://climado.test/');
    await page.getByText('System off', {exact:true}).waitFor({timeout:45000});
    await page.getByText('Heating targets', {exact:true}).click();
    await page.getByLabel('Heating Home target', {exact:true}).fill('20.5');
    await page.getByLabel('Heating Home target', {exact:true}).press('Tab');
    assert.deepEqual(await page.evaluate(()=>window.calls),[['number','set_value',{entity_id:'number.climado_heat_home',value:20.5}]]);
    await page.evaluate(()=>{window.calls=[];});
    await page.getByRole('button',{name:'Heating source Gas furnace',exact:true}).click();
    assert.deepEqual(await page.evaluate(()=>window.calls),[]);
    await page.getByRole('button',{name:'System Heat',exact:true}).click();
    assert.deepEqual(await page.evaluate(()=>window.calls),[['climado','set_system_mode',{entry_id:'test',hvac_mode:'heat',heat_source:'gas'}]]);
    assert.equal(await page.getByRole('button',{name:'System Off',exact:true}).getAttribute('aria-pressed'),'true');
    assert.equal(await page.getByRole('button',{name:'Heating source Automatic',exact:true}).isDisabled(),true);
    await page.evaluate(async()=>{
      const c=window.card;
      c.hass={...c.hass,states:{...c.hass.states,'sensor.climado_control_reason':{state:'windows_open',attributes:{}},'switch.climado_windows_open':{state:'on',attributes:{}},'sensor.climado_effective_mode':{...c.hass.states['sensor.climado_effective_mode'],state:'windows_open'}}};
      await window.report({main_temp:15.2,bedroom_temp:15.6,windows_open:true,system_control:{entry_id:'test',hvac_mode:'off',modes:['off','cool','heat'],can_start:false,can_stop:false,source_available:true,blocked_reason:'Windows open pause or restoration is active'},heating_alerts:{issues:[{code:'windows_cold',message:'Windows open is still active and an indoor sensor has stayed below 16 C for at least 10 minutes. Heating remains paused.'}]}});
    });
    for(const width of [320,390,1100]) {
      await page.setViewportSize({width,height:1100});
      const boxes=await page.locator('.system-controls,.segment,.target-inputs,.target-inputs label,.target-inputs input,.heating-alert').evaluateAll(nodes=>nodes.map(node=>{const r=node.getBoundingClientRect();return {left:r.left,right:r.right,overflow:node.scrollWidth>node.clientWidth+1};}));
      assert(boxes.every(b=>b.left>=0 && b.right<=width && !b.overflow),JSON.stringify(boxes));
      const timeline=await page.locator('.bar').boundingBox();
      assert(timeline && timeline.height>=26 && timeline.width>100,'Rate timeline must remain visible');
      const screenshot=path.join(os.tmpdir(),'climado-0.4.0b1-'+width+'.png');
      await page.screenshot({path:screenshot,fullPage:true});
      console.log('Layout passed: '+width+'px; '+screenshot);
    }
    await page.evaluate(()=>window.report({system_control:{entry_id:'test',hvac_mode:'off',modes:['off','cool','heat'],can_start:false,can_stop:false,source_available:true,blocked_reason:'Windows open pause or restoration is active'}}));
    assert.equal(await page.getByRole('button',{name:'System Heat',exact:true}).isDisabled(),true);
    await page.evaluate(()=>{window.calls=[];});
    await page.getByRole('button',{name:'Heating source Heat pump',exact:true}).click();
    await page.getByRole('button',{name:'Heating source Gas furnace',exact:true}).click();
    assert.equal(await page.getByRole('button',{name:'Heating source Gas furnace',exact:true}).getAttribute('aria-pressed'),'true');
    assert.equal(await page.getByRole('button',{name:'System Heat',exact:true}).isDisabled(),true);
    assert.equal(await page.getByRole('button',{name:'Heating source Automatic',exact:true}).isDisabled(),true);
    assert.deepEqual(await page.evaluate(()=>window.calls),[],'Draft selection during Windows pause must not send commands');
    await page.locator('ha-switch[aria-label="Windows open"] input').click();
    assert.deepEqual(await page.evaluate(()=>window.calls),[['switch','toggle',{entity_id:'switch.climado_windows_open'}]]);
    await page.evaluate(()=>{
      const c=window.card;
      c.hass={...c.hass,states:{...c.hass.states,
        'sensor.climado_control_reason':{state:'system_mode_pending',attributes:{}},
        'switch.climado_windows_open':{state:'off',attributes:{}},
        'sensor.climado_effective_mode':{...c.hass.states['sensor.climado_effective_mode'],state:'inactive'}}};
    });
    await page.evaluate(()=>window.report({
      main_temp:20,bedroom_temp:20.5,windows_open:false,heating_alerts:{issues:[]},
      thermostat_feedback:{source:'local',fresh:true,local_status:'ready',hvac_mode:'heat',hvac_action:'heating',
        mode_disagreement:true,reported_at:new Date().toISOString()},
      system_control:{entry_id:'test',hvac_mode:'heat',cloud_hvac_mode:'off',modes:['off','cool','heat'],
        can_start:false,can_stop:true,source_available:true,pending:{mode:'heat',source:'gas'},
        request:{mode:'heat',source:'gas',status:'pending',mode_confirmed_locally:true,
          requested_at:new Date(Date.now()-65000).toISOString()}}
    }));
    await page.getByText(/Heat mode confirmed locally; waiting for fuel confirmation/).waitFor();
    assert.equal(await page.getByRole('button',{name:'System Off',exact:true}).isDisabled(),false);
    assert.equal(await page.getByRole('button',{name:'System Heat',exact:true}).isDisabled(),true);
    const before=await page.locator('.feedback-note').textContent();
    await page.waitForFunction(previous=>window.card.shadowRoot.querySelector('.feedback-note').textContent!==previous,
      before,{timeout:4000});
    assert.notEqual(await page.locator('.feedback-note').textContent(),before,'Feedback age should tick without a HA update');
    for(const width of [320,390,1100]) {
      await page.setViewportSize({width,height:1100});
      const boxes=await page.locator('.system-controls,.control-note,.feedback-note,.segment').evaluateAll(nodes=>nodes.map(node=>{
        const r=node.getBoundingClientRect();return {left:r.left,right:r.right,overflow:node.scrollWidth>node.clientWidth+1};
      }));
      assert(boxes.every(b=>b.left>=0 && b.right<=width && !b.overflow),JSON.stringify(boxes));
      const screenshot=path.join(os.tmpdir(),'climado-local-feedback-'+width+'.png');
      await page.screenshot({path:screenshot,fullPage:true});
      console.log('Local feedback layout passed: '+width+'px; '+screenshot);
    }
    await page.evaluate(()=>window.report({thermostat_feedback:{source:'cloud',fresh:true,
      local_status:'unavailable_or_stale',reported_at:new Date().toISOString(),refresh_error:'offline'}}));
    await page.getByText(/Local feedback unavailable or stale/).waitFor();
    await page.getByText('Status refresh delayed; no equipment command repeated',{exact:true}).waitFor();
    await page.evaluate(async()=>{
      const c=window.card;
      c.hass={...c.hass,states:{...c.hass.states,
        'sensor.climado_fuel_cost_recommendation':{state:'unknown',attributes:{status:'disabled',setup:{
          status:'unavailable',reason:'missing_inputs',
          missing_inputs:['outdoor_temp_sensor','electricity_prices','gas_blocks','cop_points','furnace_efficiency','furnace_aux_kwh_per_heat_kwh','prices_consistent']
        }}}}};
      await window.report({main_temp:null,bedroom_temp:21.3,room_readings:{
        main:{entity_id:'sensor.main',status:'stale'},bedroom:{entity_id:'sensor.bedroom',status:'ready'}
      }});
      window.calls=[];
    });
    await page.getByText('Stale reading',{exact:true}).waitFor();
    assert.equal(await page.locator('.fuel-setup').getAttribute('open'),null);
    await page.getByText('Heating cost setup',{exact:true}).click();
    await page.getByText('heat-pump performance table',{exact:true}).waitFor();
    assert.deepEqual(await page.evaluate(()=>window.calls),[],'Setup checklist must be read-only');
    // The ticking-age behavior was checked above; freeze it for stitched images.
    await page.evaluate(async()=>{clearInterval(window.card._statusTimer);await window.card.updateComplete;});
    assert.equal(await page.locator('ha-card').count(),1);
    for(const width of [320,390,1100]) {
      await page.setViewportSize({width,height:1100});
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      const boxes=await page.locator('.room,.room-status,.fuel-setup,.fuel-setup li').evaluateAll(nodes=>nodes.map(node=>{
        const r=node.getBoundingClientRect();return {left:r.left,right:r.right,overflow:node.scrollWidth>node.clientWidth+1};
      }));
      assert(boxes.every(b=>b.left>=0 && b.right<=width && !b.overflow),JSON.stringify(boxes));
      const screenshot=path.join(os.tmpdir(),'climado-cost-setup-'+width+'.png');
      await page.screenshot({path:screenshot,fullPage:true});
      console.log('Cost setup and stale-room layout passed: '+width+'px; '+screenshot);
    }
    await page.evaluate(async()=>{
      await window.report({windows_manual:false,windows_automatic:true,auto_windows:{
        enabled:true,status:'paused',
        open_names:['Alarm Panel Pro 97b758 Patio Door','Alarm Panel Pro 97b758 Dining Room Window','Alarm Panel Pro 97b758 Basement Windows 2']
      }});
      window.calls=[];
    });
    await page.getByText(/Auto windows: heating and cooling paused/).waitFor();
    await page.locator('ha-switch[aria-label="Manual windows pause"] input').click();
    assert.deepEqual(await page.evaluate(()=>window.calls),[['switch','toggle',{entity_id:'switch.climado_windows_open'}]]);
    for(const width of [320,390,1100]) {
      await page.setViewportSize({width,height:1100});
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      const boxes=await page.locator('.row,.tgl,.control-note').evaluateAll(nodes=>nodes.map(node=>{
        const r=node.getBoundingClientRect();return {left:r.left,right:r.right,overflow:node.scrollWidth>node.clientWidth+1};
      }));
      assert(boxes.every(b=>b.left>=0 && b.right<=width && !b.overflow),JSON.stringify(boxes));
      const screenshot=path.join(os.tmpdir(),'climado-auto-windows-'+width+'.png');
      await page.screenshot({path:screenshot,fullPage:true});
      console.log('Auto windows layout passed: '+width+'px; '+screenshot);
    }
    await page.evaluate(async()=>{
      const c=window.card;
      c.hass={...c.hass,states:{...c.hass.states,
        'sensor.climado_control_reason':{state:'windows_restoring',attributes:{}},
        'sensor.climado_effective_mode':{...c.hass.states['sensor.climado_effective_mode'],state:'windows_open'}
      }};
      await window.report({
        windows_open:false,windows_manual:false,windows_automatic:false,windows_restoring:true,windows_pending:true,
        windows_restoration:{mode:'heat',heat_source:'gas',phase:'paused'},
        auto_windows:{enabled:true,status:'monitoring',open_names:[]},
        thermostat_feedback:{source:'local',fresh:true,hvac_mode:'heat',hvac_action:'idle',target:21,
          mode_disagreement:true,reported_at:new Date().toISOString()},
        system_control:{entry_id:'test',hvac_mode:'heat',cloud_hvac_mode:'off',modes:['off','cool','heat'],
          can_start:false,can_stop:false,source_available:true,heat_source:null,
          blocked_reason:'Windows open pause or restoration is active'}
      });
      window.calls=[];
    });
    await page.getByText('Restoring heating',{exact:true}).waitFor();
    assert.equal(await page.locator('.target').textContent(),'21°');
    await page.getByText('Target reported locally; Ecobee confirmation pending',{exact:true}).waitFor();
    await page.getByText('Restoring Gas furnace · awaiting Ecobee confirmation',{exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'Heating source Gas furnace',exact:true}).getAttribute('aria-pressed'),'false');
    assert.equal(await page.getByRole('button',{name:'System Heat',exact:true}).isDisabled(),true);
    for(const width of [320,390,1100]) {
      await page.setViewportSize({width,height:1100});
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      const boxes=await page.locator('.head,.mode,.temps,.control-note').evaluateAll(nodes=>nodes.map(node=>{
        const r=node.getBoundingClientRect();return {left:r.left,right:r.right,overflow:node.scrollWidth>node.clientWidth+1};
      }));
      assert(boxes.every(b=>b.left>=0 && b.right<=width && !b.overflow),JSON.stringify(boxes));
      const titleBox=await page.locator('.mode').boundingBox();
      const targetBox=await page.locator('.temps').boundingBox();
      assert(titleBox.x+titleBox.width<=targetBox.x,'Title must not overlap target');
      const screenshot=path.join(os.tmpdir(),'climado-window-restoration-'+width+'.png');
      await page.screenshot({path:screenshot,fullPage:true});
      console.log('Window restoration layout passed: '+width+'px; '+screenshot);
    }
    assert.deepEqual(await page.evaluate(()=>window.calls),[],'Restoration display must not issue controls');
    await page.evaluate(()=>window.report({thermostat_feedback:{source:'local',fresh:false,hvac_mode:'heat',target:21}}));
    assert.equal(await page.locator('.target').textContent(),'—');
    await page.evaluate(async()=>{
      const c=window.card;
      c.hass={...c.hass,services:{climado:{set_system_mode:{},set_fuel_automation:{}}},states:{...c.hass.states,
        'sensor.climado_control_reason':{state:'night/ecobee-sleep',attributes:{}},
        'sensor.climado_effective_mode':{...c.hass.states['sensor.climado_effective_mode'],state:'sleep'}}};
      window.calls=[];
      await window.report({hvac_mode:'heat',windows_restoring:false,windows_open:false,selected_source:'gas',
        auto_fuel:{enabled:false},thermostat_feedback:null,system_control:{entry_id:'test',hvac_mode:'heat',cloud_hvac_mode:'heat',
          heat_source:'gas',modes:['off','cool','heat'],can_start:false,can_stop:true,source_available:true}});
    });
    await page.getByRole('button',{name:'Heating source Automatic',exact:true}).click();
    assert.deepEqual(await page.evaluate(()=>window.calls),[['climado','set_fuel_automation',{entry_id:'test',enabled:true}]]);
    await page.evaluate(()=>window.report({fuel_control:'ulo',auto_fuel:{enabled:true,outdoor_c:3,reason:'Waiting for the heating cycle to finish'}}));
    assert.equal(await page.getByRole('button',{name:'Heating source Automatic',exact:true}).getAttribute('aria-pressed'),'true');
    for(const width of [320,390,1100]) {
      await page.setViewportSize({width,height:1100});
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      const boxes=await page.locator('.system-controls,.control-note,.segment').evaluateAll(nodes=>nodes.map(node=>{
        const r=node.getBoundingClientRect();return {left:r.left,right:r.right,overflow:node.scrollWidth>node.clientWidth+1};
      }));
      assert(boxes.every(b=>b.left>=0 && b.right<=width && !b.overflow),JSON.stringify(boxes));
      await page.screenshot({path:path.join(os.tmpdir(),'climado-ulo-fuel-'+width+'.png'),fullPage:true});
    }
    await page.getByRole('button',{name:'Heating source Automatic',exact:true}).click();
    assert.equal((await page.evaluate(()=>window.calls))[1][2].enabled,false);
    await page.evaluate(async()=>{
      const c=window.card;
      c.hass={...c.hass,entities:{...c.hass.entities,'select.utility_plan':{device_id:'test'}},states:{...c.hass.states,
        'select.utility_plan':{state:'ULO',attributes:{climado_key:'electricity_plan'}},
        'sensor.climado_rate_tier':{state:'Mid-peak',attributes:{tier_id:'mid_peak',profile:'weekday',electricity_plan:'ULO',
          electricity_price:{status:'ready',basis:'energy_only',per_kwh:.157}}}}};
      window.calls=[];
      await c.updateComplete;
    });
    await page.getByText('15.7¢/kWh',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Electricity plan TOU',exact:true}).click();
    assert.deepEqual(await page.evaluate(()=>window.calls),[['select','select_option',{entity_id:'select.utility_plan',option:'TOU'}]]);
    await page.evaluate(async()=>{
      const c=window.card;
      c.hass={...c.hass,states:{...c.hass.states,'select.utility_plan':{state:'TOU',attributes:{climado_key:'electricity_plan'}},
        'sensor.climado_rate_tier':{state:'On-peak',attributes:{tier_id:'on_peak',profile:'weekday',electricity_plan:'TOU',
          electricity_price:{status:'ready',basis:'energy_only',per_kwh:.189},
          plan:{weekday:[[0,7,'off_peak'],[7,11,'on_peak'],[11,17,'mid_peak'],[17,19,'on_peak'],[19,24,'off_peak']],weekend:[[0,24,'off_peak']]}}}}};
      await window.report({electricity_plan:'TOU',fuel_control:'manual',auto_fuel:{enabled:false}});
    });
    await page.getByText('18.9¢/kWh',{exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'Electricity plan TOU',exact:true}).getAttribute('aria-pressed'),'true');
    assert.equal(await page.getByRole('button',{name:'Heating source Automatic',exact:true}).isDisabled(),true);
    assert.equal(await page.locator('.legend .lg').count(),3);
    assert.equal(await page.locator('.bar .cell').nth(0).getAttribute('title'),'0:00 — Off-peak');
    assert.equal(await page.locator('.bar .cell').nth(7).getAttribute('title'),'7:00 — On-peak');
    assert.equal(await page.locator('.bar .cell').nth(11).getAttribute('title'),'11:00 — Mid-peak');
    for(const width of [320,390,1100]) {
      await page.setViewportSize({width,height:1100});
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      const boxes=await page.locator('.electricity-plan,.rate-segments,.energy-price').evaluateAll(nodes=>nodes.map(node=>{
        const r=node.getBoundingClientRect();return {left:r.left,right:r.right,overflow:node.scrollWidth>node.clientWidth+1};
      }));
      assert(boxes.every(b=>b.left>=0 && b.right<=width && !b.overflow),JSON.stringify(boxes));
      await page.screenshot({path:path.join(os.tmpdir(),'climado-electricity-plan-'+width+'.png'),fullPage:true});
    }
    assert.deepEqual(errors,[]);
    console.log('Targets, source draft, explicit Heat request, observed state and Windows guards passed; no page errors.');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
