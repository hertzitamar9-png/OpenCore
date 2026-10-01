"""One-shot training stage queued behind the already running GSM8K capture."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--evaluation',type=Path,required=True)
    parser.add_argument('--folder',type=Path,required=True)
    parser.add_argument('--research-root',type=Path,required=True)
    args=parser.parse_args();args.folder.mkdir(parents=True,exist_ok=True)
    (args.folder/'queue-process.json').write_text(json.dumps({'pid':os.getpid(),'started':time.time()},indent=2))
    score=args.evaluation/'score.json'
    while not score.exists():
        if (args.evaluation/'failure.json').exists(): raise RuntimeError('GSM8K stopped; preserve responses and repair/resume before training')
        runner=json.loads((args.evaluation/'process.json').read_text())['runner_pid']
        import psutil
        if not psutil.pid_exists(runner): raise RuntimeError('GSM8K runner exited without a complete score')
        progress=json.loads((args.evaluation/'progress.json').read_text())
        path=args.folder/'training-progress.json';temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'phase':'queued behind GSM8K','evaluation_completed':progress['completed'],
            'evaluation_required':progress['required'],'updated':time.time()},indent=2));temporary.replace(path)
        time.sleep(15)
    result=json.loads(score.read_text())
    if result.get('status')!='complete' or result.get('total')!=1319: raise RuntimeError('GSM8K is not a full completed main/test run')
    # Wait until the owned evaluation server has actually released the GPU.
    server=json.loads((args.evaluation/'process.json').read_text())['server_pid']
    import psutil
    for _ in range(40):
        if not psutil.pid_exists(server): break
        time.sleep(.5)
    else: raise RuntimeError('Completed GSM8K server still running; do not interrupt an unknown GPU owner')
    command=[sys.executable,'-X','utf8','-u',str(Path(__file__).with_name('train_echo_pilot.py')),
        '--folder',str(args.folder),'--research-root',str(args.research_root)]
    subprocess.run(command,check=True)


if __name__=='__main__':
    try: main()
    except BaseException as error:
        # Preserve the error beside the requested job instead of losing it in
        # a background shell; no retries can silently change the protocol.
        if '--folder' in sys.argv:
            folder=Path(sys.argv[sys.argv.index('--folder')+1]);folder.mkdir(parents=True,exist_ok=True)
            (folder/'queue-failure.json').write_text(json.dumps({'error':str(error),'time':time.time()},indent=2))
        raise
