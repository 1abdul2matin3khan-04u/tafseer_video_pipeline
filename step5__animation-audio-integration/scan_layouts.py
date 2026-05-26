import json, os
script_dir = os.path.dirname(os.path.abspath(__file__))
d = os.path.join(script_dir, 'remotion_project', 'public', 'output_resources', 'surah_001', 'ruku_1_1')
broken = []
total = 0

for lang in ['en', 'ur']:
    manifest_path = os.path.join(d, lang, 'subblocks_manifest.json')
    manifest = json.load(open(manifest_path, 'r', encoding='utf-8'))
    
    for entry in manifest:
        if entry['subblock_type'] == 'verses':
            continue
        filepath = os.path.join(d, lang, entry['filename'])
        data = json.load(open(filepath, 'r', encoding='utf-8'))
        
        for scene in data.get('scenes', []):
            total += 1
            layout = scene.get('layout', {})
            if not layout:
                broken.append((lang, entry['filename'], scene['scene_no'], 'MISSING', 0, ''))
                continue
            
            ltype = layout.get('type', 'NONE')
            title = layout.get('title', '')
            title_len = len(title)
            issue = None
            
            if title_len > 100:
                issue = f'title_too_long ({title_len} chars)'
            elif ltype == 'bullets' and not layout.get('items'):
                issue = 'missing items array'
            elif ltype == 'table' and (not layout.get('headers') or not layout.get('rows')):
                issue = 'missing headers/rows'
            elif ltype == 'flowchart' and not layout.get('steps'):
                issue = 'missing steps array'
            elif ltype == 'mindmap' and not layout.get('branches'):
                issue = 'missing branches array'
            
            if issue:
                broken.append((lang, entry['filename'], scene['scene_no'], ltype, title_len, issue))

print(f"Total non-verse scenes scanned: {total}")
print(f"Broken layouts found: {len(broken)}")
print()
en_broken = [b for b in broken if b[0] == 'en']
ur_broken = [b for b in broken if b[0] == 'ur']
print(f"EN broken: {len(en_broken)}")
print(f"UR broken: {len(ur_broken)}")
print()
for b in broken:
    print(f"  {b[0].upper()} | {b[1]:35s} | scene {b[2]:3d} | type={b[3]:12s} | {b[5]}")
