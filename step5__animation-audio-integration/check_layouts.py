import json
import os
import argparse

parser = argparse.ArgumentParser(description='Check layouts for a specific Surah and Ruku')
parser.add_argument('--surah', type=int, required=True, help='Surah number')
parser.add_argument('--ruku', type=str, required=True, help='Ruku identifier (e.g. 1_1)')
args = parser.parse_args()

script_dir = os.path.dirname(os.path.abspath(__file__))
d = os.path.join(script_dir, 'remotion_project', 'public', 'output_resources', f'surah_{args.surah:03d}', f'ruku_{args.ruku}')

for lang in ['en', 'ur']:
    manifest_path = os.path.join(d, lang, 'subblocks_manifest.json')
    if os.path.exists(manifest_path):
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
        blocks = sorted(set(e["block_no"] for e in manifest))
        print(f"{lang.upper()}: {len(blocks)} blocks -> {blocks}")
    else:
        print(f"{lang.upper()}: No manifest found at {manifest_path}")
