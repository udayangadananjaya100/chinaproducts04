#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Data & Image Extraction Pipeline (website/tools/build.py)
- Parses the source .xlsx file directly using Python standard library (zipfile + xml.etree.ElementTree) and Pillow (PIL).
- Extracts all product rows across all sheets.
- Extracts embedded product images supporting BOTH:
  1. Standard OpenXML floating drawing anchors (xl/drawings/drawing*.xml -> twoCellAnchor / oneCellAnchor)
  2. WPS Office in-cell =DISPIMG("ID_...", 1) formulas mapped via xl/cellimages.xml and xl/_rels/cellimages.xml.rels.
- Deduplicates and converts every extracted image into two optimized WebP files in website/images/:
  - Thumbnail (p001_t.webp, max side 360px, quality 82)
  - Large high-res (p001_l.webp, max side 1100px, quality 85)
- Categorizes products and translates names & variants into English (en), Sinhala (si), Chinese (zh), and Spanish (es).
- Computes Sri Lanka Winning Product Index (SL-WPI) scores via generate_winning_scores.py.
- Outputs window.CATALOG = { ... }; inside website/data/products.js.
"""

import hashlib
import io
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image

# Import SL-WPI scoring engine
sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_winning_scores import evaluate_catalog

NS = {
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}


def col_letters_to_index(col_str: str) -> int:
    """Convert Excel column letters (e.g. 'A' -> 0, 'D' -> 3, 'AA' -> 26)."""
    idx = 0
    for ch in col_str.upper():
        if "A" <= ch <= "Z":
            idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def split_cell_ref(ref: str):
    """Split 'D12' into ('D', 12, 3)."""
    m = re.match(r"^([A-Za-z]+)(\d+)$", ref.strip())
    if not m:
        return "", 0, 0
    col_str, row_str = m.group(1), m.group(2)
    return col_str.upper(), int(row_str), col_letters_to_index(col_str)


def parse_first_float(val, default=None):
    if val is None:
        return default
    s = str(val).strip()
    if not s:
        return default
    m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", s)
    if m and m.group(0) not in ("", "-", "+", "."):
        try:
            return float(m.group(0))
        except ValueError:
            return default
    return default


def parse_box_and_cbm(box_raw: str, cbm_raw=None):
    """Normalize carton dimensions string and compute CBM if not explicitly provided."""
    cbm_val = parse_first_float(cbm_raw, None)
    box_str = str(box_raw or "").strip()
    if box_str:
        # Clean multiple spaces
        box_str = re.sub(r"\s+", " ", box_str)
    if cbm_val is None and box_str:
        # Extract 3 numbers like 58*42.5*49 or 70-55-50
        nums = re.findall(r"\d+(?:\.\d+)?", box_str)
        if len(nums) >= 3:
            l, w, h = float(nums[0]), float(nums[1]), float(nums[2])
            if l > 0 and w > 0 and h > 0:
                cbm_val = round((l * w * h) / 1_000_000.0, 4)
    if cbm_val is not None:
        cbm_val = round(float(cbm_val), 4)
    # Format box display nicely
    if box_str:
        nums = re.findall(r"\d+(?:\.\d+)?", box_str)
        if len(nums) == 3 and re.match(r"^[\d\.\*\-xX\s]+(?:cm|CM)?$", box_str):
            box_str = f"{nums[0]}×{nums[1]}×{nums[2]} cm"
    return box_str, cbm_val


def load_shared_strings(z: zipfile.ZipFile) -> list:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    strings = []
    for si in root.findall("s:si", NS):
        texts = [t.text or "" for t in si.findall(".//s:t", NS)]
        strings.append("".join(texts))
    return strings


def load_wps_cellimages(z: zipfile.ZipFile) -> dict:
    """
    Support WPS Office in-cell =DISPIMG("ID_...", 1) formulas mapped via
    xl/cellimages.xml and xl/_rels/cellimages.xml.rels.
    Returns dict: { "ID_...": "xl/media/imageX.png" }
    """
    id_to_media = {}
    names = set(z.namelist())
    if "xl/cellimages.xml" not in names:
        return id_to_media

    rid_to_target = {}
    rels_path = "xl/_rels/cellimages.xml.rels"
    if rels_path in names:
        rels_root = ET.fromstring(z.read(rels_path))
        for rel in rels_root:
            rid = rel.attrib.get("Id", "")
            target = rel.attrib.get("Target", "")
            if target:
                if not target.startswith("xl/"):
                    target = "xl/" + target.lstrip("/").replace("../", "")
                rid_to_target[rid] = target

    ci_root = ET.fromstring(z.read("xl/cellimages.xml"))
    for pic in ci_root.findall(".//xdr:pic", NS):
        cnv = pic.find(".//xdr:cNvPr", NS)
        blip = pic.find(".//a:blip", NS)
        if cnv is not None and blip is not None:
            img_id = cnv.attrib.get("name", "") or cnv.attrib.get("descr", "")
            embed = blip.attrib.get(f"{{{NS['r']}}}embed", "")
            if img_id and embed in rid_to_target:
                id_to_media[img_id] = rid_to_target[embed]
    return id_to_media


def load_sheet_drawings(z: zipfile.ZipFile, sheet_target: str, merges: list) -> dict:
    """
    Extract floating drawing anchors (twoCellAnchor / oneCellAnchor) for a worksheet.
    Resolves EMU boundary offsets accurately and propagates images across merged cell ranges.
    Returns dict: { row_num (1-based): [ "xl/media/imageX.ext", ... ] }
    """
    names = set(z.namelist())
    sheet_file = sheet_target.split("/")[-1]
    rels_path = f"xl/worksheets/_rels/{sheet_file}.rels"
    row_images = {}
    if rels_path not in names:
        return row_images

    s_rels = ET.fromstring(z.read(rels_path))
    anchors = []

    for rel in s_rels:
        d_target = rel.attrib.get("Target", "")
        if "drawing" not in d_target:
            continue
        d_file = d_target.split("/")[-1]
        d_path = f"xl/drawings/{d_file}"
        d_rels_path = f"xl/drawings/_rels/{d_file}.rels"
        if d_path not in names:
            continue

        d_rid_map = {}
        if d_rels_path in names:
            for drel in ET.fromstring(z.read(d_rels_path)):
                t = drel.attrib.get("Target", "")
                if t:
                    t = "xl/" + t.lstrip("/").replace("../", "")
                    d_rid_map[drel.attrib.get("Id", "")] = t

        d_root = ET.fromstring(z.read(d_path))
        for anchor_type in ("twoCellAnchor", "oneCellAnchor"):
            for anc in d_root.findall(f"xdr:{anchor_type}", NS):
                fr = anc.find("xdr:from", NS)
                to = anc.find("xdr:to", NS)
                if fr is None:
                    continue
                c1 = int(fr.find("xdr:col", NS).text)
                r1 = int(fr.find("xdr:row", NS).text) + 1
                ro1_el = fr.find("xdr:rowOff", NS)
                ro1 = int(ro1_el.text) if ro1_el is not None and ro1_el.text else 0

                if to is not None:
                    c2 = int(to.find("xdr:col", NS).text)
                    r2 = int(to.find("xdr:row", NS).text) + 1
                    ro2_el = to.find("xdr:rowOff", NS)
                    ro2 = int(ro2_el.text) if ro2_el is not None and ro2_el.text else 0
                else:
                    c2, r2, ro2 = c1, r1, 0

                blip = anc.find(".//a:blip", NS)
                if blip is None:
                    continue
                embed = blip.attrib.get(f"{{{NS['r']}}}embed", "")
                media_path = d_rid_map.get(embed, "")
                if not media_path or media_path not in names:
                    continue

                # Determine primary target row using EMU offsets:
                # If an image spans r1 -> r1+1 and starts near the bottom of r1 (ro1 > 500,000 EMUs)
                # while extending deep into r2 (ro2 > 500,000 EMUs), it visually sits in row r2.
                if r2 == r1 + 1 and ro1 > 500_000 and ro2 > 500_000:
                    target_row = r2
                else:
                    target_row = r1

                anchors.append((target_row, c1, r1, r2, c2, media_path))

    # Sort by column then row so primary product column image comes before remarks column image
    anchors.sort(key=lambda x: (x[0], x[1]))
    for target_row, c1, r1, r2, c2, media_path in anchors:
        row_images.setdefault(target_row, [])
        if media_path not in row_images[target_row]:
            row_images[target_row].append(media_path)

    # Propagate images inside merged cell ranges (e.g., D3:D7 in Sheet 1)
    for mref in merges:
        parts = mref.split(":")
        if len(parts) != 2:
            continue
        _, mr1, mc1 = split_cell_ref(parts[0])
        _, mr2, mc2 = split_cell_ref(parts[1])
        if mr2 > mr1:
            # Find any anchor overlapping this merged cell range
            merged_imgs = []
            for target_row, c1, r1, r2, c2, media_path in anchors:
                if mc1 <= c1 <= mc2 and not (r2 < mr1 or r1 > mr2):
                    if media_path not in merged_imgs:
                        merged_imgs.append(media_path)
            if merged_imgs:
                for r in range(mr1, mr2 + 1):
                    row_images.setdefault(r, [])
                    for mp in merged_imgs:
                        if mp not in row_images[r]:
                            row_images[r].append(mp)

    return row_images


class ImageConverter:
    """Deduplicates raw image bytes and generates optimized WebP thumbnail + large files."""

    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.hash_to_id = {}
        self.counter = 0

    def process_image(self, raw_bytes: bytes) -> tuple:
        h = hashlib.md5(raw_bytes).hexdigest()
        if h in self.hash_to_id:
            img_id = self.hash_to_id[h]
            return f"images/{img_id}_t.webp", f"images/{img_id}_l.webp"

        self.counter += 1
        img_id = f"p{self.counter:03d}"
        self.hash_to_id[h] = img_id

        thumb_path = self.out_dir / f"{img_id}_t.webp"
        large_path = self.out_dir / f"{img_id}_l.webp"

        with Image.open(io.BytesIO(raw_bytes)) as im:
            # Convert palette/CMYK to RGBA or RGB cleanly
            if im.mode in ("P", "RGBA", "LA"):
                im_rgba = im.convert("RGBA")
                # Composite onto clean white background for consistent product catalogue appearance
                bg = Image.new("RGB", im_rgba.size, (255, 255, 255))
                bg.paste(im_rgba, mask=im_rgba.split()[3])
                base_im = bg
            else:
                base_im = im.convert("RGB")

            # 1. Thumbnail (max side 360px, quality 82)
            t_im = base_im.copy()
            t_im.thumbnail((360, 360), Image.Resampling.LANCZOS)
            t_im.save(thumb_path, "WEBP", quality=82, method=4)

            # 2. Large high-res (max side 1100px, quality 85)
            l_im = base_im.copy()
            l_im.thumbnail((1100, 1100), Image.Resampling.LANCZOS)
            l_im.save(large_path, "WEBP", quality=85, method=4)

        return f"images/{img_id}_t.webp", f"images/{img_id}_l.webp"


# ---------------------------------------------------------------------------
# Localization & Category Classification Helpers (EN / SI / ZH / ES)
# ---------------------------------------------------------------------------

CATEGORY_META = {
    "gas-trolley": {
        "en": "Gas Cylinder & Planter Trolleys",
        "si": "ගෑස් සිලින්ඩර සහ මල් පෝච්චි රෝද රඳවන (Gas Trolleys)",
        "zh": "煤气瓶与花盆移动底座",
        "es": "Bases Móviles para Cilindros de Gas y Macetas",
    },
    "mobile-base": {
        "en": "Mobile Wheeled Appliance Bases",
        "si": "රෝද සහිත රෙදි සෝදන යන්ත්‍ර සහ ශීතකරණ ආධාරක (Wheeled Bases)",
        "zh": "带轮移动家电底座",
        "es": "Bases Móviles con Ruedas para Electrodomésticos",
    },
    "fixed-base": {
        "en": "Fixed Heavy-Duty Appliance Stands",
        "si": "ස්ථාවර රෙදි සෝදන යන්ත්‍ර සහ ශීතකරණ ආධාරක (Fixed Stands)",
        "zh": "固定多脚家电底座",
        "es": "Soportes Fijos de Alta Resistencia",
    },
    "furniture-mover": {
        "en": "Furniture & Appliance Lifter Movers",
        "si": "ගෘහ භාණ්ඩ සහ උපකරණ එසවුම් රෝද කට්ටල (Furniture Movers)",
        "zh": "重物移动搬家器",
        "es": "Kits de Rodillos para Mover Muebles",
    },
    "riser-pad": {
        "en": "Anti-Vibration & Riser Foot Pads",
        "si": "දෙදරුම් නාශක සහ උස වැඩි කරන පාද කට්ටල (Riser Pads)",
        "zh": "防震增高脚垫与独立脚",
        "es": "Almohadillas Antivibración y Elevadores",
    },
    "ac-deflector": {
        "en": "AC Wind Deflectors & Baffles",
        "si": "වායු සමීකරණ සුළං ආවරණ (AC Wind Deflectors)",
        "zh": "空调挡风板系列",
        "es": "Deflectores de Viento para Aire Acondicionado",
    },
}


def localize_product(raw_zh: str, code: str, sheet: str, size_spec: str, extra: dict) -> dict:
    """
    Classify product into a logical category and generate natural, accurate translations
    for Product Names (zh, en, si, es) and Variant Badges (variantEn, variantSi, variantZh, variantEs).
    """
    zh_clean = re.sub(r"\s+", " ", raw_zh.replace("\n", " ")).strip()
    if zh_clean == "0" and code == "A1919-09":
        zh_clean = "灰色标准8腿4双轮款"
    zh_clean = zh_clean.replace("搬有器", "搬家器")

    size_clean = re.sub(r"\s+", " ", (size_spec or "").replace("\n", " ")).strip()
    load_cap = str(extra.get("loadCapacity", "")).strip()
    tube_spec = str(extra.get("tubeSpec", "")).strip()
    brake_spec = str(extra.get("brake", "")).strip()
    pad_spec = str(extra.get("pad", "")).strip()
    material = str(extra.get("material", "")).strip()
    color_price = str(extra.get("colorPrice", "")).strip()

    # 1. Sheet 2: AC Wind Deflectors (挡风板报价表)
    if sheet == "挡风板报价表" or "挡风板" in zh_clean:
        cat = "ac-deflector"
        style_map = {
            "引流款": (
                "Compact Air Conditioner Wind Deflector Baffle",
                "වායු සමීකරණ සුළං ආවරණය - මූලික මාදිලිය (Compact AC Wind Deflector)",
                "Deflector de Viento Compacto para Aire Acondicionado",
            ),
            "上挂新款": (
                "Top-Hang Universal AC Anti-Direct-Blow Wind Deflector",
                "ඉහළින් රඳවන වායු සමීකරණ සුළං ආවරණය (Top-Hang AC Wind Deflector)",
                "Deflector de Aire Acondicionado de Suspensión Superior",
            ),
            "卡扣款": (
                "Snap-On Clip Universal AC Wind Deflector Shield",
                "ක්ලිප් මගින් සවි කරන වායු සමීකරණ සුළං ආවරණය (Snap-On AC Wind Deflector)",
                "Deflector de Aire Acondicionado con Fijación de Clip",
            ),
            "挂钩连杆": (
                "Hook & Link-Arm Adjustable AC Wind Deflector",
                "කොකු සහ සකස් කළ හැකි අතක් සහිත වායු සමීකරණ සුළං ආවරණය (Hook-Arm AC Deflector)",
                "Deflector de Aire Acondicionado Ajustable con Brazo y Gancho",
            ),
            "上挂连杆": (
                "Top-Mount Link-Arm Adjustable AC Wind Deflector",
                "ඉහළින් සවි කරන සකස් කළ හැකි වායු සමීකරණ සුළං ආවරණය (Top-Link AC Deflector)",
                "Deflector de Aire Acondicionado de Montaje Superior con Brazo",
            ),
            "全透明款": (
                "Crystal Clear Transparent AC Wind Deflector Shield",
                "විනිවිද පෙනෙන වායු සමීකරණ සුළං ආවරණය (Transparent AC Wind Deflector)",
                "Deflector Transparente para Aire Acondicionado",
            ),
            "中央空调": (
                "Central Air Conditioner Ceiling Vent Wind Deflector",
                "මධ්‍යම වායු සමීකරණ සිවිලිම් සුළං ආවරණය (Central AC Ceiling Deflector)",
                "Deflector de Viento para Aire Acondicionado Central de Techo",
            ),
            "伸缩款": (
                "Telescopic Retractable AC Anti-Direct-Blow Wind Deflector",
                "දිග සකස් කළ හැකි (Telescopic) වායු සමීකරණ සුළං ආවරණය (Retractable AC Deflector)",
                "Deflector Telescópico Retráctil para Aire Acondicionado",
            ),
        }
        en_base, si_base, es_base = style_map.get(
            zh_clean,
            (
                f"Universal AC Wind Deflector ({zh_clean})",
                f"වායු සමීකරණ සුළං ආවරණය (AC Wind Deflector)",
                f"Deflector de Viento para Aire Acondicionado",
            ),
        )
        zh_title = f"空调防直吹挡风板 - {zh_clean} ({size_clean}cm)"
        en_title = f"{en_base} ({size_clean}cm)"
        si_title = f"{si_base} - {size_clean}cm"
        es_title = f"{es_base} ({size_clean}cm)"

        mat_en = material.replace("不锈钢管", "Stainless Steel Tube")
        mat_si = material.replace("不锈钢管", "මල නොබැඳෙන වානේ (Stainless Steel)")
        mat_es = material.replace("不锈钢管", "Tubo de Acero Inoxidable")
        v_en = f"{size_clean}cm | {mat_en}"
        v_si = f"ප්‍රමාණය {size_clean}cm | අමුද්‍රව්‍ය: {mat_si}"
        v_zh = f"{size_clean}cm | 材质: {material}"
        v_es = f"{size_clean}cm | Material: {mat_es}"

        return {
            "category": cat,
            "zh": zh_title,
            "en": en_title,
            "si": si_title,
            "es": es_title,
            "variant": v_en,
            "variantEn": v_en,
            "variantSi": v_si,
            "variantZh": v_zh,
            "variantEs": v_es,
        }

    # 2. Gas Cylinder, Flower Pot & Round AC Mobile Trolleys
    if "煤气瓶" in zh_clean or "多功能架" in zh_clean or "圆型空调底座" in zh_clean:
        cat = "gas-trolley"
        if "煤气瓶" in zh_clean:
            zh_title = f"花盆、煤气瓶万向移动底座 ({size_clean})"
            en_title = f"Heavy-Duty Gas Cylinder & Flower Pot Mobile Trolley Base ({size_clean})"
            si_title = f"ගෑස් සිලින්ඩර සහ මල් පෝච්චි රෝද රඳවන ට්‍රොලිය {size_clean} (Gas Cylinder & Planter Trolley)"
            es_title = f"Base Móvil con Ruedas para Cilindro de Gas y Macetas ({size_clean})"
            cp_clean = re.sub(r"\s+", " ", color_price.replace("\n", " / ")).strip()
            v_en = f"Dia {size_clean} | Grey / White ({cp_clean})"
            v_si = f"විෂ්කම්භය {size_clean} | අළු / සුදු (Grey / White - {cp_clean})"
            v_zh = f"直径 {size_clean} | {cp_clean}"
            v_es = f"Diámetro {size_clean} | Gris / Blanco ({cp_clean})"
        elif "多功能架" in zh_clean:
            zh_title = f"多功能可调移动架/煤气瓶饮水机底座 ({code})"
            en_title = f"Adjustable Multi-Function Gas Cylinder, Water Dispenser & Planter Trolley ({code})"
            si_title = f"ප්‍රමාණය සකස් කළ හැකි බහුකාර්ය ගෑස් සිලින්ඩර සහ මල් පෝච්චි රෝද රඳවනය ({code} Multi-Function Gas Trolley)"
            es_title = f"Soporte Móvil Ajustable Multiusos para Cilindro de Gas y Dispensador ({code})"
            v_en = f"Adjustable {size_clean} | Gas Cylinder / Dispenser / Planter"
            v_si = f"ප්‍රමාණය {size_clean} | ගෑස් සිලින්ඩර සහ මල් පෝච්චි සඳහා (Adjustable)"
            v_zh = f"可调尺寸 {size_clean} | 饮水机/花盆/煤气瓶通用"
            v_es = f"Ajustable {size_clean} | Gas / Dispensador / Macetas"
        else:
            is_double = "双轮" in zh_clean
            w_en = "Double-Wheel" if is_double else "Single-Wheel"
            w_si = "ද්විත්ව රෝද (Double-Wheel)" if is_double else "තනි රෝද (Single-Wheel)"
            w_es = "Rueda Doble" if is_double else "Rueda Simple"
            zh_title = f"{zh_clean} ({code})"
            en_title = f"Round Adjustable {w_en} Mobile Base for Cylinder AC & Heavy Planters ({code})"
            si_title = f"රවුම් හැඩැති {w_si} ජංගම ආධාරක ට්‍රොලිය ({code} Round Mobile Trolley Base)"
            es_title = f"Base Móvil Redonda Ajustable de {w_es} para AC y Macetas Pesadas ({code})"
            v_en = f"Adjustable {size_clean} | {w_en} Swivel Casters"
            v_si = f"ප්‍රමාණය {size_clean} | 360° කැරකෙන {w_si}"
            v_zh = f"可调直径 {size_clean} | {'双轮' if is_double else '单轮'}万向款"
            v_es = f"Diámetro {size_clean} | {w_es} Giratoria"

        return {
            "category": cat,
            "zh": zh_title,
            "en": en_title,
            "si": si_title,
            "es": es_title,
            "variant": v_en,
            "variantEn": v_en,
            "variantSi": v_si,
            "variantZh": v_zh,
            "variantEs": v_es,
        }

    # 3. Furniture & Appliance Lifter Movers (搬家器)
    if "搬家器" in zh_clean or "五轮款" in zh_clean:
        cat = "furniture-mover"
        mover_map = {
            "HL-002": (
                "Compact 5-Piece Furniture & Appliance Lifter Roller Set",
                "ගෘහ භාණ්ඩ සහ උපකරණ එසවුම් රෝද කට්ටලය - කොටස් 5 (Compact Furniture Lifter Set)",
                "Kit Compacto de 5 Piezas con Palanca y Rodillos para Mover Muebles",
            ),
            "HL-001": (
                "Extendable Twin-Track Appliance & Furniture Roller Mover (45–70cm)",
                "දිග සකස් කළ හැකි (45–70cm) ගෘහ භාණ්ඩ සහ ශීතකරණ රෝද ආධාරක යුවළ (Extendable Track Mover)",
                "Par de Rieles Extensibles con Ruedas para Mover Electrodomésticos (45–70cm)",
            ),
            "BJ-801": (
                "Small Square Metal Furniture Lifter & Appliance Mover Set with Pry Bar",
                "කුඩා හතරැස් ලෝහමය ගෘහ භාණ්ඩ එසවුම් සහ රෝද කට්ටලය (Small Square Metal Mover Set)",
                "Kit Metálico Cuadrado Pequeño para Mover Muebles con Palanca",
            ),
            "BJ-802": (
                "Large Square Metal Heavy-Duty Furniture Lifter & Mover Set",
                "විශාල හතරැස් ලෝහමය බරැති ගෘහ භාණ්ඩ එසවුම් රෝද කට්ටලය (Large Square Metal Mover Set)",
                "Kit Metálico Cuadrado Grande de Alta Resistencia para Mover Muebles",
            ),
            "BJ-803": (
                "Extra-Large Thickened Metal Square Furniture & Machine Mover Set",
                "අධික ඝනකමැති විශාල ලෝහමය ගෘහ භාණ්ඩ සහ යන්ත්‍ර එසවුම් රෝද කට්ටලය (XL Heavy Metal Mover Set)",
                "Kit Metálico Cuadrado Extragrande Reforzado para Maquinaria y Muebles",
            ),
            "BJ-804": (
                "Reinforced ABS Triangle Corner Furniture Lifter & Roller Set",
                "ශක්තිමත් ABS ත්‍රිකෝණාකාර ගෘහ භාණ්ඩ එසවුම් රෝද කට්ටලය (ABS Triangle Furniture Mover)",
                "Kit Triangular de ABS Reforzado con Palanca para Mover Muebles",
            ),
            "BJ-805": (
                "Large Triangle All-Metal Heavy-Duty Furniture Lifter & Mover Set",
                "විශාල ත්‍රිකෝණාකාර ලෝහමය ගෘහ භාණ්ඩ එසවුම් රෝද කට්ටලය (Large Metal Triangle Mover)",
                "Kit Triangular Metálico Grande de Alta Resistencia para Muebles",
            ),
            "BJ-806": (
                "Compact Triangle Metal Furniture Lifter & Appliance Roller Set",
                "කුඩා ත්‍රිකෝණාකාර ලෝහමය ගෘහ භාණ්ඩ එසවුම් රෝද කට්ටලය (Compact Metal Triangle Mover)",
                "Kit Triangular Metálico Compacto con Palanca para Mover Muebles",
            ),
            "BJ-807": (
                "Small Square 5-Wheel 360° Furniture Lifter & Appliance Mover Set",
                "රෝද 5 කුඩා හතරැස් 360° ගෘහ භාණ්ඩ එසවුම් කට්ටලය (Small 5-Wheel Square Mover Set)",
                "Kit Cuadrado Pequeño de 5 Ruedas 360° para Mover Muebles",
            ),
            "BJ-808": (
                "Large Square 5-Wheel Heavy-Duty Furniture & Fridge Mover Set",
                "රෝද 5 විශාල හතරැස් බරැති ගෘහ භාණ්ඩ සහ ශීතකරණ එසවුම් කට්ටලය (Large 5-Wheel Mover Set)",
                "Kit Cuadrado Grande de 5 Ruedas para Mover Refrigeradores y Muebles",
            ),
            "BJ-809": (
                "Extra-Large Industrial 5-Wheel Square Furniture & Equipment Mover Set",
                "රෝද 5 අති විශාල කාර්මික ගෘහ භාණ්ඩ සහ උපකරණ එසවුම් කට්ටලය (XL 5-Wheel Industrial Mover)",
                "Kit Industrial Extragrande de 5 Ruedas para Equipos y Muebles Pesados",
            ),
        }
        en_base, si_base, es_base = mover_map.get(
            code,
            (
                f"Heavy-Duty Furniture & Appliance Lifter Mover ({code})",
                f"ගෘහ භාණ්ඩ සහ උපකරණ එසවුම් රෝද කට්ටලය ({code} Furniture Mover)",
                f"Kit de Rodillos con Palanca para Mover Muebles ({code})",
            ),
        )
        zh_title = f"{zh_clean} ({code})"
        en_title = f"{en_base} ({code})"
        si_title = f"{si_base} [{code}]"
        es_title = f"{es_base} ({code})"

        v_en = f"Pad Size {size_clean} | Includes Lifter Crowbar" if "BJ-" in code else f"Size {size_clean} | Twin Roller Set"
        v_si = f"ප්‍රමාණය {size_clean} | එසවුම් ලීවරය සහිතයි (With Crowbar)" if "BJ-" in code else f"ප්‍රමාණය {size_clean} | රෝද ආධාරක කට්ටලය"
        v_zh = f"规格 {size_clean} | 含撬杆四轮套装" if "BJ-" in code else f"规格 {size_clean} | 移动滑轮组"
        v_es = f"Tamaño {size_clean} | Incluye Palanca Elevadora" if "BJ-" in code else f"Tamaño {size_clean} | Juego de Rodillos"

        return {
            "category": cat,
            "zh": zh_title,
            "en": en_title,
            "si": si_title,
            "es": es_title,
            "variant": v_en,
            "variantEn": v_en,
            "variantSi": v_si,
            "variantZh": v_zh,
            "variantEs": v_es,
        }

    # 4. Anti-Vibration & Height-Riser Foot Pads (独立脚 / 脚垫 / 增高脚 / 大象脚)
    if any(k in zh_clean for k in ("独立脚", "脚垫", "增高脚", "大象脚")):
        cat = "riser-pad"
        pad_map = {
            "D-01": (
                "Small Independent Washing Machine Riser Stand Legs (4 Pcs/Set)",
                "රෙදි සෝදන යන්ත්‍ර උස වැඩි කරන කුඩා පාද 4ක කට්ටලය (Small Riser Legs 4pcs/Set)",
                "Juego de 4 Patas Elevadoras Pequeñas Independientes para Lavadora",
            ),
            "D-02": (
                "Medium Independent Washing Machine Riser Stand Legs (4 Pcs/Set)",
                "රෙදි සෝදන යන්ත්‍ර උස වැඩි කරන මධ්‍යම ප්‍රමාණයේ පාද 4ක කට්ටලය (Medium Riser Legs 4pcs)",
                "Juego de 4 Patas Elevadoras Medianas Independientes para Lavadora",
            ),
            "D-03": (
                "Large Heavy-Duty Independent Washing Machine Stand Legs (4 Pcs/Set)",
                "රෙදි සෝදන යන්ත්‍ර සහ ශීතකරණ සඳහා විශාල ශක්තිමත් පාද 4ක කට්ටලය (Large Riser Legs 4pcs)",
                "Juego de 4 Patas Elevadoras Grandes de Alta Resistencia para Lavadora",
            ),
            "FP-01": (
                "Multi-Function Anti-Vibration Furniture & Appliance Foot Pads (4 Pcs/Set)",
                "දෙදරුම් නාශක (Anti-Vibration) බහුකාර්ය ගෘහ භාණ්ඩ සහ යන්ත්‍ර පාද කට්ටලය (4 Pcs/Set)",
                "Juego de 4 Almohadillas Antivibración Multiusos para Electrodomésticos",
            ),
            "FP-02": (
                "Single-Layer 4cm Height Riser Anti-Vibration Washing Machine Pads (4 Pcs/Set)",
                "4cm උස වැඩි කරන තනි තට්ටුවේ දෙදරුම් නාශක පාද කට්ටලය (4cm Single-Layer Riser Pads)",
                "Juego de 4 Almohadillas Elevadoras Antivibración de 1 Capa (4cm)",
            ),
            "FP-03": (
                "Double-Layer 6.5cm Stackable Height Riser Anti-Vibration Pads (4 Pcs/Set)",
                "6.5cm උස වැඩි කරන ද්විත්ව තට්ටුවේ දෙදරුම් නාශක පාද කට්ටලය (6.5cm Double-Layer Riser Pads)",
                "Juego de 4 Almohadillas Elevadoras Apilables Antivibración de 2 Capas (6.5cm)",
            ),
            "FP-04": (
                "Triple-Layer 9cm Stackable Height Riser Anti-Vibration Pads (4 Pcs/Set)",
                "9cm උස වැඩි කරන තෙමහල් දෙදරුම් නාශක පාද කට්ටලය (9cm Triple-Layer Riser Pads)",
                "Juego de 4 Almohadillas Elevadoras Apilables Antivibración de 3 Capas (9cm)",
            ),
            "F4-01": (
                "3.5cm Heavy-Duty Rubber Anti-Vibration Washing Machine Riser Pads (500kg, 4 Pcs)",
                "3.5cm රබර් අඩිය සහිත 500kg දෙදරුම් නාශක රෙදි සෝදන යන්ත්‍ර පාද කට්ටලය (3.5cm Riser Pads)",
                "Juego de 4 Elevadores Antivibración de Goma de 3.5cm para Lavadora (500kg)",
            ),
            "F4-02": (
                "6cm Heavy-Duty Rubber Anti-Vibration Washing Machine Riser Pads (500kg, 4 Pcs)",
                "6cm රබර් අඩිය සහිත 500kg දෙදරුම් නාශක රෙදි සෝදන යන්ත්‍ර පාද කට්ටලය (6cm Riser Pads)",
                "Juego de 4 Elevadores Antivibración de Goma de 6cm para Lavadora (500kg)",
            ),
            "F4-03": (
                "8.5cm Extra-High Rubber Anti-Vibration Washing Machine Riser Pads (500kg, 4 Pcs)",
                "8.5cm උස රබර් අඩිය සහිත 500kg දෙදරුම් නාශක රෙදි සෝදන යන්ත්‍ර පාද කට්ටලය (8.5cm Riser Pads)",
                "Juego de 4 Elevadores Antivibración de Goma de 8.5cm para Lavadora (500kg)",
            ),
            "FX-01": (
                "3.5cm Suction-Cup Grip Anti-Vibration Washing Machine Foot Pads (500kg, 4 Pcs)",
                "3.5cm සක්ෂන් කප් (Suction-Cup) සහිත ලිස්සා නොයන දෙදරුම් නාශක පාද කට්ටලය (500kg)",
                "Juego de 4 Almohadillas Antivibración de 3.5cm con Ventosa Antideslizante (500kg)",
            ),
            "FD01": (
                "Heavy-Duty 'Elephant Foot' 1000kg Drum Washing Machine Anti-Vibration Stand (9–10cm)",
                "1000kg අධික බර දැරිය හැකි 'ඇත් පාද' (Elephant Foot) ඩ්‍රම් රෙදි සෝදන යන්ත්‍ර ආධාරකය (9–10cm)",
                "Soporte Antivibración 'Pata de Elefante' de 1000kg para Lavadora (9–10cm)",
            ),
        }
        en_base, si_base, es_base = pad_map.get(
            code,
            (
                f"Anti-Vibration Appliance Riser Foot Pads ({code})",
                f"දෙදරුම් නාශක සහ උස වැඩි කරන පාද කට්ටලය ({code} Riser Pads)",
                f"Almohadillas Elevadoras Antivibración ({code})",
            ),
        )
        zh_title = f"{zh_clean} ({code})"
        en_title = f"{en_base} ({code})"
        si_title = f"{si_base} [{code}]"
        es_title = f"{es_base} ({code})"

        v_parts_en = ["4 Pcs/Set"]
        v_parts_si = ["කෑලි 4ක කට්ටලය (4 Pcs/Set)"]
        v_parts_zh = ["4个一套"]
        v_parts_es = ["4 Pzs/Juego"]
        if size_clean:
            v_parts_en.append(f"Height {size_clean}")
            v_parts_si.append(f"උස {size_clean}")
            v_parts_zh.append(f"高度 {size_clean}")
            v_parts_es.append(f"Altura {size_clean}")
        if load_cap:
            v_parts_en.append(f"Max Load {load_cap}")
            v_parts_si.append(f"උපරිම බර {load_cap}")
            v_parts_zh.append(f"承重 {load_cap}")
            v_parts_es.append(f"Carga {load_cap}")

        return {
            "category": cat,
            "zh": zh_title,
            "en": en_title,
            "si": si_title,
            "es": es_title,
            "variant": " | ".join(v_parts_en),
            "variantEn": " | ".join(v_parts_en),
            "variantSi": " | ".join(v_parts_si),
            "variantZh": " | ".join(v_parts_zh),
            "variantEs": " | ".join(v_parts_es),
        }

    # 5. Washing Machine & Refrigerator Bases (Wheeled Mobile vs Fixed Stand)
    has_wheels = ("轮" in zh_clean) or (brake_spec == "带刹")
    cat = "mobile-base" if has_wheels else "fixed-base"

    # Parse structural features from Chinese name & specs
    tier_en, tier_si, tier_es = "Standard", "සම්මත (Standard)", "Estándar"
    if "豪华" in zh_clean:
        tier_en, tier_si, tier_es = "Deluxe Heavy-Duty", "සුඛෝපභෝගී ශක්තිමත් (Deluxe)", "Deluxe Reforzado"
    elif "加强双管" in zh_clean:
        tier_en, tier_si, tier_es = "Reinforced Twin-Tube", "ද්විත්ව නළ ශක්තිමත් කළ (Twin-Tube)", "Doble Tubo Reforzado"
    elif "灰色加强" in zh_clean or "加强" in zh_clean:
        tier_en, tier_si, tier_es = "Reinforced Square-Tube", "ශක්තිමත් හතරැස් නළ (Reinforced)", "Tubo Cuadrado Reforzado"
    elif "灰色升级" in zh_clean or "升级" in zh_clean:
        tier_en, tier_si, tier_es = "Upgraded Flat-Tube", "වැඩිදියුණු කළ පැතලි නළ (Upgraded)", "Tubo Plano Mejorado"
    elif "灰色标准" in zh_clean:
        tier_en, tier_si, tier_es = "Standard Grey", "සම්මත අළු (Standard Grey)", "Estándar Gris"
    elif "80*96" in size_clean:
        tier_en, tier_si, tier_es = "Commercial XL (80×96cm)", "වාණිජ විශාල ප්‍රමාණයේ (Commercial 80×96cm)", "Comercial XL (80×96cm)"

    # Wheel & Leg configuration
    cfg_en, cfg_si, cfg_es = [], [], []
    if "恩驰万象轮" in zh_clean:
        cfg_en.append("360° Omni-Swivel Wheels")
        cfg_si.append("360° කැරකෙන රෝද (Omni-Swivel Wheels)")
        cfg_es.append("Ruedas Giratorias 360°")
    else:
        if "4双轮" in zh_clean:
            cfg_en.append("4 Double-Wheels")
            cfg_si.append("ද්විත්ව රෝද 4 (4 Double-Wheels)")
            cfg_es.append("4 Ruedas Dobles")
        elif "4单轮" in zh_clean:
            cfg_en.append("4 Single-Wheels")
            cfg_si.append("තනි රෝද 4 (4 Single-Wheels)")
            cfg_es.append("4 Ruedas Simples")

        if "12腿" in zh_clean or "12脚" in zh_clean:
            cfg_en.append("12 Stationary Legs")
            cfg_si.append("ස්ථාවර පාද 12 (12 Legs)")
            cfg_es.append("12 Patas")
        elif "8腿" in zh_clean or "8脚" in zh_clean:
            cfg_en.append("8 Legs")
            cfg_si.append("පාද 8 (8 Legs)")
            cfg_es.append("8 Patas")
        elif "4腿" in zh_clean or "4脚" in zh_clean or "4 脚" in zh_clean:
            cfg_en.append("4 Legs")
            cfg_si.append("පාද 4 (4 Legs)")
            cfg_es.append("4 Patas")

        if "2中双轮" in zh_clean:
            cfg_en.append("2 Center Double-Wheels")
            cfg_si.append("මැද ද්විත්ව රෝද 2 (2 Center Wheels)")
            cfg_es.append("2 Ruedas Centrales")
        elif "2中撑脚" in zh_clean or "中撑脚" in zh_clean:
            cfg_en.append("Center Support Legs")
            cfg_si.append("මැද ආධාරක පාද (Center Support Legs)")
            cfg_es.append("Patas Centrales de Apoyo")

    cfg_str_en = " + ".join(cfg_en) if cfg_en else "Adjustable Frame"
    cfg_str_si = " සහ ".join(cfg_si) if cfg_si else "සකස් කළ හැකි රාමුව"
    cfg_str_es = " + ".join(cfg_es) if cfg_es else "Marco Ajustable"

    if has_wheels:
        en_title = f"{tier_en} {cfg_str_en} Mobile Washing Machine & Fridge Base ({code})"
        si_title = f"{tier_si} {cfg_str_si} සහිත ජංගම රෙදි සෝදන යන්ත්‍ර සහ ශීතකරණ ආධාරක ට්‍රොලිය ({code} Washing Machine & Fridge Stand)"
        es_title = f"Base Móvil {tier_es} con {cfg_str_es} para Lavadora y Refrigerador ({code})"
    else:
        en_title = f"{tier_en} {cfg_str_en} Fixed Washing Machine & Refrigerator Stand ({code})"
        si_title = f"{tier_si} {cfg_str_si} සහිත ස්ථාවර රෙදි සෝදන යන්ත්‍ර සහ ශීතකරණ ආධාරකය ({code} Fixed Appliance Stand)"
        es_title = f"Soporte Fijo {tier_es} de {cfg_str_es} para Lavadora y Refrigerador ({code})"

    zh_title = f"{zh_clean}洗衣机/冰箱底座 ({code})"

    # Build localized variant badges
    v_en_list = [f"Size {size_clean}cm" if size_clean and "cm" not in size_clean.lower() else f"Size {size_clean}"]
    v_si_list = [f"ප්‍රමාණය {size_clean}cm" if size_clean and "cm" not in size_clean.lower() else f"ප්‍රමාණය {size_clean}"]
    v_zh_list = [f"尺寸 {size_clean}"]
    v_es_list = [f"Tamaño {size_clean}"]

    if load_cap:
        v_en_list.append(f"Load {load_cap}")
        v_si_list.append(f"උපරිම බර {load_cap}")
        v_zh_list.append(f"承重 {load_cap}")
        v_es_list.append(f"Carga {load_cap}")
    if has_wheels:
        v_en_list.append("Brake Lock Wheels")
        v_si_list.append("තිරිංග සහිත රෝද (Brake Wheels)")
        v_zh_list.append("带刹车轮")
        v_es_list.append("Ruedas con Freno")
    else:
        v_en_list.append("Anti-Slip Fixed Feet")
        v_si_list.append("ලිස්සා නොයන ස්ථාවර පාද (Fixed Feet)")
        v_zh_list.append("防滑固定脚")
        v_es_list.append("Patas Fijas Antideslizantes")

    return {
        "category": cat,
        "zh": zh_title,
        "en": en_title,
        "si": si_title,
        "es": es_title,
        "variant": " | ".join(v_en_list),
        "variantEn": " | ".join(v_en_list),
        "variantSi": " | ".join(v_si_list),
        "variantZh": " | ".join(v_zh_list),
        "variantEs": " | ".join(v_es_list),
    }


def extract_all_products(xlsx_path: Path, out_images_dir: Path) -> list:
    converter = ImageConverter(out_images_dir)
    products = []

    with zipfile.ZipFile(xlsx_path, "r") as z:
        names = set(z.namelist())
        shared_strings = load_shared_strings(z)
        wps_images = load_wps_cellimages(z)

        wb_root = ET.fromstring(z.read("xl/workbook.xml"))
        wb_rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rid_to_target = {rel.attrib["Id"]: rel.attrib["Target"] for rel in wb_rels}

        for sheet_el in wb_root.findall(".//s:sheet", NS):
            sname = sheet_el.attrib.get("name", "").strip()
            rid = sheet_el.attrib.get(f"{{{NS['r']}}}id", "")
            target = rid_to_target.get(rid, "")
            if not target.startswith("xl/"):
                target = "xl/" + target.lstrip("/")
            if target not in names:
                continue

            s_root = ET.fromstring(z.read(target))
            merges = [m.attrib.get("ref", "") for m in s_root.findall(".//s:mergeCell", NS)]
            row_drawings = load_sheet_drawings(z, target, merges)

            # Parse all cells by row_num -> { col_letter: val, "_dispimg": [media_paths] }
            rows_dict = {}
            for row_el in s_root.findall(".//s:row", NS):
                r_num = int(row_el.attrib.get("r", "0"))
                r_data = {"_dispimg": []}
                for c_el in row_el.findall("s:c", NS):
                    c_ref = c_el.attrib.get("r", "")
                    col_let, _, _ = split_cell_ref(c_ref)
                    c_type = c_el.attrib.get("t", "")
                    v_el = c_el.find("s:v", NS)
                    f_el = c_el.find("s:f", NS)
                    is_el = c_el.find("s:is", NS)

                    val = ""
                    if c_type == "s" and v_el is not None and v_el.text is not None:
                        val = shared_strings[int(v_el.text)]
                    elif c_type == "inlineStr" and is_el is not None:
                        val = "".join([t.text or "" for t in is_el.findall(".//s:t", NS)])
                    elif v_el is not None and v_el.text is not None:
                        val = v_el.text

                    # Check WPS =DISPIMG("ID_...", 1) formula
                    for check_str in (f_el.text if f_el is not None else "", val):
                        if check_str and "DISPIMG" in str(check_str):
                            m_id = re.search(r'DISPIMG\(\s*"([^"]+)"', str(check_str))
                            if m_id and m_id.group(1) in wps_images:
                                r_data["_dispimg"].append(wps_images[m_id.group(1)])

                    if val != "":
                        r_data[col_let] = str(val).strip()
                rows_dict[r_num] = r_data

            # Propagate merged text cells (such as B3:B7, L10:L21, L22:L24, L28:L29 in Sheet 1)
            for mref in merges:
                parts = mref.split(":")
                if len(parts) != 2:
                    continue
                c1_let, mr1, _ = split_cell_ref(parts[0])
                c2_let, mr2, _ = split_cell_ref(parts[1])
                if c1_let == c2_let and mr2 > mr1:
                    top_val = rows_dict.get(mr1, {}).get(c1_let, "")
                    if top_val:
                        for r in range(mr1 + 1, mr2 + 1):
                            if r in rows_dict and not rows_dict[r].get(c1_let):
                                rows_dict[r][c1_let] = top_val

            # Extract product rows according to each sheet's schema
            if sname == "高质量底座1":
                fp_seq = 0
                for r_num in sorted(rows_dict.keys()):
                    if r_num < 3:
                        continue
                    rd = rows_dict[r_num]
                    name_zh = rd.get("B", "")
                    price_raw = rd.get("I", "")
                    pcs_raw = rd.get("F", "")
                    if not name_zh or not price_raw:
                        continue

                    code = rd.get("C", "").strip()
                    size_spec = rd.get("E", "").strip()
                    if not code:
                        if "煤气瓶" in name_zh:
                            num_mm = re.sub(r"\D+", "", size_spec) or str(r_num)
                            code = f"GP-{num_mm}"
                        elif r_num == 26:
                            code = "B25-13B"
                        elif "脚垫" in name_zh:
                            fp_seq += 1
                            code = f"FP-0{fp_seq}"
                            if not size_spec:
                                m_cm = re.search(r"(\d+(?:\.\d+)?\s*CM)", name_zh, re.I)
                                if m_cm:
                                    size_spec = m_cm.group(1).upper()
                        else:
                            code = f"JY-{r_num:02d}"

                    price = parse_first_float(price_raw, 0.0)
                    pcs = int(parse_first_float(pcs_raw, 1) or 1)
                    box_str, cbm_val = parse_box_and_cbm(rd.get("G", ""), rd.get("H", None))

                    # Weight handling: J is unit weight (except J19=3320g, J20=3580g), K is carton gross weight
                    u_kg = parse_first_float(rd.get("J", None), None)
                    if u_kg is not None and u_kg > 100:
                        u_kg = round(u_kg / 1000.0, 3)  # convert grams to kg
                    ctn_kg = parse_first_float(rd.get("K", None), None)
                    if ctn_kg is None and u_kg is not None:
                        ctn_kg = round(u_kg * pcs, 2)

                    extra = {
                        "colorPrice": price_raw if ("灰" in price_raw or "白" in price_raw) else "",
                        "remarks": re.sub(r"\s+", " ", rd.get("L", "")).strip(),
                    }
                    products.append({
                        "_sheet": sname,
                        "_row": r_num,
                        "_raw_zh": name_zh,
                        "code": code,
                        "_size": size_spec,
                        "pcsPerCarton": pcs,
                        "price": round(price, 2),
                        "cbm": cbm_val,
                        "kg": round(ctn_kg, 2) if ctn_kg is not None else None,
                        "unitKg": round(u_kg, 3) if u_kg is not None else None,
                        "box": box_str,
                        "_extra": extra,
                        "_media": row_drawings.get(r_num, []) + rd.get("_dispimg", []),
                    })

            elif sname == "挡风板报价表":
                df_seq = 0
                for r_num in sorted(rows_dict.keys()):
                    if r_num < 13 or r_num > 21:
                        continue
                    rd = rows_dict[r_num]
                    name_zh = rd.get("B", "")
                    price_raw = rd.get("F", "")
                    if not name_zh or not price_raw:
                        continue
                    df_seq += 1
                    code = f"DF-0{df_seq}"
                    size_spec = rd.get("C", "").strip()
                    price = parse_first_float(price_raw, 0.0)
                    pcs = int(parse_first_float(rd.get("G", ""), 50) or 50)
                    grams = parse_first_float(rd.get("H", None), None)
                    u_kg = round(grams / 1000.0, 3) if grams is not None else None
                    ctn_kg = round(u_kg * pcs, 2) if u_kg is not None else None
                    material = rd.get("I", "").strip()

                    products.append({
                        "_sheet": sname,
                        "_row": r_num,
                        "_raw_zh": name_zh,
                        "code": code,
                        "_size": size_spec,
                        "pcsPerCarton": pcs,
                        "price": round(price, 2),
                        "cbm": None,
                        "kg": ctn_kg,
                        "unitKg": u_kg,
                        "box": "",
                        "_extra": {"material": material, "unit": rd.get("E", "套")},
                        "_media": row_drawings.get(r_num, []) + rd.get("_dispimg", []),
                    })

            elif sname == "搬家器报价表":
                for r_num in sorted(rows_dict.keys()):
                    if r_num < 2 or r_num > 10:
                        continue
                    rd = rows_dict[r_num]
                    name_zh = rd.get("A", "")
                    price_raw = rd.get("E", "")
                    if not name_zh or not price_raw:
                        continue
                    raw_code = rd.get("C", "").strip()
                    code = raw_code.replace("--", "-")
                    size_spec = rd.get("D", "").strip()
                    price = parse_first_float(price_raw, 0.0)
                    pcs = int(parse_first_float(rd.get("F", ""), 10) or 10)
                    box_str, cbm_val = parse_box_and_cbm(rd.get("G", ""), rd.get("H", None))
                    u_kg = parse_first_float(rd.get("I", None), None)
                    ctn_kg = parse_first_float(rd.get("J", None), None)
                    if ctn_kg is None and u_kg is not None:
                        ctn_kg = round(u_kg * pcs, 2)

                    products.append({
                        "_sheet": sname,
                        "_row": r_num,
                        "_raw_zh": name_zh,
                        "code": code,
                        "rawCode": raw_code,
                        "_size": size_spec,
                        "pcsPerCarton": pcs,
                        "price": round(price, 2),
                        "cbm": cbm_val,
                        "kg": round(ctn_kg, 2) if ctn_kg is not None else None,
                        "unitKg": round(u_kg, 3) if u_kg is not None else None,
                        "box": box_str,
                        "_extra": {
                            "remarks": "含撬杆价格（无需撬杆每套减10元，单个撬杆重382g）",
                        },
                        "_media": row_drawings.get(r_num, []) + rd.get("_dispimg", []),
                    })

            elif sname == "底座2":
                for r_num in sorted(rows_dict.keys()):
                    if r_num < 2 or r_num in (36, 42):
                        continue
                    rd = rows_dict[r_num]
                    name_zh = rd.get("A", "")
                    code = rd.get("C", "").strip()
                    price_raw = rd.get("D", "")
                    if not code or not price_raw:
                        continue
                    price = parse_first_float(price_raw, 0.0)
                    pcs = int(parse_first_float(rd.get("E", ""), 10) or 10)
                    size_spec = rd.get("J", "").strip()
                    load_cap = rd.get("F", "").strip()
                    u_kg = parse_first_float(rd.get("H", None), None)
                    ctn_kg = round(u_kg * pcs, 2) if u_kg is not None else None
                    box_raw = rd.get("N", "") or rd.get("P", "")
                    box_str, cbm_val = parse_box_and_cbm(box_raw, None)

                    extra = {
                        "loadCapacity": load_cap,
                        "fitMachine": rd.get("G", "").strip(),
                        "tubeSpec": rd.get("I", "").strip(),
                        "brake": rd.get("K", "").strip(),
                        "pad": rd.get("L", "").strip(),
                        "innerBox": rd.get("M", "").strip(),
                    }
                    products.append({
                        "_sheet": sname,
                        "_row": r_num,
                        "_raw_zh": name_zh,
                        "code": code,
                        "_size": size_spec,
                        "pcsPerCarton": pcs,
                        "price": round(price, 2),
                        "cbm": cbm_val,
                        "kg": ctn_kg,
                        "unitKg": round(u_kg, 3) if u_kg is not None else None,
                        "box": box_str,
                        "_extra": extra,
                        "_media": row_drawings.get(r_num, []) + rd.get("_dispimg", []),
                    })

        # Now process images and localize every product
        final_products = []
        for idx, item in enumerate(products, start=1):
            thumbs = []
            larges = []
            for m_path in item["_media"]:
                if m_path in names:
                    t_rel, l_rel = converter.process_image(z.read(m_path))
                    if t_rel not in thumbs:
                        thumbs.append(t_rel)
                    if l_rel not in larges:
                        larges.append(l_rel)

            loc = localize_product(
                item["_raw_zh"],
                item["code"],
                item["_sheet"],
                item["_size"],
                item["_extra"],
            )
            cat_id = loc["category"]
            cat_meta = CATEGORY_META[cat_id]

            p_obj = {
                "id": f"prod_{idx:03d}",
                "seq": idx,
                "sheet": item["_sheet"],
                "row": item["_row"],
                "code": item["code"],
                "rawCode": item.get("rawCode", item["code"]),
                "variant": loc["variant"],
                "variantEn": loc["variantEn"],
                "variantSi": loc["variantSi"],
                "variantZh": loc["variantZh"],
                "variantEs": loc["variantEs"],
                "zh": loc["zh"],
                "en": loc["en"],
                "si": loc["si"],
                "es": loc["es"],
                "category": cat_id,
                "categoryName": cat_meta,
                "pcsPerCarton": item["pcsPerCarton"],
                "price": item["price"],
                "cbm": item["cbm"],
                "kg": item["kg"],
                "unitKg": item["unitKg"],
                "box": item["box"],
                "specs": item["_extra"],
                "thumb": thumbs[0] if thumbs else "",
                "image": larges[0] if larges else "",
                "thumbs": thumbs,
                "images": larges,
            }
            final_products.append(p_obj)

    return final_products, converter.counter


def find_source_xlsx() -> Path:
    if len(sys.argv) > 1 and Path(sys.argv[1]).exists():
        return Path(sys.argv[1]).resolve()
    candidates = [
        Path(r"G:\gas trolly\GAS rack.xlsx"),
        Path(__file__).resolve().parent.parent.parent / "GAS rack.xlsx",
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    # Search workspace root for any .xlsx
    root_dir = Path(__file__).resolve().parent.parent.parent
    for f in root_dir.glob("*.xlsx"):
        return f.resolve()
    raise FileNotFoundError("Could not find source .xlsx file.")


def main():
    xlsx_path = find_source_xlsx()
    website_dir = Path(__file__).resolve().parent.parent
    images_dir = website_dir / "images"
    data_dir = website_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    print(f"[build.py] Extracting products and images from: {xlsx_path}")
    products, unique_images = extract_all_products(xlsx_path, images_dir)

    # Build category summary list preserving order
    cat_counts = {}
    for p in products:
        cid = p["category"]
        cat_counts[cid] = cat_counts.get(cid, 0) + 1

    categories = []
    for cid, meta in CATEGORY_META.items():
        if cat_counts.get(cid, 0) > 0:
            categories.append({
                "id": cid,
                "en": meta["en"],
                "si": meta["si"],
                "zh": meta["zh"],
                "es": meta["es"],
                "count": cat_counts[cid],
            })

    catalog = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceFile": xlsx_path.name,
        "totalProducts": len(products),
        "totalUniqueImages": unique_images,
        "exchangeRates": {
            "USD": 7.15,   # 1 USD = 7.15 RMB
            "LKR": 43.50,  # 1 RMB = 43.50 LKR
        },
        "categories": categories,
        "products": products,
    }

    # Evaluate SL-WPI 100-point scores for all products
    evaluate_catalog(catalog)

    out_file = data_dir / "products.js"
    out_content = "window.CATALOG = " + json.dumps(catalog, ensure_ascii=False, indent=2) + ";\n"
    out_file.write_text(out_content, encoding="utf-8")

    missing_imgs = sum(1 for p in products if not p["thumb"])
    print(f"[build.py] Extracted {len(products)} products across {len(categories)} categories.")
    print(f"[build.py] Generated {unique_images} deduplicated WebP pairs (thumbnail + large) in {images_dir}.")
    print(f"[build.py] Products missing images: {missing_imgs}")
    print(f"[build.py] SL-WPI Tier Breakdown: {catalog.get('winTierSummary')}")
    print(f"[build.py] Wrote dataset to: {out_file}")


if __name__ == "__main__":
    main()
