#!/usr/bin/env python3
"""
process_visual_groups.py
Shared utility for Step 4: Flattens Gemini's visual_groups into per-scene
layout objects and subdivides scenes into subblocks along visual_group boundaries.
"""

import copy


def validate_visual_group(vg):
    """
    Validate a visual_group dict. Returns (is_valid, issues_list).
    If invalid, the caller should downgrade affected scenes to narrative.
    """
    issues = []
    vg_type = vg.get("type", "")
    title = vg.get("title", "")
    scene_range = vg.get("scene_range", [])
    reveals = vg.get("reveals", [])

    if len(title) > 80:
        issues.append(f"title too long ({len(title)} chars), truncating")
        vg["title"] = title[:77] + "..."

    if not scene_range or len(scene_range) != 2:
        issues.append("invalid scene_range")
        return False, issues

    expected_len = scene_range[1] - scene_range[0] + 1
    if len(reveals) != expected_len:
        issues.append(f"reveals length {len(reveals)} != scene_range span {expected_len}")
        # Auto-fix: generate linear reveals
        reveals = list(range(1, expected_len + 1))
        vg["reveals"] = reveals

    # --- Auto-conversion: fix common Gemini output quirks ---
    _auto_convert_gemini_quirks(vg, issues)

    if vg_type == "bullets":
        items = vg.get("items", [])
        if not items:
            issues.append("bullets missing items array")
            return False, issues
        max_reveal = max(reveals) if reveals else 0
        if max_reveal > len(items):
            issues.append(f"max reveal {max_reveal} > items count {len(items)}")

    elif vg_type == "table":
        if not vg.get("headers") or not vg.get("rows"):
            issues.append("table missing headers or rows")
            return False, issues
        max_reveal = max(reveals) if reveals else 0
        if max_reveal > len(vg.get("rows", [])):
            issues.append(f"max reveal {max_reveal} > rows count {len(vg.get('rows', []))}")

    elif vg_type == "timeline":
        if not vg.get("events"):
            issues.append("timeline missing events array")
            return False, issues

    elif vg_type == "comparison":
        if not vg.get("points"):
            issues.append("comparison missing points array")
            return False, issues

    elif vg_type == "hierarchy":
        if not vg.get("children"):
            issues.append("hierarchy missing children array")
            return False, issues

    else:
        issues.append(f"unknown visual type: {vg_type}")
        return False, issues

    return len(issues) == 0 or all("truncating" in i or "reveals length" in i or "auto-converted" in i for i in issues), issues


def _auto_convert_gemini_quirks(vg, issues):
    """
    Fix common Gemini output quirks where it puts data in wrong fields.
    Modifies vg in-place.
    """
    vg_type = vg.get("type", "")

    # --- Comparison: Gemini often puts data in `rows` instead of `points` ---
    if vg_type == "comparison" and not vg.get("points"):
        rows = vg.get("rows", [])
        if rows and len(rows) > 0 and isinstance(rows[0], list):
            # Convert rows [[left, right], ...] → points [{left, right}, ...]
            left_label = vg.get("left_label", "Left")
            right_label = vg.get("right_label", "Right")
            points = []
            for row in rows:
                if len(row) >= 2:
                    points.append({"left": str(row[0]), "right": str(row[1])})
                elif len(row) == 1:
                    points.append({"left": str(row[0]), "right": ""})
            if points:
                vg["points"] = points
                issues.append(f"auto-converted {len(points)} rows -> points for comparison")
        # Also check if items were used instead
        items = vg.get("items", [])
        if not vg.get("points") and items and len(items) >= 2:
            # Try to split items into left/right pairs
            points = []
            for i in range(0, len(items) - 1, 2):
                points.append({"left": str(items[i]), "right": str(items[i + 1])})
            if points:
                vg["points"] = points
                issues.append(f"auto-converted {len(points)} items -> points for comparison")

    # --- Hierarchy: Gemini sometimes puts children in items or rows ---
    if vg_type == "hierarchy" and not vg.get("children"):
        items = vg.get("items", [])
        if items:
            # Convert items ["child1", "child2"] → children [{label: "child1"}, ...]
            children = [{"label": str(item)} for item in items]
            vg["children"] = children
            issues.append(f"auto-converted {len(children)} items -> children for hierarchy")

        rows = vg.get("rows", [])
        if not vg.get("children") and rows:
            # Convert rows [["parent", "child1", "child2"], ...] → children
            children = []
            for row in rows:
                if isinstance(row, list) and len(row) >= 1:
                    label = str(row[0])
                    grandchildren = [str(r) for r in row[1:]] if len(row) > 1 else []
                    children.append({"label": label, "children": grandchildren})
            if children:
                vg["children"] = children
                issues.append(f"auto-converted {len(children)} rows -> children for hierarchy")

    # --- Timeline: Gemini sometimes uses items instead of events ---
    if vg_type == "timeline" and not vg.get("events"):
        items = vg.get("items", [])
        if items:
            events = [{"label": str(item), "description": ""} for item in items]
            vg["events"] = events
            issues.append(f"auto-converted {len(events)} items -> events for timeline")


def flatten_visual_groups(scenes, visual_groups):
    """
    For each scene, find its visual_group (if any) and attach a 'layout' object.

    Args:
        scenes: list of scene dicts with 'scene_no' field
        visual_groups: list of visual_group dicts from Gemini

    Returns:
        Modified scenes list with 'layout' attached to each scene.
    """
    # Build a map: scene_no -> (visual_group, position_in_range)
    scene_to_vg = {}
    valid_groups = []

    for vg in (visual_groups or []):
        is_valid, issues = validate_visual_group(vg)
        if issues:
            print(f"    [Visual Group '{vg.get('group_id', '?')}'] Validation: {'; '.join(issues)}")

        if not is_valid:
            print(f"    [Visual Group '{vg.get('group_id', '?')}'] INVALID — downgrading to narrative")
            continue

        valid_groups.append(vg)
        scene_range = vg["scene_range"]
        reveals = vg.get("reveals", [])

        for i, scene_no in enumerate(range(scene_range[0], scene_range[1] + 1)):
            reveal_count = reveals[i] if i < len(reveals) else (i + 1)
            scene_to_vg[scene_no] = (vg, reveal_count)

    # Attach layout to each scene
    for scene in scenes:
        scene_no = scene["scene_no"]

        if scene_no in scene_to_vg:
            vg, reveal_count = scene_to_vg[scene_no]
            layout = _build_layout_from_vg(vg, reveal_count)
            scene["layout"] = layout
        else:
            scene["layout"] = {"type": "narrative", "theme": "default"}

    return scenes


def _build_layout_from_vg(vg, reveal_count):
    """
    Build a flat, self-contained layout dict for a single scene from a visual_group.
    """
    vg_type = vg["type"]
    theme = vg.get("theme", "default")
    title = vg.get("title", "")

    layout = {
        "type": vg_type,
        "theme": theme,
        "title": title,
        "reveal_count": reveal_count,
    }

    if vg_type == "bullets":
        layout["items"] = vg.get("items", [])

    elif vg_type == "table":
        layout["headers"] = vg.get("headers", [])
        layout["rows"] = vg.get("rows", [])

    elif vg_type == "timeline":
        layout["events"] = vg.get("events", [])

    elif vg_type == "comparison":
        layout["left_label"] = vg.get("left_label", "")
        layout["right_label"] = vg.get("right_label", "")
        layout["points"] = vg.get("points", [])

    elif vg_type == "hierarchy":
        layout["root"] = vg.get("root", "")
        layout["children"] = vg.get("children", [])

    return layout


def subdivide_by_visual_groups(scenes, visual_groups, metadata, max_narrative_scenes=6):
    """
    Subdivide block scenes into subblocks, splitting tafseer/commentary scenes
    along visual_group boundaries.

    Phase 1 (title_toc): Title and introductory scenes before verses
    Phase 2 (verses): Arabic recitation + translation scenes
    Phase 3 (tafseer/commentary): Split by visual_group boundaries

    Args:
        scenes: list of scene dicts (with 'layout' already attached)
        visual_groups: list of valid visual_group dicts (for boundary info)
        metadata: dict with surah_number, surah_name, etc.
        max_narrative_scenes: max scenes per narrative-only subblock

    Returns:
        List of subblock dicts ready for saving.
    """
    # --- Classify scenes into phases ---
    phase1_scenes = []  # Title/TOC
    phase2_scenes = []  # Verses (recitation + translation)
    phase3_scenes = []  # Tafseer/Commentary

    state = 1  # 1: Title/TOC, 2: Recitation/Translation, 3: Tafseer

    last_was_arabic = False

    for scene in scenes:
        script = scene.get("script", "")
        
        # Check if the script contains Arabic characters or explicit recitation keywords
        is_arabic = any('\u0600' <= char <= '\u06FF' for char in script)
        is_recitation_cue = "[Recite Verse" in script or is_arabic
        
        # Check if the script contains translation keywords or is English text following Arabic recitation
        is_translation_cue = (
            "Translation:" in script
            or "Translation (cont):" in script
            or (last_was_arabic and not is_arabic)
        )
        
        is_verse_scene = is_recitation_cue or is_translation_cue

        if state == 1:
            if is_verse_scene:
                state = 2
                phase2_scenes.append(scene)
            else:
                # Commentary or title scenes before the recitation goes to Phase 1
                phase1_scenes.append(scene)
        elif state == 2:
            if not is_verse_scene:
                state = 3
                phase3_scenes.append(scene)
            else:
                phase2_scenes.append(scene)
        elif state == 3:
            phase3_scenes.append(scene)
            
        last_was_arabic = is_arabic

    # --- Build subblocks ---
    subblocks = []
    block_no = metadata.get("block_no", 1)

    base_meta = {
        "surah_number": metadata.get("surah_number"),
        "surah_name": metadata.get("surah_name"),
        "absolute_ruku": metadata.get("absolute_ruku"),
        "relative_ruku": metadata.get("relative_ruku"),
        "verses": metadata.get("verses"),
        "ruku_heading": metadata.get("ruku_heading"),
        "block_no": block_no,
    }

    # Phase 1 subblock
    if phase1_scenes:
        subblocks.append({
            **base_meta,
            "subblock_id": f"block_{block_no}_phase_1",
            "subblock_type": "title_toc",
            "scenes": phase1_scenes,
        })

    # Phase 2 subblock
    if phase2_scenes:
        subblocks.append({
            **base_meta,
            "subblock_id": f"block_{block_no}_phase_2",
            "subblock_type": "verses",
            "scenes": phase2_scenes,
        })

    # Phase 3 subblocks — split by visual_group boundaries
    if phase3_scenes:
        phase3_subblocks = _split_phase3_by_visual_groups(
            phase3_scenes, visual_groups, block_no, base_meta, max_narrative_scenes
        )
        subblocks.extend(phase3_subblocks)

    return subblocks


def _split_phase3_by_visual_groups(scenes, visual_groups, block_no, base_meta, max_narrative_scenes):
    """
    Split phase 3 (tafseer) scenes into subblocks along visual_group boundaries.

    Each visual_group gets its own subblock (intact, never split mid-group).
    Narrative scenes (not in any group) are grouped together (max max_narrative_scenes).
    """
    # Build a set of scene_nos covered by each visual_group
    vg_ranges = []
    for vg in (visual_groups or []):
        sr = vg.get("scene_range", [])
        if len(sr) == 2:
            vg_ranges.append((sr[0], sr[1], vg.get("group_id", "")))

    # Sort by start scene
    vg_ranges.sort(key=lambda x: x[0])

    # Build segments: each segment is either a visual_group or a gap (narrative)
    phase3_scene_nos = [s["scene_no"] for s in scenes]
    if not phase3_scene_nos:
        return []

    scene_map = {s["scene_no"]: s for s in scenes}
    segments = []  # list of (type, scene_list) where type is 'visual' or 'narrative'

    covered = set()
    for start, end, gid in vg_ranges:
        for sno in range(start, end + 1):
            covered.add(sno)

    # Walk through scenes in order, grouping by coverage
    current_narrative = []
    i = 0
    while i < len(phase3_scene_nos):
        sno = phase3_scene_nos[i]

        # Check if this scene starts a visual_group
        vg_match = None
        for start, end, gid in vg_ranges:
            if sno == start:
                vg_match = (start, end, gid)
                break

        if vg_match:
            # Flush any pending narrative scenes
            if current_narrative:
                segments.append(("narrative", current_narrative))
                current_narrative = []

            # Collect all scenes in this visual_group
            start, end, gid = vg_match
            vg_scenes = []
            while i < len(phase3_scene_nos) and phase3_scene_nos[i] <= end:
                vg_scenes.append(scene_map[phase3_scene_nos[i]])
                i += 1
            segments.append(("visual", vg_scenes))
        elif sno in covered:
            # This scene is in a visual_group but not at its start
            # (it's mid-group, which means the group started before phase3)
            # Still include it as part of the group
            current_narrative.append(scene_map[sno])
            i += 1
        else:
            # Narrative scene
            current_narrative.append(scene_map[sno])
            i += 1

    # Flush remaining narrative scenes
    if current_narrative:
        segments.append(("narrative", current_narrative))

    # Convert segments to subblocks
    subblocks = []
    subblock_counter = 1

    for seg_type, seg_scenes in segments:
        if seg_type == "visual":
            # One subblock per visual_group (intact)
            subblocks.append({
                **base_meta,
                "subblock_id": f"block_{block_no}_phase_3_{subblock_counter}",
                "subblock_type": "tafseer",
                "scenes": seg_scenes,
            })
            subblock_counter += 1
        else:
            # Narrative: chunk by max_narrative_scenes
            for chunk_start in range(0, len(seg_scenes), max_narrative_scenes):
                chunk = seg_scenes[chunk_start:chunk_start + max_narrative_scenes]
                subblocks.append({
                    **base_meta,
                    "subblock_id": f"block_{block_no}_phase_3_{subblock_counter}",
                    "subblock_type": "tafseer",
                    "scenes": chunk,
                })
                subblock_counter += 1

    return subblocks
