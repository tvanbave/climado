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
    await page.locator('ha-switch[aria-label="Windows open"] input').click();
    assert.deepEqual(await page.evaluate(()=>window.calls),[['switch','toggle',{entity_id:'switch.climado_windows_open'}]]);
    assert.deepEqual(errors,[]);
    console.log('Targets, source draft, explicit Heat request, observed state and Windows guards passed; no page errors.');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
