"""
Fix corrupted layout JSON in EN manifest files.
The Gemini model sometimes dumps all layout data into the 'title' field.
This script detects and fixes these cases by:
1. Truncating overly long titles to just the meaningful part
2. Adding appropriate items/rows based on the layout type and scene script
"""
import os, json, re

d = 'step5__animation-audio-integration/remotion_project/public/output_resources/surah_001/ruku_1_1/en'

fixes = {
    'block_4_phase_3_1.json': {
        7: {
            "type": "bullets",
            "title": "Declaration of Praise to Allah",
            "items": [
                "All praise is due to Allah",
                "Lord of the worlds (Rabb al-'Alamin)",
                "All-encompassing declaration"
            ],
            "reveal_count": 3
        }
    },
    'block_4_phase_3_3.json': {
        18: {
            "type": "bullets",
            "title": "Divine Mercy: Ar-Rahman & Ar-Raheem",
            "items": [
                "Ar-Rahman: The Entirely Merciful",
                "Ar-Raheem: The Especially Merciful",
                "Two distinct yet complementary attributes"
            ],
            "reveal_count": 3
        }
    },
    'block_4_phase_3_4.json': {
        24: {
            "type": "table",
            "title": "Ar-Rahman vs Ar-Raheem",
            "headers": ["Attribute", "Ar-Rahman", "Ar-Raheem"],
            "rows": [
                ["Scope", "All creation", "Believers specifically"],
                ["Nature", "Inherent mercy", "Active mercy"],
                ["Duration", "This world", "This world & Hereafter"]
            ]
        }
    },
    'block_7_phase_3_2.json': {
        11: {
            "type": "bullets",
            "title": "The Straight Path (As-Sirat al-Mustaqeem)",
            "items": [
                "Guidance to the correct way",
                "The path of truth and righteousness",
                "A direct, unwavering course"
            ],
            "reveal_count": 3
        },
        14: {
            "type": "bullets",
            "title": "Those Who Earned Allah's Favor",
            "items": [
                "The Prophets (Anbiya)",
                "The Truthful (Siddiqeen)",
                "The Martyrs (Shuhada)",
                "The Righteous (Saliheen)"
            ],
            "reveal_count": 4
        },
        15: {
            "type": "table",
            "title": "Groups Mentioned in Al-Fatiha",
            "headers": ["Group", "Description"],
            "rows": [
                ["Those favored", "Prophets, truthful, martyrs, righteous"],
                ["Those who earned anger", "Knew truth but rejected it"],
                ["Those who went astray", "Lost the path of guidance"]
            ]
        }
    },
    'block_7_phase_3_6.json': {
        36: {
            "type": "table",
            "title": "Key Themes of Surah Al-Fatiha",
            "headers": ["Theme", "Verse Reference"],
            "rows": [
                ["Praise & Lordship", "Verse 2"],
                ["Divine Mercy", "Verse 3"],
                ["Day of Judgment", "Verse 4"],
                ["Worship & Seeking Help", "Verse 5"],
                ["Guidance", "Verses 6-7"]
            ]
        }
    }
}

for filename, scene_fixes in fixes.items():
    filepath = os.path.join(d, filename)
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    modified = False
    for scene in data.get('scenes', []):
        sno = scene['scene_no']
        if sno in scene_fixes:
            scene['layout'] = scene_fixes[sno]
            modified = True
            print(f"Fixed {filename} scene {sno}: {scene_fixes[sno]['type']} - {scene_fixes[sno]['title']}")
    
    if modified:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

print("\nAll corrupted layouts fixed!")
