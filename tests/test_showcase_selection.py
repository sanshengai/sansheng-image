"""Curated customer displays reject unsuitable or changed inputs before writing."""
import copy
import hashlib
import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from stylebook import build as BD, motion_gallery as MG


def picture(size):
    buf = io.BytesIO()
    Image.new('RGB', size, '#a4b8ae').save(buf, 'PNG')
    return buf.getvalue()


def gallery(tmp_path):
    root = tmp_path / 'gallery'
    root.mkdir()
    for name in ('motion.js', 'motion.css'):
        (root / name).write_text('/* test */')
    item = {'id': 'photo', 'title': '完整合影', 'scene': 'brand', 'scene_label': '合影',
            'width': 1280, 'height': 720, 'duration': 8, 'category': 'portrait',
            'source_label': '原图 · 9:16'}
    media = tmp_path / 'media' / 'photo'
    media.mkdir(parents=True)
    for role, name, raw in [('source', 'source.png', picture((90, 160))),
                            ('poster', 'poster.png', picture((160, 90))),
                            ('video', 'share.mp4', b'fixture video')]:
        (media / name).write_bytes(raw)
        item[role] = '/tools/image/motion/photo/' + name
        item[role + '_sha256'] = hashlib.sha256(raw).hexdigest()
    doc = {'schema_version': 1, 'showcase': 'portrait-to-landscape',
           'categories': [{'id': 'portrait', 'label': '人像合影', 'description': '完整照片中的一段回忆。'}],
           'default_category': 'portrait', 'items': [item]}
    return root, media.parent, doc


@pytest.mark.parametrize('role,size', [('source', (160, 90)), ('poster', (90, 160))])
def test_real_wrong_ratio_rejected_even_with_matching_hash_and_label(tmp_path, role, size):
    root, media, doc = gallery(tmp_path)
    (root / 'motion-data.json').write_text(json.dumps(doc))
    MG.prepare(media, tmp_path / 'out', root)  # valid input really passes
    name = 'source.png' if role == 'source' else 'poster.png'
    raw = picture(size)
    (media / 'photo' / name).write_bytes(raw)
    doc['items'][0][role + '_sha256'] = hashlib.sha256(raw).hexdigest()
    (root / 'motion-data.json').write_text(json.dumps(doc))
    with pytest.raises(ValueError, match='实际比例'):
        MG.prepare(media, tmp_path / 'out', root)
    assert not (tmp_path / 'out').exists()
    # This is a customer-display contract, not a ban on landscape animation inputs.
    del doc['showcase']
    (root / 'motion-data.json').write_text(json.dumps(doc))
    MG.prepare(media, tmp_path / 'out', root)


@pytest.mark.parametrize('mutate', [
    lambda d: d.update(showcase='other'),
    lambda d: d.update(items=[]),
    lambda d: d['items'][0].pop('source'),
    lambda d: d['items'][0].update(width=720, height=1280),
    lambda d: d['categories'].append({'id': 'empty', 'label': '空分类', 'description': '没有作品。'}),
    lambda d: d['items'].extend([dict(d['items'][0], id='second'), dict(d['items'][0], id='third')]),
])
def test_card_profile_rejects_missing_or_excess_work(tmp_path, mutate):
    _, _, doc = gallery(tmp_path)
    assert not MG.validate_document(doc)
    mutate(doc)
    assert MG.validate_document(doc)


def test_three_otherwise_valid_works_reject_the_cardinality_rule(tmp_path):
    _, _, doc = gallery(tmp_path)
    for iid in ('second', 'third'):
        item = copy.deepcopy(doc['items'][0])
        item['id'] = iid
        for role in ('source', 'poster', 'video'):
            item[role] = item[role].replace('/photo/', '/' + iid + '/')
        doc['items'].append(item)
    assert any('须有 1..2' in problem for problem in MG.validate_document(doc))
    del doc['showcase']
    assert not MG.validate_document(doc)


@pytest.mark.parametrize('mutate', [
    lambda d: d.update(cards={}),
    lambda d: d.update(schema_version=True),
    lambda d: d['cards']['qinglv'].clear(),
    lambda d: d['cards']['qinglv'][0].update(subject='p'),
    lambda d: d['cards']['qinglv'][0].update(sha256='unknown'),
    lambda d: d.update(comparisons=[]),
    lambda d: d.update(comparisons=[{}]),
])
def test_palette_selection_is_explicit_and_nonportrait(tmp_path, mutate):
    doc = copy.deepcopy(BD.palette_showcase())
    mutate(doc)
    source = tmp_path / 'selection.json'
    source.write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        BD.palette_showcase(source)


def test_changed_reviewed_image_fails_real_picker_before_writes(tmp_path, monkeypatch):
    policy = copy.deepcopy(BD.palette_showcase())
    row = next(iter(next(iter(policy['cards'].values()))))
    # Supply changed pixels under the real selected contract, even in a public
    # installation without bundled sample PNGs. The reviewed SHA stays unchanged.
    changed = tmp_path / 'changed.png'
    changed.write_bytes(picture((90, 160)))
    original = BD._sample_files
    def changed_files(code, contract):
        files = original(code, contract)
        if code == row['code']:
            assert row['kind'] in files
            files[row['kind']] = (changed, None)
        return files
    monkeypatch.setattr(BD, '_sample_files', changed_files)
    out = tmp_path / 'out'
    with pytest.raises(ValueError, match='已审选片不符'):
        BD.picker(out)
    assert not out.exists()


def test_selected_contract_cannot_disappear_silently(tmp_path, monkeypatch):
    policy = copy.deepcopy(BD.palette_showcase())
    next(iter(policy['cards'].values()))[0]['code'] = 'C00'
    monkeypatch.setattr(BD, 'palette_showcase', lambda: policy)
    out = tmp_path / 'out'
    with pytest.raises(ValueError, match='已审色卡缺少合同样图'):
        BD.picker(out)
    assert not out.exists()
