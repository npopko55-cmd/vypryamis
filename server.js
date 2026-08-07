const http = require('http');
const fs = require('fs');
const path = require('path');
const PORT = 5181;
const TYPES = { '.html':'text/html; charset=utf-8', '.css':'text/css; charset=utf-8',
  '.js':'application/javascript; charset=utf-8', '.svg':'image/svg+xml',
  '.webp':'image/webp', '.png':'image/png', '.jpg':'image/jpeg', '.mp3':'audio/mpeg' };
http.createServer((req, res) => {
  let p = decodeURIComponent(req.url.split('?')[0]);
  if (p === '/') p = '/index.html';
  const file = path.join(__dirname, p);
  fs.readFile(file, (err, data) => {
    if (err) { res.writeHead(404); return res.end('404'); }
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream',
                         'Cache-Control': 'no-store' });
    res.end(data);
  });
}).listen(PORT, () => console.log('http://localhost:' + PORT));
