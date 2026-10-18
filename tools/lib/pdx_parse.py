"""正确解析 PDX .mesh / .anim 二进制结构。

格式规范（社区逆向，io_pdx_mesh 同款）:
  header: b'@@b@' + 'pdxasset' property
  node:     '[' * depth + name + b'\0'
  property: '!' + u8 namelen + name + type_char + payload
  type:
    'i' : u32 count + count * int32
    'f' : u32 count + count * float32
    'd' : u32 count + count * float64  (少见)
    's' : u32 count(=1) + u32 strlen + bytes  (字符串数组, nul 分隔)
    'b' : u32 count + count * u8
"""
import struct, sys
from collections import Counter, OrderedDict, defaultdict


def anim_info(path):
    """Read only top-level animation metadata, without decoding sample arrays."""
    with open(path, 'rb') as stream:
        def exact(count):
            data = stream.read(count)
            if len(data) != count:
                raise ValueError(f'Truncated PDX metadata: {path}')
            return data

        if exact(4) != b'@@b@':
            raise ValueError(f'Invalid PDX magic: {path}')
        current = None
        metadata = {}
        while token := stream.read(1):
            if token == b'[':
                depth = 1
                token = exact(1)
                while token == b'[':
                    depth += 1
                    token = exact(1)
                name = bytearray()
                while token != b'\0':
                    name.extend(token)
                    token = exact(1)
                if current == 'info':
                    break
                current = name.decode('utf-8') if depth == 1 else None
            elif token == b'!':
                name = exact(exact(1)[0]).decode('utf-8')
                kind = exact(1)
                count = struct.unpack('<I', exact(4))[0]
                if kind == b's':
                    size = struct.unpack('<I', exact(4))[0]
                    stream.seek(size, 1)
                elif kind in (b'i', b'f', b'd', b'b'):
                    size = {b'i': 4, b'f': 4, b'd': 8, b'b': 1}[kind] * count
                    if current == 'info':
                        if count > 64:
                            raise ValueError(f'Unexpected metadata array: {path} / {name}')
                        values = struct.unpack('<' + str(count) + kind.decode(), exact(size))
                        metadata[name] = values[0] if count == 1 else values
                    else:
                        stream.seek(size, 1)
                else:
                    raise ValueError(f'Unknown PDX type {kind!r}: {path}')
            else:
                raise ValueError(f'Invalid PDX token {token!r}: {path}')
    if not isinstance(metadata.get('j'), int) or metadata['j'] <= 0:
        raise ValueError(f'Missing animation joint count: {path}')
    return metadata


class Reader:
    def __init__(self, data):
        self.d = data
        self.p = 0
    def u8(self):
        v = self.d[self.p]; self.p += 1; return v
    def u32(self):
        v = struct.unpack_from('<I', self.d, self.p)[0]; self.p += 4; return v
    def i32n(self, n):
        v = struct.unpack_from('<%di' % n, self.d, self.p); self.p += 4*n; return v
    def f32n(self, n):
        v = struct.unpack_from('<%df' % n, self.d, self.p); self.p += 4*n; return v
    def raw(self, n):
        v = self.d[self.p:self.p+n]; self.p += n; return v
    def peek(self):
        return self.d[self.p] if self.p < len(self.d) else None
    def eof(self):
        return self.p >= len(self.d)

def read_name(r):
    n = r.u8()
    return r.raw(n).decode('utf-8', 'replace')

def read_prop_value(r):
    t = chr(r.u8())
    if t == 'i':
        c = r.u32(); return ('int', list(r.i32n(c)))
    if t == 'f':
        c = r.u32(); return ('float', list(r.f32n(c)))
    if t == 'd':
        c = r.u32(); vals = struct.unpack_from('<%dd' % c, r.d, r.p); r.p += 8*c
        return ('double', list(vals))
    if t == 's':
        c = r.u32(); ln = r.u32(); blob = r.raw(ln)
        parts = [p.decode('utf-8','replace') for p in blob.split(b'\0') if p]
        return ('str', parts)
    if t == 'b':
        c = r.u32(); return ('bool', list(r.raw(c)))
    raise ValueError(f'unknown prop type {t!r} at {r.p}')

def parse(data):
    r = Reader(data)
    assert r.raw(4) == b'@@b@', 'bad magic'
    root = OrderedDict()
    stack = [(0, root)]
    while not r.eof():
        c = r.peek()
        if c == ord('!'):
            r.u8()
            name = read_name(r)
            kind, val = read_prop_value(r)
            cur = stack[-1][1]
            cur.setdefault('_props', OrderedDict())[name] = (kind, val)
        elif c == ord('['):
            depth = 0
            while r.peek() == ord('['):
                r.u8(); depth += 1
            nm = bytearray()
            while r.peek() not in (0, None):
                nm.append(r.u8())
            if r.peek() == 0: r.u8()
            name = nm.decode('utf-8','replace')
            while stack and stack[-1][0] >= depth:
                stack.pop()
            parent = stack[-1][1]
            node = OrderedDict()
            # PDX assets commonly repeat nodes with the same name (for example,
            # one ``mesh`` node per material slot).  A mapping silently dropped
            # every occurrence except the last one, so keep the encoded order.
            parent.setdefault('_children', []).append((name, node))
            stack.append((depth, node))
        else:
            r.u8()
    return root

def walk(node, path=''):
    for name, (kind, val) in node.get('_props', {}).items():
        yield path, name, kind, val
    children = node.get('_children', [])
    totals = Counter(name for name, _child in children)
    seen = defaultdict(int)
    for cname, child in children:
        index = seen[cname]
        seen[cname] += 1
        label = f'{cname}[{index}]' if totals[cname] > 1 else cname
        yield from walk(child, path + '/' + label)

if __name__ == '__main__':
    data = open(sys.argv[1], 'rb').read()
    tree = parse(data)
    want = sys.argv[2] if len(sys.argv) > 2 else None
    for path, name, kind, val in walk(tree):
        if want and want not in name and want not in path:
            continue
        if kind == 'str':
            print(f"{path} :: {name} [{kind} x{len(val)}]")
            for v in val:
                print(f"     {v}")
        else:
            n = len(val)
            head = val[:8]
            print(f"{path} :: {name} [{kind} x{n}] {head}{' ...' if n > 8 else ''}")
