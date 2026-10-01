import re
import sys
from pathlib import Path

import tree_sitter_c_sharp as tscs
from tree_sitter import Language, Parser

PARSER = Parser(Language(tscs.language()))

TYPE_KINDS = {
    "class_declaration": "class",
    "struct_declaration": "struct",
    "interface_declaration": "interface",
    "record_declaration": "record",
    "record_struct_declaration": "record",
    "enum_declaration": "enum",
}
MEMBER_KINDS = {
    "method_declaration": "method",
    "constructor_declaration": "constructor",
    "property_declaration": "property",
}
PRIMITIVES = {"string", "int", "bool", "double", "float", "long", "decimal", "object",
              "byte", "char", "void", "var", "short", "uint", "ulong", "DateTime", "Guid"}
MAX_CONTENT = 12000


def _t(node, src):
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _strip_generics(s):
    while True:
        new = re.sub(r"<[^<>]*>", "", s)
        if new == s:
            return s
        s = new


def _norm_type(s):
    s = _strip_generics(s)
    s = re.sub(r"\[.*?\]|\?", "", s)
    return s.split(".")[-1].strip()


def _signature(node, src):
    # skip [Attributes] so braces inside them don't cut the signature
    start = next((c.start_byte for c in node.children if c.type != "attribute_list"),
                 node.start_byte)
    text = src[start:node.end_byte].decode("utf-8", "replace")
    head = re.split(r"\{|=>", text, maxsplit=1)[0]
    return re.sub(r"\s+", " ", head).strip()


def _leading_comments(node, src):
    out, p = [], node.prev_sibling
    while p is not None and p.type == "comment":
        out.insert(0, _t(p, src))
        p = p.prev_sibling
    return "\n".join(out)[:1500]


def _analyze(node, src):
    """Collect calls, called method names and created types inside a member."""
    calls, names, created = set(), set(), set()
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == "invocation_expression":
            fn = n.child_by_field_name("function")
            if fn is not None:
                ftxt = re.sub(r"\s+", "", _t(fn, src))
                calls.add(ftxt[:120])
                ids = re.findall(r"[A-Za-z_]\w*", _strip_generics(ftxt))
                if ids:
                    names.add(ids[-1])
        elif n.type == "object_creation_expression":
            ty = n.child_by_field_name("type")
            if ty is not None:
                created.add(_norm_type(_t(ty, src)))
        stack.extend(n.children)
    created.discard("")
    return sorted(calls), sorted(names), sorted(created)


def _dependencies(type_node, src):
    """Types used by fields, properties and constructor parameters."""
    deps = set()
    body = type_node.child_by_field_name("body")
    for m in (body.children if body else []):
        tys = []
        if m.type == "field_declaration":
            vd = next((c for c in m.children if c.type == "variable_declaration"), None)
            if vd is not None:
                tys.append(vd.child_by_field_name("type"))
        elif m.type == "property_declaration":
            tys.append(m.child_by_field_name("type"))
        elif m.type == "constructor_declaration":
            params = m.child_by_field_name("parameters")
            for p in (params.children if params else []):
                if p.type == "parameter":
                    tys.append(p.child_by_field_name("type"))
        for ty in tys:
            if ty is not None:
                deps.add(_norm_type(_t(ty, src)))
    return sorted(d for d in deps if d and d not in PRIMITIVES)


def _join(ns, name):
    return f"{ns}.{name}" if ns else name


def _walk(node, src, ns, parents, out):
    for c in node.children:
        if c.type == "file_scoped_namespace_declaration":
            nm = c.child_by_field_name("name")
            if nm is not None:
                ns = _join(ns, _t(nm, src))   # applies to the siblings that follow
            _walk(c, src, ns, parents, out)
        elif c.type == "namespace_declaration":
            nm = c.child_by_field_name("name")
            body = c.child_by_field_name("body")
            _walk(body or c, src, _join(ns, _t(nm, src)) if nm is not None else ns, parents, out)
        elif c.type in TYPE_KINDS:
            _type(c, src, ns, parents, out)


def _type(node, src, ns, parents, out):
    nm = node.child_by_field_name("name")
    if nm is None:
        return
    name = _t(nm, src)
    chain = parents + [name]
    cls = ".".join(chain)
    body = node.child_by_field_name("body")
    base = next((c for c in node.children if c.type == "base_list"), None)
    base_types = sorted({_norm_type(_t(b, src)) for b in base.named_children}) if base else []

    member_sigs = []
    for m in (body.children if body else []):
        if m.type in MEMBER_KINDS:
            mn = m.child_by_field_name("name")
            sig = _signature(m, src)
            calls, names, created = _analyze(m, src)
            member_sigs.append(sig)
            out.append({
                "symbol_type": MEMBER_KINDS[m.type],
                "namespace": ns, "class": cls,
                "method": _t(mn, src) if mn is not None else name,
                "signature": sig,
                "doc": _leading_comments(m, src),
                "content": _t(m, src)[:MAX_CONTENT],
                "start_line": m.start_point[0] + 1,
                "end_line": m.end_point[0] + 1,
                "calls": calls[:60], "called_names": names, "created_types": created,
            })
        elif m.type in TYPE_KINDS:
            _type(m, src, ns, chain, out)

    header = _signature(node, src)
    if TYPE_KINDS[node.type] == "enum":
        content = _t(node, src)[:MAX_CONTENT]
    else:
        content = header + "\n\nMembers:\n" + "\n".join(member_sigs)
    out.append({
        "symbol_type": TYPE_KINDS[node.type],
        "namespace": ns, "class": cls, "method": None,
        "signature": header,
        "doc": _leading_comments(node, src),
        "content": content[:MAX_CONTENT],
        "start_line": node.start_point[0] + 1,
        "end_line": node.end_point[0] + 1,
        "base_types": base_types,
        "dependencies": _dependencies(node, src),
        "calls": [], "called_names": [], "created_types": [],
    })


def parse_source(src: bytes):
    if src.startswith(b"\xef\xbb\xbf"):   # strip UTF-8 BOM
        src = src[3:]
    tree = PARSER.parse(src)
    out = []
    _walk(tree.root_node, src, "", [], out)
    return out


if __name__ == "__main__":   # dry run: python csharp_parser.py path\to\File.cs
    for r in parse_source(Path(sys.argv[1]).read_bytes()):
        print(f"{r['symbol_type']:<11} {r['namespace'] or '-'} | {r['class']}.{r['method'] or ''}"
              f" | {r['signature'][:70]} | calls={len(r['calls'])}")