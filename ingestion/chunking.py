import re

HEADING = re.compile(r"^(#{1,6})\s+(.*)")


def split_long(text, max_chars):
    parts, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        while len(para) > max_chars:  # hard-split a giant paragraph
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(para[:max_chars])
            para = para[max_chars:]
        if len(cur) + len(para) + 2 > max_chars and cur:
            parts.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur.strip():
        parts.append(cur)
    return parts


def chunk_markdown(text, max_chars=1500):
    chunks, path, buf, in_fence = [], [], [], False

    def flush():
        body = "\n".join(buf).strip()
        buf.clear()
        if body:
            for part in split_long(body, max_chars):
                chunks.append({"heading": " > ".join(path), "content": part})

    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            path[:] = path[: level - 1] + [m.group(2).strip()]
        else:
            buf.append(line)
    flush()
    return chunks