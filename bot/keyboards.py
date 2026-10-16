"""
Premium Systematic Buttons - Not messed up, organized by category
Railway / Heroku / Render ready
"""
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

def main_menu():
    # Premium systematic main menu - 2x2 grid, clear categories
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Video Watermark", callback_data="menu_video"),
         InlineKeyboardButton("📄 PDF Watermark", callback_data="menu_pdf")],
        [InlineKeyboardButton("🖼️ Image Watermark", callback_data="menu_image"),
         InlineKeyboardButton("🎨 Set Logo", callback_data="menu_logo")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings"),
         InlineKeyboardButton("📊 My Config", callback_data="menu_config")],
        [InlineKeyboardButton("❓ Help", callback_data="menu_help"),
         InlineKeyboardButton("🚀 Speed Tips", callback_data="menu_speed")]
    ])

def settings_menu():
    # Systematic settings grouped by category: Text, Visual, Quality, PDF, System
    return InlineKeyboardMarkup([
        # Row 1: Text category
        [InlineKeyboardButton("📝 Text", callback_data="set_text"),
         InlineKeyboardButton("🎭 Mode", callback_data="set_mode")],
        # Row 2: Position & Opacity
        [InlineKeyboardButton("📍 Position", callback_data="set_position"),
         InlineKeyboardButton("👁️ Opacity", callback_data="set_opacity")],
        # Row 3: Color & Font
        [InlineKeyboardButton("🎨 Color", callback_data="set_color"),
         InlineKeyboardButton("🔤 Font Size", callback_data="set_font")],
        # Row 4: Background & Border - your request bg opacity + border
        [InlineKeyboardButton("🟦 BG Box", callback_data="set_bg"),
         InlineKeyboardButton("⬛ Border", callback_data="set_border")],
        # Row 5: Quality - CRF + Preset
        [InlineKeyboardButton("⚡ CRF Quality", callback_data="set_crf"),
         InlineKeyboardButton("🏃 Preset Speed", callback_data="set_preset")],
        # Row 6: Size - Resolution + Codec
        [InlineKeyboardButton("📐 Resolution", callback_data="set_res"),
         InlineKeyboardButton("🎞️ Codec", callback_data="set_codec")],
        # Row 7: PDF + Reset
        [InlineKeyboardButton("📄 PDF Mode", callback_data="set_pdfmode"),
         InlineKeyboardButton("🔄 Reset All", callback_data="set_reset")],
        # Row 8: Back
        [InlineKeyboardButton("⬅️ Back to Main", callback_data="menu_main")]
    ])

def mode_kb():
    # 8 modes from screenshots - systematic 2 per row
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📌 Static", callback_data="mode_static"),
         InlineKeyboardButton("🏓 Bouncing", callback_data="mode_bouncing")],
        [InlineKeyboardButton("🌊 Floating", callback_data="mode_floating"),
         InlineKeyboardButton("📰 Scrolling", callback_data="mode_scrolling")],
        [InlineKeyboardButton("↗️ Diagonal", callback_data="mode_diagonal"),
         InlineKeyboardButton("🔲 Tiled", callback_data="mode_tiled")],
        [InlineKeyboardButton("👻 Transparent", callback_data="mode_transparent"),
         InlineKeyboardButton("🎞️ Cinematic", callback_data="mode_cinematic")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_settings")]
    ])

def pos_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↖️ Top Left", callback_data="pos_top_left"),
         InlineKeyboardButton("↗️ Top Right", callback_data="pos_top_right")],
        [InlineKeyboardButton("↙️ Bottom Left", callback_data="pos_bottom_left"),
         InlineKeyboardButton("↘️ Bottom Right", callback_data="pos_bottom_right")],
        [InlineKeyboardButton("🎯 Center", callback_data="pos_center")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_settings")]
    ])

def preset_kb():
    # Speed vs quality - systematic fastest to slowest
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Ultrafast (14s)", callback_data="preset_ultrafast"),
         InlineKeyboardButton("🚀 Superfast", callback_data="preset_superfast")],
        [InlineKeyboardButton("💨 Veryfast", callback_data="preset_veryfast"),
         InlineKeyboardButton("🏃 Faster", callback_data="preset_faster")],
        [InlineKeyboardButton("🏃 Fast", callback_data="preset_fast"),
         InlineKeyboardButton("⚖️ Medium (48s)", callback_data="preset_medium")],
        [InlineKeyboardButton("🐢 Slow", callback_data="preset_slow"),
         InlineKeyboardButton("🐌 Slower", callback_data="preset_slower")],
        [InlineKeyboardButton("🐢 VerySlow (3m20s)", callback_data="preset_veryslow")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_settings")]
    ])

def res_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎥 Original", callback_data="res_original"),
         InlineKeyboardButton("📺 1080p", callback_data="res_1080p"),
         InlineKeyboardButton("📺 720p", callback_data="res_720p")],
        [InlineKeyboardButton("📺 540p", callback_data="res_540p"),
         InlineKeyboardButton("📺 480p", callback_data="res_480p"),
         InlineKeyboardButton("📺 360p", callback_data="res_360p")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_settings")]
    ])

def color_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚪ White", callback_data="color_white"),
         InlineKeyboardButton("⚫ Black", callback_data="color_black"),
         InlineKeyboardButton("🔴 Red", callback_data="color_red")],
        [InlineKeyboardButton("🔵 Blue", callback_data="color_blue"),
         InlineKeyboardButton("🟢 Green", callback_data="color_green"),
         InlineKeyboardButton("🟡 Yellow", callback_data="color_yellow")],
        [InlineKeyboardButton("🟦 Cyan", callback_data="color_cyan"),
         InlineKeyboardButton("🟣 Magenta", callback_data="color_magenta"),
         InlineKeyboardButton("🟠 Orange", callback_data="color_orange")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_settings")]
    ])

def pdf_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↗️ Diagonal (Best)", callback_data="pdfmode_diagonal"),
         InlineKeyboardButton("🔲 Tiled", callback_data="pdfmode_tiled")],
        [InlineKeyboardButton("🎯 Center", callback_data="pdfmode_center")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_settings")]
    ])

def crf_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 CRF 18 (High)", callback_data="crf_18"),
         InlineKeyboardButton("✨ CRF 20", callback_data="crf_20")],
        [InlineKeyboardButton("⚖️ CRF 23 (Default)", callback_data="crf_23"),
         InlineKeyboardButton("📦 CRF 26", callback_data="crf_26")],
        [InlineKeyboardButton("🗜️ CRF 28 (Small)", callback_data="crf_28"),
         InlineKeyboardButton("🤏 CRF 30 (Tiny)", callback_data="crf_30")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_settings")]
    ])

def opacity_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👻 20% Ghost", callback_data="op_0.2"),
         InlineKeyboardButton("🌫️ 40%", callback_data="op_0.4")],
        [InlineKeyboardButton("⚖️ 60% Default", callback_data="op_0.6"),
         InlineKeyboardButton("🔆 80%", callback_data="op_0.8")],
        [InlineKeyboardButton("💯 100% Solid", callback_data="op_1.0")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_settings")]
    ])

def codec_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 H264 Fast (Best)", callback_data="codec_h264"),
         InlineKeyboardButton("🗜️ H265 Small 50%", callback_data="codec_h265")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_settings")]
    ])

def bg_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ BG ON", callback_data="bg_on"),
         InlineKeyboardButton("❌ BG OFF", callback_data="bg_off")],
        [InlineKeyboardButton("👻 BG 20%", callback_data="bgop_0.2"),
         InlineKeyboardButton("⚖️ BG 50%", callback_data="bgop_0.5"),
         InlineKeyboardButton("⬛ BG 80%", callback_data="bgop_0.8")],
        [InlineKeyboardButton("🎨 BG Black", callback_data="bgcolor_black"),
         InlineKeyboardButton("🎨 BG White", callback_data="bgcolor_white"),
         InlineKeyboardButton("🎨 BG Red", callback_data="bgcolor_red")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_settings")]
    ])

def border_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Border ON", callback_data="border_on"),
         InlineKeyboardButton("❌ Border OFF", callback_data="border_off")],
        [InlineKeyboardButton("➖ Width 1", callback_data="borderw_1"),
         InlineKeyboardButton("➖ Width 3 Default", callback_data="borderw_3"),
         InlineKeyboardButton("➖ Width 5", callback_data="borderw_5")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_settings")]
    ])
