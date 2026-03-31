# gui/themes.py
# Sistema centralizado de temas para o EPCopyFlow 2.0.
# Três temas disponíveis: Escuro (Catppuccin Mocha), Claro, Oceano.

import logging

logger = logging.getLogger(__name__)

# ── Paletas de cores ──

THEMES = {
    "Escuro": {
        "base": "#1e1e2e",
        "surface": "#181825",
        "card": "#1e1e2e",
        "input": "#313244",
        "border": "#313244",
        "border_hover": "#45475a",
        "text": "#cdd6f4",
        "text_secondary": "#a6adc8",
        "text_dim": "#585b70",
        "text_disabled": "#6c7086",
        "accent": "#89b4fa",
        "success": "#a6e3a1",
        "success_hover": "#94e2d5",
        "error": "#f38ba8",
        "error_hover": "#eba0ac",
        "warning": "#f9e2af",
        "terminal_bg": "#11111b",
        "terminal_text": "#a6e3a1",
        "selection": "#45475a",
        "btn_text_on_color": "#1e1e2e",
    },
    "Claro": {
        "base": "#eff1f5",
        "surface": "#e6e9ef",
        "card": "#ffffff",
        "input": "#ffffff",
        "border": "#ccd0da",
        "border_hover": "#bcc0cc",
        "text": "#4c4f69",
        "text_secondary": "#6c6f85",
        "text_dim": "#9ca0b0",
        "text_disabled": "#bcc0cc",
        "accent": "#1e66f5",
        "success": "#40a02b",
        "success_hover": "#179299",
        "error": "#d20f39",
        "error_hover": "#e64553",
        "warning": "#df8e1d",
        "terminal_bg": "#e6e9ef",
        "terminal_text": "#40a02b",
        "selection": "#ccd0da",
        "btn_text_on_color": "#ffffff",
    },
    "Oceano": {
        "base": "#0d1b2a",
        "surface": "#1b2838",
        "card": "#162232",
        "input": "#1b3a4b",
        "border": "#1b3a4b",
        "border_hover": "#274c6b",
        "text": "#e0e8f0",
        "text_secondary": "#a8b8c8",
        "text_dim": "#5c7a8a",
        "text_disabled": "#3d5a6e",
        "accent": "#00b4d8",
        "success": "#06d6a0",
        "success_hover": "#64dfdf",
        "error": "#ef476f",
        "error_hover": "#ff6b8a",
        "warning": "#ffd166",
        "terminal_bg": "#0a1520",
        "terminal_text": "#06d6a0",
        "selection": "#274c6b",
        "btn_text_on_color": "#0d1b2a",
    },
}

# ── Tema ativo (singleton) ──

_current_theme = "Escuro"


def get_theme_names():
    return list(THEMES.keys())


def get_current_theme_name():
    return _current_theme


def set_theme(name):
    global _current_theme
    if name in THEMES:
        _current_theme = name
        logger.info(f"Tema alterado para: {name}")
    else:
        logger.warning(f"Tema '{name}' não encontrado. Mantendo '{_current_theme}'.")


def t():
    """Retorna a paleta do tema atual."""
    return THEMES[_current_theme]


# ── Geradores de Stylesheet ──

def sidebar_style():
    c = t()
    return f"""
QFrame#sidebar {{
    background-color: {c['base']};
    border-right: 1px solid {c['border']};
}}
QPushButton.nav-btn {{
    background-color: transparent;
    color: {c['text']};
    border: none;
    text-align: left;
    padding: 12px 16px;
    font-size: 14px;
    border-radius: 8px;
    margin: 2px 8px;
}}
QPushButton.nav-btn:hover {{
    background-color: {c['input']};
}}
QPushButton.nav-btn:checked {{
    background-color: {c['border_hover']};
    color: {c['accent']};
    font-weight: bold;
}}
"""


def header_style():
    c = t()
    return f"""
QFrame#header {{
    background-color: {c['base']};
    border-bottom: 1px solid {c['border']};
    padding: 4px 16px;
}}
QLabel.header-title {{
    color: {c['accent']};
    font-size: 16px;
    font-weight: bold;
}}
QLabel.header-status {{
    color: {c['text_secondary']};
    font-size: 12px;
}}
QPushButton#emergency-btn {{
    background-color: {c['error']};
    color: {c['btn_text_on_color']};
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: bold;
    font-size: 13px;
}}
QPushButton#emergency-btn:hover {{
    background-color: {c['error_hover']};
}}
QPushButton#emergency-btn:pressed {{
    background-color: {c['error']};
}}
"""


def main_area_style():
    c = t()
    return f"""
QWidget#main-area {{
    background-color: {c['surface']};
}}
"""


def page_background_style():
    c = t()
    return f"background-color: {c['surface']};"


def version_label_style():
    c = t()
    return f"color: {c['text_dim']}; font-size: 11px; padding: 8px 16px;"


def dashboard_style():
    c = t()
    return f"""
QLabel.page-title {{
    color: {c['text']};
    font-size: 20px;
    font-weight: bold;
    padding: 8px 0px;
}}
QLabel.section-title {{
    color: {c['text_secondary']};
    font-size: 14px;
    font-weight: bold;
    padding: 4px 0px;
}}
QLabel.stat-value {{
    color: {c['accent']};
    font-size: 24px;
    font-weight: bold;
}}
QLabel.stat-label {{
    color: {c['text_disabled']};
    font-size: 12px;
}}
QFrame.stat-card {{
    background-color: {c['card']};
    border: 1px solid {c['border']};
    border-radius: 10px;
    padding: 16px;
}}
"""


def dashboard_placeholder_style():
    c = t()
    return f"color: {c['text_dim']}; font-style: italic; padding: 12px;"


def brokers_page_style():
    c = t()
    return f"""
QLabel.page-title {{
    color: {c['text']};
    font-size: 20px;
    font-weight: bold;
    padding: 8px 0px;
}}
QPushButton.action-btn {{
    background-color: {c['input']};
    color: {c['text']};
    border: 1px solid {c['border_hover']};
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 13px;
}}
QPushButton.action-btn:hover {{
    background-color: {c['border_hover']};
}}
QPushButton.connect-btn {{
    background-color: {c['success']};
    color: {c['btn_text_on_color']};
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: bold;
}}
QPushButton.connect-btn:hover {{
    background-color: {c['success_hover']};
}}
QPushButton.disconnect-btn {{
    background-color: {c['error']};
    color: {c['btn_text_on_color']};
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: bold;
}}
QPushButton.disconnect-btn:hover {{
    background-color: {c['error_hover']};
}}
"""


def history_page_style():
    c = t()
    return f"""
QLabel.page-title {{
    color: {c['text']};
    font-size: 20px;
    font-weight: bold;
    padding: 8px 0px;
}}
QTableWidget {{
    background-color: {c['card']};
    color: {c['text']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    gridline-color: {c['border']};
    selection-background-color: {c['selection']};
}}
QTableWidget::item {{
    padding: 6px;
}}
QHeaderView::section {{
    background-color: {c['input']};
    color: {c['text']};
    padding: 6px;
    border: none;
    font-weight: bold;
}}
QPushButton.action-btn {{
    background-color: {c['input']};
    color: {c['text']};
    border: 1px solid {c['border_hover']};
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 13px;
}}
QPushButton.action-btn:hover {{
    background-color: {c['border_hover']};
}}
QComboBox {{
    background-color: {c['input']};
    color: {c['text']};
    border: 1px solid {c['border_hover']};
    border-radius: 4px;
    padding: 4px 8px;
}}
QLabel {{
    color: {c['text']};
}}
"""


def logs_page_style():
    c = t()
    return f"""
QLabel.page-title {{
    color: {c['text']};
    font-size: 20px;
    font-weight: bold;
    padding: 8px 0px;
}}
QTextEdit {{
    background-color: {c['terminal_bg']};
    color: {c['terminal_text']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 12px;
    padding: 8px;
}}
QPushButton.action-btn {{
    background-color: {c['input']};
    color: {c['text']};
    border: 1px solid {c['border_hover']};
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 13px;
}}
QPushButton.action-btn:hover {{
    background-color: {c['border_hover']};
}}
"""


def settings_page_style():
    c = t()
    return f"""
QLabel.page-title {{
    color: {c['text']};
    font-size: 20px;
    font-weight: bold;
    padding: 8px 0px;
}}
QLabel.section-title {{
    color: {c['text_secondary']};
    font-size: 14px;
    font-weight: bold;
    padding: 8px 0px 4px 0px;
}}
QLabel.field-label {{
    color: {c['text']};
    font-size: 13px;
}}
QLabel {{
    color: {c['text']};
}}
QLineEdit, QSpinBox, QComboBox {{
    background-color: {c['input']};
    color: {c['text']};
    border: 1px solid {c['border_hover']};
    border-radius: 4px;
    padding: 6px;
}}
QCheckBox {{
    color: {c['text']};
    font-size: 13px;
}}
QFrame.settings-group {{
    background-color: {c['card']};
    border: 1px solid {c['border']};
    border-radius: 10px;
    padding: 16px;
}}
QPushButton.save-btn {{
    background-color: {c['success']};
    color: {c['btn_text_on_color']};
    border: none;
    border-radius: 6px;
    padding: 10px 24px;
    font-weight: bold;
    font-size: 14px;
}}
QPushButton.save-btn:hover {{
    background-color: {c['success_hover']};
}}
QComboBox QAbstractItemView {{
    background-color: {c['input']};
    color: {c['text']};
    selection-background-color: {c['selection']};
}}
"""


def broker_card_style(border_color, role_color, role_bg, status_color):
    c = t()
    return f"""
QFrame.broker-card {{
    background-color: {c['card']};
    border: 1px solid {border_color};
    border-radius: 12px;
    padding: 4px;
}}
QFrame.broker-card:hover {{
    border-color: {c['accent']};
}}
QLabel.card-title {{
    color: {c['text']};
    font-size: 14px;
    font-weight: bold;
}}
QLabel.card-role {{
    color: {role_color};
    font-size: 11px;
    font-weight: bold;
    background-color: {role_bg};
    border-radius: 4px;
    padding: 2px 8px;
}}
QLabel.card-info {{
    color: {c['text_secondary']};
    font-size: 12px;
}}
QLabel.card-status {{
    color: {status_color};
    font-size: 12px;
    font-weight: bold;
}}
QLabel.card-profit-positive {{
    color: {c['success']};
    font-size: 13px;
    font-weight: bold;
}}
QLabel.card-profit-negative {{
    color: {c['error']};
    font-size: 13px;
    font-weight: bold;
}}
QPushButton.card-connect {{
    background-color: {c['success']};
    color: {c['btn_text_on_color']};
    border: none;
    border-radius: 4px;
    padding: 4px 12px;
    font-size: 11px;
    font-weight: bold;
}}
QPushButton.card-connect:hover {{
    background-color: {c['success_hover']};
}}
QPushButton.card-disconnect {{
    background-color: {c['error']};
    color: {c['btn_text_on_color']};
    border: none;
    border-radius: 4px;
    padding: 4px 12px;
    font-size: 11px;
    font-weight: bold;
}}
QPushButton.card-disconnect:hover {{
    background-color: {c['error_hover']};
}}
"""


def broker_card_dynamic_colors(is_master, is_connected):
    """Retorna (border_color, role_color, role_bg, status_color) baseado no tema."""
    c = t()
    role_color = c['warning'] if is_master else c['accent']
    role_bg = c['border_hover']
    border_color = c['warning'] if is_master else c['border']
    status_color = c['success'] if is_connected else c['error']
    return border_color, role_color, role_bg, status_color


def brokers_dialog_style():
    c = t()
    return f"""
QDialog {{
    background-color: {c['base']};
}}
QLabel {{
    color: {c['text']};
}}
QLineEdit, QComboBox, QDoubleSpinBox {{
    color: {c['text']};
    background-color: {c['input']};
    border: 1px solid {c['border_hover']};
    border-radius: 4px;
    padding: 4px;
}}
QPushButton {{
    color: {c['text']};
    background-color: {c['border_hover']};
    border: 1px solid {c['text_dim']};
    border-radius: 4px;
    padding: 6px 12px;
}}
QPushButton:hover {{
    background-color: {c['text_dim']};
}}
QPushButton:disabled {{
    color: {c['text_disabled']};
}}
QComboBox QAbstractItemView {{
    color: {c['text']};
    background-color: {c['input']};
    selection-background-color: {c['selection']};
}}
"""


def dialog_info_label_style():
    c = t()
    return f"color: {c['error']}; font-style: italic;"


def scroll_area_style():
    return "QScrollArea { border: none; background: transparent; }"


def scroll_widget_style():
    return "background: transparent;"


def splash_style():
    c = t()
    return {
        "background": f"background-color: {c['base']};",
        "title": f"color: {c['accent']};",
        "subtitle": f"color: {c['text']};",
        "version": f"color: {c['text_dim']};",
        "progress": f"""
            QProgressBar {{ background-color: {c['input']}; border-radius: 4px; height: 6px; }}
            QProgressBar::chunk {{ background-color: {c['accent']}; border-radius: 4px; }}
        """,
    }


def internet_status_color(online):
    c = t()
    return c['success'] if online == "Online" else c['error']
