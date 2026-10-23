"""Use the existing official icon assets for every browser-extension surface."""
import base64
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parent.parent
icons = ROOT / 'chrome-extension/icons'
icons.mkdir(exist_ok=True)
for size in (32, 128):
    shutil.copy2(ROOT / f'src-tauri/icons/{size}x{size}.png', icons / f'opencore-{size}.png')
pair = ROOT / 'chrome-extension/pair.html'
logo = 'data:image/png;base64,' + base64.b64encode((icons / 'opencore-32.png').read_bytes()).decode('ascii')
text, count = re.subn(r'((?:src|href)=")[^"]*(" data-opencore-logo)',
                      lambda match: match[1] + logo + match[2], pair.read_text(encoding='utf-8'))
if count != 2:
    raise RuntimeError('The pairing page must declare its logo and favicon')
pair.write_text(text, encoding='utf-8')
