"""Category metadata follows the same fail-closed build path as media."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from stylebook import motion_gallery as MG


def document():
    return {
        'schema_version': 1,
        'categories': [{'id': 'article', 'label': '文章演绎', 'description': '解释一段上下文。'},
                       {'id': 'light', 'label': '光影意境', 'description': '环境中的光线变化。'}],
        'default_category': 'article',
        'items': [{'id': 'one', 'title': '任务选择', 'scene': 'article', 'scene_label': '正文插图',
                   'width': 1280, 'height': 720, 'duration': 14, 'category': 'article',
                   'tags': ['对比与选择'], 'source_label': '原静态图 · 16:9',
                   'context': {'title': '文章标题', 'excerpt': '原文段落。', 'purpose': '三种替代选择。'},
                   'poster': '/tools/image/motion/one/poster.png', 'poster_sha256': 'a'*64,
                   'video': '/tools/image/motion/one/share.mp4', 'video_sha256': 'b'*64}]
    }


def test_categories_are_optional_but_cannot_be_empty_when_provided():
    doc = document()
    assert MG.validate_document(doc) == []
    old = copy.deepcopy(doc)
    del old['categories'], old['default_category'], old['items'][0]['category']
    assert MG.validate_document(old) == []
    for value in (None, [], {}, '', [None]):
        bad = copy.deepcopy(doc)
        bad['categories'] = value
        assert MG.validate_document(bad)


@pytest.mark.parametrize('mutation', [
    lambda d: d.pop('default_category'),
    lambda d: d.update(default_category='missing'),
    lambda d: d.update(default_category='light'),
    lambda d: d['categories'].append(copy.deepcopy(d['categories'][0])),
    lambda d: d['categories'][0].update(id='all'),
    lambda d: d['categories'][0].update(label=' '),
    lambda d: d['categories'][0].update(extra='unknown'),
    lambda d: d['items'][0].pop('category'),
    lambda d: d['items'][0].update(category='missing'),
    lambda d: d['items'][0].update(category=[]),
    lambda d: d['items'][0].update(tags=[]),
    lambda d: d['items'][0].update(tags=['过程', '过程']),
    lambda d: d['items'][0].update(tags=[{}]),
    lambda d: d['items'][0].update(context={}),
    lambda d: d['items'][0]['context'].update(excerpt=' '),
    lambda d: d['items'][0].update(source_label=' '),
])
def test_malformed_metadata_is_rejected(mutation):
    doc = document()
    mutation(doc)
    assert MG.validate_document(doc)
    with pytest.raises(ValueError):
        MG.parse_text(json.dumps(doc))


def test_changed_reference_fails_real_build_preflight_before_output(tmp_path):
    gallery = tmp_path/'gallery'
    gallery.mkdir()
    (gallery/'motion.js').write_text('// source')
    (gallery/'motion.css').write_text('/* source */')
    doc = document()
    (gallery/'motion-data.json').write_text(json.dumps(doc))
    out = tmp_path/'out'
    MG.prepare(None, out, gallery)  # the same document was valid before mutation
    doc['items'][0]['category'] = 'retired-category'
    (gallery/'motion-data.json').write_text(json.dumps(doc))
    with pytest.raises(ValueError, match='category'):
        MG.prepare(None, out, gallery)
    assert not out.exists()
