"""The ONE serve helper for a vendor double the agent ships (`world_state.confirmation` `answer: agent_fake`).

Copy `_serve_agent_fake` into `simulate.py` VERBATIM (verify_simulate compares it to this file, AST for AST; any other
function is no pin) and call it in `seed_state`, after nothing else touches the double:

    os.environ["<ENV>"] = _serve_agent_fake("fake: <path>", initial_world_state)

It reads the entry from `plurai.config.yaml` beside the harness, so the declaration is the only per-agent input.
"""


def _serve_agent_fake(target, initial_world_state):
    """Serve the agent's own vendor double `target` in this process for THIS conversation and return its URL.

    Every input file goes into a fresh temp folder (`data_env`): a shipped file copied from the agent's repo, a world
    type written from this scenario's rows under the header of the shipped file it replaces. Every `state_env` gets a
    path in a fresh temp folder. Then the double is imported and served on 127.0.0.1 on a free port in a daemon thread;
    a server that does not start in 30 s raises."""
    import csv
    import gzip
    import importlib.util
    import io
    import json
    import os
    import shutil
    import socket
    import sys
    import tempfile
    import threading
    import time
    from pathlib import Path

    import uvicorn
    import yaml

    here = Path(__file__).resolve().parent
    root = here.parent
    config = yaml.safe_load((here / 'plurai.config.yaml').read_text(encoding='utf-8'))
    [entry] = [e for e in config['world_state']['confirmation'] if e.get('answer') == 'agent_fake' and e.get('target') == target]
    data = Path(tempfile.mkdtemp(prefix='plurai-fake-data-'))
    entities = initial_world_state.get('entities') if isinstance(initial_world_state.get('entities'), dict) else None
    for name, source in entry['inputs'].items():
        if isinstance(source, str):
            shutil.copyfile(root / source, data / name)
            continue
        if entities is not None:
            rows = [r for r in entities.values() if isinstance(r, dict) and r.get('_entity_type') == source['world']]
        else:
            rows = list(initial_world_state.get(source['world']) or [])
        shipped = (root / source['shipped']).read_bytes()
        text = (gzip.decompress(shipped) if name.endswith('.gz') else shipped).decode('utf-8')
        if '.csv' in name:
            header = next(csv.reader(io.StringIO(text)))
            out = io.StringIO()
            writer = csv.DictWriter(out, header, extrasaction='ignore', lineterminator='\n')
            writer.writeheader()
            writer.writerows({k: '' if row.get(k) is None else str(row[k]) for k in header} for row in rows)
            body = out.getvalue()
        else:
            keep = [{k: v for k, v in row.items() if k != '_entity_type'} for row in rows]
            body = ''.join(json.dumps(r) + '\n' for r in keep) if '.jsonl' in name else json.dumps(keep)
        (data / name).write_bytes(gzip.compress(body.encode('utf-8')) if name.endswith('.gz') else body.encode('utf-8'))
    os.environ[entry['data_env']] = str(data)
    for var in entry.get('state_env') or []:
        os.environ[var] = str(Path(tempfile.mkdtemp(prefix='plurai-fake-state-')) / var.lower())
    path = root / target.split(':', 1)[1].strip()
    spec = importlib.util.spec_from_file_location(f'_agent_fake_{path.parent.name}', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    server = uvicorn.Server(uvicorn.Config(module.app, loop='asyncio', log_level='warning', access_log=False, log_config=None))
    threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True).start()
    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError(f'the agent double {target} did not start')
        time.sleep(0.01)
    return f'http://127.0.0.1:{sock.getsockname()[1]}'
