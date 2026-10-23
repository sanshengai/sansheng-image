"""Prompt context tests only: no model calls and no claims about image pixels."""
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from stylebook.qa import plan_review as PR


def fixture(tmp_path):
    article_root = tmp_path / 'article'
    media = article_root / '素材'
    media.mkdir(parents=True)
    snapshot = media / 'snapshots' / 'frozen.md'
    snapshot.parent.mkdir()
    lines = ['# 一篇真实经历文章']
    for index in range(8):
        path = media / f'photo-{index}.png'
        path.write_bytes(b'file bytes; not a decoded image ' + bytes([index]))
        lines.append(f'![原文图注{index}](素材/photo-{index}.png)')
    snapshot.write_text('\n\n'.join(lines), encoding='utf-8')
    plan = {'items': [], 'coverage': [], 'source': {'asset_base': str(article_root)},
            'editorial_constraints': {'author_words': '正文保留真实截图，不用生成图替换。'}}
    return plan, snapshot, media


@pytest.mark.parametrize('mode', ['inline', 'read_tool'])
def test_prompt_preserves_constraints_and_eight_existing_refs_with_zero_new_items(tmp_path, mode):
    plan, article, _ = fixture(tmp_path)
    text = PR.prompt(plan, article, mode)
    assert plan['editorial_constraints']['author_words'] in text
    assert '"reference_count": 8' in text
    for index in range(8):
        assert f'原文图注{index}' in text and f'素材/photo-{index}.png' in text
    assert 'items=[] 意味着不新增生成图，不等于全文没有图片' in text
    assert '没有看过像素时不得声称已经看图' in text
    assert '可选美化放 notes，不能冒充 missed_positions' in text
    assert '仍可指出确有独立阅读增益的必要漏项' in text
    assert all(not row['pixel_reviewed'] for row in PR.existing_visual_context(plan, article)['references'])


def test_missing_empty_remote_and_escaping_paths_never_claim_available(tmp_path):
    plan, article, media = fixture(tmp_path)
    (media / 'photo-0.png').unlink()
    (media / 'photo-1.png').write_bytes(b'')
    article.write_text(article.read_text() + '\n![远程](https://example.com/p.png)\n![越界](../outside.png)')
    context = PR.existing_visual_context(plan, article)
    rows = context['references']
    assert rows[0]['status'] == 'missing_or_unreadable'
    assert rows[1]['status'] == 'empty_file'
    assert rows[-2]['status'] == 'remote_or_scheme_unverified'
    assert rows[-1]['status'] == 'outside_asset_base'
    assert rows[2]['status'] == 'local_bytes_verified'
    assert rows[2]['sha256'] == hashlib.sha256((media / 'photo-2.png').read_bytes()).hexdigest()


def test_symlink_escape_not_counted_as_local_verified_bytes(tmp_path):
    plan, article, media = fixture(tmp_path)
    outside = tmp_path / 'outside.png'
    outside.write_bytes(b'outside')
    (media / 'photo-0.png').unlink()
    (media / 'photo-0.png').symlink_to(outside)
    assert PR.existing_visual_context(plan, article)['references'][0]['status'] == 'outside_asset_base'


def test_no_inline_images_distinct_from_unreadable_source(tmp_path):
    article = tmp_path / 'article.md'
    plan = {'items': []}
    missing = PR.existing_visual_context(plan, article)
    assert missing['source_status'] == 'unreadable' and missing['reference_count'] is None
    article.write_text('没有 Markdown 内联图片的正文')
    readable = PR.existing_visual_context(plan, article)
    assert readable['source_status'] == 'readable' and readable['reference_count'] == 0


def test_unresolved_snapshot_paths_do_not_fake_existing_files(tmp_path):
    plan, article, _ = fixture(tmp_path)
    del plan['source']['asset_base']
    context = PR.existing_visual_context(plan, article)
    assert all(row['status'] == 'missing_or_unreadable' for row in context['references'])
