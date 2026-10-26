#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sri Lanka Winning Product Index (SL-WPI — 100-Point Sea Cargo Model)
Evaluates every product in the catalogue on a 100-point scale across 4 pillars (0-25 pts each):
  1. Freight & Sea Cargo Efficiency (0-25 pts)
  2. Profit Margin & SL Retail Sweet Spot (0-25 pts)
  3. Local Consumer Demand & Viral Appeal (0-25 pts)
  4. Operational & COD Return Risk (0-25 pts)
"""

import json
import os
import re
from pathlib import Path

LKR_RATE_DEFAULT = 43.50


def score_product(p: dict) -> dict:
    price = float(p.get("price") or 0.0)
    pcs = int(p.get("pcsPerCarton") or 1)
    cbm = p.get("cbm")
    kg = p.get("kg")
    unit_kg = p.get("unitKg")
    cat = p.get("category", "")
    code = str(p.get("code", ""))
    zh = str(p.get("zh", ""))
    variant = str(p.get("variant", ""))

    # Calculate per-unit CBM and per-unit KG (using sensible category estimates only when raw sheet value is null)
    if cbm is not None and cbm > 0 and pcs > 0:
        u_cbm = float(cbm) / pcs
        has_exact_cbm = True
    else:
        has_exact_cbm = False
        if cat == "ac-deflector":
            u_cbm = 0.0016
        elif cat == "gas-trolley":
            u_cbm = 0.0022 if pcs >= 40 else 0.0038
        elif cat == "riser-pad":
            u_cbm = 0.0018 if pcs >= 30 else 0.0080
        else:
            u_cbm = 0.0110

    if unit_kg is not None and unit_kg > 0:
        u_kg = float(unit_kg)
        has_exact_kg = True
    elif kg is not None and kg > 0 and pcs > 0:
        u_kg = float(kg) / pcs
        has_exact_kg = True
    else:
        has_exact_kg = False
        if cat == "ac-deflector":
            u_kg = 0.25
        elif cat == "gas-trolley":
            u_kg = 0.45 if pcs >= 40 else 0.72
        elif cat == "riser-pad":
            u_kg = 0.42
        elif "80*96" in variant or "80*96" in zh:
            u_kg = 3.55
        else:
            u_kg = 2.40

    # -------------------------------------------------------------------------
    # 1. Freight & Sea Cargo Efficiency (0–25 pts)
    # -------------------------------------------------------------------------
    if u_cbm <= 0.0008 and u_kg <= 0.35:
        freight_score = 25
    elif u_cbm <= 0.0018 and u_kg <= 0.40:
        freight_score = 24
    elif u_cbm <= 0.0025 and u_kg <= 0.55:
        freight_score = 23
    elif u_cbm <= 0.0035 and u_kg <= 0.75:
        freight_score = 22
    elif u_cbm <= 0.0055 and u_kg <= 1.35:
        freight_score = 20
    elif u_cbm <= 0.0095 and u_kg <= 1.85:
        freight_score = 18
    elif u_cbm <= 0.0125 and u_kg <= 2.15:
        freight_score = 16
    elif u_cbm <= 0.0135 and u_kg <= 2.55:
        freight_score = 14
    elif u_cbm <= 0.0155 and u_kg <= 2.95:
        freight_score = 12
    elif u_cbm <= 0.0180 and u_kg <= 3.60:
        freight_score = 9
    else:
        freight_score = 7

    cbm_str = f"{u_cbm:.4f} m³/pc" if has_exact_cbm else f"~{u_cbm:.4f} m³/pc (est., {pcs} pcs/ctn)"
    kg_str = f"{u_kg:.2f} kg/pc" if has_exact_kg else f"~{u_kg:.2f} kg/pc (est.)"

    if freight_score >= 22:
        freight_desc = {
            "en": f"Ultra-compact sea cargo footprint ({cbm_str}, {kg_str}, {pcs} pcs/carton) keeps LCL/FCL freight cost per unit minimal.",
            "si": f"ඉතා අඩු නැව්ගත කිරීමේ පරිමාවක් ({cbm_str}, {kg_str}, පෙට්ටියකට කෑලි {pcs}) ඇති බැවින් මුහුදු ප්‍රවාහන (Sea Cargo) ගාස්තුව ඉතා අවම වේ.",
            "zh": f"单件体积与重量极小（{cbm_str}，{kg_str}，{pcs}件/箱），海运拼箱/整柜单件摊销运费极低。",
            "es": f"Volumen y peso unitario ultra compactos ({cbm_str}, {kg_str}, {pcs} pzs/caja), minimizando el flete marítimo por unidad."
        }
    elif freight_score >= 15:
        freight_desc = {
            "en": f"Moderate sea cargo profile ({cbm_str}, {kg_str}, {pcs} pcs/carton) with flat-pack knock-down carton efficiency.",
            "si": f"මධ්‍යම ප්‍රමාණයේ නැව්ගත කිරීමේ පරිමාවක් ({cbm_str}, {kg_str}, පෙට්ටියකට කෑලි {pcs}) සහිත අතර Sea Cargo සඳහා යෝග්‍ය වේ.",
            "zh": f"拆装扁平化包装（{cbm_str}，{kg_str}，{pcs}件/箱），海运空间利用率良好。",
            "es": f"Perfil de carga marítima moderado ({cbm_str}, {kg_str}, {pcs} pzs/caja) con empaque plano eficiente."
        }
    elif freight_score >= 10:
        freight_desc = {
            "en": f"Heavier multi-tube steel construction ({cbm_str}, {kg_str}, {pcs} pcs/carton) increases sea freight and local courier weight tiers.",
            "si": f"බරින් වැඩි වානේ නිමාවක් ({cbm_str}, {kg_str}, පෙට්ටියකට කෑලි {pcs}) ඇති බැවින් නැව් ගාස්තු සහ දේශීය කුරියර් ගාස්තු තරමක් ඉහළ යයි.",
            "zh": f"加厚多管钢材结构较重（{cbm_str}，{kg_str}，{pcs}件/箱），海运及末端派送运费占比上升。",
            "es": f"Estructura de acero más pesada ({cbm_str}, {kg_str}, {pcs} pzs/caja) que incrementa el costo de flete y mensajería local."
        }
    else:
        freight_desc = {
            "en": f"Bulky & heavy commercial unit ({cbm_str}, {kg_str}, {pcs} pcs/carton) incurs high sea cargo CBM charges and costly local delivery.",
            "si": f"විශාල පරිමාවක් සහ වැඩි බරක් ({cbm_str}, {kg_str}, පෙට්ටියකට කෑලි {pcs}) ඇති බැවින් Sea Cargo සහ දේශීය බෙදාහැරීමේ පිරිවැය ඉහළයි.",
            "zh": f"大尺寸重型规格（{cbm_str}，{kg_str}，{pcs}件/箱），海运体积重与本地派送费偏高。",
            "es": f"Unidad comercial voluminosa y pesada ({cbm_str}, {kg_str}, {pcs} pzs/caja) con altos cargos de flete marítimo y entrega."
        }

    # -------------------------------------------------------------------------
    # 2. Profit Margin & SL Retail Sweet Spot (0–25 pts)
    # -------------------------------------------------------------------------
    factory_lkr = round(price * LKR_RATE_DEFAULT)
    if 10.0 <= price <= 31.0:
        margin_score = 24 if price <= 25.0 else 23
    elif 3.5 <= price < 10.0:
        margin_score = 23
    elif 31.0 < price <= 38.0:
        margin_score = 20
    elif 38.0 < price <= 45.0:
        margin_score = 16
    elif 45.0 < price <= 55.0:
        margin_score = 12
    elif 55.0 < price <= 65.0:
        margin_score = 9
    else:
        margin_score = 6

    if margin_score >= 20:
        est_retail_min = max(1850, int(round(factory_lkr * 3.0, -2)))
        est_retail_max = max(2900, int(round(factory_lkr * 4.2, -2)))
        margin_desc = {
            "en": f"Factory cost ¥{price:g} (~LKR {factory_lkr:,}) hits Sri Lanka's COD impulse-buy sweet spot (LKR {est_retail_min:,}–{est_retail_max:,} retail, 3x–4x markup).",
            "si": f"කර්මාන්තශාලා මිල ¥{price:g} (~රු. {factory_lkr:,}) නිසා ශ්‍රී ලංකාවේ COD වෙළඳපොළේ රු. {est_retail_min:,}–{est_retail_max:,} අතර ඉහළ ලාභාංශයක් (3x–4x) සහිතව අලෙවි කළ හැක.",
            "zh": f"出厂价 ¥{price:g}（约 LKR {factory_lkr:,}）处于斯里兰卡 COD 黄金零售区间（建议零售 LKR {est_retail_min:,}–{est_retail_max:,}，毛利空间 3–4 倍）。",
            "es": f"Costo de fábrica ¥{price:g} (~LKR {factory_lkr:,}) ideal para venta COD en Sri Lanka (PVP LKR {est_retail_min:,}–{est_retail_max:,}, margen 3x–4x)."
        }
    elif margin_score >= 14:
        est_retail_min = int(round(factory_lkr * 2.6, -2))
        est_retail_max = int(round(factory_lkr * 3.3, -2))
        margin_desc = {
            "en": f"Factory cost ¥{price:g} (~LKR {factory_lkr:,}) supports solid mid-tier retail pricing around LKR {est_retail_min:,}–{est_retail_max:,} (2.5x–3.2x markup).",
            "si": f"කර්මාන්තශාලා මිල ¥{price:g} (~රු. {factory_lkr:,}) මත රු. {est_retail_min:,}–{est_retail_max:,} පමණ සාධාරණ සිල්ලර මිලකට (2.5x–3.2x ලාභයක් සහිතව) අලෙවි කළ හැක.",
            "zh": f"出厂价 ¥{price:g}（约 LKR {factory_lkr:,}）适合中端定位，建议零售价 LKR {est_retail_min:,}–{est_retail_max:,}（约 2.5–3.2 倍溢价）。",
            "es": f"Costo de fábrica ¥{price:g} (~LKR {factory_lkr:,}) permite un precio minorista sólido de LKR {est_retail_min:,}–{est_retail_max:,} (margen 2.5x–3.2x)."
        }
    else:
        est_retail_min = int(round(factory_lkr * 2.3, -2))
        margin_desc = {
            "en": f"High factory cost ¥{price:g} (~LKR {factory_lkr:,}) pushes landed retail price above LKR {est_retail_min:,}+, compressing COD impulse conversion.",
            "si": f"ඉහළ කර්මාන්තශාලා මිල ¥{price:g} (~රු. {factory_lkr:,}) නිසා සිල්ලර මිල රු. {est_retail_min:,}+ ඉක්මවන බැවින් සාමාන්‍ය COD පාරිභෝගික ඇණවුම් සීමා විය හැක.",
            "zh": f"出厂成本较高 ¥{price:g}（约 LKR {factory_lkr:,}），终端售价需达 LKR {est_retail_min:,}+，压缩了货到付款冲动消费转化率。",
            "es": f"Alto costo de fábrica ¥{price:g} (~LKR {factory_lkr:,}) eleva el precio final por encima de LKR {est_retail_min:,}+, reduciendo compras por impulso."
        }

    # -------------------------------------------------------------------------
    # 3. Local Consumer Demand & Viral Appeal (0–25 pts)
    # -------------------------------------------------------------------------
    is_oversized = ("80*96" in variant or "80*96" in zh)
    is_central_ac = ("中央空调" in zh)

    if cat == "gas-trolley":
        if "JF-02" in code or "煤气瓶" in zh:
            demand_score = 25
            demand_desc = {
                "en": "Essential everyday problem-solver for Sri Lankan kitchens: easily rolls heavy 12.5kg LPG gas cylinders & prevents floor rust rings.",
                "si": "ශ්‍රී ලංකාවේ සෑම නිවසකම මුළුතැන්ගෙයි 12.5kg ගෑස් සිලින්ඩර පහසුවෙන් එහා මෙහා ගෙන යාමට සහ ටයිල් පොළොව මලකඩ කෑම වැළැක්වීමට අත්‍යවශ්‍යම භාණ්ඩයකි.",
                "zh": "斯里兰卡家家户户厨房刚需：轻松移动 12.5kg 液化气罐与大花盆，防止瓷砖地面生锈留痕，社媒短视频爆款。",
                "es": "Solucionador esencial en hogares de Sri Lanka: permite mover cilindros de gas GLP de 12.5kg fácilmente y evita manchas de óxido en pisos."
            }
        else:
            demand_score = 21
            demand_desc = {
                "en": "Strong tropical household & garden demand for moving heavy cylindrical planters, water dispensers, and gas cylinders.",
                "si": "බරැති මල් පෝච්චි, වතුර බෝතල් සහ ගෑස් සිලින්ඩර පහසුවෙන් හැසිරවීමට නිවෙස් හා ගෙවතු සඳහා ඉහළ ඉල්ලුමක් පවතී.",
                "zh": "适用于热带家庭移动大型花盆、饮水机桶及煤气罐，实用性强，受众广泛。",
                "es": "Fuerte demanda doméstica para mover macetas pesadas, dispensadores de agua y cilindros de gas."
            }
    elif cat == "mobile-base":
        if is_oversized:
            demand_score = 10
            demand_desc = {
                "en": "Oversized 80×96cm frame is strictly for commercial double-door freezers & chillers; very limited household buyer base.",
                "si": "80×96cm විශාල ප්‍රමාණයේ රාමුව වාණිජ ද්විත්ව දොර ශීතකරණ (Double-Door Freezers) සඳහා පමණක් ගැළපෙන බැවින් සාමාන්‍ය ගෘහස්ථ ඉල්ලුම අඩුය.",
                "zh": "80×96cm 超大尺寸仅适用于商用双开门冰柜与大型展示柜，普通家庭受众极窄。",
                "es": "Marco extragrande de 80×96cm exclusivo para congeladores comerciales de doble puerta; demanda doméstica muy limitada."
            }
        elif price <= 36.0:
            demand_score = 24 if ("4单轮" in zh or "4双轮" in zh or "万象轮" in zh) and "脚" not in zh else 23
            demand_desc = {
                "en": "High-demand Sri Lankan home upgrade: 360° brake wheels protect washing machines & fridges from wet bathroom/utility floor rust.",
                "si": "තෙතමනය සහිත නාන කාමර හා මුළුතැන්ගෙයි රෙදි සෝදන යන්ත්‍ර සහ ශීතකරණ මලකඩ කෑමෙන් ආරක්ෂා කර පහසුවෙන් පොළොව පිරිසිදු කිරීමට ඉතා ඉහළ ඉල්ලුමක් ඇත.",
                "zh": "斯里兰卡高湿环境刚需：带刹车万向轮方便移动洗衣机与冰箱清扫积水，防止底部生锈腐烂，FB/TikTok 转化极佳。",
                "es": "Alta demanda en Sri Lanka: ruedas con freno protegen lavadoras y refrigeradores de la humedad del piso y facilitan la limpieza."
            }
        elif price <= 47.0:
            demand_score = 18
            demand_desc = {
                "en": "Reinforced multi-leg + wheel base appeals to buyers with heavier front-load drum washing machines and large family refrigerators.",
                "si": "බරින් වැඩි Front-Load රෙදි සෝදන යන්ත්‍ර සහ විශාල ශීතකරණ හිමි පාරිභෝගිකයන් අතර හොඳ ඉල්ලුමක් පවතී.",
                "zh": "加厚多腿带轮复合底座，适合滚筒洗衣机与大容量冰箱用户，兼顾移动与高承重。",
                "es": "Base reforzada de múltiples patas y ruedas ideal para lavadoras de carga frontal pesadas y refrigeradores grandes."
            }
        else:
            demand_score = 13
            demand_desc = {
                "en": "Premium heavy-duty 8-leg double-wheel model targets a smaller segment of buyers with oversized appliances.",
                "si": "අධික බර දැරිය හැකි පාද 8 සහ ද්විත්ව රෝද මාදිලිය විශාල ප්‍රමාණයේ උපකරණ සඳහා පමණක් විශේෂිත වේ.",
                "zh": "豪华重型 8 脚双轮高配款，主要面向大型家电细分客群，大众销量弱于标准款。",
                "es": "Modelo premium de 8 patas y ruedas dobles dirigido a un segmento reducido con electrodomésticos pesados."
            }
    elif cat == "fixed-base":
        if is_oversized:
            demand_score = 9
            demand_desc = {
                "en": "Stationary 80×96cm commercial cabinet stand has low residential demand compared to wheeled models.",
                "si": "රෝද රහිත 80×96cm වාණිජ මාදිලිය සඳහා සාමාන්‍ය නිවෙස්වල ඉල්ලුම ඉතා අඩුය.",
                "zh": "80×96cm 无轮商用固定大底座，家庭需求少，且缺乏移动便利性卖点。",
                "es": "Soporte fijo comercial de 80×96cm sin ruedas con baja demanda residencial."
            }
        elif price <= 29.0:
            demand_score = 20
            demand_desc = {
                "en": "Solid demand from homeowners seeking maximum anti-vibration stability for top/front-load washing machines on uneven floors.",
                "si": "රෙදි සෝදන යන්ත්‍ර ක්‍රියාත්මක වීමේදී දෙදරුම (Vibration) නැති කර උපරිම ස්ථාවරත්වයක් ලබා ගැනීමට නිවෙස් හිමියන් අතර හොඳ ඉල්ලුමක් ඇත.",
                "zh": "无轮多腿固定底座主打强力防震稳固，适合不平整地面放置波轮/滚筒洗衣机。",
                "es": "Demanda sólida de hogares que buscan máxima estabilidad antivibración para lavadoras en pisos irregulares."
            }
        else:
            demand_score = 16
            demand_desc = {
                "en": "8-to-12 leg stationary heavy-duty stand appeals to buyers prioritizing 300–400kg load capacity over mobility.",
                "si": "300–400kg දක්වා අධික බරක් දැරිය හැකි පාද 8/12 ස්ථාවර ආධාරකය දැඩි ශක්තියක් අපේක්ෂා කරන පාරිභෝගිකයන්ට ගැළපේ.",
                "zh": "8 至 12 腿高承重固定款（300–400kg），适合注重极致稳固的重型家电用户。",
                "es": "Soporte fijo de 8 a 12 patas para compradores que priorizan capacidad de 300–400kg sobre movilidad."
            }
    elif cat == "ac-deflector":
        if is_central_ac:
            demand_score = 13
            demand_desc = {
                "en": "Designed for ceiling cassette / central AC vents; mainly B2B office demand rather than household split ACs.",
                "si": "මධ්‍යම වායු සමීකරණ (Central AC) සඳහා නිපදවා ඇති බැවින් සාමාන්‍ය නිවෙස්වලට වඩා කාර්යාල (B2B) සඳහා පමණක් ගැළපේ.",
                "zh": "中央空调出风口专用挡风板，主要面向写字楼与商铺 B2B 采购，普通家庭壁挂机不适用。",
                "es": "Diseñado para aire acondicionado central; demanda principalmente de oficinas B2B más que hogares."
            }
        else:
            demand_score = 22 if price <= 10.0 else 19
            demand_desc = {
                "en": "Year-round tropical demand in Sri Lanka: prevents cold split-AC air from blowing directly onto sleeping children or office desks.",
                "si": "වසර පුරා උණුසුම් කාලගුණයක් ඇති ශ්‍රී ලංකාවේ නිදන කාමර සහ කාර්යාලවල AC සීතල සුළඟ කෙලින්ම ශරීරයට වැදීම වැළැක්වීමට ඉහළ ඉල්ලුමක් ඇත.",
                "zh": "斯里兰卡热带气候全年使用空调，防直吹挡风板在母婴卧室与办公室人群中常年热销。",
                "es": "Demanda todo el año en el clima tropical de Sri Lanka: evita el aire frío directo del AC en dormitorios y oficinas."
            }
    elif cat == "riser-pad":
        demand_score = 22 if price <= 14.0 else 18
        demand_desc = {
            "en": "Universal 4-piece height riser & anti-vibration foot pads fit any washing machine, fridge, sofa, or bed for easy drainage & sweeping.",
            "si": "ඕනෑම රෙදි සෝදන යන්ත්‍රයක්, ශීතකරණයක් හෝ ගෘහ භාණ්ඩයක් ඔසවා තැබීමට සහ දෙදරුම් නැති කිරීමට ගැළපෙන බැවින් ඉතා පහසුවෙන් අලෙවි වේ.",
            "zh": "通用型 4 件套防震增高脚垫，适配各类洗衣机、冰箱与桌椅床脚，方便排水与扫地机进出。",
            "es": "Juego universal de 4 almohadillas elevadoras antivibración para lavadoras, refrigeradores y muebles."
        }
    else:  # furniture-mover
        if price <= 25.0:
            demand_score = 22
            demand_desc = {
                "en": "Viral demonstration product: crowbar lifter + 4 roller sliders let one person move heavy wardrobes & fridges effortlessly.",
                "si": "තනි පුද්ගලයෙකුට වුවද බරැති අල්මාරි, ශීතකරණ සහ ගෘහ භාණ්ඩ පහසුවෙන් එසවීමට සහ තල්ලු කිරීමට හැකි වීඩියෝ දැන්වීම් සඳහා ඉතා ආකර්ෂණීය භාණ්ඩයකි.",
                "zh": "单人轻松撬动重型衣柜与冰箱的搬家神器，视觉演示效果极强，适合社媒视频带货。",
                "es": "Producto viral: palanca elevadora + 4 rodillos permiten a una sola persona mover armarios y refrigeradores pesados."
            }
        elif price <= 40.0:
            demand_score = 18
            demand_desc = {
                "en": "Upgraded metal/ABS furniture mover set appeals to households and small offices relocating heavy appliances.",
                "si": "බරැති ගෘහ භාණ්ඩ සහ උපකරණ ආරක්ෂිතව එහා මෙහා ගෙන යාමට නිවෙස් හා කාර්යාල සඳහා ප්‍රයෝජනවත් කට්ටලයකි.",
                "zh": "金属/ABS 加强版搬家器套装，适合家庭大扫除及小型办公家具移位。",
                "es": "Kit mejorado de rodillos en metal/ABS para mover electrodomésticos y muebles pesados en casa u oficina."
            }
        elif price <= 60.0:
            demand_score = 13
            demand_desc = {
                "en": "Heavy-duty metal mover set targets workshops, appliance installers, and professional movers rather than casual home users.",
                "si": "අධික බර දැරිය හැකි ලෝහමය එසවුම් කට්ටලය සාමාන්‍ය නිවෙස්වලට වඩා ප්‍රවාහන හා සේවා සපයන්නන් සඳහා විශේෂිත වේ.",
                "zh": "大号全金属重型搬家器，受众偏向家电安装工、仓库与搬家公司。",
                "es": "Kit metálico de alta resistencia orientado a instaladores, talleres y mudanzas profesionales."
            }
        else:
            demand_score = 9
            demand_desc = {
                "en": "Extra-large industrial mover set (¥71–90) is over-engineered and too pricey for typical Sri Lankan household buyers.",
                "si": "ඉතා මිල අධික කාර්මික මට්ටමේ එසවුම් කට්ටලයක් (¥71–90) බැවින් සාමාන්‍ය ගෘහස්ථ පාරිභෝගිකයන්ට අවශ්‍ය නොවේ.",
                "zh": "特大号工业级五轮金属搬家器（¥71–90），超出普通家庭需求，属于小众工具。",
                "es": "Kit industrial extragrande (¥71–90) demasiado costoso y especializado para el comprador doméstico promedio."
            }

    # -------------------------------------------------------------------------
    # 4. Operational & COD Return Risk (0–25 pts)
    # -------------------------------------------------------------------------
    if cat in ("riser-pad", "gas-trolley") and price <= 20.0:
        risk_score = 23
        risk_desc = {
            "en": "Zero electronics/batteries, durable molded/steel build, no complex assembly, and low ticket price ensure near-zero COD returns.",
            "si": "බැටරි හෝ බිඳෙනසුලු කොටස් නොමැති, එකලස් කිරීමට අපහසු නැති ශක්තිමත් නිෂ්පාදනයක් බැවින් COD ආපසු හරවා එවීම් (Returns) ඉතාම අවම වේ.",
            "zh": "无电池无易碎件，免复杂安装，客单价亲民，海运破损率与 COD 拒收率极低。",
            "es": "Sin baterías ni piezas frágiles, sin montaje complejo y precio accesible: tasa de devolución COD casi nula."
        }
    elif cat == "ac-deflector":
        risk_score = 21 if price <= 10.0 else 18
        risk_desc = {
            "en": "Non-electric ABS/PP baffle is safe for sea cargo; clip/hang brackets fit most split ACs easily with minimal return risk.",
            "si": "විදුලි හෝ බැටරි කොටස් රහිත ABS/PP නිමාව Sea Cargo සඳහා ඉතා ආරක්ෂිත වන අතර ඕනෑම AC යන්ත්‍රයකට පහසුවෙන් සවි කළ හැක.",
            "zh": "纯物理 ABS/PP 材质无海运合规风险，卡扣/挂钩式安装简便，售后退换率低。",
            "es": "Deflector físico en ABS/PP seguro para carga marítima; montaje sencillo con mínimo riesgo de devolución."
        }
    elif is_oversized or price >= 65.0:
        risk_score = 10
        risk_desc = {
            "en": "High COD ticket price and multi-rod frame assembly increase doorstep buyer hesitation and sizing mismatch returns.",
            "si": "මිල අධික වීම සහ කොටස් ගණනාවක් එකලස් කිරීමට සිදුවීම නිසා COD ඇණවුම් ප්‍රතික්ෂේප වීමේ (Return) අවදානම වැඩිය.",
            "zh": "高客单价叠加多配件组装要求，容易引发 COD 签收犹豫或尺寸误判退货。",
            "es": "El alto precio COD y el ensamblaje de múltiples barras aumentan el riesgo de rechazo en la entrega."
        }
    elif price <= 38.0:
        risk_score = 19 if "8腿" not in zh and "12腿" not in zh else 17
        risk_desc = {
            "en": "No battery/customs hazards; stainless steel tubes & PVC corners survive sea transit well (include printed diagram for easy DIY setup).",
            "si": "බැටරි අවදානම් නොමැති මල නොබැඳෙන වානේ (Stainless Steel) නිමාව නැව්ගත කිරීමට ඉතා ආරක්ෂිතයි; පහසුවෙන් නිවසේදීම සවි කරගත හැක.",
            "zh": "不锈钢管+工程塑料结构耐海运颠簸，无带电清关风险，附说明书即可轻松组装。",
            "es": "Tubos de acero inoxidable resistentes al transporte marítimo sin riesgos aduaneros; fácil montaje doméstico."
        }
    else:
        risk_score = 14
        risk_desc = {
            "en": "Durable steel hardware with zero battery risk, though 8–12 leg + wheel assembly requires clear instructions to prevent customer complaints.",
            "si": "ශක්තිමත් වානේ නිමාවක් වුවද පාද 8–12 සහ රෝද නිවැරදිව සවි කිරීමට පැහැදිලි උපදෙස් පත්‍රිකාවක් ලබා දීම වැදගත් වේ.",
            "zh": "五金结构坚固耐用，但多腿多轮款式配件较多，需配清晰安装图以降低售后咨询。",
            "es": "Estructura metálica duradera, aunque el montaje de 8–12 patas requiere instrucciones claras para evitar quejas."
        }

    # Exact 100-point sum check
    win_score = int(freight_score + margin_score + demand_score + risk_score)
    assert 0 <= win_score <= 100
    assert freight_score + margin_score + demand_score + risk_score == win_score

    if win_score >= 85:
        win_tier = "mega"
        win_reason = {
            "en": f"Mega Winner ({win_score}/100): Ideal sea-cargo density ({pcs} pcs/ctn), strong 3x–4x LKR COD margin at ¥{price:g}, and massive Sri Lankan household demand.",
            "si": f"සුපිරි ජයග්‍රාහී භාණ්ඩය ({win_score}/100): අඩුම මුහුදු නැව් ගාස්තුව (පෙට්ටියකට කෑලි {pcs}), ¥{price:g} කර්මාන්තශාලා මිලට ඉහළ ලාභාංශයක් සහ ශ්‍රී ලංකාවේ ඉහළම ගෘහස්ථ ඉල්ලුමක් ඇත.",
            "zh": f"超级爆款 ({win_score}/100)：海运装箱率极高（{pcs}件/箱），¥{price:g} 成本具备 3–4 倍卢比溢价空间，完美契合斯里兰卡家庭刚需。",
            "es": f"Mega Ganador ({win_score}/100): Excelente densidad de carga ({pcs} pzs/caja), margen COD 3x–4x a ¥{price:g} y altísima demanda en hogares de Sri Lanka."
        }
    elif win_score >= 70:
        win_tier = "strong"
        win_reason = {
            "en": f"Strong Winner ({win_score}/100): Proven practical problem-solver in Sri Lanka with healthy sea-freight unit economics at ¥{price:g}.",
            "si": f"ඉහළ විකුණුම් හැකියාවක් ඇති භාණ්ඩය ({win_score}/100): ¥{price:g} මිලට හොඳ ලාභාංශයක් සහ ශ්‍රී ලංකාවේ නිවෙස් සඳහා ප්‍රායෝගිකව අලෙවි කළ හැකි මට්ටමක පවතී.",
            "zh": f"强力推荐 ({win_score}/100)：斯里兰卡高频实用家居硬件，¥{price:g} 出厂价与海运成本结构均衡，适合稳定放量。",
            "es": f"Fuerte Ganador ({win_score}/100): Producto práctico de alta rotación en Sri Lanka con excelente economía unitaria a ¥{price:g}."
        }
    elif win_score >= 50:
        win_tier = "niche"
        win_reason = {
            "en": f"Niche Product ({win_score}/100): Solid build quality at ¥{price:g}, but heavier weight ({u_kg:.1f}kg/pc) or higher retail price narrows audience to specific buyers.",
            "si": f"විශේෂිත වෙළඳපොළ භාණ්ඩය ({win_score}/100): උසස් ප්‍රමිතියෙන් යුක්ත වුවද වැඩි බර ({u_kg:.1f}kg) හෝ ¥{price:g} මිල නිසා තෝරාගත් පාරිභෝගික පිරිසකට පමණක් ගැළපේ.",
            "zh": f"细分市场产品 ({win_score}/100)：品质扎实（¥{price:g}），但单件较重（{u_kg:.1f}kg）或定位偏高，更适合针对特定客群销售。",
            "es": f"Producto de Nicho ({win_score}/100): Buena calidad a ¥{price:g}, pero su mayor peso ({u_kg:.1f}kg/ud) o precio limita el público a compradores específicos."
        }
    else:
        win_tier = "low"
        win_reason = {
            "en": f"Low Feasibility ({win_score}/100): High factory cost (¥{price:g}) and heavy/bulky commercial specs ({u_kg:.1f}kg/pc) create poor COD unit economics in Sri Lanka.",
            "si": f"අඩු යෝග්‍යතාවයක් ඇති භාණ්ඩය ({win_score}/100): ඉහළ මිල (¥{price:g}) සහ අධික බර/පරිමාණ ({u_kg:.1f}kg) නිසා සාමාන්‍ය COD අලෙවිය සඳහා අවදානම වැඩිය.",
            "zh": f"低可行性 ({win_score}/100)：出厂价偏高（¥{price:g}）且属于大体积重型商用规格（{u_kg:.1f}kg/件），海运与 COD 拒收风险高。",
            "es": f"Baja Viabilidad ({win_score}/100): Alto costo de fábrica (¥{price:g}) y especificaciones comerciales pesadas ({u_kg:.1f}kg/ud) dificultan la venta COD."
        }

    p["winScore"] = win_score
    p["winRate"] = f"{win_score}%"
    p["winTier"] = win_tier
    p["winBreakdown"] = {
        "freight": {"score": freight_score, "max": 25, "desc": freight_desc},
        "margin": {"score": margin_score, "max": 25, "desc": margin_desc},
        "demand": {"score": demand_score, "max": 25, "desc": demand_desc},
        "risk": {"score": risk_score, "max": 25, "desc": risk_desc},
    }
    p["winReason"] = win_reason
    return p


def evaluate_catalog(catalog: dict) -> dict:
    products = catalog.get("products", [])
    tier_counts = {"mega": 0, "strong": 0, "niche": 0, "low": 0}
    for p in products:
        score_product(p)
        tier_counts[p["winTier"]] = tier_counts.get(p["winTier"], 0) + 1
    catalog["winTierSummary"] = tier_counts
    return catalog


def main():
    base_dir = Path(__file__).resolve().parent.parent
    products_js = base_dir / "data" / "products.js"
    if not products_js.exists():
        print(f"Error: {products_js} not found. Run build.py first.")
        return

    text = products_js.read_text(encoding="utf-8")
    m = re.search(r"window\.CATALOG\s*=\s*(\{.*\})\s*;\s*$", text, re.S)
    if not m:
        print("Error: Could not parse window.CATALOG from products.js")
        return

    catalog = json.loads(m.group(1))
    evaluate_catalog(catalog)

    out_js = "window.CATALOG = " + json.dumps(catalog, ensure_ascii=False, indent=2) + ";\n"
    products_js.write_text(out_js, encoding="utf-8")
    print(f"Updated SL-WPI scores for {len(catalog.get('products', []))} products: {catalog.get('winTierSummary')}")


if __name__ == "__main__":
    main()
