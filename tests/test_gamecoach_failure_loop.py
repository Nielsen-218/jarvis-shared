"""Failure-loop safety and geometry; synthetic tests never certify accuracy."""
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from scripts.gamecoach_failure_loop import guard_catalogs, windows, merge_boxes, predict_pair, run, assess, mining_signals, compare_reports


def meta():
    return {'game': 'apex', 'training_clip_sha256s': ['a'*64], 'development_validation_clip_sha256': 'b'*64,
            'training_source_clip_sha256': ['a'*64, 'b'*64]}


def catalog(tmp_path, split='val', source='b', reviewed=True):
    rows = []
    for i in range(4):
        name = f'{i}.png'
        Image.new('RGB', (100, 60), (i, 20, 30)).save(tmp_path/name)
        rows.append({'image': name, 'boxes': [[10, 10, 20, 30]] if i == 0 else [],
                     'human_reviewed': reviewed, 'clip_time_s': i*4})
    path = tmp_path/'dataset.json'
    path.write_text(json.dumps({'game': 'apex', 'split': split, 'source_clip_sha256': source*64, 'cases': rows}))
    return path


@pytest.mark.parametrize('split,source,mode', [
    ('heldout', 'c', 'compare'), ('val', 'c', 'compare'), ('train', 'c', 'curate'),
    ('val', 'b', 'curate'), ('train', 'a', 'compare'),
])
def test_source_guards_before_any_image_or_model_access(tmp_path, split, source, mode):
    path = catalog(tmp_path, split, source)
    with pytest.raises(ValueError, match='source or split'):
        guard_catalogs(meta(), [path], 'c'*64, mode)


@pytest.mark.parametrize('heldout', ['a'*64, 'b'*64, 'z'*64, ''])
def test_explicit_heldout_exclusion(tmp_path, heldout):
    with pytest.raises(ValueError, match='provenance'):
        guard_catalogs(meta(), [catalog(tmp_path)], heldout, 'compare')


def test_unreviewed_development_rejected(tmp_path):
    with pytest.raises(ValueError, match='human-reviewed'):
        guard_catalogs(meta(), [catalog(tmp_path, reviewed=False)], 'c'*64, 'compare')


def test_tile_offsets_and_global_duplicate_suppression():
    image = np.zeros((60, 100, 3), dtype=np.uint8)
    calls = []
    def predictor(frame):
        calls.append(frame.shape)
        return [[1., 2., 11., 12., .9]]
    full, tiles, _, _ = predict_pair(image, predictor)
    assert len(calls) == 5
    assert [34., 22., 44., 32., .9] in tiles
    assert full == [[1., 2., 11., 12., .9]]
    assert len(merge_boxes(tiles + full, .85)) == len(tiles)
    assert windows(100, 60)[-1] == (33, 20, 100, 60)


class FakePredictor:
    size = 640
    backend = 'test'
    model_sha = 'd'*64
    def __call__(self, frame):
        return [[10., 10., 20., 30., .9]]


def test_review_sidecar_does_not_relabel_or_repeat_reviewed_work(tmp_path):
    path = catalog(tmp_path, 'train', 'a')
    original = path.read_bytes()
    result = run(guard_catalogs(meta(), [path], 'c'*64, 'curate'), FakePredictor(), 'curate')
    assert result['reviewed_failure_evidence'] > 0
    assert result['review_queue'] == []
    assert result['human_labels_created'] == 0
    assert path.read_bytes() == original


def test_unreviewed_predictions_never_become_ground_truth_and_temporal_diversity(tmp_path):
    path = catalog(tmp_path, 'train', 'a', False)
    original = path.read_bytes()
    result = run(guard_catalogs(meta(), [path], 'c'*64, 'curate'), FakePredictor(), 'curate')
    assert len(result['review_queue']) == 2  # 8-second separation
    assert all(r['human_reviewed'] is False and r['boxes'] == [] for r in result['review_queue'])
    assert all(r['truth'] is None for r in result['cases'])
    assert path.read_bytes() == original
    json.dumps(result, allow_nan=False)


def test_diagnostic_gain_cannot_enable_activation_or_hide_live_regression():
    base = {'tp': 0, 'fp': 0, 'negative_fp_frames': 0, 'precision': None, 'recall': 0., 'negative_frame_fpr': 0.}
    better = {**base, 'tp': 6, 'precision': 1., 'recall': 1.}
    decision = assess({'0.92': base}, {'0.92': better})
    assert decision['advance_to_independent_certification']
    assert not decision['activation_allowed'] and not decision['fit_allowed']
    worse = {**better, 'fp': 1, 'negative_fp_frames': 1, 'precision': .99}
    assert not assess({'0.92': base}, {'0.92': worse})['development_improvement']


def test_duplicate_development_pixels_rejected(tmp_path):
    path = catalog(tmp_path)
    (tmp_path/'1.png').write_bytes((tmp_path/'0.png').read_bytes())
    with pytest.raises(ValueError, match='Duplicate development pixels'):
        run(guard_catalogs(meta(), [path], 'c'*64, 'compare'), FakePredictor(), 'compare')


def test_bad_labels_rejected_before_inference(tmp_path):
    path = catalog(tmp_path)
    data = json.loads(path.read_text())
    data['cases'][0]['boxes'] = [[0, 0, 1000, 10]]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='geometry'):
        run(guard_catalogs(meta(), [path], 'c'*64, 'compare'), FakePredictor(), 'compare')


def test_unreviewed_ranking_uses_disagreement_not_hash_randomness():
    empty = mining_signals([], [], 1920, 1080, 640)
    same = mining_signals([[0, 0, 30, 40, .6]], [[0, 0, 30, 40, .6]], 1920, 1080, 640)
    missed = mining_signals([], [[0, 0, 30, 40, .6]], 1920, 1080, 640)
    assert missed['priority'] > same['priority'] > empty['priority']
    assert missed['small_proposals'] == 1


def test_manifest_game_must_match(tmp_path):
    with pytest.raises(ValueError, match='provenance'):
        guard_catalogs({**meta(), 'game': 'cod'}, [catalog(tmp_path)], 'c'*64, 'compare')


def test_double_resolution_is_offline_and_restores_predictor_size():
    class Flexible(FakePredictor):
        pt = True
        sizes = []
        def __call__(self, frame):
            self.sizes.append(self.size)
            return super().__call__(frame)
    predictor = Flexible()
    predict_pair(np.zeros((60, 100, 3), dtype=np.uint8), predictor, 'double-resolution')
    assert predictor.sizes == [640, 1280]
    assert predictor.size == 640
    with pytest.raises(ValueError, match='checkpoint'):
        predict_pair(np.zeros((60, 100, 3), dtype=np.uint8), FakePredictor(), 'double-resolution')


def test_candidate_comparison_rejects_changed_cohorts_and_threshold_selection(tmp_path):
    path=catalog(tmp_path)
    result=run(guard_catalogs(meta(),[path],'c'*64,'compare'),FakePredictor(),'compare')
    assert not compare_reports(result,result)['decision']['activation_allowed']
    for key,value in [('cohort_sha256','bad'),('backend','other'),('heldout_used',True),('protocol',{})]:
        with pytest.raises(ValueError):compare_reports(result,{**result,key:value})
