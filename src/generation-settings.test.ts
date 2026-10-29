import {describe,it,expect} from 'vitest';
import {saveGenerationSettings,generationOptions,applyImageGenerationOptions} from './generation-settings.js';
describe('generation settings',()=>{
  it('keeps automatic untouched and applies manual values to actual image settings',()=>{
    const automatic={steps:40,width:2048,height:2048,guidanceScale:1,seed:99};
    saveGenerationSettings('qwen-image-test',{mode:'automatic'});
    expect(applyImageGenerationOptions('qwen-image-test',automatic)).toBe(automatic);
    saveGenerationSettings('qwen-image-test',{mode:'manual',values:{steps:19,guidanceScale:1.3,seed:42,width:1024,height:1024}});
    expect(applyImageGenerationOptions('qwen-image-test',automatic)).toMatchObject({steps:19,guidanceScale:1.3,seed:42,width:1024,height:1024});
    expect(generationOptions('another-model')).toBeNull();
    saveGenerationSettings('qwen-image-test',{mode:'automatic'});expect(generationOptions('qwen-image-test')).toBeNull();
  });
  it('rejects invalid values instead of forwarding them to a model',()=>{
    saveGenerationSettings('bad-model',{mode:'manual',values:{temperature:'NaN'}});
    expect(()=>generationOptions('bad-model')).toThrow(/temperature/);
  });
});
