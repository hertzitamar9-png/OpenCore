import React from 'react';
import {afterEach,describe,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {GenerationSettings} from './generation-settings.js';

afterEach(()=>{cleanup();delete (globalThis as any).__OPENCORE_PHONE__;vi.restoreAllMocks();});
describe('the same generation settings control on Phone',()=>{
  function open(){
    HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
    HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
    render(<GenerationSettings modelId="phone-native-test" supported={false}/>);
    fireEvent.click(screen.getByRole('button',{name:'Generation settings'}));
  }
  it('keeps the desktop control and enables its native-phone sampler fields',()=>{
    (globalThis as any).__OPENCORE_PHONE__=true;open();
    expect((screen.getByRole('button',{name:'Manual'}) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole('button',{name:'Manual'}));
    for(const name of ['Temperature','Top P','Top K','Repetition penalty','Maximum output tokens','Seed (blank = random)'])expect(screen.getByLabelText(name)).toBeTruthy();
  });
  it('preserves the existing unsupported desktop behavior',()=>{open();expect((screen.getByRole('button',{name:'Manual'}) as HTMLButtonElement).disabled).toBe(true);});
});
