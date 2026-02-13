"""Tests for Telegram formatting utilities."""

from agent_tether.telegram.formatting import (
    chunk_message,
    escape_markdown,
    markdown_to_telegram_html,
    strip_tool_markers,
    _markdown_table_to_pre,
)

# ========== escape_markdown ==========


def test_escape_markdown_special_chars():
    """Test special characters are escaped."""
    assert escape_markdown("hello_world") == r"hello\_world"
    assert escape_markdown("**bold**") == r"\*\*bold\*\*"
    assert escape_markdown("`code`") == r"\`code\`"
    assert escape_markdown("[link](url)") == r"\[link\]\(url\)"


def test_escape_markdown_plain_text():
    """Test plain text is unchanged."""
    assert escape_markdown("hello world") == "hello world"
    assert escape_markdown("abc123") == "abc123"


def test_escape_markdown_all_special():
    """Test all special chars are covered."""
    for char in [
        "_",
        "*",
        "[",
        "]",
        "(",
        ")",
        "~",
        "`",
        ">",
        "#",
        "+",
        "-",
        "=",
        "|",
        "{",
        "}",
        ".",
        "!",
    ]:
        assert escape_markdown(char) == f"\\{char}"


# ========== markdown_to_telegram_html ==========


def test_markdown_to_html_code_blocks():
    """Test fenced code blocks convert to <pre>."""
    text = "```python\nprint('hello')\n```"
    result = markdown_to_telegram_html(text)
    assert "<pre>" in result
    assert "print(&#x27;hello&#x27;)" in result
    assert "</pre>" in result


def test_markdown_to_html_inline_code():
    """Test inline code converts to <code>."""
    text = "Use `pip install` to install"
    result = markdown_to_telegram_html(text)
    assert "<code>pip install</code>" in result


def test_markdown_to_html_bold():
    """Test bold markdown converts to <b>."""
    text = "This is **bold** text"
    result = markdown_to_telegram_html(text)
    assert "<b>bold</b>" in result


def test_markdown_to_html_bold_underscores():
    """Test __bold__ converts to <b>."""
    text = "This is __bold__ text"
    result = markdown_to_telegram_html(text)
    assert "<b>bold</b>" in result


def test_markdown_to_html_italic():
    """Test italic markdown converts to <i>."""
    text = "This is *italic* text"
    result = markdown_to_telegram_html(text)
    assert "<i>italic</i>" in result


def test_markdown_to_html_links():
    """Test markdown links convert to <a href>."""
    text = "Visit [Google](https://google.com)"
    result = markdown_to_telegram_html(text)
    assert '<a href="https://google.com">Google</a>' in result


def test_markdown_to_html_headers():
    """Test markdown headers convert to bold."""
    text = "# Title\n## Subtitle"
    result = markdown_to_telegram_html(text)
    assert "<b>Title</b>" in result
    assert "<b>Subtitle</b>" in result


def test_markdown_to_html_escapes_html():
    """Test HTML entities are escaped."""
    text = "<script>alert('xss')</script>"
    result = markdown_to_telegram_html(text)
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


# ========== _markdown_table_to_pre ==========


def test_markdown_table_to_pre():
    """Test markdown table converts to <pre> block."""
    import html

    table = "| Name | Age |\n|------|-----|\n| Alice | 30 |\n| Bob | 25 |"
    escaped = html.escape(table)
    result = _markdown_table_to_pre(escaped)
    assert "<pre>" in result
    assert "</pre>" in result
    assert "Alice" in result
    assert "Bob" in result


def test_markdown_table_alignment():
    """Test table columns are aligned."""
    import html

    table = "| A | B |\n|---|---|\n| short | longer value |\n| x | y |"
    escaped = html.escape(table)
    result = _markdown_table_to_pre(escaped)
    assert "<pre>" in result
    # Separator row should be stripped
    assert "---" not in result


# ========== strip_tool_markers ==========


def test_strip_tool_markers():
    """Test tool marker lines are removed."""
    text = "[tool: Read]\nFile contents here\n[tool: Write]\nWriting file"
    result = strip_tool_markers(text)
    assert "[tool: Read]" not in result
    assert "[tool: Write]" not in result
    assert "File contents here" in result
    assert "Writing file" in result


def test_strip_tool_markers_no_markers():
    """Test text without markers is unchanged."""
    text = "Normal text\nwith multiple lines"
    result = strip_tool_markers(text)
    assert result == text


def test_strip_tool_markers_only_markers():
    """Test text with only markers becomes empty."""
    text = "[tool: Bash]"
    result = strip_tool_markers(text)
    assert result == ""


# ========== chunk_message ==========


def test_chunk_message_short():
    """Test short message stays as single chunk."""
    text = "Short message"
    assert chunk_message(text) == [text]


def test_chunk_message_exact_limit():
    """Test message exactly at limit stays as single chunk."""
    text = "a" * 4096
    assert chunk_message(text) == [text]


def test_chunk_message_over_limit():
    """Test long message is split into chunks."""
    text = "a" * 5000
    chunks = chunk_message(text, limit=4096)
    assert len(chunks) == 2
    assert len(chunks[0]) == 4096
    assert len(chunks[1]) == 904
    assert "".join(chunks) == text


def test_chunk_message_custom_limit():
    """Test chunking with custom limit."""
    text = "a" * 100
    chunks = chunk_message(text, limit=30)
    assert len(chunks) == 4  # 30 + 30 + 30 + 10
    assert "".join(chunks) == text


def test_chunk_message_empty():
    """Test empty message returns single empty chunk."""
    assert chunk_message("") == [""]
