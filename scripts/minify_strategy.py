#!/usr/bin/env python3
"""
Minify a Python strategy file for platforms with file-size/line limits.

What it does:
- removes module/class/function docstrings
- removes all comments
- keeps code semantics intact

Usage:
  python scripts/minify_strategy.py gkoudai_au_strategy_autonomous.py \
         -o gkoudai_au_strategy_autonomous.min.py
"""
import argparse
import ast
import io
import sys
import tokenize
import base64
import zlib


class _StripDocstrings(ast.NodeTransformer):
    def visit_Module(self, node: ast.Module):
        self.generic_visit(node)
        if node.body and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], 'value', None), ast.Constant) and isinstance(node.body[0].value.value, str):
            node.body.pop(0)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.generic_visit(node)
        if node.body and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], 'value', None), ast.Constant) and isinstance(node.body[0].value.value, str):
            node.body.pop(0)
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self.generic_visit(node)
        if node.body and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], 'value', None), ast.Constant) and isinstance(node.body[0].value.value, str):
            node.body.pop(0)
        return node

    def visit_ClassDef(self, node: ast.ClassDef):
        self.generic_visit(node)
        if node.body and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], 'value', None), ast.Constant) and isinstance(node.body[0].value.value, str):
            node.body.pop(0)
        return node


def _strip_comments(source: str) -> str:
    out = []
    sio = io.StringIO(source)
    last_lineno = -1
    last_col = 0
    for tok in tokenize.generate_tokens(sio.readline):
        ttype, tstring, (sline, scol), (eline, ecol), ltext = tok
        if ttype == tokenize.COMMENT:
            continue
        if ttype == tokenize.NL:
            # bare newline from a comment-only line — drop
            continue
        out.append(tok)
    return tokenize.untokenize(out)


def _flatten_plain_multiline_strings(source: str) -> str:
    """Turn plain triple-quoted strings into single-line literals with \n escapes.
    Skips f-strings since evaluating them is unsafe here.
    """
    out = []
    sio = io.StringIO(source)
    for tok in tokenize.generate_tokens(sio.readline):
        ttype, tstring, start, end, ltext = tok
        if ttype == tokenize.STRING:
            # heuristics: not f/r/b/u prefixes that include 'f' and contains a newline
            prefix = ''
            i = 0
            while i < len(tstring) and tstring[i] in 'rubfRUBF':
                prefix += tstring[i]
                i += 1
            is_f = 'f' in prefix.lower()
            if not is_f and ('\n' in tstring or '"""' in tstring or "'''" in tstring):
                try:
                    val = ast.literal_eval(tstring)
                    if isinstance(val, str) and ('\n' in val or '\r' in val):
                        flat = repr(val)
                        # restore original prefixes except f/F
                        for ch in prefix:
                            if ch.lower() != 'f':
                                flat = ch + flat
                        tok = (ttype, flat, start, end, ltext)
                except Exception:
                    pass
        out.append(tok)
    return tokenize.untokenize(out)


def minify_code(src: str) -> str:
    # 1) remove docstrings via AST (keeps formatting compact with ast.unparse)
    tree = ast.parse(src)
    tree = _StripDocstrings().visit(tree)
    ast.fix_missing_locations(tree)
    try:
        code_no_docs = ast.unparse(tree)
    except Exception:
        # fallback: keep original if unparse not available
        code_no_docs = src
    # 2) remove comments (tokenize) and redundant blank lines
    code_no_comments = _strip_comments(code_no_docs)
    # 3) flatten plain triple-quoted strings (e.g. large JSON/sample blocks)
    code_flat = _flatten_plain_multiline_strings(code_no_comments)
    # 4) squeeze extra blank lines
    lines = [ln.rstrip() for ln in code_flat.splitlines()]
    squeezed = []
    blank = 0
    for ln in lines:
        if ln.strip() == '':
            blank += 1
            if blank <= 1:
                squeezed.append('')
        else:
            blank = 0
            squeezed.append(ln)
    return '\n'.join(squeezed).strip() + '\n'


def compress_long_strings(src: str, threshold: int = 512) -> str:
    """Replace long plain string literals with runtime-decompressed blobs.
    Inserts a small helper _GX if at least one replacement occurs.
    Skips f-strings.
    """
    replaced = []
    out_tokens = []
    sio = io.StringIO(src)
    changed = False
    for tok in tokenize.generate_tokens(sio.readline):
        ttype, tstring, start, end, ltext = tok
        if ttype == tokenize.STRING:
            # detect prefix and skip f-strings
            prefix = ''
            i = 0
            while i < len(tstring) and tstring[i] in 'rubfRUBF':
                prefix += tstring[i]
                i += 1
            is_f = 'f' in prefix.lower()
            if not is_f:
                try:
                    val = ast.literal_eval(tstring)
                except Exception:
                    val = None
                if isinstance(val, str) and len(val) >= threshold:
                    blob = base64.b64encode(zlib.compress(val.encode('utf-8'), 9)).decode('ascii')
                    out_tokens.append((tokenize.NAME, '_GX'))
                    out_tokens.append((tokenize.OP, '('))
                    out_tokens.append((tokenize.STRING, repr(blob)))
                    out_tokens.append((tokenize.OP, ')'))
                    changed = True
                    continue
        # keep as 2-tuples to avoid position issues
        out_tokens.append((ttype, tstring))
    text = tokenize.untokenize(out_tokens)
    if not changed:
        return src
    # inject helper at top if not present
    helper = 'import base64 as _b64, zlib as _zl\n' \
             'def _GX(s):\n    return _zl.decompress(_b64.b64decode(s)).decode(\'utf-8\')\n'
    return helper + text


def pack_to_stub(src: str) -> str:
    """Pack entire source into a small stub that execs a zlib+base64 payload."""
    payload = base64.b64encode(zlib.compress(src.encode('utf-8'), 9)).decode('ascii')
    stub = (
        "# packed by minify_strategy.py\n"
        "import base64 as _b64, zlib as _zl\n"
        "exec(_zl.decompress(_b64.b64decode('" + payload + "')).decode('utf-8'))\n"
    )
    return stub


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('input', help='input .py file')
    ap.add_argument('-o', '--output', default=None, help='output path (default: <input>.min.py)')
    ap.add_argument('--compress-strings', type=int, default=0, help='threshold to compress long strings (0=off)')
    ap.add_argument('--pack', action='store_true', help='pack entire file into exec(zlib+base64) stub')
    args = ap.parse_args()

    with open(args.input, 'r', encoding='utf-8') as f:
        src = f.read()

    out = minify_code(src)
    if args.compress_strings and args.compress_strings > 0:
        out = compress_long_strings(out, threshold=args.compress_strings)
    if args.pack:
        out = pack_to_stub(out)
    out_path = args.output or (args.input.rsplit('.', 1)[0] + '.min.py')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(out)

    print(f'Wrote minified file: {out_path}')
    print(f'Original lines: {len(src.splitlines())}, Minified lines: {len(out.splitlines())}')
    print(f'Final size: {len(out.encode("utf-8"))//1024} KB')


if __name__ == '__main__':
    main()
