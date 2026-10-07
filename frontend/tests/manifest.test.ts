import { expect, it } from 'vitest';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';

it('构建清单能发现源文件变更和产物损坏', () => {
    const root=mkdtempSync(join(tmpdir(),'mewcode-manifest-'));
    try {
        mkdirSync(join(root,'frontend/src'),{recursive:true});
        mkdirSync(join(root,'src/mewcode/web/static'),{recursive:true});
        writeFileSync(join(root,'frontend/src/main.ts'),'source');
        writeFileSync(join(root,'frontend/package-lock.json'),'{}');
        writeFileSync(join(root,'src/mewcode/web/static/index.html'),'output');
        const run=(mode:string)=>execFileSync(process.execPath,['scripts/manifest.mjs',mode,'--root',root],{encoding:'utf8'});
        expect(run('write')).toMatch(/清单/);
        expect(run('verify')).toMatch(/一致/);
        writeFileSync(join(root,'frontend/src/main.ts'),'changed');
        expect(()=>run('verify')).toThrow();
        run('write');
        writeFileSync(join(root,'src/mewcode/web/static/index.html'),'broken');
        expect(()=>run('verify')).toThrow();
    } finally {rmSync(root,{recursive:true,force:true});}
});
