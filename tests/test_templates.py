from pathlib import Path
import json
from jinja2 import Environment,FileSystemLoader

BASE=Path(__file__).resolve().parents[1]
def test_templates_parse():
    env=Environment(loader=FileSystemLoader(BASE/'templates'))
    for path in (BASE/'templates').glob('*.html'):
        env.parse(path.read_text(encoding='utf-8'))

def test_seed_integrity():
    drinks=json.loads((BASE/'seed'/'recipes.json').read_text(encoding='utf-8'))
    assert len(drinks)==22
    assert sum(len(r['ingredients']) for r in drinks)==105
    for r in drinks:
        assert len(r['steps'])>=2
        assert r['source_url'].startswith('https://')
        assert all(i['quantity'] or i['kind'] in ('garnish','ice') for i in r['ingredients'])
