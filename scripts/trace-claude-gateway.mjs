import http from 'node:http';
import {writeFileSync,mkdirSync} from 'node:fs';
mkdirSync('artifacts/claude-gateway-trace',{recursive:true});
let index=0;
http.createServer(async(req,res)=>{
  const chunks=[];for await(const chunk of req)chunks.push(chunk);
  const body=Buffer.concat(chunks);
  if(req.url.startsWith('/v1/messages')&&!req.url.includes('count_tokens')){
    writeFileSync(`artifacts/claude-gateway-trace/${++index}.json`,body);
    const p=JSON.parse(body);console.log(JSON.stringify({index,roles:p.messages?.map(m=>m.role),tools:p.tools?.length}));
  }
  const upstream=http.request({host:'127.0.0.1',port:8812,path:req.url,method:req.method,headers:{...req.headers,host:'127.0.0.1:8812'}},response=>{
    res.writeHead(response.statusCode,response.headers);response.pipe(res);
  });
  upstream.on('error',e=>{res.writeHead(502);res.end(String(e));});
  res.on('close',()=>upstream.destroy());upstream.end(body);
}).listen(8815,'127.0.0.1',()=>console.log('Trace proxy listening on localhost:8815'));
