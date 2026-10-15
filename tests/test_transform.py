from pathlib import Path

import pytest

from htmlreview.transform import decode, encode, inject, scan, strip_eids, tag

EXAMPLES = sorted(
    p for p in (Path(__file__).parent.parent / "examples").rglob("report.htm*")
)


def eids(html: str) -> dict[str, str]:
    return tag(html).editable


# -- round trips -------------------------------------------------------------


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.parent.name)
def test_tag_then_strip_is_byte_identical(path):
    data = path.read_bytes()
    text, enc = decode(data)
    tagged = tag(text)
    assert tagged.editable, "examples should have editable elements"
    assert encode(strip_eids(tagged.html), enc) == data


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.parent.name)
def test_scripts_and_styles_untouched(path):
    text, _ = decode(path.read_bytes())
    tagged = tag(text).html

    def blocks(html):
        return [
            html[el.start : el.end]
            for el in scan(html)
            if el.tag in ("script", "style") and el.end is not None
        ]

    assert blocks(tagged) == blocks(text)


def test_tagging_is_deterministic_and_idempotent():
    src = "<h1>A</h1><p>B</p><ul><li>C</li></ul>"
    first = tag(src)
    assert first.html == tag(src).html
    assert tag(first.html).html == first.html
    assert list(first.editable) == ["e1", "e2", "e3"]


def test_decode_encode_handles_undecodable_bytes():
    data = b"<p>caf\xe9</p>"  # latin-1 byte in a file with no charset
    text, enc = decode(data)
    assert enc == "utf-8"
    assert encode(text, enc) == data


def test_meta_charset_detection():
    data = '<meta charset="windows-1252"><p>café</p>'.encode("cp1252")
    text, enc = decode(data)
    assert "café" in text
    assert enc == "cp1252"


# -- what gets tagged -------------------------------------------------------


def test_inline_markup_inside_unit_and_outermost_wins():
    src = "<p>Hello <b>bold</b> <span>inner</span></p><div><span>alone</span></div>"
    tagged = tag(src).html
    assert '<p data-eid="e1">Hello <b>bold</b> <span>inner</span></p>' in tagged
    assert '<span data-eid="e2">alone</span>' in tagged
    assert tagged.count("data-eid") == 2


def test_containers_with_blocks_are_not_units_but_children_are():
    src = (
        "<blockquote><p>q1</p><p>q2</p></blockquote>"
        "<ul><li>outer<ul><li>inner</li></ul></li></ul>"
        "<table><tr><td>cell</td><td><div>block</div></td></tr></table>"
    )
    assert list(eids(src).values()) == ["q1", "q2", "inner", "cell"]


def test_excluded_regions_are_never_tagged():
    src = (
        "<html><head><title>T</title></head><body>"
        "<svg><text>label</text><foreignObject><p>in svg</p></foreignObject></svg>"
        "<template><p>tpl</p></template><noscript><p>ns</p></noscript>"
        "<button><span>btn</span></button><canvas><p>fallback</p></canvas>"
        "<p>real</p></body></html>"
    )
    assert list(eids(src).values()) == ["real"]


def test_script_contents_that_look_like_html_are_ignored():
    src = '<script>var s = "<p>not real</p>";</script><p>real</p>'
    tagged = tag(src)
    assert list(tagged.editable.values()) == ["real"]
    assert '<script>var s = "<p>not real</p>";</script>' in tagged.html


def test_empty_and_implicitly_closed_elements_are_skipped():
    src = "<span id=kpi></span><p>   </p><p>open<p>closed</p><ul><li>a<li>b</li></ul>"
    assert list(eids(src).values()) == ["closed", "b"]


def test_text_is_normalized_and_entities_decoded():
    assert list(eids("<p>  a&nbsp;&amp;\n  <i>b</i>  </p>").values()) == ["a & b"]


# -- existing eids (working copies) ----------------------------------------


def test_existing_eids_are_kept_and_new_ones_continue_after_max():
    src = '<p data-eid="e7">old</p><p>new</p><p data-eid="e3">older</p>'
    tagged = tag(src)
    assert tagged.editable == {"e7": "old", "e8": "new", "e3": "older"}


def test_duplicate_eid_gets_a_fresh_number():
    src = '<p data-eid="e1">a</p><p data-eid="e1">copy</p>'
    tagged = tag(src)
    assert '<p data-eid="e2">copy</p>' in tagged.html
    assert tagged.editable == {"e1": "a", "e2": "copy"}


def test_strip_only_touches_real_start_tags():
    src = '<p data-eid="e1" class="x">a</p><script>"<p data-eid=1>"</script>'
    assert strip_eids(src) == '<p class="x">a</p><script>"<p data-eid=1>"</script>'


def test_uppercase_tags_and_attributes_kept_verbatim():
    src = "<P CLASS=lead>Text</P>"
    assert tag(src).html == '<P data-eid="e1" CLASS=lead>Text</P>'


# -- inject -----------------------------------------------------------------


def test_inject_goes_before_head_end_and_escapes_config():
    src = "<!doctype html><html><head><title>x</title></head><body><p>a</p></body></html>"
    out = inject(src, {"s": "</script><b>"}, "/__hr/editor.js")
    head_end = out.index("</head>")
    assert out.index('id="__hr-config"') < head_end
    assert out.index("/__hr/editor.js") < head_end
    assert "</script><b>" not in out
    assert out.replace(out[out.index("<script type=") : head_end], "") == src


@pytest.mark.parametrize(
    "src, before",
    [
        ("<!doctype html><body><p>a</p></body>", "<p>"),
        ("<!DOCTYPE html>\n<meta charset=utf-8><p>a", "\n<meta"),
        ("<p>no doctype</p>", "<p>"),
    ],
)
def test_inject_without_head(src, before):
    out = inject(src, {}, "/e.js")
    assert out.index("/e.js") < out.index(before)
    assert out.index("/e.js") > (out.lower().find("<!doctype") if "<!" in src else -1)
