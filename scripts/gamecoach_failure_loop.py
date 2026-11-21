"""Offline failure-driven development comparison and training-only review mining.

No fitting, label promotion, certification or activation. The fixed challenger
adds four overlapping two-thirds-frame crops to full-frame inference, then global NMS.
This tests the observed small-target failure without another blind fit.
"""
from __future__ import annotations

import argparse
import ast
import json
from hashlib import sha256
from pathlib import Path
import sys
from time import perf_counter

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.gamecoach_enemy_detector import preprocess
from scripts.gamecoach_apex_train import _hash_file, _private_frame, load_local_yolo
from scripts.gamecoach_vision_diagnose import diagnostic_boxes, pixel_rows, counts, add_counts, finish
from scripts.gamecoach_vision_validate import match_frame, overlap

THRESHOLDS = (.1, .5, .85, .92)  # Frozen diagnostics; live policy never changes.


def fingerprint(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def guard_catalogs(manifest, paths, heldout_source, mode):
    """Validate ALL source metadata before any frame access or model loading."""
    training = manifest.get('training_clip_sha256s')
    validation = manifest.get('development_validation_clip_sha256')
    if (manifest.get('game') != 'apex' or not fingerprint(heldout_source) or not isinstance(training, list) or not training
            or not all(fingerprint(v) for v in [*training, validation])
            or len(set([*training, validation, heldout_source])) != len(training) + 2
            or manifest.get('training_source_clip_sha256') != [*training, validation]):
        raise ValueError('Distinct explicit training/development/heldout provenance required')
    catalogs = []
    seen = set()
    for path in paths:
        data = json.loads(path.read_text(encoding='utf-8'))
        source = data.get('source_clip_sha256')
        split = 'val' if mode == 'compare' else 'train'
        allowed = [validation] if mode == 'compare' else training
        if data.get('game') != 'apex' or data.get('split') != split or source not in allowed:
            raise ValueError('Wrong source or split; heldout and development cannot enter curation')
        if source in seen:
            raise ValueError('Repeated source catalog')
        seen.add(source)
        cases = data.get('cases')
        if not isinstance(cases, list) or not 1 <= len(cases) <= 5000:
            raise ValueError('Expected 1..5000 cases')
        if mode == 'compare' and any(not isinstance(r, dict) or r.get('human_reviewed') is not True
                or r.get('excluded') or r.get('needs_recheck') for r in cases):
            raise ValueError('Comparison requires undisputed human-reviewed ground truth')
        catalogs.append((path, data))
    if mode == 'compare' and len(catalogs) != 1:
        raise ValueError('Use exactly one frozen development catalog')
    return catalogs


def windows(width, height):
    # Two-thirds width/height: 33% overlap and 1.5x effective target scale.
    tw, th = (width * 2 + 2) // 3, (height * 2 + 2) // 3
    return [(0, 0, width, height)] + [
        (x, y, x + tw, y + th) for y in (0, height - th) for x in (0, width - tw)]


def merge_boxes(boxes, threshold):
    kept = []
    for box in sorted(boxes, key=lambda b: b[4], reverse=True):
        if box[4] >= threshold and all(overlap(box, prior) <= .499 for prior in kept):
            kept.append(box)
    return kept


def mining_signals(full, tiled, width, height, size):
    """Rank model disagreements as review leads, never as enemy truth."""
    a, b = merge_boxes(full, .1), merge_boxes(tiled, .1)
    tile_only = sum(not any(overlap(box, other) >= .5 for other in a) for box in b)
    full_only = sum(not any(overlap(box, other) >= .5 for other in b) for box in a)
    small = sum((box[2]-box[0])*size/max(width, height) < 24 for box in b)
    max_score = max((box[4] for box in tiled), default=0.)
    return {'tile_only_proposals': tile_only, 'full_only_proposals': full_only,
            'small_proposals': small, 'max_score': max_score,
            'priority': 3*min(tile_only+full_only, 9) + min(small, 9) + max_score}


class Predictor:
    def __init__(self, manifest, raw_model, checkpoint=None, device='cpu', settings=None):
        self.size = manifest['input_size']
        self.resize = manifest['preprocessing']
        self.device = device
        self.pt = None
        if self.resize != 'opencv-linear' or self.size not in (320, 416, 512, 640):
            raise ValueError('Explicit supported preprocessing contract required')
        if checkpoint:
            import torch
            torch.set_num_threads(1)
            self.pt = load_local_yolo(checkpoint, settings)
            self.pt.model.to(device).float().eval()
            if self.pt.names != {0: 'enemy'}:
                raise ValueError('Enemy checkpoint required')
            self.model_sha = _hash_file(checkpoint)
            self.backend = 'pytorch-' + device
        else:
            if device != 'cpu':
                raise ValueError('CUDA experiment requires local --checkpoint')
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = opts.inter_op_num_threads = 1
            self.session = ort.InferenceSession(str(raw_model), sess_options=opts,
                                               providers=['CPUExecutionProvider'])
            inp = self.session.get_inputs()
            metadata = self.session.get_modelmeta().custom_metadata_map
            if (len(inp) != 1 or inp[0].shape != [1, 3, self.size, self.size]
                    or inp[0].type != 'tensor(float)' or metadata.get('nolagin_postprocess')
                    or ast.literal_eval(metadata.get('names', '{}')) != {0: 'enemy'}
                    or ast.literal_eval(metadata.get('end2end', 'None')) is not manifest['export_end2end']):
                raise ValueError('Raw model head/input/class contract mismatch')
            self.input_name = inp[0].name
            self.model_sha = _hash_file(raw_model)
            self.backend = 'onnx-cpu'
        if self.pt and bool(self.pt.model.end2end) is not manifest['export_end2end']:
            raise ValueError('Checkpoint head differs from candidate')

    def __call__(self, frame):
        tensor, transform = preprocess(frame, self.size, self.resize)
        if self.pt:
            import torch
            from ultralytics.utils.nms import non_max_suppression
            with torch.inference_mode():
                result = self.pt.model(torch.from_numpy(tensor).to(self.device))
                if isinstance(result, (tuple, list)):
                    result = result[0]
                rows = (result[0] if self.pt.model.end2end else
                        non_max_suppression(result, conf_thres=.001, iou_thres=.499, nc=1, max_det=300)[0])
                raw = rows.cpu().numpy()
        else:
            raw = self.session.run(None, {self.input_name: tensor})[0]
        return [[float(v) for v in box] for box in diagnostic_boxes(
            pixel_rows(raw, self.size, 0), .001, transform, frame.shape[:2], self.size, 0)]


def predict_pair(frame, predictor, strategy='tiles'):
    start = perf_counter()
    full = predictor(frame)
    full_ms = (perf_counter() - start) * 1000
    tiled = list(full)
    if strategy == 'double-resolution':
        if getattr(predictor, 'pt', None) is None:
            raise ValueError('Double resolution requires a fully convolutional local checkpoint')
        original_size = predictor.size
        try:
            predictor.size = original_size * 2
            tiled.extend(predictor(frame))
        finally:
            predictor.size = original_size
        return full, merge_boxes(tiled, .001), full_ms, (perf_counter() - start) * 1000
    if strategy != 'tiles':
        raise ValueError('Unknown fixed challenger strategy')
    for x1, y1, x2, y2 in windows(frame.shape[1], frame.shape[0])[1:]:
        for box in predictor(frame[y1:y2, x1:x2]):
            tiled.append([box[0] + x1, box[1] + y1, box[2] + x1, box[3] + y1, box[4]])
    return full, merge_boxes(tiled, .001), full_ms, (perf_counter() - start) * 1000


def read_frame(path, row):
    image = _private_frame(path.parent, row.get('image'))
    with Image.open(image) as im:
        if not 16 <= im.width <= 7680 or not 16 <= im.height <= 4320:
            raise ValueError('Invalid image dimensions')
        frame = np.asarray(im.convert('RGB'))
    boxes = row.get('boxes')
    if not isinstance(boxes, list) or len(boxes) > 9:
        raise ValueError('Invalid boxes')
    for b in boxes:
        if (not isinstance(b, list) or len(b) != 4 or not all(type(v) in (int, float) and np.isfinite(v) for v in b)
                or not 0 <= b[0] < b[2] <= frame.shape[1] or not 0 <= b[1] < b[3] <= frame.shape[0]):
            raise ValueError('Invalid box geometry')
    return frame


def assess(baseline, challenger):
    """Development advancement only; independent certification is still required."""
    b, c = baseline['0.92'], challenger['0.92']
    improved = c['tp'] > b['tp'] and c['fp'] <= b['fp'] and c['negative_fp_frames'] <= b['negative_fp_frames']
    quality = (c['precision'] is not None and c['precision'] >= .98
               and c['recall'] is not None and c['recall'] >= .85
               and c['negative_frame_fpr'] is not None and c['negative_frame_fpr'] <= .01)
    return {'development_improvement': improved, 'development_quality_passed': quality,
            'advance_to_independent_certification': improved and quality,
            'activation_allowed': False, 'fit_allowed': False,
            'next_action': ('Independent certification required; no activation' if improved and quality else
                            'Collect and human-review diverse training-only failures; no automatic fit')}


def compare_reports(baseline, candidate):
    """Compare two frozen full-frame experiments; no threshold selection."""
    for report in (baseline, candidate):
        if (report.get('mode') != 'compare' or report.get('heldout_used') is not False
                or report.get('certification') is not False):
            raise ValueError('Only development comparison reports qualify')
    for key in ('cohort_sha256', 'frames', 'negative_frames', 'backend'):
        if baseline.get(key) != candidate.get(key):
            raise ValueError('Frozen cohort or inference backend differs')
    if baseline.get('protocol') != candidate.get('protocol'):
        raise ValueError('Frozen operating points or challenger protocol differs')
    return {'baseline_model_sha256': baseline['model_sha256'], 'candidate_model_sha256': candidate['model_sha256'],
            'cohort_sha256': baseline['cohort_sha256'], 'heldout_used': False, 'certification': False,
            'before': baseline['metrics']['full'], 'after': candidate['metrics']['full'],
            'decision': assess(baseline['metrics']['full'], candidate['metrics']['full'])}


def run(catalogs, predictor, mode, limit=40, strategy='tiles'):
    stats = {k: {str(t): counts() for t in THRESHOLDS} for k in ('full', 'tiled')}
    negatives = 0
    seen = set()
    details, queue, timings = [], [], []
    cohort = sha256()
    for path, data in catalogs:
        cohort.update(bytes.fromhex(_hash_file(path)))
        for row in data['cases']:
            if mode == 'curate' and (row.get('excluded') or row.get('needs_recheck')):
                continue
            frame = read_frame(path, row)
            pixel_hash = sha256(str(frame.shape).encode() + frame.tobytes()).hexdigest()
            if pixel_hash in seen:
                if mode == 'compare':
                    raise ValueError('Duplicate development pixels')
                continue
            seen.add(pixel_hash)
            cohort.update(bytes.fromhex(pixel_hash))
            full, tiled, full_ms, tiled_ms = predict_pair(frame, predictor, strategy)
            timings.append([full_ms, tiled_ms])
            reviewed = row.get('human_reviewed') is True
            truth = row['boxes'] if reviewed else None
            error = None
            if reviewed:
                negatives += int(not truth)
                for name, boxes in (('full', full), ('tiled', tiled)):
                    for t in THRESHOLDS:
                        add_counts(stats[name][str(t)], merge_boxes(boxes, t), truth)
                tp, fp, fn = match_frame(merge_boxes(tiled, .5), truth)
                error = {'tp': tp, 'fp': fp, 'fn': fn}
            details.append({'image': row['image'], 'source_clip_sha256': data['source_clip_sha256'],
                            'pixel_sha256': pixel_hash, 'truth': truth, 'full': full, 'tiled': tiled,
                            'errors_at_0.5': error})
            if mode == 'curate':
                reasons = []
                if not reviewed: reasons.append('unreviewed coverage')
                if error and error['fn']: reasons.append('missed reviewed enemy')
                if error and error['fp']: reasons.append('false detection against reviewed labels')
                if truth and len(truth) > 1: reasons.append('multiple enemies')
                if truth and any((b[2]-b[0])*predictor.size/max(frame.shape[:2]) < 24 for b in truth):
                    reasons.append('small target at full-frame input')
                if reasons:
                    signals = mining_signals(full, tiled, frame.shape[1], frame.shape[0], predictor.size)
                    if not reviewed and signals['tile_only_proposals'] + signals['full_only_proposals']:
                        reasons.append('full-frame/crop disagreement; unverified')
                    if not reviewed and signals['small_proposals']:
                        reasons.append('small model proposals; unverified')
                    queue.append({'source_catalog': str(path.resolve()), 'source_catalog_sha256': _hash_file(path),
                                  'source_clip_sha256': data['source_clip_sha256'], 'image': row['image'],
                                  'pixel_sha256': pixel_hash, 'clip_time_s': row.get('clip_time_s'), 'reasons': reasons,
                                  'mining_signals': signals,
                                  'priority': (error['fn']*3+error['fp'] if error else signals['priority']) + int('multiple enemies' in reasons),
                                  'human_reviewed': False, 'boxes': [], 'candidate_boxes': [b[:4] for b in merge_boxes(tiled, .1)[:9]],
                                  'original_human_labels_preserved': reviewed})
    if not details:
        raise ValueError('No eligible frames')
    # Queue is a sidecar, never an authoritative training catalog. Already
    # reviewed failures remain in the evidence, not in another owner review.
    queue.sort(key=lambda r: (-r['priority'], r['pixel_sha256']))
    groups = {data['source_clip_sha256']: [] for _, data in catalogs}
    for row in queue:
        if not row['original_human_labels_preserved']:
            groups[row['source_clip_sha256']].append(row)
    selected = []
    while any(groups.values()) and len(selected) < limit:
        for group in groups.values():
            if group and len(selected) < limit:
                candidate = group.pop(0)
                stamp = candidate['clip_time_s']
                if stamp is not None and (type(stamp) not in (int, float) or not np.isfinite(stamp) or stamp < 0):
                    raise ValueError('Invalid clip timestamp')
                if stamp is not None and any(r['source_clip_sha256'] == candidate['source_clip_sha256']
                        and r['clip_time_s'] is not None and abs(r['clip_time_s'] - stamp) < 8 for r in selected):
                    continue
                selected.append(candidate)
    metrics = {k: finish(v, negatives) for k, v in stats.items()}
    return {'mode': mode, 'diagnostic_only': True, 'certification': False, 'heldout_used': False,
            'training_steps': 0, 'human_labels_created': 0, 'activation_allowed': False,
            'backend': predictor.backend, 'model_sha256': predictor.model_sha,
            'cohort_sha256': cohort.hexdigest(), 'frames': len(details), 'negative_frames': negatives,
            'protocol': {'thresholds': THRESHOLDS, 'nms_iou': .499, 'match_iou': .5,
                         'challenger': ('full frame plus four overlapping 2/3 crops; global NMS' if strategy == 'tiles'
                                        else 'full frame plus double input resolution; global NMS'),
                         'strategy': strategy,
                         'live_cutoff': .92, 'baked_floor': .85},
            'metrics': metrics, 'decision': assess(metrics['full'], metrics['tiled']) if mode == 'compare' else None,
            'offline_latency_ms_p95': dict(zip(('full', 'tiled'), np.percentile(timings, 95, axis=0).tolist())),
            'latency_scope': 'offline inference including preprocessing/NMS; first-call warmup included; not game FPS',
            'review_queue': selected, 'eligible_review_cases': sum(not r['original_human_labels_preserved'] for r in queue),
            'reviewed_failure_evidence': sum(r['original_human_labels_preserved'] for r in queue), 'cases': details,
            'curation_warning': 'Sidecar proposals only. Never promote predictions to truth. Review all enemies/teammates and diverse fights before another fit.'}


def main():
    if len(sys.argv)>1 and sys.argv[1]=='decide':
        parser=argparse.ArgumentParser(description='Frozen before/after candidate decision; never activation')
        for name in ('baseline-report','candidate-report','report'):
            parser.add_argument('--'+name,type=Path,required=True)
        args=parser.parse_args(sys.argv[2:])
        result=compare_reports(json.loads(args.baseline_report.read_text(encoding='utf-8')),
                               json.loads(args.candidate_report.read_text(encoding='utf-8')))
        payload=json.dumps(result,indent=2,allow_nan=False)
        args.report.parent.mkdir(parents=True,exist_ok=True)
        with args.report.open('x',encoding='utf-8') as stream:stream.write(payload)
        print(payload)
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('compare', 'curate'))
    for name in ('manifest', 'dataset', 'report'):
        parser.add_argument('--' + name, type=Path, required=True, **({'action': 'append'} if name == 'dataset' else {}))
    parser.add_argument('--heldout-source-sha256', required=True, help='Frozen exclusion fingerprint only; never open heldout media')
    parser.add_argument('--raw-model', type=Path)
    parser.add_argument('--checkpoint', type=Path)
    parser.add_argument('--device', choices=('cpu', 'cuda:0'), default='cpu')
    parser.add_argument('--limit', type=int, default=40)
    parser.add_argument('--strategy', choices=('tiles', 'double-resolution'), default='tiles')
    args = parser.parse_args()
    if args.report.exists(): parser.error('Choose a fresh report; preserve previous results')
    if not 1 <= args.limit <= 500: parser.error('Review limit must be 1..500')
    if not args.raw_model and not args.checkpoint: parser.error('Local raw export or checkpoint required')
    if args.strategy == 'double-resolution' and not args.checkpoint:
        parser.error('Double resolution requires --checkpoint; static ONNX stays unchanged')
    from scripts.nolagin import _game_running
    if _game_running('apex'): parser.error('Close Apex before offline experiments')
    meta = json.loads(args.manifest.read_text(encoding='utf-8'))
    catalogs = guard_catalogs(meta, args.dataset, args.heldout_source_sha256, args.mode)
    protected = [args.manifest, *args.dataset]
    if args.raw_model: protected.append(args.raw_model)
    if args.checkpoint: protected.append(args.checkpoint)
    before = {str(p): _hash_file(p) for p in protected}
    predictor = Predictor(meta, args.raw_model, args.checkpoint, args.device, args.report.parent/'local-settings')
    result = run(catalogs, predictor, args.mode, args.limit, args.strategy)
    result['heldout_exclusion_sha256'] = args.heldout_source_sha256
    if any(_hash_file(Path(p)) != digest for p, digest in before.items()):
        raise RuntimeError('Protected input changed during run; report refused')
    result['protected_inputs_sha256'] = before
    args.report.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result, indent=2, allow_nan=False)
    with args.report.open('x', encoding='utf-8') as stream:
        stream.write(payload)
    print(json.dumps({k: v for k, v in result.items() if k not in ('cases', 'review_queue', 'protected_inputs_sha256')}, indent=2))


if __name__ == '__main__':
    main()
