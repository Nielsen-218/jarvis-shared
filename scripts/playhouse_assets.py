"""Re-encode approved illustrations as WebP; preserve original PNG sources."""
import json
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / 'app' / 'games-assets'

if __name__ == '__main__':
    records = []
    for name in ('family', 'couple', 'covers', 'room'):
        source = ROOT / (name + '.png')
        target = ROOT / (name + '.webp')
        with Image.open(source) as original:
            original.save(target, format='WEBP', quality=90, method=6)
            with Image.open(target) as encoded:
                assert encoded.size == original.size
                records.append(dict(asset=name, dimensions=original.size,
                                    original_bytes=source.stat().st_size,
                                    webp_bytes=target.stat().st_size))
    print(json.dumps(records, indent=2))
