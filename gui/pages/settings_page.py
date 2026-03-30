# EPCopyFlow 2.0 - Versão 0.0.1 - Claude Code Parte 000
# gui/pages/settings_page.py
# Página de configurações gerais.

import logging
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QSpinBox, QCheckBox, QFrame, QMessageBox
)
from PySide6.QtCore import Qt

logger = logging.getLogger(__name__)

PAGE_STYLE = """
QLabel.page-title {
    color: #cdd6f4;
    font-size: 20px;
    font-weight: bold;
    padding: 8px 0px;
}
QLabel.section-title {
    color: #a6adc8;
    font-size: 14px;
    font-weight: bold;
    padding: 8px 0px 4px 0px;
}
QLabel.field-label {
    color: #cdd6f4;
    font-size: 13px;
}
QLineEdit, QSpinBox {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 6px;
}
QCheckBox {
    color: #cdd6f4;
    font-size: 13px;
}
QFrame.settings-group {
    background-color: #1e1e2e;
    border: 1px solid #313244;
    border-radius: 10px;
    padding: 16px;
}
QPushButton.save-btn {
    background-color: #a6e3a1;
    color: #1e1e2e;
    border: none;
    border-radius: 6px;
    padding: 10px 24px;
    font-weight: bold;
    font-size: 14px;
}
QPushButton.save-btn:hover {
    background-color: #94e2d5;
}
"""


class SettingsPage(QWidget):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setStyleSheet(PAGE_STYLE)
        self._init_ui()
        self._load_settings()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(16)

        title = QLabel("Configuracoes")
        title.setProperty("class", "page-title")
        layout.addWidget(title)

        # MT5 Settings
        mt5_group = QFrame()
        mt5_group.setProperty("class", "settings-group")
        mt5_layout = QVBoxLayout(mt5_group)

        mt5_title = QLabel("MetaTrader 5")
        mt5_title.setProperty("class", "section-title")
        mt5_layout.addWidget(mt5_title)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Caminho base MT5:"))
        self.mt5_path_edit = QLineEdit()
        row1.addWidget(self.mt5_path_edit, 1)
        mt5_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Intervalo monitor (s):"))
        self.monitor_interval_spin = QSpinBox()
        self.monitor_interval_spin.setRange(5, 120)
        self.monitor_interval_spin.setValue(10)
        row2.addWidget(self.monitor_interval_spin)
        row2.addStretch()
        mt5_layout.addLayout(row2)

        layout.addWidget(mt5_group)

        # App Settings
        app_group = QFrame()
        app_group.setProperty("class", "settings-group")
        app_layout = QVBoxLayout(app_group)

        app_title = QLabel("Aplicacao")
        app_title.setProperty("class", "section-title")
        app_layout.addWidget(app_title)

        self.splash_check = QCheckBox("Exibir Splash Screen ao iniciar")
        app_layout.addWidget(self.splash_check)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Nivel de log:"))
        self.log_level_edit = QLineEdit()
        self.log_level_edit.setPlaceholderText("INFO, DEBUG, WARNING, ERROR")
        self.log_level_edit.setMaximumWidth(200)
        row3.addWidget(self.log_level_edit)
        row3.addStretch()
        app_layout.addLayout(row3)

        layout.addWidget(app_group)

        layout.addStretch()

        # Save button
        save_btn = QPushButton("Salvar")
        save_btn.setProperty("class", "save-btn")
        save_btn.setMaximumWidth(200)
        save_btn.clicked.connect(self._save_settings)
        layout.addWidget(save_btn, alignment=Qt.AlignRight)

    def _load_settings(self):
        self.mt5_path_edit.setText(
            self.config.get('General', 'base_mt5_path', fallback='C:/Temp/MT5')
        )
        self.monitor_interval_spin.setValue(
            self.config.getint('General', 'monitor_interval', fallback=10)
        )
        self.splash_check.setChecked(
            self.config.getboolean('General', 'show_splash', fallback=True)
        )
        self.log_level_edit.setText(
            self.config.get('General', 'log_level', fallback='INFO')
        )

    def _save_settings(self):
        try:
            self.config.set('General', 'base_mt5_path', self.mt5_path_edit.text())
            self.config.set('General', 'monitor_interval', str(self.monitor_interval_spin.value()))
            self.config.set('General', 'show_splash', str(self.splash_check.isChecked()))
            self.config.set('General', 'log_level', self.log_level_edit.text().upper())
            self.config.save_config()
            QMessageBox.information(self, "Sucesso", "Configuracoes salvas.")
            logger.info("Configuracoes salvas.")
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Erro ao salvar: {e}")
            logger.error(f"Erro ao salvar configuracoes: {e}")
