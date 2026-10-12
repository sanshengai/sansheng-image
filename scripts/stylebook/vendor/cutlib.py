import subprocess, numpy as np
from PIL import Image, ImageFilter
HERE = __import__("pathlib").Path(__file__).resolve().parent

def vision(src, dst):
    r = subprocess.run([str(HERE / "cutout_bin"), str(src), str(dst)], capture_output=True, text=True)
    return r.returncode == 0 and "OK" in r.stdout, r.stdout.strip()

def key_green(src):
    """颜色键：绿通道显著高于红蓝=背景。软边 + 去绿溢。返回 RGBA Image。"""
    a = np.asarray(Image.open(src).convert("RGB")).astype(np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    gd = g - np.maximum(r, b)              # 绿优势
    # 背景 gd≈110+；前景(橙/棕/蓝/米)基本<20
    alpha = np.clip(1 - (gd - 25) / 60, 0, 1)
    al = Image.fromarray((alpha * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8))
    alpha = np.asarray(al).astype(np.float32) / 255
    # 去绿溢：把 g 限制到 max(r,b)+一点
    g2 = np.minimum(g, np.maximum(r, b) + 6)
    out = np.dstack([r, g2, b, alpha * 255]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")

def trim(im, pad=4):
    bb = im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    x0, y0, x1, y1 = bb
    return im.crop((max(0, x0 - pad), max(0, y0 - pad), min(im.width, x1 + pad), min(im.height, y1 + pad)))

def edge_stats(im):
    """边缘质量：半透明过渡带宽占比、绿溢像素数（边缘 3px 带内 g 明显高于 r,b）、孔洞数近似（内部 alpha<128 的小块）。"""
    a = np.asarray(im).astype(np.float32)
    al = a[..., 3] / 255
    solid = al > 0.5
    ring = (np.asarray(Image.fromarray((solid * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(7))) > 0) & \
           ~(np.asarray(Image.fromarray((solid * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(7))) > 0)
    vis = al > 0.15
    spill = (a[..., 1] - np.maximum(a[..., 0], a[..., 2]) > 14) & vis & ring
    soft = ((al > 0.05) & (al < 0.95)).sum()
    # 内部孔洞：solid 被 MinFilter(21) 腐蚀后的内部里 alpha<0.5 的像素
    inner = np.asarray(Image.fromarray((solid * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(31))) > 0
    holes = int(((al < 0.5) & inner).sum())
    return {"solid_px": int(solid.sum()), "edge_ring_px": int(ring.sum()), "green_spill_px_on_edge": int(spill.sum()),
            "green_spill_frac": round(float(spill.sum() / max(1, ring.sum())), 4), "soft_alpha_px": int(soft), "inner_holes_px": holes}
