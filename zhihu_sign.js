// 知乎 x-zse-96 签名：复用 MediaCrawler 的 libs/zhihu.js 算法（仅供本凭据中枢内部探活使用）
// 用法: node zhihu_sign.js <url_path> <cookies>  →  输出 {"x-zst-81": "...", "x-zse-96": "..."}
const fs = require('fs');
const vm = require('vm');

const ZHIHU_JS = '/home/ubuntu/projects/MediaCrawler/libs/zhihu.js';
const code = fs.readFileSync(ZHIHU_JS, 'utf8');
const sandbox = { require, module: { exports: {} }, console };
vm.createContext(sandbox);
vm.runInContext(code + "\nmodule.exports.get_sign = get_sign;\n", sandbox);

const url = process.argv[2] || '';
const cookies = process.argv[3] || '';
process.stdout.write(JSON.stringify(sandbox.module.exports.get_sign(url, cookies)));
