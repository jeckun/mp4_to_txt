def normalize_output_text(text: str) -> str:
    """清理空行并在每一行结尾追加中文逗号。"""
    normalized_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        normalized_lines.append(f"{line}，")
    return "\n".join(normalized_lines)
