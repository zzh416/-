const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = __dirname;
http.createServer((req,res) => {
  let name;
  try { name = decodeURIComponent(new URL(req.url,'http://localhost').pathname); } catch {res.writeHead(400); return res.end();}
  const target = path.resolve(root, '.' + name);
  if (!target.startsWith(root + path.sep)) {res.writeHead(403); return res.end();}
  fs.readFile(target,(err, data) => {
    if(err) {res.writeHead(404); return res.end('Not found');}
    res.setHeader('Content-Type', ({'.html':'text/html; charset=utf-8','.jpg':'image/jpeg','.png':'image/png','.md':'text/plain; charset=utf-8'})[path.extname(target)] || 'application/octet-stream');
    res.end(data);
  });
}).listen(8765,'127.0.0.1',()=>console.log('Preview ready at http://127.0.0.1:8765'));
