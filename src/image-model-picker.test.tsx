import React from 'react';
import {render,screen,waitFor,act,cleanup} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {ChatImageModelOptions} from './image-generation-panel.js';

afterEach(()=>{cleanup();delete (globalThis as any).__opencoreImageAdapters;delete (globalThis as any).__opencoreModelLibrary;delete (globalThis as any).__TAURI_INTERNALS__;});
const image=(id:string)=>({id,label:id,category:'image',installed:true});
const renderPicker=(loadModels:any)=>render(<ChatImageModelOptions onSelect={()=>{}} loadModels={loadModels}/>);

it('reuses the known image choices on reopen and updates them after an installation or removal',async()=>{
  const load=vi.fn(async()=>({models:[image('First image')]}));
  const adapters=vi.fn(async()=>[]);(globalThis as any).__opencoreImageAdapters=adapters;
  const picker=renderPicker(load);
  await screen.findByRole('button',{name:/First image/});await waitFor(()=>expect(adapters).toHaveBeenCalledTimes(1));
  picker.unmount();renderPicker(load);
  expect(screen.getByRole('button',{name:/First image/})).toBeTruthy();
  expect(load).toHaveBeenCalledTimes(1);expect(adapters).toHaveBeenCalledTimes(1);
  act(()=>window.dispatchEvent(new CustomEvent('opencore-model-library-changed',{detail:{models:[image('Second image')]}})));
  expect(screen.queryByRole('button',{name:/First image/})).toBeNull();expect(screen.getByRole('button',{name:/Second image/})).toBeTruthy();
  act(()=>window.dispatchEvent(new CustomEvent('opencore-model-library-changed',{detail:{models:[]}})));
  expect(screen.getByText(/No image models are downloaded/)).toBeTruthy();
});

it('does not show a previous account or device snapshot after its scope changes',async()=>{
  let scope='phone';const load=vi.fn().mockResolvedValueOnce({models:[image('Phone model')]}).mockResolvedValueOnce({models:[image('PC model')]});
  (globalThis as any).__opencoreModelLibrary={getScope:()=>scope,peek:()=>null};
  const first=renderPicker(load);await screen.findByRole('button',{name:/Phone model/});first.unmount();
  scope='pc';renderPicker(load);expect(screen.queryByRole('button',{name:/Phone model/})).toBeNull();
  await screen.findByRole('button',{name:/PC model/});expect(load).toHaveBeenCalledTimes(2);
});

it('reflects a model added while the picker is closed, without rescanning on reopen',async()=>{
  const load=vi.fn(async()=>({models:[image('Before')]}));(globalThis as any).__opencoreImageAdapters=vi.fn(async()=>[]);
  const first=renderPicker(load);await screen.findByRole('button',{name:/Before/});first.unmount();
  act(()=>window.dispatchEvent(new CustomEvent('opencore-model-library-changed',{detail:{models:[image('Added while closed')]}})));
  renderPicker(load);expect(screen.getByRole('button',{name:/Added while closed/})).toBeTruthy();expect(screen.queryByRole('button',{name:/Before/})).toBeNull();expect(load).toHaveBeenCalledTimes(1);
});

it('rechecks adapter choices only when installation state changed while closed',async()=>{
  const load=vi.fn(async()=>({models:[image('Base')]}));const adapters=vi.fn(async()=>[]);(globalThis as any).__opencoreImageAdapters=adapters;
  const first=renderPicker(load);await screen.findByRole('button',{name:/Base/});await waitFor(()=>expect(adapters).toHaveBeenCalledTimes(1));first.unmount();
  act(()=>window.dispatchEvent(new Event('opencore-model-library-invalidated')));
  renderPicker(load);expect(screen.getByRole('button',{name:/Base/})).toBeTruthy();await waitFor(()=>expect(adapters).toHaveBeenCalledTimes(2));expect(load).toHaveBeenCalledTimes(1);
});
