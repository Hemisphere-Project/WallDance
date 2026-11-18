import ast, sys, collections
p = 'src/core/tracker.py'
src = open(p).read()
tree = ast.parse(src)
total = len(src.splitlines())
cat = {
 # 1 swap correctors (default OFF) + exclusive support
 '_check_occlusion_cascade_swaps':'1 swap correctors + exclusive support (default OFF)',
 '_check_merge_direction_swaps':'1 swap correctors + exclusive support (default OFF)',
 '_compute_single_pair_cost':'1 swap correctors + exclusive support (default OFF)',
 '_check_two_opt_swaps':'1 swap correctors + exclusive support (default OFF)',
 '_tracks_share_recent_merge_context':'1 swap correctors + exclusive support (default OFF)',
 'has_recent_merge_context':'1 swap correctors + exclusive support (default OFF)',
 'get_dominant_vx_direction':'1 swap correctors + exclusive support (default OFF)',
 '_note_merge_episode':'1 swap correctors + exclusive support (default OFF)',
 # 2 dormant pool + resurrection
 'DormantSnapshot':'2 dormant pool + resurrection',
 '_try_resurrect':'2 dormant pool + resurrection',
 'restore_continuity':'2 dormant pool + resurrection',
 '_retire_track_to_dormant':'2 dormant pool + resurrection',
 '_age_and_prune_dormant_tracks':'2 dormant pool + resurrection',
 # 3 identity-discrimination cost terms
 'trajectory_cost':'3 identity-discrimination cost terms (ON)',
 '_compute_keypoint_cost':'3 identity-discrimination cost terms (ON)',
 'get_normalized_skeleton':'3 identity-discrimination cost terms (ON)',
 '_compute_separation_penalty':'3 identity-discrimination cost terms (ON)',
 '_compute_direction_penalty':'3 identity-discrimination cost terms (ON)',
 '_update_pair_distances':'3 identity-discrimination cost terms (ON)',
 '_is_detection_in_crowded_zone':'3 identity-discrimination cost terms (ON)',
 '_is_suspicious_merge_candidate':'3 identity-discrimination cost terms (ON)',
 # 4 duplicate/shadow/takeover merges
 '_detect_shadow_tracks':'4 shadow / duplicate / takeover merges',
 '_merge_duplicate_tracks':'4 shadow / duplicate / takeover merges',
 '_merge_takeover_duplicates':'4 shadow / duplicate / takeover merges',
 'inherit_subject_from':'4 shadow / duplicate / takeover merges',
 '_pair_key':'4 shadow / duplicate / takeover merges',
 # 5 motion bridge
 '_lazy_bridge_with_motion':'5 motion bridge (3 tiers)',
 '_bridge_with_local_motion_support':'5 motion bridge (3 tiers)',
 '_apply_motion_presence_bridge':'5 motion bridge (3 tiers)',
 '_apply_motion_bridge':'5 motion bridge (3 tiers)',
 'set_motion_bridge_sensitivity':'5 motion bridge (3 tiers)',
 '_bridge_gate_sensitivity_mult':'5 motion bridge (3 tiers)',
 '_bridge_local_min_motion_ratio':'5 motion bridge (3 tiers)',
 '_bridge_presence_min_motion_ratio':'5 motion bridge (3 tiers)',
 '_bridge_local_expand_per_miss':'5 motion bridge (3 tiers)',
 '_bridge_local_max_expansion':'5 motion bridge (3 tiers)',
 '_bridge_local_min_hits':'5 motion bridge (3 tiers)',
 # 6 synthetic blobs
 '_update_motion_blob_persistence':'6 cold blobs as synthetic detections',
 '_fuse_motion_blobs':'6 cold blobs as synthetic detections',
 # 7 occlusion ageing
 '_apply_occlusion_aging':'7 occlusion ageing',
 '_collect_matched_positions':'7 occlusion ageing',
 '_occlusion_distance_for_track':'7 occlusion ageing',
 '_is_track_near_matched_positions':'7 occlusion ageing',
 '_apply_fractional_occlusion_aging':'7 occlusion ageing',
 '_mark_track_occluded':'7 occlusion ageing',
 'note_occlusion_event':'7 occlusion ageing',
 'clear_occlusion_event':'7 occlusion ageing',
 # 8 unmatched-detection rescue / birth
 '_resolve_unmatched_detections':'8 unmatched-detection rules (force / fallback / birth)',
 '_find_closest_track':'8 unmatched-detection rules (force / fallback / birth)',
 '_select_force_update_target':'8 unmatched-detection rules (force / fallback / birth)',
 '_try_force_update_unmatched_detection':'8 unmatched-detection rules (force / fallback / birth)',
 '_try_fallback_update_unmatched_detection':'8 unmatched-detection rules (force / fallback / birth)',
 '_get_creation_gate':'8 unmatched-detection rules (force / fallback / birth)',
 '_create_new_track':'8 unmatched-detection rules (force / fallback / birth)',
 '_forced_update_allowed':'8 unmatched-detection rules (force / fallback / birth)',
 # 9 emission
 'emission_gate':'9 emission gate / warm-up / report cap',
 '_feed_source_of':'9 emission gate / warm-up / report cap',
 'warmup_confirmed':'9 emission gate / warm-up / report cap',
 '_warmup_travel_ok':'9 emission gate / warm-up / report cap',
 '_collect_confirmed_tracks':'9 emission gate / warm-up / report cap',
}
core = 'A association core (gates, cost blend, Hungarian passes, commit)'
for n in ['_mahalanobis_gate_allows','_compute_position_cost','_compute_iou_cost','_combine_assignment_cost','_track_height','_track_match_gate','_compute_dynamic_match_threshold','_compute_cost_matrix','_run_assignment_pass','_commit_match','_apply_track_update','_run_matching_phase','_predict_tracks_for_frame','_begin_frame_update','update@DancerTracker']:
    cat[n]=core
life='B lifecycle expiry + frame-summary log + setup'
for n in ['_finalize_track_lifecycle','_effective_max_age_for_track','_retire_expired_active_tracks','_log_frame_summary','__init__@DancerTracker','set_person_height','get_person_height','set_tracking_mode','set_frame_dimensions','_is_near_edge','smoothing_depth','reset','_compute_centroid@DancerTracker']:
    cat[n]=life
sizes = collections.Counter()
covered = 0
def add(name, n, key=None):
    global covered
    k = cat.get(key or name)
    span = n.end_lineno - n.lineno + 1
    if k is None:
        k = 'C DancerTrack state, Kalman predict/update, getters'
    sizes[k] += span
    covered += span
for n in tree.body:
    if isinstance(n, ast.FunctionDef):
        add(n.name, n)
    elif isinstance(n, ast.ClassDef):
        if n.name == 'DormantSnapshot':
            add(n.name, n); continue
        if n.name in ('PendingTrackUpdate','FrameUpdateContext','ClosestTrackResult'):
            sizes[core] += n.end_lineno-n.lineno+1; covered += n.end_lineno-n.lineno+1; continue
        for m in n.body:
            if isinstance(m, ast.FunctionDef):
                key = m.name
                if m.name in ('update','__init__','_compute_centroid') and n.name=='DancerTracker':
                    key = m.name+'@DancerTracker'
                add(m.name, m, key)
for k in sorted(sizes):
    print(f"{sizes[k]:5d}  {100*sizes[k]/total:4.1f}%  {k}")
print(f"{covered:5d} lines in functions/classes; {total} total; {total-covered} = imports, comments between defs, blank lines")
