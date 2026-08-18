"""What the reader is shown, and what the agent is allowed to quote.

The sanitising tests are the important ones. Only staff can author an article,
which is exactly the assumption that makes stored XSS survive review, so the
refusal is asserted rather than trusted.
"""

from __future__ import annotations

from apps.content.rendering import MAX_CHUNK_CHARS, render_markdown, split_into_chunks


def test_markdown_becomes_html() -> None:
    html = render_markdown("## 政府费用\n\n注册费是**固定**的。\n")
    assert "<h2>政府费用</h2>" in html
    assert "<strong>固定</strong>" in html


def test_a_script_an_editor_pasted_never_reaches_the_page() -> None:
    html = render_markdown('正文<script>alert(1)</script>与<img src="x" onerror="alert(1)">')
    assert "<script" not in html
    assert "onerror" not in html
    assert "<img" not in html
    assert "正文" in html


def test_an_external_iframe_is_stripped_but_its_text_survives() -> None:
    html = render_markdown('<iframe src="https://example.com"></iframe>剩下的正文')
    assert "<iframe" not in html
    assert "剩下的正文" in html


def test_links_keep_href_and_lose_everything_else() -> None:
    html = render_markdown('<a href="/guides/" onclick="steal()" target="_blank">目录</a>')
    assert 'href="/guides/"' in html
    assert "onclick" not in html


def test_headings_are_carried_onto_the_passages_under_them() -> None:
    chunks = split_into_chunks("## 政府费用\n\n第一段。\n\n## 服务费\n\n第二段。\n")
    assert [heading for heading, _ in chunks] == ["政府费用", "服务费"]
    assert [text for _, text in chunks] == ["第一段。", "第二段。"]


def test_a_heading_alone_is_not_a_citable_passage() -> None:
    assert split_into_chunks("## 只有标题\n") == []


def test_short_paragraphs_share_a_passage_until_the_ceiling() -> None:
    body = "\n\n".join(["段落。"] * 3)
    chunks = split_into_chunks(body)
    assert len(chunks) == 1
    assert chunks[0][1].count("段落。") == 3


def test_a_long_paragraph_is_never_cut_in_the_middle_of_a_sentence() -> None:
    long_paragraph = "费" * (MAX_CHUNK_CHARS + 200)
    chunks = split_into_chunks(long_paragraph)
    assert [text for _, text in chunks] == [long_paragraph]
