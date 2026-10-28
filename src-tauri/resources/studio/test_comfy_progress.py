import asyncio,importlib.util,json,tempfile,threading,time,unittest
from pathlib import Path
from asset_worker import SamplingProgress
from comfy_worker import start_progress_stream

@unittest.skipUnless(importlib.util.find_spec('aiohttp'),'ComfyUI requires aiohttp')
class ComfyProgressTests(unittest.TestCase):
    def test_real_websocket_events_are_scoped_to_this_prompt_and_sampling_node(self):
        from aiohttp import web
        ready,finish=threading.Event(),threading.Event();state={}
        async def socket(request):
            connection=web.WebSocketResponse();await connection.prepare(request)
            for prompt,node,step in [('other','16',2),('own','9',2),('own','16',1),('own','16',4)]:
                await connection.send_json({'type':'progress','data':{'prompt_id':prompt,'node':node,'value':step,'max':4}});await asyncio.sleep(.03)
            await connection.send_json({'type':'executing','data':{'prompt_id':'own','node':'17'}})
            await connection.close();return connection
        async def serve():
            app=web.Application();app.router.add_get('/ws',socket);runner=web.AppRunner(app);await runner.setup()
            site=web.TCPSite(runner,'127.0.0.1',0);await site.start();state['port']=site._server.sockets[0].getsockname()[1];ready.set()
            try:
                while not finish.is_set():await asyncio.sleep(.02)
            finally:await runner.cleanup()
        server=threading.Thread(target=lambda:asyncio.run(serve()),daemon=True);server.start();self.assertTrue(ready.wait(5))
        try:
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);tracker=SamplingProgress(root,4);samples=[];original=tracker.update
                def update(step,total=None):samples.append(step);return original(step,total)
                tracker.update=update
                stop=start_progress_stream('http://127.0.0.1:'+str(state['port']),'fixture',tracker,['own'])
                try:
                    deadline=time.monotonic()+5
                    while time.monotonic()<deadline:
                        try:value=json.loads((root/'progress.json').read_text())
                        except (OSError,ValueError):value={}
                        if value.get('stage')=='Decoding image':break
                        time.sleep(.03)
                    self.assertEqual(samples,[1,4]);self.assertEqual(value.get('step'),4)
                    self.assertEqual(value.get('stage'),'Decoding image')
                finally:stop()
        finally:finish.set();server.join(timeout=5)

if __name__=='__main__':unittest.main()
