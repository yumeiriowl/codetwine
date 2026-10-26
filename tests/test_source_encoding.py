"""Source files that are not UTF-8 are decoded and analysed like UTF-8 ones.

read_source() picks the codec from a BOM, UTF-8, SOURCE_ENCODING, detection, and last
UTF-8 with replacement. parse_file() parses the decoded text as UTF-8, so every
extractor sees UTF-8. The copy in the output directory keeps the original bytes.
"""

import codecs
import json
import logging
import os
import subprocess
import sys

import pytest

from codetwine import doc_creator, pipeline
from codetwine.config.settings import EXT_TO_DEFINITION_DICT
from codetwine.extractors.definitions import extract_definitions
from codetwine.parsers import ts_parser
from codetwine.utils import file_utils
from codetwine.utils.file_utils import (
    check_source_encoding,
    detected_encoding,
    is_text_file,
    read_source,
    read_source_text,
    resolve_file_output_dir,
)

# A C file whose Shift_JIS comment holds 表 (0x95 0x5C): its second byte is "\"
_CALC_C = ('#include "calc.h"\n// 一覧表\nint twice(int x) { return x * 2; }\n\n'
           'const char *label(void) { return "機能"; }\nint after(int x) { return x + 1; }\n')

_JAPANESE_LINE_TUPLE = (
    "設定ファイルを読み込み、各項目の値を検証してから辞書として返す。",
    "入力が空の場合は既定値を使い、警告をログに出力する。",
    "ファイルが存在しないときは例外を送出せず、空の辞書を返す。",
    "同じキーが二回現れた場合は、後から現れた値で上書きする。",
)


class RecordLLMClient:
    """Stand-in for LLMClient that keeps every prompt and answers with fixed text."""

    def __init__(self) -> None:
        self.prompt_list: list[str] = []

    async def generate(self, prompt: str, max_tokens: int | None = None) -> str:
        self.prompt_list.append(prompt)
        return "OK"


class NoMatch:
    """Stand-in for a charset-normalizer result that found no encoding."""

    def best(self) -> None:
        return None


class DetectAs:
    """Stand-in for a charset-normalizer result that names an encoding and decodes as cp932."""

    def __init__(self, file_content: bytes, encoding: str) -> None:
        self.encoding = encoding
        self._text = file_content.decode("cp932")

    def best(self) -> "DetectAs":
        return self

    def __str__(self) -> str:
        return self._text


@pytest.fixture
def undetectable(monkeypatch):
    """Leave SOURCE_ENCODING empty and make detection find no encoding."""
    monkeypatch.setattr(file_utils, "SOURCE_ENCODING", [])
    monkeypatch.setattr(file_utils, "from_bytes", lambda file_content: NoMatch())


def _set_run(monkeypatch, with_doc: bool) -> None:
    """Set the pipeline to write JSON and read Shift_JIS files as cp932.

    Args:
        monkeypatch: The test's monkeypatch fixture.
        with_doc: Whether design documents are generated.
    """
    monkeypatch.setattr(pipeline, "ENABLE_LLM_DOC", with_doc)
    monkeypatch.setattr(pipeline, "KNOWLEDGE_FORMAT", "json")
    monkeypatch.setattr(file_utils, "SOURCE_ENCODING", ["cp932"])


def _write(path, text: str, encoding: str) -> None:
    """Write text to path in the given encoding."""
    path.write_bytes(text.encode(encoding))


def _long_japanese_source() -> str:
    """Return a Python source with many Japanese comments."""
    line_list = []
    for i in range(60):
        line_list.append(f"# {_JAPANESE_LINE_TUPLE[i % len(_JAPANESE_LINE_TUPLE)]}")
        line_list.append(f"def func_{i}(value):\n    return value + {i}\n")
    return "\n".join(line_list)


# == read_source ============================================================

@pytest.mark.parametrize("file_encoding, expect_encoding", [
    ("utf-8", "utf-8"),
    ("utf-8-sig", "utf-8-sig"),
    ("utf-16", "utf-16"),
    ("utf-32", "utf-32"),
])
def test_utf_files_are_decoded_and_the_bom_is_dropped(tmp_path, file_encoding, expect_encoding):
    path = tmp_path / "a.py"
    _write(path, "def f():\n    return 'é'\n", file_encoding)

    text, encoding = read_source(str(path))

    assert text == "def f():\n    return 'é'\n"
    assert encoding == expect_encoding


def test_the_configured_encodings_are_tried_in_order(tmp_path, monkeypatch):
    path = tmp_path / "a.py"
    _write(path, "def add(a, b):\n    # 足し算\n    return a + b\n", "cp932")
    monkeypatch.setattr(file_utils, "SOURCE_ENCODING", ["ascii", "cp932", "latin-1"])

    text, encoding = read_source(str(path))

    assert encoding == "cp932"
    assert "足し算" in text


def test_a_file_its_bom_codec_does_not_decode_is_read_without_the_bom(tmp_path, monkeypatch):
    path = tmp_path / "a.py"
    path.write_bytes(b"\xef\xbb\xbf" + "class Foo:\n    pass\n# 日本語\n".encode("cp932"))
    monkeypatch.setattr(file_utils, "SOURCE_ENCODING", ["cp932"])

    text, encoding = read_source(str(path))

    assert (text, encoding) == ("class Foo:\n    pass\n# 日本語\n", "cp932")
    assert detected_encoding(str(path)) is None


@pytest.mark.parametrize("bom, body_encoding, tail, expect_text, expect_encoding", [
    (codecs.BOM_UTF16_LE, "utf-16-le", b"\n", "x = '日本'\n\ufffd", "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16-be", b"\n", "x = '日本'\n\ufffd", "utf-16"),
    (codecs.BOM_UTF16_LE, "utf-16-le", b"\x00\xd8", "x = '日本'\n\ufffd", "utf-16"),
    (codecs.BOM_UTF32_LE, "utf-32-le", b"\x00\x00\x11\x00", "x = '日本'\n\ufffd", "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32-be", b"\x00\x00", "x = '日本'\n\ufffd", "utf-32"),
])
def test_a_broken_utf16_or_utf32_file_is_read_by_its_bom_codec_with_replacement(
        tmp_path, monkeypatch, bom, body_encoding, tail, expect_text, expect_encoding):
    path = tmp_path / "a.py"
    path.write_bytes(bom + "x = '日本'\n".encode(body_encoding) + tail)
    monkeypatch.setattr(file_utils, "SOURCE_ENCODING", ["cp932"])

    assert read_source(str(path)) == (expect_text, expect_encoding)
    assert detected_encoding(str(path)) == expect_encoding


def test_detection_reads_a_file_its_bom_codec_does_not_decode_without_the_bom(
        tmp_path, monkeypatch):
    path = tmp_path / "a.py"
    body = "class Foo:\n    pass\n# 日本語\n".encode("cp932")
    path.write_bytes(b"\xef\xbb\xbf" + body)
    monkeypatch.setattr(file_utils, "SOURCE_ENCODING", [])
    detect_input_list = []

    def detect(file_content: bytes) -> DetectAs:
        detect_input_list.append(file_content)
        return DetectAs(file_content, "cp932")

    monkeypatch.setattr(file_utils, "from_bytes", detect)

    assert read_source(str(path)) == ("class Foo:\n    pass\n# 日本語\n", "cp932")
    assert detect_input_list == [body]


def test_a_file_no_configured_encoding_decodes_is_detected(tmp_path, monkeypatch):
    path = tmp_path / "a.py"
    source = _long_japanese_source()
    _write(path, source, "cp932")
    monkeypatch.setattr(file_utils, "SOURCE_ENCODING", [])

    text, encoding = read_source(str(path))

    assert encoding == "cp932"
    assert text == source


def test_an_undetectable_file_is_read_with_replacement(tmp_path, undetectable):
    path = tmp_path / "a.py"
    path.write_bytes(b"def f():\n    return '\xff\xfe\xfd'\n")

    text, encoding = read_source(str(path))

    assert encoding == ""
    assert text == "def f():\n    return '���'\n"


def test_detected_encoding_names_only_a_detected_or_replaced_file(tmp_path, monkeypatch):
    monkeypatch.setattr(file_utils, "SOURCE_ENCODING", ["euc_jp"])
    _write(tmp_path / "utf8.py", "# 足し算\n", "utf-8")
    _write(tmp_path / "bom.py", "# 足し算\n", "utf-16")
    _write(tmp_path / "euc.py", "# 足し算\n", "euc_jp")
    _write(tmp_path / "sjis.py", _long_japanese_source(), "cp932")

    assert detected_encoding(str(tmp_path / "utf8.py")) is None
    assert detected_encoding(str(tmp_path / "bom.py")) is None
    assert detected_encoding(str(tmp_path / "euc.py")) is None
    assert detected_encoding(str(tmp_path / "sjis.py")) == read_source(
        str(tmp_path / "sjis.py"))[1]


def test_detected_encoding_is_empty_for_a_file_read_with_replacement(tmp_path, undetectable):
    path = tmp_path / "a.py"
    path.write_bytes(b"def f():\n    return '\xff\xfe\xfd'\n")

    assert detected_encoding(str(path)) == ""


def test_line_breaks_are_kept_and_read_source_text_turns_them_into_newlines(tmp_path):
    path = tmp_path / "a.py"
    path.write_bytes(b"a = 1\r\nb = 2\rc = 3\n")

    assert read_source(str(path))[0] == "a = 1\r\nb = 2\rc = 3\n"
    assert read_source_text(str(path)) == "a = 1\nb = 2\nc = 3\n"


def test_read_source_raises_oserror_for_a_missing_file(tmp_path):
    with pytest.raises(OSError):
        read_source(str(tmp_path / "missing.py"))


# == SOURCE_ENCODING check ==================================================

def test_an_unknown_encoding_name_is_refused(monkeypatch):
    monkeypatch.setattr(file_utils, "SOURCE_ENCODING", ["cp932", "shift-jiss"])
    with pytest.raises(ValueError) as e:
        check_source_encoding()
    assert "shift-jiss" in str(e.value)


@pytest.mark.parametrize("encoding", ["hex", "base64", "rot13", "zlib"])
def test_a_codec_that_is_no_text_encoding_is_refused(monkeypatch, encoding):
    monkeypatch.setattr(file_utils, "SOURCE_ENCODING", ["cp932", encoding])
    with pytest.raises(ValueError) as e:
        check_source_encoding()
    assert f"'{encoding}'" in str(e.value)


def test_known_encoding_names_pass(monkeypatch):
    monkeypatch.setattr(file_utils, "SOURCE_ENCODING", ["cp932", "euc_jp", "latin-1"])
    check_source_encoding()


def test_an_unknown_encoding_name_does_not_stop_the_import():
    env = dict(os.environ, SOURCE_ENCODING="shift-jiss")
    proc = subprocess.run([sys.executable, "-c", "import codetwine.pipeline"],
                          env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


async def test_an_unknown_encoding_name_stops_the_run_before_anything_is_analysed(
        tmp_path, monkeypatch):
    _set_run(monkeypatch, with_doc=False)
    monkeypatch.setattr(file_utils, "SOURCE_ENCODING", ["shift-jiss"])
    project = tmp_path / "proj"
    project.mkdir()
    _write(project / "a.py", "def f():\n    return 1\n", "utf-8")
    out = tmp_path / "out"

    with pytest.raises(ValueError, match="SOURCE_ENCODING names 'shift-jiss'"):
        await pipeline.process_all_files(str(project), str(out), None, max_workers=1)
    assert not out.exists()


# == is_text_file ===========================================================

def test_a_utf16_file_with_a_bom_is_text(tmp_path):
    text_path = tmp_path / "wide.py"
    _write(text_path, "def wide():\n    return 1\n", "utf-16")
    blank_path = tmp_path / "blank.py"
    _write(blank_path, "  \n", "utf-16")

    assert is_text_file(str(text_path))
    assert not is_text_file(str(blank_path))


def test_a_utf16_file_whose_text_starts_after_many_blank_lines_is_text(tmp_path):
    text_path = tmp_path / "wide.py"
    _write(text_path, "\n" * 9000 + "def wide():\n    return 1\n", "utf-16")
    blank_path = tmp_path / "blank.py"
    _write(blank_path, "\n" * 9000, "utf-16")

    assert is_text_file(str(text_path))
    assert not is_text_file(str(blank_path))


# == parse_file =============================================================

def test_a_shift_jis_c_file_keeps_every_definition(tmp_path, monkeypatch):
    path = tmp_path / "calc.c"
    _write(path, _CALC_C, "cp932")
    monkeypatch.setattr(file_utils, "SOURCE_ENCODING", ["cp932"])

    root_node, content = ts_parser.parse_file(str(path))

    name_list = [d.name for d in extract_definitions(root_node, EXT_TO_DEFINITION_DICT["c"])]
    assert name_list == ["twice", "label", "after"]
    assert content == _CALC_C.encode("utf-8")


def test_parse_file_warns_when_the_file_is_read_with_replacement(tmp_path, undetectable, caplog):
    path = tmp_path / "a.py"
    path.write_bytes(b"def f():\n    return '\xff'\n")

    with caplog.at_level(logging.WARNING, logger="codetwine.parsers.ts_parser"):
        ts_parser.parse_file(str(path))

    assert "SOURCE_ENCODING" in caplog.text


# == pipeline ===============================================================

def _make_mixed_project(tmp_path) -> tuple[str, dict[str, bytes]]:
    """Create a project mixing UTF-8, Shift_JIS and UTF-16 files.

    Returns:
        (project directory, {relative path: the bytes written}).
    """
    project = tmp_path / "proj"
    project.mkdir()
    source_dict = {
        "main.py": ("from helper import add, sub\n\ndef run():\n    # 実行\n"
                    "    return add(1, 2) + sub(3, 1)\n", "utf-8"),
        "helper.py": ("def add(a, b):\n    # 足し算\n    return a + b\n\n"
                      "def sub(a, b):\n    return a - b\n", "cp932"),
        "calc.c": (_CALC_C, "cp932"),
        "calc.h": ("int twice(int x);\nint after(int x);\n", "utf-8"),
        "use.c": ('#include "calc.h"\nint go(void) { return twice(3) + after(1); }\n', "utf-8"),
        "wide.py": ("def wide():\n    return 1\n", "utf-16"),
        "NOTE.txt": ("これはメモです\n", "cp932"),
    }
    content_dict = {}
    for rel, (text, encoding) in source_dict.items():
        content_dict[rel] = text.encode(encoding)
        (project / rel).write_bytes(content_dict[rel])
    return str(project), content_dict


def _load_dep_dict(base_output_dir: str, rel: str) -> dict:
    """Read one file's file_dependencies.json."""
    with open(os.path.join(resolve_file_output_dir(base_output_dir, rel),
                           "file_dependencies.json"), encoding="utf-8") as f:
        return json.load(f)


async def test_non_utf8_files_and_their_importers_are_analysed(tmp_path, monkeypatch):
    _set_run(monkeypatch, with_doc=False)
    project, content_dict = _make_mixed_project(tmp_path)
    out = tmp_path / "out"
    base_output_dir = str(out / "proj")

    run_dict = await pipeline.process_all_files(project, str(out), None, max_workers=1)

    assert run_dict["dependency_fail_list"] == []
    assert run_dict["file_count"] == len(content_dict)

    helper_dict = _load_dep_dict(base_output_dir, "helper.py")
    assert [d["name"] for d in helper_dict["definitions"]] == ["add", "sub"]
    assert "# 足し算" in helper_dict["definitions"][0]["context"]
    caller_dict = next(u for u in helper_dict["caller_usages"] if u["name"] == "add")
    assert caller_dict["file"] == "proj/main_py/main.py"

    main_dict = _load_dep_dict(base_output_dir, "main.py")
    callee_dict = {u["name"]: u for u in main_dict["callee_usages"]}
    assert callee_dict["add"]["from"] == "proj/helper_py/helper.py"
    assert "# 足し算" in callee_dict["add"]["target_context"]

    calc_dict = _load_dep_dict(base_output_dir, "calc.c")
    assert [d["name"] for d in calc_dict["definitions"]] == ["twice", "label", "after"]

    wide_dict = _load_dep_dict(base_output_dir, "wide.py")
    assert [d["name"] for d in wide_dict["definitions"]] == ["wide"]

    # Each copy keeps the bytes of the original file
    for rel, content in content_dict.items():
        copy_path = os.path.join(resolve_file_output_dir(base_output_dir, rel),
                                 os.path.basename(rel))
        with open(copy_path, "rb") as f:
            assert f.read() == content, rel


async def test_the_caller_usage_context_of_a_non_utf8_caller_is_filled(tmp_path, monkeypatch):
    _set_run(monkeypatch, with_doc=False)
    project = tmp_path / "proj"
    project.mkdir()
    _write(project / "lib.py", "def f():\n    return 1\n", "utf-8")
    _write(project / "app.py", "from lib import f\n\n# 呼び出し\nvalue = f()\n", "cp932")
    out = tmp_path / "out"

    await pipeline.process_all_files(str(project), str(out), None, max_workers=1)

    lib_dict = _load_dep_dict(str(out / "proj"), "lib.py")
    usage_dict = next(u for u in lib_dict["caller_usages"] if u["name"] == "f")
    assert "# 呼び出し" in usage_dict["usage_context"]


async def test_the_design_document_of_a_non_utf8_file_reads_its_decoded_source(
        tmp_path, monkeypatch):
    _set_run(monkeypatch, with_doc=True)
    template_path = tmp_path / "template.json"
    template_path.write_text(json.dumps({
        "sections": [{"id": "overview", "title": "Overview", "prompt": "Describe it."}],
        "summary_prompt": "Summarize it.",
    }))
    monkeypatch.setattr(doc_creator, "DOC_TEMPLATE_PATH", str(template_path))
    project = tmp_path / "proj"
    project.mkdir()
    _write(project / "helper.py", "def add(a, b):\n    # 足し算\n    return a + b\n", "cp932")
    out = tmp_path / "out"
    llm_client = RecordLLMClient()

    run_dict = await pipeline.process_all_files(str(project), str(out), llm_client,
                                                max_workers=1)

    assert run_dict["doc_fail_list"] == []
    assert any("# 足し算" in prompt for prompt in llm_client.prompt_list)
    assert os.path.isfile(os.path.join(
        resolve_file_output_dir(str(out / "proj"), "helper.py"), "doc.json"))


async def test_the_files_whose_encoding_is_detected_are_recorded_and_returned(
        tmp_path, monkeypatch):
    _set_run(monkeypatch, with_doc=False)
    monkeypatch.setattr(file_utils, "SOURCE_ENCODING", [])
    monkeypatch.setattr(file_utils, "from_bytes", lambda file_content: DetectAs(file_content, "big5hkscs"))
    project = tmp_path / "proj"
    project.mkdir()
    _write(project / "a.py", "def f():\n    return 1\n", "utf-8")
    _write(project / "b.py", "# 足し算\ndef g():\n    return 1\n", "cp932")
    _write(project / "NOTE.txt", "これはメモです\n", "cp932")
    out = tmp_path / "out"

    run_dict = await pipeline.process_all_files(str(project), str(out), None, max_workers=1)

    assert run_dict["detected_encoding_dict"] == {"b.py": "big5hkscs"}
    assert _load_dep_dict(str(out / "proj"), "b.py")["detected_encoding"] == "big5hkscs"
    assert _load_dep_dict(str(out / "proj"), "a.py")["detected_encoding"] is None
    assert _load_dep_dict(str(out / "proj"), "NOTE.txt")["detected_encoding"] is None


async def test_a_utf16_file_with_an_extra_byte_keeps_its_definitions_and_is_recorded(
        tmp_path, monkeypatch):
    _set_run(monkeypatch, with_doc=False)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_bytes(
        "class Foo:\n    pass\n\ndef bar():\n    return Foo()\n".encode("utf-16") + b"\n")
    _write(project / "b.py", "from a import Foo\n", "utf-8")
    out = tmp_path / "out"

    run_dict = await pipeline.process_all_files(str(project), str(out), None, max_workers=1)

    assert run_dict["detected_encoding_dict"] == {"a.py": "utf-16"}
    dep_dict = _load_dep_dict(str(out / "proj"), "a.py")
    assert [d["name"] for d in dep_dict["definitions"]] == ["Foo", "bar"]
    assert dep_dict["detected_encoding"] == "utf-16"


async def test_the_encoding_of_a_file_is_detected_once_per_run(tmp_path, monkeypatch):
    _set_run(monkeypatch, with_doc=False)
    monkeypatch.setattr(file_utils, "SOURCE_ENCODING", [])
    detect_list: list[bytes] = []

    def detect(file_content: bytes) -> DetectAs:
        detect_list.append(file_content)
        return DetectAs(file_content, "big5hkscs")

    monkeypatch.setattr(file_utils, "from_bytes", detect)
    project = tmp_path / "proj"
    project.mkdir()
    _write(project / "b.py", "# 足し算\ndef g():\n    return 1\n", "cp932")

    run_dict = await pipeline.process_all_files(str(project), str(tmp_path / "out"), None, max_workers=1)

    assert run_dict["detected_encoding_dict"] == {"b.py": "big5hkscs"}
    assert len(detect_list) == 1
