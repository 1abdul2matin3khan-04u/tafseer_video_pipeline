import json
d = 'step5__animation-audio-integration/remotion_project/public/output_resources/surah_001/ruku_1_1'
for lang in ['en', 'ur']:
    manifest = json.load(open(f"{d}/{lang}/subblocks_manifest.json"))
    blocks = sorted(set(e["block_no"] for e in manifest))
    print(f"{lang.upper()}: {len(blocks)} blocks -> {blocks}")
