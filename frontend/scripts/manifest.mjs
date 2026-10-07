import {readFileSync,writeFileSync,readdirSync,existsSync} from 'node:fs';
import {resolve,relative,join,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const args=process.argv.slice(2), mode=args[0];
const root=args.includes('--root')?resolve(args[args.indexOf('--root')+1]):resolve(dirname(fileURLToPath(import.meta.url)),'../..');
const frontend=join(root,'frontend'),output=join(root,'src/mewcode/web/static');
const excluded=new Set(['node_modules','test-results','playwright-report','.git']);
function files(dir){return readdirSync(dir,{withFileTypes:true}).flatMap(entry=>excluded.has(entry.name)?[]:entry.isDirectory()?files(join(dir,entry.name)):[join(dir,entry.name)]).sort();}
function digest(file){return createHash('sha256').update(readFileSync(file)).digest('hex');}
function map(dir,skip=[]){return Object.fromEntries(files(dir).filter(path=>!skip.includes(relative(dir,path))).map(path=>[relative(dir,path).replaceAll('\\','/'),digest(path)]));}
function aggregate(values){return createHash('sha256').update(Object.entries(values).map(([path,hash])=>`${path}\0${hash}\n`).join('')).digest('hex');}
try {
    const artifacts=map(output,['manifest.json']);
    if(!artifacts['index.html'])throw new Error('缺少静态入口，请先运行 npm run build');
    const sources=map(frontend);const lock=join(frontend,'package-lock.json');
    const manifest={schema_version:1,algorithm:'sha256',source_sha256:aggregate(sources),lock_sha256:existsSync(lock)?digest(lock):null,sources,artifacts};
    const path=join(output,'manifest.json');
    if(mode==='write'){writeFileSync(path,JSON.stringify(manifest,null,2)+'\n');console.log('已生成前端构建清单 manifest.json');}
    else if(mode==='verify'){
        const saved=JSON.parse(readFileSync(path,'utf8'));
        if(JSON.stringify(saved)!==JSON.stringify(manifest))throw new Error('前端源码、锁文件或产物与构建清单不一致，请运行 npm ci && npm run build');
        console.log('前端源码、锁文件与静态产物一致');
    }else throw new Error('用法: node scripts/manifest.mjs write|verify [--root 项目目录]');
}catch(error){console.error(error.message);process.exitCode=1;}
