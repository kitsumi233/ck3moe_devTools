"""把源模型的 RMO / normal 贴图重打包成 CK3 portrait shader 要的通道布局。

## 为什么必须做这一步（「奇怪的反光质感」的根因）

`gfx/FX/jomini/portrait.shader` 的贴图槽位是固定的：

| 槽位 index | .asset 字段         | shader 里的名字   |
|-----------|---------------------|------------------|
| 0         | `texture_diffuse`   | `DiffuseMap`     |
| 1         | `texture_specular`  | `PropertiesMap`  |
| 2         | `texture_normal`    | `NormalMap`      |
| 3         | `texture = {...}`   | `SSAOColorMap`   |

之前我们的 `.asset` 只声明了 `texture_diffuse`，槽位 1/2 没给图。
**没给图不等于中性值** —— 采样出来是不透明白色 (1,1,1,1)，于是：

`portrait.shader` 里 `CommonPixelShaderColor()` 这样用 Properties：

```
GetMaterialProperties( Diffuse.rgb, Normal,
                       saturate( Properties.a ),   // ← roughness
                       Properties.g,               // ← specular
                       Properties.b )              // ← metalness
float ScatteringMask = Properties.r;                // ← 次表面散射/厚度
```

白图 → **metalness = 1、specular = 1**，皮肤和布料被当成抛光金属渲染。
这正是用户看到的「奇怪的反光质感」。

NormalMap 也一样。`UnpackRRxGNormal()`（`clausewitz/gfx/FX/cw/utility.fxh`）：

```
x = g * 2 - 1;  y = a * 2 - 1;  y = -y;  z = sqrt(1 - x² - y²)
```

白图 → x=1, y=-1 → z=0，得到一个**完全横向的切空间法线**，
全身光照方向都是错的。这会放大金属感，两个问题叠在一起。

## 通道对应关系

源素材（少女前线2：追放 的原皮）给的是业界通用打包：

* `*_n.png`   —— 标准切空间法线，RGB=(X,Y,Z)，R/G 以 128 为中心、B≈255
* `*_rmo.png` —— R:Roughness  G:Metalness  B:Occlusion（实测：body 的 G 全 0，
                 即皮肤完全非金属；cloth 的 G 只在金属饰件上有值）

CK3 要的是另一套，所以逐通道搬：

```
Properties.r = 0                      次表面散射掩码。二次元不要写实的肉透光感，
                                      给 0；要做耳朵透光再单独画。
Properties.g = specular               由 RMO 的 roughness 反推：越粗糙越不反光
Properties.b = RMO.g (metalness)      直接搬
Properties.a = RMO.r (roughness)      直接搬

Normal.g = 源 R (X)                   RRxG 打包：X 进 G
Normal.a = 255 - 源 G                 Y 进 A，且 shader 里会再取反一次，
                                      所以这里先反一次，两次抵消 = 保持原朝向
Normal.r = Normal.b = 源 R            R/B 是冗余位（RRxG 的两个 R），填 X 即可
```

## 二次元风格的取向

写实渲染靠 roughness 变化做细节；二次元要的是**大面积均匀、低高光**。
所以这里不直接照搬源 roughness，而是把它压进一个窄区间（见 `ANIME_ROUGH_*`），
金属度整体压低（`ANIME_METAL_SCALE`）。这样既保留布料/金属的相对差异，
又不会出现写实的油光。真正的卡渲（色阶化 + 描边）要改 shader，属后续工作。

没有源 RMO 的部件（脸、头发、眼睛、extra）走 `FLAT_PROFILES` 里的常量档，
直接生成纯色 properties 图，比留白图正确得多。

用法:
    python tools/textures/build_moe_properties_maps.py
输出到各模型子 mod 的 gfx/models/portraits/moe/ 下的 PNG（路径由 moe_paths 按角色解析），
随后由 build_moe_mod.py 的 DDS 步骤统一转换。
"""
from pathlib import Path

import sys
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / _d) for _d in ("lib", "anim")]   # 底座在 lib/，写回底座在 anim/
import moe_paths as MP    # noqa: E402  资源路径解析（子 mod 感知）
SRC = (ROOT / "assets" / "source_models" / "芙洛伦原皮_by_少女前线2：追放_f3fd2b24a00db979049c32381e901b46"
       / "芙洛伦原皮" / "Textures")
NORMAL_SRC = SRC / "normalmap"
# 单体目录已退役（2026-08-29）：产物按子 mod 直写 mod/<mod_id>/。
# MP.BASE 解析到第一个存在的模型包；**定位具体文件用 MP.find()**，
# 它会遍历全部子 mod（flo 在 gfl2 包、lau/per/eld 在 endfield 包）。
OUT = MP.BASE

# 二次元取向的重映射参数。
# roughness 压进窄区间：高光小而集中，不会像写实那样一片油光。
ANIME_ROUGH_MIN = 0.45
ANIME_ROUGH_MAX = 0.75
# 金属度整体压低。源素材里金属饰件本来就少，压到三成足够表现差异。
ANIME_METAL_SCALE = 0.30
# 次表面散射掩码。二次元皮肤不要写实肉透光，给 0。
SCATTER = 0

# 有源 RMO/normal 的部件：输出名前缀 -> (rmo 文件, normal 文件)
#
# 输出名前缀必须等于 **diffuse 贴图的 stem**（含 _d / _da 后缀），
# 因为 build_moe_mod.py 的 asset_text() 是按 `Path(diffuse).stem` 拼
# `{stem}_properties.png` / `{stem}_normal.png` 去查文件是否存在的。
# 名字对不上就静默不写 texture_specular/texture_normal，白图金属反光照旧。
RMO_PAIRS = {
    "c_FlorenceSSR01_slg_body_d": ("c_FlorenceSSR01_slg_body_rmo.png",
                                   "c_FlorenceSSR01_slg_body_n.png"),
    "c_FlorenceSSR01_slg_cloth1_da": ("c_florencessr01_slg_cloth1_rmo_05_08_2025.png",
                                      "c_FlorenceSSR01_slg_cloth1_n.png"),
    "c_FlorenceSSR01_slg_cloth2_da": ("c_florencessr01_slg_cloth2_rmo_05_08_2025.png",
                                      "c_FlorenceSSR01_slg_cloth2_n.png"),
    "c_FlorenceSSR01_slg_weapon_d": ("c_florencessr01_slg_weapon_rmo_05_08_2025.png",
                                     "c_FlorenceSSR01_slg_weapon_n.png"),
    "cw_FlorenceSR01_WL_d": ("cw_FlorenceSR01_WL_rmo.png",
                             "cw_FlorenceSR01_WL_n.png"),
}

# 没有源 RMO 的部件用常量档。(roughness, metalness, spec)
# 都是 0~1，按材质语义给：
#   脸/头发   —— 哑光，几乎不反光，避免二次元脸上出现写实高光点
#   眼睛      —— 唯一允许亮高光的地方，眼神光是二次元的重点
#   eyeblend / extra —— 叠加层，不该产生自己的高光
# 键名同样必须是 diffuse 的 stem（见 RMO_PAIRS 注释）。
FLAT_PROFILES = {
    "c_Florence_slg_face_d": (0.70, 0.0, 0.25),
    "c_Florence_slg_hair_d": (0.65, 0.0, 0.30),
    # 虹膜的粗糙度**不能低于 0.32**（2026-08-26 实机+像素实测）。
    # 原值 0.15 的 GGX 高光斑半角只有 12.18°，而虹膜网格是近平面
    # （法线偏离均值最大 洛茜 23.57° / 芙洛伦 25.46°）。斑比扇面窄 ⇒
    # 整片虹膜的法线几乎**同时**进出高光斑，没有中间状态：换个动画
    # 姿态一变就从正常翻成两个正圆白盘（HDR 溢出，边缘逐通道钳位出
    # 黄→绿→红的假彩虹）。洛茜 idle:incapable 实拍到 4285/4126 像素、
    # 长宽比 1.03/0.99 的正圆 —— 正圆是**高光斑的形状**，虹膜本身是杏仁形。
    # 0.38 让斑半角 31.7° > 扇面，仍比原版 male_eyes_properties.dds 的
    # 0.405 略锐，眼神光保住。芙洛伦当初看不出来是因为 eyeblend 层
    # （粗糙度 0.80）盖在虹膜前面挡住了，不是她的配置更对。
    "c_Florence_slg_eye_d": (0.38, 0.0, 0.55),
    "c_Florence_slg_eyeblend": (0.80, 0.0, 0.10),
    "extra": (0.80, 0.0, 0.10),
}


def spec_from_roughness(rough: np.ndarray) -> np.ndarray:
    """由 roughness 反推 specular 强度：越粗糙越不反光。

    shader 里 spec 会再过一次 RemapSpec()，所以这里只需给相对趋势，
    不用追求物理精确。线性反相后压到 [0.15, 0.5]，
    上限刻意压低 —— 二次元不需要写实的强镜面。
    """
    return 0.15 + (1.0 - rough) * 0.35


def load01(path: Path) -> np.ndarray:
    """读图并归一化到 0~1 的 float32 RGBA。"""
    with Image.open(path) as im:
        return np.asarray(im.convert("RGBA"), dtype=np.float32) / 255.0


def to_u8(a: np.ndarray) -> np.ndarray:
    return np.clip(a * 255.0 + 0.5, 0, 255).astype(np.uint8)


def write_properties(stem: str, rough: np.ndarray, metal: np.ndarray,
                     spec: np.ndarray, size: tuple[int, int]):
    """按 CK3 布局写 properties 图：R=scatter G=spec B=metal A=rough。"""
    h, w = size
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[..., 0] = SCATTER
    out[..., 1] = to_u8(spec)
    out[..., 2] = to_u8(metal)
    out[..., 3] = to_u8(rough)
    path = MP.dest(f"{stem}_properties.png")
    Image.fromarray(out, "RGBA").save(path)
    print(f"  {path.name:<46} {w}x{h}  "
          f"rough {rough.mean():.2f} metal {metal.mean():.2f} spec {spec.mean():.2f}")
    return path


def write_normal_rrxg(stem: str, src_normal: Path):
    """把标准切空间法线重打包成 CK3 的 RRxG 布局。

    UnpackRRxGNormal 取 g 当 X、a 当 Y 并对 Y 取反。
    所以这里 A 通道先存 (1 - 源G)，与 shader 的取反抵消，保持原朝向。
    """
    n = load01(src_normal)
    x = n[..., 0]
    y = n[..., 1]
    h, w = x.shape
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[..., 0] = to_u8(x)          # RRxG 的冗余 R
    out[..., 1] = to_u8(x)          # X
    out[..., 2] = to_u8(x)          # 冗余
    out[..., 3] = to_u8(1.0 - y)    # Y（预先取反）
    path = MP.dest(f"{stem}_normal.png")
    Image.fromarray(out, "RGBA").save(path)
    print(f"  {path.name:<46} {w}x{h}")
    return path


def write_flat_normal(stem: str):
    """给没有源法线的部件写一张「中性」RRxG 法线图。

    这一步不能省。没声明 texture_normal 时 NormalMap 采样成不透明白色，
    UnpackRRxGNormal 算出 x=1, y=-(1)= -1, z=sqrt(1-1-1)→0，
    得到一条**完全横向**的切空间法线。横向法线几乎接不到正面光，
    表现就是「脸和头发很暗很脏」—— 这与 color mask 压暗是两个独立的问题，
    必须分别修。

    中性法线是切空间 (0,0,1)：x=0 → g=0.5；y=0 → a=0.5。
    所以 RGBA 全 128。
    """
    out = np.full((4, 4, 4), 128, dtype=np.uint8)
    path = MP.dest(f"{stem}_normal.png")
    Image.fromarray(out, "RGBA").save(path)
    print(f"  {path.name:<46} 4x4 中性法线 (128,128,128,128)")
    return path


def main():
    if not SRC.is_dir():
        raise SystemExit(f"找不到源贴图目录: {SRC}")
    OUT.mkdir(parents=True, exist_ok=True)

    print("从源 RMO 重打包 properties + normal:")
    made = {}
    for stem, (rmo_name, n_name) in RMO_PAIRS.items():
        rmo_path = NORMAL_SRC / rmo_name
        n_path = NORMAL_SRC / n_name
        if not rmo_path.is_file():
            print(f"  !! 缺 RMO: {rmo_name}")
            continue
        rmo = load01(rmo_path)
        rough_src = rmo[..., 0]
        metal_src = rmo[..., 1]

        # 二次元重映射：roughness 压窄区间，metalness 整体压低
        rough = ANIME_ROUGH_MIN + rough_src * (ANIME_ROUGH_MAX - ANIME_ROUGH_MIN)
        metal = metal_src * ANIME_METAL_SCALE
        spec = spec_from_roughness(rough)

        entry = {"properties": write_properties(
            stem, rough, metal, spec, rough.shape)}
        if n_path.is_file():
            entry["normal"] = write_normal_rrxg(stem, n_path)
        else:
            print(f"  !! 缺 normal: {n_name}")
        made[stem] = entry

    print("\n无源 RMO 的部件用常量档 + 中性法线:")
    for stem, (r, m, s) in FLAT_PROFILES.items():
        # 常量图不需要大尺寸，采样出来到处一样。64² 省显存。
        size = (64, 64)
        rough = np.full(size, r, dtype=np.float32)
        metal = np.full(size, m, dtype=np.float32)
        spec = np.full(size, s, dtype=np.float32)
        made[stem] = {
            "properties": write_properties(stem, rough, metal, spec, size),
            "normal": write_flat_normal(stem),
        }

    print(f"\n共生成 {sum(len(v) for v in made.values())} 张图到 {OUT}")
    print("[PROPERTIES MAPS DONE]")
    return made


if __name__ == "__main__":
    main()
