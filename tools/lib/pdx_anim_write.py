"""写 PDX `.anim` 二进制。项目原先只有解析器（pdx_parse.py），没有写出器。

为什么需要：把原版动作重定向到我们骨架，走 Blender 导入→改→导出会经过
io_pdx_mesh 导出器（该导出器有把 1e-5 浮点噪声当动画通道的毛病，已打过补丁），
而且迭代一次要起 Blender。直接读写二进制迭代快得多，也不引入导出器的量化误差。

格式（与 pdx_parse.py 的 docstring 一致，逐字节核对过 female_body_idle.anim）:

    b'@@b@'
    !<u8 len>pdxasset i <u32 count=2> <i32 1> <i32 0>
    [info                                   ← 深度 1，一个 '['
        !fps f <u32 1> <f32>
        !sa  i <u32 1> <i32 帧数>
        !j   i <u32 1> <i32 骨数>
        [[<骨名>                            ← 深度 2，两个 '['
            !sa s <u32 1> <u32 strlen> <bytes 含结尾 \0>
            !t  f <u32 3> ...               ← rest 平移
            !q  f <u32 4> ...               ← rest 四元数 (x,y,z,w)
            !s  f <u32 1> ...               ← rest 缩放
    [samples
        !t f <u32 N> ...    ← 帧主序：每帧依次列出所有带 t 通道的骨
        !q f <u32 M> ...    ← 帧主序：每帧依次列出所有带 q 通道的骨
        !s f <u32 K> ...    ← 帧主序：每帧依次列出所有带 s 通道的骨

采样数组布局由计数反推验证：原版 sa=75 帧、3 根骨带 t → t 共 675 = 75*3*3 ✓；
19 根骨带 q → q 共 5700 = 75*19*4 ✓。所以是**帧主序**，且骨的先后按 info 中
出现顺序。

节点名后面必须跟一个 \0。深度用前缀 '[' 的个数表示。
"""
import struct


def _prop_name(name):
    b = name.encode("utf-8")
    assert len(b) < 256, name
    return bytes([ord("!"), len(b)]) + b


def prop_int(name, values):
    v = list(values)
    return _prop_name(name) + b"i" + struct.pack("<I", len(v)) + \
        struct.pack("<%di" % len(v), *v)


def prop_float(name, values):
    v = [float(x) for x in values]
    return _prop_name(name) + b"f" + struct.pack("<I", len(v)) + \
        struct.pack("<%df" % len(v), *v)


def prop_str(name, s):
    """字符串属性。payload 含结尾 \\0，strlen 把它算进去（照原版字节）。"""
    b = (s or "").encode("utf-8") + b"\0"
    return _prop_name(name) + b"s" + struct.pack("<I", 1) + \
        struct.pack("<I", len(b)) + b


def node(name, depth):
    return b"[" * depth + name.encode("utf-8") + b"\0"


def _drop_constant(frame_count, bones, sample_t, sample_q, sample_s=None):
    """把**全程恒定**的通道从采样数组里摘掉，恒定值写进 rest。无损。

    为什么放在这里：所有写 `.anim` 的路径（原版重定向、头部嫁接、物理烘焙、
    拆分时剥物理）都过 `write_anim`，在这儿做等于一处覆盖全部产物，**不新增
    管线步骤**。

    有多少空间（抽样 12 个已剥物理的动画）：
        q 通道 398 条，全程恒定 134 条（**34%**）
        t 通道 109 条，全程恒定   9 条（8%）
    采样数组是「每帧 × 每条通道」的密集数组，一条 301 帧的 q 通道占
    301×4×4 = 4.8 KB。实测单文件省 21.5%。

    **判据是逐帧逐分量严格相等**（`==`，不是「差值小于阈值」）。带阈值会把幅度
    很小的真实运动抹掉 —— 头发梢的摆动幅度就很小。宁可少压一点。

    **空 `sa` 是引擎支持的**：抽样 30 个原版 anim，每一个都含空 `sa` 的骨，共
    528 根。骨仍列在 `/info/` 里所以骨数不变 —— 引擎要求 anim 骨数严格等于
    mesh 骨数（[[anim-mesh-bone-check-must-be-per-entity]]）。没有采样时引擎用
    rest，而 `.anim` 的 rest 就是第 0 帧
    （[[pdx-anim-rest-is-frame0-not-bindpose]]），所以搬值进 rest 是等价的。
    """
    if frame_count <= 1:
        return bones, sample_t, sample_q, sample_s

    channels = {
        "t": (3, sample_t),
        "q": (4, sample_q),
        "s": (None, sample_s),
    }
    names_by_channel = {
        channel: [bone[0] for bone in bones if channel in bone[1]]
        for channel in channels
    }
    widths = {}
    for channel, names in names_by_channel.items():
        if channel == "s":
            widths[channel] = {bone[0]: len(bone[4]) if isinstance(bone[4], (list, tuple)) else 1
                               for bone in bones if "s" in bone[1]}
            if len(set(widths[channel].values())) > 1:
                raise ValueError("Mixed scale widths in one animation are not supported")
        else:
            widths[channel] = {name: channels[channel][0] for name in names}
        flat = channels[channel][1]
        expected = frame_count * sum(widths[channel][name] for name in names)
        if flat is None:
            if names:
                # Existing callers only write t/q.  A scale flag without sample
                # data would create an invalid animation, so fail at the source.
                raise ValueError("Scale channels require an explicit sample_s array")
            channels[channel] = (channels[channel][0], [])
        elif len(flat) != expected:
            if flat or names:
                raise ValueError(
                    f"Incorrect {channel} animation sample count: {len(flat)} != {expected}"
                )

    indices = {
        channel: {name: index for index, name in enumerate(names)}
        for channel, names in names_by_channel.items()
    }

    def series(channel, name):
        names = names_by_channel[channel]
        width = widths[channel][name]
        track = indices[channel][name]
        offset = sum(widths[channel][item] for item in names[:track])
        stride = sum(widths[channel][item] for item in names)
        flat = channels[channel][1]
        return [tuple(flat[f * stride + offset:f * stride + offset + width])
                for f in range(frame_count)]

    keep = {channel: [] for channel in channels}
    new_bones = []
    for name, sa, rt, rq, rs in bones:
        flags = set(sa)
        for channel in channels:
            if channel not in flags:
                continue
            samples = series(channel, name)
            if channel == "t":
                rt = list(samples[0])
            elif channel == "q":
                rq = list(samples[0])
            else:
                rs = list(samples[0])
            if all(value == samples[0] for value in samples[1:]):
                flags.discard(channel)
            else:
                keep[channel].append(name)
        new_bones.append((name, "".join(channel for channel in "tqs" if channel in flags),
                          rt, rq, rs))

    unchanged = all(len(keep[channel]) == len(names_by_channel[channel])
                    for channel in channels)
    if unchanged:
        # 压不掉任何通道，但**仍要返回 `new_bones`**：上面那两句
        # `rt = list(s[0])` / `rq = list(s[0])` 顺带把 rest 对齐到第 0 帧，
        # 那是原版的不变量（[[pdx-anim-rest-is-frame0-not-bindpose]]）。
        # 这里返回 `bones` 会把对齐一起丢掉，于是「rest == 首帧」守不守变成
        # **偶然的** —— 取决于该 clip 恰好有没有恒定通道。实测同一次重定向：
        #   坐姿产物（有恒定通道）  超 1° 的骨 0 根   ← 走压缩路径，守
        #   站立产物（无恒定通道）  超 1° 的骨 49 根  ← 走这条提前返回，不守
        # 没压掉通道时 `sa` 与两个采样数组都没变，只有 rest 变了，所以直接
        # 换成 `new_bones` 是安全的。
        return new_bones, sample_t, sample_q, sample_s

    packed = {}
    for channel in channels:
        out = []
        old_names = names_by_channel[channel]
        kept_names = keep[channel]
        old_flat = channels[channel][1]
        old_stride = sum(widths[channel][name] for name in old_names)
        old_offsets = {}
        running = 0
        for name in old_names:
            old_offsets[name] = running
            running += widths[channel][name]
        for frame in range(frame_count):
            for name in kept_names:
                start = frame * old_stride + old_offsets[name]
                out.extend(old_flat[start:start + widths[channel][name]])
        packed[channel] = out
    return new_bones, packed["t"], packed["q"], packed["s"] if sample_s is not None else None


def _drop_unobservable(frame_count, bones, sample_t, sample_q, sample_s,
                       observable_bones):
    """Omit samples only for an explicit mesh/attachment dependency closure.

    Bone order and all rest values stay intact. The caller must derive this
    set from the actual destination mesh, including ancestors and attachment
    hooks; this writer cannot infer visibility from bone or file names.
    """
    names = [bone[0] for bone in bones]
    if len(set(names)) != len(names):
        raise ValueError('Animation contains duplicate bone names')
    if isinstance(observable_bones, (str, bytes)):
        raise ValueError('observable_bones must be a collection of bone names')
    observable = frozenset(observable_bones)
    if not observable or any(not isinstance(name, str) for name in observable):
        raise ValueError('observable_bones must contain explicit bone names')
    if observable - set(names):
        raise ValueError('Observable mesh bones are absent from animation')
    if any(set(bone[1]) - {'t', 'q', 's'} for bone in bones):
        raise ValueError('Observable-channel export supports only t/q/s samples')
    if not isinstance(frame_count, int) or frame_count < 1:
        raise ValueError('Animation must contain a positive integer frame count')
    packed = []
    for channel, width, samples in (
        ('t', 3, sample_t), ('q', 4, sample_q),
        ('s', None, sample_s),
    ):
        animated = [bone[0] for bone in bones if channel in bone[1]]
        widths = {
            bone[0]: (len(bone[4]) if isinstance(bone[4], (list, tuple)) else 1)
            for bone in bones if channel == 's' and channel in bone[1]
        } if channel == 's' else {name: width for name in animated}
        if channel == 's' and len(set(widths.values())) > 1:
            raise ValueError('Mixed scale widths in one animation are not supported')
        if channel == 's' and widths:
            width = next(iter(widths.values()))
        width = width or 1
        expected = len(animated) * width * frame_count
        if samples is None:
            if animated:
                raise ValueError('Scale channels require an explicit sample_s array')
            samples = []
        if len(samples) != expected:
            raise ValueError(f'Incorrect {channel} animation sample count')
        kept = [i for i, name in enumerate(animated) if name in observable]
        if len(kept) == len(animated):
            packed.append(samples)
            continue
        values = []
        stride = len(animated) * width
        for frame in range(frame_count):
            for index in kept:
                start = frame * stride + index * width
                values.extend(samples[start:start + width])
        packed.append(values)
    result = [(name, flags if name in observable else '', rt, rq, rs)
              for name, flags, rt, rq, rs in bones]
    return result, packed[0], packed[1], packed[2] if sample_s is not None else None


def write_anim(path, fps, frame_count, bones, sample_t, sample_q,
               drop_constant=True, *, observable_bones=None, sample_s=None):
    """bones: [(名, sa字符串, rest_t(3), rest_q(4), rest_s(1))]，顺序即采样顺序。

    `drop_constant`：默认摘掉全程恒定的通道（无损，见 `_drop_constant`）。
    只有在**刻意要保留密集通道**时才传 False。
    """
    if observable_bones is not None and any(set(bone[1]) - {'t', 'q', 's'} for bone in bones):
        raise ValueError('Observable-channel export supports only t/q/s samples')
    if drop_constant:
        bones, sample_t, sample_q, sample_s = _drop_constant(
            frame_count, bones, sample_t, sample_q, sample_s)
    if observable_bones is not None:
        bones, sample_t, sample_q, sample_s = _drop_unobservable(
            frame_count, bones, sample_t, sample_q, sample_s, observable_bones)
    if sample_s is not None and not any('s' in bone[1] for bone in bones):
        sample_s = None
    out = bytearray(b"@@b@")
    out += prop_int("pdxasset", [1, 0])
    out += node("info", 1)
    out += prop_float("fps", [fps])
    out += prop_int("sa", [frame_count])
    out += prop_int("j", [len(bones)])
    for name, sa, rt, rq, rs in bones:
        out += node(name, 2)
        out += prop_str("sa", sa)
        out += prop_float("t", rt)
        out += prop_float("q", rq)
        out += prop_float("s", rs if isinstance(rs, (list, tuple)) else [rs])
    out += node("samples", 1)
    out += prop_float("t", sample_t)
    out += prop_float("q", sample_q)
    if sample_s is not None:
        out += prop_float("s", sample_s)
    with open(path, "wb") as f:
        f.write(out)
    return len(out)
